"""CIV-28 (D218): scripts/home_server/merge_bot.sh.

A temp git repo with a bare origin, a shimmed `gh` (answers from fixture files
and records each call) and a shimmed premerge serve every case.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "home_server" / "merge_bot.sh"
BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None, reason="needs bash")

REQUIRED = [
    "Lint",
    "Unit tests",
    "Backup/restore rehearsal",
    "Migration ladder replay",
    "Frontend (typecheck + unit tests)",
    "Operator console (typecheck + unit tests)",
    "Secret scan",
]

FAKE_GH = """#!/usr/bin/env bash
echo "$*" >> "$FIX/calls"
if [ "$1" = api ]; then cat "$FIX/required"; exit 0; fi
case "$1 $2" in
  "pr view") cat "$FIX/view_$3" ;;
  "pr checks") cat "$FIX/checks_$3" ;;
  "pr merge") printf 'MERGED\\tfalse\\tabc123\\n' > "$FIX/view_$3"
    [ -n "${FAKE_MERGE_FAIL:-}" ] && { echo 'unexpected EOF' >&2; exit 1; } ;;
esac
exit 0
"""


def git(cwd, *a):
    return subprocess.run(
        ["git", *a], cwd=cwd, check=True, capture_output=True, text=True, encoding="utf-8"
    ).stdout.strip()


class Env:
    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.fix = tmp / "fix"
        self.fix.mkdir()
        self.origin = tmp / "origin.git"
        self.seed = tmp / "seed"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(self.origin)], check=True)
        subprocess.run(["git", "clone", "-q", str(self.origin), str(self.seed)], check=True, capture_output=True)
        for k, v in (("user.email", "t@x.io"), ("user.name", "t")):
            git(self.seed, "config", k, v)
        self.commit("notes.md", "one\n", "base")
        git(self.seed, "push", "-q", "origin", "main")
        subprocess.run(
            ["git", "clone", "-q", str(self.origin), str(tmp / "work")], check=True, capture_output=True
        )
        for k, v in (("user.email", "t@x.io"), ("user.name", "t")):
            git(tmp / "work", "config", k, v)
        gh = tmp / "bin" / "gh"
        gh.parent.mkdir()
        gh.write_text(FAKE_GH, encoding="utf-8", newline="\n")
        gh.chmod(0o755)
        (self.fix / "required").write_text("\n".join(REQUIRED) + "\n", encoding="utf-8")
        (self.fix / "calls").write_text("", encoding="utf-8")
        self.premerge("0")

    def commit(self, path, text, msg):
        (self.seed / path).write_text(text, encoding="utf-8")
        git(self.seed, "add", "-A")
        git(self.seed, "commit", "-qm", msg)

    def pr(self, n, path="feature.txt", text="x\n", state="OPEN", draft="false", checks=None, queue=True):
        """A PR branch cut from the current main, published as refs/pull/<n>/head."""
        git(self.seed, "checkout", "-q", "-B", f"pr{n}", "main")
        self.commit(path, text, f"pr {n}")
        git(self.seed, "push", "-q", "origin", f"pr{n}:refs/pull/{n}/head")
        git(self.seed, "checkout", "-q", "main")
        (self.fix / f"view_{n}").write_text(f"{state}\t{draft}\t\n", encoding="utf-8")
        self.checks(n, checks)
        if queue:
            with (self.tmp / "queue.tsv").open("a", encoding="utf-8", newline="\n") as f:
                f.write(f"{n}\t99\t/x\tlane-x\tnote\n")

    def checks(self, n, rows=None, **override):
        """rows: dict name -> bucket. The default is every required check passed."""
        rows = dict(rows) if rows is not None else {name: "pass" for name in REQUIRED}
        rows.update(override)
        text = "".join(f"{k}\t{v}\thttps://github.com/o/r/actions/runs/{700 + i}/job/1\n" for i, (k, v) in enumerate(rows.items()))
        (self.fix / f"checks_{n}").write_text(text, encoding="utf-8")

    def premerge(self, code, move_main_once=False):
        s = self.fix / "premerge.sh"
        s.write_text(
            "#!/usr/bin/env bash\n"
            f'echo run >> "{self.fix.as_posix()}/premerge.count"\n'
            f'n=$(wc -l < "{self.fix.as_posix()}/premerge.count")\n'
            + (
                f'if [ "$n" = 1 ]; then cd "{self.seed.as_posix()}" && git checkout -q main && '
                'echo more >> notes.md && git commit -qam moved && git push -q origin main; fi\n'
                if move_main_once
                else ""
            )
            + ('echo "fence failed: boom" >&2\n' if code != "0" else "")
            + f"exit {code}\n",
            encoding="utf-8",
            newline="\n",
        )

    def run(self, **extra):
        env = {
            **os.environ,
            "PATH": f"{self.tmp / 'bin'}{os.pathsep}{os.environ['PATH']}",
            "FIX": str(self.fix.as_posix()),
            "MERGE_QUEUE": str((self.tmp / "queue.tsv").as_posix()),
            "MERGE_LOG": str((self.tmp / "queue.log").as_posix()),
            "MERGE_BOT_STATE": str((self.tmp / "state").as_posix()),
            "MERGE_BOT_WORKDIR": str((self.tmp / "work").as_posix()),
            "PREMERGE_CMD": f"bash {(self.fix / 'premerge.sh').as_posix()}",
            **extra,
        }
        r = subprocess.run([BASH, str(SCRIPT)], env=env, capture_output=True, text=True, encoding="utf-8")
        assert r.returncode == 0, r.stderr
        return r

    @property
    def log(self):
        p = self.tmp / "queue.log"
        return p.read_text(encoding="utf-8") if p.exists() else ""

    @property
    def calls(self):
        return (self.fix / "calls").read_text(encoding="utf-8")

    @property
    def premerge_runs(self):
        p = self.fix / "premerge.count"
        return len(p.read_text(encoding="utf-8").splitlines()) if p.exists() else 0


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path)


def test_a_pending_required_check_waits(env):
    env.pr(5, checks={n: "pass" for n in REQUIRED} | {"Unit tests": "pending"})
    env.run()
    assert env.log == "" and "pr merge" not in env.calls and env.premerge_runs == 0


def test_a_missing_required_check_is_not_ready(env):
    env.pr(5, checks={n: "pass" for n in REQUIRED if n != "Unit tests"})
    env.run()
    assert env.log == "" and "pr merge" not in env.calls


def test_a_red_required_check_goes_back(env):
    env.pr(5, checks={n: "pass" for n in REQUIRED} | {"Lint": "fail", "Secret scan": "fail"})
    env.run()
    assert env.log == "BACK 5 red: Lint, Secret scan\n" and "pr merge" not in env.calls


def test_a_cancelled_check_reruns_once_then_waits(env):
    env.pr(5, checks={n: "pass" for n in REQUIRED} | {"Unit tests": "cancel"})
    env.run()
    env.run()
    assert env.log == "" and "pr merge" not in env.calls
    reruns = [c for c in env.calls.splitlines() if c.startswith("run rerun")]
    assert len(reruns) == 1 and reruns[0].endswith("--failed")


def test_a_green_clean_pr_merges_once_and_logs_the_sha(env):
    env.pr(5, checks={n: "skipping" if n == "Backup/restore rehearsal" else "pass" for n in REQUIRED})
    env.run()
    merges = [c for c in env.calls.splitlines() if c.startswith("pr merge")]
    assert merges == ["pr merge 5 --squash"]
    assert env.log == "DONE 5 abc123\n" and env.premerge_runs == 1


def test_the_browser_suite_never_blocks(env):
    env.pr(5, checks={n: "pass" for n in REQUIRED} | {"Browser suite": "fail"})
    env.run()
    assert env.log == "DONE 5 abc123\n"


def test_a_draft_is_made_ready_before_the_merge(env):
    env.pr(5, draft="true")
    env.run()
    lines = env.calls.splitlines()
    assert "pr ready 5" in lines and lines.index("pr ready 5") < lines.index("pr merge 5 --squash")


def test_a_conflict_goes_back_and_names_the_file(env):
    env.pr(5, path="notes.md", text="mine\n")
    git(env.seed, "checkout", "-q", "main")
    env.commit("notes.md", "theirs\n", "main edit")
    git(env.seed, "push", "-q", "origin", "main")
    env.run()
    assert env.log.startswith("BACK 5 conflict: ") and "notes.md" in env.log
    assert "pr merge" not in env.calls and env.premerge_runs == 0


def test_a_behind_branch_with_a_clean_trial_merge_merges(env):
    env.pr(5)
    env.commit("other.txt", "y\n", "main moved")
    git(env.seed, "push", "-q", "origin", "main")
    env.run()
    assert env.log == "DONE 5 abc123\n"


def test_a_premerge_failure_goes_back_with_its_output(env):
    env.pr(5)
    env.premerge("1")
    env.run()
    assert env.log.startswith("BACK 5 premerge: ") and "boom" in env.log
    assert "pr merge" not in env.calls and env.log.count("\n") == 1


def test_main_moving_during_premerge_runs_premerge_again(env):
    env.pr(5)
    env.premerge("0", move_main_once=True)
    env.run()
    assert env.premerge_runs == 2 and env.log == "DONE 5 abc123\n"


def test_an_already_merged_pr_is_done_and_a_closed_pr_goes_back(env):
    env.pr(5, state="MERGED")
    (env.fix / "view_5").write_text("MERGED\tfalse\tdeadbeef\n", encoding="utf-8")
    env.pr(6, state="CLOSED")
    env.run()
    assert env.log == "DONE 5 deadbeef\nBACK 6 closed\n" and "pr merge" not in env.calls


def test_dry_run_calls_no_write_command_and_logs_nothing(env):
    env.pr(5)
    env.pr(6, checks={n: "pass" for n in REQUIRED} | {"Unit tests": "cancel"}, draft="true")
    r = env.run(MERGE_BOT_DRY_RUN="1")
    for bad in ("pr merge", "pr ready", "run rerun"):
        assert bad not in env.calls
    assert env.log == "" and "DRY RUN" in r.stderr


def test_a_pr_in_the_log_is_skipped(env):
    env.pr(5)
    (env.tmp / "queue.log").write_text("BACK 5 red: Lint\n", encoding="utf-8")
    env.run()
    assert env.calls == "" and env.log == "BACK 5 red: Lint\n"


def test_no_call_ever_uses_admin_or_auto(env):
    env.pr(5)
    env.pr(6, checks={n: "pass" for n in REQUIRED} | {"Unit tests": "cancel"})
    env.pr(7, draft="true")
    env.run()
    assert "--admin" not in env.calls and "--auto" not in env.calls and "deploy" not in env.calls


def test_a_gh_error_that_hid_a_merge_still_logs_done(env):
    # The merge landed (state MERGED on re-read) even though gh exited 1.
    env.pr(5)
    env.run(FAKE_MERGE_FAIL="1")
    assert env.log == "DONE 5 abc123\n"
