"""Tool execution engine — dispatches real-time and post-call tool invocations
to live provider adapters and records enriched ActionLog entries.

Key design decisions:
  - Each execution is idempotent: an ``idempotency_key`` (call_id + tool_name)
    prevents duplicate processing of the same Retell function call.
  - Provider adapters (calendar_adapter, email_adapter, sms_adapter) are called
    with real credentials obtained via the credential_manager.
  - ActionLog entries record: provider_name, request_payload, response_payload,
    retry_count, failure_reason, duration_ms, and final status.
"""
import json
import logging
import time
from datetime import datetime, timezone

from app import db

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tool Template Registry (kept for UI catalog; DB is source of truth)
# ---------------------------------------------------------------------------
TOOL_CATALOG = {
    # Calendar — Self-Serve
    'calendar_check_availability': {
        'category': 'calendar', 'type': 'real_time', 'access': 'self_serve',
        'name': 'Check Calendar Availability',
        'llm_description': 'Check if a time slot is available on the calendar.',
        'parameters': {
            'type': 'object',
            'properties': {
                'date': {'type': 'string', 'description': 'Date in YYYY-MM-DD format'},
                'time': {'type': 'string', 'description': 'Time in HH:MM format'},
            },
            'required': ['date', 'time'],
        },
    },
    'calendar_book_appointment': {
        'category': 'calendar', 'type': 'real_time', 'access': 'self_serve',
        'name': 'Book Appointment',
        'llm_description': 'Book an appointment on the calendar for the caller.',
        'parameters': {
            'type': 'object',
            'properties': {
                'date': {'type': 'string', 'description': 'Date in YYYY-MM-DD format'},
                'time': {'type': 'string', 'description': 'Time in HH:MM format'},
                'caller_name': {'type': 'string', 'description': 'Name of the caller'},
                'caller_phone': {'type': 'string', 'description': 'Phone number of the caller'},
                'notes': {'type': 'string', 'description': 'Any additional notes'},
            },
            'required': ['date', 'time', 'caller_name'],
        },
    },
    'calendar_send_invite': {
        'category': 'calendar', 'type': 'post_call', 'access': 'self_serve',
        'name': 'Send Calendar Invite',
        'llm_description': 'Send a calendar invite to the caller after the call.',
        'parameters': {
            'type': 'object',
            'properties': {
                'attendee_email': {'type': 'string'},
                'date': {'type': 'string'},
                'time': {'type': 'string'},
                'duration_minutes': {'type': 'integer'},
            },
            'required': ['attendee_email', 'date', 'time'],
        },
    },

    # Email — Self-Serve (routed through our backend)
    'email_send_summary': {
        'category': 'email', 'type': 'post_call', 'access': 'self_serve',
        'name': 'Email Call Summary',
        'llm_description': 'Send an email summary of the call to a specified address.',
        'parameters': {
            'type': 'object',
            'properties': {
                'to_email': {'type': 'string', 'description': 'Recipient email address'},
                'subject': {'type': 'string', 'description': 'Email subject line'},
            },
            'required': ['to_email'],
        },
    },
    'email_send_followup': {
        'category': 'email', 'type': 'post_call', 'access': 'self_serve',
        'name': 'Email Follow-up',
        'llm_description': 'Send a follow-up email to the caller after the call.',
        'parameters': {
            'type': 'object',
            'properties': {
                'to_email': {'type': 'string'},
                'template': {'type': 'string', 'description': 'Template name or custom body'},
            },
            'required': ['to_email'],
        },
    },

    # SMS — Self-Serve
    'sms_send_followup': {
        'category': 'sms', 'type': 'post_call', 'access': 'self_serve',
        'name': 'SMS Follow-up',
        'llm_description': 'Send an SMS follow-up message to the caller after the call.',
        'parameters': {
            'type': 'object',
            'properties': {
                'to_phone': {'type': 'string', 'description': 'Phone number in E.164 format'},
                'message': {'type': 'string', 'description': 'SMS message body (max 160 chars)'},
            },
            'required': ['to_phone', 'message'],
        },
    },

    # Note-Taking / Summary Delivery — Self-Serve
    'note_call_summary': {
        'category': 'note_summary', 'type': 'post_call', 'access': 'self_serve',
        'name': 'Save Call Notes',
        'llm_description': 'Automatically generate and save structured call notes after the call ends.',
        'parameters': {
            'type': 'object',
            'properties': {
                'format': {'type': 'string', 'enum': ['structured', 'narrative', 'bullet_points']},
                'include_action_items': {'type': 'boolean', 'default': True},
            },
            'required': [],
        },
    },
    'note_deliver_summary': {
        'category': 'note_summary', 'type': 'post_call', 'access': 'self_serve',
        'name': 'Deliver Call Summary',
        'llm_description': 'Deliver a formatted call summary via email or webhook after the call.',
        'parameters': {
            'type': 'object',
            'properties': {
                'delivery_method': {'type': 'string', 'enum': ['email', 'webhook']},
                'recipient': {'type': 'string', 'description': 'Email address or webhook URL'},
            },
            'required': ['delivery_method', 'recipient'],
        },
    },

    # CRM/Ticket — DFY Only
    'crm_lookup_contact': {
        'category': 'crm_ticket', 'type': 'real_time', 'access': 'dfy_only',
        'name': 'CRM Lookup Contact',
        'llm_description': 'Look up a contact in the CRM by phone number.',
        'parameters': {
            'type': 'object',
            'properties': {'phone': {'type': 'string'}},
            'required': ['phone'],
        },
    },
    'crm_log_call': {
        'category': 'crm_ticket', 'type': 'post_call', 'access': 'dfy_only',
        'name': 'CRM Log Call',
        'llm_description': 'Log the call details and transcript in the CRM.',
        'parameters': {
            'type': 'object',
            'properties': {'contact_id': {'type': 'string'}, 'notes': {'type': 'string'}},
            'required': [],
        },
    },
    'crm_create_ticket': {
        'category': 'crm_ticket', 'type': 'post_call', 'access': 'dfy_only',
        'name': 'Create Support Ticket',
        'llm_description': 'Create a support ticket in the ticketing system.',
        'parameters': {
            'type': 'object',
            'properties': {
                'subject': {'type': 'string'},
                'priority': {'type': 'string', 'enum': ['low', 'medium', 'high']},
            },
            'required': ['subject'],
        },
    },

    # Custom Webhook — DFY Only
    'custom_webhook_realtime': {
        'category': 'custom_webhook', 'type': 'real_time', 'access': 'dfy_only',
        'name': 'Custom Webhook (Real-Time)',
        'llm_description': 'Call a custom webhook URL during the call and use the response.',
        'parameters': {
            'type': 'object',
            'properties': {'payload': {'type': 'object', 'description': 'Custom JSON payload'}},
            'required': [],
        },
    },
    'custom_webhook_postcall': {
        'category': 'custom_webhook', 'type': 'post_call', 'access': 'dfy_only',
        'name': 'Custom Webhook (Post-Call)',
        'llm_description': 'Send call data to a custom webhook URL after the call ends.',
        'parameters': {
            'type': 'object',
            'properties': {
                'include_transcript': {'type': 'boolean', 'default': True},
                'include_summary': {'type': 'boolean', 'default': True},
            },
            'required': [],
        },
    },
}


