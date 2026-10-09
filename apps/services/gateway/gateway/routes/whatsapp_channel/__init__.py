"""The WhatsApp assistant channel (WS-47): a member chats with Metorite from
their own WhatsApp.

Spec: ``project-docs/specs/whatsapp_assistant_channel.md``.

This package owns WhatsApp as a way to TALK to Metorite. The inbox app
(``gateway.routes.whatsapp``, WS-20) owns a member's own business number. The
two share transport code and nothing else, so this package is not under the
``/whatsapp`` router and its ``feature:whatsapp`` gate.

* ``flags`` — the one reader of the three ``WHATSAPP_ASSISTANT_*`` settings.
* ``link`` — ``/me/whatsapp-link``, the link code (WAC-1).
"""

from gateway.routes.whatsapp_channel.link import router

__all__ = ["router"]
