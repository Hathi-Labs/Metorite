"""WS-47 WAC-3 — the run of a linked text, on a REAL database (R8).

Spec: ``project-docs/specs/whatsapp_assistant_channel.md`` §5.4, §5.5, §5.6
and §7 (WAC-3: A2, A3, A4, A5, A8 and A10).

This suite runs the REAL ``POST /whatsapp/webhook`` route, the REAL
``bot_run`` SQL, the REAL chat writers of ``routes/chat.py`` and the REAL
``resolve_identity`` and ``resolve_access`` against the phase-4 catalog of
``test_h3_rls_promotion_rehearsal``, as its NOSUPERUSER NOBYPASSRLS role.
``tenant_engine_scope`` points the async engine at that role, and the
``acb_graph`` factory is pointed at it too, so each read and write passes FORCE
row level security or fails. Only the executor and the Cloud API provider are
fakes. The identity read runs with ``IDENTITY_CUTOVER`` on, because under
FORCE RLS only the RLS-exempt shadow tables can answer an unbound question.

R7 fences named here:

* ``wac-bot-table-migration`` (A10): the migration alone installs FORCE RLS
  with USING and WITH CHECK, ``chat_session.channel`` is nullable with no
  default, and a second run changes nothing.
* ``wac-bot-thread`` (A5): one text writes a ``whatsapp`` thread with the
  member's turn and the reply in the link's org, ``GET /chat/sessions`` lists
  it for that member, and the other org sees none of it.
* ``wac-one-wamid-one-run`` (A2): one payload twice at once inserts one row.
* ``wac-identity-refusal`` (A3) and ``wac-removed-member-no-run`` (A4).
* ``wac-sweep`` (A8): a stale row runs once, a row older than 24 hours ends
  ``expired`` with no send, and the tries stop at 3.

Run (real Postgres)::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_wac_bot_run_r8.py -v -rs
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import uuid
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

import acb_graph.db as graph_db
import httpx
from acb_common import get_settings
from fastapi import FastAPI
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

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
_SECRET = "wac3-r8-app-secret"
_BOT = "1098765432"
_TOKEN = "wac3-r8-bot-token"
_TABLE = "whatsapp_bot_messages"
_FUNCTIONS = ("public.whatsapp_member_links_for_phone(text)",
              "public.chat_session_exists(text)")
_ANSWER = "Two tasks are due today."
_NO_WORKSPACE = ("Metorite cannot open this workspace from WhatsApp yet. "
                 "Use the web app.")
_FAILED = "Metorite could not answer just now. Try again in a few minutes."


def _migration() -> Path:
    """The WAC-3 migration, found by CONTENT and never by number (R1)."""
    hits = [
        p for p in sorted((_ROOT / "infra" / "postgres").glob("*.sql"))
        if "CREATE TABLE IF NOT EXISTS whatsapp_bot_messages"
        in p.read_text(encoding="utf-8")
    ]
    assert len(hits) == 1, f"expected one WAC-3 migration, found {hits}"
    return hits[0]


@pytest.fixture(scope="module")
def granted(promoted):  # noqa: F811
    """The functions go to ``acb_app`` only. The rehearsal role has a
    suite-private name, so it gets the same grants here, as the superuser."""
    with promoted.admin_engine.begin() as c:
        for fn in _FUNCTIONS:
            c.execute(text(f"GRANT EXECUTE ON FUNCTION {fn} TO {_APP_ROLE}"))
    return promoted


def _email(tag: str = "m") -> str:
    return f"{tag}-{uuid.uuid4().hex[:10]}@wac3.test"


def _phone() -> str:
    return "91" + str(uuid.uuid4().int)[:10]


def _seed_member(admin_engine, *, org: str, email: str,
                 status: str = "active", shadow: bool = True,
                 permissions: tuple[str, ...] = (
                     "feature:chat", "agents:run:orchestrator")) -> None:
    """An ``app_user`` with a role that holds *permissions*, and its active
    membership in the RLS-exempt shadow. As the admin, which escapes RLS."""
    uid, role_id = str(uuid.uuid4()), str(uuid.uuid4())
    with admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO org_role (id, organization_id, slug, display_name, "
            "is_system, rank) VALUES (:rid, :org, :slug, :slug, false, 100)"),
            {"rid": role_id, "org": org, "slug": f"wac3-{role_id[:8]}"})
        for perm in permissions:
            c.execute(text(
                "INSERT INTO org_role_permission (role_id, permission, "
                "organization_id) VALUES (:rid, :p, :org)"),
                {"rid": role_id, "p": perm, "org": org})
        c.execute(text(
            "INSERT INTO app_user (id, email, display_name, role, status, "
            "organization_id) VALUES (:uid, :e, :e, 'employee', :s, :org)"),
            {"uid": uid, "e": email, "s": status, "org": org})
        c.execute(text(
            "INSERT INTO user_role (user_id, role_id, assigned_by, "
            "organization_id) VALUES (:uid, :rid, 'wac3-test', :org)"),
            {"uid": uid, "rid": role_id, "org": org})
        if shadow:
            ident = str(c.execute(text(
                "INSERT INTO user_identity (email, display_name) "
                "VALUES (:e, :e) RETURNING id"), {"e": email}).scalar_one())
            c.execute(text(
                "INSERT INTO org_membership (organization_id, user_id, status) "
                "VALUES (CAST(:o AS uuid), CAST(:u AS uuid), 'active')"),
                {"o": org, "u": ident})


def _seed_link(admin_engine, *, org: str, email: str, phone: str) -> None:
    with admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO whatsapp_member_links (organization_id, member_email, "
            "wa_id, status, is_current, linked_at) VALUES (CAST(:o AS uuid), "
            ":e, :w, 'active', true, now())"),
            {"o": org, "e": email, "w": phone})


class _Agent:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, agent: str, payload: dict[str, Any], **kw: Any):
        self.calls.append({"agent": agent, "payload": dict(payload), **kw})
        return {"result": _ANSWER}


@pytest.fixture()
def bot(granted, monkeypatch: pytest.MonkeyPatch):
    """The channel open for both orgs, a fake executor and provider, and the
    ``acb_graph`` factory as the app role."""
    s = get_settings()
    for name, value in (
        ("whatsapp_assistant_enabled", True),
        ("whatsapp_assistant_orgs", f"{granted.org_a},{granted.org_b}"),
        ("whatsapp_assistant_phone_number_id", _BOT),
        ("whatsapp_assistant_access_token", _TOKEN),
        ("acb_env", "prod"),
    ):
        monkeypatch.setattr(s, name, value, raising=False)
    monkeypatch.setenv("WHATSAPP_APP_SECRET", _SECRET)
    monkeypatch.setenv("IDENTITY_CUTOVER", "1")

    from collections import OrderedDict

    import acb_audit
    import acb_auth.access as access
    import orchestrator.stream_relay as relay
    from gateway.routes.whatsapp_channel import bot_run, inbound
    from whatsapp_ingestion.providers import factory

    access.invalidate()
    # Order-proof (review round 1): an earlier suite in the same process can
    # leave the resolver in its degraded mode, or the legacy fallback on.
    # Every member then reads as "no feature:chat" here. This suite's
    # catalog has the access tables, so it sets both states itself.
    monkeypatch.setattr(access, "_tables_missing", False)
    monkeypatch.delenv("ACCESS_LEGACY_FALLBACK", raising=False)
    monkeypatch.setattr(acb_audit, "record", lambda _event: None)
    monkeypatch.setattr(inbound, "_FAILED", {})
    monkeypatch.setattr(inbound, "_HANDLED", OrderedDict())
    monkeypatch.setattr(bot_run, "_RUNS", set())
    monkeypatch.setattr(bot_run, "_LIVE_ROWS", set())
    monkeypatch.setattr(bot_run, "_RUN_LOCKS", {})
    monkeypatch.setattr(bot_run, "_RUN_LOCK_USERS", {})
    monkeypatch.setattr(bot_run, "_THREAD_LOCKS", {})

    async def _not_active(_thread_id: str) -> bool:
        return False

    monkeypatch.setattr(relay, "is_active", _not_active)
    agent = _Agent()
    monkeypatch.setattr(bot_run, "_executor", lambda: agent)

    sent: list[tuple[str, str]] = []

    class _Provider:
        async def send_text(self, to: str, body: str) -> str:
            sent.append((to, body))
            return f"wamid.out.{uuid.uuid4().hex[:12]}"

    monkeypatch.setattr(factory, "build_provider", lambda _n, _c: _Provider())

    eng = create_engine(granted.app_url, poolclass=NullPool, future=True)
    graph_factory = sessionmaker(bind=eng, expire_on_commit=False, future=True)
    monkeypatch.setattr(graph_db, "_session_factory", lambda: graph_factory)

    class _NS:
        pass

    ns = _NS()
    ns.p, ns.agent, ns.sent, ns.bot_run = granted, agent, sent, bot_run
    try:
        yield ns
    finally:
        access.invalidate()
        eng.dispose()


def _change(sender: str, body: str, wamid: str | None = None) -> dict[str, Any]:
    return {"field": "messages", "value": {
        "metadata": {"display_phone_number": "919800000000",
                     "phone_number_id": _BOT},
        "contacts": [{"profile": {"name": "Alice"}, "wa_id": sender}],
        "messages": [{"from": sender,
                      "id": wamid or f"wamid.{uuid.uuid4().hex[:16]}",
                      "timestamp": "1790000000", "type": "text",
                      "text": {"body": body}}],
    }}


async def _post(client: httpx.AsyncClient, *changes: dict[str, Any]):
    raw = json.dumps({"object": "whatsapp_business_account", "entry": [
        {"id": "WABA", "changes": list(changes)}]}).encode("utf-8")
    sig = "sha256=" + hmac.new(_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    return await client.post(
        "/whatsapp/webhook", content=raw,
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": sig})


async def _webhook(ns, *posts: tuple[dict[str, Any], ...], together: bool = False):
    """POST each change list to the REAL route on the app role, then wait
    for the runs. With *together*, the posts go at once."""
    from gateway.routes.whatsapp.transport.webhook import receive_webhook

    app = FastAPI()
    app.post("/whatsapp/webhook")(receive_webhook)
    async with tenant_engine_scope(ns.p.app_url.render_as_string(hide_password=False)):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://gateway.test") as client:
            if together:
                out = await asyncio.gather(*(_post(client, *c) for c in posts))
            else:
                out = [await _post(client, *c) for c in posts]
            await asyncio.wait_for(ns.bot_run.wait_for_runs(), timeout=20)
    return out


async def _in_scope(ns, coro_fn):
    async with tenant_engine_scope(ns.p.app_url.render_as_string(hide_password=False)):
        result = await coro_fn()
        await asyncio.wait_for(ns.bot_run.wait_for_runs(), timeout=20)
        return result


def _admin_rows(ns, sql: str, **params) -> list:
    with ns.p.admin_engine.connect() as c:
        return c.execute(text(sql), params).all()


def _bot_rows(ns, phone: str) -> list:
    return _admin_rows(
        ns, f"SELECT * FROM {_TABLE} WHERE wa_id = :w ORDER BY received_at, "
        "direction", w=phone)


# ── A10: the migration ──────────────────────────────────────────────────────

def _catalog(admin_engine) -> list:
    with admin_engine.connect() as c:
        return [
            c.execute(text(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                "WHERE oid = 'whatsapp_bot_messages'::regclass")).one(),
            c.execute(text(
                "SELECT policyname, qual, with_check FROM pg_policies "
                "WHERE tablename = 'whatsapp_bot_messages' ORDER BY policyname"
            )).all(),
            c.execute(text(
                "SELECT indexname, indexdef FROM pg_indexes "
                "WHERE tablename = 'whatsapp_bot_messages' ORDER BY indexname"
            )).all(),
            c.execute(text(
                "SELECT is_nullable, column_default FROM "
                "information_schema.columns WHERE table_name = 'chat_session' "
                "AND column_name = 'channel'")).all(),
        ]


def _replay(admin_engine) -> None:
    with admin_engine.connect() as c:
        with c.connection.dbapi_connection.cursor() as cur:
            cur.execute(_migration().read_text(encoding="utf-8"))
        c.connection.dbapi_connection.commit()


def test_the_migration_installs_force_rls_and_a_second_run_changes_nothing(
    granted,
) -> None:
    before = _catalog(granted.admin_engine)
    rls, policies, indexes, channel = before
    assert tuple(rls) == (True, True)
    ((name, qual, check),) = policies
    assert name == "whatsapp_bot_messages_tenant_isolation"
    assert "app.tenant_id" in qual and "app.tenant_id" in check
    assert any("wamid" in d and "UNIQUE" in d for _n, d in indexes)
    assert [tuple(r) for r in channel] == [("YES", None)]
    _replay(granted.admin_engine)
    assert _catalog(granted.admin_engine) == before


def test_the_migration_alone_installs_force_rls_on_a_bare_table(granted) -> None:
    """Drop the policy and the RLS, replay the file: it puts both back. The
    generated phase 4 is not what makes this table safe."""
    with granted.admin_engine.begin() as c:
        c.execute(text(f"DROP POLICY whatsapp_bot_messages_tenant_isolation ON {_TABLE}"))
        c.execute(text(f"ALTER TABLE {_TABLE} NO FORCE ROW LEVEL SECURITY"))
        c.execute(text(f"ALTER TABLE {_TABLE} DISABLE ROW LEVEL SECURITY"))
    _replay(granted.admin_engine)
    rls, policies, _i, _c = _catalog(granted.admin_engine)
    assert tuple(rls) == (True, True) and len(policies) == 1


def test_the_app_role_sees_only_its_bound_org_and_cannot_write_another(
    granted, app_engine,  # noqa: F811
) -> None:
    with granted.admin_engine.begin() as c:
        c.execute(text(
            f"INSERT INTO {_TABLE} (organization_id, member_email, wa_id, wamid) "
            "VALUES (CAST(:o AS uuid), 'x@wac3.test', '919000000001', :m)"),
            {"o": granted.org_a, "m": f"wamid.rls.{uuid.uuid4().hex}"})
    with app_engine.connect() as c:
        role = c.execute(text("SELECT rolsuper, rolbypassrls FROM pg_roles "
                              "WHERE rolname = current_user")).one()
        assert tuple(role) == (False, False)
        assert c.execute(text(f"SELECT count(*) FROM {_TABLE}")).scalar() == 0
    with app_engine.connect() as c, c.begin():
        c.execute(text("SELECT set_config('app.tenant_id', :t, true)"),
                  {"t": granted.org_b})
        assert c.execute(text(f"SELECT count(*) FROM {_TABLE}")).scalar() == 0
        with pytest.raises(Exception, match="row-level security"):
            c.execute(text(
                f"INSERT INTO {_TABLE} (organization_id, member_email, wa_id, "
                "wamid) VALUES (CAST(:o AS uuid), 'x@wac3.test', "
                "'919000000001', :m)"),
                {"o": granted.org_a, "m": f"wamid.rls.{uuid.uuid4().hex}"})


# ── A5: the thread, in the link's org ───────────────────────────────────────


async def test_a_linked_text_writes_a_whatsapp_thread_in_the_links_org(bot) -> None:
    p = bot.p
    email, other, phone = _email(), _email("b"), _phone()
    _seed_member(p.admin_engine, org=p.org_a, email=email)
    _seed_member(p.admin_engine, org=p.org_b, email=other)
    _seed_link(p.admin_engine, org=p.org_a, email=email, phone=phone)

    (res,) = await _webhook(bot, (_change(phone, "What is due today?"),))
    assert res.status_code == 200

    (call,) = bot.agent.calls
    assert call["agent"] == "orchestrator"
    assert call["organization_id"] == p.org_a
    assert call["session_user"] == email
    assert call["payload"]["message"] == "What is due today?"
    assert bot.sent == [(phone, _ANSWER)]

    rows = _bot_rows(bot, phone)
    inbound_row = next(r for r in rows if r.direction == "in")
    assert inbound_row.state == "replied" and inbound_row.tries == 1
    assert str(inbound_row.organization_id) == p.org_a
    assert [r.direction for r in rows].count("out") == 1
    sid = inbound_row.chat_session_id
    assert call["thread_id"] == sid

    sessions = _admin_rows(
        bot, "SELECT organization_id, user_id, channel FROM chat_session "
        "WHERE id = :s", s=sid)
    assert [(str(o), u, ch) for o, u, ch in sessions] == [(p.org_a, email, "whatsapp")]
    messages = _admin_rows(
        bot, "SELECT role, content, author_kind, author_email FROM chat_message "
        "WHERE session_id = :s ORDER BY timestamp_ms", s=sid)
    assert [(m.role, m.content, m.author_kind) for m in messages] == [
        ("user", "What is due today?", "human"),
        ("assistant", _ANSWER, "agent"),
    ]
    assert messages[0].author_email == email

    # GET /chat/sessions: the member sees the thread, the other org does not.
    listed = await asyncio.to_thread(_list_sessions, email, p.org_a)
    assert sid in [s["id"] for s in listed]
    assert sid not in [s["id"] for s in
                       await asyncio.to_thread(_list_sessions, other, p.org_b)]
    assert sid not in [s["id"] for s in
                       await asyncio.to_thread(_list_sessions, email, p.org_b)]


def _list_sessions(email: str, org: str) -> list[dict]:
    """The REAL ``GET /chat/sessions`` handler, as the member of *org*."""
    from acb_auth import UserContext, get_current_user
    from acb_auth.roles import UserRole
    from fastapi.testclient import TestClient
    from gateway.routes.chat import list_sessions

    app = FastAPI()
    app.get("/chat/sessions")(list_sessions)
    app.dependency_overrides[get_current_user] = lambda: UserContext(
        email=email, role=UserRole.EMPLOYEE, organization_id=org)
    res = TestClient(app).get("/chat/sessions")
    assert res.status_code == 200, res.text
    return res.json()


async def test_a_second_text_joins_the_same_thread_with_its_history(bot) -> None:
    p = bot.p
    email, phone = _email(), _phone()
    _seed_member(p.admin_engine, org=p.org_a, email=email)
    _seed_link(p.admin_engine, org=p.org_a, email=email, phone=phone)

    await _webhook(bot, (_change(phone, "First"),))
    await _webhook(bot, (_change(phone, "Second"),))
    first, second = bot.agent.calls
    assert first["thread_id"] == second["thread_id"]
    assert second["payload"]["messages"] == [
        {"role": "user", "content": "First"},
        {"role": "assistant", "content": _ANSWER},
    ]


async def test_a_text_after_24_idle_hours_opens_a_new_thread(bot) -> None:
    p = bot.p
    email, phone = _email(), _phone()
    _seed_member(p.admin_engine, org=p.org_a, email=email)
    _seed_link(p.admin_engine, org=p.org_a, email=email, phone=phone)
    await _webhook(bot, (_change(phone, "Old"),))
    with p.admin_engine.begin() as c:
        c.execute(text("UPDATE chat_session SET updated_at = now() - "
                       "interval '25 hours' WHERE user_id = :e"), {"e": email})
    await _webhook(bot, (_change(phone, "New"),))
    first, second = bot.agent.calls
    assert first["thread_id"] != second["thread_id"]
    assert second["payload"]["messages"] == []


# ── A2: one wamid, one run, on the real unique index ────────────────────────


async def test_one_payload_twice_at_once_inserts_one_row_and_runs_once(bot) -> None:
    p = bot.p
    email, phone = _email(), _phone()
    _seed_member(p.admin_engine, org=p.org_a, email=email)
    _seed_link(p.admin_engine, org=p.org_a, email=email, phone=phone)
    change = _change(phone, "Twice", wamid=f"wamid.same.{uuid.uuid4().hex[:8]}")

    out = await _webhook(bot, (change,), (change,), together=True)
    assert [r.status_code for r in out] == [200, 200]
    rows = [r for r in _bot_rows(bot, phone) if r.direction == "in"]
    assert len(rows) == 1 and rows[0].state == "replied"
    assert len(bot.agent.calls) == 1
    turns = _admin_rows(bot, "SELECT count(*) FROM chat_message WHERE "
                        "session_id = :s AND role = 'user'",
                        s=rows[0].chat_session_id)
    assert turns[0][0] == 1

    await _webhook(bot, (change,))   # and once more, later
    assert len(bot.agent.calls) == 1


# ── A3 and A4: the refusals, on the real reads ──────────────────────────────


async def test_an_email_with_no_identity_gets_the_refusal_and_no_run(bot) -> None:
    p = bot.p
    email, phone = _email(), _phone()
    _seed_member(p.admin_engine, org=p.org_a, email=email, shadow=False)
    _seed_link(p.admin_engine, org=p.org_a, email=email, phone=phone)
    await _webhook(bot, (_change(phone, "Hello"),))
    assert bot.agent.calls == []
    assert bot.sent == [(phone, _NO_WORKSPACE)]
    (row,) = [r for r in _bot_rows(bot, phone) if r.direction == "in"]
    assert row.state == "refused" and row.error_code == "identity"


async def test_a_suspended_member_gets_no_run_no_reply_and_no_thread(bot) -> None:
    p = bot.p
    email, phone = _email(), _phone()
    _seed_member(p.admin_engine, org=p.org_a, email=email, status="suspended")
    _seed_link(p.admin_engine, org=p.org_a, email=email, phone=phone)
    await _webhook(bot, (_change(phone, "Hello"),))
    assert bot.agent.calls == [] and bot.sent == []
    (row,) = _bot_rows(bot, phone)
    assert row.state == "refused" and row.error_code == "inactive"
    assert row.chat_session_id is None
    assert _admin_rows(bot, "SELECT id FROM chat_session WHERE user_id = :e",
                       e=email) == []


async def test_a_member_without_chat_gets_no_run(bot) -> None:
    p = bot.p
    email, phone = _email(), _phone()
    _seed_member(p.admin_engine, org=p.org_a, email=email,
                 permissions=("feature:projects",))
    _seed_link(p.admin_engine, org=p.org_a, email=email, phone=phone)
    await _webhook(bot, (_change(phone, "Hello"),))
    assert bot.agent.calls == [] and bot.sent == []
    (row,) = [r for r in _bot_rows(bot, phone) if r.direction == "in"]
    assert row.state == "refused" and row.error_code == "feature"


# ── A8: the sweep, on the real SQL ──────────────────────────────────────────


async def _recorded(bot, *, email: str, phone: str, body: str):
    """A recorded message whose run never started (a crash after the 200)."""
    from gateway.routes.whatsapp_channel import bot_run, inbound

    async def _go():
        links = await inbound._active_links_for_phone(phone)
        return await bot_run.record_inbound(
            links, phone, f"wamid.sweep.{uuid.uuid4().hex[:10]}", body)

    req = await _in_scope(bot, _go)
    assert req is not None
    return req


def _age(bot, message_id: str, *, updated: str, received: str | None = None,
         tries: int | None = None) -> None:
    sets = [f"updated_at = now() - interval '{updated}'"]
    if received:
        sets.append(f"received_at = now() - interval '{received}'")
    if tries is not None:
        sets.append(f"tries = {int(tries)}")
    with bot.p.admin_engine.begin() as c:
        c.execute(text(f"UPDATE {_TABLE} SET {', '.join(sets)} "
                       "WHERE id = CAST(:i AS uuid)"), {"i": message_id})


def _state(bot, message_id: str):
    return _admin_rows(bot, f"SELECT state, tries, error_code FROM {_TABLE} "
                       "WHERE id = CAST(:i AS uuid)", i=message_id)[0]


async def test_the_sweep_runs_a_stale_row_once_from_the_thread(bot) -> None:
    p = bot.p
    email, phone = _email(), _phone()
    _seed_member(p.admin_engine, org=p.org_a, email=email)
    _seed_link(p.admin_engine, org=p.org_a, email=email, phone=phone)
    stale = await _recorded(bot, email=email, phone=phone, body="Lost after the 200")
    fresh = await _recorded(bot, email=email, phone=phone, body="Just now")
    _age(bot, stale.message_id, updated="10 minutes", received="10 minutes")

    assert await _in_scope(bot, bot.bot_run.sweep_once) == 1
    assert await _in_scope(bot, bot.bot_run.sweep_once) == 0

    first, second = bot.agent.calls
    assert first["payload"]["message"] == "Lost after the 200"
    assert first["organization_id"] == p.org_a
    assert tuple(_state(bot, stale.message_id)) == ("replied", 1, None)
    # The rest of the thread runs right after it, in order, with no second
    # sweep, and with the first answer in its history.
    assert second["payload"]["message"] == "Just now"
    assert second["payload"]["messages"][-1] == {
        "role": "assistant", "content": _ANSWER}
    assert tuple(_state(bot, fresh.message_id)) == ("replied", 1, None)
    assert bot.sent == [(phone, _ANSWER), (phone, _ANSWER)]


async def test_a_row_older_than_24_hours_expires_with_no_send(bot) -> None:
    p = bot.p
    email, phone = _email(), _phone()
    _seed_member(p.admin_engine, org=p.org_a, email=email)
    _seed_link(p.admin_engine, org=p.org_a, email=email, phone=phone)
    old = await _recorded(bot, email=email, phone=phone, body="Yesterday")
    _age(bot, old.message_id, updated="25 hours", received="25 hours")

    assert await _in_scope(bot, bot.bot_run.sweep_once) == 0
    assert _state(bot, old.message_id).state == "expired"
    assert bot.agent.calls == [] and bot.sent == []


async def test_the_tries_stop_at_three(bot) -> None:
    p = bot.p
    email, phone = _email(), _phone()
    _seed_member(p.admin_engine, org=p.org_a, email=email)
    _seed_link(p.admin_engine, org=p.org_a, email=email, phone=phone)
    two = await _recorded(bot, email=email, phone=phone, body="Third try")
    three = await _recorded(bot, email=email, phone=phone, body="No fourth")
    _age(bot, two.message_id, updated="10 minutes", tries=2)
    _age(bot, three.message_id, updated="10 minutes", tries=3)
    with p.admin_engine.begin() as c:
        c.execute(text(f"UPDATE {_TABLE} SET state = 'running' "
                       "WHERE id IN (CAST(:a AS uuid), CAST(:b AS uuid))"),
                  {"a": two.message_id, "b": three.message_id})

    assert await _in_scope(bot, bot.bot_run.sweep_once) == 1
    (call,) = bot.agent.calls
    assert call["payload"]["message"] == "Third try"
    assert tuple(_state(bot, two.message_id)) == ("replied", 3, None)
    assert tuple(_state(bot, three.message_id)) == ("failed", 3, "tries")
    # The used-up row gets the general text once, and never again.
    assert bot.sent == [(phone, _FAILED), (phone, _ANSWER)]
    assert await _in_scope(bot, bot.bot_run.sweep_once) == 0
    assert bot.sent == [(phone, _FAILED), (phone, _ANSWER)]


async def test_the_sweep_reads_no_row_of_an_org_off_the_list(
    bot, monkeypatch: pytest.MonkeyPatch,
) -> None:
    p = bot.p
    email, phone = _email(), _phone()
    _seed_member(p.admin_engine, org=p.org_a, email=email)
    _seed_link(p.admin_engine, org=p.org_a, email=email, phone=phone)
    stale = await _recorded(bot, email=email, phone=phone, body="Org A only")
    _age(bot, stale.message_id, updated="10 minutes")
    monkeypatch.setattr(get_settings(), "whatsapp_assistant_orgs", p.org_b,
                        raising=False)
    assert await _in_scope(bot, bot.bot_run.sweep_once) == 0
    assert _state(bot, stale.message_id).state == "received"


# ── Review round 1: order, shared rooms, redelivery ─────────────────────────


def _change_many(sender: str, *bodies: str) -> dict[str, Any]:
    """One webhook change that carries several texts, as Meta batches them."""
    change = _change(sender, bodies[0])
    msgs = change["value"]["messages"]
    for body in bodies[1:]:
        msgs.append({**msgs[0], "id": f"wamid.{uuid.uuid4().hex[:16]}",
                     "text": {"body": body}})
    return change


async def test_two_texts_in_one_batch_run_in_order_on_the_real_rows(bot) -> None:
    p = bot.p
    email, phone = _email(), _phone()
    _seed_member(p.admin_engine, org=p.org_a, email=email)
    _seed_link(p.admin_engine, org=p.org_a, email=email, phone=phone)

    await _webhook(bot, (_change_many(phone, "First", "Second"),))
    first, second = bot.agent.calls
    assert [first["payload"]["message"], second["payload"]["message"]] == [
        "First", "Second"]
    assert first["thread_id"] == second["thread_id"]
    assert second["payload"]["messages"] == [
        {"role": "user", "content": "First"},
        {"role": "assistant", "content": _ANSWER},
    ]
    states = [r.state for r in _bot_rows(bot, phone) if r.direction == "in"]
    assert states == ["replied", "replied"], "the second text waited for the sweep"


async def test_a_thread_that_became_a_room_is_left_and_never_read(bot) -> None:
    p = bot.p
    email, other, phone = _email(), _email("o"), _phone()
    _seed_member(p.admin_engine, org=p.org_a, email=email)
    _seed_member(p.admin_engine, org=p.org_a, email=other)
    _seed_link(p.admin_engine, org=p.org_a, email=email, phone=phone)

    await _webhook(bot, (_change(phone, "Before"),))
    room = bot.agent.calls[0]["thread_id"]
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO chat_session_participant (session_id, subject, role, "
            "organization_id) VALUES (:s, :e, 'member', CAST(:o AS uuid))"),
            {"s": room, "e": other, "o": p.org_a})

    await _webhook(bot, (_change(phone, "After"),))
    second = bot.agent.calls[1]
    assert second["thread_id"] != room, "the bot wrote into a shared room"
    assert second["payload"]["messages"] == [], "the run read the room"
    in_room = _admin_rows(bot, "SELECT content FROM chat_message "
                          "WHERE session_id = :s ORDER BY timestamp_ms", s=room)
    assert [r.content for r in in_room] == ["Before", _ANSWER]
    solo = _admin_rows(bot, "SELECT channel FROM chat_session WHERE id = :s",
                       s=second["thread_id"])
    assert [r.channel for r in solo] == ["whatsapp"]
    # A solo reply records no clearance, the fold's rule.
    authority = _admin_rows(
        bot, "SELECT authority FROM chat_message WHERE session_id = :s "
        "AND role = 'assistant'", s=second["thread_id"])
    assert [r.authority for r in authority] == [None]


async def test_a_redelivery_keeps_the_newest_preview_and_the_chosen_agent(
    bot,
) -> None:
    p = bot.p
    email, phone = _email(), _phone()
    _seed_member(p.admin_engine, org=p.org_a, email=email)
    _seed_link(p.admin_engine, org=p.org_a, email=email, phone=phone)
    old = _change(phone, "Old text", wamid=f"wamid.old.{uuid.uuid4().hex[:8]}")

    await _webhook(bot, (old,))
    await _webhook(bot, (_change(phone, "New text"),))
    sid = bot.agent.calls[0]["thread_id"]
    with p.admin_engine.begin() as c:
        c.execute(text("UPDATE chat_session SET agent_name = 'task-manager' "
                       "WHERE id = :s"), {"s": sid})

    await _webhook(bot, (old,))   # Meta sends the old text again
    await _webhook(bot, (_change(phone, "Third text"),))
    (row,) = _admin_rows(bot, "SELECT last_preview, agent_name, message_count "
                         "FROM chat_session WHERE id = :s", s=sid)
    assert row.last_preview == _ANSWER, "a redelivery rewrote the preview"
    assert row.agent_name == "task-manager", "a bot write reset the agent"
    assert row.message_count == 6
    assert len(bot.agent.calls) == 3
