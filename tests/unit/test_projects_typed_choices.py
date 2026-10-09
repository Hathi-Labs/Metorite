"""Projects' small choices on the ``decide`` engine. Owner, 2026-10-09.

Spec: ``project-docs/specs/ai_tier_routing.md`` §6.1 and
``data_narrowing_pipeline.md`` Q4 (the boxes of 2026-10-09).

An audit found 60 of 205 Projects tool calls were small choices, and each
one cost a main-model round of about 45k tokens. Two now run inside the tool
as ONE typed question (``skill_projects.choices``):

1. a name with no exact match (a status, a type, a person, a project) asks one
   ``choice`` question over the close names plus ``unsure``. ``unsure``, a low
   confidence or a failure keeps today's refusal, with the real names;
2. a create checks the project's open tasks for a twin with one ``yes_no``
   question for each close pair. A sure ``yes`` flags the row, and blocks
   nothing.

The run is a covered, ``no_egress`` projects-assistant run, so this file also
proves the owner's other decision of that day: the typed question reaches
``tier-decide``. The requests go through the REAL facade and the REAL
System-1 client. Only the HTTP transports are scripts (the harness of
``test_system_one_tool.py``), and the Projects gateway is the fake of
``_projects_agent_fakes.py``.

Hermetic: no SQL changes and none runs here, so R8 binds nothing.

Mutations this file catches (R7), each run red on 2026-10-09 and then taken
back out:

* ``_named`` stops asking (raises at once) ->
  ``test_an_inexact_status_is_resolved_by_one_decide_question``;
* ``resolve_name`` ignores ``unsure`` or the threshold ->
  ``test_unsure_keeps_the_refusal_with_the_names`` and
  ``test_a_low_confidence_keeps_the_refusal``;
* the assist runs for an agent the flag does not cover ->
  ``test_with_the_flag_unset_no_question_goes_out``;
* the twin flag stops reaching the card, or blocks the create ->
  ``test_a_twin_is_flagged_and_the_task_is_still_made``;
* a description reaches the decide request ->
  ``test_no_full_body_reaches_the_engine``;
* two close people are not offered as options ->
  ``test_one_of_several_close_people_is_resolved``;
* the card of a project resolved by name does not name it (review P1) ->
  ``test_the_card_names_a_project_resolved_by_name``;
* the twin context passes the short bound (review P1) ->
  ``test_the_twin_context_keeps_the_short_bound``.
"""
from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import structlog

pytest.importorskip("skill_projects", reason="skill-projects not installed")

import skill_projects.client as client
from acb_common import bind_run_context, clear_run_context
from acb_common.settings import get_settings
from acb_skills.write_artifact import bind_artifact_context
from skill_projects import choices
from skill_projects import forms as F
from skill_projects import writes as W

from tests.unit._projects_agent_fakes import approve, fake_gateway, writes
from tests.unit.test_system_one_tool import _door, _fast_ok, _verdict, _wire

PA = "projects-assistant"
PID = "0f8fad5b-d9cb-469f-a165-70867728950e"
WEB = "1f8fad5b-d9cb-469f-a165-70867728950e"
S1 = "3f8fad5b-d9cb-469f-a165-70867728950e"
S2 = "4f8fad5b-d9cb-469f-a165-70867728950e"
S3 = "5f8fad5b-d9cb-469f-a165-70867728950e"
OLD = "6f8fad5b-d9cb-469f-a165-70867728950e"
NEW = "7f8fad5b-d9cb-469f-a165-70867728950e"
STATUSES = [
    {"id": S1, "name": "To do", "category": "todo"},
    {"id": S2, "name": "In progress", "category": "in_progress"},
    {"id": S3, "name": "Done", "category": "done"},
]
#: A description the member wrote. It must never reach the engine.
BODY_CANARY = "BODY-CANARY-5521 the whole contract text for Acme"
OPEN_TASK = {"id": OLD, "title": "Book the venue for the offsite", "task_number": 12,
             "status_id": S1, "project_id": PID}


