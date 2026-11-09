"""WS43-E8: app-builder edits an existing App Workshop app (WS-43h, done-when 3).

Spec ``project-docs/specs/maf_coding_engine.md``, the WS-43h block. Run it
with ``python -m evals.coding_engine.run --agent app-builder --tasks WS43-E8``.

The task: a member asks app-builder to change the heading of an existing T2
(React) app that imports ``@cc/ui``, and to build it. The run passes when the
source holds the new heading, ``node build_t2.mjs`` ran in the sandbox with
exit 0, and ``dist/bundle.html`` holds the new heading.

It drives the REAL ``orchestrator.executor.run_agent_stream`` with the REAL
app-builder factory, with ``app_builder:<test org>`` in the scope. What the
harness replaces, and nothing more:

* The loader. It imports ``agents.py`` by file path, as the Dynamic Agent
  Loader does.
* The state root and the Custom Apps root. Both are dirs of this sweep.
* The chat's app dir. ``executor._session_workspace_override`` gives the dir
  of the seeded app, as the App Workshop binds it to the chat.
* With ``--scripted`` only: the model. ``ScriptedModel`` replays one
  known-good sequence under the real client, and no model is called.

A run with no healthy broker (Docker and a pinned ``SANDBOX_IMAGE``), or with
no T2 vendor cache, is SKIPPED, never passed.
"""
from __future__ import annotations

import asyncio
import json
import shutil
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evals.coding_engine import checkers, preflight
from evals.coding_engine.checkers import Evidence, Rule, Session
from evals.coding_engine.tasks import MEMBER, SessionSpec, TaskSpec

APP_AGENT = "app-builder"
APP_AGENT_DIR = "apps/agents/agent-app-builder"
TASK_ID = "WS43-E8"
APP_SLUG = "team-board"
OLD_TITLE = "Team Board"
NEW_TITLE = "Team Dashboard"
BUILD_SCRIPT = "/opt/app-builder/build/build_t2.mjs"
BUILD_COMMAND = f"node {BUILD_SCRIPT} ."
BUNDLE_MARKER = "<!-- CC_T2_BUNDLE -->"
BUNDLE = "dist/bundle.html"
#: The tools that a sandboxed app-builder run must offer the model.
REQUIRED_TOOLS = frozenset({"run_command", "file_access_write", "file_access_read"})

PROMPT = (
    f"In this app, change the heading from {OLD_TITLE} to {NEW_TITLE}. Then run "
    "the build, and tell me whether it worked."
)

APP_JSON = {
    "name": OLD_TITLE, "icon": "📋", "description": "The open tasks of the team.",
    "entry": BUNDLE, "tier": "T2", "storage": {"tables": []},
}
INDEX_HTML = (
    '<!doctype html>\n<html><head><meta charset="utf-8">\n'
    '<meta name="viewport" content="width=device-width, initial-scale=1"></head>\n'
    f'<body><div id="root"></div>{BUNDLE_MARKER}</body></html>\n'
)
MAIN_TSX = f"""import {{ createRoot }} from "react-dom/client";
import {{ Stat }} from "@cc/ui";

function App() {{
  return (
    <main>
      <h1>{OLD_TITLE}</h1>
      <Stat label="Open tasks" value={{3}} />
    </main>
  );
}}

createRoot(document.getElementById("root")!).render(<App />);
"""

TASK = TaskSpec(
    TASK_ID, "Edit an App Workshop app",
    "Change the heading of an existing Workshop app, then build it",
    (SessionSpec(PROMPT),),
)
TASK_IDS: tuple[str, ...] = (TASK_ID,)


def select(spec: str) -> list[TaskSpec]:
    """``all`` or ``WS43-E8``: the one task of app-builder."""
    parts = {p.strip() for p in (spec or "all").split(",") if p.strip()}
    if parts <= {"all", TASK_ID}:
        return [TASK]
    raise ValueError(f"unknown task for {APP_AGENT}: {sorted(parts - {'all', TASK_ID})}. "
                     f"The tasks are {TASK_IDS}")


