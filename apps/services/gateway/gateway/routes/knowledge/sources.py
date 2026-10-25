"""Knowledge · sources — connect Google Drive, read its tree, disconnect (WS-61 KC-1a).

Spec: ``project-docs/specs/knowledge_curation.md`` §7.1, §7.2 steps 1 to 3,
§7.4, §16, §17 and §22, and ``project-docs/specs/knowledge_base.md`` §7.1,
§7.4 and §7.5.

    POST    /knowledge/sources/google_drive/connect    the consent URL
    GET     /knowledge/sources/google_drive/callback   finish the consent
    GET     /knowledge/sources                         the visible sources
    GET     /knowledge/sources/{source_id}/tree        one level of the tree
    DELETE  /knowledge/sources/{source_id}             disconnect

⚠️ **Dark twice, and three times for Google.** The router answers 404 unless
Knowledge serves the org (``KNOWLEDGE_ENABLED``, ``KNOWLEDGE_ORGS``). Each
route here also needs ``KNOWLEDGE_SOURCES`` (``flags.require_knowledge_sources``),
and the connect and the callback need ``KNOWLEDGE_SOURCE_GOOGLE`` too. Off
means 404.

**Who sees what.** A member sees and disconnects their own sources only. A
holder of ``knowledge:manage`` sees and disconnects every source of the org.
A source the caller may not see answers 404, never 403.

**The org and the member come from the session (R5, R11).** The callback
refuses, with 400 and NO call to Google, a state that is forged, expired,
signed for another purpose, or made for another org or member than the
session's (``signing.sign_state`` with :data:`STATE_PURPOSE`).

**The one Google client.** ``oauth_app("gmail")`` is the ONE reader of the
client id and secret. :func:`google_client` reads it here, in the route, and
passes it into the adapter. Unset answers 503 ``google_not_configured``.

**The grant check of the callback.** A grant must hold ``drive.readonly``,
nothing outside ``auth.ALLOWED_SCOPES``, and a refresh token
(:func:`drive_grant_refusal`, the shape of ``_gmail_grant_refusal``). A
refused grant saves nothing, and it is NOT revoked: the grant may be the
Gmail grant of the same Google account.

**The tree answer is metadata only.** No answer carries a ``text``, a
``content`` or a ``body`` key.

**Disconnect (§7.4).** In ONE transaction: the source becomes
``disconnected``, its nodes are DELETED, and ``store.disconnect`` deletes the
blob. After the commit, with no session open, the route asks the two
shared-grant functions of migration 300. It calls Google's revoke ONLY when
both answer false. ``true`` and ``NULL`` both skip it, as
``routes/email/teardown.py`` does for Gmail. The source row stays as a
record, so a reconnect of the same connection reuses it.
"""

from __future__ import annotations

import asyncio
import re
import uuid
from dataclasses import replace
from typing import Any

from acb_auth import UserContext, get_current_user
from email_ingestion.providers.app_credentials import oauth_app
from fastapi import BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from gateway.connections import store
from gateway.kb_sources.base import (
    SCOPE_MEMBER,
    KbSource,
    OAuthClientConfig,
    SourceCredential,
    SourceError,
)
from gateway.kb_sources.google_drive import auth as google_auth
from gateway.kb_sources.google_drive.client import PROVIDER as GOOGLE_DRIVE
from gateway.kb_sources.google_drive.client import GoogleDriveSource
from gateway.kb_sync import discovery
from gateway.routes.email import teardown
from gateway.routes.email.transport.oauth import _workbench_public_url
from gateway.routes.email.transport.signing import (
    SigningUnavailable,
    sign_state,
    verify_state,
)
from gateway.routes.knowledge import core, flags
from pydantic import BaseModel
from sqlalchemy import text

_log = core._log

#: The owning app of a Knowledge connection.
OWNING_APP = discovery.OWNING_APP

#: The purpose of the signed state. A value signed for another purpose never
#: verifies here (``signing.py``).
STATE_PURPOSE = "kb_source_google"

