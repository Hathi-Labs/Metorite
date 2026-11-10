"""WS-17 EM-T17 PR-A1 (A4, R8): the draft store runs in its own block.

Spec: ``project-docs/specs/email_app_master_plan.md`` §17.3.7 and §17.8 A4.

``_compose_assist_run`` closes its session before the model call, and a short
new ``_tenant_session()`` block runs ``_store_ai_draft``. This suite runs the
whole phased path on the real catalog, as the non-privileged role
``acb_app_h3rls``, with organization B bound:

* block 1 reads the target, a header-only row of B, and loads the provider;
* the fetch and the embed run with no session;
* block 2a writes the body and the rotated creds, in B;
* block 2b reads the memories, the few-shot by the vector and the calendar;
* the new block stores the draft, in B, and organization A reads none of it.

Bound to A, the same call stops at the owner check and writes nothing.
The provider, the embedding and the model are fakes.

Run (real Postgres)::

    bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_email_draft_store_rls.py -v -rs
"""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

pytest.importorskip("sqlalchemy")

from fastapi import HTTPException
from gateway.routes.email.automation import drafting as drafting_mod
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_email_automation_tenancy import (
    _BY_ACCOUNT,
    _bound,
    _FakeProvider,
    _isolated,
    _patch_providers,
    _rows,
    _scalar,
)
from tests.unit.test_email_scheduler_tenancy import _assert_non_priv, _seed_account

# ``promoted`` and ``app_engine`` are fixtures, used by name.
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

_OWNER = "b@em-t17-pra1.test"
_BODY = "The full body from the provider. Can we meet on Tuesday?"


class _BodyProvider(_FakeProvider):
    """The EM-T1b-2 fake, plus the body-only read of EM-T17 PR-A."""

    async def get_message_body(self, pmid):
        self.calls.append(f"body:{pmid}")
        return SimpleNamespace(body_text=_BODY, body_html=None)


def _seed_header_only(admin, *, org: str, account_id: str, thread_id: str) -> str:
    """A header-only row, as an Outlook sync writes it: no body, a snippet."""
    with admin.begin() as c:
        return str(c.execute(text(
            "INSERT INTO email_messages (account_id, provider_message_id, "
            "thread_id, folder, from_address, to_addresses, subject, "
            "body_text, snippet, received_at, organization_id) "
            "VALUES (CAST(:a AS uuid), :pm, :tid, 'inbox', CAST(:frm AS jsonb), "
            "CAST(:to AS jsonb), 'Quote', '', 'short snippet', :rcv, "
            "CAST(:o AS uuid)) RETURNING id"),
            {"a": account_id, "pm": f"pm-{uuid.uuid4().hex[:12]}",
             "tid": thread_id,
             "frm": json.dumps({"email": "ravi@ext-pra1.test", "name": "Ravi"}),
             "to": json.dumps([{"email": _OWNER}]),
             "rcv": datetime.now(UTC), "o": org}).scalar_one())


def _app_dsn(p) -> str:
    return p.app_url.render_as_string(hide_password=False)


class TestTheDraftStoreBlock:

    async def test_the_phased_draft_writes_in_b_and_a_reads_none(
        self, promoted, app_engine, monkeypatch,  # noqa: F811
    ):
        _assert_non_priv(app_engine)
        p = promoted
        acc = _seed_account(p.admin_engine, org=p.org_b, owner=_OWNER)
        tid = f"t-pra1-{acc}"
        mid = _seed_header_only(p.admin_engine, org=p.org_b, account_id=acc,
                                thread_id=tid)
        provider = _BodyProvider()
        _patch_providers(monkeypatch, provider)
        from email_ingestion import email_embeddings
        monkeypatch.setattr(email_embeddings, "embed_query",
                            _no_vector)
        seen: dict = {}

        async def fake_agent_draft(email, about, signature, user_email, **_kw):
            seen["body"] = email["body"]
            return "Hello Ravi, Tuesday works."

        monkeypatch.setattr(drafting_mod, "_agent_draft_reply", fake_agent_draft)
        req = drafting_mod.ComposeAssistRequest(
            account_id=acc, mode="reply", message_id=mid)
        member = SimpleNamespace(email=_OWNER)
        async with tenant_engine_scope(_app_dsn(p)):
            with _bound(p.org_b):
                got = await drafting_mod._compose_assist_run(req, member)

        assert got == {"draft": "Hello Ravi, Tuesday works."}, got
        assert seen["body"] == _BODY, "the fetched body did not reach the draft"
        assert f"body:{provider_pmid(p, mid)}" in provider.calls
        stored = _rows(p.admin_engine,
                       "SELECT thread_id, draft_text, organization_id::text AS org "
                       f"FROM email_ai_drafts {_BY_ACCOUNT}", {"a": acc})
        assert stored == [{"thread_id": tid, "draft_text": got["draft"],
                           "org": p.org_b}], stored
        _isolated(p, "email_ai_drafts", acc, expect_b=1)
        # Block 2a wrote the body and the rotated creds in B.
        assert _scalar(p.admin_engine,
                       "SELECT body_text FROM email_messages "
                       "WHERE id = CAST(:m AS uuid)", {"m": mid}) == _BODY
        assert _scalar(p.admin_engine,
                       "SELECT credentials_encrypted FROM email_accounts "
                       "WHERE id = CAST(:a AS uuid)", {"a": acc}) == (
            'enc:{"access_token": "rotated"}')

    async def test_bound_to_a_the_draft_stops_at_the_owner_check(
        self, promoted, app_engine, monkeypatch,  # noqa: F811
    ):
        _assert_non_priv(app_engine)
        p = promoted
        acc = _seed_account(p.admin_engine, org=p.org_b, owner=_OWNER)
        mid = _seed_header_only(p.admin_engine, org=p.org_b, account_id=acc,
                                thread_id=f"t-pra1-a-{acc}")
        provider = _BodyProvider()
        _patch_providers(monkeypatch, provider)
        req = drafting_mod.ComposeAssistRequest(
            account_id=acc, mode="reply", message_id=mid)
        async with tenant_engine_scope(_app_dsn(p)):
            with _bound(p.org_a), pytest.raises(HTTPException) as refused:
                await drafting_mod._compose_assist_run(
                    req, SimpleNamespace(email=_OWNER))
        assert refused.value.status_code == 404
        assert provider.calls == []
        assert _scalar(p.admin_engine,
                       f"SELECT count(*) FROM email_ai_drafts {_BY_ACCOUNT}",
                       {"a": acc}) == 0


async def _no_vector(_text: str):
    return None


def provider_pmid(p, mid: str) -> str:
    return _scalar(p.admin_engine,
                   "SELECT provider_message_id FROM email_messages "
                   "WHERE id = CAST(:m AS uuid)", {"m": mid})
