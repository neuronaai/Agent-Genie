"""Core database models for identity, tenancy, agents, billing, and operations."""
import uuid
from datetime import datetime, timezone

from flask_login import UserMixin
from sqlalchemy import (
    Column, String, Boolean, Integer, Text, DateTime, Date,
    ForeignKey, Enum as SAEnum, Index, UniqueConstraint, Numeric
)
from sqlalchemy import JSON

# Use real JSONB on PostgreSQL for index support; fall back to JSON on SQLite.
try:
    from sqlalchemy.dialects.postgresql import JSONB as _PG_JSONB
    import os
    _db_url = os.environ.get('DATABASE_URL', '')
    JSONB = _PG_JSONB if _db_url.startswith('postgres') else JSON
except ImportError:
    JSONB = JSON
from sqlalchemy.orm import relationship

from app import db


def gen_uuid():
    return str(uuid.uuid4())


def utcnow():
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------
TenantType = SAEnum('direct', 'partner_originated', name='tenant_type_enum', create_type=True)
TenantStatus = SAEnum('active', 'suspended', 'canceled', name='tenant_status_enum', create_type=True)
PartnerStatus = SAEnum('active', 'suspended', name='partner_status_enum', create_type=True)
MembershipRole = SAEnum('owner', 'admin', 'viewer', 'partner', 'superadmin', name='membership_role_enum', create_type=True)
AgentStatus = SAEnum('draft', 'pending', 'active', 'failed', 'needs_attention', name='agent_status_enum', create_type=True)
DraftStatus = SAEnum('pending_review', 'approved', 'rejected', name='draft_status_enum', create_type=True)
KBType = SAEnum('text', 'url', 'file', 'faq', 'service', 'discount', 'hours_location',
               'support_escalation', 'booking_link', 'handoff_instruction',
               name='kb_type_enum', create_type=True)
PhoneNumberStatus = SAEnum('active', 'unassigned', 'pending_provision', 'failed', name='phone_number_status_enum', create_type=True)
CallDirection = SAEnum('inbound', 'outbound', name='call_direction_enum', create_type=True)
SubscriptionStatus = SAEnum('active', 'past_due', 'canceled', 'trialing', name='subscription_status_enum', create_type=True)
InvoiceStatus = SAEnum('draft', 'open', 'paid', 'uncollectible', 'void', name='invoice_status_enum', create_type=True)
PaymentStatus = SAEnum('succeeded', 'failed', 'refunded', 'partially_refunded', 'disputed', name='payment_status_enum', create_type=True)
ReconciliationStatus = SAEnum('matched', 'adjusted', 'disputed', name='reconciliation_status_enum', create_type=True)
PricingRuleType = SAEnum('minute_overage', 'number_overage', 'custom_feature', name='pricing_rule_type_enum', create_type=True)
CostBasisProvider = SAEnum('retell', 'openai', 'infrastructure', name='cost_basis_provider_enum', create_type=True)
CostBasisUnit = SAEnum('minute', '1k_tokens', 'fixed_monthly', name='cost_basis_unit_enum', create_type=True)
LedgerSourceType = SAEnum('subscription', 'top_up', 'overage', 'setup_fee', 'refund', 'dispute', name='ledger_source_type_enum', create_type=True)
EligibilityStatus = SAEnum('pending', 'eligible', 'voided', 'settled', name='eligibility_status_enum', create_type=True)
SettlementStatus = SAEnum('pending', 'approved', 'paid', 'failed', name='settlement_status_enum', create_type=True)
WebhookStatus = SAEnum('pending', 'processed', 'failed', name='webhook_status_enum', create_type=True)
NotificationType = SAEnum('email', 'in_app', name='notification_type_enum', create_type=True)
NotificationStatus = SAEnum('pending', 'sent', 'failed', name='notification_status_enum', create_type=True)

# Tools & External Actions
ToolCategory = SAEnum('calendar', 'email', 'sms', 'crm_ticket', 'note_summary', 'custom_webhook', name='tool_category_enum', create_type=True)
ToolType = SAEnum('real_time', 'post_call', name='tool_type_enum', create_type=True)
ConnectionStatus = SAEnum('connected', 'disconnected', 'error', 'needs_reconnect', name='connection_status_enum', create_type=True)
ActionLogStatus = SAEnum('success', 'failed', 'retrying', name='action_log_status_enum', create_type=True)
AccessTier = SAEnum('self_serve', 'dfy_only', 'admin_approved', name='access_tier_enum', create_type=True)

# Outbound Calling
AgentMode = SAEnum('inbound', 'outbound', name='agent_mode_enum', create_type=True)
CampaignStatus = SAEnum('draft', 'scheduled', 'running', 'paused', 'completed', 'canceled', name='campaign_status_enum', create_type=True)
ContactStatus = SAEnum('active', 'opted_out', 'invalid_number', name='contact_status_enum', create_type=True)
TaskStatus = SAEnum('pending', 'queued', 'calling', 'completed', 'failed', 'skipped', name='task_status_enum', create_type=True)
TaskDisposition = SAEnum('completed', 'voicemail', 'no_answer', 'invalid_number', 'opted_out', 'error', name='task_disposition_enum', create_type=True)

