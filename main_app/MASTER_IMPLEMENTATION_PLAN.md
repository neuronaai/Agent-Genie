# AgentGenie — Master Implementation Plan

## 1. Architecture Overview

AgentGenie is a multi-tenant SaaS platform that enables businesses to create, manage, and deploy AI-powered voice agents via the Retell AI provider. The platform follows a modular Flask monolith architecture with a separate FastAPI microservice for OpenAI-powered agent generation, backed by Celery for asynchronous task processing.

### Core Architecture Principles

- **Multi-tenant isolation**: every data query is scoped by `tenant_id` via middleware and helper functions
- **Feature-flag gating**: incomplete or deferred features (partner program, DFY, outbound campaigns) are hidden behind environment-variable flags
- **Async-first provisioning**: all provider operations (Retell, phone numbers) run through Celery tasks, not in request/response cycles
- **Provider abstraction**: notification, payment, and AI generation services use adapter patterns for future provider swaps
- **Migration-driven schema**: all schema changes go through Flask-Migrate (Alembic), never raw `db.create_all()`

---

## 2. Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                         INTERNET / CDN                              │
└──────────────┬──────────────────────────────────┬───────────────────┘
               │                                  │
       ┌───────▼───────┐                 ┌────────▼────────┐
       │  Flask App     │                 │  OpenAI Brain   │
       │  (Gunicorn)    │◄── HTTP ──────►│  (FastAPI/Uvi)  │
       │  Port 5000     │                 │  Port 8100      │
       └──┬────┬────┬───┘                 └─────────────────┘
          │    │    │
   ┌──────▼┐ ┌▼────▼──┐  ┌──────────────┐
   │ Redis │ │PostgreSQL│  │ Celery Worker│
   │ 6379  │ │  5432   │  │  (beat opt.) │
   └───────┘ └─────────┘  └──────────────┘
          │
   ┌──────▼──────────────────────────┐
   │  External Providers             │
   │  • Retell AI (voice agents)     │
   │  • Stripe (billing)             │
   │  • Gmail SMTP (notifications)   │
   │  • Google Calendar (optional)   │
   └─────────────────────────────────┘
```

---

## 3. Folder Structure

```
saas_platform/
├── main_app/                          # Flask monolith
│   ├── app/
│   │   ├── __init__.py                # App factory, extensions, blueprint registration
│   │   ├── celery_app.py              # Celery configuration
│   │   ├── models/
│   │   │   └── core.py                # All SQLAlchemy models
│   │   ├── blueprints/
│   │   │   ├── auth/routes.py         # Signup, login, verify, reset
│   │   │   ├── dashboard/routes.py    # Customer-facing dashboard
│   │   │   ├── admin/routes.py        # Superadmin panel
│   │   │   ├── partner/routes.py      # Partner program (deferred, flag-gated)
│   │   │   └── webhooks/routes.py     # Stripe & Retell webhooks
│   │   ├── services/
│   │   │   ├── retell_adapter.py      # Retell AI HTTP client
│   │   │   ├── stripe_adapter.py      # Stripe payment operations
│   │   │   ├── openai_service.py      # Legacy AI module (unused at runtime; kept for reference only)
│   │   │   ├── openai_brain_client.py # HTTP client for brain microservice
│   │   │   ├── campaign_engine.py     # Outbound campaign logic (flag-gated)
│   │   │   ├── tenant/
│   │   │   │   ├── middleware.py       # Tenant resolution middleware
│   │   │   │   └── scoping.py         # Query scoping helpers
│   │   │   └── notifications/
│   │   │       ├── dispatcher.py       # Provider-agnostic notification dispatch
│   │   │       └── providers/
│   │   │           ├── base.py         # Abstract provider interface
│   │   │           └── smtp_gmail.py   # Gmail SMTP provider
│   │   ├── tasks/
│   │   │   ├── agent_tasks.py         # Async agent provisioning
│   │   │   └── webhook_tasks.py       # Idempotent webhook processing
│   │   ├── templates/                 # Jinja2 templates
│   │   └── static/                    # CSS, JS, images
│   ├── migrations/                    # Flask-Migrate (Alembic) migrations
│   ├── tests/                         # Pytest test suite
│   ├── manage.py                      # CLI commands
│   ├── config.py                      # Configuration classes
│   ├── seed.py                        # Seed data module
│   ├── requirements.txt
│   ├── render.yaml                    # Render deployment manifest
│   └── scripts/release.sh             # Release script for deploy
│
└── services/
    └── openai_brain/                  # Separate FastAPI microservice
        ├── app/
        │   ├── main.py                # FastAPI app with /generate endpoint
        │   ├── generator.py           # OpenAI generation logic
        │   └── schemas.py             # Pydantic request/response schemas
        ├── Dockerfile
        └── requirements.txt
