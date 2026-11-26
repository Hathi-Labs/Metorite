"""CIV-RN (task 4055): `merge_bot.sh --renumber <pr> <sha>` sets the migration number at merge.

lane-merge runs the sub-command right before premerge (lane-merge.md step d). A
migration that a PR adds, at or below main's highest number, gets the next number
of the box ledger. The bot pushes the renumber with no force and logs RENUMBERED.

The temp repos, the bare origin and the `gh` shim come from test_merge_bot.py.
A fake HOME holds the ledger and a stand-in for ~/lanes/reserve_migration.sh,
with the same rule: one above the highest of main and the ledger.
"""

import os
import subprocess
import time
from pathlib import Path

import pytest

from tests.unit.test_merge_bot import (
    BASH,
    OUTSIDE_FREEZE,
    PREMERGE,
    SCRIPT,
    Env,
    _push_main,
    git,
    pr_branch,
    pr_file,
    pr_message,
)

pytestmark = pytest.mark.skipif(BASH is None, reason="needs bash")
RENUMBER_PY = Path(__file__).resolve().parents[2] / "scripts" / "renumber_migration.py"

# The stand-in for ~/lanes/reserve_migration.sh. It reads main of the temp origin.
FAKE_RESERVE = r"""#!/usr/bin/env bash
set -euo pipefail
F="$HOME/lanes/migration-numbers.tsv"
lane="$1"; task="$2"; pr="${3:--}"
[ -f "$F" ] || printf 'number\tlane\ttask\tpr\tbooked_utc\n' > "$F"
main_top=$(git -C "$FAKE_ORIGIN" ls-tree --name-only main infra/postgres/ | sed -nE 's#.*/0*([0-9]+)_.*#\1#p' | sort -n | tail -1)
list_top=$(tail -n +2 "$F" | cut -f1 | sort -n | tail -1)
top=$(( 10#${main_top:-0} > 10#${list_top:-0} ? 10#${main_top:-0} : 10#${list_top:-0} ))
n=$(( top + 1 ))
printf '%s\t%s\t%s\t%s\t%s\n' "$n" "$lane" "$task" "$pr" "2026-11-26T10:00Z" >> "$F"
echo "$n"
"""


@pytest.fixture
def env(tmp_path):
    e = Env(tmp_path)
    home = tmp_path / "home"
    (home / "lanes").mkdir(parents=True)
    reserve = home / "lanes" / "reserve_migration.sh"
    reserve.write_text(FAKE_RESERVE, encoding="utf-8", newline="\n")
    reserve.chmod(0o755)
    e.home = home
    # The bot runs MAIN's copy of the tool, so the temp main holds this checkout's copy.
    _push_main(e, "scripts/renumber_migration.py", RENUMBER_PY.read_text(encoding="utf-8"), "tool")
    # Main reaches 500 before any PR is cut.
    _push_main(e, "infra/postgres/499_main_a.sql", "select 1;\n", "main 499")
    _push_main(e, "infra/postgres/500_main_b.sql", "select 2;\n", "main 500")
    return e


def mig_pr(env, n, number, name="feat", ref=True):
    """A PR that adds infra/postgres/<number>_<name>.sql, and a doc that names the file."""
    file = f"infra/postgres/{number}_{name}.sql"
    sha = env.pr(n, path=file, text="create table t();\n", queue=False, tier="HIGH")
    if ref:
        git(env.seed, "checkout", "-q", f"pr{n}")
        env.commit(f"docs/pr{n}.md", f"The table comes from {file}.\n", f"pr {n} doc")
        git(
            env.seed,
            "push",
            "-q",
            "-f",
            "origin",
            f"pr{n}:refs/pull/{n}/head",
            f"pr{n}:refs/heads/pr{n}",
        )
        sha = git(env.seed, "rev-parse", "HEAD")
        git(env.seed, "checkout", "-q", "main")
        env.heads[n] = sha
        env.view(n, headRefOid=sha)
    return sha


