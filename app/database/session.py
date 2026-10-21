"""Async SQLAlchemy engine and session lifecycle."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.database.models import Base


def create_database(database_url: str) -> tuple[AsyncEngine, async_sessionmaker]:
    url = make_url(database_url)
    if url.get_backend_name() == "sqlite" and url.database not in (None, ":memory:"):
        Path(url.database).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
    engine = create_async_engine(database_url, pool_pre_ping=True)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def initialize_schema(engine: AsyncEngine) -> None:
    """Create a fresh development database; production upgrades use Alembic."""
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
