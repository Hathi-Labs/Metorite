"""WFC-4a-1: the workflow editor offers the human step, and no node that cannot run.

Spec: ``project-docs/specs/ai_studio.md`` §14, row WFC-4a-1. Code:
``routes/workflows/catalog.py`` (``NODE_TYPE_META``, ``UNOFFERED_NODE_TYPES``),
``routes/workflows/search.py`` (``collect_catalog_entries``),
``routes/workflows/copilot.py`` (``COPILOT_SYSTEM_PROMPT``) and the form rules of
``workbench/control_plane/src/app/workflows/lib/humanStep.ts``.

The editor is not a security boundary. Publish and the run doors keep their
rules, and this file changes none of them. It holds the OFFER: what the palette
search and the Copilot shortlist show, and what the Copilot prompt asks for.

**Mutations (each was run and turned this file red):**

* M1. Drop ``"pm_task"`` from ``catalog.UNOFFERED_NODE_TYPES``: the search
  and shortlist cases turn red.
* M2. Drop the ``human_step`` line from ``COPILOT_SYSTEM_PROMPT``: the prompt
  cases turn red.

WFC-4a-2a adds section 5: the pair that the Studio write form writes. The
TypeScript rules (``studioWrite.ts``) are read as text, as ``_form_keys`` does
for the human step. The pair must pass ``validate_graph``.

* M3. Add ``record_id`` to ``STUDIO_WRITE_KEYS`` of ``studioWrite.ts``: the key
  case turns red.
* M4. Drop ``"studio_write"`` from ``catalog.UNOFFERED_NODE_TYPES``: the offer
  cases turn red.
"""
from __future__ import annotations

import asyncio
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from gateway.routes.workflows import catalog as catalog_mod
from gateway.routes.workflows import copilot as copilot_mod
from gateway.routes.workflows import search as search_mod
from gateway.routes.workflows import service
from gateway.routes.workflows import tools as tools_mod
from gateway.routes.workflows.engine.graph import (
    HUMAN_STEP_KEYS,
    NODE_TYPES,
    STUDIO_WRITE_KEYS,
    validate_graph,
)

_ROOT = Path(__file__).resolve().parents[2]
_FORM_RULES = (
    _ROOT / "workbench" / "control_plane" / "src" / "app" / "workflows" / "lib" / "humanStep.ts"
)

_STUDIO_WRITE_RULES = (
    _ROOT / "workbench" / "control_plane" / "src" / "app" / "workflows" / "lib" / "studioWrite.ts"
)

_PERSON = "ravi@acme.test"


def _meta(node_type: str) -> dict[str, str]:
    found = [m for m in catalog_mod.NODE_TYPE_META if m["type"] == node_type]
    assert len(found) == 1, node_type
    return found[0]


# -- 1. The catalog data ---------------------------------------------------------


def test_the_meta_holds_the_human_step_in_plain_words() -> None:
    meta = _meta("human_step")
    assert meta["category"] == "logic"
    assert meta["label"] == "Human step"
    assert "task" in meta["description"] and "{{" not in meta["description"]


def test_each_task_type_says_that_it_cannot_run() -> None:
    for node_type in ("pm_task", "pm_lifecycle"):
        words = _meta(node_type)["description"]
        assert "cannot run yet" in words, node_type
        assert "no approval node required" not in words, node_type


def test_the_offer_drops_the_types_that_cannot_run_and_keeps_the_data() -> None:
    """The set names each member-blind write type and studio_write, and the
    engine still knows each of them, so a stored graph still draws."""
    assert service._MEMBER_BLIND_WRITE_TYPES <= catalog_mod.UNOFFERED_NODE_TYPES
    assert "studio_write" in catalog_mod.UNOFFERED_NODE_TYPES
    assert "human_step" not in catalog_mod.UNOFFERED_NODE_TYPES
    assert catalog_mod.UNOFFERED_NODE_TYPES <= NODE_TYPES
    for node_type in ("pm_task", "pm_lifecycle"):
        assert _meta(node_type), "the data stays in NODE_TYPE_META"


# -- 2. The palette search and the Copilot shortlist ------------------------------


@pytest.fixture
def bare_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    """The node entries only: no agent, integration, tool or module."""

    async def _no_modules() -> list[Any]:
        return []

    monkeypatch.setattr(catalog_mod, "_agent_entries", lambda: [])
    monkeypatch.setattr(catalog_mod, "_integration_entries", lambda: [])
    monkeypatch.setattr(catalog_mod, "known_agent_names", lambda: set())
    monkeypatch.setattr(tools_mod, "list_tools", lambda: [])
    monkeypatch.setattr(search_mod, "_module_entries", _no_modules)


