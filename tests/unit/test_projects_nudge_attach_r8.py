"""WS-64 OP-6c: the nudge and the attach tools against the real routes (R8, D-OP-9).

Spec: ``project-docs/specs/one_projects_app.md`` §5a and §9 OP-6c.

``nudge_task`` and ``attach_to_task`` run through the skill's own client against
the REAL ``/projects`` router, on a phase-4 promoted scratch database as the
non-privileged ``acb_app`` role. Two members share one organization and one
space. The hermetic fences (``test_projects_nudge_attach_tools.py``) prove the
cards and the refusals. This file proves what a fake cannot:

* the nudge writes ONE ``pm_notifications`` row for the person waited on, stamps
  ``last_nudged_at`` on the member's own overlay, and sends nothing else;
* the multipart upload reaches the route with the right bytes, and the file, the
  ``attachments`` row and the ``pm_task_attachments`` join agree;
* the tool's words before the card are the route's own words, so a refusal at the
  card and a refusal at the route read the same;
* a private task of another member is not found, so nothing is attached to it.

The bytes come through the REAL ``read_attachment_bytes`` from a real thread
folder, not through a stand-in.

Run (a private ladder database, one R8 suite at a time)::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_projects_nudge_attach_r8.py -v -rs

**The mutations (R7).** Each turned the named test red. Commit before each.

* M1. ``guarded.py`` ``nudge_task``: skip the card.
  ``test_r8_a_declined_nudge_writes_nothing``.
* M2. ``guarded.py`` ``nudge_task``: post twice.
  ``test_r8_a_nudge_notifies_the_person_once_and_stamps_the_overlay``.
* M3. ``guarded.py`` ``attach_to_task``: skip the card.
  ``test_r8_a_declined_attach_writes_nothing``.
* M4. ``guarded.py`` ``attach_to_task``: skip the size checks before the card.
  ``test_r8_a_file_over_the_task_budget_is_refused_before_the_card``.
* M5. ``client.py`` ``request``: keep ``Content-Type: application/json`` on a
  multipart body. ``test_r8_attach_sends_the_file_through_the_real_route``.
* M6. ``guarded.py`` ``_nudge_refusal``: drop the ``completed_at`` check.
  ``test_r8_a_task_the_member_does_not_wait_on_is_refused_in_the_routes_words``.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("skill_projects", reason="skill-projects not installed")

import skill_projects
from fastapi import Request
from skill_projects.client import GatewayRefusal, post, post_file
from skill_projects.refusals import refusals_as_text

from tests.unit._projects_agent_fakes import approve, deny
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    promoted,
)
from tests.unit.test_projects_agent_grants import _route_tools_to

pytestmark = _DB_GATE

ALICE = "alice@op6c-r8.test"
BOB = "bob@op6c-r8.test"
PEOPLE = [ALICE, BOB]
AGENT = "agent-op6c-r8"
SID = "thread-alice-op6c-r8-0001"
PDF = b"%PDF-1.4\n% the brief of the R8 suite\n%%EOF\n"


@pytest.fixture
def world(promoted, tmp_path, monkeypatch):  # noqa: F811
    """One space in org B, open to the whole organization, with a To do lane and a
    Done lane, and three tasks. Alice waits on Bob for the first (Bob holds it, and
    Alice has the WAITING overlay). Nobody waits on anybody for the second. The
    third is done. Seeded as the superuser."""
    from acb_common import get_settings
    from sqlalchemy import text

    monkeypatch.setenv("GTD_ATTACHMENTS_DIR", str(tmp_path / "files"))
    monkeypatch.setattr(get_settings(), "agents_clone_dir", str(tmp_path / "agents"))
    org = promoted.org_b
    pid, todo, done, grant = (str(uuid.uuid4()) for _ in range(4))
    tasks = {name: str(uuid.uuid4()) for name in ("waiting", "free", "done")}
    with promoted.admin_engine.begin() as c:
        for email in PEOPLE:
            c.execute(
                text(
                    "INSERT INTO app_user (email, display_name, role, status, organization_id) "
                    "SELECT :e, :e, 'employee', 'active', CAST(:o AS uuid) WHERE NOT EXISTS "
                    "(SELECT 1 FROM app_user WHERE lower(email) = :e)"
                ),
                {"e": email, "o": org},
            )
        c.execute(
            text(
                "INSERT INTO pm_projects (id, organization_id, name, source, created_by, "
                "owns_statuses) VALUES (CAST(:p AS uuid), CAST(:o AS uuid), 'Shared', "
                "'manual', :me, true)"
            ),
            {"p": pid, "o": org, "me": ALICE},
        )
        for lane, name, position, category, default in (
            (todo, "To do", 10, "todo", True), (done, "Done", 20, "done", False),
        ):
            c.execute(
                text(
                    "INSERT INTO pm_task_statuses (id, project_id, name, position, category, "
                    "is_default) VALUES (CAST(:s AS uuid), CAST(:p AS uuid), :n, :pos, :c, :d)"
                ),
                {"s": lane, "p": pid, "n": name, "pos": position, "c": category, "d": default},
            )
        c.execute(
            text(
                "INSERT INTO pm_project_grants (id, project_id, organization_id, subject, "
                "created_by) VALUES (CAST(:g AS uuid), CAST(:p AS uuid), CAST(:o AS uuid), "
                "'org', :me)"
            ),
            {"g": grant, "p": pid, "o": org, "me": ALICE},
        )
        number = c.execute(text("SELECT coalesce(max(task_number), 0) FROM pm_tasks")).scalar_one()
        for i, (name, tid) in enumerate(tasks.items(), start=1):
            closed = name == "done"
            c.execute(
                text(
                    "INSERT INTO pm_tasks (id, title, project_id, root_project_id, status_id, "
                    "created_by, organization_id, task_number, completed_at) VALUES "
                    "(CAST(:t AS uuid), :title, CAST(:p AS uuid), CAST(:p AS uuid), "
                    "CAST(:s AS uuid), :me, CAST(:o AS uuid), :n, "
                    + ("now()" if closed else "NULL") + ")"
                ),
                {"t": tid, "title": f"The {name} task", "p": pid, "s": done if closed else todo,
                 "me": ALICE, "o": org, "n": number + i},
            )
        for name in ("waiting", "done"):
            c.execute(
                text(
                    "INSERT INTO pm_task_assignees (task_id, assignee, assigned_by, "
                    "organization_id) VALUES (CAST(:t AS uuid), :bob, :me, CAST(:o AS uuid))"
                ),
                {"t": tasks[name], "bob": BOB, "me": ALICE, "o": org},
            )
            c.execute(
                text(
                    "INSERT INTO pm_task_personal (task_id, member_email, disposition, "
                    "waiting_on, delegated_at, organization_id) VALUES (CAST(:t AS uuid), :me, "
                    "'WAITING', CAST(:w AS jsonb), now() - interval '3 days', CAST(:o AS uuid))"
                ),
                {"t": tasks[name], "me": ALICE, "o": org,
                 "w": '{"name": "Bob", "email": "' + BOB + '"}'},
            )
    yield type("World", (), {"org": org, "promoted": promoted, "space": pid, "tasks": tasks})
    with promoted.admin_engine.begin() as c:
        c.execute(
            text("DELETE FROM pm_notifications WHERE organization_id = CAST(:o AS uuid) "
                 "AND recipient = ANY(:e)"),
            {"o": org, "e": PEOPLE},
        )
        c.execute(
            text("DELETE FROM pm_tasks WHERE organization_id = CAST(:o AS uuid) "
                 "AND created_by = ANY(:e)"),
            {"o": org, "e": PEOPLE},
        )
        c.execute(
            text("DELETE FROM pm_projects WHERE organization_id = CAST(:o AS uuid) "
                 "AND (id = CAST(:p AS uuid) OR lower(personal_owner) = ANY(:e))"),
            {"o": org, "p": pid, "e": PEOPLE},
        )
        c.execute(text("DELETE FROM attachments WHERE user_id = ANY(:e)"), {"e": PEOPLE})
        c.execute(text("DELETE FROM app_user WHERE email = ANY(:e)"), {"e": PEOPLE})


def _app():
    """The real Projects router, signed in as whoever the header names."""
    from acb_auth import get_current_user
    from fastapi import FastAPI
    from gateway.routes.projects import router

    from tests.unit._projects_fakes import member_user

    people = {ALICE: member_user(ALICE), BOB: member_user(BOB)}

    async def who(request: Request) -> Any:
        return people[request.headers["x-user-email"].lower()]

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = who
    return app


async def _as(world: Any, monkeypatch, email: str, act):
    from acb_common.db import bind_tenant, release_tenant

    from tests.unit._tenant_ladder import tenant_engine_scope

    _route_tools_to(monkeypatch, _app(), email)
    token = bind_tenant(world.org)
    try:
        url = world.promoted.app_url.render_as_string(hide_password=False)
        async with tenant_engine_scope(url):
            return await act()
    finally:
        release_tenant(token)


def _rows(world: Any, sql: str, **params: Any) -> list[Any]:
    from sqlalchemy import text

    with world.promoted.admin_engine.connect() as c:
        return list(c.execute(text(sql), params).fetchall())


def _notifications(world: Any, task: str) -> list[Any]:
    return _rows(
        world,
        "SELECT recipient, kind, actor FROM pm_notifications WHERE task_id = CAST(:t AS uuid)",
        t=task,
    )


def _nudged_at(world: Any, task: str) -> Any:
    [row] = _rows(
        world,
        "SELECT last_nudged_at FROM pm_task_personal WHERE task_id = CAST(:t AS uuid) "
        "AND member_email = :m",
        t=task, m=ALICE,
    )
    return row.last_nudged_at


def _files(world: Any, task: str) -> list[Any]:
    return _rows(
        world,
        "SELECT a.id, a.name, a.mime, a.size_bytes, a.path, ta.added_by "
        "FROM pm_task_attachments ta JOIN attachments a ON a.id = ta.attachment_id "
        "WHERE ta.task_id = CAST(:t AS uuid) ORDER BY a.name",
        t=task,
    )


# ── the nudge ───────────────────────────────────────────────────────────────


async def test_r8_a_nudge_notifies_the_person_once_and_stamps_the_overlay(
    world, monkeypatch,
) -> None:
    pytest.importorskip("asyncpg")
    task = world.tasks["waiting"]
    asked = approve(monkeypatch)
    out = await _as(world, monkeypatch, ALICE,
                    lambda: refusals_as_text(skill_projects.nudge_task)(task))
    [card] = asked
    assert "«Bob»" in card["detail"] and "«The waiting task»" in card["detail"], card
    assert out.startswith("Nudged «Bob» about"), out
    # One in-app row for the person waited on, written by Alice. Nothing else.
    assert [(r.recipient, r.kind, r.actor) for r in _notifications(world, task)] == [
        (BOB, "nudge", ALICE)
    ]
    assert _nudged_at(world, task) is not None


async def test_r8_a_declined_nudge_writes_nothing(world, monkeypatch) -> None:
    pytest.importorskip("asyncpg")
    task = world.tasks["waiting"]
    deny(monkeypatch)
    out = await _as(world, monkeypatch, ALICE, lambda: skill_projects.nudge_task(task))
    assert out.startswith("Cancelled"), out
    assert _notifications(world, task) == []
    assert _nudged_at(world, task) is None


async def test_r8_a_task_the_member_does_not_wait_on_is_refused_in_the_routes_words(
    world, monkeypatch,
) -> None:
    """The tool says it before the card. The route says the same words, so the two
    doors read alike. A done task and a colleague who only holds the task too."""
    pytest.importorskip("asyncpg")
    asked = approve(monkeypatch)
    cases = (
        (ALICE, world.tasks["free"], "You are not waiting on anybody for this task."),
        (ALICE, world.tasks["done"], "This task is done. There is nobody to chase."),
        (BOB, world.tasks["waiting"], "You are not waiting on anybody for this task."),
    )
    for who, task, words in cases:
        out = await _as(world, monkeypatch, who,
                        lambda t=task: refusals_as_text(skill_projects.nudge_task)(t))
        assert out.startswith(words), (who, out)

        async def by_hand(t=task) -> Any:
            return await post(f"/projects/tasks/{t}/nudge")

        with pytest.raises(GatewayRefusal) as err:
            await _as(world, monkeypatch, who, by_hand)
        assert err.value.status == 409 and err.value.detail == words, (who, err.value.detail)
    assert asked == []
    for task in world.tasks.values():
        assert _notifications(world, task) == []


# ── the attach ──────────────────────────────────────────────────────────────


def _put_upload(world: Any, tmp_path: Path, name: str, content: bytes) -> Path:
    """A file where the upload route puts it for Alice's thread (H-227)."""
    from acb_skills.agent_paths import ensure_state_dir, tenant_instance, upload_dir_rel

    instance = tenant_instance(world.org)
    root = ensure_state_dir(AGENT, instance)
    path = root / upload_dir_rel(instance, SID) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return root


