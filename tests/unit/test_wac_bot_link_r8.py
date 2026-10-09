"""WS-47 WAC-2 — link redemption on the bot number, on a REAL database (R8).

Spec: ``project-docs/specs/whatsapp_assistant_channel.md`` §5.3, §5.4 and §9.

This suite runs the REAL ``POST /whatsapp/webhook`` route, the REAL link-code
route and the REAL SECURITY DEFINER functions of the WAC-2 migration against
the phase-4 catalog of ``test_h3_rls_promotion_rehearsal``, as its
NOSUPERUSER NOBYPASSRLS role. ``tenant_engine_scope`` points the gateway
engine at that role, so each read and write passes FORCE row level security
or fails. Only the Cloud API provider is a fake: it records each send.

R7 fences named here:

* ``wac-lookup-migration-idempotent``: a second run of the migration changes
  no function, grant or setting.
* ``wac-lookup-acb-app-only``: PUBLIC and the three Supabase roles may not
  run either function, and ``acb_app`` may.
* ``wac-lookup-sees-through-rls``: the app role reads no row of the table
  unbound, and the functions still answer for it.
* ``wac-code-never-another-phone``: the code function never returns an
  active row of another phone, a revoked row or an expired code.
* ``wac-link-redeems``: a link message writes an active row (``wa_id``,
  ``linked_at``, ``is_current`` true) and sends the success reply.
* ``wac-code-single-use``: a wrong, used or expired code links nothing.
* ``wac-one-person-per-phone`` and ``wac-second-org-not-current``.
* ``wac-issue-limit``: the 11th code in an hour answers 429 and writes nothing.

Run (real Postgres)::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_wac_bot_link_r8.py -v -rs
"""
from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

import httpx
from acb_auth import UserContext, UserRole, build_access, get_current_user
from acb_common import get_settings
from acb_common.db import bind_tenant, clear_tenant
from fastapi import FastAPI
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
_SECRET = "wac2-r8-app-secret"
_BOT = "1098765432"
_TOKEN = "wac2-r8-bot-token"
_TABLE = "whatsapp_member_links"
_PHONE_FN = "public.whatsapp_member_links_for_phone(text)"
_CODE_FN = "public.whatsapp_member_link_for_code(text, text)"
_WS20_FN = "public.wa_account_for_phone_number_id(text)"
_SUPABASE_ROLES = ("anon", "authenticated", "service_role")

_FAILED = ("That link code did not work. Open My Profile in Metorite and get "
           "a new link.")
_UNKNOWN = ("Hi, this is Metorite. To chat with your workspace, open My "
            "Profile in Metorite and select Chat on WhatsApp.")
_OTHER = ("This phone is already linked to another Metorite account. Unlink "
          "it there first.")


def _migration() -> Path:
    """The WAC-2 migration, found by CONTENT and never by number (R1)."""
    hits = [
        p for p in sorted((_ROOT / "infra" / "postgres").glob("*.sql"))
        if "FUNCTION public.whatsapp_member_link_for_code"
        in p.read_text(encoding="utf-8")
    ]
    assert len(hits) == 1, f"expected one WAC-2 migration, found {hits}"
    return hits[0]


@pytest.fixture(scope="module")
def granted(promoted):  # noqa: F811
    """The functions go to ``acb_app`` only. The rehearsal role has a
    suite-private name, so it gets the same grant here, as the superuser."""
    with promoted.admin_engine.begin() as c:
        for fn in (_PHONE_FN, _CODE_FN, _WS20_FN):
            c.execute(text(f"GRANT EXECUTE ON FUNCTION {fn} TO {_APP_ROLE}"))
    return promoted


