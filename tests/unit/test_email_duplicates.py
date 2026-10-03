"""WS-17 EM-T8g-3 — "Also in" and the draft dedupe.

Spec: ``project-docs/specs/email_app_master_plan.md`` §11.7.7, under
"EM-T8g-3". Decisions D-EM-22, D-EM-28 and D-EM-30 (§11.2). Edge cases 10 and
11 (§11.6).

R7 fences named here:

* ``email-also-in`` (R8): ``GET /email/messages`` and ``GET /email/search``
  give each row ``also_in``: the paired mailboxes that hold a copy with the
  same non-empty ``internet_message_id``. A copy in junk, drafts or trash does
  not count. A mailbox of another member, of a second organization, or a
  separate mailbox never counts. A mail with an empty id pairs with nothing.
  The pair set binds the organization also where row level security does not.
* ``email-also-in-one-read`` (R8): one page makes ONE read for ``also_in``,
  never one for each row, and an empty page makes none.
* ``email-draft-dedupe`` (R8): the automatic draft of B does not start when A
  holds a draft, or a sent mail newer than the copy, in the thread of its
  copy. B drafts when A holds neither, and when either mailbox is separate.
* ``email-draft-dedupe-race`` (R8): two overlapping runs for one mail make one
  draft. The second run meets the try-lock of the first and makes none. A run
  forced into the gap between the two statements of the guard proves that the
  lock comes before the check. After the commit the lock is free, and a
  separate mailbox is never held off.

The UI half, ``email-also-in-row``, is in
``workbench/control_plane/src/app/email/lib/alsoIn.test.ts``.

⚠️ Known limit: only the Outlook provider stores ``internet_message_id``, so
the seeds write the column by hand, as an Outlook sync does.

**R8.** The real SQL against the phase-4-promoted two-org catalog of
``test_h3_rls_promotion_rehearsal``, as the role ``acb_app_h3rls``
(NOSUPERUSER, NOBYPASSRLS). The admin engine seeds and reads the rows.

Run (real Postgres)::

    bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_email_duplicates.py -v -rs
"""
from __future__ import annotations

import inspect
import json
import uuid
from collections.abc import Iterator
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("sqlalchemy")

import structlog
from acb_auth.roles import UserContext, UserRole
from acb_common.db import bind_tenant, release_tenant
from gateway.routes.email import core
from gateway.routes.email.automation import actions, identity
from gateway.routes.email.transport import messages as messages_mod
from gateway.routes.email.transport import search as search_mod
from sqlalchemy import event, text

from tests.unit._tenant_ladder import tenant_engine_scope

# ``promoted`` and ``app_engine`` are fixtures, used by name, so the import is
# load-bearing even though it reads as unused.
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

RAVI = "ravi@contoso-t8g3.test"

# A bare AsyncMock session answers ``fetchone()`` with a coroutine that the
# code never awaits, and Python warns. That shape is the point of two tests.
_BARE_ASYNC_MOCK = "ignore:coroutine .* was never awaited:RuntimeWarning"

# FastAPI Query() defaults do not resolve on a direct call, so each filter
# gets its real "off" value.
_LIST_OFF: dict[str, Any] = {
    "account_id": None, "folder": "INBOX", "label": None, "uncategorized": False,
    "query": None, "thread_id": None, "received_after": None,
    "received_before": None, "is_read": None, "is_starred": None,
    "has_attachments": None, "importance": None, "from_email": None,
    "sender_category": None, "sort": "newest", "collapse": False,
    "page": 1, "page_size": 50,
}
_SEARCH_OFF: dict[str, Any] = {
    "q": None, "account_id": None, "folder": None, "label": None,
    "labels": None, "uncategorized": False, "from_addr": None, "to_addr": None,
    "received_after": None, "received_before": None, "is_read": None,
    "is_starred": None, "has_attachments": None, "sender_category": None,
    "importance": None, "hybrid": False, "light": False, "page": 1,
    "page_size": 50,
}


# ── hermetic ─────────────────────────────────────────────────────────────────


