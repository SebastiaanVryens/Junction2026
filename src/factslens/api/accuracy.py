"""Accuracy & Corrections: the newspaper's standards column.

Live figures come from human reviews and the latest evaluation run (both in PostgreSQL). The
report-quality figures are a fixed, documented evaluation (docs/EVAL.md, 14 Nov 2026) and are
labelled as such. Every figure says what it counts and how many.
"""

from __future__ import annotations

from typing import Any

REPO = "https://github.com/SebastiaanVryens/Junction2026"

STANDARDS = [
    "We never accuse anyone automatically. Every finding is a lead for a person to weigh.",
    "We say “not enough evidence” rather than guess, and a verdict without grounded evidence is downgraded.",
    "Every claim links to its evidence. References the model was never shown are removed before publication.",
    "Account names appear only for accounts already on public lists; pictures from individuals stay "
    "with reviewers.",
    "We publish our error rate, including where we are blind.",
]

# Each correction was found on live data on 14 Nov 2026 (docs/EVAL.md, docs/IMPROVEMENTS.md).
CORRECTIONS = [
    (
        "Disclosed weather-alert and traffic bots, created together with template bios and posting in sync, "
        "were rated suspicious.",
        "A disclosure signal (bios and handles that say “bot” or “automated feed”, several "
        "languages) and a separate declared-bots tier with a capped score. Documented-network evidence still "
        "overrides it.",
    ),
    (
        "Real people sharing the same headline through a share button looked “coordinated”.",
        "Coordination alone is never suspicious: account or provenance evidence must come on top.",
    ),
    (
        "An early prompt (v3) called a UK news-syndication cluster staged.",
        "Innocent explanation first, reasons before the verdict, abstaining allowed. No later version called "
        "one of the organic controls staged.",
    ),
    (
        "A later prompt (v6) called accounts on a server documented as part of a propaganda network organic.",
        "A rule, not a prompt: with documented-network evidence, “organic” becomes “not enough "
        "evidence” for a person to decide.",
    ),
    (
        "The same news photo with a Polish and a Russian caption counted as a “recycled picture”.",
        "Nemotron judges every reuse; only pictures that do not fit the later caption are highlighted.",
    ),
    (
        "Clusters that merely mentioned a name were listed as amplifying a contested story.",
        "Nemotron classifies each matched cluster; only clusters that repeat the story are linked.",
    ),
    (
        "Users of big general Mastodon servers counted as “accounts on network servers”.",
        "Only servers where IFTAS documented at least 10 network accounts count.",
    ),
    (
        "A Belgian article was reported as having no coverage: we read a consent page, searched only the "
        "Netherlands edition, and used eight-word queries.",
        "National news editions (e.g. Belgium in Dutch and French), short searches retried with fewer words, "
        "and the original website kept as the source when a site shows a consent page.",
    ),
]

BLIND_SPOTS = [
    "Evasive campaigns (own words, several languages, aged accounts) share no text, link or hashtags, so we "
    "do not catch them yet: 0 of 4 planted evasive campaigns. Catching them needs grouping by meaning.",
    "We see only the last 72 hours, and only Bluesky, the Telegram channels the Pravda network republishes "
    "most, and the Mastodon servers on IFTAS's list.",
    "Our random review sample is still small, so the false-positive rate on live traffic has a wide margin.",
    "Running the same Nemotron prompt twice changed 4 of 11 verdicts on our fixed set: single verdicts are "
    "leads, not measurements.",
]

# Fixed evaluation of Nemotron's reports: 12 live clusters, one run per version (docs/EVAL.md).
REPORT_QUALITY = {
    "set": "12 live clusters chosen to cover every kind, three of them organic controls",
    "grounded": "91\u201398%",
    "controls_called_staged": "0 of 3 since prompt v5 (1 of 3 with v3)",
    "date": "14 Nov 2026",
}


def directions(result: dict[str, Any] | None, reviews: dict[str, Any]) -> list[dict[str, Any]]:
    """Hostinger's three directions we combine, where a reader sees each, and how it is measured."""
    recall = (result or {}).get("campaign_recall") or {}
    simple = (recall.get("simple") or {}).get("suspicious") or {}
    varied = (recall.get("varied") or {}).get("suspicious_or_watch") or {}
    documented = (result or {}).get("documented_accounts") or {}

    def caught(r: dict[str, Any]) -> str:
        return f"{r.get('caught', '?')} of {r.get('n', '?')}" if r else "not measured"

    return [
        {
            "name": "Spot the swarm",
            "what": "Groups of accounts acting together, found from timing, copied text, shared links and "
            "hashtags, repost rings, account age and creation bursts, never from a single post.",
            "see": [("Look for the Truth: see the groups behind a post", "/lens"),
                    ("Ask the Truth: “What are the biggest campaigns now?”", "/ask")],
            "measure": f"Planted copy-paste campaigns caught: {caught(simple)}; paraphrased ones flagged for "
            f"watching: {caught(varied)}. Accounts on IFTAS's documented list flagged: "
            f"{documented.get('suspicious_or_watch', '?')} of {documented.get('posted_in_window', '?')}, "
            f"a median {documented.get('median_minutes_to_candidate', '?')} minutes after their first post.",
        },
        {
            "name": "Name the technique",
            "what": "Loaded language, fear appeals, whataboutism and fabricated authority, each shown "
            "with the exact words and explained in plain language.",
            "see": [("Look for the Truth: “How it tries to persuade”", "/lens")],
            "measure": "A technique is only published when its quote appears word for word in the text. "
            f"Claims backed by evidence in Nemotron's reports: {REPORT_QUALITY['grounded']} on our "
            "fixed set.",
        },
        {
            "name": "Trust the source",
            "what": "Who is behind a post or page: account age and reach, public network lists, domain "
            "registration and propaganda-domain lists, and how independent outlets in several countries "
            "tell the same story.",
            "see": [("Look for the Truth: “Who is behind it”", "/lens"),
                    ("Contested stories: state media against independent outlets", "/stories")],
            "measure": "Every outlet is checked against VIGINUM's and CheckFirst's lists; independence is "
            "judged by distinct outlets in different countries, never by size or official status.",
        },
    ]  # fmt: skip
