"""Sign-in requests — the queue of people knocking at a door nobody opened.

Spec: ``project-docs/specs/colleague_onboarding.md`` §6 (WS-24 / N6a).

``/admin/members`` is push-only: the only way an ``app_user`` row was ever
created is an admin typing an address into Invite. Somebody arriving at the
front door created nothing an admin could see — the refusal was logged and
then discarded, so the owner's only way to learn that a colleague was locked
out was for that colleague to say so. One address knocked 53 times over 18
hours on 2026-08-03/04 and the system told nobody.

``acb_auth.access.resolve_access`` now files that knock into ``access_request``
(migration 143) when — and only when — it comes from the sign-in path. This
module is the other half: the owner sees the queue and answers it.

Four things worth knowing before editing:

1. **The ``/admin`` auth floor is per-route, not a package property.**
   ``_common.py`` creates the router with **no** ``dependencies=``; every route
   declares ``Depends(require_admin_user)`` in its own signature. A route added
   here that omits it inherits no floor at all and is reachable by any
   authenticated member. ``tests/unit/test_signin_requests.py`` pins that.
2. **No new permission slug.** Approve and deny reuse
   ``admin:members:invite``, which the roles seed already grants. A brand-new
   slug is nobody's grant until an admin creates it — which would switch the
   feature off for the owner too (the N4 lesson, spec §4).
3. **A decided request is not a re-usable handle.** Both writes are gated on
   ``admin:members:invite``, which is *weaker* than the
   ``admin:members:manage`` that suspends or off-boards somebody. Rows are kept
   after a decision (§6 done-when 9) and the tab renders only `pending`, so a
   decided row is invisible **and** still findable — exactly the shape that
   lets a weaker permission quietly reverse a stronger one. ``_load_request``
   takes the statuses it will act on as a required argument, and
   ``_common._PROVISION_MEMBER_SQL`` refuses to activate anything that is not
   `invited`. Two locks, because either alone is one edit away from open.
4. **Approve either fully succeeds or refuses out loud.** A 200 that did not
   admit the person is the defect this module exists to eliminate. The lock in
   (3) makes provisioning *refuse* an activation; that refusal must not be
   followed by a `_decide(..., "approved")` that files the row away as
   answered — the person would leave the queue (it renders only `pending`,
   and the resolver's upsert deliberately never rewrites `status`) while still
   being locked out, with the owner shown success. That is the 53-knock
   incident, recreated by its own fix. :data:`APPROVE_MATRIX` is the whole
   answer, decided per existing ``app_user.status`` **before** anything is
   written.
"""

from __future__ import annotations

from typing import Any

from acb_auth import UserContext, is_company_email, require_permission
from acb_auth.access import mirror_identity_membership, mirror_membership_status
from fastapi import Depends, HTTPException
from gateway.routes.admin._common import (
    _iso,
    _log,
    _tenant_session,
    find_member,
    get_org_id,
    invalidate_for,
    provision_member,
    record_admin_change,
    require_admin_user,
    roles_for_user,
    router,
)
from pydantic import BaseModel, Field
from sqlalchemy import text

#: A request is only ever in one of these. `pending` is the owner's inbox;
#: the other two are the record of a decision, kept rather than deleted so a
#: repeat knock from a denied address does not read as a new one.
REQUEST_STATUSES = ("pending", "approved", "denied")

#: **What approve does about an address that ALREADY has an ``app_user`` row**,
#: keyed by that row's status. Spec ``colleague_onboarding.md`` §6. Held as
#: data rather than as branches so the matrix, the spec and the fence are one
#: thing; ``tests/unit/test_signin_requests.py`` pins it against
#: ``members.VALID_STATUSES``, so a fifth member status cannot appear without
#: somebody deciding what approving one means.
#:
#: An absent row is deliberately **not** a key — that is the normal path, and
#: it is the only one this route was originally written for.
#:
#: The invariant the three non-`provision` answers exist to hold: **approve
#: never rewrites the roles of a member who already exists in a state other
#: than `invited`.** ``provision_member`` ends in ``set_roles``, which
#: REPLACES a member's assignments wholesale, and approve holds only
#: ``admin:members:invite`` — while roles are otherwise governed by
#: ``PUT /admin/members/{email}/roles`` on the stronger
#: ``admin:members:manage``. So approving a stale request over a live `admin`
#: with the picker's default silently demoted them to `member`.
#:
#: * ``invited`` → provision: activate and assign the chosen roles. That IS
#:   what approving means, and it is the one action §2's two-click trap needs.
#: * ``active`` → they already have access. Refuse the role rewrite, but
#:   resolving the request as `approved` is *truthful* here — they do have
#:   access — so the row leaves the queue and the response says why.
#: * ``suspended`` / ``removed`` → refuse (409) and leave the request
#:   `pending`. Both states were set under ``admin:members:manage``;
#:   reinstatement is that permission's act, not this one's. Leaving the row
#:   `pending` is load-bearing: the queue renders only `pending`, and the
#:   resolver's upsert never rewrites `status`, so a row filed as `approved`
#:   here would take the person out of the owner's sight permanently while
#:   they were still locked out.
APPROVE_MATRIX: dict[str, str] = {
    "invited": "provision",
    "active": "already-a-member",
    "suspended": "refuse",
    "removed": "refuse",
}

