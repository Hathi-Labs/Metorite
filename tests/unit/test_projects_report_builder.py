"""WS-27bn R1 — the report builder over the existing API.

Spec: ``project-docs/specs/projects_reports.md`` §6.2 and §8 R1.

The builder adds one route, ``POST /projects/reports/preview``, and moves the
section loop into one module function, ``render_body``. Four claims:

* **One render function.** The saved render and the preview call
  ``render_body``. For one config and one caller they answer the same
  sections and the same period. A second render path is a defect (§8a).
* **The preview writes no row.** It is in the chat's ``READ_ONLY_POSTS``, and
  a real-DB count of ``pm_reports`` does not move.
* **The preview refuses what create refuses.** A bad config is 422 before a
  session opens. A node the caller cannot see is create's own 404.
* **A save and an edit hold exactly the chosen values.** PATCH replaces
  ``config``, and the next render follows it.

⚠️ The R8 half SKIPS without ``TENANT_LADDER_DATABASE_URL``, and a skip is not
a pass. ``bash scripts/dev_db.sh`` brings the database up.
"""
from __future__ import annotations

import asyncio
import inspect
import os
import uuid
from contextlib import asynccontextmanager
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from fastapi import HTTPException
from gateway.routes.projects import reports as rep
from sqlalchemy import text

_TENANT_URL = os.environ.get("TENANT_LADDER_DATABASE_URL", "").strip()
_needs_db = pytest.mark.skipif(
    not _TENANT_URL,
    reason=(
        "TENANT_LADDER_DATABASE_URL unset — R8 requires a REAL Postgres. A "
        "skip here is not a pass; CI must set it."
    ),
)

EVERY_SECTION = list(rep.SECTIONS)


# ── Hermetic: the shape of the seam ─────────────────────────────────────────


def test_the_preview_route_is_registered_as_a_post() -> None:
    pairs = {
        (m, getattr(r, "path", ""))
        for r in rep.router.routes
        for m in getattr(r, "methods", set())
    }
    assert ("POST", "/projects/reports/preview") in pairs


def test_both_routes_call_the_one_render_function() -> None:
    """§8a: the route, the preview, and later the chat call ONE function."""
    for fn in (rep.render_report, rep.preview_report):
        assert "render_body(" in inspect.getsource(fn), fn.__name__
    module = inspect.getsource(rep)
    # The section loop lives in one place. A copy is the second render path.
    assert module.count('for name in config["sections"]') == 1
    assert 'for name in config["sections"]' in inspect.getsource(rep.render_body)


def test_the_preview_is_a_read_only_post_for_the_chat() -> None:
    from skill_projects import manifest as m

    assert ("POST", "/projects/reports/preview") in m.READ_ONLY_POSTS
    assert m.is_read("POST", "/projects/reports/preview")
    row = m.route_for("POST", "/projects/reports/preview")
    assert row is not None
    # Class X in R1. R8 maps it to render_report and changes it to class A.
    assert row.cls == "X" and row.reason


@pytest.mark.parametrize(
    "config",
    [
        {"weeks": 0},
        {"weeks": 27},
        {"skip_current_week": "false"},
        {"include_subtree": 1},
        {"sections": ["finished", "payroll"]},
        {"sections": []},
    ],
)
def test_a_bad_preview_config_is_422_before_any_session(config, monkeypatch) -> None:
    opened: list[bool] = []

    def _session(*_a: Any, **_k: Any) -> Any:
        opened.append(True)
        raise AssertionError("a session opened for a refused config")

    monkeypatch.setattr(rep, "_tenant_session", _session)
    with pytest.raises(HTTPException) as err:
        asyncio.run(rep.preview_report({"config": config}, user=object()))
    assert err.value.status_code == 422
    assert opened == []


