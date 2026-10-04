"""The sandbox tools: ``run_command``, the file tools and skills (WS-43d).

Spec ``project-docs/specs/maf_coding_engine.md`` §7.4 (the tools), §7.5 (host
safety), §7.7 (coverage) and §16.3 (the Projects track, D86).

**Who gets them.** Only a run that the sandbox broker covers. Today that is
projects-assistant, for an organization that ``MAF_CODING_SCOPE`` names as
``projects:<org>``. :func:`attach_for_run` is the one way in: the
projects-assistant factory calls it, and it returns a per-run VIEW of the
agent that carries :class:`ProjectsSandboxProvider`. The agent object itself
never changes (``_native_run_context.agent_with_providers``). With an empty
scope it returns the agent unchanged, so nothing about any run changes.

**The boundaries are structural, and no permission mode moves them.**
Production runs ``AGENT_PERMISSION_MODE=audit``, where ``decide()`` logs and
approves everything. So ``decide()`` is called for the audit line only, and
the boundaries are these:

1. The tools exist only when ``covers(agent, org)`` is true for the run's
   own tenant (``executor._current_run_org``, never input, R5). The provider
   checks it again at the start of each turn, so a flip off ends them.
2. A command runs ONLY through ``broker.exec()``, in the container, with no
   network and no key. A broker that refuses or fails is an error, and
   nothing runs on the host (§7.1 rule 14).
3. The file tools refuse ``..``, an absolute path, a hidden root name and a
   link at any depth, through ``acb_skills.safe_open``.
4. The provider adds nothing when the run holds a host shell tool
   (``code_task``, ``run_script``, ``install_dependency``). So a sandbox
   tool never sits beside a host shell (§16.3 condition 3).
5. A covered run does not hold the host floor tools that open the working
   dir with plain path calls and no dir lock (:data:`WITHHELD_HOST_TOOLS`).
   A per-run chat middleware takes them out of each model request, and a
   per-run function middleware refuses a call to one. The file tools do
   their work, with the safe opener and the lock (review P1, fix round 1).
   The host web tools ``web_search`` and ``fetch_page`` are withheld the same
   way (:data:`HOST_NETWORK_TOOLS`). They run on the host with its network,
   so a model that an injection steers could put member data in a URL or a
   query (§16.3, the P3 review). The container has no network, and the run
   holds no web tool. The delegation tools stay: the owner kept them on
   2026-10-03, and an agent that the run calls runs outside the sandbox, with
   its own tools. H-236 binds ``no_egress`` on the covered run itself and on
   each run under it, and the rule fails closed (``acb_skills.egress``). The
   injection seam reuses :data:`HOST_NETWORK_TOOLS` there as a floor. The
   host-tool middleware here and the egress middleware share one pair
   (``acb_skills.tool_guard``).
6. The container sees ``/workspace`` READ-ONLY, except its own output folder
   and its run data. A skill loads, and its script runs, only for the member
   who made it. So no member's code or skill text reaches another member's
   run (review P1, fix round 1).

**What the provider adds to ONE run**, at the start of each turn:

* ``run_command(command, timeout_s)``, :func:`run_command`.
* MAF's eight file tools over :class:`~acb_skills.tenant_file_store.TenantFileStore`.
* MAF's ``SkillsProvider`` over ``agent-data/skills/`` (blob-store durable),
  with no cache, so a skill written in one turn lists on the next. A skill
  script runs in the sandbox, through its ``/workspace`` path.

``request_network_access`` is a stub that answers "network access is off on
this platform" (§7.3, WS-43g). The Projects track never attaches it.

Fences: ``tests/unit/test_run_command_tool.py`` (WS43-F6),
``tests/unit/test_projects_sandbox_tools.py`` (WS43-F21) and
``tests/unit/test_run_data_hygiene.py`` (WS43-F22).
"""
from __future__ import annotations

import copy
import functools
import inspect
import os
import shlex
import time
from pathlib import Path
from typing import Any

from acb_common import get_logger
from agent_framework import (
    DEFAULT_FILE_ACCESS_INSTRUCTIONS,
    ContextProvider,
    FileAccessProvider,
    FileSkillsSource,
    SkillsProvider,
    SkillsSource,
    tool,
)