@pytest.fixture(autouse=True)
def _covered_run(monkeypatch):
    """A covered, ``no_egress`` projects run, with the decide door on."""
    monkeypatch.setenv("AI_TIER_ROUTING", PA)
    monkeypatch.setenv("ROUTER_SERVING_ENABLED", "1")
    monkeypatch.setenv("CUSTOMER_CONSOLE_URL", "https://console.test")
    monkeypatch.setenv("CUSTOMER_CONSOLE_ORG_KEY", "cc_live_fixture_notarealsecret")
    monkeypatch.delenv("CUSTOMER_CONSOLE_DEPLOYMENT_KEY", raising=False)
    monkeypatch.setenv("CUSTOMER_CONSOLE_ROUTER_USES_DEPLOYMENT_KEY", "false")
    monkeypatch.setenv("SYSTEM_ONE_ON_DECIDE", "true")
    monkeypatch.setenv("DECIDE_ENABLED", "true")
    monkeypatch.delenv("DECIDE_IN_NO_EGRESS", raising=False)
    get_settings.cache_clear()
    clear_run_context()
    bind_run_context(run_id="run-pc", agent=PA, user="pm@fracktal.in", source="chat")
    bind_artifact_context(agent_name=PA, run_id="run-pc", think_mode="auto", no_egress=True)
    yield
    clear_run_context()
    bind_artifact_context()
    get_settings.cache_clear()


def _gateway(*, people: list[dict] | None = None, open_tasks: list[dict] | None = None):
    def responder(call: dict) -> Any:
        path, method = call["path"], call["method"]
        if path.endswith("/statuses"):
            return {"rows": STATUSES}
        if path == "/projects/tree":
            return {"rows": [{"id": PID, "name": "Ops", "children": [
                {"id": WEB, "name": "Website relaunch", "children": []},
                {"id": "8f8fad5b-d9cb-469f-a165-70867728950e", "name": "Archive",
                 "kind": "folder", "children": []},
            ]}]}
        if path == "/projects/assignees":
            return {"people": people or [], "agents": []}
        if path == "/projects/people/names":
            return {"names": {p["assignee"]: p["name"] for p in people or []}}
        if path == "/projects/tasks" and method == "GET":
            return {"rows": open_tasks or [], "total": len(open_tasks or [])}
        if path == "/projects/tasks" and method == "POST":
            body = call["json"] or {}
            return {"id": NEW, "task_number": 30, "title": body.get("title"),
                    "status_id": body.get("status_id", S1), "project_id": body.get("project_id")}
        if path.startswith("/projects/nodes/"):
            return {"id": PID, "name": "Ops", "kind": "project"}
        return {"rows": [], "total": 0}

    return responder


def _choice(name: str, confidence: float = 0.9):
    return lambda b: _verdict(b, choice={"choice": name, "confidence": confidence})


def _options(door) -> list[str]:
    return list(door.bodies[0]["questions"]["name"]["criteria"])


# ── 1. A name the model gave ─────────────────────────────────────────────────


async def test_an_inexact_status_is_resolved_by_one_decide_question(monkeypatch) -> None:
    calls = fake_gateway(monkeypatch, _gateway())
    cards = approve(monkeypatch)
    door = _door(monkeypatch, _choice("In progress"))
    wire = _wire(monkeypatch, _fast_ok)
    out = await W.create_task(project_id=PID, title="Order filament", status="in progres")
    assert len(door.requests) == 1 and wire.requests == []
    assert _options(door) == ["In progress", "unsure"]
    assert json.loads(door.bodies[0]["state"]) == {"name": "in progres", "kind": "status"}
    posted = [c for c in writes(calls) if c["path"] == "/projects/tasks"]
    assert posted and posted[0]["json"]["status_id"] == S2
    assert "In progress" in cards[0]["detail"]
    assert "Order filament" in out


async def test_unsure_keeps_the_refusal_with_the_names(monkeypatch) -> None:
    calls = fake_gateway(monkeypatch, _gateway())
    approve(monkeypatch)
    door = _door(monkeypatch, _choice("unsure"))
    _wire(monkeypatch, _fast_ok)
    with pytest.raises(client.GatewayRefusal, match="«To do», «In progress», «Done»"):
        await W.create_task(project_id=PID, title="Order filament", status="in progres")
    assert len(door.requests) == 1
    assert writes(calls) == []


