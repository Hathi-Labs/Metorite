"""The ``decide`` platform tool — WS-31 CP-13d (§6A.14 Done-when row 13).

Spec: ``project-docs/specs/customer_console.md`` §6A.14 "CP-13d". D75.

Hermetic. The tool is a thin shape over ``acb_llm.decide``, and no SQL runs on
this path, so R8 has nothing to bind. Two layers are faked:

* the facade itself (``acb_llm.decide``), to pin what the tool SENDS and how it
  FORMATS each answer;
* the Console client's HTTP factory (``console_resolve._new_http_client``), to
  prove that the flag off and a deployment-key box send no request at all.

🔴 **The tool sends NO member.** That is the R11 finding in §6A.14 CP-13d: a
run's ``user_email`` can come from a caller's payload, so the tool must never
let it select a tenant. The test that pins it is
``test_the_tool_sends_agent_and_run_id_from_the_run_context_and_NO_member``.
"""
from __future__ import annotations

import inspect
import json
from types import MappingProxyType
from typing import Any

import acb_llm
import httpx
import pytest
import structlog
from acb_auth import console_resolve
from acb_common import bind_run_context, clear_run_context
from acb_common.settings import get_settings
from acb_llm import (
    BooleanAnswer,
    BooleanQuestion,
    ChoiceAnswer,
    ChoiceQuestion,
    DecideRequestInvalid,
    DecideUnavailable,
    Decision,
    ScoreAnswer,
    ScoreQuestion,
)
from acb_skills import decide_tools
from acb_skills.decide_tools import MALFORMED, UNAVAILABLE, decide

CONSOLE_URL = "https://console.metorite.test"
ORG_KEY = "cc_live_abcd_secretsecretsecret"
DEPLOYMENT_KEY = "cc_depl_wxyz_anothersecretvalue"
#: Tenant text. It must never reach a log record or a returned string.
SECRET_CONTEXT = "Invoice 4471 for Acme Corp, overdue since March"
SECRET_OPTION = "Project Nightjar"


@pytest.fixture(autouse=True)
def _fresh_settings():
    get_settings.cache_clear()
    clear_run_context()
    yield
    clear_run_context()
    get_settings.cache_clear()


class FakeFacade:
    """Stands in for ``acb_llm.decide`` and records every call."""

    def __init__(self, answer: Any = None, raises: Exception | None = None):
        self.calls: list[dict[str, Any]] = []
        self._answer = answer
        self._raises = raises

    async def __call__(self, state, questions, **kwargs):
        self.calls.append({"state": state, "questions": dict(questions), **kwargs})
        if self._raises is not None:
            raise self._raises
        return Decision(answers=MappingProxyType({"q": self._answer}))


def _facade(monkeypatch, **kw) -> FakeFacade:
    fake = FakeFacade(**kw)
    monkeypatch.setattr(acb_llm, "decide", fake)
    return fake


