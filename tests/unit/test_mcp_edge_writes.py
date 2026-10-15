"""WS-55 EA-6a — the first write tools of the MCP edge, and the gateway half.

Spec: ``project-docs/specs/external_agent_access.md`` §6.1 (EA3-28 to EA3-31),
§9.2, §9.5, §9.6, §10.1 and §15 EA-6a, the acceptance W1 to W8.

Three halves.

**Database-free (always runs).** The signed string of a GET is byte-identical
to the string before EA-6a (W8, a golden). A write signs a 9th line, the
nonce. The edge app runs in process with a fake token store and a fake
gateway behind ``gateway_client.use_transport``. It proves the two tools: the
scope of check 8, the PATCH body and ``If-Match``, the refusal of a move into
``done`` or ``cancelled``, the answer map of §9.6 and the reference shapes.

**R8 (a real database, as a NON-privileged role, on a phase-4-promoted
catalog of its own).** The edge calls the REAL gateway app through
``httpx.ASGITransport`` (EA3-20), and a test also signs a request to the
gateway app itself. Branch 0 keeps its nonces and its counter in a fake under
the REAL ``TenantRedis``, so CI needs no Redis. It proves W1 to W7, the cap,
the done refusal and the 503 of a Redis outage.

**A real Redis 7 (optional).** :class:`TestOnARealRedis` runs the nonce and
the cap on a real server when ``AGENT_WRITES_REDIS_URL`` names one. CI has no
Redis service, so it skips there. A skip is not a pass.

Run::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_mcp_edge_writes.py tests/unit/test_task_refs_route.py -v -rs
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qsl

import httpx
import pytest

pytest.importorskip("sqlalchemy")

from acb_auth import access as access_module
from acb_auth import agent_oauth, agent_signing, agent_tokens
from acb_auth.access import invalidate as invalidate_access
from acb_auth.agent_tokens import TokenLookup
from acb_common import get_settings, tenant_redis
from acb_common.tenant_redis import TenantRedis
from agent_edge import app as edge_app_module
from agent_edge import gateway_client, tools
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from tests.unit._tenant_ladder import _LADDER_DIR, apply_ladder, tenant_engine_scope

_SNAPSHOT = os.environ.get("_ACB_TENANT_LADDER_URL_AT_LAUNCH")
_URL = (
    _SNAPSHOT
    if _SNAPSHOT is not None
    else os.environ.get("TENANT_LADDER_DATABASE_URL", "")
).strip()

_DB_GATE = pytest.mark.skipif(
    not _URL,
    reason=(
        "TENANT_LADDER_DATABASE_URL unset — R8 requires a REAL Postgres with "
        "pgvector. A skip here is not a pass; CI must set it. ⚠️ Do NOT export "
        "it as DATABASE_URL — see test_org_provisioning.py's docstring."
    ),
)

#: The real client factory, kept before any test patches it (fix round 1).
_REAL_GET_TENANT_REDIS = tenant_redis.get_tenant_redis

_REDIS_URL = os.environ.get("AGENT_WRITES_REDIS_URL", "").strip()
_REDIS_GATE = pytest.mark.skipif(
    not (_URL and _REDIS_URL),
    reason=(
        "AGENT_WRITES_REDIS_URL or TENANT_LADDER_DATABASE_URL unset — this class "
        "runs the nonce and the cap on a REAL Redis 7. CI has no Redis service, "
        "so it skips there. A skip is not a pass."
    ),
)

ISSUER = "https://mcp.metorite.com"
RESOURCE = "https://mcp.metorite.com/mcp"
KEY = "mcp-writes-signing-key-" + "w" * 24
ORG = "11111111-2222-4333-8444-555555555555"
CONN = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
ORIGIN = "https://client.example"
VIA = "mcp:client.example"
GOOD = "good-token"
WRITE_SCOPES = ("projects:read", "projects:write")
CHALLENGE_403_WRITE = (
    'Bearer error="insufficient_scope", scope="projects:write", '
    'resource_metadata="https://mcp.metorite.com/.well-known/oauth-protected-resource/mcp"')


def _uuid(n: int) -> str:
    return f"00000000-0000-4000-8000-{n:012d}"


#: Every write tool, in the order of ``tools/list``: the two of EA-6a, then
#: the two of EA-6d (``test_mcp_edge_task_writes.py``), then ``add_comment``
#: of EA-6c (``test_mcp_edge_comments.py``) and ``claim_task`` of EA-6b
#: (``test_mcp_edge_claim.py``).
WRITE_TOOL_NAMES = ("set_task_status", "add_task_reference", "create_task", "update_task",
                    "add_comment", "claim_task")


# ═══════════════════════════════════════════════════════════════════════════
# W8 and EA3-28: the signed string
# ═══════════════════════════════════════════════════════════════════════════

#: The signed string of one GET, as the code of 2026-10-13 (origin/main
#: 2295c9bf9, before EA-6a) built it, byte for byte. The hash line is the
#: SHA-256 of zero bytes.
GOLDEN_GET = (
    "v1\n1700000000\n" + ORG + "\n" + CONN + "\nmcp:client.example\nGET\n"
    "/projects/tasks?a=1&b=2\n"
    "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
)

#: Its ``X-Agent-Signature`` under :data:`KEY`, computed with the module of
#: origin/main 2295c9bf9 (before EA-6a).
GOLDEN_GET_SIGNATURE = (
    "v1,86187645,c076674760148b59e093b9ab02c1e4aacaebf6af8903a7cecc7840977225342d")


def _golden_args(**kw: Any) -> dict[str, Any]:
    args: dict[str, Any] = dict(
        timestamp="1700000000", organization_id=ORG, connection_id=CONN,
        actor_via=VIA, method="GET", path="/projects/tasks", query="b=2&a=1")
    args.update(kw)
    return args


class TestTheSignedString:
    """W8 and EA3-28, database-free."""

    def test_w8_the_get_string_is_byte_identical_to_before(self):
        assert agent_signing.canonical_string(**_golden_args()) == GOLDEN_GET

    def test_w8_the_get_signature_is_byte_identical_to_before(self):
        canonical = agent_signing.canonical_string(**_golden_args())
        assert agent_signing.signature_header(KEY, canonical) == GOLDEN_GET_SIGNATURE

    def test_a_get_sends_exactly_the_four_headers(self):
        headers = agent_signing.signed_headers(
            KEY, organization_id=ORG, connection_id=CONN, actor_via=VIA,
            method="GET", path="/projects/tasks", query="b=2&a=1", now=1_700_000_000)
        assert set(headers) == {agent_signing.HEADER_CONNECTION,
                                agent_signing.HEADER_TIMESTAMP,
                                agent_signing.HEADER_ACTOR_VIA,
                                agent_signing.HEADER_SIGNATURE}
        assert headers[agent_signing.HEADER_SIGNATURE] == GOLDEN_GET_SIGNATURE

    def test_a_write_signs_a_ninth_line_the_nonce(self):
        body = b'{"status_id":"x"}'
        nonce = "A" * 22
        canonical = agent_signing.canonical_string(
            **_golden_args(method="PATCH", path="/projects/tasks/t", query="",
                           body=body, nonce=nonce))
        lines = canonical.split("\n")
        assert len(lines) == 9
        assert lines[5] == "PATCH"
        assert lines[7] == hashlib.sha256(body).hexdigest()
        assert lines[8] == nonce

    @pytest.mark.parametrize("method,nonce", [
        ("GET", "A" * 22), ("PATCH", None), ("POST", ""), ("POST", "A" * 21),
        ("POST", "A" * 21 + "="), ("POST", "A" * 21 + "\n")])
    def test_a_nonce_on_a_get_or_a_bad_nonce_on_a_write_never_signs(self, method, nonce):
        with pytest.raises(ValueError):
            agent_signing.canonical_string(**_golden_args(method=method, nonce=nonce))

    def test_a_write_header_set_carries_a_fresh_nonce(self):
        one = agent_signing.signed_headers(
            KEY, organization_id=ORG, connection_id=CONN, actor_via=VIA,
            method="POST", path="/projects/tasks/t/refs", body=b"{}")
        two = agent_signing.signed_headers(
            KEY, organization_id=ORG, connection_id=CONN, actor_via=VIA,
            method="POST", path="/projects/tasks/t/refs", body=b"{}")
        assert agent_signing.nonce_is_well_formed(one[agent_signing.HEADER_NONCE])
        assert one[agent_signing.HEADER_NONCE] != two[agent_signing.HEADER_NONCE]
        assert len(one[agent_signing.HEADER_NONCE]) == 22

    def test_the_nonce_lives_twice_the_skew(self):
        assert agent_signing.NONCE_TTL_S == 2 * agent_signing.MAX_SKEW_S == 120
        assert agent_signing.NONCE_BYTES == 16

    def test_the_cap_is_120_in_600_seconds(self):
        assert (agent_oauth.WRITE_CAP, agent_oauth.WRITE_WINDOW_S) == (120, 600)

    def test_the_writes_flag_defaults_off(self):
        from acb_common.settings import Settings

        assert Settings.model_fields["agent_access_writes_enabled"].default is False


# ═══════════════════════════════════════════════════════════════════════════
# The edge, database-free: a fake token store and a fake gateway
# ═══════════════════════════════════════════════════════════════════════════

TASK = _uuid(1001)
PROJECT = _uuid(1)
S_TODO, S_DOING, S_DONE, S_CANCELLED = (_uuid(n) for n in (9001, 9002, 9003, 9004))
V1 = "2026-10-14T00:00:00.123456+00:00"
V2 = "2026-10-14T00:00:01.000001+00:00"


def _lookup(*, scope: str = "projects:read projects:write",
            scopes: tuple[str, ...] = WRITE_SCOPES) -> TokenLookup:
    return TokenLookup(
        connection_id=CONN, organization_id=ORG, kind="access",
        client_id="https://client.example/meta.json", resource=RESOURCE,
        scope=scope, expires_at=datetime.now(UTC) + timedelta(hours=1),
        scopes=scopes, pinned_projects=(), include_personal=False,
        client_origin=ORIGIN,
    )


class _Gateway:
    """A fake gateway that records each request and answers from tables."""

    def __init__(self) -> None:
        self.requests: list[SimpleNamespace] = []
        self.answers: dict[tuple[str, str], tuple[int, Any]] = {}
        self.statuses = [
            {"id": S_TODO, "name": "To do", "category": "todo"},
            {"id": S_DOING, "name": "Doing", "category": "in_progress"},
            {"id": S_DONE, "name": "Done", "category": "done"},
            {"id": S_CANCELLED, "name": "Dropped", "category": "cancelled"},
        ]
        self.ref_status = 201

    async def __call__(self, scope, receive, send) -> None:
        body = b""
        while True:
            message = await receive()
            body += message.get("body", b"")
            if not message.get("more_body"):
                break
        headers = {k.decode("latin-1").lower(): v.decode("latin-1")
                   for k, v in scope["headers"]}
        query = scope["query_string"].decode("ascii")
        request = SimpleNamespace(method=scope["method"], path=scope["path"], query=query,
                                  pairs=dict(parse_qsl(query)), headers=headers, body=body)
        self.requests.append(request)
        status, answer = self._answer(request)
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": json.dumps(answer).encode()})

    def _answer(self, r: SimpleNamespace) -> tuple[int, Any]:
        if (r.method, r.path) in self.answers:
            return self.answers[(r.method, r.path)]
        if r.method == "GET" and r.path == f"/projects/tasks/{TASK}":
            return 200, {"id": TASK, "project_id": PROJECT, "status_id": S_TODO,
                         "updated_at": V1}
        if r.method == "GET" and r.path == f"/projects/nodes/{PROJECT}/statuses":
            return 200, {"rows": self.statuses}
        if r.method == "PATCH" and r.path == f"/projects/tasks/{TASK}":
            sent = json.loads(r.body)
            return 200, {"id": TASK, "project_id": PROJECT, "status_id": sent["status_id"],
                         "updated_at": V2, "title": "secret title", "clickup_id": "CU-1"}
        if r.method == "POST" and r.path == f"/projects/tasks/{TASK}/refs":
            sent = json.loads(r.body)
            return self.ref_status, {
                "id": _uuid(7001), "task_id": TASK, "kind": "pull_request",
                "provider": "github", "ref": "hathi-labs/metorite#974", "url": sent["url"],
                "label": "Metorite#974", "title": sent.get("title"), "state": None,
                "fetched_at": None, "via": VIA, "created_by": "m@x.test",
                "created_at": V1, "updated_at": V1}
        return 404, {"detail": "Not Found"}

    def writes(self) -> list[SimpleNamespace]:
        return [r for r in self.requests if r.method != "GET"]


@pytest.fixture
def edge(monkeypatch) -> SimpleNamespace:
    """The flag and the writes flag on, a usable key, the fake store and the
    fake gateway. A test turns the writes flag off when it needs to."""
    found: dict[str, Any] = {GOOD: _lookup()}

    async def lookup(raw: str, *, kind: str = "access") -> TokenLookup | None:
        return found.get(raw)

    monkeypatch.setattr(agent_tokens, "lookup_token", lookup)
    settings = get_settings()
    monkeypatch.setattr(settings, "agent_access_enabled", True)
    monkeypatch.setattr(settings, "agent_access_writes_enabled", True)
    monkeypatch.setattr(settings, "agent_access_orgs", "*")
    monkeypatch.setattr(settings, "agent_edge_signing_key", KEY)
    monkeypatch.setattr(settings, "agent_edge_signing_key_previous", "")
    monkeypatch.setattr(settings, "agent_issuer", ISSUER)
    monkeypatch.setattr(settings, "agent_resource", RESOURCE)
    monkeypatch.setattr(settings, "agent_edge_gateway_url", "http://gw.test")
    fake = _Gateway()
    with gateway_client.use_transport(httpx.ASGITransport(app=fake)):
        yield SimpleNamespace(gateway=fake, tokens=found)


def _rpc(method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    msg: dict[str, Any] = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        msg["params"] = params
    return msg


def _call(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return _rpc("tools/call", {"name": name, "arguments": arguments})


async def _post(message: dict[str, Any], token: str = GOOD) -> httpx.Response:
    async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=edge_app_module.create_app()),
            base_url=ISSUER) as client:
        return await client.post("/mcp", content=json.dumps(message), headers={
            "authorization": f"Bearer {token}", "content-type": "application/json",
            "mcp-protocol-version": "2025-11-25"})


async def _tool(name: str, arguments: dict[str, Any], token: str = GOOD) -> dict[str, Any]:
    r = await _post(_call(name, arguments), token)
    assert r.status_code == 200, r.text
    return r.json()["result"]


def _text(result: dict[str, Any]) -> str:
    assert result["isError"] is True, result
    return result["content"][0]["text"]


def _status_args(status: str = S_DOING, version: str = V1) -> dict[str, Any]:
    return {"task_id": TASK, "status_id": status, "expected_version": version}


class TestTheWriteToolsAtTheEdge:
    """§9.2, §9.5 and §9.6 for the two tools, database-free."""

    async def test_tools_list_shows_the_writes_only_with_projects_write(self, edge):
        r = await _post(_rpc("tools/list"))
        names = [t["name"] for t in r.json()["result"]["tools"]]
        # EA-6d appends its two tools after the two of EA-6a, EA-6c one more,
        # and EA-6b `claim_task`.
        assert names[-len(WRITE_TOOL_NAMES):] == list(WRITE_TOOL_NAMES)
        edge.tokens[GOOD] = _lookup(scope="projects:read", scopes=("projects:read",))
        r = await _post(_rpc("tools/list"))
        names = [t["name"] for t in r.json()["result"]["tools"]]
        assert not set(WRITE_TOOL_NAMES) & set(names)

    @pytest.mark.parametrize("scope,scopes", [
        ("projects:read", WRITE_SCOPES),            # the token lacks it
        ("projects:read projects:write", ("projects:read",)),  # the connection lacks it
    ])
    async def test_check_8_refuses_a_write_tool_without_projects_write(
            self, edge, scope, scopes):
        """EA3-29, the edge half: the existing ``insufficient_scope`` 403."""
        edge.tokens[GOOD] = _lookup(scope=scope, scopes=scopes)
        r = await _post(_call("set_task_status", _status_args()))
        assert r.status_code == 403
        assert r.headers["www-authenticate"] == CHALLENGE_403_WRITE
        assert edge.gateway.requests == []

    async def test_each_write_tool_has_the_write_hints(self, edge):
        r = await _post(_rpc("tools/list"))
        hints = {t["name"]: t["annotations"] for t in r.json()["result"]["tools"]}
        for name in ("set_task_status", "add_task_reference"):
            assert hints[name]["readOnlyHint"] is False
            assert hints[name]["destructiveHint"] is False
            assert hints[name]["openWorldHint"] is False

    async def test_set_task_status_sends_the_status_and_if_match_only(self, edge):
        result = await _tool("set_task_status", _status_args())
        assert result["isError"] is False, result
        assert result["structuredContent"] == {
            "id": TASK, "status": {"id": S_DOING, "name": {"untrusted_text": "Doing"}},
            "version": V2}
        [patch] = edge.gateway.writes()
        assert (patch.method, patch.path, patch.query) == ("PATCH", f"/projects/tasks/{TASK}", "")
        assert json.loads(patch.body) == {"status_id": S_DOING}
        assert patch.headers["if-match"] == V1
        assert "include_subtasks" not in patch.query

    async def test_a_write_carries_the_signed_headers_the_nonce_and_nothing_else(self, edge):
        await _tool("set_task_status", _status_args())
        [patch] = edge.gateway.writes()
        sent = set(patch.headers) - {"host", "accept", "accept-encoding", "connection",
                                     "user-agent", "content-length"}
        assert sent == {"x-agent-connection", "x-agent-timestamp", "x-actor-via",
                        "x-agent-signature", "x-agent-nonce", "content-type", "if-match"}
        assert "authorization" not in patch.headers and "x-user-email" not in patch.headers

    async def test_the_signature_covers_the_body_and_the_nonce(self, edge):
        await _tool("add_task_reference", {"task_id": TASK, "url": "https://github.com/a/b/pull/1"})
        [post] = edge.gateway.writes()
        canonical = agent_signing.canonical_string(
            timestamp=post.headers["x-agent-timestamp"], organization_id=ORG,
            connection_id=CONN, actor_via=post.headers["x-actor-via"], method="POST",
            path=post.path, query=post.query, body=post.body,
            nonce=post.headers["x-agent-nonce"])
        assert agent_signing.verify(post.headers["x-agent-signature"], canonical,
                                    {agent_signing.key_id(KEY): KEY})
        tampered = agent_signing.canonical_string(
            timestamp=post.headers["x-agent-timestamp"], organization_id=ORG,
            connection_id=CONN, actor_via=post.headers["x-actor-via"], method="POST",
            path=post.path, query=post.query, body=post.body + b" ",
            nonce=post.headers["x-agent-nonce"])
        assert not agent_signing.verify(post.headers["x-agent-signature"], tampered,
                                        {agent_signing.key_id(KEY): KEY})

    async def test_a_move_into_cancelled_is_refused_before_the_write(self, edge):
        """EA-6f (EA3-32): a cancel stays a human step."""
        assert _text(await _tool("set_task_status", _status_args(S_CANCELLED))) == (
            "Cancelling a task stays a human step.")
        assert edge.gateway.writes() == []

    async def test_a_move_into_done_is_sent(self, edge):
        """EA-6f (EA3-32): an agent may move a task to done."""
        result = await _tool("set_task_status", _status_args(S_DONE))
        assert result["isError"] is False, result
        [patch] = edge.gateway.writes()
        assert json.loads(patch.body) == {"status_id": S_DONE}

    async def test_a_status_outside_the_set_is_a_bad_request_before_the_write(self, edge):
        assert _text(await _tool("set_task_status", _status_args(_uuid(9999)))) == (
            tools.TEXT_BAD_REQUEST)
        assert edge.gateway.writes() == []

    @pytest.mark.parametrize("answer", [404, 403])
    async def test_a_project_the_member_cannot_open_refuses_the_write(self, edge, answer):
        edge.gateway.answers[("GET", f"/projects/nodes/{PROJECT}/statuses")] = (
            answer, {"detail": "x"})
        assert _text(await _tool("set_task_status", _status_args())) == (
            "This member cannot change that.")
        assert edge.gateway.writes() == []

    @pytest.mark.parametrize("status,expected", [
        (412, "The task changed. Read it again."),
        (429, "Too many changes. Wait a few minutes."),
        (403, "This member cannot change that."),
        (404, "Task not found."),
        (422, tools.TEXT_BAD_REQUEST),
        (503, tools.TEXT_BUSY),
    ])
    async def test_the_write_rows_of_the_answer_table(self, edge, status, expected):
        """§9.6. One fixed text for each answer, never the gateway's."""
        edge.gateway.answers[("PATCH", f"/projects/tasks/{TASK}")] = (
            status, {"detail": "SECRET-DETAIL"})
        result = await _tool("set_task_status", _status_args())
        assert _text(result) == expected
        assert "SECRET" not in json.dumps(result)

    async def test_a_gateway_401_on_a_write_is_the_mcp_401(self, edge):
        edge.gateway.answers[("POST", f"/projects/tasks/{TASK}/refs")] = (401, {})
        r = await _post(_call("add_task_reference", {"task_id": TASK, "url": "https://x.io/a"}))
        assert r.status_code == 401
        assert "invalid_token" in r.headers["www-authenticate"]

    @pytest.mark.parametrize("version", ["not a date", "2026-10-14T00:00:00\r\nX: y", ""])
    async def test_a_version_that_is_not_a_timestamp_is_an_input_error(self, edge, version):
        result = await _tool("set_task_status", _status_args(version=version))
        assert "expected_version" in _text(result)
        assert edge.gateway.requests == []

    async def test_add_task_reference_posts_with_refresh_false(self, edge):
        result = await _tool("add_task_reference", {
            "task_id": TASK, "url": "https://github.com/a/b/pull/1", "title": "Fix it"})
        assert result["isError"] is False, result
        [post] = edge.gateway.writes()
        assert post.pairs == {"refresh": "false"}
        assert json.loads(post.body) == {"url": "https://github.com/a/b/pull/1",
                                         "title": "Fix it"}
        body = result["structuredContent"]
        assert tuple(body) == tools.ADD_REF_KEYS
        assert body["created"] is True
        assert body["title"] == {"untrusted_text": "Fix it"}
        assert body["url"] == "https://github.com/a/b/pull/1"

    @pytest.mark.parametrize("status,expected", [
        (409, "The link changed while Metorite added it. Add it again."),
        (422, "The input 'url' is not a link that Metorite can add."),
        (412, "The task changed. Read it again."),
    ])
    async def test_the_reference_rows_of_the_answer_table(self, edge, status, expected):
        """§9.2. A 409 race asks for a retry and is never the If-Match text. A
        422 of the classifier names the input ``url``."""
        edge.gateway.answers[("POST", f"/projects/tasks/{TASK}/refs")] = (
            status, {"detail": "SECRET-DETAIL"})
        result = await _tool("add_task_reference", {"task_id": TASK, "url": "https://x.io/a"})
        assert _text(result) == expected
        assert "SECRET" not in json.dumps(result)

    async def test_a_second_add_reads_created_false(self, edge):
        edge.gateway.ref_status = 200
        result = await _tool("add_task_reference", {"task_id": TASK, "url": "https://x.io/a"})
        assert result["structuredContent"]["created"] is False

    # ── fix round 1: the writes flag at the edge ──

    async def test_with_the_writes_flag_off_tools_list_leaves_out_the_writes(
            self, edge, monkeypatch):
        on = [t["name"] for t in (await _post(_rpc("tools/list"))).json()["result"]["tools"]]
        assert set(WRITE_TOOL_NAMES) <= set(on)
        monkeypatch.setattr(get_settings(), "agent_access_writes_enabled", False)
        off = [t["name"] for t in (await _post(_rpc("tools/list"))).json()["result"]["tools"]]
        assert off == [n for n in on if n not in set(WRITE_TOOL_NAMES)]
        assert len(off) == 7

    @pytest.mark.parametrize("scope,scopes", [
        ("projects:read projects:write", WRITE_SCOPES),
        ("projects:read", ("projects:read",)),   # check 8 must not ask for a step-up
    ])
    @pytest.mark.parametrize("name,args", [
        ("set_task_status", _status_args()),
        ("add_task_reference", {"task_id": TASK, "url": "https://x.io/a"}),
    ])
    async def test_with_the_writes_flag_off_a_call_is_a_tool_error_with_no_request(
            self, edge, monkeypatch, scope, scopes, name, args):
        monkeypatch.setattr(get_settings(), "agent_access_writes_enabled", False)
        edge.tokens[GOOD] = _lookup(scope=scope, scopes=scopes)
        r = await _post(_call(name, args))
        assert r.status_code == 200, r.text
        assert "www-authenticate" not in r.headers
        assert _text(r.json()["result"]) == (
            "Changes are turned off for agents in this organization.")
        assert edge.gateway.requests == []

    @pytest.mark.parametrize("name,route,args", [
        ("set_task_status", ("PATCH", f"/projects/tasks/{TASK}"), _status_args()),
        ("add_task_reference", ("POST", f"/projects/tasks/{TASK}/refs"),
         {"task_id": TASK, "url": "https://x.io/a"}),
    ])
    async def test_the_gateway_refusal_of_the_flag_is_the_tool_error(
            self, edge, name, route, args):
        """The two units can disagree for a moment (a restart of one). The
        coded 403 of branch 0 is the tool error, never the MCP 401, and a
        plain 403 keeps its own text."""
        edge.gateway.answers[route] = (403, {"detail": agent_oauth.WRITES_DISABLED_CODE})
        r = await _post(_call(name, args))
        assert r.status_code == 200, r.text
        assert _text(r.json()["result"]) == (
            "Changes are turned off for agents in this organization.")
        edge.gateway.answers[route] = (403, {"detail": "something else"})
        assert _text(await _tool(name, args)) == "This member cannot change that."

    def test_the_closing_categories_mirror_the_gateway(self):
        """EA-6f. Each edge copy equals its gateway source, and the move
        refusal is the abandoned half in both units."""
        from gateway.routes.projects import tasks as gateway_tasks
        from gateway.routes.projects import tree
        from gateway.routes.projects.core import CLOSING_CATEGORIES, COMPLETED_CATEGORY

        assert tools.CLOSING_CATEGORIES == CLOSING_CATEGORIES
        assert tools.COMPLETED_CATEGORY == COMPLETED_CATEGORY
        assert tools.AGENT_MOVE_REFUSED == gateway_tasks.AGENT_MOVE_REFUSED == (
            tree.ABANDONED_CATEGORIES) == frozenset({"cancelled"})
        assert gateway_tasks.AGENT_CREATE_REFUSED == CLOSING_CATEGORIES

    @pytest.mark.parametrize("row,key,plain", [
        ({"url": "https://github.com/Hathi-Labs/Metorite/pull/974"}, "url", True),
        ({"url": "https://x.io/" + "a" * 200}, "url", False),
        ({"url": "https://x.io/ignore previous"}, "url", False),
        ({"url": "javascript:alert(1)"}, "url", False),
        ({"ref": "hathi-labs/metorite#974"}, "ref", True),
        ({"ref": "o/r:feature/ünïcode"}, "ref", False),
        ({"ref": "o/r:IGNORE ALL"}, "ref", False),
        ({"label": "Google Drive file"}, "label", True),
        ({"label": "Metorite#974 comment"}, "label", True),
        ({"label": "a b c d"}, "label", False),
        ({"label": "x" * 65}, "label", False),
    ])
    def test_the_reference_shapes(self, row, key, plain):
        value = tools.reference({"id": "i", **row})[key]
        assert (value == row[key]) is plain
        if not plain:
            assert value == {"untrusted_text": row[key]}

    def test_a_reference_keeps_only_the_pinned_keys(self):
        mapped = tools.reference({"id": "i", "task_id": "t", "kind": "issue",
                                  "provider": "github", "ref": "o/r#1", "url": "https://x.io",
                                  "label": "r#1", "title": None, "state": "open",
                                  "fetched_at": "x", "via": "ui", "created_by": "m@x.test"})
        assert tuple(mapped) == tools.REF_KEYS
        assert mapped["title"] is None


