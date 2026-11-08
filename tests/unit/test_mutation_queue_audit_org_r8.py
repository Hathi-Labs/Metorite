"""Task 2221 — a mutation-queue decision lands in the reviewer's tenant.

Four routes of ``gateway.routes.agent`` record what a reviewer decided on a
self-mutation commit: ``mutation_monorepo_pr_opened``,
``mutation_commit_approved``, ``mutation_commit_rejected`` and
``mutation_commit_remutate_requested``. The row names the reviewer, the agent,
the commit and the PR. They recorded with no ``organization_id``, so with
``ACB_GRAPH_TENANT_BIND`` on ``acb_audit._persist`` filed them under the
OPERATOR org (Option-A). Their siblings (``mutation_commit_deleted`` and
``mutation_audit_dismissed``) already carry ``user.organization_id``.

The fix: each of the four goes through ``_mutation_audit``, which stamps
``user.organization_id``. That is the tenant that ``get_current_user``
resolved from the identity, and the one ``_member_graph_session`` already
wrote the ``pending_commit`` row in (R5). With no org and the bind on,
``acb_audit.refuse_tenantless`` skips the row.

Fences (R7):

* ``mutation-audit-row-in-reviewer-tenant`` (R8, phase-4 two-org catalog,
  non-priv role ``acb_app_h3rls``): an approve (keep-local and monorepo PR),
  a reject and a re-mutate by an org B reviewer land rows stamped org B,
  visible to org B only, and none under the operator org. RED on main.
* ``mutation-audit-tenantless-skips``: the helper records nothing with no org
  and the bind on.
* ``mutation-audit-one-door``: the four routes call ``_mutation_audit`` and no
  bare ``record(``.

Run (real Postgres)::

    TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5434/acb_tenant \\
        uv run pytest tests/unit/test_mutation_queue_audit_org_r8.py -v -rs
"""
from __future__ import annotations

import asyncio
import inspect
import subprocess
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_audit import AuditEvent
from acb_audit import log as audit
from gateway.routes import agent as agent_routes
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

# ``promoted`` is used by name for pytest fixture injection.
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    promoted,
)

REVIEWER = "reviewer@t2221.test"

_ROUTES = (
    "approve_pending_commit",
    "reject_pending_commit",
    "remutate_pending_commit",
)


def _user(org: str | None):
    from acb_auth import UserContext
    from acb_auth.roles import UserRole

    return UserContext(email=REVIEWER, role=UserRole.EXECUTIVE, organization_id=org)


@contextmanager
def _as_tenant(org: str | None) -> Iterator[None]:
    from acb_common.db import bind_tenant, clear_tenant, release_tenant

    token = bind_tenant(org) if org else clear_tenant()
    try:
        yield
    finally:
        release_tenant(token)


# ==========================================================================
# 1. No DB: the one door and the skip.
# ==========================================================================
def test_the_four_decisions_go_through_one_door() -> None:
    for name in _ROUTES:
        src = inspect.getsource(getattr(agent_routes, name))
        assert "_mutation_audit(" in src, f"{name} does not call _mutation_audit"
        assert " record(" not in src and "\trecord(" not in src, (
            f"{name} calls record() directly, so its row carries no org"
        )


def test_no_org_with_the_bind_on_records_nothing(monkeypatch) -> None:
    import acb_audit

    calls: list[Any] = []
    monkeypatch.setattr(acb_audit, "record", calls.append)
    monkeypatch.setenv("ACB_GRAPH_TENANT_BIND", "1")
    agent_routes._mutation_audit(
        _user(None),
        AuditEvent(actor=f"human:{REVIEWER}", action="mutation_commit_rejected",
                   target="agent:x", payload={}),
    )
    assert calls == [], "a tenant-less queue decision reached the audit seam"


# ==========================================================================
# 2. R8: the real routes and the real ``acb_audit`` write.
# ==========================================================================
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


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
    ).stdout.strip()


def _clone(tmp: Path) -> tuple[Path, str]:
    """A local repo with one commit and no remote: the keep-local path."""
    tmp.mkdir(parents=True)
    _git(tmp, "init", "-q", "-b", "main")
    _git(tmp, "config", "user.email", "t@t2221.test")
    _git(tmp, "config", "user.name", "t2221")
    (tmp / "f.txt").write_text("x\n", encoding="utf-8")
    _git(tmp, "add", "f.txt")
    _git(tmp, "commit", "-q", "-m", "fix")
    return tmp, _git(tmp, "rev-parse", "HEAD")


def _seed(p, org: str, agent: str, clone: Path, sha: str) -> str:
    cid = str(uuid.uuid4())
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO pending_commit (id, agent_name, run_id, local_clone_dir, "
            "commit_sha, commit_message, status, organization_id) VALUES "
            "(CAST(:i AS uuid), :a, 'run-t2221', :d, :s, 'fix', 'pending', "
            "CAST(:o AS uuid))"),
            {"i": cid, "a": agent, "d": str(clone), "s": sha, "o": org})
    return cid