class TestThePairSet:
    """Item 4: one SQL constant in ``identity.py``, beside the self set."""

    def test_it_is_the_self_set_less_this_mailbox_and_each_separate_one(self):
        assert identity.PAIRED_MAILBOX_IDS_SQL == (
            identity.SELF_MAILBOX_IDS_SQL
            + " AND o.id <> a.id AND a.in_all_inboxes AND o.in_all_inboxes")

    def test_the_self_text_did_not_change(self):
        assert identity.SELF_MAILBOX_IDS_SQL == (
            "SELECT o.id FROM email_accounts a "
            "JOIN email_accounts o ON o.user_id = a.user_id "
            "AND o.organization_id IS NOT DISTINCT FROM a.organization_id "
            "WHERE a.id = :aid")

    def test_a_column_anchors_it_for_a_page_and_nothing_else_does(self):
        assert "WHERE a.id = m.account_id AND" in identity.paired_mailbox_ids_sql(
            "m.account_id")
        for bad in ("1; DROP TABLE email_accounts", "m.account_id OR true",
                    "'x'", ":aid)"):
            with pytest.raises(ValueError):
                identity.paired_mailbox_ids_sql(bad)

    def test_the_folders_that_hold_no_copy(self):
        assert core.NOT_A_COPY_FOLDERS == ("drafts", "junk", "trash")


class _Recorder:
    """A session double that answers each statement in turn."""

    def __init__(self, *answers: Any) -> None:
        self.answers = list(answers)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None):
        self.calls.append((str(stmt), dict(params or {})))
        answer = self.answers.pop(0)
        return SimpleNamespace(fetchone=lambda: answer, fetchall=lambda: answer)


class TestTheOneReadHelper:
    """``email-also-in-one-read``, the helper half."""

    async def test_an_empty_page_reads_nothing(self):
        db = _Recorder()
        assert await identity.also_in_by_message(db, [], "m@x.test") == {}
        assert await identity.also_in_by_message(db, ["m1"], "") == {}
        assert db.calls == []

    async def test_a_page_of_fifty_is_one_read(self):
        ids = [str(uuid.uuid4()) for _ in range(50)]
        row = SimpleNamespace(id=ids[3], other="box-b")
        db = _Recorder([row, SimpleNamespace(id=ids[3], other="box-c")])
        out = await identity.also_in_by_message(db, ids + ids[:5], "m@x.test")
        assert len(db.calls) == 1
        sql, params = db.calls[0]
        assert sql == identity.ALSO_IN_SQL
        assert params == {"ids": sorted(ids), "uid": "m@x.test",
                          "not_a_copy": ["drafts", "junk", "trash"]}
        assert out == {ids[3]: ["box-b", "box-c"]}

    @pytest.mark.filterwarnings(_BARE_ASYNC_MOCK)
    async def test_a_test_double_row_counts_as_no_row(self):
        db = AsyncMock()
        assert await identity.also_in_by_message(db, ["m1"], "m@x.test") == {}


