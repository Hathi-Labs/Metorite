"""Org administration routes (prefix ``/admin``) plus ``/auth/me``.

Members, groups, roles, and per-user access overrides for the multi-user
organization model. Spec: ``project-docs/specs/org_access_control.md``
(+ ``department_centers.md`` §3 Phase B for groups).

Layout mirrors ``routes/apps/``: ``_common`` is the leaf (DB, org lookup,
invariants) and the feature modules import from it, never from each other.
"""

# Importing the modules attaches their routes to the shared `router`.
from gateway.routes.admin import access_requests as _access_requests  # noqa: F401

# `agent_access_switch` attaches `/auth/me/agent-access` to `me_router`
# (WS-55 EA-6h).
from gateway.routes.admin import agent_access_switch as _agent_access_switch  # noqa: F401

# `assistant_profile` attaches `/auth/me/assistant-profile` to `me_router`
# (WS-63 PZ-1).
from gateway.routes.admin import assistant_profile as _assistant_profile  # noqa: F401

# `assistant_profile_people` attaches `/auth/me/assistant-profile/resolve-person`
# to `me_router` (WS-63 PZ-5).
from gateway.routes.admin import (  # noqa: F401
    assistant_profile_people as _assistant_profile_people,
)
from gateway.routes.admin import groups as _groups  # noqa: F401
from gateway.routes.admin import members as _members  # noqa: F401
from gateway.routes.admin import organization as _organization  # noqa: F401
from gateway.routes.admin import roles as _roles  # noqa: F401
from gateway.routes.admin._common import router
from gateway.routes.admin.me import me_router

__all__ = ["me_router", "router"]
