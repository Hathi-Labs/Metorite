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

**Not here.** No route and no UI. Install is WS-PKG.
"""

from __future__ import annotations

import hashlib
import json
import math
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from acb_common.secret_shapes import looks_like_secret
from acb_common.studio_manifest import ManifestError, validate_manifest
from sqlalchemy import text

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

_VERSION_LIST_SQL = (
    "SELECT v.app_id::text, v.version, NULL, v.bundle_sha256, v.release_notes, "
    "v.published_by, v.published_at, v.review_status "
    "FROM app_versions v JOIN apps a ON a.id = v.app_id "
    f"WHERE a.organization_id = {_BOUND_ORG} AND a.format = 'manifest' "
    "AND a.owner_email = :m AND a.id = CAST(:id AS uuid) ORDER BY v.version DESC"
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
#: The refusal of a rollback to the live version.
ROLLBACK_LIVE = "That version is already the live version."


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


async def update_manifest(db: Any, member: str, package_id: str,
                          manifest: dict[str, Any]) -> Package | None:
    """Replace the manifest of the package *package_id* of *member*.

    The name and the description follow the new manifest. The slug, the
    visibility, the status and the format do not change. Answers None when
    no package of the member has the id: then nothing changes. A manifest
    that fails :func:`check_manifest` raises before the database.

    The manifest ``id`` cannot change, because the slug holds it. A new id
    raises :class:`PackageStoreError` (:data:`ID_FIXED`) and writes nothing.
    The read locks the row first, so a second writer reads the kept id.
    """
    m = _member(member)
    pid = _package_id(package_id)
    encoded = check_manifest(manifest)
    current = await get_package(db, m, pid, for_update=True)
    if current is None:
        return None
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


async def _audit(db: Any, m: str, pid: str, version: int, kind: str,
                 detail: dict[str, Any]) -> None:
    """The audit row, in the caller's transaction. It never drops an error."""
    await db.execute(text(_AUDIT_SQL), {
        "id": pid, "v": version, "m": m, "kind": kind,
        "detail": json.dumps(detail, ensure_ascii=False)})


async def publish_version(db: Any, member: str, package_id: str, notes: str, *,
                          catalog: Mapping[str, str]) -> PackageVersion | None:
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
    """
    m = _member(member)
    pid = _package_id(package_id)
    note = _notes(notes)
    known = _catalog(catalog)
    current = await get_package(db, m, pid, for_update=True)
    if current is None:
        return None
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


async def list_versions(db: Any, member: str, package_id: str) -> list[PackageVersion]:
    """The versions of a package of *member*, newest first, with no manifest.
    A package of another member or org answers an empty list."""
    rows = (await db.execute(text(_VERSION_LIST_SQL), {
        "m": _member(member), "id": _package_id(package_id)})).all()
    return [_version(r) for r in rows]


async def get_version(db: Any, member: str, package_id: str,
                      version: int) -> PackageVersion | None:
    """One version, with its manifest, or None."""
    _version_number(version)
    row = (await db.execute(text(_VERSION_GET_SQL), {
        "m": _member(member), "id": _package_id(package_id), "v": version})).first()
    return None if row is None else _version(row)
