"""Number sense: the concentration check (task 3129, CRM-Q25e part 4).

Spec: ``project-docs/specs/crm_platform.md`` §18.11, the row CRM-Q25e.
``acb_common.number_sense`` is pure, so each case is a list of buckets and
the findings that it gives. No database and no settings.

The five cases of the review of 2026-11-13 are pinned here: four normal
shapes that give NO finding, and the DEWiN case that MUST give one.
"""

from __future__ import annotations

import math
import shutil
import subprocess
import tempfile
import time
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

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

    source = Path(ns.__file__).read_text(encoding="utf-8")
    imported = {
        (node.module or "") if isinstance(node, ast.ImportFrom) else alias.name
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Import | ast.ImportFrom)
        for alias in node.names
    }
    assert imported <= {"__future__", "bisect", "collections.abc", "dataclasses",
                        "datetime", "decimal", "heapq", "math", "re", "typing"}


def test_the_module_reads_no_clock() -> None:
    """No ``datetime.now``, ``date.today``, ``utcnow`` or ``time.`` call."""
    source = Path(ns.__file__).read_text(encoding="utf-8")
    for banned in ("datetime.now", "date.today", ".utcnow", "time.time", "time.monotonic",
                   "time.sleep", "import time"):
        assert banned not in source, banned


# ── Hostile values ───────────────────────────────────────────────────────────

_HOSTILE = [None, math.nan, math.inf, -math.inf, True, False, "12", "abc", "", [], {},
            object(), Decimal("NaN"), 10**400]


def test_outlier_calm_and_positive() -> None:
    """Mutations: OUTLIER_RATIO 5 to 50, a flipped ``<``, the OUTLIER_MIN_PEERS
    rule dropped. Each turns this test or the edge test red."""
    calm = [(f"2026-{m:02d}", 100.0 + m, 10) for m in range(1, 13)]
    assert ns.outlier(calm) == []
    rows = [(m, 100.0, 5) for m in "abcde"] + [("October", 620.0, 5)]
    [one] = ns.outlier(rows, bucket="month")
    assert one.sentence == "October is 6.2 times the median of the other 5 months. Check why."
    assert (one.kind, one.severity, one.key, one.records) == (ns.OUTLIER, "warn", "October", 5)
    low = [("a", 100.0, 5), ("b", 100.0, 5), ("c", 100.0, 5), ("d", 10.0, 5)]
    [two] = ns.outlier(low)
    assert two.sentence == "d is under one fifth of the median of the other 3 buckets. Check why."


def test_outlier_edges() -> None:
    peers = [(f"p{i}", 100.0, 1) for i in range(3)]
    assert ns.outlier([*peers, ("x", 500.0, 1)]) == []        # exactly 5 times
    assert len(ns.outlier([*peers, ("x", 500.1, 1)])) == 1    # just over
    assert ns.outlier([*peers, ("x", 20.0, 1)]) == []         # exactly one fifth
    assert len(ns.outlier([*peers, ("x", 19.9, 1)])) == 1     # just under
    # Min peers: 2 peers are too few, 3 are enough.
    assert ns.outlier([*peers[:2], ("x", 900.0, 1)]) == []
    assert len(ns.outlier([*peers, ("x", 900.0, 1)])) == 1


def test_outlier_zero_negative_and_missing() -> None:
    zero = [(f"p{i}", 0.0, 1) for i in range(4)]
    assert ns.outlier([*zero, ("x", 50.0, 1)]) == []           # a zero median
    neg = [(f"p{i}", -10.0, 1) for i in range(4)]
    assert ns.outlier([*neg, ("x", 50.0, 1)]) == []            # a negative median
    peers = [(f"p{i}", 100.0, 1) for i in range(3)]
    # None value, no records, no key: no bucket, no peer.
    junk = [("n", None, 5), ("z", 900.0, 0), (None, 900.0, 5), ("", 900.0, 5)]
    assert ns.outlier([*peers, *junk]) == []
    assert ns.outlier([*peers, ("x", 900.0, 1), ("n", None, 5)])[0].total_records == 4
    # The leave-one-out median holds for an even and an odd count of peers.
    for size in range(3, 9):
        rows = [(f"p{i}", 100.0 * (i + 1), 1) for i in range(size)]
        for key, value, _r in rows:
            others = sorted(v for k, v, _c in rows if k != key)
            middle = len(others) // 2
            median = others[middle] if len(others) % 2 else (others[middle - 1] + others[middle]) / 2
            expect = value > 5 * median or value < median / 5
            got = {f.key for f in ns.outlier(rows)}
            assert (key in got) == expect or len(got) == ns.OUTLIER_MAX_FINDINGS


