"""Add tenant_settings column and recordings_enabled platform setting.

Adds:
  - organizations.tenant_settings (JSONB, nullable) for per-tenant
    feature toggles such as recordings_enabled.
  - Seeds the 'recordings_enabled' PlatformSetting with a default of
    True so the global toggle exists for admin control.

Revision ID: c3d4e5f6a7b8
Revises: b7c8d9e0f1a2
Create Date: 2026-03-21 16:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'c3d4e5f6a7b8'
down_revision = 'b7c8d9e0f1a2'
branch_labels = None
depends_on = None


def upgrade():
    # Add tenant_settings JSONB column to organizations
    # Use JSON for SQLite compatibility, JSONB for PostgreSQL
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        col_type = postgresql.JSONB(astext_type=sa.Text())
    else:
        col_type = sa.JSON()

    op.add_column(
        'organizations',
        sa.Column('tenant_settings', col_type, nullable=True),
    )

    # Seed the recordings_enabled platform setting (idempotent)
    platform_settings = sa.table(
        'platform_settings',
        sa.column('id', sa.String),
        sa.column('key', sa.String),
        sa.column('value', col_type),
        sa.column('description', sa.String),
    )

    # Check if already exists
    conn = op.get_bind()
    result = conn.execute(
        sa.text("SELECT id FROM platform_settings WHERE key = 'recordings_enabled'")
    )
    if result.fetchone() is None:
        import uuid
        op.execute(
            platform_settings.insert().values(
                id=str(uuid.uuid4()),
                key='recordings_enabled',
                value=True,
                description='Global toggle: whether call recordings are visible to tenants',
            )
        )


def downgrade():
    op.drop_column('organizations', 'tenant_settings')

    op.execute(
        sa.text("DELETE FROM platform_settings WHERE key = 'recordings_enabled'")
    )
