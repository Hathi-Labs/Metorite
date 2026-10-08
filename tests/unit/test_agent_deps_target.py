"""BH-F6 — WS-49 BH-7: agent installs leave the shared venv, and the T2 vendor
install runs no scripts.

Spec: project-docs/specs/box_hardening.md §5 BH-7 "Fences" and §8.

What breaks this file:

- an install command in ``acb_skills`` with ``--python sys.executable`` and no
  ``--target``, or with no ``-c``;
- a freeze with no ``--exclude-editable``, or a constraints file outside
  ``/var/cache/acb-gateway/``;
- a deps hash with no target path (spec Q3b);
- a ``.pth`` file or a ``sitecustomize`` left in agent-site, or a
  ``site.addsitedir`` of it;
- ``PYTHONPATH`` or ``CUSTOM_APPS_T2_VENDOR_DIR`` that does not reach
  ``_script_env`` and ``copilot_env()`` by value, or a unit that sets
  ``PYTHONPATH``;
- a T2 step of ``vps_apply.sh`` with no ``--ignore-scripts``, no
  ``rm -f /opt/acb/t2-vendor/.npmrc``, or a path read from ``.env``;
- the ``.env`` strip or the drop-in installer after the first service restart.

The bash tests source the ``bh7 helpers`` block of ``vps_apply.sh`` and run it
with a stub ``sudo`` and a stub ``systemctl`` on ``PATH``.
"""
from __future__ import annotations

import ast
import asyncio
import hashlib
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from acb_common import child_env as seam

ROOT = Path(__file__).resolve().parents[2]
APPLY = ROOT / "scripts" / "vps_apply.sh"
SKILLS = ROOT / "packages" / "acb_skills" / "acb_skills"
UNITS = ROOT / "deploy" / "hostinger"
AGENT_SITE_CONF = UNITS / "acb-gateway.service.d" / "40-agent-site.conf"

BOX_AGENT_SITE = "/var/lib/acb-gateway/agent-site"
BOX_T2_VENDOR = "/opt/acb/t2-vendor"
BOX_CONSTRAINTS = "/var/cache/acb-gateway/constraints.txt"

#: The real subprocess.run, for a child that must really run.
_real_run = subprocess.run

FREEZE_OUT = "six==1.17.0\n-e file:///opt/acb/app/packages/acb_common\nidna==3.20\n"


# ── The fixture: agent-site in a temp dir, and a recording subprocess.run ──


class _Done:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


@pytest.fixture
def site(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    from acb_skills import agent_site

    state = tmp_path / "var-lib-acb-gateway"
    cache = tmp_path / "var-cache-acb-gateway"
    state.mkdir()
    cache.mkdir()
    monkeypatch.setattr(agent_site, "STATE_ROOT", state)
    monkeypatch.setattr(agent_site, "AGENT_SITE", state / "agent-site")
    monkeypatch.setattr(agent_site, "CACHE_ROOT", cache)
    monkeypatch.setattr(agent_site, "CONSTRAINTS", cache / "constraints.txt")
    monkeypatch.setattr(agent_site, "find_uv", lambda: "/stub/uv")
    monkeypatch.setattr(agent_site, "_frozen", {})
    monkeypatch.setattr(agent_site, "_pruned_for", set())
    monkeypatch.setattr(sys, "path", list(sys.path))

    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kw: Any) -> _Done:
        assert "env" in kw, "every spawn passes env= (BH-F1)"
        calls.append(list(cmd))
        if cmd[1:3] == ["pip", "freeze"]:
            return _Done(stdout=FREEZE_OUT)
        return _Done()

    monkeypatch.setattr(subprocess, "run", fake_run)
    return SimpleNamespace(mod=agent_site, state=state, cache=cache, calls=calls)


def _installs(calls: list[list[str]]) -> list[list[str]]:
    return [c for c in calls if c[1:3] == ["pip", "install"]]


#: Names that no venv holds, so the install is not skipped (fix round 1, P1).
FAKE = "bh7-fake-agent-pkg==1.0"
FAKE_TWO = "bh7-fake-other>=2"


def _agent(tmp_path: Path, *, req: str = FAKE + "\n", deps: list[str] | None = None) -> Path:
    d = tmp_path / "agent"
    (d / ".git").mkdir(parents=True)
    if req:
        (d / "requirements.txt").write_text(req, encoding="utf-8")
    if deps:
        body = ", ".join(f'"{x}"' for x in deps)
        (d / "pyproject.toml").write_text(
            f'[project]\nname = "a"\ndependencies = [{body}]\n', encoding="utf-8",
        )
    return d


def _settings() -> SimpleNamespace:
    return SimpleNamespace(agent_deps_allow_source_builds=False)


# ── The install target and the constraints ─────────────────────────────


def test_the_box_paths_are_the_spec_paths() -> None:
    from acb_skills import agent_site

    assert seam.AGENT_SITE_DIR == BOX_AGENT_SITE
    assert str(agent_site.AGENT_SITE).replace("\\", "/").endswith("/var/lib/acb-gateway/agent-site")
    assert agent_site.CONSTRAINTS.as_posix().endswith(BOX_CONSTRAINTS)
    assert agent_site.CONSTRAINTS.parent == agent_site.CACHE_ROOT


