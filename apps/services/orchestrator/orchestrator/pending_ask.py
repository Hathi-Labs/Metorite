"""A durable "needs input": the question a chat run asks outlives the process.

WS-51 S2, ``project-docs/specs/chat_run_continuity.md`` §4 S2. Behind
``CHAT_DURABLE_ASKS`` (default OFF). OFF means no change.

A run that waits on a card parks on an in-process Future
(``executor._pending_user_input``). That Future is still the fast path. This
module adds the durable copy, one ``chat_pending_ask`` row per card, and the
rules that move a row through its states:

    open ──answer──▶ answered            the run took the answer
      │  ──no answer, card closed──▶ closed   (a Stop, a timeout)
      └──park window──▶ parked ──late answer──▶ answered

**The one write point.** Every card reaches the member through
``executor._push_sse_to_stream``. That function calls :func:`note_event`
before it pushes, so the seven parking sites need no change. ``note_event``
writes a row ONLY for a card whose Future is still waiting in THIS process,
so a replay, a non-blocking card and an answered card write nothing.

**The run side settles the row.** ``executor.wait_user_future`` calls
:func:`settle` when the wait ends, and :func:`park` when the park window
passes. ``settle`` waits for the row's insert first, so a fast answer never
leaves an ``open`` row behind it.

**Park and end.** After ``CHAT_ASK_PARK_SECONDS`` with no answer, the row
goes to ``parked``, a ``RUN_FINISHED`` with ``parked: true`` goes into the
stream, and the run's detached task is cancelled. The drain's ``on_complete``
then saves the reply so far: that is the checkpoint. The card's answer
reaches ``POST /agent/respond-input`` later, and the gateway answers 409
``run_restarted`` with the question and the answer as one message, the shape
of #797 (``gateway.chat_recovery.card_answer_from_ask``).

**Tenancy (R5).** Every read and write opens ``acb_graph.tenant_session(org)``
under FORCE row level security. The org of a write is the run's own,
server-side org: the tenant bound for the run (``acb_common.db``), else the
run's record in this process (``run_liveness``). Never an event field.

**My Day** (WS-58 MD-3f). The insert of a row and its settle each bump the
My Day version of the member it asks (``_bump_my_day``), so the feed card
``agent_asks`` changes at once. The bump does nothing with ``MY_DAY_CACHE``
off. It never raises and it is bounded in time, so it never breaks an ask.

**A late approve** (HRD-22, ``chat_run_continuity.md`` S2 item 8). When a
plain confirmation card parks, :func:`park` records its act
(``acb_skills.late_approve.record_offer``), while the card's Future still
waits. An answer during the park drops it again (``drop_offer``). The gateway
turns it into a grant of one use only on a late APPROVE. Dark behind
``CHAT_LATE_APPROVE_ORGS``.

**The stored call** (RESUME-1a, ``chat_run_continuity.md`` §4.12.4 item 1).
The card gate (``ask_tools.request_confirmation``) binds the call of a plain
card of a listed write to its request id with :func:`bind_call`, before the
card goes out. The bind is out of band, keyed by the request id, because on
path A :func:`note_event` runs in the executor's drain and cannot read the
tool's context. :func:`note_event` takes the entry, and :func:`insert_ask`
writes it in the column ``stored_call`` (migration 448). ``_COLUMNS`` leaves
the column out, so no browser read returns it. :func:`read_stored_call` is the
one reader, and :func:`claim_stored_call` the one claim (RESUME-2a).
:func:`claim_declined_call` is the claim of a late decline (RESUME-4a-2).
:func:`restore_stored_call` gives a claim back when its run never started. A move to
``answered`` or ``closed`` sets it to NULL, and a park keeps it. Dark behind
``CHAT_RESUME_ORGS`` (``acb_skills.stored_call``): off, nothing binds, and
each row is as before.

**Retention** (task 3435, ``chat_run_continuity.md`` §4.12.4 item 1). No
reader takes a row past ``expires_at``, and no move changes one. So
:func:`prune` is the one end of an old row. It runs as a hook of the daily
prune of the job worker (:func:`prune_hook`, ``gateway.jobs.worker
.prune_hooks``), once for each org, in the org's own session. An expired row
loses its stored call at the next prune: the arguments of a call never
outlive the card. A row past ``expires_at`` plus :data:`PENDING_ASK_KEEP_DAYS`
goes, with its payload and its answer. A row that still waits, and has not
expired, never changes. The member purge deletes the rows that ask a leaver
(``routes/admin/members.py``, ``chat_pending_asks``). These two are the only
deletes of the table. It runs only with ``JOB_QUEUE`` on.

Fences (R7): ``tests/unit/test_pending_ask_store.py`` (R8: the SQL and the
row level security, as the NOBYPASSRLS app role) and
``tests/unit/test_pending_ask_flow.py`` (the flow). The stored call:
``tests/unit/test_resume_stored_call.py`` and
``tests/unit/test_resume_stored_call_r8.py`` (R8). The retention:
``tests/unit/test_pending_ask_prune.py`` and
``tests/unit/test_pending_ask_prune_r8.py`` (R8).
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import re
from dataclasses import dataclass
from typing import Any

from acb_common import get_logger, get_settings

_log = get_logger("orchestrator.pending_ask")

#: The card events, and the kind each one stores. The gateway's restart
#: recovery reads the same map (``gateway.chat_recovery``).
KIND_BY_EVENT: dict[str, str] = {
    "user_input_requested": "ask_user",
    "elicitation_requested": "questions",
    "confirmation_requested": "confirmation",
    "generative_ui": "generative_ui",
}
EVENT_BY_KIND: dict[str, str] = {v: k for k, v in KIND_BY_EVENT.items()}

#: The states a member must still answer.
WAITING = ("open", "parked")

#: Every request id the parking sites mint is ``uuid4().hex``.
_REQUEST_ID = re.compile(r"^[0-9a-f]{32}$")

#: A card's longest stored question, and its longest stored payload. A larger
#: payload keeps the question and drops the rest, so the row still says what
#: was asked.
_QUESTION_CHARS = 2000
_PAYLOAD_BYTES = 64 * 1024
_ANSWER_CHARS = 20_000


def durable_asks_enabled() -> bool:
    """The one reader of ``CHAT_DURABLE_ASKS``. A settings failure reads OFF."""
    try:
        return bool(getattr(get_settings(), "chat_durable_asks", False))
    except Exception:
        return False


def park_after_seconds() -> float:
    """How long a run waits on a card before it parks."""
    try:
        return max(1.0, float(getattr(get_settings(), "chat_ask_park_seconds", 600)))
    except Exception:
        return 600.0


def _ttl_hours() -> int:
    try:
        return max(1, int(getattr(get_settings(), "chat_ask_ttl_hours", 168)))
    except Exception:
        return 168


def question_of(value: Any) -> str | None:
    """The words a card asks, from its event value. The one reader of them."""
    if not isinstance(value, dict):
        return None
    for key in ("question", "title", "prompt", "message"):
        text = value.get(key)
        if isinstance(text, str) and text.strip():
            return text.strip()
    qs = value.get("questions")
    if isinstance(qs, list):
        parts = [
            str(q.get("question") or q.get("header") or "").strip()
            for q in qs if isinstance(q, dict)
        ]
        joined = " / ".join(p for p in parts if p)
        return joined or None
    return None


@dataclass(frozen=True)
class CardAsk:
    request_id: str
    kind: str
    question: str
    payload: dict[str, Any]


def card_of(event: Any) -> CardAsk | None:
    """The card an event asks, or ``None``. Only a card with a request id.

    A card with no request id is a non-blocking card. Its answer arrives as
    a chat message, so nothing waits on it and no row is needed.
    """
    if not isinstance(event, dict) or event.get("type") != "CUSTOM":
        return None
    kind = KIND_BY_EVENT.get(str(event.get("name") or ""))
    value = event.get("value")
    if kind is None or not isinstance(value, dict):
        return None
    rid = str(value.get("request_id") or "")
    if not _REQUEST_ID.match(rid):
        return None
    payload = dict(value)
    payload.pop("_stream_id", None)
    question = (question_of(payload) or "")[:_QUESTION_CHARS]
    try:
        if len(json.dumps(payload, default=str)) > _PAYLOAD_BYTES:
            payload = {"request_id": rid, "question": question, "truncated": True}
    except (TypeError, ValueError):
        payload = {"request_id": rid, "question": question}
    return CardAsk(request_id=rid, kind=kind, question=question, payload=payload)


# ---------------------------------------------------------------------------
# The SQL. Sync: callers run these in a thread. R8 fence:
# tests/unit/test_pending_ask_store.py.
# ---------------------------------------------------------------------------

#: The columns of each read that a browser route reaches (``read_ask``,
#: ``waiting_asks``, the RETURNING of ``move_ask``). ⚠️ ``stored_call`` is NOT
#: one of them (RESUME-1a): it holds the arguments of a call, and only
#: :func:`read_stored_call` reads it. Fence: ``test_resume_stored_call.py``.
_COLUMNS = (
    "request_id, thread_id, run_id, actor_email, agent_name, kind, question, "
    "payload, state, answer, asked_at, parked_at, answered_at, expires_at"
)

#: The insert of a row. A row with a stored call names the column, and a row
#: with none sends the statement of before, byte for byte (RESUME-1a).
_INSERT_SQL = (
    "INSERT INTO chat_pending_ask (organization_id, request_id, "
    "  thread_id, run_id, actor_email, agent_name, kind, question, "
    "  payload, expires_at) "
    "VALUES (CAST(:org AS uuid), :rid, :tid, :run, :actor, :agent, "
    "  :kind, :question, CAST(:payload AS jsonb), "
    "  now() + make_interval(hours => :ttl)) "
    "ON CONFLICT (organization_id, request_id) DO NOTHING"
)
_INSERT_STORED_SQL = (
    "INSERT INTO chat_pending_ask (organization_id, request_id, "
    "  thread_id, run_id, actor_email, agent_name, kind, question, "
    "  payload, stored_call, expires_at) "
    "VALUES (CAST(:org AS uuid), :rid, :tid, :run, :actor, :agent, "
    "  :kind, :question, CAST(:payload AS jsonb), CAST(:stored AS jsonb), "
    "  now() + make_interval(hours => :ttl)) "
    "ON CONFLICT (organization_id, request_id) DO NOTHING"
)


def _stored_text(stored: Any) -> str | None:
    """*stored* as the JSON text of the column, or None when it is no stored
    call: not a dict, a value that JSON cannot hold (a lone surrogate, a NUL), or more than
    :data:`_PAYLOAD_BYTES` bytes of UTF-8 (RESUME-1a)."""
    if not isinstance(stored, dict) or not stored:
        return None
    try:
        text = json.dumps(stored, ensure_ascii=False, allow_nan=False,
                          separators=(",", ":"), sort_keys=True)
        too_big = len(text.encode("utf-8")) > _PAYLOAD_BYTES
    except (TypeError, ValueError, RecursionError):
        return None
    if too_big:
        return None
    if "\\u0000" in text:
        # json.dumps escapes a NUL (in a value or a key) as \u0000, and
        # Postgres jsonb refuses it. Store no call: fail closed.
        return None
    return text


def _row(r: Any) -> dict[str, Any]:
    m = dict(r._mapping)
    for k in ("asked_at", "parked_at", "answered_at", "expires_at"):
        if m.get(k) is not None:
            m[k] = m[k].isoformat()
    if isinstance(m.get("payload"), str):
        with contextlib.suppress(ValueError):
            m["payload"] = json.loads(m["payload"])
    return m


def insert_ask(organization_id: str, row: dict[str, Any]) -> bool:
    """Write one ``open`` row. A second write of the same request is a no-op.

    RESUME-1a: ``row["stored_call"]``, when :func:`_stored_text` reads it, goes
    into the column ``stored_call``. A row with none writes NULL with the
    statement of before."""
    from acb_graph import tenant_session  # type: ignore[import-untyped, unused-ignore]
    from sqlalchemy import text

    params: dict[str, Any] = {
        "org": organization_id,
        "rid": row["request_id"],
        "tid": row["thread_id"],
        "run": row.get("run_id"),
        "actor": (row.get("actor_email") or "").strip().lower(),
        "agent": row.get("agent_name"),
        "kind": row["kind"],
        "question": row.get("question") or "",
        "payload": json.dumps(row.get("payload") or {}, default=str),
        "ttl": _ttl_hours(),
    }
    stored = _stored_text(row.get("stored_call"))
    sql = _INSERT_SQL
    if stored is not None:
        sql = _INSERT_STORED_SQL
        params["stored"] = stored
    with tenant_session(organization_id) as s:
        result = s.execute(text(sql), params)
        return bool(result.rowcount)


def move_ask(
    organization_id: str,
    request_id: str,
    *,
    to: str,
    from_states: tuple[str, ...],
    answer: str | None = None,
) -> dict[str, Any] | None:
    """Move one row to *to*, only from *from_states*. Returns the row, or None.

    One statement, so two answers that race move the row once. The second
    gets ``None`` and resends nothing. An expired row never moves.

    RESUME-1a: a move to ``answered`` or ``closed`` sets ``stored_call`` to
    NULL, because no answer may run the call after that. A park keeps it.
    """
    from acb_graph import tenant_session  # type: ignore[import-untyped, unused-ignore]
    from sqlalchemy import text

    stamp = {
        "parked": "parked_at = now()",
        "answered": "answered_at = now(), answer = :answer, stored_call = NULL",
        "closed": "answered_at = COALESCE(answered_at, now()), stored_call = NULL",
    }.get(to)
    if stamp is None:
        raise ValueError(f"unknown state {to!r}")
    with tenant_session(organization_id) as s:
        r = s.execute(
            text(
                f"UPDATE chat_pending_ask SET state = :to, {stamp} "
                "WHERE organization_id = CAST(:org AS uuid) AND request_id = :rid "
                "  AND state = ANY(:from) AND expires_at > now() "
                f"RETURNING {_COLUMNS}"
            ),
            {
                "to": to, "org": organization_id, "rid": request_id,
                "from": list(from_states),
                "answer": (answer or "")[:_ANSWER_CHARS],
            },
        ).first()
        return _row(r) if r is not None else None


def close_thread_asks(
    session: Any, thread_id: str, *, actor_email: str | None = None,
) -> int:
    """Close every question of *thread_id* that still waits. Returns the count.

    Runs inside the CALLER's tenant transaction (the chat delete, or an edit
    of the last message), so the rows change together with the caller's own
    write. With *actor_email*, only the questions that member was asked
    close: an edit never closes the card of another member in a room. A
    closed row keeps no stored call (RESUME-1a).
    """
    from sqlalchemy import text

    who = (actor_email or "").strip().lower()
    result = session.execute(
        text(
            "UPDATE chat_pending_ask SET state = 'closed', "
            "  answered_at = COALESCE(answered_at, now()), stored_call = NULL "
            "WHERE thread_id = :tid AND state = ANY(:waiting) "
            "  AND (:who = '' OR actor_email = :who)"
        ),
        {"tid": thread_id, "waiting": list(WAITING), "who": who},
    )
    return int(result.rowcount or 0)


def read_ask(organization_id: str, request_id: str) -> dict[str, Any] | None:
    """One row of the caller's tenant that still waits, or None."""
    from acb_graph import tenant_session  # type: ignore[import-untyped, unused-ignore]
    from sqlalchemy import text

    with tenant_session(organization_id) as s:
        r = s.execute(
            text(
                f"SELECT {_COLUMNS} FROM chat_pending_ask "
                "WHERE request_id = :rid AND state = ANY(:waiting) "
                "  AND expires_at > now()"
            ),
            {"rid": request_id, "waiting": list(WAITING)},
        ).first()
        return _row(r) if r is not None else None


