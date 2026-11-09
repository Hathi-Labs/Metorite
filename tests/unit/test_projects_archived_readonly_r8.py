"""ARC-2b: the gateway refuses task writes into an archived project (R8).

Spec: ``project-docs/specs/one_projects_app.md`` §9b.6 rule 2.

The real ``/projects`` router runs in process on a phase-4 promoted scratch
database, as the non-privileged ``acb_app`` role. One project is filed
(``archived_at`` stamped) and one is live. A task write into the filed project
answers **409** with the one sentence ``core.ARCHIVED_PROJECT_REFUSAL``. The
same write into the live project lands. Reads, the restore of the project,
the delete of the project and a member's own overlay stay open.

Run (a private ladder database, one R8 suite at a time)::

    eval "$(bash scripts/dev_db.sh --export)"
    PYTEST_ADDOPTS="-o timeout_method=thread" \\
        uv run pytest tests/unit/test_projects_archived_readonly_r8.py -v -rs

**The mutation (R7).** Remove ``await assert_task_project_open(db, before)`` from
``tasks.patch_task``. ``test_r8_each_task_write_into_a_filed_project_is_refused``
fails on the ``PATCH /tasks/{id}`` row.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

pytest.importorskip("skill_projects", reason="skill-projects not installed")

from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    promoted,
)

pytestmark = _DB_GATE

ALICE = "alice@arc2b-r8.test"
SENTENCE = "This project is archived. Restore it to change its work."


@pytest.fixture
def world(promoted):  # noqa: F811
    """One filed project (FILED), one live project (LIVE), each with a To do
    and a Done lane and two tasks. FILED also holds a comment. Both are open
    to the whole organization. Seeded as the superuser."""
    from sqlalchemy import text

    org = promoted.org_b
    ids = {k: str(uuid.uuid4()) for k in (
        "filed", "live", "empty", "f_todo", "f_done", "l_todo", "l_done",
        "f1", "f2", "l1", "l2", "g_filed", "g_live", "g_empty", "comment",
    )}
    with promoted.admin_engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO app_user (email, display_name, role, status, organization_id) "
                "SELECT :e, :e, 'employee', 'active', CAST(:o AS uuid) WHERE NOT EXISTS "
                "(SELECT 1 FROM app_user WHERE lower(email) = :e)"
            ),
            {"e": ALICE, "o": org},
        )
        for key, name in (("filed", "Filed"), ("live", "Live"), ("empty", "Empty")):
            c.execute(
                text(
                    "INSERT INTO pm_projects (id, organization_id, name, source, created_by, "
                    "owns_statuses) VALUES (CAST(:p AS uuid), CAST(:o AS uuid), :n, "
                    "'manual', :me, true)"
                ),
                {"p": ids[key], "o": org, "n": name, "me": ALICE},
            )
            c.execute(
                text(
                    "INSERT INTO pm_project_grants (id, project_id, organization_id, subject, "
                    "created_by) VALUES (CAST(:g AS uuid), CAST(:p AS uuid), CAST(:o AS uuid), "
                    "'org', :me)"
                ),
                {"g": ids["g_" + key], "p": ids[key], "o": org, "me": ALICE},
            )
        for project, todo, done in (("filed", "f_todo", "f_done"), ("live", "l_todo", "l_done")):
            for lane, name, position, category, default in (
                (todo, "To do", 10, "todo", True), (done, "Done", 20, "done", False),
            ):
                c.execute(
                    text(
                        "INSERT INTO pm_task_statuses (id, project_id, name, position, "
                        "category, is_default) VALUES (CAST(:s AS uuid), CAST(:p AS uuid), "
                        ":n, :pos, :c, :d)"
                    ),
                    {"s": ids[lane], "p": ids[project], "n": name, "pos": position,
                     "c": category, "d": default},
                )
        number = c.execute(text("SELECT coalesce(max(task_number), 0) FROM pm_tasks")).scalar_one()
        for i, (task, project, lane) in enumerate((
            ("f1", "filed", "f_todo"), ("f2", "filed", "f_todo"),
            ("l1", "live", "l_todo"), ("l2", "live", "l_todo"),
        ), start=1):
            c.execute(
                text(
                    "INSERT INTO pm_tasks (id, title, project_id, root_project_id, status_id, "
                    "created_by, organization_id, task_number) VALUES "
                    "(CAST(:t AS uuid), :title, CAST(:p AS uuid), CAST(:p AS uuid), "
                    "CAST(:s AS uuid), :me, CAST(:o AS uuid), :n)"
                ),
                {"t": ids[task], "title": f"Task {task}", "p": ids[project],
                 "s": ids[lane], "me": ALICE, "o": org, "n": number + i},
            )
        # Filed the way `archive_node` files a project: both columns, no task row.
        c.execute(
            text(
                "UPDATE pm_projects SET archived_at = now(), "
                "archived_root_id = id WHERE id = ANY(CAST(:ps AS uuid[]))"
            ),
            {"ps": [ids["filed"], ids["empty"]]},
        )
    yield type("World", (), {"org": org, "promoted": promoted, "ids": ids})
    with promoted.admin_engine.begin() as c:
        for table in ("pm_activities",):
            c.execute(
                text(f"DELETE FROM {table} WHERE task_id IN (SELECT id FROM pm_tasks "
                     "WHERE organization_id = CAST(:o AS uuid) AND created_by = :me)"),
                {"o": org, "me": ALICE},
            )
        c.execute(
            text("DELETE FROM pm_tasks WHERE organization_id = CAST(:o AS uuid) "
                 "AND created_by = :me"),
            {"o": org, "me": ALICE},
        )
        c.execute(
            text("DELETE FROM pm_projects WHERE organization_id = CAST(:o AS uuid) "
                 "AND (id = ANY(CAST(:ps AS uuid[])) OR lower(personal_owner) = :me)"),
            {"o": org, "ps": [ids["filed"], ids["live"], ids["empty"]], "me": ALICE},
        )
        c.execute(text("DELETE FROM attachments WHERE user_id = :me"), {"me": ALICE})
        c.execute(text("DELETE FROM app_user WHERE email = :me"), {"me": ALICE})


def _app():
    from acb_auth import get_current_user
    from fastapi import FastAPI
    from gateway.routes.projects import router

    from tests.unit._projects_fakes import projects_user

    user = projects_user(ALICE)

    async def who() -> Any:
        return user

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = who
    return app


async def _calls(world: Any, acts: list[tuple[str, str, dict[str, Any]]]) -> list[Any]:
    """Send each ``(method, path, kwargs)`` through the real router, in one
    tenant scope, and return the responses in order."""
    import httpx
    from acb_common.db import bind_tenant, release_tenant

    from tests.unit._tenant_ladder import tenant_engine_scope

    token = bind_tenant(world.org)
    out = []
    try:
        url = world.promoted.app_url.render_as_string(hide_password=False)
        async with tenant_engine_scope(url):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=_app()), base_url="http://gateway.test",
            ) as client:
                for method, path, kwargs in acts:
                    out.append(await client.request(method, path, **kwargs))
    finally:
        release_tenant(token)
    return out


async def _one(world: Any, method: str, path: str, **kwargs: Any) -> Any:
    [resp] = await _calls(world, [(method, path, kwargs)])
    return resp


def _rows(world: Any, sql: str, **params: Any) -> list[Any]:
    from sqlalchemy import text

    with world.promoted.admin_engine.connect() as c:
        return list(c.execute(text(sql), params).fetchall())


def _refused_requests(w: Any) -> list[tuple[str, str, str, dict[str, Any]]]:
    """``(label, method, path, kwargs)`` for each route family that the filed
    project refuses. Task ``f1`` and ``f2`` live in the filed project."""
    i = w.ids
    f1, f2, l1 = i["f1"], i["f2"], i["l1"]
    return [
        ("make a task", "POST", "/projects/tasks",
         {"json": {"title": "New", "project_id": i["filed"]}}),
        ("add a subtask", "POST", "/projects/tasks",
         {"json": {"title": "Step", "project_id": i["filed"], "parent_task_id": f1}}),
        ("edit a field", "PATCH", f"/projects/tasks/{f1}", {"json": {"title": "Renamed"}}),
        ("change the status", "PATCH", f"/projects/tasks/{f1}",
         {"json": {"status_id": i["f_done"]}}),
        ("change the dates", "PATCH", f"/projects/tasks/{f1}",
         {"json": {"due_at": "2030-01-01T00:00:00Z"}}),
        ("change the tags", "PATCH", f"/projects/tasks/{f1}", {"json": {"tags": ["x"]}}),
        ("change the parent", "PATCH", f"/projects/tasks/{f1}",
         {"json": {"parent_task_id": f2}}),
        ("set the assignees", "PUT", f"/projects/tasks/{f1}/assignees",
         {"json": {"assignees": [ALICE]}}),
        ("claim", "POST", f"/projects/tasks/{f1}/claim", {"json": {}}),
        ("move out", "POST", f"/projects/tasks/{f1}/move",
         {"json": {"project_id": i["live"]}}),
        ("move into", "POST", f"/projects/tasks/{i['l1']}/move",
         {"json": {"project_id": i["filed"]}}),
        ("move many out", "POST", "/projects/tasks/move",
         {"json": {"task_ids": [f1], "destination_project_id": i["live"]}}),
        ("move many into", "POST", "/projects/tasks/move",
         {"json": {"task_ids": [l1], "destination_project_id": i["filed"]}}),
        ("preview a move out", "POST", "/projects/tasks/move/preview",
         {"json": {"task_ids": [f1], "destination_project_id": i["live"]}}),
        ("archive a task", "POST", f"/projects/tasks/{f1}/archive", {}),
        ("unarchive a task", "POST", f"/projects/tasks/{f1}/unarchive", {}),
        ("delete a task", "DELETE", f"/projects/tasks/{f1}", {}),
        ("add a comment", "POST", f"/projects/tasks/{f1}/comments", {"json": {"body": "Hi"}}),
        ("edit a comment", "PATCH", f"/projects/comments/{i['comment']}",
         {"json": {"body": "Edited"}}),
        ("delete a comment", "DELETE", f"/projects/comments/{i['comment']}", {}),
        ("add an attachment", "POST", f"/projects/tasks/{f1}/attachments",
         {"files": {"file": ("a.txt", b"hello", "text/plain")}}),
        ("add a relation", "POST", f"/projects/tasks/{f1}/links",
         {"json": {"target_task_id": f2, "link_type": "relates"}}),
        ("add a relation to the live end", "POST", f"/projects/tasks/{l1}/links",
         {"json": {"target_task_id": f2, "link_type": "relates"}}),
        ("add a reference", "POST", f"/projects/tasks/{f1}/refs",
         {"json": {"url": "https://github.com/acme/repo/pull/1"}}),
        ("set a repeat rule", "PUT", f"/projects/tasks/{f1}/recurrence",
         {"json": {"freq": "daily", "interval": 1}}),
        ("merge", "POST", f"/projects/tasks/{f1}/merge", {"json": {"sources": [f2]}}),
        ("merge a filed task in", "POST", f"/projects/tasks/{l1}/merge",
         {"json": {"sources": [f2]}}),
        ("complete from my list", "POST", f"/projects/tasks/{f1}/complete", {}),
        ("add my steps", "POST", f"/projects/my/tasks/{f1}/subtasks",
         {"json": {"titles": ["a step"]}}),
        ("capture into it", "POST", "/projects/my/tasks",
         {"json": {"title": "Cap", "project_id": i["filed"]}}),
        ("intake capture", "POST", "/projects/intake",
         {"json": {"title": "Cap", "project_id": i["filed"]}}),
        ("intake snooze", "POST", f"/projects/intake/{f1}/snooze",
         {"json": {"until": "2099-01-01T00:00:00Z"}}),
        ("intake decline", "POST", f"/projects/intake/{f1}/decline", {}),
    ]


async def test_r8_each_task_write_into_a_filed_project_is_refused(world) -> None:
    pytest.importorskip("asyncpg")
    w = world
    # A comment to edit, written while the project was live. The activity row
    # is seeded as the superuser so no route is needed.
    from sqlalchemy import text

    with w.promoted.admin_engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO pm_activities (id, task_id, type, body, created_by, "
                "organization_id) VALUES (CAST(:a AS uuid), CAST(:t AS uuid), 'comment', "
                "'First', :me, CAST(:o AS uuid))"
            ),
            {"a": w.ids["comment"], "t": w.ids["f1"], "me": ALICE, "o": w.org},
        )
    before = _rows(w, "SELECT id, title, status_id, archived_at, parent_task_id, project_id "
                      "FROM pm_tasks WHERE created_by = :me ORDER BY id", me=ALICE)
    bad: list[str] = []
    for label, method, path, kwargs in _refused_requests(w):
        resp = await _one(w, method, path, **kwargs)
        detail = resp.json().get("detail") if resp.headers.get(
            "content-type", "").startswith("application/json") else None
        if resp.status_code != 409 or detail != SENTENCE:
            bad.append(f"{label}: {method} {path} -> {resp.status_code} {detail!r}")
    assert bad == []
    # Nothing moved, and no task or comment row was added.
    after = _rows(w, "SELECT id, title, status_id, archived_at, parent_task_id, project_id "
                     "FROM pm_tasks WHERE created_by = :me ORDER BY id", me=ALICE)
    assert after == before
    [row] = _rows(w, "SELECT body, deleted_at FROM pm_activities WHERE id = CAST(:a AS uuid)",
                  a=w.ids["comment"])
    assert row.body == "First" and row.deleted_at is None


async def test_r8_a_bulk_act_names_each_refused_task(world) -> None:
    pytest.importorskip("asyncpg")
    w = world
    i = w.ids
    resp = await _one(w, "POST", "/projects/tasks/bulk", json={
        "task_ids": [i["f1"], i["l1"], i["f2"]], "patch": {"title": "Bulk"},
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["applied"] == 1
    assert sorted(f["task_id"] for f in body["failed"]) == sorted([i["f1"], i["f2"]])
    assert {f["reason"] for f in body["failed"]} == {SENTENCE}
    titles = {r.id: r.title for r in _rows(
        w, "SELECT id::text AS id, title FROM pm_tasks WHERE created_by = :me", me=ALICE)}
    assert titles[i["l1"]] == "Bulk"
    assert titles[i["f1"]] == "Task f1" and titles[i["f2"]] == "Task f2"
    # The delete and archive verbs go the same way.
    for action in ("delete", "archive", "unarchive"):
        resp = await _one(w, "POST", "/projects/tasks/bulk",
                          json={"task_ids": [i["f1"]], "action": action})
        assert resp.status_code == 200 and resp.json()["applied"] == 0, (action, resp.text)
        assert resp.json()["failed"] == [{"task_id": i["f1"], "reason": SENTENCE}], action
    assert len(_rows(w, "SELECT 1 FROM pm_tasks WHERE id = CAST(:t AS uuid)", t=i["f1"])) == 1


async def test_r8_a_bulk_overlay_is_not_project_work(world) -> None:
    """``action: personal`` writes MY overlay only, so a filed project allows it."""
    pytest.importorskip("asyncpg")
    w = world
    resp = await _one(w, "POST", "/projects/tasks/bulk", json={
        "task_ids": [w.ids["f1"]], "action": "personal", "personal": {"disposition": "SOMEDAY"},
    })
    assert resp.status_code == 200, resp.text
    assert resp.json()["failed"] == []
    resp = await _one(w, "PATCH", f"/projects/tasks/{w.ids['f1']}/personal",
                      json={"disposition": "SOMEDAY"})
    assert resp.status_code == 200, resp.text


async def test_r8_a_filed_project_reads_and_restores_and_deletes(world) -> None:
    pytest.importorskip("asyncpg")
    w = world
    i = w.ids
    reads = await _calls(w, [
        ("GET", f"/projects/tasks/{i['f1']}", {}),
        ("GET", f"/projects/tasks/{i['f1']}/comments", {}),
        ("GET", f"/projects/tasks/{i['f1']}/attachments", {}),
        ("GET", f"/projects/tasks/{i['f1']}/relations", {}),
        ("GET", f"/projects/nodes/{i['filed']}/tasks", {}),
    ])
    assert [r.status_code for r in reads] == [200] * len(reads), [r.text for r in reads]
    # Delete of a filed project is its own gate, and not the guard of this slice.
    gone = await _one(w, "DELETE", f"/projects/nodes/{i['empty']}")
    assert gone.status_code in (200, 204), gone.text
    assert _rows(w, "SELECT 1 FROM pm_projects WHERE id = CAST(:p AS uuid)", p=i["empty"]) == []
    # Restore the filed project, and the same writes land.
    restored = await _one(w, "POST", f"/projects/nodes/{i['filed']}/unarchive")
    assert restored.status_code == 200, restored.text
    patch = await _one(w, "PATCH", f"/projects/tasks/{i['f1']}", json={"title": "Back"})
    assert patch.status_code == 200 and patch.json()["title"] == "Back", patch.text


async def test_r8_a_live_project_takes_the_same_writes(world) -> None:
    pytest.importorskip("asyncpg")
    w = world
    i = w.ids
    l1, l2 = i["l1"], i["l2"]
    acts = [
        ("POST", "/projects/tasks", {"json": {"title": "New", "project_id": i["live"]}}),
        ("POST", "/projects/tasks",
         {"json": {"title": "Step", "project_id": i["live"], "parent_task_id": l1}}),
        ("PATCH", f"/projects/tasks/{l1}", {"json": {"title": "Renamed"}}),
        ("PATCH", f"/projects/tasks/{l1}", {"json": {"status_id": i["l_done"]}}),
        ("PUT", f"/projects/tasks/{l1}/assignees", {"json": {"assignees": [ALICE]}}),
        ("POST", f"/projects/tasks/{l1}/comments", {"json": {"body": "Hi"}}),
        ("POST", f"/projects/tasks/{l1}/attachments",
         {"files": {"file": ("a.txt", b"hello", "text/plain")}}),
        ("POST", f"/projects/tasks/{l1}/links",
         {"json": {"target_task_id": l2, "link_type": "relates"}}),
        ("POST", "/projects/tasks/bulk", {"json": {"task_ids": [l2], "patch": {"title": "B"}}}),
        ("POST", f"/projects/tasks/{l2}/archive", {}),
        ("POST", f"/projects/tasks/{l2}/unarchive", {}),
        ("POST", f"/projects/tasks/{l2}/move", {"json": {"project_id": i["filed"]}}),
    ]
    sent = await _calls(w, acts)
    codes = [r.status_code for r in sent[:-1]]
    assert all(c in (200, 201) for c in codes), [(a[1], r.status_code, r.text[:120])
                                                 for a, r in zip(acts, sent, strict=False)]
    # ...and a move from a live project INTO the filed one is refused.
    assert sent[-1].status_code == 409 and sent[-1].json()["detail"] == SENTENCE


async def test_r8_chat_shows_the_sentence_through_the_skill_client(world, monkeypatch) -> None:
    """The chat skill's client turns the 409 into a refusal with the gateway's
    words, so the model reads the sentence. No tool has a check of its own."""
    pytest.importorskip("asyncpg")
    from acb_common.db import bind_tenant, release_tenant
    from skill_projects.client import patch as skill_patch
    from skill_projects.refusals import refusals_as_text

    from tests.unit._tenant_ladder import tenant_engine_scope
    from tests.unit.test_projects_agent_grants import _route_tools_to

    w = world
    _route_tools_to(monkeypatch, _app(), ALICE)
    token = bind_tenant(w.org)
    try:
        url = w.promoted.app_url.render_as_string(hide_password=False)
        async with tenant_engine_scope(url):
            text = await refusals_as_text(skill_patch)(
                f"/projects/tasks/{w.ids['f1']}", {"title": "Chat edit"})
    finally:
        release_tenant(token)
    assert text.startswith("Refused:"), text
    assert SENTENCE in text, text
