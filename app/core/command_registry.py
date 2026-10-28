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
    CommandInfo("help", "Browse commands by category", "/help", "Help and About"),
    CommandInfo("about", "About XYRON", "/about", "Help and About"),
    CommandInfo("ping", "Check bot responsiveness", "/ping", "General"),
    CommandInfo("health", "Check database and background workers", "/health", "General"),
    CommandInfo("id", "Show Telegram IDs", "/id [reply]", "User Tools"),
    CommandInfo("admin", "Add a XYRON bot admin (owner only)", "/admin <numeric_user_id>", "Owner Tools", owner_only=True),
    CommandInfo("unadmin", "Remove a XYRON bot admin (owner only)", "/unadmin <numeric_user_id>", "Owner Tools", owner_only=True),
    CommandInfo("botadminlist", "List all bot admins in DM or only those present in the current group", "/botadminlist", "Owner Tools", owner_only=True),
    CommandInfo("pin", "Pin a replied-to message", "/pin (reply)", "Message Management", True),
    CommandInfo(
        "settings",
        "Inspect eligible groups and bot permissions",
        "/settings",
        "Owner Tools",
        owner_only=True,
    ),
    CommandInfo("ban", "Ban a member", "/ban <reply|user_id> [reason]", "Ban and Unban", True),
    CommandInfo(
        "dban", "Ban and delete replied message", "/dban <reply> [reason]", "Ban and Unban", True
    ),
    CommandInfo(
        "sban", "Silently ban a member", "/sban <reply|user_id> [reason]", "Ban and Unban", True
    ),
    CommandInfo(
        "tban",
        "Temporarily ban a member",
        "/tban <reply|user_id> <duration> [reason]",
        "Ban and Unban",
        True,
    ),
    CommandInfo("unban", "Unban a member", "/unban <user_id>", "Ban and Unban", True),
    CommandInfo(
        "kick", "Remove a member", "/kick <reply|user_id> [reason]", "Kick and Restrict", True
    ),
    CommandInfo(
        "dkick",
        "Remove a member and delete replied message",
        "/dkick <reply> [reason]",
        "Kick and Restrict",
        True,
    ),
    CommandInfo(
        "skick",
        "Silently remove a member",
        "/skick <reply|user_id> [reason]",
        "Kick and Restrict",
        True,
    ),
    CommandInfo("kickme", "Remove yourself from this group", "/kickme", "Kick and Restrict"),
    CommandInfo(
        "mute",
        "Mute permanently or with a duration",
        "/mute <reply|id> [duration] [reason]",
        "Mute and Unmute",
        True,
    ),
    CommandInfo(
        "dmute",
        "Restrict and delete replied message",
        "/dmute <reply> [reason]",
        "Mute and Unmute",
        True,
    ),
    CommandInfo(
        "smute",
        "Silently restrict a member",
        "/smute <reply|user_id> [reason]",
        "Mute and Unmute",
        True,
    ),
    CommandInfo(
        "tmute",
        "Temporarily restrict a member",
        "/tmute <reply|user_id> <duration> [reason]",
        "Mute and Unmute",
        True,
    ),
    CommandInfo(
        "unmute",
        "Safely restore saved/default permissions",
        "/unmute <reply|user_id>",
        "Mute and Unmute",
        True,
    ),
    CommandInfo(
        "mutelist", "List XYRON-tracked mute restrictions", "/mutelist", "Mute and Unmute", True
    ),
    CommandInfo("warn", "Issue a warning", "/warn <reply|user_id> [reason]", "Warnings", True),
    CommandInfo(
        "warns", "Show a member's warning count", "/warns <reply|user_id>", "Warnings", True
    ),
    CommandInfo("resetwarn", "Clear warnings", "/resetwarn <reply|user_id>", "Warnings", True),
    CommandInfo("setwarnlimit", "Set warning threshold", "/setwarnlimit <1-20>", "Warnings", True),
    CommandInfo("modlog", "Show recent moderation actions", "/modlog [count]", "Admin Logs", True),
    CommandInfo("adminlist", "List current administrators", "/adminlist", "Admin Tools", True),
    CommandInfo("report", "Report a replied-to message", "/report [reason]", "Reports"),
    CommandInfo("modules", "Show implemented module status", "/modules", "General"),
    CommandInfo("wordseek", "Start a local Word Seek game", "/wordseek [4|5|6]", "Word Games"),
    CommandInfo("guess", "Submit a Word Seek guess", "/guess <word>", "Word Games"),
    CommandInfo("wordcancel", "Cancel your active Word Seek game", "/wordcancel", "Word Games"),
    CommandInfo("wordrank", "Show this chat's Word Seek leaderboard", "/wordrank", "Leaderboards"),
    CommandInfo("rules", "Show group rules", "/rules", "Rules"),
    CommandInfo("setrules", "Set this group's rules", "/setrules <text>", "Rules", True),
    CommandInfo("save", "Save a named response", "/save <name> <response>", "Notes", True),
    CommandInfo("get", "Retrieve a saved response", "/get <name>", "Notes"),
    CommandInfo("notes", "List saved response names", "/notes", "Notes"),
    CommandInfo(
        "filter", "Reply when a phrase appears", "/filter <phrase> <response>", "Filters", True
    ),
    CommandInfo("filters", "List saved response filters", "/filters", "Filters"),
    CommandInfo("stop", "Remove a saved filter", "/stop <phrase>", "Filters", True),
    CommandInfo(
        "lock",
        "Enable a supported message lock",
        "/lock <links|forwards|stickers|media|bots|commands>",
        "Locks",
        True,
    ),
    CommandInfo("unlock", "Disable a message lock", "/unlock <lock>", "Locks", True),
    CommandInfo("locks", "List enabled message locks", "/locks", "Locks"),
    CommandInfo(
        "antiflood",
        "Configure message flood deletion",
        "/antiflood <on|off> [threshold] [seconds] [repeat threshold]",
        "Anti-Spam",
        True,
    ),
    CommandInfo(
        "approve",
        "Exempt a member from configured protections",
        "/approve <reply|user_id>",
        "Admin Tools",
        True,
    ),
    CommandInfo(
        "unapprove", "Remove a member exemption", "/unapprove <reply|user_id>", "Admin Tools", True
    ),
    CommandInfo(
        "setwelcome",
        "Set or clear the member welcome",
        "/setwelcome <template>",
        "Welcome and Goodbye",
        True,
    ),
    CommandInfo(
        "setgoodbye",
        "Set or clear the member goodbye",
        "/setgoodbye <template>",
        "Welcome and Goodbye",
        True,
    ),
    CommandInfo("del", "Delete the replied-to message", "/del (reply)", "Message Management", True),
    CommandInfo(
        "purge",
        "Delete up to 100 messages from a reply onward",
        "/purge [1-100] (reply to first)",
        "Message Management",
        True,
    ),
    CommandInfo(
        "info", "Show a group member's status", "/info <user_id> or reply", "User Information"
    ),
    CommandInfo("groupinfo", "Show current group information", "/groupinfo", "Group Information"),
    CommandInfo("reports", "List open group reports", "/reports", "Reports", True),
    CommandInfo(
        "reviewreport",
        "Set a report review status",
        "/reviewreport <report_id> <reviewed|dismissed|actioned>",
        "Reports",
        True,
    ),
    CommandInfo("hangman", "Start a local Hangman game", "/hangman", "Games"),
    CommandInfo("letter", "Guess a Hangman letter", "/letter <a-z>", "Games"),
    CommandInfo("hangcancel", "Cancel Hangman", "/hangcancel", "Games"),
    CommandInfo("scramble", "Start a scrambled word game", "/scramble", "Word Games"),
    CommandInfo("unscramble", "Submit a scrambled word answer", "/unscramble <word>", "Word Games"),
    CommandInfo("scramblecancel", "Cancel the scramble game", "/scramblecancel", "Word Games"),
    CommandInfo("numbergame", "Start a local number guessing game", "/numbergame", "Games"),
    CommandInfo("numberguess", "Submit a number guess", "/numberguess <1-100>", "Games"),
    CommandInfo("numbercancel", "Cancel Number Guess", "/numbercancel", "Games"),
    CommandInfo(
        "gamerank",
        "Show a local game leaderboard",
        "/gamerank <hangman|scramble|number>",
        "Leaderboards",
    ),
    CommandInfo("trivia", "Start a local trivia question", "/trivia", "Quiz Games"),
    CommandInfo(
        "triviaanswer", "Answer the current trivia question", "/triviaanswer <a|b|c>", "Quiz Games"
    ),
    CommandInfo("truefalse", "Start a true/false question", "/truefalse", "Quiz Games"),
    CommandInfo("tfanswer", "Answer true or false", "/tfanswer <true|false>", "Quiz Games"),
    CommandInfo("riddle", "Start a local riddle", "/riddle", "Fun"),
    CommandInfo("riddleanswer", "Answer the current riddle", "/riddleanswer <word>", "Fun"),
    CommandInfo("quickmath", "Start a local arithmetic challenge", "/quickmath", "Fun"),
    CommandInfo("mathanswer", "Answer the arithmetic challenge", "/mathanswer <number>", "Fun"),
    CommandInfo("questioncancel", "Cancel a question game", "/questioncancel <type>", "Games"),
    CommandInfo(
        "rps", "Play non-wagering Rock Paper Scissors", "/rps <rock|paper|scissors>", "Fun"
    ),
    CommandInfo("tictactoe", "Start Tic-Tac-Toe against XYRON", "/tictactoe", "Games"),
    CommandInfo("tttmove", "Play a Tic-Tac-Toe square", "/tttmove <1-9>", "Games"),
    CommandInfo("tttcancel", "Cancel Tic-Tac-Toe", "/tttcancel", "Games"),
    CommandInfo("wordchain", "Start a local Word Chain game", "/wordchain", "Word Games"),
    CommandInfo("chain", "Submit the next Word Chain word", "/chain <word>", "Word Games"),
    CommandInfo("wordchaincancel", "Cancel Word Chain", "/wordchaincancel", "Word Games"),
    CommandInfo("anagram", "Start a single-answer Anagram challenge", "/anagram", "Word Games"),
    CommandInfo(
        "anagramanswer", "Submit the Anagram answer", "/anagramanswer <word>", "Word Games"
    ),
    CommandInfo("teamword", "Start a cooperative group word game", "/teamword", "Games"),
    CommandInfo("teamguess", "Contribute a team word guess", "/teamguess <word>", "Games"),
    CommandInfo("teamwordcancel", "Cancel the group Team Word game", "/teamwordcancel", "Games"),
    CommandInfo(
        "banlist", "List bans tracked by moderation history", "/banlist", "Ban and Unban", True
    ),
    CommandInfo(
        "modstats", "Show moderation event counts for 30 days", "/modstats", "Admin Logs", True
    ),
    CommandInfo(
        "remind", "Set a durable personal reminder", "/remind <duration> <message>", "Reminders"
    ),
    CommandInfo("reminders", "List your pending reminders", "/reminders", "Reminders"),
    CommandInfo("cancelreminder", "Cancel your reminder", "/cancelreminder <id>", "Reminders"),
    CommandInfo("appeal", "Submit an appeal for admin review", "/appeal <message>", "Reports"),
    CommandInfo("appeals", "List open moderation appeals", "/appeals", "Admin Logs", True),
    CommandInfo(
        "reviewappeal",
        "Review an appeal without auto-changing moderation",
        "/reviewappeal <id> <accepted|rejected|needs_info> [note]",
        "Admin Logs",
        True,
    ),
)


def bot_commands() -> list[BotCommand]:
    return [
        BotCommand(command.name, command.description) for command in COMMANDS if command.enabled
    ]
