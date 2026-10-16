"""WS-58 MD-3d, R8: the mail kinds of My Day on a REAL Postgres
(``my_day_feed.md`` §5.9 item 12, the MD-3d ticket).

The two new wrappers of ``routes/email/digest.py`` (``awaiting_threads`` and
``promise_tasks``) run their real SQL on the phase-4-promoted catalog of
``test_h3_rls_promotion_rehearsal.py``. Most cases run as the NON-privileged
role with RLS forced (``_as_member``). ``TestTheTenantUnderTheOwnerRole``
runs as the superuser, which bypasses RLS, so only the SQL's own tenant
predicate keeps the other organization out. Production's role ``acb_app``
does not bypass RLS, so that class fences defence in depth (R5). The
hermetic half is ``test_feed_mail_kinds.py``.

The seed, in org B:

* **ME** owns two pooled mailboxes. BOX keeps the default wait of 3 business
  days. BOX10 stores ``follow_up_awaiting_days = 10``. BOX holds one AWAITING
  thread in the window, one younger than the cutoff, one older than 30 days,
  one whose last message is in the trash, one NEEDS_REPLY thread, and 16
  AWAITING threads older than 30 days, more than the read's limit. ME holds
  four tasks captured from BOX: a promise due in 2 days, a promise past due,
  a promise due in 10 days, an undated promise, and a captured task that is
  not a promise.
* **B** is another member with a mailbox and a thread of their own.

⚠️ It SKIPS without ``TENANT_LADDER_DATABASE_URL``, and a skip is not a pass.
"""

from __future__ import annotations

import contextlib
import json
import uuid
from datetime import datetime, timedelta

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth.permissions import EffectiveAccess
from acb_auth.roles import UserContext, UserRole
from fastapi import HTTPException
from gateway.routes.email import digest
from gateway.routes.shell import feed
from gateway.routes.shell import needs as shell
from sqlalchemy import text

from tests.unit import _digest_before_md3d as before

# The catalog, the app role and the binding, shared rather than copied.
# Fixtures are used by name, so the import is load-bearing.
from tests.unit.test_email_keep_separate import (  # noqa: F401
    _DB_GATE,
    _as_member,
    app_engine,
    promoted,
)
from tests.unit.test_feed_task_kinds_r8 import _as_owner
from tests.unit.test_shell_needs_r8 import NOW, _Frozen, _project, _task

pytestmark = _DB_GATE

TAG = uuid.uuid4().hex[:8]
ME = f"mail-me-{TAG}@md3d.test"
B = f"mail-b-{TAG}@md3d.test"
TWO = f"mail-two-{TAG}@md3d.test"
DANA = f"dana-{TAG}@client.test"


@pytest.fixture(autouse=True)
def _one_instant(monkeypatch):
    """The feed's clock and the lens read's "today" read ``NOW``."""
    from gateway.routes.projects import personal

    monkeypatch.setattr(shell, "_now", lambda: NOW)
    monkeypatch.setattr(personal, "datetime", _Frozen)


def _member(email: str, *features: str) -> UserContext:
    return UserContext(
        email=email, role=UserRole.EMPLOYEE,
        access=EffectiveAccess(role_granted=frozenset(
            f"feature:{f}" for f in (features or ("projects", "email", "tasks")))),
    )


def _account(c, *, org: str, owner: str, wait_days: int | None = None) -> str:
    box = str(c.execute(text(
        "INSERT INTO email_accounts (user_id, provider, email_address, "
        "credentials_encrypted, organization_id, in_all_inboxes, is_default) "
        "VALUES (:u, 'microsoft', :m, 'x', CAST(:o AS uuid), true, false) "
        "RETURNING id"),
        {"u": owner, "m": f"box-{uuid.uuid4().hex[:8]}@md3d.test", "o": org}).scalar_one())
    if wait_days is not None:
        c.execute(text(
            "INSERT INTO email_assistant_settings (account_id, follow_up_awaiting_days, "
            "organization_id) VALUES (CAST(:a AS uuid), :d, CAST(:o AS uuid))"),
            {"a": box, "d": wait_days, "o": org})
    return box


def _address(c, box: str) -> str:
    return str(c.execute(text("SELECT email_address FROM email_accounts "
                              "WHERE id = CAST(:a AS uuid)"), {"a": box}).scalar_one())


