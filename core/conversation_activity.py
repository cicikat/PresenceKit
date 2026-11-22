"""Process-local owner chat activity shared by scheduler and autonomy."""

import time

_last_user_message_time = 0.0


def mark_user_active(*, now: float | None = None) -> None:
    global _last_user_message_time
    _last_user_message_time = time.time() if now is None else now


def last_user_message_time() -> float:
    return _last_user_message_time


def user_active_recently(window_seconds: int = 120, *, now: float | None = None) -> bool:
    current = time.time() if now is None else now
    return current - last_user_message_time() < window_seconds
