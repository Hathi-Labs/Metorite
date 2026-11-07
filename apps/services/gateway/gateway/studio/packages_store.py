"""The one writer of Studio packages in ``apps`` (WS-STU A3).

Spec: ``project-docs/specs/ai_studio.md`` §3.6 (the manifest) and the §14 A3
row. Migration: the file that adds ``apps.format`` (322 at build).
``tests/unit/test_studio_packages_r8.py`` runs this SQL on a real Postgres.

**A package is a row of ``apps``** with ``format = 'manifest'``. Its manifest
lives in ``apps.manifest``, so no second app table exists (§10). An App
Workshop app has ``format = 'code'``, or NULL on a row from before A3. The
App Workshop readers skip a package (``format IS DISTINCT FROM 'manifest'``),
so a package never shows in ``/apps`` and never becomes an agent tool.

**Tenancy (R5).** Every function takes a session that the caller opened with
``tenant_session(org)``, so RLS binds each statement. No function takes an
org. An INSERT writes no ``organization_id``: the column DEFAULT reads the
bound tenant, and each statement filters on ``_BOUND_ORG`` too.

**The member.** Every function takes the member as a required argument, and
every statement filters on ``owner_email``. In A3 a member reads and writes
only the packages that the member owns. ⚠️ The store does not authenticate.
It only scopes. The caller resolves the member from the session, never from
request input (R5, contract R11).

**Validate before write.** :func:`create_package` and
:func:`update_manifest` run :func:`acb_common.studio_manifest.validate_manifest`
before any statement. Then the store checks the JSON text itself: an object,
no NUL, no lone surrogate, no key that is not text, no float that Postgres
writes longer, and at most :data:`MAX_MANIFEST_BYTES` bytes. So no manifest
reaches a statement that the CHECK ``apps_manifest_package`` refuses. Each
refusal raises one :class:`PackageStoreError` that carries every path.

**The slug.** ``apps.slug`` is UNIQUE for all orgs. The slug of a package is
``<manifest id>-<8 hex digits of the new id>``. So a slug that org A holds
never blocks a package of org B. When the slug is taken anyway, the INSERT
writes nothing (``ON CONFLICT (slug) DO NOTHING``), and the store tries a new
id. No refusal names the slug of another org. The slug never changes after
the INSERT, and so the manifest id never changes either:
:func:`update_manifest` refuses a new id.

**Private and a draft.** A new package has ``visibility = 'private'``,
``status = 'draft'`` and ``workspace_path = ''``, because it has no files. No
function here changes the visibility or the format. Only :func:`publish_version`
and :func:`rollback` change the status, and only to ``live``. The trigger
``apps_format_fixed`` refuses a change of ``format`` on the database too.

**Versions (A4).** :func:`publish_version` and :func:`rollback` write
``app_versions``. A version row holds the manifest, ``bundle_html = ''`` and
``bundle_sha256``, the SHA-256 of the manifest JSON text from
:func:`check_manifest`. The store never updates or deletes a version row, so
history is append-only. Both functions lock the package row first
(``FOR UPDATE``), so two writers get N and N+1. ``UNIQUE (app_id, version)``
is the backstop. Each writes its ``app_audit`` row in the same transaction.
⚠️ A package skips the App Workshop review: no function here calls
``record_app_audit`` or proposes ``app.publish_review``. The organization
approval is J2. The install is WS-PKG, and ``apps.live_version`` is only the
pointer of the author.

**The delete (A5c-1).** :func:`delete_preview` counts what a delete takes
and gives :func:`confirm_token` of the counts. :func:`delete_package` is one
step in the caller's transaction: the package row ``FOR UPDATE`` first, then
the org lock of the object store (``objects_store.lock_org``), the one lock
order of a publish. The first step of a call checks the token under both
locks, and raises :class:`CountsChanged` for a stale one. Each later step
checks the object names of that preview. Then the step purges at most one
chunk of records (``objects_store.purge_records``), and the purge stops at
the end of the call. A full chunk ends the step, and the caller commits, so
no lock stays between two steps. The last step of a call that leaves
records writes a partial audit row (op :data:`PARTIAL_OP`) in its own
transaction, so each record that a call removes has an audit row. A step
that finds fewer records than the chunk deletes the ``apps`` row (the one
``DELETE FROM apps`` of a package) and writes the ``app_audit`` row through
:func:`_audit` in the same transaction. The foreign keys cascade to the
versions, the objects, the fields, the hot rows, the installs, the
contributions, the grants, the files, the pins and the data. So the store
still holds no statement that updates or deletes a version row.
``app_audit`` has no foreign key, so its rows stay.

**Not here.** No UI. Install is WS-PKG. The routes are
``routes/studio/packages.py`` (A5 and A5c-1), which runs no SQL of its own.
The admin path of a delete and its chat tool are A5c-2.
"""

from __future__ import annotations

import hashlib
import json
import math
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from acb_common.secret_shapes import looks_like_secret
from acb_common.studio_manifest import ManifestError, manifest_hash, validate_manifest
from sqlalchemy import text

from gateway.package_host.sharing import OTHERS_SEE_THE_INSTALL
from gateway.studio import objects_store

#: The value of ``apps.format`` for a Studio package.
FORMAT = "manifest"
#: The bound of the CHECK ``apps_manifest_package``: the JSON text of a
#: manifest, in bytes (256 KiB). A2's ``MAX_DOC_BYTES`` is the same number.
MAX_MANIFEST_BYTES = 262144
#: A float of this size or more is refused. See :func:`first_bad_value`.
MAX_FLOAT = 1e15
#: The tries of a new id when the slug is taken.
SLUG_TRIES = 3
#: The hex digits of the new id in the slug.
SLUG_HEX = 8
#: The refusal of an ``update_manifest`` that changes the manifest id.
ID_FIXED = "The manifest id cannot change. Create a new package."

_BOUND_ORG = "CAST(NULLIF(current_setting('app.tenant_id', true), '') AS uuid)"

_PACKAGE_COLUMNS = (
    "id::text, slug, name, description, owner_email, visibility, status, "
    "manifest, live_version, created_at, updated_at"
)

