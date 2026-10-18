"""WS-55 EA-3a — branch 0 of ``get_current_user``: the signed agent request.

Spec: ``project-docs/specs/external_agent_access.md`` §10.1 and §15.2 D1, D2
and D14 (the gateway 401) · decision D97 · EA3-1.

Database-free. The app below is a stand-in gateway: the REAL default-deny
guard (``require_authenticated``), the REAL ``get_current_user`` and the REAL
``_with_resolved_access``. Only three reads are fakes: the connection row
(``agent_tokens.read_live_connection``) and the two identity reads
(``resolve_access``, ``resolve_identity``). The R8 half, on a real database
as a non-privileged role, is ``tests/unit/test_mcp_edge_authority.py``.

What it proves:

* **D1.** A valid signature, a live connection and an active member resolve
  to that member, with ``UserContext.agent`` attached and the tenant bound.
* **D2.** Every refusal of the list gets 401 through default-deny, and branch
  0 never falls through to branch 1a, 1b or 2.
* **D14 (gateway half).** A revoked connection and a suspended member each
  get 401 on the next call.
* **The one signed string.** The edge helper and the gateway check agree, and
  the canonical string has the exact shape of §10.1.
"""
from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import hmac
import time
from pathlib import Path

import httpx
import pytest
from acb_auth import (
    agent_oauth,
    agent_signing,
    agent_tokens,
    deps,
    get_current_user,
    require_authenticated,
)
from acb_auth.agent_tokens import LiveConnection
from acb_auth.permissions import EffectiveAccess, build_access
from acb_auth.roles import UserContext
from acb_common import get_settings
from acb_common.db import current_tenant
from fastapi import Depends, FastAPI

from tests.unit import _branch_zero_tripwire as tripwire

_ROOT = Path(__file__).resolve().parents[2]

KEY = "current-signing-key-" + "c" * 24
PREVIOUS_KEY = "previous-signing-key-" + "p" * 24
INTERNAL = "internal-token-for-the-signing-suite"
LLM_KEY = "sk-llm-key-for-the-signing-suite"

ORG_A = "0a0a0a0a-0000-4000-8000-00000000000a"
ORG_B = "0b0b0b0b-0000-4000-8000-00000000000b"
CONN = "c0c0c0c0-0000-4000-8000-0000000000c0"
PIN = "a1a1a1a1-0000-4000-8000-0000000000a1"
MEMBER = "agent-member@signing.test"
OTHER = "somebody-else@signing.test"
ORIGIN = "https://client.example"
VIA = "mcp:client.example"


# ── The stand-in gateway ────────────────────────────────────────────────────


class _World:
    """The three reads branch 0 makes, as plain dicts a test can change."""

    def __init__(self) -> None:
        self.connections: dict[tuple[str, str], LiveConnection] = {
            (ORG_A, CONN): LiveConnection(
                connection_id=CONN,
                organization_id=ORG_A,
                member_email=MEMBER,
                client_origin=ORIGIN,
                scopes=("projects:read",),
                pinned_projects=(PIN,),
                include_personal=False,
            ),
        }
        #: email → (organization, is_active)
        self.members: dict[str, tuple[str | None, bool]] = {
            MEMBER: (ORG_A, True),
            OTHER: (ORG_A, True),
        }
        self.reads: list[tuple[str, str]] = []

    async def read_live_connection(self, org: str, conn: str):
        self.reads.append((org, conn))
        return self.connections.get((org, conn))

    async def resolve_access(self, email, *, legacy_role=None, record_request=False, **_):
        org, active = self.members.get((email or "").lower(), (None, False))
        return build_access(["feature:projects"], [], is_active=active) if org \
            else EffectiveAccess(is_active=False)

    async def resolve_identity(self, email):
        org, _ = self.members.get((email or "").lower(), (None, False))
        return ("uid-" + (email or ""), org) if org else (None, None)


#: The armed tripwire of the running test, when there is one. ``_app``
#: installs its ``get_current_user`` override, so the tripwire also sees the
#: body of ``get_current_user`` and not only the two branch-0 helpers.
_ARMED: list = []


