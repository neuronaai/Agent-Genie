"""Stripe Billing Adapter — provider service for all Stripe API interactions.

All Stripe calls go through this module. If Stripe is not configured (no API key),
methods return mock/stub responses so the UI can be tested without a live Stripe account.
"""
import logging
from datetime import datetime, timezone

from flask import current_app

logger = logging.getLogger(__name__)

_stripe = None


def _get_stripe():
    """Lazy-load stripe module and configure API key."""
    global _stripe
    if _stripe is None:
        try:
            import stripe
            _stripe = stripe
        except ImportError:
            logger.warning("stripe package not installed — using mock mode")
            return None
    key = current_app.config.get('STRIPE_SECRET_KEY', '')
    if key:
        _stripe.api_key = key
        return _stripe
    return None


def _is_live():
    """Return True if Stripe is configured with a real API key."""
    return bool(current_app.config.get('STRIPE_SECRET_KEY', ''))


# =========================================================================
# Customer Management
# =========================================================================

def create_customer(email: str, name: str = '', metadata: dict = None) -> dict:
    """Create a Stripe customer and return {'status': 'success', 'data': {...}}."""
    stripe = _get_stripe()
    if not stripe:
        mock_id = f'cus_mock_{email.split("@")[0]}'
        return {'status': 'success', 'data': {'id': mock_id, 'email': email}}
    try:
        customer = stripe.Customer.create(
            email=email,
            name=name or email,
            metadata=metadata or {},
        )
        return {'status': 'success', 'data': {'id': customer.id, 'email': customer.email}}
    except Exception as e:
        logger.error(f"Stripe create_customer error: {e}")
        return {'status': 'error', 'message': str(e)}


def get_customer(customer_id: str) -> dict:
    """Retrieve a Stripe customer."""
    stripe = _get_stripe()
    if not stripe:
        return {'status': 'success', 'data': {'id': customer_id}}
    try:
        customer = stripe.Customer.retrieve(customer_id)
        return {'status': 'success', 'data': {
            'id': customer.id,
            'email': customer.email,
            'name': customer.name,
        }}
    except Exception as e:
        logger.error(f"Stripe get_customer error: {e}")
        return {'status': 'error', 'message': str(e)}


# =========================================================================
# Subscription Management
# =========================================================================

def create_checkout_session(
    customer_id: str,
    price_id: str,
    success_url: str,
    cancel_url: str,
    metadata: dict = None,
) -> dict:
    """Create a Stripe Checkout Session for subscription signup."""
    stripe = _get_stripe()
    if not stripe:
        return {
            'status': 'success',
            'data': {
                'id': 'cs_mock_session',
                'url': success_url + '?session_id=cs_mock_session',
            },
        }
    try:
        session = stripe.checkout.Session.create(
            customer=customer_id,
            payment_method_types=['card'],
            line_items=[{'price': price_id, 'quantity': 1}],
            mode='subscription',
            success_url=success_url + '?session_id={CHECKOUT_SESSION_ID}',
            cancel_url=cancel_url,
            metadata=metadata or {},
        )
        return {'status': 'success', 'data': {'id': session.id, 'url': session.url}}
    except Exception as e:
        logger.error(f"Stripe create_checkout_session error: {e}")
        return {'status': 'error', 'message': str(e)}


def create_billing_portal_session(customer_id: str, return_url: str) -> dict:
    """Create a Stripe Customer Portal session for self-service billing management."""
    stripe = _get_stripe()
    if not stripe:
        return {'status': 'success', 'data': {'url': return_url}}
    try:
        session = stripe.billing_portal.Session.create(
            customer=customer_id,
            return_url=return_url,
        )
        return {'status': 'success', 'data': {'url': session.url}}
    except Exception as e:
        logger.error(f"Stripe create_billing_portal_session error: {e}")
        return {'status': 'error', 'message': str(e)}


def get_subscription(subscription_id: str) -> dict:
    """Retrieve a Stripe subscription."""
    stripe = _get_stripe()
    if not stripe:
        return {'status': 'success', 'data': {'id': subscription_id, 'status': 'active'}}
    try:
        sub = stripe.Subscription.retrieve(subscription_id)
        return {'status': 'success', 'data': {
            'id': sub.id,
            'status': sub.status,
            'current_period_start': sub.current_period_start,
            'current_period_end': sub.current_period_end,
            'cancel_at_period_end': sub.cancel_at_period_end,
            'plan_id': sub['items']['data'][0]['price']['id'] if sub['items']['data'] else None,
        }}
    except Exception as e:
        logger.error(f"Stripe get_subscription error: {e}")
        return {'status': 'error', 'message': str(e)}


