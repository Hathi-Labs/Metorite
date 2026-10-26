"""WS-27bn RA-6 — the forecast by count and "Needs an owner", with no database.

Spec: ``project-docs/specs/projects_reports.md`` §6.8 B ("Forecast", "Open
work"), §6.8 F ("Needs an owner"), the §5 rows "Forecast by count", "Not
converging", "Not enough history" and "Needs an owner", and §8 RA-6.

The done-when of RA-6 is two sentences, and two classes here pin them:

* **D1 the same seed gives the same dates** (``TestTheSeed``). A different
  seed can give other dates, the generator is a local instance, and the seed
  is the same in each process.
* **D2 a pure test pins the converge line** (``TestTheConvergeLine``).
  Fix round 1: the line belongs to reason ``arrivals`` only, and a ``cap``
  verdict names no rate.

The rest pins each rule of §5: the percentile pick, the runs, "Not
converging" for both reasons, "Not enough history", the summary sentence
(§6.8 E5), and the Python twin of "Needs an owner" with how it relates to
the ``none`` holder of RA-1. ``test_projects_report_forecast_r8.py`` pins
the SQL on a real database.
"""

from __future__ import annotations

import asyncio
import os
import random
import subprocess
import sys
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from gateway.routes.projects import analytics as ana
from gateway.routes.projects.analytics_forecast import (
    FORECAST_CAP_WEEKS,
    FORECAST_TRIALS,
    MIN_FINISH_WEEKS,
    MIN_FINISHED_FOR_FORECAST,
    REASON_ARRIVALS,
    REASON_CAP,
    converge_per_week,
    count_forecast,
    day_words,
    forecast_seed,
    forecast_summary,
    percentile_weeks,
    simulate_weeks,
)

#: A fixed Wednesday, so no date depends on the run date.
TODAY = date(2026, 10, 21)

#: Six weeks with a wide spread, so two seeds can give two answers.
BUMPY = {"finished": [9, 0, 7, 1, 8, 2], "created": [0, 0, 1, 0, 0, 1]}


def _forecast(remaining: int, seed: int = 7, **history: Any) -> dict[str, Any]:
    return count_forecast(
        remaining=remaining,
        finished=history.get("finished", BUMPY["finished"]),
        created=history.get("created", BUMPY["created"]),
        seed=seed,
        today=TODAY,
    )


# ── D1: the seed ─────────────────────────────────────────────────────────────


class TestTheSeed:
    def test_the_same_seed_gives_the_same_dates(self) -> None:
        one = _forecast(40, seed=forecast_seed("p1:1", "2026-10-12"))
        two = _forecast(40, seed=forecast_seed("p1:1", "2026-10-12"))
        assert one["verdict"] == "converging"
        assert (one["p50_date"], one["p85_date"]) == (two["p50_date"], two["p85_date"])
        assert one == two

    def test_a_different_seed_can_give_other_dates(self) -> None:
        seen = {
            (got["p50_weeks"], got["p85_weeks"])
            for week in ("2026-09-07", "2026-09-14", "2026-09-21", "2026-09-28",
                         "2026-10-05", "2026-10-12")
            for got in [_forecast(40, seed=forecast_seed("p1:1", week))]
        }
        assert len(seen) > 1, seen

    def test_the_runs_of_one_seed_are_the_same_list(self) -> None:
        runs = [
            simulate_weeks(remaining=40, seed=99, **BUMPY) for _ in range(2)
        ]
        assert runs[0] == runs[1]
        assert len(runs[0]) == FORECAST_TRIALS

    def test_the_forecast_leaves_the_global_random_state_alone(self) -> None:
        random.seed(1234)
        want = random.random()
        random.seed(1234)
        _forecast(40)
        assert random.random() == want

    def test_a_new_week_or_a_new_scope_is_a_new_seed(self) -> None:
        base = forecast_seed("p1:1", "2026-10-12")
        assert base == forecast_seed("p1:1", "2026-10-12")
        assert base != forecast_seed("p1:1", "2026-10-19")
        assert base != forecast_seed("p2:1", "2026-10-12")
        assert base != forecast_seed("p1:0", "2026-10-12")

    def test_the_seed_is_the_same_in_another_process(self) -> None:
        """``hash()`` of a string changes with ``PYTHONHASHSEED``. The seed
        must not, or two workers of one gateway give two dates."""
        from gateway.routes.projects import analytics_forecast

        # The module file alone, so the child imports no gateway package.
        code = (
            "import importlib.util, sys;"
            "spec = importlib.util.spec_from_file_location('f', sys.argv[1]);"
            "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m);"
            "print(m.forecast_seed('p1:1', '2026-10-12'))"
        )
        for hash_seed in ("1", "12345"):
            env = {**os.environ, "PYTHONHASHSEED": hash_seed}
            out = subprocess.run(
                [sys.executable, "-c", code, analytics_forecast.__file__], env=env,
                capture_output=True, text=True, check=True, timeout=60,
            ).stdout.strip().splitlines()[-1]
            assert int(out) == forecast_seed("p1:1", "2026-10-12")


