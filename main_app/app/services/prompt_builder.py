"""
Prompt Builder — compiles structured agent data into a comprehensive Retell LLM prompt.

The review/edit/approve flow stores rich structured data: services, FAQs,
specials/offers, hours of operation, escalation rules, handoff rules,
guardrails, and knowledge-base items.  This module assembles all of that
into a single ``general_prompt`` string that Retell's LLM will follow at
runtime, ensuring the live agent actually enforces the reviewed configuration.

Usage::

    from app.services.prompt_builder import build_full_prompt
    prompt = build_full_prompt(config_data, handoff_rules, guardrail_rules, kb_items)
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def build_full_prompt(
    config_data: dict,
    handoff_rules: list[dict] | None = None,
    guardrail_rules: list[dict] | None = None,
    kb_items: list[dict] | None = None,
) -> str:
    """Build a comprehensive Retell LLM ``general_prompt`` from all structured data.

    Parameters
    ----------
    config_data : dict
        The full generated/edited agent config (from AgentDraft.generated_config
        or AgentConfig.business_context).
    handoff_rules : list[dict], optional
        DB-persisted HandoffRule rows (dicts with ``condition``,
        ``destination_number``, ``transfer_message``).
    guardrail_rules : list[dict], optional
        DB-persisted GuardrailRule rows (dicts with ``prohibited_topic``,
        ``fallback_message``).
    kb_items : list[dict], optional
        KnowledgeBaseItem rows (dicts with ``title``, ``content``,
        ``type``).

    Returns
    -------
    str
        A fully assembled system prompt ready for Retell's ``general_prompt``.
    """
    sections: list[str] = []

    # ── 1. Core Identity ────────────────────────────────────────────────
    # The Brain microservice emits 'agent_role'; legacy configs may use
    # 'role_description'.  Accept both, preferring agent_role when present.
    role = config_data.get('agent_role') or config_data.get('role_description', '')
    agent_name = config_data.get('agent_name', '')
    business_type = config_data.get('business_type', '')
    tone = config_data.get('tone', 'professional')

    identity_parts = []
    if agent_name:
        identity_parts.append(f"Your name is {agent_name}.")
    if role:
        identity_parts.append(role)
    if business_type:
        identity_parts.append(f"You work for a {business_type} business.")
    identity_parts.append(f"Maintain a {tone} tone throughout every interaction.")
    sections.append('\n'.join(identity_parts))

    # ── 2. Business Context ─────────────────────────────────────────────
    biz_ctx = config_data.get('business_context', '')
    if biz_ctx:
        sections.append(f"## Business Context\n{biz_ctx}")

    # ── 3. Hours of Operation ───────────────────────────────────────────
    hours = config_data.get('hours_of_operation')
    if hours:
        if isinstance(hours, dict):
            tz = hours.get('timezone', 'UTC')
            sched = hours.get('schedule', '')
            sections.append(f"## Hours of Operation\nTimezone: {tz}\n{sched}")
        elif isinstance(hours, str):
            sections.append(f"## Hours of Operation\n{hours}")

    # ── 4. Services Offered ─────────────────────────────────────────────
    services = config_data.get('services', [])
    if services:
        lines = ["## Services Offered"]
        for svc in services:
            name = svc.get('name', '')
            desc = svc.get('description', '')
            if name:
                lines.append(f"- **{name}**: {desc}" if desc else f"- **{name}**")
        sections.append('\n'.join(lines))

    # ── 5. FAQs ─────────────────────────────────────────────────────────
    faqs = config_data.get('faqs', [])
    if faqs:
        lines = ["## Frequently Asked Questions"]
        for faq in faqs:
            q = faq.get('question', '')
            a = faq.get('answer', '')
            if q:
                lines.append(f"Q: {q}")
                lines.append(f"A: {a}\n")
        sections.append('\n'.join(lines))

    # ── 6. Specials / Offers ────────────────────────────────────────────
    offers = config_data.get('specials_offers', [])
    if offers:
        lines = ["## Current Specials & Offers"]
        for o in offers:
            if isinstance(o, str) and o.strip():
                lines.append(f"- {o}")
            elif isinstance(o, dict):
                lines.append(f"- {o.get('name', o.get('description', ''))}")
        sections.append('\n'.join(lines))

    # ── 7. Booking / Support Behavior ───────────────────────────────────
    booking = config_data.get('booking_behavior', '')
    support = config_data.get('support_flow', '')
    fallback = config_data.get('fallback_behavior', '')
    unsupported = config_data.get('unsupported_request_behavior', '')

    behavior_parts = []
    if booking:
        behavior_parts.append(f"**Booking behavior**: {booking}")
    if support:
        behavior_parts.append(f"**Support flow**: {support}")
    if fallback:
        behavior_parts.append(f"**Fallback behavior**: {fallback}")
    if unsupported:
        behavior_parts.append(f"**Unsupported requests**: {unsupported}")
    if behavior_parts:
        sections.append("## Behavioral Instructions\n" + '\n'.join(behavior_parts))

    # ── 8. Escalation Rules ─────────────────────────────────────────────
    escalation = config_data.get('escalation_rules', [])
    if escalation:
        lines = ["## Escalation Rules"]
        for r in escalation:
            if isinstance(r, str) and r.strip():
                lines.append(f"- {r}")
        sections.append('\n'.join(lines))

    # ── 9. Handoff / Transfer Rules ─────────────────────────────────────
    # Merge config-level and DB-persisted rules (DB takes precedence)
    all_handoffs = _merge_handoff_rules(config_data, handoff_rules)
    if all_handoffs:
        lines = ["## Call Transfer / Handoff Rules",
                 "When any of the following conditions are met, transfer the call:"]
        for h in all_handoffs:
            cond = h.get('condition', '')
            dest = h.get('destination_number', '')
            msg = h.get('transfer_message', '')
            line = f"- **Condition**: {cond}"
            if dest:
                line += f" → Transfer to {dest}"
            if msg:
                line += f" (say: \"{msg}\")"
            lines.append(line)
        sections.append('\n'.join(lines))

    # ── 10. Guardrails / Prohibited Topics ──────────────────────────────
    all_guardrails = _merge_guardrail_rules(config_data, guardrail_rules)
    if all_guardrails:
        lines = ["## Guardrails — Prohibited Topics",
                 "You MUST NOT discuss the following topics. If a caller asks about them, "
                 "respond ONLY with the specified fallback message:"]
        for g in all_guardrails:
            topic = g.get('prohibited_topic', '')
            fb = g.get('fallback_message', 'I cannot discuss that topic.')
            lines.append(f"- **{topic}** → \"{fb}\"")
        sections.append('\n'.join(lines))

    # ── 11. Knowledge Base ──────────────────────────────────────────────
    if kb_items:
        lines = ["## Knowledge Base",
                 "Use the following reference information when answering caller questions:"]
        for item in kb_items:
            title = item.get('title', '')
            content = item.get('content', '')
            kb_type = item.get('type', 'text')
            url = item.get('url', '')
            file_name = item.get('file_name', '')

            if title and content:
                # Full content item — include type and any URL reference
                header = f"\n### {title} ({kb_type})"
                if url:
                    header += f"\nSource URL: {url}"
                lines.append(f"{header}\n{content}")
            elif title and url:
                # URL-only item (no extracted content yet)
                lines.append(f"\n### {title} (url)\nRefer callers to: {url}")
            elif title and file_name:
                # File-only item (no extracted content yet)
                lines.append(f"\n### {title} (file)\nBased on uploaded document: {file_name}")
            elif content:
                lines.append(f"\n{content}")
            elif title:
                # Title-only item — still include it as a topic the agent knows about
                lines.append(f"\n### {title} ({kb_type})")
        sections.append('\n'.join(lines))

    # ── 12. Knowledge Categories ───────────────────────────────────────
    # Generated by the Brain microservice — tells the agent what domains
    # of knowledge it should be prepared to discuss.
    knowledge_cats = config_data.get('knowledge_categories', [])
    if knowledge_cats:
        lines = ["## Knowledge Categories",
                 "You should be knowledgeable about the following topics:"]
        for cat in knowledge_cats:
            if isinstance(cat, str) and cat.strip():
                lines.append(f"- {cat.strip()}")
        sections.append('\n'.join(lines))

    # ── 13. Routing Rules ──────────────────────────────────────────────
    # Generated by the Brain microservice — defines how the agent should
    # route or categorise incoming calls based on caller intent.
    routing_rules = config_data.get('routing_rules', [])
    if routing_rules:
        lines = ["## Call Routing Rules",
                 "Apply the following routing logic based on caller intent:"]
        for rule in routing_rules:
            if isinstance(rule, str) and rule.strip():
                lines.append(f"- {rule.strip()}")
        sections.append('\n'.join(lines))

    return '\n\n'.join(sections)


def _merge_handoff_rules(config_data: dict, db_rules: list[dict] | None) -> list[dict]:
    """Merge handoff rules from config and DB, preferring DB rows."""
    if db_rules:
        return db_rules
    # Fall back to config-level rules
    rules = (
        config_data.get('handoff_rules', [])
        or config_data.get('human_handoff_conditions', [])
        or config_data.get('transfer_rules', [])
    )
    return [r for r in rules if r.get('condition')]


def _merge_guardrail_rules(config_data: dict, db_rules: list[dict] | None) -> list[dict]:
    """Merge guardrail rules from config and DB, preferring DB rows."""
    if db_rules:
        return db_rules
    rules = (
        config_data.get('guardrails', [])
        or config_data.get('prohibited_topics', [])
    )
    return [r for r in rules if r.get('prohibited_topic')]
