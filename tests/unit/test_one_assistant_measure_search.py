"""The fence of the Studio chats and the checks of the ``search`` arm (WS-67 TS-5a, R7).

``evals/one_assistant_measure`` gets five Studio chats and six pure checks. The
spec is ``project-docs/specs/one_assistant_context.md`` §4.2.9 (TS-5a, TS-5b) and
§4.3.3. This file holds the harness, never a live run: nothing here calls a model
or spends money.

* Each new check has a case that passes, a case that fails, and a case that is the
  named MUTATION of it (the line that a mutation of the check would flip).
* The Studio chats run on the ``pack`` arm and on the ``search`` arm (TS-5b) through the
  real orchestrator run. Only the HTTP transport is the script. The ``search`` arm
  prints SKIP again when ``SEARCH_PLANS`` is False.
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
from evals.one_assistant_measure.scenarios_studio import CAP_TURNS, STUDIO_SCENARIOS, pack_of
from evals.one_assistant_measure.scenarios_work import WORK_SCENARIOS
from evals.routing import dataset as ds

SEARCH, PACK = ck.SEARCH_ARM, "pack"
STUDIO_IDS = ("stu_skills", "stu_new_app", "stu_plain", "stu_no_feature", "stu_held", "stu_cap")
NOT_FOUND = 'Error: Requested function "studio_skill_list" not found'
OK_ANSWER = '{"ok": true, "skills": []}'


@pytest.fixture(scope="module")
def both() -> tuple[list[dict[str, Any]], list[str]]:
    """The Studio chats on the pack arm and on the search arm (TS-5b)."""
    return run.run_scripted(STUDIO_SCENARIOS, (PACK, SEARCH))


@pytest.fixture(scope="module")
def scripted(both) -> tuple[list[dict[str, Any]], list[str]]:
    """The rows of the pack arm, and the problems of both arms."""
    rows, problems = both
    return [r for r in rows if r["arm"] == PACK], problems


@pytest.fixture(scope="module")
def searched(both) -> list[dict[str, Any]]:
    """The rows of the search arm."""
    return [r for r in both[0] if r["arm"] == SEARCH]


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


def test_the_studio_chats_are_in_the_set_and_in_no_work_set() -> None:
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
    # TS-5b. The first three turns of ``stu_cap`` load a pack BY WORDS on purpose (the
    # next test proves which). No turn of any chat matches a Studio pack.
    cap_loads = {words for words, _, _, _ in CAP_TURNS}
    hits = [
        (sc.id, slug, tool_packs.matched_layer(slug, turn.words))
        for sc in STUDIO_SCENARIOS
        for turn in sc.turns
        for slug in tool_packs.PACKS
        if tool_packs.matched_layer(slug, turn.words)
        and not (sc.id == "stu_cap" and turn.words in cap_loads)
    ]
    assert hits == [], hits
    studio = [s for s in tool_packs.PACKS if tool_packs.PACKS[s].owner == "studio-builder"]
    assert [
        (sc.id, slug)
        for sc in STUDIO_SCENARIOS
        for turn in sc.turns
        for slug in studio
        if tool_packs.matched_layer(slug, turn.words)
    ] == []


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
    assert all(r["passed"] for r in rows)


def test_the_studio_tools_ran_in_the_orchestrators_own_run(scripted) -> None:
    rows, _ = scripted
    ran = {(r["scenario"], r["turn"]): r["work_tools"] for r in rows}
    assert ran[("stu_skills", 1)] == ["studio_skill_list"]
    assert ran[("stu_new_app", 1)] == ["studio_draft"]
    assert ran[("stu_plain", 1)] == []
    assert ran[("stu_no_feature", 1)] == ["studio_draft", "studio_skill_list"]  # tried, and refused
    assert ran[("stu_held", 2)] == ["studio_skill_list"]
    assert all(r["checks"]["ran"] for r in rows)


def test_the_pack_arm_loads_studio_by_the_model(scripted) -> None:
    """Layer (a) did not load it: the model called ``load_skill_tools`` (layer b)."""
    rows, _ = scripted
    loaded = {(r["scenario"], r["turn"]): r["loaded_by_model"] for r in rows}
    # The pack that OWNS the tool, read from tool_packs (TS-1b split the Studio pack).
    assert loaded[("stu_skills", 1)] == [pack_of("studio_skill_list")] == ["studio-more"]
    assert loaded[("stu_new_app", 1)] == [pack_of("studio_draft")] == ["studio"]
    assert loaded[("stu_plain", 1)] == []
    assert loaded[("stu_held", 2)] == ["studio-more"]


def test_the_rows_hold_the_numbers_of_the_search_arm(scripted) -> None:
    rows, _ = scripted
    for r in rows:
        assert r["searches"] == 0  # no find_tools call exists on the pack arm
        assert r["find_tools_offered"] is False and r["find_tools_requests"] == 0
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
    # BOTH packs of the split are tried and refused, with the real refusal text
    assert row["work_tools"] == ["studio_draft", "studio_skill_list"]
    assert row["loaded_by_model"] == ["studio", "studio-more"]


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
    # WS-STU J2d: the share tool of the fourth pack of the owner.
    assert "studio_skill_share" in names and len(names) == 26
    for name in names:
        assert run.owner_of_tool(name) == pack.STUDIO, name
    assert run.owner_of_tool("read_email") == pack.EA
    assert run.owner_of_tool("get_pipeline") == pack.CRM
    assert run.owner_of_tool("list_tasks") == pack.PROJECTS


def test_the_studio_names_come_from_the_skill_families() -> None:
    from acb_skills.skill_families import SKILL_FAMILIES

    assert pack.studio_tool_names() == (
        frozenset(SKILL_FAMILIES["studio"]["tools"])
        | frozenset(SKILL_FAMILIES["studio-more"]["tools"])
        | frozenset(SKILL_FAMILIES["studio-workflows"]["tools"])
        | frozenset(SKILL_FAMILIES["studio-share"]["tools"])
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
    on = tool_packs.packs_on(ds.ORG)
    # a follower comes on with its parent (FOLLOWS), as in production's TOOL_PACKS
    assert {"studio", "studio-more"} <= on and "studio-workflows" not in on


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
    """``search_built`` needs the plans of TS-5b (``SEARCH_PLANS``) and the field
    ``tool_search_orgs``. TS-2a PR 3 added the field, and TS-5b added the plans.
    It reads the settings with ``getattr``, so it needs no import of the field."""
    from types import SimpleNamespace

    assert run.SEARCH_PLANS is True and run.search_built() is True  # TS-5b built the plans
    monkeypatch.setattr(run, "SEARCH_PLANS", False)
    assert run.search_built() is False  # the field is there, the plans are off
    monkeypatch.setattr(run, "SEARCH_PLANS", True)
    assert run.search_built() is True  # TS-2a PR 3 added tool_search_orgs
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
    assert "needs the flag tool_search_orgs (TS-2a)" in out and "SEARCH_PLANS" in out
    assert seen == [(PACK,)]


@pytest.mark.parametrize("arm", [PACK, SEARCH, "delegation", run.SPAWN_ARM])
def test_only_the_search_arm_holds_tool_search_orgs(arm: str) -> None:
    """TS-2a PR 3. ``PRODUCTION`` pins ``tool_search_orgs`` to "", so a developer .env
    that sets ``TOOL_SEARCH_ORGS`` changes no number of the other arms. The search arm
    sets the eval org after it. Mutation: drop the key from ``PRODUCTION`` -> red."""
    assert run.PRODUCTION["tool_search_orgs"] == ""
    merged = {**run.PRODUCTION, **run.flag_for(scenario("stu_skills"), arm)}
    assert merged["tool_search_orgs"] == (ds.ORG if arm == SEARCH else "")


def test_the_search_arm_does_not_skip_when_built(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen: list[tuple[str, ...]] = []
    monkeypatch.setattr(run, "search_built", lambda: True)
    monkeypatch.setattr(run, "run_scripted", lambda s, arms, tm: (seen.append(arms), ([], []))[1])
    run.main(["--scripted", "--arms", "pack,search", "--scenarios", "stu_skills"])
    assert "SKIP: arm search" not in capsys.readouterr().out
    assert seen == [(PACK, SEARCH)]


def test_the_scripted_plan_of_the_search_arm_is_built() -> None:
    """TS-5b. ``scripted_models`` no longer raises. The orchestrator plays ``find_tools``
    BEFORE the Studio tool, and never ``load_skill_tools`` for it."""
    models = run.scripted_models(scenario("stu_skills"), SEARCH)
    names = [t["tool_calls"][0]["function"]["name"] for t in _turns(models) if t.get("tool_calls")]
    assert names == [ck.FIND_TOOL, "studio_skill_list"]
    pack_names = [
        t["tool_calls"][0]["function"]["name"]
        for t in _turns(run.scripted_models(scenario("stu_skills"), PACK))
        if t.get("tool_calls")
    ]
    assert pack_names == [ck.LOAD_TOOL, "studio_skill_list"]  # the pack arm still loads
    plain = run.scripted_models(scenario("stu_plain"), SEARCH)
    assert not [t for t in _turns(plain) if t.get("tool_calls")]  # a plain chat makes no find


def _turns(models: dict[str, Any]) -> list[dict[str, Any]]:
    """The assistant messages of the orchestrator's script, in order."""
    out: list[dict[str, Any]] = []
    for turn in models[run.ORCHESTRATOR].turns:
        out += [m for m in turn if m.get("role") == "assistant"]
    return out


