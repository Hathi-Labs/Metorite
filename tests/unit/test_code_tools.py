"""Coding skill for MAF agents — run_script / code_task (agent_coding_skill).

Covers the execution-hygiene contract (workspace jail, secret-scrubbed
subprocess env, timeout, output cap), the durability sweep that mirrors
native-written files into the blob store, and the fail-closed edges (no
workspace, path escape, missing script, bad type). ``code_task`` is tested
against a stubbed Copilot session — the point here is the skill layer's
always-sweep + error shaping, not the engine itself.
"""
from __future__ import annotations

import asyncio
import os
import stat
import time

import acb_skills.code_tools as ct
import pytest
from acb_skills.write_artifact import _WRITE_ARTIFACT_CONTEXT


@pytest.fixture()
def ws(monkeypatch, tmp_path):
    monkeypatch.setitem(_WRITE_ARTIFACT_CONTEXT, "workspace_root", str(tmp_path))
    monkeypatch.setitem(_WRITE_ARTIFACT_CONTEXT, "session_id", "sess-test")
    (tmp_path / "agent-data" / "scripts").mkdir(parents=True)
    (tmp_path / "outputs").mkdir()
    return tmp_path


@pytest.fixture()
def mirrored(monkeypatch):
    """Capture what the sweep mirrors instead of hitting the real store."""
    calls: list[str] = []

    async def _fake(rel_path, data, **kw):
        calls.append(rel_path)

    monkeypatch.setattr(ct, "mirror_to_blob_store", _fake)
    return calls


# ── run_script: fail-closed edges ───────────────────────────────────────────

def test_run_script_without_workspace(monkeypatch):
    monkeypatch.setitem(_WRITE_ARTIFACT_CONTEXT, "workspace_root", None)
    out = asyncio.run(ct.run_script("agent-data/scripts/x.py"))
    assert "no active workspace" in out


def test_run_script_blocks_workspace_escape(ws):
    out = asyncio.run(ct.run_script("../../etc/passwd"))
    assert "escapes the workspace" in out
    out = asyncio.run(ct.run_script("/etc/passwd"))
    assert "escapes the workspace" in out


def test_run_script_missing_points_at_manifest(ws):
    out = asyncio.run(ct.run_script("agent-data/scripts/nope.py"))
    assert "not found" in out
    assert "SCRIPTS.md" in out and "code_task" in out


def test_run_script_rejects_unsupported_type(ws):
    p = ws / "agent-data" / "scripts" / "x.rb"
    p.write_text("puts 1")
    out = asyncio.run(ct.run_script("agent-data/scripts/x.rb"))
    assert "unsupported script type" in out


def test_run_script_rejects_unparseable_args(ws):
    p = ws / "agent-data" / "scripts" / "ok.py"
    p.write_text("print('hi')")
    out = asyncio.run(ct.run_script("agent-data/scripts/ok.py", 'unclosed "quote'))
    assert "bad args" in out


# ── run_script: execution contract ──────────────────────────────────────────

def test_run_script_runs_python_with_args_and_cwd(ws, mirrored):
    p = ws / "agent-data" / "scripts" / "greet.py"
    p.write_text(
        "import os, sys\n"
        "print('args:', sys.argv[1:])\n"
        "print('cwd-ok:', os.getcwd())\n"
    )
    out = asyncio.run(ct.run_script("agent-data/scripts/greet.py", "hello world"))
    assert "exit 0" in out
    assert "args: ['hello', 'world']" in out
    # cwd is the workspace root, so relative outputs/ paths just work.
    assert str(ws.resolve()) in out


def test_run_script_runs_shell_scripts(ws, mirrored):
    p = ws / "agent-data" / "scripts" / "hello.sh"
    p.write_text("echo shell-ok\n")
    p.chmod(p.stat().st_mode | stat.S_IXUSR)
    out = asyncio.run(ct.run_script("agent-data/scripts/hello.sh"))
    assert "exit 0" in out and "shell-ok" in out


