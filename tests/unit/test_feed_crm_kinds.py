"""WS-58 MD-3e — the CRM kinds of My Day (``my_day_feed.md`` §5.9 items 2
to 7, 10.4 and 13).

The source ``crm`` gives three kinds about the member's own CRM work:
``crm_followup_due``, ``deal_quiet`` and ``deal_close_passed``. The R8 half,
with the real SQL of the three new reads and two organizations, is
``test_feed_crm_kinds_r8.py``. This file fences what the provider does:

* **Dark by the list.** With ``crm`` not in ``MY_DAY_PROVIDERS``, no read
  runs and the answer has no new byte (done-when 1).
* **Each kind gives its card** with the ``BASE``, group, ``why_key``, text,
  act and label of §5.9 (done-when 2). One record gives one card.
* **The gate.** The kill switch off gives ``absent`` (done-when 3). A plain
  member under ``access = 'admins'`` gives ``absent``, and no CRM read runs
  (done-when 4). Each other error gives ``failed``.
* **Mirror mode** gives no ``deal_quiet``, and the follow-ups still show
  (done-when 5). The source reads ``ok``.
* **The binds.** Each statement takes the bound tenant and the session
  member, never a request value (done-when 6 and 7 hold on the real SQL).
* **Two sessions at most** (done-when 11), by a counting fake.
* **``PATCH /crm/settings`` bumps the org** (done-when 10), while
  ``MY_DAY_CACHE`` is on.
* **One link builder.** ``sources.crm_href`` is the only place that writes a
  CRM record link (§5.9 rule 10.4).

The real database proves the SQL. A fake agrees with any SQL that it gets,
so this file checks only the Python around it.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import logging
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException
from gateway.routes.crm import access as crm_access
from gateway.routes.crm import activities as crm_activities
from gateway.routes.crm import aggregate as crm_aggregate
from gateway.routes.crm import core as crm_core
from gateway.routes.shell import feed, feed_rank, state
from gateway.routes.shell import needs as shell

from tests.unit._crm_fakes import crm_switch_on
from tests.unit.test_shell_feed import (  # noqa: F401  (fixtures by name)
    FULL,
    FeedFakes,
    _bound,
    _list,
    card,
    fakes,
    run_feed,
)
from tests.unit.test_shell_feed import member as _member
from tests.unit.test_shell_needs import ME, ORG, OTHER

REPO = Path(__file__).resolve().parents[2]
FEED_TS = REPO / "workbench/control_plane/src/lib/shell/feed.ts"
GATEWAY = REPO / "apps/services/gateway/gateway"
CRM_KINDS = ("crm_followup_due", "deal_quiet", "deal_close_passed")


def member(*features: str, email: str = ME) -> Any:
    """A member of ``ORG``, the tenant that ``_bound`` binds."""
    return dataclasses.replace(_member(*features, email=email), organization_id=ORG)


def crm_admin(*features: str) -> Any:
    from acb_auth.permissions import EffectiveAccess

    base = member(*features)
    return dataclasses.replace(base, access=EffectiveAccess(role_granted=frozenset(
        {*(f"feature:{f}" for f in features), crm_access.ADMIN_PERMISSION})))


# ── The fake database ───────────────────────────────────────────────────────


class _Result:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def fetchall(self) -> list[Any]:
        return list(self._rows)

    def first(self) -> Any:
        return self._rows[0] if self._rows else None


class CrmDb:
    """Answers the CRM statements of the provider and of the access rule, by
    the binds it gets. It records each statement with its binds."""

    def __init__(self, f: FeedFakes) -> None:
        self.f = f
        #: (the SQL, its binds), in order.
        self.executed: list[tuple[str, dict[str, Any]]] = []
        #: Sessions opened, by the module that opened them.
        self.opened: list[str] = []
        self.access: str | None = "everyone"
        self.mode: str | None = "native"
        self.access_error: Exception | None = None
        #: The rows of each read, by member.
        self.due: dict[str, list[dict[str, Any]]] = {}
        self.quiet: dict[str, list[dict[str, Any]]] = {}
        self.passed: dict[str, list[dict[str, Any]]] = {}

    def crm_reads(self) -> list[str]:
        """The CRM data statements that ran, by name."""
        names = {crm_activities._MY_DUE_SQL: "due", crm_aggregate._MY_OVERDUE_SQL: "quiet",
                 crm_aggregate._MY_CLOSE_PASSED_SQL: "passed"}
        return [names[sql] for sql, _ in self.executed if sql in names]

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> _Result:
        sql = str(getattr(stmt, "text", stmt))
        binds = dict(params or {})
        self.executed.append((sql, binds))
        if sql == crm_access.SETTINGS_SQL:
            if self.access_error is not None:
                raise self.access_error
            if self.access is None:
                return _Result([])
            return _Result([SimpleNamespace(access=self.access, access_groups=[])])
        if sql.startswith("SELECT mode FROM crm_settings"):
            return _Result([SimpleNamespace(mode=self.mode)] if self.mode else [])
        member_rows = {crm_activities._MY_DUE_SQL: self.due,
                       crm_aggregate._MY_OVERDUE_SQL: self.quiet,
                       crm_aggregate._MY_CLOSE_PASSED_SQL: self.passed}.get(sql)
        if member_rows is None:
            raise AssertionError(f"an unknown statement: {sql[:80]}")
        rows = member_rows.get(binds["member"], [])
        if sql == crm_activities._MY_DUE_SQL:
            # The two columns of the day rule. A row with no `all_day` is a
            # call. The provider's own order does not read `fresh`.
            rows = [{"all_day": False, "fresh": True, **r} for r in rows]
        return _Result([SimpleNamespace(**r) for r in rows][: binds["limit"]])

    def session(self, name: str):
        @contextlib.asynccontextmanager
        async def _session():
            self.opened.append(name)
            yield self
        return _session


@pytest.fixture()
def crm(fakes, monkeypatch) -> CrmDb:  # noqa: F811
    """The CRM switch on for each org, and both seams bound to the fake."""
    db = CrmDb(fakes)
    crm_switch_on(monkeypatch, "*")
    monkeypatch.setattr(crm_aggregate, "_tenant_session", db.session("crm"))
    monkeypatch.setattr(crm_access, "_tenant_session", db.session("access"))
    return db


def _tz(f: FeedFakes) -> ZoneInfo:
    return ZoneInfo(f.zone)


def _today(db: CrmDb) -> date:
    return db.f.now.astimezone(_tz(db.f)).date()


def _local(db: CrmDb, day_offset: int, hour: int, minute: int = 0) -> datetime:
    tz = _tz(db.f)
    return datetime.combine(_today(db) + timedelta(days=day_offset), time(hour, minute),
                            tzinfo=tz)


def _seed(db: CrmDb, me: str = ME) -> None:
    """One record of each kind, and one deal that gives all three kinds."""
    now = db.f.now
    db.due[me] = [
        # A lead follow-up that was due two days ago: its time passed, and
        # before my day started, so the state join keeps it in Needs you.
        {"record_kind": "lead", "record_id": "l-1", "label": "Acme lead",
         "due_at": _local(db, -2, 10), "subject": "call back"},
        # A contact follow-up due in one hour: the Now group.
        {"record_kind": "contact", "record_id": "c-1", "label": "Dana Rao",
         "due_at": now + timedelta(hours=1), "subject": None},
        # A follow-up on the deal that also went quiet and passed its date.
        {"record_kind": "deal", "record_id": "d-all", "label": "Big deal",
         "due_at": now + timedelta(hours=5), "subject": "send the quote"},
        {"record_kind": "organization", "record_id": "o-1", "label": "Globex",
         "due_at": now + timedelta(hours=6), "subject": "renewal"},
    ]
    db.quiet[me] = [
        {"id": "d-quiet", "name": "Quiet deal", "stage": "Proposal",
         "status_changed_at": now - timedelta(days=12), "limit_days": 10},
        {"id": "d-all", "name": "Big deal", "stage": "Negotiation",
         "status_changed_at": now - timedelta(days=30), "limit_days": 14},
    ]
    db.passed[me] = [
        {"id": "d-late", "name": "Late deal", "stage": "Proposal",
         "expected_close_date": _today(db) - timedelta(days=3)},
        {"id": "d-all", "name": "Big deal", "stage": "Negotiation",
         "expected_close_date": _today(db) - timedelta(days=1)},
    ]


def _labels() -> dict[str, str]:
    import re

    text = FEED_TS.read_text(encoding="utf-8")
    block = text.split("export const FEED_KIND_LABELS", 1)[1].split("};", 1)[0]
    return dict(re.findall(r"^\s*([a-z_]+): \"([^\"]+)\",", block, re.MULTILINE))


CRM_MEMBER = ("crm", *FULL)


# ── done-when 1: dark until listed ──────────────────────────────────────────


class TestDarkUntilListed:
    def test_the_provider_is_gated_and_feed_only(self):
        p = shell.REGISTRY["crm"]
        assert (p.gated, p.needs, p.features, p.app, p.handled, p.permission) == (
            True, False, ("crm",), None, None, None)
        assert p.kinds == frozenset(CRM_KINDS) == feed.CRM_KINDS
        assert shell.source_app("crm") == "crm"

    def test_with_the_list_empty_no_read_runs_and_no_byte_changes(self, crm, monkeypatch):
        _list(monkeypatch, "")
        _seed(crm)
        answer = run_feed(member(*CRM_MEMBER))
        assert crm.opened == [] and crm.executed == []
        assert "crm" not in answer["sources"]
        assert "crm" not in answer["source_apps"]
        assert not {c["kind"] for c in answer["cards"]} & set(CRM_KINDS)

    def test_listed_it_runs(self, crm, monkeypatch):
        _list(monkeypatch, "crm")
        _seed(crm)
        answer = run_feed(member(*CRM_MEMBER))
        assert answer["sources"]["crm"] == "ok"
        assert answer["source_apps"]["crm"] == "crm"

    def test_a_member_without_the_crm_feature_gets_absent_and_no_read(self, crm, monkeypatch):
        _list(monkeypatch, "crm")
        answer = run_feed(member(*FULL))
        assert answer["sources"]["crm"] == "absent"
        assert crm.opened == []


# ── done-when 2: each kind gives its card ───────────────────────────────────


class TestEachKindGivesItsCard:
    @pytest.fixture()
    def answer(self, crm, monkeypatch):
        _list(monkeypatch, "crm")
        _seed(crm)
        self.db = crm
        return run_feed(member(*CRM_MEMBER))

    def test_a_follow_up_that_is_past_due_on_a_lead(self, answer):
        c = card(answer, "crm:lead:l-1")
        assert (c["kind"], c["app"], c["group"], c["why_key"]) == (
            "crm_followup_due", "crm", "needs_you", "followup_due")
        day = _today(self.db) - timedelta(days=2)
        assert c["why"] == f"Call back is due on {day.strftime('%A')} at 10:00."
        assert c["title"] == "Acme lead"
        assert c["entity"] == {"type": "lead", "id": "l-1", "label": "Acme lead"}
        assert c["href"] == "/crm?tab=leads&lead=l-1"
        assert c["acts"] == [{"id": "open-record", "label": "Open lead", "class": "A",
                              "way": "open", "href": "/crm?tab=leads&lead=l-1"}]
        assert c["urgent_at"] == c["facts"]["due_at"] == c["at"]
        # Due time passed: the base, plus the urgency of a passed time.
        assert c["score"] == feed_rank.BASE["crm_followup_due"] + feed_rank.URGENT_PASSED
        assert "source" not in c

    def test_a_follow_up_due_in_an_hour_with_no_subject_is_now(self, answer):
        c = card(answer, "crm:contact:c-1")
        assert (c["kind"], c["group"]) == ("crm_followup_due", "now")
        clock = (self.db.f.now + timedelta(hours=1)).astimezone(_tz(self.db.f))
        assert c["why"] == f"A follow-up is due today at {clock:%H:%M}."
        assert c["acts"][0]["label"] == "Open contact"
        assert c["href"] == "/crm?tab=contacts&contact=c-1"
        assert c["score"] == 41 + feed_rank.URGENT_SOON

    def test_an_organization_opens_the_companies_tab(self, answer):
        c = card(answer, "crm:organization:o-1")
        assert c["href"] == "/crm?tab=organizations&organization=o-1"
        assert c["acts"][0]["label"] == "Open company"
        assert c["group"] == "needs_you"

    def test_deal_quiet(self, answer):
        c = card(answer, "crm:deal:d-quiet")
        assert (c["kind"], c["app"], c["group"], c["why_key"]) == (
            "deal_quiet", "crm", "fyi", "in_stage")
        assert c["why"] == "In Proposal for 12 days. The stage limit is 10 days."
        assert c["acts"] == [{"id": "log-call", "label": "Log a call", "class": "A",
                              "way": "open", "href": "/crm?tab=deals&deal=d-quiet"}]
        assert c["score"] == feed_rank.BASE["deal_quiet"] == 33
        assert c["urgent_at"] is None
        # The state fact is the stage clock (§5.9 item 4).
        assert c["facts"]["waiting_since"] == c["at"]

    def test_deal_close_passed(self, answer):
        c = card(answer, "crm:deal:d-late")
        assert (c["kind"], c["group"], c["why_key"]) == (
            "deal_close_passed", "needs_you", "close_passed")
        late = _today(self.db) - timedelta(days=3)
        assert c["why"] == (f"The close date was on {late.strftime('%A')}, "
                            "and the deal is still open.")
        assert c["facts"]["due_at"] == datetime.combine(
            late, time.min, tzinfo=_tz(self.db.f)).isoformat()
        assert [(a["id"], a["label"]) for a in c["acts"]] == [("move-date", "Move the date")]
        assert c["score"] == 34

    def test_one_record_gives_one_card(self, answer):
        """The deal of all three kinds: one card, the follow-up first."""
        c = card(answer, "crm:deal:d-all")
        assert c["kind"] == "crm_followup_due"
        assert c["folded"] == ["deal_close_passed", "deal_quiet"]
        assert c["why"].startswith("Send the quote is due today at ")
        # One link for each place: the main kind's act wins the record.
        assert [a["id"] for a in c["acts"]] == ["open-record"]
        assert c["facts"]["stage"] == "Negotiation"

    def test_each_card_has_its_label(self, answer):
        labels = _labels()
        for c in answer["cards"]:
            if c["app"] == "crm":
                assert c["kind"] in labels, c["kind"]
        assert [labels[k] for k in CRM_KINDS] == [
            "Follow-up due", "Quiet deal", "Close date passed"]


# ── done-when 3 and 4: the gate ─────────────────────────────────────────────


class TestTheGate:
    def test_the_kill_switch_off_gives_absent_and_no_session(self, crm, monkeypatch):
        """Done-when 3."""
        from acb_common import get_settings

        _list(monkeypatch, "crm")
        _seed(crm)
        monkeypatch.setattr(get_settings(), "crm_enabled", False, raising=False)
        answer = run_feed(member(*CRM_MEMBER))
        assert answer["sources"]["crm"] == "absent"
        assert crm.opened == [] and crm.executed == []
        assert not {c["kind"] for c in answer["cards"]} & set(CRM_KINDS)

    def test_an_org_off_the_list_gives_absent(self, crm, monkeypatch):
        _list(monkeypatch, "crm")
        crm_switch_on(monkeypatch, "some-other-org")
        answer = run_feed(member(*CRM_MEMBER))
        assert answer["sources"]["crm"] == "absent"
        assert crm.opened == []

    def test_access_admins_gives_a_plain_member_absent_and_no_crm_read(self, crm, monkeypatch):
        """Done-when 4: the access rule refuses BEFORE any CRM read."""
        _list(monkeypatch, "crm")
        _seed(crm)
        crm.access = "admins"
        answer = run_feed(member(*CRM_MEMBER))
        assert answer["sources"]["crm"] == "absent"
        assert crm.opened == ["access"]
        assert crm.crm_reads() == []
        assert [sql for sql, _ in crm.executed] == [crm_access.SETTINGS_SQL]

    def test_no_settings_row_reads_as_admins(self, crm, monkeypatch):
        _list(monkeypatch, "crm")
        crm.access = None
        answer = run_feed(member(*CRM_MEMBER))
        assert answer["sources"]["crm"] == "absent"
        assert crm.crm_reads() == []

    def test_an_admin_passes_with_no_access_read(self, crm, monkeypatch):
        _list(monkeypatch, "crm")
        _seed(crm)
        crm.access = "admins"
        answer = run_feed(crm_admin(*CRM_MEMBER))
        assert answer["sources"]["crm"] == "ok"
        assert crm.opened == ["crm"]
        assert crm.crm_reads() == ["due", "quiet", "passed"]

    def test_another_error_of_the_access_read_fails_the_source(self, crm, monkeypatch):
        _list(monkeypatch, "crm")
        crm.access_error = RuntimeError("down")
        answer = run_feed(member(*CRM_MEMBER))
        assert answer["sources"]["crm"] == "failed"
        assert crm.crm_reads() == []

    def test_another_http_answer_of_the_access_rule_fails_the_source(self, crm, monkeypatch):
        async def _refuse(user):
            raise HTTPException(status_code=503, detail="busy")

        _list(monkeypatch, "crm")
        monkeypatch.setattr(crm_access, "require_crm_access", _refuse)
        answer = run_feed(member(*CRM_MEMBER))
        assert answer["sources"]["crm"] == "failed"

    def test_a_failed_read_fails_the_source(self, crm, monkeypatch):
        async def _broken(db, user, **kw):
            raise RuntimeError("down")

        _list(monkeypatch, "crm")
        monkeypatch.setattr(crm_aggregate, "my_overdue_deals", _broken)
        answer = run_feed(member(*CRM_MEMBER))
        assert answer["sources"]["crm"] == "failed"


# ── done-when 5: Mirror mode ────────────────────────────────────────────────


class TestMirrorMode:
    def test_no_deal_quiet_and_the_follow_ups_still_show(self, crm, monkeypatch):
        _list(monkeypatch, "crm")
        _seed(crm)
        crm.mode = "mirror"
        answer = run_feed(member(*CRM_MEMBER))
        assert answer["sources"]["crm"] == "ok"
        kinds = {c["signal_id"]: c for c in answer["cards"] if c["app"] == "crm"}
        assert "crm:deal:d-quiet" not in kinds
        assert not any("deal_quiet" in [c["kind"], *c["folded"]] for c in kinds.values())
        assert {"crm:lead:l-1", "crm:contact:c-1", "crm:deal:d-late"} <= set(kinds)
        assert "quiet" not in crm.crm_reads()

    def test_native_mode_reads_the_quiet_deals(self, crm, monkeypatch):
        _list(monkeypatch, "crm")
        _seed(crm)
        answer = run_feed(member(*CRM_MEMBER))
        assert "crm:deal:d-quiet" in [c["signal_id"] for c in answer["cards"]]


# ── done-when 6, 7 and 8: the binds of each statement ───────────────────────


class TestTheBinds:
    def test_each_statement_takes_the_bound_tenant_and_the_session_member(
        self, crm, monkeypatch,
    ):
        _list(monkeypatch, "crm")
        _seed(crm)
        user = dataclasses.replace(member(*CRM_MEMBER), email=ME.upper())
        run_feed(user)
        reads = [(sql, b) for sql, b in crm.executed
                 if sql in (crm_activities._MY_DUE_SQL, crm_aggregate._MY_OVERDUE_SQL,
                            crm_aggregate._MY_CLOSE_PASSED_SQL)]
        assert len(reads) == 3
        for _sql, binds in reads:
            assert (binds["org"], binds["member"]) == (ORG, ME)

    def test_the_rows_of_another_member_never_reach_my_feed(self, crm, monkeypatch):
        _list(monkeypatch, "crm")
        _seed(crm, OTHER)
        answer = run_feed(member(*CRM_MEMBER))
        assert answer["sources"]["crm"] == "ok"
        assert not [c for c in answer["cards"] if c["app"] == "crm"]

    def test_each_statement_names_the_tenant_and_the_member(self):
        for sql in (crm_activities._MY_DUE_SQL, crm_aggregate._MY_OVERDUE_SQL,
                    crm_aggregate._MY_CLOSE_PASSED_SQL):
            assert "organization_id = CAST(:org AS uuid)" in sql
            assert "= :member" in sql
        assert "lower(a.created_by) = :member" in crm_activities._MY_DUE_SQL
        assert "lower(d.owner_email) = :member" in crm_aggregate._MY_OVERDUE_SQL
        assert "lower(d.owner_email) = :member" in crm_aggregate._MY_CLOSE_PASSED_SQL
        # The quiet read reuses the stage rule of the dwell route.
        assert crm_aggregate._OVERDUE_FROM in crm_aggregate._MY_OVERDUE_SQL

    def test_the_closed_types_and_the_platform_types_are_bound(self, crm, monkeypatch):
        _list(monkeypatch, "crm")
        run_feed(member(*CRM_MEMBER))
        binds = {sql: b for sql, b in crm.executed}
        assert binds[crm_activities._MY_DUE_SQL]["skip"] == ["status_change", "system"]
        assert binds[crm_aggregate._MY_OVERDUE_SQL]["closing"] == ["won", "lost"]
        assert binds[crm_aggregate._MY_OVERDUE_SQL]["days"] is None
        assert binds[crm_aggregate._MY_CLOSE_PASSED_SQL]["closing"] == ["won", "lost"]

    def test_the_bounds_are_my_local_tomorrow_and_today(self, crm, monkeypatch):
        _list(monkeypatch, "crm")
        run_feed(member(*CRM_MEMBER))
        binds = {sql: b for sql, b in crm.executed}
        tz = _tz(crm.f)
        today = _today(crm)
        due = binds[crm_activities._MY_DUE_SQL]
        tomorrow = today + timedelta(days=1)
        # A call: my local midnight. A task: the UTC midnight of the same date.
        assert due["before"] == datetime.combine(tomorrow, time.min, tzinfo=tz)
        assert due["before_day"] == datetime.combine(tomorrow, time.min, tzinfo=UTC)
        assert due["today_start"] == datetime.combine(today, time.min, tzinfo=tz)
        assert due["today_day"] == datetime.combine(today, time.min, tzinfo=UTC)
        assert due["upper"] == max(due["before"], due["before_day"])
        assert binds[crm_aggregate._MY_CLOSE_PASSED_SQL]["today"] == today

    def test_a_read_with_no_tenant_reads_nothing(self):
        user = member(*CRM_MEMBER)

        class _NoDb:
            async def execute(self, *a, **kw):
                raise AssertionError("no statement without a tenant")

        async def _go():
            return [await crm_activities.my_due_activities(
                        _NoDb(), user, today=date(2026, 10, 12), tz=ZoneInfo("UTC"), limit=5),
                    await crm_aggregate.my_overdue_deals(_NoDb(), user, limit=5),
                    await crm_aggregate.my_close_passed_deals(_NoDb(), user, today=None,
                                                              limit=5)]

        assert asyncio.run(_go()) == [[], [], []]


# ── done-when 11: two sessions at most ──────────────────────────────────────


class TestTheSessions:
    def test_a_plain_member_opens_two_sessions(self, crm, monkeypatch):
        _list(monkeypatch, "crm")
        _seed(crm)
        run_feed(member(*CRM_MEMBER))
        assert crm.opened == ["access", "crm"]
        assert crm.crm_reads() == ["due", "quiet", "passed"]

    def test_the_provider_alone(self, crm):
        _seed(crm)
        found = _bound(lambda: feed._crm(member(*CRM_MEMBER)))
        assert len(crm.opened) <= 2
        assert {s.kind for s in found.signals()} == set(CRM_KINDS)
        assert found.zone == crm.f.zone


class TestTheCap:
    def test_15_signals_at_most_by_base_then_age(self, crm):
        now = crm.f.now
        crm.passed[ME] = [
            {"id": f"d-{n:02d}", "name": f"Deal {n}", "stage": "Proposal",
             "expected_close_date": _today(crm) - timedelta(days=n + 1)}
            for n in range(14)
        ]
        crm.due[ME] = [{"record_kind": "lead", "record_id": "l-1", "label": "Lead",
                        "due_at": now - timedelta(hours=1), "subject": None}]
        crm.quiet[ME] = [{"id": "d-q", "name": "Q", "stage": "Proposal",
                          "status_changed_at": now - timedelta(days=40), "limit_days": 5}]
        found = _bound(lambda: feed._crm(member(*CRM_MEMBER)))
        signals = found.signals()
        assert found.capped is True
        assert len(signals) == shell.PER_APP
        assert [s.kind for s in signals[:2]] == ["crm_followup_due", "deal_close_passed"]
        # The oldest close date first, and the quiet deal (base 33) is cut.
        assert signals[1].signal_id == "crm:deal:d-13"
        assert "crm:deal:d-q" not in [s.signal_id for s in signals]

    def test_a_full_read_sets_capped(self, crm):
        crm.passed[ME] = [
            {"id": f"d-{n:02d}", "name": f"Deal {n}", "stage": "Proposal",
             "expected_close_date": _today(crm) - timedelta(days=n + 1)}
            for n in range(shell.PER_APP + 1)
        ]
        found = _bound(lambda: feed._crm(member(*CRM_MEMBER)))
        assert found.capped is True


# ── state facts and the fold ────────────────────────────────────────────────


class TestTheStateFacts:
    def test_each_kind_names_only_its_own_state_facts(self, crm):
        """§5.9 item 4: due_at for the follow-up and the close date, and
        waiting_since for the quiet deal."""
        _seed(crm)
        found = _bound(lambda: feed._crm(member(*CRM_MEMBER)))
        allowed = {"crm_followup_due": {"due_at"}, "deal_close_passed": {"due_at"},
                   "deal_quiet": {"waiting_since"}}
        for s in found.signals():
            assert set(s.facts) & set(state.STATE_FACTS) == allowed[s.kind], s.kind

    def test_each_kind_gives_the_facts_of_its_template(self, crm):
        """§5.9 item 11, "The facts of each template"."""
        _seed(crm)
        found = _bound(lambda: feed._crm(member(*CRM_MEMBER)))
        need = {"crm_followup_due": {"due_at", "subject"},
                "deal_quiet": {"stage", "stage_days", "stage_limit"},
                "deal_close_passed": {"due_at"}}
        for s in found.signals():
            assert need[s.kind] <= set(s.facts), s.kind


# ── done-when 10: PATCH /crm/settings bumps the org ─────────────────────────


class TestTheSettingsWriteBumpsTheOrg:
    @pytest.fixture()
    def bumps(self, monkeypatch):
        from acb_common.settings import get_settings
        from gateway.routes.crm import settings as crm_settings
        from gateway.routes.shell import cache

        from tests.unit._crm_fakes import FakeCrmDB, bind_db

        got: list[str] = []

        async def _set(org, _msg):
            got.append(org)

        monkeypatch.setattr(get_settings(), "my_day_cache", True)
        monkeypatch.setattr(cache, "_set_org_version", _set)
        fake = FakeCrmDB()
        bind_db(monkeypatch, fake, (crm_core, crm_access, crm_settings))
        crm_switch_on(monkeypatch, "*")
        return got

    def _client(self):
        from acb_auth import UserContext, UserRole, build_access, get_current_user
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from gateway.routes import crm as crm_package

        from tests.unit._crm_fakes import CRM_TEST_ORG

        user = UserContext(email="admin@md3e.test", role=UserRole.EMPLOYEE,
                           access=build_access(["feature:crm", "admin:access:manage"]),
                           organization_id=CRM_TEST_ORG)
        app = FastAPI()
        app.include_router(crm_package.router)
        app.dependency_overrides[get_current_user] = lambda: user
        return TestClient(app, raise_server_exceptions=False), CRM_TEST_ORG

    def test_a_patch_bumps_the_org_version(self, bumps):
        client, org = self._client()
        res = client.patch("/crm/settings", json={"access": "everyone"})
        assert res.status_code == 200, res.text
        assert bumps == [org]

    def test_a_refused_patch_bumps_nothing(self, bumps):
        client, _org = self._client()
        res = client.patch("/crm/settings", json={"access": "nobody"})
        assert res.status_code == 422
        assert bumps == []

    def test_with_the_cache_off_nothing_bumps(self, bumps, monkeypatch):
        from acb_common.settings import get_settings

        monkeypatch.setattr(get_settings(), "my_day_cache", False)
        client, _org = self._client()
        assert client.patch("/crm/settings", json={"access": "everyone"}).status_code == 200
        assert bumps == []


# ── §5.9 rule 10.4: one builder of the CRM record link ──────────────────────


class TestOneLinkBuilder:
    @pytest.mark.parametrize(("kind", "href"), [
        ("lead", "/crm?tab=leads&lead=x%201"),
        ("deal", "/crm?tab=deals&deal=x%201"),
        ("contact", "/crm?tab=contacts&contact=x%201"),
        ("company", "/crm?tab=organizations&organization=x%201"),
    ])
    def test_crm_href(self, kind, href):
        from gateway.retrieval import sources

        assert sources.crm_href(kind, "x 1") == href

    def test_the_link_is_written_in_one_place(self):
        """No other gateway file writes ``/crm?tab=``. The search sources
        and the feed both call ``crm_href``."""
        hits = [p.relative_to(GATEWAY).as_posix() for p in GATEWAY.rglob("*.py")
                if "/crm?tab=" in p.read_text(encoding="utf-8")]
        assert hits == ["retrieval/sources.py"]
        body = (GATEWAY / "retrieval/sources.py").read_text(encoding="utf-8")
        assert body.count('f"/crm?tab=') == 1
        assert "crm_href(kind, row[\"id\"])" in body


# ── §5.9 item 7: the elapsed time of each provider run ──────────────────────


def _answered(caplog, provider: str) -> dict[str, str]:
    """The fields of one ``provider answered`` line, read from the formatted
    text: the gateway prints ``%(message)s`` only (MD-3c review P1)."""
    fmt = logging.Formatter("%(message)s")
    lines = [fmt.format(r) for r in caplog.records]
    [line] = [x for x in lines
              if " provider answered " in x and f" provider={provider} " in x]
    return dict(part.split("=", 1) for part in line.split() if "=" in part)


class TestTheElapsedTime:
    def test_the_crm_run_logs_elapsed_ms(self, crm, monkeypatch, caplog):
        _list(monkeypatch, "crm")
        with caplog.at_level(logging.INFO, logger=shell.logger.name):
            run_feed(member(*CRM_MEMBER))
        fields = _answered(caplog, "crm")
        assert fields["outcome"] == "ok"
        assert int(fields["elapsed_ms"]) >= 0

    def test_an_absent_run_logs_it_too(self, crm, monkeypatch, caplog):
        _list(monkeypatch, "crm")
        crm.access = "admins"
        with caplog.at_level(logging.INFO, logger=shell.logger.name):
            run_feed(member(*CRM_MEMBER))
        fields = _answered(caplog, "crm")
        assert fields["outcome"] == "absent"
        assert int(fields["elapsed_ms"]) >= 0


# ── P1-1: the one CRM day rule (crm_platform.md §19.7) ──────────────────────


def _task(record_id: str, day: date, subject: str | None = "call back") -> dict[str, Any]:
    """A task, as the CRM stores it: a DATE at 00:00 UTC."""
    return {"record_kind": "contact", "record_id": record_id, "label": "Dana Rao",
            "due_at": datetime.combine(day, time.min, tzinfo=UTC), "subject": subject,
            "all_day": True}


class TestTheDayRuleOfATask:
    @pytest.mark.parametrize("zone", ["Asia/Kolkata", "America/New_York"])
    def test_a_task_of_my_today_is_due_today_with_no_clock(self, crm, zone):
        crm.f.zone = zone
        tz = ZoneInfo(zone)
        today = crm.f.now.astimezone(tz).date()
        crm.due[ME] = [_task("c-task", today)]
        found = _bound(lambda: feed._crm(member(*CRM_MEMBER)))
        [signal] = found.signals()
        # The task's date at 00:00 in MY zone, never 00:00 UTC.
        assert signal.facts["due_at"] == datetime.combine(today, time.min, tzinfo=tz).isoformat()
        assert signal.facts["all_day"] is True
        assert signal.urgent_at is None, "a midnight is not a time"
        [c] = feed.build_cards(found.signals(), crm.f.now, tz, today)
        assert c["why"] == "Call back is due today."
        assert c["group"] == "needs_you"

    def test_a_task_of_yesterday_reads_yesterday(self, crm):
        crm.f.zone = "Asia/Kolkata"
        tz = ZoneInfo(crm.f.zone)
        today = crm.f.now.astimezone(tz).date()
        crm.due[ME] = [_task("c-old", today - timedelta(days=1), subject=None)]
        found = _bound(lambda: feed._crm(member(*CRM_MEMBER)))
        [c] = feed.build_cards(found.signals(), crm.f.now, tz, today)
        assert c["why"] == "A follow-up is due yesterday."

    def test_a_call_keeps_its_time(self, crm):
        due = crm.f.now + timedelta(hours=1)
        crm.due[ME] = [{"record_kind": "contact", "record_id": "c-call", "label": "D",
                        "due_at": due, "subject": "call", "all_day": False}]
        found = _bound(lambda: feed._crm(member(*CRM_MEMBER)))
        [signal] = found.signals()
        assert signal.facts["due_at"] == signal.urgent_at == due.isoformat()
        assert "all_day" not in signal.facts

    @pytest.mark.parametrize(("zone", "today"), [
        ("Asia/Kolkata", date(2026, 10, 13)),
        ("America/New_York", date(2026, 10, 12)),
    ])
    def test_the_bounds_put_a_task_on_its_utc_date(self, zone, today):
        """A task of my local tomorrow sits at the UTC midnight of that date,
        which is not before before_day. In New York that midnight comes before
        my local midnight, so a compare of instants showed tomorrow's task."""
        b = crm_activities.due_bounds(today, ZoneInfo(zone))
        tomorrow_task = datetime.combine(today + timedelta(days=1), time.min, tzinfo=UTC)
        today_task = datetime.combine(today, time.min, tzinfo=UTC)
        assert not tomorrow_task < b["before_day"]
        assert today_task < b["before_day"] and today_task >= b["today_day"]
        if zone == "America/New_York":
            assert tomorrow_task < b["before"], "the instant rule would show it today"

    def test_the_read_binds_the_task_rule_in_sql(self):
        sql = crm_activities._MY_DUE_SQL
        assert "CASE WHEN a.type = 'task' THEN CAST(:before_day AS timestamptz)" in sql
        assert "(a.type = 'task') AS all_day" in sql


