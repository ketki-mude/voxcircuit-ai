"""GeminiLiveSession against a fake SDK client: no network, no API key."""

import asyncio
from collections import deque
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any, cast

import pytest
from google import genai
from google.genai import errors, types

from app.live.base import LiveConfigError, LiveQuotaError, LiveSessionError
from app.live.events import (
    AudioChunk,
    GoAway,
    InputTranscript,
    Interrupted,
    LiveEvent,
    OutputTranscript,
    SessionResumed,
    TurnComplete,
    Usage,
)
from app.live.gemini import GeminiLiveSession, classify_error, parse_duration, translate_message

Item = types.LiveServerMessage | Exception


def resumption(handle: str) -> types.LiveServerMessage:
    return types.LiveServerMessage(
        session_resumption_update=types.LiveServerSessionResumptionUpdate(
            new_handle=handle, resumable=True
        )
    )


def go_away(time_left: str = "5s") -> types.LiveServerMessage:
    return types.LiveServerMessage(go_away=types.LiveServerGoAway(time_left=time_left))


def said(text: str, *, turn_complete: bool = False) -> types.LiveServerMessage:
    return types.LiveServerMessage(
        server_content=types.LiveServerContent(
            output_transcription=types.Transcription(text=text), turn_complete=turn_complete
        )
    )


def closed(code: int = 1006, reason: str = "abnormal closure") -> errors.APIError:
    return errors.APIError(code, {"message": reason})


class FakeSdkSession:
    """Mimics google.genai.live.AsyncSession: receive() ends after each complete turn."""

    def __init__(self, items: list[Item]) -> None:
        self._items = deque(items)
        self._closed = asyncio.Event()
        self.sent: list[Any] = []

    def close(self) -> None:
        self._closed.set()

    async def receive(self) -> AsyncIterator[types.LiveServerMessage]:
        while self._items:
            item = self._items.popleft()
            if isinstance(item, Exception):
                raise item
            yield item
            if item.server_content and item.server_content.turn_complete:
                return
        # A quiet, open connection: wait until it is closed, then fail like the SDK does.
        await self._closed.wait()
        raise closed(1000, "connection closed")

    async def send_client_content(self, *, turns: types.Content, turn_complete: bool) -> None:
        if self._closed.is_set():
            raise closed(1000, "connection closed")
        self.sent.append(("client_content", turns, turn_complete))

    async def send_realtime_input(self, *, audio: types.Blob) -> None:
        if self._closed.is_set():
            raise closed(1000, "connection closed")
        self.sent.append(("realtime_input", audio))


class FakeLive:
    def __init__(self, sessions: list[FakeSdkSession]) -> None:
        self._sessions = deque(sessions)
        self.configs: list[types.LiveConnectConfig] = []

    @asynccontextmanager
    async def connect(
        self, *, model: str, config: types.LiveConnectConfig
    ) -> AsyncIterator[FakeSdkSession]:
        self.configs.append(config)
        session = self._sessions.popleft()
        try:
            yield session
        finally:
            session.close()


def make_session(*sdk_sessions: FakeSdkSession) -> tuple[GeminiLiveSession, FakeLive]:
    live = FakeLive(list(sdk_sessions))
    client = cast(genai.Client, SimpleNamespace(aio=SimpleNamespace(live=live)))
    session = GeminiLiveSession(
        client=client, model="test-model", system_instruction="Be Maya.", silence_duration_ms=650
    )
    return session, live


async def collect_until(
    session: GeminiLiveSession, done: Callable[[LiveEvent], bool]
) -> list[LiveEvent]:
    collected: list[LiveEvent] = []
    async with asyncio.timeout(2):
        async for event in session.events():
            collected.append(event)
            if done(event):
                break
    return collected


def is_turn_complete(event: LiveEvent) -> bool:
    return isinstance(event, TurnComplete)


