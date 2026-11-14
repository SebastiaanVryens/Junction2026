"""Score investigator versions run on the same fixed set of clusters (eval/report_quality/).

    python -m factslens.eval.report_quality --set eval/report_quality/set-v1.json \\
        --before data/report_quality/v3.jsonl --after data/report_quality/v5.jsonl \\
        --out eval/results/report-quality-v3-v5.json

Unit: one investigation of one cluster. Every number is computed the same way for both versions;
the output holds aggregates and per-cluster verdicts only (no post text), so it can be committed.
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter
from pathlib import Path
from typing import Any


def _report_calls(row: dict[str, Any]) -> int:
    return sum(c.get("purpose") == "investigate:report" for c in row.get("calls") or [])


def summarise(rows: list[dict[str, Any]], controls: set[str], documented: set[str]) -> dict[str, Any]:
    present = [r for r in rows if not r.get("missing")]
    ok = [r for r in present if r.get("ok")]
    grounded = [r for r in ok if r.get("grounding")]
    items = sum(r["grounding"]["items"] for r in grounded)
    kept = sum(r["grounding"]["grounded"] for r in grounded)
    verdicts = {r["cluster"]: r["report"]["verdict"] for r in ok}
    tools = Counter(name for r in present for name in r.get("tool_calls") or [])
    news = Counter(n["finding"] for r in ok for n in r["report"].get("news_checks") or [])

    def mean(values: list[float]) -> float | None:
        return round(statistics.fmean(values), 2) if values else None

    return {
        "prompt_version": present[0]["prompt_version"] if present else None,
        "clusters": len(present),
        "valid_reports": len(ok),
        "valid_on_first_try": sum(_report_calls(r) == 1 for r in ok),
        "calls_cut_off_at_token_limit": sum(
            any(c.get("finish") == "length" for c in r.get("calls") or []) for r in present
        ),
        "items_checked": items,
        "items_removed_as_ungrounded": items - kept,
        "grounded_share_mean": mean([r["grounding"]["share"] for r in grounded]),
        "verdicts": dict(Counter(verdicts.values())),
        "organic_controls_called_staged": sum(verdicts.get(c) == "staged" for c in controls),
        "organic_controls": len(controls),
        "documented_cases_called_staged": sum(verdicts.get(c) == "staged" for c in documented),
        "documented_cases": len(documented),
        "techniques_per_report": mean([len(r["report"]["techniques"]) for r in ok]),
        "media_shown_per_cluster": mean([r.get("media_shown", 0) for r in present]),
        "media_findings_per_report": mean([len(r["report"]["media"]) for r in ok]),
        "news_checks_per_report": mean([len(r["report"].get("news_checks") or []) for r in ok]),
        "news_findings": dict(news),
        "tool_calls": dict(tools),
        "model_calls_per_cluster": mean([len(r.get("calls") or []) for r in present]),
        "seconds_median": statistics.median([r["seconds"] for r in present]) if present else None,
        "prompt_tokens_mean": mean([r.get("prompt_tokens", 0) for r in present]),
        "completion_tokens_mean": mean([r.get("completion_tokens", 0) for r in present]),
        "per_cluster_verdict": verdicts,
    }


def compare(
    cluster_set: dict[str, Any], before: list[dict[str, Any]], after: list[dict[str, Any]]
) -> dict[str, Any]:
    controls = {c["id"] for c in cluster_set["clusters"] if c["why"].startswith("organic control")}
    documented = {c["id"] for c in cluster_set["clusters"] if "IFTAS" in c["why"] or "RT, TASS" in c["why"]}
    old, new = summarise(before, controls, documented), summarise(after, controls, documented)
    both = set(old["per_cluster_verdict"]) & set(new["per_cluster_verdict"])
    return {
        "set": cluster_set["name"],
        "unit": "one investigation of one cluster; one run per version",
        "before": old,
        "after": new,
        "same_verdict": sum(old["per_cluster_verdict"][c] == new["per_cluster_verdict"][c] for c in both),
        "compared": len(both),
    }


def _read(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare investigator versions on a fixed cluster set.")
    parser.add_argument("--set", type=Path, required=True)
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--after", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = compare(json.loads(args.set.read_text(encoding="utf-8")), _read(args.before), _read(args.after))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k not in ("before", "after")}))
    for side in ("before", "after"):
        print(side, json.dumps({k: v for k, v in result[side].items() if k != "per_cluster_verdict"}))


if __name__ == "__main__":
    main()
