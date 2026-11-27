"""The landing sweeper, LBB-3a: a dry report (Metorite task 4043).

Spec ``project-docs/specs/engineering_practice.md`` section 7b.5. Hermetic: a
fake ``gh`` that answers from JSON files and records every call, a fake freeze
command, temp queue files, and a temp git repo (the "origin" of the sweeper's
clone) so that ancestry and patch-ids are real.

Done-when map:
1. test_off_*                       off, no env, any other value: no gh call
2. test_dry_*                       one line for each open PR, only read calls, no queue write
3. test_rule*, test_skip_*          each rule in order, each ``would`` value, each skip reason
4. test_freeze_*                    exit 75 (and 1) turn every action into WAIT
5. test_different_patch_id_*        a different patch-id gives no HANDOVER or REQUEUE
6. test_units_*                     the timer, the env file with a "-", no secret
"""
from __future__ import annotations

import datetime as dt
import hashlib
import importlib.util
import json
import pathlib
import shutil
import subprocess
import sys

import pytest

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is missing")

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_DIR = _ROOT / "scripts/home_server"

FAKE_GH = r'''
import json, os, sys
d = os.environ["FAKE_GH_DIR"]
a = sys.argv[1:]
with open(os.path.join(d, "calls.txt"), "a", encoding="utf-8") as f:
    f.write(json.dumps(a) + "\n")
if a[:2] == ["pr", "list"]:
    name = "list.json"
elif a[:2] == ["pr", "view"]:
    name = "view-%s.json" % a[2]
elif a[:2] == ["pr", "checks"]:
    name = "checks-%s.json" % a[2]
elif a[:1] == ["api"]:
    name = "required.json"
else:
    sys.exit(2)
p = os.path.join(d, name)
if not os.path.exists(p):
    sys.exit(1)
sys.stdout.write(open(p, encoding="utf-8").read())
'''

REQUIRED = ["lint", "test"]


