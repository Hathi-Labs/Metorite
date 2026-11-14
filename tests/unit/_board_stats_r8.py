"""Shared data and helpers for the board_stats R8 suites (CIV-33e, task 3075).

``test_board_stats_r8.py`` was one file of about 550 s. Its two slow tests now
live in ``test_board_stats_filters_r8.py`` and ``test_board_stats_compact_r8.py``.
All three files seed the same audit project (the ``board`` fixture), build the
same callers and use the same filters. This module holds them, unchanged.

This is a helper module and holds no test.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

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
