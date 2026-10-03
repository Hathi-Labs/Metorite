"""WS-17 EM-T4e — the N+1 reads and the thread indexes (migration 226).

Spec: ``project-docs/specs/email_app_master_plan.md`` §10.4.6, the part
"EM-T4e", from §7 Tier 1 item 4.

R7 fences named here:

* ``email-load-rules-two-reads``: ``_load_rules`` for 5 rules issues 2 reads,
  and its result equals the result of the old code (a copy of it lives in
  this file). Hermetic, and R8 on a real database, where the actions of one
  rule share ``created_at`` and the sort has to keep their order. The R8 test
  runs twice: with the plan of the planner, and with a hash join over
  sequential scans, the plan in which a Sort on ``created_at`` alone loses
  that order.
* ``email-unread-one-count``: ``list_accounts`` for 3 accounts issues ONE
  read of ``email_messages``, and ``set_default_account`` issues one. The
  counts equal the old per-account ``COUNT``. Hermetic, and R8 under FORCE RLS
  with a mailbox of another member and a mailbox in another organization.
* ``email-226-fresh-and-rerun``: 226 applies to a fresh ladder database, and a
  second run changes nothing. It adds no foreign key on ``last_message_id``.
* ``email-thread-read-uses-index``: ``EXPLAIN`` of the thread read that
  ``build_thread_context`` sends names ``idx_email_messages_thread_received``
  on a seeded table, with no Sort node (R8, as the non-privileged role).

The R8 tests run as ``acb_app_h3rls`` (NOSUPERUSER, NOBYPASSRLS) on the
phase-4-promoted two-org catalog of ``test_h3_rls_promotion_rehearsal``.

Run (real Postgres)::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_email_n_plus_one.py -v -rs
"""
from __future__ import annotations

import json
import os
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth.roles import UserContext, UserRole
from acb_common.db import bind_tenant, release_tenant
from gateway.routes.email.automation import rules
from gateway.routes.email.automation.replyzero import build_thread_context
from gateway.routes.email.core import _tenant_session
from gateway.routes.email.transport import accounts
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from tests.unit._email_fakes import bind_db
from tests.unit._tenant_ladder import INIT_SCHEMA, _exec_file, ladder, tenant_engine_scope

# ``promoted`` and ``app_engine`` are used by name for fixture injection, so
# the import is load-bearing even though it reads as unused.
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    _URL,
    app_engine,
    promoted,
)

_ROOT = Path(__file__).resolve().parents[2]
_MIGRATIONS = _ROOT / "infra" / "postgres"

THREAD_INDEX = "idx_email_messages_thread_received"
STATUS_INDEX = "idx_email_thread_status_last_message"


# ══════════════════════════════════════════════════════════════════════════
# The old code, kept as the reference the new code must equal
# ══════════════════════════════════════════════════════════════════════════

async def _old_load_rules(db: Any, account_id: str) -> list[dict[str, Any]]:
    """``rules._load_rules`` as it was before EM-T4e: one read per rule."""
    rule_rows = (await db.execute(text(
        """SELECT id, account_id, name, instructions, enabled, automated,
                  run_on_threads, conditional_operator, from_pattern, to_pattern,
                  subject_pattern, body_pattern, system_type
           FROM email_rules WHERE account_id = :aid
           ORDER BY created_at"""
    ), {"aid": account_id})).fetchall()
    out: list[dict[str, Any]] = []
    for r in rule_rows:
        act_rows = (await db.execute(text(
            """SELECT id, type, label, subject, content, to_address, cc_address,
                      bcc_address, url, delay_minutes, attachments,
                      label_ai, content_manual
               FROM email_actions WHERE rule_id = :rid
               ORDER BY created_at"""
        ), {"rid": r.id})).fetchall()
        out.append({
            "id": str(r.id), "account_id": str(r.account_id), "name": r.name,
            "instructions": r.instructions, "enabled": r.enabled,
            "automated": r.automated,
            "run_on_threads": r.run_on_threads,
            "conditional_operator": r.conditional_operator,
            "from_pattern": r.from_pattern, "to_pattern": r.to_pattern,
            "subject_pattern": r.subject_pattern, "body_pattern": r.body_pattern,
            "system_type": r.system_type,
            "actions": [
                {"id": str(a.id), "type": a.type, "label": a.label,
                 "subject": a.subject, "content": a.content,
                 "to_address": a.to_address, "cc_address": a.cc_address,
                 "bcc_address": a.bcc_address, "url": a.url,
                 "delay_minutes": a.delay_minutes,
                 "label_ai": bool(a.label_ai),
                 "content_manual": bool(a.content_manual),
                 "attachments": a.attachments if isinstance(a.attachments, list)
                 else json.loads(a.attachments or "[]")}
                for a in act_rows
            ],
        })
    return rules._sort_rules_canonical(out)


