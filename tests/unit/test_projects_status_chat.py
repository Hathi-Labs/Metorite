"""WS-42 PST-4 — the chat status tools guard a shared set and place by slot.

Spec: ``project-docs/specs/projects_settings.md`` §7.1 (PST-4) and §9.4.

On 2026-11-05 the chat passed the id of a subproject that used the space's set.
33 writes then landed on a set that 28 projects share. ``create_status`` named
the subproject on its receipt, and ``position`` was a raw sort key.

With the flag on (``guard_on`` on the status-set read, the route's answer):

* a project that does not own its set gets a refusal that names the owner and
  N, and offers ``set_status_set`` mode=own first;
* ``change_whole_set=true`` shows a card with "Changes the statuses of {owner}
  for {N} projects";
* the tool sends ``slot`` or ``after`` and never a raw ``position``.

With the flag off, the tools act as before this slice, ``position`` included.

No gateway and no database: the client is patched at httpx. The server half
(the renumbering) is in ``test_projects_status_slot_r8.py``.

Mutations (R7), each turns the named tests red:

* M-a. ``writes.status_edit_refusal``: drop the ``shared_set_refusal`` return.
  ``test_a_subproject_on_a_shared_set_is_refused``, the update and delete cases,
  and ``test_the_refusal_names_the_owner_and_the_count``.
* M-b. ``writes.create_status``: send ``position`` with the flag on.
  ``test_the_flag_on_sends_a_slot_and_never_a_position``.
* M-c. ``writes.update_status``: take the ``position >= 0`` refusal out.
  ``test_the_flag_on_refuses_a_raw_position``.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

pytest.importorskip("skill_projects", reason="skill-projects not installed")

import skill_projects

from tests.unit._projects_agent_fakes import approve, deny, fake_gateway, writes

SUB = "0f8fad5b-d9cb-469f-a165-70867728950e"
SPACE = "1f8fad5b-d9cb-469f-a165-70867728950e"
S_TODO = "3f8fad5b-d9cb-469f-a165-70867728950e"
S_DOING = "4f8fad5b-d9cb-469f-a165-70867728950e"
S_DONE = "5f8fad5b-d9cb-469f-a165-70867728950e"
S_SHIP = "6f8fad5b-d9cb-469f-a165-70867728950e"
S_REL = "7f8fad5b-d9cb-469f-a165-70867728950e"
S_CLOSED = "8f8fad5b-d9cb-469f-a165-70867728950e"

#: The set of the space in the one order. Done has the lowest position.
LANES = [
    {"id": S_TODO, "name": "Backlog", "category": "todo", "position": 10},
    {"id": S_DOING, "name": "Doing", "category": "in_progress", "position": 10},
    {"id": S_DONE, "name": "Done", "category": "done", "position": 1},
    {"id": S_SHIP, "name": "Shipped", "category": "done", "position": 5},
    {"id": S_REL, "name": "Released", "category": "done", "position": 7},
    {"id": S_CLOSED, "name": "Closed", "category": "done", "position": 9},
]


def _read(**over: Any) -> dict[str, Any]:
    """``GET /projects/nodes/{id}/status-set`` for a subproject on the space's set."""
    base = {
        "project_id": SUB, "owns": False, "owner_id": SPACE, "owner_name": "Client Projects",
        "owner_kind": "space", "used_by": 28, "reach": 28, "may_edit": True,
        "edit_refusal": "", "guard_on": True,
    }
    return {**base, **over}


def _gateway(status_set: dict[str, Any]):
    def respond(call: dict) -> Any:
        path, method = call["path"], call["method"]
        if path.endswith("/status-set") and method == "GET":
            return status_set
        if path.endswith("/statuses") and method == "GET":
            counts = {S_TODO: 0, S_DOING: 0, S_DONE: 2, S_SHIP: 0, S_REL: 0, S_CLOSED: 0}
            return {"rows": LANES, "counts": counts, "owner_id": SPACE}
        if path == f"/projects/nodes/{SUB}" and method == "GET":
            return {"id": SUB, "name": "Website", "parent_project_id": SPACE}
        if path.endswith("/statuses") and method == "POST":
            return {"id": S_SHIP, **call["json"]}
        if method == "PATCH" and path.startswith("/projects/statuses/"):
            return {"id": S_DONE, "name": "Done", "category": "done"}
        if method == "DELETE":
            return {"deleted": S_DONE, "tasks_affected": 2}
        return None

    return respond


