"""HRD-9a: a card of a root-local tag, field or type names the space and N.

Spec: ``project-docs/specs/chat_failure_modes.md`` §6 row HRD-9a, class C5,
and §8 (Q1 to Q5, the defaults apply). Hermetic: the client is faked at its
httpx, so the real manifest check and the real card words run.

A tag, a custom field or a task type of a space is a row of the tree's root
(D-PM-16). A change of it reaches each project of the tree. Before HRD-9a the
edit and delete cards named the node that the member named, and no card gave
a count of projects. Now:

1. The seven edit and delete tools (``update_tag``, ``update_field``,
   ``update_type``, ``delete_tag``, ``delete_field``, ``delete_type``,
   ``merge_tags``) name the SPACE, not the node, for a root-local row.
2. With N of 2 or more, each of the twelve tools (those seven and the five
   creates) adds "Changes the {tags|custom fields|task types} of {space} for
   {N} projects" to the card detail. With N of 1 the card has no line (Q3).
3. A hidden root has no name: the card says "the space above it" (Q5).
4. A failed read of the reach gives the card with the space and no N, and the
   write still happens. No call that passes today is refused.
5. An org-wide row keeps its card of today, and reads no reach.
6. No tool gains an argument (the signatures are recorded here).
7. The create receipts do not change.
8. The rename count of ``update_tag`` comes from ``/tags/{id}/impact``, so an
   archived task counts, as the rewrite counts it (``tags._rewrite``).

Mutations (R7), each turns a test here red:

* one of the seven tools names the node again ->
  ``test_the_seven_tools_name_the_space_not_the_node``;
* a tool gains an argument -> ``test_no_tool_gains_an_argument``;
* the line shows for N of 1 -> ``test_n_of_one_shows_no_line``;
* a failed read stops the tool -> ``test_a_failed_read_shows_the_card_with_no_n``;
* the rename card counts from the list again ->
  ``test_the_rename_count_comes_from_the_impact_read``.

The R8 half of the count is ``test_shared_vocab_reach_r8.py``.
"""

from __future__ import annotations

import inspect
import json
from typing import Any

import pytest

pytest.importorskip("skill_projects", reason="skill-projects not installed")

import skill_projects
from skill_projects import manifest as m
from skill_projects import writes as W

from tests.unit import test_projects_agent_writes as tw
from tests.unit._projects_agent_fakes import (
    FakeResponse,
    approve,
    fake_gateway,
    writes,
)

UUID, OTHER = tw.UUID, tw.OTHER
SPACE, NODE = "Marketing", "Website"
REACH = f"/projects/nodes/{UUID}/vocabulary/reach"

#: The seven edit and delete tools, each with one call that changes a
#: root-local row of the fake gateway.
SEVEN: dict[str, dict[str, Any]] = {
    "update_tag": {"project_id": UUID, "tag": "urgent", "name": "q4"},
    "update_field": {"project_id": UUID, "field": "customer", "name": "Client"},
    "update_type": {"project_id": UUID, "type_name": "bug", "name": "Defect"},
    "delete_tag": {"project_id": UUID, "tag": "urgent"},
    "delete_field": {"project_id": UUID, "field": "customer"},
    "delete_type": {"project_id": UUID, "type_name": "bug"},
    "merge_tags": {"project_id": UUID, "tag": "urgent", "into": "p0"},
}

#: The five create tools.
FIVE: dict[str, dict[str, Any]] = {
    "create_tag": {"project_id": UUID, "name": "q4"},
    "create_field": {"project_id": UUID, "name": "Region"},
    "create_type": {"project_id": UUID, "name": "Chore"},
    "create_tags": {"project_id": UUID, "tags": json.dumps(["q4", "q5"])},
    "create_types": {"project_id": UUID, "types": json.dumps(["Chore", "Spike"])},
}

TWELVE = {**SEVEN, **FIVE}

#: The words of each tool's vocabulary kind on the line.
KIND_WORDS = {
    **dict.fromkeys(("update_tag", "delete_tag", "merge_tags", "create_tag", "create_tags"),
                    "tags"),
    **dict.fromkeys(("update_field", "delete_field", "create_field"), "custom fields"),
    **dict.fromkeys(("update_type", "delete_type", "create_type", "create_types"),
                    "task types"),
}