#: A taken slug makes no row. The store then tries a new id.
_CREATE_SQL = (
    "INSERT INTO apps (id, slug, name, description, owner_email, visibility, "
    "status, manifest, workspace_path, format) "
    "VALUES (CAST(:id AS uuid), :slug, :name, :desc, :m, 'private', 'draft', "
    "CAST(:manifest AS jsonb), '', 'manifest') "
    "ON CONFLICT (slug) DO NOTHING "
    f"RETURNING {_PACKAGE_COLUMNS}"
)

_GET_SQL = (
    f"SELECT {_PACKAGE_COLUMNS} FROM apps "
    f"WHERE organization_id = {_BOUND_ORG} AND format = 'manifest' "
    "AND owner_email = :m AND id = CAST(:id AS uuid)"
)

_LIST_SQL = (
    f"SELECT {_PACKAGE_COLUMNS} FROM apps "
    f"WHERE organization_id = {_BOUND_ORG} AND format = 'manifest' "
    "AND owner_email = :m ORDER BY updated_at DESC, id"
)

#: The summary of A5: no manifest, so a list of 100 packages never loads 100
#: manifests. ``manifest_id`` is the id inside the manifest.
_SUMMARY_SQL = (
    "SELECT id::text, slug, name, description, status, live_version, created_at, "
    "updated_at, manifest->>'id' FROM apps "
    f"WHERE organization_id = {_BOUND_ORG} AND format = 'manifest' "
    "AND owner_email = :m ORDER BY updated_at DESC, id"
)

#: The catalog read of B1c. One statement for all packages of the member. It
#: reads the object names and the action ids and targets with jsonb path
#: reads, so it never loads a whole manifest. ``:app`` is NULL for no filter.
_ACTIONS_SQL = (
    "SELECT id::text AS id, "
    "coalesce(jsonb_path_query_array(manifest, '$.objects[*].name'), '[]'::jsonb) AS objects, "
    "(SELECT coalesce(jsonb_agg(jsonb_build_object("
    "'id', x->'id', 'uses', x->'uses', 'title', x->'title')), '[]'::jsonb) "
    "FROM jsonb_array_elements(CASE WHEN jsonb_typeof(manifest->'actions') = 'array' "
    "THEN manifest->'actions' ELSE '[]'::jsonb END) AS x) AS actions "
    f"FROM apps WHERE organization_id = {_BOUND_ORG} AND format = 'manifest' "
    "AND owner_email = :m AND (CAST(:app AS uuid) IS NULL OR id = CAST(:app AS uuid)) "
    "ORDER BY updated_at DESC, id"
)

_UPDATE_SQL = (
    "UPDATE apps SET manifest = CAST(:manifest AS jsonb), name = :name, "
    "description = :desc, updated_at = now() "
    f"WHERE organization_id = {_BOUND_ORG} AND format = 'manifest' "
    "AND owner_email = :m AND id = CAST(:id AS uuid) "
    f"RETURNING {_PACKAGE_COLUMNS}"
)

_VERSION_FIELDS = (
    "version, manifest, bundle_sha256, release_notes, published_by, "
    "published_at, review_status")

#: One statement takes the next number, so it never reads a stale maximum.
#: The caller holds the package row lock. ``bundle_html`` is '' for a package.
_VERSION_INSERT_SQL = (
    "INSERT INTO app_versions (app_id, version, manifest, bundle_html, "
    "bundle_sha256, release_notes, scope_set_hash, published_by, review_status) "
    "SELECT CAST(:id AS uuid), COALESCE(MAX(version), 0) + 1, "
    "CAST(:manifest AS jsonb), '', :sha, :notes, '', :m, 'auto' "
    "FROM app_versions WHERE app_id = CAST(:id AS uuid) "
    f"RETURNING app_id::text, {_VERSION_FIELDS}"
)

#: One page of versions, newest first (STU-A5): at most ``:limit``, each older
#: than the cursor ``:before``.
_VERSION_LIST_SQL = (
    "SELECT v.app_id::text, v.version, NULL, v.bundle_sha256, v.release_notes, "
    "v.published_by, v.published_at, v.review_status "
    "FROM app_versions v JOIN apps a ON a.id = v.app_id "
    f"WHERE a.organization_id = {_BOUND_ORG} AND a.format = 'manifest' "
    "AND a.owner_email = :m AND a.id = CAST(:id AS uuid) "
    "AND v.version < CAST(:before AS bigint) ORDER BY v.version DESC LIMIT :limit"
)

_VERSION_GET_SQL = (
    "SELECT v.app_id::text, v.version, v.manifest, v.bundle_sha256, "
    "v.release_notes, v.published_by, v.published_at, v.review_status "
    "FROM app_versions v JOIN apps a ON a.id = v.app_id "
    f"WHERE a.organization_id = {_BOUND_ORG} AND a.format = 'manifest' "
    "AND a.owner_email = :m AND a.id = CAST(:id AS uuid) AND v.version = :v"
)

#: A publish moves the pointer and the status. It leaves ``manifest`` as it is.
_PUBLISH_POINT_SQL = (
    "UPDATE apps SET status = 'live', live_version = :v, updated_at = now() "
    f"WHERE organization_id = {_BOUND_ORG} AND format = 'manifest' "
    "AND owner_email = :m AND id = CAST(:id AS uuid)"
)

#: A rollback overwrites the draft with the copy, and moves the pointer.
_ROLLBACK_POINT_SQL = (
    "UPDATE apps SET manifest = CAST(:manifest AS jsonb), name = :name, "
    "description = :desc, status = 'live', live_version = :v, updated_at = now() "
    f"WHERE organization_id = {_BOUND_ORG} AND format = 'manifest' "
    "AND owner_email = :m AND id = CAST(:id AS uuid)"
)

_AUDIT_SQL = (
    "INSERT INTO app_audit (app_id, app_version, user_email, kind, detail) "
    "VALUES (CAST(:id AS uuid), :v, :m, :kind, CAST(:detail AS jsonb))"
)

#: The longest release note, in characters.
MAX_NOTES_CHARS = 2000
#: The version column is an INT4.
MAX_VERSION = 2**31 - 1
#: The most versions in one page of :func:`list_versions`.
MAX_VERSIONS_PAGE = 100
#: The refusal of a rollback to the live version.
ROLLBACK_LIVE = "That version is already the live version."
#: The refusal of a write whose expected hash is not the hash of the stored
#: draft (STU-G1c review round 1).
DRAFT_CHANGED_TEXT = ("The draft changed after the read that this request names. Nothing was "
                      "written. Read the package again.")

