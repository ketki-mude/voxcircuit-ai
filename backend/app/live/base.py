from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from types import TracebackType
from typing import Self

from app.live.events import LiveEvent


class LiveSessionError(Exception):
    """The live session failed. The message is for logs, not for callers."""


class LiveQuotaError(LiveSessionError):
    """The provider rejected the request for quota or rate-limit reasons (HTTP 429)."""


class LiveConfigError(LiveSessionError):
    """The session cannot work as configured (missing or invalid key, unknown model).

    caller_message, when set, is safe to show to the caller as is.
    """

    def __init__(self, message: str, *, caller_message: str | None = None) -> None:
        super().__init__(message)
        self.caller_message = caller_message


class LiveSession(ABC):
    """One real-time conversation with a speech-to-speech model.

    Usage:
        async with session:
            await session.send_text("hello")
            async for event in session.events():
                ...

    events() runs until close() is called or the session fails with a LiveSessionError.
    Implementations hide reconnects (go-away, session resumption) from the caller.
    """

    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def send_text(self, text: str) -> None:
        """Send one complete caller turn as text."""

    @abstractmethod
    async def send_audio(self, pcm: bytes) -> None:
        """Stream caller audio: 16-bit PCM, 16 kHz, mono."""

    @abstractmethod
    def events(self) -> AsyncIterator[LiveEvent]: ...

    @abstractmethod
    async def close(self) -> None: ...

    async def __aenter__(self) -> Self:
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()
