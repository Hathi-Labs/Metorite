"""WS-43v: the known-good sequences are good, in the real coding image.

Spec: ``project-docs/specs/maf_coding_engine.md``, the WS-43v slice and §16.3.

``--scripted`` replays one sequence for each session
(``evals/coding_engine/scripted.py``). Until PR #603 merges, no checkout has
the sandbox tools, so the runner reports SKIPPED and plays no step. This test
proves the sequences now, so the sweep after #603 rests on scripts that
work. It plays each sandbox step of a sequence in the coding image, with the
flags of §7.1 rule 6 and ``--network none``, and the checker of the task
judges what is left.

* A file tool step writes where ``TenantFileStore`` (PR #603) puts it:
  ``outputs/x`` in the thread's own folder, ``.run/x`` in the run data, and
  any other path in the working dir.
* A ``run_command`` step runs in a container with the three mounts of §16.3:
  the working dir at ``/workspace``, the thread's folder at
  ``/workspace/outputs`` and the run data at ``/workspace/.run``.
* At the end of a session the run data goes, as the broker deletes it. The
  real delete is fenced by WS43-F22 (``test_run_data_hygiene.py``, PR #603).

It is NOT the broker. The broker's own mounts and flags are fenced by WS43-F2
and WS43-F22. This test fences the scripts and the checkers against a real
Python, a real matplotlib and a real openpyxl, with no network.

``sandbox-docker.yml`` runs it, and that workflow fails on any skip.
"""
from __future__ import annotations

import json
import os
import shutil
import uuid
from pathlib import Path

import pytest

from evals.coding_engine import checkers as C
from evals.coding_engine import dataset as D
from evals.coding_engine import scripted as S
from evals.coding_engine import tasks as T
from evals.coding_engine.checkers import Evidence, Session, ToolCall
from tests.unit.test_coding_sandbox_image import (  # noqa: F401 — a fixture, used by name
    _RUN_FLAGS,
    _docker,
    coding_sandbox_image,
)

DS = D.load()

#: The tasks whose sequence runs code. WS43-E14 runs none: it refuses.
CODE_TASKS = ["WS43-E10", "WS43-E11", "WS43-E12", "WS43-E13", "WS43-E15", "WS43-E16", "WS43-E17"]


def _uid() -> str:
    getuid = getattr(os, "getuid", None)
    getgid = getattr(os, "getgid", None)
    return f"{getuid()}:{getgid()}" if getuid and getgid else "1000:1000"


def _mount(source: Path, target: str) -> list[str]:
    return ["--mount", f"type=bind,source={source},target={target}"]


class _Place:
    """The host dirs of one session, laid out as §16.3 says."""

    def __init__(self, ws: Path, root: Path, thread_id: str) -> None:
        from acb_skills.agent_paths import instance_slug

        self.ws = ws
        self.outputs_rel = f"outputs/{instance_slug(thread_id)}"
        self.outputs = ws / self.outputs_rel
        self.run_data = root / ".run-data" / instance_slug(thread_id)
        self.outputs.mkdir(parents=True, exist_ok=True)
        self.run_data.mkdir(parents=True, exist_ok=True)

    def host_path(self, tool_path: str) -> Path:
        head, _, rest = tool_path.lstrip("/").partition("/")
        if head == "outputs":
            return self.outputs / rest
        if head == ".run":
            return self.run_data / rest
        return self.ws / tool_path

    def run(self, image: str, command: str) -> tuple[bool, str]:
        result = _docker(
            "run", *_RUN_FLAGS, "--user", _uid(), "--workdir", "/workspace",
            *_mount(self.ws, "/workspace"), *_mount(self.outputs, "/workspace/outputs"),
            *_mount(self.run_data, "/workspace/.run"),
            image, "bash", "-o", "pipefail", "-c", command,
        )
        return result.returncode == 0, f"exit {result.returncode}\n{result.stdout}{result.stderr}"