def test_a_preview_name_over_the_limit_is_422(monkeypatch) -> None:
    monkeypatch.setattr(rep, "_tenant_session", None)
    with pytest.raises(HTTPException) as err:
        asyncio.run(
            rep.preview_report(
                {"name": "x" * (rep.MAX_NAME + 1), "config": {}}, user=object(),
            )
        )
    assert err.value.status_code == 422


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
    """Apply the tenant ladder ONCE for this module, never once per test.

    A replay spends attribute numbers on `email_assistant_settings`, and
    Postgres stops at 1600 (see test_projects_analytics_capacity.py).
    """
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
    """One project with open, overdue and finished work, in the first org."""
    from sqlalchemy import create_engine

    eng = create_engine(_TENANT_URL, future=True)
    tag = uuid.uuid4().hex[:8]
    ana = f"ana-{tag}@example.test"
    made: dict[str, Any] = {"ana": ana, "tag": tag}
    with eng.begin() as c:
        org = str(c.execute(
            text("SELECT id FROM organization ORDER BY created_at LIMIT 1")
        ).scalar_one())
        made["org"] = org
        pid = str(c.execute(
            text(
                "INSERT INTO pm_projects (name, status, source, created_by,"
                " organization_id, timezone, parent_project_id, owns_statuses)"
                " VALUES (:n,'active','manual','rb@example.test',"
                " CAST(:o AS uuid),'UTC',NULL,true) RETURNING id"
            ),
            {"n": f"rb-{tag}", "o": org},
        ).scalar_one())
        made["project"] = pid
        status: dict[str, str] = {}
        for name, cat, pos in (("To do", "todo", 0), ("Doing", "in_progress", 1),
                               ("Done", "done", 2)):
            status[cat] = str(c.execute(
                text(
                    "INSERT INTO pm_task_statuses (project_id,name,color,"
                    " position,category) VALUES (CAST(:p AS uuid),:n,'gray',"
                    " :pos,:cat) RETURNING id"
                ),
                {"p": pid, "n": name, "pos": pos, "cat": cat},
            ).scalar_one())

        def task(title: str, cat: str, who: str | None, due_days: int | None) -> None:
            tid = str(c.execute(
                text(
                    "INSERT INTO pm_tasks (title, project_id, root_project_id,"
                    " status_id, created_by, organization_id, task_number, due_at)"
                    " SELECT :t, CAST(:p AS uuid), CAST(:p AS uuid),"
                    " CAST(:s AS uuid), 'rb@example.test', CAST(:o AS uuid),"
                    " COALESCE(MAX(task_number),0)+1,"
                    " CASE WHEN CAST(:d AS int) IS NULL THEN NULL"
                    "      ELSE now() + make_interval(days => CAST(:d AS int)) END"
                    " FROM pm_tasks WHERE root_project_id = CAST(:p AS uuid)"
                    " RETURNING id"
                ),
                {"t": title, "p": pid, "s": status[cat], "o": org, "d": due_days},
            ).scalar_one())
            if who:
                c.execute(
                    text(
                        "INSERT INTO pm_task_assignees (task_id, assignee,"
                        " assigned_by) VALUES (CAST(:t AS uuid), :a,"
                        " 'rb@example.test')"
                    ),
                    {"t": tid, "a": who},
                )

        task("late", "todo", ana, -3)
        task("doing", "in_progress", ana, 5)
        task("nobody's", "todo", None, None)
        task("finished", "done", ana, None)
    yield made
    with eng.begin() as c:
        c.execute(text("DELETE FROM pm_reports WHERE project_id = CAST(:p AS uuid)"),
                  {"p": made["project"]})
        c.execute(text("DELETE FROM pm_tasks WHERE project_id = CAST(:p AS uuid)"),
                  {"p": made["project"]})
        c.execute(text("DELETE FROM pm_task_statuses WHERE project_id = CAST(:p AS uuid)"),
                  {"p": made["project"]})
        c.execute(text("DELETE FROM pm_projects WHERE id = CAST(:p AS uuid)"),
                  {"p": made["project"]})
    eng.dispose()


def _vis(seeded: dict[str, Any], *, restricted: bool = False) -> Any:
    from gateway.routes.projects.core import Visibility

    if restricted:
        # No grant names this address, so the seeded project is invisible.
        return Visibility(unrestricted=False, email=f"outsider-{seeded['tag']}@example.test",
                          groups=(), organization_id=seeded["org"])
    return Visibility(unrestricted=True, email="", groups=(), organization_id=seeded["org"])


def _user(hr: bool = True) -> Any:
    from acb_auth import UserContext, UserRole, build_access

    grants = ["feature:projects"] + (["admin:members:read"] if hr else [])
    return UserContext(email="rb@example.test", role=UserRole.EMPLOYEE,
                       access=build_access(grants))


@pytest.fixture
def wired(seeded, monkeypatch):
    """Bind the report routes to one asyncpg engine and one visibility.

    `eng.begin()` commits on exit, as the real session does, so a write in
    one call is visible to the next.
    """
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    state: dict[str, Any] = {"restricted": False}
    eng = create_async_engine(_async_url(), future=True, poolclass=NullPool)

    @asynccontextmanager
    async def _session(*_a: Any, **_k: Any):
        async with eng.begin() as conn:
            yield conn

    async def _resolve(_db: Any, _user: Any) -> Any:
        return _vis(seeded, restricted=state["restricted"])

    monkeypatch.setattr(rep, "_tenant_session", _session)
    monkeypatch.setattr(rep, "resolve_visibility", _resolve)
    yield state
    asyncio.run(eng.dispose())


def _count_reports() -> int:
    from sqlalchemy import create_engine

    eng = create_engine(_TENANT_URL, future=True)
    try:
        with eng.connect() as c:
            return int(c.execute(text("SELECT count(*) FROM pm_reports")).scalar_one())
    finally:
        eng.dispose()


def _saved_config(report_id: str) -> dict[str, Any]:
    from sqlalchemy import create_engine

    eng = create_engine(_TENANT_URL, future=True)
    try:
        with eng.connect() as c:
            return c.execute(
                text("SELECT config FROM pm_reports WHERE id = CAST(:i AS uuid)"),
                {"i": report_id},
            ).scalar_one()
    finally:
        eng.dispose()


