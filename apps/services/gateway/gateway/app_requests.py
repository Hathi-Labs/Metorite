"""The approval store of the app door: the writes of an app run (MCP-P1b-1).

Spec: ``project-docs/specs/external_agent_access.md`` §19.5, AD-5, AD-13 and
AD-16 of §19.12, and row MCP-P1b-1 of §19.13.

An outside agent (the MCP door) or a Studio write asks the member to approve
ONE call. The request waits on an agent run that names an app and no task
(migration ``pm_agent_runs_app_subject``). The member decides on
``AppRequestCard`` in Integrations, AI agents. Then the caller spends the
request once, through ``acb_auth.app_requests.spend``, and runs the STORED
call.

* :func:`open_request` opens one app run and writes one elicitation that the
  SERVER types: ``content_metadata.request`` holds the source, the app, the
  op, the class, the exact call, its digest, its binding, the card and the
  expiry. The request id is the id of that elicitation. Nothing runs.
* :func:`decide` writes the member's answer. An approve moves the run to
  ``in_progress``. A decline closes it (``app_close``).
* :func:`read_request` and :func:`list_open` read the stored request of the
  member, never the call of another member.

**This module is the one writer of app runs.** ``routes/projects/agent_runs``
stays the one writer of task runs, and this module reuses its status machine
(``transition``, with the events ``app_ask`` and ``app_close``), its lock
(``_lock_run``), its clock (``_clock``) and its move (``_move_run``). It
forks none of them. :func:`_write_app_activity` is the app variant of
``_write_activity``: it writes no task comment and no bell, because
``pm_notifications.task_id`` is NOT NULL. The home of a request is the list.

**The rules (D2, D6 and D8 of the audit).**

* The org is the bound session's and the member is ``user.email``. Neither
  comes from request input (R5, R11). The class comes from the manifest or
  ``OBJECT_OP_CLASSES``, never from the caller. Class A and X are refused.
* A call over :data:`MAX_CALL_BYTES` of JSON gets 413 ``payload_too_large``,
  because the card shows all or nothing. A key that holds a word of
  :data:`SECRET_WORDS`, at any depth, gets 422 ``secret_in_payload``, and
  nothing is stored. It refuses, and drops nothing, because a dropped key
  changes what runs.
* The same idempotency key with the same call gives the first request. The
  same key with another call is 409.
* At most :data:`OPEN_CAP` live requests for each connection, or for each
  member and app of a Studio request (AD-13). The count reads the expiry,
  because no sweeper closes a request that expired.
* A request lives :data:`REQUEST_TTL`. Each read and each spend checks the
  expiry against the clock of the database.

Fences: ``tests/unit/test_app_requests_r8.py`` (R8) and
``test_every_pm_agent_runs_query_names_its_subject`` in the same file.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from acb_auth import agent_oauth
from acb_auth import app_requests as store
from acb_common import get_logger, get_settings
from fastapi import HTTPException
from sqlalchemy import text

from gateway.routes.projects import agent_runs
from gateway.routes.projects.core import from_jsonb, wire

_log = get_logger("gateway.app_requests")


def requests_allowed(organization_id: str | None) -> bool:
    """True when ``APP_REQUESTS_ORGS`` holds the org, or holds ``*``. Default
    OFF. The ONE reader of the flag (fix round 1, review P2-1).

    It is the dark switch of the store, because ``AGENT_ACCESS_ENABLED`` is
    on in production. The org comes from the session, never from request
    input (R5). It reads the settings on each call, so a flag reload applies
    a change with no restart.
    """
    org = str(organization_id or "").strip().lower()
    if not org:
        return False
    raw = str(getattr(get_settings(), "app_requests_orgs", "") or "")
    listed = {p.strip().lower() for p in raw.split(",") if p.strip()}
    return "*" in listed or org in listed

#: A request lives this long (EA3-40 rule 5, §19.5 "The timeout").
REQUEST_TTL = agent_runs.ARCHIVE_REQUEST_TTL

#: The largest call that a card shows, in bytes of compact JSON (D8 rule 1).
MAX_CALL_BYTES = 32 * 1024

#: The live requests of one connection, or of one member and app (AD-13).
OPEN_CAP = 20

#: The most requests that one list returns.
LIST_LIMIT = 50

#: A key whose name holds one of these words never enters a request (D8 rule
#: 4). The same words as ``agent_edge.tools.SECRET_WORDS``. A test holds the
#: two lists equal.
SECRET_WORDS: tuple[str, ...] = ("token", "secret", "password", "credential", "api_key")

#: The largest integer that a browser's JSON parse keeps exactly (review P2-2).
#: Past it, the card would show a number other than the one that runs.
MAX_SAFE_INT = 2**53 - 1

#: The classes that wait for the member. A is a read and X is on no door.
ASKING_CLASSES: tuple[str, ...] = ("B", "C")

#: The text of the elicitation, fixed by the server. The agent sends no text.
REQUEST_BODY = (
    "The agent asks to make this change. Only the member of this request can "
    "approve or decline it."
)

#: The two options of every app request. The server fixes them.
OPTIONS: tuple[agent_runs.OptionIn, ...] = (
    agent_runs.OptionIn(id=store.APPROVE, label="Approve"),
    agent_runs.OptionIn(id=store.DECLINE, label="Decline"),
)

#: The class line of the card (§19.5, "The card").
CLASS_LINES: dict[str, str] = {
    "B": "You can undo this in {app}.",
    "C": "This is hard to undo.",
}

#: The stable codes of a refusal.
PAYLOAD_TOO_LARGE = "payload_too_large"
SECRET_IN_PAYLOAD = "secret_in_payload"
TOO_MANY_REQUESTS = "too_many_requests"
CLASS_REFUSED = "class_refused"
KEY_REUSED = "idempotency_key_reused"
ALREADY_DECIDED = "already_decided"
REQUEST_EXPIRED = "request_expired"
BAD_ARGS = "bad_args"

_APP = re.compile(r"[a-z]{1,32}")
_OP = re.compile(r"[A-Za-z0-9_.:-]{1,64}")
_SUBJECT = re.compile(r"[A-Za-z0-9_.:-]{0,64}")
_VIA = re.compile(r"(mcp|studio):[a-z0-9:_.-]{1,60}")
_METHODS = ("POST", "PUT", "PATCH", "DELETE")


def _refuse(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"error": code, "message": message})


@dataclass(frozen=True)
class Opened:
    """A request that :func:`open_request` opened or found again."""

    request_id: str
    run_id: str
    state: str
    expires_at: str
    created: bool


@dataclass(frozen=True)
class Decided:
    """The member's answer to one request."""

    request_id: str
    run_id: str
    decision: str
    state: str
    created: bool


