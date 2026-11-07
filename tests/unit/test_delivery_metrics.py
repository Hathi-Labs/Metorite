"""CIV-10: the weekly delivery metrics (scripts/delivery_metrics.py).

A fake gh_json serves canned run and job JSON, so no network is used.
"""

import importlib.util
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "delivery_metrics.py"
COMMAND = ROOT / ".claude" / "commands" / "delivery-review.md"

NOW = datetime(2026, 11, 7, 12, 0, 0, tzinfo=timezone.utc)


def _load():
    spec = importlib.util.spec_from_file_location("delivery_metrics", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["delivery_metrics"] = mod
    spec.loader.exec_module(mod)
    return mod


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _run(rid, minutes, conclusion, sha=None, event="pull_request", status="completed"):
    start = NOW - timedelta(days=1, hours=rid % 5)
    return {
        "id": rid,
        "status": status,
        "conclusion": conclusion,
        "event": event,
        "head_sha": sha or f"sha{rid}",
        "run_started_at": _iso(start),
        "updated_at": _iso(start + timedelta(minutes=minutes)),
        "created_at": _iso(start),
    }


def _job(name, conclusion, wait_s):
    created = NOW - timedelta(days=1)
    return {
        "name": name,
        "conclusion": conclusion,
        "status": "completed",
        "created_at": _iso(created),
        "started_at": _iso(created + timedelta(seconds=wait_s)),
    }


class FakeGh:
    """Serves runs for pr-check and deploy, jobs per run, pulls per commit."""

    def __init__(self, pr_runs, jobs, batch_runs=(), commit_pulls=None, open_prs=(), fail=()):
        self.pr_runs = list(pr_runs)
        self.batch_runs = list(batch_runs)
        self.jobs = jobs
        self.commit_pulls = commit_pulls or {}
        self.open_prs = list(open_prs)
        self.fail = fail
        self.calls = []

    def __call__(self, path):
        self.calls.append(path)
        for needle in self.fail:
            if needle in path:
                raise RuntimeError(f"gh failed for {needle}")
        if "workflows/pr-check.yml/runs" in path:
            return {"workflow_runs": self.pr_runs if re.search(r"page=1(?!\d)", path) else []}
        if "workflows/deploy.yml/runs" in path:
            assert "event=push" in path and "branch=main" in path
            return {"workflow_runs": self.batch_runs if re.search(r"page=1(?!\d)", path) else []}
        m = re.search(r"actions/runs/(\d+)/jobs", path)
        if m:
            return {"jobs": self.jobs.get(int(m.group(1)), [])}
        m = re.search(r"commits/(\w+)/pulls", path)
        if m:
            return self.commit_pulls.get(m.group(1), [])
        if "pulls?state=open" in path:
            return self.open_prs if re.search(r"page=1(?!\d)", path) else []
        raise AssertionError(path)


@pytest.fixture
def dm():
    return _load()


def _durations_file(tmp_path, data):
    p = tmp_path / ".test_durations"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def _pr_fixture():
    # durations 10,20,30,40,50 minutes ; two red runs
    runs = [
        _run(1, 10, "success"),
        _run(2, 20, "failure"),
        _run(3, 30, "success"),
        _run(4, 40, "failure"),
        _run(5, 50, "success"),
        _run(6, 5, "cancelled"),
    ]
    jobs = {
        1: [_job("lint", "success", 60)],
        2: [_job("unit-tests", "failure", 120), _job("lint", "success", 0)],
        3: [_job("lint", "success", 180)],
        4: [_job("unit-tests", "failure", 240), _job("e2e", "failure", 0)],
        5: [_job("lint", "success", 300)],
        6: [_job("lint", "cancelled", 0)],
    }
    return runs, jobs


def test_pr_check_median_p90_red_rate_and_top_jobs(dm, tmp_path):
    runs, jobs = _pr_fixture()
    gh = FakeGh(runs, jobs)
    out = dm.collect(gh, days=7, max_runs=200, now=NOW, durations=_durations_file(tmp_path, {}))
    pc = out["pr_check"]
    assert pc["runs"] == 5  # the cancelled run does not count
    assert pc["median_min"] == 30.0
    assert pc["p90_min"] == 50.0  # nearest rank
    assert pc["red_rate"] == 0.4
    assert pc["top_failing_jobs"] == [
        {"name": "unit-tests", "count": 2},
        {"name": "e2e", "count": 1},
    ]


def test_queue_wait(dm, tmp_path):
    runs, jobs = _pr_fixture()
    out = dm.collect(FakeGh(runs, jobs), days=7, max_runs=200, now=NOW,
                     durations=_durations_file(tmp_path, {}))
    qw = out["queue_wait"]
    assert qw["jobs"] == 7  # the cancelled run is skipped
    assert qw["max_min"] == 5.0
    # waits in seconds: 60,120,0,180,240,0,300 -> sorted, the median is 120 s
    assert qw["median_min"] == 2.0
    assert qw["p90_min"] == 5.0


def test_batch_main_and_selection_miss(dm, tmp_path):
    runs, jobs = _pr_fixture()
    pr_runs = runs + [_run(7, 12, "success", sha="prhead")]
    jobs[7] = [_job("lint", "success", 0)]
    batch = [
        _run(100, 60, "success", sha="m1", event="push"),
        _run(101, 80, "failure", sha="m2", event="push"),
        _run(102, 100, "failure", sha="m3", event="push"),
    ]
    pulls = {"m2": [{"number": 9, "head": {"sha": "prhead"}}], "m3": []}
    gh = FakeGh(pr_runs, jobs, batch_runs=batch, commit_pulls=pulls)
    out = dm.collect(gh, days=7, max_runs=200, now=NOW, durations=_durations_file(tmp_path, {}))
    bm = out["batch_main"]
    assert bm["runs"] == 3
    assert bm["median_min"] == 80.0
    assert bm["p90_min"] == 100.0
    assert bm["red_rate"] == round(2 / 3, 4)
    sm = out["selection_misses"]
    assert sm["batch_red_after_pr_green"] == 1
    assert sm["red_batches"] == 2
    assert sm["matched"] == 1


def test_slowest_tests_sums_per_file(dm, tmp_path):
    data = {}
    for i in range(12):
        data[f"tests/unit/test_f{i}.py::test_a"] = float(i)
        data[f"tests/unit/test_f{i}.py::TestC::test_b"] = 1.0
    path = _durations_file(tmp_path, data)
    runs, jobs = _pr_fixture()
    out = dm.collect(FakeGh(runs, jobs), days=7, max_runs=200, now=NOW, durations=path)
    top = out["slowest_tests"]
    assert len(top) == 10
    assert top[0] == {"file": "tests/unit/test_f11.py", "seconds": 12.0}
    assert top[1]["file"] == "tests/unit/test_f10.py"
    assert top[-1]["file"] == "tests/unit/test_f2.py"


def test_open_prs_older_than_2d(dm, tmp_path):
    prs = [
        {"number": 1, "title": "old", "created_at": _iso(NOW - timedelta(days=5)), "draft": False},
        {"number": 2, "title": "new", "created_at": _iso(NOW - timedelta(hours=5)), "draft": False},
        {"number": 3, "title": "mid", "created_at": _iso(NOW - timedelta(days=2, hours=1)), "draft": False},
    ]
    runs, jobs = _pr_fixture()
    gh = FakeGh(runs, jobs, open_prs=prs)
    out = dm.collect(gh, days=7, max_runs=200, now=NOW, durations=_durations_file(tmp_path, {}))
    got = out["open_prs_older_than_2d"]
    assert [p["number"] for p in got] == [1, 3]
    assert got[0] == {"number": 1, "title": "old", "age_days": 5.0}


def test_failing_gh_call_gives_null_and_error(dm, tmp_path):
    runs, jobs = _pr_fixture()
    gh = FakeGh(runs, jobs, fail=("workflows/deploy.yml", "pulls?state=open"))
    out = dm.collect(gh, days=7, max_runs=200, now=NOW, durations=_durations_file(tmp_path, {}))
    assert out["batch_main"] is None
    assert out["open_prs_older_than_2d"] is None
    assert out["selection_misses"] is None
    assert out["pr_check"]["runs"] == 5
    assert any("batch_main" in e for e in out["errors"])
    assert any("open_prs_older_than_2d" in e for e in out["errors"])


def test_failing_runs_call_nulls_dependent_metrics(dm, tmp_path):
    gh = FakeGh([], {}, fail=("workflows/pr-check.yml",))
    out = dm.collect(gh, days=7, max_runs=200, now=NOW, durations=_durations_file(tmp_path, {}))
    assert out["pr_check"] is None
    assert out["queue_wait"] is None
    assert out["errors"]


def test_missing_test_durations_is_null_with_error(dm, tmp_path):
    runs, jobs = _pr_fixture()
    out = dm.collect(FakeGh(runs, jobs), days=7, max_runs=200, now=NOW,
                     durations=tmp_path / "absent")
    assert out["slowest_tests"] is None
    assert any("slowest_tests" in e for e in out["errors"])


def test_run_cap(dm, tmp_path):
    runs = [_run(i, 10, "success") for i in range(1, 31)]
    gh = FakeGh(runs, {})
    out = dm.collect(gh, days=7, max_runs=7, now=NOW, durations=_durations_file(tmp_path, {}))
    assert out["pr_check"]["runs"] == 7
    jobs_calls = [c for c in gh.calls if "/jobs" in c]
    assert len(jobs_calls) == 7


def test_compare_adds_numeric_deltas(dm):
    cur = {"pr_check": {"runs": 5, "median_min": 30.0, "top_failing_jobs": [1]},
           "batch_main": None, "slowest_tests": [{"file": "a", "seconds": 1.0}], "errors": []}
    prev = {"pr_check": {"runs": 8, "median_min": 25.5, "top_failing_jobs": [2]},
            "batch_main": {"runs": 3}}
    d = dm.compute_delta(cur, prev)
    assert d == {"pr_check": {"runs": -3, "median_min": 4.5}}


def test_main_writes_out_file_and_compare(dm, tmp_path, monkeypatch, capsys):
    runs, jobs = _pr_fixture()
    monkeypatch.setattr(dm, "gh_json", FakeGh(runs, jobs))
    monkeypatch.setattr(dm, "DURATIONS", _durations_file(tmp_path, {}))
    prev = tmp_path / "prev.json"
    prev.write_text(json.dumps({"pr_check": {"runs": 4}}), encoding="utf-8")
    out = tmp_path / "out.json"
    rc = dm.main(["--days", "7", "--compare", str(prev), "--out", str(out)])
    assert rc == 0
    printed = json.loads(capsys.readouterr().out)
    assert json.loads(out.read_text(encoding="utf-8")) == printed
    assert printed["delta"]["pr_check"]["runs"] == 1


def test_command_file_names_every_metric_key_and_holds_thresholds():
    text = COMMAND.read_text(encoding="utf-8")
    for key in ("pr_check", "queue_wait", "batch_main", "slowest_tests",
                "open_prs_older_than_2d", "selection_misses",
                "median_min", "p90_min", "red_rate", "top_failing_jobs",
                "max_min", "batch_red_after_pr_green"):
        assert key in text, key
    assert "## Thresholds" in text
    assert text.count("| ") > 20
    for word in ("1628", "ed3027f8-bf27-4f56-9a40-2c89141606d0",
                 "519d9f0d-8dde-4910-b733-b4fa2ed3c2b9", "Owner queue"):
        assert word in text
