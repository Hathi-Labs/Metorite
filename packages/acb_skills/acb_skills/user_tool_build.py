"""The step engine of a user tool (SBX-4b1, Metorite task 3986).

Spec: ``project-docs/specs/agent_sandbox.md`` §6.3 and §12.4, decisions 1 to
8. The engine builds and smoke-tests one package in the ``tools`` container
of the sandbox broker. A fixture package stands in for the model until
SBX-4b2 (task 4049) adds ``build_tool``, the card and the build session.

**The entry.** :func:`start_build` is the one entry, and it is dark: no
production module calls it until SBX-4b2 lifts the source scan of
``tests/unit/test_user_tool_build.py`` together with its card. The engine
grants egress with no card, so only the card may call it.

**The rules (fence SBX-F12, ``tests/unit/test_user_tool_build.py``):**

1. **The tenant (R5).** The org, the member, the thread and the run come
   from the run binding (``sandbox_broker.read_tool_binding``), never from
   an argument. The approved read scopes come from the card.
2. **One build in a thread.** :meth:`SandboxBroker.acquire_tool` holds a
   lock of (org, thread) until the build ends. A second build gets "busy".
3. **The package check** (:func:`check_package`) runs on the host, through
   the safe opener, at the start of each round. Each refusal has one fixed
   code, and the model hears that code only.
4. **One host at a time** (decision 3). The install runs with the two PyPI
   hosts only. The smoke command runs with the door only. Each round ends
   with ``revoke_step`` and ``revoke_egress``, also on a failure or a cancel.
5. **The capability** (decision 4) is ``phase="smoke"``, the read scopes of
   ``tool.json``, no write scope and ``ttl_s=180``. It reaches the smoke
   command through ``exec(env_file=...)`` only.
6. **The caps** (decision 8). At most :data:`MAX_ROUNDS` rounds in a build,
   and ``user_tool_step_seconds_build`` seconds for the whole build. Each
   round has :data:`SMOKE_BUDGET_S` for the smoke step.
7. **The rows** (decision 10). One ``build`` row for each build and one
   ``smoke`` row for each round, through ``gateway/studio/user_tool_runs``
   in a ``tenant_session()``. No ``usage_event`` and no credit.
"""
from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import json
import os
import re
import stat
import time
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from acb_common import get_logger, get_settings

_log = get_logger("acb_skills.user_tool_build")

# ── The package (§6.3, decision 5) ──────────────────────────────────────────

#: The five files of a package (§6.3).
PACKAGE_FILES: tuple[str, ...] = (
    "SKILL.md", "tool.json", "main.py", "requirements.lock", "smoke.json",
)
#: The size cap of one package, in bytes (the cap of §7.2).
PACKAGE_MAX_BYTES = 262_144
#: The most lines of ``requirements.lock``.
LOCK_MAX_LINES = 20
#: One lock line: ``<name>==<version>`` and one SHA-256 hash.
_LOCK_LINE = re.compile(
    r"[A-Za-z0-9](?:[A-Za-z0-9._-]{0,99})==[A-Za-z0-9](?:[A-Za-z0-9.+!_-]{0,63})"
    r"[ \t]+--hash=sha256:[0-9a-f]{64}"
)
#: The front matter of ``SKILL.md``: a block of ``key: value`` lines.
_FRONT_MATTER = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL)

# The fixed codes of decision 5. Each one is a valid ``error_class``.
MISSING_FILE = "package_missing_file"
EXTRA_NAME = "package_extra_name"
NOT_REGULAR = "package_not_regular"
TOO_LARGE = "package_too_large"
NOT_TEXT = "package_not_text"
TOOL_JSON = "package_tool_json"
READ_SCOPE_UNKNOWN = "package_read_scope_unknown"
READ_SCOPE_NOT_APPROVED = "package_read_scope_not_approved"
WRITE_SCOPE = "package_write_scope"
SMOKE_INPUT = "package_smoke_input"
SKILL_MD = "package_skill_md"
LOCK_LINE = "package_lock_line"
LOCK_TOO_LONG = "package_lock_too_long"

# ── The step (decisions 6 to 8) ─────────────────────────────────────────────

