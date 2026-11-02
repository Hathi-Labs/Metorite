"""The one writer of installs and their claims (WS-PKG PKG-5b part 1).

Spec: ``project-docs/specs/package_host.md`` §4 (the claim rules), §5.1 (the
two tables and their five decisions) and the PKG-5b row of §10. Migration:
the file that creates ``package_install`` (324 at merge).
``tests/unit/test_package_host_store_r8.py`` runs this SQL on a real Postgres,
and ``tests/unit/test_package_host_store.py`` holds the checks with no
database.

**The one writer.** :func:`install` holds the only ``INSERT INTO
package_install`` and the only ``INSERT INTO package_contribution`` of
``apps/`` and ``packages/``. The hermetic suite scans for a second one. The
member purge deletes rows, and that stays legal.

**Tenancy (R5).** :func:`install` takes a session that the caller opened with
``tenant_session(org)``. It opens no session and commits nothing. No function
takes an org. An INSERT writes no ``organization_id``, because the column
DEFAULT reads the bound tenant. Each read names the bound org beside RLS.

**The member.** ``installed_by`` is the address of the session member. The
caller takes it from the authenticated ``UserContext``, never from request
input (contract R11). The store folds it to lower case.

**What the store checks, and what it does not.** The store checks only that
the app is a row of ``apps`` in the bound org (RLS), that it is a Studio
package (``format = 'manifest'``), and that it is not archived. It reads that
row and never writes ``apps``, because ``studio/packages_store.py`` owns it.
⚠️ The store does not check that the member may use the app, or may install
at the scope. The install routes own that check: PKG-8 for Studio apps and
PKG-12 for apps made in chat.

**The scope (D12).** ``scope_subject`` goes through
:func:`acb_auth.permissions.normalize_grant_subject`. The store takes the
kind from the NORMALISED value: ``org``, a ``group:`` prefix, or else an
address. A ``scope_kind`` that does not agree raises :class:`ScopeRefused`.
So ``GROUP:x@y.test``, asked for as private, never reaches the CHECK
``package_install_scope_subject``, where it would be a 500. A private
install belongs to ``installed_by``, and only a private install runs the
working draft (``version`` None).

**The claims (§4).** A claim on ``triggers`` or ``slot`` never conflicts.
Two claims of installs of the SAME app never conflict either, at any scope.
The resolve of part 2 shows one of them (§5.2). Each other claim conflicts
with a key that a system package holds
(:func:`acb_common.app_manifests.shell_manifest.load_contributes`), and with
an existing claim of the same point and key whose scope overlaps:

* a new ``org`` claim overlaps every claim;
* a new ``group:g`` claim overlaps the org claims, the claims of group g, and
  the private claims of each member of g;
* a new private claim of u overlaps the org claims, the private claims of u,
  and the claims of each group of u.

Overlap is one relation, so the store reads it from both sides: a private
claim of u and a claim of a group of u conflict in either order of install.
Each existing claim counts, also one of a paused or archived install, until
the disposal of PKG-9 deletes it. A conflict raises :class:`ClaimTaken`
with the point, the key and the holder. ⚠️ A route sends only
:meth:`InstallError.detail_for` of the session member, because the holder
can be the private install of another member.

**The groups.** A member of a group is an ACTIVE member. That is the rule of
the one group query, ``routes/projects/core._MY_GROUPS_SQL``. The store
reuses that query, and adds the bound org. The remove route keeps the rows
of ``org_group_member`` of a removed member (``remove_member_rows`` of
``routes/admin/members.py``). So the store counts that member in no group,
as each other reader does.

**One lock for each org.** The first statement after the read of the bound
org takes ``pg_advisory_xact_lock`` on ``package_install:<org>``. Each read
that decides the write runs after it, so two installs of one org run one at
a time. Two claims of one key then give one winner and one
:class:`ClaimTaken`. The lock ends with the caller's transaction. A caller
that also locks the row of the app in ``apps`` in the same transaction calls
:func:`install` first, so the org lock always comes first (advisory: no fence
holds this order yet).

**READ COMMITTED.** The conflict reads rely on READ COMMITTED, so the
snapshot of each read comes after the lock. Under REPEATABLE READ a second
install would miss the first one's commit. So the store reads the level with
the bound org, and it refuses another level with :class:`RuntimeError`
before it takes the lock.

**Before the database.** Each check of the arguments runs before the first
statement: the address, the scope, the version, the end date, and each
claim. So no value reaches a statement that a CHECK or a column refuses. A
payload is at most :data:`MAX_PAYLOAD_BYTES` bytes of jsonb text, as the
CHECK ``package_contribution_payload_size`` counts them. The value check of
a payload is :func:`gateway.studio.packages_store.first_bad_value`, the one
copy. The store adds only the depth cap, because no manifest check runs.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from functools import cache
from typing import Any

from acb_auth.permissions import normalize_grant_subject
from acb_common.app_manifests.shell_manifest import SystemContribution, load_contributes
from acb_common.db import TenantUnbound
from acb_common.studio_manifest import CONTRIBUTION_POINTS
from sqlalchemy import text

from gateway.routes.projects.core import _MY_GROUPS_SQL
from gateway.studio.packages_store import first_bad_value

#: The points where many claims of one key are allowed (§4). PKG-14 adds the
#: limit that a host declares for a slot.
MANY_CLAIMS: frozenset[str] = frozenset({"triggers", "slot"})
#: The bound of the CHECK ``package_contribution_payload_size``, in bytes.
MAX_PAYLOAD_BYTES = 65536
#: The deepest payload. Postgres and ``json.dumps`` both stop at some depth.
MAX_PAYLOAD_DEPTH = 64
#: The longest key and scope text, in UTF-8 bytes. Each sits in a btree key,
#: and a btree refuses a key over about 2700 bytes.
MAX_KEY_BYTES = 1024
MAX_SUBJECT_BYTES = 320
#: The isolation level that the conflict reads need (see the docstring).
ISOLATION = "read committed"
#: The range of an ``int`` column.
_INT4_MAX = 2**31 - 1

_BOUND_ORG = "CAST(NULLIF(current_setting('app.tenant_id', true), '') AS uuid)"

#: The bound org, as the canonical text of its uuid, and the isolation level
#: of the transaction. The org is NULL when no tenant is bound.
_ORG_SQL = f"SELECT CAST({_BOUND_ORG} AS text), current_setting('transaction_isolation')"

#: One install at a time for each org, until the transaction ends. The idiom
#: of ``connections/github.py`` and ``routes/crm/convert_mapping.py``.
_LOCK_SQL = "SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"

#: The app is a Studio package of the bound org, and it is not archived.
_APP_SQL = (
    "SELECT 1 FROM apps "
    f"WHERE organization_id = {_BOUND_ORG} AND id = CAST(:app AS uuid) "
    "AND format = 'manifest' AND status <> 'archived'"
)

#: One app has one install at each scope (``package_install_scope_key``).
_SCOPE_TAKEN_SQL = (
    "SELECT 1 FROM package_install "
    f"WHERE organization_id = {_BOUND_ORG} AND app_id = CAST(:app AS uuid) "
    "AND scope_kind = :kind AND scope_subject = :subject"
)

if _MY_GROUPS_SQL.count(":email") != 1:  # pragma: no cover — a guard
    raise RuntimeError(
        "routes/projects/core._MY_GROUPS_SQL changed shape. It must bind the "
        "member's email once as :email."
    )


def _groups_of(member: str) -> str:
    """The ``group:<slug>`` subjects of the ACTIVE member *member*.

    *member* is an SQL expression of a lower-case address. The body is the
    one group query, ``routes/projects/core._MY_GROUPS_SQL``, so the store
    keeps no copy of its rule (``au.status = 'active'``). The store adds the
    bound org beside RLS. ``_clean_slug`` of ``routes/admin/groups.py``, the
    one writer of a slug, writes it in lower case. So a subject of the query
    matches a normalised scope.
    """
    body = _MY_GROUPS_SQL.replace(":email", member).strip()
    return f"{body} AND g.organization_id = {_BOUND_ORG}"


#: The groups of the member of the new install (``:subject``).
_GROUPS_OF_MEMBER = _groups_of(":subject")
#: The groups of the owner of an existing private install ``i``.
_GROUPS_OF_HOLDER = _groups_of("i.scope_subject")

#: The existing installs whose scope overlaps a new install of each kind (§4).
OVERLAP: dict[str, str] = {
    "org": "TRUE",
    "group": (
        "(i.scope_kind = 'org'"
        " OR (i.scope_kind = 'group' AND i.scope_subject = :subject)"
        f" OR (i.scope_kind = 'private' AND :subject IN ({_GROUPS_OF_HOLDER})))"
    ),
    "private": (
        "(i.scope_kind = 'org'"
        " OR (i.scope_kind = 'private' AND i.scope_subject = :subject)"
        f" OR (i.scope_kind = 'group' AND i.scope_subject IN ({_GROUPS_OF_MEMBER})))"
    ),
}

#: A claim of an install of the same app is never a conflict, at any scope.
SAME_APP_EXEMPT = "i.app_id <> CAST(:app AS uuid)"

#: The first existing claim, in the order of the request, that a new claim of
#: the scope conflicts with. ``{overlap}`` is one value of :data:`OVERLAP`.
_HELD_SQL = (
    "SELECT c.point, c.key, CAST(i.id AS text), CAST(i.app_id AS text),"
    " i.scope_kind, i.scope_subject"
    " FROM unnest(CAST(:points AS text[]), CAST(:keys AS text[]))"
    " WITH ORDINALITY AS w (point, key, ord)"
    " JOIN package_contribution AS c ON c.point = w.point AND c.key = w.key"
    " JOIN package_install AS i"
    " ON i.organization_id = c.organization_id AND i.id = c.install_id"
    f" WHERE c.organization_id = {_BOUND_ORG} AND {SAME_APP_EXEMPT} AND {{overlap}}"
    " ORDER BY w.ord, i.created_at, i.id LIMIT 1"
)

_INSERT_INSTALL_SQL = (
    "INSERT INTO package_install (app_id, version, scope_kind, scope_subject, "
    "installed_by, ends_at) VALUES (CAST(:app AS uuid), :version, :kind, :subject, "
    ":by, :ends_at) RETURNING CAST(id AS text)"
)

_INSERT_CONTRIBUTION_SQL = (
    "INSERT INTO package_contribution (install_id, point, key, payload, position) "
    "VALUES (CAST(:install AS uuid), :point, :key, CAST(:payload AS jsonb), :position)"
)


# ── The types ───────────────────────────────────────────────────────────────


class InstallError(ValueError):
    """An install that the store refuses. Nothing is written.

    ``code`` names the refusal. ``status`` is the HTTP status that a route
    answers for it. No message names an address of another member.

    ⚠️ A route answers with :meth:`detail_for` of the session member, and with
    nothing else. It never sends an attribute of the error, such as
    ``ClaimTaken.holder``. That attribute can name the address and the app of
    the private install of another member.
    """

    status = 422

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code

    def detail_for(self, viewer: str) -> dict[str, Any]:
        """The body of the refusal that the member *viewer* may see."""
        return {"code": self.code, "message": str(self)}


class ScopeRefused(InstallError):
    """The scope does not agree with the request or the member (422)."""


class ContributionRefused(InstallError):
    """A claim that a column or a CHECK of ``package_contribution`` refuses (422)."""


class AppNotInstallable(InstallError):
    """No Studio package of the bound org has the id, or it is archived (404)."""

    status = 404


class AlreadyInstalled(InstallError):
    """The app has an install at this scope already (409)."""

    status = 409


@dataclass(frozen=True)
class Holder:
    """The holder of a key that a new claim wants.

    ``source`` is ``system``, ``org``, ``group`` or ``private``. ``package`` is
    the id of the system package, or the app id of the install.
    ``install_id`` and ``scope_subject`` are None for a system package.
    ⚠️ A route never sends this value. It sends :meth:`ClaimTaken.detail_for`.
    """

    source: str
    package: str
    install_id: str | None = None
    scope_subject: str | None = None


class ClaimTaken(InstallError):
    """A claim conflicts with a key that another holder has (409, §4).

    ``holder`` is the whole holder, for the store and its tests. A route
    sends :meth:`detail_for`, which hides the private install of another
    member. The message never names the app of a private install.
    """

    status = 409

    def __init__(self, point: str, key: str, holder: Holder) -> None:
        taker = ("a private install" if holder.source == "private"
                 else f"the {holder.source} package {holder.package}")
        super().__init__("claim_taken", f"The {point} key {key!r} is taken by {taker}.")
        self.point = point
        self.key = key
        self.holder = holder

    def holder_for(self, viewer: str) -> dict[str, str | None]:
        """The holder as the member *viewer* may see it.

        The private install of another member shows as ``{"source":
        "private"}`` only, with no address, no app id and no install id.
        ``get_app_or_404`` hides a private app behind a 404 for the same
        reason. The private install of *viewer*, and each other holder, shows
        whole.
        """
        h = self.holder
        me = viewer.strip().lower() if isinstance(viewer, str) else ""
        if h.source == "private" and (not me or h.scope_subject != me):
            return {"source": "private"}
        return {"source": h.source, "package": h.package, "install_id": h.install_id,
                "scope_subject": h.scope_subject}

    def detail_for(self, viewer: str) -> dict[str, Any]:
        """The body of the 409 for *viewer*: the point, the key and the holder."""
        return {**super().detail_for(viewer), "point": self.point, "key": self.key,
                "holder": self.holder_for(viewer)}


@dataclass(frozen=True)
class Contribution:
    """One claim of an install at one point (§4).

    ``key`` is the key that the conflict rule of the point names: an href, a
    job id, a card key. ``payload`` is the rest of the entry.
    """

    point: str
    key: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    position: int = 0


@dataclass(frozen=True)
class Install:
    """One install that :func:`install` wrote."""

    id: str
    app_id: str
    version: int | None
    scope_kind: str
    scope_subject: str
    installed_by: str
    ends_at: datetime | None
    contributions: tuple[Contribution, ...]


# ── The checks with no database ─────────────────────────────────────────────


def _plain(value: object, *, limit: int) -> bool:
    """True for text that a ``text`` column and a btree key take."""
    if not isinstance(value, str) or not value or "\x00" in value:
        return False
    try:
        return len(value.encode("utf-8")) <= limit
    except UnicodeEncodeError:
        return False


def _member(installed_by: object) -> str:
    """The session member as the store writes ``installed_by``."""
    refused = ScopeRefused("bad_member", "installed_by must be the address of the session member.")
    if not _plain(installed_by, limit=MAX_SUBJECT_BYTES):
        raise refused
    try:
        member = normalize_grant_subject(str(installed_by))
    except ValueError as exc:
        raise refused from exc
    if member == "org" or member.startswith("group:"):
        raise refused
    return member


def _kind_of(subject: str) -> str:
    """The kind of a NORMALISED D12 subject."""
    if subject == "org":
        return "org"
    if subject.startswith("group:"):
        return "group"
    return "private"


def _scope(scope_subject: object, scope_kind: object, member: str,
           version: int | None) -> tuple[str, str]:
    """The kind and the subject of the install, or :class:`ScopeRefused`."""
    if not _plain(scope_subject, limit=MAX_SUBJECT_BYTES):
        raise ScopeRefused("bad_scope", "The scope must be org, group:<slug> or an address.")
    try:
        subject = normalize_grant_subject(str(scope_subject))
    except ValueError as exc:
        raise ScopeRefused(
            "bad_scope", "The scope must be org, group:<slug> or an address.") from exc
    kind = _kind_of(subject)
    if scope_kind != kind:
        raise ScopeRefused(
            "scope_kind_mismatch",
            f"The scope {subject!r} is a {kind} scope. Ask for the {kind} kind.")
    if kind == "private" and subject != member:
        raise ScopeRefused(
            "private_not_owner", "A private install belongs to the member who installs it.")
    if version is None and kind != "private":
        raise ScopeRefused("draft_not_private", "Only a private install may run the draft.")
    return kind, subject


def _version(version: object) -> int | None:
    if version is None:
        return None
    if isinstance(version, bool) or not isinstance(version, int) or not 1 <= version <= _INT4_MAX:
        raise InstallError("bad_version", "The version must be a whole number from 1, or None.")
    return version


def _app_id(app_id: object) -> str:
    try:
        return str(uuid.UUID(str(app_id)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise InstallError("bad_app_id", "The app id must be a UUID.") from exc


def _ends_at(ends_at: object) -> datetime | None:
    if ends_at is None:
        return None
    if not isinstance(ends_at, datetime) or ends_at.tzinfo is None:
        raise InstallError("bad_ends_at", "The end date must be a datetime with a time zone.")
    return ends_at


def _too_deep(payload: object) -> bool:
    """True when *payload* nests deeper than :data:`MAX_PAYLOAD_DEPTH`.

    Iterative, so a deep payload never meets the recursion limit. The value
    check (:func:`first_bad_value`) has no depth cap of its own.
    """
    stack: list[tuple[object, int]] = [(payload, 0)]
    while stack:
        node, depth = stack.pop()
        if depth > MAX_PAYLOAD_DEPTH:
            return True
        if isinstance(node, Mapping):
            stack.extend((v, depth + 1) for v in node.values())
        elif isinstance(node, list | tuple):
            stack.extend((v, depth + 1) for v in node)
    return False


def _payload_text(payload: object) -> str:
    """The JSON text of a payload, with the length of its jsonb text.

    The separators are those of the jsonb text form. With the checks of
    :func:`first_bad_value`, the byte count is the count of the CHECK.
    """
    if not isinstance(payload, Mapping):
        raise ContributionRefused("bad_payload", "The payload must be a JSON object.")
    if _too_deep(payload):
        raise ContributionRefused(
            "bad_payload", f"The payload is deeper than {MAX_PAYLOAD_DEPTH} levels.")
    doc = dict(payload)
    bad = first_bad_value(doc)
    if bad is not None:
        raise ContributionRefused("bad_payload", bad.message)
    try:
        encoded = json.dumps(doc, ensure_ascii=False, allow_nan=False)
        size = len(encoded.encode("utf-8"))
    except (TypeError, ValueError, RecursionError) as exc:
        # UnicodeEncodeError (a lone surrogate) is a ValueError.
        raise ContributionRefused("bad_payload", "The payload must be plain JSON.") from exc
    if size > MAX_PAYLOAD_BYTES:
        raise ContributionRefused(
            "payload_too_large", f"The payload is larger than {MAX_PAYLOAD_BYTES} bytes.")
    return encoded


def _claims(contributions: object) -> list[tuple[Contribution, str]]:
    """Each claim with its payload text, or :class:`ContributionRefused`."""
    if not isinstance(contributions, list | tuple):
        raise ContributionRefused("bad_contributions", "The contributions must be a list.")
    out: list[tuple[Contribution, str]] = []
    seen: set[tuple[str, str]] = set()
    for c in contributions:
        if not isinstance(c, Contribution):
            raise ContributionRefused("bad_contributions", "Each claim must be a Contribution.")
        if c.point not in CONTRIBUTION_POINTS:
            raise ContributionRefused("bad_point", "The point is not a point of the spec.")
        if not _plain(c.key, limit=MAX_KEY_BYTES):
            raise ContributionRefused(
                "bad_key", f"A key must be text of 1 to {MAX_KEY_BYTES} bytes, with no NUL.")
        pos = c.position
        if isinstance(pos, bool) or not isinstance(pos, int) or abs(pos) > _INT4_MAX:
            raise ContributionRefused("bad_position", "The position must be a whole number.")
        if (c.point, c.key) in seen:
            raise ContributionRefused("duplicate_claim", "The request claims one key twice.")
        seen.add((c.point, c.key))
        out.append((c, _payload_text(c.payload)))
    return out


@cache
def _system_keys() -> dict[tuple[str, str], SystemContribution]:
    """Each ``(point, key)`` that a system package holds. The files are fixed."""
    return {(c.point, c.key): c for entries in load_contributes().values() for c in entries}


def _check_system(claims: Sequence[Contribution]) -> None:
    """Any claim conflicts with a key that a system package holds (§4)."""
    held = _system_keys()
    for c in claims:
        if c.point in MANY_CLAIMS:
            continue
        system = held.get((c.point, c.key))
        if system is not None:
            raise ClaimTaken(c.point, c.key, Holder(source="system", package=system.package))


async def _check_held(db: Any, kind: str, subject: str, app: str,
                      claims: Sequence[Contribution]) -> None:
    """A claim conflicts with an existing claim of another app whose scope
    overlaps (§4)."""
    wanted = [c for c in claims if c.point not in MANY_CLAIMS]
    if not wanted:
        return
    row = (await db.execute(text(_HELD_SQL.format(overlap=OVERLAP[kind])), {
        "points": [c.point for c in wanted], "keys": [c.key for c in wanted],
        "subject": subject, "app": app,
    })).first()
    if row is not None:
        raise ClaimTaken(str(row[0]), str(row[1]), Holder(
            source=str(row[4]), package=str(row[3]), install_id=str(row[2]),
            scope_subject=str(row[5])))


# ── The writer ──────────────────────────────────────────────────────────────


async def install(
    db: Any,
    *,
    app_id: str,
    version: int | None,
    scope_subject: str,
    scope_kind: str,
    installed_by: str,
    ends_at: datetime | None,
    contributions: Sequence[Contribution],
) -> Install:
    """Install *version* of the app *app_id* at one scope, with its claims.

    *db* is a session that the caller opened with ``tenant_session(org)``.
    *installed_by* is the address of the session member. *version* None is
    the owner's working draft. Each refusal raises an :class:`InstallError`
    and writes nothing. The caller's transaction commits the rows.
    """
    member = _member(installed_by)
    ver = _version(version)
    kind, subject = _scope(scope_subject, scope_kind, member, ver)
    app = _app_id(app_id)
    ends = _ends_at(ends_at)
    claims = _claims(contributions)
    bound = (await db.execute(text(_ORG_SQL))).first()
    org, level = (bound[0], bound[1]) if bound is not None else (None, None)
    if not org:
        raise TenantUnbound("an install needs a session bound to a tenant")
    if level != ISOLATION:
        raise RuntimeError(
            f"an install needs a {ISOLATION} transaction, and this one is {level}")
    # The lock comes first. Each read below decides the write.
    await db.execute(text(_LOCK_SQL), {"key": f"package_install:{org}"})
    if (await db.execute(text(_APP_SQL), {"app": app})).first() is None:
        raise AppNotInstallable(
            "app_not_installable", "No Studio package that is not archived has this id.")
    if (await db.execute(text(_SCOPE_TAKEN_SQL), {
            "app": app, "kind": kind, "subject": subject})).first() is not None:
        raise AlreadyInstalled("already_installed", "The app has an install at this scope.")
    wanted = [c for c, _ in claims]
    _check_system(wanted)
    await _check_held(db, kind, subject, app, wanted)
    install_id = str((await db.execute(text(_INSERT_INSTALL_SQL), {
        "app": app, "version": ver, "kind": kind, "subject": subject, "by": member,
        "ends_at": ends,
    })).scalar_one())
    if claims:
        await db.execute(text(_INSERT_CONTRIBUTION_SQL), [
            {"install": install_id, "point": c.point, "key": c.key, "payload": payload,
             "position": c.position}
            for c, payload in claims])
    return Install(
        id=install_id, app_id=app, version=ver, scope_kind=kind, scope_subject=subject,
        installed_by=member, ends_at=ends, contributions=tuple(wanted))
