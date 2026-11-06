"""Projects · agent runs on a task, after Plane (WS-55 EA-12a).

Spec: ``project-docs/specs/external_agent_access.md`` §12.1 (D-EA-10, the
decisions EA12-1 to EA12-13) and row EA-12a of §15.

    GET  /projects/tasks/{task_id}/agent-runs     → {rows}
    POST /projects/tasks/{task_id}/agent-runs     → {run, created}
    GET  /projects/agent-runs                     → {rows, next_cursor}
    GET  /projects/agent-runs/{run_id}            → {run, activities, cursor}
    POST /projects/agent-runs/{run_id}/activities → {activity, run, created}
    POST /projects/agent-runs/{run_id}/finish     → {run, activity}
    POST /projects/agent-runs/{run_id}/release    → {run, released}   (EA-14a)
    POST /projects/agent-runs/{run_id}/resume     → {run, activity, resumed, created}
                                                                      (SW-11a)
    POST /projects/tasks/{task_id}/archive-request    → {activity, run, created}
    POST /projects/agent-runs/{run_id}/archive-approval
                                                  → {activity, run, created, archived}
    POST /projects/agent-runs/{run_id}/outside-decision
                                                  → {activity, run, created}  (MCP-G15)

An outside agent works on a task inside a RUN. The run holds typed
ACTIVITIES: ``prompt``, ``thought``, ``action``, ``response``,
``elicitation`` and ``error``. An ``elicitation`` asks a member a question,
and the run waits (``awaiting``) until a member writes a ``prompt``. These
routes are the only writer of ``pm_agent_runs`` and
``pm_agent_run_activities``.

**One AI for each task (EA-14a).** A task has at most ONE open run, across
every member and every client. A start or a claim on a task that another run
holds answers 409 with the code ``task_held``. A run keeps its task while its
last activity is newer than the lease (:func:`filters.agent_run_lease_s`, two
hours, or three leases while it waits on a question). After that, the next
claim takes the task over: the old run becomes ``abandoned``. The holder, a
person who owns the run or the task, or an admin may release a run
(``released``). Fence: ``tests/unit/test_agent_run_exclusive.py``.

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
  route refuses a ``prompt`` on a typed request. SW-10a: an approve also
  closes the run (``completed``, with a system line) in that transaction.
* **Stranded runs** (WS-55 SW-10a, spec §12.6). The runs wire carries
  ``needs_agent`` (why the run waits for an agent) and ``stranded`` (the
  open strand that the sweep of ``gateway/agent_run_sweep.py`` found). The sweep
  decides; the writes to these two tables stay here
  (:func:`close_archived_runs`, :func:`write_strand_line`).
* **A decision made outside Metorite** (WS-55 MCP-G15, Metorite task 1567).
  The member can answer the agent's question in another place, such as the
  agent's own chat. The agent then records it through ``outside-decision``
  on its OWN awaiting run. The route writes an AGENT ``response``, never a
  ``prompt``: the server sets its ``in_reply_to`` to the open question and
  types it with ``content_metadata.decided_elsewhere``. So every open-question
  read finds the question closed, and the run moves ``awaiting`` →
  ``in_progress``. A typed request (EA-6g) never closes this way: only the
  member answers it, in Metorite.
* **Who may answer** (WS-55 EA-18a, D-EA-14, spec §12.8). With
  ``AGENT_QUESTIONS_ORGS`` on for the org, a person's answer to an open question
  needs :func:`may_answer_run`: the creator of the task, an assignee, the run's
  member, or the role owner, admin or manager. A Stop and a reply to a run that
  is not ``awaiting`` stay open to each member who sees the task. The list of
  ``agent_questions.py`` reads the same text, so it never offers a button that
  this route refuses.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Any, Literal

from acb_auth import UserContext, agent_oauth, get_current_user
from acb_auth.agent_oauth import ACTOR_VIA_PATTERN, PROJECTS_WRITE, actor_via_for
from acb_common import get_logger
from acb_common.no_person import NO_PERSON_PRESENT, NO_PERSON_TEXT, no_person_present
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
    AGENT_RUN_ANSWER_WAITS_SQL,
    AGENT_RUN_OPEN_STATUSES,
    AGENT_RUN_STATE_SQL,
    agent_questions_allowed,
    agent_runs_enabled,
    answer_change_enabled,
    attach_agent_state,
    connection_gone_sql,
    holder_free,
    holder_free_sql,
    lease_expired,
    live_answer_sql,
    open_recovery_sql,
    open_strand_sql,
    strand_fits_sql,
    task_closed_sql,
)
from gateway.routes.projects.notifications import (
    excerpt_of,
    mark_rows_read,
    notify,
    task_audience,
)
from gateway.routes.projects.tasks import archive_task_in
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

_log = get_logger("gateway.projects.agent_runs")

# ── The vocabulary ──────────────────────────────────────────────────────────

#: The eight statuses of Plane's ``AgentRun``, and the two of EA-14a:
#: ``abandoned`` (another session took the task over after the lease) and
#: ``released`` (the holder gave the task up). ``stale`` is never stored: it
#: is derived on read (:data:`AGENT_RUN_STATE_SQL`). The CHECK of the
#: EA-14a migration lists the stored ones.
RUN_STATUSES: tuple[str, ...] = (
    "created", "in_progress", "awaiting", "completed",
    "stopping", "stopped", "failed", "stale", "abandoned", "released",
)
OPEN_STATUSES: frozenset[str] = frozenset(AGENT_RUN_OPEN_STATUSES)
#: Terminal for the agent. A member ``prompt`` on ``completed`` opens it again.
TERMINAL_STATUSES: frozenset[str] = frozenset(
    {"completed", "stopped", "failed", "abandoned", "released"},
)
#: The stable code of the 409 for a task that another run holds (EA-14a).
TASK_HELD = "task_held"

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
#: SW-10a (spec §12.6, part 1 item 2). The system line of a run that an
#: approved archive closed. Before SW-10a such a run read ``answered`` until
#: an agent came back, and in production none came (tasks #129 and #248).
ARCHIVE_CLOSED_BODY = (
    "Closed: the member approved the archive, and the task is archived."
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


async def require_answer_change() -> None:
    """404 while ``agent_answer_change_enabled`` is off (SW-43)."""
    if not answer_change_enabled():
        raise HTTPException(status_code=404, detail="Not Found")


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


class ReleaseIn(BaseModel):
    """``POST /projects/agent-runs/{id}/release`` (EA-14a). No field: the run
    is the path, and the actor is the context."""

    model_config = ConfigDict(extra="forbid")


class ResumeIn(BaseModel):
    """``POST /projects/agent-runs/{id}/resume`` (WS-55 SW-11a, spec §12.7).
    The key only. The run is the path, the actor is the context, and the text
    of the prompt is the server's (:data:`RESUME_BODY`)."""

    model_config = ConfigDict(extra="forbid")

    idempotency_key: str = Field(min_length=1, max_length=128)


class WithdrawIn(BaseModel):
    """``POST /projects/agent-runs/{id}/answer/withdraw`` (SW-43, spec
    ``agent_coordination.md`` §3.9). The answer to take back and the key. The
    run is the path, and the actor is the context."""

    model_config = ConfigDict(extra="forbid")

    answer_id: str = Field(min_length=1, max_length=64)
    idempotency_key: str = Field(min_length=1, max_length=128)


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


#: MCP-G15. The longest ``decision`` and ``where`` of an outside decision.
MAX_DECISION = 2_000
MAX_WHERE = 80


class OutsideDecisionIn(BaseModel):
    """``POST /projects/agent-runs/{id}/outside-decision`` (WS-55 MCP-G15).

    ``decision`` is the member's decision in the member's words. ``selected``
    names the option ids of the open question, when the member picked one.
    ``where`` says where the member decided, for example "Claude Code chat".
    The server sets ``in_reply_to`` and the marker. No body names them.
    """

    model_config = ConfigDict(extra="forbid")

    decision: str = Field(min_length=1, max_length=MAX_DECISION)
    where: str = Field(min_length=1, max_length=MAX_WHERE)
    selected: list[str] | None = Field(default=None, min_length=1, max_length=10)
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
    # EA-14a. The holder (or a person) gives the task up, and a later claim
    # takes a task over after the lease. Both end an OPEN run only.
    "release": {
        "created": "released", "in_progress": "released", "awaiting": "released",
        "stopping": "released",
    },
    "abandon": {
        "created": "abandoned", "in_progress": "abandoned", "awaiting": "abandoned",
        "stopping": "abandoned",
    },
    # SW-10a (spec §12.6). The member approved the agent's archive request
    # and the task is archived: the work is over, so the run closes. Only
    # `close_archived_run` passes it. The approval moved the run from
    # `awaiting` to `in_progress` the moment before.
    "archive_close": {"in_progress": "completed"},
    # SW-10a fix round. The task of the run is done, cancelled or archived,
    # and the run was quiet for the stale band: the sweep closes it. Only
    # `close_closed_task_run` passes it.
    "task_close": {"in_progress": "completed"},
    # SW-10a fix round. The agent of a run that the SERVER closed (one of
    # the two moves above) finishes later with a summary. The summary is kept
    # as a `response`, and the run stays `completed`. Only `finish_agent_run`
    # passes it, after `_server_closed` holds.
    "late_summary": {"completed": "completed"},
    # MCP-P1b-1 (spec §19.5, AD-5). An app run opens with its one request:
    # `gateway/app_requests.open_request` writes the elicitation and moves a
    # `created` run to `awaiting`. Only that function passes it.
    "app_ask": {"created": "awaiting"},
    # MCP-P1b-1. A decline closes a waiting app request (`app_requests.decide`).
    # The spend closes an approved one: `acb_auth.app_requests.SPEND_SQL` writes
    # that move in its one UPDATE, because `acb_auth` cannot import the
    # gateway. An approve is the `member_prompt` above.
    "app_close": {"awaiting": "completed", "in_progress": "completed"},
    # WS-55 MCP-G15. The member decided outside Metorite, and the agent
    # records it: its `response` closes the open question, so the run works
    # again. Only `record_outside_decision` passes it, after its own checks.
    # It is an AGENT event: no agent ever writes `member_prompt`. An app run
    # (MCP-P1b-1) is 404 to it: `_load_visible_run` reads task runs only,
    # so an agent never approves its own app request this way.
    "agent_outside_decision": {"awaiting": "in_progress"},
    # SW-43. The member takes back an answer that no agent has read. The
    # question is open again, so the run waits for a person. Only
    # `withdraw_agent_answer` passes it, after `answer_changeable_sql` holds.
    "member_withdraw": {"in_progress": "awaiting"},
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
        if status == "abandoned":
            detail = (
                "Another AI session took this task over. This run is abandoned. "
                "Stop the work on this task."
            )
        elif status == "created" and event == "member_prompt":
            detail = "The agent has not picked this run up yet. Wait for it to start, then reply."
        else:
            detail = f"This agent run is {status}. It cannot take this activity."
        raise HTTPException(status_code=409, detail=detail)
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


