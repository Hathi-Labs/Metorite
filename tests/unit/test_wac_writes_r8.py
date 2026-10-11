"""WS-47 WAC-4 — the pending-act table and its SQL, on a REAL database (R8).

Spec: ``project-docs/specs/whatsapp_assistant_channel.md`` §14 (14.7: W8,
and W1, W2 and W4 end to end).

The REAL ``POST /whatsapp/webhook`` route, the REAL ``bot_run`` and ``acts``
SQL and the REAL chat writers run against the phase-4 catalog of
``test_h3_rls_promotion_rehearsal``, as its NOSUPERUSER NOBYPASSRLS role. Only
the executor, the Cloud API provider and the Projects gateway behind
``skill_projects.client`` are fakes. The fixtures and the seeds are those of
``test_wac_bot_run_r8.py``.

R7 fences named here:

* ``wac4-table-migration`` (W8): the migration alone installs FORCE RLS with
  USING and WITH CHECK and the one-pending index, and a second run changes
  nothing.
* ``wac4-table-tenancy`` (W8): the app role sees only the bound org's acts,
  cannot write another org's act, and reads no link row of another org.
* ``wac4-sql-one-shot``: the claim moves a row from ``pending`` once, an
  expired row cannot be claimed, and a new park voids the older one.
* ``wac4-end-to-end``: an ask parks one row, Confirm creates one task, and a
  second Confirm creates nothing.

Run (real Postgres)::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_wac_writes_r8.py -v -rs
"""
from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_common import get_settings
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope

# ``promoted``, ``app_engine``, ``granted`` and ``bot`` are fixtures used by
# name, so the imports are load-bearing even though ruff reads them as unused.
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)
from tests.unit.test_wac_bot_run_r8 import (  # noqa: F401
    _admin_rows,
    _change,
    _email,
    _phone,
    _seed_link,
    _seed_member,
    _webhook,
    bot,
    granted,
)

pytestmark = _DB_GATE

_ROOT = Path(__file__).resolve().parents[2]
_TABLE = "whatsapp_pending_acts"
_PROJECT = "0f8fad5b-d9cb-469f-a165-70867728950e"
_READY = "Ready to add it. Tap Confirm to add it, or Cancel."


def _migration() -> Path:
    """The WAC-4 migration, found by CONTENT and never by number (R1)."""
    hits = [
        p for p in sorted((_ROOT / "infra" / "postgres").glob("*.sql"))
        if f"CREATE TABLE IF NOT EXISTS {_TABLE}" in p.read_text(encoding="utf-8")
    ]
    assert len(hits) == 1, f"expected one WAC-4 migration, found {hits}"
    return hits[0]


def _catalog(admin_engine) -> list:
    with admin_engine.connect() as c:
        return [
            c.execute(text(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                f"WHERE oid = '{_TABLE}'::regclass")).one(),
            c.execute(text(
                "SELECT policyname, qual, with_check FROM pg_policies "
                f"WHERE tablename = '{_TABLE}' ORDER BY policyname")).all(),
            c.execute(text(
                "SELECT indexname, indexdef FROM pg_indexes "
                f"WHERE tablename = '{_TABLE}' ORDER BY indexname")).all(),
            c.execute(text(
                "SELECT column_name, is_nullable, column_default FROM "
                "information_schema.columns WHERE (table_name = :t AND "
                "column_name IN ('offered_at', 'offered_wamid')) OR "
                "(table_name = 'whatsapp_bot_messages' AND column_name = "
                "'context_wamid') ORDER BY column_name"), {"t": _TABLE}).all(),
            c.execute(text(
                "SELECT conname FROM pg_constraint WHERE conrelid = "
                f"'{_TABLE}'::regclass AND contype = 'c' ORDER BY conname")).all(),
        ]


def _replay(admin_engine) -> None:
    with admin_engine.connect() as c:
        with c.connection.dbapi_connection.cursor() as cur:
            cur.execute(_migration().read_text(encoding="utf-8"))
        c.connection.dbapi_connection.commit()