#: Why the refusal, in the admin's terms, and where to go instead. Keyed by
#: the same statuses ``APPROVE_MATRIX`` marks `refuse`; an unrecognised status
#: falls back to the generic line, because a status nobody has decided about
#: must fail closed rather than be provisioned.
_REFUSAL_REASONS: dict[str, str] = {
    "suspended": (
        "'{email}' is already a member, and they are suspended. Approving a "
        "sign-in request cannot lift a suspension — that was an "
        "admin:members:manage decision and approving holds only "
        "admin:members:invite. Reinstate them from the roster (Activate)."
    ),
    "removed": (
        "'{email}' is an off-boarded member. Approving a sign-in request "
        "cannot bring them back — off-boarding was an admin:members:manage "
        "decision and approving holds only admin:members:invite. Reinstate "
        "them from the roster if that is what you mean."
    ),
}

#: The decision write. ``AND status = ANY(:allowed)`` is not belt-and-braces
#: for :func:`_load_request`: read-then-write is two statements, so two admins
#: clicking the same row both pass the read. Binding the same condition into
#: the UPDATE makes the loser's row-count zero, and because this runs BEFORE
#: the commit (`_tenant_session` commits only on clean exit) the 409 it raises
#: discards that transaction's provisioning with it — approve stays
#: all-or-nothing under concurrency, not just in sequence.
_DECIDE_SQL = (
    "UPDATE access_request SET status = :status, decided_by = :by, "
    "       decided_at = now() "
    " WHERE lower(email) = :email AND status = ANY(:allowed) "
    "RETURNING id::text AS id"
)

_REQUEST_COLUMNS = (
    "SELECT id::text AS id, email, display_name, first_seen_at, last_seen_at, "
    "       attempt_count, status, decided_by, decided_at "
    "  FROM access_request "
)

#: The owner's inbox. Held as a constant so the fence can read it: the fake DB
#: in ``tests/unit/test_signin_requests.py`` ignores ``ORDER BY`` entirely, so
#: "newest knock first" (§6 done-when 6) is only ever a claim about THIS STRING.
_PENDING_REQUESTS_SQL = (
    _REQUEST_COLUMNS + " WHERE status = 'pending' ORDER BY last_seen_at DESC"
)


# ── Models ──────────────────────────────────────────────────────────────────

class AccessRequestEntry(BaseModel):
    email: str
    display_name: str = ""
    first_seen_at: str = ""
    last_seen_at: str = ""
    #: How many times they tried. The number is what makes a row legible as
    #: somebody stuck rather than somebody curious.
    attempt_count: int = 0
    status: str = "pending"
    #: The address is outside ``ALLOWED_EMAIL_DOMAIN``. Resolved on the server
    #: because the domain is server policy and the browser must not re-derive
    #: it. See :func:`_entry` for why the row has to say so.
    is_external: bool = False


class ApproveRequest(BaseModel):
    #: Role slugs to assign. Empty means the seeded default `member` — the same
    #: default the invite path applies.
    roles: list[str] = Field(default_factory=list)


class ApproveResult(BaseModel):
    email: str
    #: The member's status AFTER the approval — `active` on every path that
    #: returns 200. Echoed rather than assumed: it is read back from the row.
    status: str = ""
    #: The member's roles as they now stand. On the `already-a-member` path
    #: these are their EXISTING roles, not the ones the caller asked for —
    #: approve does not rewrite an existing member's roles (see
    #: :data:`APPROVE_MATRIX`), and returning the request would be a lie.
    roles: list[str] = Field(default_factory=list)
    #: Empty on the normal path. Non-empty when the approval resolved the
    #: request WITHOUT doing what the caller literally asked — today that is
    #: only "they were already a member, so their roles were left alone". A
    #: 200 that quietly did something else is the failure mode this whole
    #: module is a response to, so the difference is stated, not implied.
    detail: str = ""


