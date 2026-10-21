from telegram import ChatPermissions

from app.core.permissions import bounded_permissions


def test_restore_permissions_never_exceeds_current_group_defaults():
    saved = {name: True for name in ChatPermissions.__slots__}
    defaults = {name: True for name in ChatPermissions.__slots__}
    defaults["can_send_photos"] = False

    restored = bounded_permissions(saved, defaults)

    assert restored.can_send_messages is True
    assert restored.can_send_photos is False


def test_missing_baseline_fails_closed():
    defaults = {name: True for name in ChatPermissions.__slots__}
    restored = bounded_permissions(None, defaults)
    assert not any(getattr(restored, name, False) for name in ChatPermissions.__slots__)
