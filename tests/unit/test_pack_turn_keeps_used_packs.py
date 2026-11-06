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

1. A pack that was used in the last member turn never steps aside
   (``tool_packs._step_aside``, *fresh*). An idle pack can still step aside.
2. A load by the model makes its pack sticky for the next turn
   (``LoadSkillTools._load`` calls ``note_used``).
3. A turn that a sticky pack left at its start says so in its context
   (``tool_packs.left_line``, ``executor._pack_left_block``). It tells the
   model to load the pack first when the loader offers it.

What waits for the owner: in the rows 16:22:53 and 16:37:28 the cap moved the
Projects pack out for packs that the member's words named, and a reload does
not fit. The chat still hands off. A fix needs a load to move out a pack that
the member's words named in this turn. D169 item 8 says that such a pack stays
(``work_plan.md`` §3). Their tests here pin the line of point 3 only. A
17:16:47 that starts with a full request has the same limit.

Each case names, in its docstring, the mutation that turns it red (R7).

Run: ``uv run pytest tests/unit/test_pack_turn_keeps_used_packs.py -q``.
"""
from __future__ import annotations

import json
from typing import Any

import pytest

pytest.importorskip("agent_framework", reason="MAF not installed")

from acb_skills import skill_families as sf
from acb_skills import tool_packs as tp
from orchestrator import _tool_injection as ti
from orchestrator import executor as ex

from tests.unit._egress_mark_store import install_memory_mark_store
from tests.unit._native_maf_harness import ScriptedModel, text_turn
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
    _run_ctx,
    _tenant,
    orchestrator,  # noqa: F401 - a pytest fixture
)
from tests.unit.test_pack_room import _projects_real, _stub_owners

EVERY = frozenset(sf.pack_slugs())
PA = "projects-assistant"
#: The specialist tools that the real base holds, one for each owner.
SPECIALISTS = {PA: 1, "email-assistant": 1, "crm-assistant": 1}
#: The base of a real run with the load tool (``test_pack_room``: about 56).
BASE = 57
CORE, BULK, STRUCTURE = "projects", "projects-bulk", "projects-structure"
#: Words with no pack word, so only a sticky pack or the surface loads a pack.
NO_WORD = "now rename Doing to In progress"
#: The words of 16:22:53: they name the email pack and the CRM pack.
EMAIL_CRM = "add this email to the sales pipeline"
#: The words of 16:37:28: they name the email pack and the bulk pack.
EMAIL_BULK = "email them and create tasks for each deal"


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


# ── 1. The step-aside keeps a pack that the last turn used ──────────────────


def test_row_164747_create_tasks_the_bulk_pack_that_ran_last_turn_does_not_step_aside() -> None:
    """The bulk pack ran ``create_tasks`` in turn 9. In turn 10 the member says
    "sprint", which names the core pack. Before the fix the bulk pack stepped
    aside, and ``create_tasks`` was gone. Now the idle core pack steps aside,
    and the loader still reaches the owner. Mutation: drop ``fresh`` from the
    first rule of ``_step_aside`` -> the bulk pack leaves -> red."""
    choice = _choose(_turn("and the same for the next sprint", _st(CORE, 8, 0), _st(BULK, 9, 1)))
    assert BULK in choice.slugs and "create_tasks" in _tools(choice), choice
    assert choice.evicted == (CORE,), choice


def test_row_170224_update_status_the_structure_pack_that_ran_last_turn_does_not_step_aside() -> None:
    """The member is on the Projects page, so the surface names the core pack.
    The structure pack ran in turn 9. Before the fix it stepped aside at 79
    tools, far under the cap. Mutation: drop ``fresh`` from the first rule ->
    red."""
    choice = _choose(_turn(NO_WORD, _st(CORE, 8, 0), _st(STRUCTURE, 9, 1), surface="projects"))
    assert STRUCTURE in choice.slugs and "update_status" in _tools(choice), choice
    assert choice.evicted == (CORE,), choice


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
    The rule of the core pack (rule 2) must skip a pack that the last turn
    used too. Mutation: drop ``fresh`` from the core rule -> the structure
    pack leaves -> red."""
    choice = _choose(_turn("delete the Blocked column from the task board",
                           _st(CORE, 9, 0), _st(STRUCTURE, 9, 1)))
    assert STRUCTURE in choice.slugs and "delete_status" in _tools(choice), choice
    assert CORE in choice.slugs, "both packs ran last turn, so neither steps aside"
    assert choice.evicted == (), choice


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


