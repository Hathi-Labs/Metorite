"""Per-meeting SSE progress stream — honest, per-stage pipeline status.

The frontend opens this on a meeting to watch transcribe → summarize progress
without polling (spec: note_taker_app.md §3.7/§5.4). A DB-polling generator is
deliberate: the pipeline is in-process and short-lived, so a 1.5s poll is
simpler and just as timely as wiring a pub/sub for one view. The stream closes
once the meeting reaches a terminal state and no run is still active.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from acb_auth import UserContext, get_current_user
from fastapi import Depends
from fastapi.responses import StreamingResponse
from gateway.routes.notes.core import (
    _log,
    _tenant_session,
    require_owned_meeting,
    router,
)
from sqlalchemy import text

_POLL_S = 1.5
_MAX_S = 1800  # safety ceiling so a stuck job can't hold a stream forever


async def _snapshot(meeting_id: str) -> dict | None:
    async with _tenant_session() as db:
        m = (
            await db.execute(
                text(
                    "SELECT status, title, summary_md IS NOT NULL AS has_summary "
                    "FROM meeting WHERE id=:id"
                ),
                {"id": meeting_id},
            )
        ).fetchone()
        if m is None:
            return None
        runs = (
            await db.execute(
                text(
                    "SELECT kind, status, stage, chunk_done, chunk_total, error "
                    "FROM summary_run WHERE meeting_id=:id "
                    "ORDER BY created_at DESC LIMIT 4"
                ),
                {"id": meeting_id},
            )
        ).fetchall()
    return {
        "status": m.status,
        "title": m.title,
        "has_summary": bool(m.has_summary),
        "runs": [
            {
                "kind": r.kind,
                "status": r.status,
                "stage": r.stage,
                "chunk_done": r.chunk_done,
                "chunk_total": r.chunk_total,
                "error": r.error,
            }
            for r in runs
        ],
    }


def _is_terminal(snap: dict) -> bool:
    # Done when the meeting failed, or it's ready AND no run is still active.
    if snap["status"] == "failed":
        return True
    active = any(r["status"] in ("queued", "running") for r in snap["runs"])
    return snap["status"] == "ready" and snap["has_summary"] and not active


async def _stream(meeting_id: str) -> AsyncIterator[bytes]:
    yield b": connected\n\n"
    last = ""
    waited = 0.0
    while waited < _MAX_S:
        try:
            snap = await _snapshot(meeting_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _log.warning("notes.events_snapshot_failed", error=str(exc)[:200])
            await asyncio.sleep(_POLL_S)
            waited += _POLL_S
            yield b": error\n\n"
            continue
        if snap is None:
            yield b'event: gone\ndata: {}\n\n'
            return
        payload = json.dumps(snap, default=str)
        if payload != last:
            last = payload
            yield f"data: {payload}\n\n".encode()
            if _is_terminal(snap):
                return
        else:
            yield b": ping\n\n"
        await asyncio.sleep(_POLL_S)
        waited += _POLL_S


@router.get("/meetings/{meeting_id}/events")
async def meeting_events(
    meeting_id: str,
    user: UserContext = Depends(get_current_user),
) -> StreamingResponse:
    """The progress stream of one meeting. Owner only, checked BEFORE the
    response starts (task 3966)."""
    await require_owned_meeting(meeting_id, user.email)
    return StreamingResponse(
        _stream(meeting_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
