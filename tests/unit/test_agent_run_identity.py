"""S1-4 — an agent run's acting user must not outlive the run, or cross into another.

Spec: ``project-docs/specs/multi_tenancy_leak_audit.md`` §S1-4.

Both executors used to open every run with::

    if _mu:
        _set_memory_user_id(_mu)
        os.environ["ACB_AGENT_USER_EMAIL"] = _mu     # never cleared

and the four tool clients that call the gateway on an agent's behalf
(email-assistant, crm, whatsapp-assistant, skill-my-tasks) each read that env
var as their fallback answer to "who am I acting for". Two things follow, and
this file measured both against the real ``run_agent`` before fixing them:

* A run whose payload names nobody — a workflow agent node
  (``routes/workflows/service.py:118-129``), a sub-agent batch dispatch — took
  the LAST run's user, because ``if _mu:`` skipped the assignment and the slot
  still held it. Measured: ``env='alice@fracktal.in'`` inside a second run,
  in a fresh event loop, dispatched by nobody.
* Two runs in flight at once shared the one slot, so the loser of the race read
  the winner's user. Measured: alice's run observing bob's address in ``env``
  while its own ContextVar still correctly said alice.

The ContextVar leaked too, which the audit did not claim: ``_set_memory_user_id``
is a bare ``.set()`` with no reset, and an awaited coroutine runs in its
CALLER's context — so two sequential ``await run_agent(...)`` calls on one task
(exactly what a request handler or a workflow does) left run 1's identity
standing for run 2. Measured: ``ctxvar='alice@fracktal.in'`` in a run whose
payload named nobody.

Under D-MT-1 that email is the tenant, so each of these is a cross-tenant read.

What is pinned here: identity is bound per run and released with it, an
unattributed run resolves to nobody and its tool clients refuse, concurrent runs
cannot see each other — and the one inheritance that IS legitimate, a sub-agent
delegating inside its parent's run, still works.
"""
from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
from pathlib import Path
from typing import Any

import pytest
from acb_skills.memory_tools import _get_memory_user_id
from orchestrator import executor

REPO = Path(__file__).resolve().parents[2]

ALICE = "alice@fracktal.in"
BOB = "bob@othertenant.example"

# The streaming path's Tier-1→Tier-2 fallback discards one un-awaited ``run()``
# coroutine from the probe (it does not implement native streaming) — the same
# benign warning ``test_run_agent_stream_e2e`` filters, for the same reason.
pytestmark = pytest.mark.filterwarnings(
    "ignore:coroutine .*run.* was never awaited:RuntimeWarning"
)


# ── A probe agent: it reports who the tool surface thinks it is ──────────────

class _Resp:
    def __init__(self, text: str) -> None:
        self.text = text
        self.messages: list[Any] = []


class _ProbeAgent:
    """A minimal MAF-shaped agent that records the identity visible to tools.

    ``run`` is where a real agent's tool callbacks fire, so reading the identity
    here reads it exactly where a gateway call would.
    """

    def __init__(self, seen: list[dict[str, Any]], gate: Any = None) -> None:
        self.name = "identity-probe"
        self.tools: list[Any] = []
        self.default_options: dict[str, Any] = {}
        self._seen = seen
        self._gate = gate

    async def run(self, *_a: Any, **_k: Any) -> _Resp:
        if self._gate is not None:
            await self._gate()  # hold both runs open at once
        self._seen.append({
            "ctxvar": _get_memory_user_id(),
            "env": os.environ.get("ACB_AGENT_USER_EMAIL", ""),
            "client": _crm_client_identity(),
        })
        return _Resp("ok")

    async def __aenter__(self) -> _ProbeAgent:
        return self

    async def __aexit__(self, *_a: Any) -> bool:
        return False


class _Loaded:
    def __init__(self, seen: list[dict[str, Any]], gate: Any) -> None:
        self.agent_dir = Path("/tmp")
        self.agent_name = "identity-probe"
        self.config: dict[str, Any] = {}
        self._seen = seen
        self._gate = gate

    def build_agents(self) -> list[Any]:
        return [_ProbeAgent(self._seen, self._gate)]


