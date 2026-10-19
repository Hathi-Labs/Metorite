"""A Copilot agent does not run as a specialist (D84, D92).

Production, 2026-10-18: the main chat sent inbox work to ``task-manager``
with ``call_agents_parallel``. The loader logged
``loader.copilot_agent_deprecated`` and then ran the agent anyway, so the
sub-run started the Copilot CLI on the retired runtime. The deprecation line
only warns until the WS-43r switch (``loader.COPILOT_AGENTS_SUPPORTED``),
and WS-43r is an owner merge (WS43-G9).

This suite fences the narrower rule that ships before WS-43r. A DELEGATED run
of a Copilot agent is refused with ``AgentRuntimeUnsupported``, and the
caller gets the error text as the answer of the sub-task, so the orchestrator
falls back. The agent still runs as the agent of its own app (the My Tasks
rail) until its port (WS-8i).

Paths:

1. ``loader.refuse_copilot_specialist``: the label, or the object.
2. ``executor._run_sub_agent_streaming`` (``call_agent``,
   ``call_agents_parallel``, the specialist tools): a labelled agent is
   refused BEFORE the load, so no clone and no import run. An object with the
   Copilot shape is refused after the build, whatever its label says.
3. ``executor._run_agent_inner`` (the batch fallback, ``delegate_to_agent``,
   the email consult): refused when a parent context is bound, never for a
   top-level run. No self-mutation follows.
4. The orchestrator offers no Copilot agent: no specialist tool, and no line
   of the registry block.
5. The one exception, by owner call of 2026-10-19: app-builder stays a
   specialist until it is ported (``loader.COPILOT_SPECIALIST_ALLOWED``).

Hermetic: no SQL, no network, no model. R8 binds nothing here.

Mutations this suite catches (R7), each run red by hand:

* the label check in ``refuse_copilot_specialist`` is dropped ->
  ``test_the_label_refuses`` and ``test_a_labelled_sub_agent_is_refused_before_the_load``;
* the object check is dropped -> ``test_the_object_refuses`` and
  ``test_a_copilot_object_with_a_maf_label_is_refused_after_the_build``;
* the batch path refuses a top-level run ->
  ``test_a_top_level_batch_run_is_not_refused``;
* the batch path drops the delegation check ->
  ``test_a_delegated_batch_run_is_refused_and_never_self_mutates``;
* ``_load_specialist_agents_as_tools`` or ``_build_registry_block`` lists a
  refused Copilot agent, or drops app-builder ->
  ``test_the_orchestrator_offers_no_copilot_agent_but_app_builder``;
* the allow-list check is dropped, or a name joins it ->
  ``test_app_builder_delegated_from_the_main_chat_still_runs`` and
  ``test_the_allow_list_holds_app_builder_only``.
"""
from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from typing import Any, ClassVar

import pytest
from acb_skills import loader

executor = pytest.importorskip("orchestrator.executor")
agent_routes = pytest.importorskip(
    "gateway.routes.agent", reason="gateway not installed in this environment",
)

REPO = Path(__file__).resolve().parents[2]
MEMBER = "member@example.com"
ORG = "aaaaaaaa-0000-4000-8000-0000000000c1"


class _Copilot:
    """The shape the executor sends down the Copilot path."""

    _default_options: ClassVar[dict[str, Any]] = {"model": "x"}

    async def run(self, *_a: Any, **_k: Any) -> Any:  # pragma: no cover
        raise AssertionError("the Copilot CLI path ran")


class _Maf:
    _default_options = None


# ── 1. The loader rule ──────────────────────────────────────────────────────


@pytest.mark.parametrize("label", ["github-copilot", "GitHub-Copilot", "copilot", "copilot-sdk"])
def test_the_label_refuses(label: str) -> None:
    with pytest.raises(loader.AgentRuntimeUnsupported) as exc:
        loader.refuse_copilot_specialist("task-manager", label=label)
    assert loader.COPILOT_SPECIALIST_TEXT in str(exc.value)
    assert "'task-manager'" in str(exc.value)
    assert isinstance(exc.value, loader.AgentLoadError), "callers catch AgentLoadError"


def test_the_object_refuses() -> None:
    with pytest.raises(loader.AgentRuntimeUnsupported):
        loader.refuse_copilot_specialist("x", label="maf", built=[_Maf(), _Copilot()])


@pytest.mark.parametrize("label", [None, "", "maf", "MAF"])
def test_a_maf_agent_passes(label: Any) -> None:
    loader.refuse_copilot_specialist("projects-assistant", label=label, built=[_Maf()])


def test_the_gateway_reads_the_same_label_set() -> None:
    for label in loader.COPILOT_RUNTIME_LABELS:
        assert agent_routes._normalize_runtime(label) == "github-copilot"
        assert loader.is_copilot_runtime_label(label)
    assert agent_routes._normalize_runtime("maf") == "maf"
    assert not loader.is_copilot_runtime_label("maf")


