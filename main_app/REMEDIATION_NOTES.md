# AgentGenie — Remediation Notes

This document tracks all changes made during the remediation pass based on the audit report.

---

## P0 — Critical Security and Correctness

### 1. Feature Flags and Route Lockdown

**Files changed**: `config.py`, `app/blueprints/admin/routes.py`, `app/blueprints/partner/routes.py`, `app/templates/layouts/dashboard.html`

- Added `FEATURE_PARTNER_PROGRAM`, `FEATURE_DFY`, `FEATURE_CAMPAIGNS` flags to `Config`
- Admin blueprint: added `before_request` hook that checks `membership.role == 'superadmin'` on every request, returning 403 if not authorized
- Partner blueprint: all routes return 404 when `FEATURE_PARTNER_PROGRAM` is false
- Dashboard sidebar: outbound/campaign and DFY nav items wrapped in `{% if config.FEATURE_* %}` conditionals
- Dashboard routes: DFY, campaigns, and contacts routes check feature flags and redirect with flash message if disabled

### 2. Tenant Context and Isolation

**Files changed**: `app/services/tenant/middleware.py`, `app/services/tenant/scoping.py`, `app/models/core.py`, `app/blueprints/dashboard/routes.py`

- Rewrote tenant middleware to use deterministic session-based active tenant strategy
- Added `get_active_membership()` helper to scoping module
- Added `tenant_id` column (with index) to: `AgentConfig`, `AgentVersion`, `WorkflowDefinition`, `HandoffRule`, `GuardrailRule`, `RecordingMetadata`
- Fixed unscoped `Model.query.get()` calls in agent detail/edit routes to use tenant-filtered queries

### 3. Agent Edit Flow (Handoff/Guardrail Persistence)

**Files changed**: `app/blueprints/dashboard/routes.py`

- Fixed agent edit submit to properly persist handoff rules and guardrail rules
- Added tenant_id to all new HandoffRule and GuardrailRule instances
- Replaced `Model.query.get()` with `db.session.get()` + tenant check for safe deletion

### 4. Flask-Migrate and Real Migrations

**Files changed**: `requirements.txt`, `app/__init__.py`, `manage.py`, `render.yaml`

**Files added**: `migrations/` directory, `scripts/release.sh`

- Added `Flask-Migrate` to requirements and initialized in app factory
- Created baseline migration from existing models
- Updated `render.yaml` build command to use `flask db upgrade` instead of `db-init`
- Created `scripts/release.sh` for deployment automation

### 5. Correct Seed Data

**Files changed**: `manage.py`

- Fixed top-up pack seed data to exactly match prompt pricing:
  - 100 min / $39, 500 min / $175, 1000 min / $320
- Removed incorrect 300-minute pack (not in original prompt)
- Fixed plan seed data to match prompt tiers:
  - Starter $99/250min/1 agent/1 number, Growth $249/800min/3 agents/3 numbers, Scale $499/1800min/8 agents/8 numbers

---

## P1 — Architecture and Feature Completion

### 6. OpenAI Brain Microservice

**Files added**: `services/openai_brain/` (entire directory)

- Created FastAPI microservice with `/generate` and `/health` endpoints
- Expanded output schema to cover all prompt-required structured areas
- Added mock mode (`MOCK_MODE=true`) for development without OpenAI credits
- Created `app/services/openai_brain_client.py` HTTP adapter for Flask to call the microservice
- Updated `render.yaml` to include the brain service

### 7. Async Retell Operations via Celery

**Files changed**: `app/tasks/agent_tasks.py`

**Files added**: `app/tasks/webhook_tasks.py`

- Rewrote agent provisioning tasks as proper `@shared_task` with retry policies and idempotency
- Added tasks: `provision_agent_to_retell`, `update_agent_on_retell`, `purchase_phone_number_async`, `assign_number_to_agent_async`
- Created webhook processing tasks with idempotency via `WebhookEvent` deduplication
- All tasks use bounded retries with exponential backoff

### 8. Agent Review/Edit/Approve Flow

