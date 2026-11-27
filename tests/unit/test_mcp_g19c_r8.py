"""WS-55 MCP-G19c — the type, the tags and the number of a task over MCP.

Spec: ``project-docs/specs/external_agent_access.md`` (the header line of
MCP-G19c) · Metorite task 2315.

Four changes for an agent that audits a board:

1. A ``compact`` row of ``list_tasks`` holds ``type`` (id and name), ``tags``
   and ``created_at``.
2. ``list_tasks`` filters by ``type_id``, ``type_name`` and ``tag``. They
   combine with ``status_category`` and the other filters.
3. ``list_tasks`` takes ``task_number``, so one call reads a task by its
   number. A number is unique only in one root project, so the list can hold
   one task for each root project that the member sees.
4. ``get_project`` with ``compact`` returns the project, its statuses and its
   types. It leaves out the field definitions and the tag list.

**The rule that matters.** Each filter is a clause of ``build_task_filters``,
which ``tasks._task_scope`` joins to the visibility clause. So a filter never
shows a task that the member cannot see. The R8 classes prove it with two
members of organization A, a hidden project, a private project of a third
member, and organization B. Each holds a task that matches every filter.

Named mutations that turn this file red (R7):

* C1. ``G/filters.py`` ``build_task_filters``: the ``type_id`` clause is
  dropped. ``TestTheFilters.test_type_id``.
* C2. ``G/filters.py``: the ``type_name`` match is case sensitive.
  ``TestTheFilters.test_type_name``.
* C3. ``G/filters.py``: the ``task_number`` clause is dropped.
  ``TestTheNumber.test_one_number_reads_one_task_in_each_root``.
* C4. ``G/tasks.py`` ``list_tasks``: the filters skip ``_task_scope`` and its
  visibility clause. ``TestWhatStaysHidden``.
* C5. ``G/tasks.py``: ``attach_type_name`` is not called for a compact read.
  ``TestTheCompactRow.test_the_row_names_its_type``.
* C6. ``E/tools.py`` ``_compact_row``: ``tags`` is a raw string list.
  ``TestTheEdge.test_a_compact_row_has_the_new_keys``.
* C7. ``E/tools.py`` ``_get_project``: ``compact`` reads the tags.
  ``TestTheEdge.test_a_compact_project_reads_no_fields_and_no_tags``.

``G`` is ``apps/services/gateway/gateway/routes/projects`` and ``E`` is
``apps/services/agent_edge/agent_edge``. Run::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_mcp_g19c_r8.py -v -rs
"""
from __future__ import annotations

import asyncio
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
READER = auth.READER

#: The number that every seeded task with the name ``... bug one`` holds. Each
#: one is in another root project, so the unique key allows it.
SHARED_NUMBER = 7001


# ═══════════════════════════════════════════════════════════════════════════
# Database-free: the edge
# ═══════════════════════════════════════════════════════════════════════════


def _ctx() -> Any:
    class _Caller:
        lookup = SimpleNamespace(connection_id="c")

    return SimpleNamespace(key=b"k", caller=_Caller(), session=None)


