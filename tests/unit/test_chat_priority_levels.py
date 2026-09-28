"""H-173 — the chat tools speak the D78 priority levels, not the 0-4 scale.

D78 (2026-09-24) made one priority system: the matrix level, from Important
(``pm_tasks.importance >= 2``), Leveraged (``pm_tasks.leveraged``) and Urgent
(from ``due_at``). This file is the fence (R7) for the chat half:

1. **One source, twice pinned.** ``acb_common.priority`` is the server's only
   copy of the levels. The gateway re-exports it, and its labels, rank order
   and implied flags equal the client's (``lib/priority.ts``,
   ``projects/lib/matrix.ts``). No skill spells a level or a threshold.
2. **Every tool that writes a priority round-trips each level**, by name and
   with no regard to case, and prints the label the app draws.
3. **The deprecated number maps explicitly.** 0 and 1 are not Important, 2
   to 4 are, and anything else is refused. The answer says how it was read.
4. **Every reader prints the level**, never the stored number: a list line, a
   detail read, the table card, the timeline and the CSV export.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("skill_projects", reason="skill-projects not installed")

import acb_common.priority as shared
import skill_projects
from skill_projects import forms, guarded, views
from skill_projects import priority as skill_priority

from tests.unit._projects_agent_fakes import approve, fake_gateway, writes

ROOT = Path(__file__).resolve().parents[2]
CLIENT_PRIORITY = ROOT / "workbench/control_plane/src/app/tasks/lib/priority.ts"
CLIENT_MATRIX = ROOT / "workbench/control_plane/src/app/projects/lib/matrix.ts"
SKILL_DIRS = (
    ROOT / "apps/skills/skill-projects/skill_projects",
    ROOT / "apps/skills/skill-my-tasks/skill_my_tasks",
)

TID = "8f3c1a52-6b1e-4c1e-9f7a-2d8c4e5b6a70"
PID = "9a4d2b63-7c2f-4d2f-8a8b-3e9d5f6c7b81"

#: The levels that need a close due date: Urgent is the due date's to set.
URGENT_LEVELS = frozenset({"critical", "urgent", "quick-leverage"})


def _odd_case(label: str) -> str:
    """"High-Leverage" → "hIGH lEVERAGE": the name, with no regard to case,
    spaces or dashes."""
    return label.swapcase().replace("-", " ")


def _due_for(cell: str) -> str | None:
    """A due date that makes the task urgent exactly when the level is."""
    if cell not in URGENT_LEVELS:
        return None
    return (datetime.now(UTC) + timedelta(hours=2)).isoformat()


# ── 1. One source, pinned to the client ─────────────────────────────────────


def _client_cells() -> list[tuple[str, int, str]]:
    src = CLIENT_PRIORITY.read_text(encoding="utf-8")
    rows = re.findall(
        r'cell: "([a-z-]+)", order: (\d), emoji: "[^"]+", label: "([^"]+)"', src
    )
    assert len(rows) == 7, "CELL_META in priority.ts changed shape"
    return [(cell, int(order), label) for cell, order, label in rows]


def test_the_labels_and_ranks_equal_the_clients() -> None:
    """The label a tool prints is the label the app draws."""
    server = [(cell, meta[0], meta[2]) for cell, meta in shared.CELL_META.items()]
    assert sorted(server) == sorted(_client_cells())
    client_order = [c for c, _o, _l in sorted(_client_cells(), key=lambda r: r[1])]
    assert client_order == shared.CELLS_IN_ORDER


def test_the_flags_each_level_implies_equal_the_clients() -> None:
    """`CELL_FLAGS` is `flagsForCell` in `projects/lib/matrix.ts`."""
    src = CLIENT_MATRIX.read_text(encoding="utf-8")
    body = src[src.index("export function flagsForCell") :]
    body = body[: body.index("\n}")]
    value_of = {
        "both": (True, True),
        "important": (True, False),
        "leveraged": (False, True),
        "": (False, False),
    }
    seen: dict[str, tuple[bool, bool]] = {}
    pending: list[str] = []
    for line in body.splitlines():
        case = re.search(r'case "([a-z-]+)":', line)
        if case:
            pending.append(case.group(1))
        if "default:" in line:
            pending.append("low-priority")  # the one level no case names
        ret = re.search(r'return "(both|important|leveraged|)";', line)
        if ret:
            for cell in pending:
                seen[cell] = value_of[ret.group(1)]
            pending = []
    assert seen == shared.CELL_FLAGS


def test_the_flags_reach_their_level() -> None:
    """A level's flags, with the matching due date, give back that level."""
    for cell, (important, leveraged) in shared.CELL_FLAGS.items():
        got = shared.task_cell(
            {
                "importance": shared.importance_for(important),
                "leveraged": leveraged,
                "due_at": _due_for(cell),
            }
        )
        assert got == cell


