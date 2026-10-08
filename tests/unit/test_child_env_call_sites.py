"""WS-49 BH-1 — each refactored spawn gets its named extras and nothing else.

``test_child_env_seam.py`` (BH-F1) proves each spawn takes its env from
``acb_common.child_env``. This file drives the call sites that add a name, and
captures the env each one hands to the child. Each must hold its extras, and
no canary secret from ``os.environ``.
"""
from __future__ import annotations

import ast
import asyncio
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[2]
CANARIES = {
    "DATABASE_URL": "postgresql://u:bh1canary@h/db",
    "GATEWAY_INTERNAL_TOKEN": "bh1canary-internal",
    "OPENAI_API_KEY": "sk-bh1canary",
    "BH1_CANARY_SECRET": "bh1canary",
    "GITHUB_TOKEN": "ghp_bh1canary",
}


@pytest.fixture(autouse=True)
def canaries(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in CANARIES.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("UV_CACHE_DIR", "/var/cache/acb-gateway/uv")


def _clean(env: dict[str, str], *extras: str) -> None:
    assert env is not None, "the child inherited the whole gateway env (env=None)"
    leaked = sorted(k for k, v in env.items() if "bh1canary" in v and k not in extras)
    assert not leaked, f"secrets reached the child: {leaked}"
    for name in CANARIES:
        if name not in extras:
            assert name not in env, name
    assert "PATH" in env


class _Proc:
    returncode = 0
    stdout = ""
    stderr = ""

    async def communicate(self, *_: Any) -> tuple[bytes, bytes]:
        return b"", b""


def test_pdf_render_keeps_the_python_names(monkeypatch: pytest.MonkeyPatch) -> None:
    from gateway import pdf_render

    monkeypatch.setenv("PYTHONPATH", "/srv/py")
    monkeypatch.setenv("VIRTUAL_ENV", "/srv/venv")
    env = pdf_render._child_env()
    _clean(env)
    assert env["PYTHONPATH"] == "/srv/py"
    assert env["VIRTUAL_ENV"] == "/srv/venv"


def test_run_script_gets_declared_credentials_only(monkeypatch: pytest.MonkeyPatch) -> None:
    from acb_skills import code_tools

    monkeypatch.setenv("PYTHONPATH", "/srv/py")
    env = code_tools._script_env()
    _clean(env)
    assert env["PYTHONPATH"] == "/srv/py"
    assert env["PYTHONUNBUFFERED"] == "1"
    assert env["UV_CACHE_DIR"] == "/var/cache/acb-gateway/uv"


def test_loader_git_gets_the_no_prompt_names(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from acb_skills import loader

    seen: list[dict[str, str]] = []

    def fake_run(*_: Any, **kw: Any) -> Any:
        seen.append(kw.get("env"))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(loader.subprocess, "run", fake_run)
    loader._run_git(["status"], cwd=tmp_path)
    env = seen[0]
    _clean(env)
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert env["GCM_INTERACTIVE"] == "never"
    assert env["PYTHONUTF8"] == "1"


async def test_install_dependency_keeps_the_uv_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    from acb_skills import dep_tools

    seen: list[dict[str, str]] = []

    def fake_run(*_: Any, **kw: Any) -> Any:
        seen.append(kw.get("env"))
        return SimpleNamespace(returncode=1, stdout="", stderr="stub")

    monkeypatch.setattr(subprocess, "run", fake_run)
    await dep_tools.install_dependency("six")
    _clean(seen[0])
    assert seen[0]["UV_CACHE_DIR"] == "/var/cache/acb-gateway/uv"


async def test_t2_build_gets_its_two_dirs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from gateway.routes.apps import files

    seen: list[dict[str, str]] = []

    async def fake_exec(*_: Any, **kw: Any) -> _Proc:
        seen.append(kw.get("env"))
        return _Proc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(files, "t2_vendor_dir", lambda: Path("/srv/vendor/t2-react"))
    monkeypatch.setattr(
        files, "get_settings", lambda: SimpleNamespace(agents_clone_dir="/srv/agents")
    )
    ok, _ = await files._run_build_t2(tmp_path, tmp_path / "build_t2.mjs")
    assert ok
    env = seen[0]
    _clean(env)
    assert env["CUSTOM_APPS_T2_VENDOR_DIR"] == str(Path("/srv/vendor/t2-react"))
    assert env["AGENTS_CLONE_DIR"] == "/srv/agents"


async def test_gh_auth_status_alone_gets_the_gh_names(monkeypatch: pytest.MonkeyPatch) -> None:
    from gateway.routes import integrations

    monkeypatch.setenv("GH_TOKEN", "gho_bh1_status")
    seen: list[tuple[list[str], dict[str, str]]] = []

    def fake_run(args: list[str], **kw: Any) -> Any:
        seen.append((args, kw.get("env")))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(integrations.subprocess, "run", fake_run)
    monkeypatch.setattr(integrations, "get_settings", lambda: SimpleNamespace(github_token=""))
    await integrations.github_account(user=SimpleNamespace(email="a@b.c"))
    args, env = seen[0]
    assert args == ["gh", "auth", "status"]
    _clean(env, "GH_TOKEN", "GITHUB_TOKEN")
    assert env["GH_TOKEN"] == "gho_bh1_status"
    assert env["GITHUB_TOKEN"] == "ghp_bh1canary"  # gh reads both names (spec §2.2)

    seen.clear()
    monkeypatch.setattr(integrations, "_refuse_provider_key_without_byok", lambda _keys: None)
    with pytest.raises(Exception):  # noqa: B017 - gh prints no token, so it refuses
        await integrations.github_connect_cli(user=SimpleNamespace(email="a@b.c"))
    args, env = seen[0]
    assert args == ["gh", "auth", "token"]
    _clean(env)
    assert "GH_TOKEN" not in env


async def test_docker_cli_gets_the_daemon_names(monkeypatch: pytest.MonkeyPatch) -> None:
    from orchestrator import sandbox_broker

    monkeypatch.setenv("DOCKER_HOST", "unix:///run/docker.sock")
    seen: list[dict[str, str]] = []

    async def fake_exec(*_: Any, **kw: Any) -> _Proc:
        seen.append(kw.get("env"))
        return _Proc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    cli = sandbox_broker.DockerCLI()
    cli._binary = lambda: "/usr/bin/docker"  # type: ignore[method-assign]
    await cli._spawn(["ps"])
    _clean(seen[0])
    assert seen[0]["DOCKER_HOST"] == "unix:///run/docker.sock"


def test_the_approval_rebase_adds_only_the_sequence_editor() -> None:
    """The rebase of the approval flow once copied ``os.environ``. It now
    adds one name by value. Read at the source: the flow needs a live clone."""
    path = REPO / "apps/services/gateway/gateway/routes/agent.py"
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    rebase = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and any(isinstance(a, ast.Constant) and a.value == "rebase" for a in node.args)
    ]
    assert len(rebase) == 1
    env = next(k.value for k in rebase[0].keywords if k.arg == "env")
    assert ast.unparse(env) == "child_env(extra={'GIT_SEQUENCE_EDITOR': 'true'})"
