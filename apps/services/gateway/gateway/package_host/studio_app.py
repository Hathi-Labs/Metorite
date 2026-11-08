"""A published Studio app joins the registry as a private nav entry (WS-PKG
PKG-8a).

Spec: ``project-docs/specs/package_host.md`` §4 (the ``nav`` point) and the
PKG-8a row of §10. The caller is the publish route,
``routes/studio/packages.py``. It calls :func:`keep_private_install` in the
session of the publish, after ``sync_objects``, and only while
``flags.package_host_allowed`` holds the org.

**One install for the owner.** The first publish writes one private install
for the owner, with ``version`` the published number and no end date. Each
later publish moves that install to the new version and replaces its claims
(``store.set_version``). So a publish never writes a second row and never
answers 409 ``already_installed``.

**One claim.** The install claims one ``nav`` key: the href of the app,
:func:`app_href`. The payload holds the href, the name of the manifest as
the label, the icon of a package card in the Studio library, and the
feature ``studio``. The workbench serves the href with a redirect to the app
page (``src/app/apps/[id]/page.tsx``).

⚠️ **The href is outside ``/studio``.** The sidebar drops an install entry
whose href a system pane owns (``systemOwns`` in
``src/lib/shell/packageManifest.ts``), and ``/studio`` is a system pane. So
the prefix is :data:`APP_HREF_PREFIX`. The shell manifest ``studio.json``
gates the ``/apps`` route on the feature ``studio``. Fence:
``tests/unit/test_studio_publish_install.py``.

**An org or a group install (PKG-8b).** The route
``POST /studio/packages/{id}/install`` calls :func:`install_scope` on the
body, checks ``studio:publish`` (D180.3), locks the ``apps`` row, and then
calls :func:`keep_scope_install` with the live version. That function moves the
install at the scope when it exists, and writes it when it does not, with
the claims of :func:`claims_of`. A later publish moves only the private
install, so the holder approves a new version by a new call.

This module runs no SQL. The store is the one writer, and the route is the
one place that decides who may call this.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from acb_auth.permissions import normalize_grant_subject

from gateway.package_host import store
from gateway.package_host.store import Contribution, Install

#: The path of an installed Studio app. The workbench route
#: ``src/app/apps/[id]/page.tsx`` serves it.
APP_HREF_PREFIX = "/apps/"
#: The icon of a package card in the Studio library (``AppsGallery.tsx``).
NAV_ICON = "Boxes"
#: The feature of the app page. Only a holder of it may open the app.
NAV_FEATURE = "studio"
#: The label of an app whose manifest name is empty, as in the Studio library.
UNTITLED = "Untitled app"


def app_href(app_id: str) -> str:
    """The sidebar href of the Studio app *app_id*."""
    return f"{APP_HREF_PREFIX}{app_id}"


def claims_of(app_id: str, manifest: Mapping[str, Any]) -> list[Contribution]:
    """The claims of a published Studio app: one ``nav`` entry (PKG-8a).

    PKG-8c adds the jobs, and PKG-8d adds the cards, the search and the
    needs.
    """
    name = manifest.get("name")
    label = name.strip() if isinstance(name, str) and name.strip() else UNTITLED
    href = app_href(app_id)
    return [Contribution(point="nav", key=href, payload={
        "href": href, "label": label, "icon": NAV_ICON, "feature": NAV_FEATURE})]


async def keep_private_install(
    db: Any,
    *,
    app_id: str,
    version: int,
    owner: str,
    manifest: Mapping[str, Any],
) -> Install:
    """Keep the private install of *owner* at *version*, with its claims.

    *db* is the session of the publish. *owner* is the session member, who
    owns the package. The function moves the install to *version* when it
    exists, and writes it when it does not. A refusal of the store raises an
    :class:`store.InstallError`, and the route answers with its
    ``detail_for`` of the member.
    """
    claims = claims_of(app_id, manifest)
    moved = await store.set_version(
        db, app_id=app_id, version=version, scope_subject=owner, scope_kind="private",
        contributions=claims)
    if moved is not None:
        return moved
    return await store.install(
        db, app_id=app_id, version=version, scope_subject=owner, scope_kind="private",
        installed_by=owner, ends_at=None, contributions=claims)


#: The scopes of the install route (PKG-8b). A private install comes with the
#: publish (PKG-8a), so the route takes no address.
ROUTE_KINDS = frozenset({"org", "group"})
#: The refusal of a scope that the route does not take.
BAD_SCOPE = ("The scope is org or group:<slug>. Your own private install comes with "
             "each publish, so send one of the two.")


def install_scope(raw: object) -> tuple[str, str]:
    """The kind and the normalised subject of the scope in a route body.

    *raw* is the value of ``scope`` in the body: ``org`` or
    ``group:<slug>``. The case of the text does not matter: ``Org``, ``ORG``
    and ``GROUP:Ops`` fold to ``org`` and ``group:ops``, as a slug is lower
    case. Each other value, an address too, raises
    :class:`store.ScopeRefused` (422), and the route reads nothing. The store
    checks the subject again before its first statement.
    """
    refused = store.ScopeRefused("bad_scope", BAD_SCOPE)
    if not isinstance(raw, str) or "\x00" in raw:
        raise refused
    try:
        # A lone surrogate raises UnicodeEncodeError, a ValueError.
        if len(raw.encode("utf-8")) > store.MAX_SUBJECT_BYTES:
            raise refused
        subject = normalize_grant_subject(raw.strip().lower())
    except ValueError:
        raise refused from None
    kind = "org" if subject == "org" else "group" if subject.startswith("group:") else "private"
    if kind not in ROUTE_KINDS:
        raise refused
    return kind, subject


async def keep_scope_install(
    db: Any,
    *,
    app_id: str,
    version: int,
    scope_kind: str,
    scope_subject: str,
    member: str,
    manifest: Mapping[str, Any],
) -> tuple[Install, bool]:
    """Keep the install of *app_id* at an org or a group scope, at *version*.

    *db* is the session of the route, which holds the ``apps`` row ``FOR KEY
    SHARE`` already. *member* is the session member, and the store writes
    it as ``installed_by`` of a new install. The function moves the install
    at the scope when it exists, and writes it when it does not, as
    :func:`keep_private_install` does. Answers the install and True when it
    wrote a new one. A refusal of the store raises an
    :class:`store.InstallError`, and the route answers with its
    ``detail_for`` of the member.
    """
    if scope_kind not in ROUTE_KINDS:
        raise store.ScopeRefused("bad_scope", BAD_SCOPE)
    claims = claims_of(app_id, manifest)
    moved = await store.set_version(
        db, app_id=app_id, version=version, scope_subject=scope_subject,
        scope_kind=scope_kind, contributions=claims)
    if moved is not None:
        return moved, False
    made = await store.install(
        db, app_id=app_id, version=version, scope_subject=scope_subject, scope_kind=scope_kind,
        installed_by=member, ends_at=None, contributions=claims)
    return made, True
