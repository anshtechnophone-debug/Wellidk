from types import SimpleNamespace

import pytest

from app.core.module_loader import Module, load_modules


def test_disabled_module_is_not_registered():
    registrations = []
    application = object()
    load_modules(
        application,
        (Module("disabled", "disabled test module", registrations.append, enabled=False),),
    )
    assert registrations == []


def test_duplicate_module_names_are_rejected():
    application = object()
    module = Module("duplicate", "test module", lambda app: None)
    with pytest.raises(ValueError, match="Duplicate module"):
        load_modules(application, (module, module))


def test_enabled_module_registers_once():
    registrations = []
    application = SimpleNamespace()
    load_modules(
        application,
        (Module("enabled", "test module", lambda app: registrations.append(app)),),
    )
    assert registrations == [application]
