import asyncio
from datetime import UTC, datetime

import pytest
from telegram import Chat, Message, Update, User

from app.services.update_processor import PerChatUpdateProcessor


def make_update(update_id: int, chat_id: int) -> Update:
    user = User(id=100 + update_id, first_name="Tester", is_bot=False)
    chat = Chat(id=chat_id, type=Chat.GROUP)
    message = Message(
        message_id=update_id,
        date=datetime.now(UTC),
        chat=chat,
        from_user=user,
        text="test",
    )
    return Update(update_id=update_id, message=message)


@pytest.mark.asyncio
async def test_updates_in_same_chat_are_serialized():
    processor = PerChatUpdateProcessor(max_concurrent_updates=8)
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    second_started = asyncio.Event()

    async def first():
        first_started.set()
        await release_first.wait()

    async def second():
        second_started.set()

    first_task = asyncio.create_task(
        processor.do_process_update(make_update(1, -1001), first())
    )
    await first_started.wait()
    second_task = asyncio.create_task(
        processor.do_process_update(make_update(2, -1001), second())
    )

    await asyncio.sleep(0)
    assert not second_started.is_set()

    release_first.set()
    await asyncio.gather(first_task, second_task)
    assert second_started.is_set()


@pytest.mark.asyncio
async def test_updates_in_different_chats_can_run_concurrently():
    processor = PerChatUpdateProcessor(max_concurrent_updates=8)
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    second_finished = asyncio.Event()

    async def first():
        first_started.set()
        await release_first.wait()

    async def second():
        second_finished.set()

    first_task = asyncio.create_task(
        processor.do_process_update(make_update(1, -1001), first())
    )
    await first_started.wait()
    second_task = asyncio.create_task(
        processor.do_process_update(make_update(2, -1002), second())
    )

    await asyncio.wait_for(second_finished.wait(), timeout=1)
    release_first.set()
    await asyncio.gather(first_task, second_task)
