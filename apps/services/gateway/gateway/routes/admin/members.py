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
from functools import partial
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
from acb_auth.access import (
    mirror_identity_membership,
    mirror_membership_status,
    purge_identity_shadow,
)
from acb_auth.console_resolve import (
    ConsoleMemberWriteUnavailable,
    invite_member_on_console,
    remove_member_on_console,
)
from acb_auth.permissions import matched_by
from fastapi import Depends, HTTPException
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

# EM-G11f1b (email_app_master_plan.md §12.3.13.9.2). A purge deletes each
# mailbox of the member, so it tears each one down: the plans and the purge
# rows inside the transaction, and the teardown after the commit.
from gateway.routes.email.memory_purge import RECORD_MEMBER_PURGES_SQL
from gateway.routes.email.teardown import (
    MEMBER_MAILBOXES_WHERE,
    MemberScope,
    TeardownCounts,
    TeardownPlan,
    finish_teardowns,
    read_teardown_plans,
)

# D63 (H-49). The Projects package owns what a personal tree IS, so the seal
# lives there and this module calls it. Putting the SQL here would be a second
# definition of "somebody's personal tree", and the two would drift the first
# time Areas changed shape.
from gateway.routes.projects.seal import (
    seal_counts,
    seal_personal_tree,
    unseal_personal_tree,
)
from pydantic import BaseModel, Field
from sqlalchemy import text

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

    ⚠️ This docstring used to say *"No email is sent — sign-in is Entra ID SSO"*.
    **Both halves are false** (corrected 2026-08-24, D50): sign-in is Google /
    email OTP through Auth.js, and an invite notification email now exists —
    ``subscription_console.md`` SC-2c, sent by the BFF hop
    ``api/admin/members/invite`` AFTER this route answers 2xx, dark behind
    ``MEMBER_INVITE_EMAIL_ENABLED``. **This route still sends nothing itself**,
    and the mail carries **no token** (D50.1): identity is proven at sign-in, so
    the mail is a notification and not an acceptance event.

    "Inviting" means provisioning the row that turns a verified identity into a
    member with access. Until that row exists, an authenticated stranger resolves
    to no access (``resolve_access`` returns inactive for an unknown email).

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

    # H6 (WS-29, DARK, D48): mirror the identity shadow AFTER the authoritative
    # `app_user` write is durably committed — the same post-commit posture
    # `update_member`/`remove_member` already use. Moved OUT of `provision_member`
    # (WS-29 H6 orphan-closure): mirroring inside the still-open caller txn meant
    # a caller rollback left a committed shadow orphan. The existence mirror is
    # create-only; the status mirror keeps `org_membership.status` current for the
    # invited→active/reactivate cases. Best-effort, own session, moves no read,
    # `app_user` byte-identical. See `acb_auth.access`.
    await mirror_identity_membership(
        email=member["email"],
        display_name=member.get("display_name") or "",
        org_id=org_id,
        status=member["status"],
    )
    await mirror_membership_status(
        email=member["email"], org_id=org_id, status=member["status"],
    )

    # CP-2f (WS-31, D50.2): mirror the member onto the CUSTOMER CONSOLE — a
    # different plane and a different database from the H6 shadow two calls
    # above, and the one that decides whether this person can ever join.
    #
    # ⚠️ **Why this matters more than it looks.** Until this existed, the
    # Console's ONLY membership writer was `POST /orgs/provision`'s founder
    # INSERT, so an invited colleague never reached the registry: invisible to
    # `GET /me/members`, a 404 at the seat-assign door, and — with sign-in
    # resolve ARMED — `store.deployment_visible_orgs` returned nothing for them,
    # so their first sign-in answered "zero organizations" and the self-serve
    # funnel offered to create them an org OF THEIR OWN. An invite, correctly
    # performed, produced a second tenant.
    #
    # POST-COMMIT, best-effort, own session (an HTTP hop, not a transaction) —
    # the same posture as the two mirrors above and for the same reason: the
    # authoritative `app_user` write has already committed and must stay
    # byte-identical, so a Console outage can never fail an invite that
    # happened. It ships dark by reach: an unwired box raises before any hop, and
    # no live deployment key carries the `member_admin` capability until the
    # owner grants it by hand (customer_console.md §8 gate 8).
    #
    # ⚠️ It supplies `actor_email` = the AUTHENTICATED ADMIN (R11 — never a body
    # field), names NO organization (the Console derives it from
    # placement ∩ membership) and names NO role (the registry's role vocabulary
    # is not the tenant's — D12). See `acb_auth.console_resolve`.
    try:
        cc_status, cc_body = await invite_member_on_console(
            actor_email=admin.email or "",
            member_email=member["email"],
            display_name=member.get("display_name") or "",
        )
        if cc_status >= 400:
            # The Console REFUSED the mirror — capability revoked, the actor's
            # registry membership suspended, org suspended, a two-org 409, or
            # field-name drift. Without this line a refusal is silent and the
            # invited colleague quietly falls back into the `console-empty`
            # signup funnel, which is the exact failure CP-2f exists to close.
            # (`ConsoleMemberWriteUnavailable`'s docstring promises the two are
            # distinguishable in a log line; this is the verdict half.)
            _log.warning(
                "member_invite_console_refused",
                email=email, status=cc_status, body=str(cc_body)[:200],
            )
        elif (cc_body.get("created") is False
              and cc_body.get("status") not in ("invited", "active")):
            # The Console kept an existing row in a status that does not
            # match the invite (`suspended`, or a status this gateway does
            # not know). The planes now disagree: the tenant says `invited`
            # and the registry does not, so the person's next sign-in takes
            # no seat. Review round 1, P1-a: a silent 200 here is how that
            # state went unseen.
            _log.warning(
                "member_invite_console_status_mismatch",
                email=email, console_status=cc_body.get("status"),
            )
    except ConsoleMemberWriteUnavailable as exc:
        # The box is unwired, or the Console gave no answer. Logged, never
        # raised: the tenant-plane invite stands.
        _log.warning("member_invite_console_unavailable",
                     email=email, error=str(exc)[:200])
    except Exception as exc:
        # Belt and braces around a best-effort mirror on a shipped write path.
        # `mirror_identity_membership` swallows internally; this client returns
        # verdicts instead, so the swallow lives at the call site. No ruff
        # suppression comment here on purpose: the broad-except rule is not
        # enabled in this repo's config, so a directive would only add an
        # unused-suppression finding.
        _log.warning("member_invite_console_failed",
                     email=email, error=str(exc)[:200])

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

    # Declared before the session, because a display-name-only patch never
    # enters the status branch and both names are read after it, on every path.
    tree_action = "none"
    tree_projects = 0

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
            # D63 (H-49) — the personal tree follows the member's standing.
            #
            # IN THE SAME TRANSACTION as the status write, deliberately. A
            # status that says "gone" beside a workspace still visible to the
            # whole org is the exact state this decision exists to prevent, and
            # two statements in two transactions can land that way.
            #
            # `suspended` seals as well as `removed`: both mean the person
            # cannot sign in, and D63 is about the WORKSPACE being closed, not
            # about whether the departure is permanent. `active` unseals, which
            # is what makes a suspension reversible without a hand-written
            # UPDATE against production.
            #
            # ⚠️ Unsealing here returns the tree TO ITS OWNER, who is a member
            # again. It is not D63's watched door — that door lets somebody
            # ELSE read a sealed tree, is owner-only and logged, and no route
            # opens it yet.
            if patch.status in ("suspended", "removed"):
                tree_action = "sealed"
                tree_projects = await seal_personal_tree(
                    db, member["email"], org_id)
            elif patch.status == "active":
                tree_action = "unsealed"
                tree_projects = await unseal_personal_tree(
                    db, member["email"], org_id)
            # `invited` falls through on the declared defaults: somebody who
            # has not joined has no tree to close.
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
    # ⚠️ `tree_action` and `tree_projects` ride on BOTH records, not just the
    # log. D63 requires that closing or reopening somebody's workspace is never
    # an invisible act, and `record_admin_change` is the half a human reads.
    _log.info("member_updated", email=member["email"], by=admin.email,
              status=member["status"], tree=tree_action,
              tree_projects=tree_projects)
    record_admin_change(admin.email, "org.member_updated",
                        f"user:{member['email']}", status=member["status"],
                        tree=tree_action, tree_projects=tree_projects)
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
    tasks). Hard-deleting the identity would orphan all of it; what
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
        # Invariant 1, the writes and the seal all live in the shared body.
        # The operator door (`routes/operator.py`) calls the same body, so the
        # two doors cannot come to disagree about what a removal IS.
        tree_projects = await remove_member_rows(db, org_id, member)

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
    _log.info("member_removed", email=member["email"], by=admin.email,
              tree="sealed", tree_projects=tree_projects)
    record_admin_change(admin.email, "org.member_removed",
                        f"user:{member['email']}",
                        tree="sealed", tree_projects=tree_projects)
    await _mirror_removal_to_console(
        actor_email=admin.email or "", member_email=member["email"],
    )
    return {"status": "removed", "email": member["email"]}