**Files changed**: `app/blueprints/dashboard/routes.py`, `app/templates/dashboard/agent_draft_review.html`

- Rewrote the draft review template with editable sections for all prompt-required fields
- Added `agent_draft_save_edits` route that handles save, approve, and regenerate actions
- Approval now enqueues provisioning via Celery `.delay()` instead of synchronous execution

### 9. Knowledge Base CRUD

**Files changed**: `app/models/core.py`

**Files added**: `app/templates/dashboard/knowledge_base.html`, `app/templates/dashboard/knowledge_base_edit.html`

- Expanded `KBType` enum to cover all required types (text, faq, url, file, service, discount, hours_location, support_escalation, booking_link, handoff_instruction)
- Added `category`, `file_name`, `file_path`, `file_size`, `file_mime` fields to `KnowledgeBaseItem`
- Created full CRUD routes: list, add, edit, delete
- File upload with type restrictions and safe filename handling

### 10. Missing Customer Dashboard Pages

**Files added**: `app/templates/dashboard/analytics.html`, `recordings.html`, `workflows.html`, `notifications.html`, `organization.html`, `subscription.html`

- Analytics: aggregated call metrics with Chart.js daily volume chart, sentiment breakdown, per-agent stats
- Recordings: paginated list of calls with recordings and inline audio player
- Workflows: per-agent workflow definitions view
- Notifications: in-app notification center with mark-read and mark-all-read
- Organization: editable organization profile with timezone, support contact
- Subscription: current plan details with available plans comparison

---

## P2 — Billing, Notification, and Operational Completion

### 11. Gmail SMTP Notification Abstraction

**Files added**: `app/services/notifications/` (entire directory)

- Created `NotificationProvider` abstract base class
- Implemented `GmailSMTPProvider` using SMTP with TLS
- Created `dispatcher.py` with template-based notification dispatch
- Templates: welcome, email_verification, password_reset, plan_purchased, plan_changed, minute_topup_purchased, number_purchased, usage_warning, agent_provisioned, agent_failed
- Supports both email and in-app notification dispatch

### 12. Billing Cleanup and Feature Gating

**Files changed**: `app/blueprints/dashboard/routes.py`, `app/templates/layouts/dashboard.html`

- DFY routes gated behind `FEATURE_DFY` flag
- Campaign/outbound routes gated behind `FEATURE_CAMPAIGNS` flag
- Sidebar navigation items conditionally rendered based on flags
- No broken payment flows remain exposed when flags are off

### 13. Admin/Ops Tooling

**Files added**: `app/templates/admin/failed_jobs.html`, `reconciliation.html`, `feature_deferred.html`

- Failed jobs page shows failed/needs_attention agents and failed webhooks
- Reconciliation page with inline adjustment forms for billable seconds
- Feature deferred template for gated admin sections
- All admin routes already had: pricing/plans CRUD, usage reconciliation with adjustments, webhook logs, platform settings, customer detail with support notes, revenue dashboard

---

## P3 — Documentation, Testing, and Production Realism

### 14. Master Implementation Plan

**Files added**: `MASTER_IMPLEMENTATION_PLAN.md`

- Architecture overview and diagram
- Complete folder structure
- Schema overview with all models
- Service contracts (OpenAI Brain, Retell, Notifications)
- Integration map
- Billing design with plan tiers and top-up packs
- Tenant strategy
- Complete page map for customer and admin
- Phased roadmap with partner program marked as deferred

### 15. Documentation Updates

**Files changed/added**: `DEPLOY.md`, `.env.example`, `REMEDIATION_NOTES.md`, `CHANGELOG.md`

- Updated to reflect migrations, microservice architecture, Gmail SMTP default
- Documented all environment variables
- Noted deferred features and flag requirements

### 16. Test Suite Cleanup

**Files changed**: `tests/`

- Removed hardcoded secret-like strings
- Added critical regression tests for admin access control, tenant isolation, seed data correctness
- Ensured clean pytest collection

---

---