# ── The runs and the percentile ─────────────────────────────────────────────


class TestTheRuns:
    def test_a_steady_team_takes_the_same_weeks_in_each_run(self) -> None:
        runs = simulate_weeks(
            remaining=23, finished=[5] * 6, created=[0] * 6, seed=1,
        )
        assert set(runs) == {5}

    def test_each_run_puts_the_arrivals_of_its_week_back(self) -> None:
        # Five finish and two arrive each week: a net of three. 23 / 3 is 8.
        runs = simulate_weeks(
            remaining=23, finished=[5] * 6, created=[2] * 6, seed=1,
        )
        assert set(runs) == {8}

    def test_a_run_past_the_cap_reads_one_week_past_it(self) -> None:
        runs = simulate_weeks(
            remaining=10, finished=[4] * 6, created=[4] * 6, seed=1, cap=12,
        )
        assert set(runs) == {13}

    def test_nothing_open_or_no_history_is_no_run(self) -> None:
        assert simulate_weeks(remaining=0, seed=1, **BUMPY) == []
        assert simulate_weeks(remaining=5, finished=[], created=[], seed=1) == []

    def test_the_percentile_is_the_nearest_rank(self) -> None:
        runs = list(range(1, 101))
        assert percentile_weeks(runs, 0.5) == 50
        assert percentile_weeks(runs, 0.85) == 85
        assert percentile_weeks(list(reversed(runs)), 0.85) == 85
        assert percentile_weeks([7], 0.85) == 7
        with pytest.raises(ValueError):
            percentile_weeks([], 0.5)

    def test_2000_runs_at_85_percent_read_the_1700th(self) -> None:
        runs = [1] * 1699 + [2] + [3] * 300
        assert percentile_weeks(runs, 0.85) == 2

    def test_the_dates_are_today_plus_whole_weeks(self) -> None:
        got = _forecast(40)
        assert got["p50_weeks"] <= got["p85_weeks"]
        assert got["p50_date"] == (TODAY + timedelta(weeks=got["p50_weeks"])).isoformat()
        assert got["p85_date"] == (TODAY + timedelta(weeks=got["p85_weeks"])).isoformat()
        # The older readers (the slip, the email, the chat) read the 50%.
        assert got["finish_date"] == got["p50_date"]
        assert got["weeks_remaining"] == got["p50_weeks"]


# ── D2: the converge line, and "Not converging" ─────────────────────────────


