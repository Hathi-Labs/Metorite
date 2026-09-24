"""No code names a `gtd_` table or a `gtd_` tool any more (WS-39 S8 PR 2, S9).

Spec `my_tasks_cutover.md` §4.3, §5 S8 and §5 S9 · decision D73 · board WS-39.

After migration 217 no `gtd_*` table exists. Three slices renamed eleven of
them (People, Calendar and settings, then the three task-store survivors), and
217 dropped the rest. So a `gtd_` table name in the code is one of two
defects. Either it is a query that fails on the first request, or it is a
comment that describes a schema that is gone.

S9 then renamed the 29 chat tools from `gtd_*` to `my_tasks_*`, and the three
settings helpers to `task_models`, `task_toggles` and `calendar_prefs`. So no
bare `gtd_` token is allowed any more.

This fence walks `apps`, `packages`, `scripts` and `workbench` and reads EVERY
`gtd_` token, in code, comments and docs alike. Two survivors are left, and
each one is listed with its reason:

- :data:`ALLOWED_LITERALS` — the upload folder `data/gtd_attachments` on the
  box. Moving the folder is a separate deploy act, not a code change.
- :data:`ALLOWED_FILES` — the one client file that maps each old tool name to
  its new name, so a chat saved before S9 still renders its cards.

Two more survivors sit outside the four trees, so the walk never reads them.
These are the migrations in `infra/postgres/`, and the three migration-history
tests (`test_gtd_backfill.py`, `test_gtd_rename_upgrade.py` and
`test_gtd_retirement_plan.py`). Those tests are ABOUT the `gtd_*` tables.

:func:`test_the_client_carries_no_gtd_identifier` is the client fence. It
refuses `GtdItem`, any `Gtd<Capital>` identifier and any `gtd_` token under
`workbench/control_plane/src`, outside the alias map.

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

_SKILL_INIT = (REPO / "apps" / "skills" / "skill-my-tasks" / "skill_my_tasks"
               / "__init__.py")

#: The client file that may spell the old tool names. It maps each stored
#: `gtd_*` name to its `my_tasks_*` name, so a chat saved before S9 still
#: renders its task cards. `TaskToolCards.test.ts` pins the map to the skill.
LEGACY_ALIAS_FILE = (REPO / "workbench" / "control_plane" / "src" / "components"
                     / "tasks" / "TaskToolCards.tsx")

#: Files the token scan skips, each with its reason.
ALLOWED_FILES: dict[Path, str] = {
    LEGACY_ALIAS_FILE: (
        "the legacy tool-name alias map. Stored chat history carries the old "
        "`gtd_*` names, and each one must still render its card"),
}

#: Every bare `gtd_` token the four trees may still carry. S9 emptied it.
ALLOWED: dict[str, str] = {}

#: Literal strings removed before the scan, each with its reason.
ALLOWED_LITERALS: dict[str, str] = {
    "data/gtd_attachments": (
        "the upload DIRECTORY on the box, not a table. Each attachments row "
        "stores its own path, so moving the files is a disk act, not schema"),
}

#: Not tables, and gone with them. Named because no CREATE TABLE finds them.
NON_TABLE_NAMES = frozenset({
    "gtd_item_id",          # the wa_commitments column 217 dropped
    "gtd_backfill_plan",    # the S3b view
    "gtd_backfill_to_pm",   # the S3b function
    "gtd_retirement_drop",  # the S3c guard
})

_TOKEN = re.compile(r"\bgtd_[A-Za-z0-9_]+")
_CREATE = re.compile(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(gtd_\w+)", re.I)
_OLD_NAME = re.compile(r"old_name\s+CONSTANT\s+text\s*:=\s*'(gtd_\w+)'", re.I)
_SQL_USE = re.compile(
    r"\b(?:FROM|JOIN|INTO|UPDATE|TABLE)\s+(gtd_\w+)", re.I)

_CLIENT = REPO / "workbench" / "control_plane" / "src"
_CLIENT_NAME = re.compile(r"GtdItem|Gtd[A-Z]|gtd_")


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


def scanned_files() -> list[Path]:
    """The fenced files, less the files :data:`ALLOWED_FILES` names."""
    return [p for p in fenced_files() if p not in ALLOWED_FILES]


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


def _skill_tools() -> frozenset[str]:
    tree = ast.parse(_SKILL_INIT.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                getattr(t, "id", None) == "__all__" for t in node.targets):
            return frozenset(ast.literal_eval(node.value))
    raise AssertionError("skill_my_tasks.__all__ not found")


# ── The fence ───────────────────────────────────────────────────────────────

def test_the_walk_reaches_all_four_trees() -> None:
    files = fenced_files()
    assert len(files) > 500, len(files)
    for top in TREES:
        assert any(p.relative_to(REPO).parts[0] == top for p in files), top


def test_no_gtd_token_outside_the_allowed_list() -> None:
    bad = [
        f"{p.relative_to(REPO).as_posix()}:{line}: {token}"
        for p in scanned_files() for line, token in _scan_text(_read(p))
    ]
    assert not bad, (
        "These `gtd_` tokens are not allowed. After migration 217 no gtd_ "
        "table exists, and after S9 no gtd_ tool exists. Use the new name "
        "(my_tasks_cutover.md §4.3 and §5 S9):\n  " + "\n  ".join(bad)
    )


def test_no_code_uses_a_gtd_name_as_a_table() -> None:
    """A second net under the token scan. It reads the files the token scan
    skips as well, so not even the alias map may use an old table name in
    SQL."""
    retired = table_names() | NON_TABLE_NAMES
    bad = [
        f"{p.relative_to(REPO).as_posix()}: {' '.join(m.group(0).split())}"
        for p in fenced_files() for m in _SQL_USE.finditer(_read(p))
        if m.group(1).lower() in retired
    ]
    assert not bad, "SQL that names a gtd_ table:\n  " + "\n  ".join(bad)


def test_the_client_carries_no_gtd_identifier() -> None:
    """`rg -l "GtdItem|Gtd[A-Z]|gtd_" workbench/control_plane/src` returns
    only the alias map (my_tasks_cutover.md §5 S9)."""
    hits = sorted(
        p.relative_to(REPO).as_posix() for p in fenced_files()
        if _CLIENT in p.parents and _CLIENT_NAME.search(_read(p)))
    assert hits == [LEGACY_ALIAS_FILE.relative_to(REPO).as_posix()], hits


# ── The lists are honest ────────────────────────────────────────────────────

def test_the_table_list_is_discovered_not_empty() -> None:
    names = table_names()
    for expected in ("gtd_items", "gtd_waiting", "gtd_projects", "gtd_contexts",
                     "gtd_attachments", "gtd_horizons", "gtd_reviews",
                     "gtd_people", "gtd_settings", "gtd_retirement_arm"):
        assert expected in names, expected


def test_the_skill_tools_are_all_renamed() -> None:
    """S9 moved the 29 tools all at once (H-151). None keeps the old prefix."""
    tools = _skill_tools()
    assert len(tools) == 29, sorted(tools)
    assert all(name.startswith("my_tasks_") for name in tools), sorted(tools)


def test_the_alias_map_names_only_old_tool_names() -> None:
    """The alias file may spell `gtd_<tool>` only for the 29 old tool names,
    so it cannot become a place where a table name hides."""
    old_names = {"gtd_" + n.removeprefix("my_tasks_") for n in _skill_tools()}
    used = set(_TOKEN.findall(_read(LEGACY_ALIAS_FILE)))
    assert used == old_names, sorted(used ^ old_names)


def test_every_exemption_is_still_used() -> None:
    """An exemption nothing uses is stale."""
    for literal in ALLOWED_LITERALS:
        assert any(literal in _read(p) for p in fenced_files()), literal
    for path in ALLOWED_FILES:
        assert path.exists(), path
    assert not ALLOWED, f"S9 emptied ALLOWED, and it holds {sorted(ALLOWED)}"


def test_the_fence_can_see() -> None:
    """A fence that matches nothing is a fence with a hole."""
    text = (
        'SQL = "SELECT * FROM gtd_items"\n'
        "# a comment about gtd_reviews\n"
        'TOOL = "gtd_capture"\n'
        'DIR = "data/gtd_attachments"\n'
    )
    assert _scan_text(text) == [
        (1, "gtd_items"), (2, "gtd_reviews"), (3, "gtd_capture")]
    assert _SQL_USE.search("JOIN gtd_people p ON")
    assert _CLIENT_NAME.search("const x: GtdItem = y")
    assert _CLIENT_NAME.search("GtdProject")
    assert not _CLIENT_NAME.search("MyTask")
