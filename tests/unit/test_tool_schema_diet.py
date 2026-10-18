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

import orchestrator._tool_injection as ti
import pytest
from acb_skills import skill_families as sf

from tests.unit._decide_flag import (
    decide_tool_on,  # noqa: F401 — module-scoped autouse, the flag ON
)

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
    # WS-53 CRM-C7: the CRM read tools, one `crm` family in DEFAULT_PROFILE.
    # Each reads through the gateway as the member. None takes a member field.
    # 2026-10-14 (test_read_tool_paging.py): `page` on crm_aggregate,
    # crm_deal_contacts, crm_get_module_record, crm_list_fields,
    # crm_list_modules and crm_report. Each cut its list at 50 with no way
    # to read the rest. A new optional argument, so no call breaks.
    # WS-53 CRM-Q25 (crm_platform.md §18.11, done-when 7): the typed aggregate.
    # `field` names the field of a sum/avg/min/max, `group_field` and
    # `bucket` group by one field, and `field_filters` filters a module.
    # `group_by` is no longer required, because a call gives group_by OR
    # group_field. Every argument that existed keeps its name and type.
    'crm_aggregate': {'params': {'bucket': 'string',
                                 'close_from': 'string', 'close_to': 'string',
                                 'created_from': 'string', 'created_to': 'string',
                                 'custom': 'object', 'entity': 'string',
                                 'field': 'string', 'field_filters': 'object',
                                 'group_by': 'string', 'group_field': 'string',
                                 'measure': 'string',
                                 'owner': 'string', 'page': 'integer',
                                 'pipeline_id': 'string',
                                 'q': 'string', 'status': 'string',
                                 'updated_from': 'string', 'updated_to': 'string'},
                      'required': ['entity']},
    'crm_deal_contacts': {'params': {'deal_id': 'string', 'page': 'integer'},
                          'required': ['deal_id']},
    'crm_get_module_record': {'params': {'module': 'string', 'page': 'integer',
                                         'record_id': 'string'},
                              'required': ['module', 'record_id']},
    'crm_get_record': {'params': {'entity': 'string', 'record_id': 'string'},
                       'required': ['entity', 'record_id']},
    'crm_list_fields': {'params': {'entity': 'string', 'page': 'integer'},
                        'required': ['entity']},
    'crm_list_module_records': {'params': {'module': 'string', 'page': 'integer',
                                           'page_size': 'integer', 'q': 'string'},
                                'required': ['module']},
    'crm_list_modules': {'params': {'page': 'integer'}, 'required': []},
    'crm_list_owners': {'params': {'page': 'integer'}, 'required': []},
    'crm_list_pipelines': {'params': {}, 'required': []},
    'crm_list_records': {'params': {'close_from': 'string', 'close_to': 'string',
                                    'created_from': 'string', 'created_to': 'string',
                                    'custom': 'object', 'entity': 'string',
                                    'owner': 'string', 'page': 'integer',
                                    'page_size': 'integer', 'pipeline_id': 'string',
                                    'q': 'string', 'status': 'string',
                                    'updated_from': 'string', 'updated_to': 'string'},
                         'required': ['entity']},
    # projects_ai_chat.md §14.10.9 (B3): make_report makes a kept report by code.
    'crm_report': {'params': {'kind': 'string', 'make_report': 'boolean', 'page': 'integer'},
                   'required': ['kind']},
    # WS-53 CRM-Q27 (crm_platform.md §18.11.2, done-when 15 and 16): search of
    # note text by meaning. A new tool, so no call breaks.
    'crm_search_notes': {'params': {'limit': 'integer', 'query': 'string'},
                         'required': ['query']},
    'crm_stage_dwell': {'params': {'days': 'integer', 'page': 'integer'}, 'required': []},
    # WS-53 CRM-Q32-8 (crm_platform.md §18.14.10): the reply of a CRM question
    # card. It loads with the `crm` family only, with org_facts, so the core
    # floor and its total do not move. `choice` is the KEY of an option. No
    # member or org argument: the gateway binds them and decides (R5).
    'crm_answer_question': {'params': {'choice': 'string', 'question_id': 'string'},
                            'required': ['choice', 'question_id']},
    # WS-31 CP-13d (D75). One question per call. The schema has NO member
    # field on purpose: the member never comes from a tool argument.
    'decide': {'params': {'context': 'string', 'kind': 'string',
                          'options': 'string', 'question': 'string'},
               'required': ['context', 'question']},
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
    # WS-53 CRM-Q32-5 (crm_platform.md §18.14.4): the org-facts read. It
    # loads with the `crm` family only, so the core floor and its total do
    # not move. No member or org argument: the gateway binds them (R5).
    'org_facts': {'params': {'app': 'string', 'offset': 'integer', 'query': 'string'},
                  'required': []},
    # 2026-10-15, agent reads page part 2 (test_read_tool_paging.py): an
    # optional `offset` on list_workflows, query_history and recall_notes.
    # Each read every row into the context, with no page. A new optional
    # argument, so no call breaks.
    'list_workflows': {'params': {'offset': 'integer'}, 'required': []},
    'load_artifact_kit': {'params': {'components': 'string'},
                          'required': []},
    'load_design_system': {'params': {'section': 'string'}, 'required': []},
    'manage_todo_list': {'params': {'todoList': 'string'},
                         'required': ['todoList']},
    # 2026-10-11: one zip of this chat's files. The paths are a JSON list in
    # a string, like get_errors. No workspace, member or thread argument: the
    # run binds them (R5).
    'package_artifacts': {'params': {'name': 'string', 'paths': 'string'},
                          'required': []},
    # H-315 (2026-10-15): a deck or a report as a PDF, from one spec object.
    # No member, org or path argument: the run binds them (R5).
    'make_document': {'params': {'spec': 'object'}, 'required': ['spec']},
    # projects_ai_chat.md §14.10 (2026-10-16): a report as a chat card and a
    # PDF, from one spec object. The same shape as make_document. A family
    # tool, so the core floor and its total do not move.
    'make_report': {'params': {'spec': 'object'}, 'required': ['spec']},
    # MT-0c-1 (D16): query_history no longer takes SQL. The contract change is
    # deliberate — a model-composed `query` string was a raw-SQL tool, which
    # saas_multitenancy.md §0.9.3 makes a condition on the pooled decision. All
    # criteria are optional: "recall recent conversation" is a valid call.
    'query_history': {'params': {'agent_name': 'string', 'limit': 'integer',
                                 'offset': 'integer',
                                 'search': 'string', 'since_days': 'integer',
                                 'thread_id': 'string'},
                      'required': []},
    # H-229 (projects_ai_chat.md §22). A file name and an offset only: the
    # workspace and the thread come from the run's context, never the model.
    'read_attachment': {'params': {'name': 'string', 'offset': 'integer'},
                        'required': ['name']},
    'recall_agent': {'params': {'query': 'string'}, 'required': ['query']},
    'recall_notes': {'params': {'offset': 'integer', 'path': 'string',
                                'query': 'string'},
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
# 3. Per-tool token ceilings for the core floor (19 tools at the diet, 22 with decide, read_attachment and package_artifacts)
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
    # WS-31 CP-13d (2026-09-24). Measured 181. The floor went 8744 -> 8925,
    # so the total ceiling did not move.
    "decide": 200,
    # 3050 after the diet. WS-27bm S4 (2026-09-23) added five templates to the
    # catalog (timeline, taskBoard, dataGrid, reportCard, planCard), and the
    # docstring mirrors the catalog by rule (generative_ui_2.md §3). Measured
    # 3163 with the bullets trimmed to one shape line each.
    # 2026-10-09: the shapes and the two code-mode guides left the docstring.
    # The tool returns them on demand (write_artifact.genui_guide), and the
    # docstring keeps the names. Measured 641.
    "emit_generative_ui": 700,
    "fetch_page": 270,
    "get_errors": 170,
    "list_integrations": 190,
    "load_artifact_kit": 430,
    "load_design_system": 390,
    "manage_todo_list": 610,
    # 2026-10-11: zip this chat's files. Measured 139. share_artifact paid
    # for it (332 -> 201), so the floor measured 6692, under the total.
    "package_artifacts": 160,
    # 2026-10-09 (#780): every agent reads a chat attachment, so the tool
    # joined the floor. Measured 277. The floor then measured 6534, under
    # the total ceiling.
    "read_attachment": 300,
    "recall_notes": 215,
    "run_diagnostics": 240,
    "run_script": 270,
    "save_note": 290,
    # 2026-10-16 (owner report: a pasted link opened raw Markdown). Both
    # tools now say "name the file, and paste no link". share_artifact
    # measured 196 (was 201) and write_artifact 533 (was 575).
    "share_artifact": 210,
    "web_search": 280,
    "write_artifact": 550,
}

