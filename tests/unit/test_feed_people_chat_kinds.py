"""WS-58 MD-3f — people and chat in My Day (``my_day_feed.md`` §5.9 items 2
to 7, 10.3, 10.5 and 14).

The source ``people`` gives ``colleague_out``: a co-assignee of a task with
a block today is away all day today. The source ``chat`` gives
``agent_asks``: a question of a run waits for the member, in a room where
the member can still send. The R8 half, with the real SQL of
``absences.away_on`` and of the chat store, is
``test_feed_people_chat_kinds_r8.py``. This file fences what the feed does
with the rows:

* **Dark by the list.** With the two sources not in ``MY_DAY_PROVIDERS``,
  no read runs and the answer has no new byte (done-when 1).
* **Each kind gives its card** with the ``BASE``, group, ``why_key``, text,
  act and label of §5.9 (done-when 2).
* **The HR gate** (done-when 3 and 4): a member without
  ``admin:members:read`` gets ``people`` as ``absent``, and a spy shows that
  the provider never ran. ``away_on`` itself raises 403.
* **The negatives** (done-when 5, 6, 8, 9, 10 and 11): a ``partial``
  absence, my own absence, an agent co-assignee, a question of another
  member, a room where I cannot send, ``CHAT_DURABLE_ASKS`` off, and no
  question text on the card.
* **The bumps** (done-when 12): the admin absence writes bump the org, and
  an insert and a settle of a question bump the member. Each write still
  succeeds when the bump raises, hangs, or Redis is down.
* **The reads** (done-when 13): ``people`` makes 3 reads at most, and
  ``chat`` 1, plus 1 for each thread, 15 threads at most.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException
from gateway.routes.shell import feed, feed_rank, state
from gateway.routes.shell import needs as shell

from tests.unit.test_shell_feed import (  # noqa: F401  (fixtures by name)
    FULL,
    FeedFakes,
    _bound,
    _list,
    admin,
    card,
    fakes,
    member,
    run_feed,
)
from tests.unit.test_shell_needs import ME, ORG

pending_ask = pytest.importorskip("orchestrator.pending_ask")

#: The features of a member who holds both sources.
EVERY = (*FULL, "people", "chat")
COLL = "priya@customer.example"
COLL2 = "ravi@customer.example"
AGENT = "agent:projects-assistant"
QUESTION = "Archive the zebra project?"


def hr(*features: str) -> object:
    """A member with the HR grant, in the org."""
    return dataclasses.replace(admin(*features), organization_id=ORG)


def plain(*features: str) -> object:
    """A member without the HR grant, in the org."""
    return dataclasses.replace(member(*features), organization_id=ORG)


class Away:
    """``absences.away_on`` as a fake that keeps the HR gate of the real
    one, and records each call. ``rows`` holds the answer by address."""

    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}
        self.calls: list[tuple[list[str], object]] = []

    async def away_on(self, user, emails, day):
        from gateway.routes.people.absences import can_read_hr_fields

        if not can_read_hr_fields(user):
            raise HTTPException(status_code=403, detail="HR")
        self.calls.append((sorted(emails), day))
        return {e: dict(self.rows[e]) for e in emails if e in self.rows}


@pytest.fixture()
def away(fakes, monkeypatch) -> Away:  # noqa: F811
    from gateway.routes.people import absences

    a = Away()
    monkeypatch.setattr(absences, "away_on", a.away_on)
    return a


def _today(f: FeedFakes):
    return f.now.astimezone(ZoneInfo(f.zone)).date()


def _share(f: FeedFakes, *people: str) -> None:
    """My first block of today names ``people`` as co-assignees."""
    f.blocks[ME][0]["assignees"] = [ME, *people]


class Asks:
    """The chat store (``pending_ask.waiting_asks``) and the room check
    (``agent._resolve_room``), each recording its call. ``leaky`` makes the
    store ignore the member, so the provider's own check is what holds."""

    def __init__(self, f: FeedFakes) -> None:
        self.f = f
        self.rows: list[dict] = []
        self.can_send: dict[str, bool] = {}
        self.reads = 0
        self.rooms: list[str] = []
        self.error: Exception | None = None
        self.leaky = False

    def add(self, thread: str, *, actor: str = ME, agent: str | None = "projects-assistant",
            minutes_ago: int = 30, can_send: bool = True) -> str:
        rid = uuid.uuid4().hex
        at = self.f.now - timedelta(minutes=minutes_ago)
        self.rows.append({
            "request_id": rid, "thread_id": thread, "actor_email": actor,
            "agent_name": agent, "kind": "questions", "question": QUESTION,
            "payload": {"request_id": rid, "title": QUESTION}, "state": "open",
            "asked_at": at.isoformat(), "expires_at": (at + timedelta(hours=168)).isoformat(),
        })
        self.can_send[thread] = can_send
        return rid

    def waiting_asks(self, org, *, thread_id=None, actor_email=None):
        self.reads += 1
        if self.error is not None:
            raise self.error
        out = []
        for row in sorted(self.rows, key=lambda r: r["asked_at"]):
            if not self.leaky and actor_email and row["actor_email"] != actor_email.lower():
                continue
            out.append(dict(row))
        return out

    def resolve_room(self, thread_id, email, org):
        self.rooms.append(thread_id)
        return SimpleNamespace(can_send=self.can_send.get(thread_id, False))


