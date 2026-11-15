"""KIT-6: the adapter from a Claude Code stream to a conformance transcript. R7: this file is the fence.

Mutations:

* A1. ``tool_name`` keeps the ``mcp__`` prefix: ``test_an_mcp_tool_gets_its_bare_name`` fails.
* A2. A sub-agent call gets the coordinator actor: ``test_a_subagent_call_gets_a_subagent_actor`` fails.
* A3. The worktree branch is dropped: ``test_a_run_with_the_protocol_scores_every_step`` fails.
* A4. ``waiting`` adds no park note: ``test_a_run_with_the_protocol_scores_every_step`` fails.
* A5. A Bash ``sleep`` is mapped to a plain event with no cmd: ``test_a_sleeping_run_fails_the_park_step`` fails.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "scripts"))

import kit_claude_adapter as ad  # noqa: E402
import kit_conformance as kc  # noqa: E402

BRANCH = "mt-3d2a6bbd-kit6-run"


def use(ts, name, parent=None, **inp):
    block = {"type": "tool_use", "id": f"tu_{ts}", "name": name, "input": inp}
    return json.dumps({"type": "assistant", "_ts": ts, "parent_tool_use_id": parent,
                       "message": {"content": [block]}})


def m(name):
    return f"mcp__metorite__{name}"


def good_run():
    return [
        json.dumps({"type": "system", "subtype": "init", "_ts": 99}),
        use(100, m("my_work")),
        use(105, m("get_task_context"), task_id="t1"),
        use(110, m("claim_task"), task_id="t1", expected_version="v"),
        use(112, "Bash", command="bash scripts/worktree.sh new x && git worktree add ../wt -b mt-3d2a6bbd-kit6-run"),
        use(130, "Edit", file_path="/x"),
        use(150, m("post_agent_activity"), run_id="r", type="thought", body="waiting",
            waiting={"on": "ci", "until_minutes": 5}),
        use(400, m("post_agent_activity"), run_id="r", type="thought", body="back"),
        use(420, m("add_comment"), task_id="t1", body="done"),
        use(425, m("set_task_status"), task_id="t1", status="Review"),
        use(430, m("finish_agent_run"), run_id="r", summary="s"),
    ]


def run(lines, scenario="single"):
    rows = ad.convert(lines, "2.1.322", 2, scenario, day="2026-11-12")
    return rows[0], rows[1:]


def test_an_mcp_tool_gets_its_bare_name():
    assert ad.tool_name("mcp__metorite__my_work") == "my_work"
    assert ad.tool_name("mcp__claude_ai_Metorite__my_work") == "my_work"
    assert ad.tool_name("Bash") == "Bash"


def test_the_header_holds_what_the_scorer_needs():
    header, _ = run(good_run())
    assert set(kc.HEADER_KEYS) <= set(header)
    assert header["client"] == "Claude Code" and header["kit_version"] == 2


def test_time_counts_from_the_first_assistant_line():
    _, events = run(good_run())
    assert events[0]["t"] == 0 and events[-1]["t"] == 330


def test_a_run_with_the_protocol_scores_every_step():
    header, events = run(good_run())
    res = kc.score(events, header)
    assert res.failed_ids == [], [s for s in res.steps if not s.passed]
    assert res.score == "7/7"


def test_a_subagent_call_gets_a_subagent_actor():
    lines = [use(1, m("my_work")), use(2, m("post_agent_activity"), parent="toolu_A", type="thought")]
    _, events = run(lines)
    assert [e["actor"] for e in events] == ["coordinator", "subagent:toolu_A"]


def test_a_subagent_write_fails_the_silent_step():
    lines = good_run() + [use(300, "Task"), use(301, m("post_agent_activity"), parent="toolu_A", type="thought"),
                          use(302, m("add_comment"), parent="toolu_B", task_id="t1", body="x")]
    header, events = run(lines, "coordinator")
    res = kc.score(events, header)
    assert "subagents_silent" in res.failed_ids


def test_a_sleeping_run_fails_the_park_step():
    lines = good_run()
    lines.insert(8, use(160, "Bash", command="until test -f /tmp/x; do sleep 10; done"))
    header, events = run(lines)
    assert "parks_at_wait" in kc.score(events, header).failed_ids


def test_a_run_with_no_worktree_branch_fails_that_step():
    lines = [ln for ln in good_run() if "worktree" not in ln]
    header, events = run(lines)
    assert "worktree" in kc.score(events, header).failed_ids


def test_a_worktree_branch_comes_from_the_dash_b_flag():
    _, events = run([use(1, "Bash", command="git worktree add ../w -b mt-0123abcd-x origin/main")])
    assert events[0]["name"] == "worktree" and events[0]["args"]["branch"] == "mt-0123abcd-x"


def test_other_tools_and_lines_are_ignored():
    lines = [use(1, "Read", file_path="/x"), "not json", json.dumps({"type": "result", "_ts": 5})]
    _, events = run(lines)
    assert events == []


def test_the_cli_converts_a_replay(tmp_path):
    import subprocess
    src = tmp_path / "in.jsonl"
    src.write_text("\n".join(good_run()), encoding="utf-8")
    out = subprocess.run(
        [sys.executable, str(_ROOT / "scripts/kit_claude_adapter.py"), "convert", "--client-version", "2.1.322",
         "--kit-version", "2", "--scenario", "single"],
        input=src.read_text(encoding="utf-8"), capture_output=True, text=True, check=True).stdout
    rows = [json.loads(ln) for ln in out.splitlines()]
    assert rows[0]["scenario"] == "single" and len(rows) > 5