#: The second witness of the late approve (HRD-22b item 2). A confirmation row
#: of the member, in ANY thread, with the same visible card, answered APPROVE
#: after the row was asked. An APPROVE with edits (``APPROVE {...}``, the edit
#: card of EAC-1) is an APPROVE too: the act ran with the edited values. The org predicate stands beside RLS, as in
#: ``move_ask``.
_TWIN_APPROVED_SQL = (
    "SELECT 1 FROM chat_pending_ask "
    "WHERE organization_id = CAST(:org AS uuid) AND actor_email = :actor "
    "  AND kind = 'confirmation' AND state = 'answered' "
    "  AND (upper(btrim(coalesce(answer, ''))) = 'APPROVE' "
    "       OR upper(coalesce(answer, '')) LIKE 'APPROVE %') "
    "  AND answered_at > CAST(:asked AS timestamptz) AND request_id <> :rid "
    "  AND coalesce(payload->>'title', '') = :title "
    "  AND coalesce(payload->>'detail', '') = :detail "
    "  AND coalesce(payload->>'context', '') = :context "
    "LIMIT 1"
)


def twin_approved_since(organization_id: str, member: str, row: dict[str, Any]) -> bool:
    """True when a twin of *row* (the same member, the same title, detail and
    context) moved to ``answered`` with ``APPROVE`` after *row* was asked, in
    any thread. It is also True when *row* cannot be compared (no card text
    or no ``asked_at``), the fail-closed side: the grant then waits for the
    normal card. Sync: callers run it in a thread. R8 fence:
    ``tests/unit/test_late_approve_twin_r8.py``."""
    from acb_graph import tenant_session  # type: ignore[import-untyped, unused-ignore]
    from sqlalchemy import text

    payload = row.get("payload")
    asked = row.get("asked_at")
    if not isinstance(payload, dict) or payload.get("truncated") or not asked:
        return True
    title = str(payload.get("title") or "")
    if not title:
        return True
    params = {
        "org": organization_id, "actor": (member or "").strip().lower(),
        "asked": str(asked), "rid": str(row.get("request_id") or ""),
        "title": title, "detail": str(payload.get("detail") or ""),
        "context": str(payload.get("context") or ""),
    }
    with tenant_session(organization_id) as s:
        return s.execute(text(_TWIN_APPROVED_SQL), params).first() is not None