def _entry(row: dict[str, Any]) -> AccessRequestEntry:
    """Project one row, flagging an address from outside the company domain.

    **Why the flag exists.** ``deps.get_current_user`` branch 1a only *logs* an
    off-domain identity (`auth.identity_domain_mismatch`) and passes it into
    the resolve that files this row — deliberately, so a member whose sign-in
    address is off-domain is not locked out. Nor does the Entra tenant pin
    exclude these addresses: a **B2B guest is a directory member** and
    authenticates against the tenant like anybody else, so "tenant-pinned"
    bounds who can authenticate, not who works here. Approve provisions
    `active` immediately, so the one place that difference can still be seen is
    this row — an unmarked queue makes a guest look like a colleague.
    """
    return AccessRequestEntry(
        email=row["email"],
        display_name=row["display_name"] or "",
        first_seen_at=_iso(row["first_seen_at"]),
        last_seen_at=_iso(row["last_seen_at"]),
        attempt_count=int(row["attempt_count"] or 0),
        status=row["status"],
        is_external=not is_company_email(row["email"]),
    )


async def _load_request(
    db: Any, email: str, *, allowed_statuses: tuple[str, ...]
) -> dict[str, Any]:
    """Fetch one request by email, or 404 — and refuse an already-decided row.

    ``allowed_statuses`` is keyword-only with **no default**, the same shape as
    ``routes/tasks/people._row_to_person(row, *, include_hr)``: a route added
    later must state which rows it is entitled to act on rather than inherit
    the permissive answer by omission.

    **This filter is a security boundary, not tidiness.** A decided row
    outlives its decision — that is the point of keeping it (§6 done-when 9) —
    so without this check the decided row is still findable and re-POSTing
    approve re-runs provisioning. Combined with an off-boarding
    (``DELETE /admin/members/{email}``, gated ``admin:members:manage``) that
    turned a `removed` row back into `active` on the weaker
    ``admin:members:invite``: a decision taken under the stronger permission,
    reversed by the weaker one, and invisible because the Requests tab renders
    only `pending`. ``_common._PROVISION_MEMBER_SQL`` closes the same hole from
    the other end; both are here on purpose.

    The refusal is checked in Python rather than bound into the ``WHERE``
    clause deliberately: the fake DB the tests run against matches SQL on
    substrings and does not evaluate predicates, so a filter hidden in the SQL
    would be unfenceable by a behavioural test, and an admin looking at a stale
    tab deserves "already approved by X" rather than "no such request".

    That is a statement about the READ. :data:`_DECIDE_SQL` binds the same
    condition into the write as well, and the two are not redundant: this one
    exists to produce a message a human can act on, that one to make two
    concurrent decisions resolve to one. Neither replaces the other.
    """
    row = (
        await db.execute(
            text(_REQUEST_COLUMNS + " WHERE lower(email) = :email"),
            {"email": (email or "").strip().lower()},
        )
    ).mappings().first()
    if row is None:
        raise HTTPException(
            status_code=404, detail=f"No sign-in request from '{email}'."
        )
    request = dict(row)
    if request["status"] not in allowed_statuses:
        raise HTTPException(
            status_code=409,
            detail=(
                f"This sign-in request was already {request['status']}"
                + (f" by {request['decided_by']}" if request["decided_by"] else "")
                + ". A decided request cannot be decided again — manage the "
                  "member from the roster instead."
            ),
        )
    return request


async def _decide(
    db: Any,
    email: str,
    status: str,
    admin: UserContext,
    *,
    allowed_statuses: tuple[str, ...],
) -> None:
    """Stamp the decision, or 409 if the row is no longer the one we read.

    The status vocabulary is closed, and enforced here rather than only in the
    migration — a typo'd status would silently drop the row out of both the
    pending list and the decided record.

    ``allowed_statuses`` repeats :func:`_load_request`'s filter **inside the
    write**, and mirrors it exactly. The read and the write are two statements;
    between them another admin's transaction can decide the same row, and
    without the condition here both writers would succeed and both callers
    would be told they won. Zero rows updated means the row moved, so the whole
    transaction — this stamp *and* any provisioning done above it — is
    abandoned by raising before the commit (``_tenant_session`` commits only
    on clean exit).
    """
    if status not in REQUEST_STATUSES:
        raise ValueError(f"unknown access_request status {status!r}")
    row = (
        await db.execute(
            text(_DECIDE_SQL),
            {"status": status, "by": admin.email or "", "email": email,
             "allowed": list(allowed_statuses)},
        )
    ).mappings().first()
    if row is None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"This sign-in request from '{email}' was decided by somebody "
                "else while this decision was in flight; nothing here was "
                "changed. Refresh the queue and look again."
            ),
        )


