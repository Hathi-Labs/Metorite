"""The install hrefs that a member may star or hide (WS-PKG PKG-6c-1).

Spec: ``project-docs/specs/package_host.md``, the PKG-6c-1 row of §10, and
``navigation_shell.md`` §3.2b (the storage). The caller is
``PUT /auth/me/shell`` in ``routes/admin/me.py``.

``SHELL_PANES`` stays the system list. An href outside it reaches ``pins``
or ``known`` only while ``PACKAGE_HOST_ORGS`` holds the org, and only when
it is a ``nav`` entry of an install in the viewer's OWN resolve. The route
gets that resolve from ``resolve.resolve_for``, with the org of the bound
session and the viewer ``user.email``, never from request input (R5).

This module is pure. It reads no database, no clock and no flag.

* :func:`safe_href` mirrors ``safeHref`` in
  ``workbench/control_plane/src/lib/shell/packageManifest.ts``, and is a
  little stricter: it also refuses a ``//`` at any place, a DEL and an href
  over :data:`MAX_HREF` characters.
* :func:`system_owns` mirrors ``systemOwns`` in the same file. A system pane
  owns an href when the path is the pane or a child path of the pane.
* :func:`install_nav_hrefs` reads the answer of ``resolve_for``.

Fences: ``tests/unit/test_auth_me_shell.py`` and
``tests/unit/test_auth_me_shell_install_r8.py``.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from gateway.routes.admin.me import SHELL_PANES

#: The longest href that a star may name.
MAX_HREF = 200

#: A control character (C0 or DEL) or a backslash.
_BAD_CHAR = re.compile(r"[\x00-\x1f\x7f\\]")


def safe_href(v: Any) -> str | None:
    """*v* when it is a same-origin path, else None.

    A safe href is text of 1 to :data:`MAX_HREF` characters. It starts with
    one ``/``. It holds no ``//``, no backslash and no control character.
    """
    if not isinstance(v, str) or not v or len(v) > MAX_HREF:
        return None
    if not v.startswith("/") or "//" in v or _BAD_CHAR.search(v):
        return None
    return v


def system_owns(href: str, panes: Iterable[str] = SHELL_PANES) -> bool:
    """True when a system pane owns *href*, exactly or as its parent path."""
    path = re.split(r"[?#]", href, maxsplit=1)[0]
    return any(path == p or path.startswith(f"{p}/") for p in panes)


def install_href(v: Any) -> str | None:
    """*v* when it may name an install pane: safe, and no system pane owns
    it. Else None."""
    href = safe_href(v)
    if href is None or system_owns(href):
        return None
    return href


def install_nav_hrefs(manifest_body: Any) -> frozenset[str]:
    """The hrefs of the ``nav`` entries of installs in one resolve answer.

    *manifest_body* is the answer of ``resolve.resolve_for`` for the viewer.
    An entry counts when it has an ``install_id``, its source is not
    ``system``, and its ``href`` passes :func:`install_href`. A shape that
    this does not know gives no href.
    """
    if not isinstance(manifest_body, Mapping):
        return frozenset()
    contributions = manifest_body.get("contributions")
    if not isinstance(contributions, Mapping):
        return frozenset()
    entries = contributions.get("nav")
    if not isinstance(entries, list):
        return frozenset()
    out: set[str] = set()
    for e in entries:
        if not isinstance(e, Mapping) or e.get("source") == "system":
            continue
        if not isinstance(e.get("install_id"), str) or not e["install_id"]:
            continue
        href = install_href(e.get("href"))
        if href is not None:
            out.add(href)
    return frozenset(out)


def keep_held(values: list[str] | None, held: frozenset[str]) -> list[str] | None:
    """*values* with each href that is not a system pane and not in *held*
    dropped. The order stays. None stays None."""
    if values is None:
        return None
    return [v for v in values if v in SHELL_PANES or v in held]