# ── The checks of a call (D8) ───────────────────────────────────────────────


def _holds_secret_key(value: Any) -> bool:
    """True when a key of *value*, at any depth, holds a word of
    :data:`SECRET_WORDS`."""
    if isinstance(value, Mapping):
        for name, inner in value.items():
            if any(word in str(name).lower() for word in SECRET_WORDS):
                return True
            if _holds_secret_key(inner):
                return True
        return False
    if isinstance(value, (list, tuple)):
        return any(_holds_secret_key(inner) for inner in value)
    return False


def _unshowable(value: Any) -> bool:
    """True when *value*, at any depth, holds a value that the card cannot
    show exactly, or that Postgres cannot store (review P2-2 and P3-2):

    * an integer past :data:`MAX_SAFE_INT` either way, which a browser
      parses to another number;
    * a number that is not finite (NaN, Infinity), which is no JSON;
    * a text, or a key, that holds U+0000, which JSONB refuses.
    """
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return abs(value) > MAX_SAFE_INT
    if isinstance(value, float):
        return not math.isfinite(value)
    if isinstance(value, str):
        return "\x00" in value
    if isinstance(value, Mapping):
        return any(_unshowable(str(k)) or _unshowable(v) for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return any(_unshowable(v) for v in value)
    return False


def _clean_call(call: Mapping[str, Any]) -> dict[str, Any]:
    """The call as the request stores it: the method, the template, the path,
    the sorted query pairs, the body and ``if_match``. A call of another
    shape is a server error, because only the server builds a call."""
    method = str(call.get("method") or "").upper()
    if method not in _METHODS:
        raise ValueError("an app request is a write")
    query = sorted((str(k), str(v)) for k, v in (call.get("query") or ()))
    if_match = call.get("if_match")
    return {
        "method": method,
        "template": str(call["template"]),
        "path": str(call["path"]),
        "query": [list(pair) for pair in query],
        "body": call.get("body"),
        "if_match": str(if_match) if if_match is not None else None,
    }


def _call_bytes(call: Mapping[str, Any]) -> int:
    return len(json.dumps(call, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":")).encode("utf-8"))


