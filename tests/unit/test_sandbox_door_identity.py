"""SBX-F6: the identity step of the sandbox door (SBX-2, Metorite task 3801).

Spec: ``project-docs/specs/agent_sandbox.md`` §12.2 decisions 3, 4, 8 and 9,
and done-when 4 to 7, 10 and 15. ``external_agent_access.md`` §10.1 binds it
as the sibling of branch 0.

Each request goes through the real default deny, the real
``get_current_user`` under the branch-0 tripwire, the real tenant middleware
and the real ``SandboxDoorLimits``. The routes sit at real templates. The
capability row, the member resolve and Redis are stand-ins
(``_sandbox_door_kit``). ``test_sandbox_capability.py`` runs the door on a
real database.

This file also pins the digest of ``acb_auth/sandbox_capabilities.py``. That
module is outside the branch-0 digest, as ``agent_tokens`` is. An edit to it
fails here until the reviewed commit updates the pin.
"""
from __future__ import annotations

import pytest
from acb_auth import deps, sandbox_capabilities
from acb_common.db import current_tenant
from fastapi.testclient import TestClient

from tests.unit import _branch_zero_tripwire as tripwire
from tests.unit import _sandbox_door_kit as kit


@pytest.fixture
def armed_tripwire():
    yield tripwire
    tripwire.assert_clean()


def _send(door, method: str, path: str, headers=None, query: str = ""):
    url = f"{path}?{query}" if query else path
    with TestClient(door.app) as client:
        return client.request(method, url, headers=headers)


# ── The admitted call ───────────────────────────────────────────────────────


def test_a_live_capability_reads_as_the_rows_member(monkeypatch, armed_tripwire):
    door = kit.make_app(monkeypatch, tripwire=armed_tripwire)
    state = kit.arm_door(monkeypatch)
    answer = _send(door, "GET", "/crm/leads", kit.headers())
    assert answer.status_code == 200, answer.text
    assert answer.json() == {"email": kit.MEMBER, "org": kit.ORG_A, "sandbox": "run",
                             "agent": False}
    assert door.tenants == [kit.ORG_A]
    assert state.resolves == [kit.MEMBER]
    assert sum(state.redis.counts.values()) == 1, "the dependency ran once"
    ttl = next(iter(state.redis.ttls.values()))
    assert ttl == state.row.seconds_left + 60


def test_the_trigger_is_any_x_sandbox_header_in_any_case():
    from starlette.requests import Request

    def request(*names: str) -> Request:
        return Request({"type": "http", "method": "GET", "path": "/x",
                        "headers": [(n.encode(), b"v") for n in names], "query_string": b""})

    assert deps._is_sandbox_request(request("X-SANDBOX-Anything"))
    assert deps._is_sandbox_request(request("accept", "x-sandbox-capability"))
    assert not deps._is_sandbox_request(request("x-agent-connection", "authorization"))
    assert not deps._is_sandbox_request(request("x-sandboxed"))


# ── done-when 4: reads only, the list, the public list, no tenant ─────────


def test_a_post_with_a_live_capability_is_403_read_only(monkeypatch, armed_tripwire):
    door = kit.make_app(monkeypatch, tripwire=armed_tripwire)
    kit.arm_door(monkeypatch)
    answer = _send(door, "POST", "/crm/leads", kit.headers())
    assert answer.status_code == 403
    assert answer.json() == {"detail": "sandbox_read_only"}
    assert door.ran == [] and door.after == [None]


def test_a_class_x_pair_is_403_excluded(monkeypatch, armed_tripwire):
    door = kit.make_app(monkeypatch, tripwire=armed_tripwire)
    kit.arm_door(monkeypatch)
    answer = _send(door, "GET", "/crm/export/all.zip", kit.headers())
    assert answer.status_code == 403
    assert answer.json() == {"detail": "sandbox_route_excluded"}
    assert door.ran == [] and door.after == [None]


def test_a_class_a_row_off_the_list_is_404_unknown(monkeypatch, armed_tripwire):
    door = kit.make_app(monkeypatch, tripwire=armed_tripwire)
    kit.arm_door(monkeypatch)
    answer = _send(door, "GET", "/crm/aggregate", kit.headers())
    assert answer.status_code == 404
    assert answer.json() == {"detail": "sandbox_route_unknown"}
    assert door.ran == [] and door.after == [None]


def test_a_refused_capability_gets_401_on_a_public_template_too(monkeypatch, armed_tripwire):
    """The sibling raises. It never returns the anonymous context, which the
    default deny lets through on a template of its ``public`` list."""
    door = kit.make_app(monkeypatch, tripwire=armed_tripwire)
    kit.arm_door(monkeypatch, flag=kit.ORG_B)
    answer = _send(door, "GET", kit.PUBLIC_PATH, kit.headers())
    assert answer.status_code == 401
    assert door.ran == [], "the public route did not run"
    assert door.after == [None]


