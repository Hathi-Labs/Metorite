"""DEC-2b: the four pick steps of Tasks AI on the ``projects.`` decide seam.

Per step: ``off`` makes no decide call, ``shadow`` keeps the old answer and logs
keys only, ``on`` uses decide, and ``on`` falls back to the old call on a 402,
a timeout, a pick outside the options and a pick below the floor. Offline:
``acb_llm.decide`` is a fake.
"""

from __future__ import annotations

import asyncio
import time
from types import MappingProxyType
from typing import Any

import acb_llm
import pytest
from acb_common.db import bind_tenant, release_tenant
from acb_common.settings import get_settings
from acb_llm import decide_policy
from gateway import decide_features as df
from gateway.routes.tasks import projects_decide as pd

ORG = "dec2b2b2-0000-4000-8000-000000000001"


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


def ch(choice: str, p: float = 0.9) -> acb_llm.ChoiceAnswer:
    return acb_llm.ChoiceAnswer(choice=choice, probabilities={choice: p}, confidence=p)


def sc(level: str, p: float = 0.9) -> acb_llm.ScoreAnswer:
    return acb_llm.ScoreAnswer(
        score=level, probabilities={level: p}, confidence=p, level=level)


class Fake:
    """``decide``: the answer of each question comes from ``answers[qid]``."""

    def __init__(self, answers: dict[str, Any] | None = None,
                 raises: BaseException | None = None, delay: float = 0.0) -> None:
        self.answers, self.raises, self.delay, self.calls = answers or {}, raises, delay, []

    async def __call__(self, state, questions, **kw):
        self.calls.append({"state": state, "questions": dict(questions), **kw})
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.raises is not None:
            raise self.raises
        return acb_llm.Decision(
            answers=MappingProxyType({q: self.answers[q] for q in questions}),
            request_id="r1")


def _fake(monkeypatch, answers=None, **kw) -> Fake:
    fake = Fake(answers, **kw)
    monkeypatch.setattr(acb_llm, "decide", fake)
    return fake


def _log(monkeypatch) -> Log:
    log = Log()
    monkeypatch.setattr(df, "_log", log)
    return log


def _run(coro):
    async def _go():
        out = await coro
        while df._BACKGROUND:  # a detached shadow ends in the background
            await asyncio.gather(*list(df._BACKGROUND))
        return out

    return asyncio.run(_go())


class Old:
    """A counting stand-in for an old LLM call. It records its arguments."""

    def __init__(self, result: Any) -> None:
        self.result, self.calls, self.args = result, 0, []

    async def __call__(self, *args):
        self.calls += 1
        self.args.append(args)
        return self.result


FAILURES = [acb_llm.DecideUnavailable("insufficient_credits", status=402),
            TimeoutError()]

PEOPLE = [
    {"name": "Priya Nair", "email": "priya@x.com", "provider_user_id": "u1",
     "skills": ["finance"]},
    {"name": "Sam Rao", "email": "sam@x.com", "provider_user_id": "u2"},
    {"name": "", "email": "ghost@x.com"},
]


# ── The four features are registered, off, and on-legal ─────────────────────


@pytest.mark.parametrize("feature", [
    pd.PARENT_STEP, pd.ENRICH, pd.DELEGATE_PICK, pd.PROPOSE_PICKS])
def test_a_step_is_registered_off_by_default_and_bills_projects(monkeypatch, feature):
    assert feature in df.REGISTERED and feature in df.ON_FEATURES
    assert df.mode_for(feature) == "off"
    assert df.module_of(feature) == "projects"
    _mode(monkeypatch, feature, "on")
    assert df.mode_for(feature) == "on"


# ── Step 1: the parent-step check ────────────────────────────────────────────

_CANDS = [{"id": "t0", "title": "Draft the Zorblax agenda"},
          {"id": "t1", "title": "Book the Quuxville venue"}]
_PARENT = {"item_id": "t1", "title": "Book the Quuxville venue"}


