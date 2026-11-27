"""The wait node (spec F3 logic vocabulary): inline vs durable pause.

A short wait sleeps inside the run; a long one pauses with a deadline in the
pause snapshot so it outlives the gateway process, and the schedule scanner
resumes it through the SAME path the approvals inbox uses. No Postgres, no
sleeping in real time (durations are chosen around the inline threshold).
"""

from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

from gateway.routes.workflows import scheduler, service, unattended
from gateway.routes.workflows.engine.graph import compile_graph, validate_graph
from gateway.routes.workflows.engine.handlers import (
    MAX_WAIT_SECONDS,
    WAIT_INLINE_MAX_SECONDS,
    NodeServices,
    resolve_wait_seconds,
)
from gateway.routes.workflows.engine.runner import execute_workflow


def _node(nid: str, ntype: str, config: dict | None = None) -> dict:
    return {
        "id": nid,
        "type": ntype,
        "position": {"x": 0, "y": 0},
        "data": {"label": nid, "config": config or {}},
    }


def _edge(src: str, tgt: str) -> dict:
    return {"id": f"{src}->{tgt}", "source": src, "target": tgt}


def _graph(seconds: Any) -> dict:
    return {
        "nodes": [
            _node("start", "trigger"),
            _node("hold", "wait", {"seconds": seconds}),
            _node("done", "output", {"value": "finished"}),
        ],
        "edges": [_edge("start", "hold"), _edge("hold", "done")],
    }


def _services() -> NodeServices:
    async def run_agent(agent: str, message: str, model: str | None) -> str:
        return ""

    async def run_tool(action: str, args: dict, actor: str) -> dict:
        return {}

    async def get_module_code(module_id: str) -> str | None:
        return None

    return NodeServices(
        run_agent=run_agent, run_tool=run_tool, get_module_code=get_module_code
    )


def _run(seconds: Any, **kw: Any):
    serialized = compile_graph(_graph(seconds))
    return serialized, asyncio.run(
        execute_workflow(serialized, {}, _services(), **kw)
    )


# ── Validation ──────────────────────────────────────────────────────────────


def test_wait_needs_a_positive_duration() -> None:
    assert not validate_graph(_graph(30))
    for bad in (0, -5, "abc", None):
        codes = {i.code for i in validate_graph(_graph(bad))}
        assert "missing_config" in codes, bad


def test_wait_duration_is_capped_at_publish() -> None:
    issues = validate_graph(_graph(MAX_WAIT_SECONDS + 1))
    assert any("maximum" in i.message for i in issues)


def test_templated_duration_defers_to_run_time() -> None:
    """A {{ref}} duration can only be checked when the run resolves it."""
    assert not validate_graph(_graph("{{trigger.delay}}"))
    assert resolve_wait_seconds({"seconds": "{{trigger.delay}}"}, {"trigger": {"delay": 90}}) == 90


# ── Engine: inline vs pause ─────────────────────────────────────────────────


def test_short_wait_runs_inline_and_completes() -> None:
    started = time.monotonic()
    _, outcome = _run(0.05)
    assert outcome.status == "succeeded"
    assert outcome.outputs == ["finished"]
    assert outcome.node_results["hold"]["status"] == "ok"
    assert outcome.node_results["hold"]["output"] == {"waited_seconds": 0.05}
    assert outcome.wait_until == {}  # nothing to schedule
    assert time.monotonic() - started < 5  # it really slept 50ms, not 50s


def test_long_wait_pauses_with_a_deadline() -> None:
    before = time.time()
    _, outcome = _run(WAIT_INLINE_MAX_SECONDS + 600)

    assert outcome.status == "paused"
    assert outcome.paused_nodes == ["hold"]
    assert outcome.outputs == []  # downstream did NOT run
    assert outcome.node_results["done"]["status"] == "pending"
    resume_at = outcome.wait_until["hold"]
    assert before + 600 <= resume_at <= time.time() + WAIT_INLINE_MAX_SECONDS + 601
    assert outcome.node_results["hold"]["resume_at"] == resume_at


def test_elapsed_wait_resumes_and_finishes() -> None:
    """The resume pass: the matured wait is named, so it runs through and the
    rest of the graph completes — replaying earlier nodes from cache."""
    serialized, paused = _run(WAIT_INLINE_MAX_SECONDS + 600)
    precomputed = {
        nid: r["output"]
        for nid, r in paused.node_results.items()
        if r["status"] == "ok"
    }
    started = time.monotonic()
    outcome = asyncio.run(
        execute_workflow(
            serialized,
            {},
            _services(),
            precomputed=precomputed,
            elapsed_waits={"hold"},
        )
    )
    assert outcome.status == "succeeded"
    assert outcome.outputs == ["finished"]
    assert outcome.node_results["start"]["cached"] is True
    # It already served its time while paused — resuming must NOT sleep again.
    assert time.monotonic() - started < 5


def test_bad_runtime_duration_fails_the_node_cleanly() -> None:
    _, outcome = _run("{{trigger.delay}}")  # unresolved → not a number
    assert outcome.status == "failed"
    assert "hold" in (outcome.error or "")
    assert outcome.node_results["hold"]["status"] == "error"


# ── Persistence: the pause row a wait writes ────────────────────────────────


