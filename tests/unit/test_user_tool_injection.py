"""SBX-F7, the find half (SBX-1a, task 3800): where the no-match hook runs.

Spec: ``project-docs/specs/agent_sandbox.md`` §6.1 and §12.1 decision 1
(done-when 3 and 10). Code: ``_tool_injection._make_finder``,
``tool_gap_path.make_gap_hook`` and ``tool_gap_path.gap_hook``, and the gate
of ``executor._start_pack_turn`` and ``executor._apply_pack_turn``.

What this proves:

1. With ``user_tool_find_enabled`` off, ``_make_finder`` passes no hook. The
   no-match answer is ``FIND_NONE`` byte for byte, and nothing writes a row,
   asks the decide engine or reads the catalog. On, the hook is
   ``tool_gap_path.gap_hook``.
2. Neither ``find_tools`` nor the hook is in
   ``_collect_injectable_platform_tools()``, so no ``tool_scope`` entry can
   reach either.
3. A delegated run, an agent other than ``orchestrator``, a shared room, a
   batch run and a run with no thread start no pack turn, so they get no
   finder. A WhatsApp turn gets ``offer_loader=False``, so no finder.
4. The hook writes no row for an org off ``STUDIO_ORGS`` or
   ``TOOL_SEARCH_ORGS``, or a member without ``feature:studio``.
5. The org, the member, the thread and the run come from the run context
   only. The hook takes the query and the live list, and nothing else.

Mutations (R7), each red here: remove ``_studio_member_open`` from the hook;
pass the hook while the flag is off (``make_gap_hook`` returns ``gap_hook``
always).
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any

import pytest

pytest.importorskip("agent_framework", reason="MAF not installed")

from acb_common import get_settings
from acb_skills import tool_packs as tp
from agent_framework import FunctionInvocationContext
from orchestrator import _tool_injection as ti
from orchestrator import executor as ex
from orchestrator import tool_gap_path as tgp

from tests.unit import _tool_gap_fakes as fk

RUN_KEY = "key-sbx1a-inj"


def _record() -> tp.PackRun:
    turn = tp.PackTurn(fk.ORG, fk.THREAD, tp._member_part(fk.MEMBER), 3, (), "", True)
    choice = tp.choose(turn, allowed=frozenset(), base_tools=0)
    return tp.PackRun.of(turn, choice, [])


def _finder() -> tuple[tp.FindTools, tp.PackRun]:
    record = _record()
    applied = ti.PackApply(builds=(), record=record, run_key=RUN_KEY)
    return ti._make_finder(applied, tp.FindCandidates()), record


async def _find(finder: tp.FindTools, record: tp.PackRun, query: str) -> str:
    context = FunctionInvocationContext(function=finder.tool, arguments={"query": query},
                                        tools=[])

    async def call_next() -> None:
        raise AssertionError("the middleware took the call")

    with fk.in_run(**{tp.PACK_RUN_KEY: RUN_KEY, tp.PACKS_KEY: record}):
        await finder.process(context, call_next)
    return str(context.result)


# ── 1. The flag ────────────────────────────────────────────────────────────


async def test_flag_off_passes_no_hook_and_nothing_runs(monkeypatch) -> None:
    """Done-when 3. Mutation: ``make_gap_hook`` gives the hook with the flag
    off -> red."""
    world = fk.install(monkeypatch, rows=[fk.knowledge_row("search", "Search notes")],
                       picks={"answer": ("none", 0.9)})
    monkeypatch.setattr(get_settings(), "user_tool_find_enabled", False)
    assert tgp.make_gap_hook() is None
    finder, record = _finder()
    assert finder._on_gap is None
    assert await _find(finder, record, fk.NEED) == tp.FIND_NONE
    assert world.records == [] and world.asks == [] and world.catalog_calls == 0
    assert world.steps == [] and world.opened == 0


async def test_flag_on_passes_the_one_hook(monkeypatch) -> None:
    world = fk.install(monkeypatch, picks={"answer": ("none", 0.9)})
    assert tgp.make_gap_hook() is tgp.gap_hook
    finder, record = _finder()
    assert finder._on_gap is tgp.gap_hook
    answer = await _find(finder, record, fk.NEED)
    said = tgp.SAY_NO_MATCH.format(gap_id=world.record_result)
    assert answer == f"{tp.FIND_NONE}\n\n{said}"
    assert len(world.records) == 1


def test_a_fault_in_the_settings_read_is_off(monkeypatch) -> None:
    import acb_common

    def _boom() -> Any:
        raise RuntimeError("no settings")

    monkeypatch.setattr(acb_common, "get_settings", _boom)
    assert tgp.find_enabled() is False and tgp.make_gap_hook() is None


# ── 2. No tool_scope entry reaches either ─────────────────────────────────


@pytest.mark.parametrize("agent", [None, "orchestrator"])
def test_neither_find_tools_nor_the_hook_is_an_injectable_platform_tool(agent) -> None:
    tools = ti._collect_injectable_platform_tools(agent)
    names = {ti._tool_name(t) for t in tools}
    assert len(names) > 10, "non-vacuity: the collection holds the platform tools"
    assert tp.FIND_TOOL not in names
    assert "gap_hook" not in names and tgp.GAP_EVENT not in names
    assert all(getattr(t, "func", t) is not tgp.gap_hook for t in tools)


# ── 3. Only the member's own web turn of the orchestrator ─────────────────


def _start(monkeypatch, **over: Any) -> Any:
    monkeypatch.setattr(ex, "_verified_run_member", lambda: fk.MEMBER)
    monkeypatch.setattr(tp, "packs_on", lambda org: frozenset({"crm-records"}))
    kwargs: dict[str, Any] = {
        "agent_name": "orchestrator", "organization_id": fk.ORG, "thread_id": fk.THREAD,
        "member": fk.MEMBER, "delegated": False, "solo_room": True, "batch_thread": False,
        "shows_cards": True}
    kwargs.update(over)
    return ex._start_pack_turn(**kwargs)


@pytest.mark.parametrize("over", [
    {"delegated": True},
    {"agent_name": "crm-assistant"},
    {"solo_room": False},
    {"solo_room": None},
    {"batch_thread": True},
    {"shows_cards": False},
    {"thread_id": None},
    {"organization_id": None},
    {"member": "bob@gaps.test"},
])
async def test_no_pack_turn_and_so_no_finder_outside_the_members_own_turn(
        monkeypatch, over) -> None:
    """Done-when 10, decision 1. Non-vacuity: the member's own turn starts one."""
    own = _start(monkeypatch)
    assert own is not None
    own.cancel()
    with pytest.raises((asyncio.CancelledError, Exception)):
        await own
    assert _start(monkeypatch, **over) is None