class TestTheEdge:
    def test_the_new_inputs_map_to_the_route(self):
        tool = tools.TOOLS["list_tasks"]
        props = tool.input_schema["properties"]
        for name in ("type_id", "type_name", "task_number", "tag"):
            assert name in props, name
        assert tool.params["type_id"] == "type_id"
        assert tool.params["type_name"] == "type_name"
        assert tool.params["task_number"] == "task_number"
        assert tool.params["tag"] == "tags"

    def test_the_number_bound_matches_the_gateway(self):
        from gateway.routes.projects import tasks as gateway_tasks
        assert tools.TASK_NUMBER_MAX == gateway_tasks.TASK_NUMBER_MAX
        rule = tools.TOOLS["list_tasks"].input_schema["properties"]["task_number"]
        assert rule["minimum"] == 1 and rule["maximum"] == tools.TASK_NUMBER_MAX
        name = tools.TOOLS["list_tasks"].input_schema["properties"]["type_name"]
        assert name["maxLength"] == gateway_tasks.MAX_TYPE_NAME

    def test_a_bad_value_is_refused_at_the_edge(self):
        schema = tools.TOOLS["list_tasks"].input_schema
        for bad in ({"type_id": "not-a-uuid"}, {"type_name": ""},
                    {"task_number": 0}, {"task_number": "7"}):
            with pytest.raises(tools.ToolError):
                tools.check_input(schema, bad)
        assert tools.check_input(schema, {"task_number": 7, "type_name": "Bug",
                                          "tag": "audit", "compact": True})

    def test_the_run_sends_each_filter(self, monkeypatch):
        sent: list[list[tuple[str, str]]] = []

        async def _get(ctx, template, not_found, *, path_params=None, query=None,
                       timeout_s=None):
            if template == tools.LIST_TASKS_ROUTE:
                sent.append(list(query or []))
                return {"rows": [], "total": 0}
            return {"rows": []}

        monkeypatch.setattr(tools, "_get", _get)
        asyncio.run(tools._list_tasks(_ctx(), {
            "type_id": "00000000-0000-0000-0000-000000000001", "type_name": "Bug",
            "task_number": 7001, "tag": "audit", "status_category": ["todo"],
            "compact": True}))
        query = sent[0]
        assert ("type_id", "00000000-0000-0000-0000-000000000001") in query
        assert ("type_name", "Bug") in query
        assert ("task_number", "7001") in query
        assert ("tags", "audit") in query
        assert ("status_category", "todo") in query
        assert ("compact", "true") in query

    def test_a_compact_row_has_the_new_keys(self):
        row = tools._compact_row(
            {"id": "i", "task_number": 3, "title": "T", "project_id": "p",
             "status_id": "s", "status_category": "todo", "parent_task_id": None,
             "updated_at": "u", "due_at": None, "type_id": "ty", "type_name": "Bug",
             "tags": ["audit", 7], "created_at": "c"},
            {"p": {"s": "To do"}})
        assert tuple(row) == tools.COMPACT_TASK_KEYS
        assert {"type", "tags", "created_at"} <= set(tools.COMPACT_TASK_KEYS)
        assert row["type"] == {"id": "ty", "name": {"untrusted_text": "Bug"}}
        # A tag is free text, so it is (U). A value that is not text drops.
        assert row["tags"] == [{"untrusted_text": "audit"}]
        assert row["created_at"] == "c"

    def test_a_row_with_no_type_has_a_null_type(self):
        row = tools._compact_row({"id": "i", "project_id": "p"}, {})
        assert row["type"] is None
        assert row["tags"] == []

    def test_the_compact_row_meets_the_output_schema(self):
        jsonschema = pytest.importorskip("jsonschema")
        rows = [
            tools._compact_row({"id": "i", "project_id": "p", "type_id": "t",
                                "type_name": None, "tags": ["a"]}, {}),
            tools._compact_row({"id": "j", "project_id": "p"}, {}),
        ]
        jsonschema.validate({"tasks": rows, "total": 2, "nextCursor": None},
                            tools.TOOLS["list_tasks"].output_schema)

    def _project_reads(self, monkeypatch, args):
        read: list[str] = []

        async def _get(ctx, template, not_found, *, path_params=None, query=None,
                       timeout_s=None):
            read.append(template)
            if template == tools.NODE_ROUTE:
                return {"id": path_params["project_id"], "name": "Audit"}
            if template == tools.STATUSES_ROUTE:
                return {"rows": [{"id": "s1", "name": "Todo", "category": "todo"}]}
            if template == tools.TYPES_ROUTE:
                return {"rows": [{"id": "t1", "name": "Bug", "is_epic": False}]}
            return {"rows": [{"id": "x", "name": "x"}]}

        monkeypatch.setattr(tools, "_get", _get)
        body = asyncio.run(tools._get_project(_ctx(), args))
        return read, body

    def test_a_compact_project_reads_no_fields_and_no_tags(self, monkeypatch):
        pid = "00000000-0000-0000-0000-0000000000aa"
        read, body = self._project_reads(monkeypatch, {"project_id": pid, "compact": True})
        assert tools.FIELDS_ROUTE not in read and tools.TAGS_ROUTE not in read
        assert set(body) == {"project", "statuses", "types"}
        assert body["statuses"][0]["id"] == "s1"
        assert body["types"][0]["id"] == "t1"
        jsonschema = pytest.importorskip("jsonschema")
        jsonschema.validate(body, tools.TOOLS["get_project"].output_schema)

    def test_a_full_project_still_holds_all_five(self, monkeypatch):
        pid = "00000000-0000-0000-0000-0000000000aa"
        read, body = self._project_reads(monkeypatch, {"project_id": pid})
        assert tools.FIELDS_ROUTE in read and tools.TAGS_ROUTE in read
        assert set(body) == {"project", "statuses", "types", "fields", "tags"}
        jsonschema = pytest.importorskip("jsonschema")
        jsonschema.validate(body, tools.TOOLS["get_project"].output_schema)

    def test_compact_is_a_named_edge_input_of_get_project(self):
        tool = tools.TOOLS["get_project"]
        assert "compact" in tool.input_schema["properties"]
        assert "compact" in tool.edge_inputs