#: ``needs_agent``: the reason that a run waits for an agent, or NULL. ONE
#: text for the filter ``needs_agent=true`` (:data:`_NEEDS_AGENT_SQL`) and for
#: the field of the runs wire, so the two cannot disagree.
#:
#: * ``queued``: a ``created`` run (a member queued work, EA-12d).
#: * ``stopping``: a member pressed Stop.
#: * ``answered``: the RAW answered rule (``filters.AGENT_RUN_ANSWER_WAITS_SQL``).
#:   SW-10a: the derived state of such a run reads ``stale`` after five
#:   minutes (spec §12.6), and this reason still says ``answered``, so
#:   ``my_work`` keeps offering the answer as ``run_answered``.
#: * ``stranded``: SW-10a. The sweep nudged the member about this run, and no
#:   activity came since (``filters.open_strand_sql``). Derived from the
#:   ``pm_agent_run_strands`` table, with no column on the run.
_NEEDS_AGENT_REASON_SQL = (
    "(CASE WHEN r.status = 'created' THEN 'queued'"
    " WHEN r.status = 'stopping' THEN 'stopping'"
    f" WHEN r.status = 'in_progress' AND {AGENT_RUN_ANSWER_WAITS_SQL} THEN 'answered'"
    f" WHEN {open_strand_sql('r')} THEN 'stranded'"
    " END)"
)

#: ``needs_agent=true``: a run the agent must act on. A ``created`` run, a
#: ``stopping`` run, a run whose answer waits, or a stranded run (SW-10a).
_NEEDS_AGENT_SQL = f"({_NEEDS_AGENT_REASON_SQL} IS NOT NULL)"

#: EA-14a: every read of a run also carries ``idle_s`` (the seconds since its
#: last activity, for the lease) and the display name of its member. SW-10a:
#: and ``needs_agent``, the reason it waits for an agent.
_RUN_SELECT = (
    f"SELECT r.*, {AGENT_RUN_STATE_SQL} AS state, "
    f" {_NEEDS_AGENT_REASON_SQL} AS needs_agent, "
    " EXTRACT(EPOCH FROM (clock_timestamp() - r.last_activity_at)) AS idle_s, "
    " (SELECT u.display_name FROM app_user u"
    "   WHERE lower(u.email) = lower(r.member_email) LIMIT 1) AS member_name "
    "FROM pm_agent_runs r"
)

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

#: SW-10a (spec §12.6). The open strand of each run that still fits it, for
#: ``stranded`` on the runs wire. The rule is ``filters.open_strand_sql``.
_OPEN_STRANDS_SQL = f"""
SELECT DISTINCT ON (s.run_id) s.run_id, s.reason, s.nudged_at, s.recovered_at
  FROM pm_agent_run_strands s
  JOIN pm_agent_runs r ON {strand_fits_sql('s', 'r')}
 WHERE s.run_id = ANY(CAST(:ids AS uuid[])) AND r.task_id IS NOT NULL
 ORDER BY s.run_id, s.nudged_at DESC
"""


def _stranded_out(found: Any, idle_s: Any) -> dict[str, Any]:
    """``stranded`` of one run: the reason, the first miss, the second miss
    (the recovery record) and the seconds since the last activity."""
    return {
        "reason": found.reason,
        "nudged_at": wire(found.nudged_at),
        "recovered_at": wire(found.recovered_at),
        "idle_s": int(float(idle_s)) if idle_s is not None else None,
    }


async def _runs_out(db: Any, rows: list[Any]) -> list[dict[str, Any]]:
    """The wire shape of each run, with its derived ``state``, its open
    elicitation, its newest ephemeral line and its open strand (SW-10a).
    Three queries for the list."""
    ids = [str(r.id) for r in rows]
    questions: dict[str, dict[str, Any]] = {}
    lines: dict[str, dict[str, Any]] = {}
    strands: dict[str, Any] = {}
    answers: dict[str, dict[str, Any]] = {}
    if ids:
        for found in (await db.execute(text(_OPEN_ELICITATIONS_SQL), {"ids": ids})).fetchall():
            questions[str(found.run_id)] = _activity_out(found)
        for found in (await db.execute(text(_LATEST_EPHEMERAL_SQL), {"ids": ids})).fetchall():
            lines[str(found.run_id)] = _activity_out(found)
        for found in (await db.execute(text(_OPEN_STRANDS_SQL), {"ids": ids})).fetchall():
            strands[str(found.run_id)] = found
        if answer_change_enabled():
            for found in (await db.execute(
                text(live_answer_sql("p.run_id = ANY(CAST(:ids AS uuid[]))")),
                {"ids": ids},
            )).fetchall():
                answers[str(found.run_id)] = {
                    "id": str(found.id), "seq": int(found.seq),
                    "changeable": bool(found.changeable),
                }
    out: list[dict[str, Any]] = []
    for row in rows:
        key = str(row.id)
        strand = strands.get(key)
        out.append({
            "id": key,
            "task_id": str(row.task_id),
            "member_email": row.member_email,
            # EA-14a: the name for "Held by <member> via <client>", and whether
            # the lease ended (the run no longer keeps its task).
            "member_name": getattr(row, "member_name", None) or row.member_email,
            "lease_expired": bool(
                row.status in OPEN_STATUSES
                and getattr(row, "idle_s", None) is not None
                and lease_expired(row.status, float(row.idle_s))
            ),
            "connection_id": wire(row.connection_id),
            # SW-7: the MCP session key, so `my_work` keeps this session's
            # runs. The key alone is no credential: the edge verifies the HMAC
            # of the session id before it signs a key in (spec §12.5).
            "session_key": getattr(row, "session_key", None),
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
            # SW-10a (spec §12.6): why the run waits for an agent, and the
            # open strand that the sweep found, if any.
            "needs_agent": getattr(row, "needs_agent", None),
            "stranded": (_stranded_out(strand, getattr(row, "idle_s", None))
                         if strand is not None else None),
        })
        if answer_change_enabled():
            # SW-43. Only with the flag on, so a flag-off wire is unchanged.
            out[-1]["answer"] = answers.get(key)
    return out


#: MCP-P1b-1 (AD-16). A run has ONE subject: a task, or an app (migration
#: ``pm_agent_runs_app_subject``). Each read of this module names the task
#: subject, so an app run never shows in the runs list, the pill, the board,
#: ``my_work``, Needs attention or the sweep. Fence:
#: ``test_every_pm_agent_runs_query_names_its_subject``.
_LOAD_TASK_RUN_SQL = f"{_RUN_SELECT} WHERE r.id = CAST(:rid AS uuid) AND r.task_id IS NOT NULL"
#: The load of an app run, for ``gateway/app_requests.py`` only.
_LOAD_APP_RUN_SQL = f"{_RUN_SELECT} WHERE r.id = CAST(:rid AS uuid) AND r.app IS NOT NULL"


