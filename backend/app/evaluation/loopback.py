"""An in-memory WebSocket, so evaluations drive the real CallHandler without a browser."""

import asyncio
import json
import time
from typing import Any

from starlette.types import Message as AsgiMessage
from starlette.websockets import WebSocketState


class ScenarioError(Exception):
    """The conversation did not go as the scenario needs (for example, no ticket appeared)."""


class LoopbackSocket:
    """The handler's side is the CallSocket protocol; the runner's side sends caller
    messages and reads what the handler sent, like a browser would."""

    def __init__(self) -> None:
        self.application_state = WebSocketState.CONNECTED
        self.frames: list[dict[str, Any]] = []
        self.closed = False
        self._incoming: asyncio.Queue[AsgiMessage] = asyncio.Queue()
        self._changed = asyncio.Event()
        self._last_activity = time.monotonic()

    # The handler's side.

    async def accept(self) -> None:
        return None

    async def receive(self) -> AsgiMessage:
        return await self._incoming.get()

    async def send_text(self, data: str) -> None:
        self.frames.append(json.loads(data))
        self.mark_activity()

    async def send_bytes(self, data: bytes) -> None:
        self.mark_activity()  # Audio is not needed by any check.

    async def close(self, code: int = 1000, reason: str | None = None) -> None:
        self.closed = True
        self.application_state = WebSocketState.DISCONNECTED
        self.mark_activity()

    # The caller's side.

    async def send(self, message: dict[str, Any]) -> None:
        self.mark_activity()
        await self._incoming.put({"type": "websocket.receive", "text": json.dumps(message)})

    def mark_activity(self) -> None:
        """Note that something happened (also used for actions that bypass the socket, such
        as an approval), so settle() waits for the call to react."""
        self._last_activity = time.monotonic()
        self._changed.set()

    def latest(self, message_type: str) -> dict[str, Any] | None:
        return next((f for f in reversed(self.frames) if f["type"] == message_type), None)

    async def wait_for(self, *types: str, start: int = 0, limit_s: float = 20) -> dict[str, Any]:
        """The first frame of one of these types at or after index `start`."""
        try:
            async with asyncio.timeout(limit_s):
                index = start
                while True:
                    while index < len(self.frames):
                        if self.frames[index]["type"] in types:
                            return self.frames[index]
                        index += 1
                    if self.closed:
                        raise ScenarioError(f"The call ended before a {'/'.join(types)} message")
                    self._changed.clear()
                    await self._changed.wait()
        except TimeoutError as exc:
            raise ScenarioError(f"No {'/'.join(types)} message within {limit_s:g} s") from exc

    async def settle(self, quiet: float, limit_s: float = 20) -> None:
        """Wait until nothing has happened for `quiet` seconds."""
        try:
            async with asyncio.timeout(limit_s):
                while True:
                    self._changed.clear()
                    remaining = quiet - (time.monotonic() - self._last_activity)
                    if remaining <= 0:
                        return
                    try:
                        async with asyncio.timeout(remaining):
                            await self._changed.wait()
                    except TimeoutError:
                        return  # Quiet for long enough.
        except TimeoutError as exc:
            raise ScenarioError(f"The call did not settle within {limit_s:g} s") from exc
