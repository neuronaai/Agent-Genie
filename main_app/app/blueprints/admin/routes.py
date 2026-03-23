"""Admin blueprint — all routes require superadmin role."""
from flask import Blueprint, render_template, abort, g
from flask_login import login_required

from app import db
from app.services.tenant.scoping import require_superadmin

admin_bp = Blueprint('admin', __name__)


@admin_bp.before_request
@login_required
def before_request():
    """All admin routes require authentication AND superadmin role."""
    membership = getattr(g, 'membership', None)
    if not membership or membership.role != 'superadmin':
        abort(403, description='Superadmin access required.')


# =========================================================================
# Dashboard Home
# =========================================================================
@admin_bp.route('/')
def home():
    from app.models.core import Tenant, Partner, Subscription, Agent, CallLog
    from sqlalchemy import func
    total_tenants = db.session.query(Tenant).count()
    active_tenants = db.session.query(Tenant).filter_by(status='active').count()
    total_partners = db.session.query(Partner).count()
    active_subs = db.session.query(Subscription).filter_by(status='active').count()
    total_agents = db.session.query(Agent).count()
    active_agents = db.session.query(Agent).filter_by(status='active').count()
    total_calls = db.session.query(CallLog).count()
    return render_template('admin/home.html',
                           total_tenants=total_tenants,
                           active_tenants=active_tenants,
                           total_partners=total_partners,
                           active_subs=active_subs,
                           total_agents=total_agents,
                           active_agents=active_agents,
                           total_calls=total_calls)


# =========================================================================
# Customers
# =========================================================================
@admin_bp.route('/customers')
def customers():
    from app.models.core import Tenant, Organization
    tenants = db.session.query(Tenant).order_by(Tenant.created_at.desc()).all()
    return render_template('admin/customers.html', tenants=tenants)


@admin_bp.route('/customers/<tenant_id>')
def customer_detail(tenant_id):
    from app.models.core import (
        Tenant, Organization, Subscription, SupportNote,
        Agent, PhoneNumber, CallLog, Invoice,
    )
    tenant = db.session.get(Tenant, tenant_id)
    if not tenant:
        return 'Not found', 404
    org = db.session.query(Organization).filter_by(tenant_id=tenant_id).first()
    sub = db.session.query(Subscription).filter_by(tenant_id=tenant_id).first()
    notes = db.session.query(SupportNote).filter_by(tenant_id=tenant_id).order_by(SupportNote.created_at.desc()).all()
    agents = db.session.query(Agent).filter_by(tenant_id=tenant_id).all()
    numbers = db.session.query(PhoneNumber).filter_by(tenant_id=tenant_id).all()
    recent_calls = db.session.query(CallLog).filter_by(tenant_id=tenant_id).order_by(CallLog.created_at.desc()).limit(10).all()
    invoices = db.session.query(Invoice).filter_by(tenant_id=tenant_id).order_by(Invoice.created_at.desc()).limit(10).all()
    # Extract tenant_settings for the template
    tenant_settings = (org.tenant_settings or {}) if org else {}

    return render_template('admin/customer_detail.html',
                           tenant=tenant, org=org, sub=sub, notes=notes,
                           agents=agents, numbers=numbers,
                           recent_calls=recent_calls, invoices=invoices,
                           tenant_settings=tenant_settings)


