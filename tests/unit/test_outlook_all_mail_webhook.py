"""WS-17 task 1966: the Outlook push watches ALL mail while the org flag is on.

Mail that an Outlook rule files into a user folder never touches the Inbox
resource. With ``OUTLOOK_ALL_MAIL_WEBHOOK_ORGS`` naming the org, a new
subscription uses ``/me/messages``, and a renewed Inbox subscription is
replaced. A fake provider is the evidence: the local stack cannot reach Graph.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from acb_common import get_settings
from gateway.db import bind_tenant, release_tenant
from gateway.routes.email.transport import sync as sync_mod

ORG = "11111111-2222-3333-4444-555555555555"
INBOX = "me/mailFolders('inbox')/messages"
ALL_MAIL = "/me/messages"
EXP = "2026-10-04T00:00:00Z"


class _Sessions:
    def __init__(self) -> None:
        self.db = AsyncMock()
        self.row = None

    @asynccontextmanager
    async def __call__(self, org=None):
        result = MagicMock()
        result.fetchone.return_value = self.row
        self.db.execute.return_value = result
        yield self.db


class _Graph:
    """The Graph answer of a PATCH carries the ``resource`` of the subscription."""

    def __init__(self, renewed_resource: str | None = INBOX,
                 create_fails: bool = False) -> None:
        self.create_fails = create_fails
        self.created: list[dict] = []
        self.renewed: list[str] = []
        self.deleted: list[str] = []
        self.renewed_resource = renewed_resource

    async def authenticate(self) -> bool:
        return True

    def credentials_dirty(self) -> bool:
        return False

    async def create_subscription(self, url, client_state, resource=None):
        if self.create_fails:
            raise RuntimeError("Graph refused the create")
        self.created.append({"cs": client_state, "resource": resource})
        return {"id": "sub-new", "expirationDateTime": EXP, "resource": resource}

    async def renew_subscription(self, sub_id, notification_url=None):
        self.renewed.append(sub_id)
        return {"id": sub_id, "expirationDateTime": EXP,
                "resource": self.renewed_resource}

    async def delete_subscription(self, sub_id):
        self.deleted.append(sub_id)


@pytest.fixture()
def env(monkeypatch):
    from acb_llm import key_store

    s = _Sessions()
    monkeypatch.setattr(sync_mod, "_tenant_session", s)
    monkeypatch.setattr(get_settings(), "gateway_session_secret", "wh-secret", raising=False)
    monkeypatch.setenv("GATEWAY_PUBLIC_URL", "https://api.example.test")
    store = MagicMock()
    store.decrypt.return_value = "{}"
    monkeypatch.setattr(key_store, "get_key_store", lambda: store)
    return s


def _flag(monkeypatch, value: str) -> None:
    monkeypatch.setattr(get_settings(), "outlook_all_mail_webhook_orgs", value, raising=False)


def _account(**over):
    row = {"provider": "microsoft", "credentials_encrypted": "x",
           "webhook_subscription_id": None, "webhook_client_state": None,
           "webhook_expires_at": None}
    row.update(over)
    return SimpleNamespace(**row)


async def _run(env, monkeypatch, graph, **row) -> tuple:
    monkeypatch.setattr(sync_mod, "_instantiate_provider", lambda *_a: graph)
    env.row = _account(**row)
    token = bind_tenant(ORG)
    try:
        await sync_mod._ensure_subscription("acc-1")
    finally:
        release_tenant(token)
    # The UPDATE is the last execute: (sql, params).
    return env.db.execute.call_args.args[1]


_EXPIRING = {"webhook_subscription_id": "sub-old", "webhook_client_state": "cs-old",
             "webhook_expires_at": datetime.now(UTC) + timedelta(hours=1)}


async def test_a_new_subscription_watches_all_mail_while_the_flag_is_on(env, monkeypatch):
    _flag(monkeypatch, ORG)
    graph = _Graph()
    await _run(env, monkeypatch, graph)
    assert [c["resource"] for c in graph.created] == [ALL_MAIL]


async def test_a_new_subscription_watches_the_inbox_while_the_flag_is_off(env, monkeypatch):
    _flag(monkeypatch, "")
    graph = _Graph()
    await _run(env, monkeypatch, graph)
    assert [c["resource"] for c in graph.created] == [None], "no resource: the Inbox default"


async def test_a_flag_for_another_org_leaves_the_inbox(env, monkeypatch):
    _flag(monkeypatch, "99999999-8888-7777-6666-555555555555")
    graph = _Graph()
    await _run(env, monkeypatch, graph)
    assert [c["resource"] for c in graph.created] == [None]


async def test_a_renewed_inbox_subscription_is_replaced_by_an_all_mail_one(env, monkeypatch):
    _flag(monkeypatch, "*")
    graph = _Graph(renewed_resource=INBOX)
    params = await _run(env, monkeypatch, graph, **_EXPIRING)
    assert graph.renewed == ["sub-old"]
    assert graph.deleted == ["sub-old"]
    assert graph.created == [{"cs": "cs-old", "resource": ALL_MAIL}]
    assert params["sid"] == "sub-new" and params["cs"] == "cs-old"
    assert params["exp"] == datetime.fromisoformat("2026-10-04T00:00:00+00:00")


async def test_a_renewed_all_mail_subscription_only_renews(env, monkeypatch):
    _flag(monkeypatch, ORG)
    graph = _Graph(renewed_resource="/Me/Messages/")  # the form is normalised
    params = await _run(env, monkeypatch, graph, **_EXPIRING)
    assert graph.deleted == [] and graph.created == []
    assert params["sid"] == "sub-old"


async def test_an_all_mail_subscription_is_renewed_not_switched_while_the_flag_is_off(
    env, monkeypatch,
):
    _flag(monkeypatch, "")
    graph = _Graph(renewed_resource=ALL_MAIL)
    params = await _run(env, monkeypatch, graph, **_EXPIRING)
    assert graph.deleted == [] and graph.created == []
    assert params["sid"] == "sub-old"


async def test_an_inbox_subscription_is_renewed_while_the_flag_is_off(env, monkeypatch):
    _flag(monkeypatch, "")
    graph = _Graph(renewed_resource=INBOX)
    await _run(env, monkeypatch, graph, **_EXPIRING)
    assert graph.deleted == [] and graph.created == []


async def test_a_failed_switch_create_keeps_the_healthy_old_subscription(env, monkeypatch):
    _flag(monkeypatch, ORG)
    graph = _Graph(renewed_resource=INBOX, create_fails=True)
    params = await _run(env, monkeypatch, graph, **_EXPIRING)
    assert graph.deleted == [], "the old subscription is healthy: do not delete it"
    assert params["sid"] == "sub-old" and params["cs"] == "cs-old"
    assert params["exp"] == datetime.fromisoformat("2026-10-04T00:00:00+00:00")
