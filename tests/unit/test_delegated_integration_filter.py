"""MCP-C3: a delegated run gets the integration filter of its parent's member.

Spec: ``project-docs/specs/org_access_control.md`` §8a (Metorite task 1627).

🔴 **The leak.** MCP-C1 put the member's filter on the web chat run. A
hand-off from that run sends ``{"message", "mode"}``, with no session member
and no claim, so ``executor._integration_authorizer`` found nobody and gave
``None``, which means every credential. ``_run_sub_agent_streaming`` passed no
filter at all. A member with a deny on ``integrations:use:zoho-crm`` asked the
assistant, the assistant delegated to an agent that declares ``zoho-crm``, and
the sub-run bound the Zoho credential.

**The rules this suite fences (R7).**

1. A run that names no member reads the member of the run binding
   (``executor._bound_run_member``). The server binds it, and a body never
   sets it. Section A.
2. A top-level run with no member (cron, webhook) keeps ``None``. Sections A,
   B and D.
3. ``_run_sub_agent_streaming`` filters its own build, before the env
   injection. Section D.
4. A delegated run folds the room of the chat that asked, not its own
   minted thread (``executor._delegated_authority_thread``). Sections B and D.
5. Each call of ``build_integrations`` in the executor passes a filter.
   Section E.
6. A hand-off gives the parent its own memory gate back. The child's lookup
   arms the gate on the parent's task, so ``run_agent`` scopes it
   (``memory_tools.memory_permission_scope``) and ``_run_sub_agent_streaming``
   resets its token. Section F.

**The mutations.** Each ran by a script on 2026-11-11, and each named test
went red. The allow cases (A6, B2) and the no-member cases (A3, A7, B3, D4)
stay green under each one, as they must: no mutation widens a run that the
filter already left open.

* M1: ``_integration_authorizer`` drops the ``_bound_run_member()`` fallback.
  Red: A1, A2, A4, A5, B1, B4, C1 to C4, D1 to D3, F1 to F3.
* M2: ``_run_sub_agent_streaming`` drops ``is_authorized=_sub_authorizer``.
  Red: D1, D2, D3, E1.
* M3: the batch path folds on its own thread. Red: B4.
* M4: the sub-run folds on no thread. Red: D3, E2.
* M5: the fallback reads a verified member only. Red: A5.
* M6: ``run_agent`` drops ``memory_permission_scope()``. Red: F1.
* M7: ``_run_sub_agent_streaming`` drops ``reset_memory_permission``. Red: F2.

No database and no network: the access read, the model call and the store
are stubbed. The R8 half, with the real ``resolve_access``, is
``test_delegated_integration_filter_r8.py``.
"""
from __future__ import annotations

import ast
import asyncio
import contextlib
import json
import types
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("agent_framework", reason="MAF not installed")

from acb_auth import build_access

AGENT = "agent-c3-zoho"
ORG = "org-mcp-c3"
PRIYA = "priya@c3.test"
BOB = "bob@c3.test"
CHAT = "chat-c3-room"
REPO = Path(__file__).resolve().parents[2]
EXECUTOR_PY = REPO / "apps" / "services" / "orchestrator" / "orchestrator" / "executor.py"

#: Every integration but Zoho: the deny of the task.
DENIED = build_access(["integrations:use:*"], [("integrations:use:zoho-crm", "deny")])
#: Every integration.
ALLOWED = build_access(["integrations:use:*"])
INACTIVE = build_access(["integrations:use:*"], is_active=False)


async def read_inbox(limit: int = 10) -> str:
    """The agent's own tool."""
    return ""


class _NativeAgent:
    """The native MAF ``Agent`` shape: its tools live in ``default_options``."""

    def __init__(self) -> None:
        self.name = AGENT
        self.default_options: dict[str, Any] = {"tools": [read_inbox], "instructions": ""}


# ── The harness ─────────────────────────────────────────────────────────────


