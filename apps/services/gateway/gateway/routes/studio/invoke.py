"""The action door of AI Studio, first cut (STU-B2a, ``ai_studio.md`` §3.2).

``POST /studio/apps/{app_id}/actions/{action}`` runs one action that a Studio
package declares. The route registers on the router of ``actions.py``, as
``skills.py`` does, so ``main.py`` mounts no second router. The gate
``require_feature_router("workflows")`` and the prefix ``/studio`` hold here.

**The order of the checks.** Each check runs before the next one:

1. The switch. An org outside ``STUDIO_ORGS`` gets 404 ``Not found``.
2. The package. ``packages_store.get_package`` reads it for the caller. A
   package of another member or of another org, an App Workshop app and an id
   that names nothing all get 404 ``Not found``. The answer names nothing.
3. The declaration. The path names an ``actions[].id`` of the manifest. An
   id that the manifest does not declare gets 403 ``not_declared``.
4. The class. An ``object:<name>.<op>`` action takes its class from
   :data:`~acb_common.studio_manifest.OBJECT_OP_CLASSES`. Each other source
   takes its class from the B1 catalog, ``actions.catalog_rows``. Those two
   are the only sources of a class. Class X gets 403 ``class_x``, and an id
   that the catalog does not serve gets 403 ``not_in_catalog``.
5. The approval. Class B and class C get 403 ``approval_required``. No card
   exists yet (B2c), so nothing runs.
6. The executor. B2a opens one executor: ``object:<name>.get``, through
   ``objects_store.get_record`` with the package id. Each other class A
   action gets 403 ``source_not_open`` (B2b names the next executors).

**Tenancy and the member (R5).** The org is the bound session
(``_tenant_session()``), and the member is ``user.email``. No check reads an
org or a member from the path, the query or the body. A key such as
``organization_id`` in a body changes nothing.

**The audit.** Each call that reaches a package of the caller writes one
``app_audit`` row (kind ``action``) through ``record_app_audit_bound``. The
row holds the action id, its ``uses``, its class and the outcome, and no
record data. A 404 writes no row.
"""

from __future__ import annotations

import dataclasses
import uuid
from typing import Any

from acb_auth import UserContext, get_current_user
from acb_common.studio_manifest import OBJECT_OP_CLASSES, UsesRef, parse_uses
from fastapi import Body, Depends, HTTPException
from gateway.db import tenant_session as _tenant_session
from gateway.routes.apps._common import record_app_audit_bound
from gateway.routes.studio import actions, flags
from gateway.routes.studio.actions import NOT_FOUND, router
from gateway.studio import objects_store, packages_store

__all__ = ["OPEN_EXECUTORS", "REFUSALS", "router"]

#: The executors that B2a opens, as (source, op). An object ``get`` only.
OPEN_EXECUTORS: frozenset[tuple[str, str | None]] = frozenset({("object", "get")})

NOT_DECLARED = "not_declared"
CLASS_X = "class_x"
NOT_IN_CATALOG = "not_in_catalog"
APPROVAL_REQUIRED = "approval_required"
SOURCE_NOT_OPEN = "source_not_open"

#: The code of each 403 and one sentence that a model can act on.
REFUSALS: dict[str, str] = {
    NOT_DECLARED: "This app declares no action with this id, so use an id from its manifest.",
    CLASS_X: "This action is class X, so no app can run it.",
    NOT_IN_CATALOG: (
        "The catalog does not serve this action now, so change the action in the manifest."),
    APPROVAL_REQUIRED: (
        "This action writes data and needs an approval, which this door cannot ask for yet."),
    SOURCE_NOT_OPEN: "This door runs only the get action of an object for now.",
}

BAD_RECORD_ID = "Send the record id as a UUID in the key id of a JSON object."


@dataclasses.dataclass
class _Outcome:
    """What one call did. The audit row and the answer come from it."""

    action: str
    uses: str | None = None
    cls: str | None = None
    refusal: str | None = None
    record: objects_store.Record | None = None
    missing: bool = False
    invalid: bool = False


def _app_id(raw: str) -> str:
    try:
        return str(uuid.UUID(raw))
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(status_code=422, detail="The app id must be a UUID") from None


