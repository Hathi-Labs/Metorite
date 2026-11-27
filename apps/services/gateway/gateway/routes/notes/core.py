"""Note Taker routes — shared kernel.

The shared ``router``, Pydantic models, DB session factory and row→model
mappers used by the meetings/recordings/pipeline modules. Mirrors
``routes/tasks/core.py`` (the leaf module: it imports nothing from siblings).

Canonical store: the ``meeting`` / ``meeting_recording`` / ``transcript_segment``
/ ``meeting_note`` / ``summary_run`` / ``action_item`` tables
(``infra/postgres/01_schema.sql`` + ``95_note_taker.sql``; spec:
project-docs/specs/note_taker_app.md §3.6).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from acb_common import get_logger
from fastapi import APIRouter, HTTPException

# The shared gateway engine (BO-10) — see the DB section below.
from gateway.db import get_db as _get_db  # noqa: F401
from gateway.db import get_session_factory as _get_session_factory  # noqa: F401

# The shared seam (BO-10 → MT-1c/H2). `_tenant_session` IS
# `acb_common.db.tenant_session`, aliased per-package for the same reason
# `_get_db` was: every submodule imports it from here BY NAME, which is the
# seam the hermetic tests patch per module. The tenant comes from the request
# context — bound once in `_with_resolved_access` — so no call site passes
# one (H2). A call outside a bound request raises `TenantUnbound` rather than
# defaulting: fail closed, never "the usual org". `_get_db` above remains for
# the sites H2 cannot reach from a request: background jobs/pollers and the
# meeting-bot worker's service-identity paths (H4 threads their tenant).
from gateway.db import tenant_session as _tenant_session  # noqa: F401
from pydantic import BaseModel
from sqlalchemy import text
from acb_auth import require_feature_router

_log = get_logger("gateway.notes")

router = APIRouter(
    prefix="/notes", tags=["notes"],
    # Exempt: the meeting-bot worker's callbacks, machine-authed by
    # MEETING_BOT_TOKEN (live_transcript._check_bot_auth). The browser
    # recorder's twin route (/live/browser-segment) is user-authed and stays
    # gated — that split is the point.
    dependencies=[require_feature_router("notes", exempt=[
        "/notes/meetings/{meeting_id}/live/segment",
        "/notes/stt/bot-live-token",
    ])],
)


# ── Models (snake_case — same convention as tasks) ───────────────────────────

class SegmentModel(BaseModel):
    id: str
    idx: int
    start_s: float
    end_s: float
    text: str
    speaker_label: str | None = None
    channel: str | None = None
    confidence: float | None = None


class RecordingModel(BaseModel):
    id: str
    channel: str
    mime: str
    duration_s: float | None = None
    byte_size: int = 0
    created_at: str | None = None


class SummaryRunModel(BaseModel):
    id: str
    kind: str
    status: str
    stage: str | None = None
    chunk_done: int = 0
    chunk_total: int = 0
    model: str | None = None
    error: str | None = None
    created_at: str | None = None
    finished_at: str | None = None


class MeetingListItem(BaseModel):
    id: str
    title: str | None = None
    platform: str
    status: str
    language: str | None = None
    duration_s: float | None = None
    segment_count: int = 0
    has_notes: bool = False
    owner_email: str | None = None
    template_key: str | None = None
    start_at: str | None = None
    created_at: str | None = None
    #: When this meeting is planned for. Distinct from start_at, which is when
    #: capture actually began — an unstarted meeting has one but not the other.
    scheduled_at: str | None = None
    #: Per-meeting copilot decision. None = follow the account default.
    copilot_enabled: bool | None = None
    # ── What the library bands on ──────────────────────────────────────────
    #: Action items still awaiting approval — the "needs you" signal.
    pending_actions: int = 0
    action_count: int = 0
    agenda_count: int = 0
    has_brief: bool = False
    attendee_count: int = 0
    #: A session is running right now (the bot is in a call, or a mic is open).
    is_live: bool = False


class Attendee(BaseModel):
    name: str = ""
    email: str = ""


class MeetingDetail(MeetingListItem):
    transcript_source: str | None = None
    summary_md: str | None = None
    # Structured notes (decisions/action_items carry `refs` = source segment
    # indices) — powers tap-to-verify provenance in the UI.
    summary_json: dict | None = None
    scratch_notes: str | None = None
    attendees: list[Attendee] = []
    # Human names for diarized speaker labels, {"S1": "Alex Rivera", …}.
    speaker_names: dict[str, str] = {}
    recordings: list[RecordingModel] = []
    segments: list[SegmentModel] = []
    runs: list[SummaryRunModel] = []


class CreateMeetingRequest(BaseModel):
    title: str | None = None
    platform: str = "upload"        # 'in_person' once the recorder ships
    template_key: str | None = None
    scheduled_at: str | None = None


class PatchMeetingRequest(BaseModel):
    title: str | None = None
    template_key: str | None = None
    scheduled_at: str | None = None
    #: Tri-state on the wire too: omit to leave the decision alone, send
    #: true/false to pin it. There is no way to clear it back to "use the
    #: default" yet — nothing needs that, and inventing a sentinel for it now
    #: would be guessing at the shape.
    copilot_enabled: bool | None = None


# ── DB (the one shared gateway engine — gateway/db.py, BO-10) ────────────────
#
# This package used to build its own engine here with its own 5+10 pool. It now
# has none: `_get_db` / `_get_session_factory` at the top of this module are
# re-exports of the shared seam. The private names are kept so that every
# `from .core import _get_db` in this package — and every test that
# monkeypatches `_get_db` on the sibling module it is imported into — keeps
# working unchanged.


# ── Ownership — one predicate, one loader ────────────────────────────────────

#: The owner scope, as SQL, written ONCE. Every by-id endpoint that must not
#: reach a colleague's meeting binds this rather than spelling the comparison
#: out, so the two halves of the rule cannot drift apart:
#:
#: * ``lower(...) = lower(...)`` because ``owner_email`` is stamped verbatim
#:   from ``X-User-Email`` and an IdP may return the same UPN with different
#:   casing between sessions (Entra ID does). Byte equality would fail closed —
#:   a member's own library would silently empty — which is the "locked out of
#:   my own work" shape, not a leak, but it is still a defect.
#: * ``IS NULL`` stays visible to everyone: those are pre-migration-95 legacy
#:   rows (the column arrived nullable). Both insert paths stamp an owner, so
#:   the set cannot grow, and excluding them would hide a member's own old
#:   meetings rather than protect anybody.
#:
#: Requires the ``meeting`` table to be aliased ``m`` and binds ``:owner``.
OWNED_MEETING_PREDICATE = (
    "(lower(m.owner_email) = lower(:owner) OR m.owner_email IS NULL)"
)


async def load_owned_meeting(
    db, meeting_id: str, owner_email: str | None, *, columns: str = "m.*"
):
    """The meeting — if it belongs to ``owner_email``. Raises 404 otherwise.

    404 and not 403, deliberately: "that one exists but is not yours" confirms
    a meeting id to somebody who guessed it, and the two answers have to be
    indistinguishable in one place rather than consistently in a dozen.
    """
    row = (
        await db.execute(
            text(
                f"SELECT {columns} FROM meeting m "
                f"WHERE m.id = :id AND {OWNED_MEETING_PREDICATE}"
            ),
            {"id": meeting_id, "owner": owner_email or ""},
        )
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="meeting not found")
    return row


async def require_owned_meeting(meeting_id: str, owner_email: str | None) -> None:
    """Raise the 404 of :func:`load_owned_meeting` unless the meeting is the
    caller's. Opens its own tenant-bound session.

    For a route that reads no meeting row itself: a stream, an in-process map
    keyed by the meeting id (the live bus, the roster, agenda coverage), or a
    call to the bot worker. Call it BEFORE the response starts. A 404 raised
    inside a started ``StreamingResponse`` arrives as a broken stream, not as
    a refusal (task 3966).
    """
    async with _tenant_session() as db:
        await load_owned_meeting(db, meeting_id, owner_email, columns="m.id")


# ── Media storage (same recipe as tasks/attachments.py) ──────────────────────

def media_dir() -> Path:
    d = Path(os.environ.get("NOTES_MEDIA_DIR", "data/notes_media"))
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── Row → model mappers ──────────────────────────────────────────────────────

def _iso(v: Any) -> str | None:
    return v.isoformat() if v is not None else None


def row_to_segment(r: Any) -> SegmentModel:
    return SegmentModel(
        id=str(r.id), idx=r.idx, start_s=r.start_s or 0.0, end_s=r.end_s or 0.0,
        text=r.text or "", speaker_label=r.speaker_label, channel=r.channel,
        confidence=r.confidence,
    )


def row_to_recording(r: Any) -> RecordingModel:
    return RecordingModel(
        id=str(r.id), channel=r.channel, mime=r.mime, duration_s=r.duration_s,
        byte_size=r.byte_size or 0, created_at=_iso(r.created_at),
    )


def row_to_run(r: Any) -> SummaryRunModel:
    return SummaryRunModel(
        id=str(r.id), kind=r.kind, status=r.status, stage=r.stage,
        chunk_done=r.chunk_done or 0, chunk_total=r.chunk_total or 0,
        model=r.model, error=r.error, created_at=_iso(r.created_at),
        finished_at=_iso(r.finished_at),
    )


def row_to_list_item(r: Any) -> MeetingListItem:
    return MeetingListItem(
        id=str(r.id), title=r.title, platform=r.platform, status=r.status,
        language=r.language, duration_s=r.duration_s,
        segment_count=getattr(r, "segment_count", 0) or 0,
        has_notes=bool(getattr(r, "has_notes", False)),
        owner_email=r.owner_email,
        template_key=getattr(r, "template_key", None),
        start_at=_iso(r.start_at),
        created_at=_iso(r.created_at),
        scheduled_at=_iso(getattr(r, "scheduled_at", None)),
        copilot_enabled=getattr(r, "copilot_enabled", None),
        pending_actions=getattr(r, "pending_actions", 0) or 0,
        action_count=getattr(r, "action_count", 0) or 0,
        agenda_count=getattr(r, "agenda_count", 0) or 0,
        has_brief=bool(getattr(r, "has_brief", False)),
        attendee_count=getattr(r, "attendee_count", 0) or 0,
        is_live=bool(getattr(r, "is_live", False)),
    )
