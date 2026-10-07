"""WS-20 WA-C3 — history, echoes, contacts and statuses under FORCE RLS (R8).

Spec: ``project-docs/specs/whatsapp_message_manager.md`` §12.4.1 (P2, P5 to
P11, and acceptance items 2 to 8).

**Why a real database.** Every rule here lives in SQL: the service window that
a history row must not open, the watermarks that keep history out of the
commitment pass, the status that only moves forward, the contact that a remove
keeps, and the phase and progress on the account. A hermetic fake agrees with
whatever SQL it gets, so F1 shipped green that way (§12.3 F12).

This suite posts Meta's sample bodies (``tests/unit/_whatsapp_meta_samples``)
through the REAL webhook route, the REAL persist path and the REAL post-sync
hooks. It runs on the phase-4 catalog of ``test_h3_rls_promotion_rehearsal``
as its NOSUPERUSER NOBYPASSRLS role, which is not the owner of any table.

R7 fences named here:

* ``wa-history-lands-quiet``: a history chunk writes ``in`` and ``out`` rows
  with their status, its flag and both watermarks. It opens no service window,
  and a later commitment pass writes no row for it.
* ``wa-history-is-idempotent``: the same chunk sent two times adds no row.
* ``wa-echo-threads-out``: an echo lands ``out`` in the chat of the live
  inbound message, and the chat reads AWAITING.
* ``wa-contact-remove-keeps-the-row``: an add, then a remove, leave one row
  with ``in_address_book`` false and the name kept.
* ``wa-status-moves-forward``: a ``read`` then a ``delivered`` leave ``read``,
  and an unknown message id creates no row.
* ``wa-history-progress``: the phase and progress rules of P11, and the
  decline of code 2593109.
* every test reads each row back as org B and sees none.

Run (real Postgres)::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_whatsapp_history_under_rls.py -v -rs
"""
from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

import pytest

pytest.importorskip("sqlalchemy")

from acb_common import get_settings
from sqlalchemy import text

from tests.unit import _whatsapp_meta_samples as samples

# ``promoted``, ``app_engine`` and ``granted`` are fixtures used by name, so
# the imports are load-bearing even though ruff reads them as unused.
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)
from tests.unit.test_whatsapp_webhook_under_rls import (  # noqa: F401
    _SECRET,
    _admin_one,
    _assert_non_priv,
    _count_as,
    _post,
    _sign,
    granted,
)

pytestmark = _DB_GATE

_ROOT = Path(__file__).resolve().parents[2]


def _migration() -> Path:
    """The WA-C3 migration, found by CONTENT and never by number (R1)."""
    hits = [
        p for p in sorted((_ROOT / "infra" / "postgres").glob("*.sql"))
        if re.search(r"ADD COLUMN IF NOT EXISTS history_sync_state\b",
                     p.read_text(encoding="utf-8"))
    ]
    assert len(hits) == 1, f"expected one WA-C3 migration, found {hits}"
    return hits[0]


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


def _seed(admin_engine, *, org: str, state: str | None = "requested") -> tuple[str, str]:
    """A coexistence Cloud API number in ``org``. Returns ``(pnid, account)``."""
    pnid = str(uuid.uuid4().int)[:15]
    with admin_engine.begin() as c:
        acct = str(c.execute(text(
            "INSERT INTO wa_accounts (user_id, phone_number, phone_number_id, "
            "credentials_encrypted, provider, sync_status, history_sync_state, "
            "organization_id) VALUES ('alice@wa-c3.test', '+1 555-078-3881', "
            ":p, 'enc', 'cloud_api', 'live', :s, CAST(:o AS uuid)) RETURNING id"),
            {"p": pnid, "s": state, "o": org}).scalar_one())
    return pnid, acct


def _wamid() -> str:
    return f"wamid.{uuid.uuid4().hex[:16]}"


async def _send(p, body: dict) -> None:
    raw = json.dumps(body).encode("utf-8")
    resp = await _post(p.app_url, raw, _sign(raw))
    assert resp.status_code == 200, resp.text


def _rows(admin_engine, sql: str, **params):
    with admin_engine.connect() as c:
        return c.execute(text(sql), params).all()


def _org_b_sees_none(p, account_id: str, *tables: str) -> None:
    for table in tables:
        assert _count_as(p.app_url, p.org_b, table, account_id) == 0, (
            f"a session of org B reads {table} rows of org A")


# ── wa-history-lands-quiet ──────────────────────────────────────────────────