@admin_bp.route('/customers/<tenant_id>/tenant-settings', methods=['POST'])
def customer_update_tenant_settings(tenant_id):
    """Update per-tenant feature toggles (e.g. recordings_enabled)."""
    from flask import request, flash, redirect, url_for
    from app.models.core import Tenant, Organization
    tenant = db.session.get(Tenant, tenant_id)
    if not tenant:
        flash('Tenant not found.', 'error')
        return redirect(url_for('admin.customers'))

    org = db.session.query(Organization).filter_by(tenant_id=tenant_id).first()
    if not org:
        flash('Organization not found for this tenant.', 'error')
        return redirect(url_for('admin.customer_detail', tenant_id=tenant_id))

    # Read current settings (or start fresh)
    settings = dict(org.tenant_settings or {})

    # recordings_enabled: "default" removes the override, "true"/"false" sets it
    rec_val = request.form.get('recordings_enabled', 'default').strip().lower()
    if rec_val == 'default':
        settings.pop('recordings_enabled', None)
    elif rec_val == 'true':
        settings['recordings_enabled'] = True
    elif rec_val == 'false':
        settings['recordings_enabled'] = False

    org.tenant_settings = settings
    db.session.commit()
    flash('Tenant feature settings updated.', 'success')
    return redirect(url_for('admin.customer_detail', tenant_id=tenant_id))


@admin_bp.route('/customers/<tenant_id>/note', methods=['POST'])
def customer_add_note(tenant_id):
    from flask import request, flash, redirect, url_for
    from flask_login import current_user
    from app.models.core import Tenant, SupportNote
    tenant = db.session.get(Tenant, tenant_id)
    if not tenant:
        flash('Tenant not found.', 'error')
        return redirect(url_for('admin.customers'))
    note_text = request.form.get('note', '').strip()
    if note_text:
        note = SupportNote(tenant_id=tenant_id, admin_user_id=current_user.id, note=note_text)
        db.session.add(note)
        db.session.commit()
        flash('Support note added.', 'success')
    return redirect(url_for('admin.customer_detail', tenant_id=tenant_id))


# =========================================================================
# Partners (visible but read-only; partner program is deferred)
# =========================================================================
@admin_bp.route('/partners')
def partners():
    from flask import current_app
    if not current_app.config.get('FEATURE_PARTNER_PROGRAM'):
        from app.models.core import Partner
        partners_list = db.session.query(Partner).order_by(Partner.created_at.desc()).all()
        return render_template('admin/partners.html', partners=partners_list, deferred=True)
    from app.models.core import Partner
    partners_list = db.session.query(Partner).order_by(Partner.created_at.desc()).all()
    return render_template('admin/partners.html', partners=partners_list, deferred=False)


# =========================================================================
# Pricing & Plans
# =========================================================================
@admin_bp.route('/pricing')
def pricing():
    from app.models.core import PlanDefinition, TopupPackDefinition
    plans = db.session.query(PlanDefinition).order_by(PlanDefinition.price_monthly_cents).all()
    topup_packs = db.session.query(TopupPackDefinition).order_by(TopupPackDefinition.minutes).all()
    return render_template('admin/pricing.html', plans=plans, topup_packs=topup_packs)


@admin_bp.route('/pricing/plan/create', methods=['POST'])
def plan_create():
    from flask import request, flash, redirect, url_for
    from app.models.core import PlanDefinition
    plan = PlanDefinition(
        name=request.form.get('name', 'New Plan'),
        price_monthly_cents=int(request.form.get('price_monthly_cents', 0)),
        included_minutes=int(request.form.get('included_minutes', 0)),
        included_agents=int(request.form.get('included_agents', 1)),
        included_numbers=int(request.form.get('included_numbers', 1)),
        overage_rate_cents=int(request.form.get('overage_rate_cents', 10)),
        additional_number_rate_cents=int(request.form.get('additional_number_rate_cents', 500)),
        stripe_price_id=request.form.get('stripe_price_id', '').strip() or None,
        is_active=bool(request.form.get('is_active')),
    )
    db.session.add(plan)
    db.session.commit()
    flash(f'Plan "{plan.name}" created.', 'success')
    return redirect(url_for('admin.pricing'))


