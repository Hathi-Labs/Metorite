"""WS-46 P7 — the chat sets the project and personal fields the screen sets.

Spec: ``project-docs/specs/projects_agent_parity.md`` §3.3 (gaps G12 to G18),
§8.1 item 4 (the member's timezone) and §12 (slice P7). D91.

What this file holds, each with the act it fences:

* **The member's clock.** A weekly rule with no day takes today in the
  member's own zone, from ``GET /projects/my/today``, and the card names the
  zone. P1 took the UTC date.
* **G12** ``update_project`` takes the Space Settings and Lifecycle fields,
  and refuses the route's level rules before the card.
* **G13** ``move_project`` takes ``place``, with the tree drag's maths.
* **G14** ``save_view`` takes the filters in the app's own keys, so the app
  opens the view the chat saved with every filter on.
* **G15** ``set_my_overlay`` takes the block, the actual times, deep work and
  the chase. **G16** ``bulk_update`` takes the same overlay for a selection.
* **G17** ``my_areas``. **G18** ``my_work(untriaged=true)``.
* **Lockstep.** Each copy the skill keeps of an app or route vocabulary is
  held equal to its source.

F2 (``test_projects_field_parity.py``) holds each new ``SENDS`` row true on
the wire. This file holds the checks before the card, the cards and the
receipts. R8: the half at the end runs the new read's SQL on asyncpg, as a
role that FORCE ROW LEVEL SECURITY binds, over the tenant ladder.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from typing import Any

import pytest

pytest.importorskip("skill_projects", reason="skill-projects not installed")

import skill_projects
from skill_projects import guarded as G
from skill_projects import inbox as I
from skill_projects import writes as W

from tests.unit._projects_agent_fakes import (
    REPO_ROOT,
    approve,
    deny,
    empty_list,
    fake_gateway,
    writes,
)

PROJECT = "0f8fad5b-d9cb-469f-a165-70867728950e"
TASK = "1f8fad5b-d9cb-469f-a165-70867728950e"
OTHER = "2f8fad5b-d9cb-469f-a165-70867728950e"
CHILD = "3f8fad5b-d9cb-469f-a165-70867728950e"
SIBLING = "4f8fad5b-d9cb-469f-a165-70867728950e"
THIRD = "5f8fad5b-d9cb-469f-a165-70867728950e"
VIEW = "6f8fad5b-d9cb-469f-a165-70867728950e"
LANE = "7f8fad5b-d9cb-469f-a165-70867728950e"
APP = REPO_ROOT / "workbench" / "control_plane" / "src" / "app" / "projects"
#: 2026-10-06 is a Tuesday in UTC. In Pacific/Kiritimati (UTC+14) it is
#: already Wednesday for fourteen hours of the UTC day.
TUESDAY = dt.date(2026, 10, 6)
WEDNESDAY = dt.date(2026, 10, 7)


@pytest.fixture(autouse=True)
def _utc_today(monkeypatch) -> None:
    monkeypatch.setattr(W, "_today", lambda: TUESDAY)


def _clock(today: dt.date = WEDNESDAY, zone: str = "Pacific/Kiritimati") -> dict:
    return {"today": today.isoformat(), "timezone": zone, "stored": True}


def _gateway(extra: dict[tuple[str, str], Any] | None = None):
    """A gateway that answers the reads P7's tools make. ``extra`` answers a
    ``(method, path)`` with a value, or with a function of the call."""
    table = dict(extra or {})

    def answer(call: dict) -> Any:
        key = (call["method"], call["path"])
        if key in table:
            hit = table[key]
            return hit(call) if callable(hit) else hit
        path, method = call["path"], call["method"]
        if path == "/projects/my/today":
            return _clock()
        if method == "POST" and path == "/projects/tasks":
            return {"id": OTHER, "task_number": 9, "status_id": LANE, "assignees": [],
                    **(call["json"] or {})}
        if path.endswith("/recurrence") and method == "PUT":
            return {"rule": call["json"]}
        if path.endswith("/recurrence"):
            return {"rule": None}
        if path.endswith("/statuses"):
            return {"rows": [{"id": LANE, "name": "To do", "category": "todo"}]}
        if path == f"/projects/tasks/{TASK}":
            return {"id": TASK, "task_number": 7, "title": "Fix the extruder",
                    "project_id": PROJECT, "due_at": None, "completed_at": None}
        if path == f"/projects/tasks/{OTHER}":
            return {"id": OTHER, "task_number": 8, "title": "Order the nozzle",
                    "project_id": PROJECT, "completed_at": "2026-10-01T00:00:00+00:00"}
        if path.startswith("/projects/my/tasks/"):
            return {"id": TASK, "scheduled_start": None, "scheduled_end": None}
        if path == "/projects/assignees":
            return {"people": [{"assignee": "priya@x.io", "name": "Priya"}], "agents": []}
        if path == "/projects/people/names":
            return {"names": {"priya@x.io": "Priya Menon"}}
        if method in ("POST", "PATCH", "PUT"):
            return {"id": PROJECT, **(call["json"] or {})}
        return empty_list(call)

    return answer


def _sent(calls: list[dict], method: str, path: str) -> list[dict]:
    return [c for c in writes(calls) if c["method"] == method and c["path"] == path]


# ── The member's clock (§8.1 item 4) ────────────────────────────────────────


async def test_a_weekly_rule_with_no_day_takes_the_members_own_today(monkeypatch) -> None:
    """THE FENCE for the owner's "until P7": in Kiritimati it is Wednesday
    while UTC is still Tuesday, and the rule takes Wednesday."""
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _gateway())
    await skill_projects.create_task(PROJECT, "Review the backlog", repeat="weekly")
    rule = _sent(calls, "PUT", f"/projects/tasks/{OTHER}/recurrence")[0]["json"]
    assert rule["weekdays"] == [3]
    assert _sent(calls, "POST", "/projects/tasks")[0]["json"]["due_at"] == "2026-10-07"
    assert "Wednesday, today's weekday (Pacific/Kiritimati)" in asked[0]["context"]


async def test_set_recurrence_takes_the_members_today_too(monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _gateway())
    await skill_projects.set_recurrence(TASK, freq="weekly")
    assert _sent(calls, "PUT", f"/projects/tasks/{TASK}/recurrence")[0]["json"]["weekdays"] == [3]
    assert "(Pacific/Kiritimati)" in asked[0]["context"]


async def test_an_end_date_is_judged_against_the_members_date(monkeypatch) -> None:
    """Tuesday is in the past in Kiritimati, so an end on Tuesday is refused
    there, though it is today in UTC."""
    approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _gateway())
    out = await skill_projects.set_recurrence(TASK, freq="daily", until="2026-10-06")
    assert "in the past" in out and writes(calls) == []


async def test_no_clock_read_falls_back_to_utc_and_says_so(monkeypatch) -> None:
    """A gateway mid-deploy that does not serve the read yet (R6)."""
    from skill_projects.client import GatewayRefusal

    def missing(_call: dict) -> Any:
        raise GatewayRefusal("not served", status=404)

    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _gateway({("GET", "/projects/my/today"): missing}))
    await skill_projects.create_task(PROJECT, "Review the backlog", repeat="weekly")
    assert _sent(calls, "PUT", f"/projects/tasks/{OTHER}/recurrence")[0]["json"]["weekdays"] == [2]
    assert "Tuesday, today's weekday (UTC)" in asked[0]["context"]


async def test_a_create_with_no_rule_reads_no_clock(monkeypatch) -> None:
    """§8.2 item 5 holds: a plain create sends what it sent before P1."""
    approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _gateway())
    await skill_projects.create_task(PROJECT, "Call the vendor")
    assert not [c for c in calls if c["path"] == "/projects/my/today"]


async def test_my_work_opens_with_the_members_date(monkeypatch) -> None:
    fake_gateway(monkeypatch, _gateway())
    out = await skill_projects.my_work()
    assert "Your date: Wednesday 2026-10-07 (Pacific/Kiritimati)." in out


# ── G12: a space's settings ─────────────────────────────────────────────────


def _node(parent: str | None = None, **extra: Any) -> dict:
    return {"id": PROJECT, "name": "Product", "parent_project_id": parent, "icon": None,
            "icon_slot": None, "archive_after_months": None, "close_after_months": None,
            "timezone": "UTC", **extra}


async def test_update_project_sends_the_space_settings_with_before_and_after(
    monkeypatch,
) -> None:
    asked = approve(monkeypatch)
    calls = fake_gateway(
        monkeypatch, _gateway({("GET", f"/projects/nodes/{PROJECT}"): _node()})
    )
    out = await skill_projects.update_project(
        PROJECT, icon="rocket", icon_slot=4, archive_after_months=6, close_after_months=12,
        timezone="Asia/Kolkata",
    )
    body = _sent(calls, "PATCH", f"/projects/nodes/{PROJECT}")[0]["json"]
    assert body == {"icon": "Rocket", "icon_slot": 4, "archive_after_months": 6,
                    "close_after_months": 12, "timezone": "Asia/Kolkata"}
    assert "timezone: «UTC» → «Asia/Kolkata»" in asked[0]["context"]
    assert "Settings: icon «Rocket»" in out


async def test_clear_switches_a_lifecycle_policy_off(monkeypatch) -> None:
    approve(monkeypatch)
    node = _node(archive_after_months=6)
    calls = fake_gateway(monkeypatch, _gateway({("GET", f"/projects/nodes/{PROJECT}"): node}))
    out = await skill_projects.update_project(PROJECT, clear="archive_after_months")
    assert _sent(calls, "PATCH", f"/projects/nodes/{PROJECT}")[0]["json"] == {
        "archive_after_months": None
    }
    assert "archive_after_months off" in out


@pytest.mark.parametrize(
    ("kwargs", "words"),
    [
        ({"icon": "Unicorn"}, "Space Settings icons"),
        ({"icon_slot": 13}, "1 to 12"),
        ({"archive_after_months": -2}, "1 or more"),
        ({"timezone": "Mars/Olympus"}, "IANA name"),
        ({"clear": "timezone"}, "cannot be cleared"),
        ({"clear": "lead"}, "clear takes"),
        ({"icon": "Rocket", "clear": "icon"}, "both set and cleared"),
    ],
)
async def test_a_bad_setting_is_refused_before_the_card(monkeypatch, kwargs, words) -> None:
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _gateway({("GET", f"/projects/nodes/{PROJECT}"): _node()}))
    out = await skill_projects.update_project(PROJECT, **kwargs)
    assert words in out
    assert asked == [] and writes(calls) == []


@pytest.mark.parametrize(
    ("kwargs", "words"),
    [({"icon": "Rocket"}, "belong to a space"), ({"timezone": "UTC"}, "settings of the space")],
)
async def test_the_routes_level_rules_are_said_before_the_card(monkeypatch, kwargs, words) -> None:
    """``tree.py``: an icon is a space's, the lifecycle policy is a root's."""
    asked = approve(monkeypatch)
    child = _node(parent=OTHER, name="Website")
    calls = fake_gateway(monkeypatch, _gateway({("GET", f"/projects/nodes/{PROJECT}"): child}))
    out = await skill_projects.update_project(PROJECT, **kwargs)
    assert words in out and asked == [] and writes(calls) == []


def test_the_icon_list_is_the_space_settings_list() -> None:
    source = (APP / "lib" / "tree.ts").read_text(encoding="utf-8")
    block = re.search(r"SPACE_ICON_CHOICES[^=]*=\s*\[(.*?)\];", source, re.S)
    assert block, "SPACE_ICON_CHOICES moved out of lib/tree.ts"
    assert tuple(re.findall(r'"([A-Za-z0-9]+)"', block.group(1))) == W.SPACE_ICONS


def test_the_slot_range_is_the_routes() -> None:
    pytest.importorskip("gateway.routes.projects", reason="gateway not installed")
    from gateway.routes.projects import core

    assert W.ICON_SLOT_RANGE == core.ICON_SLOT_RANGE
    assert set(W._ROOT_ONLY) == set(core.LIFECYCLE_FIELDS)


# ── G13: the order of a node among its siblings ─────────────────────────────


def _tree(*children: dict) -> dict:
    """A space PROJECT, its children, and a second top-level space."""
    return {"rows": [
        {"id": PROJECT, "name": "Product", "position": None, "children": list(children)},
        {"id": THIRD, "name": "Sales", "position": None, "children": []},
    ]}


def _kid(node_id: str, name: str, position: float | None) -> dict:
    return {"id": node_id, "name": name, "position": position, "children": []}


def _move_gateway(tree: dict, moving: str = CHILD):
    node = {"id": moving, "name": "Website", "parent_project_id": PROJECT}
    return _gateway({
        ("GET", "/projects/tree"): tree,
        ("GET", f"/projects/nodes/{moving}"): node,
        ("POST", f"/projects/nodes/{moving}/move"): {"id": moving},
    })


async def test_place_first_takes_half_the_first_siblings_position(monkeypatch) -> None:
    asked = approve(monkeypatch)
    tree = _tree(_kid(SIBLING, "Docs", 100.0), _kid(CHILD, "Website", 200.0))
    calls = fake_gateway(monkeypatch, _move_gateway(tree))
    out = await skill_projects.move_project(CHILD, place="first")
    sent = writes(calls)
    assert [c["json"] for c in sent] == [{"parent_project_id": PROJECT, "position": 50.0}]
    assert asked[0]["title"] == "Reorder this project?"
    assert "Placed «Website» first among 2 siblings under «Product»" in out


async def test_place_after_takes_the_midpoint(monkeypatch) -> None:
    approve(monkeypatch)
    tree = _tree(
        _kid(CHILD, "Website", 100.0), _kid(SIBLING, "Docs", 200.0), _kid(THIRD, "API", 400.0)
    )
    calls = fake_gateway(monkeypatch, _move_gateway(tree))
    await skill_projects.move_project(CHILD, place=f"after {SIBLING}")
    assert writes(calls)[0]["json"]["position"] == 300.0


async def test_an_unordered_set_is_spread_once_siblings_first(monkeypatch) -> None:
    """``treeDrop.ts``: a midpoint of two nulls is not a number, so the first
    reorder numbers the whole set, and the card says how many."""
    asked = approve(monkeypatch)
    tree = _tree(_kid(CHILD, "Website", None), _kid(SIBLING, "Docs", None))
    calls = fake_gateway(monkeypatch, _move_gateway(tree))
    await skill_projects.move_project(CHILD, place="last")
    sent = writes(calls)
    span = G.POSITION_SPAN
    assert [(c["path"], c["json"]["position"]) for c in sent] == [
        (f"/projects/nodes/{SIBLING}/move", span / 3),
        (f"/projects/nodes/{CHILD}/move", 2 * span / 3),
    ]
    assert "1 other sibling get a position too" in asked[0]["context"]


async def test_a_node_already_in_its_place_writes_nothing(monkeypatch) -> None:
    asked = approve(monkeypatch)
    tree = _tree(_kid(CHILD, "Website", 100.0), _kid(SIBLING, "Docs", 200.0))
    calls = fake_gateway(monkeypatch, _move_gateway(tree))
    out = await skill_projects.move_project(CHILD, place="first")
    assert "already first" in out and asked == [] and writes(calls) == []


async def test_a_place_beside_a_stranger_is_refused_with_the_siblings(monkeypatch) -> None:
    asked = approve(monkeypatch)
    tree = _tree(_kid(CHILD, "Website", 100.0), _kid(SIBLING, "Docs", 200.0))
    calls = fake_gateway(monkeypatch, _move_gateway(tree))
    out = await skill_projects.move_project(CHILD, place=f"before {THIRD}")
    assert "not a live sibling" in out and "«Docs»" in out
    assert asked == [] and writes(calls) == []


async def test_a_bad_place_word_is_refused(monkeypatch) -> None:
    calls = fake_gateway(monkeypatch, _move_gateway(_tree()))
    assert "place is first, last" in await skill_projects.move_project(CHILD, place="top")
    assert calls == []


async def test_a_declined_reorder_writes_nothing(monkeypatch) -> None:
    deny(monkeypatch)
    tree = _tree(_kid(CHILD, "Website", None), _kid(SIBLING, "Docs", None))
    calls = fake_gateway(monkeypatch, _move_gateway(tree))
    await skill_projects.move_project(CHILD, place="last")
    assert writes(calls) == []


def test_the_order_maths_is_the_tree_drags() -> None:
    source = (APP / "lib" / "treeDrop.ts").read_text(encoding="utf-8")
    assert re.search(rf"POSITION_SPAN = {G.POSITION_SPAN};", source)
    assert re.search(r"MIN_GAP = 1e-6;", source) and G.MIN_GAP == 1e-6


# ── G14: a saved view with its filters ──────────────────────────────────────


def _view_gateway(config: dict | None = None):
    rows = [{"id": VIEW, "name": "Board", "view_type": "board", "config": config or {}}]
    return _gateway({
        ("GET", f"/projects/nodes/{PROJECT}"): _node(),
        ("GET", f"/projects/nodes/{PROJECT}/views"): {"rows": rows},
        ("POST", f"/projects/nodes/{PROJECT}/views"): lambda c: {"id": VIEW, **c["json"]},
    })


async def test_a_view_is_saved_with_the_apps_own_filter_keys(monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _view_gateway())
    await skill_projects.save_view(
        PROJECT, "Priya's late work", view_type="board",
        filters=json.dumps({"overdue": True, "assignee": "Priya", "archived": True,
                            "tags": "q4, urgent", "query": "badge"}),
        group_by="assignee", subtasks="hidden",
    )
    body = _sent(calls, "POST", f"/projects/nodes/{PROJECT}/views")[0]["json"]
    assert body["config"] == {
        "filters": {"overdue": True, "assignee": "priya@x.io", "archived_only": True,
                    "tags": "q4,urgent", "q": "badge"},
        "group_by": "assignee",
        "subtasks": "hidden",
    }
    assert "group by: «assignee»" in asked[0]["context"]


async def test_a_view_with_no_settings_sends_no_config(monkeypatch) -> None:
    approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _view_gateway())
    await skill_projects.save_view(PROJECT, "Mine")
    assert "config" not in _sent(calls, "POST", f"/projects/nodes/{PROJECT}/views")[0]["json"]


async def test_a_changed_view_keeps_what_the_chat_did_not_change(monkeypatch) -> None:
    """The route REPLACES ``config``, so the chat merges first: a lane set and
    the shown columns survive a change of filters."""
    approve(monkeypatch)
    saved = {"filters": {"q": "old"}, "group_by": "status", "sub_group_by": "assignee",
             "shown_fields": ["status", "due_at"]}
    calls = fake_gateway(monkeypatch, _view_gateway(saved))
    await skill_projects.save_view(PROJECT, view_id=VIEW, filters='{"overdue": true}')
    body = _sent(calls, "PATCH", f"/projects/views/{VIEW}")[0]["json"]
    assert body == {"config": {"filters": {"overdue": True}, "group_by": "status",
                               "sub_group_by": "assignee", "shown_fields": ["status", "due_at"]}}


@pytest.mark.parametrize(
    ("kwargs", "words"),
    [
        ({"filters": '{"colour": "red"}'}, "A view filters on"),
        ({"filters": "overdue"}, "JSON object"),
        ({"filters": '{"overdue": "yes"}'}, "true or false"),
        ({"filters": '{"status_category": "todo,done"}'}, "for one view"),
        ({"filters": '{"assignee": "Priya, Asha"}'}, "one assignee"),
        ({"group_by": "colour"}, "group_by is one of"),
        ({"subtasks": "flat"}, "subtasks is one of"),
    ],
)
async def test_a_bad_view_setting_is_refused_before_the_card(monkeypatch, kwargs, words) -> None:
    from skill_projects.refusals import refusals_as_text

    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _view_gateway())
    out = await refusals_as_text(skill_projects.save_view)(PROJECT, "Mine", **kwargs)
    assert words in out and asked == [] and writes(calls) == []


async def test_the_route_keeps_every_key_the_chat_writes(monkeypatch) -> None:
    """THE FENCE for "the app opens the view": the route's own normaliser
    drops an unknown key in silence, so a key it drops is a lost filter."""
    pytest.importorskip("gateway.routes.projects", reason="gateway not installed")
    from gateway.routes.projects.filters import normalise_view_config

    approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _view_gateway())
    every = {"query": "badge", "status_category": "todo", "assignee": "priya@x.io",
             "unassigned": True, "overdue": True, "watching": True, "archived": True,
             "tags": "q4"}
    await skill_projects.save_view(
        PROJECT, "All", filters=json.dumps(every), group_by="tag", subtasks="nested"
    )
    config = _sent(calls, "POST", f"/projects/nodes/{PROJECT}/views")[0]["json"]["config"]
    assert normalise_view_config(config) == config


def test_the_filter_keys_are_the_apps_and_the_routes() -> None:
    pytest.importorskip("gateway.routes.projects", reason="gateway not installed")
    from gateway.routes.projects import filters

    source = (APP / "lib" / "grouping.ts").read_text(encoding="utf-8")
    body = source[source.index("export function toConfig"):]
    body = body[: body.index("const config")]
    assert set(re.findall(r"stored\.(\w+) =", body)) == set(I.VIEW_FILTERS.values())
    assert set(I.VIEW_FILTERS.values()) <= filters.VIEW_FILTER_KEYS
    assert I.GROUP_BY == filters.GROUP_BY
    assert I.SUBTASK_MODES == filters.SUBTASK_MODES
    assert I.STATUS_CATEGORIES == filters.STATUS_CATEGORIES


# ── G15: the member's own overlay ───────────────────────────────────────────


async def test_a_block_is_read_in_the_members_own_zone(monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _gateway())
    await skill_projects.set_my_overlay(
        TASK, block_start="2026-10-07 14:00", block_end="2026-10-07T15:30", flexible="no",
        hard_date="yes", deep_work="yes",
    )
    body = _sent(calls, "PATCH", f"/projects/tasks/{TASK}/personal")[0]["json"]
    assert body == {
        "scheduled_start": "2026-10-07T14:00:00+14:00",
        "scheduled_end": "2026-10-07T15:30:00+14:00",
        "flexible": False, "is_hard_date": True, "deep_work": True,
    }
    assert "your overlay only" in asked[0]["context"]


async def test_a_time_with_an_offset_is_kept_as_given(monkeypatch) -> None:
    approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _gateway())
    await skill_projects.set_my_overlay(TASK, actual_start="2026-10-07T09:00:00Z")
    body = _sent(calls, "PATCH", f"/projects/tasks/{TASK}/personal")[0]["json"]
    assert body == {"actual_start": "2026-10-07T09:00:00+00:00"}


async def test_waiting_on_stores_the_person_and_starts_the_clock(monkeypatch) -> None:
    approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _gateway())
    await skill_projects.set_my_overlay(
        TASK, disposition="waiting", waiting_on="Priya", expected_by="2026-10-09"
    )
    body = _sent(calls, "PATCH", f"/projects/tasks/{TASK}/personal")[0]["json"]
    assert body["waiting_on"] == {"name": "Priya Menon", "email": "priya@x.io"}
    assert body["expected_by"] == "2026-10-09"
    # Migration 188: a chase has a since-when.
    assert dt.datetime.fromisoformat(body["delegated_at"]).tzinfo is not None


async def test_a_given_since_when_wins(monkeypatch) -> None:
    approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _gateway())
    await skill_projects.set_my_overlay(TASK, waiting_on="priya@x.io", waiting_since="2026-10-01")
    body = _sent(calls, "PATCH", f"/projects/tasks/{TASK}/personal")[0]["json"]
    assert body["delegated_at"] == "2026-10-01"


async def test_clear_empties_the_new_overlay_fields(monkeypatch) -> None:
    approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _gateway())
    await skill_projects.set_my_overlay(TASK, clear="block, waiting_on, deep_work")
    assert _sent(calls, "PATCH", f"/projects/tasks/{TASK}/personal")[0]["json"] == {
        "scheduled_start": None, "scheduled_end": None, "waiting_on": None, "deep_work": None,
    }


@pytest.mark.parametrize(
    ("kwargs", "words"),
    [
        ({"block_start": "2026-10-07 15:00", "block_end": "2026-10-07 14:00"}, "after"),
        ({"block_start": "2026-10-07"}, "a date and a time"),
        ({"block_start": "tomorrow 3pm"}, "a date and a time"),
        ({"expected_by": "Friday"}, "expected_by is a date"),
        ({"deep_work": "maybe"}, "yes or no"),
        ({"waiting_on": "agent:crm-assistant"}, "is a person"),
        ({"clear": "estimate"}, "clear takes"),
    ],
)
async def test_a_bad_overlay_value_is_refused_before_the_card(monkeypatch, kwargs, words) -> None:
    from skill_projects.refusals import refusals_as_text

    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _gateway())
    out = await refusals_as_text(skill_projects.set_my_overlay)(TASK, **kwargs)
    assert words in out and asked == [] and writes(calls) == []


async def test_a_block_end_is_judged_against_the_stored_start(monkeypatch) -> None:
    """The route judges the MERGED row (``_reject_impossible_block``)."""
    asked = approve(monkeypatch)
    stored = {"id": TASK, "scheduled_start": "2026-10-07T16:00:00+14:00", "scheduled_end": None}
    calls = fake_gateway(
        monkeypatch, _gateway({("GET", f"/projects/my/tasks/{TASK}"): stored})
    )
    out = await skill_projects.set_my_overlay(TASK, block_end="2026-10-07 15:00")
    assert "after block_start" in out and asked == [] and writes(calls) == []


# ── G16: the overlay over a selection ───────────────────────────────────────


async def test_bulk_personal_sends_the_overlay_action_alone(monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _gateway({
        ("POST", "/projects/tasks/bulk"): {"requested": 2, "applied": 2, "results": []},
    }))
    out = await skill_projects.bulk_update(
        f"{TASK},{OTHER}", personal='{"disposition": "next", "context": "@desk"}'
    )
    body = _sent(calls, "POST", "/projects/tasks/bulk")[0]["json"]
    assert body == {"task_ids": [TASK, OTHER], "action": "personal",
                    "personal": {"disposition": "NEXT", "context": "@desk"}}
    context = asked[0]["context"]
    assert "#7" in context and "#8" in context
    # D77: an actionable disposition reopens the finished #8 for the board.
    assert "1 finished task reopen on the board" in context
    assert "Your triage is set on 2 of 2 tasks" in out


@pytest.mark.parametrize(
    ("kwargs", "words"),
    [
        ({"personal": '{"disposition": "someday"}', "status": "done"}, "goes on its own"),
        ({"personal": '{"disposition": "done"}'}, "DONE is not your own triage"),
        ({"personal": '{"colour": "red"}'}, "The overlay takes"),
        ({"personal": "someday"}, "JSON object"),
        ({"personal": "{}"}, "Nothing to change"),
    ],
)
async def test_a_bad_bulk_overlay_is_refused_before_the_card(monkeypatch, kwargs, words) -> None:
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _gateway())
    out = await skill_projects.bulk_update(f"{TASK},{OTHER}", **kwargs)
    assert words in out and asked == [] and writes(calls) == []


def test_the_single_tool_and_the_bulk_take_the_same_overlay_words() -> None:
    """One builder, so the two cannot drift. Each argument of the overlay
    builder is an argument of ``set_my_overlay``."""
    import inspect

    params = set(inspect.signature(skill_projects.set_my_overlay).parameters)
    assert set(W.OVERLAY_ARGUMENTS) <= params


# ── G17 and G18: the member's areas, and what landed on their plate ─────────


async def test_my_areas_lists_each_area_with_its_open_work(monkeypatch) -> None:
    calls = fake_gateway(monkeypatch, _gateway({("GET", "/projects/my/areas"): {"rows": [
        {"id": PROJECT, "name": "Home", "archived": False, "open_tasks": 3},
        {"id": OTHER, "name": "Old flat", "archived": True, "open_tasks": 0},
    ]}}))
    out = await skill_projects.my_areas(include_archived=True)
    assert f"- «Home» · 3 open · project_id {PROJECT}" in out
    assert "«Old flat» · 0 open · archived" in out
    assert calls[0]["params"] == {"include_archived": True}


async def test_untriaged_reads_the_inbox_and_names_who_assigned(monkeypatch) -> None:
    row = {"id": TASK, "task_number": 7, "title": "Fix the extruder", "assigned_by": "raj@x.io"}
    calls = fake_gateway(monkeypatch, _gateway({
        ("GET", "/projects/my/inbox"): {"rows": [row], "total": 1},
    }))
    out = await skill_projects.my_work(untriaged=True)
    inbox = [c for c in calls if c["path"] == "/projects/my/inbox"]
    assert inbox and inbox[0]["params"]["untriaged"] is True
    assert "Landed on my plate" in out and "assigned_by «raj@x.io»" in out


# ── The route: GET /projects/my/today ───────────────────────────────────────


def test_zone_name_reports_utc_for_a_zone_it_cannot_read() -> None:
    pytest.importorskip("gateway.routes.projects", reason="gateway not installed")
    from gateway.routes.projects.personal import local_date, zone_name

    assert zone_name("Asia/Kolkata") == "Asia/Kolkata"
    assert zone_name(None) == "UTC" and zone_name("Mars/Olympus") == "UTC"
    at = dt.datetime(2026, 10, 6, 20, 0, tzinfo=dt.UTC)
    assert local_date("Asia/Kolkata", at) == WEDNESDAY
    assert local_date("Mars/Olympus", at) == TUESDAY


# ── R8 — the read of the member's zone, on asyncpg, under FORCE RLS ─────────
#
# ``user_settings`` takes its tenancy from the generated phases
# (``infra/postgres/generated/``), which the plain ladder never replays. So
# this half reuses the H3 rehearsal's fixture: a sibling database with the
# ladder and the four phases, and a role that is not a superuser, not the
# owner and not BYPASSRLS. A superuser passes every policy, so a test as the
# ladder's own role would prove nothing about the tenant.

from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: E402
    _DB_GATE,
    promoted,  # noqa: F401 — a fixture
)

EMAIL = "zone@p7.test"


@pytest.fixture
def zoned(promoted):  # noqa: F811
    """The member's saved zone, in org A only, seeded as the admin."""
    from sqlalchemy import text

    with promoted.admin_engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO user_settings (user_id, organization_id, timezone)"
                " VALUES (:u, CAST(:o AS uuid), 'Pacific/Kiritimati')"
            ),
            {"u": EMAIL, "o": promoted.org_a},
        )
    yield promoted
    with promoted.admin_engine.begin() as c:
        c.execute(text("DELETE FROM user_settings WHERE user_id = :u"), {"u": EMAIL})


