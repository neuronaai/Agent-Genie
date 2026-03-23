"""SendGrid email adapter — transactional email delivery for tool actions.

This is the primary email provider for v1.  All outbound email from tool
executions (call summaries, follow-ups, summary delivery) routes through
this adapter rather than through Retell or Flask-Mail directly.

The adapter supports **dual-mode credentials**:
  - Platform-managed: uses the SENDGRID_API_KEY from app config (default).
  - Tenant-provided: uses a per-tenant API key passed at runtime.

The adapter abstraction allows swapping in Postmark, Mailgun, or SES later
by implementing the same interface.

Provider interface:
    send_email(to_email, subject, body_text, body_html, from_email, from_name, credentials) -> dict
    send_call_summary(to_email, call_data, credentials) -> dict
    send_followup(to_email, template_name, call_data, credentials) -> dict
    test_connection(credentials) -> dict
"""
import logging
from flask import current_app

logger = logging.getLogger(__name__)

PROVIDER_NAME = 'sendgrid'


# ---------------------------------------------------------------------------
# Credential resolution helper
# ---------------------------------------------------------------------------
def _resolve_credentials(credentials: dict = None) -> tuple[str, str, str, str]:
    """Resolve API key and sender info from tenant credentials or platform config.

    Returns:
        (api_key, from_email, from_name, credential_source)
    """
    if credentials and credentials.get('api_key'):
        return (
            credentials['api_key'],
            credentials.get('from_email', current_app.config.get('SENDGRID_FROM_EMAIL', 'noreply@agentgenie.ai')),
            credentials.get('from_name', current_app.config.get('SENDGRID_FROM_NAME', 'AgentGenie')),
            'tenant',
        )
    # Fall back to platform credentials
    return (
        current_app.config.get('SENDGRID_API_KEY', ''),
        current_app.config.get('SENDGRID_FROM_EMAIL', 'noreply@agentgenie.ai'),
        current_app.config.get('SENDGRID_FROM_NAME', 'AgentGenie'),
        'platform',
    )


# ---------------------------------------------------------------------------
# Core send function
# ---------------------------------------------------------------------------
def send_email(
    to_email: str,
    subject: str,
    body_text: str = '',
    body_html: str = '',
    from_email: str = None,
    from_name: str = None,
    credentials: dict = None,
) -> dict:
    """Send a single transactional email via SendGrid.

    Args:
        credentials: Optional dict with 'api_key', 'from_email', 'from_name'.
                     If provided and api_key is set, uses tenant credentials.
                     Otherwise falls back to platform config.

    Returns:
        {'status': 'ok', 'message_id': str, 'message': str, 'provider': str, 'credential_source': str}
    or  {'status': 'error', 'message': str, 'provider': str, 'credential_source': str}
    """
    api_key, default_from_email, default_from_name, cred_source = _resolve_credentials(credentials)
    sender_email = from_email or default_from_email
    sender_name = from_name or default_from_name

    if not api_key:
        logger.warning('SendGrid API key not configured — email not sent')
        return {
            'status': 'error',
            'message': 'Email not configured. Please connect SendGrid in Integrations or contact your platform admin.',
            'provider': PROVIDER_NAME,
            'credential_source': 'none',
        }

    if not to_email:
        return {
            'status': 'error',
            'message': 'No recipient email address provided.',
            'provider': PROVIDER_NAME,
            'credential_source': cred_source,
        }

    try:
        from sendgrid import SendGridAPIClient
        from sendgrid.helpers.mail import Mail, Email, To, Content

        message = Mail()
        message.from_email = Email(sender_email, sender_name)
        message.to = To(to_email)
        message.subject = subject

        if body_html:
            message.content = [
                Content('text/plain', body_text or 'Please view this email in an HTML-capable client.'),
                Content('text/html', body_html),
            ]
        else:
            message.content = Content('text/plain', body_text)

        sg = SendGridAPIClient(api_key)
        response = sg.send(message)

        message_id = response.headers.get('X-Message-Id', '')
        logger.info(f'SendGrid email sent to {to_email}: status={response.status_code}, id={message_id}, creds={cred_source}')

        return {
            'status': 'ok',
            'message_id': message_id,
            'http_status': response.status_code,
            'message': f'Email sent to {to_email}.',
            'provider': PROVIDER_NAME,
            'credential_source': cred_source,
        }

    except Exception as e:
        logger.error(f'SendGrid send failed: {e}')
        return {
            'status': 'error',
            'message': f'Email delivery failed: {str(e)}',
            'provider': PROVIDER_NAME,
            'credential_source': cred_source,
        }


