"""WS-39 SP-3a — the work style of a project node, with no database.

Spec: ``project-docs/specs/project_management_app.md`` §13.1 (the catalogue,
rules 2 and 3) and the row SP-3a of §13.6. Migration: the file that adds
``pm_projects.work_style``, found by CONTENT, never by number (R1).

What this file holds:

* the list of styles: five in the order of §13.1, two live, Flow the default;
  the picker's list in ``lib/workStyle.ts`` is the same list (R7);
* the migration, read as text: expand only, a default of ``flow``, a length
  CHECK added NOT VALID and then validated, ``lock_timeout`` first, and no
  value list in SQL;
* the write: a PATCH round trip on the fake, a 422 for a bad style, and a
  timeline row for each change;
* **a change never deletes** (§13.1 rule 2): Sprints, then Flow, then Sprints
  writes only the one column, and every other row stays;
* the backlog filter of ``GET /projects/tasks``: dark behind the flag, and a
  422 for an unknown value.

The R8 half is ``tests/unit/test_project_work_style_r8.py``.

**Mutations this file catches.** Copy the file aside first, and restore it
from the copy.

* ``core.validate_work_style``: return at once ->
  ``test_a_bad_style_is_a_422_before_any_sql``.
* ``tasks.cycle_filter_clauses``: drop the flag check ->
  ``test_the_backlog_filter_is_dark_behind_the_flag``.
"""

from __future__ import annotations

import asyncio
import re
import uuid
from pathlib import Path

import pytest

pytest.importorskip("gateway.routes.projects", reason="gateway not installed")

from fastapi import HTTPException
from gateway.routes.projects import core, tasks, tree
from gateway.routes.projects.core import ProjectIn, ProjectModel
from pydantic import ValidationError

from tests.unit._projects_fakes import (
    FakeProjectsDB,
    bind_db,
    projects_user,
    silence_events,
)

_REPO = Path(__file__).resolve().parents[2]
_MIGRATIONS = _REPO / "infra" / "postgres"
_TS = _REPO / "workbench/control_plane/src/app/projects/lib/workStyle.ts"

USER = projects_user()


def run(coro):
    return asyncio.run(coro)


# ── The list of styles ──────────────────────────────────────────────────────


def test_the_five_styles_of_the_catalogue_in_order_with_flow_the_default() -> None:
    assert core.WORK_STYLES == ("flow", "sprints", "shape_up", "plan", "personal")
    assert core.LIVE_WORK_STYLES == ("flow", "sprints")
    assert set(core.LIVE_WORK_STYLES) <= set(core.WORK_STYLES)
    assert core.DEFAULT_WORK_STYLE == "flow"
    # A row from before the column, or a fake row with no key, reads Flow.
    assert ProjectModel.model_fields["work_style"].default == "flow"


def test_the_picker_offers_the_same_keys_and_the_same_live_ones() -> None:
    """``lib/workStyle.ts`` is the client's copy. A key that the picker offers
    and the server refuses would be a dead click, so the two are held equal."""
    src = _TS.read_text(encoding="utf-8")
    body = src.split("export const WORK_STYLES", 1)[1].split("];", 1)[0]
    keys = re.findall(r'key:\s*"([a-z_]+)"', body)
    live = re.findall(r"live:\s*(true|false)", body)
    assert tuple(keys) == core.WORK_STYLES
    assert tuple(k for k, on in zip(keys, live, strict=True) if on == "true") == core.LIVE_WORK_STYLES
    assert 'DEFAULT_WORK_STYLE: WorkStyleKey = "flow"' in src


# ── The migration, read as text ─────────────────────────────────────────────


def _migration() -> Path:
    found = [
        p for p in sorted(_MIGRATIONS.glob("*.sql"))
        if "ADD COLUMN IF NOT EXISTS work_style" in p.read_text(encoding="utf-8")
        and "pm_projects" in p.read_text(encoding="utf-8")
    ]
    assert len(found) == 1, [p.name for p in found]
    return found[0]


def _sql() -> str:
    return re.sub(r"--.*", "", _migration().read_text(encoding="utf-8"))


def test_the_migration_is_expand_only_with_flow_the_default() -> None:
    sql = _sql()
    assert re.search(
        r"ALTER TABLE pm_projects\s+ADD COLUMN IF NOT EXISTS work_style TEXT NOT NULL DEFAULT 'flow';",
        sql,
    )
    # Expand only (R6): no drop, no rename, no row write, no new table.
    assert not re.search(r"\bDROP\b|\bRENAME\b|\bUPDATE\b|\bDELETE\b|\bINSERT\b", sql, re.I)
    assert "CREATE TABLE" not in sql
    assert not re.search(r"\bGRANT\b", sql)


def test_the_check_holds_the_length_and_is_validated_after_a_lock_timeout() -> None:
    sql = _sql()
    assert sql.index("SET LOCAL lock_timeout") < sql.index("ALTER TABLE pm_projects")
    assert re.search(
        r"ADD CONSTRAINT pm_projects_work_style\s+CHECK \(char_length\(work_style\) BETWEEN 1 AND 40\)"
        r" NOT VALID;",
        sql,
    )
    assert "VALIDATE CONSTRAINT pm_projects_work_style;" in sql
    # Guarded, so a replay of the ladder adds nothing (idempotent).
    assert "conname = 'pm_projects_work_style'" in sql
    # The values live in code: no style name sits in the SQL.
    for style in core.WORK_STYLES[1:]:
        assert f"'{style}'" not in sql, style
    assert sql.strip().startswith("BEGIN;") and sql.strip().endswith("COMMIT;")


# ── The write ───────────────────────────────────────────────────────────────