@admin_bp.route('/pricing/plan/<plan_id>/edit', methods=['POST'])
def plan_edit(plan_id):
    from flask import request, flash, redirect, url_for
    from app.models.core import PlanDefinition
    plan = db.session.get(PlanDefinition, plan_id)
    if not plan:
        flash('Plan not found.', 'error')
        return redirect(url_for('admin.pricing'))
    plan.name = request.form.get('name', plan.name)
    plan.price_monthly_cents = int(request.form.get('price_monthly_cents', plan.price_monthly_cents))
    plan.included_minutes = int(request.form.get('included_minutes', plan.included_minutes))
    plan.included_agents = int(request.form.get('included_agents', plan.included_agents))
    plan.included_numbers = int(request.form.get('included_numbers', plan.included_numbers))
    plan.overage_rate_cents = int(request.form.get('overage_rate_cents', plan.overage_rate_cents))
    plan.additional_number_rate_cents = int(request.form.get('additional_number_rate_cents', plan.additional_number_rate_cents))
    plan.stripe_price_id = request.form.get('stripe_price_id', '').strip() or plan.stripe_price_id
    plan.is_active = bool(request.form.get('is_active'))
    db.session.commit()
    flash(f'Plan "{plan.name}" updated.', 'success')
    return redirect(url_for('admin.pricing'))


@admin_bp.route('/pricing/topup/create', methods=['POST'])
def topup_create():
    from flask import request, flash, redirect, url_for
    from app.models.core import TopupPackDefinition
    pack = TopupPackDefinition(
        label=request.form.get('label', 'New Pack'),
        minutes=int(request.form.get('minutes', 0)),
        price_cents=int(request.form.get('price_cents', 0)),
        is_active=bool(request.form.get('is_active')),
    )
    db.session.add(pack)
    db.session.commit()
    flash(f'Top-up pack "{pack.label}" created.', 'success')
    return redirect(url_for('admin.pricing'))


@admin_bp.route('/pricing/topup/<pack_id>/edit', methods=['POST'])
def topup_edit(pack_id):
    from flask import request, flash, redirect, url_for
    from app.models.core import TopupPackDefinition
    pack = db.session.get(TopupPackDefinition, pack_id)
    if not pack:
        flash('Pack not found.', 'error')
        return redirect(url_for('admin.pricing'))
    pack.label = request.form.get('label', pack.label)
    pack.minutes = int(request.form.get('minutes', pack.minutes))
    pack.price_cents = int(request.form.get('price_cents', pack.price_cents))
    pack.is_active = bool(request.form.get('is_active'))
    db.session.commit()
    flash(f'Top-up pack "{pack.label}" updated.', 'success')
    return redirect(url_for('admin.pricing'))


# =========================================================================
# Billing Review & Revenue
# =========================================================================
@admin_bp.route('/billing-review')
def billing_review():
    from flask import request
    from app.models.core import UsageRecord, Tenant
    status_filter = request.args.get('status', 'adjusted')
    query = db.session.query(UsageRecord)
    if status_filter and status_filter != 'all':
        query = query.filter_by(reconciliation_status=status_filter)
    records = query.order_by(UsageRecord.created_at.desc()).limit(100).all()
    return render_template('admin/billing_review.html', records=records, status_filter=status_filter)


@admin_bp.route('/revenue')
def revenue():
    """Revenue dashboard with aggregate billing metrics."""
    from app.models.core import (
        Subscription, Invoice, Payment, PlanDefinition,
        UsageSummary, MinuteTopupPurchase,
    )
    from sqlalchemy import func

    plan_breakdown = db.session.query(
        PlanDefinition.name,
        func.count(Subscription.id).label('count'),
        PlanDefinition.price_monthly_cents,
    ).join(Subscription, Subscription.plan_id == PlanDefinition.id).filter(
        Subscription.status == 'active'
    ).group_by(PlanDefinition.name, PlanDefinition.price_monthly_cents).all()

    total_mrr = sum(row.count * row.price_monthly_cents for row in plan_breakdown)

    recent_payments = db.session.query(Payment).filter_by(
        status='succeeded'
    ).order_by(Payment.created_at.desc()).limit(20).all()

    total_revenue = db.session.query(
        func.coalesce(func.sum(Payment.amount_cents), 0)
    ).filter_by(status='succeeded').scalar()

    topup_revenue = db.session.query(
        func.coalesce(func.sum(MinuteTopupPurchase.purchase_price_cents), 0)
    ).scalar()

    open_invoices = db.session.query(Invoice).filter_by(status='open').count()
    uncollectible = db.session.query(Invoice).filter_by(status='uncollectible').count()

    return render_template('admin/revenue.html',
                           plan_breakdown=plan_breakdown,
                           total_mrr=total_mrr,
                           recent_payments=recent_payments,
                           total_revenue=total_revenue,
                           topup_revenue=topup_revenue,
                           open_invoices=open_invoices,
                           uncollectible=uncollectible)


