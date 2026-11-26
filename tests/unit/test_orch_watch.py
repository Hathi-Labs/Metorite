"""CIV-ORCH: the event rules of the box orchestrator's watcher.

scripts/home_server/orch_watch.py. A fake `claude agents --json` and fake log
files serve every case, so no session starts and nothing is woken.
"""

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "home_server" / "orch_watch.py"
NOW = 1_800_000_000.0


@pytest.fixture
def watch(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ORCH_LANES_DIR", str(tmp_path / "lanes"))
    (tmp_path / "lanes").mkdir()
    spec = importlib.util.spec_from_file_location("orch_watch", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["orch_watch"] = mod
    spec.loader.exec_module(mod)
    return mod, tmp_path


def _agent(name, sid, state, started=1):
    return {"name": name, "sessionId": sid, "state": state, "startedAt": started}


def _transcript(home, sid, age_s):
    d = home / ".claude" / "projects" / "-p"
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{sid}.jsonl"
    f.write_text("{}\n", encoding="utf-8")
    os.utime(f, (NOW - age_s, NOW - age_s))


def test_log_lines_back_hold_red_stop_and_requeue_are_events(watch):
    mod, _ = watch
    lines = [
        "BACK 2813 red: Unit tests",
        "HOLD\t2790\tlane-x\twaits on the owner",
        "NOTE\tlane-merge\t16:31 UTC: TAKING 2877",
        "DONE 2875 902d6ff",
        "REQUEUE\t2879\t3800\tlane-sbx\tHIGH, migration 504",
        "RED main 3 tests",
        "STOP the line",
        "RESOLVED 2700 spec-rows",
    ]
    ev = mod.log_events(lines, "merge-queue.log")
    kinds = [(k, s) for k, s, _ in ev]
    assert kinds == [
        ("back", "pr-2813"), ("back", "pr-2790"), ("requeue", "pr-2879"),
        ("back", "main"), ("back", "the"),
    ]


def test_idle_lane_merger_idle_and_stall_rules(watch):
    mod, _ = watch
    agents = [
        _agent("lane-a", "a", "idle"),          # 40 min: idle-lane
        _agent("lane-b", "b", "idle"),          # 10 min: too new
        _agent("lane-merge", "m", "blocked"),   # 20 min: merger-idle (15-min limit)
        _agent("lane-c", "c", "working"),       # 70 min: stall
        _agent("lane-d", "d", "idle"),          # resting
        _agent("lane-orch", "o", "idle"),       # never reports itself
        _agent("lane-x", "x", "idle"),          # not listed
    ]
    ages = {"a": 2400, "b": 600, "m": 1200, "c": 4200, "d": 9000, "o": 9000, "x": 9000}
    lanes = {"lane-a", "lane-b", "lane-merge", "lane-c", "lane-d", "lane-orch"}
    ev = mod.lane_events(agents, ages, lanes, resting={"lane-d"})
    assert sorted((k, s) for k, s, _ in ev) == [
        ("idle-lane", "lane-a"), ("merger-idle", "lane-merge"), ("stall", "lane-c"),
    ]


def test_an_old_session_of_a_lane_does_not_count(watch):
    mod, _ = watch
    agents = [_agent("lane-a", "old", "idle", started=1), _agent("lane-a", "new", "working", started=2)]
    assert mod.lane_events(agents, {"old": 9000, "new": 60}, {"lane-a"}, set()) == []


def test_a_lane_event_repeats_only_after_an_hour_and_resets_when_it_clears(watch):
    mod, _ = watch
    seen = {}
    ev = [("idle-lane", "lane-a", "x")]
    assert mod.due(ev, seen, NOW) == ev
    assert mod.due(ev, seen, NOW + 600) == []
    assert mod.due(ev, seen, NOW + 3600) == ev
    assert mod.due([], seen, NOW + 3700) == [] and seen == {}
    assert mod.due(ev, seen, NOW + 3800) == ev


def test_run_pass_writes_new_events_once_and_starts_at_the_end_of_old_logs(watch):
    mod, home = watch
    lanes = home / "lanes"
    (lanes / "merge-queue.log").write_text("BACK 1 old line\n", encoding="utf-8")
    (lanes / "merge-bot.log").write_text("", encoding="utf-8")
    (lanes / "keeper-lanes.txt").write_text("lane-a\n", encoding="utf-8")
    _transcript(home, "a", 2400)
    agents = json.dumps([_agent("lane-a", "a", "idle")])
    first = mod.run_pass(agents_json=agents, now=NOW)
    assert [(k, s) for k, s, _ in first] == [("idle-lane", "lane-a")], "the old BACK line is not news"
    with open(lanes / "merge-bot.log", "a", encoding="utf-8") as f:
        f.write("BACK 2 premerge: conflict\nhalf a li")
    second = mod.run_pass(agents_json=agents, now=NOW + 300)
    assert [(k, s) for k, s, _ in second] == [("back", "pr-2")]
    log = (lanes / "orch-events.log").read_text(encoding="utf-8").splitlines()
    assert len(log) == 2 and log[1].split("\t")[1:3] == ["back", "pr-2"]
    # The partial line waits for its end.
    with open(lanes / "merge-bot.log", "a", encoding="utf-8") as f:
        f.write("ne\n")
    assert mod.run_pass(agents_json=agents, now=NOW + 600) == []


def test_the_timer_unit_runs_the_watcher_every_five_minutes():
    timer = (ROOT / "scripts" / "home_server" / "metorite-orch-watch.timer").read_text(encoding="utf-8")
    service = (ROOT / "scripts" / "home_server" / "metorite-orch-watch.service").read_text(encoding="utf-8")
    assert "OnUnitActiveSec=5min" in timer and "WantedBy=timers.target" in timer
    assert "orch_watch.py" in service and "Type=oneshot" in service
