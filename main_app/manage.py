#!/usr/bin/env python3
"""Management CLI for AgentGenie.

Usage:
    python manage.py seed                     # Insert missing only (production-safe default)
    python manage.py seed --force-update      # Update existing seed records to code defaults
    python manage.py seed --flush             # Reset all seed data (dev/staging only)
    python manage.py seed --demo              # Also seed demo/sample data (dev/staging only)
    python manage.py create-admin EMAIL PWD   # Create a superadmin user
    python manage.py db-init                  # Initialize migrations and create tables
"""
import os
import sys
import click
from flask.cli import FlaskGroup
from app import create_app, db

app = create_app()


@click.group(cls=FlaskGroup, create_app=lambda: app)
def cli():
    """AgentGenie management commands."""
    pass


# ---------------------------------------------------------------------------
# Seed data definitions — production baseline
# ---------------------------------------------------------------------------

PLAN_SEEDS = [
    {
        'slug': 'starter',
        'name': 'Starter',
        'price_monthly_cents': 9900,
        'included_minutes': 250,
        'included_agents': 1,
        'included_numbers': 1,
        'overage_rate_cents': 39,
        'additional_number_rate_cents': 900,
    },
    {
        'slug': 'growth',
        'name': 'Growth',
        'price_monthly_cents': 24900,
        'included_minutes': 800,
        'included_agents': 3,
        'included_numbers': 3,
        'overage_rate_cents': 35,
        'additional_number_rate_cents': 800,
    },
    {
        'slug': 'scale',
        'name': 'Scale',
        'price_monthly_cents': 49900,
        'included_minutes': 1800,
        'included_agents': 8,
        'included_numbers': 8,
        'overage_rate_cents': 32,
        'additional_number_rate_cents': 700,
    },
]

TOPUP_PACK_SEEDS = [
    {'slug': 'topup-100', 'label': '100 Minute Pack', 'minutes': 100, 'price_cents': 3900},
    {'slug': 'topup-500', 'label': '500 Minute Pack', 'minutes': 500, 'price_cents': 17500},
    {'slug': 'topup-1000', 'label': '1000 Minute Pack', 'minutes': 1000, 'price_cents': 32000},
]

