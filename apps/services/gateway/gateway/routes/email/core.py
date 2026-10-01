"""Email routes — shared kernel.

The shared ``router``, Pydantic models, DB/Redis/provider infrastructure,
message row<->model mappers and small generic helpers used by BOTH the
transport and automation layers. This module depends on nothing inside the
package (it is the leaf), so importing it never pulls in a feature layer.
"""

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from acb_common import get_logger, get_settings
from fastapi import APIRouter, HTTPException

# The shared gateway engine (BO-10) — see the DB section below.
from gateway.db import get_session_factory as _get_session_factory

# The shared tenant-bound seam (BO-10 → MT-1c/H2). `_tenant_session` IS
# `acb_common.db.tenant_session`, aliased per-package for the same reason
# `_get_db` was: every submodule imports it from here BY NAME, which is the
# seam the hermetic tests patch per module. The tenant comes from the request
# context — bound once in `_with_resolved_access` — so no call site passes
# one (H2). A call outside a bound request raises `TenantUnbound` rather than
# defaulting: fail closed, never "the usual org". The OAuth callback and the
# Graph webhook pass an explicit tenant (EM-T1a). Since EM-T1b the sync loop
# binds the organization of each mailbox, so the scheduler hooks and the ten
# automation jobs they reach use `_tenant_session()` with that ambient tenant
# (EM-T1b-2). None of them calls `commit()`: a commit inside a block ends
# SET LOCAL. The request-started jobs that remain on `_get_db` below carry an
# `# H4` marker each, and service identity binds NO ambient tenant.
from gateway.db import tenant_session as _tenant_session
from pydantic import BaseModel
from sqlalchemy import text
from acb_auth import require_feature_router

_log = get_logger("gateway.email")


router = APIRouter(
    prefix="/email", tags=["email"],
    # Org access control. Both OAuth legs are GATED (EM-T1a). The browser
    # reaches each one through a workbench BFF route that holds the session.
    # The callback must see the member, because that member must be the
    # member in the signed state (spec risk R-4).
    # Exempt: provider-initiated entrypoints with their own trust.
    #   webhook/microsoft— Graph change notification. It echoes the
    #                      validationToken, verifies the signed `org` in its
    #                      own URL, then checks clientState inside that tenant.
    dependencies=[require_feature_router("email", exempt=[
        "/email/webhook/microsoft",
    ])],
)


class EmailAddressModel(BaseModel):
    name: str = ""
    email: str


class AttachmentModel(BaseModel):
    id: str
    filename: str
    mime_type: str = "application/octet-stream"
    size_bytes: int | None = None
    download_url: str | None = None


MAX_BODY_TEXT_BYTES = 500 * 1024      # 500 KB


MAX_BODY_HTML_BYTES = 2 * 1024 * 1024  # 2 MB


ATTACHMENT_CACHE_TTL_SECS = 3600       # 1 hour


class EmailMessageModel(BaseModel):
    id: str
    provider_message_id: str
    thread_id: str | None = None
    account_id: str
    from_address: EmailAddressModel | None = None
    to_addresses: list[EmailAddressModel] = []
    cc_addresses: list[EmailAddressModel] = []
    bcc_addresses: list[EmailAddressModel] = []
    subject: str = ""
    body_text: str = ""
    body_html: str | None = None
    body_truncated: bool = False
    snippet: str = ""
    has_attachments: bool = False
    attachments: list[AttachmentModel] = []
    is_read: bool = False
    is_starred: bool = False
    is_flagged: bool = False
    importance: str = "normal"
    labels: list[str] = []
    categories: list[str] = []
    folder: str = "INBOX"
    received_at: str | None = None
    synced_at: str | None = None
    # When set and in the future, the conversation is snoozed out of the inbox
    # until this time (see migration 90). Null for the vast majority of mail.
    snoozed_until: str | None = None


def _truncate_body(text: str, max_bytes: int) -> str:
    """Truncate text to fit within max_bytes when UTF-8 encoded.

    Appends a truncation marker \"… [truncated]\" so the UI can offer a
    \"Load full message\" button.
    """
    if not text:
        return text
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= max_bytes:
        return text
    marker = b" ... [truncated]"
    # Find a safe cut point that doesn't split a multi-byte character
    cut = max_bytes - len(marker)
    while cut > 0 and (encoded[cut] & 0xC0) == 0x80:
        cut -= 1
    return encoded[:cut].decode("utf-8", errors="replace") + marker.decode()