async def _load_run(db: Any, run_id: str, *, app: bool = False) -> Any:
    """One run by id, or 404. A task run, or with *app* an app run. A run of
    the other subject is 404, never a 500 on its NULL task."""
    row = (await db.execute(
        text(_LOAD_APP_RUN_SQL if app else _LOAD_TASK_RUN_SQL), {"rid": run_id},
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


#: The lock of one run of each subject (EA12-5, MCP-P1b-1).
_LOCK_TASK_RUN_SQL = (
    "SELECT id FROM pm_agent_runs WHERE id = CAST(:rid AS uuid)"
    " AND task_id IS NOT NULL FOR UPDATE"
)
_LOCK_APP_RUN_SQL = (
    "SELECT id FROM pm_agent_runs WHERE id = CAST(:rid AS uuid)"
    " AND app IS NOT NULL FOR UPDATE"
)


async def _lock_run(db: Any, run_id: str, *, app: bool = False) -> Any:
    """The run row, locked for this transaction (EA12-5). *app* locks an app
    run (MCP-P1b-1). A run of the other subject takes no lock and is 404."""
    await db.execute(
        text(_LOCK_APP_RUN_SQL if app else _LOCK_TASK_RUN_SQL), {"rid": run_id},
    )
    return await _load_run(db, run_id, app=app)


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
        if content.get("withdraws"):
            return "Withdrew the answer"
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
    labels = option_labels(meta, selected)
    content["selected"] = list(selected)
    content["labels"] = labels
    return reply, {"question_id": str(question.id)}


def option_labels(meta: dict[str, Any], selected: list[str]) -> list[str]:
    """The labels of *selected*, the option ids of one ``select`` (R3).

    Each id must be an option of the question, with no repeat, and one id
    unless ``allow_multiple``. Else 422. ONE rule: a member's answer
    (:func:`_answer`) and an outside decision (MCP-G15) both read it.
    """
    by_id = {o["id"]: o for o in meta.get("options") or []}
    if len(set(selected)) != len(selected) or any(s not in by_id for s in selected):
        raise HTTPException(status_code=422, detail="selected names an id that is not an option.")
    if len(selected) > 1 and not meta.get("allow_multiple"):
        raise HTTPException(status_code=422, detail="This question takes one option.")
    return [by_id[s]["label"] for s in selected]


async def _prompt_reply(
    db: Any, run: Any, content: dict[str, Any], selected: list[str] | None,
    signal: str, *, typed: bool, withdraws: str | None,
) -> tuple[str | None, dict[str, Any] | None]:
    """``(in_reply_to, content_metadata)`` of a member prompt. A Stop and a
    withdraw marker (SW-43, *withdraws*) answer no question. Any other prompt
    answers the newest open one (:func:`_answer`)."""
    if withdraws is not None:
        content["withdraws"] = withdraws
        return None, None
    if signal == "stop":
        return None, None
    return await _answer(db, str(run.id), content, selected, typed=typed, status=run.status)


class _Written:
    """What one write did, for the response and the event after commit."""

    def __init__(self, activity: Any, created: bool, commented: bool) -> None:
        self.activity = activity
        self.created = created
        self.commented = commented


def _refuse_a_marked_prompt(kind: str, run_id: str) -> None:
    """AUTO-7a (``proactive_agent.md`` §4.2). A prompt is a person's word, and
    it answers the open question. A run with no person present gets 403
    ``no_person_present``, before any read or write. The mark is a ContextVar,
    so it binds an in-process caller. An HTTP call does not carry it."""
    if kind == "prompt" and no_person_present():
        _log.warning("agent_runs.no_person_refused", run_id=run_id)
        raise HTTPException(
            status_code=403,
            detail={"error": NO_PERSON_PRESENT, "message": NO_PERSON_TEXT},
        )


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
    quiet: bool = False,
    closes: str | None = None,
    metadata: dict[str, Any] | None = None,
    withdraws: str | None = None,
) -> _Written:
    """Write one activity on a LOCKED run, inside the caller's transaction.

    The idempotency check runs first, so a replay writes nothing and moves no
    status (R5). Then the status machine, the row, the cap of ephemeral rows,
    the comment, the notifications and the run's new status.

    EA-6g. *request* types an elicitation as an archive request: it goes into
    ``content_metadata``, and the question notifies the run's member only.
    *typed* lets a prompt answer such a request, and only the approval route
    passes it. *extra* adds the server's own keys to ``content``.

    SW-11a. *quiet* keeps the timeline comment and sends no bell: Resume
    writes a note to the agent, not a comment to people (spec §12.7).

    MCP-G15. *closes* and *metadata* go on an agent ``response`` only. Only
    :func:`record_outside_decision` passes them, and it checks the kind:
    *closes* is the id of the open question, which becomes ``in_reply_to``,
    and *metadata* becomes ``content_metadata``. The server reads both,
    never a request body.

    SW-43. *withdraws* is the id of the answer that a member took back. It
    types the prompt as the marker of a withdraw: no ``in_reply_to``, so
    :func:`_answer` does not run. Only :func:`withdraw_agent_answer` passes
    it.

    AUTO-7a. This is the one write path of an activity, so a marked prompt
    stops here (:func:`_refuse_a_marked_prompt`).
    """
    run_id = str(run.id)
    task_id = str(run.task_id)
    _refuse_a_marked_prompt(kind, run_id)
    found = await _existing_activity(db, run_id, key)
    if found is not None:
        return _Written(found, False, False)

    via = _agent_via(user)
    is_agent = via is not None
    me = _member(user)
    if is_agent and run.member_email != me:
        raise HTTPException(status_code=403, detail="This agent run belongs to another member.")

    new_status = transition(run.status, event or _event_of(kind, signal))
    reopened_end: str | None = None
    if run.status in TERMINAL_STATUSES and new_status in OPEN_STATUSES:
        # RA-4b. Read the end BEFORE the move clears it: the report takes the
        # idle gap of a reopened run from this end to the prompt.
        ended = getattr(run, "ended_at", None)
        reopened_end = ended.isoformat() if isinstance(ended, datetime) else None
        await _clear_for_reopen(db, run, user)

    content: dict[str, Any] = {"body": body}
    # MCP-G15: only `record_outside_decision` passes these, on a response.
    content_metadata: dict[str, Any] | None = dict(metadata) if metadata is not None else None
    signal_out: str | None = None
    signal_meta: dict[str, Any] | None = None
    in_reply_to: str | None = closes
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
        in_reply_to, content_metadata = await _prompt_reply(
            db, run, content, selected, signal_out, typed=typed, withdraws=withdraws,
        )
    if extra:
        content.update(extra)
    if kind == "prompt" and reopened_end is not None:
        # RA-4b. The marker of a REAL reopen. The report reads it for the idle
        # gap (`analytics_agents._REOPEN_GAPS_JOIN`), never the row trail,
        # because the cap of ephemeral rows deletes the rows between two acts.
        content["reopens"] = {"ended_at": reopened_end}

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
            quiet=quiet,
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
    question: bool, member_only: bool = False, quiet: bool = False,
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

    *quiet* (SW-11a, Resume) writes the comment and sends no bell at all.

    EA-18a (D-EA-14 point 4). With ``AGENT_QUESTIONS_ORGS`` on for the run's
    org, the creator of the task joins the audience of a question. ``notify``
    lower-cases and folds the recipients (``notifiable``), so one person gets
    one row, whatever the case of each address. It also drops the actor and
    each person who cannot open the task.
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
    if quiet:
        return linked
    audience = [] if member_only else await task_audience(db, task_id)
    snippet = excerpt_of(text_)
    if question:
        recipients = [run.member_email, *audience]
        if not member_only and agent_questions_allowed(getattr(run, "organization_id", None)):
            recipients.append(await _task_creator(db, task_id))
        await notify(
            db, recipients=recipients, kind="agent_question",
            task_id=task_id, actor_id=run.client_via, activity_id=str(comment.id),
            excerpt=snippet,
        )
    else:
        await notify(
            db, recipients=audience, kind="comment", task_id=task_id,
            actor_id=author, activity_id=str(comment.id), excerpt=snippet,
        )
    return linked


async def _task_creator(db: Any, task_id: str) -> str | None:
    """``pm_tasks.created_by`` of one task, as written. ``None`` adds nobody:
    ``notifiable`` drops an empty recipient."""
    return (await db.execute(
        text("SELECT created_by FROM pm_tasks WHERE id = CAST(:tid AS uuid)"),
        {"tid": task_id},
    )).scalar()


async def _move_run(
    db: Any, run: Any, new_status: str, *, ts: Any, user: UserContext | None,
    via: str | None,
    error_body: str | None = None, error_activity: str | None = None, stop: bool = False,
) -> None:
    """Set the run's status and stamps. Column names are fixed here, and
    only the values are bound.

    *user* is ``None`` only for a move that the server makes with no caller:
    the archive close of the SW-10a sweep (:func:`close_archived_run`). Such a
    move passes no *via* and no *stop*, so it reads nothing of a user.

    SW-7 (spec §12.5), the session key of the run, ONE assignment:

    * A terminal status clears it (a takeover clears it in ``_take_over``).
      A reopen of a finished run then finds no key, and the next session
      adopts the run.
    * A person's move that is not terminal keeps the key: a plain prompt,
      an answer to the session's question, and a Stop. The asking session
      may be alive and polling, so an answer must not hand its run to a
      sibling. A sibling sees a waiting run once its lease ended
      (``my_work``, group 1).
    * An agent move on the SAME connection adopts a run with no key and
      keeps a key that is set (``COALESCE``).
    * An agent move from ANOTHER connection writes the caller's key, which
      may be NULL. A key minted for the old connection never survives.
    """
    sets = ["status = :status", "last_activity_at = :ts"]
    params: dict[str, Any] = {"rid": str(run.id), "status": new_status, "ts": ts}
    if via is not None:
        agent = getattr(user, "agent", None)
        sets.append("last_agent_activity_at = :ts")
        sets.append("connection_id = CAST(:cid AS uuid)")
        params["cid"] = str(agent.connection_id) if agent is not None else None
        params["sk"] = getattr(agent, "session_key", None) if agent is not None else None
    if new_status == "in_progress" and run.started_at is None:
        sets.append("started_at = :ts")
    # Only a terminal status clears the key. Every run that reached one went
    # through here or `_take_over`, and a run that ended before migration 302
    # never had a key, so a reopen needs no clear of its own.
    if new_status in TERMINAL_STATUSES:
        sets.append("session_key = NULL")
    elif via is not None:
        sets.append(
            "session_key = CASE WHEN connection_id IS DISTINCT FROM CAST(:cid AS uuid)"
            " THEN CAST(:sk AS text) ELSE COALESCE(session_key, CAST(:sk AS text)) END")
    # A late summary goes from terminal to terminal, so the end stays.
    if new_status in TERMINAL_STATUSES and run.status not in TERMINAL_STATUSES:
        sets.append("ended_at = :ts")
    elif new_status in OPEN_STATUSES and run.status in TERMINAL_STATUSES:
        sets.append("ended_at = NULL")
    if stop and user is not None:
        sets.append("stopped_at = :ts")
        sets.append("stopped_by = :stopped_by")
        params["stopped_by"] = _member(user)
    if new_status == "failed":
        sets.append("error_metadata = CAST(:err AS jsonb)")
        params["err"] = _jsonb({"activity_id": error_activity, "body": error_body})
    update = text(f"UPDATE pm_agent_runs SET {', '.join(sets)} WHERE id = CAST(:rid AS uuid)")
    if not (run.status in TERMINAL_STATUSES and new_status in OPEN_STATUSES):
        await db.execute(update, params)
        return
    # H-409 item 1. Two members prompt two completed runs of one task at once.
    # Both pass `_clear_for_reopen`, and the second UPDATE breaks
    # `pm_agent_runs_one_open_per_task`. Inside a savepoint that error is a
    # 409 `task_held` that names the run that won, not a 500 that aborts the
    # whole transaction.
    try:
        async with _savepoint(db):
            await db.execute(update, params)
    except IntegrityError:
        holder = (await db.execute(
            text(_HOLDER_SQL), {"tid": str(run.task_id), "open": list(AGENT_RUN_OPEN_STATUSES)},
        )).fetchone()
        if holder is None:
            raise HTTPException(status_code=409, detail="Try the call again.") from None
        raise _task_held(holder) from None


@asynccontextmanager
async def _savepoint(db: Any) -> AsyncIterator[None]:
    """A ``SAVEPOINT`` around one statement, so its error leaves the
    transaction usable. The test fakes have no nested transactions, so there
    it is a pass-through (the idiom of ``crm.core``)."""
    begin_nested = getattr(db, "begin_nested", None)
    if begin_nested is None:  # pragma: no cover — only the fakes lack it
        yield
        return
    async with begin_nested():
        yield


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
                "   AND r.task_id IS NOT NULL"
                " ORDER BY r.created_at DESC, r.id DESC LIMIT 50"
            ),
            {"tid": task_id},
        )).fetchall()
        return {"rows": await _runs_out(db, list(rows))}


# ── One AI for each task (WS-55 EA-14a) ─────────────────────────────────────

#: The open run of a task, whoever holds it, with its idle time and the name
#: of its member. At most one row exists, because the partial unique index
#: ``pm_agent_runs_one_open_per_task`` allows one open run for each task.
#: SW-13c: ``connection_gone`` is true when the run's agent connection is
#: revoked or expired, and :func:`_free` then lets the gate take it over.
_HOLDER_SQL = (
    f"SELECT r.*, {AGENT_RUN_STATE_SQL} AS state, "
    " EXTRACT(EPOCH FROM (clock_timestamp() - r.last_activity_at)) AS idle_s, "
    f" {connection_gone_sql('r')} AS connection_gone, "
    " (SELECT u.display_name FROM app_user u"
    "   WHERE lower(u.email) = lower(r.member_email) LIMIT 1) AS member_name "
    "FROM pm_agent_runs r WHERE r.task_id = CAST(:tid AS uuid) "
    "  AND r.task_id IS NOT NULL AND r.status = ANY(:open) "
    "ORDER BY r.created_at DESC, r.id DESC LIMIT 1"
)

_NAME_SQL = (
    "SELECT display_name FROM app_user WHERE lower(email) = lower(:me) LIMIT 1"
)


async def _member_name(db: Any, email: str) -> str:
    found = (await db.execute(text(_NAME_SQL), {"me": email})).scalar()
    return str(found or email)


#: SW-13c fix round 1 (P2-a), and SW-7. The hint of a 409 whose holder is the
#: caller's OWN run from another session of the same client: another
#: terminal (SW-7), another machine, or an earlier sign-in (SW-13c). The
#: release checks the member and the stamp only, so the agent may end it.
SAME_CLIENT_HINT = (
    "This is your own run from another session of this client: another"
    " machine, an earlier sign-in, or another terminal when MCP sessions are"
    " on. If that session has ended, release it with release_task and this"
    " run_id, then claim again."
)


def _same_client(holder: Any, me: str | None, via: str | None,
                 connection: str | None, session: str | None = None) -> bool:
    """True when *holder* is of the caller's member and stamp, in ANOTHER
    session: another connection (SW-13c), or another MCP session of the same
    connection (SW-7). Only an agent caller passes a connection."""
    return (connection is not None and holder.member_email == me
            and holder.client_via == via
            and (str(holder.connection_id or "") != connection
                 or getattr(holder, "session_key", None) != session))


def _task_held(holder: Any, *, me: str | None = None, via: str | None = None,
               connection: str | None = None,
               session: str | None = None) -> HTTPException:
    """The 409 ``task_held``. The message names the holder, the client, the
    start and the last activity, and tells the caller to pick another task.

    SW-13c fix round 1: ``same_client`` is true when the holder is the
    caller's own run from another connection of the same client. Then the
    detail also carries ``run_id`` and :data:`SAME_CLIENT_HINT`, because only
    then may the caller release that run. Another member's run id never
    leaves."""
    name = holder.member_name or holder.member_email
    client = agent_oauth.client_label(holder.client_via)
    since = wire(holder.started_at or holder.created_at)
    last = wire(holder.last_activity_at)
    message = (
        f"Another AI session holds this task. {name} holds it through {client}. "
        f"The run began at {since}. The last activity was at {last}. "
        "Pick another task. Do not try this task again."
    )
    own = _same_client(holder, me, via, connection, session)
    detail: dict[str, Any] = {
        "error": TASK_HELD, "message": message, "same_client": own,
        "held_by": {
            "member": holder.member_email, "member_name": name,
            "client_via": holder.client_via, "client_name": client,
            "since": since, "last_activity_at": last,
        },
    }
    if own:
        detail["message"] = f"{message} {SAME_CLIENT_HINT}"
        detail["run_id"] = str(holder.id)
        detail["hint"] = SAME_CLIENT_HINT
    return HTTPException(status_code=409, detail=detail)


