"""Expand kb_type_enum with richer knowledge-base categories.

The baseline migration created kb_type_enum with only: text, url, file, faq.
The application model and UI now support additional types required by the
original product specification:
  - service              (service descriptions)
  - discount             (discount / promotional details)
  - hours_location       (hours / location details)
  - support_escalation   (support escalation instructions)
  - booking_link         (booking links / calendars)
  - handoff_instruction  (handoff instructions)

PostgreSQL enums are expanded with ALTER TYPE ... ADD VALUE which is
non-transactional and must run outside a transaction block.

Revision ID: b7c8d9e0f1a2
Revises: a1b2c3d4e5f6
Create Date: 2026-03-21 14:00:00.000000
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = 'b7c8d9e0f1a2'
down_revision = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None

# New enum values to add (in the order they appear in the model definition)
_NEW_VALUES = [
    'service',
    'discount',
    'hours_location',
    'support_escalation',
    'booking_link',
    'handoff_instruction',
]


def upgrade():
    # ALTER TYPE ... ADD VALUE cannot run inside a transaction on PostgreSQL.
    # We must execute each statement with autocommit / outside the migration
    # transaction.  Alembic's op.execute() runs inside the migration txn by
    # default, so we obtain the raw connection and set autocommit.
    bind = op.get_bind()

    # Detect dialect — only PostgreSQL needs ALTER TYPE
    if bind.dialect.name == 'postgresql':
        # Fetch existing enum values so the migration is idempotent
        result = bind.execute(
            __import__('sqlalchemy').text(
                "SELECT enumlabel FROM pg_enum "
                "WHERE enumtypid = 'kb_type_enum'::regtype"
            )
        )
        existing = {row[0] for row in result}

        for val in _NEW_VALUES:
            if val not in existing:
                # Must run outside transaction for ADD VALUE
                op.execute(
                    f"COMMIT"
                )
                op.execute(
                    f"ALTER TYPE kb_type_enum ADD VALUE IF NOT EXISTS '{val}'"
                )
    # SQLite (used in tests) stores enums as VARCHAR — no action needed.


def downgrade():
    # PostgreSQL does not support DROP VALUE from an enum.  Removing values
    # would require recreating the type + column, which is destructive.
    # For safety we leave the enum expanded; the old code simply never
    # inserts the new values.
    pass