@pytest.fixture()
def asks(fakes, monkeypatch) -> Asks:  # noqa: F811
    from acb_common import get_settings
    from gateway.routes import agent as agent_routes

    a = Asks(fakes)
    monkeypatch.setattr(get_settings(), "chat_durable_asks", True)
    monkeypatch.setattr(pending_ask, "waiting_asks", a.waiting_asks)
    monkeypatch.setattr(agent_routes, "_resolve_room", a.resolve_room)
    return a


def _signal_ids(answer: dict) -> list[str]:
    return [c["signal_id"] for c in answer["cards"]]


# ── done-when 1: dark until listed ──────────────────────────────────────────


class TestDarkUntilListed:
    def test_the_two_providers_are_gated_and_feed_only(self):
        p = shell.REGISTRY["people"]
        assert (p.gated, p.needs, p.features, p.app, p.handled, p.permission) == (
            True, False, ("people", "tasks", "projects"), None, None, "admin:members:read")
        assert p.kinds == frozenset({"colleague_out"})
        c = shell.REGISTRY["chat"]
        assert (c.gated, c.needs, c.features, c.app, c.handled, c.permission) == (
            True, False, ("chat",), None, None, None)
        assert c.kinds == frozenset({"agent_asks"})
        assert shell.source_app("people") == "people"
        assert shell.source_app("chat") == "chat"

    def test_the_permission_is_the_hr_grant(self):
        from gateway.routes.tasks.core import PEOPLE_HR_READ_PERMISSION

        assert shell.REGISTRY["people"].permission == PEOPLE_HR_READ_PERMISSION

    def test_with_the_list_empty_no_read_runs_and_no_byte_changes(
        self, fakes, away, asks, monkeypatch,  # noqa: F811
    ):
        _list(monkeypatch, "")
        _share(fakes, COLL)
        away.rows[COLL] = {"name": "Priya", "kind": "away"}
        asks.add("t-ask")
        answer = run_feed(hr(*EVERY))
        assert away.calls == [] and asks.reads == 0 and asks.rooms == []
        assert not {"people", "chat"} & set(answer["sources"])
        assert not {"people", "chat"} & set(answer["source_apps"])
        assert not {c["kind"] for c in answer["cards"]} & {"colleague_out", "agent_asks"}

    def test_listed_they_run(self, fakes, away, asks, monkeypatch):  # noqa: F811
        _list(monkeypatch, "people,chat")
        answer = run_feed(hr(*EVERY))
        assert (answer["sources"]["people"], answer["sources"]["chat"]) == ("ok", "ok")
        assert answer["source_apps"]["people"] == "people"
        assert answer["source_apps"]["chat"] == "chat"


# ── done-when 2: each kind gives its card ───────────────────────────────────