def _thread(c, *, org: str, box: str, subject: str, at: datetime,
            status: str = "AWAITING", folder: str = "sent") -> tuple[str, str]:
    """One thread whose last message is ours (a sent mail to DANA), and its
    Reply Zero row."""
    thread = f"th-{uuid.uuid4().hex[:10]}"
    message = str(c.execute(text(
        "INSERT INTO email_messages (account_id, provider_message_id, thread_id, folder, "
        "from_address, to_addresses, subject, body_text, received_at, is_read, "
        "organization_id) VALUES (CAST(:a AS uuid), :p, :t, :f, CAST(:frm AS jsonb), "
        "CAST(:to AS jsonb), :s, 'b', :r, true, CAST(:o AS uuid)) RETURNING id"),
        {"a": box, "p": f"pm-{uuid.uuid4().hex[:10]}", "t": thread, "f": folder,
         "frm": json.dumps({"name": "Me", "email": _address(c, box)}),
         "to": json.dumps([{"name": "Dana", "email": DANA}]), "s": subject, "r": at,
         "o": org}).scalar_one())
    c.execute(text(
        "INSERT INTO email_thread_status (account_id, thread_id, status, last_message_id, "
        "last_message_at, organization_id) VALUES (CAST(:a AS uuid), :t, :st, "
        "CAST(:m AS uuid), :at, CAST(:o AS uuid))"),
        {"a": box, "t": thread, "st": status, "m": message, "at": at, "o": org})
    return thread, message


def _captured(c, *, org: str, project: str, lane: str, owner: str, box: str, title: str,
              due: datetime | None, number: int, promise: bool = True,
              thread: str | None = None) -> str:
    """A task captured from ``box``, assigned to ``owner``."""
    tid = _task(c, org=org, project=project, lane=lane, title=title, due=due,
                assignee=owner, number=number)
    origin = {"kind": "email", "account_id": box}
    if promise:
        origin["commitment"] = True
    if thread:
        origin["thread_id"] = thread
    c.execute(text("UPDATE pm_tasks SET origin = CAST(:o AS jsonb) WHERE id = CAST(:t AS uuid)"),
              {"o": json.dumps(origin), "t": tid})
    return tid


@pytest.fixture(scope="module")
def seeded(promoted):  # noqa: F811
    """The rows, seeded as the superuser, so the wrappers' reads are the
    first the app role makes."""
    p = promoted
    org = p.org_b
    s: dict[str, str] = {}
    old = NOW - timedelta(days=31)
    with p.admin_engine.begin() as c:
        for email in (ME, B):
            c.execute(text(
                "INSERT INTO app_user (email, display_name, role, status, organization_id) "
                "VALUES (:e, :e, 'employee', 'active', CAST(:o AS uuid))"), {"e": email, "o": org})
        s["box"] = box = _account(c, org=org, owner=ME)
        s["box10"] = box10 = _account(c, org=org, owner=ME, wait_days=10)
        s["box_b"] = _account(c, org=org, owner=B)
        s["wait"], s["wait_msg"] = _thread(c, org=org, box=box, subject="Quote for 40 units",
                                           at=NOW - timedelta(days=6))
        s["young"], _ = _thread(c, org=org, box=box, subject="Sent an hour ago",
                                at=NOW - timedelta(hours=1))
        s["stale"], _ = _thread(c, org=org, box=box, subject="Sent last month", at=old)
        s["trash"], _ = _thread(c, org=org, box=box, subject="Thrown away",
                                at=NOW - timedelta(days=6), folder="trash")
        s["needs"], _ = _thread(c, org=org, box=box, subject="They asked me",
                                at=NOW - timedelta(days=6), status="NEEDS_REPLY")
        for n in range(16):
            _thread(c, org=org, box=box, subject=f"Ancient {n}", at=old - timedelta(days=n))
        s["box10_young"], _ = _thread(c, org=org, box=box10, subject="Ten-day wait, 6 days",
                                      at=NOW - timedelta(days=6))
        s["box10_old"], _ = _thread(c, org=org, box=box10, subject="Ten-day wait, 20 days",
                                    at=NOW - timedelta(days=20))
        s["theirs"], _ = _thread(c, org=org, box=s["box_b"], subject="Secret of B",
                                 at=NOW - timedelta(days=6))

        project, lane = _project(c, org=org, name=f"Mail {TAG}", grant_org=True)
        n = iter(range(900, 1000))

        def cap(title, due, **kw):
            return _captured(c, org=org, project=project, lane=lane, owner=ME, box=box,
                             title=title, due=due, number=next(n), **kw)

        s["p_due"] = cap("Send the drawings", NOW + timedelta(days=2), thread=s["wait"])
        s["p_late"] = cap("Call back Dana", NOW - timedelta(days=1))
        s["p_far"] = cap("Visit the plant", NOW + timedelta(days=10))
        s["p_none"] = cap("Some day promise", None)
        s["plain"] = cap("Captured, no promise", NOW + timedelta(days=1), promise=False)
    return s


