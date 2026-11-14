from __future__ import annotations

from typing import Any

from factslens.eval.report_quality import compare

SET = {
    "name": "test-set",
    "clusters": [
        {"id": "a", "why": "IFTAS-listed server"},
        {"id": "b", "why": "spam"},
        {"id": "c", "why": "organic control: news"},
    ],
}


def row(
    cluster: str, verdict: str | None, *, report_calls: int = 1, cut: bool = False, **extra: Any
) -> dict[str, Any]:
    calls = [{"purpose": "investigate:analyse", "finish": "stop"}]
    calls += [{"purpose": "investigate:report", "finish": "length" if cut else "stop"}] * report_calls
    report = None
    if verdict:
        report = {
            "verdict": verdict,
            "techniques": [{}] * 2,
            "media": [],
            "news_checks": extra.pop("news", []),
        }
    return {
        "cluster": cluster,
        "prompt_version": extra.pop("version", "v5"),
        "ok": report is not None,
        "seconds": extra.pop("seconds", 30.0),
        "calls": calls,
        "tool_calls": extra.pop("tools", []),
        "prompt_tokens": 1000,
        "completion_tokens": 500,
        "media_shown": 1,
        "grounding": {"share": 0.75, "items": 4, "grounded": 3, "problems": ["x"]} if report else None,
        "report": report,
    }


def test_compare_counts_the_same_things_for_both_versions() -> None:
    before = [
        row("a", "insufficient_evidence", report_calls=2, cut=True, version="v3"),
        row("b", "staged", version="v3"),
        row("c", "staged", version="v3"),
    ]
    after = [
        row("a", "staged", tools=["search_news"], news=[{"finding": "no_coverage_found"}]),
        row("b", "staged", seconds=50.0),
        row("c", "organic"),
    ]
    result = compare(SET, before, after)
    old, new = result["before"], result["after"]
    assert old["prompt_version"] == "v3" and new["prompt_version"] == "v5"
    assert old["valid_on_first_try"] == 2 and old["calls_cut_off_at_token_limit"] == 1
    assert old["organic_controls_called_staged"] == 1 and new["organic_controls_called_staged"] == 0
    assert old["documented_cases_called_staged"] == 0 and new["documented_cases_called_staged"] == 1
    assert new["news_findings"] == {"no_coverage_found": 1} and new["tool_calls"] == {"search_news": 1}
    assert new["items_removed_as_ungrounded"] == 3 and new["seconds_median"] == 30.0
    assert result["same_verdict"] == 1 and result["compared"] == 3


def test_failed_and_missing_clusters_are_counted_not_crashing() -> None:
    result = compare(SET, [row("a", None), {"cluster": "b", "missing": True}], [row("a", "organic")])
    assert result["before"]["clusters"] == 1 and result["before"]["valid_reports"] == 0
    assert result["compared"] == 0
