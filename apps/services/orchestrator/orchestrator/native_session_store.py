"""The native MAF session store (WS-43t2).

Spec: ``project-docs/specs/maf_coding_engine.md`` §15.9. Fence: WS43-F20,
``tests/unit/test_native_session_persistence.py``. Behind
``MAF_NATIVE_SESSIONS`` (default OFF). The executor imports this module only
when the flag is on.

This is the ONE module that reads or writes ``maf_agent_session``. A row holds
the MAF ``AgentSession`` of one native agent in one chat thread, so the next
turn sees the tool calls and the results of the turns before it (H-215).

**One session idiom.** Every read and write opens
``acb_graph.tenant_session(org)`` (§15.9.3). The organization is resolved by
the executor from the run binding (``_current_run_org()``) on the event loop,
BEFORE the ``run_in_executor`` hop, and passed in. ``tenant_session`` is sync
and takes no ambient tenant, so a worker thread could not resolve it.

**The rules.**

1. **The key** is (organization, thread, agent). The session of agent X never
   loads for agent Y.
2. **Dedup.** When a session loads, the run input is the current turn only.
3. **Staleness** (§15.9.4). Both digests hash the ``chat_message`` rows that
   the SERVER kept, never the transcript that the browser sends. At the load,
   the store hashes the rows before the new user turn, through the last user
   row. The rows after that row are the answer of the run that saved the
   session. The fold seals that row, and no route edits it, so it is the
   boundary and not hashed. At the save, the store hashes the rows that came
   before this run's user turn (read at the load), then that turn. So a
   regenerate, an agent switch, an edit or a delete changes the digest.
4. **The browser view check.** A regenerate in ``AgentChat.tsx`` drops the
   last turn and its prompt from the BROWSER only. The server keeps both
   rows, so the server digest cannot see it. So the store also compares the
   last user turns of the browser history with the server rows. A user turn
   holds the same bytes in both copies, because the browser saves the string
   it sends. The check can only turn a hit into a ``digest_drop``.
5. **The room fingerprint** (§15.9.5): a SHA-256 over the room's member set,
   each member's clearance fingerprint, and the instance key of a personal
   agent. The store records the fingerprint that it read at the LOAD, which
   is the room that the run ran in.
6. **Never stored.** The context, the memory and the persona travel by the
   per-run context provider of WS-43t1. The store keeps only the message
   history of the run, and no other session state.
7. **The bounds** (§15.9.6). Before each save, MAF's
   ``TokenBudgetComposedStrategy`` runs over ``ToolResultCompactionStrategy``
   and then ``SlidingWindowStrategy``. ``SummarizationStrategy`` is excluded,
   because it calls a model. A ``session_json`` above
   ``maf_session_max_bytes`` is refused.
8. **One outcome line per load**: ``hit``, ``no_row``, ``digest_drop`` or
   ``fingerprint_drop``. A load that fails falls back to the text history and
   never fails the run.
9. **No pickle.** ``AgentSession.from_dict`` restores through MAF's state type
   registry only.
"""
from __future__ import annotations

import asyncio
import functools
import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from acb_common import get_logger, get_settings  # type: ignore[import-untyped, unused-ignore]
from agent_framework import (
    AgentSession,
    CharacterEstimatorTokenizer,
    InMemoryHistoryProvider,
    Message,
    SlidingWindowStrategy,
    TokenBudgetComposedStrategy,
    ToolResultCompactionStrategy,
    included_messages,
)

_log = get_logger("orchestrator.native_session_store")

#: The four outcomes of a load (§15.9.4). Each load logs exactly one.
HIT = "hit"
NO_ROW = "no_row"
DIGEST_DROP = "digest_drop"
FINGERPRINT_DROP = "fingerprint_drop"
OUTCOMES = (HIT, NO_ROW, DIGEST_DROP, FINGERPRINT_DROP)

#: The ``source_id`` of the per-run history provider. The stored session
#: holds ``state[HISTORY_SOURCE_ID]["messages"]`` and nothing else.
HISTORY_SOURCE_ID = "metorite-native-session"

#: The byte backstop when the setting cannot be read (§15.9.6, 2 MiB).
DEFAULT_MAX_BYTES = 2 * 1024 * 1024

#: How many of the newest user turns the browser view check compares.
_BROWSER_CHECK_TURNS = 3

