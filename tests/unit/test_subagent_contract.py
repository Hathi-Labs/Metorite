"""The sub-agent contract of OA-9: the jobs in, and the items out (WS-67).

``acb_skills.subagent_contract`` (``project-docs/specs/one_assistant_context.md``
§6.2, §6.4 and §6.5, slice OA-9a, "GO for the parser only" in §4.5). The
module is dark: nothing calls it until OA-9b builds ``spawn_subagents``.

``tests/unit/test_tool_packs_off.py`` held no OA-9a case to move here (§4.5,
OA-9a item 1). Its cases are all OA-7c. The worker cases (the refusal with
no job, and the worker out of the picker) wait for the worker half, which is
NO-GO.

Mutations this file catches (R7). Each case names its own in its docstring.

* M1 ``_items_of`` accepts a kind outside ``quoted`` and ``inferred``.
* M2 ``unstructured`` marks its item ``quoted``.
* M3 ``_items_of`` ignores a key that is not in the contract, so text of the
  child is lost.
* M4 ``parse_items`` drops one bad item and keeps the others.
* M5 ``parse_jobs`` runs a job that names a pack that the parent run does
  not hold, or any pack when no record of the packs can be read (OA-9c).
* M6 ``parse_jobs`` lets a child hold a tool that its parent lacks.
* M7 ``parse_jobs`` lets a child hold a delegation tool or the spawn tool.
* M8 ``annotated_read_only`` reads an unknown tool as read-only.
* M9 ``parse_jobs`` runs a sixth job, or drops it with no name.
* M10 ``MAX_JOBS`` drifts from the cap of the one fan-out.
* M11 a module of the platform imports the contract before OA-9b.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from acb_skills import subagent_contract as sc

ROOT = Path(__file__).resolve().parents[2]

# ── The items out (§6.4) ────────────────────────────────────────────────────


def _answer(*rows: dict) -> str:
    return json.dumps({"items": list(rows)})


def test_good_items_keep_their_order_kind_and_source() -> None:
    """The parser accepts good output (OA-9a acceptance). Catches M4 in its
    reorder form, and a parser that drops ``source``."""
    text = _answer(
        {"value": "PO 4471 asks for 12 units", "kind": "quoted", "source": "email:abc123"},
        {"value": "The buyer wants it by Friday", "kind": "inferred", "source": "email:abc123"},
        {"value": "Part list, page 2: CB6", "kind": "quoted", "source": "parts.pdf p.2"},
    )
    assert sc.parse_items(text) == [
        sc.Item("PO 4471 asks for 12 units", "quoted", "email:abc123"),
        sc.Item("The buyer wants it by Friday", "inferred", "email:abc123"),
        sc.Item("Part list, page 2: CB6", "quoted", "parts.pdf p.2"),
    ]


def test_a_kind_in_other_case_is_read_and_folded() -> None:
    """``Quoted`` reads as ``quoted``, as ``system_one._match`` folds a choice."""
    items = sc.parse_items(_answer({"value": "12", "kind": " Quoted ", "source": "crm:42"}))
    assert items == [sc.Item("12", "quoted", "crm:42")]


def test_an_empty_list_is_a_child_that_found_nothing() -> None:
    assert sc.parse_items('{"items": []}') == []


def test_a_value_that_is_not_text_becomes_compact_json() -> None:
    items = sc.parse_items(_answer(
        {"value": 12, "kind": "quoted", "source": "crm:42"},
        {"value": {"part": "CB6", "qty": 2}, "kind": "inferred", "source": "tool:read_page"},
    ))
    assert [i.value for i in items] == ["12", '{"part":"CB6","qty":2}']


@pytest.mark.parametrize("text", [
    "The order asks for 12 units of CB6.",
    "",
    "   ",
    '```json\n{"items": [{"value": "x", "kind": "quoted", "source": "s"}]}\n```',
    '[{"value": "x", "kind": "quoted", "source": "s"}]',
    '"just a string"',
    "null",
])
def test_output_in_another_shape_becomes_one_inferred_item(text: str) -> None:
    """Bad output becomes ONE ``inferred`` item with the source
    ``unstructured`` and the whole text (OA-9a acceptance). A code fence is
    another shape: the parse is strict. Catches M2."""
    assert sc.parse_items(text) == [sc.Item(text.strip(), "inferred", sc.UNSTRUCTURED)]


def test_a_deep_nest_never_raises() -> None:
    """``json.loads`` raises ``RecursionError`` on a deep nest. The parser
    reads it as another shape, for the items and for the jobs."""
    text = "[" * 100_000
    assert sc.parse_items(text) == [sc.Item(text, "inferred", sc.UNSTRUCTURED)]
    assert _plan(text).error.startswith("Error: jobs must be")


@pytest.mark.parametrize("payload", [
    {"items": [{"value": "x", "kind": "certain", "source": "s"}]},
    {"items": [{"value": "x", "kind": None, "source": "s"}]},
    {"items": [{"value": "x", "source": "s"}]},
    {"items": [{"value": "x", "kind": "quoted", "source": ""}]},
    {"items": [{"value": "x", "kind": "quoted", "source": 7}]},
    {"items": [{"value": None, "kind": "quoted", "source": "s"}]},
    {"items": [{"value": "  ", "kind": "quoted", "source": "s"}]},
    {"items": [{"value": [], "kind": "quoted", "source": "s"}]},
    {"items": ["x"]},
    {"items": "x"},
    {"answers": []},
])
def test_an_object_off_the_contract_becomes_one_inferred_item(payload: dict) -> None:
    """A kind outside the two, a blank source or an empty value is another
    shape. Catches M1 (the first two rows)."""
    text = json.dumps(payload)
    assert sc.parse_items(text) == [sc.Item(text, "inferred", sc.UNSTRUCTURED)]


@pytest.mark.parametrize("payload", [
    {"items": [{"value": "x", "kind": "quoted", "source": "s"}], "note": "I found no PO"},
    {"items": [{"value": "x", "kind": "quoted", "source": "s", "page": 3}]},
    # A repeated key: json.loads keeps the LAST value, so a fact would vanish.
    '{"items": [{"value": "a", "kind": "quoted", "source": "s"}], '
    '"items": [{"value": "b", "kind": "inferred", "source": "s"}]}',
    '{"items": [{"value": "a", "kind": "quoted", "kind": "inferred", "source": "s"}]}',
])
def test_a_key_outside_the_contract_keeps_all_the_text(payload: dict | str) -> None:
    """An extra key may hold text of the child. The parser never drops it in
    silence: the whole answer becomes one guess. Catches M3."""
    text = payload if isinstance(payload, str) else json.dumps(payload)
    items = sc.parse_items(text)
    assert items == [sc.Item(text, "inferred", sc.UNSTRUCTURED)]
    assert all(i.kind != "quoted" for i in items)
    assert items[0].value == text


def test_one_bad_item_turns_the_whole_answer_into_one_guess() -> None:
    """A good ``quoted`` item never stands beside text that the parse lost.
    Catches M4."""
    text = _answer(
        {"value": "12 units", "kind": "quoted", "source": "email:abc"},
        {"value": "CB6", "kind": "certain", "source": "parts.pdf"},
    )
    items = sc.parse_items(text)
    assert items == [sc.Item(text, "inferred", sc.UNSTRUCTURED)]
    assert all(i.kind != "quoted" for i in items)


def test_the_fallback_item_is_never_quoted() -> None:
    """Catches M2: whatever the text, the fallback is a guess."""
    for text in ("quoted", '{"kind": "quoted"}', "x"):
        (item,) = sc.parse_items(text)
        assert item.kind == "inferred"
        assert item.source == sc.UNSTRUCTURED


def test_no_answer_at_all_is_one_empty_guess() -> None:
    assert sc.parse_items(None) == [sc.Item("", "inferred", sc.UNSTRUCTURED)]


def test_bytes_decode_and_another_type_is_never_stored_as_a_repr() -> None:
    """F4. Catches ``str(raw)`` coming back in ``_as_text``."""
    good = _answer({"value": "x", "kind": "quoted", "source": "s"})
    assert sc.parse_items(good.encode()) == [sc.Item("x", "quoted", "s")]
    for odd in (7, {"a": 1}, object()):
        assert sc.parse_items(odd) == [sc.Item("", "inferred", sc.UNSTRUCTURED)]  # type: ignore[arg-type]


# ── The jobs in (§6.2, §6.5) ────────────────────────────────────────────────

PARENT = frozenset({"web_search", "fetch_page", "read_page", "list_messages",
                    "create_task", "call_agent", "spawn_subagents"})
READS = frozenset({"web_search", "fetch_page", "read_page", "list_messages"})


def _plan(jobs: object, *, parent: frozenset[str] = PARENT,
          held: frozenset[str] | None = None) -> sc.JobPlan:
    raw = jobs if isinstance(jobs, str) else json.dumps(jobs)
    return sc.parse_jobs(raw, parent_tools=parent, read_only=READS.__contains__,
                         held_packs=held)


def test_good_jobs_run_in_order_with_the_default_tier() -> None:
    plan = _plan([
        {"job": "Find the Navy PO and list its parts.", "tools": ["list_messages"]},
        {"job": "Search the web for the CB6 datasheet.",
         "tools": ["web_search", "fetch_page", "web_search"], "tier": "fast"},
        {"job": "Summarise the three notes in this job.", "packs": [], "tier": " MAIN "},
    ])
    assert plan.error == ""
    assert plan.refused == ()
    assert plan.jobs == (
        sc.Job(1, "Find the Navy PO and list its parts.", ("list_messages",), "main"),
        sc.Job(2, "Search the web for the CB6 datasheet.", ("web_search", "fetch_page"), "fast"),
        sc.Job(3, "Summarise the three notes in this job.", (), "main"),
    )


def test_a_parsed_list_is_read_as_the_text_is() -> None:
    jobs = [{"job": "Read the page.", "tools": ["read_page"]}]
    assert sc.parse_jobs(jobs, parent_tools=PARENT, read_only=READS.__contains__) == _plan(jobs)


@pytest.mark.parametrize("raw", ["not json", "[]", '{"jobs": []}', '"a job"', "null"])
def test_a_call_that_is_not_an_array_of_jobs_runs_nothing(raw: str) -> None:
    plan = _plan(raw)
    assert plan.jobs == ()
    assert plan.error.startswith("Error: jobs must be a JSON array of 1 to 5 jobs.")
    assert plan.notes() == plan.error


def test_five_jobs_run_and_a_sixth_gets_its_name_and_does_not_run() -> None:
    """OA-9b acceptance, the parser half: at most 5 run, the sixth is named.
    Catches M9."""
    jobs = [{"job": f"Read page {n}.", "tools": ["read_page"]} for n in range(1, 8)]
    plan = _plan(jobs)
    assert [j.number for j in plan.jobs] == [1, 2, 3, 4, 5]
    assert plan.refused == (
        'At most 5 jobs run in one call. These did not run: job 6 ("Read page 6."), '
        'job 7 ("Read page 7."). Call again with them.',
    )


@pytest.mark.parametrize("packs", [0, False, {}, "", "email", ["email"], {"email": 1}])
def test_any_packs_value_but_none_or_an_empty_list_is_refused(packs: object) -> None:
    """F3: a falsy value that is no list must not pass as no pack. Catches
    the truthiness test coming back."""
    plan = _plan([{"job": "Read.", "packs": packs}])
    assert plan.jobs == ()
    assert "tool pack" in plan.refused[0]


@pytest.mark.parametrize("packs", [None, []])
def test_no_pack_and_an_empty_pack_list_pass(packs: object) -> None:
    assert len(_plan([{"job": "Read.", "packs": packs}]).jobs) == 1


def test_overflow_names_ten_jobs_and_counts_the_rest() -> None:
    """F2: a call with many jobs cannot make a long answer. Catches the
    unbounded list."""
    jobs = [{"job": f"Read page {n}.", "tools": ["read_page"]} for n in range(1, 30)]
    (line,) = _plan(jobs).refused
    assert line.count('job ') == 10
    assert 'job 15 ("Read page 15.")' in line and "job 16" not in line
    assert ", and 14 more." in line


@pytest.mark.parametrize("tool", ["ask_questions", "ask_user", "request_confirmation",
                                  "share_artifact"])
def test_a_child_cannot_ask_the_member_or_show_a_card(tool: str) -> None:
    """Q4. These say read_only=True, so the read-only rule lets them pass.
    Catches MEMBER_TOOLS emptied."""
    plan = sc.parse_jobs(json.dumps([{"job": "Ask.", "tools": [tool]}]),
                         parent_tools={tool}, read_only=lambda _n: True)
    assert plan.jobs == ()
    assert "a sub-agent cannot ask the member or show a card" in plan.refused[0]


def test_remember_stays_a_read_for_a_child() -> None:
    assert sc.annotated_read_only("remember")
    plan = sc.parse_jobs(json.dumps([{"job": "Recall.", "tools": ["remember"]}]),
                         parent_tools={"remember"})
    assert len(plan.jobs) == 1


def test_a_delegating_tool_gets_the_hand_off_reason() -> None:
    """Q5. A specialist tool is no member of ``DELEGATION_TOOLS``. Catches
    ``delegating`` ignored."""
    raw = json.dumps([{"job": "Mail.", "tools": ["email_assistant"]}])
    plain = sc.parse_jobs(raw, parent_tools={"email_assistant"}, read_only=lambda _n: False)
    assert "not a read-only tool" in plain.refused[0]
    plan = sc.parse_jobs(raw, parent_tools={"email_assistant"}, read_only=lambda _n: False,
                         delegating={"email_assistant"})
    assert "cannot start another agent" in plan.refused[0]


#: The packs that the parent run holds in the OA-9c cases (``PackRun.holds()``).
HELD = frozenset({"email", "crm-records"})


def test_a_job_runs_with_a_pack_that_the_parent_holds() -> None:
    """OA-9c replaces ``test_a_job_that_names_a_pack_does_not_run_until_oa_9c``.
    A pack that the parent holds passes, once each, in order and in lower case."""
    plan = _plan([
        {"job": "Find the Navy PO.", "packs": ["Email", "crm-records", "email"]},
        {"job": "Read the deal and the page.", "packs": ["crm-records"], "tools": ["read_page"]},
        {"job": "Search the web.", "tools": ["web_search"], "packs": []},
    ], held=HELD)
    assert plan.refused == ()
    assert [(j.number, j.packs, j.tools) for j in plan.jobs] == [
        (1, ("email", "crm-records"), ()),
        (2, ("crm-records",), ("read_page",)),
        (3, (), ("web_search",)),
    ]


def test_a_pack_that_the_parent_lacks_stops_the_job() -> None:
    """SUB-1 rule 1: a child never holds a pack that its parent lacks. Catches
    M5: ``parse_jobs`` runs a job with a pack that the parent does not hold."""
    plan = _plan([
        {"job": "Plan the sprint.", "packs": ["crm-records", "projects-plan"]},
        {"job": "Search the web.", "tools": ["web_search"]},
    ], held=HELD)
    assert [j.number for j in plan.jobs] == [2]
    assert plan.refused == (
        'Job 1 ("Plan the sprint.") did not run: this chat does not hold the tool '
        "pack 'projects-plan', and a sub-agent gets only the packs that this chat holds.",
    )


def test_with_no_record_of_the_packs_a_job_that_names_one_does_not_run() -> None:
    """Fail closed (OA-9c): ``held_packs=None`` means that no record could be
    read, so no pack is held. A job with no pack still runs. Catches a
    ``None`` that reads as "every pack"."""
    plan = _plan([
        {"job": "Find the Navy PO.", "packs": ["email"]},
        {"job": "Read the page.", "packs": "email", "tools": ["read_page"]},
        {"job": "Search the web.", "tools": ["web_search"]},
    ])
    assert [j.number for j in plan.jobs] == [3]
    assert plan.refused == (
        'Job 1 ("Find the Navy PO.") did not run: this chat holds no tool pack now, '
        "so a sub-agent cannot get one. Name its tools instead.",
        'Job 2 ("Read the page.") did not run: its packs must be a list of tool pack names.',
    )
    empty = _plan([{"job": "Find the Navy PO.", "packs": ["email"]}], held=frozenset())
    assert empty.jobs == () and "does not hold the tool pack 'email'" in empty.refused[0]


def test_a_job_with_no_pack_is_as_in_oa_9b() -> None:
    """No ``packs`` key: the job is the OA-9b job, with ``packs`` empty."""
    for held in (None, HELD):
        plan = _plan([{"job": "Read the page.", "tools": ["read_page"]}], held=held)
        assert plan.jobs == (sc.Job(1, "Read the page.", ("read_page",), "main"),)
        assert plan.jobs[0].packs == () and plan.jobs[0].pack_tools == ()
        assert plan.jobs[0].pack_run == ""


# ── OA-9c: the tools that a child may hold from a pack ──────────────────────


def test_a_child_may_hold_a_read_tool_of_a_pack() -> None:
    assert sc.child_may_hold("get_record", read_only=lambda _n: True)
    assert sc.child_may_hold("remember")


@pytest.mark.parametrize("tool", sorted({*sc.MEMBER_TOOLS, *sc.DISPLAY_TOOLS,
                                         *sc.EXTRA_CALL_TOOLS, "call_agent",
                                         "spawn_subagents", "delegate_to_agent"}))
def test_a_child_never_holds_a_pack_tool_that_sub_1_blocks(tool: str) -> None:
    """Even a lying read-only answer cannot pass a member tool, a tool that
    draws a card or moves the page, a tool with model calls of its own, or a
    delegation tool."""
    assert not sc.child_may_hold(tool, read_only=lambda _n: True)


def test_a_pack_tool_with_no_read_only_mark_stays_out() -> None:
    """The one marker: a tool with no registry entry, or ``read_only`` not
    ``True``, is not read-only. Catches the filter reading another marker."""
    assert not sc.child_may_hold("a_pack_tool_that_no_module_registered")
    assert not sc.child_may_hold("write_artifact")
    assert not sc.child_may_hold("")


def test_child_allowed_is_the_job_tools_and_the_pack_tool_names() -> None:
    async def get_record() -> str:
        return ""

    job = sc.Job(1, "Read.", ("web_search",), "fast", packs=("crm-records",),
                 pack_tools=(get_record,), pack_run="k-1")
    assert sc.child_allowed(job) == {"web_search", "get_record"}
    assert sc.child_allowed(sc.Job(1, "Read.", ("remember",), "fast")) == {"remember"}
    assert sc.child_allowed(None) == frozenset()


def test_the_pack_run_key_goes_to_the_worker_only() -> None:
    """OA-9c: the executor binds the key of a child's pack tools for the worker
    only, and only for a job that holds pack tools."""
    from acb_skills.write_artifact import artifact_context_scope, bind_artifact_context

    async def get_record() -> str:
        return ""

    job = sc.Job(1, "Read.", (), "fast", packs=("crm-records",),
                 pack_tools=(get_record,), pack_run="k-child")
    bare = sc.Job(1, "Read.", (), "fast", pack_run="k-stray")
    assert sc.job_pack_run("worker", {sc.JOB_KEY: job}) == "k-child"
    assert sc.job_pack_run("crm-assistant", {sc.JOB_KEY: job}) == ""
    assert sc.job_pack_run("worker", {sc.JOB_KEY: bare}) == ""
    assert sc.job_pack_run("worker", {sc.JOB_KEY: "not a job"}) == ""
    assert sc.job_pack_run("worker", {}) == ""
    with artifact_context_scope():
        bind_artifact_context(run_id="r")
        assert sc.job_pack_run("worker") == ""
        sc.bind_job(job)
        assert sc.job_pack_run("worker") == "k-child"


def test_a_job_refusal_has_the_shape_of_the_parser() -> None:
    job = sc.Job(2, "Read the deal.", (), "main")
    assert sc.job_refusal(job, "why.") == 'Job 2 ("Read the deal.") did not run: why.'


def test_the_key_and_the_tools_of_a_job_stay_out_of_its_repr() -> None:
    """The run key is server state. A log line of a job never shows it."""
    job = sc.Job(1, "Read.", (), "fast", pack_run="k-secret-key")
    assert "k-secret-key" not in repr(job)


def test_a_child_gets_no_tool_that_its_parent_lacks() -> None:
    """§6.2, no more tools than the parent. Catches M6."""
    plan = _plan([{"job": "Read the CRM.", "tools": ["read_page", "crm_search"]}])
    assert plan.jobs == ()
    assert plan.refused == (
        'Job 1 ("Read the CRM.") did not run: this chat has no tool \'crm_search\', '
        "and a sub-agent gets only tools of this chat.",
    )


@pytest.mark.parametrize("tool", ["spawn_subagents", "call_agent", "call_agents_parallel",
                                  "call_agent_background", "delegate_to_agent"])
def test_a_child_never_holds_a_spawn_or_a_delegation_tool(tool: str) -> None:
    """§6.2: no spawn tool and no delegation tool, even when the parent holds
    it and the read-only rule would let it pass. Catches M7."""
    parent = PARENT | {tool}
    plan = sc.parse_jobs(json.dumps([{"job": "Ask the CRM agent.", "tools": [tool]}]),
                         parent_tools=parent, read_only=lambda _name: True)
    assert plan.jobs == ()
    assert plan.refused == (
        f'Job 1 ("Ask the CRM agent.") did not run: a sub-agent cannot start another '
        f"agent, so it cannot hold '{tool}'.",
    )


def test_a_child_reads_first_and_a_write_waits_for_the_parent() -> None:
    """§6.5, read first and writes after. Catches the read-only check lost."""
    plan = _plan([{"job": "Make the follow-up task.", "tools": ["read_page", "create_task"]}])
    assert plan.jobs == ()
    assert plan.refused == (
        'Job 1 ("Make the follow-up task.") did not run: \'create_task\' is not a '
        "read-only tool, and a sub-agent may only read. Do that step yourself after "
        "the sub-agents answer.",
    )


def test_the_default_read_only_check_reads_the_risk_registry() -> None:
    """The web reads and a ``read_only=True`` tool pass. A write and an
    unknown tool do not. Catches M8: an unknown tool fails closed."""
    assert sc.annotated_read_only("web_search")
    assert sc.annotated_read_only("fetch_page")
    assert sc.annotated_read_only("read_attachment")
    assert not sc.annotated_read_only("write_artifact")
    assert not sc.annotated_read_only("emit_generative_ui")
    assert not sc.annotated_read_only("a_tool_that_no_module_registered")

    parent = {"web_search", "read_attachment", "write_artifact", "a_tool_that_no_module_registered"}
    plan = sc.parse_jobs(json.dumps([
        {"job": "Read the file.", "tools": ["read_attachment", "web_search"]},
        {"job": "Write the file.", "tools": ["write_artifact"]},
        {"job": "Use the new tool.", "tools": ["a_tool_that_no_module_registered"]},
    ]), parent_tools=parent)
    assert [j.number for j in plan.jobs] == [1]
    assert [line.split(" did not run")[0] for line in plan.refused] == [
        'Job 2 ("Write the file.")', 'Job 3 ("Use the new tool.")']


@pytest.mark.parametrize(("job", "why"), [
    ("a job", "it is not a JSON object."),
    ({"agent": "crm", "message": "find the PO"},
     "it has the key agent, message. A job has only the keys job, packs, tools and tier."),
    ({"job": "   "}, "it has no job text."),
    ({"job": ["read"]}, "it has no job text."),
    ({"job": "Read.", "tier": "max"}, 'its tier must be "fast" or "main".'),
    ({"job": "Read.", "tier": 1}, 'its tier must be "fast" or "main".'),
    ({"job": "Read.", "tools": "read_page"}, "its tools must be a list of tool names."),
    ({"job": "Read.", "tools": ["read_page", ""]}, "its tools must be a list of tool names."),
])
def test_a_bad_job_gets_one_line_and_the_others_still_run(job: object, why: str) -> None:
    """A refused job never stops the others, and its line says why."""
    plan = _plan([job, {"job": "Search the web.", "tools": ["web_search"]}])
    assert [j.number for j in plan.jobs] == [2]
    (line,) = plan.refused
    assert line.endswith(f"did not run: {why}")
    assert line.startswith("Job 1")


def test_a_long_job_is_clipped_in_its_refusal_line() -> None:
    text = "Find " + "the Navy order " * 20
    (line,) = _plan([{"job": text, "packs": ["email"]}]).refused
    label = line.split(" did not run")[0]
    assert label.startswith('Job 1 ("Find the Navy order')
    assert label.endswith('...")')
    assert len(label) < 80


# ── The caps and the seam ───────────────────────────────────────────────────


def test_the_caps_are_the_caps_of_section_6_2() -> None:
    assert sc.MAX_JOBS == 5
    assert sc.MAX_SPAWNS_PER_TURN == 2
    assert sc.MAX_CHILD_ROUNDS == 4
    assert sc.JOB_TIMEOUT_S == 120.0
    assert sc.FANOUT_TIMEOUT_S == 180.0
    assert sc.TIERS == ("fast", "main") and sc.DEFAULT_TIER == "main"


def test_the_job_cap_is_the_cap_of_the_one_fan_out() -> None:
    """§6.1: ``spawn_subagents`` and ``call_agents_parallel`` share one
    ``_fan_out``, so they share one cap. Catches M10."""
    from acb_skills import agent_tools

    assert sc.MAX_JOBS == agent_tools._PARALLEL_MAX


def test_the_handoff_list_is_the_one_delegation_list() -> None:
    """A child holds none of ``egress.DELEGATION_TOOLS``. A fifth delegation
    tool added there is refused here with no edit."""
    from acb_skills.egress import DELEGATION_TOOLS

    assert sc._handoff_tools() == DELEGATION_TOOLS | {sc.SPAWN_TOOL}


#: The modules that import the contract (WS-67 OA-9b). The pin
#: ``test_nothing_calls_the_contract_before_oa_9b`` was built to fail when
#: OA-9b imports the module, and it is removed on purpose. A new caller must
#: join this list in the same change, so the owner of the contract sees it.
_CONTRACT_CALLERS = [
    "apps/agents/agent-worker/agents.py",
    "apps/services/orchestrator/orchestrator/_tool_injection.py",
    "apps/services/orchestrator/orchestrator/agents.py",
    "apps/services/orchestrator/orchestrator/executor.py",
    "packages/acb_skills/acb_skills/agent_tools.py",
    "packages/acb_skills/acb_skills/tier_policy.py",
]


def test_the_callers_of_the_contract_are_the_known_ones() -> None:
    """OA-9b replaces ``test_nothing_calls_the_contract_before_oa_9b``. Catches M11."""
    callers = []
    for base in ("apps", "packages"):
        for path in (ROOT / base).rglob("*.py"):
            if "node_modules" in path.parts or path.name == "subagent_contract.py":
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if re.search(r"from acb_skills(\.subagent_contract| import subagent_contract)", text):
                callers.append(path.relative_to(ROOT).as_posix())
    assert sorted(callers) == _CONTRACT_CALLERS
