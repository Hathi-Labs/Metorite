"""Task 3966: each meeting route of ``/notes`` checks the owner (R8).

Owner decision (2026-11-26): "Owner only, the same rule as the meetings list
and detail." The rule is ``core.OWNED_MEETING_PREDICATE`` and
``core.load_owned_meeting``. A legacy meeting with no owner stays open to the
members of its org, because that is part of the same rule.

The suite runs the REAL ``/notes`` router in-process on a phase-4 promoted
scratch database, as the non-privileged app role, so FORCE RLS binds. Org B
holds two members, Alice and Bob. Org A holds Carol. Alice owns a meeting with
a note, an action, an agenda, a brief, a live session and a bot.

Fences (R7):

* ``notes-owner-every-meeting-route``: Bob (same org) and Carol (other org)
  get 404 on every meeting route of Alice's meeting, and on each action route
  of her action. Her rows do not change, her live bus gets no segment, and no
  provider call, bot refresh, copilot start or model call runs for them.
* ``notes-owner-lists``: ``/bots/active`` and ``/live/sessions`` of Bob and of
  Carol do not name Alice's meeting. Alice's own lists do.
* ``notes-owner-still-reads``: Alice still gets 200 on each route, with her
  data. ``/agenda`` and ``/context`` read in the bound tenant, so they show
  her agenda and her brief under FORCE RLS.
* ``notes-owner-legacy-null``: a meeting with no owner stays open to Bob, and
  it stays closed to Carol by RLS.

Run (a PRIVATE ladder database, one R8 suite at a time, because the
``promoted`` fixture drops ``<db>_h3rls``)::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_notes_meeting_owner_r8.py -v -rs

Without the database every test here SKIPS, and the run reads green. A skip
is not a pass.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

import httpx
from acb_common.db import bind_tenant, release_tenant
from fastapi import FastAPI, Request
from sqlalchemy import text

from tests.unit import _r8_lists as r8_lists
from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    promoted,
)

pytestmark = _DB_GATE

ALICE = "alice@notes-owner-r8.test"
BOB = "bob@notes-owner-r8.test"
CAROL = "carol@notes-owner-r8.test"
PEOPLE = (ALICE, BOB, CAROL)

#: Each request waits this long at most. An open SSE stream of the old code
#: never ends, and a timeout counts as "not refused".
_WAIT_S = 5.0


# ── The world ──────────────────────────────────────────────────────────────


def _user(email: str) -> Any:
    from acb_auth import UserContext, UserRole, build_access

    return UserContext(
        email=email, role=UserRole.EMPLOYEE, access=build_access(["feature:notes"]),
    )


def _app() -> FastAPI:
    """The real ``/notes`` router, signed in as ``x-user-email``."""
    from acb_auth import get_current_user
    from gateway.routes.notes import router as notes_router

    people = {e: _user(e) for e in PEOPLE}

    async def who(request: Request) -> Any:
        return people[request.headers["x-user-email"].lower()]

    app = FastAPI()
    app.include_router(notes_router)
    app.dependency_overrides[get_current_user] = who
    return app


def _seed_meeting(c: Any, org: str, owner: str | None, *, title: str) -> dict[str, str]:
    """One meeting with every child that a meeting route reads."""
    mid = str(uuid.uuid4())
    aid = str(uuid.uuid4())
    bot = str(uuid.uuid4())
    c.execute(
        text(
            "INSERT INTO meeting (id, organization_id, platform, start_at, title, "
            "status, owner_email, transcript, summary_md, attendees, agenda, "
            "copilot_brief, speaker_names) VALUES (CAST(:m AS uuid), CAST(:o AS uuid), "
            "'meet', now(), :t, 'ready', :own, 'we priced the pilot', '# notes', "
            "CAST(:att AS jsonb), CAST(:ag AS jsonb), 'Close the Acme deal', "
            "CAST(:sn AS jsonb))"
        ),
        {
            "m": mid, "o": org, "t": title, "own": owner,
            "att": json.dumps([{"name": "Dev", "email": "dev@acme.test"}]),
            "ag": json.dumps([{"title": "Pilot pricing", "notes": ""}]),
            "sn": json.dumps({"S1": "Alice"}),
        },
    )
    c.execute(
        text(
            "INSERT INTO meeting_note (meeting_id, organization_id, notes_md, updated_by) "
            "VALUES (CAST(:m AS uuid), CAST(:o AS uuid), 'secret note', :own)"
        ),
        {"m": mid, "o": org, "own": owner or "legacy"},
    )
    c.execute(
        text(
            "INSERT INTO action_item (id, meeting_id, organization_id, description, "
            "confidence, status, kind) VALUES (CAST(:a AS uuid), CAST(:m AS uuid), "
            "CAST(:o AS uuid), 'send the quote', 0.9, 'draft', 'task')"
        ),
        {"a": aid, "m": mid, "o": org},
    )
    c.execute(
        text(
            "INSERT INTO live_session (meeting_id, organization_id, source, owner_email, "
            "status) VALUES (CAST(:m AS uuid), CAST(:o AS uuid), 'bot', :own, 'live')"
        ),
        {"m": mid, "o": org, "own": owner},
    )
    for idx, (label, said) in enumerate((("S1", "Hi, I'm Alice"), ("S2", "I'm Dev"))):
        c.execute(
            text(
                "INSERT INTO transcript_segment (meeting_id, organization_id, idx, "
                "start_s, end_s, text, speaker_label) VALUES (CAST(:m AS uuid), "
                "CAST(:o AS uuid), :i, :i, :i + 1, :t, :l)"
            ),
            {"m": mid, "o": org, "i": idx, "t": said, "l": label},
        )
    c.execute(
        text(
            "INSERT INTO meeting_bot (id, meeting_id, organization_id, provider, "
            "provider_bot_id, meeting_url, status, requested_by) VALUES "
            "(CAST(:b AS uuid), CAST(:m AS uuid), CAST(:o AS uuid), 'selfhosted', "
            ":pb, 'https://meet.google.com/abc-defg-hij', 'in_call', :own)"
        ),
        {"b": bot, "m": mid, "o": org, "pb": f"pb-{bot[:8]}", "own": owner},
    )
    return {"meeting": mid, "action": aid, "bot": bot}


@pytest.fixture
def world(promoted, monkeypatch):  # noqa: F811
    """Alice's meeting and Bob's meeting in org B, Carol's meeting in org A,
    and a legacy meeting with no owner in org B. Every outward act is a fake
    that records its call."""
    from gateway.routes.notes import (
        agenda_progress,
        copilot,
        copilot_agenda,
        copilot_context,
        live_speakers,
        live_transcript,
        meeting_bot,
        speaker_id,
        summaries,
    )
    from gateway.routes.projects import personal

    org_b, org_a = promoted.org_b, promoted.org_a
    with promoted.admin_engine.begin() as c:
        # The members, for the task that a dispatch captures.
        for email, org in ((ALICE, org_b), (BOB, org_b), (CAROL, org_a)):
            c.execute(
                text(
                    "INSERT INTO app_user (email, display_name, role, status, "
                    "organization_id) SELECT :e, :e, 'employee', 'active', "
                    "CAST(:o AS uuid) WHERE NOT EXISTS "
                    "(SELECT 1 FROM app_user WHERE lower(email) = :e)"
                ),
                {"e": email, "o": org},
            )
        alice = _seed_meeting(c, org_b, ALICE, title="Alice pricing")
        bob = _seed_meeting(c, org_b, BOB, title="Bob standup")
        carol = _seed_meeting(c, org_a, CAROL, title="Carol review")
        legacy = _seed_meeting(c, org_b, None, title="Legacy call")

    calls: dict[str, list[Any]] = {
        "refresh": [], "spawn": [], "start": [], "stop": [], "draft": [],
        "speakers": [], "leave": [], "say": [], "diagnostics": [], "screenshot": [],
        "summary": [],
    }

    def spawn_summary(coro: Any) -> None:
        calls["summary"].append(coro)
        coro.close()

    async def emit(event_type: str, payload: dict) -> None:
        return None

    real_refresh = meeting_bot._refresh_bot

    async def refresh(bot_row_id: str, **_kw: Any) -> None:
        calls["refresh"].append(bot_row_id)

    def spawn(coro: Any) -> None:
        calls["spawn"].append(coro)
        coro.close()

    async def draft(message: str, current: list, **_kw: Any) -> tuple[str, list]:
        calls["draft"].append(message)
        return "drafted", [{"title": "Pilot pricing", "notes": ""}]

    async def name_speakers(sample: str, labels: list[str]) -> dict[str, str]:
        calls["speakers"].append(sample)
        return {"S2": "Dev"}

    async def leave(_self: Any, bot_id: str) -> None:
        calls["leave"].append(bot_id)

    async def say(_self: Any, bot_id: str, text_: str) -> None:
        calls["say"].append(bot_id)

    async def diagnostics(_self: Any, bot_id: str) -> dict:
        calls["diagnostics"].append(bot_id)
        return {"page": "green room"}

    async def screenshot(_self: Any, bot_id: str) -> bytes:
        calls["screenshot"].append(bot_id)
        return b"\x89PNG"

    provider = meeting_bot.SelfHostedProvider
    monkeypatch.setattr(meeting_bot, "_refresh_bot", refresh)
    monkeypatch.setattr(meeting_bot, "_spawn", spawn)
    monkeypatch.setattr(summaries, "_spawn", spawn_summary)
    monkeypatch.setattr(personal, "emit", emit)
    monkeypatch.setattr(meeting_bot, "resolve_bot_provider", lambda: provider())
    monkeypatch.setattr(provider, "leave", leave)
    monkeypatch.setattr(provider, "say", say)
    monkeypatch.setattr(provider, "diagnostics", diagnostics)
    monkeypatch.setattr(provider, "screenshot", screenshot)
    monkeypatch.setattr(copilot, "start", lambda mid: calls["start"].append(mid) or True)
    monkeypatch.setattr(copilot, "stop", lambda mid: calls["stop"].append(mid) or True)
    monkeypatch.setattr(copilot_agenda, "draft_agenda", draft)
    # The real `infer_speaker_names` runs, so its reads and its write run in
    # the bound tenant. Only the model call is a fake.
    monkeypatch.setattr(speaker_id, "_call_llm", name_speakers)
    copilot_context._CACHE.clear()
    for store in (live_transcript._BUSES, live_speakers._REGISTRIES,
                  agenda_progress._TEXT, agenda_progress._COVERED,
                  agenda_progress._STARTED):
        store.clear()

    yield SimpleNamespace(
        p=promoted, org_b=org_b, org_a=org_a, alice=alice, bob=bob, carol=carol,
        legacy=legacy, calls=calls, real_refresh=real_refresh,
    )

    copilot_context._CACHE.clear()
    with promoted.admin_engine.begin() as c:
        c.execute(
            text("DELETE FROM pm_tasks WHERE lower(created_by) = ANY(:e)"),
            {"e": list(PEOPLE)},
        )
        c.execute(
            text("DELETE FROM pm_projects WHERE lower(personal_owner) = ANY(:e)"),
            {"e": list(PEOPLE)},
        )
        c.execute(
            text("DELETE FROM meeting WHERE id = ANY(CAST(:ids AS uuid[]))"),
            {"ids": [w["meeting"] for w in (alice, bob, carol, legacy)]},
        )
        c.execute(
            text("DELETE FROM copilot_config WHERE owner_email = ANY(:e)"),
            {"e": list(PEOPLE)},
        )
        c.execute(text("DELETE FROM app_user WHERE email = ANY(:e)"), {"e": list(PEOPLE)})


@asynccontextmanager
async def _client(world: Any, org: str) -> AsyncIterator[httpx.AsyncClient]:
    """Bind ``org``, point the shared engine at the app role, and talk to the
    router in-process."""
    token = bind_tenant(org)
    try:
        url = world.p.app_url.render_as_string(hide_password=False)
        async with tenant_engine_scope(url):
            transport = httpx.ASGITransport(app=_app())
            async with httpx.AsyncClient(
                transport=transport, base_url="http://gateway.test",
            ) as client:
                yield client
    finally:
        release_tenant(token)


async def _call(c: httpx.AsyncClient, email: str, method: str, path: str,
                body: dict | None = None) -> httpx.Response | None:
    """One request as ``email``. ``None`` when it did not answer in time."""
    try:
        return await asyncio.wait_for(
            c.request(method, path, json=body, headers={"x-user-email": email}),
            timeout=_WAIT_S,
        )
    except TimeoutError:
        return None


def _state(world: Any, mid: str) -> dict[str, Any]:
    """Every row of one meeting that a route can change, as the superuser sees
    it, past RLS and past the person."""
    with world.p.admin_engine.connect() as c:
        def rows(sql: str) -> list[dict]:
            return [dict(r._mapping) for r in c.execute(text(sql), {"m": mid})]

        return {
            "meeting": rows(
                "SELECT title, status, agenda, copilot_brief, speaker_names, "
                "scratch_notes, attendees FROM meeting WHERE id = CAST(:m AS uuid)"),
            "note": rows("SELECT notes_md, updated_by FROM meeting_note "
                         "WHERE meeting_id = CAST(:m AS uuid)"),
            "actions": rows("SELECT id, status, dispatch_ref FROM action_item "
                            "WHERE meeting_id = CAST(:m AS uuid) ORDER BY id"),
            "live": rows("SELECT status, copilot_enabled, mode, deep_context "
                         "FROM live_session WHERE meeting_id = CAST(:m AS uuid)"),
            "bot": rows("SELECT status, error FROM meeting_bot "
                        "WHERE meeting_id = CAST(:m AS uuid)"),
        }


def _meeting_routes(mid: str) -> list[tuple[str, str, dict | None]]:
    """Every route of ``/notes`` that names a meeting by its id."""
    base = f"/notes/meetings/{mid}"
    return [
        ("GET", base, None),
        ("GET", f"{base}/note", None),
        ("PUT", f"{base}/note", {"notes_md": "intruder"}),
        ("GET", f"{base}/actions", None),
        ("GET", f"{base}/agenda", None),
        ("PUT", f"{base}/agenda", {"agenda": [{"title": "intruder"}]}),
        ("POST", f"{base}/agenda/chat", {"message": "add intruder"}),
        ("GET", f"{base}/agenda/progress", None),
        ("GET", f"{base}/context", None),
        ("PUT", f"{base}/brief", {"brief": "intruder"}),
        ("POST", f"{base}/context/deep", {"enabled": False}),
        ("GET", f"{base}/bot", None),
        ("GET", f"{base}/bot/diagnostics", None),
        ("GET", f"{base}/bot/screenshot", None),
        ("POST", f"{base}/bot/stop", None),
        ("GET", f"{base}/live/session", None),
        ("POST", f"{base}/live/copilot", {"enabled": True}),
        ("GET", f"{base}/live/roster", None),
        ("GET", f"{base}/live", None),
        ("POST", f"{base}/live/browser-segment", {"text": "intruder speaks"}),
        ("POST", f"{base}/say", {"text": "intruder"}),
        ("GET", f"{base}/events", None),
        ("POST", f"{base}/identify-speakers", None),
        ("GET", f"{base}/copilot/stream", None),
        ("GET", f"{base}/copilot/events", None),
        ("PUT", f"{base}/scratch", {"scratch_notes": "intruder"}),
        ("PUT", f"{base}/attendees", {"attendees": [{"name": "X", "email": "x@x.test"}]}),
        ("PUT", f"{base}/speakers", {"names": {"S1": "intruder"}}),
        ("POST", f"{base}/actions/approve-all", {"min_confidence": 0.1}),
        ("POST", f"{base}/ask", {"question": "what price?"}),
        ("POST", f"{base}/share/email/draft", None),
        ("POST", f"{base}/summarize", None),
        ("POST", f"{base}/retranscribe", None),
        ("GET", f"{base}/audio", None),
    ]


def _action_routes(aid: str) -> list[tuple[str, str, dict | None]]:
    return [
        ("POST", f"/notes/actions/{aid}/approve", None),
        ("POST", f"/notes/actions/{aid}/reject", None),
        ("POST", f"/notes/actions/{aid}/dispatch", None),
    ]


async def _refused(c: httpx.AsyncClient, email: str, target: dict[str, str]) -> list[str]:
    """Each route that did NOT answer 404 for ``email``, with what it said."""
    wrong: list[str] = []
    for method, path, body in (
        _meeting_routes(target["meeting"]) + _action_routes(target["action"])
    ):
        resp = await _call(c, email, method, path, body)
        if resp is None:
            wrong.append(f"{method} {path} -> no answer in {_WAIT_S}s (an open stream)")
        elif resp.status_code != 404:
            wrong.append(f"{method} {path} -> {resp.status_code}")
    return wrong


def _touched(world: Any, target: dict[str, str]) -> dict[str, list]:
    """Each fake that ran for the target's meeting or bot."""
    from gateway.routes.notes import agenda_progress, live_speakers, live_transcript

    mid, bot = target["meeting"], target["bot"]
    calls = world.calls
    out = {
        "refresh": [b for b in calls["refresh"] if b == bot],
        "spawn": list(calls["spawn"]),
        "start": [m for m in calls["start"] if m == mid],
        "stop": [m for m in calls["stop"] if m == mid],
        "draft": list(calls["draft"]),
        "speakers": list(calls["speakers"]),
        "provider": calls["leave"] + calls["say"] + calls["diagnostics"]
        + calls["screenshot"],
        "live_bus": list(live_transcript._BUSES[mid].ring)
        if mid in live_transcript._BUSES else [],
        "roster": [mid] if mid in live_speakers._REGISTRIES else [],
        "said": [mid] if mid in agenda_progress._TEXT else [],
    }
    return {k: v for k, v in out.items() if v}


