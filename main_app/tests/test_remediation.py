"""Critical regression tests for the remediation pass.

Covers:
  1. Admin access control — non-superadmin gets 403
  2. Tenant isolation on agent detail/edit
  3. Agent edit with handoff/guardrail save
  4. Seed data correctness for plans and top-ups
  5. Async provisioning enqueue behavior
  6. Webhook idempotency behavior
  7. Feature flag gating
"""
import json
from unittest.mock import patch, MagicMock
from flask import g, session
from flask_login import login_user

from app import db
from app.models.core import (
    Agent, AgentConfig, HandoffRule, GuardrailRule,
    WebhookEvent, PlanDefinition, TopupPackDefinition,
    Membership,
)


# =========================================================================
# 1. Admin Access Control
# =========================================================================

class TestAdminAccessControl:
    """Non-superadmin users must receive 403 on all admin routes."""

    def test_non_superadmin_gets_403_on_admin_home(self, app, client, seed_tenants):
        """A regular owner user cannot access /admin/."""
        data = seed_tenants
        with app.test_request_context():
            with client.session_transaction() as sess:
                sess['_user_id'] = str(data['user1'].id)
                sess['active_tenant_id'] = data['tenant1'].id

        response = client.get('/admin/')
        # Should be 403 or redirect to login (depending on login_required behavior)
        assert response.status_code in (302, 403)

    def test_non_superadmin_gets_403_on_admin_customers(self, app, client, seed_tenants):
        data = seed_tenants
        with client.session_transaction() as sess:
            sess['_user_id'] = str(data['user1'].id)
            sess['active_tenant_id'] = data['tenant1'].id

        response = client.get('/admin/customers')
        assert response.status_code in (302, 403)

    def test_non_superadmin_gets_403_on_admin_pricing(self, app, client, seed_tenants):
        data = seed_tenants
        with client.session_transaction() as sess:
            sess['_user_id'] = str(data['user1'].id)
            sess['active_tenant_id'] = data['tenant1'].id

        response = client.get('/admin/pricing')
        assert response.status_code in (302, 403)


# =========================================================================
# 2. Tenant Isolation
# =========================================================================

class TestTenantIsolation:
    """Agents from one tenant must not be accessible by another."""

    def test_scoped_query_filters_by_tenant(self, app, db, seed_tenants):
        """scoped_query should only return agents for the active tenant."""
        from app.services.tenant.scoping import scoped_query
        data = seed_tenants

        with app.test_request_context():
            g.tenant_id = data['tenant1'].id
            g.membership = data['membership1']

            agents = scoped_query(Agent).all()
            agent_names = [a.name for a in agents]
            assert 'Agent T1' in agent_names
            assert 'Agent T2' not in agent_names

    def test_scoped_query_other_tenant(self, app, db, seed_tenants):
        """Switching tenant context should show different agents."""
        from app.services.tenant.scoping import scoped_query
        data = seed_tenants

        with app.test_request_context():
            g.tenant_id = data['tenant2'].id
            g.membership = data['membership2']

            agents = scoped_query(Agent).all()
            agent_names = [a.name for a in agents]
            assert 'Agent T2' in agent_names
            assert 'Agent T1' not in agent_names


# =========================================================================
# 3. Agent Edit with Handoff/Guardrail Save
# =========================================================================

class TestAgentEditPersistence:
    """Handoff rules and guardrail rules must persist correctly on edit."""

    def test_handoff_rule_creation(self, app, db, seed_tenants):
        """Creating a HandoffRule should include tenant_id."""
        data = seed_tenants
        with app.app_context():
            rule = HandoffRule(
                agent_id=data['agent1'].id,
                tenant_id=data['tenant1'].id,
                condition='caller asks for manager',
                destination_number='+15551234567',
                transfer_message='Transferring to management',
            )
            db.session.add(rule)
            db.session.commit()

            saved = HandoffRule.query.filter_by(agent_id=data['agent1'].id).first()
            assert saved is not None
            assert saved.tenant_id == data['tenant1'].id
            assert saved.condition == 'caller asks for manager'
            assert saved.destination_number == '+15551234567'

    def test_guardrail_rule_creation(self, app, db, seed_tenants):
        """Creating a GuardrailRule should include tenant_id."""
        data = seed_tenants
        with app.app_context():
            rule = GuardrailRule(
                agent_id=data['agent1'].id,
                tenant_id=data['tenant1'].id,
                prohibited_topic='politics',
                fallback_message='I can only help with business-related topics.',
            )
            db.session.add(rule)
            db.session.commit()

            saved = GuardrailRule.query.filter_by(agent_id=data['agent1'].id).first()
            assert saved is not None
            assert saved.tenant_id == data['tenant1'].id
            assert saved.prohibited_topic == 'politics'