TOOL_TEMPLATE_SEEDS = [
    {
        'slug': 'calendar_check_availability',
        'name': 'Check Calendar Availability',
        'category': 'calendar',
        'tool_type': 'real_time',
        'access_tier': 'self_serve',
        'description': 'Check if a time slot is available on the calendar during a call.',
        'default_description_for_llm': 'Check if a time slot is available on the calendar.',
        'default_parameters_schema': {
            'type': 'object',
            'properties': {'date': {'type': 'string'}, 'time': {'type': 'string'}},
            'required': ['date', 'time'],
        },
        'requires_oauth': True,
        'oauth_provider': 'google',
    },
    {
        'slug': 'calendar_book_appointment',
        'name': 'Book Appointment',
        'category': 'calendar',
        'tool_type': 'real_time',
        'access_tier': 'self_serve',
        'description': 'Book an appointment on the calendar for the caller during a call.',
        'default_description_for_llm': 'Book an appointment on the calendar for the caller.',
        'default_parameters_schema': {
            'type': 'object',
            'properties': {
                'date': {'type': 'string'},
                'time': {'type': 'string'},
                'caller_name': {'type': 'string'},
            },
            'required': ['date', 'time', 'caller_name'],
        },
        'requires_oauth': True,
        'oauth_provider': 'google',
    },
    {
        'slug': 'calendar_send_invite',
        'name': 'Send Calendar Invite',
        'category': 'calendar',
        'tool_type': 'post_call',
        'access_tier': 'self_serve',
        'description': 'Send a calendar invite to the caller after the call ends.',
        'default_description_for_llm': 'Send a calendar invite to the caller after the call.',
        'requires_oauth': True,
        'oauth_provider': 'google',
    },
    {
        'slug': 'email_send_summary',
        'name': 'Email Call Summary',
        'category': 'email',
        'tool_type': 'post_call',
        'access_tier': 'self_serve',
        'description': 'Send an email summary of the call. Routed through our backend email provider.',
        'default_description_for_llm': 'Send an email summary of the call to a specified address.',
    },
    {
        'slug': 'email_send_followup',
        'name': 'Email Follow-up',
        'category': 'email',
        'tool_type': 'post_call',
        'access_tier': 'self_serve',
        'description': 'Send a follow-up email to the caller. Routed through our backend email provider.',
        'default_description_for_llm': 'Send a follow-up email to the caller after the call.',
    },
    {
        'slug': 'sms_send_followup',
        'name': 'SMS Follow-up',
        'category': 'sms',
        'tool_type': 'post_call',
        'access_tier': 'self_serve',
        'description': 'Send an SMS follow-up message to the caller after the call.',
        'default_description_for_llm': 'Send an SMS follow-up message to the caller after the call.',
    },
    {
        'slug': 'note_call_summary',
        'name': 'Save Call Notes',
        'category': 'note_summary',
        'tool_type': 'post_call',
        'access_tier': 'self_serve',
        'description': 'Automatically generate and save structured call notes after the call ends.',
        'default_description_for_llm': 'Automatically generate and save structured call notes after the call ends.',
    },
    {
        'slug': 'note_deliver_summary',
        'name': 'Deliver Call Summary',
        'category': 'note_summary',
        'tool_type': 'post_call',
        'access_tier': 'self_serve',
        'description': 'Deliver a formatted call summary via email or webhook after the call.',
        'default_description_for_llm': 'Deliver a formatted call summary via email or webhook after the call.',
    },
    {
        'slug': 'crm_lookup_contact',
        'name': 'CRM Lookup Contact',
        'category': 'crm_ticket',
        'tool_type': 'real_time',
        'access_tier': 'dfy_only',
        'description': 'Look up a contact in the CRM by phone number during a call.',
        'default_description_for_llm': 'Look up a contact in the CRM by phone number.',
        'requires_oauth': True,
        'oauth_provider': 'hubspot',
    },
    {
        'slug': 'crm_log_call',
        'name': 'CRM Log Call',
        'category': 'crm_ticket',
        'tool_type': 'post_call',
        'access_tier': 'dfy_only',
        'description': 'Log the call details and transcript in the CRM after the call.',
        'default_description_for_llm': 'Log the call details and transcript in the CRM.',
        'requires_oauth': True,
        'oauth_provider': 'hubspot',
    },
    {
        'slug': 'crm_create_ticket',
        'name': 'Create Support Ticket',
        'category': 'crm_ticket',
        'tool_type': 'post_call',
        'access_tier': 'dfy_only',
        'description': 'Create a support ticket in the ticketing system after the call.',
        'default_description_for_llm': 'Create a support ticket in the ticketing system.',
        'requires_oauth': True,
        'oauth_provider': 'hubspot',
    },
    {
        'slug': 'custom_webhook_realtime',
        'name': 'Custom Webhook (Real-Time)',
        'category': 'custom_webhook',
        'tool_type': 'real_time',
        'access_tier': 'dfy_only',
        'description': 'Call a custom webhook URL during the call and use the response.',
        'default_description_for_llm': 'Call a custom webhook URL during the call and use the response.',
    },
    {
        'slug': 'custom_webhook_postcall',
        'name': 'Custom Webhook (Post-Call)',
        'category': 'custom_webhook',
        'tool_type': 'post_call',
        'access_tier': 'dfy_only',
        'description': 'Send call data to a custom webhook URL after the call ends.',
        'default_description_for_llm': 'Send call data to a custom webhook URL after the call ends.',
    },
]