async def _get_redis():
    """Get a Redis client for caching (skips if unavailable)."""
    try:
        import redis.asyncio as aioredis  # noqa: PLC0415
        settings = get_settings()
        return aioredis.from_url(settings.redis_url, decode_responses=False)
    except Exception:
        return None


def _instantiate_provider(provider_name: str, creds: dict[str, Any]):
    """Construct an email provider instance from its name + decrypted creds.

    Thin gateway adapter over ``email_ingestion.providers.build_provider`` (the
    single name→class factory): translates the factory's ``ValueError`` into an
    HTTPException(400) so route callers get a clean HTTP error.
    """
    from email_ingestion.providers.factory import build_provider
    try:
        return build_provider(provider_name, creds)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


async def _provider_for_message(db: Any, message_id: str, user_email: str):
    """Load the provider + provider_message_id for a stored message.

    Returns (provider, provider_message_id, account_id, store) or raises 404.
    Persisting rotated OAuth tokens is the caller's responsibility via
    ``_persist_rotated_creds``.
    """
    result = await db.execute(
        text(
            """SELECT em.provider_message_id, em.account_id,
                      ea.provider, ea.credentials_encrypted
               FROM email_messages em
               JOIN email_accounts ea ON em.account_id = ea.id
               WHERE em.id = :mid AND ea.user_id = :user_id"""
        ),
        {"mid": message_id, "user_id": user_email},
    )
    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Message not found")
    from acb_llm.key_store import get_key_store
    store = get_key_store()
    creds = json.loads(store.decrypt(row.credentials_encrypted))
    provider = _instantiate_provider(row.provider, creds)
    return provider, row.provider_message_id, str(row.account_id), store


async def _provider_for_account(db: Any, account_id: str, user_email: str):
    """Load the provider for an account (no specific message).

    Returns (provider, store, owner_email) or raises 404. Used by the draft
    write-path, which creates/updates/sends provider drafts that aren't tied to
    a single stored message. Persisting rotated creds is the caller's job.
    """
    row = (await db.execute(
        text(
            """SELECT provider, credentials_encrypted, email_address
               FROM email_accounts WHERE id = :id AND user_id = :uid"""
        ),
        {"id": account_id, "uid": user_email},
    )).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Account not found")
    from acb_llm.key_store import get_key_store
    store = get_key_store()
    creds = json.loads(store.decrypt(row.credentials_encrypted))
    provider = _instantiate_provider(row.provider, creds)
    return provider, store, row.email_address


async def _provider_for_account_any(db: Any, account_id: str):
    """Unscoped account loader — for BACKGROUND jobs (scheduler ticks, webhook
    tasks, fire-and-forget cleanups) that act on an account with no request user
    to scope by. Same contract as ``_provider_for_account`` (404 on a missing
    account, returns ``(provider, store, owner_email)``) minus the ownership
    filter, which a background job has no user to apply.
    """
    row = (await db.execute(
        text(
            """SELECT provider, credentials_encrypted, email_address
               FROM email_accounts WHERE id = :id"""
        ),
        {"id": account_id},
    )).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Account not found")
    from acb_llm.key_store import get_key_store
    store = get_key_store()
    creds = json.loads(store.decrypt(row.credentials_encrypted))
    provider = _instantiate_provider(row.provider, creds)
    return provider, store, row.email_address


async def _persist_rotated_creds(db: Any, store: Any, account_id: str, provider) -> None:
    """Persist refreshed OAuth tokens if the provider rotated them mid-request."""
    if provider.credentials_dirty():
        await db.execute(
            text(
                """UPDATE email_accounts
                   SET credentials_encrypted = :creds, updated_at = now()
                   WHERE id = :id"""
            ),
            {"id": account_id, "creds": store.encrypt(json.dumps(provider.export_credentials()))},
        )


@dataclass
class ProviderSession:
    """The live handle yielded by :func:`provider_session`.

    ``provider`` is authenticated (unless ``require_auth=False`` and it failed —
    then ``authed`` is False and the caller decides what to do). ``account_id``
    and ``store`` are what a persist needs; ``provider_message_id`` is set only
    for the message-scoped form, ``owner_email`` only for the account-scoped
    form (each is the extra datum its ``_provider_for_*`` loader returns).
    """

    provider: Any
    account_id: str
    store: Any
    authed: bool = False
    provider_message_id: str | None = None
    owner_email: str | None = None


