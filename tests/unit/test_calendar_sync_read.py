"""WS-CAL CE-10a — Google Calendar read and the shared sync core (R8).

Spec: ``project-docs/specs/calendar_events_reminders.md`` §8.1, §9, §10 · D138.
Task 62. The core is ``gateway/calendar_sync/``. The Google provider answers
from RECORDED replies (``tests/fixtures/calendar_sync/``). No test calls Google.

The unit tests need no database. The R8 tests run through the REAL engine, the
REAL writer and the REAL ``/tasks`` router on a phase-4 promoted scratch
database, as the non-privileged app role, so FORCE RLS binds. Two orgs.

Run (a PRIVATE ladder database, one R8 suite at a time)::

    eval "$(bash scripts/dev_db.sh --export)"
    export TENANT_LADDER_DATABASE_URL="${TENANT_LADDER_DATABASE_URL%/*}/<private db>"
    uv run pytest tests/unit/test_calendar_sync_read.py -v -rs

**Acceptance (task 62).** With recorded Google replies a sync writes each
instance once, and a second sync adds nothing. A disconnect deletes the
external rows of the connection and keeps the member's own events. Org B sees
no row of org A. The flag off sends no request.

**The mutations (R7).** Each turns the named test red. Commit before each.

* M1. ``writer.py``: drop the series skip (``own_masters``). An instance of a
  written-out series was stored.
  ``test_r8_an_instance_of_a_written_out_series_is_skipped``.
* M2. ``engine.py``: drop the ``flags.read_allowed`` check. A flag-off org
  sent a request. ``test_r8_the_flag_off_sends_no_request``.
* M3. ``connections.py``: drop step 2 of ``disconnect_connection`` (the clear
  of the own rows). The delete of the connection failed on the key of 331.
  ``test_r8_a_disconnect_deletes_the_external_rows_and_keeps_the_own_ones``.
* M4. ``writer.py``: drop ``organization_id`` from ``ON CONFLICT``. Fails to
  parse, so the sync wrote nothing.
  ``test_r8_a_sync_writes_each_instance_once_and_a_second_adds_nothing``.
* M5. ``calendar_connections.py``: drop the owner check in ``_own``. Bob
  disconnected Ana. ``test_r8_a_member_cannot_touch_the_connection_of_another``.
* M7. ``google.py``: drop the ``truncated`` check in ``_call``. A cut page
  read as an empty feed and the writer pruned every row.
  ``test_a_page_cut_by_the_size_cap_is_asked_again_smaller_and_never_pruned``.
* M6. ``engine.py``: drop the cursor compare before the write. Two syncs that
  started on one cursor both wrote.
  ``test_r8_a_sync_that_lost_the_race_writes_nothing``.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from gateway.calendar_sync import flags, google
from gateway.calendar_sync.http import CalendarHttp, redact_cursors
from gateway.calendar_sync.provider import (
    CursorExpired,
    NeedsReconnect,
    OutEvent,
    ProviderError,
    Window,
    clean_title,
)
from gateway.crm_sources.base import OAuthClientConfig, SourceCredential
from gateway.kb_sources.http import HttpAnswer

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "calendar_sync"
CLIENT = OAuthClientConfig(
    client_id="cid.apps.googleusercontent.com", client_secret="r8-secret",
    redirect_uri="https://app.test/api/email/oauth/gmail/callback")
WINDOW = Window(datetime(2026, 10, 15, tzinfo=UTC), datetime(2027, 5, 15, tzinfo=UTC))
NOW = datetime(2026, 11, 12, 12, 0, tzinfo=UTC)


def fixture(name: str) -> Any:
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def answer(body: Any, status: int = 200) -> HttpAnswer:
    return HttpAnswer(status_code=status, body=json.dumps(body).encode())


class FakeHttp:
    """Recorded Google replies. ``routes`` maps (METHOD, path suffix) to a
    list of answers, taken in order; the last one repeats."""

    def __init__(self) -> None:
        self.routes: dict[tuple[str, str], list[HttpAnswer]] = {}
        self.calls: list[tuple[str, str, Mapping[str, str] | None, Any]] = []

    def on(self, method: str, suffix: str, *answers: HttpAnswer) -> FakeHttp:
        self.routes[(method, suffix)] = list(answers)
        return self

    def _take(self, method: str, url: str) -> HttpAnswer:
        for (m, suffix), queue in self.routes.items():
            if m == method and url.split("?")[0].endswith(suffix):
                return queue.pop(0) if len(queue) > 1 else queue[0]
        raise AssertionError(f"no recorded reply for {method} {url}")

    async def get(self, url: str, *, params: Mapping[str, str] | None = None,
                  headers: Mapping[str, str] | None = None) -> HttpAnswer:
        self.calls.append(("GET", url, dict(params or {}), None))
        return self._take("GET", url)

    async def post_form(self, url: str, *, form: Mapping[str, str],
                        headers: Mapping[str, str] | None = None) -> HttpAnswer:
        self.calls.append(("POST", url, None, dict(form)))
        return self._take("POST", url)

    async def send_json(self, method: str, url: str, *, body: Any = None,
                        headers: Mapping[str, str] | None = None) -> HttpAnswer:
        self.calls.append((method, url, None, body))
        return self._take(method, url)


def feed(*, incremental: bool = True) -> FakeHttp:
    """The recorded Google feed: two pages, then an incremental read."""
    http = FakeHttp()
    http.on("GET", "/calendars/primary/events",
            answer(fixture("google_events_full_page1.json")),
            answer(fixture("google_events_full_page2.json")),
            *( [answer(fixture("google_events_incremental.json"))] if incremental else []))
    http.on("GET", "/calendars/primary", answer(fixture("google_calendar_primary.json")))
    return http


def cred(**kw: Any) -> SourceCredential:
    return SourceCredential(
        access_token="ya29.fake", refresh_token="1//fake",
        expires_at=datetime.now(UTC) + timedelta(hours=2),
        scopes=kw.pop("scopes", google.SCOPES), **kw)


def provider(http: FakeHttp) -> google.GoogleCalendar:
    return google.GoogleCalendar(CLIENT, http)  # type: ignore[arg-type]


# ── Unit: no database ──────────────────────────────────────────────────────


def test_the_consent_asks_the_calendar_scopes_with_incremental_consent() -> None:
    url = provider(FakeHttp()).consent_url(state="S", login_hint="ana@example.test")
    for scope in ("calendar.readonly", "calendar.app.created"):
        assert scope in url
    assert "gmail.readonly" not in url and "drive" not in url
    assert "access_type=offline" in url and "prompt=consent" in url
    # D215 point 1: incremental consent, and the mail redirect URI (point 2).
    assert "include_granted_scopes=true" in url
    assert "redirect_uri=https%3A%2F%2Fapp.test%2Fapi%2Femail%2Foauth%2Fgmail%2Fcallback" in url


def test_a_grant_without_the_read_scope_or_a_refresh_token_is_refused() -> None:
    assert google.grant_refusal(cred()) is None
    assert google.grant_refusal(cred(scopes=("openid",))) == "scope_missing"
    # An incremental grant lists the Gmail scopes too (D215 point 1).
    assert google.grant_refusal(cred(scopes=(*google.SCOPES, "https://mail.google.com/"))) is None
    bare = SourceCredential(access_token="a", refresh_token="", expires_at=NOW, scopes=google.SCOPES)
    assert google.grant_refusal(bare) == "no_refresh_token"


@pytest.mark.asyncio
async def test_a_sync_reads_every_page_and_maps_each_field() -> None:
    http = feed(incremental=False)
    result = await provider(http).sync(cred(), None, WINDOW)
    assert result.full and result.next_cursor == "SYNC-1"
    by = {e.external_id: e for e in result.changes}
    # The working-location block is skipped; the other seven are kept.
    assert set(by) == {"evt_standup", "evt_holiday", "evt_untitled", "evt_long",
                       "evt_series_20261120", "evt_series_20261127"}
    standup = by["evt_standup"]
    assert standup.starts_at == datetime(2026, 11, 16, 4, 0, tzinfo=UTC)
    assert standup.tz == "Asia/Kolkata" and standup.conference_url.startswith("https://meet.")
    holiday = by["evt_holiday"]
    assert (holiday.start_date, holiday.end_date) == (date(2026, 11, 8), date(2026, 11, 9))
    assert holiday.busy is False and holiday.starts_at is None
    assert by["evt_untitled"].status == "tentative"
    assert by["evt_series_20261120"].external_series_id == "evt_series"
    # The first read lists the window and sends no sync token (audit G3).
    first = http.calls[0][2]
    assert "timeMin" in first and "timeMax" in first and "syncToken" not in first
    assert first["singleEvents"] == "true" and first["showDeleted"] == "true"
    assert http.calls[1][2]["pageToken"] == "PAGE2"


@pytest.mark.asyncio
async def test_the_next_read_sends_the_sync_token_and_no_window() -> None:
    http = feed()
    prov = provider(http)
    await prov.sync(cred(), None, WINDOW)
    result = await prov.sync(cred(), "SYNC-1", WINDOW)
    params = http.calls[-1][2]
    assert params["syncToken"] == "SYNC-1"
    assert "timeMin" not in params and "timeMax" not in params
    assert not result.full and result.next_cursor == "SYNC-2"
    gone = [e for e in result.changes if e.deleted]
    assert [e.external_id for e in gone] == ["evt_holiday"]


@pytest.mark.asyncio
async def test_a_410_is_a_dead_cursor_and_a_401_needs_a_reconnect() -> None:
    http = FakeHttp().on("GET", "/events", answer(fixture("google_events_gone.json"), 410))
    with pytest.raises(CursorExpired):
        await provider(http).sync(cred(), "OLD", WINDOW)
    http = FakeHttp().on("GET", "/events", answer({"error": {"code": 401}}, 401))
    with pytest.raises(NeedsReconnect):
        await provider(http).sync(cred(), None, WINDOW)
    http = FakeHttp().on("GET", "/events", answer({"error": {"errors": [
        {"reason": "rateLimitExceeded"}]}}, 403))
    with pytest.raises(Exception) as err:
        await provider(http).sync(cred(), None, WINDOW)
    assert getattr(err.value, "code", "") == "rate_limited"


def test_a_title_is_never_empty_and_never_over_200() -> None:
    assert clean_title(None) == "(No title)" and clean_title("   ") == "(No title)"
    assert len(clean_title("x" * 500)) == 200
    assert clean_title("  a   b ") == "a b"


@pytest.mark.asyncio
async def test_the_write_out_makes_the_calendar_and_ignores_a_gone_event() -> None:
    http = (FakeHttp()
            .on("POST", "/calendars", answer({"id": "cal-metorite@group"}))
            .on("POST", "/calendars/cal-metorite%40group/events",
                answer({"id": "new1", "etag": "\"1\"", "htmlLink": "https://g/e"}))
            .on("DELETE", "/events/gone", answer({}, 410)))
    prov = provider(http)
    assert await prov.create_calendar(cred(), "Metorite") == "cal-metorite@group"
    out = await prov.upsert_event(cred(), "cal-metorite@group", OutEvent(
        title="Write spec", starts_at=NOW, ends_at=NOW + timedelta(hours=1),
        task_url="https://app/t/1"), None)
    assert out.external_id == "new1"
    method, url, _, body = http.calls[-1]
    assert method == "POST" and "sendUpdates=none" in url
    assert body["source"]["url"] == "https://app/t/1" and body["summary"] == "Write spec"
    await prov.delete_event(cred(), "cal-metorite@group", "gone")  # 410 is success


def test_the_flags_default_off_and_a_member_list_narrows(monkeypatch: Any) -> None:
    def settings(**kw: str) -> None:
        base = {"calendar_sync_read": "", "calendar_sync_write": "", "calendar_sync_members": ""}
        monkeypatch.setattr(flags, "get_settings", lambda: SimpleNamespace(**{**base, **kw}))

    settings()
    assert not flags.read_named() and not flags.read_allowed("org-a")
    settings(calendar_sync_read="org-a")
    assert flags.read_allowed("ORG-A") and not flags.read_allowed("org-b")
    assert not flags.read_allowed(None)
    assert not flags.write_allowed("org-a")  # read alone is not write
    settings(calendar_sync_read="*", calendar_sync_write="org-a",
             calendar_sync_members="ana@x.test")
    assert flags.read_allowed("org-z", "Ana@x.test") and not flags.read_allowed("org-z", "bob@x.test")
    assert flags.write_allowed("org-a", "ana@x.test") and not flags.write_allowed("org-z", "ana@x.test")


def test_a_cursor_never_reaches_a_log_line() -> None:
    line = "GET https://www.googleapis.com/calendar/v3/x?syncToken=SECRET123&a=1"
    assert "SECRET123" not in redact_cursors(line)
    assert "deltatoken" not in redact_cursors("u?$deltatoken=ABC").replace("$deltatoken=[redacted]", "")


@pytest.mark.asyncio
async def test_the_door_refuses_a_host_off_the_list_before_any_request() -> None:
    door = CalendarHttp(google.HOSTS)
    from gateway.kb_sources.base import UntrustedHost
    with pytest.raises(UntrustedHost):
        await door.get("https://evil.example/calendar/v3/x")
    with pytest.raises(UntrustedHost):
        await door.send_json("DELETE", "http://www.googleapis.com/calendar/v3/x")
    with pytest.raises(ValueError):
        await door.send_json("GET", "https://www.googleapis.com/x")


@pytest.mark.asyncio
async def test_the_door_asks_for_plain_bytes(monkeypatch) -> None:
    """The guard keeps raw bytes, so a gzip answer would read as an empty feed
    (Opus review P0). The door must send ``Accept-Encoding: identity``."""
    from types import SimpleNamespace

    from gateway.calendar_sync import http as door_mod
    seen: dict[str, Any] = {}

    async def fake(method, url, *, headers=None, content=None, json=None):
        seen["headers"] = dict(headers or {})
        return SimpleNamespace(status_code=200, body=b"{}")

    monkeypatch.setattr(door_mod.outbound_guard, "request_https", fake)
    await CalendarHttp(google.HOSTS).get("https://www.googleapis.com/calendar/v3/x",
                                         headers={"Accept-Encoding": "gzip"})
    assert seen["headers"]["Accept-Encoding"] == "identity"


def test_the_calendar_connection_is_in_the_purge_after_the_entries() -> None:
    src = (Path(__file__).resolve().parents[2] / "apps" / "services" / "gateway" / "gateway"
           / "routes" / "admin" / "members.py").read_text(encoding="utf-8")
    assert "purge_calendar_member" in src
    assert src.index("purge_calendar_member") > src.index('_PersonRows("calendar_entries"')


# ── R8: a real database ───────────────────────────────────────────────────

import httpx  # noqa: E402
from acb_auth import UserContext, UserRole, build_access, get_current_user  # noqa: E402
from acb_common.db import bind_tenant, release_tenant  # noqa: E402
from fastapi import FastAPI, HTTPException, Request  # noqa: E402
from sqlalchemy import text  # noqa: E402

from tests.unit._tenant_ladder import tenant_engine_scope  # noqa: E402
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: E402, F401
    _DB_GATE,
    app_engine,
    promoted,
)

ANA = "ana@cal-sync.test"
BOB = "bob@cal-sync.test"
ZED = "zed@cal-sync.test"  # in org A


def _dsn(p: Any) -> str:
    return p.app_url.render_as_string(hide_password=False)


@pytest.fixture
def world(promoted, monkeypatch):  # noqa: F811
    """The switches on for both orgs, the key set, cleaned after."""
    import acb_llm.key_store as key_store
    from acb_common import get_settings
    from gateway.calendar_sync import scheduler

    settings = get_settings()
    for name, value in {
        "calendar_sync_read": "*", "calendar_sync_write": "", "calendar_sync_members": "",
        "gateway_session_secret": "r8-session-secret-not-a-default-0123456789",
        "acb_master_key": "r8-master-key-for-the-blob",
    }.items():
        monkeypatch.setattr(settings, name, value, raising=False)
    monkeypatch.setattr(key_store, "_store", None)
    http = feed()
    prov = provider(http)
    monkeypatch.setitem(scheduler.PROVIDERS, "google_calendar", lambda: prov)
    with promoted.admin_engine.begin() as c:
        for org, email in ((promoted.org_b, BOB), (promoted.org_b, ANA), (promoted.org_a, ZED)):
            c.execute(text(
                "INSERT INTO app_user (email, display_name, role, status, organization_id) "
                "SELECT :e, :e, 'employee', 'active', CAST(:o AS uuid) WHERE NOT EXISTS "
                "(SELECT 1 FROM app_user WHERE lower(email) = :e)"), {"e": email, "o": org})
    yield SimpleNamespace(p=promoted, org=promoted.org_b, other=promoted.org_a,
                          http=http, prov=prov)
    with promoted.admin_engine.begin() as c:
        who = [ANA, BOB, ZED]
        c.execute(text("DELETE FROM calendar_entries WHERE owner_email = ANY(:e)"), {"e": who})
        c.execute(text("DELETE FROM integration_connections WHERE member_email = ANY(:e)"), {"e": who})
        c.execute(text("DELETE FROM app_user WHERE email = ANY(:e)"), {"e": who})


async def _connect(w: Any, member: str, org: str | None = None) -> str:
    """Save a Google calendar connection through the REAL store."""
    from gateway.calendar_sync.provider import OWNING_APP
    from gateway.connections import store

    org = org or w.org
    async with tenant_engine_scope(_dsn(w.p)):
        from acb_common.db import tenant_session
        async with tenant_session(org) as db:
            conn = await store.save_credential(
                db, org, "google_calendar", OWNING_APP,
                cred(provider_meta={"account_email": member, "account_id": member}),
                connected_by=member, scope_kind=store.SCOPE_MEMBER,
                member_email=member, scopes=google.SCOPES)
    return conn.id


@asynccontextmanager
async def _scope(w: Any, org: str | None = None) -> AsyncIterator[None]:
    token = bind_tenant(org or w.org)
    try:
        async with tenant_engine_scope(_dsn(w.p)):
            yield
    finally:
        release_tenant(token)


async def _sync(w: Any, cid: str, org: str | None = None, **kw: Any) -> Any:
    from gateway.calendar_sync import engine

    async with tenant_engine_scope(_dsn(w.p)):
        return await engine.sync_connection(org or w.org, cid, w.prov, **kw)


def _rows(w: Any, member: str) -> dict[str, Any]:
    with w.p.admin_engine.connect() as c:
        rows = c.execute(text(
            "SELECT external_id, kind, source, title, status, busy, starts_at, start_date, "
            "end_date, etag, connection_id, organization_id::text AS org "
            "FROM calendar_entries WHERE owner_email = :m"), {"m": member}).fetchall()
    return {r.external_id or f"own-{i}": r for i, r in enumerate(rows)}


def _meta(w: Any, cid: str) -> Any:
    with w.p.admin_engine.connect() as c:
        return c.execute(text(
            "SELECT status, provider_meta, last_sync_at, last_error, credentials_encrypted "
            "FROM integration_connections WHERE id = CAST(:i AS uuid)"), {"i": cid}).one()


def _seed_own(w: Any, member: str, cid: str, *, ext: str | None = None) -> None:
    """A Metorite event that CE-11 wrote out: names the connection."""
    with w.p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO calendar_entries (organization_id, owner_email, kind, title, starts_at, "
            "ends_at, source, connection_id, external_id, push_to_provider, provider_sync_state) "
            "VALUES (CAST(:o AS uuid), :m, 'event', 'Mine', now(), now() + interval '1 hour', "
            "'metorite', CAST(:c AS uuid), :x, 'google', 'sent')"),
            {"o": w.org, "m": member, "c": cid, "x": ext or "evt_mine"})


@pytest.mark.asyncio
@_DB_GATE
async def test_r8_a_sync_writes_each_instance_once_and_a_second_adds_nothing(world) -> None:
    cid = await _connect(world, ANA)
    first = await _sync(world, cid)
    assert (first.status, first.added) == ("ok", 6), first
    rows = _rows(world, ANA)
    assert set(rows) == {"evt_standup", "evt_holiday", "evt_untitled", "evt_long",
                         "evt_series_20261120", "evt_series_20261127"}
    assert all(r.kind == "external" and r.source == "google" and r.org == world.org
               for r in rows.values())
    assert rows["evt_untitled"].title == "(No title)" and len(rows["evt_long"].title) == 200
    assert rows["evt_holiday"].busy is False and rows["evt_holiday"].start_date == date(2026, 11, 8)
    assert rows["evt_untitled"].status == "tentative"
    meta = _meta(world, cid)
    assert meta.provider_meta["sync_cursor"] == "SYNC-1" and meta.last_sync_at is not None
    assert meta.last_error is None
    # The token stays sealed in the one column and appears in no plain one.
    assert "ya29" not in json.dumps(meta.provider_meta)
    # A second sync reads the incremental feed: a move, and a cancel.
    second = await _sync(world, cid)
    assert (second.status, second.added, second.updated, second.removed) == ("ok", 0, 1, 1)
    rows = _rows(world, ANA)
    assert rows["evt_standup"].title == "Standup (moved)" and "evt_holiday" not in rows
    assert len(rows) == 5
    # A third sync of the same feed adds nothing (same etag).
    world.http.routes[("GET", "/calendars/primary/events")] = [
        answer(fixture("google_events_incremental.json"))]
    third = await _sync(world, cid)
    assert (third.added, third.updated) == (0, 0)
    assert len(_rows(world, ANA)) == 5


@pytest.mark.asyncio
@_DB_GATE
async def test_r8_a_410_lists_the_window_again_and_prunes_what_is_gone(world) -> None:
    cid = await _connect(world, ANA)
    await _sync(world, cid)
    with world.p.admin_engine.begin() as c:  # a row that the feed no longer lists
        c.execute(text(
            "INSERT INTO calendar_entries (organization_id, owner_email, kind, title, starts_at, "
            "ends_at, source, connection_id, external_id) VALUES (CAST(:o AS uuid), :m, "
            "'external', 'Old', now(), now(), 'google', CAST(:c AS uuid), 'evt_old')"),
            {"o": world.org, "m": ANA, "c": cid})
    world.http.routes[("GET", "/calendars/primary/events")] = [
        answer(fixture("google_events_gone.json"), 410),
        answer(fixture("google_events_full_page1.json")),
        answer(fixture("google_events_full_page2.json"))]
    out = await _sync(world, cid)
    assert out.status == "ok"
    assert "evt_old" not in _rows(world, ANA) and len(_rows(world, ANA)) == 6
    assert _meta(world, cid).provider_meta["sync_cursor"] == "SYNC-1"


@pytest.mark.asyncio
@_DB_GATE
async def test_r8_an_instance_of_a_written_out_series_is_skipped(world) -> None:
    cid = await _connect(world, ANA)
    _seed_own(world, ANA, cid, ext="evt_series")  # the master that Metorite wrote out
    out = await _sync(world, cid)
    assert out.status == "ok"
    rows = _rows(world, ANA)
    assert "evt_series_20261120" not in rows and "evt_series_20261127" not in rows
    assert rows["evt_series"].kind == "event"  # untouched
    assert len(rows) == 5  # 4 external + the own master


@pytest.mark.asyncio
@_DB_GATE
async def test_r8_the_flag_off_sends_no_request(world, monkeypatch) -> None:
    from acb_common import get_settings

    cid = await _connect(world, ANA)
    monkeypatch.setattr(get_settings(), "calendar_sync_read", "", raising=False)
    out = await _sync(world, cid)
    assert out.status == "skipped" and world.http.calls == []
    # Named for the other org only: still no request for this one.
    monkeypatch.setattr(get_settings(), "calendar_sync_read", world.other, raising=False)
    out = await _sync(world, cid)
    assert out.status == "skipped" and world.http.calls == []
    # The member list narrows it.
    monkeypatch.setattr(get_settings(), "calendar_sync_read", "*", raising=False)
    monkeypatch.setattr(get_settings(), "calendar_sync_members", BOB, raising=False)
    out = await _sync(world, cid)
    assert out.status == "skipped" and world.http.calls == []
    assert _rows(world, ANA) == {}


@pytest.mark.asyncio
@_DB_GATE
async def test_r8_the_loop_runs_a_due_connection_once_and_skips_a_dead_one(world) -> None:
    from gateway.calendar_sync import scheduler

    live = await _connect(world, ANA)
    dead = await _connect(world, BOB)
    with world.p.admin_engine.begin() as c:
        c.execute(text("UPDATE integration_connections SET status='needs_reconnect' "
                       "WHERE id = CAST(:i AS uuid)"), {"i": dead})
    async with tenant_engine_scope(_dsn(world.p)):
        work = [w for w in await scheduler.due_work() if w[1] in (live, dead)]
        assert work == [(world.org, live, "google_calendar")]
        assert await scheduler.run_tick() >= 1
        calls = len(world.http.calls)
        # Just synced: not due for 60 s, so the next tick sends nothing.
        assert [w for w in await scheduler.due_work() if w[1] == live] == []
    assert calls > 0 and _rows(world, BOB) == {}


@pytest.mark.asyncio
@_DB_GATE
async def test_r8_a_sync_that_lost_the_race_writes_nothing(world, monkeypatch) -> None:
    from gateway.calendar_sync import connections

    cid = await _connect(world, ANA)
    real = world.prov.sync

    async def slow(c: Any, cursor: Any, window: Any) -> Any:
        result = await real(c, cursor, window)
        # Another worker wrote its cursor while this read was in flight.
        with world.p.admin_engine.begin() as conn:
            conn.execute(text(
                "UPDATE integration_connections SET provider_meta = provider_meta || "
                "'{\"sync_cursor\": \"OTHER\"}' WHERE id = CAST(:i AS uuid)"), {"i": cid})
        return result

    monkeypatch.setattr(world.prov, "sync", slow)
    out = await _sync(world, cid)
    assert out.status == "busy" and _rows(world, ANA) == {}
    assert connections.cursor  # the helper exists


@pytest.mark.asyncio
@_DB_GATE
async def test_r8_a_dead_refresh_token_marks_the_row_and_is_not_retried(world, monkeypatch) -> None:
    cid = await _connect(world, ANA)
    with world.p.admin_engine.begin():  # an expired access token forces a refresh
        pass
    async def dead(self: Any, c: Any) -> Any:
        raise NeedsReconnect("revoked")
    monkeypatch.setattr(google.GoogleCalendar, "needs_refresh", lambda s, c, n: True)
    monkeypatch.setattr(google.GoogleCalendar, "refresh", dead)
    out = await _sync(world, cid)
    assert out.status == "needs_reconnect" and _meta(world, cid).status == "needs_reconnect"
    again = await _sync(world, cid)
    assert again.status == "needs_reconnect" and world.http.calls == []


@pytest.mark.asyncio
@_DB_GATE
async def test_r8_a_disconnect_deletes_the_external_rows_and_keeps_the_own_ones(world) -> None:
    cid = await _connect(world, ANA)
    await _sync(world, cid)
    _seed_own(world, ANA, cid)
    async with _scope(world) as _:
        resp = await _call(world, "DELETE", f"/tasks/calendar/connections/{cid}", ANA)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"disconnected": True, "removed_entries": 6}
    rows = _rows(world, ANA)
    assert [r.kind for r in rows.values()] == ["event"]
    own = next(iter(rows.values()))
    assert own.connection_id is None and own.title == "Mine"
    with world.p.admin_engine.connect() as c:
        o = c.execute(text("SELECT external_id, push_to_provider, provider_sync_state, "
                           "provider_sync_error FROM calendar_entries WHERE owner_email=:m"),
                      {"m": ANA}).one()
    assert tuple(o) == (None, None, None, None)
    meta = _meta(world, cid)
    assert meta.status == "disconnected" and meta.credentials_encrypted is None
    assert "sync_cursor" not in meta.provider_meta
    # A disconnected row is never synced.
    assert (await _sync(world, cid)).status == "skipped"


@pytest.mark.asyncio
@_DB_GATE
async def test_r8_a_member_cannot_touch_the_connection_of_another(world) -> None:
    cid = await _connect(world, ANA)
    await _sync(world, cid)
    async with _scope(world) as _:
        for method, path in (("DELETE", f"/tasks/calendar/connections/{cid}"),
                             ("POST", f"/tasks/calendar/connections/{cid}/sync")):
            resp = await _call(world, method, path, BOB)
            assert resp.status_code == 404, (method, resp.text)
        listing = (await _call(world, "GET", "/tasks/calendar/connections", BOB)).json()
    assert listing["connections"] == []
    assert len(_rows(world, ANA)) == 6 and _meta(world, cid).status == "connected"


@pytest.mark.asyncio
@_DB_GATE
async def test_r8_org_b_sees_no_row_of_org_a(world) -> None:
    mine = await _connect(world, ANA)  # org B
    theirs = await _connect(world, ZED, org=world.other)  # org A
    await _sync(world, theirs, org=world.other)
    await _sync(world, mine)
    assert {r.org for r in _rows(world, ZED).values()} == {world.other}
    # Org B, bound by RLS as the app role, reads none of org A's rows.
    async with _scope(world) as _:
        from acb_common.db import tenant_session
        async with tenant_session(world.org) as db:
            seen = (await db.execute(text(
                "SELECT count(*) AS n FROM calendar_entries WHERE owner_email = :z"),
                {"z": ZED})).first().n
            conns = (await db.execute(text(
                "SELECT count(*) AS n FROM integration_connections WHERE member_email = :z"),
                {"z": ZED})).first().n
        assert (seen, conns) == (0, 0)
        # A sync of org A's connection id from org B finds no connection.
        from gateway.calendar_sync import engine
        out = await engine.sync_connection(world.org, theirs, world.prov)
        assert out.status == "skipped"
        resp = await _call(world, "DELETE", f"/tasks/calendar/connections/{theirs}", ANA)
        assert resp.status_code == 404
    assert len(_rows(world, ZED)) == 6


@pytest.mark.asyncio
@_DB_GATE
async def test_r8_every_calendar_route_is_a_404_with_the_flag_off(world, monkeypatch) -> None:
    from acb_common import get_settings

    cid = await _connect(world, ANA)
    monkeypatch.setattr(get_settings(), "calendar_sync_read", "", raising=False)
    async with _scope(world) as _:
        for method, path in (("GET", "/tasks/calendar/connections"),
                             ("POST", "/tasks/calendar/connections/google/connect"),
                             ("POST", f"/tasks/calendar/connections/{cid}/sync"),
                             ("DELETE", f"/tasks/calendar/connections/{cid}")):
            resp = await _call(world, method, path, ANA)
            assert resp.status_code == 404, (method, path)
    assert world.http.calls == []


@pytest.mark.asyncio
@_DB_GATE
async def test_r8_connect_signs_a_state_and_the_callback_refuses_a_forged_one(world) -> None:
    async with _scope(world) as _:
        url = (await _call(world, "POST", "/tasks/calendar/connections/google/connect", ANA)).json()["url"]
        assert url.startswith("https://accounts.google.com/")
        from urllib.parse import parse_qs, urlsplit
        state = parse_qs(urlsplit(url).query)["state"][0]
        from gateway.routes.tasks import calendar_connections as cal
        ana, bob = _user(world, ANA), _user(world, BOB)
        # The mail callback asks this first. A forged state is not calendar.
        assert not cal.is_calendar_state("zzz", "google", ana)
        with pytest.raises(HTTPException) as bad:
            await cal.finish_consent(ana, "google", "c", "zzz", None)
        assert bad.value.status_code == 400
        # A state for ANOTHER member is refused, and Google is never called.
        assert not cal.is_calendar_state(state, "google", bob)
        with pytest.raises(HTTPException) as other:
            await cal.finish_consent(bob, "google", "c", state, None)
        assert other.value.status_code == 400
        # A calendar state is not an Outlook state.
        assert not cal.is_calendar_state(state, "outlook", ana)
        assert world.http.calls == []
        # The good path: the code is exchanged, the row saved, the browser sent back.
        assert cal.is_calendar_state(state, "google", ana)
        world.http.on("POST", "/token", answer(fixture("google_token.json")))
        good = await cal.finish_consent(ana, "google", "c", state, None, base="https://app.test")
    assert good.status_code == 302
    assert good.headers["location"] == "https://app.test/calendar?calendar_connected=google"
    with world.p.admin_engine.connect() as c:
        row = c.execute(text(
            "SELECT owning_app, scope_kind, member_email, status, provider_meta, scopes "
            "FROM integration_connections WHERE member_email = :m AND provider = 'google_calendar'"),
            {"m": ANA}).one()
    assert (row.owning_app, row.scope_kind, row.status) == ("calendar", "member", "connected")
    assert row.provider_meta["account_email"] == "ana@example.test"
    assert google.SCOPE_READ in row.scopes


@pytest.mark.asyncio
@_DB_GATE
async def test_r8_a_leaver_purge_deletes_the_connection_after_the_entries(world) -> None:
    from acb_common.db import tenant_session
    from gateway.calendar_sync.connections import purge_calendar_member

    cid = await _connect(world, ANA)
    await _sync(world, cid)
    _seed_own(world, ANA, cid)
    async with tenant_engine_scope(_dsn(world.p)), tenant_session(world.org) as db:
        # The purge list deletes the member's entries first.
        await db.execute(text(
            "DELETE FROM calendar_entries WHERE lower(owner_email) = :e"), {"e": ANA})
        assert await purge_calendar_member(db, world.org, ANA) == 1
    with world.p.admin_engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM integration_connections WHERE id = "
                              "CAST(:i AS uuid)"), {"i": cid}).scalar_one() == 0


def _user(w: Any, member: str) -> UserContext:
    return UserContext(email=member, role=UserRole.EMPLOYEE,
                       access=build_access(["feature:tasks"]), organization_id=w.org)


async def _call(w: Any, method: str, path: str, member: str) -> Any:
    from gateway.routes.tasks import router as tasks_router

    people = {m: UserContext(email=m, role=UserRole.EMPLOYEE,
                             access=build_access(["feature:tasks"]),
                             organization_id=w.org) for m in (ANA, BOB)}

    async def who(request: Request) -> Any:
        return people[request.headers["x-user-email"].lower()]

    app = FastAPI()
    app.include_router(tasks_router)
    app.dependency_overrides[get_current_user] = who
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://gateway.test") as client:
        return await client.request(method, path, headers={"x-user-email": member},
                                    follow_redirects=False)


# ── A cut page is never a read (Opus review P0) ───────────────────────────


@pytest.mark.asyncio
async def test_a_page_cut_by_the_size_cap_is_asked_again_smaller_and_never_pruned() -> None:
    full = fixture("google_events_full_page2.json")  # has the sync token
    cut = HttpAnswer(status_code=200, body=b'{"items": [{"id": "a"', truncated=True)
    http = FakeHttp().on("GET", "/events", cut, answer(full))
    result = await provider(http).sync(cred(), None, WINDOW)
    sizes = [int(c[2]["maxResults"]) for c in http.calls]
    assert sizes == [100, 50] and result.next_cursor == "SYNC-1"
    assert "fields" in http.calls[0][2]
    # Cut at every size: the read FAILS. It returns no empty feed.
    http = FakeHttp().on("GET", "/events", cut)
    with pytest.raises(ProviderError) as err:
        await provider(http).sync(cred(), None, WINDOW)
    assert err.value.code == "page_too_large"


@pytest.mark.asyncio
async def test_a_last_page_without_a_sync_token_is_not_a_finished_read() -> None:
    http = FakeHttp().on("GET", "/events", answer({"items": []}))
    with pytest.raises(ProviderError) as err:
        await provider(http).sync(cred(), None, WINDOW)
    assert err.value.code == "no_sync_token"
    http = FakeHttp().on("GET", "/events", HttpAnswer(status_code=200, body=b"<html>"))
    with pytest.raises(ProviderError):
        await provider(http).sync(cred(), None, WINDOW)
