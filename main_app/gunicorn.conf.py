"""Gunicorn configuration for AgentGenie production deployment."""
import os
import multiprocessing

# Bind to Render's PORT env var, default 10000
bind = f"0.0.0.0:{os.environ.get('PORT', '10000')}"

# Workers: 2-4x CPU cores is typical; Render Starter has 1 CPU
workers = int(os.environ.get('WEB_CONCURRENCY', multiprocessing.cpu_count() * 2 + 1))
worker_class = 'sync'
timeout = 120
keepalive = 5

# Logging
accesslog = '-'
errorlog = '-'
loglevel = os.environ.get('LOG_LEVEL', 'info')

# Graceful restart
graceful_timeout = 30
max_requests = 1000
max_requests_jitter = 50

# Preload app for faster worker startup
preload_app = True
