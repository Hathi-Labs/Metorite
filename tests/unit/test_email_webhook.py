"""Unit tests for the Microsoft Graph push-notification endpoint.

Covers the responsibilities of `/email/webhook/microsoft` and of the
subscription that feeds it (EM-T1a, `email_app_master_plan.md` §10.4.1,
items 5 to 7):

  1. the validation handshake (echo the validationToken, first and unchanged);
  2. the signed `org` in the URL — missing, malformed or unsigned queues
     nothing and answers 202;
  3. notification routing inside ONE `tenant_session(org)` — clientState
     compared with `hmac.compare_digest`, a NULL stored value refused;
  4. `_ensure_subscription` binds a tenant or does nothing, and writes a
     signed `notificationUrl` on create and on renew.

The DB session is faked, so no DB or mailbox is required. The R8 half (a
webhook that names org A with a subscription of org B matches nothing under
FORCE RLS) is in `test_email_tenant_bind_rls.py`.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import parse_qs, urlparse

import pytest
from acb_common import get_settings
from fastapi import BackgroundTasks
from gateway.db import bind_tenant, current_tenant, release_tenant
from gateway.routes import email as m
from gateway.routes.email.transport import signing
from gateway.routes.email.transport import sync as sync_mod

ORG = "11111111-2222-3333-4444-555555555555"
OTHER_ORG = "99999999-8888-7777-6666-555555555555"


@pytest.fixture(autouse=True)
def _secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "gateway_session_secret", "wh-secret", raising=False)


class _Req:
    """Minimal stand-in for starlette Request (query_params + json())."""

    def __init__(self, query: dict | None = None, body: dict | None = None):
        self.query_params = query or {}
        self._body = body

    async def json(self):
        if self._body is None:
            raise ValueError("no body")
        return self._body


def _signed(org: str = ORG) -> dict:
    return {"org": org, "sig": signing.sign_webhook_org(org)}


class _Sessions:
    """A fake `_tenant_session` that records the org of every open."""

    def __init__(self, row=None) -> None:
        self.opened: list[str | None] = []
        self.db = AsyncMock()
        result = MagicMock()
        result.fetchone.return_value = row
        self.db.execute.return_value = result

    @asynccontextmanager
    async def __call__(self, org=None):
        self.opened.append(org)
        yield self.db


@pytest.fixture()
def sessions(monkeypatch) -> _Sessions:
    s = _Sessions()
    monkeypatch.setattr(sync_mod, "_tenant_session", s)
    return s


def _with_row(sessions: _Sessions, row) -> None:
    sessions.db.execute.return_value.fetchone.return_value = row


# ── 1. validation ───────────────────────────────────────────────────────────


async def test_validation_handshake_echoes_token(sessions) -> None:
    req = _Req(query={"validationToken": "tok-abc-123"})
    bg = BackgroundTasks()
    resp = await m.microsoft_webhook(req, bg)
    assert resp.status_code == 200
    assert resp.body == b"tok-abc-123"
    assert len(bg.tasks) == 0  # validation must not trigger a sync
    assert sessions.opened == []


async def test_validation_echo_comes_before_the_signature_check(sessions) -> None:
    """Graph validates a NEW notificationUrl, signed or not. The echo must not
    depend on `org`, or a URL update could never validate."""
    req = _Req(query={"validationToken": "t", "org": "junk", "sig": "junk"})
    resp = await m.microsoft_webhook(req, BackgroundTasks())
    assert resp.status_code == 200 and resp.body == b"t"


# ── 2. the signed org ───────────────────────────────────────────────────────

_BODY = {"value": [{"subscriptionId": "sub-x", "clientState": "cs-match"}]}


@pytest.mark.parametrize(
    "query",
    [
        {},
        {"org": ORG},
        {"sig": "abc"},
        {"org": "not-a-uuid", "sig": "abc"},
        {"org": ORG, "sig": "forged"},
        {"org": OTHER_ORG, "sig": "A" * 43},
    ],
    ids=["missing", "no-sig", "no-org", "malformed", "forged", "wrong-sig"],
)
async def test_an_unsigned_org_queues_nothing(sessions, query) -> None:
    _with_row(sessions, SimpleNamespace(id="acc-1", webhook_client_state="cs-match"))
    bg = BackgroundTasks()
    resp = await m.microsoft_webhook(_Req(query=query, body=_BODY), bg)
    assert resp.status_code == 202
    assert len(bg.tasks) == 0
    assert sessions.opened == [], "an unsigned org must not open a session"


async def test_a_signature_for_another_org_queues_nothing(sessions) -> None:
    query = {"org": OTHER_ORG, "sig": signing.sign_webhook_org(ORG)}
    _with_row(sessions, SimpleNamespace(id="acc-1", webhook_client_state="cs-match"))
    bg = BackgroundTasks()
    resp = await m.microsoft_webhook(_Req(query=query, body=_BODY), bg)
    assert resp.status_code == 202 and len(bg.tasks) == 0


# ── 3. routing inside the tenant ────────────────────────────────────────────


async def test_unknown_subscription_queues_nothing(sessions) -> None:
    bg = BackgroundTasks()
    resp = await m.microsoft_webhook(_Req(query=_signed(), body=_BODY), bg)
    assert resp.status_code == 202
    assert len(bg.tasks) == 0
    assert sessions.opened == [ORG]


async def test_known_subscription_queues_one_sync_bound_to_its_org(sessions) -> None:
    _with_row(sessions, SimpleNamespace(id="acc-1", webhook_client_state="cs-match"))
    bg = BackgroundTasks()
    resp = await m.microsoft_webhook(_Req(query=_signed(), body=_BODY), bg)
    assert resp.status_code == 202
    assert sessions.opened == [ORG], "one tenant_session, bound to the signed org"
    assert len(bg.tasks) == 1
    assert bg.tasks[0].func is sync_mod._webhook_sync
    assert bg.tasks[0].args == ("acc-1", ORG)


async def test_many_notifications_share_one_tenant_session(sessions) -> None:
    _with_row(sessions, SimpleNamespace(id="acc-1", webhook_client_state="cs-match"))
    body = {"value": [
        {"subscriptionId": "sub-x", "clientState": "cs-match"},
        {"subscriptionId": "sub-x", "clientState": "cs-match"},
        {"subscriptionId": "sub-y", "clientState": "cs-match"},
    ]}
    bg = BackgroundTasks()
    await m.microsoft_webhook(_Req(query=_signed(), body=body), bg)
    assert sessions.opened == [ORG]
    assert len(bg.tasks) == 1, "one account, one sync"


async def test_client_state_mismatch_is_ignored(sessions) -> None:
    _with_row(sessions, SimpleNamespace(id="acc-1", webhook_client_state="cs-correct"))
    body = {"value": [{"subscriptionId": "sub-x", "clientState": "FORGED"}]}
    bg = BackgroundTasks()
    resp = await m.microsoft_webhook(_Req(query=_signed(), body=body), bg)
    assert resp.status_code == 202
    assert len(bg.tasks) == 0  # spoofed clientState rejected


@pytest.mark.parametrize("stored", [None, ""])
async def test_a_null_stored_client_state_queues_nothing(sessions, stored) -> None:
    """Item 6. The old check skipped the comparison for a NULL stored value,
    so anybody who knew a subscription id could start a sync."""
    _with_row(sessions, SimpleNamespace(id="acc-1", webhook_client_state=stored))
    for sent in ("anything", None, ""):
        body = {"value": [{"subscriptionId": "sub-x", "clientState": sent}]}
        bg = BackgroundTasks()
        await m.microsoft_webhook(_Req(query=_signed(), body=body), bg)
        assert len(bg.tasks) == 0, f"stored={stored!r} sent={sent!r}"


async def test_a_non_ascii_client_state_does_not_crash(sessions) -> None:
    _with_row(sessions, SimpleNamespace(id="acc-1", webhook_client_state="cs"))
    body = {"value": [{"subscriptionId": "sub-x", "clientState": "çs"}]}
    bg = BackgroundTasks()
    resp = await m.microsoft_webhook(_Req(query=_signed(), body=body), bg)
    assert resp.status_code == 202 and len(bg.tasks) == 0


def test_the_comparison_is_constant_time() -> None:
    import inspect

    assert "hmac.compare_digest" in inspect.getsource(sync_mod._client_state_matches)


async def test_malformed_body_returns_202_without_crashing(sessions) -> None:
    bg = BackgroundTasks()
    resp = await m.microsoft_webhook(_Req(query=_signed(), body=None), bg)
    assert resp.status_code == 202
    assert len(bg.tasks) == 0


async def test_a_lookup_failure_returns_202(monkeypatch) -> None:
    @asynccontextmanager
    async def _boom(org=None):
        raise RuntimeError("db down")
        yield  # pragma: no cover

    monkeypatch.setattr(sync_mod, "_tenant_session", _boom)
    bg = BackgroundTasks()
    resp = await m.microsoft_webhook(_Req(query=_signed(), body=_BODY), bg)
    assert resp.status_code == 202 and len(bg.tasks) == 0


async def test_the_webhook_sync_binds_its_org_and_releases_it(monkeypatch) -> None:
    import email_ingestion.scheduler as sched
    from gateway.routes.email import scheduler_hooks

    seen: dict[str, str | None] = {}

    async def _sync(account_id):
        seen["sync"] = current_tenant()
        return {"synced": 1}

    async def _pipeline(account_id):
        seen["pipeline"] = current_tenant()

    monkeypatch.setattr(sched, "_sync_account", _sync)
    monkeypatch.setattr(scheduler_hooks, "process_new_mail", _pipeline)
    before = current_tenant()
    await sync_mod._webhook_sync("acc-1", ORG)
    assert seen == {"sync": ORG, "pipeline": ORG}
    assert current_tenant() == before, "the binding must not leak past the task"


# ── 4. _ensure_subscription ────────────────────────────────────────────────


class _Graph:
    def __init__(self, *, renew_fails: bool = False) -> None:
        self.created: list[tuple[str, str]] = []
        self.renewed: list[tuple[str, str | None]] = []
        self.deleted: list[str] = []
        self.renew_fails = renew_fails

    async def authenticate(self) -> bool:
        return True

    def credentials_dirty(self) -> bool:
        return False

    async def create_subscription(self, url, client_state):
        self.created.append((url, client_state))
        return {"id": "sub-new", "expirationDateTime": "2026-10-04T00:00:00Z"}

    async def renew_subscription(self, sub_id, notification_url=None):
        self.renewed.append((sub_id, notification_url))
        if self.renew_fails:
            raise RuntimeError("Graph refused the PATCH")
        return {"id": sub_id, "expirationDateTime": "2026-10-04T00:00:00Z"}

    async def delete_subscription(self, sub_id):
        self.deleted.append(sub_id)


@pytest.fixture()
def graph_env(monkeypatch, sessions):
    from acb_llm import key_store

    monkeypatch.setenv("GATEWAY_PUBLIC_URL", "https://api.example.test")
    store = MagicMock()
    store.decrypt.return_value = "{}"
    monkeypatch.setattr(key_store, "get_key_store", lambda: store)
    return sessions


def _account(**over):
    row = {
        "provider": "microsoft",
        "credentials_encrypted": "x",
        "webhook_subscription_id": None,
        "webhook_client_state": None,
        "webhook_expires_at": None,
    }
    row.update(over)
    return SimpleNamespace(**row)


def _assert_signed_url(url: str, org: str) -> None:
    parsed = urlparse(url)
    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == (
        "https://api.example.test/email/webhook/microsoft"
    ), "the path must keep matching PUBLIC_ROUTES"
    q = parse_qs(parsed.query)
    assert q["org"] == [org]
    assert signing.verify_webhook_org(q["org"][0], q["sig"][0]) == org


async def test_no_bound_tenant_makes_no_db_call_and_no_graph_call(monkeypatch) -> None:
    def _no_db(*_a, **_k):
        raise AssertionError("an unbound _ensure_subscription opened a session")

    def _no_graph(*_a, **_k):
        raise AssertionError("an unbound _ensure_subscription reached Graph")

    events: list[str] = []
    monkeypatch.setattr(sync_mod, "_tenant_session", _no_db)
    monkeypatch.setattr(sync_mod, "_instantiate_provider", _no_graph)
    monkeypatch.setattr(sync_mod, "_get_db", _no_db, raising=False)
    monkeypatch.setattr(
        sync_mod._log, "warning", lambda ev, **kw: events.append(ev), raising=False,
    )
    assert current_tenant() is None
    await sync_mod._ensure_subscription("acc-1")
    assert events == ["email.subscription_unbound"]


async def test_a_bound_create_signs_the_notification_url(graph_env, monkeypatch) -> None:
    graph = _Graph()
    monkeypatch.setattr(sync_mod, "_instantiate_provider", lambda *_a: graph)
    _with_row(graph_env, _account())
    token = bind_tenant(ORG)
    try:
        await sync_mod._ensure_subscription("acc-1")
    finally:
        release_tenant(token)
    assert graph_env.opened == [ORG]
    assert len(graph.created) == 1
    _assert_signed_url(graph.created[0][0], ORG)
    assert graph.created[0][1], "a new subscription always gets a clientState"


async def test_a_renewal_moves_the_url_to_the_signed_one(graph_env, monkeypatch) -> None:
    """Risk R-3: a subscription from before EM-T1a has no `org` in its URL."""
    graph = _Graph()
    monkeypatch.setattr(sync_mod, "_instantiate_provider", lambda *_a: graph)
    soon = datetime.now(UTC) + timedelta(hours=1)
    _with_row(graph_env, _account(
        webhook_subscription_id="sub-old", webhook_client_state="cs",
        webhook_expires_at=soon,
    ))
    token = bind_tenant(ORG)
    try:
        await sync_mod._ensure_subscription("acc-1")
    finally:
        release_tenant(token)
    assert graph.created == []
    assert len(graph.renewed) == 1 and graph.renewed[0][0] == "sub-old"
    _assert_signed_url(graph.renewed[0][1], ORG)


async def test_a_refused_renewal_deletes_and_recreates(graph_env, monkeypatch) -> None:
    graph = _Graph(renew_fails=True)
    monkeypatch.setattr(sync_mod, "_instantiate_provider", lambda *_a: graph)
    soon = datetime.now(UTC) + timedelta(hours=1)
    _with_row(graph_env, _account(
        webhook_subscription_id="sub-old", webhook_client_state="cs",
        webhook_expires_at=soon,
    ))
    token = bind_tenant(ORG)
    try:
        await sync_mod._ensure_subscription("acc-1")
    finally:
        release_tenant(token)
    assert graph.deleted == ["sub-old"]
    assert len(graph.created) == 1
    _assert_signed_url(graph.created[0][0], ORG)
    assert graph.created[0][1] == "cs", "the stored clientState is kept"


def test_ensure_subscription_holds_no_unbound_session() -> None:
    import inspect

    src = inspect.getsource(sync_mod)
    assert "_get_db" not in src
    assert ".commit()" not in inspect.getsource(sync_mod._ensure_subscription)
