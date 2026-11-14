"""The fence of the Studio chats and the checks of the ``search`` arm (WS-67 TS-5a, R7).

``evals/one_assistant_measure`` gets five Studio chats and six pure checks. The
spec is ``project-docs/specs/one_assistant_context.md`` §4.2.9 (TS-5a, TS-5b) and
§4.3.3. This file holds the harness, never a live run: nothing here calls a model
or spends money.

* Each new check has a case that passes, a case that fails, and a case that is the
  named MUTATION of it (the line that a mutation of the check would flip).
* The Studio chats run on the ``pack`` arm through the real orchestrator run. The
  ``search`` arm prints SKIP, because the settings hold no ``tool_search_orgs``.
* No word of any pack matches a Studio chat, so layer (a) never loads ``studio``.
* ``studio-workflows`` stays OFF in the flag of every arm.

Run::

    uv run python -m pytest tests/unit/test_one_assistant_measure_search.py -q -p no:xdist
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import httpx
import pytest

pytest.importorskip("agent_framework", reason="MAF not installed")

from acb_skills import tool_packs
from acb_skills.tier_policy import SHORT_MESSAGE_WORDS

from evals.one_assistant_measure import checks as ck
from evals.one_assistant_measure import pack, run
from evals.one_assistant_measure import workgateway as wg
from evals.one_assistant_measure.scenarios import all_scenarios, scenario
from evals.one_assistant_measure.scenarios_studio import STUDIO_SCENARIOS
from evals.one_assistant_measure.scenarios_work import WORK_SCENARIOS
from evals.routing import dataset as ds

SEARCH, PACK = ck.SEARCH_ARM, "pack"
STUDIO_IDS = ("stu_skills", "stu_new_app", "stu_plain", "stu_no_feature", "stu_held")
NOT_FOUND = 'Error: Requested function "studio_skill_list" not found'
OK_ANSWER = '{"ok": true, "skills": []}'


@pytest.fixture(scope="module")
def scripted() -> tuple[list[dict[str, Any]], list[str]]:
    """The five Studio chats on the pack arm (and the search arm, which drops out)."""
    return run.run_scripted(STUDIO_SCENARIOS, (PACK, SEARCH))


def _obs(
    arm: str = SEARCH, trace: list[tuple[str, dict[str, Any], str]] | None = None, **kw: Any
) -> ck.Observation:
    return ck.Observation(arm=arm, trace=trace or [], **kw)


def _find(result: str = "found 1 tool") -> tuple[str, dict[str, Any], str]:
    return (ck.FIND_TOOL, {"query": "saved skills"}, result)


def _studio(
    result: str = OK_ANSWER, name: str = "studio_skill_list"
) -> tuple[str, dict[str, Any], str]:
    return (name, {}, result)


# ── the chats ────────────────────────────────────────────────────────────────


def test_the_five_studio_chats_are_in_the_set_and_in_no_work_set() -> None:
    assert tuple(s.id for s in STUDIO_SCENARIOS) == STUDIO_IDS
    assert not {s.id for s in STUDIO_SCENARIOS} & {s.id for s in WORK_SCENARIOS}
    assert all(s.set == "studio" for s in STUDIO_SCENARIOS)
    ids = [s.id for s in all_scenarios()]
    assert len(ids) == len(set(ids)) and set(STUDIO_IDS) <= set(ids)


def test_every_studio_chat_is_short() -> None:
    """Under 12 words, so no turn-kind request goes out. Mutation: a 12-word chat → red."""
    for sc in STUDIO_SCENARIOS:
        for turn in sc.turns:
            assert len(turn.words.split()) < SHORT_MESSAGE_WORDS, (sc.id, turn.words)


def test_the_chats_match_the_spec() -> None:
    by = {s.id: s for s in STUDIO_SCENARIOS}
    assert by["stu_skills"].surface == "crm"
    assert by["stu_new_app"].surface == ""
    assert by["stu_plain"].surface == "crm"
    assert by["stu_no_feature"].without_features == ("studio",)
    assert by["stu_no_feature"].turns[0].words == by["stu_skills"].turns[0].words
    held = by["stu_held"].turns
    assert len(held) == 2 and held[1].words == by["stu_skills"].turns[0].words
    assert any(c.kind == "found_tool" for c in held[1].checks)
    assert any(c.kind == "held_note" for c in held[1].checks)
    assert {c.kind for c in by["stu_no_feature"].turns[0].checks} >= {
        "no_studio_names",
        "answer_none",
    }
    assert any(c.kind == "not_searched" for c in by["stu_plain"].turns[0].checks)
    assert not any(c.kind == "ran_tool" for c in by["stu_plain"].turns[0].checks)


def test_no_word_of_any_pack_matches_a_studio_chat() -> None:
    """The proof that layer (a) loads no pack by the words of a Studio chat. The
    pack arm then needs ``load_skill_tools``, and the search arm needs ``find_tools``.
    Mutation: put "ai studio" in a chat → ``matched_layer("studio", ...)`` is "words" → red."""
    hits = [
        (sc.id, slug, tool_packs.matched_layer(slug, turn.words))
        for sc in STUDIO_SCENARIOS
        for turn in sc.turns
        for slug in tool_packs.PACKS
        if tool_packs.matched_layer(slug, turn.words)
    ]
    assert hits == [], hits


def test_the_proof_can_fail() -> None:
    """The control of the proof above: a pack word DOES match, so the test is no void."""
    assert tool_packs.matched_layer("studio", "Open ai studio for me") == "words"
    assert tool_packs.matched_layer("studio", "Which of my saved skills are still drafts?") is None


def test_no_studio_chat_uses_list_workflows() -> None:
    """``list_workflows`` reads the local database in process (``workflow_tools.py``)."""
    for sc in STUDIO_SCENARIOS:
        for turn in sc.turns:
            assert "list_workflows" not in {name for name, _ in turn.plan.calls}


def test_the_studio_chats_run_on_pack_and_search_only(monkeypatch: pytest.MonkeyPatch) -> None:
    sc = scenario("stu_skills")
    monkeypatch.setattr(run, "search_built", lambda: True)
    assert run.arms_for(sc, run.ALL_ARMS) == ("pack", SEARCH)
    monkeypatch.setattr(run, "search_built", lambda: False)
    assert run.arms_for(sc, run.ALL_ARMS) == ("pack",)
    assert run.arms_for(scenario("crm_pipeline"), run.ALL_ARMS) == ("delegation", "pack")


# ── the scripted run ─────────────────────────────────────────────────────────


def test_every_studio_turn_passes_on_the_pack_arm(scripted) -> None:
    """The real orchestrator run, the real Studio tools and the fake gateway. Mutations
    (each one runs red, see the report): a Studio tool mapped to email, no
    ``studio_orgs``, ``studio-workflows`` on, a Studio route with no fixture."""
    rows, problems = scripted
    assert problems == [], problems
    assert {r["arm"] for r in rows} == {PACK}
    assert len(rows) == sum(len(s.turns) for s in STUDIO_SCENARIOS)


def test_the_studio_tools_ran_in_the_orchestrators_own_run(scripted) -> None:
    rows, _ = scripted
    ran = {(r["scenario"], r["turn"]): r["work_tools"] for r in rows}
    assert ran[("stu_skills", 1)] == ["studio_skill_list"]
    assert ran[("stu_new_app", 1)] == ["studio_draft"]
    assert ran[("stu_plain", 1)] == []
    assert ran[("stu_no_feature", 1)] == ["studio_skill_list"]  # tried, and refused
    assert ran[("stu_held", 2)] == ["studio_skill_list"]
    assert all(r["checks"]["ran"] for r in rows)


def test_the_pack_arm_loads_studio_by_the_model(scripted) -> None:
    """Layer (a) did not load it: the model called ``load_skill_tools`` (layer b)."""
    rows, _ = scripted
    loaded = {(r["scenario"], r["turn"]): r["loaded_by_model"] for r in rows}
    assert "studio" in loaded[("stu_skills", 1)]
    assert "studio" in loaded[("stu_new_app", 1)]
    assert loaded[("stu_plain", 1)] == []
    assert "studio" in loaded[("stu_held", 2)]


def test_the_rows_hold_the_numbers_of_the_search_arm(scripted) -> None:
    rows, _ = scripted
    for r in rows:
        assert r["searches"] == 0  # no find_tools call exists on the pack arm
        assert r["schema_tokens"] > 0
        assert all(c["schema_tokens"] > 0 for c in r["model_calls"] if c["agent"] == "orchestrator")
    skills = next(r for r in rows if r["scenario"] == "stu_skills")
    assert skills["found_tools"] == ["studio_skill_list"]


def test_the_held_chat_is_held_and_the_pack_arm_reads_no_note(scripted) -> None:
    rows, _ = scripted
    turn2 = next(r for r in rows if r["scenario"] == "stu_held" and r["turn"] == 2)
    turn1 = next(r for r in rows if r["scenario"] == "stu_held" and r["turn"] == 1)
    assert turn1["held"] is False and turn2["held"] is True
    assert turn2["checks"]["held_note"] is None  # the pack arm: the note is in the persona
    assert turn2["checks"]["found_tool"] is True
    plain = next(r for r in rows if r["scenario"] == "stu_skills")
    assert plain["held"] is False and plain["checks"].get("held_note") is None


def test_a_member_without_the_feature_sees_no_studio_name(scripted) -> None:
    rows, _ = scripted
    row = next(r for r in rows if r["scenario"] == "stu_no_feature")
    assert row["checks"]["no_studio_names"] is True
    assert row["checks"]["lists_no_skill"] is True
    assert row["checks"]["no_studio_ran"] is True
    # the model TRIED: the load and the call are in the run, and the product refused both
    assert row["work_tools"] == ["studio_skill_list"] and "studio" in row["loaded_by_model"]


def test_the_search_only_checks_are_none_on_the_pack_arm(scripted) -> None:
    rows, _ = scripted
    for r in rows:
        assert r["checks"].get("search_round") is None
        assert r["checks"]["schema_tokens"] is None
    skills = next(r for r in rows if r["scenario"] == "stu_skills")
    assert skills["checks"]["search_round"] is None and skills["checks"]["schema_tokens"] is None


# ── the fake gateway answers /studio ─────────────────────────────────────────


def _ask(gw: wg.WorkGateway, method: str, path: str) -> httpx.Response:
    return gw.answer(httpx.Request(method, f"http://localhost:8080{path}"), path)


def test_the_work_gateway_answers_the_studio_read_routes() -> None:
    gw = wg.WorkGateway()
    skills = _ask(gw, "GET", "/studio/skills").json()["skills"]
    assert [s["state"] for s in skills] == ["draft", "published", "draft"]
    one = _ask(gw, "GET", f"/studio/skills/{wg.STUDIO_SKILLS[0]['id']}").json()
    assert one["skill"]["name"] == "Weekly digest writer" and one["version"]["body"]
    assert _ask(gw, "GET", "/studio/packages").json()["packages"]
    assert _ask(gw, "GET", "/studio/templates").json()["templates"]
    assert _ask(gw, "GET", "/studio/actions").json()["actions"]
    assert gw.unknown == []


def test_a_studio_route_with_no_fixture_answers_404_and_is_kept() -> None:
    """The fixture check. Mutation: a route with no fixture that still passes → this
    test and ``test_a_missing_fixture_fails_the_scripted_run`` go red."""
    gw = wg.WorkGateway()
    assert _ask(gw, "GET", "/studio/nothing").status_code == 404
    assert _ask(gw, "POST", "/studio/skills").status_code == 404  # no Studio chat writes
    assert gw.unknown == ["GET /studio/nothing", "POST /studio/skills"]
    assert _ask(gw, "GET", f"/studio/skills/{wg.sid(99)}").status_code == 404


def test_the_gateway_installs_for_studio_paths() -> None:
    source = Path(wg.__file__).read_text(encoding="utf-8")
    assert '"/studio/"' in source


def test_a_missing_fixture_fails_the_scripted_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no fixture for ``GET /studio/skills`` the tool reads 404, the route shows in
    ``unknown_routes`` and ``scripted_problems`` names it."""
    real = wg.WorkGateway._studio

    def without_skills(method: str, path: str, params: dict[str, Any]) -> Any:
        return None if path == "/studio/skills" else real(method, path, params)

    monkeypatch.setattr(wg.WorkGateway, "_studio", staticmethod(without_skills))
    rows, problems = run.run_scripted((scenario("stu_skills"),), (PACK,))
    assert any("has no fixture" in p and "/studio/skills" in p for p in problems), problems
    assert rows[0]["unknown_routes"] == ["GET /studio/skills"]


