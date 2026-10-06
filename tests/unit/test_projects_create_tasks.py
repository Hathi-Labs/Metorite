"""WS-46 P13 — several new tasks in one project, as ONE batch (``create_tasks``).

Spec: ``project-docs/specs/projects_agent_parity.md`` §12, slice P13. D91.

The production failure of 2026-10-06: a member asked for nine tasks from an
email in a project that existed. No tool took several top-level tasks, so the
model made nine ``create_task`` calls at once, each with its own card. This
file holds the tool that replaces those nine calls. Each rule names the
mutation that turns it red (R7):

1. **Every name is resolved before any card,** with create_task's own code.
   One bad row refuses the whole batch and names the row. Mutation: let a
   bad row through to the card -> ``test_one_bad_row_refuses_the_batch...``.
2. **One selection card, then ONE confirmation card.** Each row is a
   checkbox, ticked by default, with the assignee NAMES, the due date and the
   status in plain words, and no id. Mutation: write every row whatever the
   form says -> ``test_an_unticked_row_is_not_written``.
3. **The write path is create_task's.** A row sends the POST body that
   create_task sends for the same words, then the same assign PUT.
   Mutation: build the body by hand and drop a key -> F2 on the wire
   (``test_a_batch_that_drops_a_field_fails_the_wire_check``) and
   ``test_a_row_sends_what_create_task_sends``.
4. **A row that fails does not stop the others, and nothing raises after
   the first write.** The receipt lists each task made and each row that
   failed, with a ``stopped:`` line. Mutation: ``break`` at the first failure
   -> ``test_a_row_the_server_refuses_does_not_stop_the_others``.
5. **A retry makes no second copy.** The create route takes no idempotency
   key (``TaskIn``), so the tool reads the project's newest tasks before the
   card. A task with the same title made in the last ``RECENT_MINUTES``
   starts unticked. Mutation: tick every row by default ->
   ``test_a_retry_does_not_duplicate``.
6. **The row keys stay in step with create_task.** Mutation: a new
   create_task argument with no decision -> ``test_the_row_keys_are_create_tasks_arguments``.

R8, at the end: the batch and its retry through the REAL create and list
routes, on asyncpg, over the tenant ladder. ⚠️ It SKIPS without
``TENANT_LADDER_DATABASE_URL``, and a skip is not a pass.
"""

from __future__ import annotations

import inspect
import itertools
import json
import os
import re
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

pytest.importorskip("skill_projects", reason="skill-projects not installed")

import skill_projects
import skill_projects.client as client
from skill_projects import forms as F
from skill_projects import manifest as m
from skill_projects import writes as W

from tests.unit import test_projects_agent_writes as tw
from tests.unit._projects_agent_fakes import (
    FakeClient,
    FakeResponse,
    approve,
    deny,
    fake_gateway,
    form_stub,
    writes,
)

UUID, OTHER = tw.UUID, tw.OTHER
_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
ALL_TICKED = 'Review tasks — {"row_1": true, "row_2": true, "row_3": true}'
THREE = json.dumps(
    [
        {"title": "Book the caterer", "assignees": "Priya", "due": "2026-10-09"},
        {"title": "Print the badges", "status": "in progress"},
        {"title": "Test the projector", "tags": "av", "estimate_mins": 30},
    ]
)


@pytest.fixture(autouse=True)
def _an_open_run() -> Any:
    from acb_skills.write_artifact import artifact_context_scope, bind_artifact_context

    with artifact_context_scope():
        bind_artifact_context(agent_name="projects-assistant", no_egress=False)
        yield


def _gateway(
    *,
    recent: list[dict[str, Any]] | None = None,
    refuse: tuple[str, ...] = (),
    drop: tuple[str, ...] = (),
    boom: tuple[str, ...] = (),
    refuse_assign: bool = False,
):
    """The writes fence's gateway, with numbered creates and failing rows.

    ``recent`` is what ``GET /projects/tasks`` lists. A title in ``refuse``
    gets a 422, in ``drop`` a lost connection, in ``boom`` an error no
    gateway sends.
    """
    numbers = itertools.count(20)

    def answer(call: dict) -> Any:
        path, method = call["path"], call["method"]
        if method == "GET" and path == "/projects/tasks":
            return {"rows": list(recent or []), "total": len(recent or [])}
        if method == "POST" and path == "/projects/tasks":
            title = call["json"]["title"]
            if title in refuse:
                return FakeResponse({"detail": "That type is not in this project."}, 422)
            if title in drop:
                raise httpx.ConnectError("the gateway went away")
            if title in boom:
                raise KeyError("not a gateway error")
            n = next(numbers)
            return {
                **tw.TASK,
                "id": str(uuid.uuid5(uuid.NAMESPACE_URL, title)),
                "title": title,
                "task_number": n,
                "status_id": call["json"].get("status_id", tw.S1),
                "due_at": call["json"].get("due_at"),
                "assignees": [],
                "archived_at": None,
            }
        if refuse_assign and method == "PUT" and path.endswith("/assignees"):
            return FakeResponse({"detail": "Not permitted."}, 403)
        return tw.responder(call)

    return answer


