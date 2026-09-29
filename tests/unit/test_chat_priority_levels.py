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
3. **The 0-4 number is gone (H-196).** No tool schema offers ``importance``,
   and a stale call that still sends it is refused by name, with zero
   writes. A flag reads true or false only: "1" and "0" are refused, because
   the retired scale read 1 as NOT important.
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


# ── The removed number (H-196) ──────────────────────────────────────────────

#: Every tool that took the 0-4 number until H-196.
REMOVED_FROM = ("create_task", "update_task", "bulk_update", "capture_intake")


def test_the_legacy_map_is_gone_from_the_one_source() -> None:
    """H-196's own Check, as a fence: no second reading of the old scale."""
    assert not hasattr(shared, "LEGACY_IMPORTANCE")
    assert not hasattr(shared, "important_from_legacy")
    for folder in SKILL_DIRS:
        for path in folder.glob("*.py"):
            src = path.read_text(encoding="utf-8")
            assert "importance: int = -1" not in src, path.name
            assert not re.search(
                r"importance[^\n]*deprecated|deprecated[^\n]*importance", src, re.I
            ), path.name


@pytest.mark.parametrize("name", REMOVED_FROM)
def test_no_tool_schema_offers_importance(name: str) -> None:
    """The schema the model reads is the agent framework's, built from the
    signature. `importance` is not in it, and the replacements are."""
    from acb_skills.skill_families import tool_json_schema
    from agent_framework import FunctionTool

    fn = getattr(skill_projects, name)
    props = FunctionTool(func=fn, name=name).parameters()["properties"]
    assert "importance" not in props
    assert {"priority", "important", "leveraged"} <= set(props)
    # The Skills tab's price reads the same schema.
    assert "importance" not in tool_json_schema(fn)["function"]["parameters"]["properties"]
    assert "importance" not in (fn.__doc__ or "")


def _stale_call(name: str) -> dict[str, Any]:
    first = {"create_task": {"project_id": PID, "title": "Ship it"},
             "update_task": {"task_id": TID},
             "bulk_update": {"task_ids": TID},
             "capture_intake": {"title": "Ship it", "project_id": PID}}[name]
    return {**first, "importance": 3}


@pytest.mark.parametrize("name", REMOVED_FROM)
async def test_a_stale_importance_call_is_refused_by_name(monkeypatch, name: str) -> None:
    """Through the agent framework, as a model's call arrives. The framework
    drops an unknown argument without a word, so a parameter that simply
    vanished would write a task with no priority. It must refuse instead,
    name the replacements, and write nothing."""
    from agent_framework import FunctionTool

    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, Store())
    tool = FunctionTool(func=getattr(skill_projects, name), name=name)
    out = await tool.invoke(arguments=_stale_call(name), skip_parsing=True)
    assert out == skill_priority.IMPORTANCE_REMOVED
    for word in ("priority", "important", "leveraged", shared.level_names()):
        assert word in out
    assert writes(calls) == [] and asked == []


def test_a_plan_row_with_importance_is_refused() -> None:
    row = forms._plan_row(
        1,
        {"title": "Call the vendor", "owner": "priya@x.io", "effort_mins": 60,
         "due": "2026-12-01", "importance": 3},
    )
    assert row == f"Task 1: {skill_priority.IMPORTANCE_REMOVED}"


@pytest.mark.parametrize("value", ["1", "0", 1, 0, " 1 ", "2", 0.5])
def test_a_flag_refuses_a_number(value: Any) -> None:
    """Review of PR #509, P3 (a). The old scale read 1 as NOT important, and a
    flag read "1" as true. One string, two meanings: a number is refused."""
    out = skill_priority.priority_fields(important=value)
    assert isinstance(out, str) and "Pass true or false" in out
    out = skill_priority.priority_fields(leveraged=value)
    assert isinstance(out, str) and "Pass true or false" in out


@pytest.mark.parametrize(
    ("value", "want"),
    [("true", True), ("Yes", True), ("on", True), (True, True),
     ("false", False), ("no", False), ("off", False), (False, False),
     ("", None), (None, None)],
)
def test_a_flag_reads_the_words(value: Any, want: bool | None) -> None:
    assert skill_priority.flag(value) is want


def test_a_level_and_a_flag_together_are_refused() -> None:
    out = skill_priority.priority_fields(priority="critical", important="true")
    assert isinstance(out, str) and "Not both" in out


def test_an_unknown_level_is_refused_and_lists_the_seven() -> None:
    out = skill_priority.priority_fields(priority="Highest")
    assert isinstance(out, str)
    for meta in shared.CELL_META.values():
        assert meta[2] in out


def test_the_tool_descriptions_name_the_levels() -> None:
    """The model reads the docstring. The names in it come from the source."""
    # H-196: a capture takes every level now, leveraged ones included.
    for name in REMOVED_FROM:
        doc = getattr(skill_projects, name).__doc__ or ""
        assert shared.level_names() in doc, name
        assert "important and leveraged" in doc, name
        assert "0 to 4" not in doc, name


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
            return {"applied": 1, "requested": 1, "skipped": [], "results": [{"task_id": TID}]}
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


async def test_a_bulk_important_writes_two_over_a_stored_three(monkeypatch) -> None:
    """Review of PR #509, P3 (b), kept on purpose and pinned here. A mixed
    selection has no one `current`, so the bulk bar's rule writes the flag as
    it stands: a 3 becomes a 2. Both read as Important, so the level holds."""
    approve(monkeypatch)
    store = Store(importance=3)
    calls = fake_gateway(monkeypatch, store)
    await skill_projects.bulk_update(TID, important="true")
    body = next(c for c in writes(calls) if c["path"] == "/projects/tasks/bulk")["json"]
    assert body["patch"] == {"importance": shared.IMPORTANT_AT}
    assert shared.task_cell(store.task) == "important"