def _load():
    spec = importlib.util.spec_from_file_location("landing_sweeper", _DIR / "landing_sweeper.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["landing_sweeper"] = mod
    spec.loader.exec_module(mod)
    return mod


def _iso(hours_ago: float) -> str:
    t = dt.datetime.now(dt.UTC) - dt.timedelta(hours=hours_ago)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


class World:
    """An origin repo, a fake gh, a lanes folder, and one place to run the sweeper."""

    def __init__(self, tmp: pathlib.Path):
        self.tmp = tmp
        self.origin = tmp / "origin"
        self.lanes = tmp / "lanes"
        self.gh_dir = tmp / "ghdata"
        self.lanes.mkdir()
        self.gh_dir.mkdir()
        (tmp / "fake_gh.py").write_text(FAKE_GH, encoding="utf-8")
        self.origin.mkdir()
        self.g("init", "-q", "-b", "main")
        self.base = self.commit("a.txt", "base\n")
        self.main = self.commit("m.txt", "main moved\n", parent=self.base, branch_main=True)
        self.prs: list[dict] = []
        self.write("required.json", {"contexts": REQUIRED, "checks": []})
        self.freeze_rc = 0
        self.freeze = tmp / "freeze.py"

    # -- git --
    def g(self, *args: str) -> str:
        p = subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@t.invalid", "-c", "commit.gpgsign=false", *args],
            cwd=self.origin, capture_output=True, text=True, encoding="utf-8", check=True,
        )
        return p.stdout.strip()

    def commit(self, name: str, text: str, parent: str | None = None, branch_main: bool = False, msg: str = "c") -> str:
        if parent:
            self.g("checkout", "-q", "--detach", parent)
        (self.origin / name).write_text(text, encoding="utf-8")
        self.g("add", name)
        self.g("commit", "-q", "-m", msg)
        sha = self.g("rev-parse", "HEAD")
        if branch_main:
            self.g("update-ref", "refs/heads/main", sha)
            self.g("checkout", "-q", "--detach", sha)
        return sha

    def pull_ref(self, n: int, sha: str) -> None:
        self.g("update-ref", f"refs/pull/{n}/head", sha)

    # -- gh data --
    def write(self, name: str, data) -> None:
        (self.gh_dir / name).write_text(json.dumps(data), encoding="utf-8")

    def add_pr(self, n: int, sha: str, *, checks="green", commit_age=30, created_age=100, ref=True, **over) -> dict:
        pr = {
            "number": n, "author": {"login": "lane"}, "headRefName": f"feat-{n}", "headRefOid": sha,
            "baseRefName": "main", "isDraft": False, "isCrossRepository": False,
            "mergeable": "MERGEABLE", "title": f"PR {n}", "body": "", "createdAt": _iso(created_age),
        }
        pr.update(over)
        self.prs.append(pr)
        self.write("list.json", self.prs)
        self.write(f"view-{n}.json", {"commits": [{"committedDate": _iso(commit_age + 5)}, {"committedDate": _iso(commit_age)}]})
        rows = {
            "green": [{"name": r, "bucket": "pass"} for r in REQUIRED],
            "red": [{"name": "lint", "bucket": "fail"}, {"name": "test", "bucket": "pass"}],
            "none": [],
            "pending": [{"name": r, "bucket": "pending"} for r in REQUIRED],
        }[checks] if isinstance(checks, str) else checks
        self.write(f"checks-{n}.json", rows)
        if ref:
            self.pull_ref(n, sha)
        return pr

    def queue(self, name: str, lines: list[str]) -> None:
        (self.lanes / name).write_text("\n".join(lines) + "\n", encoding="utf-8")

    # -- run --
    def env(self, mode="dry") -> dict:
        self.freeze.write_text(f"import sys; sys.exit({self.freeze_rc})\n", encoding="utf-8")
        return {
            "LANDING_SWEEPER_MODE": mode,
            "LANDING_SWEEPER_GH": f"{pathlib.Path(sys.executable).as_posix()} {(self.tmp / 'fake_gh.py').as_posix()}",
            "LANDING_SWEEPER_FREEZE_CMD": f"{pathlib.Path(sys.executable).as_posix()} {self.freeze.as_posix()}",
            "LANDING_SWEEPER_LANES": str(self.lanes),
            "LANDING_SWEEPER_SRC": str(self.tmp / "clone"),
            "LANDING_SWEEPER_REPO_URL": str(self.origin),
            "FAKE_GH_DIR": str(self.gh_dir),
        }

    def sweep(self, mode="dry") -> int:
        import os
        env = self.env(mode)
        old = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        try:
            return _load().main(env)
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def calls(self) -> list[list[str]]:
        p = self.gh_dir / "calls.txt"
        if not p.exists():
            return []
        return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()]

    def report(self) -> dict[int, tuple[str, str, str]]:
        """PR -> (head sha, would, reason)."""
        p = self.lanes / "landing-sweeper.tsv"
        out = {}
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                _t, n, sha, would, reason = line.split("\t")
                out[int(n)] = (sha, would, reason)
        return out

    def verdict(self, n: int) -> tuple[str, str]:
        r = self.report()[n]
        return r[1], r[2]


@pytest.fixture
def w(tmp_path):
    return World(tmp_path)


def _hand_row(n: int, sha: str, tier="STANDARD", lane="lane-a") -> str:
    return "\t".join([str(n), "1", "-", lane, sha, tier, "note"])


def _verified_world(w: World, n=10, body_extra="", title="PR (STANDARD slice)"):
    """A green PR whose head holds main and has the verified own diff. Returns (vsha, head)."""
    x = w.commit("f.txt", "feature\n", parent=w.base, msg="feature")
    head = w.g("merge", "--no-ff", "-q", "-m", "merge main", w.main) if False else None
    w.g("checkout", "-q", "--detach", x)
    w.g("merge", "--no-ff", "-q", "-m", "merge main", w.main)
    head = w.g("rev-parse", "HEAD")
    w.add_pr(n, head, title=title, body=f"VERIFY: PASS {x[:10]}\n{body_extra}")
    return x, head


# ── 1. off ──────────────────────────────────────────────────────────────────


def test_off_mode_makes_no_gh_call(w):
    w.add_pr(1, w.main)
    assert w.sweep("off") == 0
    assert w.calls() == []
    assert w.report() == {}


def test_off_unset_mode_and_missing_env_make_no_gh_call(w, capsys):
    w.add_pr(1, w.main)
    mod = _load()
    env = w.env()
    del env["LANDING_SWEEPER_MODE"]
    assert mod.main(env) == 0
    assert w.calls() == []


def test_off_any_other_value_logs_one_line_and_makes_no_gh_call(w, capsys):
    w.add_pr(1, w.main)
    for val in ("on", "ON", "dry-run", "1"):
        assert w.sweep(val) == 0
    assert w.calls() == []
    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 4 and all("read as off" in x for x in out)


# ── 2. dry ──────────────────────────────────────────────────────────────────