# ── the owner of a Studio tool ───────────────────────────────────────────────


def test_a_studio_tool_belongs_to_the_studio_builder() -> None:
    """Mutation: a Studio tool mapped to email → red."""
    names = pack.studio_tool_names()
    assert {"studio_skill_list", "studio_draft", "studio_workflow_publish"} <= names
    assert len(names) == 25
    for name in names:
        assert run.owner_of_tool(name) == pack.STUDIO, name
    assert run.owner_of_tool("read_email") == pack.EA
    assert run.owner_of_tool("get_pipeline") == pack.CRM
    assert run.owner_of_tool("list_tasks") == pack.PROJECTS


def test_the_studio_names_come_from_the_skill_families() -> None:
    from acb_skills.skill_families import SKILL_FAMILIES

    assert pack.studio_tool_names() == frozenset(SKILL_FAMILIES["studio"]["tools"]) | frozenset(
        SKILL_FAMILIES["studio-workflows"]["tools"]
    )
    assert pack.STUDIO not in pack.OWNERS  # no specialist, no config.json


# ── the flag of the arms ─────────────────────────────────────────────────────


def _settings(monkeypatch: pytest.MonkeyPatch, flag: dict[str, Any]) -> None:
    from acb_common import get_settings

    settings = get_settings()
    for name, value in flag.items():
        if name in type(settings).model_fields:  # tool_search_orgs does not exist yet (TS-2)
            monkeypatch.setattr(settings, name, value, raising=False)


