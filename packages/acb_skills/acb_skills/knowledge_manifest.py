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
KB-4a builds two read tools. KB-5a gives ``POST /knowledge/proposals`` to the
two propose tools, and nothing else. An agent never publishes (§8.1, F2): no
tool maps to a publish, an accept, a reject, a restore or an archive, and
``tests/unit/test_kb_proposals.py`` fails if one does.
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
    #: More tools that reach the same route (KB-5a: one route, two tools).
    also: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.cls not in CLASSES:
            raise ValueError(f"{self.method} {self.path}: class {self.cls!r} is not one of {CLASSES}")
        if self.cls == "X":
            if not self.reason:
                raise ValueError(f"{self.method} {self.path}: an excluded route needs a reason")
            if self.tool or self.also:
                raise ValueError(f"{self.method} {self.path}: an excluded route names no tool")
            return
        if self.also and not self.tool:
            raise ValueError(f"{self.method} {self.path}: 'also' needs a first tool")
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

#: F2 (§8.1): the agent never publishes. A person decides in the app.
_DECIDE_REASON = (
    "D-KB-4 and F2: an agent never publishes. A person accepts or rejects a "
    "proposal in the Knowledge app, and the route refuses chat and mcp."
)

#: KB-9a: a gap is closed by a person in the Knowledge app.
_GAP_DECISION_REASON = (
    "A person closes a gap in the Knowledge app: Dismiss needs "
    "knowledge:manage, and \"I wrote it\" links a page the person wrote. The "
    "chat does not close a gap for the member (knowledge_base.md §8.4)."
)

#: KB-2f: a favourite is a personal shortcut of the Knowledge app.
_FAVORITE_REASON = (
    "A favourite is the member's own shortcut in the Knowledge app, not "
    "company knowledge. The member stars a page in the app."
)

#: KC-1a: a consent and a disconnect are the member's own acts, in the browser.
_SOURCE_CONSENT_REASON = (
    "A member connects a file source in the browser: the consent page of the "
    "provider needs the member, and the callback checks the member of the "
    "session (knowledge_curation.md §7.1). The chat does not connect a drive."
)
_SOURCE_DISCONNECT_REASON = (
    "A disconnect can end the shared Google grant of the member, Gmail "
    "included (knowledge_base.md §7.4). The member or a manager does it in "
    "the Knowledge app, never the chat."
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
    # ── Home and favourites (KB-2f, §21.7) ── A favourite is the member's
    # own shortcut in the app, so the chat neither reads nor writes it.
    Route("GET", "/knowledge/recent", "", "A", planned="KB-5"),
    Route("GET", "/knowledge/favorites", "", "X", reason=_FAVORITE_REASON),
    Route("POST", "/knowledge/pages/{page_id}/favorite", "", "X", reason=_FAVORITE_REASON),
    Route("DELETE", "/knowledge/pages/{page_id}/favorite", "", "X", reason=_FAVORITE_REASON),
    # KB-2g: one old version with its body, for the history modal.
    Route("GET", "/knowledge/pages/{page_id}/versions/{version}", "", "A", planned="KB-5"),
    # KB-2h: the pages that link here, for the "Linked from" list.
    Route("GET", "/knowledge/pages/{page_id}/backlinks", "", "A", planned="KB-5"),
    # ── Proposals (KB-5a, §8.1) ── The two propose tools file a proposal and
    # nothing else. Accept and reject are a person's, in the app.
    Route("POST", "/knowledge/proposals", "propose_page", "B", also=("propose_edit",)),
    Route("GET", "/knowledge/proposals", "", "A", planned="KB-5b"),
    Route("POST", "/knowledge/proposals/{proposal_id}/accept", "", "X", reason=_DECIDE_REASON),
    Route("POST", "/knowledge/proposals/{proposal_id}/reject", "", "X", reason=_DECIDE_REASON),
    # ── Search ── `search_company` already asks Knowledge (§20). The
    # Knowledge-only tool waits (§8.1, `search_knowledge` is deferred).
    Route("GET", "/knowledge/search", "", "A", planned="search_knowledge, deferred (§8.1)"),
    # ── Gaps (KB-9a, §8.4 door 3) ── the questions Knowledge could not
    # answer. A read may join the chat later. A person decides each close.
    Route("GET", "/knowledge/gaps", "", "A", planned="KB-AI-3 (§22)"),
    Route("POST", "/knowledge/gaps/{gap_id}/dismiss", "", "X", reason=_GAP_DECISION_REASON),
    Route("POST", "/knowledge/gaps/{gap_id}/answered", "", "X", reason=_GAP_DECISION_REASON),
    # ── Sources (KC-1a, knowledge_curation.md §7) ── the tree is names only. A
    # read may join the chat with `search_knowledge` (knowledge_base.md §7.2).
    Route("POST", "/knowledge/sources/google_drive/connect", "", "X",
          reason=_SOURCE_CONSENT_REASON),
    Route("GET", "/knowledge/sources/google_drive/callback", "", "X",
          reason=_SOURCE_CONSENT_REASON),
    Route("GET", "/knowledge/sources", "", "A", planned="search_knowledge, deferred (§8.1)"),
    Route("GET", "/knowledge/sources/{source_id}/tree", "", "A",
          planned="search_knowledge, deferred (§8.1)"),
    Route("DELETE", "/knowledge/sources/{source_id}", "", "X",
          reason=_SOURCE_DISCONNECT_REASON),
)


def tools() -> set[str]:
    """Every tool the manifest names."""
    return {name for r in MANIFEST for name in (r.tool, *r.also) if name}


def tool_class(name: str) -> str | None:
    """The class a tool acts at, or ``None`` for a tool the table does not name."""
    classes = {r.cls for r in MANIFEST if name and name in (r.tool, *r.also)}
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
    if tool not in (route.tool, *route.also):
        return False, f"{method.upper()} {path} belongs to {route.tool}, not to {tool}."
    return True, ""
