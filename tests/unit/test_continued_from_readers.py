"""WT-1: only three places may use ``chat_session.continued_from`` (§9.3 item 8).

Spec: ``project-docs/specs/one_assistant_context.md`` §9.3 item 8 and §9.5.

The column links a fresh chat to the held chat that it continues. The held
chat read outside mail. So a reader that follows the link into the old chat
would carry that mail into the fresh chat, and the fresh chat would hold no
mark for it. Three readers may use the column: the WT-1 route, the session
list and the chip of CO-5. A context builder, the summary job and memory
assembly never may.

The scan is structural. It parses each Python file of ``apps/``,
``packages/`` and ``scripts/`` and finds each string that names
``continued_from``. A comment and a docstring read no column, so the scan
skips them. Each hit must sit in a place that :data:`ALLOWED` names. Each
TypeScript file of the web shell is scanned too, and none may name it.

The route writes it, and the session list (``_get_sessions``) reads it since
CO-13 (task 3512). The chip of CO-5 may read it, and the allow list names it
so that its slice needs no change here. CO-5 has no code yet, so its entry is
a name and no file. A slice that adds the CO-5 read adds its file and its
function here.

The web shell never names the column. It reads the ``continuedFrom`` key of
the session list, and only the files of :data:`CAMEL_OK` may name that key.
What these scans pin: no Python file names the column outside ALLOWED, no
Python file outside ``routes/chat.py`` names the ``continuedFrom`` key, and
the dock names that key in one place. They do not prove that no context
builder reads the old chat by another path. A new use in the dock needs a
review. The list gives the title of a source that the member can read, and
only to the member's own browser.

Mutation: add ``continued_from`` to a string in another function -> red.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
COLUMN = "continued_from"

#: (file, the name of the enclosing function or module constant) -> why.
ALLOWED: dict[tuple[str, str], str] = {
    # The WT-1 route: the link that it writes on the new chat.
    ("apps/services/gateway/gateway/routes/chat.py", "_LINK_SQL"):
        "the WT-1 route writes the link (§9.3 item 8)",
    # The session list reads the link and gives `continuedFrom` (CO-13).
    ("apps/services/gateway/gateway/routes/chat.py", "_get_sessions"):
        "the session list (§9.3 item 8, CO-13)",
}

#: The only TypeScript files that name the `continuedFrom` key of the list.
CAMEL_OK: frozenset[str] = frozenset({
    "workbench/control_plane/src/lib/sessions.ts",
    "workbench/control_plane/src/lib/shell/ContinuedMark.tsx",
    "workbench/control_plane/src/lib/shell/ContinuedMark.test.tsx",
    "workbench/control_plane/src/lib/shell/AssistantDock.tsx",
})

#: The chip of CO-5 (`chat_organization.md` §7.1). It has no code yet. Its
#: slice adds a row to ALLOWED with its file and its function.
CO5_CHIP_PENDING = True

#: Files where `continued_from` is another thing with the same name. Each one
#: is a key of the `progress` JSONB of a Projects import run, and no file
#: here names `chat_session`.
OTHER_MEANING: dict[str, str] = {
    "apps/services/gateway/gateway/routes/projects/import_writer.py":
        "the `progress.continued_from` key of an import run",
    "apps/services/gateway/gateway/routes/projects/import_discard.py":
        "the `progress.continued_from` key of an import run",
}

_PY_ROOTS = ("apps", "packages", "scripts")
_TS_ROOT = "workbench/control_plane/src"
_SKIP_PARTS = {"node_modules", ".venv", "__pycache__", "tests", ".next"}


def _docstring_ids(tree: ast.AST) -> set[int]:
    """The id of each docstring and bare string statement."""
    out: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            out.add(id(node.value))
    return out


def _hits(path: Path) -> list[str]:
    """The name of the place of each string in *path* that names the column."""
    source = path.read_text(encoding="utf-8")
    if COLUMN not in source:
        return []
    tree = ast.parse(source, filename=str(path))
    skip = _docstring_ids(tree)
    found: list[str] = []

    def walk(node: ast.AST, place: str) -> None:
        for child in ast.iter_child_nodes(node):
            here = place
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                here = child.name if place == "<module>" else place
            elif place == "<module>" and isinstance(child, (ast.Assign, ast.AnnAssign)):
                targets = child.targets if isinstance(child, ast.Assign) else [child.target]
                names = [t.id for t in targets if isinstance(t, ast.Name)]
                here = names[0] if names else place
            if (
                isinstance(child, ast.Constant) and isinstance(child.value, str)
                and COLUMN in child.value and id(child) not in skip
            ):
                found.append(here)
            walk(child, here)

    walk(tree, "<module>")
    return found


def _python_files() -> list[Path]:
    out: list[Path] = []
    for root in _PY_ROOTS:
        for path in (REPO / root).rglob("*.py"):
            if _SKIP_PARTS.intersection(path.relative_to(REPO).parts):
                continue
            out.append(path)
    return out


def test_only_the_allowed_places_name_the_column() -> None:
    bad: list[str] = []
    for path in _python_files():
        rel = path.relative_to(REPO).as_posix()
        if rel in OTHER_MEANING:
            continue
        for place in _hits(path):
            if (rel, place) not in ALLOWED:
                bad.append(f"{rel}::{place}")
    assert not bad, (
        "chat_session.continued_from has three readers only: the WT-1 route, "
        "the session list and the CO-5 chip (one_assistant_context.md §9.3 "
        f"item 8). These places name it too: {sorted(set(bad))}"
    )


def test_the_route_still_writes_the_link() -> None:
    """The scan is not empty by accident: the one writer is where it says."""
    route = REPO / "apps/services/gateway/gateway/routes/chat.py"
    assert "_LINK_SQL" in _hits(route)


def test_each_file_of_another_meaning_still_has_it_and_names_no_chat() -> None:
    """A stale row of OTHER_MEANING would hide a new reader in that file."""
    for rel in OTHER_MEANING:
        source = (REPO / rel).read_text(encoding="utf-8")
        assert COLUMN in source, f"{rel} no longer names it: remove its row"
        assert "chat_session" not in source, f"{rel} names chat_session: scan it"


def _ts_files() -> list[Path]:
    root = REPO / _TS_ROOT
    return [
        path for path in list(root.rglob("*.ts")) + list(root.rglob("*.tsx"))
        if not _SKIP_PARTS.intersection(path.relative_to(REPO).parts)
    ]


def test_the_web_shell_never_names_the_column() -> None:
    pattern = re.compile(r"\bcontinued_from\b")
    bad = [
        p.relative_to(REPO).as_posix() for p in _ts_files()
        if pattern.search(p.read_text(encoding="utf-8"))
    ]
    assert not bad, f"the web shell reads continued_from: {bad}"


def test_only_the_listed_web_files_name_the_list_key() -> None:
    """CO-13: the camel key of the list is read by the merge and the header."""
    pattern = re.compile(r"\bcontinuedFrom\b")
    bad = [
        rel for p in _ts_files()
        if pattern.search(p.read_text(encoding="utf-8"))
        and (rel := p.relative_to(REPO).as_posix()) not in CAMEL_OK
    ]
    assert not bad, f"these web files name continuedFrom, add them to CAMEL_OK: {bad}"


def test_the_dock_uses_continued_from_once() -> None:
    dock = REPO / "workbench/control_plane/src/lib/shell/AssistantDock.tsx"
    count = len(re.findall(r"continuedFrom", dock.read_text(encoding="utf-8")))
    assert count == 1, (
        f"the dock names continuedFrom {count} times, and 1 was reviewed. "
        "A new use of continuedFrom in the dock must be reviewed: it must "
        "never load the old chat into a run context."
    )


def test_no_python_file_outside_the_list_names_the_list_key() -> None:
    """The ``continuedFrom`` key is made in ``routes/chat.py`` and read nowhere in Python."""
    own = "apps/services/gateway/gateway/routes/chat.py"
    bad = [
        rel for path in _python_files()
        if (rel := path.relative_to(REPO).as_posix()) != own
        and "continuedFrom" in path.read_text(encoding="utf-8")
    ]
    assert not bad, f"these Python files name the continuedFrom key: {bad}"


def test_the_co5_chip_is_still_pending_or_listed() -> None:
    """When CO-5 lands, its slice lists its read and sets the pending mark off."""
    chips = [k for k in ALLOWED if "chip" in ALLOWED[k].lower()]
    assert CO5_CHIP_PENDING or chips