async def _attach(world: Any, monkeypatch, root: Path, who: str, task: str, name: str) -> str:
    """``attach_to_task`` in a run of Alice's thread, with the real accessor."""
    from acb_skills.agent_paths import tenant_instance
    from acb_skills.write_artifact import artifact_context_scope, bind_artifact_context

    async def act() -> str:
        with artifact_context_scope():
            bind_artifact_context(
                session_id=SID, agent_name=AGENT, run_id="r-op6c-r8",
                workspace_root=str(root), instance=tenant_instance(world.org),
            )
            return await refusals_as_text(skill_projects.attach_to_task)(task, name)

    return await _as(world, monkeypatch, who, act)


async def test_r8_attach_sends_the_file_through_the_real_route(
    world, monkeypatch, tmp_path,
) -> None:
    pytest.importorskip("asyncpg")
    task = world.tasks["free"]
    root = _put_upload(world, tmp_path, "brief.pdf", PDF)
    asked = approve(monkeypatch)
    out = await _attach(world, monkeypatch, root, ALICE, task, "brief.pdf")
    [card] = asked
    assert "«brief.pdf»" in card["detail"] and "«The free task»" in card["detail"], card
    assert out.startswith("Attached «brief.pdf» to"), out
    [row] = _files(world, task)
    assert (row.name, row.mime, row.size_bytes, row.added_by) == (
        "brief.pdf", "application/pdf", len(PDF), ALICE,
    )
    assert Path(row.path).read_bytes() == PDF, "the bytes on disk are the bytes of the upload"