def _posts(calls: list[dict]) -> list[dict]:
    return [c for c in writes(calls) if c["method"] == "POST" and c["path"] == "/projects/tasks"]


# ── 1. Every name before any card, and one bad row refuses the batch ────────


async def test_three_good_rows_make_one_form_one_card_and_three_tasks(monkeypatch) -> None:
    asked = approve(monkeypatch)
    drawn = form_stub(monkeypatch, {"Add ": ALL_TICKED})
    calls = fake_gateway(monkeypatch, _gateway())
    out = await skill_projects.create_tasks(UUID, THREE)

    assert len(drawn) == 1 and len(asked) == 1, (drawn, asked)
    posts = _posts(calls)
    assert [p["json"]["title"] for p in posts] == [
        "Book the caterer",
        "Print the badges",
        "Test the projector",
    ]
    assert all(p["json"]["project_id"] == UUID for p in posts)
    assert posts[0]["json"]["due_at"] == "2026-10-09"
    assert posts[1]["json"]["status_id"] == tw.S2
    assert posts[2]["json"]["tags"] == ["av"] and posts[2]["json"]["estimate_mins"] == 30
    puts = [c for c in writes(calls) if c["method"] == "PUT"]
    assert len(puts) == 1 and puts[0]["json"] == {"assignees": ["priya@x.io"]}
    assert out.startswith("Created 3 tasks in «Ops»:")
    assert out.count("full_id:") == 3 and "#20" in out and "#22" in out
    assert "stopped:" not in out and "failed:" not in out


async def test_every_call_before_the_card_is_a_read(monkeypatch) -> None:
    import acb_skills.ask_tools as ask_tools

    form_stub(monkeypatch, {"Add ": ALL_TICKED})
    calls = fake_gateway(monkeypatch, _gateway())

    async def marker(**kwargs: Any) -> bool:
        calls.append({"method": "CARD", "path": "", "headers": {}, "params": {}, "json": kwargs})
        return True

    monkeypatch.setattr(ask_tools, "request_confirmation", marker)
    await skill_projects.create_tasks(UUID, THREE)
    at = next(i for i, c in enumerate(calls) if c["method"] == "CARD")
    assert all(m.is_read(c["method"], c["path"]) for c in calls[:at])
    assert len(_posts(calls[at:])) == 3


@pytest.mark.parametrize(
    ("rows", "words"),
    [
        ([{"title": "A"}, {"title": "B", "status": "Shipped"}], "Row 2 («B»): No status"),
        ([{"title": "A"}, {"title": "B", "due": "next week"}], "Row 2 («B»): due is a date"),
        ([{"title": "A"}, {"title": "B", "type": "Epic"}], "Row 2 («B»): No task type"),
        ([{"title": "A"}, {"title": "B", "fields": {"Colour": "red"}}], "Row 2 («B»)"),
        ([{"title": "A"}, {"title": "B", "assignees": "Nobody"}], "Row 2 («B»): No person is called exactly «Nobody»"),
        ([{"title": "A", "repeat": "weekly"}], "create_task and repeat"),
        ([{"title": "A", "project_id": OTHER}], "a second call"),
        ([{"title": "A", "colour": "red"}], "has no key «colour»"),
        ([{"title": "A"}, {"description": "no title"}], "Row 2 has no title"),
        ([{"title": "A"}, {"title": "a"}], "Rows 1 and 2 have the same title"),
        ([{"title": "A", "importance": 3}], "Row 1 («A»)"),
        ([{"title": "A", "estimate_mins": "half an hour"}], "estimate_mins is a whole number"),
        ([{"title": "A", "status": ["To do"]}], "status takes text"),
        ([], "tasks is a JSON list"),
        ("not json", "tasks is a JSON list"),
    ],
)
async def test_one_bad_row_refuses_the_batch_before_any_card(
    rows: Any, words: str, monkeypatch
) -> None:
    from skill_projects.refusals import refusals_as_text

    asked = approve(monkeypatch)
    drawn = form_stub(monkeypatch, {"Add ": ALL_TICKED})
    calls = fake_gateway(monkeypatch, _gateway())
    raw = rows if isinstance(rows, str) else json.dumps(rows)
    out = await refusals_as_text(skill_projects.create_tasks)(UUID, raw)
    assert words in out, out
    assert asked == [] and drawn == [] and writes(calls) == []


async def test_the_batch_is_capped_at_one_card(monkeypatch) -> None:
    approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _gateway())
    rows = json.dumps([{"title": f"Task {n}"} for n in range(W.MAX_BATCH + 1)])
    out = await skill_projects.create_tasks(UUID, rows)
    assert f"The limit for one card is {W.MAX_BATCH}" in out
    assert calls == []


