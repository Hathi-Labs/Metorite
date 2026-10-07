"""The email upsert writes a row only when the message changed.

🔴 Measured on production, 2026-10-07. The sync rewrote every listed message
on every pass: 915,108 upserts in one day, 2.92 M updates and 874 autovacuums
on ``email_messages`` for 39.7 k live rows. The WAL from that starved the IO
budget of the database, and then sign-in timed out.

The rule (R7): ``email_ingestion.persist._ON_CONFLICT_UPDATE`` carries a
``WHERE (...) IS DISTINCT FROM (...)`` guard over every column its SET writes,
and NOT over ``updated_at``, which the SET always bumps.

Mutation: delete the ``WHERE`` part from ``_ON_CONFLICT_UPDATE``. Then
``test_an_unchanged_message_writes_no_new_row_version`` and the two
headers-only tests fail, because ``xmin`` moves on each run. Add
``updated_at`` to ``_SYNCED_COLUMNS`` and the same tests fail.

WS-17 EM-S3 (:class:`TestColdHtml`). With ``html_tier.hot_only()`` true, a
re-sync of a cold row writes no HTML back, and ``xmin`` stays. That holds for a
cleared row and for a row that still holds its HTML. Mutation S3-M1 binds the
HTML of a cold message, and the cleared-row case fails.

R8: the ``private_db`` fixture builds a PRIVATE database on the server that
``TENANT_LADDER_DATABASE_URL`` names, applies the whole tenant ladder, and
drops the database at the end. It never touches the shared ladder database.
The upsert runs through asyncpg, the driver of production.

Run (real Postgres)::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_email_upsert_guard.py -v -rs
"""
from __future__ import annotations

import os
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from email_ingestion import persist
from email_ingestion.persist import upsert_message

# ── the shape of the SQL, DB-free ───────────────────────────────────────────


def test_the_guard_compares_every_column_the_set_writes():
    sql = persist._ON_CONFLICT_UPDATE
    set_part, where_part = sql.split("\n   WHERE ", 1)
    for col, expr in persist._SYNCED_COLUMNS:
        assert f"{col} = {expr}" in set_part
        assert f"email_messages.{col}" in where_part
        assert expr in where_part
    assert "IS DISTINCT FROM" in where_part


def test_updated_at_is_bumped_but_never_compared():
    """With ``updated_at`` in the compare, now() differs on every row and the
    guard does nothing."""
    sql = persist._ON_CONFLICT_UPDATE
    set_part, where_part = sql.split("\n   WHERE ", 1)
    assert "updated_at = now()" in set_part
    assert "updated_at" not in where_part
    assert "synced_at" not in where_part
    assert "updated_at" not in {c for c, _ in persist._SYNCED_COLUMNS}


# ── R8: a real database ─────────────────────────────────────────────────────

_SNAPSHOT = os.environ.get("_ACB_TENANT_LADDER_URL_AT_LAUNCH")
_URL = (
    _SNAPSHOT
    if _SNAPSHOT is not None
    else os.environ.get("TENANT_LADDER_DATABASE_URL", "")
).strip()

_DB_GATE = pytest.mark.skipif(
    not _URL,
    reason=(
        "TENANT_LADDER_DATABASE_URL unset — R8 requires a REAL Postgres with "
        "pgvector. A skip here is not a pass; CI must set it."
    ),
)


@dataclass
class _Addr:
    name: str = "Jane"
    email: str = "jane@example.com"


@dataclass
class _Att:
    filename: str = "f.pdf"
    mime_type: str = "application/pdf"
    size_bytes: int = 10
    provider_attachment_id: str = "att-1"


@dataclass
class _Msg:
    provider_message_id: str = "pm-1"
    internet_message_id: str | None = "<abc@example.com>"
    thread_id: str | None = "t-1"
    folder: str = "inbox"
    labels: list = field(default_factory=lambda: ["INBOX"])
    from_address: Any = field(default_factory=_Addr)
    to_addresses: list = field(default_factory=lambda: [_Addr("Me", "me@x.test")])
    cc_addresses: list = field(default_factory=list)
    bcc_addresses: list = field(default_factory=list)
    subject: str = "hello"
    body_text: str = "the body"
    body_html: str | None = "<p>the body</p>"
    snippet: str = "the body"
    has_attachments: bool = True
    attachments: list = field(default_factory=lambda: [_Att()])
    is_read: bool = False
    is_starred: bool = False
    is_flagged: bool = False
    importance: str = "normal"
    categories: list = field(default_factory=list)
    categories_authoritative: bool = False
    unsubscribe_link: str | None = "https://unsub.example/x"
    received_at: Any = field(
        default_factory=lambda: datetime(2026, 10, 7, 9, 30, 15, 123456, tzinfo=UTC))


