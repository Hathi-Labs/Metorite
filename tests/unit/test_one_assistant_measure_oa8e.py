"""The fence of the OA-8e measure harness: the CRM and Projects chats (WS-67, R7).

``evals/one_assistant_measure`` measures the packs of the CRM assistant
(``crm-records``) and of the Projects assistant (four packs) against
delegation. Spec: ``project-docs/specs/one_assistant_context.md`` §4.3.3.
This file holds the harness, not the live run.

* Each known-good plan passes every check on BOTH arms, through the real
  orchestrator run, the real CRM and Projects tools and the real cards. Only
  the model wire and the gateway are fakes. Nothing leaves the box.
* ``pack_flag`` turns on EVERY pack of an owner. A dropped pack fails a test.
* Each new check has a case that passes and a case that fails, and each one
  names its mutation.
* The fan-out chats run on the ``pack`` and ``packs+spawn`` arms (phase 3).

Run::

    uv run python -m pytest tests/unit/test_one_assistant_measure_oa8e.py -q
"""
from __future__ import annotations

import json
import re
from typing import Any

import pytest

pytest.importorskip("agent_framework", reason="MAF not installed")

from acb_skills import tool_packs
from acb_skills.tier_policy import SHORT_MESSAGE_WORDS

from evals.one_assistant_measure import checks as ck
from evals.one_assistant_measure import pack, run
from evals.one_assistant_measure import workgateway as wg
from evals.one_assistant_measure.scenarios import Check, Plan, Turn, all_scenarios, scenario
from evals.one_assistant_measure.scenarios_work import (
    CRM_SCENARIOS,
    FANOUT_SCENARIOS,
    MIXED_SCENARIOS,
    PROJECTS_SCENARIOS,
    WORK_SCENARIOS,
)
from evals.routing.harness import ORCHESTRATOR, ReplyModel, text_turn, tool_turn

CRM, PROJECTS, EA = pack.CRM, pack.PROJECTS, pack.EA
CRM_WRITES = {"create_lead", "update_deal_status", "convert_lead", "log_activity"}


@pytest.fixture(scope="module")
def scripted() -> tuple[list[dict[str, Any]], list[str]]:
    return run.run_scripted(WORK_SCENARIOS)


def _turns(group: tuple) -> int:
    return sum(len(s.turns) for s in group)


# ── the set ──────────────────────────────────────────────────────────────────


def test_every_work_plan_passes_on_both_arms(scripted) -> None:
    """The scripted run plays the known-good plan on both arms through the real
    run. Mutation: make one answer in ``scenarios_work`` miss a fact → that turn
    fails its ``answer_all`` on both arms → red."""
    rows, problems = scripted
    assert problems == [], problems
    ran = [s for s in WORK_SCENARIOS if not s.skip]
    assert len(rows) == 2 * sum(len(s.turns) for s in ran)
    assert {r["arm"] for r in rows} == {"delegation", "pack", "packs+spawn"}


def test_the_set_has_the_turns_the_spec_asks_for() -> None:
    assert _turns(CRM_SCENARIOS) == 12
    assert 18 <= _turns(PROJECTS_SCENARIOS) <= 20
    assert _turns(MIXED_SCENARIOS) >= 3
    assert all(s.set == "crm" for s in CRM_SCENARIOS)
    assert all(s.set == "projects" for s in PROJECTS_SCENARIOS)
    assert all(s.set == "mixed" for s in MIXED_SCENARIOS)
    ids = [s.id for s in all_scenarios()]
    assert len(ids) == len(set(ids))


def test_every_turn_is_short_so_no_turn_kind_request_goes_out() -> None:
    """Run 3 of the email measure sent long turns. A message under
    ``SHORT_MESSAGE_WORDS`` words sends no turn-kind request, so the layers (a)
    and (b) are the ones that load the pack. Mutation: add a 12-word turn → red."""
    for sc in WORK_SCENARIOS:
        if sc.set == "fanout":
            continue  # a research job is long by nature
        for turn in sc.turns:
            words = len(turn.words.split())
            assert words < SHORT_MESSAGE_WORDS, (sc.id, words, turn.words)


def test_the_chats_sit_in_the_apps_where_a_member_would_be() -> None:
    surfaces = {s.surface for s in WORK_SCENARIOS if not s.skip}
    assert {"crm", "projects", "tasks", "calendar", "email", ""} <= surfaces
    for sc in WORK_SCENARIOS:
        assert sc.surface in tool_packs.surfaces() | {""}, sc.id


def test_every_pack_has_a_turn_that_needs_it() -> None:
    needed = {c.arg for sc in WORK_SCENARIOS for t in sc.turns for c in t.checks
              if c.kind == "right_pack"}
    assert needed == set(tool_packs.owner_packs(CRM)) | set(tool_packs.owner_packs(PROJECTS))
    kinds = {(s.mode, s.surface) for s in CRM_SCENARIOS}
    assert ("mirror", "crm") in kinds and ("native", "") in kinds


def test_each_chat_holds_at_most_one_write() -> None:
    """Spec: one write per chat, behind its card. A batch tool is one write."""
    for sc in WORK_SCENARIOS:
        writes = sum(1 for t in sc.turns for name, _ in t.plan.calls
                     if name in ck.work_tool_names() and ck.is_write_tool(name))
        assert writes <= (2 if sc.id == "mix_call_then_task" else 1), (sc.id, writes)


def test_every_check_kind_of_the_set_exists() -> None:
    for sc in WORK_SCENARIOS:
        for turn in sc.turns:
            for c in turn.checks:
                assert c.kind in ck.KINDS, (sc.id, c.kind)


def test_the_words_of_each_pack_are_the_ones_a_turn_expects() -> None:
    """Layer (a) by a word: these turns hold a word of the pack, and the
    others hold none. A change to ``_PROJECTS_WORDS`` shows here. OA-8g added
    "overloaded" and "add tasks", so ``pm_capacity`` and ``pm_new_tasks`` load
    by a word since run 3."""
    by_word = {"pm_stuck": "projects-plan", "pm_tags": "projects-structure",
               "pm_archive": "projects-bulk", "crm_open_deals": "crm-records",
               "pm_capacity": "projects-plan", "pm_new_tasks": "projects-bulk"}
    for sid, slug in by_word.items():
        words = [t.words for t in scenario(sid).turns]
        assert any(tool_packs.matched_layer(slug, w) == "words" for w in words), sid
    for sid, slug in {"pm_status": "projects-structure", "crm_miss": "crm-records",
                      "pm_miss": "projects"}.items():
        assert tool_packs.matched_layer(slug, scenario(sid).turns[0].words) is None, sid


# ── the fan-out chats wait for OA-9b ─────────────────────────────────────────


def test_the_fanout_chats_run_on_the_pack_and_the_spawn_arm() -> None:
    """Phase 3: five fan-out chats, no skip. A research chat spawns on the spawn
    arm and reads page by page on the pack arm. Delegation holds no spawn tool.
    Mutation: give one fan-out chat ``skip`` text → ``runnable`` drops it → red."""
    assert len(FANOUT_SCENARIOS) == 6 and all(s.set == "fanout" for s in FANOUT_SCENARIOS)
    assert not any(s.skip for s in WORK_SCENARIOS)
    assert run.runnable(tuple(FANOUT_SCENARIOS)) == tuple(FANOUT_SCENARIOS)
    assert run.arms_for(FANOUT_SCENARIOS[0], run.ALL_ARMS) == ("pack", run.SPAWN_ARM)
    assert run.arms_for(scenario("crm_pipeline"), run.ALL_ARMS) == ("delegation", "pack")
    assert run.arms_for(scenario("crm_pipeline"), (run.SPAWN_ARM,)) == ()


@pytest.mark.parametrize("sid", ["fan_contracts"])
def test_a_research_chat_spawns_and_its_children_parse(sid: str) -> None:
    """The spawn arm holds the tool, the model spawns, five children read fixture
    pages, and the answer of each parses (§6.4). The pack arm reads page by page.
    OA-9f: only the heavy chat (five long contracts) spawns.
    Mutation: ``WorkerModel`` answers plain text → ``items_contract`` red."""
    sc = scenario(sid)
    models = run.scripted_models(sc, run.SPAWN_ARM)
    row, follow = run.run_chat(sc, run.SPAWN_ARM, models=models)
    assert follow["passed"] and follow["spawned"] == 0, follow["checks"]
    assert row["passed"] and row["spawned"] == 1 and row["items_problems"] == [], row["checks"]
    assert row["checks"]["spawned"] is True and row["checks"]["items_contract"] is True
    assert "spawn_subagents" in _names(models[ORCHESTRATOR].bodies[0])
    assert row["web_fetches"] == 5 and row["web_unknown"] == []
    assert any(e["event"] == "measure.fanout" and e["ms"] >= 0 for e in row["pack_events"])
    models = run.scripted_models(sc, "pack")
    solo, _follow = run.run_chat(sc, "pack", models=models)
    assert solo["passed"] and solo["spawned"] == 0 and solo["checks"]["spawned"] is None
    assert "spawn_subagents" not in _names(models[ORCHESTRATOR].bodies[0])
    assert not any(e["event"] == "measure.fanout" for e in solo["pack_events"])


@pytest.mark.parametrize("sid", ["fan_companies", "fan_invoices", "fan_tasks", "fan_one_read",
                                 "fan_three_searches"])
def test_light_reads_one_task_and_three_searches_do_not_spawn(sid: str) -> None:
    """OA-9f: five company pages and ten invoice pages are light reads. The model
    makes them itself, in one step. Mutation: make the plan spawn → ``not_spawned`` red."""
    from acb_common import get_settings

    (row,) = run.run_chat(scenario(sid), run.SPAWN_ARM)
    assert row["passed"] and row["checks"]["not_spawned"] is True and row["spawned"] == 0
    assert get_settings().spawn_subagents_orgs == ""  # the flag lived in the chat only


def test_the_invoices_total_comes_from_the_fixtures() -> None:
    from evals.one_assistant_measure import fanout

    assert fanout.INVOICE_TOTAL == 233_400 and len(fanout.INVOICES) == 10
    (row,) = run.run_chat(scenario("fan_invoices"), "pack")
    assert row["checks"]["totals_the_invoices"] is True


