"""
Celery Tasks for Agent Provisioning and Retell Operations.

All Retell provisioning and number operations run asynchronously through
these Celery tasks.  Request handlers should only validate input, create
local records with ``pending`` status, and enqueue a task via ``.delay()``.

Each task:
- has a bounded retry policy
- is idempotent (safe to re-run)
- uses structured logging
- transitions to ``failed`` / ``needs_attention`` on terminal failure
"""
import json
import logging
from datetime import datetime, timezone

from celery import shared_task

logger = logging.getLogger(__name__)


def _get_db_and_models():
    """Import db and models inside function to avoid circular imports."""
    from app import db
    from app.models.core import (
        Agent, AgentConfig, AgentDraft, AgentVersion,
        HandoffRule, GuardrailRule, PhoneNumber, KnowledgeBaseItem,
    )
    return db, Agent, AgentConfig, AgentDraft, AgentVersion, HandoffRule, GuardrailRule, PhoneNumber, KnowledgeBaseItem


def _notify_agent_failed(db, agent, error_message: str):
    """Send agent_failed notification (best-effort)."""
    try:
        from app.services.notifications.dispatcher import notify
        from app.models.core import User, Membership
        membership = db.session.query(Membership).filter_by(tenant_id=agent.tenant_id).first()
        user_email = db.session.get(User, membership.user_id).email if membership else None
        notify(
            'agent_failed',
            to_email=user_email,
            tenant_id=agent.tenant_id,
            context={
                'agent_name': agent.name,
                'error_message': error_message,
            },
        )
    except Exception as e:
        logger.warning(f"Failed to send agent_failed notification: {e}")


