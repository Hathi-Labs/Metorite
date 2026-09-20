"""RBAC roles, user context, FastAPI dependency helpers (WBS 1.7).

Two guard styles coexist:

* ``require_role(UserRole.EXECUTIVE)`` — the original coarse gate. Unchanged.
* ``require_permission("feature:whatsapp")`` — org access control: DB-backed
  roles plus per-user allow/deny overrides. See
  ``project-docs/specs/org_access_control.md``.
"""
from acb_auth.access import ensure_owner_bootstrap, identity_read_failed
from acb_auth.access import invalidate as invalidate_access
from acb_auth.access import resolve_access, resolve_session_access
from acb_auth.deps import (
    allowed_email_domain,
    assert_can_run_agent,
    assert_can_run_agent_in_session,
    get_current_user,
    is_company_email,
    require_any_permission,
    require_authenticated,
    require_feature,
    require_feature_router,
    require_internal_auth,
    require_llm_api_auth,
    require_permission,
    require_role,
)
from acb_auth.permissions import (
    ASSIGNABLE_SYSTEM_ROLES,
    CAPABILITIES,
    FEATURES,
    SYSTEM_ROLES,
    AccessDecision,
    EffectiveAccess,
    InvalidPermission,
    agent_run_permission,
    build_access,
    feature_permission,
    integration_use_permission,
    permission_matches,
    validate_permission,
)
from acb_auth.roles import UserContext, UserRole

__all__ = [
    # identity
    "UserRole",
    "identity_read_failed",
    "UserContext",
    "get_current_user",
    # guards
    "require_role",
    "require_permission",
    "require_any_permission",
    "require_authenticated",
    "require_feature",
    "require_feature_router",
    "require_internal_auth",
    "require_llm_api_auth",
    "assert_can_run_agent",
    "assert_can_run_agent_in_session",
    # identity domain (a label the UI renders, never a boundary)
    "allowed_email_domain",
    "is_company_email",
    # permission model
    "EffectiveAccess",
    "AccessDecision",
    "InvalidPermission",
    "FEATURES",
    "CAPABILITIES",
    "SYSTEM_ROLES",
    "ASSIGNABLE_SYSTEM_ROLES",
    "build_access",
    "feature_permission",
    "agent_run_permission",
    "integration_use_permission",
    "permission_matches",
    "validate_permission",
    # resolution
    "ensure_owner_bootstrap",
    "resolve_access",
    "resolve_session_access",
    "invalidate_access",
]