_OLD_UNREAD_SQL = """SELECT COUNT(*) FROM email_messages
                     WHERE account_id = :account_id AND is_read = false"""


class _Recording:
    """Wraps a session, and keeps the SQL and the params of each read."""

    def __init__(self, db: Any) -> None:
        self._db = db
        self.statements: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, stmt: Any, params: Any = None, *a: Any, **k: Any) -> Any:
        self.statements.append((str(stmt), dict(params or {})))
        return await self._db.execute(stmt, params, *a, **k)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._db, name)

    def touching(self, table: str) -> list[str]:
        return [s for s, _ in self.statements if table in s]


# ══════════════════════════════════════════════════════════════════════════
# 1. Hermetic: _load_rules
# ══════════════════════════════════════════════════════════════════════════

_T0 = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
ACCOUNT = "acc-1"


def _rule(i: int, at: datetime, account: str = ACCOUNT, **over: Any) -> SimpleNamespace:
    base = dict(
        id=f"rule-{account}-{i}", account_id=account, name=f"Rule {i}",
        instructions=f"when {i}", enabled=True, automated=True,
        run_on_threads=False, conditional_operator="AND", from_pattern=None,
        to_pattern=None, subject_pattern=None, body_pattern=None,
        system_type=None, created_at=at,
    )
    base.update(over)
    return SimpleNamespace(**base)


def _action(rule_id: str, n: int, at: datetime, seq: int) -> SimpleNamespace:
    return SimpleNamespace(
        id=f"{rule_id}-act-{n}", rule_id=rule_id, type="LABEL",
        label=f"{rule_id}/{n}", subject=None, content=None, to_address=None,
        cc_address=None, bcc_address=None, url=None, delay_minutes=None,
        attachments=json.dumps([{"name": f"f{n}.pdf"}]) if n == 0 else [],
        label_ai=n % 2 == 0, content_manual=False, created_at=at, seq=seq,
    )


class _RulesDb:
    """Holds rule and action rows, and answers the old and the new reads.

    ``seq`` stands in for ``ctid``: the order of the rows in the table.
    """

    def __init__(self, rule_rows: list[SimpleNamespace],
                 action_rows: list[SimpleNamespace]) -> None:
        self.rules = rule_rows
        self.actions = action_rows
        self.statements: list[str] = []

    async def execute(self, stmt: Any, params: Any = None) -> Any:
        sql = " ".join(str(stmt).split())
        self.statements.append(sql)
        p = dict(params or {})
        if "FROM email_actions a JOIN email_rules r" in sql:
            owned = {r.id for r in self.rules if r.account_id == p["aid"]}
            rows = sorted((a for a in self.actions if a.rule_id in owned),
                          key=lambda a: (a.created_at, a.seq))
        elif "FROM email_actions WHERE rule_id = :rid" in sql:
            rows = sorted((a for a in self.actions if a.rule_id == p["rid"]),
                          key=lambda a: (a.created_at, a.seq))
        elif "FROM email_rules WHERE account_id = :aid" in sql:
            rows = sorted((r for r in self.rules if r.account_id == p["aid"]),
                          key=lambda r: r.created_at)
        else:
            raise AssertionError(f"unexpected SQL: {sql}")
        return SimpleNamespace(fetchall=lambda: list(rows))


def _five_rules() -> _RulesDb:
    t1, t2 = _T0 + timedelta(hours=1), _T0 + timedelta(hours=2)
    rule_rows = [
        _rule(1, _T0, system_type="REPLY", name="Needs Reply"),
        _rule(2, _T0, enabled=False),
        _rule(3, _T0),
        _rule(4, t1),
        _rule(5, t2, system_type="NEWSLETTER", name="Newsletter"),
        # Another account: its rule and actions must not appear.
        _rule(9, _T0, account="acc-2"),
    ]
    acts: list[SimpleNamespace] = []
    seq = 0
    # The table order is not the time order, as after a later edit.
    for rid, n, at in (("rule-acc-1-5", 3, t2), ("rule-acc-1-1", 8, _T0),
                       ("rule-acc-2-9", 2, _T0), ("rule-acc-1-4", 2, t1),
                       ("rule-acc-1-2", 1, _T0)):
        for i in range(n):
            acts.append(_action(rid, i, at, seq))
            seq += 1
    return _RulesDb(rule_rows, acts)


