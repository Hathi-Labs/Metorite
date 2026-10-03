"""WS43-F1 — one module starts ``docker``: the sandbox broker.

Spec ``project-docs/specs/maf_coding_engine.md`` §10, WS43-F1, and §9 WS43-S2.
The Docker socket is root on the host, so the code that may start a ``docker``
process must stay in one place that a reviewer can read.

What breaks this fence: a module under ``apps/``, ``packages/`` or ``evals/``,
outside ``orchestrator/sandbox_broker.py``, that starts a ``docker`` process.
The legacy list below may only shrink. WS-43j leaves ``mutation.py`` alone on
it.

It also pins §7.1 rule 14 at the source: the broker starts no process that is
not ``docker``, so it cannot fall back to the host.
"""
from __future__ import annotations

import ast
import re
from functools import cache
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SEAM = "apps/services/orchestrator/orchestrator/sandbox_broker.py"

#: The legacy docker callers, and why each one may stay for now.
LEGACY: dict[str, str] = {
    "apps/services/orchestrator/orchestrator/copilot_sandbox.py":
        "the Copilot CLI sandbox, OFF behind copilot_sandbox_scope. WS-43j removes it.",
    "apps/services/orchestrator/orchestrator/mutation.py":
        "the self-mutation sandbox. It stays after WS-43j.",
    "evals/coding_engine/":
        "the eval-only sandbox of WS-43a. WS-43j removes it.",
}

#: The most the legacy list may ever hold (spec §10 WS43-F1). The list only
#: shrinks: a new entry fails here, and so does an entry that no longer starts
#: docker.
LEGACY_CEILING = frozenset(LEGACY)

_PROCESS_STARTERS = frozenset({
    "run", "call", "check_call", "check_output", "Popen", "getoutput",
    "getstatusoutput", "system", "popen", "create_subprocess_exec",
    "create_subprocess_shell", "execl", "execlp", "execv", "execvp", "execvpe",
    "spawnl", "spawnlp", "spawnv", "spawnvp", "posix_spawn", "posix_spawnp",
})
_SHELL_DOCKER = re.compile(r"^\s*(sudo\s+)?docker(\.exe)?\s+\S")


def _is_docker_word(node: ast.AST) -> bool:
    """The constant ``"docker"`` (or ``docker.exe``) as one argv word."""
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value.strip() in {"docker", "docker.exe"}
    )