# ── The refusals ───────────────────────────────────────────────────────────


async def test_r8_a_colleague_gets_404_on_every_route_and_changes_nothing(world) -> None:
    before = _state(world, world.alice["meeting"])
    async with _client(world, world.org_b) as c:
        wrong = await _refused(c, BOB, world.alice)
    assert wrong == [], f"Bob reached Alice's meeting on: {wrong}"
    assert _state(world, world.alice["meeting"]) == before
    assert _touched(world, world.alice) == {}


async def test_r8_another_org_gets_404_on_every_route_and_changes_nothing(world) -> None:
    before = _state(world, world.alice["meeting"])
    async with _client(world, world.org_a) as c:
        wrong = await _refused(c, CAROL, world.alice)
    assert wrong == [], f"Carol (org A) reached Alice's meeting on: {wrong}"
    assert _state(world, world.alice["meeting"]) == before
    assert _touched(world, world.alice) == {}


async def test_r8_the_lists_of_a_colleague_do_not_name_the_meeting(world) -> None:
    alice_mid = world.alice["meeting"]
    for org, email, own in ((world.org_b, BOB, world.bob), (world.org_a, CAROL, world.carol)):
        async with _client(world, org) as c:
            bots = await _call(c, email, "GET", "/notes/bots/active")
            live = await _call(c, email, "GET", "/notes/live/sessions")
        assert bots is not None and bots.status_code == 200, bots
        assert live is not None and live.status_code == 200, live
        bot_meetings = {b["meeting_id"] for b in bots.json()}
        live_meetings = {s["meeting_id"] for s in live.json()}
        assert alice_mid not in bot_meetings, f"{email} lists Alice's bot"
        assert alice_mid not in live_meetings, f"{email} lists Alice's live session"
        # The member still sees the own meeting.
        assert own["meeting"] in bot_meetings
        assert own["meeting"] in live_meetings
    # The poll-on-read of the two colleagues never refreshed Alice's bot.
    assert world.alice["bot"] not in world.calls["refresh"]


