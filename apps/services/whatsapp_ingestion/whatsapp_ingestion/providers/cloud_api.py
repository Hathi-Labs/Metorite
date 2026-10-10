"""Official Meta WhatsApp Business Cloud API provider (Graph API).

The only WhatsApp transport in the product. Sends (free-form text inside the 24h
window, approved templates outside it) and media download go through the Graph
API with a bearer token from the encrypted account credentials.

Credentials dict (decrypted from ``wa_accounts.credentials_encrypted``)::

    {"access_token": "<system-user token>", "phone_number_id": "...",
     "waba_id": "...", "graph_version": "v21.0"}
"""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx

from whatsapp_ingestion.providers.base import BaseWhatsAppProvider

logger = logging.getLogger(__name__)

_DEFAULT_GRAPH_VERSION = "v21.0"
_GRAPH_BASE = "https://graph.facebook.com"
_TIMEOUT = httpx.Timeout(30.0)

# Both values go into a Graph URL PATH. A `/`, `?` or `#` in either one can
# turn a node read of the number into a read of some other Graph object
# (WA-C1 review, round 3). ASCII digits only, so no Unicode digit gets in.
_GRAPH_VERSION_RE = re.compile(r"v[0-9]+\.[0-9]+")
_PHONE_NUMBER_ID_RE = re.compile(r"[0-9]+")


def safe_graph_version(value: Any) -> str:
    """``value`` when it is a Graph version such as ``v21.0``, else the default."""
    if isinstance(value, str) and _GRAPH_VERSION_RE.fullmatch(value):
        return value
    return _DEFAULT_GRAPH_VERSION


def is_phone_number_id(value: Any) -> bool:
    """True when ``value`` is a Meta phone number id: ASCII digits only."""
    return isinstance(value, str) and bool(_PHONE_NUMBER_ID_RE.fullmatch(value))


