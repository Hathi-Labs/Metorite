"""WS-29 MT-1d (H4), slice 2 — the agent executor carries the caller's tenant.

Spec: ``project-docs/specs/saas_multitenancy_handover.md`` H4 (MT-1d), the
``acb_graph`` tenant-isolation effort. This slice is DARK: it threads
``organization_id`` into the executor and stores it in a run-keyed plain dict
(``executor._RUN_ORG``) so the SYNC ``acb_graph`` writes later slices convert can
read it AFTER the ``loop.run_in_executor`` worker-thread hop — where contextvars
do not propagate and the detached run has outlived the request scope. No DB write
is converted here, so runtime behaviour is unchanged.

Fence ``executor-run-carries-org`` (R7). Two halves, both of which must go RED if
org sourcing regresses:

* **Executor state.** Driving a chat ``run_agent_stream`` with a known
  ``organization_id`` puts it in ``_RUN_ORG[thread_id]`` DURING the run and
  removes it AFTER (cleared in the ``finally``). A value smuggled in
  ``event_payload`` must NEVER become the run's org — sourcing the tenant from
  the client/agent-visible payload is R11's tenant-spoofing hole.
* **Route sourcing.** The gateway chat route passes the SERVER-SIDE
  ``user.organization_id`` (never ``req.payload``) into both ``run_agent_stream``
  and ``run_detached`` — asserted over the AST so a regression to payload
  sourcing fails the build rather than shipping a cross-tenant read green.
"""
from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from typing import Any

import pytest
from orchestrator import executor

REPO = Path(__file__).resolve().parents[2]

ORG_ALICE = "org-alice-0000-0000-0000-000000000001"
KNOWN_TID = "chat-thread-org-probe"

# The streaming path's Tier-1→Tier-2 fallback discards one un-awaited ``run()``
# coroutine from the probe (it does not implement native streaming) — the same
# benign warning ``test_agent_run_identity`` filters, for the same reason.
pytestmark = pytest.mark.filterwarnings(
    "ignore:coroutine .*run.* was never awaited:RuntimeWarning"
)


# ── A probe agent: it records the run-keyed tenant visible DURING the run ─────

class _Resp:
    def __init__(self, text: str) -> None:
        self.text = text
        self.messages: list[Any] = []


class _ProbeAgent:
    """A minimal MAF-shaped agent. ``run`` is where a real agent's tool
    callbacks (and the later slices' ``acb_graph`` writes) fire, so snapshotting
    ``_RUN_ORG`` here reads it exactly where a worker-thread write would."""

    def __init__(self, seen: list[dict[str, str]]) -> None:
        self.name = "org-probe"
        self.tools: list[Any] = []
        self.default_options: dict[str, Any] = {}
        self._seen = seen

    async def run(self, *_a: Any, **_k: Any) -> _Resp:
        # Snapshot the whole registry so the assertion can see both "this
        # thread's org" and "nothing leaked for a thread that had none".
        self._seen.append(dict(executor._RUN_ORG))
        return _Resp("ok")

    async def __aenter__(self) -> _ProbeAgent:
        return self

    async def __aexit__(self, *_a: Any) -> bool:
        return False


class _Loaded:
    def __init__(self, seen: list[dict[str, str]]) -> None:
        self.agent_dir = Path("/tmp")
        self.agent_name = "org-probe"
        self.config: dict[str, Any] = {}
        self._seen = seen

    def build_agents(self) -> list[Any]:
        return [_ProbeAgent(self._seen)]


class _LoadCtx:
    def __init__(self, seen: list[dict[str, str]]) -> None:
        self._seen = seen

    def __enter__(self) -> _Loaded:
        return _Loaded(self._seen)

    def __exit__(self, *_a: Any) -> bool:
        return False


@pytest.fixture
def probe(monkeypatch: pytest.MonkeyPatch):
    """``run_agent_stream`` wired to the probe agent. No clone, no LLM, no audit."""
    seen: list[dict[str, str]] = []
    monkeypatch.setattr(executor, "load_agent", lambda *a, **k: _LoadCtx(seen))
    monkeypatch.setattr(executor, "build_integrations", lambda *a, **k: ({}, {}))
    monkeypatch.setattr(executor, "record", lambda *a, **k: None)
    # Leave no run-keyed org behind from another test, and prove teardown works.
    executor._RUN_ORG.pop(KNOWN_TID, None)
    return type("Probe", (), {"seen": seen})()


