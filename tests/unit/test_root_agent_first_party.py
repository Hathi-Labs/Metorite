"""§15.4 — only an admin of the first-party organization may run the root agent.

The owner decided on 2026-10-03: the root ``metorite`` dev agent runs only for
an admin of the platform's own organization (``organization.first_party``,
migration 157), through ANY path. A customer organization is refused. It keeps
its Copilot CLI shell for those admins (``_tool_injection._D85_OWNER_PENDING``).
WS43-F17, the access clause (``maf_coding_engine.md`` §15.4).

The gate is ``executor._assert_may_run_agent``. Each run boundary calls it
once, before it loads the agent: ``run_agent_stream`` (the chat route),
``_run_agent_inner`` (the gateway run API, workflows, webhooks, cron and the
batch delegation) and ``_run_sub_agent_streaming`` (the streamed delegation).
The member and the org come from the run binding. "Admin" is the established
admin-act gate, ``admin:members:manage``.

No database. The first-party read and the access resolver are stubbed where
the gate imports them. The loader raises a sentinel the moment the gate lets a
run through, so a test sees "refused" or "loaded" and nothing else runs.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
from pathlib import Path
from typing import Any

import acb_auth
import orchestrator._tool_injection as ti
import orchestrator.executor as executor
import orchestrator.mutation as mutation
import pytest
from acb_auth.permissions import EffectiveAccess, build_access

REPO = Path(__file__).resolve().parents[2]

FP_ORG = "aaaaaaaa-0000-4000-8000-000000000001"     # first_party = true
CUST_ORG = "bbbbbbbb-0000-4000-8000-000000000002"   # a customer
FP_ADMIN = "admin@fracktal.example"
FP_MANAGER = "manager@fracktal.example"             # holds admin:members:read
CUST_OWNER = "owner@customer.example"               # '*' in its OWN org

_ACCESS: dict[str, EffectiveAccess] = {
    FP_ADMIN: build_access(
        ["admin:members:read", "admin:members:manage", "agents:run:*"], roles=["admin"],
    ),
    FP_MANAGER: build_access(["admin:members:read", "agents:run:*"], roles=["manager"]),
    CUST_OWNER: build_access(["*"], roles=["owner"]),
}

LOADED = "LOADED: the gate let this run through"


class _Calls:
    def __init__(self) -> None:
        self.loads = 0
        self.mutations = 0


@pytest.fixture()
def calls(monkeypatch) -> _Calls:
    """Stub the two seams the gate reads, the registry and the loader."""
    seen = _Calls()

    async def _first_party(org: str | None) -> bool | None:
        return org == FP_ORG

    async def _resolve(email: str | None, **_kw: Any) -> EffectiveAccess:
        return _ACCESS.get(str(email or "").lower(), EffectiveAccess(is_active=False))

    class _Ctx:
        def __enter__(self) -> Any:
            seen.loads += 1
            raise executor.RunWorkspaceRefused(LOADED)

        def __exit__(self, *_a: Any) -> bool:
            return False

    async def _no_mutation(**_kw: Any) -> None:
        seen.mutations += 1

    import gateway.routes.agent as agent_routes

    monkeypatch.setattr(mutation, "_read_first_party", _first_party)
    monkeypatch.setattr(mutation, "attempt_self_mutation", _no_mutation)
    monkeypatch.setattr(acb_auth, "resolve_access", _resolve)
    monkeypatch.setattr(executor, "load_agent", lambda *a, **k: _Ctx())
    monkeypatch.setattr(agent_routes, "_load_dynamic_agents", lambda: [
        {"name": "metorite", "description": "root", "agent_runtime": "github-copilot",
         "repo_name": None, "local_path": None},
    ])
    monkeypatch.setattr(ti, "_load_disabled_skill_families", lambda name: frozenset())
    return seen


def _stream(agent: str, *, member: str | None, org: str | None,
            claim: str | None = None) -> list[dict[str, Any]]:
    """Drive run_agent_stream as the chat route does: the session's member
    and the session's org, both from the authenticated user."""
    payload: dict[str, Any] = {"message": "run the tests"}
    if claim:
        payload["user_email"] = claim

    async def _collect() -> list[str]:
        return [line async for line in executor.run_agent_stream(
            agent, payload, session_user=member, organization_id=org,
        )]

    frames = asyncio.run(_collect())
    events = []
    for line in frames:
        for part in line.split("\n"):
            part = part.strip()
            if part.startswith("data:") and part[5:].strip() not in ("", "[DONE]"):
                with contextlib.suppress(json.JSONDecodeError):
                    events.append(json.loads(part[5:].strip()))
    return events


