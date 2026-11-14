from __future__ import annotations

from datetime import UTC, datetime, timedelta

from factslens.detector.scoring import (
    Category,
    Evidence,
    Profile,
    Tier,
    assess,
    creation_burst_share,
    template_bio_share,
)

NOW = datetime(2026, 11, 14, 12, tzinfo=UTC)
BIOS = [
    "Nurse in Tampere, night shifts and too much coffee",
    "PhD student working on glacier melt models. He/him",
    "I make ceramics and post photos of my garden",
    "Local journalist covering city hall since 2011",
    "Retired engineer. Birdwatching, chess, grandkids",
    "Software dev, open source, occasional bad puns",
    "Football fan from Liverpool, season ticket holder",
    "Illustrator for children's books; commissions closed",
    "Physics teacher who believes in public transport",
    "Mum of three, runner, amateur baker of sourdough",
]
NEWS_STATS = {"copy_share": 1.0, "burst_share_1m": 0.2, "events_per_account": 1.0}


def established(n: int) -> Profile:
    return Profile(
        available=True,
        handle=f"person{n}.bsky.social",
        created_at=NOW - timedelta(days=400 + 37 * n),
        followers=150 + n,
        follows=200,
        has_avatar=True,
        description=BIOS[n % len(BIOS)],
    )


def fresh(n: int) -> Profile:
    # Template bios like the Roska Bridge report describes: profession + hobby + interest.
    return Profile(
        available=True,
        handle=f"user{n}.mast.example.ap.brid.gy",
        created_at=NOW - timedelta(days=5, hours=n),
        followers=1,
        follows=1,
        has_avatar=False,
        description="Archaeologist who cares about the welfare of amphibians and keeps up with Cooking",
    )


def by_name(assessment_signals: list) -> dict:  # type: ignore[type-arg]
    return {s.name: s for s in assessment_signals}


def test_creation_burst_and_template_bios() -> None:
    assert creation_burst_share([NOW, NOW + timedelta(hours=1), NOW + timedelta(days=30)]) == 2 / 3
    assert creation_burst_share([]) == 0.0
    assert (
        template_bio_share(["Archaeologist who cares about frogs and keeps up with Cooking"] * 2 + [None])
        == 2 / 3
    )
    assert template_bio_share(["short", None]) == 0.0


def test_news_headline_shared_by_established_accounts_is_likely_organic() -> None:
    evidence = Evidence("text", NEWS_STATS, [established(n) for n in range(10)], [0] * 10, NOW)
    result = assess(evidence)
    assert result.tier is Tier.LIKELY_ORGANIC
    signals = by_name(result.signals)
    assert signals["copy_paste"].fired and not signals["new_accounts"].fired
    assert not any(s.fired for s in result.signals if s.category is Category.ACCOUNT)


def test_fresh_templated_accounts_copying_text_are_suspicious_with_reasons() -> None:
    stats = {"copy_share": 1.0, "burst_share_1m": 0.9, "events_per_account": 1.0}
    evidence = Evidence("text", stats, [fresh(n) for n in range(8)], [0] * 8, NOW)
    result = assess(evidence)
    assert result.tier is Tier.SUSPICIOUS
    signals = by_name(result.signals)
    for name in ("copy_paste", "synchrony", "new_accounts", "creation_burst", "low_reach", "template_bios"):
        assert signals[name].fired, name
    assert signals["new_accounts"].value == 1.0 and signals["new_accounts"].threshold == 0.5
    assert "100% of the accounts were created in the last 30 days" in signals["new_accounts"].explanation
    assert 0 < result.score <= 100


def test_pending_until_enough_profiles_are_fetched() -> None:
    profiles = [fresh(0)] + [None] * 9
    result = assess(Evidence("text", NEWS_STATS, profiles, [0] * 10, NOW))
    assert result.tier is Tier.PENDING and result.profile_coverage == 0.1


def test_provenance_with_coordination_is_suspicious_even_before_profiles() -> None:
    evidence = Evidence(
        "link",
        {"copy_share": 0.8, "burst_share_1m": 0.1, "events_per_account": 1.0},
        [None] * 5,
        [0] * 5,
        NOW,
        known_domains=["news-pravda.com"],
    )
    result = assess(evidence)
    assert result.tier is Tier.SUSPICIOUS
    assert by_name(result.signals)["known_domains"].explanation.endswith(": news-pravda.com.")


def test_removed_accounts_count_as_account_evidence() -> None:
    profiles = [Profile(available=False)] * 4 + [established(n) for n in range(6)]
    result = assess(Evidence("text", NEWS_STATS, profiles, [0] * 10, NOW))
    signals = by_name(result.signals)
    assert signals["removed_accounts"].fired and signals["removed_accounts"].value == 0.4
    assert result.tier is Tier.WATCH  # coordination + one account signal: not enough on its own


def test_repost_ring_and_cross_cluster_overlap() -> None:
    stats = {"copy_share": 0.0, "burst_share_1m": 0.1, "events_per_account": 1.0, "ring_share": 0.9}
    result = assess(Evidence("repost", stats, [established(n) for n in range(10)], [3] * 6 + [0] * 4, NOW))
    signals = by_name(result.signals)
    assert signals["repost_ring"].fired and signals["cross_cluster"].fired
    assert signals["cross_cluster"].value == 0.6
    assert result.tier is Tier.LIKELY_ORGANIC  # rings of real fans exist; no account evidence


def test_lists_not_loaded_means_no_provenance_signals() -> None:
    result = assess(Evidence("text", NEWS_STATS, [established(1)], [0], NOW, lists_loaded=False))
    assert not any(s.category is Category.PROVENANCE for s in result.signals)


def weather_bot(n: int) -> Profile:
    # Live example, 14 Nov 2026: a family of NWS alert bots created together with template bios.
    return Profile(
        available=True,
        handle=f"nws-office{n}.example.social",
        created_at=NOW - timedelta(days=20, minutes=n),
        followers=4,
        follows=0,
        has_avatar=True,
        description=f"Unofficial bot sharing alerts from NWS office {n}. This account is not monitored.",
    )


def test_disclosed_bots_are_declared_automation_not_suspicious() -> None:
    stats = {"copy_share": 1.0, "burst_share_1m": 0.9, "events_per_account": 1.0}
    result = assess(Evidence("text", stats, [weather_bot(n) for n in range(6)], [0] * 6, NOW))
    signals = by_name(result.signals)
    assert signals["creation_burst"].fired and signals["template_bios"].fired  # they do look like a bot net
    assert signals["declared_automation"].fired and signals["declared_automation"].value == 1.0
    assert result.tier is Tier.DECLARED_AUTOMATION and result.score <= 20


def test_declaring_bot_does_not_hide_known_propaganda() -> None:
    stats = {"copy_share": 1.0, "burst_share_1m": 0.9, "events_per_account": 1.0}
    evidence = Evidence(
        "text", stats, [weather_bot(n) for n in range(6)], [0] * 6, NOW, known_domains=["news-pravda.com"]
    )
    assert assess(evidence).tier is Tier.SUSPICIOUS


def test_automation_words_need_whole_words() -> None:
    from factslens.detector.scoring import declares_automation

    assert declares_automation(Profile(True, description="*Automated feed. Monitored 7AM-5PM*"))
    assert declares_automation(Profile(True, handle="newsbot.bsky.social"))
    assert not declares_automation(Profile(True, description="Robotics PhD, bottle collector, Abbott fan"))
    assert not declares_automation(Profile(True, handle="abbott.bsky.social"))