def _key_digest(binding: str, key: str) -> str:
    """The ``idempotency_key`` of an app run: a hash of the binding and the
    key, so it never meets the key of a task run of the same member."""
    return hashlib.sha256(f"app_request\n{binding}\n{key}".encode()).hexdigest()


# ── The reads ───────────────────────────────────────────────────────────────

#: One request of one member: its elicitation, its run, its decision and
#: whether it is live. Each read of this module goes through it.
_REQUEST_SELECT = (
    "SELECT e.id AS request_id, e.run_id, e.content_metadata, e.created_at,"
    "       r.app, r.status, r.client_via, r.connection_id, r.member_email,"
    "       (CAST(e.content_metadata->'request'->>'expires_at' AS timestamptz)"
    "          > clock_timestamp()) AS live,"
    "       d.decision, d.created_at AS decided_at"
    "  FROM pm_agent_run_activities e"
    "  JOIN pm_agent_runs r ON r.id = e.run_id"
    "  LEFT JOIN LATERAL ("
    "    SELECT p.content->'selected'->>0 AS decision, p.created_at"
    "      FROM pm_agent_run_activities p"
    "     WHERE p.run_id = e.run_id AND p.type = 'prompt' AND p.in_reply_to = e.id"
    "     ORDER BY p.seq LIMIT 1"
    "  ) d ON TRUE"
    " WHERE e.type = 'elicitation' AND r.app IS NOT NULL"
    "   AND e.content_metadata->'request'->>'kind' = 'app_write'"
    "   AND r.organization_id = CAST(:org AS uuid)"
    "   AND r.member_email = :me"
)


def _state(row: Any) -> str:
    """``awaiting``, ``approved``, ``declined``, ``done`` or ``expired``."""
    if row.decision == store.DECLINE:
        return "declined"
    if row.decision == store.APPROVE and row.status == "completed":
        return "done"
    if not row.live:
        return "expired"
    return "approved" if row.decision == store.APPROVE else "awaiting"


def _request_out(row: Any) -> dict[str, Any]:
    """The wire shape of one request: the server's frame and the stored call.
    The call holds text that the agent wrote, and the card marks it so."""
    request = (from_jsonb(row.content_metadata) or {}).get("request") or {}
    return {
        "request_id": str(row.request_id),
        "run_id": str(row.run_id),
        "state": _state(row),
        "decision": row.decision,
        "source": request.get("source"),
        "app": row.app,
        "subject": request.get("subject") or None,
        "op": request.get("op"),
        "class": request.get("cls"),
        "client_via": row.client_via,
        "card": request.get("card") or {},
        "call": request.get("call") or {},
        "expires_at": request.get("expires_at"),
        "created_at": wire(row.created_at),
        "decided_at": wire(row.decided_at),
    }


