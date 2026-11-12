from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy import select

from app.database.models import Base, CasualGameSession
from app.database.session import create_database
from app.modules.games.board_handlers import _TTT_TIMEOUT, _ttt_active_key, tictactoe_command


@pytest.mark.asyncio
async def test_different_members_can_host_independent_tictactoe_games(tmp_path):
    engine, sessions = create_database(f"sqlite+aiosqlite:///{tmp_path / 'tictactoe.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async def start_game(user_id, message_id):
        message = SimpleNamespace(
            reply_text=AsyncMock(return_value=SimpleNamespace(message_id=message_id))
        )
        update = SimpleNamespace(
            effective_chat=SimpleNamespace(id=-100123, type="group"),
            effective_user=SimpleNamespace(id=user_id, full_name=f"Player {user_id}"),
            effective_message=message,
        )
        application = SimpleNamespace(
            bot_data={"sessions": sessions},
            create_task=Mock(),
        )
        await tictactoe_command(update, SimpleNamespace(application=application))
        return message

    try:
        first = await start_game(101, 1)
        second = await start_game(202, 2)

        assert first.reply_text.await_count == 1
        assert second.reply_text.await_count == 1
        async with sessions() as session:
            games = list(
                await session.scalars(
                    select(CasualGameSession).where(
                        CasualGameSession.game_type == "tictactoe",
                        CasualGameSession.status == "waiting",
                    )
                )
            )
        assert len(games) == 2
        assert {game.user_id for game in games} == {101, 202}
        assert len({game.active_key for game in games}) == 2
    finally:
        await engine.dispose()


def test_tictactoe_invitation_expires_after_three_minutes():
    assert _TTT_TIMEOUT.total_seconds() == 180
    assert _ttt_active_key(-100123, 101) != _ttt_active_key(-100123, 202)
