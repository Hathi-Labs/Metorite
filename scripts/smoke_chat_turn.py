#!/usr/bin/env python3
"""Smoke check: one real model turn on a live box (CIV-SMOKE-TURN, task 4061).

Spec: ``project-docs/specs/projects_ai_chat.md`` §21.10.

``scripts/smoke_chat_persist.py`` calls this module. It is not a script of
its own. The persist smoke proves that a chat SAVES. This step proves that a
chat ANSWERS: one short turn of the orchestrator, with its tools on, in the
smoke org. On 2026-11-26, 25 flags went on and no real turn ran for hours.

It is DARK. ``SMOKE_CHAT_TURN`` is off by default, and the owner has not set
a budget. ``deploy/smoke_chat.sh`` reads both settings from the box ``.env``
and gives them to the persist run in its environment:

    SMOKE_CHAT_TURN          on when ``1``, ``true``, ``on`` or ``yes``.
                             Anything else is off.
    SMOKE_CHAT_TURN_CAP_USD  the monthly cap of the smoke org, in USD. It has
                             NO default. On with no cap is a refusal.

The four checks, in order:

  T1 the spend. ``GET /api/observability/cost?days=31`` gives the smoke
     org's daily cost rollup (``acb_common.activity.cost_summary``, the
     metering of every priced model call). The sum of this UTC month is the
     spend. At or over the cap, or not readable, the turn does not run.
     That is a WARNING line and not a red. The read writes nothing.
  T2 the stream. ``POST /api/agent/chat`` runs one turn through the BFF, the
     path of the browser. The stream must end with the ``done`` event, and
     carry no ``error`` event.
  T3 the saved answer. The assistant row of the turn must exist, with text.
  T4 the gateway log. ``journalctl -u acb-gateway`` from the turn start to
     now must hold no ``Traceback``, and at least one line.

The turn goes through the normal metered path. Nothing here writes a credit
row or a cost.

⏱ THE TIME BOUND of the turn, inside ``SMOKE_TURN_S`` (240 s):
  T1 one call                                     15 s
  T2 the stream deadline, plus one read past it   120 s + 45 s
  T3 two reads and one nap                        15 s + 2 s + 15 s
  T4 one journalctl                               15 s
  sum                                             227 s
The stream has no keepalive, and a model can think for some seconds between
two frames. So one read of the stream waits 45 s, not 15 s.
``tests/unit/test_smoke_chat_turn.py`` checks the sum.

🔴 The cookie stays in this process. ``journalctl`` gets an environment
without ``SMOKE_COOKIE``, and its argv holds only the unit and the times.
"""
from __future__ import annotations

import json
import math
import os
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

CALL_S = 15              # one HTTP call
STREAM_READ_S = 45       # the open of the stream, and one read of it
STREAM_S = 120           # the stream must end inside this
READ_TRIES = 2           # T3 reads the saved row at most this many times
READ_NAP_S = 2
JOURNAL_S = 15
TURN_S = 240             # deploy/smoke_chat.sh SMOKE_TURN_S, the outer bound
GATEWAY_UNIT = "acb-gateway"
TRACEBACK = "Traceback (most recent call last)"
ON_WORDS = frozenset({"1", "true", "on", "yes"})

AGENT = "orchestrator"
#: A tiny fixed prompt. ``list_integrations`` is a core tool of the
#: orchestrator. It is read only, and it calls no outside service.
PROMPT = (
    "This is an automated release check. Call the list_integrations tool "
    "once. Then answer in one short sentence with the number of integrations "
    "it lists. Do not call any other tool."
)


@dataclass(frozen=True)
class Config:
    on: bool
    cap_usd: float | None
    refusal: str | None


def read_config(env: dict[str, str] | os._Environ[str]) -> Config:
    """The switch and the cap. On with no valid cap is a refusal."""
    on = env.get("SMOKE_CHAT_TURN", "").strip().lower() in ON_WORDS
    if not on:
        return Config(on=False, cap_usd=None, refusal=None)
    raw = env.get("SMOKE_CHAT_TURN_CAP_USD", "").strip()
    if not raw:
        return Config(on=True, cap_usd=None, refusal=(
            "SMOKE_CHAT_TURN is on, and SMOKE_CHAT_TURN_CAP_USD is not set. "
            "The turn spends money, so it needs a monthly cap. Set the cap in "
            "the box .env, or set SMOKE_CHAT_TURN=0."))
    try:
        cap = float(raw)
    except ValueError:
        cap = float("nan")
    if not math.isfinite(cap) or cap <= 0:
        return Config(on=True, cap_usd=None, refusal=(
            f"SMOKE_CHAT_TURN_CAP_USD={raw[:20]!r} is not a number above 0."))
    return Config(on=True, cap_usd=cap, refusal=None)