class TestTheConvergeLine:
    def test_the_line_is_the_arrivals_plus_the_open_count_over_the_cap(self) -> None:
        assert converge_per_week(remaining=52, created_per_week=3.0) == 4.0
        assert converge_per_week(remaining=104, created_per_week=0.0) == 2.0

    def test_the_line_rounds_up_to_one_decimal(self) -> None:
        # 5.17 + 71 / 52 = 6.535, so 6.6. A rate rounded down does not converge.
        assert converge_per_week(remaining=71, created_per_week=5.17) == 6.6
        assert converge_per_week(remaining=1, created_per_week=0.0) == 0.1

    def test_an_exact_tenth_is_not_rounded_up_again(self) -> None:
        assert converge_per_week(remaining=26, created_per_week=0.0) == 0.5

    def test_a_steady_team_at_the_line_converges_within_the_cap(self) -> None:
        line = converge_per_week(remaining=26, created_per_week=1.0)
        assert line == 1.5
        # Three weeks at three finishes and one arrival, against a line of
        # 1.5 net a week: a steady net of 2 empties 26 well inside the cap.
        runs = simulate_weeks(remaining=26, finished=[3] * 4, created=[1] * 4, seed=1)
        assert max(runs) <= FORECAST_CAP_WEEKS

    def test_work_that_arrives_as_fast_is_not_converging_with_the_line(self) -> None:
        got = _forecast(20, finished=[4] * 6, created=[4] * 6)
        assert got["verdict"] == "not_converging"
        assert got["reason"] == REASON_ARRIVALS
        assert got["converge_per_week"] == converge_per_week(
            remaining=20, created_per_week=4.0,
        )
        assert got["p50_date"] is None and got["finish_date"] is None

    def test_past_the_cap_names_no_rate_the_reviewers_case(self) -> None:
        """Fix round 1. A mean-rate line said "needs 2.3 a week" here, and
        the team finished 2.5 a week. The 85% test failed for the spread of
        the weeks, not for the mean, so the verdict names no rate."""
        got = _forecast(80, finished=[1, 5, 8, 0, 0, 1], created=[2, 0, 0, 0, 0, 2])
        assert got["verdict"] == "not_converging"
        assert got["reason"] == REASON_CAP
        assert got["finished_per_week"] == 2.5
        assert got["converge_per_week"] is None
        assert got["p85_date"] is None
        assert "To converge" not in got["summary"]

    def test_for_arrivals_the_line_is_above_the_arrival_and_the_finish_rate(self) -> None:
        got = _forecast(30, finished=[3, 2, 4, 3, 2, 3], created=[4, 3, 4, 3, 3, 4])
        assert (got["verdict"], got["reason"]) == ("not_converging", REASON_ARRIVALS)
        line = got["converge_per_week"]
        assert line == converge_per_week(remaining=30, created_per_week=got["created_per_week"])
        assert line > got["created_per_week"] >= got["finished_per_week"]
        assert f"To converge, it needs {line} finished tasks a week." in got["summary"]

    def test_a_converging_forecast_has_no_line(self) -> None:
        got = _forecast(40)
        assert got["verdict"] == "converging"
        assert got["converge_per_week"] is None
        assert got["reason"] is None


# ── "Not enough history", and the order of the verdicts ─────────────────────


class TestNotEnoughHistory:
    """Fix round 1: fewer than 3 finished tasks, or fewer than 2 weeks with
    a finish. The arrivals rule runs BEFORE this gate."""

    def test_a_team_that_finishes_every_second_week_gets_a_date(self) -> None:
        got = _forecast(20, finished=[6, 0, 6, 0, 6, 0], created=[0] * 6)
        assert got["verdict"] == "converging"
        assert got["p50_date"] and got["p85_date"]

    def test_a_growing_project_stays_red_with_a_thin_history(self) -> None:
        got = _forecast(30, finished=[1, 0, 0, 0, 0, 0], created=[3, 2, 0, 1, 0, 2])
        assert (got["verdict"], got["reason"]) == ("not_converging", REASON_ARRIVALS)
        assert got["converge_per_week"] is not None

    def test_two_finished_tasks_are_not_enough(self) -> None:
        got = _forecast(10, finished=[1, 0, 1, 0, 0, 0], created=[0] * 6)
        assert MIN_FINISHED_FOR_FORECAST == 3
        assert got["verdict"] == "no_history"
        assert got["finished_in_window"] == 2

    def test_one_week_with_a_finish_is_not_enough(self) -> None:
        got = _forecast(10, finished=[9, 0, 0, 0, 0, 0], created=[0] * 6)
        assert MIN_FINISH_WEEKS == 2
        assert got["verdict"] == "no_history"
        assert got["weeks_with_finishes"] == 1
        assert got["p50_date"] is None and got["converge_per_week"] is None

    def test_exactly_the_rule_is_enough(self) -> None:
        got = _forecast(4, finished=[2, 1, 0, 0, 0, 0], created=[0] * 6)
        assert got["verdict"] == "converging"

    def test_no_arrival_and_no_finish_is_a_thin_history_not_a_growing_one(self) -> None:
        assert _forecast(12, finished=[0] * 6, created=[0] * 6)["verdict"] == "no_history"
        assert _forecast(12, finished=[], created=[])["verdict"] == "no_history"

    def test_nothing_open_comes_first(self) -> None:
        got = _forecast(0, finished=[], created=[])
        assert got["verdict"] == "nothing_left"

    def test_three_weeks_of_history_can_give_a_date(self) -> None:
        """Item 3 of fix round 1: the route takes `weeks` from 2 to 26."""
        got = _forecast(10, finished=[2, 0, 3], created=[0, 0, 0])
        assert got["weeks_sampled"] == 3
        assert got["verdict"] == "converging"