def _parent(old, **kw):
    return pd.parent_step(title="Call Quuxville hotel", candidates=_CANDS, old=old, **kw)


def test_parent_off_makes_no_decide_call(monkeypatch):
    fake = _fake(monkeypatch)
    old = Old(_PARENT)
    assert _run(_parent(old)) == _PARENT
    assert fake.calls == [] and old.calls == 1


def test_parent_shadow_keeps_the_old_answer_and_logs_keys_only(monkeypatch):
    fake = _fake(monkeypatch, {"parent": ch("c1")})
    log = _log(monkeypatch)
    _mode(monkeypatch, pd.PARENT_STEP, "shadow")
    assert _run(_parent(Old(_PARENT))) == _PARENT
    assert len(fake.calls) == 1 and fake.calls[0]["module_slug"] == "projects"
    line = log.fields("decide.shadow")[0]
    assert line["decide_feature"] == pd.PARENT_STEP
    assert line["old"] == "c1" and line["new"] == "c1" and line["agree"] is True
    assert line["options"] == 3 and "old_latency_ms" in line
    assert "Quuxville" not in str(line) and "Zorblax" not in str(line)
    # A disagreement logs agree=False and still returns the old answer.
    _fake(monkeypatch, {"parent": ch("none")})
    assert _run(_parent(Old(_PARENT))) == _PARENT
    assert log.fields("decide.shadow")[1]["agree"] is False


def test_parent_shadow_does_not_wait_for_decide(monkeypatch):
    _fake(monkeypatch, {"parent": ch("c1")}, delay=0.3)
    _mode(monkeypatch, pd.PARENT_STEP, "shadow")

    async def _go():
        started = time.perf_counter()
        out = await _parent(Old(_PARENT))
        took = time.perf_counter() - started
        await asyncio.gather(*list(df._BACKGROUND))
        return out, took

    out, took = asyncio.run(_go())
    assert out == _PARENT and took < 0.2


def test_parent_on_uses_decide_with_no_llm(monkeypatch):
    fake = _fake(monkeypatch, {"parent": ch("c0")})
    _mode(monkeypatch, pd.PARENT_STEP, "on")
    old = Old(_PARENT)
    assert _run(_parent(old)) == {"item_id": "t0", "title": "Draft the Zorblax agenda"}
    assert old.calls == 0 and len(fake.calls) == 1
    # The options are the candidates plus "none", and nothing else.
    assert list(fake.calls[0]["questions"]["parent"].criteria) == ["c0", "c1", "none"]


def test_parent_on_none_means_no_parent(monkeypatch):
    _fake(monkeypatch, {"parent": ch("none")})
    _mode(monkeypatch, pd.PARENT_STEP, "on")
    old = Old(_PARENT)
    assert _run(_parent(old)) is None and old.calls == 0


@pytest.mark.parametrize("failure", FAILURES)
def test_parent_on_falls_back_to_the_old_call(monkeypatch, failure):
    _fake(monkeypatch, raises=failure)
    _mode(monkeypatch, pd.PARENT_STEP, "on")
    old = Old(_PARENT)
    assert _run(_parent(old)) == _PARENT and old.calls == 1


@pytest.mark.parametrize("answer", [ch("c9"), ch("Quuxville"), ch("c1", 0.3)])
def test_parent_on_refuses_a_pick_outside_its_options_or_below_the_floor(
        monkeypatch, answer):
    _fake(monkeypatch, {"parent": answer})
    _mode(monkeypatch, pd.PARENT_STEP, "on")
    old = Old(_PARENT)
    assert _run(_parent(old)) == _PARENT and old.calls == 1


def test_parent_on_falls_back_while_cooling(monkeypatch):
    fake = _fake(monkeypatch, raises=acb_llm.DecideUnavailable("x", status=402))
    _mode(monkeypatch, pd.PARENT_STEP, "on")
    _run(_parent(Old(_PARENT)))
    assert df.cooling(pd.PARENT_STEP)
    fake.calls.clear()
    old = Old(_PARENT)
    assert _run(_parent(old)) == _PARENT
    assert fake.calls == [] and old.calls == 1


