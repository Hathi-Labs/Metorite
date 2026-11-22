"""R8 — the skill toggles of one agent are per organization (H2, task 566).

``gateway.routes.agent_skills`` opened its session with ``get_db()``: no tenant
bound. ``agent_skill_setting`` is a tenant table (FORCE row-level security after
the generated phase 4). An unbound session reads no row and writes a row with no
tenant, so a toggle either vanished or landed under no organization. The route
now opens ``tenant_session()``, the tenant that ``get_current_user`` bound.

This suite runs the REAL route functions as the NON-privileged role of the
phase-4 rehearsal (``acb_app_h3rls``: no superuser, no BYPASSRLS), on a catalog
with two organizations. A hermetic fake would agree with any SQL it is handed.

Mutations this suite catches (R7):

* ``_tenant_session`` back to the unbound ``get_db()``: the first test reads
  nothing and the second finds a row with no organization;
* an explicit ``db.commit()`` back in ``set_agent_skills``: the answer is read
  after the commit has dropped the bind, so ``settings`` comes back empty.

⚠️ **Known gap, pinned on purpose.** The primary key of ``agent_skill_setting``
is ``(agent_name, instance, family)`` with no organization. So org B cannot save
a family that org A already holds for the same agent: the insert fails on the
key, and org A's row never changes. The same class as H-201d (migration 354 and
474) and H-256 (migration 239). A migration that keys the table per tenant is
its own slice (expand and contract, R6). The test below pins today's behavior,
and it flips with that migration.

Run::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_agent_skills_per_org_r8.py -v -rs
"""
from __future__ import annotations

import contextlib
import uuid
from types import SimpleNamespace

import pytest

pytest.importorskip("sqlalchemy")

from acb_common.db import TenantUnbound, bind_tenant, clear_tenant, release_tenant
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

# The two-org phase-4 fixture and its DB gate. Used by name for injection.
from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

ADMIN = SimpleNamespace(email="admin@h2-skills.test")


def _agent() -> str:
    return f"h2-agent-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def db(promoted, monkeypatch):  # noqa: F811 — a fixture
    """The promoted catalog, with this module's rows removed after."""
    from gateway.routes import agent_skills

    made: list[str] = []
    promoted.made = made
    # The registry and the audit writer are other seams with their own fences.
    monkeypatch.setattr(agent_skills, "_known_agent_names", lambda: None)
    monkeypatch.setattr(agent_skills, "_record_change", lambda *a, **k: None)
    yield promoted
    with promoted.admin_engine.begin() as c:
        for name in made:
            c.execute(text("DELETE FROM agent_skill_setting WHERE agent_name = :n"),
                      {"n": name})


@contextlib.contextmanager
def _tenant(org: str | None):
    token = bind_tenant(org) if org else clear_tenant()
    try:
        yield
    finally:
        release_tenant(token)


def _app_dsn(ns) -> str:
    return ns.app_url.render_as_string(hide_password=False)


async def _put(org: str | None, name: str, settings: list[dict]) -> dict:
    from gateway.routes.agent_skills import (
        SkillSettingEntry,
        SkillSettingsRequest,
        set_agent_skills,
    )

    req = SkillSettingsRequest(settings=[SkillSettingEntry(**s) for s in settings])
    with _tenant(org):
        return await set_agent_skills(name, req, admin=ADMIN)


async def _get(org: str | None, name: str) -> dict:
    from gateway.routes.agent_skills import get_agent_skills

    with _tenant(org):
        return await get_agent_skills(name, user=ADMIN)


def _rows(ns, name: str) -> list[tuple[str, str, bool]]:
    """Every row for *name*, as the owner sees it: ``(org, family, enabled)``."""
    with ns.admin_engine.connect() as c:
        return [tuple(r) for r in c.execute(text(
            "SELECT coalesce(organization_id::text, 'NONE'), family, enabled "
            "FROM agent_skill_setting WHERE agent_name = :n ORDER BY 1, 2"),
            {"n": name})]


# ── The route, bound to the tenant ──────────────────────────────────────────


async def test_a_toggle_lands_in_the_admins_org_and_reads_back_there(db) -> None:
    """The answer of the PUT is read inside the bound block, so it carries the
    row. The row is stamped org A, and org B reads none of it."""
    name = _agent()
    db.made.append(name)
    async with tenant_engine_scope(_app_dsn(db)):
        put = await _put(db.org_a, name, [{"family": "memory", "enabled": False,
                                           "reason": "context budget"}])
        a = await _get(db.org_a, name)
        b = await _get(db.org_b, name)
    assert put["disabled_families"] == ["memory"], "the PUT read its answer unbound"
    assert a["disabled_families"] == ["memory"]
    assert [s["reason"] for s in a["settings"]] == ["context budget"]
    assert b["settings"] == [] and b["disabled_families"] == []
    assert _rows(db, name) == [(db.org_a, "memory", False)]


async def test_a_replace_in_one_org_deletes_nothing_of_another(db) -> None:
    """``PUT`` replaces the agent's rows wholesale. Under RLS the DELETE reaches
    the bound org only, so org B emptying its list leaves org A's row."""
    name = _agent()
    db.made.append(name)
    async with tenant_engine_scope(_app_dsn(db)):
        await _put(db.org_a, name, [{"family": "memory", "enabled": False}])
        await _put(db.org_b, name, [])
        a = await _get(db.org_a, name)
    assert a["disabled_families"] == ["memory"]
    assert _rows(db, name) == [(db.org_a, "memory", False)]


async def test_two_orgs_toggle_different_families_of_one_agent(db) -> None:
    name = _agent()
    db.made.append(name)
    async with tenant_engine_scope(_app_dsn(db)):
        await _put(db.org_a, name, [{"family": "memory", "enabled": False}])
        await _put(db.org_b, name, [{"family": "documents", "enabled": False}])
        a = await _get(db.org_a, name)
        b = await _get(db.org_b, name)
    assert a["disabled_families"] == ["memory"]
    assert b["disabled_families"] == ["documents"]
    assert _rows(db, name) == sorted([(db.org_a, "memory", False),
                                      (db.org_b, "documents", False)])


async def test_with_no_tenant_the_route_refuses_and_writes_nothing(db) -> None:
    """Fail closed: no bound tenant is ``TenantUnbound``, never an unbound read
    or a row with no organization."""
    name = _agent()
    db.made.append(name)
    async with tenant_engine_scope(_app_dsn(db)):
        with pytest.raises(TenantUnbound):
            await _put(None, name, [{"family": "memory", "enabled": False}])
        with pytest.raises(TenantUnbound):
            await _get(None, name)
    assert _rows(db, name) == []


# ── The known gap, pinned ───────────────────────────────────────────────────


async def test_a_family_that_another_org_holds_still_fails_on_the_old_key(db) -> None:
    """KNOWN GAP (see the module doc). The key has no organization, so org B's
    insert of the same ``(agent, instance, family)`` fails on the primary key. It
    never reaches org A's row. This test changes with the per-tenant key
    migration."""
    name = _agent()
    db.made.append(name)
    async with tenant_engine_scope(_app_dsn(db)):
        await _put(db.org_a, name, [{"family": "memory", "enabled": False,
                                     "reason": "a"}])
        with pytest.raises(IntegrityError) as err:
            await _put(db.org_b, name, [{"family": "memory", "enabled": False,
                                         "reason": "b"}])
        a = await _get(db.org_a, name)
    assert "agent_skill_setting_pkey" in str(err.value)
    assert [s["reason"] for s in a["settings"]] == ["a"]
    assert _rows(db, name) == [(db.org_a, "memory", False)]
