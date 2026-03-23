"""Dashboard blueprint — customer-facing routes."""
import json
import logging
from datetime import datetime, timezone

from flask import Blueprint, render_template, g, request, redirect, url_for, flash, jsonify, current_app
from flask_login import login_required, current_user

from app import db
from app.services.tenant.scoping import get_current_tenant_id, scoped_query

logger = logging.getLogger(__name__)

dashboard_bp = Blueprint('dashboard', __name__)


@dashboard_bp.before_request
@login_required
def before_request():
    """Ensure all dashboard routes require authentication."""
    pass


# =========================================================================
# Dashboard Home
# =========================================================================
@dashboard_bp.route('/')
def home():
    from app.models.core import Agent, CallLog, Subscription, PhoneNumber
    tenant_id = get_current_tenant_id()

    agents = scoped_query(Agent).all()
    recent_calls = scoped_query(CallLog).order_by(CallLog.created_at.desc()).limit(5).all()
    subscription = db.session.query(Subscription).filter_by(tenant_id=tenant_id).first()
    phone_numbers = scoped_query(PhoneNumber).filter(PhoneNumber.status != 'failed').all()

    total_calls = scoped_query(CallLog).count()
    active_agents = scoped_query(Agent).filter_by(status='active').count()
    active_numbers = scoped_query(PhoneNumber).filter(PhoneNumber.status.in_(['active', 'unassigned'])).count()
    total_minutes = sum(c.duration_seconds or 0 for c in scoped_query(CallLog).all()) // 60

    return render_template('dashboard/home.html',
                           agents=agents,
                           recent_calls=recent_calls,
                           subscription=subscription,
                           phone_numbers=phone_numbers,
                           total_calls=total_calls,
                           active_agents=active_agents,
                           active_numbers=active_numbers,
                           total_minutes=total_minutes)


# =========================================================================
# Voice & Language API (JSON)
# =========================================================================
@dashboard_bp.route('/api/voices')
def api_voices():
    """Return voice/language data as JSON for dynamic dropdowns."""
    from app.services.voice_registry import get_voice_language_data
    return jsonify(get_voice_language_data())


@dashboard_bp.route('/api/voices/validate', methods=['POST'])
def api_validate_voice():
    """Validate a voice-language combination."""
    from app.services.voice_registry import validate_voice_language
    data = request.get_json(silent=True) or {}
    voice_id = data.get('voice_id', '')
    language = data.get('language', '')
    result = validate_voice_language(voice_id, language)
    return jsonify(result)


# =========================================================================
# Agent Builder — Natural Language Flow
# =========================================================================
@dashboard_bp.route('/agents')
def agents_list():
    from app.models.core import Agent
    agents = scoped_query(Agent).order_by(Agent.created_at.desc()).all()
    return render_template('dashboard/agents_list.html', agents=agents)


@dashboard_bp.route('/agents/new')
def agents_new():
    """Step 1: Show the natural language agent builder form."""
    return render_template('dashboard/agents_new.html')


@dashboard_bp.route('/agents/generate', methods=['POST'])
def agents_generate():
    """
    Step 2: Take the user's natural language prompt, send to OpenAI microservice,
    create an AgentDraft, and redirect to the draft review page.
    """
    from app.models.core import Agent, AgentDraft
    from app.services.openai_brain_client import generate_agent_config

    tenant_id = get_current_tenant_id()
    user_prompt = request.form.get('prompt', '').strip()

    if not user_prompt or len(user_prompt) < 10:
        flash('Please describe your agent in at least a few sentences.', 'error')
        return redirect(url_for('dashboard.agents_new'))

    # Call OpenAI microservice
    result = generate_agent_config(user_prompt)

    if result['status'] == 'error':
        flash(result.get('message', 'Failed to generate agent configuration.'), 'error')
        return redirect(url_for('dashboard.agents_new'))

    config_data = result.get('data', {})

    # Create the Agent record (status=draft)
    agent_name = config_data.get('agent_name', 'New Agent')
    selected_language = request.form.get('language', 'en-US')
    selected_voice = request.form.get('voice_id', '')
    selected_mode = request.form.get('mode', 'inbound')
    if selected_mode not in ('inbound', 'outbound'):
        selected_mode = 'inbound'

    # Validate voice-language combination
    if selected_voice:
        from app.services.voice_registry import validate_voice_language
        validation = validate_voice_language(selected_voice, selected_language)
        if not validation['valid']:
            flash(f'Voice/language issue: {validation["reason"]}', 'error')
            return redirect(url_for('dashboard.agents_new'))

    # Apply default voice if none selected
    if not selected_voice:
        from app.services.voice_registry import get_default_voice
        selected_voice = get_default_voice(selected_language)

    agent = Agent(
        tenant_id=tenant_id,
        name=agent_name,
        status='draft',
        mode=selected_mode,
        language=selected_language,
        voice_id=selected_voice,
    )
    db.session.add(agent)
    db.session.flush()  # Get the agent ID

    # Create the AgentDraft
    draft = AgentDraft(
        tenant_id=tenant_id,
        agent_id=agent.id,
        raw_prompt=user_prompt,
        generated_config=config_data,
        status='pending_review',
    )
    db.session.add(draft)
    db.session.commit()

    flash('Agent configuration generated! Please review below.', 'success')
    return redirect(url_for('dashboard.agent_draft_review', draft_id=draft.id))


@dashboard_bp.route('/agents/draft/<draft_id>')
def agent_draft_review(draft_id):
    """
    Step 3: Show the AI-generated configuration for review.
    If missing_information exists, show the remediation form.
    Otherwise, show the Approve & Provision button.
    """
    from app.models.core import AgentDraft, Agent
    draft = scoped_query(AgentDraft).filter_by(id=draft_id).first_or_404()
    agent = db.session.get(Agent, draft.agent_id) if draft.agent_id else None

    config = draft.generated_config
    if isinstance(config, str):
        config = json.loads(config)

    missing_info = config.get('missing_information', [])
    has_missing = len(missing_info) > 0

    return render_template('dashboard/agent_draft_review.html',
                           draft=draft,
                           agent=agent,
                           config=config,
                           missing_info=missing_info,
                           has_missing=has_missing)


@dashboard_bp.route('/agents/draft/<draft_id>/remediate', methods=['POST'])
def agent_draft_remediate(draft_id):
    """
    Step 3b: User provides answers to missing_information questions.
    Re-generate the config with the enriched prompt.
    """
    from app.models.core import AgentDraft
    from app.services.openai_brain_client import generate_agent_config as remediate_agent_config_fn

    draft = scoped_query(AgentDraft).filter_by(id=draft_id).first_or_404()

    config = draft.generated_config
    if isinstance(config, str):
        config = json.loads(config)

    missing_info = config.get('missing_information', [])

    # Collect answers from the form
    remediation_answers = {}
    question_count = int(request.form.get('missing_count', 0))
    for i in range(question_count):
        question = request.form.get(f'missing_question_{i}', '').strip()
        answer = request.form.get(f'missing_answer_{i}', '').strip()
        if question and answer:
            remediation_answers[question] = answer

    if not remediation_answers:
        flash('Please provide at least one answer.', 'error')
        return redirect(url_for('dashboard.agent_draft_review', draft_id=draft_id))

    # Re-generate with enriched prompt
    enriched_parts = [draft.raw_prompt, '\n\nAdditional details provided by the user:']
    for q, a in remediation_answers.items():
        enriched_parts.append(f'- {q}: {a}')
    enriched_prompt = '\n'.join(enriched_parts)

    result = remediate_agent_config_fn(enriched_prompt)

    if result['status'] == 'error':
        flash(result.get('message', 'Failed to update configuration.'), 'error')
        return redirect(url_for('dashboard.agent_draft_review', draft_id=draft_id))

    # Update the draft with new config
    new_config = result.get('data', {})
    draft.generated_config = new_config

    # Update agent name if changed
    if draft.agent_id:
        from app.models.core import Agent
        agent = db.session.get(Agent, draft.agent_id)
        if agent and new_config.get('agent_name'):
            agent.name = new_config['agent_name']

    db.session.commit()

    flash('Configuration updated with your additional details!', 'success')
    return redirect(url_for('dashboard.agent_draft_review', draft_id=draft_id))


@dashboard_bp.route('/agents/draft/<draft_id>/save', methods=['POST'])
def agent_draft_save_edits(draft_id):
    """Save user edits to the draft config, or dispatch to approve/regenerate."""
    from app.models.core import AgentDraft, Agent

    action = request.form.get('action', 'save')

    # Dispatch to regenerate
    if action == 'regenerate':
        return _handle_regenerate(draft_id)

    draft = scoped_query(AgentDraft).filter_by(id=draft_id).first_or_404()
    config = draft.generated_config
    if isinstance(config, str):
        config = json.loads(config)

    # Merge form edits into the config.  role_description is NOT in this
    # list because it has been removed from the draft review UI.  The single
    # canonical role field is agent_role.
    for field in ['agent_name', 'business_type', 'business_context', 'agent_role',
                   'tone', 'greeting_message', 'booking_behavior',
                   'support_flow', 'fallback_behavior', 'unsupported_request_behavior']:
        val = request.form.get(field, '').strip()
        if val:
            config[field] = val

    # Always mirror agent_role → role_description inside the draft config
    # so prompt_builder and any legacy code that reads role_description
    # will get the same value.
    if config.get('agent_role'):
        config['role_description'] = config['agent_role']

    # Hours
    tz = request.form.get('hours_timezone', '').strip()
    sched = request.form.get('hours_schedule', '').strip()
    if tz or sched:
        config['hours_of_operation'] = {'timezone': tz or 'UTC', 'schedule': sched}

    # Escalation rules (newline-separated)
    esc = request.form.get('escalation_rules', '').strip()
    if esc:
        config['escalation_rules'] = [r.strip() for r in esc.split('\n') if r.strip()]

    # Services
    svc_names = request.form.getlist('service_name[]')
    svc_descs = request.form.getlist('service_desc[]')
    config['services'] = [
        {'name': n.strip(), 'description': d.strip()}
        for n, d in zip(svc_names, svc_descs) if n.strip()
    ]

    # FAQs
    faq_qs = request.form.getlist('faq_question[]')
    faq_as = request.form.getlist('faq_answer[]')
    config['faqs'] = [
        {'question': q.strip(), 'answer': a.strip()}
        for q, a in zip(faq_qs, faq_as) if q.strip()
    ]

    # Offers
    config['specials_offers'] = [o.strip() for o in request.form.getlist('specials_offers[]') if o.strip()]

    # Handoff rules
    h_conds = request.form.getlist('handoff_condition[]')
    h_nums = request.form.getlist('handoff_number[]')
    h_msgs = request.form.getlist('handoff_message[]')
    config['handoff_rules'] = [
        {'condition': c.strip(), 'destination_number': n.strip() or None, 'transfer_message': m.strip() or None}
        for c, n, m in zip(h_conds, h_nums, h_msgs) if c.strip()
    ]
    config['human_handoff_conditions'] = config['handoff_rules']
    config['transfer_rules'] = config['handoff_rules']

    # Guardrails
    g_topics = request.form.getlist('guardrail_topic[]')
    g_msgs = request.form.getlist('guardrail_message[]')
    config['guardrails'] = [
        {'prohibited_topic': t.strip(), 'fallback_message': m.strip() or 'I cannot discuss that topic.'}
        for t, m in zip(g_topics, g_msgs) if t.strip()
    ]
    config['prohibited_topics'] = config['guardrails']

    # Knowledge categories (newline-separated)
    kc = request.form.get('knowledge_categories', '').strip()
    config['knowledge_categories'] = [c.strip() for c in kc.split('\n') if c.strip()] if kc else config.get('knowledge_categories', [])

    # Routing rules (newline-separated)
    rr = request.form.get('routing_rules', '').strip()
    config['routing_rules'] = [r.strip() for r in rr.split('\n') if r.strip()] if rr else config.get('routing_rules', [])

    draft.generated_config = config
    db.session.commit()

    if action == 'approve':
        return redirect(url_for('dashboard.agent_draft_approve', draft_id=draft_id), code=307)

    flash('Draft edits saved successfully.', 'success')
    return redirect(url_for('dashboard.agent_draft_review', draft_id=draft_id))


def _handle_regenerate(draft_id):
    """Internal helper for regenerate action from the unified form."""
    from app.models.core import AgentDraft, Agent
    from app.services.openai_brain_client import generate_agent_config

    draft = scoped_query(AgentDraft).filter_by(id=draft_id).first_or_404()
    new_prompt = request.form.get('regenerate_prompt', '').strip()
    if not new_prompt or len(new_prompt) < 10:
        flash('Please provide a more detailed description.', 'error')
        return redirect(url_for('dashboard.agent_draft_review', draft_id=draft_id))

    result = generate_agent_config(new_prompt)
    if result['status'] == 'error':
        flash(result.get('message', 'Failed to regenerate configuration.'), 'error')
        return redirect(url_for('dashboard.agent_draft_review', draft_id=draft_id))

    new_config = result.get('data', {})
    draft.raw_prompt = new_prompt
    draft.generated_config = new_config
    draft.status = 'pending_review'

    if draft.agent_id:
        agent = db.session.get(Agent, draft.agent_id)
        if agent:
            if new_config.get('agent_name'):
                agent.name = new_config['agent_name']
            agent.status = 'draft'

    db.session.commit()
    flash('Configuration regenerated from your updated description!', 'success')
    return redirect(url_for('dashboard.agent_draft_review', draft_id=draft_id))


