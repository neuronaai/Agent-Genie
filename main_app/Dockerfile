# ============================================================
# AgentGenie — Production Dockerfile
# ============================================================
# Used by Render for the web service (Gunicorn).
# The Celery worker uses Dockerfile.worker.
# ============================================================

FROM python:3.11-slim

# Prevent Python from writing .pyc files and enable unbuffered output
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# System dependencies for psycopg2, cryptography, and .doc extraction
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    antiword \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN useradd --create-home appuser
WORKDIR /home/appuser/app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Set ownership
RUN chown -R appuser:appuser /home/appuser/app

USER appuser

# Default port for Gunicorn (overridden by Render's PORT env var)
EXPOSE 10000

# Default command: Gunicorn web server
CMD ["gunicorn", "wsgi:app", "--config", "gunicorn.conf.py"]