async def _system_activity(db: Any, run: Any, *, actor_email: str, via: str | None,
                           body: str, server_close: str | None = None) -> None:
    """One ``response`` row on *run* that the SERVER writes, as the record of
    a takeover or a release. No comment and no notification.

    SW-10a: *server_close* names why the server closed the run (``archive``,
    ``done``, ``cancelled`` or ``archived``). ``_server_closed`` reads it, so
    the agent may still leave one late summary."""
    content: dict[str, Any] = {"body": body, "system": True}
    if server_close is not None:
        content["server_close"] = server_close
    await db.execute(
        text(
            "INSERT INTO pm_agent_run_activities "
            "(run_id, task_id, type, content, ephemeral, signal, actor, actor_via) "
            "VALUES (CAST(:rid AS uuid), CAST(:tid AS uuid), 'response', "
            "        CAST(:content AS jsonb), false, NULL, :actor, :via)"
        ),
        {"rid": str(run.id), "tid": str(run.task_id), "actor": actor_email, "via": via,
         "content": _jsonb(content)},
    )


async def _take_over(db: Any, holder: Any, *, me: str, via: str | None) -> bool:
    """Move a run whose lease ended to ``abandoned``, and write the line
    "Taken over by <member>/<client>". False when the run moved in the
    meantime: a heartbeat changes ``last_activity_at``, so the guard misses
    and the caller reads the holder again."""
    ts = await _clock(db)
    moved = (await db.execute(
        text(
            "UPDATE pm_agent_runs SET status = :to, ended_at = :ts, session_key = NULL "
            " WHERE id = CAST(:rid AS uuid) AND status = ANY(:open) "
            "   AND last_activity_at = :seen RETURNING id"
        ),
        {"to": transition(holder.status, "abandon"), "ts": ts, "rid": str(holder.id),
         "open": list(AGENT_RUN_OPEN_STATUSES), "seen": holder.last_activity_at},
    )).fetchone()
    if moved is None:
        return False
    client = agent_oauth.client_label(via) if via else "the app"
    await _system_activity(
        db, holder, actor_email=me, via=via,
        body=f"Taken over by {await _member_name(db, me)}/{client}",
    )
    return True


def _free(holder: Any) -> bool:
    """True when *holder*, an open run of :data:`_HOLDER_SQL`, keeps its task
    no longer: its lease ended, or its connection is revoked or expired
    (SW-13c, ``filters.holder_free``)."""
    return holder_free(holder.status, float(holder.idle_s),
                       getattr(holder, "connection_gone", False) is True)


def _agent_session(user: UserContext) -> str | None:
    """SW-7 (spec §12.5). The MCP session key of an AGENT caller, from
    ``UserContext.agent``, which branch 0 fills from the SIGNED
    ``X-Agent-Session``. Never from a body. ``None`` for a person, and for an
    agent call outside an MCP session."""
    agent = getattr(user, "agent", None)
    return getattr(agent, "session_key", None) if agent is not None else None


def _agent_connection(user: UserContext) -> str | None:
    """SW-13c. The connection of an AGENT caller, from ``UserContext.agent``,
    which branch 0 builds from the signed ``X-Agent-Connection`` and the token
    row. Never from a body. ``None`` for a person."""
    agent = getattr(user, "agent", None)
    return str(agent.connection_id) if agent is not None else None


async def _gate_task(db: Any, task_id: str, me: str, *, via: str,
                     connection: str | None, session: str | None) -> Any | None:
    """The ONE gate of EA-14a, before any run opens on *task_id*.

    * The caller's own open run (same member, same client, EA12-6): that run.
      SW-13c: for an AGENT caller, *connection* is its own connection, and
      the run must carry it too. Two machines of one member share the stamp,
      so a run of the same member and stamp on ANOTHER connection is a
      holder like any other. A person passes ``None`` and keeps the old rule.
      SW-7: an agent's own run is of its own MCP *session* too. A run with no
      session key (a member start, or a run from before SW-7) counts as the
      own run of any session of its connection, and the gate stamps the
      caller's key on it (adoption). A sibling session that loses the race
      to adopt reads the run again and finds a holder.
    * No open run: None. The caller may insert.
    * Another run whose lease ended (and, for a question, three leases), or
      whose connection is revoked or expired (SW-13c): the run moves to
      ``abandoned``, and None comes back.
    * Any other open run: 409 ``task_held``.

    There is no task row lock. The arbiter is the partial unique index
    ``pm_agent_runs_one_open_per_task``: two callers that both read "free"
    both insert, and the second insert finds no row (``ON CONFLICT DO
    NOTHING``), so the caller runs this gate again and gets ``task_held``.
    A takeover locks only the old run, with the guard on ``last_activity_at``.
    So the gate never takes the task row before a run, which keeps the lock
    order of ``open_run_for_claim`` (run, then task row).
    """
    args = _open_run_args(task_id, me, via, connection, session)
    own = (await db.execute(text(_OPEN_RUN_SQL), args)).fetchone()
    if own is not None and session is not None and own.session_key is None:
        await db.execute(text(_ADOPT_SQL), {"rid": str(own.id), "sk": session})
        own = (await db.execute(text(_OPEN_RUN_SQL), args)).fetchone()
    if own is not None:
        return own
    for _ in range(3):
        holder = (await db.execute(
            text(_HOLDER_SQL), {"tid": task_id, "open": list(AGENT_RUN_OPEN_STATUSES)},
        )).fetchone()
        if holder is None:
            return None
        if not _free(holder):
            raise _task_held(holder, me=me, via=via, connection=connection,
                             session=session)
        if await _take_over(db, holder, me=me, via=via):
            return None
    raise HTTPException(status_code=409, detail="Try the call again.")


async def _clear_for_reopen(db: Any, run: Any, user: UserContext) -> None:
    """A member's prompt opens a ``completed`` run again. The task must have
    no OTHER open run, or the unique index would refuse the move with a 500.
    A run whose lease ended, or whose connection is revoked or expired
    (SW-13c), is taken over, as a claim would."""
    holder = (await db.execute(
        text(_HOLDER_SQL), {"tid": str(run.task_id), "open": list(AGENT_RUN_OPEN_STATUSES)},
    )).fetchone()
    if holder is None or str(holder.id) == str(run.id):
        return
    via = _agent_via(user)
    if not _free(holder) or not await _take_over(db, holder, me=_member(user), via=via):
        raise _task_held(holder, me=_member(user), via=via,
                         connection=_agent_connection(user),
                         session=_agent_session(user))


_INSERT_RUN_SQL = (
    "INSERT INTO pm_agent_runs "
    "(task_id, member_email, connection_id, client_via, status, started_by, "
    " started_at, external_link, last_activity_at, last_agent_activity_at, "
    " idempotency_key, session_key) "
    "VALUES (CAST(:tid AS uuid), :me, CAST(:cid AS uuid), :via, :status, :by, "
    "        :started, :link, :ts, :agent_ts, :key, CAST(:sk AS text)) "
    "ON CONFLICT DO NOTHING RETURNING id"
)


#: CIV-14a. The newest link of the run's own caller wins.
_SET_LINK_SQL = (
    "UPDATE pm_agent_runs SET external_link = :link "
    " WHERE id = CAST(:rid AS uuid) AND external_link IS DISTINCT FROM :link"
)


async def _set_run_link(db: Any, run_id: str, link: str | None) -> None:
    """Set the link of an open run that the caller already owns. A call with no
    link keeps the old one. The run came from the gate (``_gate_task``), so it
    is the run of this member and connection (CIV-14a)."""
    if link is None:
        return
    await db.execute(text(_SET_LINK_SQL), {"rid": run_id, "link": link})


#: The ONE open-run read of EA12-6: the open run of a task for a member and a
#: client stamp. The start, the archive request and the claim read it.
#:
#: It matches the stamp of the caller only. When the connection's client id
#: gives a slug, an open run under its legacy host stamp ``mcp:<host>`` is not
#: found (H-356 closed the window of EA-13a fix round 1, spec §12.2 item 1).
#: When it gives no slug, ``mcp:<host>`` is the caller's own stamp, so the
#: read finds that run.
#:
#: SW-13c: ``:cid`` is the connection of an AGENT caller, and the run must
#: carry it. A person binds NULL, and the read keeps the old rule (member and
#: stamp). One query for both, so the rule cannot fork.
#:
#: Fix round 1 (P2-b): a run whose connection is revoked or expired is never
#: anyone's own. So a person's start does not write into a dead run that the
#: next claim abandons: the gate takes it over, and a new run opens on the
#: live connection that the person names. An agent's own connection is live
#: (branch 0 refuses a dead one), so the arm changes nothing for an agent.
#:
#: SW-7 (spec §12.5): an agent also binds its MCP session key ``:sk``. Its
#: own run carries that key, or no key (the transition rule: a member start
#: and a run from before SW-7). An agent with no session owns only runs with
#: no key, so leaving the header out never reaches the run of a session. A
#: person binds NULL connection and skips the arm.
_OPEN_RUN_SQL = (
    f"{_RUN_SELECT} WHERE r.task_id = CAST(:tid AS uuid) AND r.member_email = :me "
    "   AND r.task_id IS NOT NULL"
    "   AND r.status = ANY(:open) AND r.client_via = :via "
    "   AND (CAST(:cid AS uuid) IS NULL OR r.connection_id = CAST(:cid AS uuid)) "
    "   AND (CAST(:cid AS uuid) IS NULL OR r.session_key IS NULL "
    "        OR r.session_key = CAST(:sk AS text)) "
    f"   AND NOT {connection_gone_sql('r')} "
    " ORDER BY r.created_at DESC LIMIT 1"
)


def _open_run_args(task_id: str, me: str, via: str,
                   connection: str | None, session: str | None) -> dict[str, Any]:
    """The binds of :data:`_OPEN_RUN_SQL`. *connection* (SW-13c) and
    *session* (SW-7) have no default, so each caller names them."""
    return {"tid": task_id, "me": me, "via": via, "cid": connection, "sk": session,
            "open": list(AGENT_RUN_OPEN_STATUSES)}


#: SW-7: the adoption of a run with no session key by the first agent
#: session that the gate finds it for. The guard on NULL makes a race of two
#: sibling sessions give the run to ONE of them.
_ADOPT_SQL = (
    "UPDATE pm_agent_runs SET session_key = :sk "
    " WHERE id = CAST(:rid AS uuid) AND session_key IS NULL"
)


