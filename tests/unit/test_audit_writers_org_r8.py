"""Task 2221, slice 1 — five audit writers file their rows in the bound tenant.

Each writer below recorded tenant text with no ``organization_id``. With
``ACB_GRAPH_TENANT_BIND`` on, ``acb_audit._persist`` filed the row under the
OPERATOR org (Option-A), so tenant B's votes, error text, queries and model
answers sat in the operator org's audit rows:

* ``gateway.routes.chat.record_message_feedback``: ``message_feedback``.
* ``orchestrator.mutation.attempt_self_mutation``: ``mutation_start`` and the
  outcome row.
* ``orchestrator.agents.pull_agent.answer`` and ``sales_pull_agent.answer``:
  the query row and ``guardrail_block``.
* ``orchestrator.resolution.resolve_with_llm``: ``tiebreak`` and
  ``tiebreak_error``.

The fix stamps the bound tenant on the calling frame. The chat route reads
``user.organization_id`` from the identity. The orchestrator writers read the
run binding (``_RUN_ORG``). The executor passes ``_RUN_ORG.get(thread_id)``
to ``attempt_self_mutation`` as ``audit_organization_id``. No writer reads a
request field or a payload field (R5). With no org and the bind on,
``acb_audit.refuse_tenantless`` skips the row.

Fences (R7):

* ``audit-writer-row-in-bound-tenant`` (R8, phase-4 two-org catalog, non-priv
  role ``acb_app_h3rls``): one case for each of the nine events. Each row
  lands stamped org B, visible to org B only, and none under the operator org.
  RED when the stamp of that one event is dropped.
* ``audit-writer-tenantless-skips`` (R8): no bound tenant writes no row.
* The no-database half is ``test_audit_writers_org.py``.

Run (real Postgres)::

    TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5434/acb_tenant \\
        uv run pytest tests/unit/test_audit_writers_org_r8.py -v -rs
"""
from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_audit import log as audit
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from tests.unit.test_audit_writers_org import (
    _feedback,
    _patch_mutation,
    _patch_pull,
    _patch_resolution,
    _pull,
    _tiebreak,
)

# ``promoted`` is used by name for pytest fixture injection.
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    promoted,
)

pytestmark = _DB_GATE


@pytest.fixture
def graph_on_promoted(promoted, monkeypatch):  # noqa: F811
    from acb_graph import db as graph_db

    dsn = promoted.app_url.render_as_string(hide_password=False)
    eng = create_engine(dsn, future=True, poolclass=NullPool)
    factory = sessionmaker(bind=eng, expire_on_commit=False, future=True)
    monkeypatch.setattr(graph_db, "_session_factory", lambda: factory)
    monkeypatch.setattr(audit, "_OPERATOR_ORG_ID", None)
    monkeypatch.delenv("OPERATOR_ORG_ID", raising=False)
    monkeypatch.setenv("ACB_GRAPH_TENANT_BIND", "1")
    try:
        yield promoted
    finally:
        eng.dispose()


@pytest.fixture
def written(monkeypatch) -> dict[str, list[str]]:
    """The id of each event that reaches the real ``acb_audit.record``, by action.

    A pass-through on the ``record`` name of each writer. The write itself is
    the real one, so the test reads the rows back by id.
    """
    import acb_audit
    from orchestrator import mutation, resolution
    from orchestrator.agents import pull_agent, sales_pull_agent

    real = audit.record
    seen: dict[str, list[str]] = {}

    def _spy(event: Any) -> None:
        seen.setdefault(event.action, []).append(str(event.id))
        real(event)

    monkeypatch.setattr(acb_audit, "record", _spy)
    for mod in (mutation, resolution, pull_agent, sales_pull_agent):
        monkeypatch.setattr(mod, "record", _spy)
    return seen


def _orgs_of(admin_engine, ids: list[str]) -> list[str]:
    with admin_engine.connect() as c:
        return sorted(
            r[0]
            for r in c.execute(
                text(
                    "SELECT organization_id::text FROM audit_event "
                    "WHERE id::text = ANY(:i)"
                ),
                {"i": ids},
            )
        )


def _visible_as(app_url, org: str, ids: list[str]) -> int:
    eng = create_engine(app_url, future=True)
    try:
        with eng.connect() as c, c.begin():
            c.execute(text("SELECT set_config('app.tenant_id', :o, true)"), {"o": org})
            return c.execute(
                text("SELECT count(*) FROM audit_event WHERE id::text = ANY(:i)"),
                {"i": ids},
            ).scalar_one()
    finally:
        eng.dispose()


def _default_org(admin_engine) -> str:
    with admin_engine.connect() as c:
        return c.execute(
            text("SELECT id::text FROM organization WHERE slug = 'default'")
        ).scalar_one()


