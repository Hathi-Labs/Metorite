"""The one writer of installs and their claims (WS-PKG PKG-5b part 1).

Spec: ``project-docs/specs/package_host.md`` §4 (the claim rules), §5.1 (the
two tables and their five decisions) and the PKG-5b row of §10. Migration:
the file that creates ``package_install`` (324 at merge).
``tests/unit/test_package_host_store_r8.py`` runs this SQL on a real Postgres,
and ``tests/unit/test_package_host_store.py`` holds the checks with no
database.

**The one writer.** :func:`install` holds the only ``INSERT INTO
package_install`` and the only ``INSERT INTO package_contribution`` of
``apps/`` and ``packages/``. Every UPDATE and DELETE of the two tables is in
this module too. The disposal of §5.3 (PKG-9) holds the moves of ``state``
and the delete of an install: :func:`remove_install`, :func:`pause_install`
and :func:`resume_install`. :func:`set_version` (PKG-8a) holds the UPDATE of
``version`` and the DELETE of the claims of one install. The lifetime of
§6.2 (PKG-10a) holds the other UPDATE statements: :func:`keep_install`,
:func:`restore_install` and :func:`expire_due`. The hermetic suite scans
``apps/`` and ``packages/`` for a write outside this module. The member
purge deletes rows, and that stays legal.

**The disposal.** Each of the three takes the org lock of :func:`install`
and its two guards, and finds the install in the bound org only. An id of
another org is :class:`InstallNotFound`, as a missing id is. A pause keeps
the claims and their keys. A move from ``archived`` is
:class:`StateRefused`. The routes of PKG-8, PKG-12 and J3 decide who may
call each one.

**The lifetime (§6.2, PKG-10a).** The four lifetime functions take the same
org lock and the same guards. :func:`keep_install` clears ``ends_at``.
:func:`restore_install` makes an archived install active again, until 30
days after ``archived_at``. :func:`expire_due` archives each due install,
then deletes each install that was archived 30 days before. A paused
install never expires (PKG-Q8). :func:`notice_times` gives the times of the
two bell notes and sends nothing. No route, loop or flag calls them yet.

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

* a new ``org`` claim overlaps the org claims and the group claims;
* a new ``group:g`` claim overlaps the org claims and the claims of group g;
* a new private claim of u overlaps the org claims, the private claims of u,
  and the claims of each group of u.

⚠️ **The org wins (PKG-Q6, owner, 2026-11-07, task 1246).** A private claim
never blocks an org or a group install. The org or group install goes in,
and the resolve shows its claim in place of the private claim of each
viewer (§5.2: org, then group, then private). The member still reaches the
private app from the Studio library. A private claim stays refused when an
org claim or a claim of a group of its member holds the key. So the
relation is one-sided on purpose. Each existing claim counts, also one of a
paused or archived install, until the disposal of PKG-9 deletes it. A
conflict raises :class:`ClaimTaken` with the point, the key and the holder.
⚠️ A route sends only :meth:`InstallError.detail_for` of the session member,
because the holder can be the private install of another member.

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
:class:`ClaimTaken`. The lock ends with the caller's transaction.

⚠️ **The lock order (PKG-8a).** The first caller is the Studio publish. It
holds the row of the app in ``apps`` ``FOR UPDATE``, then the org lock of
``objects_store``, and only then calls :func:`set_version` or
:func:`install`. The INSERT of an install checks its FK, and that check
waits for a ``FOR UPDATE`` on the ``apps`` row. So a second caller that does
not hold the row must lock it (``FOR KEY SHARE``) before it calls here.
Else it can hold this lock and wait for the row while a publish holds the
row and waits for this lock. The second caller is the install route of
PKG-8b: it calls ``studio/packages_store.lock_for_install`` first. Fence:
``tests/unit/test_studio_package_install_r8.py`` (the install waits for the
row and does not hold this lock).

**Who may install (PKG-8b).** The store does not decide it. The route reads
``studio:publish`` for an org and for a group install (D180.3), and asks
:func:`group_exists` for a group install (§6.1).

**A new version (PKG-8a).** :func:`set_version` moves an install to a new
version and replaces its claims. It takes the org lock through
:func:`_lock_org`, as each writer does. It writes the new claims with the
INSERT of :func:`install`. ⚠️ It does not run ``_APP_SQL``, because a caller
holds the ``apps`` row of a live package: the publish ``FOR UPDATE``, and
the install route of PKG-8b ``FOR KEY SHARE``.

⚠️ **A kept key stays its own (PKG-8b fix round 1).** :func:`set_version`
checks the overlap of §4 only for a claim whose point and key the install
did not hold before the move. Under PKG-Q6 an org or a group install can
take the key of a private install. That private install must still move,
or its owner could not publish again. Its kept claim stays, and the resolve
shows the wider claim in its place (§5.2). A NEW key meets the full overlap.
Fence: the kept-key cases of ``tests/unit/test_package_host_store_r8.py``.

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
from datetime import datetime, timedelta
from functools import cache
from typing import Any

from acb_auth.permissions import normalize_grant_subject
from acb_common.app_manifests.shell_manifest import SystemContribution, load_contributes
from acb_common.db import TenantUnbound
from acb_common.studio_manifest import CONTRIBUTION_POINTS
from sqlalchemy import text

from gateway.routes.projects.core import _MY_GROUPS_SQL, org_groups
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

#: The existing installs whose scope overlaps a new install of each kind (§4).
#: ⚠️ PKG-Q6 (the org wins): the org and the group rows never name a private
#: install, so a private claim never blocks them. The private row still names
#: the org and the group claims, so a private claim cannot take their key.
OVERLAP: dict[str, str] = {
    "org": "i.scope_kind <> 'private'",
    "group": (
        "(i.scope_kind = 'org'"
        " OR (i.scope_kind = 'group' AND i.scope_subject = :subject))"
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

#: The removal of one install of the bound org (§5.3, PKG-9). The FK of
#: ``package_contribution`` is ``ON DELETE CASCADE``, so its claims go too.
_DELETE_INSTALL_SQL = (
    "DELETE FROM package_install "
    f"WHERE organization_id = {_BOUND_ORG} AND id = CAST(:install AS uuid) RETURNING 1"
)

#: The state of one install of the bound org, with the row lock.
_STATE_SQL = (
    "SELECT state FROM package_install "
    f"WHERE organization_id = {_BOUND_ORG} AND id = CAST(:install AS uuid) FOR UPDATE"
)

#: One state move of one install of the bound org. ``:was`` is the state
#: that the read under the lock found.
_SET_STATE_SQL = (
    "UPDATE package_install SET state = :to, updated_at = now() "
    f"WHERE organization_id = {_BOUND_ORG} AND id = CAST(:install AS uuid) AND state = :was"
)

# ── The lifetime SQL (§6.2, PKG-10a) ────────────────────────────────────────

#: The time that the owner has to restore an archived install (§6.2).
RESTORE_DAYS = timedelta(days=30)
#: The end date of a restored install, after the restore (PKG-Q7 default).
RESTORED_ENDS = timedelta(days=30)
#: The first bell note comes this long before ``ends_at`` (PKG-10c).
NOTICE_BEFORE_END = timedelta(days=3)
#: The second bell note comes this long after ``archived_at`` (PKG-10c).
NOTICE_AFTER_ARCHIVE = RESTORE_DAYS - NOTICE_BEFORE_END
#: The most rows that one step of :func:`expire_due` moves in one call.
EXPIRE_LIMIT = 500

#: The lifetime of one install of the bound org, with the row lock.
_LIFE_SQL = (
    "SELECT state, ends_at, archived_at, CAST(app_id AS text) FROM package_install "
    f"WHERE organization_id = {_BOUND_ORG} AND id = CAST(:install AS uuid) FOR UPDATE"
)

#: A keep of one install that is not archived. The read under the lock
#: found ``ends_at`` set.
_KEEP_SQL = (
    "UPDATE package_install SET ends_at = NULL, updated_at = now() "
    f"WHERE organization_id = {_BOUND_ORG} AND id = CAST(:install AS uuid) "
    "AND state IN ('active', 'paused')"
)

#: A restore of one archived install. One statement sets ``state`` and
#: clears ``archived_at``, because the CHECK ``package_install_archived_at``
#: reads both.
_RESTORE_SQL = (
    "UPDATE package_install SET state = 'active', archived_at = NULL, "
    "ends_at = :ends, updated_at = now() "
    f"WHERE organization_id = {_BOUND_ORG} AND id = CAST(:install AS uuid) "
    "AND state = 'archived'"
)

#: The active installs of the bound org whose end date is at or before
#: ``:now``, oldest first. A paused install is not due (PKG-Q8).
_DUE_SQL = (
    "SELECT CAST(id AS text) FROM package_install "
    f"WHERE organization_id = {_BOUND_ORG} AND state = 'active' "
    "AND ends_at IS NOT NULL AND ends_at <= :now "
    "ORDER BY ends_at, id LIMIT :limit FOR UPDATE"
)

#: The archive of one due install. The guard repeats the rule of
#: :data:`_DUE_SQL`, so the statement never archives a row that changed.
_ARCHIVE_SQL = (
    "UPDATE package_install SET state = 'archived', archived_at = :now, updated_at = now() "
    f"WHERE organization_id = {_BOUND_ORG} AND id = CAST(:install AS uuid) "
    "AND state = 'active' AND ends_at <= :now RETURNING 1"
)

#: The archived installs of the bound org whose restore time has ended,
#: oldest first.
_SPENT_SQL = (
    "SELECT CAST(id AS text) FROM package_install "
    f"WHERE organization_id = {_BOUND_ORG} AND state = 'archived' "
    "AND archived_at <= :cutoff "
    "ORDER BY archived_at, id LIMIT :limit FOR UPDATE"
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


class InstallNotFound(InstallError):
    """No install of the bound org has the id (404, PKG-9).

    An install of another org gives the same answer, so a route never shows
    that the id exists.
    """

    status = 404


class StateRefused(InstallError):
    """A state move that §5.3 does not allow (409, PKG-9).

    A pause and a resume move between ``active`` and ``paused`` only. An
    ``archived`` install stays archived.
    """

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


def _install_id(install_id: object) -> str:
    try:
        return str(uuid.UUID(str(install_id)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise InstallError("bad_install_id", "The install id must be a UUID.") from exc


def _ends_at(ends_at: object) -> datetime | None:
    if ends_at is None:
        return None
    if not isinstance(ends_at, datetime) or ends_at.tzinfo is None:
        raise InstallError("bad_ends_at", "The end date must be a datetime with a time zone.")
    return ends_at


def _now(now: object) -> datetime:
    """The time of a lifetime call. A time with no zone is refused, because
    Postgres would read it in the zone of the session."""
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise InstallError("bad_now", "The time must be a datetime with a time zone.")
    return now


def _limit(limit: object) -> int:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= _INT4_MAX:
        raise InstallError("bad_limit", "The limit must be a whole number from 1.")
    return limit


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


# ── The group of an install (PKG-8b) ────────────────────────────────────────


async def group_exists(db: Any, *, group: str) -> bool:
    """True when *group* names a group of the bound org.

    *db* is a session that the caller opened with ``tenant_session(org)``.
    *group* is a ``group:<slug>`` subject. The read is
    ``routes/projects/core.org_groups``, the group list of the grant routes,
    with the bound org. ``org_group`` has no row security, so the org filter
    of that list is the wall: a group of another org answers False. A value
    that is not a group subject answers False and reads nothing. The store
    takes no lock and writes nothing here. The install route of PKG-8b calls
    it before it locks the ``apps`` row. It does not ask whether the caller
    is a member of the group: D180.3 gates a group install on
    ``studio:publish``, and a holder of it may install for any group.
    """
    if not _plain(group, limit=MAX_SUBJECT_BYTES):
        return False
    try:
        subject = normalize_grant_subject(group)
    except ValueError:
        return False
    if _kind_of(subject) != "group":
        return False
    bound = (await db.execute(text(_ORG_SQL))).first()
    org = bound[0] if bound is not None else None
    if not org:
        raise TenantUnbound("a group read needs a session bound to a tenant")
    slug = subject.removeprefix("group:")
    return any(str(g["slug"]).lower() == slug for g in await org_groups(db, org))


# ── The writer ──────────────────────────────────────────────────────────────


async def _lock_org(db: Any, act: str) -> None:
    """Read the bound org and the isolation level, then take the org lock.

    Each writer calls this first, so each write of one org runs after the
    lock. *act* names the write in the refusal.
    """
    bound = (await db.execute(text(_ORG_SQL))).first()
    org, level = (bound[0], bound[1]) if bound is not None else (None, None)
    if not org:
        raise TenantUnbound(f"{act} needs a session bound to a tenant")
    if level != ISOLATION:
        raise RuntimeError(
            f"{act} needs a {ISOLATION} transaction, and this one is {level}")
    await db.execute(text(_LOCK_SQL), {"key": f"package_install:{org}"})


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
    # The lock comes first. Each read below decides the write.
    await _lock_org(db, "an install")
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


# ── The disposal (§5.3, PKG-9) ──────────────────────────────────────────────


async def remove_install(db: Any, *, install_id: str) -> bool:
    """Delete the install *install_id* of the bound org, with its claims.

    *db* is a session that the caller opened with ``tenant_session(org)``.
    The FK cascade of ``package_contribution`` deletes each claim, so each
    key of the install is free when the caller's transaction commits. The
    FK cascade of ``package_field_value`` deletes each extension value of
    the install too (PKG-13). An install in any state can go. An id that no
    install of the bound org has raises :class:`InstallNotFound` and writes
    nothing. The store does not check that the member may remove the install.
    The routes own that check.
    Answers True.
    """
    install = _install_id(install_id)
    await _lock_org(db, "a removal")
    if (await db.execute(text(_DELETE_INSTALL_SQL), {"install": install})).first() is None:
        raise InstallNotFound("install_not_found", "No install has this id.")
    return True


async def _move_state(db: Any, install: str, was: str, to: str, act: str) -> bool:
    """Move one install from *was* to *to*, under the org lock and the row
    lock. Answers False when the install is in *to* already."""
    await _lock_org(db, act)
    row = (await db.execute(text(_STATE_SQL), {"install": install})).first()
    if row is None:
        raise InstallNotFound("install_not_found", "No install has this id.")
    state = str(row[0])
    if state == to:
        return False
    if state != was:
        raise StateRefused(
            "state_refused", f"An install that is {state} cannot move to {to}.")
    await db.execute(text(_SET_STATE_SQL), {"install": install, "to": to, "was": was})
    return True


async def pause_install(db: Any, *, install_id: str) -> bool:
    """Move the install *install_id* of the bound org from active to paused.

    The install keeps its claims, and each claim keeps its key (§4). The
    resolve shows only an active install, so the claims go from the next
    read. A pause of a paused install changes nothing and answers False. An
    archived install raises :class:`StateRefused` (409) and writes nothing.
    Answers True when the state changed.
    """
    return await _move_state(db, _install_id(install_id), "active", "paused", "a pause")


async def resume_install(db: Any, *, install_id: str) -> bool:
    """Move the install *install_id* of the bound org from paused to active.

    The claims of a paused install still hold their keys, so a resume makes
    no check of the claims. A resume of an active install changes nothing
    and answers False. An archived install raises :class:`StateRefused` (409)
    and writes nothing. Answers True when the state changed.
    """
    return await _move_state(db, _install_id(install_id), "paused", "active", "a resume")


# ── The lifetime (§6.2, PKG-10a) ────────────────────────────────────────────
#
# ``resolve.py`` already hides an install at its ``ends_at`` (§5.2 step 2),
# so a member stops seeing it at that time with no write. The daily job of
# PKG-10b calls :func:`expire_due` only to archive the install and, 30 days
# later, to delete it. No function here sends a bell note (PKG-10c).


async def _life(db: Any, install: str, act: str) -> tuple[str, datetime | None,
                                                          datetime | None, str]:
    """The state, ``ends_at``, ``archived_at`` and app id of one install,
    under the org lock and the row lock, or :class:`InstallNotFound`."""
    await _lock_org(db, act)
    row = (await db.execute(text(_LIFE_SQL), {"install": install})).first()
    if row is None:
        raise InstallNotFound("install_not_found", "No install has this id.")
    return str(row[0]), row[1], row[2], str(row[3])


async def keep_install(db: Any, *, install_id: str) -> bool:
    """Clear the end date of the install *install_id* of the bound org.

    This is the Keep of §6.2 step 2. An active or a paused install can be
    kept. An archived install raises :class:`StateRefused` (409) and writes
    nothing: a restore comes first. A keep of an install with no end date
    changes nothing and answers False. Answers True when ``ends_at`` changed.
    """
    install = _install_id(install_id)
    state, ends_at, _, _ = await _life(db, install, "a keep")
    if state == "archived":
        raise StateRefused(
            "state_refused", "An archived install cannot be kept. Restore it first.")
    if ends_at is None:
        return False
    await db.execute(text(_KEEP_SQL), {"install": install})
    return True


async def restore_install(db: Any, *, install_id: str, now: datetime) -> datetime:
    """Make the archived install *install_id* of the bound org active again.

    *now* is the time of the restore, with a time zone. The restore must come
    before ``archived_at`` plus :data:`RESTORE_DAYS`. Else, and for an install
    that is not archived, it raises :class:`StateRefused` (409) and writes
    nothing. An app that is archived, or that is no longer a Studio package,
    raises :class:`AppNotInstallable` (404), as :func:`install` does.

    The install keeps its id, version, scope and claim rows. An archived
    install kept each claim and its key (§4), so the restore checks no claim.
    The new ``ends_at`` is *now* plus :data:`RESTORED_ENDS` (PKG-Q7). Answers
    the new ``ends_at``.
    """
    install = _install_id(install_id)
    at = _now(now)
    state, _, archived_at, app = await _life(db, install, "a restore")
    if state != "archived" or archived_at is None:
        raise StateRefused(
            "state_refused", f"An install that is {state} cannot be restored.")
    if at >= archived_at + RESTORE_DAYS:
        raise StateRefused(
            "restore_closed", "The time to restore this install has ended.")
    if (await db.execute(text(_APP_SQL), {"app": app})).first() is None:
        raise AppNotInstallable(
            "app_not_installable", "No Studio package that is not archived has this id.")
    ends = at + RESTORED_ENDS
    await db.execute(text(_RESTORE_SQL), {"install": install, "ends": ends})
    return ends


async def expire_due(db: Any, *, now: datetime, limit: int = EXPIRE_LIMIT) -> dict[str, int]:
    """Archive the due installs of the bound org, then delete the spent ones.

    *now* is the time of the run, with a time zone. Step 1 archives each
    ``active`` install whose ``ends_at`` is at or before *now*, with
    ``archived_at`` = *now*. It keeps each claim. A paused install does not
    change (PKG-Q8). Step 2 deletes, through :func:`remove_install`, each
    archived install whose ``archived_at`` is at or before *now* minus
    :data:`RESTORE_DAYS`. Step 1 runs first, and an install that it archived
    has ``archived_at`` = *now*, so step 2 never deletes it in the same call.

    Each step moves at most *limit* rows. The org lock makes two runs of one
    org go one at a time, so the second run finds no due install. The lock is
    an advisory transaction lock, and Postgres lets one session take it again.
    So :func:`remove_install` takes it again with no wait. Answers the count
    of each step as ``{"archived": n, "deleted": n}``.
    """
    at = _now(now)
    most = _limit(limit)
    await _lock_org(db, "an expiry")
    archived = 0
    due = (await db.execute(text(_DUE_SQL), {"now": at, "limit": most})).all()
    for (install,) in due:
        hit = (await db.execute(text(_ARCHIVE_SQL), {"install": install, "now": at})).first()
        archived += hit is not None
    deleted = 0
    spent = (await db.execute(text(_SPENT_SQL), {
        "cutoff": at - RESTORE_DAYS, "limit": most})).all()
    for (install,) in spent:
        try:
            await remove_install(db, install_id=install)
        except InstallNotFound:
            # The row lock holds it, so this is not expected. The removal
            # raised before its statement, so the transaction is still good.
            continue
        deleted += 1
    return {"archived": archived, "deleted": deleted}


def notice_times(ends_at: datetime | None,
                 archived_at: datetime | None) -> tuple[datetime | None, datetime | None]:
    """The times of the two bell notes of §6.2, and nothing more.

    The first note is :data:`NOTICE_BEFORE_END` before *ends_at*. The second
    is :data:`NOTICE_AFTER_ARCHIVE` after *archived_at*, which is 3 days
    before the deletion. A None input gives None for its note. This function
    sends nothing. PKG-10c owns the send.
    """
    first = None if ends_at is None else ends_at - NOTICE_BEFORE_END
    second = None if archived_at is None else archived_at + NOTICE_AFTER_ARCHIVE
    return first, second


# ── The version of an install (PKG-8a) ──────────────────────────────────────

#: The install of one app at one scope in the bound org, with the row lock.
_INSTALL_AT_SCOPE_SQL = (
    "SELECT CAST(id AS text), installed_by, ends_at FROM package_install "
    f"WHERE organization_id = {_BOUND_ORG} AND app_id = CAST(:app AS uuid) "
    "AND scope_kind = :kind AND scope_subject = :subject FOR UPDATE"
)

#: The new version of one install. The state and the end date stay.
_SET_VERSION_SQL = (
    "UPDATE package_install SET version = :version, updated_at = now() "
    f"WHERE organization_id = {_BOUND_ORG} AND id = CAST(:install AS uuid)"
)

#: The claims of one install, before :func:`set_version` writes the new ones.
_CLEAR_CLAIMS_SQL = (
    "DELETE FROM package_contribution "
    f"WHERE organization_id = {_BOUND_ORG} AND install_id = CAST(:install AS uuid)"
)

#: The point and the key of each claim that one install holds now. A kept key
#: skips the overlap check of :func:`set_version` (PKG-8b fix round 1).
_KEPT_KEYS_SQL = (
    "SELECT point, key FROM package_contribution "
    f"WHERE organization_id = {_BOUND_ORG} AND install_id = CAST(:install AS uuid)"
)


async def set_version(
    db: Any,
    *,
    app_id: str,
    version: int | None,
    scope_subject: str,
    scope_kind: str,
    contributions: Sequence[Contribution],
) -> Install | None:
    """Move the install of *app_id* at one scope to *version*, with new claims.

    *db* is a session that the caller opened with ``tenant_session(org)``.
    A publish calls this first, and calls :func:`install` only when it
    answers None: the app has no install at the scope, and nothing is
    written. So a second publish never gets :class:`AlreadyInstalled`.

    The function takes the org lock of :func:`install`, then the row lock of
    the install. It checks each claim against the system keys. It checks the
    overlap of §4 only for a claim whose point and key the install did not
    hold before: a kept key stays its own, also when a wider install took it
    later (PKG-Q6). A new key meets the full overlap, as in :func:`install`.
    The old claims of the install are of the same app, so they never
    conflict. Then it writes the version and replaces each claim. The state,
    the end date and ``installed_by`` stay, so a paused install stays paused.
    Each refusal raises an :class:`InstallError` and writes nothing. The
    store does not check that the member may change the install. The routes
    own that check.

    ⚠️ The lock order of a publish is the ``apps`` row ``FOR UPDATE``, then
    the org lock of ``objects_store``, then this org lock. A second caller
    that does not hold the ``apps`` row must lock it before it calls here or
    :func:`install`. Else the FK check of an INSERT waits for the row, while
    a publish waits for the org lock, and Postgres ends one with a deadlock.
    """
    ver = _version(version)
    # A private install belongs to its subject, so the subject is the member
    # of the check. A bad subject gives "", and _scope refuses it.
    try:
        owner = (normalize_grant_subject(str(scope_subject))
                 if _plain(scope_subject, limit=MAX_SUBJECT_BYTES) else "")
    except ValueError:
        owner = ""
    kind, subject = _scope(scope_subject, scope_kind, owner, ver)
    app = _app_id(app_id)
    claims = _claims(contributions)
    # The lock comes first, as in install. Each read below decides the write.
    await _lock_org(db, "a new version")
    # ⚠️ No _APP_SQL here. The publish holds the ``apps`` row FOR UPDATE and
    # has just made it live. The install route of PKG-8b reads the row FOR KEY
    # SHARE with the same filters first. A later caller (a rollback) must
    # check the app first, as install does.
    row = (await db.execute(text(_INSTALL_AT_SCOPE_SQL), {
        "app": app, "kind": kind, "subject": subject})).first()
    if row is None:
        return None
    install_id = str(row[0])
    wanted = [c for c, _ in claims]
    _check_system(wanted)
    # A kept key stays its own (PKG-Q6). Only a new key meets the overlap.
    kept = {(str(r[0]), str(r[1])) for r in (await db.execute(
        text(_KEPT_KEYS_SQL), {"install": install_id})).all()}
    await _check_held(db, kind, subject, app, [c for c in wanted if (c.point, c.key) not in kept])
    await db.execute(text(_SET_VERSION_SQL), {"install": install_id, "version": ver})
    await db.execute(text(_CLEAR_CLAIMS_SQL), {"install": install_id})
    if claims:
        await db.execute(text(_INSERT_CONTRIBUTION_SQL), [
            {"install": install_id, "point": c.point, "key": c.key, "payload": payload,
             "position": c.position}
            for c, payload in claims])
    return Install(
        id=install_id, app_id=app, version=ver, scope_kind=kind, scope_subject=subject,
        installed_by=str(row[1]), ends_at=row[2], contributions=tuple(wanted))
