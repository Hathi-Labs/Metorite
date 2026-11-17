"""The gateway's ``POST /v1/embeddings`` goes through OUR Router. Task 3553.

Spec: ``project-docs/specs/ai_tier_routing.md`` §2.10 and §11 S5 item 2 ·
``customer_console.md`` §6A.16 · **D57.7** (a routed call that fails, FAILS)
· owner directive 2026-11-16 (every AI call goes through the Console).

🔴 **Before this slice the door built an ``OpenAI`` client with
``OPENAI_API_KEY`` and called the vendor.** With no key it answered zero
vectors. Neither path met the meter. Now the route calls
``gateway.kb_index.embed.relay``, which makes the one
``embeddings_on_console`` call on ``tier-embed``.

⚠️ Hermetic. The subject is which client is called, what it is handed, and
which status the route answers. Two fakes:

* a Router spy that replaces ``console_resolve.embeddings_on_console``
  (the precedent is ``test_internal_ai_is_routed.py``),
* a fake HTTP transport behind the REAL Console client, so the
  verdict-or-outage line under test is the production one
  (the precedent is ``test_kb_chunk.py``).

The app runs the REAL default-deny gate with the real ``PUBLIC_ROUTES``.
The BILLING half is proved by the Console's own R8 suite,
``test_customer_console_embed.py``.
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any

import httpx
import pytest

pytest.importorskip("fastapi")

from acb_common.settings import get_settings
from fastapi import FastAPI
from fastapi.testclient import TestClient

_REPO = Path(__file__).resolve().parents[2]
_MAIN = _REPO / "apps/services/gateway/gateway/main.py"
_CONSOLE_MAIN = _REPO / "apps/services/customer_console/customer_console/main.py"

SERVICE = "service-token-for-embed-test"
LLM_KEY = "llm-key-for-embed-test"
MEMBER = "owner@fracktal.in"
CONSOLE_URL = "https://console.metorite.test"
ORG_KEY = "cc_live_embedtest_notarealsecret"
DEPLOYMENT_KEY = "cc_depl_embedtest_notarealsecret"

#: A Router answer in the Console door's own shape.
OK_BODY: dict[str, Any] = {
    "object": "list",
    "data": [{"object": "embedding", "index": 0, "embedding": [0.25] * 1536}],
    "model": "tier-embed",
    "usage": {"prompt_tokens": 2, "total_tokens": 2},
    "request_id": "rtr-test",
}

AS_MEMBER = {"Authorization": f"Bearer {SERVICE}", "X-User-Email": MEMBER}
AS_SERVICE = {"Authorization": f"Bearer {SERVICE}"}


# ── The fakes ────────────────────────────────────────────────────────────────


class RouterSpy:
    """Records what ``embeddings_on_console`` was handed, and answers."""

    def __init__(self, status: int = 200, body: Any = None,
                 error: BaseException | None = None) -> None:
        self.calls: list[tuple[dict, dict]] = []
        self._status = status
        self._body = OK_BODY if body is None else body
        self._error = error

    async def __call__(self, payload: dict, **kw: Any) -> tuple[int, Any]:
        self.calls.append((payload, kw))
        if self._error is not None:
            raise self._error
        return self._status, self._body


class FakeConsole:
    """A stand-in for the Console Router, behind the REAL client."""

    def __init__(self, status: int = 200, body: Any = None,
                 error: BaseException | None = None) -> None:
        self.status = status
        self.body = OK_BODY if body is None else body
        self.error = error
        self.requests: list[httpx.Request] = []

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return httpx.Response(self.status, json=self.body)

    def client(self, timeout: Any = None) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self._handle))


# ── The fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _box(monkeypatch: pytest.MonkeyPatch):
    """A box with a service token and an LLM key, and NO vendor reach.

    ``_with_resolved_access`` reads the access tables. It is the identity,
    so this test does not need it. The vendor verbs and clients raise, so a
    fallback to a vendor fails the test that takes it.
    """
    from acb_auth import deps

    monkeypatch.setenv("GATEWAY_INTERNAL_TOKEN", SERVICE)
    monkeypatch.setenv("LITELLM_MASTER_KEY", LLM_KEY)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-must-never-be-used")
    monkeypatch.setenv("CUSTOMER_CONSOLE_URL", CONSOLE_URL)
    monkeypatch.setenv("CUSTOMER_CONSOLE_ORG_KEY", ORG_KEY)
    monkeypatch.setenv("CUSTOMER_CONSOLE_DEPLOYMENT_KEY", "")
    monkeypatch.setenv("CUSTOMER_CONSOLE_ROUTER_USES_DEPLOYMENT_KEY", "false")
    get_settings.cache_clear()

    async def _identity(user: Any) -> Any:
        return user

    monkeypatch.setattr(deps, "_with_resolved_access", _identity)

    def _vendor(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("the embeddings door reached a vendor")

    try:
        import openai

        monkeypatch.setattr(openai, "OpenAI", _vendor)
        monkeypatch.setattr(openai, "AsyncOpenAI", _vendor)
    except ImportError:  # pragma: no cover - openai ships with the gateway
        pass
    try:
        import litellm

        monkeypatch.setattr(litellm, "aembedding", _vendor)
        monkeypatch.setattr(litellm, "embedding", _vendor)
    except ImportError:  # pragma: no cover - litellm ships with the gateway
        pass
    yield
    get_settings.cache_clear()


def _client() -> TestClient:
    """The REAL route behind the REAL app-wide gate and ``PUBLIC_ROUTES``."""
    import importlib

    from acb_auth import require_authenticated

    main = importlib.import_module("gateway.main")
    app = FastAPI(dependencies=[require_authenticated(public=main.PUBLIC_ROUTES)])
    app.add_api_route(
        "/v1/embeddings", main.embeddings, methods=["POST"], response_model=None,
    )
    return TestClient(app)


def _spy(monkeypatch: pytest.MonkeyPatch, spy: RouterSpy) -> RouterSpy:
    from acb_auth import console_resolve

    monkeypatch.setattr(console_resolve, "embeddings_on_console", spy)
    return spy


def _console(monkeypatch: pytest.MonkeyPatch, fake: FakeConsole) -> FakeConsole:
    from acb_auth import console_resolve

    monkeypatch.setattr(console_resolve, "_new_http_client", fake.client)
    return fake


# ── The route reaches the Router, once, with the session's member ───────────


def test_the_door_calls_the_router_once_with_the_session_member(monkeypatch) -> None:
    spy = _spy(monkeypatch, RouterSpy())
    r = _client().post("/v1/embeddings", headers=AS_MEMBER, json={"input": "hello"})
    assert r.status_code == 200, r.text
    assert r.json() == OK_BODY
    assert len(spy.calls) == 1
    payload, kw = spy.calls[0]
    assert payload == {"model": "tier-embed", "input": ["hello"]}
    assert kw["member"] == MEMBER
    assert kw["member_proven"] is True
    assert kw["timeout"] > 0


def test_the_answer_keeps_the_shape_the_callers_read(monkeypatch) -> None:
    """Mem0, Graphiti and the OpenAI SDK read ``data[].embedding``."""
    _spy(monkeypatch, RouterSpy())
    body = _client().post(
        "/v1/embeddings", headers=AS_MEMBER, json={"input": ["a"]},
    ).json()
    assert len(body["data"][0]["embedding"]) == 1536
    assert body["usage"]["prompt_tokens"] == 2
    assert body["model"] == "tier-embed"


def test_the_body_cannot_set_the_org_the_member_or_the_model(monkeypatch) -> None:
    """R11. Only ``input`` travels. A claim in the body or a header is dropped."""
    spy = _spy(monkeypatch, RouterSpy())
    r = _client().post(
        "/v1/embeddings",
        headers={**AS_MEMBER, "X-CC-Member": "attacker@other.test"},
        json={
            "input": ["x"],
            "model": "text-embedding-3-large",
            "organization_id": "22222222-2222-2222-2222-222222222222",
            "org": "other-org",
            "member": "attacker@other.test",
            "user": "attacker@other.test",
            "dimensions": 3072,
            "encoding_format": "base64",
        },
    )
    assert r.status_code == 200, r.text
    payload, kw = spy.calls[0]
    assert payload == {"model": "tier-embed", "input": ["x"]}
    assert kw["member"] == MEMBER
    assert "attacker@other.test" not in json.dumps([payload, kw])


def test_a_service_call_names_no_member(monkeypatch) -> None:
    """``system:internal`` is not a person, so no member travels."""
    spy = _spy(monkeypatch, RouterSpy())
    r = _client().post("/v1/embeddings", headers=AS_SERVICE, json={"input": ["x"]})
    assert r.status_code == 200, r.text
    _payload, kw = spy.calls[0]
    assert kw["member"] is None
    assert kw["member_proven"] is False


# ── The gate ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer nope"},
        {"Authorization": f"Bearer {LLM_KEY}"},
        {"X-User-Email": MEMBER},
    ],
    ids=["no-credential", "wrong-key", "llm-key", "bare-email"],
)
def test_an_unauthenticated_call_is_refused_with_no_hop(monkeypatch, headers) -> None:
    spy = _spy(monkeypatch, RouterSpy())
    r = _client().post("/v1/embeddings", headers=headers, json={"input": ["x"]})
    assert r.status_code == 401, r.text
    assert spy.calls == []


# ── Verdicts are relayed, outages fail closed ───────────────────────────────


@pytest.mark.parametrize("status", [400, 402, 403, 422])
def test_a_router_verdict_is_relayed_with_its_status_and_body(monkeypatch, status) -> None:
    body = {"detail": {"reason": "insufficient_credit", "status": status}}
    _spy(monkeypatch, RouterSpy(status=status, body=body))
    r = _client().post("/v1/embeddings", headers=AS_MEMBER, json={"input": ["x"]})
    assert r.status_code == status
    assert r.json() == body


def test_a_402_through_the_real_client_stays_a_402(monkeypatch) -> None:
    body = {"detail": "no credit left for this organization"}
    console = _console(monkeypatch, FakeConsole(status=402, body=body))
    r = _client().post("/v1/embeddings", headers=AS_MEMBER, json={"input": ["x"]})
    assert r.status_code == 402
    assert r.json() == body
    assert len(console.requests) == 1


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (429, None),
        (500, None),
        (503, None),
        (401, None),
        (200, httpx.ReadTimeout("slow")),
        (200, httpx.ConnectError("down")),
    ],
    ids=["429", "500", "503", "401-box-key", "timeout", "connect"],
)
def test_an_outage_fails_closed_with_no_vendor_call(monkeypatch, status, error) -> None:
    """D57.7. A 502, one request to the Console, and nothing else."""
    console = _console(monkeypatch, FakeConsole(status=status, error=error))
    r = _client().post("/v1/embeddings", headers=AS_MEMBER, json={"input": ["x"]})
    assert r.status_code == 502, r.text
    assert r.json()["error"]["type"] == "ConsoleRouterUnavailable"
    assert len(console.requests) == 1


def test_the_request_goes_to_the_console_and_never_to_openai(monkeypatch) -> None:
    console = _console(monkeypatch, FakeConsole())
    r = _client().post("/v1/embeddings", headers=AS_MEMBER, json={"input": ["x"]})
    assert r.status_code == 200, r.text
    (req,) = console.requests
    assert str(req.url) == CONSOLE_URL + "/v1/embeddings"
    assert req.url.host != "api.openai.com"
    assert req.headers["Authorization"] == f"Bearer {ORG_KEY}"
    assert req.headers["X-CC-Member"] == MEMBER
    assert req.headers["X-CC-Member-Proven"] == "1"
    assert json.loads(req.content) == {"model": "tier-embed", "input": ["x"]}


def test_an_unwired_box_fails_closed_with_no_request(monkeypatch) -> None:
    """No zero vector and no vendor key, even with OPENAI_API_KEY set."""
    monkeypatch.setenv("CUSTOMER_CONSOLE_URL", "")
    get_settings.cache_clear()
    console = _console(monkeypatch, FakeConsole())
    r = _client().post("/v1/embeddings", headers=AS_MEMBER, json={"input": ["x"]})
    assert r.status_code == 502, r.text
    assert console.requests == []
    assert "embedding" not in r.text


def test_a_deployment_box_refuses_a_call_with_no_member(monkeypatch) -> None:
    """A per-box key needs a PROVEN member to pick the organization (H-152)."""
    monkeypatch.setenv("CUSTOMER_CONSOLE_ORG_KEY", "")
    monkeypatch.setenv("CUSTOMER_CONSOLE_DEPLOYMENT_KEY", DEPLOYMENT_KEY)
    monkeypatch.setenv("CUSTOMER_CONSOLE_ROUTER_USES_DEPLOYMENT_KEY", "true")
    get_settings.cache_clear()
    console = _console(monkeypatch, FakeConsole())
    client = _client()

    r = client.post("/v1/embeddings", headers=AS_SERVICE, json={"input": ["x"]})
    assert r.status_code == 502, r.text
    assert console.requests == []

    r = client.post("/v1/embeddings", headers=AS_MEMBER, json={"input": ["x"]})
    assert r.status_code == 200, r.text
    (req,) = console.requests
    assert req.headers["Authorization"] == f"Bearer {DEPLOYMENT_KEY}"
    assert req.headers["X-CC-Member"] == MEMBER
    assert req.headers["X-CC-Member-Proven"] == "1"


# ── The batch cap ────────────────────────────────────────────────────────────


def test_the_batch_cap_refuses_with_no_hop(monkeypatch) -> None:
    from gateway.kb_index.embed import RELAY_MAX_INPUTS

    spy = _spy(monkeypatch, RouterSpy())
    client = _client()
    too_many = {"input": ["x"] * (RELAY_MAX_INPUTS + 1)}
    assert client.post("/v1/embeddings", headers=AS_MEMBER, json=too_many).status_code == 422
    assert client.post("/v1/embeddings", headers=AS_MEMBER, json={"input": []}).status_code == 422
    assert spy.calls == []
    at_cap = {"input": ["x"] * RELAY_MAX_INPUTS}
    assert client.post("/v1/embeddings", headers=AS_MEMBER, json=at_cap).status_code == 200
    assert len(spy.calls) == 1


def test_the_cap_equals_the_console_doors_own_ceiling() -> None:
    """A lower cap here refuses work the Router takes. A higher one costs a hop."""
    from gateway.kb_index.embed import RELAY_MAX_INPUTS

    text = _CONSOLE_MAIN.read_text(encoding="utf-8")
    found = re.search(r"^_MAX_EMBED_INPUTS\s*=\s*(\d+)\s*$", text, re.MULTILINE)
    assert found, "the Console door's _MAX_EMBED_INPUTS moved"
    assert int(found.group(1)) == RELAY_MAX_INPUTS


# ── Source fences (R7) ───────────────────────────────────────────────────────


def _route() -> ast.AsyncFunctionDef:
    tree = ast.parse(_MAIN.read_text(encoding="utf-8"))
    return next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "embeddings"
    )


def test_main_imports_no_openai_and_builds_no_vendor_client() -> None:
    tree = ast.parse(_MAIN.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert not any(a.name.split(".")[0] == "openai" for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            assert node.module.split(".")[0] != "openai", node.module
        elif isinstance(node, ast.Call):
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            assert name not in {"OpenAI", "AsyncOpenAI"}, node.lineno


def test_the_route_reads_no_vendor_key_and_makes_no_zero_vector() -> None:
    route = _route()
    strings = [
        n.value for n in ast.walk(route)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
    ]
    assert not any("OPENAI_API_KEY" in s for s in strings)
    assert not any("api.openai.com" in s for s in strings)
    built = [
        n for n in ast.walk(route)
        if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Mult)
        and isinstance(n.left, ast.List)
    ]
    assert not built, "the route must not build a vector, zero or otherwise"


def test_the_route_awaits_the_relay_of_the_embed_door() -> None:
    awaited = [
        n for n in ast.walk(_route())
        if isinstance(n, ast.Await) and isinstance(n.value, ast.Call)
        and getattr(n.value.func, "id", None) == "relay"
    ]
    assert len(awaited) == 1
