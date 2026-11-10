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

**Who may ask.** Server code, and a person (WS-AP AP-8a). ALN-7 and AUTO-7
call :func:`ask_decision` with a session of their own. The one HTTP ask is
the *person ask* (source ``person``, ``waiting_on_me.md`` §7.2):
``POST /projects/tasks/{task_id}/asks``. Its guards hold that a member, and
never an agent that holds a member's connection, puts the card on the bell.
It refuses an agent connection and a run with no person present before any
read, an addressee who cannot open the task, the caller as the addressee, a
second open ask for one task and one pair, and the 31st ask of one caller in
one day. The asker withdraws an ask with ``POST /projects/asks/{id}/withdraw``.
The person ask routes need ``DECISIONS_ORGS`` AND ``WAITING_ORGS``. Either
off, each answers 404.

**Who may answer.** The named decider and nobody else. The decider is compared
in lower case with the address of the session. The answer never takes its
author from the request. An agent connection is refused, because the answer
is a person's word. A run that no person watches is refused as well
(``acb_common.no_person``, AUTO-7a), before any read or write.

**One answer.** ``UPDATE ... WHERE answered_at IS NULL AND closed_at IS NULL
AND expires_at > now()`` decides the race. Two concurrent answers end as one answer and one refusal.
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
from datetime import UTC, datetime, timedelta
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
SOURCES: tuple[str, ...] = ("align", "auto", "person")
#: ``pm_decisions.subject_kind``. Only a task is open in ALN-2a.
SUBJECT_KINDS: tuple[str, ...] = ("task", "page")
OPEN_SUBJECT_KINDS: tuple[str, ...] = ("task",)
#: The default life of a decision is the column default, 7 days. A caller may
#: ask for a shorter or longer life inside these bounds.
MIN_LIFE = timedelta(minutes=1)
MAX_LIFE = timedelta(days=30)
#: The most rows a list returns.
LIST_LIMIT = 50

#: A person ask (WS-AP AP-8a, ``waiting_on_me.md`` §7.1 and §7.2).
#: Its life: the due date plus a week, or two weeks with no date.
ASK_AFTER_DUE = timedelta(days=7)
ASK_NO_DUE_LIFE = timedelta(days=14)
#: The most asks one caller opens in one day (guard 5). The guard reads the
#: row at this offset and keeps no number (ALN-Q5, V6).
ASK_DAY_LIMIT = 30
#: The keys that the service adds to a person ask.
REPLY_KEY, NOT_ME_KEY = "reply", "not_me"
_REPLY = {"key": REPLY_KEY, "label": "Reply"}
_NOT_ME = {"key": NOT_ME_KEY, "label": "Not me"}
#: An ask with choices takes 2 or 3 of them. The service adds ``not_me``.
CHOICES_MIN, CHOICES_MAX = 2, 3


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


async def require_waiting(user: UserContext = Depends(get_current_user)) -> None:
    """404 while ``WAITING_ORGS`` does not hold the caller's org. The person
    ask is a part of "Waiting", so it reads that flag through its one reader
    (``routes/shell/waiting.waiting_allowed``). The import is local, because
    the shell package imports this module."""
    from gateway.routes.shell.waiting import waiting_allowed

    if not waiting_allowed(getattr(user, "organization_id", None)):
        raise HTTPException(status_code=404, detail="Not Found")


