"""Large CRM reads (WS-53 CRM-Q25e, tasks 3127 to 3129 and 2985 HRD-6).

Spec: ``project-docs/specs/crm_platform.md`` §18.11, the row CRM-Q25e.

Hermetic. The behaviour on a real database is proved in
``tests/unit/test_crm_aggregate_fields_r8.py``. Here: the tool text of a
split, of a finding and of a display value, the one long read on a 503 that
offers it, the budget of a read that ran out of time, and the SQL builders of
the split and of a lookup by a Metorite id.
"""

from __future__ import annotations

import re
from typing import Any

import pytest


def _total(**extra: Any) -> dict[str, Any]:
    return {"entity": "module:Invoices", "measure": "sum", "field": "Grand_Total",
            "field_type": "currency", "field_label": "Grand Total", "groups": [],
            "total": 335_683_347, "other": 0, "omitted": 0, "values": 14_313,
            "records": 14_313, **extra}


# ── The tool text (parts 2 and 4) ────────────────────────────────────────────


async def test_a_finding_goes_first_and_the_total_gets_its_display(monkeypatch) -> None:
    import acb_skills.crm_tools as tools

    sentence = ("13,222 of the 14,313 records (92%) have the one Invoice Date "
                "2026-10-17, and they hold 88% of the total.")

    async def _get(path: str, params: dict | None = None, timeout: float = 30.0) -> dict:
        return _total(findings=[{"kind": "concentration", "key": "2026-10-17",
                                 "records": 13_222, "share": 0.92, "sentence": sentence}])

    monkeypatch.setattr(tools, "_get", _get)
    out = await tools.crm_aggregate("module:Invoices", measure="sum", field="Grand_Total",
                                    field_filters={"Invoice_Date.from": "2026-10-01"})
    lines = out.splitlines()
    assert lines[0].startswith("FIRST, tell the member: " + sentence)
    assert lines[1].endswith("INR 335,683,347 across 14,313 matching records.")
    # The run's system is the default, "indian": lakh and crore grouping.
    assert lines[2] == ("Display: ₹33,56,83,347 (₹33.6\xa0Cr). "
                        "Quote this display value for the total, as written.")


async def test_a_small_total_has_no_display_and_no_finding_line(monkeypatch) -> None:
    import acb_skills.crm_tools as tools

    async def _get(path: str, params: dict | None = None) -> dict:
        return _total(total=99_999)

    monkeypatch.setattr(tools, "_get", _get)
    out = await tools.crm_aggregate("module:Invoices", measure="sum", field="Grand_Total")
    assert "Display" not in out and "FIRST" not in out


async def test_the_display_follows_the_number_system_of_the_run(monkeypatch) -> None:
    import importlib

    import acb_skills.crm_tools as tools

    artifact = importlib.import_module("acb_skills.write_artifact")
    monkeypatch.setattr(artifact, "run_numbering", lambda: "international")
    line = tools._display_line(335_683_347, money=True, code="INR")
    assert line is not None and line.startswith("Display: ₹335,683,347 (₹336M).")
    assert tools._display_line(999_999, money=True, code="INR") is None
    assert tools._display_line(None, money=True, code="INR") is None


