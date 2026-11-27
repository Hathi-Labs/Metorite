"""GET/PUT /agent/{name}/skills route tests — WS-23 S2 (skills_registry.md).

Route-level tests call the async route functions directly with a fake,
in-memory DB (the ``test_admin_groups.py`` convention: no TestClient, no live
Postgres — every I/O seam is a module-level function on the SUT submodule
``gateway.routes.agent_skills`` and is monkeypatched there). What is locked:

1. ``core`` is un-toggleable → 422 (spec rule 2); ``apps`` is managed via
   App grants → 422 (the S2 scope-independent decision); unknown families
   → 422; duplicates → 400.
2. Writes are replace-wholesale and record reason / set_by on every row —
   the member-override provenance convention.
3. GET/PUT report ``disabled_families`` (what the enforcement seam reads).
4. Unknown agents 404; the PUT route actually carries its
   ``require_permission('admin:access:manage')`` gate (wiring test).
"""
from __future__ import annotations

from typing import Any

import pytest
from acb_auth import UserContext, UserRole
from fastapi import HTTPException

import gateway.routes.agent_skills as sk
from gateway.routes.agent_skills import (
    SkillSettingEntry,
    SkillSettingsRequest,
    get_agent_skills,
    set_agent_skills,
)

ADMIN = UserContext(email="admin@fracktal.in", role=UserRole.EXECUTIVE)
AGENT = "email-assistant"


# ── Fakes ───────────────────────────────────────────────────────────────────

class _Rows:
    def __init__(self, rows: list[dict[str, Any]]):
        self._rows = rows

    def mappings(self) -> "_Rows":
        return self

    def all(self) -> list[dict[str, Any]]:
        return self._rows


class _FakeDB:
    """In-memory agent_skill_setting; SQL matched on substrings the route
    renders, so a query-shape change fails loudly here."""

    def __init__(self) -> None:
        # (agent, instance, family) → row
        self.rows: dict[tuple[str, str, str], dict[str, Any]] = {}
        self.committed = 0

    async def __aenter__(self) -> "_FakeDB":
        return self

    async def __aexit__(self, *args: Any) -> bool:
        return False

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None):
        sql = " ".join(str(stmt).split())
        params = params or {}
        if sql.startswith("SELECT family, enabled"):
            rows = sorted(
                (
                    dict(r) for (a, i, _f), r in self.rows.items()
                    if a == params["name"] and i == ""
                ),
                key=lambda r: r["family"],
            )
            return _Rows(rows)
        if sql.startswith("DELETE FROM agent_skill_setting"):
            self.rows = {
                k: v for k, v in self.rows.items()
                if not (k[0] == params["name"] and k[1] == "")
            }
            return _Rows([])
        if sql.startswith("INSERT INTO agent_skill_setting"):
            self.rows[(params["name"], "", params["family"])] = {
                "family": params["family"],
                "enabled": params["enabled"],
                "reason": params["reason"],
                "set_by": params["by"],
                "set_at": None,
            }
            return _Rows([])
        raise AssertionError(f"unexpected SQL: {sql}")

    async def commit(self) -> None:
        self.committed += 1


@pytest.fixture()
def db(monkeypatch) -> _FakeDB:
    fake = _FakeDB()

    # H2 (task 566): the route opens a tenant-bound session, which commits on
    # exit. The fake is its own async context manager.
    monkeypatch.setattr(sk, "_tenant_session", lambda: fake)
    monkeypatch.setattr(sk, "_known_agent_names", lambda: {AGENT, "sales"})
    fake.audit: list[tuple] = []  # type: ignore[attr-defined]
    monkeypatch.setattr(
        sk, "_record_change",
        lambda actor, action, target, **p: fake.audit.append(
            (actor, action, target, p)
        ),
    )
    return fake


def _put(settings: list[dict[str, Any]]) -> SkillSettingsRequest:
    return SkillSettingsRequest(
        settings=[SkillSettingEntry(**s) for s in settings]
    )


# ── GET ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_no_rows_means_defaults(db):
    out = await get_agent_skills(AGENT, user=ADMIN)
    assert out["agent"] == AGENT
    assert out["settings"] == []
    assert out["disabled_families"] == []
    assert set(out["non_toggleable"]) == {"core", "apps"}


@pytest.mark.asyncio
async def test_get_reports_stored_rows(db):
    db.rows[(AGENT, "", "memory")] = {
        "family": "memory", "enabled": False, "reason": "context budget",
        "set_by": "admin@fracktal.in", "set_at": None,
    }
    out = await get_agent_skills(AGENT, user=ADMIN)
    assert out["disabled_families"] == ["memory"]
    row = out["settings"][0]
    assert row["family"] == "memory" and row["enabled"] is False
    assert row["reason"] == "context budget"
    assert row["set_by"] == "admin@fracktal.in"


