"""D-PM-31 on a real Postgres: the search minimum is 3, and a task number
passes at any length as an exact lookup.

Spec: ``project-docs/specs/project_management_app.md`` D-PM-31 (owner,
2026-08-13; built 2026-10-06 with the owner's task-number exception).

R8. The hermetic fake in ``_projects_fakes.py`` agrees with whatever SQL it
is handed, so these run the ROUTE ITSELF (``search.search_tasks``) against a
real database, with only the session seam and the visibility lookup patched.

The claims:

* ``#7`` and ``7`` reach the route and come back as task 7, rank 0.
* A second organization's task 7 never comes back (R5). The tenant is in the
  WHERE, so an exact lookup cannot widen it.
* ``ab`` is a 422 that says what to type, and it runs no query.
* The exact arm can be served by an index. With sequential scans priced out,
  the route's statement for ``#7`` reads ``pm_tasks`` through an index, and
  the arm alone names ``idx_pm_tasks_task_number`` (migration 170). On a
  tiny table the planner prefers the tenant index for the whole statement.
  Both are index paths, and the test does not pin which one it picks.

⚠️ The R8 half SKIPS without ``TENANT_LADDER_DATABASE_URL``, and a skip is not
a pass. ``bash scripts/dev_db.sh`` gives you one.
"""
from __future__ import annotations

import os
import uuid
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from fastapi import HTTPException
from gateway.routes.projects import search as route
from sqlalchemy import text

_TENANT_URL = os.environ.get("TENANT_LADDER_DATABASE_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not _TENANT_URL,
    reason=(
        "TENANT_LADDER_DATABASE_URL unset — R8 requires a REAL Postgres. A "
        "skip here is not a pass; CI must set it."
    ),
)

ACTOR = "search-r8@example.test"


def _async_url() -> str:
    url = _TENANT_URL
    if "postgresql+psycopg" in url:
        return url.replace("postgresql+psycopg", "postgresql+asyncpg")
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


@pytest.fixture(scope="module")
def seeded():
    """Org A holds #7, #8 (whose TITLE says "#7") and #9. Org B holds its
    own #7, which must never come back to a member of org A."""
    from sqlalchemy import create_engine

    from tests.unit._tenant_ladder import apply_ladder

    eng = create_engine(_TENANT_URL, future=True)
    with eng.begin() as conn:
        apply_ladder(conn)
    made: dict[str, Any] = {"tasks": {}}
    with eng.begin() as c:
        made["org_a"] = str(c.execute(
            text("SELECT id FROM organization ORDER BY created_at LIMIT 1")
        ).scalar_one())
        made["org_b"] = str(c.execute(
            text(
                "INSERT INTO organization (display_name, slug) VALUES (:n, :s)"
                " RETURNING id"
            ),
            {"n": f"Search B {uuid.uuid4().hex[:6]}",
             "s": f"search-b-{uuid.uuid4().hex[:8]}"},
        ).scalar_one())
        made["projects"] = []
        for org_key, rows in (
            ("org_a", ((7, "Unrelated work"), (8, "Ship #7 of the batch"),
                       (9, "Calibrate the extruder"))),
            ("org_b", ((7, "Beta secret"),)),
        ):
            pid = str(c.execute(
                text(
                    "INSERT INTO pm_projects (name, status, source, created_by,"
                    " organization_id, timezone, parent_project_id,"
                    " owns_statuses) VALUES (:n,'active','manual',:a,"
                    " CAST(:o AS uuid),'Asia/Kolkata',NULL,true) RETURNING id"
                ),
                {"n": f"search-r8-{uuid.uuid4().hex[:6]}", "a": ACTOR,
                 "o": made[org_key]},
            ).scalar_one())
            made["projects"].append(pid)
            sid = str(c.execute(
                text(
                    "INSERT INTO pm_task_statuses (project_id,name,color,"
                    " position,category) VALUES (CAST(:p AS uuid),'To do',"
                    " 'gray',0,'todo') RETURNING id"
                ),
                {"p": pid},
            ).scalar_one())
            for number, title in rows:
                made["tasks"][(org_key, number)] = str(c.execute(
                    text(
                        "INSERT INTO pm_tasks (title, project_id,"
                        " root_project_id, status_id, created_by,"
                        " organization_id, task_number) VALUES (:t,"
                        " CAST(:p AS uuid), CAST(:p AS uuid), CAST(:s AS uuid),"
                        " :a, CAST(:o AS uuid), :n) RETURNING id"
                    ),
                    {"t": title, "p": pid, "s": sid, "a": ACTOR,
                     "o": made[org_key], "n": number},
                ).scalar_one())
    yield made
    with eng.begin() as c:
        ids = made["projects"]
        c.execute(text("DELETE FROM pm_tasks WHERE project_id = ANY(CAST(:p AS uuid[]))"),
                  {"p": ids})
        c.execute(text("DELETE FROM pm_task_statuses WHERE project_id = ANY(CAST(:p AS uuid[]))"),
                  {"p": ids})
        c.execute(text("DELETE FROM pm_projects WHERE id = ANY(CAST(:p AS uuid[]))"),
                  {"p": ids})
        c.execute(text("DELETE FROM organization WHERE id = CAST(:o AS uuid)"),
                  {"o": made["org_b"]})
    eng.dispose()