#: The path the owner registers in the Google console, after the workbench
#: origin. The workbench callback route forwards it to ``CALLBACK_ROUTE``.
REDIRECT_PATH = "/api/knowledge/sources/google_drive/callback"
CALLBACK_ROUTE = "/sources/google_drive/callback"

#: Where the callback sends the browser. Relative, as the CRM callback is.
RETURN_OK = "/knowledge?source_connected=google_drive"
RETURN_FAILED = "/knowledge?source_error="

#: The nil UUID: the Drive disconnect excludes no ``email_accounts`` row.
NIL_UUID = "00000000-0000-0000-0000-000000000000"

#: The sibling function of migration 300: another live Drive connection.
DRIVE_GRANT_SHARED_SQL = (
    "SELECT public.kb_google_drive_grant_shared(:email, CAST(:exclude AS uuid))")

#: The most nodes one tree answer carries, and the default.
TREE_MAX = 2000
TREE_DEFAULT = 500

#: A provider id in a query. Anything else answers 422 before the database.
_EXT_ID = re.compile(r"^[A-Za-z0-9_-]{1,200}$")

#: One callback at a time for each org and member, until the commit.
_CONNECT_LOCK_SQL = "SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"

SOURCE_COLUMNS = (
    "s.id, s.provider, s.kind, s.owner_email, s.state, s.account_email, "
    "s.node_count, s.discovered_at, s.last_error, s.created_at, s.updated_at"
)

#: The one predicate of who sees a source. A manager sees all of the org.
_VISIBLE = (
    "s.organization_id = CAST(:org AS uuid) "
    "AND (CAST(:manager AS boolean) OR s.owner_email = :me)"
)

_LIST_SQL = f"SELECT {SOURCE_COLUMNS} FROM kb_sources s WHERE {_VISIBLE} ORDER BY s.created_at, s.id"

_ONE_SQL = f"SELECT {SOURCE_COLUMNS} FROM kb_sources s WHERE s.id = CAST(:id AS uuid) AND {_VISIBLE}"

_LOCK_ONE_SQL = (
    f"SELECT {SOURCE_COLUMNS}, s.connection_id FROM kb_sources s "
    f"WHERE s.id = CAST(:id AS uuid) AND {_VISIBLE} FOR UPDATE"
)

_UPSERT_SOURCE_SQL = (
    "INSERT INTO kb_sources AS s (organization_id, connection_id, provider, kind, "
    "owner_email, state, account_email, account_id, created_at, updated_at) "
    "VALUES (CAST(:org AS uuid), CAST(:connection AS uuid), :provider, 'member', :me, "
    "'discovering', :account_email, :account_id, now(), now()) "
    "ON CONFLICT (organization_id, connection_id) DO UPDATE SET "
    "state = 'discovering', last_error = NULL, owner_email = EXCLUDED.owner_email, "
    "account_email = EXCLUDED.account_email, account_id = EXCLUDED.account_id, "
    "updated_at = now() "
    "RETURNING s.id"
)

_ACCOUNT_SQL = (
    "SELECT id, account_id FROM kb_sources WHERE organization_id = CAST(:org AS uuid) "
    "AND connection_id = CAST(:connection AS uuid) FOR UPDATE"
)

_DELETE_NODES_SQL = (
    "DELETE FROM kb_source_nodes "
    "WHERE organization_id = CAST(:org AS uuid) AND source_id = CAST(:source AS uuid)"
)

_DISCONNECTED_SQL = (
    "UPDATE kb_sources SET state = 'disconnected', node_count = 0, last_error = NULL, "
    "updated_at = now() WHERE id = CAST(:id AS uuid) AND organization_id = CAST(:org AS uuid)"
)

NODE_COLUMNS = (
    "n.ext_id, n.parent_ext_id, n.drive_id, n.kind, n.name, n.mime, n.size, "
    "n.modified_at, n.web_url, n.explored, EXISTS (SELECT 1 FROM kb_source_nodes c "
    "WHERE c.organization_id = n.organization_id AND c.source_id = n.source_id "
    "AND c.parent_ext_id = n.ext_id AND c.state = 'live') AS has_children"
)

