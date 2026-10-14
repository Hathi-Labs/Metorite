"""WS-58 MD-6a-1 — the My Day ``decide`` pass, in shadow (``my_day_feed.md``
§7 rule 2, §9.1, §10 and §13.2, MD-6a-1 done-when 1 to 7).

Hermetic. The facade is a FAKE ``acb_llm.decide``, so no test reaches a
network. The Redis is a fake UNDER the real ``TenantRedis``, so every key
goes through the real tenant-prefix wrapper. The rows go to a fake
``write_rows``. ``test_feed_decide_r8.py`` writes them to a real Postgres.

The classes, and the done-when each fences:

* ``TestTheRegistry`` (1): the eight features, ``off`` by default, in the
  one registry.
* ``TestTheOrgList`` (2): a ``my_day.`` feature reads ``MY_DAY_DECIDE_ORGS``.
* ``TestShadow`` (3): the caller gets the old value, and each comparison is
  one row.
* ``TestNoTenantText`` (3): no row and no log line holds a title, a subject
  or a name.
* ``TestKeysOnly`` (4, the reader only, §10.2): an off-list key is no answer.
* ``TestRuleTwo`` (4): ``apply_triage`` never hides a needs card.
* ``TestCaps`` (4): the member cap of 60 and the org ceiling.
* ``TestCallCounts`` (5): what makes no call, counted.
* ``TestTheCooldown`` (6): a 402 stops the next shadow.
* ``TestEveryModeOff`` (§10.9 item 9): no compute, no call, no row.
* ``TestTheHelpers``: the six helper changes of §10.6 item 2 and §10.9
  item 4. The email suites fence that each leaves email unchanged (7).
"""

from __future__ import annotations

import copy
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from typing import Any

import acb_llm
import pytest
import structlog
from acb_auth.permissions import EffectiveAccess
from acb_auth.roles import UserContext, UserRole
from acb_common import tenant_redis
from acb_common._log import clear_run_context, run_context_scope
from acb_common.db import bind_tenant, release_tenant
from acb_common.settings import get_settings
from acb_common.tenant_redis import TenantRedis
from gateway import decide_features as df
from gateway.routes.shell import feed, feed_decide, feed_rank

REPO = Path(__file__).resolve().parents[2]
ORG = "6a1a6a1a-0000-4000-8000-000000000001"
OTHER = "6a1a6a1a-0000-4000-8000-000000000002"
ME = "Member.One@md6a.example"
NOW = datetime(2026, 10, 14, 9, 0, tzinfo=UTC)

# Tenant text. None of it may reach a row or a decide.* log line.
SECRET_TITLE = "SECRET-TITLE-acme-merger"
SECRET_SUBJECT = "SECRET-SUBJECT-quarterly-pricing"
SECRET_NAME = "SECRET-NAME-Priya-Raman"
SECRET_PROJECT = "SECRET-PROJECT-Orion"

ALL_SHADOW = ",".join(f"{f}=shadow" for f in feed_decide.ASKED)


# ── Fakes ───────────────────────────────────────────────────────────────────