class FakeConsole:
    """An HTTP factory that records every request that leaves the box."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(
            200,
            json={"answers": {"q": {"type": "boolean", "probability": 0.9}}},
        )

    def client(self, timeout: Any = None):
        return httpx.AsyncClient(transport=httpx.MockTransport(self._handle))


def _box(monkeypatch, *, enabled: bool, deployment: bool = False) -> FakeConsole:
    fake = FakeConsole()
    monkeypatch.setenv("DECIDE_ENABLED", "true" if enabled else "false")
    monkeypatch.setenv("CUSTOMER_CONSOLE_URL", CONSOLE_URL)
    monkeypatch.setenv("CUSTOMER_CONSOLE_ORG_KEY", "" if deployment else ORG_KEY)
    monkeypatch.setenv("CUSTOMER_CONSOLE_DEPLOYMENT_KEY", DEPLOYMENT_KEY)
    monkeypatch.setenv(
        "CUSTOMER_CONSOLE_ROUTER_USES_DEPLOYMENT_KEY",
        "true" if deployment else "false",
    )
    get_settings.cache_clear()
    monkeypatch.setattr(console_resolve, "_new_http_client", fake.client)
    return fake


# ── The switch and the box ──────────────────────────────────────────────────


async def test_flag_off_returns_the_unavailable_text_and_makes_NO_call(monkeypatch):
    """🔴 OWNER-ONLY flag. Off must mean no byte leaves the box."""
    fake = _box(monkeypatch, enabled=False)
    spied: list[Any] = []

    async def _spy(*args, **kwargs):  # pragma: no cover - must not run
        spied.append((args, kwargs))
        return 200, {}

    monkeypatch.setattr(console_resolve, "decide_on_console", _spy)

    out = await decide("Is this urgent?", SECRET_CONTEXT)
    assert out == UNAVAILABLE
    assert spied == [], "the flag is off, and the Console client was called"
    assert fake.requests == [], "the flag is off, and a request left the box"


async def test_a_deployment_key_box_is_unavailable_with_NO_request(monkeypatch):
    """The tool sends no member, so the deployment arm refuses locally."""
    fake = _box(monkeypatch, enabled=True, deployment=True)
    # Even a member bound for the run must not be sent (the R11 finding).
    from acb_skills.memory_tools import _set_memory_user_id

    _set_memory_user_id("someone@example.com")
    out = await decide("Is this urgent?", SECRET_CONTEXT)
    assert out == UNAVAILABLE
    assert fake.requests == [], "a deployment box asked the Console anyway"


async def test_the_org_arm_serves_with_no_member_header(monkeypatch):
    fake = _box(monkeypatch, enabled=True)
    from acb_skills.memory_tools import _set_memory_user_id

    _set_memory_user_id("someone@example.com")
    out = await decide("Is this urgent?", SECRET_CONTEXT)
    assert out == "yes (p=0.90)"
    assert len(fake.requests) == 1
    sent = fake.requests[0]
    assert sent.headers["authorization"] == f"Bearer {ORG_KEY}"
    assert "x-cc-member" not in sent.headers
    assert json.loads(sent.content)["state"] == SECRET_CONTEXT


# ── Formatting ──────────────────────────────────────────────────────────────


async def test_a_yes_no_call_formats_the_probability(monkeypatch):
    fake = _facade(monkeypatch, answer=BooleanAnswer(probability=0.93))
    assert await decide("Is this urgent?", "ctx") == "yes (p=0.93)"
    question = fake.calls[0]["questions"]["q"]
    assert isinstance(question, BooleanQuestion)
    assert question.instructions == "Is this urgent?"
    assert fake.calls[0]["state"] == "ctx"


async def test_a_low_yes_probability_reads_as_no(monkeypatch):
    _facade(monkeypatch, answer=BooleanAnswer(probability=0.12))
    assert await decide("Is this urgent?", "ctx") == "no (p=0.88)"


async def test_a_choice_call_names_the_pick_and_the_runner_up(monkeypatch):
    fake = _facade(
        monkeypatch,
        answer=ChoiceAnswer(
            choice="beta",
            probabilities=MappingProxyType({"alpha": 0.07, "beta": 0.81, "gamma": 0.12}),
            confidence=0.81,
        ),
    )
    out = await decide(
        "Which project?", "ctx", kind="choice", options="alpha\nbeta\ngamma"
    )
    assert out == "beta (confidence 0.81; next: gamma 0.12)"
    question = fake.calls[0]["questions"]["q"]
    assert isinstance(question, ChoiceQuestion)
    assert list(question.criteria) == ["alpha", "beta", "gamma"]


async def test_options_may_be_a_json_list_or_pipe_separated(monkeypatch):
    fake = _facade(
        monkeypatch,
        answer=ChoiceAnswer(
            choice="a", probabilities=MappingProxyType({"a": 1.0}), confidence=None
        ),
    )
    await decide("Pick", "ctx", kind="choice", options='["a", "b"]')
    await decide("Pick", "ctx", kind="choice", options="a | b | a")
    assert [list(c["questions"]["q"].criteria) for c in fake.calls] == [
        ["a", "b"],
        ["a", "b"],
    ]


async def test_a_score_call_names_the_level_and_confidence(monkeypatch):
    fake = _facade(
        monkeypatch,
        answer=ScoreAnswer(
            score="high",
            probabilities=MappingProxyType({"low": 0.1, "mid": 0.26, "high": 0.64}),
            confidence=0.64,
        ),
    )
    out = await decide("How severe?", "ctx", kind="score", options="low\nmid\nhigh")
    assert out == "high (confidence 0.64)"
    question = fake.calls[0]["questions"]["q"]
    assert isinstance(question, ScoreQuestion)
    assert list(question.criteria) == ["low", "mid", "high"]


# ── Light validation, before any call ───────────────────────────────────────


@pytest.mark.parametrize(
    ("kind", "options"),
    [
        ("maybe", ""),
        ("choice", "only-one"),
        ("score", "one"),
        ("score", "\n".join(str(i) for i in range(11))),
    ],
)
async def test_a_bad_shape_is_refused_with_no_call(monkeypatch, kind, options):
    fake = _facade(monkeypatch, answer=None)
    out = await decide("Q?", "ctx", kind=kind, options=options)
    assert out.startswith("decide:")
    assert fake.calls == []


# ── Outcomes ────────────────────────────────────────────────────────────────


async def test_decide_unavailable_returns_the_fixed_text(monkeypatch):
    _facade(monkeypatch, raises=DecideUnavailable("tier_unknown", status=400))
    assert await decide("Q?", "ctx") == UNAVAILABLE


async def test_request_invalid_returns_malformed_with_no_tenant_text(monkeypatch):
    detail = {"reason": "invalid_request", "error": f"criterion {SECRET_OPTION!r} too long"}
    _facade(monkeypatch, raises=DecideRequestInvalid(400, detail))
    with structlog.testing.capture_logs() as caps:
        out = await decide(
            "Q?", SECRET_CONTEXT, kind="choice", options=f"{SECRET_OPTION}\nother"
        )
    assert out == MALFORMED
    assert SECRET_OPTION not in out and SECRET_CONTEXT not in out
    records = [c for c in caps if c.get("event") == "decide_tool.request_invalid"]
    assert records, "a malformed request must be logged"
    assert records[0]["log_level"] == "warning"
    assert records[0]["decide_reason"] == "invalid_request"
    for record in caps:
        blob = repr(record)
        assert SECRET_OPTION not in blob, "tenant text reached a log record"
        assert SECRET_CONTEXT not in blob, "tenant context reached a log record"


async def test_any_other_error_never_reaches_the_agent_loop(monkeypatch):
    _facade(monkeypatch, raises=RuntimeError(f"boom {SECRET_CONTEXT}"))
    with structlog.testing.capture_logs() as caps:
        out = await decide("Q?", SECRET_CONTEXT)
    assert out == UNAVAILABLE
    assert all(SECRET_CONTEXT not in repr(c) for c in caps)


# ── Identity ────────────────────────────────────────────────────────────────


async def test_the_tool_sends_agent_and_run_id_from_the_run_context_and_NO_member(
    monkeypatch,
):
    fake = _facade(monkeypatch, answer=BooleanAnswer(probability=0.5))
    from acb_skills.memory_tools import _set_memory_user_id

    _set_memory_user_id("claimed@example.com")
    bind_run_context(run_id="run-42", agent="orchestrator", user="claimed@example.com")
    await decide("Q?", "ctx")
    call = fake.calls[0]
    assert call["agent"] == "orchestrator"
    assert call["run_id"] == "run-42"
    assert call["member"] is None, "a member reached the facade (R11)"
    assert call["member_proven"] is False


def test_the_schema_has_no_member_field() -> None:
    """The member can never come from a tool argument."""
    params = set(inspect.signature(decide).parameters)
    assert params == {"question", "context", "kind", "options"}


def test_the_tool_never_imports_the_console_client() -> None:
    """``acb_llm.decide`` is the one seam. The tool must not bypass it."""
    source = inspect.getsource(decide_tools)
    assert "console_resolve" not in source
    assert "decide_on_console" not in source


# ── The flag gates the tool AND the prompt (review P2, 2026-09-24) ─────────
#
# `_wants` gates an addendum section on the SCOPE, and an unscoped agent
# renders every section. So a gate on injection alone would still advertise
# a tool the agent does not hold. `decide_tool_enabled` is the one switch, and
# both halves ask it.

#: A scoped agent: the resolved scope names `decide`, because the floor does.
_SCOPED = frozenset({"decide", "web_search", "fetch_page", "write_artifact"})


def _flag(monkeypatch, on: bool) -> None:
    import orchestrator._tool_injection as ti

    monkeypatch.setenv("DECIDE_ENABLED", "true" if on else "false")
    get_settings.cache_clear()
    ti._build_injected_tools_addendum.cache_clear()


def _injected_names() -> set[str]:
    import orchestrator._tool_injection as ti

    return {ti._tool_name(t) for t in ti._collect_injectable_platform_tools()}


def _addenda() -> list[str]:
    import orchestrator._tool_injection as ti

    out = []
    for sub in (False, True):
        for scope in (None, _SCOPED):
            ti._build_injected_tools_addendum.cache_clear()
            out.append(
                ti._build_injected_tools_addendum(
                    is_sub_agent=sub, effective_scope=scope
                )
            )
    ti._build_injected_tools_addendum.cache_clear()
    return out


def test_flag_off_injects_no_decide_and_the_prompt_never_names_it(monkeypatch):
    _flag(monkeypatch, on=False)
    assert "decide" not in _injected_names()
    for text in _addenda():
        assert "Fast decisions" not in text
        assert "decide(" not in text, "the prompt names a tool it did not inject"


def test_flag_on_injects_decide_and_the_prompt_names_it(monkeypatch):
    _flag(monkeypatch, on=True)
    assert "decide" in _injected_names()
    full_unscoped, full_scoped, compact_unscoped, compact_scoped = _addenda()
    assert "Fast decisions" in full_unscoped
    assert "Fast decisions" in full_scoped
    assert "decide(question,context" in compact_unscoped
    assert "decide(question,context" in compact_scoped


def test_a_broken_settings_read_reads_as_off(monkeypatch):
    import acb_common

    def _boom():
        raise RuntimeError("settings unreadable")

    monkeypatch.setattr(acb_common, "get_settings", _boom)
    assert decide_tools.decide_tool_enabled() is False


async def test_an_import_failure_is_logged_not_silent(monkeypatch):
    """A packaging defect must not read as "the flag is off"."""
    import builtins

    real_import = builtins.__import__

    def _no_llm(name, *args, **kwargs):
        if name == "acb_llm":
            raise ImportError("acb_llm is missing")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_llm)
    with structlog.testing.capture_logs() as caps:
        out = await decide("Q?", SECRET_CONTEXT)
    assert out == UNAVAILABLE
    records = [c for c in caps if c.get("event") == "decide_tool.import_failed"]
    assert records and records[0]["log_level"] == "warning"
    assert all(SECRET_CONTEXT not in repr(c) for c in caps)
