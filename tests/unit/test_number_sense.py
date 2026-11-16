"""Number sense: the concentration check (task 3129, CRM-Q25e part 4).

Spec: ``project-docs/specs/crm_platform.md`` §18.11, the row CRM-Q25e.
``acb_common.number_sense`` is pure, so each case is a list of buckets and
the findings that it gives. No database and no settings.

The five cases of the review of 2026-11-13 are pinned here: four normal
shapes that give NO finding, and the DEWiN case that MUST give one.
"""

from __future__ import annotations

import time

from acb_common import number_sense as ns


def _october(spike_day: str, spike: int, others: dict[str, int]) -> list:
    """Each day of *others* with its records, then *spike_day*. Each record
    holds a value of 100."""
    rows = [(day, count * 100.0, count) for day, count in others.items()]
    return [*rows, (spike_day, spike * 100.0, spike)]


# ── The five cases of the review ─────────────────────────────────────────────


def test_dewin_one_mass_updated_day_is_a_finding() -> None:
    """13,222 of 14,313 October records on 2026-10-17: the production case."""
    days = [f"2026-10-{d:02d}" for d in range(1, 32) if d != 17]
    others = {day: 36 for day in days}
    others["2026-10-31"] += 1_091 - sum(others.values())
    rows = _october("2026-10-17", 13_222, others)
    assert sum(c for _k, _v, c in rows) == 14_313
    [one] = ns.findings(rows, label="Invoice Date")
    assert (one.kind, one.key, one.records, one.total_records) == (
        ns.CONCENTRATION, "2026-10-17", 13_222, 14_313)
    assert one.sentence == (
        "13,222 of the 14,313 records (92%) have the one Invoice Date 2026-10-17, "
        "and they hold 92% of the total.")
    assert one.wire()["kind"] == "concentration" and one.wire()["records"] == 13_222


def test_rent_on_the_first_day_of_the_month_is_no_finding() -> None:
    """95 rent invoices on 2026-10-01 and 5 on other days: a billing day."""
    rows = _october("2026-10-01", 95, {f"2026-10-{d:02d}": 1 for d in (3, 8, 12, 19, 25)})
    assert ns.findings(rows, label="Invoice Date") == []


def test_a_year_end_batch_on_the_last_day_is_no_finding() -> None:
    """400 of 550 on 2026-03-31: the close of a month, a quarter and a year."""
    others = {f"2026-03-{d:02d}": 6 for d in range(2, 27)}
    rows = [*((k, 1.0, v) for k, v in others.items()), ("2026-03-31", 1.0, 400)]
    assert sum(c for _k, _v, c in rows) == 550
    assert ns.findings(rows, label="Invoice Date") == []


def test_two_buckets_are_no_finding() -> None:
    """A year of 3 records beside a year of 60, and last month beside the
    second day of this month: nothing normal to compare with."""
    assert ns.findings([("2025", 3.0, 3), ("2026", 60.0, 60)], bucket="year") == []
    assert ns.findings([("2026-10", 9.0, 900), ("2026-11", 1.0, 40)], bucket="month") == []


def test_a_bucket_is_named_as_a_bucket_never_as_a_date() -> None:
    rows = [("2026-10", 400.0, 400), *[(f"2026-0{m}", 10.0, 10) for m in range(1, 7)]]
    [one] = ns.findings(rows, label="Invoice Date", bucket="month")
    assert one.sentence == ("400 of the 460 records (87%) fall in 2026-10, one month of "
                            "Invoice Date, and they hold 87% of the total.")
    assert "the one Invoice Date 2026" not in one.sentence
    # Rule 5 holds for a day only: a month key is never a month edge.
    assert not ns.month_edge("2026-10") and ns.month_edge("2026-10-31")
    assert ns.month_edge("2024-02-29") and not ns.month_edge("2026-02-29")


# ── Each rule ────────────────────────────────────────────────────────────────


def test_an_even_spread_gives_no_finding() -> None:
    others = {f"2026-10-{d:02d}": 36 for d in range(2, 30) if d != 17}
    assert ns.findings(_october("2026-10-17", 40, others)) == []