class TestEachKindGivesItsCard:
    def test_colleague_out(self, fakes, away, monkeypatch):  # noqa: F811
        _list(monkeypatch, "people")
        _share(fakes, COLL)
        away.rows[COLL] = {"name": "Priya", "kind": "away"}
        answer = run_feed(hr(*EVERY))
        today = _today(fakes)
        c = card(answer, f"calendar:{today.isoformat()}")
        assert (c["kind"], c["app"], c["group"], c["why_key"]) == (
            "colleague_out", "people", "fyi", "colleague_out")
        assert c["title"] == "Priya is away today"
        assert c["why"] == "Priya is away today. You share Ship the extruder with them."
        assert c["acts"] == [{"id": "open-day", "label": "Open the day", "class": "A",
                              "way": "open", "href": f"/calendar?date={today.isoformat()}"}]
        assert c["score"] == feed_rank.BASE["colleague_out"] == 26
        assert c["facts"] == {"out": ["Priya"], "task": "Ship the extruder"}
        assert "source" not in c
        tz = ZoneInfo(fakes.zone)
        midnight = datetime.combine(today + timedelta(days=1), datetime.min.time(), tzinfo=tz)
        assert c["expires_at"] == midnight.isoformat()
        # The day is the main read of the provider: the member's zone.
        assert away.calls == [([COLL], today)]

    def test_two_colleagues_away(self, fakes, away, monkeypatch):  # noqa: F811
        _list(monkeypatch, "people")
        _share(fakes, COLL, COLL2)
        away.rows[COLL] = {"name": "Priya", "kind": "holiday"}
        away.rows[COLL2] = {"name": "", "kind": "away"}
        c = card(run_feed(hr(*EVERY)), f"calendar:{_today(fakes).isoformat()}")
        assert c["title"] == "2 colleagues are away today"
        # A row with no name reads as the local part of the address.
        assert c["facts"]["out"] == ["Priya", "ravi"]
        assert c["why"] == "Priya and 1 more are away today. You share Ship the extruder with them."

    def test_it_folds_into_a_calendar_card_of_today(self, fakes, away, monkeypatch):  # noqa: F811
        """One entity, one card (§4.5): an overbooked today carries it."""
        _list(monkeypatch, "people")
        fakes.capacity[ME] = 30
        _share(fakes, COLL)
        away.rows[COLL] = {"name": "Priya", "kind": "away"}
        c = card(run_feed(hr(*EVERY)), f"calendar:{_today(fakes).isoformat()}")
        assert (c["kind"], c["app"], c["folded"]) == ("day_overbooked", "calendar",
                                                      ["colleague_out"])
        assert c["why"].endswith(" Priya is away today. You share Ship the extruder with them.")

    def test_a_fold_keeps_the_state_fingerprint_of_the_day(self, fakes, away, monkeypatch):  # noqa: F811
        _list(monkeypatch, "people")
        fakes.capacity[ME] = 30
        sid = f"calendar:{_today(fakes).isoformat()}"
        alone = card(run_feed(hr(*EVERY)), sid)
        _share(fakes, COLL)
        away.rows[COLL] = {"name": "Priya", "kind": "away"}
        both = card(run_feed(hr(*EVERY)), sid)
        assert both["folded"] == ["colleague_out"]
        assert state.state_fp(sid, both["facts"]) == state.state_fp(sid, alone["facts"])
        assert not {"out", "task"} & set(state.STATE_FACTS)

    def test_agent_asks(self, fakes, asks, monkeypatch):  # noqa: F811
        _list(monkeypatch, "chat")
        rid = asks.add("t-ask", minutes_ago=30)
        answer = run_feed(hr(*EVERY))
        c = card(answer, "chat:t-ask")
        assert (c["kind"], c["app"], c["group"], c["why_key"]) == (
            "agent_asks", "chat", "needs_you", "asks")
        assert c["title"] == "projects-assistant asked you a question"
        tz = ZoneInfo(fakes.zone)
        at = (fakes.now - timedelta(minutes=30)).astimezone(tz)
        when = "today" if at.date() == _today(fakes) else "yesterday"
        assert c["why"] == (f"Asked {when} at {at.hour:02d}:{at.minute:02d}. "
                            "The run waits for your answer.")
        href = "/chat?do=open-chat&fill.session=t-ask"
        assert c["href"] == href
        assert c["acts"] == [{"id": "open-chat", "label": "Open chat", "class": "A",
                              "way": "open", "href": href}]
        assert c["score"] == feed_rank.BASE["agent_asks"] == 43
        assert c["facts"] == {"waiting_since": (fakes.now - timedelta(minutes=30)).isoformat(),
                              "request_ids": [rid]}
        assert c["expires_at"] == (fakes.now - timedelta(minutes=30)
                                   + timedelta(hours=168)).isoformat()
        assert c["entity"] == {"type": "chat", "id": "t-ask",
                               "label": "projects-assistant"}

    def test_a_thread_id_is_quoted_in_the_link(self, fakes, asks, monkeypatch):  # noqa: F811
        _list(monkeypatch, "chat")
        asks.add("t/a&b")
        c = card(run_feed(hr(*EVERY)), "chat:t/a&b")
        assert c["href"] == "/chat?do=open-chat&fill.session=t%2Fa%26b"

    def test_a_question_with_no_agent_name(self, fakes, asks, monkeypatch):  # noqa: F811
        _list(monkeypatch, "chat")
        asks.add("t-anon", agent=None)
        assert card(run_feed(hr(*EVERY)), "chat:t-anon")["title"] == \
            "An agent asked you a question"

    def test_each_card_has_its_label(self, fakes, away, asks, monkeypatch):  # noqa: F811
        from tests.unit.test_feed_task_kinds import _labels

        labels = _labels()
        assert labels["colleague_out"] == "Colleague away"
        assert labels["agent_asks"] == "Waiting for your answer"


