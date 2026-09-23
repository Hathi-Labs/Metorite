"""No code names a `gtd_` TABLE any more (WS-39 S8 PR 2, H-151).

Spec `my_tasks_cutover.md` §4.3 and §5 S8 · decision D73 · board WS-39.

After migration 216 no `gtd_*` table exists. Three slices renamed eleven of
them (People, Calendar and settings, then the three task-store survivors), and
216 dropped the rest. So a `gtd_` table name in the code is one of two
defects. Either it is a query that fails on the first request, or it is a
comment that describes a schema that is gone.

This fence walks `apps`, `packages`, `scripts` and `workbench` and reads EVERY
`gtd_` token, in code, comments and docs alike. A token passes only when
:data:`ALLOWED` names it with a reason. The survivors are S9's (the chat tool
names and three helper names), and each one is listed.

The table names are DISCOVERED from the ladder, never transcribed: every
`CREATE TABLE gtd_*` in `infra/postgres/`, and every OLD name a rename
prologue spells. So a table this suite never heard of is still covered.

Out of scope, on purpose: `infra/postgres/` (the migrations that created,
renamed and dropped these tables must keep saying so), `tests/` (fences name
what they forbid) and `project-docs/` (history).
"""
from __future__ import annotations

import ast
import os
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MIGRATIONS = REPO / "infra" / "postgres"
TREES = ("apps", "packages", "scripts", "workbench")

#: Directories that are built, installed or cached, never authored.
PRUNE = frozenset({
    "node_modules", ".next", "__pycache__", ".turbo", "dist", "build", ".venv",
    "coverage", "playwright-report", "test-results", ".git",
})
TEXT_SUFFIXES = frozenset({
    ".py", ".ts", ".tsx", ".js", ".mjs", ".cjs", ".sh", ".sql", ".md", ".yml",
    ".yaml", ".toml", ".json", ".txt", ".html", ".css",
})
#: Generated or vendored files that are large and carry no table names.
SKIP_FILES = frozenset({"package-lock.json"})

_SKILL_INIT = (REPO / "apps" / "skills" / "skill-task-gtd" / "skill_task_gtd"
               / "__init__.py")

#: The 29 chat tool names. S9 renames all of them at once (my_tasks_cutover.md
#: §5 S9). Listed here, and pinned to the skill's `__all__` below, so the list
#: cannot drift from the tools that exist.
TOOL_NAMES = (
    "gtd_accounts", "gtd_add_subtasks", "gtd_archive", "gtd_capture",
    "gtd_capture_many", "gtd_clarify", "gtd_complete", "gtd_day_digest",
    "gtd_delegate", "gtd_detail", "gtd_estimate_stats", "gtd_inbox_insights",
    "gtd_list", "gtd_list_projects", "gtd_list_schedule", "gtd_move",
    "gtd_organize", "gtd_people", "gtd_plan_day", "gtd_plan_project",
    "gtd_replan_day", "gtd_rollover", "gtd_schedule", "gtd_set_one_thing",
    "gtd_set_stage", "gtd_subtasks", "gtd_sync", "gtd_unschedule",
    "gtd_update",
)

#: Every `gtd_` token the four trees may still carry, each with its reason.
ALLOWED: dict[str, str] = {
    **{name: "a chat tool name. S9 renames all 29 at once" for name in TOOL_NAMES},
    "gtd_models": "a helper in routes/tasks/settings.py. S9 renames it",
    "gtd_toggles": "a helper in routes/tasks/settings.py. S9 renames it",
    "gtd_calendar_prefs": "a helper in routes/tasks/settings.py. S9 renames it",
    "gtd_add_task": (
        "an example tool name in the observability office's tool list. It "
        "names no table and no live tool"),
}

#: Literal strings removed before the scan, each with its reason.
ALLOWED_LITERALS: dict[str, str] = {
    "data/gtd_attachments": (
        "the upload DIRECTORY on the box, not a table. Each attachments row "
        "stores its own path, so moving the files is a disk act, not schema"),
}

#: Not tables, and gone with them. Named because no CREATE TABLE finds them.
NON_TABLE_NAMES = frozenset({
    "gtd_item_id",          # the wa_commitments column 216 dropped
    "gtd_backfill_plan",    # the S3b view
    "gtd_backfill_to_pm",   # the S3b function
    "gtd_retirement_drop",  # the S3c guard
})

