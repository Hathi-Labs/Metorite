"""CIV-ORCH: the orchestrator watch (scripts/home_server/orch_watch.py, task 4058).

Hermetic: a temp HOME, fake `claude agents --json` data and a fake child check.
No session starts, and no file under the real ~/lanes is read.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DIR = ROOT / "scripts" / "home_server"
NOW = 1_800_000_000.0


def _load(name):
    spec = importlib.util.spec_from_file_location(name, DIR / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def ow(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KEEPER_LANES_DIR", str(tmp_path / "lanes"))
    monkeypatch.setenv("ORCH_STATE_DIR", str(tmp_path / "state"))
    _load("lane_keeper")
    mod = _load("orch_watch")
    (tmp_path / "lanes").mkdir()
    (tmp_path / "lanes" / "keeper-lanes.txt").write_text(
        "lane-a\nlane-b\nlane-merge\nlane-orch\n", encoding="utf-8"
    )
    return mod, tmp_path


def _agent(name, sid, started=1, pid=None):
    a = {"name": name, "sessionId": sid, "state": "done", "startedAt": started, "status": "idle"}
    if pid:
        a["pid"] = pid
    return a


def _transcript(home, sid, age_s, last="turn_duration"):
    d = home / ".claude" / "projects" / "-p"
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{sid}.jsonl"
    rec = {"type": "system", "subtype": last} if last else {"type": "assistant"}
    f.write_text(json.dumps({"type": "user"}) + "\n" + json.dumps(rec) + "\n", encoding="utf-8")
    os.utime(f, (NOW - age_s, NOW - age_s))


def _events(home):
    p = home / "lanes" / "orch-events.log"
    return p.read_text(encoding="utf-8").splitlines() if p.exists() else []


def _no_child(pid):
    return False


def _merge_fresh(home):
    """A lane-merge session with a fresh transcript, so rule (c) stays quiet."""
    _transcript(home, "s-merge", 60, last=None)
    return _agent("lane-merge", "s-merge")


# --- rule (a): an idle lane ---------------------------------------------------


def test_rule_a_idle_over_30_and_active_over_60(ow):
    mod, home = ow
    _transcript(home, "s-a", 31 * 60)  # idle, 31 min
    _transcript(home, "s-b", 45 * 60, last=None)  # active, 45 min: not yet
    agents = [_agent("lane-a", "s-a"), _agent("lane-b", "s-b"), _merge_fresh(home)]
    ev = mod.run_pass(agents, now=NOW, child=_no_child)
    assert [(e[0], e[1]) for e in ev] == [("idle", "lane-a")]
    line = _events(home)[0].split("\t")
    assert line[0] == "EVENT" and line[1] == "idle" and line[2].endswith(" UTC")
    assert line[3] == "lane-a" and line[4].startswith("idle 31 min")
    _transcript(home, "s-b", 61 * 60, last=None)
    ev = mod.run_pass(agents, now=NOW, child=_no_child)
    assert [(e[0], e[1], e[2][:6]) for e in ev] == [("idle", "lane-b", "active")]


def test_rule_a_a_fresh_lane_a_waiting_lane_and_an_unlisted_lane_make_no_event(ow):
    mod, home = ow
    _transcript(home, "s-a", 10 * 60)
    _transcript(home, "s-b", 40 * 60)
    _transcript(home, "s-z", 99 * 60)
    agents = [
        _agent("lane-a", "s-a"),
        _agent("lane-b", "s-b", pid=42),
        _agent("lane-z", "s-z"),
        _merge_fresh(home),
    ]
    ev = mod.run_pass(agents, now=NOW, child=lambda pid: pid == 42)
    assert ev == []


def test_rule_a_the_resting_list_and_lane_orch_make_no_event(ow):
    mod, home = ow
    for sid in ("s-a", "s-b", "s-o"):
        _transcript(home, sid, 99 * 60)
    (home / "lanes" / "orch_resting.txt").write_text(
        "lane-a waits on the owner\ns-bbbbbb1\n", encoding="utf-8"
    )
    agents = [
        _agent("lane-a", "s-a"),
        _agent("lane-b", "s-bbbbbb1-rest"),
        _agent("lane-orch", "s-o"),
        _merge_fresh(home),
    ]
    _transcript(home, "s-bbbbbb1-rest", 99 * 60)
    assert mod.run_pass(agents, now=NOW, child=_no_child) == []


def test_rule_a_no_repeat_while_the_same_and_a_new_event_when_it_changes(ow):
    mod, home = ow
    _transcript(home, "s-a", 40 * 60)
    agents = [_agent("lane-a", "s-a"), _merge_fresh(home)]
    assert len(mod.run_pass(agents, now=NOW, child=_no_child)) == 1
    assert mod.run_pass(agents, now=NOW + 300, child=_no_child) == []
    assert mod.run_pass(agents, now=NOW + 600, child=_no_child) == []
    # The lane works again: the condition clears, and no event is written.
    _transcript(home, "s-a", 0, last=None)
    assert mod.run_pass(agents, now=NOW, child=_no_child) == []
    # It goes idle again with a new transcript time: one new event.
    _transcript(home, "s-a", 35 * 60)
    os.utime(home / ".claude/projects/-p/s-a.jsonl", (NOW - 35 * 60 + 1, NOW - 35 * 60 + 1))
    assert [e[1] for e in mod.run_pass(agents, now=NOW, child=_no_child)] == ["lane-a"]
    assert len(_events(home)) == 2


def test_rule_a_reads_the_newest_session_of_the_lane(ow):
    mod, home = ow
    _transcript(home, "s-old", 99 * 60)
    _transcript(home, "s-new", 5 * 60)
    agents = [_agent("lane-a", "s-old", 1), _agent("lane-a", "s-new", 2), _merge_fresh(home)]
    assert mod.run_pass(agents, now=NOW, child=_no_child) == []


# --- rule (b): a new BACK, HOLD or RED line ----------------------------------


def _queue(home, text):
    with open(home / "lanes" / "merge-queue.log", "a", encoding="utf-8") as f:
        f.write(text)


def test_rule_b_old_lines_make_no_event_and_new_lines_make_one_each(ow):
    mod, home = ow
    agents = [_merge_fresh(home)]
    _queue(home, "BACK 100 old reason\n")
    assert mod.run_pass(agents, now=NOW, child=_no_child) == []
    _queue(home, "BACK 2881 a red check: test_x\nNOTE\tlane-x\tsome note\nHOLD 2882 mode off\n")
    _queue(home, "RED 2883 main red\nREDUCED nothing\n")
    ev = mod.run_pass(agents, now=NOW, child=_no_child)
    assert [(e[0], e[1]) for e in ev] == [("back", "2881"), ("hold", "2882"), ("red", "2883")]
    assert _events(home)[0].split("\t")[4] == "a red check: test_x"
    # No repeat: the position holds.
    assert mod.run_pass(agents, now=NOW + 300, child=_no_child) == []


def test_rule_b_a_half_written_line_waits_and_a_token_never_reaches_the_log(ow):
    mod, home = ow
    agents = [_merge_fresh(home)]
    _queue(home, "")
    mod.run_pass(agents, now=NOW, child=_no_child)
    _queue(home, "BACK 7 push failed with ghp_abcdefSECRET123")
    assert mod.run_pass(agents, now=NOW, child=_no_child) == []
    _queue(home, " again\n")
    ev = mod.run_pass(agents, now=NOW, child=_no_child)
    assert len(ev) == 1
    text = (home / "lanes" / "orch-events.log").read_text(encoding="utf-8")
    assert "SECRET" not in text and "<redacted>" in text


def test_rule_b_a_log_that_shrank_is_read_from_its_start(ow):
    mod, home = ow
    agents = [_merge_fresh(home)]
    _queue(home, "NOTE a\nNOTE b\nNOTE c\n")
    mod.run_pass(agents, now=NOW, child=_no_child)
    (home / "lanes" / "merge-queue.log").write_text("HOLD 9 x\n", encoding="utf-8")
    assert [e[1] for e in mod.run_pass(agents, now=NOW, child=_no_child)] == ["9"]


# --- rule (c): a silent lane-merge ------------------------------------------


def test_rule_c_merger_silent_after_90_minutes_once_then_again_after_activity(ow):
    mod, home = ow
    _transcript(home, "s-merge", 200 * 60, last=None)
    (home / "lanes" / "keeper-lanes.txt").write_text("lane-a\n", encoding="utf-8")
    agents = [_agent("lane-merge", "s-merge")]
    _queue(home, "")
    assert mod.run_pass(agents, now=NOW, child=_no_child) == []  # the clock starts
    assert mod.run_pass(agents, now=NOW + 89 * 60, child=_no_child) == []
    ev = mod.run_pass(agents, now=NOW + 91 * 60, child=_no_child)
    assert [(e[0], e[1]) for e in ev] == [("merger-silent", "lane-merge")]
    assert mod.run_pass(agents, now=NOW + 120 * 60, child=_no_child) == []
    # A NOTE line of lane-merge clears it. 90 minutes later: a new event.
    _queue(home, "NOTE\tlane-merge\t10:00 UTC: queue empty\n")
    assert mod.run_pass(agents, now=NOW + 125 * 60, child=_no_child) == []
    assert mod.run_pass(agents, now=NOW + 200 * 60, child=_no_child) == []
    ev = mod.run_pass(agents, now=NOW + 216 * 60, child=_no_child)
    assert [e[0] for e in ev] == ["merger-silent"]


def test_rule_c_a_lane_merge_transcript_write_keeps_it_quiet(ow):
    mod, home = ow
    agents = [_agent("lane-merge", "s-merge")]
    _transcript(home, "s-merge", 0, last=None)
    mod.run_pass(agents, now=NOW, child=_no_child)
    later = NOW + 100 * 60
    f = home / ".claude/projects/-p/s-merge.jsonl"
    os.utime(f, (later - 60, later - 60))
    assert mod.run_pass(agents, now=later, child=_no_child) == []


def test_a_failed_agents_list_keeps_the_conditions(ow, monkeypatch):
    mod, home = ow
    _transcript(home, "s-a", 40 * 60)
    agents = [_agent("lane-a", "s-a"), _merge_fresh(home)]
    assert len(mod.run_pass(agents, now=NOW, child=_no_child)) == 1
    monkeypatch.setattr(mod, "read_agents", lambda: None)
    assert mod.run_pass(None, now=NOW + 300, child=_no_child) == []
    # The list comes back: lane-a is still idle in the same way, so no repeat.
    assert mod.run_pass(agents, now=NOW + 600, child=_no_child) == []


# --- the units ------------------------------------------------------------------


def test_the_timer_runs_every_5_minutes_and_ships_disabled():
    timer = (DIR / "metorite-orch-watch.timer").read_text(encoding="utf-8")
    assert "OnUnitActiveSec=5min" in timer and "DISABLED" in timer
    svc = (DIR / "metorite-orch-watch.service").read_text(encoding="utf-8")
    assert "Type=oneshot" in svc and "orch_watch.py" in svc
    assert "WorkingDirectory=%h/Metorite" in svc
    sh = (DIR / "orch_watch.sh").read_text(encoding="utf-8")
    install = sh.split("install_files() {", 1)[1].split("\n}", 1)[0]
    assert "systemctl --user enable" not in install and "--now" not in install
    assert "lane_keeper.py" in install  # orch_watch.py imports its readers


def test_the_last_record_reader(ow, tmp_path):
    mod, _ = ow
    f = tmp_path / "t.jsonl"
    f.write_text('{"type":"user"}\n{"type":"system","subtype":"stop_hook_summary"}\n\n')
    assert mod.last_record(f) == "stop_hook_summary"
    f.write_text("not json\n")
    assert mod.last_record(f) == ""
