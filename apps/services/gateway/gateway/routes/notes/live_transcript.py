"""Server-side live-transcript bus for in-progress meetings.

The batch transcript (``transcript_segment``) stays **authoritative** — this is
the fast, in-memory *live* stream produced *while a meeting is still happening*.
It powers three things, all off one pipe:

1. **Live captions** in the UI during a bot meeting (SSE, like the browser
   recorder's caption band).
2. **Acting on the transcript in real time** — an agent subscribes to
   ``subscribe(meeting_id)`` server-side and can react as words arrive (the hook
   the "interject speaking points" feature will build on).
3. **Interjecting back into the meeting** — ``POST …/say`` forwards text to the
   bot worker, which speaks it into the call (TTS → the bot's virtual mic).

Producers push segments to ``POST …/live/segment`` (the meeting-bot worker's
streaming ASR today; the browser recorder can feed the same bus later). Each
segment is run through the **live speaker registry** (``live_speakers.py``) on
the way in, so it gets a *stable* speaker id (from a running voiceprint gallery)
and, when someone self-introduces, a live name — the identity context the copilot
consumes. Nothing here is persisted: the authoritative transcript is still the
batch re-pass on completion, so live is a fast *draft* that the batch pass
upgrades — not a throwaway (spec §3.4 / §5.2 principle; §3.5 of the copilot spec).

Design: a per-meeting in-memory ring + asyncio fan-out. Single-process, best-
effort; a gateway restart drops the live buffer (the recording + batch pass are
unaffected). Spec: note_taker_app.md §3.13 (streaming extension).
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import os
import time
from collections import deque
from collections.abc import AsyncIterator

from acb_auth import UserContext, get_current_user
from fastapi import Depends, Header, HTTPException
from fastapi.responses import StreamingResponse
from gateway.routes.notes.core import (
    _log,
    _tenant_session,
    load_owned_meeting,
    require_owned_meeting,
    router,
)
from pydantic import BaseModel
from sqlalchemy import text

_RING = 200          # live segments kept per meeting for late subscribers
_QUEUE_MAX = 500     # per-subscriber backlog before we drop oldest


class LiveSegment(BaseModel):
    text: str
    start_s: float = 0.0
    end_s: float = 0.0
    speaker_label: str | None = None
    is_final: bool = True
    ts: float | None = None  # server stamp added on publish
    # Optional per-chunk speaker embedding (voiceprint). When present, the live
    # speaker registry uses it to assign a *stable* speaker id across chunks; it
    # is stripped before fan-out (never sent to captions/agents). Sources that
    # can't produce one (browser channel path) simply omit it.
    embedding: list[float] | None = None
    # Resolved on publish by the registry — the fields consumers read.
    speaker_id: str | None = None
    speaker_name: str | None = None
    role: str | None = None


class _Bus:
    """Per-meeting live fan-out: a ring of recent segments + live subscribers."""

    def __init__(self) -> None:
        self.ring: deque[dict] = deque(maxlen=_RING)
        self.subs: set[asyncio.Queue] = set()

    def publish(self, seg: dict) -> None:
        self.ring.append(seg)
        for q in list(self.subs):
            if q.qsize() >= _QUEUE_MAX:
                # drop oldest so a slow consumer can't wedge the bus
                with contextlib.suppress(asyncio.QueueEmpty):
                    q.get_nowait()
            q.put_nowait(seg)

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self.subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self.subs.discard(q)


_BUSES: dict[str, _Bus] = {}


def _bus(meeting_id: str) -> _Bus:
    bus = _BUSES.get(meeting_id)
    if bus is None:
        bus = _Bus()
        _BUSES[meeting_id] = bus
    return bus


def publish_segment(meeting_id: str, seg: dict) -> None:
    """Publish one live segment to a meeting's bus (used by the ingest endpoint
    and directly by any in-process producer)."""
    _bus(meeting_id).publish(seg)


async def subscribe(meeting_id: str) -> AsyncIterator[dict]:
    """Server-side live feed — the seam an agent consumes to act on the meeting
    as it happens. Yields recent history first, then new segments as they land."""
    bus = _bus(meeting_id)
    for seg in list(bus.ring):
        yield seg
    q = bus.subscribe()
    try:
        while True:
            yield await q.get()
    finally:
        bus.unsubscribe(q)


# ── Ingest (producer → bus) ──────────────────────────────────────────────────

#: The setting that holds the shared secret of the meeting-bot worker.
BOT_TOKEN_ENV = "MEETING_BOT_TOKEN"

#: True after the first refusal for an unset token logged its line.
_unset_logged = False


def _bot_token() -> str:
    return os.environ.get(BOT_TOKEN_ENV, "").strip()


def _check_bot_auth(authorization: str | None) -> None:
    """The ONE check of the worker's bot token (task 4113).

    Each machine route of the worker calls it first: ``/live/segment``,
    ``/live/wanted`` and ``/stt/bot-live-token``. It fails closed.

    * An unset token answers 503 and names the setting, the shape of
      ``operator._require_operator_token``. The first refusal logs one line
      with the setting name, never a value.
    * A missing or wrong bearer answers 401. The compare is constant-time.

    The env is read on each call, so a rotation needs no restart."""
    global _unset_logged
    expected = _bot_token()
    if not expected:
        if not _unset_logged:
            _unset_logged = True
            _log.warning("notes.bot_token.not_configured", setting=BOT_TOKEN_ENV)
        raise HTTPException(
            status_code=503, detail=f"{BOT_TOKEN_ENV} is not configured"
        )
    presented = (authorization or "").encode("utf-8")
    if not hmac.compare_digest(presented, f"Bearer {expected}".encode()):
        raise HTTPException(status_code=401, detail="bad bot token")


def _resolve_and_publish(meeting_id: str, seg: LiveSegment) -> dict:
    """Resolve a stable speaker identity for one live segment and fan it out.

    Shared by both producers (bot worker, browser recorder) so the bus is fed
    identically regardless of source — the copilot consumes one seam."""
    data = seg.model_dump()
    data["ts"] = time.time()
    # Resolve a stable speaker id from the running gallery and bind names live
    # from self-introductions, before fan-out. Fail-safe: any error leaves the
    # segment with its original label rather than dropping it.
    try:
        from gateway.routes.notes.live_speakers import registry

        reg = registry(meeting_id)
        resolved = reg.resolve(data.get("embedding"),
                               fallback_label=data.get("speaker_label"))
        data["speaker_id"] = resolved.speaker_id
        data["speaker_label"] = resolved.speaker_id
        reg.note_text(resolved.speaker_id, data.get("text") or "")
        # Remember who held the floor when — the batch pass reconciles its own
        # labels against this timeline on stop to carry live names across.
        reg.note_span(
            resolved.speaker_id,
            float(data.get("start_s") or 0.0),
            float(data.get("end_s") or 0.0),
        )
        name = reg.name_of(resolved.speaker_id)
        if name:
            data["speaker_name"] = name
        # Feed agenda coverage from the bus, not from the copilot — so "you
        # haven't reached pricing yet" works whether or not the copilot is on.
        from gateway.routes.notes import agenda_progress

        agenda_progress.note(meeting_id, data.get("text") or "")
        if resolved.role:
            data["role"] = resolved.role
    except Exception as exc:
        _log.warning(
            "notes.live_speaker_resolve_failed",
            meeting_id=meeting_id, error=str(exc)[:200],
        )
    finally:
        data.pop("embedding", None)  # never fan out big vectors downstream
    publish_segment(meeting_id, data)
    return {"ok": True}


@router.post("/meetings/{meeting_id}/live/segment", status_code=202)
async def ingest_live_segment(
    meeting_id: str,
    seg: LiveSegment,
    authorization: str | None = Header(default=None),
) -> dict:
    """Producer callback: the meeting-bot worker's streaming ASR posts a live
    segment here as words are recognised (machine-authed)."""
    _check_bot_auth(authorization)
    return _resolve_and_publish(meeting_id, seg)


@router.post("/meetings/{meeting_id}/live/browser-segment", status_code=202)
async def ingest_browser_segment(
    meeting_id: str,
    seg: LiveSegment,
    user: UserContext = Depends(get_current_user),
) -> dict:
    """The in-browser recorder's finalized captions → the SAME bus as the bot.

    Makes the live path source-agnostic (copilot spec §3): whether audio came
    from a headless bot or the user's own mic, agents and the console consume
    one seam. User-authenticated (the browser has a session, not the bot token);
    browser captions carry no voiceprint, so the registry passes their diarized
    label through — live name binding from self-intros still applies.

    Owner only (task 3966): a segment from another member would write into
    the owner's live captions, roster, agenda coverage and copilot."""
    await require_owned_meeting(meeting_id, user.email)
    return _resolve_and_publish(meeting_id, seg)