async def test_r8_a_declined_attach_writes_nothing(world, monkeypatch, tmp_path) -> None:
    pytest.importorskip("asyncpg")
    task = world.tasks["free"]
    root = _put_upload(world, tmp_path, "brief.pdf", PDF)
    deny(monkeypatch)
    out = await _attach(world, monkeypatch, root, ALICE, task, "brief.pdf")
    assert out.startswith("Cancelled"), out
    assert _files(world, task) == []
    assert not (tmp_path / "files").exists() or not list((tmp_path / "files").iterdir())


async def test_r8_a_file_over_the_task_budget_is_refused_before_the_card(
    world, monkeypatch, tmp_path,
) -> None:
    """The tool's size words come before the card. The route answers 413 to the
    same files, by hand, so the tool's limits are the route's limits."""
    pytest.importorskip("asyncpg")
    task = world.tasks["free"]
    big = b"x" * (6 * 1024 * 1024)
    root = _put_upload(world, tmp_path, "first.bin", big)
    _put_upload(world, tmp_path, "second.bin", big)
    _put_upload(world, tmp_path, "huge.bin", b"x" * (11 * 1024 * 1024))
    approve(monkeypatch)
    assert (await _attach(world, monkeypatch, root, ALICE, task, "first.bin")).startswith(
        "Attached"
    )
    asked = approve(monkeypatch)
    out = await _attach(world, monkeypatch, root, ALICE, task, "second.bin")
    assert "would hold 12.0 MB" in out and "It holds 6.0 MB now" in out, out
    out = await _attach(world, monkeypatch, root, ALICE, task, "huge.bin")
    assert "one task may hold 10.0 MB in total" in out, out
    assert asked == []
    assert [r.name for r in _files(world, task)] == ["first.bin"]

    async def by_hand(content: bytes) -> Any:
        return await post_file(f"/projects/tasks/{task}/attachments", "file", "again.bin", content)

    with pytest.raises(GatewayRefusal) as err:
        await _as(world, monkeypatch, ALICE, lambda: by_hand(big))
    assert err.value.status == 413 and "limit is 10.0 MB" in err.value.detail
    with pytest.raises(GatewayRefusal) as err:
        await _as(world, monkeypatch, ALICE, lambda: by_hand(b"x" * (11 * 1024 * 1024)))
    assert err.value.status == 413 and "one task may hold 10.0 MB in total" in err.value.detail


