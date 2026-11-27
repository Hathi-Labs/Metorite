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


def test_task_2466_idle_status_with_state_working_wakes_only_when_the_transcript_is_old(keeper, monkeypatch):
    mod, home = keeper
    _transcript(home, "s-old", 2000)
    _transcript(home, "s-mid", 1200)
    calls: list = []
    monkeypatch.setattr(mod.subprocess, "run", _fake_run(calls))
    agents = [
        _agent("lane-a", "s-old", "working", status="idle"),
        _agent("lane-b", "s-mid", "working", status="idle"),
        _agent("lane-ci", "s-old", "working", status="busy"),
    ]
    wakes = mod.run_pass(json.dumps(agents), now=NOW)
    assert wakes == [("lane-a", "s-old", "stale-working")]
    assert "reason=stale-working" in (home / "lanes" / "keeper.log").read_text(encoding="utf-8")


def test_task_2466_a_listed_lane_with_no_session_starts_fresh_from_its_note(keeper, monkeypatch):
    mod, home = keeper
    (home / "lanes" / "start").mkdir()
    (home / "lanes" / "start" / "lane-a.txt").write_text("FRESH NOTE\n", encoding="utf-8")
    calls: list = []
    monkeypatch.setattr(mod.subprocess, "run", _fake_run(calls))
    agents = json.dumps([_agent("lane-b", "s-b", "working", status="busy")])
    wakes = mod.run_pass(agents, now=NOW)
    assert ("lane-a", "", "fresh start") in wakes
    assert calls[0] == ["claude", "--bg", "-n", "lane-a", "--remote-control=lane-a", "FRESH NOTE"]
    # The cap holds, a lane without a note is not started, and a skip-listed lane is not.
    assert [w for w in mod.run_pass(agents, now=NOW + 600) if w[2] == "fresh start"] == []
    assert len(calls) == 1
    log_text = (home / "lanes" / "keeper.log").read_text(encoding="utf-8")
    assert "no start note" in log_text and "start lane-chat" not in log_text


# --- CIV-ORCH (task 4058): wake lane-orch for a new batch of events -----------


def _orch(home, events, cursor=None):
    (home / "lanes" / "orch-events.log").write_text(
        "".join(f"EVENT\tidle\t10:00 UTC\tlane-a\tidle {i} min\n" for i in range(events)),
        encoding="utf-8",
    )
    c = home / ".local" / "state" / "metorite" / "orch_events.cursor"
    if cursor is not None:
        c.parent.mkdir(parents=True, exist_ok=True)
        c.write_text(f"{cursor}\n", encoding="utf-8")


def _orch_calls(calls):
    return [c for c in calls if "lane-orch" in c]


def test_civ_orch_one_wake_per_batch_of_events(keeper, monkeypatch):
    mod, home = keeper
    _lanes(home, "lane-orch")
    _transcript(home, "s-o", 60)  # fresh: the normal wake leaves it alone
    calls: list = []
    monkeypatch.setattr(mod.subprocess, "run", _fake_run(calls))
    agents = json.dumps([_agent("lane-orch", "s-o", "done")])
    _orch(home, 2, cursor=0)
    wakes = mod.run_pass(agents, now=NOW)
    assert wakes == [("lane-orch", "s-o", "orch-events")]
    resume = _orch_calls(calls)[0]
    assert resume[:4] == ["claude", "--bg", "--resume", "s-o"] and resume[-1] == mod.ORCH_NOTE
    log_text = (home / "lanes" / "keeper.log").read_text(encoding="utf-8")
    assert "reason=orch-events (orch-events lines 1-2)" in log_text
    # Same batch, lane-orch ended again and moved no cursor: no second wake.
    assert mod.run_pass(agents, now=NOW + 300) == []
    # A new batch: one more wake, with no 30-minute cap.
    _orch(home, 3)
    assert [w[2] for w in mod.run_pass(agents, now=NOW + 600)] == ["orch-events"]
    assert len(_orch_calls(calls)) == 2