async def test_a_batch_that_does_not_fit_on_the_card_is_refused(monkeypatch) -> None:
    asked = approve(monkeypatch)
    drawn = form_stub(monkeypatch, {"Add ": ALL_TICKED})
    calls = fake_gateway(monkeypatch, _gateway())
    rows = json.dumps([{"title": f"Task {n}", "description": "x" * 400} for n in range(20)])
    out = await skill_projects.create_tasks(UUID, rows)
    assert "do not fit on one confirmation card" in out
    assert asked == [] and drawn == [] and writes(calls) == []


async def test_a_folder_takes_no_tasks(monkeypatch) -> None:
    def folder(call: dict) -> Any:
        if call["method"] == "GET" and call["path"] == f"/projects/nodes/{UUID}":
            return {"id": UUID, "name": "Clients", "kind": "folder"}
        return _gateway()(call)

    approve(monkeypatch)
    calls = fake_gateway(monkeypatch, folder)
    out = await skill_projects.create_tasks(UUID, THREE)
    assert "is a folder" in out and writes(calls) == []


async def test_an_agent_assignee_in_a_covered_run_is_refused_as_text(monkeypatch) -> None:
    """H-236: the refusal is the tool's text, raised before any card."""
    from acb_skills.write_artifact import artifact_context_scope, bind_artifact_context

    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _gateway())
    with artifact_context_scope():
        bind_artifact_context(agent_name="projects-assistant", no_egress=True)
        out = await skill_projects.create_tasks(
            UUID, json.dumps([{"title": "A", "assignees": "agent:crm-assistant"}])
        )
    assert "Row 1" in out and "cannot assign a task to an agent" in out
    assert asked == [] and writes(calls) == []


# ── 2. The selection card and the confirmation card ─────────────────────────


async def test_the_selection_card_shows_plain_words_and_no_id(monkeypatch) -> None:
    asked = approve(monkeypatch)
    drawn = form_stub(monkeypatch, {"Add ": ALL_TICKED})
    fake_gateway(monkeypatch, _gateway())
    await skill_projects.create_tasks(UUID, THREE)

    spec = drawn[0]
    assert spec["hitl"] is True and spec["props"]["name"] == "formCard"
    form = spec["props"]["data"]
    assert form["title"] == "Add 3 tasks to Ops"
    fields = form["fields"]
    assert [f["type"] for f in fields] == ["checkbox"] * 3
    assert [f["value"] for f in fields] == [True, True, True]
    assert [f["label"] for f in fields] == [
        "Book the caterer",
        "Print the badges",
        "Test the projector",
    ]
    # The assignee's NAME from the directory, the due date in words, the lane.
    assert fields[0]["hint"] == "Priya · due Fri 9 Oct 2026 · To do (the first lane)"
    assert fields[1]["hint"] == "nobody assigned · no due date · In progress"
    assert not _UUID_RE.search(json.dumps(spec)), spec
    card = asked[0]
    assert card["title"] == "Create 3 tasks in «Ops»?"
    assert not _UUID_RE.search(card["context"] + card["detail"]), card
    assert "row 1: «Book the caterer» · «Priya» · due Fri 9 Oct 2026" in card["context"]
    assert "status «In progress»" in card["context"]
    assert "30 min · tags «av»" in card["context"]


async def test_an_unticked_row_is_not_written(monkeypatch) -> None:
    asked = approve(monkeypatch)
    form_stub(monkeypatch, {"Add ": 'Review tasks — {"row_1": true, "row_2": false, "row_3": true}'})
    calls = fake_gateway(monkeypatch, _gateway())
    out = await skill_projects.create_tasks(UUID, THREE)

    assert [p["json"]["title"] for p in _posts(calls)] == ["Book the caterer", "Test the projector"]
    assert asked[0]["title"] == "Create 2 tasks in «Ops»?"
    assert "row 2:" not in asked[0]["context"]
    assert "row 2 left out: «Print the badges», unticked on the card" in asked[0]["context"]
    assert "left out: row 2 «Print the badges», unticked on the card." in out
    # The card and the receipt give one row one number (review round 1).
    assert "row 3: «Test the projector»" in asked[0]["context"]


async def test_a_form_with_every_row_unticked_writes_nothing(monkeypatch) -> None:
    asked = approve(monkeypatch)
    form_stub(monkeypatch, {"Add ": 'Review tasks — {"row_1": false}'})
    calls = fake_gateway(monkeypatch, _gateway())
    out = await skill_projects.create_tasks(UUID, THREE)
    assert out == F.NOTHING_TICKED and asked == [] and writes(calls) == []


