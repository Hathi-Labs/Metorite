"""POST /signup/provision — the self-serve signup flow's server half (CP-2c).

Spec: ``project-docs/specs/customer_console.md`` §6 CP-2c (items 3-7,
done-when 2-5) · ``user_management_contract.md`` R11 · MT-1j slice 7 (the tenant
create-only guard this route relies on).

A signup is COMPLETING for an ORGLESS session: the person authenticated with the
IdP but belongs to no organization yet, and this route creates one with them as
owner, on TWO planes — the tenant plane FIRST (the hard one-email-one-org
guard), the Customer Console SECOND (the registry mirror).

## The posture, mirrored from ``routes/signin.py``

* **BFF-internal bearer**, and NOT in ``PUBLIC_ROUTES`` — authentication is by
  construction (``FastAPI(dependencies=[require_authenticated(…)])``); adding a
  ``PUBLIC_ROUTES`` entry to "make it reachable" is forbidden by name (root
  ``AGENTS.md`` constraint 10). The caller is an orgless session, which
  ``require_authenticated`` admits (it checks only that the email is non-empty).
* **Session-derived email ONLY.** The owner is ``user.email`` from the
  authenticated context, never the body. A body ``email`` / ``org`` /
  ``deployment_label`` is **400, never ignored** (R11) — those are the tenant
  and identity claims a caller must not assert, the same rule the Customer
  Console applies to a deployment key naming an ``org_slug``.
* **IDENTITY-ONLY, binds no tenant.** It resolves the acting identity via
  :func:`acb_auth.get_current_user` (which labels, never binds), not
  ``_with_resolved_access``. An orgless session binds no tenant
  (``deps.py:~299``), and every read below goes through the shared unbound
  session factory — a stray tenant-bound call would fail closed with
  ``TenantUnbound``.
* **Inherited residual, stated not fixed.** This route inherits ``signin.py``'s
  accepted risk that an internal-token holder can assert any ``X-User-Email``,
  and is BROADER: sign-in only RESOLVES an existing org, but this route CREATES
  an org + owner, so a token holder could squat slugs. Narrowed by the
  ``GATEWAY_INTERNAL_TOKEN`` split (§6 WS-24(b)) and shipped dark behind
  ``SELF_SERVE_SIGNUP_ENABLED`` — an accepted, gated residual, recorded here.

## Why this route may call ``acb_auth.console_resolve`` (the second importer)

``console_resolve`` is otherwise reachable from exactly one place
(``routes/signin.py``) because ``resolve_for_signin`` allocates a SEAT and a
second call site would make the cap farmable. This route calls a DIFFERENT
function on it — :func:`~acb_auth.console_resolve.provision_org_on_console`,
which mirrors a provision and allocates no seat by itself — and it is likewise a
session-email-only route, so ``test_console_dependency_boundary.py`` grows its
allow-list to these two importers and no more. What stays forbidden is wiring
either behind ``resolve_access`` (six callers, one a room fan-out) = farmable
seat burn.

## Ships dark, both flag positions fenced

The GATEWAY reads its own ``SELF_SERVE_SIGNUP_ENABLED`` (a settings field, from
the gateway env). Not exactly ``"true"`` ⇒ ``SignupDisabled``, no organization
created, fail closed. ``console_resolve`` does NOT read this flag; the resolve
path is byte-identical under both positions.
"""
from __future__ import annotations

import re
from typing import Annotated, Any

from acb_auth import UserContext, get_current_user
from acb_auth.access import membership_of, org_owner_of
from acb_auth.console_resolve import (
    CONSOLE_UNAVAILABLE,
    ConsoleProvisionUnavailable,
    provision_org_on_console,
)
from acb_common import get_logger, get_settings
from acb_common.provisioning import (
    OwnerBelongsElsewhere,
    SlugOwnedByAnother,
    mark_console_mirrored,
    persist_org_billing_profile,
    provision_local_organization,
)
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

_log = get_logger("gateway.signup")

router = APIRouter(prefix="/signup", tags=["signup"])

# ── The wire vocabulary (item 3's four outcome codes + the four 400 codes) ───

#: The flag was not exactly ``"true"``. The feature is off; nothing is created.
SIGNUP_DISABLED = "SignupDisabled"
#: Step 0a / step 1's ``OwnerBelongsElsewhere`` — this email already owns an org.
#: Names only the caller's OWN org (``org_name`` on the wire), never a third
#: party's existence.
ALREADY_MEMBER = "AlreadyMember"
#: Step 0b / step 1's ``SlugOwnedByAnother`` — the requested slug is owned by
#: someone else. Names NOTHING beyond "this name is unavailable".
SLUG_TAKEN = "SlugTaken"
#: A required registered state was blank (400).
MISSING_STATE = "MissingState"
#: A GSTIN was present but structurally invalid (400).
INVALID_GSTIN = "InvalidGstin"
#: A required organization slug was missing/blank/whitespace (400). The mirror
#: of ``MissingState`` — a blank slug is a malformed REQUEST, never a signup
#: outcome. Without it a blank slug reaches ``provision_local_organization("")``
#: and migration 179's generic ``P0001`` surfaces through the broad step-1
#: ``except`` as a FALSE ``ConsoleUnavailable`` ("retry"), so a PERMANENT shape
#: error masquerades as a transient one and the caller retries forever.
MISSING_SLUG = "MissingSlug"
#: A slug was present but not DNS-label-safe (400). Closes the same false-transient
#: hole for a non-empty-but-malformed slug (e.g. an internal space) that would
#: otherwise reach the cross-plane join key unvalidated.
INVALID_SLUG = "InvalidSlug"

