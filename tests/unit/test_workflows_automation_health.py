"""Automation health — auto-disable on repeated failure (spec R2, migration 134).

R2 is "silent automation drift": a published workflow keeps firing on its
schedule long after the business around it changed, and nobody reads a run
list that has been green for months. The mitigation is a policy that stops
the automation itself, so these tests care about exactly two things —

  * it fires when a live automation is genuinely broken, and
  * it never fires on a maker debugging, a recovered workflow, or a workflow
    somebody already switched off.

No Postgres: a scripted fake session replays canned rows per ``execute()`` and
records the statements, so the tests pin the SQL shape as well as the outcome.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException
from gateway.routes.workflows import publish, service

USER = SimpleNamespace(email="ops@fracktal.works")


class _ScriptedDb:
    """Replays *results* (one per execute); records statements + params."""

    def __init__(self, results: list[Any], rowcounts: list[int] | None = None) -> None:
        self._results = list(results)
        self._rowcounts = list(rowcounts or [])
        self.statements: list[str] = []
        self.params: list[dict] = []
        self.committed = False

    async def execute(self, clause: Any, params: dict | None = None) -> Any:
        self.statements.append(str(clause))
        self.params.append(params or {})
        value = self._results.pop(0) if self._results else None
        count = self._rowcounts.pop(0) if self._rowcounts else 1

        class _Result:
            rowcount = count

            def fetchone(self) -> Any:
                return value

            def fetchall(self) -> list:
                return value or []

        return _Result()

    async def commit(self) -> None:
        self.committed = True

    async def close(self) -> None:
        return None


def _install(monkeypatch, module, db: _ScriptedDb) -> None:
    """Patch EVERY DB seam the module holds after H2/H4.

    ``publish`` is member-reached and converted: its seam is the
    ``_tenant_session`` context manager (commit on clean exit, like the real
    wrapper). ``service`` is the unattended run lifecycle and mostly still
    acquires via ``_get_db`` (H4) — the health check commits its own writes
    there.

    ⚠️ **Both, never "whichever" — a module can hold both, and `service` does
    since WS-27aa** (`_pm_lifecycle_sweeper` resolves on `_get_db` and then
    binds `_tenant_session(org)`). The earlier `if hasattr(...): return` form
    stopped patching `_get_db` the moment the second name appeared, and four
    health tests went looking for a real Postgres on 127.0.0.1:5432 — failing
    with `Connect call failed`, which reads as an environment problem rather
    than as a test-helper bug.

    ``organization_id`` is accepted and ignored: what these tests pin is the
    health policy, not the GUC plumbing (``test_tenant_session.py`` owns that).
    """
    if hasattr(module, "_tenant_session"):

        @asynccontextmanager
        async def fake_tenant_session(organization_id: Any = None):
            yield db
            await db.commit()

        monkeypatch.setattr(module, "_tenant_session", fake_tenant_session)

    if hasattr(module, "_get_db"):

        async def fake_get_db() -> Any:
            return db

        monkeypatch.setattr(module, "_get_db", fake_get_db)


def _runs(*statuses: str) -> list[SimpleNamespace]:
    return [SimpleNamespace(status=s) for s in statuses]


def _failures(n: int) -> list[SimpleNamespace]:
    return _runs(*(["failed"] * n))


@pytest.fixture(autouse=True)
def _quiet_activity(monkeypatch):
    """The activity feed is best-effort Redis; record calls instead."""
    calls: list[dict] = []
    monkeypatch.setattr(
        service,
        "publish_workflow_activity",
        lambda wid, name, **fields: calls.append({"workflow_id": wid, **fields}),
    )
    return calls


def _evaluate(**kw: Any) -> bool:
    params: dict[str, Any] = {
        "trigger_kind": "schedule",
        "status": "failed",
        **kw,
    }
    return asyncio.run(
        service.evaluate_automation_health("w1", "Lead intake", **params)
    )


# ── The policy fires ────────────────────────────────────────────────────────


def test_consecutive_unattended_failures_disable_the_workflow(monkeypatch, _quiet_activity) -> None:
    db = _ScriptedDb([_failures(service.AUTO_DISABLE_AFTER), None])
    _install(monkeypatch, service, db)

    assert _evaluate() is True

    update = db.statements[1]
    assert "SET status = 'disabled'" in update
    # CAS on the current status: a concurrent disable must not double-fire.
    assert "AND status = 'published'" in update
    assert "disabled_reason" in update and "disabled_at" in update
    reason = db.params[1]["reason"]
    assert str(service.AUTO_DISABLE_AFTER) in reason
    assert db.committed


def test_disable_is_announced_on_the_activity_feed(monkeypatch, _quiet_activity) -> None:
    """The 'with notification' half of R2 — /observability sees it."""
    db = _ScriptedDb([_failures(service.AUTO_DISABLE_AFTER), None])
    _install(monkeypatch, service, db)

    _evaluate()

    assert len(_quiet_activity) == 1
    event = _quiet_activity[0]
    assert event["workflow_id"] == "w1"
    assert event["status"] == "auto_disabled"
    assert event["consecutive_failures"] == service.AUTO_DISABLE_AFTER


def test_the_streak_query_is_scoped_to_the_health_window(monkeypatch) -> None:
    """Publish/rollback/enable stamp health_since; older runs must not count."""
    db = _ScriptedDb([_failures(service.AUTO_DISABLE_AFTER), None])
    _install(monkeypatch, service, db)

    _evaluate()

    select = db.statements[0]
    assert "health_since" in select and "r.started_at > w.health_since" in select
    # …and only over runs that actually reached a terminal status.
    assert "'succeeded', 'failed', 'cancelled'" in select
    assert db.params[0]["limit"] == service.AUTO_DISABLE_AFTER


# ── The policy holds its fire ───────────────────────────────────────────────


def test_one_short_of_the_threshold_does_not_disable(monkeypatch) -> None:
    db = _ScriptedDb([_failures(service.AUTO_DISABLE_AFTER - 1)])
    _install(monkeypatch, service, db)

    assert _evaluate() is False
    assert not any("SET status = 'disabled'" in s for s in db.statements)


def test_a_success_inside_the_window_breaks_the_streak(monkeypatch) -> None:
    """Consecutive means consecutive — a flaky workflow is not a dead one."""
    runs = _failures(service.AUTO_DISABLE_AFTER - 1) + _runs("succeeded")
    db = _ScriptedDb([runs])
    _install(monkeypatch, service, db)

    assert _evaluate() is False


@pytest.mark.parametrize("kind", ["manual", "api"])
def test_attended_trigger_kinds_never_disable(monkeypatch, kind: str) -> None:
    """A maker hammering Test on a broken draft must not take production down,
    and one agent passing bad arguments must not take it from everyone else."""
    db = _ScriptedDb([_failures(50)])
    _install(monkeypatch, service, db)

    assert _evaluate(trigger_kind=kind) is False
    assert db.statements == []  # not even queried


@pytest.mark.parametrize("status", ["succeeded", "paused", "cancelled"])
def test_only_a_failed_run_triggers_the_check(monkeypatch, status: str) -> None:
    db = _ScriptedDb([_failures(50)])
    _install(monkeypatch, service, db)

    assert _evaluate(status=status) is False
    assert db.statements == []


def test_already_disabled_workflow_is_a_no_op(monkeypatch, _quiet_activity) -> None:
    """The join filters on w.status='published', so a disabled workflow reads
    back no rows — and nothing is announced a second time."""
    db = _ScriptedDb([[]])
    _install(monkeypatch, service, db)

    assert _evaluate() is False
    assert _quiet_activity == []


def test_losing_the_disable_race_reports_no_disable(monkeypatch, _quiet_activity) -> None:
    """Two runs failing at once: the CAS updates zero rows for the loser, which
    must not log or announce a disable it did not perform."""
    db = _ScriptedDb([_failures(service.AUTO_DISABLE_AFTER), None], rowcounts=[1, 0])
    _install(monkeypatch, service, db)

    assert _evaluate() is False
    assert _quiet_activity == []


def test_a_database_failure_never_breaks_the_run(monkeypatch) -> None:
    """Health is bookkeeping; it runs in the run's finally block."""

    async def boom() -> Any:
        raise RuntimeError("pool exhausted")

    monkeypatch.setattr(service, "_get_db", boom)
    assert _evaluate() is False