@asynccontextmanager
async def provider_session(
    db: Any,
    user_email: str | None,
    *,
    account_id: str | None = None,
    message_id: str | None = None,
    require_auth: bool = True,
):
    """Instantiate → authenticate → (on clean exit) persist rotated creds.

    The three-step provider dance — load+decrypt+instantiate, ``authenticate()``,
    and ``_persist_rotated_creds`` — was hand-copied at ~a dozen call sites, each
    an opportunity to forget the persist (dropping a refreshed OAuth token, so
    the NEXT request re-auths from a stale refresh token and eventually fails).
    This is that dance in ONE place.

    Pass EITHER ``message_id`` (message-scoped: yields ``provider_message_id``)
    OR ``account_id`` (account-scoped: yields ``owner_email``). A 404 loader
    error propagates unchanged.

    ``user_email=None`` selects the UNSCOPED account loader — for background
    jobs (scheduler ticks, webhook tasks, fire-and-forget cleanups) that act on
    an account with no request user to scope by. Message-scoped sessions always
    require a user (no background path loads by message today).

    ``require_auth=True`` (default) raises ``HTTPException(401)`` on auth failure,
    matching the send/draft write-paths. ``require_auth=False`` yields with
    ``authed=False`` so best-effort readers (body hydrate, the cleaner's
    categorize pass) keep their own skip-or-abort handling.

    The rotated-cred persist runs in a ``finally`` ONLY on a clean exit — never
    after the body raised (a half-failed request must not commit a token write
    onto a session that is about to roll back). This preserves the pre-refactor
    "persist after the work succeeds" ordering exactly; the caller still owns the
    ``db.commit()`` boundary, so nothing here changes when the transaction lands.
    """
    if message_id is not None:
        if user_email is None:
            raise ValueError("message-scoped provider_session needs a user")
        provider, pmid, account_id, store = await _provider_for_message(
            db, message_id, user_email,
        )
        sess = ProviderSession(
            provider=provider, account_id=account_id, store=store,
            provider_message_id=pmid,
        )
    elif account_id is not None:
        if user_email is None:
            provider, store, owner = await _provider_for_account_any(
                db, account_id,
            )
        else:
            provider, store, owner = await _provider_for_account(
                db, account_id, user_email,
            )
        sess = ProviderSession(
            provider=provider, account_id=account_id, store=store,
            owner_email=owner,
        )
    else:
        raise ValueError("provider_session needs account_id or message_id")

    sess.authed = await provider.authenticate()
    if require_auth and not sess.authed:
        raise HTTPException(
            status_code=401, detail="Email account authentication failed",
        )

    raised = False
    try:
        yield sess
    except BaseException:
        raised = True
        raise
    finally:
        if not raised:
            await _persist_rotated_creds(db, sess.store, sess.account_id, provider)


async def hydrate_message_body(db: Any, message_id: str, user_email: str) -> str:
    """Ensure a message's full ``body_text`` is present, fetching it if needed.

    Some providers (notably Outlook/Graph) sync message *headers* only, so the
    stored ``body_text`` is empty and only a ~200-char ``snippet`` exists.  Any
    consumer that does ``body_text or snippet`` then silently operates on the
    200-char preview — which for the reply drafter meant it saw the incoming
    message cut off mid-sentence (e.g. "…I am ava") and wrote that truncation
    into the draft.  This helper fetches the full body from the provider ONCE
    and persists it, so the drafter (and any other reader) gets the real body.

    Returns the full ``body_text`` (possibly still empty if the provider has no
    text body, e.g. a pure-HTML or attachment-only message).  Best-effort: a
    provider/auth failure logs and returns whatever is already stored — never
    raises, so it can't break a draft/classify call.
    """
    row = (await db.execute(
        text("SELECT body_text, body_html, snippet FROM email_messages WHERE id = :id"),
        {"id": message_id},
    )).fetchone()
    if row is None:
        return ""
    if (row.body_text or "").strip():
        return row.body_text  # already hydrated
    # Header-only row: fetch the full body from the provider and persist it.
    try:
        async with provider_session(
            db, user_email, message_id=message_id, require_auth=False,
        ) as sess:
            if not sess.authed:
                return row.body_text or ""
            full = await sess.provider.get_message(sess.provider_message_id)
            body_text = _truncate_body(full.body_text or "", MAX_BODY_TEXT_BYTES)
            body_html = (
                _truncate_body(full.body_html, MAX_BODY_HTML_BYTES)
                if full.body_html else None
            )
            await db.execute(
                text(
                    """UPDATE email_messages
                       SET body_text = :bt, body_html = :bh, updated_at = now()
                       WHERE id = :id"""
                ),
                {"id": message_id, "bt": body_text, "bh": body_html},
            )
        # No commit here: the CALLER owns the transaction boundary. Converted
        # request handlers pass a `_tenant_session` session, and a mid-block
        # commit would end that transaction and silently drop the tenant GUC
        # for everything after it (H2); background callers commit their own
        # session as before, and a rolled-back hydrate persist just re-fetches
        # next time — it is a cache write, not state.
        return body_text
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 — never break the caller on a hydrate miss
        import structlog  # noqa: PLC0415
        structlog.get_logger("email.core").warning(
            "hydrate_message_body.failed", message_id=message_id, error=str(exc)[:200],
        )
        return row.body_text or ""


