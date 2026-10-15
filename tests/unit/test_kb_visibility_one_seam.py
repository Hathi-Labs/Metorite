"""WS-61 KB-1 — every read of a Knowledge page goes through ONE predicate.

Spec: ``project-docs/specs/knowledge_base.md`` §5.1. One function holds the
rule of who sees a page, ``visible_pages_clause(user)`` in
``gateway/routes/knowledge/core.py``. The app, the chat tools, the search
provider, the My Day provider and the ``/mcp`` tools all call it. A second
copy of the predicate is a defect (``navigation_shell.md`` §5.2 rule 7).

What this fence does. It parses each Python module under
``gateway/routes/knowledge/`` and ``gateway/kb*/``. It finds each string
literal that reads ``kb_pages`` (``FROM kb_pages`` or ``JOIN kb_pages``). Each
such literal must be an f-string that puts in the result of a
``visible_pages_clause(...)`` call made in the same function. A plain string,
a module-level constant, or an f-string with no clause in it fails.

The check is per statement, not per function. A function that calls the
clause for one query and reads ``kb_pages`` raw in a second query fails.

Integrity reads. Two reads look at pages the caller may not see, because a
rule of the tree needs them: the children of a page (a move to another space
refuses a page with children) and the ancestors of a page (a move refuses a
cycle). Neither returns a page to the caller. Each is listed in
``_INTEGRITY_READS`` with its reason, and the list fails when it goes stale.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_GATEWAY = _ROOT / "apps" / "services" / "gateway" / "gateway"

#: A statement that reads the pages table. ``kb_page_versions`` and
#: ``kb_page_files`` do not match, because the word ends at ``kb_pages``.
_READS_PAGES = re.compile(r"\b(?:FROM|JOIN)\s+kb_pages\b", re.IGNORECASE)

#: The one predicate.
_CLAUSE = "visible_pages_clause"

#: ``(module file name, function name) -> reason``. A read here returns no
#: page to the caller. Add an entry only with a reason a reviewer can test.
_INTEGRITY_READS: dict[tuple[str, str], str] = {
    ("core.py", "has_children"): (
        "A move to another space refuses a page with children. A child the "
        "caller cannot see still counts. The function returns a bool."
    ),
    ("core.py", "ancestor_ids"): (
        "A move refuses a parent that is the page or one of its descendants. "
        "The walk passes through pages the caller may not see. It returns ids "
        "for a membership test and never a row."
    ),
}


def _knowledge_modules() -> list[Path]:
    roots = [_GATEWAY / "routes" / "knowledge", *sorted(_GATEWAY.glob("kb*"))]
    found: list[Path] = []
    for root in roots:
        if root.is_dir():
            found.extend(sorted(root.rglob("*.py")))
    return found


def _clause_names(func: ast.AST) -> set[str]:
    """The names a function binds from a ``visible_pages_clause(...)`` call."""
    names: set[str] = set()
    for node in ast.walk(func):
        if not isinstance(node, ast.Assign):
            continue
        call = node.value
        if not (isinstance(call, ast.Call) and _called_name(call) == _CLAUSE):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                names.add(target.id)
            elif isinstance(target, ast.Tuple) and target.elts:
                first = target.elts[0]
                if isinstance(first, ast.Name):
                    names.add(first.id)
    return names


def _called_name(call: ast.Call) -> str:
    fn = call.func
    if isinstance(fn, ast.Name):
        return fn.id
    if isinstance(fn, ast.Attribute):
        return fn.attr
    return ""


def _text_of(node: ast.AST) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(
            v.value if isinstance(v, ast.Constant) and isinstance(v.value, str)
            else "{}"
            for v in node.values
        )
    return ""


def _uses_clause(node: ast.AST, names: set[str]) -> bool:
    if not isinstance(node, ast.JoinedStr):
        return False
    for part in node.values:
        if isinstance(part, ast.FormattedValue):
            for inner in ast.walk(part.value):
                if isinstance(inner, ast.Name) and inner.id in names:
                    return True
    return False


def _page_reads(source: str) -> list[tuple[str, int, bool]]:
    """``(enclosing function, line, uses the clause)`` for each page read.

    A read at module level has the function name ``<module>``.
    """
    tree = ast.parse(source)
    owner: dict[int, ast.AST] = {}
    for top in ast.walk(tree):
        if isinstance(top, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for node in ast.walk(top):
                # The outermost function wins, so a nested helper reports
                # under the function that owns the clause binding.
                owner.setdefault(id(node), top)

    inside_fstring: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            for part in node.values:
                inside_fstring.add(id(part))

    reads: list[tuple[str, int, bool]] = []
    for node in ast.walk(tree):
        if id(node) in inside_fstring:
            continue
        if not isinstance(node, (ast.Constant, ast.JoinedStr)):
            continue
        if not _READS_PAGES.search(_text_of(node)):
            continue
        func = owner.get(id(node))
        name = getattr(func, "name", "<module>")
        if name == _CLAUSE:
            # The clause's own body, if it ever names the table.
            reads.append((name, node.lineno, True))
            continue
        names = _clause_names(func) if func is not None else set()
        reads.append((name, node.lineno, _uses_clause(node, names)))
    return reads


def _all_reads() -> dict[tuple[str, str, int], bool]:
    found: dict[tuple[str, str, int], bool] = {}
    for path in _knowledge_modules():
        for func, line, ok in _page_reads(path.read_text(encoding="utf-8")):
            found[(path.name, func, line)] = ok
    return found


# ── The fence ───────────────────────────────────────────────────────────────

def test_the_knowledge_modules_exist_and_read_pages() -> None:
    """Not vacuous: the sweep finds the package and the reads in it."""
    modules = _knowledge_modules()
    assert modules, "no module under gateway/routes/knowledge/ — the sweep is empty"
    reads = _all_reads()
    through_clause = [k for k, ok in reads.items() if ok]
    assert len(through_clause) >= 3, (
        f"only {len(through_clause)} page reads go through {_CLAUSE}. The "
        "routes read pages in at least three places, so the parse is wrong"
    )


def test_every_page_read_goes_through_the_one_clause() -> None:
    bad = [
        f"  {name}:{line} in {func}()"
        for (name, func, line), ok in sorted(_all_reads().items())
        if not ok and (name, func) not in _INTEGRITY_READS
    ]
    assert not bad, (
        "These statements read kb_pages without visible_pages_clause. Each "
        "page read is an f-string that puts in the clause, bound in the same "
        "function (knowledge_base.md §5.1):\n" + "\n".join(bad)
    )


def test_the_integrity_list_is_not_stale() -> None:
    raw = {(name, func) for (name, func, _line), ok in _all_reads().items() if not ok}
    stale = sorted(set(_INTEGRITY_READS) - raw)
    assert not stale, (
        f"{stale} no longer read kb_pages without the clause. Remove them "
        "from _INTEGRITY_READS in the same change."
    )


# ── The fence sees what it claims to see ────────────────────────────────────

def test_the_fence_flags_a_raw_read() -> None:
    src = (
        "async def get_page(db, user, pid):\n"
        "    return await db.execute(text('SELECT * FROM kb_pages WHERE id = :i'))\n"
    )
    assert _page_reads(src) == [("get_page", 2, False)]


def test_the_fence_flags_a_second_raw_read_beside_a_clause() -> None:
    src = (
        "async def get_page(db, user, pid):\n"
        "    vis, params = visible_pages_clause(user, 'p')\n"
        "    a = f'SELECT p.id FROM kb_pages p WHERE {vis}'\n"
        "    b = 'SELECT body_md FROM kb_pages WHERE id = :i'\n"
    )
    assert _page_reads(src) == [("get_page", 3, True), ("get_page", 4, False)]


def test_the_fence_flags_a_module_level_constant() -> None:
    src = "SQL = 'SELECT p.id FROM kb_pages p JOIN kb_spaces s ON s.id = p.space_id'\n"
    assert _page_reads(src) == [("<module>", 1, False)]


def test_the_fence_flags_a_clause_from_another_function() -> None:
    src = (
        "def helper(user):\n"
        "    vis, params = visible_pages_clause(user, 'p')\n"
        "    return vis\n"
        "async def get_page(db, user, vis):\n"
        "    return f'SELECT p.id FROM kb_pages p WHERE {vis}'\n"
    )
    assert _page_reads(src) == [("get_page", 5, False)]


def test_the_fence_ignores_the_versions_table() -> None:
    src = "SQL = 'SELECT version FROM kb_page_versions WHERE page_id = :p'\n"
    assert _page_reads(src) == []