# ---------------------------------------------------------------------------
# Agent Provisioning
# ---------------------------------------------------------------------------
@shared_task(
    bind=True,
    name='tasks.provision_agent',
    max_retries=3,
    default_retry_delay=30,
    acks_late=True,
)
def provision_agent_to_retell(self, agent_id: str, draft_id: str) -> dict:
    """
    Provision an agent to Retell AI.

    Builds a **comprehensive** LLM prompt from ALL structured draft data
    (services, FAQs, offers, hours, handoff rules, guardrails, KB items)
    so the live agent enforces the full reviewed configuration.

    States: draft -> pending -> active (success) or failed (error)
    """
    from app.services import retell_adapter
    from app.services.prompt_builder import build_full_prompt

    db, Agent, AgentConfig, AgentDraft, AgentVersion, HandoffRule, GuardrailRule, PhoneNumber, KnowledgeBaseItem = _get_db_and_models()

    try:
        agent = db.session.get(Agent, agent_id)
        draft = db.session.get(AgentDraft, draft_id)

        if not agent or not draft:
            logger.error(f"Agent {agent_id} or draft {draft_id} not found")
            return {"status": "error", "message": "Agent or draft not found"}

        if draft.status != 'approved':
            logger.error(f"Draft {draft_id} is not approved (status: {draft.status})")
            return {"status": "error", "message": "Draft is not approved"}

        # Idempotency: if already active with a retell_agent_id, skip
        if agent.status == 'active' and agent.retell_agent_id:
            logger.info(f"Agent {agent_id} already provisioned — skipping")
            return {"status": "success", "retell_agent_id": agent.retell_agent_id}

        # Step 1: Set agent to pending
        agent.status = 'pending'
        db.session.commit()

        # Step 2: Extract config from draft
        config_data = draft.generated_config
        if isinstance(config_data, str):
            config_data = json.loads(config_data)

        agent_name = config_data.get('agent_name', agent.name)
        greeting_message = config_data.get('greeting_message', '')

        # Step 3: Gather DB-persisted handoff rules, guardrails, and KB items
        db_handoffs = [
            {'condition': r.condition, 'destination_number': r.destination_number,
             'transfer_message': r.transfer_message}
            for r in db.session.query(HandoffRule).filter_by(agent_id=agent_id).all()
        ]
        db_guardrails = [
            {'prohibited_topic': r.prohibited_topic, 'fallback_message': r.fallback_message}
            for r in db.session.query(GuardrailRule).filter_by(agent_id=agent_id).all()
        ]
        db_kb_items = [
            {'title': item.title, 'content': item.content or '',
             'type': item.type.value if hasattr(item.type, 'value') else str(item.type),
             'url': item.url or '', 'file_name': item.file_name or ''}
            for item in db.session.query(KnowledgeBaseItem).filter_by(
                agent_id=agent_id).all()
        ]

        # Step 4: Build comprehensive prompt from ALL structured data
        full_prompt = build_full_prompt(
            config_data=config_data,
            handoff_rules=db_handoffs or None,
            guardrail_rules=db_guardrails or None,
            kb_items=db_kb_items or None,
        )

        # Step 5: Call Retell to create the agent with the full prompt
        result = retell_adapter.create_agent(
            agent_name=agent_name,
            role_description=full_prompt,
            tone=config_data.get('tone', 'professional'),
            greeting_message=greeting_message,
            business_context=None,  # Already included in full_prompt
            voice_id=agent.voice_id,
            language=agent.language,
        )

        if result['status'] != 'success':
            agent.status = 'failed'
            db.session.commit()
            logger.error(f"Retell provisioning failed for agent {agent_id}: {result.get('message')}")
            if self.request.retries < self.max_retries:
                raise self.retry(exc=Exception(result.get('message', 'Retell error')))
            return {"status": "failed", "message": result.get('message', 'Retell provisioning failed')}

        # Step 6: Success — store Retell IDs and create config
        retell_data = result['data']
        retell_agent_id = retell_data.get('agent_id')
        retell_llm_id = retell_data.get('llm_id')

        agent.retell_agent_id = retell_agent_id
        agent.name = agent_name
        agent.status = 'active'

        # Create or update AgentConfig — store the full prompt as role_description
        existing_config = db.session.query(AgentConfig).filter_by(agent_id=agent_id).first()
        if existing_config:
            existing_config.role_description = full_prompt
            existing_config.tone = config_data.get('tone', 'professional')
            existing_config.business_context = {
                'full_config': config_data,
                'greeting_message': greeting_message,
                'retell_llm_id': retell_llm_id,
            }
            existing_config.version += 1
        else:
            new_config = AgentConfig(
                tenant_id=agent.tenant_id,
                agent_id=agent_id,
                role_description=full_prompt,
                tone=config_data.get('tone', 'professional'),
                business_context={
                    'full_config': config_data,
                    'greeting_message': greeting_message,
                    'retell_llm_id': retell_llm_id,
                },
                version=1,
            )
            db.session.add(new_config)

        # Create version snapshot
        version_count = db.session.query(AgentVersion).filter_by(agent_id=agent_id).count()
        version = AgentVersion(
            tenant_id=agent.tenant_id,
            agent_id=agent_id,
            version_number=version_count + 1,
            config_snapshot={
                **config_data,
                'compiled_prompt': full_prompt,
                'retell_agent_id': retell_agent_id,
                'retell_llm_id': retell_llm_id,
            },
            retell_version_id=retell_agent_id,
        )
        db.session.add(version)

        # Persist handoff rules from config (if not already in DB)
        if not db_handoffs:
            for rule_data in config_data.get('handoff_rules', []) + config_data.get('human_handoff_conditions', []) + config_data.get('transfer_rules', []):
                if rule_data.get('condition'):
                    rule = HandoffRule(
                        tenant_id=agent.tenant_id,
                        agent_id=agent_id,
                        condition=rule_data.get('condition', ''),
                        destination_number=rule_data.get('destination_number'),
                        transfer_message=rule_data.get('transfer_message'),
                    )
                    db.session.add(rule)

        # Persist guardrail rules from config (if not already in DB)
        if not db_guardrails:
            for guard_data in config_data.get('guardrails', []) + config_data.get('prohibited_topics', []):
                if guard_data.get('prohibited_topic'):
                    rule = GuardrailRule(
                        tenant_id=agent.tenant_id,
                        agent_id=agent_id,
                        prohibited_topic=guard_data.get('prohibited_topic', ''),
                        fallback_message=guard_data.get('fallback_message', 'I cannot discuss that topic.'),
                    )
                    db.session.add(rule)

        db.session.commit()

        logger.info(
            f"Agent {agent_id} provisioned successfully with full structured prompt. "
            f"Retell agent_id: {retell_agent_id}, LLM: {retell_llm_id}"
        )

        # Send notification
        try:
            from app.services.notifications.dispatcher import notify
            from app.models.core import User, Membership
            membership = db.session.query(Membership).filter_by(tenant_id=agent.tenant_id).first()
            user_email = db.session.get(User, membership.user_id).email if membership else None
            notify(
                'agent_provisioned',
                to_email=user_email,
                tenant_id=agent.tenant_id,
                context={
                    'agent_name': agent.name,
                    'agent_url': f'/app/agents/{agent_id}',
                },
            )
        except Exception as notif_err:
            logger.warning(f"Notification failed for agent {agent_id}: {notif_err}")

        return {
            "status": "success",
            "retell_agent_id": retell_agent_id,
            "retell_llm_id": retell_llm_id,
        }

    except self.MaxRetriesExceededError:
        logger.error(f"Max retries exceeded for agent {agent_id}")
        try:
            agent = db.session.get(Agent, agent_id)
            if agent:
                agent.status = 'failed'
                db.session.commit()
                _notify_agent_failed(db, agent, 'Max retries exceeded')
        except Exception:
            db.session.rollback()
        return {"status": "error", "message": "Max retries exceeded"}

    except Exception as e:
        logger.exception(f"Error provisioning agent {agent_id}: {e}")
        try:
            agent = db.session.get(Agent, agent_id)
            if agent:
                agent.status = 'failed'
                db.session.commit()
                _notify_agent_failed(db, agent, str(e)[:200])
        except Exception:
            db.session.rollback()
        return {"status": "error", "message": str(e)[:500]}


