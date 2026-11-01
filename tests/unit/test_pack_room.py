"""RX-1 part two: one chat loads what it needs, also when the chat is full.

Owner directive of 2026-11-01: "one chat must load tool packs as needed". A
request holds 3 packs and 110 tools (``tool_packs.MAX_PACKS``,
``MAX_REQUEST_TOOLS``), and packs stay sticky. This file proves what happens
when a chat holds 3 sticky packs and the member says "remind me to call Ravi at
5". The rule, in one line: **a new need of the member never loses to an old
sticky pack**. The new pack loads in that same turn, and the sticky pack that
the member used least recently leaves.

**What today's code did.** Layer (a) (``choose``) already met the rule, and the
first group of tests pins it. Layer (b) did not: at 3 packs ``plan_loader``
offered nothing, so the run held no ``load_skill_tools`` at all, and a load past
the tool cap refused while an old sticky pack sat in the request. Layer (c)
adds no room, because MAF copied the tool list before ``before_run`` runs. The
model can still load in its first step.

Each case names, in its docstring, the mutation that turns it red (R7).

Run: ``uv run pytest tests/unit/test_pack_room.py -q``.
"""
from __future__ import annotations

import contextlib
import json
import types
from collections.abc import Iterator
from typing import Any

import pytest

pytest.importorskip("agent_framework", reason="MAF not installed")

from acb_skills import egress as eg
from acb_skills import skill_families as sf
from acb_skills import tool_packs as tp
from agent_framework import FunctionInvocationContext
from orchestrator import _tool_injection as ti

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
    _mark_chat,
    _names,
    _run_ctx,
    _tenant,
    _tool_names,
    _tool_result,
    orchestrator,  # noqa: F401 - a pytest fixture
)

EVERY = frozenset(sf.pack_slugs())
REMIND = "remind me to call Ravi at 5"
#: No word of any pack, so only the model can ask for the pack.
NO_WORD = "Set an alarm so that I call Ravi at 5"
REMINDERS = "projects-reminders"
PA = "projects-assistant"


@pytest.fixture
def store(monkeypatch) -> Any:
    """A fresh in-memory tenant Redis for the marks and the sticky packs."""
    return install_memory_mark_store(monkeypatch)


def _st(slug: str, used: int, place: int = 0) -> tp.Sticky:
    return tp.Sticky(slug=slug, first=(1, place), used=used)


def _sticky(*slugs: str, used_from: int = 5) -> tuple[tp.Sticky, ...]:
    """Sticky packs, the least recently used first."""
    return tuple(_st(slug, used_from + i, i) for i, slug in enumerate(slugs))


def _turn(words: str = REMIND, sticky: tuple[tp.Sticky, ...] = (), turn: int = 9,
          surface: str = "") -> tp.PackTurn:
    return tp.PackTurn(ORG, SESSION, tp._member_part(MEMBER), turn, sticky, words, True,
                       surface=surface)


def _total(choice: tp.Choice, base: int) -> int:
    return base + sum(len(sf.SKILL_FAMILIES[s]["tools"]) for s in choice.slugs)


# ── 1. Layer (a): the words and the surface ─────────────────────────────────


@pytest.mark.parametrize("held", [
    ("email", "email-setup", "crm-records"),
    ("email", "crm-records", "projects"),
    ("crm-records", "projects", "projects-plan"),
    ("email-setup", "projects-structure", "projects-bulk"),
], ids=lambda slugs: "+".join(slugs))
@pytest.mark.parametrize("words", [
    REMIND, "Remind me about the invoice", "ping me at noon", "don't let me forget the demo",
    "set a reminder for Friday",
])
def test_three_sticky_packs_lose_to_a_reminder_in_that_turn(
    held: tuple[str, ...], words: str,
) -> None:
    """THE case of the owner. The new pack loads in the same turn, the sticky
    pack that the member used least recently leaves, and both caps hold.
    Mutation: let a new pack leave before a sticky pack -> red."""
    choice = tp.choose(_turn(words, _sticky(*held)), allowed=EVERY, base_tools=56)
    assert REMINDERS in choice.slugs, choice
    assert choice.evicted and choice.evicted[0] == held[0], choice
    assert choice.skipped == () and len(choice.slugs) <= tp.MAX_PACKS
    assert _total(choice, 56) <= tp.MAX_REQUEST_TOOLS


