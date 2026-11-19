"""The semantic model of a connection: its one writer (WS-SEM SEM-12).

Spec: ``project-docs/specs/semantic_model.md`` §3.4 and §10.4. Migration: the
file that adds ``connection_semantic_models`` (464 at build). R8 suite:
``tests/unit/test_connection_semantic_model_r8.py``.

**One module names the table.** This is the one module that runs SQL on
``connection_semantic_models``. It never names the credential column of the
registry. It reads one column of ``integration_connections``, the id, to
check and lock the connection. Fence: ``test_semantic_shape_only.py``.

**Shape only.** Each write runs ``crm_sync.catalogue.shape_only`` on the body
before it writes. A problem refuses the write with ``shape:<code>``. This
module keeps no second copy of the checks.

**The callers.** No caller exists in SEM-12. SEM-13 (the profiler) and
SEM-16 (an admin's edit) call :func:`save_draft`. SEM-16 calls
:func:`accept_part` and :func:`approve`, behind ``integrations:manage``.
SEM-15 calls :func:`read_approved`.

**Tenancy (R5).** Each function takes a session that the caller opened with
``tenant_session()``, so RLS binds each statement, and each statement also
names the bound org. No function takes an org argument. The org of a new row
comes from the column DEFAULT, the bound tenant. The flag reads the org of
the bound tenant, never request input.

**The cross-tenant write.** A foreign key check skips RLS. So each write
selects and locks the connection under the bound tenant FIRST
(``_LOCK_SQL``), and the INSERT selects the connection again under the bound
tenant. A missing connection and a connection of another org give the same
refusal, ``connection``, so the refusal leaks nothing.

**The version.** The writer sets ``version``, and the caller never passes it
to :func:`save_draft`. The lock on the connection row orders two writers of
one connection, so two drafts take two numbers.

**Approve.** In one savepoint, :func:`approve` archives the approved row and
then approves the draft, in that order. So the partial unique index never
trips, and a failure of the second step undoes the first.

**The flag.** ``CONNECTION_MODEL_ORGS``, default empty. With the org off,
each write refuses with ``off`` and :func:`read_approved` returns None.
"""

from __future__ import annotations

import copy
import json
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text

from gateway.crm_sync.catalogue import MODEL_SECTIONS, SECTION_PARTS, shape_only

__all__ = [
    "BODY_BYTES_MAX",
    "REFUSE_CONNECTION",
    "REFUSE_OFF",
    "STATE_APPROVED",
    "STATE_ARCHIVED",
    "STATE_DRAFT",
    "TABLE",
    "ApprovedModel",
    "ModelRefused",
    "accept_part",
    "approve",
    "connection_model_allowed",
    "made_by",
    "read_approved",
    "save_draft",
]

TABLE = "connection_semantic_models"
STATE_DRAFT = "draft"
STATE_APPROVED = "approved"
STATE_ARCHIVED = "archived"
#: The largest body, in bytes of its JSON text. The migration holds the same
#: cap in a CHECK.
BODY_BYTES_MAX = 262144

#: The refusal when the org is off the list, or no tenant is bound.
REFUSE_OFF = "off"
#: The refusal for a missing connection and for a connection of another
#: org. The two are the same, so the refusal leaks nothing.
REFUSE_CONNECTION = "connection"

#: A shape hash: hex, as the CHECK of the migration.
_HASH = re.compile(r"[0-9a-f]{16,128}")
#: A role slug, as the CHECK of the migration.
_ROLE = re.compile(r"[a-z][a-z0-9_-]{0,39}")

#: The tenant that the session binds. NULL when no tenant is bound, and then a
#: statement matches no row.
_BOUND_ORG = "CAST(NULLIF(current_setting('app.tenant_id', true), '') AS uuid)"

_BOUND_SQL = "SELECT NULLIF(current_setting('app.tenant_id', true), '')"

#: The connection under the bound tenant, locked until the transaction ends.
#: FORCE RLS on the registry and the org filter both keep it to the bound org.
#: ``FOR NO KEY UPDATE`` orders two writers of one connection, and it does not
#: block the ``FOR KEY SHARE`` of an insert in another table that references
#: the connection (a sync run, a source profile, a calendar row).
_LOCK_SQL = (
    "SELECT id FROM integration_connections "
    f"WHERE id = CAST(:cid AS uuid) AND organization_id = {_BOUND_ORG} "
    "FOR NO KEY UPDATE"
)

_NEXT_SQL = (
    f"SELECT COALESCE(max(version), 0) + 1 FROM {TABLE} "
    f"WHERE organization_id = {_BOUND_ORG} AND connection_id = CAST(:cid AS uuid)"
)

