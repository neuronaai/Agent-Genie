"""Billing background tasks — usage rollover, reconciliation, warning checks.

These functions are designed to be called by a scheduler (cron, Celery beat, etc.).
Each function creates its own app context when needed.
"""
import logging
from datetime import datetime, timezone, timedelta, date

logger = logging.getLogger(__name__)


def monthly_usage_rollover():
    """Reset usage summaries at the start of each billing period.
    Creates new UsageSummary records for tenants whose period has rolled over.
    """
    from app import create_app, db
    from app.models.core import Subscription, UsageSummary

    app = create_app()
    with app.app_context():
        now = datetime.now(timezone.utc)
        # Find subscriptions whose period has ended
        expired_subs = db.session.query(Subscription).filter(
            Subscription.status == 'active',
            Subscription.current_period_end <= now,
        ).all()

        rolled = 0
        for sub in expired_subs:
            old_end = sub.current_period_end
            new_start = old_end
            new_end = new_start + timedelta(days=30)

            sub.current_period_start = new_start
            sub.current_period_end = new_end

            # Create new summary for the new period
            new_summary = UsageSummary(
                tenant_id=sub.tenant_id,
                billing_period_start=new_start.date() if isinstance(new_start, datetime) else new_start,
                billing_period_end=new_end.date() if isinstance(new_end, datetime) else new_end,
            )
            db.session.add(new_summary)
            rolled += 1

        db.session.commit()
        logger.info(f"Monthly usage rollover: {rolled} subscriptions rolled over")
        return rolled


def check_usage_warnings():
    """Check all active tenants for usage threshold warnings.
    Sends notifications when 75%, 90%, or 100% of included minutes are used.
    """
    from app import create_app, db
    from app.models.core import Subscription
    from app.services.billing_engine import get_usage_status, create_usage_warning

    app = create_app()
    with app.app_context():
        active_subs = db.session.query(Subscription).filter_by(status='active').all()
        warned = 0
        for sub in active_subs:
            usage = get_usage_status(sub.tenant_id)
            if usage['warning_level']:
                create_usage_warning(sub.tenant_id, usage['usage_pct'])
                warned += 1

        logger.info(f"Usage warning check: {warned} warnings sent")
        return warned


def reconcile_usage_records():
    """Reconcile usage records where provider-reported differs from internal.
    Flags records for admin review.
    """
    from app import create_app, db
    from app.models.core import UsageRecord

    app = create_app()
    with app.app_context():
        # Find records with significant discrepancy
        records = db.session.query(UsageRecord).filter(
            UsageRecord.reconciliation_status == 'matched',
        ).all()

        adjusted = 0
        for record in records:
            diff = abs(record.provider_reported_seconds - record.internally_billable_seconds)
            if diff > 10:  # More than 10 seconds difference
                record.reconciliation_status = 'adjusted'
                record.adjustment_reason = (
                    f'Auto-flagged: {diff}s discrepancy between provider '
                    f'({record.provider_reported_seconds}s) and internal ({record.internally_billable_seconds}s)'
                )
                adjusted += 1

        db.session.commit()
        logger.info(f"Usage reconciliation: {adjusted} records flagged")
        return adjusted


def sync_stripe_invoices():
    """Sync recent invoices from Stripe for all tenants with Stripe customer IDs.
    This is a safety net — primary sync happens via webhooks.
    """
    from app import create_app, db
    from app.models.core import Subscription, Invoice
    from app.services import stripe_adapter

    app = create_app()
    with app.app_context():
        subs = db.session.query(Subscription).filter(
            Subscription.stripe_customer_id.isnot(None),
        ).all()

        synced = 0
        for sub in subs:
            result = stripe_adapter.list_invoices(sub.stripe_customer_id, limit=5)
            if result['status'] != 'success':
                continue

            for inv_data in result['data']:
                existing = db.session.query(Invoice).filter_by(
                    stripe_invoice_id=inv_data['id']
                ).first()
                if not existing:
                    invoice = Invoice(
                        tenant_id=sub.tenant_id,
                        stripe_invoice_id=inv_data['id'],
                        amount_due_cents=inv_data.get('amount_due', 0),
                        amount_paid_cents=inv_data.get('amount_paid', 0),
                        status=inv_data.get('status', 'draft'),
                        invoice_pdf_url=inv_data.get('invoice_pdf'),
                    )
                    db.session.add(invoice)
                    synced += 1

        db.session.commit()
        logger.info(f"Stripe invoice sync: {synced} new invoices synced")
        return synced
