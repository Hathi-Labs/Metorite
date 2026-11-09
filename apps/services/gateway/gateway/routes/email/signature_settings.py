"""The writer of a mailbox signature (WS-17 SG-3a, D-EM-64, D-EM-71).

Spec: ``project-docs/specs/email_app_master_plan.md`` §23.3.10, §23.3.3 and
§23.4.

* ``GET /email/signature/settings?account_id=`` answers the mode, the layout,
  the fields and ``reply_mode`` of one mailbox, the five layout ids, and
  whether this server can host an image. A mailbox with no row answers
  ``custom``, ``null``, ``{}`` and ``full``.
* ``PUT /email/signature/settings`` stores them, and answers the saved row
  with the ``html`` and the ``text`` that ``signature_render.render`` gives.

Both routes are dark. While ``signature.templates_on`` is false, each answers
404 before FastAPI reads the body (:func:`_dark_route`), and nothing reads or
writes the table. Both read the mailbox with ``resolve_account_filter`` and
prove send access with ``_assert_account_owner(level="send")`` inside
``_tenant_session()``. A caller with no send access gets 404, and nothing is
written.

The writer stores an image only at a hosted address of this server and of
the bound tenant (:func:`image_refusal`). ``signature.save_settings`` is the
one write. The tenant comes from the bound session, and no request field
names it (R5). A ``company`` body is refused until SG-4. A ``reply_mode`` of
``short`` is stored as given, and its effect starts at SG-5.

Fences: ``tests/unit/test_email_signature_writer.py`` and
``tests/unit/test_email_signature_settings_r8.py`` (R8).
"""
from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from typing import Any, Literal

from acb_auth import UserContext, get_current_user
from acb_common.db import current_tenant
from acb_common.pg_text import storable
from fastapi import Depends, HTTPException, Query
from gateway.routes.email import signature, signature_render
from gateway.routes.email.account_ref import resolve_account_filter
from gateway.routes.email.core import _assert_account_owner, _log, _tenant_session, router
from gateway.routes.email.signature_images import PUBLIC_ROUTE, public_base_url
from gateway.routes.email.signature_preview import fields_within_bound
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.exc import DBAPIError

#: The two image fields of §23.3.1.
IMAGE_FIELDS: tuple[str, ...] = ("logo", "photo")
#: The path of a hosted image up to the org: ``/email/signature-image/``.
IMAGE_PATH = PUBLIC_ROUTE.split("{org}", 1)[0]

#: The sentences of a refusal. Each one names the next step.
COMPANY_NOT_YET = (
    "The company signature is not ready yet. Choose a layout, or keep your "
    "own signature.")
LAYOUT_NEEDED = (
    "A layout signature needs a layout. Choose one of the five layouts, then "
    "save again.")
NOTHING_TO_SHOW = (
    "This signature has nothing to show. Add your name, then save again.")
NOT_JSON = (
    "The signature fields hold a value that cannot be saved. Remove it, then "
    "save again.")
TOO_LARGE = (
    "The signature fields are too large to save. Remove some text, then save "
    "again.")
TRY_AGAIN = "Another save of this signature ran at the same time. Try again."
_NO_HOST = (
    "This server cannot host signature images yet, so your signature cannot "
    "hold a {what}. Remove the {what}, or ask your admin to set up images.")
_EMBEDDED = (
    "The {what} is an embedded image, and Gmail does not show it. Upload the "
    "picture with the image button, then save again.")
_NOT_HTTPS = (
    "The {what} address does not use https. Upload the picture with the image "
    "button, then save again.")
_NOT_OURS = (
    "The {what} is not an image of your organization on this server. Upload "
    "the picture with the image button, then save again.")
_NOT_UPLOADED = (
    "The {what} is not an uploaded image. Upload the picture with the image "
    "button, then save again.")


