"""R8: ``setup_status`` and ``GET /shell/setup`` read the real rows (OB-1a).

Spec: ``project-docs/specs/onboarding.md`` §4, §7.1 and §7.2, row OB-1a.

What this proves against a REAL Postgres (R8), on a database with the whole
ladder and a fresh organization for each test:

- each step reads ``open``, then ``done`` after its real row lands, with
  ``done_at`` from that row;
- a member of org A never sees a row of org B, as the NON-privileged app role
  (FORCE RLS binds it) and as a role that bypasses RLS (the explicit tenant
  predicate of each query binds it);
- ``skipped`` comes from ``my_day_prefs.setup.skipped``, and ``done`` wins;
- the route gives the same answer, from the session only.

A hermetic fake agrees with any SQL it is handed, so none of this could be
proved without the database.
"""
from __future__ import annotations

import json
import uuid
from collections.abc import Callable

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth import UserContext, UserRole, build_access
from acb_common.db import bind_tenant, release_tenant
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

# ⚠️ Without the gate the suite ERRORS where no ladder URL is set.
pytestmark = _DB_GATE

STEP_IDS = [
    "invite_team", "connect_email", "first_project", "ask_assistant",
    "org_profile", "phone_push", "know_me",
]
STAMP = "2026-11-02T10:00:00+00:00"


def _addr() -> str:
    return f"setup.{uuid.uuid4().hex[:8]}@ob1a.test"


def _new_org(db) -> str:
    org = str(uuid.uuid4())
    with db.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO organization (id, slug, display_name) VALUES (:i, :s, :s)"),
            {"i": org, "s": f"ob1a-{org[:8]}"})
    return org


def _seed_member(db, org: str, email: str, status: str = "active") -> None:
    with db.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO app_user (email, display_name, role, status, organization_id) "
            "VALUES (:e, :e, 'employee', :s, CAST(:o AS uuid))"),
            {"e": email, "s": status, "o": org})


def _settings(db, org: str, email: str, **cols: object) -> None:
    """Upsert the member's settings row with JSON columns."""
    with db.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO user_settings (organization_id, user_id) "
            "VALUES (CAST(:o AS uuid), :u) ON CONFLICT DO NOTHING"),
            {"o": org, "u": email})
        for col, value in cols.items():
            c.execute(text(
                f"UPDATE user_settings SET {col} = CAST(:v AS jsonb) "  # noqa: S608
                "WHERE organization_id = CAST(:o AS uuid) AND user_id = :u"),
                {"v": json.dumps(value), "o": org, "u": email})


# One writer for each step: the real row that makes it done.
def _w_invite(db, org, email):
    _seed_member(db, org, _addr(), status="invited")


def _w_email(db, org, email):
    with db.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO email_accounts (user_id, provider, email_address, "
            "credentials_encrypted, organization_id) "
            "VALUES (:u, 'imap', :u, 'x', CAST(:o AS uuid))"), {"u": email, "o": org})


def _w_project(db, org, email):
    with db.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO pm_projects (name, created_by, organization_id, owns_statuses) "
            "VALUES ('First', :u, CAST(:o AS uuid), true)"), {"u": email, "o": org})


def _w_chat(db, org, email):
    _settings(db, org, email, shell_prefs={"chat_first_message_at": STAMP})


def _w_profile(db, org, email):
    with db.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO org_settings (organization_id, key, value, updated_by) "
            "VALUES (CAST(:o AS uuid), 'branding', CAST(:v AS jsonb), :u)"),
            {"o": org, "u": email, "v": json.dumps({"logo": {"mime": "image/png"}})})


def _w_push(db, org, email):
    with db.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO push_subscriptions (organization_id, member_email, endpoint, "
            "p256dh, auth) VALUES (CAST(:o AS uuid), :u, :ep, 'k', 'a')"),
            {"o": org, "u": email, "ep": f"https://push.test/{uuid.uuid4()}"})


def _w_know(db, org, email):
    _settings(db, org, email, assistant_profile={"interview": {"started_at": STAMP}})


WRITERS: dict[str, Callable] = {
    "invite_team": _w_invite, "connect_email": _w_email, "first_project": _w_project,
    "ask_assistant": _w_chat, "org_profile": _w_profile, "phone_push": _w_push,
    "know_me": _w_know,
}


def _app_dsn(ns) -> str:
    return ns.app_url.render_as_string(hide_password=False)


def _admin_dsn(ns) -> str:
    return ns.admin_engine.url.render_as_string(hide_password=False)


def _by_id(steps: list[dict]) -> dict[str, dict]:
    return {s["id"]: s for s in steps}


async def _status(org: str, email: str) -> dict[str, dict]:
    from gateway.setup_status import setup_status

    return _by_id(await setup_status(org, email))


def test_the_writers_cover_every_registry_step() -> None:
    from gateway.setup_status import REGISTRY

    assert [s.id for s in REGISTRY] == STEP_IDS
    assert set(WRITERS) == set(STEP_IDS)


