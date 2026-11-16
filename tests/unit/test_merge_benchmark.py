"""The merge-speed benchmark of D218 (ci_velocity.md section 8, task 3064).

The script reads GitHub, so these tests pin only its pure parts: the
percentile and the time parser. The benchmark compares one window before
rung 0 with windows after it, so both must stay the same between runs.
"""
from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from pathlib import Path

_PATH = Path(__file__).resolve().parents[2] / "scripts" / "merge_benchmark.py"
_SPEC = importlib.util.spec_from_file_location("merge_benchmark", _PATH)
mb = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mb)


def test_percentile_is_the_nearest_rank_from_below():
    vals = [10, 1, 7, 3, 5]
    assert mb.q(vals, 0.5) == 5
    assert mb.q(vals, 0.9) == 10
    assert mb.q([], 0.5) is None


def test_time_parser_reads_github_z_times_as_utc():
    assert mb.t("2026-11-12T18:00:00Z") == datetime(2026, 11, 12, 18, tzinfo=UTC)