async def test_a_history_chunk_lands_in_and_out_rows_quietly(
    granted, app_engine, real_hooks,  # noqa: F811
):
    _assert_non_priv(app_engine)
    p = granted
    pnid, acct = _seed(p.admin_engine, org=p.org_a)
    w_in, w_out = _wamid(), _wamid()

    await _send(p, samples.history_body(pnid, inbound_id=w_in, outbound_id=w_out))

    rows = {r.w: r for r in _rows(p.admin_engine, """
        SELECT m.wa_message_id AS w, m.direction, m.delivery_status,
               m.from_history, m.send_regime, m.organization_id::text AS o,
               m.rules_processed_at IS NOT NULL AS rules_set,
               m.commitment_checked_at IS NOT NULL AS commit_set,
               c.wa_chat_id, c.service_window_expires_at
        FROM wa_messages m JOIN wa_chats c ON c.id = m.chat_id
        WHERE m.account_id = CAST(:a AS uuid)""", a=acct)}
    assert set(rows) == {w_in, w_out}, "the history chunk was not persisted"
    assert (rows[w_in].direction, rows[w_out].direction) == ("in", "out")
    assert (rows[w_in].delivery_status, rows[w_out].delivery_status) == (
        "read", "delivered")
    for r in rows.values():
        assert r.from_history is True
        assert r.rules_set and r.commit_set, "a history row has no watermark (P8)"
        assert r.wa_chat_id == samples.CUSTOMER
        assert r.service_window_expires_at is None, "history opened a window (P7)"
        assert r.o == p.org_a
    assert rows[w_out].send_regime is None, "an outbound history row says session (P9)"

    acct_row = _admin_one(p.admin_engine,
                          "SELECT history_import_phase, history_import_progress, "
                          "history_sync_state FROM wa_accounts "
                          "WHERE id = CAST(:a AS uuid)", a=acct)
    assert (acct_row.history_import_phase, acct_row.history_import_progress,
            acct_row.history_sync_state) == (1, 55, "requested")

    # A live message from another person fires the commitment pass. The
    # history promise ("I will send ... tomorrow") must not become a row.
    await _send(p, samples.live_message_body(
        pnid, message_id=_wamid(), sender="16505550000", body="hello"))
    assert _admin_one(p.admin_engine,
                      "SELECT count(*) AS n FROM wa_commitments "
                      "WHERE account_id = CAST(:a AS uuid)", a=acct).n == 0, (
        "the commitment pass read a history row (P8)")
    _org_b_sees_none(p, acct, "wa_messages", "wa_chats")


async def test_history_media_lands_skipped(granted, app_engine, real_hooks):  # noqa: F811
    p = granted
    pnid, acct = _seed(p.admin_engine, org=p.org_a)
    body = samples.history_body(pnid, inbound_id=_wamid(), outbound_id=_wamid())
    msg = body["entry"][0]["changes"][0]["value"]["history"][0]["threads"][0][
        "messages"][0]
    msg.pop("text")
    msg.update({"type": "audio", "audio": {"id": "MEDIA-V1", "voice": True,
                                           "mime_type": "audio/ogg"}})

    await _send(p, body)

    [media] = _rows(p.admin_engine, """
        SELECT md.transcription_status FROM wa_media md
        JOIN wa_messages m ON m.id = md.message_id
        WHERE m.account_id = CAST(:a AS uuid)""", a=acct)
    assert media.transcription_status == "skipped"

    await _send(p, body)
    assert len(_rows(p.admin_engine, """
        SELECT 1 FROM wa_media md JOIN wa_messages m ON m.id = md.message_id
        WHERE m.account_id = CAST(:a AS uuid)""", a=acct)) == 1, (
        "a redelivered chunk added a second media row")


# ── wa-history-is-idempotent ────────────────────────────────────────────────

async def test_the_same_history_chunk_twice_adds_no_row(
    granted, app_engine, real_hooks,  # noqa: F811
):
    p = granted
    pnid, acct = _seed(p.admin_engine, org=p.org_b)
    body = samples.history_body(pnid, inbound_id=_wamid(), outbound_id=_wamid())

    for _ in range(2):
        await _send(p, body)

    assert _count_as(p.app_url, p.org_b, "wa_messages", acct) == 2
    assert _count_as(p.app_url, p.org_b, "wa_chats", acct) == 1
    assert _count_as(p.app_url, p.org_a, "wa_messages", acct) == 0


# ── wa-echo-threads-out ─────────────────────────────────────────────────────

