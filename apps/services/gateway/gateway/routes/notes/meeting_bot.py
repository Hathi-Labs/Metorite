"""Meeting bot — send a notetaker to join a live Meet/Teams/Zoom call.

Spec §3.13 / decision D10. A pluggable *provider* runs the actual headless
participant. Two are built:

- ``selfhosted`` (**default**, fully in-house): talks to our own worker service
  (``apps/services/meeting-bot`` — headless Chrome via Playwright) over a small
  vendor-neutral HTTP contract. No third-party cloud, no per-hour fee; the cost
  is the box the worker runs on (needs real RAM/CPU — one Chrome per meeting).
- ``recall`` (optional managed fallback): the Recall.ai API, for anyone who'd
  rather not run the worker.

The flow (identical for both):

    paste URL → create meeting + meeting_bot row → provider.join()
      → poll status (joining → waiting room → in call → done)
      → on done: download the bot's audio → ingest as a normal
        `meeting_recording` → run the EXISTING pipeline (transcribe →
        diarize → auto-name speakers → summary → actions).

So a bot recording is just another audio source; everything downstream is
unchanged. Because each bot is an independent server-side job, one user can fan
several notetakers out to concurrent meetings.

Config (all via env; the feature is inert but safe until configured):
- ``NOTES_BOT_PROVIDER`` — ``selfhosted`` (default) or ``recall``.
- ``NOTES_BOT_NAME``     — default display name for the bot ("AI Notetaker").
- self-hosted: ``MEETING_BOT_URL`` — the worker's base URL (required to enable);
                ``MEETING_BOT_TOKEN`` — optional bearer for the worker.
- recall:      ``RECALL_API_KEY`` (required); ``RECALL_REGION`` (default
                ``us-east-1``) or ``RECALL_BASE_URL``.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from urllib.parse import urlparse

import httpx
from acb_auth import UserContext, UserRole, get_current_user, require_role
from fastapi import Depends, HTTPException, Response
from gateway.routes.notes.core import (
    OWNED_MEETING_PREDICATE,
    _get_db,
    _log,
    _tenant_session,
    media_dir,
    router,
)
from gateway.routes.notes.pipeline import run_transcription
from pydantic import BaseModel
from sqlalchemy import bindparam, text

# Keep strong refs to poller/pipeline tasks (a bare create_task() can be GC'd).
_TASKS: set[asyncio.Task] = set()

# Recall status codes we care about → our compact lifecycle. Anything unknown
# maps to None (leave status unchanged). See Recall bot `status_changes[].code`.
_RECALL_STATUS = {
    "ready": "joining",
    "joining_call": "joining",
    "in_waiting_room": "waiting_room",
    "in_call_not_recording": "in_call",
    "recording_permission_allowed": "in_call",
    "in_call_recording": "in_call",
    "call_ended": "processing",
    "recording_done": "processing",
    "done": "done",
    "analysis_done": "done",
    "fatal": "failed",
    "recording_permission_denied": "not_admitted",
    "call_join_failed": "failed",
}

# Bot statuses that are still running (drive polling + the "active" surface).
ACTIVE_STATUSES = ("requested", "joining", "waiting_room", "in_call", "processing")
# Every valid lifecycle status (a self-hosted worker reports these directly).
_ALL_STATUSES = (*ACTIVE_STATUSES, "done", "failed", "left", "not_admitted")
_AUDIO_EXT = {
    "audio/mp4": ".m4a", "audio/x-m4a": ".m4a", "audio/mpeg": ".mp3",
    "audio/webm": ".webm", "audio/ogg": ".ogg", "audio/wav": ".wav",
    "video/mp4": ".mp4", "video/webm": ".webm",
}


def _spawn(coro) -> None:
    task = asyncio.create_task(coro)
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)


# ── Pure helpers (unit-tested without network/DB) ────────────────────────────

def detect_platform(url: str) -> str:
    """Map a meeting URL to the platform tag used by `meeting.platform`."""
    host = (urlparse(url).hostname or "").lower()
    if "meet.google.com" in host:
        return "meet"
    if "zoom.us" in host or "zoom.com" in host:
        return "zoom"
    if "teams.microsoft.com" in host or "teams.live.com" in host:
        return "teams"
    return "other"


def is_supported_url(url: str) -> bool:
    """A plausible https meeting link (host present). Kept permissive — Recall
    supports more platforms than we tag; we just won't pretty-label those."""
    try:
        p = urlparse(url)
    except (ValueError, TypeError):
        return False
    return p.scheme in ("http", "https") and bool(p.hostname)


#: Platforms the SELF-HOSTED worker can actually drive. Its join flow is written
#: against Google Meet's DOM; Zoom and Teams need their own automation (or their
#: SDKs) and are not built. Recall, being a managed provider, handles all three.
SELFHOSTED_PLATFORMS = ("meet",)

#: What to tell someone whose link we can't join, per platform. The alternative
#: matters: both Zoom and Teams can record locally, and an upload runs the exact
#: same transcribe → notes → action-item pipeline as a bot recording.
_UNSUPPORTED_HELP = {
    "zoom": "Zoom",
    "teams": "Microsoft Teams",
    "other": "That link's platform",
}


def unsupported_platform_message(platform: str) -> str:
    what = _UNSUPPORTED_HELP.get(platform, _UNSUPPORTED_HELP["other"])
    return (
        f"{what} isn't supported by the self-hosted notetaker yet — it can only "
        "join Google Meet. Record the call with the platform's own recorder and "
        "upload the file here (you still get the transcript, notes and action "
        "items), or use Record for an in-person meeting."
    )