def test_dry_one_line_for_each_open_pr_and_read_calls_only(w):
    w.add_pr(1, w.main, author={"login": "ishaanpilar"})
    w.add_pr(2, w.main, isDraft=True)
    w.add_pr(3, w.main)
    q = ["#hdr", _hand_row(3, w.main)]
    w.queue("merge-bot-queue.tsv", q)
    w.queue("merge-bot.log", ["DONE 3 x"])
    w.queue("merge-queue.tsv", ["REQUEUE\t9\t1\tbox\tn"])
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in w.lanes.iterdir()}
    assert w.sweep() == 0
    rep = w.report()
    assert sorted(rep) == [1, 2, 3]
    assert rep[1][0] == w.main
    for argv in w.calls():
        assert argv[0] in ("pr", "api")
        assert argv[:2] in (["pr", "list"], ["pr", "view"], ["pr", "checks"]) or argv[0] == "api"
        assert "-X" not in argv and "--method" not in argv
        assert not set(argv) & {"merge", "edit", "comment", "ready", "close"}
    after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in w.lanes.iterdir() if p.name != "landing-sweeper.tsv"}
    assert {k: v for k, v in before.items()} == after
    # one run, one line per PR, appended (a second run appends again)
    w.sweep()
    assert len((w.lanes / "landing-sweeper.tsv").read_text(encoding="utf-8").splitlines()) == 6


def test_dry_report_line_shape(w):
    w.add_pr(1, w.main, isDraft=True)
    w.sweep()
    t, n, sha, would, reason = (w.lanes / "landing-sweeper.tsv").read_text(encoding="utf-8").rstrip("\n").split("\t")
    assert t.endswith("Z") and n == "1" and sha == w.main and would == "SKIP" and reason == "draft"


def test_dry_unparseable_list_ends_run_with_exit_0_and_no_report(w):
    (w.gh_dir / "list.json").write_text("not json", encoding="utf-8")
    assert w.sweep() == 0
    assert w.report() == {}


def test_dry_gh_list_failure_ends_run_with_exit_0(w):
    assert w.sweep() == 0
    assert w.report() == {}


# ── 3. rule 1: SKIP ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "over,reason",
    [
        ({"author": {"login": "ishaanpilar"}}, "author ishaanpilar"),
        ({"headRefName": "ishaan-foo"}, "not for the sweeper"),
        ({"headRefName": "auto-revert/x"}, "not for the sweeper"),
        ({"headRefName": "release/2026"}, "not for the sweeper"),
        ({"headRefName": "green/x"}, "not for the sweeper"),
        ({"isDraft": True}, "draft"),
        ({"baseRefName": "dev"}, "is not main"),
        ({"isCrossRepository": True}, "fork"),
        ({"mergeable": "UNKNOWN"}, "mergeable is UNKNOWN"),
    ],
)
def test_skip_name_rules(w, over, reason):
    w.add_pr(1, w.main, **over)
    w.sweep()
    would, why = w.verdict(1)
    assert would == "SKIP" and reason in why


def test_skip_head_is_base_of_another_pr(w):
    w.add_pr(1, w.main, headRefName="stack-base")
    w.add_pr(2, w.main, baseRefName="stack-base")
    w.sweep()
    assert w.verdict(1) == ("SKIP", "head is the base of another open PR")


def test_skip_last_commit_under_24_hours(w):
    w.add_pr(1, w.main, commit_age=23)
    w.sweep()
    assert w.verdict(1) == ("SKIP", "last commit is less than 24 hours old")


def test_skip_error_commits(w):
    w.add_pr(1, w.main)
    (w.gh_dir / "view-1.json").unlink()
    w.sweep()
    assert w.verdict(1) == ("SKIP", "error commits")


def test_skip_error_required(w):
    w.add_pr(1, w.main)
    (w.gh_dir / "required.json").unlink()
    w.sweep()
    assert w.verdict(1) == ("SKIP", "error required")


def test_unparseable_required_ends_run_with_no_report(w):
    w.add_pr(1, w.main)
    (w.gh_dir / "required.json").write_text("nope", encoding="utf-8")
    assert w.sweep() == 0
    assert w.report() == {}


def test_skip_error_checks(w):
    w.add_pr(1, w.main)
    (w.gh_dir / "checks-1.json").unlink()
    w.sweep()
    assert w.verdict(1) == ("SKIP", "error checks")


def test_skip_error_fetch(w):
    w.add_pr(1, w.main, ref=False)
    w.sweep()
    assert w.verdict(1) == ("SKIP", "error fetch")


