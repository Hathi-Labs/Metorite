"""WS43-E8 of the coding eval: app-builder edits a Workshop app (WS-43h, done-when 3).

Spec ``project-docs/specs/maf_coding_engine.md``, the WS-43h block, and
``evals/coding_engine/app_builder.py``. The hermetic half checks the task,
the checker and the command line. The ``sandbox_docker`` half runs the
scripted task end to end: the REAL executor, the REAL app-builder factory,
a REAL container, and ``ScriptedModel`` in place of the model. No model is
called.

Mutations this suite catches (R7), each run red once by hand:

* ``check_e8`` stops reading the bundle: the stale-bundle test;
* ``check_e8`` accepts a build that ran on the host: the host-build test;
* ``--agent app-builder`` falls through to the Projects runner: the CLI test.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("agent_framework", reason="MAF not installed")

from evals.coding_engine import app_builder as A
from evals.coding_engine import run as R
from evals.coding_engine.checkers import Evidence, Session, ToolCall, snapshot


def _evidence(ws: Path, *, edit: bool = True, build: bool = True,
              in_sandbox: bool = True) -> Evidence:
    A.seed_app(ws)
    before = snapshot(ws)
    if edit:
        (ws / "src" / "main.tsx").write_text(A.MAIN_TSX.replace(A.OLD_TITLE, A.NEW_TITLE),
                                             encoding="utf-8")
    if build:
        (ws / "dist").mkdir(exist_ok=True)
        heading = A.NEW_TITLE if edit else A.OLD_TITLE
        (ws / "dist" / "bundle.html").write_text(f"<script>{heading}</script>",
                                                 encoding="utf-8")
    command = A.BUILD_COMMAND if in_sandbox else "node /host/agent-app-builder/build/build_t2.mjs ."
    session = Session(
        member=A.MEMBER, thread_id="t", prompt=A.PROMPT, answer="done",
        tool_calls=[ToolCall("run_command", json.dumps({"command": command}),
                             "run_command — exit 0 in 1.0 s\nbuild_t2: ok", True)],
        tools_offered=frozenset(A.REQUIRED_TOOLS),
    )
    return Evidence(task_id=A.TASK_ID, workspace=ws, sessions=[session], dataset=None,  # type: ignore[arg-type]
                    files_before=before, files_after=snapshot(ws))


def _failed(ev: Evidence) -> set[str]:
    return {r.rule for r in A.check_e8(ev) if not r.ok}


def test_a_good_run_passes(tmp_path: Path) -> None:
    assert _failed(_evidence(tmp_path / "app")) == set()


def test_an_unedited_source_fails(tmp_path: Path) -> None:
    assert "source_edited" in _failed(_evidence(tmp_path / "app", edit=False))


def test_a_stale_bundle_fails(tmp_path: Path) -> None:
    assert "bundle_built" in _failed(_evidence(tmp_path / "app", build=False))


def test_a_build_that_ran_on_the_host_fails(tmp_path: Path) -> None:
    assert "built_in_sandbox" in _failed(_evidence(tmp_path / "app", in_sandbox=False))


def test_the_task_and_its_script() -> None:
    assert [t.id for t in A.select("all")] == [A.TASK_ID]
    assert [t.id for t in A.select(A.TASK_ID)] == [A.TASK_ID]
    with pytest.raises(ValueError):
        A.select("WS43-E10")
    names = [s[1] for s in A.steps() if s[0] == "tool"]
    assert names == ["file_access_read", "file_access_write", "run_command"]
    assert all(isinstance(s[2], str) for s in A.steps() if s[0] == "tool")


def test_the_command_line_takes_app_builder(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[Any] = []
    monkeypatch.setattr(A, "main", lambda args: seen.append(args) or 0)
    assert R.main(["--agent", "app-builder", "--tasks", "WS43-E8", "--scripted"]) == 0
    assert seen and seen[0].agent == "app-builder" and seen[0].scripted


def test_an_unknown_task_of_app_builder_exits_one(tmp_path: Path) -> None:
    code = R.main(["--agent", "app-builder", "--tasks", "WS43-E9", "--scripted",
                   "--out", str(tmp_path)])
    assert code == R.EXIT_FAIL


# ═════════════════════════ the sandbox_docker half ══════════════════════════

from tests.unit.test_app_builder_engine import t2_vendor  # noqa: E402,F401
from tests.unit.test_coding_sandbox_image import coding_sandbox_image  # noqa: E402,F401
from tests.unit.test_sandbox_exec_hygiene import (  # noqa: E402,F401 — fixtures by name
    bind_mounts_work,
    docker_image,
    real,
)


@pytest.mark.sandbox_docker
def test_docker_the_scripted_task_passes_with_no_model(
    real: dict[str, Any], monkeypatch: pytest.MonkeyPatch, t2_vendor: Path,  # noqa: F811
    tmp_path: Path,
) -> None:
    """Done-when 3 with no model: the scripted WS43-E8 passes through the
    real executor and a real container, and leaves no container behind."""
    from orchestrator import sandbox_broker as sb

    if not real["bind"]:
        pytest.skip("this Docker cannot bind a host dir, so the build proves nothing here")
    monkeypatch.setattr(sb, "_BROKER", real["broker"])
    monkeypatch.setattr(real["settings"], "custom_apps_t2_vendor_dir", str(t2_vendor),
                        raising=False)
    out = tmp_path / "out"
    out.mkdir()
    with A.AppHarness(org="eeeeeeee-0000-4000-8000-0000000000e8", state_root=tmp_path / "state",
                      tier="tier-balanced", scripted=True) as harness:
        records = asyncio.run(A.sweep(harness, 1, 1, out))
    assert [r["status"] for r in records] == ["pass"], records[0].get("failure")
    assert records[0]["mode"] == "scripted" and records[0]["model_calls"] == 4
    assert real["broker"]._live == {}, "a container of the run stayed"