@router.get("/meetings/{meeting_id}/live/roster")
async def live_roster(
    meeting_id: str,
    user: UserContext = Depends(get_current_user),
) -> dict:
    """Who's on the call so far — stable speaker ids with any live-bound names/
    roles. The identity context the copilot console (and agent) read.

    Owner only, checked before the in-process registry is read (task 3966)."""
    from gateway.routes.notes.live_speakers import registry

    await require_owned_meeting(meeting_id, user.email)
    return {"speakers": registry(meeting_id).roster()}


# ── Live captions stream (bus → UI) ──────────────────────────────────────────

async def _sse(meeting_id: str) -> AsyncIterator[bytes]:
    yield b": connected\n\n"
    # Streams until the client disconnects (StreamingResponse cancels this
    # generator) — the browser closes it when leaving the meeting view.
    async for seg in subscribe(meeting_id):
        yield f"data: {json.dumps(seg, default=str)}\n\n".encode()


@router.get("/meetings/{meeting_id}/live")
async def live_stream(
    meeting_id: str,
    user: UserContext = Depends(get_current_user),
) -> StreamingResponse:
    """SSE of the meeting's live transcript (captions while a bot is in-call).

    Owner only, and checked BEFORE the response starts (task 3966): the bus
    replays its ring of recent segments to each new subscriber."""
    await require_owned_meeting(meeting_id, user.email)
    return StreamingResponse(
        _sse(meeting_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ── Interjection (agent/user → back into the meeting) ────────────────────────

class SayRequest(BaseModel):
    text: str


@router.post("/meetings/{meeting_id}/say", status_code=202)
async def say_into_meeting(
    meeting_id: str,
    body: SayRequest,
    user: UserContext = Depends(get_current_user),
) -> dict:
    """Have the notetaker speak a line into the live call — the seam for
    agent-driven interjections. Forwards to the bot worker's ``/say``; only the
    self-hosted worker supports speaking back (managed Recall bots don't)."""
    text_ = (body.text or "").strip()
    if not text_:
        raise HTTPException(status_code=400, detail="empty text")
    # Owner only (task 3966): the bot speaks into the owner's call.
    async with _tenant_session() as db:
        await load_owned_meeting(db, meeting_id, user.email, columns="m.id")
        row = (
            await db.execute(
                text(
                    "SELECT provider, provider_bot_id, status FROM meeting_bot "
                    "WHERE meeting_id=:id ORDER BY created_at DESC LIMIT 1"
                ),
                {"id": meeting_id},
            )
        ).fetchone()
    if row is None or not row.provider_bot_id:
        raise HTTPException(status_code=404, detail="no active bot for this meeting")
    if row.provider != "selfhosted":
        raise HTTPException(
            status_code=409,
            detail="Speaking into the call needs the self-hosted worker.",
        )
    from gateway.routes.notes.meeting_bot import SelfHostedProvider

    try:
        await SelfHostedProvider().say(row.provider_bot_id, text_)
    except Exception as exc:
        _log.warning("notes.bot_say_failed", meeting_id=meeting_id, error=str(exc)[:200])
        raise HTTPException(status_code=502, detail="could not reach the bot worker") from exc
    return {"ok": True}
