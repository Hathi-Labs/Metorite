"""DEC-2a: the three yes/no steps of Tasks AI on the ``projects.`` decide seam.

Per step: ``off`` makes no call, ``shadow`` keeps the old answer and logs
agreement, ``on`` uses decide, and ``on`` falls back to the old call on a 402
or a timeout. Offline: ``acb_llm.decide`` is a fake.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from types import MappingProxyType
from typing import Any

import acb_llm
import pytest
from acb_common.db import bind_tenant, release_tenant
from acb_common.settings import get_settings
from acb_llm import decide_policy
from gateway import decide_features as df
from gateway.routes.tasks import projects_decide as pd

ORG = "dec2a1a1-0000-4000-8000-000000000001"


def _clear() -> None:
    get_settings.cache_clear()
    df._parse_modes.cache_clear()
    df._parse_orgs.cache_clear()


def _reset_cooldowns() -> None:
    df._cooldown_until.clear()
    for table in decide_policy.ISOLATED_UNTIL.values():
        table.clear()


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for name in ("DECIDE_FEATURE_MODES", "DECIDE_FEATURE_ORGS", "MY_DAY_DECIDE_ORGS",
                 "CRM_DECIDE_ORGS", "PROJECTS_DECIDE_ORGS"):
        monkeypatch.setenv(name, "")
    _clear()
    _reset_cooldowns()
    token = bind_tenant(ORG)
    yield
    release_tenant(token)
    monkeypatch.undo()
    _clear()
    _reset_cooldowns()


def _mode(monkeypatch, feature: str, mode: str) -> None:
    monkeypatch.setenv("DECIDE_FEATURE_MODES", f"{feature}={mode}")
    monkeypatch.setenv("PROJECTS_DECIDE_ORGS", ORG)
    _clear()


class Log:
    def __init__(self) -> None:
        self.lines: list[tuple[str, dict[str, Any]]] = []

    def __getattr__(self, level: str):
        if level in ("info", "warning", "error", "debug"):
            return lambda event, **kw: self.lines.append((event, kw))
        raise AttributeError(level)

    def fields(self, event: str) -> list[dict[str, Any]]:
        return [k for e, k in self.lines if e == event]


class Fake:
    """``decide``: the probability of each question comes from ``p(qid)``."""

    def __init__(self, p: Callable[[str], float] = lambda _q: 0.9,
                 raises: BaseException | None = None, delay: float = 0.0) -> None:
        self.p, self.raises, self.delay, self.calls = p, raises, delay, []

    async def __call__(self, state, questions, **kw):
        self.calls.append({"state": state, "questions": dict(questions), **kw})
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.raises is not None:
            raise self.raises
        return acb_llm.Decision(
            answers=MappingProxyType({q: acb_llm.BooleanAnswer(probability=self.p(q))
                                      for q in questions}),
            request_id="r1")


def _fake(monkeypatch, **kw) -> Fake:
    fake = Fake(**kw)
    monkeypatch.setattr(acb_llm, "decide", fake)
    return fake


def _log(monkeypatch) -> Log:
    log = Log()
    monkeypatch.setattr(df, "_log", log)
    return log


def _run(coro):
    async def _go():
        out = await coro
        # A detached shadow ends in the background. Wait for it.
        while df._BACKGROUND:
            await asyncio.gather(*list(df._BACKGROUND))
        return out

    return asyncio.run(_go())


class Old:
    """A counting stand-in for an old LLM call."""

    def __init__(self, result: Any) -> None:
        self.result, self.calls = result, 0

    async def __call__(self):
        self.calls += 1
        return self.result


FAILURES = [acb_llm.DecideUnavailable("insufficient_credits", status=402),
            TimeoutError()]


# ── The features are registered, off, and on-legal ──────────────────────────


@pytest.mark.parametrize("feature", [pd.VAGUE_TITLE, pd.ATOMIZE_DEDUP, pd.COMMITMENT])
def test_a_step_is_registered_off_by_default_and_bills_projects(monkeypatch, feature):
    assert feature in df.REGISTERED and feature in df.ON_FEATURES
    assert df.mode_for(feature) == "off"
    assert df.module_of(feature) == "projects"
    _mode(monkeypatch, feature, "on")
    assert df.mode_for(feature) == "on"


# ── Step 3: the vague-title gate ─────────────────────────────────────────────

_VAGUE = {"is_vague": True, "suggested_title": "Call Priya about the quote"}
_CLEAR = {"is_vague": False, "suggested_title": None}


def test_vague_off_makes_no_decide_call(monkeypatch):
    fake = _fake(monkeypatch)
    old = Old(_VAGUE)
    assert _run(pd.vague_title("Call", None, old, auto=True)) == _VAGUE
    assert fake.calls == [] and old.calls == 1


def test_vague_shadow_keeps_the_old_answer_and_logs_agreement(monkeypatch):
    fake = _fake(monkeypatch, p=lambda _q: 0.9)
    log = _log(monkeypatch)
    _mode(monkeypatch, pd.VAGUE_TITLE, "shadow")
    assert _run(pd.vague_title("Call", "n", Old(_VAGUE))) == _VAGUE
    assert len(fake.calls) == 1 and fake.calls[0]["module_slug"] == "projects"
    line = log.fields("decide.shadow")[0]
    assert line["decide_feature"] == pd.VAGUE_TITLE
    assert line["old"] is True and line["new"] is True and line["agree"] is True
    assert "latency_ms" in line and "old_latency_ms" in line
    # A disagreement logs agree=False and still returns the old answer.
    _fake(monkeypatch, p=lambda _q: 0.1)
    assert _run(pd.vague_title("Call", None, Old(_VAGUE))) == _VAGUE
    assert log.fields("decide.shadow")[1]["agree"] is False


def test_vague_shadow_does_not_wait_for_decide(monkeypatch):
    _fake(monkeypatch, delay=0.3)
    _mode(monkeypatch, pd.VAGUE_TITLE, "shadow")

    async def _go():
        started = time.perf_counter()
        out = await pd.vague_title("Call", None, Old(_VAGUE))
        took = time.perf_counter() - started
        await asyncio.gather(*list(df._BACKGROUND))
        return out, took

    out, took = asyncio.run(_go())
    assert out == _VAGUE and took < 0.2


def test_vague_on_uses_decide_and_skips_the_rewrite_when_clear(monkeypatch):
    fake = _fake(monkeypatch, p=lambda _q: 0.1)
    _mode(monkeypatch, pd.VAGUE_TITLE, "on")
    old = Old(_VAGUE)
    assert _run(pd.vague_title("Send Priya the Q3 quote", None, old, auto=True)) == _CLEAR
    assert old.calls == 0 and len(fake.calls) == 1


def test_vague_on_an_explicit_request_always_runs_the_rewrite(monkeypatch):
    # "Improve title" is a person's request: no gate, no decide call.
    fake = _fake(monkeypatch, p=lambda _q: 0.1)
    _mode(monkeypatch, pd.VAGUE_TITLE, "on")
    suggestion = {"is_vague": False, "suggested_title": "Send Priya the Q3 quote today"}
    old = Old(suggestion)
    assert _run(pd.vague_title("Send Priya the Q3 quote", None, old, auto=False)) == suggestion
    assert old.calls == 1 and fake.calls == []


def test_vague_on_an_old_client_with_no_auto_field_gets_the_rewrite(monkeypatch):
    _fake(monkeypatch, p=lambda _q: 0.1)
    _mode(monkeypatch, pd.VAGUE_TITLE, "on")
    old = Old(_VAGUE)
    assert _run(pd.vague_title("Send Priya the Q3 quote", None, old)) == _VAGUE
    assert old.calls == 1


def test_the_suggest_title_route_defaults_auto_to_false():
    import inspect

    from gateway.routes.tasks import ai
    assert inspect.signature(ai.suggest_title).parameters["auto"].default is False


def test_vague_on_runs_the_rewrite_when_vague(monkeypatch):
    _fake(monkeypatch, p=lambda _q: 0.9)
    _mode(monkeypatch, pd.VAGUE_TITLE, "on")
    old = Old({"is_vague": False, "suggested_title": "Call Priya"})
    out = _run(pd.vague_title("Call", None, old, auto=True))
    assert old.calls == 1 and out["is_vague"] is True
    assert out["suggested_title"] == "Call Priya"


@pytest.mark.parametrize("failure", FAILURES)
def test_vague_on_falls_back_to_the_old_call(monkeypatch, failure):
    _fake(monkeypatch, raises=failure)
    _mode(monkeypatch, pd.VAGUE_TITLE, "on")
    old = Old(_VAGUE)
    assert _run(pd.vague_title("Call", None, old, auto=True)) == _VAGUE
    assert old.calls == 1


def test_vague_on_falls_back_while_cooling(monkeypatch):
    fake = _fake(monkeypatch, raises=acb_llm.DecideUnavailable("x", status=402))
    _mode(monkeypatch, pd.VAGUE_TITLE, "on")
    _run(pd.vague_title("Call", None, Old(_VAGUE), auto=True))
    assert df.cooling(pd.VAGUE_TITLE)
    fake.calls.clear()
    old = Old(_VAGUE)
    assert _run(pd.vague_title("Call", None, old, auto=True)) == _VAGUE
    assert fake.calls == [] and old.calls == 1


# ── Step 5: the commitment gate ──────────────────────────────────────────────

_DRAFT = {"is_commitment": True, "title": "Send Priya the quote"}
_KW = {"subject": "Quote", "owner_name": "Vijay", "owner_addrs": {"v@x.com"},
       "body": "I will send the quote by Friday.", "thread": ""}


def test_commitment_off_makes_no_decide_call(monkeypatch):
    fake = _fake(monkeypatch)
    old = Old(_DRAFT)
    assert _run(pd.commitment(old=old, **_KW)) == _DRAFT
    assert fake.calls == [] and old.calls == 1


def test_commitment_shadow_keeps_the_old_answer_and_logs_agreement(monkeypatch):
    fake = _fake(monkeypatch, p=lambda _q: 0.9)
    log = _log(monkeypatch)
    _mode(monkeypatch, pd.COMMITMENT, "shadow")
    assert _run(pd.commitment(old=Old(_DRAFT), **_KW)) == _DRAFT
    assert len(fake.calls) == 1
    line = log.fields("decide.shadow")[0]
    assert line["decide_feature"] == pd.COMMITMENT
    assert line["old"] is True and line["agree"] is True and "old_latency_ms" in line
    # The old call failed (None): nothing to compare, so no agreement line.
    assert _run(pd.commitment(old=Old(None), **_KW)) is None
    assert len(log.fields("decide.shadow")) == 1
    assert log.fields("decide.shadow_failed")[0]["error_type"] == "OldCallFailed"


def test_commitment_on_a_no_ends_the_call_with_no_llm(monkeypatch):
    _fake(monkeypatch, p=lambda _q: 0.1)
    _mode(monkeypatch, pd.COMMITMENT, "on")
    old = Old(_DRAFT)
    assert _run(pd.commitment(old=old, **_KW)) == {"is_commitment": False}
    assert old.calls == 0


def test_commitment_on_a_yes_runs_the_draft_call(monkeypatch):
    _fake(monkeypatch, p=lambda _q: 0.9)
    _mode(monkeypatch, pd.COMMITMENT, "on")
    old = Old(_DRAFT)
    assert _run(pd.commitment(old=old, **_KW)) == _DRAFT
    assert old.calls == 1


@pytest.mark.parametrize("failure", FAILURES)
def test_commitment_on_falls_back_to_the_old_call(monkeypatch, failure):
    _fake(monkeypatch, raises=failure)
    _mode(monkeypatch, pd.COMMITMENT, "on")
    old = Old(_DRAFT)
    assert _run(pd.commitment(old=old, **_KW)) == _DRAFT
    assert old.calls == 1


# ── Step 4: the duplicate pairs of Atomize ───────────────────────────────────

_EXISTING = [{"id": "a", "title": "Call the lab about calibration"},
             {"id": "b", "title": "Pay the electricity bill"}]


def _cands() -> list[dict[str, Any]]:
    return [{"title": "Call lab about calibration", "duplicate_of": 0, "same": "yes"},
            {"title": "Pay electricity bill", "duplicate_of": None, "same": "no"}]


def test_atomize_off_makes_no_decide_call(monkeypatch):
    fake = _fake(monkeypatch)
    cands = _cands()
    assert _run(pd.atomize_dedup(cands, _EXISTING, 900)) is cands
    assert fake.calls == []


def test_atomize_shadow_keeps_the_old_answer_and_logs_agreement(monkeypatch):
    fake = _fake(monkeypatch, p=lambda q: 0.9 if q == "c0e0" else 0.1)
    log = _log(monkeypatch)
    _mode(monkeypatch, pd.ATOMIZE_DEDUP, "shadow")
    assert _run(pd.atomize_dedup(_cands(), _EXISTING, 900)) == _cands()
    assert len(fake.calls) == 1
    line = log.fields("decide.shadow")[0]
    assert line["decide_feature"] == pd.ATOMIZE_DEDUP
    assert line["old"] == ["c0e0"] and line["new"] == ["c0e0"] and line["agree"] is True
    assert line["old_latency_ms"] == 900 and line["pairs"] == line["agree_pairs"]
    # The log holds keys, not titles.
    assert "calibration" not in str(line)


def test_atomize_on_sets_the_duplicate_from_decide(monkeypatch):
    # Decide says the old pick is NOT the same, and the 2nd candidate IS.
    _fake(monkeypatch, p=lambda q: 0.9 if q.startswith("c1") else 0.1)
    _mode(monkeypatch, pd.ATOMIZE_DEDUP, "on")
    out = _run(pd.atomize_dedup(_cands(), _EXISTING, 900))
    assert out[0]["duplicate_of"] is None and out[0]["same"] == "no"
    assert out[1]["duplicate_of"] is not None and out[1]["same"] == "yes"


@pytest.mark.parametrize("failure", FAILURES)
def test_atomize_on_falls_back_to_the_old_judgement(monkeypatch, failure):
    _fake(monkeypatch, raises=failure)
    _mode(monkeypatch, pd.ATOMIZE_DEDUP, "on")
    assert _run(pd.atomize_dedup(_cands(), _EXISTING, 900)) == _cands()


def test_atomize_pairs_are_capped_at_three_requests():
    many = [{"title": f"task number {i} alpha", "duplicate_of": None, "same": "no"}
            for i in range(30)]
    existing = [{"id": str(i), "title": f"task number {i} alpha"} for i in range(30)]
    pairs = pd._pairs(many, existing)
    assert len(pairs) == pd.MAX_PAIRS == 48
    reqs = pd._dedup_requests(many, existing, pairs)
    assert [len(q) for _s, q in reqs] == [16, 16, 16]