@pytest.fixture(params=["plain", "tripwire"])
def world(request, monkeypatch):
    """Every test that uses the stand-in gateway runs twice. The second run
    arms the runtime tripwire (``tests/unit/_branch_zero_tripwire.py``). It
    records each read outside the allowlist that goes through the wrapped
    request, on the paths these tests run. At TEARDOWN it asserts that it
    recorded no violation, so a violation that the code caught still fails
    the test. It does not see a read that goes around the wrapper (see the
    tripwire's docstring)."""
    w = _World()
    w.tripwire = request.param == "tripwire"
    w.armed = tripwire.arm(monkeypatch, deps) if w.tripwire else None
    if w.armed is not None:
        _ARMED.append(w.armed)
    settings = get_settings()
    monkeypatch.setattr(settings, "agent_access_enabled", True)
    monkeypatch.setattr(settings, "agent_edge_signing_key", KEY)
    monkeypatch.setattr(settings, "agent_edge_signing_key_previous", "")
    monkeypatch.setattr(settings, "gateway_internal_token", INTERNAL)
    monkeypatch.setattr(settings, "litellm_master_key", LLM_KEY)
    monkeypatch.setenv("GATEWAY_INTERNAL_TOKEN", INTERNAL)
    monkeypatch.setenv("LITELLM_MASTER_KEY", LLM_KEY)
    for name in ("GATEWAY_TRUST_UNVERIFIED_SSO_HEADERS", "IDENTITY_CUTOVER",
                 "GATEWAY_REFUSE_LLM_KEY_IDENTITY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(agent_tokens, "read_live_connection", w.read_live_connection)
    monkeypatch.setattr(deps, "resolve_access", w.resolve_access)
    monkeypatch.setattr(deps, "resolve_identity", w.resolve_identity)
    try:
        yield w
    finally:
        _ARMED.clear()
    if w.armed is not None:
        tripwire.assert_clean()


def _app() -> FastAPI:
    """Routes at templates of ``MCP_ROUTES``, and three that are not in it."""
    app = FastAPI(dependencies=[require_authenticated(public=frozenset({"/healthz"}))])
    if _ARMED:
        app.dependency_overrides[get_current_user] = (
            tripwire.current_user_override(_ARMED[0]))

    def _who(user: UserContext) -> dict:
        return {
            "email": user.email,
            "org": user.organization_id,
            "tenant": current_tenant(),
            "agent": dataclasses.asdict(user.agent) if user.agent else None,
        }

    @app.get("/projects/tasks/{task_id}")
    async def _task(task_id: str, user: UserContext = Depends(get_current_user)) -> dict:
        return _who(user)

    @app.get("/projects/tasks")
    async def _tasks(user: UserContext = Depends(get_current_user)) -> dict:
        return _who(user)

    @app.get("/auth/me")
    async def _me(user: UserContext = Depends(get_current_user)) -> dict:
        return _who(user)

    @app.get("/projects/tasks/{task_id}/comments")
    async def _comments(task_id: str, user: UserContext = Depends(get_current_user)) -> dict:
        return _who(user)

    # A write route that no pair of MCP_WRITE_ROUTES names. It was
    # `POST /projects/tasks` until EA-6d made that a write pair, and the
    # comment POST until EA-6c did the same. The hard delete of a task is a
    # non-goal of spec §3, so it never becomes a pair.
    @app.delete("/projects/tasks/{task_id}")
    async def _delete(task_id: str, user: UserContext = Depends(get_current_user)) -> dict:
        return _who(user)

    @app.get("/healthz")
    async def _health() -> dict:
        return {"ok": True}

    return app


async def _call(method: str, url: str, headers: dict[str, str] | None = None,
                content: bytes | None = None) -> httpx.Response:
    """One request in its own task, so a tenant it binds ends with it."""
    async def _go() -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=_app()), base_url="http://gateway",
        ) as client:
            return await client.request(method, url, headers=headers, content=content)

    return await asyncio.create_task(_go())


def _signed(path: str, query: str = "", *, method: str = "GET", key: str = KEY,
            org: str = ORG_A, conn: str = CONN, via: str = VIA,
            now: float | None = None) -> dict[str, str]:
    return agent_signing.signed_headers(
        key, organization_id=org, connection_id=conn, actor_via=via,
        method=method, path=path, query=query, now=now,
    )


async def _get(path: str, query: str = "", **kw) -> httpx.Response:
    extra = kw.pop("extra", {})
    headers = {**_signed(path, query, **kw), **extra}
    url = f"{path}?{query}" if query else path
    return await _call("GET", url, headers)


TASK = "/projects/tasks/7d7d7d7d-0000-4000-8000-00000000007d"


# ═══════════════════════════════════════════════════════════════════════════
# D1 — the success path
# ═══════════════════════════════════════════════════════════════════════════


async def test_a_valid_signed_request_resolves_to_the_connections_member(world):
    r = await _get(TASK)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["email"] == MEMBER
    assert body["org"] == ORG_A
    assert body["tenant"] == ORG_A, "the member's tenant is bound for the route"
    assert body["agent"] == {
        "connection_id": CONN,
        "scopes": ["projects:read"],
        "pinned_projects": [PIN],
        "include_personal": False,
        "client_origin": ORIGIN,
        "actor_via": VIA,
    }
    assert world.reads == [(ORG_A, CONN)], "the connection is read in the SIGNED org"


async def test_the_query_is_signed_in_its_sorted_form(world):
    r = await _get("/projects/tasks", "q=abc&page_size=5&page=2")
    assert r.status_code == 200, r.text
    # The same pairs in another order carry the same signature.
    headers = _signed("/projects/tasks", "page=2&page_size=5&q=abc")
    r = await _call("GET", "/projects/tasks?q=abc&page_size=5&page=2", headers)
    assert r.status_code == 200, r.text


async def test_the_previous_key_still_verifies_during_a_rotation(world, monkeypatch):
    monkeypatch.setattr(get_settings(), "agent_edge_signing_key_previous", PREVIOUS_KEY)
    r = await _get(TASK, key=PREVIOUS_KEY)
    assert r.status_code == 200, r.text


async def test_the_member_never_comes_from_a_header(world):
    """A signed request that also names somebody resolves to the ROW's member."""
    r = await _get(TASK, extra={"X-User-Email": OTHER})
    assert r.status_code == 200, r.text
    assert r.json()["email"] == MEMBER
    r = await _get(TASK, extra={"X-User-Email": OTHER,
                                "Authorization": f"Bearer {INTERNAL}"})
    assert r.status_code == 200, r.text
    assert r.json()["email"] == MEMBER