async def _replay_start(db: Any, me: str, key: str | None, task_id: str) -> dict | None:
    """The first answer of a start that carried this ``idempotency_key``, or
    None. A key that started a run on another task is a 409."""
    if not key:
        return None
    by_key = (await db.execute(
        text(f"{_RUN_SELECT} WHERE r.member_email = :me AND r.idempotency_key = :key"
             " AND r.task_id IS NOT NULL"),
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

        # EA-14a: ONE AI for each task. The gate returns this caller's own
        # open run, or None for a free task, or it raises 409 `task_held`.
        # SW-13c: an agent's own run is of its own connection. A member's
        # start keeps the old rule (member and stamp), so it binds no
        # connection here, though its insert stores the one it names.
        own_connection = _agent_connection(user)
        own_session = _agent_session(user)
        existing = await _gate_task(db, task_id, me, via=client_via,
                                    connection=own_connection, session=own_session)
        if existing is None:
            ts = await _clock(db)
            made = (await db.execute(text(_INSERT_RUN_SQL), {
                "tid": task_id, "me": me, "cid": connection_id, "via": client_via,
                "status": "in_progress" if via is not None else "created",
                "by": via if via is not None else me,
                "started": ts if via is not None else None,
                "link": link, "ts": ts, "agent_ts": ts if via is not None else None,
                "key": payload.idempotency_key, "sk": own_session,
            })).fetchone()
            if made is None:  # a concurrent start won the open-run or the key index
                # A retry with the same key that lost the race replays the
                # winner, as the key read above would have, and writes nothing.
                replay = await _replay_start(db, me, payload.idempotency_key, task_id)
                if replay is not None:
                    response.status_code = 200
                    return replay
                existing = await _gate_task(db, task_id, me, via=client_via,
                                            connection=own_connection,
                                            session=own_session)
                if existing is None:
                    raise HTTPException(status_code=409, detail="Start the run again.")
        if existing is not None and via is not None:
            # CIV-14a: an agent's start on its open run sets the link.
            await _set_run_link(db, str(existing.id), link)
            existing = await _load_run(db, str(existing.id))
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
                f"{_RUN_SELECT} WHERE r.task_id IS NOT NULL AND {' AND '.join(clauses)} "
                " ORDER BY r.created_at DESC, r.id DESC LIMIT :limit OFFSET :offset"
            ),
            {**params, "limit": limit + 1, "offset": offset},
        )).fetchall()
        more = len(rows) > limit
        out = await _runs_out(db, list(rows[:limit]))
    return {"rows": out, "next_cursor": str(offset + limit) if more else None}


#: SW-43. Stamp ``read_at`` once on the live answer. The window of a withdraw
#: closes at the first agent read.
_STAMP_READ_SQL = (
    "UPDATE pm_agent_run_activities SET content = content || CAST(:mark AS jsonb)"
    " WHERE id = CAST(:aid AS uuid) AND NOT (content ? 'read_at')"
)


def _reads_for_member(run: Any, user: UserContext) -> bool:
    """SW-43. True when the caller is the AGENT of the run's own member and
    the flag is on. The agent of another member who sees the task reads the
    run and closes no window."""
    return (
        answer_change_enabled()
        and _agent_via(user) is not None
        and str(run.member_email).strip().lower() == _member(user)
    )


_LIVE_PROBE = live_answer_sql("p.run_id = CAST(:rid AS uuid)")


async def _live_unread(db: Any, run_id: str) -> Any | None:
    """The live answer of the run when no agent has read it, else None."""
    live = (await db.execute(text(_LIVE_PROBE), {"rid": run_id})).fetchone()
    if live is None or not await _unread(db, str(live.id)):
        return None
    return live


async def _stamp(db: Any, answer_id: str) -> None:
    await db.execute(text(_STAMP_READ_SQL), {
        "aid": answer_id, "mark": _jsonb({"read_at": (await _clock(db)).isoformat()}),
    })


async def _stamp_answer_read(db: Any, run: Any, user: UserContext) -> None:
    """SW-43. A read by the run's own agent closes the window of its live
    answer (the claim path). The plain probe takes no lock. The stamp takes
    the lock of the run, the one that the withdraw route takes, then looks
    again, so a read and a withdraw serialize: one wins."""
    if not _reads_for_member(run, user) or await _live_unread(db, str(run.id)) is None:
        return
    await _lock_run(db, str(run.id))
    live = await _live_unread(db, str(run.id))
    if live is not None:
        await _stamp(db, str(live.id))


async def _unread(db: Any, answer_id: str) -> bool:
    return not (await db.execute(
        text("SELECT content ? 'read_at' FROM pm_agent_run_activities"
             " WHERE id = CAST(:aid AS uuid)"), {"aid": answer_id},
    )).scalar()


async def _stamp_returned(db: Any, run_id: str, rows: list[Any], locked: bool) -> list[Any]:
    """SW-43. Stamp the live answer when the rows just read hold it, so an
    answer that an agent received is never unstamped. When the answer
    committed after the first probe, the lock is taken now and the probe runs
    again. An answer that a withdraw took back meanwhile is dropped from the
    rows."""
    live = await _live_unread(db, run_id)
    if live is None or str(live.id) not in {str(r.id) for r in rows}:
        return rows
    if not locked:
        await _lock_run(db, run_id)
        again = await _live_unread(db, run_id)
        if again is None or str(again.id) != str(live.id):
            return [r for r in rows if str(r.id) != str(live.id)]
    await _stamp(db, str(live.id))
    return rows


async def _read_run(user: UserContext, run_id: str, after: int | None) -> tuple[dict, list]:
    """One read of the long poll. It opens and closes its own session, and it
    checks the task again each time, so a revoke takes effect at the next
    read (EA12-4)."""
    async with _tenant_session() as db:
        run = await _load_visible_run(db, user, run_id)
        own = _reads_for_member(run, user)
        locked = False
        if own and await _live_unread(db, run_id) is not None:
            # A live answer waits: lock first, so a withdraw cannot slip in
            # between this read and the stamp.
            await _lock_run(db, run_id)
            locked = True
        rows = list((await db.execute(
            text(
                "SELECT * FROM pm_agent_run_activities "
                " WHERE run_id = CAST(:rid AS uuid) AND seq > :after "
                # SW-43. A withdrawn answer is gone from the read of the agent.
                "   AND NOT (type = 'prompt' AND content ? 'withdrawn') "
                " ORDER BY seq LIMIT :limit"
            ),
            {"rid": run_id, "after": -1 if after is None else after, "limit": MAX_ACTIVITIES},
        )).fetchall())
        if own:
            rows = await _stamp_returned(db, run_id, rows, locked)
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
    already holds this ``idempotency_key`` (R5). EA-18a: a person's answer to
    an open question needs :func:`may_answer_run`, or it gets 403."""
    run_id = _parse_uuid(run_id, "Agent run")
    _require_write_scope(user)
    agent = _agent_via(user) is not None
    _validate(payload, agent=agent)
    async with _tenant_session() as db:
        await _load_visible_run(db, user, run_id)
        run = await _lock_run(db, run_id)
        if not agent:
            await _refuse_an_outside_answer(db, run, user, payload)
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


async def _refuse_an_outside_answer(
    db: Any, run: Any, user: UserContext, payload: ActivityIn,
) -> None:
    """The write rule of D-EA-14 (spec §12.8), on a LOCKED run, so the status
    that it reads is the status that the write moves.

    It binds ONLY an answer: a person's ``prompt`` while the run is ``awaiting``
    on an open question that is not a typed archive request. D-EA-14 decided
    who may answer, and it decided nothing else. So a Stop, and a reply to a run
    that is not ``awaiting``, stay open to each member who sees the task
    (D-EA-10 point 1). The narrower archive rule stays in :func:`_answer`.

    The mark and the replay keep their order. A marked run still gets
    ``no_person_present`` first. A retry of a key that the run already holds
    returns its first row and writes nothing (R5).
    """
    if payload.type != "prompt" or payload.signal == "stop" or run.status != "awaiting":
        return
    if not agent_questions_allowed(getattr(user, "organization_id", None)):
        return
    run_id = str(run.id)
    _refuse_a_marked_prompt(payload.type, run_id)
    if await _existing_activity(db, run_id, payload.idempotency_key) is not None:
        return
    question = await _newest_open_question(db, run_id)
    if question is None or archive_request_of(question) is not None:
        return
    if await may_answer_run(db, run, user):
        return
    raise HTTPException(status_code=403, detail=ANSWER_REFUSED.format(
        name=getattr(run, "member_name", None) or run.member_email,
    ))


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
        if summary and run.status == "completed" and await _server_closed(db, run_id):
            # SW-10a fix round. The server closed this run (an approved
            # archive, or a closed task), and its agent now finishes with a
            # summary. Before, that answered 409 and the summary was lost.
            # The summary is kept as a `response`, and the run stays closed.
            written = await _write_activity(
                db, run, user, kind="response", key=payload.idempotency_key, body=summary,
                event="late_summary",
            )
        elif summary:
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


# ── Release (WS-55 EA-14a) ──────────────────────────────────────────────────

#: The permission that makes a member an org admin for a release. The same
#: gate as the billing routes. HANDOFF H-409 item 2: the Release button of the
#: run panel reads `access.is_admin`, and the owner has not yet decided
#: whether a read permission may end another member's run.
RELEASE_ADMIN_PERMISSION = "admin:members:read"


def person_may_end_sql(run: str) -> str:
    """Who may END a run, for a PERSON, in SQL over the run alias *run*
    (EA-14a): the run's member, or an assignee of its task. It binds ``:me``,
    the caller's address in lower case. An org admin may too, and
    :func:`may_end_run` adds that half from the context.

    ONE text. The release route and the resume route read it through
    :func:`may_end_run`. Needs attention (``needs_attention.py``, WS-55 SW-11a,
    spec §12.7) reads it for ``can_act`` on each row, so the list never
    offers a button that the route refuses.
    """
    return (
        f"({run}.member_email = :me OR EXISTS (SELECT 1 FROM pm_task_assignees ra"
        f" WHERE ra.task_id = {run}.task_id AND lower(ra.assignee) = :me))"
    )


async def may_end_run(db: Any, run: Any, user: UserContext) -> bool:
    """True when *user* may end *run*: a release (EA-14a) or a resume
    (SW-11a).

    * An agent ends its OWN run only: the same member and the same stamp.
    * A person ends a run when they are its member, an assignee of its task
      (:func:`person_may_end_sql`), or hold :data:`RELEASE_ADMIN_PERMISSION`.
    """
    via = _agent_via(user)
    me = _member(user)
    if via is not None:
        return run.member_email == me and run.client_via == via
    if user.has_permission(RELEASE_ADMIN_PERMISSION):
        return True
    held = (await db.execute(
        text(f"SELECT {person_may_end_sql('r')} FROM pm_agent_runs r"
             " WHERE r.id = CAST(:rid AS uuid) AND r.task_id IS NOT NULL"),
        {"rid": str(run.id), "me": me},
    )).scalar()
    return bool(held)


# ── Who may answer (WS-55 EA-18a, D-EA-14, spec §12.8) ──────────────────────

#: The organization roles that may answer any question the reader can see, and
#: that read the "Everyone" tab. A role, and not a permission: the owner has not
#: decided if a read permission may act on the run of another member (HANDOFF
#: H-409 item 2). "Manager" is this role, not the manager of a space.
ANSWER_REVIEW_ROLES: frozenset[str] = frozenset({"owner", "admin", "manager"})

#: The 403 of an answer from a person outside the set. It says who may answer
#: and what the person can do next. A comment stays open to them.
ANSWER_REFUSED = (
    "Only {name}, the creator of the task, its assignees, or an owner, admin or "
    "manager can answer this question. Ask one of them, or open the task and add "
    "a comment."
)


def answer_reason_sql(run: str, *, me: str = "me") -> str:
    """Why a person sees the question of the run alias *run*: ``created``,
    ``assigned`` or ``my_agent``, the first true test in that order, or NULL.

    ONE text with :func:`person_may_answer_sql`, so ``why`` and the answer set
    cannot disagree. It binds ``:me``, or the name in *me*, to the caller's
    address in lower case. Each address test compares with ``lower()``, because
    ``created_by`` holds the actor as written. The role test is not here, and it
    never goes into this text (:func:`holds_answer_role`).
    """
    return (
        "(CASE"
        " WHEN EXISTS (SELECT 1 FROM pm_tasks qt"
        f" WHERE qt.id = {run}.task_id AND lower(qt.created_by) = :{me}) THEN 'created'"
        " WHEN EXISTS (SELECT 1 FROM pm_task_assignees qa"
        f" WHERE qa.task_id = {run}.task_id AND lower(qa.assignee) = :{me}) THEN 'assigned'"
        f" WHEN lower({run}.member_email) = :{me} THEN 'my_agent'"
        " END)"
    )


def person_may_answer_sql(run: str, *, me: str = "me") -> str:
    """The three groups of D-EA-14: the creator of the task, an assignee, or
    the run's member, in SQL over the run alias *run*.

    ONE text, as :func:`person_may_end_sql` set for Release. The list reads it
    for ``can_answer`` and for "For me" (``agent_questions.py``). The write
    route reads it through :func:`may_answer_run`. ``filters.agent_focus_clause``
    reads it for "Waiting on me". So the list never offers a button that the
    route refuses.
    """
    return f"({answer_reason_sql(run, me=me)} IS NOT NULL)"


def holds_answer_role(user: UserContext) -> bool:
    """True for a PERSON with a role of :data:`ANSWER_REVIEW_ROLES`, from the
    session. The other half of the answer set, and the gate of the "Everyone"
    tab. A signed agent holds no role here: a prompt is a person's word, and a
    connection does not carry its member's rank.
    """
    if getattr(user, "agent", None) is not None:
        return False
    return bool(user.roles & ANSWER_REVIEW_ROLES)


async def may_answer_run(db: Any, run: Any, user: UserContext) -> bool:
    """True when *user* may answer the open question of *run* (D-EA-14 point
    2): the role test, or the one text of :func:`person_may_answer_sql`.

    The caller has already passed ``_load_visible_run``, so the reader sees the
    task (D87 rule 4). An agent never answers.
    """
    if _agent_via(user) is not None:
        return False
    if holds_answer_role(user):
        return True
    held = (await db.execute(
        text(f"SELECT {person_may_answer_sql('r')} FROM pm_agent_runs r"
             " WHERE r.id = CAST(:rid AS uuid) AND r.task_id IS NOT NULL"),
        {"rid": str(run.id), "me": _member(user)},
    )).scalar()
    return bool(held)


@router.post("/agent-runs/{run_id}/release", dependencies=_ENABLED)
async def release_agent_run(
    run_id: str, payload: ReleaseIn, user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Give a task up without finishing it: the run moves to ``released``.
    The task keeps its status, and every assignee stays.

    * An agent releases its OWN run only: the same member and the same
      client stamp.
    * A person releases a run when they are its member, an assignee of the
      task, or an org admin.
    * A caller who cannot see the task gets 404. A caller who sees it and
      has no right to release gets 403.
    * A run that is already ``released`` answers 200 again, with no write.
      Any other terminal run is 409.
    """
    run_id = _parse_uuid(run_id, "Agent run")
    _require_write_scope(user)
    via = _agent_via(user)
    me = _member(user)
    async with _tenant_session() as db:
        await _load_visible_run(db, user, run_id)
        run = await _lock_run(db, run_id)
        if not await may_end_run(db, run, user):
            raise HTTPException(
                status_code=403, detail="You may not release this agent run.",
            )
        if run.status != "released":
            new_status = transition(run.status, "release")
            ts = await _clock(db)
            await _move_run(db, run, new_status, ts=ts, user=user, via=via)
            client = agent_oauth.client_label(via) if via else "the app"
            await _system_activity(
                db, run, actor_email=me, via=via,
                body=f"Released by {await _member_name(db, me)}/{client}",
            )
        out_run = (await _runs_out(db, [await _load_run(db, run_id)]))[0]
    return {"run": out_run, "released": True}


