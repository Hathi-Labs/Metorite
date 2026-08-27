"""The AI Router client — WS-31 CP-11 slice 2.

`chat_completion_on_console` is the tenant box's path to the Console's Router.
It ships DARK: nothing calls it until slice 3 puts `v1_compat.py` behind
`ROUTER_SERVING_ENABLED`.

⚠️ **The load-bearing test here is the credential one.** This arm presents
`CUSTOMER_CONSOLE_ORG_KEY` (`cc_live_`) while every other arm in
`console_resolve.py` presents `CUSTOMER_CONSOLE_DEPLOYMENT_KEY` (`cc_depl_`).
`settings.py` records why that distinction exists: *"reusing one name for two
credentials is how a box presents the wrong one and gets a 401 nobody can
explain."* Copying the seat arm and leaving its key read in place is the single
most likely way to build this wrong, and it would fail in production rather than
here — so it is asserted rather than assumed.

⚠️ **The second is the 501 carve-out.** The Router answers 501 for a streaming
request because CP-4b is unbuilt. 501 is a 5xx, and the outage rule this module
uses everywhere else would swallow it — reporting an unreachable Console to
somebody whose real problem is that they asked for a stream.
"""
from __future__ import annotations

from typing import Any

import httpx
import pytest

from acb_auth import console_resolve
from acb_common.settings import get_settings

CONSOLE_URL = "https://console.metorite.test"
ORG_KEY = "cc_live_abcd_secretsecretsecret"
DEPLOYMENT_KEY = "cc_depl_wxyz_anothersecretvalue"