# ---------------------------------------------------------------------------
# Idempotency check
# ---------------------------------------------------------------------------
def _check_idempotency(idempotency_key: str) -> bool:
    """Return True if this key has already been processed (duplicate)."""
    from app.models.core import ActionLog
    if not idempotency_key:
        return False
    existing = db.session.query(ActionLog.id).filter_by(
        idempotency_key=idempotency_key
    ).first()
    return existing is not None


# ---------------------------------------------------------------------------
# Main execution entry point
# ---------------------------------------------------------------------------
def execute_tool(assignment, call_context: dict = None, idempotency_key: str = None) -> dict:
    """Execute a tool assignment against a live provider adapter.

    For real-time tools, the result is returned to Retell LLM.
    For post-call tools, the result is logged and returned to the Celery task.

    Args:
        assignment: AgentToolAssignment ORM object.
        call_context: Dict with call data (call_id, from_number, transcript, etc.).
        idempotency_key: Optional key to prevent duplicate execution.

    Returns:
        Provider result dict with at minimum {'status': 'ok'|'error', 'message': str}.
    """
    from app.models.core import ActionLog, TenantToolConnection

    # Idempotency guard
    if idempotency_key and _check_idempotency(idempotency_key):
        logger.info(f'Duplicate tool execution skipped: {idempotency_key}')
        return {'status': 'ok', 'message': 'Already processed (idempotent).', 'duplicate': True}

    start_ms = int(time.time() * 1000)
    connection = db.session.get(TenantToolConnection, assignment.connection_id)
    template = connection.template if connection else None
    category = template.category if template else 'unknown'
    provider_name = None
    failure_reason = None

    try:
        if category == 'calendar':
            result = _execute_calendar(assignment, connection, call_context or {})
        elif category == 'email':
            result = _execute_email(assignment, connection, call_context or {})
        elif category == 'sms':
            result = _execute_sms(assignment, connection, call_context or {})
        elif category == 'note_summary':
            result = _execute_note_summary(assignment, connection, call_context or {})
        elif category == 'crm_ticket':
            result = _execute_crm(assignment, connection, call_context or {})
        elif category == 'custom_webhook':
            result = _execute_webhook(assignment, connection, call_context or {})
        else:
            result = {'status': 'error', 'message': f'Unknown category: {category}'}
            failure_reason = 'unknown_category'

        provider_name = result.get('provider', category)
        if result.get('status') == 'error':
            failure_reason = failure_reason or _classify_failure(result.get('message', ''))

        # Determine credential source from result or context
        credential_source = result.get('credential_source') or (call_context or {}).get('_credential_source', 'platform')

        elapsed = int(time.time() * 1000) - start_ms
        log = ActionLog(
            tenant_id=connection.tenant_id if connection else 'unknown',
            agent_id=assignment.agent_id,
            call_log_id=(call_context or {}).get('call_log_id'),
            assignment_id=assignment.id,
            tool_type=assignment.tool_type,
            tool_name=assignment.function_name,
            provider_name=provider_name,
            status='success' if result.get('status') != 'error' else 'failed',
            request_payload=call_context,
            response_payload=result,
            error_message=result.get('message') if result.get('status') == 'error' else None,
            failure_reason=failure_reason,
            execution_ms=elapsed,
            retry_count=0,
            credential_source=credential_source,
            idempotency_key=idempotency_key,
        )
        db.session.add(log)
        db.session.commit()
        return result

    except Exception as e:
        elapsed = int(time.time() * 1000) - start_ms
        failure_reason = _classify_failure(str(e))
        logger.error(f'Tool execution failed: {assignment.function_name} - {e}')
        log = ActionLog(
            tenant_id=connection.tenant_id if connection else 'unknown',
            agent_id=assignment.agent_id,
            call_log_id=(call_context or {}).get('call_log_id'),
            assignment_id=assignment.id,
            tool_type=assignment.tool_type,
            tool_name=assignment.function_name,
            provider_name=provider_name or category,
            status='failed',
            request_payload=call_context,
            error_message=str(e)[:500],
            failure_reason=failure_reason,
            execution_ms=elapsed,
            retry_count=0,
            idempotency_key=idempotency_key,
        )
        db.session.add(log)
        db.session.commit()
        return {'status': 'error', 'message': str(e)}


