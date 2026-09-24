"""WS-27bm S7e — the chat's dataset read: on-the-fly analysis.

Spec: ``project-docs/specs/projects_ai_chat.md`` §13.7, §10.7.

Two halves, as in ``test_projects_analytics_conflicts.py``.

* **Hermetic** — the query parser's refusals (unknown key, column, state,
  group, measure, limit, a repeated key), the filter vocabulary against
  ``build_task_filters``, the one-cycle-time source fence, the route's grant
  and its 422 before a session. These never skip.
* **R8, on a real Postgres through asyncpg** — the open count against Load,
  the median and the p90 against Throughput, a hidden project and a hidden
  blocker, the cap, the group values against hand-computed figures, and the
  per-person values ABSENT without the HR grant.

⚠️ The R8 half SKIPS without ``TENANT_LADDER_DATABASE_URL``, and a skip is not
a pass.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import os
import re
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from fastapi import HTTPException
from gateway.routes.projects import analytics
from gateway.routes.projects import analytics_dataset as route
from gateway.routes.projects.filters import build_task_filters
from sqlalchemy import text

REPO = Path(__file__).resolve().parents[2]
MODULE = REPO / "apps/services/gateway/gateway/routes/projects/analytics_dataset.py"

_TENANT_URL = os.environ.get("TENANT_LADDER_DATABASE_URL", "").strip()
_needs_db = pytest.mark.skipif(
    not _TENANT_URL,
    reason=(
        "TENANT_LADDER_DATABASE_URL unset — R8 requires a REAL Postgres. A "
        "skip here is not a pass; CI must set it."
    ),
)


def _q(**kw: Any) -> route.DatasetQuery:
    return route.parse_query([(k, str(v)) for k, v in kw.items()])


def _refused(**kw: Any) -> str:
    with pytest.raises(HTTPException) as err:
        _q(**kw)
    assert err.value.status_code == 422
    return str(err.value.detail)


# ── Hermetic: the parser (rules 4, 6, 7 and the filters) ────────────────────


def test_the_defaults() -> None:
    q = route.parse_query([])
    assert q.state == "open" and q.include_subtree is True
    assert q.limit == route.DEFAULT_LIMIT == 200
    assert q.columns == route.COLUMNS and len(route.COLUMNS) == 17
    assert q.group_by is None and q.measure == "count" and q.project_id is None


def test_the_column_allowlist_is_the_spec_list() -> None:
    assert route.COLUMNS == (
        "number", "full_id", "title", "project", "root_project", "status",
        "status_category", "type", "tags", "assignees", "estimate_mins",
        "start", "due", "completed_at", "created_at", "cycle_hours", "blockers",
    )


def test_columns_pick_a_subset_in_the_tables_order() -> None:
    assert _q(columns="title, number,title").columns == ("number", "title")


@pytest.mark.parametrize("kw", [
    {"columns": "number,salary"},
    {"state": "stuck"},
    {"group_by": "person"},
    {"group_by": "tag", "measure": "mean"},
    {"measure": "count"},
    {"limit": 501},
    {"limit": 0},
    {"limit": "lots"},
    {"project_id": "../../admin"},
    {"include_subtree": "maybe"},
    {"status_id": "not-a-uuid"},
    {"importance_gte": "high"},
    {"created_after": "last tuesday"},
    {"include_archived": "true"},
    {"viewer": "boss@example.test"},
    {"organization_id": "x"},
])
def test_every_bad_value_is_422_and_never_ignored(kw: dict[str, Any]) -> None:
    _refused(**kw)


def test_a_repeated_key_is_422() -> None:
    with pytest.raises(HTTPException) as err:
        route.parse_query([("tags", "a"), ("tags", "b")])
    assert err.value.status_code == 422


def test_the_limit_bounds_are_1_and_500() -> None:
    assert _q(limit=1).limit == 1 and _q(limit=500).limit == 500
    assert "500" in _refused(limit=501)


def test_the_filters_are_build_task_filters_plus_three() -> None:
    """The filter names are the builder's, so a saved view and the chat mean
    one thing by one word. ``include_archived`` and ``archived_only`` are
    the scope's to decide, and ``viewer`` is the caller, never the query."""
    builder = set(inspect.signature(build_task_filters).parameters)
    own = set(route.FILTER_KEYS) - set(route.DATE_FILTERS)
    assert own == builder - {"include_archived", "archived_only", "viewer"}
    assert set(route.DATE_FILTERS) == {"created_after", "completed_after", "completed_before"}


