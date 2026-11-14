"""Metrics from human reviews: precision and false-positive rate, with and without Nemotron.

Unit: cluster. Ground truth: the latest human decision per cluster (confirm = coordinated
inauthentic, false_alarm = not). "Unsure" decisions are counted but left out of the ratios.
FactsLens alone flags a cluster when its tier was `suspicious` at review time; "with Nemotron"
flags it only when Nemotron's verdict was also `staged`.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Ratio:
    hits: int
    n: int

    @property
    def value(self) -> float | None:
        return self.hits / self.n if self.n else None

    def as_dict(self) -> dict[str, Any]:
        return {"hits": self.hits, "n": self.n, "value": None if self.value is None else round(self.value, 3)}


def review_summary(rows: Iterable[tuple[str | None, str, str | None, int]]) -> dict[str, Any]:
    """rows: (tier_at, decision, verdict_at, count), one row per combination."""
    counts: list[tuple[str, str, str, int]] = [
        (tier or "pending", decision, verdict or "none", int(n)) for tier, decision, verdict, n in rows
    ]

    def total(predicate: Any) -> int:
        return sum(n for tier, decision, verdict, n in counts if predicate(tier, decision, verdict))

    def flagged_alone(tier: str, verdict: str) -> bool:
        return tier == "suspicious"

    def flagged_with_judge(tier: str, verdict: str) -> bool:
        return tier == "suspicious" and verdict == "staged"

    result: dict[str, Any] = {
        "reviewed": total(lambda t, d, v: True),
        "by_decision": {
            d: total(lambda t, dd, v, d=d: dd == d) for d in ("confirm", "false_alarm", "unsure")
        },
        "by_tier": {},
    }
    for tier in sorted({t for t, *_ in counts}):
        result["by_tier"][tier] = {
            d: total(lambda t, dd, v, tier=tier, d=d: t == tier and dd == d)
            for d in ("confirm", "false_alarm", "unsure")
        }
    for name, flagged in (("factslens", flagged_alone), ("factslens_with_nemotron", flagged_with_judge)):
        confirmed_flagged = total(lambda t, d, v, f=flagged: d == "confirm" and f(t, v))
        false_flagged = total(lambda t, d, v, f=flagged: d == "false_alarm" and f(t, v))
        all_false = total(lambda t, d, v: d == "false_alarm")
        all_confirmed = total(lambda t, d, v: d == "confirm")
        result[name] = {
            "precision": Ratio(confirmed_flagged, confirmed_flagged + false_flagged).as_dict(),
            "false_positive_rate": Ratio(false_flagged, all_false).as_dict(),
            "recall_on_confirmed": Ratio(confirmed_flagged, all_confirmed).as_dict(),
        }
    agree = total(
        lambda t, d, v: (d == "confirm" and v == "staged") or (d == "false_alarm" and v == "organic")
    )
    decided = total(lambda t, d, v: d in ("confirm", "false_alarm") and v in ("staged", "organic"))
    abstained = total(lambda t, d, v: d in ("confirm", "false_alarm") and v == "insufficient_evidence")
    result["nemotron_agreement"] = Ratio(agree, decided).as_dict() | {"abstained": abstained}
    return result
