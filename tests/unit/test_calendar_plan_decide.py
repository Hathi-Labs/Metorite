"""Task 1338 — "Rebuild my day" ranks through the decision tier.

Hermetic. The facade ``acb_llm.decide`` is a FAKE, so no test reaches a
network. The old LLM call (`_llm_rank_day`) is a fake too.

Fenced here:
- the order follows the scores, a tie keeps the candidate order;
- the One Thing goes first, a not-today task leaves the plan;
- 40 candidates make 3 chunks of 16 questions at most, each with the full state;
- the note reaches the state;
- off / shadow / on, and the three failure modes of ``on``;
- no id outside the list can enter the plan.

Mutation: in ``order_from`` drop the One Thing sort, and
``test_the_one_thing_goes_first_even_with_a_poor_score`` fails.
"""
from __future__ import annotations

import asyncio
from types import MappingProxyType
from typing import Any

import acb_llm
import pytest
from acb_common._log import clear_run_context, run_context_scope
from acb_common.db import bind_tenant, release_tenant
from acb_common.settings import get_settings
from gateway import decide_features as df
from gateway.routes.tasks import calendar as cal
from gateway.routes.tasks import calendar_decide as cd

ORG = "1338a1a1-0000-4000-8000-000000000001"
ME = "member.one@plan.example"
NOTE = "Reschedule all calls after 8 am. Keep deep work first."


def _clear() -> None:
    get_settings.cache_clear()
    df._parse_modes.cache_clear()
    df._parse_orgs.cache_clear()


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for name in ("DECIDE_FEATURE_MODES", "DECIDE_FEATURE_ORGS", "MY_DAY_DECIDE_ORGS"):
        monkeypatch.setenv(name, "")
    _clear()
    df._cooldown_until.clear()
    token = bind_tenant(ORG)
    with run_context_scope():
        clear_run_context()
        yield
    release_tenant(token)
    monkeypatch.undo()
    _clear()
    df._cooldown_until.clear()


def _mode(monkeypatch, mode: str) -> None:
    monkeypatch.setenv("DECIDE_FEATURE_MODES", f"{cd.FEATURE}={mode}")
    monkeypatch.setenv("MY_DAY_DECIDE_ORGS", ORG)
    _clear()


def _cands(n: int, **over: Any) -> list[dict]:
    out = []
    for i in range(n):
        c = {"id": f"t{i}", "title": f"Task {i}", "estimate_mins": 30,
             "leveraged": False, "important": False, "deep_work": False,
             "due_in_days": None, "energy": None, "context": None}
        out.append(c)
    for c in out:
        c.update(over)
    return out


class FakeDecide:
    """Answers each question with ``scores[qid]``, else ``default``."""

    def __init__(self, scores: dict[str, float] | None = None, default: float = 1.0,
                 raises: BaseException | None = None, skip: set[str] | None = None,
                 delay: float = 0.0) -> None:
        self.calls: list[dict[str, Any]] = []
        self.scores = scores or {}
        self.default = default
        self.raises = raises
        self.skip = skip or set()
        self.delay = delay

    async def __call__(self, state, questions, **kwargs):
        self.calls.append({"state": state, "questions": dict(questions), **kwargs})
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.raises is not None:
            raise self.raises
        answers = {
            qid: acb_llm.ScoreAnswer(
                score=self.scores.get(qid, self.default),
                probabilities=MappingProxyType({}), confidence=0.5)
            for qid in questions if qid not in self.skip
        }
        return acb_llm.Decision(answers=MappingProxyType(answers), request_id="r1")


class Old:
    """The fake LLM call. Counts its calls."""

    def __init__(self, ids: list[str] | None = None) -> None:
        self.calls = 0
        self.ids = ids if ids is not None else ["old1", "old2"]

    async def __call__(self):
        self.calls += 1
        return [{"id": i, "preferred_energy": None, "rationale": "old"}
                for i in self.ids], "old notes", None