def test_run_script_env_is_scrubbed(ws, mirrored, monkeypatch):
    """No token/secret-shaped env vars may reach arbitrary script code."""
    monkeypatch.setenv("GATEWAY_INTERNAL_TOKEN", "sk-super-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-leak")
    monkeypatch.setenv("DATABASE_URL", "postgres://user:pw@host/db")
    p = ws / "agent-data" / "scripts" / "env_dump.py"
    p.write_text("import os; print(sorted(os.environ))")
    out = asyncio.run(ct.run_script("agent-data/scripts/env_dump.py"))
    assert "exit 0" in out
    assert "GATEWAY_INTERNAL_TOKEN" not in out
    assert "OPENAI_API_KEY" not in out
    assert "DATABASE_URL" not in out
    assert "'PATH'" in out  # the allowlist still provides the basics


def test_script_env_allowlist_and_deny_pattern(monkeypatch):
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setenv("MY_SECRET_KEY", "x")
    monkeypatch.setenv("SOME_RANDOM_VAR", "y")
    env = ct._script_env()
    assert env.get("PATH") == "/usr/bin"
    assert "MY_SECRET_KEY" not in env
    assert "SOME_RANDOM_VAR" not in env  # not on the allowlist
    assert env.get("PYTHONUNBUFFERED") == "1"


def test_run_script_nonzero_exit_and_stderr(ws, mirrored):
    p = ws / "agent-data" / "scripts" / "boom.py"
    p.write_text("import sys; print('partial'); sys.exit(3)")
    out = asyncio.run(ct.run_script("agent-data/scripts/boom.py"))
    assert "exit 3" in out and "partial" in out


def test_run_script_timeout(ws, mirrored, monkeypatch):
    monkeypatch.setattr(ct, "_RUN_SCRIPT_TIMEOUT", 0.5)
    p = ws / "agent-data" / "scripts" / "sleepy.py"
    p.write_text("import time; time.sleep(10)")
    out = asyncio.run(ct.run_script("agent-data/scripts/sleepy.py"))
    assert "timed out" in out


def test_run_script_sweeps_outputs_to_blob_store(ws, mirrored):
    p = ws / "agent-data" / "scripts" / "writer.py"
    p.write_text(
        "from pathlib import Path\n"
        "Path('outputs/result.csv').write_text('a,b\\n1,2\\n')\n"
        "print('wrote')\n"
    )
    out = asyncio.run(ct.run_script("agent-data/scripts/writer.py"))
    assert "exit 0" in out
    assert "outputs/result.csv" in mirrored
    assert "1 output file(s) persisted" in out


def test_output_cap_truncates_middle():
    text = "a" * 20_000
    capped = ct._cap(text)
    assert len(capped) < 20_000
    assert "truncated" in capped
    assert capped.startswith("a") and capped.endswith("a")


# ── the durability sweep itself ─────────────────────────────────────────────

def test_sweep_mirrors_only_changed_small_files(ws, mirrored):
    old = ws / "agent-data" / "scripts" / "old.py"
    old.write_text("print('old')")
    past = time.time() - 3600
    os.utime(old, (past, past))
    cutoff = time.time() - 1
    (ws / "agent-data" / "scripts" / "new.py").write_text("print('new')")
    (ws / "agent-data" / "SCRIPTS.md").write_text("# scripts")
    (ws / "outputs" / "big.bin").write_bytes(b"x" * (ct._SWEEP_MAX_BYTES + 1))
    (ws / "outputs" / "small.txt").write_text("ok")

    n = asyncio.run(ct._sweep_to_blob_store(ws, since=cutoff))
    assert n == 3
    assert set(mirrored) == {
        "agent-data/scripts/new.py", "agent-data/SCRIPTS.md",
        "outputs/small.txt",
    }


