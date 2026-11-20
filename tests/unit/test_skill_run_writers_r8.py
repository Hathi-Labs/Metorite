"""WS-TOF TOF-3a F12b: the two writers of skill runs on a real database (R8).

Spec: ``project-docs/specs/ai_studio_rows/F12.md``, "The writers (F12b)".
Code: the live writer of ``orchestrator/_tool_injection.py``
(``_write_skill_runs``, fed by the pack tool guard) and the test writer of
``gateway/routes/studio/skills.py`` (``_record_test_failures``). The store
is ``gateway/studio/skill_runs.py`` (F12a). The hermetic half is
``tests/unit/test_skill_repair.py`` (``TestTheWriters``).

The writers run on a phase-4 promoted scratch database, as the NOBYPASSRLS
app role, so FORCE RLS binds. Run it on a PRIVATE ladder database, one R8
suite at a time::

    eval "$(bash scripts/dev_db.sh --export)"
    export TENANT_LADDER_DATABASE_URL="${TENANT_LADDER_DATABASE_URL%/*}/<private db>"
    uv run pytest tests/unit/test_skill_run_writers_r8.py -q -rs

Without the database every test here SKIPS, and the run reads green. A skip
is not a pass.

**What this proves.** A live failure and a test failure land in the store
under the bound org, with the member of the run and no word of the input.
Org B reads none of them. With the flag off, neither writer writes a row.

**The mutations (R7).** Drop the org check of ``_write_skill_runs``, and
``test_r8_a_live_failure_lands_under_the_bound_org`` goes red on the write
of org B. Drop the flag check of ``_record_test_failures``, and
``test_r8_flag_off_writes_no_row`` goes red.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("agent_framework", reason="MAF not installed")

from acb_common import get_settings
from acb_common.db import bind_tenant, release_tenant, tenant_session
from acb_skills.write_artifact import artifact_context_scope, bind_artifact_context
from gateway.studio import skill_repair, skill_tests
from gateway.studio import skill_runs as sr
from gateway.studio import skills_store as store
from orchestrator import _tool_injection as ti
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

TAG = uuid.uuid4().hex[:8]
ANA = f"ana.{TAG}@f12b.test"
_LIKE = f"%.{TAG}@f12b.test"
WORDS = "Call Asha on 555 0100"
BODY = {"description": "Use this to log a call.", "instructions": "Make a task.",
        "actions": ["tool:create_task"]}
RUN_KEY = "key-r8-run"


@pytest.fixture
def world(promoted, monkeypatch):  # noqa: F811
    p = promoted
    settings = get_settings()
    monkeypatch.setattr(settings, "studio_orgs", "*", raising=False)
    monkeypatch.setattr(settings, "studio_skill_repair_orgs", p.org_a, raising=False)
    monkeypatch.setattr("acb_skills.subagent_contract.annotated_read_only",
                        lambda _name: False)
    monkeypatch.setenv("AGENT_PERMISSION_MODE", "approve_all")
    yield SimpleNamespace(p=p)
    with p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM studio_skills WHERE owner_email LIKE :t"), {"t": _LIKE})


def _dsn(url: Any) -> str:
    return url.render_as_string(hide_password=False)


def _go(world: Any, fn: Any) -> Any:
    async def go() -> Any:
        async with tenant_engine_scope(_dsn(world.p.app_url)):
            return await fn()
    return asyncio.run(go())


async def _skill(org: str) -> str:
    async with tenant_session(org) as db:
        made = await store.create_skill(db, ANA, name="Log a call",
                                        slug=f"log-{uuid.uuid4().hex[:8]}")
        await store.add_version(db, ANA, made.id, dict(BODY))
        return made.id


@contextlib.contextmanager
def _bound(org: str) -> Any:
    token = bind_tenant(org)
    try:
        yield
    finally:
        release_tenant(token)


def _rows(world: Any, org: str) -> list[tuple]:
    async def go() -> list[tuple]:
        async with tenant_session(org) as db:
            return [tuple(r) for r in (await db.execute(text(
                "SELECT skill_id::text, kind, passed, reason, detail, created_by, run_id, "
                "test_id, input_hash, body_key, to_jsonb(r)::text FROM studio_skill_runs r "
                "WHERE created_by LIKE :t ORDER BY kind, reason"), {"t": _LIKE})).all()]
    return _go(world, go)


def _notes(org: str, skill_id: str) -> ti.SkillRunNotes:
    return ti.SkillRunNotes(
        org=org, member=ANA, run_id=f"run-{uuid.uuid4().hex}", thread_id="thread-r8",
        run_key=RUN_KEY, words=WORDS, skills={f"skill:{skill_id}": ti._LoadedSkill(
            skill_id=skill_id, version=1, body_key=skill_repair.body_key(BODY),
            actions={"create_task": "tool:create_task"})})


async def _live_run(org: str, skill_id: str) -> int:
    """One chat run: a call of the skill's tool raises in the pack tool
    guard, then the end of the run writes the rows."""
    from acb_skills.tool_packs import PACK_RUN_KEY

    notes = _notes(org, skill_id)

    async def create_task(title: str = "") -> str:
        raise RuntimeError(WORDS)

    tool = ti._pack_call(create_task, "projects-assistant", False, RUN_KEY, "projects")
    with _bound(org), artifact_context_scope():
        bind_artifact_context(run_id=notes.run_id, session_id="thread-r8",
                              agent_name="orchestrator", member=ANA, no_egress=False,
                              **{PACK_RUN_KEY: RUN_KEY, ti.SKILL_RUN_NOTES_KEY: notes})
        await tool(title=WORDS)
        task = ti.schedule_skill_runs(ti.PackApply(
            builds=(), record=None, run_key=RUN_KEY, skill_notes=notes))
        assert task is not None
        return await task


def _suite(skill_id: str) -> skill_tests.SuiteResult:
    def one(reason: str, passed: bool | None) -> skill_tests.TestResult:
        return skill_tests.TestResult(
            test_id=f"t-{reason}", passed=passed, reason=reason,
            detail="The turn did not hold the write tool:create_task.",
            writes=("tool:create_task",), credits=Decimal("1.25"), run_id=f"r-{reason}")
    return skill_tests.SuiteResult(
        skill_id=skill_id, version=1, content_hash="c" * 64,
        results=(one("passed", True), one("missing_write", False),
                 one("cap_reached", False), one("not_run", None)))


def test_r8_a_live_failure_lands_under_the_bound_org(world, monkeypatch) -> None:
    a, b = world.p.org_a, world.p.org_b
    monkeypatch.setattr(ti, "_run_org_or_none", lambda: a)

    async def go() -> tuple[str, int]:
        sid = await _skill(a)
        return sid, await _live_run(a, sid)

    sid, written = _go(world, go)
    assert written == 1
    [row] = _rows(world, a)
    assert row[:6] == (sid, "live", False, "tool_error", "create_task: RuntimeError", ANA)
    assert row[6].startswith("run-") and row[8] == sr.input_hash(a, WORDS)
    assert row[9] == skill_repair.body_key(BODY)
    for bit in ("Asha", "555", "0100"):
        assert bit not in row[10], bit
    assert _rows(world, b) == [], "org B reads no row of org A"

    # A run bound to org B that names the skill of org A writes nothing.
    settings = get_settings()
    monkeypatch.setattr(settings, "studio_skill_repair_orgs", f"{a},{b}", raising=False)
    monkeypatch.setattr(ti, "_run_org_or_none", lambda: b)

    async def go_b() -> int:
        return await _live_run(b, sid)

    assert _go(world, go_b) == 0
    assert _rows(world, b) == []
    assert len(_rows(world, a)) == 1


def test_r8_a_test_failure_lands_under_the_session_org(world) -> None:
    from gateway.routes.studio import skills as route

    a, b = world.p.org_a, world.p.org_b

    async def go() -> str:
        sid = await _skill(a)
        with _bound(a):
            await route._record_test_failures(a, ANA, sid, _suite(sid), None)
        return sid

    sid = _go(world, go)
    [row] = _rows(world, a)
    assert row[:6] == (sid, "test", False, "missing_write",
                       "The turn did not hold the write tool:create_task.", ANA)
    assert (row[6], row[7], row[8]) == ("r-missing_write", "t-missing_write", None)
    assert row[9] == skill_repair.body_key(BODY)
    assert _rows(world, b) == []


def test_r8_flag_off_writes_no_row(world, monkeypatch) -> None:
    from gateway.routes.studio import skills as route

    a = world.p.org_a
    monkeypatch.setattr(get_settings(), "studio_skill_repair_orgs", "", raising=False)
    monkeypatch.setattr(ti, "_run_org_or_none", lambda: a)

    async def go() -> int:
        sid = await _skill(a)
        written = await _live_run(a, sid)
        with _bound(a):
            await route._record_test_failures(a, ANA, sid, _suite(sid), None)
        return written

    assert _go(world, go) == 0
    assert _rows(world, a) == []