# ── W8: the migration ───────────────────────────────────────────────────────


def test_the_migration_installs_force_rls_and_a_second_run_changes_nothing(
    granted,  # noqa: F811
) -> None:
    before = _catalog(granted.admin_engine)
    rls, policies, indexes, columns, checks = before
    assert tuple(rls) == (True, True)
    # Expand-only (R6): each new column is nullable with no default.
    assert [tuple(c) for c in columns] == [
        ("context_wamid", "YES", None), ("offered_at", "YES", None),
        ("offered_wamid", "YES", None)]
    assert ("whatsapp_pending_acts_offered_together",) in [tuple(c) for c in checks]
    ((name, qual, check),) = policies
    assert name == f"{_TABLE}_tenant_isolation"
    assert "app.tenant_id" in qual and "app.tenant_id" in check
    pending = [d for n, d in indexes if n == "uq_whatsapp_pending_acts_one_pending"]
    assert pending and "UNIQUE" in pending[0] and "pending" in pending[0]
    _replay(granted.admin_engine)
    assert _catalog(granted.admin_engine) == before


def test_the_migration_alone_installs_force_rls_on_a_bare_table(granted) -> None:  # noqa: F811
    with granted.admin_engine.begin() as c:
        c.execute(text(f"DROP POLICY {_TABLE}_tenant_isolation ON {_TABLE}"))
        c.execute(text(f"ALTER TABLE {_TABLE} NO FORCE ROW LEVEL SECURITY"))
        c.execute(text(f"ALTER TABLE {_TABLE} DISABLE ROW LEVEL SECURITY"))
    _replay(granted.admin_engine)
    rls, policies, _i, _c, _k = _catalog(granted.admin_engine)
    assert tuple(rls) == (True, True) and len(policies) == 1


# ── W8: the tenancy ─────────────────────────────────────────────────────────


def _seed_thread(admin_engine, *, org: str, email: str) -> str:
    sid = str(uuid.uuid4())
    with admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO chat_session (id, user_id, agent_name, title, "
            "organization_id, channel) VALUES (:s, :e, 'orchestrator', "
            "'WhatsApp', CAST(:o AS uuid), 'whatsapp')"),
            {"s": sid, "e": email, "o": org})
    return sid


def _link_id(admin_engine, *, org: str, phone: str) -> str:
    with admin_engine.connect() as c:
        return str(c.execute(text(
            "SELECT id FROM whatsapp_member_links WHERE wa_id = :w AND "
            "organization_id = CAST(:o AS uuid)"), {"w": phone, "o": org}).scalar_one())


def _seed_act(admin_engine, *, org: str, email: str, phone: str, sid: str,
              link: str, state: str = "pending") -> str:
    with admin_engine.begin() as c:
        return str(c.execute(text(
            f"INSERT INTO {_TABLE} (organization_id, member_email, wa_id, "
            "chat_session_id, link_id, tool_name, arguments, card_text, state, "
            "expires_at) VALUES (CAST(:o AS uuid), :e, :w, :s, CAST(:l AS uuid), "
            "'create_task', '{\"title\": \"x\"}'::jsonb, '[\"t\"]', :st, "
            "now() + interval '10 minutes') RETURNING id"),
            {"o": org, "e": email, "w": phone, "s": sid, "l": link, "st": state},
        ).scalar_one())


