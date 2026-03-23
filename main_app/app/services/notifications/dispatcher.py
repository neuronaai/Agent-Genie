"""
Provider-agnostic notification dispatcher.

Selects the active email provider based on configuration and dispatches
both email and in-app notifications.

Usage:
    from app.services.notifications.dispatcher import notify
    notify('welcome', to_email='user@example.com', context={'name': 'Alice'})
"""
import logging
import os
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Email templates — minimal HTML with inline styles for maximum compatibility
# ---------------------------------------------------------------------------
_TEMPLATES = {
    'welcome': {
        'subject': 'Welcome to AgentGenie!',
        'html': '''
<div style="font-family:system-ui,-apple-system,sans-serif;max-width:600px;margin:0 auto;padding:32px;">
  <h1 style="color:#1e293b;font-size:24px;">Welcome to AgentGenie, {name}!</h1>
  <p style="color:#475569;line-height:1.6;">Your account is ready. Start building AI voice agents that handle calls, book appointments, and delight your customers.</p>
  <a href="{dashboard_url}" style="display:inline-block;padding:12px 24px;background:#4f46e5;color:#fff;border-radius:8px;text-decoration:none;font-weight:600;margin-top:16px;">Go to Dashboard</a>
  <p style="color:#94a3b8;font-size:12px;margin-top:32px;">If you did not create this account, please ignore this email.</p>
</div>''',
    },
    'email_verification': {
        'subject': 'Verify your email address',
        'html': '''
<div style="font-family:system-ui,-apple-system,sans-serif;max-width:600px;margin:0 auto;padding:32px;">
  <h1 style="color:#1e293b;font-size:24px;">Verify your email</h1>
  <p style="color:#475569;line-height:1.6;">Click the link below to verify your email address:</p>
  <a href="{verification_url}" style="display:inline-block;padding:12px 24px;background:#4f46e5;color:#fff;border-radius:8px;text-decoration:none;font-weight:600;margin-top:16px;">Verify Email</a>
  <p style="color:#94a3b8;font-size:12px;margin-top:32px;">This link expires in 24 hours.</p>
</div>''',
    },
    'password_reset': {
        'subject': 'Reset your password',
        'html': '''
<div style="font-family:system-ui,-apple-system,sans-serif;max-width:600px;margin:0 auto;padding:32px;">
  <h1 style="color:#1e293b;font-size:24px;">Password Reset</h1>
  <p style="color:#475569;line-height:1.6;">Click the link below to reset your password:</p>
  <a href="{reset_url}" style="display:inline-block;padding:12px 24px;background:#4f46e5;color:#fff;border-radius:8px;text-decoration:none;font-weight:600;margin-top:16px;">Reset Password</a>
  <p style="color:#94a3b8;font-size:12px;margin-top:32px;">If you did not request this, please ignore this email. This link expires in 1 hour.</p>
</div>''',
    },
    'plan_purchased': {
        'subject': 'Your AgentGenie plan is active!',
        'html': '''
<div style="font-family:system-ui,-apple-system,sans-serif;max-width:600px;margin:0 auto;padding:32px;">
  <h1 style="color:#1e293b;font-size:24px;">Plan Activated</h1>
  <p style="color:#475569;line-height:1.6;">Your <strong>{plan_name}</strong> plan is now active. You have {included_minutes} minutes and {included_numbers} phone numbers included.</p>
  <a href="{dashboard_url}" style="display:inline-block;padding:12px 24px;background:#4f46e5;color:#fff;border-radius:8px;text-decoration:none;font-weight:600;margin-top:16px;">Go to Dashboard</a>
</div>''',
    },
    'plan_changed': {
        'subject': 'Your plan has been updated',
        'html': '''
<div style="font-family:system-ui,-apple-system,sans-serif;max-width:600px;margin:0 auto;padding:32px;">
  <h1 style="color:#1e293b;font-size:24px;">Plan Updated</h1>
  <p style="color:#475569;line-height:1.6;">Your plan has been changed from <strong>{old_plan}</strong> to <strong>{new_plan}</strong>. The change takes effect immediately.</p>
</div>''',
    },
    'minute_topup_purchased': {
        'subject': 'Minute top-up confirmed',
        'html': '''
<div style="font-family:system-ui,-apple-system,sans-serif;max-width:600px;margin:0 auto;padding:32px;">
  <h1 style="color:#1e293b;font-size:24px;">Minutes Added</h1>
  <p style="color:#475569;line-height:1.6;"><strong>{minutes}</strong> minutes have been added to your account for <strong>${amount}</strong>.</p>
</div>''',
    },
    'number_purchased': {
        'subject': 'New phone number provisioned',
        'html': '''
<div style="font-family:system-ui,-apple-system,sans-serif;max-width:600px;margin:0 auto;padding:32px;">
  <h1 style="color:#1e293b;font-size:24px;">Phone Number Ready</h1>
  <p style="color:#475569;line-height:1.6;">Your new phone number <strong>{phone_number}</strong> has been provisioned and is ready to use.</p>
</div>''',
    },
    'usage_warning': {
        'subject': 'Usage warning — approaching minute limit',
        'html': '''
<div style="font-family:system-ui,-apple-system,sans-serif;max-width:600px;margin:0 auto;padding:32px;">
  <h1 style="color:#1e293b;font-size:24px;">Usage Warning</h1>
  <p style="color:#475569;line-height:1.6;">You have used <strong>{used_minutes}</strong> of your <strong>{total_minutes}</strong> included minutes ({percentage}%). Consider purchasing a top-up pack to avoid service interruption.</p>
  <a href="{topup_url}" style="display:inline-block;padding:12px 24px;background:#f59e0b;color:#fff;border-radius:8px;text-decoration:none;font-weight:600;margin-top:16px;">Buy Minutes</a>
</div>''',
    },
    'agent_provisioned': {
        'subject': 'Your AI agent is live!',
        'html': '''
<div style="font-family:system-ui,-apple-system,sans-serif;max-width:600px;margin:0 auto;padding:32px;">
  <h1 style="color:#1e293b;font-size:24px;">Agent Live</h1>
  <p style="color:#475569;line-height:1.6;">Your agent <strong>{agent_name}</strong> has been provisioned and is ready to take calls.</p>
  <a href="{agent_url}" style="display:inline-block;padding:12px 24px;background:#4f46e5;color:#fff;border-radius:8px;text-decoration:none;font-weight:600;margin-top:16px;">View Agent</a>
</div>''',
    },
    'agent_failed': {
        'subject': 'Agent provisioning failed',
        'html': '''
<div style="font-family:system-ui,-apple-system,sans-serif;max-width:600px;margin:0 auto;padding:32px;">
  <h1 style="color:#1e293b;font-size:24px;">Provisioning Failed</h1>
  <p style="color:#475569;line-height:1.6;">We were unable to provision your agent <strong>{agent_name}</strong>. Error: {error_message}</p>
  <p style="color:#475569;line-height:1.6;">Please retry from the agent detail page or contact support.</p>
</div>''',
    },
}


