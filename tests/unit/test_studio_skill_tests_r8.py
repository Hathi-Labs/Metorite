"""WS-STU F4b: the skill read of a test run on a real database (R8).

Spec: ``project-docs/specs/ai_studio_rows/F4.md``. Code:
``gateway/studio/skill_tests.py`` ``_load_skill`` and ``run_suite``, over
``gateway/studio/skills_store.py``.

The store runs on a phase-4 promoted scratch database, as the non-privileged
app role, so FORCE RLS binds. Two organizations, two members. The runner and
the meter are fakes, so no model call and no credit read happen.

Run (a PRIVATE ladder database, one R8 suite at a time, because the
``promoted`` fixture drops ``<db>_h3rls``)::

    export TENANT_LADDER_DATABASE_URL=<a private database of your own>
    uv run pytest tests/unit/test_studio_skill_tests_r8.py -v -rs

Without the database every test here SKIPS, and the run reads green. A skip
is not a pass.

**Acceptance (F4b).**

a. The owner reads the skill and its version, and a run of it starts.
b. Org B reads no version of org A's skill, and its run raises LookupError
   with no runner call. That holds for a member of org B with the same email.
c. Member 2 of org A reads no version of member 1's skill, and its run
   raises LookupError with no runner call.

**The mutations (R7).**

* M1. In ``skill_tests._load_skill``, open the session on the bound tenant
  of the caller's frame (``_tenant_session()``) and not on *org*. The run
  of org B then reads with no bound org, and
  ``test_r8_org_b_reads_no_version`` goes red. Run on 2026-11-19: red.

The walls of the org and of the member are the store's filters and FORCE
RLS. ``test_studio_skills_routes_r8.py`` owns them. This suite proves that
the run reads through them.
"""
from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_common import get_settings
from acb_common.db import tenant_session
from gateway.studio import skill_tests as st
from gateway.studio import skills_store as store
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)
from tests.unit.test_skill_tests import install_slot_redis

pytestmark = _DB_GATE

TAG = uuid.uuid4().hex[:8]
ANA = f"ana.{TAG}@stuf4b.test"
BEN = f"ben.{TAG}@stuf4b.test"
_LIKE = f"%.{TAG}@stuf4b.test"
BODY = {"description": "Use it for leads.", "instructions": "Add the lead.",
        "actions": ["object:lead.create"],
        "tests": [{"id": "t1", "input": "Add Ravi", "expect": []}]}


def _dsn(url: Any) -> str:
    return url.render_as_string(hide_password=False)


@pytest.fixture
def world(promoted, monkeypatch):  # noqa: F811
    monkeypatch.setattr(get_settings(), "studio_skill_tests_cap_credits", "5", raising=False)
    install_slot_redis(monkeypatch)  # the slot of a suite, in memory
    yield SimpleNamespace(p=promoted)
    with promoted.admin_engine.begin() as c:
        c.execute(text("DELETE FROM studio_skills WHERE owner_email LIKE :t"), {"t": _LIKE})


def _go(world: Any, fn: Any) -> Any:
    async def go() -> Any:
        async with tenant_engine_scope(_dsn(world.p.app_url)):
            return await fn()
    return asyncio.run(go())


async def _make(org: str, member: str) -> str:
    async with tenant_session(org) as db:
        skill = await store.create_skill(db, member, name="Leads", slug=f"leads-{TAG}")
        await store.add_version(db, member, skill.id, BODY)
    return skill.id


class _Runner:
    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, message: str, **_kw: Any) -> None:
        self.calls += 1


async def _meter(org: str, member: str, run_id: str) -> Decimal:
    return Decimal(0)


def test_r8_the_owner_reads_the_skill_and_its_version(world) -> None:
    """a."""
    a = world.p.org_a

    async def go() -> Any:
        sid = await _make(a, ANA)
        skill, version = await st._load_skill(a, ANA, sid, None)
        runner = _Runner()
        result = await st.run_suite(org=a, member=ANA, skill_id=sid, runner=runner, meter=_meter)
        return sid, skill, version, runner.calls, result

    sid, skill, version, calls, result = _go(world, go)
    assert skill is not None and skill.id == sid and version is not None
    assert version.version == 1 and version.body["actions"] == ["object:lead.create"]
    # The fake runner loads and arms nothing, so the run fails closed.
    assert calls == 1 and result.results[0].reason == "skill_unloaded"


def test_r8_org_b_reads_no_version(world) -> None:
    """b. Also a member of org B with the same email."""
    a, b = world.p.org_a, world.p.org_b

    async def go() -> Any:
        sid = await _make(a, ANA)
        found = await st._load_skill(b, ANA, sid, None)
        runner = _Runner()
        with pytest.raises(LookupError):
            await st.run_suite(org=b, member=ANA, skill_id=sid, runner=runner, meter=_meter)
        return found, runner.calls

    found, calls = _go(world, go)
    assert found == (None, None) and calls == 0


def test_r8_another_member_reads_no_version(world) -> None:
    """c."""
    a = world.p.org_a

    async def go() -> Any:
        sid = await _make(a, ANA)
        found = await st._load_skill(a, BEN, sid, None)
        pinned = await st._load_skill(a, BEN, sid, 1)
        runner = _Runner()
        with pytest.raises(LookupError):
            await st.run_suite(org=a, member=BEN, skill_id=sid, version=1, runner=runner,
                               meter=_meter)
        return found, pinned, runner.calls

    found, pinned, calls = _go(world, go)
    assert found == (None, None) and pinned == (None, None) and calls == 0