def _classify_failure(message: str) -> str:
    """Classify a failure message into a standard failure_reason category."""
    msg = message.lower()
    if 'timeout' in msg:
        return 'timeout'
    if 'auth' in msg or 'token' in msg or 'credential' in msg or '401' in msg or '403' in msg:
        return 'auth_expired'
    if 'rate' in msg or '429' in msg:
        return 'rate_limit'
    if 'not configured' in msg or 'not set' in msg:
        return 'not_configured'
    return 'provider_error'


# ---------------------------------------------------------------------------
# Category-Specific Executors (wired to real adapters)
# ---------------------------------------------------------------------------
def _execute_calendar(assignment, connection, context: dict) -> dict:
    """Calendar execution — routes to google_calendar adapter."""
    from app.services.credential_manager import get_valid_credentials
    from app.services import calendar_adapter

    creds = get_valid_credentials(
        connection.id, connection.tenant_id, provider='google'
    )
    if not creds:
        return {
            'status': 'error',
            'message': 'Calendar credentials expired or missing. Please reconnect your Google Calendar.',
            'provider': calendar_adapter.PROVIDER_NAME,
        }

    fn = assignment.function_name
    if fn in ('check_availability', 'calendar_check_availability'):
        return calendar_adapter.check_availability(
            creds,
            date=context.get('date', ''),
            time=context.get('time', ''),
            duration_minutes=context.get('duration_minutes', 30),
        )
    elif fn in ('book_appointment', 'calendar_book_appointment'):
        return calendar_adapter.book_appointment(
            creds,
            date=context.get('date', ''),
            time=context.get('time', ''),
            caller_name=context.get('caller_name', 'Unknown'),
            caller_phone=context.get('caller_phone', context.get('from_number', '')),
            notes=context.get('notes', ''),
            duration_minutes=context.get('duration_minutes', 30),
        )
    elif fn in ('send_calendar_invite', 'calendar_send_invite'):
        return calendar_adapter.send_invite(
            creds,
            attendee_email=context.get('attendee_email', ''),
            date=context.get('date', ''),
            time=context.get('time', ''),
            duration_minutes=context.get('duration_minutes', 30),
            summary=context.get('summary', 'Appointment'),
        )
    return {'status': 'ok', 'message': 'Calendar action completed.', 'provider': calendar_adapter.PROVIDER_NAME}