def steps() -> list[tuple[Any, ...]]:
    """The known-good sequence of ``--scripted``: read, edit, build, answer.

    A tool step holds its arguments as JSON text, as ``scripted.py`` does.
    """
    return [
        ("tool", "file_access_read", json.dumps({"file_name": "src/main.tsx"})),
        ("tool", "file_access_write", json.dumps({
            "file_name": "src/main.tsx", "content": MAIN_TSX.replace(OLD_TITLE, NEW_TITLE),
            "overwrite": True,
        })),
        ("tool", "run_command", json.dumps({"command": BUILD_COMMAND, "timeout_s": 120})),
        ("text", f"I changed the heading to {NEW_TITLE}, and the build worked."),
    ]


def seed_app(workspace: Path) -> Path:
    """A fresh copy of the seeded Workshop app at *workspace*."""
    shutil.rmtree(workspace, ignore_errors=True)
    (workspace / "src").mkdir(parents=True)
    (workspace / "app.json").write_text(json.dumps(APP_JSON, indent=2) + "\n", encoding="utf-8")
    (workspace / "index.html").write_text(INDEX_HTML, encoding="utf-8")
    (workspace / "src" / "main.tsx").write_text(MAIN_TSX, encoding="utf-8")
    return workspace


# ── the checker ─────────────────────────────────────────────────────────────


def _text(ev: Evidence, rel: str) -> str | None:
    try:
        return ev.text_of(rel)
    except OSError:
        return None


def check_e8(ev: Evidence) -> list[Rule]:
    """The rules of WS43-E8, from the files and the tool calls of the run."""
    rules: list[Rule] = []
    source = _text(ev, "src/main.tsx") or ""
    rules.append(Rule(
        "source_edited", f"<h1>{NEW_TITLE}</h1>" in source and OLD_TITLE not in source,
        "src/main.tsx holds the new heading, and no old one",
    ))
    template = _text(ev, "index.html") or ""
    rules.append(Rule("template_kept", BUNDLE_MARKER in template,
                      "index.html keeps the bundle marker"))
    bundle = _text(ev, BUNDLE)
    rules.append(Rule(
        "bundle_built",
        bundle is not None and NEW_TITLE in bundle and OLD_TITLE not in bundle
        and BUNDLE_MARKER not in bundle and BUNDLE in ev.changed(),
        f"{BUNDLE} was written by this run, with the new heading",
    ))
    calls = [c for s in ev.sessions for c in s.tool_calls]
    builds = [c for c in calls if c.name == "run_command" and BUILD_SCRIPT in c.args]
    rules.append(Rule(
        "built_in_sandbox", any("exit 0" in c.result for c in builds),
        f"run_command ran {BUILD_SCRIPT} in the sandbox with exit 0",
    ))
    offered = set().union(*(s.tools_offered for s in ev.sessions)) if ev.sessions else set()
    rules.append(Rule(
        "maf_tools", offered >= REQUIRED_TOOLS,
        "the model was offered run_command and the file tools of the MAF engine",
    ))
    errors = [s.error for s in ev.sessions if s.error]
    rules.append(Rule("no_session_error", not errors, errors[0] if errors else "none"))
    return rules


# ── the harness ─────────────────────────────────────────────────────────────