# Done For You
DfyBillingType = SAEnum('one_time', 'recurring', 'custom_quote', name='dfy_billing_type_enum', create_type=True)
DfyProjectStatus = SAEnum('intake', 'pending_payment', 'in_progress', 'in_review', 'completed', 'canceled', name='dfy_project_status_enum', create_type=True)


# ---------------------------------------------------------------------------
# Core Identity & Tenancy
# ---------------------------------------------------------------------------
class Partner(db.Model):
    __tablename__ = 'partners'
    id = Column(String(36), primary_key=True, default=gen_uuid)
    name = Column(String(255), nullable=False)
    subdomain = Column(String(63), unique=True, nullable=False)
    status = Column(PartnerStatus, nullable=False, default='active')
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    branding = relationship('BrandingSetting', uselist=False, back_populates='partner')
    tenants = relationship('Tenant', back_populates='partner')

    __table_args__ = (
        Index('idx_partners_subdomain', 'subdomain'),
    )


class BrandingSetting(db.Model):
    __tablename__ = 'branding_settings'
    id = Column(String(36), primary_key=True, default=gen_uuid)
    partner_id = Column(String(36), ForeignKey('partners.id'), unique=True, nullable=False)
    logo_url = Column(String(512), nullable=True)
    display_name = Column(String(255), nullable=False)
    support_email = Column(String(255), nullable=False)
    primary_color = Column(String(7), default='#000000')
    accent_color = Column(String(7), nullable=True)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    partner = relationship('Partner', back_populates='branding')


class Tenant(db.Model):
    __tablename__ = 'tenants'
    id = Column(String(36), primary_key=True, default=gen_uuid)
    type = Column(TenantType, nullable=False, default='direct')
    partner_id = Column(String(36), ForeignKey('partners.id'), nullable=True)
    status = Column(TenantStatus, nullable=False, default='active')
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    partner = relationship('Partner', back_populates='tenants')
    organization = relationship('Organization', uselist=False, back_populates='tenant')
    memberships = relationship('Membership', back_populates='tenant')

    __table_args__ = (
        Index('idx_tenants_partner_id', 'partner_id'),
    )


class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id = Column(String(36), primary_key=True, default=gen_uuid)
    email = Column(String(255), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    is_verified = Column(Boolean, default=False)
    last_login_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    memberships = relationship('Membership', back_populates='user')

    __table_args__ = (
        Index('idx_users_email', 'email'),
    )


class Organization(db.Model):
    __tablename__ = 'organizations'
    id = Column(String(36), primary_key=True, default=gen_uuid)
    tenant_id = Column(String(36), ForeignKey('tenants.id'), unique=True, nullable=False)
    name = Column(String(255), nullable=False)
    industry = Column(String(255), nullable=True)
    timezone = Column(String(63), default='UTC')
    website = Column(String(512), nullable=True)
    support_email = Column(String(255), nullable=True)
    support_phone = Column(String(50), nullable=True)
    tenant_settings = Column(JSONB, nullable=True, default=dict)  # Per-tenant feature toggles (e.g. recordings_enabled)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    tenant = relationship('Tenant', back_populates='organization')


class Membership(db.Model):
    __tablename__ = 'memberships'
    id = Column(String(36), primary_key=True, default=gen_uuid)
    user_id = Column(String(36), ForeignKey('users.id'), nullable=False)
    tenant_id = Column(String(36), ForeignKey('tenants.id'), nullable=False)
    role = Column(MembershipRole, nullable=False, default='owner')
    created_at = Column(DateTime, default=utcnow)

    user = relationship('User', back_populates='memberships')
    tenant = relationship('Tenant', back_populates='memberships')

    __table_args__ = (
        UniqueConstraint('user_id', 'tenant_id', name='uq_memberships_user_tenant'),
        Index('idx_memberships_user_tenant', 'user_id', 'tenant_id'),
    )


# ---------------------------------------------------------------------------
# AI Agents & Workflows
# ---------------------------------------------------------------------------
class Agent(db.Model):
    __tablename__ = 'agents'
    id = Column(String(36), primary_key=True, default=gen_uuid)
    tenant_id = Column(String(36), ForeignKey('tenants.id'), nullable=False)
    name = Column(String(255), nullable=False)
    retell_agent_id = Column(String(255), unique=True, nullable=True)
    status = Column(AgentStatus, nullable=False, default='draft')
    mode = Column(AgentMode, nullable=False, default='inbound')
    language = Column(String(10), nullable=False, default='en-US')
    voice_id = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    config = relationship('AgentConfig', uselist=False, back_populates='agent')
    drafts = relationship('AgentDraft', back_populates='agent')
    versions = relationship('AgentVersion', back_populates='agent')
    knowledge_items = relationship('KnowledgeBaseItem', back_populates='agent')
    handoff_rules = relationship('HandoffRule', back_populates='agent')
    guardrail_rules = relationship('GuardrailRule', back_populates='agent')

    __table_args__ = (
        Index('idx_agents_tenant_id', 'tenant_id'),
    )


class AgentDraft(db.Model):
    __tablename__ = 'agent_drafts'
    id = Column(String(36), primary_key=True, default=gen_uuid)
    tenant_id = Column(String(36), ForeignKey('tenants.id'), nullable=False)
    agent_id = Column(String(36), ForeignKey('agents.id'), nullable=True)
    raw_prompt = Column(Text, nullable=False)
    generated_config = Column(JSONB, nullable=True)
    status = Column(DraftStatus, nullable=False, default='pending_review')
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    agent = relationship('Agent', back_populates='drafts')


class AgentConfig(db.Model):
    __tablename__ = 'agent_configs'
    id = Column(String(36), primary_key=True, default=gen_uuid)
    tenant_id = Column(String(36), ForeignKey('tenants.id'), nullable=True)
    agent_id = Column(String(36), ForeignKey('agents.id'), unique=True, nullable=False)
    role_description = Column(Text, nullable=False)
    tone = Column(String(63), default='professional')
    business_context = Column(JSONB, nullable=True)
    hours_of_operation = Column(JSONB, nullable=True)
    version = Column(Integer, default=1)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    agent = relationship('Agent', back_populates='config')

    __table_args__ = (
        Index('idx_agent_configs_tenant_id', 'tenant_id'),
    )


class AgentVersion(db.Model):
    __tablename__ = 'agent_versions'
    id = Column(String(36), primary_key=True, default=gen_uuid)
    tenant_id = Column(String(36), ForeignKey('tenants.id'), nullable=True)
    agent_id = Column(String(36), ForeignKey('agents.id'), nullable=False)
    version_number = Column(Integer, nullable=False)
    config_snapshot = Column(JSONB, nullable=False)
    retell_version_id = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=utcnow)

    agent = relationship('Agent', back_populates='versions')

    __table_args__ = (
        Index('idx_agent_versions_tenant_id', 'tenant_id'),
    )