async def remove_member_rows(
    db: Any, org_id: str, member: dict[str, Any]
) -> int:
    """The in-transaction body of a removal. Returns the sealed project count.

    ONE copy, two callers: ``DELETE /admin/members/{email}`` above and the
    operator door ``DELETE /internal/operator/organizations/{slug}/members/
    {email}`` (``routes/operator.py``). Each caller opens its own tenant
    session and passes it in, so this function opens nothing and commits
    nothing.

    It holds invariant 1 (:func:`assert_owner_survives`), because both doors
    need it. It does NOT hold invariant 4 (``assert_not_self_lockout``). Only
    the admin door has a caller who is also a member, so that guard stays in
    the admin route, where the door-enumeration test looks for it.

    The row is kept and only its status changes, for the reason the admin
    route's docstring gives. The role grants go, so nothing re-grants access
    if somebody later sets the status back by hand.
    """
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
    # H-375: a send that they scheduled would go out in their name after they
    # left. The job also refuses a member who is not active, so this is the
    # second of two locks (review of #1209, P1).
    await db.execute(
        text(
            "UPDATE email_scheduled_sends SET status = 'cancelled', updated_at = now() "
            " WHERE lower(owner_email) = lower(:email) AND status = 'queued'"
        ),
        {"email": member["email"]},
    )
    # D63 (H-49) — the SECOND door, and it reaches the same seal.
    #
    # ⚠️ The remove route's own docstring records why: it "used to hold its
    # own copy of the comparison, which is precisely why the other door never
    # grew one". The seal is written into both doors in the same change for
    # that exact reason. A removal that left the tree open would be the whole
    # defect, and it is the door an admin is most likely to use.
    return await seal_personal_tree(db, member["email"], org_id)