#: The records that one step of a delete removes (A5c-1). A test makes it
#: smaller, and :func:`delete_package` reads it at each call.
DELETE_CHUNK = objects_store.PURGE_CHUNK
#: The op of the audit row of a delete.
DELETE_OP = "package.delete"
#: The op of the audit row of a call that ends with records left (202).
PARTIAL_OP = "package.delete.partial"
#: The longest wait for the ``apps`` row in a delete step, in milliseconds.
#: A publish or another delete can hold the row. Past the bound the step
#: raises ``StoreBusy``.
ROW_LOCK_WAIT_MS = 1_000
#: The longest run of one step before its decision, in seconds: the wait for
#: the ``apps`` row, the wait for the org lock and one purge. A step goes on
#: to a next step only while one more of these fits before the end of the call.
STEP_SECONDS = (ROW_LOCK_WAIT_MS + objects_store.ORG_LOCK_WAIT_MS
                + objects_store.PURGE_TIMEOUT_MS) / 1000
#: The refusal of a delete whose confirm token is not the token of now.
COUNTS_CHANGED_TEXT = ("The package changed after the preview that this request names. Nothing "
                       "was deleted. Read the new preview, then confirm it.")
#: The refusal of a later step of a call that found a new object. The earlier
#: steps of the call removed records, so the text names how many.
COUNTS_CHANGED_LATER_TEXT = ("The package changed during this delete. The delete stopped after "
                             "it removed {removed} records. Read the new preview, then confirm "
                             "it to delete the rest.")
_TOKEN_HEX = frozenset("0123456789abcdef")

#: The versions, the installs, and the installs that reach a member other
#: than the owner, of one package. An archived install reaches nobody. A
#: private install of the owner reaches another member only when the owner
#: shared it: ``sharing.OTHERS_SEE_THE_INSTALL``, the one rule, which the
#: member purge and the resolve read too. That rule names ``package_install``
#: with no alias, so the third count does too. The fourth count (PKG-8a fix
#: round 2) is the sidebar entry of the owner: a private install of the owner
#: that the resolve shows, so active and not expired. Only the delete dialog
#: reads it, so it is not in the token.
_DELETE_COUNTS_SQL = (
    "SELECT (SELECT count(*) FROM app_versions v WHERE v.app_id = a.id), "
    "(SELECT count(*) FROM package_install i "
    "WHERE i.organization_id = a.organization_id AND i.app_id = a.id), "
    "(SELECT count(*) FROM package_install "
    "WHERE package_install.organization_id = a.organization_id "
    "AND package_install.app_id = a.id AND package_install.state <> 'archived' "
    "AND NOT (package_install.scope_kind = 'private' "
    "AND package_install.scope_subject = a.owner_email "
    f"AND NOT {OTHERS_SEE_THE_INSTALL})), "
    "(SELECT count(*) FROM package_install o "
    "WHERE o.organization_id = a.organization_id AND o.app_id = a.id "
    "AND o.state = 'active' AND o.scope_kind = 'private' "
    "AND o.scope_subject = a.owner_email "
    "AND (o.ends_at IS NULL OR o.ends_at > now())) "
    f"FROM apps a WHERE a.organization_id = {_BOUND_ORG} AND a.format = 'manifest' "
    "AND a.owner_email = :m AND a.id = CAST(:id AS uuid)"
)

#: The records that the earlier calls of a delete removed: the sum of the
#: partial rows of the package. ``app_audit`` keeps them, because it has no
#: foreign key.
_PARTIAL_TOTAL_SQL = (
    "SELECT coalesce(sum(CAST(detail->>'records_this_call' AS bigint)), 0) FROM app_audit "
    f"WHERE organization_id = {_BOUND_ORG} AND app_id = CAST(:id AS uuid) "
    "AND kind = 'action' AND detail->>'op' = :op"
)

#: The one ``DELETE FROM apps`` of a package. The foreign keys cascade to the
#: versions, the objects, the fields, the records that are left, the hot rows,
#: the installs, the contributions, the grants, the files, the pins, the tool
#: grants and the data. ``app_audit`` has no foreign key, so its rows stay.
#: Fence: ``test_only_delete_package_deletes_a_package``.
_DELETE_SQL = (
    "DELETE FROM apps "
    f"WHERE organization_id = {_BOUND_ORG} AND format = 'manifest' "
    "AND owner_email = :m AND id = CAST(:id AS uuid) RETURNING id::text"
)


class PackageStoreError(ValueError):
    """A write or a read that the store refuses before it reaches the database.

    ``errors`` holds each refusal as a JSON path and one sentence, in the
    shape of A2's :class:`~acb_common.studio_manifest.ManifestError`.
    """

    def __init__(self, message: str, errors: list[ManifestError] | None = None) -> None:
        super().__init__(message)
        self.errors: list[ManifestError] = list(errors or [])

    @property
    def paths(self) -> list[str]:
        """The JSON path of each refusal."""
        return [e.path for e in self.errors]


class DraftChanged(Exception):
    """The stored draft is not the draft that the caller read. Nothing was
    written. A write that names ``expected_hash`` raises it under the row lock
    (STU-G1c review round 1). The route answers 409 ``draft_changed``."""


def _expect(manifest: dict[str, Any], expected_hash: str | None) -> None:
    """Raise :class:`DraftChanged` when *expected_hash* is given and is not
    the canonical hash of *manifest* (``studio_manifest.manifest_hash``, the
    one hash of a draft). The caller holds the row lock."""
    if expected_hash is not None and manifest_hash(manifest) != expected_hash:
        raise DraftChanged(DRAFT_CHANGED_TEXT)


@dataclass(frozen=True)
class Package:
    """One Studio package: a row of ``apps`` with ``format = 'manifest'``."""

    id: str
    slug: str
    name: str
    description: str
    owner_email: str
    visibility: str
    status: str
    manifest: dict[str, Any]
    live_version: int | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class PackageSummary:
    """One package with no manifest (STU-A5): what a list and a limit need."""

    id: str
    slug: str
    name: str
    description: str
    status: str
    live_version: int | None
    created_at: datetime
    updated_at: datetime
    manifest_id: str


@dataclass(frozen=True)
class PackageActions:
    """What the catalog needs of one package (STU-B1c): the names of its
    objects, and each named action as ``(id, uses, title)``."""

    id: str
    objects: tuple[str, ...]
    actions: tuple[tuple[str, str, str | None], ...]


