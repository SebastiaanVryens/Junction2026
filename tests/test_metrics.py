from __future__ import annotations

from factslens.api.metrics import review_summary


def test_precision_and_false_positive_rate_with_and_without_the_judge() -> None:
    rows = [
        # (FactsLens tier at review, human decision, Nemotron verdict, clusters)
        ("suspicious", "confirm", "staged", 6),
        ("suspicious", "confirm", "insufficient_evidence", 2),
        ("suspicious", "false_alarm", "staged", 1),
        ("suspicious", "false_alarm", "organic", 3),  # the judge catches these
        ("likely_organic", "false_alarm", "organic", 10),
        ("watch", "confirm", "staged", 2),
        ("suspicious", "unsure", "staged", 4),
    ]
    s = review_summary(rows)
    assert s["reviewed"] == 28 and s["by_decision"] == {"confirm": 10, "false_alarm": 14, "unsure": 4}
    alone, judged = s["factslens"], s["factslens_with_nemotron"]
    assert alone["precision"] == {"hits": 8, "n": 12, "value": 0.667}
    assert alone["false_positive_rate"] == {"hits": 4, "n": 14, "value": 0.286}
    assert judged["precision"] == {"hits": 6, "n": 7, "value": 0.857}
    assert judged["false_positive_rate"] == {"hits": 1, "n": 14, "value": 0.071}
    assert judged["recall_on_confirmed"] == {"hits": 6, "n": 10, "value": 0.6}
    # Nemotron agrees on staged+confirm (8) and organic+false_alarm (13) of 22 decided; abstained on 2.
    assert s["nemotron_agreement"] == {"hits": 21, "n": 22, "value": 0.955, "abstained": 2}
    assert s["by_tier"]["suspicious"] == {"confirm": 8, "false_alarm": 4, "unsure": 4}


def test_no_reviews_gives_empty_ratios_not_errors() -> None:
    s = review_summary([])
    assert s["reviewed"] == 0 and s["factslens"]["precision"] == {"hits": 0, "n": 0, "value": None}
