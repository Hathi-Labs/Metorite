"""R8 — the thread health finds a Gmail thread that a custom move rule filed.

WS-17 EM-G3b-f7, ``email_app_master_plan.md`` §12.3.12.6. A Gmail user label
is a label and never a folder (O-GM-1), so a rule move to ``Receipts`` leaves
the message in ``archive``. ``DAMAGED_CONVERSATION_THREADS_SQL`` compared the
rule label with the folder, so it missed that thread, and the health metric
and the repair script missed it with it.

This suite runs the ONE damage definition on a real Postgres, as the
NON-privileged role ``acb_app_h3rls`` under FORCE ROW LEVEL SECURITY. A fake
that agrees with any SQL cannot pass it.

Fences:

* ``test_a_gmail_custom_move_counts_as_damage`` (acceptance 1)
* ``test_a_gmail_system_label_move_does_not_count`` (acceptance 2)
* ``test_an_outlook_thread_counts_the_same_as_before`` (acceptance 3). The
  SQL is live for Outlook, in the analytics overview and the repair script.
* ``test_the_gmail_damage_rule_matches_folder_after_move`` (acceptance 4,
  D-G3b-f7-3). It takes its names from the Python sets, so a name that one
  side adds and the other does not turns it red.

Run (real Postgres)::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_email_thread_health_gmail.py -v -rs
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

pytest.importorskip("sqlalchemy")

from email_ingestion.providers.app_credentials import OAuthApp
from email_ingestion.providers.base import _CANONICAL_FOLDERS, local_folder_after_move
from email_ingestion.providers.gmail import GmailProvider
from gateway.routes.email.automation import replyzero as rz
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

# ``promoted`` and ``app_engine`` are used by name for fixture injection, so
# the import is load-bearing even though it reads as unused.
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

#: User labels that are no system name. One has mixed case, one has spaces at
#: the ends, and one starts like ``CATEGORY_`` with no underscore, so a LIKE
#: with an unescaped ``_`` would refuse it.
_USER_LABELS = ("Receipts", "ReCeIpTs Q3", "  Client Work  ", "CategoryX Reports")


def _gmail() -> GmailProvider:
    return GmailProvider({"access_token": "at-1", "refresh_token": "r-1"},
                         app=OAuthApp(client_id="cid", client_secret="secret"))


def _expects_archive(name: str) -> bool:
    """True when the restore reads ``archive`` for a Gmail move to ``name``.

    The restore strips the label and skips a blank one
    (``_restore_conversation_messages``), so this does the same."""
    stripped = (name or "").strip()
    if not stripped:
        return False
    return local_folder_after_move(_gmail(), stripped) == "archive"


def _assert_non_priv(engine) -> None:
    with engine.connect() as c:
        role = c.execute(text(
            "SELECT rolsuper, rolbypassrls FROM pg_roles "
            "WHERE rolname = current_user")).first()
    assert role is not None and not role[0] and not role[1], (
        "this suite connects as a SUPERUSER/BYPASSRLS role, so RLS is bypassed")


@pytest.fixture()
def seeded(promoted, app_engine):  # noqa: F811
    """A seed helper for org B, and two readers that run as ``acb_app_h3rls``.

    ``thread(provider, folder, label)`` makes one ``NEEDS_REPLY`` thread with
    one message in ``folder``, and an APPLIED ``MOVE_FOLDER`` rule to
    ``label`` on it. Each call makes its own mailbox, so a count by mailbox
    reads that thread alone."""
    _assert_non_priv(app_engine)
    p = promoted
    run = uuid.uuid4().hex[:8]
    boxes: list[str] = []

    def thread(provider: str, folder: str, label: str | None) -> str:
        n = len(boxes)
        with p.admin_engine.begin() as c:
            box = str(c.execute(text(
                "INSERT INTO email_accounts (user_id, provider, email_address, "
                "credentials_encrypted, organization_id) "
                "VALUES (:u, :p, :m, 'x', CAST(:o AS uuid)) RETURNING id"),
                {"u": f"owner-{run}@em-health.test", "p": provider,
                 "m": f"box-{run}-{n}@em-health.test", "o": p.org_b}).scalar_one())
            boxes.append(box)
            msg = str(c.execute(text(
                "INSERT INTO email_messages (account_id, provider_message_id, "
                "thread_id, folder, from_address, to_addresses, subject, "
                "received_at, organization_id) VALUES "
                "(CAST(:a AS uuid), :pid, 'th-1', :f, "
                "'{\"name\": \"Ravi\", \"email\": \"ravi@contoso.test\"}'::jsonb, "
                "'[]'::jsonb, 'Your receipt', now(), CAST(:o AS uuid)) RETURNING id"),
                {"a": box, "pid": f"pm-{run}-{n}", "f": folder,
                 "o": p.org_b}).scalar_one())
            rule = str(c.execute(text(
                "INSERT INTO email_rules (account_id, name, organization_id) "
                "VALUES (CAST(:a AS uuid), 'File receipts', CAST(:o AS uuid)) "
                "RETURNING id"), {"a": box, "o": p.org_b}).scalar_one())
            c.execute(text(
                "INSERT INTO email_actions (rule_id, type, label, organization_id) "
                "VALUES (CAST(:r AS uuid), 'MOVE_FOLDER', :l, CAST(:o AS uuid))"),
                {"r": rule, "l": label, "o": p.org_b})
            c.execute(text(
                "INSERT INTO email_executed_rules (account_id, rule_id, rule_name, "
                "message_id, thread_id, status, actions_taken, organization_id) "
                "VALUES (CAST(:a AS uuid), CAST(:r AS uuid), 'File receipts', "
                "CAST(:m AS uuid), 'th-1', 'APPLIED', '[\"MOVE_FOLDER\"]'::jsonb, "
                "CAST(:o AS uuid))"),
                {"a": box, "r": rule, "m": msg, "o": p.org_b})
            c.execute(text(
                "INSERT INTO email_thread_status (account_id, thread_id, status, "
                "organization_id) VALUES (CAST(:a AS uuid), 'th-1', 'NEEDS_REPLY', "
                "CAST(:o AS uuid))"), {"a": box, "o": p.org_b})
        return box

    # asyncpg, as the seam uses it: psycopg cannot run async on the Windows
    # Proactor loop that pytest-asyncio starts.
    app_dsn = p.app_url.set(drivername="postgresql+asyncpg").render_as_string(
        hide_password=False)

    async def count(box: str) -> int:
        """The health metric for one mailbox, as the app role, tenant bound."""
        eng = create_async_engine(app_dsn)
        try:
            async with eng.begin() as conn:
                await conn.execute(text(
                    "SELECT set_config('app.tenant_id', :o, true)"), {"o": p.org_b})
                return await rz.count_damaged_conversation_threads(conn, account_id=box)
        finally:
            await eng.dispose()

    async def damaged_boxes() -> set[str]:
        """The mailboxes of this run that the repair script would select."""
        eng = create_async_engine(app_dsn)
        try:
            async with eng.begin() as conn:
                await conn.execute(text(
                    "SELECT set_config('app.tenant_id', :o, true)"), {"o": p.org_b})
                rows = (await conn.execute(
                    text(rz.DAMAGED_CONVERSATION_THREADS_SQL))).fetchall()
        finally:
            await eng.dispose()
        return {str(r.account_id) for r in rows} & set(boxes)

    try:
        yield SimpleNamespace(thread=thread, count=count, damaged_boxes=damaged_boxes)
    finally:
        with p.admin_engine.begin() as c:
            for box in boxes:
                params = {"a": box}
                c.execute(text("DELETE FROM email_thread_status "
                               "WHERE account_id = CAST(:a AS uuid)"), params)
                c.execute(text("DELETE FROM email_executed_rules "
                               "WHERE account_id = CAST(:a AS uuid)"), params)
                c.execute(text("DELETE FROM email_rules "
                               "WHERE account_id = CAST(:a AS uuid)"), params)
                c.execute(text("DELETE FROM email_messages "
                               "WHERE account_id = CAST(:a AS uuid)"), params)
                c.execute(text("DELETE FROM email_accounts "
                               "WHERE id = CAST(:a AS uuid)"), params)


class TestTheGmailThreadHealthOnARealDatabase:
    async def test_a_gmail_custom_move_counts_as_damage(self, seeded) -> None:
        box = seeded.thread("gmail", "archive", "Receipts")
        assert await seeded.count(box) == 1
        # The repair script reads the same rows, so it heals the thread.
        assert await seeded.damaged_boxes() == {box}

    async def test_a_gmail_system_label_move_does_not_count(self, seeded) -> None:
        archive = seeded.thread("gmail", "archive", "Archive")
        # ``Archive`` counts through the compare that each provider keeps.
        assert await seeded.count(archive) == 1
        for label in ("Spam", "STARRED", "CATEGORY_SOCIAL"):
            box = seeded.thread("gmail", "archive", label)
            assert await seeded.count(box) == 0, label

    async def test_an_outlook_thread_counts_the_same_as_before(self, seeded) -> None:
        # Both values match main (acceptance 3). The SQL is live for Outlook.
        filed = seeded.thread("microsoft", "receipts", "Receipts")
        assert await seeded.count(filed) == 1
        archived = seeded.thread("microsoft", "archive", "Receipts")
        assert await seeded.count(archived) == 0
        # IMAP keeps the compare of main too (non-goal: no IMAP change).
        imap = seeded.thread("imap", "archive", "Receipts")
        assert await seeded.count(imap) == 0

    async def test_the_gmail_damage_rule_matches_folder_after_move(self, seeded) -> None:
        g = GmailProvider
        names: set[str] = set()
        for name in (set(_CANONICAL_FOLDERS) | set(g._GMAIL_RESERVED)
                     | set(g._MOVE_SYSTEM_KEYS) | set(g._MOVE_REFUSED_KEYS)):
            # Each case the Python side folds: as written, lower and title.
            names |= {name, name.lower(), name.upper(), name.title()}
        names |= {"CATEGORY_SOCIAL", "category_promotions", " CATEGORY_UPDATES "}
        names |= set(_USER_LABELS)
        # A blank label: the restore skips it, so the SQL must skip it too.
        names |= {"", "   "}

        box_of = {name: seeded.thread("gmail", "archive", name) for name in sorted(names)}
        got = await seeded.damaged_boxes()
        drift = sorted(
            (repr(name), _expects_archive(name), box in got)
            for name, box in box_of.items()
            if _expects_archive(name) != (box in got)
        )
        assert not drift, (
            "the SQL and GmailProvider.folder_after_move disagree "
            "(name, python expects archive, sql counts): " + repr(drift))
        # The user labels count. A fence that counts nothing proves nothing.
        assert all(box_of[name] in got for name in _USER_LABELS)
