"""WS-23 successor — the core-floor schema diet (contract lock + ratchet).

``specs/skills_scope_out.md`` §5 measured the non-toggleable core floor at
≈15.4k tokens of tool context, ≈10.0k of it the 19 core tools' JSON schemas.
The diet trims that half by removing what is genuinely redundant — worked
examples, prose that restates the JSON shape immediately above it, a duplicated
alias description, and a design-system block the docstring itself points at a
tool for. It must NOT change what the model can call.

This file is the guard on that distinction:

1. **The call contract is pinned.** Parameter names, JSON types and
   required-ness for EVERY statically injectable platform tool are locked to a
   literal below. A trim that drops a parameter, renames one, changes its type,
   or makes an optional argument required fails here — those changes are out of
   scope for a diet and need their own review.
2. **No tool may be left undescribed.** A schema whose description is empty
   would be a token saving bought with tool-selection accuracy.
3. **A ratchet, not a target.** Each core-floor tool has a per-tool token
   ceiling at its post-diet size plus headroom. Growing a docstring past it is
   allowed — but it must be a deliberate edit of this file, so the floor can
   never quietly re-bloat back to 10k.

Measured the same way the S1 catalog measures (``skill_families.
tool_json_schema`` + the ``assemble_run_context`` tokenizer), so these numbers
are the ones the Skills tab shows.
"""
from __future__ import annotations

import json

import pytest

import orchestrator._tool_injection as ti
from acb_skills import skill_families as sf

# ---------------------------------------------------------------------------
# 1. The pinned call contract (name → {param: json type}, required list)
# ---------------------------------------------------------------------------

CALL_CONTRACTS: dict[str, dict[str, object]] = {
    'ask_questions': {'params': {'questions': 'string'},
                      'required': ['questions']},
    'call_agent': {'params': {'agent_name': 'string', 'message': 'string'},
                   'required': ['agent_name', 'message']},
    'call_agent_background': {'params': {'agent_name': 'string',
                                         'message': 'string'},
                              'required': ['agent_name', 'message']},
    'call_agents_parallel': {'params': {'tasks': 'string'},
                             'required': ['tasks']},
    'code_task': {'params': {'task': 'string'}, 'required': ['task']},
    'emit_generative_ui': {'params': {'ui': 'string'}, 'required': ['ui']},
    'fetch_page': {'params': {'max_chars': 'integer', 'url': 'string'},
                   'required': ['url']},
    'get_errors': {'params': {'filePaths': 'string'}, 'required': []},
    'get_workflow_run': {'params': {'run_id': 'string'},
                         'required': ['run_id']},
    'github_repo_search': {'params': {'query': 'string', 'repo': 'string'},
                           'required': ['repo']},
    'github_search': {'params': {'maxResults': 'integer',
                                 'query': 'string',
                                 'scope': 'string'},
                      'required': ['query']},
    'install_dependency': {'params': {'packages': 'string'},
                           'required': ['packages']},
    'list_integrations': {'params': {}, 'required': []},
    'list_workflows': {'params': {}, 'required': []},
    'load_artifact_kit': {'params': {'components': 'string'},
                          'required': []},
    'load_design_system': {'params': {'section': 'string'}, 'required': []},
    'manage_todo_list': {'params': {'todoList': 'string'},
                         'required': ['todoList']},
    # MT-0c-1 (D16): query_history no longer takes SQL. The contract change is
    # deliberate — a model-composed `query` string was a raw-SQL tool, which
    # saas_multitenancy.md §0.9.3 makes a condition on the pooled decision. All
    # criteria are optional: "recall recent conversation" is a valid call.
    'query_history': {'params': {'agent_name': 'string', 'limit': 'integer',
                                 'search': 'string', 'since_days': 'integer',
                                 'thread_id': 'string'},
                      'required': []},
    'recall_agent': {'params': {'query': 'string'}, 'required': ['query']},
    'recall_notes': {'params': {'path': 'string', 'query': 'string'},
                     'required': ['path']},
    'recall_org': {'params': {'query': 'string'}, 'required': ['query']},
    'recall_timeline': {'params': {'entity_name': 'string',
                                   'query': 'string'},
                        'required': ['entity_name', 'query']},
    'remember': {'params': {'query': 'string'}, 'required': ['query']},
    'run_diagnostics': {'params': {'filePaths': 'string'}, 'required': []},
    'run_script': {'params': {'args': 'string', 'path': 'string'},
                   'required': ['path']},
    'run_workflow': {'params': {'payload_json': 'string',
                                'wait_seconds': 'integer',
                                'workflow': 'string'},
                     'required': ['workflow']},
    'save_agent_memory': {'params': {'fact': 'string'},
                          'required': ['fact']},
    'save_episode': {'params': {'content': 'string',
                                'name': 'string',
                                'source': 'string'},
                     'required': ['content', 'name']},
    'save_memory': {'params': {'fact': 'string'}, 'required': ['fact']},
    'save_note': {'params': {'fact': 'string', 'path': 'string'},
                  'required': ['fact', 'path']},
    'save_org_memory': {'params': {'fact': 'string'}, 'required': ['fact']},
    'share_artifact': {'params': {'path': 'string'}, 'required': ['path']},
    'web_search': {'params': {'max_results': 'integer', 'query': 'string'},
                   'required': ['query']},
    'write_artifact': {'params': {'content': 'string',
                                  'encoding': 'string',
                                  'overwrite': 'boolean',
                                  'path': 'string'},
                       'required': ['content', 'path']},
}