# ── The owner ──────────────────────────────────────────────────────────────


async def test_r8_the_owner_still_reads_every_route(world) -> None:
    mid, aid, bot = world.alice["meeting"], world.alice["action"], world.alice["bot"]
    base = f"/notes/meetings/{mid}"
    async with _client(world, world.org_b) as c:
        async def ok(method: str, path: str, body: dict | None = None,
                     code: int = 200) -> Any:
            resp = await _call(c, ALICE, method, path, body)
            assert resp is not None, f"{method} {path} did not answer"
            assert resp.status_code == code, f"{method} {path}: {resp.text}"
            ctype = resp.headers.get("content-type", "")
            return resp.json() if "json" in ctype else resp.content

        assert (await ok("GET", base))["title"] == "Alice pricing"
        assert (await ok("GET", f"{base}/note"))["notes_md"] == "secret note"
        assert [a["id"] for a in await ok("GET", f"{base}/actions")] == [aid]
        # Bound reads: under FORCE RLS an unbound session sees no row.
        agenda = await ok("GET", f"{base}/agenda")
        assert agenda == {"agenda": [{"title": "Pilot pricing", "notes": ""}]}
        progress = await ok("GET", f"{base}/agenda/progress")
        assert progress["total"] == 1
        assert [i["title"] for i in progress["items"]] == ["Pilot pricing"]
        context = await ok("GET", f"{base}/context")
        assert context["brief"] == "Close the Acme deal"
        assert context["attendees"] == ["Dev"]
        assert (await ok("GET", f"{base}/bot"))["id"] == bot
        assert world.calls["refresh"] == [bot], "the owner's poll-on-read changed"
        assert (await ok("GET", f"{base}/bot/diagnostics"))["diagnostics"] == {
            "page": "green room"}
        assert await ok("GET", f"{base}/bot/screenshot") == b"\x89PNG"
        assert (await ok("GET", f"{base}/live/session"))["meeting_id"] == mid
        assert (await ok("GET", f"{base}/live/roster")) == {"speakers": []}
        events = await ok("GET", f"{base}/events")
        assert b'"status": "ready"' in events
        assert (await ok("GET", f"{base}/copilot/events")) == {"events": []}
        bots = await ok("GET", "/notes/bots/active")
        assert mid in {b["meeting_id"] for b in bots}
        live = await ok("GET", "/notes/live/sessions")
        assert mid in {s["meeting_id"] for s in live}

        # The writes of the owner still land.
        await ok("PUT", f"{base}/note", {"notes_md": "mine"})
        await ok("PUT", f"{base}/agenda", {"agenda": [{"title": "Scope"}]})
        assert (await ok("POST", f"{base}/agenda/chat", {"message": "x"}))[
            "reply"] == "drafted"
        await ok("PUT", f"{base}/brief", {"brief": "New brief"})
        await ok("POST", f"{base}/context/deep", {"enabled": True})
        await ok("POST", f"{base}/live/copilot", {"enabled": True})
        await ok("POST", f"{base}/live/browser-segment", {"text": "pricing"}, code=202)
        await ok("POST", f"{base}/say", {"text": "hello"}, code=202)
        named = await ok("POST", f"{base}/identify-speakers")
        assert named == {"names": {"S1": "Alice", "S2": "Dev"}, "detected": {"S2": "Dev"}}
        await ok("POST", f"{base}/bot/stop", code=202)

    state = _state(world, mid)
    assert state["note"][0]["notes_md"] == "mine"
    assert state["meeting"][0]["copilot_brief"] == "New brief"
    assert state["live"][0]["copilot_enabled"] is True
    assert state["live"][0]["deep_context"] is True
    assert state["bot"][0]["status"] == "processing"
    assert state["meeting"][0]["speaker_names"] == {"S1": "Alice", "S2": "Dev"}
    assert world.calls["start"] == [mid]
    assert world.calls["say"] and world.calls["leave"]
    from gateway.routes.notes import live_transcript

    assert [s["text"] for s in live_transcript._BUSES[mid].ring] == ["pricing"]