async def test_load_rules_for_five_rules_issues_two_reads() -> None:
    db = _five_rules()
    got = await rules._load_rules(db, ACCOUNT)
    assert len(got) == 5
    assert len(db.statements) == 2, db.statements


async def test_load_rules_equals_the_old_code() -> None:
    new_db, old_db = _five_rules(), _five_rules()
    new = await rules._load_rules(new_db, ACCOUNT)
    old = await _old_load_rules(old_db, ACCOUNT)
    assert new == old
    assert len(old_db.statements) == 6, "the reference is not the old N+1 shape"
    by_name = {r["name"]: r for r in new}
    assert [a["label"] for a in by_name["Needs Reply"]["actions"]] == [
        f"rule-acc-1-1/{i}" for i in range(8)]
    assert by_name["Rule 3"]["actions"] == []
    assert by_name["Needs Reply"]["actions"][0]["attachments"] == [{"name": "f0.pdf"}]
    assert all(not a["label"].startswith("rule-acc-2")
               for r in new for a in r["actions"])


async def test_load_rules_with_no_rule_issues_one_read() -> None:
    db = _RulesDb([], [])
    assert await rules._load_rules(db, ACCOUNT) == []
    assert len(db.statements) == 1


# ══════════════════════════════════════════════════════════════════════════
# 2. Hermetic: the unread counts of the account reads
# ══════════════════════════════════════════════════════════════════════════

OWNER = "owner@em-t4e.test"


def _account_row(i: int, **over: Any) -> SimpleNamespace:
    base = dict(
        id=f"acct-{i}", provider="microsoft", email_address=f"box{i}@x.test",
        label=f"Box {i}", avatar_color=None, sync_enabled=True,
        sync_status="idle", sync_error=None, last_synced_at=None,
        is_default=i == 1, initial_sync_done=True, import_since=None,
        onboarding_done_at=None,
        # The progress columns of EM-T6b (open as #580 on 2026-10-03). A row
        # carries them now, so this fake keeps working when #580 merges.
        import_phase=None, import_count=None, import_estimate=None,
        import_reached_at=None,
    )
    base.update(over)
    return SimpleNamespace(**base)


class _AccountsDb:
    """Answers the account reads and both shapes of the unread count."""

    def __init__(self, rows: list[SimpleNamespace], unread: dict[str, int],
                 owners: dict[str, str]) -> None:
        self.rows = rows
        self.unread = unread
        self.owners = owners
        self.statements: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, stmt: Any, params: Any = None) -> Any:
        sql = " ".join(str(stmt).split())
        p = dict(params or {})
        self.statements.append((sql, p))
        if "FROM email_messages" in sql and "GROUP BY" in sql:
            assert "user_id = :uid" in sql, "the grouped count lost the owner predicate"
            ids = [a for a, o in self.owners.items() if o == p["uid"]
                   and ("aid" not in p or a == p["aid"])]
            out = [SimpleNamespace(account_id=a, unread=self.unread[a])
                   for a in ids if self.unread.get(a)]
            return SimpleNamespace(fetchall=lambda: out)
        if "FROM email_messages" in sql:
            n = self.unread.get(p.get("account_id"), 0)
            return SimpleNamespace(scalar=lambda: n)
        if "FROM email_accounts" in sql and "WHERE user_id = :user_id" in sql:
            mine = [r for r in self.rows if self.owners[r.id] == p["user_id"]]
            return SimpleNamespace(fetchall=lambda: mine)
        if sql.startswith("SELECT 1 FROM email_accounts"):
            hit = self.owners.get(p["id"]) == p["uid"]
            return SimpleNamespace(fetchone=lambda: object() if hit else None)
        if "RETURNING" in sql:
            row = next(r for r in self.rows if r.id == p["id"])
            return SimpleNamespace(fetchone=lambda: row)
        if sql.startswith("UPDATE email_accounts"):
            return SimpleNamespace()
        raise AssertionError(f"unexpected SQL: {sql}")

    def count_reads(self) -> list[str]:
        return [s for s, _ in self.statements if "email_messages" in s]