class _LoadCtx:
    def __init__(self, seen: list[dict[str, Any]], gate: Any = None) -> None:
        self._seen = seen
        self._gate = gate

    def __enter__(self) -> _Loaded:
        return _Loaded(self._seen, self._gate)

    def __exit__(self, *_a: Any) -> bool:
        return False


# ── The real reader, loaded from the agent package it lives in ──────────────

def _crm_client() -> Any:
    """``agent-crm``'s gateway client — one of the four real readers.

    Loaded by path (it is not on the import path) and cached, mirroring
    ``test_agent_gateway_identity``. Driving the REAL reader is the point: a
    test that re-implements ``_current_user_email`` would have passed
    throughout the bug.
    """
    mod = sys.modules.get("_identity_probe_crm")
    if mod is not None:
        return mod
    path = REPO / "apps/agents/agent-crm/agents.py"
    spec = importlib.util.spec_from_file_location("_identity_probe_crm", path)
    if spec is None or spec.loader is None:  # pragma: no cover - import plumbing
        pytest.skip(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception as exc:  # pragma: no cover - optional agent deps absent
        pytest.skip(f"agent-crm: {exc}")
    return mod


def _crm_client_identity() -> str:
    return str(_crm_client()._current_user_email())


# ── Driving the real executors ──────────────────────────────────────────────

@pytest.fixture
def probe(monkeypatch: pytest.MonkeyPatch):
    """Both executors, wired to the probe agent. No clone, no LLM, no audit row."""
    seen: list[dict[str, Any]] = []
    gate: dict[str, Any] = {"fn": None}
    monkeypatch.setattr(
        executor, "load_agent", lambda *a, **k: _LoadCtx(seen, gate["fn"])
    )
    monkeypatch.setattr(executor, "build_integrations", lambda *a, **k: ({}, {}))
    monkeypatch.setattr(executor, "record", lambda *a, **k: None)
    # The env var is the leak under test; never inherit one from the shell or
    # from another test, and never leave one behind.
    monkeypatch.delenv("ACB_AGENT_USER_EMAIL", raising=False)
    return type("Probe", (), {"seen": seen, "gate": gate})()


async def _run(payload: dict[str, Any]) -> Any:
    return await executor.run_agent("identity-probe", payload)


async def _drain_stream(payload: dict[str, Any]) -> None:
    async for _ in executor.run_agent_stream("identity-probe", payload):
        pass


# ── 1. The leak, in the shape it actually shipped ───────────────────────────

def test_a_later_run_that_names_nobody_does_not_inherit_the_earlier_user(probe):
    """The env-var leak, isolated: two runs, two event loops, two contexts.

    Nothing but a process-global could carry alice across this boundary — the
    second run's ContextVar is a fresh default. Before the fix the second run's
    tool client answered ``alice@fracktal.in``.
    """
    asyncio.run(_run({"user_email": ALICE}))
    asyncio.run(_run({"message": "who am I?", "mode": "sub_task"}))

    first, second = probe.seen
    assert first["client"] == ALICE
    assert second["client"] == "", (
        f"a run that named nobody acted as {second['client']!r}"
    )
    assert second["env"] == ""


def test_a_later_run_on_the_same_task_does_not_inherit_either(probe):
    """The ContextVar leak the audit did not claim.

    Sequential ``await``s share one context, and the old bind was a ``.set()``
    with no reset — so run 2 saw run 1's identity in the ContextVar itself, not
    only in the env var. This is the shape of a request handler or a workflow
    running two agents in a row.
    """
    async def _both() -> None:
        await _run({"user_email": ALICE})
        await _run({"message": "who am I?", "mode": "sub_task"})

    asyncio.run(_both())

    _first, second = probe.seen
    assert second["ctxvar"] == "", (
        f"the acting user survived its run: {second['ctxvar']!r}"
    )
    assert second["client"] == ""


def test_the_identity_is_released_when_the_run_ends(probe):
    """The caller's own context is left as the run found it.

    Otherwise the leak simply moves up one frame: the route that awaited the run
    now carries the identity into whatever it does next.
    """
    async def _scenario() -> str:
        await _run({"user_email": ALICE})
        return _get_memory_user_id()

    assert asyncio.run(_scenario()) == ""


# ── 2. Concurrency: the ContextVar has to be read from the right context ────

def test_two_interleaved_runs_never_see_each_other_s_user(probe):
    """Both runs are held open simultaneously, then read their identity.

    A ContextVar that is set but read from the wrong context is the same bug in
    a better type, so this asserts on what the tool client resolves — the value
    that would land in ``X-User-Email``.
    """
    async def _scenario() -> None:
        barrier = asyncio.Barrier(2)

        async def _gate() -> None:
            await barrier.wait()

        probe.gate["fn"] = _gate
        await asyncio.gather(
            _run({"user_email": ALICE}),
            _run({"user_email": BOB}),
        )

    asyncio.run(_scenario())

    identities = sorted(row["client"] for row in probe.seen)
    assert identities == sorted([ALICE, BOB]), (
        f"concurrent runs resolved {identities} — one read the other's tenant"
    )
    # And nothing process-global was written for either of them.
    assert {row["env"] for row in probe.seen} == {""}


def test_a_concurrent_run_cannot_lend_its_user_to_an_unattributed_one(probe):
    """The cross-tenant read in its worst form: alice runs, bob's run names
    nobody, and bob's agent must not reach alice's mailbox."""
    async def _scenario() -> None:
        barrier = asyncio.Barrier(2)

        async def _gate() -> None:
            await barrier.wait()

        probe.gate["fn"] = _gate
        await asyncio.gather(
            _run({"user_email": ALICE}),
            _run({"message": "no user here"}),
        )

    asyncio.run(_scenario())

    assert sorted(row["client"] for row in probe.seen) == ["", ALICE]


# ── 3. Fail closed, at the surface that would have made the call ────────────

def test_a_stray_env_var_cannot_supply_an_identity(
    probe, monkeypatch: pytest.MonkeyPatch
):
    """The runtime half of the source fence: the env var is present and wrong.

    This is the operator-set case and the leftover-from-a-crashed-process case
    at once. Nothing may consult it — a value in the environment is not evidence
    that this run belongs to that person.
    """
    monkeypatch.setenv("ACB_AGENT_USER_EMAIL", BOB)
    asyncio.run(_run({"message": "dispatched by nobody"}))

    assert probe.seen[0]["client"] == "", (
        f"a process-global supplied {probe.seen[0]['client']!r} to a run "
        "that named nobody"
    )


def test_an_identity_left_in_the_contextvar_is_not_inherited_either(probe):
    """The rule is "a scope is OPEN", not "the variable is non-empty".

    The distinction is the whole fix: a value sitting in the ContextVar with no
    run or request scope around it is exactly the leftover shape the env var
    had, and reading it would move the bug rather than close it. Driven at the
    seam because no executor can produce this state once the scopes are
    balanced — which is the point of asserting it.
    """
    from acb_skills.memory_tools import (
        _bind_memory_user_id,
        _memory_user_id,
        _unbind_memory_user_id,
    )

    stale = _memory_user_id.set(ALICE)  # nobody's scope; just a value
    try:
        binding = _bind_memory_user_id("")
        try:
            assert _get_memory_user_id() == ""
        finally:
            _unbind_memory_user_id(binding)
    finally:
        _memory_user_id.reset(stale)


def test_an_unattributed_run_refuses_to_call_the_gateway(probe):
    """End to end: executor binds nobody → the client raises rather than
    sending a bearer with no identity, which the gateway reads as SERVICE_ACCESS."""
    asyncio.run(_run({"message": "dispatched by a workflow node"}))

    with pytest.raises(RuntimeError) as exc:
        _crm_client()._headers()
    assert "nobody to act as" in str(exc.value)


# ── 4. The inheritance that IS legitimate ───────────────────────────────────

def test_a_sub_task_inside_a_parent_run_still_acts_as_the_parent_s_user(probe):
    """``call_agent`` and the sub-agent batch path dispatch
    ``{"message": ..., "mode": "sub_task"}`` with no user, from inside a run that
    already has one. That is delegation, not inheritance-by-accident, and it has
    to keep working — otherwise the fix silently breaks every delegated run.
    """
    async def _scenario() -> None:
        from acb_skills.memory_tools import _bind_memory_user_id
        binding = _bind_memory_user_id(ALICE)  # stands in for the parent run
        try:
            await _run({"message": "sub-task", "mode": "sub_task"})
        finally:
            from acb_skills.memory_tools import _unbind_memory_user_id
            _unbind_memory_user_id(binding)

    asyncio.run(_scenario())
    assert probe.seen[0]["client"] == ALICE


def test_a_route_that_resolved_its_user_is_honoured_by_a_payload_that_did_not(
    probe,
):
    """``routes/agent.py`` resolves the acting user (or a room's write scope) and
    calls ``_set_memory_user_id`` before dispatching; the payload does not always
    repeat it. That naming is deliberate and in scope, so it is inherited — the
    distinction the fix draws is between a scope that is OPEN and a value that was
    merely left behind."""
    async def _scenario() -> None:
        from acb_skills.memory_tools import _set_memory_user_id
        _set_memory_user_id(ALICE)
        await _run({"message": "payload without a user"})

    asyncio.run(_scenario())
    assert probe.seen[0]["client"] == ALICE


# ── 5. The streaming executor gets the same treatment ───────────────────────

def test_the_streaming_run_also_releases_its_identity(probe):
    """``run_agent_stream`` carried the identical pair of lines at :2191. Its
    teardown resets the binding in the same ``finally`` that already restores the
    relay token and this run's integration credentials."""
    async def _scenario() -> str:
        await _drain_stream({"user_email": ALICE, "message": "hi"})
        return _get_memory_user_id()

    leftover = asyncio.run(_scenario())
    assert leftover == ""
    assert os.environ.get("ACB_AGENT_USER_EMAIL", "") == ""


def test_a_streamed_run_that_names_nobody_starts_with_nobody(probe):
    async def _scenario() -> None:
        await _drain_stream({"user_email": ALICE, "message": "hi"})
        await _drain_stream({"message": "hi"})

    asyncio.run(_scenario())
    assert [row["client"] for row in probe.seen] == [ALICE, ""]


# ── 6. Source fences: the env var must not come back ────────────────────────

#: Every file that wrote or read the process-global identity.
FORMER_ENV_USERS = [
    "apps/services/orchestrator/orchestrator/executor.py",
    "apps/agents/agent-email-assistant/agents.py",
    "apps/agents/agent-crm/agents.py",
    "apps/agents/agent-whatsapp-assistant/agents.py",
    "apps/skills/skill-my-tasks/skill_my_tasks/core.py",
]


@pytest.mark.parametrize("rel", FORMER_ENV_USERS)
def test_nothing_writes_or_reads_the_process_global_identity(rel: str) -> None:
    """Asserted against the source because the runtime failure is silent: a
    reinstated fallback looks like a working run right up until it is somebody
    else's data. The name may still appear in prose explaining why it is gone."""
    src = (REPO / rel).read_text(encoding="utf-8")
    code = "\n".join(
        line for line in src.splitlines()
        if "ACB_AGENT_USER_EMAIL" in line and not line.lstrip().startswith("#")
    )
    for line in code.splitlines():
        assert "os.environ" not in line and "getenv" not in line, (
            f"{rel} reintroduced the process-global identity: {line.strip()}"
        )
