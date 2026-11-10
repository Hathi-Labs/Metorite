"""DEC-1 — the ONE seam of the decision tier serves Projects (task 2306).

Hermetic. ``acb_llm.decide`` is a FAKE, so no test reaches a network or a model.

Fenced here:
- the mode table: every feature that existed before DEC-1 resolves the mode it
  resolved before (its org flag, and whether ``on`` is legal);
- a ``projects.`` feature reads ``projects_decide_orgs`` and bills ``projects``;
- the table is the one source of ``REGISTERED`` and ``ON_FEATURES``;
- ``ask`` on an ``off`` feature builds nothing, calls nothing and returns None;
- ``shadow`` runs the old path, runs decide beside it, and logs the agreement;
- 40 questions make 3 requests that run at the same time, in input order;
- the typed choices of a skill (``ask_typed``) write ``decide.*`` and honour the
  cool-down that the gateway seam shares.

Mutation: drop the ``projects.`` row of ``PREFIXES`` and the Projects tests
fail. Drop the ``off`` check in ``ask`` and ``test_ask_on_an_off_feature_*``
fails.
"""
from __future__ import annotations

import asyncio
from types import MappingProxyType
from typing import Any

import acb_llm
import pytest
from acb_common.db import bind_tenant, release_tenant
from acb_common.settings import get_settings
from acb_llm import decide_policy
from gateway import decide_features as df

ORG = "dec1a1a1-0000-4000-8000-000000000001"
OTHER = "dec1a1a1-0000-4000-8000-000000000002"

#: The mode table BEFORE DEC-1, frozen: feature → (org setting, ``on`` legal).
BEFORE: dict[str, tuple[str, bool]] = {
    "email.cold_check": ("decide_feature_orgs", True),
    "email.sender_pin": ("decide_feature_orgs", True),
    "email.thread_status": ("decide_feature_orgs", True),
    "email.rule_match": ("decide_feature_orgs", True),
    "email.insights_screen": ("decide_feature_orgs", True),
    "my_day.triage": ("my_day_decide_orgs", False),
    "my_day.weight": ("my_day_decide_orgs", False),
    "my_day.reason": ("my_day_decide_orgs", False),
    "my_day.next_act": ("my_day_decide_orgs", False),
    "my_day.slot": ("my_day_decide_orgs", False),
    "my_day.resurface": ("my_day_decide_orgs", False),
    "my_day.compose": ("my_day_decide_orgs", False),
    "my_day.nudge": ("my_day_decide_orgs", False),
    "my_day.day_plan": ("my_day_decide_orgs", True),
    "crm.note_labels": ("crm_decide_orgs", True),
    "crm.map_role": ("crm_decide_orgs", True),
    "crm.map_target": ("crm_decide_orgs", True),
    "crm.map_stage": ("crm_decide_orgs", True),
    "crm.map_catalogue": ("crm_decide_orgs", True),
}
#: DEC-2a: the three yes/no steps of Tasks AI.
DEC2A = ("projects.vague_title", "projects.atomize_dedup", "projects.commitment")
#: DEC-2b: the four pick steps of Tasks AI.
DEC2B = ("projects.parent_step", "projects.enrich", "projects.delegate_pick",
         "projects.propose_picks")
ORG_SETTINGS = ("decide_feature_orgs", "my_day_decide_orgs", "crm_decide_orgs",
                "projects_decide_orgs")


def _clear() -> None:
    get_settings.cache_clear()
    df._parse_modes.cache_clear()
    df._parse_orgs.cache_clear()


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for name in ("DECIDE_FEATURE_MODES", *(s.upper() for s in ORG_SETTINGS)):
        monkeypatch.setenv(name, "")
    _clear()
    _clear_cooldowns()
    token = bind_tenant(ORG)
    yield
    release_tenant(token)
    monkeypatch.undo()
    _clear()
    _clear_cooldowns()


def _clear_cooldowns() -> None:
    df._cooldown_until.clear()
    for table in decide_policy.ISOLATED_UNTIL.values():
        table.clear()


def _set(monkeypatch, modes: str, **orgs: str) -> None:
    monkeypatch.setenv("DECIDE_FEATURE_MODES", modes)
    for setting, value in orgs.items():
        monkeypatch.setenv(setting.upper(), value)
    _clear()


