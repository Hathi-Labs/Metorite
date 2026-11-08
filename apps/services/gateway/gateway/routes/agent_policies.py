"""``/agent-policies`` — one policy store for agent work (WS-MCP SW-94).

Spec: ``project-docs/specs/agent_coordination.md`` §3.11.5 ("One policy store")
and the slice SW-94 of §5.11 (D186.8).

A policy is a row of ``pm_agent_policies`` (migration 371). It holds a scope
(``organization``, ``project``, ``lane``, ``member`` or ``box``), a risk tier or
``any``, a key from :data:`REGISTRY`, a value, the author and a version. Each
save adds a row, as guidance does (SW-31). The trigger
``pm_agent_policies_guard`` refuses an UPDATE.

**Two readers.**

* :func:`policy_for` resolves a TASK key: the task's project, each parent, the
  organization, then the shipped default. The walk is
  :func:`agent_guidance.project_chains`, the one project-tree walk of SW-31a.
  This module adds no walk of its own. At each level a row of the task's tier
  wins over an ``any`` row. A task with no tier (NULL) reads ``any`` rows only,
  because a NULL tier is not LOW.
* :func:`policy_for_actor` resolves an ACTOR key: the box, the member, the
  lane, the organization, then the default. The edge reads it at token
  lookup, where no task exists. An actor key takes no tier.

**The master flag is the ceiling.** A key with ordered modes names the
``Settings`` field of its master flag, a map from organization to mode
(``org=mode`` pairs, or ``*=mode``). The flag stays the default of the org.
A policy may choose a mode at or below the flag and never above it, so flag
``warn`` with policy ``refuse`` gives ``warn``. A key behind an ``*_ORGS`` gate
gives its default while the gate does not name the org. A policy does not
grant. It only sets how strict a warning or a refusal is (§3.11 rule 5).

**One route,** dark behind ``AGENT_POLICY_ORGS`` (404 while the org is not
listed), then ``feature:projects``, as the guidance router does:
``POST /agent-policies`` saves a new version of one (scope, tier, key).

**Who writes** (SW-94):

* ``admin:settings:manage`` (``core.ORG_VOCABULARY_WRITE``) writes any key at
  any scope. Each key on the auth path needs it, except as the next two say.
* A member writes ``agents_paused`` at the scope kind ``member`` for self only,
  and only the value true. Another member gets 403. A resume (false) and a
  clear by a member get 403, so a member cannot lift a pause that an admin
  holds.
* A project lead writes a key that is not on the auth path, for the lead's own
  project. The floor is :func:`authority.guidance_write_refusal`, the write
  floor of guidance and of a grant. ``required_checks`` stays admin only
  (SW-54). An auth-path key gets 403.
* An agent connection never writes.

422 for an unknown key, for a scope kind that the key does not accept, for a
tier on a key that takes none, and for a value of the wrong type. The tenant
comes from the session (R5). The version comes from the database, with an
advisory lock on the scope and a retry on the unique index.

The Settings tab "Policies" (SW-31b) is a later slice. No caller reads a policy
yet. SW-49, SW-54, SW-18 and the later slices read through this module.

Fence: ``tests/unit/test_agent_policies_r8.py`` (R8) and
``tests/unit/test_tenant_coverage.py``.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from acb_auth import UserContext, get_current_user, require_feature_router
from acb_common import get_logger, get_settings
from acb_common.db import current_tenant
from fastapi import APIRouter, Depends, HTTPException
from gateway.db import tenant_session as _tenant_session
from gateway.routes.agent_guidance import project_chains
from gateway.routes.projects.authority import guidance_write_refusal
from gateway.routes.projects.core import (
    ORG_VOCABULARY_WRITE,
    actor,
    load_visible_project,
    resolve_visibility,
)
from pydantic import BaseModel, ConfigDict
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

_log = get_logger("gateway.agent_policies")

SCOPE_KINDS = ("organization", "project", "lane", "member", "box")
#: The task scope kinds, nearest last in this tuple. A task key accepts these.
TASK_SCOPES = ("organization", "project")
#: The risk tiers of SW-91. ``any`` is the row for each tier.
TIERS = ("LOW", "STANDARD", "HIGH")
ANY = "any"

#: A save retries this many times on a lost race for the next version.
SAVE_ATTEMPTS = 5
#: The longest item of a text list, and the most items.
LIST_ITEM_MAX = 80
LIST_MAX = 50

_TIME = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
_EMAIL = re.compile(r"^[^@\s]{1,200}@[^@\s]{1,118}$")

ADMIN_REFUSED = "Only an organization admin can change this agent policy."
AUTH_PATH_REFUSED = (
    "This agent policy is on the sign-in path, so only an organization admin can change it."
)
SELF_ONLY_REFUSED = "You can pause your own agents only."
RESUME_REFUSED = "Only an organization admin can resume paused agents."
GONE_REFUSED = "The project of this policy was not found."
SHAPE_REFUSED = "The store cannot hold this policy. Check the scope and the value."

#: The unique index of one version of a (scope, tier, key). A save retries
#: only on a unique violation of this index (migration 371).
VERSION_INDEX = "pm_agent_policies_version"
LEAD_REFUSED = "Only the project lead or an organization admin can change the agent policies of this project."
AGENT_WRITE_REFUSED = "An agent connection cannot change a policy. A member does it in Settings."
ARCHIVED_REFUSED = "This project is archived, so its agent policies cannot change."
SAVE_BUSY = "Another save of this policy was in progress. Try again."


# ── The key registry ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PolicyKey:
    """One key of the fixed list (§3.11.5).

    ``form`` is ``task`` (read by :func:`policy_for`) or ``actor`` (read by
    :func:`policy_for_actor`). ``scopes`` are the scope kinds it accepts.
    ``kind`` is the value type: ``bool``, ``int``, ``int_or_null``, ``mode``,
    ``choice``, ``text_list`` or ``time_or_null``. ``modes`` holds the values
    of ``mode`` and ``choice``, lowest first for a mode. ``auth_path`` needs
    ``admin:settings:manage`` except as the writer rules say. ``tiers`` is True
    when a row may name a risk tier. ``flag`` names the ``Settings`` field of
    the master mode map, the ceiling. ``gate`` names an ``*_ORGS`` field: off,
    the key gives its default. ``admin_only`` keeps a key that is not on the
    auth path from a lead (``required_checks``, SW-54).
    """

    name: str
    form: str
    scopes: tuple[str, ...]
    kind: str
    default: Any
    auth_path: bool = False
    tiers: bool = False
    modes: tuple[str, ...] = ()
    flag: str | None = None
    gate: str | None = None
    minimum: int | None = None
    maximum: int | None = None
    admin_only: bool = False
    #: Free text for the later Settings tab. Not a rule.
    note: str = field(default="", compare=False)


_WARN_MODES = ("off", "warn", "refuse")
_ACTOR_SCOPES = ("organization", "lane", "member", "box")
_LANE_SCOPES = ("organization", "lane")

#: The first keys of §3.11.5. The defaults are policy defaults, and none is a
#: HathiLabs constant (D186.2). A key outside this list gets 422.
REGISTRY: dict[str, PolicyKey] = {k.name: k for k in (
    PolicyKey("stages_required", "task", TASK_SCOPES, "text_list", [], tiers=True,
              note="The stages a task of a tier must report (SW-91)."),
    PolicyKey("brief_required", "task", TASK_SCOPES, "mode", "off", tiers=True,
              modes=_WARN_MODES, note="A delegation with no brief (SW-90)."),
    PolicyKey("claim_scope_mode", "task", TASK_SCOPES, "mode", "off", auth_path=True,
              modes=_WARN_MODES, flag="agent_claim_scope_modes",
              note="A claim outside the member's apps (SW-46)."),
    PolicyKey("review_cap", "task", TASK_SCOPES, "int", 5, minimum=1, maximum=1000,
              note="The items that wait on one lead (SW-49)."),
    PolicyKey("park_max_minutes", "task", TASK_SCOPES, "int_or_null", None, tiers=True,
              minimum=1, maximum=10080, note="Lowers the park cap of SW-65."),
    PolicyKey("overnight_allowed", "task", TASK_SCOPES, "bool", True, tiers=True,
              note="The tiers an overnight queue may start."),
    PolicyKey("done_on", "task", TASK_SCOPES, "choice", "merge", tiers=True,
              modes=("merge", "live"), note="Done on the merge or when live (Q14)."),
    PolicyKey("question_auto_after_minutes", "task", TASK_SCOPES, "int_or_null", None,
              auth_path=True, tiers=True, minimum=240, maximum=43200,
              gate="agent_auto_answer_orgs", note="SW-75, under its master flag."),
    PolicyKey("lane_mode", "task", TASK_SCOPES, "mode", "off", auth_path=True,
              modes=_WARN_MODES, flag="agent_lane_modes", note="A full declared lane (SW-67)."),
    PolicyKey("pr_open_to_review", "task", TASK_SCOPES, "bool", False, auth_path=True,
              tiers=True, note="An open pull request moves the task to review."),
    PolicyKey("release_cutoff", "task", TASK_SCOPES, "time_or_null", None,
              note="The release cut-off, HH:MM UTC. None by default."),
    PolicyKey("review_required", "task", TASK_SCOPES, "bool", False, tiers=True,
              note="Agent work needs review (SW-18)."),
    PolicyKey("required_checks", "task", TASK_SCOPES, "text_list", [], auth_path=True,
              tiers=True, admin_only=True, note="The required check names (SW-54)."),
    PolicyKey("budget_mode", "actor", _LANE_SCOPES, "mode", "off", auth_path=True,
              modes=("off", "warn", "stop"), flag="agent_budget_modes",
              note="Day budgets and loop guards (SW-95)."),
    PolicyKey("budget_minutes_day", "actor", _LANE_SCOPES, "int_or_null", None,
              minimum=1, maximum=1440 * 31, note="Run minutes for each day (SW-95)."),
    PolicyKey("budget_writes_day", "actor", _LANE_SCOPES, "int_or_null", None,
              minimum=1, maximum=1_000_000, note="Writes for each day (SW-95)."),
    PolicyKey("loop_guard_repeats", "actor", _LANE_SCOPES, "int_or_null", None,
              minimum=2, maximum=1000, note="The same call repeated (SW-95)."),
    PolicyKey("agents_paused", "actor", _ACTOR_SCOPES, "bool", False, auth_path=True,
              note="Pause the agents of a box, a member, a lane or the org (SW-96)."),
)}


def _check_bool(spec: PolicyKey, value: Any) -> Any:
    if not isinstance(value, bool):
        raise ValueError(f"{spec.name} takes true or false.")
    return value


def _check_int(spec: PolicyKey, value: Any) -> Any:
    if value is None and spec.kind == "int_or_null":
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{spec.name} takes a whole number.")
    if spec.minimum is not None and value < spec.minimum:
        raise ValueError(f"{spec.name} takes {spec.minimum} or more.")
    if spec.maximum is not None and value > spec.maximum:
        raise ValueError(f"{spec.name} takes {spec.maximum} or less.")
    return value


def _check_mode(spec: PolicyKey, value: Any) -> Any:
    if value not in spec.modes:
        raise ValueError(f"{spec.name} takes one of {', '.join(spec.modes)}.")
    return value


def _check_text_list(spec: PolicyKey, value: Any) -> Any:
    if not isinstance(value, list) or len(value) > LIST_MAX:
        raise ValueError(f"{spec.name} takes a list of at most {LIST_MAX} names.")
    out: list[str] = []
    for item in value:
        if (not isinstance(item, str) or not item.strip() or len(item) > LIST_ITEM_MAX
                or "\x00" in item):
            raise ValueError(
                f"Each name of {spec.name} is text of 1 to {LIST_ITEM_MAX} characters.")
        if item.strip() not in out:
            out.append(item.strip())
    return out


def _check_time(spec: PolicyKey, value: Any) -> Any:
    if value is None:
        return None
    if not isinstance(value, str) or not _TIME.match(value):
        raise ValueError(f"{spec.name} takes a time as HH:MM (UTC), or null.")
    return value


_CHECKS = {
    "bool": _check_bool, "int": _check_int, "int_or_null": _check_int,
    "mode": _check_mode, "choice": _check_mode, "text_list": _check_text_list,
    "time_or_null": _check_time,
}


#: The master flags that a key names and that are not ``Settings`` fields yet.
#: Each reads as off until its slice adds the field: SW-46
#: (``agent_claim_scope_modes``), SW-67 (``agent_lane_modes``), SW-95
#: (``agent_budget_modes``) and SW-75 (``agent_auto_answer_orgs``). The slice
#: that adds the field removes its name here. Fence:
#: ``test_agent_policies_r8.py::test_each_flag_is_a_setting_or_not_yet``.
FLAGS_NOT_YET = frozenset({
    "agent_claim_scope_modes", "agent_lane_modes", "agent_budget_modes",
    "agent_auto_answer_orgs",
})


def check_value(spec: PolicyKey, value: Any) -> Any:
    """The value of *spec* in its stored shape, or ``ValueError`` (422)."""
    return _CHECKS[spec.kind](spec, value)


# ── The flags ───────────────────────────────────────────────────────────────


def _org(organization_id: Any) -> str:
    return str(organization_id or "").strip().lower()


def flag_raw(name: str) -> str:
    """The raw value of the ``Settings`` field *name*, or ``""`` when the field
    does not exist yet (its slice adds it). Read on each call, so a flag reload
    applies a change with no restart."""
    return str(getattr(get_settings(), name, "") or "")


def _listed(raw: str, org: str) -> bool:
    listed = {p.strip().lower() for p in raw.split(",") if p.strip()}
    return bool(org) and ("*" in listed or org in listed)


def policies_allowed(organization_id: Any) -> bool:
    """True when ``AGENT_POLICY_ORGS`` holds the org, or holds ``*``. Default OFF.

    The org comes from the session or the bound tenant, never from request
    input (R5)."""
    return _listed(flag_raw("agent_policy_orgs"), _org(organization_id))


def flag_mode(name: str, organization_id: Any, modes: tuple[str, ...]) -> str:
    """The mode of the master flag *name* for the org: ``org=mode`` pairs with a
    comma between, and ``*=mode`` for each org. The org's own pair wins over
    ``*``. Unset, or a mode outside *modes*, is the lowest mode (off)."""
    org = _org(organization_id)
    star = mine = None
    for pair in flag_raw(name).split(","):
        who, sep, mode = pair.partition("=")
        who, mode = who.strip().lower(), mode.strip().lower()
        if not sep:
            continue
        if who == "*":
            star = mode
        elif org and who == org:
            mine = mode
    got = mine if mine is not None else star
    return got if got in modes else modes[0]


def under_the_flag(spec: PolicyKey, organization_id: Any, value: Any, *, found: bool) -> Any:
    """Apply the master flag of *spec* to a resolved *value*.

    A policy cannot turn on what a flag turns off. For a mode key with a flag,
    the flag is the default of the org, and the result is the lower of the
    policy and the flag. For a key behind an ``*_ORGS`` gate, a gate that does
    not name the org gives the shipped default.
    """
    if spec.gate and not _listed(flag_raw(spec.gate), _org(organization_id)):
        return spec.default
    if spec.flag and spec.kind == "mode":
        ceiling = flag_mode(spec.flag, organization_id, spec.modes)
        if not found or value not in spec.modes:
            return ceiling
        return spec.modes[min(spec.modes.index(value), spec.modes.index(ceiling))]
    return value


# ── The readers ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Resolved:
    """A resolved policy. ``source`` names the row that gave the value, or
    ``{"scope_kind": "default"}``. ``value`` is after the master flag."""

    key: str
    value: Any
    source: dict[str, Any]


def _source(row: Any) -> dict[str, Any]:
    return {
        "scope_kind": row.scope_kind,
        "project_id": str(row.project_id) if row.project_id is not None else None,
        "scope_ref": row.scope_ref,
        "tier": row.tier,
        "version": int(row.version),
        "id": str(row.id),
    }


_DEFAULT_SOURCE = {"scope_kind": "default"}

#: The newest row of each (project scope or org, tier) for one key.
_TASK_ROWS_SQL = """
SELECT DISTINCT ON (p.scope_kind, p.project_id, p.tier)
       p.id, p.scope_kind, p.project_id, p.scope_ref, p.tier, p.value,
       p.value IS NULL AS cleared, p.version
  FROM pm_agent_policies p
 WHERE p.key = :key
   AND p.tier = ANY(CAST(:tiers AS text[]))
   AND (p.scope_kind = 'organization'
        OR (p.scope_kind = 'project' AND p.project_id = ANY(CAST(:ids AS uuid[]))))
 ORDER BY p.scope_kind, p.project_id, p.tier, p.version DESC