#: The person ask routes need both flags (AP-8a). Either off answers 404.
_ASK_ENABLED = [Depends(require_decisions), Depends(require_waiting)]


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
    """``answered``, ``closed``, ``expired`` or ``open``, from the row and the
    DB clock that the SELECT carried in ``is_expired``. A person ask is
    ``closed`` when its asker withdrew it (AP-8a)."""
    if row.answered_at is not None:
        return "answered"
    if getattr(row, "closed_at", None) is not None:
        return "closed"
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
    due_at: datetime | None = None,
) -> dict[str, Any]:
    """Ask one named person to decide, on the caller's session. Server code only.

    Raises :class:`DecisionRefused` and writes nothing when: the flag is off
    for the bound org, the question or the options are malformed, the subject
    is a page, or the decider cannot open the task. Otherwise it inserts the
    row and writes the bell row of the decider, in the caller's transaction.
    It does not commit. It returns the decision as the wire shows it, with
    ``heard``: whether a bell row was written. ``due_at`` is the date of a
    person ask (AP-8a). The caller computes ``expires_in`` from it.
    """
    if not decisions_allowed(current_tenant()):
        raise DecisionRefused("off", "Decisions are off for this organization.")
    if subject_kind not in OPEN_SUBJECT_KINDS:
        raise DecisionRefused("subject_kind", "ALN-2a asks about a task only.")
    if source not in SOURCES:
        raise DecisionRefused("source", "source is align, auto or person.")
    q = (question or "").strip()
    if not q or len(q) > QUESTION_MAX:
        raise DecisionRefused("bad_question", f"A question is 1 to {QUESTION_MAX} characters.")
    opts = clean_options(options)
    who = (decider or "").strip().lower()
    if not who or "@" not in who or who.startswith("agent:"):
        raise DecisionRefused("bad_decider", "Name the decider by the email address of a person.")
    asker = (asked_by or "").strip()
    if not asker:
        raise DecisionRefused("bad_asker", "Name who asks.")
    if asker.lower() == who:
        raise DecisionRefused("self_decision", "A person does not ask themselves to decide.")
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
            " question, asked_by, source, payload, expires_at, due_at) "
            "VALUES ('task', CAST(:sid AS uuid), :decider, CAST(:options AS jsonb),"
            " :question, :by, :source, CAST(:payload AS jsonb),"
            " now() + make_interval(secs => :secs), :due) "
            "RETURNING *, (expires_at <= now()) AS is_expired"
        ),
        {
            "sid": tid, "decider": who, "options": json.dumps(opts), "question": q,
            "by": asker, "source": source, "payload": json.dumps(extra),
            "secs": life.total_seconds(), "due": due_at,
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
    decision of another decider is never confirmed. 409 ``decision_answered``,
    ``ask_closed`` or ``decision_expired`` only for the decider. 422 for a key
    that is not an option, and for a ``reply`` to a person ask with no words.
    Does not commit and does not emit.
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
    # AP-8a: a reply to a person ask is words. The reason holds them.
    if row.source == "person" and answer_key == REPLY_KEY and note is None:
        raise HTTPException(status_code=422, detail="A reply needs the words of the reply.")

    won = (await db.execute(
        text(
            "UPDATE pm_decisions SET answered_by = :me, answer_key = :key,"
            " answer_reason = :reason, answered_at = now() "
            "WHERE id = CAST(:id AS uuid) AND lower(decider) = :me"
            " AND answered_at IS NULL AND closed_at IS NULL AND expires_at > now() "
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
        if again is not None and again.closed_at is not None:
            raise HTTPException(status_code=409, detail="ask_closed")
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
    # ALN-2b: the answer closes the card at once. The bell row of this ask is
    # read in the same transaction, so My Day keeps no card for a question
    # that is answered. The row has no decision id, and a column for one needs
    # a migration (``activity_id`` is a foreign key to a comment). So the
    # excerpt (the question, which ``ask_decision`` gave ``notify`` whole)
    # picks the ask, and ONE row closes for ONE answer: two asks with the same
    # question close one after the other, never together.
    await db.execute(
        text(
            "UPDATE pm_notifications SET read_at = now() "
            "WHERE id = (SELECT n.id FROM pm_notifications n"
            " WHERE n.recipient = :me AND n.kind = 'decision' AND n.read_at IS NULL"
            " AND n.task_id = CAST(:tid AS uuid) AND n.excerpt IS NOT DISTINCT FROM :ex"
            " ORDER BY n.created_at, n.id LIMIT 1)"
        ),
        {"me": me, "tid": task_id, "ex": won.question},
    )
    return _wire(won), task_id


# ── The wire ────────────────────────────────────────────────────────────────


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def _wire(row: Any) -> dict[str, Any]:
    """One decision as the routes show it. ``payload`` stays server-side.
    ``task_title`` is there only when the read joined it (the decider's open
    list, for the My Day card)."""
    out = {
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
        # AP-8a: the date and the close of a person ask. NULL for the others.
        "due_at": _iso(getattr(row, "due_at", None)),
        "closed_at": _iso(getattr(row, "closed_at", None)),
        "closed_reason": getattr(row, "closed_reason", None),
    }
    title = getattr(row, "task_title", None)
    if title is not None:
        out["task_title"] = title
    return out


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
    """The caller's own decisions. With ``task_id``: those on a task the caller
    can open, newest first, answered or not. Without: the open ones. A decision
    is shown to its decider only. Other members read the answer as the comment
    on the task. No count of people."""
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
                    " AND lower(d.decider) = :me"
                    " ORDER BY d.created_at DESC LIMIT :lim"
                ),
                {"tid": tid, "me": actor(user).strip().lower(), "lim": LIST_LIMIT},
            )).fetchall()
            # ALN-2b: a member who can open the task learns THAT a decision
            # waits, on whom, and until when. Never the question, the options
            # or the answer. The task was loaded through the member's own
            # visibility above, so a member who cannot open it gets the same
            # 404 as for a task with no decision.
            waiting = (await db.execute(
                text(
                    "SELECT d.decider, d.expires_at FROM pm_decisions d"
                    " WHERE d.subject_kind = 'task' AND d.subject_id = CAST(:tid AS uuid)"
                    " AND lower(d.decider) <> :me AND d.answered_at IS NULL"
                    " AND d.closed_at IS NULL"
                    " AND d.expires_at > now() ORDER BY d.expires_at, d.id LIMIT :lim"
                ),
                {"tid": tid, "me": actor(user).strip().lower(), "lim": LIST_LIMIT},
            )).fetchall()
        else:
            rows = (await db.execute(
                text(
                    "SELECT d.*, (d.expires_at <= now()) AS is_expired,"
                    " (SELECT t.title FROM pm_tasks t WHERE t.id = d.subject_id) AS task_title"
                    " FROM pm_decisions d"
                    " WHERE lower(d.decider) = :me AND d.answered_at IS NULL"
                    " AND d.closed_at IS NULL"
                    " AND d.expires_at > now() AND d.subject_kind = 'task'"
                    " ORDER BY d.created_at DESC LIMIT :lim"
                ),
                {"me": actor(user).strip().lower(), "lim": LIST_LIMIT},
            )).fetchall()
        # §7.2: of the tasks listed for the decider, drop one they cannot open.
        wire = [_wire(r) for r in rows]
        if task_id is None:
            # The asker by the directory name that the panel shows, from the
            # one resolver (``people.core.names_of``). A system actor has none.
            from gateway.routes.people.core import names_of

            names = await names_of(db, [i["asked_by"] for i in wire if "@" in i["asked_by"]])
            for item in wire:
                item["asked_by_name"] = names.get(item["asked_by"].strip().lower())
            keep: list[dict[str, Any]] = []
            for item in wire:
                if await deliverable(db, [actor(user).strip().lower()], item["subject_id"]):
                    keep.append(item)
            wire = keep
    out: dict[str, Any] = {"decisions": wire}
    if task_id is not None:
        out["waiting_on"] = [
            {"decider": str(w.decider), "expires_at": _iso(w.expires_at)} for w in waiting
        ]
    return out


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
    # ALN-2b: the answer read the bell row, so My Day moves its version.
    from gateway.routes.projects.personal import bump_my_day

    await bump_my_day(user.organization_id, actor(user).strip().lower())
    return {"decision": out}


# ── The person ask (WS-AP AP-8a, ``waiting_on_me.md`` §7.1 and §7.2) ─────────


class ChoiceIn(BaseModel):
    """One choice of a person ask: ``{key, label}``, as an option is."""

    model_config = ConfigDict(extra="forbid")

    key: str
    label: str


class AskIn(BaseModel):
    """A person ask. Nobody who asks is named in the body: the session is the
    asker (R5)."""

    model_config = ConfigDict(extra="forbid")

    addressee: str = Field(min_length=3, max_length=320)
    question: str = Field(min_length=1, max_length=QUESTION_MAX)
    choices: list[ChoiceIn] | None = None
    due_at: datetime | None = None


def _refuse_an_agent(user: UserContext, what: str) -> None:
    """An ask is the act of a person (AP-Q4). An agent connection is refused,
    before any read."""
    if getattr(user, "agent", None) is not None:
        raise HTTPException(status_code=403, detail=what)


def _refusal(status: int, error: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"error": error, "message": message})