# ---------------------------------------------------------------------------
# 3. Per-tool token ceilings for the 19-tool core floor (post-diet + headroom)
# ---------------------------------------------------------------------------
# Post-diet measurement 2026-08-01 (chars/4 run-context tokenizer), rounded up
# with ~10% headroom so ordinary wording edits don't fail CI. The floor summed
# 9,998 tokens before the diet and 8,510 after; this table is the ratchet that
# keeps it there.

CORE_SCHEMA_CEILINGS: dict[str, int] = {
    "ask_questions": 540,
    "call_agent": 360,
    "call_agent_background": 250,
    "call_agents_parallel": 310,
    "code_task": 420,
    # 3050 after the diet. WS-27bm S4 (2026-09-23) added five templates to the
    # catalog (timeline, taskBoard, dataGrid, reportCard, planCard), and the
    # docstring mirrors the catalog by rule (generative_ui_2.md §3). Measured
    # 3163 with the bullets trimmed to one shape line each.
    "emit_generative_ui": 3200,
    "fetch_page": 270,
    "get_errors": 170,
    "list_integrations": 190,
    "load_artifact_kit": 430,
    "load_design_system": 390,
    "manage_todo_list": 610,
    "recall_notes": 215,
    "run_diagnostics": 240,
    "run_script": 270,
    "save_note": 290,
    "share_artifact": 370,
    "web_search": 280,
    "write_artifact": 600,
}

#: The whole floor. Nudging one tool up must be paid for elsewhere.
CORE_SCHEMA_TOTAL_CEILING = 9_000


def _tools_by_name() -> dict[str, object]:
    from gateway.routes.integrations_skills import _platform_tools_by_name
    return _platform_tools_by_name()


def _count(text: str) -> int:
    from acb_llm.context import count_message_tokens
    return count_message_tokens([{"role": "system", "content": text}])


def _schema_tokens(fn: object) -> int:
    return _count(json.dumps(sf.tool_json_schema(fn)))


@pytest.fixture(scope="module")
def tools() -> dict[str, object]:
    out = _tools_by_name()
    assert out, "no platform tools importable — the collection chain is broken"
    return out


# ── 1. Call contracts unchanged ────────────────────────────────────────────

def test_every_injectable_tool_has_a_pinned_contract(tools) -> None:
    assert set(tools) == set(CALL_CONTRACTS), (
        "the injectable tool set changed — add/remove the entry in "
        "CALL_CONTRACTS deliberately (adding a tool is not a diet)"
    )


def test_parameter_names_types_and_requiredness_are_unchanged(tools) -> None:
    for name, fn in sorted(tools.items()):
        params = sf.tool_json_schema(fn)["function"]["parameters"]
        got = {k: v["type"] for k, v in params["properties"].items()}
        expected = CALL_CONTRACTS[name]
        assert got == expected["params"], (
            f"{name}: parameter names/types changed — a docstring diet must "
            f"never alter the call contract"
        )
        assert sorted(params["required"]) == expected["required"], (
            f"{name}: required-ness changed"
        )


# ── 2. Nothing left undescribed ────────────────────────────────────────────

def test_no_tool_lost_its_description(tools) -> None:
    for name, fn in sorted(tools.items()):
        desc = sf.tool_json_schema(fn)["function"]["description"]
        assert desc.strip(), f"{name}: empty description — that is not a diet"
        assert len(desc) > 60, (
            f"{name}: description trimmed to {len(desc)} chars — too thin to "
            f"select on"
        )


def test_the_first_line_does_not_merely_restate_the_tool_name(tools) -> None:
    """A description whose whole content is the name spelled out in words
    carries no information the schema does not already have."""
    for name, fn in sorted(tools.items()):
        first = (
            sf.tool_json_schema(fn)["function"]["description"]
            .strip().splitlines()[0]
        )
        assert first.lower().replace(" ", "_") != name.lower(), name


# ── 3. The ratchet ─────────────────────────────────────────────────────────

def test_core_floor_matches_the_ceiling_table(tools) -> None:
    assert set(CORE_SCHEMA_CEILINGS) == set(ti._CORE_STANDARD_TOOL_NAMES), (
        "the core floor changed — update CORE_SCHEMA_CEILINGS with the new "
        "tool's measured schema cost"
    )


def test_no_core_tool_exceeds_its_schema_ceiling(tools) -> None:
    over: list[str] = []
    for name, ceiling in sorted(CORE_SCHEMA_CEILINGS.items()):
        fn = tools.get(name)
        if fn is None:  # pragma: no cover — collection chain covered above
            continue
        cost = _schema_tokens(fn)
        if cost > ceiling:
            over.append(f"{name}: {cost} > {ceiling}")
    assert not over, (
        "tool schemas grew past their post-diet ceilings: "
        + "; ".join(over)
        + " — either trim again or raise the ceiling deliberately"
    )


def test_core_floor_schema_total_stays_under_the_ratchet(tools) -> None:
    total = sum(
        _schema_tokens(tools[n])
        for n in sorted(ti._CORE_STANDARD_TOOL_NAMES) if n in tools
    )
    assert total <= CORE_SCHEMA_TOTAL_CEILING, (
        f"core-floor schemas total {total} tokens (ratchet "
        f"{CORE_SCHEMA_TOTAL_CEILING}) — the WS-23 diet is being undone"
    )
