"""WS-PZ MM-1 — the act record on a real database (R8).

Spec: ``personalization.md`` §19.4. Code: the one writer
``packages/acb_common/acb_common/member_actions.py`` over ``member_action``
(the migration that creates it, found by content), and the purge clause
``member_actions`` of ``routes/admin/members.py``. The hermetic twin is
``tests/unit/test_member_action_writer.py``.

The writer runs on a phase-4 promoted scratch database, as the non-privileged
app role, so FORCE RLS binds each statement. Two orgs, two members.

Run (a PRIVATE ladder database, one R8 suite at a time, because the
``promoted`` fixture drops ``<db>_h3rls``)::

    eval "$(bash scripts/dev_db.sh --export)"
    export TENANT_LADDER_DATABASE_URL="${TENANT_LADDER_DATABASE_URL%/*}/<private db>"
    uv run pytest tests/unit/test_member_action_r8.py -v -rs

Without the database every test here SKIPS, and the run reads green. A skip
is not a pass.

**Acceptance (MM-1).**

a. The table has ENABLE and FORCE RLS. The migration names no text column.
b. A row lands in the bound org, with the member in lower case and the
   targets and features kept.
c. Org B reads none of org A's rows, and cannot write a row for org A. A
   session with no bound tenant writes nothing.
d. A rule act is written with ``via = rule``. The CHECKs refuse a bad ``via``,
   an empty item id and a large ``features``.
e. ``learning`` off writes nothing. No profile row counts as on.
f. The flag off writes nothing.
g. The prune deletes only the old rows of the bound org.
h. The purge clause deletes the member's rows of this org only, and its count
   equals its delete.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_common import get_settings
from acb_common import member_actions as ma
from acb_common.db import tenant_session
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

_ROOT = Path(__file__).resolve().parents[2]
_MIGRATION = next(
    p for p in (_ROOT / "infra" / "postgres").glob("*.sql")
    if "CREATE TABLE IF NOT EXISTS member_action (" in p.read_text(encoding="utf-8"))

TAG = uuid.uuid4().hex[:8]
ANA = f"ana.{TAG}@mm1.test"
BEN = f"ben.{TAG}@mm1.test"
_LIKE = f"%.{TAG}@mm1.test"


def _dsn(url: Any) -> str:
    return url.render_as_string(hide_password=False)


@pytest.fixture
def world(promoted, monkeypatch):  # noqa: F811
    p = promoted
    monkeypatch.setattr(get_settings(), "member_actions_orgs", "*", raising=False)
    yield SimpleNamespace(p=p)
    with p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM member_action WHERE member_email LIKE :t"), {"t": _LIKE})
        c.execute(text("DELETE FROM user_settings WHERE user_id LIKE :t"), {"t": _LIKE})


def _go(world: Any, fn: Any) -> Any:
    async def go() -> Any:
        async with tenant_engine_scope(_dsn(world.p.app_url)):
            return await fn()
    return asyncio.run(go())


async def _act(org: str, member: str = ANA, **kw: Any) -> bool:
    args: dict[str, Any] = {"app": "email", "act": "forward", "via": "app",
                            "item_kind": "email", "item_id": "m-1", **kw}
    async with tenant_session(org) as db:
        return await ma.record_act(db, org=org, member=member, **args)


def _rows(world: Any) -> list[dict[str, Any]]:
    with world.p.admin_engine.connect() as c:
        return [dict(r) for r in c.execute(text(
            "SELECT * FROM member_action WHERE member_email LIKE :t ORDER BY at"),
            {"t": _LIKE}).mappings()]


def _refused(world: Any, org: str | None, sql: str, params: dict[str, Any]) -> str:
    async def go() -> None:
        async with tenant_session(org) as db:
            await db.execute(text(sql), params)

    with pytest.raises(DBAPIError) as exc:
        _go(world, go)
    return str(exc.value)


_RAW = ("INSERT INTO member_action (member_email, app, act, via, item_kind, item_id, "
        "features) VALUES (:m, :app, :act, :via, :k, :i, CAST(:f AS jsonb))")


def _raw(**kw: Any) -> dict[str, Any]:
    return {"m": ANA, "app": "email", "act": "send", "via": "app", "k": "email",
            "i": "x", "f": "{}", **kw}


# ── a. The table ───────────────────────────────────────────────────────────

def test_the_migration_holds_the_rls_and_names_no_text_column() -> None:
    sql = _MIGRATION.read_text(encoding="utf-8")
    assert "ALTER TABLE %I FORCE  ROW LEVEL SECURITY" in sql
    assert "WITH CHECK (organization_id = current_setting(''app.tenant_id'', true)::uuid)" in sql
    body = sql.split("CREATE TABLE IF NOT EXISTS member_action (", 1)[1].split(");", 1)[0]
    cols = [ln.split()[0] for ln in body.splitlines()
            if ln.strip() and not ln.strip().startswith(("--", "CONSTRAINT", "PRIMARY"))
            and ln.split()[0].islower()]
    assert not [c for c in cols if c in ("subject", "body", "snippet", "text", "content")]
    assert "member_action_via" in sql and "'rule'" in sql


def test_r8_the_table_has_enable_and_force_rls(world) -> None:
    with world.p.admin_engine.connect() as c:
        row = c.execute(text(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
            "WHERE relname = 'member_action'")).one()
        policies = c.execute(text(
            "SELECT policyname FROM pg_policies WHERE tablename = 'member_action'")
        ).scalars().all()
        columns = c.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'member_action'")).scalars().all()
    assert tuple(row) == (True, True)
    assert "member_action_tenant_isolation" in policies
    assert not {"subject", "body", "snippet", "text"} & set(columns)


# ── b. A row ───────────────────────────────────────────────────────────────

def test_r8_a_row_lands_in_the_bound_org(world) -> None:
    a = world.p.org_a
    assert _go(world, lambda: _act(
        a, member=ANA.upper(), mailbox_id="mb-1",
        features={"sender_domain": "ge.com", "is_reply": False, "edit_distance": 3},
        targets=["Veena@ge.com", "kiran@ge.com"])) is True
    [row] = _rows(world)
    assert str(row["organization_id"]) == a
    assert row["member_email"] == ANA
    assert (row["app"], row["act"], row["via"]) == ("email", "forward", "app")
    assert (row["item_kind"], row["item_id"], row["mailbox_id"]) == ("email", "m-1", "mb-1")
    assert row["features"] == {"sender_domain": "ge.com", "is_reply": False, "edit_distance": 3}
    assert row["targets"] == ["veena@ge.com", "kiran@ge.com"]
    assert row["at"] is not None


# ── c. The tenant ──────────────────────────────────────────────────────────

def test_r8_org_b_reads_nothing_of_org_a_and_cannot_write_for_it(world) -> None:
    a, b = world.p.org_a, world.p.org_b
    assert _go(world, lambda: _act(a)) is True

    async def read() -> int:
        async with tenant_session(b) as db:
            return int((await db.execute(text(
                "SELECT count(*) FROM member_action WHERE member_email LIKE :t"),
                {"t": _LIKE})).scalar_one())

    assert _go(world, read) == 0
    err = _refused(
        world, b,
        _RAW.replace("(member_email,", "(organization_id, member_email,").replace(
            "VALUES (:m,", "VALUES (CAST(:org AS uuid), :m,"),
        {**_raw(), "org": a})
    assert "row-level security" in err
    assert len(_rows(world)) == 1


def test_r8_a_session_with_no_bound_tenant_writes_nothing(world) -> None:
    from acb_common.db import TenantUnbound

    async def go() -> None:
        async with tenant_session(None) as db:
            await db.execute(text(_RAW), _raw())

    with pytest.raises((DBAPIError, TenantUnbound)):
        _go(world, go)
    assert _rows(world) == []


# ── d. The CHECKs and the rule act ─────────────────────────────────────────

def test_r8_a_rule_act_is_written_as_a_rule(world) -> None:
    assert _go(world, lambda: _act(world.p.org_a, via="rule", act="archive")) is True
    [row] = _rows(world)
    assert row["via"] == "rule" and not ma.counts_as_choice(row["via"])


@pytest.mark.parametrize("over", [
    {"via": "robot"}, {"i": ""}, {"m": ANA.upper()}, {"m": ""},
    {"f": json.dumps({"k": "x" * 1100})}, {"f": "[]"},
])
def test_r8_the_checks_refuse_a_bad_row(world, over: dict[str, Any]) -> None:
    _refused(world, world.p.org_a, _RAW, _raw(**over))
    assert _rows(world) == []


# ── e. Learning ────────────────────────────────────────────────────────────

def _profile(world: Any, org: str, member: str, profile: Any) -> None:
    with world.p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO user_settings (organization_id, user_id, assistant_profile) "
            "VALUES (CAST(:o AS uuid), :u, CAST(:p AS jsonb))"),
            {"o": org, "u": member, "p": json.dumps(profile)})


def test_r8_learning_off_writes_nothing_and_on_writes(world) -> None:
    a = world.p.org_a
    _profile(world, a, BEN, {"v": 1, "learning": False})
    _profile(world, a, ANA, {"v": 1, "learning": True})
    assert _go(world, lambda: _act(a, member=BEN)) is False
    assert _go(world, lambda: _act(a, member=ANA)) is True
    assert [r["member_email"] for r in _rows(world)] == [ANA]


# ── f. The flag ────────────────────────────────────────────────────────────

def test_r8_the_flag_off_writes_nothing(world, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "member_actions_orgs", "", raising=False)
    assert _go(world, lambda: _act(world.p.org_a)) is False
    monkeypatch.setattr(get_settings(), "member_actions_orgs", world.p.org_b, raising=False)
    assert _go(world, lambda: _act(world.p.org_a)) is False
    assert _rows(world) == []


# ── g. The prune ───────────────────────────────────────────────────────────

def _age(world: Any, org: str, member: str, days: int) -> None:
    with world.p.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE member_action SET at = now() - make_interval(days => :d) "
            "WHERE member_email = :m AND organization_id = CAST(:o AS uuid)"),
            {"d": days, "m": member, "o": org})


def test_r8_the_prune_deletes_only_old_rows_of_the_bound_org(world) -> None:
    a, b = world.p.org_a, world.p.org_b
    _go(world, lambda: _act(a, member=ANA, item_id="old-a"))
    _go(world, lambda: _act(a, member=BEN, item_id="new-a"))
    _go(world, lambda: _act(b, member=ANA, item_id="old-b"))
    _age(world, a, ANA, 181)
    _age(world, b, ANA, 400)

    async def prune() -> int:
        async with tenant_session(a) as db:
            return await ma.prune(db)

    assert _go(world, prune) == 1
    assert sorted(r["item_id"] for r in _rows(world)) == ["new-a", "old-b"]


# ── h. The member purge ────────────────────────────────────────────────────

@pytest.mark.parametrize("role", ["app", "superuser"])
def test_r8_a_member_purge_removes_the_acts_of_this_org_only(world, role: str) -> None:
    from gateway.routes.admin.members import _PURGE_DELETES, _purge_params

    [rows] = [r for r in _PURGE_DELETES if r.table == "member_action"]
    a, b = world.p.org_a, world.p.org_b

    async def seed() -> None:
        await _act(a, member=ANA, item_id="mine")
        await _act(a, member=ANA, item_id="mine-too")
        await _act(a, member=BEN, item_id="bens")
        await _act(b, member=ANA, item_id="theirs")

    _go(world, seed)
    engine = world.p.app_url if role == "app" else world.p.admin_engine.url

    async def purge() -> tuple[int, int]:
        params = _purge_params(rows, uid=str(uuid.uuid4()), email=ANA)
        async with tenant_engine_scope(_dsn(engine)), tenant_session(a) as db:
            n = (await db.execute(text(rows.count_sql), params)).scalar_one()
            gone = await db.execute(text(rows.delete_sql), params)
            return int(n), int(gone.rowcount)

    counted, deleted = asyncio.run(purge())
    assert counted == deleted == 2
    assert sorted(r["item_id"] for r in _rows(world)) == ["bens", "theirs"]


# ── i. A shared mailbox ────────────────────────────────────────────────────

def test_r8_a_shared_mailbox_records_nothing(world) -> None:
    a = world.p.org_a
    with world.p.admin_engine.begin() as c:
        cols = set(c.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'email_accounts'")).scalars())
    assert "shared_since" in cols
    private, shared = str(uuid.uuid4()), str(uuid.uuid4())
    with world.p.admin_engine.begin() as c:
        for mid, since in ((private, None), (shared, "now()")):
            c.execute(text(
                "INSERT INTO email_accounts (id, organization_id, user_id, provider, email_address, credentials_encrypted, "
                "shared_since) VALUES (CAST(:i AS uuid), CAST(:o AS uuid), :u, 'imap', :e, 'x', "
                + ("now()" if since else "NULL") + ")"),
                {"i": mid, "o": a, "u": ANA, "e": f"mb.{mid[:6]}.{TAG}@mm1.test"})
    try:
        assert _go(world, lambda: _act(a, mailbox_id=private)) is True
        assert _go(world, lambda: _act(a, mailbox_id=shared, item_id="s")) is False
        assert [r["mailbox_id"] for r in _rows(world)] == [private]
    finally:
        with world.p.admin_engine.begin() as c:
            c.execute(text("DELETE FROM email_accounts WHERE id = ANY(CAST(:i AS uuid[]))"),
                      {"i": [private, shared]})