def _get_provider():
    """Return the configured email provider instance."""
    provider_name = os.environ.get('NOTIFICATION_EMAIL_PROVIDER', 'gmail_smtp')

    if provider_name == 'gmail_smtp':
        from .providers.smtp_gmail import GmailSMTPProvider
        return GmailSMTPProvider()
    elif provider_name == 'sendgrid':
        # Future: from .providers.sendgrid import SendGridProvider
        # return SendGridProvider()
        raise NotImplementedError('SendGrid provider not yet implemented')
    else:
        raise ValueError(f'Unknown email provider: {provider_name}')


def _create_in_app_notification(
    tenant_id: str,
    title: str,
    message: str = '',
    link: Optional[str] = None,
):
    """Create an in-app notification record."""
    try:
        from app import db
        from app.models.core import Notification
        notif = Notification(
            tenant_id=tenant_id,
            type='in_app',
            title=title,
            message=message,
            subject=title,
            body=message,
            link=link,
            is_read=False,
            status='sent',
        )
        db.session.add(notif)
        db.session.commit()
    except Exception as e:
        logger.exception('Failed to create in-app notification: %s', e)


def notify(
    template_name: str,
    to_email: Optional[str] = None,
    tenant_id: Optional[str] = None,
    context: Optional[dict] = None,
    send_email: bool = True,
    send_in_app: bool = True,
) -> dict:
    """Dispatch a notification using the named template.

    Args:
        template_name: key in _TEMPLATES
        to_email: recipient email (required if send_email=True)
        tenant_id: tenant ID for in-app notification (required if send_in_app=True)
        context: dict of template variables
        send_email: whether to send email
        send_in_app: whether to create in-app notification

    Returns:
        dict with email_result and in_app_result
    """
    ctx = context or {}
    template = _TEMPLATES.get(template_name)
    if not template:
        logger.error('Unknown notification template: %s', template_name)
        return {'error': f'Unknown template: {template_name}'}

    subject = template['subject']
    try:
        html_body = template['html'].format(**ctx)
    except KeyError as e:
        logger.warning('Missing template variable %s for %s — using raw template', e, template_name)
        html_body = template['html']

    result = {}

    # Email
    if send_email and to_email:
        try:
            provider = _get_provider()
            result['email'] = provider.send_email(to_email, subject, html_body)
        except Exception as e:
            logger.exception('Email dispatch failed: %s', e)
            result['email'] = {'status': 'failed', 'error': str(e)}
    elif send_email:
        result['email'] = {'status': 'skipped', 'reason': 'no to_email'}

    # In-app
    if send_in_app and tenant_id:
        # Strip HTML tags for the in-app message display
        import re
        plain_message = re.sub(r'<[^>]+>', '', html_body).strip()[:500] if html_body else ''
        _create_in_app_notification(
            tenant_id=tenant_id,
            title=subject,
            message=plain_message,
        )
        result['in_app'] = {'status': 'created'}
    elif send_in_app:
        result['in_app'] = {'status': 'skipped', 'reason': 'no tenant_id'}

    return result