# ── 2. The streamed delegation ──────────────────────────────────────────────


class _Loads:
    def __init__(self) -> None:
        self.count = 0


@pytest.fixture
def stubs(monkeypatch, tmp_path) -> _Loads:
    """A registry of one agent, an access gate that lets it run, and a
    loader that counts each load. ``agent_runtime`` is set per test."""
    seen = _Loads()

    async def _allowed(_name: str) -> None:
        return None

    monkeypatch.setattr(executor, "_assert_may_run_agent", _allowed)
    monkeypatch.setattr(
        executor, "_resolve_run_workspace", lambda *_a, **_k: (str(tmp_path), ""),
    )
    return seen


def _registry(monkeypatch, runtime: str) -> None:
    monkeypatch.setattr(agent_routes, "_load_dynamic_agents", lambda: [
        {"name": "old-agent", "description": "d", "agent_runtime": runtime,
         "repo_name": None, "local_path": None},
    ])


def _as_parent(fn) -> Any:
    """Run *fn* inside a parent run, as a delegation runs."""
    from acb_common import bind_run_context, run_context_scope
    from acb_common.db import bind_tenant, release_tenant
    from acb_skills.write_artifact import artifact_context_scope, bind_artifact_context

    async def _go() -> Any:
        with run_context_scope(), artifact_context_scope():
            bind_run_context(run_id="r-parent", agent="orchestrator",
                             user=MEMBER, member_verified=True)
            bind_artifact_context(agent_name="orchestrator", member=MEMBER,
                                  session_id="t-parent", workspace_root=str(REPO))
            token = bind_tenant(ORG)
            try:
                return await fn()
            finally:
                release_tenant(token)

    return asyncio.run(_go())


def test_a_labelled_sub_agent_is_refused_before_the_load(monkeypatch, stubs) -> None:
    _registry(monkeypatch, "github-copilot")

    def _load(*_a: Any, **_k: Any) -> Any:
        stubs.count += 1
        raise AssertionError("the loader ran for a refused agent")

    monkeypatch.setattr(executor, "load_agent", _load)
    text = _as_parent(lambda: executor._run_sub_agent_streaming(
        "old-agent", "file my inbox", "r-sub", event_queue=asyncio.Queue(),
    ))
    assert text.startswith("Sub-task to 'old-agent' failed:"), text
    assert loader.COPILOT_SPECIALIST_TEXT in text
    assert stubs.count == 0, "no clone and no import for a refused agent"


def test_a_copilot_object_with_a_maf_label_is_refused_after_the_build(
    monkeypatch, stubs, tmp_path,
) -> None:
    _registry(monkeypatch, "maf")

    class _Loaded:
        config: ClassVar[dict[str, Any]] = {"name": "old-agent"}
        agent_dir = tmp_path

        def build_agents(self) -> list[Any]:
            stubs.count += 1
            return [_Copilot()]

    @contextlib.contextmanager
    def _load(*_a: Any, **_k: Any):
        yield _Loaded()

    monkeypatch.setattr(executor, "load_agent", _load)
    text = _as_parent(lambda: executor._run_sub_agent_streaming(
        "old-agent", "file my inbox", "r-sub", event_queue=asyncio.Queue(),
    ))
    assert stubs.count == 1, "the object check needs the build"
    assert loader.COPILOT_SPECIALIST_TEXT in text, text


def test_call_agent_answers_with_the_refusal(monkeypatch, stubs) -> None:
    """The orchestrator reads the refusal as the answer of the sub-task."""
    from acb_skills.agent_tools import call_agents_parallel

    _registry(monkeypatch, "github-copilot")
    monkeypatch.setattr(executor, "load_agent", lambda *a, **k: pytest.fail("loaded"))
    text = _as_parent(lambda: call_agents_parallel(
        '[{"agent": "old-agent", "message": "file my inbox"}]',
    ))
    assert text.startswith("[old-agent]"), text
    assert loader.COPILOT_SPECIALIST_TEXT in text


# ── 3. The batch path ───────────────────────────────────────────────────────


class _Stop(Exception):
    pass


def test_a_delegated_batch_run_is_refused_and_never_self_mutates(monkeypatch, stubs) -> None:
    mutation = pytest.importorskip("orchestrator.mutation")
    mutations: list[dict] = []

    async def _record(**kw: Any) -> None:
        mutations.append(kw)

    _registry(monkeypatch, "github-copilot")
    monkeypatch.setattr(mutation, "attempt_self_mutation", _record)
    monkeypatch.setattr(executor, "load_agent", lambda *a, **k: pytest.fail("loaded"))

    async def _run() -> BaseException:
        try:
            await executor.run_agent("old-agent", {"message": "x", "mode": "sub_task"})
        except executor.AgentRunError as exc:
            return exc
        raise AssertionError("the delegated run was not refused")

    exc = _as_parent(_run)
    assert isinstance(exc.original, loader.AgentRuntimeUnsupported)
    assert loader.COPILOT_SPECIALIST_TEXT in str(exc)
    assert mutations == [], "a refused runtime started a self-mutation"