@pytest.mark.parametrize(("read_only", "offered"), [(True, False), (False, True)])
async def test_a_whatsapp_turn_offers_no_loader_and_so_no_finder(
        monkeypatch, read_only, offered) -> None:
    seen: list[dict[str, Any]] = []

    async def _apply(*_a: Any, **kwargs: Any) -> Any:
        seen.append(kwargs)
        return None

    monkeypatch.setattr(ti, "_apply_tool_packs", _apply)
    done: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
    done.set_result((object(), frozenset({"crm-records"})))
    await ex._apply_pack_turn(done, [], {}, agent_name="orchestrator", tool_scope=None,
                              agent_config=None, read_only=read_only)
    assert [k["offer_loader"] for k in seen] == [offered]


# ── 4. The gate of the hook ───────────────────────────────────────────────


@pytest.mark.parametrize("case", ["studio_orgs", "feature", "tool_search"])
async def test_no_row_off_studio_or_tool_search_or_without_the_feature(
        monkeypatch, case) -> None:
    """Done-when 10. The real ``_studio_member_open`` runs. Mutation: remove
    ``_studio_member_open`` from the hook -> rows for the first two cases
    -> red."""
    real = ti._studio_member_open
    world = fk.install(monkeypatch, picks={"answer": ("none", 0.9)})
    monkeypatch.setattr(ti, "_studio_member_open", real)
    monkeypatch.setattr(get_settings(), "studio_orgs", fk.ORG)
    if case == "studio_orgs":
        monkeypatch.setattr(get_settings(), "studio_orgs", "")
    elif case == "feature":
        world.features = frozenset({"chat", "knowledge"})
    else:
        monkeypatch.setattr(get_settings(), "tool_search_orgs", "")
    with fk.in_run():
        assert await tgp.gap_hook(fk.NEED, []) is None
    assert world.records == [] and world.asks == [] and world.catalog_calls == 0
    assert world.opened == 0


async def test_the_real_gate_lets_an_open_member_through(monkeypatch) -> None:
    """Non-vacuity of the case above: the same frame with each switch on
    writes one row."""
    real = ti._studio_member_open
    world = fk.install(monkeypatch, picks={"answer": ("none", 0.9)})
    monkeypatch.setattr(ti, "_studio_member_open", real)
    monkeypatch.setattr(get_settings(), "studio_orgs", fk.ORG)
    with fk.in_run():
        assert await tgp.gap_hook(fk.NEED, []) == tgp.SAY_NO_MATCH.format(
            gap_id=world.record_result)
    assert len(world.records) == 1


