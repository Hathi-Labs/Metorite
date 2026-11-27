#!/usr/bin/env python3
"""Smoke check: a chat save lands on a live box (WS-27bm S15).

Spec: ``project-docs/specs/projects_ai_chat.md`` §21.

It goes through the Next BFF, the same path as the browser, in four steps:

1. ``POST /api/chat/sessions`` makes a session.
2. ``POST /api/chat/sessions/<id>/messages`` saves one user row, and the
   answer must say ``saved: 1``.
3. ``GET /api/chat/sessions/<id>/messages`` must give back that id and text.
4. ``DELETE /api/chat/sessions/<id>`` removes the session.

Before step 1 it sweeps, best effort (S16). ``GET /api/chat/sessions`` lists
the smoke member's sessions, and each one it owns older than one hour is deleted
through the same ``DELETE``, at most five a run. So a run that died before
step 4 leaves no row for long. A failed sweep prints ``WARN 0`` and never
fails the smoke.

It runs as a DEDICATED smoke member in a DEDICATED smoke org. It creates no
org and no member. Before step 1 it reads ``/api/auth/me`` and stops when the
cookie belongs to anyone other than ``SMOKE_MEMBER_EMAIL`` in ``SMOKE_ORG_SLUG``.
So a wrong cookie cannot write into a real member's history.

Environment:

    SMOKE_BASE_URL       the workbench origin, for example
                         https://app.metorite.com (the AUTH_URL host)
    SMOKE_COOKIE         the Cookie header of a signed-in session of the smoke
                         member. On HTTPS, Auth.js v5 names the cookie
                         ``__Secure-authjs.session-token``, and not
                         ``authjs.session-token``.
    SMOKE_MEMBER_EMAIL   the smoke member's email
    SMOKE_ORG_SLUG       the slug of the smoke member's org

Use the workbench host ``app.metorite.com``. The host ``metorite.com`` is a
different site. Its ``/api/auth/me`` gives 404, so the script stops with
"nobody in no org".

Getting the cookie. ``deploy/smoke_chat.sh`` mints a short-lived session in
memory on each run, with ``encode`` from ``next-auth/jwt``. The salt is the
cookie name, and the secret is the workbench ``AUTH_SECRET``. No cookie file
exists on the box, and the session never leaves the process. Do not put a
secret or a token in the repo.

The model turn (CIV-SMOKE-TURN, task 4061). Between step 3 and step 4,
``scripts/smoke_chat_turn.py`` runs one short real turn in the same session,
when ``SMOKE_CHAT_TURN`` is on. Its lines start with ``T``. It is off by
default, and then it prints one ``skip T0`` line. On with no
``SMOKE_CHAT_TURN_CAP_USD`` is ``REFUSED`` and exit 2, before anything is
written. When the smoke's own count of turns, or the metered spend, reaches
the cap, it prints a ``WARNING T1`` line and runs no turn.

``SMOKE_TURN_ONLY=1`` (``deploy/smoke_chat.sh --turn-only``) runs the turn
alone: step 1, the turn, step 4, and ``PASS chat turn on``. It skips the
sweep and steps 2 and 3. With the switch off, it writes nothing.

Exit 0 when all four steps pass, 1 when a step fails, 2 when the environment
is wrong. A turn failure is exit 1. A skipped turn and a switch that is off
do not change the exit code. Every deploy runs it (S16): ``deploy/smoke_chat.sh`` mints a 1200 s
session in memory on the box, and ``deploy.yml`` goes red when it fails.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import UTC, datetime
from pathlib import Path

TIMEOUT_S = 15
SWEEP_AGE_S = 3600   # a session older than this is a leftover
SWEEP_MAX = 5        # deletes per run, so the sweep has a time bound


def _env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        print(f"smoke_chat_persist: set {name}", file=sys.stderr)
        sys.exit(2)
    return value


def _call(base: str, cookie: str, method: str, path: str, body=None):
    data = None
    headers = {"Cookie": cookie, "Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(base + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as res:
            raw = res.read().decode("utf-8") or "null"
            return res.status, json.loads(raw) if raw.strip() else None
    except urllib.error.HTTPError as err:
        raw = err.read().decode("utf-8", "replace")
        try:
            return err.code, json.loads(raw)
        except ValueError:
            return err.code, raw
    except (urllib.error.URLError, TimeoutError) as err:
        print(f"FAIL {method} {path}: the box did not answer ({err})")
        sys.exit(1)


def _fail(step: str, status, body) -> None:
    print(f"FAIL {step}: HTTP {status} {str(body)[:300]}")
    sys.exit(1)


def _age_s(row: dict, now: float) -> float:
    raw = row.get("updatedAt") or row.get("createdAt") or ""
    try:
        at = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if at.tzinfo is None:
        at = at.replace(tzinfo=UTC)
    return now - at.timestamp()


def _sweep(base: str, cookie: str, now: float | None = None) -> None:
    """Delete the smoke member's sessions older than SWEEP_AGE_S. Best effort:
    it prints one line and never fails the smoke."""
    now = time.time() if now is None else now
    try:
        status, rows = _call(base, cookie, "GET", "/api/chat/sessions")
        if status != 200 or not isinstance(rows, list):
            print(f"WARN 0 sweep: HTTP {status}")
            return
        # Only the member's OWN rooms. A room shared with the member is not
        # its to delete, and it must not use up the budget of SWEEP_MAX.
        old = [r["id"] for r in rows
               if isinstance(r, dict) and r.get("id") and r.get("isOwner") is True
               and _age_s(r, now) > SWEEP_AGE_S]
        deleted = 0
        for sid in old[:SWEEP_MAX]:
            status, _ = _call(base, cookie, "DELETE", f"/api/chat/sessions/{sid}")
            if status in (200, 204):
                deleted += 1
    except SystemExit:
        print("WARN 0 sweep: the box did not answer")
        return
    left = len(old) - deleted
    print(f"ok   0 swept {deleted} old session(s)" + (f", {left} left" if left else ""))


def _turn_module():
    """The sibling ``smoke_chat_turn.py``, by its path, so a test that loads
    this file by path loads the same module."""
    path = Path(__file__).resolve().with_name("smoke_chat_turn.py")
    spec = importlib.util.spec_from_file_location("smoke_chat_turn", path)
    mod = importlib.util.module_from_spec(spec)
    # A dataclass reads its module from sys.modules.
    sys.modules["smoke_chat_turn"] = mod
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    base = _env("SMOKE_BASE_URL").rstrip("/")
    cookie = _env("SMOKE_COOKIE")
    email = _env("SMOKE_MEMBER_EMAIL").lower()
    org_slug = _env("SMOKE_ORG_SLUG")
    turn = _turn_module()
    cfg = turn.read_config(os.environ)
    turn_only = os.environ.get("SMOKE_TURN_ONLY", "").strip() == "1"
    if cfg.refusal:
        print(f"REFUSED T0 turn: {cfg.refusal} Nothing was written.")
        return 2

    status, me = _call(base, cookie, "GET", "/api/auth/me")
    who = str((me or {}).get("email") or "").lower() if isinstance(me, dict) else ""
    org = ((me or {}).get("organization") or {}).get("slug") if isinstance(me, dict) else None
    if status != 200 or who != email or org != org_slug:
        print(f"REFUSED: the cookie is {who or 'nobody'} in {org or 'no org'}, "
              f"not {email} in {org_slug}. Nothing was written.")
        return 2

    if turn_only and not cfg.on:
        print("skip T0 turn: SMOKE_CHAT_TURN is off. No model turn ran.")
        return 0
    if not turn_only:
        _sweep(base, cookie)

    turn_rc = 0
    sid = str(uuid.uuid4())
    mid = f"smoke-{uuid.uuid4().hex[:12]}"
    text = f"S15 smoke {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}"
    try:
        status, body = _call(base, cookie, "POST", "/api/chat/sessions", {
            "id": sid, "agent_name": "orchestrator", "title": "S15 smoke",
            "last_preview": None, "message_count": 0,
        })
        if status != 200:
            _fail("1 create session", status, body)
        print("ok   1 create session")

        if not turn_only:
            status, body = _call(base, cookie, "POST", f"/api/chat/sessions/{sid}/messages", [{
                "id": mid, "role": "user", "content": text,
                "timestamp": int(time.time() * 1000), "tool_events": [],
                "progress_lines": [], "agent_state": None, "custom_events": [],
            }])
            if status != 200 or not isinstance(body, dict) or body.get("saved") != 1:
                _fail("2 save one row", status, body)
            print("ok   2 save one row (saved: 1)")

            status, body = _call(base, cookie, "GET", f"/api/chat/sessions/{sid}/messages")
            rows = body if isinstance(body, list) else []
            if status != 200 or [(r.get("id"), r.get("content")) for r in rows] != [(mid, text)]:
                _fail("3 read it back", status, body)
            print("ok   3 read it back")

        # The model turn, in this session, so step 4 deletes it too.
        turn_rc = turn.run(base, cookie, sid, cfg, _call)
    finally:
        status, body = _call(base, cookie, "DELETE", f"/api/chat/sessions/{sid}")
        if status not in (200, 204):
            print(f"WARN 4 delete: HTTP {status} {str(body)[:200]} (session {sid})")
        else:
            print("ok   4 delete session")
    # No PASS line after a red turn. deploy.yml reads the PASS line as green.
    if turn_rc:
        return 1
    if turn_only:
        print(f"PASS chat turn on {base} as {email}")
        return 0
    print(f"PASS chat persistence on {base} as {email}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
