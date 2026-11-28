"""The pin of THIS chat, from the chat (WS-CO CO-11b, task 3563).

Spec: ``project-docs/specs/chat_organization.md`` §18. Chat parity:
``project-docs/specs/chat_parity.md``, wave P-7. CO-11 (task 3510) built the
store and the routes: ``gateway/chat_session_member.py``, and ``PUT`` and
``DELETE`` ``/chat/sessions/{id}/pin`` in ``gateway/routes/chat.py``.

:func:`pin_chat` is the one tool. It pins or unpins the chat of this run, for
the member of this run, and no other chat. The rules:

* **The route decides.** The tool sends the route over HTTP as the member,
  with the internal bearer and ``X-User-Email``, as ``profile_tools.py``
  does. So the switch ``CHAT_PIN_ORGS``, the feature gate, the visibility
  rule and the archive rule of the route apply. The tool opens no database
  session, takes no SQL and writes no Redis key.
* **No id, no member and no org from the model.** The one parameter is
  ``pinned``. The chat is the session of the run binding
  (``artifact_context()["session_id"]``). The member is the verified member
  of the run binding (``get_run_context()["user"]`` with
  ``member_verified``), and it must equal the ``member`` of the artifact
  context. The memory id is not the signal, because a shared room binds
  ``room:<thread>`` there. The request has no body and no org header. The
  gateway finds the org from the member (R5, R11).
* **The address goes as the run holds it.** The visibility rule of the route
  binds the exact address, and the store keys the row in lower case. So the
  tool never changes the case of the address.
* **A refusal sends no request.** The tool refuses with no call when the run
  has no verified member, when the two members differ, when the run has no
  chat (a batch run) and in a WhatsApp run. The pin belongs to the chat list
  of the web app.
* **A card only where the rule asks.** A pin is the member's own act. It is
  reversible, and it sends nothing off the platform. So it acts at once and
  offers Undo in words. A run where ``egress.write_must_show_card`` is true
  shows one card first, bound to the member (``responder``). Only an approve
  sends the request. A card that did nothing answers with
  ``ask_tools.card_not_done_text``, the one table of card outcomes.
* **Each status gives one sentence.** A failure starts with "Nothing
  changed" and names the next step. The 409 passes the reason of the route.
  The tool never raises.
* **Dark.** Only the orchestrator holds the tool, in its ``core_tools``, and
  only for an org on ``CHAT_PIN_TOOL_ORGS`` and on ``CHAT_PIN_ORGS``
  (``agents._chat_pin_enabled``, which asks ``pin_tool_allowed`` of the store
  module). The owner gave the tool its own flag on 2026-11-21 (task 3563). It
  is in no ``config.json`` scope, no skill family and no pack.

Fences: ``tests/unit/test_chat_pin_tool.py`` and
``tests/unit/test_chat_pin_tool_r8.py`` (R8).
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx
from acb_common import get_logger
from acb_common.settings import internal_auth_headers

from acb_skills.company_search_tools import _gateway_url
from acb_skills.memory_tools import run_member_address
from acb_skills.tool_annotations import TOOL_ANNOTATIONS

_log = get_logger("acb_skills.chat_pin_tools")

#: The longest wait for the route, in seconds.
TIMEOUT_S = 10.0
#: The longest reason of the route that the answer passes on.
DETAIL_CAP = 200

#: The answers of a pin and an unpin that the route took. The chat list loads
#: when it opens, so the answer says when the change shows.
PINNED = ('Pinned this chat. Your chat list shows it in Pinned after you reload '
          'the page or open the assistant again. To undo, say "unpin this chat".')
UNPINNED = ('Unpinned this chat. Your chat list shows the change after you reload '
            'the page or open the assistant again. To undo, say "pin this chat".')

#: The refusals that send no request.
NOT_A_CHOICE = "Nothing changed. Say whether to pin this chat or to unpin it."
NO_MEMBER = ("Nothing changed. Metorite could not confirm who asked in this chat. "
             "Use Pin on the chat's row in your chat list.")
NO_CHAT = ("Nothing changed. This run has no chat to pin. Open the chat in the "
           "Metorite web app and use Pin on its row.")
ON_WHATSAPP = ("Nothing changed. A pin belongs to your chat list in the Metorite web "
               "app. Open the chat there and use Pin on its row.")

#: The answers of a request that the route refused or did not answer.
NOT_FOUND = ("Nothing changed. Pin is off for your organization, or this chat is not "
             "open to you. Use Pin on the chat's row in your chat list.")
ARCHIVED = ("This chat is archived. Restore it from your archived chats, then pin "
            "it.")
FORBIDDEN = ("Nothing changed. Your account cannot pin a chat here. It needs its own "
             "address and the chat feature. Ask your admin.")
NO_IDENTITY = ("Nothing changed. Metorite could not confirm your account. Try again "
               "in a minute.")
NO_ANSWER = ("Nothing changed. The chat list did not answer. Try again, or use Pin "
             "on the chat's row.")

#: The card of a run where a write must show its card.
CARD_TITLE_PIN = "Pin this chat?"
CARD_TITLE_UNPIN = "Unpin this chat?"
CARD_DETAIL = "Only your own chat list changes. Nobody else sees your pins."
#: The act of a card that did nothing, for ``card_not_done_text``.
ACT_NOT_PINNED = "This chat is not pinned."
ACT_STAYS_PINNED = "This chat stays pinned."


def _member() -> str:
    """The verified member of this run, as the run binding holds it, or ``""``.

    The member of ``get_run_context()`` must be verified (``member_verified``,
    H-73), and it must equal the ``member`` of the artifact context, as
    ``executor._prime_chat_constraints`` checks. Never the memory id, which a
    shared room binds to ``room:<thread>``. Never raises."""
    try:
        from acb_common import get_run_context

        from acb_skills.write_artifact import artifact_context

        ctx = get_run_context() or {}
        if ctx.get("member_verified") != "1":
            return ""
        user = str(ctx.get("user") or "").strip()
        bound = str(artifact_context().get("member") or "").strip()
    except Exception:
        return ""
    if not user or run_member_address(user) is None or user.lower() != bound.lower():
        return ""
    return user


def _session() -> str:
    """The chat of this run: the session of the artifact context, or ``""``.

    A batch run (``batch_thread``) has a thread that the executor made, and no
    chat. Never raises."""
    try:
        from acb_skills.write_artifact import artifact_context

        ctx = artifact_context()
    except Exception:
        return ""
    if ctx.get("batch_thread") is True:
        return ""
    return str(ctx.get("session_id") or "").strip()


def _on_whatsapp() -> bool:
    """True in a WhatsApp run. The rule is ``profile_tools._channel``. A fault
    reads as True, so the tool refuses."""
    try:
        from acb_skills.profile_tools import _channel

        return _channel() == "whatsapp"
    except Exception:
        return True


async def _must_ask() -> bool:
    """True when this run must show a card before a write (RX-2). The one
    reader is ``egress.write_must_show_card``. A fault reads as True."""
    try:
        from acb_skills import egress

        return bool(await egress.write_must_show_card())
    except Exception:
        return True


async def _approved(pinned: bool, member: str) -> bool:
    """Show ONE card, bound to *member*, and wait. True only on an approve."""
    from acb_skills import ask_tools

    answer = await ask_tools.request_confirmation(
        CARD_TITLE_PIN if pinned else CARD_TITLE_UNPIN, CARD_DETAIL,
        non_interactive_default="deny", responder=member)
    return answer is True


def _not_done(pinned: bool) -> str:
    """The answer of a card that did nothing, from the one outcome table."""
    from acb_skills import ask_tools

    return ask_tools.card_not_done_text(ACT_NOT_PINNED if pinned else ACT_STAYS_PINNED)


def _transport() -> httpx.AsyncBaseTransport | None:
    """The transport of the request. None is the network. A test returns a
    mock transport or an ASGI transport here."""
    return None


async def _send(method: str, path: str, member: str, body: Any = None) -> tuple[int, Any]:
    """Send ONE request of the route as *member*. A pin sends no *body*.

    The headers are the internal bearer, ``X-User-Email`` and the content
    type, and nothing else. *body* is the JSON body of a chat list write of
    ``chat_thread_tools`` (PAR-11), and None sends no body. Returns the status
    and the JSON body. A fault of the request gives status 0. Never raises."""
    headers = {
        **internal_auth_headers(),
        "X-User-Email": member,
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(base_url=_gateway_url(), transport=_transport(),
                                     timeout=TIMEOUT_S) as client:
            resp = await client.request(method, path, headers=headers, json=body)
    except Exception as exc:
        _log.warning("chat_pin_tool.request_failed", error=type(exc).__name__)
        return 0, None
    try:
        body = resp.json()
    except Exception:
        body = None
    return int(resp.status_code), body


def _detail(body: Any) -> str:
    """The reason of the route as one clean line, or ``""``."""
    raw = body.get("detail") if isinstance(body, dict) else None
    if not isinstance(raw, str):
        return ""
    line = " ".join(raw.split())
    if not line or len(line) > DETAIL_CAP or not line.isprintable():
        return ""
    return line


def _answer(status: int, body: Any, pinned: bool) -> str:
    """The sentence of a status of the route."""
    if status == 200 and isinstance(body, dict) and body.get("pinned") is pinned:
        return PINNED if pinned else UNPINNED
    if status == 404:
        return NOT_FOUND
    if status == 409:
        return f"Nothing changed. {_detail(body) or ARCHIVED}"
    if status == 403:
        return FORBIDDEN
    if status == 503:
        return NO_IDENTITY
    return NO_ANSWER


async def pin_chat(pinned: bool = True) -> str:
    """Pin or unpin this chat in the member's own chat list, and no other chat.

    Call it only when the member asks to pin or to unpin this chat.

    Args:
        pinned: True pins this chat. False unpins it.
    """
    if not isinstance(pinned, bool):
        return NOT_A_CHOICE
    if _on_whatsapp():
        return ON_WHATSAPP
    member = _member()
    if not member:
        return NO_MEMBER
    sid = _session()
    if not sid:
        return NO_CHAT
    if await _must_ask() and not await _approved(pinned, member):
        return _not_done(pinned)
    if pinned:
        status, body = await _send("PUT", f"/chat/sessions/{quote(sid, safe='')}/pin", member)
    else:
        status, body = await _send("DELETE", f"/chat/sessions/{quote(sid, safe='')}/pin", member)
    _log.info("chat_pin_tool.sent", pinned=pinned, status=status)
    return _answer(status, body, pinned)


pin_chat.__tool_risk__ = TOOL_ANNOTATIONS["pin_chat"]  # type: ignore[attr-defined]

#: The one tool, as the orchestrator holds it.
TOOLS = (pin_chat,)

__all__ = [
    "ARCHIVED",
    "FORBIDDEN",
    "NOT_FOUND",
    "NO_ANSWER",
    "NO_CHAT",
    "NO_IDENTITY",
    "NO_MEMBER",
    "ON_WHATSAPP",
    "PINNED",
    "TOOLS",
    "UNPINNED",
    "pin_chat",
]
