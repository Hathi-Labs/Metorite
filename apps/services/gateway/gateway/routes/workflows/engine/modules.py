"""Module Studio code validation + execution (spec §3.4, §5).

A module is a pure data transform: Python source defining

    def run(inputs: dict) -> dict: ...

The v1 runtime is **restricted execution, not a sandbox** (spec §3.4 — full
process isolation is BO-7; when it lands, execution moves into it without API
changes here). Defense layers, each independent:

1. AST allowlist at save time (``validate_module_code``): no imports, no
   dunder/underscore attribute access, no forbidden names.
2. Execution in a separate ``python -I -E -S`` subprocess with a fixed harness
   that compiles the code under an allowlisted ``__builtins__`` dict — the
   code arrives via stdin as data, never interpolated into the harness.
3. OS resource limits (CPU seconds, address space, file size, no subprocess
   inheritable env), a wall-clock kill, and output size caps.

⚠️ **Dark by org (WS-STU STU-Q15a).** D127.2 keeps member code off the box,
and this runner is not a sandbox. So :func:`run_module_code` takes a required
``organization_id`` and calls ``gateway.module_runs.refuse_unless_allowed``
as its first statement. An org that ``MODULE_RUN_ORGS`` does not list gets
``ModuleRunsOff``, and no child process starts. The three callers (the two
test routes and the module node) pass the org of the session or of the run,
never one from request input. Fence: ``tests/unit/test_module_runs_off.py``.
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
import json
import sys
from dataclasses import dataclass
from typing import Any

from gateway.module_runs import refuse_unless_allowed

MAX_CODE_BYTES = 20 * 1024
MAX_INPUT_BYTES = 256 * 1024
MAX_OUTPUT_BYTES = 256 * 1024
MODULE_WALL_TIMEOUT_SECS = 10.0
MODULE_CPU_SECONDS = 5
MODULE_ADDRESS_SPACE_BYTES = 512 * 1024 * 1024

#: Names a module may reference from builtins — pure-computation surface only.
SAFE_BUILTIN_NAMES = frozenset(
    {
        "abs",
        "all",
        "any",
        "bool",
        "bytes",
        "callable",
        "chr",
        "dict",
        "divmod",
        "enumerate",
        "filter",
        "float",
        "format",
        "frozenset",
        "hash",
        "hex",
        "int",
        "isinstance",
        "issubclass",
        "iter",
        "len",
        "list",
        "map",
        "max",
        "min",
        "next",
        "ord",
        "pow",
        "print",
        "range",
        "repr",
        "reversed",
        "round",
        "set",
        "slice",
        "sorted",
        "str",
        "sum",
        "tuple",
        "zip",
        # Exceptions a transform legitimately raises/catches.
        "Exception",
        "ValueError",
        "TypeError",
        "KeyError",
        "IndexError",
        "ZeroDivisionError",
        "ArithmeticError",
        "AttributeError",
        "StopIteration",
        "True",
        "False",
        "None",
    }
)

#: Referencing any of these is an immediate validation error.
FORBIDDEN_NAMES = frozenset(
    {
        "eval",
        "exec",
        "compile",
        "open",
        "input",
        "__import__",
        "globals",
        "locals",
        "vars",
        "getattr",
        "setattr",
        "delattr",
        "hasattr",
        "dir",
        "type",
        "object",
        "super",
        "memoryview",
        "classmethod",
        "staticmethod",
        "property",
        "breakpoint",
        "exit",
        "quit",
        "help",
        "id",
        "copyright",
        "credits",
        "license",
    }
)

FORBIDDEN_NODE_TYPES: tuple[type[ast.AST], ...] = (
    ast.Import,
    ast.ImportFrom,
    ast.Global,
    ast.Nonlocal,
    ast.AsyncFunctionDef,
    ast.Await,
    ast.AsyncFor,
    ast.AsyncWith,
    ast.ClassDef,
    ast.Yield,
    ast.YieldFrom,
)


@dataclass(slots=True)
class ModuleViolation:
    line: int
    message: str

    def as_dict(self) -> dict[str, Any]:
        return {"line": self.line, "message": self.message}


def validate_module_code(code: str) -> list[ModuleViolation]:
    """Return ALL violations (empty list = the code is storable/runnable)."""
    violations: list[ModuleViolation] = []
    if len(code.encode("utf-8", errors="replace")) > MAX_CODE_BYTES:
        return [ModuleViolation(0, f"module exceeds {MAX_CODE_BYTES // 1024} KB")]
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return [ModuleViolation(exc.lineno or 0, f"syntax error: {exc.msg}")]

    run_defs = [
        node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "run"
    ]
    if len(run_defs) != 1:
        violations.append(ModuleViolation(0, "define exactly one top-level `def run(inputs):`"))
    else:
        args = run_defs[0].args
        positional = list(args.posonlyargs) + list(args.args)
        if len(positional) != 1 or args.vararg or args.kwarg or args.kwonlyargs:
            violations.append(
                ModuleViolation(
                    run_defs[0].lineno,
                    "`run` must take exactly one argument (inputs)",
                )
            )

    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.Assign, ast.AnnAssign, ast.Expr)):
            violations.append(
                ModuleViolation(
                    getattr(node, "lineno", 0),
                    "top level allows only function definitions and simple assignments",
                )
            )

    for node in ast.walk(tree):
        if isinstance(node, FORBIDDEN_NODE_TYPES):
            violations.append(
                ModuleViolation(
                    getattr(node, "lineno", 0),
                    f"{type(node).__name__} is not allowed in a module",
                )
            )
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith("_"):
                violations.append(
                    ModuleViolation(
                        node.lineno,
                        f"underscore attribute '.{node.attr}' is not allowed",
                    )
                )
        elif isinstance(node, ast.Name):
            if node.id in FORBIDDEN_NAMES:
                violations.append(
                    ModuleViolation(
                        node.lineno,
                        f"'{node.id}' is not allowed in a module",
                    )
                )
            elif node.id.startswith("__"):
                violations.append(
                    ModuleViolation(
                        node.lineno,
                        f"dunder name '{node.id}' is not allowed",
                    )
                )
    return violations


# The fixed harness program. The module code and inputs arrive as one JSON
# document on stdin (data, never source interpolation); the result leaves as
# one JSON document on stdout. Builtins are the allowlist above.
_HARNESS = r"""
import json, sys
SAFE = %(safe_names)s
payload = json.loads(sys.stdin.read())
import builtins as _b
safe_builtins = {name: getattr(_b, name) for name in SAFE if hasattr(_b, name)}
namespace = {"__builtins__": safe_builtins}
try:
    exec(compile(payload["code"], "<module>", "exec"), namespace)
    run = namespace.get("run")
    if not callable(run):
        raise ValueError("module does not define run()")
    result = run(payload["inputs"])
    if not isinstance(result, dict):
        result = {"result": result}
    out = json.dumps({"ok": True, "output": result}, default=str)