def _delete(admin_engine, ids: list[str]) -> None:
    if not ids:
        return
    with admin_engine.begin() as c:
        c.execute(text("DELETE FROM audit_event WHERE id::text = ANY(:i)"), {"i": ids})


def _all_ids(seen: dict[str, list[str]]) -> list[str]:
    return [i for ids in seen.values() for i in ids]


async def _drained(coro) -> Any:
    out = await coro
    await audit.drain()
    return out


# ==========================================================================
# The drivers. Each one runs one writer with org B bound (or no tenant).
# ==========================================================================
def _run_feedback(_mp, org: str | None) -> None:
    asyncio.run(_drained(_feedback(org, f"m-{uuid.uuid4().hex[:8]}")))


def _run_mutation(mp, org: str | None) -> None:
    from orchestrator import mutation

    _patch_mutation(mp)
    asyncio.run(_drained(mutation.attempt_self_mutation(
        f"agent-t2221-{uuid.uuid4().hex[:8]}", f"run-{uuid.uuid4().hex}",
        RuntimeError("org B tenant text"), audit_organization_id=org,
    )))


def _run_pull(mp, org: str | None) -> None:
    from orchestrator.agents import pull_agent

    _patch_pull(mp, pull_agent, "retrieve")
    asyncio.run(_drained(_pull(pull_agent, org, "what does org B owe")))


def _run_sales_pull(mp, org: str | None) -> None:
    from orchestrator.agents import sales_pull_agent

    _patch_pull(mp, sales_pull_agent, "sales_context")
    asyncio.run(_drained(_pull(sales_pull_agent, org, "org B pipeline")))


def _run_tiebreak(mp, org: str | None) -> None:
    _patch_resolution(mp, fail=False)
    asyncio.run(_drained(_tiebreak(org)))


def _run_tiebreak_error(mp, org: str | None) -> None:
    _patch_resolution(mp, fail=True)
    asyncio.run(_drained(_tiebreak(org)))


#: One case for each event, so a dropped stamp turns exactly one case red.
_CASES: list[tuple[str, Callable[[Any, str | None], None]]] = [
    ("message_feedback", _run_feedback),
    ("mutation_start", _run_mutation),
    ("mutation_sandbox_failed", _run_mutation),
    ("pull_query", _run_pull),
    ("guardrail_block:pull", _run_pull),
    ("sales_pull_query", _run_sales_pull),
    ("guardrail_block:sales_pull", _run_sales_pull),
    ("tiebreak", _run_tiebreak),
    ("tiebreak_error", _run_tiebreak_error),
]


class TestAuditWritersOrgUnderRls:
    @pytest.mark.parametrize(("event", "drive"), _CASES, ids=[c[0] for c in _CASES])
    def test_the_row_for_org_b_lands_in_org_b(
        self, graph_on_promoted, written, monkeypatch, event, drive,
    ):
        p = graph_on_promoted
        operator = _default_org(p.admin_engine)
        assert operator not in (p.org_a, p.org_b)
        action = event.split(":", 1)[0]
        try:
            drive(monkeypatch, p.org_b)
            ids = written.get(action, [])
            assert len(ids) == 1, (
                f"{event}: expected ONE row to reach the audit seam, got "
                f"{len(ids)} — the event carried no organization_id, so "
                "refuse_tenantless skipped it"
            )
            orgs = _orgs_of(p.admin_engine, ids)
            assert operator not in orgs, (
                f"{event}: org B's row landed under the OPERATOR org — "
                "cross-tenant leak"
            )
            assert orgs == [p.org_b], f"{event}: expected one row in org B, got {orgs}"
            assert _visible_as(p.app_url, p.org_b, ids) == 1
            assert _visible_as(p.app_url, operator, ids) == 0
            assert _visible_as(p.app_url, p.org_a, ids) == 0
        finally:
            _delete(p.admin_engine, _all_ids(written))

    @pytest.mark.parametrize(
        "drive",
        [_run_feedback, _run_mutation, _run_pull, _run_sales_pull,
         _run_tiebreak, _run_tiebreak_error],
        ids=["feedback", "mutation", "pull", "sales_pull", "tiebreak", "tiebreak_error"],
    )
    def test_no_tenant_writes_no_row(self, graph_on_promoted, written, monkeypatch, drive):
        p = graph_on_promoted
        try:
            drive(monkeypatch, None)
            assert written == {}, (
                f"a tenant-less writer reached the audit seam: {sorted(written)} — "
                "the operator org fallback would take the row"
            )
        finally:
            _delete(p.admin_engine, _all_ids(written))
