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
    assert answer == f"{tp.FIND_NONE}\n\n{tgp.SAY_NO_MATCH}"
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
        assert await tgp.gap_hook(fk.NEED, []) == tgp.SAY_NO_MATCH
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