async def read_request(
    db: Any, *, org: str, member: str, request_id: str, binding: str | None = None,
) -> dict[str, Any] | None:
    """One request of *member*, or ``None``.

    With a *binding*, the request must be of that binding's connection too:
    an agent reads only the requests of its own connection (AD-16). A
    request of another member, of another org or of another connection is
    ``None``, and the caller answers 404.
    """
    rid = _uuid_or_none(request_id)
    if rid is None:
        return None
    sql = f"{_REQUEST_SELECT} AND e.id = CAST(:rid AS uuid)"
    params: dict[str, Any] = {"org": org, "me": _member(member), "rid": rid}
    if binding is not None:
        sql += " AND r.connection_id IS NOT DISTINCT FROM CAST(:conn AS uuid)"
        params["conn"] = store.binding_connection(binding)
    row = (await db.execute(text(sql), params)).fetchone()
    return _request_out(row) if row is not None else None


async def list_open(db: Any, *, org: str, member: str) -> list[dict[str, Any]]:
    """The requests that wait for *member*: no decision yet and live, newest
    first, at most :data:`LIST_LIMIT`."""
    rows = (await db.execute(text(
        f"{_REQUEST_SELECT} AND r.status = 'awaiting' AND d.decision IS NULL"
        "   AND CAST(e.content_metadata->'request'->>'expires_at' AS timestamptz)"
        "       > clock_timestamp()"
        " ORDER BY e.created_at DESC, e.id DESC LIMIT :limit"
    ), {"org": org, "me": _member(member), "limit": LIST_LIMIT})).fetchall()
    return [_request_out(row) for row in rows]


# ── The one write of an activity on an app run ──────────────────────────────


async def _write_app_activity(
    db: Any,
    run: Any,
    *,
    kind: str,
    event: str,
    actor: str,
    via: str | None,
    body: str | None,
    key: str | None = None,
    options: tuple[agent_runs.OptionIn, ...] | None = None,
    request: dict[str, Any] | None = None,
    selected: str | None = None,
    in_reply_to: str | None = None,
) -> Any:
    """Write one activity on a LOCKED app run, in the caller's transaction,
    and move the run by the status machine of ``agent_runs``.

    The app variant of ``agent_runs._write_activity``: no task comment, no
    bell and no cap of ephemeral rows. A prompt is a person's word, so a run
    with no person present gets 403 here too (AUTO-7a).
    """
    run_id = str(run.id)
    agent_runs._refuse_a_marked_prompt(kind, run_id)
    new_status = agent_runs.transition(run.status, event)
    content: dict[str, Any] = {"body": body}
    content_metadata: dict[str, Any] | None = None
    signal: str | None = None
    signal_meta: dict[str, Any] | None = None
    if kind == "elicitation":
        signal = "select"
        signal_meta = {"options": [o.model_dump(exclude_none=True) for o in options or ()],
                       "allow_multiple": False}
        content_metadata = {"request": dict(request or {})}
    elif kind == "prompt":
        signal = "continue"
        labels = {o.id: o.label for o in OPTIONS}
        content.update({"signal": signal, "selected": [selected],
                        "labels": [labels.get(selected or "", selected)]})
        content_metadata = {"question_id": in_reply_to}
    ts = await agent_runs._clock(db)
    made = (await db.execute(text(
        "INSERT INTO pm_agent_run_activities "
        "(run_id, task_id, app, type, content, content_metadata, ephemeral, signal, "
        " signal_metadata, actor, actor_via, in_reply_to, idempotency_key) "
        "VALUES (CAST(:rid AS uuid), NULL, :app, :type, CAST(:content AS jsonb), "
        "        CAST(:cmeta AS jsonb), false, :signal, CAST(:smeta AS jsonb), "
        "        :actor, :via, CAST(:reply AS uuid), :key) "
        "ON CONFLICT DO NOTHING RETURNING *"
    ), {
        "rid": run_id, "app": run.app, "type": kind,
        "content": json.dumps(content, ensure_ascii=False),
        "cmeta": json.dumps(content_metadata, ensure_ascii=False)
        if content_metadata is not None else None,
        "signal": signal,
        "smeta": json.dumps(signal_meta) if signal_meta is not None else None,
        "actor": actor, "via": via, "reply": in_reply_to, "key": key,
    })).fetchone()
    if made is None:
        raise HTTPException(status_code=409, detail="Send the request again.")
    await agent_runs._move_run(db, run, new_status, ts=ts, user=None, via=None)
    return made