async def test_a_low_confidence_keeps_the_refusal(monkeypatch) -> None:
    calls = fake_gateway(monkeypatch, _gateway())
    approve(monkeypatch)
    _door(monkeypatch, _choice("In progress", confidence=0.55))
    _wire(monkeypatch, _fast_ok)
    with pytest.raises(client.GatewayRefusal, match="No status is called"):
        await W.create_task(project_id=PID, title="Order filament", status="in progres")
    assert writes(calls) == []


async def test_a_failure_of_both_engines_keeps_the_refusal(monkeypatch) -> None:
    fake_gateway(monkeypatch, _gateway())
    approve(monkeypatch)
    _door(monkeypatch, lambda _b: httpx.Response(503, json={}))
    _wire(monkeypatch, lambda _b: httpx.Response(503, json={}))
    with pytest.raises(client.GatewayRefusal, match="No status is called"):
        await W.create_task(project_id=PID, title="Order filament", status="in progres")


async def test_with_the_flag_unset_no_question_goes_out(monkeypatch) -> None:
    monkeypatch.delenv("AI_TIER_ROUTING", raising=False)
    get_settings.cache_clear()
    calls = fake_gateway(monkeypatch, _gateway(open_tasks=[OPEN_TASK]))
    approve(monkeypatch)
    door = _door(monkeypatch, _choice("In progress"))
    wire = _wire(monkeypatch, _fast_ok)
    with pytest.raises(client.GatewayRefusal, match="No status is called"):
        await W.create_task(project_id=PID, title="Order filament", status="in progres")
    assert door.requests == [] and wire.requests == []
    # No twin read either: the tool reads exactly as before.
    assert not [c for c in calls if c["path"] == "/projects/tasks" and c["method"] == "GET"]


async def test_two_rows_with_the_same_name_stay_a_question_for_the_member(
    monkeypatch,
) -> None:
    rows = [{"id": S1, "name": "Review"}, {"id": S2, "name": "review"}]
    door = _door(monkeypatch, _choice("Review"))
    with pytest.raises(client.GatewayRefusal, match="more than one"):
        await W._named(rows, "Review", "status", "statuses")
    assert door.requests == []


async def test_one_of_several_close_people_is_resolved(monkeypatch) -> None:
    people = [
        {"assignee": "priya.s@x.io", "name": "Priya Sharma"},
        {"assignee": "priya.n@x.io", "name": "Priya Nair"},
    ]
    fake_gateway(monkeypatch, _gateway(people=people))
    door = _door(monkeypatch, _choice("Priya Sharma"))
    _wire(monkeypatch, _fast_ok)
    assert await W._resolve_assignee("Priya Sharm") == "priya.s@x.io"
    assert _options(door) == ["Priya Sharma", "Priya Nair", "unsure"]
    # No address leaves: the options are names.
    assert "@" not in json.dumps(door.bodies[0])


async def test_a_project_name_resolves(monkeypatch) -> None:
    fake_gateway(monkeypatch, _gateway())
    door = _door(monkeypatch, _choice("Website relaunch"))
    _wire(monkeypatch, _fast_ok)
    assert await W._project_id("website relanch") == WEB
    assert _options(door) == ["Website relaunch", "unsure"]
    # An exact name needs no question, and a folder is never a match.
    assert await W._project_id("Ops") == PID
    assert len(door.requests) == 1
    with pytest.raises(client.GatewayRefusal, match="must be a UUID"):
        await W._project_id("Archive")


async def test_the_card_names_a_project_resolved_by_name(monkeypatch) -> None:
    """Review P1: a card that names the project only by its id is not
    consent to the project that a model chose."""
    calls = fake_gateway(monkeypatch, _gateway())
    cards = approve(monkeypatch)
    _door(monkeypatch, _choice("Website relaunch"))
    _wire(monkeypatch, _fast_ok)
    await W.create_task(project_id="website relanch", title="Order filament")
    assert cards[0]["context"].splitlines()[1] == "project: «Website relaunch»"
    assert "project_id" not in cards[0]["context"]
    assert "in «Website relaunch»" in cards[0]["detail"]
    posted = [c for c in writes(calls) if c["path"] == "/projects/tasks"]
    assert posted[0]["json"]["project_id"] == WEB


