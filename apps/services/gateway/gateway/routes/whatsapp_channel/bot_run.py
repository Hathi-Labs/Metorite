"""Chat on WhatsApp — a linked member's message runs the assistant (WS-47 WAC-3).

Spec: ``project-docs/specs/whatsapp_assistant_channel.md`` §5.4 ("The bot
message record, as WAC-3 builds it"), §5.5, §5.6 and §5.9.

The path of one text from a linked phone:

1. **Before the 200** (:func:`record_inbound`, called by ``inbound.py``). The
   org and the member come from the phone's CURRENT link, never from the
   message (D-WAC-3). The org must be on ``WHATSAPP_ASSISTANT_ORGS``. A member
   who is not active in that org gets a ``refused`` row and nothing else. For
   an active member the text becomes the member's turn in the WhatsApp thread
   (``chat_session.channel = 'whatsapp'``), and then a ``received`` row goes
   into ``whatsapp_bot_messages`` with ``ON CONFLICT (wamid) DO NOTHING``.
   Only the insert that wrote the row returns a :class:`RunRequest`.
2. **After the 200** (:func:`start`). The webhook's background task starts
   the run as a task of its own and returns at once. So a slow run can never
   hold the response or the connection.
3. **The run** (:func:`run_message`). It takes the THREAD's lock first, so
   one thread runs one message at a time. Then it works the thread's waiting
   rows oldest first, so a second message runs right after the first, with
   the first answer in its history. For each row: the checks (the switch and
   the allowlist, the link is still current, the member is active, the member
   may chat, ``resolve_identity`` answers the link's org, the thread is not a
   shared room, no web run holds it), the claim (``received`` to
   ``running``), then ``run_agent`` in this process with the link's org and
   email. The reply goes into the thread, then to WhatsApp in chunks of at
   most 4096 characters.
4. **The sweep** (:func:`sweep_once`, every minute). It binds each org of the
   allowlist in turn and takes the oldest waiting row of each thread. A row
   that stayed ``received`` or ``running`` for 5 minutes runs again, at most 3
   times. A row that used its 3 tries gets the general failure text once. A
   row older than 24 hours becomes ``expired`` and gets no reply.

**At most one reply per message, and at least one unless the channel fails.**
Just before the first part of a reply goes out, the row becomes ``sending``,
in the database. Nothing runs a ``sending`` row again, and nothing sends it
the general text: the sweep closes it as ``replied`` (``send_unconfirmed``)
after 5 minutes. A crash in the middle of a long reply can lose its tail, and
it never sends a part twice. A failure BEFORE ``sending`` gives the row back
as ``received`` with its try counted, so the sweep runs it again, and the last
try sends the general text once. A first part that WhatsApp refuses gives the
row back too. The next try sends the reply stored in the thread again, with
no second run of the agent. After the last try, the row ends ``failed``
(``send``), and no general text goes, because the channel itself failed.

**The text lives in the thread only.** The table holds no text (§5.9). The
member's turn is written BEFORE the 200, with an id made from the ``wamid``
(:func:`turn_id`). So a crash after the 200 loses nothing: the sweep reads the
turn back from ``chat_session_id`` and that id, and runs it.

**A shared room is not the WhatsApp thread.** A thread is a room when it has a
participant who is not its member, or a visibility other than ``private``.
The member's own ``owner`` row (a web run on the thread writes one) does not
make a room. The next text after a room opens a new solo thread, no run reads
a room, and no reply is written into one.

**WAC-3 is reads only (§5.6).** The run opens
``acb_skills.ask_tools.refuse_cards``. A tool that asks for a card gets a deny
at once and writes nothing, and the scope rule tells the model to send the
member to the web app for a change.

**One process.** The thread locks and ``_LIVE_ROWS`` live in this gateway
process, like the WAC-2 limiter. The row's claim is the guard that holds
across processes.

Fences: ``tests/unit/test_wac_bot_run.py`` (database-free) and
``tests/unit/test_wac_bot_run_r8.py`` (R8, FORCE RLS as a non-privileged role).
"""


from __future__ import annotations

import asyncio
import contextlib
import hashlib
import time
import uuid
from collections.abc import Coroutine
from dataclasses import dataclass
from typing import Any

from acb_common import get_logger
from gateway.db import bind_tenant, release_tenant, tenant_session
from gateway.routes.whatsapp_channel import flags
from sqlalchemy import text

_log = get_logger(__name__)

#: The value of ``chat_session.channel`` for this channel's threads.
CHANNEL = "whatsapp"
#: The main Chat assistant, the default agent of ``/chat`` (§5.5).
AGENT = "orchestrator"
#: The run's ``source``, for the run context and the usage page.
SOURCE = "whatsapp"
THREAD_TITLE = "WhatsApp"

# ── The fixed replies (§5.5, §5.6), word for word ──────────────────────────

REPLY_NO_WORKSPACE = (
    "Metorite cannot open this workspace from WhatsApp yet. Use the web app."
)
REPLY_CREDITS = (
    "Your organization is out of AI credits. An admin can add credits in "
    "Settings, Billing."
)
REPLY_FAILED = "Metorite could not answer just now. Try again in a few minutes."

#: The channel instruction (§5.5 "The scope rule", D-WAC-5). Advisory: no unit
#: test can prove a model's refusal (§9). It goes to the run as
#: ``system_context``, which the executor puts before the turn.
SCOPE_RULE = (
    "You are answering a member of this organization on WhatsApp. Answer only "
    "about this member's work in Metorite: their tasks, projects, calendar and "
    "the other data of their organization in Metorite. If the member asks about "
    "any other topic, refuse in one line. Keep each reply short. Write plain "
    "text, with no tables and no headings. This chat cannot show a "
    "confirmation card, so you cannot change data from WhatsApp. When the "
    "member asks for a change, tell them to make it in the Metorite web app."
)

# ── The limits ──────────────────────────────────────────────────────────────

