"""User roles and the per-request identity context.

Two layers live here, and the split matters:

**Legacy coarse role** (``UserRole``) — ``executive`` / ``employee`` / ``agent``,
derived from the ``X-User-Role`` header. Every route written before the org
access-control work guards on this via ``require_role()``, so it is kept
verbatim: deploying migration 128 changes nobody's access.

**Effective access** (``UserContext.access``) — the DB-backed permission set
resolved from the member's roles and per-user overrides. New code guards on
this via ``require_permission()``. See
``project-docs/specs/org_access_control.md``.

The two coexist by design. `app_user.role` is dual-written and the migration
maps executive→admin / employee→member, so a route can move from one to the
other independently.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from acb_auth.permissions import NO_ACCESS, EffectiveAccess


class UserRole(StrEnum):
    EXECUTIVE = "executive"
    EMPLOYEE = "employee"
    AGENT = "agent"      # service-to-service


def _coerce_role(raw: str | None) -> "UserRole":
    """Parse a role string case-insensitively; unknown values fall back to EMPLOYEE."""
    if not raw:
        return UserRole.EMPLOYEE
    try:
        return UserRole(raw.lower().strip())
    except ValueError:
        return UserRole.EMPLOYEE


@dataclass(slots=True, frozen=True)
class UserContext:
    """Resolved identity for one request.

    ``email`` and ``role`` are the original, still-load-bearing fields. The
    rest default to "nothing", so any code path that builds a UserContext
    without resolving access gets default-deny rather than accidental
    authority.
    """

    email: str | None
    role: UserRole
    #: `app_user.id`, or the RLS-EXEMPT `user_identity.id` under IDENTITY_CUTOVER
    #: (H6 slice 3b) — a DIFFERENT UUID space. Treat as an opaque per-human
    #: identity token for display/correlation only, NEVER an `app_user` FK: the
    #: backend keys on email (`access.resolve_identity` P2b). None until the
    #: member is provisioned in the org.
    user_id: str | None = None
    organization_id: str | None = None
    access: EffectiveAccess = field(default=NO_ACCESS)

    # ── Legacy coarse role ──────────────────────────────────────────────────

    @property
    def is_executive(self) -> bool:
        return self.role is UserRole.EXECUTIVE

    @property
    def is_employee(self) -> bool:
        return self.role is UserRole.EMPLOYEE

    @property
    def is_agent(self) -> bool:
        return self.role is UserRole.AGENT

    # ── Effective access ────────────────────────────────────────────────────

    @property
    def roles(self) -> frozenset[str]:
        """Org role slugs held by this member (e.g. ``{"admin"}``)."""
        return self.access.roles

    @property
    def permissions(self) -> frozenset[str]:
        """Granted permission *patterns* — wildcards are not expanded."""
        return self.access.granted

    def has_permission(self, permission: str) -> bool:
        return self.access.has(permission)

    def can_use_feature(self, slug: str) -> bool:
        """``can_use_feature("whatsapp")`` → ``feature:whatsapp``."""
        return self.access.can_use_feature(slug)

    def can_run_agent(self, agent_name: str) -> bool:
        """``can_run_agent("agent-sales")`` → ``agents:run:agent-sales``."""
        return self.access.can_run_agent(agent_name)

    def with_access(
        self,
        access: EffectiveAccess,
        *,
        user_id: str | None = None,
        organization_id: str | None = None,
    ) -> "UserContext":
        """Return a copy carrying resolved access (the context is frozen)."""
        return UserContext(
            email=self.email,
            role=self.role,
            user_id=user_id if user_id is not None else self.user_id,
            organization_id=(
                organization_id if organization_id is not None
                else self.organization_id
            ),
            access=access,
        )