def _sent(calls: list[dict], method: str) -> list[dict]:
    return [c for c in writes(calls) if c["method"] == method]


# ── 1. The refusal ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "call",
    [
        lambda: skill_projects.create_status(SUB, "Blocked"),
        lambda: skill_projects.update_status(SUB, "Done", name="Finished"),
        lambda: skill_projects.delete_status(SUB, "Done", move_to="Shipped"),
    ],
    ids=["create_status", "update_status", "delete_status"],
)
async def test_a_subproject_on_a_shared_set_is_refused(monkeypatch, call) -> None:
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _gateway(_read()))
    out = await call()
    assert out.startswith("Refused:"), out
    assert asked == [] and writes(calls) == []


async def test_the_refusal_names_the_owner_and_the_count(monkeypatch) -> None:
    approve(monkeypatch)
    fake_gateway(monkeypatch, _gateway(_read()))
    out = await skill_projects.create_status(SUB, "Blocked")
    assert "«Client Projects»" in out and "space" in out
    assert "28 projects share that set" in out
    assert "set_status_set with mode=own" in out
    assert "change_whole_set=true" in out
    assert "Nothing was done" in out
    # Failure copy explains, and never names the model.
    assert "Jev" not in out


async def test_the_refusal_of_a_project_owner_reads_project(monkeypatch) -> None:
    approve(monkeypatch)
    fake_gateway(monkeypatch, _gateway(_read(owner_kind="project", used_by=1, reach=2)))
    out = await skill_projects.create_status(SUB, "Blocked")
    assert "the project «Client Projects»" in out and "2 projects share" in out


async def test_a_project_that_owns_its_set_is_not_refused(monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _gateway(_read(owns=True, owner_id=SUB, used_by=0, reach=1)))
    out = await skill_projects.create_status(SUB, "Blocked")
    assert out.startswith("Added status"), out
    assert len(_sent(calls, "POST")) == 1
    # Nobody else shares the set, so the card has no shared-set line.
    assert "Changes the statuses" not in asked[0]["detail"]


# ── 2. The card ─────────────────────────────────────────────────────────────


async def test_an_explicit_shared_change_shows_the_set_and_the_count(monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _gateway(_read()))
    out = await skill_projects.create_status(SUB, "Blocked", change_whole_set=True)
    assert out.startswith("Added status"), out
    card = asked[0]
    assert "Changes the statuses of «Client Projects» for 28 projects" in card["detail"]
    assert "status set: «Client Projects»" in card["context"]
    assert len(_sent(calls, "POST")) == 1
    # The receipt names the set that changed, and not only the subproject.
    assert "to the statuses of «Client Projects», used by «Website»" in out


async def test_the_card_of_update_and_delete_carries_the_same_line(monkeypatch) -> None:
    asked = approve(monkeypatch)
    fake_gateway(monkeypatch, _gateway(_read()))
    await skill_projects.update_status(SUB, "Done", name="Finished", change_whole_set=True)
    await skill_projects.delete_status(SUB, "Done", move_to="Shipped", change_whole_set=True)
    assert len(asked) == 2
    for card in asked:
        assert "Changes the statuses of «Client Projects» for 28 projects" in card["detail"]


async def test_one_project_reads_in_the_singular(monkeypatch) -> None:
    asked = approve(monkeypatch)
    fake_gateway(monkeypatch, _gateway(_read(used_by=1, reach=1)))
    await skill_projects.create_status(SUB, "Blocked", change_whole_set=True)
    assert "for 1 project" in asked[0]["detail"] and "1 projects" not in asked[0]["detail"]