def test_config_enables_transcripts_vad_compression_and_resumption() -> None:
    session, _ = make_session()
    config = session.build_config()

    assert config.response_modalities == [types.Modality.AUDIO]
    assert config.system_instruction == "Be Maya."
    assert config.input_audio_transcription is not None
    assert config.output_audio_transcription is not None
    assert config.realtime_input_config is not None
    vad = config.realtime_input_config.automatic_activity_detection
    assert vad is not None
    assert (vad.disabled, vad.silence_duration_ms) == (False, 650)
    assert config.context_window_compression is not None
    assert config.context_window_compression.sliding_window is not None
    assert config.session_resumption is not None
    assert config.session_resumption.handle is None


def test_translate_message_maps_every_field() -> None:
    message = types.LiveServerMessage(
        server_content=types.LiveServerContent(
            input_transcription=types.Transcription(text="hi maya"),
            model_turn=types.Content(
                parts=[
                    types.Part(text="thinking...", thought=True),
                    types.Part(
                        inline_data=types.Blob(data=b"\x00" * 480, mime_type="audio/pcm;rate=24000")
                    ),
                ]
            ),
            output_transcription=types.Transcription(text="Hello"),
            interrupted=True,
            turn_complete=True,
        ),
        usage_metadata=types.UsageMetadata(
            prompt_token_count=1, response_token_count=2, total_token_count=3
        ),
        go_away=types.LiveServerGoAway(time_left="10s"),
    )

    assert translate_message(message) == [
        InputTranscript(text="hi maya"),
        AudioChunk(data=b"\x00" * 480),
        OutputTranscript(text="Hello"),
        Interrupted(),
        TurnComplete(),
        Usage(prompt_tokens=1, response_tokens=2, total_tokens=3),
        GoAway(time_left_seconds=10.0),
    ]


def test_translate_message_keeps_the_finished_flag() -> None:
    message = types.LiveServerMessage(
        server_content=types.LiveServerContent(
            input_transcription=types.Transcription(text="", finished=True),
            output_transcription=types.Transcription(text="Bye.", finished=True),
        )
    )
    assert translate_message(message) == [
        InputTranscript(text="", finished=True),
        OutputTranscript(text="Bye.", finished=True),
    ]


def test_parse_duration() -> None:
    assert parse_duration("10s") == 10.0
    assert parse_duration("1.5s") == 1.5
    assert parse_duration(None) is None
    assert parse_duration("soon") is None


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (errors.APIError(429, {"message": "Too many requests"}), LiveQuotaError),
        (closed(1011, "You exceeded your current quota"), LiveQuotaError),
        (closed(1007, "API key not valid. Please pass a valid API key."), LiveConfigError),
        (closed(1008, "models/nope is not found"), LiveConfigError),
        (closed(1006), LiveSessionError),
        (TimeoutError(), LiveSessionError),
    ],
)
def test_classify_error(exc: Exception, expected: type[LiveSessionError]) -> None:
    assert type(classify_error(exc)) is expected


async def test_send_text_sends_an_ordered_user_turn() -> None:
    sdk = FakeSdkSession([])
    session, _ = make_session(sdk)
    async with session:
        await session.send_text("hello")
        await session.send_audio(b"\x00\x00")

    kind, turns, turn_complete = sdk.sent[0]
    assert (kind, turn_complete) == ("client_content", True)
    assert turns.role == "user"
    assert turns.parts[0].text == "hello"
    assert sdk.sent[1][1].mime_type == "audio/pcm;rate=16000"


async def test_go_away_while_idle_reconnects_with_latest_handle() -> None:
    first = FakeSdkSession(
        [resumption("h1"), said("Hi.", turn_complete=True), resumption("h2"), go_away()]
    )
    second = FakeSdkSession([said("Welcome back.", turn_complete=True)])
    session, live = make_session(first, second)

    async with session:
        events = await collect_until(session, is_turn_complete)
        events += await collect_until(session, is_turn_complete)

    assert [type(e) for e in events] == [
        OutputTranscript,
        TurnComplete,
        GoAway,
        SessionResumed,
        OutputTranscript,
        TurnComplete,
    ]
    assert live.configs[0].session_resumption is not None
    assert live.configs[0].session_resumption.handle is None
    assert live.configs[1].session_resumption is not None
    assert live.configs[1].session_resumption.handle == "h2"