class TestTheDraftGuard:
    """``email-draft-dedupe`` and ``email-draft-dedupe-race``, the order of
    the two statements."""

    async def test_no_pair_takes_no_lock_and_reads_nothing_more(self):
        db = _Recorder(None)
        assert await identity.draft_skip_in_pair(db, "acc-b", "m1") is None
        [(sql, params)] = db.calls
        assert "pg_try_advisory_xact_lock(hashtextextended(" in sql
        assert params == {"aid": "acc-b", "mid": "m1"}

    async def test_a_held_lock_is_busy_and_reads_nothing_more(self):
        db = _Recorder(SimpleNamespace(got=False))
        assert await identity.draft_skip_in_pair(db, "acc-b", "m1") == "busy"
        assert len(db.calls) == 1

    async def test_the_check_runs_after_the_lock_in_its_own_statement(self):
        db = _Recorder(SimpleNamespace(got=True), SimpleNamespace(answered=True))
        assert await identity.draft_skip_in_pair(db, "acc-b", "m1") == "answered"
        [(lock_sql, _), (check_sql, params)] = db.calls
        assert "pg_try_advisory_xact_lock" in lock_sql
        assert "pg_try_advisory" not in check_sql and "AS answered" in check_sql
        assert params == {"aid": "acc-b", "mid": "m1",
                          "not_a_copy": ["drafts", "junk", "trash"]}

    async def test_no_answer_drafts(self):
        db = _Recorder(SimpleNamespace(got=True), SimpleNamespace(answered=False))
        assert await identity.draft_skip_in_pair(db, "acc-b", "m1") is None

    async def test_no_mailbox_or_no_mail_reads_nothing(self):
        db = _Recorder()
        assert await identity.draft_skip_in_pair(db, "", "m1") is None
        assert await identity.draft_skip_in_pair(db, "acc-b", "") is None
        assert db.calls == []

    @pytest.mark.filterwarnings(_BARE_ASYNC_MOCK)
    async def test_a_test_double_row_drafts_as_before(self):
        """The shape of ``test_email_ai_context.py``'s rule-draft tests."""
        assert await identity.draft_skip_in_pair(AsyncMock(), "acc-b", "m1") is None

    def test_the_rule_path_asks_after_the_thread_check_and_before_the_model(self):
        src = inspect.getsource(actions._apply_rule_actions)
        thread = src.index("_resolve_existing_thread_draft(")
        pair = src.index("_skip_for_paired_mailbox(")
        assert thread < pair < src.index("_build_reply_context(")
        assert pair < src.index("_agent_draft_reply(")
        assert pair < src.index("provider.create_draft(")
        helper = inspect.getsource(actions._skip_for_paired_mailbox)
        assert "draft_skip_in_pair(" in helper
        assert '"email.draft_skipped_other_mailbox"' in helper

    async def test_a_failed_read_drafts_as_before(self, monkeypatch):
        async def _boom(*_a: Any) -> str:
            raise RuntimeError("the database went away")

        monkeypatch.setattr(actions, "draft_skip_in_pair", _boom)
        with structlog.testing.capture_logs() as caps:
            assert await actions._skip_for_paired_mailbox(
                AsyncMock(), "acc-b", "m1") is False
        assert [c["event"] for c in caps] == ["email.draft_dedupe_failed"]

    async def test_no_mailbox_asks_nothing(self, monkeypatch):
        asked = AsyncMock(return_value="answered")
        monkeypatch.setattr(actions, "draft_skip_in_pair", asked)
        assert await actions._skip_for_paired_mailbox(AsyncMock(), "", "m1") is False
        asked.assert_not_awaited()


# ── R8 helpers ───────────────────────────────────────────────────────────────


def _assert_non_priv(app_eng) -> None:
    with app_eng.connect() as c:
        role = c.execute(text(
            "SELECT rolsuper, rolbypassrls FROM pg_roles "
            "WHERE rolname = current_user")).first()
    assert role is not None and not role[0] and not role[1], (
        "this suite connects as a SUPERUSER/BYPASSRLS role — RLS is bypassed"
    )


def _account(admin, *, org: str, owner: str, pooled: bool = True) -> str:
    with admin.begin() as c:
        return str(c.execute(text(
            "INSERT INTO email_accounts (user_id, provider, email_address, "
            "credentials_encrypted, organization_id, in_all_inboxes) "
            "VALUES (:u, 'microsoft', :m, 'x', CAST(:o AS uuid), :p) "
            "RETURNING id"),
            {"u": owner, "m": f"box-{uuid.uuid4().hex[:8]}@t8g3.test",
             "o": org, "p": pooled}).scalar_one())


def _mail(admin, *, org: str, account_id: str, imid: str | None,
          folder: str = "inbox", thread: str | None = None,
          minutes_ago: int = 10) -> str:
    with admin.begin() as c:
        return str(c.execute(text(
            "INSERT INTO email_messages (account_id, provider_message_id, "
            "thread_id, folder, from_address, to_addresses, subject, body_text, "
            "received_at, is_read, internet_message_id, organization_id) "
            "VALUES (CAST(:a AS uuid), :p, :t, :f, CAST(:frm AS jsonb), "
            "'[]'::jsonb, 'Quote', 'Please send it.', :r, false, :imid, "
            "CAST(:o AS uuid)) RETURNING id"),
            {"a": account_id, "p": f"pm-{uuid.uuid4().hex[:10]}",
             "t": thread or f"t-{uuid.uuid4().hex[:8]}", "f": folder,
             "frm": json.dumps({"name": "Ravi", "email": RAVI}),
             "r": datetime.now(UTC) - timedelta(minutes=minutes_ago),
             "imid": imid, "o": org}).scalar_one())


def _purge(admin, owner_pattern: str) -> None:
    with admin.begin() as c:
        c.execute(text(
            "DELETE FROM email_accounts WHERE user_id LIKE :u"), {"u": owner_pattern})


def _drafts(admin, *account_ids: str) -> int:
    with admin.connect() as c:
        return int(c.execute(text(
            "SELECT count(*) FROM email_messages WHERE folder = 'drafts' "
            "AND account_id = ANY(CAST(:a AS uuid[]))"),
            {"a": list(account_ids)}).scalar_one())