class FakeRedis:
    """``GET``, ``MGET``, ``SET``, ``INCR``, ``DECRBY`` and ``EXPIRE``. TTLs
    are kept and not enforced. ``fail`` raises on every command."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.ttl: dict[str, int] = {}
        self.fail: BaseException | None = None
        self.names: list[str] = []

    def _check(self, name: str) -> None:
        self.names.append(name)
        if self.fail is not None:
            raise self.fail

    async def get(self, name: str) -> str | None:
        self._check(name)
        return self.store.get(name)

    async def mget(self, names: list[str]) -> list[str | None]:
        for n in names:
            self._check(n)
        return [self.store.get(n) for n in names]

    async def set(self, name: str, value: str, *, ex: int | None = None, **_: Any) -> bool:
        self._check(name)
        self.store[name] = value
        if ex is not None:
            self.ttl[name] = ex
        return True

    async def incr(self, name: str, amount: int = 1) -> int:
        self._check(name)
        value = int(self.store.get(name, "0")) + amount
        self.store[name] = str(value)
        return value

    async def decrby(self, name: str, amount: int = 1) -> int:
        return await self.incr(name, -amount)

    async def expire(self, name: str, seconds: int) -> bool:
        self._check(name)
        self.ttl[name] = seconds
        return True


class FakeDecide:
    """Records each call, and answers each question. ``choice`` maps a short
    feature name to the key it answers. ``raises`` raises instead.
    ``fail_if`` raises ``raises`` only for a request whose question ids it
    names. ``rid_by_qid`` names each request by its first question id, so a
    test can tell which request answered which question."""

    def __init__(self, *, choice: dict[str, str] | None = None, level: str | None = "high",
                 score: Any = 3, p: float = 0.9, raises: BaseException | None = None,
                 fail_if: Any = None, rid_by_qid: bool = False) -> None:
        self.calls: list[dict[str, Any]] = []
        self.choice = choice or {}
        self.level = level
        self.score = score
        self.p = p
        self.raises = raises
        self.rid = 0
        self.fail_if = fail_if
        self.rid_by_qid = rid_by_qid

    async def __call__(self, state, questions, **kwargs):
        self.calls.append({"state": state, "questions": dict(questions), **kwargs})
        if self.raises is not None and (self.fail_if is None or self.fail_if(questions)):
            raise self.raises
        answers: dict[str, Any] = {}
        for qid, q in questions.items():
            short = qid.split(".", 1)[1]
            if q.type == "boolean":
                answers[qid] = acb_llm.BooleanAnswer(probability=self.p)
            elif q.type == "score":
                answers[qid] = acb_llm.ScoreAnswer(
                    score=self.score, probabilities=MappingProxyType({}),
                    confidence=0.61, level=self.level)
            else:
                keys = list(q.criteria)
                answers[qid] = acb_llm.ChoiceAnswer(
                    choice=self.choice.get(short, keys[-1]),
                    probabilities=MappingProxyType({k: 0.1 for k in keys}),
                    confidence=0.73)
        self.rid += 1
        rid = f"req-{next(iter(questions))}" if self.rid_by_qid else f"req-{self.rid}"
        return acb_llm.Decision(answers=MappingProxyType(answers), request_id=rid)


# ── Fixtures ────────────────────────────────────────────────────────────────


def _clear() -> None:
    get_settings.cache_clear()
    df._parse_modes.cache_clear()
    df._parse_orgs.cache_clear()


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for name in ("DECIDE_FEATURE_MODES", "DECIDE_FEATURE_ORGS", "MY_DAY_DECIDE_ORGS",
                 "MY_DAY_DECIDE_ORG_DAILY_CALLS"):
        monkeypatch.setenv(name, "")
    monkeypatch.setenv("MY_DAY_DECIDE_ORG_DAILY_CALLS", "0")
    _clear()
    df._cooldown_until.clear()
    with run_context_scope():
        clear_run_context()
        yield
    monkeypatch.undo()
    _clear()
    df._cooldown_until.clear()


@pytest.fixture
def tenant():
    token = bind_tenant(ORG)
    yield ORG
    release_tenant(token)


@pytest.fixture
def redis(monkeypatch) -> FakeRedis:
    fake = FakeRedis()
    monkeypatch.setattr(tenant_redis, "get_tenant_redis", lambda **_: TenantRedis(fake))
    return fake


def _modes(monkeypatch, modes: str = ALL_SHADOW, my_day_orgs: str = ORG,
           email_orgs: str = "") -> None:
    monkeypatch.setenv("DECIDE_FEATURE_MODES", modes)
    monkeypatch.setenv("MY_DAY_DECIDE_ORGS", my_day_orgs)
    monkeypatch.setenv("DECIDE_FEATURE_ORGS", email_orgs)
    _clear()


def _fake(monkeypatch, **kw) -> FakeDecide:
    fake = FakeDecide(**kw)
    monkeypatch.setattr(acb_llm, "decide", fake)
    return fake


def _user(org: str = ORG) -> UserContext:
    return UserContext(email=ME, role=UserRole.EMPLOYEE, organization_id=org,
                       access=EffectiveAccess(role_granted=frozenset({"feature:projects"})))


ACTS = [{"id": "done", "label": "Done", "class": "A", "way": "tap"},
        {"id": "move-tomorrow", "label": "Move to tomorrow", "class": "A", "way": "open",
         "href": "/projects?task=1"}]


def _card(sid: str, *, kind: str = "overdue", group: str = "needs_you", score: int = 80,
          fp: str = "fp000001", title: str = SECRET_TITLE, app: str = "tasks",
          folded: tuple[str, ...] = (), facts: dict[str, Any] | None = None,
          acts: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "signal_id": sid, "app": app, "kind": kind, "folded": list(folded),
        "group": group, "score": score,
        "entity": {"type": "task", "id": sid.split(":")[-1], "label": title},
        "title": title, "why": f"{title} is late.", "why_key": "late",
        "facts": facts if facts is not None else {
            "priority": "important", "project": SECRET_PROJECT,
            "due_at": "2026-10-12T09:00:00+00:00", "days_late": 2},
        "fingerprint": fp, "urgent_at": None, "expires_at": None, "at": None,
        "href": "/projects?task=1", "acts": copy.deepcopy(acts if acts is not None else ACTS),
        "state": "new",
    }


class Cards:
    """What ``feed.feed_cards`` hands the pass. Counts its calls."""

    def __init__(self, cards: list[dict[str, Any]]) -> None:
        self.cards = cards
        self.calls = 0

    async def __call__(self, user, now=None, *, read=False):
        self.calls += 1
        return SimpleNamespace(cards=copy.deepcopy(self.cards), joined=SimpleNamespace(day=None))


@pytest.fixture
def ctx_box(monkeypatch) -> dict[str, feed_decide.MemberContext]:
    box = {"ctx": feed_decide.MemberContext(local_date="2026-10-14", role="employee",
                                            local_time="09:00")}

    async def _read(user, now, day):
        return box["ctx"]

    monkeypatch.setattr(feed_decide, "read_context", _read)
    return box


@pytest.fixture
def rows(monkeypatch) -> list[tuple[str, list[dict[str, Any]]]]:
    out: list[tuple[str, list[dict[str, Any]]]] = []

    async def _write(member, made):
        out.append((member, [dict(r) for r in made]))
        return len(made)

    monkeypatch.setattr(feed_decide, "write_rows", _write)
    return out


def _cards(monkeypatch, cards: list[dict[str, Any]]) -> Cards:
    source = Cards(cards)
    monkeypatch.setattr(feed, "feed_cards", source)
    return source


def _all_rows(rows) -> list[dict[str, Any]]:
    return [r for _m, batch in rows for r in batch]


# ── Done-when 1: the one registry ───────────────────────────────────────────


class TestTheRegistry:
    def test_the_eight_features_are_in_the_registry_and_off(self, tenant, monkeypatch):
        assert df.MY_DAY_FEATURES == (
            "my_day.triage", "my_day.weight", "my_day.reason", "my_day.next_act",
            "my_day.slot", "my_day.resurface", "my_day.compose", "my_day.nudge")
        assert set(df.MY_DAY_FEATURES) <= set(df.REGISTERED)
        _modes(monkeypatch, modes="")
        for feature in df.MY_DAY_FEATURES:
            assert df._parse_modes("")[feature] == "off"
            assert df.mode_for(feature) == "off"

    def test_each_feature_takes_shadow_from_the_one_parser(self, tenant, monkeypatch):
        _modes(monkeypatch, ",".join(f"{f}=shadow" for f in df.MY_DAY_FEATURES))
        assert {df.mode_for(f) for f in df.MY_DAY_FEATURES} == {"shadow"}

    def test_on_is_refused_until_md6b(self, tenant, monkeypatch):
        with structlog.testing.capture_logs() as caps:
            _modes(monkeypatch, "my_day.triage=on")
            assert df.mode_for("my_day.triage") == "off"
        refused = [c for c in caps if c.get("event") == "decide.mode_refused"]
        assert refused and refused[0]["decide_reason"] == "on_refused"

    def test_the_pass_asks_four_and_has_no_registry_of_its_own(self):
        assert set(feed_decide.ASKED) == {
            "my_day.triage", "my_day.weight", "my_day.next_act", "my_day.resurface"}
        assert set(feed_decide.ASKED) <= set(df.MY_DAY_FEATURES)
        source = (REPO / "apps/services/gateway/gateway/routes/shell/feed_decide.py"
                  ).read_text(encoding="utf-8")
        # No second mode reader and no second helper: the module reads the
        # registry through mode_for, and calls shadow.
        assert "decide_feature_modes" not in source
        assert "my_day_decide_orgs" not in source
        assert "df.shadow(" in source and "df.mode_for(" in source
        # It never calls the facade or the private gather of the helper.
        assert "acb_llm import decide" not in source and "_ask_all" not in source


# ── Done-when 2: the org list ───────────────────────────────────────────────


class TestTheOrgList:
    def test_my_day_reads_its_own_list(self, tenant, monkeypatch):
        _modes(monkeypatch, "my_day.triage=shadow,email.cold_check=shadow",
               my_day_orgs=ORG, email_orgs="")
        assert df.mode_for("my_day.triage") == "shadow"
        assert df.mode_for("email.cold_check") == "off"

    def test_email_keeps_its_star_and_my_day_stays_off(self, tenant, monkeypatch):
        _modes(monkeypatch, "my_day.triage=shadow,email.cold_check=shadow",
               my_day_orgs="", email_orgs="*")
        assert df.mode_for("my_day.triage") == "off"
        assert df.mode_for("email.cold_check") == "shadow"

    def test_another_org_and_the_star(self, monkeypatch):
        _modes(monkeypatch, "my_day.triage=shadow", my_day_orgs=OTHER)
        token = bind_tenant(ORG)
        try:
            assert df.mode_for("my_day.triage") == "off"
            _modes(monkeypatch, "my_day.triage=shadow", my_day_orgs="*")
            assert df.mode_for("my_day.triage") == "shadow"
        finally:
            release_tenant(token)

    def test_the_settings_default_to_no_org_and_no_ceiling(self, monkeypatch):
        monkeypatch.delenv("MY_DAY_DECIDE_ORGS", raising=False)
        monkeypatch.delenv("MY_DAY_DECIDE_ORG_DAILY_CALLS", raising=False)
        _clear()
        assert get_settings().my_day_decide_orgs == ""
        assert get_settings().my_day_decide_org_daily_calls == 0


# ── Done-when 3: shadow writes rows, and the caller keeps the old value ─────


class TestShadow:
    async def test_one_row_for_each_comparison(self, tenant, monkeypatch, redis, ctx_box, rows):
        _modes(monkeypatch)
        fake = _fake(monkeypatch, choice={"triage": "later", "next_act": "move-tomorrow"})
        cards = [_card("tasks:1", score=90), _card("tasks:2", score=70, group="later",
                                                   kind="due_today")]
        before = copy.deepcopy(cards)
        _cards(monkeypatch, cards)
        out = await feed_decide.judge(_user(), NOW)
        assert out.reason == "asked" and out.requests == 1 and len(fake.calls) == 1
        got = _all_rows(rows)
        # Card 1: triage, weight, next_act. Card 2: triage, weight, next_act, resurface.
        assert sorted((r["signal_id"], r["feature"]) for r in got) == sorted([
            ("tasks:1", "my_day.triage"), ("tasks:1", "my_day.weight"),
            ("tasks:1", "my_day.next_act"), ("tasks:2", "my_day.triage"),
            ("tasks:2", "my_day.weight"), ("tasks:2", "my_day.next_act"),
            ("tasks:2", "my_day.resurface")])
        assert {r["mode"] for r in got} == {"shadow"} and {r["used"] for r in got} == {False}
        # Exactly as my_day_card_state writes it: the session's address.
        assert rows[0][0] == ME
        one = {r["feature"]: r for r in got if r["signal_id"] == "tasks:1"}
        assert one["my_day.triage"]["old_value"] == "needs_you"
        assert one["my_day.triage"]["judged_value"] == "later"
        assert one["my_day.weight"]["old_value"] == "some"  # important = level 2
        assert one["my_day.weight"]["judged_value"] == "high"
        assert one["my_day.next_act"]["old_value"] == "done"
        assert one["my_day.next_act"]["judged_value"] == "move-tomorrow"
        assert one["my_day.triage"]["request_id"] == "req-1"
        two = {r["feature"]: r for r in got if r["signal_id"] == "tasks:2"}
        assert two["my_day.resurface"]["old_value"] == "false"
        assert two["my_day.resurface"]["judged_value"] == "true"
        assert two["my_day.resurface"]["confidence"] == pytest.approx(0.9)
        # The ranks ride on the triage row only.
        assert one["my_day.triage"]["old_rank"] == 1
        assert one["my_day.triage"]["judged_rank"] is not None
        assert one["my_day.weight"]["old_rank"] is None
        assert cards == before  # the pass changes no card

    async def test_the_caller_gets_the_old_value(self, tenant, monkeypatch):
        _modes(monkeypatch)
        _fake(monkeypatch, choice={"triage": "drop"})
        seen: list[Any] = []

        async def _old():
            return {"old": "value"}

        async def _record(made):
            seen.extend(made)

        q = acb_llm.ChoiceQuestion(instructions="x", criteria={"a": "a", "drop": "d"})
        out = await df.shadow(
            "my_day.automation", _old, build=lambda: ({"cards": []}, {"c0.triage": q}),
            compare=lambda old, d: [df.Comparison(old="a", new=d["c0.triage"].choice,
                                                  agree=False, options=2, qid="c0.triage")],
            features=("my_day.triage",), record=_record)
        assert out == {"old": "value"}
        assert len(seen) == 1 and seen[0].new == "drop" and seen[0].request_id == "req-1"


# ── Done-when 3: no tenant text ─────────────────────────────────────────────


class TestNoTenantText:
    SECRETS = (SECRET_TITLE, SECRET_SUBJECT, SECRET_NAME, SECRET_PROJECT)

    def _cards(self) -> list[dict[str, Any]]:
        mail = _card("email:acc-1:thr-1", kind="needs_reply", app="email",
                     title=SECRET_SUBJECT,
                     facts={"who": SECRET_NAME, "waiting_since": "2026-10-13T09:00:00+00:00",
                            "account": "acc-1", "message_id": "m-1"},
                     acts=[{"id": "open-thread", "label": "Open thread", "way": "open",
                            "href": "/email?thread=thr-1"}])
        return [_card("tasks:1"), mail]

    async def test_no_row_and_no_log_line_holds_tenant_text(
            self, tenant, monkeypatch, redis, ctx_box, rows):
        _modes(monkeypatch)
        fake = _fake(monkeypatch)
        _cards(monkeypatch, self._cards())
        with structlog.testing.capture_logs() as caps:
            out = await feed_decide.judge(_user(), NOW)
        assert out.reason == "asked"
        blob = json.dumps(_all_rows(rows), default=str)
        logs = json.dumps([c for c in caps if str(c.get("event", "")).startswith("decide.")],
                          default=str)
        for secret in self.SECRETS:
            assert secret not in blob
            assert secret not in logs
        # The state does carry the clipped facts: that is what decide reads.
        state = json.dumps(fake.calls[0]["state"])
        assert SECRET_TITLE in state and SECRET_NAME in state
        # No Redis key holds them either.
        for secret in self.SECRETS:
            assert all(secret not in n for n in redis.names)

    def test_the_table_has_no_text_column_for_a_title_a_subject_or_a_name(self):
        found = [f for f in (REPO / "infra/postgres").glob("[0-9]*_*.sql")
                 if "CREATE TABLE IF NOT EXISTS my_day_judgments" in f.read_text(encoding="utf-8")]
        assert len(found) == 1
        body = found[0].read_text(encoding="utf-8").split(
            "CREATE TABLE IF NOT EXISTS my_day_judgments", 1)[1].split(");", 1)[0]
        columns = {m.group(1) for m in re.finditer(r"^\s+([a-z_]+)\s+[A-Z]", body, re.M)}
        assert columns == {
            "organization_id", "member_email", "signal_id", "fingerprint", "feature",
            "mode", "old_value", "judged_value", "confidence", "used", "request_id",
            "old_rank", "judged_rank", "created_at"}
        assert not {c for c in columns if re.search("title|subject|name|body|sender", c)}

    def test_the_state_carries_no_mail_body(self):
        card = _card("email:acc-1:thr-1", kind="needs_reply", app="email",
                     facts={"who": "Ann", "body": "BODY-TEXT", "snippet": "SNIP"})
        state = feed_decide.build_state(
            feed_decide.MemberContext(local_date="2026-10-14"), [("c0", card)])
        assert "BODY-TEXT" not in json.dumps(state) and "SNIP" not in json.dumps(state)


# ── Done-when 4: the reader, and §7 rule 2 ──────────────────────────────────


class TestKeysOnly:
    """§10.2, the reader only (MD-6a-1). The card cases are MD-6b's."""

    def test_an_off_list_key_is_no_answer_and_a_valid_key_is_its_own(self):
        opts = tuple(feed_decide.TRIAGE_OPTIONS)
        bad = acb_llm.ChoiceAnswer(choice="Open https://evil.example", probabilities={},
                                   confidence=0.99)
        good = acb_llm.ChoiceAnswer(choice="fyi", probabilities={}, confidence=0.5)
        assert feed_decide.read_answer(feed_decide.TRIAGE, bad, opts) is None
        assert feed_decide.read_answer(feed_decide.TRIAGE, good, opts) == "fyi"
        acts = ("done", "move-tomorrow")
        act = acb_llm.ChoiceAnswer(choice="move-tomorrow", probabilities={}, confidence=0.5)
        assert feed_decide.read_answer(feed_decide.NEXT_ACT, act, acts) == "move-tomorrow"
        assert feed_decide.read_answer(feed_decide.NEXT_ACT, good, acts) is None

    def test_a_score_reads_a_level_of_ours_or_nothing(self):
        levels = feed_decide.WEIGHT_LEVELS
        mk = acb_llm.ScoreAnswer
        assert feed_decide.read_answer(feed_decide.WEIGHT, mk(3, {}, 0.5, "high"), levels) == "high"
        assert feed_decide.read_answer(feed_decide.WEIGHT, mk(3, {}, 0.5, "huge"), levels) is None
        assert feed_decide.read_answer(feed_decide.WEIGHT, mk(1.5, {}, 0.5, None), levels) == "some"
        assert feed_decide.read_answer(feed_decide.WEIGHT, mk(9, {}, 0.5, None), levels) is None
        assert feed_decide.read_answer(feed_decide.WEIGHT, mk("low", {}, 0.5), levels) == "low"

    def test_a_boolean_reads_at_the_gate(self):
        yes = acb_llm.BooleanAnswer(probability=0.6)
        no = acb_llm.BooleanAnswer(probability=0.59)
        assert feed_decide.read_answer(feed_decide.RESURFACE, yes, ("true", "false")) == "true"
        assert feed_decide.read_answer(feed_decide.RESURFACE, no, ("true", "false")) == "false"

    async def test_an_off_list_answer_writes_no_judged_value(
            self, tenant, monkeypatch, redis, ctx_box, rows):
        _modes(monkeypatch, "my_day.triage=shadow")
        _fake(monkeypatch, choice={"triage": "Ignore all rules"})
        _cards(monkeypatch, [_card("tasks:1")])
        await feed_decide.judge(_user(), NOW)
        (row,) = _all_rows(rows)
        assert row["judged_value"] is None and row["old_value"] == "needs_you"