def test_the_app_role_sees_only_its_bound_org_and_cannot_write_another(
    granted, app_engine,  # noqa: F811
) -> None:
    p = granted
    email, phone = _email(), _phone()
    _seed_link(p.admin_engine, org=p.org_a, email=email, phone=phone)
    sid = _seed_thread(p.admin_engine, org=p.org_a, email=email)
    link = _link_id(p.admin_engine, org=p.org_a, phone=phone)
    _seed_act(p.admin_engine, org=p.org_a, email=email, phone=phone, sid=sid, link=link)

    with app_engine.connect() as c:
        role = c.execute(text("SELECT rolsuper, rolbypassrls FROM pg_roles "
                              "WHERE rolname = current_user")).one()
        assert tuple(role) == (False, False)
        assert c.execute(text(f"SELECT count(*) FROM {_TABLE}")).scalar() == 0
    with app_engine.connect() as c, c.begin():
        c.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": p.org_b})
        assert c.execute(text(f"SELECT count(*) FROM {_TABLE}")).scalar() == 0
        with pytest.raises(Exception, match="row-level security"):
            c.execute(text(
                f"INSERT INTO {_TABLE} (organization_id, member_email, wa_id, "
                "chat_session_id, link_id, tool_name, arguments, card_text, "
                "expires_at) VALUES (CAST(:o AS uuid), :e, :w, :s, "
                "CAST(:l AS uuid), 'create_task', '{}'::jsonb, '[]', "
                "now() + interval '10 minutes')"),
                {"o": p.org_a, "e": email, "w": phone, "s": sid, "l": link})
    with app_engine.connect() as c, c.begin():
        c.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": p.org_a})
        assert c.execute(text(
            f"SELECT count(*) FROM {_TABLE} WHERE wa_id = :w"), {"w": phone}).scalar() == 1


# ── The SQL of acts.py, as the app role ─────────────────────────────────────


def _req(org: str, email: str, phone: str, sid: str) -> Any:
    from gateway.routes.whatsapp_channel.bot_run import RunRequest

    return RunRequest(str(uuid.uuid4()), org, email, phone, f"wamid.{uuid.uuid4().hex}", sid)


def _act(title: str = "Call the vendor") -> Any:
    from acb_skills.whatsapp_acts import ParkedAct, card_text

    return ParkedAct(tool="create_task", arguments={"project_id": _PROJECT, "title": title},
                     card=card_text("Create this task?", title, "c"),
                     title="Create this task?", detail=title, context="c")


async def _bound(org: str, coro_fn):
    from gateway.db import bind_tenant, release_tenant

    token = bind_tenant(org)
    try:
        return await coro_fn()
    finally:
        release_tenant(token)


async def test_park_claim_and_close_hold_on_the_real_table(granted) -> None:  # noqa: F811
    from gateway.routes.whatsapp_channel import acts

    p = granted
    email, phone = _email(), _phone()
    _seed_link(p.admin_engine, org=p.org_a, email=email, phone=phone)
    sid = _seed_thread(p.admin_engine, org=p.org_a, email=email)
    req = _req(p.org_a, email, phone, sid)

    async with tenant_engine_scope(p.app_url.render_as_string(hide_password=False)):
        first = await _bound(p.org_a, lambda: acts.park(req, _act("First")))
        second = await _bound(p.org_a, lambda: acts.park(req, _act("Second")))
        live = await _bound(p.org_a, lambda: acts._live_act(req))
        assert live["id"] == second and live["live"] is True
        assert live["arguments"]["title"] == "Second"
        assert live["link_id"] == _link_id(p.admin_engine, org=p.org_a, phone=phone)
        # Org B sees no act and no link row of org A.
        req_b = _req(p.org_b, email, phone, sid)
        assert await _bound(p.org_b, lambda: acts._live_act(req_b)) is None
        assert await _bound(p.org_b, lambda: acts._current_link_id(phone, email)) is None
        assert await _bound(p.org_b, lambda: acts._claim(second)) is False
        # Not offered yet: nothing can claim it.
        assert live["offered"] is False
        assert await _bound(p.org_a, lambda: acts._claim(second)) is False
        await _bound(p.org_a, lambda: acts.offered(second, "wamid.out.buttons"))
        # A second offer changes nothing: the first message id stays.
        await _bound(p.org_a, lambda: acts.offered(second, "wamid.out.other"))
        live = await _bound(p.org_a, lambda: acts._live_act(req))
        assert live["offered"] is True and live["offered_wamid"] == "wamid.out.buttons"
        # One claim only.
        assert await _bound(p.org_a, lambda: acts._claim(second)) is True
        assert await _bound(p.org_a, lambda: acts._claim(second)) is False
        assert await _bound(p.org_a, lambda: acts._close(second, "running", "done", None))

    states = dict(_admin_rows(
        type("NS", (), {"p": p}), f"SELECT id::text, state FROM {_TABLE} WHERE wa_id = :w",
        w=phone))
    assert states == {first: "void", second: "done"}