# ── The wiring from the run lifecycle into the policy ───────────────────────


@pytest.fixture
def _clean_run_state():
    """start_run mutates module-level concurrency counters; reset around it."""
    yield
    service._ACTIVE_TOTAL = 0
    service._ACTIVE_BY_WORKFLOW.clear()
    service._HUBS.clear()


def test_a_finished_run_reports_its_trigger_kind_to_the_policy(monkeypatch) -> None:
    """The wiring, not the policy. A dropped kwarg here would make the whole
    feature silently inert, and no test of the policy alone would notice."""
    seen: list[dict] = []

    async def fake_execute_workflow(*_a: Any, **_kw: Any) -> Any:
        return SimpleNamespace(
            status="failed", error="boom", state={}, node_results={},
            outputs=[], paused_nodes=[], wait_until={},
        )

    async def fake_finish(*_a: Any, **_kw: Any) -> None:
        return None

    async def fake_health(wid: str, name: str, *, trigger_kind: str, status: str) -> bool:
        seen.append({"workflow_id": wid, "trigger_kind": trigger_kind, "status": status})
        return False

    monkeypatch.setattr(service, "execute_workflow", fake_execute_workflow)
    monkeypatch.setattr(service, "_finish_run", fake_finish)
    monkeypatch.setattr(service, "evaluate_automation_health", fake_health)
    # Three args since H-328: the run binds its workflow id and its org into
    # the node seams.
    monkeypatch.setattr(service, "build_node_services", lambda actor, wid, org: None)
    monkeypatch.setattr(service, "publish_workflow_activity", lambda *a, **k: None)

    asyncio.run(
        service._execute_run(
            run_id="r1",
            workflow_id="w1",
            workflow_name="Lead intake",
            serialized={},
            trigger_payload={},
            variables={},
            started_by="scheduler",
            trigger_kind="schedule",
        )
    )

    assert seen == [
        {"workflow_id": "w1", "trigger_kind": "schedule", "status": "failed"}
    ]


