"""Projects · agent runs on a task, after Plane (WS-55 EA-12a).

Spec: ``project-docs/specs/external_agent_access.md`` §12.1 (D-EA-10, the
decisions EA12-1 to EA12-13) and row EA-12a of §15.

    GET  /projects/tasks/{task_id}/agent-runs     → {rows}
    POST /projects/tasks/{task_id}/agent-runs     → {run, created}
    GET  /projects/agent-runs                     → {rows, next_cursor}
    GET  /projects/agent-runs/{run_id}            → {run, activities, cursor}
    POST /projects/agent-runs/{run_id}/activities → {activity, run, created}
    POST /projects/agent-runs/{run_id}/finish     → {run, activity}

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
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from typing import Any, Literal

from acb_auth import UserContext, get_current_user
from acb_auth.agent_oauth import ACTOR_VIA_PATTERN, PROJECTS_WRITE, actor_via_for
from acb_common import get_logger
from fastapi import Depends, HTTPException, Query, Response
from gateway.routes.projects.core import (
    _tenant_session,
    actor,
    emit,
    from_jsonb,
    load_visible_task,
    record_activity,
    resolve_visibility,
    router,
    task_visibility_clause,
    wire,
)
from gateway.routes.projects.filters import (
    AGENT_RUN_OPEN_STATUSES,
    AGENT_RUN_STATE_SQL,
    agent_runs_enabled,
)
from gateway.routes.projects.notifications import excerpt_of, notify, task_audience
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
            detail=f"This agent run is {status}. It cannot take this activity.",
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
) -> tuple[str | None, dict[str, Any] | None]:
    """``(in_reply_to, content_metadata)`` of a member prompt (EA12-7).

    The server sets ``in_reply_to`` to the newest unanswered elicitation, and
    never takes it from input. ``selected`` is valid only on a ``select``: the
    ids must be options of that question, one id unless ``allow_multiple``
    (R3). The labels go into *content*, so a reader gets them with the ids.
    """
    question = await _newest_open_question(db, run_id)
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
) -> _Written:
    """Write one activity on a LOCKED run, inside the caller's transaction.

    The idempotency check runs first, so a replay writes nothing and moves no
    status (R5). Then the status machine, the row, the cap of ephemeral rows,
    the comment, the notifications and the run's new status.
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
    elif kind == "action":
        content["action"] = action
        content["parameters"] = dict(parameters or {})
    elif kind == "prompt":
        signal_out = signal or "continue"
        content["signal"] = signal_out
        if signal_out != "stop":
            in_reply_to, content_metadata = await _answer(db, run_id, content, selected)

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
    question: bool,
) -> Any:
    """The timeline comment of one non-ephemeral activity, and its bell rows.

    One ``comment`` through ``record_activity``, authored by the member, with
    ``meta.via`` naming the client for an agent. A question that sets
    ``awaiting`` notifies the run's member and ``task_audience()`` with the
    kind ``agent_question`` and the CLIENT as the actor (EA12-8). Every other
    comment sends the ``comment`` kind to ``task_audience()``, as
    ``add_comment`` does, with no mention pass.
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
    audience = await task_audience(db, task_id)
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
                    "SELECT id, client_origin FROM agent_connection "
                    " WHERE id = CAST(:cid AS uuid) AND lower(member_email) = :me "
                    "   AND revoked_at IS NULL AND expires_at > now()"
                ),
                {"cid": cid, "me": me},
            )).fetchone()
            client_via = actor_via_for(getattr(conn, "client_origin", None)) if conn else ""
            if conn is None or not ACTOR_VIA_PATTERN.fullmatch(client_via):
                raise HTTPException(
                    status_code=422, detail="Name one of your own live agent connections.",
                )
            connection_id = cid

        if payload.idempotency_key:
            by_key = (await db.execute(
                text(
                    f"{_RUN_SELECT} WHERE r.member_email = :me "
                    "   AND r.idempotency_key = :key"
                ),
                {"me": me, "key": payload.idempotency_key},
            )).fetchone()
            if by_key is not None:
                if str(by_key.task_id) != task_id:
                    raise HTTPException(
                        status_code=409, detail="This idempotency_key started another run.",
                    )
                response.status_code = 200
                return {"run": (await _runs_out(db, [by_key]))[0], "created": False}

        open_sql = (
            f"{_RUN_SELECT} WHERE r.task_id = CAST(:tid AS uuid) AND r.member_email = :me "
            "   AND r.client_via = :via AND r.status = ANY(:open)"
        )
        open_args = {"tid": task_id, "me": me, "via": client_via,
                     "open": list(AGENT_RUN_OPEN_STATUSES)}
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


__all__ = [
    "ACTIVITY_TYPES",
    "EPHEMERAL_CAP",
    "MAX_WAIT_S",
    "POLL_S",
    "RUN_STATUSES",
    "ActivityIn",
    "FinishIn",
    "OptionIn",
    "StartIn",
    "transition",
]
