"""
OpenAI Microservice — Natural Language to Agent Configuration.

Translates user prompts into structured, deployable Retell AI configurations.
Uses Pydantic for strict schema validation, tenacity for network retries,
and a custom validation loop for schema-correction fallbacks.

v2 Improvements:
- Contradiction detection: surfaces conflicting instructions explicitly
- Industry-aware guardrails: tailored to detected industry, avoids irrelevant defaults
- Multi-location routing: generates per-location handoff rules when multiple locations described
"""
import json
import logging
import os
from typing import List, Optional

from pydantic import BaseModel, Field, ValidationError
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pydantic Output Schema
# ---------------------------------------------------------------------------

class HandoffRuleConfig(BaseModel):
    condition: str = Field(
        description="Natural language condition that triggers the handoff "
                    "(e.g., 'user asks for a human agent')."
    )
    destination_number: Optional[str] = Field(
        default=None,
        description="E.164 formatted phone number to transfer to, if provided."
    )
    transfer_message: Optional[str] = Field(
        default=None,
        description="Message the agent says before transferring the call."
    )
    location: Optional[str] = Field(
        default=None,
        description="If this rule applies to a specific location, the location name. "
                    "Null if the rule applies globally."
    )


class GuardrailConfig(BaseModel):
    prohibited_topic: str = Field(
        description="A topic the agent must not discuss."
    )
    fallback_message: str = Field(
        description="The exact phrase the agent should say when this topic is raised."
    )
    industry_relevance: Optional[str] = Field(
        default=None,
        description="The industry this guardrail is relevant to (e.g., 'healthcare', "
                    "'financial', 'legal'). Null if universally applicable."
    )


class ContradictionFlag(BaseModel):
    """A detected contradiction in the user's instructions."""
    category: str = Field(
        description="Category of contradiction: 'tone', 'hours', 'handoff', 'policy', or 'other'."
    )
    description: str = Field(
        description="Human-readable explanation of the contradiction."
    )
    instruction_a: str = Field(
        description="The first conflicting instruction from the user."
    )
    instruction_b: str = Field(
        description="The second conflicting instruction from the user."
    )
    resolution_applied: str = Field(
        description="How the parser resolved the contradiction in the generated config."
    )


class LocationInfo(BaseModel):
    """A detected business location."""
    name: str = Field(description="Location name or identifier (e.g., 'Downtown Office').")
    address: Optional[str] = Field(default=None, description="Address if provided.")
    phone: Optional[str] = Field(default=None, description="Direct phone number if provided.")
    hours: Optional[str] = Field(default=None, description="Operating hours if different from main.")


class AgentConfiguration(BaseModel):
    """The target output schema for the OpenAI microservice."""
    agent_name: str = Field(
        description="A concise, descriptive name for the agent (e.g., 'Sunrise Dental Receptionist')."
    )
    detected_industry: str = Field(
        default="general",
        description="The detected industry of the business (e.g., 'healthcare', 'real_estate', "
                    "'legal', 'financial', 'restaurant', 'retail', 'automotive', 'general')."
    )
    role_description: str = Field(
        description="A comprehensive system prompt defining the agent's persona, goals, "
                    "and behavior. Should be at least 2-3 paragraphs."
    )
    tone: str = Field(
        default="helpful and professional",
        description="The emotional tone of the agent (e.g., 'professional', 'empathetic', 'energetic')."
    )
    business_context: str = Field(
        description="Background information about the company the agent represents, "
                    "including services offered and target audience."
    )
    greeting_message: str = Field(
        description="The first thing the agent says when answering a call."
    )
    handoff_rules: List[HandoffRuleConfig] = Field(
        default_factory=list,
        description="Rules for when the agent should transfer the call to a human. "
                    "Include location-specific rules if multiple locations are detected."
    )
    guardrails: List[GuardrailConfig] = Field(
        default_factory=list,
        description="Topics the agent must avoid discussing. Must be relevant to the "
                    "detected industry — do NOT add medical guardrails to non-medical businesses."
    )
    missing_information: List[str] = Field(
        default_factory=list,
        description="Critical business details the user failed to provide but are "
                    "necessary for a complete, effective agent."
    )
    contradictions: List[ContradictionFlag] = Field(
        default_factory=list,
        description="Conflicting instructions detected in the user's prompt. "
                    "Each contradiction should explain what conflicts and how it was resolved."
    )
    locations: List[LocationInfo] = Field(
        default_factory=list,
        description="Detected business locations. Empty if single-location or not specified."
    )
    is_multi_location: bool = Field(
        default=False,
        description="True if the business has multiple locations mentioned in the prompt."
    )