# ---------------------------------------------------------------------------
# Agent Update
# ---------------------------------------------------------------------------
@shared_task(
    bind=True,
    name='tasks.update_agent',
    max_retries=3,
    default_retry_delay=15,
    acks_late=True,
)
def update_agent_in_retell(self, agent_id: str, config_updates: dict) -> dict:
    """Update an existing agent's configuration in Retell.

    Rebuilds the **full** LLM prompt from all structured data (services,
    FAQs, handoff rules, guardrails, KB items) so that edits made via the
    review/edit UI are reflected in the live agent's runtime behavior.
    """
    from app.services import retell_adapter
    from app.services.prompt_builder import build_full_prompt

    db, Agent, AgentConfig, AgentDraft, AgentVersion, HandoffRule, GuardrailRule, PhoneNumber, KnowledgeBaseItem = _get_db_and_models()

    try:
        agent = db.session.get(Agent, agent_id)
        if not agent or not agent.retell_agent_id:
            return {"status": "error", "message": "Agent not found or not provisioned"}

        config = db.session.query(AgentConfig).filter_by(agent_id=agent_id).first()
        if not config:
            return {"status": "error", "message": "Agent config not found"}

        # Set to pending during update
        agent.status = 'pending'
        db.session.commit()

        # Get the LLM ID from business_context
        biz_ctx = config.business_context or {}
        if isinstance(biz_ctx, str):
            biz_ctx = json.loads(biz_ctx)
        llm_id = biz_ctx.get('retell_llm_id')

        # Merge config_updates into the stored full_config to get current state
        stored_config = biz_ctx.get('full_config', {})
        if isinstance(stored_config, str):
            stored_config = json.loads(stored_config)
        merged_config = {**stored_config, **config_updates}

        # Gather DB-persisted handoff rules, guardrails, and KB items
        db_handoffs = [
            {'condition': r.condition, 'destination_number': r.destination_number,
             'transfer_message': r.transfer_message}
            for r in db.session.query(HandoffRule).filter_by(agent_id=agent_id).all()
        ]
        db_guardrails = [
            {'prohibited_topic': r.prohibited_topic, 'fallback_message': r.fallback_message}
            for r in db.session.query(GuardrailRule).filter_by(agent_id=agent_id).all()
        ]
        db_kb_items = [
            {'title': item.title, 'content': item.content or '',
             'type': item.type.value if hasattr(item.type, 'value') else str(item.type),
             'url': item.url or '', 'file_name': item.file_name or ''}
            for item in db.session.query(KnowledgeBaseItem).filter_by(
                agent_id=agent_id).all()
        ]

        # Rebuild the full prompt from ALL structured data
        full_prompt = build_full_prompt(
            config_data=merged_config,
            handoff_rules=db_handoffs or None,
            guardrail_rules=db_guardrails or None,
            kb_items=db_kb_items or None,
        )

        # Update LLM with the rebuilt prompt
        new_greeting = config_updates.get('greeting_message')
        if llm_id:
            llm_result = retell_adapter.update_retell_llm(
                llm_id=llm_id,
                general_prompt=full_prompt,
                begin_message=new_greeting,
            )
            if llm_result['status'] != 'success':
                agent.status = 'needs_attention'
                db.session.commit()
                if self.request.retries < self.max_retries:
                    raise self.retry(exc=Exception(llm_result.get('message', 'LLM update failed')))
                return llm_result

        # Update agent metadata (name, voice, language)
        new_name = config_updates.get('agent_name')
        new_voice_id = config_updates.get('voice_id')
        new_language = config_updates.get('language')

        if new_name or new_voice_id or new_language:
            retell_adapter.update_agent(
                retell_agent_id=agent.retell_agent_id,
                agent_name=new_name,
                voice_id=new_voice_id,
                language=new_language,
            )
            if new_name:
                agent.name = new_name
            if new_voice_id:
                agent.voice_id = new_voice_id
            if new_language:
                agent.language = new_language

        # Update local config with the rebuilt prompt
        config.role_description = full_prompt
        if config_updates.get('tone'):
            config.tone = config_updates['tone']
        biz_ctx['full_config'] = merged_config
        if new_greeting:
            biz_ctx['greeting_message'] = new_greeting
        config.business_context = biz_ctx
        config.version += 1

        # Create version snapshot
        version_count = db.session.query(AgentVersion).filter_by(agent_id=agent_id).count()
        version = AgentVersion(
            tenant_id=agent.tenant_id,
            agent_id=agent_id,
            version_number=version_count + 1,
            config_snapshot={
                **merged_config,
                'compiled_prompt': full_prompt,
            },
            retell_version_id=agent.retell_agent_id,
        )
        db.session.add(version)

        agent.status = 'active'
        db.session.commit()

        logger.info(f"Agent {agent_id} updated with full structured prompt in Retell")
        return {"status": "success"}

    except Exception as e:
        logger.exception(f"Error updating agent {agent_id}: {e}")
        try:
            agent = db.session.get(Agent, agent_id)
            if agent:
                agent.status = 'needs_attention'
                db.session.commit()
        except Exception:
            db.session.rollback()
        return {"status": "error", "message": str(e)[:500]}