def test_the_filters_parse_to_the_builders_types() -> None:
    q = _q(overdue="true", importance_gte="3", tags="bug,cad",
           status_id=str(uuid.UUID(int=1)).upper(), created_after="2026-09-01")
    assert q.filters["overdue"] is True and q.filters["importance_gte"] == 3
    assert q.filters["tags"] == "bug,cad"
    assert q.filters["status_id"] == str(uuid.UUID(int=1))
    assert q.dates["created_after"].year == 2026


def test_group_by_and_measure_allowlists() -> None:
    assert route.GROUP_BY == (
        "tag", "status", "status_category", "project", "assignee", "type",
        "created_week", "completed_week",
    )
    assert route.MEASURES == ("count", "estimate_sum", "cycle_hours_median", "cycle_hours_p90")
    want = {"estimate_sum", "cycle_hours_median", "cycle_hours_p90"}
    assert set(route.HR_MEASURES) == want


# ── Hermetic: one cycle time, no write (rules 1 and 3) ──────────────────────


def _code() -> str:
    from tests.unit.test_people_dashboard import _strip_prose

    return _strip_prose(MODULE.read_text(encoding="utf-8"))


def test_the_module_takes_the_one_cycle_time() -> None:
    """Rule 3. ``cycle_cte_sql`` is imported, and no date is subtracted from
    another: ``completed_at`` clears when a task opens again, and a second
    arithmetic would print a second cycle time."""
    code = _code()
    assert re.search(r"from gateway\.routes\.projects\.analytics import \([^)]*cycle_cte_sql", code, re.S)
    assert "cycle_cte_sql(history)" in code
    assert not re.search(r"completed_at\s*-", code)
    assert not re.search(r"-\s*[\w.]*created_at", code)
    assert not re.search(r"\bavg\s*\(", code, re.I), "a median became a mean"


def test_the_scope_is_loads_or_throughputs() -> None:
    """Rule 2. No third spelling of the scope."""
    code = _code()
    assert "load_open_where(scope_sql, vis)" in code
    assert "history_where(scope_sql, vis)" in code
    assert "task_visibility_clause(vis, 't')" not in code


def test_the_module_never_writes() -> None:
    code = _code()
    for verb in ("INSERT", "UPDATE", "DELETE"):
        assert not re.search(rf"\b{verb}\b", code), verb


def test_the_median_and_the_p90_are_percentiles() -> None:
    sql = route.groups_sql("TRUE", "TRUE", "tag", "cycle_hours_median")
    assert "percentile_cont(0.5)" in sql
    assert "percentile_cont(0.9)" in route.groups_sql("TRUE", "TRUE", "tag", "cycle_hours_p90")


def test_throughput_reads_the_named_scope() -> None:
    """The predicate the dataset shares is the one Throughput runs."""
    src = inspect.getsource(analytics.throughput)
    assert "history_where(scope_sql, vis)" in src


# ── Hermetic: the route ─────────────────────────────────────────────────────


def _request(qs: str) -> Any:
    from starlette.datastructures import QueryParams

    return SimpleNamespace(query_params=QueryParams(qs))


@pytest.mark.parametrize("qs", ["limit=501", "salary=1", "columns=pay", "group_by=x"])
def test_a_bad_query_is_422_before_a_session(qs: str, monkeypatch) -> None:
    def _session(*_a, **_k):
        raise AssertionError("a session opened for a refused query")

    monkeypatch.setattr(route, "_tenant_session", _session)
    with pytest.raises(HTTPException) as err:
        asyncio.run(route.dataset(request=_request(qs), user=SimpleNamespace()))
    assert err.value.status_code == 422