def _list(monkeypatch, value: str) -> None:
    from acb_common.settings import get_settings

    monkeypatch.setattr(get_settings(), "my_day_providers", value)


def _roomy(monkeypatch) -> None:
    """Room for a cold database. The `sources` check proves each provider ran."""
    monkeypatch.setattr(shell, "PROVIDER_TIMEOUT_S", 30.0)
    monkeypatch.setattr(shell, "TOTAL_BUDGET_S", 90.0)
    monkeypatch.setattr(shell, "MAILBOX_TIMEOUT_S", 30.0)
    monkeypatch.setattr(feed, "FEED_PROVIDER_TIMEOUT_S", 30.0)
    monkeypatch.setattr(feed, "FEED_BUDGET_S", 90.0)
    monkeypatch.setattr(feed, "FEED_TOTAL_S", 90.0)


async def _feed(p, monkeypatch, user: UserContext, listed: str = "email_followups") -> dict:
    _roomy(monkeypatch)
    _list(monkeypatch, listed)
    async with _as_member(p, p.org_b):
        return await feed.shell_feed(limit=80, user=user)


def _by_kind(answer: dict, kind: str) -> dict[str, dict]:
    return {c["signal_id"]: c for c in answer["cards"]
            if c["kind"] == kind or kind in c["folded"]}