def renumber(env, pr, sha, *args, **extra):
    run_env = {
        **os.environ,
        "PATH": f"{env.tmp / 'bin'}{os.pathsep}{os.environ['PATH']}",
        "FIX": env.fix.as_posix(),
        "HOME": env.home.as_posix(),
        "FAKE_ORIGIN": env.origin.as_posix(),
        "MERGE_BOT_STATE": (env.tmp / "state").as_posix(),
        "MERGE_BOT_WORKDIR": (env.tmp / "work").as_posix(),
        "MERGE_BOT_BACK_COMMENT": "0",
    }
    for key in ("MERGE_QUEUE", "MERGE_LOG", "RENUMBER_BOOK_CMD"):
        run_env.pop(key, None)
    run_env.update(extra)
    return subprocess.run(
        [BASH, str(SCRIPT), "--renumber", str(pr), sha, *args],
        env=run_env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )


def bot_log(env):
    p = env.home / "lanes" / "merge-bot.log"
    return p.read_text(encoding="utf-8") if p.exists() else ""


def ledger_rows(env):
    p = env.home / "lanes" / "migration-numbers.tsv"
    if not p.exists():
        return []
    return [line.split("\t")[:4] for line in p.read_text(encoding="utf-8").splitlines()[1:]]


def pr_files(env, n):
    return git(env.origin, "ls-tree", "-r", "--name-only", f"refs/heads/pr{n}").splitlines()


def premerge_at(env, n, ledger):
    """The REAL premerge.sh, --no-tests, in a fresh clone on the PR branch."""
    clone = env.tmp / f"clone{n}-{len(list(env.tmp.glob('clone*')))}"
    subprocess.run(
        ["git", "clone", "-q", str(env.origin), str(clone)], check=True, capture_output=True
    )
    git(clone, "checkout", "-q", "-B", f"pr{n}", f"origin/pr{n}")
    return subprocess.run(
        [BASH, str(PREMERGE), "--no-tests"],
        cwd=clone,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        env={
            **os.environ,
            "PREMERGE_NOW": str(OUTSIDE_FREEZE),
            "PREMERGE_LEDGER": str(ledger),
            "PREMERGE_PR": str(n),
            "PATH": f"{env.tmp / 'bin'}{os.pathsep}{os.environ['PATH']}",
            "FIX": env.fix.as_posix(),
        },
    )


# ── (a) a low number moves to the ledger's next number ──────────────────────


def test_a_low_migration_is_renumbered_pushed_logged_and_then_passes_premerge(env):
    old = mig_pr(env, 5, 497)
    # Main moves once more, so premerge reaches step 1 (the branch is behind main).
    _push_main(env, "notes.md", "two\n", "main moved")
    before = premerge_at(env, 5, env.home / "lanes" / "migration-numbers.tsv")
    assert before.returncode == 1 and "not above main's highest number (500)" in before.stderr
    assert "merge_bot.sh --renumber" in before.stderr

    r = renumber(env, 5, old, "--task", "4055")
    assert r.returncode == 0, r.stderr
    new = pr_branch(env, 5)
    assert new != old and r.stdout.strip().splitlines()[-1] == new
    files = pr_files(env, 5)
    assert "infra/postgres/501_feat.sql" in files and "infra/postgres/497_feat.sql" not in files
    assert pr_file(env, 5, "docs/pr5.md") == "The table comes from infra/postgres/501_feat.sql."
    assert pr_message(env, 5).startswith(
        "merge gate: renumber migration 497 to 501 above main's 500 (R1)"
    )
    # The new commit sits on the old head: a plain push, no force.
    assert git(env.origin, "rev-parse", f"{new}~1") == old
    assert bot_log(env) == "RENUMBERED 5 497->501\n"
    assert ledger_rows(env) == [["501", "lane-merge", "4055", "5"]]
    # The sub-command never merges: the new head needs its own pr-check.
    assert env.merges == []
    after = premerge_at(env, 5, env.home / "lanes" / "migration-numbers.tsv")
    assert after.returncode == 0, after.stderr
    assert "WARNING" not in after.stdout + after.stderr


# ── (b) two PRs with the same low number never collide ─────────────────────


