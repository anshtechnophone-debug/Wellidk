"""Add durable Word Seek sessions and scores.

Revision ID: 0003_word_games
Revises: 0002_restriction_snapshots
"""

import sqlalchemy as sa
from alembic import op

from app.database.models import json_value

revision = "0003_word_games"
down_revision = "0002_restriction_snapshots"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("word_game_sessions"):
        op.create_table(
            "word_game_sessions",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("chat_id", sa.BigInteger(), nullable=False),
            sa.Column("user_id", sa.BigInteger(), nullable=False),
            sa.Column("active_key", sa.String(96), nullable=True),
            sa.Column("word_length", sa.Integer(), nullable=False),
            sa.Column("answer", sa.String(6), nullable=False),
            sa.Column("guesses", json_value, nullable=False),
            sa.Column("max_guesses", sa.Integer(), nullable=False),
            sa.Column("state", sa.String(16), nullable=False),
            sa.Column("points_awarded", sa.Boolean(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("active_key"),
        )
        op.create_index("ix_word_game_sessions_chat_id", "word_game_sessions", ["chat_id"])
        op.create_index(
            "ix_word_game_active_user",
            "word_game_sessions",
            ["chat_id", "user_id", "state"],
        )
    if not inspector.has_table("word_game_scores"):
        op.create_table(
            "word_game_scores",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("session_id", sa.Integer(), nullable=False),
            sa.Column("chat_id", sa.BigInteger(), nullable=False),
            sa.Column("user_id", sa.BigInteger(), nullable=False),
            sa.Column("points", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["session_id"], ["word_game_sessions.id"], ondelete="CASCADE"),
            sa.UniqueConstraint("session_id", name="uq_word_game_score_session"),
        )
        op.create_index(
            "ix_word_game_scores_group_points", "word_game_scores", ["chat_id", "points"]
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("word_game_scores"):
        op.drop_index("ix_word_game_scores_group_points", table_name="word_game_scores")
        op.drop_table("word_game_scores")
    if inspector.has_table("word_game_sessions"):
        op.drop_index("ix_word_game_active_user", table_name="word_game_sessions")
        op.drop_index("ix_word_game_sessions_chat_id", table_name="word_game_sessions")
        op.drop_table("word_game_sessions")