# ---------------------------------------------------------------------------
# Required vs Optional field validation
# ---------------------------------------------------------------------------
REQUIRED_FIELDS = ['role_description', 'business_context']
OPTIONAL_DEFAULTS = {
    'tone': 'helpful and professional',
    'handoff_rules': [],
    'guardrails': [],
}


def validate_required_fields(config: AgentConfiguration) -> List[str]:
    """Check that required fields are substantive, not just present."""
    issues = []
    if not config.role_description or len(config.role_description.strip()) < 20:
        issues.append("role_description is too short or empty")
    if not config.business_context or len(config.business_context.strip()) < 10:
        issues.append("business_context is too short or empty")
    return issues


# ---------------------------------------------------------------------------
# System Prompt (v2 — with contradiction detection, industry guardrails,
#                      and multi-location routing)
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """You are an expert AI voice agent configurator for a SaaS platform called AgentGenie.

Your job is to take a user's natural language description of their business and desired phone agent, and produce a structured JSON configuration that can be used to deploy a Retell AI voice agent.

You MUST output valid JSON matching this exact schema:
{
  "agent_name": "string — A concise name for the agent",
  "detected_industry": "string — One of: healthcare, dental, real_estate, legal, financial, restaurant, retail, automotive, hospitality, insurance, education, general",
  "role_description": "string — A comprehensive system prompt (2-3 paragraphs minimum) defining the agent's persona, goals, behavior, and how it should handle calls",
  "tone": "string — The emotional tone (e.g., 'professional', 'empathetic', 'energetic')",
  "business_context": "string — Background about the company, services, target audience",
  "greeting_message": "string — The first thing the agent says when answering",
  "handoff_rules": [
    {
      "condition": "string — When to transfer (e.g., 'caller asks for manager')",
      "destination_number": "string or null — E.164 phone number if provided",
      "transfer_message": "string or null — What agent says before transferring",
      "location": "string or null — Which location this rule applies to, null if global"
    }
  ],
  "guardrails": [
    {
      "prohibited_topic": "string — Topic to avoid",
      "fallback_message": "string — What to say instead",
      "industry_relevance": "string or null — Which industry this guardrail is relevant to"
    }
  ],
  "missing_information": [
    "string — Each item is a question about critical info the user did NOT provide"
  ],
  "contradictions": [
    {
      "category": "string — One of: tone, hours, handoff, policy, other",
      "description": "string — Human-readable explanation of the conflict",
      "instruction_a": "string — First conflicting instruction from user",
      "instruction_b": "string — Second conflicting instruction from user",
      "resolution_applied": "string — How you resolved it in the config"
    }
  ],
  "locations": [
    {
      "name": "string — Location name",
      "address": "string or null",
      "phone": "string or null",
      "hours": "string or null"
    }
  ],
  "is_multi_location": false
}

IMPORTANT RULES:

1. CONTRADICTION DETECTION (CRITICAL):
   Carefully scan the user's prompt for conflicting instructions. Common contradictions include:
   - Tone: "be casual" vs "be formal" or "be friendly" vs "be strict"
   - Hours: "open 24/7" vs "closed on weekends" or impossible hours like "25:00"
   - Handoff: "never transfer calls" vs "always transfer to a manager"
   - Policy: "offer discounts freely" vs "never give discounts"
   If you detect ANY contradiction, you MUST add it to the "contradictions" array.
   Still generate the best config you can, but explain your resolution in resolution_applied.
   Do NOT silently choose a middle ground without flagging it.

2. INDUSTRY-AWARE GUARDRAILS (CRITICAL):
   Detect the business industry and generate guardrails RELEVANT to that industry:
   - Healthcare/Dental: medical diagnosis, treatment recommendations, medication advice
   - Legal: legal advice, case outcome predictions, attorney-client privilege topics
   - Financial/Insurance: specific investment advice, guaranteeing returns, tax advice
   - Real Estate: property value guarantees, discrimination in housing
   - Restaurant/Retail: competitor pricing, employee personal information
   - Automotive: admitting fault/liability, competitor disparagement
   - General: always include "do not make promises the business cannot keep"
   DO NOT add "never provide medical advice" to a pizza shop or retail store.
   DO NOT add "never provide legal advice" to a dental office.
   Each guardrail MUST have industry_relevance set to the relevant industry or null if universal.

3. MULTI-LOCATION ROUTING:
   If the user describes multiple locations, offices, or branches:
   - Set is_multi_location to true
   - Populate the locations array with each detected location
   - Create LOCATION-SPECIFIC handoff rules with the "location" field set
   - If a location has its own phone number, use it in the handoff rule
   - If no location-specific number, set destination_number to null and add to missing_information
   - Example: "For callers asking about the Downtown office → transfer to 555-1234"

4. Always generate the BEST possible agent config from whatever information is given.

5. For the role_description, write a detailed, production-quality system prompt that tells the AI agent exactly how to behave, what to say, and how to handle different scenarios. If multi-location, include location-aware routing instructions.

6. Evaluate the user's input against this checklist and add to missing_information if not provided:
   - Company/business name
   - Core services or products offered
   - Target audience or typical callers
   - Business hours / timezone
   - Phone number for transferring to a human (if handoff is desired)
   - Any specific policies or procedures callers commonly ask about
   - For multi-location: individual location phone numbers and hours

7. Always include at least one default handoff rule (e.g., "caller explicitly asks for a human").

8. The greeting_message should be warm and professional, mentioning the business name if known.

9. If the user's prompt is very vague, still generate a reasonable config but put more items in missing_information.

10. Output ONLY valid JSON. No markdown, no code fences, no explanation text."""


