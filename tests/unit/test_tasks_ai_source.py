"""WS-39 S6d and S8 — the AI and intake routes read ONE seam, and one store.

Spec `my_tasks_cutover.md` §5 S6d and §5 S8 · **D73** · board WS-39.

Three fences, and the first is the one the spec names:

1. **No module in `routes/tasks/`, `routes/projects/` or `apps/skills/` names
   a retired `gtd_` table.** S8 PR 1 widened this from the modules S6d
   re-pointed to the WHOLE of those three trees, plus the WhatsApp intake
   modules S6d scoped. An AST walk refuses every `gtd_` token in a string
   constant, except the tokens in `ALLOWED`, each with a reason. Docstrings
   are exempt: a docstring cannot reach a database. Migrations live outside
   these trees, so the rename prologues are never walked.
2. **The seam answers the one store.** `item_source()` returns the pm arm with
   no flag, and the retired modules stay deleted.
3. **A pm row wears every name the consumers read**, asserted against the
   consumers' own source rather than a list somebody remembered to keep.

The rest checks the arm's SQL shape with a recording fake. The SQL itself is
proven against a real Postgres in `tests/live/live_ws39_s6d.py` (R8).
"""
from __future__ import annotations

import ast
import inspect
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from gateway.routes.projects import core as pm_core
from gateway.routes.projects import item_lens, personal
from gateway.routes.projects.item_lens import PM_ITEMS, _pm_item, pm_disposition
from gateway.routes.projects.personal import _MY_TASKS_SQL
from gateway.routes.tasks import ai as tasks_ai
from gateway.routes.tasks import capture_email, planning
from gateway.routes.tasks.core import _row_to_item
from gateway.routes.tasks.item_source import (
    ItemSource,
    item_source,
    origin_key_sql,
)

from tests.unit._sql_match import hits

ROUTES = Path(tasks_ai.__file__).resolve().parent.parent
REPO = Path(__file__).resolve().parents[2]
SKILLS = REPO / "apps" / "skills"

#: The trees the fence walks whole (S8 PR 1).
TREES = [ROUTES / "tasks", ROUTES / "projects", SKILLS]

#: The WhatsApp intake modules S6d re-pointed. They sit outside the trees and
#: stay fenced one by one.
WHATSAPP = [
    ROUTES / "whatsapp" / "transport" / "capture.py",
    ROUTES / "whatsapp" / "transport" / "context.py",
    ROUTES / "whatsapp" / "automation" / "commitments.py",
    ROUTES / "whatsapp" / "digest.py",
    # WS-39 S8c: the last three readers and writers of the retiring store.
    # Every module in both packages is fenced, not only the three files, so
    # a new one cannot reopen the hole.
    *sorted((ROUTES / "notes").rglob("*.py")),
    *sorted((ROUTES / "email").rglob("*.py")),
]

#: Modules S8 PR 1 deleted. Each served only the retired store, and none had a
#: caller left in the client, the skill, the agents, the operator console or
#: the tests.
RETIRED = ["items", "hierarchy", "accounts", "sync", "providers",
           "broker_handlers", "scheduler"]


def _skill_tool_names() -> frozenset[str]:
    """The 29 chat tool names. S9 renames them all at once, and
    `TaskToolCards.tsx` keys on the names, so they stay until then."""
    init = SKILLS / "skill-task-gtd" / "skill_task_gtd" / "__init__.py"
    tree = ast.parse(init.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                getattr(t, "id", None) == "__all__" for t in node.targets):
            return frozenset(ast.literal_eval(node.value))
    raise AssertionError("skill_task_gtd.__all__ not found")


#: The `gtd_` tokens a string constant may still carry, each with its reason.
#: S8 PR 2 (migration 217) removed the last two tables: `gtd_attachments` is
#: `attachments` now, and `wa_commitments.gtd_item_id` is dropped. Only the
#: tool names are left. `test_no_gtd_table_names.py` is the repo-wide fence.
ALLOWED: dict[str, str] = {
    **{name: "a chat tool name. S9 renames all 29 at once"
       for name in _skill_tool_names()},
}

#: Strings that carry a `gtd_` token and name no table, each with its reason.
ALLOWED_LITERALS: dict[str, str] = {
    "data/gtd_attachments": (
        "the upload DIRECTORY on the box (`routes/tasks/attachments.py`). Each "
        "`attachments` row stores its own path, so the files do not move"),
}