def waiting_asks(
    organization_id: str,
    *,
    thread_id: str | None = None,
    actor_email: str | None = None,
) -> list[dict[str, Any]]:
    """The rows that still wait, for one thread or for one member. Oldest first."""
    from acb_graph import tenant_session  # type: ignore[import-untyped, unused-ignore]
    from sqlalchemy import text

    if not thread_id and not actor_email:
        return []
    # The org predicate stands beside RLS, as in `move_ask`: defence in depth
    # for a session that RLS does not hold (WS-58 MD-3f fix round 1, R5).
    where = ["organization_id = CAST(:org AS uuid)", "state = ANY(:waiting)",
             "expires_at > now()"]
    params: dict[str, Any] = {"org": organization_id, "waiting": list(WAITING)}
    if thread_id:
        where.append("thread_id = :tid")
        params["tid"] = thread_id
    if actor_email:
        where.append("actor_email = :actor")
        params["actor"] = actor_email.strip().lower()
    with tenant_session(organization_id) as s:
        rows = s.execute(
            text(
                f"SELECT {_COLUMNS} FROM chat_pending_ask "
                f"WHERE {' AND '.join(where)} ORDER BY asked_at LIMIT 200"
            ),
            params,
        ).fetchall()
    return [_row(r) for r in rows]