def test_civ_orch_no_wake_when_the_lane_runs_or_no_event_is_new(keeper, monkeypatch):
    mod, home = keeper
    _lanes(home, "lane-orch")
    _transcript(home, "s-o", 60)
    calls: list = []
    monkeypatch.setattr(mod.subprocess, "run", _fake_run(calls))
    # No event past the cursor.
    _orch(home, 4, cursor=4)
    assert mod.run_pass(json.dumps([_agent("lane-orch", "s-o", "done")]), now=NOW) == []
    # No log at all.
    (home / "lanes" / "orch-events.log").unlink()
    assert mod.run_pass(json.dumps([_agent("lane-orch", "s-o", "done")]), now=NOW) == []
    # New events, but lane-orch has a busy session.
    _orch(home, 6, cursor=4)
    busy = [_agent("lane-orch", "s-o", "working", status="busy")]
    assert mod.run_pass(json.dumps(busy), now=NOW) == []
    # New events, but lane-orch is inside a turn (state working, a fresh transcript).
    inside = [_agent("lane-orch", "s-o", "working", status="idle")]
    assert mod.run_pass(json.dumps(inside), now=NOW) == []
    # New events, but lane-orch is not listed.
    _lanes(home, "lane-a")
    assert mod.run_pass(json.dumps([_agent("lane-orch", "s-o", "done")]), now=NOW) == []
    assert _orch_calls(calls) == []


def test_civ_orch_a_failed_wake_tries_again_and_a_lane_with_no_session_starts(keeper, monkeypatch):
    mod, home = keeper
    _lanes(home, "lane-orch")
    _transcript(home, "s-o", 60)
    _orch(home, 1)
    monkeypatch.setattr(mod.subprocess, "run", _fake_run([], rc=1))
    agents = json.dumps([_agent("lane-orch", "s-o", "done")])
    assert mod.run_pass(agents, now=NOW) == []
    monkeypatch.setattr(mod.subprocess, "run", _fake_run([]))
    assert [w[2] for w in mod.run_pass(agents, now=NOW + 60)] == ["orch-events"]
    # No session: the start note starts it for the batch, one time.
    (home / "lanes" / "start").mkdir()
    (home / "lanes" / "start" / "lane-orch.txt").write_text("ORCH NOTE\n", encoding="utf-8")
    _orch(home, 2)
    calls: list = []
    monkeypatch.setattr(mod.subprocess, "run", _fake_run(calls))
    wakes = mod.run_pass("[]", now=NOW + 99999)
    assert [w[2] for w in wakes] == ["orch-events"]
    assert calls == [["claude", "--bg", "-n", "lane-orch", "--remote-control=lane-orch", "ORCH NOTE"]]
    assert mod.run_pass("[]", now=NOW + 99999 + 60) == []


def test_civ_orch_only_events_wake_lane_orch(keeper, monkeypatch):
    mod, home = keeper
    _lanes(home, "lane-orch", "lane-a")
    _transcript(home, "s-o", 5000)  # ended and old: the plain wake would take it
    _transcript(home, "s-a", 5000)
    (home / "lanes" / "start").mkdir()
    (home / "lanes" / "start" / "lane-orch.txt").write_text("ORCH NOTE\n", encoding="utf-8")
    calls: list = []
    monkeypatch.setattr(mod.subprocess, "run", _fake_run(calls))
    _orch(home, 3, cursor=3)
    agents = [_agent("lane-orch", "s-o", "done"), _agent("lane-a", "s-a", "done")]
    assert [w[0] for w in mod.run_pass(json.dumps(agents), now=NOW)] == ["lane-a"]
    # No session and no new event: no fresh start either.
    assert mod.run_pass("[]", now=NOW + 99999) == []
    assert _orch_calls(calls) == []


