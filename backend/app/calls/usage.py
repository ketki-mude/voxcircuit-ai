from dataclasses import dataclass
from decimal import Decimal

_SIX_PLACES = Decimal("0.000001")


def estimate_cost(
    seconds_in: float, seconds_out: float, per_min_in: Decimal, per_min_out: Decimal
) -> Decimal:
    """Estimated USD cost of audio at the configured per-minute rates."""
    minutes_in = Decimal(str(seconds_in)) / 60
    minutes_out = Decimal(str(seconds_out)) / 60
    return (minutes_in * per_min_in + minutes_out * per_min_out).quantize(_SIX_PLACES)


@dataclass(frozen=True)
class UsageDelta:
    """Usage since the previous flush; becomes one usage_logs row."""

    audio_seconds_in: float
    audio_seconds_out: float
    prompt_tokens: int | None
    response_tokens: int | None
    total_tokens: int | None
    estimated_cost_usd: Decimal


@dataclass
class UsageMeter:
    """Accumulates audio seconds and token counts for one call."""

    per_min_in: Decimal
    per_min_out: Decimal
    total_seconds_in: float = 0.0
    total_seconds_out: float = 0.0
    total_cost_usd: Decimal = Decimal(0)
    _pending_in: float = 0.0
    _pending_out: float = 0.0
    _tokens: tuple[int | None, int | None, int | None] | None = None

    def add_audio_in(self, seconds: float) -> None:
        self._pending_in += seconds
        self.total_seconds_in += seconds

    def add_audio_out(self, seconds: float) -> None:
        self._pending_out += seconds
        self.total_seconds_out += seconds

    def set_tokens(self, prompt: int | None, response: int | None, total: int | None) -> None:
        self._tokens = (prompt, response, total)

    def take(self) -> UsageDelta | None:
        """Return pending usage and reset it, or None when nothing was used."""
        if not self._pending_in and not self._pending_out and self._tokens is None:
            return None
        cost = estimate_cost(self._pending_in, self._pending_out, self.per_min_in, self.per_min_out)
        prompt, response, total = self._tokens or (None, None, None)
        delta = UsageDelta(
            audio_seconds_in=self._pending_in,
            audio_seconds_out=self._pending_out,
            prompt_tokens=prompt,
            response_tokens=response,
            total_tokens=total,
            estimated_cost_usd=cost,
        )
        self.total_cost_usd += cost
        self._pending_in = self._pending_out = 0.0
        self._tokens = None
        return delta