@dashboard_bp.route('/agents/draft/<draft_id>/regenerate', methods=['POST'])
def agent_draft_regenerate(draft_id):
    """Regenerate the agent config from an edited prompt."""
    from app.models.core import AgentDraft, Agent
    from app.services.openai_brain_client import generate_agent_config

    draft = scoped_query(AgentDraft).filter_by(id=draft_id).first_or_404()

    new_prompt = request.form.get('prompt', '').strip()
    if not new_prompt or len(new_prompt) < 10:
        flash('Please provide a more detailed description.', 'error')
        return redirect(url_for('dashboard.agent_draft_review', draft_id=draft_id))

    result = generate_agent_config(new_prompt)

    if result['status'] == 'error':
        flash(result.get('message', 'Failed to regenerate configuration.'), 'error')
        return redirect(url_for('dashboard.agent_draft_review', draft_id=draft_id))

    new_config = result.get('data', {})
    draft.raw_prompt = new_prompt
    draft.generated_config = new_config
    draft.status = 'pending_review'

    if draft.agent_id:
        agent = db.session.get(Agent, draft.agent_id)
        if agent:
            if new_config.get('agent_name'):
                agent.name = new_config['agent_name']
            agent.status = 'draft'

            # Update voice/language if provided in the regeneration form
            new_language = request.form.get('language', '').strip()
            new_voice_id = request.form.get('voice_id', '').strip()
            if new_voice_id and new_language:
                from app.services.voice_registry import validate_voice_language
                validation = validate_voice_language(new_voice_id, new_language)
                if not validation['valid']:
                    flash(f'Voice/language issue: {validation["reason"]}', 'error')
                    return redirect(url_for('dashboard.agent_draft_review', draft_id=draft_id))
                agent.voice_id = new_voice_id
                agent.language = new_language
            elif new_language:
                agent.language = new_language
            elif new_voice_id:
                agent.voice_id = new_voice_id

    db.session.commit()

    flash('Configuration regenerated from your updated description!', 'success')
    return redirect(url_for('dashboard.agent_draft_review', draft_id=draft_id))


@dashboard_bp.route('/agents/draft/<draft_id>/approve', methods=['POST'])
def agent_draft_approve(draft_id):
    """
    Step 4: Approve the draft and trigger async provisioning to Retell.
    """
    from app.models.core import AgentDraft, Agent
    from app.tasks.agent_tasks import provision_agent_to_retell

    draft = scoped_query(AgentDraft).filter_by(id=draft_id).first_or_404()

    if draft.status != 'pending_review':
        flash('This draft has already been processed.', 'warning')
        return redirect(url_for('dashboard.agent_detail', agent_id=draft.agent_id))

    # Mark draft as approved
    draft.status = 'approved'
    db.session.commit()

    # Set agent to pending and enqueue async provisioning
    agent = db.session.get(Agent, draft.agent_id)
    if agent:
        agent.status = 'pending'
        db.session.commit()

    try:
        provision_agent_to_retell.delay(draft.agent_id, draft.id)
        flash('Agent approved! Provisioning has been queued and will complete shortly.', 'success')
    except Exception as e:
        import logging
        logging.getLogger(__name__).exception('Failed to enqueue provisioning task: %s', e)
        if agent:
            agent.status = 'failed'
            db.session.commit()
        flash('Agent approved but provisioning could not be queued. '
              'Please retry from the agent detail page.', 'error')

    return redirect(url_for('dashboard.agent_detail', agent_id=draft.agent_id))


@dashboard_bp.route('/agents/draft/<draft_id>/reject', methods=['POST'])
def agent_draft_reject(draft_id):
    """Reject a draft and optionally delete the agent."""
    from app.models.core import AgentDraft, Agent

    draft = scoped_query(AgentDraft).filter_by(id=draft_id).first_or_404()
    draft.status = 'rejected'

    # If the agent is still in draft, remove it
    if draft.agent_id:
        agent = db.session.get(Agent, draft.agent_id)
        if agent and agent.status == 'draft':
            db.session.delete(agent)

    db.session.commit()
    flash('Draft rejected.', 'info')
    return redirect(url_for('dashboard.agents_list'))


# =========================================================================
# Agent Detail & Management
# =========================================================================
@dashboard_bp.route('/agents/<agent_id>')
def agent_detail(agent_id):
    from app.models.core import (
        Agent, AgentConfig, AgentDraft, AgentVersion,
        HandoffRule, GuardrailRule, PhoneNumber, CallLog,
    )
    tenant_id = get_current_tenant_id()
    agent = scoped_query(Agent).filter_by(id=agent_id).first_or_404()
    config = db.session.query(AgentConfig).filter_by(agent_id=agent_id).first()
    drafts = scoped_query(AgentDraft).filter_by(agent_id=agent_id).order_by(AgentDraft.created_at.desc()).all()
    versions = db.session.query(AgentVersion).filter_by(agent_id=agent_id).order_by(AgentVersion.version_number.desc()).all()
    handoff_rules = db.session.query(HandoffRule).filter_by(agent_id=agent_id).all()
    guardrail_rules = db.session.query(GuardrailRule).filter_by(agent_id=agent_id).all()
    phone_numbers = scoped_query(PhoneNumber).filter_by(agent_id=agent_id).filter(PhoneNumber.status != 'failed').all()
    recent_calls = scoped_query(CallLog).filter_by(agent_id=agent_id).order_by(CallLog.created_at.desc()).limit(5).all()
    total_calls = scoped_query(CallLog).filter_by(agent_id=agent_id).count()

    deployment_ready = (agent.status == 'active' and agent.retell_agent_id is not None)
    has_number = len(phone_numbers) > 0

    tenant_id = get_current_tenant_id()
    unassigned_numbers = PhoneNumber.query.filter_by(
        tenant_id=tenant_id, agent_id=None, status='unassigned'
    ).all()

    return render_template('dashboard/agent_detail.html',
                           agent=agent,
                           config=config,
                           drafts=drafts,
                           versions=versions,
                           handoff_rules=handoff_rules,
                           guardrail_rules=guardrail_rules,
                           phone_numbers=phone_numbers,
                           recent_calls=recent_calls,
                           total_calls=total_calls,
                           deployment_ready=deployment_ready,
                           has_number=has_number,
                           unassigned_numbers=unassigned_numbers)


@dashboard_bp.route('/agents/<agent_id>/edit')
def agent_edit(agent_id):
    """Show the agent edit form with granular rule editing."""
    from app.models.core import Agent, AgentConfig, HandoffRule, GuardrailRule
    from app.services.voice_registry import get_languages, list_voices
    agent = scoped_query(Agent).filter_by(id=agent_id).first_or_404()
    config = db.session.query(AgentConfig).filter_by(agent_id=agent_id).first()
    handoff_rules = db.session.query(HandoffRule).filter_by(agent_id=agent_id).all()
    guardrail_rules = db.session.query(GuardrailRule).filter_by(agent_id=agent_id).all()
    languages = get_languages()
    voices = list_voices()
    return render_template('dashboard/agent_edit.html',
                           agent=agent, config=config,
                           handoff_rules=handoff_rules,
                           guardrail_rules=guardrail_rules,
                           languages=languages,
                           voices=voices)


@dashboard_bp.route('/agents/<agent_id>/edit', methods=['POST'])
def agent_edit_submit(agent_id):
    """Process agent edit form including granular handoff/guardrail rule editing."""
    from app.models.core import Agent, AgentConfig, HandoffRule, GuardrailRule
    from app.tasks.agent_tasks import update_agent_in_retell

    agent = scoped_query(Agent).filter_by(id=agent_id).first_or_404()
    tenant_id = get_current_tenant_id()

    # --- Basic config updates ---
    config_updates = {}
    if request.form.get('agent_name'):
        config_updates['agent_name'] = request.form['agent_name']
    # agent_role is the canonical structured role field.  It is stored in
    # business_context['full_config']['agent_role'] and used by prompt_builder
    # to compile the full Retell prompt.  We must NOT write it into
    # config.role_description, which holds the compiled prompt.
    if request.form.get('agent_role'):
        config_updates['agent_role'] = request.form['agent_role']
    if request.form.get('tone'):
        config_updates['tone'] = request.form['tone']
    if request.form.get('greeting_message'):
        config_updates['greeting_message'] = request.form['greeting_message']
    if request.form.get('business_context'):
        config_updates['business_context'] = request.form['business_context']

    # --- Voice & Language ---
    new_language = request.form.get('language', '').strip()
    new_voice_id = request.form.get('voice_id', '').strip()
    if new_voice_id and new_language:
        from app.services.voice_registry import validate_voice_language
        validation = validate_voice_language(new_voice_id, new_language)
        if not validation['valid']:
            flash(f'Voice/language issue: {validation["reason"]}', 'error')
            return redirect(url_for('dashboard.agent_edit', agent_id=agent_id))
        config_updates['voice_id'] = new_voice_id
        config_updates['language'] = new_language
    elif new_language:
        config_updates['language'] = new_language
    elif new_voice_id:
        config_updates['voice_id'] = new_voice_id

    # --- Handoff Rules (delete-then-replace strategy) ---
    handoff_count = int(request.form.get('handoff_rule_count', 0))

    # Collect the IDs of rules the form still wants to keep
    submitted_handoff_ids = set()
    for i in range(handoff_count):
        rid = request.form.get(f'handoff_rule_id_{i}', '').strip()
        if rid:
            submitted_handoff_ids.add(rid)

    # Delete rules that are no longer in the form submission
    all_handoffs = db.session.query(HandoffRule).filter_by(agent_id=agent.id).all()
    for rule in all_handoffs:
        if rule.id not in submitted_handoff_ids:
            db.session.delete(rule)
    db.session.flush()  # ensure deletes are applied before inserts

    # Now update existing and insert new rules
    for i in range(handoff_count):
        rule_id = request.form.get(f'handoff_rule_id_{i}', '').strip()
        condition = request.form.get(f'handoff_condition_{i}', '').strip()
        number = request.form.get(f'handoff_number_{i}', '').strip()
        message = request.form.get(f'handoff_message_{i}', '').strip()
        if not condition:
            continue
        if rule_id:
            rule = db.session.get(HandoffRule, rule_id)
            if rule and rule.agent_id == agent.id:
                rule.condition = condition
                rule.destination_number = number or None
                rule.transfer_message = message or None
        else:
            new_rule = HandoffRule(
                tenant_id=tenant_id,
                agent_id=agent.id,
                condition=condition,
                destination_number=number or None,
                transfer_message=message or None,
            )
            db.session.add(new_rule)

    # --- Guardrail Rules (delete-then-replace strategy) ---
    guardrail_count = int(request.form.get('guardrail_rule_count', 0))

    # Collect the IDs of rules the form still wants to keep
    submitted_guardrail_ids = set()
    for i in range(guardrail_count):
        rid = request.form.get(f'guardrail_rule_id_{i}', '').strip()
        if rid:
            submitted_guardrail_ids.add(rid)

    # Delete rules that are no longer in the form submission
    all_guardrails = db.session.query(GuardrailRule).filter_by(agent_id=agent.id).all()
    for rule in all_guardrails:
        if rule.id not in submitted_guardrail_ids:
            db.session.delete(rule)
    db.session.flush()  # ensure deletes are applied before inserts

    # Now update existing and insert new rules
    for i in range(guardrail_count):
        rule_id = request.form.get(f'guardrail_rule_id_{i}', '').strip()
        topic = request.form.get(f'guardrail_topic_{i}', '').strip()
        response = request.form.get(f'guardrail_response_{i}', '').strip()
        if not topic:
            continue
        if rule_id:
            rule = db.session.get(GuardrailRule, rule_id)
            if rule and rule.agent_id == agent.id:
                rule.prohibited_topic = topic
                rule.fallback_message = response or 'I cannot discuss that topic.'
        else:
            new_rule = GuardrailRule(
                tenant_id=tenant_id,
                agent_id=agent.id,
                prohibited_topic=topic,
                fallback_message=response or 'I cannot discuss that topic.',
            )
            db.session.add(new_rule)

    # --- Apply config changes ---
    config = db.session.query(AgentConfig).filter_by(agent_id=agent_id).first()
    if config:
        # Do NOT write agent_role into config.role_description — that column
        # stores the compiled full Retell prompt and is only written by the
        # provisioning/update Celery tasks after prompt_builder runs.
        if config_updates.get('tone'):
            config.tone = config_updates['tone']

        # Persist structured edits into business_context['full_config'] so
        # the update_agent_in_retell task can merge them and rebuild the prompt.
        bc = config.business_context if isinstance(config.business_context, dict) else {}
        fc = bc.get('full_config', {})
        if isinstance(fc, str):
            import json as _json
            fc = _json.loads(fc)

        if config_updates.get('agent_role'):
            fc['agent_role'] = config_updates['agent_role']
            # Also keep role_description in sync inside full_config for
            # backward compat with any code that reads the old key.
            fc['role_description'] = config_updates['agent_role']
        if config_updates.get('greeting_message'):
            fc['greeting_message'] = config_updates['greeting_message']
            bc['greeting_message'] = config_updates['greeting_message']
        if config_updates.get('business_context'):
            fc['business_context'] = config_updates['business_context']
            bc['text'] = config_updates['business_context']

        bc['full_config'] = fc
        config.business_context = bc
    if config_updates.get('agent_name'):
        agent.name = config_updates['agent_name']
    if config_updates.get('voice_id'):
        agent.voice_id = config_updates['voice_id']
    if config_updates.get('language'):
        agent.language = config_updates['language']

    db.session.commit()

    # Sync to Retell asynchronously if provisioned
    if agent.retell_agent_id:
        update_agent_in_retell.delay(agent.id, config_updates)
        flash('Agent updated. Syncing to Retell AI in the background.', 'success')
    else:
        flash('Agent updated locally.', 'success')

    return redirect(url_for('dashboard.agent_detail', agent_id=agent_id))


@dashboard_bp.route('/agents/<agent_id>/retry-provision', methods=['POST'])
def agent_retry_provision(agent_id):
    """Retry provisioning a failed agent."""
    from app.models.core import Agent, AgentDraft
    from app.tasks.agent_tasks import provision_agent_to_retell

    agent = scoped_query(Agent).filter_by(id=agent_id).first_or_404()

    if agent.status not in ('failed', 'needs_attention'):
        flash('Agent does not need re-provisioning.', 'info')
        return redirect(url_for('dashboard.agent_detail', agent_id=agent_id))

    # Find the latest approved draft
    draft = scoped_query(AgentDraft).filter_by(
        agent_id=agent_id, status='approved'
    ).order_by(AgentDraft.created_at.desc()).first()

    if not draft:
        flash('No approved draft found for this agent.', 'error')
        return redirect(url_for('dashboard.agent_detail', agent_id=agent_id))

    agent.status = 'pending'
    db.session.commit()
    provision_agent_to_retell.delay(agent_id, draft.id)
    flash('Re-provisioning started. The agent will be updated shortly.', 'info')
    return redirect(url_for('dashboard.agent_detail', agent_id=agent_id))