except BaseException as exc:  # report, never crash the harness
    out = json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
sys.stdout.write(out)
"""


def _harness_source() -> str:
    return _HARNESS % {"safe_names": sorted(SAFE_BUILTIN_NAMES)}


def _limit_resources() -> None:  # pragma: no cover - runs in the child only
    import resource

    resource.setrlimit(resource.RLIMIT_CPU, (MODULE_CPU_SECONDS, MODULE_CPU_SECONDS + 1))
    resource.setrlimit(
        resource.RLIMIT_AS,
        (MODULE_ADDRESS_SPACE_BYTES, MODULE_ADDRESS_SPACE_BYTES),
    )
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_OUTPUT_BYTES, MAX_OUTPUT_BYTES))
    resource.setrlimit(resource.RLIMIT_NPROC, (0, 0))


async def run_module_code(
    code: str,
    inputs: dict[str, Any],
    *,
    organization_id: str | None,
    timeout: float = MODULE_WALL_TIMEOUT_SECS,
) -> dict[str, Any]:
    """Execute validated module *code* against *inputs*.

    Returns the module's output dict, or raises ``ModuleExecutionError`` with
    a user-facing message. Re-validates before running (defense in depth —
    the DB row could predate a validator tightening).

    *organization_id* has no default, so a caller that forgets it fails at
    once. It is the org of the session or of the run. The switch runs first:
    an org off ``MODULE_RUN_ORGS``, or no org, raises
    ``gateway.module_runs.ModuleRunsOff`` and spawns nothing (STU-Q15a).
    """
    refuse_unless_allowed(organization_id)
    violations = validate_module_code(code)
    if violations:
        raise ModuleExecutionError(
            "module failed validation: " + "; ".join(v.message for v in violations[:3])
        )
    payload = json.dumps({"code": code, "inputs": inputs}, default=str)
    if len(payload.encode()) > MAX_CODE_BYTES + MAX_INPUT_BYTES:
        raise ModuleExecutionError("module inputs are too large")
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-I",
        "-E",
        "-S",
        "-c",
        _harness_source(),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={},
        preexec_fn=_limit_resources,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(payload.encode()),
            timeout=timeout,
        )
    except TimeoutError:
        proc.kill()
        # Reap before returning so the transport closes inside a live loop
        # (otherwise GC raises "Event loop is closed" after asyncio.run).
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        raise ModuleExecutionError(f"module exceeded the {timeout:.0f}s execution limit") from None
    if len(stdout) > MAX_OUTPUT_BYTES:
        raise ModuleExecutionError("module output exceeds the size limit")
    if proc.returncode != 0 and not stdout:
        detail = (stderr or b"").decode(errors="replace")[:200]
        raise ModuleExecutionError(f"module process failed: {detail or 'killed'}")
    try:
        result = json.loads(stdout.decode())
    except (ValueError, UnicodeDecodeError):
        raise ModuleExecutionError("module produced non-JSON output") from None
    if not result.get("ok"):
        raise ModuleExecutionError(str(result.get("error") or "module failed"))
    output = result.get("output")
    return output if isinstance(output, dict) else {"result": output}


class ModuleExecutionError(Exception):
    """A module run failed in a way the maker should see verbatim."""
