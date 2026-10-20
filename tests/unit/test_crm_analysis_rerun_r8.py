"""R8 — re-run the AI analysis on demand, on a real Postgres (WS-53
CRM-Q32-10, ``crm_platform.md`` §18.14.14).

Each request runs as the NON-privileged role of the phase-4 rehearsal, so
RLS binds each read and each write. The ``decide`` facade is the scripted
fake of ``test_crm_map_pipeline_r8.py``: no real model call.

What this proves:

* the route starts the real mapping pipeline for the org of the caller, and
  the pipeline writes its draft for that org;
* it writes nothing for another org, also when that org has the same empty
  slot and the same module;
* a member gets 403, and an org where no step can run gets 404.
"""

# ruff: noqa: F811 - the fixtures come in by import and are used as arguments
from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from tests.unit.test_crm_field_rules_r8 import (  # noqa: F401
    _admin_of,
    _imported,
    _member_of,
    _req,
    fr,
)
from tests.unit.test_crm_map_pipeline_r8 import (  # noqa: F401
    _cursor,
    _no_deals,
    _seed_module,
    _versions,
    mp_env,
)
from tests.unit.test_crm_sync_engine_r8 import ns  # noqa: F401
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

# ⚠️ Every test here needs the real database. Without this gate the suite
# ERRORS where no ladder URL is set (the deploy run), and the deploy stops.
pytestmark = _DB_GATE

RERUN = "/crm/analysis/rerun"
STATUS = "/crm/analysis/status"


@pytest.fixture
def fresh_scheduler():
    from gateway.crm_sync import scheduler

    def _reset() -> None:
        scheduler._stopping.clear()
        for state in (scheduler._analyses, scheduler._map_tasks,
                      scheduler._question_tasks, scheduler._label_tasks):
            state.clear()
        for orgs in (scheduler._mapping, scheduler._questioning, scheduler._labeling):
            orgs.clear()

    _reset()
    yield scheduler
    _reset()


def _roles(versions: list[tuple[Any, ...]]) -> list[tuple[str, str, str]]:
    """The ``role`` items of each version: (state, slot, module)."""
    return [(str(v[1]), str(i.get("slot")), str(i.get("module")))
            for v in versions for i in (v[4] or []) if i.get("kind") == "role"]


async def test_the_route_runs_the_pipeline_for_the_callers_org_only(
    mp_env, fresh_scheduler,
) -> None:
    p, a, b = mp_env
    for org, zoho in ((p.org_a, a), (p.org_b, b)):
        await _imported(p, org, zoho)
        _no_deals(p, org)
        _seed_module(p, org, "Commercials", "Commercials")
    before_b = (_versions(p, p.org_b), _cursor(p, p.org_b))
    assert _roles(_versions(p, p.org_a)) == [], "no draft before the press"

    member = await _req(_member_of(p.org_a), "POST", RERUN)
    assert member.status_code == 403, member.text

    got = await _req(_admin_of(p.org_a), "POST", RERUN)
    assert got.status_code == 202, got.text
    assert got.json()["started"] is True
    run = fresh_scheduler.analysis_of(p.org_a)
    assert run is not None and run.task is not None
    await run.task
    assert run.outcome == "done", run

    # The pipeline of org A ran, and wrote its draft for org A.
    assert ("draft", "deal", "Commercials") in _roles(_versions(p, p.org_a))
    assert _cursor(p, p.org_a) is not None

    status = await _req(_admin_of(p.org_a), "GET", STATUS)
    assert status.status_code == 200, status.text
    assert status.json()["run"]["id"] == got.json()["id"]
    assert status.json()["run"]["state"] == "finished"

    # Org B has the same empty slot and module, and nothing ran for it.
    assert (_versions(p, p.org_b), _cursor(p, p.org_b)) == before_b
    assert fresh_scheduler.analysis_of(p.org_b) is None
    other = await _req(_admin_of(p.org_b), "GET", STATUS)
    assert other.json() == {"run": None, "allowed": False}, (
        "an admin of org B sees no analysis of A")

    # Org B is not on the decide list, so no step can run for it.
    off = await _req(_admin_of(p.org_b), "POST", RERUN)
    assert off.status_code == 404, off.text
    assert off.json()["detail"]["code"] == "analysis_off"
    assert (_versions(p, p.org_b), _cursor(p, p.org_b)) == before_b
