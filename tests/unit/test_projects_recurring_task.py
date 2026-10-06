"""WS-46 P1 — a recurring task is made in one step, with its rule on the card.

Spec: ``project-docs/specs/projects_agent_parity.md`` §4 (the root cause),
§8 (the slice) and §14 Q1 (the weekly default). D91.

The failure this slice fixes: ``create_task`` had no repeat argument, so a
"weekly task" became a task with no rule and a receipt that said "Created:".
Each test below names the acceptance item of §8.2 it holds.

No gateway and no database: the client is patched at httpx (the shared
fakes), and the card is stubbed both ways. P1 changes no SQL (§12.2), so no
R8 test is needed here. ``test_projects_recurrence.py`` holds the route.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest

pytest.importorskip("skill_projects", reason="skill-projects not installed")

import skill_projects
from skill_projects import manifest as m
from skill_projects import writes as W

from tests.unit._projects_agent_fakes import approve, deny, empty_list, fake_gateway, writes

PROJECT = "0f8fad5b-d9cb-469f-a165-70867728950e"
TASK_ID = "1f8fad5b-d9cb-469f-a165-70867728950e"
LANE = "3f8fad5b-d9cb-469f-a165-70867728950e"
#: 2026-10-06 is a Tuesday. Every test pins the tool's today to it.
TUESDAY = dt.date(2026, 10, 6)


@pytest.fixture(autouse=True)
def _today(monkeypatch) -> None:
    monkeypatch.setattr(W, "_today", lambda: TUESDAY)


def _gateway(call: dict) -> Any:
    """The create route echoes what it was sent, as the real route does."""
    path, method = call["path"], call["method"]
    if method == "POST" and path == "/projects/tasks":
        body = call["json"] or {}
        return {"id": TASK_ID, "task_number": 9, "status_id": LANE, "assignees": [], **body}
    if path.endswith("/recurrence") and method == "PUT":
        return {"rule": call["json"]}
    if path.endswith("/statuses"):
        return {"rows": [{"id": LANE, "name": "To do", "category": "todo"}]}
    return empty_list(call)


def _posts(calls: list[dict]) -> list[dict]:
    return [c for c in writes(calls) if c["method"] == "POST" and c["path"] == "/projects/tasks"]


def _rule_puts(calls: list[dict]) -> list[dict]:
    return [c for c in writes(calls) if c["path"].endswith("/recurrence")]


# ── §8.2 item 1 — one card, two writes, the rule on the receipt ─────────────


async def test_one_card_makes_the_task_and_sets_the_rule(monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _gateway)
    out = await skill_projects.create_task(
        PROJECT, "Send the timesheet", repeat="weekly", repeat_on="5"
    )
    assert len(asked) == 1, asked
    assert len(_posts(calls)) == 1
    assert [c["json"] for c in _rule_puts(calls)] == [
        {"freq": "weekly", "interval": 1, "weekdays": [5], "anchor": "due"}
    ]
    assert len(writes(calls)) == 2, writes(calls)
    assert _rule_puts(calls)[0]["path"] == f"/projects/tasks/{TASK_ID}/recurrence"
    assert "repeats every week on Friday" in out
    assert W.NEXT_COPY in out
    assert "repeats every week on Friday" in asked[0]["detail"]
    assert "next copy:" in asked[0]["context"]


# ── §8.2 item 2 — a weekly rule with no day takes the due date's day (Q1) ───


async def test_a_weekly_rule_with_no_day_takes_the_due_dates_weekday(monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _gateway)
    await skill_projects.create_task(PROJECT, "Send the timesheet", due="2026-10-09", repeat="weekly")
    assert _rule_puts(calls)[0]["json"]["weekdays"] == [5]
    assert _posts(calls)[0]["json"]["due_at"] == "2026-10-09"
    assert "repeat day: «Friday, the due date's weekday" in asked[0]["context"]


# ── §8.2 item 3 — no due date: the POST carries the first matching day ──────


async def test_no_due_and_no_day_takes_today_and_sets_the_due_date(monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _gateway)
    await skill_projects.create_task(PROJECT, "Review the backlog", repeat="weekly")
    assert _rule_puts(calls)[0]["json"]["weekdays"] == [2]
    assert _posts(calls)[0]["json"]["due_at"] == "2026-10-06"
    context = asked[0]["context"]
    assert "repeat day: «Tuesday, today's weekday (UTC)" in context
    assert "first due: «2026-10-06" in context


async def test_no_due_takes_the_first_named_day_on_or_after_today(monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _gateway)
    await skill_projects.create_task(PROJECT, "Send the timesheet", repeat="weekly", repeat_on="5")
    assert _posts(calls)[0]["json"]["due_at"] == "2026-10-09"
    assert "first due: «2026-10-09" in asked[0]["context"]
    assert "repeat day" not in asked[0]["context"], "a named day is not a guess"


@pytest.mark.parametrize(
    ("rule", "first"),
    [
        ({"freq": "daily"}, "2026-10-06"),
        ({"freq": "weekly", "weekdays": [1]}, "2026-10-12"),
        ({"freq": "weekly", "weekdays": [2, 4]}, "2026-10-06"),
        ({"freq": "monthly", "day_of_month": 5}, "2026-11-05"),
        ({"freq": "monthly", "day_of_month": 31}, "2026-10-31"),
        ({"freq": "yearly", "day_of_month": 29, "month_of_year": 2}, "2027-02-28"),
        ({"freq": "yearly", "day_of_month": 25, "month_of_year": 12}, "2026-12-25"),
    ],
)
def test_the_first_due_day(rule: dict, first: str) -> None:
    assert W._first_due(rule, TUESDAY).isoformat() == first


async def test_a_rule_from_completion_sets_no_due_date(monkeypatch) -> None:
    approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _gateway)
    await skill_projects.create_task(
        PROJECT, "Clean the printer", repeat="daily", repeat_every=3, repeat_from="completed"
    )
    assert "due_at" not in _posts(calls)[0]["json"]
    assert _rule_puts(calls)[0]["json"] == {"freq": "daily", "interval": 3, "anchor": "completed"}


# ── §8.2 item 4 — decline writes nothing, a refused rule is named ──────────


async def test_a_declined_card_makes_zero_writes(monkeypatch) -> None:
    deny(monkeypatch)
    calls = fake_gateway(monkeypatch, _gateway)
    out = await skill_projects.create_task(PROJECT, "Send the timesheet", repeat="weekly")
    assert out == W.CANCELLED
    assert writes(calls) == []


async def test_a_refused_rule_names_the_task_and_the_refusal(monkeypatch) -> None:
    approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _gateway)

    async def refuse(path: str, payload: dict | None = None) -> Any:
        calls.append({"method": "PUT", "path": path, "json": payload, "params": {}, "headers": {}})
        raise W.GatewayRefusal(f"Projects PUT {path}: Failed (422). Weekdays are 1 to 7.")

    monkeypatch.setattr(W, "put", refuse)
    out = await skill_projects.create_task(PROJECT, "Send the timesheet", repeat="weekly")
    assert out.startswith("Created #9. The repeat rule was NOT saved: «Projects PUT")
    assert "Weekdays are 1 to 7." in out
    assert "\nstopped: the repeat rule." in out, "the receipt card must show a partial result"
    assert f"full_id: {TASK_ID}" in out
    # The task stays. Nothing archives it.
    assert [c["path"] for c in writes(calls)] == [
        "/projects/tasks",
        f"/projects/tasks/{TASK_ID}/recurrence",
    ]


# ── §8.2 item 5 — no repeat argument: exactly what it sent before ──────────


async def test_no_repeat_sends_one_post_and_no_rule(monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _gateway)
    out = await skill_projects.create_task(PROJECT, "Call the vendor", due="2026-10-09")
    assert [c["path"] for c in writes(calls)] == ["/projects/tasks"]
    assert _posts(calls)[0]["json"] == {
        "project_id": PROJECT,
        "title": "Call the vendor",
        "due_at": "2026-10-09",
    }
    assert asked[0]["title"] == "Create this task?"
    assert "repeat" not in asked[0]["context"]
    assert out.startswith("Created:\n")
    assert "repeats" not in out


@pytest.mark.parametrize(
    "stray",
    [{"repeat_on": "5"}, {"repeat_every": 2}, {"repeat_until": "2026-12-31"}, {"repeat_day": 3}],
)
async def test_a_repeat_argument_without_repeat_is_refused_not_dropped(stray, monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _gateway)
    out = await skill_projects.create_task(PROJECT, "Send the timesheet", **stray)
    assert "needs repeat" in out and next(iter(stray)) in out
    assert asked == [] and writes(calls) == []


async def test_a_refusal_names_the_create_task_argument(monkeypatch) -> None:
    asked = approve(monkeypatch)
    fake_gateway(monkeypatch, _gateway)
    assert (await skill_projects.create_task(PROJECT, "X", repeat="hourly")).startswith(
        "repeat is one of"
    )
    assert "repeat_day" in await skill_projects.create_task(PROJECT, "X", repeat="monthly")
    assert "repeat_every" in await skill_projects.create_task(
        PROJECT, "X", repeat="daily", repeat_every=400
    )
    assert asked == []


async def test_a_day_name_is_taken_as_its_number(monkeypatch) -> None:
    approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _gateway)
    await skill_projects.create_task(PROJECT, "Stand-up", repeat="weekly", repeat_on="Mon, fri")
    assert _rule_puts(calls)[0]["json"]["weekdays"] == [1, 5]


# ── §8.2 item 6 — task_detail prints the rule ──────────────────────────────


def _detail(rule: Any) -> Any:
    def answer(call: dict) -> Any:
        if call["path"] == f"/projects/tasks/{TASK_ID}":
            return {"id": TASK_ID, "title": "Send the timesheet", "task_number": 9}
        if call["path"].endswith("/recurrence"):
            return {"rule": rule}
        return empty_list(call)

    return answer


async def test_task_detail_prints_the_rule(monkeypatch) -> None:
    fake_gateway(monkeypatch, _detail({"freq": "weekly", "interval": 1, "weekdays": [5]}))
    out = await skill_projects.task_detail(TASK_ID)
    assert "  Repeats: every week on Friday, from the due date" in out


async def test_task_detail_prints_nothing_for_a_task_that_does_not_repeat(monkeypatch) -> None:
    fake_gateway(monkeypatch, _detail(None))
    assert "Repeats:" not in await skill_projects.task_detail(TASK_ID)


async def test_task_detail_never_reads_a_failed_rule_as_no_rule(monkeypatch) -> None:
    async def refused(path: str, params: dict | None = None) -> Any:
        if path.endswith("/recurrence"):
            raise W.GatewayRefusal("Projects GET: Not found, or not visible to you.")
        return {"id": TASK_ID, "title": "T", "task_number": 9} if path.endswith(TASK_ID) else {}

    import skill_projects.reads as R

    monkeypatch.setattr(R, "get", refused)
    assert "Repeats: unknown" in await skill_projects.task_detail(TASK_ID)


# ── §8.1 item 3 and §8.2 item 7 — the manifest records the composite ───────


def test_the_manifest_lets_create_task_reach_the_rule_route_and_detail_read_it() -> None:
    assert m.reaches("create_task", "set_recurrence")
    assert m.reaches("task_detail", "recurrence")
    # No new tool name: P1 adds arguments, so F6 and F7 stay as they are.
    assert "create_task" in skill_projects.__all__
    assert m.tool_class("create_task") == "B"


# ── §9.1, §9.2 and §9.5 — the instructions join the two steps ──────────────


def test_the_instructions_say_a_repeating_task_is_one_call() -> None:
    """ADVISORY (R7): no test can make the model follow a sentence. This pins
    the words, so a later edit that drops them fails here and is seen."""
    from tests.unit._projects_agent_fakes import AGENT_DIR

    text = (AGENT_DIR / "instructions.md").read_text(encoding="utf-8")
    for phrase in (
        "A task that repeats is ONE call: `create_task` with\n  `repeat`.",
        'Do not write "weekly" into the title or the description.',
        "Tell the member which day it used.",
        "The next task appears when the member closes this one.",
        "**A setting goes in its argument, never in the text.**",
        "**Check the receipt against the ask.**",
        "When one tool takes both halves of an act, such as a task and its repeat rule,\nmake one call.",
    ):
        assert phrase in text, phrase


# ── §8.2 item 8 — PO-1 and PO-2 of §11, in scripted mode ───────────────────
#
# P3 builds `evals/projects_ops/`. Until then these run the known-good tool
# sequence for each prompt and apply the checker §11.2 states.


def _po_check(asked: list[dict], calls: list[dict], out: str, weekday: int) -> None:
    assert len(asked) == 1, "one card"
    posts = _posts(calls)
    assert len(posts) == 1, "one POST /projects/tasks"
    puts = _rule_puts(calls)
    assert len(puts) == 1, "one PUT …/recurrence"
    assert puts[0]["json"]["freq"] == "weekly"
    assert puts[0]["json"]["weekdays"] == [weekday]
    title = posts[0]["json"]["title"].lower()
    assert "weekly" not in title and "every" not in title, title
    assert "It repeats" in out


async def test_po1_a_weekly_task_every_friday(monkeypatch) -> None:
    """PO-1: "Make a recurring weekly task: send the timesheet, every Friday"."""
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _gateway)
    out = await skill_projects.create_task(
        PROJECT, "Send the timesheet", repeat="weekly", repeat_on="5"
    )
    _po_check(asked, calls, out, 5)


async def test_po1_a_weekly_task_every_monday_called_x(monkeypatch) -> None:
    """The audit's case: "make a weekly task every Monday called X"."""
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _gateway)
    out = await skill_projects.create_task(PROJECT, "X", repeat="weekly", repeat_on="1")
    _po_check(asked, calls, out, 1)
    assert "every week on Monday" in out


async def test_po2_a_weekly_task_with_no_day_names_the_day(monkeypatch) -> None:
    """PO-2: "Add a weekly task to review the backlog". No day, no due."""
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _gateway)
    out = await skill_projects.create_task(PROJECT, "Review the backlog", repeat="weekly")
    _po_check(asked, calls, out, 2)
    assert "Tuesday" in asked[0]["context"] and "Tuesday" in out
