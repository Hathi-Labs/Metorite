"""WS-CAL CE-10b — Outlook Calendar read on the shared core (R8, two orgs).

Task 61. Spec ``calendar_events_reminders.md`` §8.1. The Outlook provider
answers from RECORDED Graph replies (``tests/unit/fixtures/outlook_calendar``).
No test calls Microsoft. The R8 tests run the REAL engine and writer as the
non-privileged app role, so FORCE RLS binds. Run as
``tests/unit/test_calendar_sync_read.py`` does (private ladder database).

Mutations (R7), each turns the named test red:

* M1. ``outlook.py`` ``_to_changes``: drop the ``@removed`` ids.
  ``test_r8_outlook_second_sync_updates_and_a_removed_event_goes``.
* M2. ``outlook.py`` ``pick_calendar_ids`` use: read the "Metorite" calendar.
  ``test_r8_outlook_never_reads_the_metorite_calendar``.
* M3. ``outlook_graph.py`` ``_status_and_busy``: ignore ``showAs=free``.
  ``test_r8_outlook_first_sync_writes_each_instance_once``.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_common.db import tenant_session  # noqa: E402
from sqlalchemy import text  # noqa: E402

from gateway.calendar_sync import outlook  # noqa: E402
from gateway.calendar_sync.provider import OWNING_APP  # noqa: E402
from gateway.crm_sources.base import SourceCredential  # noqa: E402
from tests.unit._tenant_ladder import tenant_engine_scope  # noqa: E402
from tests.unit.test_calendar_sync_read import (  # noqa: E402, F401
    ANA, BOB, ZED, FakeHttp, _DB_GATE, _dsn, _meta, _rows, _scope, answer,
    app_engine, promoted,
)

FIX = Path(__file__).parent / "fixtures" / "outlook_calendar"
NOW = datetime(2026, 11, 12, 12, 0, tzinfo=UTC)
CAL_LIST = {"value": [{"id": "AAA", "name": "Calendar", "isDefaultCalendar": True},
                      {"id": "CCC", "name": "Metorite"}]}
DELTA = "https://graph.microsoft.com/v1.0/me/calendars/AAA/calendarView/delta?$deltatoken="


def fx(name: str) -> Any:
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def incremental() -> dict:
    return {"@odata.deltaLink": DELTA + "SECOND", "value": [
        {"@odata.etag": "W/\"new\"", "id": "EV1", "subject": "Standup (moved)",
         "isAllDay": False, "showAs": "busy", "seriesMasterId": "MASTER1",
         "start": {"dateTime": "2026-11-13T10:00:00.0000000", "timeZone": "UTC"},
         "end": {"dateTime": "2026-11-13T10:15:00.0000000", "timeZone": "UTC"}},
        {"id": "EV3", "@removed": {"reason": "deleted"}}]}


def feed() -> FakeHttp:
    http = FakeHttp()
    http.on("GET", "/me/calendars", answer(CAL_LIST))
    http.on("GET", "/calendarView/delta", answer(fx("delta_page1.json")),
            answer(fx("delta_page2.json")), answer(incremental()))
    return http


@pytest.fixture
def world(promoted, monkeypatch):  # noqa: F811
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
    prov = outlook.OutlookProvider(
        http, client_id="cid", client_secret="sec",  # type: ignore[arg-type]
        redirect_uri="https://app.test/api/calendar/oauth/microsoft/callback",
        clock=lambda: NOW)
    monkeypatch.setitem(scheduler.PROVIDERS, "outlook_calendar", lambda: prov)
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
    from gateway.connections import store

    org = org or w.org
    cred = SourceCredential(
        access_token="EwB-fake", refresh_token="M.R3-fake",
        expires_at=datetime.now(UTC) + timedelta(hours=2),
        scopes=outlook.SCOPES, provider_meta={"account_email": member, "account_id": member})
    async with tenant_engine_scope(_dsn(w.p)):
        async with tenant_session(org) as db:
            conn = await store.save_credential(
                db, org, "outlook_calendar", OWNING_APP, cred, connected_by=member,
                scope_kind=store.SCOPE_MEMBER, member_email=member, scopes=outlook.SCOPES)
    return conn.id


async def _sync(w: Any, cid: str, org: str | None = None, **kw: Any) -> Any:
    from gateway.calendar_sync import engine

    async with tenant_engine_scope(_dsn(w.p)):
        return await engine.sync_connection(org or w.org, cid, w.prov, **kw)


@pytest.mark.asyncio
@_DB_GATE
async def test_r8_outlook_first_sync_writes_each_instance_once(world) -> None:
    cid = await _connect(world, ANA)
    first = await _sync(world, cid)
    assert (first.status, first.added) == ("ok", 5), first
    rows = _rows(world, ANA)
    assert set(rows) == {"EV1", "EV2", "EV3", "EV4", "EV6"}
    assert all(r.kind == "external" and r.source == "outlook" and r.org == world.org
               for r in rows.values())
    assert rows["EV2"].busy is False and rows["EV1"].busy is True
    assert rows["EV3"].start_date == date(2026, 11, 20) and rows["EV3"].end_date == date(2026, 11, 21)
    assert rows["EV4"].status == "cancelled" and rows["EV6"].title == "(No title)"
    meta = _meta(world, cid)
    assert "deltatoken" in json.loads(meta.provider_meta["sync_cursor"])["AAA"]
    assert "EwB-fake" not in json.dumps(meta.provider_meta)


@pytest.mark.asyncio
@_DB_GATE
async def test_r8_outlook_second_sync_updates_and_a_removed_event_goes(world) -> None:
    cid = await _connect(world, ANA)
    await _sync(world, cid)
    second = await _sync(world, cid)
    assert (second.status, second.added, second.updated, second.removed) == ("ok", 0, 1, 1)
    rows = _rows(world, ANA)
    assert rows["EV1"].title == "Standup (moved)" and "EV3" not in rows and len(rows) == 4
    # the same delta again adds nothing
    world.http.routes[("GET", "/calendarView/delta")] = [
        answer({"@odata.deltaLink": DELTA + "THIRD", "value": []})]
    third = await _sync(world, cid)
    assert (third.added, third.updated, third.removed) == (0, 0, 0)
    assert len(_rows(world, ANA)) == 4


@pytest.mark.asyncio
@_DB_GATE
async def test_r8_outlook_never_reads_the_metorite_calendar(world) -> None:
    cid = await _connect(world, ANA)
    await _sync(world, cid)
    assert not any("/CCC/" in c[1] for c in world.http.calls)


@pytest.mark.asyncio
@_DB_GATE
async def test_r8_outlook_an_instance_of_a_written_out_series_is_skipped(world) -> None:
    cid = await _connect(world, ANA)
    with world.p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO calendar_entries (organization_id, owner_email, kind, title, starts_at, "
            "ends_at, source, connection_id, external_id, push_to_provider, provider_sync_state) "
            "VALUES (CAST(:o AS uuid), :m, 'event', 'Mine', now(), now() + interval '1 hour', "
            "'metorite', CAST(:c AS uuid), 'MASTER1', 'outlook', 'sent')"),
            {"o": world.org, "m": ANA, "c": cid})
    await _sync(world, cid)
    rows = _rows(world, ANA)
    assert "EV1" not in rows and rows["MASTER1"].kind == "event"


@pytest.mark.asyncio
@_DB_GATE
async def test_r8_outlook_flag_off_sends_no_request(world, monkeypatch) -> None:
    from acb_common import get_settings

    cid = await _connect(world, ANA)
    monkeypatch.setattr(get_settings(), "calendar_sync_read", "", raising=False)
    out = await _sync(world, cid)
    assert out.status == "skipped" and world.http.calls == []
    assert _rows(world, ANA) == {}


@pytest.mark.asyncio
@_DB_GATE
async def test_r8_outlook_org_b_sees_no_row_of_org_a(world) -> None:
    cid_b = await _connect(world, ANA)
    cid_a = await _connect(world, ZED, org=world.other)
    await _sync(world, cid_b)
    await _sync(world, cid_a, org=world.other)
    for member, org in ((ANA, world.org), (ZED, world.other)):
        rows = _rows(world, member)
        assert rows and all(r.org == org for r in rows.values())
    async with _scope(world, world.org):
        async with tenant_session(world.org) as db:
            n = (await db.execute(text(
                "SELECT count(*) FROM calendar_entries WHERE owner_email = :z"), {"z": ZED})).scalar()
    assert n == 0
    # a sync of org A's connection under org B's tenant finds nothing
    out = await _sync(world, cid_a, org=world.org)
    assert out.status == "skipped"
