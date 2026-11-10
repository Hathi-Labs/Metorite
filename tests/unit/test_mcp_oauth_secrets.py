"""MCP OAuth: no token and no secret out (WS-54 IN-8a, row O6).

Spec: ``project-docs/specs/integrations_console.md`` §3.6.6 and §3.6.11. A
sentinel access token, refresh token and DCR client secret show in no log
line, no response body, no redirect URL and no ``provider_meta`` column.

⚠️ The column case is the reason for §3.6.6: ``save_credential`` and
``update_tokens`` copy ``SourceCredential.provider_meta`` into the PLAIN
column. So the secret has its own sealed field, ``client_secret``, and no SQL
parameter of the store reads it. The R8 suite reads the real column too
(``test_mcp_oauth_org_scope_r8.py``).

Mutation M7 of ``test_mcp_oauth_flow.py`` fails here.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import structlog

from tests.unit import _mcp_oauth_fakes as f

ORG_A = "11111111-1111-1111-1111-111111111111"
ADMIN = "admin@a.test"


@pytest.fixture
def redis(monkeypatch) -> f.FakeRawRedis:
    return f.configure(monkeypatch)


@pytest.fixture
def world(monkeypatch, redis) -> f.World:
    return f.install(monkeypatch, f.World())


def _assert_clean(where: str, text: str) -> None:
    for sentinel in f.SENTINELS:
        assert sentinel not in text, f"a sentinel reached {where}"


async def test_no_secret_reaches_a_log_a_response_a_url_or_a_column(
        monkeypatch, world, redis, caplog) -> None:
    reg = f.fake_registry(monkeypatch, ORG_A)
    admin = f.user(ADMIN, ORG_A)
    with structlog.testing.capture_logs() as logs:
        start = await f.call(admin, "POST", "/integrations/mcp/notion/oauth/start")
        url = start.json()["url"]
        code, state = world.consent(url)
        done = await f.finish(admin, code, state, iss=f.ISSUER)
    assert done.status_code == 302 and len(reg.attached) == 1
    _assert_clean("the start body", start.text)
    _assert_clean("the consent URL", url)
    _assert_clean("the redirect URL", done.headers["location"])
    _assert_clean("the callback body", done.text)
    _assert_clean("a log line", f.text_of(logs, caplog))
    # Redis holds the pending flow for ten minutes. The secret in it is sealed.
    assert redis.data == {}, "the callback took the pending flow"
    cred = reg.attached[0]["credential"]
    _assert_clean("the credential repr", repr(cred))
    _assert_clean("provider_meta", json.dumps(dict(cred.provider_meta)))
    assert cred.client_secret == f.SENTINEL_SECRET  # it is kept, in its own field


async def test_the_pending_flow_holds_the_secret_sealed(monkeypatch, world, redis) -> None:
    f.fake_registry(monkeypatch, ORG_A)
    await f.call(f.user(ADMIN, ORG_A), "POST", "/integrations/mcp/notion/oauth/start")
    (raw,) = redis.data.values()
    _assert_clean("Redis", raw)
    from gateway.connections import store

    assert store.open_value(json.loads(raw)["client_secret_sealed"]) == f.SENTINEL_SECRET


async def test_a_provider_answer_that_echoes_a_secret_reaches_nothing(
        monkeypatch, world, caplog) -> None:
    reg = f.fake_registry(monkeypatch, ORG_A)
    admin = f.user(ADMIN, ORG_A)
    _, code, state = await f.start_and_consent(admin, world)
    world.fixed[(f.AUTH_HOST, "/token")] = httpx.Response(400, json={
        "error": f"bad {f.SENTINEL_SECRET}", "error_description": f.SENTINEL_ACCESS})
    with structlog.testing.capture_logs() as logs:
        res = await f.finish(admin, code, state, iss=f.ISSUER)
    assert res.headers["location"] == "/integrations?mcp_connect_error=exchange_failed"
    _assert_clean("the redirect URL", res.headers["location"])
    _assert_clean("a log line", f.text_of(logs, caplog))
    assert reg.attached == []


# ── The store: the column and the blob ──────────────────────────────────────


class _Db:
    """Records each statement of the store, and answers the row lock."""

    def __init__(self, meta: dict[str, Any]) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.row = SimpleNamespace(
            id="00000000-0000-0000-0000-0000000000aa", organization_id=ORG_A,
            provider="mcp:notion", owning_app="integrations", scope_kind="org",
            member_email=None, status="connected", provider_meta=meta, scopes=[],
            connected_by="first@a.test", last_sync_at=None, last_error=None,
            created_at=None, updated_at=None, credentials_encrypted=None)

    async def execute(self, statement: Any, params: dict[str, Any]) -> Any:
        sql = str(statement)
        self.calls.append((sql, dict(params)))
        if sql.startswith("UPDATE"):
            merged = {**self.row.provider_meta, **json.loads(params["provider_meta"])}
            after = SimpleNamespace(**{**self.row.__dict__, "provider_meta": merged,
                                       "connected_by": params["connected_by"]})
            return SimpleNamespace(first=lambda: after)
        return SimpleNamespace(first=lambda: self.row)


def _credential() -> Any:
    from gateway.crm_sources.base import SourceCredential

    return SourceCredential(
        access_token=f.SENTINEL_ACCESS, refresh_token=f.SENTINEL_REFRESH,
        expires_at=datetime(2030, 1, 1, tzinfo=UTC), scopes=("read",),
        provider_meta={"issuer": f.ISSUER, "client_id": "dcr-client-1",
                       "registration": "dcr"},
        client_secret=f.SENTINEL_SECRET)


async def test_the_store_seals_the_secret_and_no_plain_parameter_holds_it(monkeypatch) -> None:
    f.configure(monkeypatch)
    from gateway.connections import store

    db = _Db({"url": f.MCP_URL, "always_confirm": True, "name": "notion"})
    done = await store.attach_oauth_credential(
        db, ORG_A, "mcp:notion", "integrations", _credential(),
        connected_by="Admin@A.test", scopes=("read",))
    assert done is not None
    update = next(p for sql, p in db.calls if sql.startswith("UPDATE"))
    plain = {k: v for k, v in update.items() if k != "blob"}
    _assert_clean("a plain SQL parameter", json.dumps(plain, default=str))
    _assert_clean("the provider_meta parameter", update["provider_meta"])
    # The blob is ciphertext, and it opens to every secret.
    _assert_clean("the blob as stored", update["blob"])
    opened = store._unseal(update["blob"])
    assert opened.client_secret == f.SENTINEL_SECRET
    assert opened.access_token == f.SENTINEL_ACCESS
    # The keys of the row stay, and the public facts merge in (§3.6.6).
    meta = done.after.provider_meta
    assert meta["url"] == f.MCP_URL and meta["always_confirm"] is True
    assert meta["auth"] == "oauth" and meta["registration"] == "dcr"
    assert done.after.connected_by == "admin@a.test"


def test_a_token_blob_without_a_secret_keeps_its_shape(monkeypatch) -> None:
    """Zoho and GitHub blobs gain no key (IN-8a adds ``client_secret`` only
    when it is set), and an old blob opens with an empty secret."""
    f.configure(monkeypatch)
    from dataclasses import replace

    from gateway.connections import store

    blob = store._seal(replace(_credential(), client_secret=""))
    assert "client_secret" not in json.loads(store._keys().decrypt(blob))
    assert store._unseal(blob).client_secret == ""
    assert f.SENTINEL_SECRET not in repr(_credential())