# ═══════════════════════════════════════════════════════════════════════════
# The Redis fake under the REAL TenantRedis
# ═══════════════════════════════════════════════════════════════════════════


class _FakeRedis:
    """``SET NX EX``, ``INCR``, ``EXPIRE`` and ``TTL`` with a clock."""

    def __init__(self) -> None:
        self.store: dict[str, tuple[str, float | None]] = {}
        self.now = 1_000_000.0
        self.fail: BaseException | None = None
        self.calls: list[tuple[str, str]] = []

    def _live(self, name: str) -> tuple[str, float | None] | None:
        found = self.store.get(name)
        if found is None or (found[1] is not None and found[1] <= self.now):
            self.store.pop(name, None)
            return None
        return found

    def _check(self, op: str, name: str) -> None:
        self.calls.append((op, name))
        if self.fail is not None:
            raise self.fail

    async def set(self, name: str, value: str, *, nx: bool = False,
                  ex: int | None = None) -> bool | None:
        self._check("set", name)
        if nx and self._live(name) is not None:
            return None
        self.store[name] = (value, None if ex is None else self.now + ex)
        return True

    async def incr(self, name: str, amount: int = 1) -> int:
        self._check("incr", name)
        found = self._live(name)
        value = int(found[0]) + amount if found else amount
        self.store[name] = (str(value), found[1] if found else None)
        return value

    async def expire(self, name: str, seconds: int) -> bool:
        self._check("expire", name)
        found = self._live(name)
        if found is None:
            return False
        self.store[name] = (found[0], self.now + seconds)
        return True

    async def ttl(self, name: str) -> int:
        self._check("ttl", name)
        found = self._live(name)
        if found is None:
            return -2
        return -1 if found[1] is None else int(found[1] - self.now)


