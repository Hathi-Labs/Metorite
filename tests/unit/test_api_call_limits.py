"""API-3: the limits of an API call, hermetic (task 3789, D228).

Spec: ``project-docs/specs/api_connections.md`` §2.3 (steps 4 and 8, the
total) and §2.3a (decisions c, d and f). The routes are
``gateway/routes/api_connections.py``, and the pure steps are
``gateway/connections/api/call.py``.

Mutation table. Each row names a mutation and the test that it breaks.

| # | Mutation | Test that fails |
|---|---|---|
| L1 | ``_spend_limiter`` passes no namespace (the Studio counters) | ``test_the_api_counters_are_apart_from_the_studio_live_read`` |
| L2 | the 429 drops ``Retry-After`` | ``test_over_the_member_limit_is_429_with_retry_after`` |
| L3 | the budget check is ``>=`` one too late, or skipped | ``test_the_daily_budget_stops_a_loop`` |
| L4 | the budget uses INCR then EXPIRE, not one MULTI block | ``test_the_budget_is_one_multi_block_with_an_end`` |
| L5 | a Redis fault lets the call go | ``test_redis_down_fails_closed`` |
| L6 | ``_fetch`` ignores ``Answer.cut`` | ``test_a_cut_answer_is_refused_with_the_page_argument`` |
| L7 | ``_read_capped`` never sets ``cut`` | ``test_the_guard_marks_a_cut_answer`` |
| L8 | the total loop has no page bound | ``test_the_total_stops_at_the_page_limit_and_says_incomplete`` |
| L9 | an incomplete total gives ``total`` | ``test_a_limit_stops_the_total_and_gives_no_figure`` |
| L10 | ``plain_number`` takes a comma, an exponent or NaN | ``test_the_total_adds_plain_numbers_only`` |
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from urllib.parse import parse_qsl, urlsplit

import pytest

pytest.importorskip("gateway.routes.api_connections", reason="gateway not installed")

from gateway import outbound_guard
from gateway.connections import api as api_templates
from gateway.connections import mcp_tools
from gateway.connections.api import call
from gateway.connections.api.template import MAX_TOTAL_PAGES, Limits
from gateway.connections.api.zoho_books import ZOHO_BOOKS
from gateway.routes import api_connections as routes

from tests.unit import _api_call_fakes as f

READ = "/connections/api/zoho_books/read"


def _ok(body: bytes) -> outbound_guard.Answer:
    return outbound_guard.Answer(status_code=200, body=body)


# ── The limiter ──────────────────────────────────────────────────────────────


async def test_over_the_member_limit_is_429_with_retry_after(monkeypatch) -> None:
    _, wire, _ = f.hermetic(monkeypatch)
    for _ in range(mcp_tools.MEMBER_CALLS_PER_WINDOW):
        r = await f.call(f.user(), "POST", READ, body=f.read())
        assert r.status_code == 200, r.text
    r = await f.call(f.user(), "POST", READ, body=f.read())
    assert r.status_code == 429, r.text
    assert r.json()["detail"]["code"] == "rate_limit"
    assert int(r.headers["Retry-After"]) >= 1
    assert len(wire.sent) == mcp_tools.MEMBER_CALLS_PER_WINDOW


async def test_the_api_counters_are_apart_from_the_studio_live_read(monkeypatch) -> None:
    _, wire, raw = f.hermetic(monkeypatch)
    from acb_common import tenant_redis

    with tenant_redis.organization_scope(f.ORG_A):
        for _ in range(mcp_tools.MEMBER_CALLS_PER_WINDOW):
            await mcp_tools._spend(f.ORG_A, f.ADMIN)
        with pytest.raises(mcp_tools.LiveLimited):
            await mcp_tools._spend(f.ORG_A, f.ADMIN)
    r = await f.call(f.user(), "POST", READ, body=f.read())
    assert r.status_code == 200, r.text
    api_member = [k for k in raw.data if f":{routes.API_NAMESPACE}:rate:m:" in k]
    live_member = [k for k in raw.data if f":{mcp_tools.LIVE_NAMESPACE}:rate:m:" in k]
    assert len(api_member) == 1 and int(raw.data[api_member[0]]) == 1
    assert len(live_member) == 1 and int(raw.data[live_member[0]]) == 11
    assert len(wire.sent) == 1


async def test_find_and_describe_spend_one_limiter_call_and_no_budget(monkeypatch) -> None:
    _, wire, raw = f.hermetic(monkeypatch)
    for path in ("/connections/api/zoho_books/ops",
                 "/connections/api/zoho_books/ops/list_invoices"):
        r = await f.call(f.user(), "GET", path)
        assert r.status_code == 200, r.text
    member = [k for k in raw.data if f":{routes.API_NAMESPACE}:rate:m:" in k]
    assert len(member) == 1 and int(raw.data[member[0]]) == 2
    assert not [k for k in raw.data if routes.BUDGET_NAMESPACE in k]
    assert wire.sent == []


# ── The daily budget ─────────────────────────────────────────────────────────


def test_the_budget_is_half_the_daily_cap_or_the_default() -> None:
    assert call.daily_budget(ZOHO_BOOKS) == call.DEFAULT_DAILY_BUDGET == 1000
    capped = replace(ZOHO_BOOKS, limits=Limits(per_minute=100, per_day=2000))
    assert call.daily_budget(capped) == 1000
    assert call.daily_budget(replace(ZOHO_BOOKS, limits=Limits(per_minute=1, per_day=1))) == 1


async def test_the_daily_budget_stops_a_loop(monkeypatch) -> None:
    _, wire, _ = f.hermetic(monkeypatch)
    monkeypatch.setattr(call, "DEFAULT_DAILY_BUDGET", 2)
    for _ in range(2):
        assert (await f.call(f.user(), "POST", READ, body=f.read())).status_code == 200
    r = await f.call(f.user(), "POST", READ, body=f.read())
    assert r.status_code == 429, r.text
    assert r.json()["detail"]["code"] == "daily_budget"
    assert 1 <= int(r.headers["Retry-After"]) <= 86400
    assert len(wire.sent) == 2


async def test_the_budget_is_one_multi_block_with_an_end(monkeypatch) -> None:
    _, _, raw = f.hermetic(monkeypatch)
    now = datetime(2026, 11, 19, 23, 0, tzinfo=UTC)
    monkeypatch.setattr(routes, "_utcnow", lambda: now)
    assert (await f.call(f.user(), "POST", READ, body=f.read())).status_code == 200
    assert raw.multi == [(True, ["incr", "expireat"])]
    (key,) = [k for k in raw.data if routes.BUDGET_NAMESPACE in k]
    assert key.endswith(":2026-11-19")
    assert raw.ends[key] == int(datetime(2026, 11, 20, 1, 0, tzinfo=UTC).timestamp())
    day, _, left = call.utc_day(now)
    assert (day, left) == ("2026-11-19", 3600)


@pytest.mark.parametrize("where", ["limiter", "budget"])
async def test_redis_down_fails_closed(monkeypatch, where) -> None:
    _, wire, raw = f.hermetic(monkeypatch)
    if where == "limiter":
        raw.down = True
    else:
        from acb_common.tenant_redis import TenantRedis

        down = f.FakeRedis(down=True)
        monkeypatch.setattr(routes, "_redis", lambda: TenantRedis(down))
    r = await f.call(f.user(), "POST", READ, body=f.read())
    assert r.status_code == 503, r.text
    assert r.json()["detail"]["code"] == f"{where}_unavailable"
    assert wire.sent == []


# ── The cut answer and the cap ───────────────────────────────────────────────


async def test_the_guard_marks_a_cut_answer(monkeypatch) -> None:
    cap = outbound_guard.MAX_ANSWER_BYTES
    for size, cut in ((cap - 1, False), (cap, True), (cap + 1, True), (10, False)):
        f.mock_wire(monkeypatch, lambda req, n=size: f.streamed(b"x" * n))
        answer = await outbound_guard.request_https("GET", "https://www.zohoapis.in/x")
        assert (answer.cut, len(answer.body)) == (cut, min(size, cap)), size


async def test_a_cut_answer_is_refused_with_the_page_argument(monkeypatch) -> None:
    f.hermetic(monkeypatch)
    big = f.books_page(1, extra={"pad": "x" * (outbound_guard.MAX_ANSWER_BYTES + 10)})
    f.mock_wire(monkeypatch, lambda req: f.streamed(big))
    r = await f.call(f.user(), "POST", READ, body=f.read())
    assert r.status_code == 422, r.text
    assert r.json()["detail"] == {"code": "answer_too_large", "page_argument": "per_page"}


async def test_an_answer_over_the_row_cap_is_refused(monkeypatch) -> None:
    f.hermetic(monkeypatch, wire=f.Wire(answer=_ok(f.books_page(20))))
    small = replace(ZOHO_BOOKS, operations=tuple(
        replace(op, answer_cap=200) for op in ZOHO_BOOKS.operations))
    monkeypatch.setattr(api_templates, "template", lambda name: small if name == "zoho_books" else None)
    r = await f.call(f.user(), "POST", READ, body=f.read())
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["code"] == "answer_too_large"


# ── The gateway total ────────────────────────────────────────────────────────


async def test_the_total_adds_the_pages_and_stops_at_a_short_page(monkeypatch) -> None:
    pages = [_ok(f.books_page(50, amount="10.10")), _ok(f.books_page(50, amount=0.2)),
             _ok(f.books_page(3, amount=1))]
    _, wire, _ = f.hermetic(monkeypatch, wire=f.Wire(answer=pages))
    r = await f.call(f.user(), "POST", READ, body=f.read(total=True, status="paid"))
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["total"] == "518.00"
    assert (out["pages_read"], out["records"], out["numbers"], out["not_numbers"]) == (3, 103, 103, 0)
    assert out["incomplete"] is False and out["stopped_by"] is None
    assert out["currency"] == "INR" and out["partial_total"] is None
    queries = [dict(parse_qsl(urlsplit(s.url).query)) for s in wire.sent]
    assert [q["page"] for q in queries] == ["1", "2", "3"]
    assert all(q["per_page"] == "50" and q["status"] == "paid" for q in queries)


async def test_the_total_stops_at_the_page_limit_and_says_incomplete(monkeypatch) -> None:
    _, wire, _ = f.hermetic(monkeypatch, wire=f.Wire(answer=lambda sent: _ok(f.books_page(50))))
    r = await f.call(f.user(), "POST", READ, body=f.read(total=True))
    assert r.status_code == 200, r.text
    out = r.json()
    assert MAX_TOTAL_PAGES == 10
    assert out["pages_read"] == MAX_TOTAL_PAGES == len(wire.sent)
    assert out["incomplete"] is True and out["stopped_by"] == "page_limit"
    assert out["total"] is None and out["partial_total"] == "5000"


async def test_a_limit_stops_the_total_and_gives_no_figure(monkeypatch) -> None:
    f.hermetic(monkeypatch, wire=f.Wire(answer=lambda sent: _ok(f.books_page(50))))
    for _ in range(6):  # six of the ten calls of the member are gone
        assert (await f.call(f.user(), "POST", READ, body=f.read())).status_code == 200
    r = await f.call(f.user(), "POST", READ, body=f.read(total=True))
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["incomplete"] is True and out["stopped_by"] == "rate_limit"
    assert out["pages_read"] == 4 and out["total"] is None and out["partial_total"] == "2000"


async def test_a_mixed_currency_total_is_refused_with_the_list(monkeypatch) -> None:
    pages = [_ok(f.books_page(50, currency="INR")), _ok(f.books_page(2, currency="USD"))]
    f.hermetic(monkeypatch, wire=f.Wire(answer=pages))
    r = await f.call(f.user(), "POST", READ, body=f.read(total=True))
    assert r.status_code == 422, r.text
    assert r.json()["detail"] == {"code": "mixed_currency", "currencies": ["INR", "USD"]}


async def test_a_total_on_an_op_with_no_mark_is_refused(monkeypatch) -> None:
    _, wire, _ = f.hermetic(monkeypatch)
    r = await f.call(f.user(), "POST", READ, body=f.read("get_invoice", invoice_id="1", total=True))
    assert r.status_code == 422 and r.json()["detail"]["code"] == "no_total"
    assert wire.sent == []


@pytest.mark.parametrize(("value", "number"), [
    (7, "7"), ("12.50", "12.50"), ("-3", "-3"), ("0", "0"), ("0.5", "0.5"),
    ("1,000", None), ("1e3", None), ("NaN", None), ("Infinity", None), (" 5", None),
    ("007", None), ("5 m", None), (True, None), (None, None), ([1], None), ("", None),
    (chr(0x661), None), ("+5", None),
])
def test_the_total_adds_plain_numbers_only(value, number) -> None:
    got = call.plain_number(value)
    assert (None if got is None else str(got)) == number


def test_the_parse_refuses_nan_and_keeps_decimals_exact() -> None:
    with pytest.raises(call.AnswerRefused):
        call.parse(b'{"total": NaN}')
    data = call.parse(b'{"invoices": [{"total": 0.1}, {"total": 0.2}]}', exact=True)
    adder = call.Adder(ZOHO_BOOKS.op("list_invoices").total)
    adder.add_page(data)
    assert adder.answer(incomplete=False, stopped_by=None)["total"] == "0.3"