```

---

## 4. Schema Overview

### Core Entities

| Model | Purpose | Key Fields |
|-------|---------|------------|
| **User** | Authentication identity | email, password_hash, is_verified |
| **Tenant** | Isolated workspace | id (UUID), status, stripe_customer_id |
| **TenantMembership** | User-Tenant link | user_id, tenant_id, role |
| **Organization** | Tenant business info | name, website, industry, timezone |

### Agent System

| Model | Purpose | Key Fields |
|-------|---------|------------|
| **Agent** | Voice agent definition | name, mode, status, retell_agent_id, tenant_id |
| **AgentConfig** | Provisioned config | system_prompt, voice_id, language, tenant_id |
| **AgentDraft** | AI-generated draft | generated_config (JSON), status, tenant_id |
| **AgentVersion** | Config version history | version_number, config_snapshot, tenant_id |
| **HandoffRule** | Call transfer rules | condition, target_number, agent_id, tenant_id |
| **GuardrailRule** | Safety guardrails | rule_type, condition, response, agent_id, tenant_id |
| **KnowledgeBaseItem** | Agent knowledge | type, title, content, file metadata, tenant_id |
| **WorkflowDefinition** | Post-call workflows | trigger_condition, steps (JSON), tenant_id |

### Billing

| Model | Purpose | Key Fields |
|-------|---------|------------|
| **Subscription** | Active plan | plan_id, status, current_period_end |
| **PlanDefinition** | Plan catalog | name, price_cents, included_minutes |
| **TopupPackDefinition** | Minute top-up packs | label, minutes, price_cents |
| **MinuteTopupPurchase** | Purchase records | pack_id, minutes_added, price_cents |
| **Invoice** | Billing invoices | amount_cents, status, stripe_invoice_id |
| **Payment** | Payment records | amount_cents, status, provider |
| **UsageRecord** | Per-call usage | provider_reported_seconds, billable_seconds |
| **UsageSummary** | Monthly aggregates | total_minutes, overage_minutes |

### Communication

| Model | Purpose | Key Fields |
|-------|---------|------------|
| **PhoneNumber** | Provisioned numbers | number, status, retell_number_id, tenant_id |
| **CallLog** | Call records | duration_seconds, sentiment, recording_url |
| **RecordingMetadata** | Recording details | storage_url, duration_seconds, tenant_id |
| **WebhookEvent** | Webhook audit log | source, event_type, payload, status |
| **Notification** | In-app notifications | title, message, type, is_read, tenant_id |

---

## 5. Service Contracts

### OpenAI Brain Microservice

**Endpoint**: `POST /generate`

Request:
```json
{
  "business_description": "string",
  "industry": "string (optional)",
  "tone": "string (optional)",
  "language": "string (optional)"
}
```

Response:
```json
{
  "status": "success",
  "config": {
    "business_overview": "...",
    "agent_role_and_tone": "...",
    "services": [...],
    "faqs": [...],
    "offers_promotions": [...],
    "hours_location": {...},
    "handoff_rules": [...],
    "guardrails": [...],
    "booking_behavior": "...",
    "escalation_behavior": "...",
    "fallback_behavior": "...",
    "system_prompt": "..."
  }
}
```

### Retell Adapter

All Retell operations return `{"status": "success"|"error", "data": {...}, "message": "..."}`.

### Notification Dispatcher

```python
from app.services.notifications.dispatcher import notify
notify('welcome', to_email='user@example.com', tenant_id='...', context={'name': 'Alice'})
```

---

## 6. Integration Map

| Provider | Purpose | Auth Method | Status |
|----------|---------|-------------|--------|
| **Retell AI** | Voice agent provisioning, phone numbers, calls | Bearer token | Active |
| **Stripe** | Subscriptions, top-ups, invoices | Secret key + webhooks | Active |
| **Gmail SMTP** | Email notifications | App password | Active (default) |
| **OpenAI** | Agent config generation | API key | Active (via microservice) |
| **Google Calendar** | Booking integration | OAuth 2.0 | Optional |
| **SendGrid** | Email (future alternative) | API key | Deferred |
| **Twilio** | SMS (future) | Account SID + token | Deferred |

---

## 7. Billing Design

### Plan Tiers (seed defaults)

| Plan | Monthly | Minutes | Agents | Numbers | Overage |
|------|---------|---------|--------|---------|---------|
| Starter | $99 | 250 | 1 | 1 | $0.39/min |
| Growth | $249 | 800 | 3 | 3 | $0.35/min |
| Scale | $499 | 1,800 | 8 | 8 | $0.32/min |

### Top-Up Packs (seed defaults)

| Pack | Minutes | Price |
|------|---------|-------|
| 100 Minutes | 100 | $39 |
| 500 Minutes | 500 | $175 |
| 1,000 Minutes | 1,000 | $320 |

### Billing Flow

1. User selects plan → Stripe Checkout → webhook confirms → subscription activated
2. Usage tracked per call via `UsageRecord` (provider-reported vs internally billable)
3. Monthly `UsageSummary` aggregates usage for invoicing
4. Overage billed at plan-specific rate
5. Top-ups add minutes to balance immediately

---

## 8. Tenant Strategy

- Each user belongs to one or more tenants via `TenantMembership`
- Active tenant stored in Flask session (`active_tenant_id`)
- All dashboard queries use `scoped_query()` which filters by `tenant_id`
- Admin routes bypass tenant scoping (superadmin sees all)
- Child models (AgentConfig, HandoffRule, etc.) carry `tenant_id` for defense-in-depth
- Tenant resolution happens in `before_request` middleware

---

## 9. Page Map

### Customer Dashboard (`/app/`)

| Route | Page | Status |
|-------|------|--------|
| `/app/` | Dashboard home | Complete |
| `/app/agents` | Agent list | Complete |
| `/app/agents/create` | Create agent wizard | Complete |
| `/app/agents/<id>` | Agent detail | Complete |
| `/app/agents/<id>/edit` | Edit agent | Complete |
| `/app/agents/<id>/draft-review` | AI draft review | Complete |
| `/app/agents/<id>/knowledge` | Knowledge base CRUD | Complete |
| `/app/agents/<id>/workflows` | Workflows view | Complete |
| `/app/agents/<id>/deployment` | Deployment/embed codes | Complete |
| `/app/agents/<id>/tools` | Tool assignments | Complete |
| `/app/numbers` | Phone numbers list | Complete |
| `/app/calls` | Call logs | Complete |
| `/app/analytics` | Analytics dashboard | Complete |
| `/app/recordings` | Recordings list | Complete |
| `/app/billing` | Billing overview | Complete |
| `/app/billing/usage` | Usage details | Complete |
| `/app/billing/invoices` | Invoice list | Complete |
| `/app/billing/topup` | Top-up purchase | Complete |
| `/app/subscription` | Subscription management | Complete |
| `/app/settings` | Account settings | Complete |
| `/app/organization` | Organization profile | Complete |
| `/app/notifications` | Notification center | Complete |
| `/app/integrations` | External integrations | Complete |
| `/app/contacts` | Contact lists | Gated (FEATURE_CAMPAIGNS) |
| `/app/campaigns` | Outbound campaigns | Gated (FEATURE_CAMPAIGNS) |
| `/app/dfy` | Done-For-You services | Gated (FEATURE_DFY) |

### Admin Panel (`/admin/`)

| Route | Page | Status |
|-------|------|--------|
| `/admin/` | Admin dashboard | Complete |
| `/admin/customers` | Customer list | Complete |
| `/admin/customers/<id>` | Customer detail | Complete |
| `/admin/partners` | Partners (deferred) | Read-only |
| `/admin/pricing` | Plans & top-up management | Complete |
| `/admin/billing-review` | Billing review | Complete |
| `/admin/revenue` | Revenue dashboard | Complete |
| `/admin/reconciliation` | Usage reconciliation | Complete |
| `/admin/webhooks` | Webhook logs | Complete |
| `/admin/failed-jobs` | Failed jobs | Complete |
| `/admin/settings` | Platform settings | Complete |

---

## 10. Phased Roadmap

### Phase 1 — Core Platform (Current Delivery)

- Multi-tenant auth and tenant isolation
- Agent CRUD with AI-assisted generation
- Retell AI integration for provisioning
- Phone number management
- Call logging and recordings
- Knowledge base CRUD
- Stripe billing (subscriptions + top-ups)
- Gmail SMTP notifications
- Admin panel with pricing, reconciliation, and ops tools
- Flask-Migrate for schema management
- Celery for async operations

### Phase 2 — Enhanced Features (Next)

- Outbound campaigns (flag-gated, partially built)
- Done-For-You services (flag-gated, partially built)
- Advanced analytics and reporting
- Webhook signature verification
- Usage threshold alerts
- Batch operations

### Phase 3 — Partner Program (Future)

- Partner registration and onboarding
- White-label subdomain routing
- Revenue sharing and payout management
- Partner dashboard
- Multi-tier commission structures

> **Note**: The partner program is explicitly deferred for this delivery branch. All partner routes are gated behind `FEATURE_PARTNER_PROGRAM=false` and the partner blueprint returns 404 when disabled.

---

## 11. Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `SECRET_KEY` | Yes | Flask secret key |
| `DATABASE_URL` | Yes | PostgreSQL connection string |
| `RETELL_API_KEY` | Yes | Retell AI API key |
| `STRIPE_SECRET_KEY` | Yes | Stripe secret key |
| `STRIPE_WEBHOOK_SECRET` | Yes | Stripe webhook signing secret |
| `OPENAI_API_KEY_CUSTOM` | Yes | OpenAI API key for brain service |
| `GMAIL_SMTP_USER` | Yes | Gmail address for notifications |
| `GMAIL_SMTP_PASSWORD` | Yes | Gmail App Password |
| `CELERY_BROKER_URL` | Yes | Redis URL for Celery |
| `OPENAI_BRAIN_URL` | No | Brain microservice URL (default: http://localhost:8100) |
| `FEATURE_PARTNER_PROGRAM` | No | Enable partner program (default: false) |
| `FEATURE_DFY` | No | Enable DFY services (default: false) |
| `FEATURE_CAMPAIGNS` | No | Enable outbound campaigns (default: false) |