# ── done-when 3 and 4: the HR gate ──────────────────────────────────────────


class TestTheHrGate:
    def test_a_member_without_the_grant_gets_absent_and_the_provider_never_runs(
        self, fakes, away, monkeypatch,  # noqa: F811
    ):
        ran: list[str] = []
        real = shell.REGISTRY["people"]

        async def _spy(user):
            ran.append(user.email)
            return await real.run(user)

        monkeypatch.setitem(shell.REGISTRY, "people", dataclasses.replace(real, run=_spy))
        _list(monkeypatch, "people")
        _share(fakes, COLL)
        away.rows[COLL] = {"name": "Priya", "kind": "away"}
        answer = run_feed(plain(*EVERY))
        assert answer["sources"]["people"] == "absent"
        assert ran == [] and away.calls == []
        assert "colleague_out" not in {c["kind"] for c in answer["cards"]}
        # The same member with the grant runs it: the spy is live.
        run_feed(hr(*EVERY))
        assert ran == [ME]

    def test_a_member_without_people_gets_absent(self, fakes, away, monkeypatch):  # noqa: F811
        _list(monkeypatch, "people")
        answer = run_feed(hr(*FULL))
        assert answer["sources"]["people"] == "absent"
        assert away.calls == []

    def test_away_on_raises_403_without_the_grant_and_opens_no_session(self, monkeypatch):
        from gateway.routes.people import absences

        def _no_session():
            raise AssertionError("away_on opened a session for a member with no grant")

        monkeypatch.setattr(absences, "_tenant_session", _no_session)
        with pytest.raises(HTTPException) as err:
            asyncio.run(absences.away_on(plain("people"), [COLL], datetime(2026, 10, 12).date()))
        assert err.value.status_code == 403

    def test_away_on_with_no_address_opens_no_session(self, monkeypatch):
        from gateway.routes.people import absences

        monkeypatch.setattr(absences, "_tenant_session", lambda: (_ for _ in ()).throw(
            AssertionError("a session for no address")))
        assert asyncio.run(absences.away_on(
            hr("people"), [ME.upper().split("@")[0], ""], datetime(2026, 10, 12).date())) == {}


# ── done-when 5 and 6, and the agent: who never gives a row ─────────────────