# ── T1: the spend of this month ─────────────────────────────────────────────


def month_spend(status: int, body: object, now: datetime) -> float | None:
    """The spend of this UTC month from a cost rollup, or None when unknown.

    The BFF answers 200 with an EMPTY rollup when the gateway fails. A real
    rollup has one row for each day it was asked for. So an empty ``days``
    list is "not known", and never "zero".
    """
    if status != 200 or not isinstance(body, dict):
        return None
    days = body.get("days")
    if not isinstance(days, list) or not days:
        return None
    month = now.strftime("%Y-%m")
    spend = 0.0
    for day in days:
        if not isinstance(day, dict):
            return None
        if str(day.get("date", "")).startswith(month):
            try:
                spend += float(day.get("cost") or 0)
            except (TypeError, ValueError):
                return None
    return spend


# ── T2: the stream ──────────────────────────────────────────────────────────


@dataclass
class StreamResult:
    done: bool = False
    error: str | None = None
    tools: list[str] | None = None
    late: bool = False


def read_stream(lines: Iterable[bytes], deadline: float,
                clock: Callable[[], float] = time.monotonic) -> StreamResult:
    """Read SSE ``data:`` frames until ``done``, an ``error`` or the end."""
    out = StreamResult(tools=[])
    for raw in lines:
        if clock() > deadline:
            out.late = True
            return out
        line = raw.decode("utf-8", "replace").strip()
        if not line.startswith("data:"):
            continue
        try:
            ev = json.loads(line[5:].strip())
        except ValueError:
            continue
        if not isinstance(ev, dict):
            continue
        kind = ev.get("type")
        if kind == "tool_start":
            out.tools.append(str(ev.get("name") or "?"))
        elif kind == "error":
            out.error = str(ev.get("content") or ev.get("code") or "error")[:200]
            return out
        elif kind == "done":
            out.done = True
            return out
    return out


def open_stream(base: str, cookie: str, body: dict):
    """POST the turn. Returns (status, response or text)."""
    req = urllib.request.Request(
        base + "/api/agent/chat", data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"Cookie": cookie, "Content-Type": "application/json",
                 "Accept": "text/event-stream"})
    try:
        res = urllib.request.urlopen(req, timeout=STREAM_READ_S)
        return res.status, res
    except urllib.error.HTTPError as err:
        return err.code, err.read().decode("utf-8", "replace")[:300]
    except (urllib.error.URLError, TimeoutError) as err:
        return 0, f"the box did not answer ({err})"


# ── T4: the gateway log ─────────────────────────────────────────────────────


def journal_argv(since: float, until: float) -> list[str]:
    return ["journalctl", "-u", GATEWAY_UNIT, "--since", f"@{int(since)}",
            "--until", f"@{int(until) + 1}", "--no-pager", "-o", "cat", "-q"]


def read_journal(since: float, until: float) -> tuple[int, str]:
    """The gateway log of the window. Never the cookie: not in argv, not in env."""
    env = {k: v for k, v in os.environ.items() if k != "SMOKE_COOKIE"}
    try:
        r = subprocess.run(journal_argv(since, until), stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=JOURNAL_S, env=env, check=False)
    except (OSError, subprocess.TimeoutExpired) as err:
        return 127, str(err)
    return r.returncode, r.stdout


def judge_journal(rc: int, text: str) -> str | None:
    """None when the window is clean. Otherwise the FAIL text."""
    if rc != 0:
        return f"journalctl -u {GATEWAY_UNIT} exited {rc}. The log was not read."
    lines = text.splitlines()
    if not lines:
        return (f"journalctl gave no line of {GATEWAY_UNIT} since the turn start. "
                "acb cannot read the gateway journal, so the log was not checked.")
    hits = [i for i, ln in enumerate(lines) if TRACEBACK in ln]
    if not hits:
        return None
    first = hits[0]
    tail = " | ".join(ln[:160] for ln in lines[first:first + 4])
    return f"{len(hits)} Traceback(s) in {GATEWAY_UNIT} since the turn start: {tail}"