def test_the_gateway_re_exports_the_one_source() -> None:
    from gateway.routes.tasks import priority as gateway_priority

    assert gateway_priority.CELL_META is shared.CELL_META
    assert gateway_priority.cell_for_inputs is shared.cell_for_inputs
    assert gateway_priority.IMPORTANT_AT is shared.IMPORTANT_AT


def test_no_skill_carries_a_second_vocabulary() -> None:
    """A level label or an `IMPORTANT_AT =` in a skill is a second copy, and
    a second copy drifts (CLAUDE.md §4). The retired phrase is gone too: it is
    H-173's own Check."""
    labels = [meta[2] for meta in shared.CELL_META.values() if meta[2] != "Important"]
    pattern = re.compile(r"[\"']({})[\"']".format("|".join(map(re.escape, labels))))
    offenders: list[str] = []
    for folder in SKILL_DIRS:
        for path in folder.glob("*.py"):
            src = path.read_text(encoding="utf-8")
            if pattern.search(src) or re.search(r"^IMPORTANT_AT\s*=", src, re.M):
                offenders.append(path.name)
            if "importance is 0 to 4" in src:
                offenders.append(f"{path.name} (the retired phrase)")
    assert offenders == []


# ── The deprecated number ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("number", "important"), [(0, False), (1, False), (2, True), (3, True), (4, True)]
)
def test_the_legacy_number_maps_explicitly(number: int, important: bool) -> None:
    fields, notes = skill_priority.priority_fields(importance=number)
    assert fields == {"importance": shared.importance_for(important)}
    assert "deprecated" in notes[0]
    assert ("not Important" in notes[0]) is (not important)


@pytest.mark.parametrize("number", [5, -3, "two", "2.5"])
def test_a_number_off_the_scale_is_refused(number: Any) -> None:
    out = skill_priority.priority_fields(importance=number)
    assert isinstance(out, str) and "deprecated" in out


def test_the_default_minus_one_means_not_passed() -> None:
    assert skill_priority.priority_fields(importance=-1) == ({}, [])


def test_a_level_and_a_flag_together_are_refused() -> None:
    out = skill_priority.priority_fields(priority="critical", important="true")
    assert isinstance(out, str) and "Not both" in out
    out = skill_priority.priority_fields(priority="critical", importance=2)
    assert isinstance(out, str) and "Not both" in out


def test_an_unknown_level_is_refused_and_lists_the_seven() -> None:
    out = skill_priority.priority_fields(priority="Highest")
    assert isinstance(out, str)
    for meta in shared.CELL_META.values():
        assert meta[2] in out


def test_the_tool_descriptions_name_the_levels() -> None:
    """The model reads the docstring. The names in it come from the source."""
    for tool in (
        skill_projects.create_task,
        skill_projects.update_task,
        skill_projects.bulk_update,
        skill_projects.capture_intake,
    ):
        doc = tool.__doc__ or ""
        assert shared.level_names() in doc, tool.__name__
        assert "0 to 4" not in doc, tool.__name__


# ── 2. The writers round-trip every level ───────────────────────────────────


class Store:
    """A gateway with one task that every write lands on and every read sees."""

    def __init__(self, **task: Any) -> None:
        self.task: dict[str, Any] = {
            "id": TID,
            "title": "Ship it",
            "task_number": 7,
            "project_id": PID,
            "root_project_id": PID,
            "status_id": "s1",
            "assignees": [],
            "importance": None,
            "leveraged": False,
            "due_at": None,
            **task,
        }

    def __call__(self, call: dict) -> Any:
        path, method, body = call["path"], call["method"], call["json"] or {}
        if path == "/projects/tasks" and method == "POST":
            self.task.update(body)
            return dict(self.task)
        if path == "/projects/intake":
            self.task.update(body)
            return {"task": dict(self.task)}
        if path == "/projects/tasks/bulk":
            self.task.update(body.get("patch") or {})
            return {"applied": 1, "requested": 1, "skipped": []}
        if path == f"/projects/tasks/{TID}":
            if method == "PATCH":
                self.task.update(body)
            return dict(self.task)
        if path.endswith("/statuses"):
            return {"rows": [{"id": "s1", "name": "To do", "category": "todo"}]}
        if path.endswith("/timeline"):
            return {"rows": [], "total": 0}
        if path.startswith("/projects/nodes/"):
            return {"id": PID, "name": "Ops", "kind": "project"}
        return {"rows": [], "total": 0}


LEVELS = sorted(shared.CELL_META)


def _label(cell: str) -> str:
    return shared.CELL_META[cell][2]


def _assert_prints(out: str, cell: str) -> None:
    """A list line names the level, except Low Priority, which draws no chip."""
    if cell == "low-priority":
        assert "priority" not in out.lower()
    else:
        assert f"priority {_label(cell)}" in out