@pytest.fixture(scope="module")
def private_db():
    """A private database with the whole tenant ladder. Dropped at the end."""
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url

    from tests.unit._tenant_ladder import apply_ladder

    admin_url = make_url(_URL)
    name = f"{admin_url.database}_upguard_{uuid.uuid4().hex[:8]}"
    maint = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with maint.connect() as c:
        c.execute(text(f'CREATE DATABASE "{name}"'))
    maint.dispose()

    eng = create_engine(admin_url.set(database=name), future=True)
    try:
        with eng.begin() as conn:
            apply_ladder(conn)
        with eng.begin() as conn:
            org = str(conn.execute(text(
                "INSERT INTO organization (slug, display_name) "
                "VALUES (:s, :s) RETURNING id"),
                {"s": f"upg-{uuid.uuid4().hex[:10]}"}).scalar())
            aid = str(uuid.uuid4())
            conn.execute(text(
                "INSERT INTO email_accounts "
                "(id, user_id, provider, email_address, credentials_encrypted, "
                " organization_id) "
                "VALUES (:id, 'me@upg.test', 'microsoft', 'me@upg.test', 'x', :org)"),
                {"id": aid, "org": org})
        yield SimpleNamespace(engine=eng, org=org, account=aid)
    finally:
        eng.dispose()
        maint = create_engine(admin_url, isolation_level="AUTOCOMMIT")
        with maint.connect() as c:
            c.execute(text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = :d AND pid <> pg_backend_pid()"), {"d": name})
            c.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        maint.dispose()


@asynccontextmanager
async def _tenant_conn(db):
    """One transaction on asyncpg, bound to the tenant, committed on exit."""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    aeng = create_async_engine(db.engine.url.set(drivername="postgresql+asyncpg"))
    try:
        async with aeng.connect() as conn, conn.begin():
            await conn.execute(
                text("SELECT set_config('app.tenant_id', :o, true)"), {"o": db.org})
            yield conn
    finally:
        await aeng.dispose()


async def _upsert(db, msg) -> None:
    async with _tenant_conn(db) as conn:
        await upsert_message(conn, db.account, msg)


def _row(db, pmid: str):
    from sqlalchemy import text

    with db.engine.connect() as c:
        return c.execute(text(
            "SELECT xmin::text AS xmin, updated_at, is_read, body_text, snippet, "
            "       categories "
            "FROM email_messages "
            "WHERE account_id = :a AND provider_message_id = :p"),
            {"a": db.account, "p": pmid}).one()


def _pmid() -> str:
    return f"pm-{uuid.uuid4().hex[:12]}"


@_DB_GATE
class TestTheGuardOnARealDatabase:
    async def test_an_unchanged_message_writes_no_new_row_version(self, private_db):
        msg = _Msg(provider_message_id=_pmid())
        await _upsert(private_db, msg)
        first = _row(private_db, msg.provider_message_id)

        await _upsert(private_db, msg)
        second = _row(private_db, msg.provider_message_id)

        assert second.xmin == first.xmin
        assert second.updated_at == first.updated_at

    async def test_a_changed_field_still_writes(self, private_db):
        msg = _Msg(provider_message_id=_pmid())
        await _upsert(private_db, msg)
        first = _row(private_db, msg.provider_message_id)

        await _upsert(private_db, replace(msg, is_read=True))
        second = _row(private_db, msg.provider_message_id)

        assert second.xmin != first.xmin
        assert second.is_read is True
        assert second.updated_at > first.updated_at

    async def test_a_headers_only_relist_writes_nothing(self, private_db):
        """Outlook lists headers only on each tick: the body and the snippet
        come back empty. The SET keeps the stored ones, so the guard must
        compare against what the SET writes, not against raw EXCLUDED."""
        msg = _Msg(provider_message_id=_pmid())
        await _upsert(private_db, msg)
        first = _row(private_db, msg.provider_message_id)

        await _upsert(private_db, replace(
            msg, body_text="", body_html=None, snippet="",
            unsubscribe_link=None, internet_message_id=None))
        second = _row(private_db, msg.provider_message_id)

        assert second.xmin == first.xmin
        assert second.body_text == "the body"
        assert second.snippet == "the body"

    async def test_rule_labels_on_a_non_authoritative_provider_write_nothing(
        self, private_db,
    ):
        """The rule engine wrote a label. A provider that cannot report
        labels sends none, and the SET keeps the stored ones."""
        from sqlalchemy import text

        msg = _Msg(provider_message_id=_pmid())
        await _upsert(private_db, msg)
        with private_db.engine.begin() as c:
            c.execute(text(
                "UPDATE email_messages SET categories = ARRAY['Newsletter'] "
                "WHERE account_id = :a AND provider_message_id = :p"),
                {"a": private_db.account, "p": msg.provider_message_id})
        first = _row(private_db, msg.provider_message_id)

        await _upsert(private_db, msg)
        second = _row(private_db, msg.provider_message_id)

        assert second.xmin == first.xmin
        assert second.categories == ["Newsletter"]

    async def test_an_authoritative_label_change_still_writes(self, private_db):
        msg = _Msg(provider_message_id=_pmid(), categories_authoritative=True)
        await _upsert(private_db, msg)
        first = _row(private_db, msg.provider_message_id)

        await _upsert(private_db, replace(msg, categories=["Done"]))
        second = _row(private_db, msg.provider_message_id)

        assert second.xmin != first.xmin
        assert second.categories == ["Done"]

    async def test_n_tup_upd_counts_only_the_real_change(self, private_db):
        """The counter the production numbers came from, read in-transaction
        from ``pg_stat_xact_user_tables`` so no stats delay applies."""
        from sqlalchemy import text

        msg = _Msg(provider_message_id=_pmid())
        await _upsert(private_db, msg)

        async with _tenant_conn(private_db) as conn:
            for _ in range(3):
                await upsert_message(conn, private_db.account, msg)
            await upsert_message(
                conn, private_db.account, replace(msg, is_starred=True))
            upd = (await conn.execute(text(
                "SELECT n_tup_upd FROM pg_stat_xact_user_tables "
                "WHERE relname = 'email_messages'"))).scalar()

        assert upd == 1


# ── EM-S3: a re-sync never writes cold HTML back (R8) ───────────────────────


def _hot_only(monkeypatch, on: bool) -> None:
    """Both flags of ``html_tier.hot_only()``, through the settings object."""
    from acb_common.settings import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "email_html_from_provider", on, raising=False)
    monkeypatch.setattr(settings, "email_html_hot_only", on, raising=False)


