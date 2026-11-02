"""One person decides (WS-ALN ALN-2a).

Spec: ``project-docs/specs/project_alignment.md`` §3.2, §7 and §13.

A decision is a question with two to four named options. It goes to one named
person (the *decider*) about one task. The answer is on the record: the table
``pm_decisions`` keeps it, and ``record_activity`` writes one ``comment`` on
the task. A decision records an answer and runs no action, so it is not a
third approval stack.

**Three verbs.** *Ask* (:func:`ask_decision`), *answer* (:func:`answer_decision`)
and *expire*. A decision expires by the clock: ``expires_at`` passes, and the
answer path refuses it. Nothing is written at that moment, so no sweep is
needed.

**Who may ask.** Only server code. ALN-7 and AUTO-7 call :func:`ask_decision`
with a session of their own. This module has NO ask route: an HTTP ask would
let a member, or an agent that holds a member's connection, put a card on
another member's bell. The answer route and two read routes are here.

**Who may answer.** The named decider and nobody else. The decider is compared
in lower case with the address of the session. The answer never takes its
author from the request. An agent connection is refused, because the answer
is a person's word. A run that no person watches is refused as well
(``acb_common.no_person``, AUTO-7a), before any read or write.

**One answer.** ``UPDATE ... WHERE answered_at IS NULL AND expires_at > now()``
decides the race. Two concurrent answers end as one answer and one refusal.
The trigger ``pm_decisions_guard`` of the migration is the second wall.

**Who hears.** The ask refuses a decider who cannot open the task, and writes
no row for them (``notifications.deliverable``, §7.2 item 1). The bell row
goes through ``notifications.notify``, the one bell writer. The flag
``DECISIONS_ORGS`` (default empty) gates the whole ask and every route. Off,
nothing is written and nothing reaches a member. This slice sends nothing
through Reach: the My Day provider is ALN-2b.

**Page subjects.** The table allows ``page``. The service refuses it in
ALN-2a. A page decision needs the Knowledge visibility predicate for a person
who is not the caller, and ``visible_pages_clause`` takes a ``UserContext``.
The answer needs a task for its activity row too.

**No score for any person (ALN-Q5).** No function here counts, groups or ranks
by ``decider`` or ``answered_by``. ``tests/unit/test_decisions.py`` scans for
it.

Fence: ``tests/unit/test_decisions.py`` (R8).
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import timedelta
from typing import Any

from acb_auth import UserContext, get_current_user
from acb_common import get_logger, get_settings
from acb_common.db import current_tenant
from acb_common.no_person import NO_PERSON_PRESENT, NO_PERSON_TEXT, no_person_present
from fastapi import Depends, HTTPException, Query
from gateway.routes.projects.core import (
    _tenant_session,
    actor,
    emit,
    load_visible_task,
    record_activity,
    resolve_visibility,
    router,
)
from gateway.routes.projects.notifications import deliverable, notify
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

_log = get_logger("gateway.projects.decisions")

#: The longest question, in characters (spec §3.2). The CHECK holds it too.
QUESTION_MAX = 140
#: Two to four options (spec §3.2). The CHECK holds it too.
OPTIONS_MIN, OPTIONS_MAX = 2, 4
#: An option key is a short token. A label is one short line.
_KEY = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
LABEL_MAX = 80
#: The optional reason of an answer. The CHECK holds it too.
REASON_MAX = 500
#: ``pm_decisions.source``.
SOURCES: tuple[str, ...] = ("align", "auto")
#: ``pm_decisions.subject_kind``. Only a task is open in ALN-2a.
SUBJECT_KINDS: tuple[str, ...] = ("task", "page")
OPEN_SUBJECT_KINDS: tuple[str, ...] = ("task",)
#: The default life of a decision is the column default, 7 days. A caller may
#: ask for a shorter or longer life inside these bounds.
MIN_LIFE = timedelta(minutes=1)
MAX_LIFE = timedelta(days=30)
#: The most rows a list returns.
LIST_LIMIT = 50


class DecisionRefused(Exception):
    """The ask wrote nothing. ``reason`` is one of the codes below."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


# ── The flag ────────────────────────────────────────────────────────────────


