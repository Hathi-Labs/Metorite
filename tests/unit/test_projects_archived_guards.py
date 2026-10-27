"""WS-64 — nothing is filed under an archived project (hermetic).

A child created or moved under an archived parent kept ``archived_at`` NULL, so
it showed in every list the archive hides. ``core.assert_not_archived`` is the
one guard, and each write route calls it after its visibility load.
SQL filters are fenced against Postgres in
``test_projects_archived_filters_r8.py``.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from gateway.routes.projects import me, tree
from gateway.routes.projects.core import assert_not_archived

ROUTES = Path(tree.__file__).parent


def _src(name: str) -> str:
    return (ROUTES / name).read_text(encoding="utf-8")


def test_an_archived_node_is_refused_with_a_422_that_names_it():
    node = SimpleNamespace(name="Old plan", archived_at="2026-10-01")
    with pytest.raises(HTTPException) as err:
        assert_not_archived(node, then="Create the task in")
    assert err.value.status_code == 422
    assert "Old plan is archived" in err.value.detail
    assert "Create the task in an active project" in err.value.detail


def test_an_active_node_passes():
    assert_not_archived(SimpleNamespace(name="x", archived_at=None), then="Move")


def test_a_row_with_no_archived_field_passes():
    assert_not_archived(SimpleNamespace(name="x"), then="Move")


@pytest.mark.parametrize("module,call", [
    ("tree.py", 'assert_not_archived(parent_row, then="Create it in")'),
    ("tree.py", 'assert_not_archived(destination, then="Move it into")'),
    ("tasks.py", 'assert_not_archived(target, then="Create the task in")'),
    ("tasks.py", 'assert_not_archived(dest, then="Move the task into")'),
    ("move.py", 'assert_not_archived(dest, then="Move into")'),
    ("intake.py", 'assert_not_archived(target, then="Capture into")'),
])
def test_each_placing_route_calls_the_guard(module, call):
    assert call in _src(module)


def test_the_portfolio_and_node_summaries_skip_archived_projects():
    assert "archived_at IS NULL" in tree.NODE_DESCENDANTS_SQL
    # Both arms of the recursive walk carry the filter.
    assert tree.NODE_DESCENDANTS_SQL.count("archived_at IS NULL") == 2


def test_the_archived_node_walk_drops_both_filters():
    assert "archived_at IS NULL" not in tree.NODE_DESCENDANTS_ARCHIVED_SQL
    assert "WITH RECURSIVE" in tree.NODE_DESCENDANTS_ARCHIVED_SQL
    assert "NODE_DESCENDANTS_ARCHIVED_SQL" in _src("tree.py")


def test_my_work_sql_filters_the_project():
    from gateway.routes.projects.personal import MY_TASKS_FROM

    assert "proj.archived_at IS NULL" in MY_TASKS_FROM


def test_assigned_to_me_filters_the_project():
    assert "pr.archived_at IS NOT NULL" in Path(me.__file__).read_text(
        encoding="utf-8",
    )
