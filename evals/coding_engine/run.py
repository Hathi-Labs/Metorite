"""The runner of the light eval: ``python -m evals.coding_engine.run`` (WS-43v).

Spec: ``project-docs/specs/maf_coding_engine.md``, the WS-43v slice and §16.
The README of this folder says how to run the sweep on a local stack.

It drives the REAL ``orchestrator.executor.run_agent_stream`` with the REAL
projects-assistant factory (``apps/agents/agent-projects/agents.py``), once
for each session of each task, ``--repeat`` times (3 by default). Then the
checker of the task (:mod:`evals.coding_engine.checkers`) decides pass or
fail from the files and the text. Each run writes one JSON file.

What the runner replaces, and nothing more:

* The loader. It imports ``agents.py`` by file path, as the Dynamic Agent
  Loader does, so the run builds the agent that the factory builds.
* The Projects API. ``skill_projects.client.gateway_url`` points at
  :mod:`evals.coding_engine.stub_api`, which serves the fixture. The tools,
  the manifest and the acting-member header are the real ones.
* The state root. ``agents_clone_dir`` is a dir of this sweep, so the eval
  never touches the files of the local stack.
* The scope. ``maf_coding_scope`` is ``projects:<test org>`` in THIS process.
* With ``--scripted`` only: the model. ``ScriptedModel`` replays a known-good
  sequence (:mod:`evals.coding_engine.scripted`) under the real client.

Two checks come first (:mod:`evals.coding_engine.preflight`). With no
sandbox tools, each task is SKIPPED, never passed. With no Router on the
local stack, the sweep is NO-GO.

Exit codes: 0 every task passed, 1 a task failed, 2 NO-GO, 3 a task was
skipped and none failed.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evals.coding_engine import checkers, preflight, scripted, stub_api, tasks
from evals.coding_engine import dataset as ds_mod
from evals.coding_engine.checkers import Evidence, Session, ToolCall
from evals.coding_engine.dataset import Dataset
from evals.coding_engine.tasks import TaskSpec

REPO_ROOT = Path(__file__).resolve().parents[2]
AGENT = "projects-assistant"
AGENT_DIR = "apps/agents/agent-projects"
DEFAULT_REPEAT = 3
SESSION_TIMEOUT_S = 900.0
ANSWER_CAP = 6000

EXIT_PASS, EXIT_FAIL, EXIT_NO_GO, EXIT_SKIPPED = 0, 1, 2, 3


# ── the model transport ─────────────────────────────────────────────────────


@dataclass
class ModelTap:
    """What the agent's client sent and what usage the Router reported."""

    bodies: list[dict[str, Any]] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    usage_seen: bool = False

    def see_line(self, raw: bytes) -> None:
        line = raw.strip()
        if line.startswith(b"data:"):
            line = line[5:].strip()
        if not line.startswith(b"{"):
            return
        try:
            usage = json.loads(line).get("usage")
        except (ValueError, AttributeError):
            return
        if isinstance(usage, dict):
            self.usage_seen = True
            self.prompt_tokens += int(usage.get("prompt_tokens") or 0)
            self.completion_tokens += int(usage.get("completion_tokens") or 0)
            self.total_tokens += int(usage.get("total_tokens") or 0)

    def tokens(self) -> dict[str, int] | None:
        if not self.usage_seen:
            return None
        return {"prompt": self.prompt_tokens, "completion": self.completion_tokens,
                "total": self.total_tokens}