def _play(image: str, place: _Place, steps: list[tuple], spec: T.SessionSpec,
          thread_id: str) -> Session:
    session = Session(member=spec.member, thread_id=thread_id, prompt=spec.prompt,
                      outputs_rel=place.outputs_rel, run_data_dir=place.run_data)
    before = set(C.snapshot(place.outputs))
    for step in steps:
        if step[0] == "text":
            session.answer = step[1]
            continue
        name, args = step[1], json.loads(step[2])
        ok, result = True, "read by the eval stub"
        if name == "file_access_write":
            target = place.host_path(args["file_name"])
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(args["content"], encoding="utf-8")
            result = f"wrote {args['file_name']}"
        elif name == "run_command":
            ok, result = place.run(image, args["command"])
        session.tool_calls.append(ToolCall(name, step[2], result, ok))
    # The sweep of PR #603 shows each new file of the thread's folder as a card.
    session.artifacts = [f"{place.outputs_rel}/{rel}"
                         for rel in sorted(set(C.snapshot(place.outputs)) - before)]
    shutil.rmtree(place.run_data)  # the broker deletes the run data at the run end
    return session


@pytest.mark.sandbox_docker
@pytest.mark.parametrize("task_id", CODE_TASKS)
def test_the_scripted_sequence_passes_its_checker_in_the_image(
    coding_sandbox_image: str, tmp_path: Path, task_id: str,  # noqa: F811
) -> None:
    spec = T.by_id(task_id)
    ws = tmp_path / "ws"
    ws.mkdir()
    for rel, source in spec.inputs.items():
        (ws / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, ws / rel)
    files_before = C.snapshot(ws)
    sessions = []
    for index, session_spec in enumerate(spec.sessions):
        thread_id = str(uuid.uuid4())
        place = _Place(ws, tmp_path, thread_id)
        steps = S.steps_for(task_id, index, DS)
        sessions.append(_play(coding_sandbox_image, place, steps, session_spec, thread_id))
    runs = [c for s in sessions for c in s.tool_calls if c.name == "run_command"]
    assert runs, "a code task must run code"
    if task_id != "WS43-E15":
        assert all(c.ok for c in runs), [c.result for c in runs if not c.ok]
    evidence = Evidence(task_id=task_id, workspace=ws, sessions=sessions, dataset=DS,
                        files_before=files_before, files_after=C.snapshot(ws))
    rules = C.check(evidence)
    assert C.passed(rules), [r for r in rules if not r.ok and not r.advisory]


@pytest.mark.sandbox_docker
def test_the_median_script_prints_the_fixture_median(
    coding_sandbox_image: str, tmp_path: Path,  # noqa: F811
) -> None:
    """The scripted answer says the median. The script must compute the same one."""
    ws = tmp_path / "ws"
    ws.mkdir()
    thread_id = str(uuid.uuid4())
    session = _play(coding_sandbox_image, _Place(ws, tmp_path, thread_id),
                    S.steps_for("WS43-E11", 0, DS), T.by_id("WS43-E11").sessions[0], thread_id)
    [run] = [c for c in session.tool_calls if c.name == "run_command"]
    assert f"median {D.median_lead_days(DS):.1f} days" in run.result, run.result


@pytest.mark.sandbox_docker
def test_the_fetch_fails_with_no_network(coding_sandbox_image: str, tmp_path: Path) -> None:  # noqa: F811
    """WS43-E15's step tries the web. With ``--network none`` it must fail."""
    ws = tmp_path / "ws"
    ws.mkdir()
    thread_id = str(uuid.uuid4())
    session = _play(coding_sandbox_image, _Place(ws, tmp_path, thread_id),
                    S.steps_for("WS43-E15", 0, DS), T.by_id("WS43-E15").sessions[0], thread_id)
    [run] = [c for c in session.tool_calls if c.name == "run_command"]
    assert run.ok is False, run.result
