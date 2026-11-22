"""Merge-speed benchmark for D218 (ci_velocity.md section 8).

Usage: python scripts/merge_benchmark.py <start ISO UTC> <end ISO UTC> [--sample N]

The baseline (2026-11-05 18:00 to 2026-11-12 18:00 UTC) is on Metorite task
3064, the rung 0 demo of D218.

Reads GitHub only (gh api). For PRs CREATED in [start, end) and merged:
open->merge, pr-check runs per PR, CI minutes per PR, last green->merge.
For pr-check runs CREATED in the window: red share, and the share of red
runs whose only failed jobs are the browser suite (sampled).
"""
from __future__ import annotations

import json
import subprocess
import sys
from collections import defaultdict
from datetime import UTC, datetime, timedelta

REPO = "Hathi-Labs/Metorite"


def gh(path: str):
    out = subprocess.run(["gh", "api", path], capture_output=True, text=True,
                         encoding="utf-8", timeout=90)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip()[:200])
    return json.loads(out.stdout)


def t(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def q(vals, p):
    v = sorted(vals)
    return v[min(len(v) - 1, int(p * len(v)))] if v else None


def runs_in(start: datetime, end: datetime):
    runs, day = [], start
    while day < end:
        nxt = min(day + timedelta(hours=12), end)
        rng = f"{day:%Y-%m-%dT%H:%M:%SZ}..{nxt:%Y-%m-%dT%H:%M:%SZ}"
        page = 1
        while True:
            d = gh(f"repos/{REPO}/actions/workflows/pr-check.yml/runs"
                   f"?created={rng}&per_page=100&page={page}")
            runs += d["workflow_runs"]
            if len(d["workflow_runs"]) < 100:
                break
            page += 1
        day = nxt
    return runs


def prs_created(start: datetime, end: datetime):
    q_ = (f"repo:{REPO} is:pr is:merged created:"
          f"{start:%Y-%m-%dT%H:%M:%SZ}..{end:%Y-%m-%dT%H:%M:%SZ}")
    out = subprocess.run(["gh", "pr", "list", "--repo", REPO, "--state", "merged",
                          "--limit", "1000", "--search", q_.split(" ", 1)[1],
                          "--json", "number,createdAt,mergedAt,headRefName"],
                         capture_output=True, text=True, encoding="utf-8", timeout=120)
    return json.loads(out.stdout or "[]")


def main(argv):
    start, end = t(argv[1]), t(argv[2])
    sample = int(argv[argv.index("--sample") + 1]) if "--sample" in argv else 60
    now = datetime.now(UTC)
    runs = runs_in(start, min(end + timedelta(days=1), now))  # a PR runs after it opens
    by_branch = defaultdict(list)
    for r in runs:
        by_branch[r["head_branch"]].append(r)
    prs = prs_created(start, end)
    rows = []
    for p in prs:
        rs = [r for r in by_branch.get(p["headRefName"], [])
              if r["status"] == "completed" and t(r["updated_at"]) <= t(p["mergedAt"])
              and t(r["created_at"]) >= t(p["createdAt"]) - timedelta(minutes=5)]
        ci = sum((t(r["updated_at"]) - t(r["run_started_at"])).total_seconds() / 60 for r in rs)
        reqgreen = [t(r["updated_at"]) for r in rs if r["conclusion"] in ("success", "failure")]
        rows.append({
            "open_to_merge_min": (t(p["mergedAt"]) - t(p["createdAt"])).total_seconds() / 60,
            "runs": len(rs), "ci_min": ci,
            "last_run_to_merge_min": ((t(p["mergedAt"]) - max(reqgreen)).total_seconds() / 60
                                      if reqgreen else None),
        })
    win = [r for r in runs if start <= t(r["created_at"]) < end and r["status"] == "completed"]
    fail = [r for r in win if r["conclusion"] == "failure"]
    ok = [r for r in win if r["conclusion"] == "success"]
    browser_only = checked = 0
    for r in fail[:sample]:
        try:
            jobs = gh(f"repos/{REPO}/actions/runs/{r['id']}/jobs?per_page=60")["jobs"]
        except Exception:
            continue
        names = [j["name"] for j in jobs if j["conclusion"] == "failure"]
        if not names:
            continue
        checked += 1
        if all(n.startswith("Browser") for n in names):
            browser_only += 1
    days = (end - start).total_seconds() / 86400

    def stat(key):
        v = [r[key] for r in rows if r[key] is not None]
        return {"p50": q(v, .5), "p75": q(v, .75), "p90": q(v, .9), "n": len(v)}

    out = {
        "window": [argv[1], argv[2]],
        "prs_merged_created_in_window": len(rows),
        "merges_per_day": round(len(rows) / days, 1) if days else None,
        "open_to_merge_min": stat("open_to_merge_min"),
        "pr_check_runs_per_pr": stat("runs"),
        "ci_minutes_per_pr": stat("ci_min"),
        "last_run_to_merge_min": stat("last_run_to_merge_min"),
        "pr_check_runs": len(win),
        "red_share": round(len(fail) / (len(fail) + len(ok)), 3) if fail or ok else None,
        "cancelled": sum(1 for r in win if r["conclusion"] == "cancelled"),
        "browser_only_red_share_of_red": (round(browser_only / checked, 3) if checked else None),
        "browser_sample": checked,
    }
    for k, v in out.items():
        if isinstance(v, dict):
            out[k] = {a: (round(b, 1) if isinstance(b, float) else b) for a, b in v.items()}
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main(sys.argv)
