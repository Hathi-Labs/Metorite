"""Task 1747: a pack that the chat used in its last turn stays, and a turn says
which packs left.

Production, thread a398b43e, 2026-11-05 UTC. The member saw 'Function
"create_task" not found.' (and the same for five other Projects tools). The
model called a tool that the run did not hold: ``choose`` picks the packs again
at the start of each member turn, a pack that held the tool left, and nothing
told the model. The ``executor.packs_loaded`` lines of the journal give the
rows of this table.

======== ============== ====================================================
Time     Tool           Cause
======== ============== ====================================================
16:22:53 create_task    The cap. "email" and "sales pipeline" loaded email and
                        CRM, and ``projects`` left. Its reload refused on the
                        cap, and the chat handed off.
16:37:28 create_task    The cap. Email and bulk came by words, ``projects``
                        left, and the run offered no load tool.
16:47:47 create_tasks   The step-aside moved ``projects-bulk`` out.
17:02:24 update_status  The step-aside moved ``projects-structure`` out.
17:05:02 set_status_set The step-aside.
17:05:53 create_status  A load added the structure pack in the last turn, no
                        tool of it ran, so it was not sticky.
17:08:16 delete_status  The step-aside.
17:16:47 create_status  The structure pack was not held, and its reload
                        refused on the cap.
======== ============== ====================================================

The name of a test of a row starts with ``test_row_<time>``. The 17:16:47
test runs the chain from 17:05:02 to 17:16:47 turn by turn.

What this file proves:

1. A pack that this turn named, and a pack that the last member turn used,
   never step aside (``tool_packs._step_aside``). An idle pack that this turn
   did not name can still step aside.
2. The reach of the Projects owner counts a pack that a load can move out as
   free (``plan_loader``, *spare*). So the specialist and its route line leave.
   When the promise breaks in the turn, the failed load reaches the Projects
   assistant through ``via_failed`` and ``LOAD_NO_PATH``.
3. D169 item 8, as amended on 2026-11-06 (owner, option A): an explicit load
   moves out a pack that came in only by the words, with no tool of it started
   (``PackRun.spare``). A pack of the surface or of a reference stays.
4. A load by the model makes its pack sticky for the next turn
   (``LoadSkillTools._load`` calls ``note_used``).
5. A turn that a sticky pack left at its start says so in its context
   (``tool_packs.left_line``, ``executor._pack_left_block``). It tells the
   model to load the pack first when the loader offers it.

Each case names, in its docstring, the mutation that turns it red (R7).

Run: ``uv run pytest tests/unit/test_pack_turn_keeps_used_packs.py -q``.
"""
from __future__ import annotations

import json
from typing import Any

import pytest

pytest.importorskip("agent_framework", reason="MAF not installed")

from acb_skills import agent_tools
from acb_skills import skill_families as sf
from acb_skills import tool_packs as tp
from orchestrator import _tool_injection as ti
from orchestrator import executor as ex

from tests.unit._egress_mark_store import install_memory_mark_store
from tests.unit._native_maf_harness import ScriptedModel, text_turn, tool_turn
from tests.unit.test_load_skill_tools import (
    CONFIG,
    MEMBER,
    ORG,
    SESSION,
    _bound,
    _call,
    _drive,
    _dummies,
    _flag,
    _names,
    _provider,
    _run_ctx,
    _tenant,
    _tool_names,
    _tool_result,
    orchestrator,  # noqa: F401 - a pytest fixture
)
from tests.unit.test_loader_replaces_handoff import _base, _fake_run_agent
from tests.unit.test_pack_room import _frame, _projects_real, _stub_owners

EVERY = frozenset(sf.pack_slugs())
PA = "projects-assistant"
#: The specialist tools that the real base holds, one for each owner.
SPECIALISTS = {PA: 1, "email-assistant": 1, "crm-assistant": 1}
#: The base of the orchestrator on production (``base_tools``). A test build
#: of the agent counts fewer tools, and at that count a third pack still fits.
PROD_BASE = 56
#: The base of a real run with the load tool (``test_pack_room``: about 56).
BASE = PROD_BASE + 1
CORE, BULK, PLAN, STRUCTURE = (
    "projects", "projects-bulk", "projects-plan", "projects-structure")
