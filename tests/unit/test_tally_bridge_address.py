"""TLY-1 PR 1: the Tally bridge in address mode (docs/tally_bridge_brief.md).

Unit-level cover of brief tests 2, 4 and 5, and of the settings and token
rules of section 6.5. A unit pass is not the brief test: the brief test runs
on the public address through the tunnel.

No TallyPrime runs here. "Tally closed" is a real TCP port with nothing on
it, and "Tally hung" is a fake reader that never answers.
"""

from __future__ import annotations

import asyncio
import json
import socket
import sys
import time
from pathlib import Path

import httpx
import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "apps" / "connectors" / "tally_bridge"))

from starlette.testclient import TestClient  # noqa: E402
from tally_bridge import __version__  # noqa: E402
from tally_bridge.reader import TallyClient  # noqa: E402
from tally_bridge.secret_store import (  # noqa: E402
    EnvSecretStore,
    MemorySecretStore,
    new_token,
    token_matches,
    token_sha256,
)
from tally_bridge.settings import (  # noqa: E402
    EnvSettingsStore,
    Settings,
    SettingsError,
    settings_from_mapping,
)
from tally_bridge.transport import build_address_app  # noqa: E402

BASE = "http://127.0.0.1:8765"
TOKEN = new_token()
INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "unit", "version": "0"},
    },
}
MCP_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}


