"""R8 — the read of the reply card, on a REAL Postgres (task 4015).

Spec: ``project-docs/specs/chat_parity.md`` §6.4, P-1b. The pure rules are in
``test_capture_reply_thread.py``.

``capture_email._sent_mail`` and ``_thread_in_mailbox`` read as the member,
as the NON-privileged app role, under FORCE ROW LEVEL SECURITY (the phase-4
catalog of ``test_h3_rls_promotion_rehearsal``).

* A member reads the newest sent mail of their own thread, with its stored
  subject and recipients. On a tie of the time, the larger id wins, so the
  card and the write pick the same mail.
* A colleague's mailbox with the SAME thread id gives nothing, even when the
  call names that mailbox. Another org gives nothing.
* A thread of inbox mail only has no sent mail, and a thread of drafts only is
  no thread.
* The route answers a 404 for a mailbox of another member. The write resolver
  names the same mail as the read.

⚠️ It SKIPS without ``TENANT_LADDER_DATABASE_URL``, and a skip is not a pass.
Run it through the scratch slot tooling of the box, or::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_capture_reply_thread_r8.py -v -rs
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth.permissions import EffectiveAccess
from acb_auth.roles import UserContext, UserRole
from fastapi import HTTPException
from sqlalchemy import text

from tests.unit.test_email_keep_separate import (  # noqa: F401  (fixtures by name)
    _DB_GATE,
    _as_member,
    _assert_non_priv,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

NOW = datetime.now(UTC)
THREAD = "<th-4015@mail.test>"


def _user(email: str, org: str) -> UserContext:
    return UserContext(email=email, role=UserRole.EMPLOYEE, organization_id=org,
                       access=EffectiveAccess(role_granted=frozenset(
                           {"feature:tasks", "feature:email"})))


@pytest.fixture
def world(promoted, app_engine):  # noqa: F811 — fixtures
    _assert_non_priv(app_engine)
    p = promoted
    tag = uuid.uuid4().hex[:8]
    m = SimpleNamespace(**{k: f"{k}-{tag}@r4015.test" for k in ("me", "col", "zed")})
    ids: dict[str, Any] = {}
    with p.admin_engine.begin() as c:
        for email, org in ((m.me, p.org_a), (m.col, p.org_a), (m.zed, p.org_b)):
            c.execute(text(
                "INSERT INTO app_user (email, display_name, role, status, organization_id) "
                "VALUES (:e, :e, 'employee', 'active', CAST(:o AS uuid))"), {"e": email, "o": org})

        def box(owner: str, org: str) -> str:
            return str(c.execute(text(
                "INSERT INTO email_accounts (user_id, provider, email_address, "
                "credentials_encrypted, initial_sync_done, organization_id) "
                "VALUES (:u, 'microsoft', :a, 'x', true, CAST(:o AS uuid)) RETURNING id"),
                {"u": owner, "a": f"box-{uuid.uuid4().hex[:6]}@r4015.test", "o": org}
            ).scalar_one())

        def mail(account: str, org: str, *, folder: str, thread: str, subject: str,
                 at: datetime, to: list[dict] | None = None, cc: list[dict] | None = None,
                 mid: str | None = None) -> str:
            return str(c.execute(text(
                "INSERT INTO email_messages (id, account_id, provider_message_id, thread_id, "
                "folder, from_address, to_addresses, cc_addresses, subject, body_text, "
                "snippet, received_at, organization_id) VALUES (CAST(:id AS uuid), "
                "CAST(:a AS uuid), :pid, :th, :f, CAST(:fr AS jsonb), CAST(:to AS jsonb), "
                "CAST(:cc AS jsonb), :s, 'body', 'body', :at, CAST(:o AS uuid)) RETURNING id"),
                {"id": mid or str(uuid.uuid4()), "a": account,
                 "pid": f"r4015-{uuid.uuid4().hex[:8]}", "th": thread, "f": folder,
                 "fr": json.dumps({"name": "X", "email": "x@r4015.test"}),
                 "to": json.dumps(to or []), "cc": json.dumps(cc or []), "s": subject,
                 "at": at, "o": org}).scalar_one())

        day = timedelta(days=1)
        ids["box_me"] = a_me = box(m.me, p.org_a)
        ids["box_col"] = a_col = box(m.col, p.org_a)
        ids["box_zed"] = a_zed = box(m.zed, p.org_b)
        to = [{"name": "ceo@company.test", "email": "ravi@vendor.test"}]
        cc = [{"name": "Priya", "email": "priya@vendor.test"}, {"email": "RAVI@vendor.test"}]
        mail(a_me, p.org_a, folder="inbox", thread=THREAD, subject="Deck", at=NOW - 5 * day)
        ids["old_sent"] = mail(a_me, p.org_a, folder="sent", thread=THREAD,
                               subject="Re: Deck (old)", at=NOW - 4 * day, to=to)
        ids["sent"] = mail(a_me, p.org_a, folder="sent", thread=THREAD, subject="Re: Deck",
                           at=NOW - 2 * day, to=to, cc=cc)
        # A tie of the time: the larger id wins.
        low, high = sorted(str(uuid.uuid4()) for _ in range(2))
        mail(a_me, p.org_a, folder="sent", thread="th-tie", subject="Low", at=NOW - day, mid=low)
        ids["tie"] = mail(a_me, p.org_a, folder="sent", thread="th-tie", subject="High",
                          at=NOW - day, mid=high)
        mail(a_me, p.org_a, folder="inbox", thread="th-inbox", subject="Only inbox",
             at=NOW - day)
        mail(a_me, p.org_a, folder="drafts", thread="th-draft", subject="Only a draft",
             at=NOW - day)
        # The SAME thread id in the mailbox of a colleague, and of another org.
        ids["col_sent"] = mail(a_col, p.org_a, folder="sent", thread=THREAD,
                               subject="Colleague", at=NOW - day, to=to)
        ids["zed_sent"] = mail(a_zed, p.org_b, folder="sent", thread=THREAD,
                               subject="Other org", at=NOW - day, to=to)
    try:
        yield SimpleNamespace(p=p, m=m, ids=ids)
    finally:
        with p.admin_engine.begin() as c:
            users = [m.me, m.col, m.zed]
            c.execute(text("DELETE FROM email_messages WHERE account_id IN (SELECT id FROM "
                           "email_accounts WHERE user_id = ANY(:u))"), {"u": users})
            c.execute(text("DELETE FROM email_accounts WHERE user_id = ANY(:u)"), {"u": users})
            c.execute(text("DELETE FROM app_user WHERE email = ANY(:u)"), {"u": users})


async def _read(w: Any, org: str, uid: str, box: str, thread: str = THREAD) -> Any:
    from gateway.db import tenant_session
    from gateway.routes.tasks import capture_email as ce

    async with _as_member(w.p, org), tenant_session() as db:
        return await ce._sent_mail(db, uid, box, thread)


async def _has_thread(w: Any, org: str, uid: str, box: str, thread: str) -> bool:
    from gateway.db import tenant_session
    from gateway.routes.tasks import capture_email as ce

    async with _as_member(w.p, org), tenant_session() as db:
        return await ce._thread_in_mailbox(db, uid, box, thread)


@pytest.mark.asyncio
async def test_r8_a_member_reads_the_newest_sent_mail_of_their_thread(world) -> None:
    from gateway.routes.tasks import capture_email as ce

    w = world
    row = await _read(w, w.p.org_a, w.m.me, w.ids["box_me"])
    assert str(row.id) == w.ids["sent"] and row.subject == "Re: Deck"
    assert ce._recipient_addresses(row) == ["ravi@vendor.test", "priya@vendor.test"]


@pytest.mark.asyncio
async def test_r8_a_tie_of_the_time_picks_the_larger_id(world) -> None:
    w = world
    row = await _read(w, w.p.org_a, w.m.me, w.ids["box_me"], "th-tie")
    assert str(row.id) == w.ids["tie"] and row.subject == "High"


@pytest.mark.asyncio
async def test_r8_a_colleagues_mailbox_gives_nothing(world) -> None:
    """The colleague's mailbox holds the same thread id. Mutation: drop
    ``a.user_id = :uid`` -> the colleague's mail -> red."""
    w = world
    assert await _read(w, w.p.org_a, w.m.me, w.ids["box_col"]) is None
    assert not await _has_thread(w, w.p.org_a, w.m.me, w.ids["box_col"], THREAD)
    theirs = await _read(w, w.p.org_a, w.m.col, w.ids["box_col"])
    assert str(theirs.id) == w.ids["col_sent"], "non-vacuity: the row is there"