@pytest.mark.parametrize("step_id", STEP_IDS)
async def test_each_step_is_open_then_done_after_its_row_lands(db_ns, step_id) -> None:
    db = db_ns
    org = _new_org(db)
    email = _addr()
    _seed_member(db, org, email)
    async with tenant_engine_scope(_app_dsn(db)):
        before = await _status(org, email)
        WRITERS[step_id](db, org, email)
        after = await _status(org, email)
    assert before[step_id]["status"] == "open"
    assert before[step_id]["done_at"] is None
    assert after[step_id]["status"] == "done"
    assert after[step_id]["done_at"] is not None
    # No other step moves.
    for other in STEP_IDS:
        if other != step_id:
            assert after[other]["status"] == before[other]["status"], other


async def test_a_personal_project_does_not_finish_first_project(db_ns) -> None:
    db = db_ns
    org = _new_org(db)
    email = _addr()
    with db.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO pm_projects (name, created_by, organization_id, personal_owner, "
            "owns_statuses) VALUES ('Mine', :u, CAST(:o AS uuid), :u, true)"), {"u": email, "o": org})
    async with tenant_engine_scope(_app_dsn(db)):
        got = await _status(org, email)
    assert got["first_project"]["status"] == "open"


async def test_the_member_alone_does_not_finish_invite_team(db_ns) -> None:
    db = db_ns
    org = _new_org(db)
    email = _addr()
    _seed_member(db, org, email)
    async with tenant_engine_scope(_app_dsn(db)):
        got = await _status(org, email)
    assert got["invite_team"]["status"] == "open"


async def test_a_logo_less_branding_row_does_not_finish_org_profile(db_ns) -> None:
    db = db_ns
    org = _new_org(db)
    email = _addr()
    with db.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO org_settings (organization_id, key, value, updated_by) "
            "VALUES (CAST(:o AS uuid), 'branding', CAST('{}' AS jsonb), :u)"),
            {"o": org, "u": email})
    async with tenant_engine_scope(_app_dsn(db)):
        got = await _status(org, email)
    assert got["org_profile"]["status"] == "open"


@pytest.mark.parametrize("role", ["app", "owner"])
async def test_a_member_of_org_a_never_sees_a_row_of_org_b(db_ns, role) -> None:
    """``app`` is the role FORCE RLS binds. ``owner`` bypasses RLS, so the
    explicit tenant predicate of each query is the only fence there."""
    db = db_ns
    org_a, org_b = _new_org(db), _new_org(db)
    email = _addr()
    _seed_member(db, org_a, email)
    for step_id in STEP_IDS:
        WRITERS[step_id](db, org_b, email)
    dsn = _app_dsn(db) if role == "app" else _admin_dsn(db)
    async with tenant_engine_scope(dsn):
        in_a = await _status(org_a, email)
        in_b = await _status(org_b, email)
    assert {i: s["status"] for i, s in in_a.items()} == dict.fromkeys(STEP_IDS, "open")
    assert {i: s["status"] for i, s in in_b.items()} == dict.fromkeys(STEP_IDS, "done")


async def test_skipped_reads_the_setup_key_and_done_wins(db_ns) -> None:
    db = db_ns
    org = _new_org(db)
    email = _addr()
    _seed_member(db, org, email)
    _settings(db, org, email, my_day_prefs={
        "mutes": [],
        "setup": {"skipped": {"first_project": STAMP, "connect_email": STAMP}},
    })
    async with tenant_engine_scope(_app_dsn(db)):
        got = await _status(org, email)
        _w_email(db, org, email)
        later = await _status(org, email)
    assert got["first_project"]["status"] == "skipped"
    assert got["first_project"]["done_at"] is None
    assert got["connect_email"]["status"] == "skipped"
    assert got["phone_push"]["status"] == "open"
    assert later["connect_email"]["status"] == "done"


async def test_a_stray_setup_value_reads_as_no_mark(db_ns) -> None:
    db = db_ns
    org = _new_org(db)
    email = _addr()
    _settings(db, org, email, my_day_prefs={"setup": "hidden"})
    async with tenant_engine_scope(_app_dsn(db)):
        got = await _status(org, email)
    assert all(s["status"] == "open" for s in got.values())


async def test_the_route_reads_the_session_and_filters_by_audience(db_ns) -> None:
    from gateway.routes.shell.setup import get_setup

    db = db_ns
    org = _new_org(db)
    email = _addr()
    _seed_member(db, org, email)
    _w_push(db, org, email)
    member = UserContext(
        email=email, role=UserRole.EMPLOYEE,
        access=build_access(["feature:chat", "feature:email"], roles=["member"]),
        organization_id=org,
    )
    admin = UserContext(
        email=email, role=UserRole.EXECUTIVE,
        access=build_access(
            ["feature:chat", "feature:email", "feature:projects",
             "admin:members:invite", "admin:settings:manage"], roles=["admin"]),
        organization_id=org,
    )
    token = bind_tenant(org)
    try:
        async with tenant_engine_scope(_app_dsn(db)):
            as_member = (await get_setup(user=member))["steps"]
            as_admin = (await get_setup(user=admin))["steps"]
    finally:
        release_tenant(token)
    assert [s["id"] for s in as_member] == [
        "connect_email", "ask_assistant", "phone_push", "know_me"]
    assert [s["id"] for s in as_admin] == STEP_IDS
    assert _by_id(as_member)["phone_push"]["status"] == "done"
    assert set(as_member[0]) == {"id", "app", "status", "done_at"}


@pytest.fixture
def db_ns(promoted):  # noqa: F811 — a fixture
    return promoted
