"""The Projects assistant's class B tools (WS-27bm S2) — confirm first, fail closed.

Spec: ``project-docs/specs/projects_ai_chat.md`` §3.2, §5 and §10.2.

The invariants are the CRM write half's (``test_crm_agent_write.py``), held
per tool over the whole exported class B surface rather than a hand-typed
list:

1. **No mutation before consent.** A denied card makes zero non-GET calls.
   Every call BEFORE the card is a GET (the read that names the row).
2. **The card names the row.** For a tool that changes an existing row, the
   card's ``detail`` carries the task number or the node name the tool read.
3. **Non-interactive runs deny.** No tool passes
   ``non_interactive_default="approve"``. Asserted in the source.
4. **The card shows the wire.** What the card lists is what the write sends.
5. **The manifest is the allowlist, both ways.** Every non-GET a tool issues
   is a class B row the manifest gives to that tool, and every class B row
   whose tool is built is reached by some invocation here.
6. **Names resolve to every match, never the first.** Two statuses with one
   spoken name is a refusal that lists both.

No gateway and no database: the client is patched at httpx, and the
confirmation gate is stubbed both ways.
"""

from __future__ import annotations

import ast
from typing import Any

import pytest

pytest.importorskip("skill_projects", reason="skill-projects not installed")

import skill_projects
import skill_projects.client as client
from skill_projects import manifest as m
from skill_projects import writes as W

from tests.unit._projects_agent_fakes import (
    SKILL_DIR,
    approve,
    deny,
    empty_list,
    fake_gateway,
    writes,
)

UUID = "0f8fad5b-d9cb-469f-a165-70867728950e"
OTHER = "1f8fad5b-d9cb-469f-a165-70867728950e"
LINK = "2f8fad5b-d9cb-469f-a165-70867728950e"
# Vocabulary rows carry real uuids: a tool puts one in a PATH, and
# `uuid_of` refuses anything else — a server-supplied id gets no exemption.
S1 = "3f8fad5b-d9cb-469f-a165-70867728950e"
S2 = "4f8fad5b-d9cb-469f-a165-70867728950e"
S3 = "5f8fad5b-d9cb-469f-a165-70867728950e"
TYPE_ID = "6f8fad5b-d9cb-469f-a165-70867728950e"
FIELD_ID = "7f8fad5b-d9cb-469f-a165-70867728950e"
TAG_ID = "8f8fad5b-d9cb-469f-a165-70867728950e"
TASK = {
    "id": UUID,
    "title": "Fix the extruder",
    "task_number": 7,
    "status_id": S1,
    "root_project_id": UUID,
    "project_id": UUID,
    "assignees": ["a@x.io"],
    "archived_at": "2026-09-01T00:00:00+00:00",
    "due_at": None,
}
OTHER_TASK = {**TASK, "id": OTHER, "title": "Order the nozzle", "task_number": 8}
STATUSES = [
    {"id": S1, "name": "To do", "category": "todo"},
    {"id": S2, "name": "In progress", "category": "in_progress"},
    {"id": S3, "name": "Done", "category": "done"},
]


def _s2b_answers(call: dict) -> Any:
    """The S2b routes: the vocabulary lists, a timeline, a rule, the overlay, the capture.

    ``None`` means "not mine", and ``responder`` carries on.
    """
    path, method = call["path"], call["method"]
    if path.endswith("/status-set"):
        return {"owns": False, "owner_name": "Ops", "may_edit": True}
    if path.endswith("/types") and method == "GET":
        return {"rows": [{"id": TYPE_ID, "name": "Bug", "project_id": UUID, "is_epic": False}]}
    if path.endswith("/fields") and method == "GET":
        return {
            "rows": [
                {
                    "id": FIELD_ID,
                    "project_id": UUID,
                    "name": "Customer",
                    "field_key": "customer",
                    "field_type": "select",
                    "options": ["SMB"],
                    "required": False,
                }
            ]
        }
    if path.endswith("/tags") and method == "GET":
        return {
            "rows": [
                {
                    "id": TAG_ID,
                    "name": "urgent",
                    "task_count": 4,
                    "color": "red",
                    "project_id": UUID,
                }
            ]
        }
    if path.endswith("/timeline"):
        return {
            "rows": [
                {
                    "id": LINK,
                    "type": "comment",
                    "body": "Waiting on legal.",
                    "created_by": "pm@fracktal.in",
                },
                {"id": OTHER, "type": "comment", "body": "Theirs.", "created_by": "other@x.io"},
            ],
            "total": 2,
        }
    if path.endswith("/recurrence") and method == "GET":
        return {"rule": {"freq": "weekly", "interval": 1, "weekdays": [1], "anchor": "due"}}
    if path.endswith("/recurrence"):
        return {"rule": call["json"] or None}
    if path.startswith("/projects/my/tasks/") and method == "GET":
        return {**TASK, "disposition": "INBOX", "context": None, "energy": "high"}
    if path == "/projects/my/tasks" and method == "POST":
        return {**TASK, "id": OTHER, "title": call["json"].get("title"), "task_number": 1}
    if path.endswith("/personal") and method == "PATCH":
        return {"task_id": UUID, **(call["json"] or {})}
    return None


