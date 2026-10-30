"""D-EA-11 — first-party agent clients: sign in again with no consent click.

Spec: ``project-docs/specs/external_agent_access.md`` §10.7 and decision
D-EA-11 · gateway rule 13 of ``gateway/routes/agent_access.py``.

What this file holds, each one a test below:

* the list ``agent_first_party_client_ids`` defaults to empty, matches the
  exact client id URL only, and drops ``*`` and every entry that is not a
  CIMD client id URL;
* the redirect rule of the path with no click: ``https`` on the client's
  own origin, never loopback, never a private-use scheme, never another host;
* ``check`` for a listed client with a live connection whose scopes cover
  the request answers ``first_party`` with a code, and mints NO ticket;
* ``check`` for a listed client with no covering connection, with no
  ``state``, or with a redirect off its origin, shows the screen as before;
* an unlisted client that calls itself "Aroh", at another host or at the
  same host, is never trusted;
* ``approve`` for a listed client replaces the old connection and revokes
  its tokens. For any other client it adds a connection, as before;
* the organization allowlist and the member check still come first.

The classes marked R8 run the store on a real database as a NON-privileged
role, with the catalog of ``test_agent_oauth_server.py``. The gate is
``TENANT_LADDER_DATABASE_URL``. A skip is not a pass.

Run::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_agent_first_party.py -v -rs
"""
from __future__ import annotations

import ast
import json
import uuid
from pathlib import Path

import httpx
import pytest

pytest.importorskip("sqlalchemy")

from acb_auth import agent_oauth, agent_tokens
from acb_common import get_settings

from tests.unit._tenant_ladder import tenant_engine_scope

# The fixtures and helpers of the EA-2a suite. ``_flag_on`` and ``redis`` are
# autouse there, and the import makes them autouse here too.
from tests.unit.test_agent_oauth_server import (  # noqa: F401
    _DB_GATE,
    CHALLENGE,
    CLIENT,
    ISSUER,
    MEMBER,
    _admin_one,
    _approve,
    _authorize,
    _connection,
    _document,
    _edge_client,
    _exchange,
    _flag_on,
    _gateway_app,
    _gw,
    _query,
    _token,
    promoted,
    redis,
    web,
)

_ROOT = Path(__file__).resolve().parents[2]
_ACCESS_FILE = (
    _ROOT / "apps" / "services" / "gateway" / "gateway" / "routes" / "agent_access.py")

#: The first-party client of these tests, in the shape of Aroh's own
#: document (``https://aroh.dev.metorite.com/hub/oauth/client-metadata.json``).
AROH = "https://aroh.example/hub/oauth/client-metadata.json"
AROH_CB = "https://aroh.example/hub/auth/callback"

#: A client at ANOTHER host that calls itself "Aroh". Never trusted.
FAKE_AROH = "https://evil.example/hub/oauth/client-metadata.json"
FAKE_AROH_CB = "https://evil.example/hub/auth/callback"

#: A client at the SAME host, on another path, that calls itself "Aroh".
#: Never trusted either: the list names one client id URL, not a host.
SAME_HOST = "https://aroh.example/hub/oauth/other-metadata.json"

#: The scopes Aroh asks for.
AROH_SCOPE = "projects:read projects:write offline_access"

V2 = "v2-" + "b" * 50

#: A look-alike of "aroh" in Cyrillic letters, built from code points so the
#: source holds no ambiguous character.
_CYR_AROH = chr(0x0430) + chr(0x0440) + chr(0x043E) + "h"


def _aroh_doc(client_id: str, redirect_uris: list[str], name: str = "Aroh") -> bytes:
    return json.dumps(
        _document(client_id, client_name=name, redirect_uris=redirect_uris)).encode()


@pytest.fixture
def aroh_web(web):  # noqa: F811
    """The fake internet of the EA-2a suite, with Aroh and two look-alikes."""
    web.docs[AROH] = (200, _aroh_doc(AROH, [
        AROH_CB, "https://other.example/cb", "http://127.0.0.1/callback",
        "com.example.aroh:/cb"]))
    web.docs[FAKE_AROH] = (200, _aroh_doc(FAKE_AROH, [FAKE_AROH_CB]))
    web.docs[SAME_HOST] = (200, _aroh_doc(SAME_HOST, [AROH_CB]))
    return web


