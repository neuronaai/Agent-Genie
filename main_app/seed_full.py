"""Full seed script: plans, superadmin, test customer with agents, calls, numbers."""
import json
from datetime import datetime, timezone, timedelta
from werkzeug.security import generate_password_hash
from app import create_app, db
from app.models.core import (
    PlanDefinition, PlatformSetting, User, Tenant, Membership,
    Organization, Subscription, Agent, AgentConfig, AgentDraft,
    AgentVersion, HandoffRule, GuardrailRule, PhoneNumber, CallLog,
    TopupPackDefinition, MinuteTopupPurchase, Invoice, Payment,
    UsageRecord, Notification, ContactList, Contact, Campaign, CampaignTask,
    ToolTemplate, TenantToolConnection, AgentToolAssignment, ActionLog,
    DfyPackage, DfyProject, DfyMessage,
)


def seed():
    app = create_app()
    with app.app_context():
        # NOTE: Tables must already exist via 'flask db upgrade'.
        # Do NOT use db.create_all() — migrations are the single source of truth.

        # ── Plans ──
        if db.session.query(PlanDefinition).count() == 0:
            plans = [
                PlanDefinition(
                    name='Starter', price_monthly_cents=9900,
                    included_minutes=250, included_agents=1, included_numbers=1,
                    overage_rate_cents=39, additional_number_rate_cents=900,
                ),
                PlanDefinition(
                    name='Growth', price_monthly_cents=24900,
                    included_minutes=800, included_agents=3, included_numbers=3,
                    overage_rate_cents=35, additional_number_rate_cents=800,
                ),
                PlanDefinition(
                    name='Scale', price_monthly_cents=49900,
                    included_minutes=1800, included_agents=8, included_numbers=8,
                    overage_rate_cents=32, additional_number_rate_cents=700,
                ),
            ]
            db.session.add_all(plans)
            db.session.flush()
            print('Seeded 3 plans.')

        growth_plan = db.session.query(PlanDefinition).filter_by(name='Growth').first()

        # ── Superadmin ──
        admin_email = 'admin@platform.com'
        if not db.session.query(User).filter_by(email=admin_email).first():
            admin_user = User(email=admin_email, password_hash=generate_password_hash('admin123'), is_verified=True)
            db.session.add(admin_user)
            admin_tenant = Tenant(type='direct')
            db.session.add(admin_tenant)
            db.session.flush()
            db.session.add(Membership(user_id=admin_user.id, tenant_id=admin_tenant.id, role='superadmin'))
            print(f'Seeded superadmin: {admin_email} / admin123')

        # ── Test Customer ──
        test_email = 'demo@agentgenie.com'
        if not db.session.query(User).filter_by(email=test_email).first():
            test_user = User(email=test_email, password_hash=generate_password_hash('demo123'), is_verified=True)
            db.session.add(test_user)
            test_tenant = Tenant(type='direct')
            db.session.add(test_tenant)
            db.session.flush()
            db.session.add(Membership(user_id=test_user.id, tenant_id=test_tenant.id, role='owner'))
            db.session.add(Organization(tenant_id=test_tenant.id, name='Sunrise Dental Group', industry='healthcare'))
            db.session.add(Subscription(tenant_id=test_tenant.id, plan_id=growth_plan.id, status='active',
                                        current_period_start=datetime.now(timezone.utc) - timedelta(days=15),
                                        current_period_end=datetime.now(timezone.utc) + timedelta(days=15)))
            db.session.flush()

            # ── Active Agent (already provisioned) ──
            agent1 = Agent(
                tenant_id=test_tenant.id,
                name='Sunrise Dental Receptionist',
                retell_agent_id='agent_demo_active_001',
                status='active',
            )
            db.session.add(agent1)
            db.session.flush()

            config1 = AgentConfig(
                agent_id=agent1.id,
                role_description='You are a friendly and professional dental receptionist for Sunrise Dental Group. You handle appointment scheduling, answer questions about services, and provide general office information. You are empathetic and patient with callers.',
                tone='warm and professional',
                business_context={
                    'text': 'Sunrise Dental Group is a multi-location dental practice offering general dentistry, cosmetic procedures, and orthodontics. We serve families in the greater metro area.',
                    'greeting_message': 'Thank you for calling Sunrise Dental Group! How can I help you today?',
                    'retell_llm_id': 'llm_demo_001',
                },
                version=2,
            )
            db.session.add(config1)

            # Handoff rules
            db.session.add(HandoffRule(agent_id=agent1.id, condition='Caller requests to speak with a dentist or doctor', destination_number='+15551234567', transfer_message='Let me connect you with our dental team right away.'))
            db.session.add(HandoffRule(agent_id=agent1.id, condition='Caller has a dental emergency', destination_number='+15559876543', transfer_message='I\'m transferring you to our emergency line immediately.'))
            db.session.add(HandoffRule(agent_id=agent1.id, condition='Caller asks about billing or insurance', destination_number='+15555551234', transfer_message='Let me connect you with our billing department.'))

            # Guardrails
            db.session.add(GuardrailRule(agent_id=agent1.id, prohibited_topic='Providing medical or dental diagnosis', fallback_message='I\'m not able to provide medical diagnoses. Let me connect you with one of our dentists who can help.'))
            db.session.add(GuardrailRule(agent_id=agent1.id, prohibited_topic='Recommending specific medications', fallback_message='I cannot recommend medications. Our dental team can discuss treatment options during your appointment.'))
            db.session.add(GuardrailRule(agent_id=agent1.id, prohibited_topic='Discussing other patients\' information', fallback_message='I\'m sorry, I cannot share information about other patients due to privacy regulations.'))

            # Version history
            db.session.add(AgentVersion(agent_id=agent1.id, version_number=1, config_snapshot={'agent_name': 'Sunrise Dental Receptionist', 'tone': 'professional'}, retell_version_id='agent_demo_active_001'))
            db.session.add(AgentVersion(agent_id=agent1.id, version_number=2, config_snapshot={'agent_name': 'Sunrise Dental Receptionist', 'tone': 'warm and professional'}, retell_version_id='agent_demo_active_001'))

            # ── Failed Agent (for retry demo) ──
            agent2 = Agent(
                tenant_id=test_tenant.id,
                name='After-Hours Answering Service',
                status='failed',
            )
            db.session.add(agent2)
            db.session.flush()

            draft2 = AgentDraft(
                tenant_id=test_tenant.id,
                agent_id=agent2.id,
                raw_prompt='Create an after-hours answering service for our dental practice. It should take messages and handle emergency calls.',
                generated_config={
                    'agent_name': 'After-Hours Answering Service',
                    'detected_industry': 'healthcare',
                    'role_description': 'You are an after-hours answering service for Sunrise Dental Group.',
                    'tone': 'calm and reassuring',
                    'business_context': 'Handles calls outside business hours.',
                    'greeting_message': 'Thank you for calling Sunrise Dental Group. Our office is currently closed.',
                    'handoff_rules': [{'condition': 'Dental emergency', 'destination_number': '+15559876543', 'transfer_message': 'Connecting you to our emergency line.'}],
                    'guardrails': [{'prohibited_topic': 'Medical diagnosis', 'fallback_message': 'Please visit our office during business hours for medical advice.'}],
                    'missing_information': [],
                    'contradictions': [],
                },
                status='approved',
            )
            db.session.add(draft2)

            # ── Outbound Agent ──
            agent_outbound = Agent(
                tenant_id=test_tenant.id,
                name='Appointment Reminder Caller',
                retell_agent_id='agent_demo_outbound_001',
                status='active',
                mode='outbound',
            )
            db.session.add(agent_outbound)
            db.session.flush()

            config_ob = AgentConfig(
                agent_id=agent_outbound.id,
                role_description='You are an outbound appointment reminder caller for Sunrise Dental Group. You call patients to remind them of upcoming appointments, confirm attendance, and offer rescheduling if needed. Be friendly, concise, and professional.',
                tone='friendly and concise',
                business_context={
                    'text': 'Sunrise Dental Group appointment reminder service.',
                    'greeting_message': 'Hi {{first_name}}, this is a courtesy call from Sunrise Dental Group regarding your upcoming appointment.',
                    'retell_llm_id': 'llm_demo_outbound_001',
                },
                version=1,
            )
            db.session.add(config_ob)

            # ── Draft Agent (for review demo) ──
            agent3 = Agent(
                tenant_id=test_tenant.id,
                name='New Agent Draft',
                status='draft',
            )
            db.session.add(agent3)
            db.session.flush()

            # ── Phone Numbers ──
            pn1 = PhoneNumber(
                tenant_id=test_tenant.id,
                number='+14155551234',
                retell_number_id='pn_demo_001',
                agent_id=agent1.id,
                status='active',
                area_code='415',
                friendly_name='Main Office Line',
                monthly_cost_cents=800,
                purchased_at=datetime.now(timezone.utc) - timedelta(days=30),
            )
            db.session.add(pn1)

            pn2 = PhoneNumber(
                tenant_id=test_tenant.id,
                number='+14155559876',
                retell_number_id='pn_demo_002',
                agent_id=None,
                status='unassigned',
                area_code='415',
                friendly_name='Backup Line',
                monthly_cost_cents=800,
                purchased_at=datetime.now(timezone.utc) - timedelta(days=10),
            )
            db.session.add(pn2)

            # ── Call Logs ──
            now = datetime.now(timezone.utc)
            call_data = [
                {'from': '+14085551111', 'dur': 245, 'status': 'completed', 'sentiment': 'positive', 'summary': 'Patient called to schedule a routine cleaning appointment for next Tuesday.', 'hours_ago': 2},
                {'from': '+14085552222', 'dur': 180, 'status': 'completed', 'sentiment': 'neutral', 'summary': 'Caller inquired about dental insurance coverage and accepted plans.', 'hours_ago': 5},
                {'from': '+14085553333', 'dur': 320, 'status': 'completed', 'sentiment': 'positive', 'summary': 'New patient called to learn about cosmetic dentistry options. Scheduled consultation.', 'hours_ago': 8},
                {'from': '+14085554444', 'dur': 90, 'status': 'transferred', 'sentiment': 'urgent', 'summary': 'Patient reported severe tooth pain. Transferred to emergency line.', 'hours_ago': 12},
                {'from': '+14085555555', 'dur': 150, 'status': 'completed', 'sentiment': 'positive', 'summary': 'Existing patient rescheduled their orthodontic follow-up appointment.', 'hours_ago': 24},
                {'from': '+14085556666', 'dur': 200, 'status': 'completed', 'sentiment': 'neutral', 'summary': 'Caller asked about office hours and location directions.', 'hours_ago': 36},
                {'from': '+14085557777', 'dur': 420, 'status': 'completed', 'sentiment': 'positive', 'summary': 'Detailed inquiry about teeth whitening procedures and pricing. Booked appointment.', 'hours_ago': 48},
                {'from': '+14085558888', 'dur': 60, 'status': 'voicemail', 'sentiment': None, 'summary': 'Caller left a voicemail requesting a callback about their upcoming appointment.', 'hours_ago': 72},
            ]
            for i, cd in enumerate(call_data):
                started = now - timedelta(hours=cd['hours_ago'])
                call = CallLog(
                    tenant_id=test_tenant.id,
                    retell_call_id=f'call_demo_{i+1:03d}',
                    agent_id=agent1.id,
                    from_number=cd['from'],
                    to_number='+14155551234',
                    direction='inbound',
                    started_at=started,
                    ended_at=started + timedelta(seconds=cd['dur']),
                    duration_seconds=cd['dur'],
                    status=cd['status'],
                    sentiment=cd['sentiment'],
                    summary=cd['summary'],
                    transcript=f"Agent: Thank you for calling Sunrise Dental Group! How can I help you today?\nCaller: Hi, I'd like to {cd['summary'].lower()[:80]}...\nAgent: Of course, I'd be happy to help with that...",
                )
                db.session.add(call)

            # ── Contact Lists & Contacts ──
            cl1 = ContactList(
                tenant_id=test_tenant.id,
                name='Q1 Appointment Reminders',
                description='Patients with appointments in Q1 2026',
                contact_count=5,
            )
            db.session.add(cl1)
            db.session.flush()

            contacts_data = [
                {'phone': '+14085551111', 'first': 'John', 'last': 'Smith', 'email': 'john@example.com', 'tz': 'America/Los_Angeles', 'data': {'appointment_date': '2026-04-01'}},
                {'phone': '+14085552222', 'first': 'Jane', 'last': 'Doe', 'email': 'jane@example.com', 'tz': 'America/New_York', 'data': {'appointment_date': '2026-04-02'}},
                {'phone': '+14085553333', 'first': 'Bob', 'last': 'Johnson', 'email': 'bob@example.com', 'tz': 'America/Chicago', 'data': {'appointment_date': '2026-04-03'}},
                {'phone': '+14085554444', 'first': 'Alice', 'last': 'Williams', 'email': 'alice@example.com', 'tz': 'America/Denver', 'data': {'appointment_date': '2026-04-04'}},
                {'phone': '+14085555555', 'first': 'Charlie', 'last': 'Brown', 'email': 'charlie@example.com', 'tz': 'America/Los_Angeles', 'data': {'appointment_date': '2026-04-05'}},
            ]
            contact_objs = []
            for cd in contacts_data:
                c = Contact(
                    tenant_id=test_tenant.id,
                    contact_list_id=cl1.id,
                    phone_number=cd['phone'],
                    first_name=cd['first'],
                    last_name=cd['last'],
                    email=cd['email'],
                    timezone=cd['tz'],
                    dynamic_data=cd['data'],
                    status='active',
                )
                db.session.add(c)
                contact_objs.append(c)
            db.session.flush()

            # ── Campaign ──
            campaign1 = Campaign(
                tenant_id=test_tenant.id,
                name='Q1 Appointment Reminders',
                agent_id=agent_outbound.id,
                contact_list_id=cl1.id,
                caller_id_number_id=pn2.id,
                status='completed',
                window_start_min=540,
                window_end_min=1260,
                allowed_days=['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday'],
                max_retries=2,
                total_tasks=5,
                completed_tasks=3,
                failed_tasks=1,
                launched_at=now - timedelta(days=3),
                completed_at=now - timedelta(days=1),
            )
            db.session.add(campaign1)
            db.session.flush()

            # Campaign tasks
            task_data = [
                {'status': 'completed', 'disp': 'completed', 'retries': 0, 'hours_ago': 72},
                {'status': 'completed', 'disp': 'completed', 'retries': 1, 'hours_ago': 60},
                {'status': 'completed', 'disp': 'voicemail', 'retries': 0, 'hours_ago': 48},
                {'status': 'failed', 'disp': 'no_answer', 'retries': 2, 'hours_ago': 36},
                {'status': 'skipped', 'disp': 'opted_out', 'retries': 0, 'hours_ago': 72},
            ]
            for i, td in enumerate(task_data):
                task = CampaignTask(
                    campaign_id=campaign1.id,
                    contact_id=contact_objs[i].id,
                    status=td['status'],
                    disposition=td['disp'],
                    retry_count=td['retries'],
                    last_attempted_at=now - timedelta(hours=td['hours_ago']),
                )
                db.session.add(task)
            db.session.flush()

            print(f'Seeded test customer: {test_email} / demo123')
            print(f'  - 4 agents (active inbound, failed, draft, active outbound)')
            print(f'  - 2 phone numbers')
            print(f'  - 1 contact list with 5 contacts')
            print(f'  - 1 completed campaign with 5 tasks')
            print(f'  - {len(call_data)} call logs')

        # ── Top-Up Packs ──
        if db.session.query(TopupPackDefinition).count() == 0:
            packs = [
                TopupPackDefinition(label='100 Minute Pack', minutes=100, price_cents=3900, is_active=True),
                TopupPackDefinition(label='500 Minute Pack', minutes=500, price_cents=17500, is_active=True),
                TopupPackDefinition(label='1000 Minute Pack', minutes=1000, price_cents=32000, is_active=True),
            ]
            db.session.add_all(packs)
            db.session.flush()
            print('Seeded 3 top-up packs.')

        # ── Billing data for demo tenant ──
        test_user_obj = db.session.query(User).filter_by(email='demo@agentgenie.com').first()
        if test_user_obj:
            test_membership = db.session.query(Membership).filter_by(user_id=test_user_obj.id).first()
            if test_membership:
                tid = test_membership.tenant_id
                # Invoice
                if db.session.query(Invoice).filter_by(tenant_id=tid).count() == 0:
                    inv1 = Invoice(
                        tenant_id=tid,
                        stripe_invoice_id='inv_demo_001',
                        amount_due_cents=24900,
                        amount_paid_cents=24900,
                        status='paid',
                        created_at=datetime.now(timezone.utc) - timedelta(days=30),
                    )
                    inv2 = Invoice(
                        tenant_id=tid,
                        stripe_invoice_id='inv_demo_002',
                        amount_due_cents=24900,
                        amount_paid_cents=0,
                        status='open',
                        created_at=datetime.now(timezone.utc) - timedelta(days=1),
                    )
                    db.session.add_all([inv1, inv2])
                    db.session.flush()
                    print('  - 2 invoices')

                # Payment
                if db.session.query(Payment).filter_by(tenant_id=tid).count() == 0:
                    pay1 = Payment(
                        tenant_id=tid,
                        stripe_payment_intent_id='pi_demo_001',
                        amount_cents=24900,
                        status='succeeded',
                        created_at=datetime.now(timezone.utc) - timedelta(days=30),
                    )
                    db.session.add(pay1)
                    db.session.flush()
                    print('  - 1 payment')

                # Top-up purchase
                if db.session.query(MinuteTopupPurchase).filter_by(tenant_id=tid).count() == 0:
                    topup = MinuteTopupPurchase(
                        tenant_id=tid,
                        minutes_added=100,
                        minutes_remaining=60,
                        purchase_price_cents=3900,
                        created_at=datetime.now(timezone.utc) - timedelta(days=5),
                    )
                    db.session.add(topup)
                    db.session.flush()
                    print('  - 1 top-up purchase (100 min, 60 remaining)')

                # Usage records (linked to existing call logs)
                from app.models.core import CallLog as CL
                call_logs = db.session.query(CL).filter_by(tenant_id=tid).order_by(CL.started_at.desc()).limit(5).all()
                if call_logs and db.session.query(UsageRecord).filter_by(tenant_id=tid).count() == 0:
                    for i, cl in enumerate(call_logs):
                        ur = UsageRecord(
                            tenant_id=tid,
                            call_log_id=cl.id,
                            provider_reported_seconds=cl.duration_seconds or (200 + i * 30),
                            internally_billable_seconds=(cl.duration_seconds or (200 + i * 30)) + (5 if i == 2 else 0),
                            reconciliation_status='adjusted' if i == 2 else 'matched',
                            adjustment_reason='Rounding correction' if i == 2 else None,
                            created_at=datetime.now(timezone.utc) - timedelta(hours=i * 12),
                        )
                        db.session.add(ur)
                    db.session.flush()
                    print(f'  - {len(call_logs)} usage records')

                # Billing notification
                if db.session.query(Notification).filter_by(tenant_id=tid).count() == 0:
                    notif = Notification(
                        tenant_id=tid,
                        subject='Usage Alert: 75% of included minutes used',
                        body='You have used 600 of your 800 included minutes this billing period. Consider purchasing a top-up pack to avoid overage charges.',
                        type='in_app',
                        status='pending',
                    )
                    db.session.add(notif)
                    db.session.flush()
                    print('  - 1 billing notification')

        # ── Tool Templates ──
        if db.session.query(ToolTemplate).count() == 0:
            tool_templates = [
                ToolTemplate(
                    slug='calendar_check_availability', name='Check Calendar Availability',
                    category='calendar', tool_type='real_time', access_tier='self_serve',
                    description='Check if a time slot is available on the calendar during a call.',
                    default_description_for_llm='Check if a time slot is available on the calendar.',
                    default_parameters_schema={'type': 'object', 'properties': {'date': {'type': 'string'}, 'time': {'type': 'string'}}, 'required': ['date', 'time']},
                    requires_oauth=True, oauth_provider='google',
                ),
                ToolTemplate(
                    slug='calendar_book_appointment', name='Book Appointment',
                    category='calendar', tool_type='real_time', access_tier='self_serve',
                    description='Book an appointment on the calendar for the caller during a call.',
                    default_description_for_llm='Book an appointment on the calendar for the caller.',
                    default_parameters_schema={'type': 'object', 'properties': {'date': {'type': 'string'}, 'time': {'type': 'string'}, 'caller_name': {'type': 'string'}}, 'required': ['date', 'time', 'caller_name']},
                    requires_oauth=True, oauth_provider='google',
                ),
                ToolTemplate(
                    slug='calendar_send_invite', name='Send Calendar Invite',
                    category='calendar', tool_type='post_call', access_tier='self_serve',
                    description='Send a calendar invite to the caller after the call ends.',
                    default_description_for_llm='Send a calendar invite to the caller after the call.',
                    requires_oauth=True, oauth_provider='google',
                ),
                ToolTemplate(
                    slug='email_send_summary', name='Email Call Summary',
                    category='email', tool_type='post_call', access_tier='self_serve',
                    description='Send an email summary of the call. Routed through our backend email provider.',
                    default_description_for_llm='Send an email summary of the call to a specified address.',
                ),
                ToolTemplate(
                    slug='email_send_followup', name='Email Follow-up',
                    category='email', tool_type='post_call', access_tier='self_serve',
                    description='Send a follow-up email to the caller. Routed through our backend email provider.',
                    default_description_for_llm='Send a follow-up email to the caller after the call.',
                ),
                ToolTemplate(
                    slug='sms_send_followup', name='SMS Follow-up',
                    category='sms', tool_type='post_call', access_tier='self_serve',
                    description='Send an SMS follow-up message to the caller after the call.',
                    default_description_for_llm='Send an SMS follow-up message to the caller after the call.',
                ),
                ToolTemplate(
                    slug='note_call_summary', name='Save Call Notes',
                    category='note_summary', tool_type='post_call', access_tier='self_serve',
                    description='Automatically generate and save structured call notes after the call ends.',
                    default_description_for_llm='Automatically generate and save structured call notes after the call ends.',
                ),
                ToolTemplate(
                    slug='note_deliver_summary', name='Deliver Call Summary',
                    category='note_summary', tool_type='post_call', access_tier='self_serve',
                    description='Deliver a formatted call summary via email or webhook after the call.',
                    default_description_for_llm='Deliver a formatted call summary via email or webhook after the call.',
                ),
                ToolTemplate(
                    slug='crm_lookup_contact', name='CRM Lookup Contact',
                    category='crm_ticket', tool_type='real_time', access_tier='dfy_only',
                    description='Look up a contact in the CRM by phone number during a call.',
                    default_description_for_llm='Look up a contact in the CRM by phone number.',
                    requires_oauth=True, oauth_provider='hubspot',
                ),
                ToolTemplate(
                    slug='crm_log_call', name='CRM Log Call',
                    category='crm_ticket', tool_type='post_call', access_tier='dfy_only',
                    description='Log the call details and transcript in the CRM after the call.',
                    default_description_for_llm='Log the call details and transcript in the CRM.',
                    requires_oauth=True, oauth_provider='hubspot',
                ),
                ToolTemplate(
                    slug='crm_create_ticket', name='Create Support Ticket',
                    category='crm_ticket', tool_type='post_call', access_tier='dfy_only',
                    description='Create a support ticket in the ticketing system after the call.',
                    default_description_for_llm='Create a support ticket in the ticketing system.',
                    requires_oauth=True, oauth_provider='hubspot',
                ),
                ToolTemplate(
                    slug='custom_webhook_realtime', name='Custom Webhook (Real-Time)',
                    category='custom_webhook', tool_type='real_time', access_tier='dfy_only',
                    description='Call a custom webhook URL during the call and use the response.',
                    default_description_for_llm='Call a custom webhook URL during the call and use the response.',
                ),
                ToolTemplate(
                    slug='custom_webhook_postcall', name='Custom Webhook (Post-Call)',
                    category='custom_webhook', tool_type='post_call', access_tier='dfy_only',
                    description='Send call data to a custom webhook URL after the call ends.',
                    default_description_for_llm='Send call data to a custom webhook URL after the call ends.',
                ),
            ]
            for t in tool_templates:
                db.session.add(t)
            db.session.flush()
            print(f'  - {len(tool_templates)} tool templates seeded')

            # Connect some tools for the demo tenant
            demo_user_for_tenant = db.session.query(User).filter_by(email='demo@agentgenie.com').first()
            demo_membership = db.session.query(Membership).filter_by(user_id=demo_user_for_tenant.id).first() if demo_user_for_tenant else None
            test_tenant_obj = db.session.query(Tenant).get(demo_membership.tenant_id) if demo_membership else None
            if test_tenant_obj:
                email_tmpl = db.session.query(ToolTemplate).filter_by(slug='email_send_summary').first()
                note_tmpl = db.session.query(ToolTemplate).filter_by(slug='note_call_summary').first()
                sms_tmpl = db.session.query(ToolTemplate).filter_by(slug='sms_send_followup').first()
                demo_connections = []
                for tmpl in [email_tmpl, note_tmpl, sms_tmpl]:
                    if tmpl:
                        conn = TenantToolConnection(
                            tenant_id=test_tenant_obj.id,
                            tool_template_id=tmpl.id,
                            status='connected',
                            connected_at=datetime.now(timezone.utc),
                            config={},
                        )
                        db.session.add(conn)
                        demo_connections.append(conn)
                db.session.flush()
                print(f'  - {len(demo_connections)} demo tool connections')

                # Assign note-taking to the first active agent
                demo_agents = db.session.query(Agent).filter_by(tenant_id=test_tenant_obj.id).all()
                if demo_connections and demo_agents:
                    active_agent = demo_agents[0]
                    note_conn = [c for c in demo_connections if c.tool_template_id == note_tmpl.id][0] if note_tmpl else None
                    if note_conn:
                        assignment = AgentToolAssignment(
                            agent_id=active_agent.id,
                            connection_id=note_conn.id,
                            tool_type='post_call',
                            function_name='note_call_summary',
                            description_for_llm='Automatically generate and save structured call notes after the call ends.',
                            is_active=True,
                        )
                        db.session.add(assignment)
                        db.session.flush()

                    # Add a sample action log
                    if note_conn:
                        sample_log = ActionLog(
                            tenant_id=test_tenant_obj.id,
                            agent_id=active_agent.id,
                            tool_type='post_call',
                            tool_name='note_call_summary',
                            status='success',
                            request_payload={'call_id': 'demo-call-001', 'transcript': 'Hello, I would like to schedule an appointment...'},
                            response_payload={'status': 'ok', 'notes': {'summary': 'Caller requested appointment scheduling.', 'action_items': ['Book appointment for Friday 2pm']}},
                            execution_ms=245,
                        )
                        db.session.add(sample_log)
                        db.session.flush()
                        print('  - 1 sample action log')

        # ── DFY Packages ──
        if db.session.query(DfyPackage).count() == 0:
            dfy_packages = [
                DfyPackage(
                    name='Inbound Agent Setup',
                    slug='inbound-setup',
                    description='We build and configure a production-ready inbound AI agent for your business.',
                    features=['Custom prompt engineering', 'Voice selection and tuning', 'Business hours configuration', 'Phone number provisioning', 'End-to-end testing'],
                    price_cents=49900,
                    billing_type='one_time',
                    estimated_days=5,
                    sort_order=1,
                    is_active=True,
                ),
                DfyPackage(
                    name='Outbound Campaign Setup',
                    slug='outbound-setup',
                    description='We design and launch your first outbound calling campaign with optimized scripts.',
                    features=['Outbound agent creation', 'Script optimization', 'Contact list import', 'Calling window configuration', 'Retry strategy setup'],
                    price_cents=69900,
                    billing_type='one_time',
                    estimated_days=7,
                    sort_order=2,
                    is_active=True,
                ),
                DfyPackage(
                    name='Calendar Integration Setup',
                    slug='calendar-setup',
                    description='Connect your booking system so your agent can schedule appointments in real time.',
                    features=['Calendar provider connection', 'Availability rules', 'Booking confirmation flow', 'Reschedule/cancel handling', 'Timezone configuration'],
                    price_cents=39900,
                    billing_type='one_time',
                    estimated_days=3,
                    sort_order=3,
                    is_active=True,
                ),
                DfyPackage(
                    name='CRM / Webhook Workflow',
                    slug='crm-webhook-workflow',
                    description='Integrate your CRM or business tools via webhooks for automated post-call actions.',
                    features=['CRM field mapping', 'Webhook endpoint setup', 'Post-call data sync', 'Lead scoring rules', 'Error handling and retry'],
                    price_cents=59900,
                    billing_type='one_time',
                    estimated_days=5,
                    sort_order=4,
                    is_active=True,
                ),
                DfyPackage(
                    name='MCP Integration Setup',
                    slug='mcp-integration-setup',
                    description='Set up a managed MCP gateway with approved connectors for your AI agents.',
                    features=['MCP server provisioning', 'Connector configuration', 'Tool-to-agent assignment', 'Security and audit setup', 'Usage metering activation'],
                    price_cents=79900,
                    billing_type='one_time',
                    estimated_days=7,
                    sort_order=5,
                    is_active=True,
                ),
                DfyPackage(
                    name='Email / SMS Workflow Setup',
                    slug='email-sms-workflow',
                    description='Configure automated email and SMS follow-ups triggered by call outcomes.',
                    features=['Email provider integration', 'SMS provider integration', 'Template design', 'Trigger rule configuration', 'Delivery monitoring'],
                    price_cents=44900,
                    billing_type='one_time',
                    estimated_days=4,
                    sort_order=6,
                    is_active=True,
                ),
                DfyPackage(
                    name='Custom Workflow Build',
                    slug='custom-workflow-build',
                    description='A fully custom integration or workflow tailored to your unique business requirements.',
                    features=['Requirements discovery', 'Custom architecture design', 'Implementation and testing', 'Documentation', 'Post-launch support'],
                    price_cents=None,
                    billing_type='custom_quote',
                    estimated_days=14,
                    sort_order=7,
                    is_active=True,
                ),
                DfyPackage(
                    name='Monthly Optimization Retainer',
                    slug='monthly-optimization',
                    description='Ongoing expert tuning of your agents, scripts, and workflows every month.',
                    features=['Monthly performance review', 'Prompt refinement', 'A/B script testing', 'Integration health checks', 'Priority support'],
                    price_cents=29900,
                    billing_type='recurring',
                    estimated_days=None,
                    sort_order=8,
                    is_active=True,
                ),
            ]
            for pkg in dfy_packages:
                db.session.add(pkg)
            db.session.flush()
            print(f'  - {len(dfy_packages)} DFY packages seeded')

            # Sample DFY project — use the demo user's actual tenant
            dfy_user = db.session.query(User).filter_by(email='demo@agentgenie.com').first()
            dfy_membership = db.session.query(Membership).filter_by(user_id=dfy_user.id).first() if dfy_user else None
            dfy_tenant = db.session.query(Tenant).get(dfy_membership.tenant_id) if dfy_membership else None
            dfy_admin = db.session.query(User).filter_by(email='admin@platform.com').first()
            if dfy_tenant and dfy_user:
                inbound_pkg = db.session.query(DfyPackage).filter_by(slug='inbound-setup').first()
                if inbound_pkg:
                    project = DfyProject(
                        tenant_id=dfy_tenant.id,
                        package_id=inbound_pkg.id,
                        status='in_progress',
                        owner_id=dfy_admin.id if dfy_admin else None,
                        description='We need a professional receptionist agent that can handle appointment scheduling, insurance verification questions, and emergency call routing.',
                        special_requirements='Must integrate with our Dentrix practice management software. Business hours are Mon-Fri 8am-5pm PST.',
                        quoted_price_cents=49900,
                        max_revisions=2,
                        intake_form_data={
                            'business_name': 'Sunrise Dental Group',
                            'business_type': 'Dental Practice',
                            'description': 'We need a professional receptionist agent that can handle appointment scheduling, insurance verification questions, and emergency call routing. The agent should sound warm and professional.',
                            'special_requirements': 'Must integrate with our Dentrix practice management software. Business hours are Mon-Fri 8am-5pm PST.',
                        },
                        invoice_id='cs_demo_001',
                        admin_notes='Client has Dentrix integration requirement. May need custom webhook for PMS sync.',
                        target_delivery_date=datetime.now(timezone.utc).date() + timedelta(days=5),
                        revision_count=0,
                    )
                    db.session.add(project)
                    db.session.flush()

                    # Sample messages
                    msgs = [
                        DfyMessage(project_id=project.id, sender_id=dfy_user.id, content='Hi, we are excited to get started! Our main priority is appointment scheduling.'),
                        DfyMessage(project_id=project.id, sender_id=dfy_admin.id, content='Welcome! I have reviewed your requirements. I will start with the prompt engineering for appointment scheduling and have a draft ready in 2 days.'),
                        DfyMessage(project_id=project.id, sender_id=dfy_admin.id, content='Note: Check if Dentrix has a webhook API or if we need to use Zapier as middleware.', is_admin_note=True),
                    ]
                    for m in msgs:
                        db.session.add(m)
                    db.session.flush()
                    print('  - 1 sample DFY project with messages')

        # ── Platform Settings ──
        defaults = {
            'settlement_hold_days': {'value': 30, 'description': 'Days before revenue becomes eligible for partner payout'},
            'minimum_payout_cents': {'value': 5000, 'description': 'Minimum payout threshold in cents ($50)'},
            'default_revenue_split_pct': {'value': 50, 'description': 'Default partner revenue share percentage'},
            'recording_retention_days': {'value': 90, 'description': 'Days to retain call recordings'},
            'recordings_enabled': {'value': True, 'description': 'Global toggle: whether call recordings are visible to tenants'},
        }
        for key, data in defaults.items():
            if not db.session.query(PlatformSetting).filter_by(key=key).first():
                db.session.add(PlatformSetting(key=key, value=data['value'], description=data['description']))

        db.session.commit()
        print('Full seed complete.')


if __name__ == '__main__':
    seed()
