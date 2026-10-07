"""WhatsApp routes — shared kernel.

The shared ``router``, Pydantic models, DB session helper and the provider
adapter used by the transport layer. Depends on nothing inside the package (the
leaf), so importing it never pulls in a route module. Structurally identical to
``gateway.routes.email.core`` — a WhatsApp channel is the same shape as email.
"""

from __future__ import annotations

import json
from typing import Any

from acb_auth import require_feature_router
from acb_common import get_logger
from fastapi import APIRouter, HTTPException

# The shared gateway engine (BO-10) — see the DB section below.
from gateway.db import get_db as _get_db  # noqa: F401
from gateway.db import get_session_factory as _get_session_factory  # noqa: F401

# The tenant-bound seam (MT-1c/H2). `_tenant_session` IS
# `acb_common.db.tenant_session`, aliased per-package exactly as
# `routes/projects/core.py` does: every submodule imports it from here BY
# NAME, which is the one seam the hermetic tests patch per module. The tenant
# comes from the request context — bound centrally in `_with_resolved_access`
# — so no call site passes one. A call outside a bound request raises
# `TenantUnbound` rather than defaulting: fail closed, never "the usual org".
#
# ⚠️ NOT every site in this package uses it. This surface is ingestion-heavy:
# the whatsmeow bridge's five push routes, the GET verify fallback and the
# enrichment scheduler run with NO ambient tenant (the Go bridge authenticates
# with its own secret, not a member session; `system:internal` binds
# nothing). Those stay on `_get_db` with an H4/H6 marker at each site until an
# explicit tenant is threaded through — deriving it ambiently there is exactly
# what the H2 runbook forbids.
#
# WS-20 WA-C1: the Meta webhook POST binds the tenant of the account row that
# owns the number, read through a SECURITY DEFINER resolver. The two post-sync
# hooks then open `_tenant_session()` under that binding.
from gateway.db import tenant_session as _tenant_session  # noqa: F401
from pydantic import BaseModel

_log = get_logger("gateway.whatsapp")

router = APIRouter(
    prefix="/whatsapp", tags=["whatsapp"],
    # Org access control: the whole surface needs `feature:whatsapp`, so a new
    # endpoint added here is gated by default. The provider webhook is exempt —
    # it arrives from Meta with no session and no internal token, and it
    # authenticates itself via the verify token / signature, so gating it would
    # break inbound message ingestion rather than restrict it.
    # Exempt: machine entrypoints that arrive with no session and no internal
    # token, each already authenticated by its own scheme. Gating them would
    # break inbound ingestion, not restrict it.
    #   /whatsapp/webhook    — Meta verify-token (GET) + signature (POST)
    #   /whatsapp/bridge/*   — the Go bridge's X-Bridge-Secret (constant-time
    #                          compare in transport/bridge.bridge_secret_ok)
    dependencies=[require_feature_router("whatsapp", exempt=[
        "/whatsapp/webhook",
        "/whatsapp/bridge/ingest",
        "/whatsapp/bridge/reclassify",
        "/whatsapp/bridge/labels",
        "/whatsapp/bridge/avatars",
        "/whatsapp/bridge/paired",
        "/whatsapp/bridge/call-event",
    ])],
)

#: WebSocket routes live here, NOT on `router`.
#:
#: The feature gate's check takes an HTTP ``Request``, and FastAPI never
#: populates that parameter for a WebSocket route — the dependency raises before
#: the handler runs, so the socket dies at the handshake with nothing useful
#: logged. Exempting the path doesn't help: the dependency still executes in
#: order to read the path. A separate ungated router is the only correct shape.
#:
#: Anything added here MUST authenticate itself. Today that is
#: /whatsapp/calls/audio, which requires a short-lived HMAC token minted by the
#: gated audio-token route.
ws_router = APIRouter(prefix="/whatsapp", tags=["whatsapp"])


# ── Pydantic models (the wire shape the Next.js app consumes) ─────────────────

class WhatsAppAccountModel(BaseModel):
    id: str
    phone_number: str
    phone_number_id: str
    waba_id: str | None = None
    display_name: str = ""
    avatar_color: str = "#25D366"
    sync_status: str = "idle"
    sync_error: str | None = None
    history_import_phase: int = 0
    quality_rating: str | None = None
    last_synced_at: str | None = None
    is_default: bool = False
    # 'cloud' (Meta Cloud API) or 'whatsmeow' (the QR-paired personal bridge).
    # The dialer needs it: voice calling only exists on the bridge transport.
    provider: str = "cloud"
    # WS-20 WA-C3 (spec §12.4.1 P11). The coexistence history import. The
    # state is None for an account that is not coexistence. The deadline is
    # `created_at` plus 24 hours, the window that Meta gives for the sync.
    history_sync_state: str | None = None
    history_sync_error: str | None = None
    history_import_progress: int | None = None
    history_sync_deadline: str | None = None


