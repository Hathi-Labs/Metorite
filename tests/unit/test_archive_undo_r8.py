"""WT-6a: the Undo of an archive that a chat card approved, on a real database.

Spec: ``project-docs/specs/one_assistant_context.md`` §9.4 (WT-6a), owner
decision of 2026-11-07. Task 1690.

In a held chat, ``archive_task`` and ``archive_project`` run behind their card
while ``HELD_CLASS_C_ORGS`` holds the org. The tool sends the version that the
archive gave (``ask_tools.publish_card_undo``), and the browser's Undo calls
the EXISTING unarchive route under the member's own session, with that version
as ``If-Match`` (``workbench/control_plane/src/lib/archiveUndo.ts``). The card
path and the flag are hermetic (``test_held_card_writes.py`` §12 and §13).
This file proves the route half on a real database (R8):

* Undo with the version restores the row.
* A change by another member after the archive gives 412, and the row stays
  archived.
* Another organization gets 404, and the row stays archived (R5).
* An absent header still restores (D-PM-20), so each app call is unchanged.
* A project's archive and its restore both move the root row's
  ``updated_at``. So the Undo token of an old archive answers 412 after
  another member restores it and a third archives it again. That later
  archive stays.

Run::

    TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5434/acb_tenant \\
        uv run pytest tests/unit/test_archive_undo_r8.py -v -rs

Mutations (each must turn a named test red):

* U1. ``tasks.unarchive_task``: drop ``_check_version``.
  ``test_a_field_change_after_the_archive_gives_412``.
* U2. ``tree.unarchive_node``: drop ``require_precondition``.
  ``test_a_project_change_after_the_archive_gives_412``.
* U3. Either route: check the version BEFORE the visibility load. A caller of
  another org then learns from a 412 that the row exists.
  ``test_another_org_gets_404_with_a_good_version``.
* U4. ``tree.unarchive_node`` or ``tree.archive_node``: drop the
  ``updated_at`` stamp on the root row.
  ``test_the_archive_and_the_restore_move_the_root_stamp``.
* U5. Drop both stamps. That is the state before the fix, and the Undo then
  clears the later archive (200).
  ``test_a_stale_undo_cannot_clear_a_later_archive``.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("gateway.routes.projects", reason="gateway not installed")

from sqlalchemy import text

from tests.unit.test_agent_runs_r8 import _principal, _seed_task, _session
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

ME = "wt6a-me@archive-undo.test"
OTHER = "wt6a-other@archive-undo.test"
STRANGER = "wt6a-stranger@archive-undo.test"


@pytest.fixture
def world(promoted):  # noqa: F811 — a fixture
    """A task in a project of org A that two members see, and a member of org B."""
    task = _seed_task(promoted, promoted.org_a, ME, grant="org")
    with promoted.admin_engine.begin() as c:
        project = str(c.execute(text(
            "SELECT project_id FROM pm_tasks WHERE id = CAST(:t AS uuid)"),
            {"t": task}).scalar_one())
        for email, org in ((OTHER, promoted.org_a), (STRANGER, promoted.org_b)):
            c.execute(text(
                "INSERT INTO app_user (email, display_name, role, status, organization_id) "
                "VALUES (:e, :e, 'employee', 'active', CAST(:o AS uuid)) "
                "ON CONFLICT DO NOTHING"), {"e": email, "o": org})
    return type("World", (), {
        "p": promoted,
        "task": task,
        "project": project,
        "me": _principal(ME, promoted.org_a),
        "other": _principal(OTHER, promoted.org_a),
        "stranger": _principal(STRANGER, promoted.org_b),
    })


async def _as(call: Any, user: Any, method: str, path: str, body: dict | None = None,
              if_match: str | None = None) -> Any:
    key = str(id(user))
    call.users[key] = user
    headers = {"x-test-as": key}
    if if_match is not None:
        headers["If-Match"] = if_match
    return await call.client.request(method, path, json=body, headers=headers)


def _archived(p: Any, table: str, row_id: str) -> bool:
    with p.admin_engine.connect() as c:
        return c.execute(text(
            f"SELECT archived_at IS NOT NULL FROM {table} WHERE id = CAST(:i AS uuid)"),
            {"i": row_id}).scalar_one()


async def _archive_task(call: Any, w: Any) -> str:
    """The tool's write: the archive route, and the version that it answers."""
    done = await _as(call, w.me, "POST", f"/projects/tasks/{w.task}/archive")
    assert done.status_code == 200, done.text
    assert done.json()["archived_at"]
    return done.json()["updated_at"]