async def test_branch_zero_names_no_member_on_a_model_call(world, monkeypatch):
    called: list[object] = []
    monkeypatch.setattr(deps, "_bind_session_member", lambda e: called.append(e))
    assert (await _get(TASK)).status_code == 200
    assert called == []


# ═══════════════════════════════════════════════════════════════════════════
# D2 — every refusal is a 401, through default-deny
# ═══════════════════════════════════════════════════════════════════════════


async def test_the_flag_off_refuses(world, monkeypatch):
    monkeypatch.setattr(get_settings(), "agent_access_enabled", False)
    assert (await _get(TASK)).status_code == 401


async def test_no_key_refuses(world, monkeypatch):
    monkeypatch.setattr(get_settings(), "agent_edge_signing_key", "")
    assert (await _get(TASK)).status_code == 401


async def test_a_key_under_32_bytes_refuses(world, monkeypatch):
    short = "s" * 31
    monkeypatch.setattr(get_settings(), "agent_edge_signing_key", short)
    assert agent_signing.usable_keys() == {}
    assert (await _get(TASK, key=short)).status_code == 401


@pytest.mark.parametrize("secret", [INTERNAL, LLM_KEY + "-padded-to-32-bytes"])
async def test_a_key_equal_to_a_platform_secret_refuses(world, monkeypatch, secret):
    monkeypatch.setattr(get_settings(), "litellm_master_key", LLM_KEY + "-padded-to-32-bytes")
    monkeypatch.setenv("LITELLM_MASTER_KEY", LLM_KEY + "-padded-to-32-bytes")
    monkeypatch.setattr(get_settings(), "agent_edge_signing_key", secret)
    assert agent_signing.usable_keys() == {}
    assert (await _get(TASK, key=secret)).status_code == 401


async def test_a_bad_signature_refuses(world):
    headers = _signed(TASK)
    digest = headers["X-Agent-Signature"]
    flipped = digest[:-1] + ("0" if digest[-1] != "0" else "1")
    headers["X-Agent-Signature"] = flipped
    assert (await _call("GET", TASK, headers)).status_code == 401


async def test_a_signature_over_another_path_refuses(world):
    headers = _signed("/projects/tasks/11111111-0000-4000-8000-000000000011")
    assert (await _call("GET", TASK, headers)).status_code == 401


async def test_a_tampered_query_refuses(world):
    headers = _signed("/projects/tasks", "q=abc")
    assert (await _call("GET", "/projects/tasks?q=abd", headers)).status_code == 401


async def test_an_unknown_kid_refuses(world):
    headers = _signed(TASK)
    _, _, digest = headers["X-Agent-Signature"].split(",")
    headers["X-Agent-Signature"] = f"v1,deadbeef,{digest}"
    assert (await _call("GET", TASK, headers)).status_code == 401


async def test_a_kid_that_names_another_usable_key_refuses(world, monkeypatch):
    """The kid PICKS the key. A signature by the previous key, sent under the
    current key's kid, is refused even though both keys are usable."""
    monkeypatch.setattr(get_settings(), "agent_edge_signing_key_previous", PREVIOUS_KEY)
    headers = _signed(TASK, key=PREVIOUS_KEY)
    _, _, digest = headers["X-Agent-Signature"].split(",")
    headers["X-Agent-Signature"] = f"v1,{agent_signing.key_id(KEY)},{digest}"
    assert (await _call("GET", TASK, headers)).status_code == 401


#: Five seconds clear of the window. The header carries WHOLE seconds, so a
#: skew of exactly 61 can truncate to 60 on a slow run and pass (it did once,
#: 2026-10-12). The exact boundary is pinned without a clock below.
@pytest.mark.parametrize("skew", [65, -65, 3600])
async def test_a_timestamp_more_than_60_seconds_off_refuses(world, skew):
    assert (await _get(TASK, now=time.time() - skew)).status_code == 401


@pytest.mark.parametrize("offset, fresh", [
    (60, True), (-60, True), (61, False), (-61, False), (0, True)])
def test_the_window_is_60_seconds_either_way(offset, fresh):
    now = 1_700_000_000
    assert agent_signing.timestamp_is_fresh(str(now + offset), now=now) is fresh


async def test_a_timestamp_inside_the_window_passes(world):
    assert (await _get(TASK, now=time.time() - 55)).status_code == 200


async def test_a_revoked_or_expired_connection_refuses(world):
    world.connections.clear()  # the read returns None for both
    assert (await _get(TASK)).status_code == 401


async def test_a_connection_of_another_organization_refuses(world):
    """The header names org B with a connection of org A. The read binds the
    SIGNED organization, so it finds nothing."""
    r = await _get(TASK, org=ORG_B)
    assert r.status_code == 401
    assert world.reads == [(ORG_B, CONN)]


async def test_a_suspended_member_refuses(world):
    world.members[MEMBER] = (ORG_A, False)
    assert (await _get(TASK)).status_code == 401


async def test_a_member_who_moved_to_another_organization_refuses(world):
    world.members[MEMBER] = (ORG_B, True)
    r = await _get(TASK)
    assert r.status_code == 401