async def _mirror_removal_to_console(*, actor_email: str, member_email: str) -> None:
    """Tell the Customer Console about a committed removal. Never raises.

    POST-COMMIT, best-effort, in its own HTTP hop: the invite mirror's posture
    (``invite_member`` above), for the invite mirror's reason. The tenant-plane
    write has already committed, so a Console outage or refusal must never
    change the route's answer. Every outcome is a log line.

    The Console sets the membership to ``removed`` and releases the person's
    seats, so the seat cap stops counting them. Without it a removed person
    holds a paid seat, and the org's admin cannot free it.

    ⚠️ ``actor_email`` is the AUTHENTICATED ADMIN (R11). The Console derives
    the org from placement and membership, so this names no organization.
    """
    try:
        cc_status, cc_body = await remove_member_on_console(
            actor_email=actor_email, member_email=member_email,
        )
        if cc_status >= 400:
            # A verdict, not an outage: capability missing, the actor not an
            # active registry member, or a self-removal. The planes now
            # disagree, and this line is how anyone finds out.
            _log.warning(
                "member_remove_console_refused",
                email=member_email, status=cc_status, body=str(cc_body)[:200],
            )
        elif cc_body.get("owner_transferred_to"):
            # The removed person was the org's last active registry owner, so
            # the Console made the acting admin the owner in the same
            # transaction (P1-b, the owner's decision of 2026-10-15). A change
            # of who owns the account is never a silent 200.
            _log.warning(
                "member_remove_console_owner_transferred",
                email=member_email,
                new_owner=cc_body.get("owner_transferred_to"),
            )
    except ConsoleMemberWriteUnavailable as exc:
        _log.warning("member_remove_console_unavailable",
                     email=member_email, error=str(exc)[:200])
    except Exception as exc:
        # Belt and braces, as at the invite mirror: an unforeseen exception
        # type must not turn a committed removal into a 500.
        _log.warning("member_remove_console_failed",
                     email=member_email, error=str(exc)[:200])


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
# `pm_tasks.created_by`, `workflows.owner_email`, `app_audit.user_email` — so
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
    # The whole mirrored mailbox — 19 direct children, 22 with transitives
    # (`crm_auto_lead_cursors` joined in migration 163: the auto-lead epoch
    # cursor is per-mailbox state and goes with the mailbox. `email_insights`
    # joined in migration 231: the Insights facts of the mailbox, EM-T14a).
    # WS-65 SM-1 adds the four tables of a shared mailbox. A member purge
    # reaches a shared row only while its user_id is the leaver, until SM-8
    # leaves a shared row out of the member scope (shared_mailbox.md §4.2).
    "email_accounts": (
        "crm_auto_lead_cursors",
        "email_actions", "email_ai_drafts", "email_assistant_settings",
        "email_attachments", "email_cold_senders", "email_contacts",
        "email_embeddings", "email_executed_rules", "email_folders",
        "email_insights", "email_knowledge", "email_learned_patterns",
        "email_mailbox_grants", "email_mailbox_member_prefs", "email_messages",
        "email_newsletters", "email_rule_guidance", "email_rule_patterns",
        "email_rules", "email_scheduled_sends", "email_send_log", "email_senders",
        "email_sync_log", "email_thread_assignments", "email_thread_status",
        "email_voice_profiles",
    ),
    # The whole WhatsApp mirror — 14 direct, 16 with transitives
    # (`wa_media` and `wa_message_embeddings` hang off `wa_messages`).
    "wa_accounts": (
        "wa_ai_drafts", "wa_categories", "wa_chat_avatars", "wa_chat_labels",
        "wa_chat_status", "wa_chats", "wa_commitments", "wa_contacts",
        "wa_group_summaries", "wa_labels", "wa_media", "wa_message_embeddings",
        "wa_messages", "wa_saved_replies", "wa_sync_log", "wa_templates",
    ),
    # Nothing, since migration 217 (WS-39 S8 PR 2, 2026-09-23). The account
    # used to cascade the SYNCED half of the retired task store. That store
    # is dropped, and no table left in the schema hangs off this one.
    "task_accounts": (),
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
    # A send that they scheduled (H-375). It would go out in their name after
    # they left, so it goes before the mailbox, and the count is real.
    _PersonRows("scheduled_sends", "email_scheduled_sends",
                "lower(owner_email) = :email"),
    # Credentials.
    # The clause is the teardown's own (EM-G11f1b), so the plans, the purge
    # rows and this delete name one set of mailboxes.
    _PersonRows("email_accounts", "email_accounts", MEMBER_MAILBOXES_WHERE),
    _PersonRows("whatsapp_accounts", "wa_accounts", "lower(user_id) = :email"),
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
    # WS-53 CRM-Q33: the member's own business rules of the CRM. An org rule
    # stays, and `purge_crm_member` takes the address off it.
    _PersonRows("crm_member_rules", "crm_business_rules",
                "scope = 'member' AND lower(member_email) = :email"),
    # WS-61 KB-2f: the member's starred Knowledge pages. A star is the
    # member's own shortcut, so it goes with the member. The page stays.
    _PersonRows("knowledge_favorites", "kb_favorites", "lower(member_email) = :email"),
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
#: `visibility <> 'private'` for that reason. (The two task-store entries
#: carried `account_id IS NULL` for the same one, until migration 217 dropped
#: that store. D63 still holds: a purge deletes no `pm_tasks` row.) It is a structural fence, not a
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
    _PersonRows("meetings", "meeting", "lower(owner_email) = :email"),
    # WS-61 KB-5a: the edits that the assistant proposed for the member. A
    # proposal is authored work about a company page, as the member's pages
    # are, and a decided one is the record of who proposed and who accepted.
    # A pending one stays for a writer of the space to decide. KB-5c expires
    # it after 14 days. Kept whole, so the table is on one side only.
    _PersonRows("knowledge_proposals", "kb_page_proposals",
                "lower(proposer_email) = :email"),
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
    #: EM-G11f1b, additive. The mailboxes that the teardown planned, the ones
    #: that finished before the answer, and the ones that continue after it.
    mailboxes: TeardownCounts = Field(default_factory=TeardownCounts)


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
        # Invariant 1, the counts, the deletes and the pre-commit audit entry
        # all live in the shared body. The operator door calls the same body.
        deleted, kept, plans = await purge_member_rows(
            db, org_id, member, actor_email=admin.email,
        )

    addr = (member["email"] or "").strip().lower()

    # H6 (WS-29, DARK, D48) orphan-closure: the authoritative purge above deletes
    # `app_user` but NOT the RLS-EXEMPT shadow, so without this a purged ACTIVE
    # member left an active `org_membership` ORPHAN (no `app_user`) that reconcile
    # 183 can never fix (it joins `app_user`) and the H6 read cutover would
    # wrong-ADMIT. Deletes ONLY the org's `org_membership` row — the GLOBAL
    # `user_identity` is NEVER touched (deleting it on the last membership was a
    # check-then-cascade cross-tenant erasure race; a membership-less identity is
    # harmless and re-used on re-join — see `purge_identity_shadow`). Best-effort,
    # own session, AFTER the irreversible purge committed, so a failed shadow
    # delete can never roll the purge back.
    await purge_identity_shadow(email=addr, org_id=org_id)

    invalidate_for(member["email"])
    _log.info("member_purged", email=member["email"], by=admin.email,
              deleted=deleted, kept=kept)
    # The registry has no hard delete. A purged person is a `removed`
    # membership there, with every seat released, which is what the seat cap
    # needs. Post-commit and best-effort, as at Remove.
    await _mirror_removal_to_console(
        actor_email=admin.email or "", member_email=member["email"],
    )
    # EM-G11f1b: the mailboxes went with the commit, so tear each one down
    # now, with the session block closed. Stop each sync loop, remove the
    # filters, revoke the grant, drop the Graph subscription and the Redis
    # keys. It goes LAST (fix round 1): its wait can last 60 s, and a stop of
    # the process in that wait must not lose the log line or the mirror.
    mailboxes = await finish_teardowns(
        org_id, plans, open_session=partial(_tenant_session, org_id),
        stop_loops=True, bump_owners=False)
    return PurgeResult(email=member["email"], deleted=deleted, kept=kept,
                       mailboxes=mailboxes)


