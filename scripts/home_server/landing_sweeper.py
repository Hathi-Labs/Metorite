#!/usr/bin/env python3
"""The landing sweeper of the home box, LBB-3a: a DRY REPORT (task 4043).

Spec: project-docs/specs/engineering_practice.md section 7b.5.

It reads every open PR and appends one line for each to
``~/lanes/landing-sweeper.tsv``: what it WOULD do. It writes nothing else
except its own clone and a log line on stdout. It calls no GitHub write (no
``gh api -X``, no ``gh pr merge|edit|comment|ready``), writes no queue line
and hands nothing over.

Mode: ``LANDING_SWEEPER_MODE`` is ``dry`` or ``off`` (the default). Any other
value reads as ``off``.

Test seams: ``LANDING_SWEEPER_GH`` (the gh command), ``LANDING_SWEEPER_FREEZE_CMD``
(the freeze check), ``LANDING_SWEEPER_LANES`` (the folder of the queue files and
the report) and ``LANDING_SWEEPER_SRC`` (the clone).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import re
import shlex
import subprocess
import sys
from typing import Any

REPO_URL_DEFAULT = "https://github.com/Hathi-Labs/Metorite.git"
GH_FIELDS = (
    "number,author,headRefName,headRefOid,baseRefName,isDraft,isCrossRepository,"
    "mergeable,title,body,createdAt"
)
#: Mirror of PROTECTED_HEAD_RE in scripts/home_server/merge_bot.sh (line 119).
PROTECTED_HEAD_RE = re.compile(r"^(main|master|release|release/.*|green/.*|HEAD)$")
VERIFY_RE = re.compile(r"^VERIFY: PASS ([0-9a-f]{7,40})", re.MULTILINE)
TIER_LINE_RE = re.compile(r"^Tier: (LOW|STANDARD|HIGH)\b", re.MULTILINE)
TIER_TITLE_RE = re.compile(r"\((HIGH|STANDARD|LOW)")
HOUR = 3600
SUBPROCESS_TIMEOUT = 120


class RunEnd(Exception):
    """An answer did not parse: end the run with exit 0 and one log line."""


def log(msg: str) -> None:
    stamp = dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"{stamp} landing-sweeper: {msg}", flush=True)


# ── Reading gh the way merge_bot.sh does ────────────────────────────────────


def newest_check_rows(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """The newest row for each check name.

    Mirror of PY_CHECKS in scripts/home_server/merge_bot.sh (line 303): a re-run
    adds a second row with the same name, and only the newest row counts. A
    queued row has no start time yet, so it is the newest.
    """

    def when(r: dict[str, Any]) -> str:
        start = r.get("startedAt") or ""
        start = "" if start.startswith("0001-") else start
        if not start and r.get("bucket") == "pending":
            return "9999"
        end = r.get("completedAt") or ""
        return start or ("" if end.startswith("0001-") else end)

    best: dict[str, dict[str, Any]] = {}
    for r in rows:
        name = r.get("name") or ""
        if name not in best or when(r) >= when(best[name]):
            best[name] = r
    return best


def required_names(d: dict[str, Any]) -> list[str]:
    """Mirror of PY_REQUIRED in merge_bot.sh (line 277)."""
    names = list(d.get("contexts") or []) + [c.get("context", "") for c in d.get("checks") or []]
    seen: list[str] = []
    for n in names:
        if n and n not in seen:
            seen.append(n)
    return seen


def parse_time(s: str | None) -> dt.datetime | None:
    if not s:
        return None
    try:
        t = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=dt.UTC)


# ── The sweeper ─────────────────────────────────────────────────────────────


class Sweeper:
    def __init__(self, env: dict[str, str]):
        home = pathlib.Path(os.path.expanduser("~"))
        self.lanes = pathlib.Path(env.get("LANDING_SWEEPER_LANES") or home / "lanes")
        self.src = pathlib.Path(env.get("LANDING_SWEEPER_SRC") or self.lanes / "sweeper-src")
        self.gh = shlex.split(env.get("LANDING_SWEEPER_GH") or "gh")
        self.freeze_cmd = env.get("LANDING_SWEEPER_FREEZE_CMD") or ""
        self.repo_url = env.get("LANDING_SWEEPER_REPO_URL") or REPO_URL_DEFAULT
        self.now = dt.datetime.now(dt.UTC)
        self.required: list[str] | None = None
        self.frozen: bool | None = None
        self.pp_cache: dict[str, str | None] = {}

    # -- process helpers (every failure is a (rc, out) pair, never a raise) --

    def _run(self, argv: list[str], cwd: pathlib.Path | None, stdin: str | None = None) -> tuple[int, str]:
        try:
            p = subprocess.run(
                argv, cwd=str(cwd) if cwd else None, input=stdin, capture_output=True,
                text=True, encoding="utf-8", errors="replace", timeout=SUBPROCESS_TIMEOUT,
            )
        except (OSError, subprocess.SubprocessError):
            return 127, ""
        return p.returncode, p.stdout

    def gh_(self, *args: str) -> tuple[int, str]:
        return self._run([*self.gh, *args], self.src)

    def git(self, *args: str) -> tuple[int, str]:
        return self._run(["git", *args], self.src)

    # -- the box files --

    def read_lines(self, name: str) -> list[str]:
        try:
            return (self.lanes / name).read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return []

    def handovers(self, pr: int) -> list[list[str]]:
        """Valid hand-over rows of the PR. Mirror of read_script in hand_over.sh (line 76)."""
        rows = []
        for line in self.read_lines("merge-bot-queue.tsv"):
            f = line.rstrip("\r").split("\t")
            if (len(f) == 7 and f[0] == str(pr) and len(f[4]) == 40
                    and re.fullmatch(r"[0-9a-f]+", f[4]) and f[5] in ("LOW", "STANDARD", "HIGH")):
                rows.append(f)
        return rows

    def results(self, pr: int) -> tuple[int, bool]:
        """(count of DONE and BACK lines, has a DONE line) in merge-bot.log."""
        n = 0
        done = False
        pat = re.compile(rf"^(DONE|BACK|RESOLVED) {pr}( |$)")
        for line in self.read_lines("merge-bot.log"):
            m = pat.match(line.rstrip("\r"))
            if m and m.group(1) in ("DONE", "BACK"):
                n += 1
                done = done or m.group(1) == "DONE"
        return n, done

    def requeued(self, pr: int) -> bool:
        for line in self.read_lines("merge-queue.tsv"):
            f = line.rstrip("\r").split("\t")
            if len(f) >= 2 and f[0] == "REQUEUE" and f[1] == str(pr):
                return True
        return False

    def held_by_merger(self, pr: int) -> str | None:
        hand = len(self.handovers(pr))
        res, done = self.results(pr)
        if hand > res and not done:
            return f"held by the merge bot: {hand} hand-over(s), {res} result line(s)"
        if self.requeued(pr):
            return "REQUEUE line in merge-queue.tsv"
        return None

    # -- the freeze --

    def is_frozen(self) -> bool:
        if self.frozen is None:
            cmd = shlex.split(self.freeze_cmd) if self.freeze_cmd else [
                "bash", str(self.src / "scripts" / "release_freeze.sh")]
            rc, _ = self._run(cmd, self.src)
            # release_freeze.sh: 0 clear, 75 frozen, 1 an answer it cannot read.
            # Anything else is also not "clear", so the sweeper waits.
            self.frozen = rc != 0
        return self.frozen

    # -- the clone --

    def ensure_clone(self) -> bool:
        if not (self.src / ".git").exists():
            self.src.parent.mkdir(parents=True, exist_ok=True)
            rc, _ = self._run(["git", "clone", "-q", self.repo_url, str(self.src)], None)
            if rc != 0:
                return False
        rc, _ = self.git("fetch", "-q", "origin", "+refs/heads/main:refs/remotes/origin/main")
        if rc != 0:
            return False
        if not self.freeze_cmd:
            # the default freeze check is a script of the clone: keep it at main
            self.git("checkout", "-q", "--detach", "origin/main")
        return True

    def fetch_pr(self, pr: int) -> bool:
        rc, _ = self.git("fetch", "-q", "origin", f"+refs/pull/{pr}/head:refs/sweeper-pull/{pr}")
        return rc == 0

    def commit_ok(self, sha: str) -> bool:
        rc, _ = self.git("rev-parse", "--verify", "-q", f"{sha}^{{commit}}")
        return rc == 0

    def is_ancestor(self, a: str, b: str) -> bool | None:
        rc, _ = self.git("merge-base", "--is-ancestor", a, b)
        return {0: True, 1: False}.get(rc)

    def own_patch_id(self, commit: str) -> str | None:
        """The patch-id of the own diff: git diff $(git merge-base origin/main C) C."""
        if commit in self.pp_cache:
            return self.pp_cache[commit]
        pid = None
        rc, base = self.git("merge-base", "origin/main", commit)
        if rc == 0 and base.strip():
            rc, diff = self.git("diff", base.strip(), commit)
            if rc == 0 and diff.strip():
                rc, out = self._run(["git", "patch-id", "--stable"], self.src, stdin=diff)
                if rc == 0 and out.split():
                    pid = out.split()[0]
        self.pp_cache[commit] = pid
        return pid

    # -- the rules --

    def load_required(self) -> list[str] | None:
        if self.required is None:
            rc, out = self.gh_("api", "repos/{owner}/{repo}/branches/main/protection/required_status_checks")
            if rc != 0:
                return None
            try:
                names = required_names(json.loads(out))
            except (ValueError, AttributeError, TypeError):
                raise RunEnd("the required-checks answer did not parse") from None
            if not names:
                return None
            self.required = names
        return self.required

    def classify(self, pr: dict[str, Any], others_bases: set[str]) -> tuple[str, str]:  # noqa: C901 - the rules of 7b.5 in their order
        n = pr.get("number")
        sha = pr.get("headRefOid") or ""
        head = pr.get("headRefName") or ""
        author = ((pr.get("author") or {}).get("login") or "")
        # Rule 1, the parts that need no gh call.
        if author == "ishaanpilar":
            return "SKIP", "author ishaanpilar"
        if head.startswith("ishaan-") or head.startswith("auto-revert/") or PROTECTED_HEAD_RE.match(head):
            return "SKIP", f"head branch {head} is not for the sweeper"
        if pr.get("isDraft"):
            return "SKIP", "draft"
        if pr.get("baseRefName") != "main":
            return "SKIP", f"base {pr.get('baseRefName')} is not main"
        if pr.get("isCrossRepository"):
            return "SKIP", "fork"
        if head in others_bases:
            return "SKIP", "head is the base of another open PR"
        mergeable = pr.get("mergeable") or "UNKNOWN"
        if mergeable == "UNKNOWN":
            return "SKIP", "mergeable is UNKNOWN"
        # Rule 1, the last commit age.
        rc, out = self.gh_("pr", "view", str(n), "--json", "commits")
        if rc != 0:
            return "SKIP", "error commits"
        try:
            commits = json.loads(out).get("commits") or []
            pushed = parse_time(commits[-1].get("committedDate"))
        except (ValueError, AttributeError, IndexError, TypeError):
            return "SKIP", "error commits"
        if pushed is None:
            return "SKIP", "error commits"
        if (self.now - pushed).total_seconds() < 24 * HOUR:
            return "SKIP", "last commit is less than 24 hours old"
        # Rule 2.
        why = self.held_by_merger(n)
        if why:
            return "WAIT", why
        # The checks.
        required = self.load_required()
        if required is None:
            return "SKIP", "error required"
        rc, out = self.gh_("pr", "checks", str(n), "--json", "name,bucket,link,startedAt,completedAt")
        # gh pr checks exits 8 (pending) or 1 (failed) and still prints rows: read the text.
        if rc != 0 and not out.strip():
            return "SKIP", "error checks"
        try:
            rows = json.loads(out) if out.strip() else []
            if not isinstance(rows, list):
                raise ValueError
        except ValueError:
            return "SKIP", "error checks"
        if rc not in (0, 1, 8) and not rows:
            return "SKIP", "error checks"
        best = newest_check_rows(rows)
        buckets = [(best.get(r) or {}).get("bucket") or "" for r in required]
        red = [r for r, b in zip(required, buckets, strict=False) if b == "fail"]
        green_n = sum(1 for b in buckets if b in ("pass", "skipping"))
        all_green = green_n == len(required)
        age_h = (self.now - (parse_time(pr.get("createdAt")) or self.now)).total_seconds() / HOUR
        old = age_h > 48
        # The freeze turns every action below into WAIT.
        frozen = self.is_frozen()

        def act(would: str, reason: str) -> tuple[str, str]:
            return ("WAIT", f"release freeze (would {would}: {reason})") if frozen else (would, reason)

        # Rule 3.
        if (mergeable == "CONFLICTING" or red) and old:
            if mergeable == "CONFLICTING":
                return act("LAND", "merge conflict, PR older than 48 hours")
            return act("LAND", "red checks: " + ", ".join(red) + ", PR older than 48 hours")
        # The clone, for rules 4 and 5.
        if not self.fetch_pr(n):
            return "SKIP", "error fetch"
        if not self.commit_ok(sha):
            return "SKIP", "error fetch"
        # Rule 4.
        holds_main = self.is_ancestor("origin/main", sha)
        if holds_main is None:
            return "SKIP", "error ancestry"
        if not holds_main and green_n == 0:
            return act("UPDATE", "head does not hold main and no required check is green")
        # Rule 5 and 6.
        verifies = VERIFY_RE.findall((pr.get("body") or "").replace("\r", ""))
        if all_green and verifies:
            return self.rule5(pr, n, sha, verifies[-1], act)
        if all_green and old:
            return act("LAND", "no verify sha")
        return "SKIP", "nothing to do"

    def rule5(self, pr: dict[str, Any], n: int, sha: str, vsha: str, act) -> tuple[str, str]:
        if not self.commit_ok(vsha):
            return "SKIP", "error verify sha"
        # A short sha resolves only from the objects of main and of this PR head.
        in_main = self.is_ancestor(vsha, "origin/main")
        in_head = self.is_ancestor(vsha, sha)
        if in_main is None or in_head is None or not (in_main or in_head):
            return "SKIP", "error verify sha"
        head_pid = self.own_patch_id(sha)
        if head_pid is None or head_pid != self.own_patch_id(vsha):
            return "SKIP", "nothing to do"
        body = (pr.get("body") or "").replace("\r", "")
        m = TIER_LINE_RE.search(body) or TIER_TITLE_RE.search(pr.get("title") or "")
        tier = m.group(1) if m else "HIGH"
        if tier == "HIGH":
            return act("REQUEUE", f"verified at {vsha[:12]}, tier HIGH")
        for row in self.handovers(n):
            if self.commit_ok(row[4]) and self.own_patch_id(row[4]) == head_pid:
                return act("HANDOVER", f"verified at {vsha[:12]}, tier {tier}, earlier hand-over by {row[3]}")
        return act("REQUEUE", f"verified at {vsha[:12]}, tier {tier}, no earlier hand-over at this patch-id")

    # -- the run --

    def run(self) -> int:
        if not self.ensure_clone():
            log("cannot make or fetch the clone: no report this run")
            return 0
        rc, out = self.gh_("pr", "list", "--state", "open", "--limit", "300", "--json", GH_FIELDS)
        if rc != 0:
            log("gh pr list failed: no report this run")
            return 0
        try:
            prs = json.loads(out)
            if not isinstance(prs, list):
                raise ValueError
            prs = [p for p in prs if isinstance(p, dict) and isinstance(p.get("number"), int)]
        except ValueError:
            log("the answer of gh pr list did not parse: no report this run")
            return 0
        stamp = self.now.strftime("%Y-%m-%dT%H:%M:%SZ")
        lines = []
        try:
            for pr in prs:
                others = {p.get("baseRefName") or "" for p in prs if p is not pr}
                would, reason = self.classify(pr, others)
                reason = re.sub(r"\s+", " ", reason).strip()
                lines.append(f"{stamp}\t{pr['number']}\t{pr.get('headRefOid') or ''}\t{would}\t{reason}\n")
        except RunEnd as e:
            log(f"{e}: no report this run")
            return 0
        self.lanes.mkdir(parents=True, exist_ok=True)
        with open(self.lanes / "landing-sweeper.tsv", "a", encoding="utf-8", newline="") as f:
            f.writelines(lines)
        log(f"dry report: {len(lines)} open PR(s)")
        return 0


def main(env: dict[str, str] | None = None) -> int:
    env = dict(os.environ if env is None else env)
    mode = (env.get("LANDING_SWEEPER_MODE") or "off").strip()
    if mode == "off":
        return 0
    if mode != "dry":
        log(f"LANDING_SWEEPER_MODE={mode!r} is not a mode of LBB-3a: read as off")
        return 0
    try:
        return Sweeper(env).run()
    except Exception as e:  # fails safe: a defect must never reach a GitHub write
        log(f"unexpected {type(e).__name__}: no report this run")
        return 0


if __name__ == "__main__":
    sys.exit(main())