@pytest.mark.parametrize("arm", [PACK, SEARCH])
def test_both_pack_arms_get_studio_by_name_and_the_org(
    arm: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutations: no ``studio_orgs`` → red here and in the scripted run. ``studio-workflows``
    in ``STUDIO_PACKS`` → red in ``test_studio_workflows_stays_off``."""
    flag = run.flag_for(scenario("stu_skills"), arm)
    assert flag["studio_orgs"] == ds.ORG
    assert flag["tool_packs_orgs"] == ds.ORG
    slugs = flag["tool_packs"].split(",")
    assert "studio" in slugs
    assert flag.get("tool_search_orgs") == (ds.ORG if arm == SEARCH else None)
    _settings(monkeypatch, flag)
    assert "studio" in tool_packs.packs_on(ds.ORG)


@pytest.mark.parametrize("arm", [PACK, SEARCH])
def test_studio_workflows_stays_off(arm: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """``owner_packs("studio-builder")`` would also turn on the DARK pack. The flag names
    ``studio`` alone. Mutation: ``STUDIO_PACKS = ("studio", "studio-workflows")`` → red."""
    assert "studio-workflows" in tool_packs.owner_packs(pack.STUDIO)  # the trap is real
    flag = run.flag_for(scenario("stu_skills"), arm)
    assert "studio-workflows" not in flag["tool_packs"].split(",")
    assert "studio-workflows" not in run.STUDIO_PACKS
    _settings(monkeypatch, flag)
    assert "studio-workflows" not in tool_packs.packs_on(ds.ORG)


def test_other_chats_keep_their_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only a Studio chat adds ``studio``: the CRM and Projects numbers stay comparable."""
    flag = run.flag_for(scenario("crm_pipeline"), PACK)
    assert "studio" not in flag["tool_packs"].split(",") and "studio_orgs" not in flag
    assert run.flag_for(scenario("crm_pipeline"), "delegation") == {}
    assert run.flag_for(scenario("stu_skills"), "delegation") == {}


# ── the arm is listed, and skips ─────────────────────────────────────────────


def test_search_is_a_listed_arm() -> None:
    assert run.SEARCH_ARM == "search" and run.SEARCH_ARM in run.ALL_ARMS
    assert run.SEARCH_ARM in run.PACK_ARMS


def test_the_search_arm_waits_for_the_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    """``search_built`` reads the settings with ``getattr``, so it needs no import of the
    field. The settings of today hold none. A stand-in that holds one reads as built."""
    from types import SimpleNamespace

    assert run.search_built() is False  # main holds no tool_search_orgs
    monkeypatch.setattr("acb_common.get_settings", lambda: SimpleNamespace())
    assert run.search_built() is False
    monkeypatch.setattr("acb_common.get_settings", lambda: SimpleNamespace(tool_search_orgs=""))
    assert run.search_built() is True


def test_arms_pack_search_prints_skip_and_runs_the_pack_arm(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen: list[tuple[str, ...]] = []

    def fake(scenarios: Any, arms: tuple[str, ...], tier_map: Any) -> tuple[list, list]:
        seen.append(arms)
        return [], []

    monkeypatch.setattr(run, "search_built", lambda: False)
    monkeypatch.setattr(run, "run_scripted", fake)
    code = run.main(["--scripted", "--arms", "pack,search", "--scenarios", "stu_skills"])
    out = capsys.readouterr().out
    assert code == run.EXIT_PASS
    assert "SKIP: arm search" in out
    assert seen == [(PACK,)]


def test_the_search_arm_does_not_skip_when_built(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen: list[tuple[str, ...]] = []
    monkeypatch.setattr(run, "search_built", lambda: True)
    monkeypatch.setattr(run, "run_scripted", lambda s, arms, tm: (seen.append(arms), ([], []))[1])
    run.main(["--scripted", "--arms", "pack,search", "--scenarios", "stu_skills"])
    assert "SKIP: arm search" not in capsys.readouterr().out
    assert seen == [(PACK, SEARCH)]


def test_the_scripted_plan_of_the_search_arm_is_not_built() -> None:
    with pytest.raises(NotImplementedError, match="TS-5b"):
        run.scripted_models(scenario("stu_skills"), SEARCH)


def test_an_unknown_arm_exits_with_status_2(capsys: pytest.CaptureFixture[str]) -> None:
    """Before TS-5a an unknown value dropped silently. Mutation: drop it again → red."""
    code = run.main(["--scripted", "--arms", "pack,serach"])
    out = capsys.readouterr().out
    assert code == run.EXIT_NO_GO == 2
    assert "serach" in out and "no such arm" in out


def test_the_default_arms_run_no_search_arm_row(scripted) -> None:
    rows, _ = scripted
    assert SEARCH not in {r["arm"] for r in rows}


# ── found_tool ───────────────────────────────────────────────────────────────


def test_found_tool_passes_after_a_find() -> None:
    assert ck._found_tool(_obs(trace=[_find(), _studio()]), None) is True


def test_found_tool_passes_after_a_load_on_the_pack_arm() -> None:
    load = (ck.LOAD_TOOL, {"pack": "studio"}, "loaded")
    assert ck._found_tool(_obs(PACK, [load, _studio()]), None) is True


def test_found_tool_fails_with_no_find_or_a_tool_before_it() -> None:
    assert ck._found_tool(_obs(trace=[_studio()]), None) is False
    assert ck._found_tool(_obs(trace=[_studio(), _find()]), None) is False
    assert ck._found_tool(_obs(trace=[_find()]), None) is False
    assert ck._found_tool(_obs(trace=[_find(), ("read_email", {}, "x")]), None) is False


def test_found_tool_does_not_pass_on_an_error_answer() -> None:
    """Mutation: drop the ``refused`` test in ``_found_tool`` so that a call that answers
    ``Requested function "X" not found`` counts as a run → this test goes red."""
    assert ck._found_tool(_obs(trace=[_find(), _studio(NOT_FOUND)]), None) is False
    assert (
        ck._found_tool(_obs(trace=[_find(), _studio("Error: Requested function not found")]), None)
        is False
    )
    assert ck._found_tool(_obs(PACK, [(ck.LOAD_TOOL, {}, "x"), _studio(NOT_FOUND)]), None) is False
    # the plain form of MAF, with no "Error:" and no "Requested" (a run with no STUDIO_ORGS)
    plain = 'Function "studio_skill_list" not found.'
    assert ck._found_tool(_obs(PACK, [(ck.LOAD_TOOL, {}, "x"), _studio(plain)]), None) is False
    quoted = "'Function \"studio_skill_list\" not found.'"  # the result text carries the quotes
    assert ck._found_tool(_obs(PACK, [(ck.LOAD_TOOL, {}, "x"), _studio(quoted)]), None) is False
    assert ck.refused(plain) and ck.refused(quoted) and ck.refused(NOT_FOUND)
    assert not ck.refused(OK_ANSWER)


def test_found_tool_takes_a_tool_list_and_gives_none_on_delegation() -> None:
    trace = [_find(), _studio()]
    assert ck._found_tool(_obs(trace=trace), ("studio_skill_list",)) is True
    assert ck._found_tool(_obs(trace=trace), ("studio_draft",)) is False
    assert ck._found_tool(_obs("delegation", trace), None) is None


# ── search_round ─────────────────────────────────────────────────────────────


def test_search_round_passes_at_exactly_one() -> None:
    assert ck._search_round(_obs(trace=[_find(), _studio()]), None) is True


def test_search_round_fails_at_zero_and_at_two() -> None:
    """Mutation: ``>= 1`` in place of ``== 1`` → the two-round case goes red. ``<= 1`` →
    the zero case goes red."""
    assert ck._search_round(_obs(trace=[]), None) is False
    assert ck._search_round(_obs(trace=[_studio()]), None) is False
    assert ck._search_round(_obs(trace=[_find(), _find(), _studio()]), None) is False


def test_a_refused_search_is_no_round_and_the_pack_arm_gives_none() -> None:
    refused = _find("Refused: a turn makes at most 3 searches.")
    assert ck._search_round(_obs(trace=[_find(), refused]), None) is True
    assert ck._search_round(_obs(trace=[refused]), None) is False
    assert ck._search_round(_obs(PACK, [_find()]), None) is None


# ── not_searched ─────────────────────────────────────────────────────────────


def test_not_searched_passes_with_no_find() -> None:
    assert ck._not_searched(_obs(trace=[]), None) is True
    assert ck._not_searched(_obs(PACK, []), None) is True


def test_not_searched_fails_on_a_find_or_a_load() -> None:
    """Mutation: make the check always True → both cases go red."""
    assert ck._not_searched(_obs(trace=[_find()]), None) is False
    assert ck._not_searched(_obs(PACK, [(ck.LOAD_TOOL, {"pack": "studio"}, "x")]), None) is False
    # a load is no search on the search arm, and a find is no load on the pack arm
    assert ck._not_searched(_obs(trace=[(ck.LOAD_TOOL, {}, "x")]), None) is True
    assert ck._not_searched(_obs(PACK, [_find()]), None) is True
    assert ck._not_searched(_obs("delegation", [_find()]), None) is None


# ── no_studio_ran ──


def test_no_studio_ran_passes_on_a_refused_try_and_fails_on_a_run() -> None:
    """Mutation: drop the ``refused`` test, or make it always True → red."""
    tried = [(ck.LOAD_TOOL, {"pack": "studio"}, "Not loaded."), _studio('Function "studio_skill_list" not found.')]
    assert ck._no_studio_ran(_obs(PACK, tried), None) is True
    assert ck._no_studio_ran(_obs(PACK, [*tried[:1], _studio()]), None) is False
    assert ck._no_studio_ran(_obs(PACK, []), None) is True
    assert ck._no_studio_ran(_obs("delegation", [_studio()]), None) is None


# ── no_studio_names ──────────────────────────────────────────────────────────

_NAMES = frozenset({"web_search", "load_skill_tools", ck.FIND_TOOL})


def test_no_studio_names_passes_with_none_anywhere() -> None:
    obs = _obs(
        answer="I cannot read saved skills here.",
        tool_names=_NAMES,
        find_description="Find a tool by what it does.",
        trace=[_find("no match")],
    )
    assert ck._no_studio_names(obs, None) is True


def test_no_studio_names_fails_on_a_name_in_any_place() -> None:
    """Mutation: check the tool list only → the three text cases go red."""
    assert ck._no_studio_names(_obs(tool_names=_NAMES | {"studio_draft"}), None) is False
    assert (
        ck._no_studio_names(
            _obs(tool_names=_NAMES, find_description="Finds studio_skill_list and more."), None
        )
        is False
    )
    assert (
        ck._no_studio_names(_obs(tool_names=_NAMES, answer="Use studio_draft to make one."), None)
        is False
    )
    assert (
        ck._no_studio_names(_obs(tool_names=_NAMES, trace=[_find("found studio_publish")]), None)
        is False
    )


def test_no_studio_names_fails_when_no_request_was_read_and_ignores_the_word_studio() -> None:
    assert ck._no_studio_names(_obs(answer="ok"), None) is False
    assert (
        ck._no_studio_names(_obs(tool_names=_NAMES, answer="Studio is not open for you."), None)
        is True
    )
    assert ck._no_studio_names(_obs("delegation", tool_names=_NAMES), None) is None


# ── held_note ────────────────────────────────────────────────────────────────


def test_held_note_passes_when_the_answer_carries_the_note() -> None:
    note = ck.hold_notes()[0]
    assert ck._held_note(_obs(held=True, trace=[_find(f"rules.\n\n{note}")]), None) is True


def test_held_note_fails_with_no_note_or_no_find() -> None:
    """Mutation: give True when the answer has no note → red."""
    assert ck._held_note(_obs(held=True, trace=[_find("rules only")]), None) is False
    assert ck._held_note(_obs(held=True, trace=[]), None) is False
    note = ck.hold_notes()[0]
    assert ck._held_note(_obs(held=True, trace=[_find(note), _find("rules only")]), None) is False


def test_held_note_is_none_in_a_chat_that_is_not_held_and_off_the_search_arm() -> None:
    assert ck._held_note(_obs(held=False, trace=[_find("rules only")]), None) is None
    assert ck._held_note(_obs("delegation", held=True), None) is None
    # the pack arm carries the note in the persona, not in the answer of the load
    assert ck._held_note(_obs(PACK, [(ck.LOAD_TOOL, {}, "rules only")], held=True), None) is None


def test_the_hold_notes_are_the_product_texts() -> None:
    from acb_skills import egress

    assert egress.EGRESS_HOLD_NOTE_PACK in ck.hold_notes()
    assert all(len(n) > 40 for n in ck.hold_notes())


# ── schema_tokens ────────────────────────────────────────────────────────────


def test_schema_tokens_passes_under_the_budget_and_fails_over_it() -> None:
    """Mutation: raise the budget or drop the test → the over-budget case goes red."""
    assert ck._schema_tokens(_obs(schema_tokens=ck.SCHEMA_TOKEN_BUDGET), None) is True
    assert ck._schema_tokens(_obs(schema_tokens=ck.SCHEMA_TOKEN_BUDGET + 1), None) is False
    assert ck._schema_tokens(_obs(schema_tokens=0), None) is False  # no reading proves nothing
    assert ck._schema_tokens(_obs(PACK, schema_tokens=999_999), None) is None


def test_schema_tokens_of_sums_the_schemas_of_a_request() -> None:
    tool = {
        "type": "function",
        "function": {"name": "a", "description": "x" * 400, "parameters": {"type": "object"}},
    }
    one = ck.schema_tokens_of([tool])
    assert one > 100
    assert ck.schema_tokens_of([tool, tool]) == 2 * one
    assert ck.schema_tokens_of([]) == 0


def test_the_estimate_is_the_one_of_maf_mcp() -> None:
    """``_maf_mcp.schema_tokens`` prices a schema with ``count_message_tokens`` on its JSON.
    This reads the same function, so a change there moves both."""
    import json

    from acb_llm.context import count_message_tokens
    from orchestrator import _maf_mcp

    source = Path(_maf_mcp.__file__).read_text(encoding="utf-8")
    assert 'count_message_tokens([{"role": "system", "content": json.dumps(' in source
    tool = {"name": "a", "description": "d", "parameters": {"type": "object", "properties": {}}}
    assert ck.schema_tokens_of([{"type": "function", "function": tool}]) == count_message_tokens(
        [{"role": "system", "content": json.dumps(tool, sort_keys=True, ensure_ascii=False)}]
    )


def test_the_trace_reads_the_calls_in_order() -> None:
    events = [
        {"type": "TOOL_CALL_START", "toolCallId": "1", "toolCallName": ck.FIND_TOOL, "args": ""},
        {"type": "TOOL_CALL_ARGS", "toolCallId": "1", "delta": '{"query": "skills"}'},
        {"type": "TOOL_CALL_RESULT", "toolCallId": "1", "content": "found"},
        {"type": "TOOL_CALL_START", "toolCallId": "2", "toolCallName": "web_search", "args": "{}"},
        {
            "type": "TOOL_CALL_START",
            "toolCallId": "3",
            "toolCallName": "studio_draft",
            "args": '{"brief": "x"}',
        },
        {"type": "TOOL_CALL_RESULT", "toolCallId": "3", "content": "done"},
    ]
    assert ck.trace_of(events) == [
        (ck.FIND_TOOL, {"query": "skills"}, "found"),
        ("studio_draft", {"brief": "x"}, "done"),
    ]


# ── stop result 12 and the numbers ───────────────────────────────────────────


def _summary(searched: int, total: int = 100, false: int = 0) -> dict[str, Any]:
    return {SEARCH: {"app_turns": total, "app_turns_searched": searched, "false_searches": false}}


def _stop12(summary: dict[str, Any]) -> tuple[int, str, str]:
    return next(r for r in run.stop_results(summary) if r[0] == 12)


def test_stop_12_is_ok_at_15_percent_and_stops_at_16() -> None:
    """Mutation: ``>=`` in place of ``>``, or a limit of 16 → red at one of the two."""
    assert _stop12(_summary(15))[1] == "ok"
    assert _stop12(_summary(16))[1] == "STOP"
    assert _stop12(_summary(0))[1] == "ok"
    assert _stop12(_summary(3, 20))[1] == "ok"  # 15 percent
    assert _stop12(_summary(4, 20))[1] == "STOP"
    assert _stop12(_summary(150, 1000))[1] == "ok" and _stop12(_summary(151, 1000))[1] == "STOP"


def test_stop_12_is_not_applicable_with_no_search_arm_or_no_app_turn() -> None:
    assert _stop12({})[1] == "n/a"
    assert _stop12(_summary(0, 0))[1] == "n/a"
    assert [r[0] for r in run.stop_results({})] == [
        1,
        3,
        4,
        9,
        10,
        12,
    ]  # the rest need the pack arm


def test_stop_12_reports_false_searches_on_their_own() -> None:
    assert "False searches, counted on their own: 4" in _stop12(_summary(10, 100, 4))[2]


def _row(arm: str, check: dict[str, Any], searches: int = 0, **more: Any) -> dict[str, Any]:
    return {
        "scenario": "x",
        "turn": 1,
        "arm": arm,
        "repeat": 1,
        "arm_label": arm,
        "set": "studio",
        "checks": check,
        "passed": True,
        "error": None,
        "model_calls": [],
        "pack_events": [],
        "first_text_s": 1.0,
        "total_s": 2.0,
        "searches": searches,
        "found_tools": [],
        "schema_tokens": 0,
        **more,
    }


def test_the_summary_counts_the_searches_of_app_turns_and_false_ones_apart() -> None:
    rows = (
        [_row(SEARCH, {"right_pack": True}, searches=1) for _ in range(3)]
        + [_row(SEARCH, {"right_pack": True}) for _ in range(17)]
        + [_row(SEARCH, {"not_searched": False}, searches=2), _row(SEARCH, {"not_searched": True})]
    )
    out = run.extra_summary(rows)
    assert out["app_turns"] == 20 and out["app_turns_searched"] == 3
    assert out["search_turn_share_pct"] == 15.0
    assert out["false_searches"] == 2  # counted on their own, not in the share
    state = run.stop_results({SEARCH: out})
    assert next(r for r in state if r[0] == 12)[1] == "ok"
    rows += [_row(SEARCH, {"right_pack": True}, searches=1)]
    again = run.extra_summary(rows)
    assert again["app_turns_searched"] == 4 and again["app_turns"] == 21
    assert next(r for r in run.stop_results({SEARCH: again}) if r[0] == 12)[1] == "STOP"


def test_the_report_prints_the_new_rows_and_stop_12() -> None:
    out = run.extra_summary([_row(SEARCH, {"right_pack": True}, searches=1, schema_tokens=900)])
    text = run.report(
        {
            "summary": {
                SEARCH: {
                    "turns": 1,
                    "turns_passed": 1,
                    "checks_passed": 1,
                    "checks": 1,
                    "errors": 0,
                    "first_text_s": {"mean": 1, "median": 1, "p90": 1},
                    "total_s": {"mean": 1, "median": 1, "p90": 1},
                    "calls_per_turn": 1,
                    "calls_orchestrator_per_turn": 1,
                    "calls_email_per_turn": 0,
                    "prompt_per_turn": 1,
                    "cached_per_turn": 0,
                    "completion_per_turn": 1,
                    "cost_per_turn_usd": 0,
                    "cost_usd": 0,
                    "per_check": {},
                    "by_scenario": {},
                    **out,
                }
            }
        }
    )
    assert "Searches (find_tools) per turn" in text and "False searches" in text
    assert "Schema tokens in a request, most" in text
    assert re.search(r"\| 12 \| STOP \|", text)


# ── the harness stays as it was, and the docs agree ──────────────────────────

ROOT = Path(__file__).resolve().parents[2]


def test_the_harness_file_is_not_edited_for_this_slice() -> None:
    """The tier of a slice that edits ``ModelCall`` is higher. The measure reads the tools
    of a request from its own wrapper (``run.tap_schemas``)."""
    text = (ROOT / "evals" / "routing" / "harness.py").read_text(encoding="utf-8")
    assert "schema_tokens" not in text and "find_description" not in text
    assert "tap_schemas" in (ROOT / "evals" / "one_assistant_measure" / "run.py").read_text(
        encoding="utf-8"
    )


def test_the_spec_anchor_of_pack_only_owners_is_the_real_line() -> None:
    lines = (
        (ROOT / "packages" / "acb_skills" / "acb_skills" / "tool_packs.py")
        .read_text(encoding="utf-8")
        .splitlines()
    )
    real = next(i for i, line in enumerate(lines, 1) if line.startswith("PACK_ONLY_OWNERS"))
    spec = (ROOT / "project-docs" / "specs" / "one_assistant_context.md").read_text(
        encoding="utf-8"
    )
    cited = {int(n) for n in re.findall(r"PACK_ONLY_OWNERS`\)? ?\(?`?tool_packs\.py:(\d+)", spec)}
    assert cited == {real}, (cited, real)


def test_the_spec_splits_ts5_and_names_the_report_and_the_proposal() -> None:
    spec = (ROOT / "project-docs" / "specs" / "one_assistant_context.md").read_text(
        encoding="utf-8"
    )
    assert "**TS-5a." in spec and "**TS-5b." in spec and "task 3289" in spec
    assert "one_assistant_measure_oa8e_run5_2026-10-30.md" in spec
    assert "--scenarios <ids>" in spec and "--scenarios <set>" not in spec
    assert "The owner has NOT approved it" in spec
    assert (ROOT / "docs" / "one_assistant_measure_oa8e_run5_2026-10-30.md").is_file()
