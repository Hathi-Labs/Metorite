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

import json
from typing import Any, Literal

from acb_auth import UserContext, get_current_user
from acb_auth.permissions import CAPABILITIES
from acb_common import get_logger
from fastapi import APIRouter, Body, Depends, HTTPException
from gateway.routes.admin._common import _tenant_session, get_org_id
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

me_router = APIRouter(prefix="/auth", tags=["auth"])

_log = get_logger("gateway.auth.me")


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

async def _registry_projection(db, org_id: str) -> dict[str, str | None]:
    """What the REGISTRY last said about this organization. Display only.

    ``registry_status`` (migration 177) and ``registry_trial_ends_at`` (199) are
    cached by the sign-in resolve, so the app can tell an admin *"Trial — 12
    days left"* instead of leaving them to discover their commercial state on
    the day something stops working (CP-2j).

    ⚠️ **Neither is a gate, and nothing may make one of them.** Access is the
    access set this endpoint already returns, resolved per call at the gateway.
    A second gate keyed on a CACHED status — or worse, on a cached DATE — is how
    a stale row or a clock skew locks out a customer who is paying.

    ⚠️ **Its own try/except, and that is the point.** These columns are NEWER
    than this endpoint. Folded into the organization SELECT above, a box whose
    ladder has not reached 199 raised, the caller's broad handler set
    ``organization = {}``, and every member lost their org's slug and display
    name — a banner's optional data taking out the identity beside it. R6 says
    old code meets new schema, and this is the other direction: new code meeting
    an old schema degrades to silence, which is exactly what an absent projection
    means anyway.
    """
    from sqlalchemy import text  # noqa: PLC0415

    try:
        row = (
            await db.execute(
                text(
                    "SELECT registry_status, registry_trial_ends_at "
                    "  FROM organization WHERE id = CAST(:id AS uuid)"
                ),
                {"id": org_id},
            )
        ).mappings().first()
        if not row:
            return {}
        # ⚠️ The row READ is inside the guard, not just the query. An old
        # schema can fail either way round — the SELECT can raise, or a row can
        # come back without the columns — and a guard that covered only the
        # first would still take the caller's whole organization block down.
        deadline = row["registry_trial_ends_at"]
        return {
            "registry_status": row["registry_status"],
            "trial_ends_at": (
                deadline.isoformat() if deadline is not None else None
            ),
        }
    except Exception:  # noqa: BLE001
        return {}



@me_router.get("/me", summary="Current user's identity and effective access")
async def get_me(user: UserContext = Depends(get_current_user)) -> dict[str, Any]:
    access = user.access

    # `str | None` since CP-2j: `registry_status` and `trial_ends_at` are
    # legitimately absent on a box whose resolve flag has never been on.
    organization: dict[str, str | None] = {}
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
                # CP-2j, and SEPARATE on purpose — see the helper.
                organization.update(await _registry_projection(db, org_id))
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


# ── /auth/me/shell — the member's shell layout (WS-44 NS-7) ────────────────
#
# `navigation_shell.md` §8. A preset only ARRANGES: the pins, the My Day card
# order and the order of the jobs in the command bar. It never grants. The
# shell draws every pin through `visibleSections`, so a stored pin for an app
# the member lacks, or one that is not live, draws nothing.
#
# ⚠️ Why a route of its own and not `/tasks/settings`: that router requires
# `feature:tasks`, and a guest does not hold it (§8.2). This router needs only
# a signed-in member, as `GET /auth/me` does.
#
# ⚠️ The member and the tenant come from the session only (R5,
# `user_management_contract.md` R11). The body names neither, and the model
# refuses a key it does not know.
#
# What the gateway checks is the VOCABULARY, not the content. `presets.ts`
# holds what each preset arranges, and the role's default is the client's to
# apply (`presetForRole`). So one rule lives in one place. These sets are
# mirrors, and `tests/unit/test_auth_me_shell.py::TestOneVocabulary` fails when
# a mirror drifts from the workbench.

#: The eight presets of §8.1, by id. The content is in `presets.ts`.
SHELL_PRESETS: frozenset[str] = frozenset({
    "founder", "sales-manager", "marketing-lead", "finance-manager",
    "operations-manager", "engineer", "accounts-assistant", "new-hire",
})

#: The My Day cards a preset may order. Team pulse and Out today wait on NS-5,
#: and a key for a card that is not built draws nothing.
SHELL_CARDS: frozenset[str] = frozenset({
    "needs", "today", "next", "team-pulse", "out-today",
})