@pytest.fixture(autouse=True)
def _aroh_listed(monkeypatch):
    """The list names Aroh for every test, unless a test changes it."""
    monkeypatch.setattr(get_settings(), "agent_first_party_client_ids", AROH)


def _aroh(**over) -> dict:
    """An authorize request of Aroh."""
    base = {"client_id": AROH, "redirect_uri": AROH_CB, "scope": AROH_SCOPE}
    base.update(over)
    return _authorize(**base)


# ═══════════════════════════════════════════════════════════════════════════
# DB-free: the list and the redirect rule
# ═══════════════════════════════════════════════════════════════════════════


class TestTheList:
    """``agent_oauth.is_first_party``, the one reader of the list."""

    def test_the_setting_defaults_to_no_client(self):
        from acb_common.settings import Settings

        assert Settings.model_fields["agent_first_party_client_ids"].default == ""

    def test_an_empty_list_trusts_no_client(self, monkeypatch):
        monkeypatch.setattr(get_settings(), "agent_first_party_client_ids", "")
        assert agent_oauth.first_party_client_ids() == frozenset()
        assert not agent_oauth.is_first_party(AROH)

    def test_the_listed_id_is_trusted(self):
        assert agent_oauth.is_first_party(AROH)

    @pytest.mark.parametrize("client_id", [
        "https://AROH.example/hub/oauth/client-metadata.json",
        AROH + "/",
        AROH.replace("https://", "http://"),
        "https://aroh.example",
        "https://aroh.example/",
        SAME_HOST,
        FAKE_AROH,
        "Aroh",
        "",
        None,
        42,
    ], ids=["case", "slash", "http", "host", "host-slash", "same-host", "other-host",
            "name", "empty", "none", "int"])
    def test_a_near_miss_is_not_trusted(self, client_id):
        assert not agent_oauth.is_first_party(client_id)

    def test_spaces_go_and_two_entries_work(self, monkeypatch):
        monkeypatch.setattr(get_settings(), "agent_first_party_client_ids",
                            f"  {AROH} , {SAME_HOST}  ")
        assert agent_oauth.first_party_client_ids() == {AROH, SAME_HOST}

    @pytest.mark.parametrize("raw", [
        "*", " * ", "aroh.example", "https://aroh.example", "http://aroh.example/x.json",
        "https://aroh.example/x.json?q=1", "https://aroh.example/x.json#f",
        "https://u:p@aroh.example/x.json", "https://" + _CYR_AROH + ".example/x.json",
    ], ids=["star", "star-spaced", "host", "no-path", "http", "query", "fragment",
            "userinfo", "non-ascii"])
    def test_a_bad_entry_trusts_nothing(self, monkeypatch, raw):
        monkeypatch.setattr(get_settings(), "agent_first_party_client_ids", raw)
        assert agent_oauth.first_party_client_ids() == frozenset()
        assert not agent_oauth.is_first_party(raw.strip())


class TestTheRedirectRule:
    """``agent_oauth.first_party_redirect_allowed``: https, on the client's origin."""

    def test_the_clients_own_https_callback_passes(self):
        assert agent_oauth.first_party_redirect_allowed(AROH, AROH_CB)

    @pytest.mark.parametrize("uri", [
        "https://other.example/hub/auth/callback",
        "https://aroh.example.evil.example/cb",
        "https://aroh.example:8443/hub/auth/callback",
        "http://aroh.example/hub/auth/callback",
        "http://127.0.0.1:5000/callback",
        "http://localhost/callback",
        "com.example.aroh:/cb",
        "https://u:p@aroh.example/cb",
        "https://aroh.example/cb#frag",
        "",
    ], ids=["other-host", "suffix-host", "other-port", "http", "loopback",
            "localhost", "private-use", "userinfo", "fragment", "empty"])
    def test_every_other_redirect_is_refused(self, uri):
        assert not agent_oauth.first_party_redirect_allowed(AROH, uri)

    def test_a_client_id_that_is_not_cimd_is_refused(self):
        assert not agent_oauth.first_party_redirect_allowed("aroh", AROH_CB)


