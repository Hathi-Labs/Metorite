"""Single name→class factory for WhatsApp providers.

Mirrors ``email_ingestion.providers.factory.build_provider`` so the gateway's
provider adapter is identical in shape. Two transports share the same normalized
store + AI brain:

* ``cloud_api`` — the official Meta WhatsApp Business Cloud API.
* ``whatsmeow`` — a personal number paired by QR through the local whatsmeow
  bridge service (unofficial multi-device). ``BridgeProvider`` only speaks HTTP
  to that bridge; the WhatsApp session lives there, never in the gateway.

The gateway never imports a concrete provider directly — it names one here.
"""

from __future__ import annotations

from typing import Any

from whatsapp_ingestion.providers.base import BaseWhatsAppProvider
from whatsapp_ingestion.providers.bridge import BridgeProvider
from whatsapp_ingestion.providers.cloud_api import (
    WhatsAppCloudProvider,
    is_phone_number_id,
    safe_graph_version,
)

# The two Graph URL guards, named here so the gateway imports no concrete
# provider (the rule of this module's docstring).
__all__ = ["build_provider", "is_phone_number_id", "safe_graph_version"]

_PROVIDERS = {
    "cloud_api": WhatsAppCloudProvider,
    "whatsmeow": BridgeProvider,
}


def build_provider(name: str, credentials: dict[str, Any]) -> BaseWhatsAppProvider:
    """Construct a provider by name, or raise ``ValueError`` for an unknown one."""
    cls = _PROVIDERS.get(name)
    if cls is None:
        supported = ", ".join(sorted(_PROVIDERS))
        raise ValueError(f"Unknown WhatsApp provider: {name}. Supported: {supported}")
    return cls(credentials)
