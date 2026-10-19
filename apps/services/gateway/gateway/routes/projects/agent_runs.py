"""Projects · agent runs on a task, after Plane (WS-55 EA-12a).

Spec: ``project-docs/specs/external_agent_access.md`` §12.1 (D-EA-10, the
decisions EA12-1 to EA12-13) and row EA-12a of §15.

    GET  /projects/tasks/{task_id}/agent-runs     → {rows}
    POST /projects/tasks/{task_id}/agent-runs     → {run, created}
    GET  /projects/agent-runs                     → {rows, next_cursor}
    GET  /projects/agent-runs/{run_id}            → {run, activities, cursor}
    POST /projects/agent-runs/{run_id}/activities → {activity, run, created}
    POST /projects/agent-runs/{run_id}/finish     → {run, activity}
    POST /projects/tasks/{task_id}/archive-request    → {activity, run, created}
    POST /projects/agent-runs/{run_id}/archive-approval
                                                  → {activity, run, created, archived}

An outside agent works on a task inside a RUN. The run holds typed
ACTIVITIES: ``prompt``, ``thought``, ``action``, ``response``,
``elicitation`` and ``error``. An ``elicitation`` asks a member a question,
and the run waits (``awaiting``) until a member writes a ``prompt``. These
routes are the only writer of ``pm_agent_runs`` and
``pm_agent_run_activities``.

The rules, each one stated in the spec:

* **The flag.** ``agent_runs_enabled`` is off by default, and each route then
  answers 404 (EA12-3).
* **Access is the task's.** Each route loads the task through
  ``core.load_visible_task``, with the agent scope of §7.3 for an agent. A
  task or a run that the caller cannot see answers 404, never 403.
* **The gateway sets every status** (:func:`transition`, EA12-5). Each write
  locks the run row with ``SELECT ... FOR UPDATE`` first. A move that the
  table of §12.1 does not list answers 409.
* **Who writes what.** An agent (a ``UserContext`` with ``.agent`` set by
  branch 0) writes ``thought``, ``action``, ``response``, ``elicitation`` and
  ``error``, and only on a run of its own member. A member writes ``prompt``
  only. Anything else is 422. A ``prompt`` from an agent would forge an
  answer.
* **The tenant, the member and the client come from the context.** No body
  names an organization, a member or a ``via`` (``extra="forbid"``).
* **One comment for each non-ephemeral activity**, through
  ``core.record_activity``, with ``meta`` of ``agent_run_id``,
  ``agent_activity_id`` and ``agent_kind``. ``thought`` and ``action`` are
  ephemeral: they write no comment, and the run keeps the newest 20 only.
* **The question notifies the run's member** (EA12-8): the actor of the
  ``agent_question`` row is the run's ``mcp:<slug>``, so ``notifiable()``
  does not drop the member.
* **The long poll holds no session while it sleeps** (EA12-4). Each read
  opens and closes its own session and checks the task again.
* **The archive with an approval** (WS-55 EA-6g, EA3-33 and EA3-40). An
  agent asks with ``archive-request``. The route writes an elicitation that
  the SERVER types: ``content_metadata.request`` holds the kind and the
  expiry, and the server fixes the two options. No body can write
  ``content_metadata``, so a free question with an option ``approve`` is
  never a request. Only the run's member answers, through
  ``archive-approval``, which is NOT in ``MCP_ROUTES``. An approve writes
  the answer and archives the task in ONE transaction, through
  ``tasks.archive_task_in``, and never the subtasks. The generic answer
  route refuses a ``prompt`` on a typed request.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from datetime import datetime, timedelta
from typing import Any, Literal

from acb_auth import UserContext, agent_oauth, get_current_user
from acb_auth.agent_oauth import ACTOR_VIA_PATTERN, PROJECTS_WRITE, actor_via_for
from acb_common import get_logger
from fastapi import Depends, HTTPException, Query, Response
from gateway.routes.projects.core import (
    TaskModel,
    _tenant_session,
    actor,
    emit,
    from_jsonb,
    load_visible_task,
    record_activity,
    resolve_visibility,
    router,
    row_to_dict,
    task_visibility_clause,
    wire,
)
from gateway.routes.projects.filters import (
    AGENT_RUN_OPEN_STATUSES,
    AGENT_RUN_STATE_SQL,
    agent_runs_enabled,
    attach_agent_state,
)
from gateway.routes.projects.notifications import excerpt_of, notify, task_audience
from gateway.routes.projects.tasks import archive_task_in
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

_log = get_logger("gateway.projects.agent_runs")

# ── The vocabulary ──────────────────────────────────────────────────────────

#: The eight statuses of Plane's ``AgentRun``. ``stale`` is never stored: it
#: is derived on read (:data:`AGENT_RUN_STATE_SQL`). The CHECK of the
#: migration lists the same eight.
RUN_STATUSES: tuple[str, ...] = (
    "created", "in_progress", "awaiting", "completed",
    "stopping", "stopped", "failed", "stale",
)
OPEN_STATUSES: frozenset[str] = frozenset(AGENT_RUN_OPEN_STATUSES)
#: Terminal for the agent. A member ``prompt`` on ``completed`` opens it again.
TERMINAL_STATUSES: frozenset[str] = frozenset({"completed", "stopped", "failed"})

ACTIVITY_TYPES: tuple[str, ...] = (
    "prompt", "thought", "action", "response", "elicitation", "error",
)
AGENT_TYPES: frozenset[str] = frozenset(
    {"thought", "action", "response", "elicitation", "error"},
)
EPHEMERAL_TYPES: frozenset[str] = frozenset({"thought", "action"})

#: The run keeps this many ephemeral rows. The next one deletes the oldest.
EPHEMERAL_CAP = 20
#: The longest ``body`` of an activity, in characters.
MAX_BODY = 20_000
#: ``wait_s`` of the long poll, at most.
MAX_WAIT_S = 20
#: The long poll reads again after this many seconds.
POLL_S = 2.0
#: The most activities that one read returns.
MAX_ACTIVITIES = 200
#: ``action`` parameters: a flat map of strings, as in Plane.
MAX_PARAMETERS = 50
MAX_PARAMETER_KEY = 100
MAX_PARAMETER_VALUE = 2_000

_OPTION_ID = r"^[A-Za-z0-9_-]{1,64}$"
_ACTION_NAME = re.compile(r"^[A-Za-z0-9_.:/-]{1,200}$")

# ── The archive with an approval (EA-6g, EA3-40) ────────────────────────────

#: The ``kind`` of a typed archive request, in ``content_metadata.request``.
ARCHIVE_REQUEST_KIND = "archive_task"
#: A typed archive request lives this long (EA3-40 rule 5). No sweeper: the
#: approval route checks ``expires_at`` when it reads the request.
ARCHIVE_REQUEST_TTL = timedelta(hours=24)
#: The two decisions of the member, and the option ids of the request.
ARCHIVE_APPROVE = "approve"
ARCHIVE_DECLINE = "decline"
#: The text of the request, fixed by the server. The agent sends no text.
ARCHIVE_REQUEST_BODY = (
    "The agent asks to archive this task. The subtasks stay where they are. "
    "Only the member of this agent run can approve or decline."
)

#: The sleep of the long poll. A name of its own, so the fence of R11 can
#: wrap it and assert that no session is open while it runs.
_sleep = asyncio.sleep


# ── The flag (EA12-3) ───────────────────────────────────────────────────────


async def require_agent_runs() -> None:
    """404 while ``agent_runs_enabled`` is off, as
    ``agent_access._require_enabled`` does."""
    if not agent_runs_enabled():
        raise HTTPException(status_code=404, detail="Not Found")


_ENABLED = [Depends(require_agent_runs)]


# ── The request shapes ──────────────────────────────────────────────────────


class OptionIn(BaseModel):
    """One choice of a ``select`` (Plane's shape, plus ``recommended``)."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=_OPTION_ID)
    label: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=1_000)
    recommended: bool = False


