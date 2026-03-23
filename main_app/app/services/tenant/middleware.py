"""Tenant resolution middleware.

Resolves the current user's active tenant and membership into Flask ``g``
context on every request.  The active tenant is stored in the session so it
persists across requests and is deterministic (no more "first membership
found" ambiguity).

Partner subdomain resolution is retained but gated behind the
``FEATURE_PARTNER_PROGRAM`` flag.
"""
from flask import g, request, session
from flask_login import current_user


def register_tenant_middleware(app):
    """Register the before_request hook on the Flask app."""

    @app.before_request
    def resolve_tenant():
        g.partner = None
        g.branding = None
        g.tenant_id = None
        g.membership = None

        # ── 1. Resolve partner from subdomain (only when feature enabled) ──
        if app.config.get('FEATURE_PARTNER_PROGRAM'):
            host = request.host.split(':')[0]
            platform_domain = app.config.get('PLATFORM_DOMAIN', 'localhost').split(':')[0]

            if host != platform_domain and host.endswith(f'.{platform_domain}'):
                subdomain = host.replace(f'.{platform_domain}', '')
                if subdomain and subdomain not in ('app', 'www'):
                    from app.models.core import Partner, BrandingSetting
                    from app import db
                    partner = db.session.query(Partner).filter_by(
                        subdomain=subdomain, status='active'
                    ).first()
                    if partner:
                        g.partner = partner
                        g.branding = db.session.query(BrandingSetting).filter_by(
                            partner_id=partner.id
                        ).first()

        # ── 2. Resolve tenant from authenticated user ──
        if current_user.is_authenticated:
            from app.models.core import Membership
            from app import db

            # Deterministic strategy: use session-stored active_tenant_id
            # when available, otherwise fall back to first membership.
            active_tenant_id = session.get('active_tenant_id')

            if active_tenant_id:
                # Validate the user actually belongs to this tenant
                membership = db.session.query(Membership).filter_by(
                    user_id=current_user.id,
                    tenant_id=active_tenant_id,
                ).first()
                if not membership:
                    # Session has stale tenant — clear and re-resolve
                    session.pop('active_tenant_id', None)
                    active_tenant_id = None

            if not active_tenant_id:
                # Fall back: pick the first membership and persist in session
                membership = db.session.query(Membership).filter_by(
                    user_id=current_user.id
                ).first()
                if membership:
                    active_tenant_id = membership.tenant_id
                    session['active_tenant_id'] = str(active_tenant_id)

            if membership:
                g.tenant_id = membership.tenant_id
                g.membership = membership

    @app.context_processor
    def inject_branding():
        """Make branding, platform name, and feature flags available to all templates."""
        return {
            'branding': getattr(g, 'branding', None),
            'partner': getattr(g, 'partner', None),
            'platform_name': app.config.get('PLATFORM_NAME', 'AgentGenie'),
            'feature_partner_program': app.config.get('FEATURE_PARTNER_PROGRAM', False),
            'feature_dfy': app.config.get('FEATURE_DFY', False),
            'feature_campaigns': app.config.get('FEATURE_CAMPAIGNS', False),
        }