async def test_a_uuid_project_keeps_the_card_of_before(monkeypatch) -> None:
    fake_gateway(monkeypatch, _gateway())
    cards = approve(monkeypatch)
    _door(monkeypatch, _choice("In progress"))
    _wire(monkeypatch, _fast_ok)
    await W.create_task(project_id=PID, title="Order filament")
    assert "project: " not in cards[0]["context"]


# ── 2. A twin before a create ────────────────────────────────────────────────


def _many_twins() -> tuple[list[str], list[dict[str, Any]]]:
    titles = [f"Prepare the quarterly supplier review pack number {n} " + "x" * 40
              for n in range(10)]
    existing = [{"id": f"id-{n}", "title": t, "task_number": n, "status_id": S1}
                for n, t in enumerate(titles)]
    return titles, existing


async def test_the_twin_context_keeps_the_short_bound(monkeypatch) -> None:
    """A ``no_egress`` run (the fixture) trims to the bound, through the
    REAL ``twin_flags``, and asks at least one pair."""
    from acb_skills import decide_tools

    titles, existing = _many_twins()
    assert len(choices.twin_pairs(titles, existing)) == choices.TWIN_PAIRS_MAX
    fake_gateway(monkeypatch, _gateway(open_tasks=existing))
    door = _door(monkeypatch, lambda b: _verdict(b, boolean={"probability": 0.1}))
    _wire(monkeypatch, _fast_ok)
    # A fresh proxy: a cached logger can hide a line from capture_logs.
    monkeypatch.setattr(choices, "_log", structlog.get_logger("skill_projects.choices"))
    with structlog.testing.capture_logs() as logs:
        await choices.twin_flags(client.get, PID, titles)
    assert len(door.requests) == 1
    assert decide_tools.short_context(door.bodies[0]["state"])
    assert 1 <= len(door.bodies[0]["questions"]) < choices.TWIN_PAIRS_MAX
    line = next(e for e in logs if e.get("purpose") == "twin")
    assert line["dropped"] == choices.TWIN_PAIRS_MAX - line["pairs"] > 0


async def test_an_open_run_asks_every_twin_pair(monkeypatch) -> None:
    """Review P1 (after #760): the bound is the ``no_egress`` rule. A run
    that may send data off the platform asks every pair."""
    bind_artifact_context(agent_name=PA, run_id="run-pc", think_mode="auto", no_egress=False)
    titles, existing = _many_twins()
    fake_gateway(monkeypatch, _gateway(open_tasks=existing))
    door = _door(monkeypatch, lambda b: _verdict(b, boolean={"probability": 0.1}))
    _wire(monkeypatch, _fast_ok)
    await choices.twin_flags(client.get, PID, titles)
    assert [len(b["questions"]) for b in door.bodies] == [choices.TWIN_PAIRS_MAX]


def test_each_title_keeps_its_best_pair_before_a_second_one() -> None:
    titles = ["Book the venue", "Print the badges"]
    existing = [
        {"id": "a", "title": "Book the venue", "task_number": 1},
        # Row 0's SECOND pair (0.875) scores above row 1's best (0.81).
        {"id": "b", "title": "Book the venue now", "task_number": 2},
        {"id": "c", "title": "Print all badges", "task_number": 3},
    ]
    pairs = choices.twin_pairs(titles, existing)
    assert [(i, r["id"]) for i, r in pairs][:2] == [(0, "a"), (1, "c")]


async def test_a_twin_is_flagged_and_the_task_is_still_made(monkeypatch) -> None:
    calls = fake_gateway(monkeypatch, _gateway(open_tasks=[OPEN_TASK]))
    cards = approve(monkeypatch)
    door = _door(monkeypatch, lambda b: _verdict(b, boolean={"probability": 0.92}))
    _wire(monkeypatch, _fast_ok)
    out = await W.create_task(project_id=PID, title="Book venue for offsite")
    assert len(door.requests) == 1
    question = next(iter(door.bodies[0]["questions"].values()))
    assert question["type"] == "boolean"
    assert "may be the same task as #12" in cards[0]["context"]
    assert [c["path"] for c in writes(calls)] == ["/projects/tasks"]
    assert "may be the same task as #12" in out