def test_a_top_level_batch_run_is_not_refused(monkeypatch, stubs) -> None:
    """With no parent run, the agent is the run's own agent, not a specialist.
    The run reaches the loader (which stops it here with a sentinel)."""
    _registry(monkeypatch, "github-copilot")

    class _Ctx:
        def __enter__(self) -> Any:
            stubs.count += 1
            raise executor.RunWorkspaceRefused("LOADED")

        def __exit__(self, *_a: Any) -> bool:
            return False

    monkeypatch.setattr(executor, "load_agent", lambda *a, **k: _Ctx())
    with pytest.raises(executor.AgentRunError) as exc:
        asyncio.run(executor.run_agent("old-agent", {"message": "x"}))
    assert "LOADED" in str(exc.value)
    assert stubs.count == 1


# ── 4. The orchestrator offers no Copilot agent ─────────────────────────────


def test_the_orchestrator_offers_no_copilot_agent_but_app_builder(monkeypatch) -> None:
    import orchestrator._tool_injection as ti
    from orchestrator.agents import _mod as orchestrator_agents

    _load_specialist_agents_as_tools = orchestrator_agents._load_specialist_agents_as_tools
    monkeypatch.setattr(agent_routes, "_load_dynamic_agents", lambda: [
        {"name": "agent-sales-assistant", "description": "Sales.",
         "agent_runtime": "github-copilot", "repo_name": "acme/agent-sales-assistant"},
    ])
    copilot = {e["name"] for e in agent_routes._AGENT_REGISTRY
               if loader.is_copilot_runtime_label(e.get("agent_runtime"))}
    assert {"task-manager", "app-builder"} <= copilot, "the fixture assumes both are held"
    refused = (copilot | {"agent-sales-assistant"}) - loader.COPILOT_SPECIALIST_ALLOWED

    tools = {t.name for t in _load_specialist_agents_as_tools()}
    assert {"projects_assistant", "app_builder"} <= tools, tools
    assert not {n.replace("-", "_") for n in refused} & tools, tools

    ti._build_registry_block.cache_clear()
    try:
        block = ti._build_registry_block()
    finally:
        ti._build_registry_block.cache_clear()
    assert "'projects-assistant'" in block
    assert "'app-builder'" in block
    for name in refused:
        assert f"'{name}'" not in block, block


# ── 5. The owner's one exception: app-builder (2026-10-19) ──────────────────


def test_the_allow_list_holds_app_builder_only() -> None:
    """A second name needs an owner call AND a test. This assertion fails the
    moment a name joins without one. Delete app-builder when it is ported."""
    assert frozenset({"app-builder"}) == loader.COPILOT_SPECIALIST_ALLOWED


def test_app_builder_passes_the_refusal() -> None:
    loader.refuse_copilot_specialist("app-builder", label="github-copilot")
    loader.refuse_copilot_specialist("app-builder", label="maf", built=[_Copilot()])
    assert loader.may_delegate_to("app-builder", "github-copilot")


@pytest.mark.parametrize("name", ["task-manager", "metorite", "agent-sales-assistant"])
def test_every_other_copilot_agent_is_refused(name: str) -> None:
    assert not loader.may_delegate_to(name, "github-copilot")
    with pytest.raises(loader.AgentRuntimeUnsupported):
        loader.refuse_copilot_specialist(name, label="github-copilot")
    with pytest.raises(loader.AgentRuntimeUnsupported):
        loader.refuse_copilot_specialist(name, label="maf", built=[_Copilot()])


def test_app_builder_delegated_from_the_main_chat_still_runs(monkeypatch, stubs) -> None:
    """The streamed delegation reaches the loader for app-builder (the loader
    stops it here with a sentinel), and refuses task-manager before it."""
    monkeypatch.setattr(agent_routes, "_load_dynamic_agents", lambda: [])

    class _Ctx:
        def __enter__(self) -> Any:
            stubs.count += 1
            raise executor.RunWorkspaceRefused("LOADED")

        def __exit__(self, *_a: Any) -> bool:
            return False

    monkeypatch.setattr(executor, "load_agent", lambda *a, **k: _Ctx())
    text = _as_parent(lambda: executor._run_sub_agent_streaming(
        "app-builder", "build me an app", "r-sub", event_queue=asyncio.Queue(),
    ))
    assert "LOADED" in text and stubs.count == 1, text

    text = _as_parent(lambda: executor._run_sub_agent_streaming(
        "task-manager", "file my inbox", "r-sub-2", event_queue=asyncio.Queue(),
    ))
    assert loader.COPILOT_SPECIALIST_TEXT in text and stubs.count == 1, text
