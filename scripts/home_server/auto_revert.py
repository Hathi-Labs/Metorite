#!/usr/bin/env python3
"""CIV-31 (D218 point 4): undo the PR that broke main. One pass per call.

Spec: project-docs/specs/ci_velocity.md section 8.8. Metorite task 3056.

    python3 scripts/home_server/auto_revert.py run

A user timer of `dev` on the home box runs one pass every 5 minutes
(systemd/metorite-auto-revert.*). The pass runs the CIV-5 poller
(bisect_poll.sh). When the poller names the first bad commit of a red push
batch of main, the pass checks that commit, makes its revert, and proves it.

The flag AUTO_REVERT_MODE (the code ships dark):
  off  Unset, empty or an unknown value is off too. The pass does nothing.
  dry  The pass does every check and the local revert. It writes one log
       line and one report. It writes nothing to GitHub and nothing to main.
  on   The pass also pushes the branch auto-revert/<sha>, and opens a revert
       PR with the trailer "Auto-Revert: <sha>". It hands that PR to the
       merge bot with lane_handover.sh, and comments on the culprit's PR.
       The queue line has the task 0 and names a temp worktree that the
       pass then removes. The culprit's task is in the note only, so
       lane-merge never closes it (lanes/lane-merge.md).

Before the probes, the pass stops when main holds a revert of the culprit,
when the log or an open PR already has a revert of it, and at the rate brake.

Fail closed. Each doubt stops the revert and writes a PERSON line:
  - The poller names no single commit, or no failed test id.
  - The culprit is not on main, is a merge commit, or is a revert.
  - Main already holds a revert of the culprit, and the run is red. The same
    for a revert PR that merged or closed. A run of a tree from before that
    revert or fix is an old run (deploy.yml does not cancel a batch): SKIP.
  - The culprit touches a HIGH path or a migration, or its title says HIGH.
    A revert cannot undo a migration (R6). This stops `dry` too.
  - 3 reverts happened in the last hour (the rate brake).
  - The failed tests run 3 times on each of three trees: the tip of main
    (want fail), the culprit (want fail) and its parent (want pass). One
    other answer stops the revert. That is a flake, or a fix landed.
  - The revert does not apply cleanly, or the failed tests fail on it.
  - A step of `on` fails, or the merge bot sends the revert PR back.
  - Main moves on each of the MAIN_READS reads after the probes.
  - The pass ends early (SIGTERM, a crash). It writes its line first. A stop
    of the unit writes it before the cleanup of the probe worktrees.
When main moves during the probes, the pass reads it again, until it holds
still. When the failed tests pass on a new tip, a fix landed, and the pass
makes no revert. Else it makes the revert again on the new tip.
While the log holds a revert PR open, each pass reads the PR on GitHub first.
A person can merge it (REVERTED) or close it (SKIP) by hand. That ends the
watch. While it is open, the pass runs the failed tests on each new tip of
main. When they pass, a fix landed: the pass closes the PR and writes SKIP.
A failed close runs again on the next pass. A doubt writes one PERSON line,
and then the watch belongs to a person. The merge bot merges a revert PR only
when open/<pr>.json holds the tip of main now (merge_bot.sh revert_tested).
scripts/premerge.sh refuses a PR that lands a reverted patch again unchanged.

It never pushes to main, never force-pushes, never merges, and never
dispatches a workflow. The merge bot merges the revert PR. The box has no
Metorite write credential. lane-merge opens the culprit's task again after
the merge. The log line and the report name the task and the failed tests.

Files (defaults in brackets):
  AUTO_REVERT_LOG [~/lanes/auto-revert.log]    one line per decision
  AUTO_REVERT_REPORTS [~/lanes/auto-revert]    one report per red run
  AUTO_REVERT_STATE [~/.cache/metorite-auto-revert]   the lock, and one file
      in open/ per open revert PR (open_state). merge_bot.sh reads it too.
  MERGE_LOG [~/lanes/merge-bot.log]            read only: DONE and BACK lines
Commands, for a test or another setup:
  AUTO_REVERT_REPO_DIR [this checkout]   a clone with the remote `origin`
  AUTO_REVERT_POLL_CMD [bash scripts/home_server/bisect_poll.sh]
  AUTO_REVERT_HANDOVER_CMD [bash scripts/home_server/lane_handover.sh]
  BISECT_TEST_CMD [the default of bisect_main.sh]  the probe. bash -c runs it
      in a temp worktree, and the test ids are "$@". Exit 0 is a pass, exit 1
      is a fail. Any other code means "cannot judge", and the pass stops.
  AUTO_REVERT_TIMEOUT_S [1800]   the limit of one probe
  GH_CMD [gh]
Fence: tests/unit/test_auto_revert.py.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
MODES = ("off", "dry", "on")
#: The rate brake (D218): the most reverts in one hour.
MAX_REVERTS_PER_HOUR = 3
#: A lock older than this is from a dead pass. The unit stops a pass at 14 hours
#: (TimeoutStartSec in systemd/metorite-auto-revert.service).
STALE_LOCK_S = 15 * 3600
#: The same probe as the default of scripts/bisect_main.sh.
DEFAULT_TEST_CMD = 'uv run python -m pytest -x -q -p no:cacheprovider -- "$@"'
BRANCH_PREFIX = "auto-revert/"
#: The merge bot refuses HIGH. A revert goes in as STANDARD.
HANDOVER_TIER = "STANDARD"
HANDOVER_LANE = "auto-revert"
#: The queue task of a revert. lane-merge closes the queue task on DONE, and
#: the culprit's task must stay open. The note names the culprit's task.
HANDOVER_TASK = "0"
#: Each check of the flake re-run runs this many times, with one answer.
FLAKE_RUNS = 3
#: The reads of main after the probes (P2). A read that finds main still ends
#: them. When the last read finds a new tip again, a person acts.
MAIN_READS = 3
#: The HIGH paths of the merge bot: HIGH_PATHS_RE in merge_bot.sh (CIV-28b,
#: CIV-31b). This is a mirror, an extended regex. The fence
#: test_the_high_paths_equal_the_merge_bot_list keeps the two equal.
HIGH_PATHS_RE = "".join(
    [
        "^(",
        "infra/(postgres|customer_console)/",
        r"|deploy/|\.github/workflows/deploy\.yml$",
        r"|scripts/(apply_[a-z_]*migrations\.sh|gen_tenant_migration\.py)$",
        "|packages/acb_auth/",
        r"|packages/acb_common/acb_common/(db|tenant_redis|provisioning|org_settings)\.py$",
        r"|packages/acb_graph/acb_graph/db\.py$",
        r"|apps/services/customer_console/customer_console/(db|seats|operator_[^/]*)\.py$",
        "|packages/acb_memory/acb_memory/tenant_guard[^/]*$",
        r"|apps/services/gateway/gateway/routes/admin/(_common|members|roles|groups)\.py$",
        "|workbench/[^/]+/src/app/api/admin/",
        r"|apps/services/gateway/gateway/studio/(batch_store|objects_store)\.py$",
        "|apps/services/gateway/gateway/kb_access/",
        "|workbench/[^/]+/src/app/.*/(auth|billing|credits)/",
        ")",
        r"|(^|/)(auth|billing|credits|rls|entitle|permission|grant)[^/]*\.(py|ts|tsx|sql)$",
        r"|(^|/)([^/]*_)?(tenancy|visibility)[^/]*\.(py|ts|tsx|sql)$",
        r"|(^|/)([^/]*_)?access(_[^/]*)?\.(py|sql)$",
        r"|(^|/)([^/]*_)?(signin|signup|oauth|seats|share|sharing|shared_token|report_scope)(_[^/]*)?\.(py|sql)$",
        "|(^|/)(tenant|[^/]*_(rls|row_rules))[^/]*$",
    ]
)
HIGH_WORD_RE = re.compile(r"\bHIGH\b")

# The verbs of the log. A lane session reads the log, as it reads merge-bot.log.
WOULD_REVERT = "WOULD-REVERT"  # dry: the revert that `on` would open
REVERT = "REVERT"  # on: a revert PR is open and handed over
REVERTED = "REVERTED"  # on: the merge bot merged the revert PR
SKIP = "SKIP"  # the culprit has a revert already, so nothing more
PERSON = "PERSON"  # no revert, or a revert that failed: a person acts

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
TASK_URL_RE = re.compile(r"https://app\.metorite\.com/projects\?task=([0-9a-fA-F-]{36})")
BRANCH_TASK_RE = re.compile(r"(?:^|[^0-9a-z])mt-([0-9a-f]{8})(?:$|[^0-9a-f])")
TASK_NUM_RE = re.compile(r"\btask (\d+)\b", re.IGNORECASE)
TRAILER_RE = re.compile(r"^Auto-Revert: [0-9a-f]{40}\s*$", re.MULTILINE)
REVERTS_LINE_RE = re.compile(r"^This reverts commit [0-9a-f]{7,40}", re.MULTILINE)
FIELD_RE = re.compile(r"^(\w+)=(\S+)$")


class Stop(Exception):
    """A doubt. The pass makes no revert, and a person reads the reason."""

    def __init__(self, reason: str, **fields: str) -> None:
        super().__init__(reason)
        self.reason = reason
        self.fields = fields


def say(msg: str) -> None:
    print(f"auto_revert: {msg}", file=sys.stderr, flush=True)


def stamp(when: dt.datetime) -> str:
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def bash() -> str:
    return shutil.which("bash") or "/bin/bash"


def one_line(text: str, limit: int = 300) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 3] + "..."


def read_mode(raw: str | None) -> tuple[str, str]:
    """Return (mode, note). Unset, empty or unknown is off: the flag ships dark."""
    value = (raw or "").strip().lower()
    if value in MODES:
        return value, ""
    if not value:
        return "off", ""
    return "off", f"AUTO_REVERT_MODE={raw!r} is not off, dry or on. The pass acts as off."


def _cmd(name: str, default: list[str]) -> list[str]:
    raw = os.environ.get(name, "").strip()
    return shlex.split(raw) if raw else default


@dataclass
class Config:
    mode: str
    repo: Path
    log: Path
    reports: Path
    state: Path
    merge_log: Path
    poll_cmd: list[str]
    handover_cmd: list[str]
    gh: list[str]
    test_cmd: str
    timeout_s: int

    @classmethod
    def from_env(cls, mode: str) -> Config:
        home = Path.home()
        env = os.environ.get
        return cls(
            mode=mode,
            repo=Path(env("AUTO_REVERT_REPO_DIR") or ROOT),
            log=Path(env("AUTO_REVERT_LOG") or home / "lanes" / "auto-revert.log"),
            reports=Path(env("AUTO_REVERT_REPORTS") or home / "lanes" / "auto-revert"),
            state=Path(env("AUTO_REVERT_STATE") or home / ".cache" / "metorite-auto-revert"),
            merge_log=Path(env("MERGE_LOG") or home / "lanes" / "merge-bot.log"),
            poll_cmd=_cmd(
                "AUTO_REVERT_POLL_CMD", [bash(), str(ROOT / "scripts/home_server/bisect_poll.sh")]
            ),
            handover_cmd=_cmd(
                "AUTO_REVERT_HANDOVER_CMD",
                [bash(), str(ROOT / "scripts/home_server/lane_handover.sh")],
            ),
            gh=_cmd("GH_CMD", ["gh"]),
            test_cmd=env("BISECT_TEST_CMD") or DEFAULT_TEST_CMD,
            timeout_s=int(env("AUTO_REVERT_TIMEOUT_S") or "1800"),
        )


# ── Processes ───────────────────────────────────────────────────────────────


def run(
    cmd: list[str], cwd: Path | None = None, stdin: str | None = None, timeout: float | None = None
) -> subprocess.CompletedProcess[str]:
    """Run a command and keep its output. A limit stops its whole process group."""
    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        start_new_session=os.name != "nt",
    )
    try:
        out, err = proc.communicate(stdin, timeout=timeout)
    except BaseException:
        # A limit, or a SIGTERM of the pass, stops the child and its children.
        if os.name != "nt":
            with contextlib.suppress(OSError):
                os.killpg(proc.pid, signal.SIGKILL)
        proc.kill()
        proc.communicate()
        raise
    return subprocess.CompletedProcess(cmd, proc.returncode, out, err)


def git(repo: Path, *args: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    return run(["git", "-C", str(repo), *args], stdin=stdin)


def git_out(repo: Path, *args: str) -> str:
    res = git(repo, *args)
    if res.returncode != 0:
        raise Stop(
            f"git {' '.join(args)} failed: {one_line(res.stderr) or 'exit ' + str(res.returncode)}."
        )
    return res.stdout.strip()


@contextmanager
def worktree(repo: Path, sha: str) -> Iterator[Path]:
    """A detached worktree of `sha` in a temp dir. It is removed on every exit."""
    tmp = Path(tempfile.mkdtemp(prefix="auto-revert."))
    wt = tmp / "wt"
    try:
        res = git(repo, "worktree", "add", "--detach", "-q", str(wt), sha)
        if res.returncode != 0:
            raise Stop(f"git cannot make a worktree of {sha[:12]}: {one_line(res.stderr)}.")
        yield wt
    finally:
        git(repo, "worktree", "remove", "--force", str(wt))
        shutil.rmtree(tmp, ignore_errors=True)
        git(repo, "worktree", "prune")


@contextmanager
def lock(state: Path) -> Iterator[bool]:
    """One pass at a time. Yields False when another pass holds the lock."""
    state.mkdir(parents=True, exist_ok=True)
    held = state / "lock.d"
    if held.exists() and time.time() - held.stat().st_mtime > STALE_LOCK_S:
        say(f"cleared a stale lock: {held} is older than {STALE_LOCK_S // 3600} hours")
        shutil.rmtree(held, ignore_errors=True)
    try:
        held.mkdir()
    except FileExistsError:
        yield False
        return
    try:
        yield True
    finally:
        shutil.rmtree(held, ignore_errors=True)


# ── The log: one line per decision, and the memory of the brakes ───────────


@dataclass
class Entry:
    when: dt.datetime | None
    verb: str
    fields: dict[str, str]


class Ledger:
    def __init__(self, path: Path) -> None:
        self.path = path

    def write(self, verb: str, text: str, quiet: bool = False) -> None:
        """Append one line. quiet: no echo to stderr (the SIGTERM handler).

        A print to stderr inside a signal handler can meet a print of the main
        code, and then Python raises "RuntimeError: reentrant call".
        """
        line = f"{stamp(now_utc())} {verb} {one_line(text, 600)}"
        if not quiet:
            say(line)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(line + "\n")

    def entries(self) -> list[Entry]:
        if not self.path.exists():
            return []
        out: list[Entry] = []
        for raw in self.path.read_text(encoding="utf-8", errors="replace").splitlines():
            parts = raw.split()
            if len(parts) < 2:
                continue
            try:
                when = dt.datetime.strptime(parts[0], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.UTC)
            except ValueError:
                when = None
            fields = {}
            for token in parts[2:]:
                m = FIELD_RE.match(token)
                if m and m.group(1) not in fields:
                    fields[m.group(1)] = m.group(2)
            out.append(Entry(when, parts[1], fields))
        return out

    def recent(self, verb: str, seconds: int, now: dt.datetime | None = None) -> int:
        """The count of `verb` lines in the last `seconds`. A line with no time counts."""
        start = (now or now_utc()) - dt.timedelta(seconds=seconds)
        return sum(
            1 for e in self.entries() if e.verb == verb and (e.when is None or e.when >= start)
        )

    def find(self, verb: str, key: str, value: str) -> Entry | None:
        for e in self.entries():
            if e.verb == verb and e.fields.get(key) == value:
                return e
        return None


# ── The report: one file per red run ───────────────────────────────────────


class Report:
    def __init__(self, mode: str, result: dict[str, Any]) -> None:
        self.mode = mode
        self.run_id = re.sub(r"[^0-9A-Za-z_-]", "_", str(result.get("run_id") or "unknown"))
        ids = result.get("test_ids") if isinstance(result.get("test_ids"), list) else []
        self.lines = [
            f"# Auto-revert report: batch run {self.run_id}",
            "",
            f"- Time: {stamp(now_utc())}",
            f"- Mode: {mode}",
            f"- Good: {result.get('good_sha') or 'none'}",
            f"- Bad: {result.get('bad_sha') or 'none'}",
            f"- Bisect: exit {result.get('exit_code')}, {result.get('result') or 'no result'}",
            "- Failed tests:",
            *[f"  - {i}" for i in ids],
            "",
            "## Checks",
            "",
        ]

    def fact(self, text: str) -> None:
        self.lines.append(f"- {text}")

    def block(self, title: str, text: str) -> None:
        self.lines += ["", f"## {title}", "", text.rstrip(), ""]

    def write(self, folder: Path, decision: str, quiet: bool = False) -> Path:
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"run-{self.run_id}.md"
        body = "\n".join([*self.lines, "", "## Decision", "", decision, ""])
        path.write_text(body, encoding="utf-8", newline="\n")
        if not quiet:
            say(f"report: {path}")
        return path


# ── The steps ──────────────────────────────────────────────────────────────


def poll(cfg: Config) -> dict[str, Any] | None:
    """Run the CIV-5 poller. Return its BISECT_RESULT, or None for no new red run."""
    say("the poller runs: " + " ".join(cfg.poll_cmd))
    # stderr goes to the journal: the bisect writes its probes there.
    proc = subprocess.run(
        cfg.poll_cmd,
        cwd=cfg.repo,
        stdout=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        say(
            f"the poller failed (exit {proc.returncode}). It keeps the run, and the next pass tries again."
        )
        return None
    found = [ln for ln in proc.stdout.splitlines() if ln.startswith("BISECT_RESULT ")]
    if not found:
        say("no new red batch run.")
        return None
    try:
        data = json.loads(found[-1].split(" ", 1)[1])
    except ValueError:
        return {
            "exit_code": None,
            "result": "the poller printed a result that does not parse: " + found[-1],
        }
    return (
        data
        if isinstance(data, dict)
        else {"exit_code": None, "result": "the poller result is not an object"}
    )


def probe(cfg: Config, sha: str, ids: list[str]) -> str:
    """Run the failed tests on `sha`. Return pass, fail, or unknown (why)."""
    with worktree(cfg.repo, sha) as wt:
        try:
            res = run([bash(), "-c", cfg.test_cmd, "probe", *ids], cwd=wt, timeout=cfg.timeout_s)
        except subprocess.TimeoutExpired:
            return f"unknown (the probe ran past {cfg.timeout_s} s)"
    tail = (res.stdout + res.stderr).strip().splitlines()[-5:]
    for line in tail:
        say(f"  probe {sha[:12]}: {line}")
    return {0: "pass", 1: "fail"}.get(res.returncode, f"unknown (exit {res.returncode})")


def is_revert(message: str) -> bool:
    subject = message.splitlines()[0] if message else ""
    return subject.startswith('Revert "') or bool(
        TRAILER_RE.search(message) or REVERTS_LINE_RE.search(message)
    )


def culprit_pr(cfg: Config, pr: str) -> dict[str, Any]:
    """Read the culprit's PR. A read is allowed in dry. A failed read gives {}."""
    if not pr:
        return {}
    res = run([*cfg.gh, "pr", "view", pr, "--json", "title,body,headRefName,url"], cwd=cfg.repo)
    if res.returncode != 0:
        say(f"gh cannot read PR {pr}: {one_line(res.stderr)}")
        return {}
    try:
        data = json.loads(res.stdout)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def task_refs(subject: str, info: dict[str, Any]) -> tuple[str, str]:
    """Return (the task number for the hand-over, a task reference for a person)."""
    num = TASK_NUM_RE.search(f"{subject}\n{info.get('title') or ''}")
    url = TASK_URL_RE.search(str(info.get("body") or ""))
    short = BRANCH_TASK_RE.search(str(info.get("headRefName") or ""))
    refs = []
    if num:
        refs.append(f"task {num.group(1)}")
    if url:
        refs.append(url.group(0))
    if short:
        refs.append(f"mt-{short.group(1)}")
    return (num.group(1) if num else "0"), (", ".join(refs) or "none found")


