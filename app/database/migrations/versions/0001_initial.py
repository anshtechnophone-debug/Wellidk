"""Initial XYRON schema.

Revision ID: 0001_initial
Revises:
"""

from alembic import op

from app.database.models import Base

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    initial_tables = [
        Base.metadata.tables[name]
        for name in (
            "groups",
            "known_users",
            "moderation_events",
            "temporary_actions",
            "warnings",
            "reminders",
        )
    ]
    Base.metadata.create_all(bind=op.get_bind(), tables=initial_tables)


def downgrade() -> None:
    initial_tables = [
        Base.metadata.tables[name]
        for name in (
            "groups",
            "known_users",
            "moderation_events",
            "temporary_actions",
            "warnings",
            "reminders",
        )
    ]
    Base.metadata.drop_all(bind=op.get_bind(), tables=initial_tables)