#: WhatsApp's limit for one text message.
MAX_CHARS = 4096
#: A row that waits this long gets a run again from the sweep.
STALE_S = 5 * 60
#: The most runs one message gets.
MAX_TRIES = 3
#: The reply window. An older row gets no reply.
EXPIRE_S = 24 * 3600
#: A message this long after the thread's last activity starts a new thread.
THREAD_IDLE_S = 24 * 3600
#: Shorter than ``STALE_S``, so the sweep never takes a run that still works.
RUN_TIMEOUT_S = 4 * 60
#: The prior turns that the run sees.
HISTORY_TURNS = 20
SWEEP_EVERY_S = 60
SWEEP_BATCH = 20

#: The namespace of a thread id that a message opens (:func:`new_thread_id`).
_THREAD_NS = uuid.UUID("5f3c1a2e-9d47-4b8e-a6c1-7e2d0b9f4a13")


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]


def turn_id(wamid: str) -> str:
    """The chat message id of the member's turn. The same for a redelivery."""
    return f"wa-in-{_digest(wamid)}"


def reply_id(wamid: str) -> str:
    """The chat message id of the assistant's reply to one message."""
    return f"wa-out-{_digest(wamid)}"


def new_thread_id(organization_id: str, member_email: str, wamid: str) -> str:
    """The id of a thread that one message opens.

    Made from the message, so two copies of one first message that arrive at
    once open ONE thread, not two. It holds no colon, so it is not a run id
    (``routes/chat.py`` H-227).
    """
    return str(uuid.uuid5(_THREAD_NS, f"{organization_id}|{member_email}|{wamid}"))


@dataclass(frozen=True)
class RunRequest:
    """One recorded message that waits for its run. It holds no text."""

    message_id: str
    organization_id: str
    member_email: str
    wa_id: str
    wamid: str
    chat_session_id: str


# ── The reply text ──────────────────────────────────────────────────────────


def split_reply(reply: str, limit: int = MAX_CHARS) -> list[str]:
    """Split *reply* at paragraph breaks into chunks of at most *limit*.

    A paragraph longer than the limit splits at a line break, then at a
    space, then hard at the limit. No chunk is empty.
    """
    chunks: list[str] = []
    current = ""
    for para in [p.strip() for p in reply.split("\n\n")]:
        if not para:
            continue
        for piece in _split_long(para, limit):
            joined = f"{current}\n\n{piece}" if current else piece
            if len(joined) <= limit:
                current = joined
            else:
                chunks.append(current)
                current = piece
    if current:
        chunks.append(current)
    return chunks


def _split_long(para: str, limit: int) -> list[str]:
    out: list[str] = []
    rest = para
    while len(rest) > limit:
        cut = rest.rfind("\n", 0, limit + 1)
        if cut <= 0:
            cut = rest.rfind(" ", 0, limit + 1)
        if cut <= 0:
            cut = limit
        out.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip()
    if rest:
        out.append(rest)
    return [p for p in out if p]


def _preview(value: str) -> str:
    return " ".join(value.split())[:200]


def _now_ms() -> int:
    return int(time.time() * 1000)


# ── The bound reads and writes ──────────────────────────────────────────────
#
# Each runs in `tenant_session()` with the org that the caller bound from the
# link row (R5, R11). The chat rows go through `routes/chat.py`, the one
# writer of the chat tables.

_MEMBER_STATUS_SQL = """
SELECT status FROM app_user
 WHERE lower(email) = lower(:email)
   AND organization_id = CAST(:org AS uuid)
 LIMIT 1
"""

_INSERT_SQL = """
INSERT INTO whatsapp_bot_messages
       (organization_id, member_email, wa_id, wamid, direction, state,
        chat_session_id, error_code)
VALUES (CAST(:org AS uuid), :email, :wa, :wamid, :direction, :state,
        :sid, :code)
ON CONFLICT (wamid) DO NOTHING
RETURNING id::text AS id
"""

#: When the session ``s`` is a shared room: a visibility other than
#: ``private`` (the room route sets it with no participant row), or a
#: participant who is not the session's member. The member's OWN ``owner``
#: row (``routes/chat.py`` ``_ensure_session``) is no room. The subject of a
#: person is the plain email, the same value as ``user_id``.
_ROOM_SQL = """(
    COALESCE(s.visibility, 'private') <> 'private'
    OR EXISTS (
        SELECT 1 FROM chat_session_participant p
         WHERE p.session_id = s.id
           AND lower(p.subject) <> lower(s.user_id)
    )
)"""

#: The member's WhatsApp thread: their latest SOLO `whatsapp` session.
_FIND_THREAD_SQL = f"""
SELECT s.id FROM chat_session s
 WHERE s.channel = 'whatsapp'
   AND s.user_id = :email
   AND s.updated_at > now() - make_interval(secs => CAST(:idle AS double precision))
   AND NOT {_ROOM_SQL}
 ORDER BY s.updated_at DESC
 LIMIT 1
"""

#: What a write needs to know of a session: the agent the member chose (a
#: bot write keeps it), the other turns, and whether this turn is there.
_SESSION_STATE_SQL = """
SELECT s.agent_name,
       (SELECT count(*) FROM chat_message m
         WHERE m.session_id = s.id AND m.id <> :mid) AS others,
       EXISTS (SELECT 1 FROM chat_message m
                WHERE m.session_id = s.id AND m.id = :mid) AS has_turn
  FROM chat_session s
 WHERE s.id = :sid
"""

_SHARED_SQL = f"""
SELECT EXISTS (SELECT 1 FROM chat_session s WHERE s.id = :sid AND {_ROOM_SQL})
"""

#: The fresh claim: only a row that waits.
_CLAIM_FRESH_SQL = """
UPDATE whatsapp_bot_messages
   SET state = 'running', tries = tries + 1, updated_at = now()
 WHERE id = CAST(:id AS uuid)
   AND direction = 'in'
   AND state = 'received'
   AND tries < :max
RETURNING tries
"""