class TestTheKindsOnARealDatabase:
    async def test_each_kind_gives_its_card_and_no_other(self, promoted, seeded, monkeypatch):  # noqa: F811
        """Done-whens 2, 4, 6 and 7."""
        answer = await _feed(promoted, monkeypatch, _member(ME))
        assert answer["sources"]["email_followups"] == "ok"
        assert answer["sources"]["email"] == "ok"
        s = seeded
        waits = _by_kind(answer, "no_answer")
        assert set(waits) == {f"email:{s['box']}:{s['wait']}",
                              f"email:{s['box10']}:{s['box10_old']}"}
        c = waits[f"email:{s['box']}:{s['wait']}"]
        assert (c["group"], c["why_key"], c["source"]) == ("needs_you", "no_answer",
                                                          "email_followups")
        assert c["why"].endswith(". Dana has not answered.")
        assert c["acts"][0]["href"] == f"/email?email={s['wait_msg']}&account={s['box']}"
        promises = _by_kind(answer, "promise_due")
        assert set(promises) == {f"tasks:{s['p_due']}", f"tasks:{s['p_late']}"}, "done-when 4"
        c = promises[f"tasks:{s['p_due']}"]
        assert (c["kind"], c["why_key"], c["source"]) == ("promise_due", "promised",
                                                         "email_followups")
        assert [a["id"] for a in c["acts"]] == ["done", "open-thread"]
        assert c["acts"][1]["href"] == f"/email?email={s['wait_msg']}&account={s['box']}"
        # The past-due promise folds into the task's overdue card, and adds 10.
        late = promises[f"tasks:{s['p_late']}"]
        assert (late["kind"], late["folded"]) == ("overdue", ["promise_due"])
        assert late["why"].endswith(" You promised this by mail.")

    async def test_the_window_holds_in_sql(self, promoted, seeded, monkeypatch):  # noqa: F811
        """16 threads older than 30 days come first in the oldest-first list.
        Only a bound in SQL keeps the one inside the window."""
        from gateway.routes.email.automation.followups import _FOLLOW_UP_MAX_AGE_DAYS

        async with _as_member(promoted, promoted.org_b):
            rows = await digest.awaiting_threads(
                _member(ME), seeded["box"], shell.PER_APP + 1,
                after=NOW - timedelta(days=_FOLLOW_UP_MAX_AGE_DAYS))
        assert [r["thread_id"] for r in rows] == [seeded["wait"]]
        assert rows[0]["who"] == "Dana"

    async def test_the_mailbox_rule_sets_the_cutoff(self, promoted, seeded, monkeypatch):  # noqa: F811
        async with _as_member(promoted, promoted.org_b):
            rows = await digest.awaiting_threads(
                _member(ME), seeded["box10"], 16, after=NOW - timedelta(days=30))
        assert [r["thread_id"] for r in rows] == [seeded["box10_old"]]

    async def test_only_a_dated_promise_in_the_horizon(self, promoted, seeded, monkeypatch):  # noqa: F811
        async with _as_member(promoted, promoted.org_b):
            rows = await digest.promise_tasks(_member(ME), seeded["box"], horizon_days=3,
                                              limit=16, now=NOW)
        assert [r["task_id"] for r in rows] == [seeded["p_late"], seeded["p_due"]]
        assert rows[1]["message_id"] == seeded["wait_msg"]

    async def test_a_member_without_projects_gets_no_promise(self, promoted, seeded, monkeypatch):  # noqa: F811
        answer = await _feed(promoted, monkeypatch, _member(ME, "email", "tasks"))
        assert answer["sources"]["email_followups"] == "ok"
        assert not _by_kind(answer, "promise_due")
        assert _by_kind(answer, "no_answer")

    async def test_with_the_list_empty_nothing_runs(self, promoted, seeded, monkeypatch):  # noqa: F811
        """Done-when 1, on the real database."""
        dark = await _feed(promoted, monkeypatch, _member(ME), listed="")
        assert "email_followups" not in dark["sources"]
        assert all("source" not in c for c in dark["cards"])
        assert not {c["kind"] for c in dark["cards"]} & {"promise_due", "no_answer"}
        assert not any({"promise_due", "no_answer"} & set(c["folded"]) for c in dark["cards"])


class TestAnotherMembersMailbox:
    """Done-when 3."""

    @pytest.mark.parametrize("wrapper", ["awaiting_threads", "promise_tasks"])
    async def test_the_wrapper_of_a_raises_404_for_the_mailbox_of_b(
        self, promoted, seeded, wrapper,  # noqa: F811
    ):
        async with _as_member(promoted, promoted.org_b):
            with pytest.raises(HTTPException) as err:
                if wrapper == "awaiting_threads":
                    await digest.awaiting_threads(_member(ME), seeded["box_b"], 16,
                                                  after=NOW - timedelta(days=30))
                else:
                    await digest.promise_tasks(_member(ME), seeded["box_b"], horizon_days=3,
                                               limit=16, now=NOW)
        assert err.value.status_code == 404

    async def test_the_feed_of_a_shows_no_thread_of_b(self, promoted, seeded, monkeypatch):  # noqa: F811
        answer = await _feed(promoted, monkeypatch, _member(ME))
        assert "Secret of B" not in json.dumps(answer, default=str)
        assert seeded["box_b"] not in json.dumps(answer, default=str)
        # Non-vacuity: B's own feed holds it.
        theirs = await _feed(promoted, monkeypatch, _member(B))
        assert f"email:{seeded['box_b']}:{seeded['theirs']}" in _by_kind(theirs, "no_answer")


class _DivideByZero:
    """The one task seam, with a read that fails IN the database."""

    async def items_by_origin(self, db, uid, key, value, **kw):
        await db.execute(text("SELECT 1 / 0"))
        return []