async def test_a_member_with_no_organization_refuses(world):
    del world.members[MEMBER]
    assert (await _get(TASK)).status_code == 401


#: A write route that no pair of ``MCP_WRITE_ROUTES`` names. EA-6d made
#: ``POST /projects/tasks`` a write pair, and EA-6c made the comment POST
#: one. So these two tests use the hard delete of a task. Spec §3 makes a
#: hard delete a non-goal, so this pair never joins the list.
_UNLISTED_METHOD = "DELETE"
_UNLISTED_WRITE = TASK
_UNLISTED_WRITE_TEMPLATE = "/projects/tasks/{task_id}"


async def test_a_method_other_than_get_refuses(world):
    assert (_UNLISTED_METHOD, _UNLISTED_WRITE_TEMPLATE) not in agent_oauth.MCP_WRITE_ROUTES
    headers = _signed(_UNLISTED_WRITE, method=_UNLISTED_METHOD)
    assert (await _call(_UNLISTED_METHOD, _UNLISTED_WRITE, headers)).status_code == 401


async def test_a_non_get_pair_in_the_allowlist_still_refuses(world, monkeypatch):
    """A method other than GET needs a pair of ``MCP_WRITE_ROUTES`` and the
    writes flag (EA-6a, EA3-29). A pair that a change puts into ``MCP_ROUTES``
    alone stays refused (§10.1, replay)."""
    pair = (_UNLISTED_METHOD, _UNLISTED_WRITE_TEMPLATE)
    assert pair not in agent_oauth.MCP_WRITE_ROUTES
    widened = agent_oauth.MCP_ROUTES | {pair}
    monkeypatch.setattr(agent_oauth, "MCP_ROUTES", widened)
    headers = _signed(_UNLISTED_WRITE, method=_UNLISTED_METHOD)
    assert (await _call(_UNLISTED_METHOD, _UNLISTED_WRITE, headers)).status_code == 401


async def test_a_route_outside_the_allowlist_refuses(world):
    path = TASK + "/comments"
    r = await _get(path)
    assert r.status_code == 401
    assert world.reads == [], "refused before any read"


async def test_a_get_with_a_body_refuses(world):
    headers = _signed(TASK)
    r = await _call("GET", TASK, headers, content=b"{}")
    assert r.status_code == 401


async def test_an_actor_stamp_the_connection_did_not_earn_refuses(world):
    """A key holder signs ``mcp:evil.example`` for a connection whose origin
    gives ``mcp:client.example``."""
    assert (await _get(TASK, via="mcp:evil.example")).status_code == 401


async def test_a_connection_with_no_origin_refuses(world):
    world.connections[(ORG_A, CONN)] = dataclasses.replace(
        world.connections[(ORG_A, CONN)], client_origin="")
    assert (await _get(TASK, via="mcp:")).status_code == 401


async def test_a_repeated_agent_header_refuses(world):
    headers = [*_signed(TASK).items(), ("X-Agent-Connection", f"{ORG_B}.{CONN}")]
    assert (await _call("GET", TASK, headers)).status_code == 401  # type: ignore[arg-type]


async def test_a_malformed_connection_header_refuses(world):
    headers = _signed(TASK)
    headers["X-Agent-Connection"] = "not-a-pair"
    assert (await _call("GET", TASK, headers)).status_code == 401


@pytest.mark.parametrize("via", ["mcp:client.example", "  MCP:Client.Example ", "mcp:"])
async def test_an_mcp_stamp_with_no_signature_refuses(world, via):
    assert (await _call("GET", TASK, {"X-Actor-Via": via})).status_code == 401


async def test_an_mcp_stamp_with_a_bearer_and_a_member_header_refuses(world):
    """The holder of the internal bearer forges an ``mcp:*`` stamp. Branch 1a
    would admit the bearer and the header. Branch 0 refuses, and never falls
    through."""
    headers = {"Authorization": f"Bearer {INTERNAL}", "X-User-Email": MEMBER,
               "X-Actor-Via": VIA}
    assert (await _call("GET", TASK, headers)).status_code == 401
    # Control: without the stamp, the same bearer and header are a member.
    ok = await _call("GET", TASK, {"Authorization": f"Bearer {INTERNAL}",
                                   "X-User-Email": MEMBER})
    assert ok.status_code == 200 and ok.json()["agent"] is None


async def test_any_x_agent_header_sends_the_request_to_branch_zero(world):
    headers = {"Authorization": f"Bearer {INTERNAL}", "X-User-Email": MEMBER,
               "X-Agent-Anything": "1"}
    assert (await _call("GET", TASK, headers)).status_code == 401


async def test_the_unverified_sso_escape_hatch_does_not_reach_branch_zero(world, monkeypatch):
    monkeypatch.setenv("GATEWAY_TRUST_UNVERIFIED_SSO_HEADERS", "1")
    monkeypatch.setenv("ALLOWED_EMAIL_DOMAIN", "signing.test")
    headers = {"X-User-Email": MEMBER, "X-Actor-Via": VIA}
    assert (await _call("GET", TASK, headers)).status_code == 401
    # Control: the escape hatch does admit the bare header without a stamp.
    ok = await _call("GET", TASK, {"X-User-Email": MEMBER})
    assert ok.status_code == 200 and ok.json()["email"] == MEMBER