DFY_PACKAGE_SEEDS = [
    {
        'slug': 'inbound-setup',
        'name': 'Inbound Agent Setup',
        'description': 'We build and configure a production-ready inbound AI agent for your business.',
        'features': ['Custom prompt engineering', 'Voice selection and tuning', 'Business hours configuration', 'Phone number provisioning', 'End-to-end testing'],
        'price_cents': 49900,
        'billing_type': 'one_time',
        'estimated_days': 5,
        'sort_order': 1,
    },
    {
        'slug': 'outbound-setup',
        'name': 'Outbound Campaign Setup',
        'description': 'We design and launch your first outbound calling campaign with optimized scripts.',
        'features': ['Outbound agent creation', 'Script optimization', 'Contact list import', 'Calling window configuration', 'Retry strategy setup'],
        'price_cents': 69900,
        'billing_type': 'one_time',
        'estimated_days': 7,
        'sort_order': 2,
    },
    {
        'slug': 'calendar-setup',
        'name': 'Calendar Integration Setup',
        'description': 'Connect your booking system so your agent can schedule appointments in real time.',
        'features': ['Calendar provider connection', 'Availability rules', 'Booking confirmation flow', 'Reschedule/cancel handling', 'Timezone configuration'],
        'price_cents': 39900,
        'billing_type': 'one_time',
        'estimated_days': 3,
        'sort_order': 3,
    },
    {
        'slug': 'crm-webhook-workflow',
        'name': 'CRM / Webhook Workflow',
        'description': 'Integrate your CRM or business tools via webhooks for automated post-call actions.',
        'features': ['CRM field mapping', 'Webhook endpoint setup', 'Post-call data sync', 'Lead scoring rules', 'Error handling and retry'],
        'price_cents': 59900,
        'billing_type': 'one_time',
        'estimated_days': 5,
        'sort_order': 4,
    },
    {
        'slug': 'mcp-integration-setup',
        'name': 'MCP Integration Setup',
        'description': 'Set up a managed MCP gateway with approved connectors for your AI agents.',
        'features': ['MCP server provisioning', 'Connector configuration', 'Tool-to-agent assignment', 'Security and audit setup', 'Usage metering activation'],
        'price_cents': 79900,
        'billing_type': 'one_time',
        'estimated_days': 7,
        'sort_order': 5,
    },
    {
        'slug': 'email-sms-workflow',
        'name': 'Email / SMS Workflow Setup',
        'description': 'Configure automated email and SMS follow-ups triggered by call outcomes.',
        'features': ['Email provider integration', 'SMS provider integration', 'Template design', 'Trigger rule configuration', 'Delivery monitoring'],
        'price_cents': 44900,
        'billing_type': 'one_time',
        'estimated_days': 4,
        'sort_order': 6,
    },
    {
        'slug': 'custom-workflow-build',
        'name': 'Custom Workflow Build',
        'description': 'A fully custom integration or workflow tailored to your unique business requirements.',
        'features': ['Requirements discovery', 'Custom architecture design', 'Implementation and testing', 'Documentation', 'Post-launch support'],
        'price_cents': None,
        'billing_type': 'custom_quote',
        'estimated_days': 14,
        'sort_order': 7,
    },
    {
        'slug': 'monthly-optimization',
        'name': 'Monthly Optimization Retainer',
        'description': 'Ongoing expert tuning of your agents, scripts, and workflows every month.',
        'features': ['Monthly performance review', 'Prompt refinement', 'A/B script testing', 'Integration health checks', 'Priority support'],
        'price_cents': 29900,
        'billing_type': 'recurring',
        'estimated_days': None,
        'sort_order': 8,
    },
]

PLATFORM_SETTING_SEEDS = {
    'settlement_hold_days': {'value': 30, 'description': 'Days before revenue becomes eligible for partner payout'},
    'minimum_payout_cents': {'value': 5000, 'description': 'Minimum payout threshold in cents ($50)'},
    'default_revenue_split_pct': {'value': 50, 'description': 'Default partner revenue share percentage'},
    'recording_retention_days': {'value': 90, 'description': 'Days to retain call recordings'},
    'recordings_enabled': {'value': True, 'description': 'Global toggle: whether call recordings are visible to tenants'},
    'partner_setup_fee_cents': {'value': 49900, 'description': 'One-time partner setup fee ($499)'},
    'partner_recurring_fee_cents': {'value': 9900, 'description': 'Monthly partner platform fee ($99)'},
    'topup_100_min_cents': {'value': 3900, 'description': '100-minute top-up pack price ($39)'},
    'topup_500_min_cents': {'value': 17500, 'description': '500-minute top-up pack price ($175)'},
    'topup_1000_min_cents': {'value': 32000, 'description': '1000-minute top-up pack price ($320)'},
}


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------