@pytest.mark.parametrize("cell", LEVELS)
async def test_create_task_round_trips_a_level(monkeypatch, cell: str) -> None:
    asked = approve(monkeypatch)
    store = Store(due_at=_due_for(cell))
    calls = fake_gateway(monkeypatch, store)
    due = (datetime.now(UTC).date().isoformat()) if cell in URGENT_LEVELS else ""
    out = await skill_projects.create_task(PID, "Ship it", due=due, priority=_odd_case(_label(cell)))
    posted = next(c for c in writes(calls) if c["method"] == "POST")["json"]
    important, leveraged = shared.CELL_FLAGS[cell]
    assert posted["importance"] == shared.importance_for(important)
    assert posted["leveraged"] is leveraged
    assert shared.task_cell(store.task) == cell
    _assert_prints(out, cell)
    # The card reads the flags and the level, never the stored number.
    assert f"priority: «{_label(cell)}»" in asked[0]["context"]
    assert "importance:" not in asked[0]["context"]


@pytest.mark.parametrize("cell", LEVELS)
async def test_update_task_round_trips_a_level(monkeypatch, cell: str) -> None:
    asked = approve(monkeypatch)
    store = Store(due_at=_due_for(cell), importance=3 if cell == "low-priority" else None)
    fake_gateway(monkeypatch, store)
    out = await skill_projects.update_task(TID, priority=_odd_case(_label(cell)))
    assert shared.task_cell(store.task) == cell
    _assert_prints(out, cell)
    # The card shows the level before → after, each value fenced.
    card = asked[0]["context"]
    assert re.search(rf"priority: .*«{re.escape(_label(cell))}»$", card, re.M), card
    assert "importance:" not in card
    detail = await skill_projects.task_detail(TID)
    assert f"priority: {_label(cell)}" in detail


async def test_update_task_keeps_a_stored_three(monkeypatch) -> None:
    """An Important task stays as stored: a 3 from before D78 is not a 2."""
    approve(monkeypatch)
    store = Store(importance=3)
    calls = fake_gateway(monkeypatch, store)
    out = await skill_projects.update_task(TID, important="true", leveraged="true")
    patched = next(c for c in writes(calls) if c["method"] == "PATCH")["json"]
    assert patched == {"leveraged": True}
    assert store.task["importance"] == 3
    assert "priority High-Leverage" in out


async def test_update_task_reads_a_legacy_number_and_says_so(monkeypatch) -> None:
    approve(monkeypatch)
    store = Store()
    fake_gateway(monkeypatch, store)
    out = await skill_projects.update_task(TID, importance=3)
    assert store.task["importance"] == shared.IMPORTANT_AT
    assert "importance 3 is deprecated" in out
    assert "priority Important" in out


async def test_clear_priority_empties_both_flags(monkeypatch) -> None:
    approve(monkeypatch)
    store = Store(importance=2, leveraged=True)
    fake_gateway(monkeypatch, store)
    await skill_projects.update_task(TID, clear="priority")
    assert store.task["importance"] is None
    assert store.task["leveraged"] is False


@pytest.mark.parametrize("cell", LEVELS)
async def test_bulk_update_round_trips_a_level(monkeypatch, cell: str) -> None:
    asked = approve(monkeypatch)
    store = Store(due_at=_due_for(cell))
    fake_gateway(monkeypatch, store)
    await skill_projects.bulk_update(TID, priority=_odd_case(_label(cell)))
    assert shared.task_cell(store.task) == cell
    impact = asked[0]["detail"]
    important, leveraged = shared.CELL_FLAGS[cell]
    assert f"important → {'yes' if important else 'no'}" in impact
    assert f"leveraged → {'yes' if leveraged else 'no'}" in impact
    assert "importance" not in impact


@pytest.mark.parametrize("cell", LEVELS)
async def test_capture_intake_round_trips_or_refuses_a_level(monkeypatch, cell: str) -> None:
    """The intake route stores no Leveraged, so a leveraged level is refused
    with zero writes, never dropped without a word."""
    approve(monkeypatch)
    store = Store(due_at=_due_for(cell))
    calls = fake_gateway(monkeypatch, store)
    out = await skill_projects.capture_intake("Ship it", PID, priority=_odd_case(_label(cell)))
    if shared.CELL_FLAGS[cell][1]:
        assert "cannot set Leveraged" in out
        assert writes(calls) == []
        return
    assert shared.task_cell(store.task) == cell
    _assert_prints(out, cell)