#: A node of a disconnected source never reaches an answer (review P2), also
#: when it outlived the disconnect.
_TREE_WHERE = (
    "n.organization_id = CAST(:org AS uuid) AND n.source_id = CAST(:source AS uuid) "
    "AND n.state = 'live' AND EXISTS (SELECT 1 FROM kb_sources live "
    "WHERE live.id = n.source_id AND live.organization_id = n.organization_id "
    "AND live.state <> 'disconnected')"
)

#: The roots: a node with no parent, or whose parent the source does not hold
#: (a file shared with the member from a folder they cannot open).
_ROOTS_SQL = (
    f"SELECT {NODE_COLUMNS} FROM kb_source_nodes n WHERE {_TREE_WHERE} "
    "AND (n.parent_ext_id IS NULL OR NOT EXISTS (SELECT 1 FROM kb_source_nodes p "
    "WHERE p.organization_id = n.organization_id AND p.source_id = n.source_id "
    "AND p.ext_id = n.parent_ext_id AND p.state = 'live')) "
    "ORDER BY (n.kind = 'folder') DESC, lower(n.name), n.ext_id LIMIT :limit"
)

_CHILDREN_SQL = (
    f"SELECT {NODE_COLUMNS} FROM kb_source_nodes n WHERE {_TREE_WHERE} "
    "AND n.parent_ext_id = :parent "
    "ORDER BY (n.kind = 'folder') DESC, lower(n.name), n.ext_id LIMIT :limit"
)


class ConnectModel(BaseModel):
    url: str


# ── Helpers ─────────────────────────────────────────────────────────────────


def _organization(user: UserContext) -> str:
    """The caller's organization, from the server. None is a 403."""
    if not user.organization_id:
        raise HTTPException(status_code=403, detail="Forbidden: you are in no organization.")
    return str(user.organization_id)


def _member(user: UserContext) -> str:
    """The caller's address. A source belongs to a member, so none is a 403."""
    me = core.viewer_email(user)
    if not me:
        raise HTTPException(status_code=403, detail="Forbidden: a source needs a member.")
    return me


def _scope(user: UserContext) -> dict[str, Any]:
    return {"org": _organization(user), "me": core.viewer_email(user),
            "manager": core.is_manager(user)}