def test_skip_error_verify_sha(w):
    _x, _head = _verified_world(w)
    w.prs[0]["body"] = "VERIFY: PASS deadbeef"
    w.write("list.json", w.prs)
    w.sweep()
    assert w.verdict(10) == ("SKIP", "error verify sha")


def test_skip_error_verify_sha_when_sha_is_in_neither_main_nor_head(w):
    _x, _head = _verified_world(w)
    stray = w.commit("z.txt", "stray\n", parent=w.base, msg="stray")  # in origin, but on no fetched ref
    w.prs[0]["body"] = f"VERIFY: PASS {stray}"
    w.write("list.json", w.prs)
    w.sweep()
    assert w.verdict(10) == ("SKIP", "error verify sha")


def test_rule7_skip_nothing_to_do(w):
    # green, holds main, no VERIFY line, but only 40 hours old: not rule 6
    w.add_pr(1, w.main, created_age=40)
    w.sweep()
    assert w.verdict(1) == ("SKIP", "nothing to do")


# ── 3. rule 2: WAIT ─────────────────────────────────────────────────────────


def test_rule2_wait_handovers_outnumber_results(w):
    w.add_pr(1, w.main)
    w.queue("merge-bot-queue.tsv", ["#hdr", _hand_row(1, "a" * 40), _hand_row(1, "b" * 40)])
    w.queue("merge-bot.log", ["BACK 1 first"])
    w.sweep()
    would, why = w.verdict(1)
    assert would == "WAIT" and "2 hand-over" in why


def test_rule2_a_done_line_ends_the_hold(w):
    w.add_pr(1, w.main)
    w.queue("merge-bot-queue.tsv", [_hand_row(1, "a" * 40), _hand_row(1, "b" * 40)])
    w.queue("merge-bot.log", ["DONE 1 merged"])
    w.sweep()
    assert w.verdict(1)[0] != "WAIT"


def test_rule2_invalid_queue_rows_do_not_count(w):
    w.add_pr(1, w.main)
    w.queue("merge-bot-queue.tsv", ["1\tshort\trow", _hand_row(1, "xyz"), _hand_row(1, "a" * 40, tier="NONE")])
    w.sweep()
    assert w.verdict(1)[0] != "WAIT"


def test_rule2_wait_on_requeue_line(w):
    w.add_pr(2487, w.main)
    w.queue("merge-queue.tsv", ["REQUEUE\t2487\t3590\tbox-loop\tCI red cause found: x"])
    w.sweep()
    assert w.verdict(2487) == ("WAIT", "REQUEUE line in merge-queue.tsv")


def test_rule2_requeue_line_of_another_pr_does_not_hold(w):
    w.add_pr(2, w.main)
    w.queue("merge-queue.tsv", ["REQUEUE\t24\t1\tbox\tn"])
    w.sweep()
    assert w.verdict(2)[0] != "WAIT"


# ── 3. rule 3: LAND ─────────────────────────────────────────────────────────


def test_rule3_land_on_conflict_when_older_than_48h(w):
    w.add_pr(1, w.main, mergeable="CONFLICTING")
    w.sweep()
    would, why = w.verdict(1)
    assert would == "LAND" and "conflict" in why


def test_rule3_land_on_red_check_names_it(w):
    w.add_pr(1, w.main, checks="red")
    w.sweep()
    would, why = w.verdict(1)
    assert would == "LAND" and "lint" in why and "test" not in why.replace("older", "")


def test_rule3_conflict_under_48h_does_not_land(w):
    w.add_pr(1, w.main, mergeable="CONFLICTING", created_age=47)
    w.sweep()
    assert w.verdict(1)[0] != "LAND"


def test_rule3_wins_over_rule_4(w):
    # red, behind main, older than 48h: LAND, not UPDATE
    w.add_pr(1, w.base, checks="red")
    w.sweep()
    assert w.verdict(1)[0] == "LAND"


# ── 3. rule 4: UPDATE ───────────────────────────────────────────────────────


def test_rule4_update_when_behind_and_no_check_green(w):
    w.add_pr(1, w.base, checks="pending")
    w.sweep()
    assert w.verdict(1)[0] == "UPDATE"


def test_rule4_update_with_no_check_rows_at_all(w):
    w.add_pr(1, w.base, checks="none")
    w.sweep()
    assert w.verdict(1)[0] == "UPDATE"


