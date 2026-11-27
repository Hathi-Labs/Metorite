#!/usr/bin/env python3
"""The "done means live" report of the home box (LBB-1a, Metorite task 4044).

Spec: ``project-docs/specs/engineering_practice.md`` section 7b.6.

**What it does.** A timer runs this file 30 minutes after each release slot.
It lists each merge of the last 72 hours and says whether production runs it.
It appends one line for each merge to ``<lanes>/live-check.tsv``.

**What it never does.** It writes only that report file, its own clone, and
one stderr line. It sends no credential. It does not touch the board.

**The deployed SHA** is ``applied_sha`` of ``GET <url>/version``. The field
``sha`` names the gateway checkout, so a failed web build could read LIVE.

Python standard library, plus ``git``.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

WINDOW_S = 72 * 3600
SLOTS = ((5, 30), (13, 30), (21, 30))  # release slots, UTC
STUCK_SLOTS = 2
REPO_URL = "https://github.com/Hathi-Labs/Metorite.git"
GIT_TIMEOUT_S = 120
TASK_RE = re.compile(r"task (\d+)")


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, "").strip() or default


def _git(src: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(src), *args],
        capture_output=True, text=True, encoding="utf-8",
        timeout=GIT_TIMEOUT_S, check=False,
    )


def slots_between(start: int, now: int) -> int:
    """Count slot instants in (start, now], in epoch UTC seconds."""
    count = 0
    for day in range(start // 86400 - 1, now // 86400 + 1):
        for hour, minute in SLOTS:
            instant = day * 86400 + hour * 3600 + minute * 60
            if start < instant <= now:
                count += 1
    return count


def read_merges(lanes: Path) -> dict[tuple[str, str], str]:
    """The (pr, sha) pairs of both logs, with the task or '-'. DONE lines only."""
    merges: dict[tuple[str, str], str] = {}
    bot = lanes / "merge-bot.log"
    if bot.is_file():
        for line in bot.read_text(encoding="utf-8", errors="replace").splitlines():
            parts = line.strip().split(" ")
            if len(parts) >= 3 and parts[0] == "DONE":
                merges.setdefault((parts[1], parts[2]), "-")
    queue = lanes / "merge-queue.log"
    if queue.is_file():
        for line in queue.read_text(encoding="utf-8", errors="replace").splitlines():
            parts = line.rstrip("\n").split("\t")
            if len(parts) >= 3 and parts[0] == "DONE":
                found = TASK_RE.search(parts[-1]) if len(parts) > 3 else None
                task = found.group(1) if found else "-"
                key = (parts[1], parts[2])
                if merges.get(key, "-") == "-":
                    merges[key] = task
    return merges


def read_version() -> str | None:
    """The ``applied_sha`` of /version, or None when it cannot be read."""
    cmd = _env("LIVE_CHECK_VERSION_CMD")
    try:
        if cmd:
            out = subprocess.run(
                cmd, shell=True, capture_output=True, text=True,
                encoding="utf-8", timeout=60, check=True,
            ).stdout
        else:
            url = _env("LIVE_CHECK_GATEWAY_URL")
            if not url:
                return None
            with urllib.request.urlopen(url.rstrip("/") + "/version", timeout=20) as r:
                out = r.read().decode("utf-8")
        applied = json.loads(out).get("applied_sha")
    except (OSError, ValueError, subprocess.SubprocessError, AttributeError):
        return None
    return applied.strip() if isinstance(applied, str) and applied.strip() else None


def ensure_clone(src: Path, shas: list[str]) -> bool:
    """Make the clone when it is missing, then fetch main and each SHA."""
    if not (src / ".git").exists() and not (src / "HEAD").exists():
        src.parent.mkdir(parents=True, exist_ok=True)
        done = subprocess.run(
            ["git", "clone", "-q", "--no-tags", _env("LIVE_CHECK_REPO", REPO_URL), str(src)],
            capture_output=True, text=True, timeout=600, check=False,
        )
        if done.returncode != 0:
            return False
    _git(src, "fetch", "-q", "--no-tags", "origin", "main")
    for sha in shas:  # best effort: a short SHA or a gone SHA fails, and that is fine
        if re.fullmatch(r"[0-9a-f]{40}", sha):
            _git(src, "fetch", "-q", "--no-tags", "origin", sha)
    return True


def resolve(src: Path, sha: str) -> str | None:
    out = _git(src, "rev-parse", "--verify", "-q", f"{sha}^{{commit}}")
    return out.stdout.strip() if out.returncode == 0 and out.stdout.strip() else None


def commit_time(src: Path, full: str) -> int | None:
    out = _git(src, "show", "-s", "--format=%ct", full)
    try:
        return int(out.stdout.strip()) if out.returncode == 0 else None
    except ValueError:
        return None


def stamp(now: int) -> str:
    return dt.datetime.fromtimestamp(now, dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def run() -> int:
    mode = _env("LIVE_CHECK_MODE", "off").lower()
    if mode != "dry":
        if mode != "off":
            print(f"live_check: LIVE_CHECK_MODE={mode!r} reads as off", file=sys.stderr)
        return 0
    lanes = Path(_env("LIVE_CHECK_LANES", str(Path.home() / "lanes")))
    src = Path(_env("LIVE_CHECK_SRC", str(lanes / "live-check-src")))
    now = int(_env("LIVE_CHECK_NOW", str(int(dt.datetime.now(dt.UTC).timestamp()))))
    report = lanes / "live-check.tsv"
    lines: list[str] = []

    def emit(pr: str, sha: str, task: str, state: str, deployed: str) -> None:
        lines.append("\t".join([stamp(now), pr, sha, task, state, deployed]))

    applied = read_version()
    if applied is None:
        emit("-", "-", "-", "UNKNOWN", "-")
    else:
        merges = read_merges(lanes)
        have_clone = ensure_clone(src, [applied, *(s for _, s in merges)])
        deployed = resolve(src, applied) if have_clone else None
        for (pr, sha), task in merges.items():
            full = resolve(src, sha) if have_clone else None
            when = commit_time(src, full) if full else None
            if when is None:
                emit(pr, sha, task, "UNKNOWN", applied)
                continue
            if now - when > WINDOW_S:
                continue
            if deployed is None:
                state = "UNKNOWN"
            else:
                anc = _git(src, "merge-base", "--is-ancestor", full, deployed).returncode
                if anc == 0:
                    state = "LIVE"
                elif anc == 1:
                    state = "STUCK" if slots_between(when, now) >= STUCK_SLOTS else "WAITING"
                else:
                    state = "UNKNOWN"
            emit(pr, sha, task, state, deployed or applied)
    lanes.mkdir(parents=True, exist_ok=True)
    with report.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write("".join(f"{ln}\n" for ln in lines))
    return 0


if __name__ == "__main__":
    sys.exit(run())