class KnowledgeBaseItem(db.Model):
    __tablename__ = 'knowledge_base_items'
    id = Column(String(36), primary_key=True, default=gen_uuid)
    tenant_id = Column(String(36), ForeignKey('tenants.id'), nullable=False)
    agent_id = Column(String(36), ForeignKey('agents.id'), nullable=False)
    type = Column(KBType, nullable=False)
    title = Column(String(255), nullable=False)
    content = Column(Text, nullable=True)
    category = Column(String(127), nullable=True)  # e.g. 'general', 'pricing', 'hours'
    file_name = Column(String(255), nullable=True)
    file_path = Column(String(512), nullable=True)
    file_size = Column(Integer, nullable=True)
    file_mime = Column(String(127), nullable=True)
    url = Column(String(1024), nullable=True)
    retell_kb_id = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    agent = relationship('Agent', back_populates='knowledge_items')


class WorkflowDefinition(db.Model):
    __tablename__ = 'workflow_definitions'
    id = Column(String(36), primary_key=True, default=gen_uuid)
    tenant_id = Column(String(36), ForeignKey('tenants.id'), nullable=True)
    agent_id = Column(String(36), ForeignKey('agents.id'), nullable=False)
    name = Column(String(255), nullable=False)
    trigger_condition = Column(String(512), nullable=False)
    steps = Column(JSONB, nullable=False)
    created_at = Column(DateTime, default=utcnow)

    __table_args__ = (
        Index('idx_workflow_definitions_tenant_id', 'tenant_id'),
    )


class HandoffRule(db.Model):
    __tablename__ = 'handoff_rules'
    id = Column(String(36), primary_key=True, default=gen_uuid)
    tenant_id = Column(String(36), ForeignKey('tenants.id'), nullable=True)
    agent_id = Column(String(36), ForeignKey('agents.id'), nullable=False)
    condition = Column(String(512), nullable=False)
    destination_number = Column(String(20), nullable=True)
    transfer_message = Column(String(512), nullable=True)
    created_at = Column(DateTime, default=utcnow)

    agent = relationship('Agent', back_populates='handoff_rules')

    __table_args__ = (
        Index('idx_handoff_rules_tenant_id', 'tenant_id'),
    )


class GuardrailRule(db.Model):
    __tablename__ = 'guardrail_rules'
    id = Column(String(36), primary_key=True, default=gen_uuid)
    tenant_id = Column(String(36), ForeignKey('tenants.id'), nullable=True)
    agent_id = Column(String(36), ForeignKey('agents.id'), nullable=False)
    prohibited_topic = Column(String(512), nullable=False)
    fallback_message = Column(String(512), nullable=False, server_default='I cannot discuss that topic.')
    created_at = Column(DateTime, default=utcnow)

    agent = relationship('Agent', back_populates='guardrail_rules')

    __table_args__ = (
        Index('idx_guardrail_rules_tenant_id', 'tenant_id'),
    )


# ---------------------------------------------------------------------------
# Telephony & Usage
# ---------------------------------------------------------------------------
class PhoneNumber(db.Model):
    __tablename__ = 'phone_numbers'
    id = Column(String(36), primary_key=True, default=gen_uuid)
    tenant_id = Column(String(36), ForeignKey('tenants.id'), nullable=False)
    number = Column(String(20), nullable=False)
    retell_number_id = Column(String(255), unique=True, nullable=True)
    agent_id = Column(String(36), ForeignKey('agents.id'), nullable=True)
    status = Column(PhoneNumberStatus, nullable=False, default='pending_provision')
    area_code = Column(String(10), nullable=True)
    friendly_name = Column(String(255), nullable=True)
    monthly_cost_cents = Column(Integer, default=500)
    purchased_at = Column(DateTime, nullable=True)
    released_at = Column(DateTime, nullable=True)

    agent = relationship('Agent', backref='phone_numbers')

    __table_args__ = (
        Index('idx_phone_numbers_tenant_id', 'tenant_id'),
    )


