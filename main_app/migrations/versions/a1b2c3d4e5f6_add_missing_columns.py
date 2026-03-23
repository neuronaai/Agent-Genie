"""Add missing columns: plan_definitions.sort_order, notification in-app fields,
organization profile fields, and tenant_id on child models.

Revision ID: a1b2c3d4e5f6
Revises: 3ab22db173a5
Create Date: 2026-03-20 12:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'a1b2c3d4e5f6'
down_revision = '3ab22db173a5'
branch_labels = None
depends_on = None


def upgrade():
    # --- plan_definitions.sort_order ---
    with op.batch_alter_table('plan_definitions', schema=None) as batch_op:
        batch_op.add_column(sa.Column('sort_order', sa.Integer(), nullable=True, server_default='0'))

    # --- notifications: title, message, link, is_read ---
    with op.batch_alter_table('notifications', schema=None) as batch_op:
        batch_op.add_column(sa.Column('title', sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column('message', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('link', sa.String(length=512), nullable=True))
        batch_op.add_column(sa.Column('is_read', sa.Boolean(), nullable=True, server_default='false'))
        # Relax subject/body NOT NULL since in-app notifications may only use title/message
        batch_op.alter_column('subject', existing_type=sa.String(length=255), nullable=True)
        batch_op.alter_column('body', existing_type=sa.Text(), nullable=True)

    # Back-fill is_read for existing rows
    op.execute("UPDATE notifications SET is_read = false WHERE is_read IS NULL")
    with op.batch_alter_table('notifications', schema=None) as batch_op:
        batch_op.alter_column('is_read', nullable=False)

    # --- organizations: website, support_email, support_phone ---
    with op.batch_alter_table('organizations', schema=None) as batch_op:
        batch_op.add_column(sa.Column('website', sa.String(length=512), nullable=True))
        batch_op.add_column(sa.Column('support_email', sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column('support_phone', sa.String(length=50), nullable=True))

    # --- tenant_id on child models (agent_configs, agent_versions, etc.) ---
    # These may already exist if db-init was used; use batch mode for safety.
    _add_tenant_id_if_missing('agent_configs')
    _add_tenant_id_if_missing('agent_versions')
    _add_tenant_id_if_missing('workflow_definitions')
    _add_tenant_id_if_missing('handoff_rules')
    _add_tenant_id_if_missing('guardrail_rules')
    _add_tenant_id_if_missing('recording_metadata')


def _add_tenant_id_if_missing(table_name):
    """Add tenant_id column to a table if it doesn't already exist."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = [c['name'] for c in inspector.get_columns(table_name)]
    if 'tenant_id' not in columns:
        with op.batch_alter_table(table_name, schema=None) as batch_op:
            batch_op.add_column(sa.Column('tenant_id', sa.String(length=36), nullable=True))


def downgrade():
    # --- Remove tenant_id from child models ---
    for table_name in ['recording_metadata', 'guardrail_rules', 'handoff_rules',
                       'workflow_definitions', 'agent_versions', 'agent_configs']:
        conn = op.get_bind()
        inspector = sa.inspect(conn)
        columns = [c['name'] for c in inspector.get_columns(table_name)]
        if 'tenant_id' in columns:
            with op.batch_alter_table(table_name, schema=None) as batch_op:
                batch_op.drop_column('tenant_id')

    # --- organizations ---
    with op.batch_alter_table('organizations', schema=None) as batch_op:
        batch_op.drop_column('support_phone')
        batch_op.drop_column('support_email')
        batch_op.drop_column('website')

    # --- notifications ---
    with op.batch_alter_table('notifications', schema=None) as batch_op:
        batch_op.alter_column('body', existing_type=sa.Text(), nullable=False)
        batch_op.alter_column('subject', existing_type=sa.String(length=255), nullable=False)
        batch_op.drop_column('is_read')
        batch_op.drop_column('link')
        batch_op.drop_column('message')
        batch_op.drop_column('title')

    # --- plan_definitions ---
    with op.batch_alter_table('plan_definitions', schema=None) as batch_op:
        batch_op.drop_column('sort_order')
