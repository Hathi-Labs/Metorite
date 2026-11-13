"""CIV-31 (D218 point 4): scripts/home_server/auto_revert.py, and the re-land
check of scripts/premerge.sh.

A temp git repo with a bare origin, a fake `gh`, a fake poller and a small
probe serve every case. Seven commits carry the subjects "(#100)" to "(#106)".
The commit of PR 104 breaks `calc.add`, so the test `test_calc.py::test_add`
fails from it on. The `on` case runs the REAL lane_handover.sh against the
fake `gh`, so its queue line is the line that the merge bot reads.

The fake `gh` writes each call to `gh_calls`, one JSON list per line. A test
reads that file to prove what the pass wrote to GitHub, and what it did not.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "home_server" / "auto_revert.py"
PREMERGE = ROOT / "scripts" / "premerge.sh"
UNITS = ROOT / "scripts" / "home_server" / "systemd"
PY = Path(sys.executable).as_posix()
TEST_ID = "test_calc.py::test_add"
TASK_URL = "https://app.metorite.com/projects?task=7191b0bd-cfb3-4c02-becf-82e49eb222a5"


def _bash() -> str | None:
    if os.name == "nt":
        # Git's bash, not WSL's bash.exe, as in test_premerge.py.
        git_bash = Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Git/bin/bash.exe"
        return str(git_bash) if git_bash.is_file() else None
    return shutil.which("bash")


BASH = _bash()
pytestmark = pytest.mark.skipif(
    BASH is None or shutil.which("git") is None, reason="needs bash and git"
)

GIT_ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.com",
}

FAKE_GH_SH = '#!/usr/bin/env bash\nexec "$PY" "$(dirname "$0")/gh.py" "$@"\n'

FAKE_GH_PY = r"""
import json, os, subprocess, sys

fix = os.environ["FIX"]
args = sys.argv[1:]
with open(os.path.join(fix, "gh_calls"), "a", encoding="utf-8", newline="\n") as f:
    f.write(json.dumps(args) + "\n")


def opt(name):
    return args[args.index(name) + 1] if name in args else None


def path(name):
    return os.path.join(fix, name)


def load(name):
    if not os.path.exists(path(name)):
        return None
    with open(path(name), encoding="utf-8") as f:
        return json.load(f)


def emit(data):
    keep = opt("--json")
    if keep:
        data = {k: data.get(k) for k in keep.split(",")}
    query = opt("-q") or opt("--jq")
    if query:
        value = data.get(query.lstrip("."))
        sys.stdout.write(("" if value is None else str(value)) + "\n")
        return
    sys.stdout.write(json.dumps(data) + "\n")


fail = os.environ.get("FAKE_GH_FAIL", "")
if fail and " ".join(args).startswith(fail):
    sys.stderr.write("HTTP 502: a fake failure\n")
    sys.exit(1)
verb = " ".join(args[:2])
if verb == "pr view":
    data = load("pr_%s.json" % args[2])
    if data is None:
        sys.stderr.write("GraphQL: Could not resolve to a PullRequest\n")
        sys.exit(1)
    emit(data)
elif verb == "pr create":
    head = opt("--head")
    with open(opt("--body-file"), encoding="utf-8") as f:
        body = f.read()
    sha = subprocess.run(
        ["git", "--git-dir", os.environ["ORIGIN"], "rev-parse", "refs/heads/" + head],
        capture_output=True, text=True,
    ).stdout.strip()
    with open(path("pr_900.json"), "w", encoding="utf-8") as f:
        json.dump({"number": 900, "title": opt("--title"), "body": body, "headRefName": head,
                   "headRefOid": sha, "state": "OPEN", "baseRefName": opt("--base")}, f)
    print("https://github.com/o/r/pull/900")
elif verb == "pr comment":
    with open(opt("--body-file"), encoding="utf-8") as f:
        body = f.read()
    with open(path("comment_%s.txt" % args[2]), "w", encoding="utf-8") as f:
        f.write(body)
elif verb == "pr list":
    # pr_list.json holds the open PRs that a search finds.
    keep = opt("--json")
    rows = load("pr_list.json") or []
    if keep and not (opt("-q") or opt("--jq")):
        rows = [{k: r.get(k) for k in keep.split(",")} for r in rows]
        sys.stdout.write(json.dumps(rows) + "\n")
    else:
        print("")
else:
    sys.stderr.write("unexpected gh call: %s\n" % " ".join(args))
    sys.exit(9)
"""

# The probe. It logs the commit it runs on. PROBE_OVERRIDE maps a sha to an
# exit code, or to a list of exit codes for the 1st, 2nd... run, so a test
# can make one tree flaky. A file BROKEN fails it, and a file FIXED passes it.
# PROBE_HOOK lands one commit on main while the pass runs, on the first probe
# of a revert tree: "fix" adds FIXED, "move" adds an unrelated file.
PROBE_PY = r"""
import json, os, subprocess, sys

sha = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
with open(os.environ["PROBE_LOG"], "a", encoding="utf-8") as f:
    f.write(sha + " " + " ".join(sys.argv[1:]) + "\n")
