"""Org administration — member roster, lifecycle, roles, and per-user access.

Spec: ``project-docs/specs/org_access_control.md`` §6.

The interesting endpoint here is ``GET /admin/members/{email}/access``: it
returns not just *what* the member can reach but *why* — which role granted it,
or which override took it away. An admin who has to simulate the resolution
algorithm in their head to answer "why can Priya still see WhatsApp?" will stop
trusting the model, so provenance is part of the API, not a debugging aid.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from acb_auth import (
    CAPABILITIES,
    FEATURES,
    EffectiveAccess,
    InvalidPermission,
    UserContext,
    agent_run_permission,
    build_access,
    feature_permission,
    integration_use_permission,
    require_permission,
    validate_permission,
)
from acb_auth.access import mirror_membership_status
from acb_auth.permissions import matched_by
from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text

from gateway.routes.admin._common import (
    PURGE_OUTCOME,
    _iso,
    _log,
    _tenant_session,
    assert_not_self_demotion,
    assert_not_self_lockout,
    assert_owner_survives,
    get_member,
    get_org_id,
    invalidate_for,
    provision_member,
    record_admin_change,
    require_admin_user,
    resolve_assignable_roles,
    roles_for_user,
    router,
    set_roles,
)

VALID_STATUSES = ("invited", "active", "suspended", "removed")


# ── Models ──────────────────────────────────────────────────────────────────

class MemberEntry(BaseModel):
    email: str
    display_name: str = ""
    avatar_url: str = ""
    status: str = "active"
    roles: list[str] = Field(default_factory=list)
    invited_by: str = ""
    joined_at: str = ""
    last_login_at: str = ""


class InviteRequest(BaseModel):
    email: str
    display_name: str = ""
    #: Role slugs to assign. Empty means the seeded default, `member`.
    roles: list[str] = Field(default_factory=list)


class MemberPatch(BaseModel):
    status: str | None = None
    display_name: str | None = None


class RoleAssignment(BaseModel):
    roles: list[str]


class OverrideEntry(BaseModel):
    permission: str
    effect: str          # allow | deny
    reason: str = ""


class OverrideRequest(BaseModel):
    """Full replacement of a member's overrides — PUT semantics.

    Replace rather than merge so the admin UI can send exactly what the
    checkboxes show. A merge API makes "unset this deny" a second verb nobody
    remembers to call.
    """

    overrides: list[OverrideEntry]


# ── Roster ──────────────────────────────────────────────────────────────────

@router.get("/members", summary="List organization members")
async def list_members(
    include_removed: bool = False,
    admin: UserContext = Depends(require_admin_user),
) -> list[MemberEntry]:
    async with _tenant_session() as db:
        org_id = await get_org_id(db, admin)
        sql = (
            "SELECT u.id::text AS id, u.email, u.display_name, u.avatar_url, "
            "       u.status, u.invited_by, u.joined_at, u.last_login_at, "
            "       COALESCE("
            "         (SELECT array_agg(r.slug ORDER BY r.rank)"
            "            FROM user_role ur JOIN org_role r ON r.id = ur.role_id"
            "           WHERE ur.user_id = u.id), ARRAY[]::text[]) AS roles "
            "  FROM app_user u "
            " WHERE u.organization_id = CAST(:org AS uuid) "
        )
        if not include_removed:
            sql += " AND u.status <> 'removed' "
        sql += " ORDER BY u.email"
        rows = (await db.execute(text(sql), {"org": org_id})).mappings().all()

    return [
        MemberEntry(
            email=r["email"],
            display_name=r["display_name"] or "",
            avatar_url=r["avatar_url"] or "",
            status=r["status"],
            roles=list(r["roles"] or []),
            invited_by=r["invited_by"] or "",
            joined_at=_iso(r["joined_at"]),
            last_login_at=_iso(r["last_login_at"]),
        )
        for r in rows
    ]


@router.post("/members", summary="Invite a member",
             dependencies=[require_permission("admin:members:invite")])
async def invite_member(
    req: InviteRequest,
    admin: UserContext = Depends(require_admin_user),
) -> MemberEntry:
    """Create (or re-activate) a member row in the `invited` state.

    No email is sent — sign-in is Entra ID SSO, so "inviting" means
    provisioning the row that turns a directory identity into a member with
    access. Until that row exists, an authenticated stranger resolves to no
    access (``resolve_access`` returns inactive for an unknown email).

    ⚠️ **This alone does not let anybody in.** `invited` is not `active`, and
    `is_active` is `status == "active"` exactly — the invited colleague sees
    the same dead-end screen as a stranger until somebody clicks Activate
    (`PATCH /admin/members/{email}`). That is §2 Step 1b of
    ``colleague_onboarding.md``, and the fact that the runbook did not say so
    is the whole of the 2026-08-04 incident. Approving a *sign-in request*
    does both at once, on purpose.
    """
    email = (req.email or "").strip().lower()

    async with _tenant_session() as db:
        org_id = await get_org_id(db, admin)
        member, _assigned = await provision_member(
            db, org_id,
            email=email,
            display_name=req.display_name or "",
            roles=req.roles,
            admin=admin,
            status="invited",
        )
        roles = await roles_for_user(db, member["id"])

    invalidate_for(email)
    _log.info("member_invited", email=email, by=admin.email, roles=roles)
    record_admin_change(admin.email, "org.member_invited", f"user:{email}",
                        roles=roles)
    return MemberEntry(
        email=email,
        display_name=req.display_name or "",
        status="invited",
        roles=roles,
        invited_by=admin.email or "",
    )


@router.patch("/members/{email}", summary="Update a member's status or name",
              dependencies=[require_permission("admin:members:manage")])
async def update_member(
    email: str,
    patch: MemberPatch,
    admin: UserContext = Depends(require_admin_user),
) -> MemberEntry:
    if patch.status is not None and patch.status not in VALID_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"status must be one of {list(VALID_STATUSES)}.",
        )

    async with _tenant_session() as db:
        org_id = await get_org_id(db, admin)
        member = await get_member(db, org_id, email)

        # Invariant 4 — nobody locks themselves out. The same helper guards the
        # DELETE below: this route reaches the identical `is_active = False`,
        # and a check written into only one of the two doors is how they came
        # to disagree. Passing `patch.status` unconditionally is what makes a
        # display-name-only patch safe by construction rather than by luck.
        assert_not_self_lockout(admin, member, status=patch.status)

        # Suspending or removing the last owner locks everyone out of admin.
        # Independent of the guard above: this one is about the ORG keeping an
        # owner, and it happens to refuse self-suspension only while there is
        # exactly one.
        if patch.status in ("suspended", "removed"):
            await assert_owner_survives(db, org_id, excluding_user_id=member["id"])

        if patch.status is not None:
            await db.execute(
                text(
                    "UPDATE app_user SET status = :status, updated_at = now(), "
                    "  joined_at = CASE WHEN :status = 'active' "
                    "                   THEN COALESCE(joined_at, now()) ELSE joined_at END "
                    " WHERE id = CAST(:uid AS uuid)"
                ),
                {"status": patch.status, "uid": member["id"]},
            )
        if patch.display_name is not None:
            await db.execute(
                text(
                    "UPDATE app_user SET display_name = :name, updated_at = now() "
                    " WHERE id = CAST(:uid AS uuid)"
                ),
                {"name": patch.display_name, "uid": member["id"]},
            )
        member = await get_member(db, org_id, email)
        roles = await roles_for_user(db, member["id"])

    # H6 slice 3a (WS-29, DARK, D48): keep the RLS-EXEMPT identity shadow's
    # status CURRENT. A suspend / reactivate / activate here mutates
    # `app_user.status`; the create-only slice-1 mirror never touches the
    # existing `org_membership` row, so status would drift stale and the H6 read
    # cutover could admit a suspended member from it. Best-effort, own session,
    # after the authoritative commit — it moves no read and the `app_user` write
    # stays byte-identical. Only on a status change; a display-name-only patch
    # touches nothing. See `acb_auth.access.mirror_membership_status`.
    if patch.status is not None:
        await mirror_membership_status(
            email=member["email"], org_id=org_id, status=member["status"],
        )

    invalidate_for(member["email"])
    _log.info("member_updated", email=member["email"], by=admin.email,
              status=member["status"])
    record_admin_change(admin.email, "org.member_updated",
                        f"user:{member['email']}", status=member["status"])
    return MemberEntry(
        email=member["email"],
        display_name=member["display_name"] or "",
        avatar_url=member["avatar_url"] or "",
        status=member["status"],
        roles=roles,
        invited_by=member["invited_by"] or "",
        joined_at=_iso(member["joined_at"]),
        last_login_at=_iso(member["last_login_at"]),
    )


@router.delete("/members/{email}", summary="Remove a member",
               dependencies=[require_permission("admin:members:manage")])
async def remove_member(
    email: str,
    admin: UserContext = Depends(require_admin_user),
) -> dict[str, str]:
    """Soft-remove: status → `removed`, role assignments dropped.

    The row is kept because ~every user-scoped table in the schema references
    people by email (`apps.owner_email`, `app_audit.user_email`, chat sessions,
    GTD items). Hard-deleting the identity would orphan all of it; what
    actually matters for access is that the member resolves to nothing, which
    the `removed` status guarantees.
    """
    async with _tenant_session() as db:
        org_id = await get_org_id(db, admin)
        member = await get_member(db, org_id, email)
        # Invariant 4, from the same helper the PATCH above calls — this route
        # used to hold its own copy of the comparison, which is precisely why
        # the other door never grew one.
        assert_not_self_lockout(admin, member, status="removed")
        await assert_owner_survives(db, org_id, excluding_user_id=member["id"])

        await db.execute(
            text("DELETE FROM user_role WHERE user_id = CAST(:uid AS uuid)"),
            {"uid": member["id"]},
        )
        await db.execute(
            text(
                "UPDATE app_user SET status = 'removed', updated_at = now() "
                " WHERE id = CAST(:uid AS uuid)"
            ),
            {"uid": member["id"]},
        )

    # H6 slice 3a (WS-29, DARK, D48): propagate the off-boarding into the
    # RLS-EXEMPT shadow so `org_membership.status` is 'removed' too, or the H6
    # read cutover admits an off-boarded member from a stale row. Best-effort,
    # own session, after the authoritative commit — moves no read, the
    # `app_user` write stays byte-identical. See
    # `acb_auth.access.mirror_membership_status`.
    await mirror_membership_status(
        email=member["email"], org_id=org_id, status="removed",
    )

    invalidate_for(member["email"])
    _log.info("member_removed", email=member["email"], by=admin.email)
    record_admin_change(admin.email, "org.member_removed",
                        f"user:{member['email']}")
    return {"status": "removed", "email": member["email"]}


# ── Permanent deletion ──────────────────────────────────────────────────────
#
# The decision this section implements, so it is not re-litigated by whoever
# reads it next: **purge the person, keep their work.** Delete the identity and
# everything that exists only to grant them access or to let the platform act
# as them; leave what they authored readable. Purging their content too was
# considered and rejected — it is unrecoverable, and in this schema it silently
# takes shared artefacts (a room with other participants, an app other people
# use) along with the private ones.
#
# ⚠️ **"Delete the identity" cannot mean "erase the address everywhere."** The
# email address IS the join key across ~50 tables — `apps.owner_email`,
# `gtd_items.user_id`, `workflows.owner_email`, `app_audit.user_email` — so
# scrubbing it is not a redaction, it is a deletion of the rows it keys. That
# is why nothing here is *anonymised*: an anonymised `owner_email` would not
# hide a person, it would orphan their apps. What a purge deletes is the
# `app_user` row (the org's member record) and every credential, grant, and
# private session keyed to it.

@dataclass(frozen=True)
class _PersonRows:
    """One table's worth of rows that belong to one person.

    ``count_sql`` and ``delete_sql`` are derived from the **same** ``where``
    clause, which is the only reason the counts in the response can be trusted:
    the number reported and the rows destroyed are the same predicate by
    construction, not by two statements that were written to agree.
    """

    #: How the count appears in the API response, and in the UI copy.
    key: str
    table: str
    #: Bound against ``:uid`` (``app_user.id``), ``:email`` (lower-cased
    #: address) and/or ``:actor`` (``user:<address>``, the audit-log form).
    where: str

    @property
    def count_sql(self) -> str:
        return f"SELECT count(*) FROM {self.table} WHERE {self.where}"

    @property
    def delete_sql(self) -> str:
        return f"DELETE FROM {self.table} WHERE {self.where}"


#: What each credential row takes with it, by FKs the schema already declares
#: `ON DELETE CASCADE`. The full closure, direct and transitive flattened
#: together — the distinction between "goes directly" and "goes through
#: `email_messages`" is not one an admin can act on; "these tables lose rows"
#: is.
#:
#: ⚠️ **HAND-MAINTAINED, and therefore stale the moment a migration adds a
#: child.** It is not derived at import time (parsing `infra/postgres/` in a
#: request path would be absurd), so the fence is a test:
#: `test_admin_member_purge.py::test_the_cascade_map_is_the_one_the_schema_declares`
#: re-derives it from the numbered migrations and fails on any drift. When it
#: fails, the map *and* the confirmation copy in the Members page owe an
#: update — on a route whose entire safety argument is "the admin is told the
#: blast radius before clicking", understating it is the wrong direction of
#: error, which is exactly how the first version of this comment named 15 of
#: 20 email tables and put `wa_media` one hop too high.
_CREDENTIAL_CASCADES: dict[str, tuple[str, ...]] = {
    # The whole mirrored mailbox — 18 direct children, 21 with transitives
    # (`crm_auto_lead_cursors` joined in migration 163: the auto-lead epoch
    # cursor is per-mailbox state and goes with the mailbox).
    "email_accounts": (
        "crm_auto_lead_cursors",
        "email_actions", "email_ai_drafts", "email_assistant_settings",
        "email_attachments", "email_cold_senders", "email_contacts",
        "email_embeddings", "email_executed_rules", "email_folders",
        "email_knowledge", "email_learned_patterns", "email_messages",
        "email_newsletters", "email_rule_guidance", "email_rule_patterns",
        "email_rules", "email_senders", "email_sync_log",
        "email_thread_status", "email_voice_profiles",
    ),
    # The whole WhatsApp mirror — 14 direct, 16 with transitives
    # (`wa_media` and `wa_message_embeddings` hang off `wa_messages`).
    "wa_accounts": (
        "wa_ai_drafts", "wa_categories", "wa_chat_avatars", "wa_chat_labels",
        "wa_chat_status", "wa_chats", "wa_commitments", "wa_contacts",
        "wa_group_summaries", "wa_labels", "wa_media", "wa_message_embeddings",
        "wa_messages", "wa_saved_replies", "wa_sync_log", "wa_templates",
    ),
    # The SYNCED half of the GTD store. ⚠️ The ONLY cascade children in this
    # whole map that carry a `user_id` of their own — everything above is
    # keyed by `account_id` alone and is mirror-of-a-credential, nothing else.
    # That is why these two are the only ones split across both lists below
    # and counted individually rather than summarised: the same table also
    # holds LOCAL rows the purge must not take.
    "task_accounts": ("gtd_items", "gtd_projects", "gtd_waiting"),
}

#: **Deleted.** Enumerated from `infra/postgres/` by grepping for every column
#: that names a person — FKs to `app_user(id)` and the email-string columns
#: (`owner_email`, `user_email`, `user_id`, `subject`, `*_by`) — not from
#: memory. Ordered children-before-parent so the statements read as the
#: dependency graph even though every count is taken first.
#:
#: The three credential rows carry `credentials_encrypted NOT NULL` — the live
#: OAuth/API tokens. The credential cannot be deleted without the row, and
#: leaving a departed colleague's tokens in the database is the hole a purge
#: exists to close; `_CREDENTIAL_CASCADES` above is what goes with them. The
#: mirrors are mirrors: the source systems stay authoritative (root AGENTS.md,
#: global constraint 8). The response reports every count so none of it is
#: silent, and the UI names it before the admin confirms.
_PURGE_DELETES: tuple[_PersonRows, ...] = (
    # Access grants keyed to app_user.id. All three declare ON DELETE CASCADE,
    # so deleting the member row would take them anyway — they are deleted
    # explicitly so the counts are real and so the purge does not depend on a
    # cascade a later migration could drop.
    _PersonRows("role_grants", "user_role", "user_id = CAST(:uid AS uuid)"),
    _PersonRows("permission_overrides", "user_permission_override",
                "user_id = CAST(:uid AS uuid)"),
    _PersonRows("group_memberships", "org_group_member",
                "user_id = CAST(:uid AS uuid)"),
    # Access grants keyed by address. `chat_session_participant.subject` and
    # `app_grants.subject` share one vocabulary (email | group:<slug> | org),
    # so an address matches exactly the person's own rows.
    _PersonRows("room_participations", "chat_session_participant",
                "lower(subject) = :email"),
    _PersonRows("app_grants", "app_grants", "lower(subject) = :email"),
    # A remembered "always allow this app to use this tool" confirm is a
    # standing authorization to act as them, so it goes with the credentials.
    _PersonRows("app_tool_grants", "app_tool_grants",
                "lower(user_email) = :email"),
    # Credentials.
    _PersonRows("email_accounts", "email_accounts", "lower(user_id) = :email"),
    _PersonRows("whatsapp_accounts", "wa_accounts", "lower(user_id) = :email"),
    # ⚠️ The SYNCED half of the GTD store, deleted EXPLICITLY and BEFORE the
    # account it hangs off, for one reason: it has to be **counted on this
    # side**. `task_accounts` cascades both tables, so leaving them implicit
    # meant the response reported the destroyed rows as *kept* — 847 synced
    # tasks answered `kept: {"tasks": 847}` while all 847 went with the
    # credential, and `gtd_projects` appeared on neither list at all. A count
    # that is wrong in the reassuring direction is worse than no count.
    #
    # `account_id IS NOT NULL` is the SYNCED half by construction: every
    # writer stamps `account_id` from an account the same person owns
    # (`tasks/sync.py` binds `uid` to `account.user_id`; the account routes
    # are owner-scoped), so "their rows with an account" and "the rows this
    # person's accounts cascade" are the same set. The complement below keeps
    # the LOCAL rows and is asserted to be exactly that — a complement.
    _PersonRows("synced_tasks", "gtd_items",
                "lower(user_id) = :email AND account_id IS NOT NULL"),
    _PersonRows("synced_projects", "gtd_projects",
                "lower(user_id) = :email AND account_id IS NOT NULL"),
    _PersonRows("task_accounts", "task_accounts", "lower(user_id) = :email"),
    # Their own private conversations. ⚠️ `visibility = 'private'` is
    # load-bearing and is the line the rejected "purge their content too"
    # option would have crossed: a `people`/`org` room they created has OTHER
    # participants, and deleting it cascades `chat_message` — one person's
    # off-boarding would silently take a shared transcript with it. Shared
    # rooms are kept and counted as kept; only their participant row goes.
    _PersonRows("private_chat_sessions", "chat_session",
                "lower(user_id) = :email AND visibility = 'private'"),
    # The knock. A decided request is normally kept as the record of the
    # decision, but a purged person must be a stranger again: leaving an
    # `approved` row for somebody with no `app_user` row means their next
    # sign-in bumps a row the Requests tab never renders (it shows `pending`
    # only) — the invisible lockout §6 exists to end.
    _PersonRows("sign_in_requests", "access_request", "lower(email) = :email"),
    # The identity itself. LAST.
    _PersonRows("member_record", "app_user", "id = CAST(:uid AS uuid)"),
)

#: **Kept**, and counted so the admin sees what survived rather than inferring
#: it. The first three are the audit trail: **an audit trail that disappears
#: when you delete the person is not an audit trail**, and `app_audit` says so
#: in its own schema — it carries `app_id UUID` with *no* FK, commented "audit
#: survives hard delete". The rest is authored work, kept per the decision
#: above. Nothing in this table is anonymised; see the section header for why.
#:
#: ⚠️ **A KEEP clause must exclude every row the delete side cascades away.**
#: "Kept" is a claim about rows that are still there when the transaction
#: commits, not about rows this list did not name — and a table can be
#: emptied by a cascade three entries up. `shared_rooms` carries
#: `visibility <> 'private'` for that reason and the two GTD entries carry
#: `account_id IS NULL` for the same one. It is a structural fence, not a
#: convention: `test_no_keep_clause_survives_a_cascade_on_the_delete_side`
#: re-derives the cascade graph from the migrations and fails on any KEEP
#: clause inside the blast radius that is not the exact complement of a
#: matching DELETE clause.
_PURGE_KEEPS: tuple[_PersonRows, ...] = (
    _PersonRows("audit_entries", "app_audit", "lower(user_email) = :email"),
    _PersonRows("audit_events", "audit_event", "lower(actor) = :actor"),
    _PersonRows("agent_runs", "agent_run", "lower(user_id) = :email"),
    _PersonRows("shared_rooms", "chat_session",
                "lower(user_id) = :email AND visibility <> 'private'"),
    _PersonRows("apps", "apps", "lower(owner_email) = :email"),
    _PersonRows("workflows", "workflows", "lower(owner_email) = :email"),
    # LOCAL only — the SYNCED rows go with `task_accounts` and are counted on
    # the delete side. Without `account_id IS NULL` this number is a lie in
    # the reassuring direction.
    _PersonRows("tasks", "gtd_items",
                "lower(user_id) = :email AND account_id IS NULL"),
    _PersonRows("projects", "gtd_projects",
                "lower(user_id) = :email AND account_id IS NULL"),
    _PersonRows("meetings", "meeting", "lower(owner_email) = :email"),
)


def _purge_params(rows: _PersonRows, *, uid: str, email: str) -> dict[str, Any]:
    """Bind only the placeholders this clause actually names."""
    params: dict[str, Any] = {}
    if ":uid" in rows.where:
        params["uid"] = uid
    if ":email" in rows.where:
        params["email"] = email
    if ":actor" in rows.where:
        # How `record_admin_change` writes an actor, so the count matches the
        # rows this package itself appends.
        params["actor"] = f"user:{email}"
    return params


class PurgeResult(BaseModel):
    """What a purge actually did, per table.

    A destructive action that answers ``{"status": "ok"}`` is unauditable: the
    admin has no way to tell a purge that removed three sessions and a live
    OAuth token from one that matched nothing. Both halves are reported —
    `deleted` because it is irreversible, `kept` because "your audit trail and
    their apps are still there" is the reassurance that makes the irreversible
    half safe to click.
    """

    status: str = PURGE_OUTCOME
    email: str
    deleted: dict[str, int]
    kept: dict[str, int]


@router.delete("/members/{email}/purge",
               summary="Permanently delete a member",
               dependencies=[require_permission("admin:members:manage")])
async def purge_member(
    email: str,
    admin: UserContext = Depends(require_admin_user),
) -> PurgeResult:
    """Hard delete: the identity, every credential and grant, in one transaction.

    A **second, harder action beside** ``DELETE /admin/members/{email}``, not a
    flag on it. Remove stays exactly what it was and its reasoning stands: the
    `app_user` row is kept because the rest of the schema refers to people by
    address, and what matters for access is that the member resolves to
    nothing. This route is for the other question — "delete them" — and it
    answers it by destroying the identity rather than by parking it.

    **The guards are the same two the other doors use.** Invariant 4 through
    :func:`assert_not_self_lockout` (this is its most destructive door, and a
    fourth private copy of the comparison is exactly how the first two came to
    disagree), then invariant 1 through :func:`assert_owner_survives` — purging
    the last owner is not a recoverable mistake, it is a permanently ownerless
    org.

    **One transaction.** Every statement runs on one session, committed once —
    by ``_tenant_session``, on clean exit of the block, and only then. A
    half-purge that deleted the credentials but left
    the member active is worse than either outcome, and a half-purge that
    deleted the member row but left an OAuth token behind is worse still.

    **The audit entry is written before the commit.** ``acb_audit.record`` opens
    its own session, so it is not inside this transaction and survives a
    rollback. The trade-off is deliberate and this way round: an audit line for
    a purge that then failed is a false positive an admin can reconcile against
    a roster that still shows the person; a completed purge with no audit line
    is unreconcilable, because every row that could have told you who it was
    is gone.

    ⚠️ **"The audit entry survives a rollback" is true. "A completed purge
    always leaves an audit row" is NOT** — and only the first of those is a
    property of this function. ``acb_audit.log.record`` wraps its write in
    ``except Exception`` and merely logs ``audit.persist_failed``, so an audit
    DB that is down turns the call into a no-op and the purge still commits.
    That is the right default for every other caller (a webhook must not 5xx
    over an audit write) and it is deliberately not overridden here, because
    refusing the purge would leave the credentials in place — but it means the
    only *guaranteed* record of a purge is the ``member_purged`` log line.
    Recorded rather than fixed: making one caller strict is a change to
    ``acb_audit``'s contract, not to this route.
    """
    async with _tenant_session() as db:
        org_id = await get_org_id(db, admin)
        member = await get_member(db, org_id, email)

        # Invariant 4, from the shared helper — same rule, fourth door. The
        # outcome name is not an `app_user.status`; the helper's rule is
        # "anything that is not `active`", which is what lets a door that
        # DELETES the row be covered by the sentence written for ones that
        # UPDATE it.
        assert_not_self_lockout(admin, member, status=PURGE_OUTCOME)
        # Invariant 1. Unlike a removal this cannot be undone by re-inviting:
        # the org would have no owner and no row to promote back.
        await assert_owner_survives(db, org_id, excluding_user_id=member["id"])

        addr = (member["email"] or "").strip().lower()
        uid = member["id"]

        # Count everything BEFORE anything is deleted — including the kept
        # side, which is the half that makes the report legible.
        deleted: dict[str, int] = {}
        for rows in _PURGE_DELETES:
            result = await db.execute(
                text(rows.count_sql), _purge_params(rows, uid=uid, email=addr)
            )
            deleted[rows.key] = int(result.scalar() or 0)
        kept: dict[str, int] = {}
        for rows in _PURGE_KEEPS:
            result = await db.execute(
                text(rows.count_sql), _purge_params(rows, uid=uid, email=addr)
            )
            kept[rows.key] = int(result.scalar() or 0)

        for rows in _PURGE_DELETES:
            await db.execute(
                text(rows.delete_sql), _purge_params(rows, uid=uid, email=addr)
            )

        # Before the commit, on its own connection — see the docstring.
        record_admin_change(admin.email, "org.member_purged", f"user:{addr}",
                            deleted=deleted, kept=kept)

    invalidate_for(member["email"])
    _log.info("member_purged", email=member["email"], by=admin.email,
              deleted=deleted, kept=kept)
    return PurgeResult(email=member["email"], deleted=deleted, kept=kept)


# ── Role assignment ─────────────────────────────────────────────────────────
#
# The two helpers this section used to define — `_resolve_assignable_roles` and
# `_set_roles` — now live in `_common.py` as `resolve_assignable_roles` /
# `set_roles`. They enforce invariant 2 ("nobody grants above themselves") and
# are shared by BOTH provisioning callers via `provision_member`, so the leaf is
# their home; a second copy is a second place for the invariant to be missed.

@router.put("/members/{email}/roles", summary="Set a member's roles",
            dependencies=[require_permission("admin:members:manage")])
async def set_member_roles(
    email: str,
    req: RoleAssignment,
    admin: UserContext = Depends(require_admin_user),
) -> MemberEntry:
    async with _tenant_session() as db:
        org_id = await get_org_id(db, admin)
        member = await get_member(db, org_id, email)
        role_ids = await resolve_assignable_roles(db, org_id, req.roles, admin)

        # Invariant 4, third door: this route never touches `status`, so
        # `assert_not_self_lockout` cannot see it — yet demoting yourself out
        # of `admin:members:manage` is the same lockout, and the floor for
        # undoing it is the permission you just gave up. Runs before invariant
        # 1 so the caller hears the true reason: "assign another owner first"
        # invites them to do exactly that and then hit the real wall.
        await assert_not_self_demotion(db, admin, member, role_ids=role_ids)

        # Demoting the last owner is the same lockout as removing them.
        if "owner" not in req.roles:
            await assert_owner_survives(db, org_id, excluding_user_id=member["id"])

        await set_roles(db, member["id"], role_ids, admin.email)

        # Keep the legacy coarse column truthful so require_role() and any
        # not-yet-migrated route agree with the new model (spec §7).
        legacy = "executive" if any(
            s in ("owner", "admin") for _rid, s in role_ids
        ) else "employee"
        await db.execute(
            text(
                "UPDATE app_user SET role = :role, updated_at = now() "
                " WHERE id = CAST(:uid AS uuid)"
            ),
            {"role": legacy, "uid": member["id"]},
        )
        roles = await roles_for_user(db, member["id"])

    invalidate_for(member["email"])
    _log.info("member_roles_set", email=member["email"], by=admin.email, roles=roles)
    record_admin_change(admin.email, "org.member_roles_set",
                        f"user:{member['email']}", roles=roles)
    return MemberEntry(
        email=member["email"],
        display_name=member["display_name"] or "",
        status=member["status"],
        roles=roles,
    )


# ── Per-user access + overrides ─────────────────────────────────────────────

async def _load_overrides(db: Any, user_id: str) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            text(
                "SELECT permission, effect, reason, set_by, set_at "
                "  FROM user_permission_override "
                " WHERE user_id = CAST(:uid AS uuid) ORDER BY permission"
            ),
            {"uid": user_id},
        )
    ).mappings().all()
    return [dict(r) for r in rows]


async def _role_permission_map(db: Any, user_id: str) -> dict[str, list[str]]:
    """``{role_slug: [permission, ...]}`` for the member's assigned roles."""
    rows = (
        await db.execute(
            text(
                "SELECT r.slug AS slug, rp.permission AS permission "
                "  FROM user_role ur "
                "  JOIN org_role r            ON r.id = ur.role_id "
                "  JOIN org_role_permission rp ON rp.role_id = r.id "
                " WHERE ur.user_id = CAST(:uid AS uuid)"
            ),
            {"uid": user_id},
        )
    ).mappings().all()
    out: dict[str, list[str]] = {}
    for r in rows:
        out.setdefault(r["slug"], []).append(r["permission"])
    return out