def _recording_transport(inner: Any, tap: ModelTap) -> Any:
    """A pass-through transport that records each body and reads the usage."""
    import httpx

    class _Stream(httpx.AsyncByteStream):
        def __init__(self, source: Any) -> None:
            self._source = source

        async def __aiter__(self) -> Any:
            pending = b""
            async for chunk in self._source:
                pending += chunk
                *done, pending = pending.split(b"\n")
                for line in done:
                    tap.see_line(line)
                yield chunk
            if pending:
                tap.see_line(pending)

        async def aclose(self) -> None:
            await self._source.aclose()

    class _Recording(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: Any) -> Any:
            with contextlib.suppress(ValueError):
                tap.bodies.append(json.loads(await request.aread() or b"{}"))
            resp = await inner.handle_async_request(request)
            return httpx.Response(
                status_code=resp.status_code, headers=resp.headers,
                stream=_Stream(resp.stream), extensions=resp.extensions, request=request,
            )

        async def aclose(self) -> None:
            await inner.aclose()

    return _Recording()


def _scripted_model(steps: list[tuple[Any, ...]]) -> Any:
    """``ScriptedModel`` of ``tests/unit/_native_maf_harness.py`` over *steps*."""
    from tests.unit._native_maf_harness import ScriptedModel, text_turn, tool_turn

    turns = []
    for i, step in enumerate(steps):
        if step[0] == "tool":
            turns.append(tool_turn(step[1], step[2], call_id=f"call_{i + 1}"))
        else:
            turns.append(text_turn(step[1]))
    return ScriptedModel(turns)


# ── the harness: patches for one sweep ──────────────────────────────────────


class _Patches:
    def __init__(self) -> None:
        self._undo: list[tuple[Any, str, Any]] = []

    def set(self, target: Any, name: str, value: Any) -> None:
        self._undo.append((target, name, getattr(target, name)))
        setattr(target, name, value)

    def undo(self) -> None:
        while self._undo:
            target, name, old = self._undo.pop()
            setattr(target, name, old)


@dataclass
class Harness:
    """One sweep: the dataset, the stub, the state root and the patches."""

    dataset: Dataset
    org: str
    state_root: Path
    tier: str
    scripted: bool
    clean_blob_store: bool = True
    stub: stub_api.RunningStub | None = None
    _patches: _Patches = field(default_factory=_Patches)
    _module: Any = None
    #: Set before each session: how the factory's client reaches a model.
    _wire: Any = None

    def __enter__(self) -> Harness:
        import gateway.routes.agent as routes_agent
        import skill_projects.client as projects_client
        from acb_common import get_settings
        from orchestrator import executor

        from tests.unit._native_maf_harness import load_agent_module

        self.stub = stub_api.serve(self.dataset)
        settings = get_settings()
        self._patches.set(settings, "agents_clone_dir", str(self.state_root))
        if hasattr(settings, "maf_coding_scope"):
            self._patches.set(settings, "maf_coding_scope", f"projects:{self.org}")
        url = self.stub.url
        self._patches.set(projects_client, "gateway_url", lambda: url)
        self._module = load_agent_module(AGENT_DIR)
        self._patches.set(executor, "load_agent", lambda *a, **k: _LoadedCtx(self))
        self._patches.set(executor, "build_integrations", lambda *a, **k: ({}, {}))
        self._patches.set(routes_agent, "_load_dynamic_agents", lambda: [])
        return self

    def __exit__(self, *exc: Any) -> None:
        self._patches.undo()
        if self.stub is not None:
            self.stub.close()

    def build_agents(self) -> list[Any]:
        """The real factory, with only the wire under its client set for this session."""
        agents = self._module.build_agents()
        oc = agents[0].client.client  # the AsyncOpenAI under the MAF client
        self._wire(oc)
        return list(agents)

    # ── paths ───────────────────────────────────────────────────────────────

    def workspace(self) -> Path:
        from acb_skills.agent_paths import agent_state_dir, tenant_instance

        return agent_state_dir(AGENT, tenant_instance(self.org))

    def outputs_rel(self, thread_id: str) -> str:
        from acb_skills import agent_paths

        helper = getattr(agent_paths, "thread_outputs_rel", None)
        return helper(thread_id) if helper else f"outputs/{agent_paths.instance_slug(thread_id)}"

    def run_data_dir(self, thread_id: str) -> Path:
        from acb_skills import agent_paths

        helper = getattr(agent_paths, "run_data_rel", None)
        if helper:
            rel = helper(self.org, thread_id)
        else:
            slug = agent_paths.instance_slug
            rel = f".run-data/{slug(self.org)}/{slug(thread_id)}"
        return agent_paths.state_root() / rel


