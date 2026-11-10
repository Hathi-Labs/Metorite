"""R8 — WS-61 KR-1, ``GET /search/company`` on a REAL Postgres (``knowledge_base.md`` §20.9).

The route adds no SQL. Each source calls its app's own search, and row level
security keeps that search in the tenant the request bound. So this runs the
WHOLE route, through FastAPI, as the non-privileged role of the phase-4
rehearsal (no superuser, no BYPASSRLS), on two organizations:

* Org A and org B each hold a task and a person whose words match. A member
  of org A gets org A's task and person, and never org B's. A member of org
  B gets the reverse.
* The request names org B in the query and in a header. The member of org A
  still gets only org A's rows, because the tenant comes from the session.

⚠️ A source that FAILED also gives no hit, so an empty answer alone would
pass on a broken build. Each case checks ``skipped`` and the log.

⚠️ It SKIPS without ``TENANT_LADDER_DATABASE_URL``, and a skip is not a pass.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth.roles import UserContext, UserRole
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_email_keep_separate import _assert_non_priv
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

# Without the gate the suite ERRORS where no ladder URL is set (deploy.yml).
pytestmark = _DB_GATE

TAG = uuid.uuid4().hex[:8]
WORD = f"falcon{TAG}"
A_MEMBER = f"kr1-a-{TAG}@kr1-r8.test"
B_MEMBER = f"kr1-b-{TAG}@kr1-r8.test"


def _member(email: str, org: str) -> UserContext:
    from acb_auth import build_access

    return UserContext(email=email, role=UserRole.EMPLOYEE, organization_id=org,
                       access=build_access(["feature:projects", "feature:people"]))


def _seed_org(c: Any, org: str, who: str, title: str) -> dict[str, str]:
    """One member, one project with an org grant, one task and one person."""
    project, lane, task = (str(uuid.uuid4()) for _ in range(3))
    c.execute(text(
        "INSERT INTO app_user (email, display_name, role, status, organization_id) "
        "VALUES (:e, :e, 'employee', 'active', CAST(:o AS uuid))"), {"e": who, "o": org})
    c.execute(text(
        "INSERT INTO pm_projects (id, organization_id, name, source, created_by, "
        "owns_statuses) VALUES (CAST(:p AS uuid), CAST(:o AS uuid), :n, 'manual', :me, "
        "true)"), {"p": project, "o": org, "n": f"kr1-{TAG}", "me": who})
    # An org grant, so each member of the org sees the task.
    c.execute(text(
        "INSERT INTO pm_project_grants (project_id, subject, created_by, organization_id) "
        "VALUES (CAST(:p AS uuid), 'org', :me, CAST(:o AS uuid))"),
        {"p": project, "me": who, "o": org})
    c.execute(text(
        "INSERT INTO pm_task_statuses (id, project_id, name, position, category, "
        "is_default) VALUES (CAST(:s AS uuid), CAST(:p AS uuid), 'To do', 1, 'todo', "
        "true)"), {"s": lane, "p": project})
    c.execute(text(
        "INSERT INTO pm_tasks (id, organization_id, project_id, root_project_id, "
        "status_id, title, source, created_by, task_number) VALUES (CAST(:t AS uuid), "
        "CAST(:o AS uuid), CAST(:p AS uuid), CAST(:p AS uuid), CAST(:s AS uuid), :ti, "
        "'manual', :me, 1)"),
        {"t": task, "o": org, "p": project, "s": lane, "ti": title, "me": who})
    person = str(c.execute(text(
        "INSERT INTO people (name, department, source, organization_id) VALUES "
        "(:n, 'Ops', :s, CAST(:o AS uuid)) RETURNING id"),
        {"n": f"{title} person", "s": f"kr1-{TAG}", "o": org}).scalar_one())
    return {"project": project, "task": task, "person": person}


@pytest.fixture(scope="module")
def two_orgs(promoted):  # noqa: F811
    with promoted.admin_engine.begin() as c:
        a = _seed_org(c, promoted.org_a, A_MEMBER, f"Alpha {WORD} plan")
        b = _seed_org(c, promoted.org_b, B_MEMBER, f"Beta {WORD} secret")
    yield {"a": a, "b": b, "ns": promoted}
    with promoted.admin_engine.begin() as c:
        c.execute(text("DELETE FROM people WHERE source = :s"), {"s": f"kr1-{TAG}"})
        for seeded in (a, b):
            c.execute(text("DELETE FROM pm_tasks WHERE project_id = CAST(:p AS uuid)"),
                      {"p": seeded["project"]})
            c.execute(text("DELETE FROM pm_task_statuses WHERE project_id = CAST(:p AS uuid)"),
                      {"p": seeded["project"]})
            c.execute(text("DELETE FROM pm_project_grants WHERE project_id = CAST(:p AS uuid)"),
                      {"p": seeded["project"]})
            c.execute(text("DELETE FROM pm_projects WHERE id = CAST(:p AS uuid)"),
                      {"p": seeded["project"]})
        c.execute(text("DELETE FROM app_user WHERE email IN (:a, :b)"),
                  {"a": A_MEMBER, "b": B_MEMBER})


@pytest.fixture
def roomy(monkeypatch):
    """A cold scratch database is slower than the route's budget, and a source
    cut off by the deadline also gives nothing. Room here, and the log and
    ``skipped`` checks prove that each source finished."""
    from gateway.retrieval import company

    monkeypatch.setattr(company, "SOURCE_TIMEOUT_S", 30.0)
    monkeypatch.setattr(company, "TOTAL_BUDGET_S", 60.0)


async def _ask(p: Any, user: UserContext, params: dict[str, str],
               headers: dict[str, str] | None = None) -> dict:
    """The route through FastAPI, as a request runs it: the tenant comes from the user."""
    import httpx
    from acb_auth import get_current_user
    from acb_common.db import bind_tenant, clear_tenant, release_tenant
    from fastapi import FastAPI
    from gateway.retrieval import company

    async def _caller() -> UserContext:
        bind_tenant(user.organization_id)
        return user

    app = FastAPI()
    app.include_router(company.router)
    app.dependency_overrides[get_current_user] = _caller
    scope = clear_tenant()
    try:
        async with (
            tenant_engine_scope(p.app_url.render_as_string(hide_password=False)),
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                              base_url="http://kr1.test") as client,
        ):
            res = await client.get("/search/company", params=params, headers=headers or {})
    finally:
        release_tenant(scope)
    assert res.status_code == 200, res.text
    return res.json()


def _clean(answer: dict, caplog: Any) -> None:
    assert answer["skipped"] == [], f"a source failed: {answer['skipped']}"
    assert not [r for r in caplog.records if "search.company source" in r.getMessage()]


def _ids(answer: dict, app: str) -> set[str]:
    return {h["id"] for h in answer["hits"] if h["app"] == app}


async def test_each_org_gets_its_own_task_and_person_only(
    two_orgs, app_engine, roomy, caplog,  # noqa: F811
) -> None:
    _assert_non_priv(app_engine)
    p, a, b = two_orgs["ns"], two_orgs["a"], two_orgs["b"]
    with caplog.at_level(logging.INFO, logger="gateway.retrieval.company"):
        answer_a = await _ask(p, _member(A_MEMBER, p.org_a), {"q": WORD})
        answer_b = await _ask(p, _member(B_MEMBER, p.org_b), {"q": WORD})
    _clean(answer_a, caplog)
    _clean(answer_b, caplog)

    assert _ids(answer_a, "tasks") == {a["task"]}
    assert _ids(answer_a, "people") == {a["person"]}
    assert _ids(answer_b, "tasks") == {b["task"]}
    assert _ids(answer_b, "people") == {b["person"]}
    # The second org's rows never reach the first, by id or by words.
    assert "Beta" not in str(answer_a) and b["task"] not in str(answer_a)
    assert "Alpha" not in str(answer_b) and a["task"] not in str(answer_b)
    task = next(h for h in answer_a["hits"] if h["app"] == "tasks")
    assert task["title"] == f"Alpha {WORD} plan"
    assert task["href"] == f"/projects?task={a['task']}"
    assert task["hint"] == f"Task · kr1-{TAG}"


async def test_an_org_named_in_the_request_changes_nothing(
    two_orgs, app_engine, roomy, caplog,  # noqa: F811
) -> None:
    p, a, b = two_orgs["ns"], two_orgs["a"], two_orgs["b"]
    with caplog.at_level(logging.INFO, logger="gateway.retrieval.company"):
        answer = await _ask(
            p, _member(A_MEMBER, p.org_a),
            {"q": WORD, "organization_id": p.org_b, "org": p.org_b,
             "user": B_MEMBER, "apps": "tasks"},
            headers={"X-Organization-Id": p.org_b, "X-User-Email": B_MEMBER},
        )
    _clean(answer, caplog)
    assert _ids(answer, "tasks") == {a["task"]}
    assert b["task"] not in str(answer)


# ── WS-61 CX-1: recency on the real task search (company_context.md §4.1) ──

HERON = f"heron{TAG}"


@pytest.fixture
def two_ages(two_orgs):
    """Two more tasks in org A's project: an OLD one whose title starts with
    the word (the search's rank 1), and a NEW one with the word only in its
    description (rank 4). The search's date tie-break never reaches them."""
    p, a = two_orgs["ns"], two_orgs["a"]
    old, new = str(uuid.uuid4()), str(uuid.uuid4())
    with p.admin_engine.begin() as c:
        lane = c.execute(text(
            "SELECT id FROM pm_task_statuses WHERE project_id = CAST(:p AS uuid)"),
            {"p": a["project"]}).scalar_one()
        for task, title, body, days, number in (
                (old, f"{HERON} roadmap", "", 400, 2),
                (new, "Quarterly notes", f"see the {HERON} draft", 1, 3)):
            c.execute(text(
                "INSERT INTO pm_tasks (id, organization_id, project_id, root_project_id, "
                "status_id, title, description, source, created_by, task_number, "
                "updated_at) VALUES (CAST(:t AS uuid), CAST(:o AS uuid), "
                "CAST(:p AS uuid), CAST(:p AS uuid), CAST(:s AS uuid), :ti, :d, 'manual', "
                ":me, :n, now() - make_interval(days => :days))"),
                {"t": task, "o": p.org_a, "p": a["project"], "s": str(lane), "ti": title,
                 "d": body, "me": A_MEMBER, "n": number, "days": days})
    yield {"old": old, "new": new}
    with p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM pm_tasks WHERE id IN (CAST(:a AS uuid), CAST(:b AS uuid))"),
                  {"a": old, "b": new})