def read_stored_call(organization_id: str, request_id: str) -> dict[str, Any] | None:
    """The stored call of one row that still waits, or None (RESUME-1a).

    THE one reader of ``stored_call``. No browser read may call it. RESUME-2a:
    only ``gateway.routes.agent._resume_from_row`` calls it, on a late
    Approve, before :func:`claim_stored_call`. It reads a row
    of the caller's tenant only, under RLS and with the org predicate beside
    it, as :func:`waiting_asks` does (R5). An answered, closed or expired row
    gives None, and so does a row with no stored call.
    """
    from acb_graph import tenant_session  # type: ignore[import-untyped, unused-ignore]
    from sqlalchemy import text

    with tenant_session(organization_id) as s:
        r = s.execute(
            text(
                "SELECT stored_call FROM chat_pending_ask "
                "WHERE organization_id = CAST(:org AS uuid) AND request_id = :rid "
                "  AND state = ANY(:waiting) AND expires_at > now() "
                "  AND stored_call IS NOT NULL"
            ),
            {"org": organization_id, "rid": request_id, "waiting": list(WAITING)},
        ).first()
    value = r[0] if r is not None else None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return None
    return value if isinstance(value, dict) else None


#: RESUME-2a: the claim of a stored call, in ONE statement. The CTE locks the
#: row and keeps its stored call. The UPDATE moves the row to ``answered``,
#: sets the column to NULL, and gives back the value of the CTE. Postgres 16
#: has no ``RETURNING OLD``, and ``move_ask`` leaves the column out of its
#: RETURNING, so the CTE is the one way to read the old value in the move.
#: The org predicate stands beside RLS (R5). A row of another member, another
#: thread, a row that expired, and a row with no stored call never move.
_CLAIM_OLD = (
    "WITH old AS ("
    "  SELECT organization_id, request_id, stored_call FROM chat_pending_ask "
    "  WHERE organization_id = CAST(:org AS uuid) AND request_id = :rid "
    "    AND state = ANY(:waiting) AND expires_at > now() "
    "    AND actor_email = :member AND thread_id = :tid "
    "    AND stored_call IS NOT NULL "
    "  FOR UPDATE"
    ") "
)
_CLAIM_SQL = _CLAIM_OLD + (
    "UPDATE chat_pending_ask AS p SET state = 'answered', answered_at = now(), "
    "  answer = :answer, stored_call = NULL "
    "FROM old "
    "WHERE p.organization_id = old.organization_id "
    "  AND p.request_id = old.request_id "
    "RETURNING old.stored_call"
)

#: RESUME-4a-2 (task 3478, D-RES-9): the claim of a late decline, in ONE
#: statement. The same CTE as :data:`_CLAIM_SQL` locks the row and keeps its
#: stored call, under the same predicates. The UPDATE moves the row to
#: ``closed`` (a decline is no answer, as ``move_ask(to="closed")``), sets
#: the column to NULL, and gives back the old stored call. No tool runs it:
#: the continuation only tells the model that nothing was done.
_CLAIM_DECLINED_SQL = _CLAIM_OLD + (
    "UPDATE chat_pending_ask AS p SET state = 'closed', "
    "  answered_at = COALESCE(p.answered_at, now()), stored_call = NULL "
    "FROM old "
    "WHERE p.organization_id = old.organization_id "
    "  AND p.request_id = old.request_id "
    "RETURNING old.stored_call"
)

#: The SQLSTATE codes that mean "no claim": a deadlock, and a lock that did
#: not come in time (``lock_timeout``).
_NO_CLAIM_CODES = frozenset({"40P01", "55P03"})

#: How long the claim waits for the lock of the row.
_CLAIM_LOCK_TIMEOUT = "5s"


def _sqlstate(exc: BaseException) -> str:
    """The SQLSTATE of a driver error, for psycopg 3 and psycopg2."""
    orig = getattr(exc, "orig", None) or exc
    return str(getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None) or "")


def claim_stored_call(
    organization_id: str,
    request_id: str,
    *,
    member: str,
    thread_id: str,
    answer: str,
) -> dict[str, Any] | None:
    """Claim the stored call of one waiting row, ONCE (RESUME-2a).

    ``gateway.routes.agent._resume_from_row`` is the one caller. One
    statement (:data:`_CLAIM_SQL`) moves the row from ``parked`` or ``open``
    to ``answered`` and gives back the stored call that it held. So two
    Approves that race claim the row once, and the second gets None.

    *member* and *thread_id* come from the session and the request's own
    thread, which the route checked against the row. They must equal the
    row's ``actor_email`` and ``thread_id``. The org is the session's, under
    RLS and with the org predicate beside it (R5). A deadlock and a lock
    timeout give None: no claim. It adds no state: ``answered`` is one of the
    four states of ``chat_pending_ask_state_known``.
    """
    return _claim(organization_id, request_id, member=member, thread_id=thread_id,
                  sql=_CLAIM_SQL, extra={"answer": (answer or "")[:_ANSWER_CHARS]})