from acb_skills.addendum import render_run_sections
from acb_skills.tool_guard import RefuseTools, WithholdTools, tool_name
from acb_skills.write_artifact import announce_artifact, artifact_context

_log = get_logger("acb_skills.sandbox_tools")

__all__ = [
    "HOST_NETWORK_TOOLS",
    "PROJECTS_AGENT",
    "WITHHELD_HOST_TOOLS",
    "LockedSkillsSource",
    "ProjectsSandboxProvider",
    "SandboxScriptRunner",
    "attach_for_run",
    "request_network_access",
    "run_command",
]

PROJECTS_AGENT = "projects-assistant"
SOURCE_ID = "metorite-sandbox"
_DEFAULT_TIMEOUT_SECONDS = 60
#: The host shell tools that a sandboxed run may never hold (§16.3 condition 3).
_HOST_SHELL_TOOLS = frozenset({"code_task", "run_script", "install_dependency"})
#: Scripts of a skill live here, and only here (§7.4).
_SKILLS_REL = "agent-data/skills"
#: The host floor tools that a covered run does not hold (review P1, fix
#: round 1). Each opens the working dir with plain path calls and no dir lock,
#: so it could follow a link that a container made, or race an exec. The file
#: tools do the same work with the safe opener and the lock.
_HOST_FILE_TOOLS = frozenset({
    "write_artifact", "share_artifact", "save_note", "recall_notes",
    "get_errors", "run_diagnostics",
})
#: The host web tools that a covered run does not hold (§16.3, the P3
#: review). The core floor gives them to every agent, and they run on the
#: HOST with its network. Member data sits in ``.run/`` and in the model's
#: context, so a URL or a search query could carry it out of the platform.
HOST_NETWORK_TOOLS = frozenset({"web_search", "fetch_page"})
WITHHELD_HOST_TOOLS = _HOST_FILE_TOOLS | HOST_NETWORK_TOOLS
WITHHELD_ANSWER = (
    "{name} is off in this chat, because its commands run in a sandbox. Use the "
    "file_access_* tools for files: outputs/ is this chat's own output folder, "
    "and a file written there shows as a card."
)
#: The owner kept delegation on 2026-10-03 (§16.3), so this answer claims
#: nothing about the whole platform. It says what is true of this chat: the
#: sandbox has no network and the host web tools are off, and an agent that
#: this chat calls runs outside the sandbox.
NETWORK_WITHHELD_ANSWER = (
    "{name} is off in this chat. The sandbox that runs the commands of this "
    "chat has no network, and the host web tools web_search and fetch_page are "
    "off. An agent that you call with call_agent runs outside the sandbox. "
    "Work with the files of this chat."
)

NETWORK_OFF = "Network access is off on this platform."


def _broker_module() -> Any:
    from orchestrator import sandbox_broker

    return sandbox_broker


def _audit_decision(tool_name: str, command: str) -> tuple[bool, str]:
    """``decide()`` with the whole command, for the audit line (§7.4 step 2).

    It refuses a denylisted command in ``enforce`` mode only. In ``audit``
    mode it approves everything, so no boundary of this module depends on it.
    """
    try:
        from acb_skills.permission_policy import build_tool_call_context, decide

        approved, code, _detail = decide(build_tool_call_context(tool_name, {"command": command}))
    except Exception:  # a policy bug must not open or brick the tool
        return True, "decide_error"
    mode = os.environ.get("AGENT_PERMISSION_MODE", "enforce").strip().lower()
    enforced = (not approved) and mode == "enforce"
    _log.info(
        "permission.decision", mode=mode, tool=tool_name, approved=not enforced,
        would_deny=not approved, reason=code, surface="sandbox_tool",
    )
    return not enforced, code


def _with_steer(text: str) -> Any:
    """Drain a pending mid-run steer at this tool boundary, as the B6 gate does."""
    try:
        from orchestrator.steer import decorate_tool_result
    except ImportError:
        return text
    try:
        return decorate_tool_result(text)
    except Exception:
        return text


def _format(label: str, result: Any, saved: list[str]) -> str:
    if result.exit_code is None:
        head = f"{label} — no exit code"
    elif result.timed_out:
        head = f"{label} — timed out (exit {result.exit_code}) after {result.duration_s:.1f} s"
    else:
        head = f"{label} — exit {result.exit_code} in {result.duration_s:.1f} s"
    parts = [head]
    if result.message:
        parts.append(f"[{result.message}]")
    parts.append(result.output.rstrip() or "(no output)")
    if saved:
        shown = ", ".join(saved[:20]) + (" …" if len(saved) > 20 else "")
        parts.append(f"[{len(saved)} file(s) saved: {shown}]")
    return "\n".join(parts)