class CallLog(db.Model):
    __tablename__ = 'call_logs'
    id = Column(String(36), primary_key=True, default=gen_uuid)
    tenant_id = Column(String(36), ForeignKey('tenants.id'), nullable=False)
    retell_call_id = Column(String(255), unique=True, nullable=False)
    agent_id = Column(String(36), ForeignKey('agents.id'), nullable=False)
    from_number = Column(String(20), nullable=False)
    to_number = Column(String(20), nullable=False)
    direction = Column(CallDirection, nullable=False)
    start_timestamp = Column(DateTime, nullable=True)
    end_timestamp = Column(DateTime, nullable=True)
    started_at = Column(DateTime, nullable=True)
    ended_at = Column(DateTime, nullable=True)
    duration_seconds = Column(Integer, nullable=True)
    status = Column(String(63), nullable=True)
    disconnection_reason = Column(String(255), nullable=True)
    transcript = Column(Text, nullable=True)
    sentiment = Column(String(63), nullable=True)
    summary = Column(Text, nullable=True)
    retell_cost = Column(Numeric(10, 4), nullable=True)
    recording_url = Column(String(512), nullable=True)
    created_at = Column(DateTime, default=utcnow)

    recording = relationship('RecordingMetadata', uselist=False, back_populates='call_log')
    agent = relationship('Agent', backref='call_logs')

    __table_args__ = (
        Index('idx_call_logs_tenant_id', 'tenant_id'),
        Index('idx_call_logs_agent_id', 'agent_id'),
    )


class RecordingMetadata(db.Model):
    __tablename__ = 'recording_metadata'
    id = Column(String(36), primary_key=True, default=gen_uuid)
    tenant_id = Column(String(36), ForeignKey('tenants.id'), nullable=True)
    call_log_id = Column(String(36), ForeignKey('call_logs.id'), unique=True, nullable=False)
    transcript = Column(Text, nullable=True)
    recording_url = Column(String(512), nullable=True)
    is_sensitive_redacted = Column(Boolean, default=False)
    call_analysis = Column(JSONB, nullable=True)
    created_at = Column(DateTime, default=utcnow)

    call_log = relationship('CallLog', back_populates='recording')

    __table_args__ = (
        Index('idx_recording_metadata_tenant_id', 'tenant_id'),
    )


# ---------------------------------------------------------------------------
# Billing, Metering & Ledger
# ---------------------------------------------------------------------------
class PlanDefinition(db.Model):
    __tablename__ = 'plan_definitions'
    id = Column(String(36), primary_key=True, default=gen_uuid)
    name = Column(String(63), nullable=False)
    stripe_product_id = Column(String(255), nullable=True)
    stripe_price_id = Column(String(255), nullable=True)
    price_monthly_cents = Column(Integer, nullable=False)
    included_minutes = Column(Integer, nullable=False)
    included_agents = Column(Integer, nullable=False, default=1)
    included_numbers = Column(Integer, nullable=False, default=1)
    overage_rate_cents = Column(Integer, nullable=False, default=39)
    additional_number_rate_cents = Column(Integer, nullable=False, default=500)
    is_active = Column(Boolean, default=True)
    sort_order = Column(Integer, default=0)
    created_at = Column(DateTime, default=utcnow)

    # Convenience aliases used by templates
    @property
    def price_cents(self):
        return self.price_monthly_cents

    @property
    def max_agents(self):
        return self.included_agents

    @property
    def included_phone_numbers(self):
        return self.included_numbers


class PricingRule(db.Model):
    __tablename__ = 'pricing_rules'
    id = Column(String(36), primary_key=True, default=gen_uuid)
    plan_id = Column(String(36), ForeignKey('plan_definitions.id'), nullable=False)
    rule_type = Column(PricingRuleType, nullable=False)
    unit_price_cents = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=utcnow)


class CostBasisSetting(db.Model):
    __tablename__ = 'cost_basis_settings'
    id = Column(String(36), primary_key=True, default=gen_uuid)
    provider = Column(CostBasisProvider, nullable=False)
    cost_per_unit_cents = Column(Numeric(10, 4), nullable=False)
    unit_type = Column(CostBasisUnit, nullable=False)
    effective_date = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=utcnow)


class Subscription(db.Model):
    __tablename__ = 'subscriptions'
    id = Column(String(36), primary_key=True, default=gen_uuid)
    tenant_id = Column(String(36), ForeignKey('tenants.id'), unique=True, nullable=False)
    plan_id = Column(String(36), ForeignKey('plan_definitions.id'), nullable=False)
    stripe_subscription_id = Column(String(255), unique=True, nullable=True)
    stripe_customer_id = Column(String(255), nullable=True)
    status = Column(SubscriptionStatus, nullable=False, default='active')
    current_period_start = Column(DateTime, nullable=True)
    current_period_end = Column(DateTime, nullable=True)
    cancel_at_period_end = Column(Boolean, default=False)
    payment_method_last4 = Column(String(4), nullable=True)
    payment_method_brand = Column(String(20), nullable=True)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    plan = relationship('PlanDefinition')