class _LoadedCtx:
    """What ``executor.load_agent`` returns: a context of the loaded agent."""

    def __init__(self, harness: Harness) -> None:
        self._harness = harness
        self.agent_dir = REPO_ROOT / AGENT_DIR
        self.agent_name = AGENT
        self.config = json.loads((self.agent_dir / "config.json").read_text(encoding="utf-8"))

    def __enter__(self) -> _LoadedCtx:
        return self

    def __exit__(self, *_a: Any) -> bool:
        return False

    def build_agents(self) -> list[Any]:
        return self._harness.build_agents()


# ── one session ─────────────────────────────────────────────────────────────


@dataclass
class _Stream:
    session: Session
    calls: dict[str, dict[str, Any]] = field(default_factory=dict)
    texts: list[str] = field(default_factory=list)
    declined: int = 0


def _resolve_card(value: dict[str, Any], answer: dict[str, Any]) -> bool:
    """Answer a parked card, as a member would. The eval approves nothing."""
    from orchestrator import executor

    fut = executor._pending_user_input.get(str(value.get("request_id") or ""))
    if fut is None or fut.done():
        return False
    fut.set_result(answer)
    return True


def _take(stream: _Stream, event: dict[str, Any]) -> None:
    kind = event.get("type")
    if kind == "TOOL_CALL_START":
        stream.calls[str(event.get("toolCallId"))] = {
            "name": event.get("toolCallName") or "", "args": event.get("args") or "",
        }
    elif kind == "TOOL_CALL_ARGS":
        call = stream.calls.setdefault(str(event.get("toolCallId")), {"name": "", "args": ""})
        call["deltas"] = call.get("deltas", "") + str(event.get("delta") or "")
    elif kind == "TOOL_CALL_RESULT":
        call = stream.calls.setdefault(str(event.get("toolCallId")), {"name": "", "args": ""})
        call["result"] = str(event.get("content") or "")
        call["ok"] = event.get("success")
    elif kind == "TEXT_MESSAGE_START":
        stream.texts.append("")
    elif kind == "TEXT_MESSAGE_CONTENT":
        if not stream.texts:
            stream.texts.append("")
        stream.texts[-1] += str(event.get("delta") or "")
    elif kind == "RUN_ERROR":
        stream.session.error = str(event.get("message") or event.get("error") or event)[:500]
    elif kind == "CUSTOM":
        _take_custom(stream, event)


def _take_custom(stream: _Stream, event: dict[str, Any]) -> None:
    name, value = event.get("name"), event.get("value") or {}
    if not isinstance(value, dict):
        return
    if name == "artifact_created" and value.get("path"):
        stream.session.artifacts.append(str(value["path"]))
    elif (name == "confirmation_requested" and _resolve_card(value, {"answer": "REJECT"})) or (name == "elicitation_requested" and _resolve_card(
        value, {"answer": "No answer: this is an unattended eval. Go on without it."},
    )):
        stream.declined += 1


def _finish(stream: _Stream) -> None:
    s = stream.session
    s.answer = "\n".join(t for t in stream.texts if t.strip())
    for call in stream.calls.values():
        args = call.get("args") or call.get("deltas") or ""
        s.tool_calls.append(ToolCall(
            name=str(call.get("name") or ""), args=str(args),
            result=str(call.get("result") or ""), ok=call.get("ok"),
        ))