#: The sweep's claim: a row that waited too long, whatever it last did.
_CLAIM_STALE_SQL = """
UPDATE whatsapp_bot_messages
   SET state = 'running', tries = tries + 1, updated_at = now()
 WHERE id = CAST(:id AS uuid)
   AND direction = 'in'
   AND state IN ('received', 'running')
   AND tries < :max
   AND updated_at < now() - make_interval(secs => CAST(:stale AS double precision))
RETURNING tries
"""

_REFUSE_SQL = """
UPDATE whatsapp_bot_messages
   SET state = 'refused', error_code = :code, updated_at = now()
 WHERE id = CAST(:id AS uuid)
   AND state IN ('received', 'running')
RETURNING id
"""

_END_SQL = """
UPDATE whatsapp_bot_messages
   SET state = :state, error_code = :code, updated_at = now()
 WHERE id = CAST(:id AS uuid)
   AND state = 'running'
RETURNING id
"""

#: One state change, from one state only. `sending` is set just before the
#: first part of a reply goes out, and nothing takes a `sending` row again.
_MOVE_SQL = """
UPDATE whatsapp_bot_messages
   SET state = :to, error_code = :code, updated_at = now()
 WHERE id = CAST(:id AS uuid)
   AND state = :frm
RETURNING id
"""

#: A failure before any reply went out: the row waits again, with its try
#: counted, so the sweep runs it again.
_RETRY_SQL = """
UPDATE whatsapp_bot_messages
   SET state = 'received', error_code = :code, updated_at = now()
 WHERE id = CAST(:id AS uuid)
   AND state = 'running'
RETURNING id
"""

#: The waiting rows of ONE thread, oldest first.
_NEXT_SQL = """
SELECT id::text AS id, organization_id::text AS organization_id,
       member_email, wa_id, wamid, chat_session_id
  FROM whatsapp_bot_messages
 WHERE chat_session_id = :sid
   AND direction = 'in'
   AND state = 'received'
   AND tries < :max
   AND received_at >= now() - make_interval(secs => CAST(:expire AS double precision))
 ORDER BY received_at, id
 LIMIT :n
"""

#: The member's own turn. A shared room gives no row: no run reads a room.
_TURN_SQL = f"""
SELECT content, timestamp_ms FROM chat_message
 WHERE session_id = :sid AND id = :mid AND role = 'user'
   AND NOT EXISTS (SELECT 1 FROM chat_session s WHERE s.id = :sid AND {_ROOM_SQL})
"""

#: The reply that a try already wrote into the thread. A retry sends it again
#: with no second run of the agent.
_STORED_REPLY_SQL = """
SELECT content FROM chat_message
 WHERE session_id = :sid AND id = :mid AND role = 'assistant'
"""

#: The prior turns: the thread as it was at this turn. A row written at or
#: before this turn, of either role. A bot reply counts by the turn it
#: answers, not by its own time: a newer text's turn was written before the
#: 200, so the reply to an older text is LATER than this turn and still
#: belongs here, and the reply to a NEWER text does not (a retry).
_HISTORY_SQL = f"""
SELECT role, content FROM chat_message m
 WHERE m.session_id = :sid
   AND m.id <> :mid
   AND m.role IN ('user', 'assistant')
   AND (
       m.timestamp_ms <= :ts
       OR (m.role = 'assistant' AND m.id LIKE 'wa-out-%' AND EXISTS (
           SELECT 1 FROM chat_message q
            WHERE q.session_id = :sid
              AND q.role = 'user'
              AND q.id = 'wa-in-' || substr(m.id, 8)
              AND q.timestamp_ms <= :ts))
   )
   AND NOT EXISTS (SELECT 1 FROM chat_session s WHERE s.id = :sid AND {_ROOM_SQL})
 ORDER BY timestamp_ms DESC, id DESC
 LIMIT :n
"""

_EXPIRE_SQL = """
UPDATE whatsapp_bot_messages
   SET state = 'expired', updated_at = now()
 WHERE direction = 'in'
   AND state IN ('received', 'running')
   AND received_at < now() - make_interval(secs => CAST(:expire AS double precision))
RETURNING id
"""

#: A row that used its tries. It gets the general text once, from the sweep.
#: Never a `sending` row, and never a row whose run is live in this process.
_EXHAUSTED_SQL = """
UPDATE whatsapp_bot_messages
   SET state = 'failed', error_code = 'tries', updated_at = now()
 WHERE direction = 'in'
   AND state IN ('received', 'running')
   AND tries >= :max
   AND updated_at < now() - make_interval(secs => CAST(:stale AS double precision))
   AND id::text <> ALL(CAST(:live AS text[]))
RETURNING id::text AS id, organization_id::text AS organization_id,
          member_email, wa_id, wamid, chat_session_id
"""

#: A `sending` row that nobody closed (a crash or a cancel between the send
#: and the end write). A part may have gone out, so it is never sent again.
_UNCONFIRMED_SQL = """
UPDATE whatsapp_bot_messages
   SET state = 'replied', error_code = 'send_unconfirmed', updated_at = now()
 WHERE direction = 'in'
   AND state = 'sending'
   AND updated_at < now() - make_interval(secs => CAST(:stale AS double precision))
   AND id::text <> ALL(CAST(:live AS text[]))
RETURNING id
"""

#: The OLDEST waiting row of each thread, when it waited too long. The run
#: then works the rest of the thread in order.
_STALE_SQL = """
SELECT id, organization_id, member_email, wa_id, wamid, chat_session_id
  FROM (
      SELECT DISTINCT ON (chat_session_id)
             id::text AS id, organization_id::text AS organization_id,
             member_email, wa_id, wamid, chat_session_id, updated_at,
             received_at
        FROM whatsapp_bot_messages
       WHERE direction = 'in'
         AND state IN ('received', 'running')
         AND tries < :max
         AND chat_session_id IS NOT NULL
         AND received_at >= now() - make_interval(secs => CAST(:expire AS double precision))
       ORDER BY chat_session_id, received_at, id
  ) oldest
 WHERE updated_at < now() - make_interval(secs => CAST(:stale AS double precision))
 ORDER BY received_at
 LIMIT :n
"""