class _CaptureDb:
    def __init__(self) -> None:
        self.params: list[dict] = []

    async def execute(self, clause: Any, params: dict | None = None) -> Any:
        self.params.append(params or {})

        class _R:
            def fetchone(self):
                return None

            def fetchall(self):
                return []

        return _R()

    async def commit(self) -> None:
        return None

    async def close(self) -> None:
        return None


def test_wait_pause_writes_reason_wait_and_files_no_proposal(monkeypatch) -> None:
    """A wait needs no human, so it must NOT land in the approvals inbox."""
    db = _CaptureDb()

    async def fake_get_db() -> Any:
        return db

    proposed: list[str] = []
    monkeypatch.setattr(service, "_get_db", fake_get_db)
    monkeypatch.setattr(
        service, "publish_workflow_activity", lambda *a, **k: proposed.append("activity")
    )

    resume_at = time.time() + 900
    asyncio.run(
        service._hold_at_gate(
            run_id="r1",
            workflow_id="w1",
            workflow_name="Nudge",
            node_id="hold",
            serialized={"blocks": [{"id": "hold", "label": "Wait"}]},
            trigger_payload={},
            variables={},
            started_by="tester",
            resume_at=resume_at,
        )
    )

    assert len(db.params) == 1
    row = db.params[0]
    assert row["reason"] == "wait"
    snapshot = json.loads(row["snapshot"])
    assert snapshot["resume_at"] == resume_at
    assert snapshot["action_id"] is None  # no broker proposal for a wait


def test_approval_pause_still_files_a_proposal(monkeypatch) -> None:
    """Regression guard on the shared gate helper: approvals keep their inbox
    proposal (resume_at=None is what distinguishes the two)."""
    db = _CaptureDb()
    calls: list[str] = []

    async def fake_get_db() -> Any:
        return db

    async def fake_submit(proposal: Any) -> dict:
        calls.append(getattr(proposal, "action", "?"))
        return {"action_id": "act-1"}

    monkeypatch.setattr(service, "_get_db", fake_get_db)
    import action_broker.broker as broker

    monkeypatch.setattr(broker, "submit", fake_submit)

    asyncio.run(
        service._hold_at_gate(
            run_id="r2",
            workflow_id="w1",
            workflow_name="Nudge",
            node_id="gate",
            serialized={"blocks": [{"id": "gate", "label": "Approve"}]},
            trigger_payload={},
            variables={},
            started_by="tester",
        )
    )
    assert calls == ["workflow.resume_run"]
    assert db.params[0]["reason"] == "approval"
    assert json.loads(db.params[0]["snapshot"])["action_id"] == "act-1"


# ── Scheduler: matured waits resume, immature ones don't ────────────────────


def _pause_row(run_id: str, resume_at: float) -> SimpleNamespace:
    return SimpleNamespace(
        id=f"p-{run_id}", run_id=run_id, snapshot={"resume_at": resume_at},
        organization_id="org-1",
    )


def _install_wait_rows(monkeypatch, rows: list[SimpleNamespace]) -> list[str]:
    class _Db:
        async def execute(self, clause: Any, params: dict | None = None) -> Any:
            class _R:
                def fetchall(self):
                    return rows

                def fetchone(self):
                    return None

            return _R()

        async def commit(self) -> None:
            return None

        async def close(self) -> None:
            return None

    async def fake_orgs() -> list[str]:
        return ["org-1"]

    @asynccontextmanager
    async def fake_tenant_session(org: str | None = None) -> Any:
        yield _Db()

    resumed: list[str] = []

    async def fake_resume(
        run_id: str, *, resumed_by: str = "", organization_id: str | None = None,
        pause_id: str | None = None,
    ) -> dict:
        assert organization_id == "org-1"  # the org of the run row
        assert pause_id == f"p-{run_id}"  # AUTO-7b: the pause that the scan read
        resumed.append(run_id)
        return {"ok": True, "run_id": run_id}

    # H-328 part (b): "org-1" is the one listed org.
    monkeypatch.setattr(scheduler, "_organization_ids", fake_orgs)
    monkeypatch.setattr(scheduler, "_tenant_session", fake_tenant_session)
    monkeypatch.setattr(
        unattended, "get_settings",
        lambda: SimpleNamespace(workflow_unattended_orgs="org-1"),
    )
    monkeypatch.setattr(scheduler, "resume_run", fake_resume)
    return resumed


def test_scan_due_waits_resumes_only_matured_pauses(monkeypatch) -> None:
    now = time.time()
    resumed = _install_wait_rows(
        monkeypatch,
        [_pause_row("due", now - 10), _pause_row("not-yet", now + 3600)],
    )
    assert asyncio.run(scheduler.scan_due_waits(now)) == 1
    assert resumed == ["due"]


def test_scan_due_waits_survives_a_bad_snapshot(monkeypatch) -> None:
    now = time.time()
    bad = SimpleNamespace(
        id="p-x", run_id="broken", snapshot={"resume_at": "soon"}, organization_id="org-1",
    )
    resumed = _install_wait_rows(monkeypatch, [bad, _pause_row("due", now - 1)])
    assert asyncio.run(scheduler.scan_due_waits(now)) == 1
    assert resumed == ["due"]