def _is_docker_shell(node: ast.AST) -> bool:
    """A shell string or f-string that starts with ``docker <verb>``."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return bool(_SHELL_DOCKER.match(node.value))
    if isinstance(node, ast.JoinedStr) and node.values:
        first = node.values[0]
        return (
            isinstance(first, ast.Constant)
            and isinstance(first.value, str)
            and bool(_SHELL_DOCKER.match(first.value + "x"))
        )
    return False


def _call_name(func: ast.AST) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def docker_sites(source: str) -> list[int]:
    """The line of each place in *source* that starts, or finds, ``docker``.

    - an argv list or tuple whose first word is ``docker``;
    - a call whose first argument is the word ``docker``, which covers a
      wrapper such as ``_run("docker", ...)`` and ``shutil.which("docker")``;
    - a process-starting call with a ``docker <verb>`` shell string;
    - the Docker SDK (``import docker``) and MAF's ``DockerShellTool``.
    """
    sites: list[int] = []
    for node in ast.walk(ast.parse(source)):
        line = getattr(node, "lineno", 0)
        if isinstance(node, ast.List | ast.Tuple) and node.elts and _is_docker_word(node.elts[0]):
            sites.append(line)
        elif isinstance(node, ast.Call) and node.args:
            first = node.args[0]
            if _is_docker_word(first) or (
                _call_name(node.func) in _PROCESS_STARTERS and _is_docker_shell(first)
            ):
                sites.append(line)
        elif isinstance(node, ast.Import):
            if any(a.name == "docker" or a.name.startswith("docker.") for a in node.names):
                sites.append(line)
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] == "docker" or any(
                a.name == "DockerShellTool" for a in node.names
            ):
                sites.append(line)
        elif isinstance(node, ast.Name | ast.Attribute) and _call_name(node) == "DockerShellTool":
            sites.append(line)
    return sites


def _python_files() -> list[Path]:
    out: list[Path] = []
    for root in ("apps", "packages", "evals"):
        base = REPO / root
        if base.is_dir():
            out.extend(
                p for p in base.rglob("*.py")
                if "__pycache__" not in p.parts and ".venv" not in p.parts
                and "node_modules" not in p.parts
            )
    return out


def _rel(path: Path) -> str:
    return path.relative_to(REPO).as_posix()


def _is_legacy(rel: str) -> bool:
    return any(rel == entry or (entry.endswith("/") and rel.startswith(entry)) for entry in LEGACY)


@cache
def _all_sites() -> dict[str, list[int]]:
    found: dict[str, list[int]] = {}
    for path in _python_files():
        sites = docker_sites(path.read_text(encoding="utf-8-sig"))
        if sites:
            found[_rel(path)] = sites
    return found


# ── the fence ────────────────────────────────────────────────────────────────


def test_only_the_broker_and_the_legacy_list_start_docker() -> None:
    offenders = {
        rel: lines for rel, lines in _all_sites().items()
        if rel != SEAM and not _is_legacy(rel)
    }
    assert not offenders, (
        "These modules start a docker process outside the sandbox broker "
        f"(WS43-F1): {offenders}. Ask the broker for a container and an exec "
        "instead (orchestrator/sandbox_broker.py)."
    )


def test_the_legacy_list_only_shrinks() -> None:
    assert frozenset(LEGACY) <= LEGACY_CEILING
    sites = _all_sites()
    for entry in LEGACY:
        path = REPO / entry
        if not path.exists():
            continue  # evals/coding_engine/ arrives with WS-43a
        still = [rel for rel in sites if rel == entry or rel.startswith(entry.rstrip("/") + "/")]
        assert still, (
            f"{entry} no longer starts docker. Remove it from the legacy list, "
            "so the list shrinks (WS43-F1)."
        )


def test_the_detector_finds_the_seam_itself() -> None:
    """A detector that finds nothing would pass the fence by finding nothing."""
    assert SEAM in _all_sites(), "the detector cannot see the broker's own docker site"


def test_the_detector_sees_each_shape_of_a_docker_start() -> None:
    hits = [
        'subprocess.run(["docker", "run", "x"])',
        'asyncio.create_subprocess_exec("docker", "exec", name)',
        'cmd = ("docker", "ps")',
        'os.system("docker rm -f x")',
        'subprocess.check_output(f"docker exec {name} ls", shell=True)',
        'shutil.which("docker")',
        "import docker",
        "from docker import from_env",
        "from agent_framework_tools import DockerShellTool",
    ]
    for source in hits:
        assert docker_sites(source), source
    misses = [
        'SKILLS = {"aws", "docker", "kubernetes"}',
        '_log.warning("docker is down")',
        'x = ["python", "docker"]',
    ]
    for source in misses:
        assert not docker_sites(source), source


# ── §7.1 rule 14 at the source: no host fallback ────────────────────────────


def _seam_tree() -> ast.Module:
    return ast.parse((REPO / SEAM).read_text(encoding="utf-8"))


def test_the_broker_starts_no_process_but_docker() -> None:
    """Every process the broker starts is ``docker``. No other way exists.

    It imports no ``subprocess`` and never calls a shell. Each
    ``create_subprocess_exec`` lives in ``DockerCLI`` and passes the docker
    binary first, so no code path can run a command on the host.
    """
    tree = _seam_tree()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert all(a.name not in {"subprocess", "pty"} for a in node.names)
        if isinstance(node, ast.ImportFrom):
            assert node.module not in {"subprocess", "pty"}
        if isinstance(node, ast.Call):
            assert _call_name(node.func) not in {
                "create_subprocess_shell", "system", "popen", "Popen", "spawnv",
                "execv", "execvp", "check_output", "getoutput",
            }, ast.unparse(node)
    docker_cli = next(
        n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "DockerCLI"
    )
    inside = {
        id(n) for n in ast.walk(docker_cli)
        if isinstance(n, ast.Call) and _call_name(n.func) == "create_subprocess_exec"
    }
    everywhere = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call) and _call_name(n.func) == "create_subprocess_exec"
    ]
    assert everywhere, "the broker must start docker somewhere"
    assert {id(n) for n in everywhere} == inside, "a process starts outside DockerCLI"
    for call in everywhere:
        first = ast.unparse(call.args[0])
        assert first == "self._binary()", f"argv[0] is {first}, not the docker binary"
    binary = next(
        n for n in docker_cli.body if isinstance(n, ast.FunctionDef) and n.name == "_binary"
    )
    assert 'which("docker")' in ast.unparse(binary).replace("'", '"')
