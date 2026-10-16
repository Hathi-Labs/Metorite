"""The Knowledge route manifest: every ``/knowledge`` route, and what the chat does with it.

Spec: ``project-docs/specs/knowledge_base.md`` §5.3 (the classes) and §8.1
(the manifest lives in ``acb_skills``). Board row WS-61, ticket KB-4a.

**This table is the chat's allowlist and its coverage record at once**, in
the shape of ``apps/skills/skill-projects/skill_projects/manifest.py``. The
client in :mod:`acb_skills.knowledge_tools` refuses a verb and path that this
table does not map to a tool. ``tests/unit/test_knowledge_chat_coverage.py``
walks the real Knowledge router and fails on a route that the table does not
carry, and on a row that names a route the router does not serve.

It lives in ``acb_skills`` and not in a skill package, because the client is
here and a client in ``acb_skills`` must not import an app's skill package.

Classes (§5.3):

    A   a read, no card
    B   a write the app can undo, one card
    C   a write that is hard to undo, one card per act
    X   not on the chat surface, ``reason`` says why

A row with no tool names the slice that decides its tool in ``planned``.
KB-4a builds two read tools. Each write waits for KB-5, and an agent never
publishes (§8.1).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = [
    "CLASSES",
    "MANIFEST",
    "Route",
    "allowed",
    "route_for",
    "tool_class",
    "tools",
]

CLASSES = ("A", "B", "C", "X")


@dataclass(frozen=True)
class Route:
    method: str
    #: The path template as FastAPI reports it, prefix included.
    path: str
    #: The tool that reaches this route, or ``""`` when no tool does yet.
    tool: str
    cls: str
    #: Required for class X. Why the chat does not reach this route.
    reason: str = ""
    #: Required for a class A, B or C row with no tool: the slice that decides.
    planned: str = ""

    def __post_init__(self) -> None:
        if self.cls not in CLASSES:
            raise ValueError(f"{self.method} {self.path}: class {self.cls!r} is not one of {CLASSES}")
        if self.cls == "X":
            if not self.reason:
                raise ValueError(f"{self.method} {self.path}: an excluded route needs a reason")
            if self.tool:
                raise ValueError(f"{self.method} {self.path}: an excluded route names no tool")
            return
        if not self.tool and not self.planned:
            raise ValueError(
                f"{self.method} {self.path}: class {self.cls} needs a tool or the slice that plans one"
            )
        if self.tool and self.planned:
            raise ValueError(f"{self.method} {self.path}: a built tool is not planned")


#: D-PM-40: the chat does not write who can see a thing.
_GRANT_REASON = (
    "D-PM-40: the chat does not write grants. Propose the grant in words, "
    "and a person applies it in the app."
)

MANIFEST: tuple[Route, ...] = (
    # ── Spaces ──
    Route("GET", "/knowledge/spaces", "list_spaces", "A"),
    Route("POST", "/knowledge/spaces", "", "C", planned="KB-5"),
    Route("PATCH", "/knowledge/spaces/{space_id}", "", "C", planned="KB-5"),
    Route("PUT", "/knowledge/spaces/{space_id}/grants", "", "X", reason=_GRANT_REASON),
    # ── Pages ──
    Route("GET", "/knowledge/pages", "", "A", planned="KB-5"),
    Route("GET", "/knowledge/pages/{page_id}", "read_page", "A"),
    Route("POST", "/knowledge/pages", "", "B", planned="KB-5"),
    Route("PATCH", "/knowledge/pages/{page_id}", "", "B", planned="KB-5"),
    Route("POST", "/knowledge/pages/{page_id}/publish", "", "B", planned="KB-5"),
    Route("POST", "/knowledge/pages/{page_id}/move", "", "B", planned="KB-5"),
    Route("POST", "/knowledge/pages/{page_id}/archive", "", "C", planned="KB-5"),
    Route("POST", "/knowledge/pages/{page_id}/restore", "", "C", planned="KB-5"),
    Route("POST", "/knowledge/pages/{page_id}/revert", "", "B", planned="KB-5"),
    # ── Search ── `search_company` already asks Knowledge (§20). The
    # Knowledge-only tool waits (§8.1, `search_knowledge` is deferred).
    Route("GET", "/knowledge/search", "", "A", planned="search_knowledge, deferred (§8.1)"),
)


def tools() -> set[str]:
    """Every tool the manifest names."""
    return {r.tool for r in MANIFEST if r.tool}


def tool_class(name: str) -> str | None:
    """The class a tool acts at, or ``None`` for a tool the table does not name."""
    classes = {r.cls for r in MANIFEST if r.tool == name}
    return sorted(classes)[0] if classes else None


def _template_regex(template: str) -> re.Pattern[str]:
    parts = re.split(r"(\{[^}]+\})", template)
    out = "".join("[^/]+" if p.startswith("{") else re.escape(p) for p in parts)
    return re.compile(f"^{out}$")


_COMPILED: tuple[tuple[Route, re.Pattern[str]], ...] = tuple(
    (r, _template_regex(r.path)) for r in MANIFEST
)


def route_for(method: str, path: str) -> Route | None:
    """The row a concrete ``method path`` falls under, or ``None``.

    ``path`` is the concrete path with ids filled in. A query string is cut.
    """
    bare = path.split("?", 1)[0]
    verb = method.upper()
    for route, pattern in _COMPILED:
        if route.method == verb and pattern.match(bare):
            return route
    return None


def allowed(method: str, path: str, tool: str) -> tuple[bool, str]:
    """May *tool* send ``method path``? ``(ok, why_not)``.

    The client asks this before it builds a request. It refuses a route that
    the table does not carry, a route with no tool, and a route that the
    table gives to another tool.
    """
    route = route_for(method, path)
    if route is None:
        return False, f"{method.upper()} {path} is not in the Knowledge route manifest."
    if route.cls == "X":
        return False, f"{method.upper()} {path} is not on the chat surface: {route.reason}"
    if not route.tool:
        return False, f"{method.upper()} {path} has no chat tool yet ({route.planned})."
    if route.tool != tool:
        return False, f"{method.upper()} {path} belongs to {route.tool}, not to {tool}."
    return True, ""