# ---------------------------------------------------------------------------
# OpenAI API Call with Network Retries (tenacity)
# ---------------------------------------------------------------------------
class OpenAIServiceUnavailable(Exception):
    """Raised when OpenAI API is completely unreachable."""
    pass


class OpenAIRateLimited(Exception):
    """Raised on 429 or 5xx errors for retry."""
    pass


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(OpenAIRateLimited),
    reraise=True,
)
def _call_openai_api(messages: list, api_key: str) -> str:
    """Make the actual OpenAI API call with network-level retries."""
    import openai

    client = openai.OpenAI(
        api_key=api_key,
        base_url='https://api.openai.com/v1',
        timeout=60.0,
    )

    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=messages,
            response_format={"type": "json_object"},
            temperature=0.7,
            max_tokens=4000,
        )
        return response.choices[0].message.content
    except openai.RateLimitError:
        logger.warning("OpenAI rate limited, retrying...")
        raise OpenAIRateLimited("Rate limited by OpenAI")
    except openai.APIStatusError as e:
        if e.status_code and e.status_code >= 500:
            logger.warning(f"OpenAI server error {e.status_code}, retrying...")
            raise OpenAIRateLimited(f"Server error: {e.status_code}")
        raise
    except openai.APIConnectionError:
        logger.error("OpenAI API connection failed")
        raise OpenAIServiceUnavailable("Cannot connect to OpenAI API")
    except openai.APITimeoutError:
        logger.warning("OpenAI API timeout, retrying...")
        raise OpenAIRateLimited("Request timed out")