def install_world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *,
                  access: bool = True) -> types.SimpleNamespace:
    """One shared agent that declares ``zoho-crm`` and ``apollo``.

    The credential build, the model call and the store are stubbed, and so is
    the access read unless *access* is False (the R8 suite reads the real
    one). Each stub records what the run did, in ``world.state``.
    """
    import acb_auth
    import acb_memory
    import acb_skills.loader as loader
    from acb_common import get_settings
    from gateway.routes import agent as agent_routes
    from orchestrator import _tool_injection as ti
    from orchestrator import app_tools, executor, workflow_tools

    clone = tmp_path / "agents" / "repos" / AGENT
    clone.mkdir(parents=True)
    parent_ws = tmp_path / "parent-ws"
    parent_ws.mkdir()
    settings = get_settings()
    monkeypatch.setattr(settings, "agents_clone_dir", str(tmp_path / "agents"))
    monkeypatch.setattr(settings, "custom_apps_root", str(tmp_path / "agents" / "apps"))
    monkeypatch.setattr(agent_routes, "_AGENT_REGISTRY", [
        {"name": AGENT, "repo_name": None, "local_path": str(clone)},
    ])
    monkeypatch.setattr(agent_routes, "_load_dynamic_agents", lambda: [])

    state = types.SimpleNamespace(
        access={PRIYA: DENIED, BOB: ALLOWED},
        rooms={},  # session id -> the other members of that room
        folds=[],  # (session id, actor) of each room read
        builds=[],  # (resolved names, filtered?) of each credential build
        bound=[],  # names that the sub-run bound before its env injection
        ran=[],  # names that the batch run handed to its model call
        gates=[],  # may the run write org memory, read at each credential build
        # True: the participant read fails, and the fold gives the actor's
        # access alone, as acb_auth.access.resolve_session_access does.
        room_read_fails=False,
        agents=lambda: [_NativeAgent()],
    )

    async def resolve_access(email: str, **_kw: Any) -> Any:
        return state.access.get(str(email).lower(), INACTIVE)

    async def resolve_session_access(session_id: Any, actor: Any) -> Any:
        state.folds.append((session_id, actor))
        own = await resolve_access(actor)
        room = state.rooms.get(session_id)
        if not room or state.room_read_fails:
            return own, [actor]
        folded = own
        for other in room:
            folded = folded.intersect(await resolve_access(other))
        return folded, sorted({actor, *room})

    @contextmanager
    def load(name: str, **_kw: Any) -> Iterator[Any]:
        cfg = {"name": name, "integrations": ["zoho-crm"],
               "optional_integrations": ["apollo"]}
        yield types.SimpleNamespace(
            agent_dir=clone, agent_name=name, config=json.loads(json.dumps(cfg)),
            build_agents=lambda: state.agents(),
        )

    def build(m: list[str], o: list[str], _s: Any, *, is_authorized: Any = None) -> Any:
        from acb_skills import memory_tools

        # The access lookup just before this build armed the memory gate.
        state.gates.append(memory_tools._may("memory:write_org"))
        names = [*m, *o]
        ok = {n: {"token": f"{n}-secret"} for n in names
              if is_authorized is None or is_authorized(n)}
        state.builds.append((sorted(ok), is_authorized is not None))
        return ok, {n: "refused" for n in names if n not in ok}

    real_bind = executor._bind_run_credentials

    def bind(integrations: Any) -> Any:
        state.bound.append(sorted(integrations or {}))
        return real_bind(integrations)

    async def run_maf(_agents: Any, **kw: Any) -> Any:
        state.ran.append(sorted(kw.get("integrations") or {}))
        return {"answer": "ok"}

    async def quiet(*_a: Any, **_k: Any) -> str:
        return ""

    async def nothing(*_a: Any, **_k: Any) -> None:
        return None

    async def no_push(*_a: Any, **_k: Any) -> bool:
        return False

    if access:
        monkeypatch.setattr(acb_auth, "resolve_access", resolve_access)
        monkeypatch.setattr(acb_auth, "resolve_session_access", resolve_session_access)
    monkeypatch.setattr(executor, "load_agent", load)
    monkeypatch.setattr(loader, "load_agent", load)
    monkeypatch.setattr(executor, "build_integrations", build)
    monkeypatch.setattr(executor, "_bind_run_credentials", bind)
    monkeypatch.setattr(executor, "_run_with_maf_agent", run_maf)
    monkeypatch.setattr(executor, "_install_push_guard", quiet)
    monkeypatch.setattr(executor, "_get_current_head", quiet)
    monkeypatch.setattr(executor, "_settle_agent_push", quiet)
    monkeypatch.setattr(executor, "_detect_agent_commits", nothing)
    monkeypatch.setattr(executor, "_agent_push_binding", no_push)
    monkeypatch.setattr(acb_memory, "rehydrate_workspace", nothing)
    monkeypatch.setattr(ti, "_collect_injectable_platform_tools", lambda *_a, **_k: [])
    monkeypatch.setattr(ti, "_load_disabled_skill_families", lambda _name: frozenset())
    monkeypatch.setattr(app_tools, "load_app_action_tools", lambda *_a, **_k: [])
    monkeypatch.setattr(workflow_tools, "load_workflow_tools", lambda *_a, **_k: [])
    return types.SimpleNamespace(state=state, clone=clone, parent_ws=parent_ws)


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> types.SimpleNamespace:
    return install_world(tmp_path, monkeypatch)