def test_an_unknown_capability_gets_401_on_a_public_template(monkeypatch, armed_tripwire):
    door = kit.make_app(monkeypatch, tripwire=armed_tripwire)
    kit.arm_door(monkeypatch)
    answer = _send(door, "GET", kit.PUBLIC_PATH,
                   {"X-Sandbox-Capability": "msbx_" + "B" * 43})
    assert answer.status_code == 401
    assert door.ran == [] and door.after == [None]


# ── done-when 5: the scope of the app ───────────────────────────────────────


def test_projects_read_only_is_403_scope_on_a_crm_read(monkeypatch, armed_tripwire):
    door = kit.make_app(monkeypatch, tripwire=armed_tripwire)
    kit.arm_door(monkeypatch, cap=kit.capability(read_scopes=("projects:read",)))
    answer = _send(door, "GET", "/crm/leads", kit.headers())
    assert answer.status_code == 403
    assert answer.json() == {"detail": "sandbox_scope"}
    assert door.ran == [] and door.after == [None]
    ok = _send(door, "GET", "/projects/tasks", kit.headers())
    assert ok.status_code == 200, ok.text


# ── done-when 6: the member and the org come only from the row ─────────────


@pytest.mark.parametrize("extra", [
    {"X-User-Email": kit.OTHER_MEMBER},
    {"Authorization": f"Bearer {kit.INTERNAL}"},
    {"Authorization": f"Bearer {kit.INTERNAL}", "X-User-Email": kit.OTHER_MEMBER},
    {"X-User-Role": "executive"},
    {"X-Agent-Connection": "44444444-4444-4444-4444-444444444444"},
    {"X-Actor-Via": "sandbox:quote-pdf:v3"},
    {"X-Actor-Via": "mcp:client.example"},
])
def test_a_second_credential_is_401_and_branch_1a_never_runs(
        monkeypatch, armed_tripwire, extra):
    door = kit.make_app(monkeypatch, tripwire=armed_tripwire)
    state = kit.arm_door(monkeypatch)
    bound: list[object] = []
    monkeypatch.setattr(deps, "_bind_session_member", lambda email: bound.append(email))
    answer = _send(door, "GET", "/crm/leads", kit.headers(**extra))
    assert answer.status_code == 401
    assert bound == [], "branch 1a ran"
    assert state.resolves == [] and state.lookups == []
    assert door.ran == [] and door.after == [None]


@pytest.mark.parametrize("extra", [
    {"X-Agent-Connection": "44444444-4444-4444-4444-444444444444"},
    {"X-Actor-Via": "mcp:client.example"},
])
def test_the_sibling_runs_before_branch_0(monkeypatch, armed_tripwire, extra):
    """A capability with a header of branch 0 is the sibling's. Branch 0
    would answer the anonymous context, and the default deny lets that
    through on a template of its ``public`` list. The sibling raises."""
    door = kit.make_app(monkeypatch, tripwire=armed_tripwire)
    kit.arm_door(monkeypatch)
    answer = _send(door, "GET", kit.PUBLIC_PATH, kit.headers(**extra))
    assert answer.status_code == 401
    assert door.ran == [], "the public route did not run"


# ── done-when 7: the seat ───────────────────────────────────────────────────


def test_a_member_who_is_no_longer_active_is_401_with_no_tenant(monkeypatch, armed_tripwire):
    door = kit.make_app(monkeypatch, tripwire=armed_tripwire)
    kit.arm_door(monkeypatch, active=False)
    answer = _send(door, "GET", "/crm/leads", kit.headers())
    assert answer.status_code == 401
    assert door.ran == [] and door.after == [None]


def test_a_member_of_another_org_is_401_with_no_tenant(monkeypatch, armed_tripwire):
    door = kit.make_app(monkeypatch, tripwire=armed_tripwire)
    kit.arm_door(monkeypatch, member_org=kit.ORG_B)
    answer = _send(door, "GET", "/crm/leads", kit.headers())
    assert answer.status_code == 401
    assert door.ran == [] and door.after == [None]


def test_an_identity_outage_is_503_with_no_tenant(monkeypatch, armed_tripwire):
    from acb_auth.access import IdentityUnavailable

    door = kit.make_app(monkeypatch, tripwire=armed_tripwire)
    kit.arm_door(monkeypatch)

    async def down(email):
        raise IdentityUnavailable("pool timeout")

    monkeypatch.setattr(deps, "resolve_identity", down)
    answer = _send(door, "GET", "/crm/leads", kit.headers())
    assert answer.status_code == 503
    assert answer.headers["retry-after"] == "2"
    assert door.ran == [] and door.after == [None]


