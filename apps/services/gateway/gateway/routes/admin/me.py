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
import re
from datetime import UTC, datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from acb_auth import UserContext, get_current_user
from acb_auth.permissions import CAPABILITIES
from acb_common import get_logger
from acb_common.app_manifests.shell_manifest import load_vocabulary
from acb_common.guide_flags import guide_allowed
from fastapi import APIRouter, Body, Depends, HTTPException
from gateway.routes.admin._common import _tenant_session, get_org_id
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    ValidationInfo,
    field_validator,
)

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
# apply (`presetForRole`). So one rule lives in one place. These sets come from
# the shell manifests, and `tests/unit/test_auth_me_shell.py::TestOneVocabulary`
# fails when the workbench drifts from them.

#: The names come from the shell manifests (`load_vocabulary`, WS-PKG PKG-2).
#: The eight presets of §8.1 are by id, and the content is in `presets.ts`.
#: The cards are the My Day cards a preset may order. Team pulse and Out today
#: wait on NS-5, and a key for a card that is not built draws nothing.
#: The panes are every `href` that `src/lib/nav.ts` declares. A star
#: (``pins``) and a ``known`` entry each name one of these. A preview pane
#: (CRM) may be stored, and it draws when it goes live.
_VOCABULARY = load_vocabulary()
SHELL_PRESETS: frozenset[str] = frozenset(_VOCABULARY.presets)
SHELL_CARDS: frozenset[str] = frozenset(_VOCABULARY.cards)
SHELL_PANES: frozenset[str] = frozenset(_VOCABULARY.panes)

#: Caps on each list. There are 21 panes, 5 cards and 23 jobs today.
MAX_PINS = 24
MAX_CARDS = 8
MAX_JOBS = 16

#: The longest ISO 8601 timestamp a mark takes (WS-62 CF-2, WS-OB GD-5a).
MAX_STAMP = 40


def _stamp(v: Any) -> str | None:
    """An ISO 8601 timestamp, or None. Anything else is not a mark.

    ``chat_first_message_at`` (`chat_first_surface.md` §4.7, D-CF-7) is the
    time of the member's first message to the assistant, or the time they
    dismissed the nudge. The server keeps the time, and never reads it as a
    gate: the nudge shows while it is null, and never again after.

    ``tour_seen_at`` (WS-OB GD-5a, `assistant_guide.md` §5.3) is the time the
    member first started the tour. It is a record for GD-14, and no gate.
    """
    if not isinstance(v, str) or not v or len(v) > MAX_STAMP:
        return None
    try:
        datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None
    return v


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
    ⚠️ ``pins`` holds the STARRED apps since the star model (owner,
    2026-10-11, `navigation_shell.md` §3.2). The field keeps its NS-7 name, so
    no stored row changes (R6). ``None`` means the member never chose, and
    every held app shows. An empty list means that no app is starred.

    ``known`` holds the hrefs the member could see when they last saved. An
    app that is not in it is new to them, and the shell shows it starred
    (`starredHrefs` in `presets.ts`). A row with no ``known`` is a row that
    NS-7 saved, when ``pins`` were shortcuts on top of the full sidebar, so
    the shell shows EVERY held app for it. The member's first save writes
    ``known``, and the stars filter from then on.

    ``chat_first_message_at`` (WS-62 CF-2, `chat_first_surface.md` §4.7) is
    the ISO time of the member's first message to the assistant, or of the
    nudge's dismissal. While it is null the nudge shows. ⚠️ A body that does
    NOT name it keeps the stored value (``_write_shell``): a layout save is
    about the layout, and the welcome dialog's one write must not bring the
    nudge back. A body that names it null clears it on purpose.

    ``tour_seen_at`` (WS-OB GD-5a) follows the same rules, and the first
    start stays: a stored time is never overwritten (``stamp-first``). The client sends it
    alone when the member starts the tour (``markTourSeen``). A body that
    names it with a time stores the SERVER's time (``_server_stamp``), so a
    wrong device clock never reaches the record.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    preset: str | None = None
    answered: Literal["answered", "skipped"] | None = None
    pins: list[str] | None = Field(default=None, max_length=MAX_PINS)
    known: list[str] | None = Field(default=None, max_length=MAX_PINS)
    card_order: list[str] | None = Field(
        default=None, alias="cardOrder", max_length=MAX_CARDS)
    new_order: list[str] | None = Field(
        default=None, alias="newOrder", max_length=MAX_JOBS)
    chat_first_message_at: str | None = Field(default=None, max_length=MAX_STAMP)
    tour_seen_at: str | None = Field(default=None, max_length=MAX_STAMP)
    buddy_hello_at: str | None = Field(default=None, max_length=MAX_STAMP)

    @field_validator("preset")
    @classmethod
    def _preset(cls, v: str | None) -> str | None:
        if v is not None and v not in SHELL_PRESETS:
            raise ValueError(f"unknown preset: {v[:40]}")
        return v

    @field_validator("chat_first_message_at", "tour_seen_at", "buddy_hello_at")
    @classmethod
    def _a_mark(cls, v: str | None, info: ValidationInfo) -> str | None:
        if v is not None and _stamp(v) is None:
            raise ValueError(f"{info.field_name} is an ISO 8601 timestamp")
        return v

    @field_validator("pins", "known")
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
    "preset": None, "answered": None, "pins": None, "known": None,
    "cardOrder": None, "newOrder": None, "chat_first_message_at": None,
    "tour_seen_at": None, "buddy_hello_at": None,
}