@asynccontextmanager
async def _as_member(p, org: str):
    """Bind ``org`` and point the shared engine at the app role."""
    token = bind_tenant(org)
    try:
        async with tenant_engine_scope(p.app_url.render_as_string(hide_password=False)):
            yield
    finally:
        release_tenant(token)


@contextmanager
def _statements(marker: str) -> Iterator[list[str]]:
    """Each statement sent to Postgres that names ``marker``. It hooks the
    shared engine, so a read that skips the helper is counted too."""
    from acb_common import db as shared

    eng = shared.get_engine().sync_engine
    seen: list[str] = []

    def _record(_conn, _cur, statement, _params, _ctx, _many):
        if marker in statement:
            seen.append(statement)

    event.listen(eng, "before_cursor_execute", _record)
    try:
        yield seen
    finally:
        event.remove(eng, "before_cursor_execute", _record)


def _also_in(out: dict[str, Any]) -> dict[str, list[str]]:
    return {str(e["id"]): e["also_in"] for e in out["emails"]}


@pytest.fixture()
def family(promoted, app_engine):  # noqa: F811
    """One member with mailboxes A and B in org B, a separate mailbox E in org
    B, and C in org A. D is a mailbox of another member in org B."""
    _assert_non_priv(app_engine)
    p = promoted
    tag = uuid.uuid4().hex[:8]
    f = SimpleNamespace(p=p, admin=p.admin_engine, org=p.org_b, tag=tag,
                        member=f"member-{tag}@t8g3.test")
    f.a = _account(f.admin, org=f.org, owner=f.member)
    f.b = _account(f.admin, org=f.org, owner=f.member)
    f.e = _account(f.admin, org=f.org, owner=f.member, pooled=False)
    f.c = _account(f.admin, org=p.org_a, owner=f.member)
    f.d = _account(f.admin, org=f.org, owner=f"stranger-{tag}@t8g3.test")
    f.me = UserContext(email=f.member, role=UserRole.EMPLOYEE, organization_id=f.org)
    try:
        yield f
    finally:
        _purge(f.admin, f"%-{tag}@t8g3.test")


def _imid(f, name: str) -> str:
    return f"<{name}-{f.tag}@contoso-t8g3.test>"


# ── R8: "Also in" ────────────────────────────────────────────────────────────