#: Words with no pack word, so only a sticky pack or the surface loads a pack.
NO_WORD = "now rename Doing to In progress"
#: The words of 16:22:53: they name the email pack and the CRM pack.
EMAIL_CRM = "add this email to the sales pipeline"
#: The words of 16:37:28: they name the email pack and the bulk pack.
EMAIL_BULK = "email them and create tasks for each deal"
#: Words of the turn after "plan the sprint", with no pack word.
TIDY = "now tidy them up"
#: The same turn, with the words of the bulk pack.
ARCHIVE = "now archive the done ones"
#: The words of the review of round 1: they name the core pack.
A_TASK = "create a task for Ravi to call Acme"
#: The live tool counts of the journal at 16:22:53 and 16:37:28.
LIVE_1622, LIVE_1637 = 95, 108


@pytest.fixture
def store(monkeypatch) -> Any:
    """A fresh in-memory tenant Redis for the sticky packs."""
    return install_memory_mark_store(monkeypatch)


@pytest.fixture(autouse=True)
def _held_card_writes_on(monkeypatch) -> None:
    """The cap reads the member's words with the RX-1 switch on, as the rows
    did. The step-aside reads the words for each org."""
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "held_card_writes_orgs", "*")


def _st(slug: str, used: int, place: int) -> tp.Sticky:
    return tp.Sticky(slug=slug, first=(1, place), used=used)


def _turn(words: str, *sticky: tp.Sticky, turn: int = 10, surface: str = "") -> tp.PackTurn:
    return tp.PackTurn(ORG, SESSION, tp._member_part(MEMBER), turn, sticky, words, True,
                       surface=surface)


def _choose(turn: tp.PackTurn) -> tp.Choice:
    return tp.choose(turn, allowed=EVERY, base_tools=BASE, specialists=SPECIALISTS)


def _tools(choice: tp.Choice) -> set[str]:
    return {name for slug in choice.slugs for name in sf.SKILL_FAMILIES[slug]["tools"]}


def _label(slug: str) -> str:
    return str(sf.SKILL_FAMILIES[slug]["label"])


def _size(slug: str) -> int:
    return len(sf.SKILL_FAMILIES[slug]["tools"])


def _plan(turn: tp.PackTurn, choice: tp.Choice, current: int | None = None) -> tp.LoaderPlan:
    """The plan of the run, as ``_apply_tool_packs`` asks it (*spare* included)."""
    run = tp.PackRun.of(turn, choice, [(s, False) for s in choice.slugs])
    total = BASE + sum(_size(s) for s in choice.slugs) if current is None else current
    return tp.plan_loader(EVERY, choice.slugs, current=total, removable={PA: 1},
                          spare={s: _size(s) for s in run.spare()})


# ── 1. The step-aside keeps a pack that the last turn used ──────────────────


def test_row_164747_create_tasks_the_bulk_pack_that_ran_last_turn_does_not_step_aside() -> None:
    """The bulk pack ran ``create_tasks`` in turn 9. In turn 10 the member says
    "sprint", which names the core pack. Before the fix the bulk pack stepped
    aside, and ``create_tasks`` was gone. Now neither pack steps aside, and the
    owner is reached through the bulk pack, which a load can move out.
    Mutation: drop ``fresh`` from ``_step_aside`` -> the bulk pack leaves ->
    red."""
    turn = _turn("and the same for the next sprint", _st(CORE, 8, 0), _st(BULK, 9, 1))
    choice = _choose(turn)
    assert choice.slugs == (CORE, BULK) and "create_tasks" in _tools(choice), choice
    assert choice.evicted == () and choice.skipped == (), choice
    assert PA in _plan(turn, choice).via_loader


def test_row_170224_update_status_the_structure_pack_that_ran_last_turn_does_not_step_aside() -> None:
    """The member is on the Projects page, so the surface names the core pack.
    The structure pack ran in turn 9. Before the fix it stepped aside at 79
    tools, far under the cap. Mutation: drop ``fresh`` from ``_step_aside`` ->
    red."""
    turn = _turn(NO_WORD, _st(CORE, 8, 0), _st(STRUCTURE, 9, 1), surface="projects")
    choice = _choose(turn)
    assert choice.slugs == (CORE, STRUCTURE) and "update_status" in _tools(choice), choice
    assert choice.evicted == (), choice
    assert PA in _plan(turn, choice).via_loader


def test_row_170502_set_status_set_the_structure_pack_stays_after_the_cap() -> None:
    """Three sticky packs and the cap. The cap moves the old ``crm-records``
    out first, and then the step-aside runs on the rest. The structure pack
    ran in turn 9, so it stays. Mutation: drop ``fresh`` from ``choose`` ->
    the structure pack leaves -> red."""
    choice = _choose(_turn(NO_WORD, _st("crm-records", 6, 0), _st(CORE, 8, 1),
                           _st(STRUCTURE, 9, 2), surface="projects"))
    assert STRUCTURE in choice.slugs and "set_status_set" in _tools(choice), choice
    assert "crm-records" in choice.evicted, choice