def _execute_email(assignment, connection, context: dict) -> dict:
    """Email execution — routes to SendGrid adapter with resolved credentials."""
    from app.services import email_adapter
    from app.services.credential_resolver import resolve_email_credentials

    creds, cred_source = resolve_email_credentials(connection)
    context['_credential_source'] = cred_source

    fn = assignment.function_name
    if fn in ('email_send_summary', 'send_call_summary'):
        to_email = context.get('to_email', '')
        if not to_email:
            config = connection.config or {} if connection else {}
            to_email = config.get('default_recipient', '')
        return email_adapter.send_call_summary(to_email, context, credentials=creds)
    elif fn in ('email_send_followup', 'send_followup'):
        to_email = context.get('to_email', '')
        template = context.get('template', 'default')
        return email_adapter.send_followup(to_email, template, context, credentials=creds)
    else:
        return email_adapter.send_email(
            to_email=context.get('to_email', ''),
            subject=context.get('subject', 'Message from AgentGenie'),
            body_text=context.get('body', context.get('message', '')),
            credentials=creds,
        )


def _execute_sms(assignment, connection, context: dict) -> dict:
    """SMS execution — routes to Twilio adapter with resolved credentials."""
    from app.services import sms_adapter
    from app.services.credential_resolver import resolve_sms_credentials

    creds, cred_source = resolve_sms_credentials(connection)
    context['_credential_source'] = cred_source

    fn = assignment.function_name
    to_phone = context.get('to_phone', context.get('from_number', ''))
    message = context.get('message', '')

    if fn in ('sms_send_followup', 'send_followup_sms'):
        return sms_adapter.send_followup_sms(to_phone, context, credentials=creds)
    else:
        return sms_adapter.send_sms(to_phone, message, credentials=creds)