def normalize_status(recall_code: str | None) -> str | None:
    """Recall status code → our lifecycle status (None = unknown, leave as-is)."""
    if not recall_code:
        return None
    return _RECALL_STATUS.get(recall_code)


def extract_download_url(bot: dict) -> str | None:
    """Best-effort pull of a media download URL from a Recall bot object.

    Recall's shape has shifted across API versions, so we probe the known
    locations (prefer mixed audio, then mixed video, then legacy top-level
    fields). Isolated + pure so it's easy to adjust against the live API."""
    if not isinstance(bot, dict):
        return None
    recordings = bot.get("recordings")
    if isinstance(recordings, list):
        for rec in recordings:
            shortcuts = (rec or {}).get("media_shortcuts") or {}
            for key in ("audio_mixed", "video_mixed"):
                node = shortcuts.get(key) or {}
                data = node.get("data") or {}
                url = data.get("download_url") or node.get("download_url")
                if url:
                    return url
    # Legacy top-level fields.
    for key in ("audio_url", "video_url", "media_url"):
        if bot.get(key):
            return bot[key]
    return None


def latest_status_code(bot: dict) -> str | None:
    """The most recent status_changes[].code (or top-level status_code)."""
    changes = bot.get("status_changes") if isinstance(bot, dict) else None
    if isinstance(changes, list) and changes:
        last = changes[-1]
        if isinstance(last, dict):
            return last.get("code")
    status = bot.get("status") if isinstance(bot, dict) else None
    if isinstance(status, dict):
        return status.get("code")
    return status if isinstance(status, str) else None


# ── Provider config + Recall client ──────────────────────────────────────────

def _provider_name() -> str:
    # Default to the fully in-house worker; 'recall' remains an optional managed
    # fallback for anyone who'd rather not run the worker.
    return os.environ.get("NOTES_BOT_PROVIDER", "selfhosted").strip().lower()


def _recall_key() -> str:
    return os.environ.get("RECALL_API_KEY", "").strip()


def _recall_base() -> str:
    base = os.environ.get("RECALL_BASE_URL", "").strip().rstrip("/")
    if base:
        return base
    region = os.environ.get("RECALL_REGION", "us-east-1").strip() or "us-east-1"
    return f"https://{region}.recall.ai/api/v1"


def _selfhosted_url() -> str:
    """Base URL of the self-hosted meeting-bot worker (apps/services/meeting-bot)."""
    return os.environ.get("MEETING_BOT_URL", "").strip().rstrip("/")


def _selfhosted_token() -> str:
    return os.environ.get("MEETING_BOT_TOKEN", "").strip()


def default_bot_name() -> str:
    return os.environ.get("NOTES_BOT_NAME", "AI Notetaker").strip() or "AI Notetaker"


def bot_configured() -> bool:
    """True when a provider is actually usable.

    - ``selfhosted`` (default goal — fully in-house): needs the worker URL.
    - ``recall`` (optional managed fallback): needs the API key."""
    provider = _provider_name()
    if provider == "selfhosted":
        return bool(_selfhosted_url())
    return provider == "recall" and bool(_recall_key())