def _run_error(events: list[dict[str, Any]]) -> str:
    errors = [e for e in events if e.get("type") == "RUN_ERROR"]
    assert errors, [e.get("type") for e in events]
    return str(errors[-1].get("message"))


# ── 1. The chat route (run_agent_stream) ───────────────────────────────────

@pytest.mark.parametrize("member, org, claim", [
    (CUST_OWNER, CUST_ORG, None),     # a customer org, even its owner
    (FP_MANAGER, FP_ORG, None),       # first-party, not an admin
    (None, FP_ORG, FP_ADMIN),         # the admin's address as a body claim
    (FP_ADMIN, None, None),           # no org in the run binding
])
def test_the_chat_route_refuses_everyone_but_a_first_party_admin(
    calls: _Calls, member, org, claim,
) -> None:
    message = _run_error(_stream("metorite", member=member, org=org, claim=claim))
    assert message == "Agent 'metorite' not found.", message
    assert calls.loads == 0, "a refused run must not load the agent"


def test_the_chat_route_lets_a_first_party_admin_run_it(calls: _Calls) -> None:
    assert _run_error(_stream("metorite", member=FP_ADMIN, org=FP_ORG)) == LOADED
    assert calls.loads == 1


@pytest.mark.parametrize("alias", ["Metorite", "agent-metorite", " METORITE "])
def test_an_alias_of_the_name_is_refused_too(calls: _Calls, alias: str) -> None:
    message = _run_error(_stream(alias, member=CUST_OWNER, org=CUST_ORG))
    assert "not found" in message, message
    assert calls.loads == 0


def test_every_other_agent_is_untouched(calls: _Calls) -> None:
    assert _run_error(_stream("crm-assistant", member=CUST_OWNER, org=CUST_ORG)) == LOADED


# ── 2. The gateway run API (POST /agent/run) ───────────────────────────────

def _user(email: str, org: str) -> Any:
    from acb_auth.roles import UserContext, UserRole
    return UserContext(
        email=email, role=UserRole.EMPLOYEE, organization_id=org, access=_ACCESS[email],
    )


def test_the_run_api_answers_404_to_a_customer(calls: _Calls) -> None:
    from fastapi import HTTPException
    from gateway.routes.agent import AgentRunRequest, run_agent_sync

    with pytest.raises(HTTPException) as err:
        asyncio.run(run_agent_sync(AgentRunRequest(agent="metorite"), _user(CUST_OWNER, CUST_ORG)))
    assert err.value.status_code == 404
    assert err.value.detail == "Agent 'metorite' not found."
    assert calls.loads == 0 and calls.mutations == 0


def test_the_run_api_lets_a_first_party_admin_run_it(calls: _Calls) -> None:
    from gateway.routes.agent import AgentRunRequest, run_agent_sync

    out = asyncio.run(run_agent_sync(AgentRunRequest(agent="metorite"), _user(FP_ADMIN, FP_ORG)))
    assert out.status == "failed" and out.error == LOADED, out
    assert calls.loads == 1