# ═══════════════════════════════════════════════════════════════════════════
# R8: the data
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture(scope="module")
def g19c(world):
    """Two seen projects, two hidden ones, and organization B.

    Organization A:

    * ``Audit`` (granted to MEMBER). Types ``Bug`` and ``Task``.
      ``Audit bug one`` (Bug, tag audit, Todo, number 7001),
      ``Audit bug two`` (Bug, tag other, Done),
      ``Audit task`` (Task, tag audit, Todo) and ``Audit no type`` (Todo).
    * ``Second`` (granted to MEMBER). Type ``bug``, in lower case.
      ``Second bug one`` (bug, tag audit, Todo, number 7001).
    * ``Hidden`` (no grant). Type ``Bug``. ``Hidden bug one`` (Bug, tag audit,
      Todo, number 7001). READER sees it through ``data:org:read``, and
      MEMBER does not.
    * ``Theirs`` (the personal project of OTHER, from the world). Type ``Bug``.
      ``Private bug one`` (Bug, tag audit, Todo, number 7001). Neither
      member sees it.

    Organization B: ``Org B bug one`` (Bug, tag audit, Todo, number 7001).
    """
    a = world.seeder_a
    b = auth._Seeder(world.admin, world.org_b)
    counter = {"n": 7100}

    def project(seeder, name, pid=None, **kw):
        pid = pid or seeder.project(name, **kw)
        status = {}
        for label, category, pos in (("Todo", "todo", 0), ("Done", "done", 1)):
            status[category] = seeder.one(
                "INSERT INTO pm_task_statuses (project_id, name, color, position,"
                " category, organization_id) VALUES (CAST(:p AS uuid), :n, 'gray',"
                " :pos, :c, CAST(:o AS uuid)) RETURNING id",
                p=pid, n=label, pos=pos, c=category, o=seeder.org)
        return pid, status

    def kind(seeder, pid, name):
        return str(seeder.one(
            "INSERT INTO pm_task_types (project_id, name, organization_id)"
            " VALUES (CAST(:p AS uuid), :n, CAST(:o AS uuid)) RETURNING id",
            p=pid, n=name, o=seeder.org))

    def task(seeder, pid, title, *, status, type_id=None, tags=(), number=None):
        if number is None:
            counter["n"] += 1
            number = counter["n"]
        return str(seeder.one(
            "INSERT INTO pm_tasks (title, project_id, root_project_id, status_id,"
            " type_id, tags, created_by, organization_id, task_number) VALUES (:t,"
            " CAST(:p AS uuid), CAST(:p AS uuid), CAST(:s AS uuid),"
            " CAST(:ty AS uuid), CAST(:tags AS text[]), :a, CAST(:o AS uuid), :n)"
            " RETURNING id",
            t=title, p=pid, s=str(status), ty=type_id, tags=list(tags),
            a=auth.OWNER, o=seeder.org, n=number))

    audit, s_audit = project(a, "G19c Audit", grant_to=MEMBER)
    second, s_second = project(a, "G19c Second", grant_to=MEMBER)
    hidden, s_hidden = project(a, "G19c Hidden")
    # One personal root for each member: the world holds the one of OTHER.
    private, s_private = project(a, "G19c Private", pid=world.p["personal_other"])
    org_b, s_b = project(b, "G19c Org B", grant_to=auth.B_MEMBER)
    ty = {
        "audit_bug": kind(a, audit, "Bug"),
        "audit_task": kind(a, audit, "Task"),
        "second_bug": kind(a, second, "bug"),
        "hidden_bug": kind(a, hidden, "Bug"),
        "private_bug": kind(a, private, "Bug"),
        "b_bug": kind(b, org_b, "Bug"),
    }
    t = {
        "one": task(a, audit, "Audit bug one", status=s_audit["todo"],
                    type_id=ty["audit_bug"], tags=("audit",), number=SHARED_NUMBER),
        "two": task(a, audit, "Audit bug two", status=s_audit["done"],
                    type_id=ty["audit_bug"], tags=("other",)),
        "task": task(a, audit, "Audit task", status=s_audit["todo"],
                     type_id=ty["audit_task"], tags=("audit",)),
        "none": task(a, audit, "Audit no type", status=s_audit["todo"]),
        "second": task(a, second, "Second bug one", status=s_second["todo"],
                       type_id=ty["second_bug"], tags=("audit",),
                       number=SHARED_NUMBER),
        "hidden": task(a, hidden, "Hidden bug one", status=s_hidden["todo"],
                       type_id=ty["hidden_bug"], tags=("audit",),
                       number=SHARED_NUMBER),
        "private": task(a, private, "Private bug one", status=s_private["todo"],
                        type_id=ty["private_bug"], tags=("audit",),
                        number=SHARED_NUMBER),
        "org_b": task(b, org_b, "Org B bug one", status=s_b["todo"],
                      type_id=ty["b_bug"], tags=("audit",), number=SHARED_NUMBER),
    }
    return SimpleNamespace(audit=audit, second=second, hidden=hidden,
                           private=private, org_b=org_b, ty=ty, t=t)


