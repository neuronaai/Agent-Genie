#!/usr/bin/env bash
# ============================================================
# AgentGenie — Release Script
# ============================================================
# Runs automatically on every Render deploy (configured in render.yaml).
#
# 1. Applies database migrations via Flask-Migrate (Alembic).
#    There is NO fallback to db.create_all() — if migrations fail,
#    the deploy is aborted so the issue can be diagnosed.
# 2. Runs production-safe seed (insert-missing-only).
# ============================================================
set -euo pipefail

echo "=== AgentGenie Release Script ==="
echo "Environment: ${FLASK_ENV:-production}"
echo ""

# Step 1: Apply database migrations (strict — no fallback)
echo "--- Applying database migrations ---"
flask db upgrade
echo "Migrations applied successfully."
echo ""

# Step 2: Production-safe seed (insert missing only)
echo "--- Running production seed ---"
python manage.py seed
echo ""

echo "=== Release complete ==="