class StartIn(BaseModel):
    """``POST /projects/tasks/{id}/agent-runs``.

    An agent sends ``external_link`` and ``idempotency_key`` only. A member
    (EA-12d) also sends ``connection_id``, one of their own live agent
    connections, and ``body``, the first ``prompt``. The tenant and the member
    never come from this body (R5).
    """

    model_config = ConfigDict(extra="forbid")

    external_link: str | None = Field(default=None, max_length=2_048)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)
    connection_id: str | None = None
    body: str | None = Field(default=None, max_length=MAX_BODY)


class ActivityIn(BaseModel):
    """``POST /projects/agent-runs/{id}/activities``.

    ``options`` makes the ``select`` signal of an ``elicitation``.
    ``selected`` answers it, on a ``prompt``. ``signal`` is the member's
    ``continue`` (the default) or ``stop``. The server sets ``select`` and
    ``in_reply_to`` itself (EA12-7).
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["prompt", "thought", "action", "response", "elicitation", "error"]
    idempotency_key: str = Field(min_length=1, max_length=128)
    body: str | None = Field(default=None, max_length=MAX_BODY)
    options: list[OptionIn] | None = Field(default=None, min_length=2, max_length=10)
    allow_multiple: bool | None = None
    selected: list[str] | None = Field(default=None, min_length=1, max_length=10)
    action: str | None = Field(default=None, min_length=1, max_length=200)
    parameters: dict[str, str] | None = None
    signal: Literal["continue", "stop"] | None = None


class FinishIn(BaseModel):
    """``POST /projects/agent-runs/{id}/finish``. ``summary`` writes a final
    ``response`` with ``idempotency_key``."""

    model_config = ConfigDict(extra="forbid")

    summary: str | None = Field(default=None, max_length=MAX_BODY)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)


class ArchiveRequestIn(BaseModel):
    """``POST /projects/tasks/{id}/archive-request`` (EA-6g). The key only.
    The target is the task of the path, the text and the options are the
    server's, and the expiry is the server's clock (EA3-40 rule 1)."""

    model_config = ConfigDict(extra="forbid")

    idempotency_key: str = Field(min_length=1, max_length=128)


class ArchiveApprovalIn(BaseModel):
    """``POST /projects/agent-runs/{id}/archive-approval`` (EA-6g). The run's
    member answers a typed archive request (EA3-40 rule 2)."""

    model_config = ConfigDict(extra="forbid")

    decision: Literal["approve", "decline"]
    idempotency_key: str = Field(min_length=1, max_length=128)


#: The two options of every typed archive request. The server fixes them,
#: and no input names them (EA3-40 rule 1).
ARCHIVE_OPTIONS: tuple[OptionIn, ...] = (
    OptionIn(id=ARCHIVE_APPROVE, label="Approve"),
    OptionIn(id=ARCHIVE_DECLINE, label="Decline"),
)


def archive_request_of(row: Any) -> dict[str, Any] | None:
    """The typed archive request of an elicitation row, or ``None``.

    Only :func:`request_task_archive` writes ``content_metadata.request``. No
    request body can carry it (``extra="forbid"``), so an option id
    ``approve`` on a free question is never a request (EA3-40, G2).
    """
    if row is None or getattr(row, "type", None) != "elicitation":
        return None
    request = (from_jsonb(row.content_metadata) or {}).get("request")
    if isinstance(request, dict) and request.get("kind") == ARCHIVE_REQUEST_KIND:
        return request
    return None


def _request_expired(request: dict[str, Any], at: Any) -> bool:
    """True when *request* expired at the instant *at*. A stamp that does
    not parse reads as expired, so it never approves."""
    try:
        expires = datetime.fromisoformat(str(request.get("expires_at")))
    except ValueError:
        return True
    if expires.tzinfo is None:
        return True
    return bool(at >= expires)


# ── The status machine (EA12-5) ─────────────────────────────────────────────

#: ``event -> {from: to}``. A pair that is not here answers 409. ``stale`` is
#: never stored, so it is read as ``in_progress`` where it occurs.
_MOVES: dict[str, dict[str, str]] = {
    # A thought, an action or a response keeps an open status. Not a 409.
    "agent_activity": {
        "created": "in_progress", "in_progress": "in_progress",
        "awaiting": "awaiting", "stopping": "stopped",
    },
    # A second question while one waits is a 409.
    "agent_elicitation": {
        "created": "awaiting", "in_progress": "awaiting", "stopping": "stopped",
    },
    "agent_error": {
        "created": "failed", "in_progress": "failed", "awaiting": "failed",
        "stopping": "stopped",
    },
    "agent_finish": {
        "in_progress": "completed", "awaiting": "completed", "stopping": "stopped",
    },
    "member_prompt": {
        "awaiting": "in_progress", "in_progress": "in_progress",
        "completed": "in_progress",
    },
    "member_stop": {
        "created": "stopping", "in_progress": "stopping", "awaiting": "stopping",
    },
    # The member's first instruction, inside the start of a `created` run
    # (EA-12d). Only `start_agent_run` passes it. No route takes it as input.
    "member_start": {"created": "created"},
}


def transition(status: str, event: str) -> str:
    """The status after *event* on a run in *status*, or a 409.

    The table of §12.1 with the rules of EA12-5: a Stop wins (an agent write
    on ``stopping`` gives ``stopped``), any agent write on a terminal run is
    409, and a member ``prompt`` on ``created``, ``stopping``, ``failed`` or
    ``stopped`` is 409.
    """
    current = "in_progress" if status == "stale" else status
    moves = _MOVES.get(event)
    if moves is None:
        raise ValueError(f"unknown event {event!r}")
    to = moves.get(current)
    if to is None:
        raise HTTPException(
            status_code=409,
            detail=(
                "The agent has not picked this run up yet. Wait for it to start, then reply."
                if status == "created" and event == "member_prompt"
                else f"This agent run is {status}. It cannot take this activity."
            ),
        )
    return to


def _event_of(kind: str, signal: str | None) -> str:
    if kind == "prompt":
        return "member_stop" if signal == "stop" else "member_prompt"
    if kind == "elicitation":
        return "agent_elicitation"
    if kind == "error":
        return "agent_error"
    return "agent_activity"


# ── Small helpers ───────────────────────────────────────────────────────────


def _parse_uuid(value: str, what: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=f"{what} not found") from exc


def _member(user: UserContext) -> str:
    return actor(user).strip().lower()


def _agent_via(user: UserContext) -> str | None:
    """``mcp:<slug>`` of an agent caller, or ``None`` for a person.

    An agent write needs ``projects:write`` (branch 0 checks it for each pair
    of ``MCP_WRITE_ROUTES`` too) and a stamp of the right shape.
    """
    agent = getattr(user, "agent", None)
    if agent is None:
        return None
    via = str(getattr(agent, "actor_via", "") or "")
    if not (via.startswith("mcp:") and ACTOR_VIA_PATTERN.fullmatch(via)):
        raise HTTPException(status_code=403, detail="This agent connection has no client stamp.")
    return via


def _require_write_scope(user: UserContext) -> None:
    agent = getattr(user, "agent", None)
    if agent is not None and PROJECTS_WRITE not in tuple(agent.scopes or ()):
        raise HTTPException(status_code=403, detail="This connection may not write.")


def _clean_body(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.replace("\x00", "").strip()
    return cleaned or None


def _check_link(value: str | None) -> str | None:
    link = (value or "").strip()
    if not link:
        return None
    if not link.lower().startswith("https://") or any(ch.isspace() for ch in link):
        raise HTTPException(status_code=422, detail="external_link must be an https link.")
    return link


def _jsonb(value: Any) -> str | None:
    return None if value is None else json.dumps(value, ensure_ascii=False)


def _activity_out(row: Any) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "run_id": str(row.run_id),
        "task_id": str(row.task_id),
        "seq": int(row.seq),
        "type": row.type,
        "content": from_jsonb(row.content) or {},
        "content_metadata": from_jsonb(row.content_metadata),
        "ephemeral": bool(row.ephemeral),
        "signal": row.signal,
        "signal_metadata": from_jsonb(row.signal_metadata),
        "actor": row.actor,
        "actor_via": row.actor_via,
        "in_reply_to": wire(row.in_reply_to),
        "pm_activity_id": wire(row.pm_activity_id),
        "idempotency_key": row.idempotency_key,
        "created_at": wire(row.created_at),
    }


_RUN_SELECT = f"SELECT r.*, {AGENT_RUN_STATE_SQL} AS state FROM pm_agent_runs r"

_OPEN_ELICITATIONS_SQL = """
SELECT DISTINCT ON (a.run_id) a.*
  FROM pm_agent_run_activities a
 WHERE a.run_id = ANY(CAST(:ids AS uuid[]))
   AND a.type = 'elicitation'
   AND NOT EXISTS (SELECT 1 FROM pm_agent_run_activities p
                    WHERE p.run_id = a.run_id AND p.in_reply_to = a.id)
 ORDER BY a.run_id, a.seq DESC