def decisions_allowed(organization_id: str | None) -> bool:
    """True when ``DECISIONS_ORGS`` holds the org, or holds ``*``. Default OFF.

    The org comes from the session or the bound tenant, never from request
    input (R5). It reads ``get_settings()`` on each call, so a flag reload
    applies a change with no restart.
    """
    org = str(organization_id or "").strip().lower()
    if not org:
        return False
    raw = str(getattr(get_settings(), "decisions_orgs", "") or "")
    listed = {p.strip().lower() for p in raw.split(",") if p.strip()}
    return "*" in listed or org in listed


async def require_decisions(user: UserContext = Depends(get_current_user)) -> None:
    """404 while ``DECISIONS_ORGS`` does not hold the caller's org."""
    if not decisions_allowed(getattr(user, "organization_id", None)):
        raise HTTPException(status_code=404, detail="Not Found")


_ENABLED = [Depends(require_decisions)]


# ── The checks ──────────────────────────────────────────────────────────────


def clean_options(options: Any) -> list[dict[str, str]]:
    """Two to four ``{key, label}`` objects with unique keys, or a refusal."""
    if not isinstance(options, (list, tuple)) or not (
        OPTIONS_MIN <= len(options) <= OPTIONS_MAX
    ):
        raise DecisionRefused(
            "bad_options", f"A decision takes {OPTIONS_MIN} to {OPTIONS_MAX} options.",
        )
    out: list[dict[str, str]] = []
    for item in options:
        if not isinstance(item, dict) or set(item) != {"key", "label"}:
            raise DecisionRefused("bad_options", "Each option is {key, label} and nothing more.")
        key, label = item["key"], item["label"]
        if not isinstance(key, str) or not _KEY.fullmatch(key):
            raise DecisionRefused("bad_options", "An option key is a short lower-case token.")
        label = label.strip() if isinstance(label, str) else ""
        if not label or len(label) > LABEL_MAX:
            raise DecisionRefused(
                "bad_options", f"An option label is 1 to {LABEL_MAX} characters.",
            )
        out.append({"key": key, "label": label})
    if len({o["key"] for o in out}) != len(out):
        raise DecisionRefused("bad_options", "Option keys must differ.")
    return out


def decision_state(row: Any) -> str:
    """``answered``, ``expired`` or ``open``, from the row and the DB clock
    that the SELECT carried in ``is_expired``."""
    if row.answered_at is not None:
        return "answered"
    return "expired" if row.is_expired else "open"


# ── Ask (server code only) ──────────────────────────────────────────────────


async def ask_decision(
    db: Any,
    *,
    task_id: str,
    decider: str,
    question: str,
    options: Any,
    asked_by: str,
    source: str = "align",
    payload: dict[str, Any] | None = None,
    subject_kind: str = "task",
    expires_in: timedelta | None = None,
) -> dict[str, Any]:
    """Ask one named person to decide, on the caller's session. Server code only.

    Raises :class:`DecisionRefused` and writes nothing when: the flag is off
    for the bound org, the question or the options are malformed, the subject
    is a page, or the decider cannot open the task. Otherwise it inserts the
    row and writes the bell row of the decider, in the caller's transaction.
    It does not commit. It returns the decision as the wire shows it, with
    ``heard``: whether a bell row was written.
    """
    if not decisions_allowed(current_tenant()):
        raise DecisionRefused("off", "Decisions are off for this organization.")
    if subject_kind not in OPEN_SUBJECT_KINDS:
        raise DecisionRefused("subject_kind", "ALN-2a asks about a task only.")
    if source not in SOURCES:
        raise DecisionRefused("source", "source is align or auto.")
    q = (question or "").strip()
    if not q or len(q) > QUESTION_MAX:
        raise DecisionRefused("bad_question", f"A question is 1 to {QUESTION_MAX} characters.")
    opts = clean_options(options)
    who = (decider or "").strip().lower()
    if not who or "@" not in who:
        raise DecisionRefused("bad_decider", "Name the decider by email address.")
    asker = (asked_by or "").strip()
    if not asker:
        raise DecisionRefused("bad_asker", "Name who asks.")
    try:
        tid = str(uuid.UUID(str(task_id)))
    except (TypeError, ValueError) as exc:
        raise DecisionRefused("no_task", "The task is not found.") from exc
    life = expires_in if expires_in is not None else timedelta(days=7)
    if not (MIN_LIFE <= life <= MAX_LIFE):
        raise DecisionRefused("bad_life", "A decision lives 1 minute to 30 days.")
    extra = payload or {}
    if not isinstance(extra, dict):
        raise DecisionRefused("bad_payload", "payload is an object.")

    # §7.2 item 1: a card never reaches a person who cannot open the task.
    # No row is written for such a decider.
    if not await deliverable(db, [who], tid):
        raise DecisionRefused("decider_cannot_open", "The decider cannot open this task.")

    made = (await db.execute(
        text(
            "INSERT INTO pm_decisions (subject_kind, subject_id, decider, options,"
            " question, asked_by, source, payload, expires_at) "
            "VALUES ('task', CAST(:sid AS uuid), :decider, CAST(:options AS jsonb),"
            " :question, :by, :source, CAST(:payload AS jsonb),"
            " now() + make_interval(secs => :secs)) "
            "RETURNING *, (expires_at <= now()) AS is_expired"
        ),
        {
            "sid": tid, "decider": who, "options": json.dumps(opts), "question": q,
            "by": asker, "source": source, "payload": json.dumps(extra),
            "secs": life.total_seconds(),
        },
    )).fetchone()
    heard = await notify(
        db, recipients=[who], kind="decision", task_id=tid, actor_id=asker, excerpt=q,
    )
    out = _wire(made)
    out["heard"] = who in heard["notified"]
    return out


