"""WS-20 WA-C1 — the Meta webhook binds its tenant under FORCE RLS (R8).

Spec: ``project-docs/specs/whatsapp_message_manager.md`` §12.3 F1, F2, F7,
F8 and F12, §12.4 slice WA-C1.

**The defect.** Meta calls ``POST /whatsapp/webhook`` with no member session,
so no tenant is bound. The route read ``wa_accounts`` on an unbound session.
Production runs every ``wa_*`` table with FORCE RLS, so the read saw no row.
The route logged ``whatsapp.webhook.unknown_number`` and answered 200, and
every inbound batch was dropped. All 27 ``test_whatsapp_*.py`` files are
hermetic, so a fake agreed with the unbound read and F1 shipped green.

This suite runs the REAL route, the REAL persist path and the REAL post-sync
hooks against the phase-4 catalog of ``test_h3_rls_promotion_rehearsal``, as
its NOSUPERUSER NOBYPASSRLS role ``acb_app_h3rls``. ``tenant_engine_scope``
points the gateway engine at that role.

R7 fences named here:

* ``wa-webhook-binds-its-tenant``: a signed batch for a connected number
  writes its message, chat status and intent under the org of the account. A
  session of another org reads none of it.
* ``wa-webhook-unknown-number-writes-nothing``: a batch for a number that no
  account holds answers 200 and writes no row.
* ``wa-webhook-needs-a-secret-outside-dev``: with no app secret and
  ``ACB_ENV`` not ``dev``, the POST answers 403 and writes no row.
* ``wa-number-connects-once``: a second connect of a connected Cloud API
  number, from another org, answers 409 and leaves one row.
* the migration: a duplicate number refuses the pre-check, and only the app
  role may run the resolver function.
* the mechanism proof: an UNBOUND read of ``wa_accounts`` as the same role
  sees nothing, so the fences above are not vacuous.

Run (real Postgres)::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_whatsapp_webhook_under_rls.py -v -rs
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import uuid
from pathlib import Path

import pytest

pytest.importorskip("sqlalchemy")

import httpx
import psycopg
from acb_common import get_settings
from fastapi import FastAPI, HTTPException
from sqlalchemy import create_engine, text

from tests.unit._tenant_ladder import tenant_engine_scope

# ``promoted`` and ``app_engine`` are fixtures used by name, so the import is
# load-bearing even though ruff reads it as unused.
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _APP_ROLE,
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

_ROOT = Path(__file__).resolve().parents[2]
_SECRET = "wa-c1-r8-app-secret"
_FN = "public.wa_account_for_phone_number_id(text)"
_SUPABASE_ROLES = ("anon", "authenticated", "service_role")


def _migration() -> Path:
    """The WA-C1 migration, found by CONTENT and never by number (R1)."""
    hits = [
        p for p in sorted((_ROOT / "infra" / "postgres").glob("*.sql"))
        if "FUNCTION public.wa_account_for_phone_number_id"
        in p.read_text(encoding="utf-8")
    ]
    assert len(hits) == 1, f"expected one WA-C1 migration, found {hits}"
    return hits[0]


@pytest.fixture(scope="module")
def granted(promoted):  # noqa: F811
    """The resolver goes to ``acb_app`` only. The rehearsal role has a
    suite-private name, so it gets the same grant here, as the superuser."""
    with promoted.admin_engine.begin() as c:
        c.execute(text(f"GRANT EXECUTE ON FUNCTION {_FN} TO {_APP_ROLE}"))
    return promoted


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WHATSAPP_APP_SECRET", _SECRET)
    monkeypatch.setattr(get_settings(), "acb_env", "prod", raising=False)


@pytest.fixture()
def real_hooks(monkeypatch: pytest.MonkeyPatch) -> None:
    """The two production hooks on the webhook path, and no other."""
    from gateway.routes.whatsapp.automation.intent import process_new_messages
    from gateway.routes.whatsapp.automation.replyzero import classify_chats
    from whatsapp_ingestion.post_sync import hooks

    monkeypatch.setattr(hooks, "on_new_messages", process_new_messages)
    monkeypatch.setattr(hooks, "classify_chats", classify_chats)


def _seed_account(admin_engine, *, org: str, pnid: str, user: str) -> str:
    """A connected Cloud API number in ``org``, written as the superuser."""
    with admin_engine.begin() as c:
        return str(c.execute(text(
            "INSERT INTO wa_accounts (user_id, phone_number, phone_number_id, "
            "credentials_encrypted, provider, sync_status, organization_id) "
            "VALUES (:u, '+910000000000', :p, 'enc', 'cloud_api', 'live', "
            "CAST(:o AS uuid)) RETURNING id"),
            {"u": user, "p": pnid, "o": org}).scalar_one())


def _payload(pnid: str, wamid: str, body: str) -> bytes:
    """One inbound text message, in the envelope Meta sends."""
    return json.dumps({
        "object": "whatsapp_business_account",
        "entry": [{"id": "WABA-WA-C1", "changes": [{"field": "messages", "value": {
            "metadata": {"display_phone_number": "910000000000",
                         "phone_number_id": pnid},
            "contacts": [{"profile": {"name": "Rajesh"}, "wa_id": "919990000001"}],
            "messages": [{"from": "919990000001", "id": wamid,
                          "timestamp": "1790000000", "type": "text",
                          "text": {"body": body}}],
        }}]}],
    }).encode("utf-8")


def _change(pnid: str, wamid: str, body: str = "hello") -> dict:
    """One ``messages`` change for one number, as Meta sends it."""
    return {"field": "messages", "value": {
        "metadata": {"display_phone_number": "910000000000",
                     "phone_number_id": pnid},
        "contacts": [{"profile": {"name": "Rajesh"}, "wa_id": "919990000001"}],
        "messages": [{"from": "919990000001", "id": wamid,
                      "timestamp": "1790000000", "type": "text",
                      "text": {"body": body}}],
    }}


def _batch(*entries: list[dict]) -> bytes:
    """One POST that carries several entries. Every customer WABA subscribes
    the one Tech Provider app, so Meta can batch several numbers together."""
    return json.dumps({
        "object": "whatsapp_business_account",
        "entry": [{"id": f"WABA-{i}", "changes": changes}
                  for i, changes in enumerate(entries)],
    }).encode("utf-8")


def _message_row(admin_engine, wamid: str):
    return _admin_one(admin_engine,
                      "SELECT organization_id::text AS o, account_id::text AS a "
                      "FROM wa_messages WHERE wa_message_id = :w", w=wamid)


def _sign(raw: bytes, secret: str = _SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()


async def _post(app_url, raw: bytes, signature: str | None) -> httpx.Response:
    """The REAL route, as Meta reaches it, on the app role's engine."""
    from gateway.routes.whatsapp.transport.webhook import receive_webhook

    app = FastAPI()
    app.post("/whatsapp/webhook")(receive_webhook)
    headers = {"Content-Type": "application/json"}
    if signature is not None:
        headers["X-Hub-Signature-256"] = signature
    async with tenant_engine_scope(app_url.render_as_string(hide_password=False)):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://gateway.test") as client:
            return await client.post("/whatsapp/webhook", content=raw,
                                     headers=headers)