class Invoice(db.Model):
    __tablename__ = 'invoices'
    id = Column(String(36), primary_key=True, default=gen_uuid)
    tenant_id = Column(String(36), ForeignKey('tenants.id'), nullable=False)
    stripe_invoice_id = Column(String(255), unique=True, nullable=True)
    amount_due_cents = Column(Integer, nullable=False, default=0)
    amount_paid_cents = Column(Integer, nullable=False, default=0)
    status = Column(InvoiceStatus, nullable=False, default='draft')
    invoice_pdf_url = Column(String(512), nullable=True)
    created_at = Column(DateTime, default=utcnow)


class Payment(db.Model):
    __tablename__ = 'payments'
    id = Column(String(36), primary_key=True, default=gen_uuid)
    tenant_id = Column(String(36), ForeignKey('tenants.id'), nullable=False)
    invoice_id = Column(String(36), ForeignKey('invoices.id'), nullable=True)
    stripe_payment_intent_id = Column(String(255), unique=True, nullable=True)
    amount_cents = Column(Integer, nullable=False)
    status = Column(PaymentStatus, nullable=False, default='succeeded')
    created_at = Column(DateTime, default=utcnow)


class UsageRecord(db.Model):
    __tablename__ = 'usage_records'
    id = Column(String(36), primary_key=True, default=gen_uuid)
    tenant_id = Column(String(36), ForeignKey('tenants.id'), nullable=False)
    call_log_id = Column(String(36), ForeignKey('call_logs.id'), unique=True, nullable=False)
    provider_reported_seconds = Column(Integer, nullable=False)
    internally_billable_seconds = Column(Integer, nullable=False)
    reconciliation_status = Column(ReconciliationStatus, nullable=False, default='matched')
    adjustment_reason = Column(String(512), nullable=True)
    created_at = Column(DateTime, default=utcnow)

    __table_args__ = (
        Index('idx_usage_records_tenant_id', 'tenant_id'),
    )


class UsageSummary(db.Model):
    __tablename__ = 'usage_summaries'
    id = Column(String(36), primary_key=True, default=gen_uuid)
    tenant_id = Column(String(36), ForeignKey('tenants.id'), nullable=False)
    billing_period_start = Column(Date, nullable=False)
    billing_period_end = Column(Date, nullable=False)
    total_included_minutes_used = Column(Integer, default=0)
    total_topup_minutes_used = Column(Integer, default=0)
    total_overage_minutes = Column(Integer, default=0)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        UniqueConstraint('tenant_id', 'billing_period_start', name='uq_usage_summaries_tenant_period'),
        Index('idx_usage_summaries_tenant_period', 'tenant_id', 'billing_period_start'),
    )


class TopupPackDefinition(db.Model):
    __tablename__ = 'topup_pack_definitions'
    id = Column(String(36), primary_key=True, default=gen_uuid)
    label = Column(String(100), nullable=True)
    minutes = Column(Integer, nullable=False)
    price_cents = Column(Integer, nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=utcnow)


class MinuteTopupPurchase(db.Model):
    __tablename__ = 'minute_topup_purchases'
    id = Column(String(36), primary_key=True, default=gen_uuid)
    tenant_id = Column(String(36), ForeignKey('tenants.id'), nullable=False)
    payment_id = Column(String(36), ForeignKey('payments.id'), nullable=True)
    minutes_added = Column(Integer, nullable=False)
    minutes_remaining = Column(Integer, nullable=False)
    purchase_price_cents = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=utcnow)


class AdditionalNumberCharge(db.Model):
    __tablename__ = 'additional_number_charges'
    id = Column(String(36), primary_key=True, default=gen_uuid)
    tenant_id = Column(String(36), ForeignKey('tenants.id'), nullable=False)
    phone_number_id = Column(String(36), ForeignKey('phone_numbers.id'), nullable=False)
    invoice_id = Column(String(36), ForeignKey('invoices.id'), nullable=True)
    charge_amount_cents = Column(Integer, nullable=False)
    billing_period_start = Column(Date, nullable=False)
    billing_period_end = Column(Date, nullable=False)
    created_at = Column(DateTime, default=utcnow)


class RevenueLedgerEntry(db.Model):
    __tablename__ = 'revenue_ledger_entries'
    id = Column(String(36), primary_key=True, default=gen_uuid)
    tenant_id = Column(String(36), ForeignKey('tenants.id'), nullable=False)
    partner_id = Column(String(36), ForeignKey('partners.id'), nullable=False)
    payment_id = Column(String(36), ForeignKey('payments.id'), nullable=True)
    source_type = Column(LedgerSourceType, nullable=False)
    gross_amount_cents = Column(Integer, nullable=False)
    net_eligible_amount_cents = Column(Integer, nullable=False)
    platform_share_cents = Column(Integer, nullable=False)
    partner_share_cents = Column(Integer, nullable=False)
    eligibility_status = Column(EligibilityStatus, nullable=False, default='pending')
    eligible_on_date = Column(Date, nullable=True)
    settlement_id = Column(String(36), ForeignKey('partner_settlement_records.id'), nullable=True)
    created_at = Column(DateTime, default=utcnow)

    __table_args__ = (
        Index('idx_ledger_partner_status', 'partner_id', 'eligibility_status'),
    )


