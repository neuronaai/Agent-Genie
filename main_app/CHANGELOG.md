# Changelog

All notable changes to AgentGenie are documented in this file.

## [1.1.2] — 2026-03-21 — Deployment Layout Fix

### Infrastructure

- Moved `render.yaml` from `main_app/` to the repository root so Render auto-detects it.
- Added explicit `rootDir: main_app` to web and worker services in `render.yaml`.
- Brain microservice retains `rootDir: services/openai_brain` (now correct relative to repo root).
- The ZIP package is now a coherent deployable bundle: extract, `git init`, push, and Render builds each service from the correct subdirectory.

### Documentation

- Updated `DEPLOY.md` and `RENDER_DEPLOYMENT_GUIDE.md` with repository layout diagram, `rootDir` table, and corrected instructions.
- Added `--beat` flag to local development Celery command in `DEPLOY.md`.

## [1.1.1] — 2026-03-21 — Recording Retention, Visibility Controls, Upload Hardening

### Features

- Added Celery Beat periodic task (`recording.cleanup_expired`) that purges recordings older than `recording_retention_days`.
- Recordings page now filters by retention window at query time (recordings beyond retention are never shown).
- Added per-tenant `recordings_enabled` toggle via `Organization.tenant_settings` JSONB column.
- Added global `recordings_enabled` platform setting with admin UI to toggle it.
- Admin customer detail page now has a "Tenant Feature Toggles" section for per-tenant overrides.
- Organization profile page now has a "Feature Preferences" section for tenant owners.
- Platform settings page is now fully editable (was display-only).

### Security

- Added `MAX_CONTENT_LENGTH = 16 MB` to Flask config for global request-size enforcement.
- Added 413 error handler with user-friendly flash message and redirect.

### Infrastructure

- Rewrote `celery_app.py` with eager Flask app bootstrap so the Celery worker has full DB access.
- Added `--beat` flag to worker start command in `render.yaml` and `Dockerfile.worker`.
- Added Celery task autodiscovery for all 5 task modules.

## [1.1.0] — 2026-03-20 — Remediation Release

### Security

- Locked down all admin routes with `before_request` superadmin check returning 403 for unauthorized users.
- Disabled partner blueprint routes behind `FEATURE_PARTNER_PROGRAM` flag (default: false).
- Added `tenant_id` columns to child models (AgentConfig, AgentVersion, WorkflowDefinition, HandoffRule, GuardrailRule, RecordingMetadata) for defense-in-depth tenant isolation.
- Replaced all unscoped `Model.query.get()` calls in dashboard routes with tenant-filtered queries.

### Architecture

- Created separate OpenAI Brain microservice (`services/openai_brain/`) using FastAPI with expanded structured output schema and mock mode.
- Rewrote agent provisioning as proper Celery `@shared_task` with retry policies, idempotency, and exponential backoff.
- Added webhook processing tasks with deduplication via `WebhookEvent` model.
- Introduced Gmail SMTP notification abstraction with provider pattern and template-based dispatch.

### Features

- Completed knowledge-base CRUD with expanded types (text, faq, url, file, service, discount, hours, escalation, booking, handoff).
- Added missing customer dashboard pages: analytics, recordings, workflows, notifications, organization profile, subscription management.
- Expanded agent draft review UI with editable sections for all prompt-required fields.
- Added admin pages: failed jobs, usage reconciliation with inline adjustments, feature-deferred placeholder.

### Billing

- Corrected seed data for top-up packs to match prompt pricing (100/$39, 500/$175, 1000/$320).
- Removed incorrect 300-minute pack not in original prompt.
- Corrected plan tier seeds (Starter $99/250min, Growth $249/800min, Scale $499/1800min).
- Gated DFY and outbound campaign flows behind feature flags to prevent broken payment paths.

### Infrastructure

- Added Flask-Migrate for schema management; deployment now uses `flask db upgrade`.
- Created `scripts/release.sh` for automated deployment.
- Updated `render.yaml` to include brain microservice and migration-based deploy.
- Added `tenacity`, `pydantic`, and `openai` to requirements.

### Documentation

- Created `MASTER_IMPLEMENTATION_PLAN.md` with architecture, schema, service contracts, and roadmap.
- Created `REMEDIATION_NOTES.md` tracking all changes.
- Updated `.env.example` and deployment guides to reflect current system state.

## [1.0.0] — 2026-03-15 — Initial Release

- Multi-tenant SaaS platform for AI voice agent management.
- Retell AI integration for agent provisioning and phone numbers.
- Stripe billing with subscriptions and top-ups.
- Admin panel with customer management, pricing, and revenue dashboards.
- Agent creation wizard with AI-assisted generation via OpenAI.
- Call logging, recordings, and action logs.
- External integrations (Google Calendar, SendGrid, Twilio stubs).
- Outbound campaigns and DFY service marketplace (partially complete).