def _admin_one(admin_engine, sql: str, **params):
    with admin_engine.connect() as c:
        return c.execute(text(sql), params).first()


def _count_as(app_url, org: str | None, table: str, account_id: str) -> int:
    """How many rows of ``table`` for the account the app role sees, bound to
    ``org``, or unbound when ``org`` is None."""
    eng = create_engine(app_url, future=True)
    try:
        with eng.connect() as c, c.begin():
            if org is not None:
                c.execute(text("SELECT set_config('app.tenant_id', :o, true)"),
                          {"o": org})
            return c.execute(text(
                f"SELECT count(*) FROM {table} WHERE account_id = CAST(:a AS uuid)"),
                {"a": account_id}).scalar_one()
    finally:
        eng.dispose()


def _assert_non_priv(app_eng) -> None:
    with app_eng.connect() as c:
        role = c.execute(text(
            "SELECT rolsuper, rolbypassrls FROM pg_roles "
            "WHERE rolname = current_user")).first()
    assert role is not None and not role[0] and not role[1], (
        "this suite connects as a SUPERUSER/BYPASSRLS role, so RLS is bypassed"
    )


# ── The mechanism: an unbound read sees nothing ─────────────────────────────

def test_an_unbound_read_of_wa_accounts_sees_no_row(granted, app_engine):  # noqa: F811
    """The production shape of F1. If this fails, the fences below prove
    nothing, because the role sees every row."""
    _assert_non_priv(app_engine)
    pnid = f"pn-{uuid.uuid4().hex[:10]}"
    _seed_account(granted.admin_engine, org=granted.org_a, pnid=pnid,
                  user="alice@wa-c1-a.test")
    with app_engine.connect() as c:
        seen = c.execute(text(
            "SELECT count(*) FROM wa_accounts WHERE phone_number_id = :p"),
            {"p": pnid}).scalar_one()
    assert seen == 0


