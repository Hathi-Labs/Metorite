"""R8 — the reads of the write check, on a REAL Postgres (LOOP-18).

``proactive_loop.md`` §4.9. ``SqlReader`` reads as the member, through the one
mailbox seam and the one task visibility clause, as the NON-privileged app role
under FORCE ROW LEVEL SECURITY.

* **Mail.** A member finds their own sent mail to an address. A colleague in the
  same org with the same recipient and the same subject is never found, and
  another org never is. A reply's own thread is left out. Mail older than the
  window is not read. A newer message of the thread comes from other people, in
  the member's own mailbox only.
* **Tasks.** A task that the member cannot open gives ``None`` (no trace). A
  task in a lane of category ``done`` reads as done. The nudge stamp is the
  member's own. The open tasks of a project leave out the done ones, and a
  project of another org or one the member cannot open gives nothing.
* **The whole check.** ``check_write`` with the real reader finds the member's
  own resend, and finds nothing of a colleague.

⚠️ It SKIPS without ``TENANT_LADDER_DATABASE_URL``, and a skip is not a pass.
Run (real Postgres)::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_write_check_r8.py -v -rs
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth.permissions import EffectiveAccess
from acb_auth.roles import UserContext, UserRole
from sqlalchemy import text

from tests.unit.test_email_keep_separate import (  # noqa: F401  (fixtures by name)
    _DB_GATE,
    _as_member,
    app_engine,
    promoted,
)
from tests.unit.test_shell_needs_r8 import _lane, _project, _task

pytestmark = _DB_GATE

NOW = datetime.now(UTC)


def _user(email: str, org: str) -> UserContext:
    return UserContext(email=email, role=UserRole.EMPLOYEE, organization_id=org,
                       access=EffectiveAccess(role_granted=frozenset(
                           {"feature:projects", "feature:tasks", "feature:email"})))


@pytest.fixture
def world(promoted, app_engine):  # noqa: F811 — fixtures
    p = promoted
    tag = uuid.uuid4().hex[:8]
    m = SimpleNamespace(**{k: f"{k}-{tag}@wc18.test" for k in ("me", "col", "zed")})
    ids: dict[str, Any] = {"tasks": [], "projects": []}
    seq = iter(range(500, 5000))
    with p.admin_engine.begin() as c:
        for email, org in ((m.me, p.org_a), (m.col, p.org_a), (m.zed, p.org_b)):
            c.execute(text(
                "INSERT INTO app_user (email, display_name, role, status, organization_id) "
                "VALUES (:e, :e, 'employee', 'active', CAST(:o AS uuid))"), {"e": email, "o": org})

        def box(owner: str, org: str) -> tuple[str, str]:
            address = f"box-{uuid.uuid4().hex[:6]}@wc18.test"
            bid = str(c.execute(text(
                "INSERT INTO email_accounts (user_id, provider, email_address, "
                "credentials_encrypted, initial_sync_done, organization_id) "
                "VALUES (:u, 'microsoft', :a, 'x', true, CAST(:o AS uuid)) RETURNING id"),
                {"u": owner, "a": address, "o": org}).scalar_one())
            return bid, address

        def mail(account: str, org: str, *, folder: str, thread: str, subject: str,
                 at: datetime, frm: str = "ravi@contoso.test", to: str = "mahesh@vendor.test",
                 files: tuple[str, ...] = ()) -> str:
            mid = str(c.execute(text(
                "INSERT INTO email_messages (account_id, provider_message_id, thread_id, "
                "folder, from_address, to_addresses, subject, body_text, snippet, received_at, "
                "has_attachments, organization_id) VALUES (CAST(:a AS uuid), :pid, :th, :f, "
                "jsonb_build_object('name', 'X', 'email', CAST(:fr AS text)), "
                "jsonb_build_array(jsonb_build_object('name', 'T', 'email', CAST(:t AS text))), "
                ":s, 'body', 'body', :at, CAST(:h AS boolean), CAST(:o AS uuid)) RETURNING id"),
                {"a": account, "pid": f"wc18-{uuid.uuid4().hex[:8]}", "th": thread, "f": folder,
                 "fr": frm, "t": to, "s": subject, "at": at, "h": bool(files), "o": org}
            ).scalar_one())
            for f in files:
                c.execute(text(
                    "INSERT INTO email_attachments (message_id, filename, provider_attachment_id, "
                    "organization_id) VALUES (CAST(:m AS uuid), :f, :p, CAST(:o AS uuid))"),
                    {"m": mid, "f": f, "p": uuid.uuid4().hex[:8], "o": org})
            return mid

        a_me, addr_me = box(m.me, p.org_a)
        a_col, addr_col = box(m.col, p.org_a)
        a_zed, _ = box(m.zed, p.org_b)
        day = timedelta(days=1)
        ids["mine"] = mail(a_me, p.org_a, folder="sent", thread="th-mine", subject="Licence terms",
                           at=NOW - 3 * day, frm=addr_me, files=("NDA_Acme_2026-11-12.pdf",))
        ids["old"] = mail(a_me, p.org_a, folder="sent", thread="th-old", subject="Licence terms",
                          at=NOW - 60 * day, frm=addr_me)
        ids["other_addr"] = mail(a_me, p.org_a, folder="sent", thread="th-zoe", subject="Licence terms",
                                 at=NOW - 2 * day, frm=addr_me, to="zoe@other.test")
        ids["col"] = mail(a_col, p.org_a, folder="sent", thread="th-col", subject="Licence terms",
                          at=NOW - 3 * day, frm=addr_col, files=("NDA_Acme.pdf",))
        ids["zed"] = mail(a_zed, p.org_b, folder="sent", thread="th-zed", subject="Licence terms",
                          at=NOW - 3 * day, frm="zed@wc18.test")
        # A thread of the member: an inbox message, then newer ones.
        ids["root"] = mail(a_me, p.org_a, folder="inbox", thread="th-live", subject="Terms",
                           at=NOW - 5 * day, to=addr_me)
        ids["newer"] = mail(a_me, p.org_a, folder="inbox", thread="th-live", subject="Re: Terms",
                            at=NOW - 1 * day, to=addr_me)
        ids["own_reply"] = mail(a_me, p.org_a, folder="sent", thread="th-live", subject="Re: Terms",
                                at=NOW - 2 * day, frm=addr_me, to="ravi@contoso.test")
        # The same thread id in the colleague's mailbox. It is not the member's.
        ids["col_root"] = mail(a_col, p.org_a, folder="inbox", thread="th-col-live", subject="Plans",
                               at=NOW - 5 * day, to=addr_col)
        ids["col_newer"] = mail(a_col, p.org_a, folder="inbox", thread="th-col-live",
                                subject="Re: Plans", at=NOW - 1 * day, to=addr_col)

        # Tasks. P_open is open to the org. P_closed has no grant for the member.
        proj_open, lane_todo = _project(c, org=p.org_a, name=f"Open {tag}", grant_org=True)
        lane_done = _lane(c, project=proj_open, name="Done", category="done", position=2)
        proj_closed, lane_c = _project(c, org=p.org_a, name=f"Closed {tag}", grant_org=False)
        proj_zed, lane_z = _project(c, org=p.org_b, name=f"Zed {tag}", grant_org=True)
        ids["projects"] = [proj_open, proj_closed, proj_zed]
        ids["proj_open"], ids["proj_closed"], ids["proj_zed"] = proj_open, proj_closed, proj_zed
        due = NOW + timedelta(days=3)
        ids["t_open"] = _task(c, org=p.org_a, project=proj_open, lane=lane_todo,
                              title="Fix the login page", due=due, assignee=m.col, number=next(seq))
        ids["t_done"] = _task(c, org=p.org_a, project=proj_open, lane=lane_done,
                              title="Fix the login page", due=due, assignee=m.col, number=next(seq))
        ids["t_other"] = _task(c, org=p.org_a, project=proj_open, lane=lane_todo,
                               title="Order paper", due=due, assignee=None, number=next(seq))
        ids["t_hidden"] = _task(c, org=p.org_a, project=proj_closed, lane=lane_c,
                                title="Fix the login page", due=due, assignee=None, number=next(seq))
        ids["t_zed"] = _task(c, org=p.org_b, project=proj_zed, lane=lane_z,
                             title="Fix the login page", due=due, assignee=None, number=next(seq))
        c.execute(text(
            "INSERT INTO pm_task_personal (task_id, member_email, disposition, last_nudged_at, "
            "organization_id) VALUES (CAST(:t AS uuid), :e, 'WAITING', now() - interval '2 hours', "
            "CAST(:o AS uuid))"), {"t": ids["t_open"], "e": m.me, "o": p.org_a})
        ids["tasks"] = [ids[k] for k in ("t_open", "t_done", "t_other", "t_hidden", "t_zed")]
    try:
        yield SimpleNamespace(p=p, m=m, ids=ids)
    finally:
        with p.admin_engine.begin() as c:
            c.execute(text("DELETE FROM pm_task_personal WHERE task_id = ANY(CAST(:t AS uuid[]))"),
                      {"t": ids["tasks"]})
            c.execute(text("DELETE FROM pm_task_assignees WHERE task_id = ANY(CAST(:t AS uuid[]))"),
                      {"t": ids["tasks"]})
            c.execute(text("DELETE FROM pm_tasks WHERE id = ANY(CAST(:t AS uuid[]))"), {"t": ids["tasks"]})
            c.execute(text("DELETE FROM pm_project_grants WHERE project_id = ANY(CAST(:p AS uuid[]))"),
                      {"p": ids["projects"]})
            c.execute(text("DELETE FROM pm_task_statuses WHERE project_id = ANY(CAST(:p AS uuid[]))"),
                      {"p": ids["projects"]})
            c.execute(text("DELETE FROM pm_projects WHERE id = ANY(CAST(:p AS uuid[]))"),
                      {"p": ids["projects"]})
            c.execute(text("DELETE FROM email_attachments WHERE message_id IN (SELECT id FROM "
                           "email_messages WHERE account_id IN (SELECT id FROM email_accounts "
                           "WHERE user_id = ANY(:u)))"), {"u": [m.me, m.col, m.zed]})
            c.execute(text("DELETE FROM email_messages WHERE account_id IN (SELECT id FROM "
                           "email_accounts WHERE user_id = ANY(:u))"), {"u": [m.me, m.col, m.zed]})
            c.execute(text("DELETE FROM email_accounts WHERE user_id = ANY(:u)"),
                      {"u": [m.me, m.col, m.zed]})
            c.execute(text("DELETE FROM app_user WHERE email = ANY(:u)"), {"u": [m.me, m.col, m.zed]})


def _reader(w: Any, who: str, org: str):
    from gateway.write_check_reader import SqlReader

    return SqlReader(_user(who, org))


def _ids(rows: list[dict[str, Any]]) -> set[str]:
    return {r["id"] for r in rows}


# ── Mail ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_r8_a_member_finds_their_own_sent_mail_and_never_a_colleagues(world) -> None:
    w, since = world, NOW - timedelta(days=30)
    async with _as_member(w.p, w.p.org_a):
        mine = await _reader(w, w.m.me, w.p.org_a).sent_mail(["mahesh@vendor.test"], since, None)
        theirs = await _reader(w, w.m.col, w.p.org_a).sent_mail(["mahesh@vendor.test"], since, None)
    assert _ids(mine) == {w.ids["mine"]}  # not old, not another address, not the colleague's
    assert _ids(theirs) == {w.ids["col"]}
    assert mine[0]["file_stems"] == frozenset({"nda", "acme"})
    assert mine[0]["to"] == ["mahesh@vendor.test"]


@pytest.mark.asyncio
async def test_r8_another_org_reads_nothing_of_org_a(world) -> None:
    w, since = world, NOW - timedelta(days=30)
    async with _as_member(w.p, w.p.org_b):
        zed = await _reader(w, w.m.zed, w.p.org_b).sent_mail(["mahesh@vendor.test"], since, None)
        wrong = await _reader(w, w.m.me, w.p.org_b).sent_mail(["mahesh@vendor.test"], since, None)
    assert _ids(zed) == {w.ids["zed"]}
    assert wrong == []  # the address of an org A member under the tenant of org B


@pytest.mark.asyncio
async def test_r8_the_thread_of_a_reply_is_left_out(world) -> None:
    w, since = world, NOW - timedelta(days=30)
    async with _as_member(w.p, w.p.org_a):
        out = await _reader(w, w.m.me, w.p.org_a).sent_mail(
            ["mahesh@vendor.test"], since, w.ids["mine"])
    assert out == []  # the only match sits in the thread that the reply answers


@pytest.mark.asyncio
async def test_r8_a_newer_message_of_the_thread_comes_from_others_in_the_members_mailbox(world) -> None:
    w = world
    async with _as_member(w.p, w.p.org_a):
        out = await _reader(w, w.m.me, w.p.org_a).thread_newer(w.ids["root"])
        col_as_me = await _reader(w, w.m.me, w.p.org_a).thread_newer(w.ids["col_root"])
        col = await _reader(w, w.m.col, w.p.org_a).thread_newer(w.ids["col_root"])
        junk = await _reader(w, w.m.me, w.p.org_a).thread_newer("not-a-uuid")
    assert _ids(out) == {w.ids["newer"]}  # not the member's own reply
    assert col_as_me == [] and junk == []  # an email of a colleague's mailbox is not readable
    assert _ids(col) == {w.ids["col_newer"]}


# ── Tasks ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_r8_task_state_follows_the_lane_the_overlay_and_the_visibility(world) -> None:
    w = world
    async with _as_member(w.p, w.p.org_a):
        r = _reader(w, w.m.me, w.p.org_a)
        opened = await r.task_state(w.ids["t_open"])
        done = await r.task_state(w.ids["t_done"])
        hidden = await r.task_state(w.ids["t_hidden"])
        other_org = await r.task_state(w.ids["t_zed"])
        junk = await r.task_state("nope")
    assert opened["done"] is False and opened["assignees"] == [w.m.col]
    assert (NOW - opened["last_nudged_at"]) < timedelta(hours=3)
    assert done["done"] is True
    assert hidden is None and other_org is None and junk is None  # no trace


@pytest.mark.asyncio
async def test_r8_the_nudge_stamp_is_the_members_own(world) -> None:
    w = world
    async with _as_member(w.p, w.p.org_a):
        mine = await _reader(w, w.m.me, w.p.org_a).task_state(w.ids["t_open"])
        theirs = await _reader(w, w.m.col, w.p.org_a).task_state(w.ids["t_open"])
    assert mine["last_nudged_at"] is not None
    assert theirs["last_nudged_at"] is None


@pytest.mark.asyncio
async def test_r8_open_tasks_leave_out_done_hidden_and_foreign_projects(world) -> None:
    w = world
    async with _as_member(w.p, w.p.org_a):
        r = _reader(w, w.m.me, w.p.org_a)
        rows = await r.open_tasks(w.ids["proj_open"])
        hidden = await r.open_tasks(w.ids["proj_closed"])
        foreign = await r.open_tasks(w.ids["proj_zed"])
    assert _ids(rows) == {w.ids["t_open"], w.ids["t_other"]}  # the done twin is not open
    assert hidden == [] and foreign == []


# ── The whole check, with the real reader ──────────────────────────────────

@pytest.mark.asyncio
async def test_r8_check_write_finds_the_members_own_resend_and_nothing_of_a_colleague(world) -> None:
    import time

    from gateway.write_check import BUDGET_S, check_write

    w = world
    args = {"to": ["mahesh@vendor.test"], "subject": "Licence terms for Acme"}
    async with _as_member(w.p, w.p.org_a):
        start = time.monotonic()
        mine = await check_write("send_email", args, member=w.m.me, mode="on",
                                 reader=_reader(w, w.m.me, w.p.org_a))
        took = time.monotonic() - start
        # Zoe is in no mail of the member, and the colleague's NDA is not the member's.
        none = await check_write("send_email", {**args, "to": ["zoe-nobody@other.test"]},
                                 member=w.m.me, mode="on", reader=_reader(w, w.m.me, w.p.org_a))
        dup = await check_write("create_task", {"project_id": w.ids["proj_open"],
                                                "title": "Fix login page"},
                                member=w.m.me, mode="on", reader=_reader(w, w.m.me, w.p.org_a))
        nudge = await check_write("nudge_task", {"task_id": w.ids["t_open"]}, member=w.m.me,
                                  mode="on", reader=_reader(w, w.m.me, w.p.org_a))
    assert [f["kind"] for f in mine] == ["write_already_sent"]
    assert mine[0]["evidence"][0]["id"] == w.ids["mine"]  # never the colleague's mail
    assert none == []
    assert [f["kind"] for f in dup] == ["write_duplicate_task"]
    assert dup[0]["evidence"][0]["id"] == w.ids["t_open"]
    assert [f["kind"] for f in nudge] == ["write_task_settled"]  # nudged two hours ago
    assert took < BUDGET_S