@admin_bp.route('/payouts')
def payouts():
    from app.models.core import PartnerSettlementRecord
    settlements = db.session.query(PartnerSettlementRecord).order_by(PartnerSettlementRecord.created_at.desc()).all()
    return render_template('admin/payouts.html', settlements=settlements)


# =========================================================================
# Webhooks & System
# =========================================================================
@admin_bp.route('/webhooks')
def webhooks():
    from app.models.core import WebhookEvent
    events = db.session.query(WebhookEvent).order_by(WebhookEvent.created_at.desc()).limit(100).all()
    return render_template('admin/webhooks.html', events=events)


@admin_bp.route('/settings')
def platform_settings():
    from app.models.core import PlatformSetting
    settings = db.session.query(PlatformSetting).order_by(PlatformSetting.key).all()
    return render_template('admin/platform_settings.html', settings=settings)


@admin_bp.route('/settings/update', methods=['POST'])
def platform_settings_update():
    from flask import request, flash, redirect, url_for
    from app.models.core import PlatformSetting
    key = request.form.get('key', '').strip()
    value = request.form.get('value', '').strip()
    if key:
        setting = db.session.query(PlatformSetting).filter_by(key=key).first()
        if setting:
            import json
            try:
                setting.value = json.loads(value)
            except (json.JSONDecodeError, ValueError):
                setting.value = value
            db.session.commit()
            flash(f'Setting "{key}" updated.', 'success')
        else:
            flash(f'Setting "{key}" not found.', 'error')
    return redirect(url_for('admin.platform_settings'))


# =========================================================================
# Usage Reconciliation
# =========================================================================
@admin_bp.route('/reconciliation')
def reconciliation():
    from flask import request as req
    from app.models.core import UsageRecord, CallLog, Tenant
    status_filter = req.args.get('status', 'all')
    query = db.session.query(UsageRecord).join(
        CallLog, CallLog.id == UsageRecord.call_log_id
    )
    if status_filter and status_filter != 'all':
        query = query.filter(UsageRecord.reconciliation_status == status_filter)
    records = query.order_by(UsageRecord.created_at.desc()).limit(200).all()
    return render_template('admin/reconciliation.html', records=records, status_filter=status_filter)


@admin_bp.route('/reconciliation/<record_id>/adjust', methods=['POST'])
def reconciliation_adjust(record_id):
    from flask import request as req, flash, redirect, url_for
    from app.models.core import UsageRecord
    record = db.session.get(UsageRecord, record_id)
    if not record:
        flash('Record not found.', 'error')
        return redirect(url_for('admin.reconciliation'))
    new_billable = req.form.get('internally_billable_seconds')
    reason = req.form.get('adjustment_reason', '').strip()
    if new_billable is not None:
        record.internally_billable_seconds = int(new_billable)
        record.reconciliation_status = 'adjusted'
        record.adjustment_reason = reason or 'Manual admin adjustment'
        db.session.commit()
        flash('Usage record adjusted.', 'success')
    return redirect(url_for('admin.reconciliation'))


# =========================================================================
# Failed Jobs
# =========================================================================
@admin_bp.route('/failed-jobs')
def failed_jobs():
    from app.models.core import Agent, WebhookEvent
    failed_agents = db.session.query(Agent).filter(
        Agent.status.in_(['failed', 'needs_attention'])
    ).order_by(Agent.updated_at.desc()).all()
    failed_webhooks = db.session.query(WebhookEvent).filter_by(
        status='failed'
    ).order_by(WebhookEvent.created_at.desc()).limit(50).all()
    return render_template('admin/failed_jobs.html',
                           failed_agents=failed_agents,
                           failed_webhooks=failed_webhooks)