def _three_accounts() -> _AccountsDb:
    rows = [_account_row(1), _account_row(2), _account_row(3),
            _account_row(4)]
    owners = {"acct-1": OWNER, "acct-2": OWNER, "acct-3": OWNER,
              "acct-4": "other@em-t4e.test"}
    return _AccountsDb(rows, {"acct-1": 2, "acct-3": 5, "acct-4": 9}, owners)


def _me() -> UserContext:
    return UserContext(email=OWNER, role=UserRole.EMPLOYEE)


async def test_list_accounts_for_three_accounts_issues_one_count(monkeypatch) -> None:
    db = _three_accounts()
    monkeypatch.setattr(accounts, "_tenant_session", bind_db(db))
    listed = await accounts.list_accounts(user=_me())
    assert {a.id: a.unread_count for a in listed} == {
        "acct-1": 2, "acct-2": 0, "acct-3": 5}
    assert len(db.count_reads()) == 1, db.count_reads()
    [(_sql, params)] = [(s, p) for s, p in db.statements if "email_messages" in s]
    assert params == {"uid": OWNER}, "the count must bind the member of the session"


async def test_list_accounts_keeps_every_field(monkeypatch) -> None:
    db = _three_accounts()
    db.rows[0].import_since = _T0
    db.rows[0].onboarding_done_at = _T0
    monkeypatch.setattr(accounts, "_tenant_session", bind_db(db))
    first = (await accounts.list_accounts(user=_me()))[0].model_dump()
    # Every field that the read returned before EM-T4e. A later part may add
    # a field, and this test does not pin the set.
    before = {
        "id": "acct-1", "provider": "microsoft", "email_address": "box1@x.test",
        "label": "Box 1", "avatar_color": "#6366f1", "sync_enabled": True,
        "sync_status": "idle", "sync_error": None, "last_synced_at": None,
        "unread_count": 2, "is_default": True, "initial_sync_done": True,
        "import_since": _T0.isoformat(), "onboarding_done": True,
    }
    assert {k: first.get(k) for k in before} == before


async def test_list_accounts_with_no_account_reads_no_count(monkeypatch) -> None:
    db = _three_accounts()
    monkeypatch.setattr(accounts, "_tenant_session", bind_db(db))
    nobody = UserContext(email="nobody@em-t4e.test", role=UserRole.EMPLOYEE)
    assert await accounts.list_accounts(user=nobody) == []
    assert db.count_reads() == []


async def test_set_default_reads_one_grouped_count(monkeypatch) -> None:
    db = _three_accounts()
    monkeypatch.setattr(accounts, "_tenant_session", bind_db(db))
    made = await accounts.set_default_account("acct-3", user=_me())
    assert made.unread_count == 5
    [(sql, params)] = [(s, p) for s, p in db.statements if "email_messages" in s]
    assert "GROUP BY" in sql
    assert params == {"uid": OWNER, "aid": "acct-3"}


# ══════════════════════════════════════════════════════════════════════════
# 3. R8 helpers
# ══════════════════════════════════════════════════════════════════════════

def _assert_non_priv(app_eng) -> None:
    with app_eng.connect() as c:
        role = c.execute(text(
            "SELECT rolsuper, rolbypassrls FROM pg_roles "
            "WHERE rolname = current_user")).first()
    assert role is not None and not role[0] and not role[1], (
        "this suite connects as a SUPERUSER/BYPASSRLS role — RLS is bypassed"
    )


def _seed_account(admin_engine, *, org: str, owner: str) -> str:
    with admin_engine.begin() as c:
        return str(c.execute(text(
            "INSERT INTO email_accounts (user_id, provider, email_address, "
            "credentials_encrypted, initial_sync_done, organization_id) "
            "VALUES (:u, 'microsoft', :m, 'x', true, CAST(:o AS uuid)) "
            "RETURNING id"),
            {"u": owner, "m": f"box-{uuid.uuid4().hex[:8]}@em-t4e.test",
             "o": org}).scalar_one())


def _delete_accounts(admin_engine, ids: list[str]) -> None:
    with admin_engine.begin() as c:
        for table in ("email_messages", "email_rules"):
            c.execute(text(f"DELETE FROM {table} WHERE account_id = ANY(CAST(:a AS uuid[]))"),
                      {"a": ids})
        c.execute(text("DELETE FROM email_accounts WHERE id = ANY(CAST(:a AS uuid[]))"),
                  {"a": ids})