def test_a_child_reaches_no_local_database_and_no_real_site(monkeypatch) -> None:
    """A child holds the read tools of its parent, and the parent holds
    ``retrieve_entity_context`` in some runs. A job that names it is refused
    here, and where a child could call it, ``no_local_database`` makes it read
    nothing. The web tools answer from fixtures, so nothing leaves the box. The
    sentinels on the data readers and on the real web code fail the test if one
    runs.
    Mutation: remove ``no_local_database`` or ``WebFixtures.install`` from
    ``run_chat`` → a sentinel raises → red."""
    from acb_skills import web_tools
    from orchestrator.agents import retrieve_entity_context

    from evals.one_assistant_measure import fanout

    def sentinel(*_a, **_k):
        raise AssertionError("a child reached the local database or the web")

    scope = retrieve_entity_context.__globals__
    monkeypatch.setitem(scope, "retrieve", sentinel)
    monkeypatch.setitem(scope, "_sales_context_fn", sentinel)
    monkeypatch.setattr(web_tools, "_serpapi_search", sentinel)
    sc = scenario("fan_companies")
    jobs = json.dumps([
        {"job": "Find Orbit in the company graph.", "tools": ["retrieve_entity_context"],
         "tier": "fast"},
        {"job": f"Read {fanout.company_url('Orbit Labs')}", "tools": ["fetch_page"], "tier": "fast"}])
    turn = Turn("Research Orbit across the company graph and the web, please",
                (Check("spawned"),), Plan((("spawn_subagents", {"jobs": jobs}),), "No match."))
    one = type(sc)("fan_local", "a child asks for the entity graph", (turn,), set="fanout")
    models = run.scripted_models(one, run.SPAWN_ARM)
    models["worker"] = fanout.WorkerModel(call_tool="retrieve_entity_context")
    (row,) = run.run_chat(one, run.SPAWN_ARM, models=models)
    result = json.dumps(models[ORCHESTRATOR].bodies[-1]["messages"][-1], ensure_ascii=False)
    # The orchestrator of the harness does not hold the tool, so no child gets it.
    assert "did not run: this chat has no tool 'retrieve_entity_context'" in result
    held = {n for body in models["worker"].bodies for n in _names(body)}
    assert "retrieve_entity_context" not in held and "fetch_page" in held
    assert row["error"] is None and row["sent"] == [] and row["web_fetches"] == 1
    # The real web tools are back after the chat, and the fixtures are gone.
    assert web_tools.web_search.__module__ == web_tools.__name__


def test_the_spawn_and_items_checks_pass_and_fail() -> None:
    """Mutations: ``_spawned`` ignores the arm → the None cases go red;
    ``items_contract_problems`` ignores ``unstructured`` → the bad block passes."""
    spawn = ck.SPAWN_ARM
    assert _one("spawned", None, _obs(arm=spawn, spawned=1)) is True
    assert _one("spawned", None, _obs(arm=spawn)) is False
    assert _one("spawned", None, _obs(arm="pack", spawned=1)) is None
    assert _one("not_spawned", None, _obs(arm=spawn)) is True
    assert _one("not_spawned", None, _obs(arm=spawn, spawned=1)) is False
    assert _one("not_spawned", None, _obs(arm="pack")) is None
    good = ("Job 1 (\"Read a\"):\n- [quoted] Orbit makes sensors (source: https://o.example/a)\n\n"
            "Job 2 (\"Read b\"):\n- [inferred] guess (source: inferred)")
    bad = "Job 1 (\"Read a\"):\n- [inferred] free text (source: unstructured)"
    err = "Job 1 (\"Read a\"): Error: no answer in 120s, so the job was stopped."
    assert ck.items_contract_problems([good]) == []
    assert ck.items_contract_problems([bad]) and ck.items_contract_problems([err])
    assert ck.items_contract_problems(["No job ran."]) == ["no job block"]
    assert _auto("items_contract", _obs(arm=spawn, spawn_results=[good])) is True
    assert _auto("items_contract", _obs(arm=spawn, spawn_results=[bad])) is False
    assert _auto("items_contract", _obs(arm=spawn)) is None
    assert _auto("items_contract", _obs(arm="pack", spawn_results=[good])) is None
    events = [{"type": "TOOL_CALL_START", "toolCallId": "a", "toolCallName": "spawn_subagents"},
              {"type": "TOOL_CALL_RESULT", "toolCallId": "a", "content": good},
              {"type": "TOOL_CALL_RESULT", "toolCallId": "b", "content": "other"}]
    assert ck.spawn_results_of(events) == [good]


def test_the_stop_results_9_and_10_read_the_spawn_arm(scripted) -> None:
    """Mutation: compare with ``<`` the other way → the slow case goes green."""
    rows, _ = scripted
    summary = run.summarise(rows)
    spawn = summary[run.SPAWN_ARM]
    assert spawn["fanout_calls"] >= 1 and spawn["fanout_ms_mean"] is not None
    assert spawn["not_spawned_turns"] == 5 and spawn["spawned_on_not_spawned"] == 0
    states = {n: s for n, s, _ in run.stop_results(summary)}
    assert states[10] == "ok" and states[9] in ("ok", "STOP")
    slow = json.loads(json.dumps(summary))
    slow[run.SPAWN_ARM]["research_total_median"] = 99.0
    slow["pack"]["research_total_median"] = 1.0
    assert {n: s for n, s, _ in run.stop_results(slow)}[9] == "STOP"
    over = json.loads(json.dumps(summary))
    over[run.SPAWN_ARM].update(not_spawned_turns=9, spawned_on_not_spawned=2)
    assert {n: s for n, s, _ in run.stop_results(over)}[10] == "STOP"
    over[run.SPAWN_ARM].update(spawned_on_not_spawned=1)
    assert {n: s for n, s, _ in run.stop_results(over)}[10] == "ok"
    assert "packs+spawn" in run.report({"summary": summary})


def test_base_tools_that_cover_the_need_satisfy_right_pack() -> None:
    """The ``crm_miss`` chats: the model answered right with the read tools of
    the base (``crm_list_records``, ``crm_get_record``), and ``right_pack`` failed
    a correct answer. A read of the ``crm`` family now counts. A hand-off, a
    write and a tool of another family do not. The facts of the answer are judged
    by their own checks.
    Mutation: empty ``base_cover`` → the first case is red; let a hand-off pass
    on the cover → the hand-off case is red."""
    base = [("crm_list_records", {}, "Contacts: Priya Menon"), ("crm_get_record", {}, "ok")]
    assert _one("right_pack", "crm-records", _obs(work_calls=base)) is True
    assert _one("right_pack", "crm-records", _obs(work_calls=[base[0]])) is True
    lost = [("crm_list_records", {}, "Error: Requested function not found.")]
    assert _one("right_pack", "crm-records", _obs(work_calls=lost)) is False
    assert _one("right_pack", "crm-records", _obs()) is False
    # A hand-off still fails when the pack was reachable, base tool or not.
    assert _one("right_pack", "crm-records", _obs(
        work_calls=base, handoffs={CRM: ["x"]}, needs="crm-records",
        reachable=frozenset({"crm-records"}))) is False
    # A tool outside the family does not cover the pack, and Projects has no cover.
    assert _one("right_pack", "crm-records", _obs(work_calls=[("my_work", {}, "ok")])) is False
    assert _one("right_pack", "projects", _obs(work_calls=base)) is False
    cover = ck.BASE_COVER["crm-records"]
    assert "crm_list_records" in cover and not cover & {"create_lead", "update_deal_status",
                                                         "convert_lead", "log_activity"}
    # The wrong answer from base tools still fails its own check.
    turn = scenario("crm_miss").turns[0]
    wrong = ck.check_turn(turn, _obs(work_calls=base, answer="I cannot tell."))
    assert wrong["right_pack"] is True and wrong["names_the_person"] is False
    right = ck.check_turn(turn, _obs(work_calls=base, answer="Priya Menon at Orbit Labs."))
    assert right["names_the_person"] is True and right["right_pack"] is True


# ── the pack flag ────────────────────────────────────────────────────────────


def test_the_pack_flag_turns_on_every_pack_of_each_owner() -> None:
    """OA-7i drops a specialist only when the loader reaches EVERY pack of its
    owner. Run 2 of the email measure set one pack, and 31 turns handed off.
    Mutation: ``pack_flag`` leaves out ``projects-bulk`` → the set differs → red."""
    flag = run.pack_flag(pack.OWNERS)
    slugs = set(flag["tool_packs"].split(","))
    want = {s for o in pack.OWNERS for s in tool_packs.owner_packs(o)}
    assert slugs == want and flag["tool_packs_orgs"] == run.ds.ORG
    assert {"crm-records", "projects", "projects-plan", "projects-structure",
            "projects-bulk"} <= slugs
    assert len(tool_packs.owner_packs(PROJECTS)) == 4
    # The email chats keep the email owner alone, as in OA-7h.
    assert set(run.pack_flag()["tool_packs"].split(",")) == set(tool_packs.owner_packs(EA))
    assert run.owners_of(scenario("read_invoice")) == (EA,)
    assert run.owners_of(scenario("crm_pipeline")) == pack.OWNERS


@pytest.mark.parametrize(("sid", "via", "gone", "stays"), [
    # CRM page: the loader reaches the email and Projects packs, so both
    # specialists leave. The CRM assistant has one pack, and it is loaded.
    ("crm_pipeline", {EA, PROJECTS}, {EA, CRM, PROJECTS}, set()),
    # Tasks page: the core Projects pack stands in the request. With the exact
    # count of OA-8f the harness base is 55 (the 3 CRM facts tools are not
    # counted for an org off the CRM list, and the count was 58 before). The
    # plan sees 78 tools: 55, the load tool (1) and the 23 core tools. Each
    # email pack fits beside that with the one email specialist tool gone
    # (77 + 31 = 108 and 77 + 28 = 105), so the loader reaches
    # ``email-assistant`` too. The plan (35) and structure (34) packs still do
    # not fit (113 and 112), so ``projects-assistant`` stays.
    # With the base trim on (OA-8f, run 2 onward) every pack fits, so no specialist stays.
    ("pm_find_task", {CRM, EA, PROJECTS}, {CRM, EA, PROJECTS}, set()),
])
def test_the_loader_reaches_the_owners_that_it_can_and_the_rest_keep_the_specialist(
        sid: str, via: set[str], gone: set[str], stays: set[str]) -> None:
    """The first scripted turn logs the plan of the loader. An owner that the
    loader reaches (or whose packs are all loaded) loses its specialist tool, and
    an owner that it cannot reach keeps it. The delegation arm holds all three.
    Mutation: drop one pack from the flag → ``via_loader`` lacks its owner →
    red."""
    sc = scenario(sid)
    models = run.scripted_models(sc, "pack")
    (first, *_) = run.run_chat(sc, "pack", models=models)
    plan = next(e for e in first["pack_events"] if e["event"] == "executor.pack_plan")
    assert set(re.findall(r"[a-z][a-z-]*-assistant", str(plan["via_loader"]))) == via, plan
    offered = {t["function"]["name"] for t in models[ORCHESTRATOR].bodies[0]["tools"]}
    assert not offered & {pack.DELEGATE_TOOLS[o] for o in gone}, offered
    assert {pack.DELEGATE_TOOLS[o] for o in stays} <= offered
    assert "load_skill_tools" in offered
    models = run.scripted_models(sc, "delegation")
    run.run_chat(sc, "delegation", models=models)
    offered = {t["function"]["name"] for t in models[ORCHESTRATOR].bodies[0]["tools"]}
    assert set(pack.DELEGATE_TOOLS.values()) <= offered and "load_skill_tools" not in offered


