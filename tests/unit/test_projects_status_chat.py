"""WS-42 PST-4 — the chat status tools guard a shared set and place by slot.

Spec: ``project-docs/specs/projects_settings.md`` §7.1 (PST-4) and §9.4.

On 2026-11-05 the chat passed the id of a subproject that used the space's set.
33 writes then landed on a set that 28 projects share. ``create_status`` named
the subproject on its receipt, and ``position`` was a raw sort key.

With the flag on (``guard_on`` on the status-set read, the route's answer):

* a project that does not own its set gets a refusal that names the owner and
  N, and offers ``set_status_set`` mode=own first;
* ``change_shared_set=true`` shows a card with "Changes the statuses of {owner}
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
    assert "change_shared_set=true" in out
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
    out = await skill_projects.create_status(SUB, "Blocked", change_shared_set=True)
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
    await skill_projects.update_status(SUB, "Done", name="Finished", change_shared_set=True)
    await skill_projects.delete_status(SUB, "Done", move_to="Shipped", change_shared_set=True)
    assert len(asked) == 2
    for card in asked:
        assert "Changes the statuses of «Client Projects» for 28 projects" in card["detail"]


async def test_one_project_reads_in_the_singular(monkeypatch) -> None:
    asked = approve(monkeypatch)
    fake_gateway(monkeypatch, _gateway(_read(used_by=1, reach=1)))
    await skill_projects.create_status(SUB, "Blocked", change_shared_set=True)
    assert "for 1 project" in asked[0]["detail"] and "1 projects" not in asked[0]["detail"]


async def test_a_denied_card_writes_nothing(monkeypatch) -> None:
    deny(monkeypatch)
    calls = fake_gateway(monkeypatch, _gateway(_read()))
    out = await skill_projects.create_status(SUB, "Blocked", change_shared_set=True)
    assert "Added" not in out and writes(calls) == []


# ── 3. Slot and after ───────────────────────────────────────────────────────


async def test_the_flag_on_sends_a_slot_and_never_a_position(monkeypatch) -> None:
    """M-b. A create with no place goes LAST in its stage: one past the lanes."""
    approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _gateway(_read()))
    await skill_projects.create_status(SUB, "Blocked", category="done", change_shared_set=True)
    await skill_projects.create_status(
        SUB, "Early", category="done", slot=2, change_shared_set=True)
    await skill_projects.create_status(
        SUB, "Behind", category="done", after="Shipped", change_shared_set=True)
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
    detail = "Slot 9 is outside the stage. Use a slot from 1 to 4."

    def respond(call: dict) -> Any:
        if call["method"] == "PATCH":
            from tests.unit._projects_agent_fakes import FakeResponse

            res = FakeResponse({"detail": detail}, 422)
            res.text = detail
            return res
        return _gateway(_read(owns=True, owner_id=SUB, used_by=0, reach=1))(call)

    fake_gateway(monkeypatch, respond)
    with pytest.raises(client.GatewayRefusal) as err:
        await skill_projects.update_status(SUB, "Done", slot=9)
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


async def test_the_flag_off_refuses_slot_and_after_and_sends_nothing(monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _gateway(_read(guard_on=False)))
    for out in (
        await skill_projects.create_status(SUB, "Blocked", slot=2),
        await skill_projects.update_status(SUB, "Done", after="Shipped"),
    ):
        assert "slot and after are not open here" in out
    assert asked == [] and writes(calls) == []


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


def test_the_rule_text_gives_the_model_the_order() -> None:
    """Acceptance 9: own set first, then the copy. The shared set only when the
    member names it. The text is in the pack rules and in the agent file."""
    from pathlib import Path

    from acb_skills.tool_packs import _PROJECTS_STRUCTURE_RULES

    flat = " ".join(_PROJECTS_STRUCTURE_RULES.split())
    assert "set_status_set with mode=own first. Then change the copy" in flat
    assert "only when the member names it" in flat and "change_shared_set=true" in flat
    agent = Path(__file__).resolve().parents[2] / "apps/agents/agent-projects/instructions.md"
    text = " ".join(agent.read_text(encoding="utf-8").split())
    assert re.search(r"`mode=own` first\. Then change the copy", text)
    assert "only when the member names it" in text


def test_the_tools_take_the_new_arguments_and_the_manifest_maps_them() -> None:
    import inspect

    from skill_projects import manifest

    for name in ("create_status", "update_status"):
        params = inspect.signature(getattr(skill_projects, name)).parameters
        assert {"slot", "after", "change_shared_set"} <= set(params), name
    assert "change_shared_set" in inspect.signature(skill_projects.delete_status).parameters
    posts = manifest.SENDS[("POST", "/projects/nodes/{project_id}/statuses")]
    patches = manifest.SENDS[("PATCH", "/projects/statuses/{status_id}")]
    assert {"slot", "after"} <= set(posts) and {"slot", "after"} <= set(patches)