def test_the_core_pack_on_the_surface_and_three_sticky_packs_all_give_way() -> None:
    """The example of the slice: ``email``, ``email-setup`` and ``crm-records``
    are sticky, and the turn comes from the Projects app, so the core pack
    counts too. The reminder pack still loads. Mutation: drop the new pack
    first -> red."""
    choice = tp.choose(_turn(REMIND, _sticky("email", "email-setup", "crm-records"),
                             surface="projects"), allowed=EVERY, base_tools=56)
    assert REMINDERS in choice.slugs and "projects" in choice.slugs, choice
    assert choice.evicted[0] == "email"
    assert _total(choice, 56) <= tp.MAX_REQUEST_TOOLS


@pytest.mark.parametrize("base", [70, 85, 95, 100])
def test_a_reminder_wins_when_the_tool_cap_is_close(base: int) -> None:
    """Under 3 packs, the tool cap alone must move a sticky pack out. Mutation:
    count only ``MAX_PACKS`` in ``_over`` -> over 110 -> red."""
    choice = tp.choose(_turn(REMIND, _sticky("email", "crm-records")),
                       allowed=EVERY, base_tools=base)
    assert REMINDERS in choice.slugs, choice
    assert _total(choice, base) <= tp.MAX_REQUEST_TOOLS


def test_a_sticky_pack_that_the_member_names_now_leaves_last() -> None:
    """``email`` is the oldest sticky pack, and the member says "email" in this
    turn. It is a need of this turn, so the next oldest leaves. Mutation: drop
    the first term of the sort in ``choose`` -> ``email`` leaves -> red."""
    choice = tp.choose(
        _turn("remind me to answer the email from Ravi",
              _sticky("email", "email-setup", "crm-records")),
        allowed=EVERY, base_tools=30)
    assert "email" in choice.slugs and REMINDERS in choice.slugs, choice
    assert choice.evicted == ("email-setup",), choice


def test_a_pack_that_the_flag_leaves_out_loads_nothing_and_moves_nothing() -> None:
    """``TOOL_PACKS`` must name the pack. Mutation: read the pack from the words
    alone -> a run holds a pack that the flag left out -> red."""
    allowed = EVERY - {REMINDERS}
    choice = tp.choose(_turn(REMIND, _sticky("email", "email-setup", "crm-records")),
                       allowed=allowed, base_tools=30)
    assert REMINDERS not in choice.slugs and choice.evicted == ()


# ── 2. A held chat still loads packs ────────────────────────────────────────


async def _seed_sticky(*slugs: str) -> None:
    """Earlier turns of this chat ran a tool of each pack, in this order."""
    for turn, slug in enumerate(slugs, start=1):
        run = tp.PackRun(org=ORG, session=SESSION, member_part=tp._member_part(MEMBER),
                         turn=turn, stored=True, slugs=(slug,), first={slug: (turn, 0)},
                         held={slug: False}, refusals={})
        with _tenant():
            assert await tp.note_used(slug, {tp.PACKS_KEY: run})


async def _apply(agents: list[Any], words: str, *, allowed: frozenset[str] = EVERY) -> Any:
    from orchestrator.agents import PACK_DELEGATION_BULLETS

    with _tenant():
        turn = await tp.begin_turn(ORG, SESSION, MEMBER, words)
        return await ti._apply_tool_packs(
            agents, turn, _run_ctx(), allowed=allowed, agent_name="orchestrator",
            tool_scope=list(CONFIG["tool_scope"]), agent_config=CONFIG,
            bullets=PACK_DELEGATION_BULLETS, offer_loader=True,
        )