def test_the_route_passes_the_callers_grant_not_a_constant(monkeypatch) -> None:
    """R7 fence for O3. Hard-coding ``hr_visible=True`` fails this."""
    from acb_auth import UserContext, UserRole, build_access

    seen: list[bool] = []

    @asynccontextmanager
    async def _session(*_a, **_k):
        yield None

    async def _vis(_db, _user):
        return SimpleNamespace(unrestricted=True)

    async def _body(_db, _vis, _query, *, hr_visible, **_k):
        seen.append(hr_visible)
        return {}

    monkeypatch.setattr(route, "_tenant_session", _session)
    monkeypatch.setattr(route, "resolve_visibility", _vis)
    monkeypatch.setattr(route, "dataset_body", _body)
    for grants, want in ((["feature:projects"], False),
                         (["feature:projects", "admin:members:read"], True)):
        user = UserContext(email="d@example.test", role=UserRole.EMPLOYEE,
                           access=build_access(grants))
        asyncio.run(route.dataset(request=_request("group_by=assignee"), user=user))
        assert seen[-1] is want


def test_the_route_is_mounted() -> None:
    from gateway.routes.projects import router

    paths = {getattr(r, "path", "") for r in router.routes}
    assert "/projects/analytics/dataset" in paths


# ── R8: a real Postgres, through asyncpg ─────────────────────────────────────


def _async_url() -> str:
    url = _TENANT_URL
    if "postgresql+psycopg" in url:
        return url.replace("postgresql+psycopg", "postgresql+asyncpg")
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


@pytest.fixture(scope="module")
def _ladder():
    """Apply the tenant ladder ONCE for this module (the S7a lesson)."""
    if not _TENANT_URL:
        pytest.skip("TENANT_LADDER_DATABASE_URL unset")
    from sqlalchemy import create_engine

    from tests.unit._tenant_ladder import apply_ladder

    eng = create_engine(_TENANT_URL, future=True)
    with eng.begin() as conn:
        apply_ladder(conn)
    eng.dispose()