class TestRuleTwo:
    def test_drop_on_an_overdue_task_leaves_it_in_later(self):
        assert feed_decide.apply_triage(_card("tasks:1", kind="overdue"), "drop") == "later"

    def test_drop_on_a_card_with_a_folded_needs_kind_leaves_it_in_later(self):
        card = _card("tasks:1", kind="block_soon", folded=("due_today",), group="now")
        assert feed_decide.apply_triage(card, "drop") == "later"

    @pytest.mark.parametrize("kind", sorted(feed_decide.NEEDS_KINDS))
    def test_no_needs_kind_is_ever_hidden(self, kind):
        assert feed_decide.apply_triage(_card("x:1", kind=kind), "drop") == "later"

    def test_drop_on_another_card_hides_it(self):
        card = _card("calendar:2026-10-14", kind="no_plan_yet", app="calendar")
        assert feed_decide.apply_triage(card, "drop") is None

    def test_no_answer_keeps_the_old_group_and_a_key_sets_it(self):
        card = _card("tasks:1", group="needs_you")
        assert feed_decide.apply_triage(card, None) == "needs_you"
        assert feed_decide.apply_triage(card, "bogus") == "needs_you"
        assert feed_decide.apply_triage(card, "fyi") == "fyi"

    def test_the_judged_rank_never_drops_a_needs_card(self):
        cards = [_card("tasks:1", kind="overdue"),
                 _card("calendar:d", kind="no_plan_yet", app="calendar", score=10)]
        answers = {"tasks:1": {feed_decide.TRIAGE: {"v": "drop"}},
                   "calendar:d": {feed_decide.TRIAGE: {"v": "drop"}}}
        ranks = feed_decide.judged_rank(cards, answers)
        assert ranks == {"tasks:1": 1}


