"""WS-54 IN-0 — close the live cross-tenant holes of the Integrations surface.

Spec: ``project-docs/specs/integrations_console.md`` §2.2, §4, §7.2 (IN-0) and
§9. Decision D96. The R8 half (the MCP list and delete, two organizations, a
real Postgres) is ``test_integrations_mcp_tenant_r8.py``.

The fences, in the order of the acceptance list:

1. An AST scan of ``routes/integrations.py`` and ``routes/oauth.py`` refuses
   every write to the process env and every call to ``_upsert_env_var`` or
   ``_persist_tokens``. Only the bodies of those two helpers are exempt.
2. Configure, ``PUT /keys`` and the GitHub device poll leave ``os.environ`` and
   the env file as they were, and store for the BOUND tenant. ``DELETE
   /keys`` leaves the env var and deletes the row.
3. A failed store write in configure is 503. Connect-cli is 410 and runs no
   subprocess. The OAuth authorize and refresh are 410, and the callback
   answers the retired page with no token request.
4. A route walk over both live routers. A member with ``feature:integrations``
   and without ``integrations:manage`` gets 403 on each writer before the
   handler runs.
6. ``/mcp/test`` and plugin install fetch only through
   ``gateway/outbound_guard.py``. A private, loopback, link-local or metadata
   address is 422 and no request goes out. A 302 is a refusal.
7. With two organizations, the startup copy
   (``ProviderKeyStore.configure_integrations``) reads nothing, so no stored
   key reaches ``os.environ``. That pins why the copy may stay until IN-5.

No test here opens a database or reaches the internet.
"""
from __future__ import annotations

import ast
import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from acb_auth import UserContext, UserRole, build_access, get_current_user
from acb_common.db import bind_tenant, release_tenant
from acb_common.settings import get_settings
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from gateway import outbound_guard
from gateway.routes import integrations
from gateway.routes import oauth as oauth_routes

REPO = Path(__file__).resolve().parents[2]
ROUTE_FILES = (
    "apps/services/gateway/gateway/routes/integrations.py",
    "apps/services/gateway/gateway/routes/oauth.py",
)
ORG = "11111111-2222-3333-4444-555555555555"
USER = UserContext(email="admin@example.test", role=UserRole.EXECUTIVE, organization_id=ORG)
ENV_BEFORE = b"EXISTING=1\nAPOLLO_API_KEY=from-the-box\n"
PUBLIC_IP = "93.184.216.34"


# ── 1. The AST fence: no env write in either route file ─────────────────────

#: The helpers whose own bodies may write the env file. IN-5 removes both.
EXEMPT_DEFS = frozenset({"_upsert_env_var", "_persist_tokens"})
_ENV_METHODS = frozenset({"pop", "setdefault", "update", "clear", "__setitem__", "__delitem__"})
_OS_CALLS = frozenset({"putenv", "unsetenv"})


def _is_environ(node: ast.AST) -> bool:
    """``os.environ``, or a bare ``environ`` taken from ``os``."""
    if isinstance(node, ast.Attribute) and node.attr == "environ":
        return isinstance(node.value, ast.Name) and node.value.id == "os"
    return isinstance(node, ast.Name) and node.id == "environ"


def _targets(node: ast.AST) -> Iterator[ast.AST]:
    if isinstance(node, (ast.Tuple, ast.List)):
        for elt in node.elts:
            yield from _targets(elt)
    else:
        yield node