def _fenced_files() -> list[Path]:
    out: list[Path] = []
    for tree in TREES:
        out += [p for p in sorted(tree.rglob("*.py"))
                if "__pycache__" not in p.parts and "tests" not in p.parts]
    return out + WHATSAPP


# ── 1. The fence: no retired `gtd_` table in the three trees ────────────────

def _docstring_nodes(tree: ast.AST) -> set[int]:
    """The ids of every Constant node that is a docstring."""
    out: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef
                      | ast.AsyncFunctionDef):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) \
                    and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                out.add(id(body[0].value))
    return out


def _gtd_strings(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    docs = _docstring_nodes(tree)
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        if id(node) in docs:
            continue
        value = node.value
        for literal in ALLOWED_LITERALS:
            value = value.replace(literal, "")
        bad = sorted(t for t in set(re.findall(r"gtd_\w+", value))
                     if t not in ALLOWED)
        if bad:
            found.append((node.lineno, ", ".join(bad)))
    return found


def test_the_walk_covers_the_three_trees() -> None:
    files = _fenced_files()
    assert len(files) > 40, len(files)
    assert ROUTES / "tasks" / "ai.py" in files
    assert ROUTES / "projects" / "item_lens.py" in files
    assert SKILLS / "skill-task-gtd" / "skill_task_gtd" / "core.py" in files
    assert all(p.exists() for p in WHATSAPP), "a WhatsApp module moved"


@pytest.mark.parametrize(
    "path", _fenced_files(), ids=lambda p: p.relative_to(REPO).as_posix())
def test_no_module_names_a_retired_gtd_table(path: Path) -> None:
    found = _gtd_strings(path)
    assert not found, (
        f"{path.name} names a retired gtd_ table: {found}. The one task store "
        "is pm_tasks + pm_task_personal (D53). Read it through `item_source()` "
        "or `agent_source()`. A survivor goes in ALLOWED with its reason."
    )


def test_the_fence_can_see(tmp_path: Path) -> None:
    """A fence that matches nothing is a fence with a hole."""
    bad = tmp_path / "bad.py"
    bad.write_text(
        '"""A docstring may say gtd_items."""\n'
        'SQL = "SELECT * FROM gtd_items"\n'
        'OK = "call gtd_capture"\n', encoding="utf-8")
    assert _gtd_strings(bad) == [(2, "gtd_items")]


def test_the_allowed_tables_are_still_used() -> None:
    """An ALLOWED table nothing uses is a stale exemption. Drop it."""
    used: set[str] = set()
    for path in _fenced_files():
        used |= set(re.findall(r"gtd_\w+", path.read_text(encoding="utf-8")))
    tables = {k for k, v in ALLOWED.items() if not v.startswith("a chat tool")}
    assert tables <= used, sorted(tables - used)


# ── 2. One store, no switch ─────────────────────────────────────────────────

def _reads() -> list[str]:
    return [
        name for name, member in inspect.getmembers(ItemSource)
        if not name.startswith("_") and callable(member)
        # `find_by_origin` is concrete in the base: the first of
        # `items_by_origin`. The arm does not need its own.
        and name != "find_by_origin"
    ]


def test_the_arm_implements_every_read() -> None:
    gaps = [
        name for name in _reads()
        if getattr(type(PM_ITEMS), name, None) is getattr(ItemSource, name)
    ]
    assert not gaps, (
        f"the pm arm does not override {gaps}. The base raises, so this is a "
        "500 on one route rather than a failure at import."
    )
    assert {"fetch_item", "insert_capture", "mark_done_by_thread"} <= set(_reads())


def test_item_source_is_the_one_store(monkeypatch) -> None:
    """No flag since S8 PR 1. The retired variable changes nothing."""
    assert item_source() is PM_ITEMS
    monkeypatch.setenv("TASKS_LENS", "0")
    assert item_source() is PM_ITEMS
    assert "environ" not in inspect.getsource(item_source)


def test_the_retired_arm_and_modules_stay_deleted() -> None:
    from gateway.routes.tasks import item_source as seam

    for name in ("GTD_ITEMS", "_GtdItems"):
        assert not hasattr(seam, name), name
    for mod in RETIRED:
        assert not (ROUTES / "tasks" / f"{mod}.py").exists(), (
            f"routes/tasks/{mod}.py is back. It served only the retired store.")


def test_origin_key_is_a_closed_set() -> None:
    assert origin_key_sql("email_id") == "origin->>'email_id'"
    with pytest.raises(ValueError):
        origin_key_sql("kind'; DROP TABLE pm_tasks; --")


# ── 3. The pm row carries every name the consumers read ─────────────────────

def _consumer_reads() -> set[str]:
    names: set[str] = set()
    for mod in (tasks_ai, capture_email):
        src = inspect.getsource(mod)
        names |= set(re.findall(r'getattr\(item,\s*"([a-z_]+)"', src))
        names |= set(re.findall(r"\bitem\.([a-z_]+)\b", src))
    src = inspect.getsource(_row_to_item)
    names |= set(re.findall(r'getattr\(row,\s*"([a-z_]+)"', src))
    names |= set(re.findall(r"\brow\.([a-z_]+)\b", src))
    # Not row attributes: pydantic model methods and the wire model's own
    # fields read off an `item` that is a GtdItemModel, not a row.
    return names - {"model_dump", "assignee_name"}


def _full_row(**kw) -> SimpleNamespace:
    base = dict(
        id="11111111-1111-1111-1111-111111111111", title="Reply to Priya",
        description="the quote", project_id="p-root", parent_task_id=None,
        status_id="st-1", status_category="backlog", due_at=None,
        completed_at=None, created_at=datetime(2026, 9, 1, tzinfo=UTC),
        updated_at=datetime(2026, 9, 2, tzinfo=UTC), archived_at=None,
        origin='{"kind": "email", "email_id": "m-1"}', workflow_stage="Inbox",
        subtask_count=0, assignee_count=1, is_mine=True,
        p_disposition=None, p_next_action=None, p_context="@computer",
        p_energy="low", p_time_estimate_mins=10, p_is_two_minute=False,
        p_defer_until=None, p_clarified_at=None, p_scheduled_start=None,
        p_scheduled_end=None, p_flexible=None, p_is_hard_date=None,
        p_actual_start=None, p_actual_end=None, p_important=None,
        p_leveraged=None, p_deep_work=None, p_kept_mine=None, p_sort_key=None,
        p_waiting_on=None, p_delegated_at=None, p_expected_by=None,
        p_last_nudged_at=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_the_pm_row_carries_every_name_the_consumers_read() -> None:
    row = _pm_item(_full_row())
    wanted = _consumer_reads()
    assert {"title", "description", "project_id", "source", "origin"} <= wanted, (
        f"the extractor found {sorted(wanted)}: it has stopped matching the "
        "consumers' reads, so this fence is blind"
    )
    missing = sorted(n for n in wanted if not hasattr(row, n))
    assert not missing, (
        f"a pm row has no {missing}. The routes read these by name and "
        "`getattr(..., None)` does not raise, so a prompt just loses a fact."
    )


def test_the_pm_row_feeds_the_wire_model() -> None:
    """`_row_to_item` is what every capture route returns. It must accept a
    pm row without a branch, origin decoded and disposition effective."""
    model = _row_to_item(_pm_item(_full_row()))
    assert model.origin == {"kind": "email", "email_id": "m-1"}
    assert model.notes == "the quote"
    assert model.source == "LOCAL" and model.provider == "local"
    # backlog + never triaged: SOMEDAY, the same rule `/projects/my/inbox`
    # applies. A stated value wins.
    assert model.disposition == "SOMEDAY"
    assert _pm_item(_full_row(p_disposition="NEXT")).disposition == "NEXT"
    assert _pm_item(_full_row(status_category="done")).disposition == "DONE"


@pytest.mark.parametrize(("raw", "want"), [
    ("INBOX", "INBOX"), ("next", "NEXT"), ("CALENDAR", "NEXT"),
    ("DO_NOW", "NEXT"), ("WAITING", "WAITING"), ("bogus", "INBOX"),
    (None, "INBOX"),
])
def test_a_routers_disposition_fits_the_overlay_check(raw, want) -> None:
    assert pm_disposition(raw) == want


# ── The recording fake ──────────────────────────────────────────────────────

class _Result:
    def __init__(self, rows):
        self._rows = list(rows)

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def scalar(self):
        row = self.fetchone()
        return None if row is None else next(iter(vars(row).values()))


class _FakeDB:
    """Records every statement. `answer(sql, params)` supplies rows."""

    def __init__(self, answer=None):
        self.calls: list[tuple[str, dict]] = []
        self._answer = answer or (lambda sql, params: [])

    async def execute(self, statement, params=None):
        sql = str(statement)
        self.calls.append((sql, dict(params or {})))
        return _Result(self._answer(sql, dict(params or {})))

    def sql(self, clause: str) -> list[tuple[str, dict]]:
        return [(s, p) for s, p in self.calls if hits(s, clause)]


def _patch_pm_helpers(monkeypatch) -> None:
    """The pm arm's collaborators from the projects package, stubbed. The
    seam's own SQL still runs through the fake and is what is asserted."""
    monkeypatch.setattr(item_lens, "resolve_organization_id",
                        AsyncMock(return_value="org-1"))
    monkeypatch.setattr(item_lens, "require_organization_of",
                        AsyncMock(return_value="org-1"))
    monkeypatch.setattr(item_lens, "ensure_personal_project",
                        AsyncMock(return_value=SimpleNamespace(id="root-1")))
    monkeypatch.setattr(item_lens, "load_default_status",
                        AsyncMock(return_value=SimpleNamespace(id="st-1")))
    monkeypatch.setattr(item_lens, "status_owner_id",
                        AsyncMock(return_value="root-1"))
    monkeypatch.setattr(item_lens, "resolve_visibility_for", AsyncMock(
        return_value=SimpleNamespace(
            params={"vis_org": "org-1", "who": "a@x"},
            project_clause=lambda column: f"{column} IN (SELECT id FROM visible)",
        )))

    # The one bind assembler `MY_TASKS_FROM` names (WS-39 S6a). Stubbed so the
    # directory reads it makes (tenant, groups) do not land in `db.calls`; what
    # these tests pin is the pm arm's OWN statements.
    async def binds(db, uid, *, archived, **params):
        who = uid.lower()
        return {"who": who, "vis_org": "org-1", "vis_email": who,
                "vis_groups": [], "archived": archived, **params}
    monkeypatch.setattr(item_lens, "my_tasks_binds", binds)
    # The capture helper's own collaborators, on the module that owns it.
    monkeypatch.setattr(personal, "next_task_number", AsyncMock(return_value=7))
    monkeypatch.setattr(personal, "record_activity", AsyncMock())
    monkeypatch.setattr(personal, "emit", AsyncMock())


def _pm_writes():
    """Rows for the writes the pm arm performs: each INSERT hands back an id."""
    counter = {"n": 0}

    def answer(sql, params):
        if hits(sql, "INSERT INTO pm_tasks"):
            counter["n"] += 1
            return [SimpleNamespace(id=f"t-{counter['n']}")]
        if hits(sql, "INSERT INTO pm_task_personal"):
            return [SimpleNamespace(task_id=params.get("task_id"))]
        return []
    return answer


# ── The pm arm's SQL shape ──────────────────────────────────────────────────

async def test_pm_reads_compose_the_shared_membership_clause(monkeypatch) -> None:
    """Which tasks are mine has one definition. The tenant clause rides on it."""
    _patch_pm_helpers(monkeypatch)
    db = _FakeDB()
    await PM_ITEMS.items_by_origin(db, "Alice@Fracktal.in", "email_id", "m-9")
    (sql, params), = db.calls
    assert sql.startswith(_MY_TASKS_SQL)
    assert "t.organization_id = CAST(:vis_org AS uuid)" in sql
    assert "t.origin->>'email_id' = :val" in sql
    # D77: only TRASH prunes. A stated DONE may have been reopened.
    assert "p.disposition <> 'TRASH'" in sql
    assert params == {"who": "alice@fracktal.in", "vis_org": "org-1",
                      "vis_email": "alice@fracktal.in", "vis_groups": [],
                      "archived": False, "val": "m-9"}


async def test_pm_find_by_origin_rules_in_python_then_limits(monkeypatch) -> None:
    """A closed task with no overlay survives the SQL prune. The Python rule
    drops it, and the LIMIT is applied after, so it cannot hide the open row."""
    _patch_pm_helpers(monkeypatch)
    db = _FakeDB(lambda sql, p: [
        _full_row(id="closed", status_category="done"),
        _full_row(id="open", p_disposition="NEXT"),
    ])
    found = await PM_ITEMS.find_by_origin(db, "a@x", "email_id", "m-1")
    assert found is not None and found.id == "open"


def test_the_s8c_packages_are_in_the_fence() -> None:
    """The notes and email packages are globbed. An empty glob is a fence
    with a hole, so the three S8c modules must each be named."""
    names = {
        p.relative_to(ROUTES).as_posix()
        for p in _fenced_files() if p.is_relative_to(ROUTES)
    }
    assert {"notes/actions.py", "notes/dispatch.py", "email/digest.py",
            "email/automation/drafting.py"} <= names


def test_the_s8c_origin_keys_are_allowed() -> None:
    assert origin_key_sql("action_item_id") == "origin->>'action_item_id'"
    assert origin_key_sql("account_id") == "origin->>'account_id'"


async def test_pm_find_by_action_item_composes_the_membership_clause(
    monkeypatch,
) -> None:
    """S8c: approving a meeting action looks for its earlier capture first."""
    _patch_pm_helpers(monkeypatch)
    db = _FakeDB()
    assert await PM_ITEMS.find_by_origin(
        db, "Alice@x", "action_item_id", "a-1") is None
    (sql, params), = db.calls
    assert sql.startswith(_MY_TASKS_SQL)
    assert "t.origin->>'action_item_id' = :val" in sql
    assert params["val"] == "a-1" and params["who"] == "alice@x"


async def test_pm_hard_dated_items_read_the_overlay_in_my_list(
    monkeypatch,
) -> None:
    """S8c: the email drafter's calendar is my open hard-date tasks. The hard
    date is MY overlay, and a closed task is ruled out in Python."""
    _patch_pm_helpers(monkeypatch)
    soon = datetime.now(UTC) + timedelta(days=1)
    db = _FakeDB(lambda sql, p: [
        _full_row(id="closed", status_category="done", due_at=soon),
        _full_row(id="open", p_disposition="NEXT", due_at=soon),
        _full_row(id="later", p_disposition="NEXT", due_at=soon),
    ])
    items = await PM_ITEMS.hard_dated_items(db, "Alice@x", days=10, limit=1)
    assert [i.id for i in items] == ["open"]
    (sql, params), = db.calls
    assert sql.startswith(_MY_TASKS_SQL)
    assert "p.is_hard_date = true" in sql
    assert "t.due_at >= now()" in sql
    assert "make_interval(days => :days)" in sql
    assert "ORDER BY t.due_at ASC" in sql
    assert "LIMIT" not in sql, "the limit applies after the Python rule"
    assert params["days"] == 10 and params["who"] == "alice@x"


async def test_pm_fetch_item_is_a_404_by_absence(monkeypatch) -> None:
    _patch_pm_helpers(monkeypatch)
    with pytest.raises(HTTPException) as exc:
        await PM_ITEMS.fetch_item(_FakeDB(), "a@x", "nope")
    assert exc.value.status_code == 404


async def test_pm_capture_lands_in_my_root_self_assigned_with_origin(
    monkeypatch,
) -> None:
    _patch_pm_helpers(monkeypatch)
    db = _FakeDB(_pm_writes())
    origin = {"kind": "email", "email_id": "m-1", "thread_id": "th-1"}
    item_id = await PM_ITEMS.insert_capture(db, "Alice@x", {
        "title": "Send the quote", "description": "to Priya",
        "disposition": "CALENDAR", "context": "@computer", "energy": "low",
        "time_estimate_mins": 10, "due_at": datetime(2026, 10, 1, tzinfo=UTC),
        "is_hard_date": True, "clarified_at": datetime.now(UTC),
        "subtasks": ["Draft it", " ", "Send it"],
    }, origin)
    assert item_id == "t-1"

    (task_sql, task_params), *subs = db.sql("INSERT INTO pm_tasks")
    assert "CAST(:origin AS jsonb)" in task_sql, "origin needs the jsonb cast"
    assert isinstance(task_params["origin"], str), "a bare dict has no codec"
    assert task_params["source"] == "email"
    assert task_params["project_id"] == "root-1"
    assert task_params["root_project_id"] == "root-1"
    assert task_params["created_by"] == "alice@x"
    assert len(subs) == 2 and all(p["parent_task_id"] == "t-1" for _s, p in subs)

    assignees = db.sql("INSERT INTO pm_task_assignees")
    assert [p["who"] for _s, p in assignees] == ["alice@x"] * 3

    (ov_sql, ov), *sub_ov = db.sql("INSERT INTO pm_task_personal")
    assert "ON CONFLICT (task_id, member_email)" in ov_sql
    assert ov["member_email"] == "alice@x" and ov["task_id"] == "t-1"
    assert ov["disposition"] == "NEXT", "CALENDAR is a view, not a bucket"
    assert ov["context"] == "@computer" and ov["is_hard_date"] is True
    assert [p["disposition"] for _s, p in sub_ov] == ["NEXT", "NEXT"]
    # Ranked by input order, like plan_apply.
    assert [p["sort_key"] for _s, p in sub_ov] == [0.0, 1000.0]

    assert not db.sql("INSERT INTO gtd_items")
    assert not db.sql("INSERT INTO gtd_waiting")


async def test_pm_waiting_capture_is_the_overlay_not_a_second_table(
    monkeypatch,
) -> None:
    """D53.8: waiting_on / delegated_at on `pm_task_personal`."""
    _patch_pm_helpers(monkeypatch)
    db = _FakeDB(_pm_writes())
    at = datetime.now(UTC)
    await PM_ITEMS.insert_capture(db, "a@x", {
        "title": "Chase the AWB", "disposition": "WAITING",
        "waiting_on": {"name": "Ravi", "email": "ravi@x"}, "delegated_at": at,
    }, {"kind": "whatsapp", "wa_message_id": "w-1"})
    overlays = db.sql("INSERT INTO pm_task_personal")
    waiting = [p for _s, p in overlays if p.get("waiting_on")]
    assert len(waiting) == 1
    assert waiting[0]["disposition"] == "WAITING"
    assert waiting[0]["delegated_at"] == at
    assert isinstance(waiting[0]["waiting_on"], str)
    assert "CAST(:waiting_on AS jsonb)" in overlays[-1][0]
    assert db.sql("INSERT INTO pm_tasks")[0][1]["source"] == "manual"


async def test_pm_set_context_guards_membership_then_upserts(monkeypatch) -> None:
    _patch_pm_helpers(monkeypatch)
    db = _FakeDB(lambda sql, p: (
        [_full_row(p_disposition="NEXT")] if "AND t.id = CAST(:tid AS uuid)" in sql
        else []))
    await PM_ITEMS.set_context(db, "a@x", "11111111-1111-1111-1111-111111111111",
                               "@calls")
    (_ov_sql, ov), = db.sql("INSERT INTO pm_task_personal")
    assert ov == {"task_id": "11111111-1111-1111-1111-111111111111",
                  "member_email": "a@x", "context": "@calls"}
    assert not db.sql("UPDATE gtd_items")

    with pytest.raises(HTTPException):
        await PM_ITEMS.set_context(_FakeDB(), "a@x", "not-mine", "@calls")


async def test_pm_mark_done_by_thread_uses_the_one_completion_path(
    monkeypatch,
) -> None:
    _patch_pm_helpers(monkeypatch)
    done = AsyncMock(return_value={"row": None})
    monkeypatch.setattr(item_lens, "complete_for_member", done)
    db = _FakeDB(lambda sql, p: [
        _full_row(id="open", p_disposition="NEXT"),
        _full_row(id="closed", status_category="done"),
    ] if "t.origin->>'thread_id'" in sql else [])
    ids = await PM_ITEMS.mark_done_by_thread(db, "A@x", "th-1")
    assert ids == ["open"]
    (call,) = done.await_args_list
    assert call.args[1].id == "open" and call.args[2] == "a@x"
    assert await PM_ITEMS.mark_done_by_thread(db, "a@x", "") == []


# ── The workflow engine hears the pm arm ────────────────────────────────────

def _capture_emits(monkeypatch) -> list[tuple[str, dict]]:
    """`core.emit` on the module that owns the helpers, recorded in order.
    The workflow catalog serves `pm.task.created` and `pm.task.status_changed`
    as triggers, so a write that stops emitting breaks every automation with
    the route tests still green."""
    captured: list[tuple[str, dict]] = []

    async def _emit(event_type, payload):
        captured.append((event_type, payload))

    monkeypatch.setattr(personal, "emit", _emit)
    return captured


async def test_pm_capture_emits_task_created_from_the_shared_helper(
    monkeypatch,
) -> None:
    _patch_pm_helpers(monkeypatch)
    events = _capture_emits(monkeypatch)
    db = _FakeDB(_pm_writes())
    item_id = await PM_ITEMS.insert_capture(db, "a@x", {
        "title": "Send the quote", "subtasks": ["Draft it"],
    }, {"kind": "email", "email_id": "m-1"})
    kinds = [e for e, _p in events]
    assert kinds == ["pm.task.created", "pm.task.created"], kinds
    assert events[0][1] == {"task_id": item_id, "project_id": "root-1",
                            "title": "Send the quote"}
    # The route and the seam share the helper, so the event has ONE source.
    src = inspect.getsource(personal.capture)
    assert "create_personal_task" in src and "emit(" not in src


async def test_pm_mark_done_by_thread_emits_status_changed_and_closes_the_thread(
    monkeypatch,
) -> None:
    """The REAL `complete_for_member`: status transition, overlay DONE, the
    email thread marked Done, and the event, from one helper."""
    _patch_pm_helpers(monkeypatch)
    events = _capture_emits(monkeypatch)
    from gateway.routes.tasks import email_link

    propagate = AsyncMock()
    monkeypatch.setattr(email_link, "propagate_task_done_to_thread", propagate)
    monkeypatch.setattr(personal, "load_default_status",
                        AsyncMock(return_value=SimpleNamespace(id="done-1")))
    monkeypatch.setattr(personal, "status_owner_id",
                        AsyncMock(return_value="root-1"))
    moved = {"row": None,
             "from": SimpleNamespace(name="Inbox", category="backlog"),
             "to": SimpleNamespace(name="Done", category="done")}
    transition = AsyncMock(return_value=moved)
    monkeypatch.setattr(pm_core, "apply_status_transition", transition)
    db = _FakeDB(lambda sql, p: [
        _full_row(id="open", p_disposition="NEXT",
                  origin='{"kind": "email", "account_id": "a1", '
                         '"thread_id": "th-1"}'),
    ] if "t.origin->>'thread_id'" in sql else [])

    assert await PM_ITEMS.mark_done_by_thread(db, "A@x", "th-1") == ["open"]
    assert transition.await_args.args[2] == "done-1"
    assert transition.await_args.kwargs == {"created_by": "a@x"}
    (_ov_sql, ov), = db.sql("INSERT INTO pm_task_personal")
    assert ov == {"task_id": "open", "member_email": "a@x", "disposition": "DONE"}
    assert propagate.await_count == 1
    assert propagate.await_args.args[1].origin["thread_id"] == "th-1"
    assert events == [("pm.task.status_changed", {
        "task_id": "open", "from": "Inbox", "to": "Done", "to_category": "done",
    })]
    # The route shares the helper, so it emits from ONE place.
    src = inspect.getsource(personal.complete_task)
    assert "complete_for_member" in src and "emit(" not in src


async def test_pm_assignee_load_is_scoped_to_the_callers_visibility(
    monkeypatch,
) -> None:
    """One member's private tasks never count into another member's roster."""
    _patch_pm_helpers(monkeypatch)
    db = _FakeDB()
    await PM_ITEMS.assignee_load(db, "A@x")
    (sql, params), = db.calls
    assert "t.root_project_id IN (SELECT id FROM visible)" in sql
    assert "GROUP BY 1" in sql and "t.archived_at IS NULL" in sql
    assert params == {"vis_org": "org-1", "who": "a@x"}
    item_lens.resolve_visibility_for.assert_awaited_once_with(db, "a@x")


async def test_pm_update_origin_merges_and_scopes_to_the_tenant(monkeypatch) -> None:
    _patch_pm_helpers(monkeypatch)
    db = _FakeDB(lambda sql, p: (
        [_full_row(p_disposition="NEXT")] if "AND t.id = CAST(:tid AS uuid)" in sql
        else []))
    await PM_ITEMS.update_origin(db, "a@x", "11111111-1111-1111-1111-111111111111",
                                 {"commitment": True})
    (sql, params), = db.sql("UPDATE pm_tasks")
    assert "coalesce(origin, '{}'::jsonb) || CAST(:patch AS jsonb)" in sql
    assert "organization_id = CAST(:vis_org AS uuid)" in sql
    assert params["patch"] == '{"commitment": true}'


async def test_pm_insights_count_the_effective_disposition(monkeypatch) -> None:
    """Counts, the oldest inbox capture, stale waiting and Areas with no NEXT,
    all off the member's rows and the derived rule."""
    _patch_pm_helpers(monkeypatch)
    at = datetime.now(UTC)
    rows = [
        _full_row(id="i1", p_disposition="INBOX",
                  created_at=at - timedelta(days=3)),
        _full_row(id="i2", p_disposition="INBOX", created_at=at - timedelta(days=9),
                  p_defer_until=at + timedelta(days=1)),   # deferred: not oldest
        _full_row(id="w1", p_disposition="WAITING",
                  p_delegated_at=at - timedelta(days=6)),   # stale: > 5 days
        _full_row(id="w2", p_disposition="WAITING",
                  p_delegated_at=at - timedelta(days=10),
                  p_last_nudged_at=at - timedelta(days=1)),  # still stale: the
        # client's rule reads delegatedAt only, a nudge does not reset it
        _full_row(id="w3", p_disposition="WAITING",
                  p_delegated_at=at - timedelta(days=4),
                  p_expected_by=at - timedelta(hours=1)),    # overdue, not stale
        _full_row(id="n1", p_disposition="NEXT", project_id="area-a"),
        _full_row(id="d1", status_category="done"),          # derived DONE
        _full_row(id="u1", status_category="todo"),          # derived NEXT
    ]
    tree = [
        SimpleNamespace(id="root-1", name="My tasks", description=None,
                        status="active", parent_project_id=None, is_root=True),
        SimpleNamespace(id="area-a", name="A", description=None,
                        status="active", parent_project_id="root-1", is_root=False),
        SimpleNamespace(id="area-b", name="B", description=None,
                        status="active", parent_project_id="root-1", is_root=False),
    ]
    # The tree read is told apart by ITS predicate, not by the table name:
    # `_MY_TASKS_SQL` now embeds the grant closure, which also reads
    # `pm_projects` (WS-39 S6a, the bounded WAITING arm).
    db = _FakeDB(lambda sql, p: tree if "lower(personal_owner) = :who" in sql else rows)
    out = await PM_ITEMS.insight_counts(db, "a@x")
    assert out["counts"] == {"INBOX": 2, "WAITING": 3, "NEXT": 2, "DONE": 1}
    assert out["oldest_inbox_at"] == (at - timedelta(days=3)).isoformat()
    assert out["stale_waiting"] == 2
    assert out["projects_without_next_action"] == 1


async def test_pm_places_are_the_personal_root_and_its_areas(monkeypatch) -> None:
    _patch_pm_helpers(monkeypatch)
    tree = [
        SimpleNamespace(id="root-1", name="My tasks", description=None,
                        status="active", parent_project_id=None, is_root=True),
        SimpleNamespace(id="area-a", name="Proposals", description="q",
                        status="on_hold", parent_project_id="root-1", is_root=False),
    ]
    db = _FakeDB(lambda sql, p: tree)
    projects = await PM_ITEMS.projects_for(db, "a@x")
    assert [(p.id, p.outcome, p.status, p.account_id) for p in projects] == [
        ("root-1", "My tasks", "ACTIVE", None), ("area-a", "Proposals", "ON_HOLD", None),
    ]
    spaces, folders = await PM_ITEMS.local_tree(db, "a@x")
    assert [(s.id, s.name) for s in spaces] == [("area-a", "Proposals")]
    assert folders == []
    places = tasks_ai._collect_places([], spaces, folders)
    assert [p["space_name"] for p in places] == [None, "Proposals"]
    assert all(p["account_id"] is None for p in places)
    assert await PM_ITEMS.synced_candidates(db, "a@x") == []


async def test_pm_link_commitment_writes_task_id(monkeypatch) -> None:
    db = _FakeDB()
    await PM_ITEMS.link_commitment(db, "a@x", "k-1", "t-1")
    (sql, params), = db.sql("UPDATE wa_commitments")
    assert "SET task_id = CAST(:iid AS uuid)" in sql
    assert "a.user_id = :uid" in sql and params["uid"] == "a@x"


# ── The consumers go through the seam ───────────────────────────────────────

async def test_plan_apply_refuses_the_clickup_target_with_410() -> None:
    req = planning.ApplyRequest(
        plan=planning.ProjectPlan(name="P", phases=[planning.PlanPhase(
            name="Ph", tasks=[planning.PlanTask(title="t")])]),
        target="clickup")
    with pytest.raises(HTTPException) as exc:
        await planning.apply_plan(req, user=SimpleNamespace(email="a@x"))
    assert exc.value.status_code == 410


def test_the_clickup_apply_is_gone() -> None:
    assert not hasattr(planning, "_apply_clickup")
    assert not hasattr(planning, "_apply_local")


def test_workload_matches_by_email_for_the_one_store() -> None:
    """The one store assigns by email, so a pm load row carries `em` and the
    roster is matched on it before the name."""
    src = inspect.getsource(tasks_ai._annotate_workload)
    assert "by_email" in src and 'p.get("email")' in src