async def purge_crm_member(db: Any, org_id: str, email: str) -> None:
    """The CRM half of a purge (WS-53 CRM-Q33 fix round 1). The address
    leaves each org rule (``rules.scrub_member``), and the cursor row of the
    questions (``questions.purge_member_state``): the member's proposals go,
    and the record of who set an answer keeps no address. Then the member's
    CRM keys leave tenant Redis (``questions.forget_member_keys``), best
    effort. The SQL runs in the caller's transaction."""
    from gateway.routes.crm import questions as crm_questions
    from gateway.routes.crm import rules as crm_rules

    await crm_rules.scrub_member(db, str(org_id), email)
    await crm_questions.purge_member_state(db, str(org_id), email)
    await crm_questions.forget_member_keys(str(org_id), email)


async def purge_knowledge_member(db: Any, org_id: str, email: str) -> None:
    """The Knowledge half of a purge (WS-61 KB-9a). The address leaves the
    ``askers`` and the ``dismissed_by`` of each gap (``gaps.scrub_member``).
    The gap and its count stay. The SQL runs in the caller's transaction."""
    from gateway.routes.knowledge import gaps as kb_gaps

    await kb_gaps.scrub_member(db, str(org_id), email)


async def purge_member_rows(
    db: Any,
    org_id: str,
    member: dict[str, Any],
    *,
    actor_email: str | None,
) -> tuple[dict[str, int], dict[str, int], list[TeardownPlan]]:
    """The in-transaction body of a purge. Returns ``(deleted, kept, plans)``.

    ONE copy, two callers: ``DELETE /admin/members/{email}/purge`` above and
    the operator door with ``mode=purge`` (``routes/operator.py``). Each caller
    opens its own tenant session and passes it in. This function opens
    nothing and commits nothing, so the whole purge stays one transaction,
    the caller's.

    It holds invariant 1 (:func:`assert_owner_survives`). Unlike a removal,
    a purge cannot be undone by re-inviting: the org would have no owner and
    no row to promote back. Invariant 4 stays in the admin route, for the
    reason :func:`remove_member_rows` gives.

    ``actor_email`` names who did it on the audit entry, which this function
    writes BEFORE the caller's commit (the admin route's docstring says why).

    EM-G11f1b (``email_app_master_plan.md`` §12.3.13.9.2): before the first
    statement of ``_PURGE_DELETES``, it reads the teardown plan of each
    mailbox of the member and writes one ``email_mailbox_purges`` row for
    each, in one set-based statement. Both commit with the deletes, or
    neither does. The caller passes ``plans`` to ``finish_teardowns`` after
    its session block exits. A refusal of invariant 1 raises before the
    read, so it reaches no provider and stops no loop. More than
    ``TEARDOWN_MAX_MAILBOXES`` mailboxes raise a 409 before any delete.
    """
    await assert_owner_survives(db, org_id, excluding_user_id=member["id"])

    addr = (member["email"] or "").strip().lower()
    uid = member["id"]

    # EM-G11f1b: the plans, then the purge rows, before any delete. The
    # cascade of `email_accounts` deletes the filter ids, and the purge row
    # must commit with the delete that owes it.
    plans = await read_teardown_plans(db, MemberScope(addr))
    await db.execute(text(RECORD_MEMBER_PURGES_SQL), {"email": addr})

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

    # EM-G11f1b, fix round 1. A mailbox that the member connected after the
    # plan read goes with the delete, and it has no plan, so nothing revokes
    # its grant. The line says so. It names counts and no address.
    if deleted.get("email_accounts", 0) > len(plans):
        _log.warning("email.teardown.unplanned_mailbox",
                     planned=len(plans), deleted=deleted["email_accounts"])

    for rows in _PURGE_DELETES:
        await db.execute(
            text(rows.delete_sql), _purge_params(rows, uid=uid, email=addr)
        )

    # WS-53 CRM-Q33: the member's address in the CRM state that no delete
    # above reaches, in this transaction, and the member's CRM keys.
    await purge_crm_member(db, org_id, addr)
    # WS-61 KB-9a: the member's address in the Knowledge gaps.
    await purge_knowledge_member(db, org_id, addr)

    # Before the commit, on its own connection — see the admin route's
    # docstring.
    record_admin_change(actor_email, "org.member_purged", f"user:{addr}",
                        deleted=deleted, kept=kept)
    return deleted, kept, plans


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
                    # H6 slice 4 (D48) DUAL-WRITE: shadow `user_identity_id` via
                    # the lower(email) bridge so migration 184's column stays
                    # current. app_user.id stays authoritative; READS user_identity.
                    "INSERT INTO user_permission_override "
                    "  (user_id, permission, effect, reason, set_by, user_identity_id) "
                    "VALUES (CAST(:uid AS uuid), :perm, :effect, :reason, :by, "
                    "        (SELECT ui.id FROM user_identity ui "
                    "           JOIN app_user au ON lower(au.email) = lower(ui.email) "
                    "          WHERE au.id = CAST(:uid AS uuid)))"
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