def ask_options(choices: list[dict[str, str]] | None) -> list[dict[str, str]]:
    """§7.1 item 4. No choices: Reply and Not me. Two or three choices: the
    choices and Not me. Raises a 422 for any other shape."""
    if choices is None:
        return [dict(_REPLY), dict(_NOT_ME)]
    if not (CHOICES_MIN <= len(choices) <= CHOICES_MAX):
        raise _refusal(422, "bad_choices",
                       f"An ask takes no choices, or {CHOICES_MIN} to {CHOICES_MAX}.")
    if {c.get("key") for c in choices} & {REPLY_KEY, NOT_ME_KEY}:
        raise _refusal(422, "bad_choices", "The keys reply and not_me are the service's own.")
    try:
        return clean_options([*choices, dict(_NOT_ME)])
    except DecisionRefused as refused:
        raise _refusal(422, refused.reason, refused.message) from refused


def ask_life(due_at: datetime | None, now: datetime) -> timedelta:
    """§7.1 item 5. The due date plus 7 days, or 14 days with no date. 422
    for a date that has passed, has no zone, or puts the end past MAX_LIFE."""
    if due_at is None:
        return ASK_NO_DUE_LIFE
    if due_at.tzinfo is None:
        raise _refusal(422, "bad_due", "due_at needs a time zone.")
    if due_at <= now:
        raise _refusal(422, "bad_due", "due_at has passed.")
    life = (due_at - now) + ASK_AFTER_DUE
    if life > MAX_LIFE:
        raise _refusal(422, "bad_due", "due_at is too far away.")
    return life