@dashboard_bp.route('/agents/<agent_id>/delete', methods=['POST'])
def agent_delete(agent_id):
    """Delete an agent (from Retell and locally)."""
    from app.models.core import Agent
    from app.tasks.agent_tasks import delete_agent_from_retell

    agent = scoped_query(Agent).filter_by(id=agent_id).first_or_404()

    if agent.retell_agent_id:
        delete_agent_from_retell.delay(agent_id)
    else:
        agent.status = 'draft'
        agent.retell_agent_id = None
        db.session.commit()
    flash('Agent deletion initiated.', 'success')
    return redirect(url_for('dashboard.agents_list'))


# =========================================================================
# Agent — Quick Assign Number
# =========================================================================
@dashboard_bp.route('/agents/<agent_id>/assign-number', methods=['POST'])
def agent_assign_number(agent_id):
    """Quick-assign an unassigned phone number to this agent."""
    from app.models.core import Agent, PhoneNumber
    from app.tasks.agent_tasks import assign_phone_number_async

    agent = scoped_query(Agent).filter_by(id=agent_id).first_or_404()
    tenant_id = get_current_tenant_id()
    number_id = request.form.get('number_id')

    if not number_id:
        flash('Please select a phone number to assign.', 'error')
        return redirect(url_for('dashboard.agent_detail', agent_id=agent_id))

    phone = db.session.query(PhoneNumber).filter_by(id=number_id, tenant_id=tenant_id).first()
    if not phone:
        flash('Phone number not found.', 'error')
        return redirect(url_for('dashboard.agent_detail', agent_id=agent_id))

    # Optimistic local update, then async Retell sync
    phone.agent_id = agent.id
    phone.status = 'active'
    db.session.commit()

    if phone.retell_number_id and agent.retell_agent_id:
        assign_phone_number_async.delay(phone.id, agent.id)

    flash(f'Phone number {phone.number} assigned to {agent.name}!', 'success')
    return redirect(url_for('dashboard.agent_detail', agent_id=agent_id))


# =========================================================================
# Knowledge Base CRUD
# =========================================================================
@dashboard_bp.route('/agents/<agent_id>/knowledge')
def knowledge_base(agent_id):
    """List all knowledge-base items for an agent."""
    from app.models.core import Agent, KnowledgeBaseItem
    agent = scoped_query(Agent).filter_by(id=agent_id).first_or_404()
    items = KnowledgeBaseItem.query.filter_by(
        agent_id=agent_id, tenant_id=get_current_tenant_id()
    ).order_by(KnowledgeBaseItem.created_at.desc()).all()
    return render_template('dashboard/knowledge_base.html', agent=agent, items=items)


# ── File upload constants ──────────────────────────────────────────────
_ALLOWED_KB_EXTENSIONS = {'.pdf', '.docx', '.doc', '.txt', '.md', '.csv', '.json', '.xml'}
_MAX_KB_FILE_SIZE = 10 * 1024 * 1024  # 10 MB


@dashboard_bp.route('/agents/<agent_id>/knowledge/add', methods=['POST'])
def kb_add(agent_id):
    """Create a new KB item with real content extraction."""
    from app.models.core import Agent, KnowledgeBaseItem
    import os
    from werkzeug.utils import secure_filename

    agent = scoped_query(Agent).filter_by(id=agent_id).first_or_404()
    tenant_id = get_current_tenant_id()

    kb_type = request.form.get('type', 'text')
    title = request.form.get('title', '').strip()
    if not title:
        flash('Title is required.', 'error')
        return redirect(url_for('dashboard.knowledge_base', agent_id=agent_id))

    user_content = request.form.get('content', '').strip() or None
    item = KnowledgeBaseItem(
        tenant_id=tenant_id,
        agent_id=agent_id,
        type=kb_type,
        title=title,
        content=user_content,
        category=request.form.get('category', '').strip() or None,
        url=request.form.get('url', '').strip() or None,
    )

    # ── Server-side file upload validation ──────────────────────────
    uploaded = request.files.get('file')
    if uploaded and uploaded.filename:
        safe_name = secure_filename(uploaded.filename)
        ext = os.path.splitext(safe_name)[1].lower()

        # Extension whitelist
        if ext not in _ALLOWED_KB_EXTENSIONS:
            flash(f'File type "{ext}" is not allowed. Accepted: {" ".join(sorted(_ALLOWED_KB_EXTENSIONS))}', 'error')
            return redirect(url_for('dashboard.knowledge_base', agent_id=agent_id))

        # Size check — read into memory first to enforce limit
        file_bytes = uploaded.read()
        if len(file_bytes) > _MAX_KB_FILE_SIZE:
            flash(f'File exceeds the 10 MB size limit ({len(file_bytes) / (1024*1024):.1f} MB).', 'error')
            return redirect(url_for('dashboard.knowledge_base', agent_id=agent_id))

        upload_dir = os.path.join(current_app.instance_path, 'uploads', 'kb', tenant_id)
        os.makedirs(upload_dir, exist_ok=True)
        save_path = os.path.join(upload_dir, safe_name)
        with open(save_path, 'wb') as f:
            f.write(file_bytes)

        item.file_name = safe_name
        item.file_path = save_path
        item.file_size = len(file_bytes)
        item.file_mime = uploaded.content_type

    db.session.add(item)
    db.session.commit()

    # ── Real content extraction for URL/file items ──────────────────
    # Step 1: Use kb_extractor to actually fetch/parse the content.
    # Step 2: Optionally enhance via Brain microservice.
    if not user_content and item.type in ('url', 'file', 'booking_link'):
        try:
            from app.services.kb_extractor import extract_content
            extracted = extract_content(
                url=item.url or '',
                file_path=item.file_path or '',
                file_name=item.file_name or '',
                file_mime=item.file_mime or '',
            )
            if extracted:
                item.content = extracted
                db.session.commit()
                current_app.logger.info(
                    f'KB item {item.id}: extracted {len(extracted)} chars from '
                    f'{"URL " + item.url if item.url else "file " + (item.file_name or "?")}')
        except Exception as e:
            current_app.logger.warning(f'Content extraction failed for KB item {item.id}: {e}')

        # Step 2: If extraction succeeded, optionally enhance via Brain
        if item.content:
            try:
                from app.services.openai_brain_client import structure_knowledge_base as brain_structure_kb
                result = brain_structure_kb(
                    item.content[:10000],
                    content_type=str(item.type),
                    agent_context=agent.name or '',
                )
                if result.get('status') == 'success' and result.get('items'):
                    structured_parts = []
                    for si in result['items']:
                        s_title = si.get('title', '')
                        s_content = si.get('content', '')
                        if s_title and s_content:
                            structured_parts.append(f"{s_title}: {s_content}")
                        elif s_content:
                            structured_parts.append(s_content)
                    if structured_parts:
                        item.content = '\n'.join(structured_parts)
                        db.session.commit()
            except Exception as e:
                current_app.logger.warning(f'Brain KB structuring failed for item {item.id}: {e}')

    # ── Trigger Retell resync if agent is already provisioned ────────
    if agent.retell_agent_id and agent.status in ('active', 'needs_attention'):
        try:
            from app.tasks.agent_tasks import update_agent_in_retell
            update_agent_in_retell.delay(agent_id, {})
            flash(f'Knowledge base item "{title}" added. Agent prompt is being resynced.', 'success')
        except Exception as e:
            current_app.logger.warning(f'Failed to enqueue Retell resync after KB add: {e}')
            flash(f'Knowledge base item "{title}" added. Retell resync could not be queued.', 'warning')
    else:
        flash(f'Knowledge base item "{title}" added.', 'success')
    return redirect(url_for('dashboard.knowledge_base', agent_id=agent_id))


@dashboard_bp.route('/agents/<agent_id>/knowledge/<item_id>/edit', methods=['GET', 'POST'])
def kb_edit(agent_id, item_id):
    """Edit an existing KB item with real content extraction."""
    from app.models.core import Agent, KnowledgeBaseItem
    import os
    from werkzeug.utils import secure_filename

    agent = scoped_query(Agent).filter_by(id=agent_id).first_or_404()
    tenant_id = get_current_tenant_id()
    item = KnowledgeBaseItem.query.filter_by(
        id=item_id, agent_id=agent_id, tenant_id=tenant_id
    ).first_or_404()

    if request.method == 'GET':
        return render_template('dashboard/knowledge_base_edit.html', agent=agent, item=item)

    item.type = request.form.get('type', item.type)
    item.title = request.form.get('title', item.title).strip()
    user_content = request.form.get('content', '').strip() or None
    item.content = user_content
    item.category = request.form.get('category', '').strip() or None
    item.url = request.form.get('url', '').strip() or None

    # ── Server-side file upload validation ──────────────────────────
    uploaded = request.files.get('file')
    if uploaded and uploaded.filename:
        safe_name = secure_filename(uploaded.filename)
        ext = os.path.splitext(safe_name)[1].lower()

        if ext not in _ALLOWED_KB_EXTENSIONS:
            flash(f'File type "{ext}" is not allowed. Accepted: {" ".join(sorted(_ALLOWED_KB_EXTENSIONS))}', 'error')
            return redirect(url_for('dashboard.kb_edit', agent_id=agent_id, item_id=item_id))

        file_bytes = uploaded.read()
        if len(file_bytes) > _MAX_KB_FILE_SIZE:
            flash(f'File exceeds the 10 MB size limit ({len(file_bytes) / (1024*1024):.1f} MB).', 'error')
            return redirect(url_for('dashboard.kb_edit', agent_id=agent_id, item_id=item_id))

        upload_dir = os.path.join(current_app.instance_path, 'uploads', 'kb', tenant_id)
        os.makedirs(upload_dir, exist_ok=True)
        save_path = os.path.join(upload_dir, safe_name)
        with open(save_path, 'wb') as f:
            f.write(file_bytes)

        item.file_name = safe_name
        item.file_path = save_path
        item.file_size = len(file_bytes)
        item.file_mime = uploaded.content_type

    # ── Real content extraction for URL/file items ──────────────────
    if not user_content and item.type in ('url', 'file', 'booking_link'):
        try:
            from app.services.kb_extractor import extract_content
            extracted = extract_content(
                url=item.url or '',
                file_path=item.file_path or '',
                file_name=item.file_name or '',
                file_mime=item.file_mime or '',
            )
            if extracted:
                item.content = extracted
                current_app.logger.info(
                    f'KB item {item.id}: re-extracted {len(extracted)} chars')
        except Exception as e:
            current_app.logger.warning(f'Content extraction failed for KB item {item.id}: {e}')

        # Optionally enhance via Brain microservice
        if item.content:
            try:
                from app.services.openai_brain_client import structure_knowledge_base as brain_structure_kb
                result = brain_structure_kb(
                    item.content[:10000],
                    content_type=str(item.type),
                    agent_context=agent.name or '',
                )
                if result.get('status') == 'success' and result.get('items'):
                    structured_parts = []
                    for si in result['items']:
                        s_title = si.get('title', '')
                        s_content = si.get('content', '')
                        if s_title and s_content:
                            structured_parts.append(f"{s_title}: {s_content}")
                        elif s_content:
                            structured_parts.append(s_content)
                    if structured_parts:
                        item.content = '\n'.join(structured_parts)
            except Exception as e:
                current_app.logger.warning(f'Brain KB structuring failed for item {item.id}: {e}')

    db.session.commit()

    # ── Trigger Retell resync if agent is already provisioned ────────
    if agent.retell_agent_id and agent.status in ('active', 'needs_attention'):
        try:
            from app.tasks.agent_tasks import update_agent_in_retell
            update_agent_in_retell.delay(agent_id, {})
            flash(f'Knowledge base item "{item.title}" updated. Agent prompt is being resynced.', 'success')
        except Exception as e:
            current_app.logger.warning(f'Failed to enqueue Retell resync after KB edit: {e}')
            flash(f'Knowledge base item "{item.title}" updated. Retell resync could not be queued.', 'warning')
    else:
        flash(f'Knowledge base item "{item.title}" updated.', 'success')
    return redirect(url_for('dashboard.knowledge_base', agent_id=agent_id))


@dashboard_bp.route('/agents/<agent_id>/knowledge/<item_id>/delete', methods=['POST'])
def kb_delete(agent_id, item_id):
    """Delete a KB item."""
    from app.models.core import Agent, KnowledgeBaseItem
    agent = scoped_query(Agent).filter_by(id=agent_id).first_or_404()
    tenant_id = get_current_tenant_id()
    item = KnowledgeBaseItem.query.filter_by(
        id=item_id, agent_id=agent_id, tenant_id=tenant_id
    ).first_or_404()
    db.session.delete(item)
    db.session.commit()

    # ── Trigger Retell resync if agent is already provisioned ────────
    if agent.retell_agent_id and agent.status in ('active', 'needs_attention'):
        try:
            from app.tasks.agent_tasks import update_agent_in_retell
            update_agent_in_retell.delay(agent_id, {})
            flash('Knowledge base item deleted. Agent prompt is being resynced.', 'info')
        except Exception as e:
            current_app.logger.warning(f'Failed to enqueue Retell resync after KB delete: {e}')
            flash('Knowledge base item deleted. Retell resync could not be queued.', 'warning')
    else:
        flash('Knowledge base item deleted.', 'info')
    return redirect(url_for('dashboard.knowledge_base', agent_id=agent_id))


