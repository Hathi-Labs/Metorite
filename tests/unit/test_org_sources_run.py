"""WS-29 acb_graph slice 6a — run-based org sources carry the caller's tenant.

Spec: ``project-docs/specs/saas_multitenancy_handover.md`` §H4 (MT-1d) · the
``acb_graph`` tenant-isolation effort · D15. Slice 6a extends slice 2's
``_RUN_ORG`` / ``bind_tenant`` machinery to the run-based sources that resolve
their tenant from in-hand SERVER-SIDE identity (no scoped DB read):

* ``/copilot/chat`` — stamps ``user.organization_id`` into ``run_detached``.
* sub-agent delegation — inherits the PARENT run's org.
* the batch path (``run_agent`` / ``_run_agent_inner``) — HONOURS the
  ``organization_id`` its callers pass.

This is DARK: it only populates ``_RUN_ORG`` / binds the tenant for more runs;
the slice 3-5 writes consume it only when ``ACB_GRAPH_TENANT_BIND`` is ON.

Three R7 fences, each RED if org sourcing regresses (payload-sourced, not
inherited, or the param ignored):

* ``copilot-run-carries-org`` — an AST fence proving the gateway ``/copilot/chat``
  route sources ``organization_id`` from the authenticated ``user`` and passes it
  to ``run_detached``, NEVER from the request/event input (R11 tenant-spoof).
* ``sub-agent-inherits-parent-org`` — ``_resolve_sub_agent_org`` returns the
  parent run's ``_RUN_ORG`` entry (never a payload), and a sub-run driven with it
  carries the parent's org in ``_RUN_ORG[thread_id]``.
* ``batch-run-honours-org-param`` — ``_run_agent_inner`` with an
  ``organization_id`` sets ``_RUN_ORG[thread_id]`` during the run and clears it
  after; with ``None`` (and even a spoofed payload org) it sets nothing and logs
  the missing-org gap.
"""
from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from typing import Any

import pytest
from orchestrator import executor

REPO = Path(__file__).resolve().parents[2]

ORG_PARENT = "org-parent-0000-0000-0000-000000000001"
ORG_BATCH = "org-batch-0000-0000-0000-000000000002"


# ── A recording logger so the missing-org warning is observable ──────────────
class _FakeLog:
    def __init__(self) -> None:
        self.warnings: list[tuple[str, dict]] = []

    def warning(self, event: str, **kw: object) -> None:
        self.warnings.append((event, kw))

    def __getattr__(self, _name: str):  # info/debug/error/exception → no-ops
        return lambda *a, **k: None