def test_the_resolver_sees_through_rls_for_the_app_role(granted, app_engine):  # noqa: F811
    pnid = f"pn-{uuid.uuid4().hex[:10]}"
    acct = _seed_account(granted.admin_engine, org=granted.org_b, pnid=pnid,
                         user="carol@wa-c1-b.test")
    with app_engine.connect() as c:
        rows = c.execute(text(
            "SELECT account_id::text AS a, organization_id::text AS o "
            "FROM public.wa_account_for_phone_number_id(:p)"),
            {"p": pnid}).all()
    assert [(r.a, r.o) for r in rows] == [(acct, granted.org_b)]


# ── wa-webhook-binds-its-tenant ─────────────────────────────────────────────

async def test_a_signed_batch_lands_under_its_org_and_org_b_sees_none(
    granted, app_engine, real_hooks,  # noqa: F811
):
    _assert_non_priv(app_engine)
    p = granted
    pnid = f"pn-{uuid.uuid4().hex[:10]}"
    wamid = f"wamid.{uuid.uuid4().hex[:12]}"
    acct = _seed_account(p.admin_engine, org=p.org_a, pnid=pnid,
                         user="alice@wa-c1-a.test")
    raw = _payload(pnid, wamid, "payment pending for invoice 4417")

    resp = await _post(p.app_url, raw, _sign(raw))

    assert resp.status_code == 200, resp.text
    msg = _admin_one(p.admin_engine,
                     "SELECT organization_id::text AS o, intent, direction "
                     "FROM wa_messages WHERE wa_message_id = :w", w=wamid)
    assert msg is not None, (
        "the batch was dropped. The route read wa_accounts unbound and took "
        "the number for unknown (F1)"
    )
    assert (msg.o, msg.direction) == (p.org_a, "in")
    assert msg.intent == "payment", "the on_new_messages hook did not run bound"
    status = _admin_one(p.admin_engine,
                        "SELECT organization_id::text AS o, status FROM "
                        "wa_chat_status WHERE account_id = CAST(:a AS uuid)",
                        a=acct)
    assert status is not None, "the classify_chats hook did not run bound"
    assert (status.o, status.status) == (p.org_a, "NEEDS_REPLY")

    for table in ("wa_messages", "wa_chats", "wa_chat_status"):
        assert _count_as(p.app_url, p.org_a, table, acct) == 1, table
        assert _count_as(p.app_url, p.org_b, table, acct) == 0, (
            f"a session of org B reads {table} rows of org A"
        )


async def test_a_redelivered_batch_writes_no_second_row(
    granted, app_engine, real_hooks,  # noqa: F811
):
    """Meta retries. The persist path is idempotent on ``wa_message_id``,
    and it stays so under RLS."""
    p = granted
    pnid = f"pn-{uuid.uuid4().hex[:10]}"
    wamid = f"wamid.{uuid.uuid4().hex[:12]}"
    acct = _seed_account(p.admin_engine, org=p.org_b, pnid=pnid,
                         user="carol@wa-c1-b.test")
    raw = _payload(pnid, wamid, "hello")
    for _ in range(2):
        resp = await _post(p.app_url, raw, _sign(raw))
        assert resp.status_code == 200, resp.text
    assert _count_as(p.app_url, p.org_b, "wa_messages", acct) == 1
    assert _count_as(p.app_url, p.org_a, "wa_messages", acct) == 0


# ── wa-webhook-one-tenant-per-number: a batch of several numbers ───────────