@pytest.mark.parametrize("over", [{"session_id": ""}, {"run_id": ""}])
async def test_a_run_with_no_thread_or_no_run_writes_no_row(monkeypatch, over) -> None:
    world = fk.install(monkeypatch, picks={"answer": ("none", 0.9)})
    with fk.in_run(**over):
        assert await tgp.gap_hook(fk.NEED, []) is None
    assert world.records == [] and world.catalog_calls == 0


async def test_a_run_with_no_verified_member_writes_no_row(monkeypatch) -> None:
    world = fk.install(monkeypatch, picks={"answer": ("none", 0.9)})
    monkeypatch.setattr(ex, "_verified_run_member", lambda: "")
    with fk.in_run():
        assert await tgp.gap_hook(fk.NEED, []) is None
    assert world.records == []


# ── 5. The facts come from the run context only ───────────────────────────


async def test_the_facts_of_the_row_come_from_the_run_only(monkeypatch) -> None:
    """R5. A query that names another member, chat or run changes nothing."""
    world = fk.install(monkeypatch, picks={"answer": ("none", 0.9)})
    query = "for bob@evil.test in session_id chat-other run_id run-other org 0b0b"
    with fk.in_run():
        await tgp.gap_hook(query, [])
    [row] = world.records
    assert (row["member"], row["thread_id"], row["run_id"]) == (fk.MEMBER, fk.THREAD, fk.RUN)
    assert world.searches == [(fk.MEMBER, query)]


def test_the_hook_and_the_tool_take_no_identity_argument() -> None:
    assert list(inspect.signature(tgp.gap_hook).parameters) == ["query", "live"]
    finder = tp.FindTools(candidates=tp.FindCandidates(), run_key="k", record=_record(),
                          build=lambda *a: None, measure=lambda items: 0)
    params = inspect.signature(finder.tool.func).parameters
    assert list(params) == ["query"]


# ── 6. SBX-4b2: build_tool (SBX-F7, the build half, §12.4 decision 12) ─────

from tests.unit.test_exact_tool_count import staging_shape  # noqa: E402,F401  (a fixture)
from tests.unit.test_tool_packs_choice import (  # noqa: E402
    CONFIG,
    _bullets,
    _names,
    _run_ctx,
    _tenant,
    orchestrator,  # noqa: F401  (a fixture)
    store,  # noqa: F401  (a fixture)
)
from tests.unit.test_tool_packs_choice import MEMBER as PACK_MEMBER  # noqa: E402
from tests.unit.test_tool_packs_choice import ORG as PACK_ORG  # noqa: E402
from tests.unit.test_tool_packs_choice import SESSION as PACK_SESSION  # noqa: E402

OTHER_ORG = "0b0b0b0b-0000-4000-8000-0000000000cc"
EVERY_PACK = frozenset(__import__("acb_skills.skill_families", fromlist=["x"]).pack_slugs())


def _build_on(monkeypatch, **off: Any) -> None:
    """The four switches of decision 12 and the tool search, on for the org
    of the pack harness. *off* names a switch to set to its value."""
    s = get_settings()
    values: dict[str, Any] = {
        "user_tool_find_enabled": True, "tool_search_orgs": PACK_ORG,
        "maf_coding_scope": f"tools:{PACK_ORG}", "sandbox_door_orgs": PACK_ORG,
        "sandbox_egress_enabled": True}
    values.update(off)
    for key, value in values.items():
        monkeypatch.setattr(s, key, value, raising=False)


async def _run_turn(agents: list[Any], *, agent_name: str = "orchestrator",
                    read_only: bool = False, run_ctx: dict[str, Any] | None = None,
                    tool_scope: list[str] | None = None, words: str = "") -> list[str]:
    """The real order of a run: the pack step, then the injection. The names
    of the agent's own list after both."""
    scope = list(CONFIG["tool_scope"]) if tool_scope is None else tool_scope
    with _tenant():
        turn = await tp.begin_turn(PACK_ORG, PACK_SESSION, PACK_MEMBER, words)
        await ti._apply_tool_packs(
            agents, turn, run_ctx or _run_ctx(), allowed=EVERY_PACK, agent_name=agent_name,
            tool_scope=scope, agent_config=CONFIG, bullets=_bullets(),
            offer_loader=not read_only, read_only=read_only)
        ti._inject_agent_tools(agents, tool_scope=scope, agent_name=agent_name,
                               agent_config=CONFIG, no_egress=False)
    return _names(agents)


