"""Registry for scheduler shadow proposal producers."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from importlib import import_module
import sys
from typing import Callable, Optional


ProposerFn = Callable[[dict], Optional[object]]


@dataclass(frozen=True)
class ProposerEntry:
    name: str
    fn: ProposerFn
    trigger_names: frozenset[str]


_REGISTRY: "OrderedDict[str, ProposerEntry]" = OrderedDict()
_BUILTINS_LOADED = False


def register_proposer(
    name: str,
    fn: ProposerFn,
    trigger_names: list[str] | tuple[str, ...] | set[str] | frozenset[str] | None = None,
) -> None:
    names = frozenset(str(x) for x in (trigger_names or (name,)))
    _REGISTRY[str(name)] = ProposerEntry(name=str(name), fn=fn, trigger_names=names)


def iter_proposers() -> list[ProposerEntry]:
    _ensure_builtins_loaded()
    return list(_REGISTRY.values())


def registered_trigger_names() -> frozenset[str]:
    _ensure_builtins_loaded()
    names: set[str] = set()
    for entry in _REGISTRY.values():
        names.update(entry.trigger_names)
    return frozenset(names)


def _ensure_builtins_loaded() -> None:
    global _BUILTINS_LOADED
    if _BUILTINS_LOADED:
        return
    _BUILTINS_LOADED = True
    for module_name in (
        "core.scheduler.triggers.watch",
        "core.scheduler.triggers.birthday",
        "core.scheduler.triggers.period",
        "core.scheduler.triggers.time_based",
        "core.scheduler.triggers.diary",
        "core.scheduler.triggers.timenode",
        "core.scheduler.triggers.festival",
        "core.scheduler.triggers.reminders",
        "core.scheduler.triggers.memory",
        "core.scheduler.triggers.garden_water",
        "core.scheduler.triggers.garden_daily",
        "core.scheduler.triggers.overflow",
        "core.scheduler.triggers.presence_nag",
        "core.scheduler.triggers.dream_exit",
        "core.scheduler.triggers.letter_writer",
        "core.coplay.commentator",
    ):
        module = import_module(module_name)
        if module_name in sys.modules and hasattr(module, "_register_proposers"):
            module._register_proposers()


def _reset_for_tests() -> None:
    global _BUILTINS_LOADED
    _REGISTRY.clear()
    _BUILTINS_LOADED = False
