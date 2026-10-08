import asyncio
import socket
import sys

import pytest

from core.windows_accept_recovery import install


pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows IOCP regression")


def winerror(number):
    return OSError(22, "injected connection error", None, number)


def test_real_listener_survives_winerror64_and_serves_next_connection():
    loop = asyncio.ProactorEventLoop()
    asyncio.set_event_loop(loop)
    errors = []
    loop.set_exception_handler(lambda _, context: errors.append(context))
    original_register = loop._proactor._register
    injected = False

    def register(operation, obj, callback=None):
        nonlocal injected
        if callback and callback.__name__ == "finish_accept" and not injected:
            injected = True

            def fail_once(transferred, key, ov):
                result = callback(transferred, key, ov)
                result[0].close()
                raise winerror(64)

            return original_register(operation, obj, fail_once)
        return original_register(operation, obj, callback)

    loop._proactor._register = register
    undo = install(loop)

    async def run():
        received = asyncio.Event()

        class Protocol(asyncio.Protocol):
            def connection_made(self, transport):
                transport.write(b"recovered")
                transport.close()
                received.set()

        server = await loop.create_server(Protocol, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        assert await asyncio.wait_for(reader.read(), 3) == b""
        writer.close()
        await writer.wait_closed()
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        assert await asyncio.wait_for(reader.read(), 3) == b"recovered"
        await asyncio.wait_for(received.wait(), 3)
        assert server.is_serving() and server.sockets[0].fileno() != -1
        writer.close()
        await writer.wait_closed()
        server.close()
        await server.wait_closed()
        await asyncio.sleep(0.05)

    try:
        loop.run_until_complete(run())
        assert injected and not errors
    finally:
        undo()
        loop.close()
        asyncio.set_event_loop(None)


@pytest.mark.parametrize("failure", [5, 64, None])
def test_nonretry_error_or_cancel_closes_incoming_socket(failure):
    loop = asyncio.ProactorEventLoop()
    asyncio.set_event_loop(loop)
    original_accept = loop._proactor.accept
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    listener.setblocking(False)
    connections = []
    original_get = loop._proactor._get_accept_socket

    def get_socket(family):
        conn = original_get(family)
        connections.append(conn)
        return conn

    loop._proactor._get_accept_socket = get_socket
    undo = install(loop)

    async def run():
        task = loop._proactor.accept(listener)
        await asyncio.sleep(0.01)
        if failure is None:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            # Complete the real pending IO operation with an injected OS error.
            waiter = task._fut_waiter
            if failure == 64:
                listener.close()  # shutdown errors must propagate, never retry
            waiter.set_exception(winerror(failure))
            with pytest.raises(OSError):
                await task
        assert connections and all(conn.fileno() == -1 for conn in connections)

    try:
        loop.run_until_complete(run())
    finally:
        listener.close()
        undo()
        assert loop._proactor.accept == original_accept
        loop.close()
        asyncio.set_event_loop(None)


def test_proactor_subprocess_support_is_retained():
    loop = asyncio.ProactorEventLoop()
    asyncio.set_event_loop(loop)
    undo = install(loop)

    async def run():
        process = await asyncio.create_subprocess_exec(sys.executable, "-c", "print('child-ok')",
                                                       stdout=asyncio.subprocess.PIPE)
        stdout, _ = await asyncio.wait_for(process.communicate(), 5)
        assert process.returncode == 0 and b"child-ok" in stdout

    try:
        loop.run_until_complete(run())
    finally:
        undo()
        loop.close()
        asyncio.set_event_loop(None)