## Round 3 — Final Acceptance Blockers

### Brain Client Env Var Mismatch

**File changed**: `app/services/openai_brain_client.py`

The brain client was reading `BRAIN_SERVICE_URL` while config, docs, and render.yaml all used `OPENAI_BRAIN_URL`. Changed the client to read `OPENAI_BRAIN_URL`. Verified no other files reference the old name.

### Agent Edit Handoff/Guardrail Deletion Bug

**File changed**: `app/blueprints/dashboard/routes.py`

The previous logic could delete newly added rules in the same submission. When a new rule (no `rule_id`) was added via `db.session.add()`, the subsequent deletion loop would query all rules for the agent and find the new one missing from `existing_ids`, deleting it. Rewrote both handoff and guardrail sections to use a **delete-then-replace** strategy: first collect submitted IDs, delete rules not in that set, flush, then update existing and insert new rules.

### Notification System Field Mismatches

**Files changed**: `app/models/core.py`, `app/services/notifications/dispatcher.py`, `app/services/billing_engine.py`

The Notification model had `subject`/`body` but the UI template expected `title`/`message`/`is_read` and the dispatcher was creating records with `title`/`message`/`link` (fields that did not exist on the model). Added `title`, `message`, `link`, and `is_read` columns to the Notification model. Made `subject`/`body` nullable since in-app notifications only use `title`/`message`. Updated the dispatcher to set both sets of fields. Updated `billing_engine._create_notification` to also set `title`/`message`/`is_read`. Added a migration for all new columns.

### Migration for plan_definitions.sort_order

**File added**: `migrations/versions/a1b2c3d4e5f6_add_missing_columns.py`

The baseline migration did not include `sort_order` on `plan_definitions`. Created a new migration that adds: `plan_definitions.sort_order`, `notifications.title`/`message`/`link`/`is_read` (with `subject`/`body` relaxed to nullable), `organizations.website`/`support_email`/`support_phone`, and `tenant_id` on child models (with runtime column-existence check for safety).

### Organization Profile Model Gaps

**File changed**: `app/models/core.py`

The organization template and route referenced `website`, `support_email`, and `support_phone` but these columns did not exist on the Organization model. Added all three columns. The migration above covers the schema change.

### Synchronous Provisioning Fallback Removed

**File changed**: `app/blueprints/dashboard/routes.py`

The `agent_draft_approve` route had a `try/except` that caught Celery broker failures and fell back to calling `provision_agent_to_retell()` synchronously. Removed the sync fallback. On Celery failure, the agent status is now set to `failed` and the user is told to retry from the agent detail page.

### Stale Workspace Docs

**Files changed**: all 20 `.md` files in `saas_platform/` root

Added `> **SUPERSEDED**` deprecation notices to all workspace-level docs from earlier development phases. Rewrote `REVISED_MASTER_PLAN.md` to redirect to the canonical docs inside `main_app/`.

---

## Round 4 — Structural Completeness

### Structured Agent Data Wired into Retell Runtime

**Files added**: `app/services/prompt_builder.py`

**Files changed**: `app/tasks/agent_tasks.py`

The provisioning and update paths previously only pushed a basic `role_description` + `greeting_message` to Retell, leaving all structured data (services, FAQs, offers, business hours, handoff rules, guardrails, KB items) stored but not operationalized. Created `prompt_builder.py` that compiles ALL structured draft data into a comprehensive system prompt with dedicated sections:

- **Identity & Personality**: agent name, role, tone, language
- **Services**: full service catalog with pricing
- **FAQ**: question/answer pairs
- **Offers & Discounts**: current promotions
- **Business Hours & Location**: operating hours and address
- **Handoff Rules**: conditions, destination numbers, transfer messages
- **Guardrails**: prohibited topics with fallback responses
- **Knowledge Base**: all KB items by type
- **Booking & Escalation**: links and instructions

Both `provision_agent_to_retell` and `update_agent_in_retell` now call `build_full_prompt()` to compile the full prompt before sending to Retell. The compiled prompt is also stored in the `AgentVersion.config_snapshot` for audit trail.

