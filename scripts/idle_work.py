#!/usr/bin/env python3
"""IDLE-1 (task 4216): find the idle work on ONE machine and report it, dry.

Spec: ``project-docs/specs/engineering_practice.md`` §7b.11 (IDLE-0, task 4204).

**What it reports.** One TSV line for each item, in this form::

    <UTC time>\t<machine>\t<state>\twould\t<target>\t<task>\t<reason>

- S1: a worktree with uncommitted files, idle more than 6 h.
- S2: a worktree with commits that no remote holds, idle more than 6 h.
- S3: a pushed local branch with no PR, its last commit older than 24 h.
- S4: a session idle more than 12 h whose worktree holds no S1 to S3 item.
- S5: a run of this machine past its lease, whose session is gone. It needs a
  Metorite token. With none, the report says so in one line.
- RESCUE: a rescue ref on origin (``refs/heads/rescue/*/*``), with its task.
  IDLE-5 decides S6 (a person) or S7 (a box lane).
- LBB-3 and REAPER: one summary line each, for the states those tools own.

**It never acts.** IDLE-1 has no action. ``IDLE_WORK_MODE`` is ``off`` (the
default: it exits 0 and runs nothing), ``dry``, or ``apply:<states>``. An
``apply`` mode runs dry and says that no action is built yet. ``--dry-run``
runs dry whatever the mode is.

**What it never reads.** No file content, no secret. A target is a path, a
branch or a session name. It reads one machine, and the report holds only
that machine (7b.11.4 rule 6).

**Windows.** A check that needs ``/proc`` (the reaper) is skipped, and the
report says so. Git, ``gh`` and ``claude`` work the same on Git Bash.

Fence: ``tests/unit/test_idle_work.py``.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import socket
import subprocess
import sys
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HOME = Path(os.environ.get("HOME", "~")).expanduser()
HERE = Path(__file__).resolve().parent
REPO = Path(os.environ.get("IDLE_WORK_REPO", str(HOME / "Metorite")))
LANES_DIR = Path(os.environ.get("KEEPER_LANES_DIR", str(HOME / "lanes")))
PROJECTS = Path(os.environ.get("IDLE_WORK_PROJECTS", str(HOME / ".claude" / "projects")))
KEEP_FILE = Path(os.environ.get("IDLE_WORK_KEEP", str(HOME / ".claude" / "idle-keep.txt")))

DIRTY_IDLE_S = 6 * 3600
BRANCH_IDLE_S = 24 * 3600
SESSION_IDLE_S = 12 * 3600
#: Never touch, never even report as idle, what changed in the last hour (rule 2).
RECENT_S = 3600
BOX_HOST = "metorite-home"

#: 7b.11.4 rule 3: another person's branch. merge_bot.sh holds PROTECTED_HEAD_RE.
PERSON_HEAD_RE = re.compile(r"^(ishaan-|auto-revert/)")
PROTECTED_HEAD_RE = re.compile(r"^(main|master|release|release/.*|green/.*|HEAD)$")
SHORT_ID_RE = re.compile(r"\bmt-([0-9a-f]{8})\b")

Runner = Callable[..., tuple[int, str]]
#: A cold worktree can take minutes in ``git status``. One that passes this
#: limit gets an UNKNOWN line, never an action.
STATUS_TIMEOUT_S = float(os.environ.get("IDLE_WORK_STATUS_TIMEOUT_S", "60"))
#: Worktrees read at once.
JOBS = int(os.environ.get("IDLE_WORK_JOBS", "8"))
#: Every git read goes through this. ``--no-optional-locks`` keeps a read from
#: refreshing the index of a worktree, so the finder writes no file there.
GIT = ["git", "--no-optional-locks"]


def run(argv: list[str], cwd: Path | None = None, timeout: float = 120) -> tuple[int, str]:
    """Run one read command. A missing tool or a timeout is a failure, never a raise.

    A timeout returns 124, as ``timeout(1)`` does."""
    try:
        res = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return 124, ""
    except (OSError, subprocess.SubprocessError):
        return 127, ""
    return res.returncode, res.stdout


def has_proc() -> bool:
    return os.name != "nt" and Path("/proc").is_dir()


def _iso(t: float) -> str:
    return dt.datetime.fromtimestamp(t, dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _h(s: float) -> str:
    return f"{s / 3600:.0f} h"


class Finder:
    """One dry pass over one machine. Every outside call goes through ``self.run``."""

    def __init__(self, *, now: float, machine: str, person: bool, repo: Path = REPO,
                 runner: Runner = run, gh: str = "gh", claude: str = "claude",
                 projects: Path = PROJECTS, keep_file: Path = KEEP_FILE,
                 lanes_dir: Path = LANES_DIR, proc: bool | None = None,
                 runs_reader: Callable[[], list[dict] | None] | None = None):
        self.now, self.machine, self.person, self.repo = now, machine, person, repo
        self.run, self.gh, self.claude = runner, gh, claude
        self.projects, self.keep_file, self.lanes_dir = projects, keep_file, lanes_dir
        self.proc = has_proc() if proc is None else proc
        self.runs_reader = runs_reader
        self.lines: list[tuple[str, str, str, str]] = []

    def add(self, state: str, target: str, task: str | None, reason: str) -> None:
        clean = lambda v: re.sub(r"[\t\r\n]", " ", v)  # noqa: E731 - one line, one field
        self.lines.append((state, clean(target), clean(task or "-"), clean(reason)))

    def git(self, *args: str, cwd: Path | None = None, timeout: float = 120) -> tuple[int, str]:
        return self.run([*GIT, *args], cwd or self.repo, timeout=timeout)

    # ── worktrees: S1, S2 ───────────────────────────────────────────────────

    def worktrees(self) -> list[dict]:
        rc, out = self.git("worktree", "list", "--porcelain")
        if rc != 0:
            self.add("ERROR", str(self.repo), None, "git worktree list failed")
            return []
        trees, cur = [], {}
        for line in [*out.splitlines(), ""]:
            if not line.strip():
                if cur.get("path"):
                    trees.append(cur)
                cur = {}
                continue
            key, _, val = line.partition(" ")
            if key == "worktree":
                cur["path"] = val
            elif key == "branch":
                cur["branch"] = val.removeprefix("refs/heads/")
            elif key == "detached":
                cur["branch"] = None
        if trees:
            trees[0]["primary"] = True
        return trees

    def tree_facts(self, wt: dict) -> dict | None:
        """Dirty files, unpushed commits and the last change of one worktree.

        It runs in a worker thread, so it never calls ``self.add``. A failure
        comes back as ``{"error": reason}``."""
        path = Path(wt["path"])
        if not path.is_dir():
            return None
        rc, status = self.git("status", "--porcelain", cwd=path, timeout=STATUS_TIMEOUT_S)
        if rc == 124:
            return {"error": f"git status ran past {STATUS_TIMEOUT_S:.0f} s: state unknown"}
        if rc != 0:
            return {"error": "git status failed"}
        files = [ln[3:].split(" -> ")[-1].strip('"') for ln in status.splitlines() if len(ln) > 3]
        times = []
        for f in files:
            try:
                times.append((path / f).stat().st_mtime)
            except OSError:
                continue
        rc, head_t = self.git("log", "-1", "--format=%ct", cwd=path)
        if rc == 0 and head_t.strip().isdigit():
            times.append(float(head_t.strip()))
        rc, cnt = self.git("rev-list", "--count", "HEAD", "--not", "--remotes", cwd=path)
        unpushed = int(cnt.strip()) if rc == 0 and cnt.strip().isdigit() else 0
        last = max(times) if times else 0.0
        return {"dirty": len(files), "unpushed": unpushed, "idle": self.now - last}

    def find_trees(self) -> set[str]:
        """S1 and S2. Returns the paths that hold an item, for S4."""
        held: set[str] = set()
        trees = self.worktrees()
        with ThreadPoolExecutor(max_workers=max(1, JOBS)) as pool:
            all_facts = list(pool.map(self.tree_facts, trees))
        for wt, facts in zip(trees, all_facts, strict=True):
            if facts is None:
                continue
            if "error" in facts:
                held.add(wt["path"])  # unknown: never stop its session
                self.add("UNKNOWN", wt["path"], None, facts["error"])
                continue
            if facts["idle"] < RECENT_S:
                continue  # rule 2: changed in the last hour, so active
            task = self.task_of(wt.get("branch") or "", Path(wt["path"]))
            note = "primary checkout, report only" if wt.get("primary") else ""
            if facts["dirty"] and facts["idle"] > DIRTY_IDLE_S:
                held.add(wt["path"])
                self.add("S1", wt["path"], task, " ".join(filter(None, [
                    f"{facts['dirty']} uncommitted file(s), idle {_h(facts['idle'])}", note])))
            if facts["unpushed"] and facts["idle"] > DIRTY_IDLE_S:
                held.add(wt["path"])
                self.add("S2", wt["path"], task, " ".join(filter(None, [
                    f"{facts['unpushed']} commit(s) on no remote, idle {_h(facts['idle'])}", note])))
        return held

    def task_of(self, branch: str, cwd: Path | None = None) -> str | None:
        m = SHORT_ID_RE.search(branch)
        if m:
            return f"mt-{m.group(1)}"
        rc, msg = self.git("log", "-1", "--format=%B", cwd=cwd)
        m = SHORT_ID_RE.search(msg) if rc == 0 else None
        return f"mt-{m.group(1)}" if m else None

    # ── branches: S3 ────────────────────────────────────────────────────────

    def find_branches(self) -> None:
        rc, remote = self.git("ls-remote", "--heads", "origin")
        if rc != 0:
            self.add("ERROR", "origin", None, "git ls-remote failed")
            return
        on_origin = {ln.split("\t")[1].removeprefix("refs/heads/"): ln.split("\t")[0]
                     for ln in remote.splitlines() if "\t" in ln}
        rc, prs = self.run([self.gh, "pr", "list", "--state", "all", "--limit", "1000",
                            "--json", "headRefName"], self.repo)
        try:
            with_pr = {p["headRefName"] for p in json.loads(prs)} if rc == 0 else None
        except (ValueError, TypeError, KeyError):
            with_pr = None
        if with_pr is None:
            self.add("ERROR", "gh pr list", None, "cannot read the PR list, S3 skipped")
            return
        rc, local = self.git("for-each-ref", "--format=%(refname:short)\t%(committerdate:unix)", "refs/heads")
        for ln in local.splitlines() if rc == 0 else []:
            name, _, t = ln.partition("\t")
            if (name not in on_origin or name in with_pr or name.startswith("rescue/")
                    or PROTECTED_HEAD_RE.match(name) or PERSON_HEAD_RE.match(name)
                    or not t.strip().isdigit()):
                continue
            age = self.now - float(t)
            if age > BRANCH_IDLE_S:
                self.add("S3", name, self.task_of(name), f"pushed, no PR, last commit {_h(age)} ago")

    # ── sessions: S4 ────────────────────────────────────────────────────────

    def agents(self) -> list[dict] | None:
        rc, out = self.run([self.claude, "agents", "--json"], None, timeout=60)
        try:
            data = json.loads(out) if rc == 0 else None
        except ValueError:
            data = None
        if isinstance(data, dict):
            data = data.get("agents")
        return data if isinstance(data, list) else None

    def transcript_age(self, session_id: str) -> float | None:
        for p in self.projects.glob(f"*/{session_id}.jsonl"):
            return self.now - p.stat().st_mtime
        return None

    def names(self, path: Path) -> set[str]:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return set()
        return {ln.split("#", 1)[0].strip() for ln in text.splitlines()} - {""}

    def find_sessions(self, held: set[str], agents: list[dict] | None) -> None:
        if agents is None:
            self.add("ERROR", "claude agents", None, "cannot read the sessions, S4 skipped")
            return
        keeper = self.names(self.lanes_dir / "keeper-lanes.txt")
        keep = self.names(self.keep_file)
        for a in agents:
            sid, name = str(a.get("sessionId") or ""), str(a.get("name") or a.get("id") or "")
            age = self.transcript_age(sid) if sid else None
            if age is None or age < SESSION_IDLE_S:
                continue
            if any(str(a.get("cwd") or "").startswith(p) for p in held):
                continue  # its worktree holds unsaved work: S1 or S2 first
            why = [f"idle {_h(age)}"]
            if name in keep:
                why.append("in idle-keep.txt: never stopped")
            elif name in keeper:
                why.append("keeper lane: never stopped")
            elif self.person:
                why.append("a person's machine: report only")
            self.add("S4", name or sid, None, ", ".join(why))

    # ── runs: S5 ────────────────────────────────────────────────────────────

    def find_runs(self, agents: list[dict] | None) -> None:
        runs = self.runs_reader() if self.runs_reader else None
        if runs is None:
            self.add("S5", "-", None, "skipped: no Metorite token on this machine")
            return
        live = {str(a.get("name") or "") for a in agents or []}

        def plain(v: object) -> str:
            return str(v.get("untrusted_text") if isinstance(v, dict) else v or "")

        for r in runs:
            if plain(r.get("agent_host")) != self.machine:
                continue
            if not (r.get("stranded") or r.get("state") == "stale"):
                continue
            label = plain(r.get("agent_label"))
            if label and label in live:
                continue  # its session lives: the run sweep nudges it
            self.add("S5", str(r.get("id")), str(r.get("task_id") or "-"),
                     f"past its lease, session {label or 'unknown'} is gone")

    # ── rescue refs ─────────────────────────────────────────────────────────

    def find_rescues(self) -> None:
        rc, out = self.git("ls-remote", "origin", "refs/heads/rescue/*")
        if rc != 0:
            self.add("ERROR", "origin", None, "cannot list the rescue refs")
            return
        refs = [ln.split("\t") for ln in out.splitlines() if "\t" in ln]
        if not refs:
            return
        self.git("fetch", "-q", "origin", "+refs/heads/rescue/*:refs/remotes/origin/rescue/*")
        for sha, ref in refs:
            name = ref.removeprefix("refs/heads/")
            task = self.rescue_task(sha)
            self.add("RESCUE", name, task, "IDLE-5 decides S6 (a person) or S7 (a box lane)"
                     if task else "no task found: report only")

    def rescue_task(self, sha: str) -> str | None:
        """The task through the source branch, never the ref name (7b.11.3)."""
        rc, msg = self.git("log", "-1", "--format=%B", sha)
        m = SHORT_ID_RE.search(msg) if rc == 0 else None
        if m:
            return f"mt-{m.group(1)}"
        rc, out = self.git("branch", "-r", "--contains", f"{sha}^")
        for ln in out.splitlines() if rc == 0 else []:
            if "rescue/" in ln:
                continue
            m = SHORT_ID_RE.search(ln)
            if m:
                return f"mt-{m.group(1)}"
        return None

    # ── the states that other tools own (7b.11.1) ───────────────────────────

    def find_delegated(self) -> None:
        sweep = self.lanes_dir / "landing-sweeper.tsv"
        try:
            rows = [ln.split("\t") for ln in sweep.read_text(encoding="utf-8").splitlines() if ln]
        except OSError:
            rows = []
        if rows:
            last = rows[-1][0][:13]  # the hour of its last run
            acts = [r for r in rows if r[0].startswith(last) and len(r) > 3 and r[3] != "SKIP"]
            self.add("LBB-3", str(sweep), None, f"{len(acts)} PR action(s) in its last run")
        else:
            self.add("LBB-3", str(sweep), None, "no sweeper report on this machine")
        if not self.proc:
            self.add("REAPER", "scripts/reaper.py", None, "skipped: no /proc on this machine (Windows)")
            return
        reaper = self.repo / "scripts" / "reaper.py"
        rc, out = self.run([sys.executable, str(reaper), "--dry-run"], self.repo)
        n = sum(1 for ln in out.splitlines() if ln.startswith("would "))
        self.add("REAPER", "scripts/reaper.py", None,
                 f"{n} item(s) in its dry run" if rc == 0 else "its dry run failed")

    # ── the pass ────────────────────────────────────────────────────────────

    def scan(self) -> list[tuple[str, str, str, str]]:
        held = self.find_trees()
        self.find_branches()
        agents = self.agents()
        self.find_sessions(held, agents)
        self.find_runs(agents)
        self.find_rescues()
        self.find_delegated()
        return self.lines

    def report(self) -> list[str]:
        stamp = _iso(self.now)
        return [f"{stamp}\t{self.machine}\t{s}\twould\t{t}\t{k}\t{r}" for s, t, k, r in self.lines]


def metorite_runs() -> list[dict] | None:
    """The runs that wait for an agent, through the box token. None with no token."""
    sys.path[:0] = [str(HERE), str(HERE / "home_server")]
    try:
        import answer_watcher as aw  # the one MCP client of the box (task 1731)
    except ImportError:
        return None
    token = os.environ.get("METORITE_WATCHER_TOKEN", "").strip() or aw.token_from_helper()
    if not token:
        return None
    try:
        sid = aw.open_session(token)
        return aw.fetch_runs(token, sid)
    except Exception:  # a read failure is a skipped state, never a crash
        return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Report the idle work on this machine (IDLE-1, dry).")
    ap.add_argument("--dry-run", action="store_true", help="report, whatever IDLE_WORK_MODE says")
    ap.add_argument("--report", help="append the report here; '-' is stdout")
    a = ap.parse_args(argv)
    mode = os.environ.get("IDLE_WORK_MODE", "off").strip() or "off"
    if not a.dry_run and mode == "off":
        print("idle_work: IDLE_WORK_MODE is off. Nothing runs.")
        return 0
    if not a.dry_run and mode.startswith("apply"):
        print("idle_work: no action is built yet (IDLE-1). This run is dry.")
    machine = os.environ.get("IDLE_WORK_MACHINE") or socket.gethostname()
    person = os.environ.get("IDLE_WORK_PERSON", "0" if machine == BOX_HOST else "1") == "1"
    now = float(os.environ.get("IDLE_WORK_NOW") or time.time())
    f = Finder(now=now, machine=machine, person=person, runs_reader=metorite_runs,
               gh=os.environ.get("IDLE_WORK_GH", "gh"), claude=os.environ.get("IDLE_WORK_CLAUDE", "claude"))
    f.scan()
    lines = f.report()
    dest = a.report or os.environ.get("IDLE_WORK_REPORT") or (
        str(LANES_DIR / "idle-work.tsv") if LANES_DIR.is_dir() else "-")
    if dest == "-":
        print("\n".join(lines))
    else:
        with open(dest, "a", encoding="utf-8") as fh:
            fh.write("".join(ln + "\n" for ln in lines))
    counts: dict[str, int] = {}
    for s, *_ in f.lines:
        counts[s] = counts.get(s, 0) + 1
    print("idle_work: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())) + f" ({dest})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