def _html_of(db, pmid: str):
    from sqlalchemy import text

    with db.engine.connect() as c:
        return c.execute(text(
            "SELECT body_html FROM email_messages "
            "WHERE account_id = :a AND provider_message_id = :p"),
            {"a": db.account, "p": pmid}).scalar_one()


def _clear_html(db, pmid: str) -> None:
    """What the clear job of EM-S4 does to one row: HTML NULL, text kept."""
    from sqlalchemy import text

    with db.engine.begin() as c:
        c.execute(text(
            "UPDATE email_messages SET body_html = NULL "
            "WHERE account_id = :a AND provider_message_id = :p"),
            {"a": db.account, "p": pmid})


_COLD = datetime(2024, 1, 2, 3, 4, 5, tzinfo=UTC)


@_DB_GATE
class TestColdHtml:
    """WS-17 EM-S3, ``email_app_master_plan.md`` §14.6.3. The SET keeps a
    stored body through ``COALESCE``, and the upsert binds NULL HTML for a
    cold message. So the guard sees no change, and ``xmin`` stays.

    Mutation S3-M1: bind the HTML of a cold message in
    ``persist._bodies``. Then the cleared row gets its HTML back, and
    ``xmin`` moves."""

    async def test_a_resync_of_a_cleared_cold_row_with_html_writes_no_row(
        self, private_db, monkeypatch,
    ) -> None:
        msg = _Msg(provider_message_id=_pmid(), received_at=_COLD)
        _hot_only(monkeypatch, False)
        await _upsert(private_db, msg)
        _clear_html(private_db, msg.provider_message_id)
        first = _row(private_db, msg.provider_message_id)

        _hot_only(monkeypatch, True)
        await _upsert(private_db, msg)
        second = _row(private_db, msg.provider_message_id)

        assert second.xmin == first.xmin
        assert _html_of(private_db, msg.provider_message_id) is None
        assert second.body_text == "the body"

    async def test_a_resync_of_a_cold_row_that_holds_html_keeps_it(
        self, private_db, monkeypatch,
    ) -> None:
        msg = _Msg(provider_message_id=_pmid(), received_at=_COLD)
        _hot_only(monkeypatch, False)
        await _upsert(private_db, msg)
        first = _row(private_db, msg.provider_message_id)

        _hot_only(monkeypatch, True)
        await _upsert(private_db, msg)
        second = _row(private_db, msg.provider_message_id)

        assert second.xmin == first.xmin
        assert _html_of(private_db, msg.provider_message_id) == "<p>the body</p>"

    async def test_a_cold_change_still_writes_and_brings_no_html(
        self, private_db, monkeypatch,
    ) -> None:
        msg = _Msg(provider_message_id=_pmid(), received_at=_COLD)
        _hot_only(monkeypatch, True)
        await _upsert(private_db, msg)
        first = _row(private_db, msg.provider_message_id)

        await _upsert(private_db, replace(msg, is_read=True))
        second = _row(private_db, msg.provider_message_id)

        assert second.xmin != first.xmin
        assert second.is_read is True
        assert _html_of(private_db, msg.provider_message_id) is None

    async def test_with_the_flag_off_a_cleared_row_gets_its_html_back(
        self, private_db, monkeypatch,
    ) -> None:
        """The behaviour before EM-S3, and the reason for it."""
        msg = _Msg(provider_message_id=_pmid(), received_at=_COLD)
        _hot_only(monkeypatch, False)
        await _upsert(private_db, msg)
        _clear_html(private_db, msg.provider_message_id)
        first = _row(private_db, msg.provider_message_id)

        await _upsert(private_db, msg)
        second = _row(private_db, msg.provider_message_id)

        assert second.xmin != first.xmin
        assert _html_of(private_db, msg.provider_message_id) == "<p>the body</p>"