def test_civ_orch_a_line_that_comes_while_lane_orch_works_wakes_it_again(keeper, monkeypatch):
    """The cursor race of the review: lane-orch writes the count that it READ."""
    mod, home = keeper
    _lanes(home, "lane-orch")
    _transcript(home, "s-o", 60)
    calls: list = []
    monkeypatch.setattr(mod.subprocess, "run", _fake_run(calls))
    ended = json.dumps([_agent("lane-orch", "s-o", "done")])
    _orch(home, 2, cursor=0)
    assert [w[2] for w in mod.run_pass(ended, now=NOW)] == ["orch-events"]
    # lane-orch is busy. It read 2 lines, and a third line comes.
    _orch(home, 3)
    busy = json.dumps([_agent("lane-orch", "s-o", "working", status="busy")])
    assert mod.run_pass(busy, now=NOW + 300) == []
    # It ends its turn, and writes the count that it read: 2.
    _orch(home, 3, cursor=2)
    assert [w[2] for w in mod.run_pass(ended, now=NOW + 600)] == ["orch-events"]
    assert "lines 3-3" in (home / "lanes" / "keeper.log").read_text(encoding="utf-8")
    assert "write THAT count" in mod.ORCH_NOTE and "never a new `wc -l`" in mod.ORCH_NOTE


# --- CIV-LIVE (task 4060): the stop cause, the backoff and the stop log --------
# The records copy the shapes of real transcripts (2026-11), with every id,
# path and token taken out.


def _ts(t):
    import datetime as dt

    return dt.datetime.fromtimestamp(t, dt.UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _prompt(text="Your turn ended."):
    return {"type": "user", "message": {"role": "user", "content": text}, "timestamp": _ts(NOW - 3000)}


def _tool_use(name="mcp__metorite__my_work"):
    return {
        "type": "assistant",
        "message": {"role": "assistant", "content": [{"type": "tool_use", "id": "tu1", "name": name, "input": {}}]},
    }


def _tool_result(text, error=False):
    return {
        "type": "user",
        "message": {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "tu1", "content": text, "is_error": error}],
        },
    }


def _said(text="Done for now."):
    return {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}


def _api_error(text, error, at=NOW - 1200, **extra):
    """A synthetic assistant record of Claude Code: the turn ended on an API error."""
    return {
        "type": "assistant",
        "timestamp": _ts(at),
        "message": {"model": "<synthetic>", "role": "assistant", "content": [{"type": "text", "text": text}]},
        "error": error,
        "isApiErrorMessage": True,
        **extra,
    }


END = [
    {"type": "system", "subtype": "stop_hook_summary", "hookCount": 1},
    {"type": "system", "subtype": "turn_duration", "durationMs": 1000},
]
BOOKKEEPING = [{"type": "bridge-session"}, {"type": "cost-state"}, {"type": "last-prompt"}]
WEEKLY_RESET = 1_800_003_600  # the resetsAt field of the record
USAGE_WEEKLY = _api_error(
    "You've hit your weekly limit · resets Nov 23, 11pm (UTC)",
    "rate_limit",
    apiError="usage_limit_reached",
    apiErrorStatus=429,
    apiErrorParams={"rate_limit_info": {"status": "rejected", "resetsAt": WEEKLY_RESET}},
    quotaLimits={"status": "rejected", "resetsAt": WEEKLY_RESET, "rateLimitType": "seven_day"},
)
NETWORK_LOST = _api_error(
    "API Error: Connection lost mid-response. The response above may be incomplete.",
    "server_error",
    apiError="connection_failed",
    streamCutCause="stale_connection",
)
SIGNOUT_CLAUDE = _api_error(
    "Please run /login · API Error: 403 Attention Required! | Cloudflare",
    "authentication_failed",
    apiErrorStatus=403,
)


def _tail(*recs):
    # A real tail starts with half a record: the read cut it.
    return 'ype":"user"}\n' + "\n".join(json.dumps(r) for r in recs) + "\n"


