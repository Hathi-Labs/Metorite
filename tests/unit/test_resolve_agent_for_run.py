"""Unit tests for _resolve_agent_for_run — the 'unknown agent' guard.

A chat_session can carry agent_name='unknown' (the /chat/active-sessions
placeholder that leaked into ~42 rows). Dispatching it verbatim 422s with
"Unknown agent 'unknown'". The resolver recovers the real agent from the thread's
most-recent agent_run trace, or raises an ACTIONABLE 422 (not the raw registry
dump) when it can't. These tests lock that contract without a live DB by mocking
the trace query.

See gateway/routes/agent.py::_resolve_agent_for_run.
"""
from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from gateway.routes import agent as ar

#: The tenant the helpers bind (WS-27bm S15, ``projects_ai_chat.md`` §21).
#: This suite runs on the ladder-only shape, so any id binds.
_ORG = "5150aaaa-0000-4000-8000-000000000015"


def _fake_tenant_session(rows: list[str]):
    """Return a contextmanager whose .execute(...).fetchall() yields agent rows."""
    @contextmanager
    def _cm(organization_id=None):
        assert organization_id == _ORG, organization_id
        class _Result:
            def fetchall(self_inner):
                return [SimpleNamespace(agent_name=n) for n in rows]

        class _Sess:
            def execute(self_inner, *a, **k):
                return _Result()

        yield _Sess()

    return _cm


# --------------------------------------------------------------------------
# Non-sentinel names go straight through _validate_agent_name.
# --------------------------------------------------------------------------
def test_real_agent_name_validates_directly():
    with patch.object(ar, "_validate_agent_name", return_value="task-manager") as v:
        assert ar._resolve_agent_for_run("task-manager", "t1", organization_id=_ORG) == "task-manager"
    v.assert_called_once_with("task-manager")


# --------------------------------------------------------------------------
# Sentinel + a thread with a real trace → recovered from the trace.
# --------------------------------------------------------------------------
@pytest.mark.parametrize("sentinel", ["unknown", "UNKNOWN", " unknown ", "", "undefined", "null", "none"])
def test_sentinel_recovers_from_trace(sentinel):
    with patch("acb_graph.tenant_session", _fake_tenant_session(["technical-project-planner"])), \
         patch.object(ar, "_validate_agent_name", side_effect=lambda n: n):
        assert ar._resolve_agent_for_run(sentinel, "thread-42", organization_id=_ORG) == "technical-project-planner"


def test_sentinel_skips_sentinel_traces_and_takes_first_real():
    # Most-recent trace is itself a sentinel; the resolver walks to the first real one.
    with patch("acb_graph.tenant_session", _fake_tenant_session(["unknown", "", "agent-sales-assistant"])), \
         patch.object(ar, "_validate_agent_name", side_effect=lambda n: n):
        assert ar._resolve_agent_for_run("unknown", "thread-42", organization_id=_ORG) == "agent-sales-assistant"


# --------------------------------------------------------------------------
# Sentinel + no recoverable agent → actionable 422 (NOT the registry dump).
# --------------------------------------------------------------------------
def test_sentinel_no_trace_raises_actionable_error():
    with patch("acb_graph.tenant_session", _fake_tenant_session([])):
        with pytest.raises(HTTPException) as ei:
            ar._resolve_agent_for_run("unknown", "thread-empty", organization_id=_ORG)
    assert ei.value.status_code == 422
    detail = str(ei.value.detail).lower()
    assert "pick" in detail and "agent" in detail
    # It must NOT leak the raw registry list the way the old error did.
    assert "registered:" not in detail


def test_sentinel_no_thread_raises_actionable_error():
    with pytest.raises(HTTPException) as ei:
        ar._resolve_agent_for_run("unknown", None, organization_id=_ORG)
    assert ei.value.status_code == 422
    assert "pick" in str(ei.value.detail).lower()


def test_sentinel_all_traces_are_sentinels_raises():
    with patch("acb_graph.tenant_session", _fake_tenant_session(["unknown", "null", ""])):
        with pytest.raises(HTTPException) as ei:
            ar._resolve_agent_for_run("unknown", "thread-x", organization_id=_ORG)
    assert ei.value.status_code == 422


# --------------------------------------------------------------------------
# A trace pointing at a since-removed agent must still be validated (may 422).
# --------------------------------------------------------------------------
def test_recovered_name_is_revalidated():
    def _reject(_n):
        raise HTTPException(status_code=422, detail="Unknown agent 'gone'.")

    with patch("acb_graph.tenant_session", _fake_tenant_session(["gone-agent"])), \
         patch.object(ar, "_validate_agent_name", side_effect=_reject):
        with pytest.raises(HTTPException):
            ar._resolve_agent_for_run("unknown", "thread-stale", organization_id=_ORG)


# --------------------------------------------------------------------------
# WS-27bm S15 (§21): with no tenant the trace read is skipped, fail closed.
# --------------------------------------------------------------------------
def test_sentinel_with_no_tenant_reads_nothing_and_raises():
    def _never(*_a, **_k):
        raise AssertionError("the trace read ran with no tenant")

    with patch("acb_graph.tenant_session", _never), \
         patch("acb_graph.get_session", _never), \
         pytest.raises(HTTPException) as ei:
        ar._resolve_agent_for_run("unknown", "thread-42", organization_id=None)
    assert ei.value.status_code == 422
