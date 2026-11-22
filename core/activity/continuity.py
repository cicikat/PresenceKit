"""Refresh domain projections after an activity dialogue has been recorded."""


async def refresh_projection(uid, char_id, kind, sid):
    """Extractive segments, never invented emotions; originals remain authoritative."""
    from core.activity import activity_summary
    await activity_summary.generate_and_reflow(uid, char_id, kind, sid)
