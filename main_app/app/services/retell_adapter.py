"""
Retell AI Provider Adapter.

All Retell API interactions go through this adapter so the provider
can be swapped without rewriting business logic.

Handles: agent CRUD, phone number management, webhook signature verification.
"""
import hashlib
import hmac
import json
import logging
import os
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
RETELL_BASE_URL = "https://api.retellai.com"
DEFAULT_TIMEOUT = 30  # seconds


def _get_api_key() -> str:
    return os.environ.get('RETELL_API_KEY', '')


def _get_webhook_secret() -> str:
    return os.environ.get('RETELL_WEBHOOK_SECRET', '')


def _headers(api_key: str = None) -> dict:
    key = api_key or _get_api_key()
    return {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }


def _handle_response(resp: requests.Response, context: str) -> dict:
    """Standardized response handling."""
    if resp.status_code in (200, 201):
        return {"status": "success", "data": resp.json()}
    elif resp.status_code == 429:
        logger.warning(f"Retell rate limited during {context}")
        return {"status": "rate_limited", "message": "Retell API rate limited. Will retry."}
    elif resp.status_code >= 500:
        logger.error(f"Retell server error during {context}: {resp.status_code}")
        return {"status": "server_error", "message": f"Retell server error: {resp.status_code}"}
    else:
        error_body = resp.text[:500]
        logger.error(f"Retell API error during {context}: {resp.status_code} — {error_body}")
        return {
            "status": "error",
            "message": f"Retell API error ({resp.status_code}): {error_body}",
            "status_code": resp.status_code,
        }


# ---------------------------------------------------------------------------
# Agent CRUD
# ---------------------------------------------------------------------------
def create_agent(
    agent_name: str,
    role_description: str,
    tone: str = "professional",
    greeting_message: str = None,
    business_context: str = None,
    voice_id: str = None,
    language: str = None,
    api_key: str = None,
) -> dict:
    """
    Create a new agent in Retell AI.

    Returns dict with status and data (including retell agent_id).
    """
    # Build the LLM prompt from our structured config
    system_prompt = role_description
    if business_context:
        system_prompt += f"\n\nBusiness Context: {business_context}"

    payload = {
        "agent_name": agent_name,
        "response_engine": {
            "type": "retell-llm",
            "llm_id": None,  # Will be auto-created
        },
        "voice_id": "11labs-Adrian",  # Default voice, can be customized later
        "language": "en-US",
    }

    # Use the create agent endpoint with LLM
    # Retell API v2: POST /v2/create-agent
    llm_payload = {
        "model": "gpt-4o",
        "general_prompt": system_prompt,
        "begin_message": greeting_message or f"Hello, thank you for calling. How can I help you today?",
    }

    try:
        # Step 1: Create the LLM
        llm_resp = requests.post(
            f"{RETELL_BASE_URL}/create-retell-llm",
            headers=_headers(api_key),
            json=llm_payload,
            timeout=DEFAULT_TIMEOUT,
        )
        llm_result = _handle_response(llm_resp, "create_retell_llm")
        if llm_result["status"] != "success":
            return llm_result

        llm_id = llm_result["data"].get("llm_id")

        # Step 2: Create the agent with the LLM
        agent_payload = {
            "agent_name": agent_name,
            "response_engine": {
                "type": "retell-llm",
                "llm_id": llm_id,
            },
            "voice_id": voice_id or "11labs-Adrian",
            "language": language or "en-US",
        }

        agent_resp = requests.post(
            f"{RETELL_BASE_URL}/create-agent",
            headers=_headers(api_key),
            json=agent_payload,
            timeout=DEFAULT_TIMEOUT,
        )
        agent_result = _handle_response(agent_resp, "create_agent")
        if agent_result["status"] == "success":
            agent_result["data"]["llm_id"] = llm_id

        return agent_result

    except requests.Timeout:
        logger.error("Retell API timeout during create_agent")
        return {"status": "timeout", "message": "Retell API request timed out"}
    except requests.ConnectionError:
        logger.error("Retell API connection error during create_agent")
        return {"status": "connection_error", "message": "Cannot connect to Retell API"}
    except Exception as e:
        logger.exception(f"Unexpected error in create_agent: {e}")
        return {"status": "error", "message": str(e)[:500]}