#: Each tool's signature at HRD-9a. The slice adds no argument (row item 6).
SIGNATURES = {
    "update_tag": "(project_id: str, tag: str, name: str = '', color: str = '', "
                  "description: str = '') -> str",
    "update_field": "(project_id: str, field: str, name: str = '', description: str = '', "
                    "options: str = '', required: str = '', field_type: str = '') -> str",
    "update_type": "(project_id: str, type_name: str, name: str = '', icon: str = '', "
                   "color: str = '', make_default: bool = False, epic: str = '') -> str",
    "delete_tag": "(project_id: str, tag: str) -> str",
    "delete_field": "(project_id: str, field: str) -> str",
    "delete_type": "(project_id: str, type_name: str) -> str",
    "merge_tags": "(project_id: str, tag: str, into: str) -> str",
    "create_tag": "(project_id: str, name: str, color: str = '', description: str = '', "
                  "org_wide: bool = False) -> str",
    "create_field": "(project_id: str, name: str, field_type: str = 'text', "
                    "options: str = '', description: str = '', required: bool = False, "
                    "org_wide: bool = False) -> str",
    "create_type": "(project_id: str, name: str, icon: str = '', color: str = '', "
                   "is_default: bool = False, is_epic: bool = False, "
                   "org_wide: bool = False) -> str",
    "create_tags": "(project_id: str, tags: str) -> str",
    "create_types": "(project_id: str, types: str) -> str",
}


@pytest.fixture(autouse=True)
def _an_open_run() -> Any:
    """Each tool runs inside a run that may send, as in production."""
    from acb_skills.write_artifact import artifact_context_scope, bind_artifact_context

    with artifact_context_scope():
        bind_artifact_context(agent_name="projects-assistant", no_egress=False)
        yield


def _reach(n: int, *, name: str | None = SPACE) -> dict[str, Any]:
    return {"root_id": OTHER, "root_name": name, "root_kind": "space", "projects": n}


def _gateway(
    reach: Any = None,
    *,
    fail: bool = False,
    hidden_parent: bool = False,
    impact: int | None = None,
):
    """The writes fence's gateway, with ``Website`` under the space ``Marketing``.

    ``reach`` answers the reach read. ``fail`` answers it with a 500.
    ``hidden_parent`` answers the read of the space with a 404, as the route
    answers a member who cannot see it. ``impact`` sets the tasks of the
    impact read.
    """

    def answer(call: dict) -> Any:
        path, method = call["path"], call["method"]
        if path == REACH:
            return FakeResponse({"detail": "boom"}, 500) if fail else reach
        if path == f"/projects/nodes/{UUID}" and method == "GET":
            return {"id": UUID, "name": NODE, "parent_project_id": OTHER}
        if path == f"/projects/nodes/{OTHER}" and method == "GET":
            if hidden_parent:
                return FakeResponse({"detail": "Project not found"}, 404)
            return {"id": OTHER, "name": SPACE, "parent_project_id": None}
        if impact is not None and path.endswith("/impact"):
            return {"tag": "urgent", "scope": "project", "tasks": impact, "projects": 1}
        return tw.responder(call)

    return answer


async def _run(tool: str, monkeypatch, responder: Any) -> tuple[dict[str, Any], str, list]:
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, responder)
    out = await getattr(skill_projects, tool)(**TWELVE[tool])
    assert len(asked) == 1, (tool, out)
    return asked[0], out, calls


def _line(tool: str, space: str, n: int) -> str:
    return f"Changes the {KIND_WORDS[tool]} of {space} for {n} projects"


# ── 1 and 2. The space and N ────────────────────────────────────────────────


@pytest.mark.parametrize("tool", sorted(TWELVE))
async def test_each_tool_shows_n_for_two_or_more_projects(tool: str, monkeypatch) -> None:
    card, _out, calls = await _run(tool, monkeypatch, _gateway(_reach(5)))
    assert card["detail"].endswith(f". {_line(tool, '«Marketing»', 5)}"), card["detail"]
    assert REACH in [c["path"] for c in calls]
    # The write still happens, after the card.
    assert writes(calls), tool


@pytest.mark.parametrize("tool", sorted(SEVEN))
async def test_the_seven_tools_name_the_space_not_the_node(tool: str, monkeypatch) -> None:
    card, _out, _calls = await _run(tool, monkeypatch, _gateway(_reach(5)))
    head = card["detail"].split(". Changes the ", 1)[0]
    assert "«Marketing»" in head, card["detail"]
    # Not in the detail, and not in the body either.
    assert NODE not in json.dumps(card, ensure_ascii=False), card