async def test_a_split_names_each_field_in_one_call(monkeypatch) -> None:
    """Part 2: ONE call with fields, and one line for each field."""
    import acb_skills.crm_tools as tools

    seen: list[dict] = []
    split = [
        {"field": "Rent", "label": "Rent", "type": "currency", "total": 4500, "values": 3},
        {"field": "IP", "label": "IP", "type": "currency", "total": 300, "values": 2},
        {"field": "Units", "label": "Units", "type": "number", "total": 12.5, "values": 3},
    ]

    async def _get(path: str, params: dict | None = None) -> dict:
        seen.append(dict(params or {}))
        if params and params.get("group_field"):
            return _total(total=4500, records=3, split=split, group_field="Invoice_Date",
                          bucket="month", groups=[{
                              "key": "2026-10", "label": "2026-10", "value": 4500,
                              "split": {"Rent": 4500, "IP": 300, "Units": 12.5}}])
        return _total(total=4500, records=3, split=split)

    monkeypatch.setattr(tools, "_get", _get)
    out = await tools.crm_aggregate("module:Bills", measure="sum",
                                    fields=["Rent", "IP", " Units "])
    assert seen[0]["fields"] == "Rent,IP,Units" and "field" not in {
        k for k, v in seen[0].items() if v is not None}
    assert out.splitlines()[0] == (
        "Bills, sum of each field: Rent INR 4,500; IP INR 300; Units 12.5, "
        "across 3 matching records.")
    grouped = await tools.crm_aggregate("module:Bills", measure="sum", fields=["Rent", "IP"],
                                        group_field="Invoice_Date", bucket="month")
    assert "- 2026-10: Rent INR 4,500; IP INR 300; Units 12.5" in grouped.splitlines()
    assert "Total: Rent INR 4,500; IP INR 300; Units 12.5." in grouped.splitlines()


def test_the_docstring_asks_for_a_split_in_one_call() -> None:
    import acb_skills.crm_tools as tools

    doc = " ".join((tools.crm_aggregate.__doc__ or "").split())
    assert "in ONE call, never one call for each field" in doc
    assert "Quote the Display value" in doc and "starts with FIRST" in doc


# ── The long read (part 5) ───────────────────────────────────────────────────


class _Resp:
    def __init__(self, status: int, body: dict) -> None:
        self.status_code = status
        self._body = body
        self.text = str(body)

    def json(self) -> dict:
        return self._body


def _timeout(long_read: bool) -> _Resp:
    detail: dict[str, Any] = {"code": "aggregate_timeout",
                              "message": "The total took longer than 10 seconds."}
    if long_read:
        detail["long_read"] = True
    return _Resp(503, {"detail": detail})


def _fake_gateway(monkeypatch, answers: list[_Resp], calls: list[tuple[dict, float]]) -> None:
    import acb_skills.crm_tools as tools

    async def _get(path: str, params: dict | None = None, timeout: float = 30.0) -> Any:
        if path != "/crm/aggregate":
            return {}  # the short-read note and the other hints read nothing here
        calls.append((dict(params or {}), timeout))
        return tools._answer_of(answers.pop(0), path, "GET")

    monkeypatch.setattr(tools, "_get", _get)
    monkeypatch.setattr(tools, "_current_user_email", lambda: "m@x.test")


async def test_a_503_that_offers_the_long_read_calls_once_more_in_long_mode(monkeypatch) -> None:
    import acb_skills.crm_tools as tools

    calls: list[tuple[dict, float]] = []
    _fake_gateway(monkeypatch, [_timeout(True), _Resp(200, _total(total=50))], calls)
    out = await tools.crm_aggregate("module:Invoices", measure="sum", field="Grand_Total")
    assert "INR 50 across" in out
    assert [c[0].get("long_read") for c in calls] == [None, "true"]
    assert calls[1][1] == tools.LONG_READ_TIMEOUT_S


async def test_with_the_flag_empty_a_503_is_the_refusal_of_before(monkeypatch) -> None:
    """The route offers no long read off ``CRM_LONG_READ_ORGS``: one call."""
    import acb_skills.crm_tools as tools

    calls: list[tuple[dict, float]] = []
    _fake_gateway(monkeypatch, [_timeout(False)], calls)
    out = await tools.crm_aggregate("module:Invoices", measure="sum", field="Grand_Total")
    assert len(calls) == 1 and "10 seconds" in out