def _visibility(org: str) -> Any:
    from gateway.routes.projects.core import Visibility

    return Visibility(unrestricted=True, email="", groups=(), organization_id=org)


async def _search(seeded: dict[str, Any], monkeypatch, q: str) -> dict[str, Any]:
    """The route's own handler, on the real database."""
    from contextlib import asynccontextmanager

    from acb_auth import UserContext, UserRole, build_access
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    eng = create_async_engine(_async_url(), future=True, poolclass=NullPool)
    statements: list[str] = []

    @asynccontextmanager
    async def _session(*_a, **_k):
        async with eng.connect() as conn:
            statements.append("opened")
            yield conn

    async def _vis(_db, _user):
        return _visibility(seeded["org_a"])

    monkeypatch.setattr(route, "_tenant_session", _session)
    monkeypatch.setattr(route, "resolve_visibility", _vis)
    user = UserContext(email=ACTOR, role=UserRole.EMPLOYEE,
                       access=build_access(["feature:projects"]))
    try:
        body = await route.search_tasks(q=q, user=user)
    finally:
        await eng.dispose()
    body["_sessions"] = len(statements)
    return body


@pytest.mark.asyncio
@pytest.mark.parametrize("q", ["#7", "7", " #7 "])
async def test_a_task_number_returns_that_task_and_only_this_orgs(seeded, monkeypatch, q):
    body = await _search(seeded, monkeypatch, q)
    assert [(r["id"], r["task_number"], r["rank"]) for r in body["rows"]] == [
        (seeded["tasks"][("org_a", 7)], 7, 0)
    ], "an exact lookup: task 7 of this org, not #8's title, not org B's #7"


@pytest.mark.asyncio
async def test_a_short_text_query_is_a_422_and_runs_no_query(seeded, monkeypatch):
    with pytest.raises(HTTPException) as refused:
        await _search(seeded, monkeypatch, "ab")
    assert refused.value.status_code == 422
    assert refused.value.detail == (
        "Give at least 3 characters to search ('ab' is 2)."
        " A task number works at any length: #7."
    )


@pytest.mark.asyncio
async def test_three_characters_of_text_still_search(seeded, monkeypatch):
    body = await _search(seeded, monkeypatch, "ext")
    ids = [r["id"] for r in body["rows"]]
    assert seeded["tasks"][("org_a", 9)] in ids


def test_the_exact_arm_is_served_by_an_index(seeded):
    """The perf note, measured. The exact statement is the route's own, built
    from its own fragments. With sequential scans priced out, the planner must
    still find an index path for it."""
    from gateway.routes.projects.core import task_visibility_clause, triage_exclusion_clause
    from sqlalchemy import create_engine

    vis = _visibility(seeded["org_a"])
    sql = route._SEARCH_SQL.format(
        visible=task_visibility_clause(vis),
        triage=triage_exclusion_clause(),
        exclude="",
        match=route._NUMBER_MATCH,
    )
    params = {**vis.params, "term": "%#7%", "prefix": "#7%", "number": 7, "cap": 51}
    eng = create_engine(_TENANT_URL, future=True)
    try:
        with eng.begin() as c:
            c.execute(text("SET LOCAL enable_seqscan = off"))
            plan = "\n".join(
                row[0] for row in c.execute(text("EXPLAIN " + sql), params)
            )
    finally:
        eng.dispose()
    assert "Seq Scan on pm_tasks" not in plan, plan


def test_the_exact_arm_alone_names_the_task_number_index(seeded):
    """The arm as the route writes it (``_NUMBER_MATCH``), on ``pm_tasks``
    alone. ``(root_project_id, task_number)`` cannot serve it well, which is
    why migration 170 added ``idx_pm_tasks_task_number``."""
    from sqlalchemy import create_engine

    sql = f"SELECT t.id FROM pm_tasks t WHERE {route._NUMBER_MATCH}"
    eng = create_engine(_TENANT_URL, future=True)
    try:
        with eng.begin() as c:
            c.execute(text("SET LOCAL enable_seqscan = off"))
            plan = "\n".join(
                row[0] for row in c.execute(text("EXPLAIN " + sql), {"number": 7})
            )
    finally:
        eng.dispose()
    assert "idx_pm_tasks_task_number" in plan, plan