@_DB_GATE
class TestAlsoIn:
    """``email-also-in``."""

    async def test_each_row_names_each_paired_mailbox_that_holds_a_copy(self, family):
        f = family
        both = _imid(f, "both")
        m_a = _mail(f.admin, org=f.org, account_id=f.a, imid=both)
        m_b = _mail(f.admin, org=f.org, account_id=f.b, imid=both)
        # The same Message-ID in the mailbox of another member, of the same
        # member in another organization, and in a separate mailbox.
        _mail(f.admin, org=f.org, account_id=f.d, imid=both)
        _mail(f.admin, org=f.p.org_a, account_id=f.c, imid=both)
        m_e = _mail(f.admin, org=f.org, account_id=f.e, imid=both)
        # A copy in junk, drafts or trash is no copy. The test names the three
        # folders itself, so a change of the constant cannot change the seed.
        thrown = _imid(f, "thrown")
        m_thrown = _mail(f.admin, org=f.org, account_id=f.a, imid=thrown)
        for folder in ("drafts", "junk", "trash"):
            _mail(f.admin, org=f.org, account_id=f.b, imid=thrown, folder=folder)
        # An empty id pairs with nothing.
        m_none = _mail(f.admin, org=f.org, account_id=f.a, imid=None)
        b_none = _mail(f.admin, org=f.org, account_id=f.b, imid=None)
        m_blank = _mail(f.admin, org=f.org, account_id=f.a, imid="")
        b_blank = _mail(f.admin, org=f.org, account_id=f.b, imid="")
        async with _as_member(f.p, f.org):
            listed = await messages_mod.list_messages(user=f.me, **_LIST_OFF)
            collapsed = await messages_mod.list_messages(
                user=f.me, **{**_LIST_OFF, "collapse": True})
            in_a = await messages_mod.list_messages(
                user=f.me, **{**_LIST_OFF, "account_id": f.a})
            in_e = await messages_mod.list_messages(
                user=f.me, **{**_LIST_OFF, "account_id": f.e})
            found = await search_mod.search_messages(user=f.me, **_SEARCH_OFF)
        for out in (listed, collapsed):
            assert _also_in(out) == {
                m_a: [f.b], m_b: [f.a], m_thrown: [], m_none: [], m_blank: [],
                b_none: [], b_blank: []}
        # One mailbox in view still names the other one (D-EM-22).
        assert _also_in(in_a)[m_a] == [f.b]
        # A separate mailbox pairs with nothing, also by its own id (D-EM-30).
        assert _also_in(in_e) == {m_e: []}
        searched = _also_in(found)
        assert searched[m_a] == [f.b] and searched[m_b] == [f.a]
        assert searched[m_thrown] == [] and searched[m_none] == []
        assert m_e not in searched

    def test_the_org_predicate_holds_where_rls_does_not_bind(self, family):
        """The owner role bypasses row level security, so only the predicates
        of the pair set keep C, D and E out here."""
        f = family
        both = _imid(f, "admin")
        m_a = _mail(f.admin, org=f.org, account_id=f.a, imid=both)
        for box, org in ((f.b, f.org), (f.c, f.p.org_a), (f.d, f.org), (f.e, f.org)):
            _mail(f.admin, org=org, account_id=box, imid=both)
        with f.admin.connect() as c:
            rows = c.execute(text(identity.ALSO_IN_SQL), {
                "ids": [m_a], "uid": f.member,
                "not_a_copy": list(core.NOT_A_COPY_FOLDERS)}).fetchall()
            paired = {str(r[0]) for r in c.execute(
                text(identity.PAIRED_MAILBOX_IDS_SQL), {"aid": f.a})}
            of_e = c.execute(text(identity.PAIRED_MAILBOX_IDS_SQL),
                             {"aid": f.e}).fetchall()
            # Another member never reads it, also with the id of the mail.
            theirs = c.execute(text(identity.ALSO_IN_SQL), {
                "ids": [m_a], "uid": f"stranger-{f.tag}@t8g3.test",
                "not_a_copy": list(core.NOT_A_COPY_FOLDERS)}).fetchall()
        assert [(r.id, r.other) for r in rows] == [(m_a, f.b)]
        assert paired == {f.b}
        assert of_e == []
        assert theirs == []


@_DB_GATE
class TestAlsoInOneRead:
    """``email-also-in-one-read``."""

    async def test_one_page_makes_one_read(self, family):
        f = family
        for i in range(3):
            imid = _imid(f, f"page{i}")
            _mail(f.admin, org=f.org, account_id=f.a, imid=imid, minutes_ago=10 + i)
            _mail(f.admin, org=f.org, account_id=f.b, imid=imid, minutes_ago=20 + i)
        for i in range(2):
            _mail(f.admin, org=f.org, account_id=f.a, imid=_imid(f, f"solo{i}"))
        async with _as_member(f.p, f.org):
            with _statements("internet_message_id") as listed_reads:
                listed = await messages_mod.list_messages(user=f.me, **_LIST_OFF)
            with _statements("internet_message_id") as second_page_reads:
                await messages_mod.list_messages(
                    user=f.me, **{**_LIST_OFF, "page": 2, "page_size": 3})
            with _statements("internet_message_id") as search_reads:
                found = await search_mod.search_messages(user=f.me, **_SEARCH_OFF)
            with _statements("internet_message_id") as empty_reads:
                empty = await messages_mod.list_messages(
                    user=f.me, **{**_LIST_OFF, "folder": "sent"})
        assert len(listed["emails"]) == 8
        assert sum(1 for v in _also_in(listed).values() if v) == 6
        assert len(listed_reads) == 1, listed_reads
        assert len(second_page_reads) == 1, second_page_reads
        assert len(found["emails"]) == 8 and len(search_reads) == 1, search_reads
        assert empty["emails"] == [] and empty_reads == []


# ── R8: the draft dedupe ─────────────────────────────────────────────────────


def _provider(pid: str) -> SimpleNamespace:
    return SimpleNamespace(create_draft=AsyncMock(return_value=pid),
                           trash_message=AsyncMock())


