"""CRM · the extension values of a package on a record (WS-PKG PKG-14a).

Spec: ``project-docs/specs/package_host.md`` §8.3 and the PKG-14a row of §10.

    GET /crm/{leads,deals,contacts,organizations}/{id}/package-fields
    PUT /crm/{leads,deals,contacts,organizations}/{id}/package-fields/{install_id}/{field_key}

**The routes are on the CRM router.** So the three router dependencies of
``core.py`` run first: the kill switch (404), the ``feature:crm`` gate (403)
and the access rule of ``access.py`` (403). The CRM router gate
(``require_crm_access``) opens a session before the flag check, so a member
without CRM access gets 403 even while the flag is off. Then the first line of
each handler reads ``package_host.flags.package_host_allowed``, and an org
outside ``PACKAGE_HOST_ORGS`` gets 404.

**The record.** Each handler loads the record with ``require_row`` (404). The
CRM has no visibility rule for each record: each member who passes the
router gates sees each record. So this module adds no narrower rule
(PKG-Q26). A write also calls ``assert_writable`` with the loaded row, so a
record that a provider owns is 409 in Mirror mode.

**Literal paths.** Each entity has its own path, as in ``records.py``, and
:data:`CRM_KINDS` is the one map from a record kind of §8.3 to an entity. The
values come from ``package_host/host_door.py``, and this module runs no SQL
of its own.

Fences: ``tests/unit/test_package_host_door.py`` and
``tests/unit/test_package_host_door_r8.py``.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from acb_auth import UserContext, get_current_user
from fastapi import Depends, HTTPException
from gateway.package_host import host_door
from gateway.package_host.flags import package_host_allowed
from gateway.routes.crm.core import (
    CONTACTS,
    DEALS,
    LEADS,
    ORGANIZATIONS,
    Entity,
    _tenant_session,
    assert_writable,
    current_tenant,
    require_row,
    router,
)
from pydantic import BaseModel, ConfigDict

#: The record kind of §8.3 → the CRM entity. The one map.
CRM_KINDS: dict[str, Entity] = {
    "crm.lead": LEADS,
    "crm.contact": CONTACTS,
    "crm.company": ORGANIZATIONS,
    "crm.deal": DEALS,
}

class PackageFieldIn(BaseModel):
    """The body of a write: one value, or ``null`` to delete it."""

    model_config = ConfigDict(extra="forbid")

    value: Any


def _switch(user: UserContext) -> None:
    if not package_host_allowed(user.organization_id):
        raise HTTPException(status_code=404, detail="Not found")


async def read_record_fields(kind: str, record_id: UUID, user: UserContext) -> dict:
    """The declared fields that *user* sees on one CRM record, with values."""
    _switch(user)
    entity = CRM_KINDS[kind]
    record = str(record_id)
    async with _tenant_session() as db:
        await require_row(db, entity.table, record, entity.label)
        fields = await host_door.read_fields(db, user, record_kind=kind, record_id=record)
    return {"record_kind": kind, "record_id": record, "fields": fields}


async def write_record_field(
    kind: str, record_id: UUID, install_id: str, field_key: str, value: Any,
    user: UserContext,
) -> dict:
    """Write one extension value on one CRM record, or delete it with None."""
    _switch(user)
    entity = CRM_KINDS[kind]
    record = str(record_id)
    try:
        async with _tenant_session() as db:
            row = await require_row(db, entity.table, record, entity.label)
            await assert_writable(db, current_tenant(), entity, None, row=row)
            stored = await host_door.write_field(
                db, user, record_kind=kind, record_id=record, install_id=install_id,
                field_key=field_key, value=value)
    except host_door.REFUSALS as exc:
        raise host_door.refusal(exc, user.email) from None
    return {"install_id": str(UUID(install_id)), "key": field_key, "value": stored}


def _mount(kind: str, entity: Entity) -> None:
    """The GET and the PUT of one entity, at literal paths."""

    async def read(
        record_id: UUID, user: UserContext = Depends(get_current_user),
    ) -> dict:
        return await read_record_fields(kind, record_id, user)

    async def write(
        record_id: UUID, install_id: str, field_key: str, payload: PackageFieldIn,
        user: UserContext = Depends(get_current_user),
    ) -> dict:
        return await write_record_field(
            kind, record_id, install_id, field_key, payload.value, user)

    base = f"/{entity.slug}/{{record_id}}/package-fields"
    router.add_api_route(
        base, read, methods=["GET"], name=f"crm_{entity.slug}_package_fields")
    router.add_api_route(
        base + "/{install_id}/{field_key}", write, methods=["PUT"],
        name=f"crm_{entity.slug}_set_package_field")


for _kind, _entity in CRM_KINDS.items():
    _mount(_kind, _entity)
