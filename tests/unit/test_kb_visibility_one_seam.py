"""WS-61 KB-1 — every read of a Knowledge page goes through ONE predicate.

Spec: ``project-docs/specs/knowledge_base.md`` §5.1. One function holds the
rule of who sees a page, ``visible_pages_clause(user)`` in
``gateway/routes/knowledge/core.py``. The app, the chat tools, the search
provider, the My Day provider and the ``/mcp`` tools all call it. A second
copy of the predicate is a defect (``navigation_shell.md`` §5.2 rule 7).

What this fence does. It parses each Python module under
``gateway/routes/knowledge/`` and ``gateway/kb*/``, and so ``gateway/kb_index/``
too. It finds each string literal that reads ``kb_pages`` (``FROM kb_pages``
or ``JOIN kb_pages``) or the index ``kb_chunks`` (KB-3, §6.3). Each such
literal must be an f-string that puts in the result of a
``visible_pages_clause(...)`` call made in the same function. A plain string,
a module-level constant, or an f-string with no clause in it fails. A chunk
read can only meet the clause by a join to ``kb_pages``, so a search never
answers a chunk of a page the member cannot see. ``DELETE FROM kb_chunks`` is
a write of the indexer, and it is not a read.

The check is per statement, not per function. A function that calls the
clause for one query and reads ``kb_pages`` raw in a second query fails.

Integrity reads. Four reads look at pages the caller may not see, because a
rule of the tree needs them: the children of a page (a move to another space
refuses a page with children) and the ancestors of a page (a move refuses a
cycle). A third reads all the siblings of a page, because a move renumbers
them 0 to n, and a fourth reads their highest place, for a create (KB-2e).
A fifth and a sixth read the chunks that wait for a
vector, for the embed job and the embed sweep, which apply no member's
clause. None returns a page or a chunk to a caller. Each is
listed in ``_INTEGRITY_READS`` with its reason, and the list fails when it
goes stale.
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

#: A statement that reads the index (KB-3).
_READS_CHUNKS = re.compile(r"\b(?:FROM|JOIN)\s+kb_chunks\b", re.IGNORECASE)

#: The indexer's delete. It names ``FROM kb_chunks`` and reads nothing.
_DELETES_CHUNKS = re.compile(r"\bDELETE\s+FROM\s+kb_chunks\b", re.IGNORECASE)


def _reads_kb(sql: str) -> bool:
    """True when *sql* reads a page or a chunk."""
    return bool(_READS_PAGES.search(sql)
                or _READS_CHUNKS.search(_DELETES_CHUNKS.sub("", sql)))

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
    ("core.py", "sibling_rows"): (
        "A move renumbers ALL the siblings 0 to n (KB-2e), so a sibling the "
        "caller cannot see keeps its place. Only pages._place reads the ids "
        "and sort keys, to write positions. Nothing of it reaches the caller: "
        "the move answer is the rank among the VISIBLE siblings."
    ),
    ("core.py", "next_position"): (
        "A create puts the page after the last sibling, a hidden one too "
        "(KB-2e). It returns one integer, which the INSERT stores. No answer "
        "carries a stored position: each gives the rank among the siblings "
        "the caller sees (pages._rank, page_dict)."
    ),
    ("index.py", "select_pending"): (
        "The embed job of KB-3 reads the chunks of one page, already loaded "
        "and written by the route, that have no vector. It runs after the "
        "commit with no member, and it hands the text to the embed door "
        "only, never to a person."
    ),
    ("sweep.py", "select_waiting"): (
        "The embed sweep of KB-3c reads the oldest chunks of published pages "
        "that have no vector, for each org on KNOWLEDGE_EMBED. It runs in a "
        "background loop with no member to apply the clause for. It hands "
        "the text to the embed door only, and it returns no page, no chunk "
        "and no text to a person. The author columns name who pays."
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
        if not _reads_kb(_text_of(node)):
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


# ── KB-3: the index is read through the same clause ─────────────────────────

def test_the_index_modules_are_in_the_sweep() -> None:
    """Not vacuous: ``gateway/kb_index/`` is scanned, and its reads are found."""
    names = {p.relative_to(_GATEWAY).parts[0] for p in _knowledge_modules()}
    assert "kb_index" in names
    chunk_reads = [
        k for k in _all_reads()
        if k[0] in {"query.py", "index.py"}
    ]
    assert len(chunk_reads) >= 3, chunk_reads


def test_the_fence_flags_a_raw_chunk_read() -> None:
    src = (
        "async def search(db, user, q):\n"
        "    return await db.execute(text('SELECT raw FROM kb_chunks WHERE tsv @@ :q'))\n"
    )
    assert _page_reads(src) == [("search", 2, False)]


def test_the_fence_flags_a_chunk_read_that_joins_pages_without_the_clause() -> None:
    src = (
        "async def search(db, user, q):\n"
        "    vis, params = visible_pages_clause(user, 'p')\n"
        "    return 'SELECT c.raw FROM kb_chunks c JOIN kb_pages p ON p.id = c.source_id'\n"
    )
    assert _page_reads(src) == [("search", 3, False)]


def test_a_chunk_read_through_the_clause_passes() -> None:
    src = (
        "async def search(db, user, q):\n"
        "    vis, params = visible_pages_clause(user, 'p')\n"
        "    return f'SELECT c.raw FROM kb_chunks c JOIN kb_pages p ON p.id = c.source_id "
        "WHERE {vis}'\n"
    )
    assert _page_reads(src) == [("search", 3, True)]


def test_the_indexer_delete_is_not_a_read() -> None:
    src = "SQL = 'DELETE FROM kb_chunks WHERE source_id = CAST(:p AS uuid)'\n"
    assert _page_reads(src) == []