# =========================================================================
# Website Deployment
# =========================================================================
@dashboard_bp.route('/agents/<agent_id>/deployment')
def agent_deployment(agent_id):
    """Website deployment section for an agent."""
    from app.models.core import Agent, PhoneNumber
    agent = scoped_query(Agent).filter_by(id=agent_id).first_or_404()
    phone_numbers = PhoneNumber.query.filter_by(agent_id=agent_id).filter(
        PhoneNumber.status != 'failed'
    ).all()

    checks = {
        'agent_active': agent.status == 'active',
        'has_retell_id': agent.retell_agent_id is not None,
        'has_phone_number': len(phone_numbers) > 0,
    }
    all_ready = all(checks.values())

    embed_snippets = {}
    if agent.retell_agent_id:
        retell_id = agent.retell_agent_id
        embed_snippets['web_call_button'] = f'''<script src="https://cdn.retellai.com/retell-embed.js"></script>\n<retell-web-call\n  agent-id="{retell_id}"\n  button-text="Talk to {agent.name}"\n  button-color="#4f46e5"\n  button-text-color="#ffffff">\n</retell-web-call>'''

        embed_snippets['web_call_sdk'] = f'''<script src="https://cdn.retellai.com/retell-client-sdk.js"></script>\n<script>\n  const retellClient = new RetellWebClient();\n  retellClient.startCall({{\n    agentId: "{retell_id}",\n    metadata: {{ source: "website" }}\n  }});\n</script>'''

        embed_snippets['iframe_widget'] = f'''<iframe\n  src="https://app.retellai.com/widget/{retell_id}"\n  width="400" height="600"\n  frameborder="0" allow="microphone"\n  style="border-radius:16px;box-shadow:0 20px 50px rgba(0,0,0,0.15);">\n</iframe>'''

        if phone_numbers:
            phone = phone_numbers[0].number
            embed_snippets['click_to_call'] = f'<a href="tel:{phone}" style="display:inline-flex;align-items:center;gap:8px;padding:12px 24px;background:#4f46e5;color:#fff;border-radius:12px;font-weight:600;text-decoration:none;">Call {agent.name}</a>'

    return render_template('dashboard/agent_deployment.html',
                           agent=agent,
                           phone_numbers=phone_numbers,
                           checks=checks,
                           all_ready=all_ready,
                           embed_snippets=embed_snippets)


# =========================================================================
# Phone Numbers
# =========================================================================
@dashboard_bp.route('/numbers')
def numbers_list():
    from app.models.core import PhoneNumber, Agent
    tenant_id = get_current_tenant_id()
    numbers = scoped_query(PhoneNumber).order_by(PhoneNumber.purchased_at.desc()).all()
    agents = scoped_query(Agent).filter_by(status='active').all()
    return render_template('dashboard/numbers_list.html', numbers=numbers, agents=agents)


@dashboard_bp.route('/numbers/purchase', methods=['POST'])
def numbers_purchase():
    """Purchase a new phone number via Retell API (async)."""
    from app.models.core import PhoneNumber, Subscription, PlanDefinition
    from app.tasks.agent_tasks import purchase_phone_number_async

    tenant_id = get_current_tenant_id()
    area_code = request.form.get('area_code', '415').strip()

    subscription = db.session.query(Subscription).filter_by(tenant_id=tenant_id).first()
    if subscription:
        plan = db.session.get(PlanDefinition, subscription.plan_id)
        current_count = scoped_query(PhoneNumber).filter(
            PhoneNumber.status.in_(['active', 'unassigned'])
        ).count()
        if plan and current_count >= plan.included_numbers:
            flash(f'Note: You have exceeded your plan\'s included numbers ({plan.included_numbers}). '
                  f'Additional numbers will be billed at ${plan.additional_number_rate_cents/100:.2f}/month.', 'warning')

    # Create a pending record and enqueue the async purchase
    phone = PhoneNumber(
        tenant_id=tenant_id,
        number=f'+1{area_code}XXXXXXX',
        status='pending_provision',
        area_code=area_code,
        monthly_cost_cents=800,
    )
    db.session.add(phone)
    db.session.commit()

    purchase_phone_number_async.delay(phone.id, area_code)
    flash('Phone number purchase initiated. It will appear shortly.', 'info')
    return redirect(url_for('dashboard.numbers_list'))


@dashboard_bp.route('/numbers/<number_id>/assign', methods=['POST'])
def numbers_assign(number_id):
    """Assign or unassign a phone number to/from an agent (async)."""
    from app.models.core import PhoneNumber, Agent
    from app.tasks.agent_tasks import assign_phone_number_async, unassign_phone_number_async

    tenant_id = get_current_tenant_id()
    phone = scoped_query(PhoneNumber).filter_by(id=number_id).first_or_404()
    agent_id = request.form.get('agent_id')

    if not agent_id:
        # Unassign — optimistic local update, then async Retell sync
        old_agent_id = phone.agent_id
        phone.agent_id = None
        phone.status = 'unassigned'
        db.session.commit()
        if phone.retell_number_id:
            unassign_phone_number_async.delay(phone.id)
        flash(f'Phone number {phone.number} unassigned.', 'info')
    else:
        agent = scoped_query(Agent).filter_by(id=agent_id).first()
        if not agent:
            flash('Agent not found.', 'error')
            return redirect(url_for('dashboard.numbers_list'))

        # Optimistic local update, then async Retell sync
        phone.agent_id = agent.id
        phone.status = 'active'
        db.session.commit()

        if phone.retell_number_id and agent.retell_agent_id:
            assign_phone_number_async.delay(phone.id, agent.id)

        flash(f'Phone number {phone.number} assigned to {agent.name}!', 'success')

    return redirect(url_for('dashboard.numbers_list'))


@dashboard_bp.route('/numbers/<number_id>/release', methods=['POST'])
def numbers_release(number_id):
    """Release a phone number (async)."""
    from app.models.core import PhoneNumber
    from app.tasks.agent_tasks import release_phone_number_async

    phone = scoped_query(PhoneNumber).filter_by(id=number_id).first_or_404()

    # Optimistic local update, then async Retell release
    phone.status = 'failed'
    phone.released_at = datetime.now(timezone.utc)
    phone.agent_id = None
    db.session.commit()

    if phone.retell_number_id:
        release_phone_number_async.delay(phone.id)

    flash(f'Phone number {phone.number} release initiated.', 'info')
    return redirect(url_for('dashboard.numbers_list'))


# =========================================================================
# Call Logs
# =========================================================================
@dashboard_bp.route('/calls')
def calls_list():
    from app.models.core import CallLog, Agent
    tenant_id = get_current_tenant_id()

    page = request.args.get('page', 1, type=int)
    per_page = 20

    agent_filter = request.args.get('agent_id', '')
    status_filter = request.args.get('status', '')
    sentiment_filter = request.args.get('sentiment', '')

    query = scoped_query(CallLog)
    if agent_filter:
        query = query.filter_by(agent_id=agent_filter)
    if status_filter:
        query = query.filter_by(status=status_filter)
    if sentiment_filter:
        query = query.filter_by(sentiment=sentiment_filter)

    total = query.count()
    calls = query.order_by(CallLog.created_at.desc()).offset((page - 1) * per_page).limit(per_page).all()
    agents = scoped_query(Agent).all()

    total_pages = (total + per_page - 1) // per_page

    return render_template('dashboard/calls_list.html',
                           calls=calls,
                           agents=agents,
                           page=page,
                           total_pages=total_pages,
                           total=total,
                           agent_filter=agent_filter,
                           status_filter=status_filter,
                           sentiment_filter=sentiment_filter)


@dashboard_bp.route('/calls/<call_id>')
def call_detail(call_id):
    from app.models.core import CallLog, Agent
    call = scoped_query(CallLog).filter_by(id=call_id).first_or_404()
    agent = db.session.get(Agent, call.agent_id) if call.agent_id else None
    return render_template('dashboard/call_detail.html', call=call, agent=agent)


# =========================================================================
# Billing & Subscription
# =========================================================================
@dashboard_bp.route('/billing')
def billing():
    """Comprehensive billing dashboard with usage, subscription, invoices, and top-up."""
    from app.models.core import (
        Subscription, Invoice, Payment, PhoneNumber, CallLog,
        MinuteTopupPurchase, TopupPackDefinition, PlanDefinition,
    )
    from app.services.billing_engine import get_billing_summary, get_usage_status, get_notifications

    tenant_id = get_current_tenant_id()
    summary = get_billing_summary(tenant_id)
    usage = get_usage_status(tenant_id)
    notifications = get_notifications(tenant_id, limit=5)

    # Get available top-up packs
    topup_packs = db.session.query(TopupPackDefinition).filter_by(
        is_active=True
    ).order_by(TopupPackDefinition.minutes).all()

    # Get all plans for upgrade comparison
    plans = db.session.query(PlanDefinition).filter_by(
        is_active=True
    ).order_by(PlanDefinition.price_monthly_cents).all()

    return render_template('dashboard/billing.html',
                           summary=summary,
                           usage=usage,
                           notifications=notifications,
                           topup_packs=topup_packs,
                           plans=plans)


@dashboard_bp.route('/billing/usage')
def billing_usage():
    """Detailed usage breakdown page."""
    from app.models.core import UsageRecord, UsageSummary, CallLog, Agent
    from app.services.billing_engine import get_usage_status

    tenant_id = get_current_tenant_id()
    usage = get_usage_status(tenant_id)

    # Get usage records for current period
    records = []
    if usage.get('period_start'):
        records = db.session.query(UsageRecord).filter(
            UsageRecord.tenant_id == tenant_id,
            UsageRecord.created_at >= usage['period_start'],
        ).order_by(UsageRecord.created_at.desc()).limit(100).all()

    # Get usage summaries for past periods
    summaries = db.session.query(UsageSummary).filter_by(
        tenant_id=tenant_id
    ).order_by(UsageSummary.billing_period_start.desc()).limit(12).all()

    # Per-agent usage breakdown
    agent_usage = db.session.query(
        CallLog.agent_id,
        Agent.name,
        db.func.count(CallLog.id).label('call_count'),
        db.func.coalesce(db.func.sum(CallLog.duration_seconds), 0).label('total_seconds'),
    ).join(Agent, Agent.id == CallLog.agent_id, isouter=True).filter(
        CallLog.tenant_id == tenant_id,
    ).group_by(CallLog.agent_id, Agent.name).all()

    return render_template('dashboard/billing_usage.html',
                           usage=usage,
                           records=records,
                           summaries=summaries,
                           agent_usage=agent_usage)


@dashboard_bp.route('/billing/invoices')
def billing_invoices():
    """Full invoice history page."""
    from app.models.core import Invoice
    tenant_id = get_current_tenant_id()
    invoices = scoped_query(Invoice).order_by(Invoice.created_at.desc()).all()
    return render_template('dashboard/billing_invoices.html', invoices=invoices)


@dashboard_bp.route('/billing/topup', methods=['POST'])
def billing_topup():
    """Purchase a top-up minute pack."""
    from app.models.core import TopupPackDefinition, Subscription
    from app.services import stripe_adapter
    from app.services.billing_engine import process_topup_purchase

    tenant_id = get_current_tenant_id()
    pack_id = request.form.get('pack_id')

    if not pack_id:
        flash('Please select a top-up pack.', 'error')
        return redirect(url_for('dashboard.billing'))

    pack = db.session.get(TopupPackDefinition, pack_id)
    if not pack or not pack.is_active:
        flash('Top-up pack not found.', 'error')
        return redirect(url_for('dashboard.billing'))

    sub = db.session.query(Subscription).filter_by(tenant_id=tenant_id).first()

    if sub and sub.stripe_customer_id:
        # Create Stripe checkout for the top-up
        result = stripe_adapter.create_topup_checkout(
            customer_id=sub.stripe_customer_id,
            amount_cents=pack.price_cents,
            description=f'{pack.minutes} Minute Top-Up Pack',
            success_url=url_for('dashboard.billing_topup_success', _external=True),
            cancel_url=url_for('dashboard.billing', _external=True),
            metadata={'tenant_id': tenant_id, 'pack_id': pack_id},
        )
        if result['status'] == 'success':
            return redirect(result['data']['url'])
        else:
            flash(f'Payment setup failed: {result.get("message", "Unknown error")}', 'error')
    else:
        # Mock mode — credit directly
        result = process_topup_purchase(tenant_id, pack_id)
        if result['status'] == 'success':
            flash(f'{result["minutes_added"]} minutes added to your account!', 'success')
        else:
            flash(f'Top-up failed: {result.get("message", "Unknown error")}', 'error')

    return redirect(url_for('dashboard.billing'))


@dashboard_bp.route('/billing/topup/success')
def billing_topup_success():
    """Handle successful top-up payment return from Stripe."""
    flash('Top-up minutes purchased successfully!', 'success')
    return redirect(url_for('dashboard.billing'))


@dashboard_bp.route('/billing/upgrade', methods=['POST'])
def billing_upgrade():
    """Initiate a plan upgrade via Stripe Checkout."""
    from app.models.core import Subscription, PlanDefinition
    from app.services import stripe_adapter

    tenant_id = get_current_tenant_id()
    new_plan_id = request.form.get('plan_id')

    if not new_plan_id:
        flash('Please select a plan.', 'error')
        return redirect(url_for('dashboard.billing'))

    new_plan = db.session.get(PlanDefinition, new_plan_id)
    if not new_plan:
        flash('Plan not found.', 'error')
        return redirect(url_for('dashboard.billing'))

    sub = db.session.query(Subscription).filter_by(tenant_id=tenant_id).first()

    if sub and sub.stripe_subscription_id and new_plan.stripe_price_id:
        # Upgrade existing subscription
        result = stripe_adapter.update_subscription(
            sub.stripe_subscription_id,
            new_plan.stripe_price_id,
        )
        if result['status'] == 'success':
            sub.plan_id = new_plan.id
            db.session.commit()
            flash(f'Upgraded to {new_plan.name}!', 'success')
        else:
            flash(f'Upgrade failed: {result.get("message", "Unknown error")}', 'error')
    elif sub and sub.stripe_customer_id and new_plan.stripe_price_id:
        # Create new subscription
        result = stripe_adapter.create_checkout_session(
            customer_id=sub.stripe_customer_id,
            price_id=new_plan.stripe_price_id,
            success_url=url_for('dashboard.billing', _external=True),
            cancel_url=url_for('dashboard.billing', _external=True),
            metadata={'tenant_id': tenant_id, 'plan_id': new_plan_id},
        )
        if result['status'] == 'success':
            return redirect(result['data']['url'])
        else:
            flash(f'Checkout failed: {result.get("message", "Unknown error")}', 'error')
    else:
        # Mock mode — upgrade directly
        if sub:
            sub.plan_id = new_plan.id
        else:
            sub = Subscription(
                tenant_id=tenant_id,
                plan_id=new_plan.id,
                status='active',
            )
            db.session.add(sub)
        db.session.commit()
        flash(f'Upgraded to {new_plan.name}!', 'success')

    return redirect(url_for('dashboard.billing'))


