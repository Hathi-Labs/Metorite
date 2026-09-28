"""Provider balance alerts — the probes, the refusal hook and the status rule.

Owner request, 2026-09-28. DeepSeek held -0.05 USD for two days, refused every
call with 402, and all AI on the platform failed with nobody told.

⚠️ **Hermetic, deliberately.** No database and no network. The probes run
against ``httpx.MockTransport`` with the response shapes each vendor documents
(links in ``provider_balance.py``). The R8 half — the table, the real route and
the transitions — is ``test_customer_console_provider_health.py``.

The subject is the case where this is WRONG:

  1. A key that reaches a result, an error string or a log line.
  2. A vendor that hides its balance drawn as green.
  3. A 402 on a failed-over step that nobody counts.
  4. A customer's own (BYOK) account running dry, alerted as ours.
"""

from __future__ import annotations

import ast
import asyncio
import logging
import pathlib
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest
from customer_console import provider_balance as pb
from customer_console.router import Credential, ResolvedTier, UpstreamFailed, walk_chain

SECRET = "sk-THIS-IS-THE-SECRET-0123456789abcdef"
NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _json(status: int, body: Any) -> httpx.Response:
    return httpx.Response(status, json=body)


# ── The recorded vendor shapes ──────────────────────────────────────────────

DEEPSEEK_OK = {
    "is_available": True,
    "balance_infos": [
        {"currency": "CNY", "total_balance": "110.00", "granted_balance": "10.00",
         "topped_up_balance": "100.00"},
        {"currency": "USD", "total_balance": "12.34", "granted_balance": "0.00",
         "topped_up_balance": "12.34"},
    ],
}
#: What production answered on 2026-09-28, in the documented shape.
DEEPSEEK_DRY = {
    "is_available": False,
    "balance_infos": [{"currency": "USD", "total_balance": "-0.05",
                       "granted_balance": "0.00", "topped_up_balance": "-0.05"}],
}
OPENROUTER_CREDITS = {"data": {"total_credits": 100.5, "total_usage": 25.75}}
OPENROUTER_KEY_CAPPED = {"data": {"limit": 100, "limit_remaining": 74.5, "usage": 25.5,
                                  "label": "sk-or-v1-au7...890"}}
OPENROUTER_KEY_UNCAPPED = {"data": {"limit": None, "limit_remaining": None, "usage": 3.2,
                                    "label": "sk-or-v1-au7...890"}}
AIMLAPI_OK = {"current_balance": 4.25, "currency": "USD"}


class TestParsers:
    def test_deepseek_prefers_usd_and_keeps_the_vendor_flag(self):
        r = pb.parse_deepseek(DEEPSEEK_OK)
        assert (r.status, r.balance, r.currency, r.available) == (
            "ok", Decimal("12.34"), "USD", True)

    def test_deepseek_falls_back_to_the_only_currency_and_converts_nothing(self):
        r = pb.parse_deepseek({"is_available": True, "balance_infos": [
            {"currency": "CNY", "total_balance": "110.00"}]})
        assert (r.balance, r.currency) == (Decimal("110.00"), "CNY")

    def test_deepseek_the_production_outage_shape(self):
        r = pb.parse_deepseek(DEEPSEEK_DRY)
        assert (r.status, r.balance, r.available) == ("ok", Decimal("-0.05"), False)

    @pytest.mark.parametrize("body", [None, [], {"balance_infos": "x"}, {"balance_infos": [
        {"currency": "USD", "total_balance": "not a number"}]}])
    def test_deepseek_unreadable_is_failed_not_zero(self, body):
        r = pb.parse_deepseek(body)
        assert r.status == "failed" and r.balance is None

    def test_openrouter_credits_is_credits_minus_usage(self):
        r = pb.parse_openrouter_credits(OPENROUTER_CREDITS)
        assert (r.balance, r.currency) == (Decimal("74.75"), "USD")

    def test_openrouter_key_limit_remaining(self):
        r = pb.parse_openrouter_key(OPENROUTER_KEY_CAPPED)
        assert (r.status, r.balance) == ("ok", Decimal("74.5"))

    def test_openrouter_uncapped_key_is_NOT_a_zero_balance(self):
        r = pb.parse_openrouter_key(OPENROUTER_KEY_UNCAPPED)
        assert r.status == "not_exposed" and r.balance is None

    def test_aimlapi_v2_billing(self):
        r = pb.parse_aimlapi(AIMLAPI_OK)
        assert (r.status, r.balance, r.currency) == ("ok", Decimal("4.25"), "USD")