async def _apply(agents: list[Any], words: str) -> Any:
    from orchestrator.agents import PACK_DELEGATION_BULLETS

    with _tenant():
        turn = await tp.begin_turn(ORG, SESSION, MEMBER, words)
        return await ti._apply_tool_packs(
            agents, turn, _run_ctx(), allowed=EVERY, agent_name="orchestrator",
            tool_scope=list(CONFIG["tool_scope"]), agent_config=CONFIG,
            bullets=PACK_DELEGATION_BULLETS, offer_loader=True,
        )


async def _seed_core() -> None:
    """An earlier turn of this chat ran a tool of the core Projects pack."""
    first = await _begin("show the board")
    await _note(first, tp.Choice(slugs=(CORE,), layers={}, first={}), CORE)


async def test_row_162253_create_task_the_turn_says_that_projects_left_and_to_load_it(
    store, monkeypatch, orchestrator,  # noqa: F811
) -> None:
    """The cap moves the sticky core pack out for the email and CRM packs that
    the words named. The loader offers the core pack again (PJP-2 reach), so
    the line tells the model to load it before ``create_task``. D169 item 8
    keeps the named packs, so a reload that does not fit still hands off.
    Mutation: drop ``offered=`` from the record in ``_apply_tool_packs`` ->
    the line says not to call the tools -> red."""
    _stub_owners(monkeypatch)
    await _seed_core()
    applied = await _apply(orchestrator, EMAIL_CRM)
    assert applied is not None
    record = applied.record
    assert set(record.slugs) == {"email", "crm-records"}, record.slugs
    assert record.gone == (CORE,) and CORE in record.gone_offered
    line = tp.left_line(record)
    assert line == (tp.PACKS_LEFT.format(labels="Projects")
                    + tp.PACKS_LEFT_LOAD.format(slugs=CORE)), line


async def test_row_163728_create_task_the_turn_says_not_to_call_the_projects_tools(
    store, monkeypatch, orchestrator,  # noqa: F811
) -> None:
    """The cap moves the sticky core pack out for the email and bulk packs, and
    no pack fits beside them, so the run offers no load. The line must not
    send the model to a load tool that the run does not hold (the second
    fault of 16:37:28). Mutation: always add ``PACKS_LEFT_LOAD`` -> red."""
    _stub_owners(monkeypatch)
    await _seed_core()
    applied = await _apply(orchestrator, EMAIL_BULK)
    assert applied is not None
    record = applied.record
    assert set(record.slugs) == {"email", BULK}, record.slugs
    assert record.gone == (CORE,) and record.gone_offered == frozenset()
    line = tp.left_line(record)
    assert "load_skill_tools" not in line
    assert line.endswith("Do not call the Projects tools in this turn."), line


def test_row_162253_a_real_run_carries_the_line_in_its_first_request(store, monkeypatch) -> None:
    """THE acceptance of point 3 in a real stream run of the orchestrator. The
    first request holds no ``create_task``, and its context names the pack
    that left and the load. Mutation: drop the block from
    ``_context_preamble_parts`` -> red."""
    import asyncio

    _flag(monkeypatch, True)
    _projects_real(monkeypatch)
    asyncio.run(_seed_core())
    model = ScriptedModel([text_turn("Done.")])
    events = _drive(monkeypatch, model, "run-1747-cap", words=EMAIL_CRM)
    assert [e.get("type") for e in events][-1] == "RUN_FINISHED", events
    first = model.bodies[0]
    names = [t["function"]["name"] for t in first.get("tools") or []]
    assert "create_task" not in names and tp.LOAD_TOOL in names
    # A turn with no history sends the context of the turn in the member's
    # message, and a structured run sends it after the cache break of the
    # instructions. Both come from ``_context_preamble_parts``.
    sent = json.dumps(first["messages"])
    want = tp.PACKS_LEFT.format(labels="Projects") + tp.PACKS_LEFT_LOAD.format(slugs=CORE)
    assert sent.count(want) == 1, sent[-1200:]