@pytest.mark.parametrize("off", [
    {}, {"maf_coding_scope": ""}, {"maf_coding_scope": f"tools:{OTHER_ORG}"},
    {"maf_coding_scope": f"code_task:{PACK_ORG}"}, {"sandbox_door_orgs": ""},
    {"sandbox_door_orgs": OTHER_ORG}, {"user_tool_find_enabled": False},
    {"sandbox_egress_enabled": False}, {"tool_search_orgs": ""},
    "delegated", "whatsapp", "no_pack", "no_chat", "no_egress",
], ids=lambda o: o if isinstance(o, str) else ("open" if not o else "-".join(
    f"{k}={v or 'off'}"[:40] for k, v in o.items())))
async def test_build_tool_reaches_only_the_gated_run(
        staging_shape, orchestrator, store, monkeypatch, off) -> None:  # noqa: F811
    """§12.4 done-when 29 and 30 (SBX-F7, the build half). Only the web pack
    turn of the orchestrator, with the four switches on for the org of the
    binding, holds ``build_tool``. A scope with no ``tools:<org>``, or of
    another org, an org off ``SANDBOX_DOOR_ORGS``, each other switch off, a
    delegated run, the WhatsApp pack turn of ``_start_pack_turn``, a chat
    turn with no pack, a run with no chat and a ``no_egress`` run do not get
    it. Non-vacuity: the open case holds it.

    Mutations: drop one switch from ``_build_tool_withheld``, or add the
    tool in a read-only (WhatsApp) turn -> red."""
    _build_on(monkeypatch, **(off if isinstance(off, dict) else {}))
    monkeypatch.setattr(ex, "_verified_run_member", lambda: PACK_MEMBER)
    monkeypatch.setattr(tp, "packs_on", lambda org: EVERY_PACK)
    monkeypatch.setattr(tp, "packs_batch_on", lambda org: True)
    start = {"agent_name": "orchestrator", "organization_id": PACK_ORG,
             "thread_id": PACK_SESSION, "member": PACK_MEMBER, "delegated": False,
             "solo_room": True, "batch_thread": False, "shows_cards": True}
    if off in ("delegated", "no_chat", "no_pack"):
        if off == "delegated":
            start["delegated"] = True
        elif off == "no_chat":
            start["thread_id"] = None
        else:
            monkeypatch.setattr(tp, "packs_on", lambda org: frozenset())
        assert ex._start_pack_turn(**start) is None, "no pack turn, so no build_tool"
        names = _names(orchestrator)
        assert "build_tool" not in names
        return
    if off == "whatsapp":
        task = ex._start_pack_turn(**start, whatsapp=True)
        assert task is not None, "the WhatsApp pack turn starts"
        task.cancel()
        with pytest.raises((asyncio.CancelledError, Exception)):
            await task
        names = await _run_turn(orchestrator, read_only=True)
        assert "build_tool" not in names
        return
    ctx = _run_ctx(no_egress=True) if off == "no_egress" else None
    names = await _run_turn(orchestrator, run_ctx=ctx)
    assert ("build_tool" in names) is (off == {}), (off, sorted(names)[:5])
    if off == {}:
        assert names.count("build_tool") == 1


@pytest.mark.parametrize("whatsapp", [True, False])
async def test_build_tool_is_not_in_the_whatsapp_pack_turn(
        store, monkeypatch, whatsapp) -> None:  # noqa: F811
    """§12.4 done-when 30, the G2 WhatsApp pack turn by name. The real
    ``_start_pack_turn(whatsapp=True)`` and ``_apply_pack_turn(read_only=True)``
    load the read packs, and the run holds no ``build_tool``, with every
    switch on. Non-vacuity: the web turn of the same harness holds it.

    Mutation: give the tool to a read-only turn -> red."""
    from tests.unit import test_chat_packs_batch as g2

    _build_on(monkeypatch, tool_search_orgs=g2.ORG, maf_coding_scope=f"tools:{g2.ORG}",
              sandbox_door_orgs=g2.ORG)
    applied, agent = await g2._apply(monkeypatch, read_only=whatsapp)
    assert applied is not None and applied.builds, "the pack turn loaded packs"
    names = {ti._tool_name(t) for t in agent.default_options["tools"]}
    assert ("build_tool" in names) is (not whatsapp), sorted(names)[:8]


