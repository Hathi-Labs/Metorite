"""Unit tests for the Phase-0 graph + retrieval + normaliser + audit-log slice."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration  # all of these hit the live Postgres container


def test_seed_visible_via_repo() -> None:
    """Seed data from scripts/seed_demo.py should be queryable."""
    from acb_graph import get_session, repo

    with get_session() as s:
        projects = repo.find_projects_by_text(s, "julian")
        assert any("Julian" in p.name for p in projects), [p.name for p in projects]
        tasks = repo.tasks_for_project(s, projects[0].id)
        assert any("torque" in t.title.lower() for t in tasks)


def test_retrieval_returns_cited_block() -> None:
    from acb_graph import get_session
    from orchestrator.retrieval import format_context, retrieve

    with get_session() as s:
        hits = retrieve(s, "Julian")
    assert hits, "expected at least one hit for 'Julian'"
    block = format_context(hits)
    assert "[project:" in block
    # Every hit must produce a copy-pasteable citation token.
    for h in hits:
        assert h.cite.startswith(f"[{h.kind}:")
        assert str(h.id) in h.cite


def test_audit_event_persists_to_db() -> None:
    from acb_audit import AuditEvent, record
    from acb_graph import get_session
    from acb_graph.models import AuditEvent as AuditRow
    from sqlalchemy import select

    evt = AuditEvent(actor="test:pytest", action="ping", target="test:target", payload={"k": 1})
    record(evt)

    with get_session() as s:
        row = s.execute(select(AuditRow).where(AuditRow.id == evt.id)).scalar_one()
    assert row.actor == "test:pytest"
    assert row.payload == {"k": 1}


# ⚠️ `test_clickup_normaliser_upserts` was DELETED 2026-08-24 by D52 (board
# WS-39 S1) along with `ingestion/sources/clickup/`. Metorite is the
# project-management system of record, so there is no PM normaliser to test.
# The `Task.clickup_id` COLUMN survives on purpose (D52.3, R6 expand/contract)
# — it is dropped in a later release, not by whoever notices it next.
