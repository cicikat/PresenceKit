"""Narrow gate for preventing dream facts from consolidating as reality."""
from __future__ import annotations

import time

ECHO_WINDOW_SECONDS = 8 * 3600
DREAM_KEYWORDS = ("梦", "梦里", "梦见", "梦到", "做了个梦", "昨晚的梦")

def should_dream_echo(*, last_exited_at: float | None, user_content: str, reply: str, now: float | None = None) -> bool:
    """Silence the first 8h after exit, then only a turn explicitly about dreams.

    This is defense in depth; impression-store physical isolation remains the
    load-bearing boundary. A dream reference avoiding this keyword set is the
    accepted F1 leakage edge, already framed as non-reality by 6g.
    """
    now = time.time() if now is None else now
    if last_exited_at and now - float(last_exited_at) <= ECHO_WINDOW_SECONDS:
        return True
    return any(keyword in f"{user_content}\n{reply}" for keyword in DREAM_KEYWORDS)
