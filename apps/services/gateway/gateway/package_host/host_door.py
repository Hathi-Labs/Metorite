"""The host door of the extension values (WS-PKG PKG-14a).

Spec: ``project-docs/specs/package_host.md`` §8.1 (the slot
``record.field``), §8.3 (field extensions) and the PKG-14a row of §10. The
routes are ``routes/projects/package_fields.py`` (a task) and
``routes/crm/package_fields.py`` (a CRM record). Each route checks the host
record with the rule of the host app first, and then calls this module.

**The set of the viewer comes from the resolve.** :func:`visible_fields`
calls :func:`gateway.package_host.resolve.resolve_for`, the one answer to
"which installs does this member see". This module keeps no copy of that
SQL, and it does not edit ``resolve.py``. :func:`fields_of` keeps each
``fields`` entry whose key is ``<record kind>:<field key>``. It drops:

* a system entry (``source`` ``system``). No system package declares a field
  today, so the rule only fails closed;
* an entry whose install needs setup (fail closed, §5.4);
* an entry whose key or declaration the store would refuse.

So the viewer sees the winner of each key (§5.2, PKG-Q25). A private install
of another member, a group install of a group that the viewer is not in,
and a paused, ended or archived install are never in the set.

**A write.** :func:`write_field` accepts only an install and a key that won
for this viewer. An install outside the set is ``InstallNotFound`` (404), so
a hidden install and a missing install give the same answer. An install in
the set that did not win the key is ``FieldNotDeclared`` (422). The store
then checks the set again before its first statement (``field_values.py``).

**Tenancy (R5).** Each function takes the session of the route, which the
route opened with ``tenant_session()``. The viewer is ``user.email`` of the
session, never request input. This module opens no session.

Fences: ``tests/unit/test_package_host_door.py`` (no database) and
``tests/unit/test_package_host_door_r8.py`` (a real database, two orgs,
FORCE RLS).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from acb_common.studio_fields import FieldError, FieldSpec
from fastapi import HTTPException

from gateway.package_host import field_values as fv
from gateway.package_host import resolve
from gateway.package_host.field_values import FIELDS_POINT, FieldNotDeclared
from gateway.package_host.store import InstallError, InstallNotFound

#: The refusals that :func:`refusal` turns into an HTTP answer.
REFUSALS: tuple[type[Exception], ...] = (InstallError, FieldError)

#: The keys that the resolve adds to the payload of a claim (§5.2).
_RESOLVE_KEYS: tuple[str, ...] = ("key", "source", "install_id", "needs_setup")


@dataclass(frozen=True)
class DoorField:
    """One declared field that the viewer sees on a record kind."""

    install_id: str
    field_key: str
    source: str
    field: FieldSpec

    def as_dict(self) -> dict[str, Any]:
        """The field as the route answers it, with no value."""
        return {
            "install_id": self.install_id, "key": self.field_key, "source": self.source,
            "type": self.field.type, "label": self.field.label, "many": self.field.many,
            "options": list(self.field.options) if self.field.options is not None else None,
        }


@dataclass(frozen=True)
class VisibleFields:
    """The winners of one record kind for one viewer."""

    fields: tuple[DoorField, ...]

    @property
    def install_ids(self) -> frozenset[str]:
        """The installs that hold a winner. The store reads only these."""
        return frozenset(f.install_id for f in self.fields)

    def winner(self, install_id: str, field_key: str) -> DoorField | None:
        """The field of *install_id* at *field_key*, when it won, else None."""
        for f in self.fields:
            if f.install_id == install_id and f.field_key == field_key:
                return f
        return None


def fields_of(contributions: Mapping[str, Any], record_kind: str) -> VisibleFields:
    """The ``fields`` winners of *record_kind* in the answer of the resolve.

    *contributions* is ``resolve_for(...)["contributions"]``. Pure: it reads
    no database.
    """
    prefix = f"{record_kind}:"
    out: list[DoorField] = []
    entries = contributions.get(FIELDS_POINT, []) if isinstance(contributions, Mapping) else []
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, Mapping):
            continue
        key, install = entry.get("key"), entry.get("install_id")
        if entry.get("source") in (None, "system") or not isinstance(install, str):
            continue
        if entry.get("needs_setup") is not False:
            continue
        if not isinstance(key, str) or not key.startswith(prefix):
            continue
        field_key = key[len(prefix):]
        payload = {k: v for k, v in entry.items() if k not in _RESOLVE_KEYS}
        try:
            fv.check_field_key(field_key)
            field = fv.declared_field(field_key, payload)
        except (InstallError, FieldError):
            continue
        out.append(DoorField(install_id=install, field_key=field_key,
                             source=str(entry["source"]), field=field))
    return VisibleFields(fields=tuple(out))


async def visible_fields(db: Any, user: Any, record_kind: str) -> VisibleFields:
    """The fields that *user* sees on *record_kind*, from the one resolve."""
    body = await resolve.resolve_for(db, viewer=user.email, can_use=user.can_use_feature)
    return fields_of(body.get("contributions", {}), record_kind)


async def read_fields(
    db: Any, user: Any, *, record_kind: str, record_id: str,
) -> list[dict[str, Any]]:
    """Each field that *user* sees on one host record, with its value or None.

    The route has checked that *user* may see the record.
    """
    seen = await visible_fields(db, user, record_kind)
    values = await fv.get_values(
        db, record_kind=record_kind, record_id=record_id, install_ids=seen.install_ids)
    stored = {(v.install_id, v.field_key): v.value for v in values}
    return [{**f.as_dict(), "value": stored.get((f.install_id, f.field_key))}
            for f in seen.fields]


async def write_field(
    db: Any, user: Any, *, record_kind: str, record_id: str, install_id: str,
    field_key: str, value: Any,
) -> Any:
    """Write one value as *user*. *value* None deletes it.

    The route has checked that *user* may edit the record. Raises
    ``InstallNotFound`` for an install that the viewer does not see, and
    ``FieldNotDeclared`` for a key that did not win for the viewer.
    """
    install = fv.install_id_of(install_id)
    key = fv.check_field_key(field_key)
    seen = await visible_fields(db, user, record_kind)
    if install not in seen.install_ids:
        raise InstallNotFound("install_not_found", "No install has this id.")
    if seen.winner(install, key) is None:
        raise FieldNotDeclared(record_kind, key)
    return await fv.set_value(
        db, install_id=install, record_kind=record_kind, record_id=record_id,
        field_key=key, value=value, install_ids=seen.install_ids)


def refusal(exc: InstallError | FieldError, viewer: str) -> HTTPException:
    """The HTTP answer of a refusal of the store or the door.

    An ``InstallError`` answers its own status with ``detail_for`` of the
    viewer, and nothing else. A ``FieldError`` is 422, and it names the key.
    """
    if isinstance(exc, InstallError):
        return HTTPException(status_code=exc.status, detail=exc.detail_for(viewer))
    return HTTPException(status_code=422, detail={
        "code": "bad_value", "field": exc.field, "message": exc.message})