async def _run_in_sandbox(command: str, timeout_s: Any, *, tool_name: str, label: str) -> str:
    """Run *command* in the bound run's container, then sweep its outputs.

    The one code path of ``run_command`` and of a skill script. It never runs
    anything on the host.
    """
    ctx = artifact_context()
    if not ctx.get("workspace_root") or not ctx.get("session_id"):
        return f"{tool_name} failed: no run is bound, so nothing ran."
    if not isinstance(command, str) or not command.strip():
        return f"{tool_name} failed: the command is empty."
    try:
        sb = _broker_module()
    except ImportError:
        return f"{tool_name} is unavailable: the sandbox broker is not installed. Nothing ran."
    try:
        binding = sb.read_run_binding()
    except sb.SandboxError as exc:
        return f"{tool_name} refused: {exc} Nothing ran."
    if not sb.covers(binding.agent, binding.org):
        return (
            f"{tool_name} refused: the sandbox does not cover this agent for this "
            "organization. Nothing ran."
        )
    allowed, code = _audit_decision(tool_name, command)
    if not allowed:
        return f"[blocked by permission policy: {code}]"
    try:
        timeout = int(timeout_s)
    except (TypeError, ValueError):
        timeout = _DEFAULT_TIMEOUT_SECONDS
    broker = sb.get_broker()
    started = time.time()
    try:
        handle = await broker.acquire()
    except sb.SandboxError as exc:
        return f"{tool_name} failed: {exc} Nothing ran on the host."
    saved: list[str] = []
    try:
        try:
            result = await broker.exec(handle, command, timeout)
        except sb.SandboxError as exc:
            return f"{tool_name} failed: {exc} Nothing ran on the host."
        try:
            async with broker.host_files(handle):
                saved = await _sweep(binding, since=started)
        except sb.SandboxError:
            saved = []
    finally:
        await broker.release(handle)
    return _format(label, result, saved)


async def _sweep(binding: Any, *, since: float) -> list[str]:
    """Mirror what the command changed in the thread's own output folder.

    The caller holds ``broker.host_files()``. The reads use the safe opener.
    The container sees the rest of ``/workspace`` read-only, and ``.run/``
    lies outside the working dir, so the output folder is the one place a
    command can change. Each file there also shows as an artifact card.
    """
    import asyncio

    from acb_skills.code_tools import _collect_changed
    from acb_skills.tenant_file_store import first_time_shown
    from acb_skills.write_artifact import mirror_to_blob_store

    collected = await asyncio.to_thread(
        _collect_changed, binding.workspace, since, (binding.outputs_rel,),
    )
    saved: list[str] = []
    for rel, data in collected:
        # The mtime slack of the sweep lets a file from just before the
        # command through. Only new content is mirrored and shown.
        if not first_time_shown(binding.workspace, rel, data):
            continue
        try:
            await mirror_to_blob_store(rel, data, actor="agent")
        except Exception:
            continue
        announce_artifact(rel, data)
        saved.append(rel)
    return saved


async def run_command(command: str, timeout_s: int = _DEFAULT_TIMEOUT_SECONDS) -> str:
    """Run a shell command in this chat's sandbox and return its output.

    The sandbox is a Linux container with Python 3.12, pandas, numpy,
    matplotlib, openpyxl and the other packages of the coding image. It has
    NO network. It sees this workspace at ``/workspace``:

    * ``/workspace/.run/`` holds the data files of this run. Put member data
      here. It is deleted when the run ends.
    * ``/workspace/outputs/`` is this chat's own output folder. A file a
      command writes here is kept, and shows in the chat as a card.
    * ``/workspace/inputs/`` holds the files that the member attached in
      this chat. It is read-only.
    * The rest of ``/workspace`` (``agent-data/``) is read-only. Write it with
      the file tools.

    Args:
        command: The bash command, for example
            ``"python3 /workspace/.run/chart.py"``.
        timeout_s: Seconds before the command is killed. The most is 300.

    Returns:
        The exit code, the time, the output (cut to its first and last 6 KB
        when it is long), and the files the command saved.
    """
    text = await _run_in_sandbox(
        command, timeout_s, tool_name="run_command", label="run_command",
    )
    return _with_steer(text)