def _live_mode(monkeypatch) -> None:
    async def crm_mode() -> str:
        return "live"

    monkeypatch.setattr("acb_skills.crm_tools.crm_mode", crm_mode)


async def test_a_held_chat_with_three_sticky_packs_loads_the_reminders_held(
    store, orchestrator, monkeypatch,  # noqa: F811
) -> None:
    """The whole report of the owner, through the real assembly: three sticky
    packs, a chat that read mail, and "remind me". ``set_reminder`` is in the
    request, the pack is held, its rules say that the write works, and the sticky
    pack that the member used least recently left. Mutation: exclude a held
    chat from layer (a) -> no pack -> red."""
    _live_mode(monkeypatch)
    await _seed_sticky("email", "email-setup", "crm-records")
    with _tenant(), _bound(**_run_ctx()):
        assert await eg.mark_untrusted_answer()
    applied = await _apply(orchestrator, REMIND)
    assert applied is not None
    record = applied.record
    assert REMINDERS in record.slugs and record.held[REMINDERS] is True
    names = set(_names(orchestrator[0].default_options["tools"]))
    assert {"set_reminder", "update_reminder", "promote_reminder"} <= names
    persona = orchestrator[0].default_options["instructions"]
    assert eg.EGRESS_HOLD_NOTE_PACK_OTHER in persona
    assert "email" not in record.slugs, record.slugs
    with _tenant():
        later = await tp.begin_turn(ORG, SESSION, MEMBER, "")
    assert "email" not in {s.slug for s in later.sticky}, "the state lost the pack too"


# ── 3. The offer at the caps ────────────────────────────────────────────────


def _plan(held: tuple[str, ...], *, current: int = 80, spare: dict[str, int] | None = None,
          allow: frozenset[str] = EVERY) -> tp.LoaderPlan:
    return tp.plan_loader(allow, held, current=current, spare=spare)


def test_a_full_run_with_no_spare_pack_offers_nothing() -> None:
    """The old rule stays for a run with no sticky pack. Three packs that the
    member asked for in this turn stay. Mutation: offer at the cap with no
    spare pack -> a load that can only refuse -> red."""
    plan = _plan(("email", "crm-records", "projects"))
    assert plan.offered == () and plan.via_loader == frozenset()


def test_a_full_run_with_a_spare_pack_offers_what_fits_once_it_leaves() -> None:
    """The fix. The run offers the rest, so the model can load. Mutation: keep
    ``pool = ()`` at the cap -> no load tool -> red."""
    plan = _plan(("email", "crm-records", "projects"), spare={"email": 31})
    assert REMINDERS in plan.offered and "email-setup" in plan.offered
    assert "email" not in plan.offered


def test_a_spare_pack_widens_the_offer_and_never_the_reach_of_an_owner() -> None:
    """No specialist leaves on the promise of a move. Mutation: run the owner
    loop over the wide list -> ``via_loader`` grows -> red."""
    held = ("email", "crm-records", "projects")
    assert _plan(held, spare={"email": 31}).via_loader == _plan(held).via_loader


def test_the_offer_counts_the_tools_that_leave() -> None:
    """The run is at 108 tools, so a pack of 21 does not fit by itself. It fits
    when the sticky pack leaves. Mutation: drop *freed* from the fit -> red."""
    held = ("email", "crm-records")
    assert "projects-bulk" not in _plan(held, current=108).offered
    assert "projects-bulk" in _plan(held, current=108, spare={"email": 31}).offered


# ── 4. Layer (b): a load that the model asks for ────────────────────────────


def _probe(name: str) -> Any:
    async def probe() -> str:
        return name

    probe.__name__ = name
    probe.__tool_risk__ = {  # type: ignore[attr-defined]
        "read_only": True, "destructive": False, "idempotent": True, "open_world": False}
    return probe