def _agent_names() -> list[str]:
    """Registered agent names, static + dynamic. Best-effort.

    Imported lazily: the admin package must not fail to load because the agent
    registry is unavailable — an access screen that 500s is worse than one
    that shows only feature toggles.
    """
    try:
        from gateway.routes.agent import (  # noqa: PLC0415
            _AGENT_REGISTRY,
            _load_dynamic_agents,
        )

        names = {a["name"] for a in _AGENT_REGISTRY}
        try:
            names |= {a["name"] for a in _load_dynamic_agents()}
        except Exception:  # noqa: BLE001
            pass
        return sorted(names)
    except Exception:  # noqa: BLE001
        return []


def _integration_names() -> list[str]:
    """Registered integration service names. Best-effort.

    Lazily imported for the same reason as the agent registry: an access
    screen that 500s because one registry is unavailable is worse than one
    showing fewer rows.
    """
    try:
        from acb_skills.integrations import list_registered  # noqa: PLC0415

        return sorted(list_registered())
    except Exception:  # noqa: BLE001
        return []


def _explain(
    access: EffectiveAccess, permission: str, role_perms: dict[str, list[str]]
) -> dict[str, Any]:
    """Resolve one permission and name what decided it."""
    decision = access.decide(permission)
    via_role = ""
    if decision.source == "role" and decision.pattern:
        # Name the role that contributed the deciding pattern, so the UI can
        # say "granted by role member" rather than "granted by feature:chat".
        for slug, perms in role_perms.items():
            if decision.pattern in perms:
                via_role = slug
                break
    return {
        "permission": permission,
        "allowed": decision.allowed,
        "source": decision.source,
        "pattern": decision.pattern or "",
        "via_role": via_role,
    }


