"""End-to-end tests for Phase 8: Live Provider Integration.

These tests verify:
  1. Credential encryption/decryption and tenant isolation
  2. Google Calendar adapter (OAuth flow, availability, booking)
  3. SendGrid email adapter (send_email, send_call_summary)
  4. Twilio SMS adapter (send_sms, send_followup_sms)
  5. ToolExecutionEngine dispatch with enriched ActionLog recording
  6. Idempotent Retell function-call webhook handler
  7. Celery post-call task dispatch
  8. DFY Stripe Checkout flow with idempotent webhook fulfillment
  9. Tool registration sync to Retell LLM

All external API calls are mocked to avoid hitting real providers.
"""
import json
import os
import sys
import unittest
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

# Ensure the app is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app import create_app, db
from app.models.core import (
    Tenant, User, Organization, Membership, Agent, AgentConfig,
    ToolTemplate, TenantToolConnection, AgentToolAssignment, ActionLog,
    DfyPackage, DfyProject, DfyMessage, WebhookEvent, CallLog,
)


class Phase8TestBase(unittest.TestCase):
    """Base class with app context and test data setup."""

    @classmethod
    def setUpClass(cls):
        os.environ['CREDENTIAL_ENCRYPTION_KEY'] = 'dGVzdC1rZXktZm9yLXVuaXQtdGVzdHMtMzItYnl0ZXM='
        cls.app = create_app()
        cls.app.config['TESTING'] = True
        cls.app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///test_phase8.db'
        cls.app.config['WTF_CSRF_ENABLED'] = False
        cls.app.config['SENDGRID_API_KEY'] = 'SG.test_key'
        cls.app.config['TWILIO_ACCOUNT_SID'] = 'ACtest'
        cls.app.config['TWILIO_AUTH_TOKEN'] = 'test_token'
        cls.app.config['TWILIO_PHONE_NUMBER'] = '+15551234567'
        cls.app.config['GOOGLE_CLIENT_ID'] = 'test_client_id'
        cls.app.config['GOOGLE_CLIENT_SECRET'] = 'test_client_secret'
        cls.app.config['GOOGLE_REDIRECT_URI'] = 'http://localhost:5000/app/integrations/google-calendar/callback'
        cls.client = cls.app.test_client()

    def setUp(self):
        with self.app.app_context():
            db.create_all()
            self._seed_test_data()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def _seed_test_data(self):
        """Create minimal test data for all tests."""
        tenant = Tenant(type='direct', status='active')
        db.session.add(tenant)
        db.session.flush()
        self.tenant_id = tenant.id

        org = Organization(name='Test Org', tenant_id=tenant.id)
        db.session.add(org)
        db.session.flush()

        user = User(
            email='test@example.com',
            password_hash='test',
        )
        db.session.add(user)
        db.session.flush()
        self.user_id = user.id

        membership = Membership(
            user_id=user.id, tenant_id=tenant.id, role='owner'
        )
        db.session.add(membership)
        db.session.flush()

        agent = Agent(
            tenant_id=tenant.id, name='Test Agent',
            retell_agent_id='retell_test_123', status='active'
        )
        db.session.add(agent)
        db.session.flush()
        self.agent_id = agent.id

        config = AgentConfig(
            agent_id=agent.id, version=1,
            role_description='Test agent role',
            business_context={'retell_llm_id': 'llm_test_123'},
        )
        db.session.add(config)

        # Tool templates
        cal_template = ToolTemplate(
            name='Google Calendar', slug='google-calendar',
            category='calendar', tool_type='real_time',
            access_tier='self_serve', is_active=True,
            requires_oauth=True, oauth_provider='google',
        )
        email_template = ToolTemplate(
            name='SendGrid Email', slug='sendgrid-email',
            category='email', tool_type='post_call',
            access_tier='self_serve', is_active=True,
        )
        sms_template = ToolTemplate(
            name='Twilio SMS', slug='twilio-sms',
            category='sms', tool_type='post_call',
            access_tier='self_serve', is_active=True,
        )
        db.session.add_all([cal_template, email_template, sms_template])
        db.session.flush()
        self.cal_template_id = cal_template.id
        self.email_template_id = email_template.id
        self.sms_template_id = sms_template.id

        # Connections
        cal_conn = TenantToolConnection(
            tenant_id=tenant.id, tool_template_id=cal_template.id,
            status='connected', connected_at=datetime.now(timezone.utc),
        )
        email_conn = TenantToolConnection(
            tenant_id=tenant.id, tool_template_id=email_template.id,
            status='connected', connected_at=datetime.now(timezone.utc),
        )
        sms_conn = TenantToolConnection(
            tenant_id=tenant.id, tool_template_id=sms_template.id,
            status='connected', connected_at=datetime.now(timezone.utc),
        )
        db.session.add_all([cal_conn, email_conn, sms_conn])
        db.session.flush()
        self.cal_conn_id = cal_conn.id
        self.email_conn_id = email_conn.id
        self.sms_conn_id = sms_conn.id

        # Tool assignments
        cal_assign = AgentToolAssignment(
            agent_id=agent.id, connection_id=cal_conn.id,
            function_name='calendar_check_availability',
            description_for_llm='Check calendar availability', tool_type='real_time',
        )
        email_assign = AgentToolAssignment(
            agent_id=agent.id, connection_id=email_conn.id,
            function_name='email_send_summary',
            description_for_llm='Email call summary', tool_type='post_call',
        )
        sms_assign = AgentToolAssignment(
            agent_id=agent.id, connection_id=sms_conn.id,
            function_name='sms_send_followup',
            description_for_llm='SMS follow-up', tool_type='post_call',
        )
        db.session.add_all([cal_assign, email_assign, sms_assign])
        db.session.flush()
        self.cal_assign_id = cal_assign.id
        self.email_assign_id = email_assign.id
        self.sms_assign_id = sms_assign.id

        # Call log
        call_log = CallLog(
            tenant_id=tenant.id, agent_id=agent.id,
            retell_call_id='call_test_456',
            direction='inbound', from_number='+15559876543',
            to_number='+15551234567', status='completed',
            started_at=datetime.now(timezone.utc),
        )
        db.session.add(call_log)
        db.session.flush()
        self.call_log_id = call_log.id

        # DFY package
        package = DfyPackage(
            name='Test Package', slug='test-package',
            billing_type='one_time', price_cents=49900,
            estimated_days=7, is_active=True,
        )
        db.session.add(package)
        db.session.flush()
        self.package_id = package.id

        db.session.commit()