@pytest.mark.asyncio
async def test_get_unknown_agent_404(db):
    with pytest.raises(HTTPException) as exc:
        await get_agent_skills("no-such-agent", user=ADMIN)
    assert exc.value.status_code == 404


# ── PUT: the three refusals ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_put_core_is_un_toggleable_422(db):
    """Spec rule 2: a toggle that can strand an agent unable to delegate or
    report is a debugging trap, not a setting."""
    with pytest.raises(HTTPException) as exc:
        await set_agent_skills(
            AGENT, _put([{"family": "core", "enabled": False}]), admin=ADMIN,
        )
    assert exc.value.status_code == 422
    assert db.rows == {}  # nothing written


@pytest.mark.asyncio
async def test_put_apps_managed_via_grants_422(db):
    """The S2 decision: an app grant is an explicit per-agent permission with
    its own management surface — not skill-toggle-governed."""
    with pytest.raises(HTTPException) as exc:
        await set_agent_skills(
            AGENT, _put([{"family": "apps", "enabled": False}]), admin=ADMIN,
        )
    assert exc.value.status_code == 422
    assert "App grants" in exc.value.detail


@pytest.mark.asyncio
async def test_put_unknown_family_422(db):
    with pytest.raises(HTTPException) as exc:
        await set_agent_skills(
            AGENT, _put([{"family": "telepathy", "enabled": False}]),
            admin=ADMIN,
        )
    assert exc.value.status_code == 422
    assert "telepathy" in exc.value.detail


@pytest.mark.asyncio
async def test_put_duplicate_family_400(db):
    with pytest.raises(HTTPException) as exc:
        await set_agent_skills(
            AGENT,
            _put([
                {"family": "memory", "enabled": False},
                {"family": "memory", "enabled": True},
            ]),
            admin=ADMIN,
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_put_unknown_agent_404(db):
    with pytest.raises(HTTPException) as exc:
        await set_agent_skills(
            "no-such-agent", _put([{"family": "memory", "enabled": False}]),
            admin=ADMIN,
        )
    assert exc.value.status_code == 404


# ── PUT: writes record provenance, replace wholesale ───────────────────────

@pytest.mark.asyncio
async def test_put_writes_reason_and_set_by(db):
    out = await set_agent_skills(
        AGENT,
        _put([{"family": "memory", "enabled": False,
               "reason": "context budget"}]),
        admin=ADMIN,
    )
    row = db.rows[(AGENT, "", "memory")]
    assert row["enabled"] is False
    assert row["reason"] == "context budget"
    assert row["set_by"] == "admin@fracktal.in"
    # The tenant-bound block commits on exit. A `commit()` inside it would end
    # the transaction and drop the bind before the answer is read.
    assert db.committed == 0
    assert out["disabled_families"] == ["memory"]
    # Audit carries the settings themselves (who lost memory and why).
    actor, action, target, payload = db.audit[0]
    assert (actor, action, target) == (
        "admin@fracktal.in", "agents.skill_settings_set", f"agent:{AGENT}",
    )
    assert payload["settings"][0]["family"] == "memory"


@pytest.mark.asyncio
async def test_put_replaces_wholesale_and_empty_body_clears(db):
    """No rows = default (enabled) — spec rule 3. An empty settings list must
    therefore CLEAR the table for the agent, restoring today's behavior."""
    await set_agent_skills(
        AGENT,
        _put([
            {"family": "memory", "enabled": False, "reason": "a"},
            {"family": "workflows", "enabled": False, "reason": "a"},
        ]),
        admin=ADMIN,
    )
    assert len(db.rows) == 2
    out = await set_agent_skills(AGENT, _put([]), admin=ADMIN)
    assert db.rows == {}
    assert out["settings"] == [] and out["disabled_families"] == []


@pytest.mark.asyncio
async def test_put_does_not_touch_other_agents_rows(db):
    db.rows[("sales", "", "memory")] = {
        "family": "memory", "enabled": False, "reason": "",
        "set_by": "x", "set_at": None,
    }
    await set_agent_skills(AGENT, _put([]), admin=ADMIN)
    assert ("sales", "", "memory") in db.rows


# ── Wiring ─────────────────────────────────────────────────────────────────

def test_put_route_carries_the_access_manage_gate() -> None:
    """Guards the wiring: the write route losing its require_permission
    dependency must fail here instead of shipping open."""
    for route in sk.router.routes:
        methods = getattr(route, "methods", set()) or set()
        if "PUT" not in methods:
            continue
        deps = [
            getattr(d.dependency, "__qualname__", "")
            for d in getattr(route, "dependencies", [])
        ]
        assert any("require_permission" in q for q in deps), (
            f"PUT {getattr(route, 'path', '')} carries no permission gate"
        )
        return
    raise AssertionError("no PUT route found on the agent_skills router")
