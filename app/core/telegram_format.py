"""Safe Telegram HTML formatting helpers."""

import html


def user_mention(user_id: int, display_name: str | None) -> str:
    """Render a person's display name as a clickable Telegram mention."""
    label = html.escape((display_name or "Telegram user").strip() or "Telegram user")
    return f'<a href="tg://user?id={int(user_id)}">{label}</a>'
