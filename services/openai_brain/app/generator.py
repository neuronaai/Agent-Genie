"""OpenAI Brain — Generation and validation logic.

Translates natural-language prompts into structured agent configurations
using OpenAI's API.  Supports mock mode for development without credentials.
"""
import json
import logging
import os
from typing import Optional

from pydantic import ValidationError
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

from app.schemas import (
    AgentDraftRequest, AgentDraftResponse, AgentDraftConfig,
    KBStructureRequest, KBStructureResponse, KBItem,
    ValidationRequest, ValidationResponse, ValidationIssue,
    HandoffRuleOut, GuardrailRuleOut, FAQItem, ServiceItem,
)

logger = logging.getLogger(__name__)

MOCK_MODE = os.environ.get("BRAIN_MOCK_MODE", "false").lower() == "true"


# ---------------------------------------------------------------------------
# System Prompt
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """You are an expert AI voice agent configurator for a SaaS platform called AgentGenie.

Your job is to take a user's natural language description of their business and desired phone agent, and produce a structured JSON configuration that can be used to deploy a Retell AI voice agent.

You MUST output valid JSON matching this exact schema:
{
  "business_type": "string — The type of business",
  "business_context": "string — Background about the company, services, target audience",
  "agent_role": "string — What the agent does (e.g., 'receptionist', 'support agent')",
  "agent_name": "string — A concise name for the agent",
  "tone": "string — The emotional tone (e.g., 'professional', 'empathetic')",
  "language": "string — Language code (e.g., 'en-US')",
  "greeting_message": "string — The first thing the agent says",
  "services": [{"name": "string", "description": "string or null"}],
  "faqs": [{"question": "string", "answer": "string"}],
  "knowledge_categories": ["string — categories of knowledge the agent needs"],
  "specials_offers": ["string — current promotions or offers"],
  "human_handoff_conditions": [
    {"condition": "string", "destination_number": "string or null", "transfer_message": "string or null"}
  ],
  "booking_behavior": "string or null — How the agent handles bookings",
  "support_flow": "string or null — How the agent handles support requests",
  "transfer_rules": [
    {"condition": "string", "destination_number": "string or null", "transfer_message": "string or null"}
  ],
  "fallback_behavior": "string or null — What the agent does when it cannot help",
  "prohibited_topics": [
    {"prohibited_topic": "string", "fallback_message": "string"}
  ],
  "escalation_rules": ["string — rules for escalating issues"],
  "unsupported_request_behavior": "string or null",
  "hours_of_operation": {"timezone": "string", "schedule": {}},
  "routing_rules": ["string — call routing rules"],
  "missing_information": ["string — critical info the user did NOT provide"],
  "contradictions": ["string — any conflicting instructions detected"]
}

IMPORTANT RULES:
1. Detect the business industry and generate guardrails RELEVANT to that industry.
2. Scan for contradictions in the user's prompt and flag them.
3. Generate the best possible config from whatever information is given.
4. For missing critical info, add to missing_information.
5. Always include at least one default handoff rule.
6. Output ONLY valid JSON. No markdown, no code fences, no explanation text."""


# ---------------------------------------------------------------------------
# OpenAI API Helpers
# ---------------------------------------------------------------------------
class OpenAIRateLimited(Exception):
    pass


class OpenAIServiceUnavailable(Exception):
    pass


def _get_api_key() -> str:
    return os.environ.get("OPENAI_API_KEY_CUSTOM", "") or os.environ.get("OPENAI_API_KEY", "")


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(OpenAIRateLimited),
    reraise=True,
)
async def _call_openai(messages: list, api_key: str) -> str:
    """Call OpenAI API with retries."""
    import openai

    client = openai.AsyncOpenAI(
        api_key=api_key,
        base_url="https://api.openai.com/v1",
        timeout=60.0,
    )

    try:
        response = await client.chat.completions.create(
            model="gpt-4o",
            messages=messages,
            response_format={"type": "json_object"},
            temperature=0.7,
            max_tokens=4000,
        )
        return response.choices[0].message.content
    except openai.RateLimitError:
        raise OpenAIRateLimited("Rate limited")
    except openai.APIStatusError as e:
        if e.status_code and e.status_code >= 500:
            raise OpenAIRateLimited(f"Server error: {e.status_code}")
        raise
    except openai.APIConnectionError:
        raise OpenAIServiceUnavailable("Cannot connect to OpenAI API")
    except openai.APITimeoutError:
        raise OpenAIRateLimited("Timeout")


# ---------------------------------------------------------------------------
# Mock data for development
# ---------------------------------------------------------------------------
def _mock_agent_config() -> AgentDraftConfig:
    return AgentDraftConfig(
        business_type="General Business",
        business_context="A sample business for development testing.",
        agent_role="Receptionist",
        agent_name="Demo Agent",
        tone="professional",
        greeting_message="Hello! Thank you for calling. How can I help you today?",
        services=[ServiceItem(name="General Inquiry", description="Answer general questions")],
        faqs=[FAQItem(question="What are your hours?", answer="We are open Monday to Friday, 9 AM to 5 PM.")],
        knowledge_categories=["business hours", "services", "pricing"],
        human_handoff_conditions=[
            HandoffRuleOut(condition="Caller explicitly asks for a human agent")
        ],
        fallback_behavior="Apologize and offer to transfer to a human agent.",
        prohibited_topics=[
            GuardrailRuleOut(prohibited_topic="Competitor pricing", fallback_message="I cannot discuss competitor information.")
        ],
        missing_information=["Business name not provided", "Specific services not listed"],
    )