@pytest.fixture()
def db(monkeypatch) -> FakeProjectsDB:
    fake = FakeProjectsDB()
    bind_db(monkeypatch, fake, (tree,))
    silence_events(monkeypatch, (tree,))
    return fake


def test_a_node_reads_flow_until_somebody_sets_a_style(db: FakeProjectsDB) -> None:
    node = db.seed_project(name="Delivery")
    out = run(tree.get_node(str(node.id), user=USER))
    assert out["work_style"] == "flow"


def test_the_patch_round_trip_and_its_timeline_row(db: FakeProjectsDB) -> None:
    node = db.seed_project(name="Delivery")
    out = run(tree.patch_node(str(node.id), ProjectIn(work_style="sprints"), user=USER))
    assert out["work_style"] == "sprints"
    assert run(tree.get_node(str(node.id), user=USER))["work_style"] == "sprints"
    # "Who turned on sprints here" is a timeline read.
    [entry] = db.activities("field_change")
    assert {c["field"] for c in entry["meta"]["changes"]} == {"work_style"}


@pytest.mark.parametrize(
    ("value", "words"),
    [
        ("kanban", "Unknown work style"),
        ("x" * 100_000, "Unknown work style"),
        ("shape_up", "not available yet"),
        ("plan", "not available yet"),
        ("personal", "not available yet"),
        (None, "always has a work style"),
    ],
    ids=["unknown", "huge", "shape_up", "plan", "personal", "null"],
)
def test_a_bad_style_is_a_422_before_any_sql(db: FakeProjectsDB, value, words) -> None:
    node = db.seed_project(name="Delivery")
    before = len(db.statements)
    with pytest.raises(HTTPException) as err:
        run(tree.patch_node(str(node.id), ProjectIn(work_style=value), user=USER))
    assert err.value.status_code == 422
    assert words in str(err.value.detail)
    # The message never echoes a long value back.
    assert len(str(err.value.detail)) < 300
    assert db.statements[before:] == []


def test_a_create_checks_the_style_too() -> None:
    values = {"name": "New", "work_style": "waterfall"}
    with pytest.raises(HTTPException) as err:
        tree.check_node_values(USER, values)
    assert err.value.status_code == 422


@pytest.mark.parametrize("value", [5, {"style": "sprints"}, ["sprints"]])
def test_a_style_that_is_not_text_is_refused_by_the_body_model(value) -> None:
    with pytest.raises(ValidationError):
        ProjectIn(work_style=value)


def test_a_change_never_deletes(db: FakeProjectsDB) -> None:
    """§13.1 rule 2. Sprints, then Flow, then Sprints again: the cycle rows and
    the task rows stay as they were, no DELETE runs, and the style is the one
    column that changed."""
    node = db.seed_project(name="Delivery")
    db.seed("pm_cycles", project_id=str(node.id), name="Sprint 1", state="planned")
    db.seed("pm_cycle_tasks", cycle_id=str(uuid.uuid4()), task_id=str(uuid.uuid4()))
    kept = {t: [dict(r) for r in db.rows(t)] for t in ("pm_cycles", "pm_cycle_tasks")}
    for style in ("sprints", "flow", "sprints"):
        out = run(tree.patch_node(str(node.id), ProjectIn(work_style=style), user=USER))
        assert out["work_style"] == style
    assert not [s for s in db.statements if re.search(r"\bDELETE\b", s, re.I)]
    for table, rows in kept.items():
        assert db.rows(table) == rows, table
    writes = [s for s in db.statements if re.search(r"\bUPDATE\s+pm_projects\b", s)]
    assert len(writes) == 3
    for statement in writes:
        assert "work_style" in statement
        assert "name" not in statement.split("WHERE", 1)[0].split("SET", 1)[1]


def test_an_agent_cannot_set_the_style_yet() -> None:
    """SP-5 gives the chat and the MCP the style (G26). Until then a signed
    agent write of a node names no style."""
    assert "work_style" not in tree.AGENT_NODE_PATCH_FIELDS
    assert "work_style" not in tree.AGENT_NODE_CREATE_FIELDS


# ── The backlog filter of the task list ─────────────────────────────────────


def _user(org: str):
    from types import SimpleNamespace

    return SimpleNamespace(organization_id=org)


def test_the_backlog_filter_is_dark_behind_the_flag(monkeypatch) -> None:
    from acb_common import get_settings

    org = str(uuid.uuid4())
    settings = get_settings()
    monkeypatch.setattr(settings, "projects_sprints_orgs", "")
    assert tasks.cycle_filter_clauses(None, _user(org)) == []
    with pytest.raises(HTTPException) as err:
        tasks.cycle_filter_clauses("none", _user(org))
    assert err.value.status_code == 404
    monkeypatch.setattr(settings, "projects_sprints_orgs", org)
    assert tasks.cycle_filter_clauses("none", _user(org)) == [tasks.NO_OPEN_CYCLE_SQL]
    with pytest.raises(HTTPException) as err:
        tasks.cycle_filter_clauses("closed", _user(org))
    assert err.value.status_code == 422


def test_the_backlog_filter_counts_only_open_cycles() -> None:
    sql = tasks.NO_OPEN_CYCLE_SQL
    assert sql.startswith("NOT EXISTS")
    assert "ct.task_id = t.id" in sql and "ct.cycle_open" in sql
    # A query parameter of the one list, and never a saved-view filter.
    import inspect

    from gateway.routes.projects.filters import build_task_filters

    assert "cycle" in inspect.signature(tasks.list_tasks).parameters
    assert "cycle" not in inspect.signature(build_task_filters).parameters