# ═══════════════════════════════════════════════════════════════════════════
# R8: the helpers
# ═══════════════════════════════════════════════════════════════════════════

#: Who reads: a name, then how. A connection id key of ``world.c``, or a
#: member address for a human.
CALLERS = {
    "member": ("human", MEMBER),
    "member_agent": ("agent", "open"),
    "reader": ("human", READER),
    "reader_agent": ("agent", "reader"),
    "org_b": ("agent", "b_member"),
}

#: The ``G19c`` titles that each caller can see.
SEEN = {
    "member": {"Audit bug one", "Audit bug two", "Audit task", "Audit no type",
               "Second bug one"},
    "reader": {"Audit bug one", "Audit bug two", "Audit task", "Audit no type",
               "Second bug one", "Hidden bug one"},
    "org_b": {"Org B bug one"},
}
SEEN["member_agent"] = SEEN["member"]
SEEN["reader_agent"] = SEEN["reader"]

#: The titles that no caller of organization A ever sees.
NEVER_IN_A = {"Private bug one", "Org B bug one"}


async def _get(world, who: str, path: str, query: str = ""):
    kind, key = CALLERS[who]
    if kind == "human":
        return await auth._human(world, key, path, query)
    org = world.org_b if who == "org_b" else world.org_a
    return await auth._agent(world, world.c[key], path, query, org=org)


async def _rows(world, who: str, query: str, *, compact: bool = True):
    size = 500 if compact else 100
    q = f"{query}&page_size={size}" if query else f"page_size={size}"
    if compact:
        q += "&compact=true"
    r = await _get(world, who, "/projects/tasks", q)
    assert r.status_code == 200, (who, query, r.status_code, r.text)
    body = r.json()
    assert body["total"] == len(body["rows"]), (who, query)
    return body["rows"]