def test_an_unknown_arm_exits_with_status_2(capsys: pytest.CaptureFixture[str]) -> None:
    """Before TS-5a an unknown value dropped silently. Mutation: drop it again → red."""
    code = run.main(["--scripted", "--arms", "pack,serach"])
    out = capsys.readouterr().out
    assert code == run.EXIT_NO_GO == 2
    assert "serach" in out and "no such arm" in out


def test_the_default_arms_run_no_search_arm_row() -> None:
    """A run that asks for ``delegation,pack`` only gets no search row. (The default of
    ``--arms`` is every arm, and the search arm joins when ``search_built``.)"""
    assert run.SEARCH_ARM not in run.ARMS
    assert run.arms_for(scenario("stu_skills"), run.ARMS) == (PACK,)


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
    tried = [
        (ck.LOAD_TOOL, {"pack": "studio"}, "Not loaded."),
        _studio('Function "studio_skill_list" not found.'),
    ]
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


# ── TS-5b: the plans that call find_tools ────────────────────────────────────

#: The tool that each Studio turn must find. Names only, from the real chats: the pack
#: of each tool comes from ``tool_packs`` (``pack_of``), and no count of a pack is here.
EXPECTED = {
    ("stu_skills", 1): "studio_skill_list",
    ("stu_new_app", 1): "studio_draft",
    ("stu_no_feature", 1): "studio_skill_list",  # the tool that the query would find
    ("stu_held", 2): "studio_skill_list",
    ("stu_cap", 4): "studio_skill_list",
}