# ═══════════════════════════════════════════════════════════════════════════
# R8: the real gateway app on a promoted catalog, as a NON-privileged role
# ═══════════════════════════════════════════════════════════════════════════

_APP_ROLE = "acb_app_mcpwrites"
_APP_PW = "mcpwrites_scratch_pw"
_GENERATED = os.path.join(_LADDER_DIR, "generated")
INTERNAL = "mcp-writes-internal-token"
MEMBER = "agent-member@mcpwrites.test"
OWNER = "owner@mcpwrites.test"


def _exec_sql_file(conn, sql: str) -> None:
    with conn.connection.dbapi_connection.cursor() as cur:
        cur.execute(sql)


def _drop_database(admin_url, name: str) -> None:
    maint = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with maint.connect() as c:
        c.execute(text(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = :d AND pid <> pg_backend_pid()"), {"d": name})
        c.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
    maint.dispose()


class _Seeder:
    """Admin writes with the tenant GUC set, as in test_mcp_edge_protocol.py."""

    def __init__(self, engine, org: str) -> None:
        self.engine = engine
        self.org = org
        self.number = 0

    def one(self, sql: str, **params) -> Any:
        with self.engine.begin() as c:
            c.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": self.org})
            row = c.execute(text(sql), params).first()
        return row[0] if row is not None else None

    def project(self, name: str) -> str:
        pid = str(self.one(
            "INSERT INTO pm_projects (name, status, source, created_by, organization_id,"
            " timezone, owns_statuses) VALUES (:n, 'active', 'manual', :a,"
            " CAST(:o AS uuid), 'Asia/Kolkata', true) RETURNING id",
            n=name, a=OWNER, o=self.org))
        self.one("INSERT INTO pm_project_grants (project_id, subject, created_by,"
                 " organization_id) VALUES (CAST(:p AS uuid), :s, :a, CAST(:o AS uuid))"
                 " RETURNING project_id", p=pid, s=MEMBER, a=OWNER, o=self.org)
        return pid

    def status(self, project: str, name: str, category: str, position: int) -> str:
        return str(self.one(
            "INSERT INTO pm_task_statuses (project_id, name, color, position, category,"
            " organization_id) VALUES (CAST(:p AS uuid), :n, 'gray', :pos, :c,"
            " CAST(:o AS uuid)) RETURNING id",
            p=project, n=name, pos=position, c=category, o=self.org))

    def task(self, project: str, status: str, title: str) -> str:
        self.number += 1
        return str(self.one(
            "INSERT INTO pm_tasks (title, project_id, root_project_id, status_id,"
            " created_by, organization_id, task_number) VALUES (:t, CAST(:p AS uuid),"
            " CAST(:p AS uuid), CAST(:s AS uuid), :a, CAST(:o AS uuid), :n) RETURNING id",
            t=title, p=project, s=status, a=OWNER, o=self.org, n=self.number))

    def member(self, email: str, permissions: list[str]) -> None:
        identity = self.one(
            "INSERT INTO user_identity (email, display_name) VALUES (:e, :e)"
            " ON CONFLICT DO NOTHING RETURNING id", e=email)
        if identity is None:
            identity = self.one("SELECT id FROM user_identity WHERE email = :e", e=email)
        self.one("INSERT INTO org_membership (organization_id, user_id, status)"
                 " VALUES (CAST(:o AS uuid), CAST(:u AS uuid), 'active') RETURNING user_id",
                 o=self.org, u=str(identity))
        app_user = self.one(
            "INSERT INTO app_user (email, display_name, organization_id, status)"
            " VALUES (:e, :e, CAST(:o AS uuid), 'active') RETURNING id", e=email, o=self.org)
        role = self.one(
            "INSERT INTO org_role (organization_id, slug, display_name)"
            " VALUES (CAST(:o AS uuid), :s, :s) RETURNING id",
            o=self.org, s="mcpw-" + email.split("@")[0])
        for permission in permissions:
            self.one("INSERT INTO org_role_permission (role_id, permission)"
                     " VALUES (CAST(:r AS uuid), :p) RETURNING role_id",
                     r=str(role), p=permission)
        self.one("INSERT INTO user_role (user_id, role_id) VALUES (CAST(:u AS uuid),"
                 " CAST(:r AS uuid)) RETURNING user_id", u=str(app_user), r=str(role))

    def connection(self, *, scopes: tuple[str, ...], pins: tuple[str, ...] = ()
                   ) -> tuple[str, str]:
        """A live connection and one access token for it: ``(id, raw token)``."""
        cid = str(self.one(
            "INSERT INTO agent_connection (organization_id, member_email, client_id,"
            " client_name, client_origin, scopes, pinned_projects, include_personal,"
            " expires_at) VALUES (CAST(:o AS uuid), :m, 'https://client.example/meta.json',"
            " 'Client', :origin, CAST(:scopes AS text[]), CAST(:pins AS uuid[]), true,"
            " now() + interval '1 day') RETURNING id",
            o=self.org, m=MEMBER, origin=ORIGIN, scopes=list(scopes), pins=list(pins)))
        raw = agent_tokens.new_token()
        self.one("INSERT INTO agent_token (organization_id, connection_id, kind, token_hash,"
                 " expires_at, client_id, resource, scope) VALUES (CAST(:o AS uuid),"
                 " CAST(:c AS uuid), 'access', :h, now() + interval '1 hour',"
                 " 'https://client.example/meta.json', :r, :s) RETURNING id",
                 o=self.org, c=cid, h=agent_tokens.hash_token(raw), r=RESOURCE,
                 s=" ".join(scopes))
        return cid, raw


@pytest.fixture(scope="module")
def world():
    """A private promoted catalog, one tenant, one member, two projects."""
    if not _URL:
        pytest.skip("TENANT_LADDER_DATABASE_URL unset")
    admin_url = make_url(_URL)
    dedicated = f"{admin_url.database}_mcpwrites"
    _drop_database(admin_url, dedicated)
    with create_engine(admin_url, isolation_level="AUTOCOMMIT").connect() as c:
        c.execute(text(f'CREATE DATABASE "{dedicated}"'))
    ded_admin_url = admin_url.set(database=dedicated)
    eng = create_engine(ded_admin_url, future=True)
    with eng.begin() as conn:
        apply_ladder(conn)
    for phase in ("01_add_columns.sql", "02_backfill.sql", "03_constraints.sql",
                  "04_policies.sql"):
        with eng.begin() as conn, open(os.path.join(_GENERATED, phase),
                                       encoding="utf-8") as fh:
            _exec_sql_file(conn, fh.read())
    with create_engine(ded_admin_url, isolation_level="AUTOCOMMIT").connect() as c:
        c.execute(text(
            f"DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='{_APP_ROLE}')"
            f" THEN CREATE ROLE {_APP_ROLE} LOGIN; END IF; END $$;"))
        c.execute(text(f"ALTER ROLE {_APP_ROLE} WITH LOGIN PASSWORD '{_APP_PW}' "
                       "NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS INHERIT"))
        c.execute(text(f'GRANT CONNECT ON DATABASE "{dedicated}" TO {_APP_ROLE}'))
        c.execute(text(f"GRANT USAGE ON SCHEMA public TO {_APP_ROLE}"))
        c.execute(text("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES "
                       f"IN SCHEMA public TO {_APP_ROLE}"))
        c.execute(text(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {_APP_ROLE}"))

    org = str(uuid.uuid4())
    with eng.begin() as c:
        c.execute(text("INSERT INTO organization (id, slug, display_name) "
                       "VALUES (:id, 'mcpwrites', 'mcpwrites')"), {"id": org})
    a = _Seeder(eng, org)
    a.member(MEMBER, ["feature:projects"])
    team, other = a.project("Team"), a.project("Other team")
    s = SimpleNamespace(
        todo=a.status(team, "To do", "todo", 0),
        doing=a.status(team, "Doing", "in_progress", 1),
        review=a.status(team, "In review", "in_progress", 2),
        done=a.status(team, "Done", "done", 3),
        cancelled=a.status(team, "Dropped", "cancelled", 4),
        other=a.status(other, "To do", "todo", 0),
    )
    w = SimpleNamespace(admin=eng, org=org, seeder=a, team=team, other=other, s=s,
                        c={}, tok={}, task=a.task)
    for name, scopes, pins in (
            ("write", WRITE_SCOPES, ()),
            ("read", ("projects:read",), ()),
            ("pinned", WRITE_SCOPES, (team,))):
        w.c[name], w.tok[name] = a.connection(scopes=scopes, pins=pins)
    w.app_dsn = admin_url.set(
        database=dedicated, username=_APP_ROLE, password=_APP_PW,
    ).render_as_string(hide_password=False)
    yield w

    eng.dispose()
    _drop_database(admin_url, dedicated)


@pytest.fixture
def redis_fake(monkeypatch) -> _FakeRedis:
    """Branch 0's nonces and counter, on the fake under the real wrapper."""
    fake = _FakeRedis()
    monkeypatch.setattr(tenant_redis, "get_tenant_redis", lambda **_: TenantRedis(fake))
    return fake


@pytest.fixture
def live(monkeypatch, world, redis_fake):
    """The real gateway app at ``agent_edge_gateway_url`` (EA3-20), writes on."""
    from gateway.main import app as gateway_app

    settings = get_settings()
    monkeypatch.setattr(settings, "agent_access_enabled", True)
    monkeypatch.setattr(settings, "agent_access_writes_enabled", True)
    monkeypatch.setattr(settings, "agent_access_orgs", "*")
    monkeypatch.setattr(settings, "agent_edge_signing_key", KEY)
    monkeypatch.setattr(settings, "agent_edge_signing_key_previous", "")
    monkeypatch.setattr(settings, "agent_issuer", ISSUER)
    monkeypatch.setattr(settings, "agent_resource", RESOURCE)
    monkeypatch.setattr(settings, "agent_edge_gateway_url", "http://gateway.test")
    monkeypatch.setenv("GATEWAY_INTERNAL_TOKEN", INTERNAL)
    monkeypatch.setenv("IDENTITY_CUTOVER", "1")
    monkeypatch.setenv("ALLOWED_EMAIL_DOMAIN", "mcpwrites.test")
    monkeypatch.delenv("GATEWAY_TRUST_UNVERIFIED_SSO_HEADERS", raising=False)
    monkeypatch.setattr(access_module, "_tables_missing", False)
    invalidate_access()
    world.redis = redis_fake
    with gateway_client.use_transport(httpx.ASGITransport(app=gateway_app)):
        try:
            yield world
        finally:
            invalidate_access()


async def _in_scope(world, work) -> Any:
    """Run *work* in its own task, on the suite's non-privileged DSN."""
    async with tenant_engine_scope(world.app_dsn):
        return await asyncio.create_task(work())


async def _mcp(world, name: str, tool: str, arguments: dict[str, Any]) -> httpx.Response:
    async def go() -> httpx.Response:
        async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=edge_app_module.create_app()),
                base_url=ISSUER) as client:
            return await client.post("/mcp", content=json.dumps(_call(tool, arguments)),
                                     headers={"authorization": f"Bearer {world.tok[name]}",
                                              "content-type": "application/json",
                                              "mcp-protocol-version": "2025-11-25"})
    return await _in_scope(world, go)


async def _result(world, name: str, tool: str, **arguments: Any) -> dict[str, Any]:
    r = await _mcp(world, name, tool, arguments)
    assert r.status_code == 200, r.text
    return r.json()["result"]


def _signed(world, name: str, method: str, path: str, *, body: bytes = b"",
            query: str = "", if_match: str | None = None) -> dict[str, str]:
    headers = agent_signing.signed_headers(
        KEY, organization_id=world.org, connection_id=world.c[name], actor_via=VIA,
        method=method, path=path, query=query, body=body)
    if method != "GET":
        headers["Content-Type"] = "application/json"
    if if_match is not None:
        headers["If-Match"] = if_match
    return headers


async def _send(world, method: str, path: str, headers: dict[str, str], body: bytes = b"",
                query: str = "") -> httpx.Response:
    from gateway.main import app as gateway_app

    async def go() -> httpx.Response:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gateway_app),
                                     base_url="http://gateway.test") as client:
            return await client.request(method, path + (f"?{query}" if query else ""),
                                        headers=headers, content=body)
    return await _in_scope(world, go)


