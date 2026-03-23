"""Recording retention enforcement tasks.

Provides a periodic Celery task that enforces the platform-wide
``recording_retention_days`` setting by:
  1. Nullifying ``recording_url`` on expired CallLog rows.
  2. Deleting the associated RecordingMetadata rows.

The task is designed to be scheduled via Celery Beat (see celery_app.py).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from app.celery_app import celery_app

logger = logging.getLogger(__name__)


def _get_retention_days() -> int:
    """Read recording_retention_days from PlatformSetting, default 90."""
    from app.models.core import PlatformSetting
    from app import db

    setting = db.session.query(PlatformSetting).filter_by(
        key='recording_retention_days'
    ).first()
    if setting and setting.value is not None:
        try:
            return int(setting.value)
        except (TypeError, ValueError):
            pass
    return 90  # default


@celery_app.task(name='recording.cleanup_expired', bind=True, max_retries=2)
def cleanup_expired_recordings(self):
    """Delete recordings older than the configured retention window.

    This task:
    - Reads ``recording_retention_days`` from PlatformSetting.
    - Finds all CallLog rows with a ``recording_url`` whose
      ``created_at`` is older than the cutoff.
    - Nullifies ``recording_url`` on those CallLog rows.
    - Deletes the corresponding RecordingMetadata rows.
    - Logs a summary of how many recordings were purged.

    Safe to run multiple times (idempotent).
    """
    from app.models.core import CallLog, RecordingMetadata
    from app import db

    retention_days = _get_retention_days()
    if retention_days <= 0:
        logger.info('Recording retention disabled (retention_days=%d), skipping cleanup.', retention_days)
        return {'purged': 0, 'retention_days': retention_days}

    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    logger.info(
        'Running recording cleanup: retention=%d days, cutoff=%s',
        retention_days, cutoff.isoformat()
    )

    try:
        # Find expired call logs that still have a recording_url
        expired_calls = (
            db.session.query(CallLog)
            .filter(
                CallLog.recording_url.isnot(None),
                CallLog.created_at < cutoff,
            )
            .all()
        )

        if not expired_calls:
            logger.info('No expired recordings found.')
            return {'purged': 0, 'retention_days': retention_days}

        expired_ids = [c.id for c in expired_calls]
        count = len(expired_ids)

        # Delete associated RecordingMetadata rows
        deleted_meta = (
            db.session.query(RecordingMetadata)
            .filter(RecordingMetadata.call_log_id.in_(expired_ids))
            .delete(synchronize_session='fetch')
        )

        # Nullify recording_url on the CallLog rows
        (
            db.session.query(CallLog)
            .filter(CallLog.id.in_(expired_ids))
            .update(
                {CallLog.recording_url: None},
                synchronize_session='fetch',
            )
        )

        db.session.commit()
        logger.info(
            'Recording cleanup complete: purged %d recording URLs, '
            'deleted %d metadata rows (retention=%d days).',
            count, deleted_meta, retention_days
        )
        return {
            'purged': count,
            'metadata_deleted': deleted_meta,
            'retention_days': retention_days,
        }

    except Exception as exc:
        db.session.rollback()
        logger.error('Recording cleanup failed: %s', exc, exc_info=True)
        raise self.retry(exc=exc, countdown=300)
