"""Fence of scripts/idle_work.py, IDLE-1 (task 4216), engineering_practice.md §7b.11.

A fake machine: a bare origin, a clone with worktrees in each state, rescue
refs, and fake ``gh`` and ``claude`` scripts. Real ``git`` runs on it. The
finder only reads, so each test also proves that no worktree changed.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def _load():
    spec = importlib.util.spec_from_file_location("idle_work", REPO / "scripts" / "idle_work.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


iw = _load()
NOW = time.time()
OLD = NOW - 30 * 3600  # older than every limit
ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
       "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}


def git(cwd: Path, *args: str, when: float | None = None) -> str:
    env = dict(ENV)
    if when is not None:
        stamp = f"@{int(when)} +0000"
        env.update(GIT_AUTHOR_DATE=stamp, GIT_COMMITTER_DATE=stamp)
    return subprocess.run(["git", *args], cwd=cwd, env=env, check=True,
                          capture_output=True, text=True).stdout


def commit(cwd: Path, name: str, msg: str, when: float = OLD) -> None:
    (cwd / name).write_text(msg, encoding="utf-8")
    git(cwd, "add", name)
    git(cwd, "commit", "-q", "-m", msg, when=when)


def age(path: Path, when: float) -> None:
    os.utime(path, (when, when))


def script(path: Path, body: str) -> str:
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    path.chmod(0o755)
    return str(path)


@pytest.fixture
def machine(tmp_path):
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    repo = tmp_path / "repo"
    git(tmp_path, "clone", "-q", str(origin), str(repo))
    git(repo, "checkout", "-q", "-b", "main")
    commit(repo, "README", "base")
    git(repo, "push", "-q", "origin", "main")

    def tree(slug: str, branch: str) -> Path:
        path = tmp_path / f"wt-{slug}"
        git(repo, "worktree", "add", "-q", "-b", branch, str(path), "main")
        return path

    # S1: a dirty file, idle 30 h.
    dirty = tree("dirty", "devx-dirty-mt-aaaa1111")
    (dirty / "wip.txt").write_text("unsaved", encoding="utf-8")
    age(dirty / "wip.txt", OLD)
    # S2: a commit on no remote, idle 30 h.
    unpushed = tree("unpushed", "devx-unpushed-mt-bbbb2222")
    commit(unpushed, "local.txt", "local only")
    # Active: a dirty file changed now. Rule 2: no item.
    active = tree("active", "devx-active-mt-cccc3333")
    (active / "now.txt").write_text("just now", encoding="utf-8")
    # S3: a pushed branch with no PR, its commit 30 h old.
    git(repo, "checkout", "-q", "-b", "devx-nopr-mt-dddd4444", "main")
    commit(repo, "nopr.txt", "work with no PR")
    git(repo, "push", "-q", "origin", "devx-nopr-mt-dddd4444")
    git(repo, "checkout", "-q", "main")
    # Not S3: a branch that main holds (a direct merge), with no PR.
    git(repo, "checkout", "-q", "-b", "devx-merged-mt-9999aaaa", "main")
    commit(repo, "merged.txt", "merged work")
    git(repo, "push", "-q", "origin", "devx-merged-mt-9999aaaa")
    git(repo, "checkout", "-q", "main")
    git(repo, "merge", "-q", "--no-ff", "-m", "merge", "devx-merged-mt-9999aaaa", when=OLD)
    git(repo, "push", "-q", "origin", "main")
    # S3 with no short id in its name: the task comes from ITS commit.
    git(repo, "checkout", "-q", "-b", "devx-plain", "main")
    commit(repo, "plain.txt", "work for mt-1234abcd")
    git(repo, "push", "-q", "origin", "devx-plain")
    git(repo, "checkout", "-q", "main")
    commit(repo, "head.txt", "main head names mt-deadbeef")
    # Not S3: a branch with a PR, and a person's branch.
    for b in ("devx-haspr-mt-eeee5555", "ishaan-feature"):
        git(repo, "branch", b, "main")
        git(repo, "push", "-q", "origin", b)
    # A rescue ref whose commit names its source branch.
    rescue = tree("rescue", "devx-saved-mt-ffff6666")
    commit(rescue, "saved.txt", "rescue: no review, do not merge (from devx-saved-mt-ffff6666)")
    git(rescue, "push", "-q", "origin", "HEAD:refs/heads/rescue/devx-saved/20261128")
    git(repo, "worktree", "remove", "--force", str(rescue))

    projects = tmp_path / "projects" / "p"
    projects.mkdir(parents=True)
    agents = []
    for name, sid, cwd in [("lane-idle", "s1", str(tmp_path / "elsewhere")),
                           ("lane-keep", "s2", str(tmp_path / "elsewhere")),
                           ("lane-keeper", "s3", str(tmp_path / "elsewhere")),
                           ("lane-heldtree", "s4", str(dirty)),
                           ("lane-fresh", "s5", str(tmp_path / "elsewhere"))]:
        t = projects / f"{sid}.jsonl"
        t.write_text("{}\n", encoding="utf-8")
        age(t, NOW if name == "lane-fresh" else OLD)
        agents.append({"name": name, "sessionId": sid, "cwd": cwd, "state": "idle",
                       "kind": "background", "id": sid, "startedAt": 0})
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    agents_file = tmp_path / "agents.json"
    agents_file.write_text(json.dumps(agents), encoding="utf-8")
    prs_file = tmp_path / "prs.json"
    prs_file.write_text(json.dumps([{"headRefName": "devx-haspr-mt-eeee5555"}]), encoding="utf-8")
    lanes = tmp_path / "lanes"
    lanes.mkdir()
    (lanes / "keeper-lanes.txt").write_text("lane-keeper\n", encoding="utf-8")
    keep = tmp_path / "idle-keep.txt"
    keep.write_text("lane-keep  # the owner's local server\n", encoding="utf-8")

    class M:
        pass

    m = M()
    m.repo, m.dirty, m.unpushed, m.active, m.lanes, m.keep = repo, dirty, unpushed, active, lanes, keep
    m.projects = projects.parent
    m.gh = script(bin_ / "gh", f'cat "{prs_file}"\n')
    m.claude = script(bin_ / "claude", f'cat "{agents_file}"\n')
    return m


def finder(m, cls=None, **kw):
    args = dict(now=NOW, machine="metorite-home", person=False, repo=m.repo, gh=m.gh,
                claude=m.claude, projects=m.projects, keep_file=m.keep, lanes_dir=m.lanes,
                proc=False, runs_reader=lambda: None)
    args.update(kw)
    return (cls or iw.Finder)(**args)


def by_state(lines):
    out: dict[str, list] = {}
    for s, t, k, r in lines:
        out.setdefault(s, []).append((t, k, r))
    return out


def snapshot(path: Path) -> tuple:
    return (git(path, "status", "--porcelain"), git(path, "rev-parse", "HEAD"),
            git(path, "rev-parse", "--abbrev-ref", "HEAD"))


def test_one_line_for_each_state_on_a_fake_machine(machine):
    s = by_state(finder(machine).scan())
    assert [(t, k) for t, k, _ in s["S1"]] == [(str(machine.dirty), "mt-aaaa1111")]
    assert [(t, k) for t, k, _ in s["S2"]] == [(str(machine.unpushed), "mt-bbbb2222")]
    assert sorted((t, k) for t, k, _ in s["S3"]) == [
        ("devx-nopr-mt-dddd4444", "mt-dddd4444"), ("devx-plain", "mt-1234abcd")]
    assert [t for t, _, _ in s["RESCUE"]] == ["rescue/devx-saved/20261128"]
    assert s["RESCUE"][0][1] == "mt-ffff6666"
    assert {"LBB-3", "REAPER", "S4", "S5"} <= set(s)


def test_a_worktree_changed_in_the_last_hour_gives_no_item(machine):
    lines = finder(machine).scan()
    assert all(str(machine.active) != t for _, t, _, _ in lines)


def test_a_branch_with_a_pr_and_a_persons_branch_are_not_s3(machine):
    s3 = [t for st, t, _, _ in finder(machine).scan() if st == "S3"]
    assert "devx-haspr-mt-eeee5555" not in s3 and "ishaan-feature" not in s3 and "main" not in s3


def test_s4_names_why_a_session_is_never_stopped(machine):
    s4 = {t: r for st, t, _, r in finder(machine).scan() if st == "S4"}
    assert "lane-fresh" not in s4  # its transcript changed now
    assert "lane-heldtree" not in s4  # its worktree holds S1: save first
    assert "idle-keep.txt: never stopped" in s4["lane-keep"]
    assert "keeper lane: never stopped" in s4["lane-keeper"]
    assert "never" not in s4["lane-idle"]


def test_on_a_persons_machine_s4_only_reports(machine):
    s4 = {t: r for st, t, _, r in finder(machine, machine="vj-pc", person=True).scan() if st == "S4"}
    assert "a person's machine: report only" in s4["lane-idle"]
    assert "idle-keep.txt: never stopped" in s4["lane-keep"]


def test_s5_reports_a_stale_run_of_this_machine_whose_session_is_gone(machine):
    runs = [
        {"id": "r-gone", "task_id": "t1", "state": "stale", "stranded": None,
         "agent_host": {"untrusted_text": "metorite-home"}, "agent_label": {"untrusted_text": "lane-old"}},
        {"id": "r-live", "task_id": "t2", "state": "stale", "stranded": None,
         "agent_host": {"untrusted_text": "metorite-home"}, "agent_label": {"untrusted_text": "lane-idle"}},
        {"id": "r-other", "task_id": "t3", "state": "stale", "stranded": None,
         "agent_host": {"untrusted_text": "vj-pc"}, "agent_label": {"untrusted_text": "pc-x"}},
        {"id": "r-working", "task_id": "t4", "state": "working", "stranded": None,
         "agent_host": {"untrusted_text": "metorite-home"}, "agent_label": {"untrusted_text": "lane-old"}},
    ]
    s5 = [t for st, t, _, _ in finder(machine, runs_reader=lambda: runs).scan() if st == "S5"]
    assert s5 == ["r-gone"]


def test_s5_with_no_token_says_so(machine):
    s5 = [r for st, _, _, r in finder(machine).scan() if st == "S5"]
    assert s5 == ["skipped: no Metorite token on this machine"]


def test_windows_skips_the_reaper(machine):
    calls = []

    def runner(argv, cwd, **kw):
        calls.append(argv)
        return iw.run(argv, cwd, **kw)

    lines = finder(machine, proc=False, runner=runner).scan()
    assert [r for st, _, _, r in lines if st == "REAPER"] == ["skipped: no /proc on this machine (Windows)"]
    assert not any("reaper.py" in " ".join(a) for a in calls)


def test_the_finder_changes_no_worktree(machine):
    before = {p: snapshot(p) for p in (machine.dirty, machine.unpushed, machine.active)}
    finder(machine).scan()
    assert {p: snapshot(p) for p in before} == before


def test_the_finder_only_reads(machine):
    """No push, no commit, no PR write, no session stop: IDLE-1 has no action."""
    calls = []

    def runner(argv, cwd, **kw):
        calls.append(argv)
        return iw.run(argv, cwd, **kw)

    finder(machine, runner=runner).scan()
    words = [" ".join(a) for a in calls]
    for bad in ("push", "commit", "pr create", "pr edit", "pr merge", "agents stop", "reset", "checkout"):
        assert not any(f" {bad}" in w for w in words), bad


def test_a_tab_in_a_name_cannot_break_the_report(machine):
    f = finder(machine)
    f.add("S3", "bad\tname\nx", None, "r\tr")
    assert f.report()[-1].count("\t") == 6


def test_off_runs_nothing(monkeypatch, capsys):
    monkeypatch.setenv("IDLE_WORK_MODE", "off")
    monkeypatch.setattr(iw, "Finder", lambda **kw: pytest.fail("the finder must not run"))
    assert iw.main([]) == 0
    monkeypatch.delenv("IDLE_WORK_MODE")
    assert iw.main([]) == 0
    assert "Nothing runs" in capsys.readouterr().out


def test_apply_runs_dry_and_says_so(machine, monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("IDLE_WORK_MODE", "apply:S1")
    monkeypatch.setenv("IDLE_WORK_NOW", str(NOW))
    made = []
    real = iw.Finder

    def make(**kw):
        f = finder(machine, cls=real)
        made.append(f)
        return f

    monkeypatch.setattr(iw, "Finder", make)
    out = tmp_path / "report.tsv"
    assert iw.main(["--report", str(out)]) == 0
    assert "no action is built yet" in capsys.readouterr().out
    assert made and isinstance(made[0], real)
    rows = out.read_text(encoding="utf-8").splitlines()
    assert rows and all(r.split("\t")[3] == "would" for r in rows)


def test_a_status_that_runs_too_long_is_unknown_and_holds_its_session(machine):
    def runner(argv, cwd, **kw):
        if "status" in argv and str(cwd) == str(machine.dirty):
            return 124, ""
        return iw.run(argv, cwd, **kw)

    lines = finder(machine, runner=runner).scan()
    s = by_state(lines)
    assert [t for t, _, _ in s["UNKNOWN"]] == [str(machine.dirty)]
    assert str(machine.dirty) not in [t for t, _, _ in s.get("S1", [])]


def test_every_git_read_takes_no_optional_lock(machine):
    """A read must never refresh the index of a worktree (7b.11.4)."""
    calls = []

    def runner(argv, cwd, **kw):
        calls.append(argv)
        return iw.run(argv, cwd, **kw)

    finder(machine, runner=runner).scan()
    gits = [a for a in calls if a[0] == "git"]
    assert gits and all(a[1] == "--no-optional-locks" for a in gits)


def test_s3_skips_a_branch_that_main_holds(machine):
    s3 = [t for st, t, _, _ in finder(machine).scan() if st == "S3"]
    assert "devx-merged-mt-9999aaaa" not in s3


def test_s3_takes_the_task_from_its_own_commit_not_the_checkout_head(machine):
    s3 = {t: k for st, t, k, _ in finder(machine).scan() if st == "S3"}
    assert s3["devx-plain"] == "mt-1234abcd"  # main's HEAD names mt-deadbeef
