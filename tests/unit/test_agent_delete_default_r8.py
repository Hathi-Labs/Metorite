"""WS-55 EA-6i — the member's delete switch is ON by default (EA3-51).

Spec: ``project-docs/specs/external_agent_access.md`` EA3-51 and §15, the
rows EA-6h and EA-6i. The owner, 2026-10-30: "Agents delete switch should
default to on."

``user_settings.agent_delete_enabled`` has four states, and
``agent_access_switch.delete_enabled`` reads each one:

=========  =====  ==================================================
State      Reads  Why
=========  =====  ==================================================
no row     on     the member has not chosen
NULL       on     the member has not chosen (the default since 316)
``false``  off    the member turned it off
``true``   on     the member turned it on
=========  =====  ==================================================

Migration 316 makes the column nullable with no default. It turns each old
``false`` into NULL once, because the old default and an explicit off were
the same value. A second run must keep an explicit off.

**R8.** A real database, on a phase-4-promoted catalog of its own (FORCE
RLS). ``delete_enabled`` runs in a ``tenant_session`` as a NON-privileged
role. The GET goes through the real gateway app. The migration runs as a
role that owns ``user_settings`` and is not a superuser, so FORCE RLS binds
it as it binds the runner in production. Run::

    bash scripts/dev_db.sh
    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_agent_delete_default_r8.py -v -rs

**The mutations (R7).** Each one turned the named tests red. ``A`` is
``apps/services/gateway/gateway/routes/admin``, and ``M`` is
``infra/postgres/316_user_settings_agent_delete_default_on.sql``.

* E1. ``A/agent_access_switch.py`` ``delete_enabled`` reads NULL as off.
  ``test_delete_enabled_reads_each_state[null]`` and
  ``test_the_get_answers_each_state[null]``.
* E2. ``M``: the UPDATE leaves each ``false`` row alone.
  ``test_316_turns_each_false_into_null_once_and_keeps_true``.
* E3. ``M``: no run-once guard, so a second run backfills again.
  ``test_316_turns_each_false_into_null_once_and_keeps_true``.
* E4. ``M``: no per-tenant pass under row security.
  ``test_316_turns_each_false_into_null_once_and_keeps_true``.

Fix round 1 (the review, P1): the case of the address. A web session keeps
the spelling of the sign-in, and an agent reads with the lower case.

* E5. ``A/agent_access_switch.py`` ``_READ_SQL``: the exact match
  ``user_id = :uid`` again. ``test_a_capital_letter_session_turns_it_off_for_the_agent``.
* E6. ``A/agent_access_switch.py`` ``put_agent_access``: no ``_SET_ALL_SQL``,
  so the PUT writes its own spelling only.
  ``test_a_new_choice_in_one_spelling_wins_over_a_stale_one``.
"""
from __future__ import annotations

import os
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from tests.unit import test_mcp_edge_delete as d
from tests.unit import test_mcp_edge_task_writes as tw
from tests.unit import test_mcp_edge_writes as w
from tests.unit._tenant_ladder import _LADDER_DIR, apply_ladder

_DB_GATE = w._DB_GATE

_APP_ROLE = "acb_app_deldefault"
_APP_PW = "deldefault_scratch_pw"
#: Owns ``user_settings`` and is NOT a superuser, as the production runner.
_OWNER_ROLE = "acb_owner_deldefault"
_OWNER_PW = "deldefault_owner_pw"

#: The domain is the one ``live_app`` allows.
CHOOSER = "deldefault-member@mcpwrites.test"

#: Found by content, never by number (R1): a renumber at merge must not
#: read as a broken migration.
_MIGRATION = next(
    p for p in sorted(os.listdir(_LADDER_DIR))
    if p.endswith("_user_settings_agent_delete_default_on.sql"))
_MIGRATION_SQL_PATH = os.path.join(_LADDER_DIR, _MIGRATION)

#: The four states: ``NONE`` is no row, and the rest is the stored value.
NONE = "no row"
STATES = [pytest.param(NONE, True, id="no_row"), pytest.param(None, True, id="null"),
          pytest.param(False, False, id="false"), pytest.param(True, True, id="true")]


