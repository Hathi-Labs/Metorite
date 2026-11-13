"""AUTO-8b-2b: the header ``X-CC-Run-Ceiling`` on its way to the Console.

Spec: ``project-docs/specs/proactive_agent.md`` §4.4 and the §9 row
AUTO-8b-2b (task 3081). Hermetic: no database and no model call.

The header can only LOWER the Router's fixed ceiling of 500 credits for one
run. The Console is the ONE place that parses and clamps it
(``customer_console.credits``, fenced by ``test_customer_console_credits.py``
and, on a real Postgres, ``test_customer_console_router.py``). This file fences
the three parts in front of it:

1. ``acb_llm.attribution.run_ceiling_scope``: the value that a model call
   sends, 4 places, rounded down. Only the headless turn binds it, so a chat
   call sends no header.
2. ``acb_auth.console_resolve``: both Router clients send the value EXACTLY as
   given, the empty string too. Only ``None`` omits it.
3. ``gateway.routes.v1_compat``: both hops forward the raw value.

**Mutations (R7)**, each run by hand on 2026-11-13:

* ``v1_compat._run_ceiling`` adds ``or None``: ``test_an_empty_value_crosses_
  each_hop_as_empty`` goes red.
* The buffered hop drops ``run_ceiling=``: ``test_the_buffered_hop_forwards_
  the_header`` goes red.
* ``attribution_headers`` drops the ceiling: ``test_the_scope_stamps_each_
  request_and_ends_with_the_block`` goes red.

Run::

    uv run pytest tests/unit/test_run_ceiling_header.py -q -p no:cacheprovider
"""

from __future__ import annotations

import asyncio
import re
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest

pytest.importorskip("gateway.routes", reason="gateway not installed")

from acb_auth import console_resolve
from acb_common.settings import get_settings
from acb_llm import attribution
from acb_llm.attribution import (
    RUN_CEILING,
    attribution_headers,
    run_ceiling_scope,
    run_ceiling_text,
)
from gateway.routes import v1_compat

_ROOT = Path(__file__).resolve().parents[2]

CONSOLE_URL = "https://console.invalid"
ORG_KEY = "cc_live_ceilingfixture_notarealsecret"
PAYLOAD = {"model": "tier-balanced", "messages": [{"role": "user", "content": "hi"}]}
OK = {"choices": [{"message": {"role": "assistant", "content": "hi"}}]}


# ── The name ────────────────────────────────────────────────────────────────


def test_the_two_packages_spell_the_header_one_way() -> None:
    assert RUN_CEILING == console_resolve.RUN_CEILING_HEADER == "X-CC-Run-Ceiling"


# ── 1. The value that a model call sends ────────────────────────────────────


@pytest.mark.parametrize(
    ("credits", "text"),
    [
        (Decimal("42.5"), "42.5000"),
        (Decimal("7"), "7.0000"),
        (5, "5.0000"),
        (Decimal("7.12349"), "7.1234"),     # down, never up
        (Decimal("0.00005"), "0.0000"),
        (Decimal("-3"), "0.0000"),          # below 0 stops each call
        (Decimal("900"), "900.0000"),       # the Console takes the min
    ],
)
def test_the_text_has_four_places_and_rounds_down(credits: Any, text: str) -> None:
    assert run_ceiling_text(credits) == text