def _request(row: Any) -> RunRequest:
    return RunRequest(str(row["id"]), str(row["organization_id"]),
                      str(row["member_email"]), str(row["wa_id"]),
                      str(row["wamid"]), str(row["chat_session_id"]))


async def _member_active(org: str, email: str) -> bool:
    """True when *email* is an ACTIVE member of *org*. A bound read."""
    async with tenant_session() as db:
        row = (await db.execute(
            text(_MEMBER_STATUS_SQL), {"org": org, "email": email},
        )).mappings().first()
    return row is not None and row["status"] == "active"


async def _insert(
    *, org: str, email: str, wa_id: str, wamid: str, direction: str,
    state: str, session_id: str | None, code: str | None = None,
) -> str | None:
    """Record one message. Returns the new row id, or None for a repeat."""
    async with tenant_session() as db:
        row = (await db.execute(text(_INSERT_SQL), {
            "org": org, "email": email, "wa": wa_id, "wamid": wamid,
            "direction": direction, "state": state, "sid": session_id,
            "code": code,
        })).mappings().first()
    return str(row["id"]) if row else None


async def _find_thread(email: str) -> str | None:
    async with tenant_session() as db:
        row = (await db.execute(
            text(_FIND_THREAD_SQL), {"email": email, "idle": THREAD_IDLE_S},
        )).mappings().first()
    return str(row["id"]) if row else None


async def _session_state(
    session_id: str, message_id: str,
) -> tuple[str | None, int, bool]:
    """``(agent_name, other turns, this turn is there)``. No row: a new one."""
    async with tenant_session() as db:
        row = (await db.execute(
            text(_SESSION_STATE_SQL), {"sid": session_id, "mid": message_id},
        )).mappings().first()
    if row is None:
        return None, 0, False
    return (str(row["agent_name"]) if row["agent_name"] else None,
            int(row["others"] or 0), bool(row["has_turn"]))


async def _thread_shared(session_id: str) -> bool:
    """True when the thread has any participant row: it is a shared room."""
    async with tenant_session() as db:
        return bool((await db.execute(
            text(_SHARED_SQL), {"sid": session_id},
        )).scalar())


async def _write_session(
    org: str, email: str, session_id: str, *, agent: str, preview: str,
    count: int,
) -> None:
    from gateway.routes.chat import SessionUpsertRequest, _upsert_session

    await asyncio.to_thread(
        _upsert_session, email,
        SessionUpsertRequest(
            id=session_id, agent_name=agent, title=THREAD_TITLE,
            last_preview=preview, message_count=count,
        ),
        organization_id=org, channel=CHANNEL,
    )


async def _write_turn(
    org: str, email: str, wamid: str, body: str,
) -> str:
    """Write the member's turn into the WhatsApp thread. Returns the thread id.

    The thread is the member's latest solo ``whatsapp`` session in this org.
    A message more than 24 hours after its last activity opens a new one
    (§5.5), and so does a thread that became a shared room. A redelivery
    whose turn is already there writes nothing, so the preview keeps the
    newest text. A bot write keeps the agent the member chose on the web.
    """
    from gateway.routes.chat import MessageRecord, _upsert_messages

    mid = turn_id(wamid)
    sid = await _find_thread(email) or new_thread_id(org, email, wamid)
    agent, others, has_turn = await _session_state(sid, mid)
    if has_turn:
        return sid
    await _write_session(org, email, sid, agent=agent or AGENT,
                         preview=_preview(body), count=others + 1)
    declined = await asyncio.to_thread(
        _upsert_messages, sid,
        [MessageRecord(id=mid, role="user", content=body, timestamp=_now_ms())],
        actor_email=email, organization_id=org,
    )
    if declined:
        raise RuntimeError("the member's turn was declined")
    return sid


async def _write_reply(req: RunRequest, reply: str) -> None:
    from gateway.chat_fold import persist_channel_reply

    mid = reply_id(req.wamid)
    agent, others, _has = await _session_state(req.chat_session_id, mid)
    await _write_session(req.organization_id, req.member_email,
                         req.chat_session_id, agent=agent or AGENT,
                         preview=_preview(reply), count=others + 1)
    if not await persist_channel_reply(
        req.chat_session_id, mid, reply,
        user_id=req.member_email, agent_name=AGENT,
        timestamp_ms=_now_ms(), organization_id=req.organization_id,
    ):
        raise RuntimeError("the reply was declined")


async def _claim(message_id: str, *, stale: bool) -> int | None:
    """Claim one row for a run. Returns its try number, or None."""
    sql, params = (
        (_CLAIM_STALE_SQL, {"id": message_id, "max": MAX_TRIES, "stale": STALE_S})
        if stale else
        (_CLAIM_FRESH_SQL, {"id": message_id, "max": MAX_TRIES})
    )
    async with tenant_session() as db:
        row = (await db.execute(text(sql), params)).first()
    return int(row[0]) if row is not None else None


async def _refuse(message_id: str, code: str) -> bool:
    async with tenant_session() as db:
        return (await db.execute(
            text(_REFUSE_SQL), {"id": message_id, "code": code},
        )).first() is not None


async def _end(message_id: str, state: str, code: str | None) -> bool:
    async with tenant_session() as db:
        return (await db.execute(
            text(_END_SQL), {"id": message_id, "state": state, "code": code},
        )).first() is not None


async def _move(message_id: str, frm: str, to: str, code: str | None) -> bool:
    """Change the row's state from *frm* to *to*. False when it was not *frm*."""
    async with tenant_session() as db:
        return (await db.execute(text(_MOVE_SQL), {
            "id": message_id, "frm": frm, "to": to, "code": code,
        })).first() is not None


async def _stored_reply(session_id: str, wamid: str) -> str | None:
    """The reply that an earlier try of this message wrote, or None."""
    async with tenant_session() as db:
        row = (await db.execute(text(_STORED_REPLY_SQL), {
            "sid": session_id, "mid": reply_id(wamid),
        })).first()
    return str(row[0]) if row is not None and row[0] else None


async def _retry(message_id: str, code: str) -> bool:
    async with tenant_session() as db:
        return (await db.execute(
            text(_RETRY_SQL), {"id": message_id, "code": code},
        )).first() is not None


