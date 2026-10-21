"""Pure helpers for the paginated, command-catalogue-driven help menu."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.core.command_registry import COMMANDS, CommandInfo

PAGE_SIZE = 8


@dataclass(frozen=True, slots=True)
class HelpPage:
    text: str
    keyboard: InlineKeyboardMarkup


def _categories() -> dict[str, list[CommandInfo]]:
    grouped: dict[str, list[CommandInfo]] = defaultdict(list)
    for command in COMMANDS:
        if command.enabled:
            grouped[command.category].append(command)
    return dict(sorted(grouped.items()))


def _category_key(category: str) -> str:
    return category.casefold().replace(" ", "_")


def _find_category(key: str) -> tuple[str, list[CommandInfo]] | None:
    return next(
        (
            (name, commands)
            for name, commands in _categories().items()
            if _category_key(name) == key
        ),
        None,
    )


def _example(command: CommandInfo) -> str:
    if command.name in {"dban", "dkick", "dmute"}:
        return f"Reply to the target's message, then send /{command.name} spam"
    if command.name == "report":
        return "Reply to the message, then send /report possible harassment"
    example = command.usage
    replacements = {
        "<reply|user_id>": "123456789",
        "<reply|id>": "123456789",
        "<user_id>": "123456789",
        "<reply>": "123456789",
        "<duration>": "30m",
        "[duration]": "30m",
        "<message>": "drink water",
        "<text>": "Be respectful",
        "<template>": "Welcome {first_name}",
        "<name>": "welcome",
        "<response>": "Thanks for joining",
        "<phrase>": "hello",
        "[reason]": "spam",
        "[count]": "5",
        "[threshold]": "5",
        "[seconds]": "10",
        "[repeat threshold]": "3",
        "<1-20>": "3",
        "<1-9>": "5",
        "<1-100>": "42",
        "[4|5|6]": "5",
        "<word>": "crane",
        "<five-letter word>": "crane",
        "<a|b|c>": "b",
        "<true|false>": "true",
        "<number>": "42",
        "<a-z>": "a",
        "<type>": "trivia",
        "<rock|paper|scissors>": "rock",
        "<on|off>": "on",
        "<hangman|scramble|number>": "hangman",
        "<trivia|truefalse|riddle|quickmath>": "trivia",
        "<lock>": "links",
        "<links|forwards|stickers|media|bots|commands>": "links",
        "<id>": "1",
        "<report_id>": "1",
        "<reviewed|dismissed|actioned>": "reviewed",
        "<accepted|rejected|needs_info>": "accepted",
        "[1-100]": "10",
        "[reply]": "",
    }
    for placeholder, value in replacements.items():
        example = example.replace(placeholder, value)
    return " ".join(example.split())


def build_help_page(category_key: str | None = None, page_number: int = 0) -> HelpPage:
    categories = _categories()
    if category_key is None:
        buttons = [
            InlineKeyboardButton(name, callback_data=f"help:cat:{_category_key(name)}:0")
            for name in categories
        ]
        rows = [buttons[index : index + 2] for index in range(0, len(buttons), 2)]
        rows.append([InlineKeyboardButton("Close", callback_data="help:close")])
        return HelpPage("XYRON Help\nChoose a command category:", InlineKeyboardMarkup(rows))

    found = _find_category(category_key)
    if found is None:
        return build_help_page()
    category, commands = found
    pages = max(1, (len(commands) + PAGE_SIZE - 1) // PAGE_SIZE)
    page_number = min(max(page_number, 0), pages - 1)
    selected = commands[page_number * PAGE_SIZE : (page_number + 1) * PAGE_SIZE]
    sections = []
    for command in selected:
        permission = (
            "owner only" if command.owner_only else "group admin" if command.admin else "everyone"
        )
        sections.append(
            f"{command.usage}\n{command.description}\n"
            f"Example: {_example(command)}\nPermission: {permission}"
        )
    rows = []
    navigation = []
    if page_number > 0:
        navigation.append(
            InlineKeyboardButton(
                "Back",
                callback_data=f"help:cat:{_category_key(category)}:{page_number - 1}",
            )
        )
    navigation.append(InlineKeyboardButton("Home", callback_data="help:home"))
    if page_number + 1 < pages:
        navigation.append(
            InlineKeyboardButton(
                "Next",
                callback_data=f"help:cat:{_category_key(category)}:{page_number + 1}",
            )
        )
    rows.append(navigation)
    rows.append([InlineKeyboardButton("Close", callback_data="help:close")])
    text = f"{category} ({page_number + 1}/{pages})\n\n" + "\n\n".join(sections)
    return HelpPage(text, InlineKeyboardMarkup(rows))


def parse_help_callback(data: str) -> tuple[str | None, int] | None:
    parts = data.split(":")
    if parts == ["help", "home"]:
        return None, 0
    if len(parts) == 4 and parts[:2] == ["help", "cat"]:
        try:
            page_number = int(parts[3])
        except ValueError:
            return None
        if page_number < 0 or _find_category(parts[2]) is None:
            return None
        return parts[2], page_number
    return None