class PartnerSettlementRecord(db.Model):
    __tablename__ = 'partner_settlement_records'
    id = Column(String(36), primary_key=True, default=gen_uuid)
    partner_id = Column(String(36), ForeignKey('partners.id'), nullable=False)
    period_start = Column(Date, nullable=False)
    period_end = Column(Date, nullable=False)
    total_payout_cents = Column(Integer, nullable=False)
    status = Column(SettlementStatus, nullable=False, default='pending')
    paid_at = Column(DateTime, nullable=True)
    payout_reference_id = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


class PayoutLineItem(db.Model):
    __tablename__ = 'payout_line_items'
    id = Column(String(36), primary_key=True, default=gen_uuid)
    settlement_id = Column(String(36), ForeignKey('partner_settlement_records.id'), nullable=False)
    ledger_entry_id = Column(String(36), ForeignKey('revenue_ledger_entries.id'), unique=True, nullable=False)
    amount_cents = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=utcnow)


# ---------------------------------------------------------------------------
# Outbound Calling
# ---------------------------------------------------------------------------
class ContactList(db.Model):
    __tablename__ = 'contact_lists'
    id = Column(String(36), primary_key=True, default=gen_uuid)
    tenant_id = Column(String(36), ForeignKey('tenants.id'), nullable=False)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    contact_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    contacts = relationship('Contact', back_populates='contact_list', cascade='all, delete-orphan')
    campaigns = relationship('Campaign', back_populates='contact_list')

    __table_args__ = (
        Index('idx_contact_lists_tenant_id', 'tenant_id'),
    )


class Contact(db.Model):
    __tablename__ = 'contacts'
    id = Column(String(36), primary_key=True, default=gen_uuid)
    tenant_id = Column(String(36), ForeignKey('tenants.id'), nullable=False)
    contact_list_id = Column(String(36), ForeignKey('contact_lists.id'), nullable=False)
    phone_number = Column(String(20), nullable=False)  # E.164
    first_name = Column(String(100), nullable=True)
    last_name = Column(String(100), nullable=True)
    email = Column(String(255), nullable=True)
    timezone = Column(String(63), nullable=True)  # IANA timezone
    dynamic_data = Column(JSONB, nullable=True)  # Arbitrary CSV columns for LLM injection
    status = Column(ContactStatus, nullable=False, default='active')
    opted_out_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    contact_list = relationship('ContactList', back_populates='contacts')

    __table_args__ = (
        UniqueConstraint('contact_list_id', 'phone_number', name='uq_contacts_list_phone'),
        Index('idx_contacts_tenant_id', 'tenant_id'),
        Index('idx_contacts_phone_number', 'phone_number'),
    )


class Campaign(db.Model):
    __tablename__ = 'campaigns'
    id = Column(String(36), primary_key=True, default=gen_uuid)
    tenant_id = Column(String(36), ForeignKey('tenants.id'), nullable=False)
    name = Column(String(255), nullable=False)
    agent_id = Column(String(36), ForeignKey('agents.id'), nullable=False)
    contact_list_id = Column(String(36), ForeignKey('contact_lists.id'), nullable=False)
    caller_id_number_id = Column(String(36), ForeignKey('phone_numbers.id'), nullable=False)
    status = Column(CampaignStatus, nullable=False, default='draft')
    retell_batch_call_id = Column(String(255), nullable=True)
    # Scheduling
    scheduled_at = Column(DateTime, nullable=True)  # UTC trigger time
    window_start_min = Column(Integer, default=540)  # 9:00 AM local
    window_end_min = Column(Integer, default=1260)  # 9:00 PM local
    allowed_days = Column(JSONB, nullable=True)  # ["Monday","Tuesday",...]
    # Retry
    max_retries = Column(Integer, default=2)
    # Stats (denormalized for dashboard)
    total_tasks = Column(Integer, default=0)
    completed_tasks = Column(Integer, default=0)
    failed_tasks = Column(Integer, default=0)
    # Timestamps
    launched_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    agent = relationship('Agent')
    contact_list = relationship('ContactList', back_populates='campaigns')
    caller_id_number = relationship('PhoneNumber')
    tasks = relationship('CampaignTask', back_populates='campaign', cascade='all, delete-orphan')

    __table_args__ = (
        Index('idx_campaigns_tenant_id', 'tenant_id'),
        Index('idx_campaigns_status', 'status'),
    )


class CampaignTask(db.Model):
    __tablename__ = 'campaign_tasks'
    id = Column(String(36), primary_key=True, default=gen_uuid)
    campaign_id = Column(String(36), ForeignKey('campaigns.id'), nullable=False)
    contact_id = Column(String(36), ForeignKey('contacts.id'), nullable=False)
    status = Column(TaskStatus, nullable=False, default='pending')
    disposition = Column(TaskDisposition, nullable=True)
    retry_count = Column(Integer, default=0)
    call_log_id = Column(String(36), ForeignKey('call_logs.id'), nullable=True)
    retell_task_id = Column(String(255), nullable=True)
    error_message = Column(Text, nullable=True)
    last_attempted_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    campaign = relationship('Campaign', back_populates='tasks')
    contact = relationship('Contact')
    call_log = relationship('CallLog')

    __table_args__ = (
        Index('idx_campaign_tasks_campaign_id', 'campaign_id'),
        Index('idx_campaign_tasks_status', 'status'),
    )