def _declared(manifest: dict[str, Any], action: str) -> dict[str, Any] | None:
    """The ``actions[]`` entry of *manifest* whose id is *action*, or None."""
    found = manifest.get("actions")
    if not isinstance(found, list):
        return None
    for entry in found:
        if isinstance(entry, dict) and entry.get("id") == action:
            return entry
    return None


def refusal_for(ref: UsesRef, cls: str | None) -> str | None:
    """The refusal code of an action of *ref* and class *cls*, or None when
    B2a runs it. *cls* is None when no source gives the action a class."""
    if cls is None:
        return NOT_IN_CATALOG
    if cls == "X":
        return CLASS_X
    if cls in ("B", "C"):
        return APPROVAL_REQUIRED
    if cls != "A" or (ref.source, ref.op) not in OPEN_EXECUTORS:
        return SOURCE_NOT_OPEN
    return None


async def _catalog_class(uses: str) -> str | None:
    """The class that the B1 catalog gives *uses*, or None. It opens its own
    session, so the caller runs it after its own session closes."""
    for row in await actions.catalog_rows():
        if row.get("id") == uses:
            return str(row.get("class"))
    return None


def _record_id(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    raw = payload.get("id")
    return raw if isinstance(raw, str) else None


async def _run_object_get(db: Any, package: packages_store.Package, ref: UsesRef,
                          payload: Any, out: _Outcome) -> None:
    """Read one record of the object *ref* of *package*. The id comes from
    the body, checked by the id rule of C1."""
    rid = _record_id(payload)
    if rid is None:
        out.invalid = True
        return
    try:
        # The store checks each id before its statement, so a refusal here
        # leaves the session usable.
        obj = await objects_store.get_object_by_name(
            db, str(ref.object), package_id=package.id)
        rec = None if obj is None else await objects_store.get_record(
            db, obj.id, rid, package_id=package.id)
    except objects_store.ObjectStoreError:
        out.invalid = True
        return
    if rec is None:
        out.missing = True
    else:
        out.record = rec


def _audit_detail(out: _Outcome) -> dict[str, Any]:
    """The audit row of *out*: the action, its class and the outcome. It holds
    no record data and no value of the body."""
    if out.refusal is not None:
        outcome, reason = "refused", out.refusal
    elif out.invalid:
        outcome, reason = "refused", "bad_input"
    elif out.missing:
        outcome, reason = "not_found", None
    else:
        outcome, reason = "ran", None
    detail: dict[str, Any] = {"action": out.action, "uses": out.uses, "class": out.cls,
                              "outcome": outcome}
    if reason is not None:
        detail["reason"] = reason
    return detail


@router.post("/apps/{app_id}/actions/{action}")
async def invoke_action(
    app_id: str,
    action: str,
    payload: Any = Body(None),
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Run the action *action* of the caller's package *app_id*. See the
    module docstring for the order of the checks."""
    if not flags.studio_allowed(user.organization_id):
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    pid = _app_id(app_id)
    out = _Outcome(action=action)
    ref: UsesRef | None = None
    async with _tenant_session() as db:
        package = await packages_store.get_package(db, user.email, pid)
        if package is None:
            raise HTTPException(status_code=404, detail=NOT_FOUND)
        entry = _declared(package.manifest, action)
        ref = None if entry is None else parse_uses(entry.get("uses"))
        if entry is None or ref is None:
            out.refusal = NOT_DECLARED
        else:
            out.uses = str(entry["uses"])
            if ref.source == "object":
                out.cls = OBJECT_OP_CLASSES.get(str(ref.op))
                out.refusal = refusal_for(ref, out.cls)
                if out.refusal is None:
                    await _run_object_get(db, package, ref, payload, out)
    if ref is not None and out.uses is not None and ref.source != "object":
        out.cls = await _catalog_class(out.uses)
        out.refusal = refusal_for(ref, out.cls)
    await record_app_audit_bound(app_id=package.id, user_email=user.email, kind="action",
                                 detail=_audit_detail(out))
    if out.refusal is not None:
        raise HTTPException(status_code=403, detail={
            "code": out.refusal, "message": REFUSALS[out.refusal]})
    if out.invalid:
        raise HTTPException(status_code=422, detail=BAD_RECORD_ID)
    if out.missing or out.record is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    return {"record": dataclasses.asdict(out.record)}