def test_start_run_hands_the_trigger_kind_to_the_executor(monkeypatch, _clean_run_state) -> None:
    seen: dict[str, Any] = {}

    async def fake_execute_run(**kw: Any) -> None:
        seen.update(kw)

    monkeypatch.setattr(service, "_execute_run", fake_execute_run)
    _install(monkeypatch, service, _ScriptedDb([None]))

    async def go() -> None:
        await service.start_run(
            workflow_id="w1",
            workflow_name="Lead intake",
            version=1,
            serialized={},
            trigger_kind="webhook",
            trigger_payload={},
            started_by="webhook",
        )
        await asyncio.sleep(0)  # let the supervised task run

    asyncio.run(go())

    assert seen["trigger_kind"] == "webhook"


# ── Recovery: the enable endpoint (spec R2) ─────────────────────────────────


def _wf_row(**over: Any) -> SimpleNamespace:
    base = dict(
        id="w1",
        name="Lead intake",
        description="",
        owner_email="owner@fracktal.works",
        status="disabled",
        latest_version=4,
        graph={"nodes": [], "edges": []},
        variables={},
        hook_token="tok",
        created_at=None,
        updated_at=None,
        disabled_reason="Auto-disabled after 5 consecutive failed runs …",
        disabled_at=None,
    )
    base.update(over)
    return SimpleNamespace(**base)