# ── The summary sentence (§6.8 E5) ──────────────────────────────────────────


class TestTheSummary:
    def test_a_converging_forecast_says_both_dates_in_words(self) -> None:
        got = _forecast(40)
        assert got["summary"] == (
            f"Half of 2,000 runs finish the 40 open tasks by"
            f" {day_words(date.fromisoformat(got['p50_date']))}, and 85% finish by"
            f" {day_words(date.fromisoformat(got['p85_date']))}."
        )

    def test_not_converging_says_why_and_the_line(self) -> None:
        got = _forecast(71, finished=[5, 4, 4, 5, 3, 4], created=[6, 5, 5, 4, 6, 5])
        assert got["summary"] == (
            "Not converging: work arrives at least as fast as it finishes"
            " (5.2 a week against 4.2). To converge, it needs 6.6 finished"
            " tasks a week."
        )
        slow = _forecast(60, finished=[1, 1, 1, 1, 0, 0], created=[0] * 6)
        assert slow["summary"] == (
            "Not converging: at the current rate, the 85% date is more than"
            " 52 weeks away. A steadier flow of finished tasks, or smaller"
            " tasks, brings it closer."
        )

    def test_not_enough_history_says_the_rule(self) -> None:
        got = _forecast(12, finished=[2, 0, 0, 0, 0, 0], created=[0] * 6)
        assert got["summary"] == (
            "Not enough history to forecast: 2 tasks finished in 1 week of"
            " the last 6. A forecast needs 3 finished tasks, in 2 weeks or more."
        )
        none = _forecast(12, finished=[0] * 6, created=[0] * 6)
        assert none["summary"] == (
            "Not enough history to forecast: no task finished in the last 6"
            " weeks. A forecast needs 3 finished tasks, in 2 weeks or more."
        )

    def test_one_open_task_is_singular(self) -> None:
        got = _forecast(1)
        assert "the 1 open task by" in got["summary"]

    def test_nothing_open_says_so(self) -> None:
        assert forecast_summary({"verdict": "nothing_left"}) == (
            "Nothing is open in this scope."
        )

    def test_the_date_words_match_the_panel(self) -> None:
        # `shortDate` in `lib/outlook.ts`: "12 Mar 2027", with no leading 0.
        assert day_words(date(2027, 3, 2)) == "2 Mar 2027"


# ── The outlook body seeds the forecast from its scope and its last week ────


class _Res:
    def __init__(self, row: Any = None, rows: list[Any] | None = None):
        self._row, self._rows = row, rows or []

    def one(self) -> Any:
        return self._row

    def fetchall(self) -> list[Any]:
        return self._rows

    def scalar(self) -> Any:
        return 30


class _WeeksDB:
    """The velocity statement answers six weeks. Every other statement
    answers one empty aggregate row."""

    def __init__(self, last_monday: date) -> None:
        self.weeks = [
            SimpleNamespace(
                week=datetime.combine(last_monday - timedelta(weeks=5 - i), datetime.min.time()),
                finished=n, created=m,
            )
            for i, (n, m) in enumerate(zip(BUMPY["finished"], BUMPY["created"], strict=True))
        ]

    async def execute(self, sql: Any, params: dict | None = None) -> _Res:
        statement = " ".join(str(sql).split())
        if "generate_series" in statement:
            return _Res(rows=self.weeks)
        row = SimpleNamespace(
            mins=0, estimated=0, tasks=30, planned_finish=None, dated=0, people=0,
            in_directory=0, schedules=[], leaving_soon=0,
        )
        return _Res(row=row)