@router.get("/members/{email}/access", summary="Resolved access for one member")
async def get_member_access(
    email: str,
    admin: UserContext = Depends(require_admin_user),
) -> dict[str, Any]:
    """The member's effective access, with provenance for every decision."""
    async with _tenant_session() as db:
        org_id = await get_org_id(db, admin)
        member = await get_member(db, org_id, email)
        roles = await roles_for_user(db, member["id"])
        role_perms = await _role_permission_map(db, member["id"])
        overrides = await _load_overrides(db, member["id"])

    access = build_access(
        [p for perms in role_perms.values() for p in perms],
        [(o["permission"], o["effect"]) for o in overrides],
        roles=roles,
        is_active=member["status"] == "active",
    )

    return {
        "email": member["email"],
        "display_name": member["display_name"] or "",
        "status": member["status"],
        "roles": roles,
        "granted": sorted(access.granted),
        "denied": sorted(access.denied),
        "features": [
            _explain(access, feature_permission(f), role_perms) | {"slug": f}
            for f in FEATURES
        ],
        "capabilities": [
            _explain(access, c, role_perms) for c in CAPABILITIES
        ],
        "agents": [
            _explain(access, agent_run_permission(n), role_perms) | {"name": n}
            for n in _agent_names()
        ],
        # Which third-party services an agent may use ON THIS MEMBER'S BEHALF.
        # Separate from `integrations:manage` (adding/rotating the credentials),
        # which appears under capabilities.
        "integrations": [
            _explain(access, integration_use_permission(s), role_perms) | {"name": s}
            for s in _integration_names()
        ],
        "overrides": [
            {
                "permission": o["permission"],
                "effect": o["effect"],
                "reason": o["reason"] or "",
                "set_by": o["set_by"] or "",
                "set_at": _iso(o["set_at"]),
            }
            for o in overrides
        ],
    }


