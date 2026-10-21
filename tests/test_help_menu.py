from app.core.command_registry import COMMANDS
from app.core.help_menu import PAGE_SIZE, _example, build_help_page, parse_help_callback


def test_help_main_menu_has_only_registered_command_categories():
    page = build_help_page()
    registered_categories = {command.category for command in COMMANDS if command.enabled}
    button_categories = {
        button.callback_data.split(":")[2]
        for row in page.keyboard.inline_keyboard
        for button in row
        if button.callback_data and button.callback_data.startswith("help:cat:")
    }
    assert button_categories == {
        name.casefold().replace(" ", "_") for name in registered_categories
    }


def test_help_category_shows_syntax_examples_and_permissions():
    page = build_help_page("mute_and_unmute")
    assert "/mute" in page.text
    assert "Example:" in page.text
    assert "Permission: group admin" in page.text
    assert "Example: /mute 123456789 30m spam" in page.text
    assert any(button.callback_data == "help:home" for button in page.keyboard.inline_keyboard[0])


def test_help_callback_parser_rejects_forged_categories_and_negative_pages():
    assert parse_help_callback("help:cat:ban_and_unban:0") == ("ban_and_unban", 0)
    assert parse_help_callback("help:cat:not_real:0") is None
    assert parse_help_callback("help:cat:ban_and_unban:-1") is None
    assert parse_help_callback("help:home") == (None, 0)


def test_category_page_size_is_bounded():
    page = build_help_page("ban_and_unban", 999)
    assert page.text.startswith("Ban and Unban (1/1)")
    assert (
        len([command for command in COMMANDS if command.category == "Ban and Unban"]) <= PAGE_SIZE
    )


def test_large_category_has_next_and_previous_navigation():
    page = build_help_page("games", 0)
    first_callbacks = [
        button.callback_data for row in page.keyboard.inline_keyboard for button in row
    ]
    assert "help:cat:games:1" in first_callbacks
    second_page = build_help_page("games", 1)
    second_callbacks = [
        button.callback_data for row in second_page.keyboard.inline_keyboard for button in row
    ]
    assert "help:cat:games:0" in second_callbacks


def test_every_registered_command_has_a_concrete_example():
    for command in COMMANDS:
        if command.enabled:
            example = _example(command)
            assert "<" not in example
            assert ">" not in example