def test_row_170816_delete_status_the_structure_pack_stays_when_the_words_name_the_core() -> None:
    """The words name the core pack ("task board"), not the structure pack.
    Both ran in the last turn, so neither steps aside, and a load can move the
    structure pack out. Mutation: step aside a pack that the last turn used ->
    the structure pack leaves -> red."""
    turn = _turn("delete the Blocked column from the task board",
                 _st(CORE, 9, 0), _st(STRUCTURE, 9, 1))
    choice = _choose(turn)
    assert STRUCTURE in choice.slugs and "delete_status" in _tools(choice), choice
    assert CORE in choice.slugs, "both packs ran last turn, so neither steps aside"
    assert choice.evicted == (), choice
    assert PA in _plan(turn, choice).via_loader


def test_a_core_pack_that_the_member_named_never_steps_aside() -> None:
    """The review of round 1. A plan tool ran in turn 9. In turn 10 on the
    Projects page the member says "create a task". Before the fix the run kept
    only the plan pack, put ``projects`` in ``skipped``, and the first request
    held no ``create_task``. Mutation: let the core pack step aside when this
    turn named it -> red."""
    turn = _turn(A_TASK, _st(PLAN, 9, 0), surface="projects")
    choice = _choose(turn)
    assert choice.slugs == (PLAN, CORE) and "create_task" in _tools(choice), choice
    assert choice.skipped == () and choice.evicted == (), choice
    assert PA in _plan(turn, choice).via_loader


def test_an_idle_projects_pack_can_still_step_aside() -> None:
    """The second sentence of the rule: a pack that was idle for longer than
    one turn still steps aside when the loader needs room to reach the owner.
    Mutation: protect each sticky pack -> red."""
    choice = _choose(_turn(NO_WORD, _st(CORE, 8, 0), _st(STRUCTURE, 7, 1), surface="projects"))
    assert choice.evicted == (STRUCTURE,), choice
    plan = tp.plan_loader(EVERY, choice.slugs, current=BASE + 23)
    assert PA in plan.via_loader


# ── 2. A load by the model is sticky for the next turn ──────────────────────


async def _begin(words: str, surface: str = "") -> tp.PackTurn:
    with _tenant():
        return await tp.begin_turn(ORG, SESSION, MEMBER, words, surface)


async def _note(turn: tp.PackTurn, choice: tp.Choice, slug: str) -> None:
    """A tool of pack *slug* ran in the run of *turn*."""
    run = tp.PackRun.of(turn, choice, [(s, False) for s in choice.slugs])
    with _tenant():
        assert await tp.note_used(slug, {tp.PACKS_KEY: run})


async def _load(turn: tp.PackTurn, choice: tp.Choice, slug: str) -> str:
    """The model calls ``load_skill_tools`` for *slug* in the run of *turn*."""
    run = tp.PackRun.of(turn, choice, [(s, False) for s in choice.slugs])
    key = "key-1747"
    loader = tp.LoadSkillTools(offered=(slug,), run_key=key, record=run,
                               build=ti._build_pack_for_load)
    with _tenant(), _bound(**{**_run_ctx(), tp.PACK_RUN_KEY: key, tp.PACKS_KEY: run}):
        answer, _passed = await _call(loader, slug, _dummies(BASE))
    return answer


async def test_row_170553_create_status_a_pack_that_the_model_loaded_is_sticky_next_turn(
    store, monkeypatch,
) -> None:
    """In turn 1 the model loads the structure pack and calls none of its
    tools. In turn 2 the chat must still hold it. Before the fix only a tool
    call made a pack sticky, so turn 2 lost ``create_status``. Mutation:
    remove the ``note_used`` call from ``LoadSkillTools._load`` -> red."""
    _stub_owners(monkeypatch)
    first = await _begin("the statuses look wrong")
    choice = _choose(first)
    assert STRUCTURE not in choice.slugs, "non-vacuity: layer (a) did not load it"
    answer = await _load(first, choice, STRUCTURE)
    assert answer.startswith(f"Loaded the {_label(STRUCTURE)} tools"), answer
    second = await _begin("now add a Blocked status")
    assert [(s.slug, s.used) for s in second.sticky] == [(STRUCTURE, first.turn)]
    nxt = _choose(second)
    assert STRUCTURE in nxt.slugs and "create_status" in _tools(nxt), nxt