# =========================================================================
# DFY Admin (gated by feature flag)
# =========================================================================
@admin_bp.route('/dfy')
def dfy_pipeline():
    from flask import current_app, request as req
    if not current_app.config.get('FEATURE_DFY'):
        return render_template('admin/feature_deferred.html', feature_name='Done For You (DFY)')
    from app.models.core import DfyProject, DfyPackage, Tenant, User
    status_filter = req.args.get('status', '')
    owner_filter = req.args.get('owner', '')
    query = db.session.query(DfyProject)
    if status_filter:
        query = query.filter_by(status=status_filter)
    if owner_filter:
        query = query.filter_by(owner_id=owner_filter)
    projects = query.order_by(DfyProject.created_at.desc()).all()
    admins = db.session.query(User).join(User.memberships).filter(
        User.memberships.any(role='superadmin')
    ).all()
    return render_template('admin/dfy_pipeline.html',
                           projects=projects, status_filter=status_filter,
                           owner_filter=owner_filter, admins=admins)


@admin_bp.route('/dfy/packages')
def dfy_packages():
    from flask import current_app
    if not current_app.config.get('FEATURE_DFY'):
        return render_template('admin/feature_deferred.html', feature_name='Done For You (DFY)')
    from app.models.core import DfyPackage
    packages = db.session.query(DfyPackage).order_by(DfyPackage.sort_order).all()
    return render_template('admin/dfy_packages.html', packages=packages)


@admin_bp.route('/dfy/packages/create', methods=['POST'])
def dfy_package_create():
    from flask import current_app, request as req, flash, redirect, url_for
    if not current_app.config.get('FEATURE_DFY'):
        abort(404)
    from app.models.core import DfyPackage
    features_raw = req.form.get('features', '')
    features = [f.strip() for f in features_raw.split('\n') if f.strip()]
    price_str = req.form.get('price_cents', '').strip()
    price_cents = int(price_str) if price_str else None
    pkg = DfyPackage(
        name=req.form.get('name', 'New Package'),
        slug=req.form.get('slug', 'new-package'),
        description=req.form.get('description', ''),
        features=features,
        price_cents=price_cents,
        billing_type=req.form.get('billing_type', 'one_time'),
        stripe_price_id=req.form.get('stripe_price_id', '').strip() or None,
        estimated_days=int(req.form.get('estimated_days', 0)) or None,
        sort_order=int(req.form.get('sort_order', 0)),
        is_active=bool(req.form.get('is_active')),
    )
    db.session.add(pkg)
    db.session.commit()
    flash(f'Package "{pkg.name}" created.', 'success')
    return redirect(url_for('admin.dfy_packages'))


@admin_bp.route('/dfy/packages/<pkg_id>/edit', methods=['POST'])
def dfy_package_edit(pkg_id):
    from flask import current_app, request as req, flash, redirect, url_for
    if not current_app.config.get('FEATURE_DFY'):
        abort(404)
    from app.models.core import DfyPackage
    pkg = db.session.get(DfyPackage, pkg_id)
    if not pkg:
        flash('Package not found.', 'error')
        return redirect(url_for('admin.dfy_packages'))
    features_raw = req.form.get('features', '')
    features = [f.strip() for f in features_raw.split('\n') if f.strip()]
    price_str = req.form.get('price_cents', '').strip()
    pkg.name = req.form.get('name', pkg.name)
    pkg.slug = req.form.get('slug', pkg.slug)
    pkg.description = req.form.get('description', pkg.description)
    pkg.features = features
    pkg.price_cents = int(price_str) if price_str else None
    pkg.billing_type = req.form.get('billing_type', pkg.billing_type)
    pkg.stripe_price_id = req.form.get('stripe_price_id', '').strip() or pkg.stripe_price_id
    pkg.estimated_days = int(req.form.get('estimated_days', 0)) or pkg.estimated_days
    pkg.sort_order = int(req.form.get('sort_order', pkg.sort_order))
    pkg.is_active = bool(req.form.get('is_active'))
    db.session.commit()
    flash(f'Package "{pkg.name}" updated.', 'success')
    return redirect(url_for('admin.dfy_packages'))


