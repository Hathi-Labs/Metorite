"""``link_tasks_batch`` — many links between tasks under ONE card.

Owner, 2026-10-19: "For linking tasks, it would also be nice if it's possible
to approve multiple at a time instead of doing one at a time. Only if there
are a lot of tasks to be linked." ``link_tasks`` asks one card per link.

``link_tasks_batch`` checks every link first (reads only), shows ONE card
with a checkbox for each link, and posts each ticked link through the route
of ``link_tasks``. It shares the card, the apply and the receipt with
``file_tasks`` (``writes._ask_batch``, ``_apply_batch``, ``_batch_receipt``).

What this file holds (R7, each one a mutation that fails a test here):

1. Three links draw ONE card, and one approval makes every link.
2. An unticked link is not made, and the receipt says so.
3. A task the member cannot see, a link that exists, a link of a task to
   itself and a ``blocks`` link that makes a loop are named on the card and
   left out. The loop check knows the batch's own links and the links that
   exist one step out.
4. A declined card writes nothing.
5. A link that the route refuses after the card is a row of the receipt,
   never a raise.
6. The tool is exported, a write, a composite of ``link_tasks``, in the
   agent's tool scope, and the instructions keep ``link_tasks`` for one or
   two links.

No gateway and no database: the client is patched at httpx. The tool adds
no SQL and no route, so R8 does not bind here.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

pytest.importorskip("skill_projects", reason="skill-projects not installed")

import skill_projects
from skill_projects import manifest as m
from skill_projects import writes as W

from tests.unit._projects_agent_fakes import (
    AGENT_DIR,
    FakeResponse,
    answer_rows,
    approve,
    deny,
    empty_list,
    fake_gateway,
    writes,
)

A = "c1000000-0000-4000-8000-000000000000"
B = "c2000000-0000-4000-8000-000000000000"
C = "c3000000-0000-4000-8000-000000000000"
D = "c4000000-0000-4000-8000-000000000000"
HIDDEN = "c9000000-0000-4000-8000-000000000000"

TASKS: dict[str, dict[str, Any]] = {
    tid: {"id": tid, "title": title, "task_number": n, "project_id": tid}
    for tid, title, n in (
        (A, "Design the bracket", 247),
        (B, "Print the bracket", 248),
        (C, "Test the bracket", 249),
        (D, "Ship the bracket", 250),
    )
}

#: The links each task has, as `GET /projects/tasks/{id}/relations` lists them.
LINKS: dict[str, list[dict[str, Any]]] = {}


@pytest.fixture(autouse=True)
def _clean_links() -> Any:
    LINKS.clear()
    yield
    LINKS.clear()


def _exists(source: str, target: str, kind: str) -> None:
    """A link that exists, on both ends, as the relations route shows it."""
    LINKS.setdefault(source, []).append(
        {
            "id": target,
            "link_id": "d0000000-0000-4000-8000-000000000000",
            "direction": "outgoing",
            "link_type": kind,
        }
    )
    LINKS.setdefault(target, []).append(
        {
            "id": source,
            "link_id": "d0000000-0000-4000-8000-000000000000",
            "direction": "incoming",
            "link_type": kind,
        }
    )


def responder(call: dict) -> Any:
    path, method = call["path"], call["method"]
    parts = path.split("/")
    if method == "GET" and path.endswith("/relations"):
        return {"subtasks": [], "links": LINKS.get(parts[3], []), "blocked_by": []}
    if method == "GET" and path.startswith("/projects/tasks/"):
        if parts[3] in TASKS:
            return TASKS[parts[3]]
        return FakeResponse({"detail": "Task not found"}, status_code=404)
    if method == "POST" and path.endswith("/links"):
        return {
            "id": "e0000000-0000-4000-8000-000000000000",
            "source_task_id": parts[3],
            "target_task_id": call["json"]["target_task_id"],
            "link_type": call["json"]["link_type"],
        }
    return empty_list(call)


def _links(*rows: tuple[str, str, str]) -> str:
    return json.dumps([{"from_task_id": a, "to_task_id": b, "type": kind} for a, b, kind in rows])


def _posted(calls: list[dict]) -> list[tuple[str, str, str]]:
    return [
        (c["path"].split("/")[3], c["json"]["target_task_id"], c["json"]["link_type"])
        for c in writes(calls)
    ]


# ── 1. Three links, one card, every link ────────────────────────────────────


async def test_three_links_are_one_card_and_one_approval_makes_them_all(monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, responder)
    out = await skill_projects.link_tasks_batch(
        _links((A, B, "blocks"), (B, C, "blocks"), (C, D, "relates_to"))
    )
    assert len(asked) == 1
    card = asked[0]
    assert card["title"] == "Make 3 links between tasks?"
    assert card["detail"].startswith("#247 «Design the bracket» blocks #248 «Print the bracket»")
    assert [r["id"] for r in card["rows"]] == ["row-1", "row-2", "row-3"]
    assert card["rows"][0]["label"] == "#247 Design the bracket blocks #248 Print the bracket"
    assert (
        "#248 Print the bracket waits until #247 Design the bracket is done"
        in (card["rows"][0]["hint"])
    )
    assert _posted(calls) == [(A, B, "blocks"), (B, C, "blocks"), (C, D, "relates_to")]
    assert out.splitlines()[0] == "Made 3 links:"
    assert "- #247 «Design the bracket» · blocks #248 «Print the bracket»" in out
    assert out.count("full_id:") == 3
    assert "Do not link them again" in out


async def test_everything_before_the_card_is_a_read(monkeypatch) -> None:
    import acb_skills.ask_tools as ask_tools

    calls = fake_gateway(monkeypatch, responder)

    async def marker(**kwargs: Any) -> Any:
        calls.append({"method": "CARD", "path": "", "json": kwargs, "params": {}, "headers": {}})
        return frozenset(r["id"] for r in kwargs["rows"])

    monkeypatch.setattr(ask_tools, "request_confirmation", marker)
    await skill_projects.link_tasks_batch(
        _links((A, B, "blocks"), (B, C, "blocks"), (C, D, "duplicates"))
    )
    at = next(i for i, c in enumerate(calls) if c["method"] == "CARD")
    assert all(m.is_read(c["method"], c["path"]) for c in calls[:at])
    assert len(writes(calls[at + 1 :])) == 3


# ── 2. An unticked link ─────────────────────────────────────────────────────


async def test_an_unticked_link_is_not_made(monkeypatch) -> None:
    answer_rows(monkeypatch, ["row-1", "row-3"])
    calls = fake_gateway(monkeypatch, responder)
    out = await skill_projects.link_tasks_batch(
        _links((A, B, "blocks"), (B, C, "blocks"), (C, D, "blocks"))
    )
    assert _posted(calls) == [(A, B, "blocks"), (C, D, "blocks")]
    assert out.splitlines()[0] == "Made 2 of 3 links. 1 was unticked."
    assert "left out: row 2 #248 «Print the bracket» blocks #249 «Test the bracket»" in out


async def test_a_card_that_answers_a_row_it_did_not_offer_links_nothing(monkeypatch) -> None:
    answer_rows(monkeypatch, ["row-1", "row-7"])
    calls = fake_gateway(monkeypatch, responder)
    out = await skill_projects.link_tasks_batch(
        _links((A, B, "blocks"), (B, C, "blocks"), (C, D, "blocks"))
    )
    assert out == W.FORGED_BATCH_ROWS
    assert writes(calls) == []


# ── 3. What cannot link is named and left out ───────────────────────────────


async def test_a_hidden_task_an_existing_link_and_a_self_link_are_named(monkeypatch) -> None:
    _exists(B, C, "relates_to")
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, responder)
    out = await skill_projects.link_tasks_batch(
        _links(
            (A, B, "blocks"),
            (A, HIDDEN, "blocks"),
            (C, B, "relates_to"),  # relates_to reads both ways: it exists
            (D, D, "relates_to"),
        )
    )
    context = asked[0]["context"]
    assert f"task «{HIDDEN}» is not found, or not visible to you" in context
    assert "they are linked like this already" in context
    assert "a task cannot be linked to itself" in context
    assert [r["id"] for r in asked[0]["rows"]] == ["row-1"]
    assert _posted(calls) == [(A, B, "blocks")]
    assert out.splitlines()[0] == "Made 1 of 4 links. 3 could not link."
    assert "not linked: row 4 #250 «Ship the bracket» relates to #250 «Ship the bracket»" in out


async def test_a_blocks_link_against_an_existing_one_is_a_loop(monkeypatch) -> None:
    _exists(B, A, "blocks")  # B blocks A already: A blocks B would loop
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, responder)
    await skill_projects.link_tasks_batch(
        _links((A, B, "blocks"), (C, D, "blocks"), (B, C, "relates_to"))
    )
    assert (
        "cannot link 1: #247 «Design the bracket» blocks #248 «Print the bracket»: that would "
        "make a loop" in asked[0]["context"]
    )
    assert (A, B, "blocks") not in _posted(calls)


async def test_the_batchs_own_links_make_a_loop_and_the_closing_one_is_left_out(
    monkeypatch,
) -> None:
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, responder)
    out = await skill_projects.link_tasks_batch(
        _links((A, B, "blocks"), (B, C, "blocks"), (C, A, "blocks"))
    )
    assert [r["id"] for r in asked[0]["rows"]] == ["row-1", "row-2"]
    assert _posted(calls) == [(A, B, "blocks"), (B, C, "blocks")]
    assert "not linked: row 3 #249 «Test the bracket» blocks #247 «Design the bracket»: " in out


async def test_when_no_link_can_be_made_there_is_no_card(monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, responder)
    out = await skill_projects.link_tasks_batch(
        _links((A, A, "blocks"), (B, HIDDEN, "blocks"), (C, C, "relates_to"))
    )
    assert asked == []
    assert writes(calls) == []
    assert out.startswith("No link of these can be made. Nothing was changed.")


# ── 4. A declined card ──────────────────────────────────────────────────────


async def test_a_declined_card_writes_nothing(monkeypatch) -> None:
    deny(monkeypatch)
    calls = fake_gateway(monkeypatch, responder)
    out = await skill_projects.link_tasks_batch(
        _links((A, B, "blocks"), (B, C, "blocks"), (C, D, "blocks"))
    )
    assert out == W.CANCELLED
    assert writes(calls) == []


# ── 5. A link the route refuses after the card ──────────────────────────────


async def test_a_refused_link_is_a_row_of_the_receipt(monkeypatch) -> None:
    def refusing(call: dict) -> Any:
        if call["method"] == "POST" and call["path"] == f"/projects/tasks/{B}/links":
            return FakeResponse({"detail": "That link would make a loop."}, status_code=422)
        return responder(call)

    approve(monkeypatch)
    calls = fake_gateway(monkeypatch, refusing)
    out = await skill_projects.link_tasks_batch(
        _links((A, B, "blocks"), (B, C, "blocks"), (C, D, "blocks"))
    )
    assert len(writes(calls)) == 3, "a refused link stopped the links after it"
    lines = out.splitlines()
    assert lines[0] == "Made 2 of 3 links. 1 was refused."
    assert any(
        line.startswith(
            "failed: row 2 #248 «Print the bracket» blocks #249 «Test the bracket» refused (422)"
        )
        for line in lines
    )
    assert "stopped: 1 of 3 links failed." in lines


# ── Input refused before any call ───────────────────────────────────────────


@pytest.mark.parametrize(
    ("links", "said"),
    [
        ("nope", "links is a JSON list"),
        (json.dumps([{"from_task_id": A, "to_task_id": B, "type": "follows"}]), "type is one of"),
        (
            json.dumps([{"from_task_id": A, "to_task_id": B, "kind": "blocks"}]),
            "Row 1 has a key that link_tasks_batch does not take: kind",
        ),
        (_links((A, B, "blocks"), (A, B, "blocks")), "Rows 1 and 2 name the same link"),
        (json.dumps([{"from_task_id": "x", "to_task_id": B}]), "Row 1: from_task_id"),
    ],
)
async def test_bad_rows_are_refused_before_any_call(links: str, said: str, monkeypatch) -> None:
    approve(monkeypatch)
    calls = fake_gateway(monkeypatch, responder)
    out = await skill_projects.link_tasks_batch(links)
    assert said in out
    assert calls == []


# ── 6. The agent holds it, as a write ───────────────────────────────────────


def test_the_tool_is_exported_annotated_as_a_write_and_a_link_tasks_composite() -> None:
    from acb_skills.tool_annotations import TOOL_ANNOTATIONS

    assert "link_tasks_batch" in skill_projects.__all__
    assert TOOL_ANNOTATIONS["link_tasks_batch"]["read_only"] is False
    assert TOOL_ANNOTATIONS["link_tasks_batch"]["open_world"] is False
    assert m.COMPOSITE["link_tasks_batch"] == frozenset({"link_tasks"})
    assert m.tool_class("link_tasks_batch") == "B"


def test_the_instructions_keep_link_tasks_for_one_or_two_links() -> None:
    config = json.loads((AGENT_DIR / "config.json").read_text(encoding="utf-8"))
    assert {"link_tasks", "link_tasks_batch"} <= set(config["own_tool_scope"])
    text = (AGENT_DIR / "instructions.md").read_text(encoding="utf-8")
    start = text.index("## Linking many tasks")
    section = text[start : text.index("\n## ", start + 1)]
    assert "Three or more links are ONE call" in section
    assert "For one or\n  two links, call `link_tasks`" in section
