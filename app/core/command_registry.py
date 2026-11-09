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
    CommandInfo("broadcast", "Copy a replied message to known private users", "/broadcast <seconds> (reply)", "Owner Tools", owner_only=True),
    CommandInfo("broadcast_stop", "Stop an active owner broadcast", "/broadcast_stop", "Owner Tools", owner_only=True),
    CommandInfo("q", "Create a quote sticker from a replied message", "/q [1-3] (reply)", "Quotes"),
    CommandInfo("qrand", "Show a random saved quote from this group", "/qrand", "Quotes"),
    CommandInfo("qtop", "Show this group's top-rated saved quotes", "/qtop", "Quotes"),
    CommandInfo("help", "Browse commands by category", "/help", "Help and About"),
    CommandInfo("about", "About XYRON", "/about", "Help and About"),
    CommandInfo("ping", "Check bot responsiveness", "/ping", "General"),
    CommandInfo("daily", "Show the top 10 group chatters today", "/daily", "Leaderboards"),
    CommandInfo("gw", "Join the global giveaway or manage it in the owner DM", "/gw [start|end|suspicious|reset]", "Giveaway"),
    CommandInfo("prize", "Show the global giveaway leaderboard", "/prize", "Giveaway"),
    CommandInfo("health", "Check database and background workers", "/health", "General"),
    CommandInfo("id", "Show Telegram IDs", "/id [reply]", "User Tools"),
    CommandInfo("admin", "Appoint a XYRON bot admin for this group (group owner only)", "/admin @username or reply (numeric ID also supported)", "Admin Tools", admin=True),
    CommandInfo("promote", "Promote a member (group owner or Telegram admin with promotion permission)", "/promote @username [l|f] (numeric ID or reply also supported)", "Admin Tools", admin=True),
    CommandInfo("smartgc", "Configure Smart GC: abusive-language and NSFW checks, media cleanup, and approved-user exemptions", "/smartgc [on|off]", "Anti-Spam", admin=True),
    CommandInfo("verification", "Turn new-member human verification on or off", "/verification <on|off>", "Group Setup", admin=True),
    CommandInfo("unadmin", "Remove this group’s XYRON bot admin (group owner only)", "/unadmin @username or reply (numeric ID also supported)", "Admin Tools", admin=True),
    CommandInfo("botadminlist", "List this group’s XYRON bot admins (group owner only)", "/botadminlist", "Admin Tools", admin=True),
    CommandInfo("pin", "Pin a replied-to message", "/pin (reply)", "Message Management", True),
    CommandInfo(
        "settings",
        "Inspect eligible groups and bot permissions",
        "/settings",
        "Owner Tools",
        owner_only=True,
    ),
    CommandInfo("ban", "Ban a member", "/ban @username [reason] (numeric ID or reply also supported)", "Ban and Unban", True),
    CommandInfo(
        "dban", "Ban and delete replied message", "/dban @username [reason] (or reply)", "Ban and Unban", True
    ),
    CommandInfo(
        "sban", "Silently ban a member", "/sban @username [reason] (numeric ID or reply also supported)", "Ban and Unban", True
    ),
    CommandInfo(
        "tban",
        "Temporarily ban a member",
        "/tban @username <duration> [reason] (numeric ID or reply also supported)",
        "Ban and Unban",
        True,
    ),
    CommandInfo("unban", "Unban a member", "/unban @username (numeric ID or reply also supported)", "Ban and Unban", True),
    CommandInfo("unbanall", "Unban imported IDs in the configured group (group owner only)", "/unbanall", "Ban and Unban", True),
    CommandInfo(
        "kick", "Remove a member", "/kick @username [reason] (numeric ID or reply also supported)", "Kick and Restrict", True
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
        "/skick @username [reason] (numeric ID or reply also supported)",
        "Kick and Restrict",
        True,
    ),
    CommandInfo("kickme", "Remove yourself from this group", "/kickme", "Kick and Restrict"),
    CommandInfo(
        "mute",
        "Mute permanently or with a duration",
        "/mute @username [duration] [reason] (numeric ID or reply also supported)",
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
        "/smute @username [reason] (numeric ID or reply also supported)",
        "Mute and Unmute",
        True,
    ),
    CommandInfo(
        "tmute",
        "Temporarily restrict a member",
        "/tmute @username <duration> [reason] (numeric ID or reply also supported)",
        "Mute and Unmute",
        True,
    ),
    CommandInfo(
        "unmute",
        "Safely restore saved/default permissions",
        "/unmute @username (numeric ID or reply also supported)",
        "Mute and Unmute",
        True,
    ),
    CommandInfo(
        "mutelist", "List XYRON-tracked mute restrictions", "/mutelist", "Mute and Unmute", True
    ),
    CommandInfo("warn", "Issue a warning", "/warn @username [reason] (numeric ID or reply also supported)", "Warnings", True),
    CommandInfo(
        "warns", "Show a member's warning count", "/warns @username (numeric ID or reply also supported)", "Warnings", True
    ),
    CommandInfo("resetwarn", "Clear warnings", "/resetwarn @username (numeric ID or reply also supported)", "Warnings", True),
    CommandInfo("setwarnlimit", "Set warning threshold", "/setwarnlimit <1-20>", "Warnings", True),
    CommandInfo("modlog", "Show recent moderation actions", "/modlog [count]", "Admin Logs", True),
    CommandInfo("adminlist", "List current administrators", "/adminlist", "Admin Tools", True),
    CommandInfo("report", "Report a replied-to message", "/report [reason]", "Reports"),
    CommandInfo("modules", "Show implemented module status", "/modules", "General"),
    CommandInfo("new4", "Start a 4-letter WordSeek game", "/new4", "Word Games"),
    CommandInfo("new5", "Start a 5-letter WordSeek game", "/new5", "Word Games"),
    CommandInfo("new6", "Start a 6-letter WordSeek game", "/new6", "Word Games"),
    CommandInfo("wordseek", "Start a WordSeek game", "/wordseek [4|5|6]", "Word Games"),
    CommandInfo("guess", "Submit a Word Seek guess", "/guess <word>", "Word Games"),
    CommandInfo("wordcancel", "Cancel your active Word Seek game", "/wordcancel", "Word Games"),
    CommandInfo("wordrank", "Show this chat's Word Seek leaderboard", "/wordrank", "Leaderboards"),
    CommandInfo("family", "Show your global Family Life profile", "/family", "Family Life"),
    CommandInfo("familytree", "Show your Family Life connections", "/familytree", "Family Life"),
    CommandInfo("familytop", "Show the global Family Life leaderboard ranked by couple protection points", "/familytop", "Family Life"),
    CommandInfo("cp", "Open your couple page and protect your partner for 50 daily points", "/cp", "Family Life"),
    CommandInfo("familyhelp", "Explain Family Life commands", "/familyhelp", "Family Life"),
    CommandInfo("familyreset", "Reset all Family Life relationships (XYRON owner only)", "/familyreset", "Family Life", owner_only=True),
    CommandInfo("familyinfo", "Show a replied-to member's Family profile", "/familyinfo (reply)", "Family Life"),
    CommandInfo("wife", "Propose marriage to a replied-to member", "/wife (reply)", "Family Life"),
    CommandInfo("husband", "Propose marriage to a replied-to member", "/husband (reply)", "Family Life"),
    CommandInfo("divorce", "Request mutual divorce from your spouse", "/divorce (reply)", "Family Life"),
    CommandInfo("son", "Make the replied-to user your son", "/son (reply)", "Family Life"),
    CommandInfo("daughter", "Make the replied-to user your daughter", "/daughter (reply)", "Family Life"),
    CommandInfo("father", "Make the replied-to user your father", "/father (reply)", "Family Life"),
    CommandInfo("mother", "Make the replied-to user your mother", "/mother (reply)", "Family Life"),
    CommandInfo("brother", "Add the replied-to user to your sibling group", "/brother (reply)", "Family Life"),
    CommandInfo("sister", "Add the replied-to user to your sibling group", "/sister (reply)", "Family Life"),
    CommandInfo("friend", "Add the replied-to user as a friend", "/friend (reply)", "Family Life"),
    CommandInfo("rules", "Show group rules", "/rules", "Rules"),
    CommandInfo("setrules", "Set this group's rules", "/setrules <text>", "Rules", True),
    CommandInfo("save", "Save a named response", "/save <name> <response>", "Notes", True),
    CommandInfo("get", "Retrieve a saved response", "/get <name>", "Notes"),
    CommandInfo("notes", "List saved response names", "/notes", "Notes"),
    CommandInfo(
        "filter", "Save a text or media response for a trigger", "/filter <name> <response> or reply to media with /filter <name>", "Filters", True
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
        "spamprotector",
        "Owner-controlled automatic spam muting",
        "/spamprotector <on|off>",
        "Anti-Spam",
        owner_only=True,
    ),
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
        "/approve @username (numeric ID or reply also supported)",
        "Admin Tools",
        True,
    ),
    CommandInfo(
        "unapprove", "Remove a member exemption", "/unapprove @username (numeric ID or reply also supported)", "Admin Tools", True
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
        "info", "Show a group member's status", "/info @username (numeric ID or reply also supported)", "User Information"
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
    CommandInfo("tictactoe", "Start a two-player Tic-Tac-Toe challenge in this group", "/tictactoe", "Games"),
    CommandInfo("tttcancel", "Cancel Tic-Tac-Toe", "/tttcancel", "Games"),
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
    CommandInfo("play", "Search Songsx and play a track in the group voice chat", "/play <song name>", "Music"),
    CommandInfo("myplaylist", "Open your personal music playlist", "/myplaylist", "Music"),
    CommandInfo("pause", "Pause music playback", "/pause", "Music", True),
    CommandInfo("resume", "Resume music playback", "/resume", "Music", True),
    CommandInfo("skip", "Skip the current track", "/skip", "Music", True),
    CommandInfo("queue", "Show upcoming music tracks", "/queue", "Music"),
    CommandInfo("np", "Show the current music player card", "/np", "Music"),
    CommandInfo("volume", "Set music volume from 1 to 200", "/volume <1-200>", "Music", True),
    CommandInfo("musichelp", "Show music commands and setup", "/musichelp", "Music"),
    CommandInfo("addaccount", "Connect a voice-player account (owner only)", "/addaccount", "Music", owner_only=True),
    CommandInfo("cancelaccount", "Cancel an active voice-player account login (owner only)", "/cancelaccount", "Music", owner_only=True),
    CommandInfo("accounts", "List connected voice-player accounts (owner only)", "/accounts", "Music", owner_only=True),
    CommandInfo("myid", "Show your Telegram numeric user ID", "/myid", "Music"),
)


def bot_commands(include_owner: bool = False) -> list[BotCommand]:
    # Telegram permits at most 100 commands in each command list/scope.
    # Keep every command available through /help and its handler; the menu
    # shows the first 100 enabled commands to avoid crashing startup.
    return [
        BotCommand(command.name, command.description)
        for command in COMMANDS
        if command.enabled and (include_owner or not command.owner_only)
    ][:100]