async def _titles(world, who: str, query: str, *, compact: bool = True) -> set[str]:
    rows = await _rows(world, who, query, compact=compact)
    return {row["title"] for row in rows if row["title"] in SEEN["reader"]
            or row["title"] in NEVER_IN_A}


# ═══════════════════════════════════════════════════════════════════════════
# R8: each filter, for each caller
# ═══════════════════════════════════════════════════════════════════════════


@_DB_GATE
class TestTheFilters:
    async def test_type_id(self, armed, g19c):
        query = f"type_id={g19c.ty['audit_bug']}"
        for who in ("member", "member_agent", "reader", "reader_agent"):
            assert await _titles(armed, who, query) == {
                "Audit bug one", "Audit bug two"}, who
        # The type of a hidden project gives an empty list, not its task.
        for who in ("member", "member_agent"):
            assert await _titles(armed, who, f"type_id={g19c.ty['hidden_bug']}") == set()
        assert await _titles(armed, "reader", f"type_id={g19c.ty['hidden_bug']}") == {
            "Hidden bug one"}
        # A type of organization B gives nothing in organization A.
        assert await _titles(armed, "member", f"type_id={g19c.ty['b_bug']}") == set()

    async def test_a_bad_type_id_is_422(self, armed, g19c):
        r = await _get(armed, "member", "/projects/tasks", "type_id=nope")
        assert r.status_code == 422, r.text

    async def test_type_name(self, armed, g19c):
        # Not case sensitive: `Bug` matches the `bug` type of Second too.
        for name in ("Bug", "bug", "BUG"):
            for who in ("member", "member_agent"):
                assert await _titles(armed, who, f"type_name={name}") == {
                    "Audit bug one", "Audit bug two", "Second bug one"}, (who, name)
            for who in ("reader", "reader_agent"):
                assert await _titles(armed, who, f"type_name={name}") == {
                    "Audit bug one", "Audit bug two", "Second bug one",
                    "Hidden bug one"}, (who, name)
        assert await _titles(armed, "org_b", "type_name=bug") == {"Org B bug one"}
        # An exact name, not a prefix.
        assert await _titles(armed, "member", "type_name=Bu") == set()

    async def test_tag(self, armed, g19c):
        assert await _titles(armed, "member", "tags=audit") == {
            "Audit bug one", "Audit task", "Second bug one"}
        assert await _titles(armed, "reader_agent", "tags=audit") == {
            "Audit bug one", "Audit task", "Second bug one", "Hidden bug one"}

    async def test_the_filters_combine(self, armed, g19c):
        query = "type_name=bug&tags=audit&status_category=todo"
        assert await _titles(armed, "member", query) == {
            "Audit bug one", "Second bug one"}
        assert await _titles(armed, "reader", query) == {
            "Audit bug one", "Second bug one", "Hidden bug one"}
        query = f"type_id={g19c.ty['audit_bug']}&status_category=done"
        assert await _titles(armed, "member_agent", query) == {"Audit bug two"}
        query = f"type_name=Bug&project_id={g19c.second}"
        assert await _titles(armed, "member", query) == {"Second bug one"}
        query = "type_name=Bug&tags=other&is_epic=false&has_parent=false"
        assert await _titles(armed, "member", query) == {"Audit bug two"}

    async def test_the_plain_list_takes_the_same_filters(self, armed, g19c):
        query = "type_name=bug&tags=audit"
        for who in ("member", "reader"):
            plain = await _titles(armed, who, query, compact=False)
            compact = await _titles(armed, who, query)
            assert plain == compact, who