# ---------------------------------------------------------------------------
# Test connection
# ---------------------------------------------------------------------------
def test_connection(credentials: dict = None) -> dict:
    """Validate that the provided (or platform) SendGrid API key is functional.

    Makes a lightweight API call to verify the key without sending an email.
    """
    api_key, _, _, cred_source = _resolve_credentials(credentials)

    if not api_key:
        return {
            'status': 'error',
            'message': 'No API key provided.',
            'provider': PROVIDER_NAME,
            'credential_source': cred_source,
        }

    try:
        from sendgrid import SendGridAPIClient
        sg = SendGridAPIClient(api_key)
        # Use the /v3/user/profile endpoint as a lightweight validation
        response = sg.client.user.profile.get()
        if response.status_code in (200, 201):
            return {
                'status': 'ok',
                'message': 'SendGrid API key is valid.',
                'provider': PROVIDER_NAME,
                'credential_source': cred_source,
            }
        return {
            'status': 'error',
            'message': f'SendGrid returned status {response.status_code}.',
            'provider': PROVIDER_NAME,
            'credential_source': cred_source,
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
def send_call_summary(to_email: str, call_data: dict, credentials: dict = None) -> dict:
    """Send a formatted call summary email.

    ``call_data`` should include keys like ``summary``, ``transcript``,
    ``caller_name``, ``caller_phone``, ``agent_name``, ``duration``.
    """
    agent_name = call_data.get('agent_name', 'AI Agent')
    caller_name = call_data.get('caller_name', 'Unknown Caller')
    caller_phone = call_data.get('caller_phone', call_data.get('from_number', ''))
    summary = call_data.get('summary', 'No summary available.')
    duration = call_data.get('duration_seconds', 0)

    subject = f'Call Summary — {caller_name} ({agent_name})'

    body_html = f"""
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 600px; margin: 0 auto;">
        <div style="background: #4f46e5; color: white; padding: 20px 24px; border-radius: 12px 12px 0 0;">
            <h2 style="margin: 0; font-size: 18px;">Call Summary</h2>
            <p style="margin: 4px 0 0; opacity: 0.85; font-size: 14px;">{agent_name}</p>
        </div>
        <div style="background: #f9fafb; padding: 24px; border: 1px solid #e5e7eb; border-top: none; border-radius: 0 0 12px 12px;">
            <table style="width: 100%; font-size: 14px; margin-bottom: 16px;">
                <tr><td style="color: #6b7280; padding: 4px 0;">Caller</td><td style="font-weight: 600;">{caller_name}</td></tr>
                <tr><td style="color: #6b7280; padding: 4px 0;">Phone</td><td>{caller_phone}</td></tr>
                <tr><td style="color: #6b7280; padding: 4px 0;">Duration</td><td>{duration // 60}m {duration % 60}s</td></tr>
            </table>
            <div style="background: white; border: 1px solid #e5e7eb; border-radius: 8px; padding: 16px;">
                <h3 style="margin: 0 0 8px; font-size: 14px; color: #374151;">Summary</h3>
                <p style="margin: 0; font-size: 14px; color: #4b5563; line-height: 1.6;">{summary}</p>
            </div>
            <p style="margin: 16px 0 0; font-size: 12px; color: #9ca3af; text-align: center;">
                Sent by AgentGenie
            </p>
        </div>
    </div>
    """

    body_text = (
        f'Call Summary — {agent_name}\n\n'
        f'Caller: {caller_name} ({caller_phone})\n'
        f'Duration: {duration // 60}m {duration % 60}s\n\n'
        f'Summary:\n{summary}\n\n'
        f'— AgentGenie'
    )

    return send_email(to_email, subject, body_text, body_html, credentials=credentials)


def send_followup(to_email: str, template_name: str, call_data: dict, credentials: dict = None) -> dict:
    """Send a follow-up email using a named template.

    For v1, we use a simple built-in template.  Future versions can support
    custom templates stored per-tenant.
    """
    agent_name = call_data.get('agent_name', 'AI Agent')
    caller_name = call_data.get('caller_name', 'there')

    subject = f'Following up on your call with {agent_name}'

    body_html = f"""
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 600px; margin: 0 auto; padding: 24px;">
        <p style="font-size: 16px; color: #1f2937;">Hi {caller_name},</p>
        <p style="font-size: 14px; color: #4b5563; line-height: 1.6;">
            Thank you for speaking with us today. We wanted to follow up to make sure
            all your questions were answered and to let you know we are here if you need
            anything else.
        </p>
        <p style="font-size: 14px; color: #4b5563; line-height: 1.6;">
            If you would like to schedule another call or have any additional questions,
            please do not hesitate to reach out.
        </p>
        <p style="font-size: 14px; color: #4b5563; margin-top: 24px;">
            Best regards,<br>
            <strong>{agent_name}</strong><br>
            <span style="color: #9ca3af; font-size: 12px;">Powered by AgentGenie</span>
        </p>
    </div>
    """

    body_text = (
        f'Hi {caller_name},\n\n'
        f'Thank you for speaking with us today. We wanted to follow up to make sure '
        f'all your questions were answered.\n\n'
        f'Best regards,\n{agent_name}\nPowered by AgentGenie'
    )

    return send_email(to_email, subject, body_text, body_html, credentials=credentials)
