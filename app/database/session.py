"""Async SQLAlchemy engine and session lifecycle."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import event
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.database.models import Base


def create_database(database_url: str) -> tuple[AsyncEngine, async_sessionmaker]:
    url = make_url(database_url)
    is_file_sqlite = (
        url.get_backend_name() == "sqlite"
        and url.database not in (None, ":memory:")
    )

    engine_options: dict[str, object] = {"pool_pre_ping": True}
    if is_file_sqlite:
        Path(url.database).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        # Wait for a concurrent SQLite writer instead of failing immediately with
        # "database is locked".
        engine_options["connect_args"] = {"timeout": 30}

    engine = create_async_engine(database_url, **engine_options)

    if is_file_sqlite:
        @event.listens_for(engine.sync_engine, "connect")
        def _configure_sqlite_connection(dbapi_connection, _connection_record) -> None:
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute("PRAGMA busy_timeout=30000")
                cursor.execute("PRAGMA synchronous=NORMAL")
            finally:
                cursor.close()

    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def initialize_schema(engine: AsyncEngine) -> None:
    """Create a fresh development database; production upgrades use Alembic."""
    if engine.dialect.name == "sqlite":
        # WAL allows readers to continue while a writer is active. Set it once
        # during startup, before background workers begin using the database.
        async with engine.connect() as connection:
            await connection.exec_driver_sql("PRAGMA journal_mode=WAL")
            await connection.commit()

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