def test_a_database_outage_on_the_lookup_is_503(monkeypatch, armed_tripwire):
    door = kit.make_app(monkeypatch, tripwire=armed_tripwire)
    kit.arm_door(monkeypatch)

    async def down(raw):
        raise ConnectionRefusedError("database is down")

    monkeypatch.setattr(sandbox_capabilities, "lookup", down)
    answer = _send(door, "GET", "/crm/leads", kit.headers())
    assert answer.status_code == 503
    assert answer.headers["retry-after"] == "2"
    assert door.ran == []


def test_a_lookup_error_that_is_not_an_outage_is_401(monkeypatch, armed_tripwire):
    door = kit.make_app(monkeypatch, tripwire=armed_tripwire)
    kit.arm_door(monkeypatch)

    async def broken(raw):
        raise ValueError("a bad row")

    monkeypatch.setattr(sandbox_capabilities, "lookup", broken)
    answer = _send(door, "GET", "/crm/leads", kit.headers())
    assert answer.status_code == 401
    assert door.ran == []


# ── done-when 10: the query and the size of the answer ─────────────────────


@pytest.mark.parametrize("query", ["page_size=101", "page_size=0100", "page_size=1e2",
                                   "limit=500", "per_lane=-1", "page_size="])
def test_a_page_over_the_cap_is_422(monkeypatch, armed_tripwire, query):
    door = kit.make_app(monkeypatch, tripwire=armed_tripwire)
    state = kit.arm_door(monkeypatch)
    answer = _send(door, "GET", "/crm/leads", kit.headers(), query)
    assert answer.status_code == 422
    assert answer.json() == {"detail": "sandbox_page_cap"}
    assert door.ran == [] and state.redis.counts == {}, "a refused call does not count"


@pytest.mark.parametrize("query", ["page_size=100", "limit=7&page_size=1", "per_lane=0"])
def test_a_page_within_the_cap_passes(monkeypatch, armed_tripwire, query):
    door = kit.make_app(monkeypatch, tripwire=armed_tripwire)
    kit.arm_door(monkeypatch)
    answer = _send(door, "GET", "/crm/leads", kit.headers(), query)
    assert answer.status_code == 200, answer.text


@pytest.mark.parametrize("query", ["page_size=10&page_size=20", "a=%FF"])
def test_a_repeated_key_or_a_bad_query_is_422(monkeypatch, armed_tripwire, query):
    door = kit.make_app(monkeypatch, tripwire=armed_tripwire)
    kit.arm_door(monkeypatch)
    answer = _send(door, "GET", "/crm/leads", kit.headers(), query)
    assert answer.status_code == 422
    assert answer.json() == {"detail": "sandbox_bad_query"}
    assert door.ran == []


def test_an_answer_of_4_mb_plus_1_byte_is_422_and_no_byte_leaves(monkeypatch, armed_tripwire):
    door = kit.make_app(monkeypatch, tripwire=armed_tripwire, big=True)
    kit.arm_door(monkeypatch)
    answer = _send(door, "GET", "/crm/owners", kit.headers())
    assert answer.status_code == 422
    assert answer.json() == {"detail": "sandbox_response_too_large"}
    assert kit.BIG_BYTE not in answer.content
    assert door.ran == ["GET /crm/owners"], "the route ran, and the middleware held it"


def test_an_answer_of_exactly_4_mb_passes(monkeypatch, armed_tripwire):
    door = kit.make_app(monkeypatch, tripwire=armed_tripwire)
    kit.arm_door(monkeypatch)
    answer = _send(door, "GET", "/crm/views", kit.headers())
    assert answer.status_code == 200
    assert len(answer.content) == 4_194_304


def test_a_large_answer_without_a_capability_passes_untouched(monkeypatch, armed_tripwire):
    """The cap acts only on a call that the sibling admitted."""
    door = kit.make_app(monkeypatch, tripwire=armed_tripwire, big=True)
    kit.arm_door(monkeypatch)
    answer = _send(door, "GET", "/crm/owners",
                   {"Authorization": f"Bearer {kit.INTERNAL}"})
    assert answer.status_code == 200
    assert len(answer.content) == kit.BIG


def test_the_real_gateway_refuses_a_capability_on_its_public_health_route(monkeypatch):
    """Through ``gateway.main.app`` itself: ``/health`` is on ``PUBLIC_ROUTES``.
    It answers a plain request, and a request with a capability gets 401
    while the flag is empty, with no database read and no Redis call."""
    from acb_common import db, get_settings, tenant_redis
    from gateway.main import app

    monkeypatch.setattr(get_settings(), "sandbox_door_orgs", "")
    reads: list[str] = []
    monkeypatch.setattr(db, "get_session_factory", lambda *a, **k: reads.append("db"))
    monkeypatch.setattr(tenant_redis, "get_tenant_redis", lambda *a, **k: reads.append("redis"))
    client = TestClient(app)  # no lifespan: the route and the middleware only
    plain = client.get("/health")
    refused = client.get("/health", headers=kit.headers())
    assert plain.status_code == 200, plain.text
    assert refused.status_code == 401
    assert refused.json() == {"detail": "Authentication required"}
    assert reads == []