def test_a_pack_left_out_of_the_flag_keeps_its_specialist(monkeypatch) -> None:
    """The fence for trap 1, run through the real loader: with one Projects pack
    off, the loader cannot reach the owner, and ``projects-assistant`` stays in
    the plan's ``via_loader`` list only when all four are on."""
    (full,) = run.run_chat(scenario("crm_deal_story"), "pack")
    plan = next(e for e in full["pack_events"] if e["event"] == "executor.pack_plan")
    assert "projects-assistant" in plan["via_loader"], plan
    real = run.pack_flag

    def short(owners=(pack.EA,)):
        flag = real(owners)
        flag["tool_packs"] = ",".join(s for s in flag["tool_packs"].split(",")
                                      if s != "projects-bulk")
        return flag

    monkeypatch.setattr(run, "pack_flag", short)
    (dropped,) = run.run_chat(scenario("crm_deal_story"), "pack")
    plan = next(e for e in dropped["pack_events"] if e["event"] == "executor.pack_plan")
    assert "projects-assistant" not in plan["via_loader"], plan


# ── the layers ───────────────────────────────────────────────────────────────


def _names(body: dict[str, Any]) -> set[str]:
    return {t["function"]["name"] for t in body.get("tools") or []}


def test_the_surface_loads_the_pack_with_no_word_and_no_load_call() -> None:
    """Layer (a), input 2. ``pm_find_task`` is on the Tasks page, and the turn
    holds no word of the pack, yet ``find_tasks`` is in the first request.
    Mutation: drop ``surface_scope`` in ``run_chat`` → no pack at the first
    request → the tool is not found → ``ran_tool`` red."""
    sc = scenario("pm_find_task")
    models = run.scripted_models(sc, "pack")
    (row,) = run.run_chat(sc, "pack", models=models)
    assert "find_tasks" in _names(models[ORCHESTRATOR].bodies[0]), row["pack_events"]
    assert row["checks"]["right_pack"] is True and row["loaded_by_model"] == []


def test_a_miss_is_loaded_by_the_model_at_layer_b() -> None:
    for sid, slug in (("crm_miss", "crm-records"), ("pm_miss", "projects")):
        sc = scenario(sid)
        models = run.scripted_models(sc, "pack")
        (row,) = run.run_chat(sc, "pack", models=models)
        assert row["loaded_by_model"] == [slug], (sid, row["loaded_by_model"])
        assert row["passed"], (sid, row["checks"])
        assert row["pack_events"] and any(
            e["event"] == "tool_packs.loaded" for e in row["pack_events"]), sid


def test_with_the_base_trim_the_plan_and_structure_packs_load_on_a_projects_page() -> None:
    """Run 1 had no room for them beside the core pack, and the route went to the
    owner. With the trim of OA-8f (both arms, run 2 onward) the loader offers
    them and the model loads them, so no hand-off is owed.
    Mutation: set `orchestrator_floor_trim` False in `PRODUCTION` → the
    structure pack is not offered → red. Since OA-8g, "overloaded" loads the
    plan pack by a word on the first request, so ``pm_capacity`` needs no load."""
    (row,) = run.run_chat(scenario("pm_status"), "pack")
    assert row["specialist_handoffs"] == {} and row["loaded_by_model"] == ["projects-structure"], row
    assert row["passed"], row["checks"]
    plan = next(e for e in row["pack_events"] if e["event"] == "executor.pack_plan")
    assert "projects-structure" in plan["offered"], plan
    (row,) = run.run_chat(scenario("pm_capacity"), "pack")
    assert row["specialist_handoffs"] == {} and row["loaded_by_model"] == [], row
    assert row["passed"], row["checks"]
    plan = next(e for e in row["pack_events"] if e["event"] == "executor.pack_plan")
    assert "projects-plan" in plan["packs"], plan


def test_a_word_of_the_pack_loads_it_in_place_of_the_core_pack() -> None:
    """``choose`` lets a pack that replaces the core pack take its place when
    both match. ``pm_tags`` says "task tags" on the Projects page."""
    (row,) = run.run_chat(scenario("pm_tags"), "pack")
    assert row["passed"] and row["specialist_handoffs"] == {} and row["loaded_by_model"] == []
    assert row["work_tools"] == ["create_tags"]


# ── the arms ─────────────────────────────────────────────────────────────────


def test_the_delegation_arm_reaches_the_real_specialists(scripted) -> None:
    rows, _ = scripted
    row = next(r for r in rows if r["arm"] == "delegation" and r["scenario"] == "crm_log_call")
    assert row["work_tools"] == ["search_crm", "log_activity"]
    assert row["specialist_handoffs"] == {CRM: 1}
    assert row["work_writes"] == [f"POST /crm/deals/{wg.cid(1)}/activities"]
    assert row["cards"] == 1
    assert row["checks"]["no_crm_delegation"] is None and row["checks"]["right_pack"] is None


def test_the_pack_arm_runs_the_tools_in_the_orchestrators_own_run(scripted) -> None:
    rows, _ = scripted
    row = next(r for r in rows if r["arm"] == "pack" and r["scenario"] == "crm_log_call")
    assert row["work_tools"] == ["search_crm", "log_activity"] and row["specialist_handoffs"] == {}
    assert row["checks"]["no_crm_delegation"] is True and row["checks"]["right_pack"] is True
    assert row["checks"]["one_card_per_write"] is True and row["cards"] == 1
    assert {c["agent"] for c in row["model_calls"]} == {ORCHESTRATOR}


def test_the_fake_gateway_answers_every_route_the_set_reaches(scripted) -> None:
    rows, _ = scripted
    assert [r["unknown_routes"] for r in rows if r["unknown_routes"]] == []
    writes = {w.split()[0] for r in rows for w in r["work_writes"]}
    assert writes == {"POST", "PATCH"}


def test_the_work_gateway_leaves_the_other_requests_to_the_transport_guard() -> None:
    """A request to any other host still reaches the trap of the scripted run,
    and the run fails. Mutation: answer every URL in the fake → red."""
    import asyncio

    import httpx

    from evals.routing.harness import RoutingHarness

    async def go() -> list[str]:
        async with httpx.AsyncClient() as client:
            seen = []
            for url in (f"{wg.WorkGateway.base()}/crm/mode", "https://example.com/crm/mode"):
                try:
                    seen.append(str((await client.get(url)).status_code))
                except AssertionError as exc:
                    seen.append("trap")
                    assert "scripted run sent" in str(exc)
            return seen

    with RoutingHarness() as harness:
        gateway = wg.WorkGateway()
        gateway.install(harness._mp)
        assert asyncio.run(go()) == ["200", "trap"]
        assert harness.sent == ["GET https://example.com/crm/mode"]
        assert gateway.calls == [("GET", "/crm/mode", {})]


# ── Mirror mode ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("arm", ["delegation", "pack"])
def test_mirror_mode_takes_the_record_writes_from_the_request(arm: str) -> None:
    """In Mirror mode the request holds no tool that creates a lead, moves a deal
    or converts a lead, and ``log_activity`` stays. Mutation: make the ``crm_mode``
    read of ``run_chat`` answer ``native`` → the tools stay → red."""
    sc = scenario("crm_mirror")
    models = run.scripted_models(sc, arm)
    rows = run.run_chat(sc, arm, models=models)
    assert all(r["passed"] and r["checks"]["mirror_respected"] is True for r in rows)
    if arm == "pack":
        names = _names(models[ORCHESTRATOR].bodies[0])
        assert not names & ck.MIRROR_TOOLS and "log_activity" in names and "search_crm" in names
    assert all(r["work_writes"] == [] for r in rows)


def test_a_move_in_mirror_mode_is_refused_by_the_gateway() -> None:
    gate = wg.WorkGateway(mode="mirror")
    out = gate.route("PATCH", f"/crm/deals/{wg.cid(1)}", {}, {"status_id": "x"})
    assert out[0] == 409 and out[1]["detail"]["code"] == "mirrored_record"
    assert wg.WorkGateway().route("PATCH", f"/crm/deals/{wg.cid(1)}", {}, {"a": 1})["a"] == 1


# ── the checks, a pass and a fail each ───────────────────────────────────────


#: One request at the gateway, for a test of a write call that succeeded.
_W = [("POST", "/projects/tasks", {})]


def _obs(**kw: Any) -> ck.Observation:
    return ck.Observation(arm=kw.pop("arm", "pack"), **kw)


def _one(kind: str, arg: Any, obs: ck.Observation) -> bool | None:
    return ck.check_turn(Turn("x", (Check(kind, arg),), Plan()), obs)[kind]


def _auto(name: str, obs: ck.Observation) -> bool | None:
    return ck.check_turn(Turn("x", (), Plan()), obs)[name]


def test_ran_tool_needs_a_call_that_ran() -> None:
    """Mutation: count a call whose result is "Error: Requested function ... not
    found" → the first run of 2026-10-28 passed a tool that was not in the
    request → this case passes → red."""
    ok = [("get_pipeline", {}, "Deal pipeline")]
    assert _one("ran_tool", ("get_pipeline",), _obs(work_calls=ok)) is True
    assert _one("ran_tool", ("get_pipeline",), _obs(sub_work_calls=ok)) is True
    assert _one("ran_tool", ("get_pipeline",), _obs()) is False
    lost = [("get_pipeline", {}, 'Error: Requested function "get_pipeline" not found.')]
    assert _one("ran_tool", ("get_pipeline",), _obs(work_calls=lost)) is False
    assert _one("ran_tool", ("search_crm",), _obs(work_calls=ok)) is False


def test_an_unasked_write_fails_a_read_turn() -> None:
    """Mutation: drop the gateway half of the check → a write that the tool hid
    from the call list passes → red."""
    write = [("update_deal_status", {}, "Moved")]
    sent = [("PATCH", "/crm/deals/x", {})]
    assert _one("no_unasked_write", 0, _obs(work_calls=[("get_pipeline", {}, "ok")])) is True
    assert _one("no_unasked_write", 0, _obs(work_calls=write, work_writes=sent)) is False
    assert _one("no_unasked_write", 0, _obs(work_writes=sent)) is False
    assert _one("no_unasked_write", 1, _obs(work_calls=write, work_writes=sent)) is True
    two = [("update_deal_status", {}, "a"), ("log_activity", {}, "b")]
    assert _one("no_unasked_write", 1, _obs(work_calls=two, work_writes=sent)) is False
    # A batch tool writes one row for each task, and is one write.
    rows = [("POST", "/projects/tasks", {})] * 3
    assert _one("no_unasked_write", 1, _obs(
        work_calls=[("create_tasks", {}, "ok")], work_writes=rows)) is True


def test_a_write_must_reach_the_gateway() -> None:
    sent = [("PATCH", f"/crm/deals/{wg.cid(2)}", {"status_id": wg.cid(903)})]
    arg = ("PATCH", r"^/crm/deals/", wg.cid(903))
    assert _one("work_write", arg, _obs(work_writes=sent)) is True
    assert _one("work_write", arg, _obs()) is False
    assert _one("work_write", ("PATCH", r"^/crm/deals/", wg.cid(904)), _obs(work_writes=sent)) is False
    assert _one("work_write", ("POST", r"^/crm/deals/"), _obs(work_writes=sent)) is False


def test_answer_none_fails_on_a_named_fact() -> None:
    assert _one("answer_none", (r"Vantage",), _obs(answer="Orbit and Kestrel.")) is True
    assert _one("answer_none", (r"Vantage",), _obs(answer="Orbit and Vantage.")) is False


