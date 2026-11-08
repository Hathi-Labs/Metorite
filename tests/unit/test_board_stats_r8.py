"""WS-55 MCP-G19a — ``board_stats`` and the audit filters of ``list_tasks``.

Spec: ``project-docs/specs/external_agent_access.md`` (the header line of
MCP-G19a) · Metorite task 1957.

An agent with only the MCP audits a board of about 1,900 tasks. It needs
filters on ``list_tasks`` and a count that equals the list.

**The rule that matters.** ``board_stats`` counts the tasks that
``list_tasks`` lists for the same caller and the same filters. Both routes call
``tasks._task_scope``, the one function that joins the visibility clause
(private, Center, org, group grants, connection pins and the personal switch)
to the filters. The R8 classes here prove it on a real database as a
non-privileged role, with two organizations and several members.

**Database-free.** The tool records, the schemas and the query pairs.

**R8.** It reuses the promoted catalog of ``test_mcp_edge_authority.py`` (the
``world`` fixture) and seeds an audit project into it.

Named mutations that turn this file red (R7):

* B1. ``G/tasks.py`` ``task_stats``: its own WHERE in place of ``_task_scope``
  that leaves out ``task_visibility_clause``.
  ``TestTheCountEqualsTheList`` and ``TestWhatIsNotCounted``.
* B2. ``G/filters.py`` ``build_task_filters``: the ``title_prefix`` clause
  matches anywhere. ``TestEachFilter.test_title_prefix``.
* B3. ``G/filters.py`` ``build_task_filters``: ``is_epic`` ignores the type.
  ``TestEachFilter.test_is_epic``.
* B4. ``G/tasks.py`` ``list_tasks``: the compact cap is dropped.
  ``TestCompact.test_the_page_caps``.
* B5. ``E/tools.py`` ``BOARD_STATS``: the pair is left out of ``TOOLS``.
  ``TestTheToolRecords.test_board_stats_is_a_read_tool``.

``G`` is ``apps/services/gateway/gateway/routes/projects`` and ``E`` is
``apps/services/agent_edge/agent_edge``. Run::

    docker exec metorite-scratch-tenant createdb -U acb <private_db>
    export TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5434/<private_db>
    uv run pytest tests/unit/test_board_stats_r8.py -v -rs
"""
from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from agent_edge import tools

from tests.unit import test_mcp_edge_authority as auth

world = auth.world
armed = auth.armed

_DB_GATE = auth._DB_GATE
MEMBER = auth.MEMBER
OTHER = auth.OTHER
READER = auth.READER

NOW = datetime.now(UTC)


def _ago(days: float) -> str:
    return (NOW - timedelta(days=days)).isoformat()


