"""PZ-33a: a chat summary is private to its member (WS-63, D142, D-PZ-24).

Spec: ``project-docs/specs/personalization.md`` §15.3 ("No reader gets it by
accident"), §15.9 item 11 and §15.10 (Fences). No database. The fences read
the source as text:

(a) No module other than ``gateway/assistant_summaries.py`` names the column
    ``member_summary``. The migration that adds it, and the staging blank of
    PZ-35 (``infra/staging/minimise.sql``), are the only other files that may.
    So no list, room, search or staff SQL can select it by name.
(b) No SQL text that reads ``chat_session`` reads the whole row: no
    ``SELECT *``, no ``RETURNING *``, no ``s.*`` and no row as JSON. So no
    reader gets the column by accident (R6 of the slice).
(c) The chat list, a shared room, ``history_tools`` and each staff source
    import nothing of ``assistant_summaries``.
(d) The module imports no litellm and calls no ``acompletion_stream_text``:
    those paths write no usage row (H-287), and PZ-33 adds no fourth.

Each fence also refuses a planted file (``test_the_fences_can_fail``), so a
scan that reads nothing cannot pass.

Mutations, each red here: name ``member_summary`` in ``routes/chat.py``;
write ``SELECT s.* FROM chat_session s`` in any module; import
``gateway.assistant_summaries`` from ``routes/rooms.py``; add ``import
litellm`` to the module.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GATEWAY = ROOT / "apps" / "services" / "gateway" / "gateway"
MODULE = GATEWAY / "assistant_summaries.py"
COLUMN = "member_summary"
IMPORT_NAME = "assistant_summaries"

#: The trees of code that the name fence reads.
CODE_ROOTS = (
    ROOT / "apps", ROOT / "packages", ROOT / "scripts", ROOT / "deploy",
    ROOT / "infra", ROOT / "workbench" / "control_plane" / "src",
    ROOT / "workbench" / "operator_console" / "src",
)
CODE_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".mjs", ".sql", ".sh"}
SKIP_PARTS = {"node_modules", ".venv", "__pycache__", ".next", "tests"}

#: The migration that adds the column, found by its name and not by its
#: number, because R1 can change the number at merge.
MIGRATION = re.compile(r"^\d+_chat_session_member_summary\.sql$")
#: The staging blank of PZ-35 (§15.5). It may name the column when it lands.
STAGING_BLANK = ROOT / "infra" / "staging" / "minimise.sql"

#: The sources that must import nothing of the module (c). The member's own
#: routes of PZ-35 (`routes/admin/assistant_profile.py`) are not staff
#: routes, so they are not here.
NO_IMPORT = (
    GATEWAY / "routes" / "chat.py",
    GATEWAY / "routes" / "rooms.py",
    GATEWAY / "rooms.py",
    GATEWAY / "room_stream.py",
    ROOT / "packages" / "acb_skills" / "acb_skills" / "history_tools.py",
    GATEWAY / "routes" / "operator.py",
    GATEWAY / "routes" / "crm",
    GATEWAY / "routes" / "people",
    GATEWAY / "routes" / "tasks" / "people.py",
    ROOT / "apps" / "services" / "customer_console",
)
#: The staff tree, minus the member's own routes.
ADMIN = GATEWAY / "routes" / "admin"
OWN_ROUTES = {"assistant_profile.py", "assistant_profile_people.py"}

#: A SQL text that reads `chat_session` as a table.
_TABLE_REF = re.compile(r"(?i)\b(?:from|join|update|into)\s+(?:public\.)?chat_session\b(?!_)")
#: The alias of `chat_session` in a SQL text, as in `chat_session s`.
_ALIAS = re.compile(r"(?i)\bchat_session\s+(?:as\s+)?([a-z_][a-z0-9_]*)\b")
_SQL_WORDS = {"where", "set", "join", "on", "left", "inner", "order", "group",
              "limit", "returning", "values", "using", "cross", "full", "right"}


def _rel(path: Path) -> str:
    """*path* from the repository root, or its name for a file outside it."""
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return path.name


def _code_files() -> list[Path]:
    out: list[Path] = []
    for root in CODE_ROOTS:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.suffix in CODE_SUFFIXES and path.is_file() \
                    and not SKIP_PARTS & set(path.relative_to(ROOT).parts):
                out.append(path)
    return sorted(out)


def _may_name_the_column(path: Path) -> bool:
    if path in (MODULE, STAGING_BLANK):
        return True
    return path.parent == ROOT / "infra" / "postgres" and MIGRATION.match(path.name) is not None


def _names_the_column(paths: list[Path]) -> list[str]:
    return [_rel(p) for p in paths
            if not _may_name_the_column(p)
            and COLUMN in p.read_text(encoding="utf-8-sig", errors="replace")]


def test_the_name_scan_reads_the_trees_it_names() -> None:
    files = _code_files()
    assert MODULE in files
    assert GATEWAY / "routes" / "chat.py" in files
    assert ROOT / "packages" / "acb_skills" / "acb_skills" / "history_tools.py" in files
    assert any(MIGRATION.match(p.name) for p in files), "the migration must be in the scan"
    assert any(p.suffix == ".tsx" for p in files)
    assert len(files) > 1000, len(files)


def test_a_the_module_names_the_column_and_no_other_code_does() -> None:
    """(a) Item 11 of the PZ-33 list."""
    assert COLUMN in MODULE.read_text(encoding="utf-8")
    found = _names_the_column(_code_files())
    assert not found, f"only assistant_summaries.py may name {COLUMN}: {found}"


# ── (b) No reader takes the whole row ────────────────────────────────────────


def _strings(path: Path) -> list[tuple[int, str]]:
    """Each string of *path*, with its line. Adjacent strings are one string,
    and an f-string keeps its literal parts."""
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            out.append((node.lineno, node.value))
        elif isinstance(node, ast.JoinedStr):
            out.append((node.lineno, "".join(
                v.value if isinstance(v, ast.Constant) else "{}" for v in node.values)))
    return out


def _whole_row_reads(sql: str) -> list[str]:
    """The ways in which *sql* reads a whole `chat_session` row."""
    found: list[str] = []
    if re.search(r"(?i)\b(?:select|returning)\s+\*", sql):
        found.append("a star")
    aliases = {"chat_session"} | {
        a.lower() for a in _ALIAS.findall(sql) if a.lower() not in _SQL_WORDS}
    for alias in sorted(aliases):
        if re.search(rf"(?i)(?<![\w.]){re.escape(alias)}\.\*", sql):
            found.append(f"{alias}.*")
        if re.search(rf"(?i)\b(?:row_to_json|to_jsonb?|jsonb?_agg|hstore)\s*\(\s*"
                     rf"{re.escape(alias)}\s*\)", sql):
            found.append(f"the row {alias} as JSON")
    return found


def _sql_reads(paths: list[Path]) -> tuple[int, list[str]]:
    """The count of SQL texts that read `chat_session`, and each whole-row read."""
    seen, bad = 0, []
    for path in paths:
        for line, value in _strings(path):
            if not _TABLE_REF.search(value):
                continue
            seen += 1
            bad += [f"{_rel(path)}:{line}: {why}" for why in _whole_row_reads(value)]
    return seen, bad


def _python_files() -> list[Path]:
    return [p for p in _code_files() if p.suffix == ".py"]


def test_b_no_sql_text_reads_the_whole_chat_session_row() -> None:
    seen, bad = _sql_reads(_python_files())
    # routes/chat.py alone holds more than ten, so a scan that finds few is
    # a scan that reads the wrong files.
    assert seen >= 25, seen
    assert not bad, bad


def test_b_no_sql_file_reads_the_whole_chat_session_row() -> None:
    bad: list[str] = []
    for path in (p for p in _code_files() if p.suffix == ".sql"):
        body = path.read_text(encoding="utf-8-sig", errors="replace")
        for statement in body.split(";"):
            if _TABLE_REF.search(statement) and not statement.lstrip().upper().startswith(
                    ("CREATE TABLE", "ALTER TABLE", "COMMENT ON")):
                bad += [f"{_rel(path)}: {why}" for why in _whole_row_reads(statement)]
    assert not bad, bad


# ── (c) The list, the rooms, the history search and staff import nothing ────


def _no_import_files() -> list[Path]:
    out: list[Path] = []
    for root in NO_IMPORT:
        out += [root] if root.is_file() else sorted(root.rglob("*.py"))
    out += [p for p in sorted(ADMIN.rglob("*.py")) if p.name not in OWN_ROUTES]
    return out


def _imports_the_module(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    where = _rel(path)
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out += [f"{where}: import {a.name}" for a in node.names
                    if IMPORT_NAME in a.name]
        elif isinstance(node, ast.ImportFrom):
            names = [a.name for a in node.names]
            if IMPORT_NAME in (node.module or "") or IMPORT_NAME in names:
                out.append(f"{where}: from {node.module or '.'} import {names}")
    return out


def test_c_the_scan_of_importers_reaches_the_files_it_names() -> None:
    files = _no_import_files()
    for must in (GATEWAY / "routes" / "chat.py", GATEWAY / "routes" / "rooms.py",
                 ADMIN / "members.py", GATEWAY / "routes" / "operator.py",
                 ROOT / "packages" / "acb_skills" / "acb_skills" / "history_tools.py"):
        assert must in files, must
    assert all(p.name not in OWN_ROUTES for p in files)


def test_c_no_list_room_history_or_staff_source_imports_the_module() -> None:
    """(c) MEM-2 (§15.7) may give ``history_tools`` the member's own
    summaries, through the solo rule. That slice changes this fence, in
    review. PZ-35 adds the removal's clear to ``members.py`` the same way."""
    problems: list[str] = []
    for path in _no_import_files():
        problems += _imports_the_module(path)
    assert not problems, problems