def test_two_prs_with_the_same_number_get_different_numbers(env):
    # 500 equals main's top: a number that main holds is renumbered too.
    a = mig_pr(env, 5, 500, name="alpha")
    b = mig_pr(env, 6, 500, name="beta")
    ra = renumber(env, 5, a)
    rb = renumber(env, 6, b)
    assert ra.returncode == 0 and rb.returncode == 0, ra.stderr + rb.stderr
    assert "infra/postgres/501_alpha.sql" in pr_files(env, 5)
    assert "infra/postgres/502_beta.sql" in pr_files(env, 6)
    assert bot_log(env) == "RENUMBERED 5 500->501\nRENUMBERED 6 500->502\n"
    assert ledger_rows(env) == [
        ["501", "lane-merge", "pr5", "5"],
        ["502", "lane-merge", "pr6", "6"],
    ]


def test_two_low_migrations_in_one_pr_are_renumbered_in_order_in_one_commit(env):
    git(env.seed, "checkout", "-q", "-B", "pr7", "main")
    env.commit("infra/postgres/498_one.sql", "select 1;\n", "one")
    env.commit("infra/postgres/499_two.sql", "select 2;\n", "two")
    git(env.seed, "push", "-q", "origin", "pr7:refs/pull/7/head", "pr7:refs/heads/pr7")
    sha = git(env.seed, "rev-parse", "HEAD")
    git(env.seed, "checkout", "-q", "main")
    env.view(
        7,
        state="OPEN",
        isDraft=False,
        mergeCommit=None,
        headRefOid=sha,
        body="",
        headRefName="pr7",
        isCrossRepository=False,
        baseRefName="main",
    )
    r = renumber(env, 7, sha)
    assert r.returncode == 0, r.stderr
    files = pr_files(env, 7)
    assert "infra/postgres/501_one.sql" in files and "infra/postgres/502_two.sql" in files
    assert bot_log(env) == "RENUMBERED 7 498->501, 499->502\n"
    assert git(env.origin, "rev-list", "--count", f"{sha}..refs/heads/pr7") == "1"


# ── (c) nothing to renumber ─────────────────────────────────────────────────


def test_a_pr_with_no_migration_is_untouched(env):
    sha = env.pr(5, queue=False)
    r = renumber(env, 5, sha)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "nothing to renumber"
    assert pr_branch(env, 5) == sha and bot_log(env) == "" and ledger_rows(env) == []


def test_a_migration_above_mains_top_is_untouched(env):
    sha = mig_pr(env, 5, 501)
    r = renumber(env, 5, sha)
    assert r.returncode == 0 and r.stdout.strip() == "nothing to renumber"
    assert pr_branch(env, 5) == sha and bot_log(env) == "" and ledger_rows(env) == []


# ── (d) a refusal leaves the PR as it was ───────────────────────────────────


def _work_is_clean_at(env, sha):
    work = env.tmp / "work"
    assert git(work, "status", "--porcelain") == ""
    assert git(work, "rev-parse", "HEAD") == sha


def test_a_refused_push_leaves_no_partial_state(env):
    sha = mig_pr(env, 5, 497)
    hook = env.origin / "hooks" / "pre-receive"
    hook.write_text(
        "#!/usr/bin/env bash\necho 'no write reach' >&2\nexit 1\n", encoding="utf-8", newline="\n"
    )
    hook.chmod(0o755)
    r = renumber(env, 5, sha)
    assert r.returncode == 1
    assert "renumbered 497->501 but the push failed" in r.stderr
    assert pr_branch(env, 5) == sha and bot_log(env) == ""
    _work_is_clean_at(env, sha)


def test_a_head_that_moved_during_the_renumber_is_not_pushed_over(env):
    sha = mig_pr(env, 5, 497)
    parent = git(env.seed, "rev-parse", "pr5~1")
    # The booking command moves the branch back, as an author's reset would.
    book = (
        f'git -C "{env.seed.as_posix()}" push -q -f origin {parent}:refs/heads/pr5 && '
        f'"$HOME/lanes/reserve_migration.sh" lane-merge pr5 5'
    )
    r = renumber(env, 5, sha, RENUMBER_BOOK_CMD=book)
    assert r.returncode == 1 and "the head branch moved during the renumber" in r.stderr
    assert pr_branch(env, 5) == parent and bot_log(env) == ""
    _work_is_clean_at(env, sha)