# ── Answer ──────────────────────────────────────────────────────────────────


def _refuse_no_person() -> None:
    """AUTO-7a: a run with no person present answers nothing, before any read
    or write. The mark is a ContextVar, so it binds an in-process caller."""
    if no_person_present():
        _log.warning("decisions.no_person_refused")
        raise HTTPException(
            status_code=403,
            detail={"error": NO_PERSON_PRESENT, "message": NO_PERSON_TEXT},
        )


_SELECT = (
    "SELECT d.*, (d.expires_at <= now()) AS is_expired FROM pm_decisions d"
)


async def answer_decision(
    db: Any, user: UserContext, decision_id: str, answer_key: str, reason: str | None,
) -> tuple[dict[str, Any], str]:
    """Record the answer of the decider. Returns the decision and its task id.

    404 for a decision that the caller does not hold, for any reason: a
    decision of another decider is never confirmed. 409 ``decision_answered``
    or ``decision_expired`` only for the decider. 422 for a key that is not an
    option. Does not commit and does not emit.
    """
    _refuse_no_person()
    if getattr(user, "agent", None) is not None:
        raise HTTPException(status_code=403, detail="A person answers a decision.")
    me = actor(user).strip().lower()
    try:
        did = str(uuid.UUID(str(decision_id)))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="Decision not found") from exc
    note = (reason or "").strip() or None
    if note is not None and len(note) > REASON_MAX:
        raise HTTPException(status_code=422, detail=f"The reason is at most {REASON_MAX} characters.")

    row = (await db.execute(
        text(f"{_SELECT} WHERE d.id = CAST(:id AS uuid)"), {"id": did},
    )).fetchone()
    if row is None or str(row.decider).lower() != me or row.subject_kind != "task":
        raise HTTPException(status_code=404, detail="Decision not found")
    task_id = str(row.subject_id)
    # The decider must still open the task. Else the answer is not theirs to give.
    if not await deliverable(db, [me], task_id):
        raise HTTPException(status_code=404, detail="Decision not found")
    labels = {str(o["key"]): str(o["label"]) for o in row.options}
    if answer_key not in labels:
        raise HTTPException(status_code=422, detail="answer_key is not one of the options.")

    won = (await db.execute(
        text(
            "UPDATE pm_decisions SET answered_by = :me, answer_key = :key,"
            " answer_reason = :reason, answered_at = now() "
            "WHERE id = CAST(:id AS uuid) AND lower(decider) = :me"
            " AND answered_at IS NULL AND expires_at > now() "
            "RETURNING *, (expires_at <= now()) AS is_expired"
        ),
        {"id": did, "me": me, "key": answer_key, "reason": note},
    )).fetchone()
    if won is None:
        # Lost the race, or late. The decider may know which.
        again = (await db.execute(
            text(f"{_SELECT} WHERE d.id = CAST(:id AS uuid)"), {"id": did},
        )).fetchone()
        if again is not None and again.answered_at is not None:
            raise HTTPException(status_code=409, detail="decision_answered")
        if again is not None and again.is_expired:
            raise HTTPException(status_code=409, detail="decision_expired")
        raise HTTPException(status_code=404, detail="Decision not found")

    body = f"Decision: {won.question} Answer: {labels[answer_key]}"
    if note:
        body += f" Reason: {note}"
    await record_activity(
        db, activity_type="comment", created_by=me, task_id=task_id, body=body,
        meta={"decision_id": did, "decision_answer": answer_key},
    )
    return _wire(won), task_id