def _disposition_for(existing: dict[str, Any] | None) -> str:
    """Read :data:`APPROVE_MATRIX` for one address, or raise the refusal.

    Called BEFORE anything is written, so a refusal leaves the request
    `pending` and the member row untouched — no compensating logic, and
    nothing for a later edit to forget to undo.

    Fails closed on a status nobody has decided about: an `app_user.status`
    added tomorrow refuses here rather than falling through to provisioning,
    which is the direction that cannot let somebody in by accident.
    """
    if existing is None:
        return "provision"

    status = str(existing["status"])
    action = APPROVE_MATRIX.get(status, "refuse")
    if action != "refuse":
        return action

    template = _REFUSAL_REASONS.get(status)
    if template is None:
        # Never ``.format`` a DB value into a template — the status is data.
        reason = (
            f"'{existing['email']}' is already a member in status "
            f"'{status}', which approving a sign-in request does not know how "
            "to answer. Manage them from the roster."
        )
    else:
        reason = template.format(email=existing["email"])
    raise HTTPException(
        status_code=409,
        detail=(
            reason
            + " The request is left pending, so they stay visible in the "
              "queue rather than disappearing from it while still locked out."
        ),
    )


# ── Routes ──────────────────────────────────────────────────────────────────

@router.get("/members/requests", summary="Pending sign-in requests")
async def list_access_requests(
    admin: UserContext = Depends(require_admin_user),
) -> list[AccessRequestEntry]:
    """Everyone who authenticated and found no door, newest knock first.

    Sits on the package's ``admin:members:read`` floor like every other read —
    seeing who is locked out is part of reading the roster, not a new right.
    """
    async with _tenant_session() as db:
        rows = (await db.execute(text(_PENDING_REQUESTS_SQL))).mappings().all()
    return [_entry(dict(r)) for r in rows]


@router.post("/members/requests/{email}/approve",
             summary="Approve a sign-in request",
             dependencies=[require_permission("admin:members:invite")])