def test_a_failed_booking_pushes_nothing(env):
    sha = mig_pr(env, 5, 497)
    r = renumber(env, 5, sha, RENUMBER_BOOK_CMD="echo 'ledger locked' >&2; exit 1")
    assert r.returncode == 1 and "the renumber of 497 failed" in r.stderr
    assert pr_branch(env, 5) == sha and bot_log(env) == ""
    _work_is_clean_at(env, sha)


def test_a_head_that_is_not_the_given_sha_is_refused(env):
    sha = mig_pr(env, 5, 497)
    env.view(5, headRefOid="f" * 40)
    r = renumber(env, 5, sha)
    assert r.returncode == 1 and "use the head now" in r.stderr
    assert pr_branch(env, 5) == sha and ledger_rows(env) == []


def test_a_fork_or_a_protected_head_is_refused(env):
    sha = mig_pr(env, 5, 497)
    env.view(5, isCrossRepository=True)
    assert renumber(env, 5, sha).returncode == 1
    env.view(5, isCrossRepository=False, headRefName="main")
    r = renumber(env, 5, sha)
    assert r.returncode == 1 and "protected" in r.stderr
    assert pr_branch(env, 5) == sha and ledger_rows(env) == []


def test_bad_arguments_are_refused(env):
    assert renumber(env, 5, "abc").returncode == 1
    r = subprocess.run(
        [BASH, str(SCRIPT), "--merge"],
        capture_output=True,
        text=True,
        timeout=30,
        env={**os.environ, "MERGE_BOT_STATE": (env.tmp / "state").as_posix()},
    )
    assert r.returncode == 1 and "unknown argument" in r.stderr


def test_the_renumber_reuses_the_one_push_of_the_bot():
    """CIV-RN: a second push seam is a defect. Both writers call push_head."""
    text = SCRIPT.read_text(encoding="utf-8")
    assert text.count('git push -q origin "HEAD:refs/heads/$HEADREF"') == 1
    assert 'push_head "$sha" resolution' in text and 'push_head "$sha" renumber' in text
    assert "RENUMBER_RULES" not in text


# ── Fix round 1 (review of c5625b5c9f) ─────────────────────────────────────


def files_pr(env, n, files):
    """A PR that adds each path of `files` (a dict path -> text) in one commit."""
    git(env.seed, "checkout", "-q", "-B", f"pr{n}", "main")
    for path, text in files.items():
        (env.seed / path).parent.mkdir(parents=True, exist_ok=True)
        (env.seed / path).write_text(text, encoding="utf-8")
    git(env.seed, "add", "-A")
    git(env.seed, "commit", "-qm", f"pr {n}")
    git(
        env.seed,
        "push",
        "-q",
        "-f",
        "origin",
        f"pr{n}:refs/pull/{n}/head",
        f"pr{n}:refs/heads/pr{n}",
    )
    sha = git(env.seed, "rev-parse", "HEAD")
    git(env.seed, "checkout", "-q", "main")
    env.heads[n] = sha
    env.view(
        n,
        state="OPEN",
        isDraft=False,
        mergeCommit=None,
        headRefOid=sha,
        body="",
        headRefName=f"pr{n}",
        isCrossRepository=False,
        baseRefName="main",
    )
    return sha


def test_p1_the_pr_keeps_its_own_order(env):
    """R6: 499 creates t1 and 501 alters it. Main is at 500. Both move, in the old order."""
    sha = files_pr(
        env,
        5,
        {
            "infra/postgres/499_create_t1.sql": "create table t1();\n",
            "infra/postgres/501_alter_t1.sql": "alter table t1 add c int;\n",
            "docs/t1.md": "infra/postgres/499_create_t1.sql, then infra/postgres/501_alter_t1.sql\n",
        },
    )
    r = renumber(env, 5, sha)
    assert r.returncode == 0, r.stderr
    sql = sorted(f for f in pr_files(env, 5) if f.startswith("infra/postgres/"))
    assert sql == [
        "infra/postgres/499_main_a.sql",
        "infra/postgres/500_main_b.sql",
        "infra/postgres/501_create_t1.sql",
        "infra/postgres/502_alter_t1.sql",
    ]
    assert pr_file(env, 5, "docs/t1.md") == (
        "infra/postgres/501_create_t1.sql, then infra/postgres/502_alter_t1.sql"
    )
    assert bot_log(env) == "RENUMBERED 5 499->501, 501->502\n"


