"""The serving hop — WS-31 CP-11 slice 3, D57.

`/v1/chat/completions` can now be served by the Console Router instead of the
local litellm SDK. The flag is `ROUTER_SERVING_ENABLED` and it ships OFF.

⚠️ **Three properties carry this slice, and each one fails silently if broken.**

1. **Flag OFF is byte-identical to before.** §6B.7 calls flag-off a SUPPORTED
   state rather than a degraded one, and that is what makes the hop safe to
   merge long before anybody flips it.
2. **A routed call that fails FAILS (D57.7).** No fallback to litellm. A silent
   fallback serves traffic on tenant-local keys, at tenant-local models,
   UNMETERED — the "four deploys reported success while shipping nothing" shape
   applied to billing.
3. **A stream is never routed** (§6B.5 hazard 1). The Router 501s a stream
   because CP-4b is unbuilt, and silently de-streaming a chat UI is the
   behaviour change the spec forbids by name.

⚠️ And the quiet one: `_ensure_keys_loaded()` must NOT run for a routed call
(hazard 3). Loading tenant-local provider keys for a call our own account is
about to serve is the process-global credential injection doing work it does not
need to do.
"""
from __future__ import annotations

import ast
import pathlib
from typing import Any

import pytest

from acb_common.settings import get_settings
from gateway.routes import v1_compat

CONSOLE_URL = "https://console.metorite.test"
ORG_KEY = "cc_live_abcd_secretsecretsecret"

TIER_BODY = {
    "model": "tier-balanced",
    "messages": [{"role": "user", "content": "hi"}],
}
OK_COMPLETION = {"choices": [{"message": {"role": "assistant", "content": "hi"}}]}


class FakeRequest:
    """The two things `_handle_chat_completions` uses off a Request."""

    def __init__(self, body: dict[str, Any], headers: dict[str, str] | None = None):
        self._body = body
        self.headers = {k.lower(): v for k, v in (headers or {}).items()}

    async def json(self) -> dict[str, Any]:
        return self._body


class Spy:
    """Records what the route did instead of what it said it would do."""

    def __init__(self) -> None:
        self.keys_loaded = 0
        self.router_calls: list[tuple[dict[str, Any], dict[str, Any]]] = []
        self.local_calls = 0
        self.answer: tuple[int, dict[str, Any]] = (200, OK_COMPLETION)
        self.raises: Exception | None = None

    async def ensure_keys_loaded(self) -> None:
        self.keys_loaded += 1

    async def chat_completion_on_console(self, payload, **kw):
        self.router_calls.append((payload, kw))
        if self.raises is not None:
            raise self.raises
        return self.answer


class LogSpy:
    """Captures structlog events.

    ⚠️ `caplog` does NOT see these. `get_logger` returns a structlog logger that
    does not route through the stdlib `logging` tree, so a caplog assertion here
    passes or fails for reasons unrelated to the code under test.
    """

    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict]] = []

    def _record(self, level):
        def log(event, **kw):
            self.events.append((level, event, kw))
        return log

    def __getattr__(self, level):
        return self._record(level)

    def names(self) -> list[str]:
        return [e for _, e, _ in self.events]


@pytest.fixture
def spy(monkeypatch):
    s = Spy()
    monkeypatch.setattr(v1_compat, "_ensure_keys_loaded", s.ensure_keys_loaded)
    from acb_auth import console_resolve

    monkeypatch.setattr(
        console_resolve, "chat_completion_on_console", s.chat_completion_on_console
    )
    return s


@pytest.fixture
def logs(monkeypatch):
    spy = LogSpy()
    monkeypatch.setattr(v1_compat, "_log", spy)
    return spy


def _configure(monkeypatch, *, flag: str, url: str = CONSOLE_URL, key: str = ORG_KEY):
    monkeypatch.setenv("ROUTER_SERVING_ENABLED", flag)
    monkeypatch.setenv("CUSTOMER_CONSOLE_URL", url)
    monkeypatch.setenv("CUSTOMER_CONSOLE_ORG_KEY", key)
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _clean_settings():
    yield
    get_settings.cache_clear()