class TestWhoGivesNoRow:
    def test_a_partial_absence_gives_no_row(self, fakes, away, monkeypatch):  # noqa: F811
        """The provider half. The R8 suite proves that ``away_on`` gives no
        partial row at all."""
        _list(monkeypatch, "people")
        _share(fakes, COLL)
        away.rows[COLL] = {"name": "Priya", "kind": "partial"}
        answer = run_feed(hr(*EVERY))
        assert answer["sources"]["people"] == "ok"
        assert "colleague_out" not in {c["kind"] for c in answer["cards"]}

    def test_my_own_absence_gives_no_row(self, fakes, away, monkeypatch):  # noqa: F811
        _list(monkeypatch, "people")
        fakes.blocks[ME][0]["assignees"] = [ME.upper(), COLL]
        away.rows[ME] = {"name": "Me", "kind": "away"}
        answer = run_feed(hr(*EVERY))
        assert "colleague_out" not in {c["kind"] for c in answer["cards"]}
        # I am never asked about.
        assert away.calls == [([COLL], _today(fakes))]

    def test_an_agent_co_assignee_gives_no_row(self, fakes, away, monkeypatch):  # noqa: F811
        _list(monkeypatch, "people")
        _share(fakes, AGENT)
        away.rows[AGENT] = {"name": "Bot", "kind": "away"}
        answer = run_feed(hr(*EVERY))
        assert "colleague_out" not in {c["kind"] for c in answer["cards"]}
        assert away.calls == []  # nobody to ask about, so no session

    def test_a_colleague_who_works_today_gives_no_row(self, fakes, away, monkeypatch):  # noqa: F811
        _list(monkeypatch, "people")
        _share(fakes, COLL)
        answer = run_feed(hr(*EVERY))
        assert answer["sources"]["people"] == "ok"
        assert "colleague_out" not in {c["kind"] for c in answer["cards"]}

    def test_another_members_block_never_reaches_the_card(self, fakes, away, monkeypatch):  # noqa: F811
        from tests.unit.test_shell_needs import OTHER

        _list(monkeypatch, "people")
        fakes.blocks[OTHER][0]["assignees"] = [OTHER, COLL]
        away.rows[COLL] = {"name": "Priya", "kind": "away"}
        answer = run_feed(hr(*EVERY))
        assert "colleague_out" not in {c["kind"] for c in answer["cards"]}

    def test_an_absence_read_that_fails_fails_the_source(self, fakes, monkeypatch):  # noqa: F811
        from gateway.routes.people import absences

        async def _down(user, emails, day):
            raise RuntimeError("down")

        monkeypatch.setattr(absences, "away_on", _down)
        _list(monkeypatch, "people")
        _share(fakes, COLL)
        assert run_feed(hr(*EVERY))["sources"]["people"] == "failed"


# ── done-when 8 to 11: the chat negatives ───────────────────────────────────


class TestTheChatNegatives:
    def test_a_question_of_another_member_gives_no_row(self, fakes, asks, monkeypatch):  # noqa: F811
        """The store reads only my questions. A leaky store is held by the
        provider's own check of ``actor_email``."""
        from tests.unit.test_shell_needs import OTHER

        _list(monkeypatch, "chat")
        asks.add("t-theirs", actor=OTHER)
        asks.leaky = True
        answer = run_feed(hr(*EVERY))
        assert answer["sources"]["chat"] == "ok"
        assert "chat:t-theirs" not in _signal_ids(answer)
        assert asks.rooms == []

    def test_a_room_where_i_cannot_send_gives_no_row(self, fakes, asks, monkeypatch):  # noqa: F811
        _list(monkeypatch, "chat")
        asks.add("t-viewer", can_send=False)
        asks.add("t-mine")
        answer = run_feed(hr(*EVERY))
        assert "chat:t-viewer" not in _signal_ids(answer)
        assert "chat:t-mine" in _signal_ids(answer)

    def test_a_room_layer_that_answers_none_gives_no_row(self, fakes, asks, monkeypatch):  # noqa: F811
        from gateway.routes import agent as agent_routes

        _list(monkeypatch, "chat")
        asks.add("t-none")
        monkeypatch.setattr(agent_routes, "_resolve_room", lambda *_a: None)
        assert "chat:t-none" not in _signal_ids(run_feed(hr(*EVERY)))

    def test_with_durable_asks_off_the_source_is_absent(self, fakes, asks, monkeypatch):  # noqa: F811
        from acb_common import get_settings

        _list(monkeypatch, "chat")
        asks.add("t-ask")
        monkeypatch.setattr(get_settings(), "chat_durable_asks", False)
        answer = run_feed(hr(*EVERY))
        assert answer["sources"]["chat"] == "absent"
        assert asks.reads == 0 and asks.rooms == []

    def test_a_member_without_chat_gets_absent(self, fakes, asks, monkeypatch):  # noqa: F811
        _list(monkeypatch, "chat")
        asks.add("t-ask")
        assert run_feed(hr(*FULL))["sources"]["chat"] == "absent"
        assert asks.reads == 0

    def test_a_store_error_fails_the_source(self, fakes, asks, monkeypatch):  # noqa: F811
        """``_my_waiting_asks`` answers None on a database error. An empty
        ``ok`` would hide each question, so the source fails."""
        _list(monkeypatch, "chat")
        asks.error = RuntimeError("down")
        assert run_feed(hr(*EVERY))["sources"]["chat"] == "failed"

    def test_the_card_holds_no_question_text(self, fakes, asks, monkeypatch):  # noqa: F811
        _list(monkeypatch, "chat")
        asks.add("t-ask")
        answer = run_feed(hr(*EVERY))
        assert "chat:t-ask" in _signal_ids(answer)
        assert "zebra" not in json.dumps(answer, default=str)

    def test_a_thread_id_that_fails_the_pattern_is_skipped(self, fakes, asks, monkeypatch):  # noqa: F811
        _list(monkeypatch, "chat")
        asks.add("t bad")
        asks.add("t-good")
        answer = run_feed(hr(*EVERY))
        assert "chat:t-good" in _signal_ids(answer)
        assert not any(s.startswith("chat:t bad") for s in _signal_ids(answer))
        assert asks.rooms == ["t-good"]