class TestProbes:
    def test_deepseek_hits_its_documented_endpoint_with_a_bearer(self):
        seen: list[httpx.Request] = []

        def handler(req: httpx.Request) -> httpx.Response:
            seen.append(req)
            return _json(200, DEEPSEEK_OK)

        r = pb.probe("deepseek", SECRET, client=_client(handler))
        assert r.status == "ok"
        assert str(seen[0].url) == "https://api.deepseek.com/user/balance"
        assert seen[0].headers["authorization"] == f"Bearer {SECRET}"

    def test_openrouter_falls_back_to_the_key_when_credits_wants_a_management_key(self):
        paths: list[str] = []

        def handler(req: httpx.Request) -> httpx.Response:
            paths.append(req.url.path)
            if req.url.path == "/api/v1/credits":
                return _json(403, {"error": {"code": 403, "message": "Only management keys"}})
            return _json(200, OPENROUTER_KEY_CAPPED)

        r = pb.probe("openrouter", SECRET, client=_client(handler))
        assert paths == ["/api/v1/credits", "/api/v1/key"]
        assert (r.status, r.balance, r.source) == ("ok", Decimal("74.5"), "/api/v1/key")

    def test_aimlapi_uses_v2_not_the_retired_v1(self):
        paths: list[str] = []

        def handler(req: httpx.Request) -> httpx.Response:
            paths.append(str(req.url))
            return _json(200, AIMLAPI_OK)

        assert pb.probe("aimlapi", SECRET, client=_client(handler)).status == "ok"
        assert paths == ["https://api.aimlapi.com/v2/billing"]

    @pytest.mark.parametrize("vendor", ["openai", "anthropic", "groq", "gemini", "mistral",
                                        "some-new-vendor"])
    def test_a_vendor_outside_the_registry_is_not_exposed_and_calls_nobody(self, vendor):
        def handler(req: httpx.Request) -> httpx.Response:  # pragma: no cover
            raise AssertionError("a vendor with no probe was called")

        r = pb.probe(vendor, SECRET, client=_client(handler))
        assert r.status == "not_exposed" and r.balance is None

    def test_a_custom_api_base_never_sends_the_key_to_the_vendor_host(self):
        def handler(req: httpx.Request) -> httpx.Response:  # pragma: no cover
            raise AssertionError("a proxy key was sent to the vendor")

        r = pb.probe("deepseek", SECRET, api_base="https://proxy.example/v1",
                     client=_client(handler))
        assert r.status == "not_exposed"

    def test_the_vendor_own_api_base_still_probes(self):
        r = pb.probe("deepseek", SECRET, api_base="https://api.deepseek.com/v1",
                     client=_client(lambda req: _json(200, DEEPSEEK_OK)))
        assert r.status == "ok"

    def test_a_timeout_is_failed_and_never_raises(self):
        def handler(req: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("slow", request=req)

        r = pb.probe("deepseek", SECRET, client=_client(handler))
        assert r.status == "failed" and r.error == pb._ERRORS["timeout"]

    def test_a_401_carries_the_status(self):
        r = pb.probe("deepseek", SECRET, client=_client(lambda req: _json(401, {})))
        assert (r.status, r.http_status) == ("failed", 401)


# ── The secret goes nowhere ─────────────────────────────────────────────────


def _echo_secret(req: httpx.Request) -> httpx.Response:
    """A hostile vendor: it quotes our key back in every answer."""
    auth = req.headers.get("authorization", "")
    return httpx.Response(500, text=f"bad key {auth}", headers={"x-echo": auth})


def _raise_with_secret(req: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError(f"cannot connect with {req.headers['authorization']}", request=req)


@pytest.mark.parametrize("handler", [_echo_secret, _raise_with_secret,
                                     lambda req: _json(200, {"echo": SECRET, "data": SECRET})])
@pytest.mark.parametrize("vendor", sorted(pb.PROBES))
def test_no_probe_result_can_carry_the_secret(vendor, handler, caplog):
    caplog.set_level(logging.DEBUG)
    r = pb.probe(vendor, SECRET, client=_client(handler))
    assert SECRET not in repr(r)
    assert SECRET[-12:] not in repr(r)
    assert SECRET not in caplog.text


def test_probe_all_logs_no_secret(monkeypatch, caplog):
    """The loop's own log line on a failed probe carries no key either."""
    caplog.set_level(logging.DEBUG)
    written: list[dict[str, Any]] = []

    class _Conn:
        def execute(self, *a, **k):  # pragma: no cover - store is patched
            raise AssertionError

    class _Engine:
        def begin(self):
            import contextlib

            return contextlib.nullcontext(_Conn())

    from customer_console import router, store

    monkeypatch.setattr(store, "provider_platform_names", lambda conn: ["deepseek"])
    monkeypatch.setattr(store, "provider_refusal_prune", lambda conn: 0)
    monkeypatch.setattr(store, "provider_health_save_probe",
                        lambda conn, **kw: written.append(kw))
    monkeypatch.setattr(router, "provider_credential",
                        lambda conn, provider, org_id=None: Credential(SECRET, None, False))
    assert pb.probe_all(_Engine(), client=_client(_echo_secret)) == 1
    assert written[0]["status"] == "failed"
    assert SECRET not in repr(written) and SECRET not in caplog.text


# ── The status rule ─────────────────────────────────────────────────────────


def _in(**kw: Any) -> pb.HealthInput:
    return pb.HealthInput(provider="v", **kw)


def _ok_usd(balance: str, **kw: Any) -> pb.HealthInput:
    return _in(balance=Decimal(balance), currency="USD", available=True,
               balance_checked_at=NOW - timedelta(minutes=5), probe_status="ok", **kw)


class TestStatusRule:
    def test_healthy(self):
        assert pb.assess(_ok_usd("50"), now=NOW).status == "ok"

    def test_the_outage_vendor_says_unavailable(self):
        h = _in(balance=Decimal("-0.05"), currency="USD", available=False, probe_status="ok",
                balance_checked_at=NOW)
        assert pb.assess(h, now=NOW).status == "out"

    def test_zero_balance_is_out(self):
        assert pb.assess(_ok_usd("0"), now=NOW).status == "out"

    def test_a_recent_402_is_out_even_when_the_balance_is_invisible(self):
        h = _in(probe_status="not_exposed", last_refusal_by_status={402: NOW - timedelta(minutes=2)})
        assert pb.assess(h, now=NOW).status == "out"

    def test_a_402_older_than_the_window_ages_out(self):
        h = _in(probe_status="not_exposed", last_refusal_by_status={402: NOW - timedelta(hours=3)})
        assert pb.assess(h, now=NOW).status == "unknown"

    def test_a_healthy_probe_AFTER_a_402_clears_it(self):
        h = _ok_usd("50", last_refusal_by_status={402: NOW - timedelta(minutes=30)})
        assert pb.assess(h, now=NOW).status == "ok"

    def test_a_healthy_probe_BEFORE_a_402_does_not(self):
        h = _ok_usd("50", last_refusal_by_status={402: NOW - timedelta(minutes=1)})
        assert pb.assess(h, now=NOW).status == "out"

    def test_ONE_401_is_decisive(self):
        h = _in(probe_status="not_exposed", last_refusal_by_status={401: NOW},
                recent_count_by_status={401: 1})
        v = pb.assess(h, now=NOW)
        assert (v.status, v.cause) == ("refusing", "key")

    def test_ONE_402_is_still_out_immediately(self):
        h = _in(probe_status="not_exposed", last_refusal_by_status={402: NOW},
                recent_count_by_status={402: 1})
        v = pb.assess(h, now=NOW)
        assert (v.status, v.cause) == ("out", "payment")

    def test_a_402_is_not_cleared_by_a_later_success(self):
        """The reviewer's rule: 401 and 402 stay as they were."""
        h = _in(probe_status="not_exposed", last_refusal_by_status={402: NOW - timedelta(minutes=5)},
                recent_count_by_status={402: 1}, last_success_at=NOW)
        assert pb.assess(h, now=NOW).status == "out"

    @pytest.mark.parametrize("status", [403, 429])
    def test_ONE_403_or_429_is_NOT_an_alarm(self, status):
        """🔴 PR #524 review P1. One moderated prompt, or one busy minute that
        failover served, drew a red banner for an hour."""
        h = _in(probe_status="not_exposed", last_refusal_by_status={status: NOW},
                recent_count_by_status={status: 1})
        assert pb.assess(h, now=NOW).status == "unknown"

    def test_three_403s_and_no_success_is_refusing(self):
        h = _in(probe_status="not_exposed", last_refusal_by_status={403: NOW},
                recent_count_by_status={403: 3})
        v = pb.assess(h, now=NOW)
        assert (v.status, v.cause) == ("refusing", "key")
        assert "rejected our key" in v.reason

    def test_three_429s_then_a_success_is_CLEARED(self):
        h = _in(probe_status="not_exposed",
                last_refusal_by_status={429: NOW - timedelta(minutes=2)},
                recent_count_by_status={429: 3},
                last_success_at=NOW - timedelta(minutes=1))
        assert pb.assess(h, now=NOW).status == "unknown"

    def test_a_success_BEFORE_the_latest_refusal_clears_nothing(self):
        h = _in(probe_status="not_exposed", last_refusal_by_status={403: NOW},
                recent_count_by_status={403: 3}, last_success_at=NOW - timedelta(minutes=1))
        assert pb.assess(h, now=NOW).status == "refusing"

    def test_429s_alone_are_RATE_LIMITED_amber_and_never_top_up(self):
        h = _ok_usd("50", last_refusal_by_status={429: NOW - timedelta(minutes=1)},
                    recent_count_by_status={429: 4})
        v = pb.assess(h, now=NOW)
        assert (v.status, v.cause) == ("rate_limited", "rate_limit")
        assert "rate-limiting" in v.reason
        assert "top up" not in v.reason.lower() and "payment" not in v.reason.lower()

    def test_the_repeat_threshold_is_configurable(self):
        h = _in(probe_status="not_exposed", last_refusal_by_status={403: NOW},
                recent_count_by_status={403: 2})
        assert pb.assess(h, now=NOW).status == "unknown"
        assert pb.assess(h, now=NOW, repeat=2).status == "refusing"
        assert pb.repeat_threshold({pb.REPEAT_VAR: "5"}) == 5
        assert pb.repeat_threshold({pb.REPEAT_VAR: "0"}) == pb.DEFAULT_REPEAT

    def test_a_probe_refused_with_401_is_refusing(self):
        h = _in(probe_status="failed", probe_http=401)
        assert pb.assess(h, now=NOW).status == "refusing"

    def test_under_the_default_usd_line_is_low(self):
        v = pb.assess(_ok_usd("4.99"), now=NOW)
        assert (v.status, v.threshold) == ("low", Decimal("5"))

    def test_the_env_line_moves_it(self):
        assert pb.assess(_ok_usd("8"), now=NOW, low_usd=Decimal("10")).status == "low"
        assert pb.low_line_usd({pb.LOW_USD_VAR: "10"}) == Decimal("10")
        assert pb.low_line_usd({pb.LOW_USD_VAR: "junk"}) == pb.DEFAULT_LOW_USD

    def test_a_per_provider_override_wins(self):
        assert pb.assess(_ok_usd("8", low_threshold=Decimal("10")), now=NOW).status == "low"
        assert pb.assess(_ok_usd("4", low_threshold=Decimal("1")), now=NOW).status == "ok"

    def test_the_usd_default_never_applies_to_another_currency(self):
        h = _in(balance=Decimal("4"), currency="CNY", available=True, probe_status="ok",
                balance_checked_at=NOW)
        v = pb.assess(h, now=NOW)
        assert v.status == "ok" and v.threshold is None
        # ⚠️ It must not claim a check that never ran.
        assert "above the low line" not in v.reason
        assert "No low line is set for CNY" in v.reason

    def test_under_three_days_of_runway_is_low_whatever_the_balance(self):
        # $70 a week is $10 a day, so $25 is 2.5 days.
        v = pb.assess(_ok_usd("25", cost_7d_usd=Decimal("70")), now=NOW)
        assert (v.status, v.days_left) == ("low", Decimal("2.5"))

    def test_days_left_is_none_for_a_non_usd_balance(self):
        assert pb.days_left(Decimal("100"), "CNY", Decimal("70")) is None
        assert pb.days_left(Decimal("100"), "USD", Decimal("0")) is None

    def test_invisible_and_quiet_is_UNKNOWN_never_ok(self):
        v = pb.assess(_in(probe_status="not_exposed"), now=NOW)
        assert v.status == "unknown"
        assert pb.assess(_in(), now=NOW).status == "unknown"

    def test_a_failed_read_is_probe_failed_but_keeps_judging_the_last_balance(self):
        h = _in(balance=Decimal("50"), currency="USD", available=True, probe_status="failed",
                probe_http=500, balance_checked_at=NOW - timedelta(hours=1))
        assert pb.assess(h, now=NOW).status == "probe_failed"
        h2 = _in(balance=Decimal("2"), currency="USD", available=True, probe_status="failed",
                 balance_checked_at=NOW - timedelta(hours=1))
        assert pb.assess(h2, now=NOW).status == "low"

    def test_every_status_is_in_the_vocabulary(self):
        assert set(pb.STATUSES) == {
            "out", "refusing", "rate_limited", "low", "probe_failed", "unknown", "ok"}


# ── The refusal buffer and the chain hook ───────────────────────────────────


@pytest.fixture(autouse=True)
def _empty_buffer():
    pb.drain_refusals()
    pb.drain_successes()
    yield
    pb.drain_refusals()
    pb.drain_successes()


def test_the_buffer_counts_watched_statuses_only():
    for status in (402, 402, 401, 429, 500, 400, 404, None):
        pb.note_refusal("deepseek", status, now=NOW)
    got = {(r["provider"], r["status"]): r["refusals"] for r in pb.drain_refusals()}
    assert got == {("deepseek", 402): 2, ("deepseek", 401): 1, ("deepseek", 429): 1,
                   ("deepseek", 500): 1}
    assert pb.drain_refusals() == []


def test_a_failed_write_puts_the_counts_back():
    pb.note_refusal("deepseek", 402, now=NOW)
    rows = pb.drain_refusals()
    pb.restore_refusals(rows)
    assert pb.drain_refusals()[0]["refusals"] == 1


class _Upstream(Exception):
    def __init__(self, status: int) -> None:
        super().__init__(f"upstream {status}")
        self.status_code = status


@pytest.mark.parametrize("status", [401, 403, 429, 503])
def test_the_hook_sees_a_refusal_that_a_failover_HIDES(status):
    """🔴 The customer is served by the second vendor, and the refusing first
    account is still counted.

    ⚠️ 402 is not here because the walk does NOT fail over on it today
    (`is_retryable`), so a 402 always reaches `_upstream_refusal`. The next
    test pins that the hook sees it there too."""
    steps = [ResolvedTier(tier="t", model="deepseek/chat"),
             ResolvedTier(tier="t", model="other/chat", rank=2)]
    seen: list[tuple[str, int | None]] = []

    async def attempt(step: ResolvedTier) -> Any:
        if step.model.startswith("deepseek"):
            raise _Upstream(status)
        return "answer"

    answer, served = asyncio.run(
        walk_chain(steps, attempt, None, lambda s, st: seen.append((s.model, st))))
    assert answer == "answer" and served.model == "other/chat"
    assert seen == [("deepseek/chat", status)]


def test_the_hook_sees_the_402_that_ends_the_walk():
    steps = [ResolvedTier(tier="t", model="deepseek/chat")]
    seen: list[int | None] = []

    async def attempt(step: ResolvedTier) -> Any:
        raise _Upstream(402)

    with pytest.raises(UpstreamFailed):
        asyncio.run(walk_chain(steps, attempt, None, lambda s, st: seen.append(st)))
    assert seen == [402]


def test_a_broken_hook_never_changes_the_walk():
    steps = [ResolvedTier(tier="t", model="deepseek/chat")]

    async def attempt(step: ResolvedTier) -> Any:
        raise _Upstream(402)

    def boom(step, status):
        raise RuntimeError("hook bug")

    with pytest.raises(UpstreamFailed) as info:
        asyncio.run(walk_chain(steps, attempt, None, boom))
    assert info.value.status == 402


def test_the_watch_counts_platform_steps_and_skips_BYOK():
    from customer_console.main import _chain_watch

    refused, served = _chain_watch({
        "deepseek": Credential("k" * 20, None, False),
        "groq": Credential("k" * 20, None, True),
    })
    for model in ("deepseek/chat", "groq/llama", "missing/x"):
        refused(ResolvedTier(tier="t", model=model), 402)
        served(ResolvedTier(tier="t", model=model))
    assert [(r["provider"], r["status"]) for r in pb.drain_refusals()] == [("deepseek", 402)]
    assert list(pb.drain_successes()) == ["deepseek"]


def test_the_success_hook_sees_the_step_that_ANSWERED_and_only_it():
    steps = [ResolvedTier(tier="t", model="deepseek/chat"),
             ResolvedTier(tier="t", model="other/chat", rank=2)]
    served: list[str] = []

    async def attempt(step: ResolvedTier) -> Any:
        if step.model.startswith("deepseek"):
            raise _Upstream(429)
        return "answer"

    asyncio.run(walk_chain(steps, attempt, None, None, lambda s: served.append(s.model)))
    assert served == ["other/chat"]


def test_a_broken_success_hook_never_costs_the_answer():
    steps = [ResolvedTier(tier="t", model="deepseek/chat")]

    async def attempt(step: ResolvedTier) -> Any:
        return "answer"

    def boom(step):
        raise RuntimeError("hook bug")

    assert asyncio.run(walk_chain(steps, attempt, None, None, boom))[0] == "answer"


def test_successes_keep_the_latest_and_survive_a_failed_write():
    pb.note_success("deepseek", now=NOW - timedelta(minutes=5))
    pb.note_success("deepseek", now=NOW)
    pb.note_success("deepseek", now=NOW - timedelta(minutes=9))
    got = pb.drain_successes()
    assert got == {"deepseek": NOW}
    pb.restore_successes(got)
    assert pb.drain_successes() == {"deepseek": NOW}


# ── The wiring fence ────────────────────────────────────────────────────────

MAIN = (pathlib.Path(__file__).resolve().parents[2]
        / "apps/services/customer_console/customer_console/main.py")


def _is_watch(node: ast.AST) -> bool:
    return isinstance(node, ast.Call) and ast.unparse(node.func) == "_chain_watch"


def test_every_chain_walk_in_main_passes_the_watch():
    """🔴 A serving route that walks a chain without the hooks is a vendor that
    can run dry unseen. Every ``call_chain`` passes ``*_chain_watch(...)`` as
    its fourth argument, and the stream opener takes ``watch=_chain_watch(...)``."""
    tree = ast.parse(MAIN.read_text(encoding="utf-8"))
    walks, streams = 0, 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(
            node.func, "id", None)
        if name == "call_chain":
            walks += 1
            assert len(node.args) == 4, ast.unparse(node)
            last = node.args[3]
            assert isinstance(last, ast.Starred) and _is_watch(last.value), ast.unparse(node)
        if name == "_open_stream_or_release":
            streams += 1
            kw = {k.arg: k.value for k in node.keywords}
            assert "watch" in kw and _is_watch(kw["watch"]), ast.unparse(node)
    assert walks >= 6 and streams >= 1, (walks, streams)
