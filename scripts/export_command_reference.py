"""Generate a complete Markdown reference from the registered command catalogue."""

from pathlib import Path

from app.core.command_registry import COMMANDS
from app.core.help_menu import _example

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs" / "command-reference.md"


def main() -> None:
    commands = [command for command in COMMANDS if command.enabled]
    categories = sorted({command.category for command in commands})
    lines = [
        "# XYRON Command Reference",
        "",
        (
            f"Generated from the enabled command registry: {len(commands)} commands "
            f"in {len(categories)} categories."
        ),
        "",
        (
            "Access labels describe XYRON's command-level gate. Administrative operations "
            "additionally re-check the configured owner's current group membership and the "
            "bot's required Telegram rights."
        ),
        "",
    ]
    for category in categories:
        lines.extend(
            [
                f"## {category}",
                "",
                "| Command | Description | Example | Access |",
                "| --- | --- | --- | --- |",
            ]
        )
        for command in commands:
            if command.category != category:
                continue
            access = (
                "owner only"
                if command.owner_only
                else "group admin"
                if command.admin
                else "any member"
            )
            usage = command.usage.replace("|", "\\|")
            example = _example(command).replace("|", "\\|")
            description = command.description.replace("|", "\\|")
            lines.append(f"| `{usage}` | {description} | `{example}` | {access} |")
        lines.append("")
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text("\n".join(lines), encoding="utf-8")
    print(
        f"Wrote {OUTPUT.relative_to(ROOT)} with {len(commands)} commands "
        f"in {len(categories)} categories."
    )


if __name__ == "__main__":
    main()