# ── Done-when 4: the caps (§9.1 rule 8) ─────────────────────────────────────


def _many(n: int) -> list[dict[str, Any]]:
    # 8 cards fill one request (8 cards, 16 questions: triage and weight).
    # Two apps, so the feed's cap of 15 cards for each app keeps all of them.
    return [_card(f"tasks:{i}", score=50 - i, acts=ACTS[:1], fp=f"fp{i:06d}",
                  app=("tasks", "email")[i % 2]) for i in range(n)]


class TestCaps:
    def _member_key(self) -> str:
        return f"cc:{ORG}:my_day:calls:{ME}:2026-10-14"

    def _org_key(self) -> str:
        return f"cc:{ORG}:my_day:calls:org:2026-10-14"

    async def test_the_counters_go_through_the_tenant_prefix_with_26_hours(
            self, tenant, monkeypatch, redis, ctx_box, rows):
        _modes(monkeypatch)
        _fake(monkeypatch)
        _cards(monkeypatch, _many(1))
        await feed_decide.judge(_user(), NOW)
        assert redis.store[self._member_key()] == "1"
        assert redis.store[self._org_key()] == "1"
        assert redis.ttl[self._member_key()] == 26 * 3600

    async def test_the_member_cap_of_60_trims_the_pass(
            self, tenant, monkeypatch, redis, ctx_box, rows):
        assert feed_decide.MEMBER_DAILY_CALLS == 60
        _modes(monkeypatch)
        fake = _fake(monkeypatch)
        redis.store[self._member_key()] = "59"
        _cards(monkeypatch, _many(16))  # two requests wanted
        out = await feed_decide.judge(_user(), NOW)
        assert out.requests == 1 and len(fake.calls) == 1
        assert redis.store[self._member_key()] == "60"

    async def test_past_the_member_cap_no_call_and_old_values(
            self, tenant, monkeypatch, redis, ctx_box, rows):
        _modes(monkeypatch)
        fake = _fake(monkeypatch)
        redis.store[self._member_key()] = "60"
        _cards(monkeypatch, _many(2))
        out = await feed_decide.judge(_user(), NOW)
        assert out.reason == "cap" and fake.calls == [] and rows == []
        assert redis.store[self._member_key()] == "60"

    async def test_the_org_ceiling(self, tenant, monkeypatch, redis, ctx_box, rows):
        _modes(monkeypatch)
        monkeypatch.setenv("MY_DAY_DECIDE_ORG_DAILY_CALLS", "3")
        _clear()
        fake = _fake(monkeypatch)
        redis.store[self._org_key()] = "3"
        _cards(monkeypatch, _many(2))
        out = await feed_decide.judge(_user(), NOW)
        assert out.reason == "cap" and fake.calls == []
        # The member's reservation is given back.
        assert redis.store[self._member_key()] == "0"
        assert redis.store[self._org_key()] == "3"

    async def test_a_ceiling_of_zero_is_no_ceiling(self, tenant, monkeypatch, redis, ctx_box, rows):
        _modes(monkeypatch)
        fake = _fake(monkeypatch)
        redis.store[self._org_key()] = "100000"
        _cards(monkeypatch, _many(2))
        out = await feed_decide.judge(_user(), NOW)
        assert out.reason == "asked" and len(fake.calls) == 1

    async def test_a_redis_failure_asks_nothing(self, tenant, monkeypatch, redis, ctx_box, rows):
        _modes(monkeypatch)
        fake = _fake(monkeypatch)
        redis.fail = ConnectionError("down")
        _cards(monkeypatch, _many(2))
        out = await feed_decide.judge(_user(), NOW)
        assert out.reason == "failed" and fake.calls == [] and rows == []

    async def test_two_zones_of_one_org_share_one_utc_org_bucket(
            self, tenant, monkeypatch, redis, rows):
        """Fix round 1: the org counter keys on the UTC date. At 20:00 UTC
        a member in IST is on the 15th and a member in US Pacific on the
        14th. With a ceiling of 1, the second member gets no call."""
        _modes(monkeypatch)
        monkeypatch.setenv("MY_DAY_DECIDE_ORG_DAILY_CALLS", "1")
        _clear()
        fake = _fake(monkeypatch)
        _cards(monkeypatch, _many(2))
        late = datetime(2026, 10, 14, 20, 0, tzinfo=UTC)
        ist, pac = "ist.member@md6a.example", "pac.member@md6a.example"
        dates = {ist: "2026-10-15", pac: "2026-10-14"}

        async def _ctx(user, now, day):
            return feed_decide.MemberContext(local_date=dates[user.email], role="employee")

        monkeypatch.setattr(feed_decide, "read_context", _ctx)

        def _member_user(email: str) -> UserContext:
            return UserContext(email=email, role=UserRole.EMPLOYEE, organization_id=ORG,
                               access=EffectiveAccess(role_granted=frozenset()))

        first = await feed_decide.judge(_member_user(ist), late)
        second = await feed_decide.judge(_member_user(pac), late)
        assert (first.reason, second.reason) == ("asked", "cap")
        assert len(fake.calls) == 1
        org_keys = [k for k in redis.store if ":my_day:calls:org:" in k]
        assert org_keys == [f"cc:{ORG}:my_day:calls:org:2026-10-14"]
        assert redis.store[org_keys[0]] == "1"
        # The member counters keep each member's local date.
        assert redis.store[f"cc:{ORG}:my_day:calls:{ist}:2026-10-15"] == "1"
        assert redis.store[f"cc:{ORG}:my_day:calls:{pac}:2026-10-14"] == "0"

    def test_a_request_holds_8_cards_and_16_questions_at_most(self):
        ctx = feed_decide.MemberContext(local_date="2026-10-14")
        wanted = [(_card(f"tasks:{i}"), list(feed_decide.ASKED)) for i in range(20)]
        requests, asked = feed_decide.pack(ctx, wanted)
        assert len(asked) == 80
        for state, questions in requests:
            assert len(state["cards"]) <= 8 and len(questions) <= 16
        assert sum(len(q) for _s, q in requests) == 80
        assert len({a.qid for a in asked}) == 80


