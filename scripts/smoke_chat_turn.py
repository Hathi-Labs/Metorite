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
    SMOKE_CHAT_TURN_EST_USD  the cost of one turn that the count guard
                             assumes. Default 0.25. Set it high, not low.
    SMOKE_TURN_STATE         the count file. Default
                             /var/lib/acb/smoke-chat-turn.json.

T1 has TWO guards, and either one skips the turn:

  * The count guard does not need Redis. The smoke keeps its own count of
    the turns of this UTC month in ``SMOKE_TURN_STATE``. It skips when
    count x SMOKE_CHAT_TURN_EST_USD is at or over the cap. It writes the
    count + 1 BEFORE the turn, so a crash still counts the turn. A file that
    does not parse, a directory it cannot write, or a lock that another
    smoke holds skips the turn (fail closed). A missing file, or a file of
    an earlier month, is a count of 0.
  * The metering guard. ``GET /api/observability/cost?days=31`` gives the
    smoke org's daily cost rollup (``acb_common.activity.cost_summary``).
    The sum of this UTC month is the spend. The rollup is in Redis, and a
    model with no price writes no cost, so it is a second guard only. An
    empty rollup is UNKNOWN. A rollup of only zeros with ``totals.calls``
    0 is UNKNOWN too, when the count says a turn ran this month.

A skip is a WARNING line and not a red. The guards write nothing but the
count file.

  T2 the stream. ``POST /api/agent/chat`` runs one turn through the BFF, the
     path of the browser. The stream must end with the ``done`` event, and
     carry no ``error`` event. A failure is red.
  T3 the saved answer. The assistant row of the turn must exist, with text.
     A failure is red.
  T4 the gateway log, from the turn start to now, as ``journalctl -o json``.
     A line with a Traceback AND the thread id of this turn is red. The
     executor binds ``thread_id`` to each structlog line of the run, and
     with ``LOG_FORMAT=json`` the Traceback is on that same line. Any other
     Traceback in the window, or a log that acb cannot read, is a WARNING,
     because another tenant can own it. T4 never prints log text. It prints
     the count and the times of the hits only.

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

A retry of the smoke after an ssh drop can run up to 3 turns in one
release. The count guard counts each one.

🔴 The cookie stays in this process. ``journalctl`` gets an environment
without ``SMOKE_COOKIE``, and its argv holds only the unit and the times.
The count file holds the month and the count, and nothing else.
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import math
import os
import re
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
EST_USD_DEFAULT = 0.25
STATE_DEFAULT = "/var/lib/acb/smoke-chat-turn.json"
HITS_SHOWN = 5           # T4 prints at most this many hit times

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
    est_usd: float = EST_USD_DEFAULT
    state_path: str = STATE_DEFAULT


def _positive(raw: str) -> float | None:
    try:
        v = float(raw)
    except ValueError:
        return None
    return v if math.isfinite(v) and v > 0 else None


def read_config(env: dict[str, str] | os._Environ[str]) -> Config:
    """The switch, the cap and the estimate. On with no valid cap is a refusal."""
    on = env.get("SMOKE_CHAT_TURN", "").strip().lower() in ON_WORDS
    if not on:
        return Config(on=False, cap_usd=None, refusal=None)
    raw = env.get("SMOKE_CHAT_TURN_CAP_USD", "").strip()
    if not raw:
        return Config(on=True, cap_usd=None, refusal=(
            "SMOKE_CHAT_TURN is on, and SMOKE_CHAT_TURN_CAP_USD is not set. "
            "The turn spends money, so it needs a monthly cap. Set the cap in "
            "the box .env, or set SMOKE_CHAT_TURN=0."))
    cap = _positive(raw)
    if cap is None:
        return Config(on=True, cap_usd=None, refusal=(
            f"SMOKE_CHAT_TURN_CAP_USD={raw[:20]!r} is not a number above 0."))
    raw_est = env.get("SMOKE_CHAT_TURN_EST_USD", "").strip()
    est = _positive(raw_est) if raw_est else EST_USD_DEFAULT
    if est is None:
        return Config(on=True, cap_usd=None, refusal=(
            f"SMOKE_CHAT_TURN_EST_USD={raw_est[:20]!r} is not a number above 0."))
    state = env.get("SMOKE_TURN_STATE", "").strip() or STATE_DEFAULT
    return Config(on=True, cap_usd=cap, refusal=None, est_usd=est, state_path=state)


