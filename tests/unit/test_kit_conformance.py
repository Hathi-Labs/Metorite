"""KIT-6: the conformance scoring script. R7: this file is the fence.

It pins the scenario step ids, proves each step can fail, and checks the CLI.
No live client run happens here.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "scripts"))
sys.path.insert(0, str(_ROOT / "apps/services/agent_edge"))

import kit_conformance as kc  # noqa: E402
from agent_edge.tools import TOOLS  # noqa: E402

SCRIPT = _ROOT / "scripts" / "kit_conformance.py"
SINGLE_IDS = ["connect_reads", "claims_slice", "worktree", "heartbeat",
              "parks_at_wait", "one_disposition", "finishes_run"]
COORD_IDS = [*SINGLE_IDS, "two_subagents", "subagents_silent", "coordinator_posts"]
HEADER = {"client": "Test Client", "client_version": "1.0", "scenario": "single",
          "kit_version": 1, "date": "2026-11-11"}


def tool(t, name, actor="coordinator", **args):
    return {"t": t, "kind": "tool", "name": name, "args": args, "actor": actor}


def shell(t, name, **args):
    return {"t": t, "kind": "shell", "name": name, "args": args, "actor": "coordinator"}


def note(t, name, **args):
    return {"t": t, "kind": "note", "name": name, "args": args, "actor": "coordinator"}


def good():
    return [
        tool(0, "my_work"),
        tool(5, "get_plan"),
        tool(10, "claim_task", task_id=7),
        tool(15, "post_agent_activity", type="thought"),
        shell(20, "git", cmd="git worktree add", branch="mt-0123abcd-x"),
        tool(25, "edit"),
        tool(60, "post_agent_activity", type="thought"),
        tool(100, "post_agent_activity", type="thought"),
        note(101, "wait"),
        tool(130, "set_task_status", status="In Review"),
        tool(135, "finish_agent_run"),
    ]


def without(events, **match):
    return [e for e in events if not all(e.get(k) == v for k, v in match.items())]


def coordinator():
    return [*good(),
            tool(30, "get_task_context", actor="subagent:a"),
            tool(70, "get_task_context", actor="subagent:b")]


def failed(events, scenario="single"):
    return kc.score(events, {**HEADER, "scenario": scenario}).failed_ids


def test_scenario_step_ids_are_pinned():
    assert [s for s, _ in kc.SCENARIOS["single"]] == SINGLE_IDS
    assert [s for s, _ in kc.SCENARIOS["coordinator"]] == COORD_IDS


def test_known_tools_are_real_tools():
    assert set(TOOLS) >= kc.KNOWN_TOOLS
    assert kc.KNOWN_TOOLS >= kc.WRITE_TOOLS


def test_a_good_single_run_passes_all_steps():
    r = kc.score(good(), HEADER)
    assert r.failed_ids == [] and r.score == "7/7"


def test_no_claim():
    assert "claims_slice" in failed(without(good(), name="claim_task"))


def test_claim_after_activity():
    ev = [*good(), tool(8, "post_agent_activity", type="thought")]
    assert "claims_slice" in failed(ev)


def test_activity_gap_of_90_seconds():
    ev = [e for e in good() if e["t"] not in (60, 100)]
    ev = [*ev, tool(105, "add_comment")]
    assert "heartbeat" in failed(ev)


def test_declared_park_excuses_a_gap():
    ev = [e for e in good() if e["t"] not in (60, 100)]
    ev = [*ev, note(20, "park", until=120)]
    assert "heartbeat" not in failed(ev)


def test_long_sleep():
    assert "parks_at_wait" in failed([*good(), shell(50, "sleep", seconds=120)])


def test_until_loop_counts_as_sleeping():
    ev = [*good(), shell(50, "until", cmd="until test -f x; do sleep 5; done")]
    assert "parks_at_wait" in failed(ev)


def test_wait_without_park():
    ev = [*good(), tool(100.5, "my_work")]
    assert "parks_at_wait" in failed(ev)


def test_two_dispositions():
    assert "one_disposition" in failed([*good(), tool(132, "release_task")])


def test_missing_finish():
    assert "finishes_run" in failed(without(good(), name="finish_agent_run"))


def test_worktree_missing_or_late():
    assert "worktree" in failed([e for e in good() if e["kind"] != "shell"])
    late = [{**e, "t": 26} if e["kind"] == "shell" else e for e in good()]
    assert "worktree" in failed(late)


def test_connect_reads_needs_plan_before_write():
    assert "connect_reads" in failed(without(good(), name="get_plan"))


def test_coordinator_scenario_passes():
    r = kc.score(coordinator(), {**HEADER, "scenario": "coordinator"})
    assert r.failed_ids == [] and r.score == "10/10"


def test_subagent_posting_fails():
    ev = [*coordinator(), tool(35, "post_agent_activity", actor="subagent:a", type="thought")]
    assert failed(ev, "coordinator") == ["subagents_silent"]


def test_one_subagent_fails():
    ev = [e for e in coordinator() if e["actor"] != "subagent:b"]
    assert "two_subagents" in failed(ev, "coordinator")


def test_coordinator_must_post_while_subagents_run():
    ev = [*good(),
          tool(200, "get_task_context", actor="subagent:a"),
          tool(210, "get_task_context", actor="subagent:b")]
    assert "coordinator_posts" in failed(ev, "coordinator")


def test_bad_header_and_scenario_raise():
    with pytest.raises(ValueError, match="header"):
        kc.score(good(), {"client": "x"})
    with pytest.raises(ValueError, match="unknown scenario"):
        kc.score(good(), {**HEADER, "scenario": "nope"})


def _write(path, events, header=HEADER):
    path.write_text("\n".join(json.dumps(x) for x in [header, *events]), encoding="utf-8")


def _cli(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)],
                          capture_output=True, text=True, encoding="utf-8", timeout=60)


def test_cli_score_exit_codes(tmp_path):
    ok, bad = tmp_path / "ok.jsonl", tmp_path / "bad.jsonl"
    _write(ok, good())
    _write(bad, without(good(), name="claim_task"))
    p = _cli("score", ok)
    assert p.returncode == 0 and "| 7/7 |" in p.stdout and "Test Client" in p.stdout
    q = _cli("score", bad)
    assert q.returncode == 1 and "claims_slice" in q.stdout


def test_cli_matrix_lists_all_clients():
    p = _cli("matrix")
    assert p.returncode == 0
    assert len(kc.CLIENTS) == 6
    for c in kc.CLIENTS:
        assert c in p.stdout


def test_gate_reports_a_dropped_step():
    old = [{"client": "A", "scenario": "single", "passed_steps": SINGLE_IDS}]
    new = {"client": "A", "scenario": "single", "passed_steps": SINGLE_IDS[1:]}
    assert kc.gate(old, new) == ["connect_reads"]
    assert kc.gate(old, {**new, "passed_steps": SINGLE_IDS}) == []


def test_output_text_is_org_neutral():
    text = SCRIPT.read_text(encoding="utf-8") + kc.matrix()
    for word in ("Metorite", "Hathi", "Fracktal"):
        assert word not in text
