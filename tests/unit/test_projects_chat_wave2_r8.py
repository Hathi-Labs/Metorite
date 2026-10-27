"""WS-64 chat wave 2 — the server rules that the chat and the MCP edge share.

**R8**, a real Postgres, through the real ``/projects`` router as the
non-privileged role. Two rules live in the gateway, so every caller gets
them, and no client keeps a copy:

* **A subtask lives in its parent's project** (items 1 and 2). A create or a
  move that names another project is a 422 with the code
  ``parent_other_project`` for every caller, a member too. A member's
  re-parent that names no project carries the task into the parent's
  project (the Tasks app's "file under"), with every guard of a move.
* **A bare due day is noon in the member's zone** (item 3). The app writes
  noon in the browser's zone (``quickAdd.dueInstantForDay``). The chat and
  the MCP edge sent ``2026-10-30``, which stored midnight UTC, so in Los
  Angeles the task fell on the day before. Create, PATCH, bulk and the
  personal capture now turn a bare day into noon in
  ``user_settings.timezone``. A value with a time stays as it is.

R7, the mutations (run after a commit, because ``git checkout`` restores HEAD):

* **M1** ``tasks.refuse_foreign_parent`` returns at once
  -> ``test_a_member_create_under_a_parent_of_another_project_is_422``.
* **M2** ``move_task_in`` checks the parent against ``task.project_id`` only
  -> ``test_a_move_that_names_another_project_than_the_parent_is_422``.
* **M3** ``due.due_instant`` returns the value as it is
  -> every test of ``TestADueDay``.
* **M4** ``tasks.carry_to_parent`` returns the payload as it is
  -> ``test_a_member_reparent_with_no_project_goes_to_the_parents_project``.
* **M5** ``recurrence.spawn_successor`` drops the ``_home_holds_no_tasks``
  check -> ``test_a_repeating_task_on_a_space_spawns_nothing_there``.
* **M6** ``filters`` and ``node_counts_sql`` disagree about a category (for
  example the list drops its triage arm)
  -> ``test_the_summary_counts_equal_the_list_totals``.

Items 9 and 14 live here too. The summary that ``project_summary`` prints
and the totals that ``list_tasks`` pages agree for each of the six
categories. A repeat rule on a task that sits on a space spawns no
successor there.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("gateway.routes.projects", reason="gateway not installed")

from sqlalchemy import text

from tests.unit import _agent_board_r8 as b
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

OWNER = "w2-owner@chat-wave2-r8.test"
LA = "w2-la@chat-wave2-r8.test"
KOLKATA = "w2-in@chat-wave2-r8.test"
NO_ZONE = "w2-utc@chat-wave2-r8.test"

pytestmark = _DB_GATE


def _zone(p: Any, org: str, email: str, zone: str) -> None:
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO user_settings (user_id, organization_id, timezone) "
            "VALUES (:e, CAST(:o AS uuid), :z) ON CONFLICT DO NOTHING"),
            {"e": email, "o": org, "z": zone})


def _row(p: Any, task_id: str) -> Any:
    with p.admin_engine.begin() as c:
        return c.execute(text(
            "SELECT project_id, parent_task_id, due_at FROM pm_tasks "
            "WHERE id = CAST(:id AS uuid)"), {"id": task_id}).first()


def _count(p: Any, title: str) -> int:
    with p.admin_engine.begin() as c:
        return int(c.execute(text(
            "SELECT count(*) FROM pm_tasks WHERE title = :t"), {"t": title}).scalar_one())


@pytest.fixture(scope="module")
def world(promoted):  # noqa: F811 - a fixture
    p = promoted
    # The seeds number their tasks by hand, and the route counts from 1. A
    # high start keeps the two apart.
    b._NUMBER[0] = max(b._NUMBER[0], 700_000)
    for email in (OWNER, LA, KOLKATA, NO_ZONE):
        b.member(p, p.org_a, email)
    _zone(p, p.org_a, LA, "America/Los_Angeles")
    _zone(p, p.org_a, KOLKATA, "Asia/Kolkata")
    space = b.project(p, p.org_a, OWNER)
    ns = SimpleNamespace(p=p)
    ns.space = space
    ns.a = b.project(p, p.org_a, OWNER, parent=space)
    ns.b = b.project(p, p.org_a, OWNER, parent=space)
    ns.owner = b._principal(OWNER, p.org_a)
    ns.la = b._principal(LA, p.org_a)
    ns.kolkata = b._principal(KOLKATA, p.org_a)
    ns.no_zone = b._principal(NO_ZONE, p.org_a)
    return ns


async def _send(w: Any, user: Any, method: str, path: str, body: dict | None = None) -> Any:
    async with b._session(w.p) as call:
        return await call(user, method, path, body)


class TestTheParentsProject:
    async def test_a_member_create_under_a_parent_of_another_project_is_422(self, world):
        w = world
        parent = b.task(w.p, w.p.org_a, OWNER, w.a, title="w2 parent in A")
        r = await _send(w, w.owner, "POST", "/projects/tasks", {
            "project_id": w.b["id"], "title": "w2 cross create", "parent_task_id": parent})
        assert r.status_code == 422, r.text
        detail = r.json()["detail"]
        assert detail["error"] == "parent_other_project"
        assert w.a["id"] in detail["message"] and w.b["id"] in detail["message"]
        assert _count(w.p, "w2 cross create") == 0

    async def test_a_create_in_the_parents_project_lands(self, world):
        """The control for the refusal above."""
        w = world
        parent = b.task(w.p, w.p.org_a, OWNER, w.a, title="w2 parent, same")
        r = await _send(w, w.owner, "POST", "/projects/tasks", {
            "project_id": w.a["id"], "title": "w2 same create", "parent_task_id": parent})
        assert r.status_code == 201, r.text
        assert str(_row(w.p, r.json()["id"]).parent_task_id) == parent

    async def test_a_move_that_names_another_project_than_the_parent_is_422(self, world):
        w = world
        parent = b.task(w.p, w.p.org_a, OWNER, w.a, title="w2 move parent")
        task = b.task(w.p, w.p.org_a, OWNER, w.b, title="w2 move child")
        r = await _send(w, w.owner, "POST", f"/projects/tasks/{task}/move", {
            "parent_task_id": parent, "project_id": w.b["id"]})
        assert r.status_code == 422, r.text
        assert r.json()["detail"]["error"] == "parent_other_project"
        row = _row(w.p, task)
        assert (str(row.project_id), row.parent_task_id) == (w.b["id"], None)

    async def test_a_member_reparent_with_no_project_goes_to_the_parents_project(self, world):
        """The Tasks app's "file under" sends ``{parent_task_id}`` only."""
        w = world
        parent = b.task(w.p, w.p.org_a, OWNER, w.a, title="w2 carry parent")
        task = b.task(w.p, w.p.org_a, OWNER, w.b, title="w2 carry child")
        step = b.task(w.p, w.p.org_a, OWNER, w.b, title="w2 carry grandchild")
        with w.p.admin_engine.begin() as c:
            c.execute(text("UPDATE pm_tasks SET parent_task_id = CAST(:p AS uuid) "
                           "WHERE id = CAST(:id AS uuid)"), {"p": task, "id": step})
        r = await _send(w, w.owner, "POST", f"/projects/tasks/{task}/move",
                        {"parent_task_id": parent})
        assert r.status_code == 200, r.text
        row = _row(w.p, task)
        assert (str(row.project_id), str(row.parent_task_id)) == (w.a["id"], parent)
        # Its own subtask goes along, so no subtask is left behind in B.
        assert str(_row(w.p, step).project_id) == w.a["id"]