@pytest.mark.parametrize("cell", LEVELS)
def test_a_plan_row_round_trips_a_level(cell: str) -> None:
    """`propose_plan`: the row keeps the flags (the card sends them back),
    the POST body carries them, and the card line names the level."""
    item = {
        "title": "Call the vendor",
        "owner": "priya@x.io",
        "effort_mins": 60,
        "due": (datetime.now(UTC) + timedelta(days=1 if cell in URGENT_LEVELS else 30))
        .date()
        .isoformat(),
        "level": _odd_case(_label(cell)),
    }
    row = forms._plan_row(1, item)
    assert isinstance(row, dict), row
    # What the card sends back on submit is re-read the same way.
    again = forms._plan_row(1, {**item, "level": "", **{k: row[k] for k in ("important", "leveraged")}})
    assert again["important"] == row["important"] and again["leveraged"] == row["leveraged"]
    body = forms._row_flags(row)
    assert shared.task_cell({**body, "due_at": row["due"]}) == cell
    assert f"priority {_label(cell)}" in forms._task_card_line(row, "Priya")


def test_the_edit_form_draws_the_two_flags() -> None:
    fields = {"importance": 3, "leveraged": True}
    changes: dict[str, Any] = {}
    refused = forms._numeric_changes(fields, {"important": False, "leveraged": True}, changes, [])
    assert refused == ""
    assert changes == {"important": "false"}


# ── My Tasks ────────────────────────────────────────────────────────────────


core = pytest.importorskip("skill_my_tasks.core", reason="skill-my-tasks not installed")


class MyStore:
    """`_request` for the My Tasks skill: one task, patched in place."""

    def __init__(self, **task: Any) -> None:
        self.task: dict[str, Any] = {
            "id": TID,
            "title": "Ship it",
            "disposition": "NEXT",
            "project_id": PID,
            "importance": None,
            "leveraged": False,
            "due_at": None,
            "assignees": [],
            **task,
        }
        self.patches: list[dict[str, Any]] = []

    async def __call__(self, method: str, path: str, **kw: Any) -> Any:
        body = kw.get("json") or {}
        if method == "PATCH" and path == f"/projects/tasks/{TID}":
            self.patches.append(body)
            self.task.update(body)
            return dict(self.task)
        if path == "/projects/my/project":
            return {"id": PID, "name": "My Tasks"}
        if path.startswith(f"/projects/my/tasks/{TID}") and path.endswith("/lanes"):
            return {"rows": []}
        if path.startswith(("/projects/my/tasks/", "/projects/tasks/")):
            return dict(self.task) if path.rstrip("/").endswith(TID) else {"rows": [], "total": 0}
        return {"rows": [], "total": 0}


@pytest.mark.parametrize("cell", LEVELS)
async def test_my_tasks_update_round_trips_a_level(monkeypatch, cell: str) -> None:
    store = MyStore(due_at=_due_for(cell))
    monkeypatch.setattr(core, "_request", store)
    monkeypatch.setattr(core, "_current_user_email", lambda: "alice@fracktal.in")
    out = await core.my_tasks_update(item_id=TID, priority=_odd_case(_label(cell)))
    assert shared.task_cell(store.task) == cell
    _assert_prints(out, cell)
    detail = await core.my_tasks_detail(TID)
    assert f"priority: {_label(cell)}" in detail


async def test_my_tasks_update_refuses_a_level_with_a_flag(monkeypatch) -> None:
    store = MyStore()
    monkeypatch.setattr(core, "_request", store)
    out = await core.my_tasks_update(item_id=TID, priority="critical", leveraged="true")
    assert "Not both" in out
    assert store.patches == []


# ── 4. The readers print the level ──────────────────────────────────────────


def test_the_table_card_prints_the_level_column() -> None:
    src = Path(views.__file__).read_text(encoding="utf-8")
    assert '"Due", "Priority"]' in src
    assert "level_label(t)" in src


async def test_the_timeline_prints_important_not_the_number(monkeypatch) -> None:
    def answer(call: dict) -> Any:
        if call["path"].endswith("/timeline"):
            return {
                "rows": [
                    {
                        "type": "field_change",
                        "created_by": "a@x.io",
                        "meta": {"field": "importance", "before": 0, "after": 3},
                    }
                ],
                "total": 1,
            }
        return Store()(call)

    fake_gateway(monkeypatch, answer)
    out = await skill_projects.task_detail(TID)
    assert "important «no» → «yes»" in out
    assert "importance" not in out.split("Timeline", 1)[-1]


def test_the_csv_export_prints_the_level() -> None:
    from gateway.routes.projects import export

    for cell in LEVELS:
        important, leveraged = shared.CELL_FLAGS[cell]
        task = {
            "importance": shared.importance_for(important),
            "leveraged": leveraged,
            "due_at": _due_for(cell),
        }
        assert export._render("importance", task, {}) == _label(cell)


def test_the_bulk_patch_refuses_a_set_and_a_clear_of_one_flag() -> None:
    out = guarded._bulk_patch("", -1, "", "", 0, "important", priority="important")
    assert isinstance(out, str) and "both set and cleared" in out