async def _drive(harness: Harness, session: Session) -> _Stream:
    from orchestrator import executor

    from tests.unit._native_maf_harness import parse_frames

    stream = _Stream(session)
    gen = executor.run_agent_stream(
        AGENT, {"message": session.prompt, "user_email": session.member, "source": "eval"},
        run_id=str(uuid.uuid4()), thread_id=session.thread_id, model=harness.tier,
        organization_id=harness.org, session_user=session.member,
    )
    async for line in gen:
        for event in parse_frames([line]):
            _take(stream, event)
    _finish(stream)
    return stream


def _set_wire(harness: Harness, tap: ModelTap, steps: list[tuple[Any, ...]] | None) -> Any:
    import httpx

    model = _scripted_model(steps) if steps is not None else None

    def wire(oc: Any) -> None:
        if model is not None:
            model.bodies = tap.bodies  # one list: the tap reads what the script saw
            oc._client = httpx.AsyncClient(
                transport=httpx.MockTransport(model), event_hooks=oc._client.event_hooks,
            )
        else:
            oc._client._transport = _recording_transport(oc._client._transport, tap)

    harness._wire = wire
    return model


def _reset_run_state() -> None:
    from acb_skills.write_artifact import bind_artifact_context
    from orchestrator import executor

    bind_artifact_context()
    executor._RUN_QUEUES.clear()
    executor._pending_user_input.clear()


async def run_session(
    harness: Harness, spec: tasks.SessionSpec, steps: list[tuple[Any, ...]] | None,
) -> tuple[Session, ModelTap, float, int]:
    """One session: a new thread, one prompt, the real executor."""
    thread_id = str(uuid.uuid4())
    session = Session(
        member=spec.member, thread_id=thread_id, prompt=spec.prompt,
        outputs_rel=harness.outputs_rel(thread_id), run_data_dir=harness.run_data_dir(thread_id),
    )
    tap = ModelTap()
    _set_wire(harness, tap, steps)
    started = time.monotonic()
    declined = 0
    try:
        stream = await asyncio.wait_for(_drive(harness, session), SESSION_TIMEOUT_S)
        declined = stream.declined
    except TimeoutError:
        session.error = f"the session ran past {SESSION_TIMEOUT_S:.0f} s"
    finally:
        _reset_run_state()
    wall = time.monotonic() - started
    if tap.bodies:
        session.tools_offered = preflight.tool_names(tap.bodies[0])
    return session, tap, wall, declined


# ── one run of one task ─────────────────────────────────────────────────────


async def _clear_blob_store(org: str) -> None:
    from acb_skills.agent_paths import tenant_instance

    try:
        from acb_memory import blob_store
    except ImportError:
        return
    instance = tenant_instance(org)
    for meta in await blob_store.list_files(AGENT, instance=instance, organization_id=org):
        await blob_store.delete_file(
            AGENT, meta.path, instance=instance, organization_id=org, actor="eval",
        )


async def reset_workspace(harness: Harness, spec: TaskSpec) -> Path:
    """An empty working dir of the test org, with the task's inputs in place."""
    from acb_skills.agent_paths import ensure_state_dir, tenant_instance

    if harness.clean_blob_store:
        await _clear_blob_store(harness.org)
    workspace = harness.workspace()
    shutil.rmtree(workspace, ignore_errors=True)
    workspace = ensure_state_dir(AGENT, tenant_instance(harness.org))
    for rel, source in spec.inputs.items():
        target = workspace / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    return workspace


def _skipped(reason: str) -> dict[str, Any]:
    return {"status": "skipped", "failure": reason, "rules": []}