def _expected(sid: str, turn: int) -> str:
    """The tool that turn *turn* of chat *sid* must find, or a clear failure that names the
    chat. A new plan with a ``find`` needs a line in ``EXPECTED`` (a bare KeyError told nothing)."""
    if (sid, turn) not in EXPECTED:
        raise AssertionError(
            f"scenario {sid} turn {turn} has a find plan and no entry in EXPECTED of this "
            "test file: add (scenario id, turn) -> the tool that its query must find."
        )
    return EXPECTED[(sid, turn)]


def _queries() -> list[tuple[str, int, str, str]]:
    return [
        (sc.id, i + 1, q, _expected(sc.id, i + 1))
        for sc in STUDIO_SCENARIOS
        for i, t in enumerate(sc.turns)
        for q in t.plan.find
    ]


def test_every_find_turn_has_an_expected_tool_and_the_plain_chat_has_none() -> None:
    assert {(a, b) for a, b, _, _ in _queries()} == set(EXPECTED)
    assert all(not t.plan.find for t in scenario("stu_plain").turns)  # not_searched needs no find
    for sc in STUDIO_SCENARIOS:  # a chat that needs a tool searches once
        for t in sc.turns:
            assert len(t.plan.find) <= 1


@pytest.mark.parametrize("sid,turn,query,tool", _queries())
def test_each_query_ranks_its_tool_in_the_top_five(
    sid: str, turn: int, query: str, tool: str
) -> None:
    """The query is a real call to the real ranker. When the registry changes and the tool
    leaves the top 5, this test names the registry and the query to fix. The pack of the
    tool comes from ``SKILL_FAMILIES``/``FOLLOWS`` through ``tool_packs`` (``pack_of``)."""
    from acb_skills import tool_search

    got = [name for name, _, _ in tool_search.rank(query, limit=5)]
    assert tool in got, (
        f"{sid} turn {turn}: the query {query!r} no longer ranks {tool} (pack {pack_of(tool)}) "
        f"in the top 5 of tool_search.rank. Top 5 now: {got}. The tool registry changed "
        "(SKILL_FAMILIES, FOLLOWS, or the tool text): fix the query in scenarios_studio.py."
    )


def test_a_query_of_stop_words_ranks_nothing() -> None:
    """The base of mutation (c): a query that ranks nothing finds no tool."""
    from acb_skills import tool_search

    assert tool_search.rank("the a for to of", limit=5) == []


def test_the_search_arm_passes_each_studio_check(both) -> None:
    rows, problems = both
    assert problems == [], problems
    mine = [r for r in rows if r["arm"] == SEARCH]
    assert len(mine) == sum(len(s.turns) for s in STUDIO_SCENARIOS)
    assert all(r["passed"] for r in mine)


def test_the_search_rows_show_what_the_model_searched_and_found(searched) -> None:
    by = {(r["scenario"], r["turn"]): r for r in searched}
    for key, tool in (("stu_skills", "studio_skill_list"), ("stu_new_app", "studio_draft")):
        row = by[(key, 1)]
        assert row["searches"] == 1 and row["found_tools"] == [tool]
        assert row["checks"]["found_tool"] is True and row["checks"]["search_round"] is True
        assert row["loaded_by_model"] == []  # the model searched, and loaded no pack
        assert row["checks"]["schema_tokens"] is True and row["schema_tokens"] > 0
    plain = by[("stu_plain", 1)]
    assert plain["searches"] == 0 and plain["checks"]["not_searched"] is True
    nofeat = by[("stu_no_feature", 1)]
    assert nofeat["searches"] == 1 and nofeat["found_tools"] == []
    assert nofeat["work_tools"] == []  # no Studio tool ran
    assert nofeat["checks"]["no_studio_names"] is True
    assert nofeat["checks"]["no_studio_ran"] is True
    assert by[("stu_held", 1)]["searches"] == 0 and by[("stu_held", 1)]["held"] is False
    # TS-5b: the new field. Every search turn carried find_tools: the plain chat offered
    # and ignored it, the others offered and used it.
    assert all(r["find_tools_offered"] and r["find_tools_requests"] >= 1 for r in searched)
    assert plain["find_tools_offered"] is True and plain["searches"] == 0


def test_the_held_chat_finds_the_studio_pack_with_its_hold_note(searched) -> None:
    """The held finding of TS-5b. The product DOES hold the Studio pack in a chat that read
    mail, so ``held_note`` can pass. The note comes AFTER the rules text, past the 2000
    characters that the event stream keeps of a tool result, so the harness reads the full
    answer from the next request (``checks.find_answers_in``)."""
    row = next(r for r in searched if r["scenario"] == "stu_held" and r["turn"] == 2)
    assert row["held"] is True
    assert row["searches"] == 1 and row["found_tools"] == ["studio_skill_list"]
    assert row["checks"]["held_note"] is True