# ── Step 2: the enrich picks ─────────────────────────────────────────────────

_CTX = ["@computer", "@calls", "@errands"]
_ALL = {"context", "energy", "assignee", "due_at", "time_estimate_mins"}
_OLD_FILL = {"context": "@calls", "energy": "low", "due_at": "2026-12-01",
             "time_estimate_mins": 15,
             "assignee": {"name": "Sam Rao", "email": "sam@x.com",
                          "provider_user_id": "u2"}}


def _enrich(old, want=None):
    return pd.enrich(title="Call Quuxville", notes="n", want=set(want or _ALL),
                     people=PEOPLE, contexts=_CTX, old=old)


def _picks(context="k1", energy="low", assignee="p1"):
    return {"context": ch(context), "energy": sc(energy), "assignee": ch(assignee)}


def test_enrich_off_makes_no_decide_call(monkeypatch):
    fake = _fake(monkeypatch)
    old = Old(_OLD_FILL)
    assert _run(_enrich(old)) == _OLD_FILL
    assert fake.calls == [] and old.args == [(_ALL,)]


def test_enrich_shadow_keeps_the_old_answer_and_logs_keys_only(monkeypatch):
    fake = _fake(monkeypatch, _picks())
    log = _log(monkeypatch)
    _mode(monkeypatch, pd.ENRICH, "shadow")
    assert _run(_enrich(Old(_OLD_FILL))) == _OLD_FILL
    assert len(fake.calls) == 1
    lines = {ln["decide_item"]: ln for ln in log.fields("decide.shadow")}
    assert set(lines) == {"context", "energy", "assignee"}
    assert lines["context"]["old"] == "k1" and lines["context"]["agree"] is True
    assert lines["assignee"]["old"] == "p1" and lines["assignee"]["agree"] is True
    assert lines["energy"]["old"] == "low" and lines["energy"]["agree"] is True
    text = str(log.lines)
    assert "Sam" not in text and "@calls" not in text and "Quuxville" not in text
    # A disagreement is logged, and the old answer is still returned.
    _fake(monkeypatch, _picks(context="k2"))
    assert _run(_enrich(Old(_OLD_FILL))) == _OLD_FILL
    assert [ln for ln in log.fields("decide.shadow")
            if ln["decide_item"] == "context"][1]["agree"] is False


def test_enrich_shadow_with_an_old_failure_logs_no_agreement(monkeypatch):
    _fake(monkeypatch, _picks())
    log = _log(monkeypatch)
    _mode(monkeypatch, pd.ENRICH, "shadow")
    assert _run(_enrich(Old(None))) is None
    assert log.fields("decide.shadow") == []
    assert log.fields("decide.shadow_failed")[0]["error_type"] == "OldCallFailed"


def test_enrich_on_decide_answers_the_picks_and_the_llm_only_the_due_date(monkeypatch):
    fake = _fake(monkeypatch, _picks(context="k2", energy="high", assignee="p0"))
    _mode(monkeypatch, pd.ENRICH, "on")
    old = Old({"due_at": "2026-12-01", "time_estimate_mins": 15,
               "context": "@calls"})  # the LLM's own context must lose
    out = _run(_enrich(old))
    assert old.args == [({"due_at", "time_estimate_mins"},)]
    assert out["due_at"] == "2026-12-01" and out["time_estimate_mins"] == 15
    assert out["context"] == "@errands" and out["energy"] == "high"
    assert out["assignee"] == {"name": "Priya Nair", "email": "priya@x.com",
                               "provider_user_id": "u1"}
    assert len(fake.calls) == 1


