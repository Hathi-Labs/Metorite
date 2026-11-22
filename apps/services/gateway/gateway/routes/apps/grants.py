"""Custom Apps · grants — share with specific people/agents + first-open
consent (RFC: docs/app-workshop/README.md §4.8).

Grants are the "specific people" rung of the private → people → org
visibility ladder: one ``app_grants`` row per ``(app, subject)`` naming a
role (``use``/``edit``/``own``). ``subject`` is either an email, an
``agent:<name>``, or the literal ``agents:*`` — never ``"org"``, which is
already what ``apps.visibility='org'`` means (a grant row re-encoding that
would be a second, driftable source of truth for the same fact).

Consent lives on the same row (``consented_scope_hash``) but is a distinct
concern from sharing: it's the first-open disclosure step a *viewer* clears
for themselves, keyed to the app's LIVE (published) ``scope_set_hash`` —
never the draft's — so nobody consents to scopes that were never actually
published and (for org+write-scope apps) admin-reviewed at publish time
(``publish.py``'s review gate). ``lifecycle.get_app`` computes whether the
current caller still needs to see the interstitial; this module records the
"I saw it" ack.
"""

from __future__ import annotations

import re
from typing import Any

from acb_auth import UserContext
from fastapi import Depends, HTTPException
from gateway.routes.apps._common import (
    _log,
    _tenant_session,
    _uid,
    get_app_or_404,
    iso,
    load_live_version_scopes,
    record_app_audit_bound,
    require_app_user,
    router,
)
from pydantic import BaseModel
from sqlalchemy import text

ROLES = ("use", "edit", "own")
_AGENT_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")
_MAX_SUBJECT_LEN = 254


# ── Models ───────────────────────────────────────────────────────────────────

class GrantCreate(BaseModel):
    subject: str
    role: str = "use"


class GrantEntry(BaseModel):
    subject: str
    role: str
    consented_scope_hash: str = ""
    granted_by: str
    created_at: str = ""


class ConsentRequest(BaseModel):
    scope_set_hash: str


# ── Subject validation (pure) ────────────────────────────────────────────────

def is_valid_subject(subject: str) -> bool:
    """email | ``agent:<name>`` | ``agents:*`` — never the literal ``org``
    (that's what ``apps.visibility='org'`` already means; a grant row is for
    named people/agents, not a re-encoding of org-wide access).

    Email check is deliberately simple (contains ``@``, no whitespace, a
    reasonable length) — not full RFC 5322 validation.
    """
    s = subject or ""
    if not s or s == "org":
        return False
    if s == "agents:*":
        return True
    if s.startswith("agent:"):
        return bool(_AGENT_NAME_RE.fullmatch(s[len("agent:"):]))
    if len(s) > _MAX_SUBJECT_LEN or any(ch.isspace() for ch in s):
        return False
    return "@" in s and not s.startswith("@") and not s.endswith("@")


# ── Row → model ──────────────────────────────────────────────────────────────

def _to_entry(row: Any) -> GrantEntry:
    return GrantEntry(
        subject=row.subject,
        role=row.role,
        consented_scope_hash=row.consented_scope_hash or "",
        granted_by=row.granted_by,
        created_at=iso(row.created_at) or "",
    )


# ── Sharing endpoints (edit-gated) ───────────────────────────────────────────

@router.get("/{slug}/grants", response_model=list[GrantEntry])
async def list_app_grants(
    slug: str,
    user: UserContext = Depends(require_app_user),
) -> list[GrantEntry]:
    async with _tenant_session() as db:
        row, _grants = await get_app_or_404(db, slug, user, edit=True)
        rows = (await db.execute(
            text(
                """SELECT subject, role, consented_scope_hash, granted_by,
                          created_at
                   FROM app_grants WHERE app_id = :app_id
                   ORDER BY created_at"""
            ),
            {"app_id": str(row.id)},
        )).fetchall()
    return [_to_entry(r) for r in rows]