#: The rounds of one build (decision 8).
MAX_ROUNDS = 3
#: The install of one round, in seconds (the limit of ``ai_studio.md`` §19.5).
INSTALL_TIMEOUT_S = 180
#: The bytes of ``site/`` after an install (the layer cap of §19.5).
SITE_MAX_BYTES = 300 * 1024 * 1024
#: The smoke step of one round: steps 2 to 5 of decision 7 (§5.6).
SMOKE_BUDGET_S = 180
#: The one smoke command (§5.6).
SMOKE_COMMAND_S = 120
#: The life of the smoke capability (decision 4).
SMOKE_TTL_S = 180
#: The most door calls of one round (condition 3, ``CALL_CAP`` of the door).
CALL_MAX = 600
#: The largest ``result.json`` that the engine reads.
RESULT_MAX_BYTES = 4 * 1024 * 1024

#: The hosts of the install grant (decision 3). Tests point them at a stub
#: index. The door host is the broker's ``DOOR_HOST``.
INSTALL_HOSTS: tuple[str, ...] = ("pypi.org", "files.pythonhosted.org")
#: The index of the install. A constant, so no package can name another.
INDEX_URL = "https://pypi.org/simple"

#: The paths inside ``<tool-data>`` (decision 2).
PKG_DIR = "pkg"
SITE_DIR = "site"
OUTPUTS_DIR = "outputs"
INPUT_FILE = "input.json"
RESULT_FILE = "result.json"
_RUN = "/workspace/.run"
#: The smoke command (decision 7, step 4).
SMOKE_COMMAND = (
    f"PYTHONPATH={_RUN}/{SITE_DIR} PYTHONNOUSERSITE=1 exec python3 {_RUN}/{PKG_DIR}/main.py"
)

# The fixed codes of a round that failed after the package check.
INSTALL_FAILED = "install_failed"
INSTALL_TIMEOUT = "install_timeout"
INSTALL_TOO_LARGE = "install_too_large"
EXIT_CODE = "smoke_exit_code"
OUTPUT_SCHEMA = "smoke_output_schema"
SMOKE_TIMEOUT = "smoke_timeout"
SMOKE_KILLED = "smoke_killed"
CALL_CAP = "smoke_call_cap"
QUOTA = "smoke_quota"
SCOPE_REFUSAL = "smoke_scope_refusal"
COUNTS_UNREADABLE = "smoke_counts_unreadable"
SANDBOX_FAILED = "sandbox_failed"
# The fixed codes of a build row.
NO_PASS = "build_no_pass"
BUILD_BUDGET = "build_budget"
ROUNDS_EXHAUSTED = "build_rounds_exhausted"