def test_enrich_on_makes_no_llm_call_when_no_due_date_is_missing(monkeypatch):
    _fake(monkeypatch, _picks())
    _mode(monkeypatch, pd.ENRICH, "on")
    old = Old(_OLD_FILL)
    out = _run(_enrich(old, want={"context", "energy", "assignee"}))
    assert old.calls == 0
    assert out["context"] == "@calls" and out["assignee"]["name"] == "Sam Rao"


def test_enrich_on_asks_only_for_the_missing_fields(monkeypatch):
    fake = _fake(monkeypatch, {"context": ch("k0")})
    _mode(monkeypatch, pd.ENRICH, "on")
    assert _run(_enrich(Old({}), want={"context"})) == {"context": "@computer"}
    assert list(fake.calls[0]["questions"]) == ["context"]


def test_enrich_assignee_options_are_the_named_people_only(monkeypatch):
    fake = _fake(monkeypatch, {"assignee": ch("p0")})
    _mode(monkeypatch, pd.ENRICH, "on")
    _run(_enrich(Old({}), want={"assignee"}))
    # The unnamed third person is not offered. No other person is added.
    assert list(fake.calls[0]["questions"]["assignee"].criteria) == ["p0", "p1", "none"]


def test_enrich_on_a_none_pick_leaves_the_field_to_the_heuristic(monkeypatch):
    _fake(monkeypatch, _picks(context="none", assignee="none"))
    _mode(monkeypatch, pd.ENRICH, "on")
    out = _run(_enrich(Old(_OLD_FILL), want={"context", "assignee", "due_at"}))
    assert "context" not in out and "assignee" not in out and out["due_at"]


@pytest.mark.parametrize("bad", [
    {"context": ch("k9"), "energy": sc("low"), "assignee": ch("p1")},
    {"context": ch("k1"), "energy": sc("low"), "assignee": ch("p2")},  # unnamed person
    {"context": ch("k1", 0.2), "energy": sc("low"), "assignee": ch("p1")},
])
def test_enrich_on_a_bad_pick_goes_back_to_the_llm_for_that_field(monkeypatch, bad):
    _fake(monkeypatch, bad)
    _mode(monkeypatch, pd.ENRICH, "on")
    old = Old({"context": "@computer"})
    out = _run(_enrich(old, want={"context", "energy", "assignee"}))
    first = old.args[0][0]
    assert first in ({"context"}, {"assignee"})
    assert out["energy"] == "low"


def test_enrich_on_an_energy_outside_the_scale_goes_back_to_the_llm(monkeypatch):
    _fake(monkeypatch, {"energy": sc("extreme")})
    _mode(monkeypatch, pd.ENRICH, "on")
    old = Old({"energy": "medium"})
    assert _run(_enrich(old, want={"energy"})) == {"energy": "medium"}
    assert old.args == [({"energy"},)]


@pytest.mark.parametrize("failure", FAILURES)
def test_enrich_on_falls_back_to_the_old_call(monkeypatch, failure):
    _fake(monkeypatch, raises=failure)
    _mode(monkeypatch, pd.ENRICH, "on")
    old = Old(_OLD_FILL)
    assert _run(_enrich(old)) == _OLD_FILL and old.args == [(_ALL,)]


def test_the_enrich_wrapper_in_ai_passes_the_user_contexts(monkeypatch):
    from gateway.routes.tasks import ai

    seen: dict[str, Any] = {}

    async def _old(item, want, people, model, contexts=None):
        seen.update(want=want, contexts=contexts, model=model)
        return {"context": "@calls"}

    monkeypatch.setattr(ai, "_llm_enrich", _old)
    item = type("I", (), {"title": "Call", "description": None})()
    out = _run(ai._enrich_picks(item, {"context", "due_at"}, PEOPLE, "m", contexts=_CTX))
    assert out == {"context": "@calls"}
    assert seen == {"want": {"context", "due_at"}, "contexts": _CTX, "model": "m"}


# ── Step 3: the delegate pick ────────────────────────────────────────────────

