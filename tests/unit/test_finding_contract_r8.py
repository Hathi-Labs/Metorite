"""R8: delivery of a finding against real group rows (LOOP-7).

``proactive_loop.md`` §8.4. A recipient outside ``visibility_scope`` gets
nothing. Group membership comes from the one groups query, as the app role
under FORCE ROW LEVEL SECURITY.

Run (real Postgres)::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_finding_contract_r8.py -v -rs
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import text
from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE


@pytest.fixture
def world(promoted, app_engine):  # noqa: F811 — fixtures
    p = promoted
    tag = uuid.uuid4().hex[:8]
    m = SimpleNamespace(**{k: f"{k}-{tag}@lp7.test" for k in
                           ("carol", "dave", "gone", "zed")})
    slug = f"sales-{tag}"
    users: list[str] = []
    with p.admin_engine.begin() as c:
        ids = {}
        for name, status, org in (("carol", "active", p.org_a), ("dave", "active", p.org_a),
                                  ("gone", "removed", p.org_a), ("zed", "active", p.org_b)):
            ids[name] = str(c.execute(text(
                "INSERT INTO app_user (email, display_name, role, status, organization_id) "
                "VALUES (:e, :e, 'employee', :s, CAST(:o AS uuid)) RETURNING id"),
                {"e": getattr(m, name), "s": status, "o": org}).scalar_one())
            users.append(getattr(m, name))
        gid = str(c.execute(text(
            "INSERT INTO org_group (organization_id, slug, display_name) "
            "VALUES (CAST(:o AS uuid), :s, :s) RETURNING id"),
            {"o": p.org_a, "s": slug}).scalar_one())
        for name in ("carol", "gone"):
            c.execute(text(
                "INSERT INTO org_group_member (group_id, user_id, organization_id) "
                "VALUES (CAST(:g AS uuid), CAST(:u AS uuid), CAST(:o AS uuid))"),
                {"g": gid, "u": ids[name], "o": p.org_a})
    try:
        yield SimpleNamespace(p=p, m=m, slug=slug)
    finally:
        with p.admin_engine.begin() as c:
            c.execute(text("DELETE FROM org_group_member WHERE group_id = CAST(:g AS uuid)"), {"g": gid})
            c.execute(text("DELETE FROM org_group WHERE id = CAST(:g AS uuid)"), {"g": gid})
            c.execute(text("DELETE FROM app_user WHERE email = ANY(:u)"), {"u": users})


async def _groups(w, org: str, email: str) -> list[str]:
    from acb_common.db import bind_tenant, release_tenant
    from gateway.db import tenant_session
    from gateway.routes.shell.finding_contract import member_groups

    token = bind_tenant(org)
    try:
        async with (
            tenant_engine_scope(w.p.app_url.render_as_string(hide_password=False)),
            tenant_session() as db,
        ):
            return await member_groups(db, email)
    finally:
        release_tenant(token)


def _finding(scope):
    from gateway.routes.shell import finding_contract as fc
    return fc.Finding(
        kind="k", entity={"type": "task", "id": "1", "label": "T"}, signal_id="tasks:1",
        fingerprint="f", facts={}, expires_at=None, acts=(), href="/t",
        evidence=(fc.Evidence("projects", "task", "1", "T", "/t"),),
        visibility_scope=tuple(scope), severity_hint="now")


@pytest.mark.asyncio
async def test_r8_a_group_member_gets_the_finding_and_others_get_nothing(world):
    from gateway.routes.shell.finding_contract import deliver

    w = world
    f = _finding([f"group:{w.slug}"])
    carol = await _groups(w, w.p.org_a, w.m.carol)
    dave = await _groups(w, w.p.org_a, w.m.dave)
    assert f"group:{w.slug}" in carol and dave == []
    assert deliver([f], w.m.carol, carol) == [f]
    assert deliver([f], w.m.dave, dave) == []


@pytest.mark.asyncio
async def test_r8_a_disabled_member_is_not_in_the_group(world):
    from gateway.routes.shell.finding_contract import deliver

    w = world
    groups = await _groups(w, w.p.org_a, w.m.gone)
    assert groups == []
    assert deliver([_finding([f"group:{w.slug}"])], w.m.gone, groups) == []


@pytest.mark.asyncio
async def test_r8_another_tenant_member_gets_nothing_even_for_the_group_slug(world):
    from gateway.routes.shell.finding_contract import deliver

    w = world
    zed = await _groups(w, w.p.org_b, w.m.zed)
    assert zed == []
    assert deliver([_finding([f"group:{w.slug}"])], w.m.zed, zed) == []
    # Zed under the tenant of org A cannot read the org A groups as carol either.
    assert await _groups(w, w.p.org_b, w.m.carol) == []


@pytest.mark.asyncio
async def test_r8_a_named_member_outside_the_scope_gets_nothing(world):
    from gateway.routes.shell.finding_contract import deliver

    w = world
    f = _finding([w.m.dave])
    carol = await _groups(w, w.p.org_a, w.m.carol)
    assert deliver([f], w.m.carol, carol) == []
    assert deliver([f], w.m.dave, []) == [f]