async def test_an_expired_act_reads_as_not_live_and_cannot_be_claimed(granted) -> None:  # noqa: F811
    from gateway.routes.whatsapp_channel import acts

    p = granted
    email, phone = _email(), _phone()
    _seed_link(p.admin_engine, org=p.org_a, email=email, phone=phone)
    sid = _seed_thread(p.admin_engine, org=p.org_a, email=email)
    req = _req(p.org_a, email, phone, sid)

    async with tenant_engine_scope(p.app_url.render_as_string(hide_password=False)):
        act_id = await _bound(p.org_a, lambda: acts.park(req, _act()))
        with p.admin_engine.begin() as c:
            c.execute(text(
                f"UPDATE {_TABLE} SET created_at = now() - interval '20 minutes', "
                "expires_at = now() - interval '1 minute' WHERE id = CAST(:i AS uuid)"),
                {"i": act_id})
        live = await _bound(p.org_a, lambda: acts._live_act(req))
        assert live["live"] is False
        assert await _bound(p.org_a, lambda: acts._claim(act_id)) is False


def _seed_message(admin_engine, *, org: str, email: str, phone: str, sid: str,
                  context: str | None, received: str) -> str:
    with admin_engine.begin() as c:
        return str(c.execute(text(
            "INSERT INTO whatsapp_bot_messages (organization_id, member_email, "
            "wa_id, wamid, chat_session_id, context_wamid, received_at) VALUES "
            "(CAST(:o AS uuid), :e, :w, :m, :s, :ctx, now() + CAST(:r AS interval)) "
            "RETURNING id"),
            {"o": org, "e": email, "w": phone, "m": f"wamid.in.{uuid.uuid4().hex}",
             "s": sid, "ctx": context, "r": received}).scalar_one())


async def test_the_live_read_names_the_tap_and_whether_it_came_after_the_offer(
    granted,  # noqa: F811
) -> None:
    """The join of ``_LIVE_SQL``: a tap's ``context_wamid``, and a message
    received before or after ``offered_at`` (review, 2026-10-11)."""
    from gateway.routes.whatsapp_channel import acts
    from gateway.routes.whatsapp_channel.bot_run import RunRequest

    p = granted
    email, phone = _email(), _phone()
    _seed_link(p.admin_engine, org=p.org_a, email=email, phone=phone)
    sid = _seed_thread(p.admin_engine, org=p.org_a, email=email)
    req = _req(p.org_a, email, phone, sid)
    before = _seed_message(p.admin_engine, org=p.org_a, email=email, phone=phone,
                           sid=sid, context=None, received="-1 minute")
    after = _seed_message(p.admin_engine, org=p.org_a, email=email, phone=phone,
                          sid=sid, context=None, received="1 minute")
    tap = _seed_message(p.admin_engine, org=p.org_a, email=email, phone=phone,
                        sid=sid, context="wamid.out.buttons", received="1 minute")

    def _as(mid: str) -> RunRequest:
        return RunRequest(mid, p.org_a, email, phone, "wamid.x", sid)

    async with tenant_engine_scope(p.app_url.render_as_string(hide_password=False)):
        act_id = await _bound(p.org_a, lambda: acts.park(req, _act()))
        await _bound(p.org_a, lambda: acts.offered(act_id, "wamid.out.buttons"))
        early = await _bound(p.org_a, lambda: acts._live_act(_as(before)))
        late = await _bound(p.org_a, lambda: acts._live_act(_as(after)))
        tapped = await _bound(p.org_a, lambda: acts._live_act(_as(tap)))

    assert (early["after_offer"], early["tap_of"]) == (False, None)
    assert (late["after_offer"], late["tap_of"]) == (True, None)
    assert tapped["tap_of"] == "wamid.out.buttons"
    assert acts._answers_this_card(tapped) and acts._answers_this_card(late)
    assert not acts._answers_this_card(early)