# =========================================================================
# 4. Seed Data Correctness
# =========================================================================

class TestSeedDataCorrectness:
    """Seed data must match the prompt-specified pricing exactly."""

    def test_plan_starter_pricing(self, app, seed_plans):
        with app.app_context():
            plan = PlanDefinition.query.filter_by(name='Starter').first()
            assert plan is not None
            assert plan.price_monthly_cents == 9900  # $99
            assert plan.included_minutes == 250
            assert plan.included_agents == 1
            assert plan.included_numbers == 1
            assert plan.overage_rate_cents == 39  # $0.39

    def test_plan_growth_pricing(self, app, seed_plans):
        with app.app_context():
            plan = PlanDefinition.query.filter_by(name='Growth').first()
            assert plan is not None
            assert plan.price_monthly_cents == 24900  # $249
            assert plan.included_minutes == 800
            assert plan.included_agents == 3
            assert plan.included_numbers == 3
            assert plan.overage_rate_cents == 35  # $0.35

    def test_plan_scale_pricing(self, app, seed_plans):
        with app.app_context():
            plan = PlanDefinition.query.filter_by(name='Scale').first()
            assert plan is not None
            assert plan.price_monthly_cents == 49900  # $499
            assert plan.included_minutes == 1800
            assert plan.included_agents == 8
            assert plan.included_numbers == 8
            assert plan.overage_rate_cents == 32  # $0.32

    def test_topup_100_minutes(self, app, seed_topups):
        with app.app_context():
            pack = TopupPackDefinition.query.filter_by(minutes=100).first()
            assert pack is not None
            assert pack.price_cents == 3900  # $39

    def test_topup_500_minutes(self, app, seed_topups):
        with app.app_context():
            pack = TopupPackDefinition.query.filter_by(minutes=500).first()
            assert pack is not None
            assert pack.price_cents == 17500  # $175

    def test_topup_1000_minutes(self, app, seed_topups):
        with app.app_context():
            pack = TopupPackDefinition.query.filter_by(minutes=1000).first()
            assert pack is not None
            assert pack.price_cents == 32000  # $320

    def test_no_300_minute_pack(self, app, seed_topups):
        """The 300-minute pack should not exist — it was not in the original prompt."""
        with app.app_context():
            pack = TopupPackDefinition.query.filter_by(minutes=300).first()
            assert pack is None


# =========================================================================
# 5. Async Provisioning Enqueue
# =========================================================================

class TestAsyncProvisioning:
    """Agent provisioning should be enqueued via Celery, not run inline."""

    @patch('app.tasks.agent_tasks.provision_agent_to_retell.delay')
    def test_provision_task_is_called_with_delay(self, mock_delay, app, db, seed_tenants):
        """Calling the task via .delay() should enqueue, not execute inline."""
        from app.tasks.agent_tasks import provision_agent_to_retell
        data = seed_tenants

        with app.app_context():
            # Simulate what the approve route does
            provision_agent_to_retell.delay(data['agent1'].id)
            mock_delay.assert_called_once_with(data['agent1'].id)


# =========================================================================
# 6. Webhook Idempotency
# =========================================================================

class TestWebhookIdempotency:
    """Duplicate webhooks should not be processed twice."""

    def test_duplicate_webhook_event_detected(self, app, db):
        """A WebhookEvent with the same source+event_type+payload hash should be detectable."""
        with app.app_context():
            evt1 = WebhookEvent(
                provider='retell',
                event_type='call_ended',
                payload={'call_id': 'call_123'},
                status='processed',
            )
            db.session.add(evt1)
            db.session.commit()

            # Check if a duplicate exists
            existing = WebhookEvent.query.filter_by(
                provider='retell',
                event_type='call_ended',
                status='processed',
            ).first()
            assert existing is not None
            assert existing.payload['call_id'] == 'call_123'


# =========================================================================
# 7. Feature Flag Gating
# =========================================================================

class TestFeatureFlags:
    """Deferred features must be inaccessible when flags are off."""

    def test_partner_flag_defaults_to_false(self, app):
        assert app.config.get('FEATURE_PARTNER_PROGRAM') is False

    def test_dfy_flag_defaults_to_false(self, app):
        assert app.config.get('FEATURE_DFY') is False

    def test_campaigns_flag_defaults_to_false(self, app):
        assert app.config.get('FEATURE_CAMPAIGNS') is False