class PackageRefused(ValueError):
    """``check_package`` refused the package. ``code`` is one fixed code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class SmokeRoundsExhausted(RuntimeError):
    """The fourth round of one build. No command ran (decision 8)."""


class BuildBudgetExceeded(RuntimeError):
    """The build has no time left. No command ran (decision 8)."""


@dataclass(frozen=True)
class Package:
    """A package that passed :func:`check_package`."""

    read_scopes: tuple[str, ...]
    input_schema: Any
    output_schema: Any
    smoke_input: Any
    lock_lines: int


def _text(raw: bytes) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        raise PackageRefused(NOT_TEXT) from None


def _json(raw: bytes, code: str) -> Any:
    try:
        return json.loads(_text(raw))
    except ValueError:
        raise PackageRefused(code) from None


def _schema(value: Any) -> Any:
    """*value* when it is a valid JSON Schema, or :class:`PackageRefused`."""
    import jsonschema

    if not isinstance(value, dict | bool):
        raise PackageRefused(TOOL_JSON)
    try:
        jsonschema.Draft202012Validator.check_schema(value)
    except jsonschema.SchemaError:
        raise PackageRefused(TOOL_JSON) from None
    return value


def _valid(schema: Any, value: Any) -> bool:
    import jsonschema

    try:
        return bool(jsonschema.Draft202012Validator(schema).is_valid(value))
    except (jsonschema.exceptions.JsonSchemaException, RecursionError, ValueError):
        return False


def _front_matter_ok(text: str) -> bool:
    found = _FRONT_MATTER.match(text)
    if found is None:
        return False
    keys: dict[str, str] = {}
    for line in found.group(1).splitlines():
        key, sep, value = line.partition(":")
        if sep:
            keys[key.strip()] = value.strip()
    return bool(keys.get("name")) and bool(keys.get("description"))


def _list_package(pkg_dir: Path) -> list[str]:
    """The names in *pkg_dir*. Refuses a name that is not a regular file."""
    try:
        st = os.lstat(pkg_dir)
    except FileNotFoundError:
        raise PackageRefused(MISSING_FILE) from None
    if not stat.S_ISDIR(st.st_mode):
        raise PackageRefused(NOT_REGULAR)
    names: list[str] = []
    with os.scandir(pkg_dir) as entries:
        for entry in entries:
            if entry.is_symlink() or not entry.is_file(follow_symlinks=False):
                raise PackageRefused(NOT_REGULAR)
            names.append(entry.name)
    return names


def _read_files(pkg_dir: Path) -> dict[str, bytes]:
    """The five files of *pkg_dir*, read through the safe opener."""
    from acb_skills import safe_open

    names = _list_package(pkg_dir)
    if any(name not in PACKAGE_FILES for name in names):
        raise PackageRefused(EXTRA_NAME)
    if set(PACKAGE_FILES) - set(names):
        raise PackageRefused(MISSING_FILE)
    files: dict[str, bytes] = {}
    for name in PACKAGE_FILES:
        try:
            raw = safe_open.read_bytes(
                pkg_dir.parent, f"{pkg_dir.name}/{name}", limit=PACKAGE_MAX_BYTES,
            )
        except safe_open.UnsafePath as exc:
            raise PackageRefused(TOO_LARGE if "larger than" in str(exc) else NOT_REGULAR) from None
        if raw is None:
            raise PackageRefused(MISSING_FILE)
        files[name] = raw
    if sum(len(raw) for raw in files.values()) > PACKAGE_MAX_BYTES:
        raise PackageRefused(TOO_LARGE)
    _text(files["main.py"])
    return files


def _check_tool_json(raw: bytes, approved: frozenset[str]) -> tuple[Any, Any, list[str]]:
    """The input schema, the output schema and the read scopes of ``tool.json``."""
    from acb_auth.sandbox_capabilities import SANDBOX_READ_SCOPES

    tool = _json(raw, TOOL_JSON)
    keys = ("input_schema", "output_schema", "read_scopes", "write_scopes")
    if not isinstance(tool, dict) or any(key not in tool for key in keys):
        raise PackageRefused(TOOL_JSON)
    input_schema = _schema(tool["input_schema"])
    output_schema = _schema(tool["output_schema"])
    reads, writes = tool["read_scopes"], tool["write_scopes"]
    if not isinstance(reads, list) or not all(isinstance(r, str) for r in reads):
        raise PackageRefused(TOOL_JSON)
    if not isinstance(writes, list):
        raise PackageRefused(TOOL_JSON)
    if any(scope not in SANDBOX_READ_SCOPES for scope in reads):
        raise PackageRefused(READ_SCOPE_UNKNOWN)
    if any(scope not in approved for scope in reads):
        raise PackageRefused(READ_SCOPE_NOT_APPROVED)
    if writes:
        raise PackageRefused(WRITE_SCOPE)
    return input_schema, output_schema, reads


def _lock_lines(raw: bytes) -> int:
    """The count of lines of ``requirements.lock``, each one checked."""
    lines = [line.strip() for line in _text(raw).splitlines()]
    lines = [line for line in lines if line]
    if len(lines) > LOCK_MAX_LINES:
        raise PackageRefused(LOCK_TOO_LONG)
    if any(_LOCK_LINE.fullmatch(line) is None for line in lines):
        raise PackageRefused(LOCK_LINE)
    return len(lines)


def check_package(pkg_dir: Path, approved_scopes: Iterable[str]) -> Package:
    """Check one package on the host (decision 5). Raises :class:`PackageRefused`.

    *pkg_dir* is ``<tool-data>/pkg``. Each read goes through the safe opener
    from ``<tool-data>``, so no link at any depth is followed. A container
    writes this dir, so every byte here is untrusted input.
    """
    files = _read_files(pkg_dir)
    input_schema, output_schema, reads = _check_tool_json(
        files["tool.json"], frozenset(approved_scopes),
    )
    smoke = _json(files["smoke.json"], SMOKE_INPUT)
    if not isinstance(smoke, dict) or "input" not in smoke:
        raise PackageRefused(SMOKE_INPUT)
    if not _valid(input_schema, smoke["input"]):
        raise PackageRefused(SMOKE_INPUT)
    if not _front_matter_ok(_text(files["SKILL.md"])):
        raise PackageRefused(SKILL_MD)
    count = _lock_lines(files["requirements.lock"])
    return Package(
        read_scopes=tuple(dict.fromkeys(reads)), input_schema=input_schema,
        output_schema=output_schema, smoke_input=smoke["input"], lock_lines=count,
    )


def install_command() -> str:
    """The install of decision 6. The engine runs it, never the model.

    ``--no-deps`` makes the lock hold each indirect package too.
    ``--only-binary :all:`` runs no build code of a package.
    """
    return (
        "pip install --require-hashes --only-binary :all: --no-deps "
        f"--index-url {INDEX_URL} --target {_RUN}/{SITE_DIR} "
        f"-r {_RUN}/{PKG_DIR}/requirements.lock"
    )


# ── A round and a build ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class RoundResult:
    """What one smoke round gives back. ``output`` is the capped exec output."""

    passed: bool
    outcome: str
    error_class: str | None
    exit_code: int | None = None
    api_reads: int = 0
    refusals: int = 0
    wall_ms: int = 0
    output: str = ""


def _broker() -> Any:
    from orchestrator import sandbox_broker as sb

    return sb.get_broker()


def _backend_name() -> str:
    """``docker-runc`` or ``docker-runsc`` (§7.3): the runtime of the dev stack."""
    from orchestrator import sandbox_broker as sb

    try:
        runtime = sb.sandbox_runtime(get_settings(), sb.BACKEND_DOCKER)
    except sb.SandboxError:
        runtime = sb.RUNTIME_RUNC
    return f"docker-{runtime}"


def _run_id(thread: str) -> str:
    from acb_skills.write_artifact import artifact_context

    return str(artifact_context().get("run_id") or thread)


@dataclass
class ToolBuild:
    """One build: its container, its approval, its rounds and its clock."""

    handle: Any
    org: str
    member: str
    thread: str
    run_id: str
    approved: frozenset[str]
    deadline: float
    gap_id: str | None = None
    backend: str = "docker-runc"
    rounds: int = 0
    passed: bool = False
    timed_out: bool = False
    api_reads: int = 0
    results: list[RoundResult] = field(default_factory=list)

    @property
    def tool_data(self) -> Path:
        return Path(self.handle.binding.tool_data)

    def remaining(self) -> float:
        """The seconds left of the whole build (decision 8)."""
        return self.deadline - time.monotonic()

    async def run_smoke(self) -> RoundResult:
        """One round of decision 7: check, install, smoke, count and revoke.

        The fourth call raises :class:`SmokeRoundsExhausted`, and a call with
        no time left raises :class:`BuildBudgetExceeded`. Neither runs a
        command. Each round writes one ``smoke`` row.
        """
        if self.rounds >= MAX_ROUNDS:
            raise SmokeRoundsExhausted(f"A build has at most {MAX_ROUNDS} smoke rounds.")
        if self.remaining() <= 0:
            self.timed_out = True
            raise BuildBudgetExceeded("The build has no time left.")
        self.rounds += 1
        started, started_at = time.monotonic(), datetime.now(UTC)
        try:
            result = await self._round()
        except asyncio.CancelledError:
            await asyncio.shield(self._record(
                "smoke", "cancelled", None, None, 0, started, started_at))
            raise
        wall_ms = int((time.monotonic() - started) * 1000)
        result = dataclasses.replace(result, wall_ms=wall_ms)
        self.api_reads += result.api_reads
        self.passed = self.passed or result.passed
        self.timed_out = self.timed_out or result.outcome == "timeout"
        self.results.append(result)
        await self._record("smoke", result.outcome, result.error_class, result.exit_code,
                           result.api_reads, started, started_at)
        _log.info("user_tool_build.round", org=self.org, round=self.rounds,
                  outcome=result.outcome, error_class=result.error_class,
                  api_reads=result.api_reads, wall_ms=wall_ms)
        return result

    # ── the steps ───────────────────────────────────────────────────────────

    async def _clean_round(self) -> None:
        """Delete ``site/``, the content of ``outputs/`` and ``input.json``
        (decision 2). ``outputs/`` stays, because the container mounts it."""
        from acb_skills import safe_open

        broker = _broker()
        root = self.tool_data

        def clean() -> None:
            safe_open.remove_tree(root, SITE_DIR)
            safe_open.unlink(root, INPUT_FILE)
            outputs = root / OUTPUTS_DIR
            st = os.lstat(outputs)
            if not stat.S_ISDIR(st.st_mode):
                raise safe_open.UnsafePath("outputs is not a real dir")
            for name in os.listdir(outputs):
                safe_open.remove_tree(root, f"{OUTPUTS_DIR}/{name}")

        async with broker.host_files(self.handle):
            await asyncio.to_thread(clean)
        await broker.measure_workspace(self.handle)

    async def _check(self) -> Package:
        broker = _broker()
        async with broker.host_files(self.handle):
            return await asyncio.to_thread(
                check_package, self.tool_data / PKG_DIR, self.approved,
            )

    async def _install(self, package: Package) -> RoundResult | None:
        """Grant 1 and the install (decision 6). ``None`` on success."""
        from orchestrator import sandbox_broker as sb

        if not package.lock_lines:
            return None  # an empty lock: the image packages only
        broker = _broker()
        limit = min(float(INSTALL_TIMEOUT_S), self.remaining())
        if limit <= 0:
            return RoundResult(False, "timeout", INSTALL_TIMEOUT)
        try:
            async with asyncio.timeout(limit + 30):
                await broker.grant_egress(self.handle, "user_tool_install", INSTALL_HOSTS)
                ran = await broker.exec(self.handle, install_command(), limit)
        except TimeoutError:
            return RoundResult(False, "timeout", INSTALL_TIMEOUT)
        except sb.SandboxError:
            return RoundResult(False, "failed", SANDBOX_FAILED)
        if ran.timed_out:
            return RoundResult(False, "timeout", INSTALL_TIMEOUT, ran.exit_code,
                               output=ran.output)
        if ran.exit_code != 0:
            return RoundResult(False, "failed", INSTALL_FAILED, ran.exit_code,
                               output=ran.output)
        if await self._site_bytes() > SITE_MAX_BYTES:
            return RoundResult(False, "failed", INSTALL_TOO_LARGE, ran.exit_code)
        return None

    async def _site_bytes(self) -> int:
        from acb_skills import safe_open

        async with _broker().host_files(self.handle):
            found = await asyncio.to_thread(safe_open.walk_files, self.tool_data, SITE_DIR)
        return sum(size for _path, size, _mtime in found)

    async def _write_input(self, package: Package) -> None:
        from acb_skills import safe_open

        data = json.dumps(package.smoke_input).encode("utf-8")
        async with _broker().host_files(self.handle):
            await asyncio.to_thread(safe_open.replace_bytes, self.tool_data, INPUT_FILE, data)

    async def _read_result(self) -> tuple[bool, Any]:
        """``(True, value)`` when ``outputs/result.json`` is one JSON value."""
        from acb_skills import safe_open

        async with _broker().host_files(self.handle):
            try:
                raw = await asyncio.to_thread(
                    safe_open.read_bytes, self.tool_data, f"{OUTPUTS_DIR}/{RESULT_FILE}",
                    limit=RESULT_MAX_BYTES,
                )
            except (safe_open.UnsafePath, OSError):
                return False, None
        if raw is None:
            return False, None
        try:
            return True, json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return False, None

    async def _revoke(self, minted: bool) -> None:
        """The end of each round, also on a failure or a cancel (decisions 3
        and 4). Each step logs its error class and goes on."""
        from acb_auth import sandbox_capabilities

        if minted:
            try:
                await sandbox_capabilities.revoke_step(
                    org=self.org, run_id=self.run_id, phase="smoke")
            except Exception as exc:
                _log.warning("user_tool_build.revoke_failed", error=type(exc).__name__)
        try:
            await _broker().revoke_egress(self.handle)
        except Exception as exc:
            _log.warning("user_tool_build.revoke_egress_failed", error=type(exc).__name__)

    async def _smoke(self, package: Package) -> RoundResult:
        """Steps 2 to 5 of decision 7, inside the smoke budget."""
        from acb_auth import agent_tokens, sandbox_capabilities
        from orchestrator import sandbox_broker as sb

        broker = _broker()
        budget = min(float(SMOKE_BUDGET_S), self.remaining())
        if budget <= 0:
            return RoundResult(False, "timeout", SMOKE_TIMEOUT)
        minted = False
        ran: Any = None
        counts: Any = None
        try:
            async with asyncio.timeout(budget):
                await self._write_input(package)
                await broker.grant_egress(self.handle, "user_tool_smoke", (sb.DOOR_HOST,))
                capability = await sandbox_capabilities.mint(
                    org=self.org, member=self.member, run_id=self.run_id,
                    thread_id=self.thread, phase="smoke", read_scopes=package.read_scopes,
                    write_scopes=(), ttl_s=SMOKE_TTL_S,
                )
                minted = True
                ran = await broker.exec(
                    self.handle, SMOKE_COMMAND, min(float(SMOKE_COMMAND_S), budget),
                    env_file={"METORITE_API_TOKEN": capability.token},
                )
                try:
                    counts = await sandbox_capabilities.door_counts(
                        self.org, agent_tokens.hash_token(capability.token))
                except Exception as exc:
                    _log.warning("user_tool_build.counts_failed", error=type(exc).__name__)
        except TimeoutError:
            return RoundResult(False, "timeout", SMOKE_TIMEOUT,
                               getattr(ran, "exit_code", None))
        except sb.SandboxError:
            return RoundResult(False, "failed", SANDBOX_FAILED)
        finally:
            await asyncio.shield(self._revoke(minted))
        return await self._judge(package, ran, counts)

    async def _judge(self, package: Package, ran: Any, counts: Any) -> RoundResult:
        """The four conditions of §6.3, in order. The first that fails names
        the error class."""
        reads = int(counts.calls) if counts is not None else 0
        refusals = int(counts.refusals) if counts is not None else 0
        exit_code = ran.exit_code
        output = str(ran.output or "")
        if ran.timed_out:
            return RoundResult(False, "timeout", SMOKE_TIMEOUT, exit_code, reads, refusals,
                               output=output)
        if exit_code == 137:
            return RoundResult(False, "failed", SMOKE_KILLED, exit_code, reads, refusals,
                               output=output)
        if reads > CALL_MAX:
            return RoundResult(False, "failed", CALL_CAP, exit_code, reads, refusals,
                               output=output)
        if _broker().quota_exceeded(self.handle):
            return RoundResult(False, "failed", QUOTA, exit_code, reads, refusals,
                               output=output)
        if exit_code != 0:
            return RoundResult(False, "failed", EXIT_CODE, exit_code, reads, refusals,
                               output=output)
        ok, value = await self._read_result()
        if not ok or not _valid(package.output_schema, value):
            return RoundResult(False, "failed", OUTPUT_SCHEMA, exit_code, reads, refusals,
                               output=output)
        if counts is None:
            return RoundResult(False, "failed", COUNTS_UNREADABLE, exit_code, output=output)
        if refusals:
            return RoundResult(False, "failed", SCOPE_REFUSAL, exit_code, reads, refusals,
                               output=output)
        return RoundResult(True, "succeeded", None, exit_code, reads, refusals, output=output)

    async def _round(self) -> RoundResult:
        """The package check, the install with grant 1, and the smoke step."""
        from orchestrator import sandbox_broker as sb

        try:
            await self._clean_round()
            package = await self._check()
        except PackageRefused as refused:
            return RoundResult(False, "refused", refused.code)
        except sb.SandboxError:
            return RoundResult(False, "failed", SANDBOX_FAILED)
        except OSError:
            return RoundResult(False, "failed", SANDBOX_FAILED)
        try:
            failed = await self._install(package)
        except BaseException:
            await asyncio.shield(self._revoke(False))
            raise
        if failed is not None:
            await asyncio.shield(self._revoke(False))
            return failed
        # The grant of the smoke step replaces the grant of the install, and
        # its restart ends each process of the install and of the session.
        return await self._smoke(package)

    async def _record(
        self, phase: str, outcome: str, error_class: str | None, exit_code: int | None,
        api_reads: int, started: float, started_at: datetime,
    ) -> None:
        """One row of ``user_tool_runs`` (decision 10). A failed write logs
        its error class, and the build goes on."""
        from acb_common.db import tenant_session
        from gateway.studio import user_tool_runs

        wall_ms = int((time.monotonic() - started) * 1000)
        try:
            async with tenant_session(self.org) as db:
                await user_tool_runs.record_run(
                    db, member=self.member, run_id=self.run_id, thread_id=self.thread,
                    phase=phase, backend=self.backend, outcome=outcome,
                    exit_code=exit_code, wall_ms=wall_ms, api_reads=api_reads,
                    error_class=error_class, gap_id=self.gap_id, started_at=started_at,
                )
        except Exception as exc:
            _log.warning("user_tool_build.row_failed", phase=phase, error=type(exc).__name__)


async def _reset_package(build: ToolBuild) -> None:
    """Each build starts with an empty ``pkg/`` (decision 2)."""
    from acb_skills import safe_open

    broker = _broker()
    root = build.tool_data

    def reset() -> None:
        safe_open.remove_tree(root, PKG_DIR)
        safe_open.ensure_dir(root, PKG_DIR)

    async with broker.host_files(build.handle):
        await asyncio.to_thread(reset)


@contextlib.asynccontextmanager
async def start_build(
    approved_scopes: Iterable[str], *, gap_id: str | None = None,
) -> AsyncIterator[ToolBuild]:
    """Start one build of a user tool in the bound run (decisions 1 to 8).

    The one entry of the step engine. *approved_scopes* are the read scopes
    that the member approved on the build card (SBX-4b2). The org, the member,
    the thread and the run come from the run binding only (R5).

    Before any start it refuses a scope outside ``SANDBOX_READ_SCOPES``, a run
    with no member, egress off, and an install host or the door off the
    grant rules. ``acquire_tool`` then refuses a second build in the thread,
    and a full cap, with ``SandboxBusy``.

    The block gets a :class:`ToolBuild`. Its end writes the ``build`` row,
    revokes each grant and capability, and frees the thread.
    """
    from acb_auth.sandbox_capabilities import SANDBOX_READ_SCOPES
    from orchestrator import sandbox_broker as sb

    approved = frozenset(approved_scopes)
    if isinstance(approved_scopes, str) or any(s not in SANDBOX_READ_SCOPES for s in approved):
        raise ValueError("an approved scope is not a read scope of the sandbox door")
    binding = sb.read_tool_binding()
    member = (binding.member or "").strip().lower()
    if not member:
        raise sb.SandboxRefused("No member is bound to this run, so no tool build starts.")
    settings = get_settings()
    if not sb.egress_enabled(settings):
        raise sb.SandboxRefused(sb.EGRESS_OFF)
    sb.check_grant_hosts(INSTALL_HOSTS, settings)
    sb.check_grant_hosts((sb.DOOR_HOST,), settings)
    budget = int(getattr(settings, "user_tool_step_seconds_build", 600))
    broker = _broker()
    handle = await broker.acquire_tool()
    started, started_at = time.monotonic(), datetime.now(UTC)
    build = ToolBuild(
        handle=handle, org=binding.org, member=member, thread=binding.thread,
        run_id=_run_id(binding.thread), approved=approved,
        deadline=started + max(budget, 0), gap_id=gap_id, backend=_backend_name(),
    )
    outcome: str = "failed"
    error_class: str | None = NO_PASS
    try:
        await _reset_package(build)
        yield build
        if build.passed:
            outcome, error_class = "succeeded", None
        elif build.timed_out or build.remaining() <= 0:
            outcome, error_class = "timeout", BUILD_BUDGET
    except asyncio.CancelledError:
        outcome, error_class = "cancelled", None
        raise
    except BuildBudgetExceeded:
        outcome, error_class = "timeout", BUILD_BUDGET
        raise
    except SmokeRoundsExhausted:
        outcome, error_class = "failed", ROUNDS_EXHAUSTED
        raise
    finally:
        try:
            await asyncio.shield(build._revoke(True))
            await asyncio.shield(build._record(
                "build", outcome, error_class, None, build.api_reads, started, started_at))
        finally:
            await broker.release_tool(handle)
        _log.info("user_tool_build.build", org=build.org, outcome=outcome,
                  rounds=build.rounds, error_class=error_class)


__all__ = [
    "INSTALL_HOSTS",
    "MAX_ROUNDS",
    "PACKAGE_FILES",
    "BuildBudgetExceeded",
    "Package",
    "PackageRefused",
    "RoundResult",
    "SmokeRoundsExhausted",
    "ToolBuild",
    "check_package",
    "install_command",
    "start_build",
]