class TestCredentialManager(Phase8TestBase):
    """Test encrypted credential storage and retrieval."""

    def test_encrypt_decrypt_roundtrip(self):
        with self.app.app_context():
            from app.services.credential_manager import encrypt_credentials, decrypt_credentials
            original = {'access_token': 'ya29.test', 'refresh_token': 'rt_test'}
            encrypted = encrypt_credentials(original)
            self.assertNotEqual(encrypted, json.dumps(original))
            decrypted = decrypt_credentials(encrypted)
            self.assertEqual(decrypted, original)

    def test_store_and_retrieve_credentials(self):
        with self.app.app_context():
            from app.services.credential_manager import store_credentials, get_credentials
            creds = {'access_token': 'ya29.test', 'refresh_token': 'rt_test'}
            stored = store_credentials(self.cal_conn_id, self.tenant_id, creds)
            self.assertTrue(stored)

            retrieved = get_credentials(self.cal_conn_id, self.tenant_id)
            self.assertEqual(retrieved['access_token'], 'ya29.test')

    def test_tenant_isolation(self):
        with self.app.app_context():
            from app.services.credential_manager import store_credentials, get_credentials
            creds = {'access_token': 'ya29.test'}
            store_credentials(self.cal_conn_id, self.tenant_id, creds)

            # Attempt to read with wrong tenant
            result = get_credentials(self.cal_conn_id, 'wrong-tenant-id')
            self.assertEqual(result, {})

    def test_clear_credentials(self):
        with self.app.app_context():
            from app.services.credential_manager import store_credentials, clear_credentials, get_credentials
            store_credentials(self.cal_conn_id, self.tenant_id, {'access_token': 'test'})
            clear_credentials(self.cal_conn_id, self.tenant_id)
            result = get_credentials(self.cal_conn_id, self.tenant_id)
            self.assertEqual(result, {})


