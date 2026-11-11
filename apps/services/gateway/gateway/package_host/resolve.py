"""The resolve of the shell manifest for one viewer (WS-PKG PKG-5b part 2).

Spec: ``project-docs/specs/package_host.md`` §5.2 (the five steps) and the
PKG-5b row of §10. The route is ``GET /shell/manifest`` in
``routes/shell/manifest.py``. This module has two parts:

* **The pure part** (:func:`resolve`, :func:`version_of`,
  :func:`etag_matches`). It reads no database and no clock. It picks the one
  install of each app, the winner of each key, the feature filter, the
  needs, the order and the hash.
* **The SQL part** (:func:`resolve_for`). It reads the installs that the
  viewer sees, and the claims of those installs, in a session that the
  caller opened with ``tenant_session()``. It opens no session and writes
  nothing.

**The steps of §5.2.**

1. The system packages come from
   :func:`acb_common.app_manifests.shell_manifest.load_contributes`. An entry
   holds ``key``, ``source`` ``system``, ``install_id`` None, ``needs_setup``
   False, ``feature`` and ``package``, and no payload.
2. An install shows only when it is ``active``, its ``ends_at`` is NULL or
   in the future, and its app is a Studio package that is not archived.
   This read hides an install at its ``ends_at`` with no write. So
   ``store.expire_due`` (PKG-10a) only archives it later, and never has to
   hide it. An ``org`` install shows to each member. A ``group`` install shows to a
   member of the group, by ``routes/crm/access.my_groups``, the one group
   read. A private install shows to its owner, and to each grantee by
   :data:`gateway.package_host.sharing.SHARE_ROW`. A draft (``version``
   NULL) shows to its owner only.
3. A claim whose ``payload.feature`` the viewer does not hold never shows.
   ``user.can_use_feature`` decides, the one entitlement seam. A claim with
   no feature shows to each member. A feature that is not text fails closed.
4. ``needs_setup`` comes from the needs of the manifest, and is never
   stored. A need is ``{"feature": <slug>}`` or ``{"package": <id>}``. Any
   other shape counts as unmet (fail closed). A package need is met only by
   a system package or by an install that this viewer sees.
5. The arrangement of §9 is not in PKG-5b.

**One app.** A viewer can see one app at two scopes (§4 lets them both
claim). The resolve keeps the widest: org, then group, then private, then
the older ``created_at``, then the lower id.

**One key.** Two apps can claim one key in front of one viewer (§5.2). The
same order picks one winner. A key that a system package holds stays the
system's. ``triggers`` and ``slot`` allow many claims, so they pick no
winner.

**The order in a point.** The system entries come first, in the order of
``load_contributes``. The claims of installs follow, by ``position``, then
by key, then by the order of their installs.

**The version** is the first 16 hex digits of the SHA-256 of the canonical
JSON of the viewer's own entries (``sort_keys``, compact separators). So it
needs no column, and it names nothing that the viewer does not see. The
route sends it as the ETag.

Fences: ``tests/unit/test_package_host_resolve.py`` (the pure part, with no
database) and ``tests/unit/test_package_host_resolve_r8.py`` (a real
database, FORCE RLS, two orgs).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from functools import cache
from typing import Any

from acb_common.app_manifests.shell_manifest import SystemContribution, load_contributes
from sqlalchemy import text

from gateway.package_host.sharing import SHARE_ROW
from gateway.package_host.store import _BOUND_ORG, MANY_CLAIMS

#: The rank of a scope. A lower rank is a wider scope, and it wins (§5.2).
SCOPE_RANK: dict[str, int] = {"org": 0, "group": 1, "private": 2}
#: The length of the version, in hex digits.
VERSION_HEX = 16
#: The two shapes of a need in PKG-5b (§5.2 step 4).
NEED_KINDS = frozenset({"feature", "package"})

CanUse = Callable[[str], bool]


# ── The rows ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class InstallRow:
    """One install that the viewer sees after the SQL of step 2.

    ``manifest`` is the manifest of the installed version, or of the app for
    a draft. It can be any JSON value, and the needs check reads it.
    """

    id: str
    app_id: str
    scope_kind: str
    scope_subject: str
    created_at: datetime
    manifest: Any


@dataclass(frozen=True)
class ClaimRow:
    """One claim of an install at one point."""

    install_id: str
    point: str
    key: str
    payload: Any
    position: int = 0


# ── The pure part ───────────────────────────────────────────────────────────


def order_key(install: InstallRow) -> tuple[int, datetime, str]:
    """The order of two installs: the wider scope, then the older, then the
    lower id. The first one wins."""
    return (SCOPE_RANK.get(install.scope_kind, len(SCOPE_RANK)), install.created_at, install.id)


def widest_per_app(installs: Iterable[InstallRow]) -> list[InstallRow]:
    """One install for each app: the first by :func:`order_key`."""
    best: dict[str, InstallRow] = {}
    for inst in installs:
        cur = best.get(inst.app_id)
        if cur is None or order_key(inst) < order_key(cur):
            best[inst.app_id] = inst
    return sorted(best.values(), key=order_key)


def manifest_id(manifest: Any) -> str | None:
    """The ``id`` of a manifest, or None."""
    if isinstance(manifest, Mapping):
        mid = manifest.get("id")
        if isinstance(mid, str) and mid:
            return mid
    return None


def needs_met(manifest: Any, can_use: CanUse, packages: frozenset[str] | set[str]) -> bool:
    """True when each need of *manifest* is met (§5.2 step 4).

    A manifest that is not an object, a ``needs`` that is not a list, and a
    need of any other shape are unmet. A need is an object with one key,
    ``feature`` or ``package``, and a text value that is not empty.
    """
    if not isinstance(manifest, Mapping):
        return False
    needs = manifest.get("needs", [])
    if not isinstance(needs, list):
        return False
    for need in needs:
        if not isinstance(need, Mapping) or len(need) != 1:
            return False
        [(kind, value)] = need.items()
        if kind not in NEED_KINDS or not isinstance(value, str) or not value:
            return False
        if kind == "feature" and not can_use(value):
            return False
        if kind == "package" and value not in packages:
            return False
    return True


def feature_ok(feature: Any, can_use: CanUse) -> bool:
    """True when the viewer may see an entry of *feature* (§5.2 step 3).

    No feature means open. A feature that is not text, or is empty, fails
    closed.
    """
    if feature is None:
        return True
    if not isinstance(feature, str) or not feature:
        return False
    return can_use(feature)


def system_package_ids(system: Mapping[str, Sequence[SystemContribution]]) -> frozenset[str]:
    """The id of each system package that holds an entry."""
    return frozenset(c.package for entries in system.values() for c in entries)


def resolve(
    system: Mapping[str, Sequence[SystemContribution]],
    installs: Iterable[InstallRow],
    claims: Iterable[ClaimRow],
    can_use: CanUse,
) -> dict[str, list[dict[str, Any]]]:
    """The entries of each point for one viewer, in order (§5.2 steps 1 to 4).

    *installs* are the installs that the viewer sees. *claims* are their
    claims. *can_use* is ``user.can_use_feature`` of the viewer.
    """
    kept = widest_per_app(installs)
    by_id = {i.id: i for i in kept}
    packages = system_package_ids(system) | {
        mid for i in kept if (mid := manifest_id(i.manifest)) is not None}
    setup = {i.id: not needs_met(i.manifest, can_use, packages) for i in kept}

    out: dict[str, list[dict[str, Any]]] = {}
    held: set[tuple[str, str]] = set()
    for point, entries in system.items():
        for c in entries:
            held.add((c.point, c.key))
            if feature_ok(c.feature, can_use):
                out.setdefault(point, []).append({
                    "key": c.key, "source": "system", "install_id": None,
                    "needs_setup": False, "feature": c.feature, "package": c.package})

    winners: dict[tuple[str, str], tuple[ClaimRow, InstallRow]] = {}
    many: list[tuple[ClaimRow, InstallRow]] = []
    for c in claims:
        inst = by_id.get(c.install_id)
        if inst is None or not isinstance(c.payload, Mapping):
            continue
        if c.point in MANY_CLAIMS:
            many.append((c, inst))
            continue
        if (c.point, c.key) in held:
            continue
        cur = winners.get((c.point, c.key))
        if cur is None or order_key(inst) < order_key(cur[1]):
            winners[(c.point, c.key)] = (c, inst)

    chosen = [(c, i) for c, i in (*winners.values(), *many)
              if feature_ok(c.payload.get("feature"), can_use)]
    chosen.sort(key=lambda ci: (ci[0].position, ci[0].key, order_key(ci[1])))
    for c, inst in chosen:
        out.setdefault(c.point, []).append({
            **dict(c.payload), "key": c.key, "source": inst.scope_kind,
            "install_id": inst.id, "needs_setup": setup[inst.id]})
    return out


def version_of(contributions: Mapping[str, Any]) -> str:
    """The first 16 hex digits of the SHA-256 of the canonical JSON."""
    canonical = json.dumps(contributions, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:VERSION_HEX]


def manifest_body(contributions: Mapping[str, Any]) -> dict[str, Any]:
    """The answer of ``GET /shell/manifest``: the version and the entries."""
    return {"version": version_of(contributions), "contributions": dict(contributions)}


def etag_matches(if_none_match: str | None, version: str) -> bool:
    """True when the ``If-None-Match`` header names *version*, or is ``*``.

    A weak tag (``W/"..."``) matches too, as RFC 9110 §13.1.2 says for this
    header.
    """
    if not if_none_match:
        return False
    want = f'"{version}"'
    for part in if_none_match.split(","):
        tag = part.strip()
        if tag == "*":
            return True
        if tag.startswith("W/"):
            tag = tag[2:]
        if tag == want:
            return True
    return False


# ── The SQL part ────────────────────────────────────────────────────────────

#: The installs that the viewer sees (§5.2 step 2). ``package_install`` has
#: no alias, because :data:`SHARE_ROW` names it so. The resolve adds the
#: viewer to the share rule, and keeps no copy of it.
INSTALLS_SQL = (
    "SELECT CAST(package_install.id AS text), CAST(package_install.app_id AS text),"
    " package_install.scope_kind, package_install.scope_subject,"
    " package_install.created_at,"
    " CASE WHEN package_install.version IS NULL THEN a.manifest ELSE v.manifest END"
    " FROM package_install"
    " JOIN apps AS a ON a.organization_id = package_install.organization_id"
    " AND a.id = package_install.app_id"
    " LEFT JOIN app_versions AS v ON v.organization_id = package_install.organization_id"
    " AND v.app_id = package_install.app_id AND v.version = package_install.version"
    f" WHERE package_install.organization_id = {_BOUND_ORG}"
    " AND package_install.state = 'active'"
    " AND (package_install.ends_at IS NULL OR package_install.ends_at > now())"
    " AND a.format = 'manifest' AND a.status <> 'archived'"
    " AND (package_install.version IS NOT NULL"
    " OR package_install.scope_subject = :viewer)"
    " AND (package_install.scope_kind = 'org'"
    " OR (package_install.scope_kind = 'group'"
    " AND package_install.scope_subject = ANY(CAST(:groups AS text[])))"
    " OR (package_install.scope_kind = 'private'"
    " AND (package_install.scope_subject = :viewer"
    " OR EXISTS (SELECT 1 FROM app_grants AS g"
    f" WHERE {SHARE_ROW} AND lower(g.subject) = :viewer))))"
)

#: The claims of the installs that the resolve keeps.
CLAIMS_SQL = (
    "SELECT CAST(install_id AS text), point, key, payload, position"
    " FROM package_contribution"
    f" WHERE organization_id = {_BOUND_ORG}"
    " AND install_id = ANY(CAST(:ids AS uuid[]))"
)


@cache
def system_contributions() -> dict[str, tuple[SystemContribution, ...]]:
    """The entries of the system packages. The files are fixed at release."""
    return load_contributes()


async def read_installs(db: Any, viewer: str, groups: Sequence[str]) -> list[InstallRow]:
    """The installs that *viewer* sees, in the bound org (step 2)."""
    rows = (await db.execute(text(INSTALLS_SQL), {
        "viewer": viewer, "groups": list(groups)})).all()
    return [InstallRow(id=str(r[0]), app_id=str(r[1]), scope_kind=str(r[2]),
                       scope_subject=str(r[3]), created_at=r[4], manifest=r[5])
            for r in rows]


async def read_claims(db: Any, install_ids: Sequence[str]) -> list[ClaimRow]:
    """The claims of *install_ids*, in the bound org."""
    if not install_ids:
        return []
    rows = (await db.execute(text(CLAIMS_SQL), {"ids": list(install_ids)})).all()
    return [ClaimRow(install_id=str(r[0]), point=str(r[1]), key=str(r[2]),
                     payload=r[3], position=int(r[4])) for r in rows]


async def resolve_for(db: Any, *, viewer: str, can_use: CanUse) -> dict[str, Any]:
    """The answer of ``GET /shell/manifest`` for *viewer*.

    *db* is a session that the caller opened with ``tenant_session()``.
    *viewer* is the address of the session member, never request input.
    *can_use* is ``user.can_use_feature`` of that member.
    """
    # A lazy import: the CRM package is large, and the gateway loads it first.
    from gateway.routes.crm.access import my_groups

    member = (viewer or "").strip().lower()
    groups = sorted(await my_groups(db, member)) if member else []
    kept = widest_per_app(await read_installs(db, member, groups))
    claims = await read_claims(db, [i.id for i in kept])
    return manifest_body(resolve(system_contributions(), kept, claims, can_use))