#: Every pane `src/lib/nav.ts` declares by a literal `href`. A pin names one of
#: these. A preview pane (CRM) may be stored, and it draws when it goes live.
SHELL_PANES: frozenset[str] = frozenset({
    "/tasks", "/calendar", "/people/me", "/dashboard", "/email", "/whatsapp",
    "/notes", "/memory", "/artifacts", "/projects", "/crm", "/people", "/chat",
    "/workflows", "/build/apps", "/build/agents", "/approvals",
    "/settings/organization", "/settings/appearance", "/agents",
    "/integrations", "/observability",
})

#: Caps on each list. There are 22 panes, 5 cards and 11 jobs today.
MAX_PINS = 24
MAX_CARDS = 8
MAX_JOBS = 16


def _job_ids() -> frozenset[str]:
    """The command bar's job ids, from the gateway's one job list.

    Imported here, not at module load: `intent.py` brings the model client,
    and `/auth/me` must not depend on it to answer.
    """
    from gateway.routes.shell.intent import JOBS

    return frozenset(j.id for j in JOBS)


def _unique(values: list[str]) -> list[str]:
    seen: list[str] = []
    for v in values:
        if v not in seen:
            seen.append(v)
    return seen


def _known(values: list[str], vocabulary: frozenset[str], what: str) -> list[str]:
    unknown = sorted({v for v in values if v not in vocabulary})
    if unknown:
        raise ValueError(f"unknown {what}: {', '.join(unknown)[:200]}")
    return _unique(values)


class ShellPrefs(BaseModel):
    """What `PUT /auth/me/shell` accepts. Each field is optional.

    ``answered`` is ``"skipped"`` when the member chose "Skip for now". That
    is a choice, and the shell does not ask again. ``None`` means never asked.
    ``pins`` of ``None`` means "the preset's pins". An empty list means none.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    preset: str | None = None
    answered: Literal["answered", "skipped"] | None = None
    pins: list[str] | None = Field(default=None, max_length=MAX_PINS)
    card_order: list[str] | None = Field(
        default=None, alias="cardOrder", max_length=MAX_CARDS)
    new_order: list[str] | None = Field(
        default=None, alias="newOrder", max_length=MAX_JOBS)

    @field_validator("preset")
    @classmethod
    def _preset(cls, v: str | None) -> str | None:
        if v is not None and v not in SHELL_PRESETS:
            raise ValueError(f"unknown preset: {v[:40]}")
        return v

    @field_validator("pins")
    @classmethod
    def _pins(cls, v: list[str] | None) -> list[str] | None:
        return None if v is None else _known(v, SHELL_PANES, "pane")

    @field_validator("card_order")
    @classmethod
    def _cards(cls, v: list[str] | None) -> list[str] | None:
        return None if v is None else _known(v, SHELL_CARDS, "card")

    @field_validator("new_order")
    @classmethod
    def _jobs(cls, v: list[str] | None) -> list[str] | None:
        return None if v is None else _known(v, _job_ids(), "job")


#: The answer for a member with no stored layout. The client applies the
#: role's preset (`presetForRole` in `presets.ts`), so the gateway holds no
#: second copy of that rule.
EMPTY_SHELL: dict[str, Any] = {
    "preset": None, "answered": None, "pins": None,
    "cardOrder": None, "newOrder": None,
}


def stored_shell(raw: Any) -> dict[str, Any]:
    """A stored value, as the route answers it. Lenient on purpose.

    A pane leaves `nav.ts`, or a job is renamed, after a member saved it. A
    read must still answer, so this drops what it does not know rather than
    refuse the whole layout. The write is the strict half (`ShellPrefs`).
    """
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return dict(EMPTY_SHELL)
    if not isinstance(raw, dict):
        return dict(EMPTY_SHELL)

    def _list(key: str, vocabulary: frozenset[str], cap: int) -> list[str] | None:
        v = raw.get(key)
        if not isinstance(v, list):
            return None
        return _unique([x for x in v if isinstance(x, str) and x in vocabulary])[:cap]

    preset = raw.get("preset")
    answered = raw.get("answered")
    return {
        "preset": preset if preset in SHELL_PRESETS else None,
        "answered": answered if answered in ("answered", "skipped") else None,
        "pins": _list("pins", SHELL_PANES, MAX_PINS),
        "cardOrder": _list("cardOrder", SHELL_CARDS, MAX_CARDS),
        "newOrder": _list("newOrder", _job_ids(), MAX_JOBS),
    }


def _member(user: UserContext) -> str:
    """The session's address, the same key `/tasks/settings` writes under."""
    return getattr(user, "email", None) or ""