def test_every_check_survives_hostile_values() -> None:
    peers = [(f"p{i}", 100.0, 1) for i in range(5)]
    for bad in _HOSTILE:
        assert ns.outlier([*peers, ("x", bad, 1)]) == []
        assert ns.outlier([*peers, ("x", 100.0, bad)]) == []
        assert ns.outlier([*peers, (bad, 100.0, 1)]) == []
        assert ns.coverage(bad, 5) == [] and ns.coverage(10, bad) == []
        assert ns.coverage(10, 1, {"draft": bad}) == []
        assert ns.parts(bad, [("a", 1.0), ("b", 2.0)]) == []
        assert ns.parts(10.0, [("a", bad), ("b", 2.0)]) == []
        assert ns.parts(10.0, bad) == []
        assert ns.part_period(bad, date(2026, 11, 1), date(2026, 11, 13)) == []
        assert ns.part_period("month", bad, date(2026, 11, 13)) == []
        assert ns.part_period("month", date(2026, 11, 1), bad) == []
        assert ns.grouping(bad, 100.0) == []
        assert ns.grouping("1,00,000", bad) == []
        assert ns.grouping("1,00,000", 100.0, bad) == []
    assert ns.outlier([]) == [] and ns.coverage(0, 0) == [] and ns.parts(0, []) == []
    assert ns.grouping("", 1.0) == [] and ns.grouping("hello world", 1.0) == []
    assert ns.part_period("month", date(9999, 12, 1), date(9999, 12, 5)) == []


def test_a_huge_series_finishes_fast_and_is_deterministic() -> None:
    rows = [(f"k{i}", 100.0 + i % 3, 1) for i in range(100_000)]
    rows.append(("spike", 9_000.0, 1))
    start = time.perf_counter()
    first = ns.outlier(rows)
    assert time.perf_counter() - start < 0.5
    assert first == ns.outlier(rows) and first == ns.outlier(list(rows))
    assert ns.parts(100.0, [(f"p{i}", 1.0) for i in range(100_000)]) == []


def test_outlier_gives_at_most_three_findings_the_farthest_first() -> None:
    rows = [(f"p{i}", 100.0, 1) for i in range(10)]
    rows += [("a", 600.0, 1), ("b", 900.0, 1), ("c", 700.0, 1), ("d", 800.0, 1)]
    assert [f.key for f in ns.outlier(rows)] == ["b", "d", "c"]


# ── coverage ─────────────────────────────────────────────────────────────────


def test_coverage_dewin_case() -> None:
    """Mutations: COVERAGE_SHARE 0.20 to 0.99, and ``>=`` to ``>``."""
    [one] = ns.coverage(14_313, 13_219, measure="Total Tax Invoice Amount")
    assert one.sentence == "13,219 of the records have no Total Tax Invoice Amount. Check why."
    assert (one.kind, one.key, one.records, one.total_records) == (
        ns.COVERAGE, "no_value", 13_219, 14_313)


def test_coverage_edges_and_status() -> None:
    assert ns.coverage(100, 19) == []
    assert len(ns.coverage(100, 20)) == 1                      # 20 % is a finding
    assert ns.coverage(100, 101) == []                         # more than the total
    assert ns.coverage(100, 0, {"draft": 19}) == []
    [one] = ns.coverage(100, 0, {"Draft": 10, "cancelled": 10, "open": 80})
    assert one.sentence == ("20 of the records have the status draft or cancelled. "
                            "Check if they belong in the total.")
    [only] = ns.coverage(100, 0, {" DRAFT ": 25})
    assert only.sentence.startswith("25 of the records have the status draft. ")
    assert ns.coverage(100, 5, {"open": 95}) == []
    assert len(ns.coverage(100, 30, {"draft": 30})) == 2


# ── parts ────────────────────────────────────────────────────────────────────