class SignatureSettingsRequest(BaseModel):
    """The signature of one mailbox. Any other key is refused."""

    model_config = ConfigDict(extra="forbid")

    account_id: str
    mode: Literal["custom", "template", "company"]
    layout: Literal["classic", "stacked", "minimal", "photo", "compact"] | None = None
    fields: dict[str, Any] = Field(default_factory=dict)
    reply_mode: Literal["full", "short", "none"]

    @field_validator("fields")
    @classmethod
    def _storable_and_small(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Each NUL and lone surrogate becomes U+FFFD (``pg_text.storable``),
        a value that is not JSON is refused, and the preview's bound holds."""
        value = storable(value)
        try:
            json.dumps(value, allow_nan=False)
        except (TypeError, ValueError):
            raise ValueError(NOT_JSON) from None
        return fields_within_bound(value)


def _dark() -> None:
    """404 while ``signature.templates_on`` is false, the one flag reader."""
    if not signature.templates_on():
        raise HTTPException(status_code=404, detail="Not Found")


async def _dark_route(_user: UserContext = Depends(get_current_user)) -> None:
    """The route dependency of :func:`_dark`. FastAPI runs it before it reads
    the query and the body, so a dark route answers 404 to any request. The
    session member is resolved first, and that binds the tenant."""
    _dark()


def _member(user: UserContext) -> str:
    return (getattr(user, "email", None) or "").strip().lower() or "anonymous"


def _tenant_id() -> str | None:
    """The bound tenant in the canonical form of a hosted address, or None."""
    tenant = current_tenant()
    try:
        return str(uuid.UUID(str(tenant))) if tenant else None
    except ValueError:
        return None


def _present(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    return value is not None and value != {} and value != []


def image_refusal(
    fields: Mapping[str, Any], *, base: str | None, org: str | None,
) -> str | None:
    """The sentence that refuses an image of ``fields``, or None.

    An image (``logo`` or ``photo``) passes only when its ``url`` starts with
    ``base + "/email/signature-image/" + org + "/"``, holds no other ``/``,
    and passes the renderer's shape check (``hosted_image_src``). ``base`` is
    ``public_base_url()``, and with no base each image is refused. ``org`` is
    the bound tenant, never a request field."""
    for what in IMAGE_FIELDS:
        value = fields.get(what)
        if not _present(value):
            continue
        if base is None:
            _log.warning("email.signature_image_no_base",
                         detail="GATEWAY_PUBLIC_URL is unset or is not https")
            return _NO_HOST.format(what=what)
        url = value.get("url") if isinstance(value, Mapping) else value
        if not isinstance(url, str) or not url.strip():
            return _NOT_UPLOADED.format(what=what)
        lower = url.strip().lower()
        if lower.startswith("data:"):
            return _EMBEDDED.format(what=what)
        if not lower.startswith("https:"):
            return _NOT_HTTPS.format(what=what)
        prefix = f"{base}{IMAGE_PATH}{org}/"
        if org is None or not url.startswith(prefix) or "/" in url[len(prefix):]:
            return _NOT_OURS.format(what=what)
        if signature_render.hosted_image_src(value) != url:
            return _NOT_UPLOADED.format(what=what)
    return None


def _render(layout: str | None, fields: Mapping[str, Any]) -> tuple[str, str]:
    """The HTML and the text of the saved layout, or ``("", "")`` with none."""
    if layout is None:
        return "", ""
    return signature_render.render(layout, fields)


def _sqlstate(exc: BaseException) -> str:
    """The SQLSTATE under a SQLAlchemy error, as ``projects.core.is_transient``
    walks it: the driver error is ``orig`` or the cause."""
    seen: BaseException | None = exc
    for _ in range(5):
        if seen is None:
            return ""
        code = getattr(seen, "sqlstate", None) or getattr(seen, "pgcode", None)
        if isinstance(code, str) and code:
            return code
        seen = getattr(seen, "orig", None) or seen.__cause__
    return ""


def _write_refusal(exc: DBAPIError) -> HTTPException | None:
    """The 4xx of a write that the database refused, or None for a fault.

    A deadlock, a serialization failure or a lock that timed out is 409. A
    CHECK of migration 393 or a value that jsonb refuses is 422. A mailbox
    that a disconnect removed during the save is 404."""
    from gateway.routes.projects.core import is_transient

    if is_transient(exc):
        return HTTPException(status_code=409, detail=TRY_AGAIN)
    state = _sqlstate(exc)
    if state == "23503":
        return HTTPException(status_code=404, detail="Account not found")
    if state == "23514":
        return HTTPException(status_code=422, detail=TOO_LARGE)
    if state.startswith("22"):
        return HTTPException(status_code=422, detail=NOT_JSON)
    return None


@router.get("/signature/settings", dependencies=[Depends(_dark_route)])
async def get_signature_settings(
    account_id: str = Query(...),
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """The signature row of one mailbox, the layouts, and the upload state."""
    _dark()
    member = _member(user)
    async with _tenant_session() as db:
        account_id = await resolve_account_filter(db, account_id, member, required=True)
        await _assert_account_owner(db, account_id, member, level="send")
        stored = await signature.stored_settings(db, account_id)
    return {
        "account_id": account_id,
        **stored,
        "layouts": list(signature_render.LAYOUTS),
        "image_upload": public_base_url() is not None,
    }


@router.put("/signature/settings", dependencies=[Depends(_dark_route)])
async def put_signature_settings(
    req: SignatureSettingsRequest,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Store the signature row of one mailbox, and answer it with its render."""
    _dark()
    if req.mode == "company":
        raise HTTPException(status_code=422, detail=COMPANY_NOT_YET)
    if req.mode == "template" and req.layout is None:
        raise HTTPException(status_code=422, detail=LAYOUT_NEEDED)
    refusal = image_refusal(req.fields, base=public_base_url(), org=_tenant_id())
    if refusal is not None:
        raise HTTPException(status_code=422, detail=refusal)
    if req.mode == "template" and not _render(req.layout, req.fields)[0]:
        # The send would give the stored signature, not this one (§23.3.3).
        raise HTTPException(status_code=422, detail=NOTHING_TO_SHOW)
    member = _member(user)
    try:
        async with _tenant_session() as db:
            account_id = await resolve_account_filter(
                db, req.account_id, member, required=True)
            await _assert_account_owner(db, account_id, member, level="send")
            saved = await signature.save_settings(
                db, account_id, mode=req.mode, layout=req.layout,
                fields=req.fields, reply_mode=req.reply_mode)
    except DBAPIError as exc:
        mapped = _write_refusal(exc)
        if mapped is None:
            raise
        raise mapped from None
    html, text = _render(saved["layout"], saved["fields"])
    return {"account_id": account_id, **saved, "html": html, "text": text}


__all__ = [
    "IMAGE_FIELDS",
    "SignatureSettingsRequest",
    "get_signature_settings",
    "image_refusal",
    "put_signature_settings",
]