def test_two_pending_acts_for_one_phone_and_thread_are_refused(granted) -> None:  # noqa: F811
    p = granted
    email, phone = _email(), _phone()
    _seed_link(p.admin_engine, org=p.org_a, email=email, phone=phone)
    sid = _seed_thread(p.admin_engine, org=p.org_a, email=email)
    link = _link_id(p.admin_engine, org=p.org_a, phone=phone)
    _seed_act(p.admin_engine, org=p.org_a, email=email, phone=phone, sid=sid, link=link)
    with pytest.raises(Exception, match="uq_whatsapp_pending_acts_one_pending"):
        _seed_act(p.admin_engine, org=p.org_a, email=email, phone=phone, sid=sid, link=link)
    _seed_act(p.admin_engine, org=p.org_a, email=email, phone=phone, sid=sid,
              link=link, state="done")


# ── End to end: the real route, the real tables ─────────────────────────────


class _AskAgent:
    """``run_agent`` that calls the REAL ``create_task`` through the seam."""

    def __init__(self) -> None:
        self.calls = 0
        self.script: list[bool] = [True]

    async def __call__(self, agent: str, payload: dict[str, Any], **kw: Any) -> Any:
        import orchestrator._tool_injection as ti
        import skill_projects
        from acb_skills.memory_tools import _bind_memory_user_id, _unbind_memory_user_id
        from skill_projects.refusals import refusals_as_text

        self.calls += 1
        if not (self.script.pop(0) if self.script else False):
            return {"result": "Nothing to add."}
        tool = ti._gate_injected_tool(refusals_as_text(skill_projects.create_task))
        binding = _bind_memory_user_id(kw["session_user"])
        try:
            await tool(project_id=_PROJECT, title="Call the vendor", due="2026-10-12")
        finally:
            _unbind_memory_user_id(binding)
        return {"result": _READY}


@pytest.fixture()
def writes_bot(bot, monkeypatch: pytest.MonkeyPatch):  # noqa: F811
    s = get_settings()
    monkeypatch.setattr(s, "whatsapp_assistant_native_ui", True, raising=False)
    monkeypatch.setattr(s, "whatsapp_assistant_writes", True, raising=False)
    monkeypatch.setattr(s, "whatsapp_assistant_writes_orgs",
                        f"{bot.p.org_a},{bot.p.org_b}", raising=False)
    agent = _AskAgent()
    monkeypatch.setattr(bot.bot_run, "_executor", lambda: agent)

    sent: list[Any] = []
    buttons: list[str] = []

    class _Provider:
        async def send_text(self, to: str, body: str, **_kw: Any) -> str:
            sent.append(body)
            return f"wamid.out.{uuid.uuid4().hex[:12]}"

        async def send_interactive(self, to: str, inter: dict, **_kw: Any) -> str:
            sent.append([b["reply"]["title"] for b in inter["action"]["buttons"]])
            out = f"wamid.out.{uuid.uuid4().hex[:12]}"
            buttons.append(out)
            return out

        async def show_typing(self, _wamid: str) -> bool:
            return True

        async def send_reaction(self, *_a: Any) -> str:
            return "wamid.reaction"

    from whatsapp_ingestion.providers import factory

    monkeypatch.setattr(factory, "build_provider", lambda _n, _c: _Provider())

    import skill_projects.client as client

    from tests.unit._projects_agent_fakes import FakeClient
    from tests.unit.test_projects_agent_writes import responder

    calls: list[dict] = []
    monkeypatch.setattr(client, "httpx", SimpleNamespace(
        AsyncClient=lambda **_kw: FakeClient(calls, responder)))
    return SimpleNamespace(bot=bot, agent=agent, sent=sent, gateway=calls,
                           buttons=buttons)


