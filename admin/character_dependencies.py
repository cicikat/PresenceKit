"""HTTP dependencies bound to narrow character-session capabilities at startup.

Standalone Admin has no running character session, just as before: read views
report unavailable and authored edits do not attempt to reload a running card.
No runtime instance, mutable binding or alternative registry lives here.
"""


def _unavailable_snapshot():
    return None


def _unavailable_reloader(char_id: str, *, clear_note: bool = False):
    return None


def require_character_reader():
    return _unavailable_snapshot


def require_character_reloader():
    return _unavailable_reloader