#: The marks of the layout. A save keeps each one that the body omits.
KEPT_KEYS: tuple[str, ...] = ("chat_first_message_at", "tour_seen_at", "buddy_hello_at")

#: How a write changes an existing row's ``shell_prefs`` (WS-62 CF-2). Each
#: is ONE statement, so two writes never race through a read-then-write
#: (review of #1206). ``CAST``, not ``::``, beside a bind
#: (``test_sql_bindparam_jsonb_cast.py``).
#:
#: ``layout``: the body is the whole layout, marks included. It replaces.
_SET_LAYOUT = "EXCLUDED.shell_prefs"


def _set_layout_keep(keys: tuple[str, ...]) -> str:
    """``layout-keep``: the body is the whole layout and omits the marks in
    *keys*. Each stored mark survives, in SQL. A key is a name of
    :data:`KEPT_KEYS`, never request input, so it may stand in the text."""
    expr = "EXCLUDED.shell_prefs"
    for key in keys:
        if key not in KEPT_KEYS:
            raise ValueError(f"not a mark: {key}")
        expr = (
            f"jsonb_set({expr}, ARRAY['{key}'], "
            f"COALESCE(user_settings.shell_prefs->'{key}', CAST('null' AS jsonb)), true)"
        )
    return expr


#: The keep of a save that omits every mark, which is each layout save.
_SET_LAYOUT_KEEP = _set_layout_keep(KEPT_KEYS)
#: ``stamp``: the body is one mark or more, alone. It merges into the
#: stored layout.
_SET_STAMP = (
    "CASE WHEN jsonb_typeof(user_settings.shell_prefs) = 'object' "
    "THEN user_settings.shell_prefs ELSE CAST('{}' AS jsonb) END || EXCLUDED.shell_prefs"
)
#: ``stamp-first``: a ``stamp`` that also keeps a stored ``tour_seen_at`` and
#: a stored ``buddy_hello_at``. The first start of the tour stays on the
#: server (WS-OB GD-5a), and so does the first hello (WS-BUD BUD-1, D177).
#: Only these two marks keep their first value. The nudge mark still takes
#: the new one. ``NULLIF`` turns a stored JSON null into "none", so the new
#: time lands then. A body that does not name a mark leaves it alone: the
#: merge keeps the stored value, and ``EXCLUDED`` holds no such key.
_FIRST_MARKS = ("tour_seen_at", "buddy_hello_at")
_SET_STAMP_FIRST = _SET_STAMP
for _k in _FIRST_MARKS:
    # The last COALESCE arm keeps a body that omits the mark from writing a
    # SQL NULL, which would void the whole value.
    _SET_STAMP_FIRST = (
        f"jsonb_set({_SET_STAMP_FIRST}, ARRAY['{_k}'], "
        f"COALESCE(NULLIF(user_settings.shell_prefs->'{_k}', CAST('null' AS jsonb)), "
        f"EXCLUDED.shell_prefs->'{_k}', CAST('null' AS jsonb)), true)"
    )