async def ask_person(
    db: Any, user: UserContext, task_id: str, body: AskIn,
) -> dict[str, Any]:
    """Open one person ask, on the caller's session. Does not commit.

    The guards of §7.2, in order. Each refusal writes no row and no bell:

    1. No person present, or an agent connection: 403, before any read.
    2. The addressee cannot open the task: 422 ``addressee_cannot_open``,
       with the next step (V4).
    3. The caller is the addressee: 422 ``self_ask``.
    4. An open ask for the same task, caller and addressee: 409
       ``ask_open``. A lock on the task row serializes two asks.
    5. The 31st ask of the caller in one day: 429 ``ask_limit``.

    The caller must open the task first, or the answer is 404.
    """
    _refuse_no_person()
    _refuse_an_agent(user, "A person asks a person.")
    me = actor(user).strip().lower()
    who = (body.addressee or "").strip().lower()
    try:
        tid = str(uuid.UUID(str(task_id)))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="Task not found") from exc
    options = ask_options(
        [c.model_dump() for c in body.choices] if body.choices is not None else None,
    )
    life = ask_life(body.due_at, datetime.now(UTC))

    # The caller opens the task, or learns nothing about it.
    await load_visible_task(db, await resolve_visibility(db, user), tid)
    # Guard 2 (V4): the addressee opens the task too. Else nothing is written.
    if not who or "@" not in who or not await deliverable(db, [who], tid):
        raise _refusal(
            422, "addressee_cannot_open",
            "This person cannot open the task. Share the project with them first.",
        )
    # Guard 3.
    if who == me:
        raise _refusal(422, "self_ask", "You cannot ask yourself.")
    # Guard 4. The lock on the task row makes a second ask for the task wait,
    # so it reads the first ask after the first commits.
    await db.execute(
        text("SELECT 1 FROM pm_tasks WHERE id = CAST(:tid AS uuid) FOR UPDATE"),
        {"tid": tid},
    )
    open_ask = (await db.execute(
        text(
            "SELECT 1 FROM pm_decisions d"
            " WHERE d.subject_kind = 'task' AND d.subject_id = CAST(:tid AS uuid)"
            " AND d.source = 'person' AND lower(d.asked_by) = :me AND d.decider = :who"
            " AND d.answered_at IS NULL AND d.closed_at IS NULL"
            " AND d.expires_at > now() LIMIT 1"
        ),
        {"tid": tid, "me": me, "who": who},
    )).fetchone()
    if open_ask is not None:
        raise _refusal(409, "ask_open", "You already wait on this person here. Nudge them?")
    # Guard 5. The row at the offset of the limit exists only when the caller
    # asked that many times in a day. Nothing is kept or shown (ALN-Q5, V6).
    at_limit = (await db.execute(
        text(
            "SELECT 1 FROM pm_decisions d"
            " WHERE d.source = 'person' AND lower(d.asked_by) = :me"
            " AND d.created_at > now() - interval '1 day'"
            " ORDER BY d.created_at OFFSET :skip LIMIT 1"
        ),
        {"me": me, "skip": ASK_DAY_LIMIT - 1},
    )).fetchone()
    if at_limit is not None:
        raise _refusal(429, "ask_limit", "You asked enough people today. Try again tomorrow.")

    try:
        return await ask_decision(
            db, task_id=tid, decider=who, question=body.question, options=options,
            asked_by=me, source="person", expires_in=life, due_at=body.due_at,
        )
    except DecisionRefused as refused:
        if refused.reason == "off":
            raise HTTPException(status_code=404, detail="Not Found") from refused
        if refused.reason == "decider_cannot_open":
            raise _refusal(
                422, "addressee_cannot_open",
                "This person cannot open the task. Share the project with them first.",
            ) from refused
        if refused.reason == "self_decision":
            raise _refusal(422, "self_ask", "You cannot ask yourself.") from refused
        raise _refusal(422, refused.reason, refused.message) from refused