def _closed_port() -> int:
    """A loopback port with nothing on it: a closed TallyPrime."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _UpReader:
    async def is_up(self) -> bool:
        return True


class _HungReader:
    async def is_up(self) -> bool:
        await asyncio.sleep(3600)
        return True


def _client(reader, settings: Settings | None = None, **kw) -> TestClient:
    settings = settings or Settings()
    app = build_address_app(settings, MemorySecretStore(token_sha256(TOKEN)), reader)
    return TestClient(app, base_url=kw.pop("base_url", BASE), **kw)


def _auth(token: str = TOKEN) -> dict[str, str]:
    return {**MCP_HEADERS, "Authorization": f"Bearer {token}"}


def _rpc(method: str, params: dict | None = None, rid: int = 2) -> dict:
    return {"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}}


def _closed_tally() -> TallyClient:
    return TallyClient(f"http://127.0.0.1:{_closed_port()}", timeout_s=2)


# --- Brief test 1 and 2, the token -------------------------------------------


def test_initialize_with_the_read_token_is_200_and_no_redirect():
    with _client(_UpReader()) as c:
        r = c.post("/mcp", json=INIT, headers=_auth(), follow_redirects=False)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    assert r.json()["result"]["serverInfo"]["name"] == "tally"


def test_no_token_is_401():
    with _client(_UpReader()) as c:
        r = c.post("/mcp", json=INIT, headers=MCP_HEADERS)
    assert r.status_code == 401


def test_wrong_token_is_401():
    with _client(_UpReader()) as c:
        r = c.post("/mcp", json=INIT, headers=_auth(new_token()))
        r2 = c.post("/mcp", json=INIT, headers={**MCP_HEADERS, "Authorization": f"Basic {TOKEN}"})
    assert r.status_code == 401
    assert r2.status_code == 401


def test_read_token_on_mcp_write_is_404():
    with _client(_UpReader()) as c:
        r = c.post("/mcp-write", json=INIT, headers=_auth(), follow_redirects=False)
    assert r.status_code == 404


def test_no_stored_hash_refuses_every_token():
    app = build_address_app(Settings(), MemorySecretStore(None), _UpReader())
    with TestClient(app, base_url=BASE) as c:
        r = c.post("/mcp", json=INIT, headers=_auth())
    assert r.status_code == 401


def test_trailing_slash_is_404_not_a_redirect():
    with _client(_UpReader()) as c:
        r = c.post("/mcp/", json=INIT, headers=_auth(), follow_redirects=False)
    assert r.status_code == 404


def test_a_foreign_host_header_is_refused():
    """The SDK guard refuses a Host that is not loopback or PUBLIC_HOST."""
    with _client(_UpReader(), base_url="http://evil.example") as c:
        r = c.post("/mcp", json=INIT, headers=_auth())
    assert r.status_code != 200


def test_the_public_host_is_accepted():
    settings = Settings(public_host="office-pc.example.ts.net")
    with _client(_UpReader(), settings, base_url="https://office-pc.example.ts.net") as c:
        r = c.post("/mcp", json=INIT, headers=_auth())
    assert r.status_code == 200


def test_a_browser_origin_is_refused():
    with _client(_UpReader()) as c:
        r = c.post("/mcp", json=INIT, headers={**_auth(), "Origin": "http://127.0.0.1:8765"})
    assert r.status_code != 200


# --- tools/list and status (brief tests 3, 4 and 5) --------------------------


def test_tools_list_with_tally_closed_is_fast():
    with _client(_closed_tally()) as c:
        start = time.monotonic()
        r = c.post("/mcp", json=_rpc("tools/list"), headers=_auth())
        took = time.monotonic() - start
    assert r.status_code == 200
    assert took < 2.0
    tools = r.json()["result"]["tools"]
    assert [t["name"] for t in tools] == ["status"]


def test_tools_list_hints_and_size():
    with _client(_UpReader()) as c:
        r = c.post("/mcp", json=_rpc("tools/list"), headers=_auth())
    tools = r.json()["result"]["tools"]
    assert len(tools) <= 9
    for t in tools:
        a = t["annotations"]
        assert a["readOnlyHint"] is True
        assert a["destructiveHint"] is False
        assert a["idempotentHint"] is True
        assert a["openWorldHint"] is False
        assert "outputSchema" not in t or t["outputSchema"] is None
    # Brief rule 8: about 6 KB for all tools together.
    assert len(r.content) < 6 * 1024


def test_status_with_tally_up():
    with _client(_UpReader()) as c:
        r = c.post(
            "/mcp", json=_rpc("tools/call", {"name": "status", "arguments": {}}), headers=_auth()
        )
    result = r.json()["result"]
    assert result.get("isError") is not True
    assert json.loads(result["content"][0]["text"]) == {
        "tally": "up",
        "bridge_version": __version__,
    }


def test_status_with_tally_closed_is_a_plain_error():
    with _client(_closed_tally()) as c:
        start = time.monotonic()
        r = c.post(
            "/mcp", json=_rpc("tools/call", {"name": "status", "arguments": {}}), headers=_auth()
        )
        took = time.monotonic() - start
    result = r.json()["result"]
    assert result["isError"] is True
    assert result["content"][0]["text"] == "TallyPrime is not running on the office machine."
    assert took < 25


def test_status_with_tally_hung_stops_at_the_deadline():
    settings = Settings(tally_timeout_s=0.5, call_deadline_s=0.5)
    with _client(_HungReader(), settings) as c:
        start = time.monotonic()
        r = c.post(
            "/mcp", json=_rpc("tools/call", {"name": "status", "arguments": {}}), headers=_auth()
        )
        took = time.monotonic() - start
    result = r.json()["result"]
    assert result["isError"] is True
    assert "did not answer in 0.5 seconds" in result["content"][0]["text"]
    assert took < 5


# --- /health ------------------------------------------------------------------


def test_health_needs_no_token_and_says_down():
    with _client(_closed_tally()) as c:
        r = c.get("/health")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "tally": "down"}


def test_health_says_up():
    with _client(_UpReader()) as c:
        assert c.get("/health").json() == {"ok": True, "tally": "up"}


# --- The reader -----------------------------------------------------------------


def test_reader_reads_a_200_as_up_and_a_500_as_down():
    def handler(status: int):
        return httpx.MockTransport(lambda _req: httpx.Response(status, text="x"))

    up = TallyClient("http://127.0.0.1:9000", 1, transport=handler(200))
    down = TallyClient("http://127.0.0.1:9000", 1, transport=handler(500))
    assert asyncio.run(up.is_up()) is True
    assert asyncio.run(down.is_up()) is False


# --- Settings (security rule 1) and tokens (rule 2) --------------------------------


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.5", "example.com", "::"])
def test_settings_refuse_a_bind_host_that_is_not_loopback(host):
    with pytest.raises(SettingsError):
        Settings(bind_host=host).validate()


@pytest.mark.parametrize(
    "url", ["http://192.168.1.5:9000", "https://127.0.0.1:9000", "http://tally.example:9000"]
)
def test_settings_refuse_a_tally_url_off_this_machine(url):
    with pytest.raises(SettingsError):
        Settings(tally_url=url).validate()


def test_settings_refuse_a_deadline_at_or_past_metorite_limit():
    with pytest.raises(SettingsError):
        Settings(call_deadline_s=30).validate()
    with pytest.raises(SettingsError):
        Settings(tally_timeout_s=26, call_deadline_s=25).validate()


def test_settings_defaults_are_the_brief_values():
    s = Settings().validate()
    assert (s.tally_url, s.bind_host, s.bind_port) == ("http://127.0.0.1:9000", "127.0.0.1", 8765)
    assert (s.tally_timeout_s, s.call_deadline_s, s.max_range_days, s.max_rows) == (20, 25, 92, 200)
    assert s.allowed_companies == ()


def test_settings_from_the_environment():
    env = {
        "TALLY_BRIDGE_BIND_PORT": "9100",
        "TALLY_BRIDGE_ALLOWED_COMPANIES": "Alpha Traders | Beta, Pvt Ltd",
        "TALLY_BRIDGE_PUBLIC_HOST": "box.example.ts.net",
    }
    s = EnvSettingsStore(env).load()
    assert s.bind_port == 9100
    assert s.allowed_companies == ("Alpha Traders", "Beta, Pvt Ltd")
    assert s.public_host == "box.example.ts.net"
    with pytest.raises(SettingsError):
        settings_from_mapping({"BIND_PORT": "not a port"})


def test_a_token_is_long_and_only_its_hash_is_kept():
    token = new_token()
    assert len(token) >= 43  # 32 random bytes
    digest = token_sha256(token)
    assert token not in digest
    assert token_matches(token, digest)
    assert not token_matches(token + "x", digest)
    assert not token_matches("", digest)
    assert not token_matches(token, None)


def test_the_env_secret_store_refuses_a_value_that_is_not_a_hash():
    with pytest.raises(ValueError):
        EnvSecretStore({EnvSecretStore.NAME: "a-raw-token-by-mistake"}).read_token_sha256()
    digest = token_sha256("t")
    assert EnvSecretStore({EnvSecretStore.NAME: digest.upper()}).read_token_sha256() == digest