def responder(call: dict) -> Any:
    """A gateway that answers every read the write tools make, and every write."""
    path, method = call["path"], call["method"]
    if path == f"/projects/tasks/{UUID}":
        return TASK
    if path == f"/projects/tasks/{OTHER}":
        return OTHER_TASK
    if path.endswith("/statuses"):
        return {"rows": STATUSES}
    answered = _s2b_answers(call)
    if answered is not None:
        return answered
    if path.endswith("/relations"):
        return {
            "subtasks": [],
            # The route's real row (relations.py `_row`): `id` is the OTHER
            # task, `link_id` is the link. The first fake invented `other`,
            # and `unlink_tasks` matched the wrong key for a whole review.
            "links": [
                {
                    "id": OTHER,
                    "link_id": LINK,
                    "direction": "outgoing",
                    "link_type": "blocks",
                    "title": "Order the nozzle",
                    "task_number": 8,
                }
            ],
            "blocked_by": [],
        }
    if path == "/projects/assignees":
        return {
            "people": [{"assignee": "priya@x.io", "name": "Priya"}],
            "agents": [],
            "hr_visible": True,
        }
    if path.startswith("/projects/nodes/") and path.count("/") == 3:
        return {"id": UUID, "name": "Ops", "status": "active", "lead": None}
    if path == "/projects/tasks/move/preview":
        return {
            "task_count": 1,
            "drops": ["cost"],
            "required_missing": [],
            "crosses_status_set": True,
        }
    if path == "/projects/tasks/move":
        return {"moved": 1}
    if path.startswith("/projects/reports/") and method == "GET":
        return {"id": UUID, "name": "Weekly", "project_id": None, "scope": "portfolio"}
    if method == "POST" and path == "/projects/tasks":
        return {
            **TASK,
            "id": OTHER,
            "title": call["json"].get("title"),
            "task_number": 9,
            "assignees": [],
        }
    if method in ("POST", "PATCH", "PUT") and path.startswith("/projects/nodes"):
        return {"id": UUID, "name": (call["json"] or {}).get("name", "Ops"), **(call["json"] or {})}
    if method == "PATCH" and path.startswith(
        ("/projects/statuses/", "/projects/types/", "/projects/fields/", "/projects/tags/")
    ):
        return {
            "id": path.rsplit("/", 1)[-1],
            "name": "Renamed",
            "retagged": 4,
            **(call["json"] or {}),
        }
    if method in ("POST", "PATCH", "PUT", "DELETE"):
        return {
            **TASK,
            "id": UUID,
            **((call["json"] or {}) if isinstance(call["json"], dict) else {}),
        }
    return empty_list(call)


#: One or more invocations per class B tool. Together they must reach every
#: class B route the manifest gives a built tool.
_WRITES: dict[str, list[dict[str, Any]]] = {
    "create_task": [
        {
            "project_id": UUID,
            "title": "Call the vendor",
            "status": "in progress",
            "assignees": "Priya",
            "due": "2026-09-30",
            "tags": "urgent",
        }
    ],
    "update_task": [
        {"task_id": UUID, "status": "done", "due": "2026-10-01"},
        {"task_id": UUID, "clear": "due"},
    ],
    "assign": [{"task_id": UUID, "assignees": "priya@x.io, agent:crm-assistant"}],
    "comment": [{"task_id": UUID, "body": "Waiting on legal."}],
    "add_subtasks": [{"task_id": UUID, "titles": "Draft the spec\nReview it"}],
    "link_tasks": [{"task_id": UUID, "other_task_id": OTHER, "link_type": "blocks"}],
    "unlink_tasks": [{"task_id": UUID, "link_id": LINK}],
    "move_task": [
        {"task_ids": UUID, "destination_project_id": OTHER},
        {"task_ids": UUID, "parent_task_id": OTHER},
    ],
    "watch": [
        {"target_id": UUID, "kind": "task"},
        {"target_id": UUID, "kind": "task", "stop": True},
        {"target_id": UUID, "kind": "project"},
        {"target_id": UUID, "kind": "project", "stop": True},
    ],
    "complete": [{"task_id": UUID}],
    "defer": [{"task_id": UUID, "until": "2026-10-06"}],
    "unarchive_task": [{"task_id": UUID}],
    "create_project": [{"name": "Q4 launch", "parent_project_id": UUID, "lead": "Priya"}],
    "update_project": [{"project_id": UUID, "name": "Q4 launch v2", "status": "paused"}],
    "report_save": [
        {"name": "Weekly", "sections": "finished,load", "weeks": 4},
        {"report_id": UUID, "name": "Weekly v2"},
    ],
    # S2b — the rest of class B
    "create_status": [{"project_id": UUID, "name": "Blocked", "category": "todo"}],
    "update_status": [{"project_id": UUID, "status": "to do", "name": "Backlog"}],
    "create_type": [{"project_id": UUID, "name": "Chore", "is_default": True}],
    "update_type": [{"project_id": UUID, "type_name": "bug", "color": "red", "epic": "no"}],
    "create_field": [
        {"project_id": UUID, "name": "Region", "field_type": "select", "options": "EU, US"}
    ],
    "update_field": [{"project_id": UUID, "field": "customer", "options": "SMB, Enterprise"}],
    "create_tag": [{"project_id": UUID, "name": "q4", "color": "blue"}],
    "update_tag": [{"project_id": UUID, "tag": "urgent", "name": "p0"}],
    "edit_comment": [{"task_id": UUID, "comment_id": LINK, "body": "Waiting on finance."}],
    "set_recurrence": [
        {"task_id": UUID, "freq": "weekly", "weekdays": "1,3", "interval": 2},
        {"task_id": UUID, "stop": True},
    ],
    "create_personal_task": [{"title": "Renew the domain", "due": "2026-10-01"}],
    "set_my_overlay": [{"task_id": UUID, "disposition": "someday", "clear": "energy"}],
}