"""

_LATEST_EPHEMERAL_SQL = """
SELECT DISTINCT ON (a.run_id) a.*
  FROM pm_agent_run_activities a
 WHERE a.run_id = ANY(CAST(:ids AS uuid[])) AND a.ephemeral
 ORDER BY a.run_id, a.seq DESC
"""


async def _runs_out(db: Any, rows: list[Any]) -> list[dict[str, Any]]:
    """The wire shape of each run, with its derived ``state``, its open
    elicitation and its newest ephemeral line. Two queries for the list."""
    ids = [str(r.id) for r in rows]
    questions: dict[str, dict[str, Any]] = {}
    lines: dict[str, dict[str, Any]] = {}
    if ids:
        for found in (await db.execute(text(_OPEN_ELICITATIONS_SQL), {"ids": ids})).fetchall():
            questions[str(found.run_id)] = _activity_out(found)
        for found in (await db.execute(text(_LATEST_EPHEMERAL_SQL), {"ids": ids})).fetchall():
            lines[str(found.run_id)] = _activity_out(found)
    out: list[dict[str, Any]] = []
    for row in rows:
        key = str(row.id)
        out.append({
            "id": key,
            "task_id": str(row.task_id),
            "member_email": row.member_email,
            "connection_id": wire(row.connection_id),
            "client_via": row.client_via,
            # EA-13d: the curated name and the verified host of the stamp, for
            # the run pill "<client_name> · <host>". The name comes from the one
            # map `CLIENT_LABELS`, never from the self-declared client name.
            "client_name": agent_oauth.client_label(row.client_via),
            "client_host": agent_oauth.client_host(row.client_via),
            "status": row.status,
            "state": row.state,
            "started_by": row.started_by,
            "created_at": wire(row.created_at),
            "started_at": wire(row.started_at),
            "ended_at": wire(row.ended_at),
            "stopped_at": wire(row.stopped_at),
            "stopped_by": row.stopped_by,
            "external_link": row.external_link,
            "last_activity_at": wire(row.last_activity_at),
            "last_agent_activity_at": wire(row.last_agent_activity_at),
            "error_metadata": from_jsonb(row.error_metadata),
            "open_elicitation": questions.get(key) if row.status in OPEN_STATUSES else None,
            "latest_ephemeral": lines.get(key),
        })
    return out


async def _load_run(db: Any, run_id: str) -> Any:
    row = (await db.execute(
        text(f"{_RUN_SELECT} WHERE r.id = CAST(:rid AS uuid)"), {"rid": run_id},
    )).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Agent run not found")
    return row


async def _load_visible_run(db: Any, user: UserContext, run_id: str) -> Any:
    """One run whose task the caller may see, or 404 for both "no such run"
    and "not yours" (R6)."""
    row = await _load_run(db, run_id)
    vis = await resolve_visibility(db, user)
    try:
        await load_visible_task(db, vis, str(row.task_id))
    except HTTPException as exc:
        raise HTTPException(status_code=404, detail="Agent run not found") from exc
    return row


async def _lock_run(db: Any, run_id: str) -> Any:
    """The run row, locked for this transaction (EA12-5)."""
    await db.execute(
        text("SELECT id FROM pm_agent_runs WHERE id = CAST(:rid AS uuid) FOR UPDATE"),
        {"rid": run_id},
    )
    return await _load_run(db, run_id)


async def _clock(db: Any) -> Any:
    """One instant, read after the lock. ``now()`` is the start of the
    transaction, and a write that waited on the lock would stamp a time
    before the write it waited for. So ``answered`` would lie."""
    return (await db.execute(text("SELECT clock_timestamp()"))).scalar()


# ── Validation of one activity ──────────────────────────────────────────────


def _validate(payload: ActivityIn, *, agent: bool) -> None:
    """Refuse an activity that its writer may not send, or a field that its
    type does not take. Every refusal is 422 (R2)."""
    kind = payload.type
    if agent and kind not in AGENT_TYPES:
        raise HTTPException(status_code=422, detail="An agent cannot write a prompt.")
    if not agent and kind != "prompt":
        raise HTTPException(status_code=422, detail="A member writes a prompt only.")
    _validate_fields(payload)
    body = _clean_body(payload.body)
    if kind in ("thought", "response", "elicitation", "error") and not body:
        raise HTTPException(status_code=422, detail=f"A {kind} needs a body.")
    if kind == "action":
        _validate_action(payload)
    if kind == "elicitation" and payload.options is not None:
        ids = [o.id for o in payload.options]
        if len(set(ids)) != len(ids):
            raise HTTPException(status_code=422, detail="Each option needs its own id.")
        if sum(1 for o in payload.options if o.recommended) > 1:
            raise HTTPException(status_code=422, detail="At most one option is recommended.")
    if kind == "prompt":
        if payload.signal == "stop":
            if payload.selected is not None:
                raise HTTPException(status_code=422, detail="A stop carries no selected.")
        elif not body and not payload.selected:
            raise HTTPException(status_code=422, detail="A prompt needs selected, a body or both.")


def _validate_fields(payload: ActivityIn) -> None:
    """Each optional field belongs to one type."""
    kind = payload.type
    if payload.signal is not None and kind != "prompt":
        raise HTTPException(status_code=422, detail="Only a prompt carries a signal.")
    if (payload.options is not None or payload.allow_multiple is not None) and kind != "elicitation":
        raise HTTPException(status_code=422, detail="Only an elicitation carries options.")
    if payload.selected is not None and kind != "prompt":
        raise HTTPException(status_code=422, detail="Only a prompt carries selected.")
    if (payload.action is not None or payload.parameters is not None) and kind != "action":
        raise HTTPException(status_code=422, detail="Only an action carries action and parameters.")


def _validate_action(payload: ActivityIn) -> None:
    """A tool name, and a flat map of strings that is not too large."""
    if not payload.action or not _ACTION_NAME.fullmatch(payload.action):
        raise HTTPException(status_code=422, detail="An action needs an action name.")
    params = payload.parameters or {}
    if len(params) > MAX_PARAMETERS or any(
        not key or len(key) > MAX_PARAMETER_KEY or len(value) > MAX_PARAMETER_VALUE
        for key, value in params.items()
    ):
        raise HTTPException(status_code=422, detail="The action parameters are too large.")


def _comment_body(kind: str, content: dict[str, Any], signal_meta: dict | None) -> str:
    """The Markdown of the timeline comment. An elicitation lists its options,
    so a reader with no card still sees the choices."""
    body = str(content.get("body") or "")
    if kind == "elicitation":
        lines = [body]
        options = (signal_meta or {}).get("options") or []
        if options:
            many = (signal_meta or {}).get("allow_multiple")
            lines.append("")
            lines.append("Choose one or more:" if many else "Choose one:")
            for option in options:
                line = f"- **{option['label']}**"
                if option.get("description"):
                    line += f" — {option['description']}"
                if option.get("recommended"):
                    line += " (recommended)"
                lines.append(line)
        return "\n".join(lines)
    if kind == "error":
        return f"**Agent error:** {body}"
    if kind == "prompt":
        if content.get("signal") == "stop":
            return "Stopped the agent run." + (f"\n\n{body}" if body else "")
        labels = content.get("labels") or []
        answer = f"Answered: {', '.join(labels)}" if labels else ""
        return "\n\n".join(part for part in (answer, body) if part)
    return body


# ── The one write path of an activity ───────────────────────────────────────


async def _existing_activity(db: Any, run_id: str, key: str | None) -> Any | None:
    if not key:
        return None
    return (await db.execute(
        text(
            "SELECT * FROM pm_agent_run_activities "
            " WHERE run_id = CAST(:rid AS uuid) AND idempotency_key = :key"
        ),
        {"rid": run_id, "key": key},
    )).fetchone()


async def _newest_open_question(db: Any, run_id: str) -> Any | None:
    """The newest elicitation of the run that no prompt answers (EA12-7)."""
    return (await db.execute(
        text(
            "SELECT a.* FROM pm_agent_run_activities a "
            " WHERE a.run_id = CAST(:rid AS uuid) AND a.type = 'elicitation' "
            "   AND NOT EXISTS (SELECT 1 FROM pm_agent_run_activities p "
            "                    WHERE p.run_id = a.run_id AND p.in_reply_to = a.id) "
            " ORDER BY a.seq DESC LIMIT 1"
        ),
        {"rid": run_id},
    )).fetchone()


async def _has_answered_select(db: Any, run_id: str) -> bool:
    """Whether the newest elicitation of the run is a ``select`` that a
    prompt already answers."""
    newest = (await db.execute(
        text(
            "SELECT a.signal, EXISTS (SELECT 1 FROM pm_agent_run_activities p "
            "                          WHERE p.run_id = a.run_id AND p.in_reply_to = a.id) "
            "       AS answered "
            "  FROM pm_agent_run_activities a "
            " WHERE a.run_id = CAST(:rid AS uuid) AND a.type = 'elicitation' "
            " ORDER BY a.seq DESC LIMIT 1"
        ),
        {"rid": run_id},
    )).fetchone()
    return newest is not None and newest.signal == "select" and bool(newest.answered)


async def _answer(
    db: Any, run_id: str, content: dict[str, Any], selected: list[str] | None,
    *, typed: bool = False, status: str | None = None,
) -> tuple[str | None, dict[str, Any] | None]:
    """``(in_reply_to, content_metadata)`` of a member prompt (EA12-7).

    The server sets ``in_reply_to`` to the newest unanswered elicitation, and
    never takes it from input. ``selected`` is valid only on a ``select``: the
    ids must be options of that question, one id unless ``allow_multiple``
    (R3). The labels go into *content*, so a reader gets them with the ids.

    A typed archive request (EA-6g) takes an answer ONLY from the approval
    route, which passes *typed*. A generic prompt with ``selected`` gets 422
    (EA3-40 rule 4). While the run is ``awaiting`` (*status*), a generic
    prompt with a body only gets 409: it would close the request, and only
    the run's member may close it (rule 3). On a run that no longer waits,
    or after the request expired (fix round 1), the request is stale. A
    reply then answers nothing and moves the run on, so an expired request
    never holds a run in ``awaiting``.
    """
    question = await _newest_open_question(db, run_id)
    request = archive_request_of(question)
    if typed and request is None:
        raise HTTPException(status_code=409, detail="This run waits for no archive request.")
    if not typed and request is not None:
        if selected is not None:
            raise HTTPException(
                status_code=422,
                detail="Answer an archive request with Approve or Decline on the task.",
            )
        if status == "awaiting" and not _request_expired(request, await _clock(db)):
            raise HTTPException(
                status_code=409,
                detail="This agent run waits for an archive approval. Only its member answers it.",
            )
        question = None
    reply = str(question.id) if question is not None else None
    if selected is None:
        return reply, None
    if question is None and await _has_answered_select(db, run_id):
        # Two members answered at once, and the other one won. A retry
        # cannot help, so this is a conflict and not a bad request.
        raise HTTPException(status_code=409, detail="This question already has an answer.")
    meta = from_jsonb(question.signal_metadata) if question is not None else None
    if question is None or question.signal != "select" or not meta:
        raise HTTPException(
            status_code=422,
            detail="selected answers a question with options, and none is open.",
        )
    by_id = {o["id"]: o for o in meta.get("options") or []}
    if len(set(selected)) != len(selected) or any(s not in by_id for s in selected):
        raise HTTPException(status_code=422, detail="selected names an id that is not an option.")
    if len(selected) > 1 and not meta.get("allow_multiple"):
        raise HTTPException(status_code=422, detail="This question takes one option.")
    content["selected"] = list(selected)
    content["labels"] = [by_id[s]["label"] for s in selected]
    return reply, {"question_id": str(question.id)}


class _Written:
    """What one write did, for the response and the event after commit."""

    def __init__(self, activity: Any, created: bool, commented: bool) -> None:
        self.activity = activity
        self.created = created
        self.commented = commented


async def _write_activity(
    db: Any,
    run: Any,
    user: UserContext,
    *,
    kind: str,
    key: str | None,
    body: str | None,
    options: list[OptionIn] | None = None,
    allow_multiple: bool = False,
    selected: list[str] | None = None,
    action: str | None = None,
    parameters: dict[str, str] | None = None,
    signal: str | None = None,
    event: str | None = None,
    request: dict[str, Any] | None = None,
    typed: bool = False,
    extra: dict[str, Any] | None = None,
) -> _Written:
    """Write one activity on a LOCKED run, inside the caller's transaction.

    The idempotency check runs first, so a replay writes nothing and moves no
    status (R5). Then the status machine, the row, the cap of ephemeral rows,
    the comment, the notifications and the run's new status.

    EA-6g. *request* types an elicitation as an archive request: it goes into
    ``content_metadata``, and the question notifies the run's member only.
    *typed* lets a prompt answer such a request, and only the approval route
    passes it. *extra* adds the server's own keys to ``content``.
    """
    run_id = str(run.id)
    task_id = str(run.task_id)
    found = await _existing_activity(db, run_id, key)
    if found is not None:
        return _Written(found, False, False)

    via = _agent_via(user)
    is_agent = via is not None
    me = _member(user)
    if is_agent and run.member_email != me:
        raise HTTPException(status_code=403, detail="This agent run belongs to another member.")

    new_status = transition(run.status, event or _event_of(kind, signal))

    content: dict[str, Any] = {"body": body}
    content_metadata: dict[str, Any] | None = None
    signal_out: str | None = None
    signal_meta: dict[str, Any] | None = None
    in_reply_to: str | None = None
    if kind == "elicitation" and options:
        signal_out = "select"
        signal_meta = {
            "options": [o.model_dump(exclude_none=True) for o in options],
            "allow_multiple": bool(allow_multiple),
        }
        if request is not None:
            content_metadata = {"request": dict(request)}
    elif kind == "action":
        content["action"] = action
        content["parameters"] = dict(parameters or {})
    elif kind == "prompt":
        signal_out = signal or "continue"
        content["signal"] = signal_out
        if signal_out != "stop":
            in_reply_to, content_metadata = await _answer(
                db, run_id, content, selected, typed=typed, status=run.status,
            )
    if extra:
        content.update(extra)

    ts = await _clock(db)
    ephemeral = kind in EPHEMERAL_TYPES
    made = (await db.execute(
        text(
            "INSERT INTO pm_agent_run_activities "
            "(run_id, task_id, type, content, content_metadata, ephemeral, signal, "
            " signal_metadata, actor, actor_via, in_reply_to, idempotency_key) "
            "VALUES (CAST(:rid AS uuid), CAST(:tid AS uuid), :type, CAST(:content AS jsonb), "
            "        CAST(:cmeta AS jsonb), :ephemeral, :signal, CAST(:smeta AS jsonb), "
            "        :actor, :via, CAST(:reply AS uuid), :key) "
            "ON CONFLICT DO NOTHING RETURNING *"
        ),
        {
            "rid": run_id, "tid": task_id, "type": kind,
            "content": _jsonb(content), "cmeta": _jsonb(content_metadata),
            "ephemeral": ephemeral, "signal": signal_out, "smeta": _jsonb(signal_meta),
            "actor": me, "via": via, "reply": in_reply_to, "key": key,
        },
    )).fetchone()
    if made is None:  # the same key, written between the check and the insert
        again = await _existing_activity(db, run_id, key)
        if again is None:
            raise HTTPException(status_code=409, detail="Write the activity again.")
        return _Written(again, False, False)

    if ephemeral:
        # The cap: the 21st ephemeral row deletes the oldest (R7).
        await db.execute(
            text(
                "DELETE FROM pm_agent_run_activities "
                " WHERE run_id = CAST(:rid AS uuid) AND ephemeral "
                "   AND id NOT IN (SELECT id FROM pm_agent_run_activities "
                "                   WHERE run_id = CAST(:rid AS uuid) AND ephemeral "
                "                   ORDER BY seq DESC LIMIT :cap)"
            ),
            {"rid": run_id, "cap": EPHEMERAL_CAP},
        )

    commented = not ephemeral
    if commented:
        made = await _comment(
            db, run, made, author=me, via=via,
            text_=_comment_body(kind, content, signal_meta) or kind,
            question=(kind == "elicitation" and new_status == "awaiting"),
            member_only=request is not None,
        )

    await _move_run(
        db, run, new_status, ts=ts, user=user, via=via,
        error_body=body if kind == "error" else None,
        error_activity=str(made.id) if kind == "error" else None,
        stop=(kind == "prompt" and signal_out == "stop"),
    )
    return _Written(made, True, commented)


async def _comment(
    db: Any, run: Any, made: Any, *, author: str, via: str | None, text_: str,
    question: bool, member_only: bool = False,
) -> Any:
    """The timeline comment of one non-ephemeral activity, and its bell rows.

    One ``comment`` through ``record_activity``, authored by the member, with
    ``meta.via`` naming the client for an agent. A question that sets
    ``awaiting`` notifies the run's member and ``task_audience()`` with the
    kind ``agent_question`` and the CLIENT as the actor (EA12-8). Every other
    comment sends the ``comment`` kind to ``task_audience()``, as
    ``add_comment`` does, with no mention pass.

    A typed archive request (EA-6g, *member_only*) notifies the run's member
    only, because nobody else may answer it (EA12-8 as amended by EA3-40).
    """
    task_id = str(run.task_id)
    meta: dict[str, Any] = {
        "agent_run_id": str(run.id),
        "agent_activity_id": str(made.id),
        "agent_kind": made.type,
    }
    if via:
        meta["via"] = via
    comment = await record_activity(
        db, activity_type="comment", created_by=author, task_id=task_id,
        body=text_, meta=meta,
    )
    linked = (await db.execute(
        text(
            "UPDATE pm_agent_run_activities SET pm_activity_id = CAST(:pm AS uuid) "
            " WHERE id = CAST(:aid AS uuid) RETURNING *"
        ),
        {"pm": str(comment.id), "aid": str(made.id)},
    )).fetchone()
    audience = [] if member_only else await task_audience(db, task_id)
    snippet = excerpt_of(text_)
    if question:
        await notify(
            db, recipients=[run.member_email, *audience], kind="agent_question",
            task_id=task_id, actor_id=run.client_via, activity_id=str(comment.id),
            excerpt=snippet,
        )
    else:
        await notify(
            db, recipients=audience, kind="comment", task_id=task_id,
            actor_id=author, activity_id=str(comment.id), excerpt=snippet,
        )
    return linked


async def _move_run(
    db: Any, run: Any, new_status: str, *, ts: Any, user: UserContext, via: str | None,
    error_body: str | None = None, error_activity: str | None = None, stop: bool = False,
) -> None:
    """Set the run's status and stamps. Column names are fixed here, and
    only the values are bound."""
    sets = ["status = :status", "last_activity_at = :ts"]
    params: dict[str, Any] = {"rid": str(run.id), "status": new_status, "ts": ts}
    if via is not None:
        agent = getattr(user, "agent", None)
        sets.append("last_agent_activity_at = :ts")
        sets.append("connection_id = CAST(:cid AS uuid)")
        params["cid"] = str(agent.connection_id) if agent is not None else None
    if new_status == "in_progress" and run.started_at is None:
        sets.append("started_at = :ts")
    if new_status in TERMINAL_STATUSES:
        sets.append("ended_at = :ts")
    elif run.status in TERMINAL_STATUSES:
        sets.append("ended_at = NULL")
    if stop:
        sets.append("stopped_at = :ts")
        sets.append("stopped_by = :stopped_by")
        params["stopped_by"] = _member(user)
    if new_status == "failed":
        sets.append("error_metadata = CAST(:err AS jsonb)")
        params["err"] = _jsonb({"activity_id": error_activity, "body": error_body})
    await db.execute(
        text(f"UPDATE pm_agent_runs SET {', '.join(sets)} WHERE id = CAST(:rid AS uuid)"),
        params,
    )


# ── The routes ──────────────────────────────────────────────────────────────


@router.get("/tasks/{task_id}/agent-runs", dependencies=_ENABLED)
async def list_task_agent_runs(
    task_id: str, user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """The runs of one task, newest first, each with its derived state and
    its open elicitation."""
    task_id = _parse_uuid(task_id, "Task")
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        await load_visible_task(db, vis, task_id)
        rows = (await db.execute(
            text(
                f"{_RUN_SELECT} WHERE r.task_id = CAST(:tid AS uuid) "
                " ORDER BY r.created_at DESC, r.id DESC LIMIT 50"
            ),
            {"tid": task_id},
        )).fetchall()
        return {"rows": await _runs_out(db, list(rows))}


_INSERT_RUN_SQL = (
    "INSERT INTO pm_agent_runs "
    "(task_id, member_email, connection_id, client_via, status, started_by, "
    " started_at, external_link, last_activity_at, last_agent_activity_at, "
    " idempotency_key) "
    "VALUES (CAST(:tid AS uuid), :me, CAST(:cid AS uuid), :via, :status, :by, "
    "        :started, :link, :ts, :agent_ts, :key) "
    "ON CONFLICT DO NOTHING RETURNING id"
)


#: The ONE open-run read of EA12-6: the open run of a task for a member and a
#: client stamp. The start, the archive request and the claim read it.
#:
#: It matches the stamp of the caller only. An open run under the legacy host
#: stamp ``mcp:<host>`` is not found (H-356 closed the window of EA-13a fix
#: round 1, spec §12.2 item 1).
_OPEN_RUN_SQL = (
    f"{_RUN_SELECT} WHERE r.task_id = CAST(:tid AS uuid) AND r.member_email = :me "
    "   AND r.status = ANY(:open) AND r.client_via = :via "
    " ORDER BY r.created_at DESC LIMIT 1"
)


def _open_run_args(task_id: str, me: str, via: str) -> dict[str, Any]:
    """The binds of :data:`_OPEN_RUN_SQL`."""
    return {"tid": task_id, "me": me, "via": via,
            "open": list(AGENT_RUN_OPEN_STATUSES)}


async def _replay_start(db: Any, me: str, key: str | None, task_id: str) -> dict | None:
    """The first answer of a start that carried this ``idempotency_key``, or
    None. A key that started a run on another task is a 409."""
    if not key:
        return None
    by_key = (await db.execute(
        text(f"{_RUN_SELECT} WHERE r.member_email = :me AND r.idempotency_key = :key"),
        {"me": me, "key": key},
    )).fetchone()
    if by_key is None:
        return None
    if str(by_key.task_id) != task_id:
        raise HTTPException(status_code=409, detail="This idempotency_key started another run.")
    return {"run": (await _runs_out(db, [by_key]))[0], "created": False}


@router.post("/tasks/{task_id}/agent-runs", status_code=201, dependencies=_ENABLED)
async def start_agent_run(
    task_id: str, payload: StartIn, response: Response,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Start a run. An agent gets ``in_progress``. A member gets ``created``
    and sends the first ``prompt`` in ``body`` (EA-12d).

    201 with ``created: true``, or 200 with ``created: false`` and the run
    that the key or the open-run rule found (EA12-6).
    """
    task_id = _parse_uuid(task_id, "Task")
    _require_write_scope(user)
    via = _agent_via(user)
    me = _member(user)
    link = _check_link(payload.external_link)
    body = _clean_body(payload.body)
    if via is not None and (payload.connection_id is not None or payload.body is not None):
        raise HTTPException(
            status_code=422, detail="An agent sends no connection_id and no body to start a run.",
        )
    if via is None and (not payload.connection_id or not body):
        raise HTTPException(
            status_code=422, detail="A member names one of their connections and an instruction.",
        )

    written: _Written | None = None
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        await load_visible_task(db, vis, task_id)
        if via is not None:
            client_via, connection_id = via, str(user.agent.connection_id)  # type: ignore[union-attr]
        else:
            cid = _parse_uuid(str(payload.connection_id), "Connection")
            conn = (await db.execute(
                text(
                    "SELECT id, client_origin, client_id FROM agent_connection "
                    " WHERE id = CAST(:cid AS uuid) AND lower(member_email) = :me "
                    "   AND revoked_at IS NULL AND expires_at > now()"
                ),
                {"cid": cid, "me": me},
            )).fetchone()
            # EA-13a: the stamp of the connection's own client id, the one
            # branch 0 gives the agent's calls on this connection.
            origin = getattr(conn, "client_origin", None)
            client_via = (
                actor_via_for(origin, client_id=getattr(conn, "client_id", None))
                if conn else ""
            )
            if conn is None or not ACTOR_VIA_PATTERN.fullmatch(client_via):
                raise HTTPException(
                    status_code=422, detail="Name one of your own live agent connections.",
                )
            connection_id = cid

        replay = await _replay_start(db, me, payload.idempotency_key, task_id)
        if replay is not None:
            response.status_code = 200
            return replay

        open_sql = _OPEN_RUN_SQL
        open_args = _open_run_args(task_id, me, client_via)
        existing = (await db.execute(text(open_sql), open_args)).fetchone()
        if existing is None:
            ts = await _clock(db)
            made = (await db.execute(text(_INSERT_RUN_SQL), {
                "tid": task_id, "me": me, "cid": connection_id, "via": client_via,
                "status": "in_progress" if via is not None else "created",
                "by": via if via is not None else me,
                "started": ts if via is not None else None,
                "link": link, "ts": ts, "agent_ts": ts if via is not None else None,
                "key": payload.idempotency_key,
            })).fetchone()
            if made is None:  # a concurrent start won the open-run or the key index
                # A retry with the same key that lost the race replays the
                # winner, as the key read above would have, and writes nothing.
                replay = await _replay_start(db, me, payload.idempotency_key, task_id)
                if replay is not None:
                    response.status_code = 200
                    return replay
                existing = (await db.execute(text(open_sql), open_args)).fetchone()
                if existing is None:
                    raise HTTPException(status_code=409, detail="Start the run again.")
        if existing is not None:
            # The open run is the answer (EA12-6). A member's instruction is
            # not dropped: it is a prompt on that run, and the status machine
            # decides. A refusal is a 409 that names the run's status.
            if via is None:
                locked = await _lock_run(db, str(existing.id))
                written = await _write_activity(
                    db, locked, user, kind="prompt", key=payload.idempotency_key, body=body,
                )
                existing = await _load_run(db, str(existing.id))
            response.status_code = 200
            out = (await _runs_out(db, [existing]))[0]
            created = False
        else:
            run = await _load_run(db, str(made.id))
            if via is None:
                # The member's instruction is the first prompt. It is the act
                # of creation, so it moves no status: the run stays `created`.
                written = await _write_activity(
                    db, run, user, kind="prompt", key=None, body=body, event="member_start",
                )
            run = await _load_run(db, str(made.id))
            out = (await _runs_out(db, [run]))[0]
            created = True
    if written is not None and written.commented:
        await emit("pm.task.comment_added", {"task_id": task_id})
    return {"run": out, "created": created}