async def _waiting(session_id: str) -> list[RunRequest]:
    """The waiting rows of one thread, oldest first."""
    async with tenant_session() as db:
        rows = (await db.execute(text(_NEXT_SQL), {
            "sid": session_id, "max": MAX_TRIES, "expire": EXPIRE_S,
            "n": SWEEP_BATCH,
        })).mappings().all()
    return [_request(r) for r in rows]


async def _thread_turns(
    session_id: str, message_id: str,
) -> tuple[str | None, list[dict[str, str]]]:
    """The member's turn, and the prior turns of the thread in order."""
    async with tenant_session() as db:
        turn = (await db.execute(
            text(_TURN_SQL), {"sid": session_id, "mid": message_id},
        )).mappings().first()
        if turn is None:
            return None, []
        rows = (await db.execute(text(_HISTORY_SQL), {
            "sid": session_id, "mid": message_id,
            "ts": int(turn["timestamp_ms"]), "n": HISTORY_TURNS,
        })).mappings().all()
    history = [{"role": str(r["role"]), "content": str(r["content"] or "")}
               for r in reversed(rows)]
    return str(turn["content"] or ""), history


# ── Before the 200 ──────────────────────────────────────────────────────────


#: One lock for each member in each org, in this process. Two copies of one
#: first message that arrive at once would both open the same thread id, and
#: the second one's `_refuse_if_elsewhere` can read the first one's row
#: between its two reads. So the thread write and the insert run one after
#: the other. Bounded: a full map drops every lock that nobody holds.
_THREAD_LOCKS: dict[tuple[str, str], asyncio.Lock] = {}
_THREAD_LOCKS_MAX = 1024


def _thread_lock(org: str, email: str) -> asyncio.Lock:
    key = (org, email)
    lock = _THREAD_LOCKS.get(key)
    if lock is None:
        if len(_THREAD_LOCKS) >= _THREAD_LOCKS_MAX:
            for k in [k for k, lk in _THREAD_LOCKS.items() if not lk.locked()]:
                del _THREAD_LOCKS[k]
        lock = _THREAD_LOCKS[key] = asyncio.Lock()
    return lock


def current_link(links: list[Any]) -> Any | None:
    """The phone's CURRENT link, or None. Every run uses it (§5.3)."""
    return next((lk for lk in links if lk["is_current"]), None)


async def record_inbound(
    links: list[Any], wa_id: str, wamid: str, body: str,
) -> RunRequest | None:
    """Record one text from a linked phone. Returns the run to start, or None.

    *links* is what ``whatsapp_member_links_for_phone`` returned. Runs inside
    the webhook request, before the 200, so the turn is durable before Meta
    hears that the message arrived.
    """
    hint = wa_id[-4:]
    link = current_link(links)
    if link is None:
        _log.info("whatsapp_channel.run.no_current_link", phone_hint=hint)
        return None
    org = str(link["organization_id"])
    email = str(link["member_email"]).strip().lower()
    if not flags.org_allowed(org):
        _log.info("whatsapp_channel.run.org_closed", phone_hint=hint,
                  organization_id=org)
        return None

    from gateway.rooms import SessionOfAnotherTenant

    token = bind_tenant(org)
    try:
        if not await _member_active(org, email):
            await _insert(org=org, email=email, wa_id=wa_id, wamid=wamid,
                          direction="in", state="refused", session_id=None,
                          code="inactive")
            _log.info("whatsapp_channel.run.refused", phone_hint=hint,
                      organization_id=org, reason="inactive")
            return None
        async with _thread_lock(org, email):
            try:
                session_id = await _write_turn(org, email, wamid, body)
            except SessionOfAnotherTenant:
                # A copy of this message in ANOTHER process opened the
                # thread between the two reads of `_refuse_if_elsewhere`.
                # Once more: the thread read finds it now. A row of another
                # tenant raises again, and nothing is written.
                session_id = await _write_turn(org, email, wamid, body)
            row_id = await _insert(org=org, email=email, wa_id=wa_id,
                                   wamid=wamid, direction="in",
                                   state="received", session_id=session_id)
    finally:
        release_tenant(token)

    if row_id is None:
        _log.info("whatsapp_channel.run.redelivered", phone_hint=hint,
                  organization_id=org)
        return None
    _log.info("whatsapp_channel.run.received", phone_hint=hint,
              organization_id=org, message_id=row_id)
    return RunRequest(row_id, org, email, wa_id, wamid, session_id)


# ── After the 200: the run ──────────────────────────────────────────────────

_RUNS: set[asyncio.Task[None]] = set()
#: The rows whose run is live in this process.
_LIVE_ROWS: set[str] = set()
#: One lock for each THREAD with a run waiting or live in this process, and
#: how many runs hold or wait for it. A lock leaves the map with its last
#: user, so the map holds only the threads that are busy now.
_RUN_LOCKS: dict[str, asyncio.Lock] = {}
_RUN_LOCK_USERS: dict[str, int] = {}

#: How long a cancelled run may take to close a row whose reply went out.
_CANCEL_WRITE_S = 2.0

#: The refusals that tell the member something. Every other one is silent.
_REFUSAL_REPLIES = {"identity": REPLY_NO_WORKSPACE, "shared": REPLY_FAILED}


def _start_task(run: Coroutine[Any, Any, None]) -> asyncio.Task[None]:
    task = asyncio.get_running_loop().create_task(run)
    _RUNS.add(task)
    task.add_done_callback(_RUNS.discard)
    return task


def start(req: RunRequest, *, stale: bool = False) -> asyncio.Task[None]:
    """Start the run of one message as a task of its own, and return at once."""
    return _start_task(run_message(req, stale=stale))


async def stop_runs(timeout: float = 5.0) -> None:
    """Cancel the live runs (gateway shutdown). Each row stays ``running``,
    so the sweep of the next process runs it again."""
    runs = list(_RUNS)
    for task in runs:
        task.cancel()
    if runs:
        await asyncio.wait(runs, timeout=timeout)