async def test_a_denied_card_writes_nothing(monkeypatch) -> None:
    deny(monkeypatch)
    calls = fake_gateway(monkeypatch, _gateway(_read()))
    out = await skill_projects.create_status(SUB, "Blocked", change_whole_set=True)
    assert "Added" not in out and writes(calls) == []


# ── 3. Slot and after ───────────────────────────────────────────────────────


async def test_the_flag_on_sends_a_slot_and_never_a_position(monkeypatch) -> None:
    """M-b. A create with no place goes LAST in its stage: one past the lanes."""
    approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _gateway(_read()))
    await skill_projects.create_status(SUB, "Blocked", category="done", change_whole_set=True)
    await skill_projects.create_status(
        SUB, "Early", category="done", slot=2, change_whole_set=True)
    await skill_projects.create_status(
        SUB, "Behind", category="done", after="Shipped", change_whole_set=True)
    posts = [c["json"] for c in _sent(calls, "POST")]
    assert posts == [
        {"name": "Blocked", "category": "done", "slot": 5},
        {"name": "Early", "category": "done", "slot": 2},
        {"name": "Behind", "category": "done", "after": "Shipped"},
    ]
    assert all("position" not in body for body in posts)


async def test_update_sends_slot_4_of_4_for_done(monkeypatch) -> None:
    """Acceptance 3: "put Done last". The model passes slot 4 of the 4 lanes
    of the done stage. The server puts it last in the stage."""
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _gateway(_read(owns=True, owner_id=SUB, used_by=0, reach=1)))
    await skill_projects.update_status(SUB, "Done", slot=4)
    patched = _sent(calls, "PATCH")
    assert [c["json"] for c in patched] == [{"slot": 4}]
    # The card says where the lane goes, in words, and shows where it is now.
    assert "position: «1 of 4 in its stage» → «4 of 4 in its stage»" in asked[0]["context"]


async def test_update_sends_after(monkeypatch) -> None:
    approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _gateway(_read(owns=True, owner_id=SUB, used_by=0, reach=1)))
    await skill_projects.update_status(SUB, "Done", after="Released")
    assert [c["json"] for c in _sent(calls, "PATCH")] == [{"after": "Released"}]


async def test_the_flag_on_refuses_a_raw_position(monkeypatch) -> None:
    """M-c. The model cannot send a sort key. The tool says what to send."""
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _gateway(_read(owns=True, owner_id=SUB, used_by=0, reach=1)))
    out = await skill_projects.update_status(SUB, "Done", position=3)
    assert "raw sort key" in out and "slot" in out and "Nothing was done" in out
    assert asked == [] and writes(calls) == []


async def test_slot_and_after_together_are_refused(monkeypatch) -> None:
    asked = approve(monkeypatch)
    fake_gateway(monkeypatch, _gateway(_read(owns=True, owner_id=SUB, used_by=0, reach=1)))
    out = await skill_projects.update_status(SUB, "Done", slot=2, after="Shipped")
    assert "not both" in out and asked == []


async def test_the_server_range_refusal_reaches_the_model(monkeypatch) -> None:
    """Acceptance 4 from the tool side: the 422 of the route has the range, and
    the tool passes its words on. The route half is the R8 suite."""
    from skill_projects import client

    approve(monkeypatch)
    detail = "Slot 3 is outside the stage. Use a slot from 1 to 4."

    def respond(call: dict) -> Any:
        if call["method"] == "PATCH":
            from tests.unit._projects_agent_fakes import FakeResponse

            res = FakeResponse({"detail": detail}, 422)
            res.text = detail
            return res
        return _gateway(_read(owns=True, owner_id=SUB, used_by=0, reach=1))(call)

    fake_gateway(monkeypatch, respond)
    with pytest.raises(client.GatewayRefusal) as err:
        await skill_projects.update_status(SUB, "Done", slot=3)
    assert "from 1 to 4" in str(err.value)


# ── 7. The flag off ─────────────────────────────────────────────────────────