@dataclass
class AppHarness:
    """One sweep of app-builder: the roots, the scope and the patches."""

    org: str
    state_root: Path
    tier: str
    scripted: bool
    _patches: Any = None
    _module: Any = None
    _wire: Any = None
    agent_dir: Path | None = None

    def app_dir(self) -> Path:
        return self.state_root / "custom_apps" / APP_SLUG

    def __enter__(self) -> AppHarness:
        import gateway.routes.agent as routes_agent
        from acb_common import get_settings
        from orchestrator import executor

        from evals.coding_engine.run import REPO_ROOT, _Patches
        from tests.unit._native_maf_harness import load_agent_module

        self._patches = _Patches()
        # A real path: the broker refuses an app dir that is not strictly
        # below the resolved Custom Apps root (a Windows short name is not).
        self.state_root = Path(self.state_root).resolve()
        settings = get_settings()
        (self.state_root / "custom_apps").mkdir(parents=True, exist_ok=True)
        self._patches.set(settings, "agents_clone_dir", str(self.state_root))
        self._patches.set(settings, "custom_apps_root", str(self.state_root / "custom_apps"))
        self._patches.set(settings, "maf_coding_scope", f"app_builder:{self.org}")
        self._module = load_agent_module(APP_AGENT_DIR)
        self.agent_dir = self.state_root / "repos" / APP_AGENT
        if not self.agent_dir.is_dir():
            shutil.copytree(REPO_ROOT / APP_AGENT_DIR, self.agent_dir,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        app = self.app_dir()
        self._patches.set(executor, "load_agent", lambda *a, **k: _AppLoaded(self))
        self._patches.set(executor, "build_integrations", lambda *a, **k: ({}, {}))
        self._patches.set(executor, "_session_workspace_override", lambda *a, **k: str(app))
        self._patches.set(routes_agent, "_load_dynamic_agents", lambda: [])
        return self

    def __exit__(self, *exc: Any) -> None:
        self._patches.undo()

    def build_agents(self) -> list[Any]:
        agents = self._module.build_agents()
        client = getattr(agents[0], "client", None)
        oc = getattr(client, "client", None)
        if oc is not None and self._wire is not None:
            self._wire(oc)
        return list(agents)


class _AppLoaded:
    def __init__(self, harness: AppHarness) -> None:
        self.agent_dir = harness.agent_dir
        self.agent_name = APP_AGENT
        self.config = json.loads((self.agent_dir / "config.json").read_text(encoding="utf-8"))
        self._harness = harness

    def __enter__(self) -> _AppLoaded:
        return self

    def __exit__(self, *_a: Any) -> bool:
        return False

    def build_agents(self) -> list[Any]:
        return self._harness.build_agents()


async def sandbox_gate() -> preflight.Check:
    """The broker answers Docker with a pinned image, and the T2 vendor cache exists."""
    await preflight.warm_broker()
    try:
        from gateway.routes.apps._common import t2_vendor_dir
        from orchestrator import sandbox_broker as sb
    except ImportError as exc:
        return preflight.Check(False, f"SKIPPED: the sandbox does not import: {exc}.")
    if not sb.get_broker().healthy():
        return preflight.Check(False, (
            "SKIPPED: the sandbox broker is not healthy. It needs Docker and a "
            "pinned SANDBOX_IMAGE."
        ))
    vendor = Path(t2_vendor_dir())
    if not (vendor / "node_modules" / "esbuild").is_dir():
        return preflight.Check(False, (
            f"SKIPPED: no T2 vendor cache at {vendor}. Set CUSTOM_APPS_T2_VENDOR_DIR."
        ))
    return preflight.Check(True, "the broker is healthy and the vendor cache exists")


@dataclass
class _Result:
    session: Session
    tap: Any
    wall: float
    declined: int = 0
    extra: dict[str, Any] = field(default_factory=dict)


async def _session(harness: AppHarness, steps_: list[tuple[Any, ...]] | None) -> _Result:
    from orchestrator import executor

    from evals.coding_engine import run as R
    from tests.unit._native_maf_harness import parse_frames

    thread_id = str(uuid.uuid4())
    session = Session(member=MEMBER, thread_id=thread_id, prompt=PROMPT)
    tap = R.ModelTap()
    R._set_wire(harness, tap, steps_)  # sets harness._wire
    stream = R._Stream(session)
    started = time.monotonic()
    try:
        async def drive() -> None:
            async for line in executor.run_agent_stream(
                APP_AGENT, {"message": PROMPT, "user_email": MEMBER, "source": "eval"},
                run_id=str(uuid.uuid4()), thread_id=thread_id, model=harness.tier,
                organization_id=harness.org, session_user=MEMBER, shows_cards=True,
            ):
                for event in parse_frames([line]):
                    R._take(stream, event)

        await asyncio.wait_for(drive(), R.SESSION_TIMEOUT_S)
    except TimeoutError:
        session.error = f"the session ran past {R.SESSION_TIMEOUT_S:.0f} s"
    finally:
        R._finish(stream)
        R._reset_run_state()
    if tap.bodies:
        session.tools_offered = preflight.tool_names(tap.bodies[0])
    return _Result(session, tap, time.monotonic() - started, stream.declined)


async def run_app_task(harness: AppHarness, run: int, *, check_sandbox: bool = True) -> dict[str, Any]:
    """One run of WS43-E8. Returns the record that becomes its JSON file."""
    from evals.coding_engine import run as R

    head = {
        "task": TASK_ID, "title": TASK.title, "short_prompt": TASK.short, "run": run,
        "engine": "maf", "agent": APP_AGENT, "tier": harness.tier,
        "mode": "scripted" if harness.scripted else "router",
        "sha": R.git_sha(), "date": datetime.now(UTC).isoformat(timespec="seconds"),
        "organization_id": harness.org,
    }
    if check_sandbox:
        gate = await sandbox_gate()
        if not gate.ok:
            return {**head, "status": "skipped", "failure": gate.reason, "rules": [],
                    "wall_s": 0.0}
    workspace = seed_app(harness.app_dir())
    before = checkers.snapshot(workspace)
    result = await _session(harness, steps() if harness.scripted else None)
    evidence = Evidence(
        task_id=TASK_ID, workspace=workspace, sessions=[result.session], dataset=None,  # type: ignore[arg-type]
        files_before=before, files_after=checkers.snapshot(workspace),
    )
    rules = check_e8(evidence)
    ok = checkers.passed(rules)
    return {
        **head, "status": "pass" if ok else "fail",
        "failure": None if ok else checkers.first_failure(rules),
        "rules": [r.as_dict() for r in rules],
        **R._measures([result.session], [result.tap], [result.wall], result.declined),
        "wall_s": round(result.wall, 2),
    }


async def sweep(
    harness: AppHarness, tasks_: int, repeat: int, out: Path, *, check_sandbox: bool = True,
) -> list[dict[str, Any]]:
    """Every run, in ONE event loop, as the gateway runs them.

    At the end it removes the containers that THIS process started, and no
    other. The gateway does that at a restart, and a CLI run has none.
    """
    records: list[dict[str, Any]] = []
    try:
        for _ in range(tasks_):
            for run in range(1, repeat + 1):
                record = await run_app_task(harness, run, check_sandbox=check_sandbox)
                record["repeat"] = repeat
                (out / f"{TASK_ID}-run{run}.json").write_text(
                    json.dumps(record, indent=2), encoding="utf-8")
                records.append(record)
                print(f"{TASK_ID} run {run}/{repeat}: {record['status']}"
                      + (f": {record['failure']}" if record.get("failure") else ""), flush=True)
    finally:
        await _remove_own_containers()
    return records


async def _remove_own_containers() -> None:
    try:
        from orchestrator import sandbox_broker as sb
    except ImportError:
        return
    broker = sb._BROKER
    if broker is None:
        return
    handles = list(broker._live.values())
    for handle in handles:
        broker._drop(handle)
    await broker._remove_all(handles)
    await broker.settle()
    await broker.stop()


def main(args: Any) -> int:
    """``--agent app-builder``: the sweep of WS43-E8. *args* is the parsed CLI."""
    from evals.coding_engine import run as R

    try:
        chosen = select(args.tasks)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return R.EXIT_FAIL
    org = args.org or "eeeeeeee-0000-4000-8000-0000000000e8"
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out = Path(args.out) if args.out else Path(__file__).parent / "results" / stamp
    out.mkdir(parents=True, exist_ok=True)
    if not args.scripted:
        gate = R._router_gate(args.tier, MEMBER)
        if not gate.ok:
            print(gate.reason, file=sys.stderr)
            (out / "NO-GO.json").write_text(json.dumps({"no_go": gate.reason}), encoding="utf-8")
            return R.EXIT_NO_GO
    state = Path(tempfile.mkdtemp(prefix="state-", dir=out))
    with AppHarness(org=org, state_root=state, tier=args.tier, scripted=args.scripted) as h:
        records = asyncio.run(sweep(h, len(chosen), max(1, args.repeat), out))
    lines = R.summary_lines(records)
    (out / "summary.json").write_text(json.dumps({"lines": lines, "sha": R.git_sha()}, indent=2),
                                      encoding="utf-8")
    print("\n".join(lines))
    print(f"results: {out}")
    return R.exit_code(records)
