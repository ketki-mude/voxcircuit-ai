"""Helpers for 16-bit little-endian mono PCM."""

import math
import sys
from array import array

from app.live.events import BYTES_PER_SAMPLE, INPUT_SAMPLE_RATE

INPUT_BYTES_PER_SECOND = INPUT_SAMPLE_RATE * BYTES_PER_SAMPLE

# RMS level (of 32768) above which a caller frame counts as speech, about -36 dBFS. The
# browser applies noise suppression, so room noise normally stays well below this.
SPEECH_RMS_THRESHOLD = 500.0


def pcm16_rms(pcm: bytes) -> float:
    if len(pcm) < BYTES_PER_SAMPLE:
        return 0.0
    samples = array("h")
    samples.frombytes(pcm[: len(pcm) - len(pcm) % BYTES_PER_SAMPLE])
    if sys.byteorder == "big":
        samples.byteswap()
    return math.sqrt(sum(s * s for s in samples) / len(samples))


def is_speech(pcm: bytes) -> bool:
    return pcm16_rms(pcm) >= SPEECH_RMS_THRESHOLD