hook = os.environ.get("PROBE_HOOK", "")
mark = os.path.join(os.environ["FIX"], "hook.done")
msg = subprocess.run(["git", "log", "-1", "--format=%B"], capture_output=True, text=True).stdout
if hook and "Auto-Revert:" in msg and not os.path.exists(mark):
    open(mark, "w").close()
    seed = os.environ["SEED"]
    name = "FIXED" if hook == "fix" else "note_108.txt"
    with open(os.path.join(seed, name), "w", encoding="utf-8") as f:
        f.write("108\n")
    for args in (["add", "-A"], ["commit", "-q", "-m", hook + " lands (#108)"], ["push", "-q", "origin", "main"]):
        subprocess.run(["git", "-C", seed, *args], check=True, capture_output=True)
over = {}
if os.path.exists(os.environ["PROBE_OVERRIDE"]):
    with open(os.environ["PROBE_OVERRIDE"], encoding="utf-8") as f:
        over = json.load(f)
if sha in over:
    codes = over[sha] if isinstance(over[sha], list) else [over[sha]]
    count = os.path.join(os.environ["FIX"], "count_" + sha)
    n = int(open(count).read()) if os.path.exists(count) else 0
    with open(count, "w") as f:
        f.write(str(n + 1))
    sys.exit(codes[min(n, len(codes) - 1)])
if os.path.exists("FIXED"):
    sys.exit(0)
if os.path.exists("BROKEN"):
    sys.exit(1)
sys.path.insert(0, os.getcwd())
import calc
sys.exit(0 if calc.add(1, 2) == 3 else 1)
"""

# The poller. It prints poll.txt when it exists, as bisect_poll.sh prints
# its BISECT_RESULT line.
POLL_PY = r"""
import os, sys

fix = os.environ["FIX"]
open(os.path.join(fix, "poll.ran"), "a").close()
p = os.path.join(fix, "poll.txt")
if os.path.exists(p):
    with open(p, encoding="utf-8") as f:
        sys.stdout.write(f.read())