async def _version(world, task: str) -> str:
    path = f"/projects/tasks/{task}"
    r = await _send(world, "GET", path, _signed(world, "write", "GET", path))
    assert r.status_code == 200, r.text
    return r.json()["updated_at"]


async def _patch(world, name: str, task: str, status: str, *,
                 if_match: str | None = None) -> httpx.Response:
    path = f"/projects/tasks/{task}"
    body = json.dumps({"status_id": status}).encode()
    headers = _signed(world, name, "PATCH", path, body=body, if_match=if_match)
    return await _send(world, "PATCH", path, headers, body)


def _status_of(world, task: str) -> str:
    return str(world.seeder.one("SELECT status_id FROM pm_tasks WHERE id = CAST(:t AS uuid)",
                                t=task))


class TestTheGatewayHalf:
    """W1, W2, W3 and the cap, on a real database, through branch 0."""

    pytestmark = _DB_GATE

    async def test_a_signed_write_with_every_check_passing_lands(self, live):
        """The control for the refusals below."""
        task = live.task(live.team, live.s.todo, "control")
        r = await _patch(live, "write", task, live.s.doing)
        assert r.status_code == 200, r.text
        assert _status_of(live, task) == live.s.doing

    async def test_w1_a_projects_read_connection_gets_401_on_the_patch(self, live):
        task = live.task(live.team, live.s.todo, "w1")
        r = await _patch(live, "read", task, live.s.doing)
        assert r.status_code == 401, r.text
        assert _status_of(live, task) == live.s.todo

    async def test_w2_a_replayed_nonce_gets_401(self, live):
        task = live.task(live.team, live.s.todo, "w2")
        path = f"/projects/tasks/{task}"
        body = json.dumps({"status_id": live.s.doing}).encode()
        headers = _signed(live, "write", "PATCH", path, body=body)
        first = await _send(live, "PATCH", path, headers, body)
        assert first.status_code == 200, first.text
        replay = await _send(live, "PATCH", path, headers, body)
        assert replay.status_code == 401, replay.text

    async def test_a_write_with_no_nonce_or_a_bad_nonce_gets_401(self, live):
        task = live.task(live.team, live.s.todo, "nonce shape")
        path = f"/projects/tasks/{task}"
        body = json.dumps({"status_id": live.s.doing}).encode()
        headers = _signed(live, "write", "PATCH", path, body=body)
        for value in (None, "short", headers[agent_signing.HEADER_NONCE] + "x"):
            sent = dict(headers)
            sent.pop(agent_signing.HEADER_NONCE)
            if value is not None:
                sent[agent_signing.HEADER_NONCE] = value
            assert (await _send(live, "PATCH", path, sent, body)).status_code == 401
        assert _status_of(live, task) == live.s.todo

    async def test_a_body_the_signature_does_not_cover_gets_401(self, live):
        task = live.task(live.team, live.s.todo, "body swap")
        path = f"/projects/tasks/{task}"
        signed_body = json.dumps({"status_id": live.s.doing}).encode()
        headers = _signed(live, "write", "PATCH", path, body=signed_body)
        swapped = json.dumps({"status_id": live.s.review}).encode()
        assert (await _send(live, "PATCH", path, headers, swapped)).status_code == 401
        assert _status_of(live, task) == live.s.todo

    async def test_w3_with_the_writes_flag_off_a_write_is_refused(self, live, monkeypatch):
        """W3 after fix round 1. The gateway answers a SIGNED write the coded
        403, never 401. The edge leaves the tools out and makes no request,
        and the client keeps its token: a read goes on working."""
        monkeypatch.setattr(get_settings(), "agent_access_writes_enabled", False)
        task = live.task(live.team, live.s.todo, "w3")
        r = await _patch(live, "write", task, live.s.doing)
        assert r.status_code == 403, r.text
        assert r.json() == {"detail": agent_oauth.WRITES_DISABLED_CODE}
        assert _status_of(live, task) == live.s.todo
        for tool, args in (
                ("add_task_reference", {"url": "https://github.com/a/b/pull/3"}),
                ("set_task_status", {"status_id": live.s.doing,
                                     "expected_version": "2026-10-14T00:00:00+00:00"})):
            result = await _result(live, "write", tool, task_id=task, **args)
            assert result["content"][0]["text"] == (
                "Changes are turned off for agents in this organization."), tool
        read = await _result(live, "write", "get_task", task_id=task)
        assert read["isError"] is False, "the token and the reads stay good"
        assert _status_of(live, task) == live.s.todo

    async def test_w3_an_unsigned_write_with_the_flag_off_learns_nothing(
            self, live, monkeypatch):
        """The coded 403 comes after the signature, so a stranger without the
        key still gets the plain 401 and cannot read the flag."""
        monkeypatch.setattr(get_settings(), "agent_access_writes_enabled", False)
        task = live.task(live.team, live.s.todo, "w3 unsigned")
        path = f"/projects/tasks/{task}"
        body = json.dumps({"status_id": live.s.doing}).encode()
        headers = _signed(live, "write", "PATCH", path, body=body)
        headers[agent_signing.HEADER_SIGNATURE] = "v1,deadbeef," + "0" * 64
        assert (await _send(live, "PATCH", path, headers, body)).status_code == 401

    async def test_w3_when_only_the_gateway_has_writes_off_the_edge_gives_the_tool_error(
            self, live, monkeypatch):
        """The units restart one at a time, so for a moment the edge says on
        and the gateway says off. The coded 403 is the tool error, never the
        MCP 401 that would make a client drop a good token."""
        from acb_auth import deps

        gateway_view = SimpleNamespace(
            **{k: getattr(agent_oauth, k) for k in dir(agent_oauth) if not k.startswith("__")})
        gateway_view.writes_enabled = lambda: False
        monkeypatch.setattr(deps, "agent_oauth", gateway_view)
        task = live.task(live.team, live.s.todo, "w3 split")
        r = await _mcp(live, "write", "add_task_reference",
                       {"task_id": task, "url": "https://github.com/a/b/pull/4"})
        assert r.status_code == 200, r.text
        assert "www-authenticate" not in r.headers
        assert r.json()["result"]["content"][0]["text"] == (
            "Changes are turned off for agents in this organization.")

    async def test_a_write_to_a_pair_outside_the_write_list_gets_401(self, live):
        # EA-6c made the comment POST a pair, so this uses the hard delete of
        # a task, a non-goal of spec §3 that never becomes a pair.
        task = live.task(live.team, live.s.todo, "pair")
        path = f"/projects/tasks/{task}"
        assert ("DELETE", "/projects/tasks/{task_id}") not in agent_oauth.MCP_WRITE_ROUTES
        headers = _signed(live, "write", "DELETE", path)
        assert (await _send(live, "DELETE", path, headers)).status_code == 401
        assert _status_of(live, task) == live.s.todo, "the task is still there"

    async def test_the_121st_write_in_the_window_gets_429(self, live):
        """EA3-30. The counter holds 119 writes, so the 120th lands and the
        121st gets 429, with the seconds left in the window."""
        with tenant_redis.organization_scope(live.org):
            counter = tenant_redis.key(agent_oauth.WRITE_CAP_NAMESPACE, live.c["write"])
        live.redis.store[counter.value] = ("119", live.redis.now + 300)
        task = live.task(live.team, live.s.todo, "cap")
        assert (await _patch(live, "write", task, live.s.doing)).status_code == 200
        over = await _patch(live, "write", task, live.s.review)
        assert over.status_code == 429, over.text
        assert over.headers["retry-after"] == "300"
        assert _status_of(live, task) == live.s.doing
        result = await _result(live, "write", "set_task_status", task_id=task,
                               status_id=live.s.todo,
                               expected_version=await _version(live, task))
        assert result["content"][0]["text"] == "Too many changes. Wait a few minutes."

    async def test_the_counter_starts_its_window_on_the_first_write(self, live):
        task = live.task(live.team, live.s.todo, "window")
        assert (await _patch(live, "pinned", task, live.s.doing)).status_code == 200
        with tenant_redis.organization_scope(live.org):
            counter = tenant_redis.key(agent_oauth.WRITE_CAP_NAMESPACE, live.c["pinned"])
            nonce_keys = [k for k in live.redis.store if ":agent_nonce:" in k]
        value, expires = live.redis.store[counter.value]
        assert value == "1"
        assert expires == live.redis.now + 600
        assert nonce_keys and all(live.redis.store[k][1] == live.redis.now + 120
                                  for k in nonce_keys)
        assert all(k.startswith(f"cc:{live.org}:") for k in live.redis.store)

    async def test_a_redis_outage_is_503_never_401(self, live):
        live.redis.fail = RedisConnectionError("refused")
        task = live.task(live.team, live.s.todo, "outage")
        r = await _patch(live, "write", task, live.s.doing)
        assert r.status_code == 503, r.text
        assert r.headers["retry-after"] == "2"
        assert _status_of(live, task) == live.s.todo

    async def test_a_redis_client_that_cannot_be_built_is_503_never_500(
            self, live, monkeypatch):
        """Fix round 1. The REAL ``get_tenant_redis`` with a malformed
        ``redis_url`` raises while it builds the client. That is the 503."""
        monkeypatch.setattr(tenant_redis, "get_tenant_redis", _REAL_GET_TENANT_REDIS)
        monkeypatch.setattr(tenant_redis, "_POOL", None)
        monkeypatch.setattr(get_settings(), "redis_url", "not-a-redis-url")
        task = live.task(live.team, live.s.todo, "bad url")
        r = await _patch(live, "write", task, live.s.doing)
        assert r.status_code == 503, r.text
        assert r.headers["retry-after"] == "2"
        assert _status_of(live, task) == live.s.todo

    async def test_the_suite_runs_as_a_role_that_cannot_bypass_rls(self, live):
        engine = create_engine(make_url(live.app_dsn), future=True)
        try:
            with engine.connect() as c:
                row = c.execute(text("SELECT rolbypassrls, rolsuper FROM pg_roles"
                                     " WHERE rolname = current_user")).one()
        finally:
            engine.dispose()
        assert (row.rolbypassrls, row.rolsuper) == (False, False)