@contextlib.contextmanager
def bound_member(member: str | None, *, verified: bool) -> Iterator[None]:
    """Only a run binding: the member and its flag, as a run boundary binds
    them (``_run_member``), with nothing else of a run."""
    from acb_common import bind_run_context, clear_run_context, run_context_scope

    with run_context_scope():
        clear_run_context()
        bind_run_context(user=member, member_verified=verified)
        yield


@contextlib.contextmanager
def parent_chat(world: Any, member: str | None = PRIYA, *, verified: bool = True,
                session: str = CHAT, org: str = ORG) -> Iterator[None]:
    """A web chat run of the orchestrator for *member*, bound as the stream run
    binds it: the run binding (H-73), the artifact context and the tenant.
    *member* None is a run with no member. *verified* False is a parent that
    bound a body's claim."""
    from acb_common import bind_run_context, clear_run_context, run_context_scope
    from acb_common.db import bind_tenant, release_tenant
    from acb_skills.write_artifact import artifact_context_scope, bind_artifact_context

    token = bind_tenant(org)
    try:
        with run_context_scope(), artifact_context_scope():
            clear_run_context()
            bind_run_context(
                run_id="r-parent", thread_id=session, agent="orchestrator",
                user=member, member_verified=verified,
            )
            bind_artifact_context(
                session_id=session, agent_name="orchestrator", run_id="r-parent",
                workspace_root=str(world.parent_ws), instance=f"o:{org}",
                member=member or "",
            )
            yield
    finally:
        release_tenant(token)


def delegated_batch_run(org: str = ORG) -> Any:
    """The batch run that ``call_agent`` starts with no stream: a payload that
    names nobody, no session member and no thread."""
    from orchestrator import executor

    return asyncio.run(executor.run_agent(
        AGENT, {"message": "the deals of Welmont", "mode": "sub_task"},
        run_id="r-child", organization_id=org,
    ))


def sub_run() -> str:
    """One streaming sub-run, as the specialist tools start it."""
    from orchestrator import executor

    return asyncio.run(executor._run_sub_agent_streaming(
        AGENT, "the deals of Welmont", "r-sub", event_queue=asyncio.Queue(),
    ))


# ── A. The authorizer reads the member of the run binding ───────────────────


def _authorize(payload: Any = None, **kw: Any) -> Any:
    from orchestrator import executor

    return asyncio.run(executor._integration_authorizer(payload, None, **kw))


def test_a1_a_run_that_names_nobody_gets_the_bound_members_filter(world) -> None:
    """The defect of the task: a deny on Zoho holds for a payload that names
    nobody, because the run binding names Priya, verified (M1)."""
    with bound_member(PRIYA, verified=True):
        predicate = _authorize({"message": "hi", "mode": "sub_task"})
    assert predicate is not None, "a delegated run got every credential"
    assert not predicate("zoho-crm")
    assert predicate("apollo")


def test_a2_an_allow_still_allows(world) -> None:
    world.state.access[PRIYA] = ALLOWED
    with bound_member(PRIYA, verified=True):
        predicate = _authorize(None)
    assert predicate is not None and predicate("zoho-crm") and predicate("apollo")


def test_a3_a_run_with_no_member_keeps_none(world) -> None:
    """A cron or webhook run binds no member, so nothing changes for it."""
    with bound_member(None, verified=False):
        assert _authorize(None) is None
        assert _authorize({"message": "nightly sweep"}) is None
    assert world.state.folds == [], "a run with no member read the access store"