def test_the_search_entries_offer_the_human_step_and_no_unoffered_type(bare_catalog) -> None:
    keys = {e.key for e in search_mod.collect_catalog_entries() if e.kind == "node"}
    assert "human_step" in keys
    assert not keys & catalog_mod.UNOFFERED_NODE_TYPES, keys
    for node_type in ("pm_task", "pm_lifecycle", "studio_write"):
        assert node_type not in keys, node_type


def test_a_task_search_finds_the_human_step_and_no_task_write(bare_catalog) -> None:
    """The word task is in the label of pm_task, so without the filter it ranks first."""
    out = asyncio.run(search_mod.search_catalog("task"))
    keys = [r["key"] for r in out["results"]]
    assert "human_step" in keys
    assert "pm_task" not in keys and "pm_lifecycle" not in keys


class _NoModulesResult:
    def fetchall(self) -> list[Any]:
        return []


class _NoModulesDb:
    async def execute(self, clause: Any, params: dict | None = None) -> _NoModulesResult:
        assert "workflow_modules" in str(clause)
        return _NoModulesResult()


def test_the_copilot_shortlist_reads_the_same_offer(bare_catalog) -> None:
    context = asyncio.run(copilot_mod._capability_context("update the task", _NoModulesDb()))
    keys = {r["key"] for r in context["shortlist"]}
    assert "human_step" in keys
    for node_type in ("pm_task", "pm_lifecycle", "studio_write"):
        assert node_type not in keys, keys


# -- 3. The Copilot prompt ---------------------------------------------------------


def _bullet(starts: str) -> str:
    """The bullet of the prompt that starts with *starts*, with its lines."""
    text = copilot_mod.COPILOT_SYSTEM_PROMPT
    start = text.index(starts)
    end = text.find("\n- ", start + 1)
    return text[start : end if end != -1 else len(text)]


def test_the_prompt_lists_the_human_step_type() -> None:
    types = _bullet("- Node:")
    assert re.search(r"\bhuman_step\b", types), types


def test_the_prompt_names_the_three_literal_keys_and_no_ref() -> None:
    line = _bullet("- human_step:")
    for key in sorted(HUMAN_STEP_KEYS):
        assert f'"{key}"' in line, key
    assert "never put a {{ref}}" in line
    assert "never invent" in line


def test_the_prompt_forbids_each_member_blind_write_type() -> None:
    line = _bullet("- Never use the node types")
    for node_type in sorted(service._MEMBER_BLIND_WRITE_TYPES):
        assert re.search(rf"\b{node_type}\b", line), node_type


# -- 4. The graph that the form writes ---------------------------------------------


def _node(nid: str, ntype: str, config: dict[str, Any]) -> dict[str, Any]:
    return {"id": nid, "type": ntype, "position": {"x": 0, "y": 0},
            "data": {"label": nid, "config": config}}


def _graph(step: dict[str, Any]) -> dict[str, Any]:
    return {
        "nodes": [
            _node("start", "trigger", {"kind": "manual"}),
            _node("step", "human_step", step),
            _node("done", "output", {"value": "end"}),
        ],
        "edges": [
            {"id": "e1", "source": "start", "target": "step"},
            {"id": "e2", "source": "step", "target": "done"},
        ],
    }


def _form_keys() -> set[str]:
    """``HUMAN_STEP_KEYS`` of the form rules, read from the TypeScript."""
    text = _FORM_RULES.read_text(encoding="utf-8")
    found = re.search(r"export const HUMAN_STEP_KEYS = \[([^\]]*)\] as const;", text)
    assert found, "humanStep.ts lost HUMAN_STEP_KEYS"
    return set(re.findall(r'"([a-z_]+)"', found.group(1)))


def test_the_form_keys_equal_the_engine_keys() -> None:
    """The form writes no key that publish refuses, and misses none."""
    assert _form_keys() == set(HUMAN_STEP_KEYS)


@pytest.mark.parametrize(
    "step",
    [
        {"assignee": _PERSON, "title": "Check the invoice", "description": ""},
        {"assignee": _PERSON, "title": "Check the invoice",
         "description": "Open the invoice, check the sum, and mark the task done."},
    ],
)
def test_a_graph_as_the_form_writes_it_has_no_issue(step: dict[str, Any]) -> None:
    assert set(step) <= _form_keys()
    assert validate_graph(_graph(step)) == []