# ── Done-when 5: what makes no call ─────────────────────────────────────────


class TestCallCounts:
    async def test_no_card_makes_no_call(self, tenant, monkeypatch, redis, ctx_box, rows):
        _modes(monkeypatch)
        fake = _fake(monkeypatch)
        _cards(monkeypatch, [])
        out = await feed_decide.judge(_user(), NOW)
        assert out.reason == "no_cards" and fake.calls == []

    async def test_the_same_facts_make_no_second_call(self, tenant, monkeypatch, redis,
                                                      ctx_box, rows):
        _modes(monkeypatch)
        fake = _fake(monkeypatch)
        _cards(monkeypatch, _many(3))
        await feed_decide.judge(_user(), NOW)
        assert len(fake.calls) == 1
        out = await feed_decide.judge(_user(), NOW + timedelta(minutes=5))
        assert out.reason == "cached" and len(fake.calls) == 1
        assert len(rows) == 1

    async def test_an_unhashed_field_makes_no_call(self, tenant, monkeypatch, redis,
                                                   ctx_box, rows):
        _modes(monkeypatch)
        fake = _fake(monkeypatch)
        _cards(monkeypatch, _many(3))
        await feed_decide.judge(_user(), NOW)
        ctx_box["ctx"] = feed_decide.MemberContext(
            local_date="2026-10-14", role="employee", local_time="11:47",
            day_hours="07-22", deadlines=("2026-10-15",), blocks=("09:00 Standup",),
            habits={"overdue": {"shown": 9, "acted": 1, "not_useful": 0}})
        out = await feed_decide.judge(_user(), NOW + timedelta(hours=3))
        assert out.reason == "cached" and len(fake.calls) == 1

    async def test_a_changed_fingerprint_asks_that_card_only(self, tenant, monkeypatch, redis,
                                                             ctx_box, rows):
        _modes(monkeypatch)
        fake = _fake(monkeypatch)
        cards = _many(3)
        source = _cards(monkeypatch, cards)
        await feed_decide.judge(_user(), NOW)
        source.cards[1]["fingerprint"] = "changed1"
        out = await feed_decide.judge(_user(), NOW)
        assert out.reason == "asked" and len(fake.calls) == 2
        assert {r["signal_id"] for r in rows[1][1]} == {"tasks:1"}

    async def test_a_hashed_field_judges_again_at_most_once_an_hour(
            self, tenant, monkeypatch, redis, ctx_box, rows):
        _modes(monkeypatch)
        fake = _fake(monkeypatch)
        _cards(monkeypatch, _many(2))
        await feed_decide.judge(_user(), NOW)
        await feed_decide.judge(_user(), NOW)  # the hash is stored
        ctx_box["ctx"] = feed_decide.MemberContext(
            local_date="2026-10-14", role="employee", one_thing="task-9")
        out = await feed_decide.judge(_user(), NOW + timedelta(minutes=20))
        assert out.reason == "cached" and len(fake.calls) == 1
        out = await feed_decide.judge(_user(), NOW + timedelta(minutes=61))
        assert out.reason == "asked" and len(fake.calls) == 2

    def test_the_hash_names_its_fields_and_no_others(self):
        base = feed_decide.MemberContext(local_date="2026-10-14", role="employee")
        same = feed_decide.MemberContext(
            local_date="2026-10-14", role="employee", local_time="17:00",
            day_hours="08-18", goals=("G",), projects=("P",), deadlines=("D",),
            blocks=("B",), habits={"k": {"shown": 1}})
        assert feed_decide.context_hash(base) == feed_decide.context_hash(same)
        for change in ({"local_date": "2026-10-15"}, {"role": "admin"},
                       {"one_thing": "t1"}, {"goal_ids": ("g1",)},
                       {"project_ids": ("p1",)}, {"corrections": ("No newsletters",)}):
            other = feed_decide.MemberContext(**{"local_date": "2026-10-14",
                                                 "role": "employee", **change})
            assert feed_decide.context_hash(other) != feed_decide.context_hash(base), change