def test_a4_an_inactive_verified_member_gets_nothing(world) -> None:
    """The MCP-C1 rule, now for a delegated run too: a verified member who does
    not resolve active gets no credential, never ``None``."""
    world.state.access[PRIYA] = INACTIVE
    with bound_member(PRIYA, verified=True):
        predicate = _authorize(None)
    assert predicate is not None, "an inactive member got every credential"
    assert not predicate("apollo") and not predicate("zoho-crm")


def test_a5_a_parent_filtered_by_a_claim_passes_the_same_filter(world) -> None:
    """A parent that bound a body's claim was filtered by that claim. Its
    delegated run gets the same filter, and an inactive claim keeps ``None``,
    as the parent had (M5). So the fallback only narrows."""
    with bound_member(PRIYA, verified=False):
        predicate = _authorize(None)
        world.state.access[PRIYA] = INACTIVE
        inactive = _authorize(None)
    assert predicate is not None and not predicate("zoho-crm"), "the claim's filter was lost"
    assert inactive is None


def test_a6_a_session_member_and_a_claim_still_win(world) -> None:
    """The binding is the last source. A session member wins, and so does a
    body's claim, exactly as before MCP-C3."""
    with bound_member(PRIYA, verified=True):
        session = _authorize(None, session_user=BOB)
        claim = _authorize({"user_email": BOB})
    assert session("zoho-crm") and claim("zoho-crm"), "Bob's own access did not apply"


def test_a7_a_synthetic_principal_in_the_binding_keeps_none(world) -> None:
    """A room id or ``system:internal`` is not a member."""
    for principal in ("room:thread-1", "system:internal"):
        with bound_member(principal, verified=True):
            assert _authorize(None) is None, principal


# ── B. The batch path of a delegated run ────────────────────────────────────


def test_b1_a_delegated_batch_run_does_not_bind_a_denied_credential(world) -> None:
    with parent_chat(world):
        assert delegated_batch_run() == {"answer": "ok"}
    assert world.state.ran == [["apollo"]], world.state.ran


def test_b2_a_delegated_batch_run_binds_an_allowed_credential(world) -> None:
    world.state.access[PRIYA] = ALLOWED
    with parent_chat(world):
        delegated_batch_run()
    assert world.state.ran == [["apollo", "zoho-crm"]], world.state.ran


def test_b3_a_top_level_run_with_no_member_is_unfiltered(world) -> None:
    """A cron run: no parent, no member. Every credential, and no access read."""
    from acb_common.db import bind_tenant, release_tenant

    token = bind_tenant(ORG)
    try:
        with bound_member(None, verified=False):
            delegated_batch_run()
    finally:
        release_tenant(token)
    assert world.state.ran == [["apollo", "zoho-crm"]], world.state.ran
    assert world.state.builds == [(["apollo", "zoho-crm"], False)]
    assert world.state.folds == []


def test_b4_a_delegated_batch_run_folds_the_room_of_the_chat_that_asked(world) -> None:
    """Bob may use Zoho, and Priya reads the room. The parent acts with the
    intersection, so its delegated run must too, and not with Bob's whole
    access on a minted thread that has no participant (M3)."""
    world.state.rooms[CHAT] = [PRIYA]
    with parent_chat(world, BOB):
        delegated_batch_run()
    assert (CHAT, BOB) in world.state.folds, world.state.folds
    assert world.state.ran == [["apollo"]], "the room's cap was lost on the hand-off"


# ── C. Each caller that hands off to the batch path ─────────────────────────


def _no_stream() -> None:
    """No queue and no relay: each caller takes its batch fallback."""
    from orchestrator import executor

    assert executor._active_run_queue.get(None) is None
    assert executor._stream_relay_thread_id.get(None) is None


def test_c1_call_agent_batch_fallback(world) -> None:
    from acb_skills import agent_tools

    _no_stream()
    with parent_chat(world):
        out = asyncio.run(agent_tools.call_agent(AGENT, "the deals of Welmont"))
    assert out == "ok", out
    assert world.state.ran == [["apollo"]], world.state.ran


