# AgentGenie — Render Deployment Guide

This guide provides step-by-step instructions for deploying the AgentGenie platform to Render using the provided `render.yaml` blueprint.

> **Canonical reference**: For full architecture details, environment variables, seed data, and migration workflow, see `DEPLOY.md`.

## Prerequisites

1. A **Render account** (https://render.com)
2. A **GitHub repository** containing the AgentGenie source code
3. A **Stripe account** (for billing and subscriptions)
4. A **Retell AI account** (for voice agent provisioning and calls)
5. An **OpenAI account** (for the Brain microservice — agent config generation)
6. A **Gmail account with App Password** (for email notifications)
7. A **Google Cloud Console project** (optional — for Calendar OAuth)

## Repository Layout

The deployment package has the following top-level structure:

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

Each service in `render.yaml` specifies an explicit `rootDir` so Render builds from the correct subdirectory:

| Service | rootDir | Build context |
| :--- | :--- | :--- |
| agentgenie-web | `main_app` | Flask app, Gunicorn |
| agentgenie-worker | `main_app` | Celery worker (same codebase as web) |
| agentgenie-brain | `services/openai_brain` | FastAPI microservice |

## Step 1: Push Code to GitHub

1. Extract the provided `agentgenie.zip` file.
2. Verify that `render.yaml` is at the **repository root** (not inside `main_app/`).
3. Initialize a Git repository and push it to GitHub:
   ```bash
   git init
   git add .
   git commit -m "Initial commit"
   git branch -M main
   git remote add origin https://github.com/yourusername/agentgenie.git
   git push -u origin main
   ```

## Step 2: Deploy via Render Blueprint

Render Blueprints allow you to deploy the entire infrastructure from a single configuration file.

1. Log in to your Render Dashboard.
2. Click **New +** and select **Blueprint**.
3. Connect your GitHub account and select the `agentgenie` repository.
4. Render will automatically detect the `render.yaml` file in the repository root.
5. Review the detected services — you should see web, brain, worker, PostgreSQL, and Redis.
6. Click **Apply Blueprint**.

Render will now provision five services:

| Service | Type | Purpose |
| :--- | :--- | :--- |
| **agentgenie-db** | PostgreSQL 16 | Primary relational database |
| **agentgenie-redis** | Redis | Celery broker and result backend |
| **agentgenie-web** | Web (Docker) | Flask application served by Gunicorn |
| **agentgenie-brain** | Web (Docker) | FastAPI microservice for OpenAI agent generation |
| **agentgenie-worker** | Worker (Docker) | Celery worker for async provisioning and webhook processing |

*Note: The first deploy might take a few minutes as it installs dependencies, runs migrations, and seeds the database.*

## Step 3: Configure Environment Variables

The blueprint automatically sets up the database and Redis connections, but you need to provide your API keys and secrets.

1. In the Render Dashboard, go to the **agentgenie-web** service.
2. Click on **Environment** in the left sidebar.
3. Add the following required environment variables:

### Security
- `SECRET_KEY`: A long, random string (e.g., generate with `openssl rand -hex 32`)
- `CREDENTIAL_ENCRYPTION_KEY`: A 32-byte url-safe base64-encoded string

### Stripe
- `STRIPE_SECRET_KEY`: Your Stripe secret key (`sk_live_...` or `sk_test_...`)
- `STRIPE_WEBHOOK_SECRET`: Your Stripe webhook signing secret (`whsec_...`)

### Retell AI
- `RETELL_API_KEY`: Your Retell API key

### OpenAI
- `OPENAI_API_KEY_CUSTOM`: Your OpenAI API key (used by the Brain microservice)
- `OPENAI_BRAIN_URL`: Internal URL of the brain service (e.g., `https://agentgenie-brain.onrender.com`)

### Gmail SMTP (Notifications)
- `GMAIL_SMTP_USER`: Your Gmail address
- `GMAIL_SMTP_PASSWORD`: A Gmail App Password (not your regular password)
- `GMAIL_SMTP_FROM`: The "From" email address (recommended: `noreply@yourdomain.com`)
- `GMAIL_SMTP_FROM_NAME`: Display name (optional, e.g., `AgentGenie`)

### Google OAuth (Optional — Calendar Integration)
- `GOOGLE_CLIENT_ID`: Your Google OAuth Client ID
- `GOOGLE_CLIENT_SECRET`: Your Google OAuth Client Secret
- `GOOGLE_REDIRECT_URI`: `https://your-render-url.onrender.com/app/integrations/google-calendar/callback`

4. **Important**: The `render.yaml` blueprint propagates shared env vars to the worker service automatically. Verify that the **agentgenie-worker** service has the same database, Redis, Retell, and Gmail SMTP variables.

## Step 4: Database Migrations and Seeding

The `preDeployCommand` in `render.yaml` executes `scripts/release.sh`, which runs two steps on every deploy:

```
flask db upgrade          # Apply pending Alembic migrations
python manage.py seed     # Insert missing seed data only (production-safe)
```

This is **idempotent and non-destructive**. It will never overwrite existing records or delete data. It only inserts records that do not yet exist.

The `migrations/` directory is included in the repository and contains all schema definitions. There is **no `db.create_all()` fallback** — the migration path is the single source of truth for the database schema.

If the first deploy fails because the database was not ready yet, simply click **Manual Deploy > Deploy latest commit** in the Render dashboard after the database shows as "Available".

## Step 5: Create the Admin User

Once the web service is live, create your first superadmin user:

1. In the Render Dashboard, go to the **agentgenie-web** service.
2. Click on **Shell** in the left sidebar.
3. Run:
   ```bash
   python manage.py create-admin your@email.com yourpassword
   ```
4. Log in at `https://your-render-url.onrender.com/login`.

## Step 6: Configure Webhooks

### 1. Stripe Webhook
- Go to Stripe Dashboard > Developers > Webhooks
- Add endpoint: `https://your-render-url.onrender.com/api/webhooks/stripe`
- Events to listen for:
  - `checkout.session.completed`
  - `invoice.paid`
  - `invoice.payment_failed`
  - `customer.subscription.updated`
  - `customer.subscription.deleted`

### 2. Retell Webhook
- Go to Retell Dashboard
- Set the post-call webhook URL to: `https://your-render-url.onrender.com/api/webhooks/retell`
- Set the function-call webhook URL to: `https://your-render-url.onrender.com/api/webhooks/retell/function-call`

### 3. Google OAuth Redirect URI (if using Calendar integration)
- Go to Google Cloud Console > APIs & Services > Credentials
- Edit your OAuth 2.0 Client ID
- Add Authorized redirect URI: `https://your-render-url.onrender.com/app/integrations/google-calendar/callback`

## Troubleshooting

- **Deploy fails with migration errors**: Check that the `migrations/` directory is committed to the repository and contains the baseline migration. Run `flask db upgrade` manually in the Render Shell to see detailed errors.
- **Worker not processing tasks**: Ensure the `agentgenie-worker` service has the exact same environment variables as the web service, especially the database and Redis URLs.
- **"Generate with AI" returns an error**: Verify that the `agentgenie-brain` service is running and that `OPENAI_BRAIN_URL` is set correctly on the web service. There is no in-process fallback — the Brain microservice must be deployed and reachable.
- **500 Internal Server Error on login**: Check the Render logs. This usually means the `SECRET_KEY` is missing or the database was not seeded properly.