def _rank(cands, old, *, note=NOTE, one=None, **kw):
    return asyncio.run(cd.rank_day(
        old, cands, note, 360, one, None, ["Deep work 9-11"], member=ME,
        default_prompt="DEFAULT", rank_fallback=cal._rank_fallback, **kw))


def _fake(monkeypatch, **kw) -> FakeDecide:
    fake = FakeDecide(**kw)
    monkeypatch.setattr(acb_llm, "decide", fake)
    return fake


def _ids(result) -> list[str]:
    return [o["id"] for o in result[0]]


def test_the_order_follows_the_scores_and_a_tie_keeps_the_candidate_order(monkeypatch):
    _mode(monkeypatch, "on")
    _fake(monkeypatch, scores={"c0": 1.4, "c1": 0.2, "c2": 1.4, "c3": 0.9})
    old = Old()
    res = _rank(_cands(4), old)
    assert _ids(res) == ["t1", "t3", "t0", "t2"]
    assert old.calls == 0
    assert res[0][0]["rationale"] == "First today."
    assert res[2] is None and res[1] is None


def test_the_one_thing_goes_first_even_with_a_poor_score(monkeypatch):
    _mode(monkeypatch, "on")
    _fake(monkeypatch, scores={"c0": 0.1, "c1": 1.9, "c2": 0.5})
    res = _rank(_cands(3), Old(), one="t1")
    assert _ids(res)[0] == "t1"
    assert sorted(_ids(res)) == ["t0", "t1", "t2"]


def test_the_one_thing_stays_even_when_it_is_not_today(monkeypatch):
    _mode(monkeypatch, "on")
    _fake(monkeypatch, scores={"c0": 0.1, "c1": 2.9})
    assert _ids(_rank(_cands(2), Old(), one="t1")) == ["t1", "t0"]


def test_a_not_today_task_leaves_the_plan(monkeypatch):
    _mode(monkeypatch, "on")
    _fake(monkeypatch, scores={"c0": 0.1, "c1": 2.8, "c2": 2.4})
    assert _ids(_rank(_cands(3), Old())) == ["t0", "t2"]


def test_a_plan_of_only_not_today_tasks_is_not_empty(monkeypatch):
    _mode(monkeypatch, "on")
    _fake(monkeypatch, default=3.0)
    assert _ids(_rank(_cands(2), Old())) == ["t0", "t1"]


def test_40_candidates_make_3_chunks_with_the_full_state(monkeypatch):
    _mode(monkeypatch, "on")
    fake = _fake(monkeypatch)
    res = _rank(_cands(40), Old())
    assert len(fake.calls) == 3
    sizes = sorted(len(c["questions"]) for c in fake.calls)
    assert sizes == [8, 16, 16]
    assert all(s <= df.QUESTION_LIMIT for s in sizes)
    states = [c["state"] for c in fake.calls]
    assert states[0] == states[1] == states[2]
    assert len(states[0]["candidates"]) == 40
    assert len(_ids(res)) == 40


def test_the_tasks_past_the_limit_follow_in_priority_order(monkeypatch):
    _mode(monkeypatch, "on")
    fake = _fake(monkeypatch)
    cands = _cands(52)
    cands[50]["leveraged"] = True
    res = _rank(cands, Old())
    assert len(fake.calls) == 3
    assert _ids(res)[:48] == [f"t{i}" for i in range(48)]
    assert _ids(res)[48:] == ["t50", "t48", "t49", "t51"]


def test_the_note_reaches_the_state_and_the_prompt_is_the_default_when_unset(monkeypatch):
    _mode(monkeypatch, "on")
    fake = _fake(monkeypatch)
    _rank(_cands(2), Old(), one="t0")
    state = fake.calls[0]["state"]
    assert state["note_for_today"] == NOTE
    assert state["standing_planning_preferences"] == "DEFAULT"
    assert state["themed_windows"] == ["Deep work 9-11"]
    assert state["capacity_hours"] == 6.0
    assert state["one_thing_ref"] == "c0"
    q = fake.calls[0]["questions"]["c0"]
    assert tuple(q.criteria) == cd.LEVELS
    assert "overrides" in q.instructions