class _Vis:
    unrestricted = True
    params: dict[str, Any] = {"vis_org": "org-1"}  # noqa: RUF012


def _outlook(db: _WeeksDB, project_id: str | None, monkeypatch) -> dict[str, Any]:
    async def _node(*_a: Any, **_k: Any) -> str:
        return "TRUE"

    monkeypatch.setattr(ana, "scope_clause", _node)
    monkeypatch.setattr(ana, "task_visibility_clause", lambda _v, _a: "TRUE")
    return asyncio.run(ana.outlook_body(
        db, _Vis(), hr_visible=False, project_id=project_id,
        include_subtree=True, today=TODAY,
    ))


class TestTheOutlookSeed:
    def test_a_reload_gives_the_same_dates(self, monkeypatch) -> None:
        one = _outlook(_WeeksDB(date(2026, 10, 12)), "p1", monkeypatch)["velocity"]
        two = _outlook(_WeeksDB(date(2026, 10, 12)), "p1", monkeypatch)["velocity"]
        assert one["verdict"] == "converging"
        assert one == two

    def test_the_seed_is_the_scope_and_the_last_week(self, monkeypatch) -> None:
        seen: list[int] = []
        real = ana.project_forecast

        def spy(**kw: Any) -> dict[str, Any]:
            seen.append(kw["seed"])
            return real(**kw)

        monkeypatch.setattr(ana, "project_forecast", spy)
        _outlook(_WeeksDB(date(2026, 10, 12)), "p1", monkeypatch)
        _outlook(_WeeksDB(date(2026, 10, 19)), "p1", monkeypatch)
        _outlook(_WeeksDB(date(2026, 10, 12)), None, monkeypatch)
        assert seen[0] == forecast_seed("p1:1", "2026-10-12")
        assert seen[1] == forecast_seed("p1:1", "2026-10-19")
        assert seen[2] == forecast_seed("portfolio:1", "2026-10-12")

    def test_the_velocity_block_carries_the_count_keys(self, monkeypatch) -> None:
        got = _outlook(_WeeksDB(date(2026, 10, 12)), "p1", monkeypatch)["velocity"]
        for key in (
            "p50_weeks", "p50_date", "p85_weeks", "p85_date", "trials",
            "cap_weeks", "weeks_with_finishes", "finished_in_window",
            "min_finished", "min_finish_weeks",
            "converge_per_week", "reason", "summary",
            # The keys that every older reader reads stay.
            "weeks_sampled", "finished_per_week", "created_per_week",
            "net_per_week", "remaining_tasks", "weeks_remaining", "finish_date",
            "verdict",
        ):
            assert key in got, key
        assert (got["trials"], got["cap_weeks"]) == (FORECAST_TRIALS, FORECAST_CAP_WEEKS)


# ── "Needs an owner": the Python twin, and how it relates to `none` ─────────