async def test_a_batch_of_two_orgs_lands_each_message_under_its_own_org(
    granted, app_engine, real_hooks,  # noqa: F811
):
    """Org A's number first, org B's second, in one POST. The first number
    must not decide the tenant of the whole batch (review P0)."""
    p = granted
    pa, pb = f"pn-{uuid.uuid4().hex[:10]}", f"pn-{uuid.uuid4().hex[:10]}"
    acct_a = _seed_account(p.admin_engine, org=p.org_a, pnid=pa,
                           user="alice@wa-c1-a.test")
    acct_b = _seed_account(p.admin_engine, org=p.org_b, pnid=pb,
                           user="carol@wa-c1-b.test")
    wa, wb = f"wamid.{uuid.uuid4().hex[:12]}", f"wamid.{uuid.uuid4().hex[:12]}"
    raw = _batch([_change(pa, wa, "payment due")], [_change(pb, wb, "payment due")])

    resp = await _post(p.app_url, raw, _sign(raw))

    assert resp.status_code == 200, resp.text
    row_a, row_b = _message_row(p.admin_engine, wa), _message_row(p.admin_engine, wb)
    assert row_a is not None and (row_a.o, row_a.a) == (p.org_a, acct_a)
    assert row_b is not None and (row_b.o, row_b.a) == (p.org_b, acct_b), (
        f"org B's message landed as {row_b}, not under org B's account"
    )
    for table in ("wa_messages", "wa_chats", "wa_chat_status"):
        assert _count_as(p.app_url, p.org_a, table, acct_a) == 1, table
        assert _count_as(p.app_url, p.org_b, table, acct_b) == 1, table
        assert _count_as(p.app_url, p.org_a, table, acct_b) == 0, table
        assert _count_as(p.app_url, p.org_b, table, acct_a) == 0, table


async def test_an_unknown_number_in_a_batch_does_not_block_a_known_one(
    granted, app_engine, real_hooks,  # noqa: F811
):
    p = granted
    known = f"pn-{uuid.uuid4().hex[:10]}"
    acct = _seed_account(p.admin_engine, org=p.org_b, pnid=known,
                         user="carol@wa-c1-b.test")
    w_unknown = f"wamid.{uuid.uuid4().hex[:12]}"
    w_known = f"wamid.{uuid.uuid4().hex[:12]}"
    raw = _batch([_change(f"pn-unknown-{uuid.uuid4().hex[:8]}", w_unknown)],
                 [_change(known, w_known)])

    resp = await _post(p.app_url, raw, _sign(raw))

    assert resp.status_code == 200, resp.text
    assert _message_row(p.admin_engine, w_unknown) is None
    row = _message_row(p.admin_engine, w_known)
    assert row is not None and (row.o, row.a) == (p.org_b, acct)


async def test_two_changes_for_one_number_in_one_batch_both_land(
    granted, app_engine, real_hooks,  # noqa: F811
):
    """One entry with two changes, and a second entry, all for one number."""
    p = granted
    pnid = f"pn-{uuid.uuid4().hex[:10]}"
    acct = _seed_account(p.admin_engine, org=p.org_a, pnid=pnid,
                         user="alice@wa-c1-a.test")
    w1, w2, w3 = (f"wamid.{uuid.uuid4().hex[:12]}" for _ in range(3))
    raw = _batch([_change(pnid, w1), _change(pnid, w2)], [_change(pnid, w3)])

    resp = await _post(p.app_url, raw, _sign(raw))

    assert resp.status_code == 200, resp.text
    for w in (w1, w2, w3):
        row = _message_row(p.admin_engine, w)
        assert row is not None and (row.o, row.a) == (p.org_a, acct), w
    assert _count_as(p.app_url, p.org_a, "wa_messages", acct) == 3


# ── wa-webhook-unknown-number-writes-nothing ────────────────────────────────

async def test_an_unknown_number_answers_200_and_writes_nothing(
    granted, app_engine, real_hooks,  # noqa: F811
):
    p = granted
    wamid = f"wamid.{uuid.uuid4().hex[:12]}"
    raw = _payload(f"pn-unknown-{uuid.uuid4().hex[:8]}", wamid, "payment due")

    resp = await _post(p.app_url, raw, _sign(raw))

    assert resp.status_code == 200, resp.text
    assert _admin_one(p.admin_engine,
                      "SELECT 1 FROM wa_messages WHERE wa_message_id = :w",
                      w=wamid) is None


async def test_a_bad_signature_answers_403_and_writes_nothing(
    granted, app_engine, real_hooks,  # noqa: F811
):
    p = granted
    pnid = f"pn-{uuid.uuid4().hex[:10]}"
    wamid = f"wamid.{uuid.uuid4().hex[:12]}"
    _seed_account(p.admin_engine, org=p.org_a, pnid=pnid, user="alice@wa-c1-a.test")
    raw = _payload(pnid, wamid, "hello")

    resp = await _post(p.app_url, raw, _sign(raw, secret="not-the-secret"))

    assert resp.status_code == 403
    assert _admin_one(p.admin_engine,
                      "SELECT 1 FROM wa_messages WHERE wa_message_id = :w",
                      w=wamid) is None