def _seed_messages(admin_engine, *, account: str, org: str,
                   unread: int, read: int) -> None:
    with admin_engine.begin() as c:
        for i in range(unread + read):
            c.execute(text(
                "INSERT INTO email_messages (account_id, provider_message_id, "
                "folder, from_address, to_addresses, subject, is_read, "
                "received_at, organization_id) VALUES (CAST(:a AS uuid), :p, "
                "'inbox', '{}'::jsonb, '[]'::jsonb, 's', :r, now(), "
                "CAST(:o AS uuid))"),
                {"a": account, "p": f"m-{uuid.uuid4().hex[:10]}",
                 "r": i >= unread, "o": org})


@asynccontextmanager
async def _as_app(p, org: str):
    """A tenant session as the non-privileged role, bound to ``org``."""
    app_dsn = p.app_url.render_as_string(hide_password=False)
    token = bind_tenant(org)
    try:
        async with tenant_engine_scope(app_dsn):
            yield
    finally:
        release_tenant(token)


# ══════════════════════════════════════════════════════════════════════════
# 4. R8: _load_rules on a real database
# ══════════════════════════════════════════════════════════════════════════

def _seed_rules(admin_engine, *, account: str, org: str) -> None:
    """Five rules. The table order of the actions is not their time order.

    Each rule writes its actions with one ``created_at``, as one transaction
    of ``_replace_actions`` does. ``Needs Reply`` has 8 actions, so a sort
    that does not keep the order of a tie can show it.
    """
    t1, t2 = _T0 + timedelta(hours=1), _T0 + timedelta(hours=2)
    specs = [("Needs Reply", "REPLY", _T0, True), ("Rule 2", None, _T0, False),
             ("Rule 3", None, _T0, True), ("Rule 4", None, t1, True),
             ("Newsletter", "NEWSLETTER", t2, True)]
    with admin_engine.begin() as c:
        ids = {}
        for name, system_type, at, enabled in specs:
            ids[name] = str(c.execute(text(
                "INSERT INTO email_rules (account_id, name, instructions, "
                "enabled, system_type, created_at, organization_id) VALUES "
                "(CAST(:a AS uuid), :n, :i, :e, :s, :at, CAST(:o AS uuid)) "
                "RETURNING id"),
                {"a": account, "n": name, "i": f"when {name}", "e": enabled,
                 "s": system_type, "at": at, "o": org}).scalar_one())
        for name, count, at in (("Newsletter", 3, t2), ("Needs Reply", 8, _T0),
                                ("Rule 4", 2, t1), ("Rule 2", 1, _T0)):
            for i in range(count):
                c.execute(text(
                    "INSERT INTO email_actions (rule_id, type, label, "
                    "attachments, label_ai, created_at, organization_id) "
                    "VALUES (CAST(:r AS uuid), 'LABEL', :l, CAST(:att AS jsonb), "
                    ":ai, :at, CAST(:o AS uuid))"),
                    {"r": ids[name], "l": f"{name}/{i}",
                     "att": json.dumps([{"name": f"{name}-{i}.pdf"}] if i == 0 else []),
                     "ai": i % 2 == 0, "at": at, "o": org})


#: Forces a hash join over two sequential scans: the plan a large table gets.
#: The rows then reach the Sort in table order, across all the rules, and a
#: Sort on ``created_at`` alone reorders the tied actions of one rule. Measured
#: on 2026-10-03: ``Needs Reply/3`` to ``/7`` came before ``/0`` to ``/2``.
_HASH_JOIN_PLAN = ("enable_nestloop", "enable_mergejoin", "enable_indexscan",
                   "enable_bitmapscan")