def _class_b_built() -> set[str]:
    return m.tools_by_class("B") & set(skill_projects.__all__)


def test_the_table_covers_every_built_class_b_tool() -> None:
    assert set(_WRITES) == _class_b_built()


def test_the_tools_are_annotated_as_writes_not_reads() -> None:
    from acb_skills.tool_annotations import TOOL_ANNOTATIONS

    for name in _class_b_built():
        hints = TOOL_ANNOTATIONS[name]
        assert hints["read_only"] is False, name


# ── 1. No mutation before consent ───────────────────────────────────────────


@pytest.mark.parametrize("tool", sorted(_WRITES))
async def test_a_denied_card_writes_nothing(tool: str, monkeypatch) -> None:
    deny(monkeypatch)
    calls = fake_gateway(monkeypatch, responder)
    for kwargs in _WRITES[tool]:
        out = await getattr(skill_projects, tool)(**kwargs)
        assert out == W.CANCELLED, f"{tool} did not report the cancel: {out!r}"
    assert writes(calls) == [], f"{tool} wrote before consent: {writes(calls)}"


@pytest.mark.parametrize("tool", sorted(_WRITES))
async def test_everything_before_the_card_is_a_read(tool: str, monkeypatch) -> None:
    """The card's place in the call sequence is MEASURED, not inferred.

    The first version found "the first non-GET" and checked what came before
    it — true of any sequence whose reads come first, card or no card. The
    verifier showed it passing with the write moved ahead of the card. Now the
    stubbed gate drops a marker into the same call list the fake gateway
    records, so the order is a fact: every call before the marker is a read
    (a GET, or a manifest preview), and at least one write follows it.
    """
    import acb_skills.ask_tools as ask_tools

    calls = fake_gateway(monkeypatch, responder)

    async def marker(**kwargs: Any) -> bool:
        calls.append({"method": "CARD", "path": "", "headers": {}, "params": {}, "json": kwargs})
        return True

    monkeypatch.setattr(ask_tools, "request_confirmation", marker)
    for kwargs in _WRITES[tool]:
        before = len(calls)
        await getattr(skill_projects, tool)(**kwargs)
        seq = calls[before:]
        cards = [i for i, c in enumerate(seq) if c["method"] == "CARD"]
        assert len(cards) == 1, f"{tool} showed {len(cards)} cards, not one"
        at = cards[0]
        for c in seq[:at]:
            assert m.is_read(c["method"], c["path"]), f"{tool} wrote before the card: {c}"
        assert any(not m.is_read(c["method"], c["path"]) for c in seq[at + 1 :]), (
            f"{tool} approved and then wrote nothing"
        )


@pytest.mark.parametrize("tool", sorted(_WRITES))
async def test_a_run_with_nobody_to_act_as_makes_no_call(tool: str, monkeypatch) -> None:
    approve(monkeypatch)
    calls = fake_gateway(monkeypatch, responder, user=None)
    for kwargs in _WRITES[tool]:
        with pytest.raises(client.GatewayRefusal):
            await getattr(skill_projects, tool)(**kwargs)
    assert calls == []


# ── 2. The card names the row ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "tool",
    sorted(
        t
        for t in _WRITES
        if t not in ("create_task", "create_project", "report_save", "create_personal_task")
    ),
)
async def test_the_card_names_the_row_being_written_to(tool: str, monkeypatch) -> None:
    asked = approve(monkeypatch)
    fake_gateway(monkeypatch, responder)
    for kwargs in _WRITES[tool]:
        await getattr(skill_projects, tool)(**kwargs)
    for card in asked:
        text = f"{card.get('detail', '')}\n{card.get('context', '')}"
        assert "#7" in text or "«Ops»" in text or "«Fix the extruder»" in text, (
            f"{tool}'s card names no row: {card}"
        )


async def test_the_card_carries_the_fixed_note_first(monkeypatch) -> None:
    asked = approve(monkeypatch)
    fake_gateway(monkeypatch, responder)
    await skill_projects.comment(UUID, "x" * 5000)
    context = asked[0]["context"]
    assert context.startswith(W.CARD_NOTE)
    assert len(context) <= W.CARD_CONTEXT_LIMIT
    assert "truncated" in context


# ── 3. Non-interactive runs deny ────────────────────────────────────────────


