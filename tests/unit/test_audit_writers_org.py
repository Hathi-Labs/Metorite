"""Task 2221, slice 1 — the no-database half of the audit-org fix.

Five writers recorded tenant text with no ``organization_id``. With
``ACB_GRAPH_TENANT_BIND`` on, ``acb_audit._persist`` filed each row under the
OPERATOR org (Option-A):

* ``gateway.routes.chat.record_message_feedback`` (``message_feedback``).
* ``orchestrator.mutation.attempt_self_mutation`` (``mutation_start`` and the
  outcome row). The payload holds the run's error text.
* ``orchestrator.agents.pull_agent.answer`` and
  ``orchestrator.agents.sales_pull_agent.answer`` (the query row and
  ``guardrail_block``, which holds the raw model answer).
* ``orchestrator.resolution.resolve_with_llm`` (``tiebreak`` and
  ``tiebreak_error``).

The fix stamps the bound tenant on the calling frame. The chat route reads
``user.organization_id``, which ``get_current_user`` resolved from the
identity. The orchestrator writers read the run binding (``_RUN_ORG`` through
``_current_run_org()``). None of them reads a request field or a payload
field (R5). With no org and the bind on, ``acb_audit.refuse_tenantless``
skips the row.

This file proves the stamp and the skip with no database. The real write
under FORCE RLS is ``test_audit_writers_org_r8.py`` (R8).
"""
from __future__ import annotations

import asyncio
import contextlib
import importlib
import inspect
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

ORG_B = "org-b-2221"
VOTER = "voter@t2221.test"
REPO = Path(__file__).resolve().parents[2]


# ==========================================================================
# Helpers. The R8 file imports these.
# ==========================================================================
def _user(org: str | None):
    from acb_auth import UserContext
    from acb_auth.roles import UserRole

    return UserContext(email=VOTER, role=UserRole.EXECUTIVE, organization_id=org)


async def _feedback(org: str | None, message_id: str) -> dict:
    from gateway.routes import chat

    req = chat.MessageFeedbackRequest(message_id=message_id, vote="up", session_id="s1")
    return await chat.record_message_feedback(req, user=_user(org))


def _bind_run(org: str | None) -> None:
    """Bind *org* to this task's run, as ``run_agent_stream`` does.

    Call it inside the coroutine that ``asyncio.run`` starts, so the
    ContextVar stays in that task. The ``_RUN_ORG`` entry is removed by
    :func:`_unbind_run`.
    """
    from orchestrator import executor

    if org is None:
        return
    tid = f"t2221-{uuid.uuid4().hex[:8]}"
    executor._RUN_ORG[tid] = org
    executor._stream_relay_thread_id.set(tid)


def _unbind_run() -> None:
    from orchestrator import executor

    tid = executor._stream_relay_thread_id.get(None)
    if tid:
        executor._RUN_ORG.pop(tid, None)


def _patch_mutation(monkeypatch) -> None:
    """Take ``attempt_self_mutation`` past its gate to both audit rows.

    The gate, the telemetry and the sandbox are fakes. The audit write is real.
    """
    from orchestrator import mutation

    async def _permitted(_org=None):
        return True, ""

    async def _sandbox(*_a: Any, **_k: Any):
        return False, None, "", "", None

    monkeypatch.setattr(mutation, "_self_mutation_permitted", _permitted)
    monkeypatch.setattr(mutation, "_build_telemetry", lambda *a, **k: {})
    monkeypatch.setattr(mutation, "_run_mutation_sandbox", _sandbox)


def _patch_pull(monkeypatch, module, retrieve_name: str) -> None:
    """One hit, and a model answer with no citation, so both rows are written."""
    from orchestrator import executor

    hit = SimpleNamespace(kind="project", id=uuid.uuid4())

    async def _complete(**_k: Any) -> str:
        return "an answer with no citation"

    monkeypatch.setattr(
        executor, "_graph_session_opener_current",
        lambda: (lambda: contextlib.nullcontext(None)),
    )
    monkeypatch.setattr(module, retrieve_name, lambda _s, _q: [hit])
    monkeypatch.setattr(module, "format_context", lambda _hits: "ctx")
    monkeypatch.setattr(module, "complete", _complete)