_KW = {"subject": "Quote", "from_name": "Anil", "from_email": "anil@y.com",
       "owner_addrs": {"v@x.com"}, "body": "Please check the Quuxville quote.",
       "thread": "", "people": PEOPLE}


def _resolve(name: str, people: list[dict]) -> dict | None:
    from gateway.routes.tasks.capture_email import _resolve_assignee

    return _resolve_assignee(name, people)


def _delegate(old):
    return pd.delegate_pick(old=old, resolve=_resolve, **_KW)


def _draft(name: str = "Sam Rao") -> dict[str, Any]:
    return {"title": "Check the quote", "disposition": "WAITING", "assignee_name": name}


def test_delegate_off_makes_no_decide_call(monkeypatch):
    fake = _fake(monkeypatch)
    old = Old(_draft())
    assert _run(_delegate(old)) == _draft()
    assert fake.calls == [] and old.calls == 1


def test_delegate_shadow_keeps_the_old_answer_and_logs_keys_only(monkeypatch):
    fake = _fake(monkeypatch, {"delegate": ch("p1")})
    log = _log(monkeypatch)
    _mode(monkeypatch, pd.DELEGATE_PICK, "shadow")
    assert _run(_delegate(Old(_draft()))) == _draft()
    assert len(fake.calls) == 1
    line = log.fields("decide.shadow")[0]
    assert line["decide_feature"] == pd.DELEGATE_PICK
    assert line["old"] == "p1" and line["new"] == "p1" and line["agree"] is True
    assert "Sam" not in str(line) and "Quuxville" not in str(line)
    # A name that is not on the roster reads as none.
    _fake(monkeypatch, {"delegate": ch("none")})
    assert _run(_delegate(Old(_draft("Anil")))) == _draft("Anil")
    assert log.fields("decide.shadow")[1]["old"] == "none"
    assert log.fields("decide.shadow")[1]["agree"] is True


def test_delegate_shadow_with_no_draft_logs_no_agreement(monkeypatch):
    _fake(monkeypatch, {"delegate": ch("p1")})
    log = _log(monkeypatch)
    _mode(monkeypatch, pd.DELEGATE_PICK, "shadow")
    assert _run(_delegate(Old(None))) is None
    assert log.fields("decide.shadow") == []


def test_delegate_on_decide_picks_the_delegate_and_the_draft_stays_an_llm_call(
        monkeypatch):
    fake = _fake(monkeypatch, {"delegate": ch("p0")})
    _mode(monkeypatch, pd.DELEGATE_PICK, "on")
    old = Old(_draft("Sam Rao"))
    out = _run(_delegate(old))
    assert old.calls == 1 and out["assignee_name"] == "Priya Nair"
    assert out["title"] == "Check the quote"
    assert list(fake.calls[0]["questions"]["delegate"].criteria) == ["p0", "p1", "none"]


def test_delegate_on_none_clears_a_roster_name_and_keeps_a_sender_name(monkeypatch):
    _fake(monkeypatch, {"delegate": ch("none")})
    _mode(monkeypatch, pd.DELEGATE_PICK, "on")
    assert _run(_delegate(Old(_draft("Sam Rao"))))["assignee_name"] == ""
    assert _run(_delegate(Old(_draft("Anil")))) ["assignee_name"] == "Anil"


@pytest.mark.parametrize("failure", FAILURES)
def test_delegate_on_falls_back_to_the_old_draft(monkeypatch, failure):
    _fake(monkeypatch, raises=failure)
    _mode(monkeypatch, pd.DELEGATE_PICK, "on")
    old = Old(_draft())
    assert _run(_delegate(old)) == _draft() and old.calls == 1


@pytest.mark.parametrize("answer", [ch("p2"), ch("p9"), ch("Priya Nair"), ch("p0", 0.2)])
def test_delegate_on_refuses_a_pick_outside_its_options_or_below_the_floor(
        monkeypatch, answer):
    _fake(monkeypatch, {"delegate": answer})
    _mode(monkeypatch, pd.DELEGATE_PICK, "on")
    assert _run(_delegate(Old(_draft()))) == _draft()


