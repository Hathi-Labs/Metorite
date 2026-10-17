"""WS-53 CRM-Q26 — labels on CRM notes, on the decide tier (hermetic).

Spec: ``project-docs/specs/crm_platform.md`` §18.11.2, CRM-Q26. The table,
its RLS, the anti-join and the caps on a real database are in
``test_crm_note_labels_r8.py``.

* ``TestTheRegistry`` (item 1): ``crm.note_labels`` is in ``CRM_FEATURES``,
  ``REGISTERED`` and ``ON_FEATURES``, and not in the email ``FEATURES``.
* ``TestTheOrgList`` (item 2): a ``crm.`` feature reads ``CRM_DECIDE_ORGS``.
* ``TestTheCooldown`` (item 10): a CRM 402 starts it, and a CRM 403 does not.
* ``TestThePack`` (item 8): 5 notes and 15 questions to a request, the
  clips, and a question that names its note by its id in ``state.notes``.
* ``TestTheReader``: keys of ours only, and numbers from 0 to 1.
* ``TestTheStep`` (items 5, 7 and 10): ``ask`` with a proven member and
  ``module_slug="crm"``, the ``off`` path, the skip with no member, the stop
  on a failed request, and logs with no note text.
* ``TestTheScheduler`` (item 5): the step starts after a run, outside the
  lock, and one at a time for each org.
* ``TestNoRouteReadsTheLabels`` (item 9).
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
import re
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from typing import Any

import acb_llm
import pytest
import structlog
from acb_common._log import clear_run_context, run_context_scope
from acb_common.db import bind_tenant, release_tenant
from acb_common.settings import get_settings
from gateway import decide_features as df
from gateway.crm_sync import note_labels as nl
from gateway.crm_sync import scheduler

REPO = Path(__file__).resolve().parents[2]
ORG = "26262626-0000-4000-8000-000000000001"
OTHER = "26262626-0000-4000-8000-000000000002"
OWNER = "admin@crm-q26.example"

# Tenant text. None of it may reach a row or a log line.
SECRET_SUBJECT = "SECRET-SUBJECT-site-visit-Pune"
SECRET_BODY = "SECRET-BODY-the-pump-at-plant-7-leaks-again"


# ── Fakes ───────────────────────────────────────────────────────────────────


class FakeDecide:
    """Records each call and answers each question. ``raises`` raises."""

    def __init__(self, *, kind: str = "payment", p: float = 0.8, level: str | None = "high",
                 raises: BaseException | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.kind, self.p, self.level, self.raises = kind, p, level, raises

    async def __call__(self, state, questions, **kwargs):
        self.calls.append({"state": state, "questions": dict(questions), **kwargs})
        if self.raises is not None:
            raise self.raises
        answers: dict[str, Any] = {}
        for qid, q in questions.items():
            if q.type == "boolean":
                answers[qid] = acb_llm.BooleanAnswer(probability=self.p)
            elif q.type == "score":
                answers[qid] = acb_llm.ScoreAnswer(
                    score=2, probabilities=MappingProxyType({"low": 0.1, "high": 0.8}),
                    confidence=0.61, level=self.level)
            else:
                answers[qid] = acb_llm.ChoiceAnswer(
                    choice=self.kind,
                    probabilities=MappingProxyType({k: 0.1 for k in q.criteria}),
                    confidence=0.73)
        return acb_llm.Decision(answers=MappingProxyType(answers),
                                request_id=f"req-{len(self.calls)}")


class Box:
    """The DB half of the step, faked: the sessions, the member, the notes,
    the backlog and the writes. The request counter is a real
    ``reserve_request`` over a fake Redis. ``used`` presets the requests of
    today, and ``left`` is what the departed check answers."""

    def __init__(self, monkeypatch, *, notes: int = 7, member: str | None = OWNER,
                 used: int = 0, backlog: int | None = None, left: str | None = None,
                 today: str | None = None) -> None:
        from acb_common import tenant_redis
        from acb_common.tenant_redis import TenantRedis

        from tests.unit.test_feed_decide import FakeRedis

        self.sessions = 0
        self.limits: list[int] = []
        self.written: list[tuple[list[dict[str, Any]], str]] = []
        self.failed: list[list[str]] = []
        self.left_asked: list[str] = []
        self.redis = FakeRedis()
        monkeypatch.setattr(tenant_redis, "get_tenant_redis", lambda **_: TenantRedis(self.redis))
        self.today = today or nl._utc_date()
        if used:
            self.redis.store[self.key(self.today)] = str(used)
        self.notes = [nl.Note(id=f"00000000-0000-4000-8000-{i:012d}",
                              subject=f"{SECRET_SUBJECT}-{i}", body=SECRET_BODY,
                              body_md5=f"md5-{i}") for i in range(notes)]
        waiting = len(self.notes) if backlog is None else backlog

        @contextlib.asynccontextmanager
        async def _session(org=None):
            self.sessions += 1
            yield SimpleNamespace()

        async def _member(db, org):
            return member

        async def _member_left(org, who):
            self.left_asked.append(who)
            return left

        async def _backlog(db, org):
            return min(waiting, nl.BACKLOG_THRESHOLD + 1)

        async def _pending(db, org, limit):
            self.limits.append(limit)
            return self.notes[:limit]

        async def _write(org, rows, step_mode):
            self.written.append(([dict(r) for r in rows], step_mode))
            return len(rows)

        async def _record_failure(org, notes, step_mode):
            self.failed.append([n.id for n in notes])

        monkeypatch.setattr(nl, "tenant_session", _session)
        monkeypatch.setattr(nl, "_member", _member)
        monkeypatch.setattr(nl, "_member_left", _member_left)
        monkeypatch.setattr(nl, "_backlog", _backlog)
        monkeypatch.setattr(nl, "_pending", _pending)
        monkeypatch.setattr(nl, "_write", _write)
        monkeypatch.setattr(nl, "_record_failure", _record_failure)

    @staticmethod
    def key(utc_date: str, org: str = ORG) -> str:
        return f"cc:{org}:{nl.NAMESPACE}:requests:{utc_date}"

    def used(self, utc_date: str | None = None) -> int:
        return int(self.redis.store.get(self.key(utc_date or self.today), "0"))


# ── Fixtures ────────────────────────────────────────────────────────────────


def _clear() -> None:
    get_settings.cache_clear()
    df._parse_modes.cache_clear()
    df._parse_orgs.cache_clear()


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for name in ("DECIDE_FEATURE_MODES", "DECIDE_FEATURE_ORGS", "MY_DAY_DECIDE_ORGS",
                 "CRM_DECIDE_ORGS"):
        monkeypatch.setenv(name, "")
    _clear()
    df._cooldown_until.clear()
    scheduler._labeling.clear()
    with run_context_scope():
        clear_run_context()
        yield
    monkeypatch.undo()
    _clear()
    df._cooldown_until.clear()
    scheduler._labeling.clear()


@pytest.fixture
def tenant():
    token = bind_tenant(ORG)
    yield ORG
    release_tenant(token)


def _modes(monkeypatch, modes: str = "crm.note_labels=shadow", crm_orgs: str = ORG,
           email_orgs: str = "") -> None:
    monkeypatch.setenv("DECIDE_FEATURE_MODES", modes)
    monkeypatch.setenv("CRM_DECIDE_ORGS", crm_orgs)
    monkeypatch.setenv("DECIDE_FEATURE_ORGS", email_orgs)
    _clear()


def _fake(monkeypatch, **kw) -> FakeDecide:
    fake = FakeDecide(**kw)
    monkeypatch.setattr(acb_llm, "decide", fake)
    return fake


def _events(caps, name: str) -> list[dict[str, Any]]:
    return [c for c in caps if c.get("event") == name]


# ── Item 1: the one registry ────────────────────────────────────────────────


class TestTheRegistry:
    def test_the_feature_is_in_the_registry_and_not_in_the_email_set(self):
        # CRM-Q20-1 adds the three features of the mapping pipeline.
        assert df.CRM_FEATURES == ("crm.note_labels", "crm.map_role", "crm.map_target",
                                   "crm.map_stage")
        assert nl.FEATURE in df.REGISTERED
        assert nl.FEATURE in df.ON_FEATURES
        assert nl.FEATURE not in df.FEATURES
        assert dict(df.DEFAULT_MODES) == {f: "off" for f in df.FEATURES}

    def test_it_is_off_by_default(self, tenant, monkeypatch):
        _modes(monkeypatch, modes="")
        assert df._parse_modes("")[nl.FEATURE] == "off"
        assert df.mode_for(nl.FEATURE) == "off"
        assert nl.mode(ORG) == "off"
        assert not nl.enabled_for(ORG)

    @pytest.mark.parametrize("mode", ["shadow", "on"])
    def test_shadow_and_on_are_accepted(self, tenant, monkeypatch, mode):
        with structlog.testing.capture_logs() as caps:
            _modes(monkeypatch, f"crm.note_labels={mode}")
            assert df.mode_for(nl.FEATURE) == mode
        assert _events(caps, "decide.mode_refused") == []
        assert nl.enabled_for(ORG)

    def test_the_email_start_up_check_never_names_it(self, monkeypatch):
        _modes(monkeypatch, "crm.note_labels=on", crm_orgs="*", email_orgs="*")
        assert df.configured_on() == ()

    def test_the_step_reads_the_registry_and_has_no_mode_reader(self):
        source = (REPO / "apps/services/gateway/gateway/crm_sync/note_labels.py"
                  ).read_text(encoding="utf-8")
        assert "decide_feature_modes" not in source
        assert "crm_decide_orgs" not in source
        assert "df.mode_for(FEATURE)" in source
        assert "df.shadow(" not in source, "no old path, so the step never shadows"


# ── Item 2: the CRM's own org list ──────────────────────────────────────────


class TestTheOrgList:
    def test_the_setting_defaults_to_no_org(self, monkeypatch):
        monkeypatch.delenv("CRM_DECIDE_ORGS", raising=False)
        _clear()
        assert get_settings().crm_decide_orgs == ""

    def test_a_crm_feature_reads_crm_decide_orgs(self, monkeypatch):
        _modes(monkeypatch, crm_orgs=ORG, email_orgs="*")
        token = bind_tenant(ORG)
        try:
            assert df.mode_for(nl.FEATURE) == "shadow"
        finally:
            release_tenant(token)
        token = bind_tenant(OTHER)
        try:
            # The email list says "*", and it opens no CRM org.
            assert df.mode_for(nl.FEATURE) == "off"
        finally:
            release_tenant(token)

    def test_the_email_list_alone_starts_no_crm_org(self, tenant, monkeypatch):
        _modes(monkeypatch, crm_orgs="", email_orgs="*")
        assert df.mode_for(nl.FEATURE) == "off"

    def test_an_email_feature_does_not_read_the_crm_list(self, tenant, monkeypatch):
        _modes(monkeypatch, "email.cold_check=shadow", crm_orgs="*", email_orgs="")
        assert df.mode_for("email.cold_check") == "off"

    def test_no_tenant_is_off(self, monkeypatch):
        _modes(monkeypatch, crm_orgs="*")
        assert df.mode_for(nl.FEATURE) == "off"

    def test_the_setting_is_a_platform_env_name(self):
        from acb_common.env_guard import is_platform_env

        assert is_platform_env("CRM_DECIDE_ORGS")


# ── Item 10: the cool-down ──────────────────────────────────────────────────


async def _ask(feature: str) -> Any:
    return await df.ask(
        feature, build=lambda: ({"notes": []}, {"n0.action": acb_llm.BooleanQuestion(
            instructions="x")}), read=lambda d: (d, {}), member=OWNER, ids={"decide_notes": 1},
        module_slug="crm")


class TestTheCooldown:
    async def test_a_crm_402_starts_the_cooldown(self, tenant, monkeypatch):
        _fake(monkeypatch, raises=acb_llm.DecideUnavailable("insufficient_credits", status=402))
        assert await _ask(nl.FEATURE) is None
        assert df.cooling()

    async def test_a_crm_403_starts_no_cooldown(self, tenant, monkeypatch):
        _fake(monkeypatch, raises=acb_llm.DecideUnavailable("forbidden", status=403))
        assert await _ask(nl.FEATURE) is None
        assert not df.cooling(), "a CRM 403 must never pause email `on` for the org"

    async def test_an_email_403_still_starts_it(self, tenant, monkeypatch):
        _fake(monkeypatch, raises=acb_llm.DecideUnavailable("forbidden", status=403))
        assert await _ask("email.rule_match") is None
        assert df.cooling()


# ── Item 8: the requests ────────────────────────────────────────────────────


def _notes(n: int, *, body: str = SECRET_BODY, subject: str = SECRET_SUBJECT) -> list[nl.Note]:
    return [nl.Note(id=f"id-{i}", subject=subject, body=body, body_md5=f"m{i}")
            for i in range(n)]


class TestThePack:
    def test_five_notes_and_fifteen_questions_to_a_request(self):
        packed = nl.pack(_notes(12))
        assert [len(state["notes"]) for state, _q, _r in packed] == [5, 5, 2]
        assert [len(q) for _s, q, _r in packed] == [15, 15, 6]
        assert all(len(q) <= df.QUESTION_LIMIT for _s, q, _r in packed)

    def test_a_note_that_failed_before_goes_out_alone(self):
        """Round 2, P2-1: a poison note never drags its batch-mates again."""
        notes = _notes(9)
        notes[3] = nl.Note(id="failed", subject="s", body="b", body_md5="m", failures=1)
        packed = nl.pack(notes)
        sizes = [len(state["notes"]) for state, _q, _r in packed]
        assert sizes == [1, 5, 3]
        assert [n.id for _ref, n in packed[0][2]] == ["failed"]
        assert all(n.failures == 0 for _s, _q, refs in packed[1:] for _r, n in refs)

    def test_the_failure_sql_clears_every_label_column(self):
        """Round 2, P2-2: the text of the fence, beside the R8 proof."""
        branch = nl._FAILED_SQL.split("DO UPDATE SET", 1)[1]
        for column in ("kind", "kind_confidence", "kind_probabilities", "needs_action",
                       "urgency", "urgency_confidence", "urgency_probabilities",
                       "request_id", "labeled_at"):
            assert f"{column} = NULL" in branch, column

    def test_three_questions_for_each_note_in_order_kind_action_urgency(self):
        (_state, questions, refs), = nl.pack(_notes(2))
        assert list(questions) == ["n0.kind", "n0.action", "n0.urgency",
                                   "n1.kind", "n1.action", "n1.urgency"]
        assert questions["n0.kind"].type == "choice"
        assert set(questions["n0.kind"].criteria) == {
            "payment", "site_issue", "commercial", "follow_up", "internal", "other"}
        assert questions["n0.action"].type == "boolean"
        assert set(questions["n0.action"].criteria) == {"true", "false"}
        assert questions["n0.urgency"].type == "score"
        assert tuple(questions["n0.urgency"].criteria) == ("low", "medium", "high")
        assert [r for r, _n in refs] == ["n0", "n1"]

    def test_a_question_names_its_note_by_its_id_in_the_state(self):
        (state, questions, _refs), = nl.pack(_notes(3))
        ids = [n["id"] for n in state["notes"]]
        assert ids == ["n0", "n1", "n2"]
        for qid, q in questions.items():
            ref = qid.split(".", 1)[0]
            assert ref in ids
            assert f"note {ref}" in q.instructions
            assert "`notes`" in q.instructions
            # The instructions name the note. They never copy its text.
            assert SECRET_SUBJECT not in q.instructions and SECRET_BODY not in q.instructions

    def test_the_state_holds_the_id_and_the_clipped_text_only(self):
        (state, _q, _r), = nl.pack(_notes(1, body="b" * 5000, subject="s" * 900))
        note = state["notes"][0]
        assert set(note) == {"id", "subject", "body"}
        assert len(note["body"]) == nl.BODY_CLIP == 2000
        assert len(note["subject"]) == nl.SUBJECT_CLIP
        assert set(state) == {"notes"}

    def test_the_clip_bounds_a_hostile_body_by_its_escaped_length(self):
        (state, _q, _r), = nl.pack(_notes(1, body="\x01" * 5000))
        assert len(state["notes"][0]["body"]) * 6 <= 2 * nl.BODY_CLIP

    def test_none_text_is_an_empty_fact(self):
        (state, _q, _r), = nl.pack([nl.Note(id="x", subject=None, body="b", body_md5="m")])
        assert state["notes"][0]["subject"] == ""

    def test_the_keys_are_the_checks_of_the_migration(self):
        found = [f for f in (REPO / "infra/postgres").glob("*.sql")
                 if "CREATE TABLE IF NOT EXISTS crm_note_labels" in f.read_text(encoding="utf-8")]
        assert len(found) == 1
        sql = found[0].read_text(encoding="utf-8")
        kinds = re.search(r"kind IN \(([^)]*)\)", sql)
        levels = re.search(r"urgency IN \(([^)]*)\)", sql)
        assert kinds and levels
        assert set(re.findall(r"'([a-z_]+)'", kinds.group(1))) == set(nl.KIND_OPTIONS)
        assert tuple(re.findall(r"'([a-z_]+)'", levels.group(1))) == nl.URGENCY_LEVELS


# ── Reading an answer ───────────────────────────────────────────────────────


def _decision(answers: dict[str, Any], rid: str | None = "req-9") -> Any:
    return acb_llm.Decision(answers=MappingProxyType(answers), request_id=rid)


class TestTheReader:
    def test_a_full_answer_becomes_one_row(self):
        d = _decision({
            "n0.kind": acb_llm.ChoiceAnswer(
                choice="site_issue",
                probabilities=MappingProxyType({"site_issue": 0.7, "payment": 0.2, "vendor": 0.1}),
                confidence=0.55),
            "n0.action": acb_llm.BooleanAnswer(probability=0.91),
            "n0.urgency": acb_llm.ScoreAnswer(score=1.4, probabilities=MappingProxyType(
                {"low": 0.1, "medium": 0.6, "high": 0.3}), confidence=0.4, level=None),
        })
        note = nl.Note(id="a1", subject="s", body="b", body_md5="m1")
        rows, fields = nl.reader([("n0", note)])(d)
        assert rows == [{
            "kind": "site_issue", "kind_confidence": 0.55,
            "kind_probabilities": {"site_issue": 0.7, "payment": 0.2},
            "needs_action": 0.91, "urgency": "medium", "urgency_confidence": 0.4,
            "urgency_probabilities": {"low": 0.1, "medium": 0.6, "high": 0.3},
            "activity_id": "a1", "body_md5": "m1", "request_id": "req-9"}]
        assert fields == {"decide_notes": 1, "decide_kinds": 1}

    def test_a_key_that_is_not_ours_is_null_never_the_vendor_string(self):
        d = _decision({
            "n0.kind": acb_llm.ChoiceAnswer(choice="DROP TABLE", probabilities=MappingProxyType({}),
                                            confidence=0.9),
            "n0.action": acb_llm.BooleanAnswer(probability=float("nan")),
            "n0.urgency": acb_llm.ScoreAnswer(score="urgentest", probabilities=MappingProxyType({}),
                                              confidence=None, level="urgentest"),
        })
        row = nl.read_note(d, "n0")
        assert row["kind"] is None and row["urgency"] is None
        assert row["needs_action"] is None
        assert row["kind_probabilities"] is None and row["urgency_probabilities"] is None

    def test_a_number_outside_zero_to_one_is_clamped(self):
        d = _decision({"n0.action": acb_llm.BooleanAnswer(probability=1.7)})
        assert nl.read_note(d, "n0")["needs_action"] == 1.0

    def test_a_missing_answer_is_null(self):
        row = nl.read_note(_decision({}), "n3")
        assert set(v for v in row.values()) == {None}


# ── Items 5, 7 and 10: the step ─────────────────────────────────────────────


class TestTheStep:
    async def test_off_opens_no_session_and_makes_no_call(self, monkeypatch):
        _modes(monkeypatch, modes="")
        box = Box(monkeypatch)
        fake = _fake(monkeypatch)
        got = await nl.label_after_run(ORG)
        assert got.reason == "off"
        assert box.sessions == 0 and fake.calls == [] and box.written == []

    async def test_an_org_outside_the_list_is_off(self, monkeypatch):
        _modes(monkeypatch, crm_orgs=OTHER)
        box = Box(monkeypatch)
        fake = _fake(monkeypatch)
        assert (await nl.label_after_run(ORG)).reason == "off"
        assert box.sessions == 0 and fake.calls == []

    @pytest.mark.parametrize("mode", ["shadow", "on"])
    async def test_the_step_asks_with_a_proven_member_and_bills_the_crm(self, monkeypatch, mode):
        _modes(monkeypatch, f"crm.note_labels={mode}")
        box = Box(monkeypatch, notes=7)
        fake = _fake(monkeypatch)
        got = await nl.label_after_run(ORG)
        assert got.reason == "done" and got.notes == 7 and got.labeled == 7
        assert got.requests == 2 and got.request_ids == ("req-1", "req-2")
        assert len(fake.calls) == 2
        for call in fake.calls:
            assert call["member"] == OWNER and call["member_proven"] is True
            assert call["module_slug"] == "crm"
        assert [len(rows) for rows, _m in box.written] == [5, 2]
        assert {m for _r, m in box.written} == {mode}
        first = box.written[0][0][0]
        assert first["kind"] == "payment" and first["needs_action"] == 0.8
        assert first["urgency"] == "high" and first["request_id"] == "req-1"
        assert first["body_md5"] == "md5-0"

    async def test_the_step_takes_at_most_200_notes(self, monkeypatch):
        _modes(monkeypatch)
        box = Box(monkeypatch, notes=230)
        fake = _fake(monkeypatch)
        got = await nl.label_after_run(ORG)
        assert box.limits == [nl.STEP_LIMIT] == [200]
        assert got.notes == 200 and len(fake.calls) == 40

    async def test_the_daily_cap_counts_requests_narrows_the_step_and_stops_it(
            self, monkeypatch):
        _modes(monkeypatch)
        cap = nl.daily_requests(0)
        box = Box(monkeypatch, notes=230, used=cap - 6, backlog=10)
        fake = _fake(monkeypatch)
        got = await nl.label_after_run(ORG)
        assert box.limits == [30] and len(fake.calls) == 6
        assert got.reason == "done" and box.used() == cap
        full = Box(monkeypatch, notes=230, used=cap, backlog=10)
        fake = _fake(monkeypatch)
        assert (await nl.label_after_run(ORG)).reason == "daily_cap"
        assert full.limits == [] and fake.calls == []

    def test_the_caps_are_notes_over_five_with_a_backlog_allowance(self):
        assert (nl.DAILY_NOTES, nl.BACKLOG_DAILY_NOTES, nl.BACKLOG_THRESHOLD) == (
            1000, 5000, 1000)
        assert nl.daily_requests(0) == nl.daily_requests(nl.BACKLOG_THRESHOLD) == 200
        assert nl.daily_requests(nl.BACKLOG_THRESHOLD + 1) == 1000

    async def test_a_backlog_lifts_the_cap(self, monkeypatch):
        _modes(monkeypatch)
        box = Box(monkeypatch, notes=230, used=nl.daily_requests(0),
                  backlog=nl.BACKLOG_THRESHOLD + 1)
        fake = _fake(monkeypatch)
        got = await nl.label_after_run(ORG)
        assert got.reason == "done" and len(fake.calls) == 40
        assert box.used() == nl.daily_requests(0) + 40

    @pytest.mark.parametrize("raises", [
        acb_llm.DecideRequestInvalid(400, {"reason": "too_long"}),
        acb_llm.DecideUnavailable("unreadable answer: x", status=200),
    ])
    async def test_a_failed_request_is_counted_and_its_batch_fails(self, monkeypatch, raises):
        _modes(monkeypatch)
        box = Box(monkeypatch, notes=12)
        fake = _fake(monkeypatch, raises=raises)
        got = await nl.label_after_run(ORG)
        assert got.reason == "batch_failed" and len(fake.calls) == 1
        assert box.used() == 1, "a billed request with no row still counts"
        assert box.failed == [[n.id for n in box.notes[:5]]] and box.written == []

    async def test_a_timeout_is_counted_and_its_batch_fails(self, monkeypatch):
        _modes(monkeypatch)
        monkeypatch.setattr(df, "ON_BOUND_S", 0.05)
        box = Box(monkeypatch, notes=3)

        async def _slow(state, questions, **kw):
            await asyncio.sleep(1)

        monkeypatch.setattr(acb_llm, "decide", _slow)
        got = await nl.label_after_run(ORG)
        assert got.reason == "batch_failed" and box.used() == 1
        assert box.failed == [[n.id for n in box.notes]]

    async def test_an_outage_counts_the_request_and_fails_no_note(self, monkeypatch):
        _modes(monkeypatch)
        box = Box(monkeypatch, notes=12)
        _fake(monkeypatch, raises=acb_llm.DecideUnavailable("HTTP 503", status=503))
        got = await nl.label_after_run(ORG)
        assert got.reason == "no_answer" and box.used() == 1 and box.failed == []

    def test_only_a_cause_of_the_batch_fails_it(self):
        for reason in ("request_invalid", "timeout", "unreadable:KeyError",
                       "unreadable answer: x", "build_failed:TypeError"):
            assert nl.batch_failed(reason), reason
        for reason in (None, "", "cooldown", "disabled", "HTTP 503", "tier_unknown",
                       "insufficient_credits", "forbidden", "budget"):
            assert not nl.batch_failed(reason), reason

    async def test_a_request_hours_old_still_counts_and_the_next_day_starts_again(
            self, monkeypatch):
        """Verifier F1: the window holds a whole UTC day, not an hour."""
        from datetime import UTC, datetime

        _modes(monkeypatch)
        cap = nl.daily_requests(0)
        early = datetime(2026, 10, 16, 1, 0, tzinfo=UTC)
        box = Box(monkeypatch, notes=5, backlog=10, today="2026-10-16")
        _fake(monkeypatch)
        monkeypatch.setattr(nl, "daily_requests", lambda _b: 1)
        assert (await nl.label_after_run(ORG, now=early)).requests == 1
        later = datetime(2026, 10, 16, 5, 0, tzinfo=UTC)
        assert (await nl.label_after_run(ORG, now=later)).reason == "daily_cap"
        evening = datetime(2026, 10, 16, 23, 59, tzinfo=UTC)
        assert (await nl.label_after_run(ORG, now=evening)).reason == "daily_cap"
        nxt = datetime(2026, 10, 17, 0, 1, tzinfo=UTC)
        assert (await nl.label_after_run(ORG, now=nxt)).requests == 1
        assert box.redis.ttl[box.key("2026-10-16")] == nl.REQUESTS_TTL_S >= 24 * 3600
        assert cap == 200

    async def test_a_redis_failure_asks_nothing(self, monkeypatch):
        _modes(monkeypatch)
        box = Box(monkeypatch, notes=5)
        box.redis.fail = ConnectionError("down")
        fake = _fake(monkeypatch)
        assert (await nl.label_after_run(ORG)).reason == "failed"
        assert fake.calls == []

    @pytest.mark.parametrize("left", ["no_org", "other_org", "status", "unverified"])
    async def test_a_member_who_left_the_org_spends_nothing(self, monkeypatch, left):
        _modes(monkeypatch)
        box = Box(monkeypatch, notes=4, left=left)
        fake = _fake(monkeypatch)
        with structlog.testing.capture_logs() as caps:
            got = await nl.label_after_run(ORG)
        assert got.reason == "member_left" and fake.calls == [] and box.used() == 0
        (line,) = _events(caps, "crm.note_labels.skipped")
        assert line["member_reason"] == left and line["notes"] == 4
        assert OWNER not in repr(caps)
        assert box.left_asked == [OWNER]


    async def test_no_member_skips_and_logs_a_count(self, monkeypatch):
        _modes(monkeypatch)
        box = Box(monkeypatch, notes=4, member=None)
        fake = _fake(monkeypatch)
        with structlog.testing.capture_logs() as caps:
            got = await nl.label_after_run(ORG)
        assert got.reason == "no_member" and got.notes == 4
        assert fake.calls == [] and box.written == []
        (line,) = _events(caps, "crm.note_labels.skipped")
        assert line["notes"] == 4 and line["reason"] == "no_member"

    async def test_a_402_stops_the_step_and_the_next_step_asks_nothing(self, monkeypatch):
        _modes(monkeypatch)
        box = Box(monkeypatch, notes=12)
        fake = _fake(monkeypatch, raises=acb_llm.DecideUnavailable(
            "insufficient_credits", status=402))
        got = await nl.label_after_run(ORG)
        assert got.reason == "cooldown" and len(fake.calls) == 1 and box.written == []
        again = await nl.label_after_run(ORG)
        assert again.reason == "cooldown" and len(fake.calls) == 1

    async def test_a_403_stops_the_step_and_pauses_nothing(self, monkeypatch):
        _modes(monkeypatch)
        Box(monkeypatch, notes=12)
        fake = _fake(monkeypatch, raises=acb_llm.DecideUnavailable("forbidden", status=403))
        got = await nl.label_after_run(ORG)
        assert got.reason == "no_answer" and len(fake.calls) == 1
        token = bind_tenant(ORG)
        try:
            assert not df.cooling()
        finally:
            release_tenant(token)

    async def test_a_stop_ends_the_step_between_requests(self, monkeypatch):
        _modes(monkeypatch)
        Box(monkeypatch, notes=12)
        fake = _fake(monkeypatch)
        asked: list[int] = []

        def _stop() -> bool:
            asked.append(1)
            return len(fake.calls) >= 1

        got = await nl.label_after_run(ORG, should_stop=_stop)
        assert got.reason == "stopped" and len(fake.calls) == 1

    async def test_a_broken_step_never_raises(self, monkeypatch):
        _modes(monkeypatch)
        Box(monkeypatch)

        async def _boom(db, org, limit):
            raise RuntimeError(SECRET_BODY)

        monkeypatch.setattr(nl, "_pending", _boom)
        with structlog.testing.capture_logs() as caps:
            got = await nl.label_after_run(ORG)
        assert got.reason == "failed"
        assert SECRET_BODY not in repr(caps)

    async def test_no_log_line_holds_note_text(self, monkeypatch):
        _modes(monkeypatch)
        Box(monkeypatch, notes=7)
        _fake(monkeypatch)
        with structlog.testing.capture_logs() as caps:
            await nl.label_after_run(ORG)
        (step,) = _events(caps, "crm.note_labels.step")
        assert step["labeled"] == 7 and step["request_ids"] == ["req-1", "req-2"]
        assert len(_events(caps, "decide.decided")) == 2
        text = repr(caps)
        assert SECRET_SUBJECT not in text and SECRET_BODY not in text

    async def test_the_step_binds_the_tenant_and_releases_it(self, monkeypatch):
        from acb_common.db import current_tenant

        _modes(monkeypatch)
        Box(monkeypatch, notes=1)
        seen: list[str | None] = []
        fake = _fake(monkeypatch)
        original = fake.__call__

        async def _spy(state, questions, **kw):
            seen.append(current_tenant())
            return await original(state, questions, **kw)

        monkeypatch.setattr(acb_llm, "decide", _spy)
        await nl.label_after_run(ORG)
        assert seen == [ORG]
        assert current_tenant() is None


class TestTheMemberCheck:
    """🔴 Money. ``_member_left`` is ``my_day_loop.departed``, the one rule:
    the member must resolve to THIS org and be ``active`` in it."""

    def _identity(self, monkeypatch, home, status="active", fail=False):
        import acb_auth.access as access
        from gateway import my_day_loop

        async def _resolve(email):
            if fail:
                raise RuntimeError("pool timeout")
            return ("uid", home) if home else (None, None)

        async def _status(org, member):
            return status

        monkeypatch.setattr(access, "resolve_identity", _resolve)
        monkeypatch.setattr(access, "identity_read_failed", lambda: False)
        monkeypatch.setattr(my_day_loop, "_member_status", _status)

    async def test_left_with_no_other_org(self, monkeypatch):
        self._identity(monkeypatch, None, status="removed")
        assert await nl._member_left(ORG, OWNER) == "no_org"

    async def test_in_two_orgs_resolves_to_none(self, monkeypatch):
        # resolve_identity answers (None, None) for two active memberships,
        # and never picks one (its P2a contract).
        self._identity(monkeypatch, None)
        assert await nl._member_left(ORG, OWNER) == "no_org"

    async def test_moved_to_one_other_org(self, monkeypatch):
        self._identity(monkeypatch, OTHER)
        assert await nl._member_left(ORG, OWNER) == "other_org"

    async def test_removed_in_this_org(self, monkeypatch):
        self._identity(monkeypatch, ORG, status="removed")
        assert await nl._member_left(ORG, OWNER) == "status"

    async def test_a_failed_read_spends_nothing(self, monkeypatch):
        self._identity(monkeypatch, ORG, fail=True)
        assert await nl._member_left(ORG, OWNER) == "unverified"

    async def test_an_active_member_of_this_org_pays(self, monkeypatch):
        self._identity(monkeypatch, ORG)
        assert await nl._member_left(ORG, OWNER) is None


class TestLastNoAnswer:
    async def test_it_names_the_reason_and_clears_on_a_decision(self, tenant, monkeypatch):
        _fake(monkeypatch, raises=acb_llm.DecideRequestInvalid(400, {"reason": "x"}))
        assert await _ask(nl.FEATURE) is None
        assert df.last_no_answer() == "request_invalid"
        _fake(monkeypatch)
        assert await _ask(nl.FEATURE) is not None
        assert df.last_no_answer() is None


# ── Item 5: the scheduler ───────────────────────────────────────────────────


class TestTheScheduler:
    async def test_run_one_starts_the_step_after_the_run(self, monkeypatch):
        _modes(monkeypatch)
        order: list[str] = []

        async def _run_org(org, *, should_stop):
            order.append("run")

        async def _label(org, *, should_stop):
            order.append("label")
            return nl.Labeled("done")

        monkeypatch.setattr(scheduler.engine, "run_org", _run_org)
        monkeypatch.setattr(nl, "label_after_run", _label)
        scheduler._stopping.clear()
        assert await scheduler._run_one(ORG, asyncio.Semaphore(1)) == 1
        await asyncio.gather(*list(scheduler._background))
        assert order == ["run", "label"]

    async def test_a_failed_run_start_labels_nothing(self, monkeypatch):
        _modes(monkeypatch)
        started: list[str] = []

        async def _run_org(org, *, should_stop):
            raise scheduler.SyncRunning()

        monkeypatch.setattr(scheduler.engine, "run_org", _run_org)
        monkeypatch.setattr(scheduler, "start_labels", lambda org: started.append(org))
        scheduler._stopping.clear()
        assert await scheduler._run_one(ORG, asyncio.Semaphore(1)) == 0
        assert started == []

    async def test_mode_off_starts_no_task(self, monkeypatch):
        _modes(monkeypatch, modes="")
        scheduler._stopping.clear()
        assert scheduler.start_labels(ORG) is None
        assert ORG not in scheduler._labeling

    async def test_one_step_at_a_time_for_each_org(self, monkeypatch):
        _modes(monkeypatch)
        gate = asyncio.Event()

        async def _label(org, *, should_stop):
            await gate.wait()
            return nl.Labeled("done")

        monkeypatch.setattr(nl, "label_after_run", _label)
        scheduler._stopping.clear()
        first = scheduler.start_labels(ORG)
        assert first is not None
        assert scheduler.start_labels(ORG) is None
        gate.set()
        await first
        assert ORG not in scheduler._labeling

    async def test_a_stopping_loop_starts_no_step(self, monkeypatch):
        _modes(monkeypatch)
        scheduler._stopping.set()
        try:
            assert scheduler.start_labels(ORG) is None
        finally:
            scheduler._stopping.clear()

    async def test_a_task_cancelled_before_its_first_step_frees_the_org(self, monkeypatch):
        """Fix round item 4: the done-callback, not a ``finally``, frees it."""
        _modes(monkeypatch)
        scheduler._stopping.clear()
        task = scheduler.start_labels(ORG)
        assert task is not None and ORG in scheduler._labeling
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        await asyncio.sleep(0)
        assert ORG not in scheduler._labeling
        assert scheduler.start_labels(ORG) is not None

    async def test_a_gate_that_raises_starts_nothing_and_the_run_still_counts(
            self, monkeypatch):
        """Verifier F2: the ``except`` of ``start_labels``."""
        _modes(monkeypatch)

        def _boom(org):
            raise RuntimeError("settings")

        async def _run_org(org, *, should_stop):
            return None

        async def _execute(handle, *, should_stop):
            return "RESULT"

        monkeypatch.setattr(nl, "enabled_for", _boom)
        monkeypatch.setattr(scheduler.engine, "run_org", _run_org)
        monkeypatch.setattr(scheduler.engine, "execute", _execute)
        scheduler._stopping.clear()
        with structlog.testing.capture_logs() as caps:
            assert scheduler.start_labels(ORG) is None
            assert await scheduler._run_one(ORG, asyncio.Semaphore(1)) == 1
            assert await scheduler.spawn(SimpleNamespace(org=ORG)) == "RESULT"
        assert ORG not in scheduler._labeling
        assert len([c for c in caps if "crm.note_labels.not_started" in str(c.get("event"))]) == 3

    async def test_spawn_returns_the_run_result_and_then_labels(self, monkeypatch):
        _modes(monkeypatch)
        order: list[str] = []

        async def _execute(handle, *, should_stop):
            order.append("run")
            return "RESULT"

        async def _label(org, *, should_stop):
            order.append(f"label:{org}")
            return nl.Labeled("done")

        monkeypatch.setattr(scheduler.engine, "execute", _execute)
        monkeypatch.setattr(nl, "label_after_run", _label)
        scheduler._stopping.clear()
        task = scheduler.spawn(SimpleNamespace(org=ORG))
        assert await task == "RESULT"
        await asyncio.gather(*[t for t in list(scheduler._background) if t is not task])
        assert order == ["run", f"label:{ORG}"]


# ── Item 9: no route reads the labels ───────────────────────────────────────


_TABLE = re.compile(r"(?<![A-Za-z0-9_])crm_note_labels(?![A-Za-z0-9_])")


class TestNoRouteReadsTheLabels:
    """In ``shadow`` and in ``on`` no route returns the labels. The table is
    named by its one writer, the migration, and the two reset lists, where
    the routes only DELETE it."""

    WRITER = "apps/services/gateway/gateway/crm_sync/note_labels.py"
    RESETS = ("apps/services/gateway/gateway/routes/crm/core.py",
              "apps/services/gateway/gateway/routes/crm/settings.py")

    def _naming(self) -> set[str]:
        out: set[str] = set()
        for root in ("apps", "packages"):
            for path in (REPO / root).rglob("*.py"):
                # The table name as a word. A test path such as
                # `test_crm_note_labels.py` in a comment does not count.
                if _TABLE.search(path.read_text(encoding="utf-8", errors="ignore")):
                    out.add(path.relative_to(REPO).as_posix())
        return out

    def test_only_the_writer_and_the_reset_lists_name_the_table(self):
        assert self._naming() == {self.WRITER, *self.RESETS}

    def test_the_reset_lists_name_it_only_inside_their_delete_tuples(self):
        for rel, name in zip(self.RESETS, ("RESET_TABLES", "_RESET_DELETES"), strict=True):
            tree = ast.parse((REPO / rel).read_text(encoding="utf-8"))
            hits = [n for n in ast.walk(tree)
                    if isinstance(n, ast.Constant) and n.value == "crm_note_labels"]
            assert len(hits) == 1, rel
            owner = next(n for n in ast.walk(tree)
                         if isinstance(n, ast.AnnAssign | ast.Assign)
                         and any(c is hits[0] for c in ast.walk(n)))
            target = owner.target if isinstance(owner, ast.AnnAssign) else owner.targets[0]
            assert getattr(target, "id", None) == name

    def test_no_route_imports_the_step(self):
        for path in (REPO / "apps/services/gateway/gateway/routes").rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            assert "crm_sync import note_labels" not in source, path
            assert "crm_sync.note_labels" not in source, path


# ── Item 11: the eval ───────────────────────────────────────────────────────


class TestTheEval:
    """``evals/crm_note_labels`` passes in ``--scripted`` mode, and each bar
    fails on a run that breaks it (R7)."""

    def _load(self):
        from evals.crm_note_labels import checkers
        from evals.crm_note_labels import run as eval_run
        from evals.crm_note_labels.dataset import load_answers, load_notes

        return checkers, eval_run, load_notes(), load_answers()

    def test_the_scripted_run_passes_every_bar(self, capsys):
        from evals.crm_note_labels import run as eval_run

        assert eval_run.main(["--scripted"]) == eval_run.EXIT_PASS
        assert '"passed": true' in capsys.readouterr().out

    def test_without_scripted_the_run_cannot_start(self):
        from evals.crm_note_labels import run as eval_run

        assert eval_run.main([]) == eval_run.EXIT_NO_GO

    def test_the_set_covers_each_kind_and_each_level(self):
        _c, _r, notes, answers = self._load()
        assert {n.expected.kind for n in notes} == set(nl.KIND_OPTIONS)
        assert {n.expected.urgency for n in notes} == set(nl.URGENCY_LEVELS)
        assert {n.expected.action for n in notes} == {True, False}
        assert set(answers) == {n.id for n in notes}
        assert any(len(n.body) > nl.BODY_CLIP for n in notes), "a long note to cut"

    @pytest.mark.parametrize("part,value,bar", [
        ("kind", {"choice": "other"}, "kind"),
        ("action", {"probability": 0.1}, "action"),
        ("urgency", {"score": 0, "level": "low"}, "urgency"),
    ])
    def test_a_wrong_answer_fails_its_bar(self, part, value, bar):
        import copy

        checkers, eval_run, notes, answers = self._load()
        changed = copy.deepcopy(answers)
        changed["pay-overdue"][part] = value
        produced, problems = eval_run.run_scripted(notes, changed)
        result = checkers.score(notes, produced, scripted=True, shape_problems=problems)
        assert result.bars()[bar] is False and not result.passed()

    def test_a_row_with_vendor_text_fails_no_text(self):
        checkers, eval_run, notes, answers = self._load()
        produced, _problems = eval_run.run_scripted(notes, answers)
        produced["pay-overdue"] = {**produced["pay-overdue"], "kind_probabilities": {
            "payment": 0.5, "Kestrel Chemicals": 0.5}}
        assert checkers.score(notes, produced, scripted=True).bars()["no_text"] is False

    def test_a_request_past_the_limits_fails_shape(self, monkeypatch):
        checkers, eval_run, notes, answers = self._load()
        monkeypatch.setattr(nl, "NOTES_PER_REQUEST", 6)
        produced, problems = eval_run.run_scripted(notes, answers)
        assert problems
        assert checkers.score(notes, produced, scripted=True,
                              shape_problems=problems).bars()["shape"] is False