# ── The wire ────────────────────────────────────────────────────────────────


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def _wire(row: Any) -> dict[str, Any]:
    """One decision as the routes show it. ``payload`` stays server-side."""
    return {
        "id": str(row.id),
        "subject_kind": row.subject_kind,
        "subject_id": str(row.subject_id),
        "decider": row.decider,
        "options": list(row.options),
        "question": row.question,
        "asked_by": row.asked_by,
        "source": row.source,
        "state": decision_state(row),
        "created_at": _iso(row.created_at),
        "expires_at": _iso(row.expires_at),
        "answered_by": row.answered_by,
        "answer_key": row.answer_key,
        "answer_reason": row.answer_reason,
        "answered_at": _iso(row.answered_at),
    }


class AnswerIn(BaseModel):
    """The answer. Nobody is named in the body: the session is the answerer."""

    model_config = ConfigDict(extra="forbid")

    answer_key: str = Field(min_length=1, max_length=32)
    reason: str | None = Field(default=None, max_length=REASON_MAX)


# ── Routes ──────────────────────────────────────────────────────────────────


@router.get("/decisions", dependencies=_ENABLED)
async def list_decisions(
    task_id: str | None = Query(default=None),
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """With ``task_id``: the decisions on a task the caller can open, newest
    first. Without: the caller's own open decisions. No count of people."""
    async with _tenant_session() as db:
        if task_id is not None:
            try:
                tid = str(uuid.UUID(task_id))
            except ValueError as exc:
                raise HTTPException(status_code=404, detail="Task not found") from exc
            await load_visible_task(db, await resolve_visibility(db, user), tid)
            rows = (await db.execute(
                text(
                    f"{_SELECT} WHERE d.subject_kind = 'task'"
                    " AND d.subject_id = CAST(:tid AS uuid)"
                    " ORDER BY d.created_at DESC LIMIT :lim"
                ),
                {"tid": tid, "lim": LIST_LIMIT},
            )).fetchall()
        else:
            rows = (await db.execute(
                text(
                    f"{_SELECT} WHERE lower(d.decider) = :me AND d.answered_at IS NULL"
                    " AND d.expires_at > now() AND d.subject_kind = 'task'"
                    " ORDER BY d.created_at DESC LIMIT :lim"
                ),
                {"me": actor(user).strip().lower(), "lim": LIST_LIMIT},
            )).fetchall()
        # §7.2: of the tasks listed for the decider, drop one they cannot open.
        wire = [_wire(r) for r in rows]
        if task_id is None:
            keep: list[dict[str, Any]] = []
            for item in wire:
                if await deliverable(db, [actor(user).strip().lower()], item["subject_id"]):
                    keep.append(item)
            wire = keep
    return {"decisions": wire}


@router.post("/decisions/{decision_id}/answer", dependencies=_ENABLED)
async def answer(
    decision_id: str, payload: AnswerIn, user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """The named decider answers once. 404 for anyone else. 409 for a second
    answer or a late one. 403 for a run with no person present, which
    :func:`answer_decision` refuses before it reads or writes."""
    async with _tenant_session() as db:
        out, task_id = await answer_decision(
            db, user, decision_id, payload.answer_key, payload.reason,
        )
    await emit("pm.task.comment_added", {"task_id": task_id})
    return {"decision": out}