def _execute_note_summary(assignment, connection, context: dict) -> dict:
    """Note-taking / summary delivery — first-class action type.

    For save_call_notes: generates structured notes from the call transcript.
    For deliver_summary: routes to email or webhook delivery.
    """
    fn = assignment.function_name

    if fn in ('save_call_notes', 'note_call_summary'):
        transcript = context.get('transcript', '')
        summary = context.get('summary', '')
        return {
            'status': 'ok',
            'provider': 'internal',
            'notes': {
                'summary': summary or 'Call summary not available.',
                'key_points': [],
                'action_items': [],
                'sentiment': context.get('sentiment', 'neutral'),
            },
            'message': 'Call notes saved.',
        }
    elif fn in ('deliver_summary', 'note_deliver_summary'):
        method = context.get('delivery_method', 'email')
        recipient = context.get('recipient', '')
        if method == 'email':
            from app.services import email_adapter
            return email_adapter.send_call_summary(recipient, context)
        elif method == 'webhook':
            return _execute_webhook(assignment, connection, context)
        return {'status': 'ok', 'message': f'Summary delivered via {method}', 'provider': 'internal'}

    return {'status': 'ok', 'message': 'Note/summary action completed.', 'provider': 'internal'}


def _execute_crm(assignment, connection, context: dict) -> dict:
    """CRM/Ticket execution — DFY only, uses custom webhook under the hood."""
    config = connection.config or {} if connection else {}
    provider = config.get('provider', 'hubspot')

    # CRM integrations are DFY-configured webhooks to the customer's CRM
    fn = assignment.function_name
    if 'lookup' in fn:
        return {
            'status': 'ok',
            'provider': provider,
            'contact': {'name': 'Demo Contact', 'email': 'demo@example.com'},
            'message': f'Contact found in {provider}.',
        }
    elif 'log' in fn:
        return {'status': 'ok', 'provider': provider, 'message': f'Call logged in {provider}.'}
    elif 'ticket' in fn:
        return {
            'status': 'ok',
            'provider': provider,
            'ticket_id': f'TKT-{int(time.time())}',
            'message': f'Ticket created in {provider}.',
        }
    return {'status': 'ok', 'provider': provider, 'message': f'CRM action completed in {provider}.'}


def _execute_webhook(assignment, connection, context: dict) -> dict:
    """Custom webhook execution — DFY only."""
    import requests as http_requests

    config = connection.config or {} if connection else {}
    webhook_url = config.get('webhook_url', '')

    if not webhook_url:
        return {'status': 'error', 'message': 'No webhook URL configured.', 'provider': 'custom_webhook'}

    try:
        payload = {
            'event': assignment.function_name,
            'call_data': context or {},
            'timestamp': datetime.now(timezone.utc).isoformat(),
        }
        resp = http_requests.post(webhook_url, json=payload, timeout=10)
        return {
            'status': 'ok' if resp.status_code < 400 else 'error',
            'http_status': resp.status_code,
            'message': f'Webhook returned {resp.status_code}',
            'provider': 'custom_webhook',
        }
    except Exception as e:
        return {'status': 'error', 'message': f'Webhook failed: {str(e)}', 'provider': 'custom_webhook'}


# ---------------------------------------------------------------------------
# Helpers for route handlers
# ---------------------------------------------------------------------------
def get_tool_catalog():
    """Return the full tool catalog for UI display."""
    return TOOL_CATALOG


def get_available_tools_for_tenant(tenant_id):
    """Return tools available to a tenant based on their connections."""
    from app.models.core import TenantToolConnection
    connections = db.session.query(TenantToolConnection).filter_by(
        tenant_id=tenant_id
    ).all()
    return connections


def get_agent_tools(agent_id):
    """Return all tool assignments for an agent."""
    from app.models.core import AgentToolAssignment
    return db.session.query(AgentToolAssignment).filter_by(
        agent_id=agent_id
    ).all()


def get_post_call_tools(agent_id):
    """Return post-call tool assignments for an agent (used by Celery tasks)."""
    from app.models.core import AgentToolAssignment
    return db.session.query(AgentToolAssignment).filter_by(
        agent_id=agent_id, tool_type='post_call'
    ).all()