async def test_row_171647_create_status_the_structure_pack_stays_held_over_the_chain(
    store, monkeypatch,
) -> None:
    """The chain of 17:05:02 to 17:16:47, turn by turn, on the Projects page.
    The core pack is sticky from an old turn. The model loads the structure
    pack (17:05:02), calls ``create_status`` (17:05:53) and ``delete_status``
    (17:08:16), and asks for ``create_status`` again (17:16:47). Each turn
    holds the structure pack, so no reload has to fit. Mutation: drop
    ``fresh`` or the sticky load -> a turn loses the pack -> red."""
    _stub_owners(monkeypatch)
    seed = await _begin("show the board", "projects")
    seeded = _choose(seed)
    assert CORE in seeded.slugs
    await _note(seed, seeded, CORE)
    for _ in range(3):  # idle turns: the core pack ages
        await _begin("thanks")
    t1 = await _begin(NO_WORD, "projects")
    c1 = _choose(t1)
    await _load(t1, c1, STRUCTURE)
    for words in ("add a Blocked status", "delete the Blocked status", "add it back"):
        turn = await _begin(words, "projects")
        choice = _choose(turn)
        assert STRUCTURE in choice.slugs, (words, choice)
        await _note(turn, choice, STRUCTURE)


# ── 3. The turn says which packs left ───────────────────────────────────────


def _record(evicted: tuple[str, ...], offered: tuple[str, ...], held: tuple[str, ...] = ()) -> tp.PackRun:
    turn = _turn(NO_WORD)
    choice = tp.Choice(slugs=held, layers={}, first={}, evicted=evicted)
    return tp.PackRun.of(turn, choice, [(s, False) for s in held], offered=offered)


def test_the_line_names_the_packs_that_left_and_the_load() -> None:
    """Modelled on ``LOAD_MADE_ROOM``. Mutation: return "" from ``left_line``
    -> red."""
    line = tp.left_line(_record((CORE, STRUCTURE), (CORE, STRUCTURE)))
    assert line.startswith("At the start of this turn, the Projects, Projects structure tools")
    assert "call load_skill_tools" in line and "projects, projects-structure" in line
    assert "Do not call" not in line


def test_the_line_tells_the_model_not_to_call_a_pack_that_no_load_offers() -> None:
    """A pack that the loader does not offer cannot come back in this turn, so
    the line must not send the model to ``load_skill_tools``. Mutation: drop
    the split by ``gone_offered`` -> red."""
    line = tp.left_line(_record((CORE,), ()))
    assert "load_skill_tools" not in line
    assert line.endswith("Do not call the Projects tools in this turn.")
    mixed = tp.left_line(_record((CORE, "crm-records"), (CORE,)))
    assert "with the pack that holds it: projects." in mixed
    assert mixed.endswith("Do not call the CRM records tools in this turn.")


def test_a_turn_with_no_pack_that_left_has_no_line() -> None:
    """A pack that the turn still holds is no pack that left. Mutation: name
    each evicted slug -> red."""
    assert tp.left_line(_record((), (CORE,))) == ""
    assert tp.left_line(_record((CORE,), (CORE,), held=(CORE,))) == ""
    assert tp.left_line(None) == "" and tp.left_line({"gone": (CORE,)}) == ""


def test_the_block_reads_only_the_record_of_the_orchestrator_run() -> None:
    """A delegated run drops the record, so it gets no line. Another agent
    gets none either. Mutation: read the record for each agent -> red."""
    record = _record((CORE,), (CORE,))
    with _bound(**{**_run_ctx(), tp.PACKS_KEY: record}):
        assert ex._pack_left_block("orchestrator") == tp.left_line(record) != ""
        assert ex._pack_left_block("projects-assistant") == ""
        assert tp.left_line(record) in ex._context_preamble_parts({}, {}, "orchestrator")
    with _bound(**_run_ctx()):
        assert ex._pack_left_block("orchestrator") == ""


async def _apply(agents: list[Any], words: str, surface: str = "") -> Any:
    from orchestrator.agents import PACK_DELEGATION_BULLETS

    with _tenant():
        turn = await tp.begin_turn(ORG, SESSION, MEMBER, words, surface)
        return await ti._apply_tool_packs(
            agents, turn, _run_ctx(), allowed=EVERY, agent_name="orchestrator",
            tool_scope=list(CONFIG["tool_scope"]), agent_config=CONFIG,
            bullets=PACK_DELEGATION_BULLETS, offer_loader=True,
        )


