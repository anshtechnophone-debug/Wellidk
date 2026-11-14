"""Async SQLAlchemy engine and session lifecycle."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import event
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.database.models import Base
from app.database import group_bot_admins as _group_bot_admins  # register group-scoped table


def create_database(database_url: str) -> tuple[AsyncEngine, async_sessionmaker]:
    url = make_url(database_url)
    is_file_sqlite = (
        url.get_backend_name() == "sqlite"
        and url.database not in (None, ":memory:")
    )

    engine_options: dict[str, object] = {"pool_pre_ping": True}
    if is_file_sqlite:
        Path(url.database).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        # Keep the SQLite pool bounded on small VPS instances. WAL allows readers
        # alongside the single SQLite writer; a finite timeout prevents a
        # stalled writer from holding an update task for 30 seconds.
        engine_options.update(
            pool_size=5,
            max_overflow=5,
            pool_timeout=10,
            connect_args={"timeout": 10},
        )

    engine = create_async_engine(database_url, **engine_options)

    if is_file_sqlite:
        @event.listens_for(engine.sync_engine, "connect")
        def _configure_sqlite_connection(dbapi_connection, _connection_record) -> None:
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute("PRAGMA busy_timeout=10000")
                cursor.execute("PRAGMA synchronous=NORMAL")
            finally:
                cursor.close()

    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def initialize_schema(engine: AsyncEngine) -> None:
    """Create missing tables, including the new group-scoped bot-admin table."""
    if engine.dialect.name == "sqlite":
        async with engine.connect() as connection:
            await connection.exec_driver_sql("PRAGMA journal_mode=WAL")
            await connection.commit()

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
