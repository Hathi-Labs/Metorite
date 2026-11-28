"""Task 4016, R8: a NUL in an address header never drops the mail.

A JSONB value cannot hold U+0000. A display name with a NUL made the insert
into ``email_messages`` fail, so the sync lost that mail. The address parse
now strips a NUL from each name and refuses an address with one. These tests
parse a real mail through the IMAP provider and insert it on a real
Postgres, as R8 asks.
"""
from __future__ import annotations

import os
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from email_ingestion.persist import upsert_message
from email_ingestion.providers.base import EmailAddress
from email_ingestion.providers.imap import _parse_imap_raw_message

_SNAPSHOT = os.environ.get("_ACB_TENANT_LADDER_URL_AT_LAUNCH")
_URL = (
    _SNAPSHOT
    if _SNAPSHOT is not None
    else os.environ.get("TENANT_LADDER_DATABASE_URL", "")
).strip()

pytestmark = pytest.mark.skipif(
    not _URL,
    reason=(
        "TENANT_LADDER_DATABASE_URL unset — R8 requires a REAL Postgres with "
        "pgvector. A skip here is not a pass; CI must set it."
    ),
)


@pytest.fixture(scope="module")
def private_db():
    """A private database with the whole tenant ladder. Dropped at the end."""
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url

    from tests.unit._tenant_ladder import apply_ladder

    admin_url = make_url(_URL)
    name = f"{admin_url.database}_fromnul_{uuid.uuid4().hex[:8]}"
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
                {"s": f"nul-{uuid.uuid4().hex[:10]}"}).scalar())
            aid = str(uuid.uuid4())
            conn.execute(text(
                "INSERT INTO email_accounts "
                "(id, user_id, provider, email_address, credentials_encrypted, "
                " organization_id) "
                "VALUES (:id, 'me@nul.test', 'imap', 'me@nul.test', 'x', :org)"),
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


def _mail(uid: str) -> str:
    return ("From: =?utf-8?q?Asha=00_Rao?= <asha@example.com>\r\n"
            "To: Ma\x00ya <maya@example.com>, x <a\x00b@example.com>\r\n"
            f"Subject: hello\r\nMessage-ID: <{uid}@example.com>\r\n\r\nbody\r\n")


async def test_a_nul_in_a_name_still_stores_the_mail(private_db) -> None:
    from sqlalchemy import text

    uid = uuid.uuid4().hex[:10]
    msg = _parse_imap_raw_message(_mail(uid), uid, [], "")
    assert msg is not None
    assert msg.from_address == EmailAddress(name="Asha Rao",
                                            email="asha@example.com")
    async with _tenant_conn(private_db) as conn:
        assert await upsert_message(conn, private_db.account, msg) is True
    with private_db.engine.connect() as c:
        row = c.execute(text(
            "SELECT from_address->>'name' AS name, "
            "       from_address->>'email' AS email, to_addresses "
            "FROM email_messages "
            "WHERE account_id = :a AND provider_message_id = :p"),
            {"a": private_db.account, "p": uid}).one()
    assert (row.name, row.email) == ("Asha Rao", "asha@example.com")
    assert [a["email"] for a in row.to_addresses] == ["maya@example.com"]
    assert row.to_addresses[0]["name"] == "Maya"


async def test_a_nul_that_reaches_the_insert_fails_it(private_db) -> None:
    """The bite: the insert refuses a NUL. So the strip in the parse is the
    thing that keeps the mail."""
    uid = uuid.uuid4().hex[:10]
    msg = _parse_imap_raw_message(_mail(uid), uid, [], "")
    assert msg is not None
    msg.from_address = EmailAddress(name="Asha\x00 Rao", email="asha@example.com")
    with pytest.raises(Exception, match=r"(?i)\\u0000|null|0x00"):
        async with _tenant_conn(private_db) as conn:
            await upsert_message(conn, private_db.account, msg)