def test_right_pack_needs_a_pack_tool_in_the_run_or_a_hand_off() -> None:
    """Mutation: let a tool of a sub-run count → the delegation path passes the
    pack arm's check → the first case below goes green → red."""
    ran = [("get_pipeline", {}, "ok")]
    assert _one("right_pack", "crm-records", _obs(work_calls=ran)) is True
    assert _one("right_pack", "crm-records", _obs(sub_work_calls=ran)) is False
    assert _one("right_pack", "crm-records", _obs()) is False
    # A hand-off passes on the rule of ``no_*_delegation`` only (P3).
    away = frozenset({"projects"})
    assert _one("right_pack", "crm-records", _obs(handoffs={CRM: ["x"]})) is False
    assert _one("right_pack", "crm-records", _obs(
        handoffs={CRM: ["x"]}, needs="crm-records", reachable=frozenset({"crm-records"}))) is False
    assert _one("right_pack", "crm-records", _obs(
        handoffs={CRM: ["x"]}, needs="crm-records", reachable=frozenset())) is True
    assert _one("right_pack", "projects-plan", _obs(
        handoffs={PROJECTS: ["x"]}, needs="projects-plan", reachable=away)) is True
    assert _one("right_pack", "crm-records", _obs(handoffs={PROJECTS: ["x"]})) is False
    assert _one("right_pack", "projects", _obs(work_calls=ran)) is False
    lost = [("get_pipeline", {}, "Error: Requested function not found.")]
    assert _one("right_pack", "crm-records", _obs(work_calls=lost)) is False
    assert _one("right_pack", "crm-records", _obs(arm="delegation", work_calls=ran)) is None


def test_no_crm_delegation_fails_a_hand_off_of_covered_work() -> None:
    """Mutation: pass any hand-off → the first fail case goes green → red."""
    kw = {"owners": pack.OWNERS, "handoffs": {CRM: ["Move the deal."]}}
    assert _auto("no_crm_delegation", _obs(**kw)) is False
    assert _auto("no_crm_delegation", _obs(**kw, needs="crm-records",
                                           reachable=frozenset({"crm-records"}))) is False
    assert _auto("no_crm_delegation", _obs(owners=pack.OWNERS, reachable=frozenset())) is True
    assert _auto("no_crm_delegation", _obs(arm="delegation", **kw)) is None
    assert _auto("no_crm_delegation", _obs(handoffs={CRM: ["x"]})) is None
    # A hand-off to the Projects assistant is not a CRM hand-off.
    assert _auto("no_crm_delegation", _obs(
        owners=pack.OWNERS, handoffs={PROJECTS: ["x"]}, reachable=frozenset())) is True


def test_no_projects_delegation_passes_only_when_no_pack_fits() -> None:
    """The pack is unreachable (not loaded, not offered): the route to the owner
    is legal. A reachable pack, an undeclared need and an unread log fail.
    Mutation: treat an unread log as "not reachable" → the last case goes
    green → red."""
    base = {"owners": pack.OWNERS, "handoffs": {PROJECTS: ["Rename the status."]}}
    none = frozenset({"projects", "projects-bulk"})
    assert _auto("no_projects_delegation", _obs(
        **base, needs="projects-structure", reachable=none)) is True
    assert _auto("no_projects_delegation", _obs(
        **base, needs="projects-bulk", reachable=none)) is False
    assert _auto("no_projects_delegation", _obs(**base, reachable=none)) is False
    assert _auto("no_projects_delegation", _obs(**base, needs="projects-structure")) is False
    assert _auto("no_projects_delegation", _obs(owners=pack.OWNERS, reachable=none)) is True
    assert _auto("no_projects_delegation", _obs(arm="delegation", **base)) is None


def test_mirror_respected_fails_a_record_write() -> None:
    """Mutation: allow ``update_deal_status`` in ``MIRROR_TOOLS`` → red."""
    mirror = {"mirror": True}
    assert _auto("mirror_respected", _obs()) is None
    assert _auto("mirror_respected", _obs(**mirror)) is True
    note = [("POST", f"/crm/deals/{wg.cid(1)}/activities", {})]
    for name in ("create_lead", "update_deal_status", "convert_lead"):
        assert _auto("mirror_respected", _obs(
            **mirror, work_calls=[(name, {}, "x")], work_writes=note)) is False
    for write in (("POST", "/crm/leads", {}), ("PATCH", f"/crm/deals/{wg.cid(1)}", {}),
                  ("POST", f"/crm/leads/{wg.cid(21)}/convert", {})):
        assert _auto("mirror_respected", _obs(**mirror, work_writes=[write])) is False
    # A logged note saves in Metorite only, and stays legal.
    assert _auto("mirror_respected", _obs(
        **mirror, work_calls=[("log_activity", {}, "x")], work_writes=note)) is True


def test_one_card_per_write_needs_a_card_for_each_write_tool() -> None:
    """Mutation: drop the lower bound → a write with no card passes → red."""
    write = [("update_deal_status", {}, "Moved")]
    assert _auto("one_card_per_write", _obs()) is None
    assert _auto("one_card_per_write", _obs(work_calls=[("get_pipeline", {}, "ok")])) is None
    assert _auto("one_card_per_write", _obs(
        work_writes=_W, work_calls=write, write_cards=[(("update_deal_status",), 1)])) is True
    assert _auto("one_card_per_write", _obs(work_writes=_W, work_calls=write)) is False
    assert _auto("one_card_per_write", _obs(
        work_writes=_W, work_calls=write, write_cards=[(("update_deal_status",), 2)])) is False
    both = [*write, ("log_activity", {}, "ok")]
    one, two = ("update_deal_status",), ("log_activity",)
    assert _auto("one_card_per_write", _obs(work_writes=_W, work_calls=both, write_cards=[
        (one, 1), (two, 1)])) is True
    # P2-a, hidden failure 1: two writes, one card. The total passed it.
    assert _auto("one_card_per_write", _obs(work_writes=_W, work_calls=both, write_cards=[
        (one, 1), (two, 0)])) is False
    # Hidden failure 2: two writes, two cards, both on one write.
    assert _auto("one_card_per_write", _obs(work_writes=_W, work_calls=both, write_cards=[
        (one, 2), (two, 0)])) is False
    # A card must be on the write that raised it, by tool name.
    assert _auto("one_card_per_write", _obs(work_writes=_W, work_calls=write, write_cards=[(two, 1)])) is False
    # Delegation arm: both writes in one delegated run need two cards.
    assert _auto("one_card_per_write", _obs(
        arm="delegation", work_writes=_W, work_calls=both, write_cards=[((*one, *two), 1)])) is False
    assert _auto("one_card_per_write", _obs(
        arm="delegation", work_writes=_W, work_calls=both, write_cards=[((*one, *two), 2)])) is True


def test_cards_are_bound_to_the_write_that_raised_them() -> None:
    """``write_cards_of`` reads the events: a card belongs to the open write.
    Mutation: count every card on the first write → the second case is red."""
    start = {"type": "TOOL_CALL_START", "toolCallName": "update_deal_status"}
    sub = {"type": "SUB_AGENT_TOOL_CALL_START", "toolCallName": "log_activity"}
    read = {"type": "TOOL_CALL_START", "toolCallName": "get_pipeline"}
    card = {"type": "CUSTOM", "name": "confirmation_requested", "value": {}}
    second = {"type": "TOOL_CALL_START", "toolCallName": "log_activity"}
    assert ck.write_cards_of([read, start, card, second, card]) == [
        (("update_deal_status",), 1), (("log_activity",), 1)]
    assert ck.write_cards_of([start, card, card, second]) == [
        (("update_deal_status",), 2), (("log_activity",), 0)]
    # A delegated run: the card comes before the sub-run's calls.
    hand = {"type": "TOOL_CALL_START", "toolCallName": "crm_assistant"}
    assert ck.write_cards_of([hand, card, sub]) == [(("log_activity",), 1)]
    # A card of a read or of another tool is not a work card.
    assert ck.write_cards_of([read, card]) == []


def test_the_request_may_not_pass_the_cap() -> None:
    """Mutation: compare with ``<`` and the cap itself fails → red."""
    cap = tool_packs.MAX_REQUEST_TOOLS
    assert _auto("tools_per_call", _obs(max_tools=cap)) is True
    assert _auto("tools_per_call", _obs(max_tools=cap + 1)) is False
    # P3: the pack arm with no count means the tap read none: fail. Delegation: None.
    assert _auto("tools_per_call", _obs()) is False
    assert _auto("tools_per_call", _obs(arm="delegation")) is None


def test_the_tool_call_readers() -> None:
    calls = [("crm_assistant", json.dumps({"task": "Move the deal"})),
             ("call_agent", json.dumps({"agent_name": PROJECTS, "message": "Archive"})),
             ("load_skill_tools", json.dumps({"pack": "projects-bulk"})),
             ("get_pipeline", "{}")]
    assert ck.handoffs_of(calls) == {CRM: ["Move the deal"], PROJECTS: ["Archive"]}
    assert ck.tasks_of(calls, EA) == [] and ck.loads_of(calls) == ["projects-bulk"]
    events = [
        {"type": "TOOL_CALL_START", "toolCallId": "a", "toolCallName": "get_pipeline"},
        {"type": "TOOL_CALL_ARGS", "toolCallId": "a", "delta": '{"owner": "x"}'},
        {"type": "TOOL_CALL_RESULT", "toolCallId": "a", "content": "ok"},
        {"type": "SUB_AGENT_TOOL_CALL_START", "agentName": CRM, "toolCallId": "b",
         "toolCallName": "search_crm", "args": '{"query": "q"}'},
        {"type": "SUB_AGENT_TOOL_CALL_RESULT", "agentName": CRM, "toolCallId": "b",
         "content": "rows"},
        {"type": "TOOL_CALL_START", "toolCallId": "c", "toolCallName": "unrelated_tool"},
    ]
    main, sub = ck.work_calls_of(events, ck.work_tool_names())
    assert main == [("get_pipeline", {"owner": "x"}, "ok")]
    assert sub == [("search_crm", {"query": "q"}, "rows")]


def test_the_tools_of_both_owners_are_known_and_a_write_is_a_write() -> None:
    names = ck.work_tool_names()
    assert len(names) > 100 and {"search_crm", "bulk_update", "analytics_load"} <= names
    assert {n for n in names if ck.is_write_tool(n)} >= CRM_WRITES
    assert not any(ck.is_write_tool(n) for n in ("get_pipeline", "my_work", "find_tasks"))
    # An unknown tool counts as a write: the check errs to failing.
    assert ck.is_write_tool("a_tool_nobody_annotated") is True


# ── the summary ──────────────────────────────────────────────────────────────


def test_the_summary_reports_the_sets_the_loader_and_the_stop_results(scripted) -> None:
    rows, _ = scripted
    summary = run.summarise(rows)
    packs, deleg = summary["pack"], summary["delegation"]
    assert {"crm", "projects", "mixed"} <= set(packs["by_set"])
    assert packs["by_set"]["crm"]["turns"] == 12 and packs["layer_b_turns"] >= 3
    assert packs["tools_per_call_max"] <= tool_packs.MAX_REQUEST_TOOLS
    assert packs["layer_a_ms_mean"] is not None and deleg["layer_a_ms_mean"] is None
    assert packs["specialist_handoffs"] == {}
    assert deleg["specialist_handoffs"][CRM] >= 10 and packs["guard_failures"] == []
    text = run.report({"summary": summary})
    assert "Stop result of" in text and "OA-8e" in text and "| crm | pack |" in text
    states = {n: state for n, state, _ in run.stop_results(summary)}
    assert states[1] == "ok" and states[2] == "ok" and states[9] in ("ok", "STOP")


