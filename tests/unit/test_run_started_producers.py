"""Exactly one place emits ``RUN_STARTED`` (task 1533, bug B3, R7).

The Next translator binds each reader to the run that opened it
(``workbench/control_plane/src/lib/runBinding.ts``) and stops on a second
``RUN_STARTED`` of another run id. If a hand-off, a sub-agent or a resume path
ever emitted one into a live stream, every reply after it would be cut. So the
producer count is pinned here: this test fails before that bug ships.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_SKIP = {"node_modules", ".venv", "__pycache__", "tests"}


def _py_files():
    for top in ("apps", "packages"):
        for p in (ROOT / top).rglob("*.py"):
            if _SKIP.isdisjoint(p.parts):
                yield p


def _producers() -> list[tuple[Path, str]]:
    """Every dict literal with ``"type": "RUN_STARTED"``, and its function."""
    found: list[tuple[Path, str]] = []
    for path in _py_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        if "RUN_STARTED" not in text:
            continue
        tree = ast.parse(text)
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(fn):
                if not isinstance(node, ast.Dict):
                    continue
                for k, v in zip(node.keys, node.values):
                    if (isinstance(k, ast.Constant) and k.value == "type"
                            and isinstance(v, ast.Constant)
                            and v.value == "RUN_STARTED"):
                        found.append((path, fn.name))
    return found


def test_run_started_has_one_producer_in_run_agent_stream():
    names = {(p.name, fn) for p, fn in _producers()}
    # ast.walk also visits nested functions, so one literal can show under an
    # enclosing function too. The literal must belong to run_agent_stream only.
    assert ("executor.py", "run_agent_stream") in names
    others = {n for n in names if n != ("executor.py", "run_agent_stream")}
    assert not others, f"another RUN_STARTED producer: {others}"
    literals = 0
    for path in _py_files():
        literals += len(re.findall(
            r"""["']type["']\s*:\s*["']RUN_STARTED["']""",
            path.read_text(encoding="utf-8", errors="replace"),
        ))
    assert literals == 1, f"expected one RUN_STARTED literal, found {literals}"


def test_sub_agent_paths_never_yield_run_started():
    translator = (
        ROOT / "apps/services/orchestrator/orchestrator/event_translator.py"
    ).read_text(encoding="utf-8")
    start = translator.index("def wrap_sub_agent_events")
    nxt = re.search(r"\n(?:async )?def |\nclass ", translator[start + 10:])
    body = translator[start: start + 10 + (nxt.start() if nxt else len(translator))]
    assert "RUN_STARTED" not in body
    assert "RUN_STARTED" not in translator, (
        "the sub-agent translator must not name RUN_STARTED at all"
    )


def test_the_chat_stream_names_its_run_in_a_header():
    src = (ROOT / "apps/services/gateway/gateway/routes/agent.py").read_text(
        encoding="utf-8")
    assert '"X-Run-Id": run_id' in src