# ── P2-1: a follow-up of today survives the cap ─────────────────────────────


class TestTodayFirst:
    def test_twenty_old_tasks_do_not_hide_a_call_due_in_an_hour(self, crm):
        today = _today(crm)
        fresh = {"record_kind": "contact", "record_id": "c-now", "label": "Now",
                 "due_at": crm.f.now + timedelta(hours=1), "subject": "call",
                 "all_day": False, "fresh": True}
        old = [_task(f"c-old-{n:02d}", today - timedelta(days=30 - n)) for n in range(20)]
        # The read gives the fresh rows first (the ORDER BY of the SQL).
        crm.due[ME] = [fresh, *old]
        found = _bound(lambda: feed._crm(member(*CRM_MEMBER)))
        ids = [s.signal_id for s in found.signals()]
        assert found.capped is True
        assert len(ids) == shell.PER_APP
        assert ids[0] == "crm:contact:c-now"

    def test_the_read_orders_the_fresh_rows_first(self):
        assert "ORDER BY fresh DESC, due_at" in crm_activities._MY_DUE_SQL


# ── P2-2: the days of a quiet deal ──────────────────────────────────────────


class TestTheDaysOfAQuietDeal:
    def test_the_first_day_past_the_limit_reads_one_more_day(self, crm):
        now = crm.f.now
        crm.quiet[ME] = [{"id": "d-q", "name": "Q", "stage": "Proposal",
                          "status_changed_at": now - timedelta(days=10, minutes=1),
                          "limit_days": 10}]
        found = _bound(lambda: feed._crm(member(*CRM_MEMBER)))
        [c] = feed.build_cards(found.signals(), now, _tz(crm.f), _today(crm))
        assert c["why"] == "In Proposal for 11 days. The stage limit is 10 days."

    def test_whole_days_stay_whole(self, crm):
        now = crm.f.now
        crm.quiet[ME] = [{"id": "d-q", "name": "Q", "stage": "Proposal",
                          "status_changed_at": now - timedelta(days=12), "limit_days": 10}]
        found = _bound(lambda: feed._crm(member(*CRM_MEMBER)))
        assert found.signals()[0].facts["stage_days"] == 12