def _app_engine(ns: Any):
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    return create_async_engine(
        ns.app_url.set(drivername="postgresql+asyncpg"), future=True, poolclass=NullPool
    )


async def _zone_as(ns: Any, tenant: str | None) -> Any:
    """``stored_zone`` as the app role, bound to *tenant* the way the gateway
    session binds ``app.tenant_id``. ``None`` leaves the session unbound."""
    from gateway.routes.projects.personal import stored_zone
    from sqlalchemy import text

    eng = _app_engine(ns)
    try:
        async with eng.begin() as conn:
            if tenant is not None:
                await conn.execute(
                    text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tenant}
                )
            return await stored_zone(conn, EMAIL)
    finally:
        await eng.dispose()


@_DB_GATE
async def test_r8_the_zone_is_read_inside_the_tenant(zoned) -> None:
    pytest.importorskip("asyncpg")
    assert await _zone_as(zoned, zoned.org_a) == "Pacific/Kiritimati"


@_DB_GATE
async def test_r8_another_tenant_reads_no_zone(zoned) -> None:
    """THE R8 FENCE for R5: the read sees only the bound tenant's row."""
    pytest.importorskip("asyncpg")
    assert await _zone_as(zoned, zoned.org_b) is None
    assert await _zone_as(zoned, None) is None


@_DB_GATE
async def test_r8_the_route_answers_the_members_own_date(zoned, monkeypatch) -> None:
    """``GET /projects/my/today`` end to end, on the real row, as the app role."""
    pytest.importorskip("asyncpg")
    from contextlib import asynccontextmanager
    from zoneinfo import ZoneInfo

    from gateway.routes.projects import personal
    from sqlalchemy import text

    from tests.unit._projects_fakes import projects_user

    eng = _app_engine(zoned)

    @asynccontextmanager
    async def _session(*_a: Any, **_k: Any):
        async with eng.begin() as conn:
            await conn.execute(
                text("SELECT set_config('app.tenant_id', :t, true)"), {"t": zoned.org_a}
            )
            yield conn

    monkeypatch.setattr(personal, "_tenant_session", _session)
    try:
        out = await personal.my_today(user=projects_user(EMAIL))
    finally:
        await eng.dispose()
    here = dt.datetime.now(ZoneInfo("Pacific/Kiritimati")).date().isoformat()
    assert out == {"today": here, "timezone": "Pacific/Kiritimati", "stored": True}