@dashboard_bp.route('/billing/cancel', methods=['POST'])
def billing_cancel():
    """Cancel subscription at period end."""
    from app.models.core import Subscription
    from app.services import stripe_adapter

    tenant_id = get_current_tenant_id()
    sub = db.session.query(Subscription).filter_by(tenant_id=tenant_id).first()

    if not sub:
        flash('No active subscription found.', 'error')
        return redirect(url_for('dashboard.billing'))

    if sub.stripe_subscription_id:
        result = stripe_adapter.cancel_subscription(sub.stripe_subscription_id)
        if result['status'] == 'success':
            sub.cancel_at_period_end = True
            db.session.commit()
            flash('Subscription will be canceled at the end of the current billing period.', 'info')
        else:
            flash(f'Cancellation failed: {result.get("message", "Unknown error")}', 'error')
    else:
        sub.cancel_at_period_end = True
        db.session.commit()
        flash('Subscription will be canceled at the end of the current billing period.', 'info')

    return redirect(url_for('dashboard.billing'))


@dashboard_bp.route('/billing/reactivate', methods=['POST'])
def billing_reactivate():
    """Reactivate a subscription that was set to cancel."""
    from app.models.core import Subscription
    from app.services import stripe_adapter

    tenant_id = get_current_tenant_id()
    sub = db.session.query(Subscription).filter_by(tenant_id=tenant_id).first()

    if not sub:
        flash('No subscription found.', 'error')
        return redirect(url_for('dashboard.billing'))

    if sub.stripe_subscription_id:
        result = stripe_adapter.reactivate_subscription(sub.stripe_subscription_id)
        if result['status'] == 'success':
            sub.cancel_at_period_end = False
            db.session.commit()
            flash('Subscription reactivated!', 'success')
        else:
            flash(f'Reactivation failed: {result.get("message", "Unknown error")}', 'error')
    else:
        sub.cancel_at_period_end = False
        db.session.commit()
        flash('Subscription reactivated!', 'success')

    return redirect(url_for('dashboard.billing'))


@dashboard_bp.route('/billing/manage')
def billing_manage():
    """Redirect to Stripe Customer Portal for payment method management."""
    from app.models.core import Subscription
    from app.services import stripe_adapter

    tenant_id = get_current_tenant_id()
    sub = db.session.query(Subscription).filter_by(tenant_id=tenant_id).first()

    if sub and sub.stripe_customer_id:
        result = stripe_adapter.create_billing_portal_session(
            sub.stripe_customer_id,
            return_url=url_for('dashboard.billing', _external=True),
        )
        if result['status'] == 'success':
            return redirect(result['data']['url'])

    flash('Billing portal is not available. Please contact support.', 'warning')
    return redirect(url_for('dashboard.billing'))


@dashboard_bp.route('/pricing')
def pricing():
    """Public-facing pricing page (also accessible when logged in)."""
    from app.models.core import PlanDefinition, Subscription
    tenant_id = get_current_tenant_id()
    plans = db.session.query(PlanDefinition).filter_by(
        is_active=True
    ).order_by(PlanDefinition.price_monthly_cents).all()
    sub = db.session.query(Subscription).filter_by(tenant_id=tenant_id).first()
    current_plan_id = sub.plan_id if sub else None
    return render_template('dashboard/pricing.html',
                           plans=plans,
                           current_plan_id=current_plan_id)


# =========================================================================
# Settings
# =========================================================================
@dashboard_bp.route('/settings')
def settings():
    from app.models.core import Organization
    tenant_id = get_current_tenant_id()
    org = db.session.query(Organization).filter_by(tenant_id=tenant_id).first()
    return render_template('dashboard/settings.html', org=org)


# =========================================================================
# Outbound Calling — Contact Lists  (gated by FEATURE_CAMPAIGNS)
# =========================================================================
@dashboard_bp.route('/contacts')
def contacts_list():
    if not current_app.config.get('FEATURE_CAMPAIGNS'):
        flash('Outbound campaigns are not enabled for this deployment.', 'info')
        return redirect(url_for('dashboard.home'))
    """List all contact lists for the tenant."""
    from app.models.core import ContactList
    tenant_id = get_current_tenant_id()
    lists = scoped_query(ContactList).order_by(ContactList.created_at.desc()).all()
    return render_template('dashboard/contacts_list.html', contact_lists=lists)


@dashboard_bp.route('/contacts/import', methods=['GET', 'POST'])
def contacts_import():
    if not current_app.config.get('FEATURE_CAMPAIGNS'):
        flash('Outbound campaigns are not enabled for this deployment.', 'info')
        return redirect(url_for('dashboard.home'))
    """Import contacts from a CSV file."""
    from app.services.campaign_engine import import_csv

    if request.method == 'GET':
        return render_template('dashboard/contacts_import.html')

    tenant_id = get_current_tenant_id()
    list_name = request.form.get('list_name', '').strip()
    description = request.form.get('description', '').strip()
    csv_file = request.files.get('csv_file')

    if not list_name:
        flash('Please provide a name for the contact list.', 'error')
        return redirect(url_for('dashboard.contacts_import'))

    if not csv_file or not csv_file.filename:
        flash('Please upload a CSV file.', 'error')
        return redirect(url_for('dashboard.contacts_import'))

    try:
        file_content = csv_file.read().decode('utf-8-sig')
        contact_list, stats = import_csv(tenant_id, list_name, file_content, description)
        flash(
            f'Imported {stats["imported"]} contacts. '
            f'{stats["invalid"]} invalid, {stats["duplicates"]} duplicates, '
            f'{stats["suppressed"]} suppressed.',
            'success'
        )
        return redirect(url_for('dashboard.contacts_detail', list_id=contact_list.id))
    except ValueError as e:
        flash(str(e), 'error')
        return redirect(url_for('dashboard.contacts_import'))
    except Exception as e:
        logger.exception(f"CSV import error: {e}")
        flash('An error occurred during import. Please check your CSV format.', 'error')
        return redirect(url_for('dashboard.contacts_import'))


@dashboard_bp.route('/contacts/<list_id>')
def contacts_detail(list_id):
    """View contacts in a specific list."""
    if not current_app.config.get('FEATURE_CAMPAIGNS'):
        flash('Outbound campaigns are not enabled for this deployment.', 'info')
        return redirect(url_for('dashboard.home'))
    from app.models.core import ContactList, Contact
    tenant_id = get_current_tenant_id()
    contact_list = scoped_query(ContactList).filter_by(id=list_id).first_or_404()

    page = request.args.get('page', 1, type=int)
    per_page = 50
    status_filter = request.args.get('status', '')

    query = Contact.query.filter_by(contact_list_id=list_id)
    if status_filter:
        query = query.filter_by(status=status_filter)

    total = query.count()
    contacts = query.order_by(Contact.created_at.desc()).offset(
        (page - 1) * per_page
    ).limit(per_page).all()

    total_pages = (total + per_page - 1) // per_page

    return render_template('dashboard/contacts_detail.html',
                           contact_list=contact_list,
                           contacts=contacts,
                           page=page,
                           total_pages=total_pages,
                           total=total,
                           status_filter=status_filter)


@dashboard_bp.route('/contacts/<list_id>/delete', methods=['POST'])
def contacts_delete(list_id):
    """Delete a contact list and all its contacts."""
    if not current_app.config.get('FEATURE_CAMPAIGNS'):
        flash('Outbound campaigns are not enabled for this deployment.', 'info')
        return redirect(url_for('dashboard.home'))
    from app.models.core import ContactList
    contact_list = scoped_query(ContactList).filter_by(id=list_id).first_or_404()
    db.session.delete(contact_list)
    db.session.commit()
    flash(f'Contact list "{contact_list.name}" deleted.', 'success')
    return redirect(url_for('dashboard.contacts_list'))


@dashboard_bp.route('/contacts/suppress', methods=['POST'])
def contacts_suppress():
    """Manually add a phone number to the suppression list."""
    if not current_app.config.get('FEATURE_CAMPAIGNS'):
        flash('Outbound campaigns are not enabled for this deployment.', 'info')
        return redirect(url_for('dashboard.home'))
    from app.services.campaign_engine import suppress_number, normalize_phone
    tenant_id = get_current_tenant_id()
    phone = request.form.get('phone_number', '').strip()
    normalized = normalize_phone(phone)
    if not normalized:
        flash('Invalid phone number format.', 'error')
    else:
        count = suppress_number(tenant_id, normalized)
        if count > 0:
            flash(f'Number {normalized} suppressed across {count} contact(s).', 'success')
        else:
            flash(f'Number {normalized} not found in any contact list.', 'warning')
    return redirect(request.referrer or url_for('dashboard.contacts_list'))


# =========================================================================
# Outbound Calling — Campaigns  (gated by FEATURE_CAMPAIGNS)
# =========================================================================
@dashboard_bp.route('/campaigns')
def campaigns_list():
    if not current_app.config.get('FEATURE_CAMPAIGNS'):
        flash('Outbound campaigns are not enabled for this deployment.', 'info')
        return redirect(url_for('dashboard.home'))
    """List all campaigns for the tenant."""
    from app.models.core import Campaign
    status_filter = request.args.get('status', '')
    query = scoped_query(Campaign)
    if status_filter:
        query = query.filter_by(status=status_filter)
    campaigns = query.order_by(Campaign.created_at.desc()).all()
    return render_template('dashboard/campaigns_list.html',
                           campaigns=campaigns,
                           status_filter=status_filter)


@dashboard_bp.route('/campaigns/new', methods=['GET', 'POST'])
def campaigns_new():
    """Create a new outbound campaign."""
    if not current_app.config.get('FEATURE_CAMPAIGNS'):
        flash('Outbound campaigns are not enabled for this deployment.', 'info')
        return redirect(url_for('dashboard.home'))
    from app.models.core import Agent, ContactList, PhoneNumber, Campaign

    tenant_id = get_current_tenant_id()

    if request.method == 'GET':
        agents = scoped_query(Agent).filter(
            Agent.status == 'active',
            Agent.mode == 'outbound',
        ).all()
        contact_lists = scoped_query(ContactList).all()
        phone_numbers = scoped_query(PhoneNumber).filter(
            PhoneNumber.status.in_(['active', 'unassigned'])
        ).all()
        return render_template('dashboard/campaigns_new.html',
                               agents=agents,
                               contact_lists=contact_lists,
                               phone_numbers=phone_numbers)

    # POST — create the campaign
    name = request.form.get('name', '').strip()
    agent_id = request.form.get('agent_id', '')
    contact_list_id = request.form.get('contact_list_id', '')
    caller_id_number_id = request.form.get('caller_id_number_id', '')

    if not all([name, agent_id, contact_list_id, caller_id_number_id]):
        flash('All fields are required.', 'error')
        return redirect(url_for('dashboard.campaigns_new'))

    # Scheduling
    scheduled_date = request.form.get('scheduled_date', '')
    scheduled_time = request.form.get('scheduled_time', '')
    window_start = request.form.get('window_start_min', '540')
    window_end = request.form.get('window_end_min', '1260')
    max_retries = request.form.get('max_retries', '2')

    # Allowed days
    allowed_days = request.form.getlist('allowed_days')
    if not allowed_days:
        allowed_days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday']

    scheduled_at = None
    if scheduled_date and scheduled_time:
        try:
            scheduled_at = datetime.strptime(
                f"{scheduled_date} {scheduled_time}", "%Y-%m-%d %H:%M"
            ).replace(tzinfo=timezone.utc)
        except ValueError:
            flash('Invalid date/time format.', 'error')
            return redirect(url_for('dashboard.campaigns_new'))

    campaign = Campaign(
        tenant_id=tenant_id,
        name=name,
        agent_id=agent_id,
        contact_list_id=contact_list_id,
        caller_id_number_id=caller_id_number_id,
        status='draft',
        scheduled_at=scheduled_at,
        window_start_min=int(window_start),
        window_end_min=int(window_end),
        allowed_days=allowed_days,
        max_retries=int(max_retries),
    )
    db.session.add(campaign)
    db.session.commit()

    flash(f'Campaign "{name}" created as draft.', 'success')
    return redirect(url_for('dashboard.campaign_detail', campaign_id=campaign.id))


@dashboard_bp.route('/campaigns/<campaign_id>')
def campaign_detail(campaign_id):
    """View campaign details and analytics."""
    if not current_app.config.get('FEATURE_CAMPAIGNS'):
        flash('Outbound campaigns are not enabled for this deployment.', 'info')
        return redirect(url_for('dashboard.home'))
    from app.models.core import Campaign, CampaignTask
    campaign = scoped_query(Campaign).filter_by(id=campaign_id).first_or_404()

    # Task stats
    tasks = CampaignTask.query.filter_by(campaign_id=campaign_id).all()
    stats = {
        'total': len(tasks),
        'pending': sum(1 for t in tasks if t.status == 'pending'),
        'queued': sum(1 for t in tasks if t.status == 'queued'),
        'calling': sum(1 for t in tasks if t.status == 'calling'),
        'completed': sum(1 for t in tasks if t.status == 'completed'),
        'failed': sum(1 for t in tasks if t.status == 'failed'),
        'skipped': sum(1 for t in tasks if t.status == 'skipped'),
    }

    # Disposition breakdown
    dispositions = {}
    for t in tasks:
        if t.disposition:
            dispositions[t.disposition] = dispositions.get(t.disposition, 0) + 1

    # Recent tasks with contact info
    recent_tasks = CampaignTask.query.filter_by(
        campaign_id=campaign_id
    ).order_by(CampaignTask.updated_at.desc()).limit(20).all()

    return render_template('dashboard/campaign_detail.html',
                           campaign=campaign,
                           stats=stats,
                           dispositions=dispositions,
                           recent_tasks=recent_tasks)