async def _draft_run(f, account_id: str, mail_id: str, thread: str,
                     provider: Any = None) -> tuple[list[str], Any]:
    """One automatic run of a rule with a DRAFT_EMAIL action, in its own
    block, as the auto-run gives each row."""
    provider = provider or _provider(f"pd-{uuid.uuid4().hex[:8]}")
    async with core._tenant_session() as db:
        done = await actions._apply_rule_actions(
            db, provider, mail_id, f"pm-{mail_id[:8]}",
            [{"type": "DRAFT_EMAIL", "content": "Thanks, we are on it."}],
            {"from": RAVI, "subject": "Quote", "body": "Please send it.",
             "thread_id": thread},
            "", "", f.member, account_id=account_id)
    return done, provider


class _AfterFirstGuardStatement:
    """A real session that runs ``hook`` once, right after the FIRST statement
    of the draft guard, the lock or the check, in the order the code runs
    them. Every other attribute is the real session's."""

    def __init__(self, db: Any, hook: Any) -> None:
        self._db, self._hook, self.fired = db, hook, False

    def __getattr__(self, name: str) -> Any:
        return getattr(self._db, name)

    async def execute(self, stmt: Any, *args: Any, **kwargs: Any) -> Any:
        res = await self._db.execute(stmt, *args, **kwargs)
        sql = str(stmt)
        if not self.fired and ("pg_try_advisory" in sql or "AS answered" in sql):
            self.fired = True
            await self._hook()
        return res


