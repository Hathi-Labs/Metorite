"""Task 691 (HANDOFF H-281): no tool wrapper passes the whole environment.

The loader writes a tool wrapper into an agent repo's ``agents.py`` for each
new skill script (``acb_skills.loader._sync_new_skills``). Before WS-49 BH-1
the template gave the script the whole gateway env. A clone keeps such a
wrapper until it changes. The loader now upgrades it on each load, and the
import refuses a generated wrapper that it cannot upgrade.

The old shapes come from the git history of the template:

* Shape A (``b97ed0c5a`` to ``980c04a49^``):
  ``_env = {**_os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}``.
* Shape B (``ae2f1ece7``, ``ea496f3a4``): ``_s.run`` with no ``env=``, so the
  child inherits the whole env.

The fence (R7) at the end fails when the template, or a generated wrapper in
``apps/agents/**/agents.py``, passes ``os.environ`` whole.
"""
from __future__ import annotations

import asyncio
import re
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from acb_skills import loader

REPO = Path(__file__).resolve().parents[2]
SECRET = "ACB_MASTER_KEY"

# The hand-written part of the clone. It uses os.environ on purpose: the
# upgrade must never touch code that the generator did not write.
HEAD = '''"""A probe agent."""
import os


def hand_written(*args: str) -> str:
    """Run nothing (by hand). Pass CLI args as strings."""
    return os.environ.get("HOME", "")

'''
TAIL = '''

def build_agents():
    return [dict(tools=[hand_written, env_dump])]
'''
# The clone before the first sync: no wrapper, and no name of the script.
TAIL_NEW = TAIL.replace(", env_dump]", "]")

# Shape A, byte for byte as the template wrote it (b97ed0c5a .. 980c04a49^).
OLD_A = '''
# ── Auto-synced tools (added by CommandCenter) ──────────────────────────

async def env_dump(*args: str) -> str:
    """Run env_dump (probe). Pass CLI args as strings."""
    import asyncio as _a, os as _os, subprocess as _s, sys as _sys
    from pathlib import Path as _P
    _d = _P(__file__).parent.resolve()
    _cmd = [_sys.executable, "-X", "utf8", str(_d / "skills/probe/scripts/env_dump.py")] + list(args)
    _env = {**_os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    _r = await _a.to_thread(
        _s.run, _cmd, capture_output=True, encoding="utf-8", errors="replace",
        cwd=str(_d), env=_env,
    )
    if _r.returncode != 0:
        raise RuntimeError(_r.stderr[:500] or "Script exited non-zero")
    return _r.stdout or "(no output)"
'''

# Shape B, as ea496f3a4 wrote it. ae2f1ece7 differs only in ``_cmd``.
OLD_B = '''
async def env_dump(*args: str) -> str:
    """Run env_dump (probe). Pass CLI args as strings."""
    import asyncio as _a, subprocess as _s, sys as _sys
    from pathlib import Path as _P
    _d = _P(__file__).parent.resolve()
    _cmd = [_sys.executable, str(_d / "skills/probe/scripts/env_dump.py")] + list(args)
    _r = await _a.to_thread(_s.run, _cmd, capture_output=True, text=True, cwd=str(_d))
    if _r.returncode != 0:
        raise RuntimeError(_r.stderr[:500] or "Script exited non-zero")
    return _r.stdout or "(no output)"
'''
OLD_B_FIRST = OLD_B.replace(
    "import asyncio as _a, subprocess as _s, sys as _sys",
    "import asyncio as _a, subprocess as _s",
).replace('[_sys.executable, str(', '["python", str(')

# A generated wrapper that no known shape fits.
ODD = OLD_A.replace(
    '_env = {**_os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}',
    "_env = dict(_os.environ)",
)

#: The literal ways to pass the whole env (task 691 fence).
WHOLE_ENV = re.compile(
    r"\{\*\*os\.environ|\{\*\*_os\.environ|env=os\.environ\b|env=_os\.environ\b"
    r"|os\.environ\.copy\(\)"
)


def _clone(tmp_path: Path, wrapper: str, *, newline: str = "\n", tail: str = TAIL) -> Path:
    scripts = tmp_path / "skills" / "probe" / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "env_dump.py").write_text(
        "import os\nprint(' '.join(sorted(os.environ)))\n", encoding="utf-8"
    )
    text = (HEAD + wrapper + tail).replace("\n", newline)
    (tmp_path / "agents.py").write_bytes(text.encode("utf-8"))
    return tmp_path


def _load_tools(agent_dir: Path, run_id: str) -> dict[str, object]:
    agent = loader.LoadedAgent(
        agent_name="probe", run_id=run_id, agent_dir=agent_dir,
        graph_module=None, injected_paths=[], module_name=f"_m_{run_id}",
    )
    try:
        built = agent.build_agents()
    finally:
        agent.cleanup()
        import sys

        sys.modules.pop(f"_agent_{run_id}_agents", None)
    return {t.__name__: t for t in built[0]["tools"]}