def test_sweep_does_not_follow_symlinked_dir_out_of_workspace(ws, mirrored, tmp_path):
    """A symlinked directory pointing outside the workspace must NOT let the
    sweep mirror host files into the durable store (rglob follows symlinked
    dirs on py<3.13; the resolved-path containment check blocks it)."""
    secret_dir = tmp_path.parent / "outside_secrets"
    secret_dir.mkdir(exist_ok=True)
    (secret_dir / "service_account.json").write_text('{"private_key":"LEAK"}')
    # Agent (or a code_task session) drops a symlink under agent-data/.
    link = ws / "agent-data" / "escape"
    try:
        link.symlink_to(secret_dir, target_is_directory=True)
    except (OSError, NotImplementedError):
        import pytest
        pytest.skip("symlinks unsupported on this platform")
    (ws / "outputs" / "legit.txt").write_text("ok")

    n = asyncio.run(ct._sweep_to_blob_store(ws, since=time.time() - 5))
    # The legit in-workspace file is mirrored; the symlinked-out secret is NOT.
    assert "outputs/legit.txt" in mirrored
    assert not any("service_account" in m or "escape" in m for m in mirrored)
    assert n == 1


# ── code_task: skill-layer behaviour around the engine ──────────────────────

def test_code_task_without_workspace(monkeypatch):
    monkeypatch.setitem(_WRITE_ARTIFACT_CONTEXT, "workspace_root", None)
    out = asyncio.run(ct.code_task("build a report script"))
    assert "no active workspace" in out


def test_code_task_requires_a_task(ws):
    out = asyncio.run(ct.code_task("   "))
    assert "describe what to build" in out


def test_code_task_success_reports_and_sweeps(ws, mirrored, monkeypatch):
    import orchestrator.code_session as cs

    async def _fake_session(*, task, workspace, **kw):
        # The engine writes via NATIVE file tools (bypassing the mirror) —
        # simulate that, then report.
        from pathlib import Path
        Path(workspace, "agent-data/scripts/report.py").write_text("print(1)")
        Path(workspace, "agent-data/SCRIPTS.md").write_text("# scripts")
        return "Created report.py; run with run_script."

    monkeypatch.setattr(cs, "run_copilot_code_session", _fake_session)
    out = asyncio.run(ct.code_task("make a report script"))
    assert "Created report.py" in out
    assert "2 file(s) persisted to the durable store" in out
    assert set(mirrored) == {
        "agent-data/scripts/report.py", "agent-data/SCRIPTS.md",
    }


def test_code_task_failure_still_sweeps(ws, mirrored, monkeypatch):
    import orchestrator.code_session as cs

    async def _boom(*, task, workspace, **kw):
        from pathlib import Path
        Path(workspace, "agent-data/scripts/half.py").write_text("print(1)")
        raise TimeoutError("session wedged")

    monkeypatch.setattr(cs, "run_copilot_code_session", _boom)
    out = asyncio.run(ct.code_task("make a thing"))
    assert "code_task failed: TimeoutError" in out
    # Partial work is not lost — the sweep still persisted it.
    assert "agent-data/scripts/half.py" in mirrored
    assert "still persisted" in out


# ── integrations: declared-only credential pass-through + discoverability ───

def test_script_env_grants_declared_integration_vars(ws, monkeypatch):
    """A script gets exactly its agent's DECLARED integrations' env vars —
    the undeclared ones stay scrubbed even though they match registry names."""
    monkeypatch.setitem(_WRITE_ARTIFACT_CONTEXT, "integrations", ["zoho-crm"])
    monkeypatch.setenv("ZOHO_CLIENT_ID", "pk_declared")
    monkeypatch.setenv("ZOHO_CLIENT_SECRET", "ws1")
    monkeypatch.setenv("APOLLO_API_KEY", "sk_undeclared")  # not declared
    monkeypatch.setenv("GATEWAY_INTERNAL_TOKEN", "sk-platform")  # never
    env = ct._script_env()
    assert env.get("ZOHO_CLIENT_ID") == "pk_declared"
    assert env.get("ZOHO_CLIENT_SECRET") == "ws1"
    assert "APOLLO_API_KEY" not in env
    assert "GATEWAY_INTERNAL_TOKEN" not in env