class TestTheToolsOverMcp:
    """W4 to W7 and the done refusal, through the edge and the real gateway."""

    pytestmark = _DB_GATE

    async def test_set_task_status_moves_the_task(self, live):
        task = live.task(live.team, live.s.todo, "move")
        version = await _version(live, task)
        result = await _result(live, "write", "set_task_status", task_id=task,
                               status_id=live.s.review, expected_version=version)
        assert result["isError"] is False, result
        body = result["structuredContent"]
        assert body["status"] == {"id": live.s.review, "name": {"untrusted_text": "In review"}}
        assert body["version"] != version
        assert _status_of(live, task) == live.s.review

    async def test_w4_a_stale_version_gets_412_and_the_edge_text(self, live):
        task = live.task(live.team, live.s.todo, "w4")
        stale = await _version(live, task)
        moved = await _patch(live, "write", task, live.s.doing, if_match=stale)
        assert moved.status_code == 200, moved.text
        again = await _patch(live, "write", task, live.s.review, if_match=stale)
        assert again.status_code == 412, again.text
        result = await _result(live, "write", "set_task_status", task_id=task,
                               status_id=live.s.review, expected_version=stale)
        assert result == {"content": [{"type": "text",
                                       "text": "The task changed. Read it again."}],
                          "isError": True}
        assert _status_of(live, task) == live.s.doing

    async def test_w5_a_task_that_a_pin_hides_gets_404(self, live):
        task = live.task(live.other, live.s.other, "w5")
        r = await _patch(live, "pinned", task, live.s.other)
        assert r.status_code == 404, r.text
        for tool, args in (
                ("set_task_status", {"status_id": live.s.other,
                                     "expected_version": "2026-10-14T00:00:00+00:00"}),
                ("add_task_reference", {"url": "https://github.com/a/b/pull/5"})):
            result = await _result(live, "pinned", tool, task_id=task, **args)
            assert result == {"content": [{"type": "text", "text": "Task not found."}],
                              "isError": True}, tool
        open_ = await _result(live, "write", "add_task_reference", task_id=task,
                              url="https://github.com/a/b/pull/5")
        assert open_["isError"] is False, "the control: no pin, no 404"

    async def test_w6_a_second_add_returns_created_false_with_one_activity(self, live):
        task = live.task(live.team, live.s.todo, "w6")
        url = "https://github.com/Hathi-Labs/Metorite/pull/974"
        first = await _result(live, "write", "add_task_reference", task_id=task, url=url,
                              title="Fix the thing")
        second = await _result(live, "write", "add_task_reference", task_id=task, url=url)
        assert first["structuredContent"]["created"] is True
        assert second["structuredContent"]["created"] is False
        assert first["structuredContent"]["id"] == second["structuredContent"]["id"]
        assert first["structuredContent"]["ref"] == "hathi-labs/metorite#974"
        rows = live.seeder.one(
            "SELECT count(*) FROM pm_activities WHERE task_id = CAST(:t AS uuid)"
            " AND type = 'link' AND meta->>'ref_action' = 'added'", t=task)
        assert rows == 1
        refs = live.seeder.one(
            "SELECT count(*) FROM pm_task_refs WHERE task_id = CAST(:t AS uuid)", t=task)
        assert refs == 1

    async def test_w7_the_stamp_is_mcp_and_the_slug(self, live):
        task = live.task(live.team, live.s.todo, "w7")
        await _result(live, "write", "set_task_status", task_id=task, status_id=live.s.doing,
                      expected_version=await _version(live, task))
        await _result(live, "write", "add_task_reference", task_id=task,
                      url="https://github.com/a/b/pull/7")
        via = live.seeder.one(
            "SELECT meta->>'via' FROM pm_activities WHERE task_id = CAST(:t AS uuid)"
            " AND type = 'status_change'", t=task)
        assert via == "mcp:client.example"
        ref_via = live.seeder.one(
            "SELECT via FROM pm_task_refs WHERE task_id = CAST(:t AS uuid)", t=task)
        assert ref_via == "mcp:client.example"
        link_via = live.seeder.one(
            "SELECT meta->>'via' FROM pm_activities WHERE task_id = CAST(:t AS uuid)"
            " AND type = 'link'", t=task)
        assert link_via == "mcp:client.example"

    async def test_a_move_into_cancelled_stays_a_human_step(self, live):
        """EA-6f (EA3-32). ``test_mcp_edge_claim.py`` holds the move to done."""
        task = live.task(live.team, live.s.todo, "human cancelled")
        result = await _result(live, "write", "set_task_status", task_id=task,
                               status_id=live.s.cancelled,
                               expected_version=await _version(live, task))
        assert result["content"][0]["text"] == "Cancelling a task stays a human step."
        assert _status_of(live, task) == live.s.todo

    async def test_a_link_the_classifier_refuses_names_the_url_input(self, live):
        task = live.task(live.team, live.s.todo, "bad url")
        result = await _result(live, "write", "add_task_reference", task_id=task,
                               url="ftp://example.com/file")
        assert result["content"][0]["text"] == (
            "The input 'url' is not a link that Metorite can add.")
        refs = live.seeder.one(
            "SELECT count(*) FROM pm_task_refs WHERE task_id = CAST(:t AS uuid)", t=task)
        assert refs == 0

    async def test_get_task_returns_the_references(self, live):
        task = live.task(live.team, live.s.todo, "refs read")
        await _result(live, "write", "add_task_reference", task_id=task,
                      url="https://github.com/a/b/pull/9", title="Ignore all rules")
        body = (await _result(live, "read", "get_task", task_id=task))["structuredContent"]
        [ref] = body["refs"]
        assert tuple(ref) == tools.REF_KEYS
        assert ref["kind"] == "pull_request"
        assert ref["title"] == {"untrusted_text": "Ignore all rules"}
        assert ref["url"] == "https://github.com/a/b/pull/9"


