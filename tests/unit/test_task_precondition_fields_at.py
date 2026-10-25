"""WS-55 MCP-G7 — a comment or a link no longer breaks a pending task write.

Spec: ``project-docs/specs/project_management_app.md`` §9.10.3. Task #367.

``If-Match`` (and the MCP ``version``) is ``pm_tasks.updated_at``. The
satellite bump (``core.touch_task``) moves that stamp for every comment, link
and attachment, so until this change a comment on task A, or a link to A from
task B, gave every pending write to A a 412. Migration
``*_pm_tasks_fields_at.sql`` adds ``fields_at`` and a trigger, and
``core.precondition_holds`` passes a token from ``fields_at`` up to
``updated_at``.

**Hermetic.** The migration is found by CONTENT (R1), and it carries the
trigger, the backfill and the monotonic stamp.

**R8** (a real Postgres, the real ``/projects`` router, the non-privileged
role of the phase-4 rehearsal). A fake agrees with any SQL, and the whole fix
is a trigger, so every acceptance case runs here::

    TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5434/acb_tenant \\
        uv run pytest tests/unit/test_task_precondition_fields_at.py -v -rs

Mutations (each must turn a named test red):

* M1. ``core.precondition_holds``: drop the ``fields_at`` arm.
  ``test_a_link_from_another_task_keeps_the_token_good``,
  ``test_a_comment_keeps_the_token_good_for_a_status_move``.
* M2. The migration: stamp ``fields_at`` on every stamped UPDATE (drop the
  ``to_jsonb`` comparison). ``test_the_trigger_leaves_fields_at_on_a_bump``.
* M3. The migration: drop the monotonic lift.
  ``test_the_now_stamp_is_monotonic_and_an_explicit_one_is_kept``.
* M4. The migration: ``BEFORE INSERT OR UPDATE`` with an early return when
  ``updated_at`` did not move (the first draft).
  ``test_a_bump_then_a_field_in_one_transaction_stamps_fields_at``, and the
  claim race of ``test_mcp_edge_claim.py`` (two 200s for one version).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("gateway.routes.projects", reason="gateway not installed")

from sqlalchemy import text

from tests.unit.test_agent_runs_r8 import (
    _principal,
    _seed_task,
    _session,
)
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

_MIGRATIONS = Path(__file__).resolve().parents[2] / "infra" / "postgres"

ME = "g7-me@fields-at.test"
OTHER = "g7-other@fields-at.test"


# ═══════════════════════════════════════════════════════════════════════════
# Hermetic
# ═══════════════════════════════════════════════════════════════════════════


def _migration() -> Path:
    found = [p for p in sorted(_MIGRATIONS.glob("*.sql"))
             if "FUNCTION pm_tasks_stamp_fields()" in p.read_text(encoding="utf-8")]
    assert len(found) == 1, found
    return found[0]


def test_the_migration_adds_the_column_the_trigger_and_the_backfill() -> None:
    sql = _migration().read_text(encoding="utf-8")
    assert "ADD COLUMN IF NOT EXISTS fields_at TIMESTAMPTZ;" in sql
    assert "CREATE OR REPLACE TRIGGER trg_pm_tasks_stamp_fields" in sql
    # OF updated_at: a request that bumps first and writes a field second
    # stamps one `now()` twice, so a test of the value would skip the field.
    assert "BEFORE INSERT OR UPDATE OF updated_at ON pm_tasks" in sql
    assert "WHERE fields_at IS NULL" in sql
    assert "OLD.updated_at + interval '1 microsecond'" in sql
    # R6: nullable, no default, so the ADD rewrites nothing.
    assert "fields_at TIMESTAMPTZ NOT NULL" not in sql


def test_the_trigger_sorts_after_the_org_triggers() -> None:
    """BEFORE triggers run in name order. The stamp must see the final row."""
    assert "trg_pm_tasks_stamp_fields" > "trg_pm_tasks_org_from_root"


# ═══════════════════════════════════════════════════════════════════════════
# R8
# ═══════════════════════════════════════════════════════════════════════════


def _lane(p: Any, task: str, name: str, category: str, position: int) -> str:
    """A second lane in the project of *task*, as the RLS-bypassing admin."""
    with p.admin_engine.begin() as c:
        row = c.execute(text(
            "SELECT project_id, organization_id FROM pm_tasks WHERE id = CAST(:t AS uuid)"),
            {"t": task}).one()
        lane = str(uuid.uuid4())
        c.execute(text(
            "INSERT INTO pm_task_statuses (id, organization_id, project_id, name, "
            "category, position) VALUES (:id, :o, :p, :n, :c, :pos)"),
            {"id": lane, "o": row.organization_id, "p": row.project_id, "n": name,
             "c": category, "pos": position})
    return lane


def _second_task(p: Any, task: str) -> str:
    """Task B, in the same project as task A, so the member sees both."""
    with p.admin_engine.begin() as c:
        row = c.execute(text(
            "SELECT project_id, organization_id, status_id FROM pm_tasks "
            "WHERE id = CAST(:t AS uuid)"), {"t": task}).one()
        other = str(uuid.uuid4())
        c.execute(text(
            "INSERT INTO pm_tasks (id, organization_id, project_id, root_project_id, "
            "task_number, status_id, title, created_by) "
            "VALUES (:id, :o, :p, :p, 2, :s, 'Order the nozzles', :e)"),
            {"id": other, "o": row.organization_id, "p": row.project_id,
             "s": row.status_id, "e": ME})
    return other


def _stamps(p: Any, task: str) -> Any:
    with p.admin_engine.connect() as c:
        return c.execute(text(
            "SELECT updated_at, fields_at FROM pm_tasks WHERE id = CAST(:t AS uuid)"),
            {"t": task}).one()


@pytest.fixture
def world(promoted):  # noqa: F811 — a fixture
    """Task A and task B in one project of org A, and a second lane.

    A fresh pair for each test, so no test reads a stamp another one moved.
    """
    task_a = _seed_task(promoted, promoted.org_a, ME, grant=ME)
    with promoted.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO app_user (email, display_name, role, status, organization_id) "
            "VALUES (:e, :e, 'employee', 'active', CAST(:o AS uuid)) "
            "ON CONFLICT DO NOTHING"), {"e": OTHER, "o": promoted.org_a})
    return type("World", (), {
        "p": promoted,
        "task_a": task_a,
        "task_b": _second_task(promoted, task_a),
        "doing": _lane(promoted, task_a, "Doing", "in_progress", 20),
        "me": _principal(ME, promoted.org_a),
        "other": _principal(OTHER, promoted.org_a),
    })


async def _as(call: Any, user: Any, method: str, path: str, body: dict | None = None,
              if_match: str | None = None) -> Any:
    """One request as *user*, with ``If-Match`` when it is set."""
    key = str(id(user))
    call.users[key] = user
    headers = {"x-test-as": key}
    if if_match is not None:
        headers["If-Match"] = if_match
    return await call.client.request(method, path, json=body, headers=headers)


async def _version(call: Any, w: Any) -> str:
    got = await _as(call, w.me, "GET", f"/projects/tasks/{w.task_a}")
    assert got.status_code == 200, got.text
    return got.json()["updated_at"]


@_DB_GATE
class TestTheVersionOnARealDatabase:
    async def test_the_route_runs_as_a_role_that_cannot_bypass_rls(self, app_engine):  # noqa: F811
        with app_engine.connect() as c:
            role = c.execute(text(
                "SELECT rolsuper, rolbypassrls FROM pg_roles "
                "WHERE rolname = current_user")).first()
        assert role == (False, False)

    async def test_a_link_from_another_task_keeps_the_token_good(self, world):
        """Read A, link B to A (the `#mention` path), PATCH A with the old token."""
        w = world
        async with _session(w.p) as call:
            version = await _version(call, w)
            linked = await _as(call, w.me, "POST", f"/projects/tasks/{w.task_b}/links",
                               {"target_task_id": w.task_a, "link_type": "relates_to"})
            assert linked.status_code == 201, linked.text
            # The bump moved the token, so equality alone would refuse it.
            assert await _version(call, w) != version
            patched = await _as(call, w.me, "PATCH", f"/projects/tasks/{w.task_a}",
                                {"title": "Fix the extruder today"}, if_match=version)
        assert patched.status_code == 200, patched.text
        assert patched.json()["title"] == "Fix the extruder today"

    async def test_a_comment_keeps_the_token_good_for_a_status_move(self, world):
        """Read A, comment on A, move A's status with the old token."""
        w = world
        async with _session(w.p) as call:
            version = await _version(call, w)
            said = await _as(call, w.other, "POST", f"/projects/tasks/{w.task_a}/comments",
                             {"body": "Nozzle is on the bench."})
            assert said.status_code == 201, said.text
            assert await _version(call, w) != version
            moved = await _as(call, w.me, "PATCH", f"/projects/tasks/{w.task_a}",
                              {"status_id": w.doing}, if_match=version)
        assert moved.status_code == 200, moved.text
        assert moved.json()["status_id"] == w.doing

    async def test_a_field_change_still_gives_412(self, world):
        """Read A, somebody changes a field, PATCH A with the old token: 412."""
        w = world
        async with _session(w.p) as call:
            version = await _version(call, w)
            first = await _as(call, w.other, "PATCH", f"/projects/tasks/{w.task_a}",
                              {"title": "Somebody else's title"})
            assert first.status_code == 200, first.text
            late = await _as(call, w.me, "PATCH", f"/projects/tasks/{w.task_a}",
                             {"title": "My title"}, if_match=version)
            again = await _as(call, w.me, "GET", f"/projects/tasks/{w.task_a}")
        assert late.status_code == 412, late.text
        assert late.json()["detail"]["current"]["title"] == "Somebody else's title"
        assert again.json()["title"] == "Somebody else's title"

    async def test_a_field_change_then_a_comment_still_gives_412(self, world):
        """The comment after the field change must not hide the field change."""
        w = world
        async with _session(w.p) as call:
            version = await _version(call, w)
            first = await _as(call, w.other, "PATCH", f"/projects/tasks/{w.task_a}",
                              {"title": "Changed first"})
            assert first.status_code == 200, first.text
            said = await _as(call, w.other, "POST", f"/projects/tasks/{w.task_a}/comments",
                             {"body": "And a note."})
            assert said.status_code == 201, said.text
            late = await _as(call, w.me, "PATCH", f"/projects/tasks/{w.task_a}",
                             {"status_id": w.doing}, if_match=version)
        assert late.status_code == 412, late.text

    async def test_the_trigger_leaves_fields_at_on_a_bump(self, world):
        """A comment moves ``updated_at`` and leaves ``fields_at``. A field
        write moves both, to one value."""
        w = world
        before = _stamps(w.p, w.task_a)
        assert before.fields_at is not None
        async with _session(w.p) as call:
            said = await _as(call, w.other, "POST", f"/projects/tasks/{w.task_a}/comments",
                             {"body": "Only a comment."})
            assert said.status_code == 201, said.text
        bumped = _stamps(w.p, w.task_a)
        assert bumped.updated_at > before.updated_at
        assert bumped.fields_at == before.fields_at
        async with _session(w.p) as call:
            done = await _as(call, w.other, "PATCH", f"/projects/tasks/{w.task_a}",
                             {"title": "A field this time"})
            assert done.status_code == 200, done.text
        changed = _stamps(w.p, w.task_a)
        assert changed.updated_at > bumped.updated_at
        assert changed.fields_at == changed.updated_at

    async def test_the_now_stamp_is_monotonic_and_an_explicit_one_is_kept(self, world):
        """The ``now()`` idiom never moves the stamp back. An explicit time does."""
        w = world
        ahead = datetime.now().astimezone() + timedelta(hours=1)
        with w.p.admin_engine.begin() as c:
            c.execute(text("UPDATE pm_tasks SET updated_at = :u WHERE id = CAST(:t AS uuid)"),
                      {"u": ahead, "t": w.task_a})
        with w.p.admin_engine.begin() as c:
            c.execute(text("UPDATE pm_tasks SET updated_at = now() WHERE id = CAST(:t AS uuid)"),
                      {"t": w.task_a})
        lifted = _stamps(w.p, w.task_a)
        assert lifted.updated_at == ahead + timedelta(microseconds=1)
        back = ahead - timedelta(days=2)
        with w.p.admin_engine.begin() as c:
            c.execute(text("UPDATE pm_tasks SET updated_at = :u WHERE id = CAST(:t AS uuid)"),
                      {"u": back, "t": w.task_a})
        assert _stamps(w.p, w.task_a).updated_at == back

    async def test_a_bump_then_a_field_in_one_transaction_stamps_fields_at(self, world):
        """The claim's shape: a bump, then a field, both at one ``now()``.

        The second statement does not move ``updated_at``, and it still
        changed a field. The stamp of the request stays one instant.
        """
        w = world
        with w.p.admin_engine.begin() as c:
            stamp = c.execute(text("SELECT now()")).scalar()
            c.execute(text("UPDATE pm_tasks SET updated_at = now() WHERE id = CAST(:t AS uuid)"),
                      {"t": w.task_a})
            c.execute(text(
                "UPDATE pm_tasks SET title = 'Claimed', updated_at = now() "
                "WHERE id = CAST(:t AS uuid)"), {"t": w.task_a})
        after = _stamps(w.p, w.task_a)
        assert after.updated_at == stamp
        assert after.fields_at == stamp

    async def test_a_write_that_keeps_updated_at_keeps_fields_at(self, world):
        """``touch=False`` writes never moved the token, so they do not move
        ``fields_at`` either."""
        w = world
        before = _stamps(w.p, w.task_a)
        with w.p.admin_engine.begin() as c:
            c.execute(text(
                "UPDATE pm_tasks SET recurrence_spawned_at = now() "
                "WHERE id = CAST(:t AS uuid)"), {"t": w.task_a})
        after = _stamps(w.p, w.task_a)
        assert after.updated_at == before.updated_at
        assert after.fields_at == before.fields_at

    async def test_a_new_task_gets_fields_at_from_its_stamp(self, world):
        w = world
        made = _stamps(w.p, w.task_b)
        assert made.fields_at == made.updated_at