def test_enable_restores_the_live_version_and_resets_the_window(monkeypatch) -> None:
    db = _ScriptedDb([_wf_row(), None])
    _install(monkeypatch, publish, db)

    out = asyncio.run(publish.enable_workflow("w1", user=USER))

    assert out == {
        "workflow_id": "w1",
        "status": "published",
        "version": 4,
        "already_live": False,
    }
    update = db.statements[1]
    assert "SET status = 'published'" in update
    assert "disabled_reason = NULL" in update
    # Without a fresh window the next failure would re-disable immediately:
    # the failures that tripped the policy are still the newest runs.
    assert "health_since = now()" in update
    # Enable must not mint a version — the graph did not change.
    assert not any("INSERT INTO workflow_versions" in s for s in db.statements)
    assert db.committed


def test_enable_on_a_never_published_workflow_conflicts(monkeypatch) -> None:
    db = _ScriptedDb([_wf_row(status="draft", latest_version=None)])
    _install(monkeypatch, publish, db)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(publish.enable_workflow("w1", user=USER))
    assert exc.value.status_code == 409
    assert "publish" in str(exc.value.detail).lower()


def test_enable_of_a_live_workflow_is_idempotent(monkeypatch) -> None:
    db = _ScriptedDb([_wf_row(status="published", disabled_reason=None)])
    _install(monkeypatch, publish, db)

    out = asyncio.run(publish.enable_workflow("w1", user=USER))

    assert out["already_live"] is True
    assert len(db.statements) == 1  # the load; nothing written


def test_enable_404s_on_an_unknown_workflow(monkeypatch) -> None:
    db = _ScriptedDb([None])
    _install(monkeypatch, publish, db)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(publish.enable_workflow("ghost", user=USER))
    assert exc.value.status_code == 404


# ── Manual disable records its reason the same way ──────────────────────────


def test_manual_disable_records_who_switched_it_off(monkeypatch) -> None:
    """The gallery answers "why is this off?" identically for both paths."""
    db = _ScriptedDb([_wf_row(status="published"), None])
    _install(monkeypatch, publish, db)

    out = asyncio.run(publish.disable_workflow("w1", user=USER))

    assert out["status"] == "disabled"
    assert USER.email in out["disabled_reason"]
    assert "disabled_reason" in db.statements[1]


# ── The health window is reset by every path that arms the workflow ─────────


#: The smallest graph that survives publish validation.
TRIGGER_ONLY_GRAPH = {
    "nodes": [
        {
            "id": "start",
            "type": "trigger",
            "position": {"x": 0, "y": 0},
            "data": {"label": "start", "config": {}},
        }
    ],
    "edges": [],
}


def test_publish_resets_the_health_window(monkeypatch) -> None:
    """A publish is a fix; the failures it fixes must not count against it."""
    row = _wf_row(status="disabled", latest_version=3, graph=TRIGGER_ONLY_GRAPH)
    vrow = SimpleNamespace(published_at=None)
    # load wf → ready modules → INSERT version → UPDATE workflows
    db = _ScriptedDb([row, [], vrow, None])
    _install(monkeypatch, publish, db)
    monkeypatch.setattr(
        "gateway.routes.workflows.catalog.known_agent_names", lambda: set()
    )

    out = asyncio.run(publish.publish_workflow("w1", user=USER))

    assert out["status"] == "published"
    update = next(s for s in db.statements if "SET status = 'published'" in s)
    assert "health_since = now()" in update
    assert "disabled_reason = NULL" in update


def test_rollback_resets_the_health_window(monkeypatch) -> None:
    row = _wf_row(status="disabled", latest_version=3)
    vrow = SimpleNamespace(serialized={"entry": "s", "blocks": []}, graph={})
    db = _ScriptedDb([row, vrow, None, None, []])
    _install(monkeypatch, publish, db)

    asyncio.run(publish.rollback_workflow("w1", 2, user=USER))

    update = next(s for s in db.statements if "SET status = 'published'" in s)
    assert "health_since = now()" in update