#: The group count of the sliding window. The token budget is the bound that
#: binds. The window holds the group count near the text path's 400-message
#: bound (``executor._HISTORY_MAX_MESSAGES``), about 200 turn pairs.
_SLIDING_WINDOW_GROUPS = 200

#: The SQL of one load. Every statement runs in ONE bound transaction, so the
#: rows, the room and the stored session agree.
_CHAT_EXISTS_SQL = "SELECT user_id FROM chat_session WHERE id = :tid"
_PARTICIPANTS_SQL = (
    "SELECT subject FROM chat_session_participant WHERE session_id = :tid"
)
_ROWS_SQL = (
    "SELECT role, content FROM chat_message WHERE session_id = :tid "
    "AND role IN ('user', 'assistant') ORDER BY timestamp_ms ASC, id ASC"
)
_STORED_SQL = (
    "SELECT session_json, transcript_digest, session_fingerprint "
    "FROM maf_agent_session WHERE organization_id = CAST(:org AS uuid) "
    "AND thread_id = :tid AND agent_name = :agent"
)
#: The save. ``WHERE EXISTS`` reads ``chat_session`` under the bound tenant,
#: so a chat that is gone, or that is not this tenant's, inserts nothing. The
#: foreign key catches a delete that lands between the check and the insert.
_UPSERT_SQL = """
    INSERT INTO maf_agent_session
        (organization_id, thread_id, agent_name, session_json,
         transcript_digest, session_fingerprint, updated_at)
    SELECT CAST(:org AS uuid), CAST(:tid AS text), CAST(:agent AS text),
           CAST(:body AS jsonb), CAST(:digest AS text), CAST(:fp AS text), now()
     WHERE EXISTS (SELECT 1 FROM chat_session WHERE id = CAST(:tid AS text))
    ON CONFLICT (organization_id, thread_id, agent_name) DO UPDATE SET
        session_json        = EXCLUDED.session_json,
        transcript_digest   = EXCLUDED.transcript_digest,
        session_fingerprint = EXCLUDED.session_fingerprint,
        updated_at          = now()
    RETURNING 1
"""

Row = tuple[str, str]


