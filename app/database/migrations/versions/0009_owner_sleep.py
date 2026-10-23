"""Add persistent owner sleep sessions.

Revision ID: 0009_owner_sleep
Revises: 0008_appeals
"""
import sqlalchemy as sa
from alembic import op

revision = "0009_owner_sleep"
down_revision = "0008_appeals"
branch_labels = None
depends_on = None

def upgrade() -> None:
    if not sa.inspect(op.get_bind()).has_table("owner_sleep_sessions"):
        op.create_table(
            "owner_sleep_sessions",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("owner_id", sa.BigInteger(), nullable=False),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_owner_sleep_sessions_owner_id", "owner_sleep_sessions", ["owner_id"])
        op.create_index("ix_owner_sleep_owner_start", "owner_sleep_sessions", ["owner_id", "started_at"])

def downgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("owner_sleep_sessions"):
        op.drop_index("ix_owner_sleep_owner_start", table_name="owner_sleep_sessions")
        op.drop_index("ix_owner_sleep_sessions_owner_id", table_name="owner_sleep_sessions")
        op.drop_table("owner_sleep_sessions")
