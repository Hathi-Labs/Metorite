"""WS-MDC CC-5a — a focus session is tenant-scoped and private to the member (R8).

Spec: ``project-docs/specs/my_day_command_centre.md`` §9.3 ("CC-5a, privacy")
and §10 (the fence row of CC-5) · D188 (D-MD-5) · Metorite task 1972.

Two walls, two proofs, as §9.3 says. The RLS of ``focus_sessions`` is tenant
RLS only, as on ``pm_task_personal``, and no policy keys on the member. So:

* **The table proves the tenant.** The non-privileged app role, bound to org A,
  reads no row of org B and cannot write one, and an unbound session reads no
  row at all. FORCE holds the table owner to the policy too.
* **The routes prove the member.** Bob, in Alice's org, never reads, changes
  or ends a session of Alice. And Alice's address in org A holds a session
  that her session in org B never sees, so RLS, and not the member filter,
  keeps org A out.
* **The member purge** takes the member's sessions of this org only.

**The ladder.** The ``promoted`` fixture calls ``apply_ladder`` on a NEW
private database, then the four generated phases (see
``test_focus_sessions_r8.py``). Each read of a day names the day of a stored
start, so no test depends on the hour of the run.

**The mutations (R7).** Each one turned the named test red, on a committed
tree. ``F`` is ``apps/services/gateway/gateway/routes/projects/focus_sessions.py``.

* R1. The migration's RLS block and the ``focus_sessions`` lines of
  ``generated/04_policies.sql`` removed, so the table has no policy.
  ``test_org_a_cannot_see_or_write_org_b_rows_through_the_table``.
* R2. ``F`` ``_DAY_SQL``: drop ``member_email = :me``.
  ``test_member_b_never_reads_changes_or_ends_member_a``.
* R3. ``F`` ``_MINE_OPEN``: drop ``member_email = :me``.
  ``test_member_b_never_reads_changes_or_ends_member_a``.
* R4. ``F`` ``_REPLACE_OPEN_SQL``: drop ``member_email = :me``.
  ``test_member_b_never_reads_changes_or_ends_member_a`` (Bob's start ends
  Alice's session).
* R5. The two ``FORCE ROW LEVEL SECURITY`` lines of the migration and of
  ``generated/04_policies.sql`` removed (ENABLE stays).
  ``test_force_holds_the_table_owner_to_the_policy``.
* R6. ``members.py``: drop the org predicate of the ``focus_sessions`` row of
  ``_PURGE_DELETES``. ``test_the_purge_takes_the_member_of_this_org_only``.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC
from pathlib import Path

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("gateway.routes.projects", reason="gateway not installed")

from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.pool import NullPool

from tests.unit.test_focus_sessions_r8 import (
    ALICE,
    BASE,
    BOB,
    CAROL,
    admin,
    as_,
    capture,
    client,
    day_for,
    day_of,
    instant,
    open_rows,
    patch,
    row,
    start,
    world,  # noqa: F401 — a fixture
)
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

_ROOT = Path(__file__).resolve().parents[2]

#: A role of this run only: the table owner of the FORCE test. A role belongs
#: to the whole Postgres server, not to one database, so a fixed name lets two
#: runs on one server drop each other's role (CC-5a re-review P3).
_OWNER_ROLE = f"focus_owner_r8_{uuid.uuid4().hex[:12]}"

_SEED_ORG_A_ROW = (
    "INSERT INTO focus_sessions (organization_id, member_email, task_id, started_at, "
    "planned_end, mode) VALUES (CAST(:o AS uuid), :e, CAST(:t AS uuid), now(), "
    "now() + interval '25 minutes', '25/5')"
)


def test_this_suite_is_named_in_the_ci_skip_guard() -> None:
    """An R8 suite that CI does not list still skips in silence when the
    database gate is disarmed. Match the argument line, not a mention."""
    for name in ("pr-check.yml", "deploy.yml"):
        workflow = (_ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
        assert re.search(
            r"^\s+tests/unit/test_focus_session_rls_r8\.py \\?$", workflow, re.M), name


def _bound(conn, org: str | None) -> None:
    """Bind the transaction to *org*, as ``acb_common.db.tenant_session`` does."""
    if org is not None:
        conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": org})


def _seen(world, org: str | None) -> set[str]:  # noqa: F811
    """The ids that the app role reads, on a NEW connection each time. A
    pooled connection keeps the setting of its last bind as an empty text,
    and then an unbound read fails on the cast instead of reading no row."""
    engine = create_engine(world.p.app_url, poolclass=NullPool)
    try:
        with engine.begin() as conn:
            _bound(conn, org)
            return {str(r.id) for r in conn.execute(text("SELECT id FROM focus_sessions"))}
    finally:
        engine.dispose()


async def test_org_a_cannot_see_or_write_org_b_rows_through_the_table(
    world, app_engine,  # noqa: F811 — the write and the UPDATE run as the app role
) -> None:
    """R1. As the app role: each org reads its own rows only, a write stamped
    with the other org is refused, an UPDATE of the other org's row changes
    nothing, and an unbound session reads no row."""
    async with client(world, world.org) as c:
        task = await capture(c, ALICE, "Org B work")
        mine = await start(c, ALICE, task)
    # Org A's row, written past RLS. The task row is org B's, and the key
    # holds no org, so the seed needs no second task.
    admin(world, _SEED_ORG_A_ROW, o=world.other, e=CAROL, t=task)
    with world.p.admin_engine.connect() as conn:
        theirs = str(conn.execute(text(
            "SELECT id FROM focus_sessions WHERE organization_id = CAST(:o AS uuid) "
            "AND member_email = :e"), {"o": world.other, "e": CAROL}).scalar())

    assert mine["id"] in _seen(world, world.org)
    assert theirs not in _seen(world, world.org)
    assert theirs in _seen(world, world.other)
    assert mine["id"] not in _seen(world, world.other)
    assert _seen(world, None) == set()

    with pytest.raises(DBAPIError, match="row-level security"), app_engine.begin() as conn:
        _bound(conn, world.org)
        conn.execute(text(
            "INSERT INTO focus_sessions (organization_id, member_email, task_id, "
            "started_at, mode) VALUES (CAST(:o AS uuid), :e, CAST(:t AS uuid), now(), "
            "'flow')"), {"o": world.other, "e": ALICE, "t": task})

    with app_engine.begin() as conn:
        _bound(conn, world.org)
        changed = conn.execute(text(
            "UPDATE focus_sessions SET outcome = 'done', ended_at = now() "
            "WHERE id = CAST(:i AS uuid)"), {"i": theirs}).rowcount
    assert changed == 0
    assert row(world, theirs).ended_at is None


async def test_force_holds_the_table_owner_to_the_policy(world) -> None:  # noqa: F811
    """F8 (R5). FORCE ROW LEVEL SECURITY binds the table owner too. A role that
    is not a superuser takes the table. Unbound, it reads no row. Bound to
    org B, it reads Alice's row, so the 0 comes from the policy and not from a
    missing grant. The superuser of the fixture skips RLS always, so it is not
    the owner under test."""
    async with client(world, world.org) as c:
        await start(c, ALICE, await capture(c, ALICE, "Owned"))
    eng = world.p.admin_engine
    with eng.begin() as conn:
        prior = conn.execute(text(
            "SELECT tableowner FROM pg_tables "
            "WHERE schemaname = 'public' AND tablename = 'focus_sessions'")).scalar()
        conn.execute(text(
            f"DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{_OWNER_ROLE}') "
            f"THEN CREATE ROLE {_OWNER_ROLE} NOLOGIN NOSUPERUSER NOBYPASSRLS; END IF; END $$"))
        conn.execute(text(f"ALTER TABLE focus_sessions OWNER TO {_OWNER_ROLE}"))
    try:
        fresh = create_engine(eng.url, poolclass=NullPool)
        try:
            with fresh.begin() as conn:
                conn.execute(text(f"SET LOCAL ROLE {_OWNER_ROLE}"))
                unbound = conn.execute(text("SELECT count(*) FROM focus_sessions")).scalar()
                _bound(conn, world.org)
                bound = conn.execute(text("SELECT count(*) FROM focus_sessions")).scalar()
        finally:
            fresh.dispose()
    finally:
        with eng.begin() as conn:
            conn.execute(text(f'ALTER TABLE focus_sessions OWNER TO "{prior}"'))
            conn.execute(text(f"DROP ROLE IF EXISTS {_OWNER_ROLE}"))
    assert unbound == 0
    assert bound >= 1


async def test_member_b_never_reads_changes_or_ends_member_a(world) -> None:  # noqa: F811
    """R2, R3, R4. Bob is in Alice's org. He reads none of her sessions, a
    write to her session id is 404 and changes nothing, and his own start
    leaves her open session open."""
    async with client(world, world.org) as c:
        hers = await start(c, ALICE, await capture(c, ALICE, "Alice's"))
        day = day_for(world, hers["id"])
        bobs_day = await day_of(c, BOB, day)
        answers = [
            await patch(c, BOB, hers["id"], action=act, **extra)
            for act, extra in (("pause", {}), ("resume", {}), ("extend", {}),
                               ("end", {"outcome": "done"}))
        ]
        his = await start(c, BOB, await capture(c, BOB, "Bob's"))
        alices_day = await day_of(c, ALICE, day)
    assert bobs_day["sessions"] == [] and bobs_day["total_minutes"] == 0
    assert [a.status_code for a in answers] == [404, 404, 404, 404]
    kept = row(world, hers["id"])
    assert kept.ended_at is None and kept.paused_at is None and kept.outcome is None
    assert kept.planned_end == instant(hers["planned_end"])
    assert [str(r.id) for r in open_rows(world, ALICE, world.org)] == [hers["id"]]
    assert [str(r.id) for r in open_rows(world, BOB, world.org)] == [his["id"]]
    assert [s["id"] for s in alices_day["sessions"]] == [hers["id"]]


async def test_the_same_address_in_another_org_stays_out(world) -> None:  # noqa: F811
    """Alice's address also holds an open session in org A. Her session in
    org B never reads it, never ends it with a start, and gets 404 on it.
    The member filter matches that row, so only RLS keeps it out."""
    async with client(world, world.org) as c:
        task = await capture(c, ALICE, "Two orgs")
        admin(world, _SEED_ORG_A_ROW, o=world.other, e=ALICE, t=task)
        [other] = open_rows(world, ALICE, world.other)
        listed = await day_of(c, ALICE, other.started_at.astimezone(UTC).date().isoformat())
        poke = await patch(c, ALICE, str(other.id), action="end", outcome="done")
        mine = await start(c, ALICE, task)
    assert listed["sessions"] == []
    assert poke.status_code == 404
    assert [str(r.id) for r in open_rows(world, ALICE, world.other)] == [str(other.id)]
    assert [str(r.id) for r in open_rows(world, ALICE, world.org)] == [mine["id"]]


async def test_a_body_that_names_a_member_or_an_org_is_refused(world) -> None:  # noqa: F811
    """R11: identity and tenant come from the session only."""
    async with client(world, world.org) as c:
        task = await capture(c, ALICE, "Names")
        for extra in ({"member_email": BOB}, {"organization_id": world.other}):
            resp = await c.post(BASE, json={"task_id": task, "mode": "25/5", **extra},
                                headers=as_(ALICE))
            assert resp.status_code == 422, resp.text
    assert open_rows(world, BOB, world.org) == []
    assert open_rows(world, ALICE, world.other) == []


async def test_the_purge_takes_the_member_of_this_org_only(world) -> None:  # noqa: F811
    """R6. The ``focus_sessions`` clause of the member purge deletes Alice's
    sessions of org B only. Bob keeps his, and Alice's address keeps its
    session of org A. The statement runs as the superuser, which skips RLS,
    bound to org B, so the clause must hold the org by itself, beside RLS."""
    from gateway.routes.admin.members import _PURGE_DELETES

    [rows] = [r for r in _PURGE_DELETES if r.table == "focus_sessions"]
    async with client(world, world.org) as c:
        task = await capture(c, ALICE, "Purged")
        await start(c, ALICE, task)
        await start(c, BOB, await capture(c, BOB, "Kept"))
    admin(world, _SEED_ORG_A_ROW, o=world.other, e=ALICE, t=task)
    with world.p.admin_engine.begin() as conn:
        _bound(conn, world.org)
        deleted = conn.execute(
            text(f"DELETE FROM focus_sessions WHERE {rows.where}"), {"email": ALICE},
        ).rowcount
    assert deleted == 1
    with world.p.admin_engine.connect() as conn:
        left = sorted(
            (r.member_email, str(r.organization_id))
            for r in conn.execute(text(
                "SELECT member_email, organization_id FROM focus_sessions "
                "WHERE member_email = ANY(:e)"), {"e": [ALICE, BOB]})
        )
    assert left == sorted([(BOB, world.org), (ALICE, world.other)])