def test_script_env_no_declared_integrations_stays_fully_scrubbed(ws, monkeypatch):
    monkeypatch.delitem(_WRITE_ARTIFACT_CONTEXT, "integrations", raising=False)
    monkeypatch.setenv("ZOHO_CLIENT_ID", "pk_x")
    assert "ZOHO_CLIENT_ID" not in ct._script_env()


def test_run_script_subprocess_sees_declared_integration(ws, mirrored, monkeypatch):
    monkeypatch.setitem(_WRITE_ARTIFACT_CONTEXT, "integrations", ["zoho-crm"])
    monkeypatch.setenv("ZOHO_CLIENT_ID", "pk_live_test")
    monkeypatch.setenv("SERPAPI_API_KEY", "sk_not_declared")
    p = ws / "agent-data" / "scripts" / "integ.py"
    p.write_text(
        "import os\n"
        "print('zoho:', 'yes' if os.getenv('ZOHO_CLIENT_ID') else 'no')\n"
        "print('serpapi:', 'yes' if os.getenv('SERPAPI_API_KEY') else 'no')\n"
    )
    out = asyncio.run(ct.run_script("agent-data/scripts/integ.py"))
    assert "zoho: yes" in out
    assert "serpapi: no" in out


def test_field_to_env_matches_every_registered_service():
    """Every registry service must have an env mapping, or its scripts could
    never receive credentials (and the executor could never inject them)."""
    from acb_skills.integrations import _REGISTRY, FIELD_TO_ENV
    assert set(_REGISTRY) == set(FIELD_TO_ENV)


def test_env_var_names_helper():
    from acb_skills.integrations import env_var_names
    assert env_var_names(["zoho-crm"]) == {
        "ZOHO_CLIENT_ID", "ZOHO_CLIENT_SECRET", "ZOHO_REFRESH_TOKEN",
        "ZOHO_API_DOMAIN", "ZOHO_ACCOUNTS_URL", "ZOHO_REGION",
    }
    assert env_var_names(["nope"]) == set()
    assert env_var_names([]) == set()


def test_list_integrations_reports_names_never_values(monkeypatch):
    from acb_skills.integration_tools import list_integrations
    monkeypatch.setitem(_WRITE_ARTIFACT_CONTEXT, "integrations", ["zoho-crm"])
    monkeypatch.setitem(
        _WRITE_ARTIFACT_CONTEXT, "integration_warnings",
        {"apollo": "apollo: APOLLO_API_KEY is required."},
    )
    monkeypatch.setenv("ZOHO_CLIENT_ID", "pk_secret_value")
    out = asyncio.run(list_integrations())
    assert "zoho-crm" in out and "ZOHO_CLIENT_ID" in out
    assert "pk_secret_value" not in out  # names only, never values
    assert "apollo" in out and "UNAVAILABLE" in out


def test_list_integrations_none_declared(monkeypatch):
    from acb_skills.integration_tools import list_integrations
    monkeypatch.delitem(_WRITE_ARTIFACT_CONTEXT, "integrations", raising=False)
    monkeypatch.delitem(
        _WRITE_ARTIFACT_CONTEXT, "integration_warnings", raising=False,
    )
    out = asyncio.run(list_integrations())
    assert "No integrations are declared" in out


def test_code_task_session_prompt_names_integration_env_vars(ws, mirrored, monkeypatch):
    """The coding session is told WHICH env vars scripts may read — but the
    prompt never carries credential values."""
    import orchestrator.code_session as cs

    monkeypatch.setitem(_WRITE_ARTIFACT_CONTEXT, "integrations", ["zoho-crm"])
    monkeypatch.setenv("ZOHO_CLIENT_ID", "pk_secret_value")
    seen: dict[str, str] = {}

    async def _capture(*, task, workspace, **kw):
        seen["task"] = task
        return "ok"

    monkeypatch.setattr(cs, "run_copilot_code_session", _capture)
    asyncio.run(ct.code_task("pull my zoho leads"))
    assert "ZOHO_CLIENT_ID" in seen["task"]
    assert "pk_secret_value" not in seen["task"]