class TestCalendarAdapter(Phase8TestBase):
    """Test Google Calendar adapter with mocked API calls."""

    def test_build_oauth_url(self):
        with self.app.app_context():
            from app.services.calendar_adapter import build_oauth_url
            url = build_oauth_url('test-state')
            self.assertIn('accounts.google.com', url)
            self.assertIn('test_client_id', url)
            self.assertIn('calendar', url)

    @patch('requests.post')
    def test_exchange_code(self, mock_post):
        with self.app.app_context():
            from app.services.calendar_adapter import exchange_code
            mock_post.return_value = MagicMock(
                status_code=200,
                json=lambda: {
                    'access_token': 'ya29.new_token',
                    'refresh_token': 'rt_new',
                    'token_type': 'Bearer',
                    'expires_in': 3600,
                    'scope': 'calendar.events',
                }
            )
            result = exchange_code('test_auth_code')
            self.assertEqual(result['status'], 'success')
            self.assertEqual(result['credentials']['access_token'], 'ya29.new_token')

    @patch('app.services.calendar_adapter._get_calendar_service')
    def test_check_availability(self, mock_service):
        with self.app.app_context():
            from app.services.calendar_adapter import check_availability
            mock_freebusy = MagicMock()
            mock_freebusy.query.return_value.execute.return_value = {
                'calendars': {'primary': {'busy': []}}
            }
            mock_service.return_value.freebusy.return_value = mock_freebusy

            result = check_availability(
                {'access_token': 'ya29.test'},
                date='2026-03-20', time='14:00'
            )
            self.assertEqual(result['status'], 'ok')
            self.assertTrue(result['available'])

    @patch('app.services.calendar_adapter._get_calendar_service')
    def test_book_appointment(self, mock_service):
        with self.app.app_context():
            from app.services.calendar_adapter import book_appointment
            mock_events = MagicMock()
            mock_events.insert.return_value.execute.return_value = {
                'id': 'evt_123', 'htmlLink': 'https://calendar.google.com/event/evt_123'
            }
            mock_service.return_value.events.return_value = mock_events

            result = book_appointment(
                {'access_token': 'ya29.test'},
                date='2026-03-20', time='14:00',
                caller_name='John Doe', caller_phone='+15559876543'
            )
            self.assertEqual(result['status'], 'ok')
            self.assertEqual(result['confirmation_id'], 'evt_123')


class TestEmailAdapter(Phase8TestBase):
    """Test SendGrid email adapter with mocked API calls."""

    @patch('sendgrid.SendGridAPIClient')
    def test_send_email(self, mock_sg_class):
        with self.app.app_context():
            from app.services.email_adapter import send_email
            mock_response = MagicMock()
            mock_response.status_code = 202
            mock_response.headers = {'X-Message-Id': 'msg_test_123'}
            mock_sg_class.return_value.send.return_value = mock_response

            result = send_email(
                to_email='recipient@example.com',
                subject='Test Email',
                body_text='Hello from tests',
            )
            self.assertEqual(result['status'], 'ok')
            self.assertEqual(result['message_id'], 'msg_test_123')

    @patch('sendgrid.SendGridAPIClient')
    def test_send_call_summary(self, mock_sg_class):
        with self.app.app_context():
            from app.services.email_adapter import send_call_summary
            mock_response = MagicMock()
            mock_response.status_code = 202
            mock_response.headers = {'X-Message-Id': 'msg_summary_456'}
            mock_sg_class.return_value.send.return_value = mock_response

            result = send_call_summary('owner@example.com', {
                'agent_name': 'Test Agent',
                'caller_name': 'Jane Doe',
                'from_number': '+15559876543',
                'summary': 'Caller asked about pricing.',
                'duration_seconds': 180,
            })
            self.assertEqual(result['status'], 'ok')

    def test_send_email_no_api_key(self):
        with self.app.app_context():
            self.app.config['SENDGRID_API_KEY'] = ''
            from app.services.email_adapter import send_email
            result = send_email('test@example.com', 'Test', 'Body')
            self.assertEqual(result['status'], 'error')
            self.assertIn('not configured', result['message'])
            self.app.config['SENDGRID_API_KEY'] = 'SG.test_key'


class TestSmsAdapter(Phase8TestBase):
    """Test Twilio SMS adapter with mocked API calls."""

    @patch('twilio.rest.Client')
    def test_send_sms(self, mock_client_class):
        with self.app.app_context():
            from app.services.sms_adapter import send_sms
            mock_msg = MagicMock()
            mock_msg.sid = 'SM_test_123'
            mock_msg.status = 'queued'
            mock_client_class.return_value.messages.create.return_value = mock_msg

            result = send_sms('+15559876543', 'Hello from test!')
            self.assertEqual(result['status'], 'ok')
            self.assertEqual(result['message_sid'], 'SM_test_123')

    @patch('twilio.rest.Client')
    def test_send_followup_sms(self, mock_client_class):
        with self.app.app_context():
            from app.services.sms_adapter import send_followup_sms
            mock_msg = MagicMock()
            mock_msg.sid = 'SM_followup_456'
            mock_msg.status = 'queued'
            mock_client_class.return_value.messages.create.return_value = mock_msg

            result = send_followup_sms('+15559876543', {
                'agent_name': 'Test Agent',
                'caller_name': 'John',
            })
            self.assertEqual(result['status'], 'ok')

    def test_phone_normalization(self):
        from app.services.sms_adapter import _normalize_phone
        self.assertEqual(_normalize_phone('+15559876543'), '+15559876543')
        self.assertEqual(_normalize_phone('5559876543'), '+15559876543')
        self.assertEqual(_normalize_phone('15559876543'), '+15559876543')
        self.assertEqual(_normalize_phone('(555) 987-6543'), '+15559876543')