_SET_BY_MODE = {
    "layout": _SET_LAYOUT,
    "layout-keep": _SET_LAYOUT_KEEP,
    "stamp": _SET_STAMP,
    "stamp-first": _SET_STAMP_FIRST,
}

#: Fields of the ANSWER that are facts of the server, not of the layout. A
#: tab on an older bundle spreads the whole answer back into its save, so the
#: PUT drops these before it validates, and never stores them.
READ_ONLY_ANSWER_KEYS: tuple[str, ...] = ("guide",)


def _server_stamp() -> str:
    """Now, in UTC, as the ISO 8601 text that ``_stamp`` reads back."""
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


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
        "known": _list("known", SHELL_PANES, MAX_PINS),
        "cardOrder": _list("cardOrder", SHELL_CARDS, MAX_CARDS),
        "newOrder": _list("newOrder", _job_ids(), MAX_JOBS),
        "chat_first_message_at": _stamp(raw.get("chat_first_message_at")),
        "tour_seen_at": _stamp(raw.get("tour_seen_at")),
        "buddy_hello_at": _stamp(raw.get("buddy_hello_at")),
    }


def _answer(layout: dict[str, Any], user: UserContext) -> dict[str, Any]:
    """The layout, and ``guide``: true when ``GUIDE_ORGS`` holds the
    session's org (WS-OB GD-5a). The org comes from the session, never from
    the request (R5). ``guide`` is a fact of the server, not of the layout,
    so ``ShellPrefs`` refuses it in a body."""
    org = getattr(user, "organization_id", None)
    return {**layout, "guide": guide_allowed(str(org) if org else None)}


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


async def _write_shell(
    db: Any, uid: str, prefs: dict[str, Any] | None, mode: str = "layout",
    keep: tuple[str, ...] = KEPT_KEYS,
) -> Any:
    """Store the layout, or reset it with ``None``. Returns the stored column.

    ⚠️ A member with no row yet gets one here, and `/tasks/settings` reads "no
    row" as "seed the calendar from the People schedule" (D-PC-16). So the
    new row carries that seed, and the calendar opens as it did before. An
    existing row changes in `shell_prefs` only.

    ``mode`` picks the one-statement expression for an existing row
    (``_SET_BY_MODE``). The read before the write decides the seed only. It
    never carries a value into the write, so a concurrent stamp and a star
    save cannot write each other over (review of #1206). ``keep`` names the
    marks that a ``layout-keep`` write keeps: the ones the body omitted.
    """
    from gateway.routes.tasks.settings import (
        _BOUND_ORG,
        _seed_from_work_schedule,
        upsert_settings_sql,
    )
    from sqlalchemy import text

    if prefs is None:
        await db.execute(text(_RESET_SHELL_SQL.format(org=_BOUND_ORG)), {"uid": uid})
        return None
    params: dict[str, Any] = {"uid": uid, "shell_prefs": json.dumps(prefs)}
    if await _read_shell(db, uid) is None:
        params.update(await _seed_from_work_schedule(db, uid))
    cols = [k for k in params if k != "uid"]

    # The space before the cast matters (`test_sql_bindparam_jsonb_cast.py`).
    def _ph(k: str) -> str:
        return ":shell_prefs ::jsonb" if k == "shell_prefs" else f":{k}"

    merge = _set_layout_keep(keep) if mode == "layout-keep" else _SET_BY_MODE[mode]
    sql = upsert_settings_sql(cols, _ph, update=["shell_prefs"], merge={"shell_prefs": merge})
    row = (await db.execute(text(f"{sql} RETURNING shell_prefs"), params)).first()
    return row[0] if row is not None else prefs