@pytest.mark.parametrize("tool", sorted(TWELVE))
async def test_n_of_one_shows_no_line(tool: str, monkeypatch) -> None:
    """Q3: with N of 1, as in PST-4, the card has no line."""
    card, _out, _calls = await _run(tool, monkeypatch, _gateway(_reach(1)))
    assert "Changes the" not in card["detail"], card["detail"]
    assert "«Marketing»" in card["detail"], card["detail"]


@pytest.mark.parametrize("tool", sorted(TWELVE))
async def test_a_hidden_space_has_no_name(tool: str, monkeypatch) -> None:
    """Q5: the member cannot see the root, so no card names it."""
    card, out, _calls = await _run(tool, monkeypatch, _gateway(_reach(4, name=None)))
    assert card["detail"].endswith(f". {_line(tool, 'the space above it', 4)}"), card["detail"]
    assert SPACE not in json.dumps(card) and SPACE not in out


# ── 4. A failed read never stops the tool ───────────────────────────────────


@pytest.mark.parametrize("tool", sorted(TWELVE))
async def test_a_failed_read_shows_the_card_with_no_n(tool: str, monkeypatch) -> None:
    """The space comes from the walk up the tree, with no N, and the write
    happens."""
    card, _out, calls = await _run(tool, monkeypatch, _gateway(fail=True))
    assert "Changes the" not in card["detail"], card["detail"]
    assert "«Marketing»" in card["detail"], card["detail"]
    assert writes(calls), tool


@pytest.mark.parametrize("tool", sorted(TWELVE))
async def test_an_answer_of_another_shape_is_a_failed_read(tool: str, monkeypatch) -> None:
    card, _out, calls = await _run(tool, monkeypatch, _gateway({"rows": []}))
    assert "Changes the" not in card["detail"] and writes(calls), card["detail"]


@pytest.mark.parametrize("tool", sorted(TWELVE))
async def test_a_failed_read_and_a_hidden_space_still_show_the_card(
    tool: str, monkeypatch
) -> None:
    card, _out, calls = await _run(tool, monkeypatch, _gateway(fail=True, hidden_parent=True))
    assert "Changes the" not in card["detail"], card["detail"]
    assert SPACE not in json.dumps(card)
    assert writes(calls), tool


# ── 5. An org-wide row keeps its card of today ──────────────────────────────


def _org_wide_rows(call: dict) -> Any:
    path, method = call["path"], call["method"]
    if method == "GET" and path.endswith("/tags"):
        return {"rows": [{"id": tw.TAG_ID, "name": "urgent", "task_count": 4,
                          "project_id": None}]}
    if method == "GET" and path.endswith("/fields"):
        return {"rows": [{"id": tw.FIELD_ID, "name": "Region", "field_key": "region",
                          "project_id": None}]}
    if method == "GET" and path.endswith("/types"):
        return {"rows": [{"id": tw.TYPE_ID, "name": "Bug", "project_id": None}]}
    return _gateway(_reach(5))(call)


@pytest.mark.parametrize(
    ("tool", "kwargs", "detail"),
    [
        ("update_tag", {"tag": "urgent", "name": "q4"},
         "«urgent» in organization-wide · organization-wide"),
        ("update_tag", {"tag": "urgent", "color": "red"}, "«urgent» in organization-wide"),
        ("update_field", {"field": "region", "name": "Territory"},
         "«Region» (key «region») in organization-wide"),
        ("update_type", {"type_name": "bug", "name": "Defect"}, "«Bug» in organization-wide"),
        ("create_tag", {"name": "q4", "org_wide": True},
         "«q4» in every project (organization-wide)"),
        ("create_field", {"name": "Region2", "org_wide": True},
         "«Region2» (text) in every project (organization-wide)"),
        ("create_type", {"name": "Chore", "org_wide": True},
         "«Chore» in every project (organization-wide)"),
    ],
)
async def test_an_org_wide_row_keeps_its_card_of_today(
    tool: str, kwargs: dict[str, Any], detail: str, monkeypatch
) -> None:
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _org_wide_rows)
    await getattr(skill_projects, tool)(project_id=UUID, **kwargs)
    assert asked[0]["detail"] == detail
    assert REACH not in [c["path"] for c in calls]


# ── 6. No tool gains an argument ────────────────────────────────────────────


def _signature(tool: str) -> str:
    """The signature of *tool* as text. The module has postponed annotations,
    so each annotation is the text of its source."""
    sig = inspect.signature(getattr(skill_projects, tool))
    params = [
        f"{p.name}: {p.annotation}" + ("" if p.default is p.empty else f" = {p.default!r}")
        for p in sig.parameters.values()
    ]
    return f"({', '.join(params)}) -> {sig.return_annotation}"