async def test_go_away_mid_turn_reconnects_after_the_turn() -> None:
    first = FakeSdkSession(
        [resumption("h1"), said("Hel"), go_away(), said("lo.", turn_complete=True)]
    )
    second = FakeSdkSession([])
    session, live = make_session(first, second)

    async with session:
        events = await collect_until(session, lambda e: isinstance(e, SessionResumed))

    assert [type(e) for e in events] == [
        OutputTranscript,
        GoAway,
        OutputTranscript,
        TurnComplete,
        SessionResumed,
    ]
    assert len(live.configs) == 2


async def test_dropped_connection_resumes_when_a_handle_exists() -> None:
    first = FakeSdkSession([resumption("h1"), closed(1006)])
    second = FakeSdkSession([said("Still here.", turn_complete=True)])
    session, live = make_session(first, second)

    async with session:
        events = await collect_until(session, is_turn_complete)

    assert [type(e) for e in events] == [SessionResumed, OutputTranscript, TurnComplete]
    assert live.configs[1].session_resumption is not None
    assert live.configs[1].session_resumption.handle == "h1"


async def test_dropped_connection_without_handle_fails() -> None:
    session, _ = make_session(FakeSdkSession([closed(1006)]))
    async with session:
        with pytest.raises(LiveSessionError):
            await collect_until(session, is_turn_complete)


async def test_quota_error_is_not_retried() -> None:
    first = FakeSdkSession([resumption("h1"), closed(1011, "Quota exceeded")])
    session, live = make_session(first, FakeSdkSession([]))
    async with session:
        with pytest.raises(LiveQuotaError):
            await collect_until(session, is_turn_complete)
    assert len(live.configs) == 1


async def test_close_ends_the_event_stream() -> None:
    session, _ = make_session(FakeSdkSession([]))
    await session.connect()

    async def drain() -> list[LiveEvent]:
        return [event async for event in session.events()]

    task = asyncio.create_task(drain())
    await asyncio.sleep(0.01)
    await session.close()
    assert await asyncio.wait_for(task, timeout=1) == []


async def test_text_sent_during_a_dropped_connection_is_retried_after_resuming() -> None:
    first = FakeSdkSession([resumption("h1")])
    second = FakeSdkSession([])
    session, live = make_session(first, second)

    async with session:
        resumed = asyncio.create_task(
            collect_until(session, lambda e: isinstance(e, SessionResumed))
        )
        await asyncio.sleep(0.01)  # events() reads the resumption handle.
        first.close()  # The server drops the connection.
        await session.send_text("hello")  # Fails on the dropped socket, then retries.
        await resumed

    assert first.sent == []
    kind, turns, _ = second.sent[0]
    assert (kind, turns.parts[0].text) == ("client_content", "hello")
    assert live.configs[1].session_resumption is not None
    assert live.configs[1].session_resumption.handle == "h1"


async def test_audio_is_dropped_while_reconnecting() -> None:
    first = FakeSdkSession([resumption("h1")])
    second = FakeSdkSession([])
    session, _ = make_session(first, second)

    async with session:
        resumed = asyncio.create_task(
            collect_until(session, lambda e: isinstance(e, SessionResumed))
        )
        await asyncio.sleep(0.01)
        first.close()
        await session.send_audio(b"\x00\x00")  # Dropped, not queued, and no error.
        await resumed
        await session.send_audio(b"\x01\x00")

    assert first.sent == []
    assert [item[1].data for item in second.sent] == [b"\x01\x00"]


async def test_audio_send_without_a_handle_fails() -> None:
    sdk = FakeSdkSession([])
    session, _ = make_session(sdk)
    async with session:
        sdk.close()
        with pytest.raises(LiveSessionError):
            await session.send_audio(b"\x00\x00")