def test_p2_a_subfolder_never_counts(env):
    sha = files_pr(env, 5, {"infra/postgres/generated/400_gen.sql": "select 1;\n"})
    r = renumber(env, 5, sha)
    assert r.returncode == 0 and r.stdout.strip() == "nothing to renumber"
    sha = files_pr(
        env,
        6,
        {
            "infra/postgres/generated/400_gen.sql": "select 1;\n",
            "infra/postgres/497_feat.sql": "select 2;\n",
        },
    )
    r = renumber(env, 6, sha)
    assert r.returncode == 0, r.stderr
    files = pr_files(env, 6)
    assert (
        "infra/postgres/generated/400_gen.sql" in files and "infra/postgres/501_feat.sql" in files
    )
    assert bot_log(env) == "RENUMBERED 6 497->501\n"


def test_f1_the_bot_runs_mains_copy_of_the_tool_never_the_prs(env):
    marker = env.tmp / "pr-code-ran"
    evil = f"open({str(marker)!r}, 'w').close()\nraise SystemExit(0)\n"
    sha = files_pr(
        env,
        5,
        {"infra/postgres/497_feat.sql": "select 1;\n", "scripts/renumber_migration.py": evil},
    )
    r = renumber(env, 5, sha)
    assert r.returncode == 0, r.stderr
    assert not marker.exists()
    assert "infra/postgres/501_feat.sql" in pr_files(env, 5)


def test_a_prose_line_with_the_old_number_is_named_in_the_log(env):
    sha = files_pr(
        env,
        5,
        {
            "infra/postgres/497_feat.sql": "select 1;\n",
            "docs/feat.md": "Intro.\nSee migration 497 for the grants.\n",
        },
    )
    r = renumber(env, 5, sha)
    assert r.returncode == 0, r.stderr
    assert bot_log(env) == "RENUMBERED 5 497->501 check by hand: docs/feat.md:2\n"
    assert r.stdout.strip() == pr_branch(env, 5)


def test_a_number_that_is_taken_pushes_nothing(env):
    sha = mig_pr(env, 5, 497)
    r = renumber(env, 5, sha, RENUMBER_BOOK_CMD="echo 500")
    assert r.returncode == 1 and "number 500 is taken" in r.stderr
    assert pr_branch(env, 5) == sha and bot_log(env) == ""
    _work_is_clean_at(env, sha)


def test_a_dirty_tree_after_the_renumber_pushes_nothing(env):
    sha = mig_pr(env, 5, 497)
    book = f'touch "{(env.tmp / "work").as_posix()}/stray.txt" && "$HOME/lanes/reserve_migration.sh" lane-merge pr5 5'
    r = renumber(env, 5, sha, RENUMBER_BOOK_CMD=book)
    assert r.returncode == 1 and "the tree is dirty" in r.stderr
    assert pr_branch(env, 5) == sha and bot_log(env) == ""
    _work_is_clean_at(env, sha)


def _hold(cmd):
    p = subprocess.Popen(cmd)
    time.sleep(0.5)
    return p


@pytest.mark.skipif(not __import__("shutil").which("flock"), reason="needs flock")
def test_a_held_flock_exits_75(env):
    sha = mig_pr(env, 5, 497)
    (env.tmp / "state").mkdir(exist_ok=True)
    holder = _hold(["flock", str(env.tmp / "state" / "lock"), "sleep", "30"])
    try:
        r = renumber(env, 5, sha, MERGE_BOT_RENUMBER_WAIT="1")
    finally:
        holder.kill()
        holder.wait()
    assert r.returncode == 75 and "holds the lock" in r.stderr
    assert pr_branch(env, 5) == sha and ledger_rows(env) == []


def test_a_held_mkdir_lock_waits_then_exits_75(env):
    sha = mig_pr(env, 5, 497)
    (env.tmp / "state" / "lock.d").mkdir(parents=True)
    started = time.monotonic()
    r = renumber(env, 5, sha, MERGE_BOT_LOCK="mkdir", MERGE_BOT_RENUMBER_WAIT="2")
    assert r.returncode == 75 and "holds the lock" in r.stderr
    assert time.monotonic() - started >= 2
    assert pr_branch(env, 5) == sha and ledger_rows(env) == []
