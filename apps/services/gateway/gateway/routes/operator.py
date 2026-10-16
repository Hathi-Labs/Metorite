"""The operator door — two acts on the tenant plane, both staff-only.

1. **Organization purge (CP-2g).** ``DELETE /internal/operator/organizations/
   {slug}`` destroys every tenant-plane row an organization owns. Since WS-17
   EM-G11f1c it also tears down each mailbox of the org, as the member purge
   does: :func:`plan_org_teardown` runs inside the purge, and
   ``finish_teardowns`` runs after it.
2. **Member removal (owner request, 2026-10-15).** ``DELETE /internal/operator/
   organizations/{slug}/members/{email}?mode=remove|purge&confirm=<email>``
   removes or purges ONE member of one organization. It runs the SAME
   in-transaction body as the admin routes (``routes/admin/members.py``
   ``remove_member_rows`` / ``purge_member_rows``), so a removal means one
   thing whichever door does it.

The operator console's BFF is the ONLY intended caller. It holds
``GATEWAY_OPERATOR_TOKEN`` server-side (the ``CUSTOMER_CONSOLE_OPERATOR_TOKEN``
idiom applied to the gateway). For the org purge it sequences the Console's
authority check FIRST (``organization.status == 'deleted'`` — terminal on the
lifecycle graph, so it cannot be un-entered between check and purge), and only
then calls this door. That ordering is fenced from the BFF side
(``operator_console/src/app/api/operator/purge/route.test.ts``).

⚠️ **The member door does NOT call the Customer Console.** The BFF calls the
Console's ``POST /registry/members/remove`` itself, with its own operator
token. The gateway's Console client holds a DEPLOYMENT key, which acts for a
member of the org, and an operator is not one.

## Two tokens, deliberately (repair round 1)

The gateway mounts ``require_authenticated`` as an APP-LEVEL dependency, and
these routes are intentionally NOT in ``PUBLIC_ROUTES`` — so a caller must first
clear the gateway's ordinary machine auth (``Authorization: Bearer`` with the
internal token), and THEN present the door's own credential in
``X-Operator-Token``. Review round 1's P0 was the first draft reading the
operator token from ``Authorization``: the app-level gate consumed that header
and refused every call — the door was a shipped no-op, green only against a
bare test app. The fix is NOT an auth exemption: the internal token alone must
never suffice (its unprovisioned-box fallback is ``LITELLM_MASTER_KEY`` — a
credential agents hold cannot double as an org-destroy credential), and the
operator token alone must not bypass the gateway's ordinary gate either. Both,
always. ``test_operator_door.py`` runs against the REAL ``gateway.main.app``
for exactly this reason.

Ship-dark: with ``GATEWAY_OPERATOR_TOKEN`` unset the door answers **503** on
every call that cleared the app-level gate — same posture as the Console's own
auth ("not configured" is a server state, not a caller error), and the state
every box is in until the owner writes the env. Deploy-side, ``/internal/*``
is additionally blocked at Caddy so the door is loopback-only.

R11: the org comes from the slug in the PATH, looked up server-side. That is
the operator's subject, the same shape as the org purge. The operator is
cross-tenant by design and names the tenant it acts on. No tenant comes from a
header, and no member identity becomes the actor.
"""
from __future__ import annotations

import os
import secrets
from functools import partial
from typing import Annotated, Any

from acb_auth.access import mirror_membership_status, purge_identity_shadow
from acb_common import get_logger
from fastapi import APIRouter, Header, HTTPException
from gateway.db import tenant_session as _tenant_session
from gateway.routes.admin._common import (
    find_member,
    invalidate_for,
    record_admin_change,
)
from gateway.routes.admin.members import (
    PurgeResult,
    purge_member_rows,
    remove_member_rows,
)
from gateway.routes.email.memory_purge import RECORD_DETACHED_PURGES_SQL
from gateway.routes.email.teardown import (
    OrgScope,
    TeardownCounts,
    TeardownPlan,
    finish_teardowns,
    read_teardown_plans,
)
from sqlalchemy import text

_log = get_logger("gateway.routes.operator")

#: The first statement of the org purge callback (EM-G11f1c, fix round 1).
#: It locks the org row until the purge commits. A mailbox connect inserts an
#: ``email_accounts`` row whose foreign key takes a KEY SHARE lock on that org
#: row, so it waits for the purge and then fails on the foreign key. No
#: mailbox can join the org between the plan read and the cascade.
ORG_LOCK_SQL = "SELECT 1 FROM organization WHERE id = CAST(:org AS uuid) FOR UPDATE"

router = APIRouter(prefix="/internal/operator", tags=["operator"])

#: The two member modes. ``remove`` is the soft off-boarding and the default,
#: because it is the reversible one. ``purge`` is the hard delete, and the
#: caller must name it.
_MEMBER_MODES = ("remove", "purge")

