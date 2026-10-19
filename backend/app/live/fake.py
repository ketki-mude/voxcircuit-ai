import asyncio
from collections import deque
from collections.abc import AsyncIterator, Sequence

from app.live.base import LiveSession, LiveSessionError
from app.live.events import LiveEvent, OutputTranscript, TurnComplete

ScriptStep = LiveEvent | LiveSessionError

DEMO_REPLIES = (
    "Hi, I'm Maya, an AI assistant for Northstar Insurance. I can set up a call with a "
    "licensed agent. This is a demo, so please use fake details only. What name should "
    "I put down?",
    "Thanks. What's the best email for the agent to reach you?",
    "Got it. Are you looking for a new policy, switching insurers, or adding a vehicle?",
    "And what's the vehicle make and year, and your ZIP code?",
    "Thank you. This offline test ends here, so nothing has been booked. Choose Gemini Live "
    "to talk with the real model.",
)


def scripted_reply(text: str) -> list[ScriptStep]:
    """One model turn: the reply streamed as word-sized transcript chunks, then turn complete."""
    words = text.split(" ")
    chunks = [f"{word} " for word in words[:-1]] + [words[-1]]
    steps: list[ScriptStep] = [OutputTranscript(text=chunk) for chunk in chunks]
    steps.append(TurnComplete())
    return steps


class FakeLiveSession(LiveSession):
    """Offline LiveSession that replays scripted events. No network, no API key.

    Each caller text turn plays the next scripted turn. With audio_turn_bytes set, every
    that many bytes of caller audio also play the next turn, standing in for the end of an
    utterance. A script step that is a LiveSessionError is raised from events(), which lets
    tests simulate provider failures.
    """

    def __init__(
        self,
        turns: Sequence[Sequence[ScriptStep]],
        *,
        step_delay_s: float = 0.0,
        connect_error: LiveSessionError | None = None,
        audio_turn_bytes: int | None = None,
    ) -> None:
        self._turns: deque[list[ScriptStep]] = deque(list(turn) for turn in turns)
        self._fallback = scripted_reply("That's the end of this scripted conversation.")
        self._step_delay_s = step_delay_s
        self._connect_error = connect_error
        self._audio_turn_bytes = audio_turn_bytes
        self._audio_since_turn = 0
        self._queue: asyncio.Queue[ScriptStep | None] = asyncio.Queue()
        self._connected = False
        self._closed = False
        self.sent_text: list[str] = []
        self.sent_audio_bytes = 0

    @classmethod
    def demo(cls) -> "FakeLiveSession":
        """The scripted intake used by the offline test engine in the UI."""
        return cls([scripted_reply(reply) for reply in DEMO_REPLIES], step_delay_s=0.04)

    async def connect(self) -> None:
        if self._connect_error is not None:
            raise self._connect_error
        if self._closed:
            raise LiveSessionError("session is closed")
        self._connected = True

    async def send_text(self, text: str) -> None:
        self._require_open()
        self.sent_text.append(text)
        self._play_next_turn()

    async def send_audio(self, pcm: bytes) -> None:
        self._require_open()
        self.sent_audio_bytes += len(pcm)
        if self._audio_turn_bytes is None:
            return
        self._audio_since_turn += len(pcm)
        if self._audio_since_turn >= self._audio_turn_bytes:
            self._audio_since_turn = 0
            self._play_next_turn()

    def _play_next_turn(self) -> None:
        turn = self._turns.popleft() if self._turns else self._fallback
        for step in turn:
            self._queue.put_nowait(step)

    async def events(self) -> AsyncIterator[LiveEvent]:
        while not self._closed:
            step = await self._queue.get()
            if step is None or self._closed:
                return
            if isinstance(step, LiveSessionError):
                raise step
            if self._step_delay_s:
                await asyncio.sleep(self._step_delay_s)
            yield step

    async def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._queue.put_nowait(None)

    def _require_open(self) -> None:
        if not self._connected or self._closed:
            raise LiveSessionError("session is not connected")
