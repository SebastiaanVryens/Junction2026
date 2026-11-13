# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "httpx>=0.27",
#   "websockets>=13",
#   "python-dotenv>=1.0",
#   "kafka-python>=2.1",
#   "psycopg[binary]>=3.2",
# ]
# ///
"""M0 access check: verifies every external integration FactsLens depends on.

Usage (from the repo root):
    uv run infra/check_access.py                    # all checks
    uv run infra/check_access.py nvidia kafka       # selected checks

Reads configuration from .env. A check whose variables are
empty is reported as SKIP, not FAIL. Everything is read-only except the Kafka
check, which writes one test message to KAFKA_CHECK_TOPIC. Secrets are never
printed.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import socket
import struct
import subprocess
import sys
import time
import uuid
import zlib
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
HTTP_TIMEOUT = httpx.Timeout(30.0)
CHECK_TIMEOUT_S = 150.0
DETAIL_MAX = 300

NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
HOSTINGER_API_URL = "https://developers.hostinger.com"
APPVIEW_URL = "https://public.api.bsky.app/xrpc"
JETSTREAM_DEFAULT = "wss://jetstream2.us-east.bsky.network/subscribe"
JETSTREAM_COLLECTIONS = (
    "app.bsky.feed.post",
    "app.bsky.feed.repost",
    "app.bsky.feed.like",
    "app.bsky.graph.follow",
)


class Status(StrEnum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"
    SKIP = "SKIP"


@dataclass(frozen=True)
class Result:
    status: Status
    detail: str


class Skip(Exception):
    """Raised when a check's configuration is missing."""


def env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise Skip(f"{name} not set")
    return value


def required(name: str) -> str:
    """Like env(), but for a check that is already partly configured: missing means FAIL."""
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"{name} not set")
    return value


def env_path(name: str) -> str:
    path = Path(required(name))
    if not path.is_absolute():
        path = ROOT / path
    if not path.is_file():
        raise FileNotFoundError(f"{name}: file not found at {path}")
    return str(path)


# --------------------------------------------------------------------------- Bluesky


async def check_jetstream() -> Result:
    """Sample the firehose to measure per-collection rates, then test cursor replay."""
    from websockets.asyncio.client import connect

    base = os.environ.get("JETSTREAM_URL", "").strip() or JETSTREAM_DEFAULT
    seconds = float(os.environ.get("JETSTREAM_SAMPLE_SECONDS", "").strip() or 10)
    query = "&".join(f"wantedCollections={c}" for c in JETSTREAM_COLLECTIONS)

    counts: Counter[str] = Counter()
    async with connect(f"{base}?{query}", open_timeout=15, max_size=2**22) as ws:
        deadline = time.monotonic() + seconds
        while (remaining := deadline - time.monotonic()) > 0:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
            except TimeoutError:
                break
            event = json.loads(raw)
            commit = event.get("commit") or {}
            if event.get("kind") == "commit" and commit.get("operation") == "create":
                counts[commit.get("collection", "?")] += 1

    if not counts:
        return Result(Status.FAIL, f"connected but no create events in {seconds:.0f}s")
    rates = ", ".join(
        f"{c.rsplit('.', 1)[-1]} {counts[c] / seconds:.0f}/s" for c in JETSTREAM_COLLECTIONS
    )

    # Cursor resume: reconnect 60 s in the past; replayed events should be ~60 s old.
    cursor = int((time.time() - 60) * 1_000_000)
    async with connect(f"{base}?{query}&cursor={cursor}", open_timeout=15, max_size=2**22) as ws:
        first = json.loads(await asyncio.wait_for(ws.recv(), timeout=15))
    age_s = time.time() - first.get("time_us", 0) / 1_000_000
    if age_s < 30:
        return Result(Status.WARN, f"{rates}; cursor replay NOT confirmed (first event {age_s:.0f}s old)")
    return Result(Status.PASS, f"{rates}; cursor replay ok (first event {age_s:.0f}s old)")


async def check_appview() -> Result:
    """Public AppView endpoints the simulator and backfill rely on (no auth)."""
    endpoints = {
        "getProfile": ("app.bsky.actor.getProfile", {"actor": "bsky.app"}),
        "getFollowers": ("app.bsky.graph.getFollowers", {"actor": "bsky.app", "limit": 5}),
        "getAuthorFeed": ("app.bsky.feed.getAuthorFeed", {"actor": "bsky.app", "limit": 5}),
    }
    ok: list[str] = []
    rate_limit = ""
    async with httpx.AsyncClient(base_url=APPVIEW_URL, timeout=HTTP_TIMEOUT) as client:
        for label, (method, params) in endpoints.items():
            response = await client.get(f"/{method}", params=params)
            if response.status_code != 200:
                return Result(Status.FAIL, f"{label}: HTTP {response.status_code}")
            ok.append(label)
            if limit := response.headers.get("ratelimit-limit"):
                rate_limit = f"; ratelimit-limit={limit}"
    return Result(Status.PASS, f"{', '.join(ok)} ok{rate_limit}")