@dashboard_bp.route('/campaigns/<campaign_id>/launch', methods=['POST'])
def campaign_launch(campaign_id):
    """Compile and launch a campaign via Retell Batch Call API (async)."""
    if not current_app.config.get('FEATURE_CAMPAIGNS'):
        flash('Outbound campaigns are not enabled for this deployment.', 'info')
        return redirect(url_for('dashboard.home'))
    from app.models.core import Campaign
    from app.tasks.agent_tasks import launch_campaign_async

    campaign = scoped_query(Campaign).filter_by(id=campaign_id).first_or_404()

    if campaign.status not in ('draft', 'paused'):
        flash('Campaign can only be launched from draft or paused state.', 'error')
        return redirect(url_for('dashboard.campaign_detail', campaign_id=campaign_id))

    # Verify the agent is active and has a Retell ID
    if not campaign.agent or campaign.agent.status != 'active' or not campaign.agent.retell_agent_id:
        flash('The assigned agent must be active and provisioned in Retell.', 'error')
        return redirect(url_for('dashboard.campaign_detail', campaign_id=campaign_id))

    # Verify the caller ID number is active
    if not campaign.caller_id_number or campaign.caller_id_number.status != 'active':
        flash('The caller ID number must be active.', 'error')
        return redirect(url_for('dashboard.campaign_detail', campaign_id=campaign_id))

    # Optimistic status update, then async dispatch
    campaign.status = 'launching'
    db.session.commit()
    launch_campaign_async.delay(campaign_id)
    flash('Campaign launch initiated. It will start shortly.', 'info')
    return redirect(url_for('dashboard.campaign_detail', campaign_id=campaign_id))


@dashboard_bp.route('/campaigns/<campaign_id>/pause', methods=['POST'])
def campaign_pause(campaign_id):
    """Pause a running campaign."""
    if not current_app.config.get('FEATURE_CAMPAIGNS'):
        flash('Outbound campaigns are not enabled for this deployment.', 'info')
        return redirect(url_for('dashboard.home'))
    from app.models.core import Campaign
    campaign = scoped_query(Campaign).filter_by(id=campaign_id).first_or_404()

    if campaign.status != 'running':
        flash('Only running campaigns can be paused.', 'error')
        return redirect(url_for('dashboard.campaign_detail', campaign_id=campaign_id))

    campaign.status = 'paused'
    db.session.commit()
    flash('Campaign paused.', 'success')
    return redirect(url_for('dashboard.campaign_detail', campaign_id=campaign_id))


@dashboard_bp.route('/campaigns/<campaign_id>/cancel', methods=['POST'])
def campaign_cancel(campaign_id):
    """Cancel a campaign."""
    if not current_app.config.get('FEATURE_CAMPAIGNS'):
        flash('Outbound campaigns are not enabled for this deployment.', 'info')
        return redirect(url_for('dashboard.home'))
    from app.models.core import Campaign, CampaignTask
    campaign = scoped_query(Campaign).filter_by(id=campaign_id).first_or_404()

    if campaign.status in ('completed', 'canceled'):
        flash('Campaign is already finished.', 'error')
        return redirect(url_for('dashboard.campaign_detail', campaign_id=campaign_id))

    # Mark pending/queued tasks as skipped
    pending_tasks = CampaignTask.query.filter(
        CampaignTask.campaign_id == campaign_id,
        CampaignTask.status.in_(['pending', 'queued']),
    ).all()
    for task in pending_tasks:
        task.status = 'skipped'

    campaign.status = 'canceled'
    campaign.completed_at = datetime.now(timezone.utc)
    db.session.commit()
    flash('Campaign canceled.', 'success')
    return redirect(url_for('dashboard.campaign_detail', campaign_id=campaign_id))


# =========================================================================
# Outbound Calling — One-Off Calls
# =========================================================================
@dashboard_bp.route('/outbound/call', methods=['GET', 'POST'])
def outbound_call():
    """Initiate a one-off outbound call."""
    if not current_app.config.get('FEATURE_CAMPAIGNS'):
        flash('Outbound calling is not enabled for this deployment.', 'info')
        return redirect(url_for('dashboard.home'))
    from app.models.core import Agent, PhoneNumber
    from app.services.campaign_engine import normalize_phone, is_suppressed
    from app.tasks.agent_tasks import outbound_call_async

    tenant_id = get_current_tenant_id()

    if request.method == 'GET':
        agents = scoped_query(Agent).filter(
            Agent.status == 'active',
            Agent.mode == 'outbound',
        ).all()
        phone_numbers = scoped_query(PhoneNumber).filter(
            PhoneNumber.status.in_(['active', 'unassigned'])
        ).all()
        return render_template('dashboard/outbound_call.html',
                               agents=agents,
                               phone_numbers=phone_numbers)

    # POST — initiate the call
    agent_id = request.form.get('agent_id', '')
    from_number_id = request.form.get('from_number_id', '')
    to_number_raw = request.form.get('to_number', '').strip()

    to_number = normalize_phone(to_number_raw)
    if not to_number:
        flash('Invalid phone number format. Use E.164 (e.g., +14155551234).', 'error')
        return redirect(url_for('dashboard.outbound_call'))

    # Check suppression
    if is_suppressed(tenant_id, to_number):
        flash('This number is on the do-not-call suppression list.', 'error')
        return redirect(url_for('dashboard.outbound_call'))

    agent = scoped_query(Agent).filter_by(id=agent_id).first()
    if not agent or not agent.retell_agent_id:
        flash('Selected agent is not active or not provisioned.', 'error')
        return redirect(url_for('dashboard.outbound_call'))

    phone = scoped_query(PhoneNumber).filter_by(id=from_number_id).first()
    if not phone:
        flash('Selected caller ID number not found.', 'error')
        return redirect(url_for('dashboard.outbound_call'))

    outbound_call_async.delay(phone.number, to_number, agent.retell_agent_id, tenant_id)
    flash('Call initiated. Check call logs for results.', 'info')
    return redirect(url_for('dashboard.outbound_call'))


# =========================================================================
# Tools & External Actions — Integrations Hub
# =========================================================================
@dashboard_bp.route('/integrations')
def integrations():
    """Integrations hub — shows tool catalog and tenant connections."""
    from app.models.core import ToolTemplate, TenantToolConnection
    tenant_id = get_current_tenant_id()

    templates = db.session.query(ToolTemplate).filter_by(is_active=True).order_by(ToolTemplate.category).all()
    connections = db.session.query(TenantToolConnection).filter_by(tenant_id=tenant_id).all()
    connection_map = {c.tool_template_id: c for c in connections}

    # Group templates by category
    categories = {}
    for t in templates:
        cat = t.category
        if cat not in categories:
            categories[cat] = []
        categories[cat].append(t)

    # Build credential status for email/sms connections
    credential_status_map = {}
    from app.services.credential_resolver import get_credential_status
    for c in connections:
        template_obj = c.template
        if template_obj and template_obj.category in ('email', 'sms'):
            credential_status_map[c.id] = get_credential_status(template_obj.category, c)

    return render_template('dashboard/integrations.html',
                           categories=categories,
                           connection_map=connection_map,
                           credential_status_map=credential_status_map,
                           templates=templates)


@dashboard_bp.route('/integrations/connect/<template_id>', methods=['POST'])
def integration_connect(template_id):
    """Connect a tool template for the current tenant.

    For OAuth-based integrations (e.g. Google Calendar), this initiates the
    OAuth flow.  For API-key integrations (SendGrid, Twilio), the connection
    is activated immediately using platform-level credentials.
    """
    from app.models.core import ToolTemplate, TenantToolConnection
    tenant_id = get_current_tenant_id()

    template = db.session.get(ToolTemplate, template_id)
    if not template:
        flash('Tool template not found.', 'error')
        return redirect(url_for('dashboard.integrations'))

    # Check access tier
    if template.access_tier in ('dfy_only', 'admin_approved'):
        flash(f'"{template.name}" requires professional setup. Please contact us or use the Done For You service.', 'warning')
        return redirect(url_for('dashboard.integrations'))

    # Ensure or create the connection record
    existing = db.session.query(TenantToolConnection).filter_by(
        tenant_id=tenant_id, tool_template_id=template_id
    ).first()

    if not existing:
        existing = TenantToolConnection(
            tenant_id=tenant_id,
            tool_template_id=template_id,
            status='disconnected',
            credential_mode='platform',
            config={},
        )
        db.session.add(existing)
        db.session.commit()

    # OAuth-based integrations: redirect to provider
    if template.category == 'calendar':
        from app.services import calendar_adapter
        import json as _json
        state = _json.dumps({'tenant_id': tenant_id, 'connection_id': existing.id})
        oauth_url = calendar_adapter.build_oauth_url(state)
        return redirect(oauth_url)

    # API-key integrations (email, sms): connect using platform credentials by default
    existing.status = 'connected'
    existing.credential_mode = existing.credential_mode or 'platform'
    existing.connected_at = datetime.now(timezone.utc)
    db.session.commit()
    flash(f'"{template.name}" connected successfully using platform credentials. You can switch to your own credentials in the settings below.', 'success')
    return redirect(url_for('dashboard.integrations'))


@dashboard_bp.route('/integrations/disconnect/<connection_id>', methods=['POST'])
def integration_disconnect(connection_id):
    """Disconnect a tool connection and clear stored credentials."""
    from app.models.core import TenantToolConnection
    from app.services.credential_manager import clear_credentials
    tenant_id = get_current_tenant_id()

    conn = db.session.get(TenantToolConnection, connection_id)
    if not conn or conn.tenant_id != tenant_id:
        flash('Connection not found.', 'error')
        return redirect(url_for('dashboard.integrations'))

    # Clear encrypted credentials, reset credential_mode, and mark disconnected
    clear_credentials(connection_id, tenant_id)
    conn.credential_mode = 'platform'
    db.session.commit()
    flash('Integration disconnected and credentials removed.', 'success')
    return redirect(url_for('dashboard.integrations'))


@dashboard_bp.route('/integrations/configure/<connection_id>', methods=['POST'])
def integration_configure(connection_id):
    """Update configuration for a tool connection."""
    from app.models.core import TenantToolConnection
    tenant_id = get_current_tenant_id()

    conn = db.session.get(TenantToolConnection, connection_id)
    if not conn or conn.tenant_id != tenant_id:
        flash('Connection not found.', 'error')
        return redirect(url_for('dashboard.integrations'))

    # Collect all form fields that start with 'config_'
    config = conn.config or {}
    for key, value in request.form.items():
        if key.startswith('config_'):
            config[key.replace('config_', '')] = value
    conn.config = config
    db.session.commit()
    flash('Configuration updated.', 'success')
    return redirect(url_for('dashboard.integrations'))


# =========================================================================
# OAuth Callbacks
# =========================================================================
@dashboard_bp.route('/integrations/google-calendar/callback')
def google_calendar_callback():
    """Handle the Google OAuth 2.0 callback after user grants calendar access.

    Exchanges the authorization code for tokens, encrypts and stores them,
    and marks the connection as connected.
    """
    import json as _json
    from app.models.core import TenantToolConnection
    from app.services import calendar_adapter
    from app.services.credential_manager import store_credentials

    error = request.args.get('error', '')
    if error:
        flash(f'Google Calendar authorization failed: {error}', 'error')
        return redirect(url_for('dashboard.integrations'))

    code = request.args.get('code', '')
    state_raw = request.args.get('state', '{}')

    try:
        state = _json.loads(state_raw)
    except _json.JSONDecodeError:
        flash('Invalid OAuth state.', 'error')
        return redirect(url_for('dashboard.integrations'))

    tenant_id = state.get('tenant_id', '')
    connection_id = state.get('connection_id', '')

    # Verify the connection belongs to the current tenant
    current_tenant = get_current_tenant_id()
    if tenant_id != current_tenant:
        flash('Tenant mismatch. Please try connecting again.', 'error')
        return redirect(url_for('dashboard.integrations'))

    # Exchange the code for tokens
    result = calendar_adapter.exchange_code(code)
    if result.get('status') != 'success':
        flash(f'Failed to connect Google Calendar: {result.get("message", "Unknown error")}', 'error')
        return redirect(url_for('dashboard.integrations'))

    # Store encrypted credentials
    credentials = result['credentials']
    stored = store_credentials(connection_id, tenant_id, credentials)
    if not stored:
        flash('Failed to save credentials. Please try again.', 'error')
        return redirect(url_for('dashboard.integrations'))

    flash('Google Calendar connected successfully!', 'success')
    return redirect(url_for('dashboard.integrations'))


# =========================================================================
# Integration Credential Management & Test Connection
# =========================================================================
@dashboard_bp.route('/integrations/<connection_id>/save-credentials', methods=['POST'])
def integration_save_credentials(connection_id):
    """Save tenant-provided API credentials for email or SMS.

    Encrypts the credentials via CredentialManager and sets credential_mode='tenant'.
    Never exposes secrets back to the UI after save.
    """
    from app.models.core import TenantToolConnection
    from app.services.credential_manager import store_credentials
    tenant_id = get_current_tenant_id()

    conn = db.session.get(TenantToolConnection, connection_id)
    if not conn or conn.tenant_id != tenant_id:
        flash('Connection not found.', 'error')
        return redirect(url_for('dashboard.integrations'))

    template = conn.template
    category = template.category if template else ''

    if category == 'email':
        api_key = request.form.get('sendgrid_api_key', '').strip()
        from_email = request.form.get('from_email', '').strip()
        from_name = request.form.get('from_name', '').strip()
        if not api_key:
            flash('API key is required.', 'error')
            return redirect(url_for('dashboard.integrations'))
        creds = {'api_key': api_key}
        if from_email:
            creds['from_email'] = from_email
        if from_name:
            creds['from_name'] = from_name

    elif category == 'sms':
        account_sid = request.form.get('twilio_account_sid', '').strip()
        auth_token = request.form.get('twilio_auth_token', '').strip()
        phone_number = request.form.get('twilio_phone_number', '').strip()
        if not account_sid or not auth_token:
            flash('Account SID and Auth Token are required.', 'error')
            return redirect(url_for('dashboard.integrations'))
        creds = {'account_sid': account_sid, 'auth_token': auth_token}
        if phone_number:
            creds['phone_number'] = phone_number

    else:
        flash('Credential management is not supported for this integration type.', 'error')
        return redirect(url_for('dashboard.integrations'))

    # Store encrypted and update mode
    stored = store_credentials(connection_id, tenant_id, creds)
    if stored:
        conn.credential_mode = 'tenant'
        db.session.commit()
        flash(f'{template.name} credentials saved and encrypted. Your own credentials are now active.', 'success')
    else:
        flash('Failed to save credentials. Please try again.', 'error')

    return redirect(url_for('dashboard.integrations'))


