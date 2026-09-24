"""The ``decide`` tenant facade — WS-31 CP-13c (§6A.14 Done-when row 12).

Spec: ``project-docs/specs/customer_console.md`` §6A.14 "CP-13c · The tenant
client". D75.

Hermetic by design. ``acb_llm.decide`` is pure mapping over
``console_resolve.decide_on_console``, and that client is exercised here
through a real ``httpx.MockTransport``. No SQL runs on this path, so R8 has
nothing to bind. The Console door itself is R8-fenced in
``test_customer_console_decide.py``.

🔴 **The load-bearing test is the first one.** ``DECIDE_ENABLED`` is the one
switch between tenant content and a third party, and OFF must mean no call.
"""
from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from acb_auth import console_resolve
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
    decide,
)

CONSOLE_URL = "https://console.metorite.test"
ORG_KEY = "cc_live_abcd_secretsecretsecret"
DEPLOYMENT_KEY = "cc_depl_wxyz_anothersecretvalue"
MEMBER = "someone@example.com"

QUESTIONS = {
    "urgent": BooleanQuestion("Is this message urgent?"),
    "project": ChoiceQuestion(
        "Which project does this belong to?",
        {"alpha": "The alpha launch", "beta": "The beta rollout"},
    ),
    "effort": ScoreQuestion(
        "How much effort does this take?",
        {"low": "Minutes", "mid": "Hours", "high": "Days"},
    ),
}

ANSWERS = {
    "tier": "tier-decide",
    "answers": {
        "urgent": {"type": "boolean", "probability": 0.91},
        "project": {
            "type": "choice",
            "choice": "beta",
            "probabilities": {"alpha": 0.2, "beta": 0.8},
            "confidence": 0.77,
        },
        "effort": {
            "type": "score",
            "score": "mid",
            "probabilities": {"low": 0.1, "mid": 0.7, "high": 0.2},
            "confidence": 0.64,
        },
    },
    "usage": {"input_tokens": 120, "output_tokens": 0},
    "request_id": "req_123",
}


