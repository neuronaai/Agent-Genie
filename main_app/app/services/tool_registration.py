"""Tool registration service — syncs agent tool assignments to Retell LLM.

When a tenant enables or disables tools on an agent, this service updates
the Retell LLM configuration to register/unregister the corresponding
``general_tools`` so that the LLM can invoke them during live calls.

Only real-time tools are registered with Retell.  Post-call tools are
dispatched by the Celery task system after the call ends and do not need
to be known to the LLM.
"""
import logging

from flask import current_app

from app import db

logger = logging.getLogger(__name__)


def sync_agent_tools(agent_id: str) -> dict:
    """Sync all real-time tool assignments for an agent to Retell LLM.

    Reads the agent's tool assignments, builds the Retell ``general_tools``
    payload, and calls ``update_retell_llm`` to register them.

    Returns:
        {'status': 'ok'|'error', 'tools_registered': int, 'message': str}
    """
    from app.models.core import Agent, AgentConfig, AgentToolAssignment
    from app.services import retell_adapter
    from app.services.tool_engine import TOOL_CATALOG

    agent = db.session.get(Agent, agent_id)
    if not agent or not agent.retell_agent_id:
        return {'status': 'error', 'message': 'Agent not found or not provisioned in Retell.'}

    # Get the LLM ID from the agent config
    config = AgentConfig.query.filter_by(agent_id=agent_id).order_by(
        AgentConfig.version.desc()
    ).first()
    if not config:
        return {'status': 'error', 'message': 'Agent config not found.'}

    biz_ctx = config.business_context or {}
    llm_id = biz_ctx.get('retell_llm_id')
    if not llm_id:
        return {'status': 'error', 'message': 'Retell LLM ID not found in agent config.'}

    # Get real-time tool assignments
    assignments = AgentToolAssignment.query.filter_by(
        agent_id=agent_id, tool_type='real_time'
    ).all()

    # Build the function call webhook URL
    platform_domain = current_app.config.get('PLATFORM_DOMAIN', 'localhost:5000')
    protocol = 'https' if 'localhost' not in platform_domain else 'http'
    webhook_url = f'{protocol}://{platform_domain}/api/webhooks/retell/function-call'

    # Build Retell general_tools list
    general_tools = []
    for assignment in assignments:
        catalog_entry = TOOL_CATALOG.get(assignment.function_name, {})
        parameters = catalog_entry.get('parameters', {
            'type': 'object', 'properties': {}, 'required': []
        })

        tool_def = {
            'type': 'end_call' if 'end_call' in assignment.function_name else 'custom',
            'name': assignment.function_name,
            'description': catalog_entry.get(
                'llm_description',
                assignment.description_for_llm or assignment.function_name
            ),
            'url': webhook_url,
            'speak_during_execution': True,
            'speak_after_execution': True,
            'parameters': parameters,
        }
        general_tools.append(tool_def)

    # Update the Retell LLM
    result = retell_adapter.update_retell_llm(
        llm_id=llm_id,
        general_tools=general_tools,
    )

    if result.get('status') in ('success', 'ok'):
        logger.info(f'Synced {len(general_tools)} real-time tools for agent {agent_id}')
        return {
            'status': 'ok',
            'tools_registered': len(general_tools),
            'message': f'{len(general_tools)} tools registered with Retell LLM.',
        }
    else:
        logger.error(f'Failed to sync tools for agent {agent_id}: {result}')
        return {
            'status': 'error',
            'message': result.get('message', 'Failed to update Retell LLM.'),
        }


def register_tool_for_agent(agent_id: str, function_name: str, tool_type: str,
                            connection_id: str, description_for_llm: str = None) -> dict:
    """Create a tool assignment and sync to Retell if real-time.

    This is the primary API for enabling a tool on an agent from the UI.
    """
    from app.models.core import Agent, AgentToolAssignment

    agent = db.session.get(Agent, agent_id)
    if not agent:
        return {'status': 'error', 'message': 'Agent not found.'}

    # Check for existing assignment
    existing = AgentToolAssignment.query.filter_by(
        agent_id=agent_id, function_name=function_name
    ).first()
    if existing:
        return {'status': 'ok', 'message': 'Tool already assigned.', 'assignment_id': existing.id}

    assignment = AgentToolAssignment(
        agent_id=agent_id,
        connection_id=connection_id,
        function_name=function_name,
        description_for_llm=description_for_llm or function_name,
        tool_type=tool_type,
    )
    db.session.add(assignment)
    db.session.commit()

    # Sync real-time tools to Retell
    if tool_type == 'real_time':
        sync_result = sync_agent_tools(agent_id)
        if sync_result.get('status') != 'ok':
            logger.warning(f'Tool assigned but Retell sync failed: {sync_result}')

    return {'status': 'ok', 'assignment_id': assignment.id, 'message': 'Tool assigned.'}


def unregister_tool_from_agent(agent_id: str, function_name: str) -> dict:
    """Remove a tool assignment and sync to Retell if real-time."""
    from app.models.core import AgentToolAssignment

    assignment = AgentToolAssignment.query.filter_by(
        agent_id=agent_id, function_name=function_name
    ).first()
    if not assignment:
        return {'status': 'ok', 'message': 'Tool not assigned.'}

    tool_type = assignment.tool_type
    db.session.delete(assignment)
    db.session.commit()

    if tool_type == 'real_time':
        sync_result = sync_agent_tools(agent_id)
        if sync_result.get('status') != 'ok':
            logger.warning(f'Tool removed but Retell sync failed: {sync_result}')

    return {'status': 'ok', 'message': 'Tool removed.'}