async def test_the_flag_off_acts_as_before(monkeypatch) -> None:
    """No refusal, no shared-set line, and the raw position goes on the wire."""
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _gateway(_read(guard_on=False)))
    out = await skill_projects.create_status(SUB, "Blocked", category="done")
    assert out.startswith("Added status"), out
    assert "to «Website»" in out
    await skill_projects.update_status(SUB, "Done", position=3)
    await skill_projects.delete_status(SUB, "Done", move_to="Shipped")
    assert _sent(calls, "POST")[0]["json"] == {
        "name": "Blocked", "category": "done", "position": 20}
    assert _sent(calls, "PATCH")[0]["json"] == {"position": 3}
    assert len(asked) == 3
    for card in asked:
        assert "Changes the statuses" not in card["detail"]


async def test_an_older_read_without_the_flag_is_off(monkeypatch) -> None:
    approve(monkeypatch)
    old = _read()
    del old["guard_on"], old["reach"], old["owner_kind"]
    calls = fake_gateway(monkeypatch, _gateway(old))
    out = await skill_projects.create_status(SUB, "Blocked")
    assert out.startswith("Added status") and len(_sent(calls, "POST")) == 1


async def test_the_flag_off_still_sends_slot_and_after(monkeypatch) -> None:
    """P1 of the review: a tool schema cannot be dark. "Put Done last" is ONE
    call in every org, flag on or off."""
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _gateway(_read(guard_on=False)))
    await skill_projects.update_status(SUB, "Done", slot=4)
    await skill_projects.update_status(SUB, "Done", after="Shipped")
    out = await skill_projects.create_status(SUB, "Blocked", category="done", slot=2)
    assert out.startswith("Added status"), out
    assert [c["json"] for c in _sent(calls, "PATCH")] == [{"slot": 4}, {"after": "Shipped"}]
    assert _sent(calls, "POST")[0]["json"] == {"name": "Blocked", "category": "done", "slot": 2}
    assert len(asked) == 3
    for card in asked:
        assert "Changes the statuses" not in card["detail"]


async def test_the_flag_off_adds_to_an_inheriting_project_in_one_step(monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _gateway(_read(guard_on=False)))
    out = await skill_projects.create_status(SUB, "Blocked", category="done")
    assert out.startswith("Added status"), out
    assert len(asked) == 1
    assert not [c for c in calls if c["path"].endswith("/status-set") and c["method"] != "GET"]
    assert not [c for c in calls if c["path"].endswith("/status-set/preview")]


async def test_set_status_set_is_not_held_by_the_guard(monkeypatch) -> None:
    """`mode=own` is the way out of the refusal. The guard must not block it."""
    asked = approve(monkeypatch)

    def respond(call: dict) -> Any:
        if call["path"].endswith("/status-set/preview"):
            return {"lanes": [{"name": "Backlog"}], "moves": [], "moving": 0,
                    "completing": 0, "reopening": 0}
        if call["path"].endswith("/status-set") and call["method"] == "POST":
            return {"project_id": SUB, "owns": True}
        return _gateway(_read())(call)

    calls = fake_gateway(monkeypatch, respond)
    out = await skill_projects.set_status_set(SUB, "own")
    assert not out.startswith("Refused:"), out
    assert len(asked) == 1
    assert [c["json"]["mode"] for c in _sent(calls, "POST")] == ["own"]


# ── The words ───────────────────────────────────────────────────────────────


def test_the_rule_text_is_flag_neutral() -> None:
    """P1: the static text holds no unconditional "mode=own first". The rule
    lives in the refusal, which only a flag-on org sees. The placement rule and
    the conditional line stay."""
    from pathlib import Path

    from acb_skills.tool_packs import _PROJECTS_STRUCTURE_RULES

    agent = Path(__file__).resolve().parents[2] / "apps/agents/agent-projects/instructions.md"
    texts = {
        "pack rules": " ".join(_PROJECTS_STRUCTURE_RULES.split()),
        "instructions": " ".join(agent.read_text(encoding="utf-8").split()).replace("`", ""),
    }
    for where, flat in texts.items():
        assert "mode=own first" not in flat and "own set first" not in flat, where
        assert "never guess a position" in flat.lower(), where
        assert "When a status tool refuses because the set is shared" in flat, where
        assert "change_whole_set=true only when the member says so" in flat, where