def test_c2_delegate_to_agent_batch_fallback(world) -> None:
    from orchestrator import agents as orch_agents

    _no_stream()
    with parent_chat(world):
        out = asyncio.run(orch_agents.delegate_to_agent(AGENT, "the deals of Welmont"))
    assert out == "ok", out
    assert world.state.ran == [["apollo"]], world.state.ran


def test_c3_call_agent_background(world, monkeypatch) -> None:
    """The background task copies the parent's context, the run binding too."""
    from acb_skills import agent_tools

    _no_stream()
    started: list[asyncio.Task[Any]] = []
    real_create = asyncio.create_task

    def create(coro: Any, **kw: Any) -> asyncio.Task[Any]:
        task = real_create(coro, **kw)
        started.append(task)
        return task

    async def go() -> str:
        monkeypatch.setattr(asyncio, "create_task", create)
        try:
            out = await agent_tools.call_agent_background(AGENT, "the deals of Welmont")
        finally:
            monkeypatch.setattr(asyncio, "create_task", real_create)
        await started[0]
        return out

    with parent_chat(world):
        out = asyncio.run(go())
    assert "Dispatched" in out, out
    assert world.state.ran == [["apollo"]], world.state.ran


def test_c4_a_run_branch_with_no_streaming_seam(world) -> None:
    """``call_agents_parallel`` and ``spawn_subagents`` run each branch here.
    With no streaming seam, a branch takes the batch path."""
    from acb_skills import agent_tools

    _no_stream()
    with parent_chat(world):
        name, text = asyncio.run(agent_tools._run_branch(
            AGENT, "the deals of Welmont", wire=agent_tools._Wire(None, None),
            model=None, timeout=60,
        ))
    assert (name, text) == (AGENT, "ok"), text
    assert world.state.ran == [["apollo"]], world.state.ran


# ── D. The streaming sub-run (_run_sub_agent_streaming) ─────────────────────


def test_d1_the_sub_run_filters_its_own_build_before_the_env_injection(world) -> None:
    """The specialist tools, ``delegate_to_agent`` with a queue,
    ``call_agent`` and each branch of ``call_agents_parallel`` come here. The
    build of the sub-run itself binds the credentials (M2)."""
    world.state.agents = lambda: []
    with parent_chat(world):
        out = sub_run()
    assert "returned empty agent list" in out, out
    assert world.state.builds == [(["apollo"], True)], world.state.builds
    assert world.state.bound == [["apollo"]], "the sub-run bound a denied credential"


def test_d2_the_sub_run_of_a_maf_agent_hands_the_batch_run_the_filter(world) -> None:
    """A MAF sub-agent runs as a batch run under the sub-run. Both builds
    filter, and the model call gets no Zoho credential."""
    with parent_chat(world):
        out = sub_run()
    assert out == "ok", out
    assert world.state.bound == [["apollo"]], world.state.bound
    assert world.state.ran == [["apollo"]], world.state.ran
    assert [filtered for _names, filtered in world.state.builds] == [True, True]


def test_d3_the_sub_run_folds_the_room_of_the_chat_that_asked(world) -> None:
    """A sub-run in a shared room takes the room's cap, as its parent does (M4)."""
    world.state.rooms[CHAT] = [PRIYA]
    world.state.agents = lambda: []
    with parent_chat(world, BOB):
        sub_run()
    assert world.state.folds[:1] == [(CHAT, BOB)], world.state.folds
    assert world.state.bound == [["apollo"]], "the room's cap was lost on the hand-off"


def test_d4_a_sub_run_with_no_member_keeps_every_credential(world) -> None:
    """A delegation inside a run with no member (a cron run that delegates):
    unchanged, no filter."""
    world.state.agents = lambda: []
    with parent_chat(world, None):
        sub_run()
    assert world.state.builds == [(["apollo", "zoho-crm"], False)], world.state.builds


# ── F. A hand-off gives the parent its memory gate back ────────────────────
#
# The review case of MCP-C3. Bob types in a room that Priya reads, so the
# parent's memory gate is the room's intersection, which holds no
# ``memory:write_org``. In the hand-off the participant read fails, so the
# fold gives Bob's access alone, and the child's lookup arms that wider gate
# on the parent's task. After the hand-off, the parent must hold its own
# gate again, or it can write org memory that the room refuses.