@dashboard_bp.route('/integrations/<connection_id>/switch-mode', methods=['POST'])
def integration_switch_mode(connection_id):
    """Switch between platform and tenant credential modes."""
    from app.models.core import TenantToolConnection
    tenant_id = get_current_tenant_id()

    conn = db.session.get(TenantToolConnection, connection_id)
    if not conn or conn.tenant_id != tenant_id:
        flash('Connection not found.', 'error')
        return redirect(url_for('dashboard.integrations'))

    new_mode = request.form.get('mode', 'platform')
    if new_mode not in ('platform', 'tenant'):
        new_mode = 'platform'

    conn.credential_mode = new_mode
    db.session.commit()

    if new_mode == 'platform':
        flash('Switched to platform-managed credentials. Usage is included in your plan.', 'success')
    else:
        flash('Switched to your own credentials. Please enter your API keys below.', 'info')

    return redirect(url_for('dashboard.integrations'))


@dashboard_bp.route('/integrations/<connection_id>/test', methods=['POST'])
def integration_test_connection(connection_id):
    """Test the connection for a tool integration.

    Tests whichever credentials are currently active (tenant or platform).
    Returns a flash message with the result.
    """
    from app.models.core import TenantToolConnection
    from app.services.credential_resolver import resolve_email_credentials, resolve_sms_credentials
    tenant_id = get_current_tenant_id()

    conn = db.session.get(TenantToolConnection, connection_id)
    if not conn or conn.tenant_id != tenant_id:
        flash('Connection not found.', 'error')
        return redirect(url_for('dashboard.integrations'))

    template = conn.template
    category = template.category if template else ''

    if category == 'email':
        creds, source = resolve_email_credentials(conn)
        from app.services import email_adapter
        result = email_adapter.test_connection(credentials=creds)
    elif category == 'sms':
        creds, source = resolve_sms_credentials(conn)
        from app.services import sms_adapter
        result = sms_adapter.test_connection(credentials=creds)
    else:
        flash('Connection testing is not available for this integration type.', 'info')
        return redirect(url_for('dashboard.integrations'))

    if result.get('status') == 'ok':
        flash(f'Connection test passed ({source} credentials): {result.get("message", "OK")}', 'success')
    else:
        flash(f'Connection test failed ({source} credentials): {result.get("message", "Unknown error")}', 'error')

    return redirect(url_for('dashboard.integrations'))


@dashboard_bp.route('/integrations/<connection_id>/clear-credentials', methods=['POST'])
def integration_clear_credentials(connection_id):
    """Clear tenant-provided credentials and revert to platform mode."""
    from app.models.core import TenantToolConnection
    from app.services.credential_manager import clear_credentials
    tenant_id = get_current_tenant_id()

    conn = db.session.get(TenantToolConnection, connection_id)
    if not conn or conn.tenant_id != tenant_id:
        flash('Connection not found.', 'error')
        return redirect(url_for('dashboard.integrations'))

    clear_credentials(connection_id, tenant_id)
    conn.credential_mode = 'platform'
    conn.status = 'connected'  # Keep connected via platform credentials
    db.session.commit()
    flash('Your credentials have been removed. Reverted to platform-managed credentials.', 'success')
    return redirect(url_for('dashboard.integrations'))


# =========================================================================
# Tools & External Actions — Agent Tools Tab
# =========================================================================
@dashboard_bp.route('/agents/<agent_id>/tools')
def agent_tools(agent_id):
    """Agent tools tab — assign tools to an agent."""
    from app.models.core import Agent, TenantToolConnection, AgentToolAssignment, ToolTemplate
    tenant_id = get_current_tenant_id()

    agent = scoped_query(Agent).filter_by(id=agent_id).first()
    if not agent:
        flash('Agent not found.', 'error')
        return redirect(url_for('dashboard.agents_list'))

    # Get all connected tools for this tenant
    connections = db.session.query(TenantToolConnection).filter_by(
        tenant_id=tenant_id, status='connected'
    ).all()

    # Get current assignments for this agent
    assignments = db.session.query(AgentToolAssignment).filter_by(agent_id=agent_id).all()
    assignment_map = {a.connection_id: a for a in assignments}

    return render_template('dashboard/agent_tools.html',
                           agent=agent,
                           connections=connections,
                           assignments=assignments,
                           assignment_map=assignment_map)


@dashboard_bp.route('/agents/<agent_id>/tools/assign', methods=['POST'])
def agent_tool_assign(agent_id):
    """Assign or update a tool for an agent."""
    from app.models.core import Agent, TenantToolConnection, AgentToolAssignment
    tenant_id = get_current_tenant_id()

    agent = scoped_query(Agent).filter_by(id=agent_id).first()
    if not agent:
        flash('Agent not found.', 'error')
        return redirect(url_for('dashboard.agents_list'))

    connection_id = request.form.get('connection_id')
    conn = db.session.get(TenantToolConnection, connection_id)
    if not conn or conn.tenant_id != tenant_id:
        flash('Invalid connection.', 'error')
        return redirect(url_for('dashboard.agent_tools', agent_id=agent_id))

    template = conn.template
    function_name = request.form.get('function_name', template.slug if template else 'unknown')
    tool_type = template.tool_type if template else 'post_call'

    existing = db.session.query(AgentToolAssignment).filter_by(
        agent_id=agent_id, connection_id=connection_id
    ).first()

    if existing:
        existing.is_active = True
        existing.function_name = function_name
        existing.description_for_llm = request.form.get('description', template.default_description_for_llm if template else '')
    else:
        assignment = AgentToolAssignment(
            agent_id=agent_id,
            connection_id=connection_id,
            tool_type=tool_type,
            function_name=function_name,
            description_for_llm=request.form.get('description', template.default_description_for_llm if template else ''),
            parameters_schema=template.default_parameters_schema if template else None,
            is_active=True,
        )
        db.session.add(assignment)

    db.session.commit()
    flash(f'Tool "{function_name}" assigned to agent.', 'success')
    return redirect(url_for('dashboard.agent_tools', agent_id=agent_id))


@dashboard_bp.route('/agents/<agent_id>/tools/remove/<assignment_id>', methods=['POST'])
def agent_tool_remove(agent_id, assignment_id):
    """Remove a tool assignment from an agent."""
    from app.models.core import AgentToolAssignment
    tenant_id = get_current_tenant_id()

    assignment = db.session.get(AgentToolAssignment, assignment_id)
    if not assignment or assignment.agent_id != agent_id:
        flash('Assignment not found.', 'error')
        return redirect(url_for('dashboard.agent_tools', agent_id=agent_id))

    assignment.is_active = False
    db.session.commit()
    flash('Tool removed from agent.', 'success')
    return redirect(url_for('dashboard.agent_tools', agent_id=agent_id))


# =========================================================================
# Tools & External Actions — Action Logs
# =========================================================================
@dashboard_bp.route('/logs/actions')
def action_logs():
    """Action logs — debugging interface for tool executions."""
    from app.models.core import ActionLog, Agent
    tenant_id = get_current_tenant_id()

    status_filter = request.args.get('status', '')
    agent_filter = request.args.get('agent_id', '')
    type_filter = request.args.get('type', '')

    query = db.session.query(ActionLog).filter_by(tenant_id=tenant_id)
    if status_filter:
        query = query.filter_by(status=status_filter)
    if agent_filter:
        query = query.filter_by(agent_id=agent_filter)
    if type_filter:
        query = query.filter_by(tool_type=type_filter)

    logs = query.order_by(ActionLog.created_at.desc()).limit(100).all()
    agents = scoped_query(Agent).all()

    return render_template('dashboard/action_logs.html',
                           logs=logs,
                           agents=agents,
                           status_filter=status_filter,
                           agent_filter=agent_filter,
                           type_filter=type_filter)


@dashboard_bp.route('/logs/actions/<log_id>')
def action_log_detail(log_id):
    """Action log detail — view raw request/response payloads."""
    from app.models.core import ActionLog
    tenant_id = get_current_tenant_id()

    log = db.session.get(ActionLog, log_id)
    if not log or log.tenant_id != tenant_id:
        flash('Log not found.', 'error')
        return redirect(url_for('dashboard.action_logs'))

    return render_template('dashboard/action_log_detail.html', log=log)


# =========================================================================
# DONE FOR YOU (DFY) SERVICE LAYER  (gated by FEATURE_DFY)
# =========================================================================

@dashboard_bp.route('/dfy')
def dfy_catalog():
    if not current_app.config.get('FEATURE_DFY'):
        flash('Done-For-You services are not enabled for this deployment.', 'info')
        return redirect(url_for('dashboard.home'))
    """DFY marketplace — browse available service packages."""
    from app.models.core import DfyPackage, DfyProject
    tenant_id = get_current_tenant_id()

    packages = DfyPackage.query.filter_by(is_active=True).order_by(DfyPackage.sort_order).all()
    projects = DfyProject.query.filter_by(tenant_id=tenant_id).all()

    return render_template('dashboard/dfy_catalog.html',
                           packages=packages,
                           projects=projects)


@dashboard_bp.route('/dfy/projects')
def dfy_projects():
    """DFY projects list — view active and past projects."""
    if not current_app.config.get('FEATURE_DFY'):
        flash('Done-For-You services are not enabled for this deployment.', 'info')
        return redirect(url_for('dashboard.home'))
    from app.models.core import DfyProject
    tenant_id = get_current_tenant_id()

    status_filter = request.args.get('status', '')
    query = DfyProject.query.filter_by(tenant_id=tenant_id)
    if status_filter:
        query = query.filter_by(status=status_filter)

    projects = query.order_by(DfyProject.created_at.desc()).all()

    return render_template('dashboard/dfy_projects.html',
                           projects=projects,
                           status_filter=status_filter)


@dashboard_bp.route('/dfy/request/<package_id>', methods=['GET', 'POST'])
def dfy_request(package_id):
    """DFY intake form — submit a service request for a specific package."""
    if not current_app.config.get('FEATURE_DFY'):
        flash('Done-For-You services are not enabled for this deployment.', 'info')
        return redirect(url_for('dashboard.home'))
    from app.models.core import DfyPackage, DfyProject
    from datetime import timedelta
    tenant_id = get_current_tenant_id()

    package = db.session.get(DfyPackage, package_id)
    if not package or not package.is_active:
        flash('Package not found.', 'error')
        return redirect(url_for('dashboard.dfy_catalog'))

    if request.method == 'POST':
        # Collect intake form data
        intake_data = {
            'business_name': request.form.get('business_name', ''),
            'business_type': request.form.get('business_type', ''),
            'description': request.form.get('description', ''),
            'special_requirements': request.form.get('special_requirements', ''),
            'preferred_timeline': request.form.get('preferred_timeline', ''),
        }

        # Calculate target delivery date from package SLA
        target_date = None
        if package.estimated_days:
            from datetime import datetime, timezone
            target_date = (datetime.now(timezone.utc) + timedelta(days=package.estimated_days)).date()

        project = DfyProject(
            tenant_id=tenant_id,
            package_id=package.id,
            status='pending_payment' if package.price_cents else 'intake',
            intake_form_data=intake_data,
            target_delivery_date=target_date,
            quoted_price_cents=package.price_cents,
            description=intake_data.get('description', ''),
            special_requirements=intake_data.get('special_requirements', ''),
        )
        db.session.add(project)
        db.session.commit()

        # If the package has a price, redirect to Stripe Checkout
        if package.price_cents and package.price_cents > 0:
            try:
                from app.services import stripe_adapter
                from app.models.core import Tenant
                tenant = db.session.get(Tenant, tenant_id)
                stripe_customer_id = tenant.stripe_customer_id if tenant else None

                if not stripe_customer_id:
                    # Create a Stripe customer for this tenant
                    import stripe as stripe_lib
                    stripe_key = current_app.config.get('STRIPE_SECRET_KEY', '')
                    if stripe_key:
                        stripe_lib.api_key = stripe_key
                        customer = stripe_lib.Customer.create(
                            metadata={'tenant_id': tenant_id}
                        )
                        stripe_customer_id = customer.id
                        if tenant:
                            tenant.stripe_customer_id = stripe_customer_id
                            db.session.commit()

                base_url = request.host_url.rstrip('/')
                checkout_result = stripe_adapter.create_topup_checkout(
                    customer_id=stripe_customer_id or '',
                    amount_cents=package.price_cents,
                    description=f'DFY: {package.name}',
                    success_url=f'{base_url}{url_for("dashboard.dfy_checkout_success", project_id=project.id)}',
                    cancel_url=f'{base_url}{url_for("dashboard.dfy_project_detail", project_id=project.id)}',
                    metadata={
                        'dfy_project_id': project.id,
                        'package_id': package.id,
                        'tenant_id': tenant_id,
                        'type': 'dfy_purchase',
                    },
                )

                if checkout_result.get('status') == 'success':
                    checkout_url = checkout_result['data'].get('url', '')
                    project.invoice_id = checkout_result['data'].get('id', '')
                    db.session.commit()
                    if checkout_url:
                        return redirect(checkout_url)

            except Exception as e:
                logger.error(f'Stripe checkout creation failed for DFY project {project.id}: {e}')
                flash('Payment setup failed. Your request has been saved and our team will follow up.', 'warning')

        flash(f'Service request submitted for {package.name}!', 'success')
        return redirect(url_for('dashboard.dfy_project_detail', project_id=project.id))

    return render_template('dashboard/dfy_request.html', package=package)


