"""CIV-28 (D218): scripts/home_server/merge_bot.sh.

A temp git repo with a bare origin, a shimmed `gh` and a shimmed premerge serve
every case. One case runs the REAL scripts/premerge.sh.

The `gh` shim is honest about shape. It serves the JSON that real gh prints,
with only the fields that the call asks for in `--json`. It applies `--jq` with
a real `jq` when one exists, and it fails the call when none does, so a script
that leans on `--jq` cannot pass here by luck. `gh pr checks` exits 8 when a
check is pending and 1 when one failed, as real gh does.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "home_server" / "merge_bot.sh"
PREMERGE = ROOT / "scripts" / "premerge.sh"
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
VERIFY = "Task: x\n\nVERIFY: PASS (verifier run 12)\nVisual review: no UI change\n"

FAKE_GH_SH = """#!/usr/bin/env bash
exec python3 "$(dirname "$0")/gh.py" "$@"
"""

FAKE_GH_PY = r"""
import json, os, shutil, subprocess, sys

fix = os.environ["FIX"]
args = sys.argv[1:]
with open(os.path.join(fix, "calls"), "a", encoding="utf-8", newline="\n") as f:
    f.write(" ".join(args) + "\n")


def opt(name):
    return args[args.index(name) + 1] if name in args else None


def load(name):
    p = os.path.join(fix, name)
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def emit(data):
    fields = opt("--json")
    if fields:
        keep = fields.split(",")
        if isinstance(data, list):
            data = [{k: r[k] for k in keep if k in r} for r in data]
        else:
            data = {k: data[k] for k in keep if k in data}
    text = json.dumps(data)
    jq = opt("--jq") or opt("-q")
    if jq:
        exe = shutil.which("jq")
        if not exe:
            sys.stderr.write("gh shim: --jq needs a real jq here, and none is on PATH\n")
            sys.exit(97)
        text = subprocess.run([exe, "-r", jq], input=text, capture_output=True, text=True).stdout
        sys.stdout.buffer.write(text.encode("utf-8"))
        return
    sys.stdout.buffer.write((text + "\n").encode("utf-8"))


if os.environ.get("FAKE_GH_DOWN"):
    sys.stderr.write("error connecting to api.github.com\n")
    sys.exit(1)

if args[0] == "api":
    emit(load("required.json"))
    sys.exit(0)
verb = " ".join(args[:2])
n = args[2] if len(args) > 2 else ""
if verb == "pr view":
    if os.environ.get("FAKE_VIEW_DOWN_AFTER_MERGE") and os.path.exists(os.path.join(fix, "merged")):
        sys.stderr.write("error connecting to api.github.com\n")
        sys.exit(1)
    emit(load(f"view_{n}.json"))
elif verb == "pr checks":
    rows = load(f"checks_{n}.json")
    if rows is None:
        sys.stderr.write("no checks reported on the 'x' branch\n")
        sys.exit(1)
    emit(rows)
    buckets = {r["bucket"] for r in rows}
    sys.exit(1 if "fail" in buckets else 8 if "pending" in buckets else 0)
elif verb == "pr merge":
    view = load(f"view_{n}.json")
    want = opt("--match-head-commit")
    if want and want != view["headRefOid"]:
        sys.stderr.write("GraphQL: Head branch was modified. Review and try the merge again.\n")
        sys.exit(1)
    view["state"] = "MERGED"
    view["mergeCommit"] = {"oid": "abc123"}
    with open(os.path.join(fix, f"view_{n}.json"), "w", encoding="utf-8") as f:
        json.dump(view, f)
    open(os.path.join(fix, "merged"), "w").close()
    if os.environ.get("FAKE_MERGE_FAIL"):
        sys.stderr.write("unexpected EOF\n")
        sys.exit(1)