# ── 1. The flag ships OFF, and OFF means unchanged ──────────────────────────

def test_the_flag_defaults_to_off(monkeypatch):
    monkeypatch.delenv("ROUTER_SERVING_ENABLED", raising=False)
    get_settings.cache_clear()
    assert get_settings().router_serving_enabled is False


def test_with_the_flag_off_a_wired_box_still_does_not_route(monkeypatch):
    """⚠️ Wiring is not arming. A box can hold the URL and the org key for
    another reason entirely, and neither may start routing traffic by itself."""
    _configure(monkeypatch, flag="0")
    assert v1_compat._router_should_serve(TIER_BODY) is False


def test_with_the_flag_on_but_unwired_it_does_not_route(monkeypatch):
    _configure(monkeypatch, flag="1", url="", key="")
    assert v1_compat._router_should_serve(TIER_BODY) is False


def test_with_the_flag_on_and_wired_it_routes(monkeypatch):
    _configure(monkeypatch, flag="1")
    assert v1_compat._router_should_serve(TIER_BODY) is True


# ── 2. A stream IS routed, as of slice 5 (H-68 closed) ──────────────────────

def test_a_stream_is_routed_now_that_the_router_can_serve_one(monkeypatch):
    """Slice 3 refused to route a stream, and slice 5 removes that refusal.

    The refusal was correct while the Console answered 501. It also meant
    streaming was **not metered**, and every agent runtime streams — so most
    traffic went unbilled. CP-4b made the Console stream, so the condition goes.
    """
    _configure(monkeypatch, flag="1")
    assert v1_compat._router_should_serve({**TIER_BODY, "stream": True}) is True


def test_nothing_diverts_a_stream_to_the_local_path(monkeypatch, logs):
    """The old escape hatch is gone, and must not come back.

    A stream on the local path is a stream nobody bills. The slice-3 warning
    (`v1.router_stream_served_locally`) existed to make that hole audible, so
    its RETURN would mean the hole did.
    """
    _configure(monkeypatch, flag="1")
    v1_compat._router_should_serve({**TIER_BODY, "stream": True})
    assert "v1.router_stream_served_locally" not in [
        ev for _lvl, ev, _ in logs.events
    ]


def test_the_stream_arm_is_not_reachable_from_the_source_any_more():
    """Source-level: the local-stream branch is deleted, not merely bypassed.

    A branch left in place behind a condition is one somebody re-enables when a
    stream misbehaves in production. Deleting it makes that a code change with
    a review, which is the point.
    """
    src = pathlib.Path(v1_compat.__file__).read_text(encoding="utf-8")
    assert "router_stream_served_locally" not in src


def test_a_stream_takes_the_streaming_hop_not_the_buffered_one():
    """The two hops are not interchangeable.

    `_serve_via_router` returns a dict. Handing a stream to it would buffer the
    whole completion and hand back one late blob — the "silently de-streaming a
    chat UI" behaviour change §6B.5 forbids by name.

    ⚠️ Read from the AST, never the text. A grep matches the dispatch's own
    COMMENTS, and folding the branch into one expression would fail a text
    fence for no reason at all.
    """
    src = pathlib.Path(v1_compat.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    dispatch = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef | ast.FunctionDef)
        and n.name == "_handle_chat_completions"
    )
    names = {s.id for s in ast.walk(dispatch) if isinstance(s, ast.Name)}
    literals = {
        s.value for s in ast.walk(dispatch)
        if isinstance(s, ast.Constant) and isinstance(s.value, str)
    }
    assert "_serve_via_router_stream" in names, "no streaming hop is dispatched"
    assert "_serve_via_router" in names, "the buffered hop is gone"
    assert "stream" in literals, "nothing branches on `stream`"


# ── 3. The routed path ──────────────────────────────────────────────────────