async def test_an_echo_threads_out_into_the_chat_of_the_live_message(
    granted, app_engine, real_hooks,  # noqa: F811
):
    p = granted
    pnid, acct = _seed(p.admin_engine, org=p.org_a)
    w_live, w_echo = _wamid(), _wamid()

    await _send(p, samples.live_message_body(pnid, message_id=w_live,
                                             body="where is my order?"))
    await _send(p, samples.echo_body(pnid, message_id=w_echo,
                                     to="+1 (650) 555-1234"))

    rows = {r.w: r for r in _rows(p.admin_engine, """
        SELECT wa_message_id AS w, direction, chat_id, send_regime, from_history
        FROM wa_messages WHERE account_id = CAST(:a AS uuid)""", a=acct)}
    assert rows[w_echo].direction == "out"
    assert rows[w_echo].chat_id == rows[w_live].chat_id, (
        "the echo opened a second chat for one person")
    assert rows[w_echo].send_regime is None
    assert rows[w_echo].from_history is False
    assert _count_as(p.app_url, p.org_a, "wa_chats", acct) == 1
    status = _admin_one(p.admin_engine,
                        "SELECT status FROM wa_chat_status "
                        "WHERE account_id = CAST(:a AS uuid)", a=acct)
    assert status is not None and status.status == "AWAITING"
    # P8: an echo is live, so its promise becomes a commitment of ours.
    [commitment] = _rows(p.admin_engine,
                         "SELECT direction FROM wa_commitments "
                         "WHERE account_id = CAST(:a AS uuid)", a=acct)
    assert commitment.direction == "ours"
    _org_b_sees_none(p, acct, "wa_messages", "wa_chats", "wa_chat_status",
                     "wa_commitments")


# ── wa-contact-remove-keeps-the-row ─────────────────────────────────────────

async def test_a_contact_add_then_remove_keeps_one_row_with_its_name(
    granted, app_engine, real_hooks,  # noqa: F811
):
    p = granted
    pnid, acct = _seed(p.admin_engine, org=p.org_a)

    await _send(p, samples.state_sync_body(pnid, action="add"))
    added = _rows(p.admin_engine,
                  "SELECT phone_number, display_name, in_address_book "
                  "FROM wa_contacts WHERE account_id = CAST(:a AS uuid)", a=acct)
    assert [tuple(r) for r in added] == [(samples.CUSTOMER, "Pablo Morales", True)]

    await _send(p, samples.state_sync_body(pnid, action="remove"))

    rows = _rows(p.admin_engine,
                 "SELECT phone_number, display_name, in_address_book "
                 "FROM wa_contacts WHERE account_id = CAST(:a AS uuid)", a=acct)
    assert [tuple(r) for r in rows] == [(samples.CUSTOMER, "Pablo Morales", False)]
    _org_b_sees_none(p, acct, "wa_contacts")


async def test_a_live_profile_name_does_not_replace_an_address_book_name(
    granted, app_engine, real_hooks,  # noqa: F811
):
    p = granted
    pnid, acct = _seed(p.admin_engine, org=p.org_a)

    await _send(p, samples.state_sync_body(pnid, action="add"))
    await _send(p, samples.live_message_body(pnid, message_id=_wamid(),
                                             name="pablo 🚀"))

    [row] = _rows(p.admin_engine,
                  "SELECT display_name FROM wa_contacts "
                  "WHERE account_id = CAST(:a AS uuid)", a=acct)
    assert row.display_name == "Pablo Morales"


# ── wa-status-moves-forward ─────────────────────────────────────────────────

async def test_a_read_then_a_delivered_leaves_read(
    granted, app_engine, real_hooks,  # noqa: F811
):
    p = granted
    pnid, _ = _seed(p.admin_engine, org=p.org_a)
    w_echo = _wamid()
    await _send(p, samples.echo_body(pnid, message_id=w_echo, body="ok"))

    await _send(p, samples.live_status_body(pnid, message_id=w_echo, status="read"))
    await _send(p, samples.live_status_body(pnid, message_id=w_echo,
                                            status="delivered"))

    row = _admin_one(p.admin_engine,
                     "SELECT delivery_status FROM wa_messages "
                     "WHERE wa_message_id = :w", w=w_echo)
    assert row.delivery_status == "read"

    await _send(p, samples.live_status_body(pnid, message_id=w_echo, status="failed"))
    assert _admin_one(p.admin_engine,
                      "SELECT delivery_status FROM wa_messages "
                      "WHERE wa_message_id = :w", w=w_echo).delivery_status == "read"


async def test_a_status_for_an_unknown_message_creates_no_row(
    granted, app_engine, real_hooks,  # noqa: F811
):
    p = granted
    pnid, acct = _seed(p.admin_engine, org=p.org_a)
    w_unknown = _wamid()

    await _send(p, samples.live_status_body(pnid, message_id=w_unknown,
                                            status="delivered"))

    assert _admin_one(p.admin_engine,
                      "SELECT 1 FROM wa_messages WHERE wa_message_id = :w",
                      w=w_unknown) is None
    assert _count_as(p.app_url, p.org_a, "wa_messages", acct) == 0