# ── wa-webhook-needs-a-secret-outside-dev (F8) ──────────────────────────────

async def test_no_app_secret_outside_dev_answers_403_and_writes_nothing(
    granted, app_engine, real_hooks, monkeypatch,  # noqa: F811
):
    p = granted
    pnid = f"pn-{uuid.uuid4().hex[:10]}"
    wamid = f"wamid.{uuid.uuid4().hex[:12]}"
    _seed_account(p.admin_engine, org=p.org_a, pnid=pnid, user="alice@wa-c1-a.test")
    monkeypatch.delenv("WHATSAPP_APP_SECRET", raising=False)
    raw = _payload(pnid, wamid, "hello")

    resp = await _post(p.app_url, raw, None)

    assert resp.status_code == 403, (
        "an unsigned POST was accepted with no app secret outside dev (F8)"
    )
    assert _admin_one(p.admin_engine,
                      "SELECT 1 FROM wa_messages WHERE wa_message_id = :w",
                      w=wamid) is None


async def test_no_app_secret_in_dev_still_ingests(
    granted, app_engine, real_hooks, monkeypatch,  # noqa: F811
):
    """Local development has no Meta app. The F8 refusal stays out of dev."""
    p = granted
    pnid = f"pn-{uuid.uuid4().hex[:10]}"
    wamid = f"wamid.{uuid.uuid4().hex[:12]}"
    _seed_account(p.admin_engine, org=p.org_a, pnid=pnid, user="alice@wa-c1-a.test")
    monkeypatch.delenv("WHATSAPP_APP_SECRET", raising=False)
    monkeypatch.setattr(get_settings(), "acb_env", "dev", raising=False)
    raw = _payload(pnid, wamid, "hello")

    resp = await _post(p.app_url, raw, None)

    assert resp.status_code == 200, resp.text
    assert _admin_one(p.admin_engine,
                      "SELECT 1 FROM wa_messages WHERE wa_message_id = :w",
                      w=wamid) is not None


# ── wa-number-connects-once (F7) ────────────────────────────────────────────

class _Store:
    def encrypt(self, raw: str) -> str:
        return f"enc:{len(raw)}"


async def test_a_second_connect_from_another_org_answers_409(
    granted, app_engine, monkeypatch,  # noqa: F811
):
    """Org A holds the number. A member of org B cannot see that row, so the
    read-first check finds nothing. The INSERT then meets the platform-wide
    index, and the member gets 409, never a 500."""
    from acb_common.db import tenant_session
    from acb_llm import key_store
    from gateway.routes.whatsapp.transport.accounts import persist_account

    p = granted
    pnid = f"pn-{uuid.uuid4().hex[:10]}"
    _seed_account(p.admin_engine, org=p.org_a, pnid=pnid, user="alice@wa-c1-a.test")
    monkeypatch.setattr(key_store, "get_key_store", lambda: _Store())

    async with tenant_engine_scope(p.app_url.render_as_string(hide_password=False)):
        with pytest.raises(HTTPException) as err:
            async with tenant_session(p.org_b) as db:
                await persist_account(
                    db, user_id="carol@wa-c1-b.test", phone_number="+910000000000",
                    phone_number_id=pnid, waba_id=None, display_name="B",
                    credentials={"access_token": "t"}, webhook_verify_token=None,
                    # Assume Meta confirmed B's token. The index still refuses.
                    verified_profile={"id": pnid},
                )
    assert err.value.status_code == 409
    assert err.value.detail == "Number already connected"
    n = _admin_one(p.admin_engine,
                   "SELECT count(*) AS n FROM wa_accounts WHERE phone_number_id = :p",
                   p=pnid).n
    assert n == 1