def test_rule4_green_but_behind_is_not_updated(w):
    w.add_pr(1, w.base, checks="green", created_age=40)
    w.sweep()
    assert w.verdict(1)[0] != "UPDATE"


def test_rule4_ahead_of_main_with_pending_checks_is_not_updated(w):
    w.add_pr(1, w.main, checks="pending")
    w.sweep()
    assert w.verdict(1)[0] != "UPDATE"


# ── 3. rule 5: HANDOVER and REQUEUE ─────────────────────────────────────────


def test_rule5_handover_with_an_earlier_handover_at_the_same_patch_id(w):
    x, _head = _verified_world(w)
    w.queue("merge-bot-queue.tsv", ["#hdr", _hand_row(10, x)])
    w.queue("merge-bot.log", ["BACK 10 sent back"])
    w.sweep()
    would, why = w.verdict(10)
    assert would == "HANDOVER" and "lane-a" in why


def test_rule5_requeue_when_no_earlier_handover(w):
    _verified_world(w)
    w.sweep()
    would, why = w.verdict(10)
    assert would == "REQUEUE" and "no earlier hand-over" in why


def test_rule5_requeue_when_the_earlier_handover_has_another_patch_id(w):
    _x, _head = _verified_world(w)
    other = w.commit("o.txt", "other\n", parent=w.base, msg="other")
    w.g("update-ref", "refs/pull/99/head", other)  # makes it fetchable only if the PR 99 is listed
    w.add_pr(99, other, headRefName="other-pr")
    w.queue("merge-bot-queue.tsv", [_hand_row(10, other)])
    w.queue("merge-bot.log", ["BACK 10 sent back"])
    w.sweep()
    assert w.verdict(10)[0] == "REQUEUE"


def test_rule5_high_tier_is_always_requeue(w):
    x, _head = _verified_world(w, title="PR (HIGH slice)")
    w.queue("merge-bot-queue.tsv", [_hand_row(10, x)])
    w.queue("merge-bot.log", ["BACK 10 x"])
    w.sweep()
    would, why = w.verdict(10)
    assert would == "REQUEUE" and "tier HIGH" in why


def test_rule5_no_tier_named_means_high(w):
    x, _head = _verified_world(w, title="PR with no tier")
    w.queue("merge-bot-queue.tsv", [_hand_row(10, x)])
    w.queue("merge-bot.log", ["BACK 10 x"])
    w.sweep()
    assert w.verdict(10) == ("REQUEUE", f"verified at {x[:10]}, tier HIGH")


def test_rule5_tier_line_in_body_beats_the_title(w):
    x, _head = _verified_world(w, title="PR (HIGH x)", body_extra="Tier: LOW\n")
    w.queue("merge-bot-queue.tsv", [_hand_row(10, x)])
    w.queue("merge-bot.log", ["BACK 10 x"])
    w.sweep()
    assert w.verdict(10)[0] == "HANDOVER"


def test_rule5_the_last_verify_line_wins(w):
    x, _head = _verified_world(w)
    w.prs[0]["body"] = f"VERIFY: PASS deadbeef\nVERIFY: PASS {x}\n"
    w.write("list.json", w.prs)
    w.sweep()
    assert w.verdict(10)[0] == "REQUEUE"


def test_newest_check_row_wins_over_an_older_red_one(w):
    _x, _head = _verified_world(w)
    rows = [
        {"name": "lint", "bucket": "fail", "startedAt": _iso(5)},
        {"name": "lint", "bucket": "pass", "startedAt": _iso(1)},
        {"name": "test", "bucket": "pass", "startedAt": _iso(1)},
    ]
    w.write("checks-10.json", rows)
    w.sweep()
    assert w.verdict(10)[0] == "REQUEUE"


def test_a_required_check_with_no_row_is_not_green(w):
    _x, _head = _verified_world(w)
    w.write("checks-10.json", [{"name": "lint", "bucket": "pass"}])
    w.sweep()
    assert w.verdict(10)[0] != "REQUEUE"


# ── 5. a different patch-id ─────────────────────────────────────────────────


def test_different_patch_id_gives_no_handover_or_requeue(w):
    x, head = _verified_world(w)
    # the head gets one more commit after the verified one
    w.g("checkout", "-q", "--detach", head)
    newer = w.commit("extra.txt", "more\n", parent=head, msg="after verify")
    w.prs[0]["headRefOid"] = newer
    w.write("list.json", w.prs)
    w.pull_ref(10, newer)
    w.queue("merge-bot-queue.tsv", [_hand_row(10, x)])
    w.queue("merge-bot.log", ["BACK 10 x"])
    w.sweep()
    would, _ = w.verdict(10)
    assert would not in ("HANDOVER", "REQUEUE")
    assert would == "SKIP"