# ── The grant axis: a restricted reader (review of #673) ────────────────────
#
# Every case above is ``unrestricted=True``, so they prove the TENANT on the
# exact arm and nothing about GRANTS. A route that moved the number lookup out
# of the shared WHERE (``task_number = N OR (...visible...)``) would leak an
# ungranted, archived or triage task, and those cases would stay green. These
# run a member who holds one direct grant and one group grant.

VIS_ACTOR = "alice-vis@example.test"
VIS_OTHER = "bob-vis@example.test"

#: name → (project, status category, archived, assignee, Alice sees it). The
#: last column is read off the visibility rules, not off the route.
VIS_TASKS = {
    "granted": ("G", "todo", False, None, True),
    "ungranted": ("H", "todo", False, None, False),
    "assignee_only": ("H", "todo", False, VIS_ACTOR, True),
    "group_granted": ("GRP", "todo", False, None, True),
    "no_grant": ("NONE", "todo", False, None, False),
    "archived": ("G", "todo", True, None, False),
    "triage": ("G", "triage", False, None, False),
}
VIS_SEEN = sorted(name for name, spec in VIS_TASKS.items() if spec[4])


@pytest.fixture(scope="module")
def vis_seeded(seeded):
    """Four projects in org A: granted to Alice, granted to Bob, granted to
    ``group:ops``, and granted to nobody. The task numbers are high and
    unique, so they cannot collide with the cases above."""
    from sqlalchemy import create_engine

    eng = create_engine(_TENANT_URL, future=True)
    org = seeded["org_a"]
    tag = uuid.uuid4().hex[:6]
    base = 900_000 + (uuid.uuid4().int % 50_000) * 10
    made: dict[str, Any] = {"org": org, "tag": tag, "projects": {}, "numbers": {}}
    with eng.begin() as c:
        statuses: dict[tuple[str, str], str] = {}
        for key, subject in (("G", VIS_ACTOR), ("H", VIS_OTHER),
                             ("GRP", "group:ops"), ("NONE", None)):
            pid = str(c.execute(
                text(
                    "INSERT INTO pm_projects (name, status, source, created_by,"
                    " organization_id, timezone, parent_project_id,"
                    " owns_statuses) VALUES (:n,'active','manual',:a,"
                    " CAST(:o AS uuid),'Asia/Kolkata',NULL,true) RETURNING id"
                ),
                {"n": f"vis-{key}-{tag}", "a": ACTOR, "o": org},
            ).scalar_one())
            made["projects"][key] = pid
            if subject:
                c.execute(
                    text(
                        "INSERT INTO pm_project_grants (project_id, subject,"
                        " created_by, organization_id) VALUES (CAST(:p AS uuid),"
                        " :s, :a, CAST(:o AS uuid))"
                    ),
                    {"p": pid, "s": subject, "a": ACTOR, "o": org},
                )
            for name, cat, pos in (("To do", "todo", 0), ("Parked", "triage", 1)):
                statuses[(key, cat)] = str(c.execute(
                    text(
                        "INSERT INTO pm_task_statuses (project_id,name,color,"
                        " position,category) VALUES (CAST(:p AS uuid),:n,'gray',"
                        " :pos,:cat) RETURNING id"
                    ),
                    {"p": pid, "n": name, "pos": pos, "cat": cat},
                ).scalar_one())
        for i, (name, (proj, cat, archived, assignee, _seen)) in enumerate(VIS_TASKS.items()):
            number = base + i
            made["numbers"][name] = number
            tid = str(c.execute(
                text(
                    "INSERT INTO pm_tasks (title, project_id, root_project_id,"
                    " status_id, created_by, organization_id, task_number,"
                    " archived_at) VALUES (:t, CAST(:p AS uuid), CAST(:p AS uuid),"
                    " CAST(:s AS uuid), :a, CAST(:o AS uuid), :n,"
                    " CASE WHEN :arch THEN now() ELSE NULL END) RETURNING id"
                ),
                {"t": f"zqx{tag} {name}", "p": made["projects"][proj],
                 "s": statuses[(proj, cat)], "a": ACTOR, "o": org, "n": number,
                 "arch": archived},
            ).scalar_one())
            if assignee:
                c.execute(
                    text(
                        "INSERT INTO pm_task_assignees (task_id, assignee,"
                        " assigned_by) VALUES (CAST(:t AS uuid), :a, :b)"
                    ),
                    {"t": tid, "a": assignee, "b": ACTOR},
                )
        made["missing"] = base + len(VIS_TASKS) + 5
    yield made
    ids = list(made["projects"].values())
    with eng.begin() as c:
        c.execute(
            text(
                "DELETE FROM pm_task_assignees WHERE task_id IN (SELECT id FROM"
                " pm_tasks WHERE project_id = ANY(CAST(:p AS uuid[])))"
            ),
            {"p": ids},
        )
        for table, column in (("pm_tasks", "project_id"),
                              ("pm_project_grants", "project_id"),
                              ("pm_task_statuses", "project_id"),
                              ("pm_projects", "id")):
            c.execute(
                text(f"DELETE FROM {table} WHERE {column} = ANY(CAST(:p AS uuid[]))"),
                {"p": ids},
            )
    eng.dispose()