@pytest.mark.parametrize(
    ("asked", "due", "reads", "why"),
    [
        ("Critical", None, "High-Leverage", "has no due date"),
        ("Urgent", None, "Important", "has no due date"),
        ("Quick Leverage Win", "2099-01-01", "Speculative Bet", "is not due within 48 hours"),
        ("High-Leverage", "2000-01-01", "Critical", "is due within 48 hours, or overdue"),
    ],
)
async def test_the_answer_says_when_the_due_date_moves_the_level(
    monkeypatch, asked: str, due: str | None, reads: str, why: str
) -> None:
    """Review of PR #509, P3 (c). `priority` sets two flags, and the due
    date sets Urgent. Critical on a task with no due date reads
    High-Leverage, and the answer must say so, or the model reports the level
    it asked for."""
    approve(monkeypatch)
    fake_gateway(monkeypatch, Store(due_at=due))
    out = await skill_projects.update_task(TID, priority=asked)
    assert f"you asked for {asked}, and the task reads {reads}" in out
    assert why in out
    # create, bulk and capture carry the same note.
    fake_gateway(monkeypatch, Store(due_at=due))
    out = await skill_projects.create_task(PID, "Ship it", due=due or "", priority=asked)
    assert f"the task reads {reads}" in out
    fake_gateway(monkeypatch, Store(due_at=due))
    out = await skill_projects.capture_intake("Ship it", PID, due=due or "", priority=asked)
    assert f"the task reads {reads}" in out
    fake_gateway(monkeypatch, Store(due_at=due))
    out = await skill_projects.bulk_update(TID, priority=asked, due=due or "")
    assert f"the task reads {reads}" in out


async def test_no_note_when_the_level_is_the_one_asked_for(monkeypatch) -> None:
    approve(monkeypatch)
    fake_gateway(monkeypatch, Store(due_at=_due_for("critical")))
    out = await skill_projects.update_task(TID, priority="Critical")
    assert "priority Critical" in out and "you asked for" not in out


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
async def test_capture_intake_round_trips_a_level(monkeypatch, cell: str) -> None:
    """H-196: `IntakeIn` takes `leveraged`, so a capture takes every level,
    High-Leverage included, and POSTs both flags."""
    approve(monkeypatch)
    store = Store(due_at=_due_for(cell))
    calls = fake_gateway(monkeypatch, store)
    out = await skill_projects.capture_intake("Ship it", PID, priority=_odd_case(_label(cell)))
    posted = next(c for c in writes(calls) if c["path"] == "/projects/intake")["json"]
    important, leveraged = shared.CELL_FLAGS[cell]
    assert posted["importance"] == shared.importance_for(important)
    assert posted["leveraged"] is leveraged
    assert shared.task_cell(store.task) == cell
    _assert_prints(out, cell)


async def test_capture_intake_takes_leveraged_directly(monkeypatch) -> None:
    approve(monkeypatch)
    store = Store()
    calls = fake_gateway(monkeypatch, store)
    out = await skill_projects.capture_intake(
        "Ship it", PID, important="true", leveraged="true"
    )
    posted = next(c for c in writes(calls) if c["path"] == "/projects/intake")["json"]
    assert (posted["importance"], posted["leveraged"]) == (shared.IMPORTANT_AT, True)
    assert "priority High-Leverage" in out


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


def test_a_plan_row_reads_a_level_sent_as_priority() -> None:
    """Every other tool takes the level as `priority`, so a plan row that
    sends it there is read, never dropped. A number there is the score."""
    item = {
        "title": "Call the vendor",
        "owner": "priya@x.io",
        "effort_mins": 60,
        "due": "2026-12-01",
        "priority": "high leverage",
    }
    row = forms._plan_row(1, item)
    assert isinstance(row, dict)
    assert (row["important"], row["leveraged"]) == (True, True)
    row = forms._plan_row(1, {**item, "priority": 27})
    assert isinstance(row, dict)
    assert "important" not in row and "leveraged" not in row


async def test_a_stated_flag_that_is_also_cleared_is_refused(monkeypatch) -> None:
    """The task already holds the flag, so nothing would change, and the
    clear must still not win (review 2026-09-28)."""
    approve(monkeypatch)
    store = Store(importance=3, leveraged=True)
    calls = fake_gateway(monkeypatch, store)
    out = await skill_projects.update_task(TID, priority="High-Leverage", clear="leveraged")
    assert "both set and cleared" in out
    out = await skill_projects.update_task(TID, important="true", clear="important")
    assert "both set and cleared" in out
    assert writes(calls) == []


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



@pytest.mark.parametrize("field", ["important", "leveraged", "deep_work"])
@pytest.mark.parametrize("value", ["1", "0"])
async def test_my_tasks_update_refuses_a_digit_flag(monkeypatch, field, value) -> None:
    """The My Tasks door reads a flag through the shared ``flag``, so "1" is
    refused there too. Before, its own parser read "1" as true and wrote it."""
    store = MyStore()
    monkeypatch.setattr(core, "_request", store)
    out = await core.my_tasks_update(item_id=TID, **{field: value})
    assert "is a number" in out
    assert store.patches == []


def test_my_tasks_has_no_second_flag_parser() -> None:
    src = Path(core.__file__).read_text(encoding="utf-8")
    assert "def _flag(" not in src

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
    out = guarded._bulk_patch("", None, "", "", 0, "important", priority="important")
    assert isinstance(out, str) and "both set and cleared" in out