def test_the_full_find_answer_is_read_from_the_request() -> None:
    long = "Found 5 tools.\n" + "x" * 3000 + "\n## Held in this chat\nnote"
    body = {
        "messages": [
            {
                "role": "assistant",
                "tool_calls": [
                    {"id": "c1", "function": {"name": ck.FIND_TOOL}},
                    {"id": "c2", "function": {"name": "other"}},
                ],
            },
            {"role": "tool", "tool_call_id": "c1", "content": long},
            {"role": "tool", "tool_call_id": "c2", "content": "not a find"},
        ]
    }
    assert ck.find_answers_in(body) == [long]
    cut = [(ck.FIND_TOOL, {}, long[: ck.RESULT_CUT])]
    assert ck.full_find_results(cut, [long])[0][2] == long
    assert ck.full_find_results(cut, [])[0][2] == long[: ck.RESULT_CUT]  # no request, no note


def test_a_found_answer_with_card_words_is_a_round() -> None:
    """The rules text of a found pack says "nothing was created". ``refused`` reads that as a
    refusal, so the round did not count (the first scripted run). An answer that starts
    ``Found`` is a round. Mutation: use ``refused`` in ``_search_round`` again -> red."""
    text = (
        "Found 1 tools. Call them from your next step.\n- x: y\n"
        "For the other decisions, nothing was created: say so."
    )
    assert ck.refused(text) is True  # the trap is real
    assert ck._search_round(_obs(trace=[_find(text), _studio()]), None) is True
    refusal = _find("Not found. find_tools acts only in the run that offered it.")
    assert ck._search_round(_obs(trace=[refusal]), None) is False


def test_stop_12_and_the_search_numbers_run_on_real_search_rows(both) -> None:
    rows, _ = both
    mine = [r for r in rows if r["arm"] == SEARCH]
    summary = run.summarise(rows)[SEARCH]
    assert summary["false_searches"] == 0  # stu_plain must not search
    assert summary["app_turns"] == 3 and summary["app_turns_searched"] == 3
    assert summary["searches_per_turn"] == round(sum(r["searches"] for r in mine) / len(mine), 3)
    assert summary["found_tools_per_turn"] > 0
    assert summary["schema_tokens_max"] >= summary["schema_tokens_mean"] > 0
    state, why = next((st, w) for n, st, w in run.stop_results({SEARCH: summary}) if n == 12)
    # Every Studio app turn needs a find, so a Studio-only set cannot pass result 12:
    # the rule counts the FULL app set (spec decision 12). The state is computed, not n/a.
    assert state == "STOP" and "3 of 3 app turns" in why and "False searches" in why


def test_the_report_and_the_out_file_hold_the_same_columns_for_pack_and_search(
    both, tmp_path: Path
) -> None:
    """A live run compares the arms column by column: pass rate, calls per turn, input
    tokens per turn, tools per request and schema tokens. ``--out`` writes the summary
    through ``_write`` (the live path), and ``report`` prints it."""
    import json

    rows, _ = both
    out = tmp_path / "chunk.json"
    run._write(out, {"rows": rows, "spent_usd": 0.0, "spent_before_usd": 0.0})
    data = json.loads(out.read_text(encoding="utf-8"))
    keys = (
        "turns_passed",
        "turns",
        "calls_per_turn",
        "prompt_per_turn",
        "tools_per_call_mean",
        "tools_per_call_max",
        "schema_tokens_max",
        "schema_tokens_mean",
        "searches_per_turn",
        "false_searches",
    )
    for arm in (PACK, SEARCH):
        assert all(k in data["summary"][arm] for k in keys), arm
    assert data["summary"][SEARCH]["turns_passed"] == data["summary"][SEARCH]["turns"]
    assert data["summary"][PACK]["tools_per_call_mean"]
    assert data["summary"][SEARCH]["schema_tokens_mean"]
    text = run.report(data)
    for label in (
        "Tools in a request, mean",
        "Schema tokens in a request, mean",
        "Searches (find_tools) per turn",
        "False searches",
        "Layer (b) turns",
    ):
        assert label in text
    assert re.search(r"\| 12 \| (STOP|ok) \|", text)
    header = next(line for line in text.splitlines() if line.startswith("| Loader and cap"))
    assert PACK in header and SEARCH in header  # one column for each arm, side by side


# ── mutations: each one runs the REAL loop and must go red ───────────────────


def _mutated(sid: str, edit: Any, arms: tuple[str, ...] = (SEARCH,)) -> tuple[list, list]:
    """Run the Studio chat *sid* with the turn plans changed by *edit*."""
    import dataclasses

    sc = scenario(sid)
    turns = tuple(dataclasses.replace(t, plan=edit(t.plan)) for t in sc.turns)
    return run.run_scripted((dataclasses.replace(sc, turns=turns),), arms, None)


def test_mutation_a_no_find_for_stu_skills_goes_red() -> None:
    """The model calls the Studio tool and never searches: the tool is not in the request."""
    import dataclasses

    rows, problems = _mutated("stu_skills", lambda p: dataclasses.replace(p, find=()))
    checks = rows[0]["checks"]
    assert checks["found_tool"] is False and checks["search_round"] is False
    assert problems and not rows[0]["passed"]


def test_mutation_b_a_find_on_the_plain_chat_goes_red_and_counts_a_false_search() -> None:
    import dataclasses

    rows, problems = _mutated("stu_plain", lambda p: dataclasses.replace(p, find=("write a note",)))
    assert rows[0]["checks"]["not_searched"] is False and problems
    assert run.extra_summary(rows)["false_searches"] == 1  # counted on its own