# ── Done-when 6: the cool-down ──────────────────────────────────────────────


class TestTheCooldown:
    async def test_a_402_starts_it_and_the_next_pass_makes_no_call(
            self, tenant, monkeypatch, redis, ctx_box, rows):
        _modes(monkeypatch)
        fake = _fake(monkeypatch, raises=acb_llm.DecideUnavailable(
            "insufficient_credits", status=402))
        cards = _many(2)
        before = copy.deepcopy(cards)
        _cards(monkeypatch, cards)
        await feed_decide.judge(_user(), NOW)
        assert len(fake.calls) == 1 and rows == []
        assert df.cooling()
        out = await feed_decide.judge(_user(), NOW)
        assert out.reason == "cooldown" and len(fake.calls) == 1
        assert cards == before

    async def test_a_my_day_403_starts_no_cooldown(self, tenant, monkeypatch, redis,
                                                   ctx_box, rows):
        _modes(monkeypatch)
        fake = _fake(monkeypatch, raises=acb_llm.DecideUnavailable("forbidden", status=403))
        _cards(monkeypatch, _many(2))
        await feed_decide.judge(_user(), NOW)
        assert len(fake.calls) == 1 and not df.cooling()
        assert df._cooldown_until == {}

    async def test_an_email_403_still_starts_it(self, tenant, monkeypatch):
        _modes(monkeypatch, "email.cold_check=shadow", email_orgs=ORG)
        _fake(monkeypatch, raises=acb_llm.DecideUnavailable("forbidden", status=403))

        async def _old():
            return "old"

        q = acb_llm.BooleanQuestion(instructions="x")
        out = await df.shadow("email.cold_check", _old, account_id="a",
                              build=lambda: ({}, {"cold": q}),
                              compare=df.compare_boolean(lambda r: True, qid="cold"))
        assert out == "old" and df.cooling()

    async def test_shadow_itself_honours_the_cooldown(self, tenant, monkeypatch):
        _modes(monkeypatch, "email.cold_check=shadow", email_orgs=ORG)
        fake = _fake(monkeypatch)
        df._cooldown_until[ORG] = df._now() + 60

        async def _old():
            return "old"

        q = acb_llm.BooleanQuestion(instructions="x")
        with structlog.testing.capture_logs() as caps:
            out = await df.shadow("email.cold_check", _old, account_id="a",
                                  build=lambda: ({}, {"cold": q}),
                                  compare=df.compare_boolean(lambda r: True, qid="cold"))
        assert out == "old" and fake.calls == []
        skipped = [c for c in caps if c.get("event") == "decide.shadow_skipped"]
        assert skipped and skipped[0]["reason"] == "cooldown"