async def wait_for_runs() -> None:
    """Wait until every run that this module started has ended (tests)."""
    while _RUNS:
        await asyncio.gather(*list(_RUNS), return_exceptions=True)


def _executor() -> Any:
    """The executor's batch entry point, in this process.

    The gateway and the orchestrator share one process: ``routes/agent.py``
    and ``routes/projects/agent_dispatch.py`` call ``run_agent`` the same
    way. No new transport.
    """
    from orchestrator.executor import run_agent

    return run_agent


async def _may_chat(email: str) -> bool:
    """``feature:chat`` and the run of the Chat assistant, in the bound org.

    The same resolver as the request path (``acb_auth.access``), with the
    link's org bound, so the read of ``app_user`` is the org's.
    """
    from acb_auth.access import resolve_access

    access = await resolve_access(email)
    return bool(access.is_active and access.can_use_feature("chat")
                and access.can_run_agent(AGENT))


async def _identity_org(email: str) -> tuple[str | None, bool]:
    """The org that ``resolve_identity`` gives *email*, and whether it is sure.

    The tools find the org again from the email (§5.5 "The tools find the org
    again"). A read that failed is not an answer, so the run waits for the
    sweep rather than refuse.
    """
    from acb_auth.access import (
        IdentityUnavailable,
        identity_read_failed,
        resolve_identity,
    )

    try:
        _user_id, org = await resolve_identity(email)
    except IdentityUnavailable:
        return None, False
    if org is None and identity_read_failed():
        return None, False
    return org, True


async def _thread_busy(session_id: str) -> bool:
    """True when a WEB run is live on the thread. A WhatsApp run of this
    process cannot be: the thread's lock holds it."""
    try:
        from orchestrator.stream_relay import is_active

        return bool(await is_active(session_id))
    except Exception:  # fail open, like `_refuse_if_another_run_is_active`
        _log.warning("whatsapp_channel.run.active_check_failed")
        return False


async def _check(req: RunRequest) -> str | None:
    """Why this run must not start, or None. The order is the spec's (§5.5).

    ``later`` means "not now": the row stays ``received`` and the sweep asks
    again. Any other answer refuses the row.
    """
    if not flags.org_allowed(req.organization_id):
        return "closed"
    from gateway.routes.whatsapp_channel import inbound

    link = current_link(await inbound._active_links_for_phone(req.wa_id))
    if (link is None or str(link["organization_id"]) != req.organization_id
            or str(link["member_email"]).strip().lower() != req.member_email):
        return "link"
    if not await _member_active(req.organization_id, req.member_email):
        return "inactive"
    if not await _may_chat(req.member_email):
        return "feature"
    org, sure = await _identity_org(req.member_email)
    if not sure:
        return "later"
    if org != req.organization_id:
        return "identity"
    if await _thread_shared(req.chat_session_id):
        # The thread became a shared room after the text arrived. No run
        # reads a room. The member's next text opens a new solo thread.
        return "shared"
    if await _thread_busy(req.chat_session_id):
        return "later"
    return None


def _error_code(exc: BaseException) -> str:
    from acb_llm.run_errors import classify_run_error

    original = getattr(exc, "original", None)
    if isinstance(original, BaseException):
        code = classify_run_error(original)
        if code != "unknown":
            return code
    return classify_run_error(exc)


@dataclass
class _Attempt:
    """What one claimed try has done so far: its try number, and whether any
    part of a reply went out. A failure reads it to never lose and never
    send twice."""

    tries: int = 0
    sent: bool = False
    #: The row is `sending` in the database.
    marked: bool = False


async def run_message(req: RunRequest, *, stale: bool = False) -> None:
    """Run the waiting rows of *req*'s thread, one at a time, oldest first.

    The thread's lock is taken BEFORE the first await, so two runs of one
    thread in this process can never interleave. *stale* is the sweep's: it
    runs *req* itself first, whatever its state. Never raises, except a
    cancel.
    """
    sid = req.chat_session_id
    _RUN_LOCK_USERS[sid] = _RUN_LOCK_USERS.get(sid, 0) + 1
    lock = _RUN_LOCKS.setdefault(sid, asyncio.Lock())
    try:
        async with lock:
            token = bind_tenant(req.organization_id)
            try:
                await _drain(req, stale=stale)
            finally:
                release_tenant(token)
    finally:
        _RUN_LOCK_USERS[sid] -= 1
        if _RUN_LOCK_USERS[sid] <= 0:
            _RUN_LOCK_USERS.pop(sid, None)
            _RUN_LOCKS.pop(sid, None)


async def _drain(req: RunRequest, *, stale: bool) -> None:
    """Work the thread's waiting rows in ``received_at`` order.

    Each row is tried once in a drain. A row that waits again (a web run
    holds the thread, a failure gave it back) is left for the sweep, and the
    newer rows still run.
    """
    seen: set[str] = set()
    if stale:
        seen.add(req.message_id)
        await _process(req, stale=True)
    while True:
        try:
            waiting = await _waiting(req.chat_session_id)
        except Exception as exc:
            _log.warning("whatsapp_channel.run.next_failed",
                         error_type=type(exc).__name__)
            return
        nxt = next((r for r in waiting
                    if r.message_id not in seen
                    and r.message_id not in _LIVE_ROWS), None)
        if nxt is None:
            return
        seen.add(nxt.message_id)
        await _process(nxt, stale=False)