def _source_id(value: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except ValueError:
        raise HTTPException(status_code=404, detail="Source not found.") from None


def redirect_uri() -> str:
    """The redirect URI that Google sees. It must equal the console value."""
    return f"{_workbench_public_url()}{REDIRECT_PATH}"


def google_client() -> OAuthClientConfig:
    """Metorite's one Google client, read HERE and passed into the adapter.

    503 ``google_not_configured`` when the id or the secret is unset.
    """
    app = oauth_app("gmail")
    if not app.configured:
        raise HTTPException(status_code=503, detail="google_not_configured")
    return OAuthClientConfig(
        client_id=app.client_id, client_secret=app.client_secret,
        redirect_uri=redirect_uri(),
    )


def make_source(client: OAuthClientConfig) -> KbSource:
    """The Google Drive adapter on *client*. A test replaces it with a fake."""
    return GoogleDriveSource(client)


def drive_grant_refusal(cred: SourceCredential) -> str | None:
    """The bounce reason when a Google grant cannot serve a Drive source.

    ``None`` when the grant holds ``drive.readonly``, holds no scope outside
    ``auth.ALLOWED_SCOPES``, and carries a refresh token. The shape of
    ``routes/email/transport/oauth._gmail_grant_refusal``.
    """
    granted = set(cred.scopes)
    if google_auth.DRIVE_READONLY not in granted:
        _log.warning("knowledge.source_scope_missing", provider=GOOGLE_DRIVE)
        return "scope_missing"
    extra = sorted(granted - google_auth.ALLOWED_SCOPES)
    if extra:
        _log.warning("knowledge.source_scope_extra", provider=GOOGLE_DRIVE, extra=extra)
        return "scope_extra"
    if not cred.refresh_token:
        _log.warning("knowledge.source_no_refresh_token", provider=GOOGLE_DRIVE)
        return "no_refresh_token"
    return None


#: A code the callback page may show. Anything else becomes ``failed``.
_CODE = re.compile(r"^[a-z0-9_]{1,64}$")


def _failed(code: str) -> RedirectResponse:
    raw = (code or "").strip().lower()
    safe = raw if _CODE.match(raw) else "failed"
    return RedirectResponse(f"{RETURN_FAILED}{safe}", status_code=302)


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def source_dict(row: Any) -> dict[str, Any]:
    """What a caller may see of a source. No token, no blob, no connection id."""
    return {
        "id": str(row.id),
        "provider": row.provider,
        "kind": row.kind,
        "owner_email": row.owner_email,
        "state": row.state,
        "account_email": row.account_email,
        "node_count": int(row.node_count or 0),
        "discovered_at": _iso(row.discovered_at),
        "last_error": row.last_error,
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


def node_dict(row: Any) -> dict[str, Any]:
    """One node of the tree: metadata only. No text, no content, no body."""
    return {
        "ext_id": row.ext_id,
        "parent_ext_id": row.parent_ext_id,
        "drive_id": row.drive_id,
        "kind": row.kind,
        "name": row.name,
        "mime": row.mime,
        "size": int(row.size) if row.size is not None else None,
        "modified_at": _iso(row.modified_at),
        "web_url": row.web_url,
        "explored": bool(row.explored),
        "has_children": bool(row.has_children),
    }


# ── The shared Google grant (§7.4) ──────────────────────────────────────────


async def drive_grant_verdict(org: str, email: str | None, connection_id: str) -> str:
    """The verdict of both shared-grant functions on *email*, in any tenant.

    * The Gmail half reuses ``teardown.grant_shared_elsewhere`` with the nil
      UUID: migration 300's ``email_gmail_grant_shared`` counts every Gmail
      mailbox AND every live Drive connection of the address.
    * The Drive half asks ``kb_google_drive_grant_shared`` for another live
      Drive connection, with this connection excluded.

    ``teardown.GRANT_FREE`` only when both answer false. A ``true`` is
    ``GRANT_SHARED``, and a NULL or a fault is the first such verdict. It
    never raises. The caller has already disconnected the row, so the row
    never counts itself.
    """

    def _session() -> Any:
        return core._tenant_session(org)

    gmail = await teardown.grant_shared_elsewhere(_session, email, NIL_UUID)
    if gmail != teardown.GRANT_FREE:
        return gmail
    address = str(email or "").strip()
    try:
        async with asyncio.timeout(teardown.REVOKE_TIMEOUT_S):
            async with core._tenant_session(org) as db:
                answer = (await db.execute(text(DRIVE_GRANT_SHARED_SQL), {
                    "email": address, "exclude": str(connection_id),
                })).scalar()
    except Exception as exc:
        _log.warning("knowledge.source_grant_check_failed", error=type(exc).__name__)
        return teardown.GRANT_CHECK_FAILED
    if answer is True:
        return teardown.GRANT_SHARED
    if answer is False:
        return teardown.GRANT_FREE
    return teardown.GRANT_OWNER_BLIND


async def _revoke_if_free(
    org: str, source: KbSource, cred: SourceCredential | None, email: str | None,
    connection_id: str,
) -> str:
    """Revoke the Google grant only when :func:`drive_grant_verdict` is free.

    Best effort. The outcome is logged by a fixed word, never with a token.
    """
    if cred is None:
        verdict = "no_token"
    else:
        verdict = await drive_grant_verdict(org, email, connection_id)
    if verdict != teardown.GRANT_FREE or cred is None:
        _log.info("knowledge.source_revoke_skipped", provider=GOOGLE_DRIVE, reason=verdict)
        return verdict
    try:
        async with asyncio.timeout(teardown.REVOKE_TIMEOUT_S):
            confirmed = await source.revoke(cred)
        outcome = "revoked" if confirmed else "not_confirmed"
    except (SourceError, TimeoutError) as exc:
        outcome = f"failed:{type(exc).__name__}"
    _log.info("knowledge.source_revoke", provider=GOOGLE_DRIVE, outcome=outcome)
    return outcome


# ── Routes ──────────────────────────────────────────────────────────────────

_GATED = [Depends(flags.require_knowledge_sources)]
_GOOGLE = [Depends(flags.require_knowledge_sources),
           Depends(flags.require_knowledge_source_google)]


@core.router.post("/sources/google_drive/connect", response_model=ConnectModel,
                  dependencies=_GOOGLE)
async def connect_google_drive(user: UserContext = Depends(get_current_user)) -> ConnectModel:
    """The consent URL, with a signed state. The member follows it to Google."""
    org = _organization(user)
    member = _member(user)
    client = google_client()
    try:
        state = sign_state(purpose=STATE_PURPOSE, org=org, member=member)
    except SigningUnavailable as exc:
        raise HTTPException(status_code=503, detail="signing_unavailable") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid_member") from exc
    return ConnectModel(url=make_source(client).consent_url(state=state, scope_kind=SCOPE_MEMBER))


@core.router.get(CALLBACK_ROUTE, dependencies=_GOOGLE)
async def google_drive_callback(
    background: BackgroundTasks,
    code: str | None = Query(default=None, max_length=2048),
    state: str | None = Query(default=None, max_length=4096),
    error: str | None = Query(default=None, max_length=200),
    user: UserContext = Depends(get_current_user),
) -> RedirectResponse:
    """Finish the consent, save the token, and start discovery in the background."""
    org = _organization(user)
    member = _member(user)
    # 1. The state, before any other work. Every refusal is the same 400.
    payload = verify_state(state, purpose=STATE_PURPOSE)
    if payload is None or payload["org"] != org or payload["member"] != member:
        raise HTTPException(status_code=400, detail="invalid_state")
    # 2. The member said no at Google.
    if error:
        return _failed(error)
    if not code:
        return _failed("no_code")
    source = make_source(google_client())
    # 3. The code for tokens, and the grant check. No session is open.
    try:
        cred = await source.exchange_code(code=code)
    except SourceError as exc:
        _log.warning("knowledge.source_exchange_failed", code=google_auth.safe_code(exc.code))
        return _failed("exchange_failed")
    refusal = drive_grant_refusal(cred)
    if refusal is not None:
        # Never revoked: the grant may be the Gmail grant of this account.
        return _failed(refusal)
    try:
        who = await source.account_identity(cred)
    except SourceError:
        return _failed("account_unreadable")
    cred = replace(cred, provider_meta={
        **dict(cred.provider_meta),
        google_auth.META_ACCOUNT_EMAIL: who.email,
        google_auth.META_ACCOUNT_ID: who.account_id,
    })
    # 4. The token and the source, in one transaction. A consent to another
    #    Google account drops the old tree, because it is that account's.
    async with core._tenant_session() as db:
        await db.execute(text(_CONNECT_LOCK_SQL),
                         {"key": f"kb.source.connect:{org}:{member}:{GOOGLE_DRIVE}"})
        conn = await store.save_credential(
            db, org, GOOGLE_DRIVE, OWNING_APP, cred, connected_by=member,
            scope_kind=store.SCOPE_MEMBER, member_email=member, scopes=cred.scopes,
        )
        before = (await db.execute(text(_ACCOUNT_SQL),
                                   {"org": org, "connection": conn.id})).first()
        if before is not None and before.account_id and before.account_id != who.account_id:
            await db.execute(text(_DELETE_NODES_SQL), {"org": org, "source": str(before.id)})
        row = (await db.execute(text(_UPSERT_SOURCE_SQL), {
            "org": org, "connection": conn.id, "provider": GOOGLE_DRIVE, "me": member,
            "account_email": who.email, "account_id": who.account_id,
        })).first()
        source_id = str(row.id)
    # 5. Discovery runs after the redirect, with its own sessions.
    background.add_task(discovery.discover_source, org, source_id, source)
    _log.info("knowledge.source_connected", provider=GOOGLE_DRIVE, organization_id=org)
    return RedirectResponse(RETURN_OK, status_code=302)


@core.router.get("/sources", dependencies=_GATED)
async def list_sources(user: UserContext = Depends(get_current_user)) -> dict[str, Any]:
    """The sources the caller may see: their own, or every one for a manager."""
    params = _scope(user)
    async with core._tenant_session() as db:
        rows = (await db.execute(text(_LIST_SQL), params)).fetchall()
    return {"sources": [source_dict(r) for r in rows]}


@core.router.get("/sources/{source_id}/tree", dependencies=_GATED)
async def source_tree(
    source_id: str,
    parent: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=TREE_DEFAULT, ge=1, le=TREE_MAX),
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """One level of the tree: the roots, or the children of *parent*.

    Metadata only. A source the caller may not see answers 404.
    """
    sid = _source_id(source_id)
    if parent is not None and not _EXT_ID.match(parent):
        raise HTTPException(status_code=422, detail="parent is a provider id")
    params = _scope(user)
    async with core._tenant_session() as db:
        src = (await db.execute(text(_ONE_SQL), {**params, "id": sid})).first()
        if src is None:
            raise HTTPException(status_code=404, detail="Source not found.")
        tree = {"org": params["org"], "source": sid, "limit": limit + 1}
        if src.state == discovery.STATE_DISCONNECTED:
            rows = []
        elif parent is None:
            rows = (await db.execute(text(_ROOTS_SQL), tree)).fetchall()
        else:
            rows = (await db.execute(text(_CHILDREN_SQL), {**tree, "parent": parent})).fetchall()
    return {
        "source": source_dict(src),
        "parent": parent,
        "nodes": [node_dict(r) for r in rows[:limit]],
        "truncated": len(rows) > limit,
    }


@core.router.delete("/sources/{source_id}", dependencies=_GATED)
async def disconnect_source(
    source_id: str, user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Disconnect one source. See the module doc for the order of the steps."""
    sid = _source_id(source_id)
    params = _scope(user)
    org = params["org"]
    cred: SourceCredential | None = None
    async with core._tenant_session() as db:
        src = (await db.execute(text(_LOCK_ONE_SQL), {**params, "id": sid})).first()
        if src is None:
            raise HTTPException(status_code=404, detail="Source not found.")
        if src.state == discovery.STATE_DISCONNECTED:
            return source_dict(src)
        conn = await store.get_by_id(db, org, str(src.connection_id))
        email = src.account_email
        if conn is not None:
            email = email or conn.provider_meta.get(google_auth.META_ACCOUNT_EMAIL)
            cred = await store.load_credential(
                db, org, conn.provider, conn.owning_app,
                scope_kind=conn.scope_kind, member_email=conn.member_email,
            )
            await store.disconnect(db, org, conn.id)
        await db.execute(text(_DELETE_NODES_SQL), {"org": org, "source": sid})
        await db.execute(text(_DISCONNECTED_SQL), {"org": org, "id": sid})
        after = (await db.execute(text(_ONE_SQL), {**params, "id": sid})).first()
    # After the commit, and with no session open: the revoke, only when free.
    if src.provider == GOOGLE_DRIVE:
        try:
            client = google_client()
        except HTTPException:
            _log.info("knowledge.source_revoke_skipped", provider=GOOGLE_DRIVE,
                      reason="not_configured")
        else:
            await _revoke_if_free(org, make_source(client), cred, email,
                                  str(src.connection_id))
    return source_dict(after)