def test_a_guard_failure_on_any_row_stops_oa_10(scripted) -> None:
    """Stop result 1. Mutation: drop a name from ``GUARD_CHECKS`` → the case for
    that name goes green → red."""
    rows, _ = scripted
    guards = ("held_card", "one_card", "one_card_per_write", "mirror_respected",
              "no_unasked_write", "tools_per_call")
    assert set(guards) == set(run.GUARD_CHECKS)
    for guard in guards:
        bad = [json.loads(json.dumps(r)) for r in rows]
        victim = next(r for r in bad if r["arm"] == "pack")
        victim["checks"][guard] = False
        states = {n: s for n, s, _ in run.stop_results(run.summarise(bad))}
        assert states[1] == "STOP", guard


def test_a_pack_arm_that_passes_fewer_turns_stops_oa_10(scripted) -> None:
    rows, _ = scripted
    bad = [json.loads(json.dumps(r)) for r in rows]
    losers = [r for r in bad if r["arm"] == "pack" and r.get("set") in ("crm", "projects")][:3]
    for r in losers:
        r["passed"] = False
    states = {n: s for n, s, _ in run.stop_results(run.summarise(bad))}
    assert states[2] == "STOP"


def test_the_unused_pack_and_layer_b_shares_read_the_log() -> None:
    loaded = {"pack_events": [{"event": "executor.packs_loaded",
                               "layers": "{'projects': 'surface'}"}],
              "work_tools": ["update_status"], "email_tools": []}
    assert run._layers_of(loaded) == {"projects": "surface"}
    assert run._unused_pack(loaded) is True
    assert run._unused_pack({**loaded, "work_tools": ["find_tasks"]}) is False
    assert run._unused_pack({"pack_events": []}) is False
    bare = {"model_calls": [], "pack_events": [], "first_text_s": None, "total_s": None,
            "passed": True, "scenario": "x", "turn": 1}
    rs = [{**bare, "checks": {"right_pack": True}, "loaded_by_model": ["projects-plan"]},
          {**bare, "checks": {"right_pack": True}, "loaded_by_model": []}]
    assert run.extra_summary(rs)["layer_b_share_pct"] == 50.0
    assert run.reachable_of([]) is None
    assert run.reachable_of([{"event": "executor.pack_built"}]) is None
    assert run.reachable_of([
        {"event": "executor.pack_plan", "offered": "['email-setup', 'projects-bulk']",
         "packs": "['projects']"},
        {"event": "tool_packs.loaded", "pack": "crm-records"}]) == frozenset(
            {"email-setup", "projects-bulk", "projects", "crm-records"})


# ── fix round 1: the unread log, the reasons of a hand-off ───────────────────


def test_a_run_with_no_pack_plan_line_passes_nothing(monkeypatch) -> None:
    """P1. The code logs ``executor.pack_plan`` only when it builds a pack or
    offers the loader. A flag that does nothing logs none, and the old
    ``reachable_of`` gave an empty set that read as "not reachable", so a
    hand-off passed. The real ``run_chat`` path, with the tap blind to the plan
    line, must give a failing hand-off check.
    Mutation: make ``reachable_of`` return ``frozenset()`` for no event → red."""
    real = run.reachable_of

    def blind(events):
        return real([e for e in events if e["event"] != "executor.pack_plan"])

    monkeypatch.setattr(run, "reachable_of", blind)
    sc = scenario("pm_capacity")  # a hand-off turn, legal when the plan is read
    (row,) = run.run_chat(sc, "pack")
    assert row["checks"]["no_projects_delegation"] is False, row["checks"]
    assert row["passed"] is False
    # Read as it is, the same turn passes.
    monkeypatch.setattr(run, "reachable_of", real)
    (row,) = run.run_chat(sc, "pack")
    assert row["checks"]["no_projects_delegation"] is True and row["passed"]
    assert row["checks"]["right_pack"] is True


def test_an_unread_plan_fails_a_turn_with_no_hand_off_too() -> None:
    assert _auto("no_crm_delegation", _obs(owners=pack.OWNERS)) is False
    assert _auto("no_projects_delegation", _obs(owners=pack.OWNERS)) is False
    assert _auto("no_projects_delegation", _obs(arm="delegation", owners=pack.OWNERS)) is None


def test_a_hand_off_reason_is_named() -> None:
    """Mutation: return ``did_not_fit`` for every unreachable pack → the
    ``not_offered`` case is red."""
    plan = "projects-plan"
    assert ck.handoff_reason(PROJECTS, plan, frozenset({"projects"})) == "did_not_fit"
    assert ck.handoff_reason(PROJECTS, plan, frozenset()) == "not_offered"
    assert ck.handoff_reason(PROJECTS, plan, frozenset({plan})) == "model_choice"
    assert ck.handoff_reason(PROJECTS, "", frozenset()) == "model_choice"
    assert ck.handoff_reason(PROJECTS, plan, None) == "model_choice"


def test_the_report_counts_hand_off_turns_on_a_packed_app(scripted) -> None:
    """P2-b. A hand-off that passes its check is still a turn the pack did not
    serve. The summary names it by owner and reason, and stop result 11 shows
    REVIEW for a legal one and STOP for a model choice."""
    rows, _ = scripted
    summary = run.summarise(rows)
    assert summary["pack"]["app_handoff_turns"] == {}
    assert summary["delegation"]["app_handoff_turns"] == {}
    states = {n: (s, why) for n, s, why in run.stop_results(summary)}
    assert states[11][0] == "ok"
    # One legal hand-off (no room) reads REVIEW, and a model choice reads STOP.
    legal = [json.loads(json.dumps(r)) for r in rows]
    victim = next(r for r in legal if r["arm"] == "pack")
    victim["handoff_reasons"] = {PROJECTS: "did_not_fit"}
    state, why = {n: (s, w) for n, s, w in run.stop_results(run.summarise(legal))}[11]
    assert state == "REVIEW" and "1 hand-off turns" in why
    bad = [json.loads(json.dumps(r)) for r in rows]
    victim = next(r for r in bad if r["arm"] == "pack")
    victim["handoff_reasons"] = {PROJECTS: "model_choice"}
    assert {n: s for n, s, _ in run.stop_results(run.summarise(bad))}[11] == "STOP"
    clean = [r for r in rows if not (r["arm"] == "pack" and r.get("handoff_reasons"))]
    assert {n: s for n, s, _ in run.stop_results(run.summarise(clean))}[11] == "ok"
    assert "Hand-off turns on a packed app" in run.report({"summary": summary})


def test_mirror_chat_accepts_zoho_or_an_offer_of_a_note() -> None:
    turn = scenario("crm_mirror").turns[1]
    check = next(c for c in turn.checks if c.kind == "answer_some")
    zoho = _obs(answer="Zoho owns the deal.")
    note = _obs(answer="I cannot move it, but I can add a note.")
    neither = _obs(answer="Done, the deal is in Proposal.")
    for obs, want in ((zoho, True), (note, True), (neither, False)):
        assert ck.KINDS[check.kind](obs, check.arg) is want


# ── fix round 2: the six harness defects ─────────────────────────────────────


def test_a_refused_call_is_no_write() -> None:
    """Item 2. A tool that is not in the request (dropped in Mirror mode, held,
    not found) or that refuses its arguments did not write. The three guard
    checks must not count it.
    Mutation: make ``refused`` return False → every case below is red."""
    gone = 'Error: Requested function "update_deal_status" not found.'
    bad = "Refused: task_id must be a UUID from an earlier tool result."
    for result in (gone, bad):
        call = [("update_deal_status", {}, result)]
        assert ck.refused(result) is True
        assert _one("no_unasked_write", 0, _obs(work_calls=call)) is True
        assert _auto("one_card_per_write", _obs(work_calls=call)) is None
        assert _auto("mirror_respected", _obs(mirror=True, work_calls=call)) is True
    assert ck.refused("Moved Kestrel pilot to Negotiation.") is False
    ran = [("update_deal_status", {}, "Moved")]
    assert _one("no_unasked_write", 0, _obs(work_calls=ran, work_writes=_W)) is False
    assert _auto("mirror_respected", _obs(
        mirror=True, work_calls=ran,
        work_writes=[("POST", f"/crm/deals/{wg.cid(1)}/activities", {})])) is False
    # One write that ran and one that was refused: one card, on the one that ran.
    both = [("complete", {}, bad), ("complete", {}, "Done: #22")]
    assert _auto("one_card_per_write", _obs(
        work_writes=_W, work_calls=both,
        write_cards=[(("complete",), 0), (("complete",), 1)])) is True
    assert _one("no_unasked_write", 1, _obs(work_calls=both, work_writes=_W)) is True
    # A write that ran and showed no card still fails.
    assert _auto("one_card_per_write", _obs(work_calls=[both[1]], work_writes=_W)) is False


def test_crm_report_reads_as_a_read_in_the_checks() -> None:
    """Item 3. ``crm_report`` carries ``read_only=False`` in
    ``tool_annotations.py`` (line 143) ON PURPOSE: with ``make_report=true`` it
    writes two files, so an edited turn must not run it again. The comment sits
    before the tool in ``crm_tools.py``. It sends only GET, so it is no product
    bug and no CRM record changes. The harness table
    ``READ_DESPITE_ANNOTATION`` makes it a read here.
    Mutation: empty the table → the first case is red."""
    from acb_skills.tool_annotations import get_annotations

    assert get_annotations("crm_report")["read_only"] is False  # the product says so
    assert ck.is_write_tool("crm_report") is False and ck.is_write_tool("status_report") is False
    call = [("crm_report", {}, "CRM report pipeline:")]
    assert _one("no_unasked_write", 0, _obs(work_calls=call)) is True
    assert _auto("one_card_per_write", _obs(work_calls=call)) is None
    assert ck.is_write_tool("update_deal_status") is True
    assert {"crm_report", "status_report"} == ck.READ_DESPITE_ANNOTATION


@pytest.mark.parametrize(("text", "want"), [
    ("Negotiation holds INR 450,000.", True),
    ("The Orbit deal is worth \u20b94.5 lakh.", True),
    ("It is worth 4.50 lakh.", True),
    ("It is worth 4,50,000 rupees.", True),
    ("It is worth 450000.", True),
    ("It is worth 4.5L.", True),
    ("It is worth 4.5 lac.", True),
    ("It is worth 45,000.", False),
    ("It is worth 14.5 lakh.", False),
    ("It is worth 1,450,000.", False),
    ("It is worth 4.5 crore.", False),
])
def test_the_amount_check_takes_the_indian_forms(text: str, want: bool) -> None:
    """Item 4. The first live run failed `crm_pipeline` on both arms, because
    the model wrote "4.5 lakh" and the check wanted "450,000".
    The check calls ``acb_common.number_check.find_figures``, the one parser.
    Mutation: compare the text of the figure to "450,000" → the lakh cases are
    red."""
    turn = scenario("crm_pipeline").turns[0]
    check = next(c for c in turn.checks if c.name == "gives_the_amount")
    assert ck.KINDS[check.kind](_obs(answer=f"Negotiation. {text}"), check.arg) is want