async def _process(req: RunRequest, *, stale: bool) -> None:
    """Check, claim, run and reply for ONE row. Never raises, except a cancel."""
    if req.message_id in _LIVE_ROWS:
        return
    _LIVE_ROWS.add(req.message_id)
    hint = req.wa_id[-4:]
    attempt = _Attempt()
    try:
        reason = await _check(req)
        if reason == "later":
            _log.info("whatsapp_channel.run.deferred", phone_hint=hint,
                      organization_id=req.organization_id,
                      message_id=req.message_id)
            return
        if reason is not None:
            refused = await _refuse(req.message_id, reason)
            _log.info("whatsapp_channel.run.refused", phone_hint=hint,
                      organization_id=req.organization_id,
                      message_id=req.message_id, reason=reason)
            if refused and reason in _REFUSAL_REPLIES:
                await _send(req, [_REFUSAL_REPLIES[reason]], kind=reason)
            return
        tries = await _claim(req.message_id, stale=stale)
        if tries is None:
            _log.info("whatsapp_channel.run.not_claimed", phone_hint=hint,
                      message_id=req.message_id)
            return
        attempt.tries = tries
        await _answer(req, attempt)
    except asyncio.CancelledError:
        # A restart. Before `sending`, the row stays `running` and the next
        # sweep takes it. After a part went out, the end write is shielded
        # and bounded. If it still fails, the row stays `sending`, which no
        # run takes again, so nothing is sent twice.
        if attempt.sent:
            with contextlib.suppress(BaseException):
                await asyncio.wait_for(asyncio.shield(_move(
                    req.message_id, "sending", "replied", "cancelled")),
                    timeout=_CANCEL_WRITE_S)
        raise
    except Exception as exc:  # the record names the class only
        _log.warning("whatsapp_channel.run.failed_internal",
                     message_id=req.message_id, error_type=type(exc).__name__,
                     tries=attempt.tries, sent=attempt.sent)
        await _after_failure(req, attempt)
    finally:
        _LIVE_ROWS.discard(req.message_id)


async def _after_failure(req: RunRequest, attempt: _Attempt) -> None:
    """Close a try that raised. Never loses a message, never sends twice.

    * Not claimed yet: nothing changed, and the row still waits.
    * The row is ``sending``: a part may have gone out. The end write tries
      once more. Every other write below needs ``running``, so a ``sending``
      row is never run again and never gets the general text.
    * Tries left: the row waits again (``received``), and the sweep runs it.
    * The last try: the row is ``failed``, and the general text goes once.
    """
    if attempt.tries == 0:
        return
    with contextlib.suppress(Exception):
        if attempt.marked:
            if attempt.sent:
                await _move(req.message_id, "sending", "replied", "internal")
            elif attempt.tries < MAX_TRIES:
                # The send refused its first part: the stored reply goes on
                # the next try.
                await _move(req.message_id, "sending", "received", "send")
            else:
                await _move(req.message_id, "sending", "failed", "send")
        elif attempt.tries < MAX_TRIES:
            await _retry(req.message_id, "internal")
        elif await _end(req.message_id, "failed", "internal"):
            await _send(req, [REPLY_FAILED], kind="failed")


def build_payload(message: str, history: list[dict[str, str]],
                  member_email: str) -> dict[str, Any]:
    """The event payload of one run.

    ``_history_loader`` makes the batch executor take its STRUCTURED path on
    every turn, the first one too: the scope rule is then a leading system
    message, and never text folded into the member's turn
    (``executor._run_with_maf_agent``).
    """
    prior = list(history)
    return {
        "mode": "chat",
        "message": message,
        "messages": prior,
        "_history_loader": lambda: list(prior),
        "system_context": SCOPE_RULE,
        "think_mode": "auto",
        # How the tools reach the member (`executor._payload_user`). The
        # server's value, from the link row, never from the message.
        "user_email": member_email,
        "source": SOURCE,
    }