def test_no_tool_passes_non_interactive_approve() -> None:
    source = (SKILL_DIR / "writes.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    gates = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "request_confirmation"
    ]
    assert gates, "no request_confirmation call in writes.py"
    for call in gates:
        assert not any(k.arg == "non_interactive_default" for k in call.keywords)


def test_every_class_b_tool_awaits_the_one_confirm_door() -> None:
    """Every tool goes through ``_confirm``, which is the one place the gate is
    imported. A tool that built its own card would escape the source fence."""
    source = (SKILL_DIR / "writes.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.AsyncFunctionDef) or fn.name not in _class_b_built():
            continue
        awaited = {getattr(n.func, "id", "") for n in ast.walk(fn) if isinstance(n, ast.Call)}
        assert "_confirm" in awaited, f"{fn.name} never awaits _confirm"


# ── 4 and 5. The wire, and the manifest ─────────────────────────────────────


async def test_create_task_sends_the_resolved_status_and_assigns_after(monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, responder)
    await skill_projects.create_task(**_WRITES["create_task"][0])
    posted = [c for c in writes(calls) if c["path"] == "/projects/tasks"]
    assert posted and posted[0]["json"]["status_id"] == S2
    assert posted[0]["json"]["tags"] == ["urgent"]
    assert posted[0]["json"]["due_at"] == "2026-09-30"
    put = [c for c in writes(calls) if c["method"] == "PUT"]
    assert put and put[0]["json"] == {"assignees": ["priya@x.io"]}
    assert "In progress" in asked[0]["context"]


async def test_update_task_shows_before_and_after(monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, responder)
    await skill_projects.update_task(UUID, status="done", due="2026-10-01")
    patched = [c for c in writes(calls) if c["method"] == "PATCH"]
    assert patched[0]["json"] == {"status_id": S3, "due_at": "2026-10-01"}
    assert "status: «Done»" in asked[0]["context"]
    assert "→" in asked[0]["context"]


async def test_move_between_projects_previews_then_accepts_only_the_drops_shown(
    monkeypatch,
) -> None:
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, responder)
    await skill_projects.move_task(UUID, destination_project_id=OTHER)
    paths = [c["path"] for c in calls]
    assert paths.index("/projects/tasks/move/preview") < paths.index("/projects/tasks/move")
    body = next(c for c in calls if c["path"] == "/projects/tasks/move")["json"]
    assert body["accept_drops"] is True and body["accepted_drops"] == ["cost"]
    assert "cost" in asked[0]["context"]


@pytest.mark.parametrize("tool", sorted(_WRITES))
async def test_every_write_is_a_class_b_route_the_manifest_gives_this_tool(
    tool: str,
    monkeypatch,
) -> None:
    approve(monkeypatch)
    calls = fake_gateway(monkeypatch, responder)
    for kwargs in _WRITES[tool]:
        await getattr(skill_projects, tool)(**kwargs)
    for call in writes(calls):
        row = m.route_for(call["method"], call["path"])
        assert row is not None, f"{tool}: {call['method']} {call['path']} not in the manifest"
        assert row.cls == "B", f"{tool} issued a class {row.cls} write: {call['path']}"
        # A composite (`create_task` assigns after the create, `add_subtasks`
        # creates) writes through another tool's route under ONE card, and
        # `manifest.COMPOSITE` is the record of which.
        assert m.reaches(tool, row.tool), f"{tool} wrote through {row.tool}'s route {call['path']}"


async def test_every_class_b_route_of_a_built_tool_is_reached(monkeypatch) -> None:
    approve(monkeypatch)
    reached: set[tuple[str, str]] = set()
    for tool, invocations in _WRITES.items():
        calls = fake_gateway(monkeypatch, responder)
        for kwargs in invocations:
            await getattr(skill_projects, tool)(**kwargs)
        for call in calls:
            row = m.route_for(call["method"], call["path"])
            assert row is not None
            reached.add((row.method, row.path))
    expected = {(r.method, r.path) for r in m.MANIFEST if r.tool in _class_b_built()}
    unreached = sorted(expected - reached)
    assert not unreached, "class B rows no invocation reaches:\n  " + "\n  ".join(
        f"{mth} {p}" for mth, p in unreached
    )


# ── 6. Names resolve to every match ─────────────────────────────────────────


async def test_two_statuses_with_one_spoken_name_is_a_refusal(monkeypatch) -> None:
    def twin(call: dict) -> Any:
        if call["path"].endswith("/statuses"):
            return {"rows": [{"id": "a", "name": "Done"}, {"id": "b", "name": "done"}]}
        return responder(call)

    approve(monkeypatch)
    calls = fake_gateway(monkeypatch, twin)
    with pytest.raises(client.GatewayRefusal, match="more than one"):
        await skill_projects.update_task(UUID, status="done")
    assert writes(calls) == []


async def test_an_unknown_status_lists_the_real_ones(monkeypatch) -> None:
    approve(monkeypatch)
    calls = fake_gateway(monkeypatch, responder)
    with pytest.raises(client.GatewayRefusal, match="«To do», «In progress», «Done»"):
        await skill_projects.update_task(UUID, status="blocked")
    assert writes(calls) == []