@_DB_GATE
class TestLoadRulesOnARealDatabase:

    @pytest.mark.parametrize("knobs", [(), _HASH_JOIN_PLAN],
                             ids=["planner-default", "hash-join-over-seq-scans"])
    async def test_five_rules_two_reads_and_the_old_result(
        self, promoted, app_engine, knobs,  # noqa: F811
    ):
        _assert_non_priv(app_engine)
        p = promoted
        mine = _seed_account(p.admin_engine, org=p.org_b, owner=OWNER)
        theirs = _seed_account(p.admin_engine, org=p.org_b, owner="other@em-t4e.test")
        _seed_rules(p.admin_engine, account=mine, org=p.org_b)
        _seed_rules(p.admin_engine, account=theirs, org=p.org_b)
        try:
            async with _as_app(p, p.org_b), _tenant_session() as db:
                for knob in knobs:
                    await db.execute(text(f"SET LOCAL {knob} = off"))
                rec = _Recording(db)
                new = await rules._load_rules(rec, mine)
                old = await _old_load_rules(db, mine)
                read = (await db.execute(
                    text(rules._ACCOUNT_ACTIONS_SQL), {"aid": mine})).fetchall()
            assert len(rec.statements) == 2, [s for s, _ in rec.statements]
            assert new == old
            # The actions read stays on the account: 8 + 1 + 2 + 3 rows,
            # not the 28 of both mailboxes.
            assert len(read) == 14
            assert {str(a.rule_id) for a in read} <= {r["id"] for r in new}
            by_name = {r["name"]: r for r in new}
            assert len(by_name) == 5
            assert [a["label"] for a in by_name["Needs Reply"]["actions"]] == [
                f"Needs Reply/{i}" for i in range(8)]
            assert by_name["Rule 3"]["actions"] == []
            assert {r["account_id"] for r in new} == {mine}
            assert by_name["Newsletter"]["actions"][0]["attachments"] == [
                {"name": "Newsletter-0.pdf"}]
        finally:
            _delete_accounts(p.admin_engine, [mine, theirs])


# ══════════════════════════════════════════════════════════════════════════
# 5. R8: the account reads under FORCE RLS
# ══════════════════════════════════════════════════════════════════════════

@_DB_GATE
class TestUnreadCountsOnARealDatabase:

    async def test_three_accounts_one_count_and_the_old_counts(
        self, promoted, app_engine, monkeypatch,  # noqa: F811
    ):
        _assert_non_priv(app_engine)
        p = promoted
        boxes = [_seed_account(p.admin_engine, org=p.org_b, owner=OWNER)
                 for _ in range(3)]
        other = _seed_account(p.admin_engine, org=p.org_b, owner="other@em-t4e.test")
        # The same member in the other organization: FORCE RLS hides it.
        elsewhere = _seed_account(p.admin_engine, org=p.org_a, owner=OWNER)
        for box, unread, read in zip(boxes, (2, 0, 5), (1, 2, 0), strict=True):
            _seed_messages(p.admin_engine, account=box, org=p.org_b,
                           unread=unread, read=read)
        _seed_messages(p.admin_engine, account=other, org=p.org_b, unread=4, read=0)
        _seed_messages(p.admin_engine, account=elsewhere, org=p.org_a, unread=7, read=0)

        recorders: list[_Recording] = []
        real = accounts._tenant_session

        @asynccontextmanager
        async def _recording_session(*a: Any, **k: Any):
            async with real(*a, **k) as db:
                rec = _Recording(db)
                recorders.append(rec)
                yield rec

        monkeypatch.setattr(accounts, "_tenant_session", _recording_session)
        me = UserContext(email=OWNER, role=UserRole.EMPLOYEE, organization_id=p.org_b)
        try:
            async with _as_app(p, p.org_b):
                listed = await accounts.list_accounts(user=me)
                [listing] = recorders
                made = await accounts.set_default_account(boxes[2], user=me)
                setting = recorders[1]
                async with real() as db:
                    old = {b: int((await db.execute(
                        text(_OLD_UNREAD_SQL), {"account_id": b})).scalar() or 0)
                        for b in boxes}
            got = {a.id: a.unread_count for a in listed}
            assert got == {boxes[0]: 2, boxes[1]: 0, boxes[2]: 5}
            assert got == old, "the grouped count differs from the old count"
            assert len(listing.touching("email_messages")) == 1
            assert made.unread_count == 5
            assert len(setting.touching("email_messages")) == 1
        finally:
            _delete_accounts(p.admin_engine, [*boxes, other, elsewhere])


# ══════════════════════════════════════════════════════════════════════════
# 6. R8: migration 226, on a fresh ladder and again
# ══════════════════════════════════════════════════════════════════════════

def _migration_226() -> Path:
    """Found by CONTENT, never by number: R1 can renumber it at merge."""
    hits = [
        p for p in _MIGRATIONS.glob("[0-9]*_*.sql")
        if f"CREATE INDEX IF NOT EXISTS {THREAD_INDEX}" in p.read_text(encoding="utf-8")
    ]
    assert len(hits) == 1, f"expected one migration to create {THREAD_INDEX}, got {hits}"
    return hits[0]