class WhatsAppChatModel(BaseModel):
    id: str
    account_id: str
    wa_chat_id: str
    kind: str = "dm"
    name: str = ""
    category: str | None = None
    status: str | None = None          # NEEDS_REPLY | AWAITING | FYI | DONE
    last_message_at: str | None = None
    last_snippet: str = ""
    # Whether the 24h free-form window is currently open, and when it closes.
    window_open: bool = False
    window_expires_at: str | None = None
    snoozed_until: str | None = None    # set while the chat is snoozed (W6)
    # Native WhatsApp labels the chat carries, mirrored read-only (W16).
    labels: list[dict[str, Any]] = []
    # Native WhatsApp profile-picture URL, synced from the number (W17).
    avatar_url: str | None = None


class WhatsAppMessageModel(BaseModel):
    id: str
    chat_id: str
    wa_message_id: str
    direction: str = "in"
    kind: str = "text"
    sender_name: str = ""
    body_text: str = ""
    transcript_text: str | None = None    # voice-note transcription (W4.3)
    quoted_wa_message_id: str | None = None
    categories: list[str] = []
    intent: str | None = None
    send_regime: str | None = None
    sent_at: str | None = None


# ── DB (the one shared gateway engine — gateway/db.py, BO-10) ────────────────
#
# This package used to build its own engine here with its own 5+10 pool. It now
# has none: `_get_db` / `_get_session_factory` / `_tenant_session` at the top
# of this module are re-exports of the shared seam. The private names are kept
# so that every `from .core import _tenant_session` (or `_get_db`, on the
# service-identity/background sites H4 owns) in this package — and every test
# that monkeypatches the seam on the sibling module it is imported into —
# keeps working unchanged.


# ── provider adapter ──────────────────────────────────────────────────────────

def _instantiate_provider(name: str, creds: dict[str, Any]):
    """Construct a WhatsApp provider from its name + decrypted creds."""
    from whatsapp_ingestion.providers.factory import build_provider
    try:
        return build_provider(name, creds)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


async def _provider_for_account(db: Any, account_id: str):
    """Load + decrypt creds and build the account's provider (cloud_api OR the
    whatsmeow bridge, per the ``provider`` column).

    Returns ``(provider, store, row)`` or raises 404. Unscoped (no user filter) —
    the callers are the send path (already user-scoped upstream) and background
    webhook tasks (no request user).
    """
    from sqlalchemy import text
    row = (await db.execute(
        text("""SELECT credentials_encrypted, phone_number_id, provider
                FROM wa_accounts WHERE id = :id"""),
        {"id": account_id},
    )).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="WhatsApp account not found")
    from acb_llm.key_store import get_key_store
    store = get_key_store()
    creds = json.loads(store.decrypt(row.credentials_encrypted))
    provider = _instantiate_provider(
        getattr(row, "provider", None) or "cloud_api", creds)
    return provider, store, row


# ── shared ownership guards (used across transport/ + automation/) ────────────

async def assert_account_owned(db: Any, account_id: str, user_email: str) -> None:
    """Raise 404 unless ``account_id`` belongs to ``user_email``."""
    from sqlalchemy import text
    owned = (await db.execute(
        text("SELECT 1 FROM wa_accounts WHERE id = :id AND user_id = :uid"),
        {"id": account_id, "uid": user_email},
    )).fetchone()
    if not owned:
        raise HTTPException(status_code=404, detail="Account not found")


async def assert_chat_owned(db: Any, chat_id: str, user_email: str) -> str:
    """Return the chat's ``account_id``, or raise 404 unless it belongs to
    ``user_email``. Callers that only need the guard can ignore the return."""
    from sqlalchemy import text
    row = (await db.execute(
        text("""SELECT c.account_id FROM wa_chats c
                JOIN wa_accounts a ON a.id = c.account_id
                WHERE c.id = :cid AND a.user_id = :uid"""),
        {"cid": chat_id, "uid": user_email},
    )).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Chat not found")
    return str(row.account_id)


# ── shared post-sync hook runner (used by both inbound transports) ────────────

async def fire_post_sync_hooks(account_id: str, counts: dict[str, int]) -> None:
    """Run the post-sync pipeline after a persisted batch: the new-message hook
    (only when inbound landed) then the chat-status classifier (always — even a
    status-only echo can flip a chat's state). Shared by the Cloud API webhook and
    the whatsmeow bridge. Best-effort: a hook failure never turns into a provider
    retry of an already-stored batch."""
    from whatsapp_ingestion.post_sync import hooks, run_hook
    if counts.get("messages"):
        try:
            await run_hook(hooks.on_new_messages, account_id)
        except Exception as exc:
            _log.warning("whatsapp.hook_failed",
                         hook="on_new_messages", error=str(exc)[:200])
    try:
        await run_hook(hooks.classify_chats, account_id)
    except Exception as exc:
        _log.warning("whatsapp.hook_failed",
                     hook="classify_chats", error=str(exc)[:200])
