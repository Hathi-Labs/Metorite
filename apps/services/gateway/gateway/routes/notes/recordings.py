"""Recording upload + audio playback.

Slice 0 ships the retro-import path (single-file upload → transcription
pipeline). The live browser recorder (chunked MediaRecorder appends) lands in
slice 1 on the same tables (spec: note_taker_app.md §6).
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

from acb_auth import UserContext, get_current_user
from fastapi import Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from gateway.routes.notes.core import (
    OWNED_MEETING_PREDICATE,
    _log,
    _tenant_session,
    load_owned_meeting,
    media_dir,
    router,
)
from gateway.routes.notes.pipeline import run_transcription
from pydantic import BaseModel
from sqlalchemy import text

_ALLOWED_EXT = {".webm", ".mp3", ".wav", ".m4a", ".mp4", ".ogg", ".oga", ".flac", ".aac"}
_MAX_UPLOAD_BYTES = 300 * 1024 * 1024  # server cap; provider caps surface as run errors

_EXT_MIME = {
    ".webm": "audio/webm", ".mp3": "audio/mpeg", ".wav": "audio/wav",
    ".m4a": "audio/mp4", ".mp4": "audio/mp4", ".ogg": "audio/ogg",
    ".oga": "audio/ogg", ".flac": "audio/flac", ".aac": "audio/aac",
}
# Container → file extension for live MediaRecorder streams (Chromium/Firefox
# emit webm/opus; Safari emits mp4/aac). Feature-detected client-side.
_MIME_EXT = {
    "audio/webm": ".webm", "audio/ogg": ".ogg", "audio/mp4": ".mp4",
    "audio/mpeg": ".mp3", "audio/wav": ".wav",
}

# Keep strong refs to in-flight pipeline tasks — a bare create_task() result
# can be garbage-collected mid-run.
_PIPELINE_TASKS: set[asyncio.Task] = set()

# In-memory per-recording append cursor (last seq written). A live recording has
# a single writer (one browser tab) uploading chunks strictly in order, so this
# gives idempotent, gap-checked appends without a DB round-trip per chunk. A
# gateway restart mid-recording abandons the recording — acceptable for capture.
_REC_SEQ: dict[str, int] = {}


def _spawn_pipeline(coro) -> None:
    task = asyncio.create_task(coro)
    _PIPELINE_TASKS.add(task)
    task.add_done_callback(_PIPELINE_TASKS.discard)


def _base_mime(mime: str) -> str:
    return (mime or "").split(";", 1)[0].strip().lower()


@router.post("/meetings/{meeting_id}/upload", status_code=202)
async def upload_recording(
    meeting_id: str,
    file: UploadFile = File(...),
    channel: str = Form("upload"),
    user: UserContext = Depends(get_current_user),
) -> dict:
    """Attach an audio file to a meeting and start transcription.

    Returns ``{recording_id, run_id}``; progress is visible on the meeting's
    ``runs`` (poll ``GET /notes/meetings/{id}`` — SSE arrives with slice 1).

    Owner only. The upload becomes a transcription run, which chains notes
    generation and ``auto_dispatch`` — the same reach ``retranscribe`` is
    guarded for. Unscoped it also let any member write audio into a
    colleague's meeting and flip its status.
    """
    if channel not in ("mic", "system", "mixed", "upload"):
        raise HTTPException(status_code=400, detail="invalid channel")
    safe_name = Path(file.filename or "audio").name
    ext = Path(safe_name).suffix.lower()
    if ext not in _ALLOWED_EXT:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported audio type: {ext or '(none)'}. "
                   f"Allowed: {', '.join(sorted(_ALLOWED_EXT))}",
        )
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="empty file")
    if len(content) > _MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="file exceeds 300 MB limit")

    mime = file.content_type or _EXT_MIME.get(ext, "application/octet-stream")
    recording_id = str(uuid.uuid4())
    rel_path = f"{meeting_id}/{recording_id}{ext}"
    dest = media_dir() / rel_path
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(content)

    async with _tenant_session() as db:
        try:
            await load_owned_meeting(db, meeting_id, user.email, columns="m.id")
        except HTTPException:
            dest.unlink(missing_ok=True)
            raise
        await db.execute(
            text(
                """
                INSERT INTO meeting_recording (id, meeting_id, channel, artifact_path,
                                               mime, byte_size)
                VALUES (:id, :meeting_id, :channel, :path, :mime, :size)
                """
            ),
            {
                "id": recording_id, "meeting_id": meeting_id, "channel": channel,
                "path": rel_path, "mime": mime, "size": len(content),
            },
        )
        run_row = (
            await db.execute(
                text(
                    """
                    INSERT INTO summary_run (meeting_id, kind, status, stage)
                    VALUES (:meeting_id, 'transcribe', 'queued', 'queued')
                    RETURNING id
                    """
                ),
                {"meeting_id": meeting_id},
            )
        ).fetchone()
        await db.execute(
            text("UPDATE meeting SET status = 'processing' WHERE id = :id"),
            {"id": meeting_id},
        )

    run_id = str(run_row.id)
    _log.info(
        "notes.recording_uploaded",
        meeting_id=meeting_id, recording_id=recording_id, bytes=len(content), mime=mime,
    )
    _spawn_pipeline(
        run_transcription(meeting_id, recording_id, run_id, user.email or "")
    )
    return {"recording_id": recording_id, "run_id": run_id, "status": "processing"}


# ── Live recording (chunked MediaRecorder append) ────────────────────────────

class StartRecordingRequest(BaseModel):
    channel: str = "mic"
    mime: str = "audio/webm"


class CompleteRecordingRequest(BaseModel):
    duration_s: float | None = None


@router.post("/meetings/{meeting_id}/recordings/start", status_code=201)
async def start_recording(
    meeting_id: str,
    body: StartRecordingRequest,
    user: UserContext = Depends(get_current_user),
) -> dict:
    """Open a live recording: create the row + empty file, mark the meeting
    'recording'. The client then streams MediaRecorder chunks to ``/chunk`` and
    finalizes with ``/complete``.

    Owner only — it flips the meeting to ``status='recording'`` and opens a
    writable recording on it, the same mutation ``bot_join``'s attach branch
    performs from the other direction."""
    if body.channel not in ("mic", "system", "mixed"):
        raise HTTPException(status_code=400, detail="invalid channel")
    base = _base_mime(body.mime)
    ext = _MIME_EXT.get(base)
    if ext is None:
        raise HTTPException(status_code=400, detail=f"unsupported recording mime: {base}")

    recording_id = str(uuid.uuid4())
    rel_path = f"{meeting_id}/{recording_id}{ext}"
    dest = media_dir() / rel_path
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.touch()

    async with _tenant_session() as db:
        try:
            await load_owned_meeting(db, meeting_id, user.email, columns="m.id")
        except HTTPException:
            dest.unlink(missing_ok=True)
            raise
        await db.execute(
            text(
                "INSERT INTO meeting_recording (id, meeting_id, channel, "
                "artifact_path, mime, byte_size) VALUES "
                "(:id, :mid, :channel, :path, :mime, 0)"
            ),
            {"id": recording_id, "mid": meeting_id, "channel": body.channel,
             "path": rel_path, "mime": base},
        )
        await db.execute(
            text("UPDATE meeting SET status='recording' WHERE id=:id"),
            {"id": meeting_id},
        )
    _REC_SEQ[recording_id] = -1
    # Register presence so this shows up as "live now" across Metorite and
    # the copilot console can attach to it. Additive + fail-safe.
    from gateway.routes.notes import live_session

    await live_session.begin(meeting_id, "browser", getattr(user, "email", None))
    _log.info("notes.recording_started", meeting_id=meeting_id, recording_id=recording_id)
    return {"recording_id": recording_id}


async def _recording_path(recording_id: str, meeting_id: str, owner_email: str | None):
    """The file behind a recording — if its meeting belongs to ``owner_email``.

    The owner scope is joined in rather than checked after the load, so the
    two routes that share this helper (``/chunk`` and ``/complete``) cannot
    each acquire the hole separately, and ``/chunk`` — which runs every few
    seconds for the whole meeting — pays no extra round trip for it.

    404 with the same "recording not found" detail either way: a recording id
    that belongs to a colleague must be indistinguishable from one that does
    not exist.
    """
    async with _tenant_session() as db:
        row = (
            await db.execute(
                text(
                    "SELECT r.artifact_path FROM meeting_recording r "
                    "JOIN meeting m ON m.id = r.meeting_id "
                    "WHERE r.id=:id AND r.meeting_id=:mid "
                    f"AND {OWNED_MEETING_PREDICATE}"
                ),
                {"id": recording_id, "mid": meeting_id,
                 "owner": owner_email or ""},
            )
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="recording not found")
    return media_dir() / row.artifact_path


@router.post("/meetings/{meeting_id}/recordings/{recording_id}/chunk")
async def append_chunk(
    meeting_id: str,
    recording_id: str,
    request: Request,
    seq: int,
    user: UserContext = Depends(get_current_user),
) -> dict:
    """Append one ordered MediaRecorder blob to the recording file.

    Idempotent + gap-checked via the in-memory cursor: a re-sent chunk (ack
    lost) is a no-op; an out-of-order chunk is rejected so the client can
    resync. The concatenation of a single MediaRecorder session's timeslice
    blobs is a valid container, so ordered append reconstructs the file.

    Owner only, via ``_recording_path`` — appending arbitrary bytes to a
    colleague's recording corrupts what is being transcribed as their meeting.
    """
    data = await request.body()
    if not data:
        raise HTTPException(status_code=400, detail="empty chunk")
    path = await _recording_path(recording_id, meeting_id, user.email)

    last = _REC_SEQ.get(recording_id)
    if last is None:
        # Cursor lost (gateway restart) but row exists — resume from file size.
        last = -1 if not path.exists() else 0
        _REC_SEQ[recording_id] = last
    if seq <= last:
        return {"ok": True, "duplicate": True, "seq": last}
    if seq != last + 1:
        raise HTTPException(
            status_code=409, detail=f"out-of-order chunk: expected {last + 1}, got {seq}"
        )
    if path.stat().st_size + len(data) > _MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="recording exceeds size limit")

    with path.open("ab") as f:
        f.write(data)
    _REC_SEQ[recording_id] = seq
    return {"ok": True, "seq": seq}


@router.post("/meetings/{meeting_id}/recordings/{recording_id}/complete", status_code=202)
async def complete_recording(
    meeting_id: str,
    recording_id: str,
    body: CompleteRecordingRequest,
    user: UserContext = Depends(get_current_user),
) -> dict:
    """Finalize a live recording and start transcription.

    Owner only, via ``_recording_path``: this is a second entry into the
    transcription pipeline → notes generation → ``auto_dispatch``, the reach
    ``retranscribe`` is guarded for."""
    path = await _recording_path(recording_id, meeting_id, user.email)
    size = path.stat().st_size if path.exists() else 0
    _REC_SEQ.pop(recording_id, None)
    if size == 0:
        async with _tenant_session() as db:
            await db.execute(
                text("UPDATE meeting SET status='failed' WHERE id=:id"),
                {"id": meeting_id},
            )
        raise HTTPException(status_code=400, detail="no audio was recorded")

    async with _tenant_session() as db:
        await db.execute(
            text(
                "UPDATE meeting_recording SET byte_size=:size, duration_s=:dur "
                "WHERE id=:id"
            ),
            {"size": size, "dur": body.duration_s, "id": recording_id},
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
            text("UPDATE meeting SET status='processing', end_at=now() WHERE id=:id"),
            {"id": meeting_id},
        )

    run_id = str(run_row.id)
    _log.info(
        "notes.recording_completed",
        meeting_id=meeting_id, recording_id=recording_id, bytes=size,
    )
    _spawn_pipeline(
        run_transcription(meeting_id, recording_id, run_id, user.email or "")
    )
    return {"recording_id": recording_id, "run_id": run_id, "status": "processing"}


@router.post("/meetings/{meeting_id}/retranscribe", status_code=202)
async def retranscribe(
    meeting_id: str,
    user: UserContext = Depends(get_current_user),
) -> dict:
    """Re-run transcription on the meeting's existing recording with the CURRENT
    STT model — e.g. after switching the STT tier to Deepgram in Settings to get
    named speakers. Replaces this recording's segments and re-chains notes.

    Owner only. "Re-chains notes" is the whole reason: this re-runs generation,
    which deletes the meeting's un-promoted draft action items and then feeds
    the new ones to ``auto_dispatch``, which can send mail from the owner's
    mailbox. Unscoped, it was a way for any member holding ``feature:notes`` to
    reach both of those on a colleague's meeting.
    """
    async with _tenant_session() as db:
        await load_owned_meeting(db, meeting_id, user.email, columns="m.id")
        rows = (
            await db.execute(
                text(
                    "SELECT id, channel FROM meeting_recording WHERE meeting_id=:id"
                ),
                {"id": meeting_id},
            )
        ).fetchall()
        if not rows:
            raise HTTPException(
                status_code=404, detail="no recording to re-transcribe"
            )
        # Prefer the same recording the audio player serves (mixed > upload > …).
        order = {"mixed": 0, "upload": 1, "mic": 2, "system": 3}
        best = sorted(rows, key=lambda r: order.get(r.channel, 9))[0]
        recording_id = str(best.id)
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
            text("UPDATE meeting SET status='processing' WHERE id=:id"),
            {"id": meeting_id},
        )

    run_id = str(run_row.id)
    _log.info("notes.retranscribe", meeting_id=meeting_id, recording_id=recording_id)
    _spawn_pipeline(
        run_transcription(meeting_id, recording_id, run_id, user.email or "")
    )
    return {"recording_id": recording_id, "run_id": run_id, "status": "processing"}


@router.get("/meetings/{meeting_id}/audio")
async def get_audio(
    meeting_id: str,
    user: UserContext = Depends(get_current_user),
) -> FileResponse:
    """Serve the meeting's audio for the seek-player (mixed > upload > mic).

    Owner only: this is the raw recording — the verbatim audio of a
    conversation nobody chose to publish, and the one asset the transcript is
    only a lossy rendering of."""
    async with _tenant_session() as db:
        await load_owned_meeting(db, meeting_id, user.email, columns="m.id")
        rows = (
            await db.execute(
                text("SELECT * FROM meeting_recording WHERE meeting_id = :id"),
                {"id": meeting_id},
            )
        ).fetchall()
    if not rows:
        raise HTTPException(status_code=404, detail="no recording for this meeting")
    order = {"mixed": 0, "upload": 1, "mic": 2, "system": 3}
    best = sorted(rows, key=lambda r: order.get(r.channel, 9))[0]
    path = media_dir() / best.artifact_path
    if not path.is_file():
        raise HTTPException(status_code=410, detail="audio file no longer on disk")
    # Serve INLINE (no filename → no Content-Disposition: attachment) so the
    # browser treats it as playable media, not a download. FileResponse honours
    # Range requests (206) for seeking; the Next proxy relays them.
    return FileResponse(path, media_type=best.mime)
