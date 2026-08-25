#!/usr/bin/env python
"""VPS feature-check harness (E2 Phase 4) — one command, exercise + report.

A live smoke of the AI-facing surfaces, meant to be run against the running
gateway (locally or over SSH on the VPS) to answer "is chat + each AI app
actually working right now, and if not, where?". For every check it drives the
real endpoint, waits for the run to settle, and — using the E2 run-trace store —
reports the run_id + status so a failure is immediately debuggable via
``GET /debug/runs/{run_id}`` or ``journalctl | grep <run_id>``.

This is a thin, dependency-light complement to the exhaustive
``tests/integration/test_chat_features.py`` (which pytest-drives the same
gateway): that suite is for CI depth; this is for a fast operator "is it up?"
sweep with a human-readable pass/fail table.

Usage (on the VPS):
    cd /opt/acb/app && uv run python scripts/feature_check.py
    uv run python scripts/feature_check.py --json          # machine-readable
    uv run python scripts/feature_check.py --only chat_maf # one check

Env (shared with test_chat_features.py):
    CC_GATEWAY_URL   (default http://127.0.0.1:8080)
    CC_AUTH_TOKEN    (default sk-local-dev-change-me)
    CC_TEST_MODEL    (default tier-fast)
    CC_MAF_AGENT     (default task-manager)
    CC_COPILOT_AGENT (default agent-project-manager)
    CC_USER_EMAIL    (default feature-check@fracktal.in) + CC_USER_ROLE (executive)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx

GATEWAY_URL = os.environ.get("CC_GATEWAY_URL", "http://127.0.0.1:8080")
AUTH_TOKEN = os.environ.get("CC_AUTH_TOKEN", "sk-local-dev-change-me")
TEST_MODEL = os.environ.get("CC_TEST_MODEL", "tier-fast")
MAF_AGENT = os.environ.get("CC_MAF_AGENT", "task-manager")
COPILOT_AGENT = os.environ.get("CC_COPILOT_AGENT", "agent-project-manager")
USER_EMAIL = os.environ.get("CC_USER_EMAIL", "feature-check@fracktal.in")
USER_ROLE = os.environ.get("CC_USER_ROLE", "executive")
STREAM_TIMEOUT = int(os.environ.get("CC_STREAM_TIMEOUT", "120"))


def _headers() -> dict[str, str]:
    # Bearer + identity headers → the gateway resolves a real (executive) user,
    # which the /debug routes require. Mirrors the Next.js proxy's forwarding.
    return {
        "Authorization": f"Bearer {AUTH_TOKEN}",
        "X-User-Email": USER_EMAIL,
        "X-User-Role": USER_ROLE,
    }


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str = ""
    run_id: str | None = None
    run_status: str | None = None
    duration_ms: int | None = None
    events: int = 0
    extra: dict[str, Any] = field(default_factory=dict)


async def _run_stream(
    agent: str, message: str, *, model: str | None = None,
) -> tuple[list[dict[str, Any]], str]:
    """Drive /agent/run/stream, return (events, run_id). Terminal on RUN_*."""
    run_id = str(uuid.uuid4())
    payload: dict[str, Any] = {
        "agent": agent,
        "payload": {"message": message, "user_email": USER_EMAIL},
        "run_id": run_id,
        "thread_id": f"featcheck-{agent}:{run_id[:8]}",
    }
    if model:
        payload["model"] = model
    events: list[dict[str, Any]] = []
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(STREAM_TIMEOUT, connect=15)
    ) as client, client.stream(
        "POST", f"{GATEWAY_URL}/agent/run/stream",
        json=payload, headers=_headers(),
    ) as resp:
        if resp.status_code != 200:
            body = (await resp.aread()).decode(errors="replace")[:400]
            events.append({"_http_error": resp.status_code, "_body": body})
            return events, run_id
        buf = ""
        async for chunk in resp.aiter_text():
            buf += chunk
            while "\n\n" in buf:
                line, buf = buf.split("\n\n", 1)
                if line.startswith("data: "):
                    try:
                        ev = json.loads(line[6:])
                    except json.JSONDecodeError:
                        continue
                    events.append(ev)
                    if ev.get("type") in ("RUN_FINISHED", "RUN_ERROR"):
                        return events, run_id
    return events, run_id


def _event_types(events: list[dict[str, Any]]) -> set[str]:
    return {str(e.get("type") or "") for e in events}


# ── Individual checks ────────────────────────────────────────────────────────


async def check_health(_: argparse.Namespace) -> CheckResult:
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(f"{GATEWAY_URL}/health", headers=_headers())
        ok = r.status_code == 200
        return CheckResult("health", ok, detail=f"HTTP {r.status_code}")
    except Exception as exc:
        return CheckResult("health", False, detail=f"unreachable: {exc}")


async def _chat_check(name: str, agent: str) -> CheckResult:
    try:
        t0 = time.monotonic()
        events, run_id = await _run_stream(
            agent, "Reply with exactly the word: pong.", model=TEST_MODEL,
        )
        dur = int((time.monotonic() - t0) * 1000)
    except Exception as exc:
        return CheckResult(name, False, detail=f"stream error: {exc}")

    if events and events[-1].get("_http_error"):
        return CheckResult(
            name, False,
            detail=f"HTTP {events[-1]['_http_error']}: {events[-1].get('_body','')}",
        )
    types = _event_types(events)
    got_text = "TEXT_MESSAGE_CONTENT" in types
    errored = "RUN_ERROR" in types
    finished = "RUN_FINISHED" in types
    ok = finished and got_text and not errored
    detail = (
        "ok" if ok
        else f"types={sorted(types)} text={got_text} err={errored} fin={finished}"
    )
    return CheckResult(
        name, ok, detail=detail, run_id=run_id, duration_ms=dur,
        events=len(events),
    )


async def check_chat_maf(_: argparse.Namespace) -> CheckResult:
    return await _chat_check("chat_maf", MAF_AGENT)


async def check_chat_copilot(_: argparse.Namespace) -> CheckResult:
    return await _chat_check("chat_copilot", COPILOT_AGENT)


async def check_debug_api(_: argparse.Namespace) -> CheckResult:
    """The diagnostics API itself must be up (it's how we debug the rest)."""
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.get(
                f"{GATEWAY_URL}/debug/runs?limit=5", headers=_headers(),
            )
        if r.status_code != 200:
            return CheckResult(
                "debug_api", False, detail=f"HTTP {r.status_code}: {r.text[:200]}",
            )
        data = r.json()
        return CheckResult(
            "debug_api", True,
            detail=f"{data.get('count', 0)} recent runs visible",
            extra={"recent": data.get("count", 0)},
        )
    except Exception as exc:
        return CheckResult("debug_api", False, detail=f"error: {exc}")


# ⚠️ `check_tasks_clickup` was REMOVED 2026-08-24 (D52, board WS-39 S1).
# Its verdict was `connected > 0 and scheduler_running and not overdue` over
# connected ClickUp workspaces. With the connector retired that verdict is
# permanently FALSE, and a check that can only ever report red is worse than
# no check: it trains an operator to ignore the report it lives in.
#
# The half worth keeping was the people-context readout (directory size,
# domain and résumé depth). It is NOT re-homed here on purpose — that belongs
# with the People surface, and inventing a home for it inside a deletion slice
# is how a second status doc gets born (CLAUDE.md §5).

async def _enrich_with_trace(results: list[CheckResult]) -> None:
    """For checks that produced a run_id, look up its durable trace status."""
    for res in results:
        if not res.run_id:
            continue
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.get(
                    f"{GATEWAY_URL}/debug/runs/{res.run_id}", headers=_headers(),
                )
            if r.status_code == 200:
                res.run_status = r.json().get("status")
        except Exception:
            pass  # trace enrichment is best-effort


_CHECKS: dict[str, Any] = {
    "health": check_health,
    "debug_api": check_debug_api,
    "chat_maf": check_chat_maf,
    "chat_copilot": check_chat_copilot,
}


async def _run(only: str | None) -> list[CheckResult]:
    names = [only] if only else list(_CHECKS)
    results: list[CheckResult] = []
    for n in names:
        fn = _CHECKS.get(n)
        if fn is None:
            results.append(CheckResult(n, False, detail="unknown check"))
            continue
        results.append(await fn(argparse.Namespace()))
    # Give the run-boundary trace write a moment to land, then enrich.
    await asyncio.sleep(1.0)
    await _enrich_with_trace(results)
    return results


def _print_table(results: list[CheckResult]) -> None:
    print(f"\nFeature check -- {GATEWAY_URL}\n" + "=" * 60)
    for r in results:
        mark = "PASS" if r.ok else "FAIL"
        line = f"  [{mark}] {r.name:<14} {r.detail}"
        if r.run_id:
            line += f"  (run {r.run_id[:8]}"
            if r.run_status:
                line += f", trace={r.run_status}"
            if r.duration_ms is not None:
                line += f", {r.duration_ms}ms"
            line += ")"
        print(line)
    n_ok = sum(1 for r in results if r.ok)
    print("=" * 60)
    print(f"  {n_ok}/{len(results)} passed")
    for r in results:
        if not r.ok and r.run_id:
            print(
                f"  ↳ debug: GET /debug/runs/{r.run_id}  "
                f"| journalctl -u acb-gateway | grep {r.run_id[:12]}"
            )


def main() -> int:
    ap = argparse.ArgumentParser(description="Metorite live feature check")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--only", help="run a single check by name")
    args = ap.parse_args()

    results = asyncio.run(_run(args.only))

    if args.json:
        print(json.dumps([r.__dict__ for r in results], indent=2))
    else:
        _print_table(results)

    # Exit non-zero if any check failed (so it's CI/monitoring-friendly).
    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