async def _seed(*turns: tuple[str, ...]) -> None:
    """Earlier member turns of this chat. Each one names the packs that ran."""
    for ran in turns:
        turn = await _begin("an earlier turn")
        choice = tp.Choice(slugs=ran, layers={}, first={})
        for slug in ran:
            await _note(turn, choice, slug)


def _assert_reached(agents: list[Any], applied: Any) -> None:
    """The owner is reached: no specialist tool, no route line, a loader."""
    assert applied is not None
    assert PA in applied.record.via_loader, applied.record
    opts = agents[0].default_options
    assert "projects_assistant" not in _names(opts["tools"])
    assert tp._PROJECTS_ROUTE_LINE not in opts["instructions"]
    assert _provider(agents[0]) is not None, "the run holds load_skill_tools"


# ── 4. The reach through the real assembly ──────────────────────────────────


@pytest.mark.parametrize(("seeds", "words", "surface", "want"), [
    (((CORE, PLAN),), TIDY, "projects", {CORE, PLAN}),
    (((CORE, PLAN),), TIDY, "", {CORE, PLAN}),
    (((CORE, PLAN),), ARCHIVE, "projects", {CORE, BULK}),
    (((CORE, PLAN),), ARCHIVE, "", {PLAN, BULK}),
    (((CORE,), (), (STRUCTURE,)), NO_WORD, "projects", {CORE, STRUCTURE}),
    (((CORE, STRUCTURE),), "delete the Blocked column from the task board", "",
     {CORE, STRUCTURE}),
    (((PLAN,),), A_TASK, "projects", {PLAN, CORE}),
], ids=["turn19-tidy-page", "turn19-tidy", "turn19-archive-page", "turn19-archive",
        "row-170224", "row-170816", "named-core"])
async def test_the_owner_is_reached_and_no_specialist_or_route_line_stays(
    seeds: tuple[tuple[str, ...], ...], words: str, surface: str, want: set[str],
    store, monkeypatch, orchestrator,  # noqa: F811
) -> None:
    """BLOCKING finding of round 1. Two Projects packs fill the room, and
    neither may step aside. PJP-2's measure is 0 hand-offs a day, so the owner
    must still be reached: no ``projects_assistant`` tool and no route line
    (the fault of 2026-11-02). Mutation: read the reach with no spare pack in
    ``plan_loader`` -> the specialist stays -> red."""
    _stub_owners(monkeypatch)
    _base(monkeypatch, PROD_BASE)
    await _seed(*seeds)
    applied = await _apply(orchestrator, words, surface)
    slugs = applied.record.slugs
    assert set(slugs) == want, slugs
    total = BASE + sum(_size(s) for s in slugs)
    without = tp.plan_loader(EVERY, slugs, current=total, removable={PA: 1})
    assert PA not in without.via_loader, "non-vacuity: only a spare pack reaches the owner"
    _assert_reached(orchestrator, applied)
    assert tp._PROJECTS_LOAD_LINE in orchestrator[0].default_options["instructions"]
    if words == A_TASK:
        assert "create_task" in _names(orchestrator[0].default_options["tools"])


def test_the_reach_of_any_other_owner_does_not_count_a_spare_pack() -> None:
    """Only an owner of ``SPECIALIST_STAYS_UNTIL_REACHED`` reaches on a promise.
    Mutation: use the spare discount in the first branch of ``plan_loader`` ->
    ``email-assistant`` is reached -> red."""
    held = ("email", "crm-records")
    current = BASE + 41
    plan = tp.plan_loader(EVERY, held, current=current, spare={"email": 31})
    assert "email-assistant" not in plan.via_loader
    assert PA in plan.via_loader


# ── 5. D169 item 8 as amended: a load outranks a words-only pack ────────────


async def _row_run(
    turn: tp.PackTurn, live_tools: int,
) -> tuple[tp.PackRun, list[Any], tp.LoadSkillTools, tp.LoaderPlan]:
    """The run of a row at the tool count of the journal, as
    ``_apply_tool_packs`` builds it: the choice, the plan and the record."""
    choice = _choose(turn)
    plan = _plan(turn, choice, live_tools)
    key = "key-1747-row"
    run = tp.PackRun.of(turn, choice, [(s, False) for s in choice.slugs], plan.via_loader,
                        offered=plan.offered)
    ctx = {**_run_ctx(), tp.PACK_RUN_KEY: key}
    tools: list[Any] = []
    with _tenant():
        for slug in choice.slugs:
            build = await ti._pack_tools(slug, ctx, holds=())
            tools.extend(ti._as_agent_tools(list(build.tools)))
    live = [*_dummies(live_tools - len(tools)), *tools]
    loader = tp.LoadSkillTools(offered=plan.offered, run_key=key, record=run,
                               build=ti._build_pack_for_load)
    return run, live, loader, plan