async def approve_access_request(
    email: str,
    req: ApproveRequest,
    admin: UserContext = Depends(require_admin_user),
) -> ApproveResult:
    """Provision the member **and activate them**, in one action.

    Deliberately `active`, not `invited`: an approval IS the admin's decision
    to let this person in, and they are already standing at the door. Leaving
    them `invited` would re-create §2's two-click trap — the exact failure this
    ticket exists to close — inside the fix for it.

    Provisioning goes through ``_common.provision_member``, the same helper
    ``POST /admin/members`` uses, so there is one provisioning path and
    invariants 1 and 2 apply here too: an admin cannot approve somebody
    straight into `owner`, and approving the last owner with the default
    `member` role cannot silently drop the org's only owner grant.

    **Only a `pending` request can be approved.** Two admins clicking the same
    row is a normal race and must never produce two members; the loser now
    learns *why* (409, naming the decision) instead of receiving a 200 that
    silently did nothing. That 200 was also the vehicle for a real escalation —
    a request approved, the member later off-boarded under
    ``admin:members:manage``, and the same row re-approved under the weaker
    ``admin:members:invite`` to put them back. See ``_load_request``.

    **And only some existing members can be approved over.**
    :data:`APPROVE_MATRIX` is consulted before anything is written, because
    the refusal ``_PROVISION_MEMBER_SQL`` performs is silent by construction —
    it declines to rewrite the row and says nothing. Running the decision
    stamp afterwards anyway turned that quiet refusal into a 200, took the
    person out of a queue that renders only `pending`, and recorded an
    `org.access_request_approved` for an approval that did not happen.
    """
    async with _tenant_session() as db:
        request = await _load_request(db, email, allowed_statuses=("pending",))
        org_id = await get_org_id(db, admin)

        existing = await find_member(db, org_id, request["email"])
        disposition = _disposition_for(existing)   # 409s on suspended/removed

        detail = ""
        provisioned = False
        if existing is not None and disposition == "already-a-member":
            # Nothing to provision and — the point — nothing to re-grant:
            # `set_roles` REPLACES assignments, and roles are governed by
            # `admin:members:manage`. Resolving the request as `approved` is
            # still the truth: they do have access.
            member = existing
            detail = (
                f"'{member['email']}' is already an active member, so nothing "
                "was provisioned and their roles were left exactly as they "
                "were. The request has been marked approved because they do "
                "have access."
            )
        else:
            member, _assigned = await provision_member(
                db, org_id,
                email=request["email"],
                display_name=request["display_name"] or "",
                roles=req.roles,
                admin=admin,
                status="active",
            )
            provisioned = True

        # Verify; do not predict. `_disposition_for` read `app_user` BEFORE
        # the upsert, and `_PROVISION_MEMBER_SQL`'s CASE arms are re-evaluated
        # by Postgres against the latest committed row (ON CONFLICT DO UPDATE
        # waits on a concurrent writer, then re-reads). So a second admin
        # off-boarding or suspending this person in that window lands every
        # arm on `ELSE app_user.status`, and the provisioning declines in
        # silence — which is exactly the shape the matrix above exists to
        # stop, one table over. `_DECIDE_SQL` made the `access_request` row
        # race-safe; without this check the `app_user` row stayed a
        # read-then-write with no write-side guard, and approve would stamp
        # `approved` over a person who still cannot sign in, removing them
        # from a queue that renders only `pending`.
        #
        # Raising here abandons the whole transaction — the provisioning
        # above included — because nothing has been committed yet.
        if member["status"] != "active":
            raise HTTPException(
                status_code=409,
                detail=(
                    f"'{member['email']}' was changed by somebody else while "
                    "this approval was in flight and is now "
                    f"'{member['status']}', so nothing here was applied and "
                    "the request is still waiting. Refresh the queue and "
                    "look again."
                ),
            )

        await _decide(db, member["email"].lower(), "approved", admin,
                      allowed_statuses=("pending",))
        roles = await roles_for_user(db, member["id"])
        status = member["status"]

    # H6 (WS-29, DARK, D48): mirror the identity shadow AFTER the authoritative
    # `app_user` write is durably committed — moved OUT of `provision_member`
    # (WS-29 H6 orphan-closure) so the shadow only ever mirrors a write that
    # actually landed. This is the case the move exists for: a concurrent-approve
    # 409 in `_decide` (or the `member["status"] != "active"` guard above) rolls
    # back the whole `_tenant_session` BEFORE this point, so the mirror never runs
    # and no active shadow orphan is committed. Only when provisioning actually
    # happened — the `already-a-member` path wrote no `app_user` row, so it mirrors
    # nothing (byte-identical to the pre-move behaviour). Best-effort, own session,
    # moves no read, `app_user` byte-identical. See `acb_auth.access`.
    if provisioned:
        await mirror_identity_membership(
            email=member["email"],
            display_name=member.get("display_name") or "",
            org_id=org_id,
            status=member["status"],
        )
        await mirror_membership_status(
            email=member["email"], org_id=org_id, status=member["status"],
        )

    # Their refusal is cached for up to 60s; without this they would be
    # approved and still bounced, which reads as the approval not working.
    invalidate_for(member["email"])
    _log.info("access_request_approved", email=member["email"], by=admin.email,
              roles=roles, attempts=request["attempt_count"],
              disposition=disposition)
    # `disposition` rides along so the audit record cannot be read as a
    # provisioning that did not occur.
    record_admin_change(admin.email, "org.access_request_approved",
                        f"user:{member['email']}", roles=roles,
                        attempts=request["attempt_count"],
                        disposition=disposition)
    return ApproveResult(
        email=member["email"], status=status, roles=roles, detail=detail,
    )


@router.post("/members/requests/{email}/deny",
             summary="Deny a sign-in request",
             dependencies=[require_permission("admin:members:invite")])
async def deny_access_request(
    email: str,
    admin: UserContext = Depends(require_admin_user),
) -> dict[str, str]:
    """Mark the request denied. Nothing is provisioned.

    A denied address that keeps signing in still bumps ``last_seen_at`` and
    ``attempt_count`` — that is the resolver's upsert, which never touches
    ``status`` — so the row stays out of the owner's queue rather than
    reappearing on the next sign-in.

    **Re-denying a denied row is harmless and stays allowed**, so a stale tab
    or a double click answers 200 rather than an error nobody can act on: deny
    provisions nobody, revokes nothing and takes no access away, so replaying
    it changes only ``decided_by``/``decided_at``. Denying an **approved**
    request is refused (409): it would not touch the live member it created,
    so the only thing it could achieve is a queue record that contradicts the
    roster. Suspend or remove them from the roster instead.
    """
    async with _tenant_session() as db:
        request = await _load_request(
            db, email, allowed_statuses=("pending", "denied"),
        )
        await _decide(db, request["email"].lower(), "denied", admin,
                      allowed_statuses=("pending", "denied"))

    _log.info("access_request_denied", email=request["email"], by=admin.email,
              attempts=request["attempt_count"])
    record_admin_change(admin.email, "org.access_request_denied",
                        f"user:{request['email']}",
                        attempts=request["attempt_count"])
    return {"status": "denied", "email": request["email"]}