# ── DB (the one shared gateway engine — gateway/db.py, BO-10) ────────────────
#
# This package used to build its own engine here with its own 10+20 pool — the
# engine that drained on 2026-08-06 behind a transaction left `idle in
# transaction` for 14h44m. Both bounds that came out of that incident now live
# on the shared seam (``acb_common.db.engine_connect_args``, re-exported as
# ``gateway.db``), which is where the reasoning for the 10-minute deadline is
# written down.
#
# `_get_session_factory` at the top of this module is a re-export of that seam.
# `_get_db` stays a real function here only to keep its signature: callers pass
# nothing today, but the parameter is part of the historical surface this
# package flattens into ``gateway.routes.email`` (see __init__.py), and tests
# monkeypatch `_get_db` on the sibling module they exercise.


async def _get_db(request_id: str | None = None):
    """Return a new async session from the shared, pooled engine."""
    return _get_session_factory()()


def _account_scope(account_id: str | None, params: dict[str, Any]) -> str:
    """Return a SQL fragment scoping email_messages `em` to the user's accounts.

    Adds :uid (and optionally :aid) to `params`. The caller must have already
    set params["uid"] to the user's email.
    """
    frag = "em.account_id IN (SELECT id FROM email_accounts WHERE user_id = :uid"
    if account_id:
        frag += " AND id = :aid"
        params["aid"] = account_id
    frag += ")"
    return frag


# The scope sentinel behind both the sidebar's All folder and the search bar's
# "All folders" scope. The sentinel is shared; the SET it resolves to is not —
# see the two constants below, and folder_scope's `include_sent`. That split is
# deliberate and is the one place "all" is allowed to mean two things.
FOLDER_ALL = "all"

# What "everything" deliberately leaves out when BROWSING.
#
# Junk and trash are mail the user has already thrown away — sweeping them into
# an unscoped view would bury real mail under spam. Sent and drafts are excluded
# for a different reason: All is a view of mail that ARRIVED, and neither of them
# ever did. Sent doubles every conversation and makes the list read as a log
# rather than an inbox (442 of the live account's messages); a draft is unfinished
# text with no counterparty, and it belongs in a composer rather than a reading
# list. A reply is still visible where it belongs — the thread view ignores the
# folder filter entirely, so opening a conversation shows both sides.
#
# All four stay reachable by selecting the folder explicitly. There is no
# "spam" entry because there is no such folder to exclude: providers/base.py
# canonicalises spam → junk on ingestion, for Gmail as well as Outlook, so junk
# already covers it. Listing it would be dead weight that reads as coverage.
# "draft" needs no entry either, for the same reason — it canonicalises to
# "drafts".
FOLDER_ALL_EXCLUDES = ("junk", "trash", "sent", "drafts")

# What "All folders" leaves out when SEARCHING — deliberately NOT the same set.
#
# Browsing All answers "what came in?"; searching All answers "where is that
# message?", and "what did I tell them?" is one of the commonest reasons to
# search at all. A scope labelled "All folders" that silently skipped the user's
# own sent mail would be a worse defect than the one excluding it from the list
# fixes. The same holds for drafts: half-written text is a poor thing to browse
# but a perfectly good thing to go looking for. Junk/trash stay out of both —
# search offers them as explicit scopes.
FOLDER_ALL_SEARCH_EXCLUDES = ("junk", "trash")


# ── Label vocabulary ────────────────────────────────────────────────────────
# Every label the RULE ENGINE writes to email_messages.categories. It lives in
# core (not in automation) because both layers need the same answer to "is this
# message categorized?" — the Email Cleaner's Uncategorized tab and the inbox's
# Uncategorized chip look at the same mailbox, and two definitions of
# "uncategorized" in two views of one mailbox is exactly the drift this package
# keeps paying down.