def update_agent(
    retell_agent_id: str,
    agent_name: str = None,
    voice_id: str = None,
    language: str = None,
    api_key: str = None,
) -> dict:
    """Update an existing Retell agent."""
    payload = {}
    if agent_name:
        payload["agent_name"] = agent_name
    if voice_id:
        payload["voice_id"] = voice_id
    if language:
        payload["language"] = language

    if not payload:
        return {"status": "success", "data": {}, "message": "Nothing to update"}

    try:
        resp = requests.patch(
            f"{RETELL_BASE_URL}/update-agent/{retell_agent_id}",
            headers=_headers(api_key),
            json=payload,
            timeout=DEFAULT_TIMEOUT,
        )
        return _handle_response(resp, "update_agent")
    except requests.Timeout:
        return {"status": "timeout", "message": "Retell API request timed out"}
    except requests.ConnectionError:
        return {"status": "connection_error", "message": "Cannot connect to Retell API"}
    except Exception as e:
        logger.exception(f"Unexpected error in update_agent: {e}")
        return {"status": "error", "message": str(e)[:500]}


def update_retell_llm(
    llm_id: str,
    general_prompt: str = None,
    begin_message: str = None,
    general_tools: list = None,
    api_key: str = None,
) -> dict:
    """Update the LLM configuration for an agent.

    Args:
        llm_id: The Retell LLM ID to update.
        general_prompt: Updated system prompt.
        begin_message: Updated greeting message.
        general_tools: List of tool definitions to register with the LLM.
            Each tool dict should have: name, description, url, speak_during_execution,
            speak_after_execution, and parameters.
        api_key: Optional override API key.
    """
    payload = {}
    if general_prompt:
        payload["general_prompt"] = general_prompt
    if begin_message:
        payload["begin_message"] = begin_message
    if general_tools is not None:
        payload["general_tools"] = general_tools

    if not payload:
        return {"status": "success", "data": {}, "message": "Nothing to update"}

    try:
        resp = requests.patch(
            f"{RETELL_BASE_URL}/update-retell-llm/{llm_id}",
            headers=_headers(api_key),
            json=payload,
            timeout=DEFAULT_TIMEOUT,
        )
        return _handle_response(resp, "update_retell_llm")
    except requests.Timeout:
        return {"status": "timeout", "message": "Retell API request timed out"}
    except requests.ConnectionError:
        return {"status": "connection_error", "message": "Cannot connect to Retell API"}
    except Exception as e:
        logger.exception(f"Unexpected error in update_retell_llm: {e}")
        return {"status": "error", "message": str(e)[:500]}


def get_agent(retell_agent_id: str, api_key: str = None) -> dict:
    """Get details of a Retell agent."""
    try:
        resp = requests.get(
            f"{RETELL_BASE_URL}/get-agent/{retell_agent_id}",
            headers=_headers(api_key),
            timeout=DEFAULT_TIMEOUT,
        )
        return _handle_response(resp, "get_agent")
    except Exception as e:
        logger.exception(f"Unexpected error in get_agent: {e}")
        return {"status": "error", "message": str(e)[:500]}


def delete_agent(retell_agent_id: str, api_key: str = None) -> dict:
    """Delete a Retell agent."""
    try:
        resp = requests.delete(
            f"{RETELL_BASE_URL}/delete-agent/{retell_agent_id}",
            headers=_headers(api_key),
            timeout=DEFAULT_TIMEOUT,
        )
        if resp.status_code == 204:
            return {"status": "success", "data": {}}
        return _handle_response(resp, "delete_agent")
    except Exception as e:
        logger.exception(f"Unexpected error in delete_agent: {e}")
        return {"status": "error", "message": str(e)[:500]}