#: 2026-10-30 is in daylight time in Los Angeles (UTC-7). India is UTC+5:30.
DAY = "2026-10-30"


class TestADueDay:
    async def _create(self, w: Any, user: Any, title: str, due: str) -> str:
        r = await _send(w, user, "POST", "/projects/tasks",
                        {"project_id": w.a["id"], "title": title, "due_at": due})
        assert r.status_code == 201, r.text
        return str(r.json()["id"])

    async def test_a_bare_day_is_noon_in_each_members_zone(self, world):
        w = world
        la = await self._create(w, w.la, "w2 due LA", DAY)
        kolkata = await self._create(w, w.kolkata, "w2 due IN", DAY)
        utc = await self._create(w, w.no_zone, "w2 due UTC", DAY)
        assert _row(w.p, la).due_at == datetime(2026, 10, 30, 19, 0, tzinfo=UTC)
        assert _row(w.p, kolkata).due_at == datetime(2026, 10, 30, 6, 30, tzinfo=UTC)
        # No saved zone reads as UTC, as `/projects/my/today` does.
        assert _row(w.p, utc).due_at == datetime(2026, 10, 30, 12, 0, tzinfo=UTC)

    async def test_the_day_is_not_overdue_on_the_evening_before(self, world):
        """Midnight UTC was 17:00 on 2026-10-29 in Los Angeles, so the task
        went overdue the evening before. Noon local is on the day itself."""
        w = world
        la = await self._create(w, w.la, "w2 due LA evening", DAY)
        due = _row(w.p, la).due_at
        evening_before = datetime(2026, 10, 30, 6, 59, tzinfo=UTC)  # 23:59 PDT on the 29th
        assert due > evening_before
        with w.p.admin_engine.begin() as c:
            overdue_then = c.execute(text(
                "SELECT due_at < CAST(:at AS timestamptz) FROM pm_tasks "
                "WHERE id = CAST(:id AS uuid)"),
                {"at": evening_before.isoformat(), "id": la}).scalar_one()
        assert overdue_then is False

    async def test_a_value_with_a_time_and_an_offset_is_untouched(self, world):
        w = world
        task = await self._create(w, w.la, "w2 due offset", "2026-10-30T17:00:00+05:30")
        assert _row(w.p, task).due_at == datetime(2026, 10, 30, 11, 30, tzinfo=UTC)

    async def test_a_patch_and_a_bulk_edit_take_the_same_rule(self, world):
        w = world
        task = await self._create(w, w.la, "w2 due patch", DAY)
        r = await _send(w, w.la, "PATCH", f"/projects/tasks/{task}", {"due_at": "2026-11-02"})
        assert r.status_code == 200, r.text
        # 2 November is in standard time in Los Angeles (UTC-8).
        assert _row(w.p, task).due_at == datetime(2026, 11, 2, 20, 0, tzinfo=UTC)
        r = await _send(w, w.kolkata, "POST", "/projects/tasks/bulk", {
            "task_ids": [task], "patch": {"due_at": DAY}})
        assert r.status_code == 200, r.text
        assert _row(w.p, task).due_at == datetime(2026, 10, 30, 6, 30, tzinfo=UTC)

    async def test_a_personal_capture_takes_the_same_rule(self, world):
        w = world
        r = await _send(w, w.la, "POST", "/projects/my/tasks",
                        {"title": "w2 due capture", "due_at": DAY})
        assert r.status_code in (200, 201), r.text
        assert _row(w.p, str(r.json()["id"])).due_at == datetime(
            2026, 10, 30, 19, 0, tzinfo=UTC)

    async def test_the_chat_renders_the_stored_day_in_the_members_zone(self, world):
        """The read side of item 3: the chat's ``_day`` names the member's
        day, from the instant the real route returns."""
        from skill_projects import reads as R

        w = world
        la = await self._create(w, w.la, "w2 due read LA", DAY)
        kolkata = await self._create(w, w.kolkata, "w2 due read IN", DAY)
        got_la = (await _send(w, w.la, "GET", f"/projects/tasks/{la}")).json()["due_at"]
        got_in = (await _send(w, w.kolkata, "GET", f"/projects/tasks/{kolkata}")).json()["due_at"]
        with R.in_member_zone("America/Los_Angeles"):
            assert R._day(got_la) == DAY
            # The old midnight UTC shape named the day before in Los Angeles.
            assert R._day("2026-10-30T00:00:00+00:00") == "2026-10-29"
        with R.in_member_zone("Asia/Kolkata"):
            assert R._day(got_in) == DAY


