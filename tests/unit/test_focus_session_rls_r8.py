"""WS-MDC CC-5a — a focus session is tenant-scoped and private to the member (R8).

Spec: ``project-docs/specs/my_day_command_centre.md`` §9.3 ("CC-5a, privacy")
and §10 (the fence row of CC-5) · D188 (D-MD-5) · Metorite task 1972.

Two walls, two proofs, as §9.3 says. The RLS of ``focus_sessions`` is tenant
RLS only, as on ``pm_task_personal``, and no policy keys on the member. So:

* **The table proves the tenant.** The non-privileged app role, bound to org A,
  reads no row of org B and cannot write one, and an unbound session reads no
  row at all.
* **The routes prove the member.** Bob, in Alice's org, never reads, changes
  or ends a session of Alice. And Alice's address in org A holds a session
  that her session in org B never sees, so RLS, and not the member filter,
  keeps org A out.

**The ladder.** The ``promoted`` fixture calls ``apply_ladder`` on a NEW
private database, then the four generated phases (see
``test_focus_sessions_r8.py``).

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
"""

from __future__ import annotations

import re
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
    admin(world, (
        "INSERT INTO focus_sessions (organization_id, member_email, task_id, started_at, "
        "planned_end, mode) VALUES (CAST(:o AS uuid), :e, CAST(:t AS uuid), now(), "
        "now() + interval '25 minutes', '25/5')"), o=world.other, e=CAROL, t=task)
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


async def test_member_b_never_reads_changes_or_ends_member_a(world) -> None:  # noqa: F811
    """R2, R3, R4. Bob is in Alice's org. He reads none of her sessions, a
    write to her session id is 404 and changes nothing, and his own start
    leaves her open session open."""
    async with client(world, world.org) as c:
        hers = await start(c, ALICE, await capture(c, ALICE, "Alice's"))
        bobs_day = await day_of(c, BOB)
        answers = [
            await patch(c, BOB, hers["id"], action=act, **extra)
            for act, extra in (("pause", {}), ("resume", {}), ("extend", {}),
                               ("end", {"outcome": "done"}))
        ]
        his = await start(c, BOB, await capture(c, BOB, "Bob's"))
        alices_day = await day_of(c, ALICE)
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
        admin(world, (
            "INSERT INTO focus_sessions (organization_id, member_email, task_id, started_at, "
            "planned_end, mode) VALUES (CAST(:o AS uuid), :e, CAST(:t AS uuid), now(), "
            "now() + interval '25 minutes', '25/5')"), o=world.other, e=ALICE, t=task)
        [other] = open_rows(world, ALICE, world.other)
        listed = await day_of(c, ALICE)
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