def list_agents(api_key: str = None) -> dict:
    """List all agents in the Retell account."""
    try:
        resp = requests.get(
            f"{RETELL_BASE_URL}/list-agents",
            headers=_headers(api_key),
            timeout=DEFAULT_TIMEOUT,
        )
        return _handle_response(resp, "list_agents")
    except Exception as e:
        logger.exception(f"Unexpected error in list_agents: {e}")
        return {"status": "error", "message": str(e)[:500]}


def list_voices(api_key: str = None) -> dict:
    """List all available voices from Retell."""
    try:
        resp = requests.get(
            f"{RETELL_BASE_URL}/list-voices",
            headers=_headers(api_key),
            timeout=DEFAULT_TIMEOUT,
        )
        return _handle_response(resp, "list_voices")
    except Exception as e:
        logger.exception(f"Unexpected error in list_voices: {e}")
        return {"status": "error", "message": str(e)[:500]}


# ---------------------------------------------------------------------------
# Phone Number Management
# ---------------------------------------------------------------------------
def list_phone_numbers(api_key: str = None) -> dict:
    """List available phone numbers from Retell."""
    try:
        resp = requests.get(
            f"{RETELL_BASE_URL}/list-phone-numbers",
            headers=_headers(api_key),
            timeout=DEFAULT_TIMEOUT,
        )
        return _handle_response(resp, "list_phone_numbers")
    except Exception as e:
        logger.exception(f"Unexpected error in list_phone_numbers: {e}")
        return {"status": "error", "message": str(e)[:500]}


def purchase_phone_number(
    area_code: str = "415",
    api_key: str = None,
) -> dict:
    """Purchase a new phone number from Retell."""
    payload = {"area_code": int(area_code)}
    try:
        resp = requests.post(
            f"{RETELL_BASE_URL}/create-phone-number",
            headers=_headers(api_key),
            json=payload,
            timeout=DEFAULT_TIMEOUT,
        )
        return _handle_response(resp, "purchase_phone_number")
    except Exception as e:
        logger.exception(f"Unexpected error in purchase_phone_number: {e}")
        return {"status": "error", "message": str(e)[:500]}


def assign_phone_number(
    phone_number_id: str,
    agent_id: str,
    api_key: str = None,
) -> dict:
    """Assign a phone number to an agent in Retell."""
    payload = {"agent_id": agent_id}
    try:
        resp = requests.patch(
            f"{RETELL_BASE_URL}/update-phone-number/{phone_number_id}",
            headers=_headers(api_key),
            json=payload,
            timeout=DEFAULT_TIMEOUT,
        )
        return _handle_response(resp, "assign_phone_number")
    except Exception as e:
        logger.exception(f"Unexpected error in assign_phone_number: {e}")
        return {"status": "error", "message": str(e)[:500]}


def release_phone_number(phone_number_id: str, api_key: str = None) -> dict:
    """Release/delete a phone number from Retell."""
    try:
        resp = requests.delete(
            f"{RETELL_BASE_URL}/delete-phone-number/{phone_number_id}",
            headers=_headers(api_key),
            timeout=DEFAULT_TIMEOUT,
        )
        if resp.status_code == 204:
            return {"status": "success", "data": {}}
        return _handle_response(resp, "release_phone_number")
    except Exception as e:
        logger.exception(f"Unexpected error in release_phone_number: {e}")
        return {"status": "error", "message": str(e)[:500]}


# ---------------------------------------------------------------------------
# Outbound Calling
# ---------------------------------------------------------------------------
def create_phone_call(
    from_number: str,
    to_number: str,
    agent_id: str,
    metadata: dict = None,
    dynamic_variables: dict = None,
    api_key: str = None,
) -> dict:
    """Initiate a single outbound phone call via Retell."""
    payload = {
        "from_number": from_number,
        "to_number": to_number,
        "override_agent_id": agent_id,
    }
    if metadata:
        payload["metadata"] = metadata
    if dynamic_variables:
        payload["retell_llm_dynamic_variables"] = dynamic_variables

    try:
        resp = requests.post(
            f"{RETELL_BASE_URL}/v2/create-phone-call",
            headers=_headers(api_key),
            json=payload,
            timeout=DEFAULT_TIMEOUT,
        )
        return _handle_response(resp, "create_phone_call")
    except requests.Timeout:
        return {"status": "timeout", "message": "Retell API request timed out"}
    except requests.ConnectionError:
        return {"status": "connection_error", "message": "Cannot connect to Retell API"}
    except Exception as e:
        logger.exception(f"Unexpected error in create_phone_call: {e}")
        return {"status": "error", "message": str(e)[:500]}