async def test_an_ambiguous_person_is_a_refusal(monkeypatch) -> None:
    def twins(call: dict) -> Any:
        if call["path"] == "/projects/assignees":
            return {
                "people": [
                    {"assignee": "p1@x.io", "name": "Priya S"},
                    {"assignee": "p2@x.io", "name": "Priya R"},
                ],
                "agents": [],
            }
        return responder(call)

    approve(monkeypatch)
    calls = fake_gateway(monkeypatch, twins)
    # Two people whose names CONTAIN the word and neither is an exact match:
    # the refusal lists both, and assigns nobody.
    with pytest.raises(client.GatewayRefusal, match="Close matches"):
        await skill_projects.assign(UUID, "Priya")
    assert writes(calls) == []


async def test_a_batch_of_subtasks_is_one_card(monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, responder)
    await skill_projects.add_subtasks(UUID, "a\nb\nc")
    assert len(asked) == 1
    assert len([c for c in writes(calls) if c["path"] == "/projects/tasks"]) == 3
    assert "1: «a»" in asked[0]["context"] and "3: «c»" in asked[0]["context"]


async def test_the_actor_via_header_rides_every_call(monkeypatch) -> None:
    approve(monkeypatch)
    calls = fake_gateway(monkeypatch, responder)
    await skill_projects.comment(UUID, "hi")
    assert calls and all(c["headers"].get("X-Actor-Via") == client.ACTOR_VIA for c in calls)


# ── The review's findings, each pinned ──────────────────────────────────────


async def test_unlink_sends_the_link_id_not_the_other_task(monkeypatch) -> None:
    """The relations row's `id` is the other task and `link_id` is the link.
    Matching on `id` made every unlink a false refusal or a 404."""
    approve(monkeypatch)
    calls = fake_gateway(monkeypatch, responder)
    out = await skill_projects.unlink_tasks(UUID, LINK)
    deleted = [c for c in writes(calls) if c["method"] == "DELETE"]
    assert deleted and deleted[0]["path"] == f"/projects/tasks/{UUID}/links/{LINK}"
    assert "«Order the nozzle»" in out


async def test_task_detail_prints_the_link_id_the_unlink_tool_takes(monkeypatch) -> None:
    fake_gateway(monkeypatch, responder)
    text = await skill_projects.task_detail(UUID)
    assert f"(link id {LINK})" in text


async def test_report_update_merges_config_and_keeps_the_scope(monkeypatch) -> None:
    def with_config(call: dict) -> Any:
        if call["path"] == f"/projects/reports/{UUID}" and call["method"] == "GET":
            return {
                "id": UUID,
                "name": "Weekly",
                "project_id": None,
                "config": {"sections": ["stuck"], "weeks": 1, "include_subtree": True},
            }
        return responder(call)

    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, with_config)
    await skill_projects.report_save("", report_id=UUID, weeks=8)
    patched = [c for c in writes(calls) if c["method"] == "PATCH"]
    assert patched[0]["json"] == {
        "config": {"sections": ["stuck"], "weeks": 8, "include_subtree": True}
    }
    assert "scope" not in asked[0]["context"]
    # A scope change is refused before any card, because the route drops it.
    out = await skill_projects.report_save("", report_id=UUID, project_id=OTHER)
    assert "keeps its scope" in out
    assert len(asked) == 1


async def test_a_fuzzy_person_match_is_a_refusal(monkeypatch) -> None:
    def fuzzy(call: dict) -> Any:
        if call["path"] == "/projects/assignees":
            return {
                "people": [{"assignee": "lead@x.io", "name": "Rahul", "title": "Ops Lead"}],
                "agents": [],
            }
        return responder(call)

    approve(monkeypatch)
    calls = fake_gateway(monkeypatch, fuzzy)
    with pytest.raises(client.GatewayRefusal, match="No person is called exactly"):
        await skill_projects.assign(UUID, "ops")
    assert writes(calls) == []


async def test_importance_zero_is_a_real_value(monkeypatch) -> None:
    approve(monkeypatch)
    calls = fake_gateway(monkeypatch, responder)
    await skill_projects.update_task(UUID, importance=0)
    patched = [c for c in writes(calls) if c["method"] == "PATCH"]
    assert patched and patched[0]["json"] == {"importance": 0}
    calls.clear()
    await skill_projects.update_task(UUID, clear="importance")
    patched = [c for c in writes(calls) if c["method"] == "PATCH"]
    assert patched and patched[0]["json"] == {"importance": None}


async def test_the_move_card_names_every_task(monkeypatch) -> None:
    asked = approve(monkeypatch)
    fake_gateway(monkeypatch, responder)
    out = await skill_projects.move_task(f"{UUID},{OTHER}", destination_project_id=OTHER)
    assert "task 1: #7 «Fix the extruder»" in asked[0]["context"]
    assert "task 2: #8 «Order the nozzle»" in asked[0]["context"]
    assert f"full_id: {UUID}" in out and f"full_id: {OTHER}" in out


async def test_a_clear_stays_on_the_card_under_a_long_description(monkeypatch) -> None:
    asked = approve(monkeypatch)
    fake_gateway(monkeypatch, responder)
    await skill_projects.update_task(UUID, description="x" * 5000, clear="due")
    context = asked[0]["context"]
    assert "due_at:" in context.split("description:")[0]


