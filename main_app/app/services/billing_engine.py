"""Billing Engine — plan enforcement, usage metering, reconciliation, and notifications.

All billing logic is centralized here. Routes and webhook handlers call into this module.
"""
import logging
from datetime import datetime, timezone, date, timedelta

from app import db
from app.services.tenant.scoping import get_current_tenant_id

logger = logging.getLogger(__name__)


# =========================================================================
# Plan Enforcement
# =========================================================================

def check_agent_limit(tenant_id: str) -> dict:
    """Check if the tenant can create another agent.
    Returns {'allowed': bool, 'current': int, 'limit': int, 'plan_name': str}.
    """
    from app.models.core import Agent, Subscription
    sub = db.session.query(Subscription).filter_by(tenant_id=tenant_id, status='active').first()
    if not sub or not sub.plan:
        return {'allowed': False, 'current': 0, 'limit': 0, 'plan_name': 'None'}

    active_agents = db.session.query(Agent).filter(
        Agent.tenant_id == tenant_id,
        Agent.status.in_(['active', 'pending', 'draft']),
    ).count()

    limit = sub.plan.included_agents
    return {
        'allowed': active_agents < limit,
        'current': active_agents,
        'limit': limit,
        'plan_name': sub.plan.name,
    }


def check_number_limit(tenant_id: str) -> dict:
    """Check if the tenant can purchase another phone number.
    Returns {'allowed': bool, 'current': int, 'included': int, 'extra_cost_cents': int}.
    """
    from app.models.core import PhoneNumber, Subscription
    sub = db.session.query(Subscription).filter_by(tenant_id=tenant_id, status='active').first()
    if not sub or not sub.plan:
        return {'allowed': False, 'current': 0, 'included': 0, 'extra_cost_cents': 0}

    active_numbers = db.session.query(PhoneNumber).filter(
        PhoneNumber.tenant_id == tenant_id,
        PhoneNumber.status.in_(['active', 'unassigned']),
    ).count()

    included = sub.plan.included_numbers
    extra_cost = sub.plan.additional_number_rate_cents if active_numbers >= included else 0

    return {
        'allowed': True,  # Always allowed, but may incur extra charges
        'current': active_numbers,
        'included': included,
        'extra_cost_cents': extra_cost,
        'plan_name': sub.plan.name,
    }


def get_usage_status(tenant_id: str) -> dict:
    """Get current usage status for the billing period.
    Returns minutes used, included, topup balance, overage, and warning thresholds.
    """
    from app.models.core import CallLog, Subscription, MinuteTopupPurchase, UsageSummary

    sub = db.session.query(Subscription).filter_by(tenant_id=tenant_id, status='active').first()
    if not sub or not sub.plan:
        return {
            'has_subscription': False,
            'included_minutes': 0,
            'used_minutes': 0,
            'topup_minutes_remaining': 0,
            'overage_minutes': 0,
            'overage_rate_cents': 0,
            'usage_pct': 0,
            'warning_level': None,
        }

    plan = sub.plan
    period_start = sub.current_period_start or datetime.now(timezone.utc).replace(day=1)
    period_end = sub.current_period_end or (period_start + timedelta(days=30))

    # Calculate total minutes used in current period
    total_seconds = db.session.query(
        db.func.coalesce(db.func.sum(CallLog.duration_seconds), 0)
    ).filter(
        CallLog.tenant_id == tenant_id,
        CallLog.created_at >= period_start,
        CallLog.created_at <= period_end,
    ).scalar()
    used_minutes = (total_seconds or 0) // 60

    # Get remaining top-up minutes
    topup_remaining = db.session.query(
        db.func.coalesce(db.func.sum(MinuteTopupPurchase.minutes_remaining), 0)
    ).filter(
        MinuteTopupPurchase.tenant_id == tenant_id,
        MinuteTopupPurchase.minutes_remaining > 0,
    ).scalar() or 0

    # Calculate overage
    included = plan.included_minutes
    if used_minutes <= included:
        overage = 0
    elif used_minutes <= included + topup_remaining:
        overage = 0
    else:
        overage = used_minutes - included - topup_remaining

    usage_pct = round((used_minutes / included * 100) if included > 0 else 0, 1)

    # Determine warning level
    warning_level = None
    if usage_pct >= 100:
        warning_level = 'exhausted'
    elif usage_pct >= 90:
        warning_level = 'critical'
    elif usage_pct >= 75:
        warning_level = 'warning'

    return {
        'has_subscription': True,
        'plan_name': plan.name,
        'included_minutes': included,
        'used_minutes': used_minutes,
        'remaining_included': max(0, included - used_minutes),
        'topup_minutes_remaining': topup_remaining,
        'overage_minutes': overage,
        'overage_rate_cents': plan.overage_rate_cents,
        'overage_cost_cents': overage * plan.overage_rate_cents,
        'usage_pct': min(usage_pct, 100),
        'warning_level': warning_level,
        'period_start': period_start,
        'period_end': period_end,
    }