# ── The step ────────────────────────────────────────────────────────────────

Call = Callable[..., tuple[int, object]]


def _judge_stream(status: int, res: object, deadline: float) -> str | None:
    """T2. None when the stream ended with ``done``. Otherwise the FAIL text."""
    if status != 200 or isinstance(res, str):
        return f"HTTP {status} {str(res)[:300]}"
    try:
        got = read_stream(res, deadline)  # type: ignore[arg-type]
    except (OSError, TimeoutError) as err:
        got = StreamResult(error=f"the stream broke ({err})")
    finally:
        close = getattr(res, "close", None)
        if close:
            close()
    if got.late:
        return f"no done event in {STREAM_S}s"
    if got.error:
        return f"an error event: {got.error}"
    if not got.done:
        return "the stream ended with no done event"
    print(f"ok   T2 stream done (tools: {', '.join(got.tools or []) or 'none'})")
    return None


def _saved_answer(base: str, cookie: str, sid: str, aid: str, call: Call,
                  nap: Callable[[float], None]) -> str | None:
    """T3. None when the assistant row has text. Otherwise the FAIL text."""
    status = 0
    for i in range(READ_TRIES):
        status, rows = call(base, cookie, "GET", f"/api/chat/sessions/{sid}/messages")
        row = next((r for r in rows if isinstance(r, dict) and r.get("id") == aid),
                   None) if isinstance(rows, list) else None
        text = str((row or {}).get("content") or "").strip()
        if text:
            print(f"ok   T3 saved answer ({len(text)} chars)")
            return None
        if i + 1 < READ_TRIES:
            nap(READ_NAP_S)
    return f"no assistant row {aid} with text (HTTP {status})"


def run(base: str, cookie: str, sid: str, cfg: Config, call: Call, *,
        stream: Callable[[str, str, dict], tuple[int, object]] = open_stream,
        journal: Callable[[float, float], tuple[int, str]] = read_journal,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        nap: Callable[[float], None] = time.sleep) -> int:
    """Run the turn in session ``sid``. 0 = passed, skipped or off. 1 = red."""
    if not cfg.on:
        print("skip T0 turn: SMOKE_CHAT_TURN is off. No model turn ran.")
        return 0
    assert cfg.cap_usd is not None  # read_config refuses on with no cap

    status, body = call(base, cookie, "GET", "/api/observability/cost?days=31")
    spend = month_spend(status, body, now())
    if spend is None:
        print(f"WARNING T1 turn skipped: the spend of this month could not be read "
              f"(HTTP {status}). No model turn ran.")
        return 0
    if spend >= cfg.cap_usd:
        print(f"WARNING T1 turn skipped: the smoke org spent ${spend:.2f} this month, "
              f"at or over the cap of ${cfg.cap_usd:.2f}. No model turn ran.")
        return 0
    print(f"ok   T1 spend ${spend:.2f} of ${cfg.cap_usd:.2f} this month")

    aid = f"smoke-turn-a-{uuid.uuid4().hex[:12]}"
    started = time.time()
    # The deadline counts from before the open, so T2 is at most
    # STREAM_S + STREAM_READ_S, whatever the open takes.
    deadline = time.monotonic() + STREAM_S
    status, res = stream(base, cookie, {
        "agentName": AGENT, "mode": "copilot", "message": PROMPT, "messages": [],
        "threadId": sid, "assistantMessageId": aid,
        "userMessageId": f"smoke-turn-u-{uuid.uuid4().hex[:12]}",
        "userMessageTs": int(started * 1000),
    })
    failed = False
    bad = _judge_stream(status, res, deadline)
    if bad:
        print(f"FAIL T2 stream: {bad}")
        failed = True
    else:
        bad = _saved_answer(base, cookie, sid, aid, call, nap)
        if bad:
            print(f"FAIL T3 saved answer: {bad}")
            failed = True

    # T4 runs after every started turn. A Traceback often names the cause of
    # a T2 or T3 failure.
    bad = judge_journal(*journal(started, time.time()))
    if bad:
        print(f"FAIL T4 gateway log: {bad}")
        failed = True
    else:
        print("ok   T4 no Traceback in the gateway log since the turn start")
    return 1 if failed else 0