def test_a_run_reads_no_local_database() -> None:
    """Item 5. The first live run let `retrieve_entity_context` read the local
    dev database, and the text reached the Router. ``no_local_database`` closes
    the graph opener of every run, so the tool answers "no match" and opens no
    session. The sentinel below fails if the tool reaches the opener.
    Mutation: remove the ``no_local_database`` call from ``run_chat`` → the
    sentinel raises, the tool answers an error, and the case is red."""
    assert {"retrieve_entity_context", "retrieve_sales_context"} == run.LOCAL_DB_TOOLS
    sc = scenario("crm_miss")
    models = {
        ORCHESTRATOR: ReplyModel([
            tool_turn("retrieve_entity_context", {"query": "Orbit"}, "r1"),
            tool_turn("retrieve_sales_context", {"query": "Orbit"}, "r2"),
            text_turn("No match."),
        ])}

    from orchestrator.agents import retrieve_entity_context

    scope = retrieve_entity_context.__globals__  # the module that holds the tools

    def sentinel(*_a, **_k):
        raise AssertionError("a run read the local database")

    real = (scope["retrieve"], scope["_sales_context_fn"])
    scope["retrieve"] = scope["_sales_context_fn"] = sentinel
    try:
        (row,) = run.run_chat(sc, "pack", models=models)
    finally:
        scope["retrieve"], scope["_sales_context_fn"] = real
    results = json.dumps(models[ORCHESTRATOR].bodies[-1]["messages"], ensure_ascii=False)
    assert "(no matching entities found)" in results and "(no matching sales data found)" in results
    assert "read the local database" not in results and row["error"] is None


def test_a_chained_spent_before_counts_each_chunk_once(tmp_path) -> None:
    """Item 6. ``spent_usd`` of a chunk already holds the chunks before it. The
    first live run summed those figures and counted the early chunks twice.
    Mutation: sum ``spent_usd`` in ``spent_so_far`` or in ``merge`` → the total
    is 4, not 3 → red."""
    a = {"rows": [], "spent_usd": 1.0, "spent_before_usd": 0.0, "stopped": None}
    b = {"rows": [], "spent_usd": 3.0, "spent_before_usd": 1.0, "stopped": None}
    pa, pb = tmp_path / "a.json", tmp_path / "b.json"
    pa.write_text(json.dumps(a), encoding="utf-8")
    pb.write_text(json.dumps(b), encoding="utf-8")
    assert run.chunk_spend(a) == 1.0 and run.chunk_spend(b) == 2.0
    assert run.spent_so_far([pa]) == 1.0 and run.spent_so_far([pa, pb]) == 3.0
    merged = run.merge([pa, pb], tmp_path / "m.json")
    assert merged["spent_usd"] == 3.0
    # A chunk written by the runner carries its own figure.
    out = tmp_path / "w.json"
    data = {"rows": [], "spent_usd": 2.5, "spent_before_usd": 2.0}
    run._write(out, data)
    assert json.loads(out.read_text(encoding="utf-8"))["spent_this_chunk_usd"] == 0.5


def test_the_fake_answers_the_routes_of_the_first_live_run() -> None:
    """Item 1. Each route that the live models called and the fake lacked now
    has a fixture. A route with no fixture still answers 404 and is listed.
    Mutation: drop the ``_crm_read_family`` call from ``_crm`` → the CRM cases
    answer 404 → red."""
    gate = wg.WorkGateway()
    deal = wg.cid(1)
    crm = ["sync/short-reads", "fields/deals", "fields/contacts", "fields/leads",
           "fields/organizations", "pipelines", "owners", f"deals/{deal}/contacts",
           "reports/pipeline", "reports/funnel", "reports/win-loss", "reports/owners",
           "aggregate", "aggregate/stage-dwell"]
    for path in crm:
        assert gate.route("GET", f"/crm/{path}", {}, None) is not None, path
    pm = ["calendar", "my/calendar", "my/inbox", "my/reminders", "my/areas", "intake",
          "assignees", "people/names", "needs-attention", "agent-runs"]
    for path in pm:
        assert gate.route("GET", f"/projects/{path}", {}, None) is not None, path
    task = wg.TASK_IDS[13]
    assert gate.route("POST", f"/projects/tasks/{task}/archive", {}, {})["archived_at"]
    assert gate.route("POST", "/projects/nodes", {}, {"name": "X"})["name"] == "X"
    assert gate.unknown == []
    # The unknown stays unknown, and the catalogue is off (a real answer).
    assert gate.route("GET", "/crm/never-heard-of-it", {}, None) is None
    assert gate.route("GET", "/crm/catalogue", {}, None)[0] == 404
    assert gate.route("GET", "/projects/nodes/" + wg.pid(999), {}, None) is None


def test_the_crm_read_tools_run_against_the_fake_without_a_404() -> None:
    """The `crm_*` read family and the Projects reads reach the fake through the
    real tools. Mutation: drop one fixture → that tool shows a 404 → red."""
    import asyncio

    from acb_skills import crm_tools

    from evals.routing.harness import RoutingHarness

    deal = wg.cid(1)

    async def go() -> list[str]:
        out = []
        for call in (crm_tools.crm_list_pipelines(), crm_tools.crm_list_owners(),
                     crm_tools.crm_report("pipeline"), crm_tools.crm_report("owners"),
                     crm_tools.crm_stage_dwell(), crm_tools.crm_deal_contacts(deal),
                     crm_tools.crm_list_fields("deals")):
            out.append(await call)
        return out

    with RoutingHarness() as harness:
        gate = wg.WorkGateway()
        gate.install(harness._mp)
        from acb_skills import memory_tools

        token = memory_tools._memory_user_id.set(run.ds.MEMBER)
        try:
            answers = asyncio.run(go())
        finally:
            memory_tools._memory_user_id.reset(token)
    assert gate.unknown == [], gate.unknown
    assert all(a and "404" not in a for a in answers), answers


def test_both_arms_run_with_the_base_trim_on() -> None:
    """Run 2 of OA-8e ships the trimmed base on BOTH arms, in the eval process
    only. Mutation: drop the key from ``PRODUCTION`` → red."""
    from acb_common import get_settings

    assert run.PRODUCTION["orchestrator_floor_trim"] is True
    seen: list[tuple[str, bool]] = []
    for arm in ("delegation", "pack"):
        run.run_chat(scenario("crm_deal_story"), arm,
                     on_turn=lambda _r, a=arm: seen.append((a, get_settings().orchestrator_floor_trim)))
    assert seen == [("delegation", True), ("pack", True)]
    assert get_settings().orchestrator_floor_trim is False


# ── fix round 3: hosts, refusals, wording ────────────────────────────────────


def test_the_fixture_hosts_look_like_businesses_and_the_guard_still_refuses_a_real_host() -> None:
    """Item 1. The `.example` hosts read as placeholders, and the model would
    not fetch. The new hosts are invented business names. The fixtures answer
    the tools, and an httpx request to any host (also these) still meets the
    trap of the scripted run.
    Mutation: put `.example` back in a `SUPPLIER_HOSTS` host → the first assert is red."""
    import asyncio

    import httpx

    from evals.one_assistant_measure import fanout
    from evals.routing.harness import RoutingHarness

    urls = [*fanout.PAGES, fanout.PRICE_URL]
    assert all(".example" not in u and u.startswith("https://") for u in urls)
    assert fanout.PRICE_URL in fanout.PAGES and len(fanout.PAGES) == 21

    async def go() -> list[str]:
        out = []
        async with httpx.AsyncClient() as client:
            for url in (fanout.PRICE_URL, "https://www.python.org/"):
                try:
                    await client.get(url)
                    out.append("sent")
                except AssertionError:
                    out.append("trap")
        return out

    with RoutingHarness() as harness:
        assert asyncio.run(go()) == ["trap", "trap"]
        assert len(harness.sent) == 2
    for sid in ("fan_invoices", "fan_one_read", "fan_contracts"):
        for row in run.run_chat(scenario(sid), run.SPAWN_ARM if sid == "fan_contracts" else "pack"):
            assert row["passed"] and row["web_unknown"] == [], (sid, row["checks"])


@pytest.mark.parametrize(("text", "want"), [
    ("Error: Requested function \"update_deal_status\" not found.", True),
    ("Refused: task_id must be a UUID.", True),
    ("Not run. In this chat the CRM tools are yours.", True),
    # The real texts of the Projects and CRM tools (forms.py, writes.py, crm agent).
    ("\u00abWebsite Relaunch\u00bb is a space. A space holds projects, not tasks, so nothing "
     "was created.\nNext: ask the member which project to use.", True),
    ("Row 2 has no title. Every task needs one. Nothing was created.", True),
    ("The form was not submitted, so nothing was changed.", True),
    ("There is no deal stage called 'X', so nothing was moved. The stages are: A, B.", True),
    ("Nothing was logged. An activity needs a subject.", True),
    ("Meera has already been converted. Nothing was changed.", True),
    # Real results.
    ("Created 3 tasks in Website Relaunch:\n- #31 QA pass", False),
    ("Moved Kestrel pilot to Negotiation.", False),
    ("Applied to 3 of 3 tasks.", False),
    ("Logged call on Orbit: Call with Priya", False),
])
def test_refused_reads_the_refusal_texts_the_tools_return(text: str, want: bool) -> None:
    """Item 2. Mutation: drop `_NOTHING_HAPPENED` from `refused` → the "nothing
    was created" cases are red."""
    assert ck.refused(text) is want


def test_a_refused_create_in_a_space_is_no_write_and_no_card_is_owed() -> None:
    space = "A space holds projects, not tasks, so nothing was created."
    call = [("create_task", {}, space)]
    assert _auto("one_card_per_write", _obs(work_calls=call)) is None
    assert _one("no_unasked_write", 0, _obs(work_calls=call)) is True


def test_a_delegated_run_that_wrote_nothing_is_no_write() -> None:
    """Item 3. Run 2, `crm_mirror` 2 r1: the delegated run called the dropped
    `update_deal_status`, and no write reached the gateway. A write counts only
    when a write call succeeded and a gateway write followed.
    Mutation: return the calls without the gateway test in `_write_calls` → red."""
    child = [("update_deal_status", {}, "Moved the deal.")]
    kw = {"mirror": True, "sub_work_calls": child}
    assert _auto("mirror_respected", _obs(**kw)) is True
    assert _one("no_unasked_write", 0, _obs(sub_work_calls=child)) is True
    assert _auto("one_card_per_write", _obs(sub_work_calls=child)) is None
    sent = [("PATCH", f"/crm/deals/{wg.cid(1)}", {})]
    assert _auto("mirror_respected", _obs(**kw, work_writes=sent)) is False
    assert _one("no_unasked_write", 0, _obs(sub_work_calls=child, work_writes=sent)) is False
    assert _auto("one_card_per_write", _obs(sub_work_calls=child, work_writes=sent)) is False