@router.get("/members/{email}/seal-preview",
            summary="What off-boarding this member would seal")
async def get_seal_preview(
    email: str,
    admin: UserContext = Depends(require_admin_user),
) -> dict[str, Any]:
    """The D63 split, in numbers, BEFORE the click.

    D63's last requirement, and it is quoted rather than paraphrased because
    the wording carries the policy: *"The deactivation dialog must state the
    split in numbers before the click"* — ``"14 tasks — 3 handed over, 11
    sealed, not deleted; later access is recorded"``. **A policy nobody is told
    about at the moment it applies is one they discover by being surprised.**

    A READ, and only a read. It seals nothing, and a caller who never presses
    the button changes nothing by asking. That is why it sits on
    ``require_admin_user`` (``admin:members:read``) rather than the manage
    permission the write doors carry: an admin who may look at the roster may
    know what off-boarding would cost, and making them hold the destructive
    permission to see the warning would be backwards.

    ⚠️ **``handed_over`` is normally 0, and that is correct.**
    ``assert_move_keeps_privacy`` and ``assert_assignable_here`` both refuse to
    create a task in a personal tree assigned to somebody else, so only rows
    predating those guards can carry it. Measured on production 2026-09-23: 2
    tasks in 1 personal tree, 0 of them cross-assigned. The dialog should say
    "0 handed over" plainly rather than hide the line — a zero here is the
    reassuring answer, not a missing one.
    """
    async with _tenant_session() as db:
        org_id = await get_org_id(db, admin)
        # Resolves the member first, so an unknown address 404s here rather
        # than returning a tidy row of zeros that reads as "nothing to lose".
        member = await get_member(db, org_id, email)
        counts = await seal_counts(db, member["email"], org_id)

    return {
        "email": member["email"],
        "status": member["status"],
        **counts,
        # Stated by the SERVER, so the dialog cannot soften it. D63 is explicit
        # that the tree is "retained, invisible, NEVER deleted", and the
        # sentence a person reads at the moment of the act is the only place
        # that promise does any work.
        "deleted": False,
        "reversible": True,
    }
