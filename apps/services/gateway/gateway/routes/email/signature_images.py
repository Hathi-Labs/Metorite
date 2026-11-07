"""Hosted signature images: upload, retire and the public route (WS-17 SG-1).

Spec: ``project-docs/specs/email_app_master_plan.md`` §23.3.4, §23.4,
D-EM-67, D-EM-68 and D-EM-70. Migration: ``email_signature_images``.

This module is the ONE writer of ``email_signature_images``.

* ``POST /email/signature/images`` takes one file. The member must be able to
  send from the mailbox that ``account_id`` names. The file goes through
  ``gateway/signature_image.py`` (the sniff in the request, the decode in a
  confined child), and the row holds the child's output. No session is open
  while the child runs.
* ``DELETE /email/signature/images/{id}`` sets ``retired_at``. The uploader or
  an admin (``admin:settings:manage``) may call it. A miss and a refusal are
  the same 404. The image stays served.
* ``GET /email/signature-image/{org}/{token}.{ext}`` is PUBLIC, with no
  sign-in and no feature gate (``main.PUBLIC_ROUTES``, the email router's
  ``exempt`` list, ``GATED_ROUTERS`` of ``test_org_access_enforcement.py``).
  The token is the capability (D-EM-68). The route checks that ``org`` is a
  UUID, binds ``tenant_session(org)`` and selects the one row whose token
  matches, under RLS. It never calls ``get_db``. A bad org, a bad token and a
  wrong ``ext`` give the same 404. It writes no log line of its own, so a
  fetch of a logo counts nothing.

The hosted base is ``GATEWAY_PUBLIC_URL``, the base of the Graph webhook URL.
With no https base the upload refuses and says why, because a save keeps only
an https image (D-EM-70). Treat the base as permanent once a logo went out.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import os
import re
import secrets
import uuid
from collections.abc import Mapping
from typing import Any

from acb_auth import UserContext, get_current_user
from acb_common.db import current_tenant
from fastapi import Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from gateway import signature_image
from gateway.avatar import MAX_UPLOAD_BYTES
from gateway.routes.email import signature as sig
from gateway.routes.email.account_ref import resolve_account_filter
from gateway.routes.email.core import _assert_account_owner, _log, _tenant_session, router
from sqlalchemy import text

#: The images that one organization holds, and that one member uploads. A
#: retired image counts, because it stays stored and served (D-EM-67).
ORG_CAP = 100
MEMBER_CAP = 20

#: The route template of the public image, as FastAPI registers it. The same
#: string is in ``main.PUBLIC_ROUTES`` and in the email router's ``exempt``.
PUBLIC_ROUTE = "/email/signature-image/{org}/{token}.{ext}"

#: The admin capability that may retire an image of another member.
ADMIN_CAPABILITY = "admin:settings:manage"

_EXT = {"image/png": "png", "image/jpeg": "jpg"}
#: 256 bits as URL-safe base64 with no padding (``secrets.token_urlsafe(32)``).
_TOKEN = re.compile(r"[A-Za-z0-9_-]{43}")

#: The headers of each served image (§23.3.4). The URL never changes and the
#: bytes behind it never change, so every cache may keep it for a year.
PUBLIC_HEADERS: Mapping[str, str] = {
    "Cache-Control": "public, max-age=31536000, immutable",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'none'",
    "Cross-Origin-Resource-Policy": "cross-origin",
}

#: The member's sentence when the server has no https base (§23.3.9). It names
#: no setting: the operator detail goes to the log (``_no_base``).
NOT_SET_UP = "Image upload is not set up on this server yet. Ask your admin."
_ORG_FULL = (
    f"Your organization has {ORG_CAP} signature images, the limit. "
    "Use an image that is in a signature already."
)
_MEMBER_FULL = (
    f"You have uploaded {MEMBER_CAP} signature images, the limit. "
    "Use an image that you uploaded before."
)
_BAD_DATA_IMAGE = (
    "One image in your signature could not be read. Remove it, then upload "
    "the picture again with the image button."
)

#: The one lock of the caps, for the bound tenant. The org comes from the
#: session, never from the request.
_LOCK_SQL = (
    "SELECT pg_advisory_xact_lock(hashtext("
    "'email_signature_images:' || current_setting('app.tenant_id', true)))"
)
_SAME_SQL = (
    "SELECT id, kind, content_type, width, height, public_token, retired_at "
    "FROM email_signature_images WHERE sha256 = :sha"
)
_UNRETIRE_SQL = (
    "UPDATE email_signature_images SET retired_at = NULL "
    "WHERE id = :id AND retired_at IS NOT NULL"
)
_COUNT_SQL = (
    "SELECT count(*) AS org_count, "
    "count(*) FILTER (WHERE created_by = :me) AS mine "
    "FROM email_signature_images"
)
_INSERT_SQL = (
    "INSERT INTO email_signature_images "
    "(created_by, kind, content_type, width, height, bytes, sha256, public_token) "
    "VALUES (:me, :kind, :ct, :w, :h, :bytes, :sha, :token) "
    "RETURNING id"
)
#: The retire binds who may call it INTO the statement (gateway AGENTS.md,
#: contract rule 3): the uploader, or a caller with the admin capability.
RETIRE_SQL = (
    "UPDATE email_signature_images "
    "SET retired_at = COALESCE(retired_at, now()) "
    "WHERE id = CAST(:id AS uuid) AND (created_by = :me OR :admin) "
    "RETURNING id, retired_at"
)
#: The public read. RLS of ``tenant_session(org)`` binds the org, and the
#: statement names it too (defence in depth, as ``_assert_account_owner``).
PUBLIC_SQL = (
    "SELECT content_type, bytes FROM email_signature_images "
    "WHERE organization_id = CAST(:org AS uuid) AND public_token = :token"
)


def public_base_url() -> str | None:
    """The https base of the hosted images, or ``None`` when there is none.

    It reads ``GATEWAY_PUBLIC_URL``, as the Graph webhook URL does
    (``transport/sync.py``). A save keeps an https image only, so an http
    base is no base."""
    base = os.environ.get("GATEWAY_PUBLIC_URL", "").strip().rstrip("/")
    return base if base.lower().startswith("https://") and len(base) > 8 else None


def _no_base(lead: str = "") -> HTTPException:
    """The 503 of a server with no https base. The member reads a plain
    sentence. The operator reads the setting in the log."""
    _log.warning("email.signature_image_no_base",
                 detail="GATEWAY_PUBLIC_URL is unset or is not https")
    return HTTPException(status_code=503, detail=lead + NOT_SET_UP)


def image_url(base: str, org: str, token: str, content_type: str) -> str:
    """The public URL of one image. It never changes."""
    return f"{base}/email/signature-image/{org}/{token}.{_EXT[content_type]}"


def _member(user: UserContext) -> str:
    return (getattr(user, "email", None) or "").strip().lower()


async def store_image(
    encoded: signature_image.Encoded, *, kind: str, member: str, base: str,
) -> dict[str, Any]:
    """Write one encoded image under the bound tenant, and answer its URL.

    The same bytes in the same org give the same row, so a second upload of
    a logo holds no second copy and counts no second time. Such a row that
    was retired comes back. The caps run under the advisory lock of the org,
    so two uploads at once cannot pass a cap together."""
    sha = hashlib.sha256(encoded.data).hexdigest()
    org = current_tenant()
    async with _tenant_session() as db:
        await db.execute(text(_LOCK_SQL))
        same = (await db.execute(text(_SAME_SQL), {"sha": sha})).fetchone()
        if same is not None:
            if same.retired_at is not None:
                await db.execute(text(_UNRETIRE_SQL), {"id": same.id})
            image_id, token = str(same.id), same.public_token
            content_type, width, height = same.content_type, same.width, same.height
            kind = same.kind
        else:
            counts = (await db.execute(text(_COUNT_SQL), {"me": member})).fetchone()
            if int(getattr(counts, "org_count", 0) or 0) >= ORG_CAP:
                raise HTTPException(status_code=409, detail=_ORG_FULL)
            if int(getattr(counts, "mine", 0) or 0) >= MEMBER_CAP:
                raise HTTPException(status_code=409, detail=_MEMBER_FULL)
            token = secrets.token_urlsafe(32)
            row = (await db.execute(text(_INSERT_SQL), {
                "me": member, "kind": kind, "ct": encoded.content_type,
                "w": encoded.width, "h": encoded.height, "bytes": encoded.data,
                "sha": sha, "token": token,
            })).fetchone()
            image_id = str(row.id)
            content_type, width, height = encoded.content_type, encoded.width, encoded.height
    return {
        "id": image_id,
        "url": image_url(base, str(org), token, content_type),
        "kind": kind,
        "content_type": content_type,
        "width": width,
        "height": height,
        "dark_share": encoded.dark_share,
        "clear_share": encoded.clear_share,
    }


def _refusal(exc: signature_image.SignatureImageError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=str(exc))


@router.post("/signature/images")
async def upload_signature_image(
    file: UploadFile = File(...),
    account_id: str = Form(...),
    kind: str = Form("logo"),
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Upload one logo or photo for a signature, and answer its public URL."""
    member = _member(user)
    async with _tenant_session() as db:
        # The one reader of an account reference (an id or an address).
        account_id = await resolve_account_filter(
            db, account_id, member or "anonymous", required=True)
        await _assert_account_owner(db, account_id, member or "anonymous", level="send")
    base = public_base_url()
    if base is None:
        raise _no_base()
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    try:
        encoded = await signature_image.encode(data, kind)
    except signature_image.SignatureImageError as exc:
        raise _refusal(exc) from exc
    return await store_image(encoded, kind=kind, member=member, base=base)