@admin_bp.route('/dfy/projects/<project_id>')
def dfy_project_admin(project_id):
    from flask import current_app
    if not current_app.config.get('FEATURE_DFY'):
        abort(404)
    from app.models.core import DfyProject, DfyMessage, Agent, User
    project = db.session.get(DfyProject, project_id)
    if not project:
        return 'Not found', 404
    messages = DfyMessage.query.filter_by(project_id=project.id).order_by(DfyMessage.created_at.asc()).all()
    linked_agent = db.session.get(Agent, project.agent_id) if project.agent_id else None
    admins = db.session.query(User).join(User.memberships).filter(
        User.memberships.any(role='superadmin')
    ).all()
    return render_template('admin/dfy_project_detail.html',
                           project=project, messages=messages,
                           linked_agent=linked_agent, admins=admins)


@admin_bp.route('/dfy/projects/<project_id>/update', methods=['POST'])
def dfy_project_update(project_id):
    from flask import current_app, request as req, flash, redirect, url_for
    if not current_app.config.get('FEATURE_DFY'):
        abort(404)
    from app.models.core import DfyProject
    from datetime import datetime
    project = db.session.get(DfyProject, project_id)
    if not project:
        flash('Project not found.', 'error')
        return redirect(url_for('admin.dfy_pipeline'))
    action = req.form.get('action', '')
    if action == 'assign_owner':
        project.owner_id = req.form.get('owner_id') or None
        flash('Owner assigned.', 'success')
    elif action == 'change_status':
        new_status = req.form.get('status', '')
        if new_status in ('intake', 'pending_payment', 'in_progress', 'in_review', 'completed', 'canceled'):
            project.status = new_status
            flash(f'Status changed to {new_status}.', 'success')
    elif action == 'update_notes':
        project.admin_notes = req.form.get('admin_notes', '')
        flash('Admin notes updated.', 'success')
    elif action == 'set_delivery_date':
        date_str = req.form.get('target_delivery_date', '')
        if date_str:
            project.target_delivery_date = datetime.strptime(date_str, '%Y-%m-%d').date()
            flash('Target delivery date updated.', 'success')
    elif action == 'link_agent':
        project.agent_id = req.form.get('agent_id') or None
        flash('Agent linked.', 'success')
    db.session.commit()
    return redirect(url_for('admin.dfy_project_admin', project_id=project_id))


@admin_bp.route('/dfy/projects/<project_id>/message', methods=['POST'])
def dfy_admin_message(project_id):
    from flask import current_app, request as req, flash, redirect, url_for
    from flask_login import current_user
    if not current_app.config.get('FEATURE_DFY'):
        abort(404)
    from app.models.core import DfyProject, DfyMessage
    project = db.session.get(DfyProject, project_id)
    if not project:
        flash('Project not found.', 'error')
        return redirect(url_for('admin.dfy_pipeline'))
    content = req.form.get('content', '').strip()
    is_admin_note = req.form.get('is_admin_note') == '1'
    if not content:
        flash('Message cannot be empty.', 'error')
        return redirect(url_for('admin.dfy_project_admin', project_id=project_id))
    msg = DfyMessage(
        project_id=project.id, sender_id=current_user.id,
        content=content, is_admin_note=is_admin_note,
    )
    db.session.add(msg)
    db.session.commit()
    flash('Admin note saved.' if is_admin_note else 'Message sent to client.', 'success')
    return redirect(url_for('admin.dfy_project_admin', project_id=project_id))