class TestADatabaseErrorIsAFailure:
    async def test_the_promise_read_fails_the_source(self, promoted, seeded, monkeypatch):  # noqa: F811
        """Done-when 5: a real database error, never an empty ``ok``."""
        from gateway.routes.tasks import item_source as seam

        monkeypatch.setattr(seam, "item_source", lambda: _DivideByZero())
        answer = await _feed(promoted, monkeypatch, _member(ME))
        assert answer["sources"]["email_followups"] == "failed"
        assert not _by_kind(answer, "no_answer") and not _by_kind(answer, "promise_due")

    async def test_the_digest_still_swallows_it(self, promoted, seeded, monkeypatch):  # noqa: F811
        from acb_common.db import tenant_session
        from gateway.routes.tasks import item_source as seam

        monkeypatch.setattr(seam, "item_source", lambda: _DivideByZero())
        async with _as_member(promoted, promoted.org_b), tenant_session() as db:
            assert await digest._digest_commitments(db, seeded["box"]) == []
            # The session stays usable after the rollback.
            assert (await db.execute(text("SELECT 1"))).scalar_one() == 1


class TestTheSessions:
    async def test_one_plus_two_for_each_mailbox(self, promoted, seeded, monkeypatch):  # noqa: F811
        """Done-when 10, with a counting wrapper of the one seam."""
        from gateway.routes.email.transport import accounts

        opened: list[str] = []

        def _counting(name, real):
            @contextlib.asynccontextmanager
            async def _session():
                opened.append(name)
                async with real() as db:
                    yield db
            return _session

        monkeypatch.setattr(accounts, "_tenant_session",
                            _counting("accounts", accounts._tenant_session))
        monkeypatch.setattr(digest, "_tenant_session",
                            _counting("digest", digest._tenant_session))
        async with _as_member(promoted, promoted.org_b):
            found = await feed._email_followups(_member(ME))
        assert opened == ["accounts", "digest", "digest", "digest", "digest"]
        assert len(opened) <= 1 + 2 * 2
        assert {s.kind for s in found.signals()} == {"no_answer", "promise_due"}


# ── A role that bypasses RLS: each statement still names the tenant ─────────
#
# Production connects as `acb_app`: no superuser, no BYPASSRLS, not the table
# owner, and FORCE RLS is on (measured on the box, 2026-10-16). So these
# predicates are defence in depth (R5). They hold only if a path ever uses a
# role that bypasses RLS, and this class proves that they hold then.


@pytest.fixture(scope="module")
def two_orgs(promoted, seeded):  # noqa: F811
    """TWO owns a mailbox in org A and one in org B, each with an AWAITING
    thread in the window and a promise. Org A's mailbox stores a wait of 1
    day, so an org B read that took it would see a different cutoff."""
    p = promoted
    a, b = p.org_a, p.org_b
    s: dict[str, str] = {}
    with p.admin_engine.begin() as c:
        # One app_user row, in org B: the address is unique. Org A holds
        # TWO's other mailbox and a promise of it, which a read past RLS
        # with no predicate would leak.
        c.execute(text(
            "INSERT INTO app_user (email, display_name, role, status, organization_id) "
            "VALUES (:e, :e, 'employee', 'active', CAST(:o AS uuid))"), {"e": TWO, "o": b})
        s["box_a"] = _account(c, org=a, owner=TWO, wait_days=1)
        s["box_b"] = _account(c, org=b, owner=TWO)
        s["th_a"], _ = _thread(c, org=a, box=s["box_a"], subject="ORGA thread",
                               at=NOW - timedelta(days=6))
        s["th_b"], _ = _thread(c, org=b, box=s["box_b"], subject="ORGB thread",
                               at=NOW - timedelta(days=6))
        proj_a, lane_a = _project(c, org=a, name=f"OrgA mail {TAG}", grant_org=True)
        proj_b, lane_b = _project(c, org=b, name=f"OrgB mail {TAG}", grant_org=True)
        s["p_a"] = _captured(c, org=a, project=proj_a, lane=lane_a, owner=TWO, box=s["box_a"],
                             title="ORGA promise", due=NOW + timedelta(days=2), number=1001,
                             thread=s["th_a"])
        s["p_b"] = _captured(c, org=b, project=proj_b, lane=lane_b, owner=TWO, box=s["box_b"],
                             title="ORGB promise", due=NOW + timedelta(days=2), number=1002,
                             thread=s["th_b"])
    return s