async def test_a_sent_then_failed_moves_to_failed(
    granted, app_engine, real_hooks,  # noqa: F811
):
    p = granted
    pnid, _ = _seed(p.admin_engine, org=p.org_a)
    w_echo = _wamid()
    await _send(p, samples.echo_body(pnid, message_id=w_echo, body="ok"))
    for status in ("sent", "failed", "sent"):
        await _send(p, samples.live_status_body(pnid, message_id=w_echo,
                                                status=status))
    assert _admin_one(p.admin_engine,
                      "SELECT delivery_status FROM wa_messages "
                      "WHERE wa_message_id = :w", w=w_echo).delivery_status == "failed"


# ── wa-history-progress ─────────────────────────────────────────────────────

def _progress(p, acct: str):
    return _admin_one(p.admin_engine,
                      "SELECT history_import_phase AS ph, "
                      "history_import_progress AS pr, history_sync_state AS st, "
                      "history_sync_error AS err FROM wa_accounts "
                      "WHERE id = CAST(:a AS uuid)", a=acct)


async def test_the_phase_and_progress_follow_p11(
    granted, app_engine, real_hooks,  # noqa: F811
):
    p = granted
    pnid, acct = _seed(p.admin_engine, org=p.org_a)
    steps = [
        ((0, 55), (1, 55, "requested")),   # first phase seen
        ((0, 40), (1, 55, "requested")),   # same phase keeps GREATEST
        ((1, 10), (2, 10, "requested")),   # a higher phase replaces
        ((0, 90), (2, 10, "requested")),   # a lower phase is ignored
        ((2, 100), (3, 100, "complete")),  # 100 means done
    ]
    for (phase, progress), want in steps:
        await _send(p, samples.history_progress_body(
            pnid, phase=phase, progress=progress))
        got = _progress(p, acct)
        assert (got.ph, got.pr, got.st) == want, (phase, progress)


@pytest.mark.parametrize("where", ["value", "item"])
async def test_the_decline_code_sets_declined(
    granted, app_engine, real_hooks, where,  # noqa: F811
):
    p = granted
    pnid, acct = _seed(p.admin_engine, org=p.org_a)

    await _send(p, samples.history_declined_body(pnid, where=where))

    got = _progress(p, acct)
    assert got.st == "declined"
    assert got.err and "WhatsApp Business app" in got.err


async def test_org_b_reads_no_account_progress_of_org_a(
    granted, app_engine, real_hooks,  # noqa: F811
):
    from sqlalchemy import create_engine

    p = granted
    pnid, acct = _seed(p.admin_engine, org=p.org_a)
    await _send(p, samples.history_progress_body(pnid, phase=0, progress=30))
    eng = create_engine(p.app_url, future=True)
    try:
        with eng.connect() as c, c.begin():
            c.execute(text("SELECT set_config('app.tenant_id', :o, true)"),
                      {"o": p.org_b})
            seen = c.execute(text(
                "SELECT count(*) FROM wa_accounts WHERE id = CAST(:a AS uuid)"),
                {"a": acct}).scalar_one()
    finally:
        eng.dispose()
    assert seen == 0


# ── The migration ───────────────────────────────────────────────────────────

def test_the_migration_replays_twice_on_the_promoted_shape(granted):  # noqa: F811
    """R6: every statement is ``ADD COLUMN IF NOT EXISTS``, so a replay on a
    database that already has the columns changes nothing and raises
    nothing."""
    sql = _migration().read_text(encoding="utf-8")
    for _ in range(2):
        with granted.admin_engine.connect() as c:
            with c.connection.dbapi_connection.cursor() as cur:
                cur.execute(sql)
            c.connection.dbapi_connection.commit()
    cols = {(r.t, r.c): (r.n, r.d) for r in _rows(granted.admin_engine, """
        SELECT table_name AS t, column_name AS c, is_nullable AS n,
               column_default AS d
        FROM information_schema.columns
        WHERE table_schema = 'public' AND (table_name, column_name) IN (
          ('wa_accounts', 'history_sync_state'),
          ('wa_accounts', 'history_sync_error'),
          ('wa_accounts', 'history_import_progress'),
          ('wa_messages', 'delivery_status'),
          ('wa_messages', 'from_history'),
          ('wa_contacts', 'in_address_book'))""")}
    assert len(cols) == 6
    assert all(n == "YES" for n, _ in cols.values()), "a WA-C3 column is NOT NULL"
    assert cols[("wa_messages", "from_history")][1] == "false"