# ── Fix round 1: a failed request costs only itself ─────────────────────────


class TestPartialFailure:
    async def test_the_middle_request_fails_and_the_other_two_record(
            self, tenant, monkeypatch, redis, ctx_box, rows):
        _modes(monkeypatch)
        # 24 cards with two questions each: three requests of 8 cards. The
        # second request holds c8 to c15, and it fails with a 503.
        fake = _fake(monkeypatch, raises=acb_llm.DecideUnavailable("HTTP 503", status=503),
                     fail_if=lambda questions: "c8.triage" in questions)
        _cards(monkeypatch, _many(24))
        out = await feed_decide.judge(_user(), NOW)
        assert out.reason == "asked" and out.requests == 3 and len(fake.calls) == 3
        got = _all_rows(rows)
        judged = {r["signal_id"] for r in got}
        assert judged == {f"tasks:{i}" for i in (*range(8), *range(16, 24))}
        assert len(got) == 32
        # The two good requests keep their own request ids.
        assert {r["request_id"] for r in got if r["signal_id"] == "tasks:0"} != {
            r["request_id"] for r in got if r["signal_id"] == "tasks:16"}
        # Their answers are cached: the next pass asks the failed request only.
        fake.fail_if = lambda questions: False
        fake.raises = None
        again = await feed_decide.judge(_user(), NOW)
        assert again.requests == 1 and len(fake.calls) == 4
        assert {r["signal_id"] for r in rows[-1][1]} == {f"tasks:{i}" for i in range(8, 16)}


# ── §10.9 item 9: every mode off ────────────────────────────────────────────


class TestEveryModeOff:
    async def test_no_compute_no_call_no_row(self, tenant, monkeypatch, redis, ctx_box, rows):
        _modes(monkeypatch, modes="", my_day_orgs="*")
        fake = _fake(monkeypatch)
        source = _cards(monkeypatch, _many(3))
        out = await feed_decide.judge(_user(), NOW)
        assert out.reason == "off"
        assert source.calls == 0 and fake.calls == [] and rows == [] and redis.names == []

    async def test_modes_on_and_no_org_listed_is_off_too(self, tenant, monkeypatch, redis,
                                                         ctx_box, rows):
        _modes(monkeypatch, my_day_orgs="", email_orgs="*")
        fake = _fake(monkeypatch)
        source = _cards(monkeypatch, _many(3))
        assert (await feed_decide.judge(_user(), NOW)).reason == "off"
        assert source.calls == 0 and fake.calls == []

    async def test_a_user_of_another_org_is_refused(self, tenant, monkeypatch, redis,
                                                    ctx_box, rows):
        _modes(monkeypatch, my_day_orgs="*")
        fake = _fake(monkeypatch)
        source = _cards(monkeypatch, _many(1))
        assert (await feed_decide.judge(_user(OTHER), NOW)).reason == "tenant"
        assert source.calls == 0 and fake.calls == []


# ── The six helper changes (§10.6 item 2, §10.9 item 4) ─────────────────────