def claim_declined_call(
    organization_id: str,
    request_id: str,
    *,
    member: str,
    thread_id: str,
) -> dict[str, Any] | None:
    """Claim the stored call of one waiting row for a late DECLINE, ONCE.

    RESUME-4a-2 (task 3478, D-RES-9). ``gateway.routes.agent._resume_from_row``
    is the one caller. One statement (:data:`_CLAIM_DECLINED_SQL`) moves the
    row from ``parked`` or ``open`` to ``closed`` and gives back the stored
    call that it held. Nothing runs the call: the continuation only tells
    the model that nothing was done. The predicates are those of
    :func:`claim_stored_call`, and so are the "no claim" codes. It adds no
    state: ``closed`` is one of the four states."""
    return _claim(organization_id, request_id, member=member, thread_id=thread_id,
                  sql=_CLAIM_DECLINED_SQL, extra={})


def _claim(
    organization_id: str, request_id: str, *, member: str, thread_id: str,
    sql: str, extra: dict[str, Any],
) -> dict[str, Any] | None:
    """Run ONE claim statement (:data:`_CLAIM_SQL` or
    :data:`_CLAIM_DECLINED_SQL`), and give back the old stored call."""
    from acb_graph import tenant_session  # type: ignore[import-untyped, unused-ignore]
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    who = (member or "").strip().lower()
    if not who or not thread_id or not isinstance(request_id, str) \
            or not _REQUEST_ID.match(request_id):
        return None
    try:
        with tenant_session(organization_id) as s:
            s.execute(text(f"SET LOCAL lock_timeout = '{_CLAIM_LOCK_TIMEOUT}'"))
            r = s.execute(
                text(sql),
                {
                    "org": organization_id, "rid": request_id,
                    "waiting": list(WAITING), "member": who, "tid": thread_id,
                    **extra,
                },
            ).first()
    except DBAPIError as exc:
        if _sqlstate(exc) in _NO_CLAIM_CODES:
            _log.info("pending_ask.claim_not_taken", request_id=request_id[:12],
                      code=_sqlstate(exc))
            return None
        raise
    value = r[0] if r is not None else None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return None
    return value if isinstance(value, dict) else None


#: RESUME-2a review, P2: the give-back of a claim whose run never started.
#: ONE statement moves the row from ``answered`` back to ``parked`` with the
#: stored call that the claim took. The WHERE is the claim's own: the org
#: beside RLS, the request id, the member, the thread, a row that has not
#: expired, and a column that the claim set to NULL. No run waits on the card
#: (the thread was at rest), so ``parked`` is its true state. A declined
#: claim (:func:`claim_declined_call`) is never given back: its ``closed``
#: row never matches (review of RESUME-4a-2, P2-1).
_RESTORE_SQL = (
    "UPDATE chat_pending_ask SET state = 'parked', answer = NULL, "
    "  answered_at = NULL, parked_at = COALESCE(parked_at, now()), "
    "  stored_call = CAST(:stored AS jsonb) "
    "WHERE organization_id = CAST(:org AS uuid) AND request_id = :rid "
    "  AND state = 'answered' AND stored_call IS NULL AND expires_at > now() "
    "  AND actor_email = :member AND thread_id = :tid "
    "RETURNING request_id"
)


def restore_stored_call(
    organization_id: str,
    request_id: str,
    *,
    member: str,
    thread_id: str,
    stored: dict[str, Any],
) -> bool:
    """Give back the claim of :func:`claim_stored_call` when its run never
    started (RESUME-2a review, P2). True when the row waits again.

    ``gateway.routes.agent._resume_from_row`` is the one caller, and only
    when the run route refused BEFORE ``stream_relay.start_detached`` began.
    So the card can be answered again, and the call runs at most once.
    *member*, *thread_id* and *stored* are the claim's own. The org is the
    session's, under RLS with the org predicate beside it (R5). A stored
    call that :func:`_stored_text` refuses gives back nothing."""
    from acb_graph import tenant_session  # type: ignore[import-untyped, unused-ignore]
    from sqlalchemy import text

    who = (member or "").strip().lower()
    body = _stored_text(stored)
    if not who or not thread_id or body is None or not isinstance(request_id, str) \
            or not _REQUEST_ID.match(request_id):
        return False
    with tenant_session(organization_id) as s:
        r = s.execute(
            text(_RESTORE_SQL),
            {"org": organization_id, "rid": request_id, "member": who,
             "tid": thread_id, "stored": body},
        ).first()
    return r is not None


# ---------------------------------------------------------------------------
# Retention (task 3435). The SQL runs on the session of the daily prune job of
# the gateway's job worker. R8 fence: tests/unit/test_pending_ask_prune_r8.py.
# ---------------------------------------------------------------------------

#: The days that a row stays after ``expires_at``. In that time the row keeps
#: its question, its payload and its answer, but no stored call. Then
#: :func:`prune` deletes it. No reader takes an expired row, so the days only
#: let a person who looks into a fault read the card.
PENDING_ASK_KEEP_DAYS = 7
#: The most rows that one prune of one org deletes, and the most rows that it
#: clears. One run never holds a long transaction. The daily run keeps up only while an
#: org makes fewer than this many expired cards a day. Above that, the table
#: grows until task 3560 raises the bound or runs the prune more often.
PENDING_ASK_PRUNE_BATCH = 1000

#: The tenant that the session binds. NULL when no tenant is bound, and then a
#: statement matches no row. The text of ``gateway.routes.tasks.settings``.
_BOUND_ORG = "CAST(NULLIF(current_setting('app.tenant_id', true), '') AS uuid)"