# ── Resume (WS-55 SW-11a, spec §12.7) ───────────────────────────────────────

#: The fixed text of the member prompt that Resume writes. It holds no member
#: text, and the agent reads it as the member's answer (``needs_agent``
#: ``answered``).
RESUME_BODY = "Resume this work: pick this run up where it stopped."

#: The open recovery record of one run that still fits it. The rule is
#: ``filters.open_recovery_sql``, the text that the "stalled" kind of Needs
#: attention reads, so Resume takes exactly the rows that the list shows.
_OPEN_RECOVERY_SQL = (
    "SELECT s.id FROM pm_agent_run_strands s"
    " JOIN pm_agent_runs r ON r.id = s.run_id"
    f" WHERE r.id = CAST(:rid AS uuid) AND r.task_id IS NOT NULL"
    f"   AND {open_recovery_sql('s', 'r')}"
    " LIMIT 1"
)

#: True when the agent connection of the run is revoked or expired
#: (``filters.connection_gone_sql``). No session of that client can come back,
#: so Resume refuses, and Close the run is the act (spec §12.7).
_RUN_CONNECTION_GONE_SQL = (
    f"SELECT {connection_gone_sql('r')} FROM pm_agent_runs r WHERE r.id = CAST(:rid AS uuid)"
    " AND r.task_id IS NOT NULL"
)

#: The refusal of Resume on a run with a dead connection.
RESUME_CONNECTION_GONE = (
    "The agent connection of this run is revoked or expired, so no agent can"
    " resume it. Close the run instead."
)

#: SW-7: Resume hands the run to the next session of its connection.
_RESUME_KEY_SQL = "UPDATE pm_agent_runs SET session_key = NULL WHERE id = CAST(:rid AS uuid)"

#: True when the task of the run is closed (``filters.task_closed_sql``).
_RUN_TASK_CLOSED_SQL = (
    f"SELECT {task_closed_sql('r')} FROM pm_agent_runs r WHERE r.id = CAST(:rid AS uuid)"
    " AND r.task_id IS NOT NULL"
)


def _is_resume(row: Any) -> bool:
    """True when *row* is a prompt that Resume wrote (``content.resume``)."""
    return row.type == "prompt" and bool((from_jsonb(row.content) or {}).get("resume"))


