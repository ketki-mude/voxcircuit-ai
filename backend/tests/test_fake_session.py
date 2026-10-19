import asyncio

import pytest

from app.live.base import LiveQuotaError, LiveSessionError
from app.live.events import LiveEvent, OutputTranscript, TurnComplete
from app.live.fake import FakeLiveSession, scripted_reply


async def take_turn(session: FakeLiveSession) -> list[LiveEvent]:
    """Collect events up to and including the next TurnComplete."""
    collected: list[LiveEvent] = []
    async with asyncio.timeout(2):
        async for event in session.events():
            collected.append(event)
            if isinstance(event, TurnComplete):
                return collected
    return collected


def text_of(events: list[LiveEvent]) -> str:
    return "".join(e.text for e in events if isinstance(e, OutputTranscript))


async def test_plays_one_scripted_turn_per_input() -> None:
    async with FakeLiveSession([scripted_reply("Hello there."), scripted_reply("Second.")]) as s:
        await s.send_text("hi")
        assert text_of(await take_turn(s)) == "Hello there."
        await s.send_text("again")
        assert text_of(await take_turn(s)) == "Second."
        assert s.sent_text == ["hi", "again"]


async def test_falls_back_when_script_runs_out() -> None:
    async with FakeLiveSession([]) as session:
        await session.send_text("hi")
        assert "end of this scripted conversation" in text_of(await take_turn(session))


async def test_scripted_error_is_raised_from_events() -> None:
    async with FakeLiveSession([[LiveQuotaError("quota")]]) as session:
        await session.send_text("hi")
        with pytest.raises(LiveQuotaError):
            await take_turn(session)


async def test_close_ends_the_event_stream() -> None:
    session = FakeLiveSession([])
    await session.connect()

    async def drain() -> list[LiveEvent]:
        return [event async for event in session.events()]

    task = asyncio.create_task(drain())
    await asyncio.sleep(0)
    await session.close()
    assert await asyncio.wait_for(task, timeout=1) == []


async def test_send_requires_connection() -> None:
    session = FakeLiveSession([])
    with pytest.raises(LiveSessionError):
        await session.send_text("hi")


async def test_connect_error_is_raised() -> None:
    session = FakeLiveSession([], connect_error=LiveSessionError("boom"))
    with pytest.raises(LiveSessionError, match="boom"):
        await session.connect()


async def test_audio_plays_a_turn_every_audio_turn_bytes() -> None:
    session = FakeLiveSession(
        [scripted_reply("One."), scripted_reply("Two.")], audio_turn_bytes=100
    )
    async with session:
        await session.send_audio(bytes(60))
        await session.send_audio(bytes(60))  # 120 bytes: the first utterance ends.
        assert text_of(await take_turn(session)) == "One."
        await session.send_audio(bytes(100))
        assert text_of(await take_turn(session)) == "Two."
    assert session.sent_audio_bytes == 220