async def test_row_162253_create_task_loads_in_the_main_chat_and_a_words_pack_gives_way(
    store, monkeypatch,
) -> None:
    """The words "email" and "sales pipeline" loaded email and CRM, and the cap
    moved ``projects`` out. Before the fix the reload refused on the cap
    (95 + 23 > 110), and the chat handed off. Now the last words-only pack
    gives way, ``create_task`` joins, both caps hold, and the hand-off stays
    refused. Mutation: drop the words packs from ``PackRun.spare`` -> the load
    answers LOAD_NO_PATH -> red."""
    _stub_owners(monkeypatch)
    turn = _turn(EMAIL_CRM, _st(CORE, 9, 0))
    run, live, loader, plan = await _row_run(turn, LIVE_1622)
    assert run.slugs == ("email", "crm-records") and run.words == run.slugs, run
    assert run.gone == (CORE,) and PA in plan.via_loader and CORE in plan.offered
    with _frame(run, loader.run_key):
        answer, _passed = await _call(loader, CORE, live)
    assert answer.startswith("Loaded the Projects tools"), answer
    assert "To make room, the CRM records tools left this chat." in answer
    names = set(_names(live))
    assert "create_task" in names and len(live) <= tp.MAX_REQUEST_TOOLS
    assert not names & set(sf.SKILL_FAMILIES["crm-records"]["tools"])
    assert names >= set(sf.SKILL_FAMILIES["email"]["tools"]), "only one pack had to leave"
    assert run.holds() == ("email", CORE) and len(run.holds()) <= tp.MAX_PACKS
    assert "load_skill_tools" in (run.refusal(PA) or ""), "no hand-off"


async def test_row_163728_create_task_loads_in_the_main_chat_and_the_chat_keeps_a_loader(
    store, monkeypatch, orchestrator,  # noqa: F811
) -> None:
    """Email and bulk came by the words, and the cap moved ``projects`` out. At
    108 tools the run offered no load tool, so ``load_skill_tools`` failed too.
    Now the run holds the loader, and the line sends the model to it. The load
    moves the bulk pack out, and ``create_task`` joins within both caps.
    Mutation: keep the offer as it was, with no words pack in ``spare`` -> no
    load tool -> red."""
    _stub_owners(monkeypatch)
    _base(monkeypatch, PROD_BASE)
    await _seed((CORE,))
    applied = await _apply(orchestrator, EMAIL_BULK)
    assert set(applied.record.slugs) == {"email", BULK}, applied.record.slugs
    _assert_reached(orchestrator, applied)
    assert CORE in _provider(orchestrator[0]).loader.offered
    assert applied.record.gone == (CORE,) and CORE in applied.record.gone_offered
    assert tp.left_line(applied.record) == (
        tp.PACKS_LEFT.format(labels="Projects") + tp.PACKS_LEFT_LOAD.format(slugs=CORE))

    turn = _turn(EMAIL_BULK, _st(CORE, 9, 0))
    run, live, loader, plan = await _row_run(turn, LIVE_1637)
    assert run.slugs == ("email", BULK) and CORE in plan.offered, plan
    with _frame(run, loader.run_key):
        answer, _passed = await _call(loader, CORE, live)
    assert answer.startswith("Loaded the Projects tools"), answer
    assert f"To make room, the {_label(BULK)} tools left this chat." in answer
    assert "create_task" in _names(live) and len(live) <= tp.MAX_REQUEST_TOOLS
    assert run.holds() == ("email", CORE), run.holds()


async def test_row_162253_the_assembled_turn_says_that_projects_left_and_to_load_it(
    store, monkeypatch, orchestrator,  # noqa: F811
) -> None:
    """The real assembly of the 16:22:53 turn. The line tells the model to load
    the core pack before ``create_task``. Mutation: drop ``offered=`` from the
    record in ``_apply_tool_packs`` -> the line says not to call the tools ->
    red."""
    _stub_owners(monkeypatch)
    _base(monkeypatch, PROD_BASE)
    await _seed((CORE,))
    applied = await _apply(orchestrator, EMAIL_CRM)
    assert set(applied.record.slugs) == {"email", "crm-records"}, applied.record.slugs
    _assert_reached(orchestrator, applied)
    assert applied.record.gone == (CORE,) and CORE in applied.record.gone_offered
    assert tp.left_line(applied.record) == (
        tp.PACKS_LEFT.format(labels="Projects") + tp.PACKS_LEFT_LOAD.format(slugs=CORE))


