"""Number sense: the concentration check (task 3129, CRM-Q25e part 4).

Spec: ``project-docs/specs/crm_platform.md`` §18.11, the row CRM-Q25e.
``acb_common.number_sense`` is pure, so each case is a list of buckets and
the findings that it gives. No database and no settings.
"""

from __future__ import annotations

from acb_common import number_sense as ns


def _days(spike: int, normal: int, days: int, *, key: str = "2026-10-17") -> list:
    """*days* days of *normal* records each, then one day of *spike*. Each
    record holds a value of 100."""
    rows = [(f"2026-10-{d:02d}", normal * 100.0, normal) for d in range(1, days + 1)
            if f"2026-10-{d:02d}" != key]
    return [*rows, (key, spike * 100.0, spike)]


def test_the_october_mass_update_is_one_finding() -> None:
    """The production case of 2026-11-13: 13,222 of 14,313 records on one day."""
    rows = _days(13_222, 36, 31)
    rows[0] = (rows[0][0], 37 * 100.0, 37)  # 30 days of about 36 records
    total = sum(c for _k, _v, c in rows)
    found = ns.findings(rows, label="Invoice Date")
    assert len(found) == 1
    one = found[0]
    assert (one.kind, one.key, one.records, one.total_records) == (
        ns.CONCENTRATION, "2026-10-17", 13_222, total)
    assert round(one.share, 2) == round(13_222 / total, 2)
    assert one.value_share is not None and round(one.value_share, 2) == round(one.share, 2)
    assert one.sentence == (
        f"13,222 of the {total:,} records (92%) have the one Invoice Date 2026-10-17, "
        "and they hold 92% of the total.")
    assert one.wire()["kind"] == "concentration" and one.wire()["records"] == 13_222


def test_an_even_spread_gives_no_finding() -> None:
    assert ns.findings(_days(40, 36, 31)) == []


def test_each_month_on_its_first_day_gives_no_finding() -> None:
    """Six months of invoices, each month on its first day: rule 4 sees six
    buckets of the same size, so nothing concentrates."""
    rows = [(f"2026-{m:02d}-01", 5000.0, 50) for m in range(5, 11)]
    assert ns.findings(rows) == []


def test_each_rule_holds_back_a_finding() -> None:
    # Rule 1: one bucket has nothing to compare with.
    assert ns.findings([("2026-10-17", 1.0, 500)]) == []
    # Rule 2: 19 records are too few, even when they are all of a spike.
    assert ns.findings([("a", 1.0, 19), ("b", 1.0, 1), ("c", 1.0, 1)]) == []
    assert len(ns.findings([("a", 1.0, 20), ("b", 1.0, 1), ("c", 1.0, 1)])) == 1
    # Rule 3: a spike under 30 % of the records is no finding.
    rows = [("spike", 1.0, 100), *[(f"d{i}", 1.0, 10) for i in range(30)]]
    assert ns.CONCENTRATION_SHARE > 100 / 400 and ns.findings(rows) == []
    # Rule 4: exactly 5 times the median is no finding, and more than 5 is.
    assert ns.findings([("a", 1.0, 50), ("b", 1.0, 10), ("c", 1.0, 10)]) == []
    assert len(ns.findings([("a", 1.0, 51), ("b", 1.0, 10), ("c", 1.0, 10)])) == 1


def test_two_spikes_give_two_findings_the_largest_first() -> None:
    rows = [("x", 1.0, 100), ("y", 1.0, 120), *[(f"d{i}", 1.0, 2) for i in range(10)]]
    assert [f.key for f in ns.findings(rows)] == ["y", "x"]


def test_a_value_that_does_not_add_up_gives_no_value_share() -> None:
    rows = [("a", 7.5, 80), ("b", 3.0, 5), ("c", 2.0, 5)]
    found = ns.findings(rows, label="Day", summed=False)
    assert found and found[0].value_share is None
    assert found[0].sentence == "80 of the 90 records (89%) have the one Day a."
    # No value at all (a count with no measure): no value share either.
    assert ns.findings([("a", None, 80), ("b", None, 5), ("c", None, 5)])[0].value_share is None


def test_a_bucket_with_no_key_or_no_record_is_no_bucket() -> None:
    rows = [(None, 9.0, 900), ("", 9.0, 900), ("a", 1.0, 0), ("b", 1.0, 30), ("c", 1.0, 1)]
    found = ns.findings(rows)
    assert [f.key for f in found] == ["b"] and found[0].total_records == 31


def test_the_thresholds_are_named_constants() -> None:
    assert (ns.CONCENTRATION_RATIO, ns.CONCENTRATION_SHARE, ns.CONCENTRATION_MIN_RECORDS,
            ns.CONCENTRATION_MIN_BUCKETS) == (5.0, 0.30, 20, 2)


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
    assert imported <= {"__future__", "collections.abc", "dataclasses", "statistics", "typing"}
