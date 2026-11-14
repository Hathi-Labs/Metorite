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
   in the future, and its app is a Studio package that is not archived and
   not paused (``apps.paused_at``, WS-STU J3a). The pause of a package also
   pauses each install, so this term covers an install that a publish or a
   restore makes active during the pause.
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
   a system package that the viewer can use, or by an install that this
   viewer sees. A system package counts when one of its entries passes the
   feature check of step 3. The ``extends``
   of a sub-app (§8.2, PKG-15a) is one more package need. A host that is
   missing, paused, not text or the sub-app itself leaves the need unmet.
   Each entry of an install with ``extends`` carries ``"extends": <host>``.
   WS-STU I1a: while ``STUDIO_CONNECTION_GATE_ORGS`` holds the bound org
   (``acb_common.studio_flags.connection_gate_on``), each name of the
   manifest's ``connections`` is one more need (:func:`connections_met`).
   WS-STU I9a: a need has an account, ``org`` or ``member`` (``as``). A
   ``member`` need is met only by a ``connected`` member row of THIS viewer.
   An ``org`` need is met only by a ``connected`` org row AND an ``approved``
   version of an ORG install (:func:`org_install_approved`). A private
   install, a group install and a draft fail closed. A ``connections`` value
   of a wrong shape fails closed. With the
   org off the list, ``connections`` makes no need, as before I1a. ``needs``
   holds no connection need (``NEED_KINDS``).
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

**PKG-7.** :func:`install_feature_slugs` reads the feature slugs of the
install claims, and ``/auth/me`` adds them to its lists. Fences:
``tests/unit/test_auth_me_features.py`` and
``tests/unit/test_auth_me_install_features_r8.py``.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from functools import cache
from typing import Any, NamedTuple

from acb_common.app_manifests.shell_manifest import SystemContribution, load_contributes
from acb_common.db import current_tenant
from acb_common.studio_flags import connection_gate_on
from acb_common.studio_manifest import connection_item
from sqlalchemy import text

from gateway.package_host.sharing import SHARE_ROW
from gateway.package_host.store import _BOUND_ORG, MANY_CLAIMS

#: The rank of a scope. A lower rank is a wider scope, and it wins (§5.2).
SCOPE_RANK: dict[str, int] = {"org": 0, "group": 1, "private": 2}
#: The length of the version, in hex digits.
VERSION_HEX = 16
#: The two shapes of a need in PKG-5b (§5.2 step 4).
NEED_KINDS = frozenset({"feature", "package"})

#: The shape of a package id. It is the pattern of ``Manifest.extends``.
PACKAGE_ID = re.compile(r"^[a-z][a-z0-9_-]{0,47}$")

CanUse = Callable[[str], bool]


# ── The rows ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class InstallRow:
    """One install that the viewer sees after the SQL of step 2.

    ``manifest`` is the manifest of the installed version, or of the app for
    a draft. It can be any JSON value, and the needs check reads it.
    ``version`` is the installed version, and None for a draft. The viewer
    read of ``viewer.py`` (WS-STU J2a) reads it.
    """

    id: str
    app_id: str
    scope_kind: str
    scope_subject: str
    created_at: datetime
    manifest: Any
    version: int | None = None
    #: WS-STU I9a. The ``review_status`` of the installed version
    #: (``app_versions``), or None for a draft. An org need reads it.
    review_status: str | None = None


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


def host_of(manifest: Any) -> str | None:
    """The host package id of a sub-app, or None (§8.2, PKG-15a).

    None means the manifest has no ``extends``, or its ``extends`` is not a
    valid package id. :func:`needs_met` treats the second case as unmet.
    """
    if not isinstance(manifest, Mapping):
        return None
    host = manifest.get("extends")
    if isinstance(host, str) and PACKAGE_ID.fullmatch(host):
        return host
    return None


def host_met(manifest: Mapping[str, Any], packages: frozenset[str] | set[str]) -> bool:
    """True when the ``extends`` of *manifest* is absent or its host is present.

    A null ``extends`` means no host. Any other value that is not a valid
    package id fails closed. The sub-app is never its own host.
    """
    if manifest.get("extends") is None:
        return True
    host = host_of(manifest)
    return host is not None and host != manifest_id(manifest) and host in packages