async def test_row_171647_create_status_a_reload_that_was_refused_now_loads(
    store, monkeypatch,
) -> None:
    """The refused reload of 17:16:47 (102 + 24 > 110). The member is on the
    Projects page and asks for a burndown, so the words bring the plan pack in.
    The structure pack is not held. Now the plan pack gives way, the core pack
    of the surface stays, and ``create_status`` joins. Mutation: drop the words
    packs from ``PackRun.spare`` -> the load answers LOAD_NO_PATH -> red."""
    _stub_owners(monkeypatch)
    turn = _turn("show the burndown", surface="projects")
    run, live, loader, plan = await _row_run(turn, 102)
    assert run.slugs == (CORE, PLAN) and run.words == (PLAN,), run
    assert STRUCTURE in plan.offered and PA in plan.via_loader, plan
    with _frame(run, loader.run_key):
        answer, _passed = await _call(loader, STRUCTURE, live)
    assert answer.startswith(f"Loaded the {_label(STRUCTURE)} tools"), answer
    assert "create_status" in _names(live) and len(live) <= tp.MAX_REQUEST_TOOLS
    assert run.holds() == (CORE, STRUCTURE), run.holds()


def test_a_pack_of_the_surface_or_a_reference_is_not_spare() -> None:
    """The amendment: "A pack the member named by surface stays". A link is an
    explicit name too. Mutation: read every new pack as spare -> red."""
    turn = _turn("show my crm deals", surface="projects")
    choice = _choose(turn)
    assert choice.layers[CORE] == "surface" and choice.layers["crm-records"] == "words"
    run = tp.PackRun.of(turn, choice, [(s, False) for s in choice.slugs])
    assert run.spare() == ("crm-records",)
    link = _turn("read /email?email=m-1")
    held = _choose(link)
    assert held.layers["email"] == "reference"
    assert tp.PackRun.of(link, held, [(s, False) for s in held.slugs]).spare() == ()


def test_a_sticky_pack_that_the_words_name_again_is_not_spare() -> None:
    """It came into the turn by the chat as well as by the words, so it did not
    come in ONLY by the words. Mutation: add each words match to ``words`` ->
    red."""
    turn = _turn("show my crm deals", _st("crm-records", 9, 0))
    choice = _choose(turn)
    run = tp.PackRun.of(turn, choice, [(s, False) for s in choice.slugs])
    assert "crm-records" in run.slugs and run.spare() == ()


def test_the_spare_order_is_old_sticky_packs_then_words_packs_last_first() -> None:
    """Mutation: put the words packs first -> a pack of this turn leaves before
    an old one -> red."""
    turn = _turn(EMAIL_CRM, _st(BULK, 5, 0))
    choice = tp.Choice(slugs=(BULK, "email", "crm-records"),
                       layers={BULK: "sticky", "email": "words", "crm-records": "words"},
                       first={})
    run = tp.PackRun.of(turn, choice, [(s, False) for s in choice.slugs])
    assert run.spare() == (BULK, "crm-records", "email")


@pytest.mark.parametrize("how", ["started", "ran"])
def test_a_words_pack_with_a_tool_that_started_or_ran_stays(how: str) -> None:
    """The amendment: "A pack whose tool ran or started stays". Mutation: drop
    ``ran`` or ``noted`` from ``spare`` -> red."""
    turn = _turn(EMAIL_CRM, _st(CORE, 9, 0))
    choice = _choose(turn)
    run = tp.PackRun.of(turn, choice, [(s, False) for s in choice.slugs])
    assert "crm-records" in run.spare()
    if how == "started":
        tp.pack_tool_runs("crm-records", {tp.PACKS_KEY: run})
    else:
        run.noted.add("crm-records")
    assert "crm-records" not in run.spare() and "email" in run.spare()


def test_off_a_words_pack_is_not_spare(monkeypatch) -> None:
    """D169 item 13: with the switch off, a load moves no pack. Mutation: read
    the words packs outside the switch -> red."""
    from acb_common import get_settings

    turn = _turn(EMAIL_CRM, _st(CORE, 9, 0))
    choice = _choose(turn)
    run = tp.PackRun.of(turn, choice, [(s, False) for s in choice.slugs])
    assert run.spare(), "non-vacuity"
    monkeypatch.setattr(get_settings(), "held_card_writes_orgs", "")
    assert run.spare() == ()