async def test_r8_a_blocked_type_is_refused_before_the_card_and_by_the_route(
    world, monkeypatch, tmp_path,
) -> None:
    pytest.importorskip("asyncpg")
    task = world.tasks["free"]
    root = _put_upload(world, tmp_path, "setup.exe", b"MZ")
    asked = approve(monkeypatch)
    out = await _attach(world, monkeypatch, root, ALICE, task, "setup.exe")
    assert out.startswith("File type not allowed: «setup.exe»"), out

    async def by_hand() -> Any:
        return await post_file(f"/projects/tasks/{task}/attachments", "file", "setup.exe", b"MZ")

    with pytest.raises(GatewayRefusal) as err:
        await _as(world, monkeypatch, ALICE, by_hand)
    assert err.value.status == 400 and err.value.detail == "File type not allowed: setup.exe"
    assert asked == [] and _files(world, task) == []


async def test_r8_another_members_private_task_is_not_found_and_gets_no_file(
    world, monkeypatch, tmp_path,
) -> None:
    """Alice's task in her own personal project. Bob passes its id: the read is a 404
    before any card, and the route would answer the same to a hand-made upload."""
    pytest.importorskip("asyncpg")
    approve(monkeypatch)

    async def make() -> Any:
        return await skill_projects.create_personal_task("Private errand")

    await _as(world, monkeypatch, ALICE, make)
    [mine] = _rows(
        world,
        "SELECT t.id FROM pm_tasks t JOIN pm_projects p ON p.id = t.project_id "
        "WHERE lower(p.personal_owner) = :e AND t.title = 'Private errand'",
        e=ALICE,
    )
    private = str(mine.id)
    root = _put_upload(world, tmp_path, "brief.pdf", PDF)
    asked = approve(monkeypatch)
    out = await _attach(world, monkeypatch, root, BOB, private, "brief.pdf")
    assert out.startswith("Refused:") and "Not found, or not visible to you" in out, out

    async def by_hand() -> Any:
        return await post_file(f"/projects/tasks/{private}/attachments", "file", "x.pdf", PDF)

    with pytest.raises(GatewayRefusal) as err:
        await _as(world, monkeypatch, BOB, by_hand)
    assert err.value.status == 404
    assert asked == [] and _files(world, private) == []