# ---------------------------------------------------------------------------
# Agent Deletion
# ---------------------------------------------------------------------------
@shared_task(
    bind=True,
    name='tasks.delete_agent',
    max_retries=2,
    default_retry_delay=10,
    acks_late=True,
)
def delete_agent_from_retell(self, agent_id: str) -> dict:
    """Delete an agent from Retell and mark as inactive locally."""
    from app.services import retell_adapter

    db, Agent, AgentConfig, AgentDraft, AgentVersion, HandoffRule, GuardrailRule, PhoneNumber, KnowledgeBaseItem = _get_db_and_models()

    try:
        agent = db.session.get(Agent, agent_id)
        if not agent:
            return {"status": "error", "message": "Agent not found"}

        if agent.retell_agent_id:
            result = retell_adapter.delete_agent(agent.retell_agent_id)
            if result['status'] != 'success':
                logger.warning(f"Failed to delete agent from Retell: {result.get('message')}")

        agent.status = 'draft'
        agent.retell_agent_id = None
        db.session.commit()

        logger.info(f"Agent {agent_id} deleted from Retell")
        return {"status": "success"}

    except Exception as e:
        logger.exception(f"Error deleting agent {agent_id}: {e}")
        return {"status": "error", "message": str(e)[:500]}


# ---------------------------------------------------------------------------
# Phone Number Purchase (async)
# ---------------------------------------------------------------------------
@shared_task(
    bind=True,
    name='tasks.purchase_phone_number',
    max_retries=3,
    default_retry_delay=15,
    acks_late=True,
)
def purchase_phone_number_async(self, phone_number_id: str, area_code: str) -> dict:
    """Purchase a phone number from Retell asynchronously.

    The request handler creates a PhoneNumber record with status='pending_provision'
    and enqueues this task.
    """
    from app.services import retell_adapter

    db, Agent, AgentConfig, AgentDraft, AgentVersion, HandoffRule, GuardrailRule, PhoneNumber, KnowledgeBaseItem = _get_db_and_models()

    try:
        phone = db.session.get(PhoneNumber, phone_number_id)
        if not phone:
            return {"status": "error", "message": "Phone number record not found"}

        # Idempotency: if already active, skip
        if phone.status == 'active' and phone.retell_number_id:
            return {"status": "success", "number": phone.number}

        result = retell_adapter.purchase_phone_number(area_code)

        if result['status'] != 'success':
            phone.status = 'failed'
            db.session.commit()
            logger.error(f"Phone purchase failed for {phone_number_id}: {result.get('message')}")
            if self.request.retries < self.max_retries:
                raise self.retry(exc=Exception(result.get('message', 'Purchase failed')))
            return {"status": "failed", "message": result.get('message')}

        data = result['data']
        phone.number = data.get('phone_number', phone.number)
        phone.retell_number_id = data.get('phone_number_id')
        phone.status = 'unassigned'
        phone.purchased_at = datetime.now(timezone.utc)
        db.session.commit()

        logger.info(f"Phone number purchased: {phone.number}")

        # Send notification
        try:
            from app.services.notifications.dispatcher import notify
            from app.models.core import User, Membership
            membership = db.session.query(Membership).filter_by(tenant_id=phone.tenant_id).first()
            user_email = db.session.get(User, membership.user_id).email if membership else None
            notify(
                'number_purchased',
                to_email=user_email,
                tenant_id=phone.tenant_id,
                context={'phone_number': phone.number},
            )
        except Exception as notif_err:
            logger.warning(f"Notification failed for phone purchase: {notif_err}")

        return {"status": "success", "number": phone.number}

    except Exception as e:
        logger.exception(f"Error purchasing phone number: {e}")
        try:
            phone = db.session.get(PhoneNumber, phone_number_id)
            if phone:
                phone.status = 'failed'
                db.session.commit()
        except Exception:
            db.session.rollback()
        return {"status": "error", "message": str(e)[:500]}


