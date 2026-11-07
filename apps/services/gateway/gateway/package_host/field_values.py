"""The one writer of package extension values (WS-PKG PKG-13).

Spec: ``project-docs/specs/package_host.md`` §5.1 (the table
``package_field_value``), §8.3 (field extensions) and the PKG-13 row of §10.
Migration: the file that creates ``package_field_value`` (353 at build).
``tests/unit/test_package_field_values_r8.py`` runs this SQL on a real
Postgres, and ``tests/unit/test_package_field_values.py`` holds the checks
with no database and the fence of the one writer.

**The one writer.** This module holds the only INSERT, UPDATE and DELETE of
``package_field_value`` in ``apps/`` and ``packages/``. The FK cascade of a
removal (``store.remove_install``) deletes values too, and that stays legal.
No route calls this module yet. PKG-14 adds the host door, and the door
checks that the host record exists and that the viewer may see it. This
module checks neither.

**The declaration.** A field is a claim of the install at the point
``fields``, with the key ``<record kind>:<field key>`` (§4). Its payload is
a field spec of ``ai_studio.md`` §3.1 without its key. The store parses it
with :func:`acb_common.studio_fields.parse_field_spec`. It refuses a spec that
sets ``required``, ``hot`` or ``to``, and the types ``relation`` and
``file``. :func:`acb_common.studio_fields.check_value` is the one value check.
Each refusal of a value raises :class:`~acb_common.studio_fields.FieldError`,
and its message names the field key.

**A value belongs to an install.** :func:`set_value` writes a value of an
``active`` install only. A paused or archived install raises
:class:`~gateway.package_host.store.StateRefused`, and the values stay.
:func:`get_values` answers the values of active installs only. It leaves out
a value whose stored type differs from the current declaration.

**The lock.** :func:`set_value` reads the install row with ``FOR KEY SHARE``.
That lock conflicts with the DELETE of ``store.remove_install`` and with the
``FOR UPDATE`` of a pause. So a removal waits for the write and then deletes
the new value by the cascade. A write that comes after a removal finds no
row and raises :class:`~gateway.package_host.store.InstallNotFound`, never a
foreign key error. The write takes no org lock, so two writes of one org do
not wait on each other.

**Tenancy (R5).** Each function takes a session that the caller opened with
``tenant_session(org)``. It opens no session and commits nothing. No function
takes an org. The INSERT writes no ``organization_id``, because the column
DEFAULT reads the bound tenant. Each statement names the bound org beside
RLS. A session with no tenant raises :class:`~acb_common.db.TenantUnbound`.
:func:`set_value` also refuses a transaction that is not READ COMMITTED, as
the install store does.

**Before the database.** The checks of the record kind, the record id, the
field key and the install id run before the first statement. The value check
and the size check run before the write. A value is at most
:data:`MAX_VALUE_BYTES` bytes of jsonb text, as the CHECK
``package_field_value_value_size`` counts them. A long ``many`` value can pass
:func:`~acb_common.studio_fields.check_value` and still be too large.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from acb_common.db import TenantUnbound
from acb_common.studio_fields import (
    NAME_PATTERN,
    FieldError,
    FieldSpec,
    check_value,
    parse_field_spec,
)
from sqlalchemy import text

from gateway.package_host.store import (
    _BOUND_ORG,
    _ORG_SQL,
    ISOLATION,
    InstallError,
    InstallNotFound,
    StateRefused,
    _install_id,
)

#: The host records that a package may extend (§8.3). ``task`` is a row of
#: ``pm_tasks``. Each ``crm.*`` kind is a native table of ``144_crm.sql``.
HOST_RECORD_KINDS: tuple[str, ...] = ("task", "crm.lead", "crm.contact", "crm.company", "crm.deal")

#: The bound of the CHECK ``package_field_value_value_size``, in bytes.
MAX_VALUE_BYTES = 65536
#: The point of a field declaration (§4).
FIELDS_POINT = "fields"
#: The types that PKG-13 refuses in a declaration (§8.3).
REFUSED_TYPES: frozenset[str] = frozenset({"relation", "file"})
#: The parts of a field spec that a declaration may not set (§8.3).
REFUSED_SPEC_KEYS: tuple[str, ...] = ("required", "hot", "to")

_FIELD_KEY_RE = re.compile(NAME_PATTERN, re.ASCII)

#: The install row with the key share lock, and its declaration of the field.
_INSTALL_SQL = (
    "SELECT i.state, CAST(c.payload AS text) FROM package_install AS i"
    " LEFT JOIN package_contribution AS c"
    " ON c.organization_id = i.organization_id AND c.install_id = i.id"
    f" AND c.point = '{FIELDS_POINT}' AND c.key = :claim"
    f" WHERE i.organization_id = {_BOUND_ORG} AND i.id = CAST(:install AS uuid)"
    " FOR KEY SHARE OF i"
)

_UPSERT_SQL = (
    "INSERT INTO package_field_value (install_id, record_kind, record_id, field_key, "
    "field_type, value) VALUES (CAST(:install AS uuid), :kind, CAST(:record AS uuid), "
    ":key, :type, CAST(:value AS jsonb)) "
    "ON CONFLICT (organization_id, install_id, record_kind, record_id, field_key) "
    "DO UPDATE SET field_type = EXCLUDED.field_type, value = EXCLUDED.value, "
    "updated_at = now() "
    f"WHERE package_field_value.organization_id = {_BOUND_ORG}"
)

_DELETE_ONE_SQL = (
    "DELETE FROM package_field_value "
    f"WHERE organization_id = {_BOUND_ORG} AND install_id = CAST(:install AS uuid) "
    "AND record_kind = :kind AND record_id = CAST(:record AS uuid) AND field_key = :key"
)

_DELETE_RECORD_SQL = (
    "DELETE FROM package_field_value "
    f"WHERE organization_id = {_BOUND_ORG} "
    "AND record_kind = :kind AND record_id = CAST(:record AS uuid)"
)

#: The values of one host record: active installs only, and only a value
#: whose stored type is the type of the current declaration.
_GET_SQL = (
    "SELECT CAST(v.install_id AS text), CAST(i.app_id AS text), v.field_key,"
    " v.field_type, CAST(v.value AS text)"
    " FROM package_field_value AS v"
    " JOIN package_install AS i"
    " ON i.organization_id = v.organization_id AND i.id = v.install_id"
    " JOIN package_contribution AS c"
    " ON c.organization_id = v.organization_id AND c.install_id = v.install_id"
    f" AND c.point = '{FIELDS_POINT}' AND c.key = v.record_kind || ':' || v.field_key"
    f" WHERE v.organization_id = {_BOUND_ORG}"
    " AND v.record_kind = :kind AND v.record_id = CAST(:record AS uuid)"
    " AND i.state = 'active' AND c.payload ->> 'type' = v.field_type"
    " ORDER BY v.field_key, v.install_id"
)


class FieldNotDeclared(InstallError):
    """The install declares no field of this key on this record kind (422)."""

    def __init__(self, record_kind: str, field_key: str) -> None:
        super().__init__(
            "field_not_declared",
            f"Field `{field_key}` is not a field that this install declares on {record_kind}.")
        self.record_kind = record_kind
        self.field_key = field_key


@dataclass(frozen=True)
class FieldValue:
    """One value that :func:`get_values` answers."""

    install_id: str
    app_id: str
    field_key: str
    field_type: str
    value: Any


# ── The checks with no database ─────────────────────────────────────────────


def _record_kind(record_kind: object) -> str:
    if not isinstance(record_kind, str) or record_kind not in HOST_RECORD_KINDS:
        raise InstallError(
            "bad_record_kind", f"The record kind must be one of {', '.join(HOST_RECORD_KINDS)}.")
    return record_kind


def _record_id(record_id: object) -> str:
    try:
        return str(uuid.UUID(str(record_id)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise InstallError("bad_record_id", "The record id must be a UUID.") from exc


def _field_key(field_key: object) -> str:
    if not isinstance(field_key, str) or not _FIELD_KEY_RE.fullmatch(field_key):
        raise InstallError(
            "bad_field_key",
            "A field key is lower-case letters, digits and _, a letter first, at most 48.")
    return field_key


def claim_key(record_kind: str, field_key: str) -> str:
    """The key of the ``fields`` claim that declares the field (§8.3)."""
    return f"{record_kind}:{field_key}"


def declared_field(field_key: str, payload: object) -> FieldSpec:
    """The field that a ``fields`` claim declares, or :class:`FieldError`.

    *payload* is the claim payload: a field spec without its key. A payload
    that names another key, sets ``required``, ``hot`` or ``to``, or has the
    type ``relation`` or ``file``, is refused. Each message names the key.
    """
    if not isinstance(payload, Mapping):
        raise FieldError(field_key, f"Field `{field_key}` has a declaration that is not an object.")
    spec = dict(payload)
    if spec.get("key", field_key) != field_key:
        raise FieldError(field_key, f"Field `{field_key}` has a declaration of another key.")
    for part in REFUSED_SPEC_KEYS:
        if spec.get(part) not in (None, False):
            raise FieldError(
                field_key, f"Field `{field_key}` sets `{part}`, and an extension field cannot.")
    spec["key"] = field_key
    field = parse_field_spec(spec)
    if field.type in REFUSED_TYPES:
        raise FieldError(
            field_key, f"Field `{field_key}` has the type {field.type}, and an extension "
            "field cannot have it.")
    return field


def value_json(field: FieldSpec, value: Any) -> tuple[Any, str]:
    """The checked value of *field* and its JSON text, or :class:`FieldError`.

    The separators are those of the jsonb text form. With the checks of
    :func:`check_value`, the byte count is the count of the CHECK.
    """
    checked = check_value(field, value)
    encoded = json.dumps(checked, ensure_ascii=False, allow_nan=False)
    if len(encoded.encode("utf-8")) > MAX_VALUE_BYTES:
        raise FieldError(
            field.key, f"Field `{field.key}` holds at most {MAX_VALUE_BYTES} bytes of JSON. "
            "Shorten the value.")
    return checked, encoded


# ── The database ────────────────────────────────────────────────────────────


async def _bound(db: Any, act: str, *, read_committed: bool = False) -> None:
    """Refuse a session with no tenant, and on request a transaction that is
    not READ COMMITTED."""
    bound = (await db.execute(text(_ORG_SQL))).first()
    org, level = (bound[0], bound[1]) if bound is not None else (None, None)
    if not org:
        raise TenantUnbound(f"{act} needs a session bound to a tenant")
    if read_committed and level != ISOLATION:
        raise RuntimeError(f"{act} needs a {ISOLATION} transaction, and this one is {level}")


async def set_value(
    db: Any,
    *,
    install_id: str,
    record_kind: str,
    record_id: str,
    field_key: str,
    value: Any,
) -> Any:
    """Write the value of one extension field on one host record.

    *db* is a session that the caller opened with ``tenant_session(org)``.
    The install must be ``active`` and must declare the field. *value* None
    deletes the row. Answers the value as the store keeps it, or None.
    Each refusal writes nothing. The caller's transaction commits the row.
    """
    kind = _record_kind(record_kind)
    record = _record_id(record_id)
    key = _field_key(field_key)
    install = _install_id(install_id)
    await _bound(db, "a field value", read_committed=True)
    row = (await db.execute(text(_INSTALL_SQL), {
        "install": install, "claim": claim_key(kind, key)})).first()
    if row is None:
        raise InstallNotFound("install_not_found", "No install has this id.")
    if str(row[0]) != "active":
        raise StateRefused(
            "install_not_active", f"An install that is {row[0]} takes no field value.")
    if row[1] is None:
        raise FieldNotDeclared(kind, key)
    field = declared_field(key, json.loads(row[1]))
    params = {"install": install, "kind": kind, "record": record, "key": key}
    if value is None:
        await db.execute(text(_DELETE_ONE_SQL), params)
        return None
    checked, encoded = value_json(field, value)
    await db.execute(text(_UPSERT_SQL), {**params, "type": field.type, "value": encoded})
    return checked


async def get_values(db: Any, *, record_kind: str, record_id: str) -> list[FieldValue]:
    """The extension values of one host record, of active installs only.

    A value whose stored type differs from the current declaration is left
    out. The store does not check that the viewer may see the record or the
    install. The host door of PKG-14 owns that check.
    """
    kind = _record_kind(record_kind)
    record = _record_id(record_id)
    await _bound(db, "a read of field values")
    rows = (await db.execute(text(_GET_SQL), {"kind": kind, "record": record})).all()
    return [FieldValue(install_id=str(r[0]), app_id=str(r[1]), field_key=str(r[2]),
                       field_type=str(r[3]), value=json.loads(r[4])) for r in rows]


async def delete_values(db: Any, *, record_kind: str, record_id: str) -> int:
    """Delete each extension value of one host record, of each install.

    The delete paths of the host records call this (PKG-14). Answers the
    number of rows that it deleted.
    """
    kind = _record_kind(record_kind)
    record = _record_id(record_id)
    await _bound(db, "a deletion of field values")
    result = await db.execute(text(_DELETE_RECORD_SQL), {"kind": kind, "record": record})
    return int(result.rowcount or 0)