@router.put("/members/{email}/overrides", summary="Replace a member's overrides",
            dependencies=[require_permission("admin:access:manage")])
async def set_member_overrides(
    email: str,
    req: OverrideRequest,
    admin: UserContext = Depends(require_admin_user),
) -> dict[str, Any]:
    """Replace the member's allow/deny overrides wholesale.

    This is the endpoint behind "no WhatsApp, no app creator, but these two
    agents": deny ``feature:whatsapp`` and ``feature:build.apps``, allow
    ``agents:run:<name>``.
    """
    cleaned: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for entry in req.overrides:
        if entry.effect not in ("allow", "deny"):
            raise HTTPException(
                status_code=400,
                detail=f"effect must be 'allow' or 'deny', got '{entry.effect}'.",
            )
        try:
            perm = validate_permission(entry.permission)
        except InvalidPermission as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if perm in seen:
            raise HTTPException(
                status_code=400,
                detail=f"Duplicate override for '{perm}' — one effect per permission.",
            )
        seen.add(perm)
        cleaned.append((perm, entry.effect, entry.reason or ""))

    async with _tenant_session() as db:
        org_id = await get_org_id(db, admin)
        member = await get_member(db, org_id, email)

        # An owner who denies themselves admin cannot undo it from the UI.
        if (member["email"] or "").lower() == (admin.email or "").lower():
            self_denied = [
                p for p, effect, _ in cleaned
                if effect == "deny" and matched_by([p], "admin:access:manage")
            ]
            if self_denied:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "This would revoke your own access management "
                        "permission. Ask another admin to make this change."
                    ),
                )

        await db.execute(
            text(
                "DELETE FROM user_permission_override WHERE user_id = CAST(:uid AS uuid)"
            ),
            {"uid": member["id"]},
        )
        for perm, effect, reason in cleaned:
            await db.execute(
                text(
                    "INSERT INTO user_permission_override "
                    "  (user_id, permission, effect, reason, set_by) "
                    "VALUES (CAST(:uid AS uuid), :perm, :effect, :reason, :by)"
                ),
                {"uid": member["id"], "perm": perm, "effect": effect,
                 "reason": reason, "by": admin.email},
            )

    invalidate_for(member["email"])
    _log.info("member_overrides_set", email=member["email"], by=admin.email,
              count=len(cleaned))
    # The overrides themselves, not just the count: "who lost WhatsApp and
    # when" is the question this record exists to answer.
    record_admin_change(admin.email, "org.member_overrides_set",
                        f"user:{member['email']}",
                        overrides=[{"permission": p, "effect": e, "reason": r}
                                   for p, e, r in cleaned])
    return await get_member_access(member["email"], admin)  # type: ignore[arg-type]