def _seed_by_slug(model_class, seeds, force_update=False, slug_field='slug'):
    """Generic insert-missing / force-update seeder keyed by slug.

    Returns (created, skipped, updated) counts.
    """
    created = skipped = updated = 0
    for seed_data in seeds:
        slug_val = seed_data.get(slug_field) or seed_data.get('slug')
        existing = db.session.query(model_class).filter(
            getattr(model_class, slug_field) == slug_val
        ).first()

        if existing:
            if force_update:
                for k, v in seed_data.items():
                    if k != 'id' and hasattr(existing, k):
                        setattr(existing, k, v)
                updated += 1
                click.echo(f'  UPDATED  {model_class.__tablename__}: {slug_val}')
            else:
                skipped += 1
                click.echo(f'  SKIPPED  {model_class.__tablename__}: {slug_val} (already exists)')
        else:
            obj = model_class(**{k: v for k, v in seed_data.items() if hasattr(model_class, k)})
            db.session.add(obj)
            created += 1
            click.echo(f'  CREATED  {model_class.__tablename__}: {slug_val}')

    return created, skipped, updated


def _seed_by_key(seeds, force_update=False):
    """Seed PlatformSettings keyed by `key`."""
    from app.models.core import PlatformSetting
    created = skipped = updated = 0
    for key, data in seeds.items():
        existing = db.session.query(PlatformSetting).filter_by(key=key).first()
        if existing:
            if force_update:
                existing.value = data['value']
                existing.description = data['description']
                updated += 1
                click.echo(f'  UPDATED  platform_settings: {key}')
            else:
                skipped += 1
                click.echo(f'  SKIPPED  platform_settings: {key} (already exists)')
        else:
            db.session.add(PlatformSetting(key=key, value=data['value'], description=data['description']))
            created += 1
            click.echo(f'  CREATED  platform_settings: {key}')
    return created, skipped, updated


def _seed_topup_packs(seeds, force_update=False):
    """Seed TopupPackDefinition keyed by minutes (stable unique key)."""
    from app.models.core import TopupPackDefinition
    created = skipped = updated = 0
    for seed_data in seeds:
        existing = db.session.query(TopupPackDefinition).filter_by(
            minutes=seed_data['minutes']
        ).first()
        if existing:
            if force_update:
                existing.label = seed_data['label']
                existing.price_cents = seed_data['price_cents']
                updated += 1
                click.echo(f'  UPDATED  topup_pack_definitions: {seed_data["minutes"]} min')
            else:
                skipped += 1
                click.echo(f'  SKIPPED  topup_pack_definitions: {seed_data["minutes"]} min (already exists)')
        else:
            obj = TopupPackDefinition(
                label=seed_data['label'],
                minutes=seed_data['minutes'],
                price_cents=seed_data['price_cents'],
                is_active=True,
            )
            db.session.add(obj)
            created += 1
            click.echo(f'  CREATED  topup_pack_definitions: {seed_data["minutes"]} min')
    return created, skipped, updated


def _seed_plans(seeds, force_update=False):
    """Seed PlanDefinition keyed by name (stable unique key)."""
    from app.models.core import PlanDefinition
    created = skipped = updated = 0
    for seed_data in seeds:
        existing = db.session.query(PlanDefinition).filter_by(
            name=seed_data['name']
        ).first()
        if existing:
            if force_update:
                for k, v in seed_data.items():
                    if k not in ('id', 'slug') and hasattr(existing, k):
                        setattr(existing, k, v)
                updated += 1
                click.echo(f'  UPDATED  plan_definitions: {seed_data["name"]}')
            else:
                skipped += 1
                click.echo(f'  SKIPPED  plan_definitions: {seed_data["name"]} (already exists)')
        else:
            obj = PlanDefinition(
                name=seed_data['name'],
                price_monthly_cents=seed_data['price_monthly_cents'],
                included_minutes=seed_data['included_minutes'],
                included_agents=seed_data['included_agents'],
                included_numbers=seed_data['included_numbers'],
                overage_rate_cents=seed_data['overage_rate_cents'],
                additional_number_rate_cents=seed_data['additional_number_rate_cents'],
            )
            db.session.add(obj)
            created += 1
            click.echo(f'  CREATED  plan_definitions: {seed_data["name"]}')
    return created, skipped, updated


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

@cli.command('seed')
@click.option('--force-update', is_flag=True, default=False,
              help='Update existing seed records to match code defaults. Manual use only.')
@click.option('--flush', is_flag=True, default=False,
              help='Drop and recreate all tables before seeding. Dev/staging only.')
@click.option('--demo', is_flag=True, default=False,
              help='Also seed demo/sample data. Dev/staging only.')
