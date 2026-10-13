"""WS-58 MD-1a — GET /shell/feed, My Day as a feed (``my_day_feed.md``).

The feed reuses each app's OWN authorized read, as the needs feed does. The
R8 half is ``test_shell_feed_r8.py``. This file fences what the feed adds,
and each case is written so a wrong build fails it:

* **One registry** (§4.4, ``TestOneRegistry``). A source registers once. The
  needs view inside the feed answer is byte-equal to ``GET /shell/needs``
  at its own default limit, 30, whatever the feed's limit. No kind outside
  ``KIND_ORDER`` reaches the needs view.
* **One entity, one card** (§4.5, ``TestOneCardPerEntity``). An overdue task
  with a block that starts in 20 minutes and overlaps a second block is
  ONE card. The other two kinds are folded into it.
* **The card shape** of §14.1a, the template why lines, the groups, and the
  ways of the acts (§11.1): ``done`` and ``read`` tap, every other act opens.
* **Another member's block never reaches a card**, one case for each
  Calendar kind. The fakes answer by the member they are handed.
* **The two gates of the Calendar kinds** (§5.1): ``feature:tasks`` AND
  ``feature:projects``.
* **The budgets.** The needs providers run first, on today's budget. A slow
  feed-only provider never turns a needs source to ``failed``.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from acb_auth.permissions import EffectiveAccess
from acb_auth.roles import UserContext, UserRole
from fastapi import HTTPException
from gateway.routes.shell import feed, feed_rank
from gateway.routes.shell import needs as shell

from tests.unit.test_shell_needs import (
    LATER_TODAY,
    ME,
    NOW,
    ORG,
    OTHER,
    Fakes,
    _freeze,
    _install,
)

#: The features of a member who holds Projects, Email, and the Calendar pane.
FULL = ("projects", "email", "tasks")
#: The §14.1a keys of one card, and of the answer.
CARD_KEYS = {"signal_id", "app", "kind", "folded", "group", "score", "entity", "title",
             "why", "why_key", "facts", "fingerprint", "urgent_at", "expires_at", "at",
             "href", "acts",
             # The state fields of MD-2a (§14.1a).
             "state", "updated", "still_open", "visits", "state_fp"}
ANSWER_KEYS = {"cards", "groups", "needs", "sources", "capped"}
NEEDS_KINDS = set(shell.KIND_ORDER)
CALENDAR_CALLS = {"day_summary", "get_user_settings", "my_calendar", "get_me"}


def member(*features: str, email: str = ME) -> UserContext:
    return UserContext(email=email, role=UserRole.EMPLOYEE,
                       access=EffectiveAccess(role_granted=frozenset(
                           f"feature:{f}" for f in features)))


def admin(*features: str, email: str = ME) -> UserContext:
    return UserContext(email=email, role=UserRole.EMPLOYEE,
                       access=EffectiveAccess(role_granted=frozenset(
                           {*(f"feature:{f}" for f in features), shell.ADMIN_PERMISSION})))


def _a_daytime_zone(now: datetime) -> str:
    """A zone where it is now between 03:00 and 20:59, so the blocks of the
    seed (20 to 110 minutes from now) stay on the member's today, whatever
    the instant the suite reads."""
    for name in ("UTC", "Asia/Kolkata", "America/New_York", "Asia/Tokyo",
                 "America/Los_Angeles", "Pacific/Kiritimati", "Europe/Berlin"):
        if 3 <= now.astimezone(ZoneInfo(name)).hour <= 20:
            return name
    raise AssertionError("no zone has a daytime hour now")


def _block(task_id: str, title: str, start: datetime, minutes: int,
           disposition: str = "NEXT", importance: int | None = None) -> dict:
    return {"id": task_id, "title": title, "disposition": disposition,
            "importance": importance, "leveraged": False, "due_at": None,
            "scheduled_start": start.isoformat(),
            "scheduled_end": (start + timedelta(minutes=minutes)).isoformat()}


class FeedFakes(Fakes):
    """The needs fakes, plus the four Calendar reads. Each answers by the
    member it is handed, as the real one does, and records the call."""

    def __init__(self) -> None:
        super().__init__(NOW)
        # The needs suite's one fixed instant (``test_shell_needs.NOW``).
        # The fixture freezes the route's clock (``shell._now``) to it.
        now = self.now
        self.zone = _a_daytime_zone(now)
        #: The member's blocks, by member: the lens rows `my_calendar` reads.
        self.blocks: dict[str, list[dict]] = {
            # The fold seed: the overdue task `t-late` has a block in 20
            # minutes that overlaps a second block.
            ME: [_block("t-late", "Ship the extruder", now + timedelta(minutes=20), 60,
                        importance=3),
                 _block("t-draw", "Draw the bracket", now + timedelta(minutes=50), 60)],
            OTHER: [_block("t-theirs-a", "Their private block", now + timedelta(minutes=10), 60),
                    _block("t-theirs-b", "Their other block", now + timedelta(minutes=40), 60)],
        }
        self.capacity: dict[str, int] = {ME: 360, OTHER: 30}
        self.day_start: dict[str, int] = {ME: 7, OTHER: 7}
        self.work_days: dict[str, list[int]] = {ME: [1, 2, 3, 4, 5, 6, 7],
                                                OTHER: [1, 2, 3, 4, 5, 6, 7]}
        self.slow_calendar = 0.0
        #: The rows of ``my_day_card_state``, by member, then by signal id.
        #: The state join reads them through ``state._read_rows`` (MD-2a).
        self.state_rows: dict[str, dict[str, dict]] = {}
        self.state_reads: list[tuple[str, list[str]]] = []
        #: The order of the state writes: the claim, the locked read, the upsert.
        self.state_log: list[tuple[str, list[str]]] = []

    def _today(self):
        return self.now.astimezone(ZoneInfo(self.zone)).date()

    def _midnight(self) -> datetime:
        return datetime.combine(self._today(), datetime.min.time(), tzinfo=ZoneInfo(self.zone))

    async def my_due_tasks(self, user, *, limit):
        # The lens read reports the member's zone, the one Calendar reads too.
        answer = await super().my_due_tasks(user, limit=limit)
        return {**answer, "today": self._today().isoformat(), "timezone": self.zone}

    async def day_summary(self, date=None, *, user):
        await self._enter("day_summary", user, {"user": user, "date": date})
        if self.slow_calendar:
            await asyncio.sleep(self.slow_calendar)
        tz = ZoneInfo(self.zone)
        today = self._today()
        scheduled = []
        for b in self.blocks.get(user.email, []):
            start = datetime.fromisoformat(b["scheduled_start"])
            if start.astimezone(tz).date() != today or b["disposition"] in ("DONE", "TRASH"):
                continue  # `busy_window` keeps the open blocks of today
            scheduled.append({"id": b["id"], "title": b["title"], "start": b["scheduled_start"],
                              "end": b["scheduled_end"], "done": False, "fixed": False})
        return {"day": today.isoformat(), "capacity_mins": self.capacity.get(user.email, 360),
                "scheduled": scheduled, "unscheduled_count": 0, "overdue_count": 0,
                "one_thing": None, "estimate_over_pct": None}

    async def get_user_settings(self, *, user):
        await self._enter("get_user_settings", user, {"user": user})
        return SimpleNamespace(timezone=self.zone, day_start_hour=self.day_start.get(user.email, 7))

    async def my_calendar(self, start, end, include_done=False, *, user):
        await self._enter("my_calendar", user, {"user": user, "start": start, "end": end,
                                                "include_done": include_done})
        lo, hi = datetime.fromisoformat(start), datetime.fromisoformat(end)
        rows = [dict(b) for b in self.blocks.get(user.email, [])
                if lo <= datetime.fromisoformat(b["scheduled_start"]) < hi
                and (include_done or b["disposition"] not in ("DONE", "TRASH"))]
        return SimpleNamespace(rows=rows, total=len(rows))

    async def get_me(self, *, user):
        await self._enter("get_me", user, {"user": user})
        return SimpleNamespace(state="resolved", email=user.email,
                               person={"schedule": {"days": self.work_days.get(user.email)}})

    async def read_state(self, db, member, ids, *, lock=False):
        self.state_reads.append((member, list(ids)))
        self.state_log.append(("read_locked" if lock else "read", sorted(set(ids))))
        mine = self.state_rows.get(member, {})
        return {sid: dict(mine[sid]) for sid in ids if sid in mine}

    async def stored_zone(self, db, email):
        return self.zone


class _NoSession:
    """The state module's session, with no database: each read goes through
    ``FeedFakes.read_state``, and each write is recorded on ``writes``."""

    def __init__(self, fakes: FeedFakes) -> None:
        self.fakes = fakes

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, sql, params=None):
        sql = str(sql)
        assert "INSERT INTO my_day_card_state" in sql, sql
        if "DO NOTHING" in sql:
            # The claim: a placeholder for each card with no row, in order.
            mine = self.fakes.state_rows.setdefault(params["member"], {})
            ids = [params[k] for k in sorted((k for k in params if k.startswith("s")),
                                             key=lambda k: int(k[1:]))]
            self.fakes.state_log.append(("claim", ids))
            made = [sid for sid in ids if sid not in mine]
            for sid in made:
                mine[sid] = {"state": "seen", "fingerprint": None}
            return [(sid,) for sid in made]
        batch = params if isinstance(params, list) else [params]
        self.fakes.state_log.append(("upsert", [p["signal_id"] for p in batch]))
        for one in batch:
            row = {k: v for k, v in one.items() if k not in ("member", "signal_id")}
            self.fakes.state_rows.setdefault(one["member"], {})[one["signal_id"]] = row
        return SimpleNamespace(rowcount=len(batch))


@pytest.fixture()
def fakes(monkeypatch) -> FeedFakes:
    """The needs suite's own install, which also freezes ``shell._now`` to
    ``f.now``, plus the four Calendar reads."""
    f = _install(monkeypatch, FeedFakes())
    import gateway.routes.people.selfservice as selfservice
    import gateway.routes.tasks.calendar as calendar
    import gateway.routes.tasks.settings as settings
    from gateway.routes.projects import personal

    monkeypatch.setattr(calendar, "day_summary", f.day_summary)
    monkeypatch.setattr(settings, "get_user_settings", f.get_user_settings)
    monkeypatch.setattr(personal, "my_calendar", f.my_calendar)
    monkeypatch.setattr(selfservice, "get_me", f.get_me)
    from gateway.routes.shell import state

    monkeypatch.setattr(state, "_read_rows", f.read_state)
    monkeypatch.setattr(state, "_tenant_session", lambda: _NoSession(f))
    monkeypatch.setattr(personal, "stored_zone", f.stored_zone)
    return f


def _bound(coro_fn):
    from acb_common.db import bind_tenant, release_tenant

    async def _go():
        token = bind_tenant(ORG)
        try:
            return await coro_fn()
        finally:
            release_tenant(token)

    return asyncio.run(_go())


def run_feed(user: UserContext, limit: int = feed.DEFAULT_FEED_LIMIT) -> dict:
    return _bound(lambda: feed.shell_feed(limit=limit, user=user))


def run_needs(user: UserContext, limit: int = shell.DEFAULT_LIMIT) -> dict:
    return _bound(lambda: shell.shell_needs(limit=limit, user=user))


def card(answer: dict, signal_id: str) -> dict:
    found = [c for c in answer["cards"] if c["signal_id"] == signal_id]
    assert len(found) == 1, (signal_id, [c["signal_id"] for c in answer["cards"]])
    return found[0]


def text_of(answer: dict) -> str:
    return json.dumps(answer["cards"], default=str)


# ── §4.4 One registry ────────────────────────────────────────────────────────


class TestOneRegistry:
    def test_each_source_registers_once(self):
        assert list(shell.REGISTRY) == ["tasks", "projects", "email", "approvals", "calendar"]
        again = shell.Provider("tasks", ("projects",), shell.REGISTRY["tasks"].run,
                               frozenset({"overdue"}))
        with pytest.raises(ValueError, match="registers twice"):
            shell.register(again)
        assert shell.REGISTRY["tasks"] is not again

    def test_the_needs_subset_is_todays_four_in_todays_order(self):
        assert list(shell.PROVIDERS) == ["tasks", "projects", "email", "approvals"]
        assert [p.source for p in shell.needs_providers()] == list(shell.PROVIDERS)
        assert [p.source for p in shell.feed_only_providers()] == ["calendar"]
        for source, (feature, run) in shell.PROVIDERS.items():
            assert (feature, run) == (shell.REGISTRY[source].features[0],
                                      shell.REGISTRY[source].run)

    def test_a_needs_provider_may_give_only_the_needs_kinds(self, monkeypatch):
        monkeypatch.setattr(shell, "REGISTRY", {})
        with pytest.raises(ValueError, match="outside KIND_ORDER"):
            shell.register(shell.Provider("x", ("projects",), feed._calendar,
                                          frozenset({"block_soon"})))
        assert shell.REGISTRY == {}

    def test_a_needs_provider_never_registers_after_a_feed_only_one(self, monkeypatch):
        monkeypatch.setattr(shell, "REGISTRY", {})
        shell.register(shell.Provider("cal", ("tasks",), feed._calendar,
                                      frozenset({"block_soon"}), needs=False))
        with pytest.raises(ValueError, match="before every feed-only"):
            shell.register(shell.Provider("y", ("projects",), feed._calendar,
                                          frozenset({"overdue"})))

    def test_the_needs_view_of_a_fixed_seed_is_todays_needs_answer(self, fakes):
        user = admin(*FULL, "approvals")
        answer = run_feed(user)
        today = run_needs(user)
        assert answer["needs"] == today
        assert json.dumps(answer["needs"], default=str) == json.dumps(today, default=str)
        # Today's answer for this seed, row by row (navigation_shell.md §7.2).
        assert [i["id"] for i in today["items"]] == [
            "tasks:t-old", "tasks:t-late", "approvals:pa-old", "approvals:pa-new",
            "tasks:t-soon", "projects:n-2", "projects:n-1",
            "email:a-mine:th-old", "email:a-mine:th-new",
        ]
        assert set(today["sources"]) == {"tasks", "projects", "email", "approvals"}

    @pytest.mark.parametrize("limit", [1, 5, 40, 80, 500])
    def test_the_needs_view_is_at_the_needs_default_whatever_the_feed_limit(self, fakes, limit):
        # Contract (coordinator, 2026-10-13): the client keeps `needs` under
        # the key of `GET /shell/needs?limit=30`.
        for n in range(20):
            fakes.tasks[ME].append({"id": f"t-x{n:02d}", "title": f"Extra {n}",
                                    "project_name": "P", "due_at": (fakes.now - timedelta(
                                        hours=n + 1)).isoformat(),
                                    "completed_at": None, "disposition": "NEXT"})
        for n in range(15):
            fakes.notes[ME].append({"id": f"n-x{n:02d}", "task_id": f"t-y{n:02d}",
                                    "kind": "comment", "actor": "lead@x.test",
                                    "task_title": f"Other {n}", "task_number": None,
                                    "excerpt": None,
                                    "created_at": fakes.now - timedelta(minutes=n)})
        user = admin(*FULL, "approvals")
        answer = run_feed(user, limit=limit)
        assert answer["needs"] == run_needs(user, limit=shell.DEFAULT_LIMIT)
        # 15 tasks, 15 notifications, 2 replies and 2 approvals, cut to 30.
        assert answer["needs"]["total"] == 34
        assert answer["needs"]["count"] == shell.DEFAULT_LIMIT
        assert len(answer["cards"]) <= min(limit, feed.MAX_FEED_LIMIT)

    def test_no_kind_outside_kind_order_reaches_the_needs_view(self, fakes):
        user = member(*FULL)
        answer = run_feed(user)
        feed_kinds = {c["kind"] for c in answer["cards"]} | {
            k for c in answer["cards"] for k in c["folded"]}
        assert feed_kinds & feed.CALENDAR_KINDS, "non-vacuity: the feed holds Calendar kinds"
        assert {i["kind"] for i in answer["needs"]["items"]} <= NEEDS_KINDS
        fakes.calls.clear()
        alone = run_needs(user)
        assert {i["kind"] for i in alone["items"]} <= NEEDS_KINDS
        assert set(alone["sources"]) == {"tasks", "projects", "email", "approvals"}
        assert not {name for name, _, _ in fakes.calls} & CALENDAR_CALLS

    def test_each_signal_is_a_kind_its_provider_names(self, fakes):
        answer = run_feed(admin(*FULL, "approvals"))
        by_app = {"tasks": "tasks", "projects": "projects", "email": "email",
                  "approvals": "approvals", "calendar": "calendar"}
        for c in answer["cards"]:
            assert c["kind"] in shell.REGISTRY[by_app[c["app"]]].kinds
            assert c["kind"] in feed_rank.BASE


# ── §4.5 One entity, one card ───────────────────────────────────────────────


class TestOneCardPerEntity:
    def test_one_task_that_gives_three_kinds_is_one_card(self, fakes):
        answer = run_feed(member(*FULL))
        late = card(answer, "tasks:t-late")
        assert late["kind"] == "overdue"
        assert sorted(late["folded"]) == ["block_overlap", "block_soon"]
        assert late["app"] == "tasks"
        # Each folded kind is part of the card: its facts and a clause.
        assert late["facts"]["overlaps"] == ["Draw the bracket"]
        assert late["facts"]["block_start"] == fakes.blocks[ME][0]["scheduled_start"]
        assert "Its block starts in 20 min." in late["why"]
        assert "Its block overlaps Draw the bracket." in late["why"]
        assert [a["id"] for a in late["acts"]] == ["done", "move-tomorrow", "open-day"]

    def test_no_two_cards_share_an_entity(self, fakes):
        answer = run_feed(admin(*FULL, "approvals"))
        ids = [c["signal_id"] for c in answer["cards"]]
        assert len(ids) == len(set(ids))

    def test_the_other_task_of_an_overlap_carries_the_same_fact(self, fakes):
        fakes.tasks[ME].append({"id": "t-draw", "title": "Draw the bracket",
                                "project_name": "P",
                                "due_at": LATER_TODAY.isoformat(),
                                "completed_at": None, "disposition": "NEXT"})
        answer = run_feed(member(*FULL))
        draw = card(answer, "tasks:t-draw")
        assert draw["facts"]["overlaps"] == ["Ship the extruder"]
        assert draw["facts"]["overlap_ids"] == ["t-late"]
        assert "Its block overlaps Ship the extruder." in draw["why"]

    def test_a_notification_folds_into_its_tasks_card_and_keeps_its_read_act(self, fakes):
        fakes.notes[ME].append({"id": "n-late", "task_id": "t-late", "kind": "mention",
                                "actor": "ravi@x.test", "task_title": "Ship the extruder",
                                "task_number": 4, "excerpt": None,
                                "created_at": fakes.now - timedelta(minutes=5)})
        answer = run_feed(member(*FULL))
        late = card(answer, "tasks:t-late")
        assert "notification" in late["folded"]
        read = next(a for a in late["acts"] if a["id"] == "read")
        assert (read["way"], read["act"], read["act_ref"]) == ("tap", "read", "n-late")
        assert "ravi mentioned you on this task today." in late["why"]
        # The needs view keeps its own id for the notification.
        assert "projects:n-late" in [i["id"] for i in answer["needs"]["items"]]

    def test_a_notification_on_a_task_with_no_other_signal_is_its_own_card(self, fakes):
        answer = run_feed(member(*FULL))
        note = card(answer, "tasks:t-9")
        assert (note["kind"], note["app"], note["folded"]) == ("notification", "projects", [])
        assert note["why"].startswith("priya gave you this task")
        assert note["acts"][0] == {"id": "read", "label": "Mark read", "class": "A",
                                   "way": "tap", "act": "read", "act_ref": "n-1",
                                   "act_refs": ["n-1"]}

    def test_a_later_comment_never_hides_an_earlier_mention(self, fakes):
        # Review round 1, P1. A mention at 09:00 and a comment at 11:00 on one
        # task. One signal with one `read` act that marks BOTH read, and the
        # asking reason (the mention) sets the group and the why line.
        nine = datetime.combine(fakes._today(), datetime.min.time(),
                                tzinfo=ZoneInfo(fakes.zone)) + timedelta(hours=9)
        fakes.notes[ME] = [
            {"id": "n-mention", "task_id": "t-q", "kind": "mention", "actor": "ravi@x.test",
             "task_title": "Quote the housing", "task_number": 7, "excerpt": "Can you check?",
             "created_at": nine},
            {"id": "n-comment", "task_id": "t-q", "kind": "comment", "actor": "lena@x.test",
             "task_title": "Quote the housing", "task_number": 7, "excerpt": "Done on my side",
             "created_at": nine + timedelta(hours=2)},
        ]
        answer = run_feed(member(*FULL))
        quote = card(answer, "tasks:t-q")
        assert [c["signal_id"] for c in answer["cards"]].count("tasks:t-q") == 1
        assert (quote["kind"], quote["group"]) == ("notification", "needs_you")
        assert quote["why_key"] == "notification_mention"
        assert quote["why"].startswith("ravi mentioned you on this task")
        reads = [a for a in quote["acts"] if a["id"] == "read"]
        assert len(reads) == 1
        assert reads[0]["act_refs"] == ["n-comment", "n-mention"]  # newest first
        assert reads[0]["act_ref"] == "n-comment"
        # The needs view keeps one row for each notification.
        needs_ids = [i["id"] for i in answer["needs"]["items"]]
        assert "projects:n-mention" in needs_ids and "projects:n-comment" in needs_ids

    def test_comments_alone_stay_information(self, fakes):
        fakes.notes[ME] = [
            {"id": f"n-c{n}", "task_id": "t-q", "kind": "comment", "actor": "lena@x.test",
             "task_title": "Quote the housing", "task_number": 7, "excerpt": None,
             "created_at": fakes.now - timedelta(hours=n)} for n in (1, 2)]
        answer = run_feed(member(*FULL))
        quote = card(answer, "tasks:t-q")
        assert (quote["group"], quote["why_key"]) == ("fyi", "notification_comment")
        read = next(a for a in quote["acts"] if a["id"] == "read")
        assert read["act_refs"] == ["n-c1", "n-c2"]


# ── §14.1a The shape, the templates and the acts ────────────────────────────


class TestTheCardShape:
    def test_the_answer_and_each_card_have_the_keys_of_14_1a(self, fakes):
        answer = run_feed(admin(*FULL, "approvals"))
        assert set(answer) == ANSWER_KEYS
        assert answer["cards"], "non-vacuity"
        for c in answer["cards"]:
            assert set(c) == CARD_KEYS, c["signal_id"]
            assert set(c["entity"]) == {"type", "id", "label"}
            assert isinstance(c["score"], int)
            assert c["why"] and c["why_key"]
            assert len(c["fingerprint"]) == 8

    def test_the_groups_are_the_md1_four_in_order_and_count_the_cards(self, fakes):
        answer = run_feed(admin(*FULL, "approvals"))
        ids = [g["id"] for g in answer["groups"]]
        assert ids == [g for g in feed_rank.GROUPS if g in ids]
        assert set(ids) <= {"now", "needs_you", "ready", "fyi"}
        for g in answer["groups"]:
            assert g["count"] == sum(1 for c in answer["cards"] if c["group"] == g["id"]) > 0
        # Rule 1: the cards come group by group.
        order = [feed_rank.GROUPS.index(c["group"]) for c in answer["cards"]]
        assert order == sorted(order)

    def test_the_app_ids_and_the_sources(self, fakes):
        fakes.capacity[ME] = 60  # a day card, so one card is the Calendar's own
        answer = run_feed(admin(*FULL, "approvals"))
        assert {c["app"] for c in answer["cards"]} == {
            "tasks", "projects", "email", "approvals", "calendar"}
        assert answer["sources"] == {"tasks": "ok", "projects": "ok", "email": "ok",
                                     "approvals": "ok", "calendar": "ok"}

    def test_done_and_read_tap_and_every_other_act_opens(self, fakes):
        answer = run_feed(admin(*FULL, "approvals"))
        acts = [a for c in answer["cards"] for a in c["acts"]]
        assert {a["way"] for a in acts} == {"tap", "open"}
        for a in acts:
            if a["way"] == "tap":
                assert a["id"] in ("done", "read") and a["act"] == a["id"]
                assert a["act_ref"] and "href" not in a
            else:
                assert a["href"].startswith("/") and "act" not in a
            assert a["class"] in ("A", "C")
        for c in answer["cards"]:
            assert len({a["id"] for a in c["acts"]}) == len(c["acts"])

    def test_an_approval_card_is_class_c_and_opens_the_queue(self, fakes):
        answer = run_feed(admin(*FULL, "approvals"))
        ask = card(answer, "approvals:pa-old")
        assert ask["acts"] == [{"id": "open-approvals", "label": "Open Approvals",
                                "class": "C", "way": "open", "href": "/approvals"}]
        assert ask["why_key"] == "proposed"
        assert ask["why"].startswith("Proposed by priya")

    def test_the_why_line_of_each_needs_kind(self, fakes):
        answer = run_feed(admin(*FULL, "approvals"))
        old = card(answer, "tasks:t-old")
        assert old["why_key"] == "days_late" and old["why"].endswith("3 days late.")
        assert old["facts"]["days_late"] == 3
        soon = card(answer, "tasks:t-soon")
        assert soon["kind"] == "due_today" and soon["why_key"] in ("due_today", "due_soon")
        mail = card(answer, "email:a-mine:th-old")
        assert mail["why_key"] == "asked" and mail["why"].startswith("Asked on ")
        assert mail["href"] == "/email?email=m-old&account=a-mine"
        assert mail["acts"] == [{"id": "open-thread", "label": "Open thread", "class": "A",
                                 "way": "open", "href": mail["href"]}]

    def test_the_overdue_weight_reads_the_d78_level(self, fakes):
        fakes.tasks[ME][0]["importance"] = 3
        fakes.tasks[ME][0]["leveraged"] = True
        answer = run_feed(member(*FULL))
        late = card(answer, "tasks:t-late")
        assert late["facts"]["priority"] == "critical"
        assert late["score"] >= 60 + 60 + 24


class TestTheMostUrgentGroup:
    """Verifier F1. A card takes the MOST urgent old-path group of all its
    kinds (now > needs_you > ready > fyi). The main kind still sets the
    title, the lead of the why line and the base of the score."""

    def _comment(self, fakes, task_id: str, title: str) -> None:
        fakes.notes[ME] = [{"id": "n-c", "task_id": task_id, "kind": "comment",
                            "actor": "lena@x.test", "task_title": title, "task_number": 9,
                            "excerpt": None, "created_at": fakes.now - timedelta(minutes=5)}]

    def test_a_comment_on_a_task_whose_block_starts_soon_stays_in_now(self, fakes):
        fakes.blocks[ME] = [_block("t-solo", "Read the spec", fakes.now + timedelta(minutes=20),
                                   30)]
        self._comment(fakes, "t-solo", "Read the spec")
        answer = run_feed(member(*FULL))
        solo = card(answer, "tasks:t-solo")
        assert (solo["kind"], solo["folded"]) == ("notification", ["block_soon"])
        assert solo["group"] == "now"
        assert solo["why_key"] == "notification_comment"
        assert solo["why"].startswith("lena commented on this task")
        assert "Its block starts in 20 min." in solo["why"]
        assert solo["score"] >= feed_rank.BASE["notification"]

    def test_a_comment_on_an_overlap_today_stays_in_now(self, fakes):
        fakes.blocks[ME] = [
            _block("t-a", "Write the plan", fakes.now + timedelta(minutes=40), 60),
            _block("t-b", "Review the plan", fakes.now + timedelta(minutes=70), 60)]
        self._comment(fakes, "t-a", "Write the plan")
        answer = run_feed(member(*FULL))
        a = card(answer, "tasks:t-a")
        assert (a["kind"], a["folded"], a["group"]) == ("notification", ["block_overlap"], "now")

    def test_a_comment_alone_stays_information(self, fakes):
        # Non-vacuity: with no folded kind the comment is still `fyi`.
        fakes.blocks[ME] = []
        self._comment(fakes, "t-solo", "Read the spec")
        assert card(run_feed(member(*FULL)), "tasks:t-solo")["group"] == "fyi"


class TestTheFingerprint:
    """Review round 1, P2. The fingerprint hashes stable facts only (§14.1a):
    the entity, the kind, the folded kinds, the due time, the block times,
    the overlap ids, the notification ids and the waiting time. A countdown
    such as ``minutes`` or ``days_late`` never goes in it."""

    def _poll(self, fakes, monkeypatch, at: datetime) -> dict:
        _freeze(monkeypatch, at)  # the route's one clock, `shell._now`
        try:
            return run_feed(member(*FULL))
        finally:
            _freeze(monkeypatch, fakes.now)

    def test_two_polls_a_minute_apart_give_the_same_fingerprint(self, fakes, monkeypatch):
        first = card(self._poll(fakes, monkeypatch, fakes.now), "tasks:t-late")
        second = card(self._poll(fakes, monkeypatch, fakes.now + timedelta(minutes=1)),
                      "tasks:t-late")
        # Non-vacuity: the countdown moved between the two polls.
        assert first["facts"]["minutes"] != second["facts"]["minutes"]
        assert "block_soon" in first["folded"] and "block_soon" in second["folded"]
        assert first["fingerprint"] == second["fingerprint"]

    def test_a_late_day_more_keeps_the_fingerprint(self):
        now = datetime(2026, 10, 13, 10, 0, tzinfo=UTC)
        cards = []
        for late in (2, 3):
            signal = shell.Signal(
                signal_id="tasks:t-1", app="tasks", kind="overdue",
                entity={"type": "task", "id": "t-1", "label": "A"}, title="A",
                facts={"due_at": "2026-10-10T17:00:00+00:00", "days_late": late},
                href="/projects?task=t-1", acts=(),
            )
            cards.append(feed.build_cards([signal], now, ZoneInfo("UTC"), now.date())[0])
        assert cards[0]["fingerprint"] == cards[1]["fingerprint"]

    def test_a_changed_due_date_gives_a_new_fingerprint(self, fakes):
        before = card(run_feed(member(*FULL)), "tasks:t-late")
        moved = next(t for t in fakes.tasks[ME] if t["id"] == "t-late")
        moved["due_at"] = (datetime.fromisoformat(moved["due_at"])
                           - timedelta(hours=1)).isoformat()
        after = card(run_feed(member(*FULL)), "tasks:t-late")
        assert before["fingerprint"] != after["fingerprint"]

    def test_a_new_unread_notification_gives_a_new_fingerprint(self, fakes):
        before = card(run_feed(member(*FULL)), "tasks:t-9")
        fakes.notes[ME].append({"id": "n-new", "task_id": "t-9", "kind": "comment",
                                "actor": "lena@x.test", "task_title": "Fix the hopper",
                                "task_number": 12, "excerpt": None, "created_at": fakes.now})
        after = card(run_feed(member(*FULL)), "tasks:t-9")
        assert before["fingerprint"] != after["fingerprint"]


class TestTheBlockTimeLink:
    """Review round 1, P2. "Block time" opens the Calendar pane, so a member
    without ``feature:tasks`` never gets it."""

    def test_a_member_without_the_calendar_pane_gets_no_block_time(self, fakes):
        answer = run_feed(member("projects", "email"))
        soon = card(answer, "tasks:t-soon")
        assert soon["kind"] == "due_today"
        assert "block-time" not in [a["id"] for a in soon["acts"]]
        assert not [a for c in answer["cards"] for a in c["acts"]
                    if a.get("href") == "/calendar"]

    def test_a_member_with_the_calendar_pane_gets_it(self, fakes):
        soon = card(run_feed(member(*FULL)), "tasks:t-soon")
        assert "block-time" in [a["id"] for a in soon["acts"]]


# ── §5.1 The Calendar kinds ─────────────────────────────────────────────────


class TestTheCalendarKinds:
    def test_block_soon_alone_is_a_now_card_that_opens_the_task(self, fakes):
        fakes.blocks[ME] = [_block("t-solo", "Read the spec", fakes.now + timedelta(minutes=15),
                                   30)]
        answer = run_feed(member(*FULL))
        solo = card(answer, "tasks:t-solo")
        assert (solo["kind"], solo["app"], solo["group"]) == ("block_soon", "calendar", "now")
        assert solo["why"].startswith("Starts in 15 min, at ")
        assert solo["acts"] == [{"id": "open-task", "label": "Open task", "class": "A",
                                 "way": "open", "href": "/projects?task=t-solo"}]

    def test_a_block_more_than_30_minutes_away_is_not_soon(self, fakes):
        fakes.blocks[ME] = [_block("t-far", "Later work", fakes.now + timedelta(minutes=45), 30)]
        answer = run_feed(member(*FULL))
        assert "tasks:t-far" not in [c["signal_id"] for c in answer["cards"]]

    def test_an_overlap_later_this_week_needs_you_and_opens_the_day(self, fakes):
        later = fakes.now + timedelta(days=3)
        fakes.blocks[ME] = [_block("t-a", "Write the plan", later, 60),
                            _block("t-b", "Review the plan", later + timedelta(minutes=30), 60)]
        answer = run_feed(member(*FULL))
        a = card(answer, "tasks:t-a")
        assert (a["kind"], a["group"], a["folded"]) == ("block_overlap", "needs_you", [])
        # MD-1b visual review, item 3: the time is where the OVERLAP starts,
        # which is the second block's start, never the first block's.
        overlap_at = (later + timedelta(minutes=30)).astimezone(ZoneInfo(fakes.zone)).strftime("%H:%M")
        assert a["why"].startswith(f"It overlaps Review the plan. They overlap from {overlap_at} ")
        assert "Both are set" not in a["why"]
        assert a["acts"] == [{"id": "open-day", "label": "Open the day", "class": "A",
                              "way": "open", "href": "/calendar"}]
        # One overlap, one card: the block that starts first carries it.
        assert "tasks:t-b" not in [c["signal_id"] for c in answer["cards"]]

    def test_the_why_clock_is_24_hour(self):
        # MD-1b visual review, item 5. Today and Calendar draw 24-hour times,
        # so the feed's why lines do too: "17:05", "09:00", never "5:05 PM".
        utc = ZoneInfo("UTC")
        assert feed._clock(datetime(2026, 10, 13, 17, 5, tzinfo=utc), utc) == "17:05"
        assert feed._clock(datetime(2026, 10, 13, 9, 0, tzinfo=utc), utc) == "09:00"
        assert feed._clock(datetime(2026, 10, 13, 0, 0, tzinfo=utc), utc) == "00:00"
        assert feed._clock(None, utc) == ""

    def test_an_overlap_that_ended_is_left_out(self, fakes):
        past = fakes.now - timedelta(hours=3)
        fakes.blocks[ME] = [_block("t-a", "A", past, 60), _block("t-b", "B", past, 60)]
        answer = run_feed(member(*FULL))
        assert not [c for c in answer["cards"] if c["kind"] == "block_overlap"]

    def test_a_done_block_overlaps_nothing(self, fakes):
        later = fakes.now + timedelta(days=2)
        fakes.blocks[ME] = [_block("t-a", "A", later, 60, disposition="DONE"),
                            _block("t-b", "B", later, 60)]
        answer = run_feed(member(*FULL))
        assert not [c for c in answer["cards"] if c["kind"] == "block_overlap"]

    def test_day_overbooked_compares_the_planned_minutes_with_capacity(self, fakes):
        fakes.capacity[ME] = 60
        answer = run_feed(member(*FULL))
        day = card(answer, f"calendar:{fakes._today().isoformat()}")
        assert (day["kind"], day["group"], day["app"]) == ("day_overbooked", "needs_you",
                                                           "calendar")
        assert day["facts"]["planned_mins"] == 120 and day["facts"]["capacity_mins"] == 60
        assert day["why"] == "2 h planned in a 1 h day."
        assert day["acts"][0]["href"] == "/calendar"

    def test_a_day_inside_its_capacity_is_not_overbooked(self, fakes):
        answer = run_feed(member(*FULL))
        assert not [c for c in answer["cards"] if c["kind"] == "day_overbooked"]

    def test_no_plan_yet_fires_on_a_working_day_after_the_grace(self, fakes, monkeypatch):
        monkeypatch.setattr(feed, "PLAN_GRACE", timedelta(0))
        fakes.blocks[ME] = []
        fakes.day_start[ME] = 0
        answer = run_feed(member(*FULL))
        day = card(answer, f"calendar:{fakes._today().isoformat()}")
        assert (day["kind"], day["group"]) == ("no_plan_yet", "needs_you")
        # One clock on one screen: 24-hour, as Calendar and Today draw it.
        assert day["why"] == "Your day started at 00:00, and nothing is on the calendar."
        assert day["acts"] == [{"id": "plan-day", "label": "Plan my day", "class": "A",
                                "way": "open", "href": "/calendar"}]

    def test_no_plan_yet_waits_60_minutes_after_the_day_starts(self, fakes):
        fakes.blocks[ME] = []
        # The day started this hour, so the 60 minutes are not over.
        fakes.day_start[ME] = fakes.now.astimezone(ZoneInfo(fakes.zone)).hour
        answer = run_feed(member(*FULL))
        assert not [c for c in answer["cards"] if c["kind"] == "no_plan_yet"]

    @pytest.mark.parametrize("stored", ["none", "missing"])
    def test_an_unset_day_start_reads_as_7_not_midnight(self, fakes, monkeypatch, stored):
        # Verifier F2. The column default is 7 (migration 77). At 05:00 the
        # member's day has not started, so no_plan_yet stays quiet.
        five = datetime.combine(fakes._today(), datetime.min.time(),
                                tzinfo=ZoneInfo(fakes.zone)) + timedelta(hours=5)

        _freeze(monkeypatch, five)  # the route's one clock, `shell._now`
        fakes.blocks[ME] = []
        fakes.day_start[ME] = None
        if stored == "missing":
            async def no_hour(*, user):
                await fakes._enter("get_user_settings", user, {"user": user})
                return SimpleNamespace(timezone=fakes.zone)

            import gateway.routes.tasks.settings as settings
            monkeypatch.setattr(settings, "get_user_settings", no_hour)
        answer = run_feed(member(*FULL))
        assert answer["sources"]["calendar"] == "ok"
        assert not [c for c in answer["cards"] if c["kind"] == "no_plan_yet"]

    def test_a_stored_midnight_start_still_counts(self, fakes, monkeypatch):
        # Non-vacuity for the case above: 0 is a real hour, and at 05:00 the
        # grace of a midnight start is over.
        five = datetime.combine(fakes._today(), datetime.min.time(),
                                tzinfo=ZoneInfo(fakes.zone)) + timedelta(hours=5)

        _freeze(monkeypatch, five)  # the route's one clock, `shell._now`
        fakes.blocks[ME] = []
        fakes.day_start[ME] = 0
        answer = run_feed(member(*FULL))
        assert [c for c in answer["cards"] if c["kind"] == "no_plan_yet"]

    def test_no_plan_yet_is_quiet_on_a_day_off(self, fakes, monkeypatch):
        monkeypatch.setattr(feed, "PLAN_GRACE", timedelta(0))
        fakes.blocks[ME] = []
        fakes.day_start[ME] = 0
        off = fakes._today().isoweekday()
        fakes.work_days[ME] = [d for d in range(1, 8) if d != off]
        answer = run_feed(member(*FULL))
        assert not [c for c in answer["cards"] if c["kind"] == "no_plan_yet"]

    def test_a_day_whose_plan_is_all_done_is_not_no_plan(self, fakes, monkeypatch):
        monkeypatch.setattr(feed, "PLAN_GRACE", timedelta(0))
        fakes.day_start[ME] = 0
        start = fakes._midnight()
        fakes.blocks[ME] = [_block("t-done", "Done early", start + timedelta(minutes=1), 5,
                                   disposition="DONE")]
        answer = run_feed(member(*FULL))
        assert not [c for c in answer["cards"] if c["kind"] == "no_plan_yet"]

    def test_a_failing_working_day_read_costs_only_no_plan_yet(self, fakes, monkeypatch):
        monkeypatch.setattr(feed, "PLAN_GRACE", timedelta(0))
        fakes.blocks[ME] = []
        fakes.day_start[ME] = 0
        fakes.fail["get_me"] = RuntimeError("people down")
        answer = run_feed(member(*FULL))
        assert answer["sources"]["calendar"] == "ok"
        assert not [c for c in answer["cards"] if c["kind"] == "no_plan_yet"]

    def test_the_week_read_is_capped_at_15_signals(self, fakes):
        start = fakes.now + timedelta(days=2)
        fakes.blocks[ME] = [_block(f"t-{n:02d}", f"Block {n}", start + timedelta(minutes=n), 120)
                            for n in range(20)]
        answer = run_feed(member(*FULL))
        assert "calendar" in answer["capped"]
        assert len([c for c in answer["cards"] if c["app"] == "calendar"]) == shell.PER_APP

    def test_the_week_read_is_the_members_next_7_days_from_their_midnight(self, fakes):
        run_feed(member(*FULL))
        call = next(kw for n, _, kw in fakes.calls if n == "my_calendar")
        start, end = datetime.fromisoformat(call["start"]), datetime.fromisoformat(call["end"])
        assert start == fakes._midnight()
        assert end - start == timedelta(days=feed.WEEK_DAYS)
        assert call["include_done"] is True


class TestAnotherMembersBlocksNeverArrive:
    """One case for each Calendar kind. OTHER's blocks would make each kind
    fire for ME if a read widened the member."""

    def _mine_only(self, answer: dict) -> None:
        got = text_of(answer)
        assert "t-theirs" not in got and "Their" not in got

    def test_block_soon(self, fakes):
        # OTHER's block starts in 10 minutes. ME's starts in 20.
        answer = run_feed(member(*FULL))
        soon = [c["signal_id"] for c in answer["cards"]
                if c["kind"] == "block_soon" or "block_soon" in c["folded"]]
        assert soon == ["tasks:t-late"]
        self._mine_only(answer)

    def test_block_overlap(self, fakes):
        answer = run_feed(member(*FULL))
        self._mine_only(answer)
        overlaps = [c["facts"].get("overlaps") for c in answer["cards"]]
        assert ["Draw the bracket"] in overlaps  # non-vacuity: mine is there

    def test_day_overbooked(self, fakes):
        # OTHER plans 120 minutes in a 30-minute day. ME plans 120 in 360.
        answer = run_feed(member(*FULL))
        assert not [c for c in answer["cards"] if c["kind"] == "day_overbooked"]
        theirs = run_feed(member(*FULL, email=OTHER))
        assert [c for c in theirs["cards"] if c["kind"] == "day_overbooked"]

    def test_no_plan_yet(self, fakes, monkeypatch):
        # ME has no block today. OTHER has two. ME still has no plan.
        monkeypatch.setattr(feed, "PLAN_GRACE", timedelta(0))
        fakes.blocks[ME] = []
        fakes.day_start[ME] = 0
        answer = run_feed(member(*FULL))
        assert [c for c in answer["cards"] if c["kind"] == "no_plan_yet"]
        self._mine_only(answer)

    def test_every_calendar_read_is_the_members_own(self, fakes):
        run_feed(member(*FULL))
        for name, user, _ in fakes.calls:
            if name in CALENDAR_CALLS:
                assert user.email == ME

    def test_the_other_members_feed_holds_their_own_blocks(self, fakes):
        # Non-vacuity: the blocks held out above are real for their owner.
        theirs = run_feed(member(*FULL, email=OTHER))
        a = card(theirs, "tasks:t-theirs-a")
        assert a["kind"] == "block_overlap" and "block_soon" in a["folded"]
        assert "Ship the extruder" not in text_of(theirs)


class TestTheCalendarGates:
    def test_a_member_without_the_calendar_pane_gets_no_calendar_card(self, fakes):
        answer = run_feed(member("projects", "email"))
        assert answer["sources"]["calendar"] == "absent"
        assert not [c for c in answer["cards"] if c["app"] == "calendar"]
        assert not {c["kind"] for c in answer["cards"]} & feed.CALENDAR_KINDS
        assert not {n for n, _, _ in fakes.calls} & CALENDAR_CALLS
        assert answer["sources"]["tasks"] == "ok"  # non-vacuity: the rest ran

    def test_a_member_without_projects_gets_no_calendar_card(self, fakes):
        answer = run_feed(member("tasks", "email"))
        assert answer["sources"]["calendar"] == "absent"
        assert not [c for c in answer["cards"] if c["app"] == "calendar"]
        # The state join (MD-2a) reads the day hours of a Calendar holder
        # once (my_day_feed.md §6.8 item 8). No other Calendar read runs.
        calls = [n for n, _, _ in fakes.calls]
        assert not set(calls) & (CALENDAR_CALLS - {"get_user_settings"})
        assert calls.count("get_user_settings") == 1

    def test_a_member_with_both_gets_the_calendar(self, fakes):
        answer = run_feed(member("tasks", "projects"))
        assert answer["sources"]["calendar"] == "ok"
        assert [c for c in answer["cards"] if c["app"] == "calendar"
                or set(c["folded"]) & feed.CALENDAR_KINDS]

    def test_a_signed_out_caller_is_refused(self, fakes):
        with pytest.raises(HTTPException) as e:
            run_feed(UserContext(email=None, role=UserRole.EMPLOYEE))
        assert e.value.status_code == 401


class TestTheBudgets:
    def test_the_needs_providers_run_first_in_todays_order(self, fakes):
        run_feed(admin(*FULL, "approvals"))
        names = [n for n, _, _ in fakes.calls]
        first_calendar = min(names.index(n) for n in CALENDAR_CALLS if n in names)
        for needs_call in ("my_due_tasks", "list_notifications", "feed_mailboxes",
                           "read_pending"):
            assert names.index(needs_call) < first_calendar
        assert names.index("my_due_tasks") < names.index("list_notifications") \
            < names.index("feed_mailboxes") < names.index("read_pending")

    def test_a_slow_feed_only_provider_never_fails_a_needs_source(self, fakes, monkeypatch):
        monkeypatch.setattr(feed, "FEED_PROVIDER_TIMEOUT_S", 0.05)
        fakes.slow_calendar = 5.0
        user = admin(*FULL, "approvals")
        answer = run_feed(user)
        assert answer["sources"]["calendar"] == "failed"
        assert {answer["sources"][k] for k in shell.PROVIDERS} == {"ok"}
        assert answer["needs"]["sources"] == {k: "ok" for k in shell.PROVIDERS}
        assert answer["needs"] == run_needs(user)

    def test_the_feed_only_budget_is_its_own(self, fakes, monkeypatch):
        # The needs run spends all of its budget. The Calendar still runs.
        monkeypatch.setattr(shell, "TOTAL_BUDGET_S", 0.0)
        answer = run_feed(member(*FULL))
        assert set(answer["needs"]["sources"].values()) == {"failed", "absent"}
        assert answer["sources"]["calendar"] == "ok"

    def test_a_failing_needs_provider_reads_failed_in_both(self, fakes):
        fakes.fail["my_due_tasks"] = RuntimeError("db down")
        answer = run_feed(member(*FULL))
        assert answer["sources"]["tasks"] == "failed"
        assert answer["needs"]["sources"]["tasks"] == "failed"
        assert not [c for c in answer["cards"] if c["app"] == "tasks"]

    def test_a_failing_signal_builder_costs_only_the_feed(self, fakes, monkeypatch):
        def broken(*_a, **_k):
            raise RuntimeError("template bug")

        monkeypatch.setattr(shell, "_task_signals", broken)
        answer = run_feed(member(*FULL))
        assert answer["sources"]["tasks"] == "failed"
        assert answer["needs"]["sources"]["tasks"] == "ok"
        assert "tasks:t-late" in [i["id"] for i in answer["needs"]["items"]]

    def test_the_needs_route_never_builds_a_signal(self, fakes, monkeypatch):
        def broken(*_a, **_k):
            raise AssertionError("the needs route ran feed code")

        for name in ("_task_signals", "_note_signals", "_thread_signal"):
            monkeypatch.setattr(shell, name, broken)
        answer = run_needs(admin(*FULL, "approvals"))
        assert set(answer["sources"].values()) == {"ok"}


class TestTheCallsStayInStep:
    def test_the_day_summary_takes_a_date_and_the_member(self):
        from gateway.routes.tasks.calendar import day_summary

        assert list(inspect.signature(day_summary).parameters) == ["date", "user"]

    def test_the_settings_read_takes_only_the_member(self):
        from gateway.routes.tasks.settings import get_user_settings

        assert list(inspect.signature(get_user_settings).parameters) == ["user"]

    def test_the_lens_week_read_takes_a_window_and_the_member(self):
        from gateway.routes.projects.personal import my_calendar

        assert list(inspect.signature(my_calendar).parameters) == [
            "start", "end", "include_done", "user"]

    def test_the_self_read_takes_only_the_member(self):
        from gateway.routes.people.selfservice import get_me

        assert list(inspect.signature(get_me).parameters) == ["user"]

    def test_the_feed_reads_no_clock_but_the_needs_one(self):
        # One clock for the whole feed (main #951): `needs._now`, which the
        # suites freeze. A clock of the feed's own would escape the freeze.
        for module in (feed, feed_rank):
            source = inspect.getsource(module)
            for clock in ("datetime.now(", "datetime.utcnow(", "date.today(", "time.time("):
                assert clock not in source, (module.__name__, clock)
        assert inspect.getsource(feed).count("needs._now()") == 2

    def test_the_calendar_provider_opens_no_session_of_its_own(self):
        source = inspect.getsource(feed)
        for idiom in ("_tenant_session", "tenant_session(", "get_db(", "create_engine"):
            assert idiom not in source.replace("own tenant session", ""), idiom


class TestTheRouteIsMounted:
    def test_the_gateway_serves_shell_feed(self, monkeypatch):
        # `main.py` mounts every router inside `try/except: pass`, so an import
        # error would hide the route. This finds it.
        monkeypatch.setenv("GATEWAY_INTERNAL_TOKEN", "test-internal-token")
        from fastapi.routing import iter_route_contexts
        from gateway.main import app

        paths = {getattr(c, "path", None) for c in iter_route_contexts(app.routes)}
        assert "/shell/feed" in paths
        assert "/shell/needs" in paths  # non-vacuity: the walk sees the router