def _iso_z(days: float) -> str:
    """A query value in the ``Z`` form, so that no ``+`` needs encoding."""
    return (NOW - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


# ═══════════════════════════════════════════════════════════════════════════
# Database-free: the tool records
# ═══════════════════════════════════════════════════════════════════════════


class TestTheToolRecords:
    def test_board_stats_is_a_read_tool(self):
        tool = tools.TOOLS["board_stats"]
        assert tool.method == "GET"
        assert tool.required_scope == "projects:read"
        assert tool.route == "/projects/tasks/stats"
        assert dict(tool.annotations)["readOnlyHint"] is True

    def test_the_new_filters_are_inputs_of_both_tools(self):
        new = {"status_category", "updated_before", "updated_after", "created_after",
               "due_before", "has_parent", "is_epic", "assigned", "title_prefix"}
        for name in ("list_tasks", "board_stats"):
            props = tools.TOOLS[name].input_schema["properties"]
            assert new <= set(props), (name, new - set(props))
            assert set(tools.TOOLS[name].params) >= new

    def test_board_stats_takes_the_group_axes(self):
        rule = tools.TOOLS["board_stats"].input_schema["properties"]["group_by"]
        assert rule["maxItems"] == 2
        assert set(rule["items"]["enum"]) == {
            "project", "status_category", "is_epic", "assignee", "holder",
            "updated_age"}

    def test_the_compact_cap_matches_the_gateway(self):
        from gateway.routes.projects import tasks as gateway_tasks
        assert tools.LIST_COMPACT_LIMIT_MAX == gateway_tasks.COMPACT_MAX_PAGE_SIZE
        assert tools.LIST_LIMIT_MAX == gateway_tasks.MAX_PAGE_SIZE

    def test_the_query_pairs(self):
        query = tools._audit_query({
            "status_category": ["todo", "done"], "has_parent": False,
            "is_epic": True, "assigned": False, "title_prefix": "CRM-Z10a",
            "updated_before": "2026-01-01"})
        assert ("status_category", "todo,done") in query
        assert ("has_parent", "false") in query
        assert ("is_epic", "true") in query
        assert ("assigned", "false") in query
        assert ("title_prefix", "CRM-Z10a") in query
        assert ("updated_before", "2026-01-01") in query

    def test_a_bad_filter_value_is_refused_at_the_edge(self):
        schema = tools.TOOLS["list_tasks"].input_schema
        with pytest.raises(tools.ToolError):
            tools.check_input(schema, {"status_category": ["nope"]})
        with pytest.raises(tools.ToolError):
            tools.check_input(schema, {"updated_before": "yesterday"})
        with pytest.raises(tools.ToolError):
            tools.check_input(schema, {"has_parent": "no"})
        assert tools.check_input(schema, {"updated_before": "2026-11-01T10:00:00Z",
                                          "limit": 500, "compact": True})

    def test_a_plain_page_over_100_is_refused_at_the_edge_run(self):
        import asyncio

        class _Caller:
            lookup = SimpleNamespace(connection_id="c")

        ctx = SimpleNamespace(key=b"k", caller=_Caller(), session=None)
        with pytest.raises(tools.ToolError):
            asyncio.run(tools._list_tasks(ctx, {"limit": 101}))  # type: ignore[arg-type]

    def test_a_compact_row_has_the_named_keys(self):
        row = tools._compact_row(
            {"id": "i", "task_number": 3, "title": "T", "project_id": "p",
             "status_id": "s", "status_category": "todo", "parent_task_id": None,
             "updated_at": "u", "due_at": None, "assignees": ["x"]},
            {"p": {"s": "To do"}})
        assert tuple(row) == tools.COMPACT_TASK_KEYS
        # MCP-G19c adds three keys. `tests/unit/test_mcp_g19c_r8.py` holds them.
        assert tools.COMPACT_TASK_KEYS[-3:] == ("type", "tags", "created_at")
        assert row["status_category"] == "todo"
        assert row["status_name"] == {"untrusted_text": "To do"}


# ═══════════════════════════════════════════════════════════════════════════
# R8: the data
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture(scope="module")
def board(world):
    """An audit project in organization A, and one task set in B.

    Audit (granted to MEMBER), with the statuses Todo, Doing and Done and the
    types Epic and Task:

    * ``CRM-Z10a one``   epic, Doing, 40 days old, no assignee, held by MEMBER
    * ``CRM-Z10a two``   task, Todo, 2 days old, MEMBER and OTHER assigned
    * ``crm-z10b sub``   task, Done, 10 days old, subtask of ``one``, due 5 days ago
    * ``Plain``          task, Todo, 0.2 days old, MEMBER assigned, due in 9 days
    * ``Under_score``    task, Todo, 5 days old, to prove the LIKE escape

    ``Secret`` has no grant for MEMBER and holds ``SECRET one``. OTHER's
    personal project holds ``PRIVATE of other``. Organization B holds
    ``CRM-Z10a org B``.
    """
    a = world.seeder_a
    b = auth._Seeder(world.admin, world.org_b)
    audit = a.project("Audit", grant_to=MEMBER)
    secret = a.project("Secret")
    private = a.project("Others private", personal_owner="board-other@mcpauth.test")
    s = {}
    for name, category, pos in (("Todo", "todo", 0), ("Doing", "in_progress", 1),
                                ("Done", "done", 2)):
        s[category] = a.one(
            "INSERT INTO pm_task_statuses (project_id, name, color, position,"
            " category, organization_id) VALUES (CAST(:p AS uuid), :n, 'gray', :pos,"
            " :c, CAST(:o AS uuid)) RETURNING id",
            p=audit, n=name, pos=pos, c=category, o=a.org)
    epic = a.one(
        "INSERT INTO pm_task_types (project_id, name, is_epic, organization_id)"
        " VALUES (CAST(:p AS uuid), 'Epic', true, CAST(:o AS uuid)) RETURNING id",
        p=audit, o=a.org)
    kind = a.one(
        "INSERT INTO pm_task_types (project_id, name, organization_id)"
        " VALUES (CAST(:p AS uuid), 'Task', CAST(:o AS uuid)) RETURNING id",
        p=audit, o=a.org)
    counter = {"n": 5000}

    def task(seeder, project, root, title, *, status, type_id=None, parent=None,
             updated_days=1.0, due=None, assignees=(), org=None):
        counter["n"] += 1
        tid = str(seeder.one(
            "INSERT INTO pm_tasks (title, project_id, root_project_id, status_id,"
            " type_id, parent_task_id, created_by, organization_id, task_number,"
            " created_at, updated_at, due_at) VALUES (:t, CAST(:p AS uuid),"
            " CAST(:r AS uuid), CAST(:s AS uuid), CAST(:ty AS uuid),"
            " CAST(:pa AS uuid), :a, CAST(:o AS uuid), :n,"
            " CAST(:ca AS timestamptz), CAST(:ua AS timestamptz),"
            " CAST(:due AS timestamptz)) RETURNING id",
            t=title, p=project, r=root, s=str(status), ty=type_id, pa=parent,
            a=auth.OWNER, o=seeder.org, n=counter["n"] + (0 if seeder is a else 1000),
            ca=_ago(updated_days + 1), ua=_ago(updated_days), due=due))
        for who in assignees:
            seeder.one(
                "INSERT INTO pm_task_assignees (task_id, assignee, assigned_by,"
                " organization_id) VALUES (CAST(:t AS uuid), :e, :a,"
                " CAST(:o AS uuid)) RETURNING task_id",
                t=tid, e=who, a=auth.OWNER, o=seeder.org)
        return tid

    t = {}
    t["one"] = task(a, audit, audit, "CRM-Z10a one", status=s["in_progress"],
                    type_id=str(epic), updated_days=40)
    t["two"] = task(a, audit, audit, "CRM-Z10a two", status=s["todo"],
                    type_id=str(kind), updated_days=2, assignees=(MEMBER, OTHER))
    t["sub"] = task(a, audit, audit, "crm-z10b sub", status=s["done"],
                    type_id=str(kind), parent=t["one"], updated_days=10,
                    due=_ago(5))
    t["plain"] = task(a, audit, audit, "Plain", status=s["todo"], type_id=str(kind),
                      updated_days=0.2, due=(NOW + timedelta(days=9)).isoformat(),
                      assignees=(MEMBER,))
    t["under"] = task(a, audit, audit, "Under_score", status=s["todo"],
                      updated_days=5)
    t["secret"] = task(a, secret, secret, "SECRET one", status=s["todo"])
    t["private"] = task(a, private, private, "PRIVATE of other", status=s["todo"])
    status_b = b.one(
        "INSERT INTO pm_task_statuses (project_id, name, color, position, category,"
        " organization_id) VALUES (CAST(:p AS uuid), 'Todo', 'gray', 0, 'todo',"
        " CAST(:o AS uuid)) RETURNING id", p=world.p["b_root"], o=b.org)
    t["org_b"] = task(b, world.p["b_root"], world.p["b_root"], "CRM-Z10a org B",
                      status=status_b, assignees=(auth.B_MEMBER,))
    # An open run of MEMBER holds `one`. A finished run holds nothing.
    a.one(
        "INSERT INTO pm_agent_runs (task_id, member_email, client_via, status,"
        " started_by, last_activity_at) VALUES (CAST(:t AS uuid), :m,"
        " 'mcp:client.example:meta', 'in_progress', :m, now()) RETURNING id",
        t=t["one"], m=MEMBER)
    a.one(
        "INSERT INTO pm_agent_runs (task_id, member_email, client_via, status,"
        " started_by, last_activity_at) VALUES (CAST(:t AS uuid), :m,"
        " 'mcp:client.example:meta', 'completed', :m, now()) RETURNING id",
        t=t["two"], m=MEMBER)
    return SimpleNamespace(audit=audit, secret=secret, private=private, t=t, s=s)


# ═══════════════════════════════════════════════════════════════════════════
# R8: the helpers
# ═══════════════════════════════════════════════════════════════════════════

#: Who reads. A name, then how: a connection id key of ``world.c``, or a
#: member address for a human.
CALLERS = {
    "human": ("human", MEMBER),
    "agent": ("agent", "open"),
    "pinned": ("agent", "pinned"),
    "no_personal": ("agent", "no_personal"),
    "reader_agent": ("agent", "reader"),
    "reader_human": ("human", READER),
    "org_b": ("agent", "b_member"),
}


async def _get(world, who: str, path: str, query: str = ""):
    kind, key = CALLERS[who]
    if kind == "human":
        return await auth._human(world, key, path, query)
    org = world.org_b if who == "org_b" else world.org_a
    return await auth._agent(world, world.c[key], path, query, org=org)


async def _list_all(world, who: str, query: str = "", *, compact: bool = False):
    """Every row of ``list_tasks`` for the caller, by paging."""
    rows: list[dict[str, Any]] = []
    page = 1
    total = None
    while True:
        q = f"{query}&" if query else ""
        q += f"page={page}&page_size={500 if compact else 100}"
        if compact:
            q += "&compact=true"
        r = await _get(world, who, "/projects/tasks", q)
        assert r.status_code == 200, (who, query, r.status_code, r.text)
        body = r.json()
        total = body["total"]
        rows += body["rows"]
        if len(rows) >= total or not body["rows"]:
            return rows, total
        page += 1


async def _stats(world, who: str, query: str = ""):
    r = await _get(world, who, "/projects/tasks/stats", query)
    assert r.status_code == 200, (who, query, r.status_code, r.text)
    return r.json()


def _titles(rows) -> set[str]:
    return {row["title"] for row in rows}


# ═══════════════════════════════════════════════════════════════════════════
# R8: the count equals the list
# ═══════════════════════════════════════════════════════════════════════════

FILTERS = [
    "",
    "status_category=todo",
    "status_category=todo,in_progress",
    "status_category=done",
    "has_parent=false",
    "has_parent=true",
    "is_epic=true",
    "is_epic=false",
    "assigned=false",
    "assigned=true",
    "title_prefix=crm-z10a",
    "title_prefix=CRM",
    f"updated_before={_iso_z(30)}",
    f"updated_after={_iso_z(3)}",
    f"created_after={_iso_z(3)}",
    f"due_before={_iso_z(0)}",
    "unheld=true",
    "has_parent=false&assigned=false&status_category=in_progress",
    f"title_prefix=CRM&updated_before={_iso_z(1)}&is_epic=false",
    "assignee=" + MEMBER,
    "q=Plain",
]


@_DB_GATE
class TestTheCountEqualsTheList:
    """The pin of the slice: ``total`` of the stats is ``total`` of the list
    and the number of rows that the list returns, for each caller and filter."""

    @pytest.mark.parametrize("who", sorted(CALLERS))
    async def test_every_filter_for_every_caller(self, armed, board, who):
        for query in FILTERS:
            rows, total = await _list_all(armed, who, query)
            stats = await _stats(armed, who, query)
            assert stats["total"] == total == len(rows), (who, query)

    @pytest.mark.parametrize("who", ["human", "agent", "pinned", "org_b"])
    async def test_the_compact_list_reads_the_same_rows(self, armed, board, who):
        for query in FILTERS:
            plain, total = await _list_all(armed, who, query)
            compact, ctotal = await _list_all(armed, who, query, compact=True)
            assert ctotal == total
            assert {r["id"] for r in compact} == {r["id"] for r in plain}, (who, query)

    @pytest.mark.parametrize("axis", ["project", "status_category", "is_epic",
                                      "holder", "updated_age"])
    async def test_a_group_axis_sums_to_the_total(self, armed, board, axis):
        for who in ("human", "agent", "pinned"):
            stats = await _stats(armed, who, f"group_by={axis}")
            assert sum(g["count"] for g in stats["groups"]) == stats["total"], (who, axis)
            _, total = await _list_all(armed, who, "")
            assert stats["total"] == total

    async def test_two_axes_sum_to_the_total(self, armed, board):
        stats = await _stats(armed, "human", "group_by=project,status_category")
        assert sum(g["count"] for g in stats["groups"]) == stats["total"]
        assert all(set(g["keys"]) == {"project", "status_category"}
                   for g in stats["groups"])

    async def test_the_project_scope_is_shared(self, armed, board):
        for sub in ("true", "false"):
            query = f"project_id={board.audit}&include_subtree={sub}"
            rows, total = await _list_all(armed, "human", query)
            stats = await _stats(armed, "human", query)
            assert stats["total"] == total == len(rows) == 5

    async def test_a_hidden_project_is_a_404_for_both(self, armed, board):
        query = f"project_id={board.secret}"
        for path in ("/projects/tasks", "/projects/tasks/stats"):
            r = await _get(armed, "human", path, query)
            assert r.status_code == 404, (path, r.text)


@_DB_GATE
class TestTheGroups:
    """Each axis counts what the rows say."""

    async def test_status_category(self, armed, board):
        rows, _ = await _list_all(armed, "human", f"project_id={board.audit}")
        want = Counter(r["status_category"] for r in rows)
        stats = await _stats(armed, "human",
                             f"group_by=status_category&project_id={board.audit}")
        got = {g["keys"]["status_category"]: g["count"] for g in stats["groups"]}
        assert got == dict(want) == {"todo": 3, "in_progress": 1, "done": 1}

    async def test_project(self, armed, board):
        rows, _ = await _list_all(armed, "human", "")
        want = Counter(str(r["project_id"]) for r in rows)
        stats = await _stats(armed, "human", "group_by=project")
        got = {g["keys"]["project"]: g["count"] for g in stats["groups"]}
        assert got == dict(want)

    async def test_is_epic(self, armed, board):
        stats = await _stats(armed, "human",
                             f"group_by=is_epic&project_id={board.audit}")
        got = {g["keys"]["is_epic"]: g["count"] for g in stats["groups"]}
        assert got == {True: 1, False: 4}

    async def test_updated_age_bands(self, armed, board):
        stats = await _stats(armed, "human",
                             f"group_by=updated_age&project_id={board.audit}")
        got = {g["keys"]["updated_age"]: g["count"] for g in stats["groups"]}
        # 0.2d, 2d, 5d, 10d and 40d old.
        assert got == {"<1d": 1, "1-3d": 1, "3-7d": 1, "7-30d": 1, ">30d": 1}

    async def test_holder(self, armed, board):
        stats = await _stats(armed, "human",
                             f"group_by=holder&project_id={board.audit}")
        got = {g["keys"]["holder"]: g["count"] for g in stats["groups"]}
        assert got == {MEMBER: 1, None: 4}
        free = await _stats(armed, "human",
                            f"unheld=true&project_id={board.audit}")
        assert free["total"] == 4

    async def test_assignee_counts_a_task_once_for_each_assignee(self, armed, board):
        stats = await _stats(armed, "human",
                             f"group_by=assignee&project_id={board.audit}")
        got = {g["keys"]["assignee"]: g["count"] for g in stats["groups"]}
        assert got == {MEMBER: 2, OTHER: 1, None: 3}
        assert stats["total"] == 5

    async def test_a_bad_axis_is_422(self, armed, board):
        for query in ("group_by=nope", "group_by=project,project",
                      "group_by=project,assignee,holder"):
            r = await _get(armed, "human", "/projects/tasks/stats", query)
            assert r.status_code == 422, (query, r.text)


@_DB_GATE
class TestWhatIsNotCounted:
    """A private project of member B, a project with no grant for A, and the
    tasks of another organization stay out of the count of member A."""

    async def test_member_a_does_not_count_what_it_cannot_see(self, armed, board):
        for who in ("human", "agent", "pinned", "no_personal"):
            rows, _ = await _list_all(armed, who, "")
            titles = _titles(rows)
            assert "SECRET one" not in titles, who
            assert "PRIVATE of other" not in titles, who
            assert "CRM-Z10a org B" not in titles, who
            for needle in ("SECRET", "PRIVATE"):
                stats = await _stats(armed, who, f"title_prefix={needle}")
                assert stats["total"] == 0, (who, needle)
            stats = await _stats(armed, who, "title_prefix=CRM-Z10a")
            assert stats["total"] == (0 if who == "pinned" else 2), who

    async def test_group_counts_leave_the_hidden_tasks_out_too(self, armed, board):
        stats = await _stats(armed, "human", "group_by=project")
        projects = {g["keys"]["project"] for g in stats["groups"]}
        assert board.secret not in projects
        assert board.private not in projects

    async def test_a_pin_narrows_the_count(self, armed, board):
        stats = await _stats(armed, "pinned", "")
        _, total = await _list_all(armed, "pinned", "")
        assert stats["total"] == total
        open_stats = await _stats(armed, "agent", "")
        assert stats["total"] < open_stats["total"]

    async def test_the_personal_switch_narrows_the_count(self, armed, board):
        with_personal = await _stats(armed, "agent", "")
        without = await _stats(armed, "no_personal", "")
        assert without["total"] < with_personal["total"]

    async def test_organization_b_counts_only_its_own(self, armed, board):
        stats = await _stats(armed, "org_b", "title_prefix=CRM-Z10a")
        assert stats["total"] == 1
        rows, _ = await _list_all(armed, "org_b", "")
        assert _titles(rows) == {"CRM-Z10a org B"}
        member_a = await _stats(armed, "human", "title_prefix=CRM-Z10a")
        assert member_a["total"] == 2

    async def test_the_reader_does_not_count_another_members_personal_project(
            self, armed, board):
        stats = await _stats(armed, "reader_human", "title_prefix=PRIVATE")
        assert stats["total"] == 0


@_DB_GATE
class TestEachFilter:
    """One test per new filter: it returns what the data says."""

    async def _titles_of(self, armed, board, query: str, who: str = "human") -> set[str]:
        rows, total = await _list_all(armed, who, f"project_id={board.audit}&{query}")
        assert total == len(rows)
        return _titles(rows)

    async def test_status_category(self, armed, board):
        assert await self._titles_of(armed, board, "status_category=done") == {"crm-z10b sub"}
        assert await self._titles_of(armed, board, "status_category=done,in_progress") == {
            "crm-z10b sub", "CRM-Z10a one"}

    async def test_updated_before(self, armed, board):
        assert await self._titles_of(armed, board, f"updated_before={_iso_z(30)}") == {
            "CRM-Z10a one"}

    async def test_updated_after(self, armed, board):
        assert await self._titles_of(armed, board, f"updated_after={_iso_z(3)}") == {
            "Plain", "CRM-Z10a two"}

    async def test_created_after(self, armed, board):
        # `created_at` is one day before `updated_at` in the data.
        assert await self._titles_of(armed, board, f"created_after={_iso_z(3.5)}") == {
            "Plain", "CRM-Z10a two"}

    async def test_due_before(self, armed, board):
        assert await self._titles_of(armed, board, f"due_before={_iso_z(0)}") == {
            "crm-z10b sub"}

    async def test_has_parent(self, armed, board):
        assert await self._titles_of(armed, board, "has_parent=true") == {"crm-z10b sub"}
        assert "crm-z10b sub" not in await self._titles_of(armed, board, "has_parent=false")

    async def test_is_epic(self, armed, board):
        assert await self._titles_of(armed, board, "is_epic=true") == {"CRM-Z10a one"}
        assert "CRM-Z10a one" not in await self._titles_of(armed, board, "is_epic=false")
        # A task with no type is not an epic.
        assert "Under_score" in await self._titles_of(armed, board, "is_epic=false")

    async def test_assigned(self, armed, board):
        assert await self._titles_of(armed, board, "assigned=true") == {
            "CRM-Z10a two", "Plain"}
        assert await self._titles_of(armed, board, "assigned=false") == {
            "CRM-Z10a one", "crm-z10b sub", "Under_score"}

    async def test_title_prefix(self, armed, board):
        assert await self._titles_of(armed, board, "title_prefix=crm-z10a") == {
            "CRM-Z10a one", "CRM-Z10a two"}
        # A prefix, not a substring.
        assert await self._titles_of(armed, board, "title_prefix=z10a") == set()

    async def test_title_prefix_escapes_like(self, armed, board):
        assert await self._titles_of(armed, board, "title_prefix=Under_") == {"Under_score"}
        assert await self._titles_of(armed, board, "title_prefix=Under%25") == set()
        assert await self._titles_of(armed, board, "title_prefix=UnderXscore") == set()

    async def test_a_bad_date_is_422(self, armed, board):
        for name in ("updated_before", "updated_after", "created_after", "due_before"):
            for path in ("/projects/tasks", "/projects/tasks/stats"):
                r = await _get(armed, "human", path, f"{name}=yesterday")
                assert r.status_code == 422, (name, path, r.text)

    async def test_filters_combine(self, armed, board):
        assert await self._titles_of(
            armed, board, "has_parent=false&assigned=false&status_category=in_progress"
        ) == {"CRM-Z10a one"}


@_DB_GATE
class TestCompact:
    async def test_a_compact_row_has_the_small_keys(self, armed, board):
        r = await _get(armed, "human", "/projects/tasks",
                       f"compact=true&project_id={board.audit}")
        assert r.status_code == 200, r.text
        row = r.json()["rows"][0]
        assert row["status_category"] in {"todo", "in_progress", "done"}
        assert not row.get("assignees")
        assert not row.get("held_by")

    async def test_the_page_caps(self, armed, board):
        ok = await _get(armed, "human", "/projects/tasks", "compact=true&page_size=500")
        assert ok.status_code == 200, ok.text
        over = await _get(armed, "human", "/projects/tasks",
                          "compact=true&page_size=501")
        assert over.status_code == 422
        plain = await _get(armed, "human", "/projects/tasks", "page_size=101")
        assert plain.status_code == 422
        plain_ok = await _get(armed, "human", "/projects/tasks", "page_size=100")
        assert plain_ok.status_code == 200