#: The prune of the bound org, in ONE statement and one snapshot.
#:
#: * ``doomed``: the rows past ``expires_at`` plus :data:`PENDING_ASK_KEEP_DAYS`,
#:   in each state. They go, with the payload and the answer.
#: * ``stale``: each other row past ``expires_at`` that still holds a stored
#:   call. The column goes to NULL. ``NOT EXISTS`` keeps the two sets apart,
#:   so no row changes twice in the statement. ``doomed`` is MATERIALIZED, so
#:   its LIMIT gives one set to both readers of it.
#:
#: A row that has not expired is in neither set, in each state. So a card that
#: still waits, and a call that a claim can still take, never change. Each
#: statement names the bound org beside RLS (R5). The DELETE and the UPDATE
#: test the age again, so a row that a concurrent write changed is read again
#: after its lock. The CTEs read the key only, never the stored call.
_PRUNE_SQL = (
    "WITH doomed AS MATERIALIZED ("
    "  SELECT request_id "
    "  FROM chat_pending_ask "
    f"  WHERE organization_id = {_BOUND_ORG} "
    "    AND expires_at <= now() - make_interval(days => :keep) "
    "  ORDER BY expires_at LIMIT :batch"
    "), stale AS ("
    "  SELECT request_id "
    "  FROM chat_pending_ask AS s "
    f"  WHERE s.organization_id = {_BOUND_ORG} "
    "    AND s.expires_at <= now() AND s.stored_call IS NOT NULL "
    "    AND NOT EXISTS (SELECT 1 FROM doomed AS d WHERE d.request_id = s.request_id) "
    "  ORDER BY s.expires_at LIMIT :batch"
    "), deleted AS ("
    "  DELETE FROM chat_pending_ask AS p USING doomed "
    f"  WHERE p.organization_id = {_BOUND_ORG} "
    "    AND p.request_id = doomed.request_id "
    "    AND p.expires_at <= now() - make_interval(days => :keep) "
    "  RETURNING 1"
    "), cleared AS ("
    "  UPDATE chat_pending_ask AS p SET stored_call = NULL FROM stale "
    f"  WHERE p.organization_id = {_BOUND_ORG} "
    "    AND p.request_id = stale.request_id "
    "    AND p.expires_at <= now() AND p.stored_call IS NOT NULL "
    "  RETURNING 1"
    ") "
    "SELECT (SELECT count(*) FROM deleted) AS deleted, "
    "  (SELECT count(*) FROM cleared) AS cleared"
)


async def prune(db: Any) -> tuple[int, int]:
    """Prune the rows of the bound org: ``(deleted, cleared)`` (task 3435).

    *db* is an async session that the caller opened with ``tenant_session``.
    The org is the tenant of that session, never an argument. No bound
    tenant changes nothing. One statement (:data:`_PRUNE_SQL`)."""
    from sqlalchemy import text

    row = (await db.execute(text(_PRUNE_SQL), {
        "keep": PENDING_ASK_KEEP_DAYS, "batch": PENDING_ASK_PRUNE_BATCH,
    })).first()
    if row is None:
        return 0, 0
    return int(row[0] or 0), int(row[1] or 0)


async def prune_hook(db: Any, today: Any) -> None:
    """The ``gateway.jobs.worker.prune_hooks`` hook (task 3435). It never
    raises: a hook that raises fails the whole prune of the org. A savepoint
    keeps the transaction of the prune usable after a failure. The log names
    the class only, because a DB message can echo a value of a row."""
    try:
        async with db.begin_nested():
            deleted, cleared = await prune(db)
    except Exception as exc:  # the class only: a DB message can echo a value
        _log.warning("pending_ask.prune_failed", error=type(exc).__name__)
        return
    if deleted or cleared:
        _log.info("pending_ask.pruned", deleted=deleted, cleared=cleared)


def register_prune() -> bool:
    """Add :func:`prune_hook` to the prune of the job worker, once. True when
    the hook is in the list. A process with no gateway has no job worker, so
    nothing is added there."""
    try:
        from gateway.jobs import worker
    except ImportError:
        return False
    if prune_hook not in worker.prune_hooks:
        worker.prune_hooks.append(prune_hook)
    return True


# ---------------------------------------------------------------------------
# The run side: in this process, beside the Future.
# ---------------------------------------------------------------------------

#: The insert of each card's row, by request id. The task returns the org it
#: wrote under, or None when it wrote nothing.
_INSERTS: dict[str, asyncio.Task[str | None]] = {}
#: The thread of each row in ``_INSERTS``, so a run's end can close them.
_THREAD_OF: dict[str, str] = {}
#: The requests this process parked, oldest first. Their Future is
#: cancelled, and the card must stay open for a late answer. The parking
#: site reads it AFTER the wait has ended (``ask_tools._block_on``), so an
#: entry outlives the wait, and the oldest go once there are too many.
_PARKED: dict[str, None] = {}
_PARKED_KEEP = 1000
#: RESUME-1a: the stored call of each plain card, by request id, oldest
#: first. The card gate binds it before the card goes out (:func:`bind_call`),
#: and :func:`note_event` takes it into the row. The card gate's wait drops
#: what is left when it ends (:func:`unbind_call`), so nothing stays with
#: ``CHAT_DURABLE_ASKS`` off. The oldest go once there are too many.
_STORED: dict[str, dict[str, Any]] = {}
_STORED_KEEP = _PARKED_KEEP


def bind_call(request_id: str, stored: Any) -> bool:
    """Bind the stored call of the card *request_id*, before the card goes
    out (RESUME-1a). True when bound.

    ``acb_skills.ask_tools.request_confirmation`` is the one caller. It binds
    out of band, by the request id, because on path A :func:`note_event` runs
    in the executor's drain, where the tool's context is not. Nothing binds
    for a request id of another shape, a value that is not a dict, a value
    that JSON cannot hold (a lone surrogate, a NUL), or more than :data:`_PAYLOAD_BYTES` bytes of
    UTF-8. The map keeps the value as JSON gives it back. Never raises.
    """
    if not isinstance(request_id, str) or not _REQUEST_ID.match(request_id):
        return False
    text = _stored_text(stored)
    if text is None:
        return False
    _STORED.pop(request_id, None)
    _STORED[request_id] = json.loads(text)
    while len(_STORED) > _STORED_KEEP:
        _STORED.pop(next(iter(_STORED)))
    return True