@dashboard_bp.route('/dfy/checkout/success/<project_id>')
def dfy_checkout_success(project_id):
    """Handle successful Stripe Checkout return for DFY package purchase."""
    if not current_app.config.get('FEATURE_DFY'):
        flash('Done-For-You services are not enabled for this deployment.', 'info')
        return redirect(url_for('dashboard.home'))
    from app.models.core import DfyProject
    tenant_id = get_current_tenant_id()

    project = db.session.get(DfyProject, project_id)
    if not project or project.tenant_id != tenant_id:
        flash('Project not found.', 'error')
        return redirect(url_for('dashboard.dfy_projects'))

    # The actual status update happens via the Stripe webhook (idempotent).
    # This page is just the user-facing redirect after payment.
    session_id = request.args.get('session_id', '')
    if session_id and project.status == 'pending_payment':
        # Optimistic update — webhook will confirm
        project.status = 'intake'
        project.invoice_id = session_id
        db.session.commit()

    flash('Payment successful! Your project is now in progress.', 'success')
    return redirect(url_for('dashboard.dfy_project_detail', project_id=project.id))


@dashboard_bp.route('/dfy/projects/<project_id>')
def dfy_project_detail(project_id):
    """DFY project workspace — view details, status timeline, and messaging thread."""
    if not current_app.config.get('FEATURE_DFY'):
        flash('Done-For-You services are not enabled for this deployment.', 'info')
        return redirect(url_for('dashboard.home'))
    from app.models.core import DfyProject, DfyMessage, Agent
    tenant_id = get_current_tenant_id()

    project = db.session.get(DfyProject, project_id)
    if not project or project.tenant_id != tenant_id:
        flash('Project not found.', 'error')
        return redirect(url_for('dashboard.dfy_projects'))

    messages = DfyMessage.query.filter_by(project_id=project.id, is_admin_note=False)\
        .order_by(DfyMessage.created_at.asc()).all()

    linked_agent = None
    if project.agent_id:
        linked_agent = db.session.get(Agent, project.agent_id)

    return render_template('dashboard/dfy_project_detail.html',
                           project=project,
                           messages=messages,
                           linked_agent=linked_agent)


@dashboard_bp.route('/dfy/projects/<project_id>/message', methods=['POST'])
def dfy_send_message(project_id):
    """Send a message in the DFY project thread."""
    if not current_app.config.get('FEATURE_DFY'):
        flash('Done-For-You services are not enabled for this deployment.', 'info')
        return redirect(url_for('dashboard.home'))
    from app.models.core import DfyProject, DfyMessage
    tenant_id = get_current_tenant_id()

    project = db.session.get(DfyProject, project_id)
    if not project or project.tenant_id != tenant_id:
        flash('Project not found.', 'error')
        return redirect(url_for('dashboard.dfy_projects'))

    content = request.form.get('content', '').strip()
    is_revision = request.form.get('is_revision_request') == '1'

    if not content:
        flash('Message cannot be empty.', 'error')
        return redirect(url_for('dashboard.dfy_project_detail', project_id=project_id))

    msg = DfyMessage(
        project_id=project.id,
        sender_id=current_user.id,
        content=content,
        is_revision_request=is_revision,
    )
    db.session.add(msg)

    # If revision request and project is in_review, move back to in_progress
    if is_revision and project.status == 'in_review':
        project.status = 'in_progress'
        project.revision_count = (project.revision_count or 0) + 1

    db.session.commit()
    flash('Message sent.', 'success')
    return redirect(url_for('dashboard.dfy_project_detail', project_id=project_id))


@dashboard_bp.route('/dfy/projects/<project_id>/approve', methods=['POST'])
def dfy_approve_project(project_id):
    """Tenant approves the completed work — moves project to completed."""
    if not current_app.config.get('FEATURE_DFY'):
        flash('Done-For-You services are not enabled for this deployment.', 'info')
        return redirect(url_for('dashboard.home'))
    from app.models.core import DfyProject
    tenant_id = get_current_tenant_id()

    project = db.session.get(DfyProject, project_id)
    if not project or project.tenant_id != tenant_id:
        flash('Project not found.', 'error')
        return redirect(url_for('dashboard.dfy_projects'))

    if project.status == 'in_review':
        project.status = 'completed'
        db.session.commit()
        flash('Project approved and marked as completed!', 'success')
    else:
        flash('Project can only be approved when in review.', 'error')

    return redirect(url_for('dashboard.dfy_project_detail', project_id=project_id))


# =========================================================================
# Analytics
# =========================================================================
@dashboard_bp.route('/analytics')
def analytics():
    """Call analytics dashboard with aggregated metrics."""
    from app.models.core import CallLog, Agent, PhoneNumber
    from datetime import datetime, timezone, timedelta
    tenant_id = get_current_tenant_id()

    # Date range
    days = request.args.get('days', 30, type=int)
    since = datetime.now(timezone.utc) - timedelta(days=days)

    calls = scoped_query(CallLog).filter(CallLog.created_at >= since).all()
    agents = scoped_query(Agent).all()

    total_calls = len(calls)
    total_seconds = sum(c.duration_seconds or 0 for c in calls)
    total_minutes = total_seconds // 60
    avg_duration = (total_seconds // total_calls) if total_calls else 0
    completed = sum(1 for c in calls if c.status == 'completed')
    failed = sum(1 for c in calls if c.status in ('failed', 'error'))
    transferred = sum(1 for c in calls if c.disconnection_reason == 'call_transfer')

    sentiment_counts = {}
    for c in calls:
        s = c.sentiment or 'unknown'
        sentiment_counts[s] = sentiment_counts.get(s, 0) + 1

    # Per-agent breakdown
    agent_map = {a.id: a.name for a in agents}
    agent_stats = {}
    for c in calls:
        name = agent_map.get(c.agent_id, 'Unknown')
        if name not in agent_stats:
            agent_stats[name] = {'calls': 0, 'minutes': 0}
        agent_stats[name]['calls'] += 1
        agent_stats[name]['minutes'] += (c.duration_seconds or 0) // 60

    # Daily call volume for chart
    daily = {}
    for c in calls:
        day = c.created_at.strftime('%Y-%m-%d')
        daily[day] = daily.get(day, 0) + 1

    return render_template('dashboard/analytics.html',
                           days=days,
                           total_calls=total_calls,
                           total_minutes=total_minutes,
                           avg_duration=avg_duration,
                           completed=completed,
                           failed=failed,
                           transferred=transferred,
                           sentiment_counts=sentiment_counts,
                           agent_stats=agent_stats,
                           daily=daily)


# =========================================================================
# Recordings
# =========================================================================

def _get_recording_retention_days():
    """Read recording_retention_days from PlatformSetting, default 90."""
    from app.models.core import PlatformSetting
    setting = db.session.query(PlatformSetting).filter_by(
        key='recording_retention_days'
    ).first()
    if setting and setting.value is not None:
        try:
            return int(setting.value)
        except (TypeError, ValueError):
            pass
    return 90


def _is_recordings_visible_for_tenant(tenant_id):
    """Check whether recordings are enabled for this tenant.

    Recordings visibility is controlled by the ``recordings_enabled``
    PlatformSetting (global default) and can be overridden per-tenant
    via the ``tenant_settings`` JSON on the Organization model.

    Visibility hierarchy:
    1. Organization.tenant_settings['recordings_enabled'] (per-tenant override)
    2. PlatformSetting key='recordings_enabled' (global default)
    3. True (default if neither is set)
    """
    from app.models.core import Organization, PlatformSetting

    # Per-tenant override
    org = db.session.query(Organization).filter_by(tenant_id=tenant_id).first()
    if org:
        ts = getattr(org, 'tenant_settings', None) or {}
        if isinstance(ts, dict) and 'recordings_enabled' in ts:
            return bool(ts['recordings_enabled'])

    # Global default
    setting = db.session.query(PlatformSetting).filter_by(
        key='recordings_enabled'
    ).first()
    if setting and setting.value is not None:
        return str(setting.value).lower() in ('true', '1', 'yes')

    return True  # default: visible


@dashboard_bp.route('/recordings')
def recordings():
    """Recordings page — list calls with recordings.

    Enforces:
    - Tenant isolation via scoped_query (only this tenant's calls).
    - Tenant-level visibility control (recordings_enabled setting).
    - Retention-aware filtering (excludes recordings older than
      recording_retention_days even if the cleanup task hasn't run yet).
    """
    from app.models.core import CallLog, RecordingMetadata, Agent
    tenant_id = get_current_tenant_id()

    # ── Tenant-level visibility gate ──
    if not _is_recordings_visible_for_tenant(tenant_id):
        return render_template('dashboard/recordings.html',
                               calls=[],
                               agent_map={},
                               page=1,
                               total_pages=0,
                               total=0,
                               recordings_disabled=True)

    page = request.args.get('page', 1, type=int)
    per_page = 20

    # ── Retention-aware query ──
    from datetime import datetime, timedelta, timezone as tz
    retention_days = _get_recording_retention_days()
    cutoff = datetime.now(tz.utc) - timedelta(days=retention_days)

    query = (
        scoped_query(CallLog)
        .filter(
            CallLog.recording_url.isnot(None),
            CallLog.created_at >= cutoff,
        )
    )
    total = query.count()
    calls = query.order_by(CallLog.created_at.desc()).offset((page - 1) * per_page).limit(per_page).all()
    agents = scoped_query(Agent).all()
    agent_map = {a.id: a.name for a in agents}

    total_pages = (total + per_page - 1) // per_page

    return render_template('dashboard/recordings.html',
                           calls=calls,
                           agent_map=agent_map,
                           page=page,
                           total_pages=total_pages,
                           total=total,
                           retention_days=retention_days)


# =========================================================================
# Workflows
# =========================================================================
@dashboard_bp.route('/agents/<agent_id>/workflows')
def workflows(agent_id):
    """View workflow definitions for an agent."""
    from app.models.core import Agent, WorkflowDefinition
    agent = scoped_query(Agent).filter_by(id=agent_id).first_or_404()
    workflows = WorkflowDefinition.query.filter_by(
        agent_id=agent_id, tenant_id=get_current_tenant_id()
    ).order_by(WorkflowDefinition.created_at.desc()).all()
    return render_template('dashboard/workflows.html', agent=agent, workflows=workflows)


# =========================================================================
# Notifications
# =========================================================================
@dashboard_bp.route('/notifications')
def notifications():
    """In-app notification center."""
    from app.models.core import Notification
    tenant_id = get_current_tenant_id()

    notifs = Notification.query.filter_by(
        tenant_id=tenant_id
    ).order_by(Notification.created_at.desc()).limit(100).all()

    unread = sum(1 for n in notifs if not n.is_read)

    return render_template('dashboard/notifications.html',
                           notifications=notifs,
                           unread=unread)


@dashboard_bp.route('/notifications/<notif_id>/read', methods=['POST'])
def notification_mark_read(notif_id):
    """Mark a notification as read."""
    from app.models.core import Notification
    tenant_id = get_current_tenant_id()

    notif = db.session.get(Notification, notif_id)
    if notif and notif.tenant_id == tenant_id:
        notif.is_read = True
        db.session.commit()
    return redirect(url_for('dashboard.notifications'))


@dashboard_bp.route('/notifications/read-all', methods=['POST'])
def notifications_read_all():
    """Mark all notifications as read."""
    from app.models.core import Notification
    tenant_id = get_current_tenant_id()

    Notification.query.filter_by(tenant_id=tenant_id, is_read=False).update({'is_read': True})
    db.session.commit()
    flash('All notifications marked as read.', 'success')
    return redirect(url_for('dashboard.notifications'))


# =========================================================================
# Organization Profile
# =========================================================================
@dashboard_bp.route('/organization', methods=['GET', 'POST'])
def organization_profile():
    """Organization profile — view and edit organization details."""
    from app.models.core import Organization, Tenant
    tenant_id = get_current_tenant_id()

    org = db.session.query(Organization).filter_by(tenant_id=tenant_id).first()
    tenant = db.session.get(Tenant, tenant_id)

    if request.method == 'POST':
        if not org:
            org = Organization(tenant_id=tenant_id)
            db.session.add(org)

        org.name = request.form.get('name', org.name or '').strip()
        org.website = request.form.get('website', '').strip() or None
        org.industry = request.form.get('industry', '').strip() or None
        org.timezone = request.form.get('timezone', '').strip() or None
        org.support_email = request.form.get('support_email', '').strip() or None
        org.support_phone = request.form.get('support_phone', '').strip() or None

        # Handle recordings_enabled tenant setting
        rec_val = request.form.get('recordings_enabled', '').strip().lower()
        settings = dict(org.tenant_settings or {})
        if rec_val == 'true':
            settings['recordings_enabled'] = True
        elif rec_val == 'false':
            settings['recordings_enabled'] = False
        else:
            # 'default' or empty — remove the override so global applies
            settings.pop('recordings_enabled', None)
        org.tenant_settings = settings

        db.session.commit()
        flash('Organization profile updated.', 'success')
        return redirect(url_for('dashboard.organization_profile'))

    # Determine current recordings_enabled state for the template
    tenant_settings = (org.tenant_settings or {}) if org else {}
    recordings_enabled_override = tenant_settings.get('recordings_enabled')  # None = use global

    return render_template('dashboard/organization.html',
                           org=org, tenant=tenant,
                           recordings_enabled_override=recordings_enabled_override)


# =========================================================================
# Subscription Management
# =========================================================================
@dashboard_bp.route('/subscription')
def subscription():
    """Subscription details page."""
    from app.models.core import Subscription, PlanDefinition
    tenant_id = get_current_tenant_id()

    sub = db.session.query(Subscription).filter_by(tenant_id=tenant_id).first()
    plan = db.session.get(PlanDefinition, sub.plan_id) if sub and sub.plan_id else None
    plans = db.session.query(PlanDefinition).filter_by(
        is_active=True
    ).order_by(PlanDefinition.sort_order, PlanDefinition.price_monthly_cents).all()

    return render_template('dashboard/subscription.html',
                           subscription=sub,
                           current_plan=plan,
                           plans=plans)
