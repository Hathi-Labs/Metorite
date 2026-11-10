"""The Knowledge route manifest, as the chat reads it: a re-export (MCP-P1a).

Spec: ``project-docs/specs/knowledge_base.md`` §5.3 and §8.1, and
``external_agent_access.md`` §19.3 (AD-1). Board rows WS-61 (KB-4a) and
WS-MCP (MCP-P1a).

The rows live in ``acb_common/app_manifests/knowledge.py`` since MCP-P1a, so
the MCP edge and ``acb_auth`` can read the same table (one manifest, two
doors, D136). This module keeps the names that its readers import, so
``acb_skills.knowledge_tools`` and the tests do not change:

* :data:`MANIFEST` and :class:`Route` are the rows and their type;
* :func:`route_for`, :func:`allowed`, :func:`tools` and :func:`tool_class`
  read the Knowledge rows only.

The client in :mod:`acb_skills.knowledge_tools` asks :func:`allowed` before
it builds a request. ``tests/unit/test_knowledge_chat_coverage.py`` walks the
real Knowledge router against :data:`MANIFEST`.
"""

from __future__ import annotations

from acb_common.app_manifests import base as _base
from acb_common.app_manifests.base import CLASSES, Route
from acb_common.app_manifests.knowledge import KNOWLEDGE, MANIFEST

__all__ = [
    "CLASSES",
    "MANIFEST",
    "Route",
    "allowed",
    "route_for",
    "tool_class",
    "tools",
]


def tools() -> set[str]:
    """Every chat tool the manifest names."""
    return _base.tools(KNOWLEDGE)


def tool_class(name: str) -> str | None:
    """The class a tool acts at, or ``None`` for a tool the table does not name."""
    return _base.tool_class(KNOWLEDGE, name)


def route_for(method: str, path: str) -> Route | None:
    """The row a concrete ``method path`` falls under, or ``None``.

    ``path`` is the concrete path with ids filled in. A query string is cut.
    """
    return _base.route_for(KNOWLEDGE, method, path)


def allowed(method: str, path: str, tool: str) -> tuple[bool, str]:
    """May *tool* send ``method path``? ``(ok, why_not)``.

    The client asks this before it builds a request. It refuses a route that
    the table does not carry, a route with no tool, and a route that the
    table gives to another tool.
    """
    return _base.allowed(KNOWLEDGE, method, path, tool)