@pytest.fixture
def seeded(_ladder):
    """Five projects, two people, one agent, and the cases below.

    Projects: ``A`` (with a sub-project ``A1``), ``B`` and ``H`` are active
    roots. ``S`` is stopped. The restricted viewer holds a grant on ``A``.

    Open in A's subtree, oldest first: ``O1`` (bug, 60 min, ana, type Bug),
    ``O2`` (bug and cad, 120 min, ana and the agent, Doing), ``O3`` (no tag,
    no estimate, nobody), ``O4`` in A1 (cad, 30 min, bo), ``KA`` (nothing).
    Done in A: ``D1`` 2h, ``D2`` 4h, ``D3`` 10h on the spine, and ``D4`` with
    no start. ``C1`` is cancelled. ``AR`` is archived and ``TR`` is parked in
    triage, so neither is in any state.

    ``O1`` is blocked by ``KA`` (A, open), ``KH`` (H, open, hidden from the
    restricted viewer) and ``KC`` (A, done). ``KA`` also relates to ``O2``.
    ``SO`` is open in the stopped project, and ``B1`` is open in B.
    """
    from sqlalchemy import create_engine

    eng = create_engine(_TENANT_URL, future=True)
    tag = uuid.uuid4().hex[:8]
    made: dict[str, Any] = {"tag": tag, "viewer": f"viewer-{tag}@example.test"}
    with eng.begin() as c:
        org = str(c.execute(
            text("SELECT id FROM organization ORDER BY created_at LIMIT 1")
        ).scalar_one())
        made["org"] = org

        def project(key: str, status: str = "active", parent: str | None = None) -> None:
            made[key] = str(c.execute(
                text(
                    "INSERT INTO pm_projects (name, status, source, created_by,"
                    " organization_id, timezone, parent_project_id, owns_statuses)"
                    " VALUES (:n, :st, 'manual', 'ds@example.test', CAST(:o AS uuid),"
                    " 'UTC', CAST(:par AS uuid), :owns) RETURNING id"
                ),
                {"n": f"ds-{key}-{tag}", "st": status, "o": org,
                 "par": made[parent] if parent else None, "owns": parent is None},
            ).scalar_one())

        def status(root: str, name: str, category: str, pos: int) -> None:
            made[f"{root}:{category}"] = str(c.execute(
                text(
                    "INSERT INTO pm_task_statuses (project_id, name, color, position,"
                    " category) VALUES (CAST(:p AS uuid), :n, 'gray', :pos, :cat)"
                    " RETURNING id"
                ),
                {"p": made[root], "n": name, "pos": pos, "cat": category},
            ).scalar_one())

        for key in ("A", "B", "H"):
            project(key)
        project("S", "stopped")
        project("A1", parent="A")
        for root in ("A", "B", "H", "S"):
            status(root, "To do", "todo", 0)
        status("A", "Doing", "in_progress", 1)
        status("A", "Done", "done", 2)
        status("A", "Dropped", "cancelled", 3)
        status("A", "Parked", "triage", 4)
        made["type_bug"] = str(c.execute(
            text("INSERT INTO pm_task_types (project_id, name) VALUES"
                 " (CAST(:p AS uuid), :n) RETURNING id"),
            {"p": made["A"], "n": f"Bug {tag}"},
        ).scalar_one())
        c.execute(
            text(
                "INSERT INTO pm_project_grants (project_id, subject, created_by,"
                " organization_id) VALUES (CAST(:p AS uuid), :s, 'ds@example.test',"
                " CAST(:o AS uuid))"
            ),
            {"p": made["A"], "s": made["viewer"], "o": org},
        )

        def person(key: str) -> None:
            made[key] = f"{key}-{tag}@example.test"
            made[f"{key}_id"] = str(c.execute(
                text(
                    "INSERT INTO people (id, name, email, status, skills, source,"
                    " source_key, organization_id, updated_by, updated_at)"
                    " VALUES (gen_random_uuid(), :n, :e, 'active', ARRAY[]::text[],"
                    " 'manual', :k, CAST(:o AS uuid), 'test', now()) RETURNING id"
                ),
                {"n": f"{key.title()} {tag}", "e": made[key],
                 "k": f"manual:ds-{key}-{tag}", "o": org},
            ).scalar_one())

        person("ana")
        person("bo")
        made["bot"] = f"agent:ds-bot-{tag}"
        made["bug"], made["cad"] = f"bug-{tag}", f"cad-{tag}"

        def task(key: str, where: str, *, root: str | None = None, lane: str = "todo",
                 age: int = 0, est: int | None = None, tags: tuple[str, ...] = (),
                 who: tuple[str, ...] = (), archived: bool = False,
                 type_id: str | None = None) -> None:
            root = root or where
            made[key] = str(c.execute(
                text(
                    "INSERT INTO pm_tasks (title, project_id, root_project_id,"
                    " status_id, created_by, organization_id, task_number,"
                    " estimate_mins, tags, type_id, created_at, archived_at,"
                    " completed_at)"
                    " SELECT :t, CAST(:p AS uuid), CAST(:r AS uuid), CAST(:s AS uuid),"
                    " 'ds@example.test', CAST(:o AS uuid),"
                    " COALESCE(MAX(task_number),0)+1, :est, CAST(:tags AS text[]),"
                    " CAST(:ty AS uuid), now() - make_interval(mins => :age),"
                    " CASE WHEN :arch THEN now() END,"
                    # A decoy stamp, 100 days after birth, on every done task.
                    # A cycle time taken from `completed_at - created_at`
                    # reads THIS and fails the spine assertions below.
                    " CASE WHEN :done THEN now() + interval '100 days' END"
                    " FROM pm_tasks WHERE root_project_id = CAST(:r AS uuid)"
                    " RETURNING id"
                ),
                {"t": f"{key} {tag}", "p": made[where], "r": made[root],
                 "s": made[f"{root}:{lane}"], "o": org, "est": est,
                 "tags": [made[t] for t in tags], "ty": type_id, "age": age,
                 "arch": archived, "done": lane == "done"},
            ).scalar_one())
            for w in who:
                c.execute(
                    text(
                        "INSERT INTO pm_task_assignees (task_id, assignee, assigned_by)"
                        " VALUES (CAST(:t AS uuid), :a, 'ds@example.test')"
                    ),
                    {"t": made[key], "a": made[w]},
                )

        def move(key: str, to: str, hours_ago: float) -> None:
            c.execute(
                text(
                    "INSERT INTO pm_activities (task_id, organization_id, type,"
                    " created_by, body, meta, created_at)"
                    " VALUES (CAST(:t AS uuid), CAST(:o AS uuid), 'status_change',"
                    " 'ds@example.test', :b, CAST(:m AS jsonb),"
                    " now() - make_interval(secs => :s))"
                ),
                {"t": made[key], "o": org, "b": f"-> {to}",
                 "m": json.dumps({"from_category": "todo", "to_category": to}),
                 "s": hours_ago * 3600},
            )

        def link(source: str, target: str, kind: str = "blocks") -> None:
            c.execute(
                text(
                    "INSERT INTO pm_task_links (source_task_id, target_task_id,"
                    " link_type, created_by) VALUES (CAST(:s AS uuid),"
                    " CAST(:t AS uuid), :k, 'ds@example.test')"
                ),
                {"s": made[source], "t": made[target], "k": kind},
            )

        task("O1", "A", age=50, est=60, tags=("bug",), who=("ana",), type_id=made["type_bug"])
        task("O2", "A", lane="in_progress", age=40, est=120, tags=("bug", "cad"),
             who=("ana", "bot"))
        task("O3", "A", age=30)
        task("O4", "A1", root="A", age=20, est=30, tags=("cad",), who=("bo",))
        task("KA", "A", age=10)
        for key, hours, who in (("D1", 2, "ana"), ("D2", 4, "bo"), ("D3", 10, "ana")):
            task(key, "A", lane="done", age=60 * 24 * 5, tags=("bug",), who=(who,), est=60)
            move(key, "in_progress", 72 + hours)
            move(key, "done", 72)
        task("D4", "A", lane="done", age=60 * 24 * 5, tags=("bug",))
        move("D4", "done", 72)
        task("C1", "A", lane="cancelled", age=60 * 24 * 5)
        move("C1", "cancelled", 72)
        task("AR", "A", age=5, archived=True)
        task("TR", "A", lane="triage", age=5)
        task("KC", "A", lane="done", age=60 * 24 * 5)
        task("KH", "H", age=5)
        task("SO", "S", age=5)
        task("B1", "B", age=5)
        link("KA", "O1")
        link("KH", "O1")
        link("KC", "O1")
        link("KA", "O2", "relates_to")
    yield made
    with eng.begin() as c:
        ids = [made[k] for k in ("A1", "A", "B", "H", "S")]
        c.execute(text("DELETE FROM pm_tasks WHERE project_id = ANY(CAST(:p AS uuid[]))"),
                  {"p": ids})
        c.execute(text("DELETE FROM pm_task_types WHERE project_id = ANY(CAST(:p AS uuid[]))"),
                  {"p": ids})
        c.execute(text("DELETE FROM pm_task_statuses WHERE project_id = ANY(CAST(:p AS uuid[]))"),
                  {"p": ids})
        c.execute(text("DELETE FROM pm_project_grants WHERE project_id = ANY(CAST(:p AS uuid[]))"),
                  {"p": ids})
        for key in ("A1", "A", "B", "H", "S"):
            c.execute(text("DELETE FROM pm_projects WHERE id = CAST(:p AS uuid)"),
                      {"p": made[key]})
        c.execute(text("DELETE FROM people WHERE id = ANY(CAST(:i AS uuid[]))"),
                  {"i": [made[f"{k}_id"] for k in ("ana", "bo")]})
    eng.dispose()


