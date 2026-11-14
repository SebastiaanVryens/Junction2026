"""Run the investigator of the checkout this is started in on a fixed set of clusters.

    uv run python eval/report_quality/run_version.py eval/report_quality/set-v1.json OUT.jsonl

Used to compare prompt/pipeline versions on the same clusters: run it once in the current
checkout and once in a git worktree of an older commit (it only uses interfaces that exist in
both). Nothing is written to the database; one JSON line per cluster goes to the output file
(git-ignored, because reports quote post texts). Score the files with
`python -m factslens.eval.report_quality`.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

from factslens.common import db
from factslens.common.settings import get_settings
from factslens.enrich.lists import KnownLists
from factslens.investigator.__main__ import load_inputs
from factslens.investigator.agent import PROMPT_VERSION, Investigator
from factslens.investigator.evidence import EvidenceGatherer
from factslens.investigator.llm import ChatClient


def main(set_file: Path, out_file: Path) -> None:
    ids = [c["id"] for c in json.loads(set_file.read_text(encoding="utf-8"))["clusters"]]
    settings = get_settings()
    chat = ChatClient.from_settings(settings)
    calls: list[dict[str, Any]] = []
    original = chat.chat

    def recorded(messages: list[dict[str, Any]], **kwargs: Any) -> Any:
        response = original(messages, **kwargs)
        calls.append({"purpose": kwargs.get("purpose"), "finish": response.finish_reason})
        return response

    chat.chat = recorded  # type: ignore[method-assign]
    agent = Investigator(chat, EvidenceGatherer(), KnownLists.load(settings.ground_truth_dir))
    out_file.parent.mkdir(parents=True, exist_ok=True)
    with (
        psycopg.connect(db.database_url(settings), row_factory=dict_row) as connection,  # type: ignore[arg-type]
        out_file.open("w", encoding="utf-8") as out,
    ):
        for cluster_id in ids:
            cluster, posts, accounts = load_inputs(connection, cluster_id)
            if cluster is None:
                out.write(json.dumps({"cluster": cluster_id, "missing": True}) + "\n")
                continue
            pack = agent.build_pack(cluster, posts, accounts)
            calls.clear()
            started = time.time()
            result = agent.investigate(cluster_id, pack, [])
            report = result.report.model_dump() if result.report else None
            grounding = result.grounding
            row = {
                "cluster": cluster_id,
                "prompt_version": PROMPT_VERSION,
                "ok": report is not None,
                "error": result.error,
                "seconds": round(time.time() - started, 1),
                "calls": [dict(c) for c in calls],
                "tool_calls": result.tool_calls,
                "prompt_tokens": result.prompt_tokens,
                "completion_tokens": result.completion_tokens,
                "media_shown": sum(a.get("type") in ("image_url", "video_url") for a in pack.attachments),
                "grounding": None
                if grounding is None
                else {
                    "share": grounding.share,
                    "items": grounding.claims + grounding.techniques + grounding.media,
                    "grounded": grounding.claims_grounded
                    + grounding.techniques_grounded
                    + grounding.media_grounded,
                    "problems": grounding.problems,
                },
                "report": report,
            }
            out.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
            out.flush()
            verdict = report["verdict"] if report else None
            print(f"{cluster_id}: {verdict} in {row['seconds']} s, {len(calls)} calls", flush=True)


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