def _tap(sender: str, title: str, of: str) -> dict[str, Any]:
    change = _change(sender, title)
    msg = change["value"]["messages"][0]
    msg.pop("text", None)
    msg.update(type="interactive", context={"from": "919800000000", "id": of},
               interactive={"type": "button_reply",
                            "button_reply": {"id": "b1", "title": title}})
    return change


async def test_an_ask_parks_one_row_and_one_confirm_creates_one_task(writes_bot) -> None:
    w = writes_bot
    p = w.bot.p
    email, phone = _email(), _phone()
    _seed_member(p.admin_engine, org=p.org_a, email=email)
    _seed_link(p.admin_engine, org=p.org_a, email=email, phone=phone)

    await _webhook(w.bot, (_change(phone, "Add a task: call the vendor tomorrow"),))
    creates = [c for c in w.gateway if c["method"] == "POST" and c["path"] == "/projects/tasks"]
    assert creates == []
    rows = _admin_rows(w.bot, f"SELECT state, tool_name, arguments, organization_id, "
                       f"member_email FROM {_TABLE} WHERE wa_id = :w", w=phone)
    ((state, tool, args, org, member),) = rows
    assert (state, tool, str(org), member) == ("pending", "create_task", p.org_a, email)
    assert args["title"] == "Call the vendor"
    assert w.sent[-1] == ["Confirm", "Cancel"]

    offered = _admin_rows(w.bot, f"SELECT offered_wamid FROM {_TABLE} WHERE wa_id = :w",
                          w=phone)
    assert [r[0] for r in offered] == [w.buttons[-1]], "the act names its buttons"

    # A tap on another message's buttons writes nothing, and the act stays.
    await _webhook(w.bot, (_tap(phone, "Confirm", "wamid.out.older"),))
    assert [r[0] for r in _admin_rows(
        w.bot, f"SELECT state FROM {_TABLE} WHERE wa_id = :w", w=phone)] == ["pending"]
    taps = _admin_rows(w.bot, "SELECT context_wamid FROM whatsapp_bot_messages "
                       "WHERE wa_id = :w AND context_wamid IS NOT NULL", w=phone)
    assert [r[0] for r in taps] == ["wamid.out.older"]

    await _webhook(w.bot, (_tap(phone, "Confirm", w.buttons[-1]),))
    await _webhook(w.bot, (_change(phone, "Confirm"),))
    await asyncio.sleep(0)
    creates = [c for c in w.gateway if c["method"] == "POST" and c["path"] == "/projects/tasks"]
    assert len(creates) == 1, "a second Confirm wrote"
    assert creates[0]["headers"]["X-User-Email"] == email
    assert w.agent.calls == 2, "the model ran on the first Confirm"
    states = [r[0] for r in _admin_rows(
        w.bot, f"SELECT state FROM {_TABLE} WHERE wa_id = :w", w=phone)]
    assert states == ["done"]


async def test_cancel_and_any_other_message_close_the_row(writes_bot) -> None:
    w = writes_bot
    p = w.bot.p
    email, phone = _email(), _phone()
    _seed_member(p.admin_engine, org=p.org_a, email=email)
    _seed_link(p.admin_engine, org=p.org_a, email=email, phone=phone)

    w.agent.script = [True, False, True]
    await _webhook(w.bot, (_change(phone, "Add a task"),))
    await _webhook(w.bot, (_change(phone, "What else is due?"),))
    await _webhook(w.bot, (_change(phone, "Add a task"),))
    await _webhook(w.bot, (_change(phone, "cancel"),))

    states = sorted(r[0] for r in _admin_rows(
        w.bot, f"SELECT state FROM {_TABLE} WHERE wa_id = :w", w=phone))
    assert states == ["cancelled", "void"]
    assert not [c for c in w.gateway if c["method"] == "POST" and c["path"] == "/projects/tasks"]