async def test_a_long_read_keeps_its_count_even_when_it_runs_out_of_time(monkeypatch) -> None:
    """Review item 6: a long read that runs out of time spends its count, and
    its refusal asks for a narrower read, never for the same read again."""
    import acb_skills.crm_tools as tools

    reads = tools.RunReads()
    monkeypatch.setattr(tools, "_run_reads", lambda: reads)
    calls: list[tuple[dict, float]] = []
    long_timeout = _Resp(503, {"detail": {
        "code": "aggregate_timeout",
        "message": "The total took longer than 3 minutes, so the CRM stopped it. Narrow "
                   "the date range, or split it into shorter ranges."}})
    _fake_gateway(monkeypatch, [_timeout(True), long_timeout] * 9, calls)
    outs = [await tools.crm_aggregate("module:Invoices", measure="sum", field="Grand_Total")
            for _ in range(9)]
    # Eight calls ran a long read and spent the budget, so the ninth reads nothing.
    assert len(calls) == 16 and reads.aggregates == tools.AGGREGATE_BUDGET
    assert outs[-1].startswith("BUDGET:")
    assert "shorter ranges" in outs[0] and "ask again" not in outs[0].lower()


async def test_a_10_s_timeout_with_no_long_read_spends_no_count(monkeypatch) -> None:
    """The 10 s read ran out of time, and the call could take no long read
    (here: a headless turn). It read nothing, so it spends no count."""
    import acb_skills.crm_tools as tools

    reads = tools.RunReads()
    monkeypatch.setattr(tools, "_run_reads", lambda: reads)
    monkeypatch.setattr(tools, "_headless_turn", lambda: True)
    calls: list[tuple[dict, float]] = []
    _fake_gateway(monkeypatch, [_timeout(True)] * 9, calls)
    for _ in range(9):
        await tools.crm_aggregate("module:Invoices", measure="sum", field="Grand_Total")
    # No long read in a headless turn, and no call spent a count.
    assert [c[0].get("long_read") for c in calls] == [None] * 9
    assert reads.aggregates == 0 and reads.total == 9


async def test_a_headless_turn_takes_no_long_read() -> None:
    """The run ceiling marks the headless turn (``acb_llm.attribution``)."""
    from decimal import Decimal

    import acb_skills.crm_tools as tools
    from acb_llm.attribution import run_ceiling_scope

    assert not tools._headless_turn()
    with run_ceiling_scope(Decimal("5")):
        assert tools._headless_turn()
        call = tools._ToolCall(deadline=tools.time.monotonic() + tools.TOOL_CALL_SECONDS)
        assert not tools._long_read_open(call)


async def test_one_tool_call_takes_at_most_one_long_read(monkeypatch) -> None:
    """A term total or an answer-first total makes several reads in ONE call.
    Only the first may escalate, so the call stays under its deadline."""
    import acb_skills.crm_tools as tools

    calls: list[tuple[dict, float]] = []
    _fake_gateway(monkeypatch, [_timeout(True), _Resp(200, _total(total=7)), _timeout(True)],
                  calls)
    token = tools._CALL.set(tools._ToolCall(
        deadline=tools.time.monotonic() + tools.TOOL_CALL_SECONDS))
    try:
        first = await tools._aggregate_read({"entity": "module:Invoices"})
        with pytest.raises(tools._GatewayError) as exc:
            await tools._aggregate_read({"entity": "module:Invoices"})
    finally:
        tools._CALL.reset(token)
    assert first["total"] == 7 and exc.value.code == "aggregate_timeout"
    assert [c[0].get("long_read") for c in calls] == [None, "true", None]


