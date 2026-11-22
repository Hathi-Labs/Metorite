"""The preview of a layout signature (WS-17 SG-2, D-EM-65).

Spec: ``project-docs/specs/email_app_master_plan.md`` §23.3.3 and §23.5.

``POST /email/signature/preview`` renders one layout and one field map with
``signature_render.render``, the renderer of each send, and answers the HTML
and the text. It writes nothing and reads no table, so it reads no mailbox.
The form of SG-3 calls it, and the browser never renders by itself
(D-EM-65).

The route is dark: while ``EMAIL_SIGNATURE_TEMPLATES_ORGS`` does not hold the
org of the session, it answers 404 (``signature.templates_on``). It is a
member route behind the email feature gate of the router, never public.
"""
from __future__ import annotations

import json
from typing import Any, Literal

from acb_auth import UserContext, get_current_user
from fastapi import Depends, HTTPException
from gateway.routes.email import signature, signature_render
from gateway.routes.email.core import router
from pydantic import BaseModel, ConfigDict, Field, field_validator

#: The most JSON that one field map holds. The CHECK of
#: ``email_signature_settings.fields`` holds the same bound.
MAX_FIELDS_BYTES = 16384


class SignaturePreviewRequest(BaseModel):
    """One layout and the fields to draw in it."""

    model_config = ConfigDict(extra="forbid")

    layout: Literal["classic", "stacked", "minimal", "photo", "compact"]
    fields: dict[str, Any] = Field(default_factory=dict)

    @field_validator("fields")
    @classmethod
    def _small(cls, value: dict[str, Any]) -> dict[str, Any]:
        size = len(json.dumps(value, ensure_ascii=False, default=str).encode("utf-8"))
        if size > MAX_FIELDS_BYTES:
            raise ValueError(
                f"The signature fields hold {size} bytes. The limit is {MAX_FIELDS_BYTES}.")
        return value


@router.post("/signature/preview")
async def preview_signature(
    req: SignaturePreviewRequest,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Render one layout signature for the form. It writes nothing."""
    if not signature.templates_on():
        raise HTTPException(status_code=404, detail="Not Found")
    html, text = signature_render.render(req.layout, req.fields)
    return {"layout": req.layout, "html": html, "text": text}


__all__ = ["MAX_FIELDS_BYTES", "SignaturePreviewRequest", "preview_signature"]
