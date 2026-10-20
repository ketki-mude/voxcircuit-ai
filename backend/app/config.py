from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache
from typing import Literal
from zoneinfo import ZoneInfo

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

    # Signs caller session tokens. When unset, a random key is made at startup, so tokens
    # stop working after a restart (fine for short-lived calls).
    app_secret_key: SecretStr | None = None
    integrations_mode: Literal["mock", "live"] = "mock"
    business_timezone: str = "America/New_York"
    approval_ttl_seconds: int = Field(default=300, ge=10)

    # INTEGRATIONS_MODE=live: HubSpot (service key) and Google Calendar (OAuth).
    hubspot_private_app_token: SecretStr | None = None
    google_oauth_client_id: str | None = None
    google_oauth_client_secret: SecretStr | None = None
    google_oauth_refresh_token: SecretStr | None = None
    google_calendar_id: str | None = None

    @property
    def allowed_origins(self) -> list[str]:
        return [o.strip() for o in self.frontend_origins.split(",") if o.strip()]

    @property
    def timezone(self) -> ZoneInfo:
        return ZoneInfo(self.business_timezone)

    def live_integrations(self) -> "LiveIntegrations":
        """The settings INTEGRATIONS_MODE=live needs, or ValueError naming any missing."""
        values = {
            "HUBSPOT_PRIVATE_APP_TOKEN": _secret(self.hubspot_private_app_token),
            "GOOGLE_OAUTH_CLIENT_ID": self.google_oauth_client_id or "",
            "GOOGLE_OAUTH_CLIENT_SECRET": _secret(self.google_oauth_client_secret),
            "GOOGLE_OAUTH_REFRESH_TOKEN": _secret(self.google_oauth_refresh_token),
            "GOOGLE_CALENDAR_ID": self.google_calendar_id or "",
        }
        missing = [name for name, value in values.items() if not value.strip()]
        if missing:
            raise ValueError(f"INTEGRATIONS_MODE=live needs these settings: {', '.join(missing)}")
        return LiveIntegrations(*values.values())

    @property
    def gemini_key(self) -> str | None:
        """The API key, or None when unset or blank."""
        if self.gemini_api_key is None:
            return None
        return self.gemini_api_key.get_secret_value().strip() or None


@dataclass(frozen=True)
class LiveIntegrations:
    hubspot_token: str
    google_client_id: str
    google_client_secret: str
    google_refresh_token: str
    google_calendar_id: str


def _secret(value: SecretStr | None) -> str:
    return value.get_secret_value() if value is not None else ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