async def test_every_read_of_a_call_ends_by_its_deadline(monkeypatch) -> None:
    import acb_skills.crm_tools as tools

    calls: list[tuple[dict, float]] = []
    _fake_gateway(monkeypatch, [_timeout(True), _Resp(200, _total(total=7))], calls)
    # 150 s left: the 10 s read gets 30 s, and no long read fits.
    token = tools._CALL.set(tools._ToolCall(deadline=tools.time.monotonic() + 150))
    try:
        with pytest.raises(tools._GatewayError):
            await tools._aggregate_read({"entity": "module:Invoices"})
    finally:
        tools._CALL.reset(token)
    assert [c[0].get("long_read") for c in calls] == [None]
    # The real _get cuts each httpx limit to the time that the call has left.
    token = tools._CALL.set(tools._ToolCall(deadline=tools.time.monotonic() + 12))
    try:
        assert 11 < tools._within_call(30.0, "/crm/aggregate") <= 12
    finally:
        tools._CALL.reset(token)
    assert tools._within_call(30.0, "/crm/aggregate") == 30.0  # outside a call
    # A call with its time used reads nothing more.
    token = tools._CALL.set(tools._ToolCall(deadline=tools.time.monotonic() + 0.5))
    try:
        with pytest.raises(tools._GatewayError) as exc:
            tools._within_call(30.0, "/crm/aggregate")
    finally:
        tools._CALL.reset(token)
    assert exc.value.code == "call_deadline" and tools.CALL_DEADLINE_LINE in exc.value.detail


def test_the_deadline_of_a_call_stays_under_the_tool_limit() -> None:
    """The orchestrator stops a tool at 300 s (COPILOT_TOOL_TIMEOUT_SECONDS).
    A call holds one 10 s read (30 s of httpx) and one long read (200 s)."""
    import acb_skills.crm_tools as tools

    assert tools.TOOL_CALL_SECONDS < 300
    assert tools.TOOL_CALL_SECONDS >= 30 + tools.LONG_READ_TIMEOUT_S


async def test_the_display_names_each_currency_and_never_adds_them(monkeypatch) -> None:
    """Review item 1: INR and USD deals give one display entry for each."""
    import acb_skills.crm_tools as tools

    async def _get(path: str, params: dict | None = None, timeout: float = 30.0) -> dict:
        return {"entity": "deals", "measure": "sum_amount", "groups": [], "total": 1_234_567,
                "currency": "INR", "others": [{"currency": "USD", "total": 4500, "other": 0}],
                "other": 0, "omitted": 0, "records": 4}

    monkeypatch.setattr(tools, "_get", _get)
    out = await tools.crm_aggregate("deals", measure="sum_amount")
    [line] = [x for x in out.splitlines() if x.startswith("Display:")]
    assert line == ("Display: ₹12,34,567 (₹12.3\xa0L) and USD 4,500 (USD 4.5K). Quote each "
                    "display value with its currency, as written. Never add two currencies "
                    "together.")


def test_a_split_groups_its_values_in_the_number_system_of_the_run(monkeypatch) -> None:
    import importlib

    import acb_skills.crm_tools as tools

    split = [{"field": "Rent", "label": "Rent", "type": "currency", "total": 1_234_567,
              "other": 150_000},
             {"field": "Units", "label": "Units", "type": "number", "total": 123456.5,
              "other": 2}]
    assert tools._split_text(split, None, "sum") == "Rent INR 12,34,567; Units 1,23,456.5"
    assert tools._split_text(split, None, "sum", key="other") == "Rent INR 1,50,000; Units 2"
    artifact = importlib.import_module("acb_skills.write_artifact")
    monkeypatch.setattr(artifact, "run_numbering", lambda: "international")
    assert tools._split_text(split, None, "sum") == "Rent INR 1,234,567; Units 123,456.5"


async def test_a_timeout_with_the_flag_empty_still_spends_the_budget(monkeypatch) -> None:
    """Today's behaviour, exactly, for an org off the list."""
    import acb_skills.crm_tools as tools

    reads = tools.RunReads()
    monkeypatch.setattr(tools, "_run_reads", lambda: reads)
    calls: list[tuple[dict, float]] = []
    _fake_gateway(monkeypatch, [_timeout(False)], calls)
    await tools.crm_aggregate("module:Invoices", measure="sum", field="Grand_Total")
    assert reads.aggregates == 1