def test_the_record_of_the_sibling_reaches_the_middleware_of_the_gateway():
    """Decision 9: the ContextVar reaches the middleware. The gateway installs
    ``SandboxDoorLimits`` inside ``TenantScopeMiddleware``, as the small
    gateway of these tests does."""
    from gateway.main import TenantScopeMiddleware, app
    from gateway.sandbox_door import SandboxDoorLimits

    order = [m.cls for m in app.user_middleware]
    assert SandboxDoorLimits in order
    assert order.index(TenantScopeMiddleware) < order.index(SandboxDoorLimits), (
        "the door's middleware sits inside the tenant middleware")


def test_the_record_is_fresh_for_each_request(monkeypatch, armed_tripwire):
    door = kit.make_app(monkeypatch, tripwire=armed_tripwire, big=True)
    kit.arm_door(monkeypatch)
    with TestClient(door.app) as client:
        first = client.get("/crm/leads", headers=kit.headers())
        second = client.get("/crm/owners", headers={"Authorization": f"Bearer {kit.INTERNAL}"})
    assert first.status_code == 200
    assert second.status_code == 200 and len(second.content) == kit.BIG
    assert sandbox_capabilities.current_door_call() is None


# ── done-when 15: the flag and the shape of the headers ────────────────────


def test_a_flag_of_org_b_only_refuses_a_capability_of_org_a(monkeypatch, armed_tripwire):
    door = kit.make_app(monkeypatch, tripwire=armed_tripwire)
    state = kit.arm_door(monkeypatch, flag=f" {kit.ORG_B.upper()} ")
    answer = _send(door, "GET", "/crm/leads", kit.headers())
    assert answer.status_code == 401
    assert state.redis.pipelines == 0 and state.resolves == []
    assert door.ran == [] and door.after == [None]


def test_a_flag_that_names_org_a_admits_it(monkeypatch, armed_tripwire):
    door = kit.make_app(monkeypatch, tripwire=armed_tripwire)
    kit.arm_door(monkeypatch, flag=f"{kit.ORG_B}, {kit.ORG_A.upper()}")
    answer = _send(door, "GET", "/crm/leads", kit.headers())
    assert answer.status_code == 200, answer.text


def _spy_reads(monkeypatch) -> list[str]:
    """Spies on the session factory and on the tenant Redis client."""
    from acb_common import db, tenant_redis

    reads: list[str] = []

    def factory(*_a, **_k):
        reads.append("session")
        raise AssertionError("the door read the database")

    def redis(*_a, **_k):
        reads.append("redis")
        raise AssertionError("the door called Redis")

    monkeypatch.setattr(db, "get_session_factory", factory)
    monkeypatch.setattr(tenant_redis, "get_tenant_redis", redis)
    return reads


def test_a_star_with_self_serve_signup_on_is_401_with_no_read(monkeypatch, armed_tripwire):
    """``*`` counts as empty while self-serve signup is on. The real lookup
    runs here, and the spy records no read."""
    from acb_common import get_settings

    door = kit.make_app(monkeypatch, tripwire=armed_tripwire)
    settings = get_settings()
    monkeypatch.setattr(settings, "sandbox_door_orgs", "*")
    monkeypatch.setattr(settings, "self_serve_signup_enabled", "true")
    reads = _spy_reads(monkeypatch)
    answer = _send(door, "GET", "/crm/leads", kit.headers())
    assert answer.status_code == 401
    assert reads == []
    assert door.ran == [] and door.after == [None]


@pytest.mark.parametrize("pairs", [
    [("X-Sandbox-Capability", kit.RAW), ("X-Sandbox-Capability", kit.RAW)],
    [("X-Sandbox-Capability", kit.RAW), ("X-Sandbox-Other", "1")],
    [("X-Sandbox-Capabilityx", kit.RAW)],
    [("X-Sandbox-Other", kit.RAW)],
    [("X-Sandbox-Capability", "msbx_short")],
    [("X-Sandbox-Capability", kit.RAW + "A")],
    [("X-Sandbox-Capability", "Bearer " + kit.RAW)],
])
def test_a_second_sandbox_header_or_a_bad_shape_is_401_with_no_read(
        monkeypatch, armed_tripwire, pairs):
    door = kit.make_app(monkeypatch, tripwire=armed_tripwire)
    state = kit.arm_door(monkeypatch)
    answer = _send(door, "GET", "/crm/leads", pairs)
    assert answer.status_code == 401
    assert state.lookups == [] and state.redis.pipelines == 0
    assert door.ran == [] and door.after == [None]


