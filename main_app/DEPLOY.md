# AgentGenie — Deployment Guide

**Last Updated:** March 21, 2026

This document covers how to deploy AgentGenie to Render, what runs automatically on each deploy, and how to manage seed data, admin users, and provider integrations in production.

---

## 1. Architecture Overview

AgentGenie deploys as five services on Render, all defined in `render.yaml` at the **repository root**:

| Service | Type | rootDir | Purpose |
| :--- | :--- | :--- | :--- |
| **agentgenie-web** | Web (Docker) | `main_app` | Flask application served by Gunicorn |
| **agentgenie-brain** | Web (Docker) | `services/openai_brain` | FastAPI microservice for OpenAI agent generation |
| **agentgenie-worker** | Worker (Docker) | `main_app` | Celery worker for async provisioning and webhook processing |
| **agentgenie-db** | PostgreSQL 16 | — | Primary relational database |
| **agentgenie-redis** | Redis | — | Celery broker and result backend |

### Repository Layout

```
render.yaml              ← Render Blueprint (repo root — auto-detected)
main_app/                ← Flask web app + Celery worker
  app/                   ← Application code
  migrations/            ← Alembic database migrations
  scripts/release.sh     ← Pre-deploy script (migrations + seed)
  requirements.txt
  wsgi.py
  Dockerfile             ← Web service image
  Dockerfile.worker      ← Worker service image
services/openai_brain/   ← FastAPI brain microservice
  app/
  requirements.txt
  Dockerfile
```

Each service in `render.yaml` specifies an explicit `rootDir` so Render builds from the correct subdirectory. The web service and worker share the same codebase (`main_app/`) but use different start commands. The brain microservice is a separate FastAPI application with its own Docker image. The web service communicates with the brain service via internal HTTP calls.

---

## 2. Prerequisites

Before deploying, you will need accounts and API keys for the following services:

| Provider | Required For | Where to Get | Required? |
| :--- | :--- | :--- | :--- |
| **Stripe** | Billing, subscriptions | [stripe.com/docs/keys](https://stripe.com/docs/keys) | Yes |
| **Retell AI** | Voice agent provisioning and calls | [retellai.com](https://www.retellai.com/) | Yes |
| **OpenAI** | Agent config generation (brain service) | [platform.openai.com](https://platform.openai.com/) | Yes |
| **Gmail** | Email notifications (App Password) | [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords) | Yes |
| **Google Cloud** | Calendar OAuth integration | [console.cloud.google.com](https://console.cloud.google.com/) | Optional |

Gmail SMTP is the default notification provider. You need a Gmail account with an App Password (not your regular password). Two-factor authentication must be enabled on the Gmail account to generate App Passwords.

---

## 3. Deploying to Render

### 3.1 One-Click Deploy

1. Push the repository to GitHub or GitLab. Ensure `render.yaml` is at the **repository root**.
2. Go to [render.com/deploy](https://render.com/deploy) and connect the repository.
3. Render will detect `render.yaml` at the repo root and create all five services automatically. Each service builds from its own `rootDir` subdirectory.
4. Set the required environment variables in the Render dashboard (see Section 5).
5. Trigger the first deploy.

### 3.2 What Runs on Every Deploy

The `preDeployCommand` in `render.yaml` executes `scripts/release.sh`, which performs two steps in order:

```
flask db upgrade          # Apply any pending database migrations
python manage.py seed     # Insert missing seed data only (production-safe)
```

This is **idempotent and non-destructive**. It will never overwrite existing records, delete data, or reset admin-modified values. It only inserts records that do not yet exist, keyed by stable slugs or unique keys.

### 3.3 First Deploy Checklist

After the first successful deploy:

1. Create the first superadmin user (see Section 6).
2. Set all provider API keys in the Render dashboard.
3. Configure Stripe webhooks to point to `https://your-domain.com/api/webhooks/stripe`.
4. Configure Retell webhooks to point to `https://your-domain.com/api/webhooks/retell`.
5. Set `PLATFORM_DOMAIN` to your actual domain (e.g., `app.agentgenie.ai`).
6. Set `GOOGLE_REDIRECT_URI` to `https://your-domain.com/app/integrations/google-calendar/callback`.

---

## 4. Seed Data Strategy

### 4.1 What Gets Seeded

The `manage.py seed` command seeds baseline production data. The corrected seed values are:

**Plans:**

| Plan | Monthly Price | Included Minutes | Agents | Numbers | Overage Rate |
| :--- | :--- | :--- | :--- | :--- | :--- |
| Starter | $99 | 250 | 1 | 1 | $0.39/min |
| Growth | $249 | 800 | 3 | 3 | $0.35/min |
| Scale | $499 | 1,800 | 8 | 8 | $0.32/min |

**Top-Up Packs:**

| Pack | Minutes | Price |
| :--- | :--- | :--- |
| 100 Minutes | 100 | $39 |
| 500 Minutes | 500 | $175 |
| 1,000 Minutes | 1,000 | $320 |

### 4.2 Seed Modes

| Mode | Command | Safe for Production | Behavior |
| :--- | :--- | :--- | :--- |
| **Insert Missing Only** | `python manage.py seed` | Yes (default) | Only creates records that do not exist |
| **Force Update** | `python manage.py seed --force-update` | Manual only | Updates all seed records to match code defaults |
| **Flush + Reseed** | `python manage.py seed --flush` | No (dev only) | Drops all tables and reseeds from scratch |

### 4.3 Demo Data

Demo data is excluded from production by default. Use `--demo` flag in non-production environments only:

```bash
python manage.py seed --demo
```

---

## 5. Environment Variables

### 5.1 Auto-Configured by Render

| Variable | Source |
| :--- | :--- |
| `DATABASE_URL` | From `agentgenie-db` PostgreSQL service |
| `CELERY_BROKER_URL` | From `agentgenie-redis` Redis service |
| `CELERY_RESULT_BACKEND` | From `agentgenie-redis` Redis service |
| `SECRET_KEY` | Auto-generated by Render |
| `CREDENTIAL_ENCRYPTION_KEY` | Auto-generated by Render |
| `FLASK_ENV` | Set to `production` |

### 5.2 Must Be Set Manually

| Variable | Example | Required |
| :--- | :--- | :--- |
| `PLATFORM_DOMAIN` | `app.agentgenie.ai` | Yes |
| `STRIPE_SECRET_KEY` | `sk_live_...` | Yes |
| `STRIPE_WEBHOOK_SECRET` | `whsec_...` | Yes |
| `RETELL_API_KEY` | `key_...` | Yes |
| `OPENAI_API_KEY_CUSTOM` | `sk-...` | Yes |
| `OPENAI_BRAIN_URL` | `https://agentgenie-brain.onrender.com` | Yes |
| `GMAIL_SMTP_USER` | `your-email@gmail.com` | Yes |
| `GMAIL_SMTP_PASSWORD` | `xxxx-xxxx-xxxx-xxxx` | Yes |
| `GMAIL_SMTP_FROM` | `noreply@yourdomain.com` | Recommended |
| `GMAIL_SMTP_FROM_NAME` | `AgentGenie` | Optional |
| `GOOGLE_CLIENT_ID` | `xxxx.apps.googleusercontent.com` | Optional |
| `GOOGLE_CLIENT_SECRET` | (from Google Cloud Console) | Optional |
| `GOOGLE_REDIRECT_URI` | `https://your-domain.com/app/integrations/...` | Optional |

### 5.3 Feature Flags

These control visibility of deferred features. All default to `false`.

| Variable | Controls |
| :--- | :--- |
| `FEATURE_PARTNER_PROGRAM` | Partner registration, dashboard, payouts |
| `FEATURE_DFY` | Done-For-You service marketplace |
| `FEATURE_CAMPAIGNS` | Outbound campaigns, contact lists, quick call |

---

## 6. Creating the First Superadmin

After the first deploy, create a superadmin user via the Render Shell:

```bash
python manage.py create-admin admin@yourdomain.com YourSecurePassword123
```

This creates a user with the `superadmin` role who can access the `/admin` dashboard.

---

## 7. Configuring Webhooks

### 7.1 Stripe Webhooks

In the Stripe Dashboard, create a webhook endpoint:

- **URL:** `https://your-domain.com/api/webhooks/stripe`
- **Events:** `checkout.session.completed`, `invoice.paid`, `invoice.payment_failed`, `customer.subscription.updated`, `customer.subscription.deleted`

### 7.2 Retell Webhooks

In the Retell Dashboard, configure:

- **Post-call webhook URL:** `https://your-domain.com/api/webhooks/retell`
- **Function-call webhook URL:** `https://your-domain.com/api/webhooks/retell/function-call`

---

## 8. Database Migrations

Migrations are managed by Flask-Migrate (Alembic). The release script runs `flask db upgrade` on every deploy. When you change models locally:

1. Run: `flask db migrate -m "Description of change"`
2. Commit the new migration file in `migrations/versions/`.
3. Deploy. The release script applies it automatically.

---

## 9. OpenAI Brain Microservice

The brain microservice runs as a separate Render service (`agentgenie-brain`). It requires:

- `OPENAI_API_KEY_CUSTOM` — same key as the main app
- `MOCK_MODE` — set to `true` for development without OpenAI credits

The main Flask app calls the brain service via `OPENAI_BRAIN_URL`. There is **no in-process fallback** — if the brain service is unavailable, the "Generate with AI" feature returns a clear error directing the operator to check the Brain microservice deployment.

---

## 10. Local Development

```bash
# Clone and setup
git clone <repo-url>
cd <repo-root>/main_app
cp .env.example .env
pip install -r requirements.txt

# Initialize database
flask db upgrade

# Seed baseline + demo data
python manage.py seed --demo

# Start the app
python wsgi.py

# Start Celery worker (separate terminal, requires Redis)
celery -A app.celery_app:celery_app worker --beat --loglevel=info

# Start brain microservice (separate terminal)
cd <repo-root>/services/openai_brain
pip install -r requirements.txt
uvicorn app.main:app --port 8100 --reload
```

---

## 11. Deferred Features

The following features are partially built but gated behind feature flags for this delivery:

| Feature | Flag | Status |
| :--- | :--- | :--- |
| Partner Program | `FEATURE_PARTNER_PROGRAM` | Routes exist, gated off. Future phase. |
| Done-For-You | `FEATURE_DFY` | Full CRUD exists, gated off. Stripe flow needs verification. |
| Outbound Campaigns | `FEATURE_CAMPAIGNS` | Full flow exists, gated off. Requires Retell batch call API access. |

Wildcard subdomain routing for partner white-labeling is reserved for the future partner phase and is not active in this deployment.