# ── done-when 13: the reads ─────────────────────────────────────────────────


class TestTheReads:
    def test_people_makes_three_reads_at_most(self, fakes, away, monkeypatch):  # noqa: F811
        _list(monkeypatch, "people")
        _share(fakes, COLL)
        away.rows[COLL] = {"name": "Priya", "kind": "away"}
        fakes.calls.clear()
        _bound(lambda: feed._people(hr(*EVERY)))
        made = [c[0] for c in fakes.calls]
        assert made == ["get_user_settings", "my_calendar"]
        assert len(made) + len(away.calls) == 3

    def test_chat_reads_one_plus_one_for_each_thread_and_15_threads(
        self, fakes, asks, monkeypatch,  # noqa: F811
    ):
        _list(monkeypatch, "chat")
        for n in range(20):
            asks.add(f"t-{n:02d}", minutes_ago=100 - n)
        found = _bound(lambda: feed._chat(hr(*EVERY)))
        assert asks.reads == 1
        assert len(asks.rooms) == feed.CHAT_THREADS == 15
        # The oldest threads first, and the newest five are cut.
        assert asks.rooms == [f"t-{n:02d}" for n in range(15)]
        assert found.capped is True
        assert len(found.signals()) == 15


# ── done-when 12: the bumps, and a bump never breaks a write ────────────────


class _Session:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


@pytest.fixture()
def cache_on(monkeypatch):
    from acb_common.settings import get_settings

    monkeypatch.setattr(get_settings(), "my_day_cache", True)