# ── T1, the count guard: the smoke's own count of this month's turns ────────


def read_count(path: str, month: str) -> int | None:
    """The turns of ``month`` in the count file. None when the file is corrupt.

    A missing file, or a file of another month, is 0 (the month rolled over).
    """
    try:
        with open(path, encoding="utf-8") as fh:
            raw = fh.read()
    except FileNotFoundError:
        return 0
    except OSError:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    got_month, turns = data.get("month"), data.get("turns")
    if not isinstance(got_month, str) or not re.fullmatch(r"\d{4}-\d{2}", got_month):
        return None
    if isinstance(turns, bool) or not isinstance(turns, int) or turns < 0:
        return None
    return turns if got_month == month else 0


def write_count(path: str, month: str, turns: int) -> bool:
    """Write the count atomically: a temp file in the same directory, then a
    rename. False when it cannot write."""
    tmp = f"{path}.tmp.{os.getpid()}"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"month": month, "turns": turns}, fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except OSError:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        return False
    return True


@contextlib.contextmanager
def count_lock(path: str):
    """An exclusive lock beside the count file, never waited for. Yields
    None when this smoke holds it, or the reason it does not."""
    try:
        fh = open(f"{path}.lock", "a", encoding="utf-8")  # noqa: SIM115
    except OSError as err:
        yield f"the count file cannot be opened ({err.strerror})"
        return
    try:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield "another smoke holds the turn count"
            return
        yield None
    finally:
        fh.close()


# ── T1: the spend of this month ─────────────────────────────────────────────