def make_revert(cfg: Config, tip: str, culprit: str, message: str) -> str:
    """Commit the revert of `culprit` on `tip` in a temp worktree. Return its sha."""
    with worktree(cfg.repo, tip) as wt:
        res = git(wt, "revert", "--no-commit", culprit)
        if res.returncode != 0:
            files = git(wt, "diff", "--name-only", "--diff-filter=U").stdout.split()
            git(wt, "revert", "--abort")
            raise Stop(
                f"The revert of {culprit[:12]} does not apply cleanly on {tip[:12]}: "
                f"{' '.join(files) or one_line(res.stderr)}."
            )
        if git(wt, "diff", "--cached", "--quiet").returncode == 0:
            raise Stop(f"The revert of {culprit[:12]} changes nothing on {tip[:12]}.")
        # --no-verify: a revert holds only old text, and pr-check tests the PR.
        res = git(wt, "commit", "-q", "--no-verify", "-F", "-", stdin=message)
        if res.returncode != 0:
            raise Stop(f"The git commit of the revert failed: {one_line(res.stderr)}.")
        return git_out(wt, "rev-parse", "HEAD")


def follow_up(cfg: Config, ledger: Ledger) -> None:
    """Watch each revert PR until it merges or closes.

    Each BACK gets one PERSON line, with back=<n> for the nth BACK. A person can
    hand the PR over again, so a PERSON line does not end the watch. A DONE
    ends it, with one REVERTED line. watch() ends it too, when GitHub says that
    the PR is merged or closed: a REVERTED line, or a SKIP line with revert_pr.
    """
    opened: dict[str, str] = {}
    ended: set[str] = set()
    told: set[tuple[str, str]] = set()
    for e in ledger.entries():
        pr = e.fields.get("revert_pr")
        if not pr:
            continue
        if e.verb == REVERT:
            opened[pr] = e.fields.get("culprit", "")
        elif e.verb in (REVERTED, SKIP):
            ended.add(pr)
        elif e.verb == PERSON and "back" in e.fields:
            told.add((pr, e.fields["back"]))
    pending = [pr for pr in opened if pr not in ended]
    if not pending:
        return
    merge_lines = (
        cfg.merge_log.read_text(encoding="utf-8", errors="replace").splitlines()
        if cfg.merge_log.exists()
        else []
    )
    backs: dict[str, int] = {}
    for raw in merge_lines:
        m = re.match(r"^(DONE|BACK) (\d+)(?: (.*))?$", raw.strip())
        if not m or m.group(2) not in pending or m.group(2) in ended:
            continue
        pr, rest = m.group(2), m.group(3) or ""
        if m.group(1) == "DONE":
            ledger.write(REVERTED, f"revert_pr={pr} culprit={opened[pr]} merge={rest or 'unknown'}")
            ended.add(pr)
            open_state(cfg, pr).unlink(missing_ok=True)
            continue
        backs[pr] = backs.get(pr, 0) + 1
        # "BACK <pr> closed": watch() reads the PR and writes the true line.
        if (pr, str(backs[pr])) in told or rest.strip() == "closed":
            continue
        ledger.write(
            PERSON,
            f"mode=on revert_pr={pr} back={backs[pr]} culprit={opened[pr]} The merge bot sent the "
            f"revert PR back: {rest}. A person merges it, or closes it and fixes main.",
        )
    for pr in pending:
        if pr not in ended:
            watch(cfg, ledger, pr, opened[pr])