#: The insert names the connection only through a select under the bound
#: tenant, never by its id alone. ``organization_id`` comes from the DEFAULT.
_INSERT_SQL = (
    f"INSERT INTO {TABLE} "
    "(connection_id, version, state, body, shape_hash, made_by, untrusted_origin) "
    f"SELECT c.id, :version, '{STATE_DRAFT}', CAST(:body AS jsonb), :hash, :made_by, "
    ":untrusted FROM integration_connections c "
    f"WHERE c.id = CAST(:cid AS uuid) AND c.organization_id = {_BOUND_ORG} "
    "RETURNING version"
)

_ROW_SQL = (
    f"SELECT state, body FROM {TABLE} "
    f"WHERE organization_id = {_BOUND_ORG} AND connection_id = CAST(:cid AS uuid) "
    "AND version = :version FOR UPDATE"
)

_BODY_SQL = (
    f"UPDATE {TABLE} SET body = CAST(:body AS jsonb), made_by = :made_by, "
    "updated_at = now() "
    f"WHERE organization_id = {_BOUND_ORG} AND connection_id = CAST(:cid AS uuid) "
    f"AND version = :version AND state = '{STATE_DRAFT}'"
)

_ARCHIVE_SQL = (
    f"UPDATE {TABLE} SET state = '{STATE_ARCHIVED}', updated_at = now() "
    f"WHERE organization_id = {_BOUND_ORG} AND connection_id = CAST(:cid AS uuid) "
    f"AND state = '{STATE_APPROVED}'"
)

_APPROVE_SQL = (
    f"UPDATE {TABLE} SET state = '{STATE_APPROVED}', approved_role = :role, "
    "approved_at = now(), updated_at = now() "
    f"WHERE organization_id = {_BOUND_ORG} AND connection_id = CAST(:cid AS uuid) "
    f"AND version = :version AND state = '{STATE_DRAFT}'"
)

_READ_SQL = (
    "SELECT version, body, shape_hash, made_by, approved_role, approved_at, "
    f"untrusted_origin FROM {TABLE} "
    f"WHERE organization_id = {_BOUND_ORG} AND connection_id = CAST(:cid AS uuid) "
    f"AND state = '{STATE_APPROVED}'"
)