class Log:
    """A stand-in for a structlog logger. Keeps (level, event, fields)."""

    def __init__(self) -> None:
        self.lines: list[tuple[str, str, dict[str, Any]]] = []

    def _add(self, level: str):
        return lambda event, **kw: self.lines.append((level, event, kw))

    def __getattr__(self, name: str):
        if name in ("info", "warning", "error", "debug"):
            return self._add(name)
        raise AttributeError(name)

    def events(self) -> list[str]:
        return [e for _l, e, _k in self.lines]

    def fields(self, event: str) -> list[dict[str, Any]]:
        return [k for _l, e, k in self.lines if e == event]


class FakeDecide:
    """Answers each question with a yes (boolean) or its first option (choice)."""

    def __init__(self, delay: float = 0.0, raises: BaseException | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.delay = delay
        self.raises = raises
        self.in_flight = 0
        self.peak = 0

    async def __call__(self, state, questions, **kwargs):
        self.calls.append({"state": state, "questions": dict(questions), **kwargs})
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
        finally:
            self.in_flight -= 1
        if self.raises is not None:
            raise self.raises
        answers = {
            qid: acb_llm.BooleanAnswer(probability=0.9) for qid in questions
        }
        return acb_llm.Decision(answers=MappingProxyType(answers), request_id="r1")


def _fake(monkeypatch, **kw) -> FakeDecide:
    fake = FakeDecide(**kw)
    monkeypatch.setattr(acb_llm, "decide", fake)
    return fake


def _questions(n: int) -> dict[str, Any]:
    return {f"q{i:02d}": acb_llm.BooleanQuestion(
        instructions=f"Is {i} a twin?", criteria={"true": "yes", "false": "no"})
        for i in range(n)}


def _read(decision):
    return list(decision.answers), {}


# ── The mode table ───────────────────────────────────────────────────────────


def test_the_registry_lists_every_old_feature_in_the_old_order():
    old = list(BEFORE)
    assert list(df.REGISTERED[:len(old)]) == old
    assert df.REGISTERED[len(old):] == ("projects.align", *DEC2A, *DEC2B)
    assert df.FEATURES == tuple(f for f in old if f.startswith("email."))
    assert df.MY_DAY_FEATURES == tuple(
        f for f in old if f.startswith("my_day.") and f != "my_day.day_plan")
    assert df.DAY_PLAN_FEATURES == ("my_day.day_plan",)
    assert df.CRM_FEATURES == tuple(f for f in old if f.startswith("crm."))
    assert df.PROJECTS_FEATURES == ("projects.align", *DEC2A, *DEC2B)


def test_on_features_is_the_old_set_plus_projects_align():
    old_on = frozenset(f for f, (_s, on) in BEFORE.items() if on)
    assert df.ON_FEATURES == old_on | {"projects.align", *DEC2A, *DEC2B}


def test_every_feature_has_a_question_type_and_one_entry():
    names = [f.name for f in df.FEATURE_TABLE]
    assert len(names) == len(set(names))
    assert {f.qtype for f in df.FEATURE_TABLE} <= {"boolean", "choice", "score", "mixed"}


@pytest.mark.parametrize("feature", list(BEFORE))
def test_each_old_feature_resolves_the_mode_it_resolved_before(monkeypatch, feature):
    setting, on_legal = BEFORE[feature]
    # The right org list opens `shadow`. Every other list leaves it `off`.
    _set(monkeypatch, f"{feature}=shadow", **{setting: ORG})
    assert df.mode_for(feature) == "shadow"
    for wrong in (s for s in ORG_SETTINGS if s != setting):
        _set(monkeypatch, f"{feature}=shadow", **{setting: "", wrong: ORG})
        assert df.mode_for(feature) == "off", (feature, wrong)
    # `on` holds only where it was legal.
    _set(monkeypatch, f"{feature}=on", **{setting: ORG, ORG_SETTINGS[-1]: ""})
    assert df.mode_for(feature) == ("on" if on_legal else "off")
    # `*` allows every org. An org not on the list is `off`. No list is `off`.
    _set(monkeypatch, f"{feature}=shadow", **{setting: "*"})
    assert df.mode_for(feature) == "shadow"
    _set(monkeypatch, f"{feature}=shadow", **{setting: OTHER})
    assert df.mode_for(feature) == "off"
    _set(monkeypatch, f"{feature}=shadow", **{setting: ""})
    assert df.mode_for(feature) == "off"


def test_an_unknown_feature_and_an_unknown_mode_stay_off(monkeypatch):
    _set(monkeypatch, "email.rule_pick=shadow,email.cold_check=maybe",
         decide_feature_orgs=ORG)
    assert df.mode_for("email.rule_pick") == "off"
    assert df.mode_for("email.cold_check") == "off"


# ── The projects. prefix ─────────────────────────────────────────────────────


def test_a_projects_feature_reads_projects_decide_orgs(monkeypatch):
    _set(monkeypatch, "projects.align=shadow", projects_decide_orgs=ORG)
    assert df.mode_for("projects.align") == "shadow"
    # The email, My Day and CRM lists do not start it.
    _set(monkeypatch, "projects.align=shadow", projects_decide_orgs="",
         decide_feature_orgs=ORG, my_day_decide_orgs=ORG, crm_decide_orgs=ORG)
    assert df.mode_for("projects.align") == "off"
    _set(monkeypatch, "projects.align=on", projects_decide_orgs=ORG)
    assert df.mode_for("projects.align") == "on"


def test_the_projects_list_does_not_start_an_email_feature(monkeypatch):
    _set(monkeypatch, "email.cold_check=shadow", projects_decide_orgs=ORG)
    assert df.mode_for("email.cold_check") == "off"


def test_the_projects_flag_is_empty_by_default():
    from acb_common.settings import Settings

    assert Settings().projects_decide_orgs == ""


def test_the_projects_flag_is_an_operator_name():
    from acb_common import env_guard

    assert "PROJECTS_DECIDE_ORGS" in env_guard.PLATFORM_ENV_NAMES


def test_a_projects_call_bills_projects_and_an_email_call_keeps_its_app(monkeypatch):
    fake = _fake(monkeypatch)
    _set(monkeypatch, "projects.align=on,email.cold_check=on",
         projects_decide_orgs=ORG, decide_feature_orgs=ORG)
    q = _questions(1)
    out = asyncio.run(df.ask("projects.align", build=lambda: ({"t": 1}, q), read=_read))
    assert out == ["q00"]
    assert fake.calls[0]["module_slug"] == "projects"
    asyncio.run(df.ask("email.cold_check", build=lambda: ({"t": 1}, q), read=_read))
    assert fake.calls[1]["module_slug"] == "email"
    # The caller's own slug still wins.
    asyncio.run(df.ask("projects.align", build=lambda: ({"t": 1}, q), read=_read,
                       module_slug="crm"))
    assert fake.calls[2]["module_slug"] == "crm"


def test_module_of_derives_the_app_from_the_prefix():
    assert df.module_of("projects.align") == "projects"
    assert df.module_of("crm.map_role") == "crm"
    assert df.module_of("my_day.triage") == "my_day"
    assert df.module_of("email.cold_check") is None
    assert df.module_of("email.cold_check", "x") == "x"


def test_a_403_from_projects_does_not_start_the_cool_down(monkeypatch):
    fake = _fake(monkeypatch, raises=acb_llm.DecideUnavailable("forbidden", status=403))
    _set(monkeypatch, "projects.align=on", projects_decide_orgs=ORG)
    asyncio.run(df.ask("projects.align", build=lambda: ({}, _questions(1)), read=_read))
    assert not df.cooling("projects.align") and not df.cooling()
    fake.raises = acb_llm.DecideUnavailable("insufficient_credits", status=402)
    asyncio.run(df.ask("projects.align", build=lambda: ({}, _questions(1)), read=_read))
    assert df.cooling("projects.align")
    # DEC-1 repair: a Projects 402 pauses Projects only.
    assert not df.cooling() and not df.cooling("crm.map_role")
    assert not df.cooling("my_day.triage") and not df.cooling("email.rule_match")


# ── ask reads the mode ───────────────────────────────────────────────────────


def test_ask_on_an_off_feature_builds_nothing_calls_nothing_and_returns_none(monkeypatch):
    fake = _fake(monkeypatch)
    built: list[int] = []

    def _build():
        built.append(1)
        return {}, _questions(1)

    # No org list: the feature is off for the org.
    _set(monkeypatch, "projects.align=on")
    async def _go():
        # `last_no_answer` is a ContextVar, so read it in the same task.
        return await df.ask("projects.align", build=_build, read=_read), df.last_no_answer()

    out, why = asyncio.run(_go())
    assert out is None
    assert built == [] and fake.calls == []
    assert why == "mode_off"
    # No mode: off by default, also with the org listed.
    _set(monkeypatch, "", projects_decide_orgs=ORG)
    assert asyncio.run(df.ask("projects.align", build=_build, read=_read)) is None
    assert built == [] and fake.calls == []


def test_ask_on_an_unknown_feature_returns_none(monkeypatch):
    fake = _fake(monkeypatch)
    _set(monkeypatch, "x.y=on", decide_feature_orgs=ORG)
    assert asyncio.run(df.ask("x.y", build=lambda: ({}, _questions(1)), read=_read)) is None
    assert fake.calls == []


@pytest.mark.parametrize("mode", ["shadow", "on"])
def test_ask_goes_on_in_shadow_and_on(monkeypatch, mode):
    fake = _fake(monkeypatch)
    _set(monkeypatch, f"crm.note_labels={mode}", crm_decide_orgs=ORG)
    out = asyncio.run(df.ask("crm.note_labels", build=lambda: ({}, _questions(2)),
                             read=_read, module_slug="crm"))
    assert out == ["q00", "q01"]
    assert len(fake.calls) == 1


# ── shadow ───────────────────────────────────────────────────────────────────


def test_shadow_runs_the_old_path_and_decide_beside_it_and_logs_agreement(monkeypatch):
    fake = _fake(monkeypatch)
    log = Log()
    monkeypatch.setattr(df, "_log", log)
    _set(monkeypatch, "projects.align=shadow", projects_decide_orgs=ORG)
    old_calls: list[int] = []

    async def _old():
        old_calls.append(1)
        return True

    out = asyncio.run(df.shadow(
        "projects.align", _old, build=lambda: ({"t": 1}, _questions(1)),
        compare=df.compare_boolean(lambda r: r, qid="q00")))
    assert out is True and old_calls == [1]
    assert len(fake.calls) == 1
    assert fake.calls[0]["module_slug"] == "projects"
    line = log.fields("decide.shadow")[0]
    assert line["decide_feature"] == "projects.align"
    assert line["old"] is True and line["agree"] is True


def test_shadow_on_an_off_feature_runs_only_the_old_path(monkeypatch):
    fake = _fake(monkeypatch)
    _set(monkeypatch, "projects.align=shadow")  # no org: off
    built: list[int] = []

    async def _old():
        return "old"

    def _build():
        built.append(1)
        return {}, _questions(1)

    out = asyncio.run(df.shadow("projects.align", _old, build=_build,
                                compare=df.compare_boolean(lambda r: True, qid="q00")))
    assert out == "old" and fake.calls == [] and built == []


# ── The chunk helper ─────────────────────────────────────────────────────────


def test_40_questions_make_3_requests_that_run_together_and_return_in_order(monkeypatch):
    fake = _fake(monkeypatch, delay=0.05)
    _set(monkeypatch, "projects.align=on", projects_decide_orgs=ORG)
    qs = _questions(40)
    state = {"task": "t1", "rules": ["a", "b"]}
    out = asyncio.run(df.ask(
        "projects.align", build=lambda: df.chunk_requests(state, qs), read=_read))
    assert out == list(qs), "the answers come back in the input order"
    assert sorted(len(c["questions"]) for c in fake.calls) == [8, 16, 16]
    assert all(c["state"] == state for c in fake.calls), "each request holds the full state"
    assert fake.peak == 3, "the three requests ran at the same time"
    assert df.QUESTION_LIMIT == 16


def test_chunk_requests_cuts_in_order_and_an_empty_list_gives_none():
    qs = _questions(33)
    parts = df.chunk_requests("s", qs)
    assert [len(q) for _s, q in parts] == [16, 16, 1]
    assert [k for _s, q in parts for k in q] == list(qs)
    assert df.chunk_requests("s", {}) == []
    assert [len(q) for _s, q in df.chunk_requests("s", _questions(16))] == [16]
    assert [len(q) for _s, q in df.chunk_requests("s", _questions(5), size=2)] == [2, 2, 1]


def test_map_pipeline_split_is_the_chunk_helper():
    from gateway.crm_sync import map_pipeline as mp

    qs = _questions(20)
    state = {"s": 1}
    assert mp.split(qs, state) == df.chunk_requests(state, qs)


# ── The cool-down is one state, for the gateway and for a skill ──────────────


def test_the_gateway_cool_down_is_the_shared_state():
    assert df._cooldown_until is decide_policy.COOLDOWN_UNTIL
    assert df.REFUSAL_COOLDOWN_S == decide_policy.REFUSAL_COOLDOWN_S
    assert not df.cooling()
    decide_policy.start_cooldown(ORG)
    assert df.cooling() and df.cooling("crm.map_role") and df.cooling("my_day.triage")
    assert not df.cooling("projects.align"), "Projects has its own table"
    assert not decide_policy.cooling(OTHER)
    assert not decide_policy.start_cooldown(None)


def test_starts_cooldown_by_status():
    assert decide_policy.starts_cooldown(402) and decide_policy.starts_cooldown(403)
    assert decide_policy.starts_cooldown(402, only_402=True)
    assert not decide_policy.starts_cooldown(403, only_402=True)
    assert not decide_policy.starts_cooldown(None)


# ── The skill seam: ask_typed and the Projects choices ───────────────────────


class _Answer:
    def __init__(self, choice: str, confidence: float) -> None:
        self.id, self.choice, self.confidence = "a", choice, confidence


def test_ask_typed_writes_the_decide_lines_and_honours_the_cool_down(monkeypatch):
    from acb_skills import decide_tools

    log = Log()
    monkeypatch.setattr(decide_tools, "_log", log)
    asked: list[str] = []

    async def _answers(context, items, kind):
        asked.append(kind)
        return [_Answer("yes", 0.95)]

    monkeypatch.setattr(decide_tools, "_answers_of", _answers)
    out = asyncio.run(decide_tools.ask_typed("ctx", ["i"], purpose="projects.name"))
    assert [a.choice for a in out] == ["yes"]
    done = log.fields("decide.decided")
    assert done and done[0]["decide_feature"] == "projects.name"

    # A Projects verdict in the gateway seam pauses the skill seam as well.
    decide_policy.start_cooldown(ORG, area="projects.")
    out = asyncio.run(decide_tools.ask_typed("ctx", ["i"], purpose="projects.name"))
    assert out is None and asked == ["projects.name"], "no second call in the cool-down"
    gone = log.fields("decide.unavailable")
    assert gone[-1]["decide_reason"] == "cooldown"
    assert gone[-1]["decide_feature"] == "projects.name"


def _cooling_features() -> list[str]:
    """The probe features that are cooling for ORG. Empty means none."""
    return [f for f in ("email.rule_match", "my_day.triage", "crm.map_role", "projects.align")
            if df.cooling(f)]


def test_a_402_inside_ask_typed_pauses_projects_only(monkeypatch):
    from acb_skills import decide_tools

    monkeypatch.setattr(decide_tools, "_log", Log())
    monkeypatch.setattr(
        acb_llm, "decide",
        FakeDecide(raises=acb_llm.DecideUnavailable("insufficient_credits", status=402)))

    async def _answers(context, items, kind):
        await decide_tools._one_decide(context, {"q": object()}, {}, 1, kind)
        raise decide_tools.system_one.SystemOneUnavailable("down") \
            if hasattr(decide_tools, "system_one") else RuntimeError("down")

    monkeypatch.setattr(decide_tools, "_answers_of", _answers)
    assert asyncio.run(decide_tools.ask_typed("c", ["i"], purpose="projects.twin")) is None
    assert _cooling_features() == ["projects.align"]
    # The marker is released after the call.
    assert decide_tools._TYPED_CALL.get() is False


def test_a_403_inside_ask_typed_starts_nothing(monkeypatch):
    from acb_skills import decide_tools

    monkeypatch.setattr(decide_tools, "_log", Log())
    monkeypatch.setattr(
        acb_llm, "decide",
        FakeDecide(raises=acb_llm.DecideUnavailable("forbidden", status=403)))

    async def _answers(context, items, kind):
        await decide_tools._one_decide(context, {"q": object()}, {}, 1, kind)
        return []

    monkeypatch.setattr(decide_tools, "_answers_of", _answers)
    asyncio.run(decide_tools.ask_typed("c", ["i"], purpose="projects.twin"))
    assert _cooling_features() == []


@pytest.mark.parametrize("status", [402, 403])
def test_the_general_decide_tool_path_starts_no_cool_down(monkeypatch, status):
    from acb_skills import decide_tools

    monkeypatch.setattr(decide_tools, "_log", Log())
    monkeypatch.setattr(
        acb_llm, "decide",
        FakeDecide(raises=acb_llm.DecideUnavailable("refused", status=status)))
    out = asyncio.run(decide_tools._one_decide("ctx", {"q": object()}, {}, 1, "decide"))
    assert out is None
    assert _cooling_features() == [], "as before DEC-1, the tool starts none"


def test_an_email_403_does_not_pause_ask_typed_and_still_pauses_email(monkeypatch):
    from acb_skills import decide_tools

    _fake(monkeypatch, raises=acb_llm.DecideUnavailable("forbidden", status=403))
    _set(monkeypatch, "email.rule_match=on", decide_feature_orgs=ORG)
    asyncio.run(df.ask("email.rule_match", build=lambda: ({}, _questions(1)), read=_read))
    assert df.cooling(), "email's own 403 cool-down works as before"
    assert df.cooling("crm.map_role") and df.cooling("my_day.triage")  # the shared table
    assert not df.cooling("projects.align")

    monkeypatch.setattr(decide_tools, "_log", Log())
    asked: list[str] = []

    async def _answers(context, items, kind):
        asked.append(kind)
        return [_Answer("yes", 0.95)]

    monkeypatch.setattr(decide_tools, "_answers_of", _answers)
    out = asyncio.run(decide_tools.ask_typed("ctx", ["i"], purpose="projects.name"))
    assert out is not None and asked == ["projects.name"]


def test_a_projects_cool_down_does_not_pause_email_crm_or_my_day(monkeypatch):
    fake = _fake(monkeypatch)
    decide_policy.start_cooldown(ORG, area="projects.")
    _set(monkeypatch, "email.rule_match=on,crm.note_labels=on,my_day.day_plan=on",
         decide_feature_orgs=ORG, crm_decide_orgs=ORG, my_day_decide_orgs=ORG)
    for feature in ("email.rule_match", "crm.note_labels", "my_day.day_plan"):
        out = asyncio.run(df.ask(feature, build=lambda: ({}, _questions(1)), read=_read))
        assert out == ["q00"], feature
    assert len(fake.calls) == 3


def test_an_email_402_does_not_pause_a_projects_ask(monkeypatch):
    fake = _fake(monkeypatch)
    decide_policy.start_cooldown(ORG)
    _set(monkeypatch, "projects.align=on", projects_decide_orgs=ORG)
    out = asyncio.run(df.ask("projects.align", build=lambda: ({}, _questions(1)), read=_read))
    assert out == ["q00"] and len(fake.calls) == 1


def test_the_projects_name_and_twin_checks_use_that_seam(monkeypatch):
    from acb_skills import decide_tools
    from skill_projects import choices

    log = Log()
    monkeypatch.setattr(decide_tools, "_log", log)
    asked: list[str] = []

    async def _answers(context, items, kind):
        asked.append(kind)
        return [_Answer("yes", 0.95) for _ in items]

    monkeypatch.setattr(decide_tools, "_answers_of", _answers)
    for purpose in ("projects.name", "projects.twin"):
        out = asyncio.run(choices._ask({"wanted": "x"}, ["item"], purpose))
        assert out is not None
    assert {f["decide_feature"] for f in log.fields("decide.decided")} == {
        "projects.name", "projects.twin"}

    decide_policy.start_cooldown(ORG, area="projects.")
    for purpose in ("projects.name", "projects.twin"):
        assert asyncio.run(choices._ask({"wanted": "x"}, ["item"], purpose)) is None
    assert asked == ["projects.name", "projects.twin"], "the cool-down made no call"
    assert [f["decide_reason"] for f in log.fields("decide.unavailable")] == [
        "cooldown", "cooldown"]
