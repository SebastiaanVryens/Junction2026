"""Configuration, read from environment variables and the repo's `.env` file."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]

# Bluesky's four public Jetstream servers. The ingest rotates to the next one when a server
# stalls: on 14 Nov 2026 both us-east servers stopped replaying at the same point in the stream
# while both us-west servers passed it.
JETSTREAM_DEFAULT_URLS = (
    "wss://jetstream2.us-east.bsky.network/subscribe,"
    "wss://jetstream1.us-east.bsky.network/subscribe,"
    "wss://jetstream1.us-west.bsky.network/subscribe,"
    "wss://jetstream2.us-west.bsky.network/subscribe"
)


class Settings(BaseSettings):
    """Every setting is optional here; each service checks the ones it needs at start-up."""

    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
    )

    # Bluesky: comma-separated Jetstream endpoints, tried in order
    jetstream_urls: str = JETSTREAM_DEFAULT_URLS

    def jetstream_url_list(self) -> list[str]:
        return [url.strip() for url in self.jetstream_urls.split(",") if url.strip()]

    # Aiven Kafka
    kafka_bootstrap: str | None = None
    kafka_ca_file: Path | None = None
    kafka_sasl_username: str | None = None
    kafka_sasl_password: SecretStr | None = None
    kafka_posts_topic: str = "posts"

    # Aiven PostgreSQL
    database_url: SecretStr | None = None

    # Local data (recorded stream windows, file cursors); git-ignored
    data_dir: Path = REPO_ROOT / "data"

    def resolved(self, path: Path) -> Path:
        """Paths in `.env` are relative to the repo root."""
        return path if path.is_absolute() else REPO_ROOT / path


class MissingSettingError(RuntimeError):
    def __init__(self, *names: str) -> None:
        super().__init__(f"missing setting(s) in .env: {', '.join(names)}")


@lru_cache
def get_settings() -> Settings:
    return Settings()