# --------------------------------------------------------------------------- NVIDIA


def nvidia_keys() -> list[str]:
    keys = [k.strip() for k in env("NVIDIA_API_KEYS").split(",") if k.strip()]
    if not keys:
        raise Skip("NVIDIA_API_KEYS is empty")
    return keys


async def resolve_nemotron_model(client: httpx.AsyncClient, key: str) -> str:
    """Return NEMOTRON_MODEL if set and listed, else the single Super VL ID in the catalog."""
    response = await client.get("/models", headers={"Authorization": f"Bearer {key}"})
    response.raise_for_status()
    ids = {m["id"] for m in response.json().get("data", [])}
    configured = os.environ.get("NEMOTRON_MODEL", "").strip()
    if configured:
        if configured not in ids:
            raise LookupError(f"NEMOTRON_MODEL={configured} is not in the NVIDIA Build catalog")
        return configured
    candidates = sorted(i for i in ids if "super-vl" in i)
    if len(candidates) != 1:
        raise LookupError(f"set NEMOTRON_MODEL explicitly; Super VL candidates: {candidates}")
    return candidates[0]


def message_text(body: dict[str, Any]) -> str:
    message = body["choices"][0]["message"]
    return (message.get("content") or "").strip()


async def check_nvidia() -> Result:
    """Every teammate key can call Super VL (text-only, tiny request)."""
    keys = nvidia_keys()
    async with httpx.AsyncClient(base_url=NVIDIA_BASE_URL, timeout=httpx.Timeout(120.0)) as client:
        model = await resolve_nemotron_model(client, keys[0])
        lines: list[str] = []
        failed = 0
        for n, key in enumerate(keys, start=1):
            started = time.perf_counter()
            response = await client.post(
                "/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": "Reply with exactly one word: ready"}],
                    "max_tokens": 256,
                    "temperature": 0,
                },
            )
            ms = (time.perf_counter() - started) * 1000
            if response.status_code == 200:
                tokens = response.json().get("usage", {}).get("total_tokens", "?")
                lines.append(f"key{n} ok {ms:.0f}ms {tokens}tok")
            else:
                failed += 1
                lines.append(f"key{n} HTTP {response.status_code}")
    status = Status.PASS if failed == 0 else (Status.FAIL if failed == len(keys) else Status.WARN)
    return Result(status, f"{model}: {'; '.join(lines)}")


def solid_png(rgb: tuple[int, int, int], size: int = 64) -> bytes:
    """A minimal solid-colour PNG, built without image libraries."""

    def chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    header = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    pixels = (b"\x00" + bytes(rgb) * size) * size
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(pixels))
        + chunk(b"IEND", b"")
    )


async def check_nvidia_vision() -> Result:
    """Super VL accepts an image (OpenAI-style image_url data URL) and reads it correctly."""
    key = nvidia_keys()[0]
    image = base64.b64encode(solid_png((220, 20, 20))).decode()
    async with httpx.AsyncClient(base_url=NVIDIA_BASE_URL, timeout=httpx.Timeout(120.0)) as client:
        model = await resolve_nemotron_model(client, key)
        response = await client.post(
            "/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={
                "model": model,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "What colour fills this image? Answer with one word."},
                            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image}"}},
                        ],
                    }
                ],
                "max_tokens": 512,
                "temperature": 0,
            },
        )
    if response.status_code != 200:
        return Result(Status.FAIL, f"HTTP {response.status_code}: {response.text}")
    answer = message_text(response.json())
    if "red" in answer.lower():
        return Result(Status.PASS, f"image understood (answer: {answer[:40]!r})")
    return Result(Status.WARN, f"call ok but unexpected answer: {answer[:80]!r}")


# --------------------------------------------------------------------------- Aiven


