"""``GET /studio/actions`` on a real database (STU-B1, R8).

Spec: ``project-docs/specs/ai_studio.md`` §3.2 and §14 B1. Code:
``gateway/routes/studio/actions.py``.

The real handler runs on a phase-4 promoted scratch database, as the
non-privileged app role, so FORCE RLS binds. Org A and org B each hold
workflows. The route takes no org in its SQL, so the wall is the bound
session and nothing else.

Run (one R8 suite at a time on the shared scratch database)::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_studio_catalog_r8.py -v -rs -p no:xdist

Without the database every test here SKIPS. A skip is not a pass.

**Acceptance (STU-B1).**

a. Org A sees its own published workflows, and never org B's.
b. Org B sees its own, and never org A's.
c. A draft and a disabled workflow never appear, in either org.
d. An unbound session sees no workflow at all (the role cannot bypass RLS).

**The mutation (R7).** In ``actions._workflow_rows``, read with a session
that is not bound to the tenant (``get_session_factory()()`` in place of
``_tenant_session()``). ``test_r8_an_org_sees_its_own_published_workflows``
goes red, because the app role then reads no row.

A second mutation: read with a session that bypasses RLS (a superuser or
BYPASSRLS engine). Both ``test_r8_an_org_sees_its_own_published_workflows``
and ``test_r8_an_org_never_sees_another_orgs_workflow`` go red. The
independent verifier ran it on 2026-10-31 and got ``2 failed, 3 passed``.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth import UserContext, UserRole, build_access
from acb_common import get_settings
from acb_common.db import bind_tenant, release_tenant
from gateway.routes.studio import actions
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

TAG = uuid.uuid4().hex[:8]


@pytest.fixture
def world(promoted, monkeypatch):  # noqa: F811
    """Org A holds two published, one draft and one disabled workflow. Org B
    holds one published and one draft. The flag is open for every org."""
    monkeypatch.setattr(get_settings(), "studio_orgs", "*")
    seeded: dict[tuple[str, str], str] = {}
    with promoted.admin_engine.begin() as conn:
        for tag, org, kinds in (
            ("A", promoted.org_a, ("published", "published", "draft", "disabled")),
            ("B", promoted.org_b, ("published", "draft")),
        ):
            for i, status in enumerate(kinds):
                wid = str(uuid.uuid4())
                seeded[(tag, f"{status}{i}")] = wid
                conn.execute(text(
                    "INSERT INTO workflows (id, name, owner_email, status, "
                    "organization_id) VALUES (:id, :n, :o, :s, :org)"),
                    {"id": wid, "n": f"{tag} {status} {i} {TAG}",
                     "o": f"owner@{tag.lower()}.stu.test", "s": status, "org": org})
    promoted.seeded = seeded
    yield promoted
    with promoted.admin_engine.begin() as conn:
        conn.execute(text("DELETE FROM workflows WHERE name LIKE :p"),
                     {"p": f"% {TAG}"})


def _user(org: str) -> UserContext:
    return UserContext(
        email="m@example.test", role=UserRole.EMPLOYEE,
        access=build_access(["feature:workflows"]), organization_id=org)


def _as(world, org: str):
    """Call the real handler on the app-role engine with *org* bound."""

    async def _go():
        dsn = world.app_url.render_as_string(hide_password=False)
        async with tenant_engine_scope(dsn):
            token = bind_tenant(org)
            try:
                return await actions.list_actions(_user(org))
            finally:
                release_tenant(token)

    return asyncio.run(_go())


def _workflow_ids(body: dict) -> set[str]:
    return {r["id"].removeprefix("workflow:")
            for r in body["actions"] if r["source"] == "workflow"}


def _published(world, tag: str) -> set[str]:
    return {wid for (t, kind), wid in world.seeded.items()
            if t == tag and kind.startswith("published")}


def test_the_admin_sees_both_orgs_rows(world):
    """Precondition: both orgs hold rows, so a wall is what hides them."""
    with world.admin_engine.connect() as c:
        n = c.execute(text(
            "SELECT count(*) FROM workflows WHERE name LIKE :p"),
            {"p": f"% {TAG}"}).scalar()
    assert n == 6


def test_r8_an_org_sees_its_own_published_workflows(world):
    assert _workflow_ids(_as(world, world.org_a)) == _published(world, "A")
    assert _workflow_ids(_as(world, world.org_b)) == _published(world, "B")


def test_r8_an_org_never_sees_another_orgs_workflow(world):
    a = _workflow_ids(_as(world, world.org_a))
    b = _workflow_ids(_as(world, world.org_b))
    assert not a & set(world.seeded.values()) - _published(world, "A")
    assert not b & set(world.seeded.values()) - _published(world, "B")
    assert not a & b


def test_r8_a_draft_and_a_disabled_workflow_never_appear(world):
    unpublished = {wid for (_, kind), wid in world.seeded.items()
                   if not kind.startswith("published")}
    for org in (world.org_a, world.org_b):
        assert not _workflow_ids(_as(world, org)) & unpublished


def test_r8_an_unbound_session_sees_no_workflow(world):
    async def _go():
        dsn = world.app_url.render_as_string(hide_password=False)
        async with tenant_engine_scope(dsn):
            from acb_common.db import get_session_factory

            async with get_session_factory()() as db:
                return (await db.execute(text(
                    "SELECT count(*) FROM workflows WHERE name LIKE :p"),
                    {"p": f"% {TAG}"})).scalar()

    assert asyncio.run(_go()) == 0
