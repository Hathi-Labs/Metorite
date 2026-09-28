"""H-183 — the report schedule PATCH, against a real Postgres.

Spec: ``project-docs/specs/projects_reports.md`` §3.1, and CLAUDE.md §3 rule 6
(R8).

``PATCH /projects/reports/{id}/schedule`` answered 500 on every call. The
route sent ``"updated_at": text("now()")`` to ``update_row``, and
``update_row`` also appends ``updated_at = now()``. Postgres refuses two
assignments to one column::

    multiple assignments to same column "updated_at"

No test called the route. The suites that name the schedule read the module
SOURCE, or write the table with hand SQL. Neither sends the route's own UPDATE
to a server, so a statement that Postgres refuses stayed green.

This file calls the route through FastAPI, on an asyncpg engine, and reads the
row back with a second connection. It fails if the double assignment returns.

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


# ── Hermetic: the shape of the route ────────────────────────────────────────


def test_the_schedule_route_does_not_assign_updated_at_itself() -> None:
    """`update_row` owns `updated_at`. A second assignment is a 500.

    Advisory only. The real-DB tests below are the fence.
    """
    src = inspect.getsource(rep.set_schedule)
    assert '"updated_at"' not in src


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
def report(_ladder):
    """One portfolio report in the first org, with no recipient yet."""
    from sqlalchemy import create_engine

    eng = create_engine(_TENANT_URL, future=True)
    made: dict[str, Any] = {}
    with eng.begin() as c:
        made["org"] = str(c.execute(
            text("SELECT id FROM organization ORDER BY created_at LIMIT 1")
        ).scalar_one())
        made["id"] = str(c.execute(
            text(
                "INSERT INTO pm_reports (project_id, organization_id, name,"
                " config, created_by) VALUES (NULL, CAST(:o AS uuid), :n,"
                " '{}'::jsonb, 'sched@example.test') RETURNING id"
            ),
            {"o": made["org"], "n": f"sched-{uuid.uuid4().hex[:6]}"},
        ).scalar_one())
    yield made
    with eng.begin() as c:
        c.execute(
            text("DELETE FROM pm_reports WHERE id = CAST(:i AS uuid)"),
            {"i": made["id"]},
        )
    eng.dispose()


def _add_recipient(report: dict[str, Any], email: str) -> None:
    from sqlalchemy import create_engine

    eng = create_engine(_TENANT_URL, future=True)
    try:
        with eng.begin() as c:
            c.execute(
                text(
                    "INSERT INTO pm_report_recipients (report_id, recipient,"
                    " created_by) VALUES (CAST(:r AS uuid), :e,"
                    " 'sched@example.test')"
                ),
                {"r": report["id"], "e": email},
            )
    finally:
        eng.dispose()


def _stored(report_id: str) -> Any:
    """The row as the database holds it, read on a SECOND connection."""
    from sqlalchemy import create_engine

    eng = create_engine(_TENANT_URL, future=True)
    try:
        with eng.connect() as c:
            return c.execute(
                text(
                    "SELECT enabled, schedule, updated_at FROM pm_reports"
                    " WHERE id = CAST(:i AS uuid)"
                ),
                {"i": report_id},
            ).one()
    finally:
        eng.dispose()


@pytest.fixture
def client(report, monkeypatch):
    """The report router in a FastAPI app, on one asyncpg engine.

    The tenant is not in the request. The session is the seam that binds it,
    and the route never reads one from the body.
    """
    from acb_auth import UserContext, UserRole, build_access, get_current_user
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from gateway.routes.projects.core import Visibility
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    eng = create_async_engine(_async_url(), future=True, poolclass=NullPool)

    @asynccontextmanager
    async def _session(*_a: Any, **_k: Any):
        # `begin()` commits on exit, as the real session does.
        async with eng.begin() as conn:
            yield conn

    async def _resolve(_db: Any, _user: Any) -> Any:
        return Visibility(unrestricted=True, email="", groups=(),
                          organization_id=report["org"])

    monkeypatch.setattr(rep, "_tenant_session", _session)
    monkeypatch.setattr(rep, "resolve_visibility", _resolve)

    app = FastAPI()
    app.include_router(rep.router)
    app.dependency_overrides[get_current_user] = lambda: UserContext(
        email="sched@example.test", role=UserRole.EMPLOYEE,
        access=build_access(["feature:projects"]),
    )
    with TestClient(app, raise_server_exceptions=False) as tc:
        yield tc
    asyncio.run(eng.dispose())


def _patch(client: Any, report_id: str, body: dict[str, Any]) -> Any:
    return client.patch(f"/projects/reports/{report_id}/schedule", json=body)


@_needs_db
def test_arming_writes_the_row_and_moves_updated_at(client, report) -> None:
    """The success path. H-183 answered 500 here on every call."""
    _add_recipient(report, "ana@example.test")
    before = _stored(report["id"])
    assert before.enabled is False

    res = _patch(client, report["id"], {"enabled": True})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["enabled"] is True
    assert body["schedule"] == "weekly"
    assert body["delivery_armed_on_this_deployment"] is False
    assert body["mine"] is True

    after = _stored(report["id"])
    assert after.enabled is True
    assert after.schedule == "weekly"
    assert after.updated_at > before.updated_at


@_needs_db
def test_disarming_clears_the_schedule(client, report) -> None:
    _add_recipient(report, "ana@example.test")
    assert _patch(client, report["id"], {"enabled": True}).status_code == 200

    res = _patch(client, report["id"], {"enabled": False})
    assert res.status_code == 200, res.text
    after = _stored(report["id"])
    assert after.enabled is False
    assert after.schedule is None


@_needs_db
def test_arming_with_no_recipient_is_422_and_writes_nothing(client, report) -> None:
    before = _stored(report["id"])
    res = _patch(client, report["id"], {"enabled": True})
    assert res.status_code == 422
    assert "Add at least one recipient" in res.json()["detail"]
    after = _stored(report["id"])
    assert after.enabled is False
    assert after.updated_at == before.updated_at


@_needs_db
@pytest.mark.parametrize(
    "body",
    [{}, {"enabled": "yes"}, {"enabled": True, "schedule": "hourly"}],
)
def test_a_bad_body_is_422_and_writes_nothing(client, report, body) -> None:
    _add_recipient(report, "ana@example.test")
    before = _stored(report["id"])
    res = _patch(client, report["id"], body)
    assert res.status_code == 422, res.text
    assert _stored(report["id"]) == before


@_needs_db
def test_an_unknown_report_is_404(client, report) -> None:
    res = _patch(client, str(uuid.uuid4()), {"enabled": False})
    assert res.status_code == 404


# ── The other routes in the module, for the same class of defect ────────────
#
# H-183 was SQL that only a real server refuses. These calls send every other
# statement in `reports.py` to Postgres at least once. The builder suite covers
# create, patch, render and preview.


@pytest.fixture
def member(_ladder):
    """A directory address, so `_known_member` answers yes."""
    from sqlalchemy import create_engine

    email = f"mem-{uuid.uuid4().hex[:8]}@example.test"
    eng = create_engine(_TENANT_URL, future=True)
    with eng.begin() as c:
        c.execute(
            text("INSERT INTO app_user (email) VALUES (:e)"), {"e": email},
        )
    yield email
    with eng.begin() as c:
        c.execute(text("DELETE FROM app_user WHERE email = :e"), {"e": email})
    eng.dispose()


@_needs_db
def test_the_recipient_routes_round_trip_and_arm_the_schedule(
    client, report, member,
) -> None:
    base = f"/projects/reports/{report['id']}/recipients"
    res = client.post(base, json={"recipient": member.upper()})
    assert res.status_code == 201, res.text
    # Adding twice is adding.
    assert client.post(base, json={"recipient": member}).status_code == 201

    res = client.get(base)
    assert res.status_code == 200, res.text
    got = res.json()["recipients"]
    assert [r["recipient"] for r in got] == [member]
    assert got[0]["added_by"] == "sched@example.test"

    # The recipient the ROUTE wrote is the one the schedule counts.
    res = _patch(client, report["id"], {"enabled": True})
    assert res.status_code == 200, res.text
    assert _stored(report["id"]).enabled is True

    assert client.delete(f"{base}/{member}").status_code == 204
    assert client.get(base).json()["recipients"] == []


@_needs_db
def test_a_stranger_is_not_a_recipient(client, report) -> None:
    res = client.post(
        f"/projects/reports/{report['id']}/recipients",
        json={"recipient": f"nobody-{uuid.uuid4().hex[:6]}@example.test"},
    )
    assert res.status_code == 422


@_needs_db
def test_the_read_routes_and_delete_answer(client, report) -> None:
    assert client.get("/projects/reports/templates").status_code == 200
    res = client.get("/projects/reports")
    assert res.status_code == 200, res.text
    assert report["id"] in {r["id"] for r in res.json()["reports"]}
    res = client.get(f"/projects/reports/{report['id']}")
    assert res.status_code == 200, res.text
    assert res.json()["enabled"] is False

    res = client.delete(f"/projects/reports/{report['id']}")
    assert res.status_code == 204
    assert client.get(f"/projects/reports/{report['id']}").status_code == 404