def get_billing_summary(tenant_id: str) -> dict:
    """Get a complete billing summary for the dashboard."""
    from app.models.core import (
        Subscription, Invoice, PhoneNumber, Agent,
        MinuteTopupPurchase, Payment,
    )

    sub = db.session.query(Subscription).filter_by(tenant_id=tenant_id).first()
    usage = get_usage_status(tenant_id)

    # Recent invoices
    invoices = db.session.query(Invoice).filter_by(
        tenant_id=tenant_id
    ).order_by(Invoice.created_at.desc()).limit(10).all()

    # Recent payments
    payments = db.session.query(Payment).filter_by(
        tenant_id=tenant_id
    ).order_by(Payment.created_at.desc()).limit(10).all()

    # Active numbers count
    active_numbers = db.session.query(PhoneNumber).filter(
        PhoneNumber.tenant_id == tenant_id,
        PhoneNumber.status.in_(['active', 'unassigned']),
    ).count()

    # Active agents count
    active_agents = db.session.query(Agent).filter(
        Agent.tenant_id == tenant_id,
        Agent.status == 'active',
    ).count()

    # Top-up balance
    topup_balance = db.session.query(
        db.func.coalesce(db.func.sum(MinuteTopupPurchase.minutes_remaining), 0)
    ).filter(
        MinuteTopupPurchase.tenant_id == tenant_id,
        MinuteTopupPurchase.minutes_remaining > 0,
    ).scalar() or 0

    # Extra numbers cost
    extra_numbers = 0
    extra_number_cost = 0
    if sub and sub.plan:
        extra_numbers = max(0, active_numbers - sub.plan.included_numbers)
        extra_number_cost = extra_numbers * sub.plan.additional_number_rate_cents

    return {
        'subscription': sub,
        'usage': usage,
        'invoices': invoices,
        'payments': payments,
        'active_numbers': active_numbers,
        'active_agents': active_agents,
        'topup_balance': topup_balance,
        'extra_numbers': extra_numbers,
        'extra_number_cost_cents': extra_number_cost,
    }


# =========================================================================
# Usage Recording
# =========================================================================