# ── (d) No litellm and no stream ────────────────────────────────────────────


def _model_paths(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out += [f"import {a.name}" for a in node.names
                    if a.name.split(".")[0] == "litellm"]
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] == "litellm":
                out.append(f"from {node.module} import ...")
            out += [f"from {node.module} import {a.name}" for a in node.names
                    if a.name == "acompletion_stream_text"]
        elif isinstance(node, ast.Name) and node.id in {"litellm", "acompletion_stream_text"}:
            out.append(f"name {node.id}")
        elif isinstance(node, ast.Attribute) and node.attr == "acompletion_stream_text":
            out.append("attribute acompletion_stream_text")
    return out


def test_d_the_module_imports_no_litellm_and_no_stream() -> None:
    assert _model_paths(MODULE) == []


# ── The fences can fail ─────────────────────────────────────────────────────


def test_the_fences_can_fail(tmp_path: Path) -> None:
    planted = tmp_path / "planted.py"
    planted.write_text(
        "import litellm\n"
        "from acb_llm.context import acompletion_stream_text\n"
        "from gateway import assistant_summaries\n"
        "SQL = 'SELECT s.id, s.member_summary FROM chat_session s'\n"
        "ROW = 'SELECT s.* FROM chat_session s WHERE s.id = :sid'\n"
        "JSON = 'SELECT row_to_json(cs) FROM chat_session cs'\n"
        "STAR = 'UPDATE chat_session SET title = :t WHERE id = :sid RETURNING *'\n",
        encoding="utf-8")
    assert COLUMN in planted.read_text(encoding="utf-8")
    assert not _may_name_the_column(planted)
    assert _names_the_column([planted]) == ["planted.py"]
    _seen, bad = _sql_reads([planted])
    assert any("s.*" in b for b in bad), bad
    assert any("the row cs as JSON" in b for b in bad), bad
    assert any("a star" in b for b in bad), bad
    assert _imports_the_module(planted)
    assert len(_model_paths(planted)) >= 2
    # The plain reads of today pass: a named column list is no whole-row read.
    assert _whole_row_reads("SELECT s.id, s.title FROM chat_session s WHERE x") == []
    assert _whole_row_reads(
        "SELECT 1 FROM chat_session_participant p WHERE p.session_id = :sid") == []