@pytest.fixture
def secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(SECRET, "fake-master-key-691")
    monkeypatch.setenv("BH1_CANARY_SECRET", "bh1canary")


@pytest.fixture
def git_calls(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    calls: list[list[str]] = []

    def fake(args: list[str], **_kw: object) -> SimpleNamespace:
        calls.append(list(args))
        return SimpleNamespace(returncode=0, stdout="abc123\n", stderr="")

    monkeypatch.setattr(loader, "_run_git", fake)
    monkeypatch.setattr(loader, "_register_sync_commit_async", lambda **_k: None)
    return calls


# ── the child gets only child_env's set ─────────────────────────────────────


def test_an_old_wrapper_in_a_clone_no_longer_leaks_the_env(tmp_path: Path, secret: None) -> None:
    """Red before task 691: the shape A wrapper gave the script every secret."""
    agent_dir = _clone(tmp_path, OLD_A)
    tools = _load_tools(agent_dir, "t691a")
    names = asyncio.run(tools["env_dump"]()).split()
    assert SECRET not in names
    assert "BH1_CANARY_SECRET" not in names
    assert "PATH" in names and "PYTHONUTF8" in names


def test_a_shape_b_wrapper_no_longer_leaks_the_env(tmp_path: Path, secret: None) -> None:
    agent_dir = _clone(tmp_path, OLD_B)
    names = asyncio.run(_load_tools(agent_dir, "t691b")["env_dump"]()).split()
    assert SECRET not in names
    assert "PATH" in names


# ── the upgrade on disk ─────────────────────────────────────────────────────


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_the_sync_upgrades_the_wrapper_and_keeps_every_other_byte(
    tmp_path: Path, git_calls: list[list[str]], newline: str
) -> None:
    agent_dir = _clone(tmp_path, OLD_A, newline=newline)
    before = (agent_dir / "agents.py").read_bytes()
    loader._sync_new_skills(agent_dir, SimpleNamespace())
    after = (agent_dir / "agents.py").read_bytes()

    old_line = b'    _env = {**_os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}'
    nl = newline.encode()
    new_lines = (
        b"    from acb_common.child_env import child_env as _child_env" + nl
        + b'    _env = _child_env(extra={"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})'
    )
    assert before.count(old_line) == 1
    assert after == before.replace(old_line, new_lines)
    assert not WHOLE_ENV.search(after.decode("utf-8").split("# ──")[1])
    # The hand-written function still reads os.environ: nobody touched it.
    assert b'return os.environ.get("HOME", "")' in after
    assert ["add", "agents.py"] in git_calls
    commit = next(c for c in git_calls if c[0] == "commit")
    assert "child_env" in commit[2]


def test_the_upgrade_keeps_the_wrappers_own_extra_values() -> None:
    text = HEAD + OLD_A.replace('"PYTHONIOENCODING": "utf-8"', '"MY_FLAG": "on"') + TAIL
    new, upgraded, refused = loader.upgrade_old_wrappers(text)
    assert upgraded == [("env_dump", "probe")] and refused == []
    assert '_env = _child_env(extra={"PYTHONUTF8": "1", "MY_FLAG": "on"})' in new


@pytest.mark.parametrize("wrapper", [OLD_B, OLD_B_FIRST], ids=["ea496f3a4", "ae2f1ece7"])
def test_the_upgrade_adds_env_to_a_run_with_none(wrapper: str) -> None:
    new, upgraded, refused = loader.upgrade_old_wrappers(HEAD + wrapper + TAIL)
    assert upgraded == [("env_dump", "probe")] and refused == []
    assert "cwd=str(_d), env=_child_env())" in new
    assert new.replace(
        "    from acb_common.child_env import child_env as _child_env\n", ""
    ).replace(", env=_child_env())", ")") == HEAD + wrapper + TAIL


def test_a_current_wrapper_and_hand_written_code_stay_the_same(
    tmp_path: Path, git_calls: list[list[str]]
) -> None:
    """A fresh sync writes the current template, and a second load changes nothing."""
    agent_dir = _clone(tmp_path, "", tail=TAIL_NEW)
    loader._sync_new_skills(agent_dir, SimpleNamespace())
    synced = (agent_dir / "agents.py").read_bytes()
    git_calls.clear()
    loader._sync_new_skills(agent_dir, SimpleNamespace())
    assert (agent_dir / "agents.py").read_bytes() == synced
    assert git_calls == []
    assert loader.upgrade_old_wrappers(synced.decode("utf-8")) == (synced.decode("utf-8"), [], [])


def test_the_upgrade_logs_the_agent_and_the_skill_and_no_value(
    tmp_path: Path, git_calls: list[list[str]], monkeypatch: pytest.MonkeyPatch, secret: None
) -> None:
    lines: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        loader, "_log",
        SimpleNamespace(
            info=lambda e, **k: lines.append((e, k)),
            warning=lambda e, **k: lines.append((e, k)),
        ),
    )
    agent_dir = _clone(tmp_path, OLD_A)
    loader._sync_new_skills(agent_dir, SimpleNamespace())
    upgraded = [k for e, k in lines if e == "loader.wrapper_env_upgraded"]
    assert upgraded == [{"agent": agent_dir.name, "skill": "probe", "tool": "env_dump", "where": "clone"}]
    assert "fake-master-key-691" not in repr(lines)