def month_spend(status: int, body: object, now: datetime,
                turns_this_month: int = 0) -> float | None:
    """The spend of this UTC month from a cost rollup, or None when unknown.

    The BFF answers 200 with an EMPTY rollup when the gateway fails. A real
    rollup has one row for each day it was asked for. So an empty ``days``
    list is "not known", and never "zero".

    A failed Redis read, or a flush, gives a full rollup of zeros. So zeros
    on every day with ``totals.calls`` 0 are also "not known", when the count
    says that a turn ran this month. With no turn this month, zero is the
    expected answer, and the count guard still holds.
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
    if turns_this_month > 0 and _all_zero(body, days):
        return None
    return spend


def _all_zero(body: dict, days: list) -> bool:
    totals = body.get("totals")
    calls = totals.get("calls") if isinstance(totals, dict) else None
    try:
        no_calls = not calls or float(calls) == 0
        return no_calls and all(float(d.get("cost") or 0) == 0 for d in days)
    except (TypeError, ValueError):
        return True


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
            "--until", f"@{int(until) + 1}", "--no-pager", "-o", "json", "-q"]


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


def _entries(text: str) -> list[tuple[str, str]]:
    """(time, message) of each ``journalctl -o json`` line. journald stores a
    message with control bytes, such as ANSI colour, as a list of bytes."""
    out = []
    for line in text.splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if not isinstance(ev, dict):
            continue
        msg = ev.get("MESSAGE")
        if isinstance(msg, list):
            try:
                msg = bytes(msg).decode("utf-8", "replace")
            except (TypeError, ValueError):
                msg = ""
        try:
            at = datetime.fromtimestamp(int(ev.get("__REALTIME_TIMESTAMP")) / 1e6, UTC)
            when = at.strftime("%H:%M:%S")
        except (TypeError, ValueError):
            when = "?"
        out.append((when, msg if isinstance(msg, str) else ""))
    return out


def judge_journal(rc: int, text: str, thread_id: str) -> tuple[str, str | None]:
    """("ok", None), ("warn", why) or ("fail", why).

    Red only for a Traceback on a line that carries this turn's thread id.
    The text of a line never reaches the output: another tenant can own it.
    """
    read_it = f"Read it on the box: journalctl -u {GATEWAY_UNIT} --since <time> as acb."
    if rc != 0:
        return "warn", f"journalctl -u {GATEWAY_UNIT} exited {rc}. The log was not checked."
    entries = _entries(text)
    if not entries:
        return "warn", (f"journalctl gave no line of {GATEWAY_UNIT} since the turn start. "
                        "acb cannot read the gateway journal, so the log was not checked.")
    ours = [w for w, m in entries if TRACEBACK in m and thread_id in m]
    other = [w for w, m in entries if TRACEBACK in m and thread_id not in m]
    if ours:
        return "fail", (f"{len(ours)} Traceback(s) of this turn at "
                        f"{', '.join(ours[:HITS_SHOWN])} UTC. {read_it}")
    if other:
        return "warn", (f"{len(other)} Traceback(s) in {GATEWAY_UNIT} since the turn "
                        f"start with no thread id of this turn, at "
                        f"{', '.join(other[:HITS_SHOWN])} UTC. {read_it}")
    return "ok", None


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


def _claim_turn(base: str, cookie: str, cfg: Config, call: Call,
                at: datetime) -> str | None:
    """T1. Both guards, then the count + 1 is written. None means "run the
    turn". Otherwise the reason for the skip."""
    assert cfg.cap_usd is not None  # read_config refuses on with no cap
    cap, month = cfg.cap_usd, at.strftime("%Y-%m")
    with count_lock(cfg.state_path) as held:
        if held:
            return held
        turns = read_count(cfg.state_path, month)
        if turns is None:
            return f"the count file {cfg.state_path} does not parse"
        if turns * cfg.est_usd >= cap:
            return (f"{turns} turns ran this month, at ${cfg.est_usd:.2f} each, "
                    f"at or over the cap of ${cap:.2f}")
        status, body = call(base, cookie, "GET", "/api/observability/cost?days=31")
        spend = month_spend(status, body, at, turns)
        if spend is None:
            return f"the metered spend of this month could not be read (HTTP {status})"
        if spend >= cap:
            return (f"the smoke org spent ${spend:.2f} this month, at or over the "
                    f"cap of ${cap:.2f}")
        # Before the turn, so a crash in the turn still counts it.
        if not write_count(cfg.state_path, month, turns + 1):
            return f"the count file {cfg.state_path} cannot be written"
    print(f"ok   T1 turn {turns + 1} this month, metered ${spend:.2f} of ${cap:.2f}")
    return None


def run(base: str, cookie: str, sid: str, cfg: Config, call: Call, *,
        stream: Callable[[str, str, dict], tuple[int, object]] = open_stream,
        journal: Callable[[float, float], tuple[int, str]] = read_journal,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        nap: Callable[[float], None] = time.sleep) -> int:
    """Run the turn in session ``sid``. 0 = passed, skipped or off. 1 = red."""
    if not cfg.on:
        print("skip T0 turn: SMOKE_CHAT_TURN is off. No model turn ran.")
        return 0
    skip = _claim_turn(base, cookie, cfg, call, now())
    if skip:
        print(f"WARNING T1 turn skipped: {skip}. No model turn ran.")
        return 0

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
    level, why = judge_journal(*journal(started, time.time()), sid)
    if level == "fail":
        print(f"FAIL T4 gateway log: {why}")
        failed = True
    elif level == "warn":
        print(f"WARNING T4 gateway log: {why}")
    else:
        print("ok   T4 no Traceback in the gateway log since the turn start")
    return 1 if failed else 0
