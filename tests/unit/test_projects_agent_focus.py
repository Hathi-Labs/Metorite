"""Task #986 — the agent focus needs no database (the unit half of the fence)."""

from __future__ import annotations

import inspect

import pytest

pytest.importorskip("gateway.routes.projects", reason="gateway not installed")

from gateway.routes.projects import agent_summary, filters, tasks

FIVE = ("queued", "working", "waiting", "review", "merged")


def test_the_helper_covers_exactly_the_five_foci() -> None:
    assert filters.AGENT_FOCI == FIVE
    for focus in FIVE:
        assert filters.agent_focus_clause(focus)
    with pytest.raises(ValueError):
        filters.agent_focus_clause("nonsense")


def test_the_run_foci_keep_nothing_with_the_flag_off(monkeypatch) -> None:
    monkeypatch.setattr(filters, "agent_runs_enabled", lambda: False)
    for focus in ("queued", "working", "waiting"):
        assert filters.agent_focus_clause(focus) == "false"
    for focus in ("review", "merged"):
        assert "pm_task_refs" in filters.agent_focus_clause(focus)


def test_the_strip_counts_through_the_helper() -> None:
    src = inspect.getsource(agent_summary)
    assert "agent_focus_clause" in src and "AGENT_FOCI" in src


def test_list_tasks_takes_the_focus_and_never_a_member() -> None:
    names = list(inspect.signature(tasks.list_tasks).parameters)
    assert "agent_focus" in names and "agent_worked" in names
    assert "me" not in names and "viewer" not in names


async def test_an_unknown_focus_is_refused_with_422() -> None:
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as caught:
        await tasks.list_tasks(user=None, agent_focus="nonsense")  # type: ignore[arg-type]
    assert caught.value.status_code == 422
    assert "waiting" in str(caught.value.detail)
