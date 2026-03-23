"""
Celery Tasks for Webhook Processing.

Stores raw payloads, enforces idempotency via event keys, and processes
Retell and Stripe webhook events asynchronously.
"""
import json
import logging
from datetime import datetime, timezone

from celery import shared_task

logger = logging.getLogger(__name__)


def _get_db():
    from app import db
    return db


# ---------------------------------------------------------------------------
# Retell Webhook Processing
# ---------------------------------------------------------------------------
@shared_task(
    bind=True,
    name='tasks.process_retell_webhook',
    max_retries=2,
    default_retry_delay=10,
    acks_late=True,
)
def process_retell_webhook(self, event_type: str, payload: dict, idempotency_key: str = None) -> dict:
    """Process a Retell webhook event idempotently.

    The webhook endpoint stores the raw payload and enqueues this task.
    """
    from app.models.core import WebhookEvent

    db = _get_db()

    try:
        # Idempotency check
        if idempotency_key:
            existing = db.session.query(WebhookEvent).filter_by(
                idempotency_key=idempotency_key
            ).first()
            if existing and existing.status == 'processed':
                logger.info(f"Webhook {idempotency_key} already processed — skipping")
                return {"status": "skipped", "reason": "duplicate"}

        # Store raw payload
        log = WebhookEvent(
            provider='retell',
            event_type=event_type,
            payload=payload,
            idempotency_key=idempotency_key,
            status='processing',
        )
        db.session.add(log)
        db.session.commit()

        # Process based on event type
        if event_type == 'call_ended':
            _handle_call_ended(payload)
        elif event_type == 'call_analyzed':
            _handle_call_analyzed(payload)
        elif event_type == 'agent_updated':
            _handle_agent_updated(payload)
        else:
            logger.info(f"Unhandled Retell event type: {event_type}")

        log.status = 'processed'
        log.processed_at = datetime.now(timezone.utc)
        db.session.commit()

        return {"status": "processed", "event_type": event_type}

    except Exception as e:
        logger.exception(f"Error processing Retell webhook: {e}")
        try:
            if log:
                log.status = 'failed'
                log.error_message = str(e)[:500]
                db.session.commit()
        except Exception:
            db.session.rollback()
        return {"status": "error", "message": str(e)[:500]}


def _handle_call_ended(payload: dict):
    """Handle a call_ended event — update usage records."""
    from app.models.core import Agent, UsageRecord
    db = _get_db()

    call_id = payload.get('call_id')
    agent_id_retell = payload.get('agent_id')
    duration_seconds = payload.get('duration_seconds', 0)
    duration_minutes = round(duration_seconds / 60, 2)

    agent = db.session.query(Agent).filter_by(retell_agent_id=agent_id_retell).first()
    if not agent:
        logger.warning(f"No agent found for retell_agent_id {agent_id_retell}")
        return

    # Create usage record
    usage = UsageRecord(
        tenant_id=agent.tenant_id,
        agent_id=agent.id,
        call_id=call_id,
        duration_minutes=duration_minutes,
        provider_reported_minutes=duration_minutes,
        direction=payload.get('direction', 'inbound'),
        recorded_at=datetime.now(timezone.utc),
    )
    db.session.add(usage)
    db.session.commit()
    logger.info(f"Usage recorded: {duration_minutes} min for agent {agent.name}")


def _handle_call_analyzed(payload: dict):
    """Handle a call_analyzed event — store analysis data."""
    logger.info(f"Call analyzed event received: {payload.get('call_id')}")


def _handle_agent_updated(payload: dict):
    """Handle an agent_updated event from Retell."""
    logger.info(f"Agent updated event received: {payload.get('agent_id')}")


# ---------------------------------------------------------------------------
# Stripe Webhook Processing
# ---------------------------------------------------------------------------
@shared_task(
    bind=True,
    name='tasks.process_stripe_webhook',
    max_retries=2,
    default_retry_delay=10,
    acks_late=True,
)
def process_stripe_webhook(self, event_type: str, payload: dict, idempotency_key: str = None) -> dict:
    """Process a Stripe webhook event idempotently."""
    from app.models.core import WebhookEvent

    db = _get_db()

    try:
        # Idempotency check
        if idempotency_key:
            existing = db.session.query(WebhookEvent).filter_by(
                idempotency_key=idempotency_key
            ).first()
            if existing and existing.status == 'processed':
                logger.info(f"Stripe webhook {idempotency_key} already processed — skipping")
                return {"status": "skipped", "reason": "duplicate"}

        log = WebhookEvent(
            provider='stripe',
            event_type=event_type,
            payload=payload,
            idempotency_key=idempotency_key,
            status='processing',
        )
        db.session.add(log)
        db.session.commit()

        # Process based on event type
        if event_type == 'checkout.session.completed':
            _handle_checkout_completed(payload)
        elif event_type == 'invoice.payment_succeeded':
            _handle_payment_succeeded(payload)
        elif event_type == 'invoice.payment_failed':
            _handle_payment_failed(payload)
        elif event_type == 'customer.subscription.updated':
            _handle_subscription_updated(payload)
        elif event_type == 'customer.subscription.deleted':
            _handle_subscription_deleted(payload)
        else:
            logger.info(f"Unhandled Stripe event type: {event_type}")

        log.status = 'processed'
        log.processed_at = datetime.now(timezone.utc)
        db.session.commit()

        return {"status": "processed", "event_type": event_type}

    except Exception as e:
        logger.exception(f"Error processing Stripe webhook: {e}")
        return {"status": "error", "message": str(e)[:500]}


def _handle_checkout_completed(payload: dict):
    logger.info(f"Checkout completed: {payload.get('id')}")


def _handle_payment_succeeded(payload: dict):
    logger.info(f"Payment succeeded: {payload.get('id')}")


def _handle_payment_failed(payload: dict):
    logger.info(f"Payment failed: {payload.get('id')}")


def _handle_subscription_updated(payload: dict):
    logger.info(f"Subscription updated: {payload.get('id')}")


def _handle_subscription_deleted(payload: dict):
    logger.info(f"Subscription deleted: {payload.get('id')}")