class RecallProvider:
    """Thin async client over the Recall.ai bot API. All calls raise on HTTP
    error; callers wrap them so a provider hiccup never crashes the poller."""

    def __init__(self) -> None:
        self._base = _recall_base()
        self._headers = {
            "Authorization": f"Token {_recall_key()}",
            "Content-Type": "application/json",
        }

    async def join(
        self, meeting_url: str, bot_name: str, live_callback: str | None = None
    ) -> str:
        """Create a bot in the call; returns the provider bot id. (Recall doesn't
        do our live-callback streaming, so ``live_callback`` is ignored.)"""
        body: dict = {"meeting_url": meeting_url, "bot_name": bot_name}
        raw = os.environ.get("RECALL_RECORDING_CONFIG", "").strip()
        if raw:
            import contextlib
            import json as _json

            with contextlib.suppress(ValueError):
                body["recording_config"] = _json.loads(raw)
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{self._base}/bot/", headers=self._headers, json=body
            )
            resp.raise_for_status()
            data = resp.json()
        bot_id = data.get("id") or data.get("bot_id")
        if not bot_id:
            raise RuntimeError("Recall did not return a bot id")
        return str(bot_id)

    async def fetch(self, bot_id: str) -> dict:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{self._base}/bot/{bot_id}/", headers=self._headers
            )
            resp.raise_for_status()
            return resp.json()

    async def status(self, bot_id: str) -> tuple[str | None, str | None, str | None]:
        """Provider-agnostic (lifecycle_status, download_url|None, error|None)
        for the poller. Recall doesn't surface a failure explanation here."""
        bot = await self.fetch(bot_id)
        return (
            normalize_status(latest_status_code(bot)),
            extract_download_url(bot),
            None,
        )

    async def leave(self, bot_id: str) -> None:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{self._base}/bot/{bot_id}/leave_call/", headers=self._headers
            )
            # 200/202 expected; a already-gone bot may 404 — tolerate.
            if resp.status_code not in (200, 202, 204, 404):
                resp.raise_for_status()

    async def download(self, url: str) -> tuple[bytes, str]:
        """Fetch the recording bytes + content-type from a (signed) media URL."""
        async with httpx.AsyncClient(timeout=600, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            ctype = (resp.headers.get("content-type") or "").split(";")[0].strip()
            return resp.content, ctype


class SelfHostedProvider:
    """Client for a *self-hosted* meeting-bot worker — a headless Chrome that
    joins the call, running on our own box (apps/services/meeting-bot). Fully
    in-house: no third-party cloud, no per-hour fee. Speaks a small
    vendor-neutral HTTP contract, and the worker already reports our lifecycle
    vocabulary so no per-provider status translation is needed.

    Contract:
      POST   {base}/bots                {meeting_url, bot_name} -> {id, status}
      GET    {base}/bots/{id}           -> {id, status, download_url|null}
      POST   {base}/bots/{id}/leave     -> 202
      GET    {base}/bots/{id}/recording -> audio bytes (when status == done)
    """

    def __init__(self) -> None:
        self._base = _selfhosted_url()
        self._headers = {"Content-Type": "application/json"}
        tok = _selfhosted_token()
        if tok:
            self._headers["Authorization"] = f"Bearer {tok}"

    async def join(
        self, meeting_url: str, bot_name: str, live_callback: str | None = None
    ) -> str:
        body: dict = {"meeting_url": meeting_url, "bot_name": bot_name}
        # Tell the worker where to POST live transcript segments (per meeting) so
        # the UI + agents get a live feed. Omitted → the worker just records.
        if live_callback:
            body["live_callback"] = live_callback
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{self._base}/bots", headers=self._headers, json=body
            )
            resp.raise_for_status()
            data = resp.json()
        bot_id = data.get("id")
        if not bot_id:
            raise RuntimeError("meeting-bot worker did not return a bot id")
        return str(bot_id)

    async def status(self, bot_id: str) -> tuple[str | None, str | None, str | None]:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{self._base}/bots/{bot_id}", headers=self._headers
            )
            resp.raise_for_status()
            data = resp.json()
        status = data.get("status")
        status = status if status in _ALL_STATUSES else None
        download = data.get("download_url") or None
        # Worker may serve the file at a stable path instead of returning a URL.
        if status == "done" and not download:
            download = f"{self._base}/bots/{bot_id}/recording"
        # The worker's own explanation of a failed join ("nobody admitted…",
        # "Meet showed its landing page…") — the only text that tells a human
        # what to do differently, so it must reach the DB row the UI renders.
        error = data.get("error") or None
        return status, download, str(error)[:800] if error else None

    async def leave(self, bot_id: str) -> None:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{self._base}/bots/{bot_id}/leave", headers=self._headers
            )
            if resp.status_code not in (200, 202, 204, 404):
                resp.raise_for_status()

    async def say(self, bot_id: str, text: str) -> None:
        """Have the worker speak a line into the live call (TTS → virtual mic).
        The seam for agent-driven interjections; only the self-hosted worker
        can do this."""
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{self._base}/bots/{bot_id}/say",
                headers=self._headers,
                json={"text": text},
            )
            resp.raise_for_status()

    async def diagnostics(self, bot_id: str) -> dict:
        """What the worker's browser saw when a join failed. Meet's DOM isn't a
        public API, so when a selector misses this is the only way to know which
        one to write next."""
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{self._base}/bots/{bot_id}/diagnostics", headers=self._headers
            )
            resp.raise_for_status()
            return resp.json()

    async def screenshot(self, bot_id: str) -> bytes:
        """The page as the bot saw it (PNG) — the fastest way to tell a waiting
        room from a sign-in wall from Meet's marketing page."""
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{self._base}/bots/{bot_id}/screenshot", headers=self._headers
            )
            resp.raise_for_status()
            return resp.content

    async def download(self, url: str) -> tuple[bytes, str]:
        async with httpx.AsyncClient(timeout=600, follow_redirects=True) as client:
            resp = await client.get(url, headers=self._headers)
            resp.raise_for_status()
            ctype = (resp.headers.get("content-type") or "").split(";")[0].strip()
            return resp.content, ctype

    # ── Bot identity (Google sign-in) ────────────────────────────────────────
    # Google auto-declines ANONYMOUS participants, so an unattended join needs
    # the worker's browser signed into a real account. That sign-in lived only
    # behind SSH + curl on the worker's loopback port; these three calls put it
    # on an authenticated app surface instead.

    async def identity(self) -> dict:
        """Who the bot's browser is signed in as (never includes a password)."""
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.get(
                f"{self._base}/google-login", headers=self._headers
            )
            resp.raise_for_status()
            return resp.json()

    async def identity_sign_in(self, email: str, password: str) -> dict:
        """Scripted sign-in. Slow by nature (a real browser drives Google's
        login), and 400s carry the diagnostics that name which wall it hit."""
        async with httpx.AsyncClient(timeout=180) as client:
            resp = await client.post(
                f"{self._base}/google-login",
                headers=self._headers,
                json={"email": email, "password": password},
            )
            resp.raise_for_status()
            return resp.json()

    async def identity_interactive(self) -> dict:
        """Hold Google's sign-in page open in the worker's browser so a human can
        finish 2FA / 'verify it's you' over VNC."""
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{self._base}/google-login/interactive", headers=self._headers
            )
            resp.raise_for_status()
            return resp.json()


def resolve_bot_provider() -> RecallProvider | SelfHostedProvider | None:
    if not bot_configured():
        return None
    if _provider_name() == "selfhosted":
        return SelfHostedProvider()
    return RecallProvider()


# ── DB helpers + ingest ──────────────────────────────────────────────────────