@me_router.get("/me/shell", summary="The caller's shell layout (NS-7)")
async def get_my_shell(user: UserContext = Depends(get_current_user)) -> dict[str, Any]:
    """The stored layout, or all-null for a member who was never asked.

    A failed read answers 503, never an empty layout. All-null means "ask the
    first sign-in question", and a database fault must not ask it again.
    The answer also carries ``guide`` (``_answer``, WS-OB GD-5a).
    """
    uid = _member(user)
    if not uid:
        return _answer(dict(EMPTY_SHELL), user)
    try:
        async with _tenant_session() as db:
            row = await _read_shell(db, uid)
    except Exception as exc:
        _log.warning("auth.shell.read_failed", error=str(exc)[:160])
        raise HTTPException(status_code=503, detail="unavailable") from exc
    return _answer(stored_shell(row[0]) if row is not None else dict(EMPTY_SHELL), user)


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
    mode = "layout"
    keep: tuple[str, ...] = ()
    if body is not None:
        # An old tab sends back the answer it read, ``guide`` included.
        body = {k: v for k, v in body.items() if k not in READ_ONLY_ANSWER_KEYS}
        # The hello route is the ONLY writer of ``buddy_hello_at`` (BUD-1).
        # A client PUT never claims it, so it never blocks the hello.
        if "buddy_hello_at" in body:
            body.pop("buddy_hello_at")
            if not body:
                # The mark was all the body held: write nothing, answer the read.
                return await get_my_shell(user)
        try:
            model = ShellPrefs.model_validate(body)
        except ValidationError as exc:
            raise HTTPException(
                status_code=422,
                detail=exc.errors(include_url=False, include_context=False),
            ) from exc
        if model.answered == "answered" and model.preset is None:
            raise HTTPException(status_code=422, detail="An answer names its preset")
        named = model.model_fields_set
        if named and named <= set(KEPT_KEYS):
            # Marks alone (`markChatFirstMessage`, `markTourSeen`): merge
            # them, keep the rest.
            mode = "stamp"
            prefs = {k: getattr(model, k) for k in KEPT_KEYS if k in named}
        else:
            prefs = model.model_dump(by_alias=True)
            # A mark the body did not name keeps its stored value (CF-2).
            # ``tour_seen_at`` is kept even when named: an old tab copies the
            # whole answer back, so a layout save never writes the tour mark.
            # Only the key-only stamp sets it (WS-OB GD-5a).
            keep = tuple(k for k in KEPT_KEYS if k not in named or k in _FIRST_MARKS)
            for k in _FIRST_MARKS:
                prefs[k] = None
            mode = "layout-keep"
        # GD-5a: a first mark is the SERVER's time, never the device's.
        stamped = False
        for k in _FIRST_MARKS:
            if prefs.get(k) is not None:
                prefs[k] = _server_stamp()
                stamped = True
        # The first time stays: a stored time wins over this one.
        if stamped and mode == "stamp":
            mode = "stamp-first"
    try:
        async with _tenant_session() as db:
            stored = await _write_shell(db, uid, prefs, mode, keep)
    except Exception as exc:
        _log.warning("auth.shell.write_failed", error=str(exc)[:160])
        raise HTTPException(status_code=503, detail="unavailable") from exc
    return _answer(stored_shell(stored) if stored is not None else dict(EMPTY_SHELL), user)


# ── /auth/me/zone — the member's time zone (RX-7) ──────────────────────────
#
# `calendar_events_reminders.md` §5.6 (RX-7). The gateway reads a member's day
# from `user_settings.timezone`: the morning summary, "tomorrow at 5" in chat,
# and the window of My Day. The shell saves the browser's zone once (RX-5,
# `lib/shell/saveZone.ts`), and `/tasks/settings` needs `feature:tasks`. A
# member without it could not save a zone, so the summary used UTC for them.
#
# This route needs only a signed-in member, as `/auth/me/shell` does. It reads
# and writes the member's OWN row in the session's tenant. The write is the
# one write of the settings row (`write_member_settings`), so no second writer
# exists. The member and the tenant come from the session only (R5). The body
# names neither, and the model refuses a key it does not know.
#
# Fence: `tests/unit/test_auth_me_zone.py`.

#: The longest zone name the route takes. The longest IANA name is under 40.
MAX_ZONE = 64

_READ_ZONE_SQL = (
    "SELECT timezone FROM user_settings "
    "WHERE organization_id = {org} AND user_id = :uid"
)


def valid_zone(value: Any) -> str | None:
    """``value`` when it is an IANA zone name, else None."""
    if not isinstance(value, str) or not value or len(value) > MAX_ZONE:
        return None
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        # OSError: "America" is a directory of the zone database, not a zone.
        return None
    return value


class ZoneIn(BaseModel):
    """What `PUT /auth/me/zone` accepts: one IANA zone name."""

    model_config = ConfigDict(extra="forbid")

    timezone: str = Field(max_length=MAX_ZONE)

    @field_validator("timezone")
    @classmethod
    def _zone(cls, v: str) -> str:
        if valid_zone(v) is None:
            raise ValueError("timezone is an IANA zone name, such as Asia/Kolkata")
        return v


