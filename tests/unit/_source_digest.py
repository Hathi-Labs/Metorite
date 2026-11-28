"""The source digest of a function and of the helpers that it reaches (task 4053).

The idiom is the digest fence of ``test_tenant_request_binding.py``
(``_module_text``, ``_top_level_defs`` and ``_branch_zero_digest``). It cuts
each source from the module FILE by the AST, never by ``inspect``, which
follows ``__wrapped__``. It hashes ``name`` and then
``ast.dump(ast.parse(source))`` for each name in sorted order, with SHA-256.
``ast.dump`` drops comments, blank lines and positions, and it differs between
Python versions, so a pin is keyed by the minor version.

The helper walk:

* It starts at the top-level function of the tool.
* Each ``Name`` that the code LOADS, and that a top-level statement of the
  same module binds, is a helper. A top-level statement is a ``def``, an
  ``async def``, a ``class``, or an assignment to a bare name. So a call, a
  callback passed by name, a decorator and a module constant all count.
* The walk repeats on each new helper, up to :data:`WALK_DEPTH` hops. A walk
  that still finds a new helper at that depth is an error, so the digest
  never covers part of a closure in silence.

The walk does not follow an attribute (``obj.method``, ``module.name``), a
name that the module imports, or a call through ``getattr`` or a subscript.
The fence that uses it names those limits.

Only the standard library is imported here, so a pin for another Python
version can be computed with that interpreter and no virtual environment.
"""
from __future__ import annotations

import ast
import hashlib
from collections.abc import Iterable

#: The most hops of the helper walk. On 2026-11-27 the walk of each covered
#: email tool ends at 5 hops.
WALK_DEPTH = 8


def module_text(path: str) -> str:
    """The source FILE at *path*. A file can start with a BOM."""
    with open(path, encoding="utf-8-sig") as fh:
        return fh.read()


def top_level(text: str) -> dict[str, list[ast.stmt]]:
    """Each name that a top-level statement of *text* binds, and each such
    statement in file order. A name bound twice keeps both statements."""
    out: dict[str, list[ast.stmt]] = {}
    for node in ast.parse(text).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.setdefault(node.name, []).append(node)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    out.setdefault(target.id, []).append(node)
    return out


def _loads(nodes: Iterable[ast.stmt], defs: dict[str, list[ast.stmt]]) -> set[str]:
    return {n.id for node in nodes for n in ast.walk(node)
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load) and n.id in defs}


def closure(defs: dict[str, list[ast.stmt]], start: str) -> set[str]:
    """*start* and each helper that the walk reaches from it.

    Raises :class:`ValueError` when the walk finds a new helper after
    :data:`WALK_DEPTH` hops."""
    seen = {start}
    frontier = {start}
    for _hop in range(WALK_DEPTH):
        frontier = {name for name in _loads(
            (n for f in frontier for n in defs[f]), defs) if name not in seen}
        seen |= frontier
        if not frontier:
            return seen
    if {name for name in _loads((n for f in frontier for n in defs[f]), defs)
            if name not in seen}:
        raise ValueError(f"the helper walk of {start} did not end in {WALK_DEPTH} hops")
    return seen


def source_of(text: str, node: ast.stmt) -> str:
    """The source of *node*, cut from *text*, with its decorators."""
    first = min([node.lineno, *(d.lineno for d in getattr(node, "decorator_list", ()))])
    lines = text.splitlines(keepends=True)
    return "".join(lines[first - 1:node.end_lineno])


def digest(text: str, start: str) -> str:
    """SHA-256 of the normalised AST of *start* and of each helper that the
    walk reaches from it. Raises :class:`KeyError` when *text* binds no
    *start* at the top level."""
    defs = top_level(text)
    if start not in defs:
        raise KeyError(start)
    sources: dict[str, str] = {}
    for name in closure(defs, start):
        for i, node in enumerate(defs[name]):
            sources[f"{name}#{i}"] = source_of(text, node)
    out = hashlib.sha256()
    for name, source in sorted(sources.items()):
        out.update(name.encode())
        out.update(ast.dump(ast.parse(source)).encode())
    return out.hexdigest()