def create_batch_call(
    from_number: str,
    tasks: list,
    name: str = None,
    trigger_timestamp: int = None,
    call_time_window: dict = None,
    api_key: str = None,
) -> dict:
    """
    Create a batch outbound call via Retell.

    tasks: list of dicts, each with 'to_number' and optional
           'retell_llm_dynamic_variables', 'metadata'.
    trigger_timestamp: Unix ms timestamp for scheduled start.
    call_time_window: {windows: [{start, end}], timezone, day}.
    """
    payload = {
        "from_number": from_number,
        "tasks": tasks,
    }
    if name:
        payload["name"] = name
    if trigger_timestamp:
        payload["trigger_timestamp"] = trigger_timestamp
    if call_time_window:
        payload["call_time_window"] = call_time_window

    try:
        resp = requests.post(
            f"{RETELL_BASE_URL}/create-batch-call",
            headers=_headers(api_key),
            json=payload,
            timeout=60,  # Batch calls may take longer
        )
        return _handle_response(resp, "create_batch_call")
    except requests.Timeout:
        return {"status": "timeout", "message": "Retell API request timed out"}
    except requests.ConnectionError:
        return {"status": "connection_error", "message": "Cannot connect to Retell API"}
    except Exception as e:
        logger.exception(f"Unexpected error in create_batch_call: {e}")
        return {"status": "error", "message": str(e)[:500]}


def get_batch_call(batch_call_id: str, api_key: str = None) -> dict:
    """Get the status and details of a batch call."""
    try:
        resp = requests.get(
            f"{RETELL_BASE_URL}/get-batch-call/{batch_call_id}",
            headers=_headers(api_key),
            timeout=DEFAULT_TIMEOUT,
        )
        return _handle_response(resp, "get_batch_call")
    except Exception as e:
        logger.exception(f"Unexpected error in get_batch_call: {e}")
        return {"status": "error", "message": str(e)[:500]}


# ---------------------------------------------------------------------------
# Webhook Signature Verification
# ---------------------------------------------------------------------------
def verify_webhook_signature(
    payload_body: bytes,
    signature_header: str,
    secret: str = None,
) -> bool:
    """
    Verify the Retell webhook signature.

    Retell signs webhooks using HMAC-SHA256.
    The signature is sent in the x-retell-signature header.
    """
    if not secret:
        secret = _get_webhook_secret()

    if not secret or not signature_header:
        logger.warning("Missing webhook secret or signature header")
        return False

    try:
        expected = hmac.new(
            secret.encode('utf-8'),
            payload_body,
            hashlib.sha256,
        ).hexdigest()

        return hmac.compare_digest(expected, signature_header)
    except Exception as e:
        logger.exception(f"Webhook signature verification failed: {e}")
        return False


# ---------------------------------------------------------------------------
# Health Check
# ---------------------------------------------------------------------------
def check_connection(api_key: str = None) -> dict:
    """Check if the Retell API is reachable and the key is valid."""
    try:
        resp = requests.get(
            f"{RETELL_BASE_URL}/list-agents",
            headers=_headers(api_key),
            timeout=10,
        )
        if resp.status_code == 200:
            return {"status": "connected", "message": "Retell AI connected"}
        elif resp.status_code == 401:
            return {"status": "auth_error", "message": "Invalid Retell API key"}
        else:
            return {"status": "error", "message": f"Retell returned {resp.status_code}"}
    except requests.ConnectionError:
        return {"status": "unreachable", "message": "Cannot reach Retell API"}
    except Exception as e:
        return {"status": "error", "message": str(e)[:200]}