def _rows(admin_engine, agent: str) -> list[tuple[str, str]]:
    with admin_engine.connect() as c:
        return sorted(
            (r[0], r[1])
            for r in c.execute(
                text(
                    "SELECT action, organization_id::text FROM audit_event "
                    "WHERE target = :t"
                ),
                {"t": f"agent:{agent}"},
            )
        )


def _visible_as(app_url, org: str, agent: str) -> int:
    eng = create_engine(app_url, future=True)
    try:
        with eng.connect() as c, c.begin():
            c.execute(text("SELECT set_config('app.tenant_id', :o, true)"), {"o": org})
            return c.execute(
                text("SELECT count(*) FROM audit_event WHERE target = :t"),
                {"t": f"agent:{agent}"},
            ).scalar_one()
    finally:
        eng.dispose()


def _default_org(admin_engine) -> str:
    with admin_engine.connect() as c:
        return c.execute(
            text("SELECT id::text FROM organization WHERE slug = 'default'")
        ).scalar_one()


def _cleanup(admin_engine, agent: str) -> None:
    with admin_engine.begin() as c:
        c.execute(text("DELETE FROM audit_event WHERE target = :t"),
                  {"t": f"agent:{agent}"})
        c.execute(text("DELETE FROM pending_commit WHERE agent_name = :a"),
                  {"a": agent})


async def _drained(coro):
    out = await coro
    await audit.drain()
    return out


@_DB_GATE
class TestMutationQueueAuditOrgUnderRls:
    def test_decisions_by_an_org_b_reviewer_land_in_org_b(
        self, graph_on_promoted, tmp_path, monkeypatch
    ):
        from gateway.routes import monorepo_pr

        p = graph_on_promoted
        operator = _default_org(p.admin_engine)
        assert operator not in (p.org_a, p.org_b)
        agent = f"t2221-{uuid.uuid4().hex[:8]}"
        user = _user(p.org_b)
        try:
            # 1. approve, keep-local (no remote, monorepo PR off).
            monkeypatch.setattr(monorepo_pr, "monorepo_pr_configured", lambda: False)
            clone, sha = _clone(tmp_path / "keep")
            cid = _seed(p, p.org_b, agent, clone, sha)
            with _as_tenant(p.org_b):
                out = asyncio.run(_drained(
                    agent_routes.approve_pending_commit(cid, user=user)))
            assert out["status"] == "approved"

            # 2. approve, monorepo PR (no remote, monorepo PR on).
            async def _pr(**_k):
                return SimpleNamespace(pr_url="https://example.test/pr/1",
                                       target_path="apps/agents/x")

            monkeypatch.setattr(monorepo_pr, "monorepo_pr_configured", lambda: True)
            monkeypatch.setattr(monorepo_pr, "open_monorepo_pr", _pr)
            clone, sha = _clone(tmp_path / "pr")
            cid = _seed(p, p.org_b, agent, clone, sha)
            with _as_tenant(p.org_b):
                out = asyncio.run(_drained(
                    agent_routes.approve_pending_commit(cid, user=user)))
            assert out["status"] == "pr_open"

            # 3. reject and 4. re-mutate (HEAD is not the row's sha: no reset).
            for route in (agent_routes.reject_pending_commit,
                          agent_routes.remutate_pending_commit):
                clone, _sha = _clone(tmp_path / route.__name__)
                cid = _seed(p, p.org_b, agent, clone, "0" * 40)
                with _as_tenant(p.org_b):
                    asyncio.run(_drained(route(cid, user=user)))

            rows = _rows(p.admin_engine, agent)
            orgs = [o for _a, o in rows]
            assert operator not in orgs, (
                "org B's queue decision landed under the OPERATOR org — "
                f"cross-tenant leak (the event carried no organization_id): {rows}"
            )
            assert rows == sorted([
                ("mutation_commit_approved", p.org_b),
                ("mutation_monorepo_pr_opened", p.org_b),
                ("mutation_commit_rejected", p.org_b),
                ("mutation_commit_remutate_requested", p.org_b),
            ]), f"expected FOUR rows stamped org B, got {rows}"
            assert _visible_as(p.app_url, p.org_b, agent) == 4
            assert _visible_as(p.app_url, operator, agent) == 0
            assert _visible_as(p.app_url, p.org_a, agent) == 0
        finally:
            _cleanup(p.admin_engine, agent)

    def test_a_tenantless_decision_writes_no_row(self, graph_on_promoted):
        p = graph_on_promoted
        agent = f"t2221-none-{uuid.uuid4().hex[:8]}"
        try:
            with _as_tenant(None):
                agent_routes._mutation_audit(
                    _user(None),
                    AuditEvent(actor=f"human:{REVIEWER}",
                               action="mutation_commit_rejected",
                               target=f"agent:{agent}", payload={}),
                )
                asyncio.run(audit.drain())
            assert _rows(p.admin_engine, agent) == [], (
                "a tenant-less queue decision wrote an audit row (the operator "
                "org fallback took it) — fail-closed broke"
            )
        finally:
            _cleanup(p.admin_engine, agent)