def test_parts_positive_calm_and_edges() -> None:
    """Mutations: PARTS_GAP_SHARE 0.01 to 0.5, PARTS_GAP_MIN_UNITS 1 to 1e9."""
    rows = [("Rent", 30_000_000.0), ("IP", 20_000_000.0), ("power", 11_000_000.0)]
    [one] = ns.parts(78_000_000.0, rows)
    assert one.sentence == "Rent, IP and power add up to 6.1 crore, not 7.8 crore. Check the parts."
    assert ns.parts(61_000_000.0, rows) == []
    # Over 1 % but 1 unit or less: calm. Over 1 unit but 1 % or less: calm.
    assert ns.parts(100.0, [("a", 50.0), ("b", 50.9)]) == []
    assert ns.parts(1_000_000.0, [("a", 500_000.0), ("b", 505_000.0)]) == []
    assert len(ns.parts(1_000_000.0, [("a", 500_000.0), ("b", 510_001.0)])) == 1
    assert len(ns.parts(100.0, [("a", 50.0), ("b", 52.0)])) == 1
    assert ns.parts(100.0, [("a", 100.0)]) == []               # one part is no sum
    two = ns.parts(1000.0, [("a", 400.0), ("b", 500.0)])[0]
    assert two.sentence == "a and b add up to 900, not 1,000. Check the parts."
    many = ns.parts(1000.0, [(f"p{i}", 1.0) for i in range(8)])[0]
    assert many.sentence.startswith("The 8 parts add up to 8, not 1,000.")
    west = ns.parts(7.8e9, [("a", 6.1e9), ("b", 0.0)], indian=False)[0]
    assert "6.1 billion, not 7.8 billion" in west.sentence


# ── part_period ──────────────────────────────────────────────────────────────


def test_part_period() -> None:
    """Mutation: ``<`` to ``<=`` on the end of the period."""
    [one] = ns.part_period("month", date(2026, 11, 1), date(2026, 11, 13))
    assert one.sentence == "November has only 13 days so far."
    assert (one.severity, one.records, one.total_records) == (ns.INFO, 13, 30)
    assert ns.part_period("month", date(2026, 11, 1), date(2026, 11, 30)) == []   # the last day
    assert len(ns.part_period("month", date(2026, 11, 1), date(2026, 11, 29))) == 1
    assert ns.part_period("month", date(2026, 11, 1), date(2026, 12, 2)) == []
    assert ns.part_period("month", date(2026, 11, 1), date(2026, 10, 31)) == []   # not begun
    assert ns.part_period("month", date(2026, 12, 1), date(2026, 12, 1))[0].sentence == (
        "December has only 1 day so far.")
    [week] = ns.part_period("week", date(2026, 11, 9), datetime(2026, 11, 11, 9, 0))
    assert week.sentence == "The week of 2026-11-09 has only 3 days so far."
    assert ns.part_period("week", date(2026, 11, 9), date(2026, 11, 15)) == []
    [quarter] = ns.part_period("quarter", date(2026, 10, 1), date(2026, 11, 15))
    assert quarter.sentence == "The quarter from 2026-10-01 has only 46 days so far."
    assert ns.part_period("quarter", date(2026, 10, 1), date(2026, 12, 31)) == []
    assert ns.part_period("year", date(2026, 1, 1), date(2026, 2, 1)) == []


# ── grouping ─────────────────────────────────────────────────────────────────


def test_grouping_dewin_cases() -> None:
    """Mutations: GROUPING_SCALE_RATIO 9 to 1000, and the Indian pattern dropped."""
    # The right digits, the wrong scale: 3,35,68,33,347 is ten times 33.57 crore.
    [scale] = ns.grouping("INR 3,35,68,33,347", 335_700_000.0)
    assert scale.sentence == "INR 3,35,68,33,347 is not 33.57 crore. Use the display value."
    assert scale.kind == ns.GROUPING and scale.severity == "warn"
    [bad] = ns.grouping("335,683,334,7", 335_683_334.7)
    assert bad.sentence == "335,683,334,7 has a wrong digit grouping. Use the display value."
    assert ns.grouping("INR 33,56,83,334", 335_683_334.0) == []
    assert ns.grouping("335,683,334", 335_683_334.0) == []
    assert ns.grouping("335683334", 335_683_334.0) == []
    assert ns.grouping("33.57 crore", 335_700_000.0) == []
    assert ns.grouping("₹ 1,00,000.50", 100_000.5) == []


