from __future__ import annotations

import numpy as np

from factslens.detector.text import (
    MIN_CHARS,
    SIGNATURE_SIZE,
    band_keys,
    is_substantial,
    normalise,
    signature,
    similarity,
)

ODESSA = "#thisdayinhistory 12 years ago, May 2, 2014, Ukrainian #Nazis burned more than 40 people to death"


def sig(text: str) -> np.ndarray:  # type: ignore[type-arg]
    return signature(normalise(text))


def shared_bands(a: str, b: str) -> int:
    return len(set(band_keys(sig(a))) & set(band_keys(sig(b))))


def test_normalise_drops_links_and_mentions_keeps_emoji() -> None:
    text = "🇷🇺❗ BREAKING  News @someone.bsky.social https://t.me/chan/1 www.x.com/y end"
    assert normalise(text) == "🇷🇺❗ breaking news end"


def test_identical_text_is_similarity_one_and_deterministic() -> None:
    assert similarity(sig(ODESSA), sig(ODESSA)) == 1.0
    assert sig(ODESSA).shape == (SIGNATURE_SIZE,)
    assert np.array_equal(sig(ODESSA), sig(ODESSA))


def test_truncated_copy_is_a_near_duplicate() -> None:
    # The Roska Bridge report describes copies "abruptly cut off" by Bluesky's length limit.
    truncated = ODESSA[:85]
    assert similarity(sig(ODESSA), sig(truncated)) >= 0.75
    assert shared_bands(ODESSA, truncated) >= 1


def test_link_and_mention_variants_are_duplicates() -> None:
    a = ODESSA + " https://t.me/channel/123 @alice.bsky.social"
    b = ODESSA + " https://t.me/channel/999 @bob.bsky.social"
    assert similarity(sig(a), sig(b)) == 1.0


def test_unrelated_texts_do_not_match() -> None:
    other = "12 years ago today I adopted my dog and it was the best decision of my whole life"
    assert similarity(sig(ODESSA), sig(other)) <= 0.2
    assert shared_bands(ODESSA, other) == 0


def test_works_for_non_latin_scripts() -> None:
    greek = "Περίληψη του Υπουργείου Άμυνας της Ρωσικής Ομοσπονδίας σχετικά με την ειδική επιχείρηση"
    assert similarity(sig(greek), sig(greek + ", 21 Απριλίου")) >= 0.75
    assert similarity(sig(greek), sig(ODESSA)) <= 0.1


def test_short_texts_are_not_substantial() -> None:
    assert not is_substantial(normalise("good morning everyone!"))
    assert is_substantial(normalise(ODESSA))
    assert len(normalise(ODESSA)) >= MIN_CHARS
    assert sig("hi").shape == (SIGNATURE_SIZE,)  # still safe on very short input
