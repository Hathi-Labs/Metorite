#!/usr/bin/env python3
"""CIV-10: the weekly delivery metrics. Read-only, stdlib only.

Prints ONE JSON object. Every GitHub read goes through gh_json(), which runs
`gh api <path>`. A failing call gives the metric as null and adds a line to
`errors`. It never crashes the run.

    python3 scripts/delivery_metrics.py --days 7 --compare prev.json --out now.json

Metrics that need Metorite (the owner queue, stale holds) are NOT here. The
command .claude/commands/delivery-review.md reads them through the MCP.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import subprocess
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = "Hathi-Labs/Metorite"
PR_WORKFLOW = "pr-check.yml"
# deploy.yml triggers on `push` to branch `main`: that run is the batch.
BATCH_WORKFLOW = "deploy.yml"
ROOT = Path(__file__).resolve().parents[1]
DURATIONS = ROOT / ".test_durations"
CALL_TIMEOUT_S = 60
RED = ("failure", "timed_out")
DONE = ("success", "failure", "timed_out")


def gh_json(path: str):
    """The ONLY GitHub read. Tests monkeypatch it."""
    res = subprocess.run(
        ["gh", "api", path],
        capture_output=True, text=True, timeout=CALL_TIMEOUT_S, encoding="utf-8",
    )
    if res.returncode != 0:
        raise RuntimeError((res.stderr or "gh api failed").strip()[:200])
    return json.loads(res.stdout)


def _t(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _p90(vals):
    s = sorted(vals)
    return s[max(0, math.ceil(0.9 * len(s)) - 1)]  # nearest rank


def _stats(vals, scale=1.0):
    vals = [v / scale for v in vals]
    return {"median_min": round(statistics.median(vals), 2),
            "p90_min": round(_p90(vals), 2)}


def _run_minutes(run):
    return (_t(run["updated_at"]) - _t(run["run_started_at"])).total_seconds() / 60


def _fetch_runs(gh, workflow, extra, since, max_runs):
    runs, page = [], 1
    while len(runs) < max_runs:
        path = (f"repos/{REPO}/actions/workflows/{workflow}/runs?per_page=100"
                f"&created=%3E%3D{since}&page={page}{extra}")
        batch = gh(path).get("workflow_runs", [])
        if not batch:
            break
        runs.extend(batch)
        page += 1
    return runs[:max_runs]


def _finished(runs):
    return [r for r in runs if r.get("status") == "completed"
            and r.get("conclusion") in DONE and r.get("run_started_at")]


def _jobs(gh, runs, errors):
    """One jobs call per run. A failing call is logged and skipped."""
    out = {}
    for r in runs:
        try:
            out[r["id"]] = gh(f"repos/{REPO}/actions/runs/{r['id']}/jobs?per_page=100").get("jobs", [])
        except Exception as e:  # noqa: BLE001
            errors.append(f"jobs of run {r['id']}: {e}")
    return out


def _pr_check(pr_runs, jobs):
    if not pr_runs:
        return {"runs": 0, "median_min": None, "p90_min": None,
                "red_rate": None, "top_failing_jobs": []}
    red = [r for r in pr_runs if r["conclusion"] in RED]
    names = Counter()
    for r in red:
        for j in jobs.get(r["id"], []):
            if j.get("conclusion") in RED:
                names[j["name"]] += 1
    top = sorted(names.items(), key=lambda kv: (-kv[1], kv[0]))[:5]
    return {"runs": len(pr_runs),
            **_stats([_run_minutes(r) for r in pr_runs]),
            "red_rate": round(len(red) / len(pr_runs), 4),
            "top_failing_jobs": [{"name": n, "count": c} for n, c in top]}


def _queue_wait(jobs):
    waits = []
    for js in jobs.values():
        for j in js:
            if j.get("started_at") and j.get("created_at"):
                waits.append((_t(j["started_at"]) - _t(j["created_at"])).total_seconds())
    if not waits:
        return {"jobs": 0, "median_min": None, "p90_min": None, "max_min": None}
    return {"jobs": len(waits), **_stats(waits, 60.0), "max_min": round(max(waits) / 60, 2)}


def _batch_main(runs):
    if not runs:
        return {"runs": 0, "median_min": None, "p90_min": None, "red_rate": None}
    red = sum(1 for r in runs if r["conclusion"] in RED)
    return {"runs": len(runs), **_stats([_run_minutes(r) for r in runs]),
            "red_rate": round(red / len(runs), 4)}


def _selection_misses(gh, batch_runs, pr_runs):
    """Count red main batches whose PR run was green.

    A squash merge gives main a NEW sha, so the PR run never shares it. For
    each red batch we ask `commits/<sha>/pulls` for the merged PR, take its
    head sha, and find the newest pr-check run of that head sha in the window.
    Green PR run + red batch = a miss. A batch with no PR or no PR run in the
    window is "unmatched": it is not counted as a miss.
    """
    red = [r for r in batch_runs if r["conclusion"] in RED]
    by_sha = {}
    for r in sorted(pr_runs, key=lambda r: r["run_started_at"]):
        by_sha[r["head_sha"]] = r  # newest wins
    matched = misses = 0
    for r in red:
        pulls = gh(f"repos/{REPO}/commits/{r['head_sha']}/pulls")
        if not pulls:
            continue
        pr_run = by_sha.get(pulls[0]["head"]["sha"])
        if pr_run is None:
            continue
        matched += 1
        if pr_run["conclusion"] == "success":
            misses += 1
    return {"batch_red_after_pr_green": misses, "red_batches": len(red), "matched": matched}


def _slowest(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    files = Counter()
    for test_id, sec in data.items():
        files[test_id.split("::", 1)[0]] += sec
    top = sorted(files.items(), key=lambda kv: (-kv[1], kv[0]))[:10]
    return [{"file": f, "seconds": round(s, 2)} for f, s in top]


def _old_prs(gh, now):
    out, page = [], 1
    while page <= 5:
        batch = gh(f"repos/{REPO}/pulls?state=open&per_page=100&page={page}")
        if not batch:
            break
        for p in batch:
            age = (now - _t(p["created_at"])).total_seconds() / 86400
            if age > 2:
                out.append({"number": p["number"], "title": p["title"], "age_days": round(age, 2)})
        page += 1
    return sorted(out, key=lambda p: -p["age_days"])


def collect(gh, days=7, max_runs=200, now=None, durations=None):
    now = now or datetime.now(timezone.utc)
    since = (now - timedelta(days=days)).strftime("%Y-%m-%d")
    errors: list[str] = []
    out: dict = {"generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "days": days}

    def guard(key, fn):
        try:
            out[key] = fn()
        except Exception as e:  # noqa: BLE001
            out[key] = None
            errors.append(f"{key}: {e}")

    pr_runs = None
    try:
        pr_runs = _finished(_fetch_runs(gh, PR_WORKFLOW, "", since, max_runs))
    except Exception as e:  # noqa: BLE001
        errors.append(f"pr_check runs: {e}")
    jobs = {}
    if pr_runs is not None:
        # One jobs call per run, capped by max_runs through the run list.
        jobs = _jobs(gh, pr_runs, errors)
        guard("pr_check", lambda: _pr_check(pr_runs, jobs))
        guard("queue_wait", lambda: _queue_wait(jobs))
    else:
        out["pr_check"] = out["queue_wait"] = None

    batch = None
    try:
        batch = _finished(_fetch_runs(gh, BATCH_WORKFLOW, "&event=push&branch=main", since, max_runs))
    except Exception as e:  # noqa: BLE001
        errors.append(f"batch_main: {e}")
    if batch is not None:
        guard("batch_main", lambda: _batch_main(batch))
    else:
        out["batch_main"] = None

    guard("slowest_tests", lambda: _slowest(durations or DURATIONS))
    guard("open_prs_older_than_2d", lambda: _old_prs(gh, now))
    if batch is not None and pr_runs is not None:
        guard("selection_misses", lambda: _selection_misses(gh, batch, pr_runs))
    else:
        out["selection_misses"] = None
        errors.append("selection_misses: needs both the pr-check and batch runs")
    out["errors"] = errors
    return out


def compute_delta(cur, prev):
    """This week minus last week, for every numeric leaf found in both."""
    delta = {}
    for k, v in cur.items():
        pv = prev.get(k) if isinstance(prev, dict) else None
        if isinstance(v, dict) and isinstance(pv, dict):
            sub = compute_delta(v, pv)
            if sub:
                delta[k] = sub
        elif (isinstance(v, (int, float)) and not isinstance(v, bool)
              and isinstance(pv, (int, float)) and not isinstance(pv, bool)):
            delta[k] = round(v - pv, 4)
    return delta


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--out")
    ap.add_argument("--compare")
    ap.add_argument("--max-runs", type=int, default=200)
    a = ap.parse_args(argv)
    res = collect(gh_json, days=a.days, max_runs=min(a.max_runs, 200))
    if a.compare:
        try:
            prev = json.loads(Path(a.compare).read_text(encoding="utf-8"))
            res["delta"] = compute_delta({k: v for k, v in res.items() if k not in ("days", "errors")}, prev)
        except Exception as e:  # noqa: BLE001
            res["delta"] = None
            res["errors"].append(f"compare: {e}")
    text = json.dumps(res, indent=2)
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