async def test_the_llm_key_fallback_does_not_reach_branch_zero(world, monkeypatch):
    monkeypatch.delenv("GATEWAY_INTERNAL_TOKEN", raising=False)
    monkeypatch.setattr(get_settings(), "gateway_internal_token", "")
    headers = {"Authorization": f"Bearer {LLM_KEY}", "X-User-Email": MEMBER,
               "X-Actor-Via": VIA}
    assert (await _call("GET", TASK, headers)).status_code == 401
    ok = await _call("GET", TASK, {"Authorization": f"Bearer {LLM_KEY}",
                                   "X-User-Email": MEMBER})
    assert ok.status_code == 200, "control: the fallback admits the header alone"


async def test_a_refused_request_leaves_no_tenant_bound(world):
    world.members[MEMBER] = (ORG_B, True)

    async def _go() -> tuple[int, str | None]:
        from starlette.requests import Request

        headers = _signed(TASK)
        scope = {
            "type": "http", "method": "GET", "path": TASK, "raw_path": TASK.encode(),
            "query_string": b"", "root_path": "",
            "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        }
        request = Request(scope)
        app = _app()
        scope["app"] = app
        # A matched route, as the router would set it.
        from fastapi.routing import iter_route_contexts

        for ctx in iter_route_contexts(app.routes):
            if ctx.path == "/projects/tasks/{task_id}" and "GET" in ctx.methods:
                scope["route"] = ctx.original_route
        user = await get_current_user(request=request)
        return (0 if user.email is None else 1), current_tenant()

    anonymous, tenant = await asyncio.create_task(_go())
    assert anonymous == 0
    assert tenant is None


# ═══════════════════════════════════════════════════════════════════════════
# Default-deny and the human paths are unchanged
# ═══════════════════════════════════════════════════════════════════════════


async def test_default_deny_still_answers_401_to_a_bare_request(world):
    assert (await _call("GET", TASK)).status_code == 401
    assert (await _call("GET", "/healthz")).status_code == 200


async def test_a_human_request_carries_no_agent_scope(world):
    r = await _call("GET", TASK, {"Authorization": f"Bearer {INTERNAL}",
                                  "X-User-Email": MEMBER,
                                  "X-Actor-Via": "chat:projects-assistant"})
    assert r.status_code == 200
    assert r.json()["agent"] is None
    assert world.reads == [], "a human request never reads a connection"


async def test_a_direct_caller_with_no_request_skips_branch_zero(world):
    user = await get_current_user(
        x_user_email=MEMBER, authorization=f"Bearer {INTERNAL}")
    assert user.email == MEMBER and user.agent is None


# ═══════════════════════════════════════════════════════════════════════════
# D14 (gateway half) — the next call after a revoke or a suspension is a 401
# ═══════════════════════════════════════════════════════════════════════════


async def test_a_revoked_connection_gets_401_on_the_next_call(world):
    assert (await _get(TASK)).status_code == 200
    del world.connections[(ORG_A, CONN)]
    assert (await _get(TASK)).status_code == 401


async def test_a_suspended_member_gets_401_on_the_next_call(world):
    assert (await _get(TASK)).status_code == 200
    world.members[MEMBER] = (ORG_A, False)
    assert (await _get(TASK)).status_code == 401


# ═══════════════════════════════════════════════════════════════════════════
# The one signed string, the keys and the stamp
# ═══════════════════════════════════════════════════════════════════════════


def test_the_canonical_string_is_the_eight_lines_of_the_spec():
    canonical = agent_signing.canonical_string(
        timestamp="1700000000", organization_id=ORG_A, connection_id=CONN,
        actor_via=VIA, method="get", path="/projects/tasks",
        query="q=b&page=2&a=x%20y&blank=",
    )
    assert canonical == "\n".join([
        "v1",
        "1700000000",
        ORG_A,
        CONN,
        VIA,
        "GET",
        "/projects/tasks?a=x+y&blank=&page=2&q=b",
        hashlib.sha256(b"").hexdigest(),
    ])
    assert agent_signing.canonical_target("/projects/tree") == "/projects/tree"


def test_the_signature_header_is_v1_kid_hex():
    canonical = "v1\nx"
    header = agent_signing.signature_header(KEY, canonical)
    expected = hmac.new(KEY.encode(), canonical.encode(), hashlib.sha256).hexdigest()
    assert header == f"v1,{hashlib.sha256(KEY.encode()).hexdigest()[:8]},{expected}"
    assert agent_signing.verify(header, canonical, {agent_signing.key_id(KEY): KEY})


def test_the_edge_helper_signs_what_the_gateway_checks():
    headers = _signed("/projects/search", "q=abc", now=1700000000)
    assert set(headers) == {"X-Agent-Connection", "X-Agent-Timestamp",
                            "X-Actor-Via", "X-Agent-Signature"}
    assert "Authorization" not in headers and "X-User-Email" not in headers
    canonical = agent_signing.canonical_string(
        timestamp="1700000000", organization_id=ORG_A, connection_id=CONN,
        actor_via=VIA, method="GET", path="/projects/search", query="q=abc")
    assert agent_signing.verify(
        headers["X-Agent-Signature"], canonical, {agent_signing.key_id(KEY): KEY})


@pytest.mark.parametrize("origin, stamp", [
    ("https://client.example", "mcp:client.example"),
    ("https://Client.Example:8443", "mcp:client.example"),
    ("http://127.0.0.1:33418", "mcp:127.0.0.1"),
    ("", ""),
    (None, ""),
])
def test_actor_via_for_names_the_host(origin, stamp):
    """With no client id, the host stamp of before EA-13a. EA-13a adds the
    slug of a client id: ``test_agent_client_stamp.py``."""
    assert agent_oauth.actor_via_for(origin, client_id=None) == stamp


def test_actor_via_for_hashes_a_host_the_pattern_refuses():
    origin = "https://" + "a" * 70 + ".example"
    expected = "mcp:" + hashlib.sha256(origin.encode()).hexdigest()[:16]
    assert agent_oauth.actor_via_for(origin, client_id=None) == expected
    assert agent_oauth.ACTOR_VIA_PATTERN.match(expected)


def test_the_actor_via_pattern_has_one_copy():
    from gateway.routes.projects import core

    assert core.ACTOR_VIA_PATTERN is agent_oauth.ACTOR_VIA_PATTERN
    source = (_ROOT / "apps/services/gateway/gateway/routes/projects/core.py"
              ).read_text(encoding="utf-8")
    assert "ACTOR_VIA_PATTERN = " not in source


def test_the_settings_defaults_are_unset_keys_and_a_loopback_gateway():
    from acb_common.settings import Settings

    fields = Settings.model_fields
    assert fields["agent_edge_signing_key"].default == ""
    assert fields["agent_edge_signing_key_previous"].default == ""
    assert fields["agent_edge_gateway_url"].default == "http://127.0.0.1:8080"


# ═══════════════════════════════════════════════════════════════════════════
# Fix round 1: the query, the regexes and the outage
# ═══════════════════════════════════════════════════════════════════════════


async def test_a_repeated_query_key_refuses(world):
    """Starlette keeps the LAST value of a key, and the signed string sorts
    every pair. So a replay with ``q=b&q=a`` reordered to ``q=a&q=b`` would
    read another value under the same signature. Branch 0 refuses both."""
    for raw in ("q=a&q=b", "q=b&q=a"):
        headers = _signed("/projects/tasks", [("q", "a"), ("q", "b")])
        r = await _call("GET", f"/projects/tasks?{raw}", headers)
        assert r.status_code == 401, raw
    assert world.reads == [], "refused before any read"


def test_the_edge_cannot_sign_a_repeated_key():
    with pytest.raises(ValueError):
        agent_signing.canonical_target("/projects/tasks", "q=a&q=b")


@pytest.mark.parametrize("raw", ["q=%FF", "q=%FE", "q=%C3", b"q=\xc3\xa9"])
def test_a_query_that_is_not_strict_utf8_or_ascii_is_refused(raw):
    assert agent_signing.query_pairs(raw) is None


def test_ff_and_fe_cannot_share_one_signature():
    """With a lenient decode both become U+FFFD and one signature covers two
    different raw queries. The strict parse refuses each one."""
    assert agent_signing.query_pairs("q=%FF") is None
    assert agent_signing.query_pairs("q=%FE") is None
    assert agent_signing.query_pairs("q=%C3%A9") == [("q", "é")]


async def test_a_bad_utf8_query_refuses_through_the_gateway(world):
    headers = _signed("/projects/tasks", [("q", "�")])
    r = await _call("GET", "/projects/tasks?q=%FF", headers)
    assert r.status_code == 401


def test_a_trailing_newline_never_passes_a_header_regex():
    """``$`` matches before a trailing newline. The checks use ``fullmatch``."""
    kid = agent_signing.key_id(KEY)
    digest = "a" * 64
    assert agent_signing.parse_signature_header(f"v1,{kid},{digest}") == (kid, digest)
    assert agent_signing.parse_signature_header(f"v1,{kid}\n,{digest}") is None
    assert agent_signing.parse_signature_header(f"v1,{kid},{digest}\n") is None
    assert agent_signing.timestamp_is_fresh("1700000000", now=1700000000)
    assert not agent_signing.timestamp_is_fresh("1700000000\n", now=1700000000)


def _pool_timeout() -> Exception:
    import sqlalchemy.exc

    return sqlalchemy.exc.TimeoutError(
        "QueuePool limit of size 5 overflow 10 reached, connection timed out,"
        " timeout 30.00")


async def test_a_pool_timeout_in_the_connection_read_is_the_one_503(world, monkeypatch):
    """An outage is not a refusal. A 401 would make the MCP client drop a good
    token. The answer is the 503 of ``_with_resolved_access``."""
    async def _timeout(org, conn):
        raise _pool_timeout()

    monkeypatch.setattr(agent_tokens, "read_live_connection", _timeout)
    r = await _get(TASK)
    assert r.status_code == 503, r.text
    assert r.headers["retry-after"] == "2"
    assert r.json()["detail"] == deps.IDENTITY_UNAVAILABLE_DETAIL


async def test_the_503_never_falls_through_to_another_branch(world, monkeypatch):
    """A bearer and a member header beside the signed request change nothing:
    branch 0 answers the 503 itself."""
    async def _timeout(org, conn):
        raise _pool_timeout()

    monkeypatch.setattr(agent_tokens, "read_live_connection", _timeout)
    r = await _get(TASK, extra={"Authorization": f"Bearer {INTERNAL}",
                                "X-User-Email": MEMBER})
    assert r.status_code == 503, r.text


async def test_any_other_failed_read_stays_a_refusal(world, monkeypatch):
    async def _broken(org, conn):
        raise RuntimeError("a SQL fault, not a timeout")

    monkeypatch.setattr(agent_tokens, "read_live_connection", _broken)
    assert (await _get(TASK)).status_code == 401


# ═══════════════════════════════════════════════════════════════════════════
# Fix round 2: what branch 0 reads, and every database outage is the 503
# ═══════════════════════════════════════════════════════════════════════════


async def test_the_success_path_reads_exactly_the_allowed_scope_keys(world, monkeypatch):
    """The tripwire's scope allowlist is EXACTLY what branch 0 reads."""
    armed = world.armed or tripwire.arm(monkeypatch, deps)
    assert (await _get(TASK, "q=x")).status_code == 200
    scope_keys = armed.scope_reads
    header_names = armed.header_reads
    assert scope_keys == tripwire.ALLOWED_SCOPE_KEYS
    assert header_names <= tripwire.ALLOWED_HEADERS
    assert {"x-agent-connection", "x-agent-timestamp", "x-agent-signature",
            "x-actor-via"} <= header_names


REFUSAL = (
    "(EMAXCONNSESSION) max clients reached in session mode - max clients are"
    " limited to pool_size: 15"
)


def _wrapped(orig: BaseException) -> Exception:
    """What SQLAlchemy raises around a driver error."""
    import sqlalchemy.exc as sa_exc

    return sa_exc.DBAPIError.instance(
        "SELECT 1", {}, orig, Exception, connection_invalidated=False)


def _outages() -> dict[str, BaseException]:
    import asyncpg

    errors = asyncpg.exceptions
    return {
        "EMAXCONNSESSION bare": errors.InternalServerError(REFUSAL),
        "EMAXCONNSESSION wrapped": _wrapped(errors.InternalServerError(REFUSAL)),
        "TooManyConnectionsError": errors.TooManyConnectionsError("sorry"),
        "CannotConnectNowError": errors.CannotConnectNowError(
            "the database system is starting up"),
        "ConnectionDoesNotExistError": errors.ConnectionDoesNotExistError(
            "connection was closed in the middle of operation"),
        "ConnectionRefusedError": ConnectionRefusedError(111, "Connection refused"),
        "OSError": OSError(113, "No route to host"),
        "pool timeout": _pool_timeout(),
    }


@pytest.mark.parametrize("label", sorted(_outages()))
async def test_a_database_outage_in_the_connection_read_is_the_one_503(world, monkeypatch, label):
    error = _outages()[label]

    async def _down(org, conn):
        raise error

    monkeypatch.setattr(agent_tokens, "read_live_connection", _down)
    for extra in ({}, {"Authorization": f"Bearer {INTERNAL}", "X-User-Email": MEMBER}):
        r = await _get(TASK, extra=extra)
        assert r.status_code == 503, (label, extra, r.text)
        assert r.headers["retry-after"] == "2"
        assert r.json()["detail"] == deps.IDENTITY_UNAVAILABLE_DETAIL


@pytest.mark.parametrize("error", [ValueError("bad"), RuntimeError("bug"),
                                   KeyError("member_email")])
async def test_a_failed_read_that_is_not_an_outage_stays_a_refusal(world, monkeypatch, error):
    async def _broken(org, conn):
        raise error

    monkeypatch.setattr(agent_tokens, "read_live_connection", _broken)
    for extra in ({}, {"Authorization": f"Bearer {INTERNAL}", "X-User-Email": MEMBER}):
        assert (await _get(TASK, extra=extra)).status_code == 401


@pytest.mark.parametrize("label", ["EMAXCONNSESSION wrapped", "TooManyConnectionsError",
                                   "ConnectionRefusedError", "pool timeout"])
async def test_a_database_outage_in_the_member_resolve_is_the_one_503(world, monkeypatch, label):
    """Step 6 runs the REAL resolvers of ``acb_auth.access``. The database
    refuses them both. The identity read records the failure
    (``identity_read_failed``), and branch 0 answers the 503, not "no seat"."""
    from acb_auth import access

    error = _outages()[label]

    def _refusing_factory():
        def _session():
            raise error
        return _session

    access.invalidate()
    monkeypatch.setattr(access, "_get_session_factory", _refusing_factory)
    monkeypatch.setattr(deps, "resolve_access", access.resolve_access)
    monkeypatch.setattr(deps, "resolve_identity", access.resolve_identity)
    try:
        for extra in ({}, {"Authorization": f"Bearer {INTERNAL}", "X-User-Email": MEMBER}):
            r = await _get(TASK, extra=extra)
            assert r.status_code == 503, (label, extra, r.text)
            assert r.headers["retry-after"] == "2"
    finally:
        access.invalidate()


def test_the_oauth_shape_checks_refuse_a_trailing_newline():
    """``$`` matches before a trailing newline, so each shape check in
    ``agent_oauth`` uses ``fullmatch``."""
    verifier = "v" * 43
    challenge = agent_oauth.s256(verifier)
    assert agent_oauth.is_valid_challenge(challenge)
    assert not agent_oauth.is_valid_challenge(challenge + "\n")
    assert agent_oauth.is_valid_verifier(verifier)
    assert not agent_oauth.is_valid_verifier(verifier + "\n")
    assert agent_oauth.parse_scope("projects:read") == ("projects:read",)
    with pytest.raises(ValueError):
        agent_oauth.parse_scope("projects:read\n")
    assert agent_oauth._PRIVATE_SCHEME.fullmatch("com.example.app")
    assert not agent_oauth._PRIVATE_SCHEME.fullmatch("com.example.app\n")
    assert agent_oauth.ACTOR_VIA_PATTERN.fullmatch("mcp:client.example")
    assert not agent_oauth.ACTOR_VIA_PATTERN.fullmatch("mcp:client.example\n")


# ═══════════════════════════════════════════════════════════════════════════
# Fix round 3, C: the ROLE read alone fails
# ═══════════════════════════════════════════════════════════════════════════


def _role_outages() -> dict[str, BaseException]:
    import sqlalchemy.exc as sa_exc

    found = {k: v for k, v in _outages().items() if k != "pool timeout"}
    found["OperationalError(OSError)"] = sa_exc.OperationalError(
        "SELECT 1", {}, OSError(111, "Connection refused"))
    return found


def _role_read(monkeypatch, error: BaseException | None):
    """The REAL ``resolve_access`` with a session factory that raises *error*
    (or records a read when *error* is None). The identity read is the
    world's fake, and it works.

    Returns ``(reads, flags)``. ``flags`` holds ``access.role_read_failed()``
    as it is INSIDE the request, read by a probe right after each
    ``resolve_access`` returns, in the request's own context."""
    from acb_auth import access

    reads: list[int] = []
    flags: list[bool] = []

    def _factory():
        def _session():
            reads.append(1)
            if error is not None:
                raise error
            raise AssertionError("no read was expected")
        return _session

    async def _probe(email, **kwargs):
        result = await access.resolve_access(email, **kwargs)
        flags.append(access.role_read_failed())
        return result

    access.invalidate()
    # `access._tables_missing` is a process-wide latch. An earlier suite that
    # reached a database with no access tables sets it, and `resolve_access`
    # then degrades before it reads anything. Pin it False for this test and
    # restore it after, as `test_mcp_edge_authority.py` does.
    monkeypatch.setattr(access, "_tables_missing", False)
    monkeypatch.setattr(access, "_get_session_factory", _factory)
    monkeypatch.setattr(deps, "resolve_access", _probe)
    return reads, flags


@pytest.fixture
def stale_role_flag():
    """The request's context starts with a STALE ``True`` in the flag, as a
    task that a failed read ran in would leave it. Only the reset on the way
    in to ``resolve_access`` can clear it."""
    from acb_auth import access

    token = access._role_read_failed.set(True)
    yield
    access._role_read_failed.reset(token)


@pytest.mark.parametrize("label", sorted(_role_outages()))
async def test_an_outage_in_the_role_read_alone_is_the_one_503(world, monkeypatch, label):
    from acb_auth import access

    _, flags = _role_read(monkeypatch, _role_outages()[label])
    try:
        for extra in ({}, {"Authorization": f"Bearer {INTERNAL}", "X-User-Email": MEMBER}):
            r = await _get(TASK, extra=extra)
            assert r.status_code == 503, (label, extra, r.text)
            assert r.headers["retry-after"] == "2"
        assert flags == [True, True]
    finally:
        access.invalidate()


@pytest.mark.parametrize("error", [ValueError("bad"), RuntimeError("bug")])
async def test_a_role_read_that_fails_otherwise_stays_a_refusal(
        world, monkeypatch, stale_role_flag, error):
    """A stale ``True`` in the context is cleared by the reset on the way in,
    and a failure that is not an outage never sets it again."""
    from acb_auth import access

    _, flags = _role_read(monkeypatch, error)
    try:
        assert (await _get(TASK)).status_code == 401
        assert flags == [False], "the flag inside the request"
    finally:
        access.invalidate()


async def test_a_cached_role_never_reads_and_never_flags(world, monkeypatch, stale_role_flag):
    """A cache hit reads nothing, and inside the request the flag is False:
    the reset on the way in cleared the stale ``True``, and the cache hit
    did not set it."""
    from acb_auth import access

    reads, flags = _role_read(monkeypatch, None)
    access._cache_put(MEMBER, build_access(["feature:projects"], [], is_active=True))
    try:
        r = await _get(TASK)
        assert r.status_code == 200, r.text
        assert reads == [], "a cache hit opened a session"
        assert flags == [False], "the flag inside the request"
    finally:
        access.invalidate()


def test_the_outage_list_is_one_list():
    """``db_busy.is_db_outage`` is the one classification. Branch 0 and the
    role read both call it, and neither keeps a copy."""
    import inspect

    from acb_auth import access

    assert "is_db_outage(" in inspect.getsource(deps._read_connection)
    assert "is_db_outage(" in inspect.getsource(access.resolve_access)
    assert not hasattr(deps, "_is_database_outage")