def open_state(cfg: Config, pr: str) -> Path:
    """The file of an open revert PR. merge_bot.sh reads its "tip" too.

    Keys: culprit, run, ids (the failed tests), tip (the last tip of main with a
    sure answer, at first the base of the revert), and three marks that a pass
    can add: close_at (a fix tip whose close must run again), close_told (the
    PERSON line of a failed close is written), person (a doubt went to a person).
    """
    return cfg.state / "open" / f"{pr}.json"


def write_open(cfg: Config, pr: str, data: dict[str, Any]) -> None:
    """Keep what recheck_main needs. A failed write only stops the recheck of this PR."""
    path = open_state(cfg, pr)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8", newline="\n")
    except OSError as exc:
        say(f"cannot keep the state of revert PR {pr}: {exc}.")


def read_open(cfg: Config, pr: str) -> dict[str, Any] | None:
    try:
        data = json.loads(open_state(cfg, pr).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def pr_state(cfg: Config, pr: str) -> tuple[str, str] | None:
    """(state, merge sha) of a PR on GitHub, or None when gh cannot read it."""
    res = run([*cfg.gh, "pr", "view", pr, "--json", "state,mergeCommit"], cwd=cfg.repo)
    try:
        data = json.loads(res.stdout) if res.returncode == 0 else None
    except ValueError:
        data = None
    if not isinstance(data, dict) or not data.get("state"):
        return None
    merge = data.get("mergeCommit")
    return str(data["state"]), str(merge.get("oid") or "") if isinstance(merge, dict) else ""


def watch(cfg: Config, ledger: Ledger, pr: str, culprit: str) -> None:
    """One look at a revert PR that the log still holds open (P1).

    GitHub answers first, because a person can merge or close the PR by hand.
    MERGED: a REVERTED line, so lane-merge opens the culprit's task again.
    CLOSED: a SKIP line that says who closed it. Both end the watch. A failed
    read changes nothing, and the next pass reads it again. OPEN: recheck_main.
    """
    seen = pr_state(cfg, pr)
    if seen is None:
        say(f"revert PR {pr}: gh cannot read it. The next pass tries again.")
        return
    state, merge = seen
    data = read_open(cfg, pr)
    if state == "MERGED":
        ledger.write(
            REVERTED,
            f"revert_pr={pr} culprit={culprit} merge={merge or 'unknown'} by=hand "
            "A person merged the revert PR. The merge bot has no DONE line for it.",
        )
        open_state(cfg, pr).unlink(missing_ok=True)
        return
    if state == "CLOSED":
        closing = str((data or {}).get("close_at") or "")
        if closing:
            # The close of an earlier pass landed, though gh reported a failure.
            text = (
                f"fixed_at={closing} The failed tests passed on {closing[:12]}, "
                "and the pass closed the revert PR."
            )
        else:
            text = "closed_by=person A person closed the revert PR. The pass watches it no more."
        ledger.write(SKIP, f"mode=on culprit={culprit} revert_pr={pr} {text}")
        open_state(cfg, pr).unlink(missing_ok=True)
        return
    if state != "OPEN":
        say(f"revert PR {pr}: the state is {state}. The next pass reads it again.")
        return
    if data is None:
        say(f"revert PR {pr}: no state file, so no recheck of main.")
        return
    recheck_main(cfg, ledger, pr, data)


def recheck_main(cfg: Config, ledger: Ledger, pr: str, data: dict[str, Any]) -> None:
    """A fix can land while the revert PR waits for pr-check (P2). Test each new tip.

    The failed tests run on each new tip of main, FLAKE_RUNS times. When each
    run passes, a fix landed: the pass closes the PR and writes SKIP. A failed
    close runs again on each pass. A fail on the first run keeps the PR, and
    "tip" moves to the new tip, so the merge bot can merge the PR. Any other
    answer (a flake, a test that a later commit deleted, a probe that cannot
    start) writes one PERSON line. Then the watch belongs to a person: no more
    probes and no more lines. "tip" stays, so the merge bot holds the PR.
    """
    try:
        culprit, run_id, ids, last = data["culprit"], data["run"], data["ids"], data["tip"]
    except (KeyError, TypeError):
        say(f"revert PR {pr}: the state file does not parse, so no recheck of main.")
        return
    head = f"mode=on run={run_id} culprit={culprit} revert_pr={pr}"
    if data.get("person"):
        return
    if data.get("close_at"):
        close_after_fix(cfg, ledger, pr, data, head)
        return
    try:
        tip = fetch_main(cfg, culprit)
    except Stop as stop:
        say(f"revert PR {pr}: {stop.reason} The next pass tries again.")
        return
    if tip == last:
        return
    answers: list[str] = []
    try:
        for _ in range(FLAKE_RUNS):
            answers.append(probe(cfg, tip, ids))
            if answers[-1] != "pass":
                break
    except Stop as stop:
        # P3: git cannot make the probe worktree. poll() still runs after this.
        answers.append(f"unknown ({stop.reason})")
    if answers == ["pass"] * FLAKE_RUNS:
        data["close_at"] = tip
        write_open(cfg, pr, data)
        close_after_fix(cfg, ledger, pr, data, head)
        return
    if answers == ["fail"]:
        # A sure answer: main is still red. The merge bot can merge the PR.
        data["tip"] = tip
        write_open(cfg, pr, data)
        return
    ledger.write(
        PERSON,
        f"{head} The failed tests give {', '.join(answers)} on the new tip {tip[:12]} of main. "
        "A flake, a fix in part, or a test that a later commit deleted. The revert PR "
        "stays open, and the merge bot holds it. A person merges it or closes it.",
    )
    data["person"] = tip
    write_open(cfg, pr, data)


def close_after_fix(cfg: Config, ledger: Ledger, pr: str, data: dict[str, Any], head: str) -> None:
    """Close the revert PR, because the failed tests pass on data["close_at"] (P2).

    A failed close writes one PERSON line and stays in the state file, so the
    next pass tries again. Until then, "tip" is old, and the merge bot holds the PR.
    """
    fix = str(data["close_at"])
    why = f"The failed tests pass {FLAKE_RUNS} times on the tip {fix[:12]} of main."
    note = f"{why} A later commit fixed main, so the auto-revert closes this PR (CIV-31)."
    shut = run([*cfg.gh, "pr", "close", pr, "--comment", note], cwd=cfg.repo)
    if shut.returncode == 0:
        ledger.write(
            SKIP, f"{head} fixed_at={fix} {why} A fix landed, so the pass closed the revert PR."
        )
        open_state(cfg, pr).unlink(missing_ok=True)
        return
    say(f"revert PR {pr}: gh pr close failed: {one_line(shut.stderr)}. The next pass tries again.")
    if not data.get("close_told"):
        ledger.write(
            PERSON,
            f"{head} {why} gh pr close failed: {one_line(shut.stderr)}. Each pass tries "
            "again, and the merge bot holds the PR. A person can close it.",
        )
        data["close_told"] = True
        write_open(cfg, pr, data)


@dataclass
class Case:
    """One red run and its culprit, as the checks find them."""

    run_id: str
    culprit: str
    pr: str
    ids: list[str]
    tip: str = ""
    first_tip: str = ""
    parent: str = ""
    subject: str = ""
    title: str = ""
    task_num: str = "0"
    task_ref: str = "none found"
    revert_sha: str = ""
    #: The head sha of the red run (bad_sha of the poller): the tree it tested.
    bad: str = ""

    @property
    def branch(self) -> str:
        return f"{BRANCH_PREFIX}{self.culprit[:12]}"

    def tests(self, fmt: str = "- {}\n") -> str:
        return "".join(fmt.format(i) for i in self.ids)


class EarlyEnd:
    """The PERSON line and the report of a pass that ends early. Written once.

    The poller already took the run, so the pass must leave a line. A stop of
    the unit writes it in the SIGTERM handler (on_sigterm), BEFORE the finally
    blocks remove the probe worktrees. That removal can be slow on the HDD of
    the box, and systemd sends SIGKILL 90 s after SIGTERM (P3).
    """

    #: The pass that runs now. handle() sets it, and on_sigterm reads it.
    now: EarlyEnd | None = None

    def __init__(self, cfg: Config, ledger: Ledger, rep: Report) -> None:
        self.cfg = cfg
        self.ledger = ledger
        self.rep = rep
        self.written = False

    def write(self, cause: str, quiet: bool = False) -> None:
        if self.written:
            return
        # A pushed branch or an open PR can exist: a person checks for them.
        why = f"The pass ended early: {cause}. A person checks for a branch auto-revert/<sha>."
        self.ledger.write(
            PERSON, f"mode={self.cfg.mode} run={self.rep.run_id} No revert. {why}", quiet=quiet
        )
        # Only a line on disk counts. A SIGTERM during the write finds written False,
        # and the handler writes the line.
        self.written = True
        self.rep.write(self.cfg.reports, f"NO REVERT. {why}", quiet=quiet)


def on_sigterm(_signum: int, _frame: object) -> None:
    """systemd stops a pass with SIGTERM. Write the PERSON line first, then end.

    sys.exit raises SystemExit, so the finally blocks remove the worktrees after it.
    The handler prints nothing to stderr (Ledger.write).
    """
    if EarlyEnd.now is not None:
        EarlyEnd.now.write("SystemExit(143), a SIGTERM from the stop of the unit", quiet=True)
    sys.exit(143)


def handle(cfg: Config, ledger: Ledger, result: dict[str, Any]) -> int:
    """Act on one BISECT_RESULT. Each doubt is a PERSON line and a report."""
    rep = Report(cfg.mode, result)
    ending = EarlyEnd(cfg, ledger, rep)
    EarlyEnd.now = ending
    try:
        decision = decide(cfg, ledger, result, rep)
    except Stop as stop:
        extra = "".join(f" {k}={v}" for k, v in stop.fields.items())
        # After the PR opens, the revert exists, and a person finishes it.
        head = "The revert stopped." if "revert_pr" in stop.fields else "No revert."
        ledger.write(PERSON, f"mode={cfg.mode} run={rep.run_id}{extra} {head} {stop.reason}")
        # The line is on disk. A SIGTERM before this point writes the early-end line.
        ending.written = True
        rep.write(cfg.reports, f"{head.upper()} A person reads this. {stop.reason}")
        return 0
    except BaseException as exc:
        # Fail closed. A crash, a SIGTERM (SystemExit) or a stop of the unit ends
        # the pass. on_sigterm wrote the line already, so this writes no second one.
        ending.write(repr(exc))
        if isinstance(exc, Exception):
            return 1
        raise
    finally:
        EarlyEnd.now = None
    rep.write(cfg.reports, decision)
    return 0


def read_case(result: dict[str, Any], rep: Report) -> Case:
    """The culprit and the failed tests of the poller's result, or a Stop."""
    line = str(result.get("result") or "")
    ids = [i for i in result.get("test_ids") or [] if isinstance(i, str) and i.strip()]
    if result.get("exit_code") != 0 or not line.startswith("FIRST_BAD "):
        raise Stop(
            f"The bisect names no single commit (exit {result.get('exit_code')}: {line or 'no result'})."
        )
    if not ids:
        raise Stop(
            "The run names no failed test, so nothing can prove a revert. Read the JUnit report of the run."
        )
    culprit = line.split()[1]
    if not SHA_RE.match(culprit):
        raise Stop(f"The bisect result names no full commit sha: {line}.")
    pr = re.search(r"PR=(\d+)\s*$", line)
    case = Case(run_id=rep.run_id, culprit=culprit, pr=pr.group(1) if pr else "", ids=ids)
    case.bad = str(result.get("bad_sha") or "")
    rep.fact(f"Culprit: {culprit} (PR {case.pr or 'none'}).")
    return case


def fetch_main(cfg: Config, culprit: str) -> str:
    """Fetch main, and return its tip. A failed fetch is a Stop."""
    fetch = git(
        cfg.repo, "fetch", "-q", "--no-tags", "origin", "+refs/heads/main:refs/remotes/origin/main"
    )
    if fetch.returncode != 0:
        raise Stop(f"git fetch of main failed: {one_line(fetch.stderr)}.", culprit=culprit)
    return git_out(cfg.repo, "rev-parse", "refs/remotes/origin/main")


def check_culprit(cfg: Config, case: Case, rep: Report) -> None:
    """The culprit is one squash commit on main, and it is not a revert."""
    c = case.culprit
    case.tip = fetch_main(cfg, c)
    rep.fact(f"Tip of main: {case.tip}.")
    if git(cfg.repo, "cat-file", "-e", f"{c}^{{commit}}").returncode != 0:
        raise Stop(f"This clone does not have the culprit {c[:12]}.", culprit=c)
    if git(cfg.repo, "merge-base", "--is-ancestor", c, case.tip).returncode != 0:
        raise Stop(f"The culprit {c[:12]} is not on main.", culprit=c)
    parents = git_out(cfg.repo, "rev-list", "--parents", "-n", "1", c).split()[1:]
    if len(parents) != 1:
        raise Stop(
            f"The culprit {c[:12]} has {len(parents)} parents. Main takes squash merges, "
            "so the pass does not revert a merge commit or a root commit.",
            culprit=c,
        )
    case.parent = parents[0]
    message = git_out(cfg.repo, "log", "-1", "--format=%B", c)
    case.subject = message.splitlines()[0] if message else c[:12]
    if is_revert(message):
        raise Stop(f"The culprit {c[:12]} is a revert. A revert is never reverted.", culprit=c)


def check_brakes(cfg: Config, ledger: Ledger, case: Case) -> str:
    """The brakes before any probe. Return a decision to skip, or an empty string."""
    c = case.culprit
    # First main: a revert that landed and a run that is still red is a PERSON
    # line, also when the log has that revert (P2-a).
    on_main = git_out(
        cfg.repo, "log", case.tip, "-F", f"--grep=Auto-Revert: {c}", "--format=%H", "-n", "1"
    )
    if on_main:
        if tested_before(cfg, case, on_main):
            return skip(ledger, cfg, case, old_run(case, on_main, "the revert"))
        raise Stop(
            f"Main already holds the revert {on_main[:12]} of {c[:12]}, and the run is red. "
            "The red has another cause.",
            culprit=c,
        )
    verb = REVERT if cfg.mode == "on" else WOULD_REVERT
    earlier = ledger.find(verb, "culprit", c)
    revert_pr = earlier.fields.get("revert_pr", "") if earlier else ""
    end = revert_end(ledger, revert_pr)
    if end is not None:
        # The revert PR merged or closed. A run of a later tree that is red needs a person.
        since = end.fields.get("fixed_at") or end.fields.get("merge", "")
        if tested_before(cfg, case, since):
            return skip(
                ledger, cfg, case, old_run(case, since, "the end of revert PR " + revert_pr)
            )
        raise Stop(
            f"{ended_text(end, revert_pr, c)} Now a run is red again. A person reads it.",
            culprit=c,
        )
    if earlier is not None:
        note = (
            f"revert_pr={earlier.fields['revert_pr']}"
            if "revert_pr" in earlier.fields
            else f"{verb} line"
        )
        return skip(ledger, cfg, case, f"The log has a revert of it ({note}).")
    other = open_revert_pr(cfg, case)
    if other:
        return skip(ledger, cfg, case, f"The open PR {other} reverts it already.")
    done = ledger.recent(verb, 3600)
    if done >= MAX_REVERTS_PER_HOUR:
        raise Stop(
            f"The rate brake holds: {done} reverts in the last hour, and the limit is {MAX_REVERTS_PER_HOUR}.",
            culprit=c,
        )
    return ""


def tested_before(cfg: Config, case: Case, sha: str) -> bool:
    """True when the red run tested a tree from before `sha`: an old run (P2).

    deploy.yml does not cancel a run in progress, so a batch that started
    before a fix or a revert can end red after it. The head sha of the run
    stands for the tree that it tested (the known limit of the bad commit). A
    doubt (no sha, or a git error) is False, so the brake stays a PERSON line.
    """
    if not (SHA_RE.match(case.bad) and SHA_RE.match(sha)):
        return False
    return git(cfg.repo, "merge-base", "--is-ancestor", sha, case.bad).returncode == 1


def old_run(case: Case, sha: str, what: str) -> str:
    return (
        f"The run tested {case.bad[:12]}, from before {what} at {sha[:12]}. "
        "An old run of main, so no new revert."
    )


def revert_end(ledger: Ledger, revert_pr: str) -> Entry | None:
    """The line that ended the watch of a revert PR: REVERTED, or a SKIP with revert_pr."""
    if not revert_pr:
        return None
    return ledger.find(REVERTED, "revert_pr", revert_pr) or ledger.find(
        SKIP, "revert_pr", revert_pr
    )


def ended_text(end: Entry, revert_pr: str, culprit: str) -> str:
    if end.verb == REVERTED:
        merge = end.fields.get("merge", "unknown")
        return f"The revert PR {revert_pr} of {culprit[:12]} merged ({merge[:12]})."
    if end.fields.get("fixed_at"):
        return (
            f"The pass closed its revert PR {revert_pr} of {culprit[:12]}, because a fix "
            f"landed at {end.fields['fixed_at'][:12]}."
        )
    return f"A person closed the revert PR {revert_pr} of {culprit[:12]}."


def skip(ledger: Ledger, cfg: Config, case: Case, why: str) -> str:
    ledger.write(SKIP, f"mode={cfg.mode} run={case.run_id} culprit={case.culprit} {why}")
    return f"NO NEW REVERT of {case.culprit[:12]}. {why}"


def open_revert_pr(cfg: Config, case: Case) -> str:
    """The number of an open PR that reverts the culprit, or an empty string (P2-c).

    lane-merge or a person can open a revert with `git revert`. Its body or its
    title names the culprit. A failed search is a doubt, so it stops the pass.
    """
    res = run(
        [
            *cfg.gh,
            *("pr", "list", "--state", "open", "--search", case.culprit, "--limit", "50"),
            *("--json", "number,title,body"),
        ],
        cwd=cfg.repo,
    )
    try:
        rows = json.loads(res.stdout) if res.returncode == 0 else None
    except ValueError:
        rows = None
    if not isinstance(rows, list):
        raise Stop(
            f"gh cannot search the open PRs for a revert of {case.culprit[:12]}: "
            f"{one_line(res.stderr) or 'no answer'}.",
            culprit=case.culprit,
        )
    title = f'Revert "{case.subject}"'
    for row in rows:
        text = f"{row.get('title') or ''}\n{row.get('body') or ''}"
        if f"This reverts commit {case.culprit}" in text or (row.get("title") or "") == title:
            return str(row.get("number"))
    return ""


def high_paths(paths: list[str]) -> list[str]:
    """The paths that the merge bot calls HIGH (HIGH_PATHS_RE)."""
    return [p for p in paths if re.search(HIGH_PATHS_RE, p)]


def check_high(cfg: Config, case: Case, rep: Report) -> None:
    """A HIGH culprit goes to a person, in dry too (P1-c).

    A revert of a migration deletes the file, but each database that applied it
    keeps the schema (R6: no rollback). A HIGH path needs the full loop (D218),
    and a revert PR has only its own proof.
    """
    changed = git_out(cfg.repo, "diff", "--no-renames", "--name-only", case.parent, case.culprit)
    hits = high_paths(changed.splitlines())
    if hits:
        rep.fact(f"HIGH paths: {' '.join(hits[:5])}.")
        raise Stop(
            f"The culprit {case.culprit[:12]} touches a HIGH path ({' '.join(hits[:3])}). "
            "A revert cannot undo a migration, and a HIGH path needs a person.",
            culprit=case.culprit,
        )
    for name, text in (("subject", case.subject), ("PR title", case.title)):
        if HIGH_WORD_RE.search(text):
            raise Stop(
                f"The {name} of the culprit {case.culprit[:12]} says HIGH. A HIGH slice needs a person.",
                culprit=case.culprit,
            )


def rerun_for_flake(cfg: Config, case: Case, rep: Report) -> None:
    """The failed tests on three trees, FLAKE_RUNS times each (P3-a).

    Every run must give the wanted answer. One other answer stops the revert.
    """
    for name, sha, want in (
        ("the tip of main", case.tip, "fail"),
        ("the culprit", case.culprit, "fail"),
        ("the parent of the culprit", case.parent, "pass"),
    ):
        same(cfg, case, rep, name, sha, want)


def same(cfg: Config, case: Case, rep: Report, name: str, sha: str, want: str) -> None:
    """Run the failed tests on `sha` FLAKE_RUNS times. Each run must give `want`."""
    for n in range(1, FLAKE_RUNS + 1):
        got = probe(cfg, sha, case.ids)
        rep.fact(f"Run {n} of {FLAKE_RUNS} on {name} {sha[:12]}: {got} (want {want}).")
        if got != want:
            why = (
                "The pass cannot judge it."
                if got.startswith("unknown")
                else "A flake, or a later commit fixed it."
            )
            raise Stop(
                f"The failed tests {got} on {name} {sha[:12]} (run {n} of {FLAKE_RUNS}), "
                f"and the pass wants {want}. {why}",
                culprit=case.culprit,
            )


def build_and_prove(cfg: Config, case: Case, rep: Report) -> None:
    """Make the revert on case.tip and run the failed tests on it once."""
    case.revert_sha = make_revert(cfg, case.tip, case.culprit, revert_message(case))
    rep.fact(f"Revert commit: {case.revert_sha} on {case.tip[:12]}.")
    got = probe(cfg, case.revert_sha, case.ids)
    rep.fact(f"Proof on the revert tree {case.revert_sha[:12]}: {got} (want pass).")
    if got != "pass":
        raise Stop(
            f"The failed tests {got} on the revert tree. The revert does not fix main.",
            culprit=case.culprit,
        )


def read_main_again(cfg: Config, ledger: Ledger, case: Case, rep: Report) -> str:
    """Main can move during the probes (P1-b, P2). Read it again until it holds still.

    When the failed tests pass on a new tip, a fix landed: no revert. Else the
    revert is made again on the new tip, and main is read again. The new probes
    take time too, so main can move again. The pass reads main at most
    MAIN_READS times. When the last read finds a new tip again, a person acts.
    Return a decision to skip, or "".
    """
    for read in range(1, MAIN_READS + 1):
        tip = fetch_main(cfg, case.culprit)
        if tip == case.tip:
            rep.fact(f"Read {read} of main: it did not move during the probes ({tip}).")
            return ""
        rep.fact(f"Read {read} of main: it moved during the probes, from {case.tip} to {tip}.")
        if read == MAIN_READS:
            break
        case.first_tip = case.first_tip or case.tip
        case.tip = tip
        if fixed_on_new_tip(cfg, case, rep):
            return skip(
                ledger,
                cfg,
                case,
                f"The failed tests pass on the new tip {tip[:12]} (first tip {case.first_tip[:12]}). "
                "A later commit fixed main. No revert.",
            )
        build_and_prove(cfg, case, rep)
    raise Stop(
        f"Main moved on each of {MAIN_READS} reads after the probes (last tip {tip[:12]}). "
        "The pass cannot prove the revert on a tip that holds still.",
        culprit=case.culprit,
    )


def fixed_on_new_tip(cfg: Config, case: Case, rep: Report) -> bool:
    """Run the failed tests on case.tip. True: they pass, so a fix landed.

    A first pass is enough to stop the revert. Else they must fail FLAKE_RUNS
    times. One other answer stops the revert.
    """
    tip = case.tip
    for n in range(1, FLAKE_RUNS + 1):
        got = probe(cfg, tip, case.ids)
        rep.fact(f"Run {n} of {FLAKE_RUNS} on the new tip of main {tip[:12]}: {got} (want fail).")
        if n == 1 and got == "pass":
            return True
        if got != "fail":
            why = (
                "The pass cannot judge it."
                if got.startswith("unknown")
                else "A flake, or a later commit fixed it."
            )
            raise Stop(
                f"The failed tests {got} on the new tip {tip[:12]} (run {n} of {FLAKE_RUNS}). {why}",
                culprit=case.culprit,
            )
    return False


def revert_message(case: Case) -> str:
    return (
        f'Revert "{case.subject}"\n\n'
        f"This reverts commit {case.culprit}.\n\n"
        f"The push batch run {case.run_id} of main is red. The bisect names this commit as the\n"
        "first bad commit. The failed tests pass on this revert tree (CIV-31).\n\n"
        f"Failed tests:\n{case.tests()}\n"
        f"Auto-Revert: {case.culprit}\n"
    )


def pr_body(case: Case) -> str:
    return (
        f"Auto-revert (CIV-31). The push batch run {case.run_id} of main is red. The bisect names "
        f"{case.culprit[:12]} (PR {('#' + case.pr) if case.pr else 'none'}) as the first bad commit.\n\n"
        f"Failed tests:\n{case.tests('- `{}`' + chr(10))}\n"
        f"Checks ({FLAKE_RUNS} runs of each, with one answer):\n"
        f"- The failed tests fail on the tip of main {(case.first_tip or case.tip)[:12]}.\n"
        f"- They fail on {case.culprit[:12]}, and pass on its parent {case.parent[:12]}.\n"
        + (
            f"- Main moved to {case.tip[:12]} during the checks, and they fail there too.\n"
            if case.first_tip
            else ""
        )
        + f"- They pass on this revert tree {case.revert_sha[:12]}, on {case.tip[:12]}.\n\n"
        f"Task of the reverted PR: {case.task_ref}. A person opens it again with the failed tests.\n\n"
        f"VERIFY: PASS (auto-revert: the failed tests pass on the revert tree {case.revert_sha[:12]})\n"
        "Visual review: a revert. It adds no new UI.\n\n"
        f"Auto-Revert: {case.culprit}\n"
    )


def pr_comment(case: Case, revert: str) -> str:
    return (
        f"Auto-revert (CIV-31): the push batch run {case.run_id} of main is red, and the bisect names "
        f"this PR's commit {case.culprit[:12]}. The revert PR is {revert}.\n\n"
        f"Failed tests:\n{case.tests('- `{}`' + chr(10))}\n"
        f"Task: {case.task_ref}. Open the task again, fix the cause, and land the change again. "
        "scripts/premerge.sh refuses the same patch unchanged.\n"
    )


def decide(cfg: Config, ledger: Ledger, result: dict[str, Any], rep: Report) -> str:
    case = read_case(result, rep)
    check_culprit(cfg, case, rep)
    held = check_brakes(cfg, ledger, case)
    if held:
        return held
    info = culprit_pr(cfg, case.pr)
    case.title = str(info.get("title") or "")
    case.task_num, case.task_ref = task_refs(case.subject, info)
    rep.fact(f"Task of the culprit: {case.task_ref}.")
    check_high(cfg, case, rep)
    rerun_for_flake(cfg, case, rep)
    build_and_prove(cfg, case, rep)
    held = read_main_again(cfg, ledger, case, rep)
    if held:
        return held

    if cfg.mode == "dry":
        ledger.write(
            WOULD_REVERT,
            f"run={case.run_id} culprit={case.culprit} pr={case.pr or 'none'} revert={case.revert_sha[:12]} "
            f"tests={len(case.ids)} Task: {case.task_ref}.",
        )
        rep.block(
            "The PR that `on` would open",
            f'Branch: {case.branch}\n\nTitle: Revert "{case.subject}"\n\n{pr_body(case)}',
        )
        rep.block(
            "The comment that `on` would post",
            f"On PR {case.pr or 'none'}:\n\n{pr_comment(case, '(the new PR)')}",
        )
        rep.block(
            "The task that a person opens again", f"{case.task_ref}, with the failed tests above."
        )
        return f"WOULD REVERT {case.culprit[:12]} (dry: nothing written to GitHub or main)."
    return open_revert(cfg, ledger, rep, case)


def open_revert(cfg: Config, ledger: Ledger, rep: Report, case: Case) -> str:
    """Mode on: push the revert branch, open the PR, hand it over, comment."""
    c = case.culprit
    ref = f"refs/heads/{case.branch}"
    # Never main, never a force push: no "+" and no --force on the refspec.
    if not ref.startswith(f"refs/heads/{BRANCH_PREFIX}") or ref == "refs/heads/main":
        raise Stop(f"The pass refuses to push to {ref}.", culprit=c)
    push = git(cfg.repo, "push", "-q", "origin", f"{case.revert_sha}:{ref}")
    if push.returncode != 0:
        raise Stop(f"git push of {case.branch} failed: {one_line(push.stderr)}.", culprit=c)
    rep.fact(f"Pushed {case.branch} at {case.revert_sha[:12]}.")

    revert_pr = create_pr(cfg, case)
    ledger.write(
        REVERT,
        f"run={case.run_id} culprit={c} revert_pr={revert_pr} branch={case.branch} pr={case.pr or 'none'} "
        f"tests={len(case.ids)} Task: {case.task_ref}.",
    )
    rep.fact(f"Opened revert PR {revert_pr}.")
    # recheck_main tests each later tip of main while the PR is open (P2).
    write_open(cfg, revert_pr, {"culprit": c, "run": case.run_id, "ids": case.ids, "tip": case.tip})

    view = run([*cfg.gh, "pr", "view", revert_pr, "--json", "headRefOid,state"], cwd=cfg.repo)
    try:
        head = json.loads(view.stdout).get("headRefOid") if view.returncode == 0 else None
    except ValueError:
        head = None
    if head != case.revert_sha:
        raise Stop(
            f"Revert PR {revert_pr} has the head {str(head)[:12]}, not {case.revert_sha[:12]}. No hand-over.",
            culprit=c,
            revert_pr=revert_pr,
        )

    # The queue task is 0 (P1-a): lane-merge closes the queue task on DONE, and
    # the culprit's task must open again. The note names the culprit's task.
    note = (
        f"Auto-Revert of {c[:12]} (run {case.run_id}). Culprit task: {case.task_ref}. "
        "Open it again after the merge, never close it."
    )
    with worktree(cfg.repo, case.revert_sha) as wt:
        hand = run(
            [
                *cfg.handover_cmd,
                revert_pr,
                HANDOVER_TASK,
                str(wt),
                HANDOVER_LANE,
                HANDOVER_TIER,
                one_line(note),
            ],
            cwd=wt,
        )
    if hand.returncode != 0:
        raise Stop(
            f"Revert PR {revert_pr} is open, and the hand-over to the merge bot failed: "
            f"{one_line(hand.stderr or hand.stdout)}. A person hands it over or merges it.",
            culprit=c,
            revert_pr=revert_pr,
        )
    rep.fact(f"Handed revert PR {revert_pr} to the merge bot ({HANDOVER_TIER}).")

    if case.pr:
        said = gh_with_file(cfg, ["pr", "comment", case.pr], pr_comment(case, f"#{revert_pr}"))
        if said.returncode != 0:
            raise Stop(
                f"Revert PR {revert_pr} is open and handed over. The comment on PR {case.pr} failed: "
                f"{one_line(said.stderr)}. A person tells the owner of {case.task_ref}.",
                culprit=c,
                revert_pr=revert_pr,
            )
        rep.fact(f"Commented on PR {case.pr}.")
    rep.block(
        "The task that a person opens again", f"{case.task_ref}, with the failed tests above."
    )
    return f"REVERT PR {revert_pr} is open for {c[:12]}, and the merge bot has it."


def gh_with_file(cfg: Config, args: list[str], text: str) -> subprocess.CompletedProcess[str]:
    """Run a gh command with --body-file. The file lives in a temp dir."""
    with tempfile.TemporaryDirectory(prefix="auto-revert-body.") as tmp:
        body = Path(tmp) / "body.md"
        body.write_text(text, encoding="utf-8", newline="\n")
        return run([*cfg.gh, *args, "--body-file", str(body)], cwd=cfg.repo)


def create_pr(cfg: Config, case: Case) -> str:
    """Open the revert PR. Return its number, or a Stop that names the pushed branch."""
    made = gh_with_file(
        cfg,
        [
            "pr",
            "create",
            "--base",
            "main",
            "--head",
            case.branch,
            "--title",
            f'Revert "{case.subject}"',
        ],
        pr_body(case),
    )
    number = re.search(r"/pull/(\d+)", made.stdout or "")
    if made.returncode != 0 or not number:
        raise Stop(
            f"The branch {case.branch} is pushed, and gh pr create failed: {one_line(made.stderr or made.stdout)}.",
            culprit=case.culprit,
        )
    return number.group(1)


def run_pass() -> int:
    mode, note = read_mode(os.environ.get("AUTO_REVERT_MODE"))
    if note:
        say(note)
    if mode == "off":
        say("AUTO_REVERT_MODE is off. The pass does nothing.")
        return 0
    cfg = Config.from_env(mode)
    with lock(cfg.state) as held:
        if not held:
            say("another pass runs.")
            return 0
        ledger = Ledger(cfg.log)
        if cfg.mode == "on":
            follow_up(cfg, ledger)
        result = poll(cfg)
        if result is None:
            return 0
        return handle(cfg, ledger, result)


def main(argv: list[str]) -> int:
    # systemd stops a pass with SIGTERM. on_sigterm writes the PERSON line, and
    # then the finally blocks remove the worktrees.
    signal.signal(signal.SIGTERM, on_sigterm)
    if argv[:1] == ["run"]:
        return run_pass()
    print("usage: auto_revert.py run", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