"""

#: The newest row of each actor scope for one key, in one org.
_ACTOR_ROWS_SQL = """
SELECT DISTINCT ON (p.scope_kind, p.scope_ref)
       p.id, p.scope_kind, p.project_id, p.scope_ref, p.tier, p.value,
       p.value IS NULL AS cleared, p.version
  FROM pm_agent_policies p
 WHERE p.key = :key
   AND p.organization_id = CAST(:org AS uuid)
   AND p.tier = 'any'
   AND (p.scope_kind = 'organization'
        OR (p.scope_kind = 'box' AND p.scope_ref = CAST(:box AS text))
        OR (p.scope_kind = 'member' AND p.scope_ref = CAST(:member AS text))
        OR (p.scope_kind = 'lane' AND p.scope_ref = CAST(:lane AS text)))
 ORDER BY p.scope_kind, p.scope_ref, p.version DESC
"""


def _spec(key: str, form: str) -> PolicyKey:
    spec = REGISTRY.get(key)
    if spec is None:
        raise KeyError(f"unknown policy key {key!r}")
    if spec.form != form:
        raise ValueError(f"{key!r} is a {spec.form} key")
    return spec


def _field(row: Any, name: str) -> Any:
    return row.get(name) if isinstance(row, dict) else getattr(row, name, None)


async def policy_for(db: Any, task: Any, key: str, *, tier: str | None = None) -> Resolved:
    """Resolve the task key *key* for *task*, a row of ``pm_tasks``.

    The order: the task's project, each parent (the walk of
    :func:`agent_guidance.project_chains`), the organization, then the
    default. At each level a row of the task's tier wins over an ``any`` row.
    *tier* overrides the task's ``risk_tier`` (SW-91 adds that column). A
    NULL tier reads ``any`` rows only. A cleared row gives no value, so the
    next level applies. The master flag applies last.

    *db* is a session of the caller, bound to the tenant (R5). While
    ``AGENT_POLICY_ORGS`` does not name the org, it reads no row and gives the
    default under the flag.
    """
    spec = _spec(key, "task")
    org = _field(task, "organization_id") or current_tenant()
    if not policies_allowed(org):
        return Resolved(key, under_the_flag(spec, org, spec.default, found=False),
                        dict(_DEFAULT_SOURCE))
    tier = tier if tier is not None else _field(task, "risk_tier")
    tiers = [tier] if (spec.tiers and tier in TIERS) else []
    project_id = str(_field(task, "project_id") or "")
    chain = (await project_chains(db, [project_id])).get(project_id, []) if project_id else []
    ids = [str(r.id) for r in chain]
    rows = (await db.execute(
        text(_TASK_ROWS_SQL), {"key": key, "tiers": [*tiers, ANY], "ids": ids},
    )).fetchall()
    got = {
        (r.scope_kind, str(r.project_id) if r.project_id is not None else None, r.tier): r
        for r in rows
    }
    levels: list[tuple[str, str | None]] = [("project", pid) for pid in ids]
    levels.append(("organization", None))
    for kind, pid in levels:
        for t in (*tiers, ANY):
            row = got.get((kind, pid, t))
            if row is not None and not row.cleared:
                return Resolved(key, under_the_flag(spec, org, row.value, found=True),
                                _source(row))
    return Resolved(key, under_the_flag(spec, org, spec.default, found=False),
                    dict(_DEFAULT_SOURCE))


async def policy_for_actor(
    db: Any, org: str, member: str | None, box: str | None, lane: str | None, key: str,
) -> Resolved:
    """Resolve the actor key *key*: the box, the member, the lane, the org,
    then the default. The master flag applies last.

    *org* is the bound tenant of the caller (the token's org at token lookup),
    never request input (R5). The read names it too, so a wrong org reads no
    row. *member* is an email. *box* and *lane* are ids.
    """
    spec = _spec(key, "actor")
    if not policies_allowed(org):
        return Resolved(key, under_the_flag(spec, org, spec.default, found=False),
                        dict(_DEFAULT_SOURCE))
    params = {
        "key": key, "org": str(org),
        "box": str(box).strip().lower() if box else None,
        "member": str(member).strip().lower() if member else None,
        "lane": str(lane).strip().lower() if lane else None,
    }
    rows = (await db.execute(text(_ACTOR_ROWS_SQL), params)).fetchall()
    got = {(r.scope_kind, r.scope_ref): r for r in rows}
    for kind, ref in (("box", params["box"]), ("member", params["member"]),
                      ("lane", params["lane"]), ("organization", None)):
        if kind != "organization" and ref is None:
            continue
        row = got.get((kind, ref))
        if row is not None and not row.cleared:
            return Resolved(key, under_the_flag(spec, org, row.value, found=True), _source(row))
    return Resolved(key, under_the_flag(spec, org, spec.default, found=False),
                    dict(_DEFAULT_SOURCE))


# ── The route ───────────────────────────────────────────────────────────────


async def require_policies(user: UserContext = Depends(get_current_user)) -> None:
    """404 while ``AGENT_POLICY_ORGS`` does not hold the caller's org."""
    if not policies_allowed(getattr(user, "organization_id", None)):
        raise HTTPException(status_code=404, detail="Not Found")


#: The flag first, so a dark route answers 404 for every caller. Then the
#: feature gate of the Projects router, as ``agent_guidance.py`` does.
router = APIRouter(
    prefix="/agent-policies", tags=["agent-policies"],
    dependencies=[Depends(require_policies), require_feature_router("projects")],
)


class PolicyIn(BaseModel):
    """One save. ``scope_id`` is null for the organization, a project id, a
    lane id, a member's email or a box id. ``clear`` saves a cleared row: the
    scope then gives no value, and the next level applies."""

    model_config = ConfigDict(extra="forbid")

    key: str
    scope_kind: str
    scope_id: str | None = None
    tier: str = ANY
    value: Any = None
    clear: bool = False


def _unprocessable(detail: str) -> HTTPException:
    return HTTPException(status_code=422, detail=detail)


def _scope_ref(kind: str, scope_id: str | None) -> str | None:
    """The stored ``project_id`` or ``scope_ref`` of a scope, or 422/404."""
    raw = (scope_id or "").strip()
    if kind == "organization":
        if raw:
            raise _unprocessable("The organization scope takes no scope_id.")
        return None
    if not raw:
        raise _unprocessable(f"The scope {kind} needs a scope_id.")
    if kind == "member":
        if not _EMAIL.match(raw):
            raise _unprocessable("The member scope takes the member's email.")
        return raw.lower()
    try:
        return str(uuid.UUID(raw))
    except ValueError as exc:
        if kind == "project":
            raise HTTPException(status_code=404, detail="Project not found") from exc
        raise _unprocessable(f"The scope {kind} takes an id.") from exc


def may_write_any_policy(user: UserContext | None) -> bool:
    """``admin:settings:manage`` (:data:`core.ORG_VOCABULARY_WRITE`), the house
    rule for an org-wide write. A ``manager`` does not hold it."""
    return user is not None and user.has_permission(ORG_VOCABULARY_WRITE)


def _out(row: Any) -> dict[str, Any]:
    return {
        **_source(row),
        "key": row.key,
        "value": None if row.cleared else row.value,
        "cleared": bool(row.cleared),
        "written_by": row.written_by,
        "written_at": row.written_at.isoformat() if row.written_at is not None else None,
    }


_INSERT_SQL = """
INSERT INTO pm_agent_policies
       (scope_kind, project_id, scope_ref, tier, key, value, version, written_by)
SELECT :kind, CAST(:pid AS uuid), CAST(:ref AS text), :tier, :key, CAST(:value AS jsonb),
       COALESCE((SELECT max(p.version) FROM pm_agent_policies p
                  WHERE p.scope_kind = :kind
                    AND p.project_id IS NOT DISTINCT FROM CAST(:pid AS uuid)
                    AND p.scope_ref IS NOT DISTINCT FROM CAST(:ref AS text)
                    AND p.tier = :tier AND p.key = :key), 0) + 1,
       :me
RETURNING id, scope_kind, project_id, scope_ref, tier, key, value,
          value IS NULL AS cleared, version, written_by, written_at
"""


async def _assert_may_write(
    db: Any, user: Any, spec: PolicyKey, kind: str, ref: str | None, *, me: str, admin: bool,
    pause: bool,
) -> None:
    """The writer rules of SW-94: 404, 409 or 403, or return.

    A project must be visible (404) and not archived (409), also for an admin.
    An admin writes any key at any scope. A lead writes a key that is not on
    the auth path for the lead's own project. A member writes ``agents_paused``
    at the scope kind ``member`` for self only, and only the value true. So a
    member never resumes, also not a pause that the member wrote. A pause that
    an admin holds stays until an admin lifts it (*pause* is False for a
    value of false and for a clear).
    """
    if kind == "project":
        vis = await resolve_visibility(db, user)
        project = await load_visible_project(db, vis, ref)
        if project.archived_at is not None or project.status == "archived":
            raise HTTPException(status_code=409, detail=ARCHIVED_REFUSED)
        if admin:
            return
        if spec.auth_path or spec.admin_only:
            raise HTTPException(status_code=403, detail=AUTH_PATH_REFUSED)
        if await guidance_write_refusal(db, user, project):
            raise HTTPException(status_code=403, detail=LEAD_REFUSED)
        return
    if admin:
        return
    if not (spec.name == "agents_paused" and kind == "member"):
        raise HTTPException(
            status_code=403, detail=AUTH_PATH_REFUSED if spec.auth_path else ADMIN_REFUSED)
    if not me or ref != me:
        raise HTTPException(status_code=403, detail=SELF_ONLY_REFUSED)
    if not pause:
        raise HTTPException(status_code=403, detail=RESUME_REFUSED)


def _pg_error(exc: BaseException) -> tuple[set[str], set[str]]:
    """The SQLSTATE codes and the constraint names along the exception chain.

    It reads the driver's own fields (asyncpg and psycopg), never the message
    text, because that text also holds the SQL and the bound values."""
    codes: set[str] = set()
    names: set[str] = set()
    seen: BaseException | None = exc
    for _depth in range(8):
        if seen is None:
            break
        code = getattr(seen, "sqlstate", None) or getattr(seen, "pgcode", None)
        if isinstance(code, str):
            codes.add(code)
        name = getattr(seen, "constraint_name", None) or getattr(
            getattr(seen, "diag", None), "constraint_name", None,
        )
        if isinstance(name, str):
            names.add(name)
        seen = getattr(seen, "orig", None) or seen.__cause__
    return codes, names


def retry_or_raise(exc: BaseException) -> None:
    """Return when *exc* is a unique violation of :data:`VERSION_INDEX`: two
    saves read the same newest version, the index kept one, and the next
    attempt reads the newest version again. Else raise: 404 for a foreign key
    (the project went between the visibility read and the insert), and 422
    for each other violation."""
    codes, names = _pg_error(exc)
    if "23505" in codes and VERSION_INDEX in names:
        return
    if "23503" in codes:
        raise HTTPException(status_code=404, detail=GONE_REFUSED) from None
    raise _unprocessable(SHAPE_REFUSED) from None


@router.post("", status_code=201)
async def save_policy(
    payload: PolicyIn, user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Save a new version of one (scope, tier, key). Append only.

    422 for an unknown key, a scope kind the key does not accept, a tier on a
    key that takes none, and a bad value. 403 for an agent connection and
    for a writer outside the rules of the module docstring. 404 for a project
    the member cannot see. 409 for an archived project.
    """
    if getattr(user, "agent", None) is not None:
        raise HTTPException(status_code=403, detail=AGENT_WRITE_REFUSED)
    spec = REGISTRY.get(payload.key)
    if spec is None:
        raise _unprocessable(f"{payload.key[:64]!r} is not a policy key.")
    kind = payload.scope_kind
    if kind not in SCOPE_KINDS:
        raise _unprocessable(f"{kind[:32]!r} is not a scope kind.")
    if kind not in spec.scopes:
        raise _unprocessable(
            f"{spec.name} takes the scope kinds {', '.join(spec.scopes)}, not {kind}.")
    tier = payload.tier
    if tier != ANY and tier not in TIERS:
        raise _unprocessable(f"{tier[:16]!r} is not a risk tier.")
    if tier != ANY and not spec.tiers:
        raise _unprocessable(f"{spec.name} takes no risk tier.")
    try:
        value = None if payload.clear else check_value(spec, payload.value)
    except ValueError as exc:
        raise _unprocessable(str(exc)) from exc
    ref = _scope_ref(kind, payload.scope_id)
    me = actor(user).strip().lower()
    admin = may_write_any_policy(user)
    stored = None if payload.clear else json.dumps(value)
    for _attempt in range(SAVE_ATTEMPTS):
        try:
            async with _tenant_session() as db:
                await _assert_may_write(db, user, spec, kind, ref, me=me, admin=admin,
                                        pause=value is True)
                # One save of a (scope, tier, key) at a time: the lock lasts to
                # the end of this transaction. The unique index and the retry
                # stay as the backstop.
                await db.execute(
                    text("SELECT pg_advisory_xact_lock(hashtextextended(:scope, 0))"),
                    {"scope": f"agent_policy:{current_tenant()}:{kind}:{ref or ''}:{tier}:{spec.name}"},
                )
                row = (await db.execute(text(_INSERT_SQL), {
                    "kind": kind,
                    "pid": ref if kind == "project" else None,
                    "ref": ref if kind in ("lane", "member", "box") else None,
                    "tier": tier, "key": spec.name, "value": stored, "me": me,
                })).fetchone()
                return _out(row)
        except IntegrityError as exc:
            retry_or_raise(exc)
            _log.info("agent_policies.save_retry")
            continue
    raise HTTPException(status_code=409, detail=SAVE_BUSY)
