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
the INSERT.

**Private and a draft.** A new package has ``visibility = 'private'``,
``status = 'draft'`` and ``workspace_path = ''``, because it has no files. No
function here changes the visibility, the status or the format. The trigger
``apps_format_fixed`` refuses a change of ``format`` on the database too.

**Not here.** No route and no UI. Publish, versions and rollback are A4,
over ``app_versions``. Install is WS-PKG.
"""

from __future__ import annotations

import json
import math
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from acb_common.studio_manifest import ManifestError, validate_manifest
from sqlalchemy import text

#: The value of ``apps.format`` for a Studio package.
FORMAT = "manifest"
#: The bound of the CHECK ``apps_manifest_package``: the JSON text of a
#: manifest, in bytes (256 KiB). A2's ``MAX_DOC_BYTES`` is the same number.
MAX_MANIFEST_BYTES = 262144
#: A float of this size or more is refused. See :func:`_first_bad_value`.
MAX_FLOAT = 1e15
#: The tries of a new id when the slug is taken.
SLUG_TRIES = 3
#: The hex digits of the new id in the slug.
SLUG_HEX = 8

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


def _first_bad_value(doc: Any) -> ManifestError | None:
    """The first value, a key too, that jsonb refuses or writes longer.

    jsonb refuses the NUL character. A key that is not text is not JSON, and
    JSON would turn it into text. Postgres writes each jsonb number with all
    its digits, so a float in exponent form (1e308 becomes 309 digits) grows
    past the measured size. A lone surrogate has no UTF-8 form, and
    :func:`_manifest_text` refuses it when it encodes the text.

    Iterative. :func:`validate_manifest` runs first and bounds the depth.
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
    :func:`_first_bad_value`, the byte count is the count that the CHECK
    ``apps_manifest_package`` makes.
    """
    bad = _first_bad_value(doc)
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
    """
    m = _member(member)
    pid = _package_id(package_id)
    encoded = check_manifest(manifest)
    row = (await db.execute(text(_UPDATE_SQL), {
        "m": m, "id": pid, "manifest": encoded, "name": manifest["name"],
        "desc": manifest["purpose"]})).first()
    return None if row is None else _package(row)