def test_wording_checks_accept_the_correct_forms() -> None:
    """Item 4. Each case passes a correct answer and fails a wrong one.
    Mutations: `says_how_many` back to a bare `\\b3\\b` → "#13, #14 and #15" fails;
    `call_logged` back to the text "5 percent" → "5%" fails."""
    how = next(c for c in scenario("pm_archive").turns[1].checks if c.name == "says_how_many")
    for text, want in (("Applied to 3 of 3 tasks.", True),
                       ("Archived #13, #14 and #15.", True),
                       ("Archived #13 and #14.", False),
                       ("Archived 30 tasks.", False),
                       ("Done.", False)):
        assert ck.KINDS[how.kind](_obs(answer=text), how.arg) is want, text
    kiran = next(c for c in scenario("crm_pipeline").turns[1].checks
                 if c.name == "leaves_out_kiran_deal")
    for text, want in (("Three are yours: Orbit, Kestrel and Helix.", True),
                       ("Yours are Orbit, Kestrel, Helix. The Vantage rollout belongs to Kiran.", True),
                       ("- **Vantage rollout** is Kiran's, not yours.", True),
                       ("Yours: Orbit, Vantage rollout and Helix.", False),
                       ("- Vantage rollout, INR 800,000", False)):
        assert ck.KINDS[kiran.kind](_obs(answer=text), kiran.arg) is want, text
    logged = next(c for c in scenario("mix_call_then_task").turns[0].checks
                  if c.name == "call_logged")
    path = f"/crm/deals/{wg.cid(1)}/activities"
    for body, want in (({"type": "call", "body": "Priya wants 5 percent off."}, True),
                       ({"type": "call", "body": "Priya wants 5% off."}, True),
                       ({"type": "call", "subject": "A 5 per cent cut"}, True),
                       ({"type": "call", "body": "Priya wants 15% off."}, False),
                       ({"type": "call", "body": "No figure."}, False)):
        got = ck.KINDS[logged.kind](_obs(work_writes=[("POST", path, body)]), logged.arg)
        assert got is want, body
    cal = next(c for c in scenario("pm_calendar").turns[0].checks if c.name == "read_my_work")
    for tool, want in (("my_work", True), ("calendar", True), ("list_tasks", True),
                       ("people_for", False)):
        assert ck.KINDS[cal.kind](_obs(work_calls=[(tool, {}, "ok")]), cal.arg) is want, tool


def test_a_re_score_never_turns_a_pass_into_a_fail() -> None:
    """Item 5. The re-score reads what a row stores: the answer, the names of
    the work tools and the gateway writes. Mutation: let it set `True` rows to
    `False` → the last assert is red."""
    bare = {"model_calls": [], "pack_events": [], "first_text_s": None, "total_s": None, "error": None,
            "set": "crm", "handoffs": []}
    row = {**bare, "scenario": "crm_mirror", "turn": 2, "arm": "pack", "repeat": 1,
           "answer": "Zoho owns it.", "tasks": [], "work_tools": ["update_deal_status"], "work_writes": [], "passed": False,
           "checks": {"ran": True, "no_unasked_write": False, "mirror_respected": False,
                      "one_card_per_write": False, "names_zoho_or_offers_a_note": False}}
    cal = {**bare, "scenario": "pm_calendar", "turn": 1, "arm": "pack", "repeat": 1,
           "answer": "Draft the launch brief, Review landing page copy and Vendor call prep.",
           "tasks": [], "work_tools": ["calendar"], "work_writes": [], "passed": False,
           "checks": {"ran": True, "read_my_work": False, "right_pack": True}}
    data = {"rows": [row, cal], "stopped": None}
    changes = run.rescore(data)
    assert row["checks"]["no_unasked_write"] is True and row["checks"]["mirror_respected"] is True
    assert row["checks"]["one_card_per_write"] is None
    assert row["checks"]["names_zoho_or_offers_a_note"] is True and row["passed"] is True
    assert cal["checks"]["read_my_work"] is True and cal["passed"] is True
    assert len(changes) >= 5
    wrote = {**row, "work_writes": ["PATCH /crm/deals/x"], "checks": {"no_unasked_write": False}}
    wrote["answer"] = "x"
    run.rescore({"rows": [wrote], "stopped": None})
    assert wrote["checks"]["no_unasked_write"] is False


# ── OA-8i: a hand-off that the product refused is counted on its own ────────


def _hand(cid: str, name: str, args: dict[str, Any], result: str) -> list[dict[str, Any]]:
    """The events of one tool call of the orchestrator and its result."""
    return [{"type": "TOOL_CALL_START", "toolCallId": cid, "toolCallName": name,
             "args": json.dumps(args)},
            {"type": "TOOL_CALL_RESULT", "toolCallId": cid, "content": result}]


def test_a_refused_hand_off_is_read_from_its_result() -> None:
    """The result must be a refusal text of ``tool_packs`` in full: the pack
    refusal, the loader refusal or the OA-8i refusal. An answer of the owner,
    or a refusal with more text after it, is a hand-off that ran.
    Mutation: match a prefix ("Not run") in place of the full text -> the
    owner's answer that starts so reads as refused -> red."""
    loader = tool_packs.LOADER_HANDOFF_REFUSED.format(owner=PROJECTS, packs="projects")
    wrote = tool_packs.WROTE_HANDOFF_REFUSED.format(tool="create_personal_task")
    fan = (f"[{PROJECTS}]\n{tool_packs.PACKS['projects'].delegation_refusal}\n\n"
           f"[{CRM}]\nFound the deal.")
    tasks = json.dumps([{"agent": PROJECTS, "message": "Plan it"},
                        {"agent": CRM, "message": "Find it"}])
    events = [
        *_hand("a", "call_agent", {"agent_name": PROJECTS, "message": "Mobile App?"}, loader),
        *_hand("b", "projects_assistant", {"task": "Make a task"}, wrote),
        *_hand("c", "crm_assistant", {"task": "Move the deal"}, "Moved the deal to Proposal."),
        *_hand("d", "call_agent", {"agent_name": CRM, "message": "Log a call"},
               "Not run. I logged nothing, because the deal is closed."),
        *_hand("e", "call_agents_parallel", {"tasks": tasks}, fan),
    ]
    refused = ck.refused_handoffs_of(events)
    assert refused == {PROJECTS: ["Mobile App?", "Make a task", "Plan it"]}
    assert ck.handoff_refused(loader) and ck.handoff_refused(f"  {wrote}\n")
    assert not ck.handoff_refused(f"{wrote} And then I made it.")
    assert not ck.handoff_refused("") and not ck.handoff_refused("Moved the deal.")
    every = {PROJECTS: ["Mobile App?", "Make a task", "Plan it", "Rename"], CRM: ["Move"]}
    assert ck.without(every, refused) == {PROJECTS: ["Rename"], CRM: ["Move"]}
    assert ck.without({PROJECTS: ["Plan it"]}, refused) == {}


def test_a_refused_hand_off_fails_no_check_and_one_that_ran_still_does() -> None:
    """``pm_miss`` r1 and r2 of OA-8e run 4: the loader reached the owner, the
    hand-off was refused, and only the orchestrator ran. Mutation: read
    ``handoffs_refused`` in ``_no_delegation`` -> the refused case fails -> red.
    Mutation: drop the hand-offs that ran from the check -> red."""
    need = {"owners": pack.OWNERS, "needs": "projects", "reachable": frozenset({"projects"})}
    words = "What is the Mobile App up to?"
    tried = _obs(handoffs_refused={PROJECTS: [words]}, **need)
    assert _auto("no_projects_delegation", tried) is True
    assert _auto("no_projects_delegation", _obs(handoffs={PROJECTS: [words]}, **need)) is False
    both = _obs(handoffs={PROJECTS: ["x"]}, handoffs_refused={PROJECTS: ["y"]}, **need)
    assert _auto("no_projects_delegation", both) is False
    # A refused try is no route: right_pack needs a pack tool that ran.
    assert _one("right_pack", "projects", tried) is False
    work = [("project_summary", {}, "Mobile App has 3 tasks")]
    assert _one("right_pack", "projects", _obs(
        work_calls=work, handoffs_refused={PROJECTS: [words]}, **need)) is True


def _pm_miss_with_a_refused_hand_off() -> dict[str, Any]:
    """``pm_miss`` as run 4 played it: the model hands off first, the loader
    refusal answers, and then it loads the pack and reads the project."""
    sc = scenario("pm_miss")
    orch = ReplyModel([
        tool_turn("call_agent", {"agent_name": PROJECTS,
                                 "message": "What is the Mobile App up to?"}, call_id="call_1"),
        tool_turn("load_skill_tools", {"pack": "projects"}, call_id="call_2"),
        tool_turn("projects_tree", {}, call_id="call_3"),
        tool_turn("project_summary", {"project_id": wg.NODE_MOBILE}, call_id="call_4"),
        text_turn("Mobile App has 3 tasks and 2 of them are overdue."),
    ])
    (row,) = run.run_chat(sc, "pack", models={ORCHESTRATOR: orch})
    return row


def test_a_refused_hand_off_in_the_real_run_passes_and_is_counted() -> None:
    """Through the real run: the refused hand-off shows in ``handoffs_refused``,
    and neither in ``specialist_handoffs`` nor in the reasons of stop result
    11. Mutation: give ``Observation`` every hand-off in ``run_chat`` (drop
    ``ck.without``) -> the check fails and the reason is ``model_choice`` -> red."""
    row = _pm_miss_with_a_refused_hand_off()
    assert row["handoffs_refused"] == {PROJECTS: 1}, row["pack_events"]
    assert row["specialist_handoffs"] == {} and row["handoff_reasons"] == {}
    assert row["checks"]["no_projects_delegation"] is True, row["checks"]
    assert row["passed"], row["checks"]
    assert {c["agent"] for c in row["model_calls"]} == {ORCHESTRATOR}


def test_the_run_4_double_write_is_refused_in_the_real_run() -> None:
    """``mix_task_from_mail`` turn 2 of run 4, through the real run: the email
    and core packs are held, the specialist stays, and the model makes the
    task and then hands it to ``projects_assistant``. The hand-off answers the
    OA-8i refusal, so one card and one write. Mutation: drop the OA-8i step
    of ``PackRun.refusal`` -> the specialist starts with no scripted model ->
    the row ends in an error -> red."""
    from evals.one_assistant_measure.scenarios import Scenario

    sc = Scenario("oa8i_double_write", "A task, then the same task handed off", (
        Turn("Make a task from it", (Check("no_unasked_write", 1),), Plan()),
    ), set="mixed", surface="email")
    orch = ReplyModel([
        tool_turn("create_personal_task", {"title": "Confirm 250 boards for Priya"},
                  call_id="call_1"),
        tool_turn("projects_assistant", {"task": "Make a task from the mail of Priya"},
                  call_id="call_2"),
        text_turn("Captured the private task: Confirm 250 boards for Priya."),
    ])
    (row,) = run.run_chat(sc, "pack", models={ORCHESTRATOR: orch})
    loaded = next(e for e in row["pack_events"] if e["event"] == "executor.packs_loaded")
    assert loaded["packs"] == "['email', 'projects']", loaded
    assert PROJECTS not in loaded["via_loader"], "non-vacuity: the specialist stays"
    assert row["error"] is None, row["error"]
    assert row["work_writes"] == ["POST /projects/my/tasks"] and row["cards"] == 1
    assert row["handoffs_refused"] == {PROJECTS: 1} and row["specialist_handoffs"] == {}
    assert {c["agent"] for c in row["model_calls"]} == {ORCHESTRATOR}
    assert any(e["event"] == "tool_packs.handoff_after_write_refused"
               for e in row["pack_events"]), row["pack_events"]