def test_no_tool_gains_an_argument() -> None:
    assert set(SIGNATURES) == set(TWELVE)
    for tool, recorded in SIGNATURES.items():
        assert _signature(tool) == recorded, tool


# ── 7. The create receipts do not change ────────────────────────────────────


@pytest.mark.parametrize("tool", sorted(FIVE))
async def test_the_create_receipt_does_not_change(tool: str, monkeypatch) -> None:
    """The receipt with N equals the receipt with no read of the reach."""
    _card, with_n, _calls = await _run(tool, monkeypatch, _gateway(_reach(5)))
    _card, without, _calls = await _run(tool, monkeypatch, _gateway(fail=True))
    assert with_n == without
    assert "Changes the" not in with_n
    assert "«Marketing», the root of «Website», and its whole tree" in with_n


# ── 8. The rename count reads the impact ────────────────────────────────────


async def test_the_rename_count_comes_from_the_impact_read(monkeypatch) -> None:
    """The list says 4 open tasks, and the rewrite changes 9 with the archived
    ones. The card says 9."""
    card, _out, calls = await _run("update_tag", monkeypatch, _gateway(_reach(5), impact=9))
    assert f"/projects/tags/{tw.TAG_ID}/impact" in [c["path"] for c in calls]
    assert " · renames 9 tasks. Changes the tags of «Marketing» for 5 projects" in card["detail"]
    assert card["context"].split("\n")[1] == "tasks renamed: 9"


async def test_a_failed_impact_read_gives_the_list_count(monkeypatch) -> None:
    def no_impact(call: dict) -> Any:
        if call["path"].endswith("/impact"):
            return FakeResponse({"detail": "Project not found"}, 404)
        return _gateway(_reach(5))(call)

    card, _out, calls = await _run("update_tag", monkeypatch, no_impact)
    assert "renames 4 tasks" in card["detail"] and writes(calls)


async def test_a_change_with_no_rename_reads_no_impact(monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _gateway(_reach(5)))
    await skill_projects.update_tag(UUID, "urgent", color="red")
    assert not [c for c in calls if c["path"].endswith("/impact")]
    assert asked[0]["detail"] == "«urgent» in «Marketing». Changes the tags of «Marketing» for 5 projects"


# ── The read and its line ───────────────────────────────────────────────────


def test_the_reach_read_is_a_class_a_route_of_the_manifest() -> None:
    row = m.route_for("GET", REACH)
    assert row is not None and row.cls == "A" and row.tool == "vocabulary"


def test_the_line_names_each_kind_in_the_words_of_a_person() -> None:
    space = W._Space(OTHER, "Ops", 3)
    assert W.shared_vocab_line("tags", space) == "Changes the tags of «Ops» for 3 projects"
    assert W.shared_vocab_line("fields", space) == (
        "Changes the custom fields of «Ops» for 3 projects")
    assert W.shared_vocab_line("types", space) == (
        "Changes the task types of «Ops» for 3 projects")
    assert W.shared_vocab_line("tags", W._Space(OTHER, "Ops", 1)) == ""
    assert W.shared_vocab_line("tags", W._Space(OTHER, "Ops", None)) == ""
    assert W.shared_vocab_line("tags", W._Space(OTHER, None, 2)) == (
        "Changes the tags of the space above it for 2 projects")


@pytest.mark.parametrize(
    "body",
    [None, {}, {"rows": []}, {"root_id": OTHER}, {"root_id": OTHER, "projects": "5"},
     {"root_id": OTHER, "projects": True}, {"root_id": OTHER, "projects": -1},
     {"root_id": "", "projects": 3}],
)
def test_a_reach_answer_of_another_shape_gives_no_n(body: Any) -> None:
    assert W._reach_count(body) is None


def test_the_count_sql_does_not_stop_at_a_status_owner() -> None:
    """A status set stops at a node that owns its own set. A vocabulary row
    does not, so the walk has no ``owns_statuses`` test, and the helper does
    not read ``_STATUS_USERS_SQL``. The R8 half proves the count."""
    pytest.importorskip("gateway.routes.projects", reason="gateway not installed")
    from gateway.routes.projects import core

    assert "owns_statuses" not in core._TREE_PROJECTS_SQL
    assert ":max_depth" in core._TREE_PROJECTS_SQL
    assert "organization_id = t.organization_id" in core._TREE_PROJECTS_SQL
    source = inspect.getsource(core.vocabulary_reach)
    assert "_STATUS_USERS_SQL" not in source and "status_users" not in source
    assert "load_visible_project" in source and "root_project_id" in source