def test_civ_live_stop_cause_reads_each_cause_from_a_real_tail(keeper):
    mod, _ = keeper
    sc = mod.stop_cause
    # network: a lost connection, and an ENOTFOUND text with a 5xx status.
    assert sc(_tail(_prompt(), NETWORK_LOST, *BOOKKEEPING)).cause == "network"
    dns = _api_error("API Error: getaddrinfo ENOTFOUND api.anthropic.com", "unknown")
    assert sc(_tail(_prompt(), dns)).cause == "network"
    five = _api_error("API Error: 529 overloaded_error", "unknown", apiErrorStatus=529)
    assert sc(_tail(_prompt(), five)).cause == "network"
    # usage: the reset comes from the record field.
    assert sc(_tail(_prompt(), USAGE_WEEKLY, *BOOKKEEPING)) == mod.Stop("usage", WEEKLY_RESET)
    # signout: Claude, and the Metorite MCP in a tool result of the last turn.
    assert sc(_tail(_prompt(), SIGNOUT_CLAUDE, *BOOKKEEPING)).cause == "signout"
    mcp = _tool_result('MCP server "metorite" needs you to sign in again (run /mcp to re-authenticate)', True)
    assert sc(_tail(_prompt(), _tool_use(), mcp, _said("The board is down."), *END)).cause == "signout"
    # memory: the turn stopped inside a tool call, with no end record and no process.
    mid = _tail(_prompt(), _tool_use("Bash"))
    assert sc(mid, alive=False).cause == "memory"
    assert sc(mid, alive=True).cause == "normal" and sc(mid).cause == "normal"
    # normal: an end record, even with "fetch failed" in tool output and no process.
    git = _tool_result("warning: git fetch failed, so origin/main may be stale")
    assert sc(_tail(_prompt(), _tool_use("Bash"), git, _said(), *END, *BOOKKEEPING), alive=False).cause == "normal"
    # A sign-in error of an earlier turn does not count, nor does an error before a new prompt.
    old = _tail(_prompt(), _tool_use(), mcp, _said(), *END, _prompt(), _said(), *END)
    assert sc(old).cause == "normal"
    assert sc(_tail(USAGE_WEEKLY, _prompt("You have new orders."), _said(), *END)).cause == "normal"
    assert sc("").cause == "normal"


def test_civ_live_the_reset_time_comes_from_the_text_when_no_field_has_it(keeper):
    mod, _ = keeper
    import datetime as dt

    at = dt.datetime(2026, 11, 18, 7, 20, tzinfo=dt.UTC).timestamp()
    five_hour = _api_error("You've hit your 5-hour limit · resets 9:20am (UTC)", "rate_limit", at=at)
    want = dt.datetime(2026, 11, 18, 9, 20, tzinfo=dt.UTC).timestamp()
    assert mod.stop_cause(_tail(five_hour)) == mod.Stop("usage", want)
    late = _api_error("You've hit your 5-hour limit · resets 1am (UTC)", "rate_limit", at=at)
    nxt = dt.datetime(2026, 11, 19, 1, 0, tzinfo=dt.UTC).timestamp()
    assert mod.stop_cause(_tail(late)).reset_at == nxt
    weekly = _api_error("You've hit your weekly limit · resets Nov 23, 11pm (UTC)", "rate_limit", at=at)
    assert mod.stop_cause(_tail(weekly)).reset_at == dt.datetime(2026, 11, 23, 23, tzinfo=dt.UTC).timestamp()
    bare = _api_error("Rate limit reached.", "rate_limit", at=at)
    assert mod.stop_cause(_tail(bare)) == mod.Stop("usage", None)


def _write(home, sid, recs, age_s):
    d = home / ".claude" / "projects" / "-p"
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{sid}.jsonl"
    f.write_text("\n".join(json.dumps(r) for r in recs) + "\n", encoding="utf-8")
    os.utime(f, (NOW - age_s, NOW - age_s))
    return f


