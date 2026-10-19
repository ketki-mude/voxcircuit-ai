from decimal import Decimal
from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Backend configuration, read from environment variables and backend/.env."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+psycopg://localhost:5432/voxcircuit"
    frontend_origins: str = "http://localhost:3000"

    gemini_api_key: SecretStr | None = None
    gemini_live_model: str = "gemini-3.8-live"
    vad_silence_duration_ms: int = Field(default=700, ge=100, le=5000)

    max_call_seconds: int = Field(default=300, ge=1)
    daily_call_cap: int = Field(default=20, ge=0)
    max_turns: int = Field(default=30, ge=1)
    audio_cost_per_min_in: Decimal = Field(default=Decimal("0.005"), ge=0)
    audio_cost_per_min_out: Decimal = Field(default=Decimal("0.018"), ge=0)

    @property
    def allowed_origins(self) -> list[str]:
        return [o.strip() for o in self.frontend_origins.split(",") if o.strip()]

    @property
    def gemini_key(self) -> str | None:
        """The API key, or None when unset or blank."""
        if self.gemini_api_key is None:
            return None
        return self.gemini_api_key.get_secret_value().strip() or None


@lru_cache
def get_settings() -> Settings:
    return Settings()