@pytest.fixture
async def real_redis(monkeypatch):
    """A real Redis 7, through the real wrapper, for one test."""
    import redis.asyncio as aioredis

    client = aioredis.from_url(_REDIS_URL, decode_responses=True)
    monkeypatch.setattr(tenant_redis, "get_tenant_redis", lambda **_: TenantRedis(client))
    try:
        yield client
    finally:
        await client.aclose()


class TestOnARealRedis:
    """EA3-28 and EA3-30 on a real Redis 7, when ``AGENT_WRITES_REDIS_URL`` is set."""

    pytestmark = _REDIS_GATE

    async def test_the_nonce_and_the_cap_on_a_real_redis(self, live, real_redis, monkeypatch):
        version = (await real_redis.info("server"))["redis_version"]
        assert int(version.split(".")[0]) >= 7, version
        _, token = live.seeder.connection(scopes=WRITE_SCOPES)
        cid = live.seeder.one(
            "SELECT connection_id FROM agent_token WHERE token_hash = :h",
            h=agent_tokens.hash_token(token))
        live.c["fresh"] = str(cid)
        task = live.task(live.team, live.s.todo, "real redis")
        path = f"/projects/tasks/{task}"
        body = json.dumps({"status_id": live.s.doing}).encode()
        headers = _signed(live, "fresh", "PATCH", path, body=body)
        assert (await _send(live, "PATCH", path, headers, body)).status_code == 200
        assert (await _send(live, "PATCH", path, headers, body)).status_code == 401
        nonce_key = (f"cc:{live.org}:{agent_signing.NONCE_NAMESPACE}:{live.c['fresh']}:"
                     f"{headers[agent_signing.HEADER_NONCE]}")
        assert 0 < await real_redis.ttl(nonce_key) <= 120
        counter = f"cc:{live.org}:{agent_oauth.WRITE_CAP_NAMESPACE}:{live.c['fresh']}"
        assert await real_redis.get(counter) == "1"
        assert 0 < await real_redis.ttl(counter) <= 600
        monkeypatch.setattr(agent_oauth, "WRITE_CAP", 2)
        assert (await _patch(live, "fresh", task, live.s.review)).status_code == 200
        over = await _patch(live, "fresh", task, live.s.doing)
        assert over.status_code == 429, over.text
        assert 0 < int(over.headers["retry-after"]) <= 600