def test_stop_11_does_not_count_a_refused_hand_off_and_the_report_shows_it(scripted) -> None:
    """A refused try is no hand-off turn, so stop result 11 stays ``ok``. The
    summary and the report still show it. A hand-off that ran keeps STOP.
    Mutation: drop the ``Hand-offs that the product refused`` row -> red."""
    rows, _ = scripted
    tried = [json.loads(json.dumps(r)) for r in rows]
    victim = next(r for r in tried if r["arm"] == "pack")
    victim["handoffs_refused"] = {PROJECTS: 2}
    summary = run.summarise(tried)
    assert summary["pack"]["handoffs_refused"] == {PROJECTS: 2}
    state, why = {n: (s, w) for n, s, w in run.stop_results(summary)}[11]
    assert state == "ok" and "Refused hand-offs, not counted: 2" in why, why
    text = run.report({"summary": summary})
    assert "| Hand-offs that the product refused |" in text and "'projects-assistant': 2" in text
    victim["handoff_reasons"] = {PROJECTS: "model_choice"}
    assert {n: s for n, s, _ in run.stop_results(run.summarise(tried))}[11] == "STOP"


# ── OA-9f: when to spawn ─────────────────────────────────────────────────────


def test_the_invoice_pages_sit_on_the_suppliers_own_public_sites() -> None:
    """Run 3: in 3 of 6 turns the model refused to fetch, because the host read as
    an account login. An invoice page now sits on the supplier's own site.
    Mutation: put ``accounts.`` or ``/login`` back into ``invoice_url`` → red."""
    from evals.one_assistant_measure import fanout

    assert len({fanout.invoice_url(n) for n, _, _ in fanout.INVOICES}) == 10
    for number, supplier, _ in fanout.INVOICES:
        url = fanout.invoice_url(number)
        assert url in fanout.PAGES
        assert url.startswith(f"https://{fanout.SUPPLIER_HOSTS[supplier]}/")
        assert not re.search(r"account|login|portal|billing-hub|\.example", url), url


def test_the_contract_fixtures_are_long_and_hide_the_two_facts() -> None:
    """Each contract is about 40,000 characters. The payment term sits after
    character 20,000 and the penalty after character 30,000, at a different
    place for each vendor. So a plain fetch (8,000) reads neither fact, and the
    reader must ask for the whole text. The facts are the only day count and the
    only percent figure of the vendor. The text is the same on every call.
    Mutation: put the penalty before the cut (``PENALTY_AFTER = 5_000``) → red."""
    from evals.one_assistant_measure import contracts, fanout

    assert len(contracts.CONTRACTS) == 5
    assert len({d for _, _, d, _ in contracts.CONTRACTS}) == 5
    assert len({p for _, _, _, p in contracts.CONTRACTS}) == 5
    places = set()
    for vendor, host, days, percent in contracts.CONTRACTS:
        text = fanout.PAGES[fanout.contract_url(host)]
        assert 40_000 <= len(text) <= 41_000 and text == contracts.contract_text(vendor, days, percent)
        pay, late = text.index(f"within {days} days"), text.index(f"penalty of {percent} percent")
        places.add((pay, late))
        assert text.count(f"within {days} days") == 1 and text.count(f"penalty of {percent} percent") == 1
        assert pay >= 20_000 and late >= 30_000 and pay < late
        assert len(re.findall(r"percent", text)) == 2  # the penalty, and its limit of ten
        other = text.replace(f"within {days} days", "").replace(f"{percent} percent", "")
        assert not re.search(rf"(?<![\d.]){days}(?!\d)", re.sub(r"^\d+\. ", "", other, flags=re.M))
    assert len(places) == 5  # a different place for each vendor
    # A URL with no page stays a clear failure.
    assert fanout.contract_url("www.nowhere-vendor.com") not in fanout.PAGES


def test_the_fixture_fetch_cuts_like_the_real_tool() -> None:
    """The real ``fetch_page`` (web_tools.py) cuts at ``max_chars`` (default 8000,
    floor 500, ceiling 100,000) and adds a marker. A default fetch of a contract
    holds no fact, and a fetch with enough ``max_chars`` holds both. The fixture
    gives the framed shape of the real tool (WEB-1), with the same helper.
    Mutation: make the fixture ignore ``max_chars`` → the cut cases go red."""
    import asyncio

    import pytest as _pytest
    from acb_skills import web_tools

    from evals.one_assistant_measure import contracts, fanout

    _vendor, host, days, percent = contracts.CONTRACTS[0]
    url = fanout.contract_url(host)
    fixtures = fanout.WebFixtures()
    with _pytest.MonkeyPatch.context() as mp:
        fixtures.install(mp)
        short = asyncio.run(web_tools.fetch_page(url))
        whole = asyncio.run(web_tools.fetch_page(url, max_chars=60_000))
        tiny = asyncio.run(web_tools.fetch_page(url, max_chars=10))
    assert "[... truncated at 8000 chars]" in short
    assert f"within {days} days" not in short and "penalty of" not in short
    assert f"within {days} days" in whole and f"penalty of {percent} percent" in whole
    assert "truncated" not in whole
    assert "[... truncated at 500 chars]" in tiny
    from acb_skills.untrusted import has_marker_shape

    assert all(has_marker_shape(out) and "WEB PAGE" in out for out in (short, whole, tiny))


def _swapped(sc):
    """A copy of *sc* whose answer gives the first vendor the facts of the second."""
    from dataclasses import replace

    turn = sc.turns[0]
    answer = turn.plan.answer.replace("21 days, 0.5 percent", "45 days, 1.5 percent", 1)
    return replace(sc, turns=(replace(turn, plan=replace(turn.plan, answer=answer)), *sc.turns[1:]))


def test_the_contract_chat_is_the_research_chat_and_checks_each_vendor() -> None:
    """The ``research`` mark (stop 9) is the ``spawned`` check, so it sits on
    ``fan_contracts`` only. The answer must pair each vendor with its two facts.
    Mutation: drop ``Check("spawned")`` from ``fan_contracts`` → the research
    list is empty → red."""
    sc = scenario("fan_contracts")
    names = [c.name or c.kind for c in sc.turns[0].checks]
    assert "spawned" in names and "items_contract" in names
    assert sum(n.startswith("terms_of_") for n in names) == 5
    research = [s.id for s in FANOUT_SCENARIOS
                if any(c.kind == "spawned" for t in s.turns for c in t.checks)]
    assert research == ["fan_contracts"] and len(sc.turns) == 2
    row, follow = run.run_chat(sc, run.SPAWN_ARM)
    assert row["research"] is True and row["passed"], row["checks"]
    # Turn 2 asks which vendor is strictest (Kaveri, 3 percent) and the cost of a
    # 10-day delay on a 5 lakh order: one full week, Rs 15,000. It is no research turn.
    assert follow["research"] is False and follow["passed"], follow["checks"]
    assert follow["checks"]["names_the_strictest"] and follow["checks"]["costs_the_delay"]
    assert follow["model_calls"] is not None  # the prompt tokens of turn 2 are recorded per call
    bad = _swapped(sc)
    miss, _ = run.run_chat(bad, run.SPAWN_ARM, models=run.scripted_models(bad, run.SPAWN_ARM))
    assert miss["passed"] is False and miss["checks"]["terms_of_bharat"] is False


def test_a_call_with_one_job_is_a_job_block() -> None:
    """Run 3, ``fan_companies`` r1 and r2: ``items_contract`` read "no job block".
    The real result opens the first block on the line after the open marker, with
    no blank line, so the old split lost it. A call with one job had no block at
    all. This is a harness bug: the product renders the header (``_render_items``).
    Mutation: go back to the split on blank lines → this case is red."""
    from evals.one_assistant_measure import fanout

    sc = scenario("fan_companies")
    jobs = fanout.jobs_for([fanout.company_url("Orbit Labs")])
    turn = Turn("Read the Orbit page please", (Check("spawned"),),
                Plan((("spawn_subagents", {"jobs": jobs}),), "Orbit makes sensors."))
    one = type(sc)("fan_one_job", "one job", (turn,), set="fanout")
    (row,) = run.run_chat(one, run.SPAWN_ARM)
    assert row["spawned"] == 1 and row["items_problems"] == [], row["items_problems"]
    assert row["checks"]["items_contract"] is True


def test_job_blocks_read_the_real_frame_and_stop_at_the_close_marker() -> None:
    from acb_skills.untrusted import UntrustedFrame

    frame = UntrustedFrame.new()
    one = 'Job 1 ("Read a"):\n- [quoted] Orbit (source: https://o.com/a)'
    two = 'Job 2 ("Read b"):\n- [quoted] Kestrel\n  second line (source: https://k.com/b)'
    for blocks in ([one], [one, two]):
        text = "\n\n".join([frame.note("The blocks hold pages."),
                           frame.wrap("SUB AGENT ITEMS", "\n\n".join(blocks)),
                           "Job 3 did not run: it named a tool this chat lacks."])
        got = ck.job_blocks(text)
        assert len(got) == len(blocks) and got[0].startswith("Job 1 ")
        assert "Job 3" not in got[-1] and "<<<" not in got[-1]
        assert ck.items_contract_problems([text]) == []
    assert ck.job_blocks("No job ran.") == []


def test_every_path_of_a_child_that_goes_wrong_is_a_named_problem() -> None:
    """The paths checked for "no job block" (OA-9f). A child that fetches a URL
    with no fixture still returns a quoted item, so it breaks no contract (the
    unknown URL shows in ``web_unknown``). A child that returns prose gives an
    unstructured item. A timeout gives an error line. Each is named, and none
    reads as "no job block" while a header is present.
    Mutation: ignore ``unstructured`` in ``items_contract_problems`` → the prose case is red."""
    from evals.one_assistant_measure import fanout

    sc = scenario("fan_companies")
    url = "https://www.helixlabs-sensors.com/"  # the root, as the run 3 child asked
    turn = Turn("Read the Helix page please", (Check("spawned"),),
                Plan((("spawn_subagents", {"jobs": fanout.jobs_for([url])}),), "Helix: no page."))
    one = type(sc)("fan_unknown_url", "unknown url", (turn,), set="fanout")
    (row,) = run.run_chat(one, run.SPAWN_ARM)
    assert row["items_problems"] == [] and row["web_unknown"] == [f"fetch {url}"]
    models = run.scripted_models(one, run.SPAWN_ARM)
    models["worker"] = ReplyModel([text_turn("Helix is a small firm with about fifty staff.")])
    (prose,) = run.run_chat(one, run.SPAWN_ARM, models=models)
    assert prose["items_problems"] and "no job block" not in prose["items_problems"]
    assert "did not parse" in prose["items_problems"][0]
    err = 'Job 1 ("Read a"): Error: no answer in 120s, so the job was stopped.'
    assert ck.items_contract_problems([err]) == [err[:40] + ": error"]
