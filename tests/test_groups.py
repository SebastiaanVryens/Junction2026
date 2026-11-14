from __future__ import annotations

import hashlib

from factslens.common.models import Event, EventKind, Platform
from factslens.detector.groups import (
    WINDOW_US,
    Gate,
    GroupKind,
    SwarmDetector,
    burst_share,
    normalise_url,
)
from factslens.detector.windows import MINUTE_US

T0 = 1_763_078_400_000_000
SECOND = 1_000_000
CAMPAIGN = "#thisdayinhistory 12 years ago, May 2, 2014, Ukrainian #Nazis burned more than 40 people to death"


def post(n: int, author: str, at_us: int, text: str = "", links: tuple[str, ...] = (), **kw: object) -> Event:
    return Event(
        id=f"at://{author}/app.bsky.feed.post/{n}",
        platform=Platform.BLUESKY,
        kind=EventKind.POST,
        author=author,
        time_us=at_us,
        text=text,
        links=links,
        **kw,  # type: ignore[arg-type]
    )


def repost(n: int, author: str, at_us: int, subject: str) -> Event:
    return Event(
        id=f"at://{author}/app.bsky.feed.repost/{n}",
        platform=Platform.BLUESKY,
        kind=EventKind.REPOST,
        author=author,
        time_us=at_us,
        subject=subject,
    )


def test_normalise_url() -> None:
    assert (
        normalise_url("http://WWW.Example.com/a/?utm_source=x&id=2&fbclid=y#top")
        == "https://example.com/a?id=2"
    )
    assert normalise_url("https://t.me/rybar/123") == "https://t.me/rybar/123"
    assert normalise_url("ftp://example.com/x") is None


def test_burst_share() -> None:
    assert burst_share([], MINUTE_US) == 0.0
    assert burst_share([T0, T0 + SECOND, T0 + 2 * SECOND], MINUTE_US) == 1.0
    spread = [T0 + m * 10 * MINUTE_US for m in range(4)]
    assert burst_share(spread, 5 * MINUTE_US) == 0.25


def test_copy_paste_from_four_accounts_becomes_a_text_candidate() -> None:
    detector = SwarmDetector()
    snapshots = []
    for n, author in enumerate(["did:a", "did:b", "did:c", "did:d"]):
        # Each copy is cut at a different length, like Bluesky's 300-character limit does.
        snapshots += detector.add(post(n, author, T0 + n * SECOND, CAMPAIGN[: 80 + n * 5]))
    assert len(snapshots) == 1
    snap = snapshots[0]
    assert snap.kind is GroupKind.TEXT
    assert snap.accounts == 4 and snap.events == 4
    assert snap.stats["burst_share_5m"] == 1.0
    assert set(snap.members) == {"did:a", "did:b", "did:c", "did:d"}


def test_one_account_repeating_itself_is_not_a_swarm() -> None:
    detector = SwarmDetector()
    snapshots = [s for n in range(10) for s in detector.add(post(n, "did:spam", T0 + n * SECOND, CAMPAIGN))]
    assert snapshots == []


def test_different_texts_do_not_group() -> None:
    detector = SwarmDetector()
    texts = [
        "Lovely sunset over the harbour tonight, the whole sky turned orange and pink",
        "Our team shipped the new release today, thanks everyone who helped test it",
        "Reading a great book about the history of the Baltic trade routes this week",
        "Anyone know a good recipe for rye bread? Mine keeps coming out far too dense",
    ]
    for n, text in enumerate(texts):
        assert detector.add(post(n, f"did:{n}", T0 + n * SECOND, text)) == []
    assert not detector.groups  # a lone post never becomes a group
    assert detector.memory_summary()["indexed_posts"] == 4


