from __future__ import annotations

import pytest

from factslens.common.privacy import AccountHasher


def test_hash_is_stable_keyed_and_not_the_plain_id() -> None:
    a, b = AccountHasher(b"k" * 32), AccountHasher(b"j" * 32)
    did = "did:plc:abc123"
    assert a(did) == a(did)
    assert a(did) != b(did)  # without the key, the hash cannot be recomputed from public DIDs
    assert did not in a(did) and len(a(did)) == 32


def test_short_key_is_rejected() -> None:
    with pytest.raises(ValueError):
        AccountHasher(b"short")


def test_retention_removes_every_identifier_after_72_hours() -> None:
    from factslens.common.privacy import RAW_RETENTION_HOURS
    from factslens.common.store import RETENTION_SQL

    sql = " ".join(RETENTION_SQL)
    assert RAW_RETENTION_HOURS == 72
    for cleared in (
        "cluster_members SET did = NULL",
        "handle = NULL",
        "display_name = NULL",
        "description = NULL",
    ):
        assert cleared in sql, cleared
    assert "DELETE FROM cluster_posts" in sql
    assert "evidence = evidence - 'posts'" in sql  # post ids contain account ids
    assert "DELETE FROM campaigns" in sql
