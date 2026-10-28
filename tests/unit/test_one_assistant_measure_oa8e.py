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
* The fan-out chats are listed and skipped until ``spawn_subagents`` exists.

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
from evals.routing.harness import ORCHESTRATOR

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
    assert {r["arm"] for r in rows} == {"delegation", "pack"}


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
            continue  # a research job is long by nature, and it is skipped today
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
    others hold none. A change to ``_PROJECTS_WORDS`` shows here."""
    by_word = {"pm_stuck": "projects-plan", "pm_tags": "projects-structure",
               "pm_archive": "projects-bulk", "crm_open_deals": "crm-records"}
    for sid, slug in by_word.items():
        words = [t.words for t in scenario(sid).turns]
        assert any(tool_packs.matched_layer(slug, w) == "words" for w in words), sid
    for sid, slug in {"pm_capacity": "projects-plan", "pm_status": "projects-structure",
                      "pm_new_tasks": "projects-bulk", "crm_miss": "crm-records",
                      "pm_miss": "projects"}.items():
        assert tool_packs.matched_layer(slug, scenario(sid).turns[0].words) is None, sid


# ── the fan-out chats wait for OA-9b ─────────────────────────────────────────


def test_the_fanout_chats_are_listed_and_skipped_until_spawn_exists(capsys) -> None:
    """Mutation: clear ``skip`` of one fan-out chat → it runs, the scripted plan
    is empty, and this test is red. When OA-9b merges, ``spawn_built`` reads True
    and ``test_spawn_subagents_is_not_built`` fails: build the arm then."""
    assert len(FANOUT_SCENARIOS) == 3 and all(s.skip for s in FANOUT_SCENARIOS)
    assert all(s.set == "fanout" for s in FANOUT_SCENARIOS)
    assert not any(s.skip for s in WORK_SCENARIOS if s.set != "fanout")
    assert run.runnable(tuple(FANOUT_SCENARIOS)) == ()
    code = run.main(["--scripted", "--scenarios", "fan_companies,fan_tasks,fan_invoices",
                     "--arms", f"delegation,pack,{run.SPAWN_ARM}"])
    out = capsys.readouterr().out
    assert code == 0 and out.count("SKIP: fan_") == 3 and f"SKIP: the arm {run.SPAWN_ARM}" in out


def test_spawn_subagents_is_not_built() -> None:
    assert run.spawn_built() is False, (
        "spawn_subagents exists: build the packs+spawn arm and the fan-out plans (OA-8e phase 2)")


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
    # Tasks page: the core Projects pack stands in the request (81 tools at the
    # base of this harness). The email pack and the plan and structure packs do
    # not fit beside it, so their specialists stay.
    ("pm_find_task", {CRM}, {CRM}, {EA, PROJECTS}),
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
    for sid, slug in (("crm_miss", "crm-records"), ("pm_miss", "projects"),
                      ("pm_capacity_open", "projects-plan")):
        sc = scenario(sid)
        models = run.scripted_models(sc, "pack")
        (row,) = run.run_chat(sc, "pack", models=models)
        assert row["loaded_by_model"] == [slug], (sid, row["loaded_by_model"])
        assert row["passed"], (sid, row["checks"])
        assert row["pack_events"] and any(
            e["event"] == "tool_packs.loaded" for e in row["pack_events"]), sid


def test_a_pack_that_does_not_fit_sends_the_route_to_the_owner() -> None:
    """On the Projects page the core pack stands in the request, and the plan
    and structure packs do not fit under the cap of 110 tools. The loader does
    not offer them, so the specialist stays and the hand-off is legal. This is
    the finding of the first scripted run: it is a gap of the packs, and the
    live run reports its cost. Mutation: raise ``MAX_REQUEST_TOOLS`` → the
    pack loads and the plan's hand-off is wrong → red."""
    for sid, slug in (("pm_capacity", "projects-plan"), ("pm_status", "projects-structure")):
        (row,) = run.run_chat(scenario(sid), "pack")
        assert row["specialist_handoffs"] == {PROJECTS: 1}, (sid, row["specialist_handoffs"])
        assert row["checks"]["right_pack"] is True
        assert row["checks"]["no_projects_delegation"] is True, row["checks"]
        plan = next(e for e in row["pack_events"] if e["event"] == "executor.pack_plan")
        assert slug not in plan["offered"], plan


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
    or converts a lead, and ``log_activity`` stays. Mutation: set the fake's mode
    to ``native`` for this chat → the tools stay → red."""
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
    assert _one("no_unasked_write", 0, _obs(work_calls=write)) is False
    assert _one("no_unasked_write", 0, _obs(work_writes=sent)) is False
    assert _one("no_unasked_write", 1, _obs(work_calls=write, work_writes=sent)) is True
    two = [("update_deal_status", {}, "a"), ("log_activity", {}, "b")]
    assert _one("no_unasked_write", 1, _obs(work_calls=two)) is False
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
    assert _one("right_pack", "crm-records", _obs(handoffs={CRM: ["x"]})) is True
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
    assert _auto("no_crm_delegation", _obs(owners=pack.OWNERS)) is True
    assert _auto("no_crm_delegation", _obs(arm="delegation", **kw)) is None
    assert _auto("no_crm_delegation", _obs(handoffs={CRM: ["x"]})) is None
    # A hand-off to the Projects assistant is not a CRM hand-off.
    assert _auto("no_crm_delegation", _obs(owners=pack.OWNERS, handoffs={PROJECTS: ["x"]})) is True


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
    assert _auto("no_projects_delegation", _obs(owners=pack.OWNERS)) is True
    assert _auto("no_projects_delegation", _obs(arm="delegation", **base)) is None


def test_mirror_respected_fails_a_record_write() -> None:
    """Mutation: allow ``update_deal_status`` in ``MIRROR_TOOLS`` → red."""
    mirror = {"mirror": True}
    assert _auto("mirror_respected", _obs()) is None
    assert _auto("mirror_respected", _obs(**mirror)) is True
    for name in ("create_lead", "update_deal_status", "convert_lead"):
        assert _auto("mirror_respected", _obs(**mirror, work_calls=[(name, {}, "x")])) is False
    for write in (("POST", "/crm/leads", {}), ("PATCH", f"/crm/deals/{wg.cid(1)}", {}),
                  ("POST", f"/crm/leads/{wg.cid(21)}/convert", {})):
        assert _auto("mirror_respected", _obs(**mirror, work_writes=[write])) is False
    # A logged note saves in Metorite only, and stays legal.
    note = [("POST", f"/crm/deals/{wg.cid(1)}/activities", {})]
    assert _auto("mirror_respected", _obs(
        **mirror, work_calls=[("log_activity", {}, "x")], work_writes=note)) is True


def test_one_card_per_write_needs_a_card_for_each_write_tool() -> None:
    """Mutation: drop the lower bound → a write with no card passes → red."""
    write = [("update_deal_status", {}, "Moved")]
    card = [{"title": "Move this deal?"}]
    assert _auto("one_card_per_write", _obs()) is None
    assert _auto("one_card_per_write", _obs(work_calls=[("get_pipeline", {}, "ok")])) is None
    assert _auto("one_card_per_write", _obs(work_calls=write, cards=card)) is True
    assert _auto("one_card_per_write", _obs(work_calls=write)) is False
    assert _auto("one_card_per_write", _obs(work_calls=write, cards=card * 2)) is False
    both = [*write, ("log_activity", {}, "ok")]
    assert _auto("one_card_per_write", _obs(work_calls=both, cards=card * 2)) is True


def test_the_request_may_not_pass_the_cap() -> None:
    """Mutation: compare with ``<`` and the cap itself fails → red."""
    cap = tool_packs.MAX_REQUEST_TOOLS
    assert _auto("tools_per_call", _obs(max_tools=cap)) is True
    assert _auto("tools_per_call", _obs(max_tools=cap + 1)) is False
    assert _auto("tools_per_call", _obs()) is None


def test_spawned_and_not_spawned() -> None:
    assert _one("spawned", None, _obs(spawned=2)) is True
    assert _one("spawned", None, _obs()) is False
    assert _one("not_spawned", None, _obs()) is True
    assert _one("not_spawned", None, _obs(spawned=1)) is False


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
    assert packs["by_set"]["crm"]["turns"] == 12 and packs["layer_b_turns"] >= 4
    assert packs["tools_per_call_max"] <= tool_packs.MAX_REQUEST_TOOLS
    assert packs["layer_a_ms_mean"] is not None and deleg["layer_a_ms_mean"] is None
    assert packs["specialist_handoffs"] == {PROJECTS: 2}
    assert deleg["specialist_handoffs"][CRM] >= 10 and packs["guard_failures"] == []
    text = run.report({"summary": summary})
    assert "Stop result of" in text and "OA-8e" in text and "| crm | pack |" in text
    states = {n: state for n, state, _ in run.stop_results(summary)}
    assert states[1] == "ok" and states[2] == "ok" and states[9] == "n/a"


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
    assert run.reachable_of([
        {"event": "executor.pack_plan", "offered": "['email-setup', 'projects-bulk']",
         "packs": "['projects']"},
        {"event": "tool_packs.loaded", "pack": "crm-records"}]) == frozenset(
            {"email-setup", "projects-bulk", "projects", "crm-records"})