def test_grouping_edges() -> None:
    assert ns.grouping("1,00,000", 100_000.0) == [] and ns.grouping("1,000", 1000.0) == []
    assert ns.grouping("12,34", 1234.0) != []                  # neither system
    assert ns.grouping("1,2,3", 123.0) != []
    assert ns.grouping("123,456", 123_456.0) == []             # western only
    assert ns.grouping("1,23,456", 123_456.0) == []            # Indian only
    assert ns.grouping("1234,567", 1_234_567.0) != []
    assert ns.grouping("1,000", 0.0) == [] and ns.grouping("1,000", 1000.0, 0.5) == []
    assert ns.grouping("1,000", 8_999.0) == []                 # 8.999 times: calm
    assert len(ns.grouping("1,000", 9_000.0)) == 1             # 9 times: finding
    assert len(ns.grouping("1,000", 111.0)) == 1
    assert len(ns.grouping("0", 5.0)) == 1
    assert ns.grouping("1,000", 1_001.0) == []                 # inside any tolerance


# ── Whole module ─────────────────────────────────────────────────────────────


def test_a_calm_twelve_month_series_gives_no_finding_of_any_kind() -> None:
    months = [(f"2026-{m:02d}", 1000.0 + 10 * m, 40 + m) for m in range(1, 13)]
    assert ns.findings(months, bucket="month") == []
    assert ns.outlier(months, bucket="month") == []
    assert ns.coverage(sum(c for _k, _v, c in months), 3, {"open": 400}) == []
    assert ns.parts(sum(v for _k, v, _c in months), [(k, v) for k, v, _c in months]) == []
    assert ns.part_period("month", date(2026, 10, 1), date(2026, 11, 15)) == []
    assert ns.grouping("12,000", 12_000.0) == []


def test_the_new_constants() -> None:
    assert (ns.OUTLIER_RATIO, ns.OUTLIER_MIN_PEERS, ns.COVERAGE_SHARE, ns.COVERAGE_STATUSES,
            ns.PARTS_GAP_SHARE, ns.PARTS_GAP_MIN_UNITS, ns.GROUPING_SCALE_RATIO) == (
        5.0, 3, 0.20, ("draft", "cancelled"), 0.01, 1.0, 9.0)


def test_every_sentence_holds_the_ste_rules() -> None:
    """Under 20 words, no semicolon, and the repo STE linter passes."""
    peers = [(f"p{i}", 100.0, 1) for i in range(5)]
    found = [
        *ns.outlier([*peers, ("October", 900.0, 1)], bucket="month"),
        *ns.outlier([*peers, ("May", 1.0, 1)]),
        *ns.coverage(14_313, 13_219, {"draft": 5_000}, measure="Total Tax Invoice Amount"),
        *ns.parts(78_000_000.0, [("Rent", 3e7), ("IP", 2e7), ("power", 1.1e7)]),
        *ns.part_period("month", date(2026, 11, 1), date(2026, 11, 13)),
        *ns.part_period("week", date(2026, 11, 9), date(2026, 11, 11)),
        *ns.grouping("INR 3,35,68,33,347", 335_700_000.0),
        *ns.grouping("335,683,334,7", 335_683_334.7),
        *ns.findings([("a", 1.0, 80), *[(f"d{i}", 1.0, 2) for i in range(5)]], label="Day"),
    ]
    assert len(found) >= 9
    for f in found:
        assert len(f.sentence.split()) < 20 and ";" not in f.sentence, f.sentence
    root = Path(__file__).resolve().parents[2]
    lint = root / ".claude" / "hooks" / "ste-lint.mjs"
    if shutil.which("node") is None or not lint.exists():
        return
    with tempfile.TemporaryDirectory() as tmp:
        doc = Path(tmp) / "sentences.md"
        doc.write_text("\n\n".join(f.sentence for f in found) + "\n", encoding="utf-8")
        run = subprocess.run(["node", str(lint), str(doc)], capture_output=True,
                             text=True, timeout=30, check=False)
    assert run.returncode == 0, run.stdout + run.stderr