async def test_a_form_not_submitted_writes_nothing(monkeypatch) -> None:
    asked = approve(monkeypatch)
    form_stub(monkeypatch, {})
    calls = fake_gateway(monkeypatch, _gateway())
    out = await skill_projects.create_tasks(UUID, THREE)
    assert out == F.NOT_SUBMITTED and asked == [] and writes(calls) == []


async def test_a_declined_card_writes_nothing(monkeypatch) -> None:
    deny(monkeypatch)
    form_stub(monkeypatch, {"Add ": ALL_TICKED})
    calls = fake_gateway(monkeypatch, _gateway())
    assert await skill_projects.create_tasks(UUID, THREE) == W.CANCELLED
    assert writes(calls) == []


async def test_with_no_surface_for_the_form_the_card_alone_carries_the_batch(
    monkeypatch,
) -> None:
    """A chat that cannot draw the form still gets the one confirmation card."""
    import importlib

    wa = importlib.import_module("acb_skills.write_artifact")

    async def no_surface(_ui: str) -> dict:
        return {"ok": False, "error": "no active run stream to render into"}

    monkeypatch.setattr(wa, "emit_generative_ui", no_surface)
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _gateway())
    out = await skill_projects.create_tasks(UUID, THREE)
    assert len(asked) == 1 and len(_posts(calls)) == 3 and out.startswith("Created 3 tasks")


# ── 3. The write path is create_task's ─────────────────────────────────────


async def test_a_row_sends_what_create_task_sends(monkeypatch) -> None:
    """The same words give the same POST body and the same assign PUT."""
    words = {
        "title": "Weld the frame",
        "description": "The full brief.",
        "status": "in progress",
        "assignees": "Priya",
        "due": "2026-10-09",
        "start": "2026-10-05",
        "estimate_mins": 45,
        "tags": "urgent, q4",
        "parent_task_id": OTHER,
        "important": "yes",
        "leveraged": "no",
        "type": "bug",
        "fields": '{"Customer": "SMB"}',
    }
    assert set(words) == set(F.ROW_KEYS) - {"priority"}
    approve(monkeypatch)
    form_stub(monkeypatch, {"Add ": ALL_TICKED})
    one = fake_gateway(monkeypatch, _gateway())
    await skill_projects.create_task(UUID, **words)
    batch = fake_gateway(monkeypatch, _gateway())
    await skill_projects.create_tasks(UUID, json.dumps([words]))
    assert [(c["method"], c["json"]) for c in writes(batch)] == [
        (c["method"], c["json"]) for c in writes(one)
    ]
    assert len(writes(one)) == 2  # the POST, then the assign PUT


def test_the_row_keys_are_create_tasks_arguments() -> None:
    """Each create_task argument is a row key, a repeat key, or the call's own.

    The mutation: a new create_task argument with no decision here fails.
    """
    params = set(inspect.signature(skill_projects.create_task).parameters)
    decided = set(F.ROW_KEYS) | set(F.REPEAT_KEYS) | {"project_id", "importance"}
    assert params == decided, f"undecided: {sorted(params ^ decided)}"
    assert set(inspect.signature(skill_projects.create_tasks).parameters) == {
        "project_id",
        "tasks",
    }


# ── 4. A row that fails does not stop the others, and nothing raises ────────


@pytest.mark.parametrize(
    ("kind", "words"),
    [
        ("refuse", "refused (422): «That type is not in this project.»"),
        ("drop", "lost its connection to the gateway (ConnectError)"),
        ("boom", "failed (KeyError), so it may or may not exist"),
    ],
)
async def test_a_row_the_server_refuses_does_not_stop_the_others(
    kind: str, words: str, monkeypatch
) -> None:
    approve(monkeypatch)
    form_stub(monkeypatch, {"Add ": ALL_TICKED})
    calls = fake_gateway(monkeypatch, _gateway(**{kind: ("Print the badges",)}))
    out = await skill_projects.create_tasks(UUID, THREE)  # never raises

    assert [p["json"]["title"] for p in _posts(calls)] == [
        "Book the caterer",
        "Print the badges",
        "Test the projector",
    ]
    assert out.startswith("Created 2 of 3 tasks in «Ops». 1 was not created:")
    assert out.count("full_id:") == 2
    assert f"failed: row 2 «Print the badges» {words}" in out
    assert re.search(r"^stopped: 1 of 3 rows failed", out, re.MULTILINE)
    assert "Never create them again" in out


async def test_every_row_failing_still_reads_as_text(monkeypatch) -> None:
    approve(monkeypatch)
    form_stub(monkeypatch, {"Add ": ALL_TICKED})
    fake_gateway(
        monkeypatch,
        _gateway(refuse=("Book the caterer", "Print the badges", "Test the projector")),
    )
    out = await skill_projects.create_tasks(UUID, THREE)
    assert out.startswith("Nothing was created in «Ops». Each of the 3 tasks failed:")
    assert "full_id:" not in out and out.count("failed: row") == 3