class _EnvWriteFinder(ast.NodeVisitor):
    def __init__(self) -> None:
        self.found: list[str] = []

    def _skip(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
        return node.name in EXEMPT_DEFS

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        if not self._skip(node):
            self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        if not self._skip(node):
            self.generic_visit(node)

    def _check_targets(self, targets: list[ast.expr], line: int, verb: str) -> None:
        for target in targets:
            for t in _targets(target):
                if isinstance(t, ast.Subscript) and _is_environ(t.value):
                    self.found.append(f"{line}: {verb} os.environ[...]")

    def visit_Assign(self, node: ast.Assign) -> None:
        self._check_targets(node.targets, node.lineno, "assign")
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self._check_targets([node.target], node.lineno, "assign")
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self._check_targets([node.target], node.lineno, "assign")
        self.generic_visit(node)

    def visit_Delete(self, node: ast.Delete) -> None:
        self._check_targets(node.targets, node.lineno, "del")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if isinstance(func, ast.Attribute):
            if func.attr in _ENV_METHODS and _is_environ(func.value):
                self.found.append(f"{node.lineno}: os.environ.{func.attr}()")
            if (func.attr in _OS_CALLS and isinstance(func.value, ast.Name)
                    and func.value.id == "os"):
                self.found.append(f"{node.lineno}: os.{func.attr}()")
            if func.attr in EXEMPT_DEFS:
                self.found.append(f"{node.lineno}: {func.attr}()")
        elif isinstance(func, ast.Name) and func.id in EXEMPT_DEFS | _OS_CALLS:
            self.found.append(f"{node.lineno}: {func.id}()")
        self.generic_visit(node)


def env_writes(source: str) -> list[str]:
    """Each env write in ``source`` outside the two exempt helper bodies."""
    finder = _EnvWriteFinder()
    finder.visit(ast.parse(source))
    return finder.found


@pytest.mark.parametrize("relative_path", ROUTE_FILES)
def test_no_route_writes_the_process_env(relative_path: str) -> None:
    source = (REPO / relative_path).read_text(encoding="utf-8")
    found = env_writes(source)
    assert found == [], (
        f"{relative_path} writes the process env or the env file at {found}. "
        "One env has one value for the whole deployment, so the write crosses "
        "every tenant (WS-54 IN-0, D96.3). Write the store of the organization."
    )


@pytest.mark.parametrize("relative_path", ROUTE_FILES)
def test_the_exempt_helpers_still_exist(relative_path: str) -> None:
    """The exemption names two real defs. A rename would empty it silently."""
    tree = ast.parse((REPO / relative_path).read_text(encoding="utf-8"))
    names = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    expected = {"_upsert_env_var"} if relative_path.endswith("integrations.py") else {
        "_persist_tokens"}
    assert expected <= names


_PLANTED = [
    pytest.param('os.environ["X"] = "y"', id="assign"),
    pytest.param('os.environ["X"] += "y"', id="aug-assign"),
    pytest.param('a, os.environ["X"] = 1, "y"', id="tuple-target"),
    pytest.param('del os.environ["X"]', id="del"),
    pytest.param('os.environ.pop("X", None)', id="pop"),
    pytest.param('os.environ.setdefault("X", "y")', id="setdefault"),
    pytest.param('os.environ.update({"X": "y"})', id="update"),
    pytest.param("os.environ.clear()", id="clear"),
    pytest.param('os.putenv("X", "y")', id="putenv"),
    pytest.param('os.unsetenv("X")', id="unsetenv"),
    pytest.param('_upsert_env_var(path, "X", "y")', id="upsert"),
    pytest.param("_persist_tokens(provider, tokens)", id="persist"),
    pytest.param('environ["X"] = "y"', id="bare-environ"),
]


@pytest.mark.parametrize("planted", _PLANTED)
def test_the_fence_can_fail(planted: str) -> None:
    """A write planted in a synthetic handler fails the fence."""
    source = (
        "import os\n"
        "@router.post('/x')\n"
        "async def handler(req):\n"
        f"    {planted}\n"
        "    return {}\n"
    )
    assert env_writes(source), planted


def test_only_the_bodies_of_the_two_helpers_are_exempt() -> None:
    source = (
        "def _persist_tokens(provider, tokens):\n"
        "    _upsert_env_var(path, 'X', 'y')\n"
        "def _upsert_env_var(path, key, value):\n"
        "    pass\n"
        "async def handler():\n"
        "    _upsert_env_var(path, 'X', 'y')\n"
    )
    assert env_writes(source) == ["6: _upsert_env_var()"]


def test_a_read_of_the_env_is_not_a_write() -> None:
    assert env_writes('import os\nx = os.environ.get("X")\ny = os.environ["Y"]\n') == []


# ── Shared fakes ─────────────────────────────────────────────────────────────


class RecordingStore:
    """Records each store call with its keyword arguments."""

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.puts: list[tuple[str, str, dict[str, Any]]] = []
        self.deletes: list[tuple[str, dict[str, Any]]] = []
        self.gets: list[tuple[str, dict[str, Any]]] = []
        self.held: dict[str, str] = {}

    async def put(self, provider: str, api_key: str, **kw: Any) -> None:
        if self.fail:
            raise RuntimeError("store down")
        self.puts.append((provider, api_key, kw))

    async def delete(self, provider: str, **kw: Any) -> None:
        self.deletes.append((provider, kw))

    async def get(self, provider: str, **kw: Any) -> str:
        self.gets.append((provider, kw))
        return self.held.get(provider, "")

    async def get_by_type(self, credential_type: str, **_: Any) -> dict[str, str]:
        return {}


@pytest.fixture(autouse=True)
def _restore_environ() -> Iterator[None]:
    saved = dict(os.environ)
    get_settings.cache_clear()
    yield
    os.environ.clear()
    os.environ.update(saved)
    get_settings.cache_clear()


@pytest.fixture()
def env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / ".env"
    path.write_bytes(ENV_BEFORE)
    monkeypatch.setattr(integrations, "_find_env_file", lambda: path)
    monkeypatch.setattr(oauth_routes, "_find_env_file", lambda: path)
    return path


@pytest.fixture()
def store(monkeypatch: pytest.MonkeyPatch) -> RecordingStore:
    import acb_llm.key_store as key_store_module

    fake = RecordingStore()
    monkeypatch.setattr(key_store_module, "get_key_store", lambda: fake)
    return fake


@pytest.fixture()
def tenant() -> Iterator[str]:
    """Bind the tenant the way the auth dependency does, from the session."""
    token = bind_tenant(ORG)
    try:
        yield ORG
    finally:
        release_tenant(token)


@pytest.fixture()
def byok_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BYOK_ENABLED", "true")
    get_settings.cache_clear()


def _snapshot(env_path: Path) -> tuple[bytes, dict[str, str]]:
    return env_path.read_bytes(), dict(os.environ)


def _assert_untouched(env_path: Path, before: tuple[bytes, dict[str, str]]) -> None:
    assert env_path.read_bytes() == before[0], "the env file changed"
    assert dict(os.environ) == before[1], "os.environ changed"


class _FakeGithub:
    """Stands in for ``httpx.AsyncClient`` in the device poll."""

    def __init__(self, *_: Any, **__: Any) -> None:
        pass

    async def __aenter__(self) -> _FakeGithub:
        return self

    async def __aexit__(self, *_: Any) -> bool:
        return False

    async def post(self, *_: Any, **__: Any) -> httpx.Response:
        return httpx.Response(200, json={"access_token": "gho_In0Token123"})

    async def get(self, *_: Any, **__: Any) -> httpx.Response:
        return httpx.Response(200, json={"login": "octocat"})


# ── 2. The three writers leave the env alone and store for the bound tenant ──


class TestTheWritersStoreForTheTenantOnly:
    async def test_configure(self, env_file, store, tenant) -> None:
        os.environ["APOLLO_API_KEY"] = "from-the-box"
        before = _snapshot(env_file)
        req = integrations.ConfigureRequest(
            vars=[integrations.IntegrationVar(key="APOLLO_API_KEY", value="org-a-key")],
        )
        out = await integrations.configure_integrations(req, user=USER)
        _assert_untouched(env_file, before)
        assert out["written"] == ["APOLLO_API_KEY"]
        assert [(p, v) for p, v, _ in store.puts] == [("apollo:api_key", "org-a-key")]
        assert store.puts[0][2]["organization_id"] == tenant

    async def test_put_keys(self, env_file, store, tenant) -> None:
        before = _snapshot(env_file)
        req = integrations.IntegrationKeyRequest(
            service="apollo", key_name="api_key", value="org-a-key",
        )
        await integrations.put_integration_key(req, user=USER)
        _assert_untouched(env_file, before)
        assert [(p, v) for p, v, _ in store.puts] == [("apollo:api_key", "org-a-key")]
        assert store.puts[0][2]["organization_id"] == tenant

    async def test_the_device_poll(self, env_file, store, tenant, monkeypatch, byok_on) -> None:
        monkeypatch.setenv("GITHUB_CLIENT_ID", "Iv1.8a61f9b3a7aba766")
        get_settings.cache_clear()
        monkeypatch.setattr(integrations.httpx, "AsyncClient", _FakeGithub)
        before = _snapshot(env_file)
        out = await integrations.github_device_poll(
            integrations.DevicePollRequest(device_code="d"), user=USER,
        )
        _assert_untouched(env_file, before)
        assert out == {"status": "authorized", "login": "octocat"}
        assert [(p, v) for p, v, _ in store.puts] == [("github:token", "gho_In0Token123")]
        assert store.puts[0][2]["organization_id"] == tenant
        assert store.puts[0][2]["credential_type"] == "integration"

    async def test_the_device_poll_answers_503_when_the_store_fails(
        self, env_file, monkeypatch, tenant, byok_on,
    ) -> None:
        import acb_llm.key_store as key_store_module

        monkeypatch.setattr(key_store_module, "get_key_store", lambda: RecordingStore(fail=True))
        monkeypatch.setenv("GITHUB_CLIENT_ID", "Iv1.8a61f9b3a7aba766")
        get_settings.cache_clear()
        monkeypatch.setattr(integrations.httpx, "AsyncClient", _FakeGithub)
        before = _snapshot(env_file)
        with pytest.raises(HTTPException) as caught:
            await integrations.github_device_poll(
                integrations.DevicePollRequest(device_code="d"), user=USER,
            )
        assert caught.value.status_code == 503
        _assert_untouched(env_file, before)

    async def test_delete_keys_leaves_the_env_var(self, env_file, store, tenant) -> None:
        os.environ["APOLLO_API_KEY"] = "from-the-box"
        before = _snapshot(env_file)
        req = integrations.IntegrationKeyDelete(service="apollo", key_name="api_key")
        out = await integrations.delete_integration_key(req, user=USER)
        assert out["deleted"] is True
        assert os.environ["APOLLO_API_KEY"] == "from-the-box"
        _assert_untouched(env_file, before)
        assert store.deletes == [("apollo:api_key", {"organization_id": tenant})]


# ── 3. The refusals ─────────────────────────────────────────────────────────


class TestTheRetiredAndFailedPaths:
    async def test_a_failed_store_write_in_configure_is_503(
        self, env_file, monkeypatch, tenant,
    ) -> None:
        import acb_llm.key_store as key_store_module

        monkeypatch.setattr(key_store_module, "get_key_store", lambda: RecordingStore(fail=True))
        before = _snapshot(env_file)
        req = integrations.ConfigureRequest(
            vars=[integrations.IntegrationVar(key="APOLLO_API_KEY", value="org-a-key")],
        )
        with pytest.raises(HTTPException) as caught:
            await integrations.configure_integrations(req, user=USER)
        assert caught.value.status_code == 503
        _assert_untouched(env_file, before)

    async def test_connect_cli_is_410_and_runs_no_subprocess(
        self, env_file, monkeypatch, byok_on,
    ) -> None:
        ran: list[Any] = []
        monkeypatch.setattr(integrations.subprocess, "run", lambda *a, **k: ran.append(a))
        before = _snapshot(env_file)
        with pytest.raises(HTTPException) as caught:
            await integrations.github_connect_cli(user=USER)
        assert caught.value.status_code == 410
        assert ran == []
        _assert_untouched(env_file, before)

    async def test_oauth_authorize_is_410(self) -> None:
        with pytest.raises(HTTPException) as caught:
            await oauth_routes.oauth_authorize("zoho-crm", user=USER)
        assert caught.value.status_code == 410

    async def test_oauth_refresh_is_410(self, env_file) -> None:
        before = _snapshot(env_file)
        with pytest.raises(HTTPException) as caught:
            await oauth_routes.oauth_refresh("zoho-crm", user=USER)
        assert caught.value.status_code == 410
        _assert_untouched(env_file, before)

    async def test_the_callback_answers_retired_with_no_token_request(
        self, env_file, monkeypatch,
    ) -> None:
        class _NoNetwork:
            def __init__(self, *_: Any, **__: Any) -> None:
                raise AssertionError("the retired callback made a token request")

        monkeypatch.setattr(oauth_routes.httpx, "AsyncClient", _NoNetwork)
        monkeypatch.setattr(oauth_routes, "_verify_state", lambda *_: True)
        before = _snapshot(env_file)
        page = await oauth_routes.oauth_callback(
            service="zoho-crm", code="a-real-code", state="s", error="",
        )
        assert page.status_code == 400
        assert oauth_routes.RETIRED_DETAIL in bytes(page.body).decode()
        _assert_untouched(env_file, before)

    async def test_refresh_access_token_writes_no_env(self, env_file, monkeypatch) -> None:
        class _Token:
            def __init__(self, *_: Any, **__: Any) -> None:
                pass

            async def __aenter__(self) -> _Token:
                return self

            async def __aexit__(self, *_: Any) -> bool:
                return False

            async def post(self, *_: Any, **__: Any) -> httpx.Response:
                return httpx.Response(200, json={"access_token": "1000.new", "expires_in": 3600})

        monkeypatch.setenv("ZOHO_REFRESH_TOKEN", "1000.refresh")
        monkeypatch.setenv("ZOHO_CLIENT_ID", "cid")
        get_settings.cache_clear()
        monkeypatch.setattr(oauth_routes.httpx, "AsyncClient", _Token)
        before = _snapshot(env_file)
        assert await oauth_routes.refresh_access_token("zoho-crm") == "1000.new"
        _assert_untouched(env_file, before)


# ── 4. The route walk: every writer needs integrations:manage ────────────────


def _writer_routes() -> list[tuple[str, str, str]]:
    """``(method, path, router)`` for each writer, read off the live routers.

    Every route whose method is not GET, on both routers, and the OAuth
    authorize, which is a GET that once started a write.
    """
    found: list[tuple[str, str, str]] = []
    for name, module in (("integrations", integrations), ("oauth", oauth_routes)):
        for route in module.router.routes:
            methods = set(getattr(route, "methods", set()) or set()) - {"HEAD", "OPTIONS"}
            path = route.path  # type: ignore[attr-defined]
            if methods - {"GET"} or path.endswith("/authorize"):
                for method in sorted(methods):
                    found.append((method, path, name))
    return found


WRITERS = _writer_routes()


def test_the_walk_sees_every_writer() -> None:
    """The walk is not empty, and it holds the routes the spec names."""
    paths = {(m, p) for m, p, _ in WRITERS}
    for expected in [
        ("POST", "/integrations/configure"), ("PUT", "/integrations/keys"),
        ("DELETE", "/integrations/keys"), ("POST", "/integrations/discover"),
        ("POST", "/integrations/custom"), ("DELETE", "/integrations/custom/{service_id}"),
        ("POST", "/integrations/mcp"), ("DELETE", "/integrations/mcp/{name}"),
        ("POST", "/integrations/mcp/test"), ("POST", "/integrations/plugins/install"),
        ("DELETE", "/integrations/plugins/{plugin_id}"),
        ("POST", "/integrations/github/device/start"),
        ("POST", "/integrations/github/device/poll"),
        ("POST", "/integrations/github/connect-cli"),
        ("GET", "/integrations/oauth/{service}/authorize"),
        ("POST", "/integrations/oauth/{service}/refresh"),
    ]:
        assert expected in paths, expected
    assert len(WRITERS) == 16, WRITERS


def _client(permissions: list[str], monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, list[str]]:
    """Both live routers, behind a member with ``permissions``.

    Each side effect that a handler could reach is replaced by a recorder, so
    a handler that runs leaves a mark and never reaches a database or a host.
    """
    ran: list[str] = []

    async def _db(*_: Any, **__: Any) -> list[dict[str, Any]]:
        ran.append("db")
        raise RuntimeError("no database in this test")

    class _NoNetwork:
        def __init__(self, *_: Any, **__: Any) -> None:
            ran.append("httpx")
            raise RuntimeError("no network in this test")

    async def _no_guard(*_: Any, **__: Any) -> Any:
        ran.append("outbound")
        raise outbound_guard.OutboundRefused("x", "test")

    monkeypatch.setattr(integrations, "_db_query", _db)
    monkeypatch.setattr(integrations.httpx, "AsyncClient", _NoNetwork)
    monkeypatch.setattr(oauth_routes.httpx, "AsyncClient", _NoNetwork)
    monkeypatch.setattr(outbound_guard, "request", _no_guard)
    monkeypatch.setattr(integrations.subprocess, "run", lambda *a, **k: ran.append("subprocess"))
    monkeypatch.delenv("GITHUB_CLIENT_ID", raising=False)
    get_settings.cache_clear()

    app = FastAPI()
    app.include_router(integrations.router)
    app.include_router(oauth_routes.router)
    app.dependency_overrides[get_current_user] = lambda: UserContext(
        email="member@example.test", role=UserRole.EXECUTIVE,
        access=build_access(permissions),
    )
    return TestClient(app, raise_server_exceptions=False), ran


def _concrete(path: str) -> str:
    out = path
    for param in ("{service_id}", "{name}", "{plugin_id}", "{service}"):
        out = out.replace(param, "zoho-crm" if param == "{service}" else "x")
    return out


@pytest.mark.parametrize(("method", "path", "router"), WRITERS)
def test_a_member_without_manage_is_refused_before_the_handler(
    method: str, path: str, router: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, ran = _client(["feature:integrations"], monkeypatch)
    res = client.request(method, _concrete(path), json={})
    assert res.status_code == 403, (method, path, res.status_code, res.text)
    assert "integrations:manage" in res.json()["detail"], res.text
    assert ran == [], f"the handler ran before the gate: {ran}"


@pytest.mark.parametrize(("method", "path", "router"), WRITERS)
def test_a_member_with_manage_passes_that_gate(
    method: str, path: str, router: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _ = _client(["feature:integrations", "integrations:manage"], monkeypatch)
    res = client.request(method, _concrete(path), json={})
    refused_by_the_gate = res.status_code == 403 and "integrations:manage" in res.text
    assert not refused_by_the_gate, (method, path, res.text)


def test_a_read_stays_on_the_feature_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    """GET reads need ``feature:integrations`` and not the manage right."""
    client, _ = _client(["feature:integrations"], monkeypatch)
    res = client.get("/integrations/mcp")
    assert res.status_code == 200, res.text
    assert res.json() == []  # no tenant bound, so no row of any organization


# ── 6. Customer URLs go through the one URL guard ───────────────────────────


class _Net:
    """Records each request that reaches a transport."""

    def __init__(self, status: int = 200) -> None:
        self.requests: list[httpx.Request] = []
        self.status = status

    def install(self, monkeypatch: pytest.MonkeyPatch) -> _Net:
        real = httpx.AsyncClient

        def _handle(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            headers = {"location": "http://127.0.0.1/"} if 300 <= self.status < 400 else {}
            return httpx.Response(self.status, headers=headers, stream=httpx.ByteStream(b"{}"))

        def _client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
            kwargs.pop("transport", None)
            return real(*args, transport=httpx.MockTransport(_handle), **kwargs)

        monkeypatch.setattr(httpx, "AsyncClient", _client)
        return self


REFUSED_URLS = [
    pytest.param("http://127.0.0.1/", {}, id="loopback"),
    pytest.param("http://169.254.169.254/", {}, id="metadata"),
    pytest.param("http://10.0.0.1/", {}, id="private"),
    pytest.param("http://[::1]/", {}, id="ipv6-loopback"),
    pytest.param("http://mcp.example.com/sse", {"mcp.example.com": ["127.0.0.1"]},
                 id="public-name-to-loopback"),
]


def _resolver(monkeypatch: pytest.MonkeyPatch, table: dict[str, list[str]]) -> None:
    def _resolve(host: str, port: int) -> list[str]:
        if host in table:
            return list(table[host])
        return [host.strip("[]")]

    monkeypatch.setattr(outbound_guard, "_resolve", _resolve)


class TestCustomerUrlsPassTheGuard:
    @pytest.mark.parametrize(("url", "dns"), REFUSED_URLS)
    async def test_mcp_test_refuses_and_sends_nothing(self, url, dns, monkeypatch) -> None:
        net = _Net().install(monkeypatch)
        _resolver(monkeypatch, dns)
        req = integrations.McpServerRequest(
            name="srv", transport="http-sse", url=url, headers={"Authorization": "Bearer t"},
        )
        with pytest.raises(HTTPException) as caught:
            await integrations.test_mcp_server(req, user=USER)
        assert caught.value.status_code == 422
        assert net.requests == []

    async def test_a_redirect_is_a_refusal_not_ok(self, monkeypatch) -> None:
        net = _Net(status=302).install(monkeypatch)
        _resolver(monkeypatch, {"mcp.example.com": [PUBLIC_IP]})
        monkeypatch.setattr(outbound_guard, "_is_local_address", lambda _a: False)
        req = integrations.McpServerRequest(
            name="srv", transport="http-sse", url="https://mcp.example.com/sse",
        )
        with pytest.raises(HTTPException) as caught:
            await integrations.test_mcp_server(req, user=USER)
        assert caught.value.status_code == 422
        assert "redirect" in caught.value.detail
        assert len(net.requests) == 1  # the one request, and no follow

    async def test_a_public_address_is_still_tested(self, monkeypatch) -> None:
        net = _Net(status=200).install(monkeypatch)
        _resolver(monkeypatch, {"mcp.example.com": [PUBLIC_IP]})
        monkeypatch.setattr(outbound_guard, "_is_local_address", lambda _a: False)
        req = integrations.McpServerRequest(
            name="srv", transport="http-sse", url="https://mcp.example.com/sse",
        )
        out = await integrations.test_mcp_server(req, user=USER)
        assert out["ok"] is True
        assert len(net.requests) == 1
        assert net.requests[0].url.host == PUBLIC_IP  # pinned to the checked address

    @pytest.mark.parametrize(("url", "dns"), REFUSED_URLS)
    async def test_plugin_install_refuses_and_sends_nothing(self, url, dns, monkeypatch) -> None:
        net = _Net().install(monkeypatch)
        _resolver(monkeypatch, dns)
        with pytest.raises(HTTPException) as caught:
            await integrations.install_plugin(
                integrations.PluginInstallRequest(manifest_url=url), user=USER,
            )
        assert caught.value.status_code == 422
        assert net.requests == []

    async def test_a_refused_api_url_is_skipped(self, monkeypatch) -> None:
        """The manifest is public, and its ``api.url`` points at metadata."""
        manifest = {"name_for_model": "demo", "api": {"url": "http://169.254.169.254/spec"}}
        seen: list[str] = []
        real = httpx.AsyncClient

        def _handle(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            return httpx.Response(200, stream=httpx.ByteStream(json.dumps(manifest).encode()))

        def _client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
            kwargs.pop("transport", None)
            return real(*args, transport=httpx.MockTransport(_handle), **kwargs)

        monkeypatch.setattr(httpx, "AsyncClient", _client)
        _resolver(monkeypatch, {"plugins.example.com": [PUBLIC_IP]})
        monkeypatch.setattr(outbound_guard, "_is_local_address", lambda _a: False)

        class _Session:
            def __enter__(self) -> _Session:
                return self

            def __exit__(self, *_: Any) -> bool:
                return False

            def execute(self, *_: Any, **__: Any) -> None:
                return None

            def commit(self) -> None:
                return None

        import acb_graph

        monkeypatch.setattr(acb_graph, "get_session", lambda: _Session())
        out = await integrations.install_plugin(
            integrations.PluginInstallRequest(manifest_url="https://plugins.example.com/ai-plugin.json"),
            user=USER,
        )
        assert out["ok"] is True and out["tools_count"] == 0
        assert len(seen) == 1, seen  # the manifest only, never the metadata address


# ── 7. Two organizations: the startup copy reads nothing ────────────────────


def _startup_store(monkeypatch: pytest.MonkeyPatch, *, org_count: int) -> Any:
    """A real ``ProviderKeyStore`` whose ``_execute`` answers like Postgres.

    The resolve query answers the sole organization only while ``org_count``
    is 1, the same predicate as the real one. One ``apollo:api_key`` row is
    stored. A put records and writes nothing.
    """
    from acb_llm.key_store import INTEGRATION_ENV_MAP, ProviderKeyStore

    monkeypatch.setenv("ACB_MASTER_KEY", "test-master-key-for-in0")
    get_settings.cache_clear()
    for key_map in INTEGRATION_ENV_MAP.values():
        for env_var in key_map.values():
            monkeypatch.delenv(env_var, raising=False)
    ks = ProviderKeyStore()
    cipher = ks.encrypt("stored-apollo-key")
    statements: list[str] = []

    async def _execute(sql: str, **_: Any) -> list[dict[str, Any]]:
        statements.append(sql)
        if "count(*) FROM organization" in sql:
            return [{"id": ORG}] if org_count == 1 else []
        if "FROM provider_keys" in sql:
            return [{"provider": "apollo:api_key", "encrypted": cipher}]
        return []

    monkeypatch.setattr(ks, "_execute", _execute)
    return ks


async def test_two_orgs_the_startup_copy_sets_no_env(monkeypatch: pytest.MonkeyPatch) -> None:
    ks = _startup_store(monkeypatch, org_count=2)
    await ks.configure_integrations()
    assert "APOLLO_API_KEY" not in os.environ


async def test_one_org_the_startup_copy_still_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    """The companion: the same fake does arm the copy, so the test above can fail."""
    ks = _startup_store(monkeypatch, org_count=1)
    await ks.configure_integrations()
    assert os.environ.get("APOLLO_API_KEY") == "stored-apollo-key"


# ── Fix round 1, P1: configure and PUT write the name the startup copy reads ─


def _startup_provider() -> dict[str, str]:
    """Each built-in env var, and the provider name that the startup copy reads."""
    from acb_llm.key_store import INTEGRATION_ENV_MAP

    return {
        env: f"{svc}:{suffix}"
        for svc, key_map in INTEGRATION_ENV_MAP.items()
        for suffix, env in key_map.items()
        if env in integrations.BUILTIN_ENV_KEYS
    }


def test_every_builtin_key_has_one_startup_name() -> None:
    from acb_llm.key_store import INTEGRATION_ENV_MAP

    names: dict[str, list[str]] = {}
    for svc, key_map in INTEGRATION_ENV_MAP.items():
        for suffix, env in key_map.items():
            names.setdefault(env, []).append(f"{svc}:{suffix}")
    for key in integrations.BUILTIN_ENV_KEYS:
        assert len(names.get(key, [])) == 1, (key, names.get(key))


@pytest.mark.parametrize("key", sorted(integrations.BUILTIN_ENV_KEYS))
async def test_configure_stores_the_name_the_startup_copy_reads(
    key: str, env_file, store, tenant, byok_on,
) -> None:
    req = integrations.ConfigureRequest(
        vars=[integrations.IntegrationVar(key=key, value="v1-value")],
    )
    await integrations.configure_integrations(req, user=USER)
    assert [p for p, _, _ in store.puts] == [_startup_provider()[key]]


@pytest.mark.parametrize("key", sorted(integrations.BUILTIN_ENV_KEYS))
async def test_put_keys_stores_the_name_the_startup_copy_reads(
    key: str, env_file, store, tenant, byok_on,
) -> None:
    service, key_name = _startup_provider()[key].split(":", 1)
    req = integrations.IntegrationKeyRequest(service=service, key_name=key_name, value="v1-value")
    out = await integrations.put_integration_key(req, user=USER)
    assert out["env_var"] == key
    assert [p for p, _, _ in store.puts] == [_startup_provider()[key]]


# ── Fix round 1, P2: the device flow reads the client id of the org ─────────


class _RecordingGithub(_FakeGithub):
    """Records the ``client_id`` of each POST to GitHub."""

    sent: list[str] = []

    async def post(self, url: str, *_: Any, data: dict[str, Any] | None = None, **__: Any,
                   ) -> httpx.Response:
        _RecordingGithub.sent.append((data or {}).get("client_id", ""))
        if url.endswith("/login/device/code"):
            return httpx.Response(200, json={
                "user_code": "U", "verification_uri": "https://github.com/login/device",
                "device_code": "D", "expires_in": 900, "interval": 5,
            })
        return httpx.Response(200, json={"access_token": "gho_In0Token123"})


@pytest.fixture()
def github(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    _RecordingGithub.sent = []
    monkeypatch.setattr(integrations.httpx, "AsyncClient", _RecordingGithub)
    return _RecordingGithub.sent


class TestTheDeviceFlowReadsTheOrgClientId:
    async def test_start_uses_the_client_id_of_the_org(
        self, store, tenant, github, monkeypatch,
    ) -> None:
        monkeypatch.delenv("GITHUB_CLIENT_ID", raising=False)
        get_settings.cache_clear()
        store.held["github:client_id"] = "Iv1.org-a-client"
        out = await integrations.github_device_start(user=USER)
        assert out["device_code"] == "D"
        assert github == ["Iv1.org-a-client"]
        assert store.gets == [("github:client_id", {"organization_id": tenant})]

    async def test_start_falls_back_to_the_operator_value(
        self, store, tenant, github, monkeypatch,
    ) -> None:
        monkeypatch.setenv("GITHUB_CLIENT_ID", "Iv1.operator-client")
        get_settings.cache_clear()
        await integrations.github_device_start(user=USER)
        assert github == ["Iv1.operator-client"]

    async def test_start_is_422_when_neither_holds_one(
        self, store, tenant, github, monkeypatch,
    ) -> None:
        monkeypatch.delenv("GITHUB_CLIENT_ID", raising=False)
        get_settings.cache_clear()
        with pytest.raises(HTTPException) as caught:
            await integrations.github_device_start(user=USER)
        assert caught.value.status_code == 422
        assert github == []

    async def test_poll_uses_the_client_id_of_the_org(
        self, store, tenant, github, monkeypatch, byok_on,
    ) -> None:
        monkeypatch.delenv("GITHUB_CLIENT_ID", raising=False)
        get_settings.cache_clear()
        store.held["github:client_id"] = "Iv1.org-a-client"
        out = await integrations.github_device_poll(
            integrations.DevicePollRequest(device_code="d"), user=USER,
        )
        assert out["status"] == "authorized"
        assert github == ["Iv1.org-a-client"]
        assert [(p, v) for p, v, _ in store.puts] == [("github:token", "gho_In0Token123")]


# ── Fix round 1, P2: the guard reads raw bytes, so ask for no encoding ───────


class TestTheGuardedFetchesAskForNoEncoding:
    """``outbound_guard`` keeps the raw bytes of an answer and never decodes them.

    httpx sends ``Accept-Encoding: gzip, deflate`` by default. A server that
    honours it sends gzip, and ``json.loads`` fails on it. This server answers
    gzip whenever the request allows it, as a real one may.
    """

    @staticmethod
    def _install(monkeypatch: pytest.MonkeyPatch, body: bytes) -> list[httpx.Request]:
        import gzip

        seen: list[httpx.Request] = []
        real = httpx.AsyncClient

        def _handle(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            accepts = request.headers.get("accept-encoding", "")
            if "gzip" in accepts:
                return httpx.Response(200, headers={"content-encoding": "gzip"},
                                      stream=httpx.ByteStream(gzip.compress(body)))
            return httpx.Response(200, stream=httpx.ByteStream(body))

        def _client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
            kwargs.pop("transport", None)
            return real(*args, transport=httpx.MockTransport(_handle), **kwargs)

        monkeypatch.setattr(httpx, "AsyncClient", _client)
        _resolver(monkeypatch, {"plugins.example.com": [PUBLIC_IP]})
        monkeypatch.setattr(outbound_guard, "_is_local_address", lambda _a: False)
        return seen

    async def test_plugin_install_parses_the_manifest_and_the_spec(self, monkeypatch) -> None:
        spec = {"paths": {"/ping": {"get": {"operationId": "ping"}}}}
        manifest = {"name_for_model": "demo",
                    "api": {"url": "https://plugins.example.com/openapi.json"}}

        real = httpx.AsyncClient  # before `_install` replaces it
        seen = self._install(monkeypatch, b"")

        def _handle(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            body = json.dumps(spec if request.url.path.endswith("openapi.json") else manifest)
            if "gzip" in request.headers.get("accept-encoding", ""):
                import gzip
                return httpx.Response(200, headers={"content-encoding": "gzip"},
                                      stream=httpx.ByteStream(gzip.compress(body.encode())))
            return httpx.Response(200, stream=httpx.ByteStream(body.encode()))

        def _client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
            kwargs.pop("transport", None)
            return real(*args, transport=httpx.MockTransport(_handle), **kwargs)

        monkeypatch.setattr(httpx, "AsyncClient", _client)

        class _Session:
            def __enter__(self) -> _Session:
                return self

            def __exit__(self, *_: Any) -> bool:
                return False

            def execute(self, *_: Any, **__: Any) -> None:
                return None

            def commit(self) -> None:
                return None

        import acb_graph

        monkeypatch.setattr(acb_graph, "get_session", lambda: _Session())
        out = await integrations.install_plugin(
            integrations.PluginInstallRequest(manifest_url="https://plugins.example.com/ai-plugin.json"),
            user=USER,
        )
        assert out["tools_count"] == 1
        assert [r.headers.get("accept-encoding") for r in seen] == ["identity", "identity"]

    async def test_mcp_test_asks_for_no_encoding(self, monkeypatch) -> None:
        seen = self._install(monkeypatch, b"{}")
        req = integrations.McpServerRequest(
            name="srv", transport="http-sse", url="https://plugins.example.com/sse",
        )
        out = await integrations.test_mcp_server(req, user=USER)
        assert out["ok"] is True
        assert [r.headers.get("accept-encoding") for r in seen] == ["identity"]