class TestTheTenantUnderTheOwnerRole:
    async def test_the_provider_reads_only_this_orgs_mailbox(
        self, promoted, two_orgs, monkeypatch,  # noqa: F811
    ):
        _roomy(monkeypatch)
        async with _as_owner(promoted, promoted.org_b):
            found = await feed._email_followups(_member(TWO))
        got = json.dumps([dataclass_dict(s) for s in found.signals()], default=str)
        assert "ORGB thread" in got and "ORGB promise" in got
        assert "ORGA" not in got and two_orgs["box_a"] not in got
        assert found.capped is False  # the org A mailbox is no failure

    @pytest.mark.parametrize("wrapper", ["awaiting_threads", "promise_tasks"])
    async def test_the_owner_check_names_the_tenant(
        self, promoted, two_orgs, wrapper,  # noqa: F811
    ):
        async with _as_owner(promoted, promoted.org_b):
            with pytest.raises(HTTPException) as err:
                if wrapper == "awaiting_threads":
                    await digest.awaiting_threads(_member(TWO), two_orgs["box_a"], 16,
                                                  after=NOW - timedelta(days=30))
                else:
                    await digest.promise_tasks(_member(TWO), two_orgs["box_a"],
                                               horizon_days=3, limit=16, now=NOW)
        assert err.value.status_code == 404

    async def test_each_statement_names_the_tenant(self, promoted, two_orgs, monkeypatch):  # noqa: F811
        """Each read of the wrappers, alone, on org A's mailbox, bound to
        org B, as a role that bypasses RLS. With no predicate, each would
        answer."""
        from acb_common.db import tenant_session
        from gateway.routes.email.automation import replyzero

        asked: list[float] = []
        real = replyzero._business_days_cutoff

        def _cutoff(n):
            asked.append(n)
            return real(n)

        monkeypatch.setattr(replyzero, "_business_days_cutoff", _cutoff)
        box_a = two_orgs["box_a"]
        async with _as_owner(promoted, promoted.org_b), tenant_session() as db:
            # Non-vacuity: the old statement reads org A's thread here.
            assert await digest._digest_thread_list(db, box_a, "AWAITING", 16)
            assert await digest._digest_thread_list(
                db, box_a, "AWAITING", 16, this_tenant=True) == []
            # Org A's stored wait is 1 day. Org B reads no row: the default.
            await digest._awaiting_cutoff(db, box_a)
            assert asked == [digest.NO_ANSWER_DEFAULT_DAYS]
            _at, picked, latest = await digest._commitments_core(
                db, box_a, horizon_days=3, limit=16, commitment=True, this_tenant=True,
                now=NOW)
            assert (picked, latest) == ([], {})

    async def test_the_latest_message_read_names_the_tenant(self, promoted, two_orgs):  # noqa: F811
        """The core's last read, alone: org A's thread, bound to org B."""
        from acb_common.db import tenant_session

        async with _as_owner(promoted, promoted.org_b), tenant_session() as db:
            params = {"aid": two_orgs["box_a"], "tids": [two_orgs["th_a"]]}
            assert (await db.execute(text(digest._latest_message_sql()), params)).fetchall()
            assert (await db.execute(text(digest._latest_message_sql(True)),
                                     params)).fetchall() == []


def dataclass_dict(signal) -> dict:
    import dataclasses

    return dataclasses.asdict(signal)


# ── done-when 8: the digest did not change, on a real mailbox ───────────────


class TestTheDigestOnARealDatabase:
    async def test_the_output_is_the_old_one(self, promoted, seeded, monkeypatch):  # noqa: F811
        from acb_common.db import tenant_session

        async def _all(db) -> list[dict]:
            return [await digest._generate_digest(db, seeded["box"], days, None, full=full)
                    for days, full in ((1, False), (7, False), (1, True))]

        async with _as_member(promoted, promoted.org_b), tenant_session() as db:
            new = await _all(db)
            monkeypatch.setattr(digest, "_digest_thread_list", before.old_digest_thread_list)
            monkeypatch.setattr(digest, "_digest_commitments", before.old_digest_commitments)
            old = await _all(db)
        assert new == old
        # Non-vacuity: the lists the change touched hold rows, and the digest
        # still reads every captured task, the plain one too.
        assert new[0]["awaiting"] and new[2]["backlog"]
        titles = {c["title"] for c in new[2]["commitments"]}
        assert {"Captured, no promise", "Some day promise", "Send the drawings"} <= titles