@me_router.get("/me/zone", summary="The caller's saved time zone (RX-7)")
async def get_my_zone(user: UserContext = Depends(get_current_user)) -> dict[str, str]:
    """The saved zone. A member with no row reads ``UTC``, the column default,
    as `GET /tasks/settings` does. A failed read answers 503, never a zone."""
    from gateway.routes.tasks.settings import _BOUND_ORG
    from sqlalchemy import text

    uid = _member(user)
    if not uid:
        raise HTTPException(status_code=403, detail="Sign in to read your time zone")
    try:
        async with _tenant_session() as db:
            row = (await db.execute(
                text(_READ_ZONE_SQL.format(org=_BOUND_ORG)), {"uid": uid},
            )).first()
    except Exception as exc:
        _log.warning("auth.zone.read_failed", error=str(exc)[:160])
        raise HTTPException(status_code=503, detail="unavailable") from exc
    return {"timezone": valid_zone(row[0] if row is not None else None) or "UTC"}


@me_router.put("/me/zone", summary="Save the caller's time zone (RX-7)")
async def put_my_zone(
    body: ZoneIn, user: UserContext = Depends(get_current_user),
) -> dict[str, str]:
    """Save the zone in the member's own settings row. It needs a signed-in
    member and no app feature."""
    from gateway.routes.tasks.settings import write_member_settings

    uid = _member(user)
    if not uid:
        raise HTTPException(status_code=403, detail="Sign in to save your time zone")
    try:
        await write_member_settings(uid, {"timezone": body.timezone})
    except Exception as exc:
        _log.warning("auth.zone.write_failed", error=str(exc)[:160])
        raise HTTPException(status_code=503, detail="unavailable") from exc
    return {"timezone": body.timezone}


# ── /auth/me/appearance — the member's mode, density and accent (PKG-20) ────
#
# `package_host.md` §9.5. Mode, density and accent lived in the browser only
# (`lib/theme/storage.ts`), so a second device opened in the defaults. This
# route keeps one copy for each member in each organization, in the column
# `user_settings.appearance`. The workbench reads it once after the first
# paint (`lib/theme/syncAppearance.ts`), and writes it only on a member's
# change. The Design choice (`cc-look`) is not part of the record.
#
# **Dark.** Both routes check `package_host_allowed` FIRST, as `GET
# /shell/manifest` does, and before the sign-in check. An org outside
# `PACKAGE_HOST_ORGS` (the default, empty) gets 404 and no session opens.
#
# It needs only a signed-in member, as `/auth/me/zone` does. It reads and
# writes the member's OWN row in the session's tenant. The write is the one
# write of the settings row (`write_member_settings`), so no second writer
# exists. The member and the tenant come from the session only (R5). The body
# names neither, and the model refuses a key it does not know. The SERVER
# stamps `updated_at`, never the client. The last write wins.
#
# Fences: `tests/unit/test_auth_me_appearance.py` (with the accent sample
# list that the TypeScript test reads too) and
# `tests/unit/test_auth_me_appearance_r8.py`.

#: The colour modes. next-themes runs with `enableSystem={false}`.
APPEARANCE_MODES: tuple[str, ...] = ("dark", "light")

#: The densities of `lib/theme/types.ts` `DENSITY_SCALE`. Null follows the org.
APPEARANCE_DENSITIES: tuple[str, ...] = ("compact", "default", "comfortable")

#: The longest accent, after the trim. The TypeScript rule has the same cap.
MAX_ACCENT = 64

#: The characters of `\s` and of `String.prototype.trim` in JavaScript. Python's
#: own `\s` and `str.strip()` read a different set, so the port names them.
_JS_SPACE = (
    "\t\n\v\f\r \u00a0\u1680"
    + "".join(chr(c) for c in range(0x2000, 0x200B))
    + "\u2028\u2029\u202f\u205f\u3000\ufeff"
)