def test_the_loader_installs_into_agent_site_with_constraints(
    site: SimpleNamespace, tmp_path: Path
) -> None:
    from acb_skills.loader import _install_agent_deps

    agent = _agent(tmp_path, deps=[FAKE_TWO])
    _install_agent_deps(agent, _settings())
    installs = _installs(site.calls)
    assert len(installs) == 1, site.calls
    # Nothing dropped: the agent's OWN file, as before (fix round 2, P2-a).
    assert installs[0][installs[0].index("-r") + 1] == str(agent / "requirements.txt")
    assert installs[0][-1] == FAKE_TWO
    for cmd in installs:
        assert cmd[cmd.index("--target") + 1] == str(site.mod.AGENT_SITE)
        assert cmd[cmd.index("-c") + 1] == str(site.mod.CONSTRAINTS)
        assert cmd[cmd.index("--python") + 1] == sys.executable
        assert cmd[cmd.index("--only-binary") + 1] == ":all:"
    assert site.mod.AGENT_SITE.is_dir()


def test_install_dependency_installs_into_agent_site_with_constraints(
    site: SimpleNamespace,
) -> None:
    from acb_skills import dep_tools

    msg = asyncio.run(dep_tools.install_dependency("bh7-fake-agent-pkg"))
    assert msg.startswith("Installed into the agent package dir"), msg
    (cmd,) = _installs(site.calls)
    assert cmd[cmd.index("--target") + 1] == str(site.mod.AGENT_SITE)
    assert cmd[cmd.index("-c") + 1] == str(site.mod.CONSTRAINTS)
    assert cmd[-1] == "bh7-fake-agent-pkg"


def test_the_freeze_excludes_editables_and_writes_under_var_cache(
    site: SimpleNamespace,
) -> None:
    """B7-1. A plain freeze gives `-e file:///` lines, and uv refuses them as
    constraints. So the freeze holds --exclude-editable, and no option line
    reaches the file."""
    cmd = site.mod.freeze_command("/stub/uv")
    assert cmd[1:4] == ["pip", "freeze", "--exclude-editable"]
    assert cmd[cmd.index("--python") + 1] == sys.executable
    site.mod.prepare("/stub/uv")
    text = site.mod.CONSTRAINTS.read_text(encoding="utf-8")
    assert text.splitlines() == ["six==1.17.0", "idna==3.20"]


def test_one_freeze_per_process(site: SimpleNamespace) -> None:
    site.mod.prepare("/stub/uv")
    site.mod.prepare("/stub/uv")
    assert sum(1 for c in site.calls if c[1:3] == ["pip", "freeze"]) == 1