#: ``needs_agent=true``: a run the agent must act on. A ``created`` run, a
#: ``stopping`` run, or a derived ``answered`` run.
_NEEDS_AGENT_SQL = (
    "(r.status IN ('created', 'stopping')"
    " OR (r.status = 'in_progress'"
    "     AND (r.last_agent_activity_at IS NULL"
    "          OR r.last_activity_at > r.last_agent_activity_at)))"
)


@router.get("/agent-runs", dependencies=_ENABLED)
async def list_my_agent_runs(
    status: str | None = None,
    needs_agent: bool = False,
    task_id: str | None = None,
    client_via: str | None = None,
    limit: int = Query(50, ge=1, le=100),
    cursor: str | None = None,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """The caller member's runs across the tasks they can see, newest first.

    ``cursor`` is the opaque ``next_cursor`` of the page before. A new agent
    session calls this with ``needs_agent=true`` first.
    """
    if status is not None and status not in RUN_STATUSES:
        raise HTTPException(
            status_code=422, detail=f"Unknown status '{status}'. One of: {list(RUN_STATUSES)}.",
        )
    try:
        offset = int(cursor) if cursor else 0
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Unknown cursor.") from exc
    if offset < 0:
        raise HTTPException(status_code=422, detail="Unknown cursor.")
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        clauses = [
            "r.member_email = :me",
            "EXISTS (SELECT 1 FROM pm_tasks t WHERE t.id = r.task_id"
            f" AND {task_visibility_clause(vis)})",
        ]
        params: dict[str, Any] = {**vis.params, "me": _member(user)}
        if status == "stale":
            # `stale` is never stored (§12.1). It is the derived state.
            clauses.append(f"({AGENT_RUN_STATE_SQL}) = 'stale'")
        elif status is not None:
            clauses.append("r.status = :status")
            params["status"] = status
        if needs_agent:
            clauses.append(_NEEDS_AGENT_SQL)
        if task_id:
            clauses.append("r.task_id = CAST(:tid AS uuid)")
            params["tid"] = _parse_uuid(task_id, "Task")
        if client_via:
            clauses.append("r.client_via = :via")
            params["via"] = client_via.strip().lower()
        rows = (await db.execute(
            text(
                f"{_RUN_SELECT} WHERE {' AND '.join(clauses)} "
                " ORDER BY r.created_at DESC, r.id DESC LIMIT :limit OFFSET :offset"
            ),
            {**params, "limit": limit + 1, "offset": offset},
        )).fetchall()
        more = len(rows) > limit
        out = await _runs_out(db, list(rows[:limit]))
    return {"rows": out, "next_cursor": str(offset + limit) if more else None}


async def _read_run(user: UserContext, run_id: str, after: int | None) -> tuple[dict, list]:
    """One read of the long poll. It opens and closes its own session, and it
    checks the task again each time, so a revoke takes effect at the next
    read (EA12-4)."""
    async with _tenant_session() as db:
        run = await _load_visible_run(db, user, run_id)
        rows = (await db.execute(
            text(
                "SELECT * FROM pm_agent_run_activities "
                " WHERE run_id = CAST(:rid AS uuid) AND seq > :after "
                " ORDER BY seq LIMIT :limit"
            ),
            {"rid": run_id, "after": -1 if after is None else after, "limit": MAX_ACTIVITIES},
        )).fetchall()
        return (await _runs_out(db, [run]))[0], [_activity_out(r) for r in rows]


@router.get("/agent-runs/{run_id}", dependencies=_ENABLED)
async def get_agent_run(
    run_id: str,
    after: int | None = Query(None, ge=0),
    wait_s: int = Query(0, ge=0, le=MAX_WAIT_S),
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """The run and its activities after the cursor ``after`` (a ``seq``).

    With ``wait_s``, the answer waits until a new activity arrives, for at
    most that many seconds. The gateway reads again every 2 seconds, and NO
    session is open while it sleeps (EA12-4, fence R11).
    """
    run_id = _parse_uuid(run_id, "Agent run")
    deadline = time.monotonic() + wait_s
    while True:
        run, activities = await _read_run(user, run_id, after)
        left = deadline - time.monotonic()
        if activities or left <= 0:
            break
        await _sleep(min(POLL_S, left))
    cursor = activities[-1]["seq"] if activities else after
    return {"run": run, "activities": activities, "cursor": cursor}


@router.post("/agent-runs/{run_id}/activities", status_code=201, dependencies=_ENABLED)
async def post_agent_activity(
    run_id: str, payload: ActivityIn, response: Response,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Write one activity. 201 with the new row, or 200 with the row that
    already holds this ``idempotency_key`` (R5)."""
    run_id = _parse_uuid(run_id, "Agent run")
    _require_write_scope(user)
    agent = _agent_via(user) is not None
    _validate(payload, agent=agent)
    async with _tenant_session() as db:
        await _load_visible_run(db, user, run_id)
        run = await _lock_run(db, run_id)
        written = await _write_activity(
            db, run, user, kind=payload.type, key=payload.idempotency_key,
            body=_clean_body(payload.body), options=payload.options,
            allow_multiple=bool(payload.allow_multiple), selected=payload.selected,
            action=payload.action, parameters=payload.parameters, signal=payload.signal,
        )
        if not written.created:
            response.status_code = 200
        out_run = (await _runs_out(db, [await _load_run(db, run_id)]))[0]
        out_activity = _activity_out(written.activity)
    if written.commented:
        await emit("pm.task.comment_added", {"task_id": out_run["task_id"]})
    return {"activity": out_activity, "run": out_run, "created": written.created}


#: The statuses that a finish leaves: ``completed``, or ``stopped`` when a
#: Stop waited (EA12-5). A second finish with no summary returns the run.
_FINISHED: frozenset[str] = frozenset({"completed", "stopped"})


@router.post("/agent-runs/{run_id}/finish", dependencies=_ENABLED)
async def finish_agent_run(
    run_id: str, payload: FinishIn, user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """The agent finishes its run: ``completed``, with an optional final
    ``response``. A Stop wins: on a ``stopping`` run this gives ``stopped``.
    A person cannot finish a run. They press Stop."""
    run_id = _parse_uuid(run_id, "Agent run")
    _require_write_scope(user)
    if _agent_via(user) is None:
        raise HTTPException(status_code=403, detail="Only the agent finishes its run.")
    summary = _clean_body(payload.summary)
    written: _Written | None = None
    async with _tenant_session() as db:
        await _load_visible_run(db, user, run_id)
        run = await _lock_run(db, run_id)
        replay = await _existing_activity(db, run_id, payload.idempotency_key)
        if replay is not None:
            out_run = (await _runs_out(db, [run]))[0]
            return {"run": out_run, "activity": _activity_out(replay)}
        if run.member_email != _member(user):
            raise HTTPException(status_code=403, detail="This agent run belongs to another member.")
        if not summary and run.status in _FINISHED:
            # A retry of a finish with no summary. That finish wrote no
            # activity, so no key finds it. The run is the result, as the
            # edge's `finish_agent_run` reads it.
            return {"run": (await _runs_out(db, [run]))[0], "activity": None}
        if summary:
            # The response and the finish are ONE move: the response is
            # written under the event `agent_finish`, so it sets `completed`
            # (or `stopped`, when a Stop waits) itself.
            written = await _write_activity(
                db, run, user, kind="response", key=payload.idempotency_key, body=summary,
                event="agent_finish",
            )
        else:
            new_status = transition(run.status, "agent_finish")
            await _move_run(db, run, new_status, ts=await _clock(db), user=user,
                            via=_agent_via(user))
        out_run = (await _runs_out(db, [await _load_run(db, run_id)]))[0]
        out_activity = _activity_out(written.activity) if written is not None else None
    if written is not None and written.commented:
        await emit("pm.task.comment_added", {"task_id": out_run["task_id"]})
    return {"run": out_run, "activity": out_activity}


# ── The archive with an approval (EA-6g, EA3-33 and EA3-40) ─────────────────


_KEY_USED = "This idempotency_key was used for another activity. Send a new key."


async def _earlier_archive_request(db: Any, task_id: str, me: str, key: str) -> Any | None:
    """The activity that this member wrote with *key* on a run of this task,
    or ``None``. A replay finds its first request here, also after its run
    moved on, so a replay never starts a second run."""
    return (await db.execute(
        text(
            "SELECT a.* FROM pm_agent_run_activities a "
            "  JOIN pm_agent_runs r ON r.id = a.run_id "
            " WHERE r.task_id = CAST(:tid AS uuid) AND r.member_email = :me "
            "   AND a.idempotency_key = :key "
            " ORDER BY a.seq DESC LIMIT 1"
        ),
        {"tid": task_id, "me": me, "key": key},
    )).fetchone()


async def _agent_open_run(db: Any, user: UserContext, task_id: str, me: str,
                          via: str) -> tuple[str | None, bool]:
    """The id of the agent's open run on the task, or of a new
    ``in_progress`` run (EA12-6), and True when this call made it.

    The ONE place where an agent's act, not a start, opens a run: the archive
    request (EA-6g) and the claim (EA-13c). It writes no activity and no
    comment. The id is None only when a concurrent start won the open-run
    index and then rolled back, which the caller answers as a retry.
    """
    open_args = _open_run_args(task_id, me, via)
    existing = (await db.execute(text(_OPEN_RUN_SQL), open_args)).fetchone()
    if existing is not None:
        return str(existing.id), False
    ts = await _clock(db)
    made = (await db.execute(text(_INSERT_RUN_SQL), {
        "tid": task_id, "me": me, "cid": str(user.agent.connection_id),  # type: ignore[union-attr]
        "via": via, "status": "in_progress", "by": via, "started": ts,
        "link": None, "ts": ts, "agent_ts": ts, "key": None,
    })).fetchone()
    if made is not None:
        return str(made.id), True
    # A concurrent start won the open-run index. Its run is the answer.
    existing = (await db.execute(text(_OPEN_RUN_SQL), open_args)).fetchone()
    return (str(existing.id) if existing is not None else None), False


async def _open_or_new_run(db: Any, user: UserContext, task_id: str, me: str,
                           via: str) -> Any:
    """The open run of this member and client on the task, or a new
    ``in_progress`` run, LOCKED for this transaction (EA12-6, as
    ``ask_on_task`` gets it through the start)."""
    run_id, _ = await _agent_open_run(db, user, task_id, me, via)
    if run_id is None:
        raise HTTPException(status_code=409, detail="Send the request again.")
    return await _lock_run(db, run_id)


async def open_run_for_claim(db: Any, task_id: str, user: UserContext) -> str | None:
    """WS-55 EA-13c (spec §12.2 item 3, EA3-42). A claim by an agent opens
    its run, so the card reads "working" even when the agent skips
    ``start_agent_run``. ``claim_task`` calls it in the claim's OWN
    transaction, so a refused claim (C4, C5) rolls the run back with it.

    * Only for a signed agent claim (``UserContext.agent``) while
      ``agent_runs_enabled`` is on. A member's claim opens no run, and None
      comes back.
    * With an open run of this task, member and stamp (EA12-6), it writes
      nothing and returns that run. Else it
      inserts one ``in_progress`` run, started by the stamp, with the
      connection and ``last_agent_activity_at`` now. No activity row, no
      comment and no notification (C7 and C9 of EA-6b).
    * ``start_agent_run`` after the claim finds this run and answers
      ``created: false``. After a Stop and a terminal run, the next claim
      opens a new run.

    ⚠️ The lock order. ``claim_task`` calls this BEFORE its row lock. A run
    writer that comments (a member's start, the archive request) takes the
    run first and the task row after it, so the claim takes them in the same
    order. O-M9 of ``test_mcp_edge_claim.py`` deadlocks when the claim takes
    the row first. The call also sits after the assignee key, but that place
    is not a rule: no run writer takes an assignee key, so no cycle passes
    through the key.
    """
    if getattr(user, "agent", None) is None or not agent_runs_enabled():
        return None
    run_id, _ = await _agent_open_run(db, user, task_id, _member(user), _agent_via(user) or "")
    return run_id


@router.post("/tasks/{task_id}/archive-request", status_code=201, dependencies=_ENABLED)
async def request_task_archive(
    task_id: str, payload: ArchiveRequestIn, response: Response,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """An agent asks the run's member to archive this task (EA3-40 rule 1).

    It starts or reuses the member's open run, as ``ask_on_task`` does, and
    writes ONE typed elicitation: ``content_metadata.request`` holds the kind
    and ``expires_at``, the options are the server's two, and the target is
    the run's task. It archives nothing. 201 with ``created: true``, or 200
    with the first request of this ``idempotency_key``.
    """
    task_id = _parse_uuid(task_id, "Task")
    _require_write_scope(user)
    via = _agent_via(user)
    if via is None:
        raise HTTPException(
            status_code=403, detail="Only an agent asks to archive a task. A member archives it.",
        )
    me = _member(user)
    key = payload.idempotency_key
    written: _Written | None = None
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        await load_visible_task(db, vis, task_id)
        found = await _earlier_archive_request(db, task_id, me, key)
        if found is not None:
            if archive_request_of(found) is None:
                raise HTTPException(status_code=409, detail=_KEY_USED)
            response.status_code = 200
            run = await _load_run(db, str(found.run_id))
            return {"activity": _activity_out(found),
                    "run": (await _runs_out(db, [run]))[0], "created": False}
        run = await _open_or_new_run(db, user, task_id, me, via)
        if run.status == "stopping":
            # A Stop waits. The agent must stop, not ask for an archive.
            raise HTTPException(
                status_code=409, detail="This agent run is stopping. It cannot take this activity.",
            )
        expires = await _clock(db) + ARCHIVE_REQUEST_TTL
        written = await _write_activity(
            db, run, user, kind="elicitation", key=key, body=ARCHIVE_REQUEST_BODY,
            options=list(ARCHIVE_OPTIONS),
            request={"kind": ARCHIVE_REQUEST_KIND, "expires_at": expires.isoformat()},
        )
        if not written.created:
            # The same key, written by a concurrent call on this run.
            if archive_request_of(written.activity) is None:
                raise HTTPException(status_code=409, detail=_KEY_USED)
            response.status_code = 200
        out_run = (await _runs_out(db, [await _load_run(db, str(run.id))]))[0]
        out_activity = _activity_out(written.activity)
    if written.commented:
        await emit("pm.task.comment_added", {"task_id": task_id})
    return {"activity": out_activity, "run": out_run, "created": written.created}


def _replayed_decision(row: Any) -> str | None:
    """The decision of an earlier answer to a typed request, or ``None`` when
    *row* is not such an answer."""
    content = from_jsonb(row.content) or {}
    if row.type != "prompt" or "archived" not in content:
        return None
    selected = content.get("selected") or []
    return selected[0] if len(selected) == 1 else None


@router.post("/agent-runs/{run_id}/archive-approval", dependencies=_ENABLED)
async def approve_task_archive(
    run_id: str, payload: ArchiveApprovalIn, user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """The run's member approves or declines a typed archive request.

    ⚠️ This route is NOT in ``MCP_ROUTES``, so branch 0 refuses a signed
    request to it with 401 (EA3-40 rule 2). It refuses an agent caller too.

    * Only ``run.member_email`` answers. Another member who sees the task
      gets 403, and nothing changes (rule 3). A hidden run is 404.
    * A run that is not ``awaiting`` on an open typed request gets 409, and
      nothing changes. An APPROVE past ``expires_at`` gets 409 too (rule 5).
      A decline still closes an expired request (fix round 1): it archives
      nothing and moves the run on, so the run never stays ``awaiting``.
    * An approve archives the task through ``tasks.archive_task_in``, with no
      cascade to the subtasks, and writes the answer through
      :func:`_write_activity`, in ONE transaction (rule 2). The answer's
      ``content`` carries ``archived``.
    * The same ``idempotency_key`` again returns the first answer with 200.
      A second answer with a new key finds no open request: 409. So a second
      approve never archives twice. Every answer, a replay too, returns the
      task as it is now, so a retried click shows ``archived_at``.
    """
    run_id = _parse_uuid(run_id, "Agent run")
    if _agent_via(user) is not None:
        raise HTTPException(status_code=403, detail="Only the member approves an archive.")
    me = _member(user)
    decision = payload.decision
    key = payload.idempotency_key
    outcome = None
    written: _Written | None = None
    async with _tenant_session() as db:
        await _load_visible_run(db, user, run_id)
        run = await _lock_run(db, run_id)
        if run.member_email != me:
            raise HTTPException(
                status_code=403, detail="Only the member of this agent run answers it.",
            )
        replay = await _existing_activity(db, run_id, key)
        if replay is not None:
            if _replayed_decision(replay) != decision:
                raise HTTPException(status_code=409, detail=_KEY_USED)
            out_run = (await _runs_out(db, [run]))[0]
            archived = bool((from_jsonb(replay.content) or {}).get("archived"))
            return {"activity": _activity_out(replay), "run": out_run, "created": False,
                    "archived": archived, "task": await _task_now(db, user, str(run.task_id))}
        request = archive_request_of(await _newest_open_question(db, run_id))
        if run.status != "awaiting" or request is None:
            raise HTTPException(status_code=409, detail="This run waits for no archive request.")
        if decision == ARCHIVE_APPROVE and _request_expired(request, await _clock(db)):
            raise HTTPException(
                status_code=409, detail="This archive request expired. Decline it to close it.",
            )
        archived = False
        if decision == ARCHIVE_APPROVE:
            vis = await resolve_visibility(db, user)
            task = await load_visible_task(db, vis, str(run.task_id))
            # The ONE core, never the subtree (EA3-33).
            outcome = await archive_task_in(db, vis, task, user, include_subtasks=False)
            archived = True
        written = await _write_activity(
            db, run, user, kind="prompt", key=key, body=None, selected=[decision],
            typed=True, extra={"archived": archived},
        )
        out_run = (await _runs_out(db, [await _load_run(db, run_id)]))[0]
        out_activity = _activity_out(written.activity)
        task_now = (outcome.row if outcome is not None
                    else await _task_now(db, user, str(run.task_id)))
    if written.commented:
        await emit("pm.task.comment_added", {"task_id": out_run["task_id"]})
    if outcome is not None and not outcome.was_archived:
        await emit("pm.task.archived", {"task_id": out_run["task_id"]})
    return {"activity": out_activity, "run": out_run, "created": written.created,
            "archived": archived, "task": task_now}


async def _task_now(db: Any, user: UserContext, task_id: str) -> dict[str, Any]:
    """The task as the member sees it now, with its agent pill."""
    vis = await resolve_visibility(db, user)
    row = row_to_dict(await load_visible_task(db, vis, task_id), TaskModel)
    await attach_agent_state(db, [row])
    return row


__all__ = [
    "ACTIVITY_TYPES",
    "ARCHIVE_APPROVE",
    "ARCHIVE_DECLINE",
    "ARCHIVE_OPTIONS",
    "ARCHIVE_REQUEST_KIND",
    "ARCHIVE_REQUEST_TTL",
    "EPHEMERAL_CAP",
    "MAX_WAIT_S",
    "POLL_S",
    "RUN_STATUSES",
    "ActivityIn",
    "ArchiveApprovalIn",
    "ArchiveRequestIn",
    "FinishIn",
    "OptionIn",
    "StartIn",
    "archive_request_of",
    "transition",
]