def connection_names(manifest: Any) -> tuple[str, ...] | None:
    """The names in the ``connections`` of *manifest*, in order (WS-STU I1a).

    No key, or a manifest that is not an object, gives ``()``. An item is a
    text, or an object with a text ``name`` (I9a). A value that is not a list
    of these gives None, and each caller treats None as unmet. Pure, so IN-10
    ("used by") can read the same names."""
    if not isinstance(manifest, Mapping) or "connections" not in manifest:
        return ()
    items = manifest["connections"]
    if not isinstance(items, list):
        return None
    found = [connection_item(i) for i in items]
    if any(f is None for f in found):
        return None
    return tuple(f[0] for f in found if f is not None)


class Met(NamedTuple):
    """The connections that a viewer may use (WS-STU I9a): ``org`` holds each
    provider with a ``connected`` org row, and ``member`` each provider with a
    ``connected`` row of the viewer. The two never mix."""

    org: frozenset[str] = frozenset()
    member: frozenset[str] = frozenset()


def org_install_approved(scope_kind: str | None, review_status: str | None) -> bool:
    """True when an install is an ORG install of an ``approved`` version
    (WS-STU I9a, J2b-1). THE one test that an admin approved the version that
    runs. A private install, a group install, a draft and a preview give
    False, so an ``org`` need fails closed there until I9b."""
    return scope_kind == "org" and review_status == "approved"


def first_unmet_connection(manifest: Any, met: Met, org_approved: bool = False,
                           ) -> tuple[str | None, str | None] | None:
    """The first connection need of *manifest* that is not met, as
    ``(provider, as)``, or None when each is met (WS-STU I9a).

    THE one rule of a connection need. A ``member`` need is met only by a
    member row of the viewer (``met.member``), never by an org row. An ``org``
    need is met only by an org row (``met.org``) AND when *org_approved*
    (:func:`org_install_approved`). A ``connections`` value of a wrong shape
    gives ``(None, None)``: it fails closed with no name. Pure."""
    items = manifest.get("connections", []) if isinstance(manifest, Mapping) else []
    if not isinstance(items, list):
        return None, None
    for raw in items:
        item = connection_item(raw)
        if item is None:
            return None, None
        name, whose = item
        if whose == "member":
            if name not in met.member:
                return name, whose
        elif not (org_approved and name in met.org):
            return name, whose
    return None


def connections_met(manifest: Any, met: Met, org_approved: bool = False) -> bool:
    """True when each connection that *manifest* names is met (WS-STU I1a,
    I9a): :func:`first_unmet_connection` finds none. :func:`needs_met` and the
    action door (``routes/studio/invoke.py``) both call it. *met* is the answer
    of :func:`met_connections` for the viewer."""
    return first_unmet_connection(manifest, met, org_approved) is None


def needs_met(manifest: Any, can_use: CanUse, packages: frozenset[str] | set[str],
              met: Met | None = None, org_approved: bool = False) -> bool:
    """True when each need of *manifest* is met (§5.2 step 4).

    A manifest that is not an object, a ``needs`` that is not a list, and a
    need of any other shape are unmet. A need is an object with one key,
    ``feature`` or ``package``, and a text value that is not empty. The
    ``extends`` of the manifest adds one package need (:func:`host_met`).
    WS-STU I1a: when *met* is a set, each name of ``connections`` adds one
    connection need, and *met* holds the connections of the viewer
    (:func:`connections_met`). *org_approved* is :func:`org_install_approved`
    of the install (I9a). None, the default, is the gate off:
    ``connections`` then makes no need, as before I1a.
    """
    if not isinstance(manifest, Mapping):
        return False
    if not host_met(manifest, packages):
        return False
    if met is not None and not connections_met(manifest, met, org_approved):
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


def system_package_ids(
    system: Mapping[str, Sequence[SystemContribution]], can_use: CanUse,
) -> frozenset[str]:
    """The id of each system package that the viewer can use (§8.2).

    A system package counts only when at least one of its entries passes
    :func:`feature_ok`, the gate that hides its entries. So a package whose
    entries the viewer cannot see is not present. A package whose entries
    name no feature is present for each viewer.
    """
    return frozenset(c.package for entries in system.values() for c in entries
                     if feature_ok(c.feature, can_use))