# ── the flag reader ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(("raw", "signup", "expected"), [
    ("", "", frozenset()),
    ("  ", "", frozenset()),
    ("*", "", frozenset({"*"})),
    ("*", "true", frozenset()),
    ("*", " TRUE ", frozenset()),
    (f"{kit.ORG_A},*", "true", frozenset()),
    (f"{kit.ORG_A.upper()} , {kit.ORG_B}", "true", frozenset({kit.ORG_A, kit.ORG_B})),
    ("*", "false", frozenset({"*"})),
])
def test_door_orgs_reads_the_flag(monkeypatch, raw, signup, expected):
    from acb_common import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "sandbox_door_orgs", raw)
    monkeypatch.setattr(settings, "self_serve_signup_enabled", signup)
    assert sandbox_capabilities.door_orgs() == expected


def test_the_flag_reloads_with_sighup():
    from gateway.flag_reload import reloadable

    assert reloadable("SANDBOX_DOOR_ORGS")


# ── the scope and the context ──────────────────────────────────────────────


def test_the_context_holds_the_run_and_never_the_token_or_its_hash(monkeypatch):
    """Step 11: ``sandbox`` holds the run, the thread, the phase, the app,
    the version and the read scopes. ``agent`` stays ``None``."""
    import asyncio
    import dataclasses

    from starlette.requests import Request

    state = kit.arm_door(monkeypatch, cap=kit.capability(
        app_id="99999999-9999-4999-8999-999999999999", version=3, phase="smoke"))

    request = Request({"type": "http", "method": "GET", "path": "/crm/leads",
                       "headers": [(b"x-sandbox-capability", kit.RAW.encode())],
                       "query_string": b"", "route": None})
    monkeypatch.setattr(deps, "_matched_template", lambda r: "/crm/leads")

    async def go():
        token = sandbox_capabilities.open_door_call()[1]
        try:
            return await deps.get_current_user(request=request)
        finally:
            sandbox_capabilities.close_door_call(token)

    user = asyncio.run(go())
    assert user.agent is None
    assert user.sandbox is not None
    assert dataclasses.asdict(user.sandbox) == {
        "run_id": "run-1", "thread_id": "thread-1", "phase": "smoke",
        "app_id": "99999999-9999-4999-8999-999999999999", "version": 3,
        "read_scopes": ("crm:read", "projects:read"),
        # SBX-3a: the row's write scopes, and no stamp with no ticket.
        "write_scopes": (), "stamp": "",
    }
    text = repr(user) + repr(dataclasses.asdict(user.sandbox))
    assert kit.RAW not in text and state.row.token_sha256 not in text
    assert current_tenant() is None


def test_a_websocket_style_call_with_no_request_never_runs_the_sibling(monkeypatch):
    import asyncio

    state = kit.arm_door(monkeypatch)
    user = asyncio.run(deps.get_current_user())
    assert user.email is None
    assert state.lookups == []


# ── SBX-4b1 done-when 1 and 2: the refusal counter (§12.4 decision 9) ──────

#: The real lookup, before ``kit.arm_door`` puts its stand-in in place.
_REAL_LOOKUP = sandbox_capabilities.lookup


def _real_lookup(monkeypatch, row) -> None:
    """Run the REAL ``lookup`` over a canned row, so the note of the hash on
    the open record is the store's own code, not the kit's."""
    from types import SimpleNamespace

    from acb_auth import agent_tokens

    canned = SimpleNamespace(
        token_sha256=row.token_sha256, organization_id=row.organization_id,
        member_email=row.member_email, run_id=row.run_id, thread_id=row.thread_id,
        app_id=row.app_id, version=row.version, phase=row.phase,
        read_scopes=list(row.read_scopes), write_scopes=list(row.write_scopes),
        expires_at=row.expires_at, seconds_left=row.seconds_left)

    async def _run(work):
        return canned

    monkeypatch.setattr(sandbox_capabilities, "lookup", _REAL_LOOKUP)
    monkeypatch.setattr(agent_tokens, "_run", _run)


def _keys(state, namespace: str) -> dict[str, int]:
    return {k: v for k, v in state.redis.counts.items() if f":{namespace}:" in k}


_REFUSALS = [
    ("POST", "/crm/leads", None, 403, sandbox_capabilities.READ_ONLY),
    ("GET", "/crm/export/all.zip", None, 403, sandbox_capabilities.ROUTE_EXCLUDED),
    ("GET", "/crm/aggregate", None, 404, sandbox_capabilities.ROUTE_UNKNOWN),
    ("GET", "/crm/leads", ("projects:read",), 403, sandbox_capabilities.SCOPE),
]