def test_delegate_on_with_no_draft_returns_none(monkeypatch):
    _fake(monkeypatch, {"delegate": ch("p0")})
    _mode(monkeypatch, pd.DELEGATE_PICK, "on")
    assert _run(_delegate(Old(None))) is None


def test_delegate_makes_no_decide_call_with_nobody_to_pick(monkeypatch):
    fake = _fake(monkeypatch)
    _mode(monkeypatch, pd.DELEGATE_PICK, "on")
    old = Old(_draft(""))
    kw = {**_KW, "people": []}
    assert _run(pd.delegate_pick(old=old, resolve=_resolve, **kw)) == _draft("")
    assert fake.calls == []


def test_the_capture_wrapper_in_capture_email_is_off_by_default(monkeypatch):
    from gateway.routes.tasks import capture_email as ce

    seen: dict[str, Any] = {}

    async def _old(**kw):
        seen.update(kw)
        return _draft()

    monkeypatch.setattr(ce, "_llm_capture", _old)
    fake = _fake(monkeypatch)
    out = _run(ce._draft_capture(
        **{**_KW, "cc_line": "", "to_line": "", "model": "m", "contexts": None}))
    assert out == _draft() and seen["model"] == "m" and fake.calls == []


# ── The commitment route reads the contexts only inside the old call (DEC-2a P2) ──


def test_detect_commitment_reads_the_contexts_only_in_the_old_call(monkeypatch):
    from contextlib import asynccontextmanager

    from gateway.routes.tasks import ai, people, settings
    from gateway.routes.tasks import capture_email as ce

    reads: list[int] = []

    @asynccontextmanager
    async def _session():
        yield object()

    async def _none(*_a, **_k):
        return None

    async def _owner(*_a, **_k):
        return "Vijay", "v@x.com"

    async def _people(_db):
        return []

    async def _models(_db, _uid):
        return {"email_capture": "m"}

    async def _contexts(_db, _uid):
        reads.append(1)
        return ["@calls"]

    async def _llm(**_kw):
        return {"is_commitment": True, "title": "Send the quote"}

    async def _similar(*_a, **_k):
        return []

    monkeypatch.setattr(ce, "_tenant_session", _session)
    monkeypatch.setattr(ce, "_find_existing_commitment", _none)
    monkeypatch.setattr(ce, "_account_owner", _owner)
    monkeypatch.setattr(ce, "_llm_detect_commitment", _llm)
    monkeypatch.setattr(ce, "_find_similar_tasks", _similar)
    monkeypatch.setattr(ai, "_user_contexts", _contexts)
    monkeypatch.setattr(people, "fetch_people_for_clarify", _people)
    monkeypatch.setattr(settings, "task_models", _models)
    user = type("U", (), {"email": "v@x.com"})()
    req = ce.DetectCommitmentRequest(account_id="a", thread_id="t", body="I will send it.")

    # `off`: the old call runs, and reads the contexts once.
    _fake(monkeypatch)
    out = _run(ce.detect_commitment_from_reply(req, user))
    assert out.is_commitment is True and reads == [1]
    # `on`, decide says no: no LLM call, and no contexts read.
    reads.clear()
    _fake(monkeypatch, {"commitment": acb_llm.BooleanAnswer(probability=0.1)})
    _mode(monkeypatch, pd.COMMITMENT, "on")
    out = _run(ce.detect_commitment_from_reply(req, user))
    assert out.is_commitment is False and reads == []


# ── Step 4: the propose picks ────────────────────────────────────────────────


class _P:
    def __init__(self, outcome: str, status: str = "ACTIVE", purpose: str | None = None):
        self.outcome, self.status, self.purpose, self.account_id = outcome, status, purpose, None


