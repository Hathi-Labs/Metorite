"""Metorite Meeting Bot — a fully self-hosted meeting-joining worker.

A headless-Chrome (Playwright) participant that joins a meeting link, records
the call's audio, and serves it back over a small vendor-neutral HTTP contract.
Metorite's ``selfhosted`` bot provider (gateway
``routes/notes/meeting_bot.py``) drives this — so there is **no third-party
cloud** in the loop; the only cost is the box this runs on.

Contract (what the gateway provider speaks):
    POST   /bots                {meeting_url, bot_name} -> {id, status}
    GET    /bots/{id}           -> {id, status, download_url|null, error|null}
    POST   /bots/{id}/leave     -> 202  (leave the call now)
    GET    /bots/{id}/recording -> audio bytes (when status == "done")
    GET    /health              -> {ok: true}

Status vocabulary matches the gateway's lifecycle directly:
    joining -> waiting_room -> in_call -> processing -> done
    (or failed / not_admitted)

Scope: this MVP targets **Google Meet** (the most automatable via a browser).
Zoom/Teams are future work (they usually need their SDKs). One meeting per
worker instance — scale by running more instances (each is ~1 headless Chrome).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .meet import MeetingBotError, join_and_record

DATA_DIR = os.environ.get("MEETING_BOT_DATA", "/data")
TOKEN = os.environ.get("MEETING_BOT_TOKEN", "").strip()
#: How many meetings this worker will take at once. One by default, and that is
#: not timidity: a bot is a real Chrome joining a live WebRTC call (~1.5-3 vCPU,
#: 3-6 GB each — the figures the commercial providers publish for their own
#: per-bot pods), and a signed-in profile can only be held by one Chrome anyway.
#: Scale by running more worker instances, not by raising this.
MAX_CONCURRENT = max(1, int(os.environ.get("MEET_MAX_CONCURRENT", "1")))

logging.basicConfig(
    level=os.environ.get("MEETING_BOT_LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
_log = logging.getLogger("meeting_bot")

app = FastAPI(title="Metorite Meeting Bot", version="0.1.0")


class _Job:
    def __init__(
        self, job_id: str, meeting_url: str, bot_name: str, live_callback: str | None
    ) -> None:
        self.id = job_id
        self.meeting_url = meeting_url
        self.bot_name = bot_name
        self.live_callback = live_callback  # gateway URL for live segments (or None)
        self.status = "requested"
        self.error: str | None = None
        #: What the page looked like when a join failed (see meet._snapshot).
        self.diagnostics: dict = {}
        self.recording: str | None = None
        #: Why the call stopped — see meet._wait_until_end. Distinguishes a
        #: normal end from being removed by the host, from nobody ever joining.
        self.end_reason: str | None = None
        self.leave = asyncio.Event()
        self.say_queue: asyncio.Queue[str] = asyncio.Queue()
        self.task: asyncio.Task | None = None


_JOBS: dict[str, _Job] = {}

_TERMINAL = ("done", "failed", "left", "not_admitted")


def _state_path(job_id: str) -> str:
    return os.path.join(DATA_DIR, f"{job_id}.state.json")


def _persist(job: _Job) -> None:
    """Mirror the job's state to /data on every transition (atomic rename).

    Jobs live in memory, but the worker is recreated by every deploy — before
    this, a restart made GET /bots/{id} 404 and the gateway's row sat in
    "joining" forever while the evidence (error text, diagnostics) vanished.
    The screenshot already survives on the volume; now the story does too.
    """
    state = {
        "id": job.id,
        "meeting_url": job.meeting_url,
        "bot_name": job.bot_name,
        "status": job.status,
        "error": job.error,
        "diagnostics": job.diagnostics,
        "recording": job.recording,
        "end_reason": job.end_reason,
    }
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = _state_path(job.id) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(state, fh)
        os.replace(tmp, _state_path(job.id))
    except Exception:
        _log.warning("bot.persist_failed id=%s", job.id, exc_info=True)


def _load_persisted(job_id: str) -> _Job | None:
    """Rehydrate a job another worker life wrote. In-flight statuses become a
    clean failure — this process cannot rejoin a call it was never in."""
    if not job_id or "/" in job_id or "\\" in job_id or ".." in job_id:
        return None
    try:
        with open(_state_path(job_id), encoding="utf-8") as fh:
            state = json.load(fh)
    except Exception:
        return None
    job = _Job(
        job_id,
        str(state.get("meeting_url") or ""),
        str(state.get("bot_name") or "AI Notetaker"),
        None,
    )
    job.status = str(state.get("status") or "failed")
    job.error = state.get("error")
    job.diagnostics = state.get("diagnostics") or {}
    job.end_reason = state.get("end_reason")
    rec = state.get("recording")
    job.recording = rec if rec and os.path.isfile(rec) else None
    if job.status not in _TERMINAL:
        # Interrupted mid-meeting by a worker restart (deploys recreate the
        # container). Keep a finished recording if one made it to disk.
        if job.recording:
            job.status = "done"
        else:
            job.status = "failed"
            job.error = job.error or (
                "The meeting-bot worker restarted (most likely a deploy) "
                "while this bot was in flight — the join was lost. "
                "Send the notetaker again."
            )
        _persist(job)
    _JOBS[job_id] = job
    return job


def _get_job(job_id: str) -> _Job | None:
    return _JOBS.get(job_id) or _load_persisted(job_id)


def _auth(authorization: str | None) -> None:
    if TOKEN and authorization != f"Bearer {TOKEN}":
        raise HTTPException(status_code=401, detail="unauthorized")


class JoinRequest(BaseModel):
    meeting_url: str
    bot_name: str = "AI Notetaker"
    # Optional gateway URL to POST live transcript segments to (per meeting).
    live_callback: str | None = None


class SayRequest(BaseModel):
    text: str


#: Why an empty recording is empty. Without these, "captured no audio" always
#: reads as a broken audio stack and sends someone into the container to check
#: PulseAudio — when usually nobody ever joined the call.
_NO_AUDIO_REASONS = {
    "noone_joined": (
        "Nobody else ever joined, so there was nothing to record. The notetaker "
        "waited alone and left. Check the meeting actually went ahead."
    ),
    "kicked": (
        "The host removed the notetaker from the call before anything was "
        "recorded. If that wasn't deliberate, admit it and send it again."
    ),
    "ended": (
        "The call ended before any audio was captured — it was probably already "
        "over when the notetaker arrived."
    ),
}

#: Same information, for a recording that DID capture audio but ended oddly.
_NOTE_REASONS = {
    "kicked": (
        "The host removed the notetaker mid-call, so this recording stops early."
    ),
    "noone_joined": (
        "The notetaker was alone for the whole call and left by itself."
    ),
}


async def _run(job: _Job) -> None:
    """Background driver: join + record, then mark the job terminal."""
    os.makedirs(DATA_DIR, exist_ok=True)
    out_path = os.path.join(DATA_DIR, f"{job.id}.ogg")

    def on_status(s: str) -> None:
        job.status = s
        _persist(job)

    def on_end_reason(reason: str) -> None:
        job.end_reason = reason
        _persist(job)

    try:
        await join_and_record(
            job.meeting_url, job.bot_name, out_path, job.leave, on_status,
            live_callback=job.live_callback, say_queue=job.say_queue,
            job_id=job.id, on_end_reason=on_end_reason,
        )
        ok = os.path.isfile(out_path) and os.path.getsize(out_path) > 0
        job.recording = out_path if ok else None
        job.status = "done" if ok else "failed"
        if not ok and not job.error:
            # A bot that joined and captured nothing is usually not a broken
            # audio stack — it's a call that never had anyone in it. Say which,
            # because the two send you to completely different places.
            job.error = _NO_AUDIO_REASONS.get(
                job.end_reason or "",
                "Joined the call but captured no audio — check that PulseAudio "
                "and ffmpeg are running inside the container.",
            )
        elif ok and job.end_reason in ("kicked", "noone_joined"):
            # Recording is fine but short, and the reason belongs on the meeting
            # rather than only in a log line nobody will read.
            job.error = _NOTE_REASONS.get(job.end_reason)
    except MeetingBotError as exc:
        job.status = exc.status
        job.error = str(exc)
        job.diagnostics = exc.diagnostics
        _log.warning("bot.failed id=%s status=%s error=%s", job.id, exc.status, exc)
    except Exception as exc:  # never let a runner crash take down the worker
        job.status = "failed"
        job.error = f"{type(exc).__name__}: {str(exc)[:400]}"
        _log.exception("bot.crashed id=%s", job.id)
    finally:
        _persist(job)


def _active_count() -> int:
    return sum(
        1
        for j in _JOBS.values()
        if j.status in ("joining", "waiting_room", "in_call", "processing")
    )


@app.get("/health")
async def health() -> dict:
    from .meet import PROFILE_DIR

    return {
        "ok": True,
        "active": _active_count(),
        # Whether this worker can present a signed-in identity at all — the
        # single most useful fact when joins are being auto-declined.
        "profile": bool(PROFILE_DIR),
    }


# ── Google sign-in (so Meet stops auto-declining the bot) ───────────────────
# Anonymous participants are refused outright by Meet whenever the host isn't
# in the call yet or link-guests can't ask in. A signed-in profile is the fix;
# these endpoints put an account into it once.

@app.get("/google-login")
async def google_login_status(
    authorization: str | None = Header(default=None),
) -> dict:
    _auth(authorization)
    from . import google_login

    if _active_count() > 0:
        # Chrome locks the profile dir; checking mid-call would either fail or
        # disturb a live recording.
        raise HTTPException(
            status_code=409,
            detail="a bot is in a call — the browser profile is in use",
        )
    try:
        return {
            **await google_login.current_account(),
            "interactive": google_login.interactive_status(),
            "credentials_configured": all(google_login.configured_credentials()),
        }
    except google_login.LoginError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


class GoogleLoginRequest(BaseModel):
    email: str | None = None
    password: str | None = None


@app.post("/google-login")
async def google_login(
    req: GoogleLoginRequest | None = None,
    authorization: str | None = Header(default=None),
) -> dict:
    """Sign the bot into a Google account (scripted). Credentials may come from
    the body or from MEET_GOOGLE_EMAIL / MEET_GOOGLE_PASSWORD."""
    _auth(authorization)
    from . import google_login as gl

    if _active_count() > 0:
        raise HTTPException(
            status_code=409,
            detail="a bot is in a call — try again once it has finished",
        )
    if gl.interactive_status()["running"]:
        raise HTTPException(
            status_code=409, detail="an interactive login session is open"
        )
    try:
        return await gl.sign_in(
            (req.email if req else "") or "", (req.password if req else "") or ""
        )
    except gl.LoginError as exc:
        # 400 + the diagnostics: which wall Google put up is the whole answer.
        raise HTTPException(
            status_code=400,
            detail={"error": str(exc), "diagnostics": exc.diagnostics},
        ) from None


@app.post("/google-login/interactive", status_code=202)
async def google_login_interactive(
    authorization: str | None = Header(default=None),
) -> dict:
    """Open Google's sign-in page in the container's browser and hold it open so
    a human can finish it over VNC (2FA, passkeys, 'verify it's you')."""
    _auth(authorization)
    from . import google_login as gl

    if _active_count() > 0:
        raise HTTPException(
            status_code=409,
            detail="a bot is in a call — try again once it has finished",
        )
    try:
        return gl.start_interactive()
    except gl.LoginError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@app.post("/bots", status_code=201)
async def create_bot(
    req: JoinRequest, authorization: str | None = Header(default=None)
) -> dict:
    """Dispatch a bot to one meeting.

    Refuses when the worker is already at capacity, which matters more than it
    looks: each bot is a real Chrome (~1.5-3 vCPU, 3-6 GB) and a **persistent
    profile can only be opened by one Chrome at a time** — Chrome takes an
    exclusive lock on the user-data dir. Accepting a second concurrent job
    would either fail deep in the launch with a lock error or, worse, corrupt
    the signed-in profile that makes unattended joining possible at all. A
    clean 409 up front is the honest answer, and the one the gateway can
    explain to a user.
    """
    _auth(authorization)
    url = (req.meeting_url or "").strip()
    if not url.startswith("http"):
        raise HTTPException(status_code=400, detail="invalid meeting_url")
    from .meet import PROFILE_DIR

    active = _active_count()
    if active >= MAX_CONCURRENT:
        raise HTTPException(
            status_code=409,
            detail=(
                f"this notetaker is already in {active} meeting(s) and can take "
                f"{MAX_CONCURRENT}. Wait for it to finish, or run another worker "
                "instance (each concurrent meeting needs its own Chrome)."
            ),
        )
    if PROFILE_DIR and active > 0:
        # Belt and braces: raising MEET_MAX_CONCURRENT above 1 while sharing one
        # signed-in profile is a footgun, so refuse it here rather than let two
        # Chromes race for the profile lock.
        raise HTTPException(
            status_code=409,
            detail=(
                "this notetaker is already in a meeting and its signed-in "
                "browser profile can only be used by one call at a time."
            ),
        )
    job_id = uuid.uuid4().hex
    job = _Job(
        job_id, url,
        (req.bot_name or "AI Notetaker").strip() or "AI Notetaker",
        (req.live_callback or None),
    )
    job.status = "joining"
    _JOBS[job_id] = job
    _persist(job)
    job.task = asyncio.create_task(_run(job))
    return {"id": job_id, "status": job.status}


@app.post("/bots/{bot_id}/say", status_code=202)
async def say(
    bot_id: str, req: SayRequest, authorization: str | None = Header(default=None)
) -> dict:
    """Queue a line for the bot to speak into the call (TTS → virtual mic).
    The actuator for agent interjections; the runner drains this queue while
    in-call."""
    _auth(authorization)
    job = _JOBS.get(bot_id)  # deliberately memory-only: can't speak after a restart
    if job is None:
        raise HTTPException(status_code=404, detail="unknown bot")
    text = (req.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="empty text")
    job.say_queue.put_nowait(text)
    return {"ok": True}


@app.get("/bots/{bot_id}")
async def get_bot(
    bot_id: str, authorization: str | None = Header(default=None)
) -> dict:
    _auth(authorization)
    job = _get_job(bot_id)
    if job is None:
        raise HTTPException(status_code=404, detail="unknown bot")
    # download_url null → the gateway falls back to GET /bots/{id}/recording.
    # end_reason rides along so the gateway can tell a call that finished
    # normally from one the host ejected the bot from, without parsing prose.
    return {
        "id": bot_id,
        "status": job.status,
        "download_url": None,
        "error": job.error,
        "end_reason": job.end_reason,
    }


@app.get("/bots/{bot_id}/diagnostics")
async def get_diagnostics(
    bot_id: str, authorization: str | None = Header(default=None)
) -> dict:
    """What the page looked like when a join failed.

    Exists because Meet's DOM is not a public API: when a selector misses, the
    only way to write the next one is to see which controls the page actually
    rendered. ``controls`` is that list; ``screenshot`` is served by
    ``/bots/{id}/screenshot``.
    """
    _auth(authorization)
    job = _get_job(bot_id)
    if job is None:
        raise HTTPException(status_code=404, detail="unknown bot")
    diag = dict(job.diagnostics)
    diag.pop("screenshot", None)  # a path inside the container is not useful
    return {
        "id": bot_id,
        "status": job.status,
        "error": job.error,
        "has_screenshot": bool(job.diagnostics.get("screenshot")),
        "diagnostics": diag,
    }


@app.get("/bots/{bot_id}/screenshot")
async def get_screenshot(
    bot_id: str, authorization: str | None = Header(default=None)
) -> FileResponse:
    """The green room as the bot saw it — the fastest way to tell a waiting
    room from a sign-in wall from a device dialog."""
    _auth(authorization)
    job = _get_job(bot_id)
    shot = (job.diagnostics or {}).get("screenshot") if job else None
    if not shot or not os.path.isfile(shot):
        raise HTTPException(status_code=404, detail="no screenshot")
    return FileResponse(shot, media_type="image/png")


@app.post("/bots/{bot_id}/leave", status_code=202)
async def leave_bot(
    bot_id: str, authorization: str | None = Header(default=None)
) -> dict:
    _auth(authorization)
    job = _get_job(bot_id)
    if job is None:
        raise HTTPException(status_code=404, detail="unknown bot")
    job.leave.set()  # the runner finalises the recording and leaves
    return {"ok": True}


@app.get("/bots/{bot_id}/recording")
async def get_recording(
    bot_id: str, authorization: str | None = Header(default=None)
) -> FileResponse:
    _auth(authorization)
    job = _get_job(bot_id)
    if job is None or not job.recording or not os.path.isfile(job.recording):
        raise HTTPException(status_code=404, detail="no recording")
    return FileResponse(job.recording, media_type="audio/ogg")