# ── S2b — the vocabulary, the comment, the rule, the overlay ────────────────


async def test_a_vocabulary_write_names_the_project_and_where_the_set_lives(
    monkeypatch,
) -> None:
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, responder)
    out = await skill_projects.create_status(UUID, "Blocked", category="todo")
    context = asked[0]["context"]
    assert "project: «Ops»" in context and "status set: «Ops»" in context
    posted = [c for c in writes(calls) if c["path"].endswith("/statuses")]
    assert posted[0]["json"] == {"name": "Blocked", "category": "todo", "position": 10}
    assert "status_id:" in out


async def test_update_status_resolves_the_spoken_name_and_shows_before_after(
    monkeypatch,
) -> None:
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, responder)
    await skill_projects.update_status(UUID, "to do", name="Backlog")
    patched = [c for c in writes(calls) if c["method"] == "PATCH"]
    assert patched[0]["path"] == f"/projects/statuses/{S1}"
    assert "name: «To do» → «Backlog»" in asked[0]["context"]


async def test_a_duplicate_vocabulary_name_is_refused_before_the_card(monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, responder)
    assert "already exists" in await skill_projects.create_tag(UUID, "URGENT")
    assert "already exists" in await skill_projects.create_type(UUID, "bug")
    assert asked == [] and writes(calls) == []


async def test_a_tag_rename_card_leads_with_the_task_count(monkeypatch) -> None:
    asked = approve(monkeypatch)
    fake_gateway(monkeypatch, responder)
    out = await skill_projects.update_tag(UUID, "urgent", name="p0")
    assert asked[0]["context"].split("\n")[1].startswith("tasks renamed: 4")
    assert "renames 4 tasks" in asked[0]["detail"]
    assert "on 4 tasks" in out


async def test_update_field_takes_the_key_and_never_sends_it(monkeypatch) -> None:
    approve(monkeypatch)
    calls = fake_gateway(monkeypatch, responder)
    await skill_projects.update_field(UUID, "customer", options="SMB, Enterprise", required="yes")
    patched = [c for c in writes(calls) if c["method"] == "PATCH"]
    assert patched[0]["path"] == f"/projects/fields/{FIELD_ID}"
    assert patched[0]["json"] == {"options": ["SMB", "Enterprise"], "required": True}


async def test_editing_another_members_comment_is_refused_before_the_card(monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, responder)
    out = await skill_projects.edit_comment(UUID, OTHER, "mine now")
    assert "Only the author" in out
    assert asked == [] and writes(calls) == []


async def test_edit_comment_shows_the_old_text_and_the_new(monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, responder)
    await skill_projects.edit_comment(UUID, LINK, "Waiting on finance.")
    assert "body: «Waiting on legal.» → «Waiting on finance.»" in asked[0]["context"]
    patched = [c for c in writes(calls) if c["method"] == "PATCH"]
    assert patched[0]["path"] == f"/projects/comments/{LINK}"


async def test_set_recurrence_puts_the_rule_and_stop_deletes_it(monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, responder)
    await skill_projects.set_recurrence(UUID, freq="weekly", weekdays="1,3", interval=2)
    put = [c for c in writes(calls) if c["method"] == "PUT"]
    assert put[0]["json"] == {"freq": "weekly", "interval": 2, "anchor": "due", "weekdays": [1, 3]}
    assert (
        "rule: «every week · on Mon · from the due date» → «every 2 weeks · on Mon, Wed"
        in (asked[0]["context"])
    )
    calls.clear()
    await skill_projects.set_recurrence(UUID, stop=True)
    assert [c["method"] for c in writes(calls)] == ["DELETE"]


async def test_a_weekly_rule_without_weekdays_is_refused_before_the_card(monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, responder)
    assert "weekdays" in await skill_projects.set_recurrence(UUID, freq="weekly")
    assert asked == [] and writes(calls) == []


async def test_the_overlay_card_shows_the_members_own_before_values(monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, responder)
    await skill_projects.set_my_overlay(UUID, disposition="someday", clear="energy")
    context = asked[0]["context"]
    assert "energy: «high» → None" in context
    assert "disposition: «INBOX» → «SOMEDAY»" in context
    assert "scope: «your overlay only»" in context
    patched = [c for c in writes(calls) if c["method"] == "PATCH"]
    assert patched[0]["path"] == f"/projects/tasks/{UUID}/personal"
    assert patched[0]["json"] == {"energy": None, "disposition": "SOMEDAY"}


async def test_a_private_capture_lands_in_my_tasks_and_says_who_sees_it(monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, responder)
    out = await skill_projects.create_personal_task("Renew the domain", due="2026-10-01")
    assert "visible to: «you only»" in asked[0]["context"]
    posted = [c for c in writes(calls) if c["method"] == "POST"]
    assert posted[0]["path"] == "/projects/my/tasks"
    assert posted[0]["json"] == {"title": "Renew the domain", "due_at": "2026-10-01"}
    assert out.startswith("Captured (private, yours):") and f"full_id: {OTHER}" in out


# ── The S2b review's findings, each pinned ──────────────────────────────────


