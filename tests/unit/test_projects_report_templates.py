"""WS-27bn R2 — report templates and the Reports home.

Spec: ``project-docs/specs/projects_reports.md`` §4, §6.1 and §8 R2.

Five claims:

* **The catalogue route.** ``GET /projects/reports/templates`` answers all 13
  templates, and exactly ``weekly_delivery`` is live. The call goes through a
  real FastAPI app, so the test also proves that ``/reports/{report_id}``
  does not capture the word "templates".
* **``config.template`` is an origin label.** A live key saves, PATCH and
  render return it, and an edit of the sections keeps it. An unknown key and
  a coming-soon key are 422. No key saves exactly as R1 saved.
* **``mine`` is the server's.** Each list row says whether the caller wrote
  it. Two authors prove it on a real database.
* **H-182.** The list hides a report on a node that the caller cannot see,
  and shows it to a caller who can. A portfolio report shows to both.
* **The chat manifest.** The route is class X, above the ``{report_id}`` row.

⚠️ The R8 half SKIPS without ``TENANT_LADDER_DATABASE_URL``, and a skip is not
a pass. ``bash scripts/dev_db.sh`` brings the database up.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from contextlib import asynccontextmanager
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
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


def _user(email: str = "rt@example.test") -> Any:
    from acb_auth import UserContext, UserRole, build_access

    return UserContext(email=email, role=UserRole.EMPLOYEE,
                       access=build_access(["feature:projects"]))


# ── Hermetic: the catalogue route ────────────────────────────────────────────


def _client() -> TestClient:
    from acb_auth import get_current_user

    app = FastAPI()
    app.include_router(rep.router)
    app.dependency_overrides[get_current_user] = lambda: _user()
    return TestClient(app)


def test_the_route_answers_all_13_and_only_weekly_delivery_is_live(monkeypatch) -> None:
    def _no_session(*_a: Any, **_k: Any) -> Any:
        # The id route opens a session. The catalogue route opens none.
        raise AssertionError("GET /reports/{report_id} captured 'templates'")

    monkeypatch.setattr(rep, "_tenant_session", _no_session)
    res = _client().get("/projects/reports/templates")
    assert res.status_code == 200, res.text
    templates = res.json()["templates"]
    assert [t["key"] for t in templates] == list(rep.TEMPLATES)
    assert len(templates) == 13
    assert [t["key"] for t in templates if t["available"]] == ["weekly_delivery"]
    for t in templates:
        assert t["name"] and t["question"] and t["scope_kinds"], t["key"]
        if not t["available"]:
            assert "sections" not in t and t["waits_for"], t["key"]


def test_the_catalogue_route_is_matched_before_the_id_route() -> None:
    from starlette.routing import Match

    app = FastAPI()
    app.include_router(rep.router)
    scope = {"type": "http", "method": "GET",
             "path": "/projects/reports/templates", "root_path": ""}
    first = next(r for r in app.router.routes if r.matches(scope)[0] == Match.FULL)
    assert first.endpoint is rep.list_report_templates


def test_the_answer_is_a_copy_of_the_catalogue() -> None:
    """A caller that edits the answer must not edit the map."""
    body = asyncio.run(rep.list_report_templates(user=_user()))
    body["templates"][3]["sections"].append("conflicts")
    assert rep.TEMPLATES["weekly_delivery"]["sections"] == list(rep.DEFAULT_SECTIONS)


def test_T11_waits_for_a_conflict_kind_filter() -> None:
    t11 = rep.TEMPLATES["focus_switching"]
    assert t11["available"] is False
    assert "kind of conflict" in t11["waits_for"]


# ── Hermetic: config.template ────────────────────────────────────────────────


def test_a_live_template_is_kept() -> None:
    got = rep.normalise_report_config({"template": "weekly_delivery"})
    assert got["template"] == "weekly_delivery"


@pytest.mark.parametrize("raw", [{}, {"template": None}, None])
def test_no_template_adds_no_key(raw) -> None:
    """A config with no template saves exactly as R1 saved."""
    assert "template" not in rep.normalise_report_config(raw)
    assert "template" not in rep._DEFAULTS


@pytest.mark.parametrize("bad", ["payroll", "", 4, ["weekly_delivery"], True])
def test_an_unknown_template_is_422_and_names_the_known_keys(bad) -> None:
    with pytest.raises(HTTPException) as err:
        rep.normalise_report_config({"template": bad})
    assert err.value.status_code == 422
    assert "weekly_delivery" in err.value.detail


@pytest.mark.parametrize(
    "key", [k for k, t in rep.TEMPLATES.items() if not t["available"]],
)
def test_a_coming_soon_template_is_422(key: str) -> None:
    with pytest.raises(HTTPException) as err:
        rep.normalise_report_config({"template": key})
    assert err.value.status_code == 422
    assert "coming soon" in err.value.detail


def test_a_template_does_not_fix_the_sections() -> None:
    """The template is an origin label. The member may change the chips."""
    got = rep.normalise_report_config(
        {"template": "weekly_delivery", "sections": ["load"], "weeks": 4},
    )
    assert got == {"template": "weekly_delivery", "sections": ["load"],
                   "weeks": 4, "skip_current_week": True,
                   "include_subtree": True}


# ── Hermetic: the chat manifest ──────────────────────────────────────────────


def test_the_route_is_class_X_in_the_chat_manifest() -> None:
    from skill_projects import manifest as m

    row = m.route_for("GET", "/projects/reports/templates")
    assert row is not None
    assert row.path == "/projects/reports/templates"
    assert row.cls == "X" and row.reason and not row.tool
    # The id row still answers for an id.
    assert m.route_for("GET", "/projects/reports/abc").path == (
        "/projects/reports/{report_id}"
    )


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
    """Apply the tenant ladder ONCE for this module, never once per test."""
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
    """One project, a viewer granted on it, and an outsider who is not."""
    from sqlalchemy import create_engine

    eng = create_engine(_TENANT_URL, future=True)
    tag = uuid.uuid4().hex[:8]
    made: dict[str, Any] = {
        "tag": tag,
        "ana": f"ana-{tag}@example.test",
        "ben": f"ben-{tag}@example.test",
        "viewer": f"viewer-{tag}@example.test",
        "outsider": f"outsider-{tag}@example.test",
    }
    with eng.begin() as c:
        org = str(c.execute(
            text("SELECT id FROM organization ORDER BY created_at LIMIT 1")
        ).scalar_one())
        made["org"] = org
        made["project"] = str(c.execute(
            text(
                "INSERT INTO pm_projects (name, status, source, created_by,"
                " organization_id, timezone, parent_project_id, owns_statuses)"
                " VALUES (:n,'active','manual','rt@example.test',"
                " CAST(:o AS uuid),'UTC',NULL,true) RETURNING id"
            ),
            {"n": f"rt-{tag}", "o": org},
        ).scalar_one())
        c.execute(
            text(
                "INSERT INTO pm_project_grants (project_id, subject, created_by,"
                " organization_id) VALUES (CAST(:p AS uuid), :s,"
                " 'rt@example.test', CAST(:o AS uuid))"
            ),
            {"p": made["project"], "s": made["viewer"], "o": org},
        )
    yield made
    authors = [made["ana"], made["ben"], made["viewer"], "rt@example.test"]
    with eng.begin() as c:
        c.execute(
            text(
                "DELETE FROM pm_reports WHERE project_id = CAST(:p AS uuid)"
                " OR (project_id IS NULL AND created_by = ANY(:a)"
                " AND name LIKE :n)"
            ),
            {"p": made["project"], "a": authors, "n": f"%{tag}%"},
        )
        c.execute(text("DELETE FROM pm_projects WHERE id = CAST(:p AS uuid)"),
                  {"p": made["project"]})
    eng.dispose()


@pytest.fixture
def wired(seeded, monkeypatch):
    """Bind the report routes to one asyncpg engine.

    ``state["as"]`` picks the caller's visibility: ``"org"`` sees the whole
    tenant, ``"viewer"`` holds a grant on the seeded project, and
    ``"outsider"`` holds none.
    """
    from gateway.routes.projects.core import Visibility
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    state: dict[str, Any] = {"as": "org"}
    eng = create_async_engine(_async_url(), future=True, poolclass=NullPool)

    @asynccontextmanager
    async def _session(*_a: Any, **_k: Any):
        async with eng.begin() as conn:
            yield conn

    async def _resolve(_db: Any, _user: Any) -> Any:
        if state["as"] == "org":
            return Visibility(unrestricted=True, email="", groups=(),
                              organization_id=seeded["org"])
        return Visibility(unrestricted=False, email=seeded[state["as"]],
                          groups=(), organization_id=seeded["org"])

    monkeypatch.setattr(rep, "_tenant_session", _session)
    monkeypatch.setattr(rep, "resolve_visibility", _resolve)
    yield state
    asyncio.run(eng.dispose())


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
def test_a_template_saves_and_patch_and_render_return_it(seeded, wired) -> None:
    user = _user()

    async def go() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        made = await rep.create_report(
            {"name": f"Weekly {seeded['tag']}", "project_id": seeded["project"],
             "config": {"template": "weekly_delivery",
                        "sections": list(rep.DEFAULT_SECTIONS),
                        "weeks": 1, "skip_current_week": True}},
            user=user,
        )
        # A builder edit that changes only the sections keeps the template.
        edited = await rep.update_report(
            made["id"],
            {"config": {"template": "weekly_delivery",
                        "sections": ["finished", "load"], "weeks": 1,
                        "skip_current_week": True, "include_subtree": True}},
            user=user,
        )
        rendered = await rep.render_report(made["id"], user=user)
        return made, edited, rendered

    made, edited, rendered = asyncio.run(go())
    assert made["config"]["template"] == "weekly_delivery"
    assert edited["config"]["template"] == "weekly_delivery"
    assert edited["config"]["sections"] == ["finished", "load"]
    assert rendered["report"]["config"]["template"] == "weekly_delivery"
    assert set(rendered["sections"]) == {"finished", "load"}
    row = _saved_config(made["id"])
    assert row["template"] == "weekly_delivery"
    assert row["sections"] == ["finished", "load"]


@_needs_db
def test_a_coming_soon_template_saves_no_row(seeded, wired) -> None:
    with pytest.raises(HTTPException) as err:
        asyncio.run(rep.create_report(
            {"name": f"Soon {seeded['tag']}", "project_id": seeded["project"],
             "config": {"template": "focus_switching"}},
            user=_user(),
        ))
    assert err.value.status_code == 422


@_needs_db
def test_mine_is_true_only_for_the_author(seeded, wired) -> None:
    ana, ben = _user(seeded["ana"]), _user(seeded["ben"])

    async def go() -> dict[str, Any]:
        a = await rep.create_report(
            {"name": f"Ana {seeded['tag']}", "config": {}}, user=ana,
        )
        b = await rep.create_report(
            {"name": f"Ben {seeded['tag']}", "config": {}}, user=ben,
        )
        listed = await rep.list_reports(user=ana)
        return {"a": a["id"], "b": b["id"], "rows": listed["reports"]}

    got = asyncio.run(go())
    mine = {r["id"]: r["mine"] for r in got["rows"]}
    assert mine[got["a"]] is True
    assert mine[got["b"]] is False


@_needs_db
def test_the_list_hides_a_report_on_a_node_the_caller_cannot_see(seeded, wired) -> None:
    """H-182. The list must not name a report that get and render refuse."""
    user = _user()
    node = asyncio.run(rep.create_report(
        {"name": f"Node {seeded['tag']}", "project_id": seeded["project"],
         "config": {}},
        user=user,
    ))
    portfolio = asyncio.run(rep.create_report(
        {"name": f"All {seeded['tag']}", "config": {}}, user=user,
    ))

    def listed(who: str) -> set[str]:
        wired["as"] = who
        body = asyncio.run(rep.list_reports(user=_user(seeded.get(who, "x"))))
        return {r["id"] for r in body["reports"]}

    outsider = listed("outsider")
    assert node["id"] not in outsider
    assert portfolio["id"] in outsider

    viewer = listed("viewer")
    assert node["id"] in viewer
    assert portfolio["id"] in viewer

    # The list and the 404 agree for the outsider.
    wired["as"] = "outsider"
    with pytest.raises(HTTPException) as err:
        asyncio.run(rep.get_report(node["id"], user=_user()))
    assert err.value.status_code == 404
