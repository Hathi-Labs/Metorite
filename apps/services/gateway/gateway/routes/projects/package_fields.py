"""Projects · the extension values of a package on a task (WS-PKG PKG-14a).

Spec: ``project-docs/specs/package_host.md`` §8.3 and the PKG-14a row of §10.

    GET /projects/tasks/{task_id}/package-fields
    PUT /projects/tasks/{task_id}/package-fields/{install_id}/{field_key}

**Dark.** The first line of each handler reads
``package_host.flags.package_host_allowed``. While ``PACKAGE_HOST_ORGS``
leaves the org out, each route answers 404 and opens no session.

**The task comes first.** Each handler runs ``resolve_visibility`` and
``load_visible_task``, the load of ``patch_task``. A task that the caller
cannot see is 404 ``Task not found``, also when an org install declares a
field on it. So the rule of who may edit a task is the rule of Projects
(PKG-Q26), and this module adds no second rule.

**The values come from the door.** ``package_host/host_door.py`` reads the set
of installs from the one resolve and calls the field store. This module runs
no SQL of its own. The body of the PUT is ``{"value": ...}``, and a value
``null`` deletes the row.

The chat has no tool for these routes yet. ``skill_projects/manifest.py``
holds them as class X until PKG-14b. Fences:
``tests/unit/test_package_host_door.py`` and
``tests/unit/test_package_host_door_r8.py``.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from acb_auth import UserContext, get_current_user
from fastapi import Depends, HTTPException
from gateway.package_host.flags import package_host_allowed
from gateway.routes.projects.core import (
    _tenant_session,
    load_visible_task,
    resolve_visibility,
    router,
)
from pydantic import BaseModel, ConfigDict

#: The record kind of a task (§8.3).
TASK_KIND = "task"


class PackageFieldIn(BaseModel):
    """The body of a write: one value, or ``null`` to delete it."""

    model_config = ConfigDict(extra="forbid")

    value: Any


def _switch(user: UserContext) -> None:
    if not package_host_allowed(user.organization_id):
        raise HTTPException(status_code=404, detail="Not found")


def _door() -> Any:
    """The door module, imported at call time.

    ``package_host/store.py`` imports ``routes/projects/core.py``, and the
    package ``routes/projects`` imports this module. A top-level import of
    the door would close that loop while the store is half loaded.
    """
    from gateway.package_host import host_door

    return host_door


@router.get("/tasks/{task_id}/package-fields")
async def task_package_fields(
    task_id: UUID, user: UserContext = Depends(get_current_user),
) -> dict:
    """The declared fields that the caller sees on one task, with their values."""
    _switch(user)
    host_door = _door()
    record = str(task_id)
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        await load_visible_task(db, vis, record)
        fields = await host_door.read_fields(db, user, record_kind=TASK_KIND, record_id=record)
    return {"record_kind": TASK_KIND, "record_id": record, "fields": fields}


@router.put("/tasks/{task_id}/package-fields/{install_id}/{field_key}")
async def set_task_package_field(
    task_id: UUID, install_id: str, field_key: str, payload: PackageFieldIn,
    user: UserContext = Depends(get_current_user),
) -> dict:
    """Write one extension value on one task, or delete it with ``null``."""
    _switch(user)
    host_door = _door()
    record = str(task_id)
    try:
        async with _tenant_session() as db:
            vis = await resolve_visibility(db, user)
            await load_visible_task(db, vis, record)
            stored = await host_door.write_field(
                db, user, record_kind=TASK_KIND, record_id=record, install_id=install_id,
                field_key=field_key, value=payload.value)
    except host_door.REFUSALS as exc:
        raise host_door.refusal(exc, user.email) from None
    return {"install_id": str(UUID(install_id)), "key": field_key, "value": stored}