_PROJ = [_P("Launch the Zorblax portal"), _P("Hire the finance lead")]
_DATA = {"disposition": "WAITING", "next_action": "Ask Sam for the quote",
         "project_match": "[P1]", "assignee_name": "Sam Rao"}
_USER = "CAPTURED ITEM:\n\"Get the Quuxville quote\""


def _resolve_project(token: str):
    from gateway.routes.tasks.ai import _resolve_project_match

    return _resolve_project_match(token, _PROJ)


class Run:
    """A stand-in for the one clarify model call."""

    def __init__(self, data) -> None:
        self.data, self.users = data, []

    async def __call__(self, user: str):
        self.users.append(user)
        return (None, "timeout") if self.data is None else (dict(self.data), None)


def _propose(run):
    return pd.propose_picks(
        title="Get the Quuxville quote", notes=None, guidance="", people=PEOPLE,
        projects=_PROJ, user=_USER, run=run, resolve_project=_resolve_project)


def _pp(disposition="WAITING", project="j1", assignee="p1"):
    return {"disposition": ch(disposition), "project": ch(project), "assignee": ch(assignee)}


def test_propose_off_makes_no_decide_call(monkeypatch):
    fake = _fake(monkeypatch)
    run = Run(_DATA)
    assert _run(_propose(run)) == (_DATA, None)
    assert fake.calls == [] and run.users == [_USER]


def test_propose_shadow_keeps_the_old_answer_and_logs_keys_only(monkeypatch):
    fake = _fake(monkeypatch, _pp())
    log = _log(monkeypatch)
    _mode(monkeypatch, pd.PROPOSE_PICKS, "shadow")
    run = Run(_DATA)
    assert _run(_propose(run)) == (_DATA, None)
    assert run.users == [_USER] and len(fake.calls) == 1
    lines = {ln["decide_item"]: ln for ln in log.fields("decide.shadow")}
    assert set(lines) == {"disposition", "project", "assignee"}
    assert lines["disposition"]["old"] == "WAITING" and lines["disposition"]["agree"]
    assert lines["project"]["old"] == "j1" and lines["project"]["agree"]
    assert lines["assignee"]["old"] == "p1" and lines["assignee"]["agree"]
    text = str(log.lines)
    assert "Sam" not in text and "Zorblax" not in text and "Quuxville" not in text
    # A disagreement is logged, and the old answer is still returned.
    _fake(monkeypatch, _pp(disposition="NEXT"))
    assert _run(_propose(Run(_DATA))) == (_DATA, None)
    assert [ln for ln in log.fields("decide.shadow")
            if ln["decide_item"] == "disposition"][1]["agree"] is False


def test_propose_shadow_skips_the_assignee_unless_the_item_is_delegated(monkeypatch):
    _fake(monkeypatch, _pp(disposition="NEXT"))
    log = _log(monkeypatch)
    _mode(monkeypatch, pd.PROPOSE_PICKS, "shadow")
    _run(_propose(Run({**_DATA, "disposition": "NEXT", "assignee_name": ""})))
    assert {ln["decide_item"] for ln in log.fields("decide.shadow")} == {
        "disposition", "project"}


def test_propose_shadow_with_an_old_failure_logs_no_agreement(monkeypatch):
    _fake(monkeypatch, _pp())
    log = _log(monkeypatch)
    _mode(monkeypatch, pd.PROPOSE_PICKS, "shadow")
    assert _run(_propose(Run(None))) == (None, "timeout")
    assert log.fields("decide.shadow") == []