async def _set_bot(db, bot_id: str, **fields) -> None:
    assigns = ", ".join(f"{k} = :{k}" for k in fields)
    assigns = f"{assigns}, updated_at = now()" if assigns else "updated_at = now()"
    await db.execute(
        text(f"UPDATE meeting_bot SET {assigns} WHERE id = :id"),
        {**fields, "id": bot_id},
    )


async def _ingest_recording(
    meeting_id: str, audio: bytes, ctype: str, requested_by: str
) -> None:
    """Save the bot's audio as a `meeting_recording` and run the pipeline —
    identical to an upload, so diarization/speaker-naming/summary all apply.

    ``requested_by`` is ``meeting_bot.requested_by`` — the member who sent the
    notetaker. Nobody clicks this ingest (the poller finds the recording
    ready), but it still has a person behind it, and the pipeline chains into
    notes generation and then ``auto_dispatch``, which can send mail from the
    meeting owner's mailbox. So the requester is what travels down, not the
    meeting's owner: ``bot_join`` will attach a bot to a meeting the caller
    does not own (see the gateway AGENTS.md list), and passing the owner there
    would launder one member's request into another member's authority.
    """
    ext = _AUDIO_EXT.get(ctype, ".mp4")
    mime = ctype or "audio/mp4"
    recording_id = str(uuid.uuid4())
    rel_path = f"{meeting_id}/{recording_id}{ext}"
    dest = media_dir() / rel_path
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(audio)

    # H4: reached from `_refresh_bot` (background poller / poll-on-read) — no
    # ambient tenant; derive it from the meeting_bot row (`requested_by`).
    async with await _get_db() as db:
        await db.execute(
            text(
                "INSERT INTO meeting_recording (id, meeting_id, channel, "
                "artifact_path, mime, byte_size) VALUES "
                "(:id, :mid, 'mixed', :path, :mime, :size)"
            ),
            {"id": recording_id, "mid": meeting_id, "path": rel_path,
             "mime": mime, "size": len(audio)},
        )
        run_row = (
            await db.execute(
                text(
                    "INSERT INTO summary_run (meeting_id, kind, status, stage) "
                    "VALUES (:mid, 'transcribe', 'queued', 'queued') RETURNING id"
                ),
                {"mid": meeting_id},
            )
        ).fetchone()
        await db.execute(
            text("UPDATE meeting SET status='processing', end_at=COALESCE(end_at, now()) "
                 "WHERE id=:id"),
            {"id": meeting_id},
        )
        await db.commit()
    _spawn(
        run_transcription(
            meeting_id, recording_id, str(run_row.id), requested_by or ""
        )
    )
    _log.info("notes.bot_ingested", meeting_id=meeting_id, bytes=len(audio), mime=mime)