async def request_network_access(reason: str, hosts: list[str]) -> str:
    """Ask for network access for the sandbox. It is off on this platform.

    Args:
        reason: Why the sandbox needs the network.
        hosts: The exact hosts it needs.

    Returns:
        Always that network access is off on this platform (WS-43g builds it).
    """
    del reason, hosts
    return NETWORK_OFF


# ── skills ───────────────────────────────────────────────────────────────────


class SandboxScriptRunner:
    """Runs a skill script in the sandbox, through its ``/workspace`` path (§7.4).

    It refuses a script outside ``agent-data/skills/`` of the run's working
    dir, and a script of a skill that another member made. A list of
    arguments passes as positional arguments, and a mapping as
    ``--key value`` pairs, each one shell-quoted.
    """

    def __init__(self, workspace: Path, member: str = "") -> None:
        self._workspace = Path(workspace)
        self._member = str(member or "").strip().lower()

    def _own_skill(self, posix: str) -> bool:
        from acb_skills.agent_paths import skill_author, skill_top_rel

        top = skill_top_rel(posix)
        return top is not None and bool(self._member) and (
            skill_author(self._workspace, top) == self._member
        )

    def container_path(self, full_path: str) -> str:
        try:
            rel = Path(full_path).resolve().relative_to(self._workspace.resolve())
        except (OSError, ValueError) as exc:
            raise ValueError("The script is not in this workspace.") from exc
        posix = rel.as_posix()
        if not posix.startswith(_SKILLS_REL + "/"):
            raise ValueError("A skill script must lie under agent-data/skills/.")
        return f"/workspace/{posix}"

    async def __call__(self, skill: Any, script: Any, args: Any = None) -> str:
        del skill
        try:
            path = self.container_path(str(script.full_path))
        except ValueError as exc:
            return f"run_skill_script refused: {exc}"
        if not self._own_skill(path[len("/workspace/"):]):
            return "run_skill_script refused: only the member who made a skill may run it."
        argv: list[str] = []
        if isinstance(args, dict):
            for key, value in args.items():
                argv += [f"--{key}", str(value)]
        elif isinstance(args, (list, tuple)):
            argv = [str(a) for a in args]
        command = " ".join(shlex.quote(p) for p in ["python3", path, *argv])
        # The steer drain runs at the tool, which _steered() wraps.
        return await _run_in_sandbox(
            command, _DEFAULT_TIMEOUT_SECONDS,
            tool_name="run_skill_script", label=f"skill script {script.name}",
        )


# ── the per-run provider ─────────────────────────────────────────────────────


class _BrokerGuard:
    """The broker's lock, dirs and quota, for :class:`TenantFileStore`."""

    def __init__(self, broker: Any, binding: Any) -> None:
        self._broker = broker
        self._binding = binding

    def hold(self) -> Any:
        return self._broker.host_dir()

    async def prepare(self) -> None:
        await self._broker.ensure_thread_dirs(self._binding)

    def writes_refused(self) -> bool:
        return bool(self._broker.writes_refused(self._binding.workspace))


def _tool_names(agent: Any) -> set[str]:
    """Every tool name the agent object holds, in each shape MAF uses."""
    names: set[str] = set()
    pools: list[Any] = []
    options = getattr(agent, "default_options", None)
    if isinstance(options, dict):
        pools.append(options.get("tools") or [])
    for attr in ("tools", "_tools"):
        pools.append(getattr(agent, attr, None) or [])
    for pool in pools:
        if not isinstance(pool, (list, tuple)):
            continue
        for item in pool:
            name = getattr(item, "name", None) or getattr(
                getattr(item, "func", item), "__name__", None,
            )
            if isinstance(name, str):
                names.add(name)
    return names


def _file_instructions() -> str:
    return DEFAULT_FILE_ACCESS_INSTRUCTIONS + (
        "\n- These are the files that `run_command` sees under `/workspace`: "
        "`agent-data/`, `inputs/`, `outputs/` and `.run/`. `outputs/` is this "
        "chat's own output folder. `inputs/` holds the files that the member "
        "attached in this chat. `.run/` holds the data files of this run, "
        "and it is deleted when the run ends. A command can write only "
        "`outputs/` and `.run/`."
        "\n- This chat has no web access: `web_search` and `fetch_page` are off."
    )