class SessionHistoryProvider(InMemoryHistoryProvider):
    """MAF's in-memory history, with an append that keeps every message.

    ``InMemoryHistoryProvider.save_messages`` drops an incoming message that
    has no id when a stored message has the same role and content. A member
    who answers "yes" on two turns would lose the second answer. A run here
    sends only its own new turn, so a plain append is exact.
    """

    def __init__(self) -> None:
        super().__init__(HISTORY_SOURCE_ID)

    async def save_messages(
        self,
        session_id: str | None,
        messages: Sequence[Message],
        *,
        state: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        """Append *messages* to the session state, with no dedup."""
        if state is None or not messages:
            return
        state["messages"] = [*state.get("messages", []), *messages]


@dataclass
class StoredRow:
    """One ``maf_agent_session`` row, as the load reads it."""

    session_json: Mapping[str, Any]
    transcript_digest: str
    session_fingerprint: str


@dataclass
class LoadState:
    """What one bound load transaction read."""

    chat_exists: bool
    session_user: str = ""
    participants: list[str] = field(default_factory=list)
    rows: list[Row] = field(default_factory=list)
    stored: StoredRow | None = None


@dataclass
class SessionTurn:
    """One native run's session: what the load found, and what the save needs."""

    organization_id: str
    thread_id: str
    agent_name: str
    outcome: str
    session: AgentSession
    history: SessionHistoryProvider
    fingerprint: str
    save_digest: str
    usable: bool = True

    @property
    def loaded(self) -> bool:
        """True when the stored session loaded, so the input is the current turn only."""
        return self.outcome == HIT and self.usable

    def abandon(self, reason: str) -> None:
        """Run this turn with no session: no history provider and no save."""
        self.usable = False
        _log.info(
            "native_session.abandoned",
            agent=self.agent_name, thread_id=self.thread_id[:40], reason=reason,
        )


# ── The digest and the fingerprint ──────────────────────────────────────────


def _canon(role: Any, content: Any) -> Row:
    return (str(role or ""), str(content or "").strip())


def server_rows(raw: Sequence[Sequence[Any]]) -> list[Row]:
    """The rows a digest reads: user and assistant rows that hold text.

    The same rows that the route's history loader reads. A minted agent row
    holds no text until its first checkpoint, so it drops out here.
    """
    rows = [_canon(r[0], r[1]) for r in raw]
    return [r for r in rows if r[0] in ("user", "assistant") and r[1]]


def rows_before_turn(rows: Sequence[Row], current_message: str) -> list[Row]:
    """The rows before the new user turn.

    The browser saves the new turn while the run starts, so the row may or
    may not be there. When the last row is that turn, it drops out.
    """
    out = list(rows)
    if out and out[-1] == ("user", current_message.strip()):
        out.pop()
    return out


def covered_rows(rows: Sequence[Row]) -> list[Row]:
    """The rows a stored session covers: through the last user row."""
    for index in range(len(rows) - 1, -1, -1):
        if rows[index][0] == "user":
            return list(rows[: index + 1])
    return []


def transcript_digest(rows: Sequence[Row]) -> str:
    """SHA-256 over the role and content of each row, in order."""
    digest = hashlib.sha256()
    for role, content in rows:
        digest.update(json.dumps([role, content], ensure_ascii=False).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def room_members(participants: Sequence[str], session_user: str) -> list[str]:
    """The room's member set, as ``gateway.rooms.resolve_room_access`` names it."""
    from gateway.rooms import _expand_members  # type: ignore[import-untyped, unused-ignore]

    class _Subject:
        __slots__ = ("subject",)

        def __init__(self, subject: str) -> None:
            self.subject = subject

    return list(_expand_members([_Subject(p) for p in participants], session_user))


def session_fingerprint(
    *, members: Sequence[str], agent_name: str, thread_id: str, instance: str,
) -> str:
    """SHA-256 over the member set, each member's clearance and the instance key.

    The clearance fingerprint is the one the memory cache keys on
    (``acb_memory.Clearance.fingerprint``). A room of one is not shared, and a
    room of two or more is, as ``RoomAccess.is_shared`` decides.
    """
    from acb_memory import resolve_clearance  # type: ignore[import-untyped, unused-ignore]

    unique = sorted(set(members))
    shared = len(unique) > 1
    lines = ["v1", f"instance={instance}"]
    for member in unique:
        clearance = resolve_clearance(
            actor=member, agent_name=agent_name, thread_id=thread_id, shared=shared,
        )
        lines.append(f"member={member}|clearance={clearance.fingerprint}")
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def browser_agrees(browser_history: Any, rows: Sequence[Row]) -> bool:
    """True unless the browser history dropped a user turn the session covers.

    *browser_history* is ``None`` when the caller sent no browser history (an
    API caller). Then there is nothing to compare. A regenerate drops the last
    turn and its prompt from the browser, so the browser's newest user turns
    differ from the server's.
    """
    if browser_history is None:
        return True
    server_users = [content for role, content in rows if role == "user"]
    if not server_users:
        return True
    browser_users = [
        str(m.get("content") or "").strip()
        for m in browser_history
        if isinstance(m, Mapping) and m.get("role") == "user"
        and str(m.get("content") or "").strip()
    ]
    if not browser_users:
        return False
    count = min(_BROWSER_CHECK_TURNS, len(browser_users), len(server_users))
    return browser_users[-count:] == server_users[-count:]


# ── The database (sync, in a worker thread, always bound) ───────────────────


def read_state(organization_id: str, thread_id: str, agent_name: str) -> LoadState:
    """Read the chat rows, the room and the stored session in ONE bound transaction.

    *organization_id* is required. ``tenant_session`` raises with none.
    """
    from acb_graph import tenant_session  # type: ignore[import-untyped, unused-ignore]
    from sqlalchemy import text

    with tenant_session(organization_id) as s:
        chat = s.execute(text(_CHAT_EXISTS_SQL), {"tid": thread_id}).first()
        if chat is None:
            return LoadState(chat_exists=False)
        participants = [
            str(r[0]) for r in s.execute(text(_PARTICIPANTS_SQL), {"tid": thread_id})
        ]
        rows = server_rows(
            [(r[0], r[1]) for r in s.execute(text(_ROWS_SQL), {"tid": thread_id})]
        )
        found = s.execute(
            text(_STORED_SQL),
            {"org": organization_id, "tid": thread_id, "agent": agent_name},
        ).first()
    stored = None
    if found is not None:
        body = found[0]
        if isinstance(body, str):
            body = json.loads(body)
        stored = StoredRow(dict(body), str(found[1]), str(found[2]))
    return LoadState(
        chat_exists=True, session_user=str(chat[0] or ""),
        participants=participants, rows=rows, stored=stored,
    )


def write_row(
    organization_id: str,
    thread_id: str,
    agent_name: str,
    body: str,
    digest: str,
    fingerprint: str,
) -> bool:
    """Upsert one session row. False when the chat is gone (no row written)."""
    from acb_graph import tenant_session  # type: ignore[import-untyped, unused-ignore]
    from sqlalchemy import text

    with tenant_session(organization_id) as s:
        written = s.execute(
            text(_UPSERT_SQL),
            {
                "org": organization_id, "tid": thread_id, "agent": agent_name,
                "body": body, "digest": digest, "fp": fingerprint,
            },
        ).first()
    return written is not None


# ── The compaction ──────────────────────────────────────────────────────────


async def compact_history(messages: Sequence[Message], token_budget: int) -> list[Message]:
    """Hold *messages* inside *token_budget* with the compaction of §15.9.6.

    ``TokenBudgetComposedStrategy`` over ``ToolResultCompactionStrategy`` and
    then ``SlidingWindowStrategy``. No ``SummarizationStrategy``: it calls a
    model, and no run pays for that call. The newest tool call group stays
    whole, so a confirm turn keeps the proposal it confirms.
    """
    working = list(messages)
    if not working:
        return working
    strategy = TokenBudgetComposedStrategy(
        token_budget=max(1, int(token_budget)),
        tokenizer=CharacterEstimatorTokenizer(),
        strategies=[
            ToolResultCompactionStrategy(keep_last_tool_call_groups=1),
            SlidingWindowStrategy(keep_last_groups=_SLIDING_WINDOW_GROUPS),
        ],
    )
    await strategy(working)
    return list(included_messages(working))


def _history(session: AgentSession) -> list[Message]:
    bucket = session.state.get(HISTORY_SOURCE_ID)
    messages = bucket.get("messages") if isinstance(bucket, Mapping) else None
    return list(messages) if isinstance(messages, list) else []


def _restore(stored: StoredRow) -> AgentSession:
    """``AgentSession.from_dict`` through MAF's registry, then a shape check."""
    session = AgentSession.from_dict(dict(stored.session_json))
    messages = session.state.get(HISTORY_SOURCE_ID, {}).get("messages")
    if not isinstance(messages, list) or not all(isinstance(m, Message) for m in messages):
        raise ValueError("the stored session holds no message history")
    return session


# ── The load ────────────────────────────────────────────────────────────────


def _log_outcome(
    outcome: str, *, agent_name: str, thread_id: str, reason: str = "", **extra: Any,
) -> None:
    """The ONE outcome line of a load (§15.9.4)."""
    _log.info(
        "native_session.load",
        outcome=outcome, agent=agent_name, thread_id=thread_id[:40],
        reason=reason or None, **extra,
    )


def _decide(
    state: LoadState, *, covered: Sequence[Row], fingerprint: str, browser_history: Any,
) -> tuple[str, str]:
    """(outcome, reason) of one load, before the restore."""
    stored = state.stored
    if stored is None:
        return NO_ROW, "absent"
    if stored.session_fingerprint != fingerprint:
        return FINGERPRINT_DROP, "room"
    if stored.transcript_digest != transcript_digest(covered):
        return DIGEST_DROP, "transcript"
    if not browser_agrees(browser_history, covered):
        return DIGEST_DROP, "browser_dropped_turn"
    return HIT, ""


async def begin_turn(
    *,
    organization_id: str,
    thread_id: str,
    agent_name: str,
    instance: str,
    current_message: str,
    browser_history: Any,
    token_budget: int,
) -> SessionTurn | None:
    """Load the stored session of this run. Logs ONE outcome line.

    *organization_id* is the run binding that the executor resolved on the
    event loop. Returns ``None`` when the run must use the text history with
    no session at all (the load failed, or the thread has no chat row).
    Otherwise a :class:`SessionTurn`: on ``hit`` it holds the stored session,
    fitted to *token_budget*, and on any other outcome a fresh one.
    """
    loop = asyncio.get_running_loop()
    try:
        state = await loop.run_in_executor(
            None, functools.partial(read_state, organization_id, thread_id, agent_name),
        )
    except Exception as exc:
        _log_outcome(NO_ROW, agent_name=agent_name, thread_id=thread_id,
                     reason="load_failed", error=str(exc)[:200])
        return None
    if not state.chat_exists:
        _log_outcome(NO_ROW, agent_name=agent_name, thread_id=thread_id, reason="no_chat")
        return None

    prior = rows_before_turn(state.rows, current_message)
    covered = covered_rows(prior)
    try:
        fingerprint = session_fingerprint(
            members=room_members(state.participants, state.session_user),
            agent_name=agent_name, thread_id=thread_id, instance=instance,
        )
    except Exception as exc:
        _log_outcome(NO_ROW, agent_name=agent_name, thread_id=thread_id,
                     reason="load_failed", error=str(exc)[:200])
        return None
    outcome, reason = _decide(
        state, covered=covered, fingerprint=fingerprint, browser_history=browser_history,
    )
    session = AgentSession()
    extra: dict[str, Any] = {}
    if outcome == HIT and state.stored is not None:
        try:
            session = _restore(state.stored)
            messages = await compact_history(_history(session), token_budget)
            session.state[HISTORY_SOURCE_ID] = {"messages": messages}
        except Exception as exc:
            outcome, reason = NO_ROW, "restore_failed"
            extra["error"] = str(exc)[:200]
            session = AgentSession()
    _log_outcome(outcome, agent_name=agent_name, thread_id=thread_id, reason=reason, **extra)
    return SessionTurn(
        organization_id=organization_id, thread_id=thread_id, agent_name=agent_name,
        outcome=outcome, session=session, history=SessionHistoryProvider(),
        fingerprint=fingerprint,
        save_digest=transcript_digest([*prior, ("user", current_message.strip())]),
    )


# ── The save ────────────────────────────────────────────────────────────────


def _max_bytes() -> int:
    try:
        return int(getattr(get_settings(), "maf_session_max_bytes", DEFAULT_MAX_BYTES))
    except Exception:
        return DEFAULT_MAX_BYTES


async def _session_body(turn: SessionTurn, token_budget: int) -> tuple[str, int]:
    """The compacted session as JSON, and its message count.

    Only the message history is kept. Every other state key of the run (a
    provider's own state, for example) is left out, so nothing but the turns
    is stored.
    """
    messages = await compact_history(_history(turn.session), token_budget)
    stored = AgentSession(session_id=turn.session.session_id)
    stored.state[HISTORY_SOURCE_ID] = {"messages": messages}
    return json.dumps(stored.to_dict(), ensure_ascii=False), len(messages)


def _is_foreign_key_error(exc: BaseException) -> bool:
    from sqlalchemy.exc import IntegrityError

    return isinstance(exc, IntegrityError) and "foreign key" in str(exc).lower()


async def finish_turn(turn: SessionTurn, *, token_budget: int) -> str:
    """Compact, bound and save the session of a run that succeeded.

    Returns ``saved``, ``too_large``, ``chat_gone``, ``failed`` or
    ``skipped``. Never raises: a save that fails leaves the run as it was.
    """
    if not turn.usable:
        return "skipped"
    log = {"agent": turn.agent_name, "thread_id": turn.thread_id[:40]}
    try:
        body, count = await _session_body(turn, token_budget)
    except Exception as exc:
        _log.warning("native_session.save_failed", **log, error=str(exc)[:200])
        return "failed"
    size = len(body.encode("utf-8"))
    limit = _max_bytes()
    if size > limit:
        _log.warning("native_session.save_refused", **log, bytes=size, limit=limit)
        return "too_large"
    loop = asyncio.get_running_loop()
    try:
        written = await loop.run_in_executor(None, functools.partial(
            write_row, turn.organization_id, turn.thread_id, turn.agent_name,
            body, turn.save_digest, turn.fingerprint,
        ))
    except Exception as exc:
        if _is_foreign_key_error(exc):
            written = False
        else:
            _log.warning("native_session.save_failed", **log, error=str(exc)[:200])
            return "failed"
    if not written:
        _log.info("native_session.chat_gone", **log)
        return "chat_gone"
    _log.info("native_session.saved", **log, bytes=size, messages=count)
    return "saved"
