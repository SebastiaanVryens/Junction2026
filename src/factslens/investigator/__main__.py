"""Investigator service (Nemotron jobs 1 and 2): investigate the most suspicious clusters.

    python -m factslens.investigator [--once CLUSTER_ID]

Picks clusters rated suspicious or watch, highest score first; re-investigates a cluster only
when it grew by half since the last report. Stays inside the call budget in settings
(NEMOTRON_MAX_CALLS_PER_MINUTE / _PER_HOUR); when the hourly budget is used up it waits.
"""

from __future__ import annotations

import argparse
import logging
import time
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from factslens.common import db
from factslens.common.logging import configure_logging
from factslens.common.settings import get_settings
from factslens.enrich.lists import KnownLists
from factslens.investigator import campaigns
from factslens.investigator.agent import PROMPT_VERSION, InvestigationResult, Investigator
from factslens.investigator.evidence import EvidenceGatherer
from factslens.investigator.llm import BudgetExhausted, CallRecord, ChatClient

log = logging.getLogger("factslens.investigator")

SERVICE = "investigator"
IDLE_SECONDS = 15.0
BETWEEN_INVESTIGATIONS_SECONDS = 5.0
BUDGET_WAIT_SECONDS = 300.0
CAMPAIGN_EVERY_SECONDS = 900.0  # one campaign report per round at most
CAMPAIGN_FIRST_AFTER_SECONDS = 60.0

CAMPAIGN_CANDIDATES = """
SELECT id, coalesce(score, 0) AS score FROM clusters
WHERE tier IN ('suspicious', 'watch') AND last_seen > now() - interval '24 hours'
"""

CAMPAIGN_LINKS = """
SELECT m.cluster_id AS a, o.cluster_id AS b, count(*) AS n
FROM cluster_members m
JOIN cluster_members o ON o.account_hash = m.account_hash AND o.cluster_id < m.cluster_id
WHERE m.cluster_id = ANY(%(ids)s) AND o.cluster_id = ANY(%(ids)s)
GROUP BY 1, 2 HAVING count(*) >= 2
"""

LAST_CAMPAIGN = """
SELECT status, cardinality(cluster_ids) AS clusters, created_at < now() - interval '20 minutes' AS stale
FROM campaigns WHERE campaign_key = %s ORDER BY created_at DESC LIMIT 1
"""

CAMPAIGN_MEMBERS = """
SELECT m.cluster_id, m.account_hash, a.handle, a.created_at, a.followers, a.follows, a.posts,
       a.description, a.status, coalesce(a.on_public_list, false) AS on_public_list
FROM cluster_members m LEFT JOIN accounts a ON a.account_hash = m.account_hash
WHERE m.cluster_id = ANY(%s)
"""

CAMPAIGN_FINDINGS = """
SELECT DISTINCT ON (cluster_id) cluster_id, report FROM investigations
WHERE cluster_id = ANY(%s) AND status = 'done' ORDER BY cluster_id, created_at DESC
"""

SAVE_CAMPAIGN = """
INSERT INTO campaigns (campaign_key, cluster_ids, accounts, status, title, kind, verdict, report, evidence,
                       model, prompt_version, calls, prompt_tokens, completion_tokens, latency_ms, error)
VALUES (%(key)s, %(cluster_ids)s, %(accounts)s, %(status)s, %(title)s, %(kind)s, %(verdict)s, %(report)s,
        %(evidence)s, %(model)s, %(prompt_version)s, %(calls)s, %(prompt_tokens)s, %(completion_tokens)s,
        %(latency_ms)s, %(error)s)
"""

type Connection = psycopg.Connection[dict[str, Any]]

NEXT_CLUSTER = """
SELECT c.* FROM clusters c
LEFT JOIN LATERAL (
    SELECT accounts_at, created_at, status FROM investigations i
    WHERE i.cluster_id = c.id ORDER BY created_at DESC LIMIT 1
) last ON true
WHERE c.tier IN ('suspicious', 'watch')
  AND c.last_seen > now() - interval '6 hours'
  AND (last.created_at IS NULL
       OR (last.status = 'failed' AND last.created_at < now() - interval '20 minutes')
       OR (c.accounts >= greatest(last.accounts_at * 1.5, last.accounts_at + 3)
           AND last.created_at < now() - interval '30 minutes'))
ORDER BY c.score DESC NULLS LAST, c.accounts DESC
LIMIT 1
"""

REVIEW_EXAMPLES = """
SELECT c.kind, c.accounts, c.label, r.decision, r.note, r.verdict_at, r.category
FROM reviews r JOIN clusters c ON c.id = r.cluster_id
WHERE r.decision IN ('confirm', 'false_alarm') AND r.cluster_id <> %s
ORDER BY r.created_at DESC
LIMIT 4
"""