@pytest.mark.asyncio
async def test_r8_another_org_gives_nothing(world) -> None:
    """Under the tenant of org B, the member's own mailbox of org A reads
    nothing, and org B reads its own mail only."""
    w = world
    assert await _read(w, w.p.org_b, w.m.me, w.ids["box_me"]) is None
    assert not await _has_thread(w, w.p.org_b, w.m.me, w.ids["box_me"], THREAD)
    assert await _read(w, w.p.org_a, w.m.zed, w.ids["box_zed"]) is None
    zed = await _read(w, w.p.org_b, w.m.zed, w.ids["box_zed"])
    assert str(zed.id) == w.ids["zed_sent"], "non-vacuity: the row is there"


@pytest.mark.asyncio
async def test_r8_inbox_only_has_no_sent_mail_and_a_draft_is_no_thread(world) -> None:
    w = world
    box = w.ids["box_me"]
    assert await _read(w, w.p.org_a, w.m.me, box, "th-inbox") is None
    assert await _has_thread(w, w.p.org_a, w.m.me, box, "th-inbox")
    assert await _read(w, w.p.org_a, w.m.me, box, "th-draft") is None
    assert not await _has_thread(w, w.p.org_a, w.m.me, box, "th-draft")
    assert not await _has_thread(w, w.p.org_a, w.m.me, box, "th-none")