@router.delete("/signature/images/{image_id}")
async def retire_signature_image(
    image_id: str,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Retire one image: it leaves the picker and stays served (D-EM-67)."""
    try:
        uuid.UUID(image_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Not Found") from None
    admin = bool(user.has_permission(ADMIN_CAPABILITY))
    async with _tenant_session() as db:
        row = (await db.execute(text(RETIRE_SQL), {
            "id": image_id, "me": _member(user), "admin": admin,
        })).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Not Found")
    return {"id": str(row.id), "retired": True}


def _canonical_org(org: str) -> str | None:
    """The org id when it is a UUID in its canonical form, else ``None``."""
    try:
        parsed = uuid.UUID(org)
    except (ValueError, AttributeError):
        return None
    return org if str(parsed) == org else None


@router.get(PUBLIC_ROUTE.removeprefix("/email"))
async def signature_image_file(org: str, token: str, ext: str) -> Response:
    """Serve one hosted image to anybody who holds its URL (D-EM-68)."""
    tenant = _canonical_org(org)
    if tenant is None or not _TOKEN.fullmatch(token) or ext not in _EXT.values():
        raise HTTPException(status_code=404, detail="Not Found")
    async with _tenant_session(tenant) as db:
        row = (await db.execute(
            text(PUBLIC_SQL), {"org": tenant, "token": token})).fetchone()
    if row is None or _EXT.get(row.content_type) != ext:
        raise HTTPException(status_code=404, detail="Not Found")
    return Response(
        content=bytes(row.bytes), media_type=row.content_type,
        headers=dict(PUBLIC_HEADERS))


# ── The save of a signature (D-EM-70) ───────────────────────────────────────


def _decode_data_image(src: str) -> bytes:
    """The bytes of one ``data:image/...;base64,`` source, or a refusal."""
    _, _, payload = src.partition(",")
    if len(payload) > (MAX_UPLOAD_BYTES * 4) // 3 + 8:
        raise HTTPException(status_code=413, detail=(
            f"One image in your signature is larger than "
            f"{MAX_UPLOAD_BYTES // (1024 * 1024)} MB. Remove it, then upload a "
            "smaller picture with the image button."))
    try:
        return base64.b64decode(re.sub(r"\s+", "", payload), validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=422, detail=_BAD_DATA_IMAGE) from None


async def host_data_images(sources: list[str], *, member: str) -> dict[str, str]:
    """Host each ``data:image`` source of a signature, and map it to its URL.

    Each one goes through the same child and the same caps as an upload. A
    source that fails refuses the save with a plain sentence, so a picture
    never disappears from a signature without a word."""
    if not sources:
        return {}
    base = public_base_url()
    if base is None:
        raise _no_base(
            "Your signature holds an embedded image, and this server cannot host it. ")
    hosted: dict[str, str] = {}
    for src in sources:
        data = _decode_data_image(src)
        try:
            encoded = await signature_image.encode(data, "logo")
        except signature_image.SignatureImageError as exc:
            raise HTTPException(status_code=exc.status, detail=(
                f"One image in your signature could not be saved. {exc}")) from exc
        stored = await store_image(encoded, kind="logo", member=member, base=base)
        hosted[src] = stored["url"]
    return hosted


async def signature_for_save(
    account_id: str, member: str, new: str | None,
) -> tuple[str | None, list[dict[str, str]]]:
    """The signature that ``PUT /email/assistant/settings`` stores, and each
    link or image address that the clean removed from it.

    D-EM-70: the server cleans each signature HTML on save, and it hosts
    each ``data:`` image first. A plain-text signature is escaped at the send,
    so it stays as it is. A signature equal to the stored one is no new save:
    it stays byte for byte (§23.0 rule 7), so a change of another setting
    never changes a signature. The caller has checked the mailbox, and this
    checks it again in its own session. The removed list goes back to the
    member as ``signature_removed``, so no link leaves without a word."""
    if not new or not sig.looks_html(new):
        return new, []
    async with _tenant_session() as db:
        await _assert_account_owner(db, account_id, member or "anonymous")
        stored = await sig.stored_signature(db, account_id)
    if new == stored:
        return new, []
    hosted = await host_data_images(sig.data_image_sources(new), member=member)
    return sig.clean_signature_report(new, hosted=hosted)


__all__ = [
    "ADMIN_CAPABILITY",
    "MEMBER_CAP",
    "ORG_CAP",
    "PUBLIC_HEADERS",
    "PUBLIC_ROUTE",
    "host_data_images",
    "image_url",
    "public_base_url",
    "signature_for_save",
    "store_image",
]
