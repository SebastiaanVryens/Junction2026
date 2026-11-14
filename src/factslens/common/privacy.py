"""Keyed hashing of account identifiers (CLAUDE.md §7).

A plain hash of a public Bluesky DID is reversible: anyone can hash every DID in the public
firehose and look the result up. An HMAC with a secret key is not.
"""

from __future__ import annotations

import hashlib
import hmac

from factslens.common.settings import MissingSettingError, Settings

RAW_RETENTION_HOURS = 72


class AccountHasher:
    def __init__(self, key: bytes) -> None:
        if len(key) < 16:
            raise ValueError("ACCOUNT_HASH_KEY must be at least 16 bytes")
        self._key = key

    @classmethod
    def from_settings(cls, settings: Settings) -> AccountHasher:
        if settings.account_hash_key is None:
            raise MissingSettingError("ACCOUNT_HASH_KEY")
        return cls(settings.account_hash_key.get_secret_value().encode())

    def __call__(self, account_id: str) -> str:
        return hmac.new(self._key, account_id.encode(), hashlib.sha256).hexdigest()[:32]