def check_kafka() -> Result:
    """Authenticate, list topics, and round-trip one message on KAFKA_CHECK_TOPIC."""
    from kafka import KafkaConsumer, KafkaProducer, TopicPartition

    bootstrap = env("KAFKA_BOOTSTRAP")
    common: dict[str, Any] = {
        "bootstrap_servers": bootstrap,
        "ssl_cafile": env_path("KAFKA_CA_FILE"),
        "request_timeout_ms": 20_000,
    }
    if os.environ.get("KAFKA_SASL_USERNAME", "").strip():
        common |= {
            "security_protocol": "SASL_SSL",
            "sasl_mechanism": "SCRAM-SHA-256",
            "sasl_plain_username": required("KAFKA_SASL_USERNAME"),
            "sasl_plain_password": required("KAFKA_SASL_PASSWORD"),
        }
        mode = "SASL/SCRAM-SHA-256"
    elif os.environ.get("KAFKA_CERT_FILE", "").strip():
        common |= {
            "security_protocol": "SSL",
            "ssl_certfile": env_path("KAFKA_CERT_FILE"),
            "ssl_keyfile": env_path("KAFKA_KEY_FILE"),
        }
        mode = "client certificate"
    else:
        raise ValueError("set KAFKA_SASL_USERNAME/PASSWORD or KAFKA_CERT_FILE/KEY_FILE")

    topic = os.environ.get("KAFKA_CHECK_TOPIC", "").strip() or "access-check"
    consumer = KafkaConsumer(**common, enable_auto_commit=False, consumer_timeout_ms=15_000)
    try:
        topics = consumer.topics()
        if topic not in topics:
            return Result(
                Status.WARN,
                f"auth ok ({mode}), {len(topics)} topics; create topic '{topic}' to test produce/consume",
            )
        partitions = [TopicPartition(topic, p) for p in sorted(consumer.partitions_for_topic(topic) or [])]
        consumer.assign(partitions)
        consumer.seek_to_end(*partitions)
        for partition in partitions:
            consumer.position(partition)  # resolve offsets before producing

        marker = uuid.uuid4().hex.encode()
        producer = KafkaProducer(**common, acks="all")
        try:
            producer.send(topic, value=marker).get(timeout=20)
        finally:
            producer.close()

        for record in consumer:
            if record.value == marker:
                return Result(Status.PASS, f"{mode}: produced + consumed on '{topic}' ({len(topics)} topics)")
        return Result(Status.FAIL, f"{mode}: produced but not read back within 15s")
    finally:
        consumer.close()


def check_postgres() -> Result:
    """Connect over TLS, report server version and pgvector availability."""
    import psycopg

    with psycopg.connect(env("DATABASE_URL"), connect_timeout=15) as conn:
        version_row = conn.execute("SHOW server_version").fetchone()
        vector_row = conn.execute(
            "SELECT default_version, installed_version FROM pg_available_extensions WHERE name = 'vector'"
        ).fetchone()
        tls = "TLS" if conn.pgconn.ssl_in_use else "NO TLS"
    version = version_row[0] if version_row else "?"
    if vector_row is None:
        pgvector = "pgvector NOT available"
    elif vector_row[1]:
        pgvector = f"pgvector {vector_row[1]} enabled"
    else:
        pgvector = f"pgvector {vector_row[0]} available (not yet enabled)"
    status = Status.PASS if tls == "TLS" else Status.WARN
    return Result(status, f"PostgreSQL {version}, {tls}, {pgvector}")


# --------------------------------------------------------------------------- Hostinger


async def check_hostinger_api() -> Result:
    """Token is valid and the team VPS is visible (GET /api/vps/v1/virtual-machines)."""
    token = env("HOSTINGER_API_TOKEN")
    async with httpx.AsyncClient(base_url=HOSTINGER_API_URL, timeout=HTTP_TIMEOUT) as client:
        response = await client.get(
            "/api/vps/v1/virtual-machines", headers={"Authorization": f"Bearer {token}"}
        )
    if response.status_code != 200:
        return Result(Status.FAIL, f"HTTP {response.status_code}: {response.text}")
    vms: list[dict[str, Any]] = response.json()
    if not vms:
        return Result(Status.WARN, "token valid, but no VPS on this account")
    summaries = []
    for vm in vms:
        ips = ",".join(ip.get("address", "?") for ip in vm.get("ipv4") or [])
        template = (vm.get("template") or {}).get("name", "no OS")
        summaries.append(
            f"#{vm.get('id')} {vm.get('hostname')} [{vm.get('state')}] {ips} "
            f"{template} {vm.get('cpus')}cpu/{vm.get('memory')}MB"
        )
    return Result(Status.PASS, "; ".join(summaries))


