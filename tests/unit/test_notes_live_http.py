"""HTTP-level test of the live path the in-browser recorder actually drives.

The unit tests cover the bus and registry as functions; this drives the real
FastAPI routes end to end — POST a caption exactly as `postLiveSegment()` does,
then read it back off the roster and the SSE stream. It's the surface the
recording screen hits, so a routing/auth/serialisation regression shows up here
rather than in the browser.

No database: these three routes are pure in-memory (presence lives in Postgres
and is covered separately), which is also why on-page live transcription works
the moment the app is deployed.

Task 3966: each member route here checks the owner first, through
``core.require_owned_meeting``. The fixture below plays that check for the
signed-in tester, so these tests stay hermetic. The R8 proof of the check is
``test_notes_meeting_owner_r8.py``.
"""
from __future__ import annotations

import asyncio
import json

import pytest
from acb_auth import UserContext, UserRole, build_access, get_current_user
from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.testclient import TestClient
from gateway.routes.notes import live_speakers as ls
from gateway.routes.notes import live_transcript as lt

OWNER = "tester@example.com"


@pytest.fixture(autouse=True)
def _owner_check(monkeypatch) -> list[str]:
    """The owner check, played for ``OWNER``: any other member gets the 404."""
    checked: list[str] = []

    async def require(meeting_id: str, owner_email: str | None) -> None:
        checked.append(meeting_id)
        if owner_email != OWNER:
            raise HTTPException(status_code=404, detail="meeting not found")

    monkeypatch.setattr(lt, "require_owned_meeting", require)
    return checked


def _client() -> TestClient:
    """Mount just the live routes, with auth stubbed to a fixed user."""
    app = FastAPI()
    sub = APIRouter()
    for route in lt.router.routes:
        if "/live" in getattr(route, "path", ""):
            sub.routes.append(route)
    app.include_router(sub)
    # A real UserContext, not a duck-typed stand-in: the /notes routes are
    # gated on `feature:notes` (org access control), so the override has to
    # carry a resolvable permission set the way the real dependency does.
    app.dependency_overrides[get_current_user] = lambda: UserContext(
        email=OWNER,
        role=UserRole.EMPLOYEE,
        access=build_access(["feature:notes"], roles=["member"]),
    )
    return TestClient(app)


def test_browser_caption_round_trip() -> None:
    """A relayed caption becomes an attributed line + a roster entry."""
    mid = "http-round-trip"
    lt._BUSES.pop(mid, None)
    ls.reset(mid)
    c = _client()

    # Exactly the payload postLiveSegment() sends.
    r = c.post(
        f"/notes/meetings/{mid}/live/browser-segment",
        json={
            "text": "Hi everyone, I'm Priya",
            "start_s": 0.0,
            "end_s": 2.0,
            "speaker_label": "S1",
            "is_final": True,
        },
    )
    assert r.status_code == 202, r.text

    roster = c.get(f"/notes/meetings/{mid}/live/roster")
    assert roster.status_code == 200
    speakers = roster.json()["speakers"]
    assert speakers == [
        {"speaker_id": "S1", "name": "Priya", "role": None, "utterances": 0}
    ]
    ls.reset(mid)


def test_two_speakers_stay_distinct_over_http() -> None:
    mid = "http-two-speakers"
    lt._BUSES.pop(mid, None)
    ls.reset(mid)
    c = _client()
    for text, label in [
        ("I'm Priya from sales", "S1"),
        ("Alex here", "S2"),
        ("as I mentioned", "S1"),
    ]:
        c.post(
            f"/notes/meetings/{mid}/live/browser-segment",
            json={"text": text, "start_s": 0.0, "end_s": 1.0,
                  "speaker_label": label, "is_final": True},
        )
    names = {s["speaker_id"]: s["name"] for s in
             c.get(f"/notes/meetings/{mid}/live/roster").json()["speakers"]}
    assert names == {"S1": "Priya", "S2": "Alex"}
    ls.reset(mid)


def test_sse_frames_replay_what_was_posted() -> None:
    """The console attaches mid-meeting and still gets recent context.

    Driven against the SSE generator rather than over HTTP on purpose: the real
    stream only ends when the client disconnects, so a TestClient read would
    block forever. This checks the same frames the browser's EventSource sees."""
    mid = "http-sse"
    lt._BUSES.pop(mid, None)
    ls.reset(mid)
    c = _client()
    c.post(
        f"/notes/meetings/{mid}/live/browser-segment",
        json={"text": "first line", "start_s": 0.0, "end_s": 1.0,
              "speaker_label": "S1", "is_final": True},
    )

    async def first_data_frame() -> dict:
        agen = lt._sse(mid)
        try:
            async for frame in agen:
                if frame.startswith(b"data: "):
                    return json.loads(frame[6:].decode())
        finally:
            await agen.aclose()
        return {}

    payload = asyncio.run(first_data_frame())
    assert payload["text"] == "first line"
    assert payload["speaker_id"] == "S1"
    assert "embedding" not in payload  # never fanned out to clients
    ls.reset(mid)


def test_bot_ingest_still_requires_its_token(monkeypatch) -> None:
    """The browser route is user-authed; the worker's stays machine-authed."""
    monkeypatch.setenv("MEETING_BOT_TOKEN", "s3cret")
    c = _client()
    body = {"text": "x", "start_s": 0.0, "end_s": 1.0, "is_final": True}
    assert c.post("/notes/meetings/m/live/segment", json=body).status_code == 401
    ok = c.post(
        "/notes/meetings/m/live/segment",
        json=body,
        headers={"Authorization": "Bearer s3cret"},
    )
    assert ok.status_code == 202


def test_the_member_routes_check_the_owner_first(_owner_check) -> None:
    """Task 3966: the caption post and the roster read run the owner check."""
    mid = "http-owner"
    lt._BUSES.pop(mid, None)
    ls.reset(mid)
    c = _client()
    body = {"text": "x", "start_s": 0.0, "end_s": 1.0, "is_final": True}
    assert c.post(f"/notes/meetings/{mid}/live/browser-segment", json=body).status_code == 202
    assert c.get(f"/notes/meetings/{mid}/live/roster").status_code == 200
    assert _owner_check == [mid, mid]
    ls.reset(mid)
