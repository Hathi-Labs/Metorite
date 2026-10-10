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

Fences (R7): ``tests/unit/test_pending_ask_store.py`` (R8: the SQL and the
row level security, as the NOBYPASSRLS app role) and
``tests/unit/test_pending_ask_flow.py`` (the flow).
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

_COLUMNS = (
    "request_id, thread_id, run_id, actor_email, agent_name, kind, question, "
    "payload, state, answer, asked_at, parked_at, answered_at, expires_at"
)


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
    """Write one ``open`` row. A second write of the same request is a no-op."""
    from acb_graph import tenant_session  # type: ignore[import-untyped, unused-ignore]
    from sqlalchemy import text

    with tenant_session(organization_id) as s:
        result = s.execute(
            text(
                "INSERT INTO chat_pending_ask (organization_id, request_id, "
                "  thread_id, run_id, actor_email, agent_name, kind, question, "
                "  payload, expires_at) "
                "VALUES (CAST(:org AS uuid), :rid, :tid, :run, :actor, :agent, "
                "  :kind, :question, CAST(:payload AS jsonb), "
                "  now() + make_interval(hours => :ttl)) "
                "ON CONFLICT (organization_id, request_id) DO NOTHING"
            ),
            {
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
            },
        )
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
    """
    from acb_graph import tenant_session  # type: ignore[import-untyped, unused-ignore]
    from sqlalchemy import text

    stamp = {
        "parked": "parked_at = now()",
        "answered": "answered_at = now(), answer = :answer",
        "closed": "answered_at = COALESCE(answered_at, now())",
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


def close_thread_asks(session: Any, thread_id: str) -> int:
    """Close every question of *thread_id* that still waits. Returns the count.

    Runs inside the CALLER's tenant transaction (the chat delete), so the
    chat and its questions go together.
    """
    from sqlalchemy import text

    result = session.execute(
        text(
            "UPDATE chat_pending_ask SET state = 'closed', "
            "  answered_at = COALESCE(answered_at, now()) "
            "WHERE thread_id = :tid AND state = ANY(:waiting)"
        ),
        {"tid": thread_id, "waiting": list(WAITING)},
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
    where = ["state = ANY(:waiting)", "expires_at > now()"]
    params: dict[str, Any] = {"waiting": list(WAITING)}
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


async def _record(thread_id: str, ask: CardAsk) -> str | None:
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
    try:
        await asyncio.to_thread(insert_ask, org, row)
    except Exception:
        _log.warning("pending_ask.insert_failed", request_id=ask.request_id[:12],
                     thread_id=thread_id[:12], exc_info=True)
        return None
    _log.info("pending_ask.recorded", request_id=ask.request_id[:12],
              thread_id=thread_id[:12], kind=ask.kind)
    return org


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
    _THREAD_OF[ask.request_id] = thread_id
    _INSERTS[ask.request_id] = loop.create_task(
        _record(thread_id, ask), name=f"cc-ask-{ask.request_id[:12]}",
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
        await asyncio.to_thread(
            move_ask, org, request_id, to=to, from_states=("open",), answer=answer,
        )
    except Exception:
        _log.warning("pending_ask.settle_failed", request_id=request_id[:12], exc_info=True)


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
    if _answered(fut):
        # The answer came in while the row moved. It reached the live run, so
        # the row records it, and the run goes on.
        with contextlib.suppress(Exception):
            await asyncio.to_thread(
                move_ask, org, request_id, to="answered",
                from_states=("parked",), answer=_answer_of(fut),
            )
        return False
    # No await from the check above to the pop: from here on, an answer finds
    # no Future and takes the late path.
    _remember_parked(request_id)
    with contextlib.suppress(Exception):
        from orchestrator.executor import _pending_user_input

        _pending_user_input.pop(request_id, None)
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