@pytest.mark.parametrize(("method", "path", "scopes", "status", "code"), _REFUSALS)
def test_the_door_counts_each_scope_refusal(
    monkeypatch, armed_tripwire, method, path, scopes, status, code,
):
    """SBX-4b1 done-when 1. Each of the four codes adds 1 to the refusal
    counter of the token hash, in the org of the row, with the life of the
    call counter. The line ``sandbox.door.call`` holds no token and no hash.

    Mutation: drop the mark in ``log_door_call``, or the note in ``lookup``,
    and no key exists -> red."""
    import structlog
    from acb_auth.agent_tokens import hash_token

    door = kit.make_app(monkeypatch, tripwire=armed_tripwire)
    cap = kit.capability(read_scopes=scopes) if scopes else kit.capability()
    state = kit.arm_door(monkeypatch, cap=cap)
    _real_lookup(monkeypatch, state.row)
    with structlog.testing.capture_logs() as logs:
        answer = _send(door, method, path, kit.headers())
    assert answer.status_code == status
    assert answer.json() == {"detail": code}
    refusals = _keys(state, sandbox_capabilities.REFUSALS_NAMESPACE)
    assert refusals == {
        f"cc:{kit.ORG_A}:{sandbox_capabilities.REFUSALS_NAMESPACE}:{state.row.token_sha256}": 1}
    [ttl] = [v for k, v in state.redis.ttls.items() if k in refusals]
    assert ttl == state.row.seconds_left + sandbox_capabilities.CALLS_GRACE_S
    assert _keys(state, sandbox_capabilities.CALLS_NAMESPACE) == {}
    lines = [line for line in logs if line.get("event") == "sandbox.door.call"]
    assert [line["code"] for line in lines] == [code]
    for line in logs:
        text = repr(line)
        assert kit.RAW not in text and kit.RAW[5:] not in text
        assert hash_token(kit.RAW) not in text


@pytest.mark.parametrize(("path", "query", "status"), [
    ("/crm/leads", "", 200),
    ("/crm/leads", "limit=101", 422),
    ("/crm/leads", "a=1&a=2", 422),
])
def test_the_door_counts_no_admitted_call_and_no_422(
    monkeypatch, armed_tripwire, path, query, status,
):
    """SBX-4b1 done-when 1: an admitted call and a 422 of the query count no
    refusal. ``door_counts`` reads the call count and a refusal count of 0."""
    import asyncio

    door = kit.make_app(monkeypatch, tripwire=armed_tripwire)
    state = kit.arm_door(monkeypatch)
    _real_lookup(monkeypatch, state.row)
    answer = _send(door, "GET", path, kit.headers(), query=query)
    assert answer.status_code == status
    assert _keys(state, sandbox_capabilities.REFUSALS_NAMESPACE) == {}
    counts = asyncio.run(sandbox_capabilities.door_counts(kit.ORG_A, state.row.token_sha256))
    assert counts == sandbox_capabilities.DoorCounts(
        calls=1 if status == 200 else 0, refusals=0)


def test_door_counts_reads_the_refusals_of_one_token(monkeypatch, armed_tripwire):
    """``door_counts`` reads the refusal count in the org of the run binding.
    A count of another org is not the count of this org."""
    import asyncio

    door = kit.make_app(monkeypatch, tripwire=armed_tripwire)
    state = kit.arm_door(monkeypatch)
    _real_lookup(monkeypatch, state.row)
    for _ in range(2):
        assert _send(door, "POST", "/crm/leads", kit.headers()).status_code == 403
    assert _send(door, "GET", "/crm/leads", kit.headers()).status_code == 200
    got = asyncio.run(sandbox_capabilities.door_counts(kit.ORG_A, state.row.token_sha256))
    assert got == sandbox_capabilities.DoorCounts(calls=1, refusals=2)
    other = asyncio.run(sandbox_capabilities.door_counts(kit.ORG_B, state.row.token_sha256))
    assert other == sandbox_capabilities.DoorCounts(calls=0, refusals=0)
    with pytest.raises(ValueError):
        asyncio.run(sandbox_capabilities.door_counts("not-a-uuid", state.row.token_sha256))
    state.redis.down = True
    with pytest.raises(ConnectionError):
        asyncio.run(sandbox_capabilities.door_counts(kit.ORG_A, state.row.token_sha256))


def test_a_refusal_with_no_live_row_counts_nothing(monkeypatch, armed_tripwire):
    """A 401 has no row, so no hash is on the record and no key exists."""
    door = kit.make_app(monkeypatch, tripwire=armed_tripwire)
    state = kit.arm_door(monkeypatch, flag=kit.ORG_B)
    _real_lookup(monkeypatch, state.row)
    assert _send(door, "POST", "/crm/leads", kit.headers()).status_code == 401
    assert state.redis.counts == {}


def test_the_refusal_count_lands_before_the_answer(monkeypatch, armed_tripwire):
    """SBX-4b1 done-when 2. An outer ASGI layer reads the store at the moment
    that ``http.response.start`` of the refusal leaves the middleware. The
    count is already 1 then.

    Mutation: move the INCR after ``send(message)``, or into the ``finally``
    only, and the snapshot reads 0 -> red."""
    door = kit.make_app(monkeypatch, tripwire=armed_tripwire)
    state = kit.arm_door(monkeypatch)
    _real_lookup(monkeypatch, state.row)
    seen: list[dict[str, int]] = []
    inner = door.app

    async def outer(scope, receive, send):
        async def watch(message):
            if message.get("type") == "http.response.start":
                seen.append(_keys(state, sandbox_capabilities.REFUSALS_NAMESPACE))
            await send(message)

        await inner(scope, receive, watch)

    with TestClient(outer) as client:
        answer = client.post("/crm/leads", headers=kit.headers())
    assert answer.status_code == 403
    assert [sum(s.values()) for s in seen] == [1]


