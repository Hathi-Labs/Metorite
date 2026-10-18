"""WS-55 EA-13d — the card: the PR chip, the hover line and the run pill text.

Spec: ``project-docs/specs/external_agent_access.md`` §12.2 item 2 and row
EA-13d of §15 (EA3-41, EA3-48), acceptance P1 to P5.

**R8**, a real Postgres, through the real ``/projects`` router as the
non-privileged role of the phase-4 rehearsal, on a database with the whole
ladder and two organizations. A hermetic fake agrees with any SQL it is
handed, so the batched queries run here against the real tables:

* **P1** a task with an open and a merged pull request gets the open one, with
  ``more = 1``. The rest of EA3-48: draft counts as open, the newest by
  ``pr_updated_at`` wins, merged beats closed, and a closed one shows alone.
* **P2** a task with no pull request reference gets no ``pr``. An issue, and a
  pull request with no read state, earn no chip.
* **P3** a reference of another organization never reaches the row.
* **P4** ``agent_note`` holds the first line of the newest ``thought``,
  ``response`` or ``elicitation`` of the open run, at most 140 characters.
* **P5** one list read makes one query for the chips, not one for each row.
* **P6** the list route and the task route both carry ``pr``.
* **P7** the run wire names the client: ``client_name`` and ``client_host``.
* **P8** the CSV has a "Pull request" column when the view shows the field.

R7, the mutations. Each one turns a test here red (run 2026-10-18, each after a
commit, because ``git checkout`` restores HEAD):

* **M1** ``_PR_CHIP_SQL`` orders by ``pr_updated_at`` alone, with no state rank
  -> ``test_p1_an_open_pull_request_beats_a_newer_merged_one``.
* **M2** the state rank gives ``draft`` the rank of ``merged``
  -> ``test_p1_draft_counts_as_open_and_the_newest_open_wins``.
* **M3** the state rank swaps ``merged`` and ``closed``
  -> ``test_p1_merged_beats_closed_and_a_closed_one_shows_alone``.
* **M4** ``more`` is the total, not the total less one -> the P1 tests.
* **M5** the ``kind = 'pull_request'`` predicate dropped
  -> ``test_p2_an_issue_and_an_unread_pull_request_earn_no_chip``.
* **M6** the ``state IN (...)`` predicate dropped -> the same test.
* **M7** ``attach_pr_chip`` runs one query for each row
  -> ``test_p5_one_query_for_the_whole_page``.
* **M8** the note takes the OLDEST line (``ORDER BY a.seq ASC``)
  -> ``test_p4_the_note_is_the_first_line_of_the_newest_thought``.
* **M9** the note drops the ``type`` list (an ``action`` or a ``prompt`` counts)
  -> ``test_p4_only_a_thought_a_response_or_a_question_makes_a_note``.
* **M10** the note is not cut to ``AGENT_NOTE_MAX``
  -> ``test_p4_the_note_is_cut_to_140_characters``.
* **M11** ``attach_agent_state`` does not call ``attach_pr_chip``
  -> ``test_p6_the_list_route_and_the_task_route_carry_the_chip``.
* **M12** ``client_name`` reads the host only -> ``test_p7_the_run_wire_names_the_client``.
* **M13** the export does not attach ``pr`` -> ``test_p8_the_csv_has_a_pull_request_column``.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("gateway.routes.projects", reason="gateway not installed")

from tests.unit import _agent_board_r8 as b
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

A_EMAIL = "ea13d-a@pr-chip-r8.test"
B_EMAIL = "ea13d-b@pr-chip-r8.test"


@pytest.fixture(scope="module")
def world(promoted):  # noqa: F811 — a fixture
    ns = SimpleNamespace(p=promoted)
    ns.a = b._principal(A_EMAIL, promoted.org_a)
    ns.b = b._principal(B_EMAIL, promoted.org_b)
    return ns


@pytest.fixture
def w(world, monkeypatch):
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "agent_runs_enabled", True)
    b.clear(world.p)
    # A new project for each test, so a list read stays inside one page.
    world.proj = b.project(world.p, world.p.org_a, A_EMAIL)
    return world


def _task(w: Any) -> str:
    return b.task(w.p, w.p.org_a, A_EMAIL, w.proj)


async def _rows(w: Any, *task_ids: str) -> dict[str, dict]:
    """The list rows of the project, by task id."""
    async with b._session(w.p) as call:
        got = await call(w.a, "GET", f"/projects/tasks?project_id={w.proj['id']}"
                                     "&include_subtree=true&page_size=100")
    assert got.status_code == 200, got.text
    by_id = {r["id"]: r for r in got.json()["rows"]}
    return {t: by_id[t] for t in task_ids}


class TestThePullRequestChip:
    async def test_p1_an_open_pull_request_beats_a_newer_merged_one(self, w):
        t = _task(w)
        b.ref(w.p, w.p.org_a, t, 1, "open", checks="passing",
              summary={"passed": 3, "failed": 0, "pending": 0, "skipped": 0, "total": 3},
              updated="now() - interval '1 day'", title="Open one")
        b.ref(w.p, w.p.org_a, t, 2, "merged", updated="now()", merged="now()")
        pr = (await _rows(w, t))[t]["pr"]
        assert pr["number"] == 1 and pr["state"] == "open"
        assert pr["repo"] == b.REPO and pr["more"] == 1
        assert pr["checks_state"] == "passing"
        assert pr["checks_summary"] == {"passed": 3, "failed": 0, "pending": 0,
                                        "skipped": 0, "total": 3}
        assert pr["url"] == f"https://github.com/{b.REPO}/pull/1"
        assert pr["title"] == "Open one"

    async def test_p1_draft_counts_as_open_and_the_newest_open_wins(self, w):
        t = _task(w)
        b.ref(w.p, w.p.org_a, t, 1, "open", updated="now() - interval '2 hours'")
        b.ref(w.p, w.p.org_a, t, 2, "draft", updated="now() - interval '1 hour'")
        b.ref(w.p, w.p.org_a, t, 3, "merged", updated="now()", merged="now()")
        pr = (await _rows(w, t))[t]["pr"]
        assert (pr["number"], pr["state"], pr["more"]) == (2, "draft", 2)

    async def test_p1_merged_beats_closed_and_a_closed_one_shows_alone(self, w):
        t, only = _task(w), _task(w)
        b.ref(w.p, w.p.org_a, t, 1, "closed", updated="now()")
        b.ref(w.p, w.p.org_a, t, 2, "merged", updated="now() - interval '3 days'",
              merged="now() - interval '3 days'")
        b.ref(w.p, w.p.org_a, only, 9, "closed")
        rows = await _rows(w, t, only)
        assert (rows[t]["pr"]["number"], rows[t]["pr"]["state"], rows[t]["pr"]["more"]) == (
            2, "merged", 1)
        assert rows[t]["pr"]["merged_at"] is not None
        assert (rows[only]["pr"]["state"], rows[only]["pr"]["more"]) == ("closed", 0)

    async def test_p2_a_task_with_no_reference_gets_no_chip(self, w):
        t = _task(w)
        assert (await _rows(w, t))[t]["pr"] is None

    async def test_p2_an_issue_and_an_unread_pull_request_earn_no_chip(self, w):
        t = _task(w)
        b.ref(w.p, w.p.org_a, t, 4, "open", kind="issue")
        b.ref(w.p, w.p.org_a, t, 5, None)
        assert (await _rows(w, t))[t]["pr"] is None

    async def test_p3_a_reference_of_another_organization_never_reaches_the_row(self, w):
        t = _task(w)
        # The row of organization B points at the task of organization A. No
        # key forbids it, so only the row level security keeps it out.
        b.ref(w.p, w.p.org_b, t, 7, "open", updated="now()", title="Not yours")
        b.ref(w.p, w.p.org_a, t, 8, "merged", updated="now() - interval '1 day'",
              merged="now() - interval '1 day'")
        pr = (await _rows(w, t))[t]["pr"]
        assert (pr["number"], pr["state"], pr["more"]) == (8, "merged", 0)
        assert pr["title"] != "Not yours"

    async def test_p5_one_query_for_the_whole_page(self, w):
        from acb_common.db import bind_tenant
        from gateway.db import tenant_session
        from gateway.routes.projects import filters

        tasks = [_task(w) for _ in range(5)]
        for index, t in enumerate(tasks):
            b.ref(w.p, w.p.org_a, t, index + 1, "open")

        class Counting:
            def __init__(self, db: Any) -> None:
                self.db, self.chips = db, 0

            async def execute(self, statement: Any, params: Any = None) -> Any:
                if "pm_task_refs" in str(statement):
                    self.chips += 1
                return await self.db.execute(statement, params)

        async with b._session(w.p):
            bind_tenant(w.p.org_a)
            async with tenant_session() as db:
                counting = Counting(db)
                rows = [{"id": t} for t in tasks]
                await filters.attach_pr_chip(counting, rows)
        assert counting.chips == 1
        assert [r["pr"]["number"] for r in rows] == [1, 2, 3, 4, 5]

    async def test_p6_the_list_route_and_the_task_route_carry_the_chip(self, w):
        t = _task(w)
        b.ref(w.p, w.p.org_a, t, 11, "open", checks="failing")
        assert (await _rows(w, t))[t]["pr"]["checks_state"] == "failing"
        async with b._session(w.p) as call:
            got = await call(w.a, "GET", f"/projects/tasks/{t}")
        assert got.status_code == 200, got.text
        assert got.json()["pr"]["number"] == 11


class TestTheCsv:
    async def test_p8_the_csv_has_a_pull_request_column(self, w):
        t = _task(w)
        b.ref(w.p, w.p.org_a, t, 12, "open")
        b.ref(w.p, w.p.org_a, _task(w), 13, "merged", merged="now()")
        async with b._session(w.p) as call:
            got = await call(w.a, "GET", f"/projects/export/tasks.csv?project_id={w.proj['id']}"
                                         "&include_subtree=true&shown_fields=pr")
        assert got.status_code == 200, got.text
        text = got.content.decode("utf-8-sig")
        assert "Pull request" in text.splitlines()[0]
        assert f"{b.REPO}#12 open" in text and f"{b.REPO}#13 merged" in text


class TestTheHoverLine:
    async def test_p4_the_note_is_the_first_line_of_the_newest_thought(self, w):
        t = _task(w)
        run = b.run(w.p, w.p.org_a, t, A_EMAIL, "in_progress")
        b.activity(w.p, w.p.org_a, run, t, "thought", "Old line\nmore")
        b.activity(w.p, w.p.org_a, run, t, "thought", "\n  Reading the tests\nsecond line")
        row = (await _rows(w, t))[t]
        assert row["agent_state"] in ("working", "answered")
        assert row["agent_note"] == "Reading the tests"

    async def test_p4_only_a_thought_a_response_or_a_question_makes_a_note(self, w):
        t = _task(w)
        run = b.run(w.p, w.p.org_a, t, A_EMAIL, "in_progress")
        b.activity(w.p, w.p.org_a, run, t, "response", "The answer")
        b.activity(w.p, w.p.org_a, run, t, "action", "run_tests")
        b.activity(w.p, w.p.org_a, run, t, "prompt", "A member prompt")
        assert (await _rows(w, t))[t]["agent_note"] == "The answer"
        q = _task(w)
        run_q = b.run(w.p, w.p.org_a, q, A_EMAIL, "awaiting")
        b.activity(w.p, w.p.org_a, run_q, q, "elicitation", "Which database?")
        assert (await _rows(w, q))[q]["agent_note"] == "Which database?"

    async def test_p4_the_note_is_cut_to_140_characters(self, w):
        t = _task(w)
        run = b.run(w.p, w.p.org_a, t, A_EMAIL, "in_progress")
        b.activity(w.p, w.p.org_a, run, t, "thought", "x" * 400)
        note = (await _rows(w, t))[t]["agent_note"]
        assert note == "x" * 140

    async def test_p4_a_task_with_no_run_has_no_note(self, w):
        t = _task(w)
        row = (await _rows(w, t))[t]
        assert row["agent_note"] is None and row["agent_state"] is None


class TestTheRunPill:
    async def test_p7_the_run_wire_names_the_client(self, w):
        t = _task(w)
        b.run(w.p, w.p.org_a, t, A_EMAIL, "in_progress")
        async with b._session(w.p) as call:
            got = await call(w.a, "GET", f"/projects/tasks/{t}/agent-runs")
        assert got.status_code == 200, got.text
        run = got.json()["rows"][0]
        assert (run["client_name"], run["client_host"]) == ("Claude Code", "claude.ai")

    async def test_p7_a_stamp_that_the_map_does_not_know_reads_as_its_host(self, w):
        t = _task(w)
        b.run(w.p, w.p.org_a, t, A_EMAIL, "in_progress", via="mcp:evil.example:claude-code")
        async with b._session(w.p) as call:
            got = await call(w.a, "GET", f"/projects/tasks/{t}/agent-runs")
        run = got.json()["rows"][0]
        assert (run["client_name"], run["client_host"]) == ("evil.example", "evil.example")
