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

Getting the cookie. The operator mints it on the box with ``encode`` from
``next-auth/jwt``. The salt is the cookie name, and the secret is the
workbench ``AUTH_SECRET``. The box keeps it at ``/home/acb/.smoke/cookie``
(mode 600, owner acb), and it never leaves the box. It expires after 30
days. An ``AUTH_SECRET`` rotation makes it invalid. Do not put a secret or a
token in the repo.

Exit 0 when all four steps pass, 1 when a step fails, 2 when the environment
is wrong. Every deploy runs it (S16): ``deploy/smoke_chat.sh`` mints a 900 s
session in memory on the box, and ``deploy.yml`` goes red when it fails.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import UTC, datetime

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


def main() -> int:
    base = _env("SMOKE_BASE_URL").rstrip("/")
    cookie = _env("SMOKE_COOKIE")
    email = _env("SMOKE_MEMBER_EMAIL").lower()
    org_slug = _env("SMOKE_ORG_SLUG")

    status, me = _call(base, cookie, "GET", "/api/auth/me")
    who = str((me or {}).get("email") or "").lower() if isinstance(me, dict) else ""
    org = ((me or {}).get("organization") or {}).get("slug") if isinstance(me, dict) else None
    if status != 200 or who != email or org != org_slug:
        print(f"REFUSED: the cookie is {who or 'nobody'} in {org or 'no org'}, "
              f"not {email} in {org_slug}. Nothing was written.")
        return 2

    _sweep(base, cookie)

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
    finally:
        status, body = _call(base, cookie, "DELETE", f"/api/chat/sessions/{sid}")
        if status not in (200, 204):
            print(f"WARN 4 delete: HTTP {status} {str(body)[:200]} (session {sid})")
        else:
            print("ok   4 delete session")
    print(f"PASS chat persistence on {base} as {email}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