@pytest.fixture(scope="module")
def world():
    """A private promoted catalog: organization A with one member, and B."""
    if not w._URL:
        pytest.skip("TENANT_LADDER_DATABASE_URL unset")
    admin_url = make_url(w._URL)
    dedicated = f"{admin_url.database}_deldefault"
    w._drop_database(admin_url, dedicated)
    with create_engine(admin_url, isolation_level="AUTOCOMMIT").connect() as c:
        c.execute(text(f'CREATE DATABASE "{dedicated}"'))
    ded_admin_url = admin_url.set(database=dedicated)
    eng = create_engine(ded_admin_url, future=True)
    with eng.begin() as conn:
        apply_ladder(conn)
    for phase in ("01_add_columns.sql", "02_backfill.sql", "03_constraints.sql",
                  "04_policies.sql"):
        with eng.begin() as conn, open(os.path.join(w._GENERATED, phase),
                                       encoding="utf-8") as fh:
            w._exec_sql_file(conn, fh.read())
    with create_engine(ded_admin_url, isolation_level="AUTOCOMMIT").connect() as c:
        for role, pw in ((_APP_ROLE, _APP_PW), (_OWNER_ROLE, _OWNER_PW)):
            c.execute(text(
                f"DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='{role}')"
                f" THEN CREATE ROLE {role} LOGIN; END IF; END $$;"))
            c.execute(text(f"ALTER ROLE {role} WITH LOGIN PASSWORD '{pw}' "
                           "NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS INHERIT"))
            c.execute(text(f'GRANT CONNECT ON DATABASE "{dedicated}" TO {role}'))
            c.execute(text(f"GRANT USAGE ON SCHEMA public TO {role}"))
        c.execute(text("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES "
                       f"IN SCHEMA public TO {_APP_ROLE}"))
        c.execute(text(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {_APP_ROLE}"))
        c.execute(text(f"GRANT SELECT ON organization TO {_OWNER_ROLE}"))
        c.execute(text(f"ALTER TABLE user_settings OWNER TO {_OWNER_ROLE}"))

    org, org_b = str(uuid.uuid4()), str(uuid.uuid4())
    with eng.begin() as c:
        for oid, slug in ((org, "deldefault"), (org_b, "deldefaultb")):
            c.execute(text("INSERT INTO organization (id, slug, display_name) "
                           "VALUES (:id, :s, :s)"), {"id": oid, "s": slug})
    a = w._Seeder(eng, org)
    a.member(CHOOSER, ["feature:projects"])
    # A write connection of the member. Its address is lower case, as each
    # connection's is (`agent_tokens`), so a signed read is the agent's read.
    cid, raw = d._connection_of(a, CHOOSER)
    world_ = SimpleNamespace(
        admin=eng, org=org, org_b=org_b, c={"chooser": cid}, tok={"chooser": raw},
        app_dsn=admin_url.set(database=dedicated, username=_APP_ROLE, password=_APP_PW,
                              ).render_as_string(hide_password=False),
        owner_url=admin_url.set(database=dedicated, username=_OWNER_ROLE,
                                password=_OWNER_PW),
        admin_url=ded_admin_url)
    yield world_

    eng.dispose()
    w._drop_database(admin_url, dedicated)


redis_fake = tw.redis_fake
live_app = tw.live_app


# ── Helpers ─────────────────────────────────────────────────────────────────


def _set(world, state: Any, *, org: str | None = None, email: str = CHOOSER,
         keep_other_spellings: bool = False) -> None:
    """Put the switch of *email* in *state*, as the admin.

    ``NONE`` deletes the row. ``None`` inserts a row that does not name the
    column, so the row holds the column default. The rows of every other
    spelling of *email* go too, unless *keep_other_spellings*.
    """
    org = org or world.org
    match = "user_id = :u" if keep_other_spellings else "lower(user_id) = lower(:u)"
    with world.admin.begin() as c:
        c.execute(text("SELECT set_config('app.tenant_id', :o, true)"), {"o": org})
        c.execute(text("DELETE FROM user_settings WHERE organization_id = CAST(:o AS uuid)"
                       f" AND {match}"), {"o": org, "u": email})
        if state is NONE:
            return
        if state is None:
            c.execute(text("INSERT INTO user_settings (organization_id, user_id)"
                           " VALUES (CAST(:o AS uuid), :u)"), {"o": org, "u": email})
        else:
            c.execute(text("INSERT INTO user_settings (organization_id, user_id,"
                           " agent_delete_enabled) VALUES (CAST(:o AS uuid), :u, :v)"),
                      {"o": org, "u": email, "v": state})


def _stored(world, *, org: str | None = None, email: str = CHOOSER) -> Any:
    """The stored value, or ``NONE`` when the member has no row."""
    with world.admin.begin() as c:
        row = c.execute(text("SELECT agent_delete_enabled FROM user_settings"
                             " WHERE organization_id = CAST(:o AS uuid) AND user_id = :u"),
                        {"o": org or world.org, "u": email}).first()
    return NONE if row is None else row[0]


def _all(world) -> dict[tuple[str, str], Any]:
    with world.admin.begin() as c:
        return {(str(r[0]), r[1]): r[2] for r in c.execute(text(
            "SELECT organization_id, user_id, agent_delete_enabled FROM user_settings"))}


async def _read(world, email: str = CHOOSER, *, org: str | None = None) -> bool:
    """``delete_enabled`` as the NON-privileged role, in a tenant session."""
    from gateway.db import tenant_session
    from gateway.routes.admin.agent_access_switch import delete_enabled

    async def go() -> bool:
        async with tenant_session(org or world.org) as db:
            return await delete_enabled(db, email)
    return await tw._in_scope(world, go)


def _column(world) -> tuple[str, Any]:
    with world.admin.begin() as c:
        row = c.execute(text(
            "SELECT is_nullable, column_default FROM information_schema.columns"
            " WHERE table_name = 'user_settings'"
            " AND column_name = 'agent_delete_enabled'")).one()
    return row[0], row[1]


def _run_migration(url) -> list[str]:
    """Apply the file once on *url*. Returns the notices."""
    notices: list[str] = []
    eng = create_engine(url, future=True)
    try:
        with eng.begin() as conn:
            conn.connection.dbapi_connection.add_notice_handler(
                lambda diag: notices.append(diag.message_primary or ""))
            with open(_MIGRATION_SQL_PATH, encoding="utf-8") as fh:
                w._exec_sql_file(conn, fh.read())
    finally:
        eng.dispose()
    return notices


# ── Database-free ───────────────────────────────────────────────────────────


class _NoRead:
    async def execute(self, *_: Any, **__: Any) -> Any:
        raise AssertionError("delete_enabled read the database for an empty address")


class _BrokenRead:
    async def execute(self, *_: Any, **__: Any) -> Any:
        raise RuntimeError("connection lost")


async def test_an_empty_address_is_off_and_reads_nothing():
    from gateway.routes.admin.agent_access_switch import delete_enabled

    assert await delete_enabled(_NoRead(), "") is False


async def test_a_failed_read_raises_and_never_reads_as_on():
    from gateway.routes.admin.agent_access_switch import delete_enabled

    with pytest.raises(RuntimeError):
        await delete_enabled(_BrokenRead(), CHOOSER)


# ── R8: the four states ─────────────────────────────────────────────────────


class TestTheFourStates:
    """No row, NULL, ``false`` and ``true``, read in the bound organization."""

    pytestmark = _DB_GATE

    @pytest.mark.parametrize("state,expected", STATES)
    async def test_delete_enabled_reads_each_state(self, world, state, expected):
        _set(world, state)
        assert _stored(world) is state
        assert await _read(world) is expected

    @pytest.mark.parametrize("state,expected", STATES)
    async def test_the_get_answers_each_state(self, live_app, state, expected):
        _set(live_app, state)
        r = await d._person(live_app, CHOOSER, "GET", "/auth/me/agent-access")
        assert r.status_code == 200, r.text
        assert r.json() == {"delete_enabled": expected}

    async def test_a_new_row_holds_null_and_the_column_has_no_default(self, world):
        assert _column(world) == ("YES", None)
        _set(world, None)
        assert _stored(world) is None

    async def test_the_put_writes_an_explicit_value(self, live_app):
        _set(live_app, NONE)
        for on in (False, True, False):
            r = await d._person(live_app, CHOOSER, "PUT", "/auth/me/agent-access",
                                {"delete_enabled": on})
            assert r.status_code == 200, r.text
            assert r.json() == {"delete_enabled": on}
            # Never NULL: a stored value is the member's choice.
            assert _stored(live_app) is on
            r = await d._person(live_app, CHOOSER, "GET", "/auth/me/agent-access")
            assert r.json() == {"delete_enabled": on}

    async def test_the_bound_organization_decides(self, world):
        """D15: a member in two organizations has two switches."""
        _set(world, NONE)
        _set(world, False, org=world.org_b)
        assert await _read(world) is True
        assert await _read(world, org=world.org_b) is False
        _set(world, False)
        _set(world, NONE, org=world.org_b)
        assert await _read(world) is False
        assert await _read(world, org=world.org_b) is True


# ── R8: the case of the address (fix round 1) ───────────────────────────────

#: The member's sign-in spelling, as a web session sends it.
CAPS = "DelDefault-Member@mcpwrites.test"


async def _agent_get(world) -> dict[str, Any]:
    """The signed GET of the member's agent: the read of ``tools/list``."""
    r = await tw._send(world, "GET", "/auth/me/agent-access", signed="chooser")
    assert r.status_code == 200, r.text
    return r.json()


async def _put_as(world, email: str, on: bool) -> dict[str, Any]:
    r = await d._person(world, email, "PUT", "/auth/me/agent-access", {"delete_enabled": on})
    assert r.status_code == 200, r.text
    return r.json()


class TestTheCaseOfTheAddress:
    """A row keys on the session's spelling. The agent reads in lower case."""

    pytestmark = _DB_GATE

    async def test_a_capital_letter_session_turns_it_off_for_the_agent(self, live_app):
        assert CAPS.lower() == CHOOSER
        _set(live_app, NONE)
        assert await _put_as(live_app, CAPS, False) == {"delete_enabled": False}
        # The row holds the session's spelling, and no lower-case row exists.
        assert _stored(live_app, email=CAPS) is False
        assert _stored(live_app, email=CHOOSER) is NONE
        assert await _agent_get(live_app) == {"delete_enabled": False}
        assert await _read(live_app, CHOOSER) is False
        # Back on: the agent reads on.
        assert await _put_as(live_app, CAPS, True) == {"delete_enabled": True}
        assert await _agent_get(live_app) == {"delete_enabled": True}
        assert await _read(live_app, CHOOSER) is True

    async def test_a_new_choice_in_one_spelling_wins_over_a_stale_one(self, live_app):
        # A stale off under the lower case, from an older session.
        _set(live_app, False, email=CHOOSER)
        _set(live_app, None, email=CAPS, keep_other_spellings=True)
        assert await _agent_get(live_app) == {"delete_enabled": False}
        # The member turns it on with the capital letter spelling.
        assert await _put_as(live_app, CAPS, True) == {"delete_enabled": True}
        assert _stored(live_app, email=CHOOSER) is True
        assert _stored(live_app, email=CAPS) is True
        assert await _agent_get(live_app) == {"delete_enabled": True}
        # And off again: each spelling holds the off.
        assert await _put_as(live_app, CAPS, False) == {"delete_enabled": False}
        assert _stored(live_app, email=CHOOSER) is False
        assert _stored(live_app, email=CAPS) is False
        assert await _agent_get(live_app) == {"delete_enabled": False}

    async def test_one_false_in_any_spelling_is_off(self, world):
        """``bool_and`` skips NULL. A stale ``true`` loses to a ``false``."""
        _set(world, True, email=CHOOSER)
        _set(world, False, email=CAPS, keep_other_spellings=True)
        assert await _read(world, CHOOSER) is False
        assert await _read(world, CAPS) is False
        _set(world, None, email=CAPS, keep_other_spellings=True)
        assert await _read(world, CHOOSER) is True


# ── R8: migration 316 ───────────────────────────────────────────────────────


def _back_to_295(world) -> None:
    """Put the column back in the shape of 295, as the admin (a superuser)."""
    with world.admin.begin() as c:
        c.execute(text("UPDATE user_settings SET agent_delete_enabled = false"
                       " WHERE agent_delete_enabled IS NULL"))
        c.execute(text("ALTER TABLE user_settings"
                       " ALTER COLUMN agent_delete_enabled SET DEFAULT false,"
                       " ALTER COLUMN agent_delete_enabled SET NOT NULL"))


class TestMigration316:
    pytestmark = _DB_GATE

    async def test_316_turns_each_false_into_null_once_and_keeps_true(self, world):
        _back_to_295(world)
        assert _column(world) == ("NO", "false")
        a, b = world.org, world.org_b
        _set(world, False, org=a, email="off@mcpwrites.test")
        _set(world, True, org=a, email="on@mcpwrites.test")
        _set(world, None, org=a, email="default@mcpwrites.test")  # 295's default
        _set(world, False, org=b, email="off@mcpwrites.test")
        _set(world, True, org=b, email="on@mcpwrites.test")
        before = _all(world)
        assert before[(a, "default@mcpwrites.test")] is False
        assert set(before.values()) == {False, True}

        # As the owner, under FORCE RLS: only the per-tenant pass sees a row.
        notices = _run_migration(world.owner_url)
        after = _all(world)
        assert set(after) == set(before)
        for key, value in before.items():
            assert after[key] is (None if value is False else True), key
        falses = sum(1 for v in before.values() if v is False)
        assert f"316: {falses} row(s) of agent_delete_enabled went from false to NULL (on)" \
            in notices, notices
        assert _column(world) == ("YES", None)

        # After the first run, a false is the member's explicit off. A new
        # row gets NULL.
        _set(world, False, org=a, email="on@mcpwrites.test")
        _set(world, None, org=a, email="new@mcpwrites.test")
        settled = _all(world)
        assert settled[(a, "on@mcpwrites.test")] is False
        assert settled[(a, "new@mcpwrites.test")] is None

        # Idempotent: a second run, as each role, changes nothing.
        for url in (world.owner_url, world.admin_url):
            notices = _run_migration(url)
            assert _all(world) == settled
            assert any("nullable already" in n for n in notices), notices
        assert _column(world) == ("YES", None)