async def test_a_no_flags_nothing(monkeypatch) -> None:
    fake_gateway(monkeypatch, _gateway(open_tasks=[OPEN_TASK]))
    cards = approve(monkeypatch)
    _door(monkeypatch, lambda b: _verdict(b, boolean={"probability": 0.1}))
    _wire(monkeypatch, _fast_ok)
    out = await W.create_task(project_id=PID, title="Book venue for offsite")
    assert "same task" not in cards[0]["context"] and "same task" not in out


async def test_no_close_title_sends_no_question(monkeypatch) -> None:
    fake_gateway(monkeypatch, _gateway(open_tasks=[OPEN_TASK]))
    approve(monkeypatch)
    door = _door(monkeypatch, lambda b: _verdict(b))
    wire = _wire(monkeypatch, _fast_ok)
    await W.create_task(project_id=PID, title="Calibrate the printer bed")
    assert door.requests == [] and wire.requests == []


async def test_no_full_body_reaches_the_engine(monkeypatch) -> None:
    fake_gateway(monkeypatch, _gateway(open_tasks=[{**OPEN_TASK, "description": BODY_CANARY}]))
    approve(monkeypatch)
    door = _door(monkeypatch, lambda b: _verdict(b, boolean={"probability": 0.92}))
    wire = _wire(monkeypatch, _fast_ok)
    await W.create_task(project_id=PID, title="Book venue for offsite",
                        description=BODY_CANARY, status="in progres")
    sent = json.dumps([*door.bodies, *wire.bodies])
    states = [json.loads(b["state"]) for b in door.bodies]
    # Both questions went out: the status name, and the twin check.
    assert [sorted(s) for s in states] == [["kind", "name"], ["new", "open"]]
    assert "BODY-CANARY" not in sent
    assert states[1]["new"] == {"n1": "Book venue for offsite"}
    assert states[1]["open"] == {"o1": {"title": OPEN_TASK["title"], "number": "#12"}}


async def test_create_tasks_flags_a_row_and_keeps_it_ticked(monkeypatch) -> None:
    fake_gateway(monkeypatch, _gateway(open_tasks=[OPEN_TASK]))
    cards = approve(monkeypatch)
    _door(monkeypatch, lambda b: _verdict(b, boolean={"probability": 0.92}))
    _wire(monkeypatch, _fast_ok)
    tasks = json.dumps([{"title": "Book venue for offsite"}, {"title": "Print the badges"}])
    await F.create_tasks(project_id=PID, tasks=tasks)
    rows = cards[0]["rows"]
    assert "may be the same task as #12" in rows[0]["hint"]
    assert rows[0]["checked"] is True
    assert "same task" not in rows[1]["hint"]


async def test_the_logs_hold_no_tenant_text(monkeypatch) -> None:
    from acb_skills import decide_tools

    # A fresh logger proxy for each module that logs here. configure_logging
    # sets cache_logger_on_first_use, and a cached logger keeps the processor
    # list of THAT configure call. So a `_log` that an earlier test used,
    # followed by a second configure_logging (test_observability.py), hides
    # every line from capture_logs. The idiom of test_permission_policy.py.
    monkeypatch.setattr(choices, "_log", structlog.get_logger("skill_projects.choices"))
    monkeypatch.setattr(decide_tools, "_log", structlog.get_logger("acb_skills.decide_tools"))
    fake_gateway(monkeypatch, _gateway(open_tasks=[OPEN_TASK]))
    approve(monkeypatch)
    _door(monkeypatch, lambda b: _verdict(b, boolean={"probability": 0.92}))
    _wire(monkeypatch, _fast_ok)
    with structlog.testing.capture_logs() as logs:
        await W.create_task(project_id=PID, title="Book venue for offsite")
    text = repr(logs)
    assert "venue" not in text and "offsite" not in text
    assert any(e["event"] == "projects.typed_choice" for e in logs)


def test_the_close_rows_rule() -> None:
    rows = [{"name": "In progress"}, {"name": "To do"}, {"name": "Done"}]
    assert [r["name"] for r in choices.close_rows(rows, "in progres")] == ["In progress"]
    assert choices.close_rows(rows, "zzz") == []
    assert choices.close_rows(rows, "") == []