async def test_an_assign_that_fails_after_the_create_is_named(monkeypatch) -> None:
    approve(monkeypatch)
    form_stub(monkeypatch, {"Add ": ALL_TICKED})
    calls = fake_gateway(monkeypatch, _gateway(refuse_assign=True))
    out = await skill_projects.create_tasks(UUID, THREE)
    assert len(_posts(calls)) == 3 and out.count("full_id:") == 3
    assert "not saved: the assignees of #20 «Book the caterer» refused (403)" in out
    assert "Use assign on that task." in out and "stopped:" in out


# ── 5. A retry makes no second copy ─────────────────────────────────────────


def _made(title: str, minutes: int) -> dict[str, Any]:
    at = datetime.now(UTC) - timedelta(minutes=minutes)
    return {
        **tw.TASK,
        "id": str(uuid.uuid4()),
        "title": title,
        "task_number": 12,
        "created_at": at.isoformat(),
        "archived_at": None,
    }


async def test_a_retry_does_not_duplicate(monkeypatch) -> None:
    """The first call made row 1 and lost row 2. The model calls again with the
    same rows, and the member submits the selection card as drawn."""
    asked = approve(monkeypatch)
    drawn = form_stub(monkeypatch, {})

    async def as_drawn(ui: str) -> dict:
        spec = json.loads(ui)
        drawn.append(spec)
        values = {f["name"]: f["value"] for f in spec["props"]["data"]["fields"]}
        return {"ok": True, "response": f"Review tasks — {json.dumps(values)}"}

    import importlib

    monkeypatch.setattr(importlib.import_module("acb_skills.write_artifact"), "emit_generative_ui",
                        as_drawn)
    calls = fake_gateway(monkeypatch, _gateway(recent=[_made("book the CATERER", 3)]))
    out = await skill_projects.create_tasks(UUID, THREE)

    fields = drawn[0]["props"]["data"]["fields"]
    assert [f["value"] for f in fields] == [False, True, True]
    assert "made 3 min ago, as #12, so it starts unticked" in fields[0]["hint"]
    assert [p["json"]["title"] for p in _posts(calls)] == ["Print the badges", "Test the projector"]
    assert "row 1 left out: «Book the caterer», unticked on the card" in asked[0]["context"]
    assert "row 1 «Book the caterer»" in out


async def test_an_old_twin_is_not_a_retry(monkeypatch) -> None:
    approve(monkeypatch)
    drawn = form_stub(monkeypatch, {"Add ": ALL_TICKED})
    fake_gateway(monkeypatch, _gateway(recent=[_made("Book the caterer", F.RECENT_MINUTES + 5)]))
    await skill_projects.create_tasks(UUID, THREE)
    assert [f["value"] for f in drawn[0]["props"]["data"]["fields"]] == [True, True, True]


async def test_a_twin_the_member_ticks_again_is_made_and_the_card_says_so(monkeypatch) -> None:
    asked = approve(monkeypatch)
    form_stub(monkeypatch, {"Add ": ALL_TICKED})
    calls = fake_gateway(monkeypatch, _gateway(recent=[_made("Book the caterer", 1)]))
    await skill_projects.create_tasks(UUID, THREE)
    assert len(_posts(calls)) == 3
    assert "Approving makes a second task with this title" in asked[0]["context"]


async def test_with_no_form_a_recent_twin_stays_out(monkeypatch) -> None:
    import importlib

    wa = importlib.import_module("acb_skills.write_artifact")

    async def no_surface(_ui: str) -> dict:
        return {"ok": False}

    monkeypatch.setattr(wa, "emit_generative_ui", no_surface)
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _gateway(recent=[_made("Book the caterer", 2)]))
    out = await skill_projects.create_tasks(UUID, THREE)
    assert [p["json"]["title"] for p in _posts(calls)] == ["Print the badges", "Test the projector"]
    # Both the card and the receipt say WHY, and never "unticked" (review round 1).
    why = "because a task with this title was made 2 min ago, as #12"
    assert f"row 1 left out: «Book the caterer», {why}" in asked[0]["context"]
    assert f"left out: row 1 «Book the caterer», {why}." in out
    assert "unticked" not in out


async def test_the_card_shows_the_whole_description_it_writes(monkeypatch) -> None:
    """A cut card is not consent (projects_ai_chat.md §13.6 rule 8)."""
    asked = approve(monkeypatch)
    form_stub(monkeypatch, {"Add ": ALL_TICKED})
    calls = fake_gateway(monkeypatch, _gateway())
    brief = "Book the hall for 120 people. " * 20 + "The last words."
    rows = json.dumps([{"title": "Book the hall", "description": brief}, {"title": "Pay"}])
    await skill_projects.create_tasks(UUID, rows)
    assert _posts(calls)[0]["json"]["description"] == brief.strip()
    assert "The last words." in asked[0]["context"]
    assert "truncated" not in asked[0]["context"]