#: Bob may write org memory. Priya may not, so the room may not.
WRITER = build_access(["integrations:use:*", "memory:read_org", "memory:write_org"])


def _hand_off_in_a_room(world: Any, hand_off: Any) -> tuple[Any, Any, bool]:
    """Run *hand_off* under Bob's parent chat with the room gate armed.
    Returns the room gate, the parent's gate after the hand-off, and whether
    the parent may write org memory then."""
    from acb_skills import memory_tools

    world.state.access[BOB] = WRITER
    world.state.rooms[CHAT] = [PRIYA]
    world.state.room_read_fails = True
    room_gate = WRITER.intersect(DENIED).has
    assert not room_gate("memory:write_org")

    async def go() -> tuple[Any, bool]:
        memory_tools._set_memory_permission(room_gate)
        await hand_off()
        return memory_tools._memory_permission.get(), memory_tools._may("memory:write_org")

    with parent_chat(world, BOB):
        after, may = asyncio.run(go())
    assert True in world.state.gates, "the child never held the wider gate: no case"
    return room_gate, after, may


def test_f1_a_batch_hand_off_gives_the_parent_its_gate_back(world) -> None:
    """``run_agent`` scopes the gate (``memory_permission_scope``, M6). The
    batch fallback of ``call_agent`` awaits it on the parent's task."""
    from acb_skills import agent_tools

    _no_stream()

    async def hand_off() -> None:
        assert await agent_tools.call_agent(AGENT, "the deals of Welmont") == "ok"

    room_gate, after, may = _hand_off_in_a_room(world, hand_off)
    assert after is room_gate, "the child's gate outlived the hand-off"
    assert may is False


def test_f2_a_streaming_sub_run_gives_the_parent_its_gate_back(world) -> None:
    """``_run_sub_agent_streaming`` resets its own token in its finally (M7)."""
    from orchestrator import executor

    world.state.agents = lambda: []

    async def hand_off() -> None:
        await executor._run_sub_agent_streaming(
            AGENT, "the deals of Welmont", "r-sub", event_queue=asyncio.Queue(),
        )

    room_gate, after, may = _hand_off_in_a_room(world, hand_off)
    assert after is room_gate, "the child's gate outlived the hand-off"
    assert may is False


def test_f3_the_child_still_holds_its_own_gate_while_it_runs(world) -> None:
    """The scope does not open the child's gate: during the hand-off a member
    with no ``memory:write_org`` still may not write org memory."""
    from acb_skills import agent_tools

    _no_stream()
    with parent_chat(world, PRIYA):
        asyncio.run(agent_tools.call_agent(AGENT, "the deals of Welmont"))
    assert world.state.gates == [False], world.state.gates


# ── E. Source fences ────────────────────────────────────────────────────────


def _calls(tree: ast.AST, name: str) -> list[ast.Call]:
    return [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name) and node.func.id == name
    ]


def test_e1_every_credential_build_of_the_executor_passes_a_filter() -> None:
    """A new call of ``build_integrations`` with no ``is_authorized`` gives
    every credential to a member who is denied one (M2)."""
    tree = ast.parse(EXECUTOR_PY.read_text(encoding="utf-8"))
    calls = _calls(tree, "build_integrations")
    assert len(calls) >= 3, "the stream, batch and sub-run builds are gone"
    for call in calls:
        kw = {k.arg: k.value for k in call.keywords}
        value = kw.get("is_authorized")
        assert isinstance(value, ast.Name), f"line {call.lineno}: no member filter"


def test_e2_the_sub_run_reads_its_room_from_the_parent() -> None:
    """The sub-run's check folds the chat that asked, from the parent's bound
    context, and takes no member from its message (R5, M4)."""
    tree = ast.parse(EXECUTOR_PY.read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.AsyncFunctionDef) and n.name == "_run_sub_agent_streaming")
    (call,) = _calls(fn, "_integration_authorizer")
    payload, thread = call.args
    assert isinstance(payload, ast.Constant) and payload.value is None
    assert isinstance(thread, ast.Call) and isinstance(thread.func, ast.Name)
    assert thread.func.id == "_delegated_authority_thread"
    kw = {k.arg: k.value for k in call.keywords}
    assert isinstance(kw.get("session_user"), ast.Constant) and kw["session_user"].value is None