#: The row of this member in the BOUND tenant. The tenant predicate is beside
#: RLS, not instead of it: an owner role that bypasses RLS still reads one row.
_READ_SHELL_SQL = (
    "SELECT shell_prefs FROM user_settings "
    "WHERE organization_id = {org} AND user_id = :uid"
)
_RESET_SHELL_SQL = (
    "UPDATE user_settings SET shell_prefs = NULL, updated_at = now() "
    "WHERE organization_id = {org} AND user_id = :uid"
)


async def _read_shell(db: Any, uid: str) -> Any:
    from gateway.routes.tasks.settings import _BOUND_ORG
    from sqlalchemy import text

    return (await db.execute(
        text(_READ_SHELL_SQL.format(org=_BOUND_ORG)), {"uid": uid},
    )).first()


async def _write_shell(db: Any, uid: str, prefs: dict[str, Any] | None) -> None:
    """Store the layout, or reset it with ``None``.

    ⚠️ A member with no row yet gets one here, and `/tasks/settings` reads "no
    row" as "seed the calendar from the People schedule" (D-PC-16). So the
    new row carries that seed, and the calendar opens as it did before. An
    existing row changes in `shell_prefs` only.
    """
    from gateway.routes.tasks.settings import (
        _BOUND_ORG,
        _seed_from_work_schedule,
        upsert_settings_sql,
    )
    from sqlalchemy import text

    if prefs is None:
        await db.execute(text(_RESET_SHELL_SQL.format(org=_BOUND_ORG)), {"uid": uid})
        return
    params: dict[str, Any] = {"uid": uid, "shell_prefs": json.dumps(prefs)}
    if await _read_shell(db, uid) is None:
        params.update(await _seed_from_work_schedule(db, uid))
    cols = [k for k in params if k != "uid"]

    # The space before the cast matters (`test_sql_bindparam_jsonb_cast.py`).
    def _ph(k: str) -> str:
        return ":shell_prefs ::jsonb" if k == "shell_prefs" else f":{k}"

    await db.execute(
        text(upsert_settings_sql(cols, _ph, update=["shell_prefs"])), params)


@me_router.get("/me/shell", summary="The caller's shell layout (NS-7)")
async def get_my_shell(user: UserContext = Depends(get_current_user)) -> dict[str, Any]:
    """The stored layout, or all-null for a member who was never asked.

    A failed read answers 503, never an empty layout. All-null means "ask the
    first sign-in question", and a database fault must not ask it again.
    """
    uid = _member(user)
    if not uid:
        return dict(EMPTY_SHELL)
    try:
        async with _tenant_session() as db:
            row = await _read_shell(db, uid)
    except Exception as exc:
        _log.warning("auth.shell.read_failed", error=str(exc)[:160])
        raise HTTPException(status_code=503, detail="unavailable") from exc
    return stored_shell(row[0]) if row is not None else dict(EMPTY_SHELL)


@me_router.put("/me/shell", summary="Save or reset the caller's shell layout (NS-7)")
async def put_my_shell(
    body: dict[str, Any] | None = Body(default=None),
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Save the layout. A body of ``null`` resets the member to the preset.

    It needs a signed-in member and no feature, so a guest can pin an app.
    """
    uid = _member(user)
    if not uid:
        raise HTTPException(status_code=403, detail="Sign in to save a layout")
    prefs: dict[str, Any] | None = None
    if body is not None:
        try:
            model = ShellPrefs.model_validate(body)
        except ValidationError as exc:
            raise HTTPException(
                status_code=422,
                detail=exc.errors(include_url=False, include_context=False),
            ) from exc
        if model.answered == "answered" and model.preset is None:
            raise HTTPException(status_code=422, detail="An answer names its preset")
        prefs = model.model_dump(by_alias=True)
    try:
        async with _tenant_session() as db:
            await _write_shell(db, uid, prefs)
    except Exception as exc:
        _log.warning("auth.shell.write_failed", error=str(exc)[:160])
        raise HTTPException(status_code=503, detail="unavailable") from exc
    return stored_shell(prefs) if prefs is not None else dict(EMPTY_SHELL)