# ---------------------------------------------------------------------------
# Tools & External Actions
# ---------------------------------------------------------------------------
class ToolTemplate(db.Model):
    """Pre-built tool definitions in the platform catalog."""
    __tablename__ = 'tool_templates'
    id = Column(String(36), primary_key=True, default=gen_uuid)
    slug = Column(String(100), unique=True, nullable=False)  # e.g. 'google_calendar'
    name = Column(String(255), nullable=False)
    category = Column(ToolCategory, nullable=False)
    tool_type = Column(ToolType, nullable=False)
    access_tier = Column(AccessTier, nullable=False, default='self_serve')
    description = Column(Text, nullable=True)
    icon_class = Column(String(100), nullable=True)  # CSS class for UI icon
    default_parameters_schema = Column(JSONB, nullable=True)
    default_description_for_llm = Column(Text, nullable=True)
    requires_oauth = Column(Boolean, default=False)
    oauth_provider = Column(String(50), nullable=True)  # 'google', 'microsoft', 'hubspot'
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=utcnow)


class TenantToolConnection(db.Model):
    """Tenant-level connection/credentials for a tool template."""
    __tablename__ = 'tenant_tool_connections'
    id = Column(String(36), primary_key=True, default=gen_uuid)
    tenant_id = Column(String(36), ForeignKey('tenants.id'), nullable=False)
    tool_template_id = Column(String(36), ForeignKey('tool_templates.id'), nullable=False)
    status = Column(ConnectionStatus, nullable=False, default='disconnected')
    credential_mode = Column(String(20), nullable=False, default='platform')  # 'platform' | 'tenant'
    credentials_encrypted = Column(Text, nullable=True)  # Fernet-encrypted JSON
    config = Column(JSONB, nullable=True)  # Tenant-specific settings
    connected_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    template = relationship('ToolTemplate')

    __table_args__ = (
        UniqueConstraint('tenant_id', 'tool_template_id', name='uq_tenant_tool_connection'),
        Index('idx_tenant_tool_connections_tenant', 'tenant_id'),
    )


class AgentToolAssignment(db.Model):
    """Maps a connected tool to a specific agent."""
    __tablename__ = 'agent_tool_assignments'
    id = Column(String(36), primary_key=True, default=gen_uuid)
    agent_id = Column(String(36), ForeignKey('agents.id'), nullable=False)
    connection_id = Column(String(36), ForeignKey('tenant_tool_connections.id'), nullable=False)
    tool_type = Column(ToolType, nullable=False)
    function_name = Column(String(100), nullable=False)  # LLM function name
    description_for_llm = Column(Text, nullable=True)
    parameters_schema = Column(JSONB, nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=utcnow)

    agent = relationship('Agent')
    connection = relationship('TenantToolConnection')

    __table_args__ = (
        UniqueConstraint('agent_id', 'function_name', name='uq_agent_tool_function'),
        Index('idx_agent_tool_assignments_agent', 'agent_id'),
    )


class ActionLog(db.Model):
    """Records every tool/action execution for debugging and billing."""
    __tablename__ = 'action_logs'
    id = Column(String(36), primary_key=True, default=gen_uuid)
    tenant_id = Column(String(36), ForeignKey('tenants.id'), nullable=False)
    agent_id = Column(String(36), ForeignKey('agents.id'), nullable=False)
    call_log_id = Column(String(36), ForeignKey('call_logs.id'), nullable=True)
    assignment_id = Column(String(36), ForeignKey('agent_tool_assignments.id'), nullable=True)
    tool_type = Column(ToolType, nullable=False)
    tool_name = Column(String(100), nullable=False)
    provider_name = Column(String(50), nullable=True)    # e.g. 'google_calendar', 'sendgrid', 'twilio'
    status = Column(ActionLogStatus, nullable=False, default='success')
    request_payload = Column(JSONB, nullable=True)
    response_payload = Column(JSONB, nullable=True)
    error_message = Column(Text, nullable=True)
    failure_reason = Column(String(100), nullable=True)  # e.g. 'timeout', 'auth_expired', 'rate_limit', 'provider_error'
    execution_ms = Column(Integer, nullable=True)
    retry_count = Column(Integer, default=0)
    credential_source = Column(String(20), nullable=True)  # 'platform' | 'tenant' | 'oauth'
    idempotency_key = Column(String(255), nullable=True, unique=True)  # prevent duplicate executions
    created_at = Column(DateTime, default=utcnow)

    agent = relationship('Agent')

    __table_args__ = (
        Index('idx_action_logs_tenant', 'tenant_id'),
        Index('idx_action_logs_agent', 'agent_id'),
        Index('idx_action_logs_call', 'call_log_id'),
        Index('idx_action_logs_idempotency', 'idempotency_key'),
    )


# ---------------------------------------------------------------------------
# System & Operations
# ---------------------------------------------------------------------------
class PlatformSetting(db.Model):
    __tablename__ = 'platform_settings'
    id = Column(String(36), primary_key=True, default=gen_uuid)
    key = Column(String(255), unique=True, nullable=False)
    value = Column(JSONB, nullable=True)
    description = Column(String(512), nullable=True)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