@_needs_db
@pytest.mark.parametrize(
    "config",
    [
        {},
        {"sections": EVERY_SECTION, "weeks": 4, "skip_current_week": True,
         "include_subtree": False},
        {"sections": ["load", "stuck"], "weeks": 1, "skip_current_week": False},
    ],
)
def test_the_render_and_the_preview_agree(seeded, wired, config) -> None:
    """N1. One config, one caller, two routes, one answer."""

    async def go() -> tuple[dict[str, Any], dict[str, Any]]:
        user = _user()
        made = await rep.create_report(
            {"name": "Agree", "project_id": seeded["project"], "config": config},
            user=user,
        )
        rendered = await rep.render_report(made["id"], user=user)
        previewed = await rep.preview_report(
            {"name": "Agree", "project_id": seeded["project"], "config": config},
            user=user,
        )
        return rendered, previewed

    rendered, previewed = asyncio.run(go())
    assert previewed["sections"] == rendered["sections"]
    assert previewed["period_start"] == rendered["period_start"]
    assert previewed["period_end"] == rendered["period_end"]
    # The seed is not empty, so the equality compares real figures.
    if "load" in rendered["sections"]:
        assert rendered["sections"]["load"]["total_tasks"] == 3
    # The stub carries what RenderedBody reads.
    assert previewed["report"]["name"] == "Agree"
    assert previewed["report"]["scope"] == "node"
    assert previewed["report"]["id"] is None
    assert previewed["report"]["config"] == rendered["report"]["config"]


@_needs_db
def test_a_portfolio_preview_says_portfolio(seeded, wired) -> None:
    body = asyncio.run(rep.preview_report({"config": {}}, user=_user()))
    assert body["report"]["scope"] == "portfolio"
    assert body["report"]["project_id"] is None
    assert body["report"]["name"] == rep.PREVIEW_NAME
    assert set(body["sections"]) == set(rep.DEFAULT_SECTIONS)


@_needs_db
def test_the_preview_writes_no_row(seeded, wired) -> None:
    """N2. The count of pm_reports does not move."""
    before = _count_reports()
    asyncio.run(rep.preview_report(
        {"name": "Nothing saved", "project_id": seeded["project"],
         "config": {"sections": EVERY_SECTION}},
        user=_user(),
    ))
    asyncio.run(rep.preview_report({"config": {}}, user=_user()))
    assert _count_reports() == before


@_needs_db
def test_a_hidden_node_gets_the_same_refusal_as_create(seeded, wired) -> None:
    """N3. The preview must not be a way to read a node create refuses."""
    wired["restricted"] = True
    payload = {"name": "Hidden", "project_id": seeded["project"], "config": {}}
    with pytest.raises(HTTPException) as made:
        asyncio.run(rep.create_report(dict(payload), user=_user()))
    with pytest.raises(HTTPException) as seen:
        asyncio.run(rep.preview_report(dict(payload), user=_user()))
    assert seen.value.status_code == made.value.status_code == 404
    assert seen.value.detail == made.value.detail


@_needs_db
def test_a_save_holds_exactly_the_chosen_values(seeded, wired) -> None:
    """R1 done-when 1. The builder's chips land in `config` as chosen."""
    chosen = {"weeks": 4, "skip_current_week": True, "include_subtree": False,
              "sections": ["finished", "load", "conflicts"]}
    made = asyncio.run(rep.create_report(
        {"name": "Four weeks", "project_id": seeded["project"], "config": chosen},
        user=_user(),
    ))
    assert made["config"] == chosen
    assert _saved_config(made["id"]) == chosen


@_needs_db
def test_an_edit_replaces_the_config_and_the_render_follows(seeded, wired) -> None:
    """R1 done-when 2. PATCH replaces `config`, and the next render changes."""
    user = _user()

    async def go() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        made = await rep.create_report(
            {"name": "Before", "project_id": seeded["project"],
             "config": {"sections": ["finished"], "weeks": 1}},
            user=user,
        )
        first = await rep.render_report(made["id"], user=user)
        edited = await rep.update_report(
            made["id"],
            {"name": "After", "config": {"sections": ["load", "stuck"], "weeks": 4,
                                         "skip_current_week": True,
                                         "include_subtree": True}},
            user=user,
        )
        second = await rep.render_report(made["id"], user=user)
        return first, edited, second

    first, edited, second = asyncio.run(go())
    assert set(first["sections"]) == {"finished"}
    assert edited["name"] == "After"
    assert edited["config"]["sections"] == ["load", "stuck"]
    assert set(second["sections"]) == {"load", "stuck"}
    assert second["report"]["name"] == "After"
    # Four weeks start earlier than one week, and the server says so.
    assert second["period_start"] < first["period_start"]


@_needs_db
def test_an_unknown_section_is_422_on_save(seeded, wired) -> None:
    """R1 done-when 4."""
    with pytest.raises(HTTPException) as err:
        asyncio.run(rep.create_report(
            {"name": "Bad", "config": {"sections": ["payroll"]}}, user=_user(),
        ))
    assert err.value.status_code == 422