def unbind_call(request_id: str) -> None:
    """Drop the stored call of *request_id*, when the card's wait ends
    (RESUME-1a). The card gate calls it on every way out. Never raises."""
    _STORED.pop(str(request_id or ""), None)


def was_parked(request_id: str) -> bool:
    """True when this process parked *request_id*. The card stays open."""
    return request_id in _PARKED


def _remember_parked(request_id: str) -> None:
    _PARKED[request_id] = None
    while len(_PARKED) > _PARKED_KEEP:
        _PARKED.pop(next(iter(_PARKED)))


async def _run_facts(thread_id: str) -> tuple[str | None, str | None, str | None, str]:
    """``(org, run_id, agent, actor)`` of the run on *thread_id*, server side."""
    org: str | None = None
    with contextlib.suppress(Exception):
        from acb_common.db import current_tenant

        org = current_tenant()
    run_id = agent = None
    with contextlib.suppress(Exception):
        from orchestrator import run_liveness, stream_relay

        r = await stream_relay._get_client()
        raw = await r.hget(run_liveness.instance_runs_key(run_liveness.INSTANCE_ID), thread_id)
        rec = json.loads(raw) if raw else {}
        if isinstance(rec, dict):
            org = org or (str(rec.get("org")) if rec.get("org") else None)
            run_id = str(rec.get("runId") or "") or None
            agent = str(rec.get("agent") or "") or None
    actor = ""
    with contextlib.suppress(Exception):
        from orchestrator.stream_relay import get_run_actor

        actor = (await get_run_actor(thread_id) or "").strip().lower()
    return org, run_id, agent, actor


async def _record(thread_id: str, ask: CardAsk,
                  stored: dict[str, Any] | None = None) -> str | None:
    org, run_id, agent, actor = await _run_facts(thread_id)
    if not org:
        # No tenant, so no row: the fail-closed answer (a run with no org has
        # no tenant to be listed under, as in ``register_live_run``).
        return None
    row = {
        "request_id": ask.request_id, "thread_id": thread_id, "run_id": run_id,
        "actor_email": actor, "agent_name": agent, "kind": ask.kind,
        "question": ask.question, "payload": ask.payload,
    }
    if stored is not None:
        # RESUME-1a: only a row with a stored call names the key, so a row
        # with none is the row of before.
        row["stored_call"] = stored
    failed: str | None = None
    try:
        await asyncio.to_thread(insert_ask, org, row)
    except Exception as exc:
        if stored is None:
            _log.warning("pending_ask.insert_failed", request_id=ask.request_id[:12],
                         thread_id=thread_id[:12], exc_info=True)
            return None
        failed = type(exc).__name__
    if failed is not None:
        # The stored call must never cost the card its row: log the error
        # TYPE only (no argument text), then write the row of before. The
        # retry runs OUTSIDE the except block, so its exception has no
        # __context__ that holds the first one's bound parameters.
        _log.warning("pending_ask.stored_insert_failed",
                     request_id=ask.request_id[:12],
                     thread_id=thread_id[:12], error=failed)
        row.pop("stored_call", None)
        try:
            await asyncio.to_thread(insert_ask, org, row)
        except Exception:
            _log.warning("pending_ask.insert_failed", request_id=ask.request_id[:12],
                         thread_id=thread_id[:12], exc_info=True)
            return None
    _log.info("pending_ask.recorded", request_id=ask.request_id[:12],
              thread_id=thread_id[:12], kind=ask.kind)
    await _bump_my_day(org, actor)
    return org


async def _bump_my_day(org: str | None, actor: str | None) -> None:
    """Move the My Day version of the member a question asks, after its row
    changed (WS-58 MD-3f, ``my_day_feed.md`` §5.9 item 14). So the card
    ``agent_asks`` comes and goes at once, not at the 600-second bound.

    The org and the member are the run's own, server-side, never an event
    field. ``cache.bump_member_safe`` is the one seam: it does nothing with
    ``MY_DAY_CACHE`` off, it waits 0.5 seconds at most, and it never raises,
    so a bump never breaks an ask. The import is local, as the other gateway
    imports here are. A failed import only logs."""
    if not org or not actor:
        return
    try:
        from gateway.routes.shell import cache as feed_cache
    except Exception as exc:  # no gateway in this process: no feed to bump
        _log.warning("pending_ask.my_day_bump_failed", error=type(exc).__name__)
        return
    await feed_cache.bump_member_safe(org, actor)


def note_event(thread_id: str, event: Any) -> None:
    """Start the row of a card that waits in THIS process. Sync and cheap.

    Called by ``executor._push_sse_to_stream`` for every event it pushes.
    The Future check and the task registration happen in one step with no
    await between them, so :func:`settle` either finds the insert to wait
    for, or this call sees the Future already done and writes nothing.
    """
    if not thread_id or not durable_asks_enabled():
        return
    ask = card_of(event)
    if ask is None or ask.request_id in _INSERTS:
        return
    try:
        from orchestrator.executor import _pending_user_input

        fut = _pending_user_input.get(ask.request_id)
        if fut is None or fut.done():
            return
        loop = asyncio.get_running_loop()
    except Exception:
        return
    # RESUME-1a: the stored call that the card gate bound for this request
    # id, out of band. Only a confirmation card takes one.
    stored = _STORED.pop(ask.request_id, None) if ask.kind == "confirmation" else None
    _THREAD_OF[ask.request_id] = thread_id
    _INSERTS[ask.request_id] = loop.create_task(
        _record(thread_id, ask, stored), name=f"cc-ask-{ask.request_id[:12]}",
    )


async def _insert_org(request_id: str, *, pop: bool) -> str | None:
    if pop:
        _THREAD_OF.pop(request_id, None)
    task = _INSERTS.pop(request_id, None) if pop else _INSERTS.get(request_id)
    if task is None:
        return None
    try:
        return await asyncio.shield(task)
    except BaseException:
        return None


