from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[2]

REQUIRED_SCOPES: tuple[str, ...] = (
    "tweet.read",
    "tweet.write",
    "users.read",
    "offline.access",
)


class Settings(BaseSettings):
    """Application settings, loaded from environment variables / `.env`.

    Every secret is a `SecretStr` so it never shows up in repr()/logs by accident.
    """

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- App ---
    app_name: str = "X Post Automation API"
    app_env: str = "development"
    debug: bool = True
    host: str = "127.0.0.1"
    port: int = 8000
    log_level: str = "INFO"

    # --- MongoDB ---
    mongodb_uri: SecretStr = SecretStr("mongodb://localhost:27017")
    mongodb_database: str = "x_post_automation"

    # --- X (OAuth 2.0 user context) ---
    x_client_id: str = ""
    x_client_secret: SecretStr = SecretStr("")
    # Optional bootstrap tokens (OAuth 2.0 user-context tokens only!).
    x_access_token: SecretStr = SecretStr("")
    x_refresh_token: SecretStr = SecretStr("")
    x_redirect_uri: str = "http://localhost:8000/api/v1/auth/x/callback"
    x_scopes: str = "tweet.read,tweet.write,users.read,offline.access"

    token_refresh_buffer_seconds: int = 300
    oauth_state_ttl_seconds: int = 600

    # ------------------------------------------------------------------ helpers
    @property
    def is_production(self) -> bool:
        return self.app_env.strip().lower() in {"prod", "production"}

    @property
    def debug_enabled(self) -> bool:
        """Debug is never honoured in production."""
        return bool(self.debug) and not self.is_production

    @property
    def scopes(self) -> list[str]:
        return [s for s in re.split(r"[,\s]+", self.x_scopes.strip()) if s]

    @property
    def scope_string(self) -> str:
        return " ".join(self.scopes)

    @property
    def missing_required_scopes(self) -> list[str]:
        have = set(self.scopes)
        return [s for s in REQUIRED_SCOPES if s not in have]

    def secret_values(self) -> list[str]:
        """All configured secret strings (used to seed the log/response redactor)."""
        values = [
            self.mongodb_uri.get_secret_value(),
            self.x_client_secret.get_secret_value(),
            self.x_access_token.get_secret_value(),
            self.x_refresh_token.get_secret_value(),
        ]
        return [v for v in values if v]


@lru_cache
def get_settings() -> Settings:
    return Settings()