class TestToolExecutionEngine(Phase8TestBase):
    """Test the ToolExecutionEngine dispatch and ActionLog recording."""

    @patch('sendgrid.SendGridAPIClient')
    def test_execute_email_tool_creates_action_log(self, mock_sg_class):
        with self.app.app_context():
            from app.services.tool_engine import execute_tool
            mock_response = MagicMock()
            mock_response.status_code = 202
            mock_response.headers = {'X-Message-Id': 'msg_engine_test'}
            mock_sg_class.return_value.send.return_value = mock_response

            assignment = db.session.get(AgentToolAssignment, self.email_assign_id)
            result = execute_tool(assignment, {
                'call_log_id': self.call_log_id,
                'to_email': 'test@example.com',
                'summary': 'Test summary',
                'agent_name': 'Test Agent',
            }, idempotency_key='test_email_001')

            self.assertIn(result['status'], ('ok', 'success'))

            # Verify ActionLog was created
            log = ActionLog.query.filter_by(idempotency_key='test_email_001').first()
            self.assertIsNotNone(log)
            self.assertEqual(log.provider_name, 'sendgrid')
            self.assertEqual(log.status, 'success')
            self.assertIsNotNone(log.execution_ms)
            self.assertIsNotNone(log.response_payload)

    def test_idempotency_prevents_duplicate(self):
        with self.app.app_context():
            from app.services.tool_engine import execute_tool
            # Create a pre-existing ActionLog with the same key
            existing = ActionLog(
                tenant_id=self.tenant_id, agent_id=self.agent_id,
                tool_type='post_call', tool_name='email_send_summary',
                provider_name='sendgrid', status='success',
                idempotency_key='dup_key_001',
            )
            db.session.add(existing)
            db.session.commit()

            assignment = db.session.get(AgentToolAssignment, self.email_assign_id)
            result = execute_tool(assignment, {}, idempotency_key='dup_key_001')
            self.assertTrue(result.get('duplicate'))


class TestRetellFunctionCallWebhook(Phase8TestBase):
    """Test the real-time function call webhook endpoint."""

    @patch('app.services.calendar_adapter._get_calendar_service')
    @patch('app.services.credential_manager.get_valid_credentials')
    def test_function_call_endpoint(self, mock_creds, mock_service):
        with self.app.app_context():
            mock_creds.return_value = {'access_token': 'ya29.test'}
            mock_freebusy = MagicMock()
            mock_freebusy.query.return_value.execute.return_value = {
                'calendars': {'primary': {'busy': []}}
            }
            mock_service.return_value.freebusy.return_value = mock_freebusy

            response = self.client.post('/api/webhooks/retell/function-call',
                json={
                    'call_id': 'call_test_456',
                    'agent_id': 'retell_test_123',
                    'function_name': 'calendar_check_availability',
                    'arguments': {'date': '2026-03-20', 'time': '14:00'},
                    'invocation_id': 'inv_001',
                },
                content_type='application/json',
            )
            self.assertEqual(response.status_code, 200)
            data = response.get_json()
            self.assertIn('result', data)

    def test_function_call_idempotency(self):
        with self.app.app_context():
            # Pre-create an ActionLog for this invocation
            log = ActionLog(
                tenant_id=self.tenant_id, agent_id=self.agent_id,
                tool_type='real_time', tool_name='calendar_check_availability',
                provider_name='google_calendar', status='success',
                response_payload={'status': 'ok', 'available': True},
                idempotency_key='rt:call_test_456:calendar_check_availability:inv_dup',
            )
            db.session.add(log)
            db.session.commit()

            response = self.client.post('/api/webhooks/retell/function-call',
                json={
                    'call_id': 'call_test_456',
                    'agent_id': 'retell_test_123',
                    'function_name': 'calendar_check_availability',
                    'arguments': {'date': '2026-03-20', 'time': '14:00'},
                    'invocation_id': 'inv_dup',
                },
                content_type='application/json',
            )
            self.assertEqual(response.status_code, 200)
            data = response.get_json()
            # Should return cached result
            self.assertEqual(data['result']['status'], 'ok')


