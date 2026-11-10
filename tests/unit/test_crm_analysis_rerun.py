"""Re-run the AI analysis on demand (WS-53 CRM-Q32-10).

Spec: ``project-docs/specs/crm_platform.md`` §18.14.14. Hermetic: the
``/crm`` router is mounted on a bare FastAPI app, and the steps are fakes.
``tests/unit/test_crm_analysis_rerun_r8.py`` runs the real pipeline on a real
Postgres.

What this file pins:

1. Only an admin (``admin:access:manage``) may start or read an analysis.
2. One analysis at a time for each org. A second press returns the running one.
3. A step that a sync run started is waited for, and not started twice.
4. No step can run: 404 ``analysis_off``.
5. A cap of the pipeline reads as the outcome ``limit``.
6. The org comes from the session.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

pytest.importorskip("gateway.routes.crm", reason="gateway not installed")

from acb_auth import UserContext, UserRole, build_access, get_current_user
from acb_common import get_settings
from fastapi import FastAPI
from gateway.crm_sync import map_pipeline, note_labels, scheduler
from gateway.routes import crm as crm_package
from gateway.routes.crm import access as crm_access
from gateway.routes.crm import analysis, flags
from gateway.routes.crm import questions as q_routes

from tests.unit._crm_fakes import crm_switch_on

ORG_A = "0b0e8c1e-5c7a-4f6e-9a35-c3d0c0de0a01"
ORG_B = "0b0e8c1e-5c7a-4f6e-9a35-c3d0c0de0a02"
RERUN = "/crm/analysis/rerun"
STATUS = "/crm/analysis/status"


@asynccontextmanager
async def _no_session(*_a: Any, **_k: Any):
    yield SimpleNamespace()


def _user(org: str = ORG_A, *perms: str) -> UserContext:
    return UserContext(
        email=f"admin@{org[-4:]}.test", role=UserRole.EMPLOYEE,
        access=build_access(list(perms or ("feature:crm", "admin:access:manage"))),
        organization_id=org,
    )


def _member(org: str = ORG_A) -> UserContext:
    return _user(org, "feature:crm")


async def _call(user: UserContext, method: str, path: str) -> httpx.Response:
    app = FastAPI()
    app.include_router(crm_package.router)
    app.dependency_overrides[get_current_user] = lambda: user
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://gw") as client:
        return await client.request(method, path)


class Steps:
    """The fakes of the steps, with a gate that holds the mapping open."""

    def __init__(self) -> None:
        self.gate = asyncio.Event()
        self.mapped: list[str] = []
        self.asked: list[str] = []
        self.reason = "done"

    async def map_after_run(self, org: str, *, should_stop: Any = None) -> Any:
        self.mapped.append(org)
        await self.gate.wait()
        return map_pipeline.Mapped(self.reason)

    async def questions_after_run(self, org: str, *, now: Any = None) -> int:
        self.asked.append(org)
        return 3


@pytest.fixture
def steps(monkeypatch: pytest.MonkeyPatch) -> Steps:
    crm_switch_on(monkeypatch, "*")
    settings = get_settings()
    monkeypatch.setattr(settings, "crm_mirror", True, raising=False)
    monkeypatch.setattr(settings, "crm_mirror_orgs", "*", raising=False)

    async def _everyone(db: Any, org: str) -> Any:
        return SimpleNamespace(access="everyone", access_groups=[])

    async def _mirror(db: Any, org: str) -> None:
        return None

    monkeypatch.setattr(crm_access, "_tenant_session", _no_session)
    monkeypatch.setattr(crm_access, "read_settings", _everyone)
    monkeypatch.setattr(analysis, "_tenant_session", _no_session)
    monkeypatch.setattr(analysis, "_require_mirror", _mirror)
    fake = Steps()
    monkeypatch.setattr(map_pipeline, "map_after_run", fake.map_after_run)
    monkeypatch.setattr(map_pipeline, "enabled_for", lambda org: True)
    monkeypatch.setattr(note_labels, "enabled_for", lambda org: False)
    monkeypatch.setattr(flags, "questions_allowed", lambda org: True)
    monkeypatch.setattr(analysis, "questions_allowed", lambda org: True)
    monkeypatch.setattr(q_routes, "questions_after_run", fake.questions_after_run)
    _reset()
    yield fake
    fake.gate.set()
    _reset()


def _reset() -> None:
    scheduler._stopping.clear()
    for state in (scheduler._analyses, scheduler._map_tasks, scheduler._question_tasks,
                  scheduler._label_tasks):
        state.clear()
    for orgs in (scheduler._mapping, scheduler._questioning, scheduler._labeling):
        orgs.clear()


async def _finish(fake: Steps, org: str = ORG_A) -> scheduler.Analysis:
    fake.gate.set()
    run = scheduler.analysis_of(org)
    assert run is not None and run.task is not None
    await run.task
    return run


# ── 1. Admin only ────────────────────────────────────────────────────────────


async def test_1_a_member_may_not_start_or_read_an_analysis(steps: Steps) -> None:
    assert (await _call(_member(), "POST", RERUN)).status_code == 403
    assert (await _call(_member(), "GET", STATUS)).status_code == 403
    assert steps.mapped == [], "a refused press starts nothing"
    assert scheduler.analysis_of(ORG_A) is None


# ── 2. One at a time ─────────────────────────────────────────────────────────


async def test_2_a_second_press_returns_the_running_analysis(steps: Steps) -> None:
    first = await _call(_user(), "POST", RERUN)
    assert first.status_code == 202, first.text
    body = first.json()
    assert body["started"] is True and body["state"] == "running"
    await asyncio.sleep(0)

    again = await _call(_user(), "POST", RERUN)
    assert again.status_code == 202, again.text
    assert again.json()["started"] is False
    assert again.json()["id"] == body["id"]

    status = (await _call(_user(), "GET", STATUS)).json()["run"]
    assert status["id"] == body["id"] and status["state"] == "running"

    run = await _finish(steps)
    assert steps.mapped == [ORG_A], "one mapping run, not two"
    assert steps.asked == [ORG_A], "the questions rebuild after the mapping"
    assert run.outcome == "done"
    done = (await _call(_user(), "GET", STATUS)).json()["run"]
    assert done["state"] == "finished" and done["outcome"] == "done"
    assert done["steps"] == {"mapping": "done", "labels": "skipped", "questions": "done"}

    # After the end, a press starts a new analysis.
    steps.gate = asyncio.Event()
    third = await _call(_user(), "POST", RERUN)
    assert third.json()["started"] is True and third.json()["id"] != body["id"]
    await _finish(steps)


async def test_2_status_is_null_before_any_analysis(steps: Steps) -> None:
    got = await _call(_user(), "GET", STATUS)
    assert got.status_code == 200, got.text
    assert got.json() == {"run": None, "allowed": True}


# ── 3. A step that a sync run started ────────────────────────────────────────


async def test_3_a_running_sync_step_is_waited_for_and_not_started_twice(steps: Steps) -> None:
    sync_task = scheduler.start_mapping(ORG_A)
    assert sync_task is not None
    await asyncio.sleep(0)
    got = await _call(_user(), "POST", RERUN)
    assert got.status_code == 202, got.text
    await asyncio.sleep(0)
    run = scheduler.analysis_of(ORG_A)
    assert run is not None and run.running, "it waits for the sync's mapping"
    run = await _finish(steps)
    assert steps.mapped == [ORG_A], "the mapping of the sync run was not started again"
    assert run.steps["mapping"] == "done"


# ── 4. Off ───────────────────────────────────────────────────────────────────


async def test_4_no_step_can_run_gives_404_analysis_off(
    steps: Steps, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(map_pipeline, "enabled_for", lambda org: False)
    monkeypatch.setattr(analysis, "questions_allowed", lambda org: False)
    got = await _call(_user(), "POST", RERUN)
    assert got.status_code == 404, got.text
    assert got.json()["detail"]["code"] == analysis.ANALYSIS_OFF
    assert scheduler.analysis_of(ORG_A) is None
    status = await _call(_user(), "GET", STATUS)
    assert status.json() == {"run": None, "allowed": False}, "the screen hides the button"


# ── 5. A cap ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("reason", sorted(scheduler.LIMIT_REASONS))
async def test_5_a_cap_reads_as_limit(steps: Steps, reason: str) -> None:
    steps.reason = reason
    assert (await _call(_user(), "POST", RERUN)).status_code == 202
    run = await _finish(steps)
    assert run.outcome == "limit"


async def test_5_a_failed_mapping_reads_as_failed(steps: Steps) -> None:
    steps.reason = "failed"
    assert (await _call(_user(), "POST", RERUN)).status_code == 202
    assert (await _finish(steps)).outcome == "failed"


# ── 6. The org of the session ────────────────────────────────────────────────


async def test_6_the_analysis_runs_for_the_org_of_the_session(steps: Steps) -> None:
    assert (await _call(_user(ORG_B), "POST", RERUN)).status_code == 202
    await _finish(steps, ORG_B)
    assert steps.mapped == [ORG_B]
    assert scheduler.analysis_of(ORG_A) is None
    other = (await _call(_user(ORG_A), "GET", STATUS)).json()
    assert other["run"] is None, "an admin of another org sees no analysis"