def update_subscription(subscription_id: str, new_price_id: str) -> dict:
    """Update a subscription to a new plan (upgrade/downgrade)."""
    stripe = _get_stripe()
    if not stripe:
        return {'status': 'success', 'data': {'id': subscription_id}}
    try:
        sub = stripe.Subscription.retrieve(subscription_id)
        updated = stripe.Subscription.modify(
            subscription_id,
            items=[{
                'id': sub['items']['data'][0].id,
                'price': new_price_id,
            }],
            proration_behavior='create_prorations',
        )
        return {'status': 'success', 'data': {'id': updated.id, 'status': updated.status}}
    except Exception as e:
        logger.error(f"Stripe update_subscription error: {e}")
        return {'status': 'error', 'message': str(e)}


def cancel_subscription(subscription_id: str, at_period_end: bool = True) -> dict:
    """Cancel a subscription (at period end by default)."""
    stripe = _get_stripe()
    if not stripe:
        return {'status': 'success', 'data': {'id': subscription_id, 'cancel_at_period_end': True}}
    try:
        if at_period_end:
            updated = stripe.Subscription.modify(
                subscription_id,
                cancel_at_period_end=True,
            )
        else:
            updated = stripe.Subscription.cancel(subscription_id)
        return {'status': 'success', 'data': {
            'id': updated.id,
            'status': updated.status,
            'cancel_at_period_end': updated.cancel_at_period_end,
        }}
    except Exception as e:
        logger.error(f"Stripe cancel_subscription error: {e}")
        return {'status': 'error', 'message': str(e)}


def reactivate_subscription(subscription_id: str) -> dict:
    """Reactivate a subscription that was set to cancel at period end."""
    stripe = _get_stripe()
    if not stripe:
        return {'status': 'success', 'data': {'id': subscription_id, 'cancel_at_period_end': False}}
    try:
        updated = stripe.Subscription.modify(
            subscription_id,
            cancel_at_period_end=False,
        )
        return {'status': 'success', 'data': {
            'id': updated.id,
            'status': updated.status,
            'cancel_at_period_end': updated.cancel_at_period_end,
        }}
    except Exception as e:
        logger.error(f"Stripe reactivate_subscription error: {e}")
        return {'status': 'error', 'message': str(e)}


# =========================================================================
# One-Time Payments (Top-ups)
# =========================================================================

def create_topup_checkout(
    customer_id: str,
    amount_cents: int,
    description: str,
    success_url: str,
    cancel_url: str,
    metadata: dict = None,
) -> dict:
    """Create a Stripe Checkout Session for a one-time top-up purchase."""
    stripe = _get_stripe()
    if not stripe:
        return {
            'status': 'success',
            'data': {
                'id': 'cs_mock_topup',
                'url': success_url + '?session_id=cs_mock_topup',
            },
        }
    try:
        session = stripe.checkout.Session.create(
            customer=customer_id,
            payment_method_types=['card'],
            line_items=[{
                'price_data': {
                    'currency': 'usd',
                    'unit_amount': amount_cents,
                    'product_data': {'name': description},
                },
                'quantity': 1,
            }],
            mode='payment',
            success_url=success_url + '?session_id={CHECKOUT_SESSION_ID}',
            cancel_url=cancel_url,
            metadata=metadata or {},
        )
        return {'status': 'success', 'data': {'id': session.id, 'url': session.url}}
    except Exception as e:
        logger.error(f"Stripe create_topup_checkout error: {e}")
        return {'status': 'error', 'message': str(e)}


# =========================================================================
# Invoice Retrieval
# =========================================================================

def list_invoices(customer_id: str, limit: int = 20) -> dict:
    """List invoices for a customer."""
    stripe = _get_stripe()
    if not stripe:
        return {'status': 'success', 'data': []}
    try:
        invoices = stripe.Invoice.list(customer=customer_id, limit=limit)
        result = []
        for inv in invoices.data:
            result.append({
                'id': inv.id,
                'amount_due': inv.amount_due,
                'amount_paid': inv.amount_paid,
                'status': inv.status,
                'created': inv.created,
                'invoice_pdf': inv.invoice_pdf,
                'hosted_invoice_url': inv.hosted_invoice_url,
            })
        return {'status': 'success', 'data': result}
    except Exception as e:
        logger.error(f"Stripe list_invoices error: {e}")
        return {'status': 'error', 'message': str(e)}


# =========================================================================
# Webhook Signature Verification
# =========================================================================

def verify_webhook_signature(payload: bytes, sig_header: str) -> dict:
    """Verify a Stripe webhook signature and return the parsed event."""
    stripe = _get_stripe()
    secret = current_app.config.get('STRIPE_WEBHOOK_SECRET', '')
    if not stripe or not secret:
        # In dev mode without Stripe, parse the JSON directly
        import json
        try:
            event = json.loads(payload)
            return {'status': 'success', 'data': event}
        except Exception:
            return {'status': 'error', 'message': 'Invalid JSON payload'}
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, secret)
        return {'status': 'success', 'data': event}
    except stripe.error.SignatureVerificationError as e:
        return {'status': 'error', 'message': f'Signature verification failed: {e}'}
    except Exception as e:
        return {'status': 'error', 'message': str(e)}
