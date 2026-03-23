"""Celery tasks for post-call tool execution.

These tasks are dispatched from the Retell ``call_analyzed`` webhook handler
after the call transcript and summary are available.  Each post-call tool
assignment for the agent is executed as a separate Celery task, ensuring
isolation and independent retry behaviour.

Idempotency: each task receives an ``idempotency_key`` derived from
``{call_log_id}:{function_name}`` to prevent duplicate execution if the
webhook is delivered more than once.
"""
import logging

from app.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    acks_late=True,
    name='tasks.execute_post_call_tool',
)
def execute_post_call_tool(self, assignment_id: str, call_context: dict,
                           idempotency_key: str = None):
    """Execute a single post-call tool assignment.

    Args:
        assignment_id: ID of the AgentToolAssignment to execute.
        call_context: Dict with call data (call_log_id, transcript, summary,
                      from_number, agent_name, etc.).
        idempotency_key: Key to prevent duplicate execution.
    """
    from app import db
    from app.models.core import AgentToolAssignment
    from app.services.tool_engine import execute_tool

    assignment = db.session.get(AgentToolAssignment, assignment_id)
    if not assignment:
        logger.error(f'Post-call task: assignment {assignment_id} not found')
        return {'status': 'error', 'message': 'Assignment not found'}

    try:
        result = execute_tool(assignment, call_context, idempotency_key=idempotency_key)

        if result.get('status') == 'error' and not result.get('duplicate'):
            # Retry on transient failures
            failure = result.get('message', '')
            if any(kw in failure.lower() for kw in ['timeout', 'rate', '429', '503', 'connection']):
                raise self.retry(exc=Exception(failure))

        return result

    except self.MaxRetriesExceededError:
        logger.error(f'Post-call task max retries exceeded: {assignment.function_name}')
        # Update the ActionLog retry_count
        _update_retry_count(assignment_id, call_context.get('call_log_id'), self.request.retries)
        return {'status': 'error', 'message': 'Max retries exceeded'}

    except Exception as e:
        logger.error(f'Post-call task error: {assignment.function_name} - {e}')
        try:
            raise self.retry(exc=e)
        except self.MaxRetriesExceededError:
            _update_retry_count(assignment_id, call_context.get('call_log_id'), self.request.retries)
            return {'status': 'error', 'message': str(e)}


def _update_retry_count(assignment_id: str, call_log_id: str, retries: int):
    """Update the retry count on the ActionLog entry."""
    from app import db
    from app.models.core import ActionLog

    log = ActionLog.query.filter_by(
        assignment_id=assignment_id,
        call_log_id=call_log_id,
    ).order_by(ActionLog.created_at.desc()).first()

    if log:
        log.retry_count = retries
        log.failure_reason = 'max_retries_exceeded'
        db.session.commit()


def dispatch_post_call_tools(agent_id: str, call_log_id: str, call_data: dict):
    """Dispatch all post-call tools for an agent as Celery tasks.

    Called from the Retell ``call_analyzed`` webhook handler.

    Args:
        agent_id: The agent that handled the call.
        call_log_id: The CallLog.id for this call.
        call_data: Dict with transcript, summary, from_number, etc.
    """
    from app.services.tool_engine import get_post_call_tools

    assignments = get_post_call_tools(agent_id)
    if not assignments:
        logger.debug(f'No post-call tools for agent {agent_id}')
        return

    call_context = {
        'call_log_id': call_log_id,
        **call_data,
    }

    for assignment in assignments:
        idempotency_key = f'{call_log_id}:{assignment.function_name}'
        logger.info(f'Dispatching post-call task: {assignment.function_name} for call {call_log_id}')

        execute_post_call_tool.delay(
            assignment_id=assignment.id,
            call_context=call_context,
            idempotency_key=idempotency_key,
        )
