"""Celery application factory and configuration.

Usage:
    # Start worker (with embedded beat scheduler):
    celery -A app.celery_app:celery_app worker --beat --loglevel=info

    # Or start beat separately:
    celery -A app.celery_app:celery_app beat --loglevel=info

Bootstrap strategy:
    When this module is imported (by the ``celery -A`` flag), it eagerly
    creates a Flask application via ``create_app()`` and binds it to the
    Celery instance through ``init_celery()``.  This ensures every Celery
    task executes inside a Flask application context with full access to
    SQLAlchemy, config, and other extensions — even though no WSGI server
    is running.
"""
from celery import Celery
from celery.schedules import crontab

celery_app = Celery('agentgenie')

# Default config — will be updated by init_celery() below
celery_app.config_from_object({
    'broker_url': 'redis://localhost:6379/0',
    'result_backend': 'redis://localhost:6379/0',
    'task_serializer': 'json',
    'result_serializer': 'json',
    'accept_content': ['json'],
    'timezone': 'UTC',
    'enable_utc': True,
    'task_acks_late': True,
    'task_reject_on_worker_lost': True,
    'task_default_retry_delay': 30,
    'task_max_retries': 3,
    # ── Periodic task schedule (Celery Beat) ──
    'beat_schedule': {
        'cleanup-expired-recordings': {
            'task': 'recording.cleanup_expired',
            'schedule': crontab(hour=3, minute=0),  # Daily at 03:00 UTC
            'options': {'queue': 'default'},
        },
    },
    # ── Auto-discover task modules ──
    'include': [
        'app.tasks.agent_tasks',
        'app.tasks.billing_tasks',
        'app.tasks.post_call_tasks',
        'app.tasks.recording_tasks',
        'app.tasks.webhook_tasks',
    ],
})


def init_celery(flask_app):
    """Bind the Celery instance to a Flask application context.

    This ensures tasks can access Flask extensions (db, config, etc.).
    """
    celery_app.conf.update(
        broker_url=flask_app.config.get('CELERY_BROKER_URL', 'redis://localhost:6379/0'),
        result_backend=flask_app.config.get('CELERY_RESULT_BACKEND', 'redis://localhost:6379/0'),
    )

    class ContextTask(celery_app.Task):
        """Ensure every task runs inside the Flask app context."""
        abstract = True

        def __call__(self, *args, **kwargs):
            with flask_app.app_context():
                return self.run(*args, **kwargs)

    celery_app.Task = ContextTask
    return celery_app


# ── Eager bootstrap ──────────────────────────────────────────────────────
# When the Celery worker imports this module (``celery -A app.celery_app``),
# we immediately create a Flask app and bind it.  This is the standard
# pattern for Flask + Celery integration — the Flask app is created once
# at worker startup and shared across all task invocations.
from app import create_app as _create_app  # noqa: E402

_flask_app = _create_app()
init_celery(_flask_app)
