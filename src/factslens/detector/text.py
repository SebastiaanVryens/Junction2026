"""Near-duplicate text detection: normalisation, MinHash signatures and LSH band keys.

Works on characters, not words, so it behaves the same for English, Greek, Russian or
Japanese posts. Two texts are "near-duplicates" when their 5-character shingle sets overlap
strongly (Jaccard similarity), which survives small edits, truncation and added emoji.
"""

from __future__ import annotations

import re
import unicodedata

import numpy as np
import numpy.typing as npt

SHINGLE_CHARS = 5
SIGNATURE_SIZE = 24
BAND_ROWS = 4  # 6 bands of 4 rows: two texts with Jaccard 0.9 share a band 99.8% of the time, 0.3 only 5%
MIN_CHARS = 40  # shorter texts ("good morning", "lol") repeat by chance

_URL = re.compile(r"https?://\S+|\bwww\.\S+|\b[\w-]+(?:\.[\w-]+)+/\S*")
_MENTION = re.compile(r"@[\w.-]+")
_SPACE = re.compile(r"\s+")

_rng = np.random.default_rng(20261114)  # fixed seed: signatures must be comparable across runs
_MULTIPLIERS = _rng.integers(1, 2**63, size=SIGNATURE_SIZE, dtype=np.uint64) | np.uint64(1)
_OFFSETS = _rng.integers(0, 2**63, size=SIGNATURE_SIZE, dtype=np.uint64)
_POWERS = np.array([pow(1_000_003, i, 2**64) for i in range(SHINGLE_CHARS)], dtype=np.uint64)
_MIX = np.uint64(0x9E3779B97F4A7C15)

type Signature = npt.NDArray[np.uint64]


def normalise(text: str) -> str:
    """Lower-case, drop links and @mentions, collapse whitespace. Emoji are kept: copied
    campaigns reuse the same emoji sequences."""
    text = unicodedata.normalize("NFKC", text).casefold()
    text = _MENTION.sub(" ", _URL.sub(" ", text))
    return _SPACE.sub(" ", text).strip()


def is_substantial(normalised: str) -> bool:
    return len(normalised) >= MIN_CHARS


def signature(normalised: str) -> Signature:
    """MinHash signature of the text's character shingles."""
    codes = np.frombuffer(normalised.encode("utf-32-le"), dtype=np.uint32).astype(np.uint64)
    count = len(codes) - SHINGLE_CHARS + 1
    if count <= 0:
        codes = np.pad(codes, (0, SHINGLE_CHARS - len(codes)))
        count = 1
    with np.errstate(over="ignore"):
        shingles = np.zeros(count, dtype=np.uint64)
        for offset in range(SHINGLE_CHARS):
            shingles += codes[offset : offset + count] * _POWERS[offset]
        shingles = (shingles ^ (shingles >> np.uint64(29))) * _MIX
        hashed = shingles[None, :] * _MULTIPLIERS[:, None] + _OFFSETS[:, None]
        hashed ^= hashed >> np.uint64(32)
    return hashed.min(axis=1)


def band_keys(sig: Signature) -> list[tuple[int, bytes]]:
    """Texts that share any one of these keys are candidates for being near-duplicates."""
    return [
        (band, sig[start : start + BAND_ROWS].tobytes())
        for band, start in enumerate(range(0, len(sig), BAND_ROWS))
    ]


def similarity(a: Signature, b: Signature) -> float:
    """Estimated Jaccard similarity of the two texts' shingle sets (0..1)."""
    return float(np.mean(a == b))