### In-Process OpenAI Fallback Removed

**File changed**: `app/services/openai_brain_client.py`

The brain client previously imported and called the legacy monolith `openai_service.generate_agent_config()` as a fallback when the Brain microservice was unreachable. This defeated the purpose of the microservice separation. Completely removed the fallback — the client now returns a clear error message directing the operator to verify the Brain microservice is deployed and `OPENAI_BRAIN_URL` is correct. No imports from `openai_service` remain.

### Gmail SMTP Env Var Reconciliation

**Files changed**: `app/services/notifications/providers/smtp_gmail.py`, `.env.example`, `render.yaml`

The provider code expected `GMAIL_SMTP_FROM` (an email address) while `.env.example` and `render.yaml` documented `GMAIL_SMTP_FROM_NAME` (a display name). Reconciled by supporting both:
- `GMAIL_SMTP_FROM` — the "From" email address (defaults to `GMAIL_SMTP_USER`)
- `GMAIL_SMTP_FROM_NAME` — optional display name (e.g. "AgentGenie")

When both are set, the From header becomes `AgentGenie <noreply@yourdomain.com>`. Updated `.env.example` and `render.yaml` (both web and worker services) to document and propagate both variables.

### Deployment Strictly Migration-Only

**Files changed**: `scripts/release.sh`, `manage.py`

The release script and `db-init` command both had fallback paths to `db.create_all()` when migrations failed or were missing. Removed all fallbacks:
- `release.sh`: now runs `flask db upgrade` with `set -euo pipefail` — any failure aborts the deploy
- `manage.py db-init`: exits with error code 1 if migrations directory is missing or `flask db upgrade` fails
- `manage.py seed --flush`: recreates tables via `flask db upgrade` after `drop_all()` instead of `create_all()`

Zero `db.create_all()` calls remain in the codebase.

### Stale SQLite Files Removed

**Files deleted**: `instance/dev.db`, `instance/test.db`

Removed leftover SQLite database files that did not reflect the latest schema. The `.gitignore` already had `*.db` and `instance/` entries, so these were workspace artifacts only.

---

## Definition of Done Checklist

| # | Requirement | Status |
|---|-------------|--------|
| 1 | Customers cannot access admin or deferred partner areas | Done |
| 2 | Tenant isolation enforced server-side | Done |
| 3 | Agent edit/review/approve works without runtime errors | Done |
| 4 | Top-up defaults match prompt exactly | Done |
| 5 | Migrations exist and deployment uses them | Done |
| 6 | OpenAI runs as separate microservice | Done |
| 7 | Retell ops are async from request handlers (no sync fallback) | Done |
| 8 | Knowledge-base flows implemented | Done |
| 9 | Required customer pages exist with coherent UI | Done |
| 10 | Gmail SMTP notification abstraction live | Done |
| 11 | Docs accurately describe delivered system | Done |
| 12 | Tests pass, no secret strings remain | Done |
| 13 | Partner program clearly deferred and gated | Done |
| 14 | Brain client uses OPENAI_BRAIN_URL consistently | Done |
| 15 | Agent edit does not delete newly added handoff/guardrail rules | Done |
| 16 | Notification model/dispatcher/UI fields aligned | Done |
| 17 | Migration covers sort_order, notification fields, org fields | Done |
| 18 | Organization profile backed by real model columns | Done |
| 19 | No synchronous provisioning fallback path | Done |
| 20 | Workspace docs marked superseded, canonical docs in main_app | Done |
| 21 | Structured agent data (services, FAQs, handoffs, guardrails, KB) compiled into Retell LLM prompt | Done |
| 22 | No in-process OpenAI fallback — brain client is microservice-only | Done |
| 23 | Gmail SMTP env vars consistent across code, .env.example, render.yaml, docs | Done |
| 24 | Zero db.create_all() calls — deploy is strictly migration-based | Done |
| 25 | No stale SQLite files shipped in package | Done |