"""

GOOD = "def add(a, b):\n    return a + b\n"
BAD = "def add(a, b):\n    return a - b\n"


def git(cwd: Path, *args: str) -> str:
    res = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, **GIT_ENV},
        check=True,
    )
    return res.stdout.strip()


def load_module():
    spec = importlib.util.spec_from_file_location("auto_revert", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    # A dataclass reads its module from sys.modules.
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


class Box:
    """A bare origin, a seed clone that builds main, and the box clone."""

    def __init__(
        self, tmp: Path, variant: str = "plain", extra104: dict[str, str] | None = None
    ) -> None:
        self.tmp = tmp
        self.fix = tmp / "fix"
        self.fix.mkdir()
        self.temp = tmp / "t"
        self.temp.mkdir()
        self.origin = tmp / "origin.git"
        self.seed = tmp / "seed"
        self.repo = tmp / "box"
        self.sha: dict[int, str] = {}
        git(tmp, "init", "-q", "--bare", "-b", "main", str(self.origin))
        git(tmp, "clone", "-q", str(self.origin), str(self.seed))
        git(self.seed, "checkout", "-q", "-b", "main")
        self.commit(100, "base (#100)", {"calc.py": GOOD, ".gitignore": "__pycache__/\n"})
        for n in range(101, 107):
            # One note file per PR, so the revert of one PR applies cleanly.
            files = {f"note_{n}.txt": f"{n}\n"}
            subject = f"change number {n} (#{n})"
            if n == 104:
                files["calc.py"] = BAD
                files.update(extra104 or {})
                subject = "change number 104 (task 2985) (#104)"
            if n == 105 and variant == "conflict":
                files["calc.py"] = "def add(a, b):\n    return (a - b)\n"
            if n == 105 and variant == "broken":
                files["BROKEN"] = "yes\n"
            self.commit(n, subject, files)
        if variant == "merge":
            git(self.seed, "checkout", "-q", "-b", "side", self.sha[105])
            self.commit(0, "side work", {"side.txt": "s\n"})
            git(self.seed, "checkout", "-q", "main")
            git(self.seed, "merge", "-q", "--no-ff", "side", "-m", "merge side (#107)")
            self.sha[107] = git(self.seed, "rev-parse", "HEAD")
        if variant == "revert":
            message = (
                'Revert "change number 103 (#103)" (#107)\n\n'
                f"This reverts commit {self.sha[103]}.\n\nAuto-Revert: {self.sha[103]}\n"
            )
            self.commit(107, message, {"note_107.txt": "107\n"})
        git(self.seed, "push", "-q", "origin", "main")
        git(tmp, "clone", "-q", str(self.origin), str(self.repo))
        self.tip = git(self.repo, "rev-parse", "origin/main")

        bindir = tmp / "bin"
        bindir.mkdir()
        (bindir / "gh").write_text(FAKE_GH_SH, encoding="utf-8", newline="\n")
        (bindir / "gh").chmod(0o755)
        (bindir / "gh.py").write_text(FAKE_GH_PY, encoding="utf-8", newline="\n")
        (self.fix / "probe.py").write_text(PROBE_PY, encoding="utf-8", newline="\n")
        (self.fix / "poll.py").write_text(POLL_PY, encoding="utf-8", newline="\n")
        (self.fix / "gh_calls").write_text("", encoding="utf-8")
        self.pr_view(
            104,
            title="change number 104 (task 2985)",
            body=f"Task: {TASK_URL}\n\nVERIFY: PASS\n",
            headRefName="x-change-mt-1234abcd",
            url="https://github.com/o/r/pull/104",
        )

    def commit(self, n: int, message: str, files: dict[str, str]) -> None:
        for name, text in files.items():
            (self.seed / name).parent.mkdir(parents=True, exist_ok=True)
            (self.seed / name).write_text(text, encoding="utf-8", newline="\n")
        git(self.seed, "add", "-A")
        git(self.seed, "commit", "-q", "-m", message)
        self.sha[n] = git(self.seed, "rev-parse", "HEAD")

    def pr_view(self, n: int, **fields: str) -> None:
        (self.fix / f"pr_{n}.json").write_text(json.dumps(fields), encoding="utf-8")

    def result(self, culprit: int = 104, exit_code: int = 0, ids=None, line=None) -> None:
        sha = self.sha[culprit]
        data = {
            "run_id": 30,
            "good_sha": self.sha[101],
            "bad_sha": self.tip,
            "exit_code": exit_code,
            "result": line if line is not None else f"FIRST_BAD {sha} subject PR={culprit}",
            "test_ids": [TEST_ID] if ids is None else ids,
        }
        (self.fix / "poll.txt").write_text(
            "BISECT_RESULT " + json.dumps(data) + "\n", encoding="utf-8"
        )

    def override(self, **codes: int) -> None:
        """Map a commit (by its PR number, or `tip`) to a probe exit code."""
        data = {(self.tip if k == "tip" else self.sha[int(k[1:])]): v for k, v in codes.items()}
        (self.fix / "override.json").write_text(json.dumps(data), encoding="utf-8")

    @property
    def log(self) -> Path:
        return self.tmp / "auto-revert.log"

    @property
    def merge_log(self) -> Path:
        return self.tmp / "merge-bot.log"

    @property
    def queue(self) -> Path:
        return self.tmp / "queue.tsv"

    def run(self, mode: str | None, **extra: str) -> subprocess.CompletedProcess[str]:
        env = {
            **os.environ,
            **GIT_ENV,
            "PATH": f"{self.tmp / 'bin'}{os.pathsep}{os.environ['PATH']}",
            "FIX": str(self.fix),
            "ORIGIN": str(self.origin),
            "SEED": str(self.seed),
            "PY": PY,
            "PROBE": (self.fix / "probe.py").as_posix(),
            "PROBE_LOG": str(self.fix / "probe.log"),
            "PROBE_OVERRIDE": str(self.fix / "override.json"),
            "AUTO_REVERT_REPO_DIR": str(self.repo),
            "AUTO_REVERT_LOG": str(self.log),
            "AUTO_REVERT_REPORTS": str(self.tmp / "reports"),
            "AUTO_REVERT_STATE": str(self.tmp / "state"),
            "MERGE_LOG": str(self.merge_log),
            "AUTO_REVERT_POLL_CMD": f"{shlex.quote(PY)} {shlex.quote((self.fix / 'poll.py').as_posix())}",
            "BISECT_TEST_CMD": '"$PY" -B "$PROBE" "$@"',
            "GH_CMD": f"{shlex.quote(PY)} {shlex.quote((self.tmp / 'bin' / 'gh.py').as_posix())}",
            "HANDOVER_QUEUE": str(self.queue),
            "TMPDIR": str(self.temp),
            "TMP": str(self.temp),
            "TEMP": str(self.temp),
            **extra,
        }
        env.pop("AUTO_REVERT_MODE", None)
        if mode is not None:
            env["AUTO_REVERT_MODE"] = mode
        return subprocess.run(
            [sys.executable, str(SCRIPT), "run"],
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=300,
        )

    # What a pass left behind.
    def lines(self) -> list[str]:
        return self.log.read_text(encoding="utf-8").splitlines() if self.log.exists() else []

    def gh_calls(self) -> list[list[str]]:
        text = (self.fix / "gh_calls").read_text(encoding="utf-8")
        return [json.loads(line) for line in text.splitlines() if line]

    def gh_writes(self) -> list[list[str]]:
        reads = (("pr", "view"), ("pr", "list"))
        return [c for c in self.gh_calls() if tuple(c[:2]) not in reads]

    def probes(self) -> list[str]:
        p = self.fix / "probe.log"
        return (
            [ln.split()[0] for ln in p.read_text(encoding="utf-8").splitlines()]
            if p.exists()
            else []
        )

    def remote_heads(self) -> dict[str, str]:
        out = git(self.repo, "ls-remote", "--heads", "origin")
        return {ref: sha for sha, ref in (ln.split("\t") for ln in out.splitlines())}

    def report(self) -> str:
        return (self.tmp / "reports" / "run-30.md").read_text(encoding="utf-8")

    def assert_clean(self, main: str | None = None) -> None:
        """No worktree, no temp dir and no lock stay behind. The pass never moves main."""
        assert len(git(self.repo, "worktree", "list").splitlines()) == 1
        assert list(self.temp.glob("auto-revert*")) == []
        assert not (self.tmp / "state" / "lock.d").exists()
        assert self.remote_heads()["refs/heads/main"] == (main or self.tip)


@pytest.fixture
def box(tmp_path: Path) -> Box:
    return Box(tmp_path)


def person(box: Box) -> str:
    found = [ln for ln in box.lines() if " PERSON " in ln]
    assert len(found) == 1, box.lines()
    return found[0]


# ── The flag ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("mode", [None, "", "off", "yes"])
def test_off_does_nothing(box: Box, mode: str | None) -> None:
    box.result()
    got = box.run(mode)
    assert got.returncode == 0, got.stderr
    assert not (box.fix / "poll.ran").exists()
    assert box.gh_calls() == []
    assert not box.log.exists()
    assert not (box.tmp / "reports").exists()
    assert not (box.tmp / "state").exists()
    assert box.probes() == []
    if mode == "yes":
        assert "acts as off" in got.stderr


def test_the_code_default_is_off_and_the_unit_ships_dry() -> None:
    mod = load_module()
    assert mod.read_mode(None) == ("off", "")
    assert mod.read_mode("ON") == ("on", "")
    service = (UNITS / "metorite-auto-revert.service").read_text(encoding="utf-8")
    assert "Environment=AUTO_REVERT_MODE=dry" in service
    assert "auto_revert.py run" in service
    assert "OnUnitInactiveSec=" in (UNITS / "metorite-auto-revert.timer").read_text(
        encoding="utf-8"
    )


def test_no_new_red_run_writes_nothing(box: Box) -> None:
    got = box.run("dry")
    assert got.returncode == 0, got.stderr
    assert (box.fix / "poll.ran").exists()
    assert box.lines() == []
    assert not (box.tmp / "reports").exists()
    assert box.gh_calls() == []


# ── dry and on ─────────────────────────────────────────────────────────────


def test_dry_reports_the_revert_and_writes_nothing_else(box: Box) -> None:
    box.result()
    got = box.run("dry")
    assert got.returncode == 0, got.stderr
    lines = box.lines()
    assert len(lines) == 1 and " WOULD-REVERT " in lines[0], lines
    assert f"culprit={box.sha[104]}" in lines[0]
    report = box.report()
    assert "WOULD REVERT" in report
    assert f"Auto-Revert: {box.sha[104]}" in report
    assert "VERIFY: PASS" in report
    assert "task 2985" in report and TASK_URL in report and "mt-1234abcd" in report
    # GitHub reads only: the search for an open revert PR, and the culprit PR.
    # No PR, no comment, no push, no hand-over.
    assert box.gh_writes() == []
    assert ["pr", "view", "104", "--json", "title,body,headRefName,url"] in box.gh_calls()
    assert list(box.remote_heads()) == ["refs/heads/main"]
    assert not box.queue.exists()
    # Tip, culprit and parent 3 times each, then the revert tree once.
    tip, bad, parent = box.tip, box.sha[104], box.sha[103]
    assert box.probes()[:9] == [tip] * 3 + [bad] * 3 + [parent] * 3
    assert len(box.probes()) == 10
    box.assert_clean()


def test_on_opens_one_revert_pr_with_the_trailer_and_hands_it_over(box: Box) -> None:
    box.result()
    got = box.run("on")
    assert got.returncode == 0, got.stderr
    branch = f"refs/heads/auto-revert/{box.sha[104][:12]}"
    heads = box.remote_heads()
    assert set(heads) == {"refs/heads/main", branch}
    revert = heads[branch]
    git(box.repo, "fetch", "-q", "origin", branch)
    message = git(box.repo, "log", "-1", "--format=%B", revert)
    assert f"Auto-Revert: {box.sha[104]}" in message.splitlines()
    assert f"This reverts commit {box.sha[104]}." in message
    assert git(box.repo, "rev-parse", f"{revert}^") == box.tip
    assert git(box.repo, "show", f"{revert}:calc.py") == GOOD.strip()

    creates = [c for c in box.gh_calls() if c[:2] == ["pr", "create"]]
    assert len(creates) == 1
    assert (
        creates[0][creates[0].index("--title") + 1]
        == 'Revert "change number 104 (task 2985) (#104)"'
    )
    body = json.loads((box.fix / "pr_900.json").read_text(encoding="utf-8"))["body"]
    assert f"Auto-Revert: {box.sha[104]}" in body.splitlines()
    assert any(ln.startswith("VERIFY: PASS") for ln in body.splitlines())

    # The REAL lane_handover.sh wrote the line that the merge bot reads.
    queue = box.queue.read_text(encoding="utf-8").splitlines()
    assert queue[0].startswith("# PR")
    fields = queue[1].split("\t")
    # P1-a: task 0, so lane-merge never closes the culprit's task on DONE.
    # The culprit's task is in the note only.
    assert fields[0] == "900" and fields[1] == "0"
    assert fields[3] == "auto-revert" and fields[4] == revert and fields[5] == "STANDARD"
    assert "task 2985" in fields[6] and "never close" in fields[6]
    assert len(queue) == 2

    comment = (box.fix / "comment_104.txt").read_text(encoding="utf-8")
    assert "#900" in comment and TEST_ID in comment
    revert_lines = [ln for ln in box.lines() if " REVERT " in ln]
    assert len(revert_lines) == 1 and "revert_pr=900" in revert_lines[0]
    assert not [ln for ln in box.lines() if " PERSON " in ln]
    # It never merges, never dispatches a workflow, never reruns a run.
    assert {tuple(c[:2]) for c in box.gh_writes()} == {("pr", "create"), ("pr", "comment")}
    box.assert_clean()


# ── Fail closed ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("codes", "probes", "word"),
    [
        # Each tree runs up to 3 times. The first wrong answer stops the pass.
        ({"p104": 0}, 4, "on the culprit"),
        ({"tip": 0}, 1, "on the tip of main"),
        ({"p103": 1}, 7, "on the parent"),
        ({"p104": 3}, 4, "cannot judge"),
        # P3-a: a flaky culprit fails once, then passes once.
        ({"p104": [1, 0]}, 5, "on the culprit"),
        ({"p103": [0, 0, 1]}, 9, "on the parent"),
    ],
)
def test_the_flake_rerun_stops_the_revert(box: Box, codes, probes: int, word: str) -> None:
    box.result()
    box.override(**codes)
    got = box.run("on")
    assert got.returncode == 0, got.stderr
    assert word in person(box)
    assert len(box.probes()) == probes
    assert box.gh_writes() == []
    assert list(box.remote_heads()) == ["refs/heads/main"]
    assert not box.queue.exists()
    assert "NO REVERT" in box.report()
    box.assert_clean()


@pytest.mark.parametrize(
    ("exit_code", "ids", "line", "word"),
    [
        (4, None, "UNJUDGEABLE_RANGE a b", "no single commit"),
        (3, [], "no failed test id", "no single commit"),
        (5, None, "", "no single commit"),
        (0, [], None, "no failed test"),
    ],
)
def test_no_single_culprit_goes_to_a_person(box: Box, exit_code, ids, line, word) -> None:
    box.result(exit_code=exit_code, ids=ids, line=line)
    got = box.run("on")
    assert got.returncode == 0, got.stderr
    assert word in person(box)
    assert box.probes() == [] and box.gh_writes() == []
    box.assert_clean()


def test_a_merge_commit_is_not_reverted(tmp_path: Path) -> None:
    box = Box(tmp_path, variant="merge")
    box.result(culprit=107)
    got = box.run("on")
    assert got.returncode == 0, got.stderr
    assert "2 parents" in person(box)
    assert box.probes() == [] and box.gh_writes() == []
    box.assert_clean()


def test_a_revert_is_never_reverted(tmp_path: Path) -> None:
    box = Box(tmp_path, variant="revert")
    box.result(culprit=107)
    got = box.run("on")
    assert got.returncode == 0, got.stderr
    assert "A revert is never reverted" in person(box)
    assert box.probes() == [] and box.gh_writes() == []
    box.assert_clean()


def test_the_rate_brake_holds_after_three_reverts_in_an_hour(box: Box) -> None:
    mod = load_module()
    now = mod.stamp(mod.now_utc())
    box.log.write_text(
        "".join(f"{now} REVERT run={i} culprit={'a' * 40} revert_pr={i}\n" for i in (1, 2, 3)),
        encoding="utf-8",
    )
    box.merge_log.write_text("", encoding="utf-8")
    box.result()
    got = box.run("on")
    assert got.returncode == 0, got.stderr
    assert "rate brake" in person(box)
    assert box.probes() == [] and box.gh_writes() == []


def test_old_reverts_do_not_hold_the_brake(tmp_path: Path) -> None:
    mod = load_module()
    log = tmp_path / "a.log"
    now = mod.now_utc()
    old = mod.stamp(now - dt.timedelta(hours=2))
    fresh = mod.stamp(now - dt.timedelta(minutes=10))
    log.write_text(
        f"{old} REVERT culprit=x\n{old} REVERT culprit=y\n{fresh} REVERT culprit=z\n"
        f"{fresh} WOULD-REVERT culprit=w\nnot-a-time REVERT culprit=v\n",
        encoding="utf-8",
    )
    ledger = mod.Ledger(log)
    # A line with no time counts: the brake fails closed.
    assert ledger.recent("REVERT", 3600, now) == 2
    assert ledger.recent("WOULD-REVERT", 3600, now) == 1


def test_a_second_red_run_of_the_same_culprit_opens_no_second_pr(box: Box) -> None:
    box.log.write_text(
        f"2026-11-14T00:00:00Z REVERT run=29 culprit={box.sha[104]} revert_pr=900\n",
        encoding="utf-8",
    )
    box.result()
    got = box.run("on")
    assert got.returncode == 0, got.stderr
    assert " SKIP " in box.lines()[-1]
    assert box.probes() == [] and box.gh_writes() == []


def test_a_revert_that_does_not_apply_goes_to_a_person(tmp_path: Path) -> None:
    box = Box(tmp_path, variant="conflict")
    box.result()
    got = box.run("on")
    assert got.returncode == 0, got.stderr
    line = person(box)
    assert "does not apply cleanly" in line and "calc.py" in line
    assert box.gh_writes() == [] and list(box.remote_heads()) == ["refs/heads/main"]
    box.assert_clean()


def test_a_revert_that_does_not_fix_main_goes_to_a_person(tmp_path: Path) -> None:
    box = Box(tmp_path, variant="broken")
    box.result()
    got = box.run("on")
    assert got.returncode == 0, got.stderr
    assert "does not fix main" in person(box)
    assert len(box.probes()) == 10
    assert box.gh_writes() == [] and list(box.remote_heads()) == ["refs/heads/main"]
    box.assert_clean()


def test_a_failed_hand_over_goes_to_a_person(box: Box) -> None:
    box.result()
    fail = f"{shlex.quote(PY)} -c \"import sys; sys.stderr.write('no VERIFY line'); sys.exit(1)\""
    got = box.run("on", AUTO_REVERT_HANDOVER_CMD=fail)
    assert got.returncode == 0, got.stderr
    line = person(box)
    assert "revert_pr=900" in line and "hand-over to the merge bot failed" in line
    assert not box.queue.exists()
    box.assert_clean()


def test_a_revert_pr_that_the_merge_bot_sends_back_goes_to_a_person(box: Box) -> None:
    box.log.write_text(
        f"2026-11-14T00:00:00Z REVERT run=29 culprit={box.sha[104]} revert_pr=900\n"
        f"2026-11-14T00:00:00Z REVERT run=28 culprit={box.sha[103]} revert_pr=901\n",
        encoding="utf-8",
    )
    box.merge_log.write_text("BACK 900 red: Unit tests\nDONE 901 abc123\n", encoding="utf-8")
    got = box.run("on")  # no new red run
    assert got.returncode == 0, got.stderr
    assert "revert_pr=900" in person(box) and "red: Unit tests" in person(box)
    assert any(" REVERTED revert_pr=901 " in ln for ln in box.lines())
    # A second pass writes no second line for either PR.
    box.run("on")
    assert len(box.lines()) == 4


def test_a_revert_pr_handed_over_by_a_person_still_logs_reverted(box: Box) -> None:
    """A PERSON line of the pass (the hand-over failed) does not end the watch."""
    box.log.write_text(
        f"2026-11-14T00:00:00Z REVERT run=29 culprit={box.sha[104]} revert_pr=900\n"
        f"2026-11-14T00:00:01Z PERSON mode=on run=29 culprit={box.sha[104]} revert_pr=900 "
        "The revert stopped. The hand-over to the merge bot failed.\n",
        encoding="utf-8",
    )
    box.merge_log.write_text("BACK 900 red: Unit tests\nDONE 900 abc123\n", encoding="utf-8")
    got = box.run("on")
    assert got.returncode == 0, got.stderr
    new = box.lines()[2:]
    assert len(new) == 2, new
    assert " PERSON " in new[0] and "back=1" in new[0] and "red: Unit tests" in new[0]
    assert " REVERTED revert_pr=900 " in new[1]
    box.run("on")
    assert len(box.lines()) == 4


# ── The review round (P1 to P3) ───────────────────────────────────────────


@pytest.mark.parametrize("hook", ["fix", "move"])
def test_main_that_moves_during_the_pass_is_read_again(box: Box, hook: str) -> None:
    """P1-b: a commit lands on main while the probes run. The pass reads main again.
    A fix forward stops the revert. Another commit gets the revert on the new tip."""
    box.result()
    got = box.run("on", PROBE_HOOK=hook)
    assert got.returncode == 0, got.stderr
    new_tip = box.remote_heads()["refs/heads/main"]
    assert new_tip != box.tip
    report = box.report()
    assert box.tip in report and new_tip in report
    branch = f"refs/heads/auto-revert/{box.sha[104][:12]}"
    if hook == "fix":
        assert branch not in box.remote_heads()
        assert box.gh_writes() == [] and not box.queue.exists()
        assert any("pass on the new tip" in ln for ln in box.lines()), box.lines()
        assert not any(" REVERT " in ln for ln in box.lines())
    else:
        revert = box.remote_heads()[branch]
        git(box.repo, "fetch", "-q", "origin", branch)
        assert git(box.repo, "rev-parse", f"{revert}^") == new_tip
        assert box.queue.read_text(encoding="utf-8").splitlines()[1].split("\t")[4] == revert
    box.assert_clean(main=new_tip)


@pytest.mark.parametrize("mode", ["dry", "on"])
@pytest.mark.parametrize(
    ("files", "title"),
    [
        ({"infra/postgres/440_studio_x.sql": "CREATE TABLE x (id int);\n"}, None),
        ({"packages/acb_auth/acb_auth/roles.py": "ROLES = 1\n"}, None),
        ({}, "change number 104 (task 2985), HIGH tier"),
    ],
    ids=["migration", "auth-path", "high-title"],
)
def test_a_high_culprit_goes_to_a_person(tmp_path: Path, mode: str, files, title) -> None:
    """P1-c: a revert cannot undo a migration (R6), and a HIGH path needs a person."""
    box = Box(tmp_path, extra104=files)
    if title:
        box.pr_view(104, title=title, body="", headRefName="x", url="u")
    box.result()
    got = box.run(mode)
    assert got.returncode == 0, got.stderr
    assert "HIGH" in person(box)
    assert box.probes() == [] and box.gh_writes() == []
    assert list(box.remote_heads()) == ["refs/heads/main"]
    box.assert_clean()


def test_the_high_paths_equal_the_merge_bot_list() -> None:
    """P1-c: the mirror of HIGH_PATHS_RE in scripts/home_server/merge_bot.sh (CIV-28b)."""
    text = (ROOT / "scripts" / "home_server" / "merge_bot.sh").read_text(encoding="utf-8")
    parts = re.findall(r"^HIGH_PATHS_RE\+?='([^']*)'\s*$", text, re.MULTILINE)
    if not parts:
        pytest.skip("merge_bot.sh has no HIGH_PATHS_RE until CIV-28b (PR 2253) merges")
    assert "".join(parts) == load_module().HIGH_PATHS_RE


def test_the_high_paths_match_as_grep_matches(tmp_path: Path) -> None:
    """P1-c: Python reads the ERE of the merge bot the same way as grep -E."""
    mod = load_module()
    paths = [
        "infra/postgres/440_x.sql",
        "infra/customer_console/1.sql",
        "deploy/vps.sh",
        ".github/workflows/deploy.yml",
        ".github/workflows/pr-check.yml",
        "scripts/apply_migrations.sh",
        "scripts/gen_tenant_migration.py",
        "packages/acb_auth/acb_auth/x.py",
        "packages/acb_common/acb_common/db.py",
        "packages/acb_common/acb_common/dbx.py",
        "packages/acb_memory/acb_memory/tenant_guard.py",
        "workbench/control_plane/src/app/settings/billing/page.tsx",
        "apps/x/auth_tokens.py",
        "apps/x/tenant_scope.py",
        "apps/x/my_rls.sql",
        "apps/x/calc.py",
        "scripts/home_server/auto_revert.py",
    ]
    listing = tmp_path / "paths.txt"
    listing.write_text("\n".join(paths) + "\n", encoding="utf-8", newline="\n")
    grep = subprocess.run(
        [BASH, "-c", 'grep -E "$1" "$2" || true', "x", mod.HIGH_PATHS_RE, listing.as_posix()],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    by_grep = set(grep.stdout.split())
    by_python = {p for p in paths if mod.high_paths([p])}
    assert by_python == by_grep
    assert "apps/x/calc.py" not in by_python and "infra/postgres/440_x.sql" in by_python


def test_a_red_run_after_the_revert_landed_goes_to_a_person(box: Box) -> None:
    """P2-a: main holds the revert of the culprit and is still red. That is a PERSON
    line, not a SKIP, even when the log has the revert."""
    box.commit(
        108,
        f'Revert "change number 104 (task 2985) (#104)" (#108)\n\n'
        f"This reverts commit {box.sha[104]}.\n\nAuto-Revert: {box.sha[104]}\n",
        {"note_108.txt": "108\n"},
    )
    git(box.seed, "push", "-q", "origin", "main")
    box.log.write_text(
        f"2026-11-14T00:00:00Z REVERT run=29 culprit={box.sha[104]} revert_pr=900\n"
        f"2026-11-14T00:00:01Z REVERTED revert_pr=900 culprit={box.sha[104]} merge=abc\n",
        encoding="utf-8",
    )
    box.result()
    got = box.run("on")
    assert got.returncode == 0, got.stderr
    assert "already holds the revert" in person(box)
    assert not any(" SKIP " in ln for ln in box.lines())
    assert box.probes() == [] and box.gh_writes() == []


@pytest.mark.parametrize("gh_fails", [False, True])
def test_an_open_revert_pr_of_the_culprit_stops_a_second_one(box: Box, gh_fails: bool) -> None:
    """P2-c: lane-merge or a person opened a revert of the culprit. The pass opens no
    second one. When gh cannot search, the pass stops too (fail closed)."""
    (box.fix / "pr_list.json").write_text(
        json.dumps(
            [
                {"number": 776, "title": "unrelated", "body": "nothing here"},
                {
                    "number": 777,
                    "title": 'Revert "change number 104 (task 2985) (#104)"',
                    "body": f"This reverts commit {box.sha[104]}.",
                },
            ]
        ),
        encoding="utf-8",
    )
    box.result()
    got = box.run("on", **({"FAKE_GH_FAIL": "pr list"} if gh_fails else {}))
    assert got.returncode == 0, got.stderr
    if gh_fails:
        assert "cannot search" in person(box)
    else:
        assert " SKIP " in box.lines()[-1] and "777" in box.lines()[-1]
    assert box.probes() == [] and box.gh_writes() == []
    assert list(box.remote_heads()) == ["refs/heads/main"]


def test_lane_merge_defers_to_the_auto_revert() -> None:
    """P1-a and P2-c: the lane-merge role reopens the culprit's task and never closes
    it, and its own revert of a red main waits for the pass when the mode is on."""
    text = (ROOT / "scripts" / "home_server" / "lanes" / "lane-merge.md").read_text(
        encoding="utf-8"
    )
    lowered = " ".join(text.split()).lower()
    assert "lane `auto-revert`" in lowered
    assert "never close" in lowered and "open the culprit's task again" in lowered
    assert "auto_revert_mode" in lowered and "auto-revert.log" in lowered


def test_a_stop_signal_still_writes_a_person_line_and_a_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P3-b: systemd stops a pass with SIGTERM, and the poller already took the run.
    The pass writes its PERSON line and its report, then ends."""
    mod = load_module()
    cfg = mod.Config(
        mode="dry",
        repo=tmp_path,
        log=tmp_path / "a.log",
        reports=tmp_path / "reports",
        state=tmp_path / "state",
        merge_log=tmp_path / "m.log",
        poll_cmd=[],
        handover_cmd=[],
        gh=[],
        test_cmd="",
        timeout_s=1,
    )

    def stopped(*_args, **_kwargs):
        raise SystemExit(143)

    monkeypatch.setattr(mod, "decide", stopped)
    with pytest.raises(SystemExit):
        mod.handle(cfg, mod.Ledger(cfg.log), {"run_id": 31, "exit_code": 0, "result": ""})
    line = cfg.log.read_text(encoding="utf-8")
    assert " PERSON " in line and "SystemExit" in line
    assert (tmp_path / "reports" / "run-31.md").exists()