@router.post("/agent-runs/{run_id}/resume", dependencies=_ENABLED)
async def resume_agent_run(
    run_id: str, payload: ResumeIn, user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Mark a stalled run as needing an agent again (SW-11a, spec §12.7).

    A member act on a run whose open recovery record still fits it. It writes
    ONE member ``prompt`` with :data:`RESUME_BODY`, through
    :func:`_write_activity`, the one write path of an activity. The run then
    reads ``needs_agent`` ``answered``, so ``list_agent_runs`` with
    ``needs_agent=true`` lists it and ``my_work`` offers it as
    ``run_answered``. The prompt renews the lease, and the strand stops
    fitting at once. The sweep clears it with ``new_activity`` at its next
    tick, and it stays the only writer of ``pm_agent_run_strands``.

    * The rule of Release decides who may (:func:`may_end_run`). An agent
      caller gets 403: it has its own paths.
    * A caller who cannot see the task gets 404. A closed task gets 409, and
      so does a run with no open recovery record that fits it, and a run
      whose agent connection is revoked or expired (Close the run instead).
    * It is quiet: the timeline keeps the prompt, and no bell and no event
      go out. It is a note to the agent, not a comment to people.
    * A replay of the key returns the first answer, and it writes nothing.
    * It never changes the task.
    """
    run_id = _parse_uuid(run_id, "Agent run")
    if _agent_via(user) is not None:
        raise HTTPException(status_code=403, detail="Only a member resumes an agent run.")
    written: _Written | None = None
    async with _tenant_session() as db:
        await _load_visible_run(db, user, run_id)
        run = await _lock_run(db, run_id)
        if not await may_end_run(db, run, user):
            raise HTTPException(status_code=403, detail="You may not resume this agent run.")
        replay = await _existing_activity(db, run_id, payload.idempotency_key)
        if replay is not None and not _is_resume(replay):
            raise HTTPException(status_code=409, detail=_KEY_USED)
        if replay is None:
            if (await db.execute(text(_RUN_TASK_CLOSED_SQL), {"rid": run_id})).scalar():
                raise HTTPException(
                    status_code=409,
                    detail="The task of this agent run is closed. Close the run instead.",
                )
            if (await db.execute(text(_OPEN_RECOVERY_SQL), {"rid": run_id})).fetchone() is None:
                raise HTTPException(
                    status_code=409,
                    detail="This agent run has no open recovery record, so nothing waits to resume.",
                )
            # Resume asks the SAME agent client to continue. A dead connection
            # can send nothing again, so only Close the run helps (§12.7).
            if (await db.execute(text(_RUN_CONNECTION_GONE_SQL), {"rid": run_id})).scalar():
                raise HTTPException(status_code=409, detail=RESUME_CONNECTION_GONE)
            # `quiet`: a note to the agent, not a comment to people. The
            # timeline keeps the row, and no bell and no event go out.
            written = await _write_activity(
                db, run, user, kind="prompt", key=payload.idempotency_key,
                body=RESUME_BODY, extra={"resume": True}, quiet=True,
            )
            # SW-7 (spec §12.5). The session that held the run went quiet past
            # its lease, so its key names nobody. A run with no key is the own
            # run of any session of its connection, so the next one adopts it.
            # A person's prompt keeps the key (`_move_run`), so Resume clears
            # it here, once, in the same transaction.
            await db.execute(text(_RESUME_KEY_SQL), {"rid": run_id})
        out_run = (await _runs_out(db, [await _load_run(db, run_id)]))[0]
        activity = written.activity if written is not None else replay
        out_activity = _activity_out(activity)
    # No `pm.task.comment_added`: a workflow that fires on a comment must not
    # take a note to the agent for a comment to people.
    return {"run": out_run, "activity": out_activity, "resumed": True,
            "created": written is not None and written.created}


_ANSWER_STALE = "The agent already has your answer. Send a comment instead."
_ANSWER_GONE = "This answer is no longer the live answer. The question may be open again."

#: SW-43. The live answer of ONE run, with ``changeable``, read under the lock.
_LIVE_ANSWER_ONE_SQL = live_answer_sql("p.run_id = CAST(:rid AS uuid)")

#: SW-43. The mark on the answer: it answers nothing, and it keeps the id of
#: the question in ``content_metadata`` so the history can name it.
_WITHDRAW_MARK_SQL = (
    "UPDATE pm_agent_run_activities SET in_reply_to = NULL,"
    " content = content || CAST(:mark AS jsonb),"
    " content_metadata = COALESCE(content_metadata, '{}'::jsonb)"
    "                    || CAST(:qmeta AS jsonb)"
    " WHERE id = CAST(:aid AS uuid)"
)


def _is_withdraw(row: Any) -> bool:
    """True when *row* is the marker prompt of a withdraw (``content.withdraws``)."""
    return row.type == "prompt" and bool((from_jsonb(row.content) or {}).get("withdraws"))


@router.post("/agent-runs/{run_id}/answer/withdraw",
             dependencies=[*_ENABLED, Depends(require_answer_change)])
async def withdraw_agent_answer(
    run_id: str, payload: WithdrawIn, user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Take back the answer of a member until an agent reads it or posts,
    whichever is first (SW-43, spec ``agent_coordination.md`` §3.9). An agent
    read (``get_agent_run``, a claim that finds the run) stamps
    ``content.read_at`` on the answer, under the lock of the run.

    * Only the run's member, and never an agent caller (403). The task must
      be visible (404).
    * Under the lock of the run, :func:`filters.answer_changeable_sql` must
      hold and *answer_id* must be the live answer, else 409. The agent post
      takes the same lock, so the two writes serialize: one wins.
    * The answer loses its ``in_reply_to``, so the question is open again for
      every read. A quiet marker prompt (``content.withdraws``) tells an agent
      that long polls. The run returns to ``awaiting``.
    * A replay of the key returns the first answer and writes nothing.
    """
    run_id = _parse_uuid(run_id, "Agent run")
    answer_id = _parse_uuid(payload.answer_id, "Answer")
    if _agent_via(user) is not None:
        raise HTTPException(status_code=403, detail="Only a member withdraws an answer.")
    written: _Written | None = None
    async with _tenant_session() as db:
        seen = await _load_visible_run(db, user, run_id)
        if seen.member_email != _member(user):
            raise HTTPException(status_code=403, detail="This agent run belongs to another member.")
        run = await _lock_run(db, run_id)
        replay = await _existing_activity(db, run_id, payload.idempotency_key)
        if replay is not None and not _is_withdraw(replay):
            raise HTTPException(status_code=409, detail=_KEY_USED)
        if replay is None:
            live = (await db.execute(text(_LIVE_ANSWER_ONE_SQL), {"rid": run_id})).fetchone()
            if live is None or str(live.id) != answer_id:
                raise HTTPException(status_code=409, detail=_ANSWER_GONE)
            if not live.changeable:
                raise HTTPException(status_code=409, detail=_ANSWER_STALE)
            ts = await _clock(db)
            await db.execute(text(_WITHDRAW_MARK_SQL), {
                "aid": answer_id,
                "mark": _jsonb({"withdrawn": {"at": ts.isoformat(), "by": _member(user)}}),
                "qmeta": _jsonb({"question_id": str(live.in_reply_to)}),
            })
            written = await _write_activity(
                db, run, user, kind="prompt", key=payload.idempotency_key,
                body="Withdrew the answer", extra=None, quiet=True,
                event="member_withdraw", withdraws=answer_id,
            )
        out_run = (await _runs_out(db, [await _load_run(db, run_id)]))[0]
        activity = written.activity if written is not None else replay
        out_activity = _activity_out(activity)
    return {"run": out_run, "activity": out_activity, "withdrawn": True,
            "created": written is not None and written.created}


# ── Stranded runs (WS-55 SW-10a, spec §12.6) ────────────────────────────────
#
# This module stays the only writer of `pm_agent_runs` and
# `pm_agent_run_activities`. The sweep (`gateway/agent_run_sweep.py`) decides WHICH
# run to close or to mark, and calls these three to write.


async def close_archived_run(db: Any, run: Any) -> None:
    """Close a run whose archive request the member approved (SW-10a, part 1
    item 2). The run moves to ``completed`` and gets
    :data:`ARCHIVE_CLOSED_BODY` as a system line, in the caller's
    transaction. *run* is a row of :data:`_RUN_SELECT` in ``in_progress``.

    The approval route calls it in the transaction of the archive, and the
    sweep calls it for a run of the old shape. No caller passes a user: the
    server closes the run, so no agent stamp and no session key move."""
    new_status = transition(run.status, "archive_close")
    await _move_run(db, run, new_status, ts=await _clock(db), user=None, via=None)
    await _system_activity(db, run, actor_email=run.member_email, via=None,
                           body=ARCHIVE_CLOSED_BODY, server_close="archive")


#: SW-10a. An open run of the OLD archive shape: the newest prompt of the run
#: approved an archive request (``content.archived``, which only the approval
#: route writes), the task is archived, and no agent activity followed. The
#: approval route closes a new one at once, so the sweep finds a run of the
#: deploy window only. ``SKIP LOCKED``: a writer that holds the run wins.
_ARCHIVE_APPLIED_SQL = (
    "SELECT r.id FROM pm_agent_runs r"
    " WHERE r.status = 'in_progress' AND r.task_id IS NOT NULL"
    f"   AND {AGENT_RUN_ANSWER_WAITS_SQL}"
    "   AND EXISTS (SELECT 1 FROM pm_tasks t WHERE t.id = r.task_id"
    "               AND t.archived_at IS NOT NULL)"
    "   AND (SELECT a.content->>'archived' FROM pm_agent_run_activities a"
    "         WHERE a.run_id = r.id AND a.type = 'prompt'"
    "         ORDER BY a.seq DESC LIMIT 1) = 'true'"
    " ORDER BY r.last_activity_at, r.id LIMIT :n FOR UPDATE OF r SKIP LOCKED"
)


async def archive_applied_run_ids(db: Any, *, limit: int) -> list[str]:
    """The open runs of the old archive shape, at most *limit*, locked for
    the caller's tenant transaction (SW-10a). The sweep closes each one with
    :func:`close_archived_run` in a savepoint of its own."""
    return [str(r.id) for r in (await db.execute(
        text(_ARCHIVE_APPLIED_SQL), {"n": limit})).fetchall()]


async def load_run(db: Any, run_id: str) -> Any:
    """One run as :data:`_RUN_SELECT` reads it, for the closers of the
    sweep (SW-10a)."""
    return await _load_run(db, run_id)


#: SW-10a fix round. The text of the system line of a run that the sweep
#: closed because its task is closed, by the reason.
CLOSED_TASK_BODIES: dict[str, str] = {
    "done": "Closed: the task is done.",
    "cancelled": "Closed: the task is cancelled.",
    "archived": "Closed: the task is archived.",
}

def _closed_task_runs_sql() -> str:
    """SW-10a. An ``in_progress`` run whose task is closed
    (``filters.task_closed_sql``) and whose holder is free by the rule of the
    gate (``filters.holder_free_sql``): its lease ended, or its connection is
    revoked or expired. The bar is the LEASE, not the stale band. An agent
    that moves its own task to done and then waits in a long call, for a
    deploy for example, keeps its run, and any activity, a member's answer
    too, renews the lease. ``why`` is ``archived`` first, then the status
    category. Built when a tick runs, because the lease is read then."""
    return (
        "SELECT r.id, CASE WHEN t.archived_at IS NOT NULL THEN 'archived'"
        "                  ELSE st.category END AS why"
        "  FROM pm_agent_runs r"
        "  JOIN pm_tasks t ON t.id = r.task_id"
        "  LEFT JOIN pm_task_statuses st ON st.id = t.status_id"
        " WHERE r.status = 'in_progress' AND r.task_id IS NOT NULL"
        f"   AND {holder_free_sql('r')}"
        f"   AND {task_closed_sql('r')}"
        " ORDER BY r.last_activity_at, r.id LIMIT :n FOR UPDATE OF r SKIP LOCKED"
    )


async def closed_task_runs(db: Any, *, limit: int) -> list[tuple[str, str]]:
    """``(run_id, why)`` of each open run whose task is closed and whose
    lease ended, at most *limit*, locked for the caller's tenant transaction
    (SW-10a)."""
    return [(str(r.id), str(r.why)) for r in (await db.execute(
        text(_closed_task_runs_sql()), {"n": limit})).fetchall()]


async def close_closed_task_run(db: Any, run: Any, why: str) -> None:
    """Close a run whose task is closed (SW-10a fix round). The run moves to
    ``completed`` and gets the line of :data:`CLOSED_TASK_BODIES`. The task is
    never touched. Nobody can claim a closed task, so a nudge would be false,
    and the run would hold nothing."""
    new_status = transition(run.status, "task_close")
    await _move_run(db, run, new_status, ts=await _clock(db), user=None, via=None)
    await _system_activity(db, run, actor_email=run.member_email, via=None,
                           body=CLOSED_TASK_BODIES[why], server_close=why)


#: SW-10a fix round. True when the newest activity of the run that is not
#: ephemeral is a server-close line. Then the run's agent may still leave
#: ONE summary: after it, the newest such activity is the summary.
_SERVER_CLOSED_SQL = (
    "SELECT (a.content ? 'server_close') AS closed FROM pm_agent_run_activities a"
    " WHERE a.run_id = CAST(:rid AS uuid) AND NOT a.ephemeral"
    " ORDER BY a.seq DESC LIMIT 1"
)


async def _server_closed(db: Any, run_id: str) -> bool:
    found = (await db.execute(text(_SERVER_CLOSED_SQL), {"rid": run_id})).fetchone()
    return found is not None and bool(found.closed)


async def write_strand_line(db: Any, run: Any, body: str) -> None:
    """The system line of a recovery record on *run* (SW-10a, second miss).
    It moves no status and no stamp, so the run stays stranded."""
    await _system_activity(db, run, actor_email=run.member_email, via=None, body=body)


async def member_name(db: Any, email: str) -> str:
    """The display name of a member, or the address (SW-10a texts)."""
    return await _member_name(db, email)


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
            "   AND r.task_id IS NOT NULL AND a.idempotency_key = :key "
            " ORDER BY a.seq DESC LIMIT 1"
        ),
        {"tid": task_id, "me": me, "key": key},
    )).fetchone()


async def _agent_open_run(db: Any, user: UserContext, task_id: str, me: str,
                          via: str, link: str | None = None) -> tuple[str | None, bool]:
    """The id of the agent's open run on the task, or of a new
    ``in_progress`` run (EA12-6), and True when this call made it.

    The ONE place where an agent's act, not a start, opens a run: the archive
    request (EA-6g) and the claim (EA-13c). It writes no activity and no
    comment. The id is None only when a concurrent start won the open-run
    index and then rolled back, which the caller answers as a retry.
    """
    # EA-14a: the gate returns the caller's own open run, None for a free
    # task, or raises 409 `task_held`. A lease that ended is taken over.
    existing = await _gate_task(db, task_id, me, via=via,
                                connection=_agent_connection(user),
                                session=_agent_session(user))
    if existing is not None:
        await _set_run_link(db, str(existing.id), link)
        return str(existing.id), False
    ts = await _clock(db)
    made = (await db.execute(text(_INSERT_RUN_SQL), {
        "tid": task_id, "me": me, "cid": str(user.agent.connection_id),  # type: ignore[union-attr]
        "via": via, "status": "in_progress", "by": via, "started": ts,
        "link": link, "ts": ts, "agent_ts": ts, "key": None,
        "sk": _agent_session(user),
    })).fetchone()
    if made is not None:
        return str(made.id), True
    # A concurrent start won the open-run index. Its run is the answer when it
    # is this caller's own. Another member's run is `task_held`.
    existing = await _gate_task(db, task_id, me, via=via,
                                connection=_agent_connection(user),
                                session=_agent_session(user))
    if existing is not None:
        await _set_run_link(db, str(existing.id), link)
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