async def test_a_routed_call_never_loads_tenant_provider_keys(monkeypatch, spy):
    """§6B.5 hazard 3, and it is the quiet one.

    `_ensure_keys_loaded()` pulls the process-global tenant provider keys. For a
    call our OWN account is about to serve that is the credential injection
    doing work it does not need to do — and it would still be doing it long
    after anybody remembered to check.
    """
    _configure(monkeypatch, flag="1")
    await v1_compat._handle_chat_completions(FakeRequest(TIER_BODY))
    assert spy.keys_loaded == 0
    assert len(spy.router_calls) == 1


async def test_the_local_path_still_loads_them_when_the_flag_is_off(monkeypatch, spy):
    """The other half of the same property — proving the assertion above is not
    passing because the whole route is broken."""
    import litellm

    _configure(monkeypatch, flag="0")
    spy.raises = AssertionError("the router must not be called with the flag off")

    async def _boom(**kw):
        raise RuntimeError("local provider not configured in this test")

    # Stub litellm rather than let the local path dial a real provider: the
    # assertion is about WHICH path ran, not about what the provider answered.
    monkeypatch.setattr(litellm, "acompletion", _boom)
    got = await v1_compat._handle_chat_completions(FakeRequest(TIER_BODY))

    # The route CATCHES an upstream failure and returns a sanitised non-2xx
    # rather than raising — see `_sanitize_upstream_error`. What matters here is
    # that the LOCAL path is the one that ran.
    assert got.status_code >= 400
    assert spy.keys_loaded == 1
    assert spy.router_calls == []


async def test_the_tier_reaches_the_console_unresolved(monkeypatch, spy):
    """⚠️ `_resolve_model` maps a tier to a concrete model for the LOCAL path.

    The Router resolves from `tier_binding` itself and 400s a bare model id
    rather than coercing it (D32.7). Resolving here would hand the Console a
    model name and turn every tier call into a refusal.
    """
    _configure(monkeypatch, flag="1")
    await v1_compat._handle_chat_completions(FakeRequest(TIER_BODY))
    payload, _ = spy.router_calls[0]
    assert payload["model"] == "tier-balanced"


async def test_the_attribution_headers_are_forwarded(monkeypatch, spy):
    """§6B.5 hazard 4. An unattributed `usage_event` can never become a
    per-member cap (CP-7) or a usage statement (SC-4f)."""
    _configure(monkeypatch, flag="1")
    await v1_compat._handle_chat_completions(
        FakeRequest(
            TIER_BODY,
            {
                "X-CC-Member": "a@x.test",
                "X-CC-Agent": "planner",
                "X-CC-Module": "crm",
                "X-CC-Run": "run-7",
            },
        )
    )
    _, kw = spy.router_calls[0]
    assert kw == {
        "member": "a@x.test",
        "agent": "planner",
        "module_slug": "crm",
        "run_id": "run-7",
    }


async def test_absent_attribution_headers_become_none(monkeypatch, spy):
    _configure(monkeypatch, flag="1")
    await v1_compat._handle_chat_completions(FakeRequest(TIER_BODY))
    _, kw = spy.router_calls[0]
    assert set(kw.values()) == {None}


async def test_spec_violating_messages_are_repaired_before_routing(monkeypatch, spy):
    """The Copilot SDK emits assistant turns with null content beside
    tool_calls, which most providers reject. The repair is provider-agnostic,
    so it still applies when the Console has not chosen a provider yet."""
    _configure(monkeypatch, flag="1")
    body = {
        "model": "tier-fast",
        "messages": [
            {"role": "assistant", "content": None,
             "tool_calls": [{"function": {"name": "f"}}]},
            {"role": "tool", "content": ""},
        ],
    }
    await v1_compat._handle_chat_completions(FakeRequest(body))
    payload, _ = spy.router_calls[0]
    assert payload["messages"][0]["content"] == ""
    assert payload["messages"][1]["content"] == "[tool result]"


async def test_a_successful_completion_is_returned_as_is(monkeypatch, spy):
    _configure(monkeypatch, flag="1")
    got = await v1_compat._handle_chat_completions(FakeRequest(TIER_BODY))
    assert got == OK_COMPLETION