def test_a_redis_failure_at_the_refusal_count_keeps_the_refusal(monkeypatch, armed_tripwire):
    """The residual of decision 9: a Redis failure loses one count, and the
    refusal still answers. The warning names the error class only."""
    import structlog

    door = kit.make_app(monkeypatch, tripwire=armed_tripwire)
    state = kit.arm_door(monkeypatch)
    _real_lookup(monkeypatch, state.row)
    state.redis.down = True
    with structlog.testing.capture_logs() as logs:
        answer = _send(door, "POST", "/crm/leads", kit.headers())
    assert answer.status_code == 403
    assert answer.json() == {"detail": sandbox_capabilities.READ_ONLY}
    failed = [line for line in logs if line.get("event") == "sandbox.door_refusal_count_failed"]
    assert [line["error"] for line in failed] == ["ConnectionError"]


# ── SBX-3a done-when 11: the ticket of one approved write ──────────────────

_BODY = b'{"last_name":"Rao"}'


def _write_door(monkeypatch, armed_tripwire, **cap_over):
    """The small gateway with a write route that records the stamp."""
    from acb_auth import deps as deps_module
    from fastapi import Depends

    door = kit.make_app(monkeypatch, tripwire=armed_tripwire)
    stamps: list[str] = []

    async def write(user=Depends(deps_module.get_current_user)):
        stamps.append(user.sandbox.stamp if user.sandbox else "-")
        door.tenants.append(current_tenant())
        return {"ok": True}

    door.app.router.routes = [r for r in door.app.router.routes
                              if getattr(r, "path", "") != "/crm/leads"
                              or "POST" not in getattr(r, "methods", set())]
    door.app.add_api_route("/crm/leads", write, methods=["POST"])
    over = {"write_scopes": ("crm:write",), "app_id": "99999999-9999-4999-8999-999999999999",
            "version": 1, **cap_over}
    state = kit.arm_door(monkeypatch, cap=kit.capability(**over))
    return door, state, stamps


def _ticket(state, *, body: bytes = _BODY, token_sha256: str | None = None,
            path: str = "/crm/leads", method: str = "POST") -> str:
    import asyncio

    return asyncio.run(sandbox_capabilities.issue_ticket(
        org=kit.ORG_A, token_sha256=token_sha256 or state.row.token_sha256,
        method=method, path=path, query=b"", body=body, stamp="sandbox:lead-tool:v1",
        held_id="h" * 32))


def _post(door, headers, body: bytes = _BODY):
    with TestClient(door.app) as client:
        return client.post("/crm/leads", content=body, headers=headers)


def test_a_ticket_admits_one_write_once(monkeypatch, armed_tripwire):
    """Done-when 11. A ticket admits one write once, with its stamp. A second
    use, a ticket of another capability, another body, another path, or a
    ticket with no capability gets 403 ``sandbox_ticket`` or 401, and no
    tenant stays bound. A second ``X-Sandbox-Ticket`` gets 401."""
    door, state, stamps = _write_door(monkeypatch, armed_tripwire)
    ticket = _ticket(state)
    first = _post(door, kit.headers(**{"X-Sandbox-Ticket": ticket}))
    assert first.status_code == 200, first.text
    assert stamps == ["sandbox:lead-tool:v1"]
    assert door.tenants == [kit.ORG_A]

    again = _post(door, kit.headers(**{"X-Sandbox-Ticket": ticket}))
    assert again.status_code == 403
    assert again.json() == {"detail": sandbox_capabilities.TICKET}

    other_cap = _ticket(state, token_sha256="0" * 64)
    assert _post(door, kit.headers(**{"X-Sandbox-Ticket": other_cap})).status_code == 403
    other_body = _ticket(state)
    assert _post(door, kit.headers(**{"X-Sandbox-Ticket": other_body}),
                 body=b'{"last_name":"Ravi"}').status_code == 403
    other_path = _ticket(state, path="/crm/deals")
    assert _post(door, kit.headers(**{"X-Sandbox-Ticket": other_path})).status_code == 403
    unknown = "f" * 32
    assert _post(door, kit.headers(**{"X-Sandbox-Ticket": unknown})).status_code == 403

    alone = _ticket(state)
    assert _post(door, {"X-Sandbox-Ticket": alone}).status_code == 401
    twice = _ticket(state)
    doubled = [("X-Sandbox-Capability", kit.RAW), ("X-Sandbox-Ticket", twice),
               ("X-Sandbox-Ticket", twice)]
    assert _post(door, doubled).status_code == 401
    assert _post(door, kit.headers(**{"X-Sandbox-Ticket": "NOT-A-TICKET"})).status_code == 401

    assert stamps == ["sandbox:lead-tool:v1"], "only the first write ran"
    assert door.after[0] == kit.ORG_A
    assert len(door.after) == 9 and all(t is None for t in door.after[1:]), (
        "a refused ticket leaves no tenant bound")