LOG_CALL = """
INSERT INTO model_calls (purpose, cluster_id, provider, model, status, attempts, latency_ms,
                         prompt_tokens, completion_tokens, reasoning_tokens, error)
VALUES (%(purpose)s, %(cluster_id)s, %(provider)s, %(model)s, %(status)s, %(attempts)s, %(latency_ms)s,
        %(prompt_tokens)s, %(completion_tokens)s, %(reasoning_tokens)s, %(error)s)
"""

SAVE_INVESTIGATION = """
INSERT INTO investigations (cluster_id, accounts_at, model, prompt_version, status, verdict, report,
                            evidence, calls, prompt_tokens, completion_tokens, latency_ms, error)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""


def load_inputs(
    connection: Connection, cluster_id: str
) -> tuple[dict[str, Any] | None, list[Any], dict[str, Any]]:
    cluster = connection.execute("SELECT * FROM clusters WHERE id = %s", (cluster_id,)).fetchone()
    posts = connection.execute(
        "SELECT * FROM cluster_posts WHERE cluster_id = %s ORDER BY posted_at", (cluster_id,)
    ).fetchall()
    accounts = {
        row["account_hash"]: row
        for row in connection.execute(
            "SELECT a.* FROM cluster_members m JOIN accounts a USING (account_hash) "
            "WHERE m.cluster_id = %s ORDER BY m.first_seen",
            (cluster_id,),
        ).fetchall()
    }
    return cluster, posts, accounts


def save(
    connection: Connection,
    cluster: dict[str, Any],
    model: str,
    result: InvestigationResult,
    evidence: dict[str, Any],
) -> None:
    report = result.report
    grounding = result.grounding
    evidence = evidence | {
        "tool_calls": result.tool_calls,
        "grounding": None
        if grounding is None
        else {
            "share": round(grounding.share, 3),
            "claims": [grounding.claims_grounded, grounding.claims],
            "techniques": [grounding.techniques_grounded, grounding.techniques],
            "media": [grounding.media_grounded, grounding.media],
            "problems": grounding.problems[:10],
        },
    }
    connection.execute(
        SAVE_INVESTIGATION,
        (
            cluster["id"],
            cluster["accounts"],
            model,
            PROMPT_VERSION,
            "done" if report is not None else "failed",
            report.verdict if report is not None else None,
            Jsonb(report.model_dump()) if report is not None else None,
            Jsonb(evidence),
            result.calls,
            result.prompt_tokens,
            result.completion_tokens,
            result.latency_ms,
            result.error,
        ),
    )


def review_examples(connection: Connection, cluster_id: str) -> list[str]:
    """Human decisions on other clusters, phrased for the prompt (feedback loop)."""
    examples = []
    for row in connection.execute(REVIEW_EXAMPLES, (cluster_id,)).fetchall():
        kind = f", {row['category']}" if row.get("category") else ""
        human = (
            f"coordinated inauthentic (confirmed{kind})" if row["decision"] == "confirm" else "false alarm"
        )
        label = " ".join(row["label"].split())[:140]
        note = f'; reviewer note: "{row["note"][:160]}"' if row.get("note") else ""
        said = f"Nemotron had said {row['verdict_at']}; " if row.get("verdict_at") else ""
        examples.append(
            f"{row['kind']} cluster of {row['accounts']} accounts sharing {label!r}: "
            f"{said}human: {human}{note}"
        )
    return examples


def investigate_one(
    connection: Connection, agent: Investigator, cluster_id: str
) -> InvestigationResult | None:
    cluster, posts, accounts = load_inputs(connection, cluster_id)
    if cluster is None:
        return None
    pack = agent.build_pack(cluster, posts, accounts)
    log.info(
        "investigating",
        extra={"cluster": cluster_id, "refs": len(pack.refs), "media": len(pack.attachments) // 2},
    )
    examples = review_examples(connection, cluster_id)
    result = agent.investigate(cluster_id, pack, examples)
    save(connection, cluster, agent.chat.model, result, pack.summary | {"feedback_examples": len(examples)})
    log.info(
        "investigation stored",
        extra={
            "cluster": cluster_id,
            "verdict": result.report.verdict if result.report else None,
            "calls": result.calls,
            "latency_ms": result.latency_ms,
            "error": result.error,
        },
    )
    return result


def needs_report(last: dict[str, Any] | None, clusters: int) -> bool:
    """A campaign gets a report when it is new, when it grew by half since the last one, or when the
    last attempt failed more than 20 minutes ago."""
    if last is None:
        return True
    if last["status"] == "failed":
        return bool(last["stale"])
    return clusters >= max(float(last["clusters"]) * 1.5, float(last["clusters"]) + 2)


def campaign_round(connection: Connection, chat: ChatClient) -> int:
    """Find campaigns (clusters sharing accounts) and write a report for the first that needs one."""
    scores = {r["id"]: float(r["score"]) for r in connection.execute(CAMPAIGN_CANDIDATES).fetchall()}
    if len(scores) < 2:
        return 0
    links = [(r["a"], r["b"], int(r["n"])) for r in connection.execute(CAMPAIGN_LINKS, {"ids": list(scores)})]
    for group in campaigns.find_campaigns(scores, links)[:10]:
        key = campaigns.campaign_key(group)
        if not needs_report(connection.execute(LAST_CAMPAIGN, (key,)).fetchone(), len(group)):
            continue
        ids = group[: campaigns.MAX_CLUSTERS]
        clusters = sorted(
            connection.execute("SELECT * FROM clusters WHERE id = ANY(%s)", (ids,)).fetchall(),
            key=lambda c: ids.index(c["id"]),
        )
        members = connection.execute(CAMPAIGN_MEMBERS, (ids,)).fetchall()
        posts = connection.execute(
            "SELECT cluster_id, account_hash, posted_at, text FROM cluster_posts WHERE cluster_id = ANY(%s)",
            (ids,),
        ).fetchall()
        findings = {
            r["cluster_id"]: r["report"] for r in connection.execute(CAMPAIGN_FINDINGS, (ids,)) if r["report"]
        }
        pack = campaigns.build_pack(clusters, members, posts, findings)
        log.info(
            "campaign report", extra={"campaign": key, "clusters": len(ids), "accounts": len(pack.accounts)}
        )
        result = campaigns.investigate_campaign(chat, key, pack)
        report = result.report
        connection.execute(
            SAVE_CAMPAIGN,
            {
                "key": key,
                "cluster_ids": ids,
                "accounts": len({m["account_hash"] for m in members}),
                "status": "done" if report else "failed",
                "title": report.title if report else None,
                "kind": report.kind if report else None,
                "verdict": report.verdict if report else None,
                "report": Jsonb(report.model_dump()) if report else None,
                "evidence": Jsonb(campaigns.evidence_summary(pack, result)),
                "model": chat.model,
                "prompt_version": campaigns.PROMPT_VERSION,
                "calls": result.calls,
                "prompt_tokens": result.prompt_tokens,
                "completion_tokens": result.completion_tokens,
                "latency_ms": result.latency_ms,
                "error": result.error,
            },
        )
        log.info(
            "campaign report stored",
            extra={"campaign": key, "verdict": report.verdict if report else None, "error": result.error},
        )
        return 1
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="FactsLens investigator (Nemotron)")
    parser.add_argument("--once", metavar="CLUSTER_ID", help="investigate one cluster and exit")
    parser.add_argument("--campaign-once", action="store_true", help="write one campaign report and exit")
    args = parser.parse_args()
    settings = get_settings()

    with psycopg.connect(db.database_url(settings), autocommit=True, row_factory=dict_row) as connection:

        def log_call(record: CallRecord) -> None:
            connection.execute(LOG_CALL, record.__dict__)

        chat = ChatClient.from_settings(settings, logger=log_call)
        agent = Investigator(chat, EvidenceGatherer(), KnownLists.load(settings.ground_truth_dir))
        if args.once:
            investigate_one(connection, agent, args.once)
            return
        if args.campaign_once:
            campaign_round(connection, chat)
            return

        counters: dict[str, Any] = {
            "investigations": 0,
            "failed": 0,
            "budget_waits": 0,
            "last_verdict": None,
            "campaign_reports": 0,
        }
        next_campaign_at = time.monotonic() + CAMPAIGN_FIRST_AFTER_SECONDS
        log.info("investigator started", extra={"model": chat.model, "prompt_version": PROMPT_VERSION})
        try:
            while True:
                pause = IDLE_SECONDS
                try:
                    row = connection.execute(NEXT_CLUSTER).fetchone()
                    if row is not None:
                        result = investigate_one(connection, agent, row["id"])
                        if result is not None:
                            counters["investigations"] += 1
                            counters["failed"] += result.report is None
                            counters["last_verdict"] = result.report.verdict if result.report else None
                        pause = BETWEEN_INVESTIGATIONS_SECONDS
                    if time.monotonic() >= next_campaign_at:
                        next_campaign_at = time.monotonic() + CAMPAIGN_EVERY_SECONDS
                        counters["campaign_reports"] += campaign_round(connection, chat)
                except BudgetExhausted as exhausted:
                    counters["budget_waits"] += 1
                    log.warning("call budget used up; waiting", extra={"detail": str(exhausted)})
                    pause = BUDGET_WAIT_SECONDS
                except psycopg.Error as error:
                    log.warning("database error", extra={"error": str(error)[:300]})
                connection.execute(
                    """
                    INSERT INTO service_status (service, updated_at, data) VALUES (%s, now(), %s)
                    ON CONFLICT (service) DO UPDATE SET updated_at = now(), data = EXCLUDED.data
                    """,
                    (
                        SERVICE,
                        Jsonb(
                            counters
                            | {
                                "calls_last_hour": chat.budget.used_last_hour(),
                                "budget_per_hour": chat.budget.per_hour,
                                "model": chat.model,
                                "prompt_version": PROMPT_VERSION,
                            }
                        ),
                    ),
                )
                time.sleep(pause)
        except KeyboardInterrupt:
            log.info("stopped by user")


if __name__ == "__main__":
    configure_logging()
    main()