def test_the_member_is_the_session_address_and_proven(monkeypatch):
    _mode(monkeypatch, "on")
    fake = _fake(monkeypatch)
    _rank(_cands(2), Old())
    assert fake.calls[0]["member"] == ME
    assert fake.calls[0]["member_proven"] is True


def test_off_does_not_call_decide(monkeypatch):
    fake = _fake(monkeypatch)
    old = Old()
    res = _rank(_cands(3), old)
    assert old.calls == 1 and fake.calls == []
    assert _ids(res) == ["old1", "old2"]


def test_shadow_keeps_the_old_answer_and_runs_decide(monkeypatch):
    _mode(monkeypatch, "shadow")
    fake = _fake(monkeypatch)
    old = Old()
    res = _rank(_cands(3), old)
    assert old.calls == 1
    assert len(fake.calls) == 1
    assert _ids(res) == ["old1", "old2"]


def test_on_decide_answers_and_the_old_call_is_not_made(monkeypatch):
    _mode(monkeypatch, "on")
    fake = _fake(monkeypatch, scores={"c0": 2.0, "c1": 0.0})
    old = Old()
    res = _rank(_cands(2), old)
    assert old.calls == 0 and len(fake.calls) == 1
    assert _ids(res) == ["t1", "t0"]


@pytest.mark.parametrize("kind", ["unavailable", "timeout", "partial"])
def test_on_falls_back_to_the_old_call(monkeypatch, kind):
    _mode(monkeypatch, "on")
    if kind == "unavailable":
        _fake(monkeypatch, raises=acb_llm.DecideUnavailable("down"))
    elif kind == "timeout":
        monkeypatch.setattr(df, "ON_BOUND_S", 0.05)
        _fake(monkeypatch, delay=1.0)
    else:
        _fake(monkeypatch, skip={"c1"})
    old = Old()
    res = _rank(_cands(3), old)
    assert old.calls == 1
    assert _ids(res) == ["old1", "old2"]


def test_a_bad_score_falls_back(monkeypatch):
    _mode(monkeypatch, "on")
    _fake(monkeypatch, default=float("nan"))
    old = Old()
    _rank(_cands(2), old)
    assert old.calls == 1


def test_no_id_outside_the_list_enters_the_plan(monkeypatch):
    _mode(monkeypatch, "on")
    fake = _fake(monkeypatch)
    real = fake.__call__

    async def extra(state, questions, **kw):
        d = await real(state, questions, **kw)
        answers = dict(d.answers)
        answers["c99"] = acb_llm.ScoreAnswer(
            score=0.0, probabilities=MappingProxyType({}), confidence=None)
        answers["evil"] = answers["c99"]
        return acb_llm.Decision(answers=MappingProxyType(answers), request_id="r")

    monkeypatch.setattr(acb_llm, "decide", extra)
    assert sorted(_ids(_rank(_cands(3), Old()))) == ["t0", "t1", "t2"]


def test_energy_keeps_the_tag_and_deep_work_is_high(monkeypatch):
    _mode(monkeypatch, "on")
    _fake(monkeypatch, scores={"c0": 0.0, "c1": 0.5, "c2": 1.0})
    cands = _cands(3)
    cands[0]["deep_work"] = True
    cands[1]["energy"] = "low"
    res = _rank(cands, Old())
    assert [o["preferred_energy"] for o in res[0]] == ["high", "low", None]


def test_the_feature_is_registered_off_by_default_and_may_run_on(monkeypatch):
    assert cd.FEATURE in df.REGISTERED
    assert cd.FEATURE in df.ON_FEATURES
    assert cd.FEATURE not in df.MY_DAY_FEATURES
    assert df._parse_modes("")[cd.FEATURE] == "off"
    assert df.mode_for(cd.FEATURE) == "off"
    _mode(monkeypatch, "on")
    assert df.mode_for(cd.FEATURE) == "on"


def test_replan_core_goes_through_the_seam():
    import inspect

    src = inspect.getsource(cal._replan_core)
    assert "_calendar_decide.rank_day(" in src
    assert "member=uid" in src