async def test_the_recent_read_is_one_page_of_the_project(monkeypatch) -> None:
    approve(monkeypatch)
    form_stub(monkeypatch, {"Add ": ALL_TICKED})
    calls = fake_gateway(monkeypatch, _gateway())
    await skill_projects.create_tasks(UUID, THREE)
    [read] = [c for c in calls if c["method"] == "GET" and c["path"] == "/projects/tasks"]
    assert read["params"] == {"project_id": UUID, "page_size": F.RECENT_PAGE}


# ── 3b. F2 on the wire: a batch that drops a field fails (R7) ───────────────


class _Stripping(FakeClient):
    """The fake client, with ONE field removed from each POST /projects/tasks."""

    def __init__(self, calls, responder, name) -> None:
        super().__init__(calls, responder)
        self._name = name

    async def request(self, method: str, url: str, **kwargs: Any) -> Any:
        if method == "POST" and url.endswith("/projects/tasks"):
            kwargs = {**kwargs, "json": {k: v for k, v in kwargs["json"].items() if k != self._name}}
        return await super().request(method, url, **kwargs)


_BATCH_FIELDS = ("due_at", "status_id", "start_date", "type_id", "custom_fields", "tags")


@pytest.mark.parametrize("name", _BATCH_FIELDS)
async def test_a_batch_that_drops_a_field_fails_the_wire_check(name: str, monkeypatch) -> None:
    from tests.unit import test_projects_field_parity as f2

    key = ("POST", "/projects/tasks")
    witness = next(w for w in m.witnesses(m.SENDS[key][name]) if w.startswith("create_tasks."))
    tool, _, arg = witness.partition(".")

    async def holds(strip: bool) -> bool:
        approve(monkeypatch)
        form_stub(monkeypatch, tw.FORM_ANSWERS)
        calls: list[dict] = []
        cls = _Stripping if strip else None

        def factory(**_kw: Any) -> Any:
            if cls is not None:
                return cls(calls, f2._responder, name)
            return FakeClient(calls, f2._responder)

        monkeypatch.setattr(client, "httpx", SimpleNamespace(AsyncClient=factory))
        monkeypatch.setattr(client, "current_user_email", lambda: "pm@fracktal.in")
        await getattr(skill_projects, tool)(**f2._call_for(key, name, tool, arg))
        return any(f2._carries(c, name, "body") for c in _posts(calls))

    assert await holds(strip=False), "the witness holds as built"
    assert not await holds(strip=True), f"F2 still passes with {name} stripped"


def test_every_create_tasks_witness_is_on_the_create_and_assign_routes() -> None:
    sent = {
        (key, name)
        for key, names in m.SENDS.items()
        for name, value in names.items()
        for w in m.witnesses(value)
        if w.startswith("create_tasks.")
    }
    create = {n for (k, n) in sent if k == ("POST", "/projects/tasks")}
    assert create == set(m.SENDS[("POST", "/projects/tasks")])
    assert (("PUT", "/projects/tasks/{task_id}/assignees"), "assignees") in sent
    assert m.COMPOSITE["create_tasks"] == frozenset({"create_task"})
    assert m.tool_class("create_tasks") == "B"


# ── R8 — the batch and its retry through the REAL routes, on asyncpg ────────

_TENANT_URL = os.environ.get("TENANT_LADDER_DATABASE_URL", "").strip()
_r8 = pytest.mark.skipif(
    not _TENANT_URL, reason="TENANT_LADDER_DATABASE_URL unset — R8 requires a REAL Postgres."
)


def _async_url() -> str:
    url = _TENANT_URL
    if "postgresql+psycopg" in url:
        return url.replace("postgresql+psycopg", "postgresql+asyncpg")
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


@pytest.fixture(scope="module")
def _ladder():
    """Apply the tenant ladder ONCE for this module (each replay spends columns)."""
    if not _TENANT_URL:
        pytest.skip("TENANT_LADDER_DATABASE_URL unset")
    pytest.importorskip("sqlalchemy")
    pytest.importorskip("asyncpg")
    from sqlalchemy import create_engine

    from tests.unit._tenant_ladder import apply_ladder

    eng = create_engine(_TENANT_URL, future=True)
    with eng.begin() as conn:
        apply_ladder(conn)
    eng.dispose()