def _vis(seeded: dict[str, Any], *, restricted: bool) -> Any:
    from gateway.routes.projects.core import Visibility

    if restricted:
        return Visibility(unrestricted=False, email=seeded["viewer"], groups=(),
                          organization_id=seeded["org"])
    return Visibility(unrestricted=True, email="", groups=(), organization_id=seeded["org"])


async def _body(seeded: dict[str, Any], *, restricted: bool = False, hr: bool = True,
                project: str | None = "A", **query: Any) -> dict[str, Any]:
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    items = [(k, str(v)) for k, v in query.items()]
    if project:
        items.append(("project_id", seeded[project]))
    eng = create_async_engine(_async_url(), future=True, poolclass=NullPool)
    try:
        async with eng.connect() as db:
            return await route.dataset_body(
                db, _vis(seeded, restricted=restricted), route.parse_query(items),
                hr_visible=hr, viewer="",
            )
    finally:
        await eng.dispose()


async def _via_route(module: Any, fn: str, seeded: dict[str, Any], monkeypatch,
                     **kwargs: Any) -> dict[str, Any]:
    """Run another analytics route on the real database, as the unrestricted
    caller, so its figure is the route's and never a transcription."""
    from acb_auth import UserContext, UserRole, build_access
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    eng = create_async_engine(_async_url(), future=True, poolclass=NullPool)

    @asynccontextmanager
    async def _session(*_a, **_k):
        async with eng.connect() as conn:
            yield conn

    async def _resolve(_db, _user):
        return _vis(seeded, restricted=False)

    monkeypatch.setattr(module, "_tenant_session", _session)
    monkeypatch.setattr(module, "resolve_visibility", _resolve)
    try:
        user = UserContext(email="ds@example.test", role=UserRole.EMPLOYEE,
                           access=build_access(["feature:projects"]))
        return await getattr(module, fn)(user=user, **kwargs)
    finally:
        await eng.dispose()