# Sender-stable cleanup categories (the preset cleanup rules).
CLEANUP_CATEGORIES = (
    "Newsletter", "Marketing", "Receipt", "Calendar", "Notification", "Cold Email",
)
# Reply Zero conversation labels, plus the legacy names from before the rename —
# mail stamped with an old name is still categorized and must not resurface as
# uncategorized.
CONVERSATION_LABELS_LOWER = frozenset({
    "needs reply", "awaiting reply", "fyi", "done", "follow-up",
    # Legacy names still present on provider-side messages the reconciler
    # hasn't replaced yet ("Reply"/"To Reply" → "Needs Reply", "Actioned" →
    # "Done") — kept so old-labelled mail doesn't read as uncategorized.
    "reply", "to reply", "actioned",
})
KNOWN_LABELS_LOWER: list[str] = (
    [c.lower() for c in CLEANUP_CATEGORIES] + sorted(CONVERSATION_LABELS_LOWER)
)

# SENDER-level category for someone there is an ongoing exchange with: several
# of their messages carry conversation labels and none was ever filed as bulk
# mail. Unlike the cleanup categories this is never written onto a message and
# never syncs to the provider — it is derived per sender (senders._rule_category)
# and shown only in the Email Cleaner.
#
# Was "Personal" until 2026-07-20. The word implied private-life mail; every
# sender it matched was in fact a work colleague or client, and it collided
# with Cold Email — also a person writing one-to-one, just a stranger. The
# distinguishing signal is the ongoing conversation, so the name says that.
CONVERSATION_SENDER_CATEGORY = "Conversation"
# Sender categories that mark a human correspondent, so mail from them ranks
# higher in "important emails". Bound as a query parameter — defined ONCE here
# because the producer (senders) and the consumer (messages) are in different
# modules and a rename that reached only one of them would silently drop the
# boost rather than fail.
HUMAN_SENDER_CATEGORIES_LOWER: list[str] = [
    CONVERSATION_SENDER_CATEGORY.lower(), "support",
]

# "Uncategorized" = carries none of the labels above. NOT "has no labels at
# all": a user's own hand-made label doesn't make a message categorized as far
# as the rules are concerned, and treating it as such would hide exactly the
# mail the cleaner exists to find.
UNCATEGORIZED_SQL = (
    "NOT EXISTS (SELECT 1 FROM unnest(COALESCE(em.categories, '{}')) AS c"
    " WHERE LOWER(TRIM(c)) = ANY(:known_labels))"
)

# "Uncategorized" is a STATE — the absence of any known label — never a label
# itself. It must not be writable as a category anywhere: not by a rule's LABEL
# action (an AI-resolved {{...}} label could produce it), not by the manual
# label endpoint, not synced in from the provider. Every category writer checks
# this set; letting it through would turn the indicator into a real provider
# category that then reads as "categorized" forever.
RESERVED_INDICATORS = frozenset({"uncategorized"})


def folder_scope(
    folder: str | None,
    params: dict[str, Any],
    *,
    include_sent: bool = False,
) -> str | None:
    """SQL scoping `em` to a folder, or None when the scope spans every folder.

    Handles the two pseudo-folders the UI offers alongside real ones:
      • ``all``     — every folder except junk/trash/spam, and (unless
                      ``include_sent``) the user's own sent mail
      • ``starred`` — a flag, not a stored folder

    ``include_sent`` is for SEARCH, the one caller that means "look everywhere":
    browsing All is a view of what arrived, searching All is a hunt for a
    message, and the user's own replies are a legitimate target of the second.
    Callers that render a folder listing — the message list and its facet chips
    — must leave it False, or the chip counts would describe a different set of
    mail than the list under them.

    Adds :folder / :folder_excludes to `params` as needed.
    """
    key = (folder or "").strip().lower()
    if not key:
        return None
    if key == FOLDER_ALL:
        params["folder_excludes"] = list(
            FOLDER_ALL_SEARCH_EXCLUDES if include_sent else FOLDER_ALL_EXCLUDES)
        return "LOWER(em.folder) <> ALL(:folder_excludes)"
    if key == "starred":
        return "em.is_starred = true"
    if key == "snoozed":
        # The virtual Snoozed view: conversations still sleeping. (Every OTHER
        # browse excludes these — that filter lives in list_messages so it also
        # covers All/user folders, which don't route through here.)
        return "em.snoozed_until > now()"
    params["folder"] = folder
    return "LOWER(em.folder) = LOWER(:folder)"