def test_each_rule_holds_back_a_finding() -> None:
    four = [(f"d{i}", 1.0, 1) for i in range(4)]
    # Rule 1: 3 other buckets are too few, and 4 are enough.
    assert ns.findings([("a", 1.0, 500), *four[:3]]) == []
    assert len(ns.findings([("a", 1.0, 500), *four])) == 1
    # Rule 2: 19 records are too few, even when they are all of a spike.
    assert ns.findings([("a", 1.0, 19), *four]) == []
    assert len(ns.findings([("a", 1.0, 20), *four])) == 1
    # Rule 3: a spike under 30 % of the records is no finding.
    rows = [("spike", 1.0, 100), *[(f"d{i}", 1.0, 10) for i in range(30)]]
    assert ns.CONCENTRATION_SHARE > 100 / 400 and ns.findings(rows) == []
    # Rule 4: exactly 5 times the median is no finding, and more than 5 is.
    tens = [(f"d{i}", 1.0, 10) for i in range(4)]
    assert ns.findings([("a", 1.0, 50), *tens]) == []
    assert len(ns.findings([("a", 1.0, 51), *tens])) == 1


def test_two_spikes_give_two_findings_the_largest_first() -> None:
    rows = [("x", 1.0, 100), ("y", 1.0, 120), *[(f"d{i}", 1.0, 2) for i in range(10)]]
    assert [f.key for f in ns.findings(rows)] == ["y", "x"]


def test_a_value_that_does_not_add_up_gives_no_value_share() -> None:
    rows = [("a", 7.5, 80), *[(f"d{i}", 3.0, 2) for i in range(5)]]
    found = ns.findings(rows, label="Day", summed=False)
    assert found and found[0].value_share is None
    assert found[0].sentence == "80 of the 90 records (89%) have the one Day a."
    # No value at all (a count with no measure): no value share either.
    none = [("a", None, 80), *[(f"d{i}", None, 2) for i in range(5)]]
    assert ns.findings(none)[0].value_share is None


def test_a_bucket_with_no_key_or_no_record_is_no_bucket() -> None:
    rows = [(None, 9.0, 900), ("", 9.0, 900), ("z", 1.0, 0), ("b", 1.0, 30),
            *[(f"d{i}", 1.0, 1) for i in range(4)]]
    found = ns.findings(rows)
    assert [f.key for f in found] == ["b"] and found[0].total_records == 34


def test_the_thresholds_are_named_constants() -> None:
    assert (ns.CONCENTRATION_RATIO, ns.CONCENTRATION_SHARE, ns.CONCENTRATION_MIN_RECORDS,
            ns.CONCENTRATION_MIN_OTHERS, ns.MONTH_EDGE_IS_NORMAL,
            ns.CONCENTRATION_MAX_BUCKETS) == (5.0, 0.30, 20, 4, True, 20_000)


# ── The cost ─────────────────────────────────────────────────────────────────


def test_ten_thousand_buckets_take_under_fifty_milliseconds() -> None:
    """The route runs the check on the event loop of the gateway. The review
    measured 3.5 s for 7,300 buckets before the fix."""
    rows = [(f"k{i:05d}", float(i), 1 + i % 7) for i in range(10_000)]
    rows.append(("spike", 1.0, 40_000))
    ns.findings(rows)  # warm the imports
    best = float("inf")
    for _ in range(3):
        start = time.perf_counter()
        found = ns.findings(rows)
        best = min(best, time.perf_counter() - start)
    assert [f.key for f in found] == ["spike"]
    assert best < 0.050, best


def test_past_the_cap_the_check_reads_the_largest_buckets() -> None:
    rows = [(f"k{i:05d}", 1.0, 1) for i in range(ns.CONCENTRATION_MAX_BUCKETS + 50)]
    rows.append(("spike", 1.0, 20_000))
    [one] = ns.findings(rows)
    # The total counts every bucket, the ones past the cap too.
    assert one.total_records == ns.CONCENTRATION_MAX_BUCKETS + 50 + 20_000


def test_the_module_is_pure() -> None:
    """No I/O, no settings and no model: the module imports none of them."""
    import ast
    from pathlib import Path

    source = Path(ns.__file__).read_text(encoding="utf-8")
    imported = {
        (node.module or "") if isinstance(node, ast.ImportFrom) else alias.name
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Import | ast.ImportFrom)
        for alias in node.names
    }
    assert imported <= {"__future__", "bisect", "collections.abc", "dataclasses",
                        "datetime", "heapq", "re", "typing"}
