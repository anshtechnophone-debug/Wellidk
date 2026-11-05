"""Add persistent family proposal requests.

Revision ID: 0010_family_requests
Revises: 0009_global_family
"""
import sqlalchemy as sa
from alembic import op

revision = "0010_family_requests"
down_revision = "0009_global_family"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if not sa.inspect(op.get_bind()).has_table("family_requests"):
        op.create_table(
            "family_requests",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("requester_id", sa.BigInteger(), nullable=False),
            sa.Column("target_id", sa.BigInteger(), nullable=False),
            sa.Column("kind", sa.String(16), nullable=False),
            sa.Column("requester_role", sa.String(16), nullable=True),
            sa.Column("chat_id", sa.BigInteger(), nullable=False),
            sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_family_requests_target_status", "family_requests", ["target_id", "status"])


def downgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("family_requests"):
        op.drop_index("ix_family_requests_target_status", table_name="family_requests")
        op.drop_table("family_requests")