def test_the_httpx_limit_covers_the_long_budget_of_the_route() -> None:
    import acb_skills.crm_tools as tools
    from gateway.routes.crm import aggregate

    assert tools.LONG_READ_TIMEOUT_S >= aggregate.LONG_READ_SECONDS + 10
    assert aggregate.LONG_READ_SECONDS <= 180


# ── The SQL builders (parts 2 and 3) ─────────────────────────────────────────


def _scope() -> Any:
    from gateway.routes.crm import field_sql as fs

    refs = {k: fs.FieldRef(key=k, type=t, label=k, lookup_module=m) for k, t, m in (
        ("Rent", "currency", None), ("IP", "currency", None), ("EB", "currency", None),
        ("Units", "number", None), ("Status", "select", None),
        ("Operators", "lookup", "Operators"))}
    return fs.FieldScope(column="fields", owner="module:Bills", fields=refs)


def test_a_split_binds_each_field_under_its_own_name() -> None:
    from gateway.routes.crm import field_sql as fs

    scope = _scope()
    refs = fs.check_split(scope, "Rent, IP,EB")
    assert [r.key for r in refs] == ["Rent", "IP", "EB"]
    parts = fs.split_sql(scope, refs[1:], "sum")
    names = [n for p in parts for n in p.params]
    assert names == ["agf_m1_0", "agf_m2_0"] and len(set(names)) == len(names)
    for part in parts:
        assert part.source == fs.DETOAST_JOIN
        assert not re.search(r"(?<![.\w])fields ->", part.value)


@pytest.mark.parametrize("names", ["Rent", "Rent,Rent", "Rent,IP,EB,Units,Rent,IP",
                                   "Rent,IP,EB,Units,A,B", "Rent,Status", "Rent,,IP"])
def test_a_bad_split_is_a_422(names: str) -> None:
    from fastapi import HTTPException
    from gateway.routes.crm import field_sql as fs

    with pytest.raises(HTTPException) as exc:
        fs.check_split(_scope(), names)
    assert exc.value.status_code == 422


def test_a_lookup_filter_by_a_metorite_id_also_matches_its_zoho_id() -> None:
    from gateway.routes.crm import field_sql as fs

    scope = _scope()
    plain = fs.module_where(scope, "Bills", [("Operators", "VODAFONE IDEA LIMITED")], zone="UTC")
    with_id = fs.module_where(scope, "Bills", [("Operators", "77309599-0000-4000-8000-000000000001")],
                              zone="UTC", ids={0: "4567000001"})
    assert "agf_x0" not in plain.sql
    assert ":agf_x0" in with_id.sql and with_id.params["agf_x0"] == "4567000001"
    assert "4567000001" not in with_id.sql and "77309599" not in with_id.sql


def test_only_a_uuid_of_a_lookup_filter_asks_for_its_zoho_id() -> None:
    from gateway.routes.crm import field_sql as fs

    scope = _scope()
    pairs = [("Operators", "77309599-0000-4000-8000-000000000001"),
             ("Operators", "VODAFONE"), ("Status", "77309599-0000-4000-8000-000000000001"),
             ("Operators", "4567000001")]
    assert fs.lookup_targets(scope, pairs) == {
        0: ("Operators", "77309599-0000-4000-8000-000000000001")}


# ── The guard of the long read (part 5) ──────────────────────────────────────


async def test_a_second_long_read_of_one_org_waits_inside_its_budget() -> None:
    from fastapi import HTTPException
    from gateway.routes.crm import aggregate as agg

    async with agg.long_read_slot("org-a", 5) as left:
        assert 4 < left <= 5
        with pytest.raises(HTTPException) as exc:
            async with agg.long_read_slot("org-a", 0.05):
                pass
        assert exc.value.status_code == 503 and exc.value.detail["code"] == "aggregate_busy"
    # Free again: the next long read of the org takes it at once.
    async with agg.long_read_slot("org-a", 0.05):
        pass


