from array import array

from app.calls.latency import LatencyTracker
from app.live.audio import SPEECH_RMS_THRESHOLD, is_speech, pcm16_rms


def pcm(*samples: int) -> bytes:
    return array("h", samples).tobytes()


def test_pcm16_rms() -> None:
    assert pcm16_rms(b"") == 0.0
    assert pcm16_rms(pcm(0, 0, 0)) == 0.0
    assert pcm16_rms(pcm(1000, -1000, 1000, -1000)) == 1000.0
    assert pcm16_rms(pcm(1000, -1000) + b"\x01") == 1000.0  # A trailing odd byte is ignored.


def test_is_speech_uses_the_threshold() -> None:
    level = int(SPEECH_RMS_THRESHOLD)
    assert is_speech(pcm(level, -level))
    assert not is_speech(pcm(level - 1, -(level - 1)))


def test_latency_is_last_caller_input_to_first_model_audio() -> None:
    tracker = LatencyTracker(audio_only=True)
    tracker.caller_input(10.0)
    tracker.caller_input(10.4)  # The caller kept talking; the latest input counts.
    assert tracker.model_output(10.6, is_audio=False) is None  # Transcripts don't count.
    assert tracker.model_output(11.15, is_audio=True) == 750
    assert tracker.model_output(11.3, is_audio=True) is None  # Once per reply.

    tracker.reply_finished()
    tracker.caller_input(20.0)
    assert tracker.model_output(20.5, is_audio=True) == 500


def test_text_calls_count_the_first_transcript() -> None:
    tracker = LatencyTracker(audio_only=False)
    tracker.caller_input(1.0)
    assert tracker.model_output(1.25, is_audio=False) == 250


def test_reply_without_caller_input_has_no_latency() -> None:
    tracker = LatencyTracker(audio_only=True)
    assert tracker.model_output(5.0, is_audio=True) is None
    tracker.caller_input(5.5)  # Speech during the reply (barge-in or echo).
    assert tracker.model_output(5.6, is_audio=True) is None
    tracker.reply_finished()
    assert tracker.model_output(6.0, is_audio=True) == 500


def test_latency_is_never_negative() -> None:
    tracker = LatencyTracker(audio_only=False)
    tracker.caller_input(3.0)
    assert tracker.model_output(2.9, is_audio=True) == 0