_TOKEN = re.compile(r"\bgtd_[A-Za-z0-9_]+")
_CREATE = re.compile(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(gtd_\w+)", re.I)
_OLD_NAME = re.compile(r"old_name\s+CONSTANT\s+text\s*:=\s*'(gtd_\w+)'", re.I)
_SQL_USE = re.compile(
    r"\b(?:FROM|JOIN|INTO|UPDATE|TABLE)\s+(gtd_\w+)", re.I)


def table_names() -> frozenset[str]:
    """Every `gtd_` table the ladder ever created, under its old name."""
    names: set[str] = set()
    for path in MIGRATIONS.glob("[0-9]*_*.sql"):
        text = path.read_text(encoding="utf-8")
        names |= {m.lower() for m in _CREATE.findall(text)}
        names |= {m.lower() for m in _OLD_NAME.findall(text)}
    return frozenset(names)


def fenced_files() -> list[Path]:
    out: list[Path] = []
    for top in TREES:
        for dirpath, dirnames, filenames in os.walk(REPO / top):
            dirnames[:] = [d for d in dirnames if d not in PRUNE]
            for name in filenames:
                path = Path(dirpath) / name
                if path.suffix in TEXT_SUFFIXES and name not in SKIP_FILES:
                    out.append(path)
    return sorted(out)


def _scan_text(text: str) -> list[tuple[int, str]]:
    for literal in ALLOWED_LITERALS:
        text = text.replace(literal, "")
    found: list[tuple[int, str]] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        for token in _TOKEN.findall(line):
            if token not in ALLOWED:
                found.append((lineno, token))
    return found


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


# ── The fence ───────────────────────────────────────────────────────────────

def test_the_walk_reaches_all_four_trees() -> None:
    files = fenced_files()
    assert len(files) > 500, len(files)
    for top in TREES:
        assert any(p.relative_to(REPO).parts[0] == top for p in files), top


def test_no_gtd_token_outside_the_allowed_list() -> None:
    bad = [
        f"{p.relative_to(REPO).as_posix()}:{line}: {token}"
        for p in fenced_files() for line, token in _scan_text(_read(p))
    ]
    assert not bad, (
        "These `gtd_` tokens are not in ALLOWED. After migration 216 no gtd_ "
        "table exists. Use the new name (my_tasks_cutover.md §4.3), or, for a "
        "survivor, add it to ALLOWED with its reason:\n  " + "\n  ".join(bad)
    )


def test_no_code_uses_a_gtd_name_as_a_table() -> None:
    """`gtd_people` is a tool name AND the People table's old name. The token
    fence must allow the tool, so this catches the table use it would miss."""
    retired = table_names() | NON_TABLE_NAMES
    bad = [
        f"{p.relative_to(REPO).as_posix()}: {' '.join(m.group(0).split())}"
        for p in fenced_files() for m in _SQL_USE.finditer(_read(p))
        if m.group(1).lower() in retired
    ]
    assert not bad, "SQL that names a gtd_ table:\n  " + "\n  ".join(bad)


# ── The list is honest ──────────────────────────────────────────────────────

def test_the_table_list_is_discovered_not_empty() -> None:
    names = table_names()
    for expected in ("gtd_items", "gtd_waiting", "gtd_projects", "gtd_contexts",
                     "gtd_attachments", "gtd_horizons", "gtd_reviews",
                     "gtd_people", "gtd_settings", "gtd_retirement_arm"):
        assert expected in names, expected


def test_the_only_allowed_table_name_is_the_people_tool() -> None:
    clash = sorted(set(ALLOWED) & (table_names() | NON_TABLE_NAMES))
    assert clash == ["gtd_people"], (
        f"ALLOWED lets these table names through: {clash}. Only the chat tool "
        "`gtd_people` may share a retired table's name, and "
        "test_no_code_uses_a_gtd_name_as_a_table covers its SQL use."
    )


def test_the_tool_names_are_the_skills_tools() -> None:
    tree = ast.parse(_SKILL_INIT.read_text(encoding="utf-8"))
    exported: frozenset[str] = frozenset()
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                getattr(t, "id", None) == "__all__" for t in node.targets):
            exported = frozenset(ast.literal_eval(node.value))
    assert exported == frozenset(TOOL_NAMES)


def test_every_allowed_token_is_still_used() -> None:
    """An exemption nothing uses is stale. S9 deletes these as it renames."""
    used: set[str] = set()
    for path in fenced_files():
        used |= set(_TOKEN.findall(_read(path)))
    stale = sorted(set(ALLOWED) - used)
    assert not stale, f"ALLOWED names tokens nothing uses: {stale}"
    for literal in ALLOWED_LITERALS:
        assert any(literal in _read(p) for p in fenced_files()), literal


def test_the_fence_can_see() -> None:
    """A fence that matches nothing is a fence with a hole."""
    text = (
        'SQL = "SELECT * FROM gtd_items"\n'
        "# a comment about gtd_reviews\n"
        'TOOL = "gtd_capture"\n'
        'DIR = "data/gtd_attachments"\n'
    )
    assert _scan_text(text) == [(1, "gtd_items"), (2, "gtd_reviews")]
    assert _SQL_USE.search("JOIN gtd_people p ON")
