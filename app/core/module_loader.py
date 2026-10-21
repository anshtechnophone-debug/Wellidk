"""Small module registry; only explicitly implemented modules are exposed."""

from __future__ import annotations

from dataclasses import dataclass

from telegram.ext import Application


@dataclass(frozen=True, slots=True)
class Module:
    name: str
    description: str
    register: callable
    enabled: bool = True


def load_modules(application: Application, modules: tuple[Module, ...]) -> None:
    names: set[str] = set()
    for module in modules:
        if module.name in names:
            raise ValueError(f"Duplicate module name: {module.name}")
        names.add(module.name)
        if module.enabled:
            module.register(application)