async def _answer(req: RunRequest, attempt: _Attempt) -> None:
    """The claimed run: read the turn, run the assistant, write, send."""
    message, history = await _thread_turns(req.chat_session_id, turn_id(req.wamid))
    if not message:
        if await _end(req.message_id, "failed", "no_turn"):
            await _send(req, [REPLY_FAILED], kind="failed", attempt=attempt)
        return

    run_id = str(uuid.uuid4())
    # A try whose reply is already in the thread (its send failed, or it
    # crashed before `sending`) sends that reply again. The agent runs once.
    reply = await _stored_reply(req.chat_session_id, req.wamid)
    if reply is None:
        payload = build_payload(message, history, req.member_email)
        from acb_skills.ask_tools import refuse_cards

        try:
            with refuse_cards():
                result = await asyncio.wait_for(
                    _executor()(
                        AGENT, payload,
                        run_id=run_id,
                        thread_id=req.chat_session_id,
                        model=None,
                        # D-WAC-3: the link's org, bound explicitly.
                        organization_id=req.organization_id,
                        # H-73: the member who pays is the link's member.
                        session_user=req.member_email,
                    ),
                    timeout=RUN_TIMEOUT_S,
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            code = _error_code(exc)
            _log.warning("whatsapp_channel.run.agent_failed", run_id=run_id,
                         message_id=req.message_id, code=code,
                         error_type=type(exc).__name__)
            if await _end(req.message_id, "failed", code):
                await _send(req, [REPLY_CREDITS if code == "credits" else REPLY_FAILED],
                            kind="failed", attempt=attempt)
            return

        from gateway.routes.projects.agent_dispatch import reply_text

        reply = reply_text(result).strip()
        if not reply:
            if await _end(req.message_id, "failed", "empty"):
                await _send(req, [REPLY_FAILED], kind="failed", attempt=attempt)
            return
        if await _thread_shared(req.chat_session_id):
            # The thread became a room while the agent ran. The answer was
            # made for one member, so it goes into no room: nothing is
            # written, and the member gets the general text once.
            if await _end(req.message_id, "refused", "shared"):
                await _send(req, [REPLY_FAILED], kind="shared", attempt=attempt)
            return
        await _write_reply(req, reply)
    else:
        _log.info("whatsapp_channel.run.resend_stored", run_id=run_id,
                  message_id=req.message_id)

    # The durable send mark. From here no run takes this row again, and
    # nothing sends it the general text.
    if not await _move(req.message_id, "running", "sending", None):
        _log.info("whatsapp_channel.run.not_sending", message_id=req.message_id)
        return
    attempt.marked = True
    parts = split_reply(reply)
    sent = await _send(req, parts, kind="answer", attempt=attempt)
    if sent == len(parts):
        await _move(req.message_id, "sending", "replied", None)
    elif sent:
        # A part went out. The rest is lost, and nothing goes twice.
        await _move(req.message_id, "sending", "replied", "send_partial")
    elif attempt.tries < MAX_TRIES:
        # Nothing went out. The next try sends the stored reply again.
        await _move(req.message_id, "sending", "received", "send")
    else:
        # The channel itself failed on every try. No general text either.
        await _move(req.message_id, "sending", "failed", "send")
    _log.info("whatsapp_channel.run.replied" if sent == len(parts) else
              "whatsapp_channel.run.send_failed",
              run_id=run_id, message_id=req.message_id, chars=len(reply),
              parts=len(parts), sent=sent)


async def _send(req: RunRequest, texts: list[str], *, kind: str,
                attempt: _Attempt | None = None) -> int:
    """Send each text from the bot number, and record each one sent.

    Logs Meta's error fields only, never the exception text, which can carry
    a URL or a token. Returns how many texts went out, and stops at the first
    one that fails. *attempt* notes that a part went out.
    """
    hint = req.wa_id[-4:]
    creds = flags.bot_credentials()
    if creds is None:
        _log.warning("whatsapp_channel.run.no_token_at_send", kind=kind)
        return 0

    from gateway.routes.whatsapp.transport.connect import meta_error_fields
    from whatsapp_ingestion.providers.factory import build_provider

    try:
        provider = build_provider("cloud_api", creds)
    except ValueError as exc:
        _log.warning("whatsapp_channel.run.provider_refused",
                     error_class=type(exc).__name__)
        return 0
    sent = 0
    for part in texts:
        try:
            out_id = await provider.send_text(req.wa_id, part)
        except Exception as exc:
            _log.warning("whatsapp_channel.run.reply_failed", kind=kind,
                         phone_hint=hint, sent=sent, **meta_error_fields(exc))
            return sent
        sent += 1
        if attempt is not None:
            attempt.sent = True
        if out_id:
            try:
                await _insert(
                    org=req.organization_id, email=req.member_email,
                    wa_id=req.wa_id, wamid=str(out_id)[:200], direction="out",
                    state="replied", session_id=req.chat_session_id,
                )
            except Exception as exc:  # the send already happened
                _log.warning("whatsapp_channel.run.out_record_failed",
                             error_type=type(exc).__name__)
    _log.info("whatsapp_channel.run.reply_sent", kind=kind, phone_hint=hint,
              parts=len(texts))
    return sent


# ── The sweep ───────────────────────────────────────────────────────────────


def _uuid_or_none(value: str) -> str | None:
    try:
        return str(uuid.UUID(value))
    except ValueError:
        return None


async def _sweep_org(org: str) -> list[RunRequest]:
    """Expire, close and collect the waiting rows of ONE org. Bound.

    A row that used its tries gets the general text once, here: the UPDATE
    that closes it returns it only once.
    """
    token = bind_tenant(org)
    try:
        async with tenant_session() as db:
            expired = (await db.execute(
                text(_EXPIRE_SQL), {"expire": EXPIRE_S},
            )).fetchall()
            live = sorted(_LIVE_ROWS)
            unconfirmed = (await db.execute(
                text(_UNCONFIRMED_SQL), {"stale": STALE_S, "live": live},
            )).fetchall()
            exhausted = (await db.execute(text(_EXHAUSTED_SQL), {
                "max": MAX_TRIES, "stale": STALE_S, "live": live,
            })).mappings().all()
            rows = (await db.execute(text(_STALE_SQL), {
                "max": MAX_TRIES, "stale": STALE_S, "expire": EXPIRE_S,
                "n": SWEEP_BATCH,
            })).mappings().all()
        for row in exhausted:
            if row["chat_session_id"] is None:
                continue
            with contextlib.suppress(Exception):
                await _send(_request(row), [REPLY_FAILED], kind="tries")
    finally:
        release_tenant(token)
    if expired or exhausted or unconfirmed:
        _log.info("whatsapp_channel.sweep.closed", organization_id=org,
                  expired=len(expired), exhausted=len(exhausted),
                  unconfirmed=len(unconfirmed))
    # A thread whose lock a run of this process holds runs its rows in that
    # drain. A second task would only wait and then fail its claim.
    return [_request(r) for r in rows
            if str(r["id"]) not in _LIVE_ROWS
            and str(r["chat_session_id"]) not in _RUN_LOCKS]


async def sweep_once() -> int:
    """One pass over every org of the allowlist. Returns the runs started.

    Only with the switch on. Each org is bound in turn, so the sweep needs no
    SECURITY DEFINER function. One bad org does not stop the others.
    """
    if not flags.assistant_enabled():
        return 0
    started = 0
    for raw in sorted(flags.assistant_orgs()):
        org = _uuid_or_none(raw)
        if org is None:
            _log.warning("whatsapp_channel.sweep.bad_org_id", length=len(raw))
            continue
        try:
            for req in await _sweep_org(org):
                start(req, stale=True)
                started += 1
        except Exception as exc:
            _log.warning("whatsapp_channel.sweep.org_failed",
                         organization_id=org, error_type=type(exc).__name__)
    return started


_sweep_task: asyncio.Task[None] | None = None


async def _sweep_loop() -> None:
    while True:
        try:
            started = await sweep_once()
            if started:
                _log.info("whatsapp_channel.sweep.started_runs", runs=started)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # never let one bad pass kill the loop
            _log.warning("whatsapp_channel.sweep.failed",
                         error_type=type(exc).__name__)
        await asyncio.sleep(SWEEP_EVERY_S)


async def start_sweep() -> None:
    """Launch the one sweep loop (the gateway lifespan). It reads the switch
    on each pass, so a dark channel costs one settings read a minute."""
    global _sweep_task
    if _sweep_task and not _sweep_task.done():
        return
    _sweep_task = asyncio.create_task(_sweep_loop())
    _log.info("whatsapp_channel.sweep.started")


async def stop_sweep() -> None:
    """Cancel the sweep loop (gateway shutdown)."""
    global _sweep_task
    task, _sweep_task = _sweep_task, None
    if task and not task.done():
        task.cancel()
