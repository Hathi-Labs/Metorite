"""R8 — the founder questions agree with the real routes (WS-53 CRM-Q30).

Spec: ``project-docs/specs/crm_platform.md`` §18.11.2, CRM-Q30 item 11.

Against a REAL Postgres, through the real ``/crm`` router, as the
non-privileged role of the phase-4 rehearsal. The dataset of
``evals/crm_founder_questions`` goes into org A as the superuser. Then each
scripted tool call of each question runs through the REAL tool of
``crm_tools.py``, and its GET goes to the real route. Each fact that Python
computed must equal the answer of the route that its selector names.

So the ground truth of the eval and the route are the same numbers. A
question where they differ is a real bug of the route or of the fact, and
the test names it.

The test also holds ``recorded_routes.json`` to the routes: the scripted run
replays that file, so it must be what the routes answer. Set
``CRM_FOUNDER_RECORD=1`` to write the file again after a change of the
dataset, then run the test once more without it.

Only Q30, the note search, has no route here, because CRM-Q27 needs the
embed door. Every other question runs.
"""

# ruff: noqa: F811 - the fixtures come in by import and are used as arguments
from __future__ import annotations

import json
import os
from typing import Any
from urllib.parse import urlencode

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import text

from tests.unit.test_crm_aggregate_fields_r8 import _admin, _call
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

# Without the gate the suite ERRORS where no ladder URL is set (deploy.yml).
pytestmark = _DB_GATE


@pytest.fixture
def seeded(promoted, monkeypatch):
    from evals.crm_founder_questions.seed import TABLES, statements
    from tests.unit._crm_fakes import crm_switch_on

    crm_switch_on(monkeypatch, "*")
    with promoted.admin_engine.begin() as c:
        for sql, rows in statements(promoted.org_a):
            c.execute(text(sql), rows)
    yield promoted
    with promoted.admin_engine.begin() as c:
        for org in (promoted.org_a, promoted.org_b):
            for table in TABLES:
                c.execute(text(
                    f"DELETE FROM {table} WHERE organization_id = CAST(:o AS uuid)"),
                    {"o": org})


def _route(p: Any, org: str) -> Any:
    """The GET of the tools, sent to the real router as an admin of *org*."""
    async def answer(path: str, params: dict[str, str]) -> Any:
        query = f"{path}?{urlencode(params)}" if params else path
        res = await _call(p, _admin(org), "GET", query)
        assert res.status_code == 200, (query, res.status_code, res.text[:500])
        return res.json()
    return answer


async def _run_all(p: Any) -> tuple[dict[str, Any], dict[str, list[str]]]:
    """Every question with a route: the route answers by key, and the
    problems of the agreement by question."""
    from evals.crm_founder_questions import checkers, run
    from evals.crm_founder_questions.questions import questions

    answers = run.load_answers()
    seen: dict[str, Any] = {}
    problems: dict[str, list[str]] = {}
    for q in questions():
        if not q.route_check:
            continue
        calls, _texts = await run.run_tools(answers[q.id], _route(p, p.org_a))
        for c in calls:
            seen[run.route_key(c.path, c.params)] = c.data
        found = checkers.check_agree(q, [c.data for c in calls])
        found += checkers.check_tools(q.needs, calls)
        if found:
            problems[q.id] = found
    return seen, problems


async def test_every_fact_agrees_with_the_real_route(seeded) -> None:
    from evals.crm_founder_questions import run

    seen, problems = await _run_all(seeded)
    assert problems == {}, json.dumps(problems, indent=2)

    if os.environ.get("CRM_FOUNDER_RECORD") == "1":
        run.ROUTES.write_text(json.dumps({
            "_about": "WS-53 CRM-Q30. The answers of the real routes on the seeded dataset, "
                      "recorded by tests/unit/test_crm_founder_questions_r8.py with "
                      "CRM_FOUNDER_RECORD=1. --scripted replays them. Do not edit by hand.",
            "routes": dict(sorted(seen.items())),
        }, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        pytest.skip("recorded_routes.json written. Run again without CRM_FOUNDER_RECORD.")
    assert run.load_routes() == seen, (
        "recorded_routes.json differs from the routes. Record it again (README).")


async def test_the_route_reads_the_crm_zone_of_the_profile(seeded) -> None:
    """The months of Q04 and Q05 count in Asia/Kolkata, and the dataset has
    rows whose UTC month is another month, so the zone changes a number."""
    from datetime import UTC

    from evals.crm_founder_questions.dataset import IST, build

    body = await _route(seeded, seeded.org_a)("/crm/aggregate", {
        "entity": "module:Invoices", "measure": "count", "group_by": "created_month"})
    assert body["zone"] == "Asia/Kolkata"
    edge = [r for r in build().rows("Invoices")
            if r.created.astimezone(IST).month != r.created.astimezone(UTC).month]
    assert edge, "the dataset must hold invoices created just after midnight on a 1st"
    by_month = {g["key"]: g["value"] for g in body["groups"]}
    month = f"{edge[0].created.astimezone(IST):%Y-%m}"
    assert by_month.get(month), (month, by_month)


async def test_org_b_reads_none_of_the_dataset(seeded) -> None:
    body = await _route(seeded, seeded.org_b)("/crm/modules", {})
    assert body == []