# ── fail closed ─────────────────────────────────────────────────────────────


def test_a_failed_commit_still_starts_no_child_with_the_whole_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, secret: None
) -> None:
    """The commit fails, and the loader restores the old file. The import
    upgrades the wrapper in memory, so the child still gets child_env."""
    agent_dir = _clone(tmp_path, OLD_A)
    original = (agent_dir / "agents.py").read_bytes()

    def fake(args: list[str], **_kw: object) -> SimpleNamespace:
        if args[:2] == ["checkout", "--"]:
            (agent_dir / "agents.py").write_bytes(original)
        return SimpleNamespace(returncode=1 if args[0] == "commit" else 0, stdout="", stderr="")

    monkeypatch.setattr(loader, "_run_git", fake)
    loader._sync_new_skills(agent_dir, SimpleNamespace())
    assert (agent_dir / "agents.py").read_bytes() == original
    names = asyncio.run(_load_tools(agent_dir, "t691c")["env_dump"]()).split()
    assert SECRET not in names and "PATH" in names


def test_a_wrapper_no_shape_fits_is_refused_and_starts_nothing(
    tmp_path: Path, git_calls: list[list[str]], monkeypatch: pytest.MonkeyPatch, secret: None
) -> None:
    agent_dir = _clone(tmp_path, ODD)
    before = (agent_dir / "agents.py").read_bytes()
    loader._sync_new_skills(agent_dir, SimpleNamespace())
    # The disk keeps it: a person may have edited it.
    assert (agent_dir / "agents.py").read_bytes() == before
    assert git_calls == []

    started: list[object] = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: started.append(a))
    tools = _load_tools(agent_dir, "t691d")
    with pytest.raises(RuntimeError, match="This tool is off"):
        asyncio.run(tools["env_dump"]())
    assert started == []
    # The hand-written tool still loads and works.
    assert tools["hand_written"]() == __import__("os").environ.get("HOME", "")


def test_the_banner_alone_does_not_make_a_wrapper_generated() -> None:
    """A person's function under the banner, without the generator's
    docstring and alias import, is not a generated wrapper."""
    own = OLD_A.replace('"""Run env_dump (probe). Pass CLI args as strings."""', '"""Mine."""')
    text = HEAD + own + TAIL
    assert loader.upgrade_old_wrappers(text) == (text, [], [])


# ── the fence (R7) ──────────────────────────────────────────────────────────


def test_the_template_passes_only_the_one_env(tmp_path: Path, git_calls: list[list[str]]) -> None:
    """Fails when the template goes back to an old line, or when its
    docstring changes so the upgrade can no longer see a generated wrapper."""
    agent_dir = _clone(tmp_path, "", tail=TAIL_NEW)
    loader._sync_new_skills(agent_dir, SimpleNamespace())
    text = (agent_dir / "agents.py").read_text(encoding="utf-8")
    block = text.split("# ── Auto-synced tools")[1]
    assert not WHOLE_ENV.search(block), "the wrapper template passes the whole env"
    import ast

    wrappers = loader._generated_wrappers(ast.parse(text))
    assert [fn.name for fn, _ in wrappers] == ["env_dump"]
    assert loader._wrapper_is_safe(wrappers[0][0])


def test_no_agent_dir_in_the_repo_holds_a_whole_env_wrapper() -> None:
    import ast

    files = sorted((REPO / "apps" / "agents").glob("**/agents.py"))
    assert files, "the scan sees no agents.py"
    bad: list[str] = []
    for path in files:
        text = path.read_text(encoding="utf-8")
        for fn, _skill in loader._generated_wrappers(ast.parse(text)):
            segment = ast.get_source_segment(text, fn) or ""
            if WHOLE_ENV.search(segment) or not loader._wrapper_is_safe(fn):
                bad.append(f"{path.relative_to(REPO).as_posix()}:{fn.lineno} {fn.name}")
    assert not bad, bad