def _stub_owners(monkeypatch) -> None:
    """Each owner builds a read probe for each of its tools."""
    for owner in ("email-assistant", "crm-assistant", PA):
        names = [n for slug in tp.owner_packs(owner) for n in sf.SKILL_FAMILIES[slug]["tools"]]
        monkeypatch.setitem(
            ti._PACK_OWNER_MODULES, owner,
            types.SimpleNamespace(_agent_tools=lambda n=tuple(names): [_probe(x) for x in n]))
    monkeypatch.setattr(ti, "_PACK_OWNER_FILTERS", {})


async def _full_run(
    sticky: tuple[str, ...], *, extra: int = 20, new: tuple[str, ...] = (),
    **over: Any,
) -> tuple[tp.PackRun, list[Any], str]:
    """A run of 3 packs: the *sticky* packs from earlier turns (the oldest first)
    and the *new* packs of this turn, with their real guarded tools."""
    entries = _sticky(*sticky)
    turn = _turn(NO_WORD, entries)
    slugs = (*sticky, *new)
    choice = tp.Choice(slugs=slugs, layers=dict.fromkeys(sticky, "sticky"),
                       first={e.slug: e.first for e in entries})
    run = tp.PackRun.of(turn, choice, [(slug, False) for slug in slugs])
    key = "key-rx1"
    ctx = {**_run_ctx(**over), tp.PACK_RUN_KEY: key}
    live: list[Any] = _dummies(extra)
    with _tenant():
        for slug in slugs:
            build = await ti._pack_tools(slug, ctx, holds=())
            live.extend(ti._as_agent_tools(list(build.tools)))
    return run, live, key


def _loader(run: tp.PackRun, key: str, offered: tuple[str, ...] = (REMINDERS,)) -> tp.LoadSkillTools:
    return tp.LoadSkillTools(offered=offered, run_key=key, record=run,
                             build=ti._build_pack_for_load)


@contextlib.contextmanager
def _frame(run: tp.PackRun, key: str, **over: Any) -> Iterator[None]:
    with _tenant(), _bound(**{**_run_ctx(**over), tp.PACK_RUN_KEY: key, tp.PACKS_KEY: run}):
        yield


async def test_a_model_load_at_the_pack_cap_moves_the_oldest_sticky_pack_out(
    store, monkeypatch,
) -> None:
    """THE fix of layer (b). Mutation: refuse at ``MAX_PACKS`` as before -> red."""
    _stub_owners(monkeypatch)
    await _seed_sticky("email", "email-setup", "crm-records")
    run, live, key = await _full_run(("email", "email-setup", "crm-records"))
    loader = _loader(run, key)
    with _frame(run, key):
        answer, went_on = await _call(loader, REMINDERS, live)
    assert went_on is False and answer.startswith("Loaded the Projects reminders tools")
    assert run.holds() == ("email-setup", "crm-records", REMINDERS)
    names = set(_names(live))
    assert "set_reminder" in names, "the pack joined in the same turn"
    assert not names & set(sf.SKILL_FAMILIES["email"]["tools"]), "the sticky pack left"
    assert names >= set(sf.SKILL_FAMILIES["crm-records"]["tools"])
    assert "To make room, the Email tools left this chat." in answer
    with _tenant():
        later = await tp.begin_turn(ORG, SESSION, MEMBER, "")
    assert "email" not in {s.slug for s in later.sticky}, "the state lost it too"


async def test_a_model_load_at_the_tool_cap_moves_a_sticky_pack_out(store, monkeypatch) -> None:
    """Two packs and a full list. Only the tool cap stops the load. Mutation:
    check only the pack count in ``_room_for`` -> red."""
    _stub_owners(monkeypatch)
    await _seed_sticky("email", "crm-records")
    run, live, key = await _full_run(("email", "crm-records"), extra=70)
    assert len(live) == 110
    with _frame(run, key):
        answer, _ = await _call(_loader(run, key), REMINDERS, live)
    assert "To make room, the Email tools left this chat." in answer
    assert len(live) <= tp.MAX_REQUEST_TOOLS and "set_reminder" in _names(live)
    assert "crm-records" in run.holds()


