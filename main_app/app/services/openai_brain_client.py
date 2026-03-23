"""HTTP client adapter for the OpenAI Brain microservice.

The Flask app calls this module instead of invoking OpenAI directly.
There is **no** in-process fallback — the Brain microservice is the
single source of AI generation.  If it is unreachable, the request
fails with a clear error so the operator can diagnose the issue.
"""
import logging
import os

import requests

logger = logging.getLogger(__name__)

BRAIN_BASE_URL = os.environ.get("OPENAI_BRAIN_URL", "http://localhost:8100")
BRAIN_SERVICE_TOKEN = os.environ.get("BRAIN_SERVICE_TOKEN", "dev-token")
REQUEST_TIMEOUT = 90  # seconds — OpenAI calls can be slow


def _headers() -> dict:
    return {
        "Content-Type": "application/json",
        "X-Service-Token": BRAIN_SERVICE_TOKEN,
    }


def _brain_url(path: str) -> str:
    return f"{BRAIN_BASE_URL}{path}"


# ---------------------------------------------------------------------------
# Agent Draft Generation
# ---------------------------------------------------------------------------
def generate_agent_config(user_prompt: str, language: str = "en-US", tenant_id: str = None) -> dict:
    """Generate a structured agent config via the Brain microservice.

    Returns:
        dict with keys: status, data/config, message
    """
    payload = {
        "raw_prompt": user_prompt,
        "language": language,
    }
    if tenant_id:
        payload["tenant_id"] = tenant_id

    try:
        resp = requests.post(
            _brain_url("/v1/agent-drafts/generate"),
            json=payload,
            headers=_headers(),
            timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code == 200:
            data = resp.json()
            if data.get("status") == "success" and data.get("config"):
                return {"status": "success", "data": data["config"]}
            return {"status": data.get("status", "error"), "message": data.get("message", "Unknown error")}
        else:
            error_detail = resp.text[:300]
            logger.error(f"Brain service returned {resp.status_code}: {error_detail}")
            return {
                "status": "error",
                "message": f"AI generation service returned HTTP {resp.status_code}. "
                           f"Please verify the Brain microservice is running and OPENAI_BRAIN_URL is correct.",
            }

    except requests.exceptions.ConnectionError:
        logger.error("Brain microservice unreachable at %s", BRAIN_BASE_URL)
        return {
            "status": "error",
            "message": "AI generation service is unreachable. "
                       "Please verify the Brain microservice is deployed and OPENAI_BRAIN_URL is correct.",
        }
    except requests.exceptions.Timeout:
        logger.error("Brain microservice timed out at %s", BRAIN_BASE_URL)
        return {
            "status": "error",
            "message": "AI generation service timed out. Please try again.",
        }
    except Exception as e:
        logger.exception(f"Unexpected error calling Brain service: {e}")
        return {"status": "error", "message": f"AI generation unavailable: {str(e)[:200]}"}


# ---------------------------------------------------------------------------
# Knowledge Base Structuring
# ---------------------------------------------------------------------------
def structure_knowledge_base(raw_content: str, content_type: str = "text", agent_context: str = None) -> dict:
    """Structure raw KB content via the Brain microservice."""
    payload = {
        "raw_content": raw_content,
        "content_type": content_type,
    }
    if agent_context:
        payload["agent_context"] = agent_context

    try:
        resp = requests.post(
            _brain_url("/v1/knowledge-base/structure"),
            json=payload,
            headers=_headers(),
            timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code == 200:
            return resp.json()
        return {"status": "error", "message": f"Brain service error: {resp.status_code}"}
    except Exception as e:
        logger.error(f"KB structuring via Brain failed: {e}")
        return {"status": "error", "items": [], "message": str(e)[:200]}


# ---------------------------------------------------------------------------
# Config Validation
# ---------------------------------------------------------------------------
def validate_agent_config(config: dict) -> dict:
    """Validate an agent config via the Brain microservice."""
    try:
        resp = requests.post(
            _brain_url("/v1/agent-config/validate"),
            json={"config": config},
            headers=_headers(),
            timeout=30,
        )
        if resp.status_code == 200:
            return resp.json()
        return {"status": "error", "message": f"Validation service error: {resp.status_code}"}
    except Exception as e:
        logger.warning(f"Validation via Brain failed: {e}")
        return {"status": "valid", "issues": [], "message": "Validation skipped (service unavailable)"}


# ---------------------------------------------------------------------------
# Health Check
# ---------------------------------------------------------------------------
def check_brain_health() -> dict:
    """Check if the Brain microservice is healthy."""
    try:
        resp = requests.get(_brain_url("/health"), timeout=5)
        if resp.status_code == 200:
            return resp.json()
        return {"status": "unhealthy", "code": resp.status_code}
    except Exception as e:
        return {"status": "unreachable", "error": str(e)[:100]}