async def _assert_account_owner(db: Any, account_id: str, user_email: str) -> None:
    """Raise 404 if the account isn't owned by the user."""
    res = await db.execute(
        text("SELECT 1 FROM email_accounts WHERE id = :id AND user_id = :uid"),
        {"id": account_id, "uid": user_email},
    )
    if not res.fetchone():
        raise HTTPException(status_code=404, detail="Account not found")


def _safe_json(content: str) -> Any | None:
    """Extract a JSON object/array from an LLM response.

    Tolerates ``` fences, leading prose, and trailing commentary. First tries a
    naive first-open→last-close slice; if that doesn't parse (e.g. prose contains
    a stray brace, or there's text after the JSON), falls back to a string-aware
    balanced-bracket scan that returns the first complete {...}/[...] span. Still
    returns None for genuinely truncated JSON (no matching close)."""
    if not content:
        return None
    s = content.strip()
    if s.startswith("```"):
        s = s.split("```", 2)[1] if "```" in s[3:] else s.strip("`")
        if s.startswith("json"):
            s = s[4:]
    start = min((i for i in (s.find("{"), s.find("[")) if i >= 0), default=-1)
    if start < 0:
        return None
    s = s[start:]
    # Fast path: trim to the last closing bracket and parse.
    end = max(s.rfind("}"), s.rfind("]"))
    if end >= 0:
        try:
            return json.loads(s[:end + 1])
        except Exception:  # noqa: BLE001 — fall through to the tolerant scan
            pass
    # Backstop: decode the first valid JSON value starting at any '{'/'[',
    # ignoring trailing text and stray braces in surrounding prose. Truncated
    # JSON (no matching close) still fails, returning None.
    decoder = json.JSONDecoder()
    for i, ch in enumerate(s):
        if ch in "{[":
            try:
                obj, _ = decoder.raw_decode(s, i)
                return obj
            except Exception:  # noqa: BLE001 — try the next opening bracket
                continue
    return None


async def _llm_json(
    model: str,
    messages: list[dict[str, Any]],
    *,
    max_tokens: int,
    temperature: float = 0.0,
) -> tuple[Any, str, str]:
    """The single seam for the email package's "ask the LLM, get JSON" calls.

    Runs a JSON-mode chat completion via ``acompletion_with_fallback`` — forcing
    ``response_format={"type": "json_object"}`` (dropped automatically for models
    that don't support it) — and parses the reply with :func:`_safe_json`.

    Returns ``(data, content, used_model)``:
    - ``data`` — the parsed JSON, or ``None`` when the reply wasn't valid JSON;
    - ``content`` — the raw reply text (for logging an unparseable sample);
    - ``used_model`` — the model the fallback chain actually used.

    Errors are the caller's concern: each call site wraps this in its own
    try/except with a fail-closed default, so this helper does not swallow
    exceptions.
    """
    from acb_llm.context import acompletion_with_fallback
    resp, used = await acompletion_with_fallback(
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        response_format={"type": "json_object"},
    )
    content = resp.choices[0].message.content or ""
    return _safe_json(content), content, used


def _fmt_addr_list(field: Any) -> str:
    """A JSONB ``[{name, email}]`` list → ``"Name <email>, …"`` for an LLM prompt
    (To/Cc rendering). Empty string when there are none. Mirrors the classifier's
    recipient formatter so every engine renders recipients identically."""
    try:
        items = field if isinstance(field, list) else json.loads(field or "[]")
    except Exception:  # noqa: BLE001
        return ""
    out: list[str] = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        em, nm = (it.get("email") or "").strip(), (it.get("name") or "").strip()
        if em and nm:
            out.append(f"{nm} <{em}>")
        elif em or nm:
            out.append(em or nm)
    return ", ".join(out)