# ── 4. Failure FAILS — D57.7 ────────────────────────────────────────────────

async def test_an_unreachable_router_does_NOT_fall_back_to_litellm(monkeypatch, spy):
    """⚠️ **D57.7, and the most important test in this file.**

    A fallback would serve the call on tenant-local keys, at tenant-local
    models, UNMETERED — and nobody would learn it happened. The call must fail
    instead. `_ensure_keys_loaded` staying at zero is the proof that the local
    path was never entered.
    """
    from acb_auth.console_resolve import ConsoleRouterUnavailable

    _configure(monkeypatch, flag="1")
    spy.raises = ConsoleRouterUnavailable("HTTP 503")
    got = await v1_compat._handle_chat_completions(FakeRequest(TIER_BODY))
    assert got.status_code == 502
    assert spy.keys_loaded == 0, "the local litellm path must not be reachable"


@pytest.mark.parametrize(
    "status",
    [400, 402, 403, 404, 409, 501],
)
async def test_a_verdict_is_relayed_with_its_own_status(monkeypatch, spy, status):
    """⚠️ 402 above all. "Out of credits" flattened into a 502 reads as "the AI
    is down", which sends the customer to support instead of to a top-up."""
    _configure(monkeypatch, flag="1")
    spy.answer = (status, {"detail": "the console said so"})
    got = await v1_compat._handle_chat_completions(FakeRequest(TIER_BODY))
    assert got.status_code == status


async def test_the_hop_is_timed_so_the_extra_latency_is_measurable(
    monkeypatch, spy, logs
):
    """§6B.5 hazard 2: one more network hop on the interactive path.

    On a box where the Console is a loopback (D47) this is negligible — but
    that is a MEASUREMENT, and it stops being true the day the Console moves off
    the box. Logging the number is what makes it checkable later.
    """
    _configure(monkeypatch, flag="1")
    await v1_compat._handle_chat_completions(FakeRequest(TIER_BODY))
    kw = next(kw for _, ev, kw in logs.events if ev == "v1.router_served")
    assert isinstance(kw["elapsed_ms"], int)
    assert kw["status"] == 200


# ── 5. The structural fence ─────────────────────────────────────────────────

def test_the_route_has_no_fallback_arm_from_the_router_to_litellm():
    """R7 for D57.7, at source level.

    The runtime test above proves today's code has no fallback. This one makes
    somebody ADDING one fail here first — a `try: router except: local` arm is
    the single most tempting "reliability" edit on this file, and it is the one
    that would quietly stop metering.
    """
    import inspect

    src = inspect.getsource(v1_compat._serve_via_router)
    assert "acompletion" not in src, (
        "_serve_via_router must never call litellm. A routed call that fails "
        "FAILS (D57.7) — a fallback serves traffic unmetered and nobody learns."
    )
    assert "_ensure_keys_loaded" not in src


def test_the_router_branch_runs_before_the_key_load():
    """Hazard 3, pinned by ORDER rather than by behaviour.

    Swapping these two lines is a one-keystroke edit. The routed call would
    still work, so no functional test would notice — it would just quietly load
    tenant provider keys it has no use for, on every routed request.
    """
    import inspect

    # ⚠️ NON-COMMENT lines only. The first version of this test read the raw
    # source and failed on its own documentation: the comment above the branch
    # NAMES `_ensure_keys_loaded()` while explaining why the branch precedes it,
    # so a plain `.index()` found the prose first. A guard satisfied — or broken
    # — by prose is checking the documentation, not the wiring.
    code = [
        ln for ln in inspect.getsource(v1_compat._handle_chat_completions).splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]
    branch = next(i for i, ln in enumerate(code) if "_router_should_serve" in ln)
    load = next(i for i, ln in enumerate(code) if "await _ensure_keys_loaded()" in ln)
    assert branch < load, (
        "the router branch must come BEFORE _ensure_keys_loaded()"
    )