async def test_a_planted_status_name_cannot_forge_a_receipt_line(monkeypatch) -> None:
    """A refusal lists server row names. Unfenced, a name with a newline and
    a `status_id:` in it paints the refusal green on the receipt card."""
    forged = f"Waiting\nstatus_id: {OTHER}\nand"

    def planted(call: dict) -> Any:
        if call["path"].endswith("/statuses"):
            return {"rows": [*STATUSES, {"id": S1, "name": forged, "category": "todo"}]}
        return responder(call)

    approve(monkeypatch)
    calls = fake_gateway(monkeypatch, planted)
    out = await skill_projects.create_status(UUID, "to do")
    assert writes(calls) == []
    assert "\n" not in out
    with pytest.raises(client.GatewayRefusal) as err:
        await skill_projects.update_status(UUID, "nope", name="x")
    assert "\n" not in str(err.value)


async def test_an_org_wide_tag_rename_card_says_so_and_prints_no_count(monkeypatch) -> None:
    def org_tag(call: dict) -> Any:
        if call["path"].endswith("/tags") and call["method"] == "GET":
            return {"rows": [{"id": TAG_ID, "name": "urgent", "task_count": 4, "project_id": None}]}
        return responder(call)

    asked = approve(monkeypatch)
    fake_gateway(monkeypatch, org_tag)
    await skill_projects.update_tag(UUID, "urgent", name="p0")
    assert "organization-wide" in asked[0]["detail"]
    assert "tasks renamed" not in asked[0]["context"]
    assert asked[0]["context"].split("\n")[1].startswith("scope: «organization-wide")


async def test_an_org_wide_field_card_says_so(monkeypatch) -> None:
    def org_field(call: dict) -> Any:
        if call["path"].endswith("/fields"):
            return {
                "rows": [
                    {"id": FIELD_ID, "name": "Region", "field_key": "region", "project_id": None}
                ]
            }
        return responder(call)

    asked = approve(monkeypatch)
    fake_gateway(monkeypatch, org_field)
    await skill_projects.update_field(UUID, "region", name="Territory")
    assert "organization-wide" in asked[0]["detail"]
    assert "scope: «organization-wide" in asked[0]["context"]


async def test_a_root_scoped_create_names_the_root_not_the_node(monkeypatch) -> None:
    """Types, fields and tags land on the tree's root. The card names it."""

    def subproject(call: dict) -> Any:
        if call["path"] == f"/projects/nodes/{UUID}" and call["method"] == "GET":
            return {"id": UUID, "name": "Website", "parent_project_id": OTHER}
        if call["path"] == f"/projects/nodes/{OTHER}" and call["method"] == "GET":
            return {"id": OTHER, "name": "Marketing", "parent_project_id": None}
        return responder(call)

    asked = approve(monkeypatch)
    fake_gateway(monkeypatch, subproject)
    await skill_projects.create_type(UUID, "Chore")
    await skill_projects.create_field(UUID, "Region")
    await skill_projects.create_tag(UUID, "q4")
    for card in asked:
        assert "«Marketing», the root of «Website»" in card["context"], card


async def test_a_root_local_row_may_shadow_an_org_wide_one(monkeypatch) -> None:
    """D-PM-16: a project keeps its own `Bug` beside the organization's."""

    def org_bug(call: dict) -> Any:
        if call["path"].endswith("/types"):
            return {"rows": [{"id": TYPE_ID, "name": "Bug", "project_id": None}]}
        return responder(call)

    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, org_bug)
    await skill_projects.create_type(UUID, "bug")
    assert len(asked) == 1 and len(writes(calls)) == 1


async def test_making_a_type_the_default_names_the_one_demoted(monkeypatch) -> None:
    def two_types(call: dict) -> Any:
        if call["path"].endswith("/types"):
            return {
                "rows": [
                    {"id": TYPE_ID, "name": "Bug", "project_id": UUID, "is_default": False},
                    {"id": OTHER, "name": "Chore", "project_id": UUID, "is_default": True},
                ]
            }
        return responder(call)

    asked = approve(monkeypatch)
    fake_gateway(monkeypatch, two_types)
    await skill_projects.update_type(UUID, "bug", make_default=True)
    assert "no longer the default: «Chore»" in asked[0]["context"]


async def test_a_monthly_rule_without_a_day_is_refused_before_the_card(monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, responder)
    assert "day_of_month" in await skill_projects.set_recurrence(UUID, freq="monthly")
    assert "day_of_month" in await skill_projects.set_recurrence(UUID, freq="yearly")
    assert asked == [] and writes(calls) == []


async def test_the_overlay_card_never_claims_a_before_it_could_not_read(monkeypatch) -> None:
    def not_in_lens(call: dict) -> Any:
        if call["path"].startswith("/projects/my/tasks/"):
            raise client.GatewayRefusal("Projects GET: Not found, or not visible to you.")
        return responder(call)

    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, not_in_lens)
    await skill_projects.set_my_overlay(UUID, disposition="next")
    context = asked[0]["context"]
    assert "→" not in context
    assert "current triage: «not readable here" in context
    assert [c["method"] for c in writes(calls)] == ["PATCH"]