def test_mutation_c_a_query_that_ranks_nothing_goes_red() -> None:
    import dataclasses

    rows, problems = _mutated(
        "stu_skills", lambda p: dataclasses.replace(p, find=("the a for to",))
    )
    assert rows[0]["checks"]["found_tool"] is False
    assert problems and not rows[0]["passed"]


def test_mutation_d_an_empty_flag_runs_no_find_and_fails_loud(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The arm runs with ``tool_search_orgs`` empty. The product offers no ``find_tools``, the
    call is refused, and the checks fail. No silent pass."""
    real = run.flag_for
    monkeypatch.setattr(run, "flag_for", lambda sc, arm: {**real(sc, arm), "tool_search_orgs": ""})
    rows, problems = run.run_scripted((scenario("stu_skills"),), (SEARCH,), None)
    row = rows[0]
    assert row["found_tools"] == []  # no Studio tool ran
    assert row["checks"]["found_tool"] is False and row["checks"]["search_round"] is False
    assert problems and not row["passed"]


def test_mutation_e_plans_off_prints_skip_again(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(run, "SEARCH_PLANS", False)
    monkeypatch.setattr(run, "run_scripted", lambda s, arms, tm: ([], []))
    run.main(["--scripted", "--arms", "pack,search", "--scenarios", "stu_skills"])
    assert "SKIP: arm search" in capsys.readouterr().out
    assert run.arms_for(scenario("stu_skills"), run.ALL_ARMS) == (PACK,)


def test_mutation_f_a_studio_tool_mapped_to_email_goes_red(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``owner_of_tool`` sent a Studio tool to email before TS-5a. The scripted model then
    hands the work to the email owner, and the Studio tool never runs."""
    assert run.owner_of_tool("studio_skill_list") == pack.STUDIO
    monkeypatch.setattr(run, "owner_of_tool", lambda name: run.EA)
    rows, problems = run.run_scripted((scenario("stu_skills"),), (PACK, SEARCH), None)
    by_arm = {r["arm"]: r for r in rows}
    # The pack arm loads the wrong pack, the Studio tool is not in the request: red.
    assert not by_arm[PACK]["passed"] and by_arm[PACK]["checks"]["found_tool"] is False
    assert problems
    # The search arm finds the tool anyway, but the wrong mapping adds a stray load of an
    # email pack. The row test (``loaded_by_model == []``) is the fence on that arm.
    assert by_arm[SEARCH]["loaded_by_model"] != []


def test_mutation_g_studio_workflows_on_in_the_flag_is_seen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With ``studio-workflows`` in ``STUDIO_PACKS`` the flag of the search arm turns the
    DARK pack on. ``test_studio_workflows_stays_off`` (both arms) is the fence, and it fails."""
    monkeypatch.setattr(run, "STUDIO_PACKS", ("studio", "studio-workflows"))
    flag = run.flag_for(scenario("stu_skills"), SEARCH)
    assert "studio-workflows" in flag["tool_packs"].split(",")


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


def test_the_spec_names_pack_only_owners_with_no_line_number() -> None:
    """The spec names the constant and no line of ``tool_packs.py``.

    A line number goes stale each time a pack PR adds a line above it, and the
    fence then turns main red for every lane. The symbol must exist, and the
    spec must not pin a line for it.
    Mutation: put ``tool_packs.py:<n>`` after ``PACK_ONLY_OWNERS`` in the spec.
    """
    src = (ROOT / "packages" / "acb_skills" / "acb_skills" / "tool_packs.py").read_text(
        encoding="utf-8"
    )
    assert re.search(r"^PACK_ONLY_OWNERS\b", src, re.M)
    spec = (ROOT / "project-docs" / "specs" / "one_assistant_context.md").read_text(
        encoding="utf-8"
    )
    assert "PACK_ONLY_OWNERS" in spec
    assert not re.findall(r"PACK_ONLY_OWNERS`[^\n]{0,12}tool_packs\.py:\d+", spec)


def test_the_spec_splits_ts5_and_names_the_report_and_the_proposal() -> None:
    spec = (ROOT / "project-docs" / "specs" / "one_assistant_context.md").read_text(
        encoding="utf-8"
    )
    assert "**TS-5a." in spec and "**TS-5b." in spec and "task 3289" in spec
    assert "one_assistant_measure_oa8e_run5_2026-10-30.md" in spec
    assert "--scenarios <ids>" in spec and "--scenarios <set>" not in spec
    assert "The owner has NOT approved it" in spec
    assert (ROOT / "docs" / "one_assistant_measure_oa8e_run5_2026-10-30.md").is_file()


# ── TS-5b: a fair check of the skill list ────────────────────────────────────

#: The answer of the first live pilot (2026-11-15): right, and the strict check failed it.
PILOT = (
    "Two of your three skills are still drafts: Weekly digest writer and Meeting notes tidy. "
    "Quote follow-up is already published."
)
LIVE = ("Quote follow-up",)
DRAFTS = (r"Weekly digest writer", r"Meeting notes tidy")


def _fair(answer: str) -> bool:
    return ck._drafts_listed_fairly(_obs(answer=answer), (DRAFTS, LIVE))


def _strict(answer: str) -> bool:
    """The old check: ``answer_all`` of the drafts, and ``answer_none`` of the live name."""
    obs = _obs(answer=answer)
    return ck._answer_all(obs, DRAFTS) and ck._answer_none(obs, (r"Quote follow-up",))


def test_the_pilot_answer_passes_the_fair_check_and_failed_the_strict_one() -> None:
    assert _fair(PILOT) is True
    assert _strict(PILOT) is False  # the finding: a correct answer failed


def test_the_fair_check_passes_other_correct_shapes() -> None:
    assert _fair("The drafts are Weekly digest writer and Meeting notes tidy.") is True
    assert (
        _fair("Drafts:\n- Weekly digest writer\n- Meeting notes tidy\nQuote follow-up is live.")
        is True
    )
    assert _fair(
        "Weekly digest writer and Meeting notes tidy are drafts, and Quote follow-up is published."
    )
    assert _fair(
        "Drafts: Weekly digest writer, Meeting notes tidy. Quote follow-up is not a draft."
    )


def test_the_fair_check_fails_a_live_skill_listed_as_a_draft() -> None:
    assert not _fair("Three drafts: Weekly digest writer, Meeting notes tidy and Quote follow-up.")
    assert not _fair(
        "Drafts:\n- Weekly digest writer\n- Meeting notes tidy\n- Quote follow-up (draft)"
    )
    assert not _fair(
        "Weekly digest writer and Meeting notes tidy are drafts. Quote follow-up is a draft too."
    )


def test_the_fair_check_fails_when_a_draft_is_missing() -> None:
    assert not _fair("One skill is a draft: Weekly digest writer. Quote follow-up is published.")
    assert not _fair("Meeting notes tidy is a draft.")
    assert not _fair("")


def test_calls_live_skill_a_draft_is_a_pure_function_of_the_text() -> None:
    assert ck.calls_live_skill_a_draft(PILOT, LIVE) is False
    assert ck.calls_live_skill_a_draft("Quote follow-up is still a draft.", LIVE) is True
    assert ck.calls_live_skill_a_draft("No skill is named here.", LIVE) is False
    assert ck.calls_live_skill_a_draft("quote FOLLOW-UP is still a draft.", LIVE) is True


def test_mutation_the_strict_check_back_fails_the_pilot_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The old behaviour: a live skill that the answer NAMES fails. With it, the pilot answer
    goes red on the real check."""

    def strict(answer: str, live: tuple[str, ...]) -> bool:
        return any(ck._found(rf"\b{re.escape(n)}\b", answer) for n in live)

    monkeypatch.setattr(ck, "calls_live_skill_a_draft", strict)
    assert _fair(PILOT) is False  # red: the finding is back


def test_the_stu_skills_chat_uses_the_fair_check_and_no_strict_one() -> None:
    checks = scenario("stu_skills").turns[0].checks
    assert "drafts_listed_fairly" in {c.kind for c in checks}
    assert not any(c.kind == "answer_none" and "Quote" in str(c.arg) for c in checks)


# ── TS-5b: was find_tools offered? ───────────────────────────────────────────


def test_a_search_row_with_the_tool_offered_and_no_search(searched) -> None:
    """The plain chat on the search arm: the tool was in the request, and the model made no
    search. That is "offered and ignored", and it is not "not offered"."""
    row = next(r for r in searched if r["scenario"] == "stu_plain")
    assert row["find_tools_offered"] is True
    assert row["find_tools_requests"] >= 1 and row["searches"] == 0


def test_a_pack_row_never_offers_the_tool(scripted) -> None:
    rows, _ = scripted
    assert rows
    assert all(r["find_tools_offered"] is False and r["find_tools_requests"] == 0 for r in rows)


def test_find_requests_of_counts_the_requests_that_held_the_tool() -> None:
    class Call:
        def __init__(self, names: list[str]) -> None:
            self.tool_names = names

    calls = [Call(["a", ck.FIND_TOOL]), Call(["a"]), Call([ck.FIND_TOOL])]
    assert run.find_requests_of(calls) == 2
    assert run.find_requests_of([Call([])]) == 0
    assert run.find_requests_of([object()]) == 0  # a call that the tap did not read


def test_the_summary_and_the_report_say_offered_but_not_called() -> None:
    rows = [
        _row(
            SEARCH, {"right_pack": True}, searches=1, find_tools_offered=True, find_tools_requests=2
        ),
        _row(SEARCH, {"not_searched": True}, find_tools_offered=True, find_tools_requests=1),
        _row(SEARCH, {"right_pack": True}, find_tools_offered=False, find_tools_requests=0),
    ]
    out = run.extra_summary(rows)
    assert out["find_offered_turns"] == 2 and out["find_offered_not_called"] == 1
    assert out["offered_not_called"] == "1 of 2"
    assert out["find_requests_per_turn"] == 1.0
    base = {
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
    }
    text = run.report({"summary": {SEARCH: {**base, **out}}})
    assert f"{SEARCH}: offered but not called: 1 of 2" in text
    assert "Requests that carried find_tools, per turn" in text
    # a pack-only report has no turn with the tool, so it prints no such line
    pack_out = run.extra_summary([_row(PACK, {"right_pack": True})])
    assert "offered but not called" not in run.report({"summary": {PACK: {**base, **pack_out}}})
    # an old row with no field counts as not offered
    assert run.extra_summary([_row(SEARCH, {"right_pack": True})])["find_offered_turns"] == 0


# ── TS-5b: the cap chat ──────────────────────────────────────────────────────

CAP_SLUGS = ("crm-records", "email", "projects-reminders")


def _packs_of(row: dict[str, Any], event: str, key: str) -> list[str]:
    import ast

    for e in row["pack_events"]:
        if e["event"] == event:
            return list(ast.literal_eval(str(e[key])))
    return []


def _cap_problems(rows: list[dict[str, Any]]) -> list[str]:
    """What the cap chat must show. Empty when it holds. Each mutation of the chat gives a
    non-empty list. It reads the REAL run: the plan of the loader and the request."""
    out: list[str] = []
    mine = [r for r in rows if r["scenario"] == "stu_cap"]
    by = {}
    for arm in (PACK, SEARCH):  # the LAST turn of each arm: the Studio ask (4, or 3 when mutated)
        turns = [r for r in mine if r["arm"] == arm]
        if not turns:
            return [f"no turn on the {arm} arm"]
        by[(arm, 4)] = max(turns, key=lambda r: r["turn"])
    for arm in (PACK, SEARCH):
        row = by[(arm, 4)]
        held = _packs_of(row, "executor.packs_loaded", "packs")
        offered = _packs_of(row, "executor.pack_plan", "offered")
        if len(held) != tool_packs.MAX_PACKS:
            out.append(f"{arm}: holds {held}, not {tool_packs.MAX_PACKS} packs")
        if offered:
            out.append(f"{arm}: the loader offers {offered} (load_skill_tools is in the request)")
        if row["loaded_by_model"]:
            out.append(f"{arm}: the model loaded {row['loaded_by_model']}")
    pack_row, find_row = by[(PACK, 4)], by[(SEARCH, 4)]
    if pack_row["find_tools_offered"] or pack_row["work_tools"]:
        out.append("pack arm: a Studio tool ran or find_tools was offered")
    if not find_row["find_tools_offered"]:
        out.append("search arm: find_tools was not in the request")
    if find_row["found_tools"] != ["studio_skill_list"]:
        out.append(f"search arm: found {find_row['found_tools']}")
    return out


@pytest.fixture(scope="module")
def cap(both) -> list[dict[str, Any]]:
    return [r for r in both[0] if r["scenario"] == "stu_cap"]


def test_the_cap_chat_loads_three_packs_by_words_and_studio_by_none() -> None:
    """The proof with the real ``matched_layer``: each of turns 1 to 3 loads its pack by
    words, and turn 4 loads no pack and no Studio pack."""
    sc = scenario("stu_cap")
    assert len(sc.turns) == 4 and sc.surface == "crm"
    assert tuple(slug for _, slug, _, _ in CAP_TURNS) == CAP_SLUGS
    for turn, (words, slug, _, _) in zip(sc.turns, CAP_TURNS, strict=False):
        assert turn.words == words
        assert tool_packs.matched_layer(slug, words) in ("words", "reference"), (slug, words)
        others = [s for s in tool_packs.PACKS if s != slug and tool_packs.matched_layer(s, words)]
        assert others == [], (words, others)  # one pack for each turn, never two
    ask = sc.turns[3].words
    assert ask == "Which of my saved skills are still drafts?"
    assert [s for s in tool_packs.PACKS if tool_packs.matched_layer(s, ask)] == []


def test_the_loader_has_no_room_at_three_packs_and_a_control_has_room() -> None:
    allow = frozenset(tool_packs.PACKS)
    full = tool_packs.plan_loader(allow, CAP_SLUGS, current=100)
    assert full.offered == ()
    assert len(CAP_SLUGS) == tool_packs.MAX_PACKS == 3
    room = tool_packs.plan_loader(allow, CAP_SLUGS[:2], current=60)
    assert "studio-more" in room.offered  # the control: with two packs the loader offers
    found = tool_packs.search_packs(allow, CAP_SLUGS, owners=tool_packs.PACK_ONLY_OWNERS)
    assert "studio-more" in found and "studio" in found  # find_tools still reaches Studio


def test_the_cap_chat_runs_the_real_plan_of_the_loader(both) -> None:
    """On turn 4 of the REAL run, three packs are held and the loader offers nothing, so
    ``load_skill_tools`` is not in the request. On the search arm ``find_tools`` is, and the
    found tool lives in ``studio-more``. Mutations: two packs by words, a flag with no search."""
    rows, problems = both
    assert problems == [], problems
    assert _cap_problems(rows) == []
    by = {(r["arm"], r["turn"]): r for r in rows if r["scenario"] == "stu_cap"}
    for arm in (PACK, SEARCH):
        assert _packs_of(by[(arm, 4)], "executor.packs_loaded", "packs") == list(CAP_SLUGS)
        assert _packs_of(by[(arm, 3)], "executor.packs_loaded", "packs") == list(CAP_SLUGS)
    assert pack_of(by[(SEARCH, 4)]["found_tools"][0]) == "studio-more"


def test_the_cap_chat_search_arm_finds_the_tool(cap) -> None:
    row = next(r for r in cap if r["arm"] == SEARCH and r["turn"] == 4)
    for name in ("read_the_skills", "found_tool", "search_round", "drafts_listed_fairly"):
        assert row["checks"][name] is True, name
    assert row["searches"] == 1 and row["find_tools_offered"] is True
    assert row["work_tools"] == ["studio_skill_list"]
    assert {"no_studio_ran", "says_it_cannot", "no_load_offered"}.isdisjoint(row["checks"])


def test_the_cap_chat_pack_arm_cannot_reach_studio(cap) -> None:
    """The expected outcome, from the real scripted run: no loader is offered, so no Studio
    tool runs and the answer says that it cannot."""
    row = next(r for r in cap if r["arm"] == PACK and r["turn"] == 4)
    for name in ("no_studio_ran", "no_load_offered", "says_it_cannot", "lists_no_skill"):
        assert row["checks"][name] is True, name
    assert row["work_tools"] == [] and row["found_tools"] == [] and row["searches"] == 0
    assert row["find_tools_offered"] is False
    assert {"found_tool", "search_round", "read_the_skills"}.isdisjoint(row["checks"])
    assert "cannot" in row["answer"]
    assert all(r["passed"] for r in cap)


def test_a_check_with_arms_runs_on_those_arms_only() -> None:
    from evals.one_assistant_measure.scenarios import Check, Turn

    turn = Turn("x", (Check("answer_all", (r"a",), "only_search", arms=(SEARCH,)),), None)  # type: ignore[arg-type]
    assert "only_search" in ck.check_turn(turn, _obs(answer="a"))
    assert "only_search" not in ck.check_turn(turn, _obs(arm=PACK, answer="a"))


def _cap_run(arms: tuple[str, ...] = (PACK, SEARCH), edit: Any = None) -> tuple[list, list]:
    import dataclasses

    sc = scenario("stu_cap")
    if edit is not None:
        sc = dataclasses.replace(sc, turns=tuple(edit(sc.turns)))
    return run.run_scripted((sc,), arms, None)


def test_mutation_h_two_packs_by_words_leaves_room_and_the_proof_goes_red() -> None:
    """Turn 3 (reminders) is dropped, so the run holds 2 packs and the loader offers."""
    rows, _ = _cap_run(edit=lambda turns: (*turns[:2], turns[3]))
    problems = _cap_problems(rows)
    assert problems and any("offers" in p for p in problems), problems
    assert any("not 3 packs" in p for p in problems), problems


def test_mutation_i_turn_4_without_find_tools_goes_red() -> None:
    import dataclasses

    def edit(turns: Any) -> Any:
        last = dataclasses.replace(turns[3], plan=dataclasses.replace(turns[3].plan, find=()))
        return (*turns[:3], last)

    rows, problems = _cap_run((SEARCH,), edit)
    row = next(r for r in rows if r["turn"] == 4)
    assert row["checks"]["found_tool"] is False and row["checks"]["search_round"] is False
    assert problems and not row["passed"]


def test_mutation_j_find_tools_offered_always_false_goes_red(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(run, "find_requests_of", lambda calls: 0)
    rows, _ = _cap_run()
    problems = _cap_problems(rows)
    assert any("find_tools was not in the request" in p for p in problems), problems
    mine = [r for r in rows if r["arm"] == SEARCH]
    assert mine and not any(r["find_tools_offered"] for r in mine)  # a reader would be misled


def test_mutation_k_an_empty_search_flag_on_the_cap_chat_goes_red(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real = run.flag_for
    monkeypatch.setattr(run, "flag_for", lambda sc, arm: {**real(sc, arm), "tool_search_orgs": ""})
    rows, problems = _cap_run()
    row = next(r for r in rows if r["arm"] == SEARCH and r["turn"] == 4)
    assert row["find_tools_offered"] is False and row["found_tools"] == []
    assert row["checks"]["found_tool"] is False
    assert problems and _cap_problems(rows)


def test_the_cap_comment_and_the_docs_name_the_design() -> None:
    src = (ROOT / "evals" / "one_assistant_measure" / "scenarios_studio.py").read_text(
        encoding="utf-8"
    )
    assert "THE CASE THAT THE TOOL-SEARCH DESIGN EXISTS FOR" in src
    readme = (ROOT / "evals" / "one_assistant_measure" / "README.md").read_text(encoding="utf-8")
    assert "stu_cap" in readme and "offered but not called" in readme
    spec = (ROOT / "project-docs" / "specs" / "one_assistant_context.md").read_text(
        encoding="utf-8"
    )
    assert "**Cap turn.**" in spec


# ── TS-5b fix: the fair check reads a Drafts heading ─────────────────────────


def test_a_live_skill_as_a_bare_bullet_under_a_drafts_heading_fails() -> None:
    assert not _fair("Drafts:\n- Weekly digest writer\n- Meeting notes tidy\n- Quote follow-up")
    assert not _fair(
        "Your drafts are:\n1. Weekly digest writer\n2. Meeting notes tidy\n3. Quote follow-up"
    )


def test_a_live_skill_under_its_own_published_heading_passes() -> None:
    assert _fair(
        "Drafts:\n- Weekly digest writer\n- Meeting notes tidy\n\nPublished:\n- Quote follow-up"
    )
    assert _fair(
        "Drafts:\n- Weekly digest writer\n- Meeting notes tidy\nPublished:\n- Quote follow-up"
    )
    assert _fair(
        "Two of your three skills are still drafts:\n- Weekly digest writer\n"
        "- Meeting notes tidy\nQuote follow-up is already published."
    )
    assert _fair(PILOT)
    assert _fair(
        "Quote follow-up is not a draft. Drafts: Weekly digest writer, Meeting notes tidy."
    )


def test_mutation_the_heading_rule_removed_lets_the_bare_bullet_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    text = "Drafts:\n- Weekly digest writer\n- Meeting notes tidy\n- Quote follow-up"
    assert not _fair(text)
    monkeypatch.setattr(ck, "_named_under_draft_heading", lambda answer, live: False)
    assert _fair(text)  # red: the false pass is back