async def open_run_for_claim(db: Any, task_id: str, user: UserContext,
                             link: str | None = None) -> str | None:
    """WS-55 EA-13c (spec §12.2 item 3, EA3-42). A claim by an agent opens
    its run, so the card reads "working" even when the agent skips
    ``start_agent_run``. ``claim_task`` calls it in the claim's OWN
    transaction, so a refused claim (C4, C5) rolls the run back with it.

    * Only for a signed agent claim (``UserContext.agent``) while
      ``agent_runs_enabled`` is on. A member's claim opens no run, and None
      comes back.
    * With an open run of this task, member and stamp (EA12-6), it writes
      nothing and returns that run. CIV-14a: a *link* (already checked by
      ``check_run_link``) is the one exception. It sets the link of the run
      or of the new run, and the newest link wins. Else it
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
    run_id, made = await _agent_open_run(db, user, task_id, _member(user),
                                         _agent_via(user) or "", link)
    if run_id is not None and not made:
        # SW-43. The claim finds the run of the agent: that is a read.
        await _stamp_answer_read(db, await _load_run(db, run_id), user)
    return run_id


def check_run_link(value: str | None) -> str | None:
    """The https check of ``external_link`` for the claim route (CIV-14a)."""
    return _check_link(value)


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
      ``content`` carries ``archived``. SW-10a: in the same transaction the
      run closes (:func:`close_archived_run`), so it never waits as
      ``answered`` for an agent that has nothing left to do.
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
        if archived and written.created:
            # SW-10a (spec §12.6, part 1 item 2). The archive was the agent's
            # disposition, so the run closes in the SAME transaction. Before,
            # the run read `answered` until an agent came back, and none did.
            await close_archived_run(db, await _load_run(db, run_id))
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


# ── A decision made outside Metorite (WS-55 MCP-G15, task 1567) ─────────────

#: The key of the marker in ``content_metadata`` of the agent's response.
DECIDED_ELSEWHERE = "decided_elsewhere"

#: The stable codes of the refusals. Each detail is ``{"error", "message"}``,
#: and the edge maps the code to its own fixed text (§9.6 rule 13).
OUTSIDE_NOT_YOURS = "run_not_yours"
OUTSIDE_NOT_AWAITING = "run_not_awaiting"
OUTSIDE_NO_QUESTION = "no_open_question"
OUTSIDE_TYPED = "typed_request"
OUTSIDE_SELECTED = "selected_refused"
OUTSIDE_CODES: tuple[str, ...] = (
    OUTSIDE_NOT_YOURS, OUTSIDE_NOT_AWAITING, OUTSIDE_NO_QUESTION, OUTSIDE_TYPED,
    OUTSIDE_SELECTED,
)


def _refusal(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"error": code, "message": message})


def typed_question(row: Any) -> bool:
    """True when the elicitation *row* is typed by the server.

    Only the server writes ``content_metadata`` on an elicitation, and no
    request body can carry it (``extra="forbid"``). Today the one kind is the
    archive request of EA-6g (:func:`archive_request_of`). Any later kind
    counts too: only the member answers a typed request, in Metorite.
    """
    return bool(from_jsonb(getattr(row, "content_metadata", None)))


def outside_comment(where: str, decision: str, labels: list[str]) -> str:
    """The timeline text: "Decided outside Metorite (Claude Code chat):
    Approve. <note>". The labels lead. When the decision already starts with
    them, the decision stands alone, so the text never says a label twice."""
    head = f"Decided outside Metorite ({where}): "
    picked = ", ".join(labels)
    if not labels or decision.casefold().startswith(picked.casefold()):
        return head + decision
    return f"{head}{picked}. {decision}"


def _marked_outside(row: Any) -> bool:
    """True when *row* is the response of an outside decision."""
    meta = from_jsonb(row.content_metadata) or {}
    return row.type == "response" and isinstance(meta.get(DECIDED_ELSEWHERE), dict)


@router.post("/agent-runs/{run_id}/outside-decision", status_code=201,
             dependencies=_ENABLED)
async def record_outside_decision(
    run_id: str, payload: OutsideDecisionIn, response: Response,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """The agent records a decision that its member made outside Metorite.

    The member can answer the agent's question in another place, such as the
    agent's own chat. Without this route the run stays ``awaiting``, and the
    task keeps asking the member for an answer that the member already gave.

    * **An agent act, never a member prompt.** The route writes one AGENT
      ``response`` through :func:`_write_activity`, the one write path. The
      server sets its ``in_reply_to`` to the open question and its
      ``content_metadata.decided_elsewhere`` (``where``, ``question_id`` and
      ``decision``). So every open-question read finds the question closed:
      ``_OPEN_ELICITATIONS_SQL``, :func:`_newest_open_question` and
      :func:`_has_answered_select`. The event ``agent_outside_decision``
      moves the run ``awaiting`` → ``in_progress``. The response is the
      newest agent activity, so the derived state reads ``working``.
    * **The agent's OWN run.** :func:`may_end_run` decides, the rule of
      Release: the same member and the same client stamp. Else 403
      ``run_not_yours``. A person gets 403: a person answers on the task.
    * **One open question, and not a typed one.** A run that is not
      ``awaiting`` gets 409 ``run_not_awaiting``. A run with no open question
      gets 409 ``no_open_question``. A typed request (EA-6g, and any later
      kind, :func:`typed_question`) gets 409 ``typed_request``: only the
      member answers it, in Metorite.
    * ``selected`` names option ids of that question, by the rule of a
      member's answer (:func:`option_labels`). Else 422 ``selected_refused``.
    * The same ``idempotency_key`` again returns the first response with
      200. A key of another activity gets 409.
    * **The bell** (fix round 1). The member's ``agent_question`` row of the
      question becomes read in the same transaction
      (:func:`_clear_question_bell`), so the bell and My Day stop asking.
      After the commit, ``personal.bump_my_day`` moves My Day.

    Fences: ``tests/unit/test_mcp_edge_outside_decision.py`` (R8 and
    database-free).
    """
    run_id = _parse_uuid(run_id, "Agent run")
    _require_write_scope(user)
    via = _agent_via(user)
    if via is None:
        raise HTTPException(
            status_code=403,
            detail="Only an agent records a decision made outside Metorite. "
                   "Answer the question on the task.",
        )
    decision = _clean_body(payload.decision)
    where = " ".join((payload.where or "").replace("\x00", "").split())
    if not decision or not where:
        raise HTTPException(status_code=422, detail="decision and where need text.")
    key = payload.idempotency_key
    async with _tenant_session() as db:
        await _load_visible_run(db, user, run_id)
        run = await _lock_run(db, run_id)
        if not await may_end_run(db, run, user):
            raise _refusal(403, OUTSIDE_NOT_YOURS,
                           "This agent run belongs to another member or another agent client.")
        replay = await _existing_activity(db, run_id, key)
        if replay is not None:
            if not _marked_outside(replay):
                raise HTTPException(status_code=409, detail=_KEY_USED)
            response.status_code = 200
            out_run = (await _runs_out(db, [run]))[0]
            return {"activity": _activity_out(replay), "run": out_run, "created": False}
        if run.status != "awaiting":
            raise _refusal(409, OUTSIDE_NOT_AWAITING,
                           f"This agent run is {run.status}. No question waits on the member.")
        question = await _newest_open_question(db, run_id)
        if question is None:
            raise _refusal(409, OUTSIDE_NO_QUESTION, "This agent run has no open question.")
        if typed_question(question):
            raise _refusal(409, OUTSIDE_TYPED,
                           "Only the member answers this request, on the task in Metorite.")
        labels = _outside_labels(question, payload.selected)
        record: dict[str, Any] = {
            "where": where, "question_id": str(question.id), "decision": decision,
        }
        written = await _write_activity(
            db, run, user, kind="response", key=key,
            body=outside_comment(where, decision, labels),
            event="agent_outside_decision", closes=str(question.id),
            metadata={DECIDED_ELSEWHERE: record},
            extra=({"selected": list(payload.selected), "labels": labels}
                   if payload.selected is not None else None),
        )
        if not written.created:
            response.status_code = 200
        # Fix round 1 (P2). The member answered, so the bell row of the
        # question, and the My Day card it makes, must go too.
        cleared = (await _clear_question_bell(db, _member(user), question)
                   if written.created else 0)
        out_run = (await _runs_out(db, [await _load_run(db, run_id)]))[0]
        out_activity = _activity_out(written.activity)
    if written.commented:
        await emit("pm.task.comment_added", {"task_id": out_run["task_id"]})
    if cleared:
        # After the commit, as `notifications.mark_read` does: a read note
        # leaves My Day, and a read emits no `pm.*` event.
        from gateway.routes.projects.personal import bump_my_day

        await bump_my_day(user.organization_id, _member(user))
    return {"activity": out_activity, "run": out_run, "created": written.created}


def _outside_labels(question: Any, selected: list[str] | None) -> list[str]:
    """The labels of the option ids that the member chose, or ``[]`` with no
    ids. A question with no options gets 422 ``selected_refused``, and so does
    an id that :func:`option_labels` refuses."""
    if selected is None:
        return []
    meta = from_jsonb(question.signal_metadata)
    if question.signal != "select" or not meta:
        raise _refusal(422, OUTSIDE_SELECTED, "selected names options, and this question has none.")
    try:
        return option_labels(meta, selected)
    except HTTPException as exc:
        raise _refusal(422, OUTSIDE_SELECTED, str(exc.detail)) from None


#: The member's unread bell rows of one question: ``kind = 'agent_question'``,
#: and ``activity_id`` is the timeline row of the question (``_comment``
#: writes the bell with ``activity_id`` = that row).
_QUESTION_BELL_SQL = (
    "SELECT id FROM pm_notifications"
    " WHERE recipient = :me AND kind = 'agent_question'"
    " AND activity_id = CAST(:aid AS uuid) AND read_at IS NULL"
)


async def _clear_question_bell(db: Any, me: str, question: Any) -> int:
    """Mark the member's bell row of *question* read, in the caller's
    transaction, through the ONE writer of ``read_at``
    (``notifications.mark_rows_read``). Only the member's own row: the bell
    of an assignee or a watcher stays theirs to read. The count it marked."""
    activity = getattr(question, "pm_activity_id", None)
    if activity is None:
        return 0
    found = (await db.execute(text(_QUESTION_BELL_SQL),
                              {"me": me, "aid": str(activity)})).fetchall()
    if not found:
        return 0
    return await mark_rows_read(db, me, [str(row.id) for row in found])


__all__ = [
    "ACTIVITY_TYPES",
    "ARCHIVE_APPROVE",
    "ARCHIVE_CLOSED_BODY",
    "ARCHIVE_DECLINE",
    "ARCHIVE_OPTIONS",
    "ARCHIVE_REQUEST_KIND",
    "ARCHIVE_REQUEST_TTL",
    "CLOSED_TASK_BODIES",
    "DECIDED_ELSEWHERE",
    "EPHEMERAL_CAP",
    "MAX_WAIT_S",
    "OUTSIDE_CODES",
    "OUTSIDE_NOT_AWAITING",
    "OUTSIDE_NOT_YOURS",
    "OUTSIDE_NO_QUESTION",
    "OUTSIDE_SELECTED",
    "OUTSIDE_TYPED",
    "POLL_S",
    "RUN_STATUSES",
    "TASK_HELD",
    "ActivityIn",
    "ArchiveApprovalIn",
    "ArchiveRequestIn",
    "FinishIn",
    "OptionIn",
    "OutsideDecisionIn",
    "ReleaseIn",
    "StartIn",
    "archive_applied_run_ids",
    "archive_request_of",
    "close_archived_run",
    "close_closed_task_run",
    "closed_task_runs",
    "load_run",
    "member_name",
    "option_labels",
    "outside_comment",
    "record_outside_decision",
    "transition",
    "typed_question",
    "write_strand_line",
]