@pytest.mark.parametrize("key", sorted(HUMAN_STEP_KEYS))
def test_a_ref_in_any_field_fails(key: str) -> None:
    step = {"assignee": _PERSON, "title": "Check the invoice", "description": "Read it"}
    step[key] = "{{trigger.who}}" if key == "assignee" else "Check {{trigger.id}}"
    issues = validate_graph(_graph(step))
    assert any(i.node_id == "step" for i in issues), issues


def test_the_copilot_round_accepts_a_human_step_graph(monkeypatch, bare_catalog) -> None:
    @asynccontextmanager
    async def _tenant_session():
        yield _NoModulesDb()

    monkeypatch.setattr(copilot_mod, "_tenant_session", _tenant_session)
    step = {"assignee": _PERSON, "title": "Check the invoice", "description": ""}
    result = {"reply": "Added a human step.", "graph": _graph(step), "new_modules": [],
              "_model": "stub"}
    out, created = asyncio.run(
        copilot_mod._apply_round(result, "wf-1", "ask Ravi to check it", "maker@acme.test"),
    )
    assert out["_problems"] == [] and out["_issues"] == []
    assert created == {}


# -- 5. The pair that the Studio write form writes (WFC-4a-2a) --------------------

_PACKAGE = "11111111-1111-4111-8111-111111111111"


def _studio_form_keys() -> set[str]:
    """``STUDIO_WRITE_KEYS`` of the form rules, read from the TypeScript."""
    text = _STUDIO_WRITE_RULES.read_text(encoding="utf-8")
    found = re.search(r"export const STUDIO_WRITE_KEYS = \[([^\]]*)\] as const;", text)
    assert found, "studioWrite.ts lost STUDIO_WRITE_KEYS"
    return set(re.findall(r'"([a-z_]+)"', found.group(1)))


def _pair_graph(write: dict[str, Any], *, second_write: bool = False,
                gate: bool = True) -> dict[str, Any]:
    """The graph that the palette drops (approval, then write), joined by ONE edge."""
    nodes = [
        _node("start", "trigger", {"kind": "schedule"}),
        _node("gate", "approval", {"message": "Add this record to the app?"}),
        _node("write", "studio_write", write),
        _node("done", "output", {"value": "end"}),
    ]
    edges = [
        {"id": "e1", "source": "start", "target": "gate" if gate else "write"},
        {"id": "e2", "source": "gate", "target": "write"},
        {"id": "e3", "source": "write", "target": "done"},
    ]
    if second_write:
        nodes.append(_node("write2", "studio_write", write))
        edges.append({"id": "e4", "source": "gate", "target": "write2"})
    if not gate:
        edges = [e for e in edges if e["id"] != "e2"]
    return {"nodes": nodes, "edges": edges}


_WRITE = {"package_id": _PACKAGE, "action": "add_ticket", "data": {"title": "Fix the door"}}


def test_the_studio_form_keys_are_the_engine_keys_without_the_record_id() -> None:
    """A create has no record id, and the form writes no key that publish refuses."""
    assert _studio_form_keys() == {"package_id", "action", "data"}
    assert _studio_form_keys() == set(STUDIO_WRITE_KEYS) - {"record_id"}


def test_a_studio_write_stays_unoffered_in_search_and_has_no_node_type_meta() -> None:
    assert "studio_write" in catalog_mod.UNOFFERED_NODE_TYPES
    assert "studio_write" not in {m["type"] for m in catalog_mod.NODE_TYPE_META}


def test_the_pair_as_the_form_writes_it_has_no_issue() -> None:
    assert set(_WRITE) <= _studio_form_keys()
    assert validate_graph(_pair_graph(_WRITE)) == []


def test_the_dropped_blank_pair_fails_only_on_its_blank_config() -> None:
    """Before the owner fills the form, publish names the package and the action."""
    blank = {"package_id": "", "action": "", "data": {}}
    issues = validate_graph(_pair_graph(blank))
    assert {i.code for i in issues} == {"missing_config"}, issues
    assert {i.node_id for i in issues} == {"write"}


def test_a_ref_in_the_data_fails() -> None:
    write = {**_WRITE, "data": {"title": "Fix {{trigger.door}}"}}
    issues = validate_graph(_pair_graph(write))
    assert any(i.code == "templated_config" and i.node_id == "write" for i in issues), issues


def test_a_write_with_no_approval_above_it_fails() -> None:
    issues = validate_graph(_pair_graph(_WRITE, gate=False))
    assert any(i.code == "studio_write_without_approval" for i in issues), issues


def test_a_second_write_under_one_approval_fails() -> None:
    issues = validate_graph(_pair_graph(_WRITE, second_write=True))
    assert any(i.code == "approval_clears_one_write" for i in issues), issues