def seed_command(force_update, flush, demo):
    """Seed baseline production data.

    Default mode (no flags): INSERT MISSING ONLY — safe for every deploy.
    """
    from app.models.core import (
        PlanDefinition, TopupPackDefinition, ToolTemplate,
        DfyPackage, PlatformSetting,
    )

    env = os.environ.get('FLASK_ENV', 'development')

    # Safety: --flush only allowed in non-production
    if flush:
        if env == 'production':
            click.echo('ERROR: --flush is not allowed in production. Aborting.')
            sys.exit(1)
        click.echo('WARNING: Flushing all tables...')
        db.drop_all()
        # Recreate via migrations (not create_all) to stay consistent
        import subprocess
        result = subprocess.run(
            ['flask', 'db', 'upgrade'],
            env={**os.environ, 'FLASK_APP': 'wsgi.py'},
            cwd=os.path.dirname(__file__),
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            click.echo(f'Migration after flush failed: {result.stderr}', err=True)
            sys.exit(1)
        click.echo('Tables recreated via migrations.\n')

    mode = 'FORCE-UPDATE' if force_update else 'INSERT-MISSING-ONLY'
    click.echo(f'=== AgentGenie Seed ({mode}) ===\n')

    totals = {'created': 0, 'skipped': 0, 'updated': 0}

    def _add(counts):
        totals['created'] += counts[0]
        totals['skipped'] += counts[1]
        totals['updated'] += counts[2]

    # 1. Plans
    click.echo('[Plans]')
    _add(_seed_plans(PLAN_SEEDS, force_update))

    # 2. Top-Up Packs
    click.echo('\n[Top-Up Packs]')
    _add(_seed_topup_packs(TOPUP_PACK_SEEDS, force_update))

    # 3. Tool Templates
    click.echo('\n[Tool Templates]')
    _add(_seed_by_slug(ToolTemplate, TOOL_TEMPLATE_SEEDS, force_update))

    # 4. DFY Packages
    click.echo('\n[DFY Packages]')
    _add(_seed_by_slug(DfyPackage, DFY_PACKAGE_SEEDS, force_update))

    # 5. Platform Settings
    click.echo('\n[Platform Settings]')
    _add(_seed_by_key(PLATFORM_SETTING_SEEDS, force_update))

    db.session.commit()

    click.echo(f'\n=== Seed Complete ===')
    click.echo(f'  Created: {totals["created"]}')
    click.echo(f'  Skipped: {totals["skipped"]}')
    click.echo(f'  Updated: {totals["updated"]}')

    # 6. Optional demo data
    if demo:
        if env == 'production':
            click.echo('\nWARNING: --demo is not recommended in production. Skipping.')
        else:
            click.echo('\n[Demo Data]')
            _seed_demo_data()
            click.echo('Demo data seeded.')


@cli.command('create-admin')
@click.argument('email')
@click.argument('password')
def create_admin_command(email, password):
    """Create a superadmin user."""
    from werkzeug.security import generate_password_hash
    from app.models.core import User, Tenant, Membership

    existing = db.session.query(User).filter_by(email=email).first()
    if existing:
        click.echo(f'User {email} already exists.')
        return

    user = User(
        email=email,
        password_hash=generate_password_hash(password),
        is_verified=True,
    )
    db.session.add(user)
    tenant = Tenant(type='direct')
    db.session.add(tenant)
    db.session.flush()
    db.session.add(Membership(user_id=user.id, tenant_id=tenant.id, role='superadmin'))
    db.session.commit()
    click.echo(f'Superadmin created: {email}')


@cli.command('db-init')
def db_init_command():
    """Initialize the database using Flask-Migrate (Alembic).

    Applies any pending migrations.  There is NO fallback to
    db.create_all() — if migrations fail, the command exits with
    an error so the issue can be diagnosed.

    Note: The release.sh script calls ``flask db upgrade`` directly;
    this command exists for convenience during local development.
    """
    import subprocess
    import sys
    migrations_dir = os.path.join(os.path.dirname(__file__), 'migrations')

    if not os.path.isdir(migrations_dir):
        click.echo('ERROR: No migrations directory found.', err=True)
        click.echo('Run "flask db init" and "flask db migrate" to create one.', err=True)
        sys.exit(1)

    click.echo('Applying database migrations...')
    result = subprocess.run(
        ['flask', 'db', 'upgrade'],
        env={**os.environ, 'FLASK_APP': 'wsgi.py'},
        cwd=os.path.dirname(__file__),
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        click.echo('Migrations applied successfully.')
        if result.stdout.strip():
            click.echo(result.stdout)
    else:
        click.echo(f'ERROR: Migration failed.', err=True)
        click.echo(result.stderr, err=True)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Demo data seeder (dev/staging only)
# ---------------------------------------------------------------------------

def _seed_demo_data():
    """Seed demo customer, agents, calls, billing data for dev/staging."""
    from datetime import datetime, timezone, timedelta
    from werkzeug.security import generate_password_hash
    from app.models.core import (
        User, Tenant, Membership, Organization, Subscription,
        Agent, AgentConfig, AgentDraft, AgentVersion, HandoffRule,
        GuardrailRule, PhoneNumber, CallLog, PlanDefinition,
        Invoice, Payment, MinuteTopupPurchase, UsageRecord,
        Notification, ContactList, Contact, Campaign, CampaignTask,
        TenantToolConnection, AgentToolAssignment, ActionLog,
        ToolTemplate, DfyPackage, DfyProject, DfyMessage,
    )

    # Check if demo user already exists
    test_email = 'demo@agentgenie.com'
    if db.session.query(User).filter_by(email=test_email).first():
        click.echo('  Demo user already exists — skipping demo data.')
        return

    # Superadmin for DFY project owner
    admin_email = 'admin@platform.com'
    admin_user = db.session.query(User).filter_by(email=admin_email).first()
    if not admin_user:
        admin_user = User(email=admin_email, password_hash=generate_password_hash('admin123'), is_verified=True)
        db.session.add(admin_user)
        admin_tenant = Tenant(type='direct')
        db.session.add(admin_tenant)
        db.session.flush()
        db.session.add(Membership(user_id=admin_user.id, tenant_id=admin_tenant.id, role='superadmin'))
        click.echo(f'  Created superadmin: {admin_email} / admin123')

    growth_plan = db.session.query(PlanDefinition).filter_by(name='Growth').first()

    # Demo customer
    test_user = User(email=test_email, password_hash=generate_password_hash('demo123'), is_verified=True)
    db.session.add(test_user)
    test_tenant = Tenant(type='direct')
    db.session.add(test_tenant)
    db.session.flush()
    db.session.add(Membership(user_id=test_user.id, tenant_id=test_tenant.id, role='owner'))
    db.session.add(Organization(tenant_id=test_tenant.id, name='Sunrise Dental Group', industry='healthcare'))
    if growth_plan:
        db.session.add(Subscription(
            tenant_id=test_tenant.id, plan_id=growth_plan.id, status='active',
            current_period_start=datetime.now(timezone.utc) - timedelta(days=15),
            current_period_end=datetime.now(timezone.utc) + timedelta(days=15),
        ))
    db.session.flush()

    # Agents
    agent1 = Agent(tenant_id=test_tenant.id, name='Sunrise Dental Receptionist', retell_agent_id='agent_demo_active_001', status='active')
    db.session.add(agent1)
    db.session.flush()
    db.session.add(AgentConfig(
        agent_id=agent1.id,
        role_description='You are a friendly dental receptionist for Sunrise Dental Group.',
        tone='warm and professional',
        business_context={'text': 'Sunrise Dental Group dental practice.', 'retell_llm_id': 'llm_demo_001'},
        version=2,
    ))

    agent2 = Agent(tenant_id=test_tenant.id, name='After-Hours Answering Service', status='failed')
    db.session.add(agent2)
    db.session.flush()

    agent_ob = Agent(tenant_id=test_tenant.id, name='Appointment Reminder Caller', retell_agent_id='agent_demo_outbound_001', status='active', mode='outbound')
    db.session.add(agent_ob)
    db.session.flush()
    db.session.add(AgentConfig(
        agent_id=agent_ob.id,
        role_description='You are an outbound appointment reminder caller.',
        tone='friendly and concise',
        business_context={'text': 'Appointment reminders.', 'retell_llm_id': 'llm_demo_outbound_001'},
        version=1,
    ))

    agent3 = Agent(tenant_id=test_tenant.id, name='New Agent Draft', status='draft')
    db.session.add(agent3)
    db.session.flush()

    # Phone numbers
    db.session.add(PhoneNumber(
        tenant_id=test_tenant.id, number='+14155551234', retell_number_id='pn_demo_001',
        agent_id=agent1.id, status='active', area_code='415', friendly_name='Main Office Line',
        monthly_cost_cents=800, purchased_at=datetime.now(timezone.utc) - timedelta(days=30),
    ))
    db.session.add(PhoneNumber(
        tenant_id=test_tenant.id, number='+14155559876', retell_number_id='pn_demo_002',
        agent_id=None, status='unassigned', area_code='415', friendly_name='Backup Line',
        monthly_cost_cents=800, purchased_at=datetime.now(timezone.utc) - timedelta(days=10),
    ))

    # Call logs
    now = datetime.now(timezone.utc)
    call_data = [
        {'from': '+14085551111', 'dur': 245, 'status': 'completed', 'sent': 'positive'},
        {'from': '+14085552222', 'dur': 420, 'status': 'completed', 'sent': 'neutral'},
        {'from': '+14085553333', 'dur': 200, 'status': 'completed', 'sent': 'positive'},
        {'from': '+14085554444', 'dur': 150, 'status': 'completed', 'sent': 'negative'},
        {'from': '+14085555555', 'dur': 90, 'status': 'transferred', 'sent': 'neutral'},
        {'from': '+14085556666', 'dur': 60, 'status': 'voicemail', 'sent': None},
        {'from': '+14085557777', 'dur': 310, 'status': 'completed', 'sent': 'positive'},
        {'from': '+14085558888', 'dur': 0, 'status': 'no_answer', 'sent': None},
    ]
    for i, cd in enumerate(call_data):
        cl = CallLog(
            tenant_id=test_tenant.id, retell_call_id=f'call_demo_{i+1:03d}',
            agent_id=agent1.id, from_number=cd['from'], to_number='+14155551234',
            direction='inbound', duration_seconds=cd['dur'], status=cd['status'],
            sentiment=cd['sent'], started_at=now - timedelta(hours=i * 6),
            ended_at=now - timedelta(hours=i * 6) + timedelta(seconds=cd['dur']),
            summary=f'Demo call {i+1}',
        )
        db.session.add(cl)
    db.session.flush()

    # Tool connections and action log
    email_tmpl = db.session.query(ToolTemplate).filter_by(slug='email_send_summary').first()
    note_tmpl = db.session.query(ToolTemplate).filter_by(slug='note_call_summary').first()
    sms_tmpl = db.session.query(ToolTemplate).filter_by(slug='sms_send_followup').first()
    for tmpl in [email_tmpl, note_tmpl, sms_tmpl]:
        if tmpl:
            conn = TenantToolConnection(
                tenant_id=test_tenant.id, tool_template_id=tmpl.id,
                status='connected', connected_at=now, config={},
            )
            db.session.add(conn)
    db.session.flush()

    # DFY sample project
    inbound_pkg = db.session.query(DfyPackage).filter_by(slug='inbound-setup').first()
    if inbound_pkg:
        project = DfyProject(
            tenant_id=test_tenant.id, package_id=inbound_pkg.id,
            status='in_progress', owner_id=admin_user.id,
            description='Professional receptionist agent for appointment scheduling.',
            special_requirements='Must integrate with Dentrix PMS.',
            quoted_price_cents=49900, max_revisions=2,
            intake_form_data={'business_name': 'Sunrise Dental Group'},
            invoice_id='cs_demo_001',
            admin_notes='Client has Dentrix integration requirement.',
            target_delivery_date=datetime.now(timezone.utc).date() + timedelta(days=5),
        )
        db.session.add(project)
        db.session.flush()
        db.session.add(DfyMessage(project_id=project.id, sender_id=test_user.id, content='Excited to get started!'))
        db.session.add(DfyMessage(project_id=project.id, sender_id=admin_user.id, content='Welcome! Draft ready in 2 days.'))

    db.session.commit()
    click.echo(f'  Demo customer: {test_email} / demo123')
    click.echo(f'  4 agents, 2 phone numbers, 8 call logs, tool connections, DFY project')


if __name__ == '__main__':
    cli()