#: Who the audit entry names for an operator act. The operator is not a member
#: of the org, so no member address is the actor.
_OPERATOR_ACTOR = "operator"


def _require_operator_token(presented: str | None) -> None:
    """Constant-time check of ``X-Operator-Token`` against the env.

    Reads the env per-request (not module-load) so tests and env reloads see
    the current value — and because a cached secret is a rotation hazard.
    """
    expected = os.environ.get("GATEWAY_OPERATOR_TOKEN", "").strip()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="GATEWAY_OPERATOR_TOKEN is not configured",
        )
    presented = (presented or "").strip()
    if not presented or not secrets.compare_digest(presented, expected):
        raise HTTPException(status_code=401, detail="operator token refused")


async def plan_org_teardown(db: Any, org_id: str) -> list[TeardownPlan]:
    """The callback of the org purge (WS-17 EM-G11f1c, §12.3.13.9.2).

    ``acb_auth.offboard.purge_tenant_organization`` runs it in its tenant
    session, BEFORE its first delete, so each write here commits with
    ``DELETE FROM organization`` or not at all. It opens nothing and commits
    nothing. It makes no network call.

    0. It locks the org row (:data:`ORG_LOCK_SQL`), so no mailbox can join the
       org before the cascade.
    1. It reads the teardown plan of each mailbox of the org, under FORCE RLS
       in the session that the purge bound. More than
       ``TEARDOWN_MAX_MAILBOXES`` mailboxes raise a 409, and the purge rolls
       back whole.
    2. It calls the SECURITY DEFINER ``record_detached_purges`` of migration
       276. That function writes one ``email_detached_purges`` row for each
       mailbox, and moves each open ``email_mailbox_purges`` row of the org
       there. The cascade of the org delete would delete those rows, and then
       no sweep could find the Mem0 purges that the org owes. The function
       takes rows only from the org that this transaction binds.

    It returns the plans, and the purge puts them in its receipt. The door
    takes them out before it answers, because a plan holds a credential blob.
    """
    await db.execute(text(ORG_LOCK_SQL), {"org": org_id})
    plans = await read_teardown_plans(db, OrgScope(org_id))
    recorded = int((await db.execute(
        text(RECORD_DETACHED_PURGES_SQL), {"org": org_id})).scalar() or 0)
    # A mailbox that a member connected after the plan read goes with the
    # delete, and it has no plan, so nothing revokes its grant. The line says
    # so, with counts and no address.
    if recorded > len(plans):
        _log.warning("email.teardown.unplanned_mailbox",
                     planned=len(plans), deleted=recorded)
    return plans


@router.delete("/organizations/{slug}")
async def purge_organization(
    slug: str,
    confirm: str,
    x_operator_token: Annotated[
        str | None, Header(alias="X-Operator-Token")
    ] = None,
) -> dict[str, Any]:
    """Destroy every tenant-plane row the organization owns.

    ``confirm`` must echo the slug — the typed-confirmation contract the
    operator UI enforces, repeated here so a mis-wired caller cannot purge by
    accident. Idempotent: an already-absent org answers 200 with
    ``already_absent: true`` (the retry arm for a half-failed two-plane purge;
    the BFF surfaces that answer to the operator rather than treating it as
    success — the wrong-box case must reach a human).

    **The mailboxes (WS-17 EM-G11f1c).** The purge runs
    :func:`plan_org_teardown` in its transaction, before its first delete.
    Above the mailbox cap that callback answers 409, and the purge rolls back
    whole. After the purge returns, the door logs the purge, and THEN tears
    down each mailbox through ``finish_teardowns``: the sync loop, the
    filters and rules, the Google grant, the Graph subscription and the Redis
    keys. The door waits ``TEARDOWN_WAIT_S`` at most, and the receipt carries
    the ``mailboxes`` counts. The plans never reach the receipt.
    """
    _require_operator_token(x_operator_token)
    if confirm != slug:
        raise HTTPException(
            status_code=400,
            detail="confirm must equal the organization slug, verbatim",
        )

    from acb_auth.offboard import purge_tenant_organization

    try:
        receipt = await purge_tenant_organization(
            slug=slug, before_delete=plan_org_teardown)
    except HTTPException:
        # The 409 of the mailbox cap. The purge rolled back and deleted
        # nothing, and the operator must hear the count, not a 502.
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # a half-failed purge must be REPORTED, not 500'd bare
        _log.error("tenant_org_purge_failed", slug=slug, error=str(exc))
        raise HTTPException(
            status_code=502,
            detail=f"tenant purge failed and was rolled back: {exc}",
        ) from exc
    plans: list[TeardownPlan] = receipt.pop("before_delete", None) or []
    org_id = receipt.get("organization_id")
    if not plans or not org_id:
        receipt["mailboxes"] = TeardownCounts().model_dump()
        return receipt
    # EM-G11f1c (email_app_master_plan.md §12.3.13.9.2): the same teardown as
    # the member purge, after the commit. It goes LAST, after the log line,
    # so a stop of the process in its wait loses no record. The org and its
    # members are gone, so no My Day is bumped.
    _log.warning("operator_org_purged", slug=slug, organization_id=org_id,
                 mailboxes=len(plans))
    mailboxes = await finish_teardowns(
        org_id, plans, open_session=partial(_tenant_session, org_id),
        stop_loops=True, bump_owners=False)
    receipt["mailboxes"] = mailboxes.model_dump()
    return receipt


