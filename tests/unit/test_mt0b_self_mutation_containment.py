"""MT-0b — self-mutation is first-party-only.

Root ``AGENTS.md`` non-negotiable 3 has said this since it was written: native
MAF agents land approved self-mutations by opening a PR against **this**
Metorite monorepo, and "third parties must never push to the shared
monorepo". Nothing enforced it, and ``work_plan.md`` WS-3 records why — no
``first_party`` field existed anywhere, "the phrase occurs only in comments and
one test helper".

Migration 157 creates ``organization.first_party``, defaulting to **false**, so
a tenant created tomorrow is contained by construction rather than by someone
remembering. This module pins the gate that reads it.

The governing property is **fail closed**: every path that cannot *prove* the
org is first-party must refuse. Availability of self-mutation is worth far less
than the guarantee that a customer's agent never pushes to our repository.

Spec: ``project-docs/specs/saas_multitenancy.md`` §6.2 / MT-0b · WS-29.
"""
from __future__ import annotations

import orchestrator.mutation as mut
import pytest


class _Row(tuple):
    """A SQLAlchemy-ish row: indexable, truthy."""


class _FakeResult:
    def __init__(self, row):
        self._row = row

    def first(self):
        return self._row


class _FakeSession:
    """Minimal stand-in for the async session ``_self_mutation_permitted`` uses."""

    def __init__(self, row=None, raises: Exception | None = None):
        self._row, self._raises = row, raises
        self.closed = False

    async def execute(self, *_a, **_kw):
        if self._raises:
            raise self._raises
        return _FakeResult(self._row)

    async def close(self):
        self.closed = True


def _patch_db(monkeypatch, session):
    async def _get_db():
        return session
    monkeypatch.setattr("acb_common.db.get_db", _get_db, raising=True)


# ==========================================================================
# The containment itself.
# ==========================================================================
@pytest.mark.asyncio
async def test_non_first_party_org_is_refused(monkeypatch) -> None:
    """A tenant flagged first_party=false may not self-mutate."""
    _patch_db(monkeypatch, _FakeSession(row=_Row((False,))))

    allowed, reason = await mut._self_mutation_permitted("org-tenant-b")

    assert allowed is False
    assert "not flagged first-party" in reason


@pytest.mark.asyncio
async def test_first_party_org_is_permitted(monkeypatch) -> None:
    """The operator's own org keeps the behaviour it has today."""
    _patch_db(monkeypatch, _FakeSession(row=_Row((True,))))

    allowed, reason = await mut._self_mutation_permitted("org-fracktal")

    assert allowed is True
    assert reason == ""


@pytest.mark.asyncio
async def test_no_row_is_refused(monkeypatch) -> None:
    """No sole organization resolved → refuse.

    This is the multi-tenant case: the untenanted query requires
    ``count(*) = 1``, so it returns nothing once a second org exists. Refusing
    there is the whole containment — a "default org" fallback would keep
    answering true forever.
    """
    _patch_db(monkeypatch, _FakeSession(row=None))

    allowed, reason = await mut._self_mutation_permitted(None)

    assert allowed is False
    assert "no single first-party organization" in reason


@pytest.mark.asyncio
async def test_database_failure_is_refused_not_ignored(monkeypatch) -> None:
    """Fail CLOSED. An unreachable DB must not mean 'go ahead'."""
    _patch_db(monkeypatch, _FakeSession(raises=RuntimeError("connection refused")))

    allowed, reason = await mut._self_mutation_permitted("org-anything")

    assert allowed is False
    assert "could not establish" in reason


@pytest.mark.asyncio
async def test_operator_kill_switch_refuses_before_touching_the_db(monkeypatch) -> None:
    """SELF_MUTATION_DISABLED short-circuits — no query, no container."""
    monkeypatch.setenv("SELF_MUTATION_DISABLED", "1")

    def _boom():
        raise AssertionError("the DB must not be consulted when the kill switch is on")

    monkeypatch.setattr("acb_common.db.get_db", _boom, raising=True)

    allowed, reason = await mut._self_mutation_permitted("org-fracktal")

    assert allowed is False
    assert "SELF_MUTATION_DISABLED" in reason


# ==========================================================================
# done-when: a non-first-party failure event produces NO PR attempt.
# ==========================================================================
@pytest.mark.asyncio
async def test_tenant_failure_never_reaches_the_sandbox(monkeypatch) -> None:
    """The acceptance criterion, end to end at the public entry point.

    ``attempt_self_mutation`` must return ``attempted=False`` and must not reach
    the attempt tally, the sandbox, git, or the network. Anything it *did* reach
    would be work done on a tenant's behalf against our own repository.
    """
    _patch_db(monkeypatch, _FakeSession(row=_Row((False,))))

    def _never(*_a, **_kw):
        raise AssertionError("a non-first-party run reached the mutation machinery")

    monkeypatch.setattr(mut, "_register_mutation_attempt", _never, raising=True)
    monkeypatch.setattr(mut, "_run_mutation_sandbox", _never, raising=True)

    result = await mut.attempt_self_mutation(
        "agent-tenant", "run-123", RuntimeError("boom"),
        organization_id="org-tenant-b",
    )

    assert result.attempted is False
    assert result.skipped_reason
    assert "first-party" in result.skipped_reason


@pytest.mark.asyncio
async def test_first_party_still_reaches_the_attempt_tally(monkeypatch) -> None:
    """The gate must not break the operator's own path.

    Guards against the fix being "refuse everything", which would pass every
    test above and silently switch the feature off for Fracktal too.
    """
    _patch_db(monkeypatch, _FakeSession(row=_Row((True,))))
    reached = {}

    def _tally(run_id, prior=0):
        reached["yes"] = True
        return False, 1          # deny at the tally, so nothing further runs

    monkeypatch.setattr(mut, "_register_mutation_attempt", _tally, raising=True)

    result = await mut.attempt_self_mutation(
        "agent-ours", "run-456", RuntimeError("boom"),
        organization_id="org-fracktal",
    )

    assert reached.get("yes") is True
    assert result.attempted is False
    assert "max_mutation_attempts" in (result.skipped_reason or "")


# ==========================================================================
# The migration is the source of truth for the default.
# ==========================================================================
def test_migration_defaults_first_party_to_false() -> None:
    """DEFAULT false is the containment. A later edit to `true` would silently
    un-contain every tenant created after it."""
    from pathlib import Path
    sql = Path(__file__).resolve().parents[2] / "infra/postgres/157_org_first_party.sql"
    text = sql.read_text(encoding="utf-8")
    assert "first_party BOOLEAN NOT NULL DEFAULT false" in text
    assert "WHERE slug = 'default'" in text, "the operator's own org must be backfilled true"