def check_vps_ssh() -> Result:
    """Non-interactive SSH works and the VPS has Docker + Compose."""
    host = env("VPS_HOST")
    user = os.environ.get("VPS_SSH_USER", "").strip() or "root"
    command = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
    if key := os.environ.get("VPS_SSH_KEY", "").strip():
        command += ["-i", str(Path(key).expanduser())]
    remote = (
        "echo docker=$(docker version --format '{{.Server.Version}}' 2>/dev/null || echo missing); "
        "echo compose=$(docker compose version --short 2>/dev/null || echo missing); "
        "echo cpus=$(nproc); "
        "free -m | awk '/^Mem:/ {print \"mem_total_mb=\" $2; print \"mem_avail_mb=\" $7}'"
    )
    proc = subprocess.run(
        [*command, f"{user}@{host}", remote], capture_output=True, text=True, timeout=45
    )
    if proc.returncode != 0:
        hint = " (first time? ssh in once by hand to accept the host key)" if "Host key" in proc.stderr else ""
        return Result(Status.FAIL, f"ssh exit {proc.returncode}: {proc.stderr.strip()}{hint}")
    facts = dict(line.split("=", 1) for line in proc.stdout.splitlines() if "=" in line)
    detail = (
        f"{user}@{host}: docker {facts.get('docker')}, compose {facts.get('compose')}, "
        f"{facts.get('cpus')} cpu, {facts.get('mem_avail_mb')}/{facts.get('mem_total_mb')} MB free"
    )
    missing = facts.get("docker") == "missing" or facts.get("compose") == "missing"
    return Result(Status.WARN if missing else Status.PASS, detail)


async def check_public_url() -> Result:
    """The domain resolves (ideally to the VPS) and answers over HTTP(S)."""
    url = env("PUBLIC_URL")
    host = httpx.URL(url).host
    try:
        resolved = await asyncio.to_thread(socket.gethostbyname, host)
    except OSError as error:
        return Result(Status.FAIL, f"DNS: {host} does not resolve ({error})")

    dns_note = ""
    if vps := os.environ.get("VPS_HOST", "").strip():
        vps_ip = await asyncio.to_thread(socket.gethostbyname, vps)
        dns_note = " (= VPS)" if vps_ip == resolved else f" (VPS is {vps_ip}: DNS not pointed yet?)"

    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, follow_redirects=True) as client:
            response = await client.get(url)
    except httpx.HTTPError as error:
        return Result(Status.WARN, f"{host} -> {resolved}{dns_note}; HTTP unreachable ({type(error).__name__})")
    status = Status.PASS if response.status_code < 400 and "DNS not" not in dns_note else Status.WARN
    return Result(status, f"{host} -> {resolved}{dns_note}; HTTP {response.status_code}")


# --------------------------------------------------------------------------- runner


CHECKS: dict[str, Callable[[], Awaitable[Result]]] = {
    "jetstream": check_jetstream,
    "appview": check_appview,
    "nvidia": check_nvidia,
    "nvidia-vision": check_nvidia_vision,
    "kafka": lambda: asyncio.to_thread(check_kafka),
    "postgres": lambda: asyncio.to_thread(check_postgres),
    "hostinger-api": check_hostinger_api,
    "vps-ssh": lambda: asyncio.to_thread(check_vps_ssh),
    "public-url": check_public_url,
}


async def run_check(check: Callable[[], Awaitable[Result]]) -> Result:
    try:
        return await asyncio.wait_for(check(), timeout=CHECK_TIMEOUT_S)
    except Skip as skip:
        return Result(Status.SKIP, str(skip))
    except TimeoutError:
        return Result(Status.FAIL, f"timed out after {CHECK_TIMEOUT_S:.0f}s")
    except Exception as error:  # noqa: BLE001 - report any failure, keep checking the rest
        return Result(Status.FAIL, f"{type(error).__name__}: {error}")


async def run_all(names: list[str]) -> dict[str, Result]:
    results = await asyncio.gather(*(run_check(CHECKS[name]) for name in names))
    return dict(zip(names, results, strict=True))


def main() -> int:
    parser = argparse.ArgumentParser(description="Check FactsLens's external integrations.")
    parser.add_argument("checks", nargs="*", metavar="CHECK", help=f"subset of: {', '.join(CHECKS)}")
    args = parser.parse_args()
    unknown = [name for name in args.checks if name not in CHECKS]
    if unknown:
        parser.error(f"unknown check(s): {', '.join(unknown)}; choose from {', '.join(CHECKS)}")
    names = args.checks or list(CHECKS)

    load_dotenv(ROOT / ".env")
    results = asyncio.run(run_all(names))

    width = max(len(name) for name in names)
    for name, result in results.items():
        detail = " ".join(result.detail.split())
        if len(detail) > DETAIL_MAX:
            detail = detail[: DETAIL_MAX - 3] + "..."
        print(f"{name:<{width}}  {result.status:<4}  {detail}")
    tally = Counter(result.status for result in results.values())
    print("\n" + "  ".join(f"{status}={tally.get(status, 0)}" for status in Status))
    return 1 if tally.get(Status.FAIL) else 0


if __name__ == "__main__":
    sys.exit(main())