def test_a_failed_freeze_installs_nothing(
    site: SimpleNamespace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from acb_skills.loader import _install_agent_deps

    def fail_run(cmd: list[str], **_: Any) -> _Done:
        site.calls.append(list(cmd))
        return _Done(returncode=2, stderr="freeze broke")

    monkeypatch.setattr(subprocess, "run", fail_run)
    _install_agent_deps(_agent(tmp_path), _settings())
    assert _installs(site.calls) == []


def _list_strings(node: ast.List) -> list[str]:
    return [e.value for e in node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]


def test_no_install_command_in_acb_skills_writes_the_venv() -> None:
    """Each list literal that names `pip install` holds --target and -c. No
    `python -m pip` fallback is left. Each freeze holds --exclude-editable."""
    installs = 0
    for path in sorted(SKILLS.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.List):
                continue
            words = _list_strings(node)
            where = f"{path.relative_to(ROOT)}:{node.lineno}"
            if "pip" in words and "install" in words:
                installs += 1
                assert "--target" in words, f"{where}: an install with no --target"
                assert "-c" in words, f"{where}: an install with no -c"
                assert "-m" not in words, f"{where}: a python -m pip install"
            if "pip" in words and "freeze" in words:
                assert "--exclude-editable" in words, f"{where}: a freeze with no --exclude-editable"
    assert installs == 1, "agent_site.install_command must be the ONE install command"


# ── The hash (Q3b, acceptance 3) ─────────────────────────────────────


def test_an_agent_installed_before_bh7_installs_again(
    site: SimpleNamespace, tmp_path: Path
) -> None:
    """The old marker hashed only the declared deps. The new hash holds the
    target path, so the old marker never matches and the agent installs again
    into agent-site, once."""
    from acb_skills.loader import _install_agent_deps

    agent = _agent(tmp_path)
    req = (agent / "requirements.txt").read_text(encoding="utf-8")
    old = hashlib.sha256(req.encode("utf-8")).hexdigest()
    marker = agent / ".git" / "acb-deps-hash"
    marker.write_text(old, encoding="utf-8")

    _install_agent_deps(agent, _settings())
    assert len(_installs(site.calls)) == 1
    new = marker.read_text(encoding="utf-8").strip()
    assert new != old

    site.calls.clear()
    _install_agent_deps(agent, _settings())
    assert _installs(site.calls) == [], "an unchanged set must not install again"


def test_the_hash_changes_with_the_target(
    site: SimpleNamespace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from acb_skills.loader import _install_agent_deps

    agent = _agent(tmp_path)
    _install_agent_deps(agent, _settings())
    first = (agent / ".git" / "acb-deps-hash").read_text(encoding="utf-8")
    monkeypatch.setattr(site.mod, "AGENT_SITE", site.state / "other-site")
    site.calls.clear()
    _install_agent_deps(agent, _settings())
    assert len(_installs(site.calls)) == 1
    assert (agent / ".git" / "acb-deps-hash").read_text(encoding="utf-8") != first


# ── The guard: no agent-site means no install, and never the venv ─────


@pytest.mark.parametrize("missing", ["state", "cache"])
def test_no_unit_dirs_means_a_refusal(
    site: SimpleNamespace, tmp_path: Path, missing: str
) -> None:
    from acb_skills import dep_tools
    from acb_skills.loader import _install_agent_deps, read_dep_status

    shutil.rmtree(site.state if missing == "state" else site.cache)
    agent = _agent(tmp_path)
    _install_agent_deps(agent, _settings())
    msg = asyncio.run(dep_tools.install_dependency("bh7-fake-agent-pkg"))
    assert site.calls == [], "no freeze and no install without the unit dirs"
    assert msg.startswith("Refused to install bh7-fake-agent-pkg"), msg
    status = read_dep_status(agent)
    assert status is not None and status["ok"] is False
    assert status["error"].startswith("install skipped:")
    assert not (agent / ".git" / "acb-deps-hash").exists()


# ── No .pth and no sitecustomize ───────────────────────────────────────


def test_scrub_removes_start_up_files_and_keeps_packages(site: SimpleNamespace) -> None:
    s = site.mod.AGENT_SITE
    (s / "usercustomize").mkdir(parents=True)
    (s / "six.py").write_text("x = 1\n", encoding="utf-8")
    (s / "pkg").mkdir()
    (s / "distutils-precedence.pth").write_text("import os\n", encoding="utf-8")
    (s / "sitecustomize.py").write_text("import os\n", encoding="utf-8")
    removed = site.mod.scrub()
    assert sorted(removed) == ["distutils-precedence.pth", "sitecustomize.py", "usercustomize"]
    assert sorted(p.name for p in s.iterdir()) == ["pkg", "six.py"]


def test_each_install_path_scrubs(site: SimpleNamespace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A package that ships a .pth file or a sitecustomize leaves none."""
    from acb_skills import dep_tools
    from acb_skills.loader import _install_agent_deps

    def planting_run(cmd: list[str], **_: Any) -> _Done:
        site.calls.append(list(cmd))
        if cmd[1:3] == ["pip", "freeze"]:
            return _Done(stdout=FREEZE_OUT)
        target = Path(cmd[cmd.index("--target") + 1])
        target.mkdir(parents=True, exist_ok=True)
        (target / "evil.pth").write_text("import os\n", encoding="utf-8")
        (target / "sitecustomize.py").write_text("import os\n", encoding="utf-8")
        return _Done()

    monkeypatch.setattr(subprocess, "run", planting_run)
    _install_agent_deps(_agent(tmp_path), _settings())
    assert list(site.mod.AGENT_SITE.iterdir()) == []
    asyncio.run(dep_tools.install_dependency("bh7-fake-agent-pkg"))
    assert list(site.mod.AGENT_SITE.iterdir()) == []


def test_agent_site_is_never_a_site_dir() -> None:
    """site.addsitedir would run each .pth file in agent-site."""
    for path in sorted(SKILLS.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            name = node.attr if isinstance(node, ast.Attribute) else getattr(node, "id", "")
            assert name != "addsitedir", f"{path.name}:{getattr(node, 'lineno', '?')}"


# ── Acceptance 1 and 2: the gateway and a run_script child ────────────


def _probe_site(tmp_path: Path) -> Path:
    s = tmp_path / "agent-site"
    (s / "bh7_probe_pkg").mkdir(parents=True)
    (s / "bh7_probe_pkg" / "__init__.py").write_text('WHERE = "agent-site"\n', encoding="utf-8")
    # A copy of a venv package, at another version. The gateway must not take it.
    (s / "idna").mkdir()
    (s / "idna" / "__init__.py").write_text('__version__ = "0.0.0-agent-site"\n', encoding="utf-8")
    return s


def test_acceptance_1_the_package_imports_in_the_gateway_and_in_a_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from acb_skills import agent_site, code_tools

    s = _probe_site(tmp_path)
    monkeypatch.setattr(agent_site, "AGENT_SITE", s)
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.delitem(sys.modules, "bh7_probe_pkg", raising=False)
    agent_site.ensure_on_sys_path()
    import bh7_probe_pkg  # type: ignore[import-not-found]

    assert bh7_probe_pkg.WHERE == "agent-site"

    monkeypatch.setitem(seam.AGENT_PATH_VALUES, "PYTHONPATH", str(s))
    env = code_tools._script_env()
    r = subprocess.run(
        [sys.executable, "-c", "import bh7_probe_pkg; print(bh7_probe_pkg.WHERE)"],
        capture_output=True, text=True, env=env, timeout=60,
    )
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "agent-site"


def test_acceptance_2_a_venv_package_wins_in_the_gateway(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """agent-site goes AFTER the venv, so the venv's idna wins over a copy."""
    from acb_skills import agent_site

    s = _probe_site(tmp_path)
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(agent_site, "AGENT_SITE", s)
    agent_site.ensure_on_sys_path()
    agent_site.ensure_on_sys_path()
    assert sys.path.count(str(s)) == 1
    assert sys.path[-1] == str(s)
    site_packages = [i for i, p in enumerate(sys.path) if p.endswith("site-packages")]
    assert site_packages and max(site_packages) < sys.path.index(str(s))

    code = textwrap.dedent(f"""
        from pathlib import Path
        from acb_skills import agent_site
        agent_site.AGENT_SITE = Path({str(s)!r})
        agent_site.ensure_on_sys_path()
        import idna, bh7_probe_pkg
        print(idna.__version__)
    """)
    env = {**os.environ}
    env.pop("PYTHONPATH", None)
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, timeout=120)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() != "0.0.0-agent-site"


def test_acceptance_2_a_child_takes_the_venv_version(site: SimpleNamespace, tmp_path: Path) -> None:
    """In a child, PYTHONPATH goes before the venv. So the version must be the
    same: the install pins every venv package with -c (B7-1)."""
    from acb_skills.loader import _install_agent_deps

    _install_agent_deps(_agent(tmp_path), _settings())
    (cmd,) = _installs(site.calls)
    pins = Path(cmd[cmd.index("-c") + 1]).read_text(encoding="utf-8").splitlines()
    assert "idna==3.20" in pins and "six==1.17.0" in pins


# ── PYTHONPATH and the T2 vendor dir, by value (Q3a, Q3d) ─────────────


def test_both_agent_children_get_the_two_paths_by_value(monkeypatch: pytest.MonkeyPatch) -> None:
    from acb_skills import code_tools

    monkeypatch.setenv("PYTHONPATH", "/srv/py")
    monkeypatch.setenv("CUSTOM_APPS_T2_VENDOR_DIR", "/home/acb/.acb/agents/vendor/t2-react")
    assert seam.AGENT_PATH_VALUES == {
        "PYTHONPATH": BOX_AGENT_SITE,
        "CUSTOM_APPS_T2_VENDOR_DIR": BOX_T2_VENDOR,
    }
    for env in (seam.copilot_env(), code_tools._script_env()):
        assert env["PYTHONPATH"] == BOX_AGENT_SITE
        assert env["CUSTOM_APPS_T2_VENDOR_DIR"] == BOX_T2_VENDOR
    # A child that is not an agent child keeps the gateway's own value.
    assert seam.child_env()["PYTHONPATH"] == "/srv/py"


def _conf_lines(p: Path) -> list[str]:
    return [
        ln.strip() for ln in p.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.strip().startswith(("#", ";"))
    ]


def test_no_unit_sets_pythonpath() -> None:
    files = [*UNITS.glob("*.service"), *UNITS.glob("*.service.d/*.conf"), *UNITS.glob("rollback/*.conf")]
    assert AGENT_SITE_CONF in files
    for f in files:
        for ln in _conf_lines(f):
            assert "PYTHONPATH" not in ln, f"{f.name} sets PYTHONPATH: {ln}"


def test_one_t2_vendor_value_everywhere() -> None:
    """Acceptance 4. The route build reads it from the unit env
    (40-agent-site.conf). The in-chat build gets it from copilot_env(). The
    deploy installs it at the same literal."""
    assert f"Environment=CUSTOM_APPS_T2_VENDOR_DIR={BOX_T2_VENDOR}" in _conf_lines(AGENT_SITE_CONF)
    assert seam.T2_VENDOR_DIR == BOX_T2_VENDOR
    assert f'T2_VENDOR_DIR="{BOX_T2_VENDOR}"' in _t2_step()


def test_the_route_build_reads_the_unit_value(monkeypatch: pytest.MonkeyPatch) -> None:
    from acb_common.settings import get_settings
    from gateway.routes.apps._common import t2_vendor_dir

    monkeypatch.setenv("CUSTOM_APPS_T2_VENDOR_DIR", BOX_T2_VENDOR)
    get_settings.cache_clear()
    try:
        assert t2_vendor_dir() == Path(BOX_T2_VENDOR)
    finally:
        get_settings.cache_clear()


# ── vps_apply.sh: the T2 step ─────────────────────────────────────────


def _apply_lines() -> list[str]:
    return APPLY.read_text(encoding="utf-8").splitlines()


def _code(lines: list[str]) -> list[str]:
    return [ln for ln in lines if ln.strip() and not ln.strip().startswith("#")]


def _t2_step() -> str:
    lines = _apply_lines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith('echo "==> Provisioning T2'))
    end = next(i for i, ln in enumerate(lines) if i > start and ln.startswith("# ── Build a Next.js app"))
    return "\n".join(_code(lines[start:end]))


def test_the_t2_step_runs_no_script_and_reads_no_env() -> None:
    step = _t2_step()
    npm = [ln for ln in step.splitlines() if "npm install" in ln]
    assert len(npm) == 1, npm
    assert "--ignore-scripts" in npm[0]
    assert "--userconfig /dev/null" in npm[0]
    assert "rm -f /opt/acb/t2-vendor/.npmrc" in step
    assert step.index("rm -f /opt/acb/t2-vendor/.npmrc") < step.index("npm install")
    for word in ("ENV_FILE", ".env", "grep", "AGENTS_CLONE_DIR", "CUSTOM_APPS_T2_VENDOR_DIR", "HOME"):
        assert word not in step, f"the T2 step reads {word}"


# ── vps_apply.sh: the order (B7-2, B7-3) ──────────────────────────────


def _first(lines: list[str], needle: str) -> int:
    return next(i for i, ln in enumerate(lines) if needle in ln)


def test_the_strip_and_the_installer_run_before_any_restart() -> None:
    lines = _apply_lines()
    code = [(i, ln) for i, ln in enumerate(lines) if ln.strip() and not ln.strip().startswith("#")]
    strip = next(i for i, ln in code if ln.strip() == 'strip_t2_vendor_env_line "$ENV_FILE"')
    install = next(i for i, ln in code if ln.strip() == 'install_dropins "$APP_DIR/deploy/hostinger"')
    a, b = lines.index("# >>> bh7 helpers"), lines.index("# <<< bh7 helpers")
    # Any restart of any unit, outside the definitions of the helpers.
    first_restart = next(i for i, ln in code if "systemctl restart" in ln and not a < i < b)
    gateway = next(i for i, ln in code if ln.strip() == "sudo systemctl restart acb-gateway")
    assert strip < install < first_restart <= gateway
    # The BO-23 loop stays AFTER the gateway restart, on purpose (B7-2).
    assert gateway < _first(lines, 'echo "==> Syncing systemd units (BO-23)"')
    stale = next(i for i, ln in code if ln.strip() == 'restart_stale_dropin_units "$APP_DIR/deploy/hostinger"')
    assert _first(lines, 'echo "==> Syncing systemd units (BO-23)"') < stale
    assert stale < _first(lines, 'record_applied_sha "$(git')


# ── vps_apply.sh: the strip, run for real ─────────────────────────────


def _bash() -> str | None:
    b = shutil.which("bash")
    if not b or "system32" in b.lower():
        return None
    return b


needs_bash = pytest.mark.skipif(_bash() is None, reason="needs a POSIX bash")


def _helpers(tmp: Path) -> Path:
    lines = _apply_lines()
    a = lines.index("# >>> bh7 helpers")
    b = lines.index("# <<< bh7 helpers")
    out = tmp / "bh7_helpers.sh"
    out.write_text("\n".join(lines[a:b + 1]) + "\n", encoding="utf-8", newline="\n")
    return out


def _sh(path: Path) -> str:
    return path.as_posix()


@needs_bash
def test_the_strip_removes_the_line_warns_and_prints_no_value(tmp_path: Path) -> None:
    env = tmp_path / "dot.env"
    env.write_text(
        "A=1\nCUSTOM_APPS_T2_VENDOR_DIR=/home/acb/canary-t2\n"
        "export CUSTOM_APPS_T2_VENDOR_DIR=/srv/canary-two\nCUSTOM_APPS_ROOT=/keep\nB=2\n",
        encoding="utf-8", newline="\n",
    )
    script = f'set -e; source "{_sh(_helpers(tmp_path))}"; strip_t2_vendor_env_line "{_sh(env)}"'
    r = subprocess.run([_bash(), "-c", script], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert env.read_text(encoding="utf-8").splitlines() == ["A=1", "CUSTOM_APPS_ROOT=/keep", "B=2"]
    assert "WARN BH-7: removed a CUSTOM_APPS_T2_VENDOR_DIR line" in r.stdout
    assert "canary" not in r.stdout + r.stderr


@needs_bash
def test_the_strip_is_silent_with_no_line(tmp_path: Path) -> None:
    env = tmp_path / "dot.env"
    env.write_text("A=1\n", encoding="utf-8", newline="\n")
    script = f'set -e; source "{_sh(_helpers(tmp_path))}"; strip_t2_vendor_env_line "{_sh(env)}"'
    r = subprocess.run([_bash(), "-c", script], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert r.stdout == ""
    assert env.read_text(encoding="utf-8") == "A=1\n"


# ── Fix round 1, P1: a dependency the venv provides is never installed ─────


def _is_editable(name: str) -> bool:
    import importlib.metadata
    import json

    raw = importlib.metadata.distribution(name).read_text("direct_url.json") or "{}"
    return bool(json.loads(raw).get("dir_info", {}).get("editable"))


def test_an_editable_workspace_member_is_skipped(site: SimpleNamespace, tmp_path: Path) -> None:
    """projects-assistant declares skill-projects. It is an editable member of
    the workspace, and PyPI answers 404 for it. It must never reach uv."""
    from acb_skills.loader import _install_agent_deps

    assert _is_editable("skill-projects"), "the test venv must hold skill-projects as editable"
    assert site.mod.venv_version("Skill_Projects") is not None, "names are normalised"
    agent = _agent(tmp_path, deps=["skill-projects", FAKE_TWO])
    _install_agent_deps(agent, _settings())
    (cmd,) = _installs(site.calls)
    assert "skill-projects" not in cmd
    assert cmd[cmd.index("-r") + 1] == str(agent / "requirements.txt")
    assert cmd[-1] == FAKE_TWO


def test_a_set_the_venv_provides_writes_the_marker_and_runs_no_uv(
    site: SimpleNamespace, tmp_path: Path
) -> None:
    from acb_skills.loader import _install_agent_deps, read_dep_status

    agent = _agent(tmp_path, req="idna>=3\n", deps=["Skill_Projects"])
    _install_agent_deps(agent, _settings())
    assert site.calls == [], "no freeze and no install: the venv provides it all"
    assert (agent / ".git" / "acb-deps-hash").is_file()
    status = read_dep_status(agent)
    assert status is not None and status["ok"] is True


def test_install_dependency_skips_what_the_venv_holds(site: SimpleNamespace) -> None:
    from acb_skills import dep_tools

    msg = asyncio.run(dep_tools.install_dependency("idna skill-projects"))
    assert site.calls == []
    assert msg.startswith("Nothing was installed. Already provided by the platform: idna"), msg
    msg = asyncio.run(dep_tools.install_dependency("idna bh7-fake-agent-pkg"))
    (cmd,) = _installs(site.calls)
    assert cmd[-1] == "bh7-fake-agent-pkg"
    assert "Already provided by the platform: idna" in msg


# ── Fix round 1, P2: no copy in agent-site shadows the venv ──────────────


def _fake_dist(site_dir: Path, name: str, version: str, files: dict[str, str]) -> None:
    # A wheel names the dir with "_" for "-" (PEP 427), as importlib expects.
    info = site_dir / f"{name.replace('-', '_')}-{version}.dist-info"
    info.mkdir(parents=True)
    (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n\n",
                                   encoding="utf-8")
    rows = []
    for rel, body in files.items():
        f = site_dir / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(body, encoding="utf-8")
        rows.append(f"{rel},,")
    rows += [f"{info.name}/METADATA,,", f"{info.name}/RECORD,,"]
    (info / "RECORD").write_text("\n".join(rows) + "\n", encoding="utf-8")


def test_prune_removes_a_venv_copy_by_its_record(site: SimpleNamespace) -> None:
    s = site.mod.AGENT_SITE
    _fake_dist(s, "idna", "0.0.1", {"idna/__init__.py": "__version__ = '0.0.1'\n",
                                   "nsx/from_idna.py": "x = 1\n"})
    (s / "idna" / "__pycache__").mkdir()
    (s / "idna" / "__pycache__" / "x.pyc").write_bytes(b"\0")
    _fake_dist(s, "bh7-only-here", "1.0", {"bh7_only_here/__init__.py": "x = 1\n",
                                          "nsx/from_agent.py": "y = 2\n"})
    # The gateway has agent-site on sys.path. The lookup must still skip it,
    # or the prune would remove what only the agent installed.
    site.mod.ensure_on_sys_path()
    removed = site.mod.prune_venv_duplicates()
    assert removed == ["idna"]
    assert not (s / "idna").exists()
    assert not (s / "idna-0.0.1.dist-info").exists()
    assert (s / "bh7_only_here" / "__init__.py").is_file()
    assert (s / "bh7_only_here-1.0.dist-info").is_dir()
    assert sorted(p.name for p in (s / "nsx").iterdir()) == ["from_agent.py"]


def test_after_a_venv_bump_a_child_takes_the_venv_version(
    site: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """agent-site holds idna 0.0.1, an old copy from before a venv bump. The
    first prepare() of the new venv state prunes it, so a run_script child
    imports the venv's idna, not the copy."""
    import idna as venv_idna
    from acb_skills import code_tools

    s = site.mod.AGENT_SITE
    _fake_dist(s, "idna", "0.0.1", {"idna/__init__.py": "__version__ = '0.0.1'\n"})
    monkeypatch.setitem(seam.AGENT_PATH_VALUES, "PYTHONPATH", str(s))
    probe = [sys.executable, "-c", "import idna; print(idna.__version__)"]

    before = _real_run(probe, capture_output=True, text=True,
                       env=code_tools._script_env(), timeout=60)
    assert before.stdout.strip() == "0.0.1", "the copy shadows the venv in a child"

    # A new venv state: the first prepare() of its digest prunes agent-site.
    monkeypatch.setattr(site.mod, "_frozen", {"/stub/uv": "new-venv-state"})
    site.mod.CONSTRAINTS.write_text("idna==3.20\n", encoding="utf-8")
    site.mod.prepare("/stub/uv")
    after = _real_run(probe, capture_output=True, text=True,
                      env=code_tools._script_env(), timeout=60)
    assert after.returncode == 0, after.stderr
    assert after.stdout.strip() == venv_idna.__version__



def test_each_install_prunes(site: SimpleNamespace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """--target copies the deps of a venv package too. Each install path
    prunes them at once."""
    from acb_skills import dep_tools
    from acb_skills.loader import _install_agent_deps

    def copying_run(cmd: list[str], **_: Any) -> _Done:
        site.calls.append(list(cmd))
        if cmd[1:3] == ["pip", "freeze"]:
            return _Done(stdout=FREEZE_OUT)
        target = Path(cmd[cmd.index("--target") + 1])
        _fake_dist(target, "idna", "0.0.1", {"idna/__init__.py": "x = 1\n"})
        _fake_dist(target, "bh7-fake-agent-pkg", "1.0", {"bh7_fake_agent_pkg/__init__.py": "x = 1\n"})
        return _Done()

    monkeypatch.setattr(subprocess, "run", copying_run)
    _install_agent_deps(_agent(tmp_path), _settings())
    assert not (site.mod.AGENT_SITE / "idna").exists()
    assert (site.mod.AGENT_SITE / "bh7_fake_agent_pkg").is_dir()
    shutil.rmtree(site.mod.AGENT_SITE)
    asyncio.run(dep_tools.install_dependency("bh7-fake-agent-pkg"))
    assert not (site.mod.AGENT_SITE / "idna").exists()
    assert (site.mod.AGENT_SITE / "bh7_fake_agent_pkg").is_dir()


# ── Fix round 1, R7: the constraints digest is in the deps hash ──────────


def test_a_new_venv_state_installs_again(site: SimpleNamespace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The hash holds the constraints digest. A new venv state (a deploy that
    changed a pin) installs the agent again, so its packages follow the new
    pins. Same deps, same target: only the constraints changed."""
    from acb_skills.loader import _install_agent_deps

    agent = _agent(tmp_path)
    _install_agent_deps(agent, _settings())
    assert len(_installs(site.calls)) == 1
    site.calls.clear()
    _install_agent_deps(agent, _settings())
    assert _installs(site.calls) == [], "the same venv state installs nothing"

    def bumped(cmd: list[str], **_: Any) -> _Done:
        site.calls.append(list(cmd))
        if cmd[1:3] == ["pip", "freeze"]:
            return _Done(stdout=FREEZE_OUT.replace("idna==3.20", "idna==3.21"))
        return _Done()

    monkeypatch.setattr(subprocess, "run", bumped)
    monkeypatch.setattr(site.mod, "_frozen", {})
    _install_agent_deps(agent, _settings())
    assert len(_installs(site.calls)) == 1, "a new constraints digest must install again"


# ── Fix round 2, P2-a: the gateway's own requirements file stays valid ────


def _written(site: SimpleNamespace) -> list[str]:
    (cmd,) = _installs(site.calls)
    f = Path(cmd[cmd.index("-r") + 1])
    assert f.parent == site.cache / "requirements", "lines were dropped: the gateway's own file"
    return f.read_text(encoding="utf-8").splitlines()


@pytest.mark.parametrize("form", ["-r base.txt", "--requirement base.txt", "--requirement=base.txt"])
def test_a_relative_include_is_made_absolute(site: SimpleNamespace, tmp_path: Path, form: str) -> None:
    from acb_skills.loader import _install_agent_deps

    agent = _agent(tmp_path, req=f"idna\n{form}\n")  # idna is provided, so a line drops
    (agent / "base.txt").write_text(FAKE + "\n", encoding="utf-8")
    _install_agent_deps(agent, _settings())
    (line,) = _written(site)
    flag, path = line.split(" ", 1)
    assert flag in ("-r", "--requirement")
    assert Path(path).is_absolute() and Path(path).is_file()
    assert Path(path) == agent / "base.txt"


@pytest.mark.parametrize("form", ["-c pins.txt", "--constraint=pins.txt"])
def test_a_relative_constraint_is_made_absolute(site: SimpleNamespace, tmp_path: Path, form: str) -> None:
    from acb_skills.loader import _install_agent_deps

    agent = _agent(tmp_path, req=f"idna\n{form}\n{FAKE}\n")
    (agent / "pins.txt").write_text(FAKE + "\n", encoding="utf-8")
    _install_agent_deps(agent, _settings())
    lines = _written(site)
    assert lines[1] == FAKE
    flag, path = lines[0].split(" ", 1)
    assert flag in ("-c", "--constraint")
    assert Path(path) == agent / "pins.txt"


def test_a_url_include_stays_as_it_is() -> None:
    from acb_skills import agent_site

    assert agent_site.absolute_includes(["-c https://x/pins.txt"], Path("/a")) == ["-c https://x/pins.txt"]


def test_a_hashed_file_drops_a_provided_pin_with_its_hashes(
    site: SimpleNamespace, tmp_path: Path
) -> None:
    """A pip-compile --generate-hashes file. idna is provided, so its pin and
    its two --hash lines go. The kept pin keeps its hashes on its own line."""
    from acb_skills.loader import _install_agent_deps

    req = (
        "# via pip-compile --generate-hashes\n"
        "idna==3.20 \\\n    --hash=sha256:aaaa \\\n    --hash=sha256:bbbb\n"
        f"{FAKE} \\\n    --hash=sha256:cccc\n"
    )
    _install_agent_deps(_agent(tmp_path, req=req), _settings())
    lines = _written(site)
    assert lines == [f"{FAKE} --hash=sha256:cccc"]
    for line in lines:
        assert not line.endswith("\\") and not line.lstrip().startswith("--hash")


def test_continuations_join_before_the_split() -> None:
    from acb_skills import agent_site

    text = "a==1 \\\n  --hash=sha256:x \\\n  --hash=sha256:y\n# c\nb==2  # note\nc==3 \\\n"
    assert agent_site.requirement_lines(text) == [
        "a==1 --hash=sha256:x --hash=sha256:y", "b==2", "c==3",
    ]


# ── Fix round 2, P2-b: provided means the name AND an allowed version ─────


def test_a_satisfied_specifier_is_provided() -> None:
    from acb_skills import agent_site

    v = agent_site.classify(["idna>=3", "idna==3.20", "IDNA", "idna==3.20 --hash=sha256:x"])
    assert [line for line, _ in v.provided] == ["idna>=3", "idna==3.20", "IDNA", "idna==3.20 --hash=sha256:x"]
    assert v.to_install == [] and v.conflicts == []


def test_an_unpinned_name_is_provided() -> None:
    from acb_skills import agent_site

    v = agent_site.classify(["idna"])
    assert v.provided == [("idna", agent_site.venv_version("idna"))]


def test_a_false_marker_is_not_installed() -> None:
    from acb_skills import agent_site

    v = agent_site.classify(['bh7-fake-agent-pkg==1.0; python_version < "3.0"'])
    assert v.not_here and v.to_install == [] and v.provided == []


def test_a_prerelease_counts_only_for_a_prerelease_venv(monkeypatch: pytest.MonkeyPatch) -> None:
    from acb_skills import agent_site

    monkeypatch.setattr(agent_site, "venv_version", lambda name: "2.0rc1")
    assert agent_site.classify(["x>=1.0"]).provided
    monkeypatch.setattr(agent_site, "venv_version", lambda name: "2.0")
    assert agent_site.classify(["x<3"]).provided


def test_an_unsatisfied_specifier_is_a_conflict(site: SimpleNamespace, tmp_path: Path) -> None:
    """idna>=99 against the venv's idna. Not installed (a copy would shadow the
    venv in a child), the dep status is red and names both versions, no
    marker. The agent's other deps still install."""
    from acb_skills.loader import _install_agent_deps, read_dep_status

    agent = _agent(tmp_path, req=f"idna>=99\n{FAKE}\n")
    _install_agent_deps(agent, _settings())
    (line,) = _written(site)
    assert line == FAKE
    status = read_dep_status(agent)
    assert status is not None and status["ok"] is False
    assert f"the venv holds idna {site.mod.venv_version('idna')}, the agent asks for idna>=99" in status["error"]
    assert not (agent / ".git" / "acb-deps-hash").exists()


def test_a_conflict_alone_runs_no_uv(site: SimpleNamespace, tmp_path: Path) -> None:
    from acb_skills.loader import _install_agent_deps, read_dep_status

    agent = _agent(tmp_path, req="idna>=99\n")
    _install_agent_deps(agent, _settings())
    assert site.calls == []
    status = read_dep_status(agent)
    assert status is not None and status["ok"] is False and "idna>=99" in status["error"]
    assert not (agent / ".git" / "acb-deps-hash").exists()


def test_install_dependency_gives_the_same_verdict(site: SimpleNamespace) -> None:
    from acb_skills import dep_tools

    msg = asyncio.run(dep_tools.install_dependency("idna>=99"))
    assert site.calls == []
    assert "Refused, a conflict with the platform: the venv holds idna" in msg
    msg = asyncio.run(dep_tools.install_dependency("idna>=99 bh7-fake-agent-pkg"))
    (cmd,) = _installs(site.calls)
    assert cmd[-1] == "bh7-fake-agent-pkg" and "idna>=99" not in cmd
    assert "Refused, a conflict" in msg


# ── Fix round 2: the lookup reads the venv's site dirs only ──────────────


def test_an_egg_info_in_an_agent_dir_is_not_a_venv_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """load_agent puts the agent and skill dirs on sys.path. A checked-in
    *.egg-info there must not count as a venv package."""
    from acb_skills import agent_site

    agent = tmp_path / "repos" / "an-agent"
    egg = agent / "bh7_checked_in.egg-info"
    egg.mkdir(parents=True)
    (egg / "PKG-INFO").write_text("Metadata-Version: 2.1\nName: bh7-checked-in\nVersion: 9.9\n",
                                  encoding="utf-8")
    monkeypatch.setattr(sys, "path", [str(agent), *sys.path])
    import importlib.metadata
    assert importlib.metadata.version("bh7-checked-in") == "9.9", "the egg-info is on sys.path"
    assert agent_site.venv_version("bh7-checked-in") is None
    assert agent_site.classify(["bh7-checked-in"]).to_install == ["bh7-checked-in"]


# ── Fix round 2: the venv-provides-all marker is read on the warm path ────


def test_a_warm_load_reads_the_provides_all_marker(
    site: SimpleNamespace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from acb_skills import loader

    agent = _agent(tmp_path, req="idna\n")
    loader._install_agent_deps(agent, _settings())
    assert (agent / ".git" / "acb-deps-hash").is_file()
    writes: list[bool] = []
    monkeypatch.setattr(loader, "_write_dep_status", lambda *a, **k: writes.append(k["ok"]))
    loader._install_agent_deps(agent, _settings())
    assert writes == [], "an unchanged set returns at the marker"
    (agent / "requirements.txt").write_text("idna\nskill-projects\n", encoding="utf-8")
    loader._install_agent_deps(agent, _settings())
    assert writes == [True], "a changed set writes the status again"
    assert site.calls == []


def test_a_conflict_added_later_turns_the_status_red(site: SimpleNamespace, tmp_path: Path) -> None:
    """The install marker covers only the lines that go to uv. A conflict line
    added later leaves those the same, so the marker would still match. The
    loader must not return at the marker while a conflict stands."""
    from acb_skills.loader import _install_agent_deps, read_dep_status

    agent = _agent(tmp_path)
    _install_agent_deps(agent, _settings())
    status = read_dep_status(agent)
    assert status is not None and status["ok"] is True
    (agent / "requirements.txt").write_text(f"{FAKE}\nidna>=99\n", encoding="utf-8")
    _install_agent_deps(agent, _settings())
    status = read_dep_status(agent)
    assert status is not None and status["ok"] is False and "idna>=99" in status["error"]