#: A port of `SAFE_COLOR` in `workbench/control_plane/src/lib/theme/css.ts`.
#: `re.ASCII` keeps `[a-z]` with IGNORECASE to the ASCII letters, as the
#: JavaScript rule without the `u` flag does. The character class spells the
#: JavaScript `\s` set. `fullmatch` stands for `^...$`. The fence that the two
#: rules agree is the shared sample list `tests/fixtures/accent_samples.json`.
SAFE_COLOR = re.compile(
    "(#[0-9a-f]{3,8}|(rgb|hsl|oklch|lab|lch)a?\\([0-9a-z%.,/"
    + _JS_SPACE
    + "+-]*\\)|[a-z]{3,20})",
    re.IGNORECASE | re.ASCII,
)


def is_safe_color(value: Any) -> bool:
    """The Python half of `isSafeColor` in `lib/theme/css.ts`."""
    if not isinstance(value, str):
        return False
    v = value.strip(_JS_SPACE)
    return 0 < len(v) <= MAX_ACCENT and SAFE_COLOR.fullmatch(v) is not None


#: The answer for a member with no server copy.
EMPTY_APPEARANCE: dict[str, Any] = {
    "mode": None, "density": None, "accent": None, "updated_at": None,
}

_READ_APPEARANCE_SQL = (
    "SELECT appearance FROM user_settings "
    "WHERE organization_id = {org} AND user_id = :uid"
)


class AppearanceIn(BaseModel):
    """What `PUT /auth/me/appearance` accepts: the whole record, less the stamp."""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["dark", "light"]
    density: Literal["compact", "default", "comfortable"] | None = None
    accent: str | None = None

    @field_validator("accent")
    @classmethod
    def _accent(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if not is_safe_color(v):
            raise ValueError("accent is a colour literal, such as #e11d48")
        return v.strip(_JS_SPACE)


def stored_appearance(raw: Any) -> dict[str, Any]:
    """A stored value, as the route answers it. Lenient on purpose: a field
    that is not valid reads as null, and the other fields still answer."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return dict(EMPTY_APPEARANCE)
    if not isinstance(raw, dict):
        return dict(EMPTY_APPEARANCE)
    mode = raw.get("mode")
    density = raw.get("density")
    accent = raw.get("accent")
    return {
        "mode": mode if mode in APPEARANCE_MODES else None,
        "density": density if density in APPEARANCE_DENSITIES else None,
        "accent": accent.strip(_JS_SPACE) if is_safe_color(accent) else None,
        "updated_at": _stamp(raw.get("updated_at")),
    }


@me_router.get("/me/appearance", summary="The caller's saved appearance (PKG-20)")
async def get_my_appearance(user: UserContext = Depends(get_current_user)) -> dict[str, Any]:
    """The saved record, or all-null for a member with no server copy. A
    failed read answers 503, never a default, so the browser changes nothing."""
    from gateway.package_host.flags import package_host_allowed
    from gateway.routes.tasks.settings import _BOUND_ORG
    from sqlalchemy import text

    if not package_host_allowed(user.organization_id):
        raise HTTPException(status_code=404, detail="Not found")
    uid = _member(user)
    if not uid:
        raise HTTPException(status_code=403, detail="Sign in to read your appearance")
    try:
        async with _tenant_session() as db:
            row = (await db.execute(
                text(_READ_APPEARANCE_SQL.format(org=_BOUND_ORG)), {"uid": uid},
            )).first()
    except Exception as exc:
        _log.warning("auth.appearance.read_failed", error=str(exc)[:160])
        raise HTTPException(status_code=503, detail="unavailable") from exc
    return stored_appearance(row[0] if row is not None else None)


@me_router.put("/me/appearance", summary="Save the caller's appearance (PKG-20)")
async def put_my_appearance(
    body: AppearanceIn, user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Save the whole record in the member's own settings row. It needs a
    signed-in member and no app feature. The server stamps ``updated_at``."""
    from gateway.package_host.flags import package_host_allowed
    from gateway.routes.tasks.settings import write_member_settings

    if not package_host_allowed(user.organization_id):
        raise HTTPException(status_code=404, detail="Not found")
    uid = _member(user)
    if not uid:
        raise HTTPException(status_code=403, detail="Sign in to save your appearance")
    record = {**body.model_dump(), "updated_at": _server_stamp()}
    try:
        await write_member_settings(uid, {"appearance": record})
    except Exception as exc:
        _log.warning("auth.appearance.write_failed", error=str(exc)[:160])
        raise HTTPException(status_code=503, detail="unavailable") from exc
    return record
