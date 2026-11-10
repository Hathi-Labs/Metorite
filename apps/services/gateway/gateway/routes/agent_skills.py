"""Per-agent skill toggles — WS-23 S2 (specs/skills_registry.md §3, §4).

GET /agent/{name}/skills
    The stored ``agent_skill_setting`` rows for one agent (agent-wide
    ``instance = ''``), plus the derived disabled-family list and which
    families are not toggleable and why.

PUT /agent/{name}/skills
    Replace the agent's settings wholesale (the ``PUT
    /admin/members/{email}/overrides`` convention — the body is the complete
    explicit state, provenance columns reason/set_by/set_at recorded on every
    row). Gated ``admin:access:manage``: a skill toggle is an access decision,
    the same seam that governs member overrides.

Rules enforced HERE, at the API (the spec's three rules §2):
    * a family must exist in ``SKILL_FAMILIES`` — unknown slugs are 422;
    * ``core`` is the un-toggleable floor — 422 (rule 2);
    * ``apps`` is managed via App grants (an app grant is an explicit
      per-agent permission with its own management surface) — 422;
    * writes always record reason / set_by.

Enforcement of stored rows happens in ONE place for both runtimes:
``orchestrator._tool_injection._resolve_injected_scope`` (intersection only —
a toggle can only narrow; no rows ⇒ byte-identical to today).

Every I/O seam is a module-level function so route tests patch the SUT
submodule hermetically (the ``test_admin_groups.py`` convention).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from acb_auth import UserContext, get_current_user, require_permission
from acb_common import get_logger
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

_log = get_logger("gateway.agent_skills")

router = APIRouter(prefix="/agent", tags=["agents"])

#: Families the toggle surface refuses, with the reason the UI shows.
NON_TOGGLEABLE: dict[str, str] = {
    "core": (
        "The guaranteed core floor — always injected, never toggleable "
        "(skills_registry.md rule 2)."
    ),
    "apps": (
        "Managed via App grants — a granted Custom App is an explicit "
        "per-agent permission with its own management surface."
    ),
}


# ── Models ──────────────────────────────────────────────────────────────────

class SkillSettingEntry(BaseModel):
    family: str
    enabled: bool
    reason: str = ""


class SkillSettingsRequest(BaseModel):
    settings: list[SkillSettingEntry]


# ── Seams (module-level so tests patch the SUT submodule, hermetically) ─────

async def _get_db() -> Any:
    """Async session from the shared pooled engine (admin kernel recipe)."""
    from gateway.routes.admin._common import get_db  # noqa: PLC0415
    return await get_db()


def _known_agent_names() -> set[str] | None:
    """Registered agent names, or ``None`` when the registry is unavailable
    (best-effort — a registry hiccup must not 404 every settings read)."""
    try:
        from gateway.routes.agent import (  # noqa: PLC0415
            _AGENT_REGISTRY, _load_dynamic_agents,
        )
        return {
            str(a.get("name"))
            for a in _load_dynamic_agents() + _AGENT_REGISTRY
            if a.get("name")
        }
    except Exception:  # noqa: BLE001
        return None


def _record_change(actor: str | None, action: str, target: str,
                   **payload: Any) -> None:
    """Append to the audit log, best-effort, through the ONE admin writer.

    ``admin._common.record_admin_change`` stamps the bound tenant and skips
    a tenant-less event with ``ACB_GRAPH_TENANT_BIND`` on (task 2221). So the
    row lands in the admin's org, never under the operator org. A failed
    audit still never fails the toggle. Fence:
    ``tests/unit/test_agent_skills_audit_org_r8.py`` (R8).
    """
    from gateway.routes.admin._common import record_admin_change

    record_admin_change(actor, action, target, **payload)


def _iso(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return "" if value is None else str(value)


# ── Shared read ─────────────────────────────────────────────────────────────

async def _settings_payload(db: Any, name: str) -> dict[str, Any]:
    rows = (
        await db.execute(
            text(
                "SELECT family, enabled, reason, set_by, set_at "
                "  FROM agent_skill_setting "
                " WHERE agent_name = :name AND instance = '' "
                " ORDER BY family"
            ),
            {"name": name},
        )
    ).mappings().all()
    settings = [
        {
            "family": r["family"],
            "enabled": bool(r["enabled"]),
            "reason": r["reason"] or "",
            "set_by": r["set_by"] or "",
            "set_at": _iso(r["set_at"]),
        }
        for r in rows
    ]
    return {
        "agent": name,
        "instance": "",
        "settings": settings,
        "disabled_families": sorted(
            s["family"] for s in settings if not s["enabled"]
        ),
        "non_toggleable": NON_TOGGLEABLE,
    }


def _require_known_agent(name: str) -> None:
    known = _known_agent_names()
    if known is not None and name not in known:
        raise HTTPException(
            status_code=404, detail=f"No registered agent '{name}'."
        )


# ── Endpoints ───────────────────────────────────────────────────────────────

@router.get("/{name}/skills", summary="Per-agent skill-family settings")
async def get_agent_skills(
    name: str,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Stored skill settings for one agent (agent-wide instance)."""
    _require_known_agent(name)
    db = await _get_db()
    async with db:
        return await _settings_payload(db, name)


@router.put("/{name}/skills",
            summary="Replace an agent's skill-family settings",
            dependencies=[require_permission("admin:access:manage")])
async def set_agent_skills(
    name: str,
    req: SkillSettingsRequest,
    admin: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Replace the agent's settings wholesale.

    The body is the complete explicit state for ``instance = ''``: a family
    not listed reverts to the default (enabled — injection unchanged, spec
    rule 3). Enforcement reads only ``enabled = false`` rows, so the minimal
    body is just the disabled families; explicit ``enabled = true`` rows are
    kept as a recorded decision.
    """
    from acb_skills.skill_families import SKILL_FAMILIES  # noqa: PLC0415

    _require_known_agent(name)

    cleaned: list[tuple[str, bool, str]] = []
    seen: set[str] = set()
    for entry in req.settings:
        family = (entry.family or "").strip()
        if family in NON_TOGGLEABLE:
            raise HTTPException(status_code=422, detail=NON_TOGGLEABLE[family])
        if family not in SKILL_FAMILIES:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Unknown skill family '{family}'. Valid families: "
                    f"{sorted(set(SKILL_FAMILIES) - set(NON_TOGGLEABLE))}."
                ),
            )
        if family in seen:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Duplicate setting for '{family}' — one row per family."
                ),
            )
        seen.add(family)
        cleaned.append((family, bool(entry.enabled), entry.reason or ""))

    db = await _get_db()
    async with db:
        await db.execute(
            text(
                "DELETE FROM agent_skill_setting "
                " WHERE agent_name = :name AND instance = ''"
            ),
            {"name": name},
        )
        for family, enabled, reason in cleaned:
            await db.execute(
                text(
                    "INSERT INTO agent_skill_setting "
                    "  (agent_name, instance, family, enabled, reason, set_by) "
                    "VALUES (:name, '', :family, :enabled, :reason, :by)"
                ),
                {"name": name, "family": family, "enabled": enabled,
                 "reason": reason, "by": admin.email},
            )
        await db.commit()
        payload = await _settings_payload(db, name)

    _log.info("agent_skills_set", agent=name, by=admin.email,
              count=len(cleaned),
              disabled=payload["disabled_families"])
    # The settings themselves, not just the count: "which agent lost memory
    # and why" is the question this record exists to answer.
    _record_change(admin.email, "agents.skill_settings_set", f"agent:{name}",
                   settings=[{"family": f, "enabled": e, "reason": r}
                             for f, e, r in cleaned])
    return payload