async def test_r8_a_meeting_with_no_owner_stays_open_to_its_org_only(world) -> None:
    """The legacy NULL-owner arm is part of the one rule (owner, 2026-11-26)."""
    base = f"/notes/meetings/{world.legacy['meeting']}"
    async with _client(world, world.org_b) as c:
        for path in (f"{base}/note", f"{base}/agenda", f"{base}/live/session"):
            resp = await _call(c, BOB, "GET", path)
            assert resp is not None and resp.status_code == 200, (path, resp)
    async with _client(world, world.org_a) as c:
        for path in (f"{base}/note", f"{base}/agenda", f"{base}/live/session"):
            resp = await _call(c, CAROL, "GET", path)
            assert resp is not None and resp.status_code == 404, (path, resp)


# ── The request paths that write (verifier finding F1) ─────────────────────


async def test_r8_the_owner_dispatch_and_summarize_write_in_the_bound_tenant(world) -> None:
    """The task write, the mark and the summary run of a member request run
    in the tenant of that request. On an unbound session FORCE RLS refuses
    each one, so the owner's own action stayed a draft."""
    mid, aid = world.alice["meeting"], world.alice["action"]
    async with _client(world, world.org_b) as c:
        one = await _call(c, ALICE, "POST", f"/notes/actions/{aid}/dispatch")
        bulk = await _call(c, BOB, "POST",
                           f"/notes/meetings/{world.bob['meeting']}/actions/approve-all",
                           {"min_confidence": 0.5})
        run = await _call(c, ALICE, "POST", f"/notes/meetings/{mid}/summarize")
    assert one is not None and one.status_code == 200, one and one.text
    assert one.json()["dispatch_error"] is None, one.json()
    assert one.json()["status"] == "created"
    assert bulk is not None and bulk.status_code == 200, bulk and bulk.text
    assert bulk.json()["created"] == [world.bob["action"]]
    assert run is not None and run.status_code == 202, run and run.text
    with world.p.admin_engine.connect() as c:
        mine = c.execute(text(
            "SELECT a.status, a.dispatch_ref, t.organization_id::text AS org "
            "FROM action_item a JOIN pm_tasks t ON t.id::text = a.dispatch_ref "
            "WHERE a.id = CAST(:a AS uuid)"), {"a": aid}).fetchone()
        bobs = c.execute(text(
            "SELECT status FROM action_item WHERE id = CAST(:a AS uuid)"),
            {"a": world.bob["action"]}).scalar_one()
        queued = c.execute(text(
            "SELECT organization_id::text FROM summary_run "
            "WHERE id = CAST(:r AS uuid) AND kind = 'summary'"),
            {"r": run.json()["run_id"]}).scalar_one()
    assert mine is not None and mine.status == "created" and mine.org == world.org_b
    assert bobs == "created"
    assert queued == world.org_b
    assert len(world.calls["summary"]) == 1


