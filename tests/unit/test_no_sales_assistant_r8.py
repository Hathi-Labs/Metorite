"""R8 — the removed agent's ``dynamic_agents`` row goes, and stays gone.

Spec: ``project-docs/specs/crm_platform.md`` §20.10 item 6, done-when 6
(WS-53 CRM-C7). Two facts, against a REAL Postgres in the production shape:
the four ``generated/`` phases applied, so ``dynamic_agents`` has
``organization_id`` and FORCE row level security.

1. The migration deletes the row in each tenant. It runs as a role that is
   NOT a superuser, NOT the owner and NOT BYPASSRLS. So a file with no tenant
   bind would delete nothing and still report success, and this test would
   go red.
2. A load of the agents does not bring the row back. The importer
   (``routes/agent.py::_sync_file_into_db``) copies each ``agents.json``
   entry that the table lacks. The control case proves that it WOULD copy the
   old entry back, so the empty ``agents.json`` is what keeps the row gone.

The migration is found by CONTENT, never by number (R1).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

# Without the gate the suite ERRORS where no ladder URL is set (deploy.yml).
pytestmark = _DB_GATE

REPO = Path(__file__).resolve().parents[2]
NAME = "agent-sales-assistant"
KEEP = "keep-me-c7"


def _migration() -> Path:
    found = [
        p for p in sorted((REPO / "infra" / "postgres").glob("*.sql"))
        if "DELETE FROM dynamic_agents" in p.read_text(encoding="utf-8")
        and NAME in p.read_text(encoding="utf-8")
    ]
    assert len(found) == 1, found
    return found[0]


def _run_migration(engine: Any) -> None:
    """Run the file verbatim through the DBAPI cursor, in one transaction."""
    with engine.begin() as conn, conn.connection.dbapi_connection.cursor() as cur:
        cur.execute(_migration().read_text(encoding="utf-8"))


def _insert(admin: Any, org: str, name: str) -> None:
    with admin.begin() as c:
        c.execute(text(
            "INSERT INTO dynamic_agents (name, description, organization_id) "
            "VALUES (:n, 'r8', CAST(:o AS uuid))"), {"n": name, "o": org})


def _count(admin: Any, name: str) -> int:
    with admin.connect() as c:
        return int(c.execute(text(
            "SELECT count(*) FROM dynamic_agents WHERE name = :n"), {"n": name},
        ).scalar() or 0)


@pytest.fixture
def ns(promoted):  # noqa: F811 — a fixture
    yield promoted
    with promoted.admin_engine.begin() as c:
        c.execute(text(
            "DELETE FROM dynamic_agents WHERE name = ANY(CAST(:n AS text[]))"),
            {"n": [NAME, KEEP]})


def _bound_graph(monkeypatch: Any, p: Any, org: str) -> Any:
    """Point ``acb_graph.get_session`` at the catalog as the non-privileged
    role, with *org* bound on each connection, the way a tenant run would."""
    from acb_graph import db as graph_db

    eng = create_engine(
        p.app_url.render_as_string(hide_password=False), future=True,
        poolclass=NullPool,
    )

    @event.listens_for(eng, "connect")
    def _bind(dbapi_conn: Any, _record: Any) -> None:
        with dbapi_conn.cursor() as cur:
            cur.execute("SELECT set_config('app.tenant_id', %s, false)", (org,))
        dbapi_conn.commit()

    factory = sessionmaker(bind=eng, expire_on_commit=False, future=True)
    monkeypatch.setattr(graph_db, "_session_factory", lambda: factory)
    return eng


def test_the_migration_deletes_the_row_in_each_tenant(ns, app_engine) -> None:  # noqa: F811
    admin = ns.admin_engine
    _insert(admin, ns.org_b, NAME)
    _insert(admin, ns.org_a, KEEP)
    assert _count(admin, NAME) == 1

    _run_migration(app_engine)
    assert _count(admin, NAME) == 0
    assert _count(admin, KEEP) == 1

    _run_migration(app_engine)  # idempotent
    assert _count(admin, NAME) == 0 and _count(admin, KEEP) == 1


def test_the_agents_file_no_longer_names_the_agent() -> None:
    entries = json.loads(
        (REPO / "apps" / "services" / "gateway" / "agents.json").read_text(encoding="utf-8"),
    )
    assert NAME not in {e.get("name") for e in entries}


def test_a_load_does_not_bring_the_row_back(ns, monkeypatch, tmp_path) -> None:
    import gateway.routes.agent as routes_agent

    eng = _bound_graph(monkeypatch, ns, ns.org_a)
    try:
        # Control: the importer copies an agents.json entry that the table
        # lacks. So the old file WOULD bring the row back.
        old = tmp_path / "agents.json"
        old.write_text(json.dumps([{"name": NAME, "description": "old"}]), encoding="utf-8")
        monkeypatch.setattr(routes_agent, "_get_agents_file", lambda: old)
        routes_agent._sync_file_into_db()
        assert _count(ns.admin_engine, NAME) == 1, "the control did not import"

        _run_migration(ns.admin_engine)
        assert _count(ns.admin_engine, NAME) == 0

        # The real file: a load leaves the table without the row.
        monkeypatch.undo()
        _bound_graph(monkeypatch, ns, ns.org_a)
        loaded = routes_agent._load_dynamic_agents()
        assert NAME not in {a["name"] for a in loaded}
        assert _count(ns.admin_engine, NAME) == 0
    finally:
        eng.dispose()