def test_a_direct_write_with_a_write_scope_is_still_read_only(monkeypatch, armed_tripwire):
    door, _state, stamps = _write_door(monkeypatch, armed_tripwire)
    answer = _post(door, kit.headers())
    assert answer.status_code == 403
    assert answer.json() == {"detail": sandbox_capabilities.READ_ONLY}
    assert stamps == []


def test_a_ticket_needs_the_write_scope_of_the_app(monkeypatch, armed_tripwire):
    door, state, stamps = _write_door(monkeypatch, armed_tripwire,
                                      write_scopes=("projects:write",))
    answer = _post(door, kit.headers(**{"X-Sandbox-Ticket": _ticket(state)}))
    assert answer.status_code == 403
    assert answer.json() == {"detail": sandbox_capabilities.SCOPE}
    assert stamps == []


def test_a_ticket_on_a_get_is_401_and_reads_no_body(monkeypatch, armed_tripwire):
    door = kit.make_app(monkeypatch, tripwire=armed_tripwire)
    kit.arm_door(monkeypatch)
    answer = _send(door, "GET", "/crm/leads", kit.headers(**{"X-Sandbox-Ticket": "a" * 32}))
    assert answer.status_code == 401
    assert door.ran == []


def test_a_redis_fault_at_the_ticket_is_503(monkeypatch, armed_tripwire):
    door, state, stamps = _write_door(monkeypatch, armed_tripwire)
    ticket = _ticket(state)
    state.redis.down = True
    answer = _post(door, kit.headers(**{"X-Sandbox-Ticket": ticket}))
    assert answer.status_code == 503
    assert stamps == []


# ── the digest of the capability store ──────────────────────────────────────

#: SHA-256 of the normalised AST of ``acb_auth/sandbox_capabilities.py``, by
#: Python minor version. Update it ONLY in the commit that carries the
#: diff-reviewer review and the independent verification of
#: ``external_agent_access.md`` §10.1, as the branch-0 digest.
_STORE_DIGESTS: dict[tuple[int, int], str] = {
    # SBX-2 (2026-11-26): the first pin was 4b7b1023... (3.12) and
    # 35c1dc2e... (3.13).
    # SBX-4b1 (task 3986, 2026-11-26): the refusal counter of §12.4 decision
    # 9 (the note in `lookup`, the mark in `log_door_call`, `count_refusal`
    # and `door_counts`). CPython 3.12.16 and 3.13.18 gave it. Reviewed:
    # the Opus diff-reviewer APPROVE of 532a1b2cd0, which names both values
    # ("approved by this review"). Verified: the independent Opus
    # ws-verifier of 532a1b2cd0 (done-when 1, 2 and 4 to 7 met, R8 15 of 15
    # on a real database, mutations a to d red). Task 3986, 2026-11-26.
    # SBX-3a (task 3802, 2026-11-27, agent_sandbox.md §12.5 decisions 4 and
    # 6): `mint` takes a write scope of `SANDBOX_WRITE_SCOPES` in the run
    # phase only, with an app id and a version. The ticket: `issue_ticket`,
    # `take_ticket`, `ticket_well_formed` and the new codes. The pins before
    # it were 3.12 ``a226cce6…137bec11`` and 3.13 ``ac7483eb…1dd1a8a27``.
    # CPython 3.12.16 and 3.13.18 gave the new pins. OWED: the Opus
    # diff-reviewer review and the independent verification. A later commit
    # names both.
    (3, 12): "51e708c7d277f902287a16e210ac612a6c4339083af88f6dccc95c01bb9fecc5",
    (3, 13): "665a61291b760d4facd9cb52615a3c9eb8862668875d43520c69b34269f14a45",
}


def _store_digest() -> str:
    import ast
    import hashlib

    with open(sandbox_capabilities.__file__, encoding="utf-8-sig") as fh:
        return hashlib.sha256(ast.dump(ast.parse(fh.read())).encode()).hexdigest()


def test_the_digest_of_the_capability_store_is_pinned():
    import sys

    version = sys.version_info[:2]
    assert version in _STORE_DIGESTS, f"No digest is pinned for Python {version}."
    assert _store_digest() == _STORE_DIGESTS[version], (
        "sandbox_capabilities.py changed. Get a diff-reviewer review and an "
        "independent verification, then update this digest in the same commit.")