async def _drain_stream(
    payload: dict[str, Any], *, organization_id: str | None, thread_id: str,
) -> None:
    async for _ in executor.run_agent_stream(
        "org-probe", payload,
        run_id="run-1", thread_id=thread_id,
        organization_id=organization_id,
    ):
        pass


# ── 1. The executor state: set DURING, cleared AFTER ─────────────────────────

def test_run_carries_org_during_and_clears_after(probe):
    """``executor-run-carries-org``: the run-keyed org is live while the agent
    runs and gone once the generator's ``finally`` has run."""
    asyncio.run(_drain_stream(
        {"message": "hi", "source": "chat"},
        organization_id=ORG_ALICE, thread_id=KNOWN_TID,
    ))

    assert probe.seen, "the probe agent never ran — the harness is wrong"
    during = probe.seen[0]
    assert during.get(KNOWN_TID) == ORG_ALICE, (
        f"the run did not carry its tenant during execution: {during!r}"
    )
    # Cleared in the finally — a tenant left in _RUN_ORG is the next run's tenant.
    assert KNOWN_TID not in executor._RUN_ORG, (
        "the run-keyed org was not cleared when the run ended"
    )


# ── 2. The spoofing guard: a payload org is NEVER the run's org ──────────────

def test_a_payload_supplied_org_is_never_the_runs_org(probe):
    """R11: ``event_payload`` is client/agent-visible. With no server-side org,
    an org smuggled in the payload must not become the run's tenant."""
    asyncio.run(_drain_stream(
        {
            "message": "hi", "source": "chat",
            # Every field a regressed executor might read from the payload.
            "organization_id": "SPOOF", "org": "SPOOF", "organization": "SPOOF",
            "tenant": "SPOOF", "tenant_id": "SPOOF",
        },
        organization_id=None, thread_id=KNOWN_TID,
    ))

    during = probe.seen[0]
    assert KNOWN_TID not in during, (
        f"a payload-supplied org leaked into the run's tenant: {during!r}"
    )
    assert KNOWN_TID not in executor._RUN_ORG


def test_the_server_side_org_wins_over_a_spoofed_payload(probe):
    """The server-side value is what binds even when the payload disagrees."""
    asyncio.run(_drain_stream(
        {"message": "hi", "source": "chat", "organization_id": "SPOOF"},
        organization_id=ORG_ALICE, thread_id=KNOWN_TID,
    ))

    during = probe.seen[0]
    assert during.get(KNOWN_TID) == ORG_ALICE, (
        f"run org should be the server-side value, got {during.get(KNOWN_TID)!r}"
    )


# ── 3. The route sources the tenant server-side (AST fence) ──────────────────

def _call_name(func: ast.expr) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _assignments_of(tree: ast.AST, name: str) -> list[ast.expr]:
    """Every RHS bound to a bare ``name = ...`` in *tree*."""
    out: list[ast.expr] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == name:
                    out.append(node.value)
    return out


def test_the_chat_route_sources_org_from_the_authenticated_identity() -> None:
    """The gateway chat route passes ``user.organization_id`` (server-side) to
    both ``run_agent_stream`` and ``run_detached`` — and never an org derived
    from the request/event payload. Over the AST so an aliased or reshaped
    regression to payload sourcing still fails."""
    src = (
        REPO / "apps/services/gateway/gateway/routes/agent.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(src)

    targets = {"run_agent_stream", "run_detached"}
    kw_value: dict[str, ast.expr] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _call_name(node.func) in targets:
            kw = next(
                (k for k in node.keywords if k.arg == "organization_id"), None,
            )
            if kw is not None:
                kw_value[_call_name(node.func)] = kw.value

    for name in targets:
        assert name in kw_value, (
            f"{name} is called without an organization_id — the chat run would "
            "carry no tenant"
        )
        val = kw_value[name]
        val_src = ast.get_source_segment(src, val) or ""

        # Trace a local (e.g. ``_organization_id``) back to its assignment(s);
        # otherwise judge the expression passed inline.
        exprs_src = [val_src]
        if isinstance(val, ast.Name):
            for rhs in _assignments_of(tree, val.id):
                exprs_src.append(ast.get_source_segment(src, rhs) or "")

        joined = " ".join(exprs_src)
        assert "user" in joined and "organization_id" in joined, (
            f"{name}'s organization_id does not trace to the authenticated "
            f"user identity: {joined!r}"
        )
        for forbidden in ("payload", "event_payload", "req.body", "request."):
            assert forbidden not in joined, (
                f"{name} sources organization_id from request input "
                f"({forbidden!r}) — a tenant-spoofing hole (R11): {joined!r}"
            )