def resolve(
    system: Mapping[str, Sequence[SystemContribution]],
    installs: Iterable[InstallRow],
    claims: Iterable[ClaimRow],
    can_use: CanUse,
    met: Met | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """The entries of each point for one viewer, in order (§5.2 steps 1 to 4).

    *installs* are the installs that the viewer sees. *claims* are their
    claims. *can_use* is ``user.can_use_feature`` of the viewer. *met* holds
    the connections of the viewer (WS-STU I1a, :func:`met_connections`), or
    None while the gate is off for the org (:func:`needs_met`).
    """
    kept = widest_per_app(installs)
    by_id = {i.id: i for i in kept}
    packages = system_package_ids(system, can_use) | {
        mid for i in kept if (mid := manifest_id(i.manifest)) is not None}
    setup = {i.id: not needs_met(i.manifest, can_use, packages, met,
                                 org_install_approved(i.scope_kind, i.review_status))
             for i in kept}

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
        host = host_of(inst.manifest)
        # ``extends`` is reserved. Only the manifest of the install sets it.
        payload = {k: v for k, v in c.payload.items() if k != "extends"}
        out.setdefault(c.point, []).append({
            **payload, "key": c.key, "source": inst.scope_kind,
            "install_id": inst.id, "needs_setup": setup[inst.id],
            **({"extends": host} if host else {})})
    return out


def version_of(contributions: Mapping[str, Any]) -> str:
    """The first 16 hex digits of the SHA-256 of the canonical JSON."""
    canonical = json.dumps(contributions, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:VERSION_HEX]


def manifest_body(contributions: Mapping[str, Any]) -> dict[str, Any]:
    """The answer of ``GET /shell/manifest``: the version and the entries."""
    return {"version": version_of(contributions), "contributions": dict(contributions)}


def install_feature_slugs(resolved: Mapping[str, Any]) -> list[str]:
    """The ``payload.feature`` slugs of the install claims in *resolved*
    (WS-PKG PKG-7, ``package_host.md`` §10).

    *resolved* is the answer of :func:`resolve_for`. ``/auth/me`` adds these
    slugs to the system slugs, and the access set of the member then puts
    each slug in ``features`` or in ``features_denied``. So this list grants
    nothing.

    Each slug shows once, in the order of the points and their entries. A
    system entry adds nothing, because its feature is a system slug. A slug
    that is not text is dropped. A slug is also dropped when
    ``feature:<slug>`` fails ``validate_permission``, when the check changes
    it (upper case or a space), or when it is a wildcard or holds a colon.
    So a claim cannot widen the vocabulary of permissions.
    """
    from acb_auth.permissions import (
        InvalidPermission,
        feature_permission,
        validate_permission,
    )

    out: list[str] = []
    points = resolved.get("contributions") if isinstance(resolved, Mapping) else None
    if not isinstance(points, Mapping):
        return out
    for entries in points.values():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, Mapping) or entry.get("source") == "system":
                continue
            slug = entry.get("feature")
            if not isinstance(slug, str) or not slug or slug in out:
                continue
            if "*" in slug or ":" in slug:
                continue
            perm = feature_permission(slug)
            try:
                if validate_permission(perm) != perm:
                    continue
            except InvalidPermission:
                continue
            out.append(slug)
    return out


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
    " CASE WHEN package_install.version IS NULL THEN a.manifest ELSE v.manifest END,"
    " package_install.version, v.review_status"
    " FROM package_install"
    " JOIN apps AS a ON a.organization_id = package_install.organization_id"
    " AND a.id = package_install.app_id"
    " LEFT JOIN app_versions AS v ON v.organization_id = package_install.organization_id"
    " AND v.app_id = package_install.app_id AND v.version = package_install.version"
    f" WHERE package_install.organization_id = {_BOUND_ORG}"
    " AND package_install.state = 'active'"
    " AND (package_install.ends_at IS NULL OR package_install.ends_at > now())"
    " AND a.format = 'manifest' AND a.status <> 'archived' AND a.paused_at IS NULL"
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

#: The bound org of the session, as the text of its uuid, or NULL (WS-STU I1a).
BOUND_ORG_SQL = f"SELECT CAST({_BOUND_ORG} AS text)"

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
                       scope_subject=str(r[3]), created_at=r[4], manifest=r[5],
                       version=None if r[6] is None else int(r[6]),
                       review_status=None if r[7] is None else str(r[7]))
            for r in rows]