def _log(home):
    return (home / "lanes" / "keeper.log").read_text(encoding="utf-8")


def _stops(home):
    p = home / "lanes" / "lane-stops.tsv"
    return [ln.split("\t") for ln in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []


def test_civ_live_network_backoff_is_2_5_15_30_minutes_and_resets_after_a_good_turn(keeper, monkeypatch):
    mod, home = keeper
    _lanes(home, "lane-n")
    calls: list = []
    monkeypatch.setattr(mod.subprocess, "run", _fake_run(calls))
    agents = json.dumps([_agent("lane-n", "s-n", "blocked")])
    net = [_prompt(), NETWORK_LOST]
    t = NOW  # every pass runs at NOW: the backoff reads the transcript age only
    # Step 1: 2 minutes. At 1 minute, a skip with the cause in the log.
    _write(home, "s-n", net, 60)
    assert mod.run_pass(agents, now=t) == []
    assert "skip lane-n cause=network: network backoff 2 min" in _log(home)
    _write(home, "s-n", net, 130)
    assert [w[0] for w in mod.run_pass(agents, now=t)] == ["lane-n"]
    assert "reason=blocked cause=network exit=0" in _log(home)
    # Steps 2 to 4, then the last step holds. No 30-minute cap and no 10-minute age.
    for step, wait in enumerate((300, 900, 1800, 1800), start=2):
        _write(home, "s-n", net, wait - 30)
        assert mod.run_pass(agents, now=t) == [], step
        _write(home, "s-n", net, wait + 10)
        assert len(mod.run_pass(agents, now=t)) == 1, step
    # A turn that worked: the backoff is back at 2 minutes.
    _write(home, "s-n", [_prompt(), _said(), *END], 700)
    assert len(mod.run_pass(agents, now=t + 2000)) == 1
    _write(home, "s-n", net, 130)
    assert len(mod.run_pass(agents, now=t)) == 1


def test_civ_live_no_usage_wake_before_the_reset_plus_two_minutes(keeper, monkeypatch):
    mod, home = keeper
    _lanes(home, "lane-u")
    calls: list = []
    monkeypatch.setattr(mod.subprocess, "run", _fake_run(calls))
    agents = json.dumps([_agent("lane-u", "s-u", "blocked")])
    _write(home, "s-u", [_prompt(), USAGE_WEEKLY, *BOOKKEEPING], 1200)
    reset = WEEKLY_RESET
    # The transcript is old and the cap is clear, but the limit holds.
    assert mod.run_pass(agents, now=reset - 600) == []
    assert mod.run_pass(agents, now=reset + 119) == []
    assert "skip lane-u cause=usage: usage limit, no wake before" in _log(home)
    assert calls == []
    assert [w[0] for w in mod.run_pass(agents, now=reset + 121)] == ["lane-u"]
    # With no reset time: 60 minutes after the last transcript write.
    _lanes(home, "lane-v")
    bare = _api_error("Rate limit reached.", "rate_limit")
    v = json.dumps([_agent("lane-v", "s-v", "blocked")])
    _write(home, "s-v", [_prompt(), bare], 3000)
    assert mod.run_pass(v, now=NOW) == []
    _write(home, "s-v", [_prompt(), bare], 3700)
    assert [w[0] for w in mod.run_pass(v, now=NOW)] == ["lane-v"]


def test_civ_live_an_account_change_wakes_a_usage_lane(keeper, monkeypatch):
    mod, home = keeper
    _lanes(home, "lane-u")
    monkeypatch.setattr(mod.subprocess, "run", _fake_run([]))
    agents = json.dumps([_agent("lane-u", "s-u", "blocked")])
    _write(home, "s-u", [_prompt(), USAGE_WEEKLY], 1200)
    _account(home, "a@x.io")
    assert mod.run_pass(agents, now=NOW) == []
    _account(home, "b@x.io")
    assert [w[2] for w in mod.run_pass(agents, now=NOW + 60)] == ["account change"]


def test_civ_live_a_signout_never_wakes_and_writes_one_note(keeper, monkeypatch):
    mod, home = keeper
    _lanes(home, "lane-s")
    calls: list = []
    monkeypatch.setattr(mod.subprocess, "run", _fake_run(calls))
    agents = json.dumps([_agent("lane-s", "s-s", "blocked")])
    _write(home, "s-s", [_prompt(), SIGNOUT_CLAUDE, *BOOKKEEPING], 99999)
    for i in range(4):
        assert mod.run_pass(agents, now=NOW + i * 3600) == []
    _account(home, "b@x.io")  # an account change does not wake it either
    assert mod.run_pass(agents, now=NOW + 99999) == []
    assert calls == []
    notes = (home / "lanes" / "merge-queue.log").read_text(encoding="utf-8").splitlines()
    assert notes == [
        "NOTE\tlane-keeper\t"
        + mod._hhmm(NOW)
        + " UTC: lane-s stopped on a sign-out of its Claude account. "
        "See docs/home_box.md §5.1: run hub-add-accounts, then start fresh lanes."
    ]
    assert "skip lane-s cause=signout: no wake on a sign-out" in _log(home)
    # A sign-out of the Metorite MCP is the same cause, so still no second NOTE.
    mcp = _tool_result('MCP server "metorite" needs authentication', True)
    _write(home, "s-s", [_prompt(), _tool_use(), mcp, _said(), *END], 99999)
    mod.run_pass(agents, now=NOW + 200000)
    assert len((home / "lanes" / "merge-queue.log").read_text(encoding="utf-8").splitlines()) == 1


def test_civ_live_the_stop_log_opens_once_and_closes_when_the_lane_runs(keeper, monkeypatch):
    mod, home = keeper
    _lanes(home, "lane-n", "lane-m", "lane-ok")
    monkeypatch.setattr(mod.subprocess, "run", _fake_run([]))
    monkeypatch.setattr(mod, "pid_alive", lambda pid: False)
    _write(home, "s-n", [_prompt(), NETWORK_LOST], 60)
    _write(home, "s-m", [_prompt(), _tool_use("Bash")], 60)  # killed inside a tool call
    _write(home, "s-ok", [_prompt(), _said(), *END], 60)
    ended = [
        _agent("lane-n", "s-n", "blocked"),
        _agent("lane-m", "s-m", "blocked"),
        _agent("lane-ok", "s-ok", "done"),
    ]
    mod.run_pass(json.dumps(ended), now=NOW)
    mod.run_pass(json.dumps(ended), now=NOW + 60)  # seen again: no second row
    iso = mod._iso
    assert _stops(home) == [[iso(NOW), "-", "lane-m", "memory"], [iso(NOW), "-", "lane-n", "network"]]
    # lane-n runs again: its row closes. lane-m stays open.
    running = [
        _agent("lane-n", "s-n", "working", status="busy"),
        _agent("lane-m", "s-m", "blocked"),
        _agent("lane-ok", "s-ok", "done"),
    ]
    mod.run_pass(json.dumps(running), now=NOW + 600)
    assert _stops(home) == [
        [iso(NOW), "-", "lane-m", "memory"],
        [iso(NOW), iso(NOW + 600), "lane-n", "network"],
    ]
    assert "stop-log close lane-n cause=network" in _log(home)
    # It stops on the network again: a new row.
    mod.run_pass(json.dumps(ended), now=NOW + 1200)
    assert _stops(home)[-1] == [iso(NOW + 1200), "-", "lane-n", "network"]


def test_civ_live_the_time_lost_table_sums_hours_by_cause(keeper):
    mod, _ = keeper
    iso = mod._iso
    text = "\n".join(
        [
            f"{iso(NOW - 9 * 86400)}\t{iso(NOW - 8 * 86400)}\tlane-a\tnetwork",  # outside the window
            f"{iso(NOW - 7 * 86400 - 3600)}\t{iso(NOW - 7 * 86400 + 3600)}\tlane-a\tnetwork",  # half inside
            f"{iso(NOW - 3 * 86400)}\t{iso(NOW - 3 * 86400 + 7200)}\tlane-a\tusage",
            f"{iso(NOW - 5400)}\t-\tlane-b\tsignout",  # open: counts until now
            "a broken line",
        ]
    )
    table = mod.time_lost(text, NOW)
    assert "| Lane | network | usage | signout | memory | Total |" in table
    assert "| lane-a | 1.0 | 2.0 | 0.0 | 0.0 | 3.0 |" in table
    assert "| lane-b | 0.0 | 0.0 | 1.5 | 0.0 | 1.5 |" in table
    assert "| Total | 1.0 | 2.0 | 1.5 | 0.0 | 4.5 |" in table
    assert "No lane stopped" in mod.time_lost("", NOW)


def test_civ_live_a_metorite_signout_note_names_the_sign_in_alarm(keeper, monkeypatch):
    mod, home = keeper
    _lanes(home, "lane-s")
    monkeypatch.setattr(mod.subprocess, "run", _fake_run([]))
    mcp = _tool_result('MCP server "metorite" needs authentication', True)
    _write(home, "s-s", [_prompt(), _tool_use(), mcp, _said(), *END], 99999)
    assert mod.run_pass(json.dumps([_agent("lane-s", "s-s", "done")]), now=NOW) == []
    note = (home / "lanes" / "merge-queue.log").read_text(encoding="utf-8")
    assert "lane-s stopped on a sign-out of the Metorite MCP" in note
    assert "(docs/home_box.md §5.2 and §5.3)" in note


def test_civ_live_a_bash_error_that_quotes_the_sign_in_text_is_not_a_signout(keeper):
    mod, _ = keeper
    quoted = _tool_result(
        'grep: MCP server "metorite" needs you to sign in again (run /mcp to re-authenticate)', True
    )
    tail = _tail(_prompt(), _tool_use("Bash"), quoted, _said(), *END)
    assert mod.stop_cause(tail).cause == "normal"
    # The same text from a Metorite tool is a sign-out.
    real = _tail(_prompt(), _tool_use("mcp__metorite__my_work"), quoted, _said(), *END)
    assert mod.stop_cause(real) == mod.Stop("signout", kind="metorite")


def test_civ_live_a_plain_text_reset_date_in_the_past_is_next_year_and_dropped(keeper):
    mod, _ = keeper
    import datetime as dt

    at = dt.datetime(2026, 11, 25, 10, tzinfo=dt.UTC).timestamp()
    old = _api_error("You've hit your weekly limit · resets Nov 23, 11pm (UTC)", "rate_limit", at=at)
    # Nov 23 of 2027 is more than 8 days ahead: no reset, so the 60-minute wait serves.
    assert mod.stop_cause(_tail(old)) == mod.Stop("usage", None)
    near = _api_error("You've hit your weekly limit · resets Nov 27, 11pm (UTC)", "rate_limit", at=at)
    assert mod.stop_cause(_tail(near)).reset_at == dt.datetime(2026, 11, 27, 23, tzinfo=dt.UTC).timestamp()
    far = _api_error("limit", "rate_limit", at=at, quotaLimits={"resetsAt": at + 9 * 86400})
    assert mod.stop_cause(_tail(far)).reset_at is None


def test_civ_live_an_open_row_of_a_lane_that_left_the_list_closes(keeper, monkeypatch):
    mod, home = keeper
    _lanes(home, "lane-n", "lane-x")
    monkeypatch.setattr(mod.subprocess, "run", _fake_run([]))
    _write(home, "s-n", [_prompt(), NETWORK_LOST], 60)
    _write(home, "s-x", [_prompt(), NETWORK_LOST], 60)
    agents = json.dumps([_agent("lane-n", "s-n", "blocked"), _agent("lane-x", "s-x", "blocked")])
    mod.run_pass(agents, now=NOW)
    _lanes(home, "lane-x")  # lane-n leaves the list
    mod.run_pass(agents, now=NOW + 600)
    iso = mod._iso
    assert _stops(home) == [
        [iso(NOW), iso(NOW + 600), "lane-n", "network"],
        [iso(NOW), "-", "lane-x", "network"],
    ]
    state = json.loads((home / "lanes" / "keeper-state.json").read_text(encoding="utf-8"))
    assert list(state["_stops"]) == ["lane-x"]
    # The pure rule: a lane missing from seen closes, with its cause kept.
    open_stops = {"lane-q": {"cause": "usage", "start": iso(NOW)}}
    assert mod.update_stops(open_stops, {}, NOW + 60) == [("close", "lane-q", "usage", iso(NOW))]
    assert open_stops == {}


def test_civ_live_the_stop_log_drops_closed_rows_older_than_30_days(keeper, tmp_path):
    mod, _ = keeper
    iso = mod._iso
    path = tmp_path / "lane-stops.tsv"
    path.write_text(
        f"{iso(NOW - 40 * 86400)}\t{iso(NOW - 31 * 86400)}\tlane-a\tusage\n"
        f"{iso(NOW - 40 * 86400)}\t-\tlane-b\tmemory\n"
        f"{iso(NOW - 20 * 86400)}\t{iso(NOW - 19 * 86400)}\tlane-c\tnetwork\n",
        encoding="utf-8",
    )
    mod.write_stops(path, [("open", "lane-d", "network", iso(NOW))], NOW)
    lanes = [ln.split("\t")[2] for ln in path.read_text(encoding="utf-8").splitlines()]
    assert lanes == ["lane-b", "lane-c", "lane-d"]


def test_civ_live_lane_orch_does_not_wake_on_a_signout_and_waits_for_a_usage_reset(keeper, monkeypatch):
    mod, home = keeper
    _lanes(home, "lane-orch")
    calls: list = []
    monkeypatch.setattr(mod.subprocess, "run", _fake_run(calls))
    agents = json.dumps([_agent("lane-orch", "s-o", "done")])
    _orch(home, 2, cursor=0)
    _write(home, "s-o", [_prompt(), SIGNOUT_CLAUDE], 60)
    assert mod.run_pass(agents, now=NOW) == []
    assert "skip lane-orch cause=signout" in _log(home)
    _write(home, "s-o", [_prompt(), USAGE_WEEKLY], 60)
    assert mod.run_pass(agents, now=WEEKLY_RESET + 60) == []
    assert "skip lane-orch cause=usage" in _log(home)
    assert _orch_calls(calls) == []
    assert [w[2] for w in mod.run_pass(agents, now=WEEKLY_RESET + 121)] == ["orch-events"]


def test_civ_live_one_bad_tail_does_not_stop_the_pass(keeper, monkeypatch):
    mod, home = keeper
    _lanes(home, "lane-a", "lane-b")
    monkeypatch.setattr(mod.subprocess, "run", _fake_run([]))
    _write(home, "s-a", [_prompt(), _said(), *END], 5000)
    _write(home, "s-b", [_prompt(), _said(), *END], 5000)
    real = mod.stop_cause

    def flaky(tail, **kw):
        if "BAD" in tail:
            raise RuntimeError("bad record")
        return real(tail, **kw)

    monkeypatch.setattr(mod, "stop_cause", flaky)
    _write(home, "s-a", [_prompt("BAD"), _said(), *END], 5000)
    agents = json.dumps([_agent("lane-a", "s-a", "done"), _agent("lane-b", "s-b", "done")])
    assert [w[0] for w in mod.run_pass(agents, now=NOW)] == ["lane-a", "lane-b"]
    assert "cause lane-a: read failed (RuntimeError), so cause=normal" in _log(home)