# ── mutability harmonisation: repo-baked skill edits → approval pipeline ────

def _git(root, *args):
    import subprocess
    return subprocess.run(
        ["git", *args], cwd=str(root), capture_output=True, text=True, timeout=30,
    )


@pytest.fixture()
def git_ws(ws):
    """Turn the workspace into a mini agent clone: tracked skill + gitignore."""
    (ws / "skills" / "demo" / "scripts").mkdir(parents=True)
    (ws / "skills" / "demo" / "scripts" / "tool.py").write_text("print('v1')")
    (ws / ".gitignore").write_text("outputs/\ninputs/\nagent-data/\n")
    assert _git(ws, "init").returncode == 0
    _git(ws, "config", "user.name", "test-bot")
    _git(ws, "config", "user.email", "test-bot@example.com")
    _git(ws, "add", "-A")
    assert _git(ws, "commit", "-m", "baseline").returncode == 0
    return ws


def _commit_count(root) -> int:
    return int(_git(root, "rev-list", "--count", "HEAD").stdout.strip())


def test_code_task_commits_uncommitted_repo_skill_edit(git_ws, mirrored, monkeypatch):
    """A session that fixes a repo-baked skill but forgets to commit must not
    leave the clone dirty — dirty tracked files are destroyed by the loader's
    next pull (stash-drop / hard-reset), and only a LOCAL COMMIT reaches the
    pending_commit approval inbox."""
    import orchestrator.code_session as cs

    async def _fix_skill(*, task, workspace, **kw):
        from pathlib import Path
        Path(workspace, "skills/demo/scripts/tool.py").write_text("print('v2-fixed')")
        Path(workspace, "agent-data/SCRIPTS.md").write_text("# scripts")
        return "Fixed the demo skill."

    monkeypatch.setattr(cs, "run_copilot_code_session", _fix_skill)
    out = asyncio.run(ct.code_task("fix the demo skill tool.py"))
    assert "Fixed the demo skill" in out
    assert "committed locally" in out and "human approval" in out
    assert _commit_count(git_ws) == 2  # baseline + the fail-safe commit
    # Tree is clean again — nothing left for a pull to destroy.
    assert not _git(git_ws, "status", "--porcelain").stdout.strip()
    # The commit message carries the task for the approval inbox.
    assert "code_task: fix the demo skill" in _git(git_ws, "log", "-1", "--format=%s").stdout
    # gitignored runtime state stayed OUT of the commit (blob store owns it)…
    assert "agent-data" not in _git(git_ws, "show", "--name-only", "HEAD").stdout
    # …and went through the durability sweep instead.
    assert "agent-data/SCRIPTS.md" in mirrored


def test_code_task_no_commit_when_repo_clean(git_ws, mirrored, monkeypatch):
    import orchestrator.code_session as cs

    async def _workspace_only(*, task, workspace, **kw):
        from pathlib import Path
        Path(workspace, "agent-data/scripts/new.py").write_text("print(1)")
        return "Made a workspace script."

    monkeypatch.setattr(cs, "run_copilot_code_session", _workspace_only)
    out = asyncio.run(ct.code_task("make a workspace script"))
    assert _commit_count(git_ws) == 1  # no empty/noise commit
    assert "committed locally" not in out


def test_code_task_non_git_workspace_skips_commit(ws, mirrored, monkeypatch):
    import orchestrator.code_session as cs

    async def _ok(*, task, workspace, **kw):
        return "done"

    monkeypatch.setattr(cs, "run_copilot_code_session", _ok)
    out = asyncio.run(ct.code_task("anything"))
    assert "done" in out and "committed locally" not in out