def _ids(body: dict[str, Any]) -> list[str]:
    return [r["full_id"] for r in body["rows"]]


def _groups(body: dict[str, Any]) -> dict[Any, dict[str, Any]]:
    return {g["key"]: g for g in body["groups"]}


@_needs_db
async def test_the_open_count_equals_loads_total(seeded, monkeypatch) -> None:
    """§10.7 item 2. One scope, two routes, one number. The portfolio as
    well, where the stopped project's ``SO`` drops out of both."""
    s = seeded
    for project in ("A", None):
        load = await _via_route(analytics, "load", s, monkeypatch,
                                project_id=s[project] if project else None,
                                include_subtree=True)
        body = await _body(s, project=project, state="open", limit=500)
        assert body["total"] == load["total_tasks"], project
    body = await _body(s, state="open", columns="full_id")
    assert sorted(_ids(body)) == sorted(s[k] for k in ("O1", "O2", "O3", "O4", "KA"))


@_needs_db
async def test_the_states_hold_the_right_tasks(seeded) -> None:
    """Rule 2. Archived and parked tasks are in no state. ``closed`` holds
    the done and the cancelled."""
    s = seeded
    closed = await _body(s, state="closed", columns="full_id", limit=500)
    assert sorted(_ids(closed)) == sorted(s[k] for k in ("D1", "D2", "D3", "D4", "C1", "KC"))
    every = await _body(s, state="all", columns="full_id", limit=500)
    assert every["total"] == 11
    for key in ("AR", "TR"):
        assert s[key] not in _ids(every), key


@_needs_db
async def test_cycle_hours_come_from_the_spine(seeded) -> None:
    """Rule 3. The row's figure is the spine's first in_progress to done,
    never the decoy ``completed_at``. D4 has no start, and C1 is not done."""
    s = seeded
    body = await _body(s, state="closed", columns="full_id,cycle_hours,completed_at", limit=500)
    got = {r["full_id"]: r for r in body["rows"]}
    assert got[s["D1"]]["cycle_hours"] == 2.0
    assert got[s["D2"]]["cycle_hours"] == 4.0
    assert got[s["D3"]]["cycle_hours"] == 10.0
    assert got[s["D4"]]["cycle_hours"] is None and got[s["D4"]]["completed_at"]
    assert got[s["C1"]]["cycle_hours"] is None and got[s["C1"]]["completed_at"] is None
    assert body["cycle_window"]["weeks"] == 26


