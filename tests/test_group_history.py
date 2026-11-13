from datetime import UTC, datetime

import pytest
from sqlalchemy import Integer, inspect

from app.database.models import Base
from app.database.session import create_database
from app.services.group_history import GroupHistoryRepository


@pytest.fixture
async def history_repository(tmp_path):
    engine, sessions = create_database(f"sqlite+aiosqlite:///{tmp_path / 'history.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield GroupHistoryRepository(sessions), engine, sessions
    await engine.dispose()


@pytest.mark.asyncio
async def test_group_history_inserts_without_manual_id_allocation(history_repository):
    repository, engine, _ = history_repository
    now = datetime.now(UTC)

    await repository.save_message(
        chat_id=-1001,
        message_id=10,
        user_id=20,
        username="member",
        display_name="Member",
        message_text="hello",
        sent_at=now,
    )
    await repository.save_message(
        chat_id=-1001,
        message_id=11,
        user_id=21,
        username=None,
        display_name="Another member",
        message_text="world",
        sent_at=now,
    )

    rows = await repository.recent(chat_id=-1001, hours=24)
    assert [row["message_id"] for row in rows] == [10, 11]
    async with engine.connect() as connection:
        column_type = await connection.run_sync(
            lambda sync_connection: next(
                column["type"]
                for column in inspect(sync_connection).get_columns("group_message_history")
                if column["name"] == "id"
            )
        )
    assert isinstance(column_type, Integer)


@pytest.mark.asyncio
async def test_group_history_edit_updates_existing_message(history_repository):
    repository, _, _ = history_repository
    now = datetime.now(UTC)

    await repository.save_message(
        chat_id=-1001,
        message_id=10,
        user_id=20,
        username="old_name",
        display_name="Old Name",
        message_text="before",
        sent_at=now,
    )
    await repository.save_message(
        chat_id=-1001,
        message_id=10,
        user_id=20,
        username="new_name",
        display_name="New Name",
        message_text="after",
        sent_at=now,
    )

    rows = await repository.recent(chat_id=-1001, hours=24)
    assert len(rows) == 1
    assert rows[0]["text"] == "after"
    assert rows[0]["username"] == "new_name"
    assert rows[0]["display_name"] == "New Name"