# ── Open (D2.B) ─────────────────────────────────────────────────────────────


def _member(member: str) -> str:
    return str(member or "").strip().lower()


def _uuid_or_none(value: object) -> str | None:
    try:
        return str(uuid.UUID(str(value)))
    except (TypeError, ValueError):
        return None


#: The live requests under one cap: of one connection, or, for a Studio
#: request, of one member and app with no connection (AD-13, D6). The expiry
#: is part of the count, because no sweeper closes an expired run.
_CAP_SQL = (
    "SELECT count(*) FROM pm_agent_runs r"
    "  JOIN pm_agent_run_activities e ON e.run_id = r.id AND e.type = 'elicitation'"
    " WHERE r.app IS NOT NULL AND r.member_email = :me"
    "   AND r.status IN ('awaiting', 'in_progress')"
    "   AND CAST(e.content_metadata->'request'->>'expires_at' AS timestamptz)"
    "       > clock_timestamp()"
    "   AND ((CAST(:conn AS uuid) IS NOT NULL AND r.connection_id = CAST(:conn AS uuid))"
    "        OR (CAST(:conn AS uuid) IS NULL AND r.connection_id IS NULL AND r.app = :app))"
)

#: The first request of one idempotency key, by the hash of its binding.
_REPLAY_SQL = (
    "SELECT r.id AS run_id, e.id AS request_id, e.content_metadata"
    "  FROM pm_agent_runs r"
    "  JOIN pm_agent_run_activities e ON e.run_id = r.id AND e.type = 'elicitation'"
    " WHERE r.app IS NOT NULL AND r.member_email = :me AND r.idempotency_key = :key"
    " ORDER BY e.seq LIMIT 1"
)

_INSERT_APP_RUN_SQL = (
    "INSERT INTO pm_agent_runs "
    "(task_id, app, member_email, connection_id, client_via, status, started_by, "
    " started_at, last_activity_at, last_agent_activity_at, idempotency_key) "
    "VALUES (NULL, :app, :me, CAST(:cid AS uuid), :via, 'created', :via, "
    "        :ts, :ts, :ts, :key) "
    "ON CONFLICT DO NOTHING RETURNING id"
)


async def _replay(db: Any, me: str, key_hash: str, *, app: str, op: str,
                  digest: str) -> Opened | None:
    """The first request of this key, or ``None``. Another call under the
    same key is 409."""
    found = (await db.execute(text(_REPLAY_SQL), {"me": me, "key": key_hash})).fetchone()
    if found is None:
        return None
    request = (from_jsonb(found.content_metadata) or {}).get("request") or {}
    if (request.get("digest"), request.get("app"), request.get("op")) != (digest, app, op):
        raise _refuse(409, KEY_REUSED,
                      "This idempotency_key asked for another change. Send a new key.")
    return Opened(request_id=str(found.request_id), run_id=str(found.run_id),
                  state="awaiting", expires_at=str(request.get("expires_at")),
                  created=False)