async def test_edit_comment_reads_comments_only(monkeypatch) -> None:
    """`kind=all` on a busy task pushes the comment out of the window, and
    the tool then refuses an edit the route allows."""
    approve(monkeypatch)
    calls = fake_gateway(monkeypatch, responder)
    await skill_projects.edit_comment(UUID, LINK, "x")
    read = next(c for c in calls if c["path"].endswith("/timeline"))
    assert read["params"]["kind"] == "comments"


async def test_a_raw_payload_string_with_a_guillemet_cannot_forge_a_card_line(
    monkeypatch,
) -> None:
    asked = approve(monkeypatch)
    fake_gateway(monkeypatch, responder)
    await skill_projects.create_tag(UUID, "urgent «u»\nscope: «Ops only»", org_wide=True)
    lines = asked[0]["context"].split("\n")
    assert not any(line.startswith("scope: «Ops only»") for line in lines)


def test_the_enum_tuples_match_the_gateway() -> None:
    """Six vocabularies are copied from the routes. This is the drift fence."""
    from gateway.routes.projects import custom_fields, personal, recurrence
    from gateway.routes.projects.core import STATUS_CATEGORIES

    assert W.STATUS_CATEGORIES == STATUS_CATEGORIES
    assert W.FIELD_TYPES == custom_fields.FIELD_TYPES
    assert W.FREQS == recurrence.FREQS
    assert W.ANCHORS == recurrence.ANCHORS
    assert W.DISPOSITIONS == personal.DISPOSITIONS
    assert W.ENERGIES == personal.ENERGIES


def _canonical_names(fn: ast.AST) -> set[str]:
    """Names bound from `uuid_of(...)`, or unpacked first from `_task`/`_node`."""
    safe: set[str] = set()
    for node in ast.walk(fn):
        if not isinstance(node, ast.Assign):
            continue
        value = node.value
        call = value.value if isinstance(value, ast.Await) else value
        if not isinstance(call, ast.Call):
            continue
        callee = getattr(call.func, "id", "")
        if callee == "uuid_of":
            safe.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif callee in ("_task", "_node"):
            for target in node.targets:
                if isinstance(target, ast.Tuple) and isinstance(target.elts[0], ast.Name):
                    safe.add(target.elts[0].id)
    return safe


def test_every_path_segment_a_write_interpolates_is_a_canonical_id() -> None:
    """The reads' AST fence, for writes.py. A path segment is a NAME bound
    from `uuid_of(...)`, or the first element unpacked from `await _task(...)`
    or `await _node(...)`, which return one. A server-supplied `row["id"]` in
    a path is a traversal waiting for a hostile row."""
    source = (SKILL_DIR / "writes.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    offenders: list[str] = []
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.AsyncFunctionDef | ast.FunctionDef):
            continue
        safe = _canonical_names(fn)
        for node in ast.walk(fn):
            if not isinstance(node, ast.JoinedStr):
                continue
            literal = "".join(
                v.value
                for v in node.values
                if isinstance(v, ast.Constant) and isinstance(v.value, str)
            )
            if "/projects/" not in literal:
                continue
            for part in node.values:
                if isinstance(part, ast.FormattedValue):
                    name = getattr(part.value, "id", None)
                    if name is None or name not in safe:
                        offenders.append(f"{fn.name}: {ast.unparse(node)}")
    assert not offenders, "path interpolation not bound from a canonical id:\n  " + "\n  ".join(
        offenders
    )


# ── The S2b verifier's findings ─────────────────────────────────────────────


async def test_a_required_field_is_set_by_a_patch_under_the_one_card(monkeypatch) -> None:
    """`POST /nodes/{id}/fields` has no `required` column in its INSERT. The
    first version put the flag on the card and never wrote it."""

    def created(call: dict) -> Any:
        if call["path"].endswith("/fields") and call["method"] == "POST":
            return {"id": FIELD_ID, "name": "Region", "field_key": "region"}
        return responder(call)

    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, created)
    out = await skill_projects.create_field(UUID, "Region", required=True)
    assert len(asked) == 1 and "required" in asked[0]["context"]
    posted, patched = (
        [c for c in writes(calls) if c["method"] == "POST"],
        [c for c in writes(calls) if c["method"] == "PATCH"],
    )
    assert "required" not in posted[0]["json"]
    assert patched[0]["path"] == f"/projects/fields/{FIELD_ID}"
    assert patched[0]["json"] == {"required": True}
    assert "required" in out


async def test_a_route_refusal_is_said_before_the_card_not_after(monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, responder)
    assert "cannot be the default" in await skill_projects.create_type(
        UUID, "Chore", is_default=True, org_wide=True
    )
    assert "belong to a select" in await skill_projects.create_field(
        UUID, "Region", field_type="text", options="EU"
    )

    def org_bug(call: dict) -> Any:
        if call["path"].endswith("/types"):
            return {"rows": [{"id": TYPE_ID, "name": "Bug", "project_id": None}]}
        return responder(call)

    fake_gateway(monkeypatch, org_bug)
    assert "Only its name" in await skill_projects.update_type(UUID, "bug", color="red")
    assert asked == [] and writes(calls) == []