async def _refresh_bot(bot_row_id: str) -> None:
    """Poll the provider once and advance the bot's state; ingest on completion.
    Idempotent + safe to call from both the background poller and poll-on-read;
    ingestion is claimed atomically so it happens exactly once. Never raises."""
    provider = resolve_bot_provider()
    if provider is None:
        return
    try:
        # H4: dual-entry — the background poller (`_poll_bot`) reaches this
        # with no ambient tenant, so every `_get_db` block in this function
        # stays unbound until H4 derives the tenant from the meeting_bot row.
        async with await _get_db() as db:
            row = (
                await db.execute(
                    text("SELECT id, meeting_id, provider_bot_id, status, "
                         "requested_by FROM meeting_bot WHERE id = :id"),
                    {"id": bot_row_id},
                )
            ).fetchone()
        if row is None or row.status not in ACTIVE_STATUSES or not row.provider_bot_id:
            return

        try:
            new_status, download_url, worker_error = await provider.status(
                row.provider_bot_id
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404:
                raise
            # The provider no longer knows this bot. The self-hosted worker
            # persists terminal state across restarts, so a 404 means even
            # that is gone (volume wiped / very old id) — fail the row
            # honestly instead of polling "joining" for four hours.
            async with await _get_db() as db:
                await _set_bot(
                    db, bot_row_id, status="failed",
                    error="The meeting-bot worker no longer knows this bot — "
                          "it was probably restarted (a deploy does this) "
                          "while the bot was in flight. Send the notetaker "
                          "again.",
                )
                await db.execute(
                    text("UPDATE meeting SET status='failed' WHERE id=:id"),
                    {"id": str(row.meeting_id)},
                )
                await db.commit()
            from gateway.routes.notes import live_session

            await live_session.end(str(row.meeting_id))
            return

        # Terminal success: call finished AND media is downloadable → claim the
        # ingest atomically (only one caller flips out of an active status).
        if new_status == "done" and download_url:
            async with await _get_db() as db:
                claim = (
                    await db.execute(
                        text(
                            "UPDATE meeting_bot SET status='done', updated_at=now() "
                            "WHERE id=:id AND status IN :active RETURNING id"
                        ).bindparams(bindparam("active", expanding=True)),
                        {"id": bot_row_id, "active": list(ACTIVE_STATUSES)},
                    )
                ).fetchone()
                await db.commit()
            if claim is None:
                return  # another caller is ingesting
            try:
                audio, ctype = await provider.download(download_url)
                await _ingest_recording(
                    str(row.meeting_id), audio, ctype,
                    getattr(row, "requested_by", None) or "",
                )
            except Exception as exc:  # ingest failed after claiming
                async with await _get_db() as db:
                    await _set_bot(db, bot_row_id, status="failed",
                                   error=f"ingest failed: {str(exc)[:400]}")
                    await db.execute(
                        text("UPDATE meeting SET status='failed' WHERE id=:id"),
                        {"id": str(row.meeting_id)},
                    )
                    await db.commit()
            return

        # Non-terminal (or terminal-without-media-yet): record status changes.
        if new_status and new_status != row.status and new_status != "done":
            async with await _get_db() as db:
                if new_status in ("failed", "not_admitted") and worker_error:
                    # Carry the worker's own explanation to the row the UI
                    # renders — "Failed" alone tells a human nothing.
                    await _set_bot(db, bot_row_id, status=new_status,
                                   error=worker_error)
                else:
                    await _set_bot(db, bot_row_id, status=new_status)
                if new_status in ("failed", "not_admitted"):
                    await db.execute(
                        text("UPDATE meeting SET status='failed' WHERE id=:id"),
                        {"id": str(row.meeting_id)},
                    )
                    # Never left the ground — clear presence (the pipeline that
                    # normally ends a session won't run without a recording).
                    from gateway.routes.notes import live_session

                    await live_session.end(str(row.meeting_id))
                elif new_status == "in_call":
                    await db.execute(
                        text("UPDATE meeting SET status='recording' "
                             "WHERE id=:id AND status='draft'"),
                        {"id": str(row.meeting_id)},
                    )
                await db.commit()
    except Exception as exc:
        _log.warning("notes.bot_refresh_failed", bot=bot_row_id, error=str(exc)[:200])


async def _poll_bot(bot_row_id: str) -> None:
    """Background loop: refresh a bot until it leaves the active set. Best-effort
    — a gateway restart drops it, but poll-on-read (`GET /notes/bots/active`)
    keeps the DB state advancing and still triggers ingest."""
    # ~4h ceiling at 15s cadence — long enough for any real meeting.
    for _ in range(960):
        await _refresh_bot(bot_row_id)
        # H4: background poller loop — no ambient tenant; see `_refresh_bot`.
        async with await _get_db() as db:
            row = (
                await db.execute(
                    text("SELECT status FROM meeting_bot WHERE id=:id"),
                    {"id": bot_row_id},
                )
            ).fetchone()
        if row is None or row.status not in ACTIVE_STATUSES:
            return
        await asyncio.sleep(15)


# ── API models ───────────────────────────────────────────────────────────────

class BotJoinRequest(BaseModel):
    meeting_url: str
    title: str | None = None
    bot_name: str | None = None
    #: Send the bot to a meeting you already prepared. Without this a fresh
    #: meeting is created, which would strand the agenda, briefing, attendees
    #: and copilot decision you just set up on a different row.
    meeting_id: str | None = None


class MeetingBotModel(BaseModel):
    id: str
    meeting_id: str
    status: str
    provider: str
    meeting_url: str
    bot_name: str | None = None
    error: str | None = None
    meeting_title: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


def _row_to_bot(r) -> MeetingBotModel:
    return MeetingBotModel(
        id=str(r.id), meeting_id=str(r.meeting_id), status=r.status,
        provider=r.provider, meeting_url=r.meeting_url, bot_name=r.bot_name,
        error=r.error,
        meeting_title=getattr(r, "meeting_title", None),
        created_at=r.created_at.isoformat() if r.created_at else None,
        updated_at=r.updated_at.isoformat() if getattr(r, "updated_at", None) else None,
    )


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.get("/bots/status")
async def bot_status(_user: UserContext = Depends(get_current_user)) -> dict:
    """Whether the meeting-bot feature is usable (a provider key is set)."""
    return {"configured": bot_configured(), "provider": _provider_name()}


@router.post("/meetings/bot-join", status_code=201)
async def bot_join(
    body: BotJoinRequest,
    user: UserContext = Depends(get_current_user),
) -> MeetingBotModel:
    """Send a notetaker bot to join a meeting URL. Creates the meeting + bot,
    dispatches the provider, and starts tracking. Call once per URL to fan out
    to multiple meetings concurrently.

    Two branches with different scopes, deliberately: creating a meeting is
    open to any ``feature:notes`` holder and stamps them as its owner;
    *attaching* to one that already exists is owner-only, because it is a
    write to somebody's prepared row — it flips the meeting to ``recording``,
    overwrites its title and start time, and puts a bot in the call."""
    url = (body.meeting_url or "").strip()
    if not is_supported_url(url):
        raise HTTPException(status_code=400, detail="Enter a valid meeting link (https://…).")
    provider = resolve_bot_provider()
    if provider is None:
        raise HTTPException(
            status_code=503,
            detail="The meeting notetaker isn't set up yet — an admin needs to "
                   "point it at a self-hosted meeting-bot worker (MEETING_BOT_URL) "
                   "or configure a provider key.",
        )
    platform = detect_platform(url)
    # Refuse a platform the worker can't drive, HERE, rather than dispatching a
    # bot that opens Zoom in a Meet-shaped automation and reports "no join
    # button". The self-hosted worker is Meet-only; Recall handles the rest.
    if (
        isinstance(provider, SelfHostedProvider)
        and platform not in SELFHOSTED_PLATFORMS
    ):
        raise HTTPException(
            status_code=400, detail=unsupported_platform_message(platform)
        )
    bot_name = (body.bot_name or "").strip() or default_bot_name()
    title = (body.title or "").strip() or None

    async with _tenant_session() as db:
        if body.meeting_id:
            # Attaching to a prepared meeting: keep its agenda/brief/attendees
            # and just mark it recording.
            #
            # Owner-scoped, and the scope is bound INTO the UPDATE rather than
            # checked by a preceding SELECT: a load-then-write leaves a window
            # in which the row can change owner between the two statements, and
            # this statement is the mutation — one statement, one decision.
            #
            # The principal is the CALLER (`user.email`), not the meeting's
            # owner. It has to be: the caller is the only identity this request
            # carries, and resolving the check against the row's own
            # `owner_email` would compare the meeting to itself and pass every
            # time. That is the same reason the ingest side reads
            # `meeting_bot.requested_by` — the member who sent the notetaker —
            # rather than the owner (PR #346): authority follows the person who
            # asked, and is never laundered through the row being acted on.
            # After this check the two are the same person for an attach, which
            # is the point; for the create branch below they already were.
            existing = (
                await db.execute(
                    text(
                        "UPDATE meeting AS m "
                        "SET status = 'recording', platform = :p, "
                        "start_at = now(), title = COALESCE(:t, title) "
                        "WHERE m.id = CAST(:id AS UUID) "
                        f"AND {OWNED_MEETING_PREDICATE} RETURNING m.id"
                    ),
                    {"p": platform, "t": title, "id": body.meeting_id,
                     "owner": user.email or ""},
                )
            ).fetchone()
            if existing is None:
                # Same answer for "no such meeting" and "not yours" — an id
                # that belongs to a colleague must not be confirmable.
                raise HTTPException(status_code=404, detail="unknown meeting")
            meeting_id = str(existing.id)
        else:
            m = (
                await db.execute(
                    text(
                        "INSERT INTO meeting (platform, start_at, title, status, "
                        "owner_email) "
                        "VALUES (:p, now(), :t, 'recording', :o) RETURNING id"
                    ),
                    {"p": platform, "t": title, "o": user.email},
                )
            ).fetchone()
            meeting_id = str(m.id)
        bot_row = (
            await db.execute(
                text(
                    "INSERT INTO meeting_bot (meeting_id, provider, meeting_url, "
                    "bot_name, status, requested_by) VALUES "
                    "(:mid, :prov, :url, :name, 'requested', :by) RETURNING id"
                ),
                {"mid": meeting_id, "prov": _provider_name(), "url": url,
                 "name": bot_name, "by": user.email},
            )
        ).fetchone()
    bot_row_id = str(bot_row.id)

    # Where the worker posts live transcript segments for this meeting (enables
    # live captions + agent hooks). Only when a reachable gateway base is set.
    live_callback = None
    base = os.environ.get("NOTES_LIVE_CALLBACK_BASE", "").strip().rstrip("/")
    if base:
        live_callback = f"{base}/notes/meetings/{meeting_id}/live/segment"

    try:
        provider_bot_id = await provider.join(url, bot_name, live_callback=live_callback)
    except Exception as exc:
        # "The notetaker is already busy" is NOT a bad link, and telling someone
        # to check the link when the worker is at capacity sends them to debug
        # the wrong thing. Each bot is a whole Chrome, so one-at-a-time is the
        # normal state, not an edge case.
        busy = (
            isinstance(exc, httpx.HTTPStatusError)
            and exc.response.status_code == 409
        )
        if busy:
            try:
                detail = exc.response.json().get("detail") or ""
            except Exception:
                detail = ""
            message = (
                "The notetaker is already in another meeting — it can only "
                "attend one at a time. Wait for that call to end, or record "
                "this one and upload it."
            )
            error_text = f"notetaker busy: {detail}"[:400]
        else:
            message = "Couldn't dispatch the notetaker to that meeting. Check the link."
            error_text = f"join failed: {str(exc)[:400]}"
        async with _tenant_session() as db:
            await _set_bot(db, bot_row_id, status="failed", error=error_text)
            await db.execute(text("UPDATE meeting SET status='failed' WHERE id=:id"),
                             {"id": meeting_id})
        _log.warning("notes.bot_join_failed", meeting_id=meeting_id,
                     busy=busy, error=str(exc)[:200])
        raise HTTPException(
            status_code=409 if busy else 502, detail=message
        ) from None

    async with _tenant_session() as db:
        await _set_bot(db, bot_row_id, provider_bot_id=provider_bot_id, status="joining")
    # Register presence — a bot meeting shows as "live now" in Metorite
    # from the moment it's dispatched, not just once audio starts flowing.
    from gateway.routes.notes import live_session

    await live_session.begin(meeting_id, "bot", user.email)
    _log.info("notes.bot_joined", meeting_id=meeting_id, platform=platform)
    _spawn(_poll_bot(bot_row_id))

    return MeetingBotModel(
        id=bot_row_id, meeting_id=meeting_id, status="joining",
        provider=_provider_name(), meeting_url=url, bot_name=bot_name,
        meeting_title=title,
    )


# Running bots, PLUS ones that failed in the last half hour.
#
# A bot that fails leaves the active set instantly, so the surface the user was
# watching just empties — the single most confusing outcome, because "it didn't
# work" is indistinguishable from "nothing happened". Keeping recent failures
# visible is what makes the error text reachable at all.
_RECENT_FAILURE_WINDOW = "30 minutes"
_ACTIVE_SQL = (
    "SELECT b.*, m.title AS meeting_title FROM meeting_bot b "
    "JOIN meeting m ON m.id = b.meeting_id "
    "WHERE b.status IN :active "
    "   OR (b.status IN ('failed', 'not_admitted') "
    f"       AND b.updated_at > now() - interval '{_RECENT_FAILURE_WINDOW}') "
    "ORDER BY b.created_at DESC"
)


@router.get("/bots/active")
async def list_active_bots(
    _user: UserContext = Depends(get_current_user),
) -> list[MeetingBotModel]:
    """Active notetaker bots (for the live surface). Poll-on-read: refresh each
    from the provider so status advances (and completed calls ingest) even
    without the in-process poller."""
    async with _tenant_session() as db:
        rows = (
            await db.execute(
                text(_ACTIVE_SQL).bindparams(bindparam("active", expanding=True)),
                {"active": list(ACTIVE_STATUSES)},
            )
        ).fetchall()
    ids = [str(r.id) for r in rows]
    if ids:
        await asyncio.gather(*(_refresh_bot(i) for i in ids), return_exceptions=True)
        async with _tenant_session() as db:
            rows = (
                await db.execute(
                    text(_ACTIVE_SQL).bindparams(bindparam("active", expanding=True)),
                    {"active": list(ACTIVE_STATUSES)},
                )
            ).fetchall()
    return [_row_to_bot(r) for r in rows]


@router.get("/meetings/{meeting_id}/bot")
async def get_meeting_bot(
    meeting_id: str,
    _user: UserContext = Depends(get_current_user),
) -> MeetingBotModel | None:
    async with _tenant_session() as db:
        r = (
            await db.execute(
                text("SELECT b.*, m.title AS meeting_title FROM meeting_bot b "
                     "JOIN meeting m ON m.id=b.meeting_id WHERE b.meeting_id=:id "
                     "ORDER BY b.created_at DESC LIMIT 1"),
                {"id": meeting_id},
            )
        ).fetchone()
    if r is None:
        return None
    if r.status in ACTIVE_STATUSES:
        await _refresh_bot(str(r.id))
        async with _tenant_session() as db:
            r = (
                await db.execute(
                    text("SELECT b.*, m.title AS meeting_title FROM meeting_bot b "
                         "JOIN meeting m ON m.id=b.meeting_id WHERE b.id=:id"),
                    {"id": str(r.id)},
                )
            ).fetchone()
    return _row_to_bot(r)


@router.get("/meetings/{meeting_id}/bot/diagnostics")
async def meeting_bot_diagnostics(
    meeting_id: str,
    _user: UserContext = Depends(get_current_user),
) -> dict:
    """Why a notetaker couldn't join, in enough detail to act on.

    Browser automation against a UI that isn't a public API breaks in ways a
    status code can't express — a sign-in wall, a device dialog covering the
    green room and a host who never clicked Admit all end as "didn't join".
    This returns the page the bot actually saw."""
    async with _tenant_session() as db:
        r = (
            await db.execute(
                text("SELECT id, provider, provider_bot_id, status, error "
                     "FROM meeting_bot WHERE meeting_id=:id "
                     "ORDER BY created_at DESC LIMIT 1"),
                {"id": meeting_id},
            )
        ).fetchone()
    if r is None:
        raise HTTPException(status_code=404, detail="no bot for this meeting")
    out: dict = {"status": r.status, "error": r.error, "diagnostics": None}
    provider = resolve_bot_provider()
    # Only the self-hosted worker keeps page-level diagnostics; Recall doesn't.
    if isinstance(provider, SelfHostedProvider) and r.provider_bot_id:
        try:
            out["diagnostics"] = await provider.diagnostics(str(r.provider_bot_id))
        except Exception as exc:
            out["diagnostics_error"] = str(exc)[:200]
    return out


@router.get("/meetings/{meeting_id}/bot/screenshot")
async def meeting_bot_screenshot(
    meeting_id: str,
    _user: UserContext = Depends(get_current_user),
) -> Response:
    """The green room exactly as the bot saw it (PNG). 404 when no screenshot
    was captured (only failure paths snapshot the page)."""
    async with _tenant_session() as db:
        r = (
            await db.execute(
                text("SELECT provider_bot_id FROM meeting_bot "
                     "WHERE meeting_id=:id ORDER BY created_at DESC LIMIT 1"),
                {"id": meeting_id},
            )
        ).fetchone()
    provider = resolve_bot_provider()
    if (r is None or not r.provider_bot_id
            or not isinstance(provider, SelfHostedProvider)):
        raise HTTPException(status_code=404, detail="no screenshot")
    try:
        png = await provider.screenshot(str(r.provider_bot_id))
    except Exception as exc:
        raise HTTPException(status_code=404, detail="no screenshot") from exc
    return Response(content=png, media_type="image/png")


@router.post("/meetings/{meeting_id}/bot/stop", status_code=202)
async def stop_meeting_bot(
    meeting_id: str,
    _user: UserContext = Depends(get_current_user),
) -> dict:
    """Remove the notetaker from the call. Any audio captured so far is still
    processed by the provider and ingested when ready."""
    async with _tenant_session() as db:
        r = (
            await db.execute(
                text("SELECT id, provider_bot_id, status FROM meeting_bot "
                     "WHERE meeting_id=:id ORDER BY created_at DESC LIMIT 1"),
                {"id": meeting_id},
            )
        ).fetchone()
    if r is None:
        raise HTTPException(status_code=404, detail="no bot for this meeting")
    provider = resolve_bot_provider()
    if provider is not None and r.provider_bot_id and r.status in ACTIVE_STATUSES:
        try:
            await provider.leave(r.provider_bot_id)
        except Exception as exc:
            _log.warning("notes.bot_stop_failed", meeting_id=meeting_id, error=str(exc)[:200])
    async with _tenant_session() as db:
        await _set_bot(db, str(r.id), status="processing")
    # Kick one refresh so ingest happens promptly once the recording finalizes.
    _spawn(_poll_bot(str(r.id)))
    return {"ok": True, "status": "processing"}


# ── Bot identity ─────────────────────────────────────────────────────────────
# The notetaker's Google account is the difference between a bot Meet refuses as
# anonymous and one that walks straight in off a calendar invite. Configuring it
# used to mean SSH-ing to the box and curling a loopback port, which is not a
# thing anyone should have to do to set up a feature — and worse, it hid the
# *state* (is the bot signed in? as whom?) from the only screen that cares.
#
# Executive-only: this writes a credential that acts on the org's behalf, so it
# sits with the other credential-writing endpoints, not with per-user settings.
# The password is never stored here, never logged, and never returned.

class BotIdentityRequest(BaseModel):
    email: str
    password: str


def _identity_unavailable() -> HTTPException:
    return HTTPException(
        status_code=503,
        detail="Bot sign-in only applies to the self-hosted notetaker. The "
               "current provider manages its own bot identity.",
    )


def _worker_or_503() -> SelfHostedProvider:
    provider = resolve_bot_provider()
    if not isinstance(provider, SelfHostedProvider):
        raise _identity_unavailable()
    return provider


def _worker_error(exc: Exception) -> HTTPException:
    """Translate a worker failure into something actionable.

    The worker's 400s carry the diagnostics that name the wall Google put up
    (``blocked`` = scripted login impossible, ``needs_human`` = 2FA), and its
    409s mean a bot is mid-call. Both are the answer, so neither gets flattened
    into a generic 502.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        try:
            detail = exc.response.json().get("detail")
        except Exception:
            detail = None
        if code == 409:
            return HTTPException(
                status_code=409,
                detail=str(detail) if detail else
                       "The notetaker is in a call — its browser profile is in "
                       "use. Try again once the meeting has finished.",
            )
        if code == 400:
            return HTTPException(status_code=400, detail=detail or "sign-in failed")
    return HTTPException(
        status_code=502,
        detail="Couldn't reach the notetaker worker. It may be restarting.",
    )


@router.get("/bot/identity")
async def bot_identity(
    _user: UserContext = Depends(get_current_user),
) -> dict:
    """Whether the notetaker is signed into a Google account, and as whom.

    Readable by any signed-in user: it exposes no secret, and "why did the bot
    get refused?" is a question anyone looking at a failed join will ask.
    """
    provider = resolve_bot_provider()
    if not isinstance(provider, SelfHostedProvider):
        # Not an error — Recall manages identity itself. Say so plainly so the
        # UI can hide the section instead of showing a broken one.
        return {"supported": False, "signed_in": None, "provider": _provider_name()}
    try:
        info = await provider.identity()
    except Exception as exc:
        _log.warning("notes.bot_identity_failed", error=str(exc)[:200])
        return {
            "supported": True,
            "signed_in": None,
            "unreachable": True,
            "error": "Couldn't reach the notetaker worker.",
        }
    interactive = info.get("interactive") or {}
    return {
        "supported": True,
        "signed_in": bool(info.get("signed_in")),
        "email": info.get("email"),
        "profile": bool(info.get("profile")),
        "credentials_configured": bool(info.get("credentials_configured")),
        "interactive_running": bool(interactive.get("running")),
        "vnc_enabled": bool(interactive.get("vnc_enabled")),
    }


@router.post(
    "/bot/identity/sign-in",
    dependencies=[require_role(UserRole.EXECUTIVE, UserRole.AGENT)],
)
async def bot_identity_sign_in(
    body: BotIdentityRequest,
    user: UserContext = Depends(get_current_user),
) -> dict:
    """Sign the notetaker into a Google account (scripted).

    Takes up to ~2 minutes: a real browser drives Google's login. Fails with a
    named wall rather than a shrug — ``blocked`` means Google refused a scripted
    login outright (use the interactive path), ``needs_human`` means the
    password worked and something interactive remains (2FA).
    """
    provider = _worker_or_503()
    email = (body.email or "").strip()
    password = body.password or ""
    if not email or not password:
        raise HTTPException(status_code=400, detail="Enter the email and password.")
    if "@" not in email:
        raise HTTPException(status_code=400, detail="That doesn't look like an email address.")
    try:
        out = await provider.identity_sign_in(email, password)
    except Exception as exc:
        # Never let the password reach a log line, even truncated.
        _log.warning("notes.bot_sign_in_failed", email=email, error=type(exc).__name__)
        raise _worker_error(exc) from None
    _log.info("notes.bot_signed_in", email=email, by=user.email)
    return {"ok": True, "signed_in": bool(out.get("signed_in")), "email": out.get("email")}


@router.post(
    "/bot/identity/interactive",
    status_code=202,
    dependencies=[require_role(UserRole.EXECUTIVE, UserRole.AGENT)],
)
async def bot_identity_interactive(
    user: UserContext = Depends(get_current_user),
) -> dict:
    """Open Google's sign-in page in the worker's browser and hold it open so a
    human can finish it over VNC — the path for 2FA, passkeys and consent
    screens that a scripted login can never pass."""
    provider = _worker_or_503()
    try:
        out = await provider.identity_interactive()
    except Exception as exc:
        raise _worker_error(exc) from None
    _log.info("notes.bot_interactive_login_opened", by=user.email)
    return {"ok": True, **out}
