"""WS-53 CRM-Q20-1 — the mapping pipeline on the decide tier (hermetic).

Spec: ``project-docs/specs/crm_platform.md`` §18.9.4 to §18.9.6, "CRM-Q20-1
· The pipeline, backend". ``test_crm_map_pipeline_r8.py`` proves the SQL,
the draft, the 422 of rule 1 and the cursor row on a real Postgres. The
five new transforms are in ``test_crm_executor.py``.

A scripted fake of ``decide`` tests each branch. It is the asker of
``evals/crm_mapping/run.py``, so the eval and the tests replay one set of
answers. No test here makes a real model call.

* Item 1: the gate is ``mode_for``, for each feature. An org outside
  ``CRM_DECIDE_ORGS`` makes 0 ``ask`` calls.
* Item 2: the payer. No member, or a member who left, spends nothing.
* Item 3: the run writes a draft through ``write_draft``, never a live rule.
* Item 4: the scheduler starts it after a run, outside the run lock.
* Item 5: the token cap, the run cap and the day cap.
* Item 7: each mode of each feature.
* Item 8: the three rules of the kinds (``check_kinds``).
* Item 9: the stage values with no entry.
* Item 10: an unchanged schema makes 0 ``ask`` calls.
* The six steps of §18.9.4, the log lines and the eval.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
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
from fastapi import HTTPException
from gateway import decide_features as df
from gateway.crm_sync import map_pipeline as mp
from gateway.crm_sync import note_labels as nl
from gateway.crm_sync import runs, scheduler
from gateway.routes.crm import mapping_plans

from evals.crm_mapping import run as eval_run
from evals.crm_mapping.dataset import load_answers, load_schemas

REPO = Path(__file__).resolve().parents[2]
SOURCE = REPO / "apps/services/gateway/gateway/crm_sync/map_pipeline.py"
ORG = "20202020-0000-4000-8000-000000000001"
OTHER = "20202020-0000-4000-8000-000000000002"
OWNER = "admin@crm-q20.example"

ALL_ON = "crm.map_role=on,crm.map_target=on,crm.map_stage=on"
ALL_SHADOW = "crm.map_role=shadow,crm.map_target=shadow,crm.map_stage=shadow"


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
    scheduler._mapping.clear()
    with run_context_scope():
        clear_run_context()
        yield
    monkeypatch.undo()
    _clear()
    df._cooldown_until.clear()
    scheduler._mapping.clear()


def _modes(monkeypatch, modes: str = ALL_ON, crm_orgs: str = ORG, email_orgs: str = "") -> None:
    monkeypatch.setenv("DECIDE_FEATURE_MODES", modes)
    monkeypatch.setenv("CRM_DECIDE_ORGS", crm_orgs)
    monkeypatch.setenv("DECIDE_FEATURE_ORGS", email_orgs)
    _clear()


def _schema(schema_id: str = "dewin") -> Any:
    return next(s for s in load_schemas() if s.id == schema_id)


def _answers(schema_id: str = "dewin", mode: str = "on") -> dict[str, Any]:
    return load_answers()[schema_id].get(mode, {})


ON = dict(eval_run.ON)
SHADOW = dict(eval_run.SHADOW)
OFF = {f: "off" for f in mp.FEATURES}


async def _pipeline(schema_id: str = "dewin", modes: dict[str, str] | None = None,
                    cache: mp.Cache | None = None, answers: dict[str, Any] | None = None,
                    ) -> tuple[mp.Result, eval_run.ScriptedAsker]:
    schema = _schema(schema_id)
    asker = eval_run.ScriptedAsker(answers if answers is not None else _answers(schema_id))
    pipeline = mp.Pipeline(schema.inputs, modes or ON, cache or mp.Cache(),
                           eval_run.EvalSource(schema), asker)
    return await pipeline.run(), asker


def _asked(asker: eval_run.ScriptedAsker) -> list[str]:
    return [qid for _f, reqs in asker.calls for _s, q in reqs for qid in q]


def _cache(result: mp.Result) -> mp.Cache:
    return eval_run._cache_of(result)


class FakeDecide:
    """``acb_llm.decide``, scripted by the eval's asker. Records each call
    and its keyword arguments."""

    def __init__(self, answers: dict[str, Any] | None = None,
                 raises: BaseException | None = None) -> None:
        self.scripted = eval_run.ScriptedAsker(answers if answers is not None else _answers())
        self.calls: list[dict[str, Any]] = []
        self.raises = raises

    async def __call__(self, state, questions, **kwargs):
        self.calls.append({"state": state, "questions": dict(questions), **kwargs})
        if self.raises is not None:
            raise self.raises
        return await self.scripted("x", [(state, questions)])


class Box:
    """The database half of a run, faked: the sessions, the load, the
    payer, the draft and the cursor row. The day counter is the real
    ``reserve_requests`` over a fake Redis."""

    def __init__(self, monkeypatch, *, schema_id: str = "dewin", member: str | None = OWNER,
                 left: str | None = None, cache: mp.Cache | None = None, used: int = 0,
                 loaded: bool = True) -> None:
        from acb_common import tenant_redis
        from acb_common.tenant_redis import TenantRedis

        from tests.unit.test_feed_decide import FakeRedis

        self.schema = _schema(schema_id)
        self.sessions = 0
        self.loads = 0
        self.drafts: list[list[dict[str, Any]]] = []
        self.cursors: list[Any] = []
        self.left_asked: list[str] = []
        self.redis = FakeRedis()
        monkeypatch.setattr(tenant_redis, "get_tenant_redis", lambda **_: TenantRedis(self.redis))
        self.today = mp._utc_date()
        if used:
            self.redis.store[self.key()] = str(used)
        start = cache or mp.Cache()

        @contextlib.asynccontextmanager
        async def _session(org=None):
            self.sessions += 1
            yield SimpleNamespace()

        async def _load(db, org, modes):
            self.loads += 1
            return (self.schema.inputs, start) if loaded else None

        async def _member(db, org):
            return member

        async def _member_left(org, who):
            self.left_asked.append(who)
            return left

        async def _write_draft(db, *, items, by=mapping_plans.SUGGEST_ACTOR):
            self.drafts.append([dict(i) for i in items])
            return SimpleNamespace(version=7)

        async def _write_cursor(db, org, row, status):
            self.cursors.append(row)

        monkeypatch.setattr(mp, "tenant_session", _session)
        monkeypatch.setattr(mp, "load", _load)
        monkeypatch.setattr(nl, "_member", _member)
        monkeypatch.setattr(nl, "_member_left", _member_left)
        monkeypatch.setattr(mapping_plans, "write_draft", _write_draft)
        monkeypatch.setattr(runs, "write_cursor", _write_cursor)
        monkeypatch.setattr(mp, "DbSource", lambda org: eval_run.EvalSource(self.schema))

    def key(self, org: str = ORG) -> str:
        return f"cc:{org}:{mp.NAMESPACE}:requests:{self.today}"

    def used(self) -> int:
        return int(self.redis.store.get(self.key(), "0"))


def _fake(monkeypatch, **kw) -> FakeDecide:
    fake = FakeDecide(**kw)
    monkeypatch.setattr(acb_llm, "decide", fake)
    return fake


def _events(caps, name: str) -> list[dict[str, Any]]:
    return [c for c in caps if c.get("event") == name]


# ── Item 1: the gate ────────────────────────────────────────────────────────


class TestTheGate:
    def test_the_three_features_join_the_crm_set_and_not_the_email_set(self):
        for feature in mp.FEATURES:
            assert feature in df.CRM_FEATURES
            assert feature in df.REGISTERED
            assert feature in df.ON_FEATURES
            assert feature not in df.FEATURES
            assert df._parse_modes("")[feature] == "off"

    @pytest.mark.parametrize("mode", ["shadow", "on"])
    def test_shadow_and_on_are_accepted(self, monkeypatch, mode):
        _modes(monkeypatch, ",".join(f"{f}={mode}" for f in mp.FEATURES))
        token = bind_tenant(ORG)
        try:
            assert mp.modes_now() == dict.fromkeys(mp.FEATURES, mode)
        finally:
            release_tenant(token)

    async def test_an_org_outside_crm_decide_orgs_makes_no_ask_call(self, monkeypatch):
        """Verdict (b): the gate is ``mode_for``, checked per feature."""
        _modes(monkeypatch, ALL_ON, crm_orgs=OTHER, email_orgs="*")
        fake = _fake(monkeypatch)
        box = Box(monkeypatch)
        got = await mp.map_after_run(ORG)
        assert got.reason == "off"
        assert fake.calls == [], "an org outside CRM_DECIDE_ORGS asks nothing"
        assert box.sessions == 0 and box.loads == 0, "and reads nothing"
        assert not mp.enabled_for(ORG)

    async def test_the_list_of_the_org_opens_it(self, monkeypatch):
        _modes(monkeypatch, ALL_ON, crm_orgs=ORG)
        fake = _fake(monkeypatch)
        Box(monkeypatch)
        got = await mp.map_after_run(ORG)
        assert got.reason == "done" and fake.calls
        assert mp.enabled_for(ORG)

    async def test_a_feature_that_is_off_builds_no_question(self):
        """Each feature is gated alone: ``target`` off asks no target, and
        ``role`` off writes no role."""
        modes = {**ON, mp.FEATURE_TARGET: "off"}
        result, asker = await _pipeline("commercials", modes)
        assert not [q for q in _asked(asker) if q.startswith("t.")]
        assert not [i for i in result.items if i["kind"] == "target"]
        assert [i for i in result.items if i["kind"] == "stage"], "stage is on"
        modes = {**ON, mp.FEATURE_ROLE: "off"}
        result, asker = await _pipeline("commercials", modes)
        assert not [q for q in _asked(asker) if q.startswith("role.")]
        assert result.items == [], "no approved role, so no target and no stage"

    async def test_each_call_goes_through_ask_on_crm_with_a_proven_member(self, monkeypatch):
        _modes(monkeypatch)
        fake = _fake(monkeypatch)
        Box(monkeypatch)
        await mp.map_after_run(ORG)
        assert fake.calls
        for call in fake.calls:
            assert call["module_slug"] == "crm"
            assert call["member"] == OWNER and call["member_proven"] is True
            assert len(call["questions"]) <= df.QUESTION_LIMIT

    def test_the_source_asks_only_through_the_registry(self):
        source = SOURCE.read_text(encoding="utf-8")
        assert "df.mode_for(f) for f in FEATURES" in source
        assert "df.ask(" in source
        for banned in ("df.shadow(", "acompletion", "from acb_llm import decide",
                       "acb_llm.decide(", "tier-", "decide_feature_modes", "crm_decide_orgs"):
            assert banned not in source, banned


# ── Item 2: who pays ────────────────────────────────────────────────────────


class TestThePayer:
    async def test_no_member_spends_nothing(self, monkeypatch):
        _modes(monkeypatch)
        fake = _fake(monkeypatch)
        box = Box(monkeypatch, member=None)
        with structlog.testing.capture_logs() as caps:
            got = await mp.map_after_run(ORG)
        assert got.reason == "no_member" and got.requests == 0
        assert fake.calls == [] and box.used() == 0
        (line,) = _events(caps, "crm.map_pipeline.step")
        assert line["reason"] == "no_member" and line["requests"] == 0

    async def test_a_member_who_left_spends_nothing(self, monkeypatch):
        _modes(monkeypatch)
        fake = _fake(monkeypatch)
        box = Box(monkeypatch, left="status")
        got = await mp.map_after_run(ORG)
        assert got.reason == "member_left"
        assert fake.calls == [] and box.used() == 0
        assert box.left_asked == [OWNER]

    async def test_the_payer_is_checked_once_before_the_first_request(self, monkeypatch):
        _modes(monkeypatch)
        fake = _fake(monkeypatch)
        box = Box(monkeypatch)
        got = await mp.map_after_run(ORG)
        assert got.requests >= 2 and len(fake.calls) == got.requests
        assert box.left_asked == [OWNER]

    async def test_a_run_with_nothing_to_ask_checks_no_payer(self, monkeypatch):
        _modes(monkeypatch, "crm.map_role=shadow")
        _fake(monkeypatch)
        box = Box(monkeypatch, member=None)
        got = await mp.map_after_run(ORG)
        assert got.reason == "done" and box.left_asked == []


# ── Item 3: a draft, never a live rule ──────────────────────────────────────


class TestTheDraft:
    async def test_the_run_writes_one_draft_through_write_draft(self, monkeypatch):
        _modes(monkeypatch)
        _fake(monkeypatch)
        box = Box(monkeypatch)
        got = await mp.map_after_run(ORG)
        assert got.draft == 7
        (items,) = box.drafts
        kinds = {i["kind"] for i in items}
        assert kinds == {"role", "target"}
        mapping_plans.check_kinds(items)

    async def test_shadow_writes_no_draft(self, monkeypatch):
        _modes(monkeypatch, ALL_SHADOW)
        _fake(monkeypatch, answers=_answers("dewin", "shadow"))
        box = Box(monkeypatch)
        got = await mp.map_after_run(ORG)
        assert got.reason == "done" and box.drafts == []
        assert got.requests >= 1, "the shadow asks its questions"

    def test_the_pipeline_writes_no_rule_row_and_no_sql_write(self):
        source = SOURCE.read_text(encoding="utf-8")
        assert "crm_field_rules" not in source
        assert not re.search(r"\b(INSERT|UPDATE|DELETE)\b", source.replace("UPDATE crm", ""))
        assert "mapping_plans.write_draft(" in source
        assert "runs.write_cursor(" in source


# ── Item 4: the scheduler ───────────────────────────────────────────────────


class TestTheScheduler:
    async def test_run_one_starts_the_pipeline_after_the_run(self, monkeypatch):
        _modes(monkeypatch)
        order: list[str] = []

        async def _run_org(org, *, should_stop):
            order.append("run")

        async def _map(org, *, should_stop):
            order.append("map")
            return mp.Mapped("done")

        monkeypatch.setattr(scheduler.engine, "run_org", _run_org)
        monkeypatch.setattr(scheduler, "start_labels", lambda org: None)
        monkeypatch.setattr(mp, "map_after_run", _map)
        scheduler._stopping.clear()
        assert await scheduler._run_one(ORG, asyncio.Semaphore(1)) == 1
        await asyncio.gather(*list(scheduler._background))
        assert order == ["run", "map"]

    async def test_spawn_starts_it_after_the_run(self, monkeypatch):
        _modes(monkeypatch)
        order: list[str] = []

        async def _execute(handle, *, should_stop):
            order.append("run")
            return "result"

        async def _map(org, *, should_stop):
            order.append("map")
            return mp.Mapped("done")

        monkeypatch.setattr(scheduler.engine, "execute", _execute)
        monkeypatch.setattr(scheduler, "start_labels", lambda org: None)
        monkeypatch.setattr(mp, "map_after_run", _map)
        scheduler._stopping.clear()
        task = scheduler.spawn(SimpleNamespace(org=ORG))
        assert await task == "result"
        await asyncio.gather(*[t for t in list(scheduler._background) if t is not task])
        assert order == ["run", "map"]

    async def test_all_modes_off_starts_no_task(self, monkeypatch):
        _modes(monkeypatch, "")
        scheduler._stopping.clear()
        assert scheduler.start_mapping(ORG) is None
        assert ORG not in scheduler._mapping

    async def test_one_run_at_a_time_for_each_org(self, monkeypatch):
        _modes(monkeypatch)
        gate = asyncio.Event()

        async def _map(org, *, should_stop):
            await gate.wait()
            return mp.Mapped("done")

        monkeypatch.setattr(mp, "map_after_run", _map)
        scheduler._stopping.clear()
        first = scheduler.start_mapping(ORG)
        assert first is not None
        assert scheduler.start_mapping(ORG) is None
        gate.set()
        await first
        assert ORG not in scheduler._mapping

    async def test_a_stopping_loop_starts_nothing(self, monkeypatch):
        _modes(monkeypatch)
        scheduler._stopping.set()
        try:
            assert scheduler.start_mapping(ORG) is None
        finally:
            scheduler._stopping.clear()


# ── Item 5: the caps ────────────────────────────────────────────────────────


class TestTheCaps:
    async def test_the_token_cap_stops_the_run_and_keeps_a_draft(self, monkeypatch):
        """Past the cap the run stops, and keeps what it decided: here the
        role, and not the targets."""
        full, _asker = await _pipeline()
        role_cost = mp.estimate([({"x": 1}, {})])  # a floor for the check below
        assert full.tokens > role_cost
        monkeypatch.setattr(mp, "TOKEN_CAP", 700)
        result, asker = await _pipeline()
        assert result.reason == "token_cap"
        assert result.tokens <= 700
        assert [i["kind"] for i in result.items] == ["role"]
        assert mp.SLOTS_KEY in result.done
        assert not [k for k in result.done if k.startswith("module:")], (
            "a key that did not finish keeps no hash, so the next run asks it")
        assert len(asker.calls) == 1

    async def test_the_run_cap_stops_the_run(self, monkeypatch):
        monkeypatch.setattr(mp, "RUN_REQUESTS", 1)
        result, asker = await _pipeline()
        assert result.reason == "run_cap" and len(asker.calls) == 1

    async def test_the_day_cap_counts_before_each_request(self, monkeypatch):
        _modes(monkeypatch)
        fake = _fake(monkeypatch)
        box = Box(monkeypatch, used=mp.DAILY_REQUESTS)
        got = await mp.map_after_run(ORG)
        assert got.reason == "daily_cap"
        assert fake.calls == [] and box.used() == mp.DAILY_REQUESTS

    async def test_a_request_that_fails_still_counts(self, monkeypatch):
        _modes(monkeypatch)
        fake = _fake(monkeypatch, raises=acb_llm.DecideUnavailable("insufficient_credits",
                                                                   status=402))
        box = Box(monkeypatch)
        got = await mp.map_after_run(ORG)
        assert got.reason == "no_answer" and len(fake.calls) == 1
        assert box.used() == 1 and got.requests == 1
        token = bind_tenant(ORG)
        try:
            assert df.cooling(), "a CRM 402 starts the cool-down"
        finally:
            release_tenant(token)

    async def test_a_cooling_org_asks_nothing(self, monkeypatch):
        _modes(monkeypatch)
        fake = _fake(monkeypatch)
        box = Box(monkeypatch)
        df._cooldown_until[ORG] = df._now() + 60
        got = await mp.map_after_run(ORG)
        assert got.reason == "cooldown" and fake.calls == [] and box.loads == 0

    def test_the_caps_are_constants(self):
        assert mp.TOKEN_CAP == 20_000
        assert mp.RUN_REQUESTS == 12 and mp.DAILY_REQUESTS == 40
        source = SOURCE.read_text(encoding="utf-8")
        assert "from gateway.kb_index.chunk import estimate_tokens" in source


class TestAKeyThatKeepsFailing:
    """Batch M review P2. A billed request that fails counts a failure on
    its key in the cursor row. After ``PARK_AFTER`` (3) in a row the key
    asks nothing for ``PARK_DAYS`` (7), or until its schema hash changes."""

    async def test_three_failed_requests_park_the_key(self, monkeypatch):
        from datetime import UTC, datetime, timedelta

        assert (mp.PARK_AFTER, mp.PARK_DAYS) == (3, 7)
        _modes(monkeypatch)
        fake = _fake(monkeypatch, raises=acb_llm.DecideUnavailable("server_error", status=503))
        cache = mp.Cache()
        box = Box(monkeypatch, cache=cache)
        start = datetime(2026, 10, 17, 9, tzinfo=UTC)
        for n in range(1, 4):
            got = await mp.map_after_run(ORG, now=start + timedelta(hours=n))
            assert got.reason == "no_answer" and len(fake.calls) == n
        (key,) = cache.failures
        assert key == mp.SLOTS_KEY and cache.failures[key]["count"] == 3
        assert box.cursors[-1].state["failures"][key]["count"] == 3

        # Parked: no request goes out, and nothing is billed.
        got = await mp.map_after_run(ORG, now=start + timedelta(days=2))
        assert len(fake.calls) == 3 and got.requests == 0

        # A changed schema hash asks again.
        cache.failures[key]["schema"] = "an-older-hash"
        await mp.map_after_run(ORG, now=start + timedelta(days=2))
        assert len(fake.calls) == 4
        assert cache.failures[key]["count"] == 1, "a new hash starts the count again"

    async def test_the_park_ends_after_seven_days(self, monkeypatch):
        from datetime import UTC, datetime, timedelta

        _modes(monkeypatch)
        fake = _fake(monkeypatch, raises=acb_llm.DecideUnavailable("server_error", status=503))
        cache = mp.Cache()
        Box(monkeypatch, cache=cache)
        start = datetime(2026, 10, 17, 9, tzinfo=UTC)
        for _n in range(3):
            await mp.map_after_run(ORG, now=start)
        await mp.map_after_run(ORG, now=start + timedelta(days=6, hours=23))
        assert len(fake.calls) == 3
        await mp.map_after_run(ORG, now=start + timedelta(days=7, minutes=1))
        assert len(fake.calls) == 4

    async def test_a_key_that_finishes_clears_its_failures(self, monkeypatch):
        _modes(monkeypatch)
        _fake(monkeypatch)
        cache = mp.Cache(failures={mp.SLOTS_KEY: {"count": 2, "schema": "x", "at": "2026-10-17"}})
        Box(monkeypatch, cache=cache)
        got = await mp.map_after_run(ORG)
        assert got.reason == "done" and mp.SLOTS_KEY not in cache.failures


# ── Item 7: each mode of each feature ───────────────────────────────────────


class TestEachMode:
    async def test_role_on_writes_a_role_from_a_sure_answer(self):
        result, asker = await _pipeline("dewin", ON)
        roles = [i for i in result.items if i["kind"] == "role"]
        assert roles == [{"kind": "role", "slot": "company", "module": "Operator_Associates",
                          "profile_version": 1}]
        assert {"role.company", "role.deal"} <= set(_asked(asker))

    async def test_role_shadow_is_a_smoke_check_with_no_ask(self):
        result, asker = await _pipeline("dewin", {**OFF, mp.FEATURE_ROLE: "shadow"})
        assert asker.calls == [] and result.items == []
        assert (result.empty_slots, result.sure_candidates) == (2, 0)
        assert mp.SLOTS_KEY in result.done, "shadow writes the hash too"

    async def test_role_shadow_counts_a_sure_candidate_of_code(self):
        result, asker = await _pipeline("opportunities", {**OFF, mp.FEATURE_ROLE: "shadow"})
        assert asker.calls == [] and result.sure_candidates == 1

    async def test_role_off_asks_and_writes_nothing(self):
        result, asker = await _pipeline("dewin", OFF)
        assert asker.calls == [] and result.items == [] and result.done == {}

    async def test_target_on_writes_targets_as_the_table_shows(self):
        result, _asker = await _pipeline("commercials", ON)
        targets = {i["target"]: i for i in result.items if i["kind"] == "target"}
        assert targets["probability"] == {
            "kind": "target", "slot": "deal", "module": "Commercials", "target": "probability",
            "fields": ["Probability"], "transform": "cast", "unmapped": 0}
        assert targets["name"]["transform"] == "copy"
        assert set(targets) == {"name", "expected_close_date", "probability", "lost_note"}

    async def test_target_shadow_logs_agreement_and_writes_nothing(self):
        result, asker = await _pipeline("dewin", {**OFF, mp.FEATURE_TARGET: "shadow"},
                                        answers=_answers("dewin", "shadow"))
        assert result.items == []
        assert result.shadow == {mp.FEATURE_TARGET: [6, 5]}
        assert all(q.startswith("t.") for q in _asked(asker))

    async def test_target_off_asks_no_target(self):
        result, asker = await _pipeline("dewin", {**ON, mp.FEATURE_TARGET: "off"})
        assert not [q for q in _asked(asker) if q.startswith("t.")]
        assert [i["kind"] for i in result.items] == ["role"]

    async def test_stage_on_writes_a_stage_as_the_table_shows(self):
        result, _asker = await _pipeline("commercials", ON)
        (stage,) = [i for i in result.items if i["kind"] == "stage"]
        assert stage == {"kind": "stage", "slot": "deal", "module": "Commercials",
                         "field_api_name": "Status", "unsorted": 0,
                         "values": {"Lost": "lost", "On Hold": "open", "Open": "open",
                                    "Won": "won"}}

    async def test_stage_shadow_asks_about_the_marked_deal_stages(self):
        schema = _schema("dewin")
        slots = tuple(mp.SlotShape(slot=s.slot, empty=False, statuses={"Closed Won": "won",
                                                                      "Closed Lost": "lost"})
                      if s.slot == "deal" else s for s in schema.inputs.slots)
        inputs = mp.Inputs(1, schema.inputs.modules, slots)
        asker = eval_run.ScriptedAsker({"s:Closed Won": {"pick": "won", "p": 0.9},
                                        "s:Closed Lost": {"pick": "open", "p": 0.9}})
        pipeline = mp.Pipeline(inputs, {**OFF, mp.FEATURE_STAGE: "shadow"}, mp.Cache(),
                               eval_run.EvalSource(schema), asker)
        result = await pipeline.run()
        assert result.items == []
        assert result.shadow == {mp.FEATURE_STAGE: [2, 1]}

    async def test_stage_off_asks_no_stage(self):
        result, asker = await _pipeline("commercials", {**ON, mp.FEATURE_STAGE: "off"})
        assert not [q for q in _asked(asker) if q.startswith("s.")]
        assert not [i for i in result.items if i["kind"] == "stage"]


class TestTheApprovedPlan:
    async def test_an_approved_role_asks_only_its_open_targets(self):
        """An approved role is no open slot, an approved target is no open
        target, and an approved stage asks nothing (a new value is CRM-Q22)."""
        schema = _schema("commercials")
        approved = (
            {"kind": "role", "slot": "deal", "module": "Commercials", "profile_version": 1},
            {"kind": "target", "slot": "deal", "module": "Commercials", "target": "name",
             "fields": ["Commercial_Name"], "transform": "copy"},
            {"kind": "stage", "slot": "deal", "module": "Commercials",
             "field_api_name": "Status", "values": {"Won": "won"}},
        )
        inputs = mp.Inputs(1, schema.inputs.modules, schema.inputs.slots, approved)
        asker = eval_run.ScriptedAsker(_answers("commercials"))
        result = await mp.Pipeline(inputs, ON, mp.Cache(), eval_run.EvalSource(schema),
                                   asker).run()
        asked = _asked(asker)
        assert not [q for q in asked if q.startswith(("role.", "s."))]
        assert "t.name" not in asked
        kinds = sorted(i["kind"] for i in result.items)
        assert kinds == ["target", "target", "target"]
        assert {i["target"] for i in result.items} == {
            "expected_close_date", "probability", "lost_note"}

    async def test_one_module_named_for_two_slots_plays_neither(self):
        answers = dict(_answers())
        answers["role.deal"] = {"pick": "Operator Associates", "p": 0.9}
        result, _asker = await _pipeline(answers=answers)
        assert [i for i in result.items if i["kind"] == "role"] == []
        assert result.unsure == 2


# ── Item 8: the rules of the kinds ──────────────────────────────────────────


ROLE = {"kind": "role", "slot": "deal", "module": "Commercials", "profile_version": 3}
TARGET = {"kind": "target", "slot": "deal", "module": "Commercials", "target": "next_step",
          "fields": ["Next"], "transform": "copy"}
STAGE = {"kind": "stage", "slot": "deal", "module": "Commercials", "field_api_name": "Status",
         "values": {"Won": "won", "Lost": "lost", "Open": "open"}}


def _code(items: list[dict[str, Any]]) -> str | None:
    try:
        mapping_plans.check_kinds(items)
    except HTTPException as exc:
        assert exc.status_code == 422
        return exc.detail["code"]
    return None


class TestTheKinds:
    def test_a_role_with_its_target_and_stage_is_legal(self):
        assert _code([ROLE, TARGET, STAGE]) is None
        assert _code([{"kind": "field", "entity": "lead"}, ROLE]) is None

    def test_rule_1_a_target_or_a_stage_needs_its_role(self):
        assert _code([TARGET]) == "role_missing"
        assert _code([STAGE]) == "role_missing"
        other = {**ROLE, "module": "Quotes"}
        assert _code([other, TARGET]) == "role_missing", "a role of another module"
        assert _code([{**ROLE, "slot": "lead"}, TARGET]) == "role_missing"

    def test_rule_2_the_target_and_the_transform(self):
        assert _code([ROLE, {**TARGET, "target": "amount"}]) == "bad_target"
        assert _code([ROLE, {**TARGET, "target": "name"}]) is None
        assert _code([ROLE, {**TARGET, "transform": "guess"}]) == "bad_target"
        assert _code([ROLE, {**TARGET, "transform": "value_map"}]) == "bad_target"
        assert _code([ROLE, {**TARGET, "fields": []}]) == "bad_target"
        assert _code([ROLE, {**TARGET, "transform": "coalesce", "fields": ["A"]}]) == "bad_target"
        assert _code([ROLE, {**TARGET, "transform": "coalesce", "fields": ["A", "B"]}]) is None
        assert _code([ROLE, {**TARGET, "target": "probability", "transform": "copy"}]) == (
            "bad_target")

    def test_rule_3_each_stage_value_is_won_lost_or_open(self):
        assert _code([ROLE, {**STAGE, "values": {"Won": "victory"}}]) == "bad_stage"
        assert _code([ROLE, {**STAGE, "field_api_name": ""}]) == "bad_stage"

    def test_a_bad_role_and_a_duplicate(self):
        assert _code([{**ROLE, "slot": "invoice"}]) == "bad_role"
        assert _code([ROLE, TARGET, dict(TARGET)]) == "duplicate"

    def test_merge_replaces_by_identity_and_drops_the_items_of_a_moved_role(self):
        merged = mapping_plans.merge_kinds(
            [{"kind": "field", "entity": "lead"}, ROLE, TARGET, STAGE],
            [{**ROLE, "module": "Quotes"}])
        assert [i["kind"] for i in merged] == ["field", "role"]
        assert merged[1]["module"] == "Quotes"
        kept = mapping_plans.merge_kinds([ROLE, TARGET], [{**TARGET, "fields": ["Other"]}])
        assert [i.get("fields") for i in kept] == [None, ["Other"]]

    def test_commit_checks_the_kinds_before_it_locks(self):
        import ast
        import inspect

        tree = ast.parse(inspect.getsource(mapping_plans.commit))
        calls = [ast.unparse(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)]
        assert calls.index("check_kinds") < calls.index("lock_rules")
        tree = ast.parse(inspect.getsource(mapping_plans.write_draft))
        calls = [ast.unparse(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)]
        assert "check_kinds" in calls


# ── Item 9: the stage values with no entry ──────────────────────────────────


class TestTheUnsortedCount:
    async def test_a_value_that_stays_unsure_is_counted(self):
        answers = dict(_answers("opportunities"))
        answers["s:Qualification"] = {"pick": "open", "p": 0.6, "p2": 0.6}
        result, _asker = await _pipeline("opportunities", ON, answers=answers)
        (stage,) = [i for i in result.items if i["kind"] == "stage"]
        assert "Qualification" not in stage["values"]
        assert stage["unsorted"] == 1

    async def test_a_value_past_the_limit_is_counted(self, monkeypatch):
        monkeypatch.setattr(mp, "STAGE_VALUE_LIMIT", 1)
        result, _asker = await _pipeline("opportunities", ON)
        (stage,) = [i for i in result.items if i["kind"] == "stage"]
        assert stage["unsorted"] == 2


# ── Item 10: the schema hash ────────────────────────────────────────────────


class TestTheSchemaHash:
    async def test_a_second_run_on_the_same_schema_makes_no_ask_call(self):
        first, asker = await _pipeline()
        assert asker.calls
        again, asker = await _pipeline(cache=_cache(first))
        assert asker.calls == []
        assert sorted(map(json.dumps, again.items)) == sorted(map(json.dumps, first.items))

    async def test_a_shadow_run_writes_the_hash_and_a_second_asks_nothing(self):
        first, asker = await _pipeline(modes=SHADOW, answers=_answers("dewin", "shadow"))
        assert asker.calls and set(first.done) == {"slots", "core:contact", "core:lead"}
        _again, asker = await _pipeline(modes=SHADOW, cache=_cache(first),
                                        answers=_answers("dewin", "shadow"))
        assert asker.calls == []

    async def test_a_change_of_mode_asks_again(self):
        first, _asker = await _pipeline(modes=SHADOW, answers=_answers("dewin", "shadow"))
        _again, asker = await _pipeline(modes=ON, cache=_cache(first))
        assert asker.calls, "a shadow hash never silences on"

    async def test_a_changed_field_asks_that_module_again_and_no_other(self):
        first, _asker = await _pipeline()
        cache = _cache(first)
        cache.schema["module:Operator_Associates"] = "an-old-hash"
        _again, asker = await _pipeline(cache=cache)
        asked = _asked(asker)
        assert asked and all(q.startswith("t.") for q in asked), "no role question"

    async def test_the_run_writes_the_cursor_row(self, monkeypatch):
        _modes(monkeypatch)
        _fake(monkeypatch)
        box = Box(monkeypatch)
        await mp.map_after_run(ORG)
        (row,) = box.cursors
        assert row.module == mp.CURSOR_ROW == "zoho:map_pipeline"
        assert set(row.state["schema"]) == {"slots", "module:Operator_Associates"}
        assert row.state["items"]["slots"][0]["kind"] == "role"


# ── The six steps ───────────────────────────────────────────────────────────


class TestTheSteps:
    def test_1_the_pattern_of_a_value(self):
        cases = {
            "ann@example.com": "email", "+91 98765 43210": "phone", "9876543210": "phone",
            "https://www.example.com": "url", "2026-10-01": "date",
            "2026-10-01T10:00:00+05:30": "datetime", "42": "number", "Ravi Iyer": "name",
            "pump leak": "text", 7: "number", True: "boolean",
        }
        for value, kind in cases.items():
            assert mp.pattern_of(value) == kind, value
        assert mp.pattern_of({"id": "1", "name": "X"}) == "lookup"
        assert mp.pattern_of({"id": "1", "email": "a@b.co"}) == "owner"
        for blank in (None, "", " ", [], {}, False):
            assert mp.pattern_of(blank) is None

    def test_1_a_person_value_goes_as_its_class_only(self):
        assert mp.masked("ann@example.com") == "<email>"
        assert mp.masked("+91 98765 43210") == "<phone>"
        assert mp.masked("Ravi Iyer") == "<name>"
        assert mp.masked({"id": "9", "name": "Ravi Iyer"}) == "<name>"
        assert mp.masked("DOC-1001") == "DOC-1001"
        assert len(mp.masked("x" * 500)) == mp.SAMPLE_CLIP

    def test_1_the_shape_of_a_sample(self):
        values = ["a@x.co", "b@x.co", None, "c@x.co", "nope"]
        assert mp.shape_values(values) == (0.8, "email", 0.75)
        assert mp.shape_values([]) == (None, None, None)

    def test_3_a_module_named_like_the_slot_plays_it(self):
        def m(label: str, records: int = 5) -> mp.ModuleShape:
            return mp.ModuleShape(label.replace(" ", "_"), label, None, records, None)

        assert mp.code_roles(["company"], [m("Customers"), m("Invoices")]) == {
            "company": "Customers"}
        assert mp.code_roles(["deal"], [m("Opportunities")]) == {"deal": "Opportunities"}
        assert mp.code_roles(["company"], [m("Customer Visits")]) == {}
        assert mp.code_roles(["deal"], [m("Deals", records=0)]) == {}, "no record, no role"
        assert mp.code_roles(["company"], [m("Clients"), m("Customers")]) == {}, "two: unsure"

    def test_3_code_answers_the_targets_it_can(self):
        def f(key: str, label: str, type_: str, pattern: str | None = None,
              share: float = 1.0, fill: float = 0.9) -> mp.FieldShape:
            return mp.FieldShape(key, label, type_, fill=fill, pattern=pattern,
                                 pattern_share=share)

        fields = (f("Nm", "Name", "text", "text"), f("Mail", "Mail", "email", "email", 0.95),
                  f("Mail2", "Other", "text", "email", 0.5), f("Empty", "Industry", "text",
                                                               "text", fill=0.0))
        module = mp.ModuleShape("M", "M", None, 9, "Nm", fields)
        cands = [x for x in fields if x.offered()]
        assert mp.code_target("email", cands, module).key == "Mail"
        assert mp.code_target("name", cands, module).key == "Nm"
        assert mp.code_target("industry", cands, module) is None, "0% fill is never offered"
        two = (*fields, f("Mail3", "Third", "email", "email", 0.99))
        assert mp.code_target("email", list(two), module) is None, "two: a question"

    async def test_4_and_5_the_requests_of_the_dewin_shape(self):
        """Step 4: one request for each entity, fields named by path, no
        value. Step 5: the unsure question goes again, narrowed, with the
        samples of its own candidates only."""
        result, asker = await _pipeline()
        first_target = next(reqs for f, reqs in asker.calls if f == mp.FEATURE_TARGET)
        state, questions = first_target[0]
        assert set(questions) == {"t.industry", "t.description", "t.no_of_employees",
                                  "t.annual_revenue"}
        for q in questions.values():
            assert all(re.fullmatch(r"f\d+|none", k) for k in q.criteria)
            assert all(v.startswith("fields.") for k, v in q.criteria.items() if k != "none")
        assert all("samples" not in f for f in state["fields"].values()), "round 1"
        assert len(state["fields"]) <= mp.MAX_CANDIDATES + 2
        # Step 5: the unsure description goes again, narrowed and with samples.
        (reask,) = [reqs for f, reqs in asker.calls if f == mp.FEATURE_TARGET][1]
        state, questions = reask
        assert list(questions) == ["t.description"]
        assert len(questions["t.description"].criteria) <= mp.REASK_OPTIONS + 1
        assert set(state["fields"]) == {k for k in questions["t.description"].criteria
                                        if k != "none"}
        assert all("samples" in f for f in state["fields"].values())
        assert result.unsure == 0

    async def test_5_an_answer_that_stays_unsure_makes_no_suggestion(self):
        answers = dict(_answers())
        answers["t.description"] = {"pick": "Remarks", "p": 0.6, "p2": 0.7}
        answers["role.company"] = {"pick": "Operator Associates", "p": 0.5, "p2": 0.55}
        result, asker = await _pipeline(answers=answers)
        assert result.items == [], "no sure role, so nothing"
        assert result.unsure == 1
        assert [q for q in _asked(asker) if q == "role.company"] == ["role.company"] * 2

    def test_5_a_close_second_is_unsure(self):
        answer = acb_llm.ChoiceAnswer(choice="f1", probabilities=MappingProxyType(
            {"f1": 0.8, "f2": 0.7}), confidence=0.8)
        assert mp.sure_choice(answer, ["f1", "f2", "none"]) == ("f1", False)
        answer = acb_llm.ChoiceAnswer(choice="f1", probabilities=MappingProxyType(
            {"f1": 0.8, "f2": 0.1}), confidence=0.8)
        assert mp.sure_choice(answer, ["f1", "f2", "none"]) == ("f1", True)
        answer = acb_llm.ChoiceAnswer(choice="vendor", probabilities=MappingProxyType({}),
                                      confidence=0.99)
        assert mp.sure_choice(answer, ["f1", "none"]) == (None, False)

    async def test_6_a_target_counts_the_values_that_did_not_map(self):
        schema = _schema("dewin")
        rows = [dict(r) for r in schema.records["Operator_Associates"]]
        for r in rows[:3]:
            r["Email"] = "not an email"
        module = schema.inputs.module("Operator_Associates")
        detail = mp.detail_module(module, rows)
        assert detail.field("Email").pattern == "email"
        work = mp._ModuleWork(mp.Pipeline(schema.inputs, ON, mp.Cache(), None, None),
                              "company", detail)
        work.picks["email"] = detail.field("Email")
        (item,) = work.items()
        assert item["transform"] == "normalize" and item["unmapped"] == 3


# ── The log lines ───────────────────────────────────────────────────────────


class TestTheLogs:
    async def test_the_lines_hold_counts_and_no_name_or_value(self, monkeypatch):
        _modes(monkeypatch)
        _fake(monkeypatch)
        Box(monkeypatch)
        with structlog.testing.capture_logs() as caps:
            got = await mp.map_after_run(ORG)
        (line,) = _events(caps, "crm.map_pipeline.step")
        assert line["reason"] == "done" and line["requests"] == got.requests
        assert line["roles"] == 1 and line["targets"] == 9
        text = json.dumps([c for c in caps if str(c.get("event", "")).startswith(
            ("crm.map_pipeline", "decide."))], default=str)
        for secret in ("Operator", "Remarks", "Industry", "example.com", "Pumps", "Email"):
            assert secret not in text, secret


# ── The eval ────────────────────────────────────────────────────────────────


class TestTheEval:
    async def test_the_scripted_run_passes_each_bar(self):
        report = await eval_run.run_all()
        assert report["passed"], json.dumps(report, indent=1)
        dewin = report["schemas"][0]
        assert dewin["schema"] == "dewin"
        assert dewin["requests"] <= 7 and dewin["tokens_estimated"] < 5000

    async def test_a_wrong_answer_fails_a_bar(self):
        answers = json.loads(json.dumps(load_answers()))
        answers["dewin"]["on"]["t.industry"] = {"pick": "City", "p": 0.9}
        report = await eval_run.run_all(answers)
        assert not report["passed"]
        assert report["schemas"][0]["bars"]["targets"] is False

    def test_the_runner_exits_2_without_scripted(self):
        assert eval_run.main([]) == eval_run.EXIT_NO_GO
