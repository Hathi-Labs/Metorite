"""Reply Zero on a real database: the active buckets read the digest's rule (R8).

HANDOFF H-286. The digest's ``_LIVE_THREAD`` (``routes/email/digest.py``)
leaves out a thread when its last message is in trash, junk or the archive,
or the member snoozed it. Reply Zero's active buckets now read the same
predicate, so a snoozed thread and a junk thread are off the Reply list.
Done still shows an archived thread, and hides trash only.

Against a REAL Postgres, under FORCE RLS, as the non-privileged app role. With
no ``TENANT_LADDER_DATABASE_URL`` the suite skips, and a skip is not a pass.

Mutation (R7): put back the old ``'trash', 'archive'`` list in
``replyzero.reply_zero``, and the snoozed and junk threads come back.

Run (real Postgres)::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_email_reply_zero_r8.py -v -rs
"""
from __future__ import annotations

import uuid

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import text

from tests.unit.test_email_keep_separate import _account, _as_member
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)
from tests.unit.test_read_tool_paging_r8 import (  # noqa: F401
    ME,
    T0,
    _direct,
    _messages,
    _user,
    db,
)

pytestmark = _DB_GATE


async def test_the_reply_list_leaves_out_a_snoozed_and_a_junk_thread(db) -> None:  # noqa: F811
    from fastapi import BackgroundTasks
    from gateway.routes.email.automation import replyzero

    box = _account(db.admin_engine, org=db.org_b, owner=ME)
    kinds = ("live", "snoozed", "junk", "archive", "trash", "woke")
    threads = {k: f"{k}-{uuid.uuid4().hex[:8]}" for k in kinds}
    with db.admin_engine.begin() as c:
        ids = dict(zip(kinds, _messages(c, box, db.org_b,
                                        [(threads[k], "x@y.io") for k in kinds]),
                       strict=True))
        c.execute(text("UPDATE email_messages SET snoozed_until = now() + interval '1 day' "
                       "WHERE id = CAST(:m AS uuid)"), {"m": ids["snoozed"]})
        # A snooze that has passed puts the thread back on the list.
        c.execute(text("UPDATE email_messages SET snoozed_until = now() - interval '1 hour' "
                       "WHERE id = CAST(:m AS uuid)"), {"m": ids["woke"]})
        for k in ("junk", "archive", "trash"):
            c.execute(text("UPDATE email_messages SET folder = :f "
                           "WHERE id = CAST(:m AS uuid)"), {"f": k.upper(), "m": ids[k]})
        for k in kinds:
            c.execute(text(
                "INSERT INTO email_thread_status (account_id, thread_id, status, "
                "last_message_id, last_message_at, organization_id) VALUES "
                "(CAST(:a AS uuid), :t, 'NEEDS_REPLY', CAST(:m AS uuid), :at, "
                "CAST(:o AS uuid))"),
                {"a": box, "t": threads[k], "m": ids[k], "at": T0, "o": db.org_b})
    user = _user(db.org_b, "feature:email")

    async def bucket(kind: str) -> set[str]:
        async with _as_member(db, db.org_b):
            got = await _direct(replyzero.reply_zero, background=BackgroundTasks(),
                                account_id=box, type=kind, limit=50, user=user)
        return {t["thread_id"] for t in got["threads"]}

    assert await bucket("needs_reply") == {threads["live"], threads["woke"]}

    # Done is not an active bucket: it shows an archived thread, and no trash.
    with db.admin_engine.begin() as c:
        c.execute(text("UPDATE email_thread_status SET status = 'DONE' "
                       "WHERE account_id = CAST(:a AS uuid)"), {"a": box})
    done = await bucket("done")
    assert threads["archive"] in done and threads["trash"] not in done
    assert threads["snoozed"] in done and threads["junk"] in done