#: Every category of ``core.STATUS_CATEGORIES``.
CATEGORIES = ("backlog", "todo", "in_progress", "done", "cancelled", "triage")


class TestTheSummaryAndTheList:
    """Item 9: ``project_summary`` prints the summary's own numbers, and
    ``list_tasks`` pages the list. The two reads must agree, category by
    category, or the chat tells the member two different counts."""

    async def test_the_summary_counts_equal_the_list_totals(self, world):
        w = world
        p = w.p
        root = b.project(p, p.org_a, OWNER)
        with p.admin_engine.begin() as c:
            for position, category in enumerate(("backlog", "in_progress", "cancelled",
                                                 "triage"), start=3):
                sid = c.execute(text(
                    "INSERT INTO pm_task_statuses (organization_id, project_id, name, "
                    "category, position) VALUES (CAST(:o AS uuid), CAST(:p AS uuid), :n, "
                    ":c, :pos) RETURNING id"),
                    {"o": p.org_a, "p": root["id"], "n": f"w2 {category}",
                     "c": category, "pos": position * 10}).scalar_one()
                root["lanes"][category] = str(sid)
        proj = b.project(p, p.org_a, OWNER, parent=root)
        sub = b.project(p, p.org_a, OWNER, parent=proj)
        wanted = {"backlog": 2, "todo": 3, "in_progress": 1, "done": 4, "cancelled": 2,
                  "triage": 1}
        for category, n in wanted.items():
            for i in range(n):
                b.task(p, p.org_a, OWNER, proj if i % 2 else sub, category=category,
                       title=f"w2 count {category} {i}")
        # An archived task is in neither read.
        gone = b.task(p, p.org_a, OWNER, proj, category="todo", title="w2 count archived")
        with p.admin_engine.begin() as c:
            c.execute(text("UPDATE pm_tasks SET archived_at = now() "
                           "WHERE id = CAST(:id AS uuid)"), {"id": gone})

        summary = (await _send(w, w.owner, "GET", f"/projects/nodes/{proj['id']}/summary")).json()
        assert summary["by_category"] == wanted
        assert summary["open"] == 7  # backlog, todo, in_progress and triage
        for category in CATEGORIES:
            query = (f"/projects/tasks?project_id={proj['id']}&include_subtree=true"
                     f"&status_category={category}&page_size=100")
            if category == "triage":
                query += "&include_triage=true"
            listed = (await _send(w, w.owner, "GET", query)).json()
            assert listed["total"] == summary["by_category"].get(category, 0), category