@_DB_GATE
class TestTheNumber:
    async def test_one_number_reads_one_task_in_each_root(self, armed, g19c):
        query = f"task_number={SHARED_NUMBER}"
        assert await _titles(armed, "member", query) == {
            "Audit bug one", "Second bug one"}
        assert await _titles(armed, "member_agent", query) == {
            "Audit bug one", "Second bug one"}
        assert await _titles(armed, "reader", query) == {
            "Audit bug one", "Second bug one", "Hidden bug one"}
        assert await _titles(armed, "org_b", query) == {"Org B bug one"}

    async def test_a_project_scope_reads_exactly_one(self, armed, g19c):
        rows = await _rows(armed, "member_agent",
                           f"task_number={SHARED_NUMBER}&project_id={g19c.audit}"
                           "&include_subtree=true")
        assert [r["id"] for r in rows] == [g19c.t["one"]]
        assert rows[0]["task_number"] == SHARED_NUMBER

    async def test_a_hidden_project_is_a_404_for_a_number(self, armed, g19c):
        r = await _get(armed, "member", "/projects/tasks",
                       f"task_number={SHARED_NUMBER}&project_id={g19c.hidden}")
        assert r.status_code == 404, r.text

    async def test_an_unseen_number_is_an_empty_list(self, armed, g19c):
        assert await _rows(armed, "member", "task_number=987654321") == []

    async def test_a_bad_number_is_422(self, armed, g19c):
        for value in ("0", "-1", "abc", "9223372036854775808"):
            r = await _get(armed, "member", "/projects/tasks", f"task_number={value}")
            assert r.status_code == 422, (value, r.text)
        r = await _get(armed, "member", "/projects/tasks", "type_name=" + "x" * 101)
        assert r.status_code == 422, r.text


@_DB_GATE
class TestWhatStaysHidden:
    """Each filter matches a task in the hidden project, the private project
    and organization B. A member never gets one it cannot see."""

    QUERIES = ("type_name=bug", "tags=audit", f"task_number={SHARED_NUMBER}",
               "type_name=bug&tags=audit&status_category=todo")

    @pytest.mark.parametrize("who", sorted(CALLERS))
    async def test_no_filter_shows_a_hidden_task(self, armed, g19c, who):
        for query in self.QUERIES:
            got = await _titles(armed, who, query)
            assert got <= SEEN[who], (who, query, got - SEEN[who])
            if who != "org_b":
                assert not got & NEVER_IN_A, (who, query)
            if who.startswith("member"):
                assert "Hidden bug one" not in got, (who, query)

    async def test_a_hidden_type_id_reads_as_no_match(self, armed, g19c):
        for key in ("hidden_bug", "private_bug"):
            for who in ("member", "member_agent"):
                assert await _rows(armed, who, f"type_id={g19c.ty[key]}") == [], (who, key)
        assert await _rows(armed, "reader", f"type_id={g19c.ty['private_bug']}") == []


@_DB_GATE
class TestTheCompactRow:
    async def test_the_row_names_its_type(self, armed, g19c):
        rows = await _rows(armed, "member_agent", f"project_id={g19c.audit}")
        by_title = {r["title"]: r for r in rows}
        one = by_title["Audit bug one"]
        assert one["type_id"] == g19c.ty["audit_bug"]
        assert one["type_name"] == "Bug"
        assert one["tags"] == ["audit"]
        assert one["created_at"]
        assert by_title["Audit task"]["type_name"] == "Task"
        none = by_title["Audit no type"]
        assert none["type_id"] is None and none["type_name"] is None
        assert none["tags"] == []

    async def test_the_edge_shapes_the_gateway_row(self, armed, g19c):
        rows = await _rows(armed, "member_agent", f"project_id={g19c.audit}")
        shaped = {r["title"]: tools._compact_row(r, {}) for r in rows}
        assert shaped["Audit bug one"]["type"] == {
            "id": g19c.ty["audit_bug"], "name": {"untrusted_text": "Bug"}}
        assert shaped["Audit bug one"]["tags"] == [{"untrusted_text": "audit"}]
        assert shaped["Audit no type"]["type"] is None
        for row in shaped.values():
            assert tuple(row) == tools.COMPACT_TASK_KEYS

    async def test_a_plain_row_keeps_its_shape(self, armed, g19c):
        rows = await _rows(armed, "member", f"project_id={g19c.audit}", compact=False)
        assert all("type_name" not in r for r in rows)