class WhatsAppCloudProvider(BaseWhatsAppProvider):
    """WhatsApp Business Cloud API client."""

    def __init__(self, credentials: dict[str, Any]):
        super().__init__(credentials)
        self.access_token: str = credentials.get("access_token", "") or ""
        self.phone_number_id: str = credentials.get("phone_number_id", "") or ""
        raw_version = credentials.get("graph_version")
        self.graph_version: str = safe_graph_version(raw_version)
        if raw_version and raw_version != self.graph_version:
            logger.warning("whatsapp.cloud.graph_version_refused")
        if not self.access_token or not self.phone_number_id:
            raise ValueError(
                "WhatsApp Cloud credentials need access_token + phone_number_id"
            )
        if not is_phone_number_id(self.phone_number_id):
            raise ValueError(
                "WhatsApp Cloud phone_number_id must be the numeric id from Meta"
            )

    @property
    def _messages_url(self) -> str:
        return (
            f"{_GRAPH_BASE}/{self.graph_version}/"
            f"{self.phone_number_id}/messages"
        )

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

    async def _post_message(self, payload: dict[str, Any]) -> str:
        """POST to the messages endpoint and return the Meta message id."""
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                self._messages_url, headers=self._headers(), json=payload
            )
            resp.raise_for_status()
            data = resp.json()
        try:
            return data["messages"][0]["id"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(
                f"unexpected send response shape: {data!r}"
            ) from exc

    async def send_text(
        self,
        to_wa_id: str,
        body: str,
        *,
        reply_to_wa_message_id: str | None = None,
    ) -> str:
        payload: dict[str, Any] = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to_wa_id,
            "type": "text",
            "text": {"preview_url": True, "body": body},
        }
        if reply_to_wa_message_id:
            payload["context"] = {"message_id": reply_to_wa_message_id}
        return await self._post_message(payload)

    async def send_template(
        self,
        to_wa_id: str,
        template_name: str,
        language: str,
        *,
        components: list[dict[str, Any]] | None = None,
    ) -> str:
        template: dict[str, Any] = {
            "name": template_name,
            "language": {"code": language},
        }
        if components:
            template["components"] = components
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to_wa_id,
            "type": "template",
            "template": template,
        }
        return await self._post_message(payload)

    async def download_media(self, wa_media_id: str) -> tuple[bytes, str]:
        """Two hops: resolve the media id to a short-lived URL, then GET it."""
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            meta_resp = await client.get(
                f"{_GRAPH_BASE}/{self.graph_version}/{wa_media_id}",
                headers={"Authorization": f"Bearer {self.access_token}"},
            )
            meta_resp.raise_for_status()
            meta = meta_resp.json()
            url = meta.get("url")
            mime_type = meta.get("mime_type") or "application/octet-stream"
            if not url:
                raise RuntimeError(f"media {wa_media_id} has no url: {meta!r}")
            # The media URL still requires the bearer token.
            bin_resp = await client.get(
                url, headers={"Authorization": f"Bearer {self.access_token}"}
            )
            bin_resp.raise_for_status()
            return bin_resp.content, mime_type

    async def get_phone_number_profile(self) -> dict[str, Any]:
        """Verify the token + fetch the number's public profile (a Graph GET on
        the phone_number_id). Backs the connect flow's 'test connection': a 200
        proves the token can act for this number and returns its display name /
        number / quality rating. Raises ``httpx.HTTPStatusError`` on a Meta error
        (bad token, wrong id) so the caller can surface Meta's own message.
        """
        url = f"{_GRAPH_BASE}/{self.graph_version}/{self.phone_number_id}"
        params = {
            "fields": (
                "display_phone_number,verified_name,quality_rating,"
                "code_verification_status,platform_type"
            )
        }
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(
                url,
                headers={"Authorization": f"Bearer {self.access_token}"},
                params=params,
            )
            resp.raise_for_status()
            return resp.json()

    async def send_interactive(
        self, to_wa_id: str, interactive: dict[str, Any],
    ) -> str:
        """Reply buttons, a list or a link button (``type`` inside the object).

        The caller builds and checks the object (WS-47 WAC-10a,
        ``acb_skills.whatsapp_ui``). This method only posts it.
        """
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to_wa_id,
            "type": "interactive",
            "interactive": interactive,
        }
        return await self._post_message(payload)

    async def upload_media(
        self, data: bytes, mime_type: str, filename: str,
    ) -> str:
        """Upload a file to this number's media store. Returns the media id."""
        url = f"{_GRAPH_BASE}/{self.graph_version}/{self.phone_number_id}/media"
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                url,
                headers={"Authorization": f"Bearer {self.access_token}"},
                data={"messaging_product": "whatsapp", "type": mime_type},
                files={"file": (filename, data, mime_type)},
            )
            resp.raise_for_status()
            body = resp.json()
        media_id = body.get("id") if isinstance(body, dict) else None
        if not media_id:
            raise RuntimeError("unexpected media upload response shape")
        return str(media_id)

    async def send_image(
        self, to_wa_id: str, media_id: str, *, caption: str | None = None,
    ) -> str:
        image: dict[str, Any] = {"id": media_id}
        if caption:
            image["caption"] = caption
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to_wa_id,
            "type": "image",
            "image": image,
        }
        return await self._post_message(payload)

    async def show_typing(self, wa_message_id: str) -> bool:
        """Mark the member's message read and show "typing…" to them.

        Meta hides the indicator after 25 seconds, or when the reply arrives.
        Best effort: it returns False on any error and never raises.
        """
        payload = {
            "messaging_product": "whatsapp",
            "status": "read",
            "message_id": wa_message_id,
            "typing_indicator": {"type": "text"},
        }
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(
                    self._messages_url, headers=self._headers(), json=payload
                )
                resp.raise_for_status()
        except Exception as exc:
            logger.warning("whatsapp.typing_failed error_class=%s",
                           type(exc).__name__)
            return False
        return True

    async def mark_read(self, wa_message_id: str) -> None:
        payload = {
            "messaging_product": "whatsapp",
            "status": "read",
            "message_id": wa_message_id,
        }
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(
                    self._messages_url, headers=self._headers(), json=payload
                )
                resp.raise_for_status()
        except Exception as exc:
            logger.warning(
                "whatsapp.mark_read_failed id=%s error=%s",
                wa_message_id, str(exc)[:120],
            )
