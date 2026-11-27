"""``GET /auth/me/integrations``: may I use each integration, and why (ACC-3).

Spec: ``project-docs/specs/integrations_console.md`` §3.9.4 and §3.9.5
(slice c). Metorite task 3989. The People tab "My access" reads it. This
module is the backend only.

**Who.** The member and the tenant are the session's (``get_current_user``).
The route takes no parameter, so no request input names a member or an org
(R5). It needs no feature and no admin right: a member reads their own answer.

**The order.**

1. An org off ``INTEGRATION_ACCESS_ORGS`` gets 404, before any session opens
   (``_common.integration_access_allowed``).
2. A caller with no address, or with access that is not active, gets 403.
3. The org comes from ``get_org_id``. The member row is the caller's own
   ``app_user`` row in that org (``find_member`` by the session's address).
   ``user.user_id`` is not used, because under ``IDENTITY_CUTOVER`` it is an
   identity id. No row gets 403.
4. A failed read of the closed list or of the rules gets 503. The route never
   sends a list with no reasons.

**The answer.** ``{"services": [...], "source": "integration-rule"}``. Each
row is exactly ``{"service", "kind", "name", "allowed", "reason"}``, sorted by
service. The list is ``integration_access.closed_list``, unchanged, so a
stored rule of a service that left the list does not show. ``allowed`` is
``user.access.has(integrations:use:<service>)``: the same resolver and the same
cache that the chat's ``_integration_authorizer`` reads. The reason comes from
``_common.member_rule_access``, the step of the admin preview. When that step
does not agree with ``allowed`` (the 60 second cache), ``allowed`` stays and
the reason is ``updating``.

A caller with no grant pattern for any listed service (a guest) gets
``{"services": [], "source": ..., "blocked": "no-role"}``. So a guest does not
learn the names of the MCP servers of the org.

The answer never holds a mode, a team, a person, ``set_by``, ``set_at``, a
subject or an address, not even the caller's own team slugs.

**Limits, stated.** The answer is the caller's solo access. It is never the
access of a room or of a chat session:

* A shared room folds the access of each member, so a room can still refuse
  a service that this answer says Yes to.
* A room that expands past 200 members does not narrow, and a failed read of
  the room members does not narrow either.
* A delegated run acts as the member of its parent.
* A run with no member gets no integration filter.

No field of the answer says any of these.

Fences: ``tests/unit/test_auth_me_integrations.py`` and
``tests/unit/test_auth_me_integrations_r8.py`` (R8).
"""

from __future__ import annotations

from typing import Any

from acb_auth import UserContext, get_current_user, integration_use_permission
from acb_auth.access import read_integration_rules
from acb_auth.integration_access import MANAGE, RULE_SOURCE, parse_rules
from acb_auth.permissions import matched_by
from fastapi import Depends, HTTPException
from gateway.routes.admin import integration_access as _rules_routes
from gateway.routes.admin._common import (
    _load_overrides,
    _log,
    _role_permission_map,
    _tenant_session,
    find_member,
    get_org_id,
    integration_access_allowed,
    member_rule_access,
)
from gateway.routes.admin.me import me_router

#: The reasons of a row. ``updating`` means that the cache of the resolver
#: and a fresh read do not agree yet. The answer keeps the resolver's Yes or No.
REASONS: tuple[str, ...] = (
    "admin", "on-rule", "role", "allowed-for-you",
    "inactive", "rule", "rules-unreadable", "denied-for-you", "no-role",
    "updating",
)

#: The keys of each row, and no other key.
ROW_KEYS: tuple[str, ...] = ("service", "kind", "name", "allowed", "reason")

_PREFIXES = {"mcp": "mcp-", "api": "api-"}


def _name(service: str, kind: str) -> str:
    """The service without its prefix: ``mcp-tally`` gives ``tally``."""
    prefix = _PREFIXES.get(kind, "")
    return service[len(prefix):] if prefix and service.startswith(prefix) else service


def _reason(service: str, allowed: bool, base: Any, narrowed: Any, *,
            readable: bool) -> str:
    """Why *allowed* is what it is. *base* and *narrowed* come from the shared step.

    A No follows the precedence of ``members._explain_ruled``: a refusal by a
    rule names the rule when the roles and the overrides allow the service.
    """
    perm = integration_use_permission(service)
    if narrowed.access.has(perm) != allowed:
        return "updating"
    ruled = service in narrowed.ruled or not readable
    if allowed:
        if ruled and base.has(MANAGE):
            return "admin"
        if service in narrowed.ruled:
            return "on-rule"
        source = base.decide(perm).source
        return "allowed-for-you" if source == "allow-override" else "role"
    if not base.is_active:
        return "inactive"
    if service in narrowed.refused and base.has(perm):
        return "rule"
    if narrowed.all_refused and base.has(perm):
        return "rules-unreadable"
    source = base.decide(perm).source
    if source == "deny-override":
        return "denied-for-you"
    return "no-role"


@me_router.get("/me/integrations",
               summary="May I use each integration of the org, and why (ACC-3)")
async def get_my_integrations(
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """The caller's own Yes or No for each integration, with one reason.

    The order and the shape are in the module docstring. The answer is the
    caller's solo access, never a room or a session. A shared room can still
    refuse a Yes. A room over 200 members, or a failed read of a room, does
    not narrow. A delegated run acts as its parent. A run with no member gets
    no filter. No field says these.
    """
    if not integration_access_allowed(user.organization_id):
        raise HTTPException(status_code=404, detail="Not Found")
    email = str(user.email or "").strip().lower()
    if "@" not in email or not user.access.is_active:
        raise HTTPException(status_code=403, detail="Sign in to read your integrations")

    async with _tenant_session() as db:
        org_id = await get_org_id(db, user)
        member = await find_member(db, org_id, email)
        if member is None:
            raise HTTPException(status_code=403, detail="Sign in to read your integrations")
        try:
            services = await _rules_routes.closed_list(db, org_id)
        except Exception as exc:
            _log.warning("my_integrations_list_failed", error=type(exc).__name__)
            raise HTTPException(
                status_code=503, detail="Could not read the integrations. Try again.",
            ) from exc
        role_perms = await _role_permission_map(db, member["id"])
        overrides = await _load_overrides(db, member["id"])
        rules = await read_integration_rules(db, org_id, member["id"])
    if rules.failed:
        raise HTTPException(
            status_code=503, detail="Could not read who can use each integration. Try again.",
        )

    base, narrowed = member_rule_access(
        role_perms, overrides, roles=sorted(role_perms),
        is_active=member["status"] == "active", email=member["email"], rules=rules,
    )
    names = sorted(services)
    # A guest: no grant pattern reaches any listed service. Name no server.
    if names and all(
        matched_by(base.role_granted | base.allowed, integration_use_permission(s)) is None
        for s in names
    ):
        return {"services": [], "source": RULE_SOURCE, "blocked": "no-role"}

    _, readable = parse_rules(rules.raw)
    rows = []
    for service in names:
        kind = services[service]
        allowed = user.access.has(integration_use_permission(service))
        rows.append({
            "service": service, "kind": kind, "name": _name(service, kind),
            "allowed": allowed,
            "reason": _reason(service, allowed, base, narrowed, readable=readable),
        })
    return {"services": rows, "source": RULE_SOURCE}