class TestNeedsAnOwner:
    @pytest.mark.parametrize(
        ("assignees", "open_run", "want"),
        [
            ([], False, True),
            (["  "], False, True),
            ([""], False, True),
            (["asha@example.test"], False, False),
            (["agent:summariser"], False, False),
            ([], True, False),
            (["asha@example.test"], True, False),
        ],
    )
    def test_the_rule(self, assignees: list[str], open_run: bool, want: bool) -> None:
        assert ana.is_needs_owner(assignees, open_run=open_run) is want

    @pytest.mark.parametrize("assignees", [[], [""], ["asha@example.test"], ["agent:x"]])
    @pytest.mark.parametrize(
        ("open_run", "lease_holds"), [(False, False), (True, False), (True, True)],
    )
    def test_every_task_that_needs_an_owner_is_in_none(
        self, assignees: list[str], open_run: bool, lease_holds: bool,
    ) -> None:
        """§6.8 F against RA-1. A run keeps a task only while it is open AND
        its lease holds, so "needs an owner" is inside `none`."""
        holder = ana.classify_holder(assignees, run_holds=open_run and lease_holds)
        if ana.is_needs_owner(assignees, open_run=open_run):
            assert holder is None

    def test_none_also_holds_a_task_whose_open_run_went_quiet(self) -> None:
        """The one difference: no assignee, an open run, and a lease that
        ended. Nobody holds it now, and it has an owner that stopped."""
        assert ana.classify_holder([], run_holds=False) is None
        assert ana.is_needs_owner([], open_run=True) is False

    def test_it_leads_when_it_is_at_least_each_holder(self) -> None:
        people = [
            {"assignee": None, "open_tasks": 25},
            {"assignee": "asha@example.test", "open_tasks": 1},
        ]
        assert ana.needs_owner_leads(25, people) is True
        assert ana.needs_owner_leads(1, people) is True
        assert ana.needs_owner_leads(2, [{"assignee": "bo@x.test", "open_tasks": 5}]) is False
        assert ana.needs_owner_leads(3, [{"assignee": "agent:x", "open_tasks": 4}]) is False
        assert ana.needs_owner_leads(0, []) is False
        assert ana.needs_owner_leads(4, [{"assignee": None, "open_tasks": 9}]) is True

    def test_the_sql_reads_the_one_assignee_text_and_the_open_statuses(self) -> None:
        from gateway.routes.projects.filters import AGENT_RUN_OPEN_STATUSES

        sql = ana.needs_owner_sql()
        assert ana.any_assignee_sql() in sql
        assert ana.any_assignee_sql() in ana.holder_label_sql()
        for status in AGENT_RUN_OPEN_STATUSES:
            assert f"'{status}'" in sql
        # No lease here: the lease is the `none` rule's, not this one's.
        assert "last_activity_at" not in sql

    def test_the_rows_read_the_load_predicate_and_a_cap(self) -> None:
        sql = ana.needs_owner_rows_sql("OPEN_WHERE_MARK")
        assert "OPEN_WHERE_MARK" in sql
        assert ":needs_owner_max" in sql
        assert ana.needs_owner_sql() in sql


# ── The chat: the printers of the two sections (§6.8 E7) ────────────────────

skill_projects = pytest.importorskip("skill_projects", reason="skill-projects not installed")

from tests.unit._projects_agent_fakes import fake_gateway  # noqa: E402


def _owner_section(*, leads: bool, total: int = 23, named: int = 20) -> dict[str, Any]:
    return {
        "total_tasks": 30,
        "people": [{"assignee": None, "open_tasks": total, "overdue": 0},
                   {"assignee": "ana@example.test", "open_tasks": 4, "overdue": 0}],
        "needs_owner": {
            "total": total, "leads": leads, "max_rows": 20,
            "tasks": [
                {"id": f"t{i}", "task_number": i + 1, "title": f"Task {i}\nforged row",
                 "project_id": "p1", "due_at": "2026-11-02T00:00:00+00:00" if i == 0 else None}
                for i in range(named)
            ],
        },
    }