def _task_ids(answer: dict) -> list[str]:
    return [h["id"] for h in answer["hits"] if h["app"] == "tasks"]


async def test_the_decay_swaps_an_older_better_ranked_task(
    two_orgs, two_ages, app_engine, roomy, caplog, monkeypatch,  # noqa: F811
) -> None:
    from acb_common import get_settings

    _assert_non_priv(app_engine)
    p = two_orgs["ns"]
    user = _member(A_MEMBER, p.org_a)
    params = {"q": HERON, "apps": "tasks"}
    with caplog.at_level(logging.INFO, logger="gateway.retrieval.company"):
        off = await _ask(p, user, params)
        monkeypatch.setattr(get_settings(), "context_recency", True, raising=False)
        on = await _ask(p, user, params)
    _clean(off, caplog)
    _clean(on, caplog)
    # Off: the app's own order, rank first. On: the decay moves the new task up.
    assert _task_ids(off) == [two_ages["old"], two_ages["new"]]
    assert "stamp" not in off["hits"][0] and "searched" not in off
    assert _task_ids(on) == [two_ages["new"], two_ages["old"]]
    # The stamp is the real `updated_at`, and the old match still shows.
    stamps = {h["id"]: h["stamp"] for h in on["hits"] if h["app"] == "tasks"}
    assert stamps[two_ages["old"]] and stamps[two_ages["new"]]
    assert stamps[two_ages["old"]] < stamps[two_ages["new"]]
    assert on["searched"] == ["tasks", "people"]


async def test_after_drops_the_older_task(
    two_orgs, two_ages, app_engine, roomy, caplog, monkeypatch,  # noqa: F811
) -> None:
    from datetime import UTC, datetime, timedelta

    from acb_common import get_settings

    p = two_orgs["ns"]
    monkeypatch.setattr(get_settings(), "context_recency", True, raising=False)
    since = (datetime.now(UTC) - timedelta(days=30)).date().isoformat()
    with caplog.at_level(logging.INFO, logger="gateway.retrieval.company"):
        answer = await _ask(p, _member(A_MEMBER, p.org_a),
                            {"q": HERON, "apps": "tasks", "after": since})
    _clean(answer, caplog)
    assert _task_ids(answer) == [two_ages["new"]]