async def test_the_process_runs_at_most_its_long_read_slots() -> None:
    import asyncio

    from fastapi import HTTPException
    from gateway.routes.crm import aggregate as agg

    assert agg.LONG_READ_SLOTS == 2
    async with agg.long_read_slot("org-a", 5), agg.long_read_slot("org-b", 5):
        with pytest.raises(HTTPException) as exc:
            async with agg.long_read_slot("org-c", 0.05):
                pass
        assert exc.value.detail["code"] == "aggregate_busy"

    async def hold() -> None:
        async with agg.long_read_slot("org-a", 5):
            await asyncio.sleep(0.05)

    holder = asyncio.create_task(hold())
    await asyncio.sleep(0.01)
    # A waiter of the same org gets the read when the first one ends.
    async with agg.long_read_slot("org-a", 5) as left:
        assert left > 4
    await holder


def test_the_long_reads_stay_below_the_pool_of_one_process() -> None:
    """The pool is db_pool_size + db_max_overflow (7 + 2 = 9), and background
    work may hold db_background_max (4). The long reads leave room for pages."""
    from acb_common import get_settings
    from gateway.routes.crm import aggregate as agg

    s = get_settings()
    pool = s.db_pool_size + s.db_max_overflow
    assert pool - s.db_background_max > agg.LONG_READ_SLOTS


def test_the_flag_is_empty_by_default_and_names_each_org(monkeypatch) -> None:
    from acb_common import get_settings
    from gateway.routes.crm import flags

    from tests.unit._crm_fakes import crm_switch_on

    crm_switch_on(monkeypatch, "*")
    settings = get_settings()
    assert settings.crm_long_read_orgs == ""
    assert not flags.long_read_allowed("org-a")
    monkeypatch.setattr(settings, "crm_long_read_orgs", "ORG-A, org-b", raising=False)
    assert flags.long_read_allowed("org-a") and flags.long_read_allowed("org-b")
    assert not flags.long_read_allowed("org-c") and not flags.long_read_allowed(None)
    monkeypatch.setattr(settings, "crm_long_read_orgs", "*", raising=False)
    assert flags.long_read_allowed("org-c")
    # The CRM switch still decides first.
    monkeypatch.setattr(settings, "crm_enabled", False, raising=False)
    assert not flags.long_read_allowed("org-c")


def test_the_aggregate_holds_the_grouping_sets_and_the_split_in_one_statement() -> None:
    """Parts 2 and 4: the split columns and the day set join the ONE
    statement of the read. The field SQL itself stays in field_sql."""
    from pathlib import Path

    source = (Path(__file__).resolve().parents[2] / "apps" / "services" / "gateway"
              / "gateway" / "routes" / "crm" / "aggregate.py").read_text(encoding="utf-8")
    assert source.count("GROUP BY GROUPING SETS") == 1
    assert "count(*) AS c{more}{grouping} FROM {table}{source}{where_sql}{sets}" in source


# ── The fences of the review (item 8) ────────────────────────────────────────


async def test_the_lookup_read_names_the_org_and_binds_every_value() -> None:
    """M2: the read of the Zoho id names the org beside RLS, and binds the
    org, the module and the id. None of them reaches the SQL text."""
    from gateway.routes.crm import field_sql as fs

    assert "organization_id = CAST(:agf_org AS uuid)" in fs._LOOKUP_ID_SQL
    ident = "77309599-0000-4000-8000-000000000001"
    seen: list[tuple[str, dict]] = []

    class _Row:
        def scalar(self) -> str:
            return "4567000001"

    class _DB:
        async def execute(self, statement: Any, params: dict) -> _Row:
            seen.append((str(statement), dict(params)))
            return _Row()

    found = await fs.lookup_ids(_DB(), "org-a", _scope(), [("Operators", ident)])
    assert found == {0: "4567000001"}
    [(sql, params)] = seen
    assert params == {"agf_org": "org-a", "agf_module": "Operators", "agf_id": ident}
    assert ident not in sql and "org-a" not in sql and "Operators" not in sql