async def open_request(
    db: Any,
    *,
    org: str,
    member: str,
    source: str,
    app: str,
    subject: str,
    op: str,
    cls: str,
    call: Mapping[str, Any],
    idempotency_key: str,
    connection_id: str | None,
    via: str,
    title: str = "",
    app_name: str = "",
) -> Opened:
    """Open one app request that waits for *member*, in the caller's tenant
    transaction. Nothing runs.

    *org* is the org of the bound session and *member* is ``user.email``.
    *cls* comes from the manifest or ``OBJECT_OP_CLASSES``. *call* is the
    exact call that the spend gives back. *connection_id* is set for
    ``mcp`` and ``None`` for ``studio``. *title* and *app_name* are the
    server's words for the card, from the manifest.
    """
    me = _member(member)
    org_id = _uuid_or_none(org)
    if source not in store.SOURCES or org_id is None or not me:
        raise ValueError("an app request needs a source, the org and the member")
    if cls not in ASKING_CLASSES:
        raise _refuse(422, CLASS_REFUSED,
                      "Only a change of class B or C waits for the member.")
    if not _APP.fullmatch(app) or not _OP.fullmatch(op) or not _SUBJECT.fullmatch(subject):
        raise ValueError("the app, the op or the subject has a bad shape")
    if not _VIA.fullmatch(via) or not via.startswith(f"{source}:"):
        raise ValueError("the stamp of the client does not match the source")
    conn = _uuid_or_none(connection_id) if connection_id is not None else None
    if (source == "mcp") != (conn is not None):
        raise ValueError("an MCP request names its connection, and a Studio request none")
    bound = (await db.execute(text("SELECT current_setting('app.tenant_id', true)"))).scalar()
    if str(bound or "") != org_id:
        raise ValueError("the org is not the org of the bound session")

    stored = _clean_call(call)
    if _unshowable(stored):
        raise _refuse(422, BAD_ARGS,
                      "The change holds a value that the card cannot show exactly: a whole "
                      "number past 2^53 - 1, a number that is not finite, or U+0000.")
    if _holds_secret_key(stored["body"]) or any(
            any(word in k.lower() for word in SECRET_WORDS) for k, _ in stored["query"]):
        raise _refuse(422, SECRET_IN_PAYLOAD,
                      "The change holds a key that names a secret. Metorite refuses it.")
    if _call_bytes(stored) > MAX_CALL_BYTES:
        raise _refuse(413, PAYLOAD_TOO_LARGE,
                      "The change is too large for the card to show. Make it smaller.")

    binding = (store.mcp_binding(conn) if conn is not None
               else store.studio_binding(me, app, subject))
    digest = store.call_digest(stored, binding)
    key_hash = _key_digest(binding, idempotency_key)

    # One writer at a time under one cap, so the count and the replay hold.
    scope = conn if conn is not None else f"{me}:{app}"
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
                     {"k": f"app_request:{org_id}:{scope}"})
    replay = await _replay(db, me, key_hash, app=app, op=op, digest=digest)
    if replay is not None:
        return replay
    live = int((await db.execute(text(_CAP_SQL), {"me": me, "conn": conn, "app": app}))
               .scalar() or 0)
    if live >= OPEN_CAP:
        raise _refuse(429, TOO_MANY_REQUESTS,
                      "Too many changes wait for the member. Wait for an answer first.")

    ts = await agent_runs._clock(db)
    expires = (ts + REQUEST_TTL).isoformat()
    made = (await db.execute(text(_INSERT_APP_RUN_SQL), {
        "app": app, "me": me, "cid": conn, "via": via, "ts": ts, "key": key_hash,
    })).fetchone()
    if made is None:  # the key index decided a race that the lock missed
        replay = await _replay(db, me, key_hash, app=app, op=op, digest=digest)
        if replay is None:
            raise HTTPException(status_code=409, detail="Send the request again.")
        return replay
    run = await agent_runs._lock_run(db, str(made.id), app=True)
    name = app_name or app
    request = {
        "kind": store.REQUEST_KIND,
        "source": source,
        "app": app,
        "subject": subject,
        "op": op,
        "cls": cls,
        "call": stored,
        "digest": digest,
        "binding": binding,
        "card": {
            "app_name": name,
            "title": title or op,
            "class_line": CLASS_LINES[cls].format(app=name),
            "client": agent_oauth.client_label(via) if source == "mcp" else "Studio",
        },
        "expires_at": expires,
    }
    asked = await _write_app_activity(
        db, run, kind="elicitation", event="app_ask", actor=me, via=via,
        body=REQUEST_BODY, options=OPTIONS, request=request,
    )
    _log.info("app_requests.opened", app=app, op=op, cls=cls, source=source,
              request_id=str(asked.id))
    return Opened(request_id=str(asked.id), run_id=str(made.id), state="awaiting",
                  expires_at=expires, created=True)


