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


async def test_a_long_read_that_runs_out_of_time_spends_no_aggregate_budget(monkeypatch) -> None:
    import acb_skills.crm_tools as tools

    reads = tools.RunReads()
    monkeypatch.setattr(tools, "_run_reads", lambda: reads)
    calls: list[tuple[dict, float]] = []
    _fake_gateway(monkeypatch, [_timeout(True), _timeout(False)] * 9, calls)
    for _ in range(9):
        await tools.crm_aggregate("module:Invoices", measure="sum", field="Grand_Total")
    # Nine reads past the aggregate budget of 8, and none was refused.
    assert len(calls) == 18 and reads.aggregates == 0 and reads.total == 9
    # A read that answers still counts.
    _fake_gateway(monkeypatch, [_Resp(200, _total(total=5))], calls)
    await tools.crm_aggregate("module:Invoices", measure="sum", field="Grand_Total")
    assert reads.aggregates == 1


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