def test_a_refused_batch_run_never_self_mutates(calls: _Calls) -> None:
    """The AgentLoadError clause starts a self-mutation. The refusal must not
    reach it."""
    from acb_common import bind_run_context, run_context_scope
    from acb_common.db import bind_tenant, release_tenant

    async def _go() -> Exception:
        with run_context_scope():
            bind_run_context(user=FP_MANAGER, member_verified=True)
            token = bind_tenant(FP_ORG)
            try:
                await executor.run_agent("metorite", {"message": "x"})
            except executor.AgentRunError as exc:
                return exc
            finally:
                release_tenant(token)
        raise AssertionError("the run was not refused")

    exc = asyncio.run(_go())
    assert isinstance(exc.original, executor.AgentNotFound)
    assert calls.mutations == 0 and calls.loads == 0


# ── 3. Delegation: call_agent and the streamed sub-agent ───────────────────

def _as_parent(member: str, org: str, fn) -> Any:
    """Run *fn* inside a parent run: its member (verified) and its tenant."""
    from acb_common import bind_run_context, run_context_scope
    from acb_common.db import bind_tenant, release_tenant
    from acb_skills.write_artifact import artifact_context_scope, bind_artifact_context

    async def _go() -> Any:
        with run_context_scope(), artifact_context_scope():
            bind_run_context(run_id="r-parent", agent="projects-assistant",
                             user=member, member_verified=True)
            bind_artifact_context(agent_name="projects-assistant", member=member,
                                  session_id="t-parent", workspace_root=str(REPO))
            token = bind_tenant(org)
            try:
                return await fn()
            finally:
                release_tenant(token)

    return asyncio.run(_go())


@pytest.mark.parametrize("member, org, allowed", [
    (CUST_OWNER, CUST_ORG, False),
    (FP_MANAGER, FP_ORG, False),
    (FP_ADMIN, FP_ORG, True),
])
def test_call_agent_takes_the_parent_runs_member(calls: _Calls, member, org, allowed) -> None:
    from acb_skills.agent_tools import call_agent

    text = _as_parent(member, org, lambda: call_agent("metorite", "run the tests"))
    if allowed:
        assert LOADED in text and calls.loads == 1, text
    else:
        assert "Agent 'metorite' not found." in text and calls.loads == 0, text


@pytest.mark.parametrize("member, org, allowed", [
    (CUST_OWNER, CUST_ORG, False),
    (FP_ADMIN, FP_ORG, True),
])
def test_the_streamed_delegation_takes_the_parent_runs_member(
    calls: _Calls, member, org, allowed,
) -> None:
    text = _as_parent(member, org, lambda: executor._run_sub_agent_streaming(
        "metorite", "run the tests", "r-sub", event_queue=asyncio.Queue(),
    ))
    if allowed:
        assert LOADED in text and calls.loads == 1, text
    else:
        assert "Agent 'metorite' not found." in text and calls.loads == 0, text


# ── 4. The rules behind the gate ───────────────────────────────────────────

def test_every_agent_d85_leaves_alone_by_name_is_behind_this_gate() -> None:
    """The D85 name exemption keeps a host shell. Only this gate makes it safe."""
    assert ti._D85_OWNER_PENDING <= executor._FIRST_PARTY_ADMIN_ONLY_AGENTS
    assert frozenset({"metorite"}) == executor._FIRST_PARTY_ADMIN_ONLY_AGENTS


def test_admin_means_admin_members_manage_and_a_manager_lacks_it() -> None:
    """``admin:members:read`` is not an admin check: migration 130 gives it to
    the manager role too."""
    assert executor._FIRST_PARTY_ADMIN_PERMISSION == "admin:members:manage"
    sql = (REPO / "infra" / "postgres" / "130_org_access_control.sql").read_text(encoding="utf-8")
    manager = sql[sql.index("-- manager --"):sql.index("-- member (the default")]
    assert "'admin:members:read'" in manager and "admin:members:manage" not in manager
    admin = sql[sql.index("-- admin ---"):sql.index("-- manager --")]
    assert "'admin:members:manage'" in admin
