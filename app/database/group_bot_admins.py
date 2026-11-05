"""Group-scoped XYRON bot-admin trust records."""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import BigInteger
from sqlalchemy.orm import Mapped, mapped_column

from app.database.models import Base, UTCDateTime


class GroupBotAdmin(Base):
    """A trusted moderator appointment that exists only in one Telegram group."""
    __tablename__ = "group_bot_admins"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    added_by: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=lambda: datetime.now(UTC), nullable=False
    )