@_needs_db
async def test_the_median_and_the_p90_equal_throughputs(seeded, monkeypatch) -> None:
    """§10.7 item 3. The same tasks, the same figures. Hand-computed: the
    median of 2, 4 and 10 is 4, and the p90 is 4 + 0.8 * 6 = 8.8. The mean
    would be 5.33, and neither figure is it."""
    s = seeded
    tp = await _via_route(analytics, "throughput", s, monkeypatch,
                          project_id=s["A"], include_subtree=True, weeks=26)
    median = _groups(await _body(s, state="all", group_by="status_category",
                                 measure="cycle_hours_median"))["done"]
    p90 = _groups(await _body(s, state="all", group_by="status_category",
                              measure="cycle_hours_p90"))["done"]
    assert median["value"] == tp["summary"]["median_hours"] == 4.0
    assert p90["value"] == tp["summary"]["p90_hours"] == 8.8
    assert median["measured"] == tp["summary"]["measured"] == 3
    assert median["n"] == 5  # D1 to D4 and KC; two of them have no cycle time


@_needs_db
async def test_the_group_values_match_the_hand_count(seeded) -> None:
    """§10.7 item 7, against the fixture's own arithmetic, over open work."""
    s = seeded
    tags = _groups(await _body(s, group_by="tag"))
    assert tags[s["bug"]]["value"] == 2 and tags[s["cad"]]["value"] == 2
    assert tags[None]["value"] == 2 and tags[None]["label"] == "no tag"
    est = _groups(await _body(s, group_by="project", measure="estimate_sum"))
    assert est[s["A"]]["value"] == 180 and est[s["A"]]["n"] == 4
    assert est[s["A"]]["measured"] == 2 and est[s["A"]]["label"] == f"ds-A-{s['tag']}"
    assert est[s["A1"]]["value"] == 30
    types = _groups(await _body(s, group_by="type"))
    assert types[s["type_bug"]]["n"] == 1 and types[None]["label"] == "no type"
    lanes = _groups(await _body(s, group_by="status"))
    assert lanes["To do"]["n"] == 4 and lanes["Doing"]["n"] == 1


@_needs_db
async def test_an_agent_is_marked_in_an_assignee_group(seeded) -> None:
    s = seeded
    people = _groups(await _body(s, group_by="assignee", measure="estimate_sum"))
    assert people[s["ana"]]["value"] == 180 and people[s["ana"]]["label"] == f"Ana {s['tag']}"
    assert people[s["bo"]]["value"] == 30
    assert people[s["bot"]]["agent"] is True and people[s["bot"]]["value"] == 120
    assert people[None]["label"] == "unassigned" and people[None]["n"] == 2
    assert "agent" not in people[s["ana"]]


@_needs_db
async def test_without_the_hr_grant_the_per_person_values_are_absent(seeded) -> None:
    """O3, §10.7 item 7. The count per person stays for every member. The
    estimate and the speed per person are ABSENT, not null."""
    s = seeded
    for measure in ("estimate_sum", "cycle_hours_median", "cycle_hours_p90"):
        body = await _body(s, hr=False, state="all", group_by="assignee", measure=measure)
        assert body["hr_visible"] is False and body["measure_hidden"] is True
        for group in body["groups"]:
            assert "value" not in group and "measured" not in group, measure
            assert set(group) >= {"key", "label", "n"}
        assert _groups(body)[s["ana"]]["n"] == 4
    counted = await _body(s, hr=False, group_by="assignee")
    assert "measure_hidden" not in counted
    assert _groups(counted)[s["ana"]]["value"] == 2
    tagged = await _body(s, hr=False, state="all", group_by="tag", measure="cycle_hours_median")
    assert _groups(tagged)[s["bug"]]["value"] == 4.0, "only the per-person values hide"


@_needs_db
async def test_the_cap_is_visible_and_the_order_is_stable(seeded) -> None:
    """§10.7 item 6. Oldest first, then id. ``total`` counts past the cap."""
    s = seeded
    body = await _body(s, state="open", columns="full_id", limit=2)
    assert _ids(body) == [s["O1"], s["O2"]]
    assert body["total"] == 5 and body["truncated"] is True and body["limit"] == 2
    whole = await _body(s, state="open", columns="full_id")
    assert whole["truncated"] is False and whole["limit"] == 200
    assert _ids(whole) == [s[k] for k in ("O1", "O2", "O3", "O4", "KA")]


