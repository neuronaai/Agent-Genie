"""Twilio SMS adapter — outbound SMS delivery for tool actions.

This is the primary SMS provider for v1.  The adapter supports **dual-mode
credentials**:
  - Platform-managed: uses TWILIO_* env vars from app config (default).
  - Tenant-provided: uses per-tenant SID/auth token passed at runtime.

The adapter abstraction allows swapping in Vonage, Plivo, or AWS SNS later
by implementing the same interface.

Provider interface:
    send_sms(to_phone, message, from_phone, credentials) -> dict
    send_followup_sms(to_phone, call_data, credentials) -> dict
    test_connection(credentials) -> dict
"""
import logging
import re

from flask import current_app

logger = logging.getLogger(__name__)

PROVIDER_NAME = 'twilio'

# E.164 format: +[country code][number], 8-15 digits total
E164_PATTERN = re.compile(r'^\+[1-9]\d{7,14}$')


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _normalize_phone(phone: str) -> str:
    """Attempt to normalize a phone number to E.164 format.

    If the number is already E.164, return as-is.
    If it looks like a US number without country code, prepend +1.
    """
    cleaned = re.sub(r'[\s\-\(\)\.]+', '', phone.strip())
    if E164_PATTERN.match(cleaned):
        return cleaned
    # Try adding US country code
    if cleaned.startswith('1') and len(cleaned) == 11:
        return f'+{cleaned}'
    if len(cleaned) == 10:
        return f'+1{cleaned}'
    # Return as-is and let Twilio validate
    return cleaned


# ---------------------------------------------------------------------------
# Credential resolution helper
# ---------------------------------------------------------------------------
def _resolve_credentials(credentials: dict = None) -> tuple[str, str, str, str]:
    """Resolve Twilio SID, auth token, and phone from tenant or platform config.

    Returns:
        (account_sid, auth_token, phone_number, credential_source)
    """
    if credentials and credentials.get('account_sid') and credentials.get('auth_token'):
        return (
            credentials['account_sid'],
            credentials['auth_token'],
            credentials.get('phone_number', current_app.config.get('TWILIO_PHONE_NUMBER', '')),
            'tenant',
        )
    # Fall back to platform credentials
    return (
        current_app.config.get('TWILIO_ACCOUNT_SID', ''),
        current_app.config.get('TWILIO_AUTH_TOKEN', ''),
        current_app.config.get('TWILIO_PHONE_NUMBER', ''),
        'platform',
    )


# ---------------------------------------------------------------------------
# Core send function
# ---------------------------------------------------------------------------
def send_sms(to_phone: str, message: str, from_phone: str = None, credentials: dict = None) -> dict:
    """Send an SMS message via Twilio.

    Args:
        credentials: Optional dict with 'account_sid', 'auth_token', 'phone_number'.
                     If provided and valid, uses tenant credentials.
                     Otherwise falls back to platform config.

    Returns:
        {'status': 'ok', 'message_sid': str, 'message': str, 'provider': str, 'credential_source': str}
    or  {'status': 'error', 'message': str, 'provider': str, 'credential_source': str}
    """
    account_sid, auth_token, default_from, cred_source = _resolve_credentials(credentials)
    sender = from_phone or default_from

    if not account_sid or not auth_token:
        logger.warning('Twilio credentials not configured — SMS not sent')
        return {
            'status': 'error',
            'message': 'SMS not configured. Please connect Twilio in Integrations or contact your platform admin.',
            'provider': PROVIDER_NAME,
            'credential_source': 'none',
        }

    if not to_phone:
        return {
            'status': 'error',
            'message': 'No recipient phone number provided.',
            'provider': PROVIDER_NAME,
            'credential_source': cred_source,
        }

    if not sender:
        return {
            'status': 'error',
            'message': 'No Twilio phone number configured.',
            'provider': PROVIDER_NAME,
            'credential_source': cred_source,
        }

    # Normalize and validate
    to_normalized = _normalize_phone(to_phone)

    # Truncate message to SMS limit
    if len(message) > 1600:
        message = message[:1597] + '...'
        logger.warning(f'SMS message truncated to 1600 chars for {to_normalized}')

    try:
        from twilio.rest import Client

        client = Client(account_sid, auth_token)
        msg = client.messages.create(
            body=message,
            from_=sender,
            to=to_normalized,
        )

        logger.info(f'Twilio SMS sent to {to_normalized}: sid={msg.sid}, status={msg.status}, creds={cred_source}')

        return {
            'status': 'ok',
            'message_sid': msg.sid,
            'twilio_status': msg.status,
            'message': f'SMS sent to {to_normalized}.',
            'provider': PROVIDER_NAME,
            'credential_source': cred_source,
        }

    except Exception as e:
        logger.error(f'Twilio SMS send failed: {e}')
        return {
            'status': 'error',
            'message': f'SMS delivery failed: {str(e)}',
            'provider': PROVIDER_NAME,
            'credential_source': cred_source,
        }


# ---------------------------------------------------------------------------
# Test connection
# ---------------------------------------------------------------------------
def test_connection(credentials: dict = None) -> dict:
    """Validate that the provided (or platform) Twilio credentials are functional.

    Makes a lightweight API call to verify the SID/token without sending an SMS.
    """
    account_sid, auth_token, phone_number, cred_source = _resolve_credentials(credentials)

    if not account_sid or not auth_token:
        return {
            'status': 'error',
            'message': 'No Twilio credentials provided.',
            'provider': PROVIDER_NAME,
            'credential_source': cred_source,
        }

    try:
        from twilio.rest import Client
        client = Client(account_sid, auth_token)
        # Fetch the account to verify credentials
        account = client.api.accounts(account_sid).fetch()
        return {
            'status': 'ok',
            'message': f'Twilio account verified: {account.friendly_name}',
            'provider': PROVIDER_NAME,
            'credential_source': cred_source,
            'phone_number': phone_number or 'Not configured',
        }
    except Exception as e:
        return {
            'status': 'error',
            'message': f'Connection test failed: {str(e)}',
            'provider': PROVIDER_NAME,
            'credential_source': cred_source,
        }


# ---------------------------------------------------------------------------
# High-level tool actions
# ---------------------------------------------------------------------------
def send_followup_sms(to_phone: str, call_data: dict, credentials: dict = None) -> dict:
    """Send a follow-up SMS after a call.

    Constructs a brief, professional message from the call context.
    """
    agent_name = call_data.get('agent_name', 'our team')
    caller_name = call_data.get('caller_name', '')

    greeting = f'Hi {caller_name}, t' if caller_name else 'T'
    message = (
        f'{greeting}hank you for your call with {agent_name}. '
        f'If you have any questions, feel free to call us back anytime. '
        f'— AgentGenie'
    )

    return send_sms(to_phone, message, credentials=credentials)