# ── 3. rule 6 ───────────────────────────────────────────────────────────────


def test_rule6_land_no_verify_sha_when_green_and_old(w):
    w.add_pr(1, w.main, created_age=100)
    w.sweep()
    assert w.verdict(1) == ("LAND", "no verify sha")


def test_rule6_not_when_a_verify_line_exists(w):
    _x, _head = _verified_world(w)
    w.sweep()
    assert w.verdict(10)[1] != "no verify sha"


# ── 4. the freeze ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("rc", [75, 1])
def test_freeze_turns_every_action_into_wait(w, rc):
    w.freeze_rc = rc
    _x, _head = _verified_world(w, n=10)
    w.add_pr(11, w.main, mergeable="CONFLICTING")      # would LAND
    w.add_pr(12, w.base, checks="pending")             # would UPDATE
    w.add_pr(13, w.main, created_age=100)              # would LAND (no verify sha)
    w.sweep()
    rep = w.report()
    for n in (10, 11, 12, 13):
        assert rep[n][1] == "WAIT", n
        assert "release freeze" in rep[n][2]
    assert "would REQUEUE" in rep[10][2] and "would LAND" in rep[11][2] and "would UPDATE" in rep[12][2]


def test_freeze_does_not_turn_a_skip_into_a_wait(w):
    w.freeze_rc = 75
    w.add_pr(1, w.main, isDraft=True)
    w.sweep()
    assert w.verdict(1)[0] == "SKIP"


def test_no_freeze_leaves_the_actions(w):
    w.freeze_rc = 0
    w.add_pr(11, w.main, mergeable="CONFLICTING")
    w.sweep()
    assert w.verdict(11)[0] == "LAND"


# ── mirrors of merge_bot.sh ─────────────────────────────────────────────────


def test_protected_head_regex_mirrors_merge_bot():
    src = (_DIR / "merge_bot.sh").read_text(encoding="utf-8")
    assert "PROTECTED_HEAD_RE='^(main|master|release|release/.*|green/.*|HEAD)$'" in src
    assert _load().PROTECTED_HEAD_RE.pattern == "^(main|master|release|release/.*|green/.*|HEAD)$"


def test_check_rule_mirrors_merge_bot_py_checks():
    src = (_DIR / "merge_bot.sh").read_text(encoding="utf-8")
    assert 'if not start and r.get("bucket") == "pending":' in src and 'return "9999"' in src
    m = _load()
    best = m.newest_check_rows([
        {"name": "a", "bucket": "fail", "startedAt": "2026-01-01T00:00:00Z"},
        {"name": "a", "bucket": "pending"},
    ])
    assert best["a"]["bucket"] == "pending"


# ── 6. the units ────────────────────────────────────────────────────────────


def test_units_timer_runs_every_two_hours():
    timer = (_DIR / "metorite-landing-sweeper.timer").read_text(encoding="utf-8")
    assert "OnUnitActiveSec=2h" in timer
    assert "WantedBy=timers.target" in timer


def test_units_service_reads_env_file_with_a_dash_and_holds_no_secret():
    svc = (_DIR / "metorite-landing-sweeper.service").read_text(encoding="utf-8")
    assert "EnvironmentFile=-%h/.config/metorite/landing-sweeper.env" in svc
    assert "Type=oneshot" in svc
    assert "ExecStart=/usr/bin/python3 %h/.local/lib/metorite/landing_sweeper.py" in svc
    assert "TimeoutStartSec=900" in svc
    for bad in ("mbt_", "TOKEN=", "SECRET", "PASSWORD", "GH_TOKEN", "ghp_"):
        assert bad not in svc
    assert "LANDING_SWEEPER_MODE" not in [ln.split("=")[0] for ln in svc.splitlines() if not ln.startswith("#")]


def test_script_has_no_github_write():
    src = (_DIR / "landing_sweeper.py").read_text(encoding="utf-8")
    code = "\n".join(ln for ln in src.splitlines() if not ln.lstrip().startswith("#"))
    for bad in ('"-X"', '"merge"', '"edit"', '"comment"', '"ready"'):
        assert bad not in code
    assert "hand_over.sh" not in code.replace('(line 76)', "").split('"""')[2]