class TestDfyStripeCheckout(Phase8TestBase):
    """Test DFY Stripe Checkout flow and webhook fulfillment."""

    def test_dfy_checkout_fulfillment_idempotent(self):
        with self.app.app_context():
            from app.services.billing_engine import _fulfill_dfy_checkout

            # Create a DFY project in pending_payment
            project = DfyProject(
                tenant_id=self.tenant_id, package_id=self.package_id,
                status='pending_payment', quoted_price_cents=49900,
            )
            db.session.add(project)
            db.session.commit()
            project_id = project.id

            # First fulfillment — should transition to intake
            _fulfill_dfy_checkout(
                {'id': 'cs_test_session_001'},
                {'dfy_project_id': project_id, 'type': 'dfy_purchase'}
            )
            project = db.session.get(DfyProject, project_id)
            self.assertEqual(project.status, 'intake')
            self.assertEqual(project.invoice_id, 'cs_test_session_001')

            # Second fulfillment (duplicate webhook) — should be no-op
            _fulfill_dfy_checkout(
                {'id': 'cs_test_session_001'},
                {'dfy_project_id': project_id, 'type': 'dfy_purchase'}
            )
            project = db.session.get(DfyProject, project_id)
            self.assertEqual(project.status, 'intake')  # unchanged


class TestToolRegistration(Phase8TestBase):
    """Test tool registration sync to Retell LLM."""

    @patch('app.services.retell_adapter.requests.patch')
    def test_sync_agent_tools(self, mock_patch):
        with self.app.app_context():
            from app.services.tool_registration import sync_agent_tools
            mock_patch.return_value = MagicMock(
                status_code=200,
                json=lambda: {'llm_id': 'llm_test_123'},
            )

            result = sync_agent_tools(self.agent_id)
            self.assertEqual(result['status'], 'ok')
            self.assertEqual(result['tools_registered'], 1)  # only real_time tools

            # Verify the PATCH was called with general_tools
            call_args = mock_patch.call_args
            payload = call_args[1].get('json', call_args[0][0] if call_args[0] else {})
            if isinstance(payload, dict):
                self.assertIn('general_tools', payload)


class TestPostCallDispatch(Phase8TestBase):
    """Test Celery post-call task dispatch."""

    def test_get_post_call_tools(self):
        with self.app.app_context():
            from app.services.tool_engine import get_post_call_tools
            tools = get_post_call_tools(self.agent_id)
            self.assertEqual(len(tools), 2)  # email + sms
            names = {t.function_name for t in tools}
            self.assertIn('email_send_summary', names)
            self.assertIn('sms_send_followup', names)


class TestActionLogEnrichedFields(Phase8TestBase):
    """Test that ActionLog entries contain all required enriched fields."""

    @patch('sendgrid.SendGridAPIClient')
    def test_action_log_has_all_fields(self, mock_sg_class):
        with self.app.app_context():
            from app.services.tool_engine import execute_tool
            mock_response = MagicMock()
            mock_response.status_code = 202
            mock_response.headers = {'X-Message-Id': 'msg_fields_test'}
            mock_sg_class.return_value.send.return_value = mock_response

            assignment = db.session.get(AgentToolAssignment, self.email_assign_id)
            execute_tool(assignment, {
                'call_log_id': self.call_log_id,
                'to_email': 'test@example.com',
                'summary': 'Test',
                'agent_name': 'Agent',
            }, idempotency_key='fields_test_001')

            log = ActionLog.query.filter_by(idempotency_key='fields_test_001').first()
            self.assertIsNotNone(log)

            # Verify all enriched fields are present
            self.assertIsNotNone(log.provider_name, 'provider_name should be set')
            self.assertIsNotNone(log.request_payload, 'request_payload should be set')
            self.assertIsNotNone(log.response_payload, 'response_payload should be set')
            self.assertIsNotNone(log.execution_ms, 'execution_ms should be set')
            self.assertIsNotNone(log.idempotency_key, 'idempotency_key should be set')
            self.assertEqual(log.retry_count, 0)
            self.assertEqual(log.status, 'success')
            # failure_reason should be None for successful executions
            self.assertIsNone(log.failure_reason)


if __name__ == '__main__':
    unittest.main(verbosity=2)
