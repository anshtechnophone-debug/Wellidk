"""Shared command metadata used to generate truthful help output."""

from __future__ import annotations

from dataclasses import dataclass

from telegram import BotCommand


@dataclass(frozen=True, slots=True)
class CommandInfo:
    name: str
    description: str
    usage: str
    category: str
    admin: bool = False
    enabled: bool = True
    owner_only: bool = False


COMMANDS = (
    CommandInfo("start", "Start XYRON", "/start", "General"),
    CommandInfo("help", "Show implemented commands", "/help", "General"),
    CommandInfo("about", "About XYRON", "/about", "General"),
    CommandInfo("ping", "Check bot responsiveness", "/ping", "General"),
    CommandInfo("id", "Show Telegram IDs", "/id [reply]", "General"),
    CommandInfo(
        "settings",
        "Inspect eligible groups and bot permissions",
        "/settings",
        "General",
        owner_only=True,
    ),
    CommandInfo("ban", "Ban a member", "/ban <reply|user_id> [reason]", "Moderation", True),
    CommandInfo(
        "dban",
        "Ban and delete replied message",
        "/dban <reply|user_id> [reason]",
        "Moderation",
        True,
    ),
    CommandInfo(
        "sban", "Silently ban a member", "/sban <reply|user_id> [reason]", "Moderation", True
    ),
    CommandInfo(
        "tban",
        "Temporarily ban a member",
        "/tban <reply|user_id> <duration> [reason]",
        "Moderation",
        True,
    ),
    CommandInfo("unban", "Unban a member", "/unban <user_id>", "Moderation", True),
    CommandInfo("kick", "Remove a member", "/kick <reply|user_id> [reason]", "Moderation", True),
    CommandInfo(
        "dkick",
        "Remove a member and delete replied message",
        "/dkick <reply|user_id> [reason]",
        "Moderation",
        True,
    ),
    CommandInfo(
        "skick", "Silently remove a member", "/skick <reply|user_id> [reason]", "Moderation", True
    ),
    CommandInfo("kickme", "Remove yourself from this group", "/kickme", "Moderation"),
    CommandInfo("mute", "Restrict a member", "/mute <reply|user_id> [reason]", "Moderation", True),
    CommandInfo(
        "dmute",
        "Restrict and delete replied message",
        "/dmute <reply|user_id> [reason]",
        "Moderation",
        True,
    ),
    CommandInfo(
        "smute", "Silently restrict a member", "/smute <reply|user_id> [reason]", "Moderation", True
    ),
    CommandInfo(
        "tmute",
        "Temporarily restrict a member",
        "/tmute <reply|user_id> <duration> [reason]",
        "Moderation",
        True,
    ),
    CommandInfo(
        "unmute",
        "Restore a member's default permissions",
        "/unmute <reply|user_id>",
        "Moderation",
        True,
    ),
    CommandInfo("warn", "Issue a warning", "/warn <reply|user_id> [reason]", "Moderation", True),
    CommandInfo(
        "warns", "Show a member's warning count", "/warns <reply|user_id>", "Moderation", True
    ),
    CommandInfo(
        "resetwarn", "Clear a member's warnings", "/resetwarn <reply|user_id>", "Moderation", True
    ),
    CommandInfo(
        "setwarnlimit", "Set warning threshold", "/setwarnlimit <1-20>", "Moderation", True
    ),
    CommandInfo("modlog", "Show recent moderation actions", "/modlog [count]", "Moderation", True),
    CommandInfo("adminlist", "List current administrators", "/adminlist", "Moderation", True),
    CommandInfo("report", "Report a replied-to message", "/report [reason]", "Community"),
    CommandInfo("modules", "Show implemented module status", "/modules", "General"),
)


def bot_commands() -> list[BotCommand]:
    return [
        BotCommand(command.name, command.description) for command in COMMANDS if command.enabled
    ]