class FakeRouter:
    """Records every request and answers with whatever the test set."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.timeouts: list[Any] = []
        self._status = 200
        self._body: Any = {"choices": [{"message": {"content": "hi"}}]}
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
        if isinstance(self._body, (dict, list)):
            return httpx.Response(self._status, json=self._body)
        return httpx.Response(self._status, text=str(self._body))

    def client(self, timeout: Any = None):
        # Records the timeout the production code asked for, so the "a
        # completion is not a sign-in" decision is checked rather than trusted.
        self.timeouts.append(timeout)
        return httpx.AsyncClient(
            transport=httpx.MockTransport(self._handle), timeout=5.0
        )


@pytest.fixture
def router(monkeypatch):
    """A box wired for the Router: the URL and the ORG key, both set."""
    fake = FakeRouter()
    monkeypatch.setenv("CUSTOMER_CONSOLE_URL", CONSOLE_URL)
    monkeypatch.setenv("CUSTOMER_CONSOLE_ORG_KEY", ORG_KEY)
    monkeypatch.setenv("CUSTOMER_CONSOLE_DEPLOYMENT_KEY", DEPLOYMENT_KEY)
    get_settings.cache_clear()
    monkeypatch.setattr(console_resolve, "_new_http_client", fake.client)
    try:
        yield fake
    finally:
        get_settings.cache_clear()


PAYLOAD = {"model": "tier-balanced", "messages": [{"role": "user", "content": "hi"}]}


# ── Wiring ──────────────────────────────────────────────────────────────────

def test_router_is_wired_needs_both_the_url_and_the_org_key(monkeypatch):
    for url, key, expected in (
        (CONSOLE_URL, ORG_KEY, True),
        (CONSOLE_URL, "", False),
        ("", ORG_KEY, False),
        ("", "", False),
        ("   ", ORG_KEY, False),  # whitespace is not configuration
    ):
        monkeypatch.setenv("CUSTOMER_CONSOLE_URL", url)
        monkeypatch.setenv("CUSTOMER_CONSOLE_ORG_KEY", key)
        get_settings.cache_clear()
        assert console_resolve.router_is_wired() is expected
    get_settings.cache_clear()


def test_the_deployment_key_alone_does_not_arm_the_router(monkeypatch):
    """⚠️ The two capabilities are separate on purpose.

    A box wired for SIGN-IN resolution is not thereby wired for AI. One
    predicate for both would arm the Router the moment somebody configured the
    thing that lets people log in.
    """
    monkeypatch.setenv("CUSTOMER_CONSOLE_URL", CONSOLE_URL)
    monkeypatch.setenv("CUSTOMER_CONSOLE_DEPLOYMENT_KEY", DEPLOYMENT_KEY)
    monkeypatch.setenv("CUSTOMER_CONSOLE_ORG_KEY", "")
    get_settings.cache_clear()
    assert console_resolve.is_wired() is True
    assert console_resolve.router_is_wired() is False
    get_settings.cache_clear()


async def test_an_unwired_box_refuses_without_making_a_request(monkeypatch):
    fake = FakeRouter()
    monkeypatch.setenv("CUSTOMER_CONSOLE_URL", "")
    monkeypatch.setenv("CUSTOMER_CONSOLE_ORG_KEY", "")
    get_settings.cache_clear()
    monkeypatch.setattr(console_resolve, "_new_http_client", fake.client)
    with pytest.raises(console_resolve.ConsoleRouterUnavailable, match="unwired"):
        await console_resolve.chat_completion_on_console(PAYLOAD)
    assert fake.requests == [], "an unwired box must not reach the network"
    get_settings.cache_clear()


# ── The credential ──────────────────────────────────────────────────────────

async def test_the_router_presents_the_ORG_key_and_never_the_deployment_key(router):
    """⚠️ **The one that matters.** See this module's docstring.

    Both keys are set on the box, so a wrong read would still produce a
    plausible-looking `Bearer cc_depl_…` and a 401 in production that nobody
    could explain from the logs.
    """
    await console_resolve.chat_completion_on_console(PAYLOAD)
    sent = router.requests[0].headers["authorization"]
    assert sent == f"Bearer {ORG_KEY}"
    assert DEPLOYMENT_KEY not in sent


async def test_it_posts_to_the_router_endpoint_on_the_configured_console(router):
    await console_resolve.chat_completion_on_console(PAYLOAD)
    assert str(router.requests[0].url) == f"{CONSOLE_URL}/v1/chat/completions"


async def test_a_trailing_slash_on_the_url_does_not_double_up(monkeypatch):
    fake = FakeRouter()
    monkeypatch.setenv("CUSTOMER_CONSOLE_URL", CONSOLE_URL + "/")
    monkeypatch.setenv("CUSTOMER_CONSOLE_ORG_KEY", ORG_KEY)
    get_settings.cache_clear()
    monkeypatch.setattr(console_resolve, "_new_http_client", fake.client)
    await console_resolve.chat_completion_on_console(PAYLOAD)
    assert str(fake.requests[0].url) == f"{CONSOLE_URL}/v1/chat/completions"
    get_settings.cache_clear()


async def test_the_payload_reaches_the_console_untouched(router):
    """The TIER is not coerced, renamed or defaulted on the way out.

    D32.7: a bare model id must 400 rather than be quietly turned into a
    default, and that decision is the Console's. A client that helpfully
    rewrote `model` would take it away.
    """
    import json

    await console_resolve.chat_completion_on_console({"model": "gpt-4o", "messages": []})
    assert json.loads(router.requests[0].content) == {"model": "gpt-4o", "messages": []}


# ── Attribution (§6B.5 hazard 4) ────────────────────────────────────────────

async def test_the_attribution_headers_ride_when_given(router):
    await console_resolve.chat_completion_on_console(
        PAYLOAD,
        member="a@x.test",
        agent="planner",
        module_slug="crm",
        run_id="run-7",
    )
    h = router.requests[0].headers
    assert h["x-cc-member"] == "a@x.test"
    assert h["x-cc-agent"] == "planner"
    assert h["x-cc-module"] == "crm"
    assert h["x-cc-run"] == "run-7"


async def test_an_absent_attribution_value_is_omitted_not_sent_empty(router):
    """An empty header records the empty string as a member.

    That reads as an attribution somebody made, rather than as the absence of
    one — and `usage_event` is what a per-member cap (CP-7) is later built on.
    """
    await console_resolve.chat_completion_on_console(PAYLOAD, member="", agent=None)
    h = router.requests[0].headers
    assert "x-cc-member" not in h
    assert "x-cc-agent" not in h


# ── Verdict versus outage ───────────────────────────────────────────────────

@pytest.mark.parametrize(
    "status,body",
    [
        (200, {"choices": []}),
        (400, {"detail": "unknown tier 'gpt-4o'; name a tier, not a model"}),
        (402, {"detail": "out of credits", "top_up": {}}),
        (403, {"detail": "circuit breaker open"}),
        (404, {"detail": "no such thing"}),
        (409, {"detail": "conflict"}),
    ],
)
async def test_a_verdict_is_returned_for_the_caller_to_relay(router, status, body):
    """⚠️ 402 in particular. "Out of credits" must reach the tenant as itself.

    Flattened into an outage it becomes "the AI is down", which sends the
    customer to support instead of to the top-up they actually need.
    """
    router.answers(status, body)
    got_status, got_body = await console_resolve.chat_completion_on_console(PAYLOAD)
    assert (got_status, got_body) == (status, body)


async def test_streaming_501_is_a_VERDICT_even_though_it_is_a_5xx(router):
    """⚠️ The carve-out. CP-4b is unbuilt and the Console says so explicitly.

    The outage rule used by every other arm is `status >= 500`. Applied here it
    would swallow the one answer slice 3 needs in order to fall back to the
    local path, and would report an unreachable Console instead.
    """
    router.answers(501, {"detail": "streaming is not implemented on the Router yet (CP-4b)"})
    status, body = await console_resolve.chat_completion_on_console(PAYLOAD)
    assert status == 501
    assert "CP-4b" in body["detail"]


@pytest.mark.parametrize("status", [500, 502, 503, 504, 401, 408, 429])
async def test_a_no_answer_status_is_an_outage(router, status):
    router.answers(status, {"detail": "nope"})
    with pytest.raises(console_resolve.ConsoleRouterUnavailable) as e:
        await console_resolve.chat_completion_on_console(PAYLOAD)
    assert str(status) in str(e.value)


async def test_a_transport_failure_is_an_outage(router):
    router.explodes(httpx.ConnectError("no route to host"))
    with pytest.raises(console_resolve.ConsoleRouterUnavailable):
        await console_resolve.chat_completion_on_console(PAYLOAD)


async def test_a_body_that_is_not_a_json_object_becomes_an_empty_dict(router):
    # nginx's HTML error page on a 200, or a bare JSON list.
    router.answers(200, "<html>hello</html>")
    assert await console_resolve.chat_completion_on_console(PAYLOAD) == (200, {})
    router.answers(200, [1, 2, 3])
    assert await console_resolve.chat_completion_on_console(PAYLOAD) == (200, {})


# ── The two decisions that are easy to undo by accident ─────────────────────

async def test_there_is_exactly_ONE_request_per_call(router):
    """⚠️ NO RETRY, deliberately — and this is the fence for it.

    Every other arm of this module tolerates a retry because a resolve is
    idempotent. A completion is not: the Console meters and CHARGES on the way
    through, so retrying a request that actually succeeded bills the customer
    twice for one answer. Someone adding a retry loop "for reliability" must
    fail here first.
    """
    router.answers(503, {})
    with pytest.raises(console_resolve.ConsoleRouterUnavailable):
        await console_resolve.chat_completion_on_console(PAYLOAD)
    assert len(router.requests) == 1


async def test_the_router_gets_its_own_timeout_and_not_the_signin_one(router):
    """5 seconds would abort essentially every real completion.

    `_HTTP_TIMEOUT_SECONDS` is tuned so an unreachable Console degrades inside
    a person's patience during SIGN-IN. Reusing it here would look correct and
    fail on every model that thinks for six seconds.
    """
    await console_resolve.chat_completion_on_console(PAYLOAD)
    assert router.timeouts == [console_resolve._ROUTER_TIMEOUT_SECONDS]
    assert (
        console_resolve._ROUTER_TIMEOUT_SECONDS
        > console_resolve._HTTP_TIMEOUT_SECONDS
    )


def test_the_module_stays_the_one_console_http_client():
    """R7 for the rule the module's own header states.

    A second `httpx.AsyncClient` built anywhere else for the Console is root
    CLAUDE.md §5's defect by name. `_new_http_client` is the only constructor,
    and CP-11 gave it a timeout parameter rather than a sibling.
    """
    import inspect

    source = inspect.getsource(console_resolve)
    assert source.count("httpx.AsyncClient(") == 1, (
        "console_resolve must build exactly one httpx client; a second "
        "constructor is a second Console client by another name"
    )