def _skips(caps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [c for c in caps if c.get("event") == "email.draft_skipped_other_mailbox"]


@_DB_GATE
class TestTheDraftDedupe:
    """``email-draft-dedupe``."""

    def _pair(self, f, name: str, *, other: str | None = None,
              this: str | None = None, other_org: str | None = None,
              other_folder: str = "inbox", imid: str | None = "auto"):
        """A copy of one mail in ``other`` (A by default) and in ``this`` (B
        by default). Returns the ids and the thread of each copy."""
        other, this = other or f.a, this or f.b
        mid = _imid(f, name) if imid == "auto" else imid
        s = SimpleNamespace(other=other, this=this,
                            other_thread=f"{name}-o-{f.tag}",
                            this_thread=f"{name}-t-{f.tag}")
        s.copy = _mail(f.admin, org=other_org or f.org, account_id=other, imid=mid,
                       folder=other_folder, thread=s.other_thread, minutes_ago=30)
        s.mail = _mail(f.admin, org=f.org, account_id=this, imid=mid,
                       thread=s.this_thread, minutes_ago=30)
        return s

    async def test_a_draft_in_the_thread_of_the_copy_stops_the_second_draft(
        self, family,
    ):
        f = family
        s = self._pair(f, "draft")
        _mail(f.admin, org=f.org, account_id=f.a, imid=None, folder="drafts",
              thread=s.other_thread, minutes_ago=5)
        async with _as_member(f.p, f.org):
            with structlog.testing.capture_logs() as caps:
                done, prov = await _draft_run(f, f.b, s.mail, s.this_thread)
        assert done == []
        prov.create_draft.assert_not_awaited()
        assert _drafts(f.admin, f.b) == 0
        [skip] = _skips(caps)
        assert skip["reason"] == "answered" and skip["account_id"] == f.b
        # The log names no address (item 3).
        assert not any("@" in str(v) for v in skip.values())

    async def test_a_newer_sent_mail_in_the_thread_of_the_copy_stops_it(self, family):
        f = family
        s = self._pair(f, "sent")
        _mail(f.admin, org=f.org, account_id=f.a, imid=_imid(f, "reply"),
              folder="sent", thread=s.other_thread, minutes_ago=5)
        async with _as_member(f.p, f.org):
            done, prov = await _draft_run(f, f.b, s.mail, s.this_thread)
        assert done == []
        prov.create_draft.assert_not_awaited()

    async def test_an_older_sent_mail_does_not_stop_it(self, family):
        f = family
        s = self._pair(f, "older")
        _mail(f.admin, org=f.org, account_id=f.a, imid=_imid(f, "before"),
              folder="sent", thread=s.other_thread, minutes_ago=90)
        async with _as_member(f.p, f.org):
            done, prov = await _draft_run(f, f.b, s.mail, s.this_thread)
        assert done == ["DRAFT_EMAIL"]
        prov.create_draft.assert_awaited_once()
        assert _drafts(f.admin, f.b) == 1

    async def test_b_drafts_when_a_holds_neither(self, family):
        f = family
        s = self._pair(f, "neither")
        async with _as_member(f.p, f.org):
            done, prov = await _draft_run(f, f.b, s.mail, s.this_thread)
        assert done == ["DRAFT_EMAIL"]
        prov.create_draft.assert_awaited_once()

    @pytest.mark.parametrize("separate", ["other", "this"])
    async def test_b_drafts_when_either_mailbox_is_separate(self, family, separate):
        f = family
        if separate == "other":
            s = self._pair(f, "sep-o", other=f.e)
        else:
            s = self._pair(f, "sep-t", this=f.e)
        _mail(f.admin, org=f.org, account_id=s.other, imid=None, folder="drafts",
              thread=s.other_thread, minutes_ago=5)
        async with _as_member(f.p, f.org):
            with structlog.testing.capture_logs() as caps:
                done, prov = await _draft_run(f, s.this, s.mail, s.this_thread)
        assert done == ["DRAFT_EMAIL"]
        prov.create_draft.assert_awaited_once()
        assert _skips(caps) == []

    async def test_a_copy_in_junk_is_no_copy(self, family):
        f = family
        s = self._pair(f, "junk", other_folder="junk")
        _mail(f.admin, org=f.org, account_id=f.a, imid=None, folder="drafts",
              thread=s.other_thread, minutes_ago=5)
        async with _as_member(f.p, f.org):
            done, prov = await _draft_run(f, f.b, s.mail, s.this_thread)
        assert done == ["DRAFT_EMAIL"]
        prov.create_draft.assert_awaited_once()

    @pytest.mark.parametrize("who", ["another member", "another organization"])
    async def test_a_mailbox_that_does_not_pair_never_stops_it(self, family, who):
        f = family
        if who == "another member":
            s = self._pair(f, "member", other=f.d)
            draft_org = f.org
        else:
            s = self._pair(f, "org", other=f.c, other_org=f.p.org_a)
            draft_org = f.p.org_a
        _mail(f.admin, org=draft_org, account_id=s.other, imid=None,
              folder="drafts", thread=s.other_thread, minutes_ago=5)
        async with _as_member(f.p, f.org):
            done, prov = await _draft_run(f, f.b, s.mail, s.this_thread)
        assert done == ["DRAFT_EMAIL"]
        prov.create_draft.assert_awaited_once()

    @pytest.mark.parametrize("empty", [None, ""])
    async def test_a_mail_with_an_empty_id_pairs_with_nothing(self, family, empty):
        f = family
        s = self._pair(f, f"empty{empty!r}", imid=empty)
        _mail(f.admin, org=f.org, account_id=f.a, imid=None, folder="drafts",
              thread=s.other_thread, minutes_ago=5)
        async with _as_member(f.p, f.org):
            done, prov = await _draft_run(f, f.b, s.mail, s.this_thread)
        assert done == ["DRAFT_EMAIL"]
        prov.create_draft.assert_awaited_once()

    async def test_the_second_mailbox_skips_after_the_first_drafted(self, family):
        """Edge case 11 in sequence: B drafts first, and the later run of A
        sees the local copy of that draft in the thread of B."""
        f = family
        s = self._pair(f, "seq")
        async with _as_member(f.p, f.org):
            first, prov_b = await _draft_run(f, f.b, s.mail, s.this_thread)
            second, prov_a = await _draft_run(f, f.a, s.copy, s.other_thread)
        assert first == ["DRAFT_EMAIL"] and second == []
        prov_b.create_draft.assert_awaited_once()
        prov_a.create_draft.assert_not_awaited()
        assert _drafts(f.admin, f.a, f.b) == 1


@_DB_GATE
class TestTheDraftDedupeRace:
    """``email-draft-dedupe-race``: two overlapping runs for one mail."""

    async def test_two_overlapping_runs_make_one_draft(self, family):
        f = family
        mid = _imid(f, "race")
        thread_a, thread_b = f"race-a-{f.tag}", f"race-b-{f.tag}"
        in_a = _mail(f.admin, org=f.org, account_id=f.a, imid=mid, thread=thread_a)
        in_b = _mail(f.admin, org=f.org, account_id=f.b, imid=mid, thread=thread_b)
        inner: dict[str, Any] = {}
        prov_a = _provider("pd-a")

        async def _b_drafts_while_a_runs(**_kw: Any) -> str:
            # Run 1 (B) holds its block open here, between the lock and the
            # local copy of its draft. Run 2 (A) starts and ends in that gap.
            inner["done"], _ = await _draft_run(f, f.a, in_a, thread_a, prov_a)
            return "pd-b"

        prov_b = SimpleNamespace(
            create_draft=AsyncMock(side_effect=_b_drafts_while_a_runs),
            trash_message=AsyncMock())
        async with _as_member(f.p, f.org):
            with structlog.testing.capture_logs() as caps:
                done_b, _ = await _draft_run(f, f.b, in_b, thread_b, prov_b)
            # After run 1 committed, the lock is free and the check answers.
            async with core._tenant_session() as db:
                later = await identity.draft_skip_in_pair(db, f.a, in_a)
        assert done_b == ["DRAFT_EMAIL"]
        assert inner["done"] == []
        prov_a.create_draft.assert_not_awaited()
        prov_b.create_draft.assert_awaited_once()
        assert [c["reason"] for c in _skips(caps)] == ["busy"]
        assert _drafts(f.admin, f.a, f.b) == 1
        assert later == "answered"
        # The lock is transaction-scoped: after the commit, a connection that
        # is not in the pool of the app takes it at once.
        with f.admin.connect() as c:
            free = c.execute(text(identity._DRAFT_LOCK_SQL),
                             {"aid": f.a, "mid": in_a}).scalar_one()
        assert free is True

    async def test_the_lock_comes_before_the_check_on_a_real_database(self, family):
        """The other run starts and ends between the two statements of this
        run's guard, whatever their order. With the lock first, the other run
        meets the lock. With the check first, this run read a snapshot from
        before the other draft, and then takes a free lock: two drafts."""
        f = family
        mid = _imid(f, "gap")
        thread_a, thread_b = f"gap-a-{f.tag}", f"gap-b-{f.tag}"
        in_a = _mail(f.admin, org=f.org, account_id=f.a, imid=mid, thread=thread_a)
        in_b = _mail(f.admin, org=f.org, account_id=f.b, imid=mid, thread=thread_b)
        other: dict[str, Any] = {}

        async def _b_runs_in_the_gap() -> None:
            other["done"], other["prov"] = await _draft_run(f, f.b, in_b, thread_b)

        prov_a = _provider("pd-a")
        async with _as_member(f.p, f.org), core._tenant_session() as db:
            gap = _AfterFirstGuardStatement(db, _b_runs_in_the_gap)
            done_a = await actions._apply_rule_actions(
                gap, prov_a, in_a, "pm-gap",
                [{"type": "DRAFT_EMAIL", "content": "Thanks, we are on it."}],
                {"from": RAVI, "subject": "Quote", "body": "Please send it.",
                 "thread_id": thread_a},
                "", "", f.member, account_id=f.a)
        assert gap.fired, "the guard ran no statement"
        assert done_a == ["DRAFT_EMAIL"] and other["done"] == []
        other["prov"].create_draft.assert_not_awaited()
        assert _drafts(f.admin, f.a, f.b) == 1

    async def test_a_separate_mailbox_is_never_held_off(self, family):
        """D-EM-30: no pair, so no lock. A pooled run that holds the lock for
        the same Message-ID never stops the draft of a separate mailbox."""
        f = family
        mid = _imid(f, "race-sep")
        thread_a, thread_e = f"race-sa-{f.tag}", f"race-se-{f.tag}"
        in_a = _mail(f.admin, org=f.org, account_id=f.a, imid=mid, thread=thread_a)
        _mail(f.admin, org=f.org, account_id=f.b, imid=mid)
        in_e = _mail(f.admin, org=f.org, account_id=f.e, imid=mid, thread=thread_e)
        inner: dict[str, Any] = {}
        prov_e = _provider("pd-e")

        async def _e_drafts_while_a_runs(**_kw: Any) -> str:
            inner["done"], _ = await _draft_run(f, f.e, in_e, thread_e, prov_e)
            return "pd-a"

        prov_a = SimpleNamespace(
            create_draft=AsyncMock(side_effect=_e_drafts_while_a_runs),
            trash_message=AsyncMock())
        async with _as_member(f.p, f.org):
            with structlog.testing.capture_logs() as caps:
                done_a, _ = await _draft_run(f, f.a, in_a, thread_a, prov_a)
        assert done_a == ["DRAFT_EMAIL"]
        assert inner["done"] == ["DRAFT_EMAIL"]
        prov_e.create_draft.assert_awaited_once()
        assert _skips(caps) == []