class FakeConsole:
    """Records every request and answers with whatever the test set."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.timeouts: list[Any] = []
        self._status = 200
        self._body: Any = ANSWERS
        self._raise: Exception | None = None

    def answers(self, status: int, body: Any = None) -> None:
        self._status = status
        self._body = {} if body is None else body

    def explodes(self, exc: Exception) -> None:
        self._raise = exc

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self._raise is not None:
            raise self._raise
        return httpx.Response(self._status, json=self._body)

    def client(self, timeout: Any = None):
        self.timeouts.append(timeout)
        return httpx.AsyncClient(
            transport=httpx.MockTransport(self._handle), timeout=5.0
        )


def _box(
    monkeypatch,
    *,
    enabled: bool = True,
    url: str = CONSOLE_URL,
    org: str = ORG_KEY,
    depl: str = DEPLOYMENT_KEY,
    depl_flag: bool = False,
) -> FakeConsole:
    fake = FakeConsole()
    monkeypatch.setenv("DECIDE_ENABLED", "true" if enabled else "false")
    monkeypatch.setenv("CUSTOMER_CONSOLE_URL", url)
    monkeypatch.setenv("CUSTOMER_CONSOLE_ORG_KEY", org)
    monkeypatch.setenv("CUSTOMER_CONSOLE_DEPLOYMENT_KEY", depl)
    monkeypatch.setenv(
        "CUSTOMER_CONSOLE_ROUTER_USES_DEPLOYMENT_KEY", "true" if depl_flag else "false"
    )
    get_settings.cache_clear()
    monkeypatch.setattr(console_resolve, "_new_http_client", fake.client)
    return fake


@pytest.fixture(autouse=True)
def _fresh_settings():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# ── The switch ──────────────────────────────────────────────────────────────


def test_the_switch_defaults_off():
    """🔴 OWNER-ONLY. A box that never set it must not send a byte."""
    from acb_common.settings import Settings

    assert Settings.model_fields["decide_enabled"].default is False


async def test_switch_off_raises_disabled_and_makes_NO_call(monkeypatch):
    fake = _box(monkeypatch, enabled=False)
    calls: list[Any] = []

    async def _spy(*args, **kwargs):  # pragma: no cover - must not run
        calls.append((args, kwargs))
        return 200, ANSWERS

    monkeypatch.setattr(console_resolve, "decide_on_console", _spy)

    with pytest.raises(DecideUnavailable) as err:
        await decide("state", QUESTIONS, member=MEMBER)
    assert err.value.reason == "disabled"
    assert calls == [], "the switch is off, and the client was still called"
    assert fake.requests == [], "the switch is off, and a request left the box"


# ── Wiring and the credential ───────────────────────────────────────────────


async def test_an_unwired_box_is_unavailable_with_no_request(monkeypatch):
    fake = _box(monkeypatch, url="", org="")
    with pytest.raises(DecideUnavailable, match="unwired"):
        await decide("state", QUESTIONS, member=MEMBER)
    assert fake.requests == []


async def test_a_deployment_key_with_no_member_is_refused_LOCALLY(monkeypatch):
    fake = _box(monkeypatch, org="", depl_flag=True)
    with pytest.raises(DecideUnavailable, match="member"):
        await decide("state", QUESTIONS, member=None)
    assert fake.requests == [], "it asked the Console a question it had to refuse"


async def test_the_deployment_arm_sends_the_deployment_key_and_the_member(monkeypatch):
    fake = _box(monkeypatch, org="", depl_flag=True)
    await decide("state", QUESTIONS, member=MEMBER, member_proven=True)
    sent = fake.requests[0]
    assert sent.headers["authorization"] == f"Bearer {DEPLOYMENT_KEY}"
    assert sent.headers["x-cc-member"] == MEMBER
    assert sent.headers["x-cc-member-proven"] == "1"


async def test_the_org_arm_sends_the_org_key(monkeypatch):
    fake = _box(monkeypatch)
    await decide("state", QUESTIONS, agent="triage", run_id="run-1")
    sent = fake.requests[0]
    assert sent.headers["authorization"] == f"Bearer {ORG_KEY}"
    assert sent.headers["x-cc-agent"] == "triage"
    assert sent.headers["x-cc-run"] == "run-1"
    assert "x-cc-member" not in sent.headers


# ── The wire body ───────────────────────────────────────────────────────────


async def test_the_body_is_our_wire_shape_on_the_decide_door(monkeypatch):
    fake = _box(monkeypatch)
    await decide({"subject": "Server down"}, QUESTIONS)
    sent = fake.requests[0]
    assert str(sent.url) == f"{CONSOLE_URL}/v1/decide"
    body = json.loads(sent.content)
    assert body == {
        "tier": "tier-decide",
        "state": {"subject": "Server down"},
        "questions": {
            "urgent": {
                "type": "boolean",
                "instructions": "Is this message urgent?",
                "criteria": {},
            },
            "project": {
                "type": "choice",
                "instructions": "Which project does this belong to?",
                "criteria": {"alpha": "The alpha launch", "beta": "The beta rollout"},
            },
            "effort": {
                "type": "score",
                "instructions": "How much effort does this take?",
                "criteria": {"low": "Minutes", "mid": "Hours", "high": "Days"},
            },
        },
    }
    assert "model" not in body, "the caller names a tier, never a model (D32.7)"


async def test_the_decide_client_uses_its_own_short_timeout(monkeypatch):
    fake = _box(monkeypatch)
    await decide("state", QUESTIONS)
    assert fake.timeouts == [console_resolve._DECIDE_TIMEOUT_SECONDS]
    assert (
        console_resolve._DECIDE_TIMEOUT_SECONDS
        < console_resolve._ROUTER_TIMEOUT_SECONDS
    )


async def test_a_question_that_is_not_typed_is_a_TypeError_with_no_call(monkeypatch):
    fake = _box(monkeypatch)
    with pytest.raises(TypeError):
        await decide("state", {"q": {"type": "boolean", "instructions": "x"}})  # type: ignore[dict-item]
    assert fake.requests == []


# ── Typed answers ───────────────────────────────────────────────────────────


async def test_typed_answers_come_back_and_never_a_dict(monkeypatch):
    _box(monkeypatch)
    result = await decide("state", QUESTIONS)
    assert isinstance(result, Decision)
    assert result.request_id == "req_123"

    urgent = result["urgent"]
    assert isinstance(urgent, BooleanAnswer)
    assert urgent.probability == pytest.approx(0.91)

    project = result["project"]
    assert isinstance(project, ChoiceAnswer)
    assert project.choice == "beta"
    assert dict(project.probabilities) == {"alpha": 0.2, "beta": 0.8}
    assert project.confidence == pytest.approx(0.77)

    effort = result["effort"]
    assert isinstance(effort, ScoreAnswer)
    assert effort.score == "mid"
    assert effort.confidence == pytest.approx(0.64)

    for answer in result.answers.values():
        assert not isinstance(answer, dict)
    with pytest.raises(TypeError):
        result.answers["urgent"] = BooleanAnswer(0.0)  # type: ignore[index]


async def test_a_200_we_cannot_read_is_unavailable(monkeypatch):
    fake = _box(monkeypatch)
    fake.answers(200, {"answers": {"urgent": {"type": "boolean"}}})
    with pytest.raises(DecideUnavailable, match="unreadable"):
        await decide("state", {"urgent": QUESTIONS["urgent"]})


# ── Outages: Unavailable, exactly one call ──────────────────────────────────


@pytest.mark.parametrize("status", [500, 502, 503, 504, 501, 401, 408, 429])
async def test_an_outage_status_is_unavailable_with_exactly_one_call(monkeypatch, status):
    fake = _box(monkeypatch)
    fake.answers(status, {"detail": "no"})
    with pytest.raises(DecideUnavailable) as err:
        await decide("state", QUESTIONS)
    assert err.value.reason == f"HTTP {status}"
    assert len(fake.requests) == 1, "a decision is metered, so there is no retry"


async def test_a_transport_failure_is_unavailable_with_exactly_one_call(monkeypatch):
    fake = _box(monkeypatch)
    fake.explodes(httpx.ConnectError("refused"))
    with pytest.raises(DecideUnavailable, match="refused"):
        await decide("state", QUESTIONS)
    assert len(fake.requests) == 1


# ── Verdicts: 402 and 403 degrade, 400 is loud ──────────────────────────────


@pytest.mark.parametrize(
    ("status", "reason"), [(402, "insufficient_credits"), (403, "forbidden")]
)
async def test_a_402_or_403_verdict_degrades_with_the_reason_named(
    monkeypatch, status, reason
):
    fake = _box(monkeypatch)
    fake.answers(status, {"detail": "refused"})
    with pytest.raises(DecideUnavailable) as err:
        await decide("state", QUESTIONS)
    assert err.value.reason == reason
    assert err.value.status == status
    assert len(fake.requests) == 1


@pytest.mark.parametrize("status", [400, 422])
async def test_a_refused_request_is_DecideRequestInvalid_and_not_a_fallback(
    monkeypatch, status
):
    fake = _box(monkeypatch)
    detail = "a decide request takes at most 16 questions, and this one has 17"
    fake.answers(status, {"detail": detail})
    with pytest.raises(DecideRequestInvalid) as err:
        await decide("state", QUESTIONS)
    assert not isinstance(err.value, DecideUnavailable)
    assert err.value.status == status
    assert err.value.detail == detail
    assert len(fake.requests) == 1


async def test_an_unexpected_status_degrades(monkeypatch):
    """A Console without the door answers 404. That is not a caller bug."""
    fake = _box(monkeypatch)
    fake.answers(404, {"detail": "Not Found"})
    with pytest.raises(DecideUnavailable, match="HTTP 404"):
        await decide("state", QUESTIONS)


# ── The boundary ────────────────────────────────────────────────────────────


def test_the_facade_does_not_import_the_console_service():
    """The Console stays the one validator of clause 13, and the tenant
    deployable must not need ``customer_console`` (the boundary fence)."""
    from pathlib import Path

    src = (
        Path(__file__).resolve().parents[2]
        / "packages/acb_llm/acb_llm/decide.py"
    ).read_text(encoding="utf-8")
    assert "import customer_console" not in src
    assert "from customer_console" not in src
    assert "MAX_QUESTIONS" not in src, "clause 13's limits live on the Console only"