class WebhookEvent(db.Model):
    __tablename__ = 'webhook_events'
    id = Column(String(36), primary_key=True, default=gen_uuid)
    provider = Column(String(63), nullable=False)
    event_type = Column(String(127), nullable=False)
    payload = Column(JSONB, nullable=False)
    idempotency_key = Column(String(255), nullable=True)
    status = Column(WebhookStatus, nullable=False, default='pending')
    error_message = Column(Text, nullable=True)
    retry_count = Column(Integer, default=0)
    processed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


class AuditLog(db.Model):
    __tablename__ = 'audit_logs'
    id = Column(String(36), primary_key=True, default=gen_uuid)
    tenant_id = Column(String(36), ForeignKey('tenants.id'), nullable=True)
    user_id = Column(String(36), ForeignKey('users.id'), nullable=True)
    action = Column(String(255), nullable=False)
    resource_type = Column(String(127), nullable=True)
    resource_id = Column(String(36), nullable=True)
    ip_address = Column(String(45), nullable=True)
    details = Column(JSONB, nullable=True)
    created_at = Column(DateTime, default=utcnow)


class Notification(db.Model):
    __tablename__ = 'notifications'
    id = Column(String(36), primary_key=True, default=gen_uuid)
    tenant_id = Column(String(36), ForeignKey('tenants.id'), nullable=False)
    user_id = Column(String(36), ForeignKey('users.id'), nullable=True)
    type = Column(NotificationType, nullable=False, default='in_app')
    # Email-oriented fields
    subject = Column(String(255), nullable=True)
    body = Column(Text, nullable=True)
    # In-app notification fields (used by the dashboard UI)
    title = Column(String(255), nullable=True)
    message = Column(Text, nullable=True)
    link = Column(String(512), nullable=True)
    is_read = Column(Boolean, default=False, nullable=False)
    # Status tracking
    status = Column(NotificationStatus, nullable=False, default='pending')
    created_at = Column(DateTime, default=utcnow)
    sent_at = Column(DateTime, nullable=True)


class SupportNote(db.Model):
    __tablename__ = 'support_notes'
    id = Column(String(36), primary_key=True, default=gen_uuid)
    tenant_id = Column(String(36), ForeignKey('tenants.id'), nullable=False)
    admin_user_id = Column(String(36), ForeignKey('users.id'), nullable=False)
    note = Column(Text, nullable=False)
    created_at = Column(DateTime, default=utcnow)


# ---------------------------------------------------------------------------
# Done For You (DFY) Service Layer
# ---------------------------------------------------------------------------
class DfyPackage(db.Model):
    """Defines a purchasable service package in the DFY catalog."""
    __tablename__ = 'dfy_packages'
    id = Column(String(36), primary_key=True, default=gen_uuid)
    name = Column(String(255), nullable=False)
    slug = Column(String(127), unique=True, nullable=False)
    description = Column(Text, nullable=True)
    features = Column(JSONB, nullable=True)          # list of feature strings
    price_cents = Column(Integer, nullable=True)      # null = custom quote
    billing_type = Column(DfyBillingType, nullable=False, default='one_time')
    stripe_price_id = Column(String(255), nullable=True)
    estimated_days = Column(Integer, nullable=True)   # SLA: expected delivery days
    is_active = Column(Boolean, default=True)
    sort_order = Column(Integer, default=0)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    projects = relationship('DfyProject', backref='package', lazy='dynamic')


class DfyProject(db.Model):
    """A purchased or requested DFY service engagement."""
    __tablename__ = 'dfy_projects'
    id = Column(String(36), primary_key=True, default=gen_uuid)
    tenant_id = Column(String(36), ForeignKey('tenants.id'), nullable=False)
    package_id = Column(String(36), ForeignKey('dfy_packages.id'), nullable=False)
    agent_id = Column(String(36), ForeignKey('agents.id'), nullable=True)
    status = Column(DfyProjectStatus, nullable=False, default='intake')
    owner_id = Column(String(36), ForeignKey('users.id'), nullable=True)  # admin assigned
    intake_form_data = Column(JSONB, nullable=True)
    description = Column(Text, nullable=True)           # customer-facing description
    special_requirements = Column(Text, nullable=True)  # customer special requirements
    quoted_price_cents = Column(Integer, nullable=True)  # quoted price for this project
    max_revisions = Column(Integer, default=2)           # max allowed revisions
    invoice_id = Column(String(255), nullable=True)   # Stripe invoice/checkout ID
    admin_notes = Column(Text, nullable=True)          # internal-only admin notes
    target_delivery_date = Column(Date, nullable=True) # SLA target
    revision_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    tenant = relationship('Tenant', foreign_keys=[tenant_id])
    owner = relationship('User', foreign_keys=[owner_id])
    messages = relationship('DfyMessage', backref='project', lazy='dynamic',
                            order_by='DfyMessage.created_at')


class DfyMessage(db.Model):
    """Threaded communication within a DFY project."""
    __tablename__ = 'dfy_messages'
    id = Column(String(36), primary_key=True, default=gen_uuid)
    project_id = Column(String(36), ForeignKey('dfy_projects.id'), nullable=False)
    sender_id = Column(String(36), ForeignKey('users.id'), nullable=False)
    content = Column(Text, nullable=False)
    attachments = Column(JSONB, nullable=True)         # list of file URLs
    is_revision_request = Column(Boolean, default=False)
    is_admin_note = Column(Boolean, default=False)     # internal-only, hidden from tenant
    created_at = Column(DateTime, default=utcnow)
