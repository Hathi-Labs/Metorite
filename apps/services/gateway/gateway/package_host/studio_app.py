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

This module runs no SQL. The store is the one writer, and the route is the
one place that decides who may call this.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

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