class TestTheAdminAbsenceWritesBumpTheOrg:
    @pytest.fixture()
    def writes(self, cache_on, monkeypatch):
        from gateway.routes.people import absences
        from gateway.routes.shell import cache

        got: list[str] = []

        async def _set(org, _msg):
            got.append(org)

        monkeypatch.setattr(cache, "_set_org_version", _set)
        monkeypatch.setattr(absences, "_tenant_session", lambda: _Session())

        async def _authorized(db, pid, user, *, write):
            return SimpleNamespace(id=pid, email=COLL)

        async def _created(db, pid, body, by):
            return {"id": "a-1"}

        async def _deleted(db, pid, aid):
            return aid == "a-1"

        monkeypatch.setattr(absences, "_authorized", _authorized)
        monkeypatch.setattr(absences, "create_absence", _created)
        monkeypatch.setattr(absences, "delete_absence", _deleted)
        return got

    def _body(self):
        from gateway.routes.people.absences import AbsenceIn

        return AbsenceIn(starts_on="2026-10-20", ends_on="2026-10-21")

    def test_post_and_delete_bump_the_org_version(self, writes):
        from gateway.routes.people import absences

        asyncio.run(absences.add_absence("p-1", self._body(), user=hr("people")))
        assert writes == [ORG]
        asyncio.run(absences.remove_absence("p-1", "a-1", user=hr("people")))
        assert writes == [ORG, ORG]

    def test_a_delete_that_deleted_nothing_bumps_nothing(self, writes):
        from gateway.routes.people import absences

        with pytest.raises(HTTPException):
            asyncio.run(absences.remove_absence("p-1", "a-9", user=hr("people")))
        assert writes == []

    def test_with_the_cache_off_nothing_bumps(self, writes, monkeypatch):
        from acb_common.settings import get_settings
        from gateway.routes.people import absences

        monkeypatch.setattr(get_settings(), "my_day_cache", False)
        asyncio.run(absences.add_absence("p-1", self._body(), user=hr("people")))
        assert writes == []

    def test_a_bump_that_raises_never_fails_the_write(self, writes, monkeypatch):
        from gateway.routes.people import absences
        from gateway.routes.shell import cache

        async def _boom(org):
            raise RuntimeError("bump down")

        monkeypatch.setattr(cache, "bump_org", _boom)
        assert asyncio.run(absences.add_absence("p-1", self._body(),
                                                user=hr("people"))) == {"id": "a-1"}
        assert asyncio.run(absences.remove_absence("p-1", "a-1", user=hr("people"))) == {
            "deleted": "a-1"}

    def test_redis_down_never_fails_the_write(self, cache_on, monkeypatch):
        from gateway.routes.people import absences
        from gateway.routes.shell import cache

        def _down():
            raise ConnectionError("redis down")

        monkeypatch.setattr(cache, "_redis", _down)
        monkeypatch.setattr(absences, "_tenant_session", lambda: _Session())

        async def _authorized(db, pid, user, *, write):
            return SimpleNamespace(id=pid, email=COLL)

        async def _created(db, pid, body, by):
            return {"id": "a-1"}

        monkeypatch.setattr(absences, "_authorized", _authorized)
        monkeypatch.setattr(absences, "create_absence", _created)
        assert asyncio.run(absences.add_absence("p-1", self._body(),
                                                user=hr("people"))) == {"id": "a-1"}

    def test_a_bump_that_hangs_is_cut_off(self, writes, monkeypatch):
        from gateway.routes.people import absences
        from gateway.routes.shell import cache

        async def _hang(org):
            await asyncio.sleep(30)

        monkeypatch.setattr(cache, "bump_org", _hang)
        monkeypatch.setattr(absences, "MY_DAY_BUMP_TIMEOUT_S", 0.01)
        out = asyncio.run(asyncio.wait_for(
            absences.add_absence("p-1", self._body(), user=hr("people")), 20))
        assert out == {"id": "a-1"}


class _Store:
    """``chat_pending_ask`` in memory, for the two writes."""

    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}

    def insert_ask(self, org, row):
        self.rows[row["request_id"]] = {**row, "state": "open"}
        return True

    def move_ask(self, org, rid, *, to, from_states, answer=None):
        row = self.rows.get(rid)
        if row is None or row["state"] not in from_states:
            return None
        row["state"] = to
        return dict(row)


