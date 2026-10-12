"""MCP OAuth: every hop goes through the guard (WS-54 IN-8a, rows O1 and O2).

Spec: ``project-docs/specs/integrations_console.md`` §3.6.5 and §3.6.11.

* **O1.** Each hop refuses before a socket opens: an ``http`` URL, a port that
  is not 443, ``127.0.0.1``, ``169.254.169.254``, an RFC 1918 address, and a
  3xx answer. The hops are the probe of the MCP URL, the protected-resource
  metadata, the auth-server metadata, a metadata document that names a
  private address, registration, token and revoke. "No socket" is read from
  the fake transport: a refused request never reaches it.
* **O2.** No OAuth module calls ``outbound_guard.request``,
  ``outbound_guard.mcp_client`` or ``httpx`` itself (an AST fence).

Mutations M1 and M2 of ``test_mcp_oauth_flow.py`` fail here.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import httpx
import pytest

from tests.unit import _mcp_oauth_fakes as f

ROOT = Path(__file__).resolve().parents[2]
GATEWAY = ROOT / "apps" / "services" / "gateway" / "gateway"
#: The modules of the OAuth flow. Each fetch in them goes through the guard.
OAUTH_MODULES = (
    GATEWAY / "connections" / "mcp_oauth.py",
    GATEWAY / "routes" / "integrations_mcp_oauth.py",
)

BAD_PATH = "/bad-hop"
#: The bad URL forms of O1, each on a path that no good hop uses.
BAD_URLS = {
    "http": f"http://{f.AUTH_HOST}{BAD_PATH}",
    "port": f"https://{f.AUTH_HOST}:8443{BAD_PATH}",
    "loopback": f"https://127.0.0.1{BAD_PATH}",
    "metadata": f"https://169.254.169.254{BAD_PATH}",
    "rfc1918": f"https://10.0.0.7{BAD_PATH}",
    "private-name": f"https://lan.fake.test{BAD_PATH}",
}


@pytest.fixture
def world(monkeypatch) -> f.World:
    f.configure(monkeypatch)
    return f.install(monkeypatch, f.World())


def _pending(**over: Any) -> Any:
    from gateway.connections import mcp_oauth

    base = dict(
        name="notion", member="admin@a.test", server_url=f.MCP_URL, resource=f.MCP_URL,
        issuer=f.ISSUER, iss_required=True, token_endpoint=f"{f.ISSUER}/token",
        revocation_endpoint=f"{f.ISSUER}/revoke", client_id="dcr-client-1",
        registration="dcr", token_auth="client_secret_post", scope="read write",
        verifier="v" * 64, client_secret=f.SENTINEL_SECRET,
    )
    base.update(over)
    return mcp_oauth.Pending(**base)


def _credential(**meta: str) -> Any:
    from datetime import UTC, datetime

    from gateway.crm_sources.base import SourceCredential

    return SourceCredential(
        access_token=f.SENTINEL_ACCESS, refresh_token=f.SENTINEL_REFRESH,
        expires_at=datetime.now(UTC),
        provider_meta={"client_id": "dcr-client-1", "token_auth": "client_secret_post",
                       "revocation_endpoint": f"{f.ISSUER}/revoke", **meta},
        client_secret=f.SENTINEL_SECRET,
    )


# ── Each hop, pointed at a bad URL ──────────────────────────────────────────


async def _hop(hop: str, world: f.World, bad: str) -> str:
    """Point *hop* at *bad*, run it, and return the failure code."""
    from gateway.connections import mcp_oauth

    try:
        if hop == "probe":
            await mcp_oauth.discover(bad)
        elif hop == "prm":
            world.www_authenticate = f'Bearer resource_metadata="{bad}"'
            await mcp_oauth.discover(f.MCP_URL)
        elif hop == "as":
            assert world.prm is not None
            world.prm["authorization_servers"] = [bad]
            await mcp_oauth.discover(f.MCP_URL)
        elif hop == "registration":
            world.as_meta["registration_endpoint"] = bad
            plan = await mcp_oauth.discover(f.MCP_URL)
            await mcp_oauth.register(plan)
        elif hop == "metadata-names-it":
            # The metadata passes discovery, and names a bad token endpoint.
            # The guard refuses it when the exchange fetches it.
            world.as_meta["token_endpoint"] = bad
            plan = await mcp_oauth.discover(f.MCP_URL)
            await mcp_oauth.exchange_code(_pending(token_endpoint=plan.token_endpoint), "c")
        elif hop == "token":
            await mcp_oauth.exchange_code(_pending(token_endpoint=bad), "c")
        elif hop == "revoke":
            outcome = await mcp_oauth.revoke(_credential(revocation_endpoint=bad))
            assert outcome.startswith("failed:"), outcome
            return outcome.removeprefix("failed:")
        else:  # pragma: no cover - a typo in the table
            raise AssertionError(hop)
    except mcp_oauth.OAuthFailed as exc:
        return exc.code
    raise AssertionError(f"the {hop} hop did not refuse {bad}")


HOPS = ("probe", "prm", "as", "registration", "metadata-names-it", "token", "revoke")


@pytest.mark.parametrize("bad", sorted(BAD_URLS))
@pytest.mark.parametrize("hop", HOPS)
async def test_each_hop_refuses(world: f.World, hop: str, bad: str) -> None:
    code = await _hop(hop, world, BAD_URLS[bad])
    assert code in {"refused", "insecure_endpoint"}, (hop, bad, code)
    # Before a socket opens: the bad URL never reached the transport.
    reached = [r for r in world.requests if r.url.path == BAD_PATH]
    assert reached == [], f"the {hop} hop opened a socket to a {bad} URL"


#: Where each hop's own answer sits, for the 3xx case.
_REDIRECT_AT = {
    "probe": (f.MCP_HOST, "/mcp"),
    "prm": (f.MCP_HOST, "/.well-known/oauth-protected-resource/mcp"),
    "as": (f.AUTH_HOST, "/.well-known/oauth-authorization-server"),
    "registration": (f.AUTH_HOST, "/register"),
    "token": (f.AUTH_HOST, "/token"),
    "revoke": (f.AUTH_HOST, "/revoke"),
}


@pytest.mark.parametrize("hop", sorted(_REDIRECT_AT))
async def test_each_hop_refuses_a_redirect(world: f.World, hop: str) -> None:
    from gateway.connections import mcp_oauth

    target = f"https://{f.AUTH_HOST}{BAD_PATH}"
    world.fixed[_REDIRECT_AT[hop]] = httpx.Response(302, headers={"location": target})
    if hop == "token":
        with pytest.raises(mcp_oauth.OAuthFailed) as err:
            await mcp_oauth.exchange_code(_pending(), "c")
        code = err.value.code
    elif hop == "revoke":
        code = (await mcp_oauth.revoke(_credential())).removeprefix("failed:")
    else:
        with pytest.raises(mcp_oauth.OAuthFailed) as err:
            plan = await mcp_oauth.discover(f.MCP_URL)
            await mcp_oauth.register(plan)
        code = err.value.code
    assert code == "refused", (hop, code)
    assert [r for r in world.requests if r.url.path == BAD_PATH] == [], (
        f"the {hop} hop followed a redirect")


async def test_a_good_flow_passes_the_guard_and_is_pinned(world: f.World) -> None:
    """The control case: the same world, no bad URL, every hop answers. Each
    request went to the pinned address with the name in ``Host``."""
    from gateway.connections import mcp_oauth

    plan = await mcp_oauth.discover(f.MCP_URL)
    client = await mcp_oauth.register(plan)
    assert client.kind == "dcr"
    assert world.requests, "the control case sent nothing"
    for request in world.requests:
        assert request.url.host == f.PUBLIC_IP
        assert request.url.scheme == "https" and (request.url.port or 443) == 443
        assert request.headers["host"] in (f.MCP_HOST, f.AUTH_HOST)


async def test_request_https_refuses_http_and_a_port_before_dns(monkeypatch) -> None:
    """The guard mode itself: the scheme and the port fail before a lookup."""
    from gateway import outbound_guard as g

    looked: list[str] = []
    monkeypatch.setattr(g, "_resolve", lambda h, p: looked.append(h) or [f.PUBLIC_IP])
    for url, reason in (("http://a.example/x", "scheme"), ("https://a.example:444/x", "port")):
        with pytest.raises(g.OutboundRefused) as err:
            await g.request_https("GET", url)
        assert err.value.reason == reason
    assert looked == []


# ── O2: the AST fence ───────────────────────────────────────────────────────


def _offences(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    bad: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            bad += [f"import {a.name}" for a in node.names if a.name.split(".")[0] == "httpx"]
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod.split(".")[0] == "httpx":
                bad.append(f"from {mod} import")
            if mod.endswith("outbound_guard"):
                for alias in node.names:
                    if alias.name in ("request", "mcp_client", "send", "check_url"):
                        bad.append(f"from {mod} import {alias.name}")
        elif isinstance(node, ast.Attribute):
            if node.attr in ("request", "mcp_client", "send", "check_url") and (
                    isinstance(node.value, ast.Name) and node.value.id in ("outbound_guard", "g")):
                bad.append(f"outbound_guard.{node.attr}")
            if isinstance(node.value, ast.Name) and node.value.id == "httpx":
                bad.append(f"httpx.{node.attr}")
    return bad


def test_no_oauth_module_fetches_by_itself() -> None:
    for path in OAUTH_MODULES:
        assert path.exists(), path
        assert _offences(path) == [], f"{path.name} fetches outside request_https"


def test_the_oauth_modules_do_reach_the_guard_mode() -> None:
    """The fence is not empty: the protocol module calls ``request_https``."""
    source = (GATEWAY / "connections" / "mcp_oauth.py").read_text(encoding="utf-8")
    assert "outbound_guard.request_https(" in source


def test_the_fence_catches_each_banned_form(tmp_path: Path) -> None:
    probe = tmp_path / "probe.py"
    for line in ("import httpx", "from httpx import AsyncClient",
                 "from gateway.outbound_guard import request",
                 "outbound_guard.request('GET', u)", "outbound_guard.mcp_client(u)",
                 "httpx.get(u)"):
        probe.write_text(line + "\n", encoding="utf-8")
        assert _offences(probe), line
