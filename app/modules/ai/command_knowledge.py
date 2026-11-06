from app.core.command_registry import COMMANDS


def command_catalog() -> str:
    lines = []
    for item in COMMANDS:
        if item.enabled:
            access = "owner only" if item.owner_only else "group admin" if item.admin else "available"
            lines.append(f"/{item.name} — {item.description}; usage: {item.usage}; access: {access}")
    return "\n".join(lines)