# ── Decide ──────────────────────────────────────────────────────────────────


async def decide(
    db: Any,
    *,
    org: str,
    member: str,
    request_id: str,
    decision: str,
    idempotency_key: str,
) -> Decided:
    """The member's answer to one request, in the caller's transaction.

    Only the member of the run decides. The caller checks that the session
    is a member's, with no ``UserContext.agent``. A request of another
    member, or of another org, is 404. A second decision is 409. An approve
    after the expiry is 409, and a decline after the expiry still closes the
    run. The same key again gives the first answer.
    """
    if decision not in (store.APPROVE, store.DECLINE):
        raise ValueError("a decision is approve or decline")
    me = _member(member)
    rid = _uuid_or_none(request_id)
    found = None
    if rid is not None:
        found = (await db.execute(
            text(f"{_REQUEST_SELECT} AND e.id = CAST(:rid AS uuid)"),
            {"org": org, "me": me, "rid": rid},
        )).fetchone()
    if found is None:
        raise HTTPException(status_code=404, detail="Request not found")
    run_id = str(found.run_id)
    run = await agent_runs._lock_run(db, run_id, app=True)
    again = await agent_runs._existing_activity(db, run_id, idempotency_key)
    if again is not None:
        content = from_jsonb(again.content) or {}
        if (again.type != "prompt" or str(again.in_reply_to) != rid
                or (content.get("selected") or [None])[0] != decision):
            raise HTTPException(status_code=409, detail=agent_runs._KEY_USED)
        now = (await db.execute(text(f"{_REQUEST_SELECT} AND e.id = CAST(:rid AS uuid)"),
                                {"org": org, "me": me, "rid": rid})).fetchone()
        return Decided(request_id=rid, run_id=run_id, decision=decision,
                       state=_state(now), created=False)
    # Read again under the lock: a decision that won the race is seen now.
    locked = (await db.execute(text(f"{_REQUEST_SELECT} AND e.id = CAST(:rid AS uuid)"),
                               {"org": org, "me": me, "rid": rid})).fetchone()
    if locked.decision is not None or run.status != "awaiting":
        raise _refuse(409, ALREADY_DECIDED, "This request already has an answer.")
    if decision == store.APPROVE and not locked.live:
        raise _refuse(409, REQUEST_EXPIRED,
                      "This request expired. Decline it to close it, and ask again.")
    await _write_app_activity(
        db, run, kind="prompt",
        event="member_prompt" if decision == store.APPROVE else "app_close",
        actor=me, via=None, body=None, key=idempotency_key, selected=decision,
        in_reply_to=rid,
    )
    now = (await db.execute(text(f"{_REQUEST_SELECT} AND e.id = CAST(:rid AS uuid)"),
                            {"org": org, "me": me, "rid": rid})).fetchone()
    _log.info("app_requests.decided", request_id=rid, decision=decision)
    return Decided(request_id=rid, run_id=run_id, decision=decision,
                   state=_state(now), created=True)


__all__ = [
    "ASKING_CLASSES",
    "CLASS_LINES",
    "LIST_LIMIT",
    "MAX_CALL_BYTES",
    "MAX_SAFE_INT",
    "OPEN_CAP",
    "OPTIONS",
    "REQUEST_BODY",
    "REQUEST_TTL",
    "SECRET_WORDS",
    "Decided",
    "Opened",
    "decide",
    "list_open",
    "open_request",
    "read_request",
]