async def run_task(
    harness: Harness, spec: TaskSpec, run: int, *, check_sandbox: bool = True,
) -> dict[str, Any]:
    """One run of *spec*. Returns the record that becomes its JSON file.

    ``check_sandbox=False`` turns off BOTH tool checks, the preflight and the
    tools that the model was offered. It is no CLI flag: a test sets it to
    see a checker judge a real run that had no sandbox tools.
    """
    started = time.monotonic()
    head = _head(harness, spec, run)
    if check_sandbox:
        gate = preflight.sandbox_tools(harness.org)
        if not gate.ok:
            return {**head, **_skipped(gate.reason), "wall_s": 0.0}
    workspace = await reset_workspace(harness, spec)
    before = checkers.snapshot(workspace)
    sessions, taps, walls, declined = [], [], [], 0
    for index, session_spec in enumerate(spec.sessions):
        steps = scripted.steps_for(spec.id, index, harness.dataset) if harness.scripted else None
        session, tap, wall, n = await run_session(harness, session_spec, steps)
        sessions.append(session)
        taps.append(tap)
        walls.append(wall)
        declined += n
        problem = preflight.tools_offered_problem(session.tools_offered)
        if check_sandbox and index == 0 and problem:
            return {**head, **_skipped(problem), "wall_s": round(time.monotonic() - started, 2),
                    "sessions": [_session_record(session, wall)]}
    evidence = Evidence(
        task_id=spec.id, workspace=workspace, sessions=sessions, dataset=harness.dataset,
        files_before=before, files_after=checkers.snapshot(workspace),
    )
    return {**head, **_verdict(evidence), **_measures(sessions, taps, walls, declined),
            "wall_s": round(time.monotonic() - started, 2)}


def _verdict(evidence: Evidence) -> dict[str, Any]:
    errors = [s.error for s in evidence.sessions if s.error]
    try:
        rules = checkers.check(evidence)
    except Exception as exc:
        return {"status": "error", "failure": f"checker raised {type(exc).__name__}: {exc}",
                "rules": []}
    ok = checkers.passed(rules) and not errors
    failure = checkers.first_failure(rules) or (f"session error: {errors[0]}" if errors else None)
    return {"status": "pass" if ok else "fail", "failure": None if ok else failure,
            "rules": [r.as_dict() for r in rules]}


def _measures(
    sessions: list[Session], taps: list[ModelTap], walls: list[float], declined: int,
) -> dict[str, Any]:
    calls = [c for s in sessions for c in s.tool_calls]
    tokens = [t.tokens() for t in taps]
    seen = [t for t in tokens if t is not None]
    return {
        "tool_calls": len(calls),
        "failed_tool_calls": sum(1 for c in calls if c.ok is False),
        "model_calls": sum(len(t.bodies) for t in taps),
        "tokens": None if not seen else {
            k: sum(t[k] for t in seen) for k in ("prompt", "completion", "total")
        },
        "cards_declined": declined,
        "approvals": 0,
        "sessions": [_session_record(s, w) for s, w in zip(sessions, walls, strict=True)],
    }


def _session_record(session: Session, wall: float) -> dict[str, Any]:
    return {
        "member": session.member, "thread_id": session.thread_id, "prompt": session.prompt,
        "wall_s": round(wall, 2), "error": session.error, "artifacts": session.artifacts,
        "outputs_rel": session.outputs_rel,
        "tool_calls": [{"name": c.name, "ok": c.ok, "args": c.args[:400],
                        "result": c.result[:400]} for c in session.tool_calls],
        "answer": session.answer[:ANSWER_CAP],
    }


def _head(harness: Harness, spec: TaskSpec, run: int) -> dict[str, Any]:
    return {
        "task": spec.id, "title": spec.title, "short_prompt": spec.short, "run": run,
        "engine": "maf", "agent": AGENT, "tier": harness.tier,
        "mode": "scripted" if harness.scripted else "router",
        "sha": git_sha(), "date": datetime.now(UTC).isoformat(timespec="seconds"),
        "organization_id": harness.org,
    }


def git_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"], cwd=REPO_ROOT, capture_output=True,
            text=True, encoding="utf-8", timeout=10, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    return out.stdout.strip() or "unknown"


# ── the sweep ───────────────────────────────────────────────────────────────


def exit_code(records: list[dict[str, Any]]) -> int:
    statuses = {r["status"] for r in records}
    if statuses & {"fail", "error"}:
        return EXIT_FAIL
    if "skipped" in statuses:
        return EXIT_SKIPPED
    return EXIT_PASS


