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
    ConsoleProvisionRefused,
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

# ── The wire vocabulary (item 3's four outcome codes + the five 400 codes) ───

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
#: A well-formed slug that names a PLATFORM hostname (400). WS-29 MT-1f owner
#: ruling B7, 2026-08-24. Distinct from ``InvalidSlug`` because the value is
#: perfectly well-formed, and distinct from ``SlugTaken`` because the reserved
#: set is static, public and identical for every caller — it reveals nothing
#: about any organization, which is precisely what "taken" would if the two
#: shared one code.
RESERVED_SLUG = "ReservedSlug"
#: A team size was present but is not a whole number in ``1..MAX_TEAM_SIZE``
#: (400). Same shape-violation class as ``InvalidGstin``: the form offers a
#: number input, so a value outside the range is a malformed REQUEST and never a
#: signup outcome.
INVALID_TEAM_SIZE = "InvalidTeamSize"

#: R11: the tenant/identity claims a caller must not assert in the body. The
#: owner is the SESSION email; the deployment is the Console key's own. Present
#: is 400, never ignored — an ignored field is a caller who believes it worked.
_FORBIDDEN_BODY_KEYS = frozenset({"email", "org", "deployment_label"})

#: The GST identification number's structure (customer_console.md CP-2c item 4).
#: Optional, but validated when present so a typo is caught at signup rather
#: than at the first invoice.
_GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")

#: The Core seats a signup grants when the founder says nothing. ONE was the old
#: behaviour and it was a dead end: the founder took the only seat, and the first
#: colleague they invited was refused at the cap with *"ask your admin for an
#: invite"*. One is still the right FLOOR — a solo founder buys nothing they do
#: not need — so the form asks instead of guessing, and this is only the fallback
#: for a body that omits the field.
DEFAULT_TEAM_SIZE = 1

#: The upper bound on a self-serve team size. **OWNER-RULED 2026-09-15: TEN.**
#: An agent proposed fifty and the owner set it here, so this is a decision and
#: not a default any more — do not re-argue it in code.
#:
#: Every Core seat granted here is a free TRIAL seat on an unpaid organization,
#: because the Console opens a 14-day trial in the same transaction. So the
#: number is an abuse bound on a public form, and a bigger team is a sales
#: conversation. The OPERATOR arm has no cap at all, which is the release valve:
#: a company of forty signs up for ten and an operator raises it at activation.
#:
#: D19.3's hard cap is a DIFFERENT rule. It governs assignment beyond what the
#: customer bought, never how many a signup may ask for.
MAX_TEAM_SIZE = 10

#: A team size sent as a STRING must be plain ASCII digits. See ``_team_size``
#: for why neither ``str.isdigit`` nor ``str.isdecimal`` expresses this: one
#: admits characters ``int()`` crashes on, the other admits digits from other
#: scripts. A wire integer has one spelling.
_ASCII_DIGITS_RE = re.compile(r"[0-9]+")

#: How many digits a team size may carry before it is refused WITHOUT being
#: parsed. Derived from ``MAX_TEAM_SIZE`` so the two cannot drift, and it exists
#: because ``int()`` RAISES rather than returns for a very long literal.
_MAX_TEAM_SIZE_DIGITS = len(str(MAX_TEAM_SIZE))

#: The slug's shape: a DNS-label-safe subdomain, forward-compatible with MT-1f's
#: per-tenant ``<slug>.metorite.com`` — lowercase alphanumeric plus internal
#: hyphens, no leading/trailing hyphen, at most 63 characters. Applied with
#: ``re.fullmatch`` (the whole slug is the label, not a prefix of it). An
#: AGENT-PROPOSED DEFAULT (D16/D17): the owner may overrule the exact charset,
#: and slice 4's form mirrors it client-side (advisory — THIS route is the fence).
_SLUG_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")

