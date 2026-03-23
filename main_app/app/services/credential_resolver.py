"""Credential resolver — resolves provider credentials per tenant at runtime.

Execution precedence:
  1. If the tenant has provided their own credentials (credential_mode='tenant')
     and those credentials are stored and valid, use them.
  2. Otherwise, fall back to platform-level credentials from app config.
  3. If neither exists, return an empty dict so the caller can surface a
     clear "setup required" state.

This module is the single entry point for the ToolExecutionEngine to obtain
credentials.  It does NOT store or modify credentials — that is handled by
the credential_manager module.
"""
import logging
from flask import current_app

from app import db

logger = logging.getLogger(__name__)


def resolve_email_credentials(connection) -> tuple[dict, str]:
    """Resolve SendGrid credentials for a tenant tool connection.

    Returns:
        (credentials_dict, credential_source)
        where credential_source is 'tenant', 'platform', or 'none'.
    """
    from app.services.credential_manager import get_credentials

    # 1. Check for tenant-provided credentials
    if connection and connection.credential_mode == 'tenant':
        creds = get_credentials(connection.id, connection.tenant_id)
        if creds and creds.get('api_key'):
            return creds, 'tenant'
        # Tenant mode selected but no valid credentials stored
        logger.warning(f'Tenant {connection.tenant_id} has email credential_mode=tenant but no valid credentials')

    # 2. Fall back to platform credentials
    api_key = current_app.config.get('SENDGRID_API_KEY', '')
    if api_key:
        return {
            'api_key': api_key,
            'from_email': current_app.config.get('SENDGRID_FROM_EMAIL', 'noreply@agentgenie.ai'),
            'from_name': current_app.config.get('SENDGRID_FROM_NAME', 'AgentGenie'),
        }, 'platform'

    # 3. Neither available
    return {}, 'none'


def resolve_sms_credentials(connection) -> tuple[dict, str]:
    """Resolve Twilio credentials for a tenant tool connection.

    Returns:
        (credentials_dict, credential_source)
        where credential_source is 'tenant', 'platform', or 'none'.
    """
    from app.services.credential_manager import get_credentials

    # 1. Check for tenant-provided credentials
    if connection and connection.credential_mode == 'tenant':
        creds = get_credentials(connection.id, connection.tenant_id)
        if creds and creds.get('account_sid') and creds.get('auth_token'):
            return creds, 'tenant'
        logger.warning(f'Tenant {connection.tenant_id} has sms credential_mode=tenant but no valid credentials')

    # 2. Fall back to platform credentials
    account_sid = current_app.config.get('TWILIO_ACCOUNT_SID', '')
    auth_token = current_app.config.get('TWILIO_AUTH_TOKEN', '')
    if account_sid and auth_token:
        return {
            'account_sid': account_sid,
            'auth_token': auth_token,
            'phone_number': current_app.config.get('TWILIO_PHONE_NUMBER', ''),
        }, 'platform'

    # 3. Neither available
    return {}, 'none'


def resolve_credentials_for_category(category: str, connection) -> tuple[dict, str]:
    """Resolve credentials for any tool category.

    Returns:
        (credentials_dict, credential_source)
    """
    if category == 'email':
        return resolve_email_credentials(connection)
    elif category == 'sms':
        return resolve_sms_credentials(connection)
    elif category == 'calendar':
        # Calendar uses OAuth — handled separately by credential_manager.get_valid_credentials
        return {}, 'oauth'
    else:
        # CRM, webhook, note_summary — no tenant credentials needed
        return {}, 'platform'


def get_credential_status(category: str, connection) -> dict:
    """Return a human-readable status of credential configuration for a category.

    Used by the Integrations Hub UI to show the current state.

    Returns:
        {
            'mode': 'platform' | 'tenant',
            'has_platform_credentials': bool,
            'has_tenant_credentials': bool,
            'active_source': 'tenant' | 'platform' | 'none',
            'message': str,
        }
    """
    from app.services.credential_manager import get_credentials

    mode = connection.credential_mode if connection else 'platform'
    has_tenant = False
    has_platform = False

    if connection:
        creds = get_credentials(connection.id, connection.tenant_id)
        if category == 'email':
            has_tenant = bool(creds and creds.get('api_key'))
            has_platform = bool(current_app.config.get('SENDGRID_API_KEY', ''))
        elif category == 'sms':
            has_tenant = bool(creds and creds.get('account_sid') and creds.get('auth_token'))
            has_platform = bool(
                current_app.config.get('TWILIO_ACCOUNT_SID', '') and
                current_app.config.get('TWILIO_AUTH_TOKEN', '')
            )

    # Determine active source
    if mode == 'tenant' and has_tenant:
        active = 'tenant'
        msg = 'Using your own credentials.'
    elif has_platform:
        active = 'platform'
        msg = 'Using platform-managed credentials. Usage is included in your plan.'
    else:
        active = 'none'
        msg = 'No credentials configured. Please set up this integration to use it.'

    return {
        'mode': mode,
        'has_platform_credentials': has_platform,
        'has_tenant_credentials': has_tenant,
        'active_source': active,
        'message': msg,
    }