def summary_lines(records: list[dict[str, Any]]) -> list[str]:
    """One line for each task: ``WS43-E10  3/3 pass``, and the first failure."""
    by_task: dict[str, list[dict[str, Any]]] = {}
    for r in records:
        by_task.setdefault(r["task"], []).append(r)
    lines = []
    for task_id, runs in by_task.items():
        passes = sum(1 for r in runs if r["status"] == "pass")
        kinds = sorted({r["status"] for r in runs})
        line = f"{task_id}  {passes}/{len(runs)} pass  ({', '.join(kinds)})"
        failure = next((r["failure"] for r in runs if r.get("failure")), None)
        if failure:
            line += f"  first reason: {failure[:160]}"
        lines.append(line)
    return lines


async def sweep(
    harness: Harness, chosen: list[TaskSpec], repeat: int, out: Path,
    *, check_sandbox: bool = True,
) -> list[dict[str, Any]]:
    """Every run of every chosen task, in ONE event loop, as the gateway runs them."""
    out.mkdir(parents=True, exist_ok=True)
    records = []
    for spec in chosen:
        for run in range(1, repeat + 1):
            record = await run_task(harness, spec, run, check_sandbox=check_sandbox)
            record["repeat"] = repeat
            (out / f"{spec.id}-run{run}.json").write_text(
                json.dumps(record, indent=2), encoding="utf-8",
            )
            records.append(record)
            print(f"{spec.id} run {run}/{repeat}: {record['status']}"
                  + (f": {record['failure']}" if record.get("failure") else ""), flush=True)
    return records


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m evals.coding_engine.run", description=__doc__)
    p.add_argument("--engine", choices=["maf"], default="maf")
    p.add_argument("--agent", choices=[AGENT], default=AGENT)
    p.add_argument("--tier", default="tier-balanced", help="the Router tier of the sweep")
    p.add_argument("--tasks", default="all", help="all, a comma list, or WS43-E10..WS43-E17")
    p.add_argument("--repeat", "--runs", dest="repeat", type=int, default=DEFAULT_REPEAT)
    p.add_argument("--org", default="", help="the test organization (default: the fixture's)")
    p.add_argument("--out", default="", help="the results dir (default: a new one under results/)")
    p.add_argument("--scripted", action="store_true",
                   help="replay the known-good sequences with ScriptedModel, and call no model")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        chosen = tasks.select(args.tasks)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FAIL
    data = ds_mod.load()
    org = args.org or data.organization_id
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out = Path(args.out) if args.out else Path(__file__).parent / "results" / stamp
    out.mkdir(parents=True, exist_ok=True)
    if not args.scripted:
        gate = _router_gate(args.tier, tasks.MEMBER)
        if not gate.ok:
            print(gate.reason, file=sys.stderr)
            (out / "NO-GO.json").write_text(json.dumps({"no_go": gate.reason}), encoding="utf-8")
            return EXIT_NO_GO
    # The state root stays under the results, so a failed run can be read after.
    state = Path(tempfile.mkdtemp(prefix="state-", dir=out))
    with Harness(
        dataset=data, org=org, state_root=state, tier=args.tier, scripted=args.scripted,
    ) as harness:
        records = asyncio.run(sweep(harness, chosen, max(1, args.repeat), out))
    lines = summary_lines(records)
    (out / "summary.json").write_text(json.dumps({"lines": lines, "sha": git_sha()}, indent=2),
                                      encoding="utf-8")
    print("\n".join(lines))
    print(f"results: {out}")
    return exit_code(records)


def _router_gate(tier: str, member: str) -> preflight.Check:
    from tests.unit._native_maf_harness import load_agent_module

    provider = load_agent_module(AGENT_DIR)._llm_provider()
    return preflight.router(provider["base_url"], provider["api_key"], tier, member)


if __name__ == "__main__":
    raise SystemExit(main())
