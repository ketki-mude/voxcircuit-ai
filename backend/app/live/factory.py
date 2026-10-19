from collections.abc import Callable
from typing import Literal

from google import genai

from app.config import Settings
from app.live.base import LiveConfigError, LiveSession
from app.live.fake import FakeLiveSession
from app.live.gemini import GeminiLiveSession
from app.prompts import MAYA_SYSTEM_INSTRUCTION

EngineName = Literal["gemini", "fake"]
InputMode = Literal["text", "audio"]
LiveSessionFactory = Callable[[EngineName, InputMode], LiveSession]


def default_live_session_factory(settings: Settings) -> LiveSessionFactory:
    """Build sessions for each call. The Gemini client is created once, on first use."""
    client: genai.Client | None = None

    def make(engine: EngineName, input_mode: InputMode) -> LiveSession:
        nonlocal client
        if engine == "fake":
            if input_mode == "audio":
                # The scripted demo cannot hear; it only answers typed turns.
                raise LiveConfigError(
                    "offline engine is text-only",
                    caller_message="Offline test works in text mode only. "
                    "Choose Gemini Live for a voice call.",
                )
            return FakeLiveSession.demo()
        key = settings.gemini_key
        if key is None:
            raise LiveConfigError("GEMINI_API_KEY is not set")
        if client is None:
            client = genai.Client(api_key=key)
        return GeminiLiveSession(
            client=client,
            model=settings.gemini_live_model,
            system_instruction=MAYA_SYSTEM_INSTRUCTION,
            silence_duration_ms=settings.vad_silence_duration_ms,
        )

    return make
