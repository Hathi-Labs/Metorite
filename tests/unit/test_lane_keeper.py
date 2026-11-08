"""CIV-19: the lane keeper (scripts/home_server/lane_keeper.py).

A fake `claude agents --json` output and a fake subprocess serve every case,
so no session starts.
"""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "home_server" / "lane_keeper.py"
NOW = 1_800_000_000.0


def _load():
    spec = importlib.util.spec_from_file_location("lane_keeper", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["lane_keeper"] = mod
    spec.loader.exec_module(mod)
    return mod


def _agent(name, sid, state, started=1, status="idle", short=None, pid=None):
    a = {
        "name": name,
        "sessionId": sid,
        "state": state,
        "startedAt": started,
        "kind": "background",
        "status": status,
    }
    if short:
        a["id"] = short
    if pid:
        a["pid"] = pid
    return a


@pytest.fixture
def keeper(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KEEPER_LANES_DIR", str(tmp_path / "lanes"))
    for k in (
        "KEEPER_SKIP_LANES",
        "KEEPER_SWITCH_CMD",
        "KEEPER_SKIP_ACCOUNTS",
        "CLAUDE_CONFIG_DIR",
    ):
        monkeypatch.delenv(k, raising=False)
    # The list file is the whole list of lanes that may wake (task 2206, 2026-11-08).
    names = ("lane-ci", "lane-pro", "lane-new", "lane-ghost", "lane-chat", "lane-a", "lane-b")
    _lanes(tmp_path, *names)
    return _load(), tmp_path


def _lanes(home, *names):
    (home / "lanes").mkdir(exist_ok=True)
    (home / "lanes" / "keeper-lanes.txt").write_text("\n".join(names) + "\n", encoding="utf-8")


def _transcript(home, sid, age_s):
    d = home / ".claude" / "projects" / "-p"
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{sid}.jsonl"
    f.write_text("{}", encoding="utf-8")
    os.utime(f, (NOW - age_s, NOW - age_s))


def _fake_run(calls, rc=0):
    def run(argv, **kw):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, rc, stdout="", stderr="")

    return run


def test_one_idle_lane_gets_one_wake(keeper, monkeypatch):
    mod, home = keeper
    _transcript(home, "s-ci", 1200)
    _transcript(home, "s-work", 1200)
    calls: list = []
    monkeypatch.setattr(mod.subprocess, "run", _fake_run(calls))
    agents = [_agent("lane-ci", "s-ci", "done"), _agent("lane-pro", "s-work", "working")]
    wakes = mod.run_pass(json.dumps(agents), now=NOW)
    assert [w[0] for w in wakes] == ["lane-ci"]
    assert calls[0][:4] == ["claude", "--bg", "--resume", "s-ci"]
    assert calls[0][4:7] == ["-n", "lane-ci", "--remote-control=lane-ci"]
    assert "wake lane-ci" in (home / "lanes" / "keeper.log").read_text(encoding="utf-8")


def test_no_second_wake_inside_thirty_minutes(keeper, monkeypatch):
    mod, home = keeper
    _transcript(home, "s-ci", 1200)
    calls: list = []
    monkeypatch.setattr(mod.subprocess, "run", _fake_run(calls))
    agents = json.dumps([_agent("lane-ci", "s-ci", "done")])
    assert len(mod.run_pass(agents, now=NOW)) == 1
    assert mod.run_pass(agents, now=NOW + 1500) == []
    assert len(mod.run_pass(agents, now=NOW + 1900)) == 1


def test_a_failed_wake_is_not_counted(keeper, monkeypatch):
    mod, home = keeper
    _transcript(home, "s-ci", 1200)
    monkeypatch.setattr(mod.subprocess, "run", _fake_run([], rc=1))
    agents = json.dumps([_agent("lane-ci", "s-ci", "done")])
    mod.run_pass(agents, now=NOW)
    assert len(mod.run_pass(agents, now=NOW + 60)) == 1


def test_a_fresh_transcript_a_missing_one_and_the_skip_list_do_not_wake(keeper, monkeypatch):
    mod, home = keeper
    _transcript(home, "s-new", 100)
    _transcript(home, "s-chat", 5000)
    monkeypatch.setattr(mod.subprocess, "run", _fake_run([]))
    agents = [
        _agent("lane-new", "s-new", "done"),
        _agent("lane-ghost", "s-none", "done"),
        _agent("lane-chat", "s-chat", "blocked"),
    ]
    assert mod.run_pass(json.dumps(agents), now=NOW) == []


def test_the_newest_session_of_a_name_decides(keeper):
    mod, _ = keeper
    agents = [_agent("lane-l1", "old", "blocked", 1), _agent("lane-l1", "new", "working", 2)]
    assert mod.newest_per_lane(agents)["lane-l1"]["sessionId"] == "new"
    assert (
        mod.plan(
            agents,
            {},
            NOW,
            ages={"old": 9999, "new": 9999},
            skip_lanes=set(),
            only_lanes=set(),
            force_all=False,
        )
        == []
    )


def test_only_lanes_limits_the_wake(keeper, monkeypatch):
    mod, home = keeper
    for s in ("a", "b"):
        _transcript(home, s, 5000)
    _lanes(home, "lane-b")
    monkeypatch.setattr(mod.subprocess, "run", _fake_run([]))
    agents = [_agent("lane-a", "a", "done"), _agent("lane-b", "b", "done")]
    assert [w[0] for w in mod.run_pass(json.dumps(agents), now=NOW)] == ["lane-b"]


def _account(home, email):
    (home / ".claude.json").write_text(
        json.dumps({"oauthAccount": {"emailAddress": email}}), encoding="utf-8"
    )


def test_a_switch_wakes_every_ended_lane_past_the_cap(keeper, monkeypatch):
    mod, home = keeper
    _transcript(home, "s-ci", 1200)
    monkeypatch.setattr(mod.subprocess, "run", _fake_run([]))
    agents = json.dumps([_agent("lane-ci", "s-ci", "done")])
    _account(home, "a@x.io")
    assert len(mod.run_pass(agents, now=NOW)) == 1
    assert mod.run_pass(agents, now=NOW + 60) == []
    _account(home, "b@x.io")
    wakes = mod.run_pass(agents, now=NOW + 120)
    assert [(w[0], w[2]) for w in wakes] == [("lane-ci", "account change")]


def test_the_switch_command_never_runs_on_a_skip_list_account(keeper, monkeypatch):
    mod, home = keeper
    monkeypatch.setenv("KEEPER_SWITCH_CMD", "switch-now")
    calls: list = []
    monkeypatch.setattr(mod.subprocess, "run", _fake_run(calls))
    _account(home, "vjvarada@hathilabs.com")
    mod.run_pass("[]", now=NOW)
    assert not any(isinstance(c, str) for c in calls)
    assert "switch skipped" in (home / "lanes" / "keeper.log").read_text(encoding="utf-8")
    _account(home, "other@x.io")

    def run(argv, **kw):
        calls.append(argv)
        if isinstance(argv, str):
            assert "vjvarada@hathilabs.com" in kw["env"]["KEEPER_SKIP_ACCOUNTS"]
        return subprocess.CompletedProcess(argv, 0, stdout="ok", stderr="")

    monkeypatch.setattr(mod.subprocess, "run", run)
    mod.run_pass("[]", now=NOW)
    assert "switch-now" in calls


def test_the_units_are_a_user_timer_of_ten_minutes():
    d = ROOT / "scripts" / "home_server"
    assert "OnUnitActiveSec=10min" in (d / "metorite-lane-keeper.timer").read_text(encoding="utf-8")
    svc = (d / "metorite-lane-keeper.service").read_text(encoding="utf-8")
    assert "lane_keeper.py" in svc and "Type=oneshot" in svc


def test_a_state_file_that_is_not_an_object_is_ignored(keeper, monkeypatch):
    mod, home = keeper
    _transcript(home, "s-ci", 1200)
    (home / "lanes").mkdir(exist_ok=True)
    (home / "lanes" / "keeper-state.json").write_text("[]", encoding="utf-8")
    monkeypatch.setattr(mod.subprocess, "run", _fake_run([]))
    agents = json.dumps([_agent("lane-ci", "s-ci", "done")])
    assert len(mod.run_pass(agents, now=NOW)) == 1


def test_a_failed_account_change_wake_repeats(keeper, monkeypatch):
    mod, home = keeper
    _transcript(home, "s-ci", 1200)
    agents = json.dumps([_agent("lane-ci", "s-ci", "done")])
    _account(home, "a@x.io")
    monkeypatch.setattr(mod.subprocess, "run", _fake_run([]))
    mod.run_pass(agents, now=NOW)
    _account(home, "b@x.io")
    monkeypatch.setattr(mod.subprocess, "run", _fake_run([], rc=1))
    assert [w[2] for w in mod.run_pass(agents, now=NOW + 60)] == ["account change"]
    monkeypatch.setattr(mod.subprocess, "run", _fake_run([]))
    assert [w[2] for w in mod.run_pass(agents, now=NOW + 120)] == ["account change"]


def test_the_wake_runs_in_the_directory_of_the_lane(keeper, monkeypatch):
    mod, home = keeper
    _transcript(home, "s-ci", 1200)
    lane_dir = home / "proj"
    lane_dir.mkdir()
    seen: list = []

    def run(argv, **kw):
        seen.append(kw.get("cwd"))
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(mod.subprocess, "run", run)
    agent = {**_agent("lane-ci", "s-ci", "done"), "cwd": str(lane_dir)}
    mod.run_pass(json.dumps([agent]), now=NOW)
    assert seen == [str(lane_dir)]


def test_the_service_starts_in_a_project_directory():
    svc = (ROOT / "scripts" / "home_server" / "metorite-lane-keeper.service").read_text(
        encoding="utf-8"
    )
    assert "WorkingDirectory=%h/Metorite" in svc


def test_a_wake_stops_the_old_session_and_names_the_fork_after_the_lane(keeper, monkeypatch):
    mod, home = keeper
    _lanes(home, "lane-x")
    _transcript(home, "s-x", 1200)
    calls: list = []
    monkeypatch.setattr(mod.subprocess, "run", _fake_run(calls))
    agents = [_agent("lane-x", "s-x", "done", short="abcd1234", pid=4242)]
    assert [w[0] for w in mod.run_pass(json.dumps(agents), now=NOW)] == ["lane-x"]
    assert calls[0][:3] == ["claude", "stop", "abcd1234"]
    assert calls[1][:5] == ["claude", "--bg", "--resume", "s-x", "-n"] and calls[1][5] == "lane-x"
    assert "--remote-control=lane-x" in calls[1]
    assert len(calls) == 2


def test_the_fork_with_the_lane_name_is_the_session_of_the_next_pass(keeper, monkeypatch):
    mod, home = keeper
    _lanes(home, "lane-x")
    _transcript(home, "s-old", 5000)
    _transcript(home, "s-fork", 5000)
    monkeypatch.setattr(mod.subprocess, "run", _fake_run([]))
    agents = [
        _agent("lane-x", "s-old", "done", started=1),
        _agent("lane-x", "s-fork", "done", started=2),
    ]
    wakes = mod.run_pass(json.dumps(agents), now=NOW)
    assert [(w[0], w[1]) for w in wakes] == [("lane-x", "s-fork")]


def test_a_lane_with_a_busy_session_of_its_name_is_not_woken(keeper, monkeypatch):
    mod, home = keeper
    _lanes(home, "lane-x")
    _transcript(home, "s-old", 5000)
    calls: list = []
    monkeypatch.setattr(mod.subprocess, "run", _fake_run(calls))
    agents = [
        _agent("lane-x", "s-old", "done", started=1),
        _agent("lane-x", "s-busy", "working", started=2, status="busy"),
    ]
    assert mod.run_pass(json.dumps(agents), now=NOW) == []
    assert calls == []


def _fork_transcript(home, sid, lane):
    """A fork of a lane: the header copies the lane's name, the later records do not."""
    d = home / ".claude" / "projects" / "-p"
    d.mkdir(parents=True, exist_ok=True)
    recs = [
        {"type": "history-suppression", "sessionId": sid},
        {"type": "custom-title", "customTitle": lane, "sessionId": sid},
        {"type": "agent-name", "agentName": lane, "sessionId": sid},
        {"type": "agent-name", "agentName": "merge-queue-lane", "sessionId": sid},
    ]
    (d / f"{sid}.jsonl").write_text("\n".join(json.dumps(r) for r in recs) + "\n", encoding="utf-8")


def test_the_fence_of_task_2206_an_idle_lane_a_busy_fork_and_an_unlisted_lane(keeper, monkeypatch):
    """The 5 points: lane-x is idle but has a busy auto-named fork, lane-y is idle,
    lane-z is idle and not in the list. One stop and one resume, for lane-y only."""
    mod, home = keeper
    _lanes(home, "lane-x", "lane-y")
    for sid in ("s-x", "s-y", "s-z"):
        _transcript(home, sid, 5000)
    _fork_transcript(home, "s-fork", "lane-x")
    calls: list = []
    monkeypatch.setattr(mod.subprocess, "run", _fake_run(calls))
    agents = [
        _agent("lane-x", "s-x", "done", short="x1", pid=11),
        _agent("lane-y", "s-y", "done", short="y1", pid=12),
        _agent("lane-z", "s-z", "done", short="z1", pid=13),
        _agent("merge-queue-lane", "s-fork", "working", started=3, status="busy", pid=14),
    ]
    wakes = mod.run_pass(json.dumps(agents), now=NOW)
    assert [w[0] for w in wakes] == ["lane-y"]
    assert [c[:3] for c in calls if c[1] == "stop"] == [["claude", "stop", "y1"]]
    resumes = [c for c in calls if c[1] == "--bg"]
    assert len(resumes) == 1 and resumes[0][3] == "s-y" and resumes[0][4:6] == ["-n", "lane-y"]


def test_an_idle_fork_of_another_name_does_not_block(keeper, monkeypatch):
    mod, home = keeper
    _lanes(home, "lane-x")
    _transcript(home, "s-x", 5000)
    _fork_transcript(home, "s-other", "lane-q")
    monkeypatch.setattr(mod.subprocess, "run", _fake_run([]))
    agents = [
        _agent("lane-x", "s-x", "done"),
        _agent("some-other-session", "s-other", "working", started=3, status="busy"),
    ]
    assert [w[0] for w in mod.run_pass(json.dumps(agents), now=NOW)] == ["lane-x"]


def test_a_session_without_a_process_is_not_stopped(keeper, monkeypatch):
    mod, home = keeper
    _lanes(home, "lane-x")
    _transcript(home, "s-x", 5000)
    calls: list = []
    monkeypatch.setattr(mod.subprocess, "run", _fake_run(calls))
    mod.run_pass(json.dumps([_agent("lane-x", "s-x", "done", short="x1")]), now=NOW)
    assert [c[1] for c in calls] == ["--bg"]


def test_a_failed_stop_means_no_resume_and_a_retry(keeper, monkeypatch):
    mod, home = keeper
    _lanes(home, "lane-x")
    _transcript(home, "s-x", 5000)
    calls: list = []

    def run(argv, **kw):
        calls.append(argv)
        return subprocess.CompletedProcess(
            argv, 1 if argv[1] == "stop" else 0, stdout="", stderr="no"
        )

    monkeypatch.setattr(mod.subprocess, "run", run)
    agents = json.dumps([_agent("lane-x", "s-x", "done", short="x1", pid=7)])
    mod.run_pass(agents, now=NOW)
    assert [c[1] for c in calls] == ["stop"]
    assert "stop failed" in (home / "lanes" / "keeper.log").read_text(encoding="utf-8")
    mod.run_pass(agents, now=NOW + 60)  # no cap after a failure: it tries again
    assert [c[1] for c in calls] == ["stop", "stop"]


def test_a_stop_that_times_out_does_not_lose_the_earlier_wake(keeper, monkeypatch):
    mod, home = keeper
    _lanes(home, "lane-a", "lane-b")
    for sid in ("s-a", "s-b"):
        _transcript(home, sid, 5000)

    def run(argv, **kw):
        if argv[1] == "stop":
            raise subprocess.TimeoutExpired(argv, 60)
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(mod.subprocess, "run", run)
    agents = [
        _agent("lane-a", "s-a", "done"),
        _agent("lane-b", "s-b", "done", short="b1", pid=9),
    ]
    mod.run_pass(json.dumps(agents), now=NOW)
    state = json.loads((home / "lanes" / "keeper-state.json").read_text(encoding="utf-8"))
    assert state.get("lane-a") == NOW and "lane-b" not in state


def test_an_unlisted_lane_is_not_woken_and_no_list_file_wakes_nothing(keeper, monkeypatch):
    mod, home = keeper
    _transcript(home, "s-x", 5000)
    _transcript(home, "s-z", 5000)
    monkeypatch.setattr(mod.subprocess, "run", _fake_run([]))
    agents = json.dumps([_agent("lane-x", "s-x", "done"), _agent("lane-z", "s-z", "done")])
    _lanes(home, "lane-x")
    assert [w[0] for w in mod.run_pass(agents, now=NOW)] == ["lane-x"]
    (home / "lanes" / "keeper-lanes.txt").unlink()
    assert mod.run_pass(agents, now=NOW + 99999) == []
    assert "no lane named" in (home / "lanes" / "keeper.log").read_text(encoding="utf-8")