async def _attachment_summaries(db: Any, message_ids: Any) -> dict[str, str]:
    """Batched: ``{str(message_id): "Attachments: invoice.pdf (application/pdf),
    q3.xlsx (…)"}`` for the messages that HAVE attachments. One query, so callers
    can enrich an LLM prompt with attachment metadata (filename + MIME) without an
    N+1. Empty dict on error / none — callers simply omit the line. Metadata only;
    extracting attachment TEXT (PDF/doc) is a separate, larger feature."""
    ids = [str(m) for m in (message_ids or []) if m]
    if not ids:
        return {}
    try:
        rows = (await db.execute(text(
            "SELECT message_id, filename, mime_type FROM email_attachments "
            "WHERE message_id::text = ANY(:ids) ORDER BY filename"
        ), {"ids": ids})).fetchall()
    except Exception:  # noqa: BLE001 — table optional / DB hiccup
        return {}
    by_msg: dict[str, list[str]] = {}
    for r in rows:
        name = (getattr(r, "filename", None) or "file").strip()
        mime = (getattr(r, "mime_type", None) or "").strip()
        mid = str(getattr(r, "message_id", "") or "")
        if mid:
            by_msg.setdefault(mid, []).append(
                f"{name} ({mime})" if mime else name)
    return {mid: "Attachments: " + ", ".join(p) for mid, p in by_msg.items()}


