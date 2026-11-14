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
from factslens.investigator.agent import PROMPT_VERSION, InvestigationResult, Investigator
from factslens.investigator.evidence import EvidenceGatherer
from factslens.investigator.llm import BudgetExhausted, CallRecord, ChatClient

log = logging.getLogger("factslens.investigator")

SERVICE = "investigator"
IDLE_SECONDS = 15.0
BETWEEN_INVESTIGATIONS_SECONDS = 5.0
BUDGET_WAIT_SECONDS = 300.0

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
    result = agent.investigate(cluster_id, pack)
    save(connection, cluster, agent.chat.model, result, pack.summary)
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


def main() -> None:
    parser = argparse.ArgumentParser(description="FactsLens investigator (Nemotron)")
    parser.add_argument("--once", metavar="CLUSTER_ID", help="investigate one cluster and exit")
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

        counters: dict[str, Any] = {"investigations": 0, "failed": 0, "budget_waits": 0, "last_verdict": None}
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