sys.exit(0)
"""


def git(cwd, *a):
    return subprocess.run(
        ["git", *a], cwd=cwd, check=True, capture_output=True, text=True, encoding="utf-8"
    ).stdout.strip()


def wjson(path: Path, data):
    path.write_text(json.dumps(data), encoding="utf-8", newline="\n")


class Env:
    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.fix = tmp / "fix"
        self.fix.mkdir()
        self.origin = tmp / "origin.git"
        self.seed = tmp / "seed"
        self.heads = {}
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(self.origin)], check=True)
        subprocess.run(
            ["git", "clone", "-q", str(self.origin), str(self.seed)],
            check=True,
            capture_output=True,
        )
        for k, v in (("user.email", "t@x.io"), ("user.name", "t")):
            git(self.seed, "config", k, v)
        self.commit("notes.md", "one\n", "base")
        git(self.seed, "push", "-q", "origin", "main")
        subprocess.run(
            ["git", "clone", "-q", str(self.origin), str(tmp / "work")],
            check=True,
            capture_output=True,
        )
        for k, v in (("user.email", "t@x.io"), ("user.name", "t")):
            git(tmp / "work", "config", k, v)
        bindir = tmp / "bin"
        bindir.mkdir()
        (bindir / "gh").write_text(FAKE_GH_SH, encoding="utf-8", newline="\n")
        (bindir / "gh").chmod(0o755)
        (bindir / "gh.py").write_text(FAKE_GH_PY, encoding="utf-8", newline="\n")
        wjson(self.fix / "required.json", {"strict": False, "contexts": REQUIRED, "checks": []})
        (self.fix / "calls").write_text("", encoding="utf-8")
        self.premerge("0")

    def commit(self, path, text, msg):
        (self.seed / path).write_text(text, encoding="utf-8")
        git(self.seed, "add", "-A")
        git(self.seed, "commit", "-qm", msg)

    def pr(
        self,
        n,
        path="feature.txt",
        text="x\n",
        state="OPEN",
        draft=False,
        checks=None,
        queue=True,
        tier="STANDARD",
        body=VERIFY,
    ):
        """A PR branch cut from the current main, published as refs/pull/<n>/head."""
        git(self.seed, "checkout", "-q", "-B", f"pr{n}", "main")
        self.commit(path, text, f"pr {n}")
        git(self.seed, "push", "-q", "origin", f"pr{n}:refs/pull/{n}/head")
        sha = git(self.seed, "rev-parse", "HEAD")
        git(self.seed, "checkout", "-q", "main")
        self.heads[n] = sha
        self.view(n, state=state, isDraft=draft, mergeCommit=None, headRefOid=sha, body=body)
        self.checks(n, checks)
        if queue:
            self.queue(n, sha=sha, tier=tier)
        return sha

    def push_pr(self, n, path="later.txt"):
        """A new commit on the PR branch, so its head moves."""
        git(self.seed, "checkout", "-q", f"pr{n}")
        self.commit(path, "later\n", f"pr {n} later")
        git(self.seed, "push", "-q", "-f", "origin", f"pr{n}:refs/pull/{n}/head")
        sha = git(self.seed, "rev-parse", "HEAD")
        git(self.seed, "checkout", "-q", "main")
        self.view(n, headRefOid=sha)
        return sha

    def queue(self, n, sha=None, tier="STANDARD", note="note", path=None):
        path = path or self.tmp / "queue.tsv"
        new = not path.exists()
        with path.open("a", encoding="utf-8", newline="\n") as f:
            if new:
                f.write("# PR\ttask\tworktree\tlane\thead_sha\ttier\tnote\n")
            f.write(f"{n}\t99\t/x\tlane-x\t{sha or self.heads[n]}\t{tier}\t{note}\n")

    def view(self, n, **fields):
        p = self.fix / f"view_{n}.json"
        data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        data.update(fields)
        data.setdefault("number", int(n))
        wjson(p, data)

    def checks(self, n, rows=None, **override):
        """rows: dict name -> bucket, or a list of (name, bucket, startedAt). Default: all pass."""
        if isinstance(rows, list):
            items = rows
        else:
            d = dict(rows) if rows is not None else {name: "pass" for name in REQUIRED}
            d.update(override)
            items = [(k, v, "2026-11-13T10:00:00Z") for k, v in d.items()]
        out = []
        for i, (name, bucket, started) in enumerate(items):
            out.append(
                {
                    "name": name,
                    "bucket": bucket,
                    "state": {
                        "pass": "SUCCESS",
                        "fail": "FAILURE",
                        "pending": "PENDING",
                        "cancel": "CANCELLED",
                        "skipping": "SKIPPED",
                    }[bucket],
                    "link": f"https://github.com/o/r/actions/runs/{700 + i}/job/1",
                    "startedAt": started,
                    "completedAt": "0001-01-01T00:00:00Z" if bucket == "pending" else started,
                    "workflow": "pr-check",
                }
            )
        wjson(self.fix / f"checks_{n}.json", out)

    def premerge(self, code, move_main_once=False, move_head_of=None):
        s = self.fix / "premerge.sh"
        seed = self.seed.as_posix()
        extra = ""
        if move_main_once:
            extra += (
                f'if [ "$n" = 1 ]; then cd "{seed}" && git checkout -q main && '
                "echo more >> notes.md && git commit -qam moved && git push -q origin main; fi\n"
            )
        if move_head_of is not None:
            extra += (
                f'cd "{seed}" && git checkout -q pr{move_head_of} && echo l > later.txt && '
                f"git add -A && git commit -qm later && git push -q -f origin pr{move_head_of}:refs/pull/{move_head_of}/head && "
                f"python3 -c \"import json,sys; p=sys.argv[1]; d=json.load(open(p)); d['headRefOid']=sys.argv[2]; json.dump(d, open(p, 'w'))\" "
                f'"{self.fix.as_posix()}/view_{move_head_of}.json" "$(git rev-parse HEAD)" && git checkout -q main\n'
            )
        s.write_text(
            "#!/usr/bin/env bash\n"
            f'echo run >> "{self.fix.as_posix()}/premerge.count"\n'
            f'n=$(wc -l < "{self.fix.as_posix()}/premerge.count")\n'
            f'git rev-parse --abbrev-ref HEAD >> "{self.fix.as_posix()}/premerge.branch"\n'
            + extra
            + ('echo "fence failed: boom" >&2\n' if code != "0" else "")
            + f"exit {code}\n",
            encoding="utf-8",
            newline="\n",
        )

    def run(self, home=None, **extra):
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
        if home is not None:
            env["HOME"] = home.as_posix()
            env.pop("MERGE_QUEUE")
            env.pop("MERGE_LOG")
        r = subprocess.run(
            [BASH, str(SCRIPT)], env=env, capture_output=True, text=True, encoding="utf-8"
        )
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
    def merges(self):
        return [c for c in self.calls.splitlines() if c.startswith("pr merge")]

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
    assert len(reruns) == 1 and reruns[0] == "run rerun 701 --failed"


def test_a_green_clean_pr_merges_once_at_its_pinned_head(env):
    sha = env.pr(
        5, checks={n: "skipping" if n == "Backup/restore rehearsal" else "pass" for n in REQUIRED}
    )
    env.run()
    assert env.merges == [f"pr merge 5 --squash --match-head-commit {sha}"]
    assert env.log == "DONE 5 abc123\n" and env.premerge_runs == 1


def test_the_browser_suite_never_blocks(env):
    env.pr(5, checks={n: "pass" for n in REQUIRED} | {"Browser suite": "fail"})
    env.run()
    assert env.log == "DONE 5 abc123\n"


def test_a_draft_is_made_ready_before_the_merge(env):
    env.pr(5, draft=True)
    env.run()
    lines = env.calls.splitlines()
    merge = next(i for i, c in enumerate(lines) if c.startswith("pr merge 5"))
    assert "pr ready 5" in lines and lines.index("pr ready 5") < merge


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
    env.view(5, mergeCommit={"oid": "deadbeef"})
    env.pr(6, state="CLOSED")
    env.run()
    assert env.log == "DONE 5 deadbeef\nBACK 6 closed\n" and "pr merge" not in env.calls


def test_dry_run_calls_no_write_command_and_logs_nothing(env):
    env.pr(5)
    env.pr(6, checks={n: "pass" for n in REQUIRED} | {"Unit tests": "cancel"}, draft=True)
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
    env.pr(7, draft=True)
    env.run()
    assert "--admin" not in env.calls and "--auto" not in env.calls and "deploy" not in env.calls


def test_a_gh_error_that_hid_a_merge_still_logs_done(env):
    # The merge landed (state MERGED on re-read) even though gh exited 1.
    env.pr(5)
    env.run(FAKE_MERGE_FAIL="1")
    assert env.log == "DONE 5 abc123\n"


def test_a_second_hand_over_after_a_back_is_handled_again(env):
    env.pr(5, checks={n: "pass" for n in REQUIRED} | {"Lint": "fail"})
    env.run()
    assert env.log == "BACK 5 red: Lint\n"
    env.checks(5)
    env.queue(5, note="again")
    env.run()
    assert env.log == "BACK 5 red: Lint\nDONE 5 abc123\n"


def test_a_back_with_no_new_hand_over_is_skipped(env):
    env.pr(5, checks={n: "pass" for n in REQUIRED} | {"Lint": "fail"})
    env.run()
    env.checks(5)
    env.run()
    assert env.log == "BACK 5 red: Lint\n" and "pr merge" not in env.calls


def test_a_stray_hand_over_after_done_is_skipped(env):
    env.pr(5)
    env.run()
    assert env.log == "DONE 5 abc123\n"
    env.queue(5, note="again")
    env.run()
    assert env.log == "DONE 5 abc123\n" and len(env.merges) == 1


# ── Repair round 1 (PR 2183 review) ─────────────────────────────────────────


def test_p0_1_a_head_that_moved_after_the_hand_over_goes_back(env):
    env.pr(5)
    env.push_pr(5)
    env.run()
    assert env.log == "BACK 5 head moved after the hand-over: hand over again\n"
    assert env.merges == [] and env.premerge_runs == 0


def test_p0_1_a_head_that_moves_during_premerge_is_not_merged(env):
    sha = env.pr(5)
    env.premerge("0", move_head_of=5)
    env.run()
    # The merge call pins the SHA that was gated, so GitHub refuses the newer head.
    assert env.merges == [f"pr merge 5 --squash --match-head-commit {sha}"]
    assert env.log.startswith("BACK 5 merge failed: ") and "Head branch was modified" in env.log


def test_p0_1_the_gate_checks_out_the_pinned_sha(env):
    sha = env.pr(5)
    env.run()
    assert git(env.tmp / "work", "rev-parse", "refs/heads/merge-bot/pr-5") == sha


def test_p0_2_the_old_queue_is_never_read(env, tmp_path):
    home = tmp_path / "home"
    (home / "lanes").mkdir(parents=True)
    old = home / "lanes" / "merge-queue.tsv"
    env.pr(5, queue=False)
    old.write_text(
        f"5\t99\t/x\tlane-x\tnote\nHOLD 6 owner said wait\n5\t99\t/x\tlane-x\t{env.heads[5]}\tLOW\tn\n",
        encoding="utf-8",
    )
    before = old.read_text(encoding="utf-8")
    env.run(home=home)
    assert env.calls == "" and env.merges == []
    assert old.read_text(encoding="utf-8") == before
    assert not (home / "lanes" / "merge-queue.log").exists()
    assert not (home / "lanes" / "merge-bot.log").exists()


def test_p0_2_the_new_queue_and_log_are_the_defaults(env, tmp_path):
    home = tmp_path / "home"
    (home / "lanes").mkdir(parents=True)
    env.pr(5, queue=False)
    env.queue(5, path=home / "lanes" / "merge-bot-queue.tsv")
    env.run(home=home)
    assert (home / "lanes" / "merge-bot.log").read_text(encoding="utf-8") == "DONE 5 abc123\n"
    assert not (home / "lanes" / "merge-queue.log").exists()


def test_p0_2_an_old_format_line_in_the_new_queue_is_skipped(env):
    env.pr(5, queue=False)
    (env.tmp / "queue.tsv").write_text("5\t99\t/x\tlane-x\tnote\n", encoding="utf-8")
    r = env.run()
    assert env.calls == "" and env.log == "" and "bad queue line" in r.stderr


def test_p1_1_the_real_premerge_gates_a_branch_one_commit_behind(env):
    env.pr(5)
    env.commit("other.txt", "y\n", "main moved")
    git(env.seed, "push", "-q", "origin", "main")
    env.run(PREMERGE_CMD=f"bash {PREMERGE.as_posix()}")
    assert "detached" not in env.log
    assert env.log == "DONE 5 abc123\n"


def test_p1_1_premerge_runs_on_a_bot_branch(env):
    env.pr(5)
    env.run()
    assert (env.fix / "premerge.branch").read_text(encoding="utf-8").split() == ["merge-bot/pr-5"]


def test_p1_2_the_bot_never_merges_a_high_tier_pr(env):
    env.pr(5, tier="HIGH")
    env.run()
    assert env.log == "BACK 5 HIGH tier: lane-merge or a person merges it\n"
    assert env.merges == [] and env.premerge_runs == 0


def test_p1_2_a_body_with_no_verify_pass_line_goes_back(env):
    env.pr(5, body="Visual review: no UI change\nVERIFY: FAIL\n  VERIFY: PASS indented\n")
    env.run()
    assert env.log == "BACK 5 no VERIFY: PASS line in the PR body\n" and env.merges == []


def test_p1_3_the_newest_row_of_a_check_wins(env):
    rows = [(n, "pass", "2026-11-13T10:00:00Z") for n in REQUIRED if n != "Lint"]
    env.pr(
        5,
        checks=[
            *rows,
            ("Lint", "fail", "2026-11-13T09:00:00Z"),
            ("Lint", "pass", "2026-11-13T11:00:00Z"),
        ],
    )
    env.pr(
        6,
        checks=[
            *rows,
            ("Lint", "pass", "2026-11-13T09:00:00Z"),
            ("Lint", "fail", "2026-11-13T11:00:00Z"),
        ],
    )
    env.run()
    assert env.log == "DONE 5 abc123\nBACK 6 red: Lint\n"


def test_p1_3_a_queued_rerun_counts_as_newest(env):
    rows = [(n, "pass", "2026-11-13T10:00:00Z") for n in REQUIRED if n != "Lint"]
    env.pr(
        5,
        checks=[
            *rows,
            ("Lint", "fail", "2026-11-13T09:00:00Z"),
            ("Lint", "pending", "0001-01-01T00:00:00Z"),
        ],
    )
    env.run()
    assert env.log == "" and env.merges == []


def test_p2a_a_gh_network_error_is_a_skip(env):
    env.pr(5)
    env.run(FAKE_GH_DOWN="1")
    assert env.log == "" and env.merges == []


def test_p2a_a_failed_fetch_is_a_skip(env):
    env.pr(5)
    shutil.move(str(env.origin), str(env.tmp / "gone.git"))
    env.run()
    assert env.log == "" and env.merges == []


def test_p2a_a_failed_view_after_the_merge_is_a_skip_then_done(env):
    env.pr(5)
    env.run(FAKE_VIEW_DOWN_AFTER_MERGE="1")
    assert env.log == "" and len(env.merges) == 1
    env.run()
    assert env.log == "DONE 5 abc123\n" and len(env.merges) == 1


def test_p2b_the_gate_cleans_the_bot_workdir_first(env):
    env.pr(5)
    work = env.tmp / "work"
    (work / "notes.md").write_text("dirty\n", encoding="utf-8")
    (work / "stray.txt").write_text("left over\n", encoding="utf-8")
    env.run()
    assert env.log == "DONE 5 abc123\n" and not (work / "stray.txt").exists()


def test_p2b_the_bot_refuses_to_clean_its_own_checkout(env):
    # A copy of the script inside the temp workdir, so ROOT == WORKDIR. A broken
    # guard then resets only the temp repo, never this checkout.
    env.pr(5)
    work = env.tmp / "work"
    copy = work / "scripts" / "home_server" / "merge_bot.sh"
    copy.parent.mkdir(parents=True)
    shutil.copy(SCRIPT, copy)
    keep = work / "keep.txt"
    keep.write_text("mine\n", encoding="utf-8")
    env_ = {
        **os.environ,
        "PATH": f"{env.tmp / 'bin'}{os.pathsep}{os.environ['PATH']}",
        "FIX": env.fix.as_posix(),
        "MERGE_QUEUE": (env.tmp / "queue.tsv").as_posix(),
        "MERGE_LOG": (env.tmp / "queue.log").as_posix(),
        "MERGE_BOT_STATE": (env.tmp / "state").as_posix(),
        "MERGE_BOT_WORKDIR": work.as_posix(),
        "PREMERGE_CMD": f"bash {(env.fix / 'premerge.sh').as_posix()}",
    }
    r = subprocess.run(
        [BASH, str(copy)], env=env_, capture_output=True, text=True, encoding="utf-8"
    )
    assert r.returncode == 0, r.stderr
    assert "the workdir is the bot's own checkout" in r.stderr
    assert keep.exists() and env.log == "" and env.merges == []


def test_p2c_premerge_does_not_inherit_the_lock_fd(env):
    # The probe proves it only where flock exists (the box). The source line holds everywhere.
    assert '"$PREMERGE_CMD" 9>&-' in SCRIPT.read_text(encoding="utf-8")
    env.pr(5)
    probe = env.fix / "premerge.sh"
    probe.write_text(
        "#!/usr/bin/env bash\n"
        f'if {{ true >&9; }} 2>/dev/null; then echo open > "{env.fix.as_posix()}/fd9"; fi\nexit 0\n',
        encoding="utf-8",
        newline="\n",
    )
    env.run()
    assert not (env.fix / "fd9").exists() and env.log == "DONE 5 abc123\n"


def test_p2d_a_stale_mkdir_lock_is_cleared(env):
    env.pr(5)
    lock = env.tmp / "state" / "lock.d"
    lock.mkdir(parents=True)
    old = 1_700_000_000
    os.utime(lock, (old, old))
    r = env.run(MERGE_BOT_LOCK="mkdir")
    assert "cleared a stale lock" in r.stderr and env.log == "DONE 5 abc123\n"


def test_p2d_a_fresh_mkdir_lock_stops_the_pass(env):
    env.pr(5)
    (env.tmp / "state" / "lock.d").mkdir(parents=True)
    r = env.run(MERGE_BOT_LOCK="mkdir")
    assert "another pass runs" in r.stderr and env.calls == ""