async def _archive_project(call: Any, w: Any, user: Any = None) -> str:
    """The tool's write, and the root stamp that it answers, as
    ``guarded.archive_project`` reads it. A read after it gives the same
    stamp while nobody else wrote."""
    done = await _as(call, user or w.me, "POST", f"/projects/nodes/{w.project}/archive")
    assert done.status_code == 200, done.text
    assert done.json()["projects"] == 1
    stamp = done.json()["updated_at"]
    assert stamp, done.text
    node = await _as(call, user or w.me, "GET", f"/projects/nodes/{w.project}")
    assert node.status_code == 200, node.text
    assert node.json()["updated_at"] == stamp
    return stamp


@_DB_GATE
class TestArchiveUndoOnARealDatabase:
    async def test_the_route_runs_as_a_role_that_cannot_bypass_rls(self, app_engine):  # noqa: F811
        with app_engine.connect() as c:
            role = c.execute(text(
                "SELECT rolsuper, rolbypassrls FROM pg_roles "
                "WHERE rolname = current_user")).first()
        assert role == (False, False)

    async def test_undo_with_its_version_restores_the_task(self, world):
        w = world
        async with _session(w.p) as call:
            version = await _archive_task(call, w)
            assert _archived(w.p, "pm_tasks", w.task)
            undone = await _as(call, w.me, "POST", f"/projects/tasks/{w.task}/unarchive",
                               if_match=version)
        assert undone.status_code == 200, undone.text
        assert undone.json()["archived_at"] is None
        assert not _archived(w.p, "pm_tasks", w.task)

    async def test_a_comment_after_the_archive_keeps_the_undo_good(self, world):
        """A comment moves ``updated_at`` and no field, so the Undo still works."""
        w = world
        async with _session(w.p) as call:
            version = await _archive_task(call, w)
            said = await _as(call, w.other, "POST", f"/projects/tasks/{w.task}/comments",
                             {"body": "Seen."})
            assert said.status_code == 201, said.text
            undone = await _as(call, w.me, "POST", f"/projects/tasks/{w.task}/unarchive",
                               if_match=version)
        assert undone.status_code == 200, undone.text
        assert not _archived(w.p, "pm_tasks", w.task)

    async def test_a_field_change_after_the_archive_gives_412(self, world):
        """Another member renames the archived task. The Undo restores nothing."""
        w = world
        async with _session(w.p) as call:
            version = await _archive_task(call, w)
            renamed = await _as(call, w.other, "PATCH", f"/projects/tasks/{w.task}",
                                {"title": "Renamed by another member"})
            assert renamed.status_code == 200, renamed.text
            late = await _as(call, w.me, "POST", f"/projects/tasks/{w.task}/unarchive",
                             if_match=version)
        assert late.status_code == 412, late.text
        assert _archived(w.p, "pm_tasks", w.task)

    async def test_a_restore_by_another_member_gives_412(self, world):
        """Somebody restored it already. The Undo writes nothing more."""
        w = world
        async with _session(w.p) as call:
            version = await _archive_task(call, w)
            first = await _as(call, w.other, "POST", f"/projects/tasks/{w.task}/unarchive")
            assert first.status_code == 200, first.text
            late = await _as(call, w.me, "POST", f"/projects/tasks/{w.task}/unarchive",
                             if_match=version)
        assert late.status_code == 412, late.text

    async def test_another_org_gets_404_with_a_good_version(self, world):
        """R5: the version is right, and the caller is of org B. No 412 tells it
        that the row exists. Mutation U3 turns this red."""
        w = world
        async with _session(w.p) as call:
            version = await _archive_task(call, w)
            node_version = await _archive_project(call, w)
            task = await _as(call, w.stranger, "POST", f"/projects/tasks/{w.task}/unarchive",
                             if_match=version)
            node = await _as(call, w.stranger, "POST",
                             f"/projects/nodes/{w.project}/unarchive", if_match=node_version)
        assert task.status_code == 404, task.text
        assert node.status_code == 404, node.text
        assert _archived(w.p, "pm_tasks", w.task)
        assert _archived(w.p, "pm_projects", w.project)

    async def test_an_absent_header_still_restores(self, world):
        """D-PM-20: each app call sends no header and works as before."""
        w = world
        async with _session(w.p) as call:
            await _archive_task(call, w)
            undone = await _as(call, w.me, "POST", f"/projects/tasks/{w.task}/unarchive")
        assert undone.status_code == 200, undone.text

    async def test_undo_with_its_version_restores_the_project(self, world):
        w = world
        async with _session(w.p) as call:
            version = await _archive_project(call, w)
            undone = await _as(call, w.me, "POST", f"/projects/nodes/{w.project}/unarchive",
                               if_match=version)
        assert undone.status_code == 200, undone.text
        assert undone.json()["projects"] == 1
        assert not _archived(w.p, "pm_projects", w.project)

    async def test_a_project_change_after_the_archive_gives_412(self, world):
        """Mutation U2 turns this red."""
        w = world
        async with _session(w.p) as call:
            version = await _archive_project(call, w)
            renamed = await _as(call, w.other, "PATCH", f"/projects/nodes/{w.project}",
                                {"name": f"runs {uuid.uuid4().hex[:6]}"})
            assert renamed.status_code == 200, renamed.text
            late = await _as(call, w.me, "POST", f"/projects/nodes/{w.project}/unarchive",
                             if_match=version)
        assert late.status_code == 412, late.text
        assert _archived(w.p, "pm_projects", w.project)

    async def test_a_stale_undo_cannot_clear_a_later_archive(self, world):
        """THE finding. A archives P (token V), B restores it, C archives it
        again, and A clicks Undo. V is the version of A's archive, so the Undo
        answers 412 and C's archive stays. Mutation U4 turns this red."""
        w = world
        async with _session(w.p) as call:
            first = await _archive_project(call, w)
            restored = await _as(call, w.other, "POST",
                                 f"/projects/nodes/{w.project}/unarchive")
            assert restored.status_code == 200, restored.text
            assert restored.json()["projects"] == 1
            await _archive_project(call, w, w.other)
            late = await _as(call, w.me, "POST", f"/projects/nodes/{w.project}/unarchive",
                             if_match=first)
        assert late.status_code == 412, late.text
        assert _archived(w.p, "pm_projects", w.project)

    async def test_the_archive_and_the_restore_move_the_root_stamp(self, world):
        """The root row's ``updated_at`` moves on archive and on restore. Then
        the Undo with the token of the archive restores the project."""
        w = world
        with w.p.admin_engine.connect() as c:
            before = c.execute(text(
                "SELECT updated_at FROM pm_projects WHERE id = CAST(:i AS uuid)"),
                {"i": w.project}).scalar_one()
        async with _session(w.p) as call:
            version = await _archive_project(call, w)
            with w.p.admin_engine.connect() as c:
                after = c.execute(text(
                    "SELECT updated_at FROM pm_projects WHERE id = CAST(:i AS uuid)"),
                    {"i": w.project}).scalar_one()
            assert after > before
            assert after.isoformat() == version
            undone = await _as(call, w.me, "POST", f"/projects/nodes/{w.project}/unarchive",
                               if_match=version)
        assert undone.status_code == 200, undone.text
        assert undone.json()["projects"] == 1
        assert not _archived(w.p, "pm_projects", w.project)
        with w.p.admin_engine.connect() as c:
            restored = c.execute(text(
                "SELECT updated_at FROM pm_projects WHERE id = CAST(:i AS uuid)"),
                {"i": w.project}).scalar_one()
        assert restored > after