def _parse_iso_date(s: str | None, end_of_day: bool) -> datetime | None:
    """Parse a 'YYYY-MM-DD' string into a UTC datetime (or None)."""
    if not s:
        return None
    try:
        d = datetime.strptime(s.strip()[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        if end_of_day:
            d = d.replace(hour=23, minute=59, second=59)
        return d
    except (ValueError, TypeError):
        return None


def _date_range_clause(
    account_id: str, start: datetime | None, end: datetime | None,
    only_unread: bool = False, unprocessed_only: bool = False,
) -> tuple[str, dict[str, Any]]:
    """SQL WHERE clause (+ params) for inbox mail in a received_at date range.

    ``unprocessed_only`` restricts to mail the rules have never run over
    (``rules_processed_at IS NULL``). Overlapping backfills are the normal case —
    the date picker is a range, not a cursor, so widening it re-covers everything
    already done. Each of those messages costs a classification call and rewrites
    a label it already has, so skipping them is both cheaper and quieter.

    The watermark is stamped only by runs that actually applied (live, with a
    working provider), so a dry run or a provider-auth failure leaves mail
    eligible rather than silently consuming it.

    It deliberately does NOT check ``rules_held_back_at``. That column marks mail
    "Clean older mail" downloaded and kept away from the AUTOMATIC per-cycle run
    (migration 84); a deliberate, bounded, user-initiated run over a date range
    is exactly the case the hold-back leaves room for. Adding the guard here
    would make backfilled history permanently un-categorizable.
    """
    clause = "em.account_id = :aid AND LOWER(em.folder) = 'inbox'"
    params: dict[str, Any] = {"aid": account_id}
    if start is not None:
        clause += " AND em.received_at >= :start"
        params["start"] = start
    if end is not None:
        clause += " AND em.received_at <= :end"
        params["end"] = end
    if only_unread:
        clause += " AND em.is_read = false"
    if unprocessed_only:
        clause += " AND em.rules_processed_at IS NULL"
    return clause, params


def _default_label(provider: str) -> str:
    labels = {"gmail": "Gmail", "microsoft": "Outlook", "imap": "Email"}
    return labels.get(provider, "Email")


def email_memory_scope(user_email: str, account_id: str | None) -> str:
    """Namespace email-assistant Mem0 memory PER connected account.

    A user with several inboxes (work + personal) must not have one account's
    learned writing style / reply preferences leak into another's drafting.
    Mem0 keys by a single ``user_id`` string, so we fold the account id into it.

    CRITICAL: reads (``remember`` / ``get_memory_context``) and writes
    (``add_memories_background``) for a given account MUST both pass the value
    returned here, or retrieval silently misses. Falls back to the bare user
    email when no account is resolved (legacy / cross-account global scope).

    This is used ONLY for the gateway-side direct Mem0 calls. It is deliberately
    NOT pushed into the agent's memory ContextVar — the email-assistant reuses
    that same var as its ``X-User-Email`` gateway-auth identity, so a scoped
    value there would break the agent's tool calls.
    """
    uid = (user_email or "").strip().lower()
    aid = (account_id or "").strip()
    return f"{uid}#acct:{aid}" if (uid and aid) else uid


def _is_body_truncated(body_text: str, body_html: str) -> bool:
    """Check whether a stored message body was truncated at sync time."""
    if body_text and len(body_text.encode("utf-8", errors="replace")) >= MAX_BODY_TEXT_BYTES:
        return True
    if body_html and len(body_html.encode("utf-8", errors="replace")) >= MAX_BODY_HTML_BYTES:
        return True
    return False


def _row_to_message(row: Any) -> EmailMessageModel:
    """Convert a database row to an EmailMessageModel."""
    def _parse_jsonb(val: Any) -> Any:
        if val is None:
            return None
        if isinstance(val, str):
            return json.loads(val)
        return val

    def _parse_address_list(val: Any) -> list[EmailAddressModel]:
        data = _parse_jsonb(val) or []
        if isinstance(data, list):
            return [
                EmailAddressModel(
                    name=a.get("name", ""),
                    email=a.get("email", ""),
                )
                for a in data
            ]
        return []

    def _parse_address(val: Any) -> EmailAddressModel | None:
        data = _parse_jsonb(val)
        if data:
            return EmailAddressModel(
                name=data.get("name", ""),
                email=data.get("email", ""),
            )
        return None

    return EmailMessageModel(
        id=str(row.id),
        provider_message_id=row.provider_message_id,
        thread_id=row.thread_id,
        account_id=str(row.account_id),
        folder=row.folder,
        labels=list(row.labels) if row.labels else [],
        from_address=_parse_address(row.from_address),
        to_addresses=_parse_address_list(row.to_addresses),
        cc_addresses=_parse_address_list(row.cc_addresses),
        bcc_addresses=_parse_address_list(row.bcc_addresses),
        subject=row.subject or "",
        body_text=row.body_text or "",
        body_html=row.body_html,
        body_truncated=_is_body_truncated(
            row.body_text or "", row.body_html or ""
        ),
        snippet=row.snippet or "",
        has_attachments=row.has_attachments or False,
        attachments=[],  # populated by get_message endpoint
        is_read=row.is_read or False,
        is_starred=row.is_starred or False,
        is_flagged=row.is_flagged or False,
        importance=getattr(row, "importance", None) or "normal",
        categories=list(row.categories) if getattr(row, "categories", None) else [],
        received_at=row.received_at.isoformat() if row.received_at else None,
        synced_at=row.synced_at.isoformat() if row.synced_at else None,
        snoozed_until=(
            row.snoozed_until.isoformat()
            if getattr(row, "snoozed_until", None) else None),
    )


async def _upsert_message(db: Any, account_id: str, msg: Any) -> None:
    """Insert/update one normalized provider message into ``email_messages``.

    Thin gateway adapter over the shared ingest helper
    (:func:`email_ingestion.persist.upsert_message`) — the ONE upsert every
    ingest path shares. Used here by the on-demand history backfill.
    """
    from email_ingestion.persist import upsert_message

    await upsert_message(db, account_id, msg)


async def _fetch_attachments(db: Any, message_id: str) -> list[AttachmentModel]:
    """Fetch attachment metadata for a message."""
    result = await db.execute(
        text(
            "SELECT id, filename, mime_type, size_bytes, download_url "
            "FROM email_attachments WHERE message_id = :mid ORDER BY filename"
        ),
        {"mid": message_id},
    )
    rows = result.fetchall()
    gateway_url = os.environ.get("GATEWAY_EXTERNAL_URL", "")
    return [
        AttachmentModel(
            id=str(r.id),
            filename=r.filename,
            mime_type=r.mime_type,
            size_bytes=r.size_bytes,
            download_url=f"{gateway_url}/email/attachments/{r.id}/download" if gateway_url else None,
        )
        for r in rows
    ]


async def _fetch_attachments_batch(
    db: Any, message_ids: list[str]
) -> dict[str, list[AttachmentModel]]:
    """Attachment metadata for MANY messages in one query, keyed by message id.

    The conversation/thread list uses this so EVERY message in the thread carries
    its own attachments (the single-message detail path uses _fetch_attachments).
    Mirrors that helper's download_url construction. Returns {} on no input."""
    out: dict[str, list[AttachmentModel]] = {}
    if not message_ids:
        return out
    result = await db.execute(
        text(
            "SELECT message_id, id, filename, mime_type, size_bytes "
            "FROM email_attachments WHERE message_id::text = ANY(:mids) "
            "ORDER BY message_id, filename"
        ),
        {"mids": [str(m) for m in message_ids]},
    )
    gateway_url = os.environ.get("GATEWAY_EXTERNAL_URL", "")
    for r in result.fetchall():
        out.setdefault(str(r.message_id), []).append(
            AttachmentModel(
                id=str(r.id),
                filename=r.filename,
                mime_type=r.mime_type,
                size_bytes=r.size_bytes,
                download_url=f"{gateway_url}/email/attachments/{r.id}/download"
                if gateway_url else None,
            )
        )
    return out
