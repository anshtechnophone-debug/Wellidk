"""Use SQLite-compatible auto-increment IDs for group message history.

Revision ID: 0009_group_history_autoincrement
Revises: 0008_appeals
"""

import sqlalchemy as sa
from alembic import op

revision = "0009_group_history_autoincrement"
down_revision = "0008_appeals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table("group_message_history"):
        return
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("group_message_history") as batch_op:
            batch_op.alter_column(
                "id",
                existing_type=sa.BigInteger(),
                type_=sa.Integer(),
                existing_nullable=False,
                existing_server_default=None,
            )


def downgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table("group_message_history"):
        return
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("group_message_history") as batch_op:
            batch_op.alter_column(
                "id",
                existing_type=sa.Integer(),
                type_=sa.BigInteger(),
                existing_nullable=False,
                existing_server_default=None,
            )