def _new_id() -> uuid.UUID:
    """The id of a new package. A test replaces it to force a slug clash."""
    return uuid.uuid4()


def _member(member: object) -> str:
    """The member as the store writes ``owner_email``: trimmed, lower case."""
    if not isinstance(member, str) or not member.strip():
        raise PackageStoreError("member must be a non-empty string")
    return member.strip().lower()


def _package_id(package_id: object) -> str:
    """A package id must be a UUID. A bad one fails here, not in a statement
    that would end the caller's transaction."""
    try:
        return str(uuid.UUID(str(package_id)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise PackageStoreError("package_id must be a UUID") from exc


def _refuse(path: str, message: str) -> PackageStoreError:
    return PackageStoreError(
        "The manifest is not valid.", [ManifestError(path=path, message=message)])


def _path(loc: list[Any]) -> str:
    """A JSON path in the form of A2: ``$``, ``.key`` and ``[index]``."""
    out = "$"
    for part in loc:
        out += f"[{part}]" if isinstance(part, int) else f".{part}"
    return out


def first_bad_value(doc: Any) -> ManifestError | None:
    """The first value, a key too, that jsonb refuses or writes longer.

    jsonb refuses the NUL character. A key that is not text is not JSON, and
    JSON would turn it into text. Postgres writes each jsonb number with all
    its digits, so a float in exponent form (1e308 becomes 309 digits) grows
    past the measured size. A lone surrogate has no UTF-8 form, and
    :func:`_manifest_text` refuses it when it encodes the text.

    Iterative. :func:`validate_manifest` runs first and bounds the depth.

    This is the ONE copy of the check. The install store of the package host
    (``package_host/store.py``) calls it for each contribution payload. That
    caller runs no :func:`validate_manifest`, so it bounds the depth first.
    """
    stack: list[tuple[Any, list[Any]]] = [(doc, [])]
    while stack:
        node, loc = stack.pop()
        if isinstance(node, str):
            if "\x00" in node:
                return ManifestError(
                    path=_path(loc), message="This text holds a NUL character. Remove it.")
        elif isinstance(node, bool) or node is None or isinstance(node, int):
            continue
        elif isinstance(node, float):
            if (not math.isfinite(node) or abs(node) >= MAX_FLOAT
                    or "e" in float.__repr__(node)):
                return ManifestError(
                    path=_path(loc),
                    message="A number must be finite and below 10^15, with no exponent.")
        elif isinstance(node, dict):
            for k, v in node.items():
                if not isinstance(k, str):
                    return ManifestError(path=_path(loc), message="Each key must be text.")
                if "\x00" in k:
                    return ManifestError(
                        path=_path(loc), message="A key holds a NUL character. Remove it.")
                stack.append((v, [*loc, k]))
        elif isinstance(node, list):
            stack.extend((v, [*loc, i]) for i, v in enumerate(node))
        else:
            return ManifestError(
                path=_path(loc), message="This value is not JSON. Use text, numbers, lists and objects.")
    return None


def _manifest_text(doc: dict[str, Any]) -> str:
    """The JSON text of a valid manifest, with the length of its jsonb text.

    The separators are those of the jsonb text form. With the value checks of
    :func:`first_bad_value`, the byte count is the count that the CHECK
    ``apps_manifest_package`` makes.
    """
    bad = first_bad_value(doc)
    if bad is not None:
        raise PackageStoreError("The manifest is not valid.", [bad])
    try:
        encoded = json.dumps(doc, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise _refuse("$", "The manifest must be plain JSON.") from exc
    try:
        size = len(encoded.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise _refuse("$", "The manifest holds a lone surrogate. Remove it.") from exc
    if size > MAX_MANIFEST_BYTES:
        raise _refuse(
            "$", f"The manifest is larger than {MAX_MANIFEST_BYTES // 1024} KiB. Shorten it.")
    return encoded


def check_manifest(doc: object) -> str:
    """Validate *doc* as a package manifest. Answers its JSON text.

    The order: an object, then :func:`validate_manifest` with no catalog
    (structure only), then the checks of the JSON text. Each refusal raises
    one :class:`PackageStoreError` with every path that A2 names.
    """
    if not isinstance(doc, dict):
        raise _refuse("$", "A manifest is a JSON object.")
    errors = validate_manifest(doc)
    if errors:
        raise PackageStoreError("The manifest is not valid.", errors)
    return _manifest_text(doc)


def _package(row: Any) -> Package:
    manifest = row[7]
    if isinstance(manifest, str | bytes):
        manifest = json.loads(manifest)
    return Package(
        id=str(row[0]), slug=str(row[1]), name=str(row[2]), description=str(row[3] or ""),
        owner_email=str(row[4]), visibility=str(row[5]), status=str(row[6]),
        manifest=dict(manifest), live_version=None if row[8] is None else int(row[8]),
        created_at=row[9], updated_at=row[10])


async def create_package(db: Any, member: str, manifest: dict[str, Any]) -> Package:
    """Create a package of *member* from *manifest*: private, a draft, no files.

    The name is ``manifest["name"]`` and the description is
    ``manifest["purpose"]``. The slug is ``<manifest id>-<8 hex digits>``.
    A manifest that fails :func:`check_manifest` raises
    :class:`PackageStoreError` and writes nothing.
    """
    m = _member(member)
    encoded = check_manifest(manifest)
    params = {"name": manifest["name"], "desc": manifest["purpose"], "m": m,
              "manifest": encoded}
    for _ in range(SLUG_TRIES):
        new_id = _new_id()
        slug = f"{manifest['id']}-{new_id.hex[:SLUG_HEX]}"
        row = (await db.execute(text(_CREATE_SQL), {
            **params, "id": str(new_id), "slug": slug})).first()
        if row is not None:
            return _package(row)
    # The text names no slug, so it tells nothing of a package of another org.
    raise PackageStoreError("No free name is left for this package. Try again.")


async def get_package(db: Any, member: str, package_id: str, *,
                      for_update: bool = False) -> Package | None:
    """The package *package_id* of *member*, or None. A package of another
    member or of another org, and an App Workshop app, answer None.

    With *for_update*, the read locks the row until the transaction ends.
    """
    sql = _GET_SQL + " FOR UPDATE" if for_update else _GET_SQL
    row = (await db.execute(text(sql), {
        "m": _member(member), "id": _package_id(package_id)})).first()
    return None if row is None else _package(row)


async def list_packages(db: Any, member: str) -> list[Package]:
    """The packages that *member* owns in the bound org, newest change first."""
    rows = (await db.execute(text(_LIST_SQL), {"m": _member(member)})).all()
    return [_package(r) for r in rows]


async def list_summaries(db: Any, member: str) -> list[PackageSummary]:
    """The packages that *member* owns in the bound org, newest change first,
    with no manifest. One statement. A5 counts them for the limit and finds the
    manifest id of a repeat create from them."""
    rows = (await db.execute(text(_SUMMARY_SQL), {"m": _member(member)})).all()
    return [PackageSummary(
        id=str(r[0]), slug=str(r[1]), name=str(r[2]), description=str(r[3] or ""),
        status=str(r[4]), live_version=None if r[5] is None else int(r[5]),
        created_at=r[6], updated_at=r[7], manifest_id=str(r[8] or "")) for r in rows]


def _jsonb(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str | bytes) else value


async def list_package_actions(db: Any, member: str, *,
                               package_id: str | None = None) -> list[PackageActions]:
    """The objects and named actions of the packages that *member* owns in the
    bound org, in ONE statement however many packages exist. With
    *package_id*, only that package. The draft manifest is the source."""
    pid = None if package_id is None else _package_id(package_id)
    rows = (await db.execute(text(_ACTIONS_SQL), {"m": _member(member), "app": pid})).all()
    out: list[PackageActions] = []
    for row in rows:
        names = tuple(n for n in (_jsonb(row[1]) or []) if isinstance(n, str))
        named: list[tuple[str, str, str | None]] = []
        for a in _jsonb(row[2]) or []:
            if isinstance(a, dict) and isinstance(a.get("id"), str) and isinstance(a.get("uses"), str):
                title = a.get("title")
                named.append((a["id"], a["uses"], title if isinstance(title, str) else None))
        out.append(PackageActions(id=str(row[0]), objects=names, actions=tuple(named)))
    return out


async def update_manifest(db: Any, member: str, package_id: str,
                          manifest: dict[str, Any], *,
                          expected_hash: str | None = None) -> Package | None:
    """Replace the manifest of the package *package_id* of *member*.

    The name and the description follow the new manifest. The slug, the
    visibility, the status and the format do not change. Answers None when
    no package of the member has the id: then nothing changes. A manifest
    that fails :func:`check_manifest` raises before the database.

    The manifest ``id`` cannot change, because the slug holds it. A new id
    raises :class:`PackageStoreError` (:data:`ID_FIXED`) and writes nothing.
    The read locks the row first, so a second writer reads the kept id.

    With *expected_hash*, the stored draft must hash to it under the row lock,
    or :class:`DraftChanged` raises and nothing is written. So of two saves
    that read one draft, one writes and the other gets the refusal.
    """
    m = _member(member)
    pid = _package_id(package_id)
    encoded = check_manifest(manifest)
    current = await get_package(db, m, pid, for_update=True)
    if current is None:
        return None
    _expect(current.manifest, expected_hash)
    if current.manifest.get("id") != manifest["id"]:
        raise PackageStoreError(ID_FIXED, [ManifestError(path="$.id", message=ID_FIXED)])
    row = (await db.execute(text(_UPDATE_SQL), {
        "m": m, "id": pid, "manifest": encoded, "name": manifest["name"],
        "desc": manifest["purpose"]})).first()
    return None if row is None else _package(row)


@dataclass(frozen=True)
class PackageVersion:
    """One row of ``app_versions`` for a package. ``manifest`` is None in the
    answer of :func:`list_versions`, which carries the metadata only."""

    package_id: str
    version: int
    manifest: dict[str, Any] | None
    bundle_sha256: str
    release_notes: str
    published_by: str
    published_at: datetime
    review_status: str


def _version(row: Any) -> PackageVersion:
    manifest = row[2]
    if isinstance(manifest, str | bytes):
        manifest = json.loads(manifest)
    return PackageVersion(
        package_id=str(row[0]), version=int(row[1]),
        manifest=None if manifest is None else dict(manifest),
        bundle_sha256=str(row[3]), release_notes=str(row[4] or ""),
        published_by=str(row[5]), published_at=row[6], review_status=str(row[7]))


def _notes(notes: object) -> str:
    if not isinstance(notes, str):
        raise _refuse("$.notes", "The release notes are text.")
    if "\x00" in notes:
        raise _refuse("$.notes", "The release notes hold a NUL character. Remove it.")
    try:
        notes.encode("utf-8")
    except UnicodeEncodeError:
        raise _refuse("$.notes", "The release notes hold a lone surrogate. Remove it.") from None
    if looks_like_secret(notes):
        raise _refuse("$.notes", "The release notes look like a secret. Remove it.")
    if len(notes) > MAX_NOTES_CHARS:
        raise _refuse("$.notes", f"The release notes are longer than {MAX_NOTES_CHARS} characters.")
    return notes


def _version_number(version: object) -> int:
    """A version is an int from 1 to :data:`MAX_VERSION`. The refusal names no value."""
    if isinstance(version, bool) or not isinstance(version, int) or not 1 <= version <= MAX_VERSION:
        raise PackageStoreError(f"version must be an integer from 1 to {MAX_VERSION}")
    return version


def _catalog(catalog: object) -> Mapping[str, str]:
    """A missing catalog would skip the catalog checks, so it is refused."""
    if not isinstance(catalog, Mapping):
        raise PackageStoreError("catalog is required: pass a map of action id to class")
    return catalog


def _check_publishable(doc: dict[str, Any], catalog: Mapping[str, str]) -> str:
    """The JSON text of *doc* after :func:`check_manifest` and A2's checks WITH the catalog."""
    encoded = check_manifest(doc)
    errors = validate_manifest(doc, catalog=catalog)
    if errors:
        raise PackageStoreError("The manifest is not valid.", errors)
    return encoded


def _sha256(encoded: str) -> str:
    """The SHA-256 of the manifest JSON text, as UTF-8 bytes."""
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


async def _append_version(db: Any, m: str, pid: str, encoded: str, notes: str) -> PackageVersion:
    row = (await db.execute(text(_VERSION_INSERT_SQL), {
        "id": pid, "manifest": encoded, "sha": _sha256(encoded), "notes": notes,
        "m": m})).first()
    if row is None:  # pragma: no cover - the INSERT .. SELECT always answers a row
        raise PackageStoreError("The version was not written.")
    return _version(row)


async def _audit(db: Any, m: str, pid: str, version: int | None, kind: str,
                 detail: dict[str, Any]) -> None:
    """The audit row, in the caller's transaction. It never drops an error."""
    await db.execute(text(_AUDIT_SQL), {
        "id": pid, "v": version, "m": m, "kind": kind,
        "detail": json.dumps(detail, ensure_ascii=False)})


async def publish_version(db: Any, member: str, package_id: str, notes: str, *,
                          catalog: Mapping[str, str],
                          expected_hash: str | None = None) -> PackageVersion | None:
    """Publish the draft of the package *package_id* of *member* as version N+1.

    Answers None when no package of the member has the id. The call locks the
    row, reads the stored draft, and runs :func:`check_manifest` on it again.
    Then it runs A2's ``validate_manifest`` WITH *catalog*, so a colour, a px
    size, an unknown block or an action that the catalog does not serve
    raises :class:`PackageStoreError` and writes nothing.

    *catalog* maps an action id to its class letter, as
    ``routes/studio/actions.catalog_rows`` serves it (``{row["id"]:
    row["class"]}``). The caller passes it, because a store must not import
    a route module. The caller reads it BEFORE it opens the store session.

    One transaction writes the version row (``review_status = 'auto'``,
    ``scope_set_hash = ''``, ``published_by`` the member), sets
    ``apps.status = 'live'`` and ``apps.live_version = N+1``, and writes the
    ``app_audit`` row (kind ``publish``). ``apps.manifest`` stays as it is.
    No review step runs here: J2 owns the organization approval.

    With *expected_hash*, the stored draft must hash to it under the row lock,
    or :class:`DraftChanged` raises and nothing is written (STU-G1c review
    round 1). A chat publish names the draft that its approved plan names, so
    a save that lands after its read cannot go live in its place.
    """
    m = _member(member)
    pid = _package_id(package_id)
    note = _notes(notes)
    known = _catalog(catalog)
    current = await get_package(db, m, pid, for_update=True)
    if current is None:
        return None
    _expect(current.manifest, expected_hash)
    encoded = _check_publishable(current.manifest, known)
    version = await _append_version(db, m, pid, encoded, note)
    await db.execute(text(_PUBLISH_POINT_SQL), {"m": m, "id": pid, "v": version.version})
    await _audit(db, m, pid, version.version, "publish", {
        "version": version.version, "sha256": version.bundle_sha256, "notes": note})
    return version


async def rollback(db: Any, member: str, package_id: str, version: int, *,
                   catalog: Mapping[str, str]) -> PackageVersion | None:
    """Roll the package back to *version*. History stays append-only.

    ⚠️ This OVERWRITES THE DRAFT. In one transaction under the row lock it
    inserts version N+1 as a copy of the manifest of *version*, sets
    ``apps.manifest`` to that copy (the name and the description follow it),
    and sets ``apps.live_version = N+1``. It writes an ``app_audit`` row (kind
    ``rollback``) whose detail names both numbers, and says the draft was
    overwritten. Any edit of the draft since the last publish is lost.

    Answers None for a missing package or a missing version. Raises
    :class:`PackageStoreError` (:data:`ROLLBACK_LIVE`) for the live version.
    *catalog* is required, as for :func:`publish_version`. The copy runs
    :func:`check_manifest` and A2's checks WITH the catalog before any write,
    so a version with an action that the catalog withdrew or reclassified is
    refused, and the draft stays as it is.
    """
    m = _member(member)
    pid = _package_id(package_id)
    version = _version_number(version)
    known = _catalog(catalog)
    current = await get_package(db, m, pid, for_update=True)
    if current is None:
        return None
    old = await get_version(db, m, pid, version)
    if old is None or old.manifest is None:
        return None
    if current.live_version == version:
        raise PackageStoreError(ROLLBACK_LIVE)
    encoded = _check_publishable(old.manifest, known)
    new = await _append_version(db, m, pid, encoded, f"Rollback to version {version}.")
    await db.execute(text(_ROLLBACK_POINT_SQL), {
        "m": m, "id": pid, "manifest": encoded, "name": old.manifest["name"],
        "desc": old.manifest["purpose"], "v": new.version})
    await _audit(db, m, pid, new.version, "rollback", {
        "to_version": version, "new_version": new.version,
        "from_version": current.live_version, "draft_overwritten": True,
        "sha256": new.bundle_sha256})
    return new


async def list_versions(db: Any, member: str, package_id: str, *,
                        limit: int = MAX_VERSIONS_PAGE,
                        before: int | None = None) -> list[PackageVersion]:
    """One page of the versions of a package of *member*, newest first, with
    no manifest: at most *limit* (1 to :data:`MAX_VERSIONS_PAGE`), and only
    the versions older than *before* when it is given. A package of another
    member or org answers an empty list."""
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_VERSIONS_PAGE:
        raise PackageStoreError(f"limit must be an integer from 1 to {MAX_VERSIONS_PAGE}")
    cursor = MAX_VERSION + 1 if before is None else _version_number(before)
    rows = (await db.execute(text(_VERSION_LIST_SQL), {
        "m": _member(member), "id": _package_id(package_id), "before": cursor,
        "limit": limit})).all()
    return [_version(r) for r in rows]


async def get_version(db: Any, member: str, package_id: str,
                      version: int) -> PackageVersion | None:
    """One version, with its manifest, or None."""
    _version_number(version)
    row = (await db.execute(text(_VERSION_GET_SQL), {
        "m": _member(member), "id": _package_id(package_id), "v": version})).first()
    return None if row is None else _version(row)


# --- the delete of a package (A5c-1) ----------------------------------------


@dataclass(frozen=True)
class DeletePreview:
    """What a delete of one package removes, and the token that confirms it.

    ``objects`` holds each object by name, with its field count and its
    record count up to ``objects_store.COUNT_CAP``. ``confirm`` is
    :func:`confirm_token` of these counts. The preview holds no manifest
    text and no record value."""

    id: str
    name: str
    slug: str
    objects: tuple[objects_store.ObjectCount, ...]
    versions: int
    installs: int
    other_member_installs: int
    confirm: str
    #: The sidebar entry of the owner: an active private install of the owner
    #: (PKG-8a fix round 2). The dialog alone reads it, and the token does not.
    own_entries: int = 0

    @property
    def fields(self) -> int:
        """The fields of all objects."""
        return sum(o.fields for o in self.objects)

    @property
    def records(self) -> int:
        """The records of all objects, each up to the cap."""
        return sum(o.records for o in self.objects)

    @property
    def capped(self) -> bool:
        """True when an object holds more records than the cap."""
        return any(o.capped for o in self.objects)


@dataclass(frozen=True)
class DeleteStep:
    """One step of a delete.

    ``deleted`` is True when the ``apps`` row is gone. ``removed`` is the
    records that this step removed. ``names`` are the object names of the
    preview that the member confirmed, so the next step of the call can
    check them. ``left`` is the preview of now when the call ends here with
    records left: the route answers 202 with its token. ``changed`` is True
    when a later step found an object that the confirmed preview did not
    hold (409). ``busy`` is True when a later step got ``StoreBusy``, or when
    the preview of the end was busy: then ``left`` can be None. ``gone`` is
    True when another call deleted the package between two steps of this
    call (404)."""

    deleted: bool
    removed: int
    names: tuple[str, ...] = ()
    left: DeletePreview | None = None
    changed: bool = False
    busy: bool = False
    gone: bool = False


class CountsChanged(Exception):
    """The confirm token is not the token of the package now, so nothing was
    deleted. ``preview`` is the preview of now, with its own token. The route
    answers 409 ``counts_changed`` with it."""

    def __init__(self, preview: DeletePreview) -> None:
        super().__init__(COUNTS_CHANGED_TEXT)
        self.preview = preview


def confirm_token(package_id: str, objects: Sequence[objects_store.ObjectCount],
                  versions: int, installs: int, other_member_installs: int) -> str:
    """The SHA-256 of the canonical JSON of the id, the object names and the
    counts. The token stops a delete after a stale preview. It is not an
    authorization: the owner check runs before it."""
    doc = {
        "v": 1, "id": package_id, "versions": versions, "installs": installs,
        "other_member_installs": other_member_installs,
        "objects": [[o.name, o.fields, o.records, o.capped]
                    for o in sorted(objects, key=lambda o: o.name)],
    }
    canonical = json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


def _confirm(token: object) -> str:
    """A confirm token is 64 lower-case hex digits. The refusal names no value."""
    if not isinstance(token, str) or len(token) != 64 or not set(token) <= _TOKEN_HEX:
        raise PackageStoreError("confirm must be 64 lower-case hexadecimal digits")
    return token


def _count(value: object, what: str, low: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < low:
        raise PackageStoreError(f"{what} must be a whole number from {low}")
    return value


def _names(names: object) -> frozenset[str]:
    """The object names of the confirmed preview, as a later step takes them."""
    if (isinstance(names, str | bytes) or not isinstance(names, tuple | list | set | frozenset)
            or not all(isinstance(n, str) for n in names)):
        raise PackageStoreError("names must be the object names of the confirmed preview")
    return frozenset(names)


async def _preview(db: Any, m: str, current: Package) -> DeletePreview | None:
    """The preview of *current*, or None when its row is gone. A delete by
    another call can commit between the read of the package and this count.
    That is a 404, not a 500."""
    objects = await objects_store.package_counts(db, current.id)
    row = (await db.execute(text(_DELETE_COUNTS_SQL), {"m": m, "id": current.id})).first()
    if row is None:
        return None
    versions, installs, other, own = (int(v) for v in row)
    found = tuple(objects)
    return DeletePreview(
        id=current.id, name=current.name, slug=current.slug, objects=found,
        versions=versions, installs=installs, other_member_installs=other,
        confirm=confirm_token(current.id, found, versions, installs, other), own_entries=own)


async def delete_preview(db: Any, member: str, package_id: str) -> DeletePreview | None:
    """The preview of a delete of the package *package_id* of *member*, or
    None. A package of another member or org, an App Workshop app, and a
    package that a concurrent delete removes answer None. A read: it takes no
    lock. The record counts run under the 5 s deadline of the object store,
    and a timeout raises ``StoreBusy``."""
    m = _member(member)
    current = await get_package(db, m, _package_id(package_id))
    return None if current is None else await _preview(db, m, current)


def _who(current: Package) -> dict[str, Any]:
    return {"by": "owner", "owner": current.owner_email, "name": current.name,
            "slug": current.slug}


async def record_partial(db: Any, member: str, package_id: str, this_call: int,
                         reason: str) -> None:
    """The partial audit row of a call whose step could not read the package:
    another call deleted it (``gone``), or its row wait was busy (``busy``).
    ``app_audit`` has no foreign key, so the row can name a package that is
    gone. It names the member, the package id and the records of the earlier
    steps of this call, which committed. The route calls it for a busy row
    wait in a new session, because that wait ended the transaction of its
    step."""
    m = _member(member)
    pid = _package_id(package_id)
    count = _count(this_call, "this_call", 0)
    await _audit(db, m, pid, None, "action", {
        "op": PARTIAL_OP, "by": "owner", "owner": m, "records_this_call": count,
        "reason": reason})


async def _read_for_delete(db: Any, m: str, pid: str) -> Package | None:
    """The package row ``FOR UPDATE``, with a wait of at most
    :data:`ROW_LOCK_WAIT_MS`. A publish or another delete can hold the row.
    Past the bound it raises ``StoreBusy``, and the transaction is over."""
    async with objects_store.bounded_wait(db, ROW_LOCK_WAIT_MS):
        return await get_package(db, m, pid, for_update=True)


async def _end_call(db: Any, m: str, current: Package, seen: frozenset[str], this_call: int,
                    reason: str, *, removed: int = 0) -> DeleteStep:
    """The end of a call that leaves records: the partial audit row, in the
    transaction of this step, and the preview of now for the token of a 202.

    The audit row comes first. A failed insert rolls back this step, its
    purge too. A busy preview leaves the row in place and gives no token, so
    the route answers 503 and the caller reads the preview again."""
    await _audit(db, m, current.id, current.live_version, "action", {
        "op": PARTIAL_OP, **_who(current), "records_this_call": this_call, "reason": reason})
    try:
        left = await _preview(db, m, current)
    except objects_store.StoreBusy:
        left = None
    return DeleteStep(deleted=False, removed=removed, names=tuple(sorted(seen)), left=left,
                      changed=reason == "changed", busy=reason == "busy" or left is None)


async def _finish_delete(db: Any, m: str, current: Package, seen: frozenset[str],
                         before: int, gone: int, *, first: bool) -> DeleteStep | None:
    """The last step of a delete: no record is left, because the org lock
    keeps each record writer out. It deletes the ``apps`` row (the one
    ``DELETE FROM apps`` of a package, and the cascade takes the rest) and
    writes the final audit row in the same transaction. Its one caller is
    :func:`delete_package`.

    The counts of the final row can be busy. On the first step of a call
    ``StoreBusy`` raises, and the step rolls back. On a later step the
    earlier steps and this purge hold, so the call ends with the partial row
    (reason ``busy``), and the next call deletes the package."""
    pid = current.id
    try:
        left = await _preview(db, m, current)
    except objects_store.StoreBusy:
        if first:
            raise
        return await _end_call(db, m, current, seen, before + gone, "busy", removed=gone)
    if left is None:  # pragma: no cover - the row lock holds the row
        return None
    earlier = int((await db.execute(text(_PARTIAL_TOTAL_SQL), {
        "id": pid, "op": PARTIAL_OP})).scalar_one())
    row = (await db.execute(text(_DELETE_SQL), {"m": m, "id": pid})).first()
    if row is None:  # pragma: no cover - the row lock holds the row
        raise PackageStoreError("The package was not deleted.")
    await _audit(db, m, pid, current.live_version, "action", {
        "op": DELETE_OP, **_who(current), "objects": len(left.objects), "fields": left.fields,
        "records_this_call": before + gone, "records_total": earlier + before + gone,
        "versions": left.versions, "installs": left.installs,
        "other_member_installs": left.other_member_installs})
    return DeleteStep(deleted=True, removed=gone, names=tuple(sorted(seen)))


async def delete_package(db: Any, member: str, package_id: str, *,
                         confirm: str | None = None, names: Any = None,
                         chunk: int | None = None, removed: int = 0,
                         end_at: float | None = None) -> DeleteStep | None:
    """One step of the delete of the package *package_id* of *member*, in the
    caller's transaction (A5c-1). Answers None when no package of the member
    has the id: then nothing changes.

    **The lock order.** The ``apps`` row ``FOR UPDATE`` first, then the org
    lock (``objects_store.lock_org``), the order of a publish. So a delete
    and a publish of one package run one at a time, and no step deadlocks
    with a record write.

    **The row wait.** The step waits at most :data:`ROW_LOCK_WAIT_MS` for the
    ``apps`` row. Past it ``StoreBusy`` raises, and the transaction is over.
    For a later step the route writes the partial row in a new session
    (:func:`record_partial`). A later step that finds the package gone,
    because another call deleted it, writes the partial row of the earlier
    steps and answers ``gone``.

    **The first step of a call** takes *confirm*. It builds the preview under
    both locks. A token that is not :func:`confirm_token` of now raises
    :class:`CountsChanged`, and nothing changes. ``StoreBusy`` raises too,
    because the call removed nothing yet. The step answers the object names
    of that preview in ``names``.

    **Each later step** takes *names* in place of a token. It reads the
    object names under both locks. A name that is not in *names* (a publish
    between two steps added it) ends the call with ``changed``. A
    ``StoreBusy`` ends the call with ``busy``. Each end writes the partial
    audit row (:func:`_end_call`), because the earlier steps of the call
    removed records.

    **The purge** deletes at most *chunk* records (:data:`DELETE_CHUNK` when
    None), and it stops at *end_at* (a time of the object store clock) when
    that comes first. A full chunk can leave records. Then the step goes on
    to a next step only while :data:`STEP_SECONDS` more fit before *end_at*.
    Else this step is the last of the call. It writes the partial audit row
    (op :data:`PARTIAL_OP`) and builds the preview of the 202, in its own
    transaction, so a failed audit rolls back its purge too.

    **The last step of a delete** finds fewer records than the chunk. The org
    lock keeps each record writer out, so none is left. It deletes the
    ``apps`` row (the cascade takes the rest) and writes the final audit row
    (op :data:`DELETE_OP`) through :func:`_audit` in that transaction.
    *removed* is the records that the earlier steps of this call removed.
    ``records_this_call`` counts this call only. ``records_total`` adds the
    partial rows of the earlier calls.
    """
    m = _member(member)
    pid = _package_id(package_id)
    token = None if confirm is None else _confirm(confirm)
    if (token is None) == (names is None):
        raise PackageStoreError("Send confirm on the first step of a call, and names after it.")
    seen: frozenset[str] = frozenset() if names is None else _names(names)
    size = _count(DELETE_CHUNK if chunk is None else chunk, "chunk", 1)
    before = _count(removed, "removed", 0)
    current = await _read_for_delete(db, m, pid)
    if current is None:
        if token is None and before > 0:
            await record_partial(db, m, pid, before, "gone")
            return DeleteStep(deleted=False, removed=0, names=tuple(sorted(seen)), gone=True)
        return None
    if token is not None:
        await objects_store.lock_org(db)
        now = await _preview(db, m, current)
        if now is None:  # pragma: no cover - the row lock holds the row
            return None
        if now.confirm != token:
            raise CountsChanged(now)
        seen = frozenset(o.name for o in now.objects)
    else:
        try:
            await objects_store.lock_org(db)
            found = await objects_store.object_names(db, pid)
        except objects_store.StoreBusy:
            return await _end_call(db, m, current, seen, before, "busy")
        if not set(found) <= seen:
            return await _end_call(db, m, current, seen, before, "changed")
    try:
        gone = await objects_store.purge_records(db, pid, limit=size, deadline=end_at)
    except objects_store.StoreBusy:
        if token is not None:
            raise
        return await _end_call(db, m, current, seen, before, "busy")
    if gone >= size:
        if end_at is not None and objects_store._clock() + STEP_SECONDS > end_at:
            return await _end_call(db, m, current, seen, before + gone, "budget", removed=gone)
        return DeleteStep(deleted=False, removed=gone, names=tuple(sorted(seen)))
    return await _finish_delete(db, m, current, seen, before, gone, first=token is not None)