# ---------------------------------------------------------------------------
# Main Generation Function with Validation Fallback
# ---------------------------------------------------------------------------
def generate_agent_config(user_prompt: str, api_key: str = None) -> dict:
    """
    Generate a structured agent configuration from a natural language prompt.

    Returns:
        dict with keys:
            - status: 'success' | 'error'
            - data: AgentConfiguration dict (if success)
            - message: error message (if error)
    """
    if not api_key:
        api_key = os.environ.get('OPENAI_API_KEY_CUSTOM', '')

    if not api_key:
        return {
            'status': 'error',
            'message': 'OpenAI API key is not configured. Please contact your administrator.',
        }

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    max_validation_retries = 2
    raw_json_str = None

    for attempt in range(max_validation_retries):
        try:
            raw_json_str = _call_openai_api(messages, api_key)
            logger.info(f"OpenAI response (attempt {attempt + 1}): {raw_json_str[:200]}...")

            # Parse and validate with Pydantic
            config = AgentConfiguration.model_validate_json(raw_json_str)

            # Check required fields are substantive
            field_issues = validate_required_fields(config)
            if field_issues:
                logger.warning(f"Required field issues: {field_issues}")
                for issue in field_issues:
                    if issue not in config.missing_information:
                        config.missing_information.append(issue)

            return {
                'status': 'success',
                'data': config.model_dump(),
            }

        except ValidationError as e:
            logger.warning(f"Pydantic validation failed (attempt {attempt + 1}): {e}")
            if attempt == max_validation_retries - 1:
                # Last attempt — try to salvage what we can
                try:
                    raw_data = json.loads(raw_json_str) if raw_json_str else {}
                    return {
                        'status': 'partial',
                        'data': raw_data,
                        'message': f'Configuration generated but has validation issues: {str(e)[:200]}',
                    }
                except json.JSONDecodeError:
                    return {
                        'status': 'error',
                        'message': 'Failed to generate a valid configuration. Please try rephrasing your request.',
                    }

            # Append validation error and retry
            messages.append({"role": "assistant", "content": raw_json_str})
            messages.append({
                "role": "user",
                "content": f"Your JSON failed validation with error: {str(e)}. "
                           f"Please fix the JSON to match the required schema exactly.",
            })

        except OpenAIServiceUnavailable:
            return {
                'status': 'error',
                'message': 'AI services are currently experiencing high load. Please try again in a few minutes.',
            }

        except OpenAIRateLimited:
            return {
                'status': 'error',
                'message': 'AI services are temporarily busy. Please try again in a moment.',
            }

        except Exception as e:
            logger.exception(f"Unexpected error in generate_agent_config: {e}")
            return {
                'status': 'error',
                'message': f'An unexpected error occurred: {str(e)[:200]}',
            }

    return {
        'status': 'error',
        'message': 'Failed to generate configuration after multiple attempts.',
    }


# ---------------------------------------------------------------------------
# Remediation: Re-generate with additional context
# ---------------------------------------------------------------------------
def remediate_agent_config(
    original_prompt: str,
    remediation_answers: dict,
    api_key: str = None,
) -> dict:
    """
    Re-generate agent config by combining the original prompt with
    user-provided answers to the missing_information questions.

    Args:
        original_prompt: The user's original natural language prompt.
        remediation_answers: Dict mapping question -> answer.
        api_key: OpenAI API key.

    Returns:
        Same format as generate_agent_config().
    """
    # Build an enriched prompt
    enriched_parts = [original_prompt, "\n\nAdditional details provided by the user:"]
    for question, answer in remediation_answers.items():
        enriched_parts.append(f"- {question}: {answer}")

    enriched_prompt = "\n".join(enriched_parts)
    return generate_agent_config(enriched_prompt, api_key=api_key)
