"""Server-owned input provenance; body text never establishes authorship."""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Iterator


@dataclass(frozen=True)
class InputProvenance:
    source: str
    caller_label: str
    channel: str

    def __post_init__(self):
        if self.source not in {"owner_channel", "integration", "development", "scheduler", "unknown"}:
            raise ValueError("invalid input provenance")

    def projection(self) -> dict:
        # An authenticated transport proves the principal, never whether a
        # human typed the message. Do not add a model-derived human flag.
        return {"source": self.source, "caller_label": self.caller_label,
                "channel": self.channel, "human_authorship": "unverified"}


_CURRENT: ContextVar[InputProvenance | None] = ContextVar("input_provenance", default=None)


def current() -> InputProvenance | None:
    return _CURRENT.get()


@contextmanager
def bind(provenance: InputProvenance) -> Iterator[None]:
    parent = current()
    if parent is not None and parent.source == "development" and provenance.source != "development":
        raise PermissionError("development_context_cannot_be_promoted")
    token = _CURRENT.set(provenance)
    try:
        yield
    finally:
        _CURRENT.reset(token)


def assert_production_allowed() -> None:
    provenance = current()
    if provenance is not None and provenance.source == "development":
        raise PermissionError("development_context_has_no_production_access")
