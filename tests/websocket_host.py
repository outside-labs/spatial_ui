"""Bounded loopback HTTP/WebSocket wire host, exclusively for browser tests.

The application still owns origin, identity and resource policies. This runner
does not select a production host, TLS implementation or deployment profile.
"""

import asyncio
import base64
import hashlib
import struct
from contextlib import asynccontextmanager
from http import HTTPStatus


@asynccontextmanager
async def lifespan(app):
    incoming, outgoing = asyncio.Queue(), asyncio.Queue()
    task = asyncio.create_task(app({"type": "lifespan"}, incoming.get, outgoing.put))
    await incoming.put({"type": "lifespan.startup"})
    if (await asyncio.wait_for(outgoing.get(), 5))["type"] != "lifespan.startup.complete":
        raise RuntimeError("Test application startup failed")
    try:
        yield
    finally:
        await incoming.put({"type": "lifespan.shutdown"})
        response = await asyncio.wait_for(outgoing.get(), 5)
        await asyncio.wait_for(task, 5)
        if response["type"] != "lifespan.shutdown.complete":
            raise RuntimeError("Test application shutdown failed")


class WireHost:
    def __init__(self):
        self.tasks, self.sockets, self.messages = set(), set(), []
        self.failures = []
        self.app = None

    async def frame(self, writer, opcode, data):
        if len(data) > 65536:
            raise ValueError("Test frame exceeds its bound")
        header = bytes([128 | opcode])
        header += bytes([len(data)]) if len(data) < 126 else b"\x7e" + struct.pack("!H", len(data)) if len(data) < 65536 else b"\x7f" + struct.pack("!Q", len(data))
        writer.write(header + data)
        await asyncio.wait_for(writer.drain(), 2)

    async def websocket(self, scope, reader, writer):
        connected, accepted, closed = False, False, False
        headers = dict(scope["headers"])
        if headers.get(b"sec-websocket-version") != b"13":
            raise ValueError("Unsupported test WebSocket version")
        key = headers[b"sec-websocket-key"]
        if len(base64.b64decode(key, validate=True)) != 16:
            raise ValueError("Invalid test WebSocket key")
        scope.update(type="websocket", scheme="ws", subprotocols=[part.strip() for part in
            headers.get(b"sec-websocket-protocol", b"").decode("ascii").split(",") if part.strip()])
        self.sockets.add(writer)

        async def receive():
            nonlocal connected
            if not connected:
                connected = True
                return {"type": "websocket.connect"}
            try:
                first, second = await reader.readexactly(2)
                if first & 112 or not first & 128 or not second & 128:
                    raise ValueError("Test host requires complete masked frames")
                opcode, size = first & 15, second & 127
                if size == 126:
                    size = struct.unpack("!H", await reader.readexactly(2))[0]
                elif size == 127:
                    size = struct.unpack("!Q", await reader.readexactly(8))[0]
                if size > 65536:
                    raise ValueError("Test receive exceeds its bound")
                mask = await reader.readexactly(4)
                data = bytes(value ^ mask[index % 4] for index, value in enumerate(await reader.readexactly(size)))
                if opcode == 8:
                    return {"type": "websocket.disconnect", "code": 1000}
                if opcode not in (1, 2):
                    raise ValueError("Unsupported test frame opcode")
                return {"type": "websocket.receive", "text": data.decode("utf-8")} if opcode == 1 else {"type": "websocket.receive", "bytes": data}
            except (asyncio.IncompleteReadError, ConnectionError):
                return {"type": "websocket.disconnect", "code": 1006}

        async def send(event):
            nonlocal accepted, closed
            if closed:
                raise OSError("Test socket already closed")
            kind = event["type"]
            if kind == "websocket.accept" and not accepted:
                token = base64.b64encode(hashlib.sha1(key + b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11").digest())
                writer.write(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: "
                    + token + b"\r\nSec-WebSocket-Protocol: " + event["subprotocol"].encode("ascii") + b"\r\n\r\n")
                await asyncio.wait_for(writer.drain(), 2)
                accepted = True
            elif kind == "websocket.send" and accepted:
                text = event["text"]
                self.messages.append(text)
                if len(self.messages) > 256:
                    self.messages.pop(0)
                await self.frame(writer, 1, text.encode("utf-8"))
            elif kind == "websocket.close":
                closed = True
                if accepted:
                    await self.frame(writer, 8, struct.pack("!H", event.get("code", 1000)))
                else:
                    writer.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
                    await writer.drain()
            else:
                raise ValueError("Unexpected test socket event")
        try:
            await self.app(scope, receive, send)
        finally:
            self.sockets.discard(writer)

    async def connection(self, reader, writer):
        task = asyncio.current_task()
        self.tasks.add(task)
        try:
            raw = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 3)
            if len(raw) > 32768:
                raise ValueError("Test request headers exceed their bound")
            lines = raw[:-4].split(b"\r\n")
            method, path, version = lines[0].decode("ascii").split(" ")
            headers = [tuple(part.strip() for part in line.split(b":", 1)) for line in lines[1:]]
            headers = [(name.lower(), value) for name, value in headers]
            scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.5"}, "http_version": "1.1",
                "method": method, "path": path.split("?", 1)[0], "raw_path": path.split("?", 1)[0].encode(),
                "query_string": path.partition("?")[2].encode(), "root_path": "", "headers": headers,
                "scheme": "http", "server": writer.get_extra_info("sockname")[:2],
                "client": writer.get_extra_info("peername")[:2], "state": {}}
            if dict(headers).get(b"upgrade", b"").lower() == b"websocket":
                async with asyncio.timeout(30):
                    await self.websocket(scope, reader, writer)
                return
            size = int(dict(headers).get(b"content-length", b"0"))
            if not 0 <= size <= 65536:
                raise ValueError("Test request body exceeds its bound")
            body = await asyncio.wait_for(reader.readexactly(size), 3)
            consumed = False
            async def receive():
                nonlocal consumed
                if not consumed:
                    consumed = True
                    return {"type": "http.request", "body": body}
                await reader.read(1)
                return {"type": "http.disconnect"}
            async def send(event):
                if event["type"] == "http.response.start":
                    status = event["status"]
                    writer.write(f"HTTP/1.1 {status} {HTTPStatus(status).phrase}\r\n".encode()
                        + b"".join(k + b": " + v + b"\r\n" for k, v in event["headers"])
                        + b"Connection: close\r\n\r\n")
                elif event["type"] == "http.response.body":
                    writer.write(event.get("body", b""))
                else:
                    raise ValueError("Unexpected test HTTP event")
                await asyncio.wait_for(writer.drain(), 2)
            await asyncio.wait_for(self.app(scope, receive, send), 10)
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        except Exception as error:
            self.failures.append(type(error).__name__)
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionError:
                pass
            self.tasks.discard(task)

    def drop(self):
        for writer in tuple(self.sockets):
            writer.close()

    @asynccontextmanager
    async def listening(self):
        server = await asyncio.start_server(self.connection, "127.0.0.1", 0, limit=32769)
        self.port = server.sockets[0].getsockname()[1]
        self.origin = f"http://127.0.0.1:{self.port}"
        try:
            yield self
        finally:
            server.close()
            await server.wait_closed()
            self.drop()
            if self.tasks:
                await asyncio.wait_for(asyncio.gather(*tuple(self.tasks)), 5)
            if self.sockets or self.tasks or self.failures:
                raise AssertionError(f"Test wire host cleanup failed: {self.failures}")