# ═══════════════════════════════════════════════════════════════════════════
# DB-free: the routes with a fake store
# ═══════════════════════════════════════════════════════════════════════════


class _Store:
    """A fake of the store calls the two routes make. It records each call."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []
        self.connections: list[agent_tokens.ClientConnection] = []
        self.mint_raises: BaseException | None = None


@pytest.fixture
def store(monkeypatch) -> _Store:
    fake = _Store()

    async def _no_client(client_id):
        return None

    async def _cached(client_id, metadata):
        return True

    async def _create(*a, **k):
        fake.calls.append(("create_connection", a, k))
        return str(uuid.uuid4())

    async def _mint(*a, **k):
        fake.calls.append(("mint_code", a, k))
        if fake.mint_raises is not None:
            raise fake.mint_raises
        return "code-" + uuid.uuid4().hex

    async def _live(*a, **k):
        fake.calls.append(("live_connections_of_client", a, k))
        return list(fake.connections)

    monkeypatch.setattr(agent_tokens, "get_client", _no_client)
    monkeypatch.setattr(agent_tokens, "cache_cimd_client", _cached)
    monkeypatch.setattr(agent_tokens, "create_connection", _create)
    monkeypatch.setattr(agent_tokens, "mint_code", _mint)
    monkeypatch.setattr(agent_tokens, "live_connections_of_client", _live)
    return fake


def _covering(*scopes: str) -> agent_tokens.ClientConnection:
    return agent_tokens.ClientConnection(id=str(uuid.uuid4()), scopes=tuple(scopes))


async def _check(org: str, body: dict, email: str = MEMBER) -> httpx.Response:
    return await _gw(_gateway_app(org, email), "/agent-access/authorize/check", body)


def _names(store: _Store) -> list[str]:
    return [c[0] for c in store.calls]


def _assert_screen(r: httpx.Response) -> dict:
    """The consent screen as before: a view with a ticket, and no redirect."""
    assert r.status_code == 200, r.text
    view = r.json()
    assert "first_party" not in view
    assert "redirect_to" not in view
    assert len(view["consent_ticket"]) == 43
    return view


class TestTheFirstPartyCheck:
    """Rule 13 on ``check``, with the store replaced."""

    async def test_a_covering_connection_skips_the_screen(self, aroh_web, store, redis):  # noqa: F811
        held = _covering("projects:read", "projects:write", "offline_access")
        store.connections = [held]
        org = str(uuid.uuid4())
        r = await _check(org, _aroh())
        assert r.status_code == 200, r.text
        body = r.json()
        assert set(body) == {"first_party", "redirect_to"}
        assert body["first_party"] is True
        assert body["redirect_to"].startswith(AROH_CB + "?")
        q = _query(body["redirect_to"])
        assert set(q) == {"code", "state", "iss"}
        assert q["state"] == "st-123"
        assert q["iss"] == ISSUER
        assert r.headers["cache-control"] == "no-store"
        # No ticket: the screen never showed.
        assert "setex" not in redis.calls
        assert _names(store) == ["live_connections_of_client", "mint_code"]
        (_, live_args, _) = store.calls[0]
        assert live_args == (MEMBER, AROH)
        (_, mint_args, mint_kw) = store.calls[1]
        assert mint_args == (held.id,)
        assert mint_kw == {
            "client_id": AROH, "code_challenge": CHALLENGE, "redirect_uri": AROH_CB,
            "resource": "https://mcp.metorite.com/mcp", "scope": AROH_SCOPE}

    async def test_no_connection_shows_the_screen(self, aroh_web, store):
        store.connections = []
        _assert_screen(await _check(str(uuid.uuid4()), _aroh()))
        assert "mint_code" not in _names(store)

    async def test_a_narrower_connection_shows_the_screen(self, aroh_web, store):
        store.connections = [_covering("projects:read", "offline_access")]
        _assert_screen(await _check(str(uuid.uuid4()), _aroh()))
        assert "mint_code" not in _names(store)

    async def test_a_write_scope_covers_a_read_request(self, aroh_web, store):
        store.connections = [_covering("projects:write", "offline_access")]
        r = await _check(str(uuid.uuid4()), _aroh(scope="projects:read offline_access"))
        assert r.json()["first_party"] is True
        assert store.calls[-1][2]["scope"] == "projects:read offline_access"

    async def test_offline_access_must_be_held_too(self, aroh_web, store):
        store.connections = [_covering("projects:read", "projects:write")]
        _assert_screen(await _check(str(uuid.uuid4()), _aroh()))

    async def test_the_first_covering_connection_wins(self, aroh_web, store):
        narrow = _covering("projects:read")
        wide = _covering("projects:read", "projects:write", "offline_access")
        store.connections = [narrow, wide]
        r = await _check(str(uuid.uuid4()), _aroh())
        assert r.json()["first_party"] is True
        assert store.calls[-1][1] == (wide.id,)

    @pytest.mark.parametrize("state", [None, ""], ids=["absent", "empty"])
    async def test_no_state_shows_the_screen(self, aroh_web, store, state):
        store.connections = [_covering("projects:read", "projects:write", "offline_access")]
        _assert_screen(await _check(str(uuid.uuid4()), _aroh(state=state)))
        assert "mint_code" not in _names(store)

    @pytest.mark.parametrize("uri", [
        "https://other.example/cb",
        "http://127.0.0.1:5000/callback",
        "com.example.aroh:/cb",
    ], ids=["other-host", "loopback-any-port", "private-use"])
    async def test_a_redirect_off_the_origin_shows_the_screen(self, aroh_web, store, uri):
        """The document lists each of these, so the request is valid. Only
        the path with no click refuses them."""
        store.connections = [_covering("projects:read", "projects:write", "offline_access")]
        _assert_screen(await _check(str(uuid.uuid4()), _aroh(redirect_uri=uri)))
        assert "mint_code" not in _names(store)

    async def test_the_empty_default_list_skips_nothing(self, aroh_web, store, monkeypatch):
        monkeypatch.setattr(get_settings(), "agent_first_party_client_ids", "")
        store.connections = [_covering("projects:read", "projects:write", "offline_access")]
        _assert_screen(await _check(str(uuid.uuid4()), _aroh()))
        assert "live_connections_of_client" not in _names(store)

    @pytest.mark.parametrize(("client_id", "redirect", "host"), [
        (FAKE_AROH, FAKE_AROH_CB, "evil.example"),
        (SAME_HOST, AROH_CB, "aroh.example"),
    ], ids=["other-host", "same-host"])
    async def test_a_client_that_calls_itself_aroh_is_not_trusted(
        self, aroh_web, store, client_id, redirect, host
    ):
        store.connections = [_covering("projects:read", "projects:write", "offline_access")]
        view = _assert_screen(await _check(
            str(uuid.uuid4()), _aroh(client_id=client_id, redirect_uri=redirect)))
        # The screen names the verified host first, and "Aroh" only as a claim.
        assert view["client"]["host"] == host
        assert view["client"]["name"] == "Aroh"
        assert "live_connections_of_client" not in _names(store)

    async def test_a_revoke_after_the_read_shows_the_screen(self, aroh_web, store):
        store.connections = [_covering("projects:read", "projects:write", "offline_access")]
        store.mint_raises = LookupError("no live connection with that id in this tenant")
        _assert_screen(await _check(str(uuid.uuid4()), _aroh()))

    @pytest.mark.parametrize("over", [
        {"code_challenge": None, "code_challenge_method": None},
        {"code_challenge_method": "plain"},
        {"resource": "https://other.example/mcp"},
        {"response_type": "token"},
    ], ids=["no-pkce", "plain", "wrong-resource", "wrong-response-type"])
    async def test_every_other_check_still_runs_first(self, aroh_web, store, over):
        store.connections = [_covering("projects:read", "projects:write", "offline_access")]
        r = await _check(str(uuid.uuid4()), _aroh(**over))
        assert r.status_code == 400, r.text
        assert "mint_code" not in _names(store)

    async def test_a_redirect_that_the_document_does_not_list_is_refused(
        self, aroh_web, store
    ):
        store.connections = [_covering("projects:read", "projects:write", "offline_access")]
        r = await _check(str(uuid.uuid4()), _aroh(redirect_uri=AROH_CB + "/x"))
        assert r.status_code == 400, r.text
        assert r.json()["detail"]["redirect_to"] is None
        assert "mint_code" not in _names(store)

    async def test_the_org_allowlist_still_comes_first(self, aroh_web, store, monkeypatch):
        monkeypatch.setattr(get_settings(), "agent_access_orgs", str(uuid.uuid4()))
        store.connections = [_covering("projects:read", "projects:write", "offline_access")]
        r = await _check(str(uuid.uuid4()), _aroh())
        assert r.status_code == 403
        assert r.json()["detail"]["error"] == "org_not_enabled"
        assert store.calls == []

    async def test_a_caller_who_is_not_a_member_is_refused(self, aroh_web, store):
        store.connections = [_covering("projects:read", "projects:write", "offline_access")]
        r = await _check(str(uuid.uuid4()), _aroh(), email="not-an-address")
        assert r.status_code == 403
        assert store.calls == []

    async def test_the_flag_off_is_404(self, aroh_web, store, monkeypatch):
        monkeypatch.setattr(get_settings(), "agent_access_enabled", False)
        store.connections = [_covering("projects:read", "projects:write", "offline_access")]
        r = await _check(str(uuid.uuid4()), _aroh())
        assert r.status_code == 404
        assert store.calls == []


class TestTheFirstPartyApprove:
    """Rule 13 on ``approve``: replace for a listed client only."""

    @staticmethod
    async def _approve(org: str, body: dict) -> httpx.Response:
        # The ticket of a screen that ``check`` showed for this request.
        r = await _check(org, body)
        view = _assert_screen(r)
        return await _gw(_gateway_app(org), "/agent-access/authorize/approve",
                         {**body, "consent_ticket": view["consent_ticket"]})

    @pytest.mark.parametrize(("body", "replace"), [
        (_aroh(), True),
        (_aroh(client_id=FAKE_AROH, redirect_uri=FAKE_AROH_CB), False),
        (_aroh(client_id=SAME_HOST), False),
        (_authorize(), False),
    ], ids=["aroh", "lookalike-other-host", "lookalike-same-host", "other-client"])
    async def test_replace_is_on_for_a_listed_client_only(self, aroh_web, store, body, replace):
        store.connections = []
        r = await self._approve(str(uuid.uuid4()), body)
        assert r.status_code == 200, r.text
        (create,) = [c for c in store.calls if c[0] == "create_connection"]
        assert create[1][0] == MEMBER
        assert create[2]["replace_same_client"] is replace

    async def test_the_approve_of_a_listed_client_still_needs_the_ticket(
        self, aroh_web, store
    ):
        r = await _gw(_gateway_app(str(uuid.uuid4())), "/agent-access/authorize/approve",
                      _aroh())
        assert r.status_code == 400
        assert r.json()["detail"]["error"] == "invalid_request"
        assert "create_connection" not in _names(store)


class TestTheStructuralFences:
    """R7: the rules of rule 13, read from the source of ``agent_access.py``."""

    @staticmethod
    def _tree() -> ast.Module:
        return ast.parse(_ACCESS_FILE.read_text(encoding="utf-8"))

    def _calls(self, suffix: str) -> list[ast.Call]:
        return [
            node for node in ast.walk(self._tree())
            if isinstance(node, ast.Call) and ast.unparse(node.func).endswith(suffix)
        ]

    def test_replace_reads_the_list_and_nothing_else(self):
        (create,) = self._calls("create_connection")
        (kw,) = [k for k in create.keywords if k.arg == "replace_same_client"]
        assert ast.unparse(kw.value) == "agent_oauth.is_first_party(req.client_id)"

    def test_the_connection_read_takes_the_member_of_the_session(self):
        (read,) = self._calls("live_connections_of_client")
        assert [ast.unparse(a) for a in read.args] == ["email", "req.client_id"]
        assert not read.keywords
        (helper,) = self._calls("_first_party_redirect")
        assert [ast.unparse(a) for a in helper.args] == ["req", "email", "validated"]

    def test_check_runs_the_member_list_and_validate_before_the_skip(self):
        check = next(
            node for node in ast.walk(self._tree())
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "check_authorize")
        order = [ast.unparse(s) for s in check.body
                 if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]
        assert order[:4] == [
            "email = _member(user)",
            "_require_org_allowed()",
            "validated = await _validate(req)",
            "continued = await _first_party_redirect(req, email, validated)",
        ]

    def test_only_the_first_party_path_mints_a_code_in_check(self):
        """Two homes of ``mint_code``: approve, and the helper of rule 13."""
        homes = []
        for fn in ast.walk(self._tree()):
            if isinstance(fn, ast.AsyncFunctionDef):
                homes += [
                    fn.name for node in ast.walk(fn)
                    if isinstance(node, ast.Call)
                    and ast.unparse(node.func).endswith("mint_code")
                ]
        assert sorted(homes) == ["_first_party_redirect", "approve_authorize"]


# ═══════════════════════════════════════════════════════════════════════════
# R8: the flows on a real database, as a non-privileged role
# ═══════════════════════════════════════════════════════════════════════════


def _live_count(promoted, client_id: str, email: str) -> int:  # noqa: F811
    return _admin_one(
        promoted,
        "SELECT count(*) FROM agent_connection "
        " WHERE client_id = :c AND member_email = :m AND revoked_at IS NULL",
        c=client_id, m=email)[0]


def _unrevoked_tokens(promoted, connection_id: str) -> int:  # noqa: F811
    return _admin_one(
        promoted,
        "SELECT count(*) FROM agent_token "
        " WHERE connection_id = CAST(:c AS uuid) AND revoked_at IS NULL",
        c=connection_id)[0]


def _connection_of_code(promoted, code: str) -> str:  # noqa: F811
    return str(_admin_one(
        promoted, "SELECT connection_id FROM agent_token WHERE token_hash = :h",
        h=agent_tokens.hash_token(code))[0])


def _someone() -> str:
    return f"fp-{uuid.uuid4().hex[:10]}@agentoauth.test"


async def _sign_in(
    promoted, email: str, org: str | None = None, *, verifier: str | None = None, **over  # noqa: F811
) -> tuple[str, dict]:
    """A sign-in through the screen: Approve, then the token.

    With *verifier*, the request carries its S256 challenge, as a second
    device with its own PKCE pair does.
    """
    if verifier is not None:
        over["code_challenge"] = agent_oauth.s256(verifier)
    r = await _approve(promoted, org, session_email=email, **{
        "client_id": AROH, "redirect_uri": AROH_CB, "scope": AROH_SCOPE, **over})
    assert r.status_code == 200, r.text
    code = _query(r.json()["redirect_to"])["code"]
    extra = {"code_verifier": verifier} if verifier is not None else {}
    t = await _token(_exchange(code, client_id=AROH, redirect_uri=AROH_CB, **extra))
    assert t.status_code == 200, t.text
    return code, t.json()


@_DB_GATE
class TestTheFirstPartyFlowOnARealDatabase:
    """The done-when of task 1109, end to end through the gateway and the edge."""

    async def test_a_second_device_signs_in_with_no_click(self, promoted, aroh_web, redis):  # noqa: F811
        email = _someone()
        async with tenant_engine_scope(promoted.app_dsn):
            code1, tokens1 = await _sign_in(promoted, email)
            redis.calls.clear()
            # The second device: its own PKCE pair, no screen, no ticket.
            r = await _check(promoted.org_a, _aroh(code_challenge=agent_oauth.s256(V2)), email)
            assert r.status_code == 200, r.text
            assert r.json()["first_party"] is True
            assert "setex" not in redis.calls
            code2 = _query(r.json()["redirect_to"])["code"]
            t2 = await _token(_exchange(
                code2, client_id=AROH, redirect_uri=AROH_CB, code_verifier=V2))
            assert t2.status_code == 200, t2.text
            first = await agent_tokens.lookup_token(tokens1["access_token"])
            second = await agent_tokens.lookup_token(t2.json()["access_token"])
        # One connection: the code went on the member's own row.
        assert _connection_of_code(promoted, code2) == _connection_of_code(promoted, code1)
        assert _live_count(promoted, AROH, email) == 1
        # The first device stays signed in.
        assert first is not None
        assert second is not None
        assert t2.json()["scope"] == AROH_SCOPE
        assert t2.json()["refresh_token"]

    async def test_no_connection_shows_the_screen(self, promoted, aroh_web):  # noqa: F811
        async with tenant_engine_scope(promoted.app_dsn):
            r = await _check(promoted.org_a, _aroh(), _someone())
        _assert_screen(r)

    async def test_a_new_approval_replaces_the_old_connection(self, promoted, aroh_web):  # noqa: F811
        email = _someone()
        async with tenant_engine_scope(promoted.app_dsn):
            code1, tokens1 = await _sign_in(promoted, email)
            code2, tokens2 = await _sign_in(promoted, email, verifier=V2)
            old_access = await agent_tokens.lookup_token(tokens1["access_token"])
            old_refresh = await agent_tokens.lookup_token(
                tokens1["refresh_token"], kind="refresh")
            new_access = await agent_tokens.lookup_token(tokens2["access_token"])
        old_id = _connection_of_code(promoted, code1)
        new_id = _connection_of_code(promoted, code2)
        assert old_id != new_id
        assert _connection(promoted, old_id).revoked
        assert not _connection(promoted, new_id).revoked
        assert _live_count(promoted, AROH, email) == 1
        assert old_access is None, "the replaced connection kept a live access token"
        assert old_refresh is None, "the replaced connection kept a live refresh token"
        assert new_access is not None
        # The token rows themselves are revoked, not only hidden by the dead
        # connection: a lookup refuses both, so read the rows.
        assert _unrevoked_tokens(promoted, old_id) == 0
        assert _unrevoked_tokens(promoted, new_id) > 0

    async def test_a_replace_touches_only_this_member_and_client(self, promoted, aroh_web):  # noqa: F811
        email, other = _someone(), _someone()
        async with tenant_engine_scope(promoted.app_dsn):
            _, mine = await _sign_in(promoted, email)
            _, theirs = await _sign_in(promoted, other)
            r = await _approve(promoted, session_email=email)  # another client
            assert r.status_code == 200, r.text
            await _sign_in(promoted, email)  # replaces the member's own Aroh row
            await _sign_in(promoted, email, org=promoted.org_b)
            theirs_after = await agent_tokens.lookup_token(theirs["access_token"])
            mine_after = await agent_tokens.lookup_token(mine["access_token"])
        assert mine_after is None
        assert theirs_after is not None, "a replace revoked another member's row"
        assert _live_count(promoted, AROH, other) == 1
        assert _live_count(promoted, CLIENT, email) == 1
        # One Aroh row in each organization.
        assert _live_count(promoted, AROH, email) == 2

    async def test_another_client_is_unchanged(self, promoted, aroh_web):  # noqa: F811
        """A client off the list: each approval adds a row, and check never
        skips the screen, even with a live connection that covers it."""
        email = _someone()
        async with tenant_engine_scope(promoted.app_dsn):
            for _ in range(2):
                r = await _approve(promoted, session_email=email)
                assert r.status_code == 200, r.text
            check = await _check(promoted.org_a, _authorize(), email)
        _assert_screen(check)
        assert _live_count(promoted, CLIENT, email) == 2

    @pytest.mark.parametrize(("client_id", "redirect"), [
        (FAKE_AROH, FAKE_AROH_CB), (SAME_HOST, AROH_CB),
    ], ids=["other-host", "same-host"])
    async def test_a_client_that_calls_itself_aroh_is_not_trusted(
        self, promoted, aroh_web, client_id, redirect  # noqa: F811
    ):
        email = _someone()
        async with tenant_engine_scope(promoted.app_dsn):
            for _ in range(2):
                r = await _approve(promoted, session_email=email, client_id=client_id,
                                   redirect_uri=redirect, scope=AROH_SCOPE)
                assert r.status_code == 200, r.text
            check = await _check(
                promoted.org_a, _aroh(client_id=client_id, redirect_uri=redirect), email)
        _assert_screen(check)
        assert _live_count(promoted, client_id, email) == 2

    async def test_a_revoked_connection_shows_the_screen(self, promoted, aroh_web):  # noqa: F811
        email = _someone()
        async with tenant_engine_scope(promoted.app_dsn):
            code, _ = await _sign_in(promoted, email)
            assert await agent_tokens.revoke_connection_scoped(
                promoted.org_a, _connection_of_code(promoted, code), member_email=email)
            r = await _check(promoted.org_a, _aroh(), email)
        _assert_screen(r)

    async def test_another_members_connection_does_not_count(self, promoted, aroh_web):  # noqa: F811
        async with tenant_engine_scope(promoted.app_dsn):
            await _sign_in(promoted, _someone())
            r = await _check(promoted.org_a, _aroh(), _someone())
        _assert_screen(r)

    async def test_another_organizations_connection_does_not_count(self, promoted, aroh_web):  # noqa: F811
        email = _someone()
        async with tenant_engine_scope(promoted.app_dsn):
            await _sign_in(promoted, email)
            r = await _check(promoted.org_b, _aroh(), email)
        _assert_screen(r)

    async def test_a_narrower_approval_asks_again_for_more(self, promoted, aroh_web):  # noqa: F811
        email = _someone()
        async with tenant_engine_scope(promoted.app_dsn):
            await _sign_in(promoted, email, scope="projects:read offline_access")
            r = await _check(promoted.org_a, _aroh(), email)
        _assert_screen(r)

    async def test_the_skip_keeps_the_pins_and_the_personal_switch(self, promoted, aroh_web):  # noqa: F811
        email = _someone()
        pin = str(uuid.uuid4())
        async with tenant_engine_scope(promoted.app_dsn):
            code1, _ = await _sign_in(promoted, email, pinned_projects=[pin],
                                      include_personal=False)
            r = await _check(promoted.org_a, _aroh(), email)
            code2 = _query(r.json()["redirect_to"])["code"]
        row = _connection(promoted, _connection_of_code(promoted, code2))
        assert _connection_of_code(promoted, code2) == _connection_of_code(promoted, code1)
        assert row.pins == [pin]
        assert row.include_personal is False

    async def test_a_code_of_the_skip_works_once(self, promoted, aroh_web):  # noqa: F811
        email = _someone()
        async with tenant_engine_scope(promoted.app_dsn):
            await _sign_in(promoted, email)
            r = await _check(promoted.org_a, _aroh(code_challenge=agent_oauth.s256(V2)), email)
            code = _query(r.json()["redirect_to"])["code"]
            form = _exchange(code, client_id=AROH, redirect_uri=AROH_CB, code_verifier=V2)
            first = await _token(form)
            again = await _token(form)
        assert first.status_code == 200, first.text
        assert again.status_code == 400
        assert again.json()["error"] == "invalid_grant"

    async def test_a_code_of_the_skip_needs_its_own_verifier(self, promoted, aroh_web):  # noqa: F811
        """A caller who starts the flow with a challenge it made still needs
        the verifier of THAT challenge, and the redirect of the code."""
        email = _someone()
        async with tenant_engine_scope(promoted.app_dsn):
            await _sign_in(promoted, email)
            r = await _check(promoted.org_a, _aroh(code_challenge=agent_oauth.s256(V2)), email)
            code = _query(r.json()["redirect_to"])["code"]
            wrong = await _token(_exchange(code, client_id=AROH, redirect_uri=AROH_CB))
            other_uri = await _token(_exchange(
                code, client_id=AROH, redirect_uri=AROH_CB + "x", code_verifier=V2))
        assert wrong.status_code == 400
        assert other_uri.status_code == 400