class TestTheQuestionWritesBumpTheMember:
    @pytest.fixture()
    def store(self, monkeypatch):
        from gateway.routes.shell import cache

        s = _Store()
        s.bumps = []

        async def _incr(org, op, event):
            s.bumps.append(org)

        monkeypatch.setattr(pending_ask, "insert_ask", s.insert_ask)
        monkeypatch.setattr(pending_ask, "move_ask", s.move_ask)

        async def _facts(thread_id):
            return ORG, "run-1", "projects-assistant", ME

        monkeypatch.setattr(pending_ask, "_run_facts", _facts)
        monkeypatch.setattr(cache, "_bump", _incr)
        pending_ask._reset_for_tests()
        yield s
        pending_ask._reset_for_tests()

    def _ask(self) -> object:
        rid = uuid.uuid4().hex
        return pending_ask.CardAsk(request_id=rid, kind="ask_user", question=QUESTION,
                                   payload={"request_id": rid})

    def _member_bumps(self, monkeypatch) -> list[tuple[str, str]]:
        from gateway.routes.shell import cache

        got: list[tuple[str, str]] = []
        real = cache.bump_member

        async def _spy(org, email):
            got.append((org, email))
            await real(org, email)

        monkeypatch.setattr(cache, "bump_member", _spy)
        return got

    def test_an_insert_and_a_settle_bump_the_member(self, store, cache_on, monkeypatch):
        got = self._member_bumps(monkeypatch)
        ask = self._ask()

        async def go():
            assert await pending_ask._record("t-1", ask) == ORG
            pending_ask._INSERTS[ask.request_id] = asyncio.get_running_loop().create_task(
                asyncio.sleep(0, result=ORG))
            await pending_ask.settle(ask.request_id, answer="Yes")

        asyncio.run(go())
        assert got == [(ORG, ME), (ORG, ME)]
        assert store.bumps == [ORG, ORG]
        assert store.rows[ask.request_id]["state"] == "answered"

    def test_with_the_cache_off_nothing_bumps(self, store, monkeypatch):
        from acb_common.settings import get_settings

        monkeypatch.setattr(get_settings(), "my_day_cache", False)
        asyncio.run(pending_ask._record("t-1", self._ask()))
        assert store.bumps == []

    def test_a_settle_that_moved_no_row_bumps_nothing(self, store, cache_on, monkeypatch):
        got = self._member_bumps(monkeypatch)

        async def go():
            pending_ask._INSERTS["r-gone"] = asyncio.get_running_loop().create_task(
                asyncio.sleep(0, result=ORG))
            await pending_ask.settle("r-gone", answer=None)

        asyncio.run(go())
        assert got == []

    def test_a_bump_that_raises_never_breaks_the_ask(self, store, cache_on, monkeypatch):
        from gateway.routes.shell import cache

        async def _boom(org, email):
            raise RuntimeError("bump down")

        monkeypatch.setattr(cache, "bump_member", _boom)
        ask = self._ask()

        async def go():
            org = await pending_ask._record("t-1", ask)
            pending_ask._INSERTS[ask.request_id] = asyncio.get_running_loop().create_task(
                asyncio.sleep(0, result=org))
            await pending_ask.settle(ask.request_id, answer="Yes")
            return org

        assert asyncio.run(go()) == ORG
        assert store.rows[ask.request_id]["state"] == "answered"

    def test_redis_down_never_breaks_the_ask(self, cache_on, monkeypatch):
        from gateway.routes.shell import cache

        s = _Store()
        monkeypatch.setattr(pending_ask, "insert_ask", s.insert_ask)
        monkeypatch.setattr(pending_ask, "move_ask", s.move_ask)

        async def _facts(thread_id):
            return ORG, "run-1", "projects-assistant", ME

        def _down():
            raise ConnectionError("redis down")

        monkeypatch.setattr(pending_ask, "_run_facts", _facts)
        monkeypatch.setattr(cache, "_redis", _down)
        ask = self._ask()
        assert asyncio.run(pending_ask._record("t-1", ask)) == ORG
        assert s.rows[ask.request_id]["state"] == "open"

    def test_a_bump_that_hangs_is_cut_off(self, store, cache_on, monkeypatch):
        from gateway.routes.shell import cache

        async def _hang(org, email):
            await asyncio.sleep(30)

        monkeypatch.setattr(cache, "bump_member", _hang)
        monkeypatch.setattr(pending_ask, "MY_DAY_BUMP_TIMEOUT_S", 0.01)
        out = asyncio.run(asyncio.wait_for(pending_ask._record("t-1", self._ask()), 20))
        assert out == ORG

    def test_a_failed_insert_bumps_nothing(self, store, cache_on, monkeypatch):
        def _fails(org, row):
            raise RuntimeError("db down")

        monkeypatch.setattr(pending_ask, "insert_ask", _fails)
        assert asyncio.run(pending_ask._record("t-1", self._ask())) is None
        assert store.bumps == []