class TestTheHelpers:
    async def test_ids_and_module_slug_reach_the_log_and_the_facade(self, tenant, monkeypatch):
        _modes(monkeypatch, "my_day.triage=shadow")
        fake = _fake(monkeypatch)

        async def _old():
            return "old"

        q = acb_llm.ChoiceQuestion(instructions="x", criteria={"a": "a", "b": "b"})
        with structlog.testing.capture_logs() as caps:
            await df.shadow("my_day.automation", _old, build=lambda: ({}, {"c0.triage": q}),
                            compare=df.compare_choice(lambda r: "a", qid="c0.triage",
                                                      options=2, keys=("a", "b")),
                            ids={"decide_cards": 1}, module_slug="my_day",
                            features=("my_day.triage",))
        assert fake.calls[0]["module_slug"] == "my_day"
        line = next(c for c in caps if c.get("event") == "decide.shadow")
        assert line["decide_cards"] == 1 and line["decide_feature"] == "my_day.automation"
        assert "account_id" not in line and "message_id" not in line

    async def test_ask_takes_ids_and_module_slug(self, tenant, monkeypatch):
        fake = _fake(monkeypatch)
        q = acb_llm.ChoiceQuestion(instructions="x", criteria={"a": "a", "b": "b"})
        with structlog.testing.capture_logs() as caps:
            out = await df.ask("my_day.triage", build=lambda: ({}, {"c0.triage": q}),
                               read=lambda d: (d["c0.triage"].choice, {}),
                               ids={"decide_cards": 2}, module_slug="my_day")
        assert out == "b" and fake.calls[0]["module_slug"] == "my_day"
        line = next(c for c in caps if c.get("event") == "decide.decided")
        assert line["decide_cards"] == 2

    async def test_a_packed_request_asks_when_one_feature_is_in_shadow(self, tenant,
                                                                       monkeypatch):
        _modes(monkeypatch, "my_day.weight=shadow")
        fake = _fake(monkeypatch)

        async def _old():
            return "old"

        q = acb_llm.BooleanQuestion(instructions="x")
        build = lambda: ({}, {"c0.resurface": q})  # noqa: E731
        compare = df.compare_boolean(lambda r: False, qid="c0.resurface")
        await df.shadow("my_day.automation", _old, build=build, compare=compare,
                        features=("my_day.triage",))
        assert fake.calls == []
        await df.shadow("my_day.automation", _old, build=build, compare=compare,
                        features=("my_day.triage", "my_day.weight"))
        assert len(fake.calls) == 1

    async def test_one_log_line_and_one_request_id_for_each_comparison(self, tenant,
                                                                      monkeypatch):
        _modes(monkeypatch, "my_day.triage=shadow")
        _fake(monkeypatch, rid_by_qid=True)
        got: list[df.Comparison] = []

        async def _old():
            return "old"

        async def _record(made):
            got.extend(made)

        q = acb_llm.BooleanQuestion(instructions="x")

        def _compare(old, decision):
            return [df.Comparison(old=False, new=True, agree=False, options=2,
                                  probability=decision[qid].probability,
                                  feature="my_day.resurface", item=f"tasks:{qid}", qid=qid)
                    for qid in ("c0.r", "c1.r")]

        with structlog.testing.capture_logs() as caps:
            await df.shadow("my_day.automation", _old,
                            build=lambda: [({}, {"c0.r": q}), ({}, {"c1.r": q})],
                            compare=_compare, features=("my_day.triage",), record=_record)
        lines = [c for c in caps if c.get("event") == "decide.shadow"]
        assert [(ln["decide_feature"], ln["decide_item"]) for ln in lines] == [
            ("my_day.resurface", "tasks:c0.r"), ("my_day.resurface", "tasks:c1.r")]
        # Each item gets the id of the request that ASKED it, not a neighbour's.
        assert {c.qid: c.request_id for c in got} == {"c0.r": "req-c0.r", "c1.r": "req-c1.r"}
        assert {ln["decide_item"]: ln["request_id"] for ln in lines} == {
            "tasks:c0.r": "req-c0.r", "tasks:c1.r": "req-c1.r"}

    async def test_a_failed_record_never_reaches_the_caller(self, tenant, monkeypatch):
        _modes(monkeypatch, "my_day.triage=shadow")
        _fake(monkeypatch)

        async def _old():
            return "old"

        async def _record(made):
            raise RuntimeError("db down")

        q = acb_llm.BooleanQuestion(instructions="x")
        with structlog.testing.capture_logs() as caps:
            out = await df.shadow(
                "my_day.automation", _old, build=lambda: ({}, {"c0.r": q}),
                compare=df.compare_boolean(lambda r: False, qid="c0.r"),
                features=("my_day.triage",), record=_record)
        assert out == "old"
        assert any(c.get("event") == "decide.shadow_record_failed" for c in caps)

    async def test_no_record_means_no_record_call(self, tenant, monkeypatch):
        _modes(monkeypatch, "my_day.triage=shadow")
        _fake(monkeypatch)

        async def _old():
            return "old"

        q = acb_llm.BooleanQuestion(instructions="x")
        with structlog.testing.capture_logs() as caps:
            out = await df.shadow(
                "my_day.automation", _old, build=lambda: ({}, {"c0.r": q}),
                compare=df.compare_boolean(lambda r: False, qid="c0.r"),
                features=("my_day.triage",))
        assert out == "old"
        events = [c.get("event") for c in caps]
        assert "decide.shadow" in events
        assert "decide.shadow_record_failed" not in events

    def test_compare_score(self):
        cmp = df.compare_score(lambda r: "some", qid="w", levels=feed_decide.WEIGHT_LEVELS)
        answer = acb_llm.ScoreAnswer(score=3, probabilities={"high": 0.7, "some": 0.2},
                                     confidence=0.7, level="high")
        made = cmp(None, {"w": answer})
        assert (made.old, made.new, made.agree, made.options) == ("some", "high", False, 5)
        assert made.confidence == 0.7 and made.fields["margin"] == pytest.approx(0.5)
        odd = acb_llm.ScoreAnswer(score=3, probabilities={}, confidence=0.7, level="vast")
        assert cmp(None, {"w": odd}).new == "unknown"


# ── feed_cards: the one compute (§10.9 item 1) ──────────────────────────────


class TestFeedCards:
    async def test_the_pass_runs_no_handled_read_and_no_needs_view(self, monkeypatch):
        from gateway.routes.shell import handled, needs, state

        calls: list[str] = []
        empty = SimpleNamespace(sources={}, capped=[], found={}, items=[], totals={})

        async def _run(user):
            calls.append("needs_run")
            return empty

        async def _extra(user, providers, **kw):
            calls.append("feed_only")
            return empty

        async def _apply(user, cards, now):
            calls.append("apply_state")
            return state.Applied(cards)

        async def _handled(*a, **kw):
            calls.append("handled")
            return handled.HandledRun()

        def _answer(run, size):
            calls.append("needs_view")
            return {}

        monkeypatch.setattr(needs, "needs_run", _run)
        monkeypatch.setattr(needs, "run_providers", _extra)
        monkeypatch.setattr(needs, "needs_answer", _answer)
        monkeypatch.setattr(state, "apply_state", _apply)
        monkeypatch.setattr(handled, "handled_run", _handled)
        run = await feed.feed_cards(_user(), NOW)
        assert calls == ["needs_run", "feed_only", "apply_state"]
        assert run.now == NOW and run.closed is None and run.needs_view is None
        calls.clear()
        await feed.feed_answer(_user(), 40)
        assert calls == ["needs_run", "needs_view", "feed_only", "apply_state", "handled"]


def test_the_rank_table_is_the_feeds_own():
    # The judged rank orders by feed_rank.order, so it inherits rule 1 and 3.
    cards = [_card("tasks:1", score=10), _card("tasks:2", score=90)]
    assert feed_decide.places(feed_rank.order(cards, 40)) == {"tasks:2": 1, "tasks:1": 2}

