"""Permission vocabulary and the resolution rule.

This module is **pure** — no DB, no FastAPI, no I/O. It defines what a
permission string looks like, how a granted pattern matches a required
permission, and how grants + per-user overrides combine into a yes/no. The
DB-backed side lives in :mod:`acb_auth.access`; the request-time side in
:mod:`acb_auth.deps`.

Spec: ``project-docs/specs/org_access_control.md`` §3.3–§3.4.

Grammar
-------
Colon-separated lowercase segments, e.g. ``feature:whatsapp``,
``agents:run:agent-sales``, ``admin:members:invite``. ``*`` is legal **only as
the final segment**, where it matches any non-empty suffix:

    "*"              matches everything
    "feature:*"      matches "feature:whatsapp", "feature:build.apps"
    "agents:run:*"   matches "agents:run:agent-sales"
    "feature:*:read" REJECTED — see validate_permission()

Inward-matching wildcards are refused on purpose. A grant set you cannot read
top-to-bottom is a grant set nobody audits, and nothing in the product needs
them.

Resolution
----------
Two layers. Roles are the baseline; per-user overrides are exceptions layered
on top, and only overrides compete with each other:

    1. Among the OVERRIDE patterns matching `required`, the most specific one
       decides. A tie goes to deny.
    2. If no override matches, the role grants decide.
    3. Otherwise, deny.

Specificity, not a flat "deny always wins", is what makes the product's real
request expressible. "Give them `member`, but only these two agents" is:

    role:     agents:run:*                     (baseline — every agent)
    override: agents:run:*              deny   (take the blanket away)
    override: agents:run:email-assistant allow (hand two back)

Under flat deny-wins the specific allow could never surface, so an admin would
have to clone a role per person — which is the failure mode roles exist to
prevent. Under specificity the exact allow beats the wildcard deny, while a
same-specificity conflict still resolves to deny.

Keeping role grants OUT of that comparison is equally deliberate: an override
of `feature:*` = deny must switch everything off, and it would not if a role's
exact `feature:chat` could out-specify it. An admin's explicit exception
outranks the role, always; specificity only orders exceptions against each
other.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

# ── Vocabulary ──────────────────────────────────────────────────────────────

#: Nav panes / product surfaces. Kept in sync with the `feature_catalog` table
#: (seeded by infra/postgres/130_org_access_control.sql, extended with the
#: `center.*` rows by infra/postgres/140_center_features.sql) and the
#: frontend's nav.ts + centers.ts.
#:
#: This tuple — not the catalog table — is what `allowed_features()` iterates,
#: so a slug seeded in SQL but missing here is invisible to `/auth/me` and
#: therefore unreachable in the UI **even for an owner holding `*`**: the
#: wildcard is only ever evaluated against these literals. Adding a
#: feature_catalog row means adding it here too
#: (tests/unit/test_org_access_control.py pins the Center half of that).
FEATURES: tuple[str, ...] = (
    "chat",
    "email",
    "whatsapp",
    "memory",
    "tasks",
    "notes",
    # Native CRM — leads, deals, contacts, organizations (144_crm.sql,
    # category `apps`, sort_order 55: beside Tasks). NOT a member default:
    # `feature:crm` reaches `*`-holders and `admin`'s `feature:*` until an
    # admin grants it (D-CRM-3, specs/crm_app.md §5).
    "crm",
    # Native project management — departments, projects, subprojects, tasks
    # (146_projects.sql, category `apps`, sort_order 56: after CRM, so the three
    # work surfaces sit together). NOT a member default, same posture as `crm`:
    # `feature:projects` reaches `*`-holders and `admin`'s `feature:*` until an
    # admin grants it (specs/project_management_app.md §5). Note this feature
    # scopes DATA as well as navigation — the grant model in §4 is what decides
    # which projects a holder actually sees.
    "projects",
    # The People Center's directory, person page and org chart (149_people.sql,
    # category `apps`, sort_order 57: after Projects, so the work surfaces and
    # the people behind them sit together). NOT a member default, same posture
    # as `crm` and `projects` (specs/people_center_app.md §6). The directory
    # itself is open to holders; the HR-sensitive half of a person record stays
    # restricted by `admin:members:read`, a projection WS-24 N4 already shipped
    # and which this feature must NOT re-implement.
    "people",
    "dashboard",
    "observability",
    "artifacts",
    "models",
    "agents",
    "approvals",
    "integrations",
    "workflows",
    "build.agents",
    "build.apps",
    # Departmental Centers (140_center_features.sql, category `centers`). Order
    # matches that migration's sort_order: `allowed_features()` emits in tuple
    # order and the member access editor renders in it
    # (gateway/routes/admin/members.py). Slug = group slug, 1:1 —
    # department_centers.md §1.
    "center.sales",
    "center.marketing",
    "center.finance",
    "center.operations",
    "center.people",
    "center.company",
)

#: Non-feature permissions, listed so the admin UI can offer a closed set and
#: so a typo in a role definition is catchable rather than silently inert.
CAPABILITIES: tuple[str, ...] = (
    "agents:run:*",
    "agents:manage",
    "apps:use:*",
    "apps:create",
    "apps:publish",
    # Drafting and TEST-running a workflow needs only `feature:workflows`;
    # this is the right to make one live — publish a version, roll back to an
    # earlier one, or disable it. See specs/workflows_app.md Q3.
    "workflows:publish",
    "admin:members:read",
    # Spending the company's money at /settings/billing's checkout. A
    # DELIBERATE new slug, argued in user_management_contract.md §3 against
    # that section's own "do not mint a permission" rule and in
    # specs/subscription_console.md SC-4a's B7 block: no existing capability
    # governs a purchase, and reusing the §3 floor `admin:members:read` would
    # hand purchase authority to every member-reader. Seeded onto `admin`
    # only (178) — `owner` already holds '*'; `manager` is excluded on
    # purpose, which is where this diverges from `workflows:publish`.
    "billing:purchase",
    "admin:members:invite",
    "admin:members:manage",
    "admin:roles:manage",
    "admin:access:manage",
    "admin:settings:manage",
    "admin:audit:read",
    # Which third-party services an agent run may receive credentials for, on
    # this member's behalf. `integrations:manage` is the separate right to
    # add/rotate those credentials.
    "integrations:use:*",
    "integrations:manage",
    # Organisation-wide memory. Personal and agent-scoped memory need no
    # permission: they are already keyed to the acting user / running agent.
    # Org memory is the shared one, so writing to it is the gated act.
    "memory:read_org",
    "memory:write_org",
    "data:org:read",
)

#: System role slugs seeded by migration 128. `agent_service` is never
#: assignable to a person.
SYSTEM_ROLES: tuple[str, ...] = (
    "owner",
    "admin",
    "manager",
    "member",
    "guest",
    "agent_service",
)

ASSIGNABLE_SYSTEM_ROLES: tuple[str, ...] = tuple(
    r for r in SYSTEM_ROLES if r != "agent_service"
)

#: Legacy `app_user.role` → seeded role slug. Preserves everyone's access
#: across the migration and keeps `require_role()` meaningful (spec §7).
LEGACY_ROLE_MAP: dict[str, str] = {
    "executive": "admin",
    "employee": "member",
    "agent": "agent_service",
}

_SEGMENT_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
MAX_PERMISSION_LEN = 128


def feature_permission(slug: str) -> str:
    """``"whatsapp"`` → ``"feature:whatsapp"``."""
    return f"feature:{slug}"


def agent_run_permission(agent_name: str) -> str:
    """``"agent-sales"`` → ``"agents:run:agent-sales"``."""
    return f"agents:run:{agent_name}"


def integration_use_permission(service: str) -> str:
    """``"zoho-crm"`` → ``"integrations:use:zoho-crm"``.

    Service names carry dots and dashes (``zoho-crm``, ``gmail-send``), which
    the segment grammar already allows.
    """
    return f"integrations:use:{service}"


# ── Validation ──────────────────────────────────────────────────────────────

class InvalidPermission(ValueError):
    """A permission string that would be unauditable or is simply malformed."""


def validate_permission(raw: str) -> str:
    """Normalise and validate a permission string, or raise.

    Called on every write path (role editing, override editing) so malformed
    grants never reach the database — an unmatched permission is invisible
    rather than loud, so the check belongs at the boundary.
    """
    p = (raw or "").strip().lower()
    if not p:
        raise InvalidPermission("permission must not be empty")
    if len(p) > MAX_PERMISSION_LEN:
        raise InvalidPermission(f"permission exceeds {MAX_PERMISSION_LEN} chars")
    if p == "*":
        return p

    segments = p.split(":")
    for i, seg in enumerate(segments):
        is_last = i == len(segments) - 1
        if seg == "*":
            if not is_last:
                raise InvalidPermission(
                    f"'{raw}': '*' is only allowed as the final segment"
                )
            continue
        if not _SEGMENT_RE.match(seg):
            raise InvalidPermission(f"'{raw}': invalid segment '{seg}'")
    return p


# ── Matching ────────────────────────────────────────────────────────────────

def permission_matches(pattern: str, required: str) -> bool:
    """Does a granted/denied ``pattern`` cover the ``required`` permission?"""
    if pattern == required:
        return True
    if pattern == "*":
        return True
    if pattern.endswith(":*"):
        prefix = pattern[:-1]          # "feature:*" → "feature:"
        return required.startswith(prefix) and len(required) > len(prefix)
    return False


def matched_by(patterns: Iterable[str], required: str) -> str | None:
    """Return the first pattern covering ``required``, else None.

    Returning the *pattern* rather than a bool is what lets the admin UI
    explain itself ("denied by `feature:*`" beats a bare "no").
    """
    for pattern in patterns:
        if permission_matches(pattern, required):
            return pattern
    return None


def specificity(pattern: str, required: str) -> int:
    """How precisely ``pattern`` names ``required``. Higher is more specific.

    An exact match outranks every wildcard; among wildcards, the longer literal
    prefix wins; bare ``*`` is the floor. Only meaningful for a pattern that
    actually matches.
    """
    if pattern == required:
        return 1_000_000
    if pattern == "*":
        return 0
    return len(pattern) - 1          # literal prefix length, minus the '*'


def most_specific(patterns: Iterable[str], required: str) -> str | None:
    """The matching pattern that names ``required`` most precisely."""
    best: str | None = None
    best_score = -1
    for pattern in patterns:
        if not permission_matches(pattern, required):
            continue
        score = specificity(pattern, required)
        if score > best_score:
            best, best_score = pattern, score
    return best


# ── Resolved access ─────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class AccessDecision:
    """Why a permission resolved the way it did. Powers the admin UI preview."""

    permission: str
    allowed: bool
    #: "deny-override" | "allow-override" | "role" | "default-deny"
    source: str
    #: The pattern that decided it, if any.
    pattern: str | None = None


@dataclass(frozen=True, slots=True)
class EffectiveAccess:
    """A principal's fully-resolved permission set.

    Three pattern sets, kept separate because the resolution rule treats them
    differently (see the module docstring): ``role_granted`` is the baseline,
    ``allowed`` and ``denied`` are the per-user exceptions that compete with
    each other by specificity.

    All three hold *patterns*, not expanded permissions, so a wildcard grant
    stays a wildcard and a newly-registered agent is covered by
    ``agents:run:*`` without anyone re-saving a role.
    """

    roles: frozenset[str] = frozenset()
    role_granted: frozenset[str] = frozenset()
    #: allow-overrides
    allowed: frozenset[str] = frozenset()
    #: deny-overrides
    denied: frozenset[str] = frozenset()
    #: False for suspended/removed/unknown members — they resolve to nothing.
    is_active: bool = True

    @property
    def granted(self) -> frozenset[str]:
        """Everything that grants, for display and export. Not the decision."""
        return self.role_granted | self.allowed

    def decide(self, required: str) -> AccessDecision:
        if not self.is_active:
            return AccessDecision(required, False, "inactive")

        # Layer 1 — overrides, most specific wins, ties go to deny.
        allow = most_specific(self.allowed, required)
        deny = most_specific(self.denied, required)
        if allow is not None or deny is not None:
            if deny is not None and (
                allow is None
                or specificity(deny, required) >= specificity(allow, required)
            ):
                return AccessDecision(required, False, "deny-override", deny)
            return AccessDecision(required, True, "allow-override", allow)

        # Layer 2 — the role baseline.
        role = matched_by(self.role_granted, required)
        if role is not None:
            return AccessDecision(required, True, "role", role)
        return AccessDecision(required, False, "default-deny")

    def has(self, required: str) -> bool:
        return self.decide(required).allowed

    def can_use_feature(self, slug: str) -> bool:
        return self.has(feature_permission(slug))

    def can_run_agent(self, agent_name: str) -> bool:
        return self.has(agent_run_permission(agent_name))

    def allowed_features(self) -> tuple[str, ...]:
        """The feature slugs this principal may reach, in catalog order."""
        return tuple(f for f in FEATURES if self.can_use_feature(f))

    def intersect(self, other: "EffectiveAccess") -> "EffectiveAccess":
        """Narrow this access by ``other`` — used for agent runs.

        An agent acts on behalf of a member and must never exceed them, so
        grants intersect (a pattern survives only if the other side also covers
        it) while denials union. Widening is impossible by construction.

        Both sides' grant sets are flattened to the baseline layer: an
        allow-override is an exception *for a person*, and carrying it into the
        combined set would let it out-specify a denial from the other side —
        the one thing this method exists to prevent.
        """
        mine, theirs = self.granted, other.granted
        granted = frozenset(
            p for p in mine if matched_by(theirs, p) is not None
        ) | frozenset(
            p for p in theirs if matched_by(mine, p) is not None
        )
        return EffectiveAccess(
            roles=self.roles | other.roles,
            role_granted=granted,
            denied=self.denied | other.denied,
            is_active=self.is_active and other.is_active,
        )


def build_access(
    role_permissions: Iterable[str],
    overrides: Iterable[tuple[str, str]] = (),
    roles: Iterable[str] = (),
    *,
    is_active: bool = True,
) -> EffectiveAccess:
    """Combine role grants with ``(permission, effect)`` overrides.

    Invalid strings are dropped rather than raised on: this runs on the read
    path for every request, and one bad row written before validation existed
    must not lock a member out of the product.
    """
    role_granted: set[str] = set()
    allowed: set[str] = set()
    denied: set[str] = set()

    for raw in role_permissions:
        try:
            role_granted.add(validate_permission(raw))
        except InvalidPermission:
            continue

    for raw, effect in overrides:
        try:
            perm = validate_permission(raw)
        except InvalidPermission:
            continue
        if effect == "deny":
            denied.add(perm)
        elif effect == "allow":
            allowed.add(perm)

    return EffectiveAccess(
        roles=frozenset(roles),
        role_granted=frozenset(role_granted),
        allowed=frozenset(allowed),
        denied=frozenset(denied),
        is_active=is_active,
    )


#: What an unknown / signed-out / suspended principal gets.
NO_ACCESS = EffectiveAccess(is_active=False)