@router.delete("/organizations/{slug}/members/{email}")
async def remove_organization_member(
    slug: str,
    email: str,
    confirm: str,
    mode: str = "remove",
    x_operator_token: Annotated[
        str | None, Header(alias="X-Operator-Token")
    ] = None,
) -> dict[str, Any]:
    """Remove or purge one member of one organization (owner request, 2026-10-15).

    **The order of the refusals.** FastAPI validates the query first, so a
    request with no ``confirm`` at all gets its 422 before this body runs.
    Inside the body the operator token comes first, so an unconfigured box
    answers 503 and a wrong token 401 before ``confirm``, ``mode`` or the slug
    is checked. Then ``confirm`` must equal the email VERBATIM (400): the typed
    confirmation, repeated here so a mis-wired caller cannot remove somebody
    by accident. Then ``mode`` must be ``remove`` or ``purge`` (400).

    **The org.** Looked up by slug, unbound, through
    ``acb_auth.offboard.organization_id_for_slug``. An unknown slug is 404,
    because a member of an org that is not here is a wrong box, and the
    operator must hear that.

    **The member.** An address with no row in that org answers 200
    ``{"already_absent": true, "email": ...}``. It is not a 404, because the
    BFF relays it and the Console half may still hold the person.

    **The guard.** Invariant 1 (``assert_owner_survives``) runs inside the
    shared body and answers 409. There is no self-lockout guard, because the
    operator is not a member.

    **After the commit.** ``remove`` mirrors the status into the identity
    shadow, drops the cached access and writes one audit entry.
    ``purge`` deletes the shadow row and drops the cached access. Its audit
    entry is written before the commit, inside the shared body. Then it tears
    down each mailbox of the member through ``finish_teardowns`` (EM-G11f1b),
    and the answer carries the ``mailboxes`` counts. Neither mode calls the
    Customer Console, as the module docstring explains.
    """
    _require_operator_token(x_operator_token)
    if confirm != email:
        raise HTTPException(
            status_code=400,
            detail="confirm must equal the member email, verbatim",
        )
    if mode not in _MEMBER_MODES:
        raise HTTPException(
            status_code=400,
            detail=f"mode must be one of {list(_MEMBER_MODES)}",
        )

    from acb_auth.offboard import organization_id_for_slug

    # Whitespace-trim ONLY, never case-fold: the org purge's rule, and its
    # reason (a mixed-case slug must not read as absent).
    org_id = await organization_id_for_slug((slug or "").strip())
    if org_id is None:
        raise HTTPException(status_code=404, detail=f"no organization {slug!r}")

    async with _tenant_session(org_id) as db:
        member = await find_member(db, org_id, email)
        if member is None:
            return {"already_absent": True, "email": email}
        if mode == "remove":
            tree_projects = await remove_member_rows(db, org_id, member)
        else:
            deleted, kept, plans = await purge_member_rows(
                db, org_id, member, actor_email=_OPERATOR_ACTOR,
            )

    if mode == "remove":
        await mirror_membership_status(
            email=member["email"], org_id=org_id, status="removed",
        )
        invalidate_for(member["email"])
        _log.warning("operator_member_removed", slug=slug,
                     email=member["email"], tree_projects=tree_projects)
        record_admin_change(_OPERATOR_ACTOR, "org.member_removed",
                            f"user:{member['email']}", tree="sealed",
                            tree_projects=tree_projects, via="operator")
        return {"status": "removed", "email": member["email"],
                "tree_projects": tree_projects}

    addr = (member["email"] or "").strip().lower()
    await purge_identity_shadow(email=addr, org_id=org_id)
    invalidate_for(member["email"])
    _log.warning("operator_member_purged", slug=slug, email=member["email"],
                 deleted=deleted, kept=kept)
    # EM-G11f1b (email_app_master_plan.md §12.3.13.9.2): the same teardown as
    # the admin door, after the session block exits. It goes LAST, after the
    # log line, so a stop of the process in its wait loses no record.
    mailboxes = await finish_teardowns(
        org_id, plans, open_session=partial(_tenant_session, org_id),
        stop_loops=True, bump_owners=False)
    return PurgeResult(email=member["email"], deleted=deleted,
                       kept=kept, mailboxes=mailboxes).model_dump()