# ── A reverted patch does not land again unchanged (premerge.sh) ──────────


@pytest.fixture
def landed(tmp_path: Path):
    """main: base, X (PR 104 breaks calc), then the auto-revert R of X."""
    origin = tmp_path / "origin.git"
    work = tmp_path / "work"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    git(tmp_path, "clone", "-q", str(origin), str(work))
    git(work, "checkout", "-q", "-b", "main")

    def commit(message: str, files: dict[str, str]) -> str:
        for name, text in files.items():
            (work / name).write_text(text, encoding="utf-8", newline="\n")
        git(work, "add", "-A")
        git(work, "commit", "-q", "-m", message)
        return git(work, "rev-parse", "HEAD")

    base = commit("base", {"calc.py": GOOD, "notes.md": "a\n"})
    x = commit("change (#104)", {"calc.py": BAD})
    revert = commit(
        f'Revert "change (#104)"\n\nThis reverts commit {x}.\n\nAuto-Revert: {x}\n',
        {"calc.py": GOOD},
    )
    git(work, "push", "-q", "origin", "main")
    return work, base, x, revert, commit


def premerge(work: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [BASH, PREMERGE.as_posix()],
        cwd=work,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, **GIT_ENV},
        timeout=120,
    )


def test_premerge_refuses_a_reverted_patch_that_lands_again_unchanged(landed) -> None:
    work, base, x, _, commit = landed
    git(work, "checkout", "-q", "-b", "again", base)
    commit("change, the same patch", {"calc.py": BAD})
    got = premerge(work)
    assert got.returncode == 1, got.stdout + got.stderr
    assert "again, unchanged" in got.stderr and x[:12] in got.stderr


