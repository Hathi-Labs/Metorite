"""The gateway fold and the live reducer keep one hand-off rule (WS-67 OA-9b hardening).

The stored chat row is written by ``gateway/chat_fold.py`` at the run end, and
it replaces the checkpoint of ``route.ts``. The live view is
``workbench/control_plane/src/lib/chatStream.ts``. The two MUST stay the same
in meaning (``chat_fold.py`` docstring). The fold trajectory eval is in
``evals/trajectories``, and pr-check does not run that folder. So these two
fences live in ``tests/unit/``, where each PR runs them.

* ``test_four_hand_offs_keep_their_own_steps``: a turn of ``spawn_subagents``,
  a specialist tool, ``delegate_to_agent`` and ``call_agent``. Each row keeps
  its own steps. The spawn row keeps no text.
* ``test_the_hand_off_names_are_the_same_in_both_twins``: the names are READ
  from the body of ``isHandoffTool`` and ``isSpawnTool`` in ``chatStream.ts``
  and compared with ``chat_fold.HANDOFF_TOOLS`` as sets, in both directions.
  A comment in the TS file cannot satisfy it.

Mutations this file catches (each run red, then restored):

* a name leaves the TS function only -> the parity test;
* a name joins ``HANDOFF_TOOLS`` only -> the parity test;
* the fold ignores ``spawn_subagents`` -> ``test_four_hand_offs_keep_their_own_steps``.
"""
from __future__ import annotations

import re
from pathlib import Path

from gateway.chat_fold import HANDOFF_TOOLS, SPAWN_TOOL, fold_run_events

REPO = Path(__file__).resolve().parents[2]
LIVE = REPO / "workbench" / "control_plane" / "src" / "lib" / "chatStream.ts"


def _ev(t: str, ms: int, **kw) -> dict:
    return {"type": t, "_stream_id": f"{ms}-0", **kw}


def _body(source: str, name: str) -> str:
    """The text between the braces of ``export function <name>``, with each
    ``//`` and block comment cut out, so a comment cannot hold a name."""
    start = source.index(f"export function {name}(")
    brace = source.index("{", source.index(")", source.index("): ", start)))
    depth, i = 0, brace
    while True:
        depth += {"{": 1, "}": -1}.get(source[i], 0)
        if depth == 0:
            break
        i += 1
    body = source[brace + 1:i]
    body = re.sub(r"/\*.*?\*/", "", body, flags=re.S)
    return re.sub(r"//[^\n]*", "", body)


def _names_in(body: str) -> set[str]:
    return set(re.findall(r'\.includes\("([a-z_]+)"\)', body))


def test_four_hand_offs_keep_their_own_steps() -> None:
    """The reload view. The live twin is ``takeHandoff`` (``spawnHandoff.test.ts``)."""
    events = [
        _ev("TOOL_CALL_START", 10, toolCallId="sp1", toolCallName="spawn_subagents"),
        _ev("SUB_AGENT_TOOL_CALL_START", 11, agentName="worker", toolCallId="c1", toolCallName="web_search"),
        _ev("SUB_AGENT_TOOL_CALL_RESULT", 12, toolCallId="c1", content="r", success=True),
        _ev("SUB_AGENT_TEXT_DELTA", 13, agentName="worker", delta='{"items":[]}'),
        _ev("TOOL_CALL_END", 14, toolCallId="sp1", result="ok", success=True),
        _ev("TOOL_CALL_START", 20, toolCallId="em1", toolCallName="email_assistant"),
        _ev("SUB_AGENT_TOOL_CALL_START", 21, agentName="email-assistant", toolCallId="e1", toolCallName="draft_reply"),
        _ev("SUB_AGENT_TOOL_CALL_RESULT", 22, toolCallId="e1", content="d", success=True),
        _ev("TOOL_CALL_END", 23, toolCallId="em1", result="ok", success=True),
        _ev("TOOL_CALL_START", 30, toolCallId="d1", toolCallName="delegate_to_agent"),
        _ev("SUB_AGENT_TOOL_CALL_START", 31, agentName="projects-assistant", toolCallId="p1", toolCallName="list_tasks"),
        _ev("SUB_AGENT_TOOL_CALL_RESULT", 32, toolCallId="p1", content="t", success=True),
        _ev("TOOL_CALL_END", 33, toolCallId="d1", result="ok", success=True),
        _ev("TOOL_CALL_START", 40, toolCallId="ca1", toolCallName="call_agent"),
        _ev("SUB_AGENT_TOOL_CALL_START", 41, agentName="task-manager", toolCallId="t1", toolCallName="sql"),
        _ev("SUB_AGENT_TOOL_CALL_RESULT", 42, toolCallId="t1", content="3", success=True),
        _ev("TOOL_CALL_END", 43, toolCallId="ca1", result="ok", success=True),
        _ev("RUN_FINISHED", 50),
    ]
    folded = fold_run_events(events)
    assert folded is not None
    rows = {t["id"]: t for t in folded["tool_events"]}
    want = {
        "sp1": ("worker", ["c1"]),
        "em1": ("email-assistant", ["e1"]),
        "d1": ("projects-assistant", ["p1"]),
        "ca1": ("task-manager", ["t1"]),
    }
    for row_id, (agent, steps) in want.items():
        assert rows[row_id]["subAgentName"] == agent, row_id
        assert [st["id"] for st in rows[row_id]["subAgentTools"]] == steps, row_id
    assert rows["sp1"]["subAgentText"] == ""  # raw child JSON is never text
    assert "worker" not in {rows[i]["subAgentName"] for i in ("em1", "d1", "ca1")}


def test_a_plain_tool_takes_no_steps() -> None:
    events = [
        _ev("TOOL_CALL_START", 10, toolCallId="w1", toolCallName="web_search"),
        _ev("TOOL_CALL_END", 11, toolCallId="w1", result="ok", success=True),
        _ev("RUN_FINISHED", 20),
    ]
    folded = fold_run_events(events)
    assert folded is not None
    assert "subAgentTools" not in folded["tool_events"][0]


def test_the_hand_off_names_are_the_same_in_both_twins() -> None:
    source = LIVE.read_text(encoding="utf-8")
    handoff = _names_in(_body(source, "isHandoffTool"))
    spawn = _names_in(_body(source, "isSpawnTool"))
    assert len(spawn) == 1, "isSpawnTool names one tool"
    live = handoff | spawn  # isHandoffTool calls isSpawnTool
    assert live, "the parse found no names: the TS shape changed"
    assert live == set(HANDOFF_TOOLS), (
        f"live {sorted(live)} and fold {sorted(HANDOFF_TOOLS)} must be the same set")
    assert spawn == {SPAWN_TOOL}
    # The spawn row keeps no text, in both twins.
    assert 'isSpawnTool(toolName) ? "" : acc.text' in source