def record_call_usage(call_log_id: str, tenant_id: str, duration_seconds: int,
                      provider_reported_seconds: int = None) -> dict:
    """Record usage for a completed call. Creates a UsageRecord and updates UsageSummary."""
    from app.models.core import UsageRecord, UsageSummary, Subscription, MinuteTopupPurchase

    # Check if already recorded
    existing = db.session.query(UsageRecord).filter_by(call_log_id=call_log_id).first()
    if existing:
        return {'status': 'already_recorded', 'usage_record_id': existing.id}

    provider_seconds = provider_reported_seconds or duration_seconds
    billable_seconds = duration_seconds  # Could apply rounding rules here

    # Determine reconciliation status
    recon_status = 'matched'
    adjustment_reason = None
    if abs(provider_seconds - billable_seconds) > 5:
        recon_status = 'adjusted'
        adjustment_reason = f'Provider reported {provider_seconds}s vs internal {billable_seconds}s'

    record = UsageRecord(
        tenant_id=tenant_id,
        call_log_id=call_log_id,
        provider_reported_seconds=provider_seconds,
        internally_billable_seconds=billable_seconds,
        reconciliation_status=recon_status,
        adjustment_reason=adjustment_reason,
    )
    db.session.add(record)

    # Update or create usage summary for current period
    sub = db.session.query(Subscription).filter_by(tenant_id=tenant_id, status='active').first()
    if sub and sub.current_period_start:
        period_start = sub.current_period_start.date() if isinstance(sub.current_period_start, datetime) else sub.current_period_start
        period_end = sub.current_period_end.date() if isinstance(sub.current_period_end, datetime) else sub.current_period_end

        summary = db.session.query(UsageSummary).filter_by(
            tenant_id=tenant_id,
            billing_period_start=period_start,
        ).first()

        if not summary:
            summary = UsageSummary(
                tenant_id=tenant_id,
                billing_period_start=period_start,
                billing_period_end=period_end or (period_start + timedelta(days=30)),
            )
            db.session.add(summary)

        # Allocate minutes: included -> topup -> overage
        call_minutes = max(1, (billable_seconds + 59) // 60)  # Round up
        included_remaining = max(0, sub.plan.included_minutes - summary.total_included_minutes_used)

        if call_minutes <= included_remaining:
            summary.total_included_minutes_used += call_minutes
        else:
            # Use remaining included
            if included_remaining > 0:
                summary.total_included_minutes_used += included_remaining
                call_minutes -= included_remaining

            # Try top-up minutes
            topups = db.session.query(MinuteTopupPurchase).filter(
                MinuteTopupPurchase.tenant_id == tenant_id,
                MinuteTopupPurchase.minutes_remaining > 0,
            ).order_by(MinuteTopupPurchase.created_at).all()

            for topup in topups:
                if call_minutes <= 0:
                    break
                deduct = min(call_minutes, topup.minutes_remaining)
                topup.minutes_remaining -= deduct
                summary.total_topup_minutes_used += deduct
                call_minutes -= deduct

            # Remaining goes to overage
            if call_minutes > 0:
                summary.total_overage_minutes += call_minutes

    db.session.commit()
    return {'status': 'success', 'usage_record_id': record.id}


# =========================================================================
# Top-Up Purchase
# =========================================================================

def process_topup_purchase(tenant_id: str, pack_id: str, payment_id: str = None) -> dict:
    """Record a top-up purchase and add minutes to the tenant's balance."""
    from app.models.core import TopupPackDefinition, MinuteTopupPurchase

    pack = db.session.get(TopupPackDefinition, pack_id)
    if not pack or not pack.is_active:
        return {'status': 'error', 'message': 'Top-up pack not found or inactive'}

    purchase = MinuteTopupPurchase(
        tenant_id=tenant_id,
        payment_id=payment_id,
        minutes_added=pack.minutes,
        minutes_remaining=pack.minutes,
        purchase_price_cents=pack.price_cents,
    )
    db.session.add(purchase)
    db.session.commit()

    return {
        'status': 'success',
        'minutes_added': pack.minutes,
        'purchase_id': purchase.id,
    }


# =========================================================================
# Admin Adjustments
# =========================================================================

def admin_credit_minutes(tenant_id: str, minutes: int, reason: str, admin_user_id: str) -> dict:
    """Admin grants free minutes to a tenant (creates a topup with no payment)."""
    from app.models.core import MinuteTopupPurchase, AuditLog

    purchase = MinuteTopupPurchase(
        tenant_id=tenant_id,
        payment_id=None,
        minutes_added=minutes,
        minutes_remaining=minutes,
        purchase_price_cents=0,
    )
    db.session.add(purchase)

    # Audit trail
    audit = AuditLog(
        tenant_id=tenant_id,
        user_id=admin_user_id,
        action='admin_credit_minutes',
        resource_type='MinuteTopupPurchase',
        details={'minutes': minutes, 'reason': reason},
    )
    db.session.add(audit)
    db.session.commit()

    return {'status': 'success', 'minutes_credited': minutes}


def admin_adjust_usage(usage_record_id: str, new_billable_seconds: int,
                       reason: str, admin_user_id: str) -> dict:
    """Admin adjusts a usage record's billable seconds."""
    from app.models.core import UsageRecord, AuditLog

    record = db.session.get(UsageRecord, usage_record_id)
    if not record:
        return {'status': 'error', 'message': 'Usage record not found'}

    old_seconds = record.internally_billable_seconds
    record.internally_billable_seconds = new_billable_seconds
    record.reconciliation_status = 'adjusted'
    record.adjustment_reason = reason

    audit = AuditLog(
        tenant_id=record.tenant_id,
        user_id=admin_user_id,
        action='admin_adjust_usage',
        resource_type='UsageRecord',
        resource_id=record.id,
        details={
            'old_billable_seconds': old_seconds,
            'new_billable_seconds': new_billable_seconds,
            'reason': reason,
        },
    )
    db.session.add(audit)
    db.session.commit()

    return {'status': 'success'}


# =========================================================================
# Webhook Processing
# =========================================================================

def process_stripe_webhook(event_data: dict) -> dict:
    """Process a Stripe webhook event. Called from the API webhook handler."""
    from app.models.core import (
        Subscription, Invoice, Payment, Notification, Tenant,
    )

    event_type = event_data.get('type', '')
    obj = event_data.get('data', {}).get('object', {})

    try:
        if event_type == 'checkout.session.completed':
            _handle_checkout_completed(obj)
        elif event_type == 'customer.subscription.updated':
            _handle_subscription_updated(obj)
        elif event_type == 'customer.subscription.deleted':
            _handle_subscription_deleted(obj)
        elif event_type == 'invoice.paid':
            _handle_invoice_paid(obj)
        elif event_type == 'invoice.payment_failed':
            _handle_invoice_payment_failed(obj)
        elif event_type == 'payment_intent.succeeded':
            _handle_payment_succeeded(obj)
        elif event_type == 'payment_intent.payment_failed':
            _handle_payment_failed(obj)
        else:
            logger.info(f"Unhandled Stripe event: {event_type}")

        return {'status': 'success'}
    except Exception as e:
        logger.error(f"Error processing Stripe event {event_type}: {e}")
        return {'status': 'error', 'message': str(e)}


def _handle_checkout_completed(session_obj):
    """Handle checkout.session.completed — activate subscription or fulfill DFY purchase.

    Idempotent: checks current state before applying changes.
    """
    metadata = session_obj.get('metadata', {})

    # DFY package purchase fulfillment
    if metadata.get('type') == 'dfy_purchase':
        _fulfill_dfy_checkout(session_obj, metadata)
        return

    # Subscription activation
    from app.models.core import Subscription
    stripe_sub_id = session_obj.get('subscription')
    stripe_customer_id = session_obj.get('customer')
    if not stripe_sub_id:
        return

    sub = db.session.query(Subscription).filter_by(
        stripe_customer_id=stripe_customer_id
    ).first()
    if sub:
        sub.stripe_subscription_id = stripe_sub_id
        sub.status = 'active'
        db.session.commit()


def _fulfill_dfy_checkout(session_obj, metadata):
    """Fulfill a DFY package purchase after successful Stripe Checkout.

    Idempotent: only transitions from ``pending_payment`` to ``intake``.
    If the project is already in ``intake`` or later, this is a no-op.
    """
    from app.models.core import DfyProject, DfyMessage

    project_id = metadata.get('dfy_project_id')
    if not project_id:
        logger.warning('DFY checkout completed but no project_id in metadata')
        return

    project = db.session.get(DfyProject, project_id)
    if not project:
        logger.warning(f'DFY checkout: project {project_id} not found')
        return

    # Idempotency: only update if still pending
    if project.status != 'pending_payment':
        logger.info(f'DFY checkout idempotent skip: project {project_id} already in {project.status}')
        return

    checkout_session_id = session_obj.get('id', '')
    project.status = 'intake'
    project.invoice_id = checkout_session_id

    # Add an automated message to the project thread
    system_message = DfyMessage(
        project_id=project.id,
        sender_id=project.owner_id or project.tenant_id,  # system sender
        content=(
            f'Payment confirmed (Stripe session: {checkout_session_id[:20]}...). '
            f'Your project is now in the intake phase. Our team will begin work shortly.'
        ),
        is_admin_note=False,
    )
    db.session.add(system_message)
    db.session.commit()
    logger.info(f'DFY project {project_id} fulfilled via Stripe checkout {checkout_session_id}')


def _handle_subscription_updated(sub_obj):
    """Handle customer.subscription.updated."""
    from app.models.core import Subscription
    stripe_sub_id = sub_obj.get('id')
    sub = db.session.query(Subscription).filter_by(
        stripe_subscription_id=stripe_sub_id
    ).first()
    if not sub:
        return

    sub.status = sub_obj.get('status', sub.status)
    sub.cancel_at_period_end = sub_obj.get('cancel_at_period_end', False)

    period_start = sub_obj.get('current_period_start')
    period_end = sub_obj.get('current_period_end')
    if period_start:
        sub.current_period_start = datetime.fromtimestamp(period_start, tz=timezone.utc)
    if period_end:
        sub.current_period_end = datetime.fromtimestamp(period_end, tz=timezone.utc)

    # Update payment method info if available
    default_pm = sub_obj.get('default_payment_method')
    if isinstance(default_pm, dict):
        card = default_pm.get('card', {})
        sub.payment_method_last4 = card.get('last4')
        sub.payment_method_brand = card.get('brand')

    db.session.commit()


def _handle_subscription_deleted(sub_obj):
    """Handle customer.subscription.deleted."""
    from app.models.core import Subscription
    stripe_sub_id = sub_obj.get('id')
    sub = db.session.query(Subscription).filter_by(
        stripe_subscription_id=stripe_sub_id
    ).first()
    if sub:
        sub.status = 'canceled'
        db.session.commit()


def _handle_invoice_paid(inv_obj):
    """Handle invoice.paid — sync invoice to local DB."""
    from app.models.core import Invoice, Subscription
    stripe_inv_id = inv_obj.get('id')
    stripe_customer_id = inv_obj.get('customer')

    # Find tenant via subscription
    sub = db.session.query(Subscription).filter_by(
        stripe_customer_id=stripe_customer_id
    ).first()
    if not sub:
        return

    # Upsert invoice
    invoice = db.session.query(Invoice).filter_by(stripe_invoice_id=stripe_inv_id).first()
    if not invoice:
        invoice = Invoice(
            tenant_id=sub.tenant_id,
            stripe_invoice_id=stripe_inv_id,
        )
        db.session.add(invoice)

    invoice.amount_due_cents = inv_obj.get('amount_due', 0)
    invoice.amount_paid_cents = inv_obj.get('amount_paid', 0)
    invoice.status = 'paid'
    invoice.invoice_pdf_url = inv_obj.get('invoice_pdf')
    db.session.commit()

    # Create notification
    _create_notification(sub.tenant_id, 'Payment Received',
                         f'Your payment of ${invoice.amount_paid_cents / 100:.2f} has been received.')


def _handle_invoice_payment_failed(inv_obj):
    """Handle invoice.payment_failed."""
    from app.models.core import Invoice, Subscription
    stripe_inv_id = inv_obj.get('id')
    stripe_customer_id = inv_obj.get('customer')

    sub = db.session.query(Subscription).filter_by(
        stripe_customer_id=stripe_customer_id
    ).first()
    if not sub:
        return

    invoice = db.session.query(Invoice).filter_by(stripe_invoice_id=stripe_inv_id).first()
    if not invoice:
        invoice = Invoice(
            tenant_id=sub.tenant_id,
            stripe_invoice_id=stripe_inv_id,
        )
        db.session.add(invoice)

    invoice.amount_due_cents = inv_obj.get('amount_due', 0)
    invoice.status = 'open'
    db.session.commit()

    sub.status = 'past_due'
    db.session.commit()

    _create_notification(sub.tenant_id, 'Payment Failed',
                         f'Your payment of ${invoice.amount_due_cents / 100:.2f} failed. '
                         f'Please update your payment method to avoid service interruption.')


def _handle_payment_succeeded(pi_obj):
    """Handle payment_intent.succeeded."""
    from app.models.core import Payment, Subscription
    stripe_pi_id = pi_obj.get('id')
    stripe_customer_id = pi_obj.get('customer')

    sub = db.session.query(Subscription).filter_by(
        stripe_customer_id=stripe_customer_id
    ).first()
    if not sub:
        return

    existing = db.session.query(Payment).filter_by(stripe_payment_intent_id=stripe_pi_id).first()
    if existing:
        return

    payment = Payment(
        tenant_id=sub.tenant_id,
        stripe_payment_intent_id=stripe_pi_id,
        amount_cents=pi_obj.get('amount', 0),
        status='succeeded',
    )
    db.session.add(payment)
    db.session.commit()


def _handle_payment_failed(pi_obj):
    """Handle payment_intent.payment_failed."""
    from app.models.core import Payment, Subscription
    stripe_pi_id = pi_obj.get('id')
    stripe_customer_id = pi_obj.get('customer')

    sub = db.session.query(Subscription).filter_by(
        stripe_customer_id=stripe_customer_id
    ).first()
    if not sub:
        return

    existing = db.session.query(Payment).filter_by(stripe_payment_intent_id=stripe_pi_id).first()
    if not existing:
        payment = Payment(
            tenant_id=sub.tenant_id,
            stripe_payment_intent_id=stripe_pi_id,
            amount_cents=pi_obj.get('amount', 0),
            status='failed',
        )
        db.session.add(payment)
    else:
        existing.status = 'failed'

    db.session.commit()


# =========================================================================
# Notifications
# =========================================================================

def _create_notification(tenant_id: str, subject: str, body: str):
    """Create an in-app notification for a tenant."""
    from app.models.core import Notification
    notif = Notification(
        tenant_id=tenant_id,
        type='in_app',
        subject=subject,
        body=body,
        title=subject,
        message=body,
        is_read=False,
        status='sent',
    )
    db.session.add(notif)
    db.session.commit()


def create_usage_warning(tenant_id: str, usage_pct: float):
    """Create a usage warning notification if threshold is crossed."""
    if usage_pct >= 100:
        _create_notification(tenant_id, 'Minutes Exhausted',
                             'You have used all your included minutes. Additional calls will be billed at overage rates. '
                             'Consider purchasing a top-up pack or upgrading your plan.')
    elif usage_pct >= 90:
        _create_notification(tenant_id, 'Usage Alert: 90% of Minutes Used',
                             f'You have used {usage_pct:.0f}% of your included minutes this billing period. '
                             f'Consider purchasing a top-up pack to avoid overage charges.')
    elif usage_pct >= 75:
        _create_notification(tenant_id, 'Usage Notice: 75% of Minutes Used',
                             f'You have used {usage_pct:.0f}% of your included minutes this billing period.')


def get_notifications(tenant_id: str, limit: int = 20) -> list:
    """Get recent notifications for a tenant."""
    from app.models.core import Notification
    return db.session.query(Notification).filter_by(
        tenant_id=tenant_id
    ).order_by(Notification.created_at.desc()).limit(limit).all()