# ---------------------------------------------------------------------------
# Phone Number Assignment (async)
# ---------------------------------------------------------------------------
@shared_task(
    bind=True,
    name='tasks.assign_phone_number',
    max_retries=2,
    default_retry_delay=10,
    acks_late=True,
)
def assign_phone_number_async(self, phone_number_id: str, agent_id: str) -> dict:
    """Assign a phone number to an agent in Retell asynchronously."""
    from app.services import retell_adapter

    db, Agent, AgentConfig, AgentDraft, AgentVersion, HandoffRule, GuardrailRule, PhoneNumber, KnowledgeBaseItem = _get_db_and_models()

    try:
        phone = db.session.get(PhoneNumber, phone_number_id)
        agent = db.session.get(Agent, agent_id)

        if not phone or not agent:
            return {"status": "error", "message": "Phone or agent not found"}

        if phone.retell_number_id and agent.retell_agent_id:
            result = retell_adapter.assign_phone_number(phone.retell_number_id, agent.retell_agent_id)
            if result['status'] != 'success':
                logger.error(f"Retell assignment failed: {result.get('message')}")
                if self.request.retries < self.max_retries:
                    raise self.retry(exc=Exception(result.get('message')))
                return result

        phone.agent_id = agent.id
        phone.status = 'active'
        db.session.commit()

        logger.info(f"Phone {phone.number} assigned to agent {agent.name}")
        return {"status": "success"}

    except Exception as e:
        logger.exception(f"Error assigning phone number: {e}")
        return {"status": "error", "message": str(e)[:500]}


# ---------------------------------------------------------------------------
# Phone Number Unassignment (async)
# ---------------------------------------------------------------------------
@shared_task(
    bind=True,
    name='tasks.unassign_phone_number',
    max_retries=2,
    default_retry_delay=10,
    acks_late=True,
)
def unassign_phone_number_async(self, phone_number_id: str) -> dict:
    """Unassign a phone number from its agent in Retell asynchronously."""
    from app.services import retell_adapter

    db, Agent, AgentConfig, AgentDraft, AgentVersion, HandoffRule, GuardrailRule, PhoneNumber, KnowledgeBaseItem = _get_db_and_models()

    try:
        phone = db.session.get(PhoneNumber, phone_number_id)
        if not phone:
            return {"status": "error", "message": "Phone number not found"}

        if phone.retell_number_id:
            result = retell_adapter.assign_phone_number(phone.retell_number_id, '')
            if result['status'] != 'success':
                logger.warning(f"Retell unassignment warning: {result.get('message')}")

        phone.agent_id = None
        phone.status = 'unassigned'
        db.session.commit()

        logger.info(f"Phone {phone.number} unassigned")
        return {"status": "success"}

    except Exception as e:
        logger.exception(f"Error unassigning phone number: {e}")
        return {"status": "error", "message": str(e)[:500]}