async def test_a_load_takes_only_as_many_packs_as_it_needs(store, monkeypatch) -> None:
    """Mutation: move each spare pack out at once -> ``crm-records`` leaves -> red."""
    _stub_owners(monkeypatch)
    await _seed_sticky("email", "email-setup", "crm-records")
    run, live, key = await _full_run(("email", "email-setup", "crm-records"), extra=5)
    with _frame(run, key):
        await _call(_loader(run, key), REMINDERS, live)
    assert run.left == {"email"}


async def test_a_pack_that_ran_in_this_turn_stays(store, monkeypatch) -> None:
    """A tool of the oldest pack ran in this run, so it is no old pack now, and
    the next one leaves. Mutation: drop ``noted`` from ``spare`` -> red."""
    _stub_owners(monkeypatch)
    await _seed_sticky("email", "email-setup", "crm-records")
    run, live, key = await _full_run(("email", "email-setup", "crm-records"))
    run.noted.add("email")
    with _frame(run, key):
        answer, _ = await _call(_loader(run, key), REMINDERS, live)
    assert run.left == {"email-setup"} and "Email setup" in answer


async def test_a_pack_that_the_member_named_now_stays(store) -> None:
    """Mutation: drop ``_asked_now`` from ``PackRun.of`` -> ``crm-records`` is
    spare -> red."""
    entries = _sticky("email", "crm-records")
    turn = _turn("show my crm deals", entries)
    choice = tp.Choice(slugs=("email", "crm-records"),
                       layers=dict.fromkeys(("email", "crm-records"), "sticky"),
                       first={e.slug: e.first for e in entries})
    run = tp.PackRun.of(turn, choice, [("email", False), ("crm-records", False)])
    assert run.spare() == ("email",)


async def test_three_packs_of_this_turn_keep_the_old_refusal(store, monkeypatch) -> None:
    """No sticky pack, so no pack can leave. The load refuses as before, and its
    answer names ``spawn_subagents``. Mutation: move a pack of this turn -> red."""
    _stub_owners(monkeypatch)
    run, live, key = await _full_run((), new=("email", "crm-records", "projects"))
    before = list(live)
    with _frame(run, key):
        answer, _ = await _call(_loader(run, key), REMINDERS, live)
    assert answer == tp.LOAD_PAST_CAP.format(
        label="Projects reminders", packs=tp.MAX_PACKS, tools=tp.MAX_REQUEST_TOOLS,
        slug=REMINDERS, owner=PA)
    assert live == before and run.left == set()


async def test_a_failed_add_moves_no_pack_out(store, monkeypatch) -> None:
    """The tools join first, so a fault leaves the run as it was. Mutation: move
    the pack before ``add_tools`` -> the pack is gone and the new one is not ->
    red."""
    _stub_owners(monkeypatch)
    await _seed_sticky("email", "email-setup", "crm-records")
    run, live, key = await _full_run(("email", "email-setup", "crm-records"))
    before = list(live)

    class _Refuses(FunctionInvocationContext):
        def add_tools(self, _tools: Any) -> None:
            raise ValueError("a name clash")

    loader = _loader(run, key)

    async def call_next() -> None:
        raise AssertionError("the body must not run")

    context = _Refuses(function=loader.tool, arguments={"pack": REMINDERS}, tools=live)
    with _frame(run, key):
        await loader.process(context, call_next)
    assert live == before and run.left == set() and REMINDERS not in run.holds()


async def test_a_pack_that_left_gives_up_its_hand_off_rules(store, monkeypatch) -> None:
    """A run held both email packs, so a hand-off to the owner was refused. One
    left, so the run no longer holds every tool, and the hand-off opens. Mutation:
    read the start-of-run ``refusals`` after a pack left -> red."""
    _stub_owners(monkeypatch)
    await _seed_sticky("email", "email-setup", "crm-records")
    run, live, key = await _full_run(("email", "email-setup", "crm-records"))
    assert run.refusal("email-assistant") is not None
    with _frame(run, key):
        await _call(_loader(run, key), REMINDERS, live)
    assert run.left == {"email"} and run.refusal("email-assistant") is None