async def test_an_embedded_connect_row_reads_back_live_under_its_org(
    granted, app_engine, monkeypatch,  # noqa: F811
):
    """WS-20 WA-C2 P3. The Embedded Signup path inserts ``sync_status='live'``.
    The row reads back ``live`` for its own org under FORCE RLS, and another
    org sees no row."""
    from acb_common.db import tenant_session
    from acb_llm import key_store
    from gateway.routes.whatsapp.transport.accounts import persist_account

    p = granted
    pnid = str(uuid.uuid4().int)[:15]
    monkeypatch.setattr(key_store, "get_key_store", lambda: _Store())

    async with (
        tenant_engine_scope(p.app_url.render_as_string(hide_password=False)),
        tenant_session(p.org_a) as db,
    ):
        row = await persist_account(
            db, user_id="alice@wa-c2-a.test", phone_number="+910000000000",
            phone_number_id=pnid, waba_id="102290129340398",
            display_name="A", webhook_verify_token=None,
            credentials={"access_token": "t", "onboarding": "coexistence"},
            verified_profile={"id": pnid}, sync_status="live",
        )
    assert row.sync_status == "live"

    def _status_as(org: str):
        eng = create_engine(p.app_url, future=True)
        try:
            with eng.connect() as c, c.begin():
                c.execute(text("SELECT set_config('app.tenant_id', :o, true)"),
                          {"o": org})
                return c.execute(text(
                    "SELECT sync_status FROM wa_accounts "
                    "WHERE phone_number_id = :p"), {"p": pnid}).scalars().all()
        finally:
            eng.dispose()

    assert _status_as(p.org_a) == ["live"]
    assert _status_as(p.org_b) == []


# ── The migration ───────────────────────────────────────────────────────────

def test_the_precheck_refuses_a_duplicate_cloud_number(granted):
    """The index cannot hold a duplicate, so the test drops it inside a
    transaction that it rolls back. Then the pre-check must name the count."""
    sql = _migration().read_text(encoding="utf-8")
    block = re.search(r"DO \$precheck\$.*?\$precheck\$;", sql, re.S)
    assert block, "the pre-check DO block is gone from the migration"
    pnid = f"pn-dup-{uuid.uuid4().hex[:8]}"
    with granted.admin_engine.connect() as c:
        tx = c.begin()
        try:
            c.execute(text("DROP INDEX uq_wa_accounts_cloud_phone_number_id"))
            for user in ("a@dup.test", "b@dup.test"):
                c.execute(text(
                    "INSERT INTO wa_accounts (user_id, phone_number, "
                    "phone_number_id, credentials_encrypted, organization_id) "
                    "VALUES (:u, '+91', :p, 'enc', CAST(:o AS uuid))"),
                    {"u": user, "p": pnid, "o": granted.org_a})
            with pytest.raises(psycopg.Error) as err, \
                    c.connection.dbapi_connection.cursor() as cur:
                cur.execute(block.group(0))
            assert "1 Cloud API phone_number_id value(s)" in str(err.value)
        finally:
            tx.rollback()


def test_only_the_app_role_may_run_the_resolver(granted):
    """PUBLIC may not run it, and nor may the three Supabase roles. The
    scratch server has no such roles, so this creates them and grants them
    EXECUTE, as a Supabase default privilege does. A replay of the migration
    must then revoke all three."""
    with granted.admin_engine.begin() as c:
        for r in _SUPABASE_ROLES:
            # A role is cluster-wide, so two R8 runs on one scratch server
            # can race here. The loser catches the error and goes on.
            c.execute(text(
                f"DO $$ BEGIN CREATE ROLE {r} NOLOGIN; "
                f"EXCEPTION WHEN duplicate_object OR unique_violation "
                f"THEN NULL; END $$;"))
            c.execute(text(f"GRANT EXECUTE ON FUNCTION {_FN} TO {r}"))
    with granted.admin_engine.connect() as c:
        with c.connection.dbapi_connection.cursor() as cur:
            cur.execute(_migration().read_text(encoding="utf-8"))
        c.connection.dbapi_connection.commit()
    with granted.admin_engine.connect() as c:
        public = c.execute(text(
            f"SELECT has_function_privilege('public', '{_FN}', 'EXECUTE')"
        )).scalar_one()
        for r in _SUPABASE_ROLES:
            held = c.execute(text(
                f"SELECT has_function_privilege('{r}', '{_FN}', 'EXECUTE')"
            )).scalar_one()
            assert held is False, f"{r} may run the cross-tenant resolver"
        definer, path = c.execute(text(
            "SELECT p.prosecdef, p.proconfig FROM pg_proc p "
            "WHERE p.oid = CAST(:f AS regprocedure)"), {"f": _FN}).one()
    assert public is False, "PUBLIC may run the cross-tenant resolver"
    assert definer is True
    assert path == ["search_path=pg_catalog, pg_temp"]