async def test_build_tool_reaches_only_the_orchestrator(
        staging_shape, orchestrator, store, monkeypatch) -> None:  # noqa: F811
    """§12.4 done-when 36a (audit B1). A custom agent that lists
    ``build_tool`` in its ``tool_scope`` does not get it: the tool is not an
    injectable platform tool, and the gate names the agent. The orchestrator
    gets it with the same switches, and its ``config.json`` does not list it.

    Mutation: add ``build_tool`` to the injectable platform tools, or drop
    the agent check of ``_build_tool_withheld`` -> red."""
    _build_on(monkeypatch)
    assert "build_tool" not in CONFIG["tool_scope"]
    for agent in (None, "orchestrator", "crm-assistant"):
        assert "build_tool" not in {ti._tool_name(t)
                                    for t in ti._collect_injectable_platform_tools(agent)}
    names = await _run_turn(orchestrator, agent_name="crm-assistant",
                            tool_scope=["build_tool", "web_search"])
    assert "build_tool" not in names
    with _tenant():
        token = ti._BUILD_ROOM.set(True)
        try:
            assert ti._build_tool_withheld(
                "crm-assistant", _run_ctx(), pack_turn=True) == {"build_tool"}
            assert ti._build_tool_withheld("orchestrator", _run_ctx(), pack_turn=True) == set()
            assert ti._build_tool_withheld(
                "orchestrator", _run_ctx(), pack_turn=False) == {"build_tool"}
        finally:
            ti._BUILD_ROOM.reset(token)


async def test_build_tool_has_its_own_room_under_the_cap(
        staging_shape, orchestrator, store, monkeypatch) -> None:  # noqa: F811
    """§12.4 done-when 36a (audit B2). ``build_tool`` takes the place after
    the packs and ``compute_run``, and no other: the base count never holds
    it, a base at the cap leaves it out, and a turn with it never sends more
    than the cap.

    Mutations: count the tool in the base, or set the room before
    ``compute_run`` takes its place -> red."""
    from orchestrator import sandbox_broker

    _build_on(monkeypatch)
    monkeypatch.setattr(sandbox_broker, "compute_enabled", lambda org: True, raising=False)
    base = ti._pack_base_names(orchestrator[0], agent_name="orchestrator",
                               tool_scope=list(CONFIG["tool_scope"]), agent_config=CONFIG,
                               disabled=frozenset())
    assert "build_tool" not in base
    names = await _run_turn(orchestrator)
    assert "build_tool" in names
    loader = 1 if ex._pack_loader_offer(orchestrator[0]) else 0
    finder = 1 if ex._pack_finder(orchestrator[0]) is not None else 0
    assert len(names) + loader + finder <= tp.MAX_REQUEST_TOOLS
    # A base at the cap leaves no place: the tool goes, and no pack does.
    monkeypatch.setattr(ti, "_pack_base_count", lambda *a, **k: tp.MAX_REQUEST_TOOLS)
    fresh = await _fresh_orchestrator(monkeypatch)
    names = await _run_turn(fresh)
    assert "build_tool" not in names and ti._BUILD_ROOM.get() is False
    # One place left: compute_run takes it, and build_tool gets none. Two
    # places: each gets one. The plan line of the real apply finds the base.
    plans: list[dict[str, Any]] = []
    real_info = ti._log.info

    def keep(event: str, **kw: Any) -> None:
        if event == "executor.pack_plan":
            plans.append(kw)
        real_info(event, **kw)

    monkeypatch.setattr(ti._log, "info", keep)
    from acb_skills.skill_families import SKILL_FAMILIES

    cap, seen = tp.MAX_REQUEST_TOOLS, set()
    for n in range(cap, 40, -1):
        monkeypatch.setattr(ti, "_pack_base_count", lambda *a, _n=n, **k: _n)
        names = await _run_turn(await _fresh_orchestrator(monkeypatch))
        plan = plans[-1]
        planned = plan["planned_tools"]
        reserve = max((len(SKILL_FAMILIES[x]["tools"]) for x in plan["offered"]
                       if planned + len(SKILL_FAMILIES[x]["tools"]) <= cap), default=0)
        compute = ti._COMPUTE_ROOM.get()
        build = ti._BUILD_ROOM.get()
        assert compute is (planned + reserve + 1 <= cap), (n, plan)
        assert build is (planned + reserve + int(compute) + 1 <= cap), (n, plan)
        assert ("build_tool" in names) is build
        seen.add((compute, build))
    assert {(True, False), (True, True), (False, False)} <= seen, seen


async def _fresh_orchestrator(monkeypatch) -> list[Any]:
    from orchestrator.agents import build_orchestrator_agent

    return [build_orchestrator_agent(with_history=False)]