def test_a_read_that_reads_no_field_gets_no_day_set() -> None:
    """Review P2: the day check adds no detoast to a read with no field read
    (a plain count, a count by owner, a fixed month)."""
    from gateway.routes.crm import aggregate as agg
    from gateway.routes.crm import field_sql as fs

    day = fs.FieldRef(key="Invoice_Date", type="date", label="Invoice Date")
    scope = fs.FieldScope(column="fields", owner="module:Invoices",
                          fields={"Invoice_Date": day, "@date": day})
    bound: dict = {}
    assert agg._day_of(scope, "Invoices", None, [], "UTC", bound, reads=False) == (None, "", "")
    assert bound == {}
    found, key, source = agg._day_of(scope, "Invoices", None, [], "UTC", bound, reads=True)
    assert found is day and key and source == fs.DETOAST_JOIN


# ── The last review (2026-11-14) ─────────────────────────────────────────────


def test_a_month_edge_batch_that_the_day_set_held_back_is_no_month_finding() -> None:
    """A fiscal-year count by month with 400 of 550 invoices on 2026-03-31.
    The day set ran, and rule 5 held the day back. The bucket pass must not
    bring the batch back as "408 of the 550 records fall in 2026-03"."""
    from gateway.routes.crm import aggregate as agg
    from gateway.routes.crm import field_sql as fs

    day = fs.FieldRef(key="Invoice_Date", type="date", label="Invoice Date")
    days = [(f"2025-{m:02d}-15", 13.0, 13) for m in range(4, 13)]
    days += [("2026-01-15", 13.0, 13), ("2026-02-15", 13.0, 13), ("2026-03-10", 7.0, 7),
             ("2026-03-31", 400.0, 400)]
    months = {k[:7]: 0 for k, _v, _c in days}
    for k, _v, c in days:
        months[k[:7]] += c
    assert sum(months.values()) == 550 and months["2026-03"] == 407
    values = {k: agg._Acc(value=float(c), rows=c) for k, c in months.items()}
    with_days = agg._Read(values, {}, {}, None, day, "UTC", {}, day=day, days=days)
    assert agg._findings(with_days, "count", "month", None) == []
    # With no day set, the bucket pass still reads the months.
    no_days = agg._Read(values, {}, {}, None, day, "UTC", {})
    [found] = agg._findings(no_days, "count", "month", None)
    assert found["key"] == "2026-03" and "fall in 2026-03, one month of" in found["sentence"]


async def test_a_whatsapp_or_text_run_takes_no_long_read(monkeypatch) -> None:
    """Its whole-run cap (240 s, or 420 s) cannot hold a 180 s read."""
    import acb_skills.crm_tools as tools

    call = tools._ToolCall(deadline=tools.time.monotonic() + tools.TOOL_CALL_SECONDS)
    assert tools._long_read_open(call)
    token = tools.open_text_asks("whatsapp")
    try:
        assert tools.run_text_asks() is not None
        assert not tools._long_read_open(call)
    finally:
        tools.release_text_asks(token)
    assert tools._long_read_open(call)
    # Through the tool: the 503 that offers the long read gives one call only.
    reads = tools.RunReads()
    monkeypatch.setattr(tools, "_run_reads", lambda: reads)
    monkeypatch.setattr(tools, "run_text_asks", lambda: object())
    calls: list[tuple[dict, float]] = []
    _fake_gateway(monkeypatch, [_timeout(True)], calls)
    await tools.crm_aggregate("module:Invoices", measure="sum", field="Grand_Total")
    assert [c[0].get("long_read") for c in calls] == [None] and reads.aggregates == 0