def test_the_tools_take_the_new_arguments_and_the_manifest_maps_them() -> None:
    import inspect

    from skill_projects import manifest

    for name in ("create_status", "update_status"):
        params = inspect.signature(getattr(skill_projects, name)).parameters
        assert {"slot", "after", "change_whole_set"} <= set(params), name
    assert "change_whole_set" in inspect.signature(skill_projects.delete_status).parameters
    # The tools send them only with the flag on, so the fence's gateway cannot
    # witness them. They are exempt with a reason, never silent.
    for key in (("POST", "/projects/nodes/{project_id}/statuses"),
                ("PATCH", "/projects/statuses/{status_id}")):
        assert {"slot", "after"} <= set(manifest.FIELD_EXEMPT[key]), key


# ── The review round ────────────────────────────────────────────────────────


@pytest.mark.parametrize("guard_on", [True, False], ids=["flag-on", "flag-off"])
async def test_a_slot_past_the_stage_is_refused_before_any_card(monkeypatch, guard_on) -> None:
    """P2: the card must not show a place that the route will refuse. Done
    stage holds 4 lanes: slot 6 is out for an update (1 to 4), and slot 6 is out
    for a create (1 to 5)."""
    asked = approve(monkeypatch)
    calls = fake_gateway(
        monkeypatch,
        _gateway(_read(owns=True, owner_id=SUB, used_by=0, reach=1, guard_on=guard_on)),
    )
    moved = await skill_projects.update_status(SUB, "Done", slot=6)
    made = await skill_projects.create_status(SUB, "Late", category="done", slot=6)
    assert moved.startswith("Refused:") and "slot is 1 to 4" in moved
    assert made.startswith("Refused:") and "slot is 1 to 5" in made
    assert "Nothing was done" in moved and "Nothing was done" in made
    assert asked == [] and writes(calls) == []


async def test_an_after_that_is_not_in_the_stage_is_refused_before_any_card(monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _gateway(_read(owns=True, owner_id=SUB, used_by=0, reach=1)))
    out = await skill_projects.update_status(SUB, "Done", after="Doing")
    assert out.startswith("Refused:") and "No lane named «Doing»" in out
    assert "«Shipped», «Released», «Closed»" in out
    assert asked == [] and writes(calls) == []


async def test_the_last_slot_of_a_create_is_allowed(monkeypatch) -> None:
    approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _gateway(_read(owns=True, owner_id=SUB, used_by=0, reach=1)))
    await skill_projects.create_status(SUB, "Late", category="done", slot=5)
    assert _sent(calls, "POST")[0]["json"]["slot"] == 5


async def test_one_project_reads_in_the_singular_in_the_refusal(monkeypatch) -> None:
    """P3: "1 project shares that set … changes it". Not "1 project share"."""
    approve(monkeypatch)
    fake_gateway(monkeypatch, _gateway(_read(used_by=1, reach=1)))
    one = await skill_projects.create_status(SUB, "Blocked")
    assert "1 project shares that set, so a change here changes it." in one
    assert "1 project share " not in one and "all 1" not in one
    fake_gateway(monkeypatch, _gateway(_read(used_by=3, reach=3)))
    many = await skill_projects.create_status(SUB, "Blocked")
    assert "3 projects share that set, so a change here changes all 3." in many


async def test_the_vocabulary_read_says_a_shared_set_needs_its_own_first(monkeypatch) -> None:
    """Noted: with the flag on, "you may edit it" must not stand for a set that
    the tools refuse to change."""
    fake_gateway(monkeypatch, _gateway(_read()))
    on = await skill_projects.vocabulary(SUB)
    assert "shared by 28 projects" in on and "mode=own" in on
    assert "you may edit it" not in on
    fake_gateway(monkeypatch, _gateway(_read(guard_on=False)))
    off = await skill_projects.vocabulary(SUB)
    assert "the server says you may edit it" in off and "shared by" not in off
