"""Task #467 — sent mail is never Uncategorized.

The rules run reads inbox mail only, so a Sent, Drafts or Outbox message
carries no label. ``core.UNCATEGORIZED_SQL`` once counted that as
"uncategorized", so the chip, the count and the list held the member's own
mail. The definition now leaves outbound folders out, and the Email Cleaner
reads the same fragment (``core.NOT_OUTBOUND_SQL``).

R8: real Postgres, as the non-privileged role, under FORCE RLS.

Run::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_email_sent_not_uncategorized.py -v -rs
"""
from __future__ import annotations

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth.roles import UserContext, UserRole
from gateway.routes.email import core
from gateway.routes.email.automation import cleanup
from gateway.routes.email.transport.messages import list_messages, message_facets
from sqlalchemy import text

from tests.unit.test_email_n_plus_one import (
    _as_app,
    _assert_non_priv,
    _delete_accounts,
    _seed_account,
)
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

OWNER = "owner@mt-467.test"


def test_one_fragment_serves_the_chip_the_count_and_the_cleaner() -> None:
    assert core.NOT_OUTBOUND_SQL in core.UNCATEGORIZED_SQL
    assert core.NOT_OUTBOUND_SQL in cleanup._CLEANUP_SCOPE
    assert set(core.NOT_INBOUND_FOLDERS) == {"sent", "drafts", "outbox"}


_MAIL = (
    "INSERT INTO email_messages (account_id, provider_message_id, folder, "
    "from_address, to_addresses, subject, categories, received_at, "
    "organization_id) VALUES (CAST(:a AS uuid), :pid, :f, "
    "'{\"email\": \"x@y.test\"}'::jsonb, '[]'::jsonb, 's', "
    "CAST(:c AS text[]), now(), CAST(:o AS uuid))"
)

_DEFAULTS = dict(
    label=None, query=None, thread_id=None, received_after=None,
    received_before=None, is_read=None, is_starred=None, has_attachments=None,
    importance=None, from_email=None, sender_category=None, sort="newest",
    collapse=False,
)


async def test_sent_mail_is_not_in_the_uncategorized_count_or_list(
    promoted, app_engine,  # noqa: F811
) -> None:
    _assert_non_priv(app_engine)
    p = promoted
    box = _seed_account(p.admin_engine, org=p.org_b, owner=OWNER)
    rows = [
        ("in-bare", "inbox", "{}"),
        ("in-labelled", "inbox", "{Newsletter}"),
        ("sent-bare", "sent", "{}"),
        ("sent-upper", "SENT", "{}"),
        ("draft-bare", "drafts", "{}"),
        ("outbox-bare", "outbox", "{}"),
    ]
    with p.admin_engine.begin() as c:
        for pid, folder, cats in rows:
            c.execute(text(_MAIL), {"a": box, "pid": pid, "f": folder,
                                    "c": cats, "o": p.org_b})
    me = UserContext(email=OWNER, role=UserRole.EMPLOYEE, organization_id=p.org_b)
    try:
        async with _as_app(p, p.org_b):
            # The count of each folder, as the chip row asks for it.
            counts = {
                f: (await message_facets(account_id=box, folder=f, user=me))
                ["uncategorized"]
                for f in ("INBOX", "sent", "drafts", "outbox", "all")
            }
            assert counts == {"INBOX": 1, "sent": 0, "drafts": 0,
                              "outbox": 0, "all": 1}, counts
            # The list behind the chip.
            for f, want in (("INBOX", 1), ("sent", 0), ("drafts", 0), ("all", 1)):
                got = await list_messages(
                    account_id=box, folder=f, uncategorized=True, page=1,
                    page_size=50, user=me, **_DEFAULTS)
                total = got["total"] if isinstance(got, dict) else len(got)
                assert total == want, (f, got)
    finally:
        _delete_accounts(p.admin_engine, [box])
