"""Gmail's maintenance check runs in the existing scheduler gather."""

import logging

from core.mail import gmail, gmail_poll

logger = logging.getLogger(__name__)


async def check_gmail_poll(uid: str, char_id: str | None) -> None:
    if uid and char_id:
        try:
            await gmail_poll.tick(uid, char_id)
        except gmail.GmailError as exc:
            logger.warning("[gmail] poll could not persist state: %s", exc)