def test_premerge_refuses_a_revert_of_the_auto_revert(landed) -> None:
    work, _, x, revert, _ = landed
    git(work, "checkout", "-q", "-b", "undo")
    git(work, "revert", "--no-edit", revert)
    got = premerge(work)
    assert got.returncode == 1, got.stdout + got.stderr
    assert "again, unchanged" in got.stderr and x[:12] in got.stderr


def test_premerge_refuses_a_re_land_after_a_nearby_edit_on_main(landed) -> None:
    """P2-b: main edits a line near the reverted lines. The re-land then has other
    context lines. The patch with no context (-U0) is still the same patch."""
    work, _, _, _, commit = landed
    five = "# calc\ndef mul(a, b):\n    return a * b\n\n# end\n"
    commit("add mul", {"calc2.py": five})
    x2 = commit("break mul (#105)", {"calc2.py": five.replace("a * b", "a / b")})
    commit(
        f'Revert "break mul (#105)"\n\nThis reverts commit {x2}.\n\nAuto-Revert: {x2}\n',
        {"calc2.py": five},
    )
    commit("a nearby edit", {"calc2.py": five.replace("# calc", "# calc, with mul")})
    git(work, "push", "-q", "origin", "main")
    git(work, "checkout", "-q", "-b", "again2")
    git(work, "cherry-pick", x2)
    got = premerge(work)
    assert got.returncode == 1, got.stdout + got.stderr
    assert "again, unchanged" in got.stderr and x2[:12] in got.stderr


def test_premerge_lets_a_changed_patch_through(landed) -> None:
    work, base, _, _, commit = landed
    git(work, "checkout", "-q", "-b", "fixed", base)
    commit("change, with the fix", {"calc.py": BAD, "notes.md": "a\nthe fix\n"})
    got = premerge(work)
    assert got.returncode == 0, got.stdout + got.stderr
    assert "PASS" in got.stdout