#: R11: the tenant/identity claims a caller must not assert in the body. The
#: owner is the SESSION email; the deployment is the Console key's own. Present
#: is 400, never ignored — an ignored field is a caller who believes it worked.
_FORBIDDEN_BODY_KEYS = frozenset({"email", "org", "deployment_label"})

#: The GST identification number's structure (customer_console.md CP-2c item 4).
#: Optional, but validated when present so a typo is caught at signup rather
#: than at the first invoice.
_GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")

#: The slug's shape: a DNS-label-safe subdomain, forward-compatible with MT-1f's
#: per-tenant ``<slug>.metorite.com`` — lowercase alphanumeric plus internal
#: hyphens, no leading/trailing hyphen, at most 63 characters. Applied with
#: ``re.fullmatch`` (the whole slug is the label, not a prefix of it). An
#: AGENT-PROPOSED DEFAULT (D16/D17): the owner may overrule the exact charset,
#: and slice 4's form mirrors it client-side (advisory — THIS route is the fence).
_SLUG_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")


def _refuse(code: str, **extra: Any) -> dict[str, Any]:
    """A 200 outcome refusal — the refusal is the ANSWER, carried as a code."""
    body: dict[str, Any] = {"admit": False, "code": code}
    body.update(extra)
    return body


def _bad_request(code: str, detail: str) -> JSONResponse:
    """A 400 shape violation. Distinct from the outcome refusals on purpose: a
    body that names a tenant/identity or omits a required field is malformed,
    not a signup outcome the form should render with friendly copy."""
    return JSONResponse(status_code=400, content={"code": code, "detail": detail})


def _slug_shape_refusal(slug: str) -> JSONResponse | None:
    """The slug SHAPE gate: a 400 for a missing/blank slug (the mirror of
    ``MissingState``) or one that is not DNS-label-safe, else ``None``. Extracted
    so the handler stays under the complexity fence while the two refusals join
    the same shape-violation class as ``MissingState``/``InvalidGstin`` — a
    malformed REQUEST, never a 200 outcome."""
    if not slug:
        return _bad_request(MISSING_SLUG, "an organization slug is required")
    if not _SLUG_RE.fullmatch(slug):
        return _bad_request(
            INVALID_SLUG,
            "the slug must be a DNS-label-safe subdomain: lowercase "
            "alphanumeric and internal hyphens, no leading or trailing hyphen, "
            "at most 63 characters",
        )
    return None