#: Hostnames a customer may never own, because the platform already does — or
#: intends to. **Owner ruling B7, 2026-08-24** (`saas_multitenancy.md` §11 MT-1f).
#:
#: ⚠️ **This is a LIVE-DEFECT fix and it is not gated by anything.** ``_SLUG_RE``
#: is a *shape* rule, and shape was never the whole rule: measured 2026-08-24 the
#: gate above admits ``api``, ``app`` and ``www``, so a self-serve customer could
#: register the slug that names this very gateway's hostname. The moment MT-1f's
#: wildcard record exists that slug is a hostname collision with a live service,
#: minted by a stranger through a public form — so the refusal ships now, ahead of
#: and independent of ``SUBDOMAIN_WORKSPACE_ENABLED``.
#:
#: ⚠️ **The canonical list lives in ONE place and it is not this one.**
#: ``workbench/control_plane/src/lib/subdomain.ts``'s ``RESERVED_LABELS`` owns the
#: vocabulary (it exists because of DNS, so the host parser owns it) and
#: ``tests/unit/test_subdomain_host_vocabulary.py`` PARSES that file and pins this
#: set equal to it — the ``test_seed_status_colours_match_the_shared_vocabulary``
#: idiom, chosen for the same stated reason: a hand-copied mirror goes stale and
#: then lies. Editing one side without the other is a red test.
#:
#: It is not an existence oracle: static, public, and identical for every caller,
#: which is exactly what separates it from ``SlugTaken``.
#:
#: ⚠️ **WIDENED 2026-08-24 (repair round 1) — additively, and in lockstep with
#: the TypeScript.** B7's thirteen labels all remain; the eight added name
#: surfaces the platform already has or has ticketed (``operator`` = the Operator
#: Console, D35; ``billing``; ``auth``/``login``; ``assets``/``ws``/``dev``/
#: ``staging``). Safe only because self-serve signup is dark, so no customer can
#: already hold one — a later widening must check the org table first.
_RESERVED_SLUGS = frozenset({
    "admin",
    "api",
    "app",
    "assets",
    "auth",
    "billing",
    "cdn",
    "console",
    "dev",
    "docs",
    "help",
    "login",
    "mail",
    "operator",
    "signin",
    "signup",
    "staging",
    "static",
    "status",
    "ws",
    "www",
})


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
    ``MissingState``), one that is not DNS-label-safe, or one that names a
    PLATFORM hostname; else ``None``. Extracted so the handler stays under the
    complexity fence while the three refusals join the same shape-violation
    class as ``MissingState``/``InvalidGstin`` — a malformed REQUEST, never a
    200 outcome."""
    if not slug:
        return _bad_request(MISSING_SLUG, "an organization slug is required")
    if not _SLUG_RE.fullmatch(slug):
        return _bad_request(
            INVALID_SLUG,
            "the slug must be a DNS-label-safe subdomain: lowercase "
            "alphanumeric and internal hyphens, no leading or trailing hyphen, "
            "at most 63 characters",
        )
    # AFTER the shape check, deliberately: a reserved label is perfectly
    # well-formed, so reporting it as malformed would send the customer to fix a
    # thing that is not wrong. Two causes, two codes. The comparison needs no
    # `.lower()` — `_SLUG_RE` above admits lowercase only, so anything reaching
    # here is already folded, and adding one would imply a case this gate can
    # see and the regex cannot.
    if slug in _RESERVED_SLUGS:
        return _bad_request(
            RESERVED_SLUG,
            "that workspace address is reserved for the platform; "
            "please choose a different one",
        )
    return None


def _gst_refusal(registered_state: str, gstin: str) -> JSONResponse | None:
    """The GST gate: state REQUIRED, GSTIN optional but structural when present.

    Extracted 2026-09-15 for the reason :func:`_slug_shape_refusal` was — the
    handler sits against the ``C901`` complexity fence (15), and the team-size
    gate added a branch. Both refusals keep their codes, their ordering and
    their shape-violation class; only their home moved.
    """
    if not registered_state:
        return _bad_request(
            MISSING_STATE, "a registered billing state is required"
        )
    if gstin and not _GSTIN_RE.match(gstin):
        return _bad_request(INVALID_GSTIN, "the GSTIN is structurally invalid")
    return None


def _team_size(raw_value: Any) -> int | JSONResponse:
    """The team size the founder gave, or a 400 for anything that is not one.

    Absent/blank ⇒ :data:`DEFAULT_TEAM_SIZE`, so a body that never learned about
    this field keeps the old behaviour exactly. Otherwise it must be a whole
    number in ``1..MAX_TEAM_SIZE``.

    ⚠️ **``bool`` is refused before ``int``, and that ordering is the whole
    reason this is not a one-liner.** ``isinstance(True, int)`` is True in
    Python, so a JSON ``true`` would otherwise provision an organization with
    one seat and no complaint.

    A digit STRING is accepted because an HTML number input round-trips its
    value as text through ``JSON.stringify`` whenever the form holds it in
    state as a string — which is how the sibling ``core_seats`` field in the
    Operator Console already behaves. Accepting it here means the wire shape
    cannot be got subtly wrong by a caller that is otherwise correct.

    ⚠️ **ASCII digits ONLY, and neither ``isdigit()`` nor ``isdecimal()`` says
    that.** ``"²".isdigit()`` is True while ``int("²")`` raises ``ValueError``,
    so that pair let a shape violation escape as a 500 — breaking this route's
    promise that every shape violation is a 400. ``isdecimal()`` fixes that one
    and still admits ``"٣"``, which ``int()`` reads as 3. Neither is wrong about
    Unicode; both are the wrong question for a WIRE INTEGER, where the set of
    acceptable spellings should be small, obvious and the same in every
    language. So the rule is written out.

    A blank or whitespace-only string is the same statement as an absent field
    — *"I did not answer"* — so both take the default. Treating ``""`` as
    absent and ``"  "`` as malformed would be one rule with two answers.
    """
    if raw_value is None or (
        isinstance(raw_value, str) and raw_value.strip() == ""
    ):
        return DEFAULT_TEAM_SIZE
    if isinstance(raw_value, bool):
        return _bad_request(
            INVALID_TEAM_SIZE, "the team size must be a whole number"
        )
    if isinstance(raw_value, int):
        size = raw_value
    elif isinstance(raw_value, str) and _ASCII_DIGITS_RE.fullmatch(
        raw_value.strip()
    ):
        # ⚠️ LENGTH first, `int()` second, and the order is the whole point.
        # CPython refuses to parse an integer literal beyond
        # `sys.get_int_max_str_digits()` (4300 by default) and raises
        # ValueError — so `int("1" * 4301)` crashed out of this function and
        # FastAPI answered 500, breaking this route's promise that every shape
        # violation is a 400. `isdigit()` had the identical hole before it.
        # `MAX_TEAM_SIZE` is two digits, so anything longer is out of range
        # anyway and the bound below would refuse it: this only decides
        # WHETHER IT IS ASKED AS A 400 OR A CRASH.
        digits = raw_value.strip()
        if len(digits) > _MAX_TEAM_SIZE_DIGITS:
            return _bad_request(
                INVALID_TEAM_SIZE,
                f"the team size must be between 1 and {MAX_TEAM_SIZE}",
            )
        size = int(digits)
    else:
        return _bad_request(
            INVALID_TEAM_SIZE, "the team size must be a whole number"
        )
    if size < 1 or size > MAX_TEAM_SIZE:
        return _bad_request(
            INVALID_TEAM_SIZE,
            f"the team size must be between 1 and {MAX_TEAM_SIZE}",
        )
    return size


async def _mirror_to_console(
    *,
    slug: str,
    display_name: str,
    email: str,
    gstin: str | None,
    registered_state: str,
    team_size: int,
) -> dict[str, Any] | None:
    """Step 2 — mirror the new org onto the Console. A refusal, or ``None``.

    A fresh tenant-born slug cannot be permanently refused by the create-only
    guard (it passes for the same owner), so the ordinary failure here is
    transient — unwired, unreachable, a 5xx — and a resubmit converges because
    both planes are idempotent on the slug.

    Extracted for the reason :func:`_slug_shape_refusal` and
    :func:`_gst_refusal` were: the handler sits against the ``C901`` complexity
    fence, and the two typed refusals below are two more branches. It moves the
    try/except out of the handler and changes neither the call nor the codes.
    """
    try:
        await provision_org_on_console(
            slug,
            display_name,
            email,
            gstin=gstin,
            billing_state=registered_state,
            # The Core seats the new org is born with. Sent because the Console
            # defaults to 1, which left the founder holding the only seat and
            # every colleague they invited refused at the cap.
            core_seats=team_size,
        )
    except ConsoleProvisionRefused as exc:
        # The Console refused this body's SHAPE. Unreachable from THIS route by
        # construction — `_slug_shape_refusal` applies the same rule first, and
        # `test_subdomain_host_vocabulary` pins the two equal — so arriving here
        # means those two have DRIFTED. Caught anyway, because the alternative
        # is a 500 on a signup, and logged at ERROR so the drift is findable
        # rather than rendered to the founder as a transient "try again".
        _log.error(
            "signup.console_refused_shape", slug=slug, error=str(exc)[:200]
        )
        return _refuse(CONSOLE_UNAVAILABLE)
    except ConsoleProvisionUnavailable as exc:
        _log.warning("signup.console_unavailable", error=str(exc)[:200])
        return _refuse(CONSOLE_UNAVAILABLE)
    return None


@router.post("/provision")
async def provision_signup(
    request: Request,
    user: Annotated[UserContext, Depends(get_current_user)] = None,  # type: ignore[assignment]
) -> Any:
    """Create the caller's organization on both planes, or refuse with a code.

    Always 200 for an OUTCOME (admit, or a refusal code the form renders), and
    400 only for a SHAPE violation (a body that asserts a tenant/identity, a
    missing/malformed slug, a missing registered state, a malformed GSTIN, a
    team size that is not a whole number in range). A
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
    gst_refusal = _gst_refusal(registered_state, gstin)
    if gst_refusal is not None:
        return gst_refusal

    # ── Team size → the Core seats step 2 buys. Validated in the SAME
    # shape-violation class as the three above, and BEFORE either plane is
    # touched, so a bad number never leaves a tenant org behind.
    team_size = _team_size(raw.get("team_size"))
    if isinstance(team_size, JSONResponse):
        return team_size

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
    # ⚠️ `core_seats` is persisted HERE and nowhere else on the tenant plane, and
    # leaving it out made this repair undo itself. Step 0a's `AlreadyMember`
    # blocks every resubmit once step 1 has committed, so the CP-2e reconciler is
    # the ONLY route back from a failed step 2 — and it rebuilds the Console call
    # from these columns alone. Without the count it re-drove with none, the
    # Console applied its default of 1, and `grant_seats` runs once only.
    await persist_org_billing_profile(
        slug,
        gstin=gstin or None,
        billing_state=registered_state,
        core_seats=team_size,
    )

    # ── Step 2 · the CONSOLE plane, SECOND — the registry mirror ─────────────
    console_refusal = await _mirror_to_console(
        slug=slug,
        display_name=display_name or slug,
        email=email,
        gstin=gstin or None,
        registered_state=registered_state,
        team_size=team_size,
    )
    if console_refusal is not None:
        return console_refusal

    # ── Step 2.5 · stamp the Console-mirror marker (CP-2e) ───────────────────
    # Mirrored on both planes now, so record it and the reconciler's sweep skips
    # it. The SAME writer the reconciler calls; best-effort and monotonic
    # (`console_mirrored_at IS NULL` in the WHERE), so a miss only leaves the org
    # for a later idempotent-on-slug pass — it can never double-mirror.
    await mark_console_mirrored(slug)

    # Owner on both planes; the flow works dark (the tenant `app_user` admits
    # sign-in while the resolve flag is OFF).
    _log.info("signup.provisioned", slug=slug, core_seats=team_size)
    return {"admit": True, "code": None, "slug": slug}