#: The whole floor. Nudging one tool up must be paid for elsewhere.
#: 9,000 until 2026-10-09. The floor then measured 6,438 with `decide`, after
#: the catalog of emit_generative_ui went on demand.
#: 2026-10-15, agent reads page part 2: recall_notes gained an `offset`
#: (193 -> 215, +22), and paid for it itself. Its worked example left the
#: docstring, so it measured 180, and the floor 6,693 -> 6,680.
CORE_SCHEMA_TOTAL_CEILING = 6_700


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


# ── 4. The System-1 engine of `decide` (WS-45 S1, D90) ─────────────────────
#
# `ai_tier_routing.md` §6.3. An agent that AI_TIER_ROUTING covers holds the
# System-1 engine in the SAME floor slot, so its schema pays from the same
# total. It widens the Jev contract by `items`, and breaks nothing.

#: The System-1 `decide` contract: the Jev contract, plus `items`.
SYSTEM_ONE_DECIDE_CONTRACT: dict[str, object] = {
    "params": {**CALL_CONTRACTS["decide"]["params"], "items": "string"},
    "required": CALL_CONTRACTS["decide"]["required"],
}

#: Measured 181 on 2026-10-06, the same as the Jev engine. The floor with the
#: Jev engine measured 8997 of 9000 that day, so the System-1 description was
#: cut to fit, and its ceiling is the Jev ceiling.
SYSTEM_ONE_DECIDE_CEILING = 200