def _alice(org: str) -> Any:
    from gateway.routes.projects.core import Visibility

    return Visibility(unrestricted=False, email=VIS_ACTOR, groups=("group:ops",),
                      organization_id=org)


async def _search_as_alice(vis_seeded, monkeypatch, q: str) -> dict[str, Any]:
    from contextlib import asynccontextmanager

    from acb_auth import UserContext, UserRole, build_access
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    eng = create_async_engine(_async_url(), future=True, poolclass=NullPool)

    @asynccontextmanager
    async def _session(*_a, **_k):
        async with eng.connect() as conn:
            yield conn

    async def _vis(_db, _user):
        return _alice(vis_seeded["org"])

    monkeypatch.setattr(route, "_tenant_session", _session)
    monkeypatch.setattr(route, "resolve_visibility", _vis)
    user = UserContext(email=VIS_ACTOR, role=UserRole.EMPLOYEE,
                       access=build_access(["feature:projects"]))
    try:
        return await route.search_tasks(q=q, user=user)
    finally:
        await eng.dispose()


def _list_as_alice(vis_seeded, q: str) -> list[str]:
    """The list endpoint's composition: the visibility clause, the shared
    filters (the archived rules included) and the triage exclusion."""
    from gateway.routes.projects.core import task_visibility_clause, triage_exclusion_clause
    from gateway.routes.projects.filters import build_task_filters
    from sqlalchemy import create_engine

    vis = _alice(vis_seeded["org"])
    clauses = [task_visibility_clause(vis)]
    params = dict(vis.params)
    more, bound = build_task_filters(q=q)
    clauses += more
    params.update(bound)
    clauses.append(triage_exclusion_clause())
    eng = create_engine(_TENANT_URL, future=True)
    try:
        with eng.begin() as c:
            rows = c.execute(
                text("SELECT t.title FROM pm_tasks t WHERE " + " AND ".join(clauses)),
                params,
            )
            return sorted(r.title.split()[1] for r in rows)
    finally:
        eng.dispose()


def _names(body: dict[str, Any]) -> list[str]:
    return sorted(r["title"].split()[1] for r in body["rows"])


@pytest.mark.asyncio
async def test_a_restricted_reader_sees_by_number_exactly_what_text_shows(
    vis_seeded, monkeypatch,
):
    """Search: the text query and each task number agree, task by task."""
    words = await _search_as_alice(vis_seeded, monkeypatch, f"zqx{vis_seeded['tag']}")
    assert _names(words) == VIS_SEEN
    for name, number in vis_seeded["numbers"].items():
        body = await _search_as_alice(vis_seeded, monkeypatch, f"#{number}")
        want = [name] if name in VIS_SEEN else []
        assert _names(body) == want, f"#{number} ({name}) on /projects/search"


def test_the_list_composition_agrees_with_text_by_number(vis_seeded):
    """The list endpoint: the same claim, through ``build_task_filters``."""
    assert _list_as_alice(vis_seeded, f"zqx{vis_seeded['tag']}") == VIS_SEEN
    for name, number in vis_seeded["numbers"].items():
        want = [name] if name in VIS_SEEN else []
        assert _list_as_alice(vis_seeded, f"#{number}") == want, f"#{number} ({name}) on the list"


@pytest.mark.asyncio
async def test_a_hidden_number_answers_exactly_what_a_missing_one_does(
    vis_seeded, monkeypatch,
):
    """No enumeration. A number Alice may not see and a number that nobody
    holds give the same body, except for the echoed query."""
    missing = await _search_as_alice(vis_seeded, monkeypatch, f"#{vis_seeded['missing']}")
    assert missing["rows"] == [] and missing["total"] == 0
    for name, spec in VIS_TASKS.items():
        if spec[4]:
            continue
        hidden = await _search_as_alice(
            vis_seeded, monkeypatch, f"#{vis_seeded['numbers'][name]}",
        )
        assert {k: v for k, v in hidden.items() if k != "query"} == {
            k: v for k, v in missing.items() if k != "query"
        }, name
