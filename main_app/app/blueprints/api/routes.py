"""API blueprint — webhook ingestion and internal polling endpoints."""
import hashlib
import hmac
import json
import logging
from datetime import datetime

from flask import Blueprint, request, jsonify, current_app

from app import db, csrf
from app.models.core import WebhookEvent, Agent, CallLog, AgentConfig

logger = logging.getLogger(__name__)

api_bp = Blueprint('api', __name__)


def _verify_retell_signature(payload_bytes: bytes, signature: str) -> bool:
    """Verify Retell webhook signature using HMAC-SHA256."""
    secret = current_app.config.get('RETELL_WEBHOOK_SECRET', '')
    if not secret:
        logger.warning("RETELL_WEBHOOK_SECRET not configured — skipping verification")
        return True  # Allow in dev; enforce in production
    expected = hmac.new(
        secret.encode('utf-8'),
        payload_bytes,
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def _check_idempotency(provider: str, event_id: str) -> bool:
    """Return True if this event has already been processed (idempotent check)."""
    if not event_id:
        return False
    existing = WebhookEvent.query.filter_by(
        provider=provider,
        idempotency_key=event_id
    ).first()
    return existing is not None


def _process_retell_event(event: WebhookEvent):
    """Process a Retell webhook event based on its type."""
    payload = event.payload
    if isinstance(payload, str):
        payload = json.loads(payload)

    event_type = event.event_type

    try:
        if event_type == 'call_started':
            _handle_call_started(payload)
        elif event_type == 'call_ended':
            _handle_call_ended(payload)
        elif event_type == 'call_analyzed':
            _handle_call_analyzed(payload)
        elif event_type == 'agent_updated':
            _handle_agent_updated(payload)
        elif event_type in ('function_call', 'tool_call'):
            # Real-time tool invocation handled via dedicated endpoint
            logger.info(f'Function call event received via webhook: {event_type}')
        else:
            logger.info(f"Unhandled Retell event type: {event_type}")

        event.status = 'processed'
        event.processed_at = datetime.utcnow()
    except Exception as e:
        logger.error(f"Error processing Retell event {event.id}: {e}")
        event.status = 'failed'
        event.error_message = str(e)[:500]

    db.session.commit()


def _handle_call_started(payload):
    """Handle a call_started event from Retell."""
    call_data = payload.get('call', payload.get('data', payload))
    retell_call_id = call_data.get('call_id')
    retell_agent_id = call_data.get('agent_id')

    if not retell_call_id:
        return

    # Find the agent by retell_agent_id
    agent = Agent.query.filter_by(retell_agent_id=retell_agent_id).first()
    if not agent:
        logger.warning(f"call_started for unknown retell_agent_id: {retell_agent_id}")
        return

    # Check if call already exists
    existing = CallLog.query.filter_by(retell_call_id=retell_call_id).first()
    if existing:
        return

    call = CallLog(
        tenant_id=agent.tenant_id,
        agent_id=agent.id,
        retell_call_id=retell_call_id,
        direction=call_data.get('direction', 'inbound'),
        from_number=call_data.get('from_number', ''),
        to_number=call_data.get('to_number', ''),
        status='in_progress',
        started_at=datetime.utcnow(),
    )
    db.session.add(call)
    db.session.commit()


def _handle_call_ended(payload):
    """Handle a call_ended event from Retell."""
    call_data = payload.get('call', payload.get('data', payload))
    retell_call_id = call_data.get('call_id')

    if not retell_call_id:
        return

    call = CallLog.query.filter_by(retell_call_id=retell_call_id).first()
    if not call:
        # Create it if we missed the call_started event
        retell_agent_id = call_data.get('agent_id')
        agent = Agent.query.filter_by(retell_agent_id=retell_agent_id).first()
        if not agent:
            return
        call = CallLog(
            tenant_id=agent.tenant_id,
            agent_id=agent.id,
            retell_call_id=retell_call_id,
            direction=call_data.get('direction', 'inbound'),
            from_number=call_data.get('from_number', ''),
            to_number=call_data.get('to_number', ''),
            status='completed',
            started_at=datetime.utcnow(),
        )
        db.session.add(call)

    call.status = 'completed'
    call.ended_at = datetime.utcnow()
    call.duration_seconds = call_data.get('duration_ms', 0) // 1000
    call.retell_cost = call_data.get('cost', 0.0)

    # Store transcript if available
    transcript = call_data.get('transcript', '')
    if transcript:
        call.transcript = transcript

    db.session.commit()

    # Record usage for billing
    try:
        from app.services.billing_engine import record_call_usage
        if call.duration_seconds and call.duration_seconds > 0:
            record_call_usage(
                call_log_id=call.id,
                tenant_id=call.tenant_id,
                duration_seconds=call.duration_seconds,
                provider_reported_seconds=call_data.get('duration_ms', 0) // 1000,
            )
    except Exception as e:
        logger.error(f"Failed to record usage for call {call.id}: {e}")


def _handle_call_analyzed(payload):
    """Handle a call_analyzed event (post-call analysis from Retell)."""
    call_data = payload.get('call', payload.get('data', payload))
    retell_call_id = call_data.get('call_id')

    if not retell_call_id:
        return

    call = CallLog.query.filter_by(retell_call_id=retell_call_id).first()
    if not call:
        return

    # Store analysis data
    analysis = call_data.get('call_analysis', {})
    if analysis:
        call.sentiment = analysis.get('user_sentiment', '')
        call.summary = analysis.get('call_summary', '')

    db.session.commit()

    # Dispatch post-call tool executions via Celery
    try:
        from app.tasks.post_call_tasks import dispatch_post_call_tools
        post_call_context = {
            'transcript': call.transcript or call_data.get('transcript', ''),
            'summary': call.summary or '',
            'sentiment': call.sentiment or '',
            'from_number': call.from_number or '',
            'to_number': call.to_number or '',
            'duration_seconds': call.duration_seconds or 0,
            'agent_name': call.agent.name if call.agent_id else '',
            'retell_call_id': retell_call_id,
        }
        dispatch_post_call_tools(call.agent_id, call.id, post_call_context)
    except Exception as e:
        logger.error(f'Failed to dispatch post-call tools for call {call.id}: {e}')


def _handle_agent_updated(payload):
    """Handle an agent_updated event from Retell."""
    agent_data = payload.get('agent', payload.get('data', payload))
    retell_agent_id = agent_data.get('agent_id')

    if not retell_agent_id:
        return

    agent = Agent.query.filter_by(retell_agent_id=retell_agent_id).first()
    if agent:
        logger.info(f"Agent {agent.id} updated externally in Retell")


# =========================================================================
# Retell Webhook Endpoint
# =========================================================================
@api_bp.route('/webhooks/retell', methods=['POST'])
@csrf.exempt
def retell_webhook():
    """
    Ingest Retell AI webhook events.
    - Verify signature
    - Check idempotency
    - Store raw event
    - Process event
    """
    payload_bytes = request.get_data()
    signature = request.headers.get('X-Retell-Signature', '')

    # Verify signature
    if not _verify_retell_signature(payload_bytes, signature):
        logger.warning("Retell webhook signature verification failed")
        return jsonify({'error': 'Invalid signature'}), 401

    payload = request.get_json(silent=True) or {}
    event_type = payload.get('event', payload.get('event_type', 'unknown'))
    event_id = payload.get('event_id', payload.get('call', {}).get('call_id', ''))

    # Idempotency check
    if _check_idempotency('retell', event_id):
        logger.info(f"Duplicate Retell event skipped: {event_id}")
        return jsonify({'status': 'already_processed'}), 200

    # Store raw event
    event = WebhookEvent(
        provider='retell',
        event_type=event_type,
        payload=payload,
        idempotency_key=event_id if event_id else None,
        status='pending',
    )
    db.session.add(event)
    db.session.commit()

    # Process event
    _process_retell_event(event)

    return jsonify({'status': 'received'}), 200


# =========================================================================
# Stripe Webhook Endpoint
# =========================================================================
@api_bp.route('/webhooks/stripe', methods=['POST'])
@csrf.exempt
def stripe_webhook():
    """Ingest and process Stripe webhook events with idempotency."""
    from app.services import stripe_adapter
    from app.services.billing_engine import process_stripe_webhook

    payload_bytes = request.get_data()
    sig_header = request.headers.get('Stripe-Signature', '')

    # Verify signature
    verify_result = stripe_adapter.verify_webhook_signature(payload_bytes, sig_header)
    if verify_result['status'] != 'success':
        logger.warning(f"Stripe webhook signature verification failed: {verify_result.get('message')}")
        return jsonify({'error': 'Invalid signature'}), 401

    event_data = verify_result['data']
    event_id = event_data.get('id', '')
    event_type = event_data.get('type', 'unknown')

    # Idempotency check
    if _check_idempotency('stripe', event_id):
        logger.info(f"Duplicate Stripe event skipped: {event_id}")
        return jsonify({'status': 'already_processed'}), 200

    # Store raw event
    event = WebhookEvent(
        provider='stripe',
        event_type=event_type,
        payload=event_data if isinstance(event_data, dict) else {},
        idempotency_key=event_id if event_id else None,
        status='pending',
    )
    db.session.add(event)
    db.session.commit()

    # Process event
    try:
        result = process_stripe_webhook(event_data)
        event.status = 'processed' if result['status'] == 'success' else 'failed'
        if result.get('message'):
            event.error_message = result['message'][:500]
        event.processed_at = datetime.utcnow()
    except Exception as e:
        logger.error(f"Error processing Stripe event {event_id}: {e}")
        event.status = 'failed'
        event.error_message = str(e)[:500]

    db.session.commit()
    return jsonify({'status': 'received'}), 200


# =========================================================================
# Internal Polling Endpoint
# =========================================================================
@api_bp.route('/internal/provisioning-status/<agent_id>', methods=['GET'])
def provisioning_status(agent_id):
    """Polled by UI to get real-time status of agent provisioning."""
    agent = db.session.get(Agent, agent_id)
    if not agent:
        return jsonify({'error': 'Not found'}), 404
    return jsonify({
        'agent_id': agent.id,
        'status': agent.status,
        'retell_agent_id': agent.retell_agent_id,
        'name': agent.name,
    }), 200


# =========================================================================
# Retell Real-Time Function Call Endpoint
# =========================================================================
@api_bp.route('/webhooks/retell/function-call', methods=['POST'])
@csrf.exempt
def retell_function_call():
    """Handle real-time function calls from Retell LLM during a live call.

    Retell sends a POST when the LLM invokes a registered tool.  We execute
    the tool synchronously and return the result so the agent can speak it
    to the caller.

    Idempotency: keyed on ``{call_id}:{function_name}:{invocation_id}``.
    If the same invocation is received twice, we return the cached result.

    Expected payload structure (Retell Custom LLM):
        {
            "call_id": "...",
            "agent_id": "...",
            "function_name": "calendar_check_availability",
            "arguments": {"date": "2026-03-20", "time": "14:00"},
            "invocation_id": "unique-per-invocation"
        }
    """
    payload = request.get_json(silent=True) or {}
    call_id = payload.get('call_id', '')
    retell_agent_id = payload.get('agent_id', '')
    function_name = payload.get('function_name', '')
    arguments = payload.get('arguments', {})
    invocation_id = payload.get('invocation_id', '')

    if not function_name:
        return jsonify({'error': 'Missing function_name'}), 400

    # Build idempotency key
    idempotency_key = f'rt:{call_id}:{function_name}:{invocation_id}' if invocation_id else ''

    # Check for duplicate invocation
    if idempotency_key:
        from app.models.core import ActionLog
        existing_log = ActionLog.query.filter_by(idempotency_key=idempotency_key).first()
        if existing_log:
            logger.info(f'Duplicate function call skipped: {idempotency_key}')
            cached_result = existing_log.response_payload or {'status': 'ok', 'message': 'Already processed.'}
            return jsonify({'result': cached_result}), 200

    # Resolve the agent
    agent = Agent.query.filter_by(retell_agent_id=retell_agent_id).first()
    if not agent:
        logger.warning(f'Function call for unknown agent: {retell_agent_id}')
        return jsonify({'result': {'status': 'error', 'message': 'Agent not found.'}}), 200

    # Find the matching tool assignment
    from app.models.core import AgentToolAssignment
    assignment = AgentToolAssignment.query.filter_by(
        agent_id=agent.id,
        function_name=function_name,
    ).first()

    if not assignment:
        logger.warning(f'No tool assignment for {function_name} on agent {agent.id}')
        return jsonify({'result': {'status': 'error', 'message': f'Tool {function_name} not configured.'}}), 200

    # Resolve the call log if available
    call_log = CallLog.query.filter_by(retell_call_id=call_id).first() if call_id else None

    # Build call context from arguments + call metadata
    call_context = {
        **arguments,
        'call_log_id': call_log.id if call_log else None,
        'retell_call_id': call_id,
        'from_number': call_log.from_number if call_log else '',
        'to_number': call_log.to_number if call_log else '',
    }

    # Execute the tool synchronously
    from app.services.tool_engine import execute_tool
    result = execute_tool(assignment, call_context, idempotency_key=idempotency_key)

    # Return result to Retell LLM
    return jsonify({'result': result}), 200
