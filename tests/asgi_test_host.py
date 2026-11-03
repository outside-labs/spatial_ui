"""Local native ASGI browser host using the existing qualified test profile."""

import asyncio
from contextlib import contextmanager
from queue import Queue
import socket
import threading

from expose_api import serve
from spatial_ui import ASGIApp
from spatial_ui.forms import MAX_BODY


@contextmanager
def running_asgi(app):
    ready = Queue()
    failures, receipts = [], []
    # The test-only ASGI runner chooses its socket after lifespan startup.
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    def worker():
        async def run():
            adapter = ASGIApp(app, origin=f"http://127.0.0.1:{port}")
            class Host:
                # The test runner additionally requires its own body bound/state.
                max_body_size = MAX_BODY
                state = adapter.state
                async def __call__(self, scope, receive, send):
                    await adapter(scope, receive, send)
            stop = asyncio.Event()
            async with serve(Host(), port=port) as server:
                ready.put((asyncio.get_running_loop(), stop, server.sockets[0].getsockname()[1]))
                await stop.wait()
            receipts.append({"sessions": len(adapter._app._sessions), "requests": len(adapter._tickets),
                             "worker_closed": adapter._executor is None})
        try:
            asyncio.run(run())
        except BaseException as error:
            failures.append(error)
            ready.put(None)
    thread = threading.Thread(target=worker, name="asgi-browser-host")
    thread.start()
    state = None
    try:
        state = ready.get(timeout=15)
        if state is None:
            raise RuntimeError("ASGI test host failed to start") from failures[0]
        yield state[2]
    finally:
        if state:
            state[0].call_soon_threadsafe(state[1].set)
        thread.join(15)
        if thread.is_alive():
            raise RuntimeError("ASGI test host failed to stop")
        if failures:
            raise RuntimeError("ASGI test host failed") from failures[0]
        if receipts != [{"sessions": 0, "requests": 0, "worker_closed": True}]:
            raise AssertionError("ASGI browser host retained owned state")
