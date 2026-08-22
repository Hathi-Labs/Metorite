"""``GET /auth/me`` — the caller's own identity and effective access.

Separate router (no ``/admin`` prefix, no admin permission) because every
signed-in member needs this: it is what the Control Plane filters the sidebar
and guards routes with. Asking for your own access is not an administrative
action.

It returns *resolved outcomes* — a list of allowed feature slugs and runnable
agents — rather than raw permission patterns for the client to evaluate. Two
implementations of the matching rule (one Python, one TypeScript) is one
implementation too many; the server decides and the client renders.
"""

from __future__ import annotations

from typing import Any

from acb_auth import UserContext, get_current_user
from acb_auth.permissions import CAPABILITIES
from fastapi import APIRouter, Depends

from gateway.routes.admin._common import _tenant_session, get_org_id

me_router = APIRouter(prefix="/auth", tags=["auth"])


async def _catalog_slugs(db: Any) -> list[str]:
    """Every feature slug the DATABASE knows about, in catalog order.

    ``feature_catalog`` is the registry — 130's own header says so: *"the nav-pane
    registry the admin UI renders from, so adding a pane is a seeded row, not a
    code change in the gateway AND the frontend."* ``FEATURES`` is a mirror of it
    kept in code for the places that cannot reach a database.

    Reading the catalog here is what stops a stale mirror from hiding a pane.
    ``allowed_features()`` iterates ``FEATURES`` alone, so a slug seeded by a
    migration but missing from the deployed ``acb_auth`` is invisible to the
    sidebar even for a caller holding ``*`` — the pane simply is not there, with
    no error anywhere to explain it.
    """
    from sqlalchemy import text

    rows = (await db.execute(
        text("SELECT slug FROM feature_catalog ORDER BY category, sort_order"),
    )).fetchall()
    return [r.slug for r in rows if getattr(r, "slug", None)]


def resolve_features(access: Any, catalog: list[str]) -> dict[str, list[Any]]:
    """Split every known feature into what this caller may reach and what they
    may not, naming the permission each absent one needs.

    **The union, not one or the other.** The catalog can carry a slug the code
    has never heard of (a pane added by migration); the code can carry one the
    database has not been seeded with yet (a fresh box mid-deploy). Taking
    either alone loses a real pane, and the failure looks identical both ways:
    nothing renders and nothing says why.

    **This grants nothing.** The gate is ``require_feature_router`` on each
    router, which asks ``access.has("feature:<slug>")`` directly and does not
    consult either list. Everything here is a *report* about that same
    decision — so widening the report can only stop the UI hiding a pane the
    API would already have served.

    ``denied`` is what makes the failure legible: "Projects needs
    `feature:projects`" is something a person can act on. A pane that silently
    is not there is something they can only guess at.
    """
    from acb_auth.permissions import FEATURES, feature_permission

    known: list[str] = []
    for slug in [*catalog, *FEATURES]:
        if slug and slug not in known:
            known.append(slug)

    allowed = [s for s in known if access.has(feature_permission(s))]
    denied = [
        {"slug": s, "permission": feature_permission(s)}
        for s in known
        if s not in set(allowed)
    ]
    return {"features": allowed, "features_denied": denied}


def _agent_names() -> list[str]:
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


@me_router.get("/me", summary="Current user's identity and effective access")
async def get_me(user: UserContext = Depends(get_current_user)) -> dict[str, Any]:
    access = user.access

    organization: dict[str, str] = {}
    catalog: list[str] = []
    try:
        async with _tenant_session() as db:
            # The CALLER's organization, not the deployment's. This line used to
            # report the `default` org's slug and display name to every
            # signed-in member of every tenant, so the frontend's "which org am
            # I in" — `access.organization` in `lib/access.ts`, rendered on the
            # Members header — was wrong for all but one
            # (`multi_tenancy_leak_audit.md` S1-1).
            org_id = await get_org_id(db, user)
            from sqlalchemy import text  # noqa: PLC0415

            row = (
                await db.execute(
                    text(
                        "SELECT slug, display_name FROM organization "
                        " WHERE id = CAST(:id AS uuid)"
                    ),
                    {"id": org_id},
                )
            ).mappings().first()
            if row:
                organization = {
                    "id": org_id,
                    "slug": row["slug"],
                    "display_name": row["display_name"],
                }
            catalog = await _catalog_slugs(db)
    except Exception:  # noqa: BLE001
        # An unprovisioned org must not stop a member from loading the app —
        # the access set is authoritative either way. An unreachable catalog
        # falls back to the code mirror inside `resolve_features`, which is the
        # behaviour this endpoint had before the catalog was consulted at all.
        organization = {}

    return {
        "email": user.email or "",
        # An opaque identity token for display/correlation, NOT an app_user
        # foreign key — the backend keys on email. Its id-space depends on
        # IDENTITY_CUTOVER (H6 slice 3b): OFF it is `app_user.id`, ON it is the
        # RLS-EXEMPT `user_identity.id` (a stable per-human id). A consumer that
        # joins this to `app_user.id` breaks the day the flag flips; use email.
        "user_id": user.user_id or "",
        "authenticated": bool(user.email),
        "is_active": access.is_active,
        "organization": organization,
        "roles": sorted(access.roles),
        # Legacy coarse role, still consumed by pre-migration UI checks.
        "legacy_role": user.role.value,
        # Resolved against the CATALOG unioned with the code mirror, so a pane
        # seeded by a migration is reachable even if the deployed `acb_auth`
        # predates its slug — and `features_denied` names the permission each
        # absent pane needs, so "why is Projects missing" has an answer on
        # screen instead of being a silent nothing.
        **resolve_features(access, catalog),
        "agents": [n for n in _agent_names() if access.can_run_agent(n)],
        "permissions": sorted(access.granted),
        # Resolved yes/no for every concrete capability, so the browser never
        # re-implements the wildcard rule (an owner holds "*", not the literal
        # string). `permissions` above stays the raw grant patterns for the
        # admin screens that display them. Wildcard capabilities are omitted:
        # they are answered per-target ("agents" above), not as a flat yes.
        "capabilities": [
            c for c in CAPABILITIES if "*" not in c and access.has(c)
        ],
        "denied": sorted(access.denied),
        "is_admin": access.has("admin:members:read"),
    }