class LockedSkillsSource(SkillsSource):
    """A skills source that lists ``agent-data/skills/`` under the dir lock.

    A caller-supplied source, so ``SkillsProvider`` caches nothing: the list
    is read on each turn, and a skill written in one turn lists on the next.
    It reads nothing when ``agent-data`` or ``agent-data/skills`` is a link
    (the safe opener). A skill's extra files are not offered as resources:
    the model reads them with the file tools, which use the safe opener and
    the lock. Scripts run in the sandbox (:class:`SandboxScriptRunner`).
    """

    def __init__(self, workspace: Path, guard: Any, member: str = "") -> None:
        self._workspace = Path(workspace)
        self._guard = guard
        self._member = str(member or "").strip().lower()
        self._inner = FileSkillsSource(
            str(self._workspace / _SKILLS_REL),
            script_runner=SandboxScriptRunner(self._workspace, self._member),
            resource_extensions=(),
        )

    def _scan(self, context: Any) -> list[Any]:
        """MAF's scan and the author filter, in a worker thread.

        ``FileSkillsSource.get_skills`` does its disk work synchronously, so
        it runs here, off the event loop, on a loop of its own. Only the
        skills that this run's member made come back (review P1, fix round 1).
        """
        import asyncio

        from acb_skills import safe_open
        from acb_skills.agent_paths import skill_author

        try:
            if safe_open.list_dir(self._workspace, _SKILLS_REL) is None:
                return []
        except (safe_open.UnsafePath, OSError):
            return []
        if not self._member:
            return []
        skills = asyncio.run(self._inner.get_skills(context))
        root = (self._workspace / _SKILLS_REL).resolve()
        mine: list[Any] = []
        for skill in skills:
            try:
                top = Path(skill.path).resolve().relative_to(root).parts[0]
            except (AttributeError, IndexError, OSError, ValueError):
                continue
            if skill_author(self._workspace, f"{_SKILLS_REL}/{top}") == self._member:
                mine.append(skill)
        return mine

    async def get_skills(self, context: Any) -> list[Any]:
        import asyncio

        async with self._guard.hold():
            return await asyncio.to_thread(self._scan, context)


class ProjectsSandboxProvider(ContextProvider):
    """Adds the sandbox tools to ONE run of a covered agent (§16.3).

    Built per run by :func:`attach_for_run`, and read at the start of each
    turn (``before_run``). It reads the run binding then, never input. It adds
    nothing when the run is not covered, when the run holds a host shell
    tool, or when the thread id cannot name a folder.
    """

    def __init__(self, agent_name: str) -> None:
        super().__init__(SOURCE_ID)
        self.agent_name = agent_name

    async def before_run(
        self, *, agent: Any, session: Any, context: Any, state: dict[str, Any],
    ) -> None:
        prepared = _layout(self.agent_name, agent)
        if prepared is None:
            return
        broker, binding = prepared
        await _add_tools(broker, binding, agent, session, context, state)


def _is_host_tool(item: Any) -> bool:
    return _one_tool_name(item) in WITHHELD_HOST_TOOLS


def _host_answer(name: str) -> str:
    answer = NETWORK_WITHHELD_ANSWER if name in HOST_NETWORK_TOOLS else WITHHELD_ANSWER
    return answer.format(name=name)


class WithholdHostTools(WithholdTools):
    """Takes :data:`WITHHELD_HOST_TOOLS` out of each model request of ONE run.

    The one pair of ``acb_skills.tool_guard``, with the host-tool rule.
    """

    def __init__(self) -> None:
        super().__init__(_is_host_tool)


class RefuseHostTools(RefuseTools):
    """Refuses a call to a withheld host tool, in case the model names one."""

    def __init__(self) -> None:
        super().__init__(_is_host_tool, _host_answer)


_one_tool_name = tool_name