# ---------------------------------------------------------------------------
# Phone Number Release (async)
# ---------------------------------------------------------------------------
@shared_task(
    bind=True,
    name='tasks.release_phone_number',
    max_retries=2,
    default_retry_delay=10,
    acks_late=True,
)
def release_phone_number_async(self, phone_number_id: str) -> dict:
    """Release a phone number from Retell asynchronously."""
    from app.services import retell_adapter

    db, Agent, AgentConfig, AgentDraft, AgentVersion, HandoffRule, GuardrailRule, PhoneNumber, KnowledgeBaseItem = _get_db_and_models()

    try:
        phone = db.session.get(PhoneNumber, phone_number_id)
        if not phone:
            return {"status": "error", "message": "Phone number not found"}

        if phone.retell_number_id:
            result = retell_adapter.release_phone_number(phone.retell_number_id)
            if result['status'] != 'success':
                logger.warning(f"Retell release warning: {result.get('message')}")

        phone.status = 'failed'
        phone.agent_id = None
        db.session.commit()

        logger.info(f"Phone {phone.number} released")
        return {"status": "success"}

    except Exception as e:
        logger.exception(f"Error releasing phone number: {e}")
        return {"status": "error", "message": str(e)[:500]}


# ---------------------------------------------------------------------------
# Campaign Launch (async)
# ---------------------------------------------------------------------------
@shared_task(
    bind=True,
    name='tasks.launch_campaign',
    max_retries=2,
    default_retry_delay=30,
    acks_late=True,
)
def launch_campaign_async(self, campaign_id: str) -> dict:
    """Launch a campaign via Retell Batch Call API asynchronously."""
    from app.services import retell_adapter
    from app.services.campaign_engine import compile_campaign, build_retell_tasks, build_call_time_window

    db, Agent, AgentConfig, AgentDraft, AgentVersion, HandoffRule, GuardrailRule, PhoneNumber, KnowledgeBaseItem = _get_db_and_models()
    from app.models.core import Campaign, CampaignTask

    try:
        campaign = db.session.get(Campaign, campaign_id)
        if not campaign:
            return {"status": "error", "message": "Campaign not found"}

        tasks = compile_campaign(campaign)
        if not tasks:
            campaign.status = 'failed'
            db.session.commit()
            return {"status": "error", "message": "No eligible contacts"}

        retell_tasks = build_retell_tasks(campaign, tasks)
        call_time_window = build_call_time_window(campaign)

        trigger_ts = None
        if campaign.scheduled_at:
            trigger_ts = int(campaign.scheduled_at.timestamp() * 1000)

        result = retell_adapter.create_batch_call(
            from_number=campaign.caller_id_number.number,
            tasks=retell_tasks,
            name=f"AG-{campaign.name[:50]}",
            trigger_timestamp=trigger_ts,
            call_time_window=call_time_window,
        )

        if result['status'] == 'success':
            from datetime import datetime, timezone
            campaign.retell_batch_call_id = result['data'].get('batch_call_id')
            campaign.status = 'scheduled' if campaign.scheduled_at else 'running'
            campaign.launched_at = datetime.now(timezone.utc)
            for task in tasks:
                task.status = 'queued'
            db.session.commit()
            logger.info(f"Campaign {campaign.name} launched with {len(tasks)} calls")
            return {"status": "success", "calls": len(tasks)}
        else:
            campaign.status = 'failed'
            db.session.commit()
            logger.error(f"Campaign launch failed: {result.get('message')}")
            return result

    except Exception as e:
        logger.exception(f"Error launching campaign {campaign_id}: {e}")
        return {"status": "error", "message": str(e)[:500]}


# ---------------------------------------------------------------------------
# One-Off Outbound Call (async)
# ---------------------------------------------------------------------------
@shared_task(
    bind=True,
    name='tasks.outbound_call',
    max_retries=2,
    default_retry_delay=10,
    acks_late=True,
)
def outbound_call_async(self, from_number: str, to_number: str, retell_agent_id: str, tenant_id: str) -> dict:
    """Initiate a one-off outbound call via Retell asynchronously."""
    from app.services import retell_adapter

    try:
        result = retell_adapter.create_phone_call(
            from_number=from_number,
            to_number=to_number,
            agent_id=retell_agent_id,
            metadata={'tenant_id': tenant_id, 'one_off': True},
        )

        if result['status'] == 'success':
            logger.info(f"Outbound call initiated: {from_number} -> {to_number}")
        else:
            logger.error(f"Outbound call failed: {result.get('message')}")

        return result

    except Exception as e:
        logger.exception(f"Error initiating outbound call: {e}")
        return {"status": "error", "message": str(e)[:500]}