@router.post("/{slug}/grants", response_model=GrantEntry)
async def upsert_app_grant(
    slug: str,
    body: GrantCreate,
    user: UserContext = Depends(require_app_user),
) -> GrantEntry:
    """Share with one more person/agent, or change an existing grantee's
    role — an admin re-sharing with a different role is expected, so the
    upsert's ``DO UPDATE`` always applies the new role."""
    if body.role not in ROLES:
        raise HTTPException(
            status_code=422, detail="role must be use|edit|own",
        )
    if not is_valid_subject(body.subject):
        raise HTTPException(status_code=422, detail="Invalid subject")
    async with _tenant_session() as db:
        row, _grants = await get_app_or_404(db, slug, user, edit=True)
        rec = (await db.execute(
            text(
                """INSERT INTO app_grants (app_id, subject, role, granted_by)
                   VALUES (:app_id, :subject, :role, :granted_by)
                   ON CONFLICT (app_id, subject)
                   DO UPDATE SET role = excluded.role
                   RETURNING subject, role, consented_scope_hash, granted_by,
                             created_at"""
            ),
            {
                "app_id": str(row.id), "subject": body.subject,
                "role": body.role, "granted_by": _uid(user),
            },
        )).fetchone()
    await record_app_audit_bound(
        app_id=str(row.id), user_email=_uid(user), kind="action",
        detail={"action": "grant_upsert", "subject": body.subject,
                "role": body.role},
    )
    _log.info(
        "apps.grant_upserted", slug=slug, subject=body.subject,
        role=body.role, by=_uid(user),
    )
    return _to_entry(rec)


@router.delete("/{slug}/grants/{subject}")
async def revoke_app_grant(
    slug: str,
    subject: str,
    user: UserContext = Depends(require_app_user),
) -> dict[str, Any]:
    async with _tenant_session() as db:
        row, _grants = await get_app_or_404(db, slug, user, edit=True)
        await db.execute(
            text(
                "DELETE FROM app_grants "
                "WHERE app_id = :app_id AND subject = :subject"
            ),
            {"app_id": str(row.id), "subject": subject},
        )
    await record_app_audit_bound(
        app_id=str(row.id), user_email=_uid(user), kind="action",
        detail={"action": "grant_revoke", "subject": subject},
    )
    _log.info("apps.grant_revoked", slug=slug, subject=subject, by=_uid(user))
    return {"subject": subject, "deleted": True}


# ── Consent (view-gated — any viewer consents for themselves) ──────────────

@router.post("/{slug}/consent")
async def consent_app(
    slug: str,
    body: ConsentRequest,
    user: UserContext = Depends(require_app_user),
) -> dict[str, bool]:
    """First-open disclosure ack. View-gated, not edit-gated: consent is
    something every viewer does for themselves, unlike the sharing endpoints
    above. The client echoes ``live_scope_set_hash`` from ``GET /{slug}``,
    but it's never trusted blindly — the LIVE version's real hash is
    re-fetched server-side and compared; a mismatch means the client has a
    stale view (a new version published between fetch and consent) and must
    re-fetch + re-show the interstitial rather than silently recording
    consent to an outdated scope set.
    """
    async with _tenant_session() as db:
        row, _grants = await get_app_or_404(db, slug, user)
        if row.live_version is None:
            raise HTTPException(
                status_code=404, detail="App has no live version",
            )
        live = await load_live_version_scopes(db, str(row.id), row.live_version)
        if live is None:
            raise HTTPException(status_code=404, detail="Live version not found")
        _scopes, live_hash = live
        if body.scope_set_hash != live_hash:
            raise HTTPException(
                status_code=409, detail={"error": "scope_set_changed"},
            )
        email = _uid(user)
        # Only ever updates the hash on conflict — role stays whatever it
        # already was (a fresh row from THIS insert gets role='use'; an
        # existing edit/own grant is never downgraded by a consent call).
        await db.execute(
            text(
                """INSERT INTO app_grants
                       (app_id, subject, role, consented_scope_hash, granted_by)
                   VALUES (:app_id, :email, 'use', :hash, :email)
                   ON CONFLICT (app_id, subject)
                   DO UPDATE SET consented_scope_hash = excluded.consented_scope_hash"""
            ),
            {"app_id": str(row.id), "email": email, "hash": live_hash},
        )
    await record_app_audit_bound(
        app_id=str(row.id), user_email=_uid(user), kind="action",
        detail={"action": "consent", "scope_set_hash": live_hash},
    )
    return {"consented": True}