@pytest.fixture
def seeded(_ladder):
    """One root project with two lanes and one select field."""
    from sqlalchemy import create_engine, text

    eng = create_engine(_TENANT_URL, future=True)
    tag = uuid.uuid4().hex[:8]
    made: dict[str, Any] = {}
    with eng.begin() as c:
        org = str(
            c.execute(text("SELECT id FROM organization ORDER BY created_at LIMIT 1")).scalar_one()
        )
        made["org"] = org
        made["project"] = pid = str(
            c.execute(
                text(
                    "INSERT INTO pm_projects (name, status, source, created_by, organization_id,"
                    " timezone, owns_statuses) VALUES (:n, 'active', 'manual',"
                    " 'p13@example.test', CAST(:o AS uuid), 'UTC', true) RETURNING id"
                ),
                {"n": f"p13-{tag}", "o": org},
            ).scalar_one()
        )
        made["lanes"] = {}
        for pos, (name, cat) in enumerate((("To do", "todo"), ("Doing", "in_progress"))):
            made["lanes"][name] = str(
                c.execute(
                    text(
                        "INSERT INTO pm_task_statuses (project_id, name, color, position,"
                        " category) VALUES (CAST(:p AS uuid), :n, 'gray', :pos, :c)"
                        " RETURNING id"
                    ),
                    {"p": pid, "n": name, "pos": pos, "c": cat},
                ).scalar_one()
            )
        c.execute(
            text(
                "INSERT INTO pm_custom_fields (project_id, organization_id, field_key, name,"
                " field_type, options, required, created_by) VALUES (CAST(:p AS uuid),"
                " CAST(:o AS uuid), 'tier', 'Tier', 'select', CAST(:opts AS jsonb), false,"
                " 'p13@example.test')"
            ),
            {"p": pid, "o": org, "opts": json.dumps(["gold", "silver"])},
        )
    yield made
    with eng.begin() as c:
        for sql in (
            "DELETE FROM pm_tasks WHERE project_id = CAST(:p AS uuid)",
            "DELETE FROM pm_custom_fields WHERE project_id = CAST(:p AS uuid)",
            "DELETE FROM pm_task_statuses WHERE project_id = CAST(:p AS uuid)",
            "DELETE FROM pm_projects WHERE id = CAST(:p AS uuid)",
        ):
            c.execute(text(sql), {"p": made["project"]})
    eng.dispose()


class _RouteClient(FakeClient):
    """The fake client, with the create and the list answered by the REAL
    routes on one asyncpg database. Every other read is answered by *fake*."""

    def __init__(self, calls: list[dict], fake: Any, routes: Any) -> None:
        super().__init__(calls, fake)
        self._routes = routes

    async def request(self, method: str, url: str, **kwargs: Any) -> Any:
        path = "/" + url.split("/", 3)[-1]
        answered = await self._routes(method, path, kwargs)
        if answered is None:
            return await super().request(method, url, **kwargs)
        self._calls.append(
            {"method": method, "path": path, "params": kwargs.get("params") or {},
             "json": kwargs.get("json"), "headers": kwargs.get("headers") or {}}
        )
        return answered


def _real_routes(seeded: dict[str, Any], monkeypatch: pytest.MonkeyPatch):
    from contextlib import asynccontextmanager

    from fastapi import HTTPException
    from fastapi.encoders import jsonable_encoder
    from gateway.routes.projects import tasks as pm_tasks
    from gateway.routes.projects.core import Page, Visibility
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    from tests.unit._projects_fakes import projects_user

    eng = create_async_engine(_async_url(), future=True, poolclass=NullPool)

    @asynccontextmanager
    async def _session(*_a: Any, **_k: Any):
        async with eng.begin() as conn:
            yield conn

    async def _visibility(_db: Any, _user: Any) -> Any:
        return Visibility(
            unrestricted=True, email="", groups=(), organization_id=seeded["org"]
        )

    async def _emit(*_a: Any, **_k: Any) -> None:
        return None

    monkeypatch.setattr(pm_tasks, "_tenant_session", _session)
    monkeypatch.setattr(pm_tasks, "resolve_visibility", _visibility)
    monkeypatch.setattr(pm_tasks, "emit", _emit)
    user = projects_user("p13@example.test")

    async def routes(method: str, path: str, kwargs: dict[str, Any]) -> Any:
        try:
            if method == "POST" and path == "/projects/tasks":
                made = await pm_tasks.create_task(pm_tasks.TaskIn(**kwargs["json"]), user=user)
                return FakeResponse(jsonable_encoder(made))
            if method == "GET" and path == "/projects/tasks":
                q = kwargs.get("params") or {}
                listed = await pm_tasks.list_tasks(
                    user=user,
                    project_id=q.get("project_id"),
                    page=Page(page=1, page_size=int(q.get("page_size") or 50)),
                )
                return FakeResponse(jsonable_encoder(listed))
        except HTTPException as exc:
            return FakeResponse({"detail": exc.detail}, exc.status_code)
        return None

    return routes, eng