def _number(path: str | Path) -> int:
    return int(os.path.basename(str(path)).split("_", 1)[0])


def _run_226(url) -> None:
    eng = create_engine(url, isolation_level="AUTOCOMMIT")
    try:
        with eng.connect() as conn:
            _exec_file(conn, str(_migration_226()))
    finally:
        eng.dispose()


def _shape(conn) -> dict:
    """The indexes and the constraints of the two tables."""
    return {
        "indexes": sorted(tuple(r) for r in conn.execute(text(
            "SELECT tablename, indexname, indexdef FROM pg_indexes "
            "WHERE tablename IN ('email_messages', 'email_thread_status')")).all()),
        "constraints": sorted(tuple(r) for r in conn.execute(text(
            "SELECT conrelid::regclass::text, conname, contype FROM pg_constraint "
            "WHERE conrelid IN ('email_messages'::regclass, "
            "'email_thread_status'::regclass)")).all()),
    }


def _index_def(conn, name: str) -> str | None:
    return conn.execute(text(
        "SELECT indexdef FROM pg_indexes WHERE indexname = :n"), {"n": name}).scalar()


def _last_message_fks(conn) -> list[str]:
    return [r[0] for r in conn.execute(text(
        "SELECT c.conname FROM pg_constraint c "
        "JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY(c.conkey) "
        "WHERE c.conrelid = 'email_thread_status'::regclass AND c.contype = 'f' "
        "AND a.attname = 'last_message_id'")).all()]


def test_226_is_two_plain_idempotent_index_builds() -> None:
    sql = _migration_226().read_text(encoding="utf-8")
    code = "\n".join(line for line in sql.splitlines()
                     if not line.lstrip().startswith("--"))
    statements = [" ".join(s.split()) for s in code.split(";") if s.strip()]
    assert statements == [
        f"CREATE INDEX IF NOT EXISTS {THREAD_INDEX} ON email_messages "
        "(account_id, thread_id, received_at DESC NULLS LAST)",
        f"CREATE INDEX IF NOT EXISTS {STATUS_INDEX} ON email_thread_status "
        "(last_message_id)",
    ]


