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


def _agent(name, sid, state, started=1):
    return {
        "name": name,
        "sessionId": sid,
        "state": state,
        "startedAt": started,
        "kind": "background",
    }


@pytest.fixture
def keeper(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KEEPER_LANES_DIR", str(tmp_path / "lanes"))
    for k in (
        "KEEPER_LANES",
        "KEEPER_SKIP_LANES",
        "KEEPER_SWITCH_CMD",
        "KEEPER_SKIP_ACCOUNTS",
        "CLAUDE_CONFIG_DIR",
    ):
        monkeypatch.delenv(k, raising=False)
    return _load(), tmp_path


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
    monkeypatch.setenv("KEEPER_LANES", "lane-b")
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
    (home / "lanes").mkdir()
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
    svc = (ROOT / "scripts" / "home_server" / "metorite-lane-keeper.service").read_text(encoding="utf-8")
    assert "WorkingDirectory=%h/Metorite" in svc