async def test_a_pack_that_left_opens_the_hand_off_that_the_loader_closed(store) -> None:
    """Mutation: drop the ``via_failed`` write of ``leaves`` -> the refusal sends
    the model to a load that is not on offer -> red."""
    entries = _sticky("email")
    turn = _turn(NO_WORD, entries)
    choice = tp.Choice(slugs=("email",), layers={"email": "sticky"},
                       first={e.slug: e.first for e in entries})
    run = tp.PackRun.of(turn, choice, [("email", False)], via_loader=("email-assistant",))
    assert "load_skill_tools" in (run.refusal("email-assistant") or "")
    run.leaves("email")
    assert run.refusal("email-assistant") is None


async def test_a_pack_that_left_is_not_noted_by_a_call_in_flight(store) -> None:
    """A call of the batch in flight still runs, and it makes the pack sticky
    no more. Mutation: read ``slugs`` in ``note_used`` -> red."""
    entries = _sticky("email")
    turn = _turn(NO_WORD, entries)
    choice = tp.Choice(slugs=("email",), layers={"email": "sticky"},
                       first={e.slug: e.first for e in entries})
    run = tp.PackRun.of(turn, choice, [("email", False)])
    run.leaves("email")
    with _tenant():
        assert await tp.note_used("email", {tp.PACKS_KEY: run}) is False


async def test_layer_c_moves_no_pack(store, monkeypatch) -> None:
    """MAF copied the tool list before ``before_run``, so a move there would not
    reach the request. The early load stays out, and the model can still call
    the load tool. Mutation: pass *spare* in ``_build_early`` -> a pack leaves ->
    red."""
    _stub_owners(monkeypatch)
    await _seed_sticky("email", "email-setup", "crm-records")
    run, live, key = await _full_run(("email", "email-setup", "crm-records"))
    loader = _loader(run, key)
    loader.set_early((REMINDERS,))
    agent = types.SimpleNamespace(default_options={"tools": live}, mcp_tools=[])
    context = types.SimpleNamespace(tools=[], extend_tools=lambda *_a: None,
                                    extend_instructions=lambda *_a: None)
    with _frame(run, key):
        await loader.load_early(agent, context)
    assert run.left == set() and REMINDERS not in run.holds()


async def test_a_held_load_at_the_cap_keeps_the_write_and_says_so(store, monkeypatch) -> None:
    """A held chat still loads, at the cap, and the guard goes with the pack.
    Mutation: skip the held note in a load -> red."""
    _stub_owners(monkeypatch)
    await _seed_sticky("email", "email-setup", "crm-records")
    run, live, key = await _full_run(("email", "email-setup", "crm-records"))
    with _tenant(), _bound(**_run_ctx()):
        assert await eg.mark_untrusted_answer()
    with _frame(run, key):
        answer, _ = await _call(_loader(run, key), REMINDERS, live)
    assert run.later[REMINDERS] is True, "the build is held"
    assert answer.count(eg.EGRESS_HOLD_NOTE_PACK_OTHER) == 1
    assert "set_reminder" in _names(live)


# ── 5. The whole turn, with a scripted model ────────────────────────────────


def _projects_real(monkeypatch) -> None:
    """The real Projects owner. The email and CRM owners build read probes, so
    the seeded sticky packs load with no gateway."""
    for owner in ("email-assistant", "crm-assistant"):
        names = [n for slug in tp.owner_packs(owner) for n in sf.SKILL_FAMILIES[slug]["tools"]]
        monkeypatch.setitem(
            ti._PACK_OWNER_MODULES, owner,
            types.SimpleNamespace(_agent_tools=lambda n=tuple(names): [_probe(x) for x in n]))
    monkeypatch.setitem(ti._PACK_OWNER_FILTERS, "crm-assistant", _no_filter)


