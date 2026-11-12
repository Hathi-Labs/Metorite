"""WS-CAL CE-10d — the mailbox offers and ``login_hint`` on the calendar connect.

Task 3069 · D215, D-EM-31. Mail and calendar stay SEPARATE connections. The
read adds no scope and no token, and it never selects ``credentials_encrypted``.

``GET /tasks/calendar/connections`` gains ``mailbox_offers``: the PRIVATE
mailboxes of the session member that have no calendar connection of the same
provider and address. ``POST .../{provider}/connect`` takes an optional
``{"login_hint"}`` that must be one of the member's OWN mailboxes.

The unit tests use fakes. The R8 tests run the REAL router on a promoted
scratch database as the non-privileged app role (FORCE RLS binds), two orgs::

    eval "$(bash scripts/dev_db.sh --export)"
    export TENANT_LADDER_DATABASE_URL="${TENANT_LADDER_DATABASE_URL%/*}/<private db>"
    uv run pytest tests/unit/test_calendar_mailbox_offers.py -v -rs

**The mutations (R7).** Each turns the named test red. Commit before each.

* M1. ``calendar_connections.py``: drop ``private_predicate()`` from
  ``_OWN_MAILBOXES_SQL``. A shared mailbox and a colleague's mailbox were
  offered.
  ``test_r8_the_offers_are_the_private_mailboxes_of_the_member_only``.
* M2. Same SQL: drop ``AND organization_id = {_BOUND_ORG}``. Org A's row with
  the same address showed on a role that bypasses RLS.
  ``test_r8_the_org_bind_holds_for_a_role_that_bypasses_rls``.
* M3. ``list_connections``: drop the ``(w, a) not in have`` filter. A
  connected account was offered again.
  ``test_r8_the_offers_are_the_private_mailboxes_of_the_member_only``.
* M5. Bind the lower-cased uid in the two ``_own_mailboxes`` calls. A member
  whose ``user_id`` has capitals saw no offer and got 422.
  ``test_r8_a_mixed_case_user_id_gets_offers_and_a_hint_in_any_case``.
* M6. ``list_connections``: hide by (provider, address) again. A second Gmail
  mailbox was offered while Gmail had a calendar.
  ``test_r8_a_calendar_on_one_gmail_mailbox_hides_every_gmail_offer``.
* M7. ``connect``: drop the ``already_connected`` check.
  ``test_a_hint_for_a_provider_with_a_live_calendar_is_409_and_signs_nothing``.
* M4. ``connect``: drop the ``(provider, want) not in mine`` check. A foreign
  hint reached the consent URL.
  ``test_a_hint_that_is_not_the_members_own_mailbox_is_422_and_signs_nothing``.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

import httpx
from acb_auth import UserContext, UserRole, build_access, get_current_user
from acb_common.db import bind_tenant, release_tenant
from fastapi import FastAPI
from gateway.routes.tasks import calendar_connections as cal
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

ORG_A = "11111111-1111-1111-1111-111111111111"
ME = "ana@offers.test"


# ── Unit: fakes ───────────────────────────────────────────────────────────


class FakeDb:
    def __init__(self, mailboxes: list[tuple[str, str]]) -> None:
        self.mailboxes = mailboxes

    async def execute(self, stmt: Any, params: Any = None) -> Any:
        return SimpleNamespace(fetchall=lambda: list(self.mailboxes))


class FakeProv:
    provider = "google_calendar"

    def __init__(self) -> None:
        self.hints: list[str] = []

    def consent_url(self, *, state: str, login_hint: str) -> str:
        self.hints.append(login_hint)
        return f"https://consent.test/?state={state}&login_hint={login_hint}"


def _user(org: str = ORG_A, email: str = ME) -> UserContext:
    return UserContext(email=email, role=UserRole.EMPLOYEE,
                       access=build_access(["feature:tasks"]), organization_id=org)


@pytest.fixture
def unit(monkeypatch):
    prov = FakeProv()
    signed: list[dict] = []
    monkeypatch.setattr(cal.flags, "read_allowed", lambda org, email: True)
    monkeypatch.setitem(cal.PROVIDERS, "google_calendar", lambda: prov)
    monkeypatch.setitem(cal.PROVIDERS, "outlook_calendar", lambda: prov)

    def sign(**kw: Any) -> str:
        signed.append(kw)
        return "STATE"

    monkeypatch.setattr(cal, "sign_state", sign)
    live: list[Any] = []

    async def member_connections(db: Any, org: str, me: str) -> list[Any]:
        return live

    monkeypatch.setattr(cal.connections, "member_connections", member_connections)
    boxes = [("gmail", "Ana@Gmail.test"), ("microsoft", "ana@corp.test")]

    @asynccontextmanager
    async def session() -> AsyncIterator[FakeDb]:
        yield FakeDb(boxes)

    monkeypatch.setattr(cal, "_tenant_session", session)
    return SimpleNamespace(prov=prov, signed=signed, live=live)


async def _post(path: str, **kw: Any) -> Any:
    from gateway.routes.tasks import router

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = _user
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://gateway.test") as c:
        return await c.post(path, **kw)


@pytest.mark.asyncio
@pytest.mark.parametrize("word,address", [("google", "ana@gmail.test"),
                                          ("outlook", "ana@corp.test")])
async def test_the_connect_puts_the_own_mailbox_in_the_consent_url(unit, word, address) -> None:
    resp = await _post(f"/tasks/calendar/connections/{word}/connect",
                       json={"login_hint": address.upper()})
    assert resp.status_code == 200, resp.text
    assert unit.prov.hints == [address]
    assert f"login_hint={address}" in resp.json()["url"]
    assert len(unit.signed) == 1


@pytest.mark.asyncio
async def test_no_body_behaves_as_before_and_hints_the_member(unit) -> None:
    resp = await _post("/tasks/calendar/connections/google/connect")
    assert resp.status_code == 200, resp.text
    assert unit.prov.hints == [ME]


@pytest.mark.asyncio
@pytest.mark.parametrize("hint", [
    "eve@gmail.test",        # not the member's mailbox
    "ana@corp.test",         # the member's, but another provider (google route)
    "not-an-address",        # not an email shape
    "a" * 250 + "@x.test",   # over 254 characters
])
async def test_a_hint_that_is_not_the_members_own_mailbox_is_422_and_signs_nothing(
        unit, hint) -> None:
    resp = await _post("/tasks/calendar/connections/google/connect",
                       json={"login_hint": hint})
    assert resp.status_code == 422
    assert unit.signed == [] and unit.prov.hints == []


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["connected", "needs_reconnect"])
async def test_a_hint_for_a_provider_with_a_live_calendar_is_409_and_signs_nothing(
        unit, status) -> None:
    unit.live.append(SimpleNamespace(provider="google_calendar", status=status))
    resp = await _post("/tasks/calendar/connections/google/connect",
                       json={"login_hint": "ana@gmail.test"})
    assert (resp.status_code, resp.json()["detail"]) == (409, "already_connected")
    assert unit.signed == [] and unit.prov.hints == []
    # Outlook has no calendar row, so its hint still works.
    ok = await _post("/tasks/calendar/connections/outlook/connect",
                     json={"login_hint": "ana@corp.test"})
    assert ok.status_code == 200


@pytest.mark.asyncio
async def test_an_extra_key_in_the_body_is_422(unit) -> None:
    resp = await _post("/tasks/calendar/connections/google/connect",
                       json={"login_hint": "ana@gmail.test", "org": ORG_A})
    assert resp.status_code == 422
    assert unit.signed == []


@pytest.mark.asyncio
async def test_the_flag_off_keeps_the_connect_a_404(unit, monkeypatch) -> None:
    monkeypatch.setattr(cal.flags, "read_allowed", lambda org, email: False)
    resp = await _post("/tasks/calendar/connections/google/connect",
                       json={"login_hint": "ana@gmail.test"})
    assert resp.status_code == 404
    assert unit.signed == []


def test_the_offer_read_names_no_credential_and_binds_the_org() -> None:
    sql = cal._OWN_MAILBOXES_SQL
    assert "credentials_encrypted" not in sql and "SELECT *" not in sql
    assert "shared_since IS NULL" in sql and "organization_id" in sql
    assert re.search(r"user_id = :uid", sql)


# ── R8: a real database ───────────────────────────────────────────────────

A1 = "a1@offers-r8.test"
A2 = "a2@offers-r8.test"
B1 = "b1@offers-r8.test"
SAME = "same@gmail.offers-r8.test"
CORP = "a1@corp.offers-r8.test"
PEOPLE = [A1, A2, B1]


def _box(w: Any, org: str, member: str, provider: str, address: str, *,
         shared: bool = False) -> None:
    with w.p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO email_accounts (organization_id, user_id, provider, email_address, "
            "credentials_encrypted, shared_since) VALUES (CAST(:o AS uuid), :u, :p, :a, "
            "'SECRET-BLOB-MUST-NOT-LEAK', CASE WHEN :s THEN now() ELSE NULL END)"),
            {"o": org, "u": member, "p": provider, "a": address, "s": shared})


@pytest.fixture
def world(promoted, monkeypatch):  # noqa: F811
    from acb_common import get_settings

    settings = get_settings()
    for name, value in {"calendar_sync_read": "*", "calendar_sync_members": ""}.items():
        monkeypatch.setattr(settings, name, value, raising=False)
    w = SimpleNamespace(p=promoted, a=promoted.org_a, b=promoted.org_b)
    _box(w, w.a, A1, "gmail", SAME)
    _box(w, w.a, A1, "microsoft", CORP)
    _box(w, w.a, A1, "gmail", "a1-shared@gmail.offers-r8.test", shared=True)
    _box(w, w.a, A1, "imap", "a1@imap.offers-r8.test")
    _box(w, w.a, A2, "gmail", "a2@gmail.offers-r8.test")
    _box(w, w.b, B1, "gmail", SAME)
    yield w
    with promoted.admin_engine.begin() as c:
        c.execute(text("DELETE FROM integration_connections WHERE member_email = ANY(:e)"),
                  {"e": PEOPLE})
        c.execute(text("DELETE FROM email_accounts WHERE user_id = ANY(:e)"), {"e": PEOPLE})


def _calendar_row(w: Any, member: str, provider: str, address: str,
                  status: str = "connected") -> None:
    with w.p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO integration_connections (organization_id, provider, owning_app, "
            "scope_kind, member_email, status, provider_meta) VALUES "
            "(CAST(:o AS uuid), :p, 'calendar', 'member', :m, :s, CAST(:meta AS jsonb))"),
            {"o": w.a, "p": provider, "m": member, "s": status,
             "meta": f'{{"account_email": "{address}"}}'})


async def _call(w: Any, org: str, member: str, path: str = "/tasks/calendar/connections",
                dsn: str | None = None, **kw: Any) -> Any:
    from gateway.routes.tasks import router

    async def who() -> Any:
        return UserContext(email=member, role=UserRole.EMPLOYEE,
                           access=build_access(["feature:tasks"]), organization_id=org)

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = who
    token = bind_tenant(org)
    try:
        url = dsn or w.p.app_url.render_as_string(hide_password=False)
        async with tenant_engine_scope(url), httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://gateway.test") as c:
            return await (c.post(path, **kw) if kw else c.get(path))
    finally:
        release_tenant(token)


def _offers(resp: Any) -> list[tuple[str, str]]:
    assert resp.status_code == 200, resp.text
    return [(o["provider"], o["address"]) for o in resp.json()["mailbox_offers"]]


@pytest.mark.asyncio
@_DB_GATE
async def test_r8_the_offers_are_the_private_mailboxes_of_the_member_only(world) -> None:
    resp = await _call(world, world.a, A1)
    assert _offers(resp) == [("google", SAME), ("outlook", CORP)]
    assert "SECRET-BLOB" not in resp.text and "credentials" not in resp.text
    # A colleague of the same org sees hers only.
    assert _offers(await _call(world, world.a, A2)) == [("google", "a2@gmail.offers-r8.test")]
    # Org B sees its own box and none of org A's, though the address is the same.
    assert _offers(await _call(world, world.b, B1)) == [("google", SAME)]
    # A connected calendar hides the offers of its provider, and keeps the other.
    _calendar_row(world, A1, "google_calendar", SAME.upper())
    assert _offers(await _call(world, world.a, A1)) == [("outlook", CORP)]


@pytest.mark.asyncio
@_DB_GATE
async def test_r8_a_needs_reconnect_row_hides_the_provider_and_a_disconnected_one_does_not(
        world) -> None:
    _calendar_row(world, A1, "google_calendar", SAME, status="disconnected")
    assert ("google", SAME) in _offers(await _call(world, world.a, A1))
    _calendar_row(world, A1, "outlook_calendar", CORP, status="needs_reconnect")
    assert _offers(await _call(world, world.a, A1)) == [("google", SAME)]


@pytest.mark.asyncio
@_DB_GATE
async def test_r8_the_org_bind_holds_for_a_role_that_bypasses_rls(world) -> None:
    """The admin engine is a superuser: RLS does not bind it. Only the
    ``organization_id`` predicate keeps org A's mailbox out of org B's read."""
    dsn = world.p.admin_engine.url.render_as_string(hide_password=False)
    box_a = "a-only@gmail.offers-r8.test"
    _box(world, world.a, B1, "gmail", box_a)  # org A holds a row of user B1
    assert _offers(await _call(world, world.b, B1, dsn=dsn)) == [("google", SAME)]


@pytest.mark.asyncio
@_DB_GATE
async def test_r8_the_connect_accepts_only_the_own_private_mailbox_as_a_hint(
        world, monkeypatch) -> None:
    from gateway.calendar_sync import scheduler

    prov = FakeProv()
    monkeypatch.setitem(scheduler.PROVIDERS, "google_calendar", lambda: prov)
    monkeypatch.setattr(cal, "sign_state", lambda **kw: "STATE")
    url = "/tasks/calendar/connections/google/connect"
    ok = await _call(world, world.a, A1, url, json={"login_hint": SAME})
    assert ok.status_code == 200 and prov.hints == [SAME]
    for who, hint in ((A1, "a1-shared@gmail.offers-r8.test"),  # shared: not "own"
                      (A1, "a2@gmail.offers-r8.test"),         # a colleague's
                      (A2, SAME)):                             # another member's
        bad = await _call(world, world.a, who, url, json={"login_hint": hint})
        assert (bad.status_code, bad.json()["detail"]) == (422, "unknown_mailbox")
    assert prov.hints == [SAME]


@pytest.mark.asyncio
@_DB_GATE
async def test_r8_a_calendar_on_one_gmail_mailbox_hides_every_gmail_offer(world) -> None:
    other = "a1-second@gmail.offers-r8.test"
    _box(world, world.a, A1, "gmail", other)
    _calendar_row(world, A1, "google_calendar", SAME)
    assert _offers(await _call(world, world.a, A1)) == [("outlook", CORP)]
    # The connect refuses a hint for Gmail, so a stale page swaps nothing.
    bad = await _call(world, world.a, A1, "/tasks/calendar/connections/google/connect",
                      json={"login_hint": other})
    assert (bad.status_code, bad.json()["detail"]) == (409, "already_connected")
    with world.p.admin_engine.begin() as c:
        c.execute(text("UPDATE integration_connections SET status = 'disconnected' "
                       "WHERE member_email = :m"), {"m": A1})
    assert _offers(await _call(world, world.a, A1)) == [
        ("google", other), ("google", SAME), ("outlook", CORP)]


@pytest.mark.asyncio
@_DB_GATE
async def test_r8_a_mixed_case_user_id_gets_offers_and_a_hint_in_any_case(
        world, monkeypatch) -> None:
    from gateway.calendar_sync import scheduler

    mixed = "Ana.Silva@Contoso.offers-r8.test"
    PEOPLE.append(mixed)  # cleaned by the fixture
    _box(world, world.a, mixed, "microsoft", "Ana.Silva@Contoso.offers-r8.test")
    assert _offers(await _call(world, world.a, mixed)) == [
        ("outlook", "ana.silva@contoso.offers-r8.test")]
    prov = FakeProv()
    monkeypatch.setitem(scheduler.PROVIDERS, "outlook_calendar", lambda: prov)
    monkeypatch.setattr(cal, "sign_state", lambda **kw: "STATE")
    ok = await _call(world, world.a, mixed, "/tasks/calendar/connections/outlook/connect",
                     json={"login_hint": "ANA.SILVA@contoso.offers-r8.test"})
    assert ok.status_code == 200 and prov.hints == ["ana.silva@contoso.offers-r8.test"]