# =============================================================================
# Shared harness: drive ``_run_agent_inner`` far enough to observe its tenant
# binding, then short-circuit via a load error so no real agent runs.
# =============================================================================
def _drive_batch(
    monkeypatch: pytest.MonkeyPatch,
    *,
    organization_id: str | None,
    thread_id: str,
    event_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Call ``_run_agent_inner`` with the tenant plumbing live but ``load_agent``
    stubbed to raise immediately — so the run reaches the binding block + the
    first ``record`` (where ``_RUN_ORG`` is snapshotted) and then unwinds through
    the ``finally`` (where it is popped), with nothing else executing.

    Returns ``{"during": <_RUN_ORG at agent_run_start>, "after": <_RUN_ORG once
    unwound>, "warnings": [...], "event_org": <organization_id on the audit
    event>}``.
    """
    from acb_skills.loader import AgentLoadError

    captured: dict[str, Any] = {}
    fake = _FakeLog()

    def _record_spy(event: Any) -> None:
        # Snapshot at the FIRST record (agent_run_start), which fires right after
        # the tenant-binding block — exactly where a converted write would read.
        captured.setdefault("during", dict(executor._RUN_ORG))
        captured.setdefault("event_org", getattr(event, "organization_id", None))

    def _boom(*_a: Any, **_k: Any) -> Any:
        raise AgentLoadError("probe: short-circuit before the real run")

    async def _no_mutation(*_a: Any, **_k: Any) -> None:
        return None

    import orchestrator.mutation as _mutation

    monkeypatch.setattr(executor, "record", _record_spy)
    monkeypatch.setattr(executor, "get_settings", lambda: object())
    monkeypatch.setattr(executor, "load_agent", _boom)
    monkeypatch.setattr(executor, "_log", fake)
    monkeypatch.setattr(_mutation, "attempt_self_mutation", _no_mutation)

    payload = (
        event_payload
        if event_payload is not None
        else {"message": "hi", "mode": "sub_task"}
    )
    # Leave no stale entry from another test.
    executor._RUN_ORG.pop(thread_id, None)
    with pytest.raises(executor.AgentRunError):
        asyncio.run(
            executor._run_agent_inner(
                "probe-agent", payload,
                run_id="run-probe", thread_id=thread_id,
                organization_id=organization_id,
            )
        )
    captured["after"] = dict(executor._RUN_ORG)
    captured["warnings"] = fake.warnings
    executor._RUN_ORG.pop(thread_id, None)
    return captured


# =============================================================================
# 1. batch-run-honours-org-param  (no DB; always runs)
# =============================================================================
def test_batch_run_with_org_sets_run_org_during_and_clears_after(monkeypatch):
    """``batch-run-honours-org-param``: ``_run_agent_inner`` given an
    ``organization_id`` puts it in ``_RUN_ORG[thread_id]`` while the run executes
    and removes it in the ``finally``. RED if the batch path ignores the param
    (slice-2 state) — ``during`` would then not carry the org."""
    tid = "batch-tid-with-org"
    got = _drive_batch(monkeypatch, organization_id=ORG_BATCH, thread_id=tid)

    assert got["during"].get(tid) == ORG_BATCH, (
        f"the batch run did not carry its tenant during execution: {got['during']!r}"
    )
    assert got["event_org"] == ORG_BATCH, (
        "the agent_run_start audit event was not stamped with the run's tenant"
    )
    assert tid not in got["after"], (
        "the run-keyed org was not cleared when the batch run ended (finally)"
    )
    assert not any(
        ev == "executor.run_missing_org" for ev, _ in got["warnings"]
    ), "an org-carrying batch run must not log the missing-org gap"


def test_batch_run_without_org_sets_nothing_and_logs_gap(monkeypatch):
    """``None`` ⇒ the batch path stays on the pre-slice behaviour: no
    ``_RUN_ORG`` entry (operator-org audit fallback / flag-ON write skip) and the
    gap is LOGGED so it stays visible (slice 3, P1). RED if the missing-org
    signal is dropped or an org is invented."""
    tid = "batch-tid-no-org"
    got = _drive_batch(monkeypatch, organization_id=None, thread_id=tid)

    assert tid not in got["during"], (
        f"a batch run with no org must set no _RUN_ORG entry: {got['during']!r}"
    )
    assert got["event_org"] is None
    assert any(
        ev == "executor.run_missing_org" for ev, _ in got["warnings"]
    ), f"the missing-org gap must be logged: {[e for e, _ in got['warnings']]}"


def test_batch_run_never_sources_org_from_the_payload(monkeypatch):
    """R11 spoofing guard: with no server-side org, an org smuggled in the event
    payload must NEVER become the run's tenant."""
    tid = "batch-tid-spoof"
    got = _drive_batch(
        monkeypatch,
        organization_id=None,
        thread_id=tid,
        event_payload={
            "message": "hi", "mode": "sub_task",
            "organization_id": "SPOOF", "org": "SPOOF",
            "organization": "SPOOF", "tenant": "SPOOF", "tenant_id": "SPOOF",
        },
    )

    assert tid not in got["during"], (
        f"a payload-supplied org leaked into the batch run's tenant: {got['during']!r}"
    )
    assert got["event_org"] is None


# =============================================================================
# 2. sub-agent-inherits-parent-org  (no DB; always runs)
# =============================================================================
def test_resolve_sub_agent_org_reads_the_parent_runs_org(monkeypatch):
    """``_resolve_sub_agent_org`` returns the PARENT run's ``_RUN_ORG`` entry,
    keyed by the parent thread id it reads from ``_stream_relay_thread_id``."""
    parent_tid = "parent-thread-alpha"
    executor._RUN_ORG[parent_tid] = ORG_PARENT
    token = executor._stream_relay_thread_id.set(parent_tid)
    try:
        assert executor._resolve_sub_agent_org() == ORG_PARENT
    finally:
        executor._stream_relay_thread_id.reset(token)
        executor._RUN_ORG.pop(parent_tid, None)


def test_resolve_sub_agent_org_falls_back_to_the_bound_tenant(monkeypatch):
    """With no parent ``_RUN_ORG`` entry (the ``/copilot/chat`` shape, which binds
    the tenant on its detached task but sets no ``_RUN_ORG``), it falls back to
    the async bound tenant."""
    from acb_common.db import bind_tenant, release_tenant

    # No _stream_relay_thread_id, no _RUN_ORG entry → the bound tenant is used.
    tok = bind_tenant(ORG_PARENT)
    try:
        assert executor._resolve_sub_agent_org() == ORG_PARENT
    finally:
        release_tenant(tok)


def test_resolve_sub_agent_org_is_none_when_no_parent_tenant():
    """No parent thread, no ``_RUN_ORG``, no bound tenant → ``None`` (a truly
    untenanted background run)."""
    # Make sure nothing is bound/leaked.
    assert executor._stream_relay_thread_id.get(None) is None
    assert executor._resolve_sub_agent_org() is None


def test_resolve_sub_agent_org_takes_no_payload_argument():
    """Structural R11 guard: the resolver takes NO argument, so it is impossible
    for it to source the tenant from a delegated message / event payload — the
    org can only come from the run boundary (``_RUN_ORG`` / the bound tenant)."""
    import inspect

    sig = inspect.signature(executor._resolve_sub_agent_org)
    assert list(sig.parameters) == [], (
        "._resolve_sub_agent_org must take no argument — a parameter would open a "
        "door to payload-sourced tenants (R11)"
    )


def test_a_sub_run_carries_the_parent_org_in_run_org(monkeypatch):
    """``sub-agent-inherits-parent-org`` end to end: a parent run's org, resolved
    by ``_resolve_sub_agent_org`` and threaded into the batch path, becomes the
    SUB-run's ``_RUN_ORG[thread_id]``. RED if inheritance breaks (resolver
    returns ``None``) OR the batch path ignores the param."""
    parent_tid = "parent-thread-beta"
    sub_tid = "sub-thread-beta"
    executor._RUN_ORG[parent_tid] = ORG_PARENT
    token = executor._stream_relay_thread_id.set(parent_tid)
    try:
        resolved = executor._resolve_sub_agent_org()
        assert resolved == ORG_PARENT, "the sub-agent did not inherit the parent org"

        got = _drive_batch(
            monkeypatch, organization_id=resolved, thread_id=sub_tid,
        )
        assert got["during"].get(sub_tid) == ORG_PARENT, (
            f"the sub-run's _RUN_ORG did not equal the parent's org: {got['during']!r}"
        )
        assert sub_tid not in got["after"], (
            "the sub-run's org was not cleared when it ended"
        )
    finally:
        executor._stream_relay_thread_id.reset(token)
        executor._RUN_ORG.pop(parent_tid, None)


def test_sub_agent_streaming_threads_the_resolved_parent_org(monkeypatch):
    """The MAF sub-agent branch of ``_run_sub_agent_streaming`` passes the
    resolved parent org into ``run_agent`` — over the AST so an aliased regression
    to a payload/message-sourced org still fails."""
    src = (
        REPO / "apps/services/orchestrator/orchestrator/executor.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(src)

    # Find the run_agent(...) call inside _run_sub_agent_streaming.
    sub_fn = next(
        (
            n for n in ast.walk(tree)
            if isinstance(n, ast.AsyncFunctionDef)
            and n.name == "_run_sub_agent_streaming"
        ),
        None,
    )
    assert sub_fn is not None, "_run_sub_agent_streaming not found"

    run_agent_calls = [
        n for n in ast.walk(sub_fn)
        if isinstance(n, ast.Call) and _call_name(n.func) == "run_agent"
    ]
    assert run_agent_calls, "_run_sub_agent_streaming no longer calls run_agent"

    for call in run_agent_calls:
        kw = next(
            (k for k in call.keywords if k.arg == "organization_id"), None,
        )
        assert kw is not None, (
            "the sub-agent run_agent call carries no organization_id — the "
            "sub-run would not inherit its parent's tenant"
        )
        val_src = ast.get_source_segment(src, kw.value) or ""
        assert "_resolve_sub_agent_org" in val_src, (
            f"the sub-agent org is not the server-side parent resolver: {val_src!r}"
        )
        for forbidden in ("message", "payload", "event", "request"):
            assert forbidden not in val_src, (
                f"the sub-agent org is sourced from delegated input ({forbidden!r}) "
                f"— a tenant-spoofing hole (R11): {val_src!r}"
            )


# =============================================================================
# 3. copilot-run-carries-org  (AST fence over the gateway route)
# =============================================================================
def _call_name(func: ast.expr) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _assignments_of(tree: ast.AST, name: str) -> list[ast.expr]:
    out: list[ast.expr] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == name:
                    out.append(node.value)
    return out


def test_copilot_chat_sources_org_from_the_authenticated_identity() -> None:
    """``copilot-run-carries-org``: the gateway ``/copilot/chat`` route passes the
    SERVER-SIDE ``user.organization_id`` (never the request/event input) into
    ``run_detached`` — asserted over the AST so an aliased or reshaped regression
    to payload sourcing fails the build rather than shipping a cross-tenant run
    green."""
    src = (
        REPO / "apps/services/gateway/gateway/main.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(src)

    kw_value: ast.expr | None = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _call_name(node.func) == "run_detached":
            kw = next(
                (k for k in node.keywords if k.arg == "organization_id"), None,
            )
            if kw is not None:
                kw_value = kw.value

    assert kw_value is not None, (
        "run_detached in /copilot/chat is called without an organization_id — the "
        "chat run would carry no tenant"
    )

    exprs_src = [ast.get_source_segment(src, kw_value) or ""]
    if isinstance(kw_value, ast.Name):
        for rhs in _assignments_of(tree, kw_value.id):
            exprs_src.append(ast.get_source_segment(src, rhs) or "")

    joined = " ".join(exprs_src)
    assert "user" in joined and "organization_id" in joined, (
        f"run_detached's organization_id does not trace to the authenticated "
        f"user identity: {joined!r}"
    )
    for forbidden in (
        "payload", "input_data", "request_body", "messages", "request.",
    ):
        assert forbidden not in joined, (
            f"/copilot/chat sources organization_id from request input "
            f"({forbidden!r}) — a tenant-spoofing hole (R11): {joined!r}"
        )