def _steered(item: Any) -> Any:
    """A copy of a provider tool whose result drains a pending steer (review P2).

    The B6 gate does this for an agent's own tools (``_gate_own_maf_tools``).
    The file tools and the skill tools join the run through a context
    provider, so the gate never sees them. The copy keeps the tool's name,
    description and schema, and the original object is never changed.
    """
    func = getattr(item, "func", None)
    if not callable(func) or getattr(func, "__cc_steered__", False):
        return item

    @functools.wraps(func)
    async def call(*args: Any, **kwargs: Any) -> Any:
        result = func(*args, **kwargs)
        if inspect.isawaitable(result):
            result = await result
        return _with_steer(result)

    call.__cc_steered__ = True  # type: ignore[attr-defined]
    clone = copy.copy(item)
    clone.func = call
    return clone


def _layout(agent_name: str, agent: Any) -> tuple[Any, Any] | None:
    try:
        sb = _broker_module()
    except ImportError:
        return None
    try:
        binding = sb.read_run_binding()
    except sb.SandboxError as exc:
        _log.info("sandbox_tools.not_bound", agent=agent_name, reason=str(exc)[:200])
        return None
    if binding.agent != agent_name or not sb.covers(agent_name, binding.org):
        return None
    held = _tool_names(agent) & _HOST_SHELL_TOOLS
    if held:
        _log.warning(
            "sandbox_tools.host_shell_present", agent=agent_name, tools=sorted(held),
        )
        return None
    try:
        binding.thread_slug  # noqa: B018 — raises on an id that names no folder
    except ValueError:
        _log.info("sandbox_tools.thread_id_refused", agent=agent_name)
        return None
    return sb.get_broker(), binding


async def _add_tools(
    broker: Any, binding: Any, agent: Any, session: Any, context: Any, state: dict[str, Any],
) -> None:
    from acb_skills.tenant_file_store import TenantFileStore

    member = str(artifact_context().get("member") or "")
    guard = _BrokerGuard(broker, binding)
    store = TenantFileStore(
        workspace=binding.workspace, outputs_rel=binding.outputs_rel,
        run_data=binding.run_data, guard=guard, member=member,
        inputs_rel=binding.inputs_rel,
    )
    files = FileAccessProvider(
        store,
        source_id=f"{SOURCE_ID}-files",
        instructions=_file_instructions(),
        # A chat member is present, but the store is the boundary: it reaches
        # only this run's own files, and refuses links and escapes.
        disable_readonly_tool_approval=True,
        disable_write_tool_approval=True,
    )
    before = len(context.tools)
    await files.before_run(agent=agent, session=session, context=context, state=state)
    skills = SkillsProvider(
        LockedSkillsSource(binding.workspace, guard, member),
        source_id=f"{SOURCE_ID}-skills",
        disable_load_skill_approval=True,
        disable_read_skill_resource_approval=True,
        disable_run_skill_script_approval=True,
    )
    await skills.before_run(agent=agent, session=session, context=context, state=state)
    # The steer drain at every tool these two providers added (review P2).
    context.tools[before:] = [_steered(t) for t in context.tools[before:]]
    context.extend_tools(SOURCE_ID, [tool(run_command, approval_mode="never_require")])
    context.extend_middleware(SOURCE_ID, [WithholdHostTools(), RefuseHostTools()])
    # WS-43u (§16.3): the rules for code, keyed on `run_command`. They come
    # from the tools that this turn now holds, so a run without the tool never
    # reads them.
    rules = render_run_sections(_one_tool_name(t) for t in context.tools)
    if rules:
        context.extend_instructions(SOURCE_ID, rules)


def attach_for_run(agent: Any, agent_name: str) -> Any:
    """The object a factory hands to this run: *agent*, or a view with the tools.

    *agent* itself, unchanged, unless the broker covers *agent_name* for the
    run's own tenant. Then a per-run view (``agent_with_providers``) that
    carries :class:`ProjectsSandboxProvider`. The agent object is never
    changed, so a concurrent run of another organization can never see the
    tools (§16.3).
    """
    try:
        sb = _broker_module()
        from orchestrator.executor import _current_run_org
    except ImportError:
        return agent
    try:
        org = _current_run_org()
        covered = bool(org) and sb.covers(agent_name, str(org))
    except Exception as exc:
        _log.warning("sandbox_tools.cover_check_failed", agent=agent_name, error=str(exc)[:200])
        return agent
    if not covered:
        return agent
    from orchestrator._native_run_context import agent_with_providers

    _log.info("sandbox_tools.attached", agent=agent_name)
    return agent_with_providers(agent, [ProjectsSandboxProvider(agent_name)])