class TestARepeatOnASpace:
    async def test_a_repeating_task_on_a_space_spawns_nothing_there(self, world):
        """Item 14. A task on a team space is legacy data: the space rule
        came after it. Its successor must not land on the space too."""
        w = world
        p = w.p
        task = b.task(p, p.org_a, OWNER, w.space, title="w2 weekly on a space")
        r = await _send(w, w.owner, "PUT", f"/projects/tasks/{task}/recurrence",
                        {"freq": "daily"})
        assert r.status_code == 200, r.text
        r = await _send(w, w.owner, "PATCH", f"/projects/tasks/{task}",
                        {"status_id": w.space["lanes"]["done"]})
        assert r.status_code == 200, r.text
        assert _count(p, "w2 weekly on a space") == 1
        with p.admin_engine.begin() as c:
            said = c.execute(text(
                "SELECT count(*) FROM pm_activities WHERE task_id = CAST(:t AS uuid) "
                "AND body LIKE 'Not recurred:%'"), {"t": task}).scalar_one()
        assert said == 1

    async def test_a_repeating_task_in_a_project_still_spawns(self, world):
        """The control for the refusal above."""
        w = world
        p = w.p
        task = b.task(p, p.org_a, OWNER, w.a, title="w2 weekly in a project")
        r = await _send(w, w.owner, "PUT", f"/projects/tasks/{task}/recurrence",
                        {"freq": "daily"})
        assert r.status_code == 200, r.text
        r = await _send(w, w.owner, "PATCH", f"/projects/tasks/{task}",
                        {"status_id": w.a["lanes"]["done"]})
        assert r.status_code == 200, r.text
        assert _count(p, "w2 weekly in a project") == 2
