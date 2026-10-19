class LatencyTracker:
    """Measures caller input -> first model output, once per model reply.

    Caller input is the latest voiced audio frame or typed turn, so for speech this is the
    time from the caller's last audio to Maya's first audio, including the silence the
    voice activity detector waits for. In audio calls only model audio counts as output;
    in text calls the first transcript fragment counts too.
    """

    def __init__(self, *, audio_only: bool) -> None:
        self._audio_only = audio_only
        self._input_at: float | None = None
        self._replying = False

    def caller_input(self, at: float) -> None:
        self._input_at = at

    def model_output(self, at: float, *, is_audio: bool) -> int | None:
        """Return the latency in ms if this output starts a reply after caller input."""
        if self._replying or (self._audio_only and not is_audio):
            return None
        self._replying = True
        if self._input_at is None:
            return None
        latency_ms = max(0, round((at - self._input_at) * 1000))
        self._input_at = None
        return latency_ms

    def reply_finished(self) -> None:
        self._replying = False
