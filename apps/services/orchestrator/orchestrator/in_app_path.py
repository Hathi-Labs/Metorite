"""The path check of ``show_in_app`` (WS-62 CF-3, ``chat_first_surface.md`` §4.4).

The orchestrator's ``show_in_app`` tool opens a page of the app for the
member. A model can pass any string as the path, so two checks run here
before the client hears of it.

1. :func:`is_in_app_path` is the Python twin of ``isInAppPath`` in
   ``workbench/control_plane/src/lib/inAppLink.ts``. Both read the same table,
   ``src/lib/inAppPaths.cases.json``, in ``tests/unit/test_in_app_path_twin.py``
   and ``inAppLink.test.ts``. A path that leaves the app, such as
   ``//evil.example`` or ``https://…``, is refused. So is a backslash and a
   control mark, because a URL parser reads each in its own way.
2. :func:`pane_of` finds the pane the path belongs to, and
   :func:`member_features` says which features the member holds. A path to a
   pane the member does not hold is refused. ``PANES`` mirrors the literal
   ``href`` entries of ``nav.ts``, and ``test_show_in_app.py`` holds the two
   equal. An admin-only pane and a Center are not here, so the tool never
   opens them (D49).

The member comes from the run binding, never from the tool's arguments.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

#: A backslash, or a control mark such as a tab, CR or LF. The same class as
#: ``OFF_PATH_MARKS`` in ``inAppLink.ts``.
_OFF_PATH_MARKS = re.compile(r"[\\\x00-\x1f\x7f]")


def is_in_app_path(path: object) -> bool:
    """True for a path inside this app, such as ``/projects?task=42``.

    Never ``//host``, which a browser reads as another site, never a scheme,
    and never ``/api/…``, which is a JSON answer and not a page. The path must
    start with ONE ``/``, carry no backslash and no control mark, and name no
    host when it is split as a URL.
    """
    if not isinstance(path, str) or not path.startswith("/") or path.startswith("//"):
        return False
    if _OFF_PATH_MARKS.search(path):
        return False
    try:
        parts = urlsplit(path)
    except ValueError:
        return False
    if parts.scheme or parts.netloc:
        return False
    pathname = parts.path
    return not pathname.startswith("/api/") and pathname != "/api"


@dataclass(frozen=True)
class Pane:
    """One pane of ``nav.ts``: its ``href`` and the feature that gates it."""

    href: str
    #: The feature slug, or None for a pane every member holds.
    feature: str | None


#: The literal ``href`` entries of ``nav.ts``, with the feature each one
#: needs. The order does not matter: :func:`pane_of` takes the longest match.
#: An admin-only pane (``/settings/organization``) is left out on purpose.
PANES: tuple[Pane, ...] = (
    Pane("/", None),
    Pane("/tasks", "tasks"),
    Pane("/calendar", "tasks"),
    Pane("/people/me", None),
    Pane("/dashboard", "dashboard"),
    Pane("/email", "email"),
    Pane("/whatsapp", "whatsapp"),
    Pane("/notes", "notes"),
    Pane("/memory", "memory"),
    Pane("/artifacts", "artifacts"),
    Pane("/projects", "projects"),
    Pane("/crm", "crm"),
    Pane("/people", "people"),
    Pane("/knowledge", "knowledge"),
    Pane("/chat", "chat"),
    Pane("/workflows", "workflows"),
    Pane("/build/apps", "build.apps"),
    Pane("/build/agents", "build.agents"),
    Pane("/approvals", "approvals"),
    Pane("/settings/appearance", None),
    Pane("/agents", "agents"),
    Pane("/integrations", None),
    Pane("/observability", "observability"),
)


def pane_of(path: str) -> Pane | None:
    """The pane that holds *path*: the longest ``href`` that contains it.

    The same rule as ``paneAt`` in ``assistant.ts``. The query and the
    fragment do not count. ``/`` matches the root alone.
    """
    pathname = urlsplit(path).path
    best: Pane | None = None
    for pane in PANES:
        if pane.href == "/":
            inside = pathname == "/"
        else:
            inside = pathname == pane.href or pathname.startswith(pane.href + "/")
        if inside and (best is None or len(pane.href) > len(best.href)):
            best = pane
    return best


async def member_features() -> frozenset[str] | None:
    """The feature slugs the run's member holds, or None when nobody is known.

    The member is the VERIFIED member of the run
    (``executor._verified_run_member``). The read goes through
    ``acb_auth.resolve_access``, which caches for a short time, so a turn
    pays at most one read. Any failure gives None, and the caller refuses.
    """
    try:
        from orchestrator.executor import _verified_run_member

        member = _verified_run_member()
    except Exception:
        return None
    if not member:
        return None
    try:
        from acb_auth import resolve_access

        access = await resolve_access(member)
    except Exception:
        return None
    if not getattr(access, "is_active", False):
        return None
    return frozenset(access.allowed_features())


def refusal(path: str, features: frozenset[str] | None) -> str | None:
    """Why *path* may not open for a member with *features*, or None.

    The shape comes first, then the pane, then the member's hold on it. A
    member nobody can name (``features`` is None) holds nothing.
    """
    if not is_in_app_path(path):
        return "not a path inside this app"
    pane = pane_of(path)
    if pane is None:
        return "no page of this app at that path"
    if pane.feature is None:
        return None
    if features is None or pane.feature not in features:
        return f"the member does not hold {pane.href}"
    return None


__all__ = ["PANES", "Pane", "is_in_app_path", "member_features", "pane_of", "refusal"]