def test_commit_never_stages_runtime_dirs_even_without_gitignore(ws, mirrored, monkeypatch):
    """External workspace_root repos may lack the loader's .gitignore. A secret
    a script wrote under agent-data/ must STILL never land in the commit — the
    explicit unstage of agent-data/inputs/outputs is the guarantee."""
    import orchestrator.code_session as cs

    # Git repo with NO .gitignore.
    (ws / "skills").mkdir()
    (ws / "skills" / "tool.py").write_text("print('v1')")
    assert _git(ws, "init").returncode == 0
    _git(ws, "config", "user.name", "t")
    _git(ws, "config", "user.email", "t@e.com")
    _git(ws, "add", "-A")
    _git(ws, "commit", "-m", "baseline")

    async def _sess(*, task, workspace, **kw):
        from pathlib import Path
        Path(workspace, "skills/tool.py").write_text("print('v2')")   # tracked edit
        Path(workspace, "agent-data/leaked_token.txt").write_text("sk-SECRET")
        return "changed"

    monkeypatch.setattr(cs, "run_copilot_code_session", _sess)
    asyncio.run(ct.code_task("change the tool"))
    committed = _git(ws, "show", "--name-only", "HEAD").stdout
    assert "skills/tool.py" in committed        # the real edit is committed…
    assert "agent-data" not in committed        # …the secret is NOT
    assert "leaked_token" not in committed


def test_harness_contract_covers_both_script_homes():
    """The coding-session prompt must teach the two-home model and the
    local-commit-for-approval rule (never push)."""
    from orchestrator.code_session import _HARNESS_INSTRUCTIONS as h
    assert "agent-data/scripts/" in h          # workspace home
    assert "skills/*/scripts/" in h            # repo-baked skill home
    assert "git commit" in h and "NEVER push" in h
    assert "human approval" in h
    assert "Never commit `agent-data/`" in h or "Never commit \\\n`agent-data/`" in h or "agent-data/`, `inputs/`, or `outputs/`" in h


def test_addendum_explains_builtin_skill_fixing():
    from orchestrator._tool_injection import _build_injected_tools_addendum
    text = _build_injected_tools_addendum()
    assert "BUILT-IN skills" in text
    assert "HUMAN APPROVAL" in text


# ── platform wiring: floor, addendum, annotations ───────────────────────────

def test_coding_skill_rides_the_core_floor():
    from orchestrator._tool_injection import (
        _CORE_STANDARD_TOOL_NAMES,
        _resolve_injected_scope,
    )
    assert {"run_script", "code_task"} <= _CORE_STANDARD_TOOL_NAMES
    resolved = _resolve_injected_scope(["web_search"])
    assert resolved is not None and "code_task" in resolved


def test_addendum_documents_the_coding_skill():
    from orchestrator._tool_injection import (
        _build_injected_tools_addendum,
        _resolve_injected_scope,
    )
    full = _build_injected_tools_addendum()
    assert "### Coding skill" in full
    assert "code_task(task)" in full and "run_script(path" in full
    assert "agent-data/SCRIPTS.md" in full
    assert "list_integrations()" in full  # integration discoverability
    # Rides the floor: even a narrow scope's addendum documents it.
    scoped = _build_injected_tools_addendum(
        effective_scope=frozenset(_resolve_injected_scope(["web_search"]))
    )
    assert "### Coding skill" in scoped
    compact = _build_injected_tools_addendum(
        is_sub_agent=True,
        effective_scope=frozenset(_resolve_injected_scope(["web_search"])),
    )
    assert "run_script" in compact and "code_task" in compact


def test_risk_annotations_registered():
    from acb_skills.tool_annotations import TOOL_ANNOTATIONS
    for name in ("run_script", "code_task"):
        hints = TOOL_ANNOTATIONS[name]
        assert hints["open_world"] is True  # arbitrary code may reach out
        assert hints["destructive"] is False  # workspace-jailed, reversible
        assert hints["read_only"] is False