async def _no_filter(tools: list[Any], dropped: list[str], *, held: bool = False) -> Any:
    return tools, dropped


def _seed(*slugs: str) -> None:
    import asyncio

    asyncio.run(_seed_sticky(*slugs))


def test_a_held_chat_at_the_cap_sets_the_reminder_in_that_turn(store, monkeypatch) -> None:
    """THE acceptance, in a real stream run. Three sticky packs, a chat that read
    mail, and "remind me to call Ravi at 5". The first request holds
    ``set_reminder``, the call shows one card, and the run answers with no held
    text. Mutation: put the old note back -> the assertion on the persona fails
    -> red. Mutation: take ``set_reminder`` off the list -> the tool is gone ->
    red."""
    from tests.unit._projects_agent_fakes import approve, fake_gateway

    _flag(monkeypatch, True)
    _projects_real(monkeypatch)
    _seed("email", "email-setup", "crm-records")
    _mark_chat()

    def responder(call: dict[str, Any]) -> Any:
        if call["path"].endswith("/my/today"):
            return {"timezone": "Asia/Kolkata", "day_start_hour": 9}
        if call["method"] == "POST":
            return {"id": "11111111-1111-4111-8111-111111111111", "title": "Call Ravi",
                    "fire_at": "2026-11-02T11:30:00+00:00", "tz": "Asia/Kolkata",
                    "kind": "action", "status": "open"}
        return {"rows": [], "total": 0}

    calls = fake_gateway(monkeypatch, responder, user=MEMBER)
    cards = approve(monkeypatch)
    monkeypatch.setattr("acb_skills.ask_tools.confirmation_channel_open", lambda: True)
    model = ScriptedModel([
        tool_turn("set_reminder", json.dumps({"title": "Call Ravi", "in_minutes": 30}),
                  call_id="c-remind"),
        text_turn("Done."),
    ])
    events = _drive(monkeypatch, model, "run-rx1-held", words=REMIND)
    kinds = [e.get("type") for e in events]
    assert kinds[-1] == "RUN_FINISHED" and "RUN_ERROR" not in kinds, events
    first = _tool_names(model.bodies[0])
    assert "set_reminder" in first
    assert len(first) == len(set(first)) and len(first) <= tp.MAX_REQUEST_TOOLS
    assert len(cards) == 1, cards
    assert [c["method"] for c in calls if c["path"].endswith("/my/reminders")][-1] == "POST"
    answer = _tool_result(model.bodies[1], "c-remind")
    assert "off now" not in answer and "new chat" not in answer, answer
    system = json.dumps(model.bodies[0]["messages"][0])
    assert "They can make the change in the app" not in system


def test_a_model_load_at_the_cap_works_in_a_real_run(store, monkeypatch) -> None:
    """The words load no pack, so the model asks. Three sticky packs that fit
    the real base stay in the first request. Before this slice the run held no
    ``load_skill_tools`` at 3 packs. Mutation: keep ``pool = ()`` at the cap ->
    the first request has no load tool -> red."""
    _flag(monkeypatch, True)
    _projects_real(monkeypatch)
    _seed("crm-records", "projects-bulk", "projects")
    model = ScriptedModel([
        tool_turn(tp.LOAD_TOOL, json.dumps({"pack": REMINDERS}), call_id="c-load"),
        text_turn("Done."),
    ])
    events = _drive(monkeypatch, model, "run-rx1-load", words=NO_WORD)
    assert [e.get("type") for e in events][-1] == "RUN_FINISHED", events
    first, second = _tool_names(model.bodies[0]), _tool_names(model.bodies[1])
    assert first[-1] == tp.LOAD_TOOL and "set_reminder" not in first
    assert "search_crm" in first, "non-vacuity: the run held three packs"
    assert "set_reminder" in second and "search_crm" not in second
    assert "find_tasks" in second, "only the oldest sticky pack left"
    assert "To make room, the CRM records tools left" in _tool_result(model.bodies[1], "c-load")