# ── P2-3: the cache key follows the CRM kill switch ─────────────────────────


class TestTheCacheKeyFollowsTheKillSwitch:
    def _hash(self, monkeypatch, *, listed: str, orgs: str) -> str:
        from gateway.routes.shell import cache

        _list(monkeypatch, listed)
        crm_switch_on(monkeypatch, orgs)
        return cache.features_hash(member(*CRM_MEMBER))

    def test_a_delisted_org_gets_a_new_key_while_crm_is_listed(self, monkeypatch):
        on = self._hash(monkeypatch, listed="crm", orgs="*")
        off = self._hash(monkeypatch, listed="crm", orgs="another-org")
        assert on != off

    def test_with_crm_not_listed_the_switch_changes_no_key(self, monkeypatch):
        on = self._hash(monkeypatch, listed="tasks_followups", orgs="*")
        off = self._hash(monkeypatch, listed="tasks_followups", orgs="another-org")
        assert on == off
        empty_on = self._hash(monkeypatch, listed="", orgs="*")
        assert empty_on == self._hash(monkeypatch, listed="", orgs="another-org")


# ── P1-2: a folded deal_quiet keeps a dismissed card dismissed ──────────────


class TestAFoldKeepsADismissedDealDismissed:
    def test_a_dismissed_close_passed_card_stays_dismissed_when_quiet_folds_in(self):
        tz = ZoneInfo("UTC")
        now = datetime(2026, 10, 12, 12, 0, tzinfo=tz)
        today = now.date()
        day = state.Day(tz)
        late = feed._close_passed_signal(
            {"id": "d-x", "name": "X", "stage": "Proposal",
             "expected_close_date": today - timedelta(days=2)}, tz)
        quiet = feed._quiet_deal_signal(
            {"id": "d-x", "name": "X", "stage": "Proposal",
             "status_changed_at": now - timedelta(days=12), "limit_days": 10}, now)
        cards = feed.build_cards([late], now, tz, today)
        fp = state.join(cards, {}, state.Context(now, day))[0]["state_fp"]
        row = {"kind": "deal_close_passed", "state": "dismissed",
               "dismiss_reason": "dismiss", "fingerprint": fp, "visits": 0,
               "visits_day": None, "last_seen_at": None, "snoozed_until": None,
               "snooze_on_change": False}
        both = feed.build_cards([late, quiet], now, tz, today)
        assert both[0]["folded"] == ["deal_quiet"]
        assert not state.join(both, {"crm:deal:d-x": row}, state.Context(now, day))
