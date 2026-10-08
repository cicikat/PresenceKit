"""Lifecycle-scoped workaround for CPython IOCP AcceptEx WinError 64.

Retain Proactor subprocess support. Only a disconnected incoming connection
is retried; the server's listening socket must remain owned by asyncio.
"""
from __future__ import annotations

import asyncio
import logging
import socket
import struct
import sys
import time

logger = logging.getLogger(__name__)


def install(loop):
    """Return an undo callback; non-Windows/non-Proactor loops need no patch."""
    if sys.platform != "win32" or not isinstance(loop, asyncio.ProactorEventLoop):
        return lambda: None
    import _overlapped
    from asyncio.windows_events import NULL

    proactor = loop._proactor
    original = proactor.accept

    async def accept_connection(listener):
        proactor._register_with_iocp(listener)
        failures = 0
        last_warning = 0.0
        while True:
            conn = proactor._get_accept_socket(listener.family)
            try:
                ov = _overlapped.Overlapped(NULL)
                ov.AcceptEx(listener.fileno(), conn.fileno())

                def finish_accept(transferred, key, operation):
                    operation.getresult()
                    conn.setsockopt(socket.SOL_SOCKET, _overlapped.SO_UPDATE_ACCEPT_CONTEXT,
                                    struct.pack('@P', listener.fileno()))
                    conn.settimeout(listener.gettimeout())
                    return conn, conn.getpeername()

                accepted = await proactor._register(ov, listener, finish_accept)
            except BaseException as exc:
                conn.close()
                if not isinstance(exc, OSError) or exc.winerror != 64 or listener.fileno() == -1:
                    raise
                failures += 1
                now = time.monotonic()
                if failures == 1 or now - last_warning >= 60:
                    logger.warning("[http_accept] WinError64 on incoming connection; preserving listener and retrying (attempt=%s)", failures)
                    last_warning = now
                await asyncio.sleep(min(0.1 * (2 ** min(failures - 1, 4)), 1.0))
            else:
                if failures:
                    logger.info("[http_accept] listener recovered after %s retries", failures)
                return accepted

    def accept(listener):
        return loop.create_task(accept_connection(listener))

    proactor.accept = accept

    def undo():
        if proactor.accept is accept:
            proactor.accept = original

    return undo
