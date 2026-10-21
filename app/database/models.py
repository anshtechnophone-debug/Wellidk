"""SQLAlchemy models for durable community and moderation state."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import JSON, TypeDecorator


class Base(DeclarativeBase):
    pass


class UTCDateTime(TypeDecorator[datetime]):
    impl = DateTime
    cache_ok = True

    def load_dialect_impl(self, dialect):
        return dialect.type_descriptor(DateTime(timezone=True))

    def process_bind_param(self, value: datetime | None, dialect):
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("database timestamps must be timezone-aware")
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect):
        if value is None or value.tzinfo is not None:
            return value
        return value.replace(tzinfo=UTC)


json_value = JSON().with_variant(JSONB, "postgresql")


class Group(Base):
    __tablename__ = "groups"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    title: Mapped[str] = mapped_column(String(255), default="")
    warn_limit: Mapped[int] = mapped_column(Integer, default=3)
    warn_action: Mapped[str] = mapped_column(String(16), default="mute")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())


class KnownUser(Base):
    __tablename__ = "known_users"
    __table_args__ = (Index("ix_known_users_group_username", "chat_id", "username"),)

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str | None] = mapped_column(String(64))
    first_name: Mapped[str] = mapped_column(String(255), default="")
    last_seen: Mapped[datetime] = mapped_column(UTCDateTime())


class ModerationEvent(Base):
    __tablename__ = "moderation_events"
    __table_args__ = (Index("ix_moderation_events_group_created", "chat_id", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("groups.chat_id"), index=True)
    actor_id: Mapped[int] = mapped_column(BigInteger)
    target_id: Mapped[int] = mapped_column(BigInteger)
    action: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())


class TemporaryAction(Base):
    __tablename__ = "temporary_actions"
    __table_args__ = (
        UniqueConstraint(
            "chat_id", "target_id", "action", name="uq_temporary_action_chat_target_action"
        ),
        Index("ix_temporary_actions_expires", "expires_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("groups.chat_id"), index=True)
    target_id: Mapped[int] = mapped_column(BigInteger)
    action: Mapped[str] = mapped_column(String(16))
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime())
    original_permissions: Mapped[dict[str, bool] | None] = mapped_column(json_value)
    attempts: Mapped[int] = mapped_column(Integer, default=0)


class RestrictionSnapshot(Base):
    __tablename__ = "restriction_snapshots"
    __table_args__ = (Index("ix_restriction_snapshots_group_user", "chat_id", "target_id"),)

    chat_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("groups.chat_id", ondelete="CASCADE"), primary_key=True
    )
    target_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    permissions: Mapped[dict[str, bool]] = mapped_column(json_value)
    actor_id: Mapped[int] = mapped_column(BigInteger)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())


class WarningRecord(Base):
    __tablename__ = "warnings"
    __table_args__ = (Index("ix_warnings_group_user", "chat_id", "target_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("groups.chat_id"), index=True)
    actor_id: Mapped[int] = mapped_column(BigInteger)
    target_id: Mapped[int] = mapped_column(BigInteger)
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())


class Reminder(Base):
    __tablename__ = "reminders"
    __table_args__ = (Index("ix_reminders_due", "due_at", "delivered_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger)
    creator_id: Mapped[int] = mapped_column(BigInteger)
    message: Mapped[str] = mapped_column(Text)
    due_at: Mapped[datetime] = mapped_column(UTCDateTime())
    delivered_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class WordGameSession(Base):
    __tablename__ = "word_game_sessions"
    __table_args__ = (Index("ix_word_game_active_user", "chat_id", "user_id", "state"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    user_id: Mapped[int] = mapped_column(BigInteger)
    active_key: Mapped[str | None] = mapped_column(String(96), unique=True, nullable=True)
    word_length: Mapped[int] = mapped_column(Integer)
    answer: Mapped[str] = mapped_column(String(6))
    guesses: Mapped[list[str]] = mapped_column(json_value, default=list)
    max_guesses: Mapped[int] = mapped_column(Integer, default=6)
    state: Mapped[str] = mapped_column(String(16), default="active")
    points_awarded: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime())


class WordGameScore(Base):
    __tablename__ = "word_game_scores"
    __table_args__ = (
        UniqueConstraint("session_id", name="uq_word_game_score_session"),
        Index("ix_word_game_scores_group_points", "chat_id", "points"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("word_game_sessions.id", ondelete="CASCADE")
    )
    chat_id: Mapped[int] = mapped_column(BigInteger)
    user_id: Mapped[int] = mapped_column(BigInteger)
    points: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())


class CasualGameSession(Base):
    __tablename__ = "casual_game_sessions"
    __table_args__ = (
        UniqueConstraint("active_key", name="uq_casual_game_active_key"),
        Index("ix_casual_game_active", "chat_id", "game_type", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    user_id: Mapped[int] = mapped_column(BigInteger)
    game_type: Mapped[str] = mapped_column(String(24))
    active_key: Mapped[str | None] = mapped_column(String(96), nullable=True)
    state: Mapped[dict[str, object]] = mapped_column(json_value)
    status: Mapped[str] = mapped_column(String(16), default="active")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime())


class CasualGameScore(Base):
    __tablename__ = "casual_game_scores"
    __table_args__ = (
        UniqueConstraint("session_id", name="uq_casual_game_score_session"),
        Index("ix_casual_game_scores_board", "chat_id", "game_type", "points"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("casual_game_sessions.id", ondelete="CASCADE")
    )
    chat_id: Mapped[int] = mapped_column(BigInteger)
    user_id: Mapped[int] = mapped_column(BigInteger)
    game_type: Mapped[str] = mapped_column(String(24))
    points: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())


class AIUsageEvent(Base):
    __tablename__ = "ai_usage_events"
    __table_args__ = (
        Index("ix_ai_usage_chat_created", "chat_id", "created_at"),
        Index("ix_ai_usage_user_created", "chat_id", "user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    user_id: Mapped[int] = mapped_column(BigInteger)
    trigger_type: Mapped[str] = mapped_column(String(24))
    tokens: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="reserved")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)


class ReportRecord(Base):
    __tablename__ = "reports"
    __table_args__ = (
        UniqueConstraint("chat_id", "message_id", "reporter_id", name="uq_reporter_message"),
        Index("ix_reports_group_created", "chat_id", "created_at"),
        Index("ix_reports_reporter_created", "chat_id", "reporter_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    message_id: Mapped[int] = mapped_column(BigInteger)
    reported_user_id: Mapped[int] = mapped_column(BigInteger)
    reporter_id: Mapped[int] = mapped_column(BigInteger)
    reason: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="open")
    ai_summary: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)


class GroupPolicy(Base):
    __tablename__ = "group_policies"

    chat_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("groups.chat_id", ondelete="CASCADE"), primary_key=True
    )
    policy: Mapped[dict[str, object]] = mapped_column(json_value, default=dict)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())


class AppealRecord(Base):
    __tablename__ = "appeals"
    __table_args__ = (
        Index("ix_appeals_group_created", "chat_id", "created_at"),
        Index("ix_appeals_user_created", "chat_id", "user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    user_id: Mapped[int] = mapped_column(BigInteger)
    moderation_event_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("moderation_events.id", ondelete="SET NULL"), nullable=True
    )
    message: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="open")
    reviewed_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
