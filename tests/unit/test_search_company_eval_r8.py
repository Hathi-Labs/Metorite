"""R8 — WS-KG KG-1, the retrieval eval of ``GET /search/company`` on a REAL Postgres.

The 50 questions of the synthetic set run through the whole route (FastAPI, the
five sources, row level security) as the non-privileged role of the phase-4
rehearsal. The seed is ``evals/search_company/seed.py`` and the runner is
``evals/search_company/run.py``, the code that ``python -m evals.search_company
--fixture`` runs. So this suite and the command line cannot drift.

* The baseline: recall at 5 is 1.00, no source is empty, no source is skipped.
* A source that returns nothing for its 10 questions FAILS the run. Two causes
  are shown: a closed switch (CRM) and an empty index (Knowledge). A run that
  reads green with a dead source is the failure this eval exists to catch.
* A member of another org asks the same words and finds none of the gold ids.

⚠️ It SKIPS without ``TENANT_LADDER_DATABASE_URL``, and a skip is not a pass.
"""

from __future__ import annotations

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import text

from evals.search_company import run, score, seed
from evals.search_company.questions import SOURCES, load_fixture, resolve
from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_email_keep_separate import _assert_non_priv
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

# Without the gate the suite ERRORS where no ladder URL is set (deploy.yml).
pytestmark = _DB_GATE


@pytest.fixture(scope="module")
def seeded(promoted):  # noqa: F811
    fixture = load_fixture()
    with promoted.admin_engine.begin() as c:
        for sql, rows in seed.statements(promoted.org_a, fixture):
            c.execute(text(sql), rows)
    yield {"ns": promoted, "fixture": fixture}


async def _run(p, org: str, questions) -> list[score.Outcome]:
    url = p.app_url.render_as_string(hide_password=False)
    async with tenant_engine_scope(url):
        with run.switches(p.org_a):
            async with run.inprocess_asker(run.member_of(org)) as ask:
                return await run.run_set(ask, questions)


async def test_baseline_every_source_answers_its_ten_questions(seeded, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    p = seeded["ns"]
    questions = resolve(seeded["fixture"], p.org_a)
    assert len(questions) == 50
    summary = score.summarize(await _run(p, p.org_a, questions), run.registry_names())
    assert summary.errors == 0
    assert summary.skipped == (), f"a source failed or ran out of time: {summary.skipped}"
    assert summary.failing == ()
    assert summary.recall == 1.0, f"missed: {summary.missed}"
    assert summary.by_source == {s: (10, 10) for s in SOURCES}
    # Only the two sources without questions are empty. Whatsapp and artifacts hold no data.
    assert set(summary.empty) == {"artifacts", "whatsapp"}
    assert score.verdict(summary) == score.EXIT_PASS


async def test_a_closed_switch_fails_the_run(seeded, app_engine, monkeypatch) -> None:  # noqa: F811
    """CRM answers nothing when the org is off ``CRM_ORGS``. Its ten questions fail."""
    from acb_common import get_settings

    p = seeded["ns"]
    url = p.app_url.render_as_string(hide_password=False)
    questions = resolve(seeded["fixture"], p.org_a)
    async with tenant_engine_scope(url):
        with run.switches(p.org_a):
            object.__setattr__(get_settings(), "crm_orgs", "")
            async with run.inprocess_asker(run.member_of(p.org_a)) as ask:
                outcomes = await run.run_set(ask, questions)
    summary = score.summarize(outcomes, run.registry_names())
    assert summary.failing == ("crm",)
    assert summary.by_source["crm"] == (0, 10)
    assert summary.recall == pytest.approx(0.8)
    assert score.verdict(summary) == score.EXIT_FAIL


async def test_an_empty_index_fails_the_run(seeded, app_engine) -> None:  # noqa: F811
    """Knowledge with no chunk answers nothing, and the run says so. The chunks come back after."""
    p = seeded["ns"]
    questions = resolve(seeded["fixture"], p.org_a)
    chunk_sql = [(s, r) for s, r in seed.statements(p.org_a, seeded["fixture"])
                 if s.startswith("INSERT INTO kb_chunks")]
    with p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM kb_chunks WHERE organization_id = CAST(:o AS uuid)"),
                  {"o": p.org_a})
    try:
        summary = score.summarize(await _run(p, p.org_a, questions), run.registry_names())
    finally:
        with p.admin_engine.begin() as c:
            for sql, rows in chunk_sql:
                c.execute(text(sql), rows)
    assert summary.failing == ("knowledge",)
    assert "knowledge" in summary.empty
    assert score.verdict(summary) == score.EXIT_FAIL


async def test_a_member_of_another_org_finds_none_of_the_gold(seeded, app_engine) -> None:  # noqa: F811
    p = seeded["ns"]
    questions = resolve(seeded["fixture"], p.org_a)
    summary = score.summarize(await _run(p, p.org_b, questions), run.registry_names())
    assert summary.recall == 0.0
    assert summary.merged == 0.0