@_needs_db
async def test_the_rows_carry_the_names_and_only_the_named_columns(seeded) -> None:
    """§10.7 item 4. The tag and status names are in the table."""
    s = seeded
    body = await _body(s, state="open")
    assert body["columns"] == list(route.COLUMNS)
    [o2] = [r for r in body["rows"] if r["full_id"] == s["O2"]]
    assert o2["status"] == "Doing" and o2["status_category"] == "in_progress"
    assert o2["tags"] == [s["bug"], s["cad"]]
    assert o2["assignees"] == sorted([s["ana"], s["bot"]])
    assert o2["project"] == o2["root_project"] == f"ds-A-{s['tag']}"
    [o4] = [r for r in body["rows"] if r["full_id"] == s["O4"]]
    assert o4["project"] == f"ds-A1-{s['tag']}" and o4["root_project"] == f"ds-A-{s['tag']}"
    [o1] = [r for r in body["rows"] if r["full_id"] == s["O1"]]
    assert o1["type"] == f"Bug {s['tag']}" and o1["estimate_mins"] == 60
    narrow = await _body(s, state="open", columns="title,number")
    assert all(set(r) == {"number", "title"} for r in narrow["rows"])


@_needs_db
async def test_a_hidden_blocker_leaves_no_trace(seeded) -> None:
    """§10.7 item 5. The unrestricted caller sees KA and KH block O1. The
    restricted viewer sees KA only: KH's id and title are absent. A closed
    blocker and a ``relates_to`` link never count."""
    s = seeded
    seen = await _body(s, state="open", columns="full_id,blockers")
    [o1] = [r for r in seen["rows"] if r["full_id"] == s["O1"]]
    assert sorted(b["id"] for b in o1["blockers"]) == sorted([s["KA"], s["KH"]])
    body = await _body(s, restricted=True, state="open", columns="full_id,title,blockers")
    [o1] = [r for r in body["rows"] if r["full_id"] == s["O1"]]
    assert [b["id"] for b in o1["blockers"]] == [s["KA"]]
    raw = json.dumps(body)
    assert s["KH"] not in raw and f"KH {s['tag']}" not in raw
    assert s["KC"] not in json.dumps(seen["rows"])
    [o2] = [r for r in seen["rows"] if r["full_id"] == s["O2"]]
    assert o2["blockers"] == []


@_needs_db
async def test_a_hidden_project_is_404_and_absent_from_the_portfolio(seeded) -> None:
    s = seeded
    with pytest.raises(HTTPException) as err:
        await _body(s, restricted=True, project="H", state="all")
    assert err.value.status_code == 404
    body = await _body(s, restricted=True, project=None, state="all",
                       columns="full_id,title", limit=500)
    raw = json.dumps(body)
    for key in ("KH", "B1"):
        assert s[key] not in raw and f"{key} {s['tag']}" not in raw, key
    assert s["O1"] in raw


@_needs_db
async def test_the_filters_narrow_on_a_real_database(seeded) -> None:
    s = seeded
    bug = await _body(s, state="all", tags=s["bug"], columns="full_id", limit=500)
    assert sorted(_ids(bug)) == sorted(s[k] for k in ("O1", "O2", "D1", "D2", "D3", "D4"))
    nobody = await _body(s, state="open", unassigned="true", columns="full_id")
    assert sorted(_ids(nobody)) == sorted([s["O3"], s["KA"]])
    done_lately = await _body(s, state="all", completed_after="2000-01-01", columns="full_id",
                              limit=500)
    assert sorted(_ids(done_lately)) == sorted(s[k] for k in ("D1", "D2", "D3", "D4"))
    none_yet = await _body(s, state="all", completed_before="2000-01-01", columns="full_id")
    assert none_yet["total"] == 0 and none_yet["rows"] == []
    young = await _body(s, state="open", created_after="2999-01-01", columns="full_id")
    assert young["total"] == 0