def _fake_reads(seeded: dict[str, Any]):
    pid = seeded["project"]
    lanes = [
        {"id": seeded["lanes"]["To do"], "name": "To do", "category": "todo", "position": 0},
        {"id": seeded["lanes"]["Doing"], "name": "Doing", "category": "in_progress",
         "position": 1},
    ]

    def answer(call: dict) -> Any:
        path = call["path"]
        if path == f"/projects/nodes/{pid}":
            return {"id": pid, "name": "Kits", "kind": "project"}
        if path.endswith("/statuses"):
            return {"rows": lanes}
        if path.endswith("/fields"):
            return {"rows": [{"name": "Tier", "field_key": "tier", "field_type": "select",
                              "options": ["gold", "silver"]}]}
        return {"rows": [], "names": {}, "people": [], "agents": []}

    return answer


def _stored(seeded: dict[str, Any]) -> list[tuple[str, Any]]:
    from sqlalchemy import create_engine, text

    eng = create_engine(_TENANT_URL, future=True)
    try:
        with eng.begin() as c:
            rows = c.execute(
                text(
                    "SELECT title, custom_fields FROM pm_tasks WHERE project_id = CAST(:p AS uuid)"
                    " ORDER BY task_number"
                ),
                {"p": seeded["project"]},
            ).fetchall()
    finally:
        eng.dispose()
    return [(r.title, r.custom_fields) for r in rows]


async def _drive(
    seeded, monkeypatch, rows: list[dict[str, Any]], fake: Any = None
) -> tuple[str, list[dict]]:
    """create_tasks over the real routes, with the selection card submitted as drawn."""
    import importlib

    routes, eng = _real_routes(seeded, monkeypatch)
    calls: list[dict] = []
    fake = fake or _fake_reads(seeded)
    monkeypatch.setattr(
        client, "httpx",
        SimpleNamespace(AsyncClient=lambda **_kw: _RouteClient(calls, fake, routes)),
    )
    monkeypatch.setattr(client, "current_user_email", lambda: "p13@example.test")
    approve(monkeypatch)

    async def as_drawn(ui: str) -> dict:
        spec = json.loads(ui)
        values = {f["name"]: f["value"] for f in spec["props"]["data"]["fields"]}
        return {"ok": True, "response": f"Review tasks — {json.dumps(values)}"}

    monkeypatch.setattr(
        importlib.import_module("acb_skills.write_artifact"), "emit_generative_ui", as_drawn
    )
    try:
        out = await skill_projects.create_tasks(seeded["project"], json.dumps(rows))
    finally:
        await eng.dispose()
    return out, calls


@_r8
async def test_r8_a_batch_lands_and_a_row_the_route_refuses_stops_nothing(
    seeded, monkeypatch
) -> None:
    """Three rows. The route refuses the middle row's value (#679), and the
    other two land with their lanes and values."""
    rows = [
        {"title": "Pack the kits", "status": "Doing", "fields": {"Tier": "gold"}},
        {"title": "Label the kits", "fields": '{"Tier": "bronze"}'},
        {"title": "Ship the kits", "due": "2026-10-20"},
    ]
    # The tool checks a choice before the card, so "bronze" never reaches the
    # route from the tool. Here the fake vocabulary lists it, to see the route
    # refuse it after the card. That is the failure a stale read makes.
    original = _fake_reads(seeded)

    def stale(call: dict) -> Any:
        if call["path"].endswith("/fields"):
            return {"rows": [{"name": "Tier", "field_key": "tier", "field_type": "select",
                              "options": ["gold", "silver", "bronze"]}]}
        return original(call)

    out, _calls = await _drive(seeded, monkeypatch, rows, fake=stale)
    assert out.startswith("Created 2 of 3 tasks"), out
    assert "failed: row 2 «Label the kits» refused (422)" in out
    stored = _stored(seeded)
    assert [t for t, _ in stored] == ["Pack the kits", "Ship the kits"]
    assert _as_dict(stored[0][1]) == {"tier": "gold"}


def _as_dict(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


@_r8
async def test_r8_a_retry_over_the_real_list_route_makes_no_second_copy(
    seeded, monkeypatch
) -> None:
    """The list route's own order and its ``created_at`` find the first run's
    tasks, so the second run makes only the row that is new."""
    first = [{"title": "Pack the kits"}, {"title": "Ship the kits"}]
    out, _ = await _drive(seeded, monkeypatch, first)
    assert out.startswith("Created 2 tasks"), out
    again = [*first, {"title": "Count the kits"}]
    out, calls = await _drive(seeded, monkeypatch, again)
    assert out.startswith("Created 1 task"), out
    assert "left out: row 1 «Pack the kits», unticked on the card." in out
    assert "left out: row 2 «Ship the kits», unticked on the card." in out
    assert [t for t, _ in _stored(seeded)] == ["Pack the kits", "Ship the kits", "Count the kits"]
    [listed] = [c for c in calls if c["method"] == "GET" and c["path"] == "/projects/tasks"]
    assert listed["params"]["project_id"] == seeded["project"]
