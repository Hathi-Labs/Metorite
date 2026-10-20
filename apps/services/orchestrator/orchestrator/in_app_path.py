"""The path check of ``show_in_app`` (WS-62 CF-3, ``chat_first_surface.md`` §4.4).

The orchestrator's ``show_in_app`` tool opens a page of the app for the
member. A model can pass any string as the path, so three checks run here
before the client hears of it.

1. :func:`is_in_app_path` is the Python twin of ``isInAppPath`` in
   ``workbench/control_plane/src/lib/inAppLink.ts``. Both read the same table,
   ``src/lib/inAppPaths.cases.json``, in ``tests/unit/test_in_app_path_twin.py``
   and ``inAppLink.test.ts``. A path that leaves the app, such as
   ``//evil.example`` or ``https://…``, is refused. So is a backslash and a
   control mark, because a URL parser reads each in its own way. Each segment
   is percent-decoded once, and a segment that decodes to ``.``, ``..``, a
   slash, a backslash, a control mark or another percent is refused (review
   of #1197, P0): ``router.push`` resolves ``/projects/../crm`` to ``/crm``.
2. :func:`pane_of` finds the pane the path belongs to. ``PANES`` is the
   allowlist: the LIVE panes of ``launch_surface.md`` §2, as ``nav.ts`` marks
   them, without the admin pages. A first segment that is not a pane is
   refused, so ``/connect``, ``/signup``, ``/settings`` and a Center (D49)
   never open. ``test_show_in_app.py`` holds the list equal to ``nav.ts``.
3. :func:`member_features` says which features the member holds, and a path
   to a pane the member does not hold is refused.

The member comes from the run binding, never from the tool's arguments.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

#: A backslash, or a control mark such as a tab, CR or LF. The same class as
#: ``OFF_PATH_MARKS`` in ``inAppLink.ts``.
_OFF_PATH_MARKS = re.compile(r"[\\\x00-\x1f\x7f]")

#: First segments that are never a page, on either side of the twin.
_NEVER_A_PAGE = frozenset({"api", "_next"})


def path_segments(pathname: str) -> list[str] | None:
    """The decoded segments of *pathname*, or None when one is not a page's.

    The root gives ``[]``. Every other path must have no empty segment (so
    no ``//`` inside and no trailing slash), and each segment must decode
    once, as valid UTF-8, to a name with no slash, backslash, control mark
    or percent, and the name may not be ``.`` or ``..``. The same rule as
    ``pathSegments`` in ``inAppLink.ts``.
    """
    if pathname == "/":
        return []
    out: list[str] = []
    for raw in pathname.split("/")[1:]:
        if raw == "":
            return None
        try:
            seg = unquote(raw, errors="strict")
        except UnicodeDecodeError:
            return None
        if "%" in seg or "/" in seg or _OFF_PATH_MARKS.search(seg):
            return None
        if seg in (".", ".."):
            return None
        out.append(seg)
    return out


def is_in_app_path(path: object) -> bool:
    """True for a path inside this app, such as ``/projects?task=42``.

    Never ``//host``, which a browser reads as another site, never a scheme,
    never ``/api/…`` or ``/_next/…``, and never a path with a dot segment or
    an encoded trick (:func:`path_segments`). The path must start with ONE
    ``/``, carry no backslash and no control mark, and name no host when it
    is split as a URL.
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
    segments = path_segments(parts.path)
    if segments is None:
        return False
    return not segments or segments[0] not in _NEVER_A_PAGE


@dataclass(frozen=True)
class Pane:
    """One live pane of ``nav.ts``: its ``href`` and the feature that gates it."""

    href: str
    #: The feature slug, or None for a pane every member holds.
    feature: str | None


#: The allowlist: the LIVE panes of ``launch_surface.md`` §2 (``launch:
#: "live"`` in ``nav.ts``), with the feature each one needs. The admin pages
#: (``/settings/…``) are left out on purpose, and so is every preview pane.
#: The order does not matter: :func:`pane_of` takes the longest match.
PANES: tuple[Pane, ...] = (
    Pane("/", None),
    Pane("/tasks", "tasks"),
    Pane("/calendar", "tasks"),
    Pane("/people/me", None),
    Pane("/email", "email"),
    Pane("/whatsapp", "whatsapp"),
    Pane("/artifacts", "artifacts"),
    Pane("/projects", "projects"),
    Pane("/crm", "crm"),
    Pane("/people", "people"),
    Pane("/knowledge", "knowledge"),
    Pane("/chat", "chat"),
    Pane("/approvals", "approvals"),
    Pane("/integrations", None),
)


def pane_of(path: str) -> Pane | None:
    """The pane that holds *path*: the longest ``href`` that contains it.

    The same rule as ``paneAt`` in ``assistant.ts``, on the DECODED
    segments. The query and the fragment do not count. ``/`` matches the
    root alone. A path with a bad segment has no pane.
    """
    segments = path_segments(urlsplit(path).path)
    if segments is None:
        return None
    pathname = "/" + "/".join(segments)
    best: Pane | None = None
    for pane in PANES:
        if pane.href == "/":
            inside = pathname == "/"
        else:
            inside = pathname == pane.href or pathname.startswith(pane.href + "/")
        if inside and (best is None or len(pane.href) > len(best.href)):
            best = pane
    return best


def chat_first_tools_enabled() -> bool:
    """True when ``CHAT_FIRST_TOOLS`` is on. Fails closed.

    The one reader of the setting, as ``company_search_enabled`` is for its
    flag. OFF leaves the orchestrator's tool list as it was before CF-3.
    """
    try:
        from acb_common import get_settings

        return getattr(get_settings(), "chat_first_tools", False) is True
    except Exception:
        return False


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


__all__ = [
    "PANES",
    "Pane",
    "chat_first_tools_enabled",
    "is_in_app_path",
    "member_features",
    "pane_of",
    "path_segments",
    "refusal",
]