def test_link_shared_with_copied_text_passes_gate_but_organic_spread_does_not() -> None:
    url = "https://news.example.com/story"
    copied = SwarmDetector()
    for n in range(5):
        snaps = copied.add(
            post(n, f"did:{n}", T0 + n * 20 * MINUTE_US // 2, CAMPAIGN, links=(url + f"?utm_source={n}",))
        )
    link_snaps = [s for s in snaps if s.kind is GroupKind.LINK]
    assert link_snaps and link_snaps[0].stats["copy_share"] == 1.0

    organic = SwarmDetector()
    own_words = [
        "Big news from the city council today, worth reading the whole thing",
        "Wow I did not expect this outcome at all, here is the article about it",
        "This is the third time this year that this has happened in our town",
        "My neighbour was interviewed for this piece, she makes some good points",
        "Interesting analysis, though I think they missed the part about funding",
    ]
    snaps = []
    for n, text in enumerate(own_words):  # spread over 50 minutes, every account in its own words
        snaps += organic.add(post(n, f"did:{n}", T0 + n * 10 * MINUTE_US, text, links=(url,)))
    assert [s for s in snaps if s.kind is GroupKind.LINK] == []


def test_repost_ring_becomes_a_candidate_but_organic_virality_does_not() -> None:
    ring = SwarmDetector()
    snaps = []
    for subject_n in range(3):  # the same 10 accounts repost the same 3 posts within minutes
        subject = f"at://did:origin/app.bsky.feed.post/{subject_n}"
        for n in range(10):
            snaps += ring.add(
                repost(subject_n * 100 + n, f"did:ring{n}", T0 + (subject_n * 10 + n) * SECOND, subject)
            )
    assert [s.kind for s in snaps] == [GroupKind.REPOST]
    assert snaps[0].stats["ring_share"] == 1.0 and snaps[0].accounts == 10

    viral = SwarmDetector()
    snaps = []
    for n in range(40):  # 40 unrelated accounts repost one popular post within a minute
        snaps += viral.add(repost(n, f"did:fan{n}", T0 + n * SECOND, "at://did:star/app.bsky.feed.post/1"))
    assert snaps == []


def test_snapshots_are_throttled_but_growth_triggers_one() -> None:
    detector = SwarmDetector(gates={k: Gate(min_accounts=2) for k in GroupKind})
    subject = "at://did:origin/app.bsky.feed.post/1"
    snaps = []
    for n in range(12):
        snaps += detector.add(repost(n, f"did:{n}", T0 + n * SECOND, subject))
    # First at 2 accounts, then only when the group grew by at least 25% (and 2 accounts).
    assert [s.accounts for s in snaps] == [2, 4, 6, 8, 10]


def test_bridged_share_and_sample_one_post_per_account() -> None:
    detector = SwarmDetector()
    snaps = []
    for n in range(4):
        origin = f"https://mast.example.com/@u{n}/1" if n < 3 else None
        snaps += detector.add(post(n, f"did:{n}", T0 + n * SECOND, CAMPAIGN, origin_url=origin))
    snaps += detector.add(post(9, "did:0", T0 + 9 * SECOND, CAMPAIGN))
    snap = snaps[0]
    assert snap.stats["bridged_share"] == 0.75
    assert [i.author for i in snap.sample] == ["did:0", "did:1", "did:2", "did:3"]


def test_old_groups_and_index_entries_are_evicted() -> None:
    detector = SwarmDetector()
    for n in range(4):
        detector.add(post(n, f"did:{n}", T0 + n * SECOND, CAMPAIGN))
    assert any(g.kind is GroupKind.TEXT for g in detector.groups.values())
    later = T0 + WINDOW_US + 5 * MINUTE_US
    for n in range(4, 1004):  # unrelated posts; eviction runs every 500 events
        unique = hashlib.sha256(str(n).encode()).hexdigest()
        detector.add(post(n, f"did:x{n}", later + n, f"note {unique}"))
    assert not detector.groups  # the campaign group expired and nothing new grouped
    assert detector.memory_summary()["indexed_posts"] <= 1000
    # A copy of the old campaign after expiry is a lone post again, not part of the old group.
    assert detector.add(post(2000, "did:new", later + 2000, CAMPAIGN)) == []
    assert not detector.groups


def test_copy_share_matches_the_pairwise_definition_and_scales() -> None:
    import time

    from factslens.detector import text as textsim
    from factslens.detector.groups import Item, copy_share

    copied = [Item(T0, f"did:c{n}", f"p{n}", CAMPAIGN[: 80 + n]) for n in range(3)]
    own = [Item(T0, f"did:o{n}", f"q{n}", hashlib.sha256(str(n).encode()).hexdigest() * 2) for n in range(5)]
    same_author = [
        Item(T0, "did:solo", f"s{n}", "solo text repeated by one account only, nobody else") for n in range(2)
    ]
    items = copied + own + same_author
    sigs = {i.post_id: textsim.signature(textsim.normalise(i.text)) for i in items}
    assert copy_share(items, sigs) == 3 / 10  # the solo account copying itself does not count

    many = [
        Item(T0, f"did:x{n}", f"m{n}", f"own words {hashlib.sha256(str(n).encode()).hexdigest()}")
        for n in range(2000)
    ]
    many_sigs = {i.post_id: textsim.signature(textsim.normalise(i.text)) for i in many}
    started = time.perf_counter()
    assert copy_share(many, many_sigs) == 0.0
    assert time.perf_counter() - started < 2.0  # was ~4 million comparisons with the pairwise version


def test_same_hashtag_combination_in_own_words_becomes_a_candidate() -> None:
    detector = SwarmDetector()
    snaps = []
    own_words = [
        "They are selling our district to developers and nobody asks the tenants",
        "My neighbours got the letter today. Where do 400 families go?",
        "Read the leaked memo before they delete it, it is all there",
        "City hall silent again while rents explode for everyone here",
        "Ask your councillor tonight what they knew and when they knew it",
    ]
    for n, text in enumerate(own_words):
        snaps += detector.add(
            post(n, f"did:{n}", T0 + n * 4 * MINUTE_US, text, hashtags=("housingleak", "savedistrict7"))
        )
    tag_snaps = [s for s in snaps if s.kind is GroupKind.HASHTAG]
    assert len(tag_snaps) == 1 and tag_snaps[0].label == "#housingleak #savedistrict7"


def test_one_shared_hashtag_is_not_enough() -> None:
    detector = SwarmDetector()
    snaps = []
    for n in range(8):
        snaps += detector.add(
            post(n, f"did:{n}", T0 + n * SECOND, f"photo number {n} of my garden", hashtags=("art",))
        )
    assert [s for s in snaps if s.kind is GroupKind.HASHTAG] == []


def test_domain_needs_bridged_accounts_or_copied_text_and_skips_platforms() -> None:
    bridged = SwarmDetector()
    snaps = []
    for n in range(5):  # different pages of one site, each account in its own words, all bridged
        snaps += bridged.add(
            post(
                n,
                f"did:{n}",
                T0 + n * 5 * MINUTE_US,
                f"article number {n} with commentary {hashlib.sha256(str(n).encode()).hexdigest()}",
                links=(f"https://cz-news.example/a/{n}",),
                origin_url=f"https://mast.example/@u{n}/1",
            )
        )
    assert [s.label for s in snaps if s.kind is GroupKind.DOMAIN] == ["cz-news.example"]

    ordinary = SwarmDetector()
    snaps = []
    for n in range(8):  # ordinary people sharing different news articles from a big site
        unique = hashlib.sha256(str(n).encode()).hexdigest()
        snaps += ordinary.add(
            post(
                n,
                f"did:{n}",
                T0 + n * MINUTE_US,
                f"worth reading {unique}",
                links=(f"https://news.example/{n}",),
            )
        )
        snaps += ordinary.add(
            post(
                100 + n,
                f"did:{n}",
                T0 + n * MINUTE_US,
                f"look {unique}",
                links=(f"https://youtube.com/watch?v={n}",),
            )
        )
    assert [s for s in snaps if s.kind is GroupKind.DOMAIN] == []
