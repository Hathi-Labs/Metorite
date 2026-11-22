"""TLY-1: the setup page and the stores (brief section 6.7, address-mode form).

Brief test 18: "From another browser tab, send a request to the setup page
with no secret, and then with a wrong Origin. Both refused." The unit half
runs here. Brief test 19 (no token in plain text on disk) runs here for the
dev file store. On Windows it runs against DPAPI in build step 4.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
ROOT = REPO / "apps" / "connectors" / "tally_bridge"
sys.path.insert(0, str(ROOT))

from starlette.testclient import TestClient  # noqa: E402
from tally_bridge.reader.parse import parse_answer  # noqa: E402
from tally_bridge.secret_store import (  # noqa: E402
    DevFileSecretStore,
    EnvSecretStore,
    MemorySecretStore,
    ReadOnlySecretStore,
    new_token,
    token_sha256,
)
from tally_bridge.settings import (  # noqa: E402
    EnvSettingsStore,
    FileSettingsStore,
    LiveSettings,
    Settings,
    SettingsError,
)
from tally_bridge.transport import build_address_app  # noqa: E402

PORT = 8765
BASE = f"http://127.0.0.1:{PORT}"
KEY = new_token()
COMPANIES = (
    b"<ENVELOPE><COLLECTION>"
    b"<COMPANY><NAME>Alpha Co</NAME></COMPANY>"
    b"<COMPANY><NAME>Beta &amp; Sons</NAME></COMPANY>"
    b"<COMPANY><NAME>&lt;script&gt;x</NAME></COMPANY>"
    b"</COLLECTION></ENVELOPE>"
)
LEDGERS = ROOT / "fixtures" / "synthetic" / "SYNTHETIC_ledgers.xml"


class Reader:
    def __init__(self, up: bool = True) -> None:
        self.up = up

    async def is_up(self) -> bool:
        return self.up

    async def post(self, envelope: bytes):
        if b"BridgeCompanies" in envelope:
            return parse_answer(COMPANIES)
        return parse_answer(LEDGERS.read_bytes())


def make(tmp_path, reader=None, secrets=None):
    store = FileSettingsStore(tmp_path / "settings.json")
    secrets = secrets or MemorySecretStore(None, token_sha256(KEY))
    live = LiveSettings(store.load())
    app = build_address_app(live, secrets, reader or Reader(), store)
    return TestClient(app, base_url=BASE), store, secrets, live


def api(c, method, path, body=None, key=KEY, origin=BASE, **headers):
    h = {"X-Setup-Key": key, **headers}
    if origin:
        h["Origin"] = origin
    if body is not None:
        return c.request(method, path, json=body, headers=h)
    return c.request(method, path, headers=h)


# --- Brief test 18 and the guard ------------------------------------------------------


def test_the_page_opens_with_the_key(tmp_path):
    c, *_ = make(tmp_path)
    with c:
        r = c.get(f"/setup?k={KEY}")
    assert r.status_code == 200
    assert "Tally bridge setup" in r.text
    assert r.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["referrer-policy"] == "no-referrer"
    assert r.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    "path,headers",
    [
        ("/setup", {}),  # brief test 18: no secret
        (f"/setup?k={KEY}x", {}),
        (f"/setup?k={KEY}", {"Origin": "http://evil.example"}),  # brief test 18: wrong Origin
        (f"/setup?k={KEY}", {"Origin": "http://localhost:8765"}),
        (f"/setup?k={KEY}", {"Host": "box.example.ts.net"}),  # through the tunnel
        (f"/setup?k={KEY}", {"Host": "localhost:8765"}),
        (f"/setup?k={KEY}", {"X-Forwarded-For": "203.0.113.9"}),
        (f"/setup?k={KEY}", {"Tailscale-Funnel-Request": "?1"}),
        (f"/setup?k={KEY}", {"CF-Connecting-IP": "203.0.113.9"}),
        ("/setup/app.js", {}),
    ],
)
def test_the_page_refuses(tmp_path, path, headers):
    c, *_ = make(tmp_path)
    with c:
        r = c.get(path, headers=headers)
    assert r.status_code == 403
    assert r.json() == {"error": "refused"}


@pytest.mark.parametrize(
    "kw",
    [
        {"key": ""},
        {"key": "x" + KEY},
        {"origin": "http://evil.example"},
        {"origin": "null"},
        {"Host": "box.example.ts.net"},
        {"Forwarded": "for=203.0.113.9"},
        {"Via": "1.1 proxy"},
        {"X-Forwarded-Port": "443"},
        {"X-Original-Forwarded-For": "203.0.113.9"},
        {"True-Client-IP": "203.0.113.9"},
        {"CF-Visitor": '{"scheme":"https"}'},
        {"Tailscale-Headers-Info": "x"},
        {"Sec-Fetch-Site": "cross-site"},
        {"Sec-Fetch-Site": "same-site"},
    ],
)
def test_every_api_call_needs_the_key_the_origin_and_the_host(tmp_path, kw):
    c, *_ = make(tmp_path)
    with c:
        assert api(c, "GET", "/setup/api/state", **kw).status_code == 403
        assert api(c, "POST", "/setup/api/token", {}, **kw).status_code == 403
        assert api(c, "POST", "/setup/api/companies", {"companies": []}, **kw).status_code == 403


def test_a_write_needs_the_origin_and_a_read_does_not(tmp_path):
    """A browser sends no Origin on a same-origin GET, and always one on a POST."""
    c, *_ = make(tmp_path)
    with c:
        read = api(c, "GET", "/setup/api/state", origin=None, **{"Sec-Fetch-Site": "same-origin"})
        write = api(c, "POST", "/setup/api/token", {}, origin=None)
    assert read.status_code == 200
    assert write.status_code == 403


def test_no_stored_setup_key_closes_setup(tmp_path):
    c, *_ = make(tmp_path, secrets=MemorySecretStore(None, None))
    with c:
        assert c.get(f"/setup?k={KEY}").status_code == 403
        assert api(c, "GET", "/setup/api/state").status_code == 403


def test_a_post_that_is_not_json_is_refused(tmp_path):
    """A plain HTML form can send a cross-site POST with no preflight. JSON cannot."""
    c, *_ = make(tmp_path)
    with c:
        r = c.post(
            "/setup/api/token",
            content="x=1",
            headers={"X-Setup-Key": KEY, "Origin": BASE, "Content-Type": "text/plain"},
        )
    assert r.status_code == 415


def test_the_key_in_the_page_cannot_break_out_of_its_attribute(tmp_path):
    c, *_ = make(tmp_path)
    with c:
        bad = c.get('/setup?k="><script>x</script>')
        r = c.get(f"/setup?k={KEY}")
    assert bad.status_code == 403  # a wrong key never reaches the page
    assert r.text.count(f"k={KEY}") == 2  # the stylesheet and the script carry it
    assert "<script>x" not in r.text


def test_the_assets_load_with_the_key(tmp_path):
    c, *_ = make(tmp_path)
    with c:
        js = c.get(f"/setup/app.js?k={KEY}")
        css = c.get(f"/setup/app.css?k={KEY}")
    assert js.status_code == 200 and "X-Setup-Key" in js.text
    assert "innerHTML" not in js.text  # every value goes in as text
    assert css.status_code == 200


# --- The four steps -------------------------------------------------------------------


def test_step_1_and_4_the_state(tmp_path):
    c, *_ = make(tmp_path)
    with c:
        s = api(c, "GET", "/setup/api/state").json()
    assert s["tally"] == {"up": True, "companies": ["Alpha Co", "Beta & Sons", "<script>x"]}
    assert s["allowed"] == [] and s["token_set"] is False and s["address"] is None


def test_step_1_with_tally_closed(tmp_path):
    c, *_ = make(tmp_path, reader=Reader(up=False))
    with c:
        s = api(c, "GET", "/setup/api/state").json()
    assert s["tally"] == {"up": False, "companies": None}


def test_step_2_writes_the_allow_list_and_the_tools_use_it_at_once(tmp_path):
    secrets = MemorySecretStore(token_sha256("read-token"), token_sha256(KEY))
    c, store, _, live = make(tmp_path, secrets=secrets)
    call = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "find_ledgers", "arguments": {"query": "alpha"}},
    }
    mcp = {
        "Authorization": "Bearer read-token",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    with c:
        before = c.post("/mcp", json=call, headers=mcp).json()["result"]
        assert "No company is allowed yet" in before["content"][0]["text"]
        r = api(c, "POST", "/setup/api/companies", {"companies": ["Beta & Sons"]})
        assert r.status_code == 200 and r.json() == {"allowed": ["Beta & Sons"]}
        after = c.post("/mcp", json=call, headers=mcp).json()["result"]
    assert after.get("isError") is not True
    assert json.loads(after["content"][0]["text"])["company"] == "Beta & Sons"
    assert store.load().allowed_companies == ("Beta & Sons",)
    assert live.allowed_companies == ("Beta & Sons",)


@pytest.mark.parametrize(
    "body",
    [
        {"companies": ["Gamma Co"]},  # not open in Tally
        {"companies": "Alpha Co"},
        {"companies": [1]},
        {"companies": [""]},
        {"companies": ["x" * 201]},
        {"other": []},
        [],
    ],
)
def test_step_2_refuses_a_bad_list_and_writes_nothing(tmp_path, body):
    c, store, *_ = make(tmp_path)
    with c:
        r = api(c, "POST", "/setup/api/companies", body)
    assert r.status_code == 400
    assert store.load().allowed_companies == ()


def test_step_2_with_tally_closed_writes_nothing(tmp_path):
    c, *_ = make(tmp_path, reader=Reader(up=False))
    with c:
        r = api(c, "POST", "/setup/api/companies", {"companies": ["Alpha Co"]})
    assert r.status_code == 409
    assert not (tmp_path / "settings.json").exists()


def test_step_3_shows_a_new_token_once_and_stores_only_its_hash(tmp_path):
    c, _, secrets, _ = make(tmp_path)
    with c:
        r = api(c, "POST", "/setup/api/token", {})
        token = r.json()["token"]
        state = api(c, "GET", "/setup/api/state")
    assert len(token) >= 43
    assert secrets.read_token_sha256() == token_sha256(token)
    assert state.json()["token_set"] is True
    assert token not in state.text  # the page never shows a stored secret again


def test_step_3_with_a_read_only_store_says_so(tmp_path):
    class ReadOnly(MemorySecretStore):
        def set_read_token_sha256(self, digest):
            raise ReadOnlySecretStore("no")

    c, *_ = make(tmp_path, secrets=ReadOnly(None, token_sha256(KEY)))
    with c:
        r = api(c, "POST", "/setup/api/token", {})
    assert r.status_code == 500 and "cannot store a token" in r.json()["error"]


def test_step_3_shows_the_tunnel_address(tmp_path):
    (tmp_path / "settings.json").write_text(json.dumps({"PUBLIC_HOST": "box.example.ts.net"}))
    c, *_ = make(tmp_path)
    with c:
        s = api(c, "GET", "/setup/api/state").json()
    assert s["address"] == "https://box.example.ts.net/mcp"


# --- The stores ------------------------------------------------------------------------


def test_a_stale_temp_file_does_not_keep_its_mode(tmp_path):
    path = tmp_path / "secrets.json"
    stale = tmp_path / "secrets.json.tmp"
    stale.write_text("old")
    stale.chmod(0o644)
    DevFileSecretStore(path).set_read_token_sha256(token_sha256("t"))
    if os.name == "posix":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert not stale.exists()


def test_the_settings_file_round_trips(tmp_path):
    store = FileSettingsStore(tmp_path / "s.json")
    assert store.load() == Settings()
    s = Settings(allowed_companies=("A | B", "C, D"), bind_port=9100, public_host="h.example")
    with pytest.raises(SettingsError):
        store.save(Settings(allowed_companies=("A|B",), bind_host="0.0.0.0"))
    store.save(Settings(allowed_companies=("C, D",), bind_port=9100, public_host="h.example"))
    assert store.load().allowed_companies == ("C, D",)
    assert store.load().bind_port == 9100
    assert s.bind_port == 9100
    assert not (tmp_path / "s.json.tmp").exists()


def test_a_broken_settings_file_is_refused(tmp_path):
    (tmp_path / "s.json").write_text("{not json")
    with pytest.raises(SettingsError):
        FileSettingsStore(tmp_path / "s.json").load()


def test_the_env_stores_are_read_only():
    with pytest.raises(SettingsError):
        EnvSettingsStore({}).save(Settings())
    with pytest.raises(ReadOnlySecretStore):
        EnvSecretStore({}).set_read_token_sha256(token_sha256("x"))
    with pytest.raises(ReadOnlySecretStore):
        EnvSecretStore({}).set_setup_secret_sha256(token_sha256("x"))


def test_brief_test_19_the_dev_file_store_holds_hashes_only(tmp_path):
    path = tmp_path / "secrets.json"
    store = DevFileSecretStore(path)
    token, key = new_token(), new_token()
    store.set_read_token_sha256(token_sha256(token))
    store.set_setup_secret_sha256(token_sha256(key))
    text = path.read_text()
    assert token not in text and key not in text
    assert store.read_token_sha256() == token_sha256(token)
    assert store.setup_secret_sha256() == token_sha256(key)
    if os.name == "posix":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    with pytest.raises(ValueError):
        store.set_read_token_sha256(token)  # a raw token is refused: hashes only


def test_live_settings_swap_and_refuse_a_bad_value():
    live = LiveSettings(Settings())
    live.replace(Settings(allowed_companies=("A",)))
    assert live.allowed_companies == ("A",)
    with pytest.raises(SettingsError):
        live.replace(Settings(bind_host="0.0.0.0"))
    assert live.allowed_companies == ("A",)