async def settle(request_id: str, *, answer: str | None) -> None:
    """Close the row when the wait ends. ``answer=None`` means no answer came.

    A parked row stays parked: its answer comes later, to a new run.
    """
    org = await _insert_org(request_id, pop=True)
    if request_id in _PARKED:
        return
    if not org:
        return
    to = "answered" if answer is not None else "closed"
    try:
        row = await asyncio.to_thread(
            move_ask, org, request_id, to=to, from_states=("open",), answer=answer,
        )
    except Exception as exc:
        # Class and id only (task 3538): the answer is a statement parameter.
        _log.warning("pending_ask.settle_failed", request_id=request_id[:12],
                     error=type(exc).__name__)
        return
    if row:
        # The question no longer waits, so it leaves My Day at once.
        await _bump_my_day(org, str(row.get("actor_email") or ""))


async def end_of_run(thread_id: str) -> None:
    """Close the rows a run on *thread_id* left open, when the run ends.

    ``stream_relay.run_detached`` calls this in its ``finally``. A card that
    no wait ever settled (a parking site that gave up its Future, a bridge
    that two paths raced) must not read as "needs you" once the run is over.
    A parked row stays parked. A process that dies runs no ``finally``, so
    its open rows stay open, and that is the point: the restart case.
    """
    for request_id in [r for r, t in list(_THREAD_OF.items()) if t == thread_id]:
        await settle(request_id, answer=None)


def _answered(fut: Any) -> bool:
    return fut is not None and fut.done()


def _declined(fut: Any) -> bool:
    """True when the Future's result is the member's "Not now" (owner
    request, 2026-10-18). A decline gives no answer, so its row closes."""
    try:
        result = fut.result()
    except BaseException:
        return False
    return isinstance(result, dict) and result.get("declined") is True


def _answer_of(fut: Any) -> str:
    try:
        result = fut.result()
    except BaseException:
        return ""
    return str((result or {}).get("answer", "") or "") if isinstance(result, dict) else ""


async def park(request_id: str, thread_id: str, fut: Any = None) -> bool:
    """Park the run that waits on *request_id*, and end it. True when parked.

    False changes nothing, and the caller keeps waiting as before: no row, no
    detached run to end, a row that is no longer open, or an answer that
    arrived while this ran.

    Review of #813, both P1s:

    * An answer can resolve *fut* during any await here. After each await,
      a done *fut* means the member answered: the row moves to ``answered``
      and the run is NOT ended, so the answer is never lost.
    * The Future leaves ``executor._pending_user_input`` BEFORE the run is
      cancelled, in the same step as the last check, with no await between
      them. A later answer then finds no Future, reads the parked row, and
      starts a new run. Two parking sites pop their Future only on a timeout
      (ask_questions path A, the B1 bridge), so an orphan Future used to take
      a late answer, answer 200, and start nothing.
    """
    from orchestrator import stream_relay

    org = await _insert_org(request_id, pop=False)
    if not org or _answered(fut):
        return False
    run = stream_relay.get_detached_task(thread_id)
    if run is None or run.done():
        return False
    try:
        row = await asyncio.to_thread(
            move_ask, org, request_id, to="parked", from_states=("open",),
        )
    except Exception:
        _log.warning("pending_ask.park_failed", request_id=request_id[:12], exc_info=True)
        return False
    if row is None:
        return False
    # HRD-22 (chat_run_continuity.md S2 item 8): record what a plain card
    # binds, while its Future still waits. An answer during this write
    # reaches the live run, and the check below drops the offer again. It
    # reads the card of this wait (``ask_tools.waiting_card``) and lives no
    # longer than the row. With CHAT_LATE_APPROVE_ORGS off it writes
    # nothing, and it never raises.
    with contextlib.suppress(Exception):
        from acb_skills.late_approve import record_offer

        await record_offer(org, thread_id, request_id, row, _ttl_hours() * 3600)
    if _answered(fut):
        # The answer came in while the row moved. It reached the live run, so
        # the row records it, and the run goes on. A "Not now" is no answer:
        # its row closes, as `settle` closes it (review of #1152).
        with contextlib.suppress(Exception):
            if _declined(fut):
                await asyncio.to_thread(
                    move_ask, org, request_id, to="closed", from_states=("parked",),
                )
            else:
                await asyncio.to_thread(
                    move_ask, org, request_id, to="answered",
                    from_states=("parked",), answer=_answer_of(fut),
                )
        # HRD-22: the row left `parked`, so its offer goes too.
        with contextlib.suppress(Exception):
            from acb_skills.late_approve import drop_offer

            await drop_offer(org, str(row.get("actor_email") or ""), thread_id, request_id)
        return False
    # No await from the check above to the pop: from here on, an answer finds
    # no Future and takes the late path.
    _remember_parked(request_id)
    with contextlib.suppress(Exception):
        from orchestrator.executor import _pending_user_input

        _pending_user_input.pop(request_id, None)
    with contextlib.suppress(Exception):
        # Task 3130: a card that a chat message closed earlier in this run
        # gets its "Not done" line before the end. The park runs in the
        # card's own wait, so it reads the run's record.
        from acb_skills.ask_tools import closed_card_text_events, take_closed_card_line

        for event in closed_card_text_events(take_closed_card_line()):
            await stream_relay.push_event(thread_id, event)
    with contextlib.suppress(Exception):
        await stream_relay.push_event(thread_id, {
            "type": "RUN_FINISHED", "threadId": thread_id, "parked": True,
        })
    _log.info("pending_ask.parked", request_id=request_id[:12], thread_id=thread_id[:12])
    # The drain's finally saves the reply so far (on_complete), marks the run
    # inactive and clears its live-run entry. That save is the checkpoint.
    run.cancel()
    return True


def _reset_for_tests() -> None:
    _INSERTS.clear()
    _THREAD_OF.clear()
    _PARKED.clear()
    _STORED.clear()


register_prune()