@pytest.mark.asyncio
async def test_r8_the_route_and_the_write_resolver_name_the_same_mail(world) -> None:
    from gateway.db import tenant_session
    from gateway.routes.tasks import capture_email as ce

    w = world
    req = ce.ReplyThreadRequest(account_id=w.ids["box_me"], thread_id=THREAD)
    async with _as_member(w.p, w.p.org_a):
        got = await ce.preview_commitment_thread(req, user=_user(w.m.me, w.p.org_a))
        async with tenant_session() as db:
            email_id, subject = await ce._resolve_sent_mail(
                db, w.m.me, w.ids["box_me"], THREAD, None)
        empty = await ce.preview_commitment_thread(
            ce.ReplyThreadRequest(account_id=w.ids["box_me"], thread_id="th-inbox"),
            user=_user(w.m.me, w.p.org_a))
        with pytest.raises(HTTPException) as other:
            await ce.preview_commitment_thread(req, user=_user(w.m.col, w.p.org_a))
        with pytest.raises(HTTPException) as none:
            await ce.preview_commitment_thread(
                ce.ReplyThreadRequest(account_id=w.ids["box_me"], thread_id="th-draft"),
                user=_user(w.m.me, w.p.org_a))
    assert (got.email_id, got.subject) == (w.ids["sent"], "Re: Deck")
    assert got.recipients == ["ravi@vendor.test", "priya@vendor.test"]
    assert (email_id, subject) == (got.email_id, got.subject)
    assert (empty.email_id, empty.recipients) == ("", [])
    assert other.value.status_code == 404 and none.value.status_code == 404
    async with _as_member(w.p, w.p.org_b):
        with pytest.raises(HTTPException) as cross:
            await ce.preview_commitment_thread(req, user=_user(w.m.me, w.p.org_b))
    assert cross.value.status_code == 404