# ---------------------------------------------------------------------------
# Agent Draft Generation
# ---------------------------------------------------------------------------
async def generate_agent_draft(req: AgentDraftRequest) -> AgentDraftResponse:
    """Generate a structured agent config from a natural-language prompt."""
    if MOCK_MODE:
        logger.info("Mock mode — returning mock agent config")
        return AgentDraftResponse(status="success", config=_mock_agent_config())

    api_key = _get_api_key()
    if not api_key:
        return AgentDraftResponse(
            status="error",
            message="OpenAI API key is not configured.",
        )

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": req.raw_prompt},
    ]

    max_retries = 2
    raw_json_str = None

    for attempt in range(max_retries):
        try:
            raw_json_str = await _call_openai(messages, api_key)
            config = AgentDraftConfig.model_validate_json(raw_json_str)
            return AgentDraftResponse(status="success", config=config)

        except ValidationError as e:
            logger.warning(f"Validation failed (attempt {attempt + 1}): {e}")
            if attempt == max_retries - 1:
                try:
                    raw_data = json.loads(raw_json_str) if raw_json_str else {}
                    config = AgentDraftConfig(**{
                        k: v for k, v in raw_data.items()
                        if k in AgentDraftConfig.model_fields
                    })
                    return AgentDraftResponse(
                        status="success",
                        config=config,
                        message="Generated with partial validation.",
                    )
                except Exception:
                    return AgentDraftResponse(
                        status="error",
                        message="Failed to generate valid configuration.",
                    )

            messages.append({"role": "assistant", "content": raw_json_str})
            messages.append({
                "role": "user",
                "content": f"JSON validation failed: {str(e)}. Fix the JSON.",
            })

        except OpenAIServiceUnavailable:
            return AgentDraftResponse(
                status="error",
                message="AI services are currently unavailable.",
            )
        except OpenAIRateLimited:
            return AgentDraftResponse(
                status="error",
                message="AI services are temporarily busy.",
            )
        except Exception as e:
            logger.exception(f"Unexpected error: {e}")
            return AgentDraftResponse(
                status="error",
                message=f"Unexpected error: {str(e)[:200]}",
            )

    return AgentDraftResponse(status="error", message="Failed after multiple attempts.")


# ---------------------------------------------------------------------------
# Knowledge Base Structuring
# ---------------------------------------------------------------------------
KB_SYSTEM_PROMPT = """You are a knowledge-base structuring assistant. Given raw content,
categorize it into structured items. Output a JSON array of objects with:
{"category": "string", "title": "string", "content": "string", "type": "text|faq|url"}
Output ONLY valid JSON."""


async def structure_knowledge_base(req: KBStructureRequest) -> KBStructureResponse:
    if MOCK_MODE:
        return KBStructureResponse(
            status="success",
            items=[KBItem(category="general", title="Sample", content=req.raw_content, type="text")],
        )

    api_key = _get_api_key()
    if not api_key:
        return KBStructureResponse(status="error", message="OpenAI API key not configured.")

    messages = [
        {"role": "system", "content": KB_SYSTEM_PROMPT},
        {"role": "user", "content": req.raw_content},
    ]

    try:
        raw = await _call_openai(messages, api_key)
        data = json.loads(raw)
        items_list = data if isinstance(data, list) else data.get("items", [])
        items = [KBItem(**item) for item in items_list]
        return KBStructureResponse(status="success", items=items)
    except Exception as e:
        logger.exception(f"KB structuring failed: {e}")
        return KBStructureResponse(status="error", message=str(e)[:200])


# ---------------------------------------------------------------------------
# Config Validation
# ---------------------------------------------------------------------------
async def validate_agent_config(req: ValidationRequest) -> ValidationResponse:
    """Validate an agent config for completeness."""
    issues = []
    config = req.config

    if not config.get("agent_name"):
        issues.append(ValidationIssue(field="agent_name", severity="error", message="Agent name is required."))
    if not config.get("greeting_message"):
        issues.append(ValidationIssue(field="greeting_message", severity="warning", message="No greeting message."))
    if not config.get("business_context") and not config.get("role_description"):
        issues.append(ValidationIssue(field="business_context", severity="error", message="Business context or role description is required."))
    if not config.get("human_handoff_conditions") and not config.get("handoff_rules") and not config.get("transfer_rules"):
        issues.append(ValidationIssue(field="handoff_rules", severity="warning", message="No handoff rules defined."))

    status = "valid" if not any(i.severity == "error" for i in issues) else "invalid"
    if status == "valid" and issues:
        status = "warnings"

    return ValidationResponse(status=status, issues=issues)