@pytest.mark.parametrize(
    "credits", [Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity"), 5.0, "5", True, None])
def test_a_value_that_is_not_a_finite_decimal_is_refused(credits: Any) -> None:
    with pytest.raises(ValueError):
        run_ceiling_text(credits)


@pytest.mark.parametrize("credits", ["0", "0.0001", "7.5", "49.9999", "50", "900"])
def test_each_text_the_turn_sends_parses_at_the_console(credits: str) -> None:
    from customer_console.credits import parse_run_ceiling

    text = run_ceiling_text(Decimal(credits))
    assert parse_run_ceiling(text) == Decimal(credits)


def test_no_scope_sends_no_header() -> None:
    # A chat call: a member, a run, and no ceiling.
    out = attribution_headers({"user": "dana@acme.com", "run_id": "run-7", "app": "chat"})
    assert RUN_CEILING not in out
    assert RUN_CEILING not in attribution_headers()
    assert attribution_headers({}) == {}


def test_the_scope_stamps_each_request_and_ends_with_the_block() -> None:
    async def go() -> list[str | None]:
        seen: list[str | None] = []
        with run_ceiling_scope(Decimal("7.5")) as text:
            assert text == "7.5000"
            assert attribution_headers({"run_id": "r"})[RUN_CEILING] == "7.5000"
            request = httpx.Request("POST", "https://gw.invalid/v1/chat/completions")
            await attribution._stamp(request)
            seen.append(request.headers.get(RUN_CEILING))

            # A task that the run starts inherits the value, as `run_agent` does.
            async def child() -> str | None:
                return attribution_headers().get(RUN_CEILING)

            seen.append(await asyncio.create_task(child()))
            seen.append(await asyncio.to_thread(
                lambda: attribution_headers().get(RUN_CEILING)))
        after = httpx.Request("POST", "https://gw.invalid/v1/chat/completions")
        await attribution._stamp(after)
        seen.append(after.headers.get(RUN_CEILING))
        return seen

    assert asyncio.run(go()) == ["7.5000", "7.5000", "7.5000", None]


def test_a_nested_scope_gives_the_outer_value_back() -> None:
    with run_ceiling_scope(Decimal("10")):
        with run_ceiling_scope(Decimal("3")):
            assert attribution_headers()[RUN_CEILING] == "3.0000"
        assert attribution_headers()[RUN_CEILING] == "10.0000"
    assert RUN_CEILING not in attribution_headers()


def test_the_stamp_never_overwrites_a_set_header() -> None:
    request = httpx.Request("POST", "https://gw.invalid/v1", headers={RUN_CEILING: "1"})
    with run_ceiling_scope(Decimal("9")):
        asyncio.run(attribution._stamp(request))
    assert request.headers[RUN_CEILING] == "1"


def test_a_copilot_provider_gets_the_value_of_its_session() -> None:
    with run_ceiling_scope(Decimal("4")):
        stamped = attribution.attributed_copilot_provider(
            {"type": "openai", "headers": {RUN_CEILING: "stale"}})
    assert stamped is not None
    assert stamped["headers"][RUN_CEILING] == "4.0000"


#: The one module that may bind the scope. A chat path that binds it would
#: lower the ceiling of a person's own run with no goal behind it.
_BINDERS = {"apps/services/gateway/gateway/jobs/headless_turn.py"}
_DEFINER = "packages/acb_llm/acb_llm/attribution.py"
_CALL = re.compile(r"\brun_ceiling_scope\(")


def test_only_the_headless_turn_binds_the_scope() -> None:
    found: set[str] = set()
    for base in ("apps", "packages"):
        for path in (_ROOT / base).rglob("*.py"):
            if "node_modules" in path.parts or ".venv" in path.parts:
                continue
            rel = path.relative_to(_ROOT).as_posix()
            if rel == _DEFINER:
                continue
            if _CALL.search(path.read_text(encoding="utf-8", errors="replace")):
                found.add(rel)
    assert found == _BINDERS


# ── 2. The header builder and both Router clients ───────────────────────────


def test_the_builder_omits_none_and_keeps_every_other_value_as_given() -> None:
    base = dict(member="a@b.com", agent="w", module_slug="auto", run_id="r")
    assert RUN_CEILING not in console_resolve._attribution_headers(**base)
    assert RUN_CEILING not in console_resolve._attribution_headers(**base, run_ceiling=None)
    for raw in ("5", "", "abc", "900", "-1"):
        out = console_resolve._attribution_headers(**base, run_ceiling=raw)
        assert out[RUN_CEILING] == raw
        # The other headers do not change.
        assert {k: v for k, v in out.items() if k != RUN_CEILING} == \
            console_resolve._attribution_headers(**base)


class _Console:
    """A Console Router behind MockTransport. It records each request."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.headers.get("accept") == "text/event-stream" or b'"stream"' in request.content:
            return httpx.Response(200, stream=httpx.ByteStream(b"data: [DONE]\n\n"),
                                  headers={"content-type": "text/event-stream"})
        return httpx.Response(200, json=OK)

    def client(self, timeout: Any = None) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self._handle), timeout=5.0)


@pytest.fixture
def console(monkeypatch):
    fake = _Console()
    monkeypatch.setenv("ROUTER_SERVING_ENABLED", "1")
    monkeypatch.setenv("CUSTOMER_CONSOLE_URL", CONSOLE_URL)
    monkeypatch.setenv("CUSTOMER_CONSOLE_ORG_KEY", ORG_KEY)
    get_settings.cache_clear()
    monkeypatch.setattr(console_resolve, "_new_http_client", fake.client)
    try:
        yield fake
    finally:
        get_settings.cache_clear()


async def _drain(agen) -> bytes:
    return b"".join([chunk async for chunk in agen])


@pytest.mark.parametrize("raw", ["5", ""])
def test_the_buffered_client_sends_the_value(console, raw: str) -> None:
    asyncio.run(console_resolve.chat_completion_on_console(
        PAYLOAD, run_id="r", run_ceiling=raw))
    assert console.requests[-1].headers[RUN_CEILING] == raw


@pytest.mark.parametrize("raw", ["5", ""])
def test_the_stream_client_sends_the_value(console, raw: str) -> None:
    asyncio.run(_drain(console_resolve.stream_completion_on_console(
        {**PAYLOAD, "stream": True}, run_id="r", run_ceiling=raw)))
    assert console.requests[-1].headers[RUN_CEILING] == raw


def test_both_clients_send_no_header_by_default(console) -> None:
    asyncio.run(console_resolve.chat_completion_on_console(PAYLOAD, run_id="r"))
    asyncio.run(_drain(console_resolve.stream_completion_on_console(
        {**PAYLOAD, "stream": True}, run_id="r")))
    assert [RUN_CEILING.lower() in r.headers for r in console.requests] == [False, False]


# ── 3. Both hops of `/v1` ───────────────────────────────────────────────────


class _Req:
    """What the hops read off a Starlette Request: the headers and the body."""

    def __init__(self, headers: dict[str, str] | None = None,
                 body: dict[str, Any] | None = None) -> None:
        self.headers = httpx.Headers(headers or {})
        self._body = body or PAYLOAD

    async def json(self) -> dict[str, Any]:
        return self._body


def _headers(raw: str | None) -> dict[str, str]:
    out = {"X-CC-Run": "run-9"}
    if raw is not None:
        out["X-CC-Run-Ceiling"] = raw
    return out


@pytest.mark.parametrize("raw", ["5", "900", "abc"])
def test_the_buffered_hop_forwards_the_header(console, raw: str) -> None:
    asyncio.run(v1_compat._serve_via_router(_Req(_headers(raw)), PAYLOAD))
    sent = console.requests[-1].headers
    assert sent[RUN_CEILING] == raw
    assert sent["x-cc-run"] == "run-9"


@pytest.mark.parametrize("raw", ["5", "900", "abc"])
def test_the_stream_hop_forwards_the_header(console, raw: str) -> None:
    async def go() -> None:
        resp = await v1_compat._serve_via_router_stream(
            _Req(_headers(raw)), {**PAYLOAD, "stream": True})
        [chunk async for chunk in resp.body_iterator]

    asyncio.run(go())
    sent = console.requests[-1].headers
    assert sent[RUN_CEILING] == raw
    assert sent["x-cc-run"] == "run-9"


def test_an_empty_value_crosses_each_hop_as_empty(console) -> None:
    """The gateway never turns a bad value into "no header". The Console
    answers 400 to it, and never serves it at the default ceiling."""
    async def go() -> None:
        await v1_compat._serve_via_router(_Req(_headers("")), PAYLOAD)
        resp = await v1_compat._serve_via_router_stream(
            _Req(_headers("")), {**PAYLOAD, "stream": True})
        [chunk async for chunk in resp.body_iterator]

    asyncio.run(go())
    assert [r.headers.get(RUN_CEILING) for r in console.requests] == ["", ""]


def test_no_header_crosses_neither_hop(console) -> None:
    async def go() -> None:
        await v1_compat._serve_via_router(_Req(_headers(None)), PAYLOAD)
        resp = await v1_compat._serve_via_router_stream(
            _Req(_headers(None)), {**PAYLOAD, "stream": True})
        [chunk async for chunk in resp.body_iterator]

    asyncio.run(go())
    assert [RUN_CEILING.lower() in r.headers for r in console.requests] == [False, False]


def test_both_hops_read_the_header_in_one_place() -> None:
    src = (_ROOT / "apps/services/gateway/gateway/routes/v1_compat.py").read_text(
        encoding="utf-8")
    assert src.count("run_ceiling=_run_ceiling(request)") == 2
    assert src.count('"x-cc-run-ceiling"') == 1


# ── 4. The Console's auth seam, with no database ────────────────────────────


def test_the_caller_field_defaults_to_no_ceiling() -> None:
    pytest.importorskip("fastapi")
    from customer_console.auth import Caller

    caller = Caller(organization_id="org", key_prefix="cc_live_x")
    assert caller.run_ceiling is None


@pytest.mark.parametrize(
    ("raw", "run", "want"),
    [(None, None, None), (None, "r", None), ("5", "r", Decimal("5")),
     ("900", "r", Decimal("900")), ("0.0001", "r", Decimal("0.0001"))],
)
def test_the_arm_parses_a_good_value(raw: str | None, run: str | None, want: Any) -> None:
    pytest.importorskip("fastapi")
    from customer_console.auth import _run_ceiling_of

    # The arm keeps the parsed value. The min with 500 is `_spend_refusal`'s.
    assert _run_ceiling_of(raw, run) == want


@pytest.mark.parametrize(
    ("raw", "run"),
    [("abc", "r"), ("", "r"), ("-1", "r"), ("NaN", "r"), ("5", None), ("5", ""),
     ("5", "  ")],
)
def test_the_arm_answers_400_and_never_the_default(raw: str, run: str | None) -> None:
    pytest.importorskip("fastapi")
    from customer_console.auth import _run_ceiling_of
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        _run_ceiling_of(raw, run)
    assert exc.value.status_code == 400
    assert exc.value.detail["reason"] == "run_ceiling_invalid"


def test_both_arms_read_the_header() -> None:
    pytest.importorskip("fastapi")
    import inspect

    from customer_console import auth

    for arm in (auth.organization_from_key, auth.organization_from_key_or_deployment):
        assert "x_cc_run_ceiling" in inspect.signature(arm).parameters, arm.__name__
    src = inspect.getsource(auth.organization_from_key_or_deployment)
    # The org-key shape passes the header on, and each `Caller` of the
    # deployment arm parses it.
    assert "x_cc_run_ceiling=x_cc_run_ceiling" in src
    assert src.count("run_ceiling=_run_ceiling_of(x_cc_run_ceiling, x_cc_run)") == 2
