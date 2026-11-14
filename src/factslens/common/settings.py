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

    # Telegram: comma-separated public channel names; empty = the channels Pravda republishes most
    telegram_channels: str = ""
    telegram_poll_seconds: float = 300.0

    def telegram_channel_list(self) -> list[str]:
        return [c.strip().lstrip("@") for c in self.telegram_channels.split(",") if c.strip()]

    # Mastodon: comma-separated servers; empty = the servers on IFTAS's list
    mastodon_servers: str = ""
    mastodon_poll_seconds: float = 300.0

    def mastodon_server_list(self) -> list[str]:
        return [s.strip().lower() for s in self.mastodon_servers.split(",") if s.strip()]

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

    # Privacy: key for the account-id HMAC (CLAUDE.md §7). Every service writing to the same
    # database must use the same key, or the same account gets different hashes.
    account_hash_key: SecretStr | None = None
    # Shared secret that unlocks account names and post texts on cluster pages.
    reviewer_token: SecretStr | None = None

    # NVIDIA Nemotron (NVIDIA Build, OpenAI-compatible). CLAUDE.md §2B: Super VL is mandatory;
    # never substitute another model. Whether teammates' keys may be pooled is still open, so
    # only the first key is used unless NEMOTRON_KEYS_IN_USE is raised.
    nvidia_api_keys: SecretStr | None = None
    nvidia_base_url: str = "https://integrate.api.nvidia.com/v1"
    nemotron_model: str = "nvidia/nemotron-3.5-super-vl-120b-a12b"
    nemotron_keys_in_use: int = 1
    # Our own ceiling, well below the ~40 requests/minute per key NVIDIA mentions.
    nemotron_max_calls_per_minute: int = 20
    nemotron_max_calls_per_hour: int = 300

    # Downloaded ground-truth lists (eval/ground_truth/fetch.py output)
    ground_truth_dir: Path = REPO_ROOT / "eval" / "ground_truth" / "data"

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