def test_the_system_one_decide_widens_the_contract_and_breaks_nothing() -> None:
    from acb_skills.decide_tools import system_one_decide

    fn = sf.tool_json_schema(system_one_decide)["function"]
    assert fn["name"] == "decide"
    got = {k: v["type"] for k, v in fn["parameters"]["properties"].items()}
    assert got == SYSTEM_ONE_DECIDE_CONTRACT["params"]
    assert sorted(fn["parameters"]["required"]) == SYSTEM_ONE_DECIDE_CONTRACT["required"]
    assert len(fn["description"]) > 60


def test_the_system_one_decide_stays_under_its_ceiling_and_the_floor_total(tools) -> None:
    from acb_skills.decide_tools import system_one_decide

    cost = _schema_tokens(system_one_decide)
    assert cost <= SYSTEM_ONE_DECIDE_CEILING, (
        f"system-one decide: {cost} > {SYSTEM_ONE_DECIDE_CEILING}"
    )
    total = sum(
        _schema_tokens(system_one_decide if n == "decide" else tools[n])
        for n in sorted(ti._CORE_STANDARD_TOOL_NAMES) if n in tools
    )
    assert total <= CORE_SCHEMA_TOTAL_CEILING, (
        f"a covered agent's floor totals {total} tokens (ratchet "
        f"{CORE_SCHEMA_TOTAL_CEILING})"
    )