async def withdraw_ask(db: Any, user: UserContext, ask_id: str) -> tuple[dict[str, Any], str]:
    """The asker withdraws an open person ask. Returns the ask and its
    addressee. Does not commit.

    404 for an ask that the caller did not make, for any reason, and for a
    decision that is not a person ask. 409 ``decision_answered``,
    ``ask_closed`` or ``decision_expired`` for the asker only.
    """
    _refuse_no_person()
    _refuse_an_agent(user, "A person withdraws an ask.")
    me = actor(user).strip().lower()
    try:
        aid = str(uuid.UUID(str(ask_id)))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="Ask not found") from exc
    row = (await db.execute(
        text(f"{_SELECT} WHERE d.id = CAST(:id AS uuid)"), {"id": aid},
    )).fetchone()
    if (row is None or row.source != "person" or row.subject_kind != "task"
            or str(row.asked_by).strip().lower() != me):
        raise HTTPException(status_code=404, detail="Ask not found")
    done = (await db.execute(
        text(
            "UPDATE pm_decisions SET closed_at = now(), closed_reason = 'withdrawn' "
            "WHERE id = CAST(:id AS uuid) AND source = 'person'"
            " AND lower(asked_by) = :me"
            " AND answered_at IS NULL AND closed_at IS NULL AND expires_at > now() "
            "RETURNING *, (expires_at <= now()) AS is_expired"
        ),
        {"id": aid, "me": me},
    )).fetchone()
    if done is None:
        again = (await db.execute(
            text(f"{_SELECT} WHERE d.id = CAST(:id AS uuid)"), {"id": aid},
        )).fetchone()
        if again is not None and again.answered_at is not None:
            raise HTTPException(status_code=409, detail="decision_answered")
        if again is not None and again.closed_at is not None:
            raise HTTPException(status_code=409, detail="ask_closed")
        if again is not None and again.is_expired:
            raise HTTPException(status_code=409, detail="decision_expired")
        raise HTTPException(status_code=404, detail="Ask not found")
    # The bell row of the addressee closes, as the answer closes it: ONE row,
    # picked by the question, which ``ask_decision`` gave ``notify`` whole.
    await db.execute(
        text(
            "UPDATE pm_notifications SET read_at = now() "
            "WHERE id = (SELECT n.id FROM pm_notifications n"
            " WHERE n.recipient = :who AND n.kind = 'decision' AND n.read_at IS NULL"
            " AND n.task_id = CAST(:tid AS uuid) AND n.excerpt IS NOT DISTINCT FROM :ex"
            " AND lower(n.actor) = :me"
            " ORDER BY n.created_at, n.id LIMIT 1)"
        ),
        {
            "who": str(done.decider), "tid": str(done.subject_id),
            "ex": done.question, "me": me,
        },
    )
    return _wire(done), str(done.decider)


@router.post("/tasks/{task_id}/asks", status_code=201, dependencies=_ASK_ENABLED)
async def ask(
    task_id: str, payload: AskIn, user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """A member asks a member about one task (AP-8a). The asker is the
    session. The bell of the addressee goes through ``notify``."""
    async with _tenant_session() as db:
        out = await ask_person(db, user, task_id, payload)
    from gateway.routes.projects.personal import bump_my_day

    await bump_my_day(user.organization_id, out["decider"])
    return {"decision": out}


@router.post("/asks/{ask_id}/withdraw", dependencies=_ASK_ENABLED)
async def withdraw(ask_id: str, user: UserContext = Depends(get_current_user)) -> dict[str, Any]:
    """The asker withdraws an open ask. 404 for anyone else."""
    async with _tenant_session() as db:
        out, addressee = await withdraw_ask(db, user, ask_id)
    from gateway.routes.projects.personal import bump_my_day

    await bump_my_day(user.organization_id, addressee)
    return {"decision": out}