class TestTheChat:
    def test_each_title_is_fenced_and_each_row_sent_prints(self) -> None:
        from skill_projects.client import data
        from skill_projects.reads import needs_owner_lines

        lines = needs_owner_lines(_owner_section(leads=True))
        rows = [line for line in lines if line.startswith("- needs an owner")]
        # Every row the server named, so the paging fence holds: no cut here.
        assert len(rows) == 20
        # A newline in a title cannot forge a second row.
        assert rows[0] == f"- needs an owner · #1 {data('Task 0' + chr(10) + 'forged row')} · due 2026-11-02"
        assert lines[-1] == "  (and 3 more that need an owner, past the 20 named)"
        assert "leads the open work" in lines[0]

    def test_none_to_name_prints_nothing(self) -> None:
        from skill_projects.reads import needs_owner_lines

        assert needs_owner_lines({"needs_owner": {"total": 0, "tasks": []}}) == []
        assert needs_owner_lines({}) == []

    def test_the_load_section_prints_it_first_when_it_leads_and_last_when_not(self) -> None:
        from skill_projects.reads import _report_section

        first = _report_section("load", _owner_section(leads=True))
        at = next(i for i, line in enumerate(first) if line.startswith("  Needs an owner"))
        assert at < next(i for i, line in enumerate(first) if "unassigned" in line)
        assert "tasks that need an owner 23" in first[0]
        last = _report_section("load", _owner_section(leads=False))
        at = next(i for i, line in enumerate(last) if line.startswith("  Needs an owner"))
        assert at > next(i for i, line in enumerate(last) if "ana@example.test" in line)

    def test_the_outlook_section_prints_the_server_sentence(self) -> None:
        from skill_projects.reads import _report_section

        said = "Half of 2,000 runs finish the 9 open tasks by 23 Dec 2026, and 85% finish by 13 Jan 2027."
        lines = _report_section("outlook", {"velocity": {
            "verdict": "converging", "finish_date": "2026-12-23", "p85_date": "2027-01-13",
            "remaining_tasks": 9, "summary": said,
        }})
        assert lines[1] == f"  {said}"
        assert "85% finish 2027-01-13" in lines[0]

    def test_analytics_outlook_prints_the_sentence_first(self, monkeypatch) -> None:
        said = "Not enough history to forecast: 2 of the last 6 weeks had a finished task, and a forecast needs 4."
        fake_gateway(monkeypatch, lambda c: {
            "scope": "portfolio", "velocity": {"verdict": "no_history", "summary": said},
        })
        out = asyncio.run(skill_projects.analytics_outlook())
        assert out.splitlines()[1] == f"  {said}"

    def test_analytics_load_prints_the_tasks_that_need_an_owner(self, monkeypatch) -> None:
        fake_gateway(monkeypatch, lambda c: {
            **_owner_section(leads=True, total=2, named=2), "people_listed": 2,
        })
        out = asyncio.run(skill_projects.analytics_load())
        assert "Needs an owner: 2 open tasks with no assignee and no open run" in out
        assert out.index("Needs an owner") < out.index("- unassigned")

    def test_analytics_load_prints_it_on_the_first_page_only(self, monkeypatch) -> None:
        """Fix round 1. It is not a page of people, so page 2 does not repeat it."""
        fake_gateway(monkeypatch, lambda c: {
            **_owner_section(leads=True, total=2, named=2), "people_listed": 25,
        })
        later = asyncio.run(skill_projects.analytics_load(offset=20))
        assert "Needs an owner" not in later
        assert "needs an owner ·" not in later

    def test_a_null_figure_prints_nothing(self) -> None:
        """Fix round 1. A converging forecast sends `converge_per_week: null`,
        and the model must not read "to converge None"."""
        from skill_projects.reads import _report_section

        lines = _report_section("outlook", {"velocity": {
            "verdict": "converging", "finish_date": "2026-12-23", "p85_date": "2027-01-13",
            "converge_per_week": None, "remaining_tasks": 9,
        }})
        assert "None" not in lines[0]
        assert "to converge" not in lines[0]
        arrivals = _report_section("outlook", {"velocity": {
            "verdict": "not_converging", "converge_per_week": 6.6, "remaining_tasks": 9,
        }})
        assert "finished tasks a week to converge 6.6" in arrivals[0]

    def test_the_card_says_not_enough_history_in_the_section_5_word(self) -> None:
        from skill_projects.views import _card_section

        _stats, table = _card_section("outlook", {"velocity": {"verdict": "no_history"}})
        assert table is not None
        assert table["rows"][0]["cells"][0] == "Not enough history"

    def test_the_card_names_the_85_percent_date_and_the_count(self) -> None:
        from skill_projects.views import REPORT_CARD_SECTIONS

        assert ("velocity.p85_date", "85% finish") in REPORT_CARD_SECTIONS["outlook"]["fields"]
        assert ("needs_owner.total", "Need an owner") in REPORT_CARD_SECTIONS["load"]["stats"]