@pytest.fixture(scope="module")
def fresh_db():
    """A dedicated database built from the ladder UP TO 226. Never the shared
    ladder database, which other suites replay."""
    admin_url = make_url(_URL)
    dedicated = f"{admin_url.database}_emt4e"
    maint = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with maint.connect() as c:
        c.execute(text(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = :d AND pid <> pg_backend_pid()"), {"d": dedicated})
        c.execute(text(f'DROP DATABASE IF EXISTS "{dedicated}"'))
        c.execute(text(f'CREATE DATABASE "{dedicated}"'))
    maint.dispose()

    url = admin_url.set(database=dedicated)
    eng = create_engine(url, future=True)
    n226 = _number(_migration_226())
    with eng.begin() as conn:
        _exec_file(conn, INIT_SCHEMA)
        for path in ladder():
            if _number(path) < n226:
                _exec_file(conn, path)
    try:
        yield url, eng
    finally:
        eng.dispose()
        maint = create_engine(admin_url, isolation_level="AUTOCOMMIT")
        with maint.connect() as c:
            c.execute(text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = :d AND pid <> pg_backend_pid()"), {"d": dedicated})
            c.execute(text(f'DROP DATABASE IF EXISTS "{dedicated}"'))
        maint.dispose()


@_DB_GATE
class TestTheMigration:

    def test_226_applies_to_a_fresh_ladder_and_a_rerun_changes_nothing(self, fresh_db):
        url, eng = fresh_db
        with eng.connect() as c:
            assert _index_def(c, THREAD_INDEX) is None
            assert _index_def(c, STATUS_INDEX) is None

        _run_226(url)
        with eng.connect() as c:
            assert _index_def(c, THREAD_INDEX) == (
                f"CREATE INDEX {THREAD_INDEX} ON public.email_messages USING btree "
                "(account_id, thread_id, received_at DESC NULLS LAST)")
            assert _index_def(c, STATUS_INDEX) == (
                f"CREATE INDEX {STATUS_INDEX} ON public.email_thread_status "
                "USING btree (last_message_id)")
            assert _last_message_fks(c) == [], "226 must not add a foreign key"
            before = _shape(c)

        _run_226(url)
        with eng.connect() as c:
            assert _shape(c) == before, "a second run of 226 changed something"

    def test_the_promoted_catalog_has_both_and_a_rerun_changes_nothing(
        self, promoted,  # noqa: F811
    ):
        admin = promoted.admin_engine
        with admin.connect() as c:
            assert _index_def(c, THREAD_INDEX) is not None
            assert _index_def(c, STATUS_INDEX) is not None
            assert _last_message_fks(c) == []
            before = _shape(c)
        _run_226(admin.url)
        with admin.connect() as c:
            assert _shape(c) == before


# ══════════════════════════════════════════════════════════════════════════
# 7. R8: the thread read of build_thread_context uses the new index
# ══════════════════════════════════════════════════════════════════════════

def _plan_nodes(plan: dict) -> list[dict]:
    out = [plan]
    for child in plan.get("Plans", []):
        out.extend(_plan_nodes(child))
    return out


async def _explain(db: Any, sql: str, params: dict[str, Any]) -> dict:
    raw = (await db.execute(text("EXPLAIN (FORMAT JSON) " + sql), params)).scalar_one()
    return (raw if isinstance(raw, list) else json.loads(raw))[0]["Plan"]


_INTERLEAVED_MAIL = (
    "INSERT INTO email_messages (account_id, provider_message_id, thread_id, "
    "folder, from_address, to_addresses, subject, body_text, received_at, "
    "organization_id) "
    "SELECT CAST(:a AS uuid), 'm' || g, 't' || (g % 500), 'inbox', "
    "'{\"email\": \"x@y.test\"}'::jsonb, '[]'::jsonb, 's', repeat('b', 400), "
    "now() - (g || ' minutes')::interval, CAST(:o AS uuid) "
    "FROM generate_series(CAST(:lo AS int), CAST(:hi AS int)) g"
)


@_DB_GATE
class TestTheThreadReadUsesTheIndex:

    async def test_explain_of_the_thread_read_names_the_new_index(
        self, promoted, app_engine,  # noqa: F811
    ):
        """Three mailboxes of 2000 messages in threads of 4. The mail of the
        three is interleaved in the table, in blocks of 50, as syncs write it.

        Two plans. The plan of the planner names the new index: the done-when
        of the spec. With this layout it is a bitmap scan and a Sort, and with
        the mail of one mailbox in one block it is a backward index scan. So
        the second plan turns the Sort off: only an index that holds
        ``ASC NULLS FIRST`` can then serve the read, and the backward scan of
        ``DESC NULLS LAST`` is that order. A plain ``DESC`` index fails it.
        """
        _assert_non_priv(app_engine)
        p = promoted
        boxes = [_seed_account(p.admin_engine, org=p.org_b, owner=OWNER)
                 for _ in range(3)]
        with p.admin_engine.begin() as c:
            for lo in range(1, 2001, 50):
                for box in boxes:
                    c.execute(text(_INTERLEAVED_MAIL),
                              {"a": box, "o": p.org_b, "lo": lo, "hi": lo + 49})
        with create_engine(p.admin_engine.url, isolation_level="AUTOCOMMIT").connect() as c:
            c.execute(text("ANALYZE email_messages"))
        try:
            async with _as_app(p, p.org_b), _tenant_session() as db:
                rec = _Recording(db)
                ctx = await build_thread_context(
                    rec, boxes[0], "t7", OWNER, extra_domains=frozenset())
                [(sql, params)] = [
                    (s, prm) for s, prm in rec.statements
                    if "FROM email_messages" in s and "thread_id = :tid" in s]
                chosen = await _explain(db, sql, params)
                await db.execute(text("SET LOCAL enable_sort = off"))
                unsorted = await _explain(db, sql, params)
            assert ctx is not None
            assert ctx.thread_text.count("\n\n---\n\n") == 3, "a thread of 4 reads 4"

            names = [n.get("Index Name") for n in _plan_nodes(chosen)]
            assert THREAD_INDEX in names, json.dumps(chosen, indent=1)

            nodes = _plan_nodes(unsorted)
            assert [n["Node Type"] for n in nodes] == ["Index Scan"], (
                "no index gives the thread read its order: "
                + json.dumps(unsorted, indent=1))
            assert nodes[0]["Index Name"] == THREAD_INDEX
            assert nodes[0]["Scan Direction"] == "Backward"
        finally:
            _delete_accounts(p.admin_engine, boxes)