async def test_r8_the_poll_on_read_of_the_owner_writes_in_the_bound_tenant(
    world, monkeypatch,
) -> None:
    """The real ``_refresh_bot`` runs. A worker that reports a failed join
    lands on the owner's rows, and the poll-on-read of Bob moves only his own
    bots."""
    from gateway.routes.notes import meeting_bot

    seen: list[str] = []
    report = {"status": "failed"}

    async def status(_self: Any, bot_id: str) -> tuple:
        seen.append(bot_id)
        return report["status"], None, "The host did not admit the notetaker."

    monkeypatch.setattr(meeting_bot, "_refresh_bot", world.real_refresh)
    monkeypatch.setattr(meeting_bot.SelfHostedProvider, "status", status)
    mid = world.alice["meeting"]
    async with _client(world, world.org_b) as c:
        got = await _call(c, ALICE, "GET", f"/notes/meetings/{mid}/bot")
        report["status"] = "waiting_room"
        listed = await _call(c, BOB, "GET", "/notes/bots/active")
    assert got is not None and got.status_code == 200, got and got.text
    assert got.json()["status"] == "failed"
    assert got.json()["error"] == "The host did not admit the notetaker."
    state = _state(world, mid)
    assert state["bot"][0]["status"] == "failed"
    assert state["meeting"][0]["status"] == "failed"
    assert state["live"][0]["status"] == "ended"
    assert listed is not None and listed.status_code == 200
    assert _state(world, world.bob["meeting"])["bot"][0]["status"] == "waiting_room"
    assert _state(world, world.carol["meeting"])["bot"][0]["status"] == "in_call"
    # Bob's poll never asked the worker about Alice's bot.
    alice_pb = f"pb-{world.alice['bot'][:8]}"
    assert seen.count(alice_pb) == 1