class ModelRefused(Exception):
    """A write that the writer refused. ``code`` is the short reason:
    ``off``, ``connection``, ``shape:<code>``, ``shape_hash``, ``origin``,
    ``too_big``, ``version``, ``not_draft``, ``section``, ``part`` or
    ``role``. The text of the exception is the code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ApprovedModel:
    """The approved version of the model of one connection."""

    connection_id: str
    version: int
    body: dict[str, Any]
    shape_hash: str
    made_by: str
    approved_role: str
    approved_at: datetime
    untrusted_origin: bool


def _parse_orgs(raw: str) -> frozenset[str]:
    """The entries of a comma list, trimmed and in lower case."""
    return frozenset(p.strip().lower() for p in raw.split(",") if p.strip())


def connection_model_allowed(organization_id: str | None) -> bool:
    """True when ``CONNECTION_MODEL_ORGS`` holds the org, or holds ``*``.
    Default OFF.

    The one reader of the setting. It reads the settings on each call, so a
    reload (SIGHUP) applies a change with no restart. A fault reads as off.
    The callers in this module give the org of the bound tenant, never
    request input (R5).
    """
    org = str(organization_id or "").strip().lower()
    if not org:
        return False
    try:
        from acb_common import get_settings

        raw = str(getattr(get_settings(), "connection_model_orgs", "") or "")
    except Exception:
        return False
    listed = _parse_orgs(raw)
    return "*" in listed or org in listed


def made_by(body: Mapping[str, Any]) -> str:
    """The highest writer kind in *body*: the last section of
    ``MODEL_SECTIONS`` that holds an item. A body with no item is ``code``."""
    kind = MODEL_SECTIONS[0]
    for section in MODEL_SECTIONS:
        parts = body.get(section)
        if isinstance(parts, Mapping) and any(parts.values()):
            kind = section
    return kind


def _uuid(value: Any) -> str | None:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        return None


def _version(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ModelRefused("version")
    return value


def _json(body: Mapping[str, Any]) -> str:
    out = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    if len(out.encode("utf-8")) > BODY_BYTES_MAX:
        raise ModelRefused("too_big")
    return out


def _shape(body: Any) -> None:
    found = shape_only(body)
    if found is not None:
        raise ModelRefused(f"shape:{found}")


async def _on(db: Any) -> None:
    """Refuse with ``off`` unless the flag holds the org of the bound
    tenant. No bound tenant reads as off."""
    org = (await db.execute(text(_BOUND_SQL))).scalar()
    if not connection_model_allowed(org):
        raise ModelRefused(REFUSE_OFF)


async def _lock(db: Any, cid: str | None) -> str:
    """The connection under the bound tenant, locked. A missing connection
    and a connection of another org give the same refusal."""
    if cid is None:
        raise ModelRefused(REFUSE_CONNECTION)
    row = (await db.execute(text(_LOCK_SQL), {"cid": cid})).first()
    if row is None:
        raise ModelRefused(REFUSE_CONNECTION)
    return cid


async def save_draft(db: Any, connection_id: Any, body: Any, shape_hash: Any,
                     untrusted_origin: Any) -> int:
    """Write *body* as a new draft of the model of *connection_id*, and
    return its version.

    It checks the flag, runs ``shape_only``, checks the hash and the origin,
    then locks the connection under the bound tenant. The version is the
    highest version of the connection plus 1. *db* is a session of
    ``tenant_session()``. Raises :class:`ModelRefused`."""
    await _on(db)
    _shape(body)
    if not isinstance(shape_hash, str) or not _HASH.fullmatch(shape_hash):
        raise ModelRefused("shape_hash")
    if not isinstance(untrusted_origin, bool):
        raise ModelRefused("origin")
    payload = _json(body)
    cid = await _lock(db, _uuid(connection_id))
    version = int((await db.execute(text(_NEXT_SQL), {"cid": cid})).scalar() or 1)
    row = (await db.execute(text(_INSERT_SQL), {
        "cid": cid, "version": version, "body": payload, "hash": shape_hash,
        "made_by": made_by(body), "untrusted": untrusted_origin})).first()
    if row is None:
        raise ModelRefused(REFUSE_CONNECTION)
    return int(row[0])


async def accept_part(db: Any, connection_id: Any, version: Any, section: Any, key: Any,
                      change: Any) -> dict[str, Any]:
    """Set the part *key* of *section* of the draft *version* to *change*, a
    list of items, or remove the part when *change* is None. It runs
    ``shape_only`` on the new body, writes the row again, and returns the
    new body. It refuses a row that is not a draft. Raises
    :class:`ModelRefused`."""
    await _on(db)
    number = _version(version)
    if section not in MODEL_SECTIONS:
        raise ModelRefused("section")
    if not isinstance(key, str) or key not in SECTION_PARTS[section]:
        raise ModelRefused("part")
    cid = await _lock(db, _uuid(connection_id))
    row = (await db.execute(text(_ROW_SQL), {"cid": cid, "version": number})).first()
    if row is None:
        raise ModelRefused("version")
    if row.state != STATE_DRAFT:
        raise ModelRefused("not_draft")
    body = copy.deepcopy(dict(row.body))
    parts = dict(body.get(section) or {})
    if change is None:
        parts.pop(key, None)
    else:
        parts[key] = change
    body[section] = parts
    _shape(body)
    result = await db.execute(text(_BODY_SQL), {
        "cid": cid, "version": number, "body": _json(body), "made_by": made_by(body)})
    if result.rowcount != 1:
        raise ModelRefused("not_draft")
    return body


async def approve(db: Any, connection_id: Any, version: Any, role: Any) -> None:
    """Approve the draft *version* of *connection_id* as an admin of *role*.

    In one savepoint it archives the approved row of the connection, then
    approves the draft, in that order. So the partial unique index never
    trips, and no reader sees two approved rows. It refuses a version that
    is not a draft, and a body that ``shape_only`` refuses. Raises
    :class:`ModelRefused`."""
    await _on(db)
    number = _version(version)
    if not isinstance(role, str) or not _ROLE.fullmatch(role):
        raise ModelRefused("role")
    cid = await _lock(db, _uuid(connection_id))
    row = (await db.execute(text(_ROW_SQL), {"cid": cid, "version": number})).first()
    if row is None:
        raise ModelRefused("version")
    if row.state != STATE_DRAFT:
        raise ModelRefused("not_draft")
    _shape(row.body)
    args = {"cid": cid, "version": number, "role": role}
    async with db.begin_nested():
        await db.execute(text(_ARCHIVE_SQL), args)
        result = await db.execute(text(_APPROVE_SQL), args)
        if result.rowcount != 1:
            raise ModelRefused("not_draft")


async def read_approved(db: Any, connection_id: Any) -> ApprovedModel | None:
    """The approved version of the model of *connection_id* in the bound
    org, or None. It never returns a draft or an archived row (D-SEM-11).
    With the org off the list it reads nothing and returns None."""
    org = (await db.execute(text(_BOUND_SQL))).scalar()
    if not connection_model_allowed(org):
        return None
    cid = _uuid(connection_id)
    if cid is None:
        return None
    row = (await db.execute(text(_READ_SQL), {"cid": cid})).first()
    if row is None:
        return None
    return ApprovedModel(
        connection_id=cid, version=int(row.version), body=dict(row.body),
        shape_hash=str(row.shape_hash), made_by=str(row.made_by),
        approved_role=str(row.approved_role), approved_at=row.approved_at,
        untrusted_origin=bool(row.untrusted_origin))