def _patch_resolution(monkeypatch, *, fail: bool) -> None:
    from orchestrator import resolution

    async def _complete(**_k: Any) -> str:
        if fail:
            raise RuntimeError("model down")
        return "1"

    monkeypatch.setattr(resolution, "complete", _complete)


def _candidates() -> list:
    from acb_graph.resolver import ResolutionCandidate

    return [ResolutionCandidate(entity_id=uuid.uuid4(), score=0.9, reason="name")]


async def _pull(module, org: str | None, query: str) -> str:
    _bind_run(org)
    try:
        return await module.answer(query, user_email=VOTER, trace_id="t1")
    finally:
        _unbind_run()


async def _tiebreak(org: str | None) -> Any:
    from orchestrator import resolution

    _bind_run(org)
    try:
        return await resolution.resolve_with_llm(
            incoming={"name": "Acme"}, candidates=_candidates(),
            candidate_summaries={},
        )
    finally:
        _unbind_run()


# ==========================================================================
# Fixtures
# ==========================================================================
@pytest.fixture
def events(monkeypatch) -> list[Any]:
    """Catch each event that reaches ``record`` in every writer of the slice."""
    import acb_audit
    from orchestrator import mutation, resolution
    from orchestrator.agents import pull_agent, sales_pull_agent

    calls: list[Any] = []
    monkeypatch.setattr(acb_audit, "record", calls.append)
    for mod in (mutation, resolution, pull_agent, sales_pull_agent):
        monkeypatch.setattr(mod, "record", calls.append)
    return calls


@pytest.fixture
def bind_on(monkeypatch) -> None:
    monkeypatch.setenv("ACB_GRAPH_TENANT_BIND", "1")


# ==========================================================================
# 1. The chat vote.
# ==========================================================================
def test_a_vote_stamps_the_voters_org(events) -> None:
    assert asyncio.run(_feedback(ORG_B, "m1")) == {"ok": True}
    [ev] = events
    assert ev.action == "message_feedback"
    assert ev.organization_id == ORG_B
    # The org rides on the event, never in the payload.
    assert ev.payload == {"vote": "up", "session_id": "s1"}


def test_a_vote_with_no_org_and_the_bind_on_records_nothing(events, bind_on) -> None:
    assert asyncio.run(_feedback(None, "m1")) == {"ok": True}
    assert events == [], "a tenant-less vote reached the audit seam"


# ==========================================================================
# 2. The self-mutation rows.
# ==========================================================================
def test_both_mutation_rows_stamp_the_run_org(events, monkeypatch) -> None:
    from orchestrator import mutation

    _patch_mutation(monkeypatch)
    res = asyncio.run(mutation.attempt_self_mutation(
        "agent-2221", f"run-{uuid.uuid4().hex}", RuntimeError("tenant text"),
        audit_organization_id=ORG_B,
    ))
    assert res.attempted is True
    assert [(e.action, e.organization_id) for e in events] == [
        ("mutation_start", ORG_B), ("mutation_sandbox_failed", ORG_B),
    ]


def test_the_audit_org_does_not_feed_the_first_party_gate(monkeypatch) -> None:
    """The slice changes the audit rows only. The gate still sees no org."""
    from orchestrator import mutation

    seen: list[Any] = []

    async def _gate(org=None):
        seen.append(org)
        return False, "refused"

    monkeypatch.setattr(mutation, "_self_mutation_permitted", _gate)
    res = asyncio.run(mutation.attempt_self_mutation(
        "agent-2221", f"run-{uuid.uuid4().hex}", RuntimeError("x"),
        audit_organization_id=ORG_B,
    ))
    assert res.attempted is False
    assert seen == [None]