# ── /live/wanted cannot read a meeting with no bound tenant (task 4120) ─────


async def test_r8_the_unbound_live_wanted_read_does_not_see_the_meeting(
    world,
) -> None:
    """Task 4120 keeps ``GET /live/wanted`` gated, and this case is the why.

    The worker has no member, so the route calls ``live_wanted`` with no
    ``db``. That read runs in an unbound session, and FORCE RLS hides the
    meeting and its settings. Alice set ``never``. The bound read answers
    "switched off". The unbound read must not give that answer, because it
    cannot see her row. If this case goes red, the unbound read crosses the
    tenant wall, and that is a leak, not a repair."""
    from acb_common.db import tenant_session
    from gateway.routes.notes import live

    mid = world.alice["meeting"]
    with world.p.admin_engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO copilot_config (organization_id, owner_email, "
                "live_transcription) VALUES (CAST(:o AS uuid), :e, 'never')"
            ),
            {"o": world.org_b, "e": ALICE},
        )
    url = world.p.app_url.render_as_string(hide_password=False)
    async with tenant_engine_scope(url):
        token = bind_tenant(world.org_b)
        try:
            async with tenant_session() as db:
                bound = await live.live_wanted(mid, db)
        finally:
            release_tenant(token)
        unbound = await live.live_wanted(mid)
    assert bound[0] is False and "switched off" in bound[1], bound
    assert "switched off" not in unbound[1], (
        f"the unbound read saw Alice's setting: {unbound}"
    )


def test_this_suite_is_in_the_r8_must_run_list() -> None:
    assert "tests/unit/test_notes_meeting_owner_r8.py" in r8_lists.must_run()