@pytest.fixture()
def sent(granted, monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """The channel open for both orgs, and a provider that records sends."""
    s = get_settings()
    for name, value in (
        ("whatsapp_assistant_enabled", True),
        ("whatsapp_assistant_orgs", f"{granted.org_a},{granted.org_b}"),
        ("whatsapp_assistant_display_number", "919800000000"),
        ("whatsapp_assistant_phone_number_id", _BOT),
        ("whatsapp_assistant_access_token", _TOKEN),
        ("acb_env", "prod"),
    ):
        monkeypatch.setattr(s, name, value, raising=False)
    monkeypatch.setenv("WHATSAPP_APP_SECRET", _SECRET)

    import acb_audit
    from gateway.routes.whatsapp_channel import inbound
    from whatsapp_ingestion.providers import factory

    monkeypatch.setattr(acb_audit, "record", lambda _event: None)
    monkeypatch.setattr(inbound, "_FAILED", {})
    out: list[tuple[str, str]] = []

    class _Provider:
        async def send_text(self, to: str, body: str) -> str:
            out.append((to, body))
            return "wamid.out"

    monkeypatch.setattr(factory, "build_provider", lambda _n, _c: _Provider())
    return out


def _member() -> str:
    return f"m-{uuid.uuid4().hex[:10]}@wac2.test"


def _phone() -> str:
    return "91" + str(uuid.uuid4().int)[:10]


def _code() -> str:
    from gateway.routes.whatsapp_channel.link import new_code

    return new_code()


def _hash(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def _change(pnid: str, sender: str, body: str) -> dict[str, Any]:
    return {"field": "messages", "value": {
        "metadata": {"display_phone_number": "919800000000",
                     "phone_number_id": pnid},
        "contacts": [{"profile": {"name": "Alice"}, "wa_id": sender}],
        "messages": [{"from": sender, "id": f"wamid.{uuid.uuid4().hex[:12]}",
                      "timestamp": "1790000000", "type": "text",
                      "text": {"body": body}}],
    }}


async def _webhook(p, *changes: dict[str, Any]) -> httpx.Response:
    """The REAL route, signed as Meta signs it, on the app role's engine."""
    from gateway.routes.whatsapp.transport.webhook import receive_webhook

    raw = json.dumps({"object": "whatsapp_business_account", "entry": [
        {"id": "WABA", "changes": list(changes)}]}).encode("utf-8")
    sig = "sha256=" + hmac.new(_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    app = FastAPI()
    app.post("/whatsapp/webhook")(receive_webhook)
    async with tenant_engine_scope(p.app_url.render_as_string(hide_password=False)):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://gateway.test") as client:
            return await client.post(
                "/whatsapp/webhook", content=raw,
                headers={"Content-Type": "application/json",
                         "X-Hub-Signature-256": sig})


async def _link_message(p, sender: str, code: str) -> httpx.Response:
    return await _webhook(p, _change(_BOT, sender, f"Link me: {code}"))


async def _issue(p, *, org: str, email: str) -> httpx.Response:
    """The REAL code route, with the tenant bound as ``acb_auth.deps`` binds it."""
    from gateway.routes.whatsapp_channel.link import router

    async def _user() -> UserContext:
        bind_tenant(org)
        return UserContext(email=email, role=UserRole.EMPLOYEE,
                           organization_id=org,
                           access=build_access(["feature:chat"]))

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = _user
    try:
        async with tenant_engine_scope(p.app_url.render_as_string(hide_password=False)):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport,
                                         base_url="http://gateway.test") as client:
                return await client.post("/me/whatsapp-link/code")
    finally:
        clear_tenant()


def _seed(admin_engine, **cols) -> str:
    names = ", ".join(cols)
    marks = ", ".join(f":{k}" for k in cols)
    with admin_engine.begin() as c:
        return str(c.execute(text(
            f"INSERT INTO {_TABLE} ({names}) VALUES ({marks}) RETURNING id"),
            cols).scalar_one())


def _pending(admin_engine, *, org: str, email: str, code: str,
             expires: str = "now() + interval '15 minutes'") -> str:
    with admin_engine.begin() as c:
        return str(c.execute(text(
            f"INSERT INTO {_TABLE} (organization_id, member_email, status, "
            f"code_hash, code_expires_at) VALUES (CAST(:o AS uuid), :e, "
            f"'pending', :h, {expires}) RETURNING id"),
            {"o": org, "e": email, "h": _hash(code)}).scalar_one())


def _row(admin_engine, link_id: str):
    with admin_engine.connect() as c:
        return c.execute(text(f"SELECT * FROM {_TABLE} WHERE id = :i"),
                         {"i": link_id}).one()


def _rows_for_phone(admin_engine, phone: str) -> list:
    with admin_engine.connect() as c:
        return c.execute(text(f"SELECT * FROM {_TABLE} WHERE wa_id = :w"),
                         {"w": phone}).all()


def _assert_non_priv(app_eng) -> None:
    with app_eng.connect() as c:
        role = c.execute(text(
            "SELECT rolsuper, rolbypassrls FROM pg_roles "
            "WHERE rolname = current_user")).first()
    assert role is not None and not role[0] and not role[1], (
        "this suite connects as a SUPERUSER/BYPASSRLS role, so RLS is bypassed"
    )


# ── wac-lookup-migration-idempotent ─────────────────────────────────────────

def _functions(admin_engine) -> list:
    with admin_engine.connect() as c:
        return c.execute(text(
            "SELECT p.proname, pg_get_functiondef(p.oid), p.prosecdef, "
            "p.proconfig, p.proacl::text FROM pg_proc p "
            "WHERE p.proname IN ('whatsapp_member_links_for_phone', "
            "'whatsapp_member_link_for_code') ORDER BY p.proname")).all()


def _replay(admin_engine) -> None:
    with admin_engine.connect() as c:
        with c.connection.dbapi_connection.cursor() as cur:
            cur.execute(_migration().read_text(encoding="utf-8"))
        c.connection.dbapi_connection.commit()


def test_a_second_run_of_the_migration_changes_nothing(granted) -> None:
    before = _functions(granted.admin_engine)
    assert len(before) == 2
    _replay(granted.admin_engine)
    assert _functions(granted.admin_engine) == before


# ── wac-lookup-acb-app-only ─────────────────────────────────────────────────

def test_only_acb_app_may_run_the_two_functions(granted) -> None:
    """The scratch server has no Supabase roles and maybe no ``acb_app``.
    This creates them, grants the Supabase roles EXECUTE as a default
    privilege does, and replays the migration, which must revoke them."""
    with granted.admin_engine.begin() as c:
        for r in (*_SUPABASE_ROLES, "acb_app"):
            # A role is cluster-wide, so two R8 runs on one scratch server
            # can race here. The loser catches the error and goes on.
            c.execute(text(
                f"DO $$ BEGIN CREATE ROLE {r} NOLOGIN; "
                f"EXCEPTION WHEN duplicate_object OR unique_violation "
                f"THEN NULL; END $$;"))
        for r in _SUPABASE_ROLES:
            for fn in (_PHONE_FN, _CODE_FN):
                c.execute(text(f"GRANT EXECUTE ON FUNCTION {fn} TO {r}"))
    _replay(granted.admin_engine)

    with granted.admin_engine.connect() as c:
        for fn in (_PHONE_FN, _CODE_FN):
            def held(role: str, fn: str = fn) -> bool:
                return c.execute(text(
                    f"SELECT has_function_privilege('{role}', '{fn}', 'EXECUTE')"
                )).scalar_one()

            assert held("public") is False, f"PUBLIC may run {fn}"
            for r in _SUPABASE_ROLES:
                assert held(r) is False, f"{r} may run {fn}"
            assert held("acb_app") is True, f"acb_app may not run {fn}"
            definer, path = c.execute(text(
                "SELECT p.prosecdef, p.proconfig FROM pg_proc p "
                "WHERE p.oid = CAST(:f AS regprocedure)"), {"f": fn}).one()
            assert definer is True
            assert path == ["search_path=pg_catalog, pg_temp"]


# ── wac-lookup-sees-through-rls ─────────────────────────────────────────────

def test_the_functions_answer_under_force_rls_for_the_app_role(
    granted, app_engine,  # noqa: F811
) -> None:
    _assert_non_priv(app_engine)
    phone, email, code = _phone(), _member(), _code()
    _seed(granted.admin_engine, organization_id=granted.org_b,
          member_email=email, wa_id=phone, status="active", is_current=True)
    pending = _pending(granted.admin_engine, org=granted.org_a, email=email,
                       code=code)

    with app_engine.connect() as c:
        unbound = c.execute(text(
            f"SELECT count(*) FROM {_TABLE} WHERE member_email = :e"),
            {"e": email}).scalar_one()
        links = c.execute(text(
            "SELECT organization_id::text AS o, member_email, is_current "
            "FROM public.whatsapp_member_links_for_phone(:w)"), {"w": phone}).all()
        rows = c.execute(text(
            "SELECT id::text AS id, organization_id::text AS o, status "
            "FROM public.whatsapp_member_link_for_code(:h, :w)"),
            {"h": _hash(code), "w": _phone()}).all()

    assert unbound == 0, "an unbound read sees the table, so the proof is vacuous"
    assert [(r.o, r.member_email, r.is_current) for r in links] == [
        (granted.org_b, email, True)]
    assert [(r.id, r.o, r.status) for r in rows] == [
        (pending, granted.org_a, "pending")]


# ── wac-code-never-another-phone ────────────────────────────────────────────

def test_the_code_function_never_returns_another_phones_row(
    granted, app_engine,  # noqa: F811
) -> None:
    mine, theirs = _phone(), _phone()
    active = _code()
    _seed(granted.admin_engine, organization_id=granted.org_a,
          member_email=_member(), wa_id=theirs, status="active",
          code_hash=_hash(active), code_expires_at="2099-01-01T00:00:00Z")
    revoked = _code()
    _seed(granted.admin_engine, organization_id=granted.org_a,
          member_email=_member(), status="revoked", code_hash=_hash(revoked),
          code_expires_at="2099-01-01T00:00:00Z")
    expired = _code()
    _pending(granted.admin_engine, org=granted.org_a, email=_member(),
             code=expired, expires="now() - interval '1 minute'")

    def rows(code: str, phone: str) -> list:
        with app_engine.connect() as c:
            return c.execute(text(
                "SELECT status FROM public.whatsapp_member_link_for_code(:h, :w)"),
                {"h": _hash(code), "w": phone}).all()

    assert rows(active, mine) == [], "another phone's active row came back"
    assert [r.status for r in rows(active, theirs)] == ["active"]
    assert rows(revoked, mine) == []
    assert rows(expired, mine) == []


# ── wac-link-redeems ────────────────────────────────────────────────────────

async def test_a_link_message_writes_an_active_current_row_and_replies(
    granted, app_engine, sent,  # noqa: F811
) -> None:
    _assert_non_priv(app_engine)
    p, email, phone = granted, _member(), _phone()
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO app_user (email, display_name, role, status, "
            "organization_id) VALUES (:e, 'Alice Rao', 'employee', 'active', "
            "CAST(:o AS uuid))"), {"e": email, "o": p.org_a})
    issued = await _issue(p, org=p.org_a, email=email)
    assert issued.status_code == 201, issued.text
    code = issued.json()["code"]

    # Lower case, with spaces, as a person may type it.
    res = await _link_message(p, phone, f" {code[:5].lower()} {code[5:].lower()}")

    assert res.status_code == 200, res.text
    (row,) = _rows_for_phone(p.admin_engine, phone)
    assert row.status == "active" and str(row.organization_id) == p.org_a
    assert row.member_email == email and row.linked_at is not None
    assert row.is_current is True
    assert sent == [(phone, "Linked to h3rls-a as Alice Rao. Ask me about your "
                            "tasks, projects or calendar.")]


async def test_a_redelivered_link_message_replies_again_and_changes_nothing(
    granted, sent,
) -> None:
    p, email, phone, code = granted, _member(), _phone(), _code()
    link_id = _pending(p.admin_engine, org=p.org_a, email=email, code=code)
    await _link_message(p, phone, code)
    first = _row(p.admin_engine, link_id)

    await _link_message(p, phone, code)

    assert _row(p.admin_engine, link_id) == first
    assert len(sent) == 2 and sent[0] == sent[1]
    assert sent[1][1].startswith("Linked to h3rls-a as ")


# ── wac-code-single-use ─────────────────────────────────────────────────────

async def test_a_wrong_code_links_nothing(granted, sent) -> None:
    p, phone = granted, _phone()
    _pending(p.admin_engine, org=p.org_a, email=_member(), code=_code())
    await _link_message(p, phone, _code())
    assert _rows_for_phone(p.admin_engine, phone) == []
    assert sent == [(phone, _FAILED)]


async def test_a_used_code_links_nothing_for_a_second_phone(granted, sent) -> None:
    p, email, code = granted, _member(), _code()
    first, second = _phone(), _phone()
    link_id = _pending(p.admin_engine, org=p.org_a, email=email, code=code)
    await _link_message(p, first, code)

    await _link_message(p, second, code)

    assert _row(p.admin_engine, link_id).wa_id == first
    assert _rows_for_phone(p.admin_engine, second) == []
    assert sent[-1] == (second, _FAILED)


async def test_an_expired_code_links_nothing(granted, sent) -> None:
    p, phone, code = granted, _phone(), _code()
    link_id = _pending(p.admin_engine, org=p.org_a, email=_member(), code=code,
                       expires="now() - interval '1 second'")
    await _link_message(p, phone, code)
    row = _row(p.admin_engine, link_id)
    assert (row.status, row.wa_id) == ("pending", None)
    assert sent == [(phone, _FAILED)]


async def test_an_org_that_is_not_on_the_list_links_nothing(
    granted, sent, monkeypatch,
) -> None:
    p, phone, code = granted, _phone(), _code()
    monkeypatch.setattr(get_settings(), "whatsapp_assistant_orgs", p.org_b,
                        raising=False)
    link_id = _pending(p.admin_engine, org=p.org_a, email=_member(), code=code)
    await _link_message(p, phone, code)
    assert _row(p.admin_engine, link_id).status == "pending"
    assert sent == [(phone, _FAILED)]


# ── wac-one-person-per-phone, wac-second-org-not-current ───────────────────

async def test_a_second_person_on_a_linked_phone_links_nothing(
    granted, sent,
) -> None:
    p, phone = granted, _phone()
    _seed(p.admin_engine, organization_id=p.org_a, member_email=_member(),
          wa_id=phone, status="active", is_current=True)
    code = _code()
    other = _pending(p.admin_engine, org=p.org_b, email=_member(), code=code)

    await _link_message(p, phone, code)

    assert _row(p.admin_engine, other).status == "pending"
    assert sent == [(phone, _OTHER)]


async def test_the_same_person_in_a_second_org_links_without_current(
    granted, sent,
) -> None:
    p, email, phone = granted, _member(), _phone()
    _seed(p.admin_engine, organization_id=p.org_a, member_email=email.upper(),
          wa_id=phone, status="active", is_current=True)
    code = _code()
    second = _pending(p.admin_engine, org=p.org_b, email=email, code=code)

    await _link_message(p, phone, code)

    row = _row(p.admin_engine, second)
    assert (row.status, row.wa_id, row.is_current) == ("active", phone, False)
    assert sent[0][1].startswith("Linked to h3rls-b as ")


async def test_a_new_code_for_the_same_phone_in_the_same_org_relinks(
    granted, sent,
) -> None:
    """The partial unique index allows one active link per phone and org.
    A fresh code for the same phone takes over, and keeps it current."""
    p, email, phone = granted, _member(), _phone()
    old = _seed(p.admin_engine, organization_id=p.org_a, member_email=email,
                wa_id=phone, status="active", is_current=True)
    code = _code()
    new = _pending(p.admin_engine, org=p.org_a, email=email, code=code)

    res = await _link_message(p, phone, code)

    assert res.status_code == 200, res.text
    assert _row(p.admin_engine, old).status == "revoked"
    row = _row(p.admin_engine, new)
    assert (row.status, row.is_current) == ("active", True)


# ── The unknown sender ──────────────────────────────────────────────────────

async def test_an_unknown_phone_gets_the_fixed_reply_and_nothing_is_written(
    granted, sent,
) -> None:
    p, phone = granted, _phone()
    with p.admin_engine.connect() as c:
        before = c.execute(text(f"SELECT count(*) FROM {_TABLE}")).scalar_one()
    res = await _webhook(p, _change(_BOT, phone, "What is due today?"))
    assert res.status_code == 200
    assert sent == [(phone, _UNKNOWN)]
    with p.admin_engine.connect() as c:
        assert c.execute(text(f"SELECT count(*) FROM {_TABLE}")).scalar_one() == before


async def test_a_linked_phone_without_a_code_gets_no_reply(granted, sent) -> None:
    p, phone = granted, _phone()
    _seed(p.admin_engine, organization_id=p.org_a, member_email=_member(),
          wa_id=phone, status="active", is_current=True)
    res = await _webhook(p, _change(_BOT, phone, "What is due today?"))
    assert res.status_code == 200 and sent == []


# ── wac-ws20-unchanged: one batch with both numbers ────────────────────────

async def test_a_mixed_batch_lands_the_inbox_message_and_redeems_the_code(
    granted, sent,
) -> None:
    p = granted
    ws20 = str(uuid.uuid4().int)[:12]
    with p.admin_engine.begin() as c:
        account = str(c.execute(text(
            "INSERT INTO wa_accounts (user_id, phone_number, phone_number_id, "
            "credentials_encrypted, provider, sync_status, organization_id) "
            "VALUES ('carol@wac2-b.test', '+910000000000', :p, 'enc', "
            "'cloud_api', 'live', CAST(:o AS uuid)) RETURNING id"),
            {"p": ws20, "o": p.org_b}).scalar_one())
    phone, code = _phone(), _code()
    link_id = _pending(p.admin_engine, org=p.org_a, email=_member(), code=code)

    res = await _webhook(p, _change(ws20, "919990000001", "payment due"),
                         _change(_BOT, phone, f"Link me: {code}"))

    assert res.status_code == 200, res.text
    with p.admin_engine.connect() as c:
        msgs = c.execute(text(
            "SELECT organization_id::text AS o, body_text FROM wa_messages "
            "WHERE account_id = CAST(:a AS uuid)"), {"a": account}).all()
        leaked = c.execute(text(
            "SELECT count(*) FROM wa_messages WHERE body_text LIKE 'Link me:%'"
        )).scalar_one()
    assert [(m.o, m.body_text) for m in msgs] == [(p.org_b, "payment due")]
    assert leaked == 0, "the bot's message reached the WS-20 inbox"
    assert _row(p.admin_engine, link_id).wa_id == phone


# ── wac-issue-limit ─────────────────────────────────────────────────────────

async def test_the_eleventh_code_in_an_hour_answers_429_and_writes_nothing(
    granted, sent,
) -> None:
    p, email = granted, _member()
    for _ in range(10):
        _seed(p.admin_engine, organization_id=p.org_a, member_email=email,
              status="revoked", code_hash=_hash(_code()),
              code_expires_at="2099-01-01T00:00:00Z")
    # A code in the other org does not count against this one.
    other = await _issue(p, org=p.org_b, email=email)
    assert other.status_code == 201, other.text

    res = await _issue(p, org=p.org_a, email=email)

    assert res.status_code == 429, res.text
    with p.admin_engine.connect() as c:
        n = c.execute(text(
            f"SELECT count(*) FROM {_TABLE} WHERE member_email = :e "
            "AND organization_id = CAST(:o AS uuid)"),
            {"e": email, "o": p.org_a}).scalar_one()
    assert n == 10


async def test_a_code_older_than_an_hour_does_not_count(granted, sent) -> None:
    p, email = granted, _member()
    with p.admin_engine.begin() as c:
        for _ in range(10):
            c.execute(text(
                f"INSERT INTO {_TABLE} (organization_id, member_email, status, "
                "code_hash, code_expires_at, created_at) VALUES "
                "(CAST(:o AS uuid), :e, 'revoked', :h, now(), "
                "now() - interval '61 minutes')"),
                {"o": p.org_a, "e": email, "h": _hash(_code())})
    res = await _issue(p, org=p.org_a, email=email)
    assert res.status_code == 201, res.text


def test_the_count_of_the_unbound_app_role_is_zero(granted) -> None:
    """The mechanism under the limit: the route counts INSIDE its bound
    session. Unbound, the app role counts nothing, so a count that ran
    unbound would never limit."""
    eng = create_engine(granted.app_url, future=True)
    try:
        with eng.connect() as c:
            assert c.execute(text(f"SELECT count(*) FROM {_TABLE}")).scalar_one() == 0
    finally:
        eng.dispose()
