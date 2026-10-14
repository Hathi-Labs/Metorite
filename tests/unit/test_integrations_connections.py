"""WS-54 IN-1 - the Integrations console read model, rules that need no database.

Spec: ``project-docs/specs/integrations_console.md`` §3.2, §3.5, §7.2 (IN-1)
and §9. ``tests/unit/test_integrations_connections_r8.py`` holds the rules that
need a real Postgres: two orgs, the sentinel fence, disconnect and the MCP test.

* ``_is_configured`` answers False for an unknown service (IN-D8).
* The read needs ``admin:members:read``. Each write needs
  ``integrations:manage``. A route walk pins both.
* No SELECT in the module names a credential column or a message column.
* The row model has no field that can hold a secret.
* ``main.py`` includes the router.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest
from acb_auth import UserContext, UserRole, build_access, get_current_user
from fastapi import FastAPI
from fastapi.testclient import TestClient
from gateway.routes import integrations
from gateway.routes import integrations_connections as mod

_GATEWAY = Path(mod.__file__).resolve().parents[1]


class _Settings:
    apollo_api_key = "x"


def test_an_unknown_service_is_not_configured() -> None:
    assert integrations._is_configured("no-such-service", _Settings()) is False
    assert integrations._is_configured("", _Settings()) is False


def test_a_known_service_still_reads_its_settings() -> None:
    assert integrations._is_configured("apollo", _Settings()) is True
    assert integrations._is_configured("instantly", _Settings()) is False
    assert integrations._is_configured("whatsapp", _Settings()) is False


def _client(permissions: list[str]) -> TestClient:
    app = FastAPI()
    app.include_router(mod.router)
    app.dependency_overrides[get_current_user] = lambda: UserContext(
        email="member@example.test", role=UserRole.EMPLOYEE,
        access=build_access(permissions), organization_id="00000000-0000-0000-0000-000000000001",
    )
    return TestClient(app, raise_server_exceptions=False)


def test_a_non_admin_gets_403_on_the_read() -> None:
    client = _client(["feature:integrations"])
    res = client.get("/integrations/connections")
    assert res.status_code == 403
    assert "admin:members:read" in res.json()["detail"]


@pytest.mark.parametrize("path", [
    "/integrations/connections/zoho:abc/disconnect",
    "/integrations/connections/mcp:x/disconnect",
    "/integrations/connections/key:apollo/disconnect",
    "/integrations/mcp/x/test",
])
def test_a_writer_without_manage_gets_403(path: str) -> None:
    client = _client(["feature:integrations", "admin:members:read"])
    res = client.post(path)
    assert res.status_code == 403, res.text
    assert "integrations:manage" in res.json()["detail"]


def _permissions_of(route: Any) -> set[str]:
    found: set[str] = set()
    for dep in route.dependencies:
        closure = getattr(dep.dependency, "__closure__", None) or ()
        for cell in closure:
            value = cell.cell_contents
            if isinstance(value, tuple) and all(isinstance(v, str) for v in value):
                found.update(value)
    return found


def test_the_route_walk_every_writer_needs_manage_and_the_read_needs_admin() -> None:
    seen: dict[tuple[str, str], set[str]] = {}
    for route in mod.router.routes:
        for method in route.methods:
            seen[(method, route.path)] = _permissions_of(route)
    assert seen == {
        ("GET", "/integrations/connections"): {"admin:members:read"},
        ("POST", "/integrations/connections/{connection_id}/disconnect"): {"integrations:manage"},
        ("POST", "/integrations/mcp/{name}/test"): {"integrations:manage"},
    }


_FORBIDDEN = (
    "credentials_encrypted", "encrypted", "body_text", "body_html", "snippet",
    "subject", "access_token", "refresh_token", "webhook_verify_token",
)


def _string_constants(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = {
        id(n.body[0].value)
        for n in ast.walk(tree)
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and n.body and isinstance(n.body[0], ast.Expr)
        and isinstance(n.body[0].value, ast.Constant)
    }
    return [
        n.value for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docstrings
    ]


def test_no_query_in_the_module_names_a_credential_or_message_column() -> None:
    path = Path(mod.__file__)
    bad = [
        (word, text) for text in _string_constants(path) for word in _FORBIDDEN
        if word in text.lower()
    ]
    assert bad == [], f"the read model must never select these: {bad}"


def test_the_mcp_list_shape_names_no_value() -> None:
    """IN-4: the list reads the registry, and keeps the shape of IN-0.

    Header names, never a value. No stdio server is stored, so no command
    and no env var name is listed.
    """
    from gateway.connections.mcp import StoredServer

    secret = "Bearer sentinel-in4-shape"
    srv = StoredServer(
        name="srv", label="Srv", description="", transport="http-sse",
        url="https://mcp.example.com/mcp", agent_scope=("*",), enabled=True,
        header_names=("Authorization",), headers={"Authorization": secret},
    )
    out = integrations.mcp_out(srv)
    assert set(out) == {
        "name", "label", "description", "transport", "command", "url",
        "env_var_names", "header_names", "agent_scope", "enabled",
        "always_confirm", "created_at", "updated_at",
    }
    assert out["header_names"] == ["Authorization"] and out["env_var_names"] == []
    assert out["command"] is None
    assert secret not in repr(out) and secret not in repr(srv)


def test_the_row_model_has_no_field_that_can_hold_a_secret() -> None:
    assert set(mod.Row.model_fields) == {
        "id", "kind", "provider", "label", "owning_app", "scope", "owner", "status",
        "last_sync_at", "last_error", "setup_href", "can_disconnect", "detail",
        # IN-8c (D98): an admin email and a flag. Neither can hold a secret.
        "acts_as", "can_connect",
    }
    assert mod.Row.model_fields["detail"].annotation == dict[str, str]
    assert mod.Row.model_fields["acts_as"].annotation == str | None
    assert mod.Row.model_fields["can_connect"].annotation is bool
    assert set(mod.McpTestOut.model_fields) == {"ok", "tools", "error"}


def test_main_includes_the_router() -> None:
    source = (_GATEWAY / "main.py").read_text(encoding="utf-8")
    assert "from gateway.routes.integrations_connections import router" in source
    assert "app.include_router(_connections_router)" in source


def test_the_module_writes_no_environment_and_opens_no_connection() -> None:
    tree = ast.parse(Path(mod.__file__).read_text(encoding="utf-8"))
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert "environ" not in attrs and "putenv" not in attrs
    assert not ({"create_engine", "create_async_engine", "psycopg", "Redis"} & (names | attrs))


# ── IN-8c (D98): the acts-as label and the Connect offer of an MCP row ──────


def _stored(name: str, **kw: Any) -> Any:
    from gateway.connections.mcp import StoredServer

    base: dict[str, Any] = dict(
        name=name, label=name.title(), description="", transport="http-sse",
        url=f"https://{name}.example.com/mcp", agent_scope=("*",), enabled=True,
        header_names=(),
    )
    base.update(kw)
    return StoredServer(**base)


_ROWS = [
    _stored("notion", oauth=True, connected_by="dana@fracktal.test"),
    _stored("linear", oauth=True, connected_by="dana@fracktal.test",
            status="needs_reconnect", last_error="invalid_grant", enabled=False),
    _stored("fresh"),
    _stored("keyed", header_names=("Authorization",), connected_by="dana@fracktal.test"),
    _stored("off", enabled=False, status="disconnected"),
]


def _user(*permissions: str) -> UserContext:
    return UserContext(
        email="admin@example.test", role=UserRole.EMPLOYEE,
        access=build_access(list(permissions)),
        organization_id="00000000-0000-0000-0000-000000000001",
    )


async def _rows(monkeypatch, *, flag: bool, user: UserContext) -> dict[str, Any]:
    from acb_common import get_settings
    from gateway.connections import mcp as mcp_registry

    async def _list(_org: str) -> list[Any]:
        return list(_ROWS)

    monkeypatch.setattr(mcp_registry, "list_servers", _list)
    monkeypatch.setattr(get_settings(), "mcp_oauth_enabled", flag, raising=False)
    return {r.detail["name"]: r for r in await mod._mcp_rows("org", user)}


async def test_with_the_flag_on_an_oauth_row_names_its_admin_and_offers_a_consent(
        monkeypatch) -> None:
    rows = await _rows(monkeypatch, flag=True, user=_user("integrations:manage"))
    assert rows["notion"].acts_as == "dana@fracktal.test"
    assert rows["linear"].acts_as == "dana@fracktal.test"
    assert rows["linear"].status == "needs_reconnect"
    assert rows["linear"].last_error == "invalid_grant"
    # A key row acts as nobody, whoever saved it.
    assert rows["keyed"].acts_as is None and rows["fresh"].acts_as is None
    offered = {name for name, r in rows.items() if r.can_connect}
    assert offered == {"notion", "linear", "fresh"}, (
        "Connect is for an OAuth row, or a row with no header. Never a key row "
        "or a disabled row")


async def test_with_the_flag_off_the_row_is_the_row_of_in4(monkeypatch) -> None:
    rows = await _rows(monkeypatch, flag=False, user=_user("integrations:manage"))
    assert all(r.acts_as is None and r.can_connect is False for r in rows.values())


async def test_a_reader_without_manage_is_never_offered_a_consent(monkeypatch) -> None:
    rows = await _rows(monkeypatch, flag=True, user=_user("admin:members:read"))
    assert not any(r.can_connect for r in rows.values())
    # The label is a fact about the connection, so a reader sees it too.
    assert rows["notion"].acts_as == "dana@fracktal.test"
