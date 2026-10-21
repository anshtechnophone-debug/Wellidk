"""Persist de-duplicated, rate-limited reports.

Revision ID: 0005_reports
Revises: 0004_ai_usage
"""

import sqlalchemy as sa
from alembic import op

revision = "0005_reports"
down_revision = "0004_ai_usage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("reports"):
        return
    op.create_table(
        "reports",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=False),
        sa.Column("reported_user_id", sa.BigInteger(), nullable=False),
        sa.Column("reporter_id", sa.BigInteger(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("ai_summary", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("chat_id", "message_id", "reporter_id", name="uq_reporter_message"),
    )
    op.create_index("ix_reports_chat_id", "reports", ["chat_id"])
    op.create_index("ix_reports_created_at", "reports", ["created_at"])
    op.create_index("ix_reports_group_created", "reports", ["chat_id", "created_at"])
    op.create_index(
        "ix_reports_reporter_created", "reports", ["chat_id", "reporter_id", "created_at"]
    )


def downgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("reports"):
        op.drop_index("ix_reports_reporter_created", table_name="reports")
        op.drop_index("ix_reports_group_created", table_name="reports")
        op.drop_index("ix_reports_created_at", table_name="reports")
        op.drop_index("ix_reports_chat_id", table_name="reports")
        op.drop_table("reports")
