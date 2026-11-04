"""Create a central registry of Telegram users encountered by XYRON.

Revision ID: 0009_global_user_registry
Revises: 0008_appeals
"""

from __future__ import annotations

from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision = "0009_global_user_registry"
down_revision = "0008_appeals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("global_users"):
        op.create_table(
            "global_users",
            sa.Column("user_id", sa.BigInteger(), primary_key=True),
            sa.Column("username", sa.String(64), nullable=True),
            sa.Column("first_name", sa.String(255), nullable=False, server_default=""),
            sa.Column("last_name", sa.String(255), nullable=False, server_default=""),
            sa.Column("is_bot", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("seen_in_private", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("seen_in_groups", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
            sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
        )

    inspector = sa.inspect(bind)
    if not inspector.has_index("global_users", "ix_global_users_username"):
        op.create_index("ix_global_users_username", "global_users", ["username"])

    # Backfill group users already observed by the group-specific tracker.
    users: dict[int, dict[str, object]] = {}
    if inspector.has_table("known_users"):
        known = sa.Table("known_users", sa.MetaData(), autoload_with=bind)
        for row in bind.execute(sa.select(known)).mappings():
            user_id = int(row["user_id"])
            seen = row["last_seen"] or datetime.now(UTC)
            record = users.get(user_id)
            if record is None:
                users[user_id] = {
                    "user_id": user_id,
                    "username": row["username"],
                    "first_name": row["first_name"] or "",
                    "last_name": "",
                    "is_bot": False,
                    "seen_in_private": False,
                    "seen_in_groups": True,
                    "first_seen": seen,
                    "last_seen": seen,
                }
            else:
                record["seen_in_groups"] = True
                if row["username"]:
                    record["username"] = row["username"]
                if row["first_name"]:
                    record["first_name"] = row["first_name"]
                record["first_seen"] = min(record["first_seen"], seen)
                record["last_seen"] = max(record["last_seen"], seen)

    # Merge the private users already collected by the broadcast tracker.
    if inspector.has_table("broadcast_recipients"):
        recipients = sa.Table("broadcast_recipients", sa.MetaData(), autoload_with=bind)
        for row in bind.execute(sa.select(recipients)).mappings():
            user_id = int(row["user_id"])
            first_seen = row["first_seen"] or datetime.now(UTC)
            last_seen = row["last_seen"] or first_seen
            record = users.get(user_id)
            if record is None:
                users[user_id] = {
                    "user_id": user_id,
                    "username": None,
                    "first_name": "",
                    "last_name": "",
                    "is_bot": False,
                    "seen_in_private": True,
                    "seen_in_groups": False,
                    "first_seen": first_seen,
                    "last_seen": last_seen,
                }
            else:
                record["seen_in_private"] = True
                record["first_seen"] = min(record["first_seen"], first_seen)
                record["last_seen"] = max(record["last_seen"], last_seen)

    if users:
        global_users = sa.Table("global_users", sa.MetaData(), autoload_with=bind)
        bind.execute(global_users.insert(), list(users.values()))


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("global_users"):
        op.drop_index("ix_global_users_username", table_name="global_users")
        op.drop_table("global_users")