def test_propose_on_decide_picks_and_the_llm_writes_the_prose(monkeypatch):
    fake = _fake(monkeypatch, _pp(disposition="WAITING", project="j0", assignee="p0"))
    _mode(monkeypatch, pd.PROPOSE_PICKS, "on")
    run = Run(_DATA)  # the model's own picks (P1, Sam) must lose
    data, reason = _run(_propose(run))
    assert reason is None and len(run.users) == 1 and len(fake.calls) == 1
    assert run.users[0].startswith(_USER)
    for fixed in ("disposition: WAITING", "project_match: [P0]",
                  "assignee_name: Priya Nair"):
        assert fixed in run.users[0]
    assert data["disposition"] == "WAITING" and data["project_match"] == "[P0]"
    assert data["assignee_name"] == "Priya Nair"
    assert data["next_action"] == "Ask Sam for the quote"  # the prose is the LLM's
    q = fake.calls[0]["questions"]
    assert list(q["project"].criteria) == ["j0", "j1", "none"]
    assert list(q["assignee"].criteria) == ["p0", "p1", "none"]
    assert list(q["disposition"].criteria) == list(pd.DISPOSITIONS)


def test_propose_on_a_none_project_clears_the_project(monkeypatch):
    _fake(monkeypatch, _pp(disposition="NEXT", project="none"))
    _mode(monkeypatch, pd.PROPOSE_PICKS, "on")
    data, _ = _run(_propose(Run(_DATA)))
    assert data["project_match"] == "" and data["disposition"] == "NEXT"
    assert data["assignee_name"] == "Sam Rao"  # not delegated: the pick is not applied


@pytest.mark.parametrize("failure", FAILURES)
def test_propose_on_falls_back_to_the_plain_call(monkeypatch, failure):
    _fake(monkeypatch, raises=failure)
    _mode(monkeypatch, pd.PROPOSE_PICKS, "on")
    run = Run(_DATA)
    assert _run(_propose(run)) == (_DATA, None) and run.users == [_USER]


@pytest.mark.parametrize("answers", [
    {"disposition": ch("MAYBE"), "project": ch("j9"), "assignee": ch("p2")},
    {"disposition": ch("NEXT", 0.2), "project": ch("j0", 0.2), "assignee": ch("p0", 0.2)},
])
def test_propose_on_refuses_picks_outside_the_options_or_below_the_floor(
        monkeypatch, answers):
    _fake(monkeypatch, answers)
    _mode(monkeypatch, pd.PROPOSE_PICKS, "on")
    run = Run(_DATA)
    assert _run(_propose(run)) == (_DATA, None) and run.users == [_USER]


def test_propose_on_keeps_a_usable_pick_when_another_is_bad(monkeypatch):
    _fake(monkeypatch, _pp(disposition="PROJECT", project="j9", assignee="p0"))
    _mode(monkeypatch, pd.PROPOSE_PICKS, "on")
    run = Run(_DATA)
    data, _ = _run(_propose(run))
    assert data["disposition"] == "PROJECT" and data["project_match"] == "[P1]"
    assert "project_match" not in run.users[0]


def test_propose_on_passes_a_model_failure_through(monkeypatch):
    _fake(monkeypatch, _pp())
    _mode(monkeypatch, pd.PROPOSE_PICKS, "on")
    assert _run(_propose(Run(None))) == (None, "timeout")


def test_propose_offers_only_the_active_projects_the_prompt_shows(monkeypatch):
    # The wrapper passes `_active_projects(projects)[:40]`, so the keys match [P#].
    from gateway.routes.tasks import ai

    seen: dict[str, Any] = {}

    async def _model(model, system, user):
        seen["user"] = user
        return dict(_DATA), None

    monkeypatch.setattr(ai, "_ask_clarify_model", _model)
    fake = _fake(monkeypatch, _pp(project="j0"))
    _mode(monkeypatch, pd.PROPOSE_PICKS, "on")
    item = type("I", (), {"title": "Get the quote", "description": None, "id": "i1"})()
    projects = [_P("Parked", status="SOMEDAY"), *_PROJ]
    core = _run(ai._llm_propose(item, PEOPLE, projects, {}, "m"))
    assert list(fake.calls[0]["questions"]["project"].criteria) == ["j0", "j1", "none"]
    assert core is not None and core["llm_project"] is projects[1]
    assert "project_match: [P0]" in seen["user"]