# ── 6. When the promise breaks, the failed load reaches the assistant ───────


@pytest.mark.parametrize(("words", "sticky", "surface", "live_tools", "started", "load", "pad"), [
    (EMAIL_CRM, ((CORE, 9),), "", LIVE_1622, ("email", "crm-records"), CORE, 0),
    (TIDY, ((CORE, 9), (PLAN, 9)), "projects", 104, (PLAN,), BULK, 0),
    (EMAIL_CRM, ((CORE, 9),), "", LIVE_1622, (), CORE, 60),
], ids=["words-pack-started", "fresh-spare-pack-started", "no-room-after-the-move"])
async def test_a_load_that_cannot_keep_the_promise_reaches_the_projects_assistant(
    words: str, sticky: tuple[tuple[str, int], ...], surface: str, live_tools: int,
    started: tuple[str, ...], load: str, pad: int, store, monkeypatch,
) -> None:
    """The safety net of the reach. The plan counted a spare pack as free, and
    the specialist left. Then a tool of that pack starts, so it may not leave,
    or the room does not fit after it leaves. The load refuses with
    LOAD_NO_PATH, nothing moves, and ``call_agent`` reaches the Projects
    assistant. Mutation: drop ``via_failed`` from ``_admit`` -> the hand-off
    refuses too, and the chat has no path -> red."""
    _stub_owners(monkeypatch)
    ran = _fake_run_agent(monkeypatch)
    entries = [_st(slug, used, i) for i, (slug, used) in enumerate(sticky)]
    turn = _turn(words, *entries, surface=surface)
    run, live, loader, plan = await _row_run(turn, live_tools)
    assert PA in plan.via_loader and load in plan.offered, plan
    assert "load_skill_tools" in (run.refusal(PA) or ""), "non-vacuity: the hand-off is shut"
    for slug in started:
        tp.pack_tool_runs(slug, {tp.PACKS_KEY: run})
    live.extend(_dummies(pad, "extra"))
    before = _names(live)
    with _frame(run, loader.run_key):
        answer, _passed = await _call(loader, load, live)
        out = await agent_tools.call_agent(PA, "Make the task")
    assert answer == tp.LOAD_NO_PATH.format(label=_label(load), slug=load, owner=PA), answer
    assert _names(live) == before and run.left == set()
    assert PA in run.via_failed and run.refusal(PA) is None
    assert ran == [PA] and "ran" in out


# ── 7. The whole turn, with a scripted model ────────────────────────────────


def test_row_162253_a_real_run_loads_create_task_with_no_hand_off(store, monkeypatch) -> None:
    """THE acceptance, in a real stream run of the orchestrator. The first
    request holds no ``create_task`` and no specialist, and its context names
    the pack that left and the load. The model loads the pack. The second
    request holds ``create_task``, within the cap. Mutation: drop the block
    from ``_context_preamble_parts`` -> red. Mutation: drop the words packs
    from ``PackRun.spare`` -> the load refuses -> red."""
    import asyncio

    _flag(monkeypatch, True)
    _projects_real(monkeypatch)
    asyncio.run(_seed((CORE,)))
    model = ScriptedModel([
        tool_turn(tp.LOAD_TOOL, json.dumps({"pack": CORE}), call_id="c-load"),
        text_turn("Done."),
    ])
    events = _drive(monkeypatch, model, "run-1747-cap", words=EMAIL_CRM)
    assert [e.get("type") for e in events][-1] == "RUN_FINISHED", events
    first, second = model.bodies[0], model.bodies[1]
    names = _tool_names(first)
    assert "create_task" not in names and tp.LOAD_TOOL in names
    assert "projects_assistant" not in names
    # A turn with no history sends the context of the turn in the member's
    # message, and a structured run sends it after the cache break of the
    # instructions. Both come from ``_context_preamble_parts``.
    sent = json.dumps(first["messages"])
    want = tp.PACKS_LEFT.format(labels="Projects") + tp.PACKS_LEFT_LOAD.format(slugs=CORE)
    assert sent.count(want) == 1, sent[-1200:]
    assert tp._PROJECTS_ROUTE_LINE not in sent
    later = _tool_names(second)
    assert "create_task" in later and len(later) <= tp.MAX_REQUEST_TOOLS
    answer = _tool_result(second, "c-load")
    assert answer.startswith("Loaded the Projects tools"), answer
    assert "To make room, the CRM records tools left this chat." in answer
