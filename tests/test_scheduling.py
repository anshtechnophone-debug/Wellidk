import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram import ChatMember, ChatPermissions

from app.config import Settings
from app.database.models import Base, TemporaryAction
from app.database.session import create_database
from app.services.moderation import ModerationRepository
from app.services.scheduling import expiration_worker


@pytest.mark.asyncio
async def test_temporary_mute_expiration_restores_bounded_permissions_after_restart(tmp_path):
    engine, sessions = create_database(f"sqlite+aiosqlite:///{tmp_path / 'worker.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    repository = ModerationRepository(sessions)
    saved = {name: True for name in ChatPermissions.__slots__}
    await repository.remember_permissions(-1001, 22, 20, saved)
    await repository.schedule(-1001, 22, "unmute", datetime.now(UTC) - timedelta(seconds=1), saved)
    defaults = {name: True for name in ChatPermissions.__slots__}
    defaults["can_send_photos"] = False
    stop = asyncio.Event()

    async def restrict(*args, **kwargs):
        stop.set()

    bot = SimpleNamespace(
        id=99,
        get_chat_member=AsyncMock(
            side_effect=[
                SimpleNamespace(status=ChatMember.OWNER),
                SimpleNamespace(status=ChatMember.ADMINISTRATOR, can_restrict_members=True),
            ]
        ),
        get_chat=AsyncMock(return_value=SimpleNamespace(permissions=ChatPermissions(**defaults))),
        restrict_chat_member=AsyncMock(side_effect=restrict),
    )
    application = SimpleNamespace(
        bot=bot,
        bot_data={"sessions": sessions, "settings": Settings(bot_token="token", owner_id=10)},
    )

    await expiration_worker(application, stop)

    restored = bot.restrict_chat_member.await_args.kwargs["permissions"]
    assert restored.can_send_messages is True
    assert restored.can_send_photos is False
    async with sessions() as session:
        assert await session.get(TemporaryAction, 1) is None
    assert await repository.restriction_permissions(-1001, 22) is None
    await engine.dispose()


@pytest.mark.asyncio
async def test_expiration_waits_while_owner_is_not_group_admin(tmp_path):
    engine, sessions = create_database(f"sqlite+aiosqlite:///{tmp_path / 'owner.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    repository = ModerationRepository(sessions)
    await repository.schedule(-1001, 22, "unban", datetime.now(UTC) - timedelta(seconds=1))
    stop = asyncio.Event()

    async def notify(*args, **kwargs):
        stop.set()

    bot = SimpleNamespace(
        id=99,
        get_chat_member=AsyncMock(
            side_effect=[
                SimpleNamespace(status=ChatMember.MEMBER),
                SimpleNamespace(status=ChatMember.ADMINISTRATOR, can_restrict_members=True),
            ]
        ),
        send_message=AsyncMock(side_effect=notify),
        unban_chat_member=AsyncMock(),
    )
    application = SimpleNamespace(
        bot=bot,
        bot_data={"sessions": sessions, "settings": Settings(bot_token="token", owner_id=10)},
    )

    await expiration_worker(application, stop)

    bot.unban_chat_member.assert_not_awaited()
    actions = await repository.due_actions()
    assert len(actions) == 1 and actions[0].attempts == 1
    await engine.dispose()
