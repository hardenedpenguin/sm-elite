"""One AMI session per Asterisk host.

Commands are serialized on the connection. The caller owns reconnects.
Login turns events off; status is polled, which matches how app_rpt exposes
XStat and SawStat today.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any


class AmiError(Exception):
    """The manager connection failed or rejected a command."""


class AmiClient:
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self.reader = reader
        self.writer = writer
        self._buffer = b""
        self.alive = True

    @classmethod
    async def connect(cls, host: str, port: int, user: str, password: str, timeout: float = 8.0) -> AmiClient:
        try:
            reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
        except (OSError, asyncio.TimeoutError) as exc:
            raise AmiError(f"AMI connect failed for {host}:{port}") from exc
        client = cls(reader, writer)
        try:
            banner = await client._read_until(b"\n", timeout)
        except (OSError, asyncio.TimeoutError, AmiError) as exc:
            await client.close()
            raise AmiError("AMI banner was not received") from exc
        if not banner.startswith("Asterisk Call Manager"):
            await client.close()
            raise AmiError("the remote port is not Asterisk Manager")
        response = await client.request([
            "ACTION: Login",
            f"Username: {user}",
            f"Secret: {password}",
            "Events: off",
        ])
        if "response: success" not in response.lower():
            await client.close()
            raise AmiError("AMI login was rejected")
        return client

    async def request(self, lines: list[str], timeout: float = 8.0) -> str:
        if not self.alive:
            raise AmiError("AMI connection is closed")
        action_id = str(uuid.uuid4())
        payload = "\r\n".join([*lines, f"ActionID: {action_id}", "", ""])
        self.writer.write(payload.encode())
        try:
            await asyncio.wait_for(self.writer.drain(), timeout)
            raw = await self._read_message(timeout)
        except (OSError, asyncio.TimeoutError, ConnectionError) as exc:
            self.alive = False
            raise AmiError("AMI connection dropped") from exc
        # Classic manager "Response: Follows" keeps the command body in the
        # next packet. Pull it in when this packet has no Conn/Output lines.
        lowered = raw.lower()
        if "response: follows" in lowered and "conn:" not in lowered and "output:" not in lowered:
            try:
                raw = raw + "\r\n" + await self._read_message(timeout)
            except (OSError, asyncio.TimeoutError, ConnectionError) as exc:
                self.alive = False
                raise AmiError("AMI connection dropped") from exc
        return raw

    async def command(self, command: str, timeout: float = 8.0) -> str:
        return await self.request(["ACTION: Command", f"Command: {command}"], timeout)

    async def rpt_status(self, node: str, which: str) -> str:
        return await self.request([
            "ACTION: RptStatus",
            f"COMMAND: {which}",
            f"NODE: {node}",
        ])

    async def close(self) -> None:
        self.alive = False
        try:
            self.writer.write(b"ACTION: Logoff\r\n\r\n")
            await self.writer.drain()
        except (OSError, ConnectionError):
            pass
        self.writer.close()
        try:
            await self.writer.wait_closed()
        except (OSError, ConnectionError):
            pass

    async def _read_until(self, marker: bytes, timeout: float) -> str:
        while marker not in self._buffer:
            chunk = await asyncio.wait_for(self.reader.read(4096), timeout)
            if not chunk:
                raise AmiError("AMI closed the connection")
            self._buffer += chunk
            if len(self._buffer) > 1_000_000:
                raise AmiError("AMI response is too large")
        index = self._buffer.index(marker) + len(marker)
        raw = self._buffer[:index]
        self._buffer = self._buffer[index:]
        return raw.decode("utf-8", "replace")

    async def _read_message(self, timeout: float) -> str:
        while b"\r\n\r\n" not in self._buffer and b"\n\n" not in self._buffer:
            chunk = await asyncio.wait_for(self.reader.read(4096), timeout)
            if not chunk:
                raise AmiError("AMI closed the connection")
            self._buffer += chunk
            if len(self._buffer) > 1_000_000:
                raise AmiError("AMI response is too large")
        if b"\r\n\r\n" in self._buffer:
            marker = b"\r\n\r\n"
        else:
            marker = b"\n\n"
        index = self._buffer.index(marker) + len(marker)
        raw = self._buffer[:index]
        self._buffer = self._buffer[index:]
        return raw.decode("utf-8", "replace")


class AmiPool:
    """Reuse one logged-in session per host. A lock keeps commands in order."""

    def __init__(self):
        self._clients: dict[tuple, AmiClient] = {}
        self._locks: dict[tuple, asyncio.Lock] = {}

    def _lock(self, key: tuple) -> asyncio.Lock:
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock

    async def call(self, host: dict[str, Any], builder) -> str:
        key = (host["host"], int(host["port"]), host["user"])
        async with self._lock(key):
            client = self._clients.get(key)
            if client is None or not client.alive:
                client = await AmiClient.connect(host["host"], int(host["port"]), host["user"], host["password"])
                self._clients[key] = client
            try:
                return await builder(client)
            except AmiError:
                self._clients.pop(key, None)
                await client.close()
                raise

    async def close(self) -> None:
        clients = list(self._clients.values())
        self._clients.clear()
        for client in clients:
            await client.close()