def test_mutation_rows_with_no_org_and_the_bind_on_record_nothing(
    events, bind_on, monkeypatch,
) -> None:
    from orchestrator import mutation

    _patch_mutation(monkeypatch)
    res = asyncio.run(mutation.attempt_self_mutation(
        "agent-2221", f"run-{uuid.uuid4().hex}", RuntimeError("tenant text"),
    ))
    assert res.attempted is True, "a refused audit row must not stop the run"
    assert events == [], "a tenant-less mutation row reached the audit seam"


def test_the_executor_passes_the_run_binding_to_both_calls() -> None:
    src = (REPO / "apps/services/orchestrator/orchestrator/executor.py").read_text(
        encoding="utf-8",
    )
    calls = src.count("await attempt_self_mutation(")
    stamped = src.count("audit_organization_id=_RUN_ORG.get(thread_id)")
    assert calls == 2
    assert stamped == calls, (
        "an attempt_self_mutation call in the executor does not pass the run's "
        "tenant, so its audit rows land under the operator org"
    )


# ==========================================================================
# 3. The two pull agents.
# ==========================================================================
@pytest.mark.parametrize(
    ("mod_name", "retrieve_name", "query_action"),
    [
        ("pull_agent", "retrieve", "pull_query"),
        ("sales_pull_agent", "sales_context", "sales_pull_query"),
    ],
)
def test_pull_rows_stamp_the_run_org(
    events, monkeypatch, mod_name, retrieve_name, query_action,
) -> None:
    module = importlib.import_module(f"orchestrator.agents.{mod_name}")
    _patch_pull(monkeypatch, module, retrieve_name)
    out = asyncio.run(_pull(module, ORG_B, "what does org B owe"))
    assert "Refusing to surface" in out
    assert [(e.action, e.organization_id) for e in events] == [
        (query_action, ORG_B), ("guardrail_block", ORG_B),
    ]


@pytest.mark.parametrize(
    ("mod_name", "retrieve_name"),
    [("pull_agent", "retrieve"), ("sales_pull_agent", "sales_context")],
)
def test_pull_rows_with_no_tenant_and_the_bind_on_record_nothing(
    events, bind_on, monkeypatch, mod_name, retrieve_name,
) -> None:
    module = importlib.import_module(f"orchestrator.agents.{mod_name}")
    _patch_pull(monkeypatch, module, retrieve_name)
    out = asyncio.run(_pull(module, None, "what does org B owe"))
    assert "Refusing to surface" in out, "a refused audit row must not stop the answer"
    assert events == [], "a tenant-less pull row reached the audit seam"


def test_the_pull_agents_take_no_org_from_input() -> None:
    from orchestrator.agents import pull_agent, sales_pull_agent

    for mod in (pull_agent, sales_pull_agent):
        params = inspect.signature(mod.answer).parameters
        assert "organization_id" not in params and "org" not in params


# ==========================================================================
# 4. The resolver tie-break.
# ==========================================================================
@pytest.mark.parametrize(("fail", "action"), [(False, "tiebreak"), (True, "tiebreak_error")])
def test_a_tiebreak_row_stamps_the_run_org(events, monkeypatch, fail, action) -> None:
    _patch_resolution(monkeypatch, fail=fail)
    asyncio.run(_tiebreak(ORG_B))
    assert [(e.action, e.organization_id) for e in events] == [(action, ORG_B)]


@pytest.mark.parametrize("fail", [False, True])
def test_a_tiebreak_with_no_tenant_and_the_bind_on_records_nothing(
    events, bind_on, monkeypatch, fail,
) -> None:
    _patch_resolution(monkeypatch, fail=fail)
    asyncio.run(_tiebreak(None))
    assert events == [], "a tenant-less tie-break row reached the audit seam"