@router.post("/provision")
async def provision_signup(
    request: Request,
    user: Annotated[UserContext, Depends(get_current_user)] = None,  # type: ignore[assignment]
) -> Any:
    """Create the caller's organization on both planes, or refuse with a code.

    Always 200 for an OUTCOME (admit, or a refusal code the form renders), and
    400 only for a SHAPE violation (a body that asserts a tenant/identity, a
    missing/malformed slug, a missing registered state, a malformed GSTIN). A
    4xx for an outcome would make "signup says no" indistinguishable from "the
    route is broken".
    """
    settings = get_settings()

    # ── Flag: fail closed. Read FIRST, before the body or any plane is touched,
    # so an off box creates nothing and leaks nothing. `=== "true"` exactly, not
    # truthiness (an operator debugging with `=false` must get OFF).
    if settings.self_serve_signup_enabled != "true":
        _log.info("signup.disabled")
        return _refuse(SIGNUP_DISABLED)

    # ── R11: the body may not assert a tenant or identity. Refused on SHAPE,
    # before any value is read, so naming a real org and a nonexistent one are
    # one refusal.
    try:
        raw = await request.json()
    except Exception:
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    named = _FORBIDDEN_BODY_KEYS & set(raw)
    if named:
        _log.warning("signup.body_claims_identity", keys=sorted(named))
        return _bad_request(
            "InvalidBody",
            "the owner is the authenticated session and the deployment is the "
            "box's own; a body email/org/deployment_label is refused (R11)",
        )

    slug = str(raw.get("slug") or "").strip()
    display_name = str(raw.get("display_name") or "").strip()
    registered_state = str(raw.get("registered_state") or "").strip()
    gstin_raw = raw.get("gstin")
    gstin = str(gstin_raw).strip() if gstin_raw is not None else ""

    # ── Slug SHAPE — the cross-plane join key, refused HERE (a malformed
    # REQUEST, 400) before step 0/1/2 touch either plane. Same shape-violation
    # class as InvalidBody/MissingState/InvalidGstin, never a 200 outcome.
    # Missing/blank/whitespace ⇒ MissingSlug (the mirror of MissingState); a
    # value that is not DNS-label-safe ⇒ InvalidSlug. Without this a blank slug
    # would reach provision_local_organization("") and 179's generic P0001 would
    # come back as a FALSE ConsoleUnavailable — a permanent error told to retry.
    slug_refusal = _slug_shape_refusal(slug)
    if slug_refusal is not None:
        return slug_refusal

    # ── GST: registered state REQUIRED; GSTIN optional but structural when
    # present. Both 400s, both thread to the Console org row below.
    if not registered_state:
        return _bad_request(
            MISSING_STATE, "a registered billing state is required"
        )
    if gstin and not _GSTIN_RE.match(gstin):
        return _bad_request(INVALID_GSTIN, "the GSTIN is structurally invalid")

    # The owner is the SESSION email and nothing else (R11).
    email = (user.email or "") if user else ""

    # ── Step 0 · READ-ONLY pre-flight on the tenant plane (no seat, no write) ─
    # 0a — does this email already have an account? Names only the caller's OWN
    # org, from the tenant `organization` row the helper joins.
    existing = await membership_of(email)
    if existing is not None:
        _log.info("signup.refused", code=ALREADY_MEMBER)
        return _refuse(ALREADY_MEMBER, org_name=existing[1])

    # 0b — is the requested slug already owned by someone who is NOT this email?
    # (If it were owned by THIS email, 0a would already have fired.) Names
    # nothing beyond "unavailable".
    owner = await org_owner_of(slug)
    if owner is not None and owner.lower().strip() != email.lower().strip():
        _log.info("signup.refused", code=SLUG_TAKEN)
        return _refuse(SLUG_TAKEN)

    # ── Step 1 · the TENANT plane, FIRST — the hard one-email-one-org guard ──
    # slice 7's typed raises are the TOCTOU-safe backstop for a signup that
    # raced its own pre-flight, mapped to the SAME two codes so a race is
    # indistinguishable from the pre-flighted case at the wire.
    try:
        await provision_local_organization(
            slug, display_name or None, owner_email=email
        )
    except OwnerBelongsElsewhere:
        # The rare TOCTOU race pre-flight 0a normally catches. Re-read
        # `membership_of` to populate `org_name`, so the wire shape matches the
        # 0a path; if the racing write landed elsewhere (re-read is None), omit
        # `org_name` rather than invent one.
        backstop = await membership_of(email)
        _log.warning("signup.refused_backstop", code=ALREADY_MEMBER)
        if backstop is not None:
            return _refuse(ALREADY_MEMBER, org_name=backstop[1])
        return _refuse(ALREADY_MEMBER)
    except SlugOwnedByAnother:
        _log.warning("signup.refused_backstop", code=SLUG_TAKEN)
        return _refuse(SLUG_TAKEN)
    except Exception as exc:
        # A raise that is NEITHER typed cause is a genuine error, never a false
        # `SlugTaken`. The tenant plane rolled back (the seam's "refused writes
        # nothing" contract), so the org works dark only once step 1 commits.
        _log.error("signup.tenant_provision_failed", error=str(exc)[:200])
        return _refuse(CONSOLE_UNAVAILABLE)

    # ── Step 1.5 · persist the GST profile on the tenant org (CP-2e) ─────────
    # AFTER step 1 (the org now exists) and BEFORE step 2, so a transient step-2
    # failure still leaves `gstin`/`billing_state` recorded for the reconciler
    # sweep to re-drive on — they thread to the Console below and are persisted
    # NOWHERE else on the tenant plane. Best-effort (never raises); a miss only
    # degrades them to NULL, which the sweep tolerates (customer_console.md CP-2e).
    await persist_org_billing_profile(
        slug, gstin=gstin or None, billing_state=registered_state
    )

    # ── Step 2 · the CONSOLE plane, SECOND — the registry mirror ─────────────
    # A fresh tenant-born slug cannot be permanently refused here (the
    # create-only guard passes for the same owner); only transient failures
    # (unwired / unreachable / 5xx) raise, and a resubmit converges because both
    # planes are idempotent on the slug.
    try:
        await provision_org_on_console(
            slug,
            display_name or slug,
            email,
            gstin=gstin or None,
            billing_state=registered_state,
        )
    except ConsoleProvisionUnavailable as exc:
        _log.warning("signup.console_unavailable", error=str(exc)[:200])
        return _refuse(CONSOLE_UNAVAILABLE)

    # ── Step 2.5 · stamp the Console-mirror marker (CP-2e) ───────────────────
    # Mirrored on both planes now, so record it and the reconciler's sweep skips
    # it. The SAME writer the reconciler calls; best-effort and monotonic
    # (`console_mirrored_at IS NULL` in the WHERE), so a miss only leaves the org
    # for a later idempotent-on-slug pass — it can never double-mirror.
    await mark_console_mirrored(slug)

    # Owner on both planes; the flow works dark (the tenant `app_user` admits
    # sign-in while the resolve flag is OFF).
    _log.info("signup.provisioned", slug=slug)
    return {"admit": True, "code": None, "slug": slug}