async def read_claims(db: Any, install_ids: Sequence[str]) -> list[ClaimRow]:
    """The claims of *install_ids*, in the bound org."""
    if not install_ids:
        return []
    rows = (await db.execute(text(CLAIMS_SQL), {"ids": list(install_ids)})).all()
    return [ClaimRow(install_id=str(r[0]), point=str(r[1]), key=str(r[2]),
                     payload=r[3], position=int(r[4])) for r in rows]


async def visible_installs(db: Any, *, viewer: str) -> list[InstallRow]:
    """The one install of each app that *viewer* sees (step 2 and the rule of
    one app). :func:`resolve_for` and the viewer read of ``viewer.py`` (WS-STU
    J2a) both call it, so one rule decides which installs a member sees.

    *db* is a session that the caller opened with ``tenant_session()``.
    *viewer* is the address of the session member, never request input.
    """
    # A lazy import: the CRM package is large, and the gateway loads it first.
    from gateway.routes.crm.access import my_groups

    member = (viewer or "").strip().lower()
    groups = sorted(await my_groups(db, member)) if member else []
    return widest_per_app(await read_installs(db, member, groups))


async def install_seen_by(db: Any, *, viewer: str, install_id: str) -> InstallRow | None:
    """The install *install_id* when *viewer* sees it now, else None (WS-STU
    B6a, ``ai_studio.md`` B6a).

    The member of a run with no viewer reads through ONE install: the install
    of the run. So this read keeps the install of that id, and never the
    widest install of the app (:func:`widest_per_app`). The rule of who sees
    an install is :data:`INSTALLS_SQL` with the groups of
    ``routes/crm/access.my_groups``, as :func:`visible_installs` reads it. So
    a paused, an archived or an expired install, a paused or an archived app,
    and a group that the member left each give None. The row holds the
    manifest of the version that the install pins.

    *db* is a session that the caller opened with ``tenant_session(org)``.
    *viewer* is the member that ``package_install.run_as`` names, never
    request input. A viewer that is not an address reads nothing.
    """
    from gateway.routes.crm.access import my_groups

    member = (viewer or "").strip().lower()
    if "@" not in member:
        return None
    groups = sorted(await my_groups(db, member))
    for row in await read_installs(db, member, groups):
        if row.id == install_id:
            return row
    return None


async def met_connections(db: Any, *, viewer: str) -> Met:
    """The connections that *viewer* may use now, in the org that *db* binds
    (WS-STU I1a): THE one read of a connection need.

    It reads the bound org of the session, never an org of the caller, and
    asks ``connections/store.met_providers``, the one reader of the registry.
    So the met set and the installs come from one org. A session with no
    bound org gives an empty :class:`Met`, so each connection need fails closed.
    I9a: the answer keeps the org rows and the viewer's own rows apart.

    *db* is a session that the caller opened with ``tenant_session()``.
    *viewer* is the session member, or the ``run_as`` member of an install,
    never request input. The resolve and the action door call it only for a
    manifest that names a connection, so an app with none costs no read."""
    from gateway.connections import store as connections

    org = (await db.execute(text(BOUND_ORG_SQL))).scalar()
    if not org:
        return Met()
    org_met, member_met = await connections.met_providers(db, str(org), viewer)
    return Met(org=org_met, member=member_met)


async def resolve_for(db: Any, *, viewer: str, can_use: CanUse) -> dict[str, Any]:
    """The answer of ``GET /shell/manifest`` for *viewer*.

    *db* is a session that the caller opened with ``tenant_session()``.
    *viewer* is the address of the session member, never request input.
    *can_use* is ``user.can_use_feature`` of that member.

    WS-STU I1a: while ``STUDIO_CONNECTION_GATE_ORGS`` holds the bound tenant,
    it reads the connections of the viewer ONCE, and only when a kept install
    names one. ``/auth/me`` calls this function, so an install with no
    connection costs no statement more. With the org off the list, nothing
    reads the registry and ``connections`` makes no need.
    """
    kept = await visible_installs(db, viewer=viewer)
    claims = await read_claims(db, [i.id for i in kept])
    met: Met | None = None
    if connection_gate_on(current_tenant()):
        met = Met()
        if any(connection_names(i.manifest) for i in kept):
            met = await met_connections(db, viewer=viewer)
    return manifest_body(resolve(system_contributions(), kept, claims, can_use, met))
