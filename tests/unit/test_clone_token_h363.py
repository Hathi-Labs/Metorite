"""H-363 — no GitHub token in an agent clone, and none for a non-reviewer push.

The loader wrote the operator's ``GITHUB_TOKEN`` into the remote URL of each
agent clone (``x-token:<token>@github.com``). A run with a host shell could
read it from ``.git/config`` and push, or call the GitHub API, past every
regex of #1168. Only the absence of the token closes that path.

``acb_common.git_credentials`` is now the one way a credential reaches git.
These tests prove each claim with real git, against local repos. Every token
here is fake. A stand-in for github.com (:func:`fake_github`) maps the GitHub
URL to a local bare repo, so no test reaches the network. It records the argv
and the env extras of every git process that the seam starts.

1. The clone and the refresh leave no token in ``.git/config``.
2. No env of an agent child holds a token or the one-shot name.
3. The helper gives the token only to the process that the gateway gave the
   one-shot value, only for github.com, and no helper stores it.
4. A push request of a member who is not a reviewer gets no credential. A
   reviewer's push gets it, in one process only.
5. The startup scrub removes a planted token from a clone and a store.
6. No source file builds a URL with a secret in its userinfo.
"""
from __future__ import annotations

import asyncio
import contextlib
import re
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from acb_common import git_credentials as gc
from acb_common.child_env import child_env, copilot_env, docker_env

FAKE = "ghp_H363fakeTOKENfakeTOKEN0123456789ab"
READ_FAKE = "github_pat_H363readFAKEreadFAKE_0123456789"
OWNER, REPO = "FracktalWorks", "agent-h363"
GH_URL = f"https://github.com/{OWNER}/{REPO}.git"
REPO_ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.skipif(
    subprocess.run(["git", "--version"], capture_output=True).returncode != 0,
    reason="git is not installed",
)


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
        encoding="utf-8",
    ).stdout.strip()


def _commit(repo: Path, name: str) -> str:
    (repo / f"{name}.txt").write_text(name + "\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", f"feat: {name}")
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture(autouse=True)
def _no_pins(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each test starts with no loader record of a clone's repo."""
    monkeypatch.setattr(gc, "_PINNED", {})


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A home dir of the test's own, so no real store or config is touched."""
    h = tmp_path / "home"
    h.mkdir()
    for name in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(name, str(h))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(h / ".config"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    (h / ".gitconfig").write_text(
        "[user]\n\tname = h363\n\temail = h363@example.com\n"
        "[commit]\n\tgpgsign = false\n[init]\n\tdefaultBranch = main\n",
        encoding="utf-8",
    )
    return h


@pytest.fixture
def tokens(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """The settings the seam reads: a push token, and no read token."""
    values = {"github_token": FAKE, "github_read_token": ""}
    monkeypatch.setattr(gc, "_setting", lambda name: values.get(name, ""))
    return values


@pytest.fixture
def origin(tmp_path: Path, home: Path) -> Path:
    """A local bare repo that stands in for github.com/OWNER/REPO."""
    bare = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(bare))
    seed = tmp_path / "seed"
    _git(tmp_path, "init", "-q", "-b", "main", str(seed))
    _commit(seed, "base")
    _git(seed, "push", "-q", str(bare), "main:main")
    return bare


@pytest.fixture
def fake_github(origin: Path, monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Map GH_URL to *origin* in every git process of the seam. Record each one.

    Only the URL changes, and the https-only rule drops so that the file
    transport of the stand-in works. The env extras reach git unchanged.
    After a clone, ``origin`` is set back to GH_URL, as a real clone would.
    """
    calls: list[dict[str, Any]] = []
    real = gc._run
    # The repo's URL, with or without userinfo, so a URL that carries a token
    # still reaches the stand-in (and a mutation that puts it there shows).
    ours = re.compile(r"https://(?:[^@/\s]+@)?github\.com/" + OWNER + "/" + REPO + r"\.git")

    def _run(argv: Any, *, cwd: Any, extra: Any = None, timeout: float = 60,
             stdin: Any = None) -> gc.GitResult:
        argv = [str(a) for a in argv]
        calls.append({"argv": list(argv), "extra": dict(extra or {}), "cwd": str(cwd)})
        mapped: list[str] = []
        skip = False
        for i, a in enumerate(argv):
            if skip:
                skip = False
                continue
            if a == "-c" and i + 1 < len(argv) and argv[i + 1] == "protocol.allow=never":
                skip = True
                continue
            if ours.fullmatch(a):
                a = str(origin)
            elif re.match(r"https://([^@/\s]+@)?github\.com/", a):
                # Any other GitHub repo: a path that does not exist, so a
                # test never reaches the network, and the act fails.
                a = str(origin.parent / "no-such-remote.git")
            mapped.append(a)
        result = real(mapped, cwd=cwd, extra=extra, timeout=timeout, stdin=stdin)
        url = next((a for a in argv if ours.fullmatch(a)), None)
        if result.ok and "clone" in argv and url:
            # A real clone records the URL it was given.
            real(["remote", "set-url", "origin", url], cwd=Path(argv[-1]))
        return result

    monkeypatch.setattr(gc, "_run", _run)
    return calls


def _credentialed(calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [c for c in calls if c["extra"].get(gc.CREDENTIAL_ENV)]


def _assert_no_token(clone_root: Path, *secrets: str) -> None:
    """The fence of H-363: read every clone's .git/config, fail on any token."""
    assert gc.find_clone_secrets(clone_root, list(secrets)) == []
    for config in clone_root.glob("*/.git/config"):
        text = config.read_text(encoding="utf-8")
        for secret in secrets:
            assert secret not in text, config
        assert not re.search(r"https?://[^@/\s]+@", text), text


def _no_store_written(home: Path) -> None:
    assert not (home / ".git-credentials").exists()
    assert not (home / ".config" / "git" / "credentials").exists()


@pytest.fixture
def loader(monkeypatch: pytest.MonkeyPatch) -> Any:
    from acb_skills import loader as mod

    monkeypatch.setattr(mod, "_install_agent_deps", lambda *_a, **_k: None)
    monkeypatch.setattr(mod, "_sync_new_skills", lambda *_a, **_k: None)
    return mod


def _settings(tmp_path: Path) -> Any:
    return SimpleNamespace(
        github_token=FAKE, github_read_token="", mutation_pr_token="",
        agents_clone_dir=str(tmp_path / "agents"), github_bot_name="bot",
        github_bot_email="bot@example.com",
    )


# ── 1. The clone and the refresh write no token ──────────────────────────────


def test_a_new_clone_holds_no_token(
    tmp_path: Path, home: Path, tokens: dict, fake_github: list, loader: Any,
) -> None:
    settings = _settings(tmp_path)
    root = Path(settings.agents_clone_dir) / "repos"
    clone = loader._ensure_repo(
        REPO, org=OWNER, token=FAKE, cache_root=root, settings=settings, clone_as="x",
    )
    assert (clone / "base.txt").is_file(), "the clone did not work"
    assert _git(clone, "config", "--get", "remote.origin.url") == GH_URL
    _assert_no_token(root, FAKE)
    _no_store_written(home)
    # The read credential (github_token, as no read token is set) reached the
    # one clone process through the env, never through its argv.
    cred = _credentialed(fake_github)
    assert len(cred) == 1 and "clone" in cred[0]["argv"]
    assert cred[0]["extra"][gc.CREDENTIAL_ENV] == FAKE
    assert cred[0]["extra"]["GIT_CONFIG_NOSYSTEM"] == "1"
    assert all(FAKE not in a for c in fake_github for a in c["argv"])


def test_a_read_token_wins_over_the_push_token(
    tmp_path: Path, home: Path, tokens: dict, fake_github: list, loader: Any,
) -> None:
    tokens["github_read_token"] = READ_FAKE
    settings = _settings(tmp_path)
    root = Path(settings.agents_clone_dir) / "repos"
    loader._ensure_repo(REPO, org=OWNER, token=FAKE, cache_root=root, settings=settings)
    assert {c["extra"][gc.CREDENTIAL_ENV] for c in _credentialed(fake_github)} == {READ_FAKE}
    _assert_no_token(root, FAKE, READ_FAKE)


def _old_style_clone(origin: Path, root: Path, name: str = "x") -> Path:
    """A clone as the OLD loader left it: the token in its URL, and more."""
    clone = root / name
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "clone", "-q", str(origin), str(clone))
    _git(clone, "remote", "set-url", "origin", f"https://x-token:{FAKE}@github.com/{OWNER}/{REPO}.git")
    _git(clone, "remote", "set-url", "--push", "origin", f"https://{FAKE}@github.com/{OWNER}/{REPO}.git")
    _git(clone, "config", "credential.helper", "store")
    _git(clone, "config", "credential.https://github.com.username", "x-token")
    _git(clone, "config", "http.https://github.com/.extraheader", f"AUTHORIZATION: basic {FAKE}")
    _git(clone, "config", f"url.https://x-token:{FAKE}@github.com/.insteadOf", "https://github.com/")
    _git(clone, "config", "user.name", "kept")
    return clone


def test_the_refresh_scrubs_an_old_clone_and_still_pulls(
    tmp_path: Path, home: Path, tokens: dict, origin: Path, fake_github: list, loader: Any,
) -> None:
    settings = _settings(tmp_path)
    root = Path(settings.agents_clone_dir) / "repos"
    clone = _old_style_clone(origin, root)
    assert gc.find_clone_secrets(root, [FAKE]), "the planted clone must trip the fence"
    # A new commit upstream, which the pull must bring in.
    seed = tmp_path / "seed"
    head = _commit(seed, "upstream")
    _git(seed, "push", "-q", str(origin), "main:main")

    loader._ensure_repo(REPO, org=OWNER, token=FAKE, cache_root=root, settings=settings, clone_as="x")

    _assert_no_token(root, FAKE)
    assert _git(clone, "config", "--get", "remote.origin.url") == GH_URL
    assert _git(clone, "config", "--get", "user.name") == "kept"
    assert _git(clone, "rev-parse", "HEAD") == head, "the pull did not fast-forward"
    _no_store_written(home)


# ── 2. No env of an agent child holds a token ────────────────────────────────


def test_no_agent_child_env_holds_a_token(
    tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch, loader: Any,
) -> None:
    for name in ("GITHUB_TOKEN", "GH_TOKEN", "COPILOT_GITHUB_TOKEN", gc.CREDENTIAL_ENV):
        monkeypatch.setenv(name, FAKE)
    from acb_skills import code_tools
    from acb_skills.write_artifact import artifact_context_scope, bind_artifact_context

    with artifact_context_scope():
        bind_artifact_context(
            agent_name="probe", workspace_root=str(tmp_path), integrations=["github"],
        )
        script_env = code_tools._script_env()
    for env in (child_env(), copilot_env(), docker_env(), loader._git_env(), script_env):
        assert FAKE not in env.values()
        assert gc.CREDENTIAL_ENV not in env
    # A shell's own git fails at once, with no prompt that could hang it.
    for env in (copilot_env(), script_env):
        assert env["GIT_TERMINAL_PROMPT"] == "0"


def test_only_the_network_process_holds_the_credential(
    tmp_path: Path, home: Path, tokens: dict, origin: Path, fake_github: list,
) -> None:
    root = tmp_path / "repos"
    root.mkdir()
    clone = root / "x"
    _git(root, "clone", "-q", str(origin), str(clone))
    _git(clone, "remote", "set-url", "origin", GH_URL)
    gc.pin_remote(clone, gc.GitHubRemote(OWNER, REPO))  # as the loader does
    assert gc.fetch(clone).ok
    cred = _credentialed(fake_github)
    assert len(cred) == 1 and "fetch" in cred[0]["argv"]
    # It ran in the gateway's bare repo, never in the clone.
    assert Path(cred[0]["cwd"]).resolve() != clone.resolve()
    assert str(clone.resolve()) not in str(Path(cred[0]["cwd"]).resolve())
    for call in fake_github:
        if call not in cred:
            assert gc.CREDENTIAL_ENV not in call["extra"]


def test_the_credentialed_fetch_starts_no_git_in_the_clone(
    tmp_path: Path, home: Path, tokens: dict, origin: Path, fake_github: list,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rule 3: git lists an alternate's refs with a child IN the clone.

    That child reads the clone's config with the credential in its env.
    ``GIT_TRACE`` shows each child that git starts.
    """
    root = tmp_path / "repos"
    root.mkdir()
    clone = root / "x"
    _git(root, "clone", "-q", str(origin), str(clone))
    _git(clone, "remote", "set-url", "origin", GH_URL)
    gc.pin_remote(clone, gc.GitHubRemote(OWNER, REPO))
    trace = tmp_path / "trace.txt"
    monkeypatch.setitem(gc._PLAIN_EXTRA, "GIT_TRACE", str(trace))
    seed = tmp_path / "seed"
    _commit(seed, "upstream-2")
    _git(seed, "push", "-q", str(origin), "main:main")
    assert gc.fetch(clone).ok
    lines = trace.read_text(encoding="utf-8", errors="replace").splitlines()
    clone_git = (clone / ".git").resolve().as_posix().lower()
    starts = [ln for ln in lines if "run_command:" in ln and "for-each-ref" in ln
              and clone_git in ln.replace("\\", "/").lower()]
    assert not starts, "a credentialed git started a child in the clone:\n" + "\n".join(starts)


# ── 3. The helper ────────────────────────────────────────────────────────────


def _fill(tmp: Path, env: dict[str, str], host: str = "github.com") -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *gc._isolated_args(tmp), "credential", "fill"],
        cwd=tmp, input=f"protocol=https\nhost={host}\n\n", capture_output=True,
        text=True, encoding="utf-8", env=env, timeout=30,
    )


def test_the_helper_answers_only_the_gated_process(
    tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", FAKE)
    with gc._workspace() as tmp:
        gated = child_env(extra={**gc._PLAIN_EXTRA, **gc._isolated_extra(tmp, FAKE)})
        out = _fill(tmp, gated)
        assert out.returncode == 0 and f"password={FAKE}" in out.stdout
        # An agent's shell runs the same helper with its own env. It has no
        # one-shot value, so the helper answers nothing.
        for shell_env in (copilot_env(), child_env(extra=gc._PLAIN_EXTRA)):
            shell = _fill(tmp, {**shell_env, **{
                k: v for k, v in gc._isolated_extra(tmp, None).items() if v is not None}})
            assert shell.returncode != 0 and FAKE not in shell.stdout + shell.stderr
        # Another host (an insteadOf or a planted URL) gets nothing.
        evil = _fill(tmp, gated, host="evil.example")
        assert evil.returncode != 0 and FAKE not in evil.stdout + evil.stderr


def _approve(tmp: Path, env: dict[str, str], args: list[str]) -> None:
    subprocess.run(
        ["git", *args, "credential", "approve"], cwd=tmp,
        input=f"protocol=https\nhost=github.com\nusername=x\npassword={FAKE}\n\n",
        capture_output=True, text=True, encoding="utf-8", env=env, timeout=30,
    )


def test_no_helper_of_the_home_dir_stores_the_credential(tmp_path: Path, home: Path) -> None:
    """A ``store`` helper in ~/.gitconfig would write ~/.git-credentials."""
    with (home / ".gitconfig").open("a", encoding="utf-8") as f:
        f.write("[credential]\n\thelper = store\n")
    with gc._workspace() as tmp:
        # The control: plain git with this home DOES store the value, so the
        # test can go red. GIT_CONFIG_NOSYSTEM keeps a system helper (the
        # Windows credential manager) out, so the fake never reaches it.
        _approve(tmp, child_env(extra={**gc._PLAIN_EXTRA, "GIT_CONFIG_NOSYSTEM": "1"}), [])
        assert FAKE in (home / ".git-credentials").read_text(encoding="utf-8")
        (home / ".git-credentials").unlink()
        # The seam's process stores nothing.
        _approve(tmp, child_env(extra={**gc._PLAIN_EXTRA, **gc._isolated_extra(tmp, FAKE)}),
                 gc._isolated_args(tmp))
        _no_store_written(home)


# ── 4. The push: a reviewer gets the credential, nobody else does ───────────


@pytest.fixture
def agent_clone(tmp_path: Path, home: Path, origin: Path) -> tuple[Path, str, str]:
    root = tmp_path / "repos"
    root.mkdir()
    clone = root / "x"
    _git(root, "clone", "-q", str(origin), str(clone))
    _git(clone, "remote", "set-url", "origin", GH_URL)
    # The loader pins each clone's repo when it loads the agent.
    gc.pin_remote(clone, gc.GitHubRemote(OWNER, REPO))
    base = _git(clone, "rev-parse", "HEAD")
    head = _commit(clone, "agent-change")
    return clone, base, head


@contextlib.contextmanager
def _run_scope(run_id: str, *, push_allowed: bool):
    from acb_skills.write_artifact import artifact_context_scope, bind_artifact_context

    with artifact_context_scope():
        bind_artifact_context(
            agent_name="x", run_id=run_id, workspace_root=str(Path.cwd()),
            host_shell_refused=False, agent_push_allowed=push_allowed,
        )
        yield


def _detect(monkeypatch: pytest.MonkeyPatch, clone: Path, since: str, run_id: str,
            gate: tuple[bool, str]) -> list[dict[str, Any]]:
    import orchestrator.executor as executor
    import orchestrator.mutation as mutation

    rows: list[dict[str, Any]] = []

    async def _register(**kw: Any) -> None:
        rows.append(kw)

    async def _diff(_d: str, _sha: str) -> str:
        return "diff"

    async def _gate() -> tuple[bool, str]:
        return gate

    monkeypatch.setattr(executor, "_host_git_ok", lambda p: bool(p))
    monkeypatch.setattr(executor, "_graph_session_opener", lambda tid: None)
    monkeypatch.setattr(executor, "_agent_push_allowed", _gate)
    monkeypatch.setattr(executor, "record", lambda *_a, **_k: None)
    monkeypatch.setattr(mutation, "_register_pending_commit", _register)
    monkeypatch.setattr(mutation, "_git_diff", _diff)
    async def _end_of_turn() -> None:
        # As every run boundary does: the one push step, then the scan.
        notes.append(await executor._settle_agent_push(
            "x", str(clone), run_id, since_sha=since, thread_id="t-h363",
        ))
        await executor._detect_agent_commits(
            "x", str(clone), run_id, since_sha=since, thread_id="t-h363",
        )

    notes: list[Any] = []
    asyncio.run(_end_of_turn())
    _NOTES[run_id] = notes[0]
    return rows


#: The member-visible push text of each driven run, by run id.
_NOTES: dict[str, Any] = {}


_PUSH = {"kind": "shell", "full_command_text": "git push --no-verify origin HEAD"}


def test_the_guard_notes_a_push_for_a_reviewer_only() -> None:
    from acb_skills import permission_policy as pp

    guarded = pp.guard_shared_agent_shell(lambda r, i: "approved")
    with _run_scope("run-guard-no", push_allowed=False):
        guarded(_PUSH, {"session_id": "s"})
    assert pp.take_agent_push_request("run-guard-no") is False
    with _run_scope("run-guard-yes", push_allowed=True):
        assert guarded(_PUSH, {"session_id": "s"}) == "approved"
    assert pp.take_agent_push_request("run-guard-yes") is True
    assert pp.take_agent_push_request("run-guard-yes") is False, "taken once"


def test_a_non_reviewer_push_gets_no_credential(
    monkeypatch: pytest.MonkeyPatch, tokens: dict, origin: Path, fake_github: list,
    agent_clone: tuple[Path, str, str],
) -> None:
    from acb_skills import permission_policy as pp

    clone, base, head = agent_clone
    # Even a request that reached the map (forged, or noted before a role
    # change) gets nothing: the executor asks the reviewer rule again.
    pp._PUSH_REQUESTS["run-no"] = None
    rows = _detect(monkeypatch, clone, base, "run-no", (False, "not a reviewer"))
    assert _credentialed(fake_github) == [], "a non-reviewer got the credential"
    assert not [c for c in fake_github if "push" in c["argv"]]
    assert _git(origin, "rev-parse", "main") == base, "the commit reached the remote"
    row = {r["commit_sha"]: r for r in rows}[head]
    assert row["status"] == "pending" and row["reviewed_by"] is None


def test_a_reviewer_push_gets_the_credential_in_one_process(
    monkeypatch: pytest.MonkeyPatch, tokens: dict, origin: Path, fake_github: list,
    agent_clone: tuple[Path, str, str], home: Path,
) -> None:
    from acb_skills import permission_policy as pp

    clone, base, head = agent_clone
    with _run_scope("run-yes", push_allowed=True):
        assert pp.note_agent_push_request() is True
    rows = _detect(monkeypatch, clone, base, "run-yes", (True, ""))
    # One push process holds the push credential. The fresh fetch before it
    # (to read the remote tip) holds the read credential, in its own process.
    cred = [c for c in _credentialed(fake_github) if "push" in c["argv"]]
    assert len(cred) == 1
    assert cred[0]["extra"][gc.CREDENTIAL_ENV] == FAKE
    assert all(
        "push" in c["argv"] or "fetch" in c["argv"] for c in _credentialed(fake_github)
    )
    assert "Metorite pushed" in _NOTES["run-yes"]
    assert all(FAKE not in a for c in fake_github for a in c["argv"])
    assert _git(origin, "rev-parse", "main") == head, "the reviewer's push did not land"
    row = {r["commit_sha"]: r for r in rows}[head]
    assert row["status"] == "approved" and row["reviewed_by"] == "chat:autopush"
    _assert_no_token(clone.parent, FAKE)
    _no_store_written(home)


def test_a_reviewer_push_goes_to_the_pinned_repo_not_to_origin(
    monkeypatch: pytest.MonkeyPatch, tokens: dict, origin: Path, fake_github: list,
    agent_clone: tuple[Path, str, str],
) -> None:
    """A run rewrote ``origin`` to another repo. The push uses the loader's record."""
    from acb_skills import permission_policy as pp

    clone, base, head = agent_clone
    gc.pin_remote(clone, gc.GitHubRemote(OWNER, REPO))
    other = "https://github.com/FracktalWorks/Metorite.git"
    _git(clone, "remote", "set-url", "origin", other)
    with _run_scope("run-pin", push_allowed=True):
        assert pp.note_agent_push_request() is True
    _detect(monkeypatch, clone, base, "run-pin", (True, ""))
    pushes = [c for c in _credentialed(fake_github) if "push" in c["argv"]]
    assert len(pushes) == 1 and GH_URL in pushes[0]["argv"]
    assert all(other not in a for c in fake_github for a in c["argv"])
    assert _git(origin, "rev-parse", "main") == head


def test_a_clone_with_no_record_gets_no_credential(
    monkeypatch: pytest.MonkeyPatch, tokens: dict, origin: Path, fake_github: list,
    agent_clone: tuple[Path, str, str],
) -> None:
    """After a restart no clone is pinned. Its agent-writable origin gets nothing.

    A reviewer asked to push, and the registry has no entry for the agent.
    The push is refused, and a fetch reads with NO credential.
    """
    import gateway.routes.agent as agent_routes
    from acb_skills import permission_policy as pp

    clone, base, _head = agent_clone
    monkeypatch.setattr(gc, "_PINNED", {})
    monkeypatch.setattr(agent_routes, "_load_dynamic_agents", lambda: [])
    with _run_scope("run-unpinned", push_allowed=True):
        assert pp.note_agent_push_request() is True
    _detect(monkeypatch, clone, base, "run-unpinned", (True, ""))
    assert _credentialed(fake_github) == [], "an unpinned clone got the credential"
    assert _git(origin, "rev-parse", "main") == base
    assert gc.fetch(clone).ok, "a read of an unpinned clone must still work"
    assert _credentialed(fake_github) == []


def test_the_routes_pin_a_clone_from_the_registry(
    monkeypatch: pytest.MonkeyPatch, agent_clone: tuple[Path, str, str],
) -> None:
    import gateway.routes.agent as agent_routes

    clone, _base, _head = agent_clone
    monkeypatch.setattr(gc, "_PINNED", {})
    entries = [
        {"name": "x", "repo_name": f"{OWNER}/{REPO}"},
        {"name": "local", "local_path": "C:/dev/local"},
    ]
    monkeypatch.setattr(agent_routes, "_load_dynamic_agents", lambda: entries)
    _git(clone, "remote", "set-url", "origin", "https://github.com/FracktalWorks/Metorite.git")
    assert agent_routes._pin_registry_clone("x", clone) is True
    assert gc.pinned_remote(clone) == gc.GitHubRemote(OWNER, REPO), "the pin read origin"
    assert agent_routes._pin_registry_clone("local", clone.parent / "local") is False
    assert agent_routes._pin_registry_clone("nobody", clone.parent / "nobody") is False
    assert gc.pinned_remote(clone.parent / "nobody") is None


def test_a_push_never_carries_a_commit_of_another_run(
    monkeypatch: pytest.MonkeyPatch, tokens: dict, origin: Path, fake_github: list,
    agent_clone: tuple[Path, str, str],
) -> None:
    """The clone is shared. A pending commit from before this turn stays out.

    ``agent_clone`` made one commit that is not on the remote. This turn
    starts AFTER it and makes one more. A push of HEAD would carry both, so
    the step refuses, and the member reads why.
    """
    from acb_skills import permission_policy as pp

    clone, base, earlier = agent_clone
    mine = _commit(clone, "this-turn")
    # A SHA planted in the hook queue does not widen the window either.
    (clone / ".git" / "cc-commits-queue").write_text(earlier + "\n", encoding="utf-8")
    with _run_scope("run-mixed", push_allowed=True):
        assert pp.note_agent_push_request() is True
    _detect(monkeypatch, clone, earlier, "run-mixed", (True, ""))
    assert not [c for c in _credentialed(fake_github) if "push" in c["argv"]]
    assert _git(origin, "rev-parse", "main") == base
    assert "did not push" in _NOTES["run-mixed"] and "earlier commit" in _NOTES["run-mixed"]
    assert mine != earlier


def test_a_clone_whose_config_is_not_clean_gets_no_credential(
    tokens: dict, origin: Path, fake_github: list, agent_clone: tuple[Path, str, str],
) -> None:
    """A secret the scrub cannot remove (here, a token in a comment) fails closed."""
    clone, _base, head = agent_clone
    with (clone / ".git" / "config").open("a", encoding="utf-8") as f:
        f.write(f"# left by hand: {FAKE}\n")
    pushed = gc.push(clone, src=head, branch="main", credential=FAKE)
    assert pushed.returncode == 4 and "still holds a secret" in pushed.stderr
    gc.fetch(clone)
    assert _credentialed(fake_github) == [], "an unclean clone got the credential"
    assert _git(origin, "rev-parse", "main") != head


def test_a_config_that_git_cannot_parse_is_not_clean(agent_clone: tuple[Path, str, str]) -> None:
    clone, _base, _head = agent_clone
    (clone / ".git" / "config").write_text("[remote \"origin\"\n\turl = x\n", encoding="utf-8")
    assert gc.config_is_clean(clone) is False
    assert gc.find_clone_secrets(clone.parent) == [str(clone / ".git" / "config")]


def test_no_push_request_means_no_push(
    monkeypatch: pytest.MonkeyPatch, tokens: dict, origin: Path, fake_github: list,
    agent_clone: tuple[Path, str, str],
) -> None:
    """A reviewer who did not ask to push gets no push and no credential."""
    clone, base, _head = agent_clone
    _detect(monkeypatch, clone, base, "run-quiet", (True, ""))
    assert _credentialed(fake_github) == []
    assert _git(origin, "rev-parse", "main") == base


# ── 5. The startup scrub ─────────────────────────────────────────────────────


def test_the_startup_scrub_removes_a_planted_token(
    tmp_path: Path, home: Path, origin: Path, loader: Any,
) -> None:
    settings = _settings(tmp_path)
    root = Path(settings.agents_clone_dir) / "repos"
    clone = _old_style_clone(origin, root)
    store = home / ".git-credentials"
    store.write_text(
        f"https://x-token:{FAKE}@github.com\n"
        f"https://someone:{FAKE}@github.com\n"
        "https://alice:alice-own-pw@github.com\n"
        "https://bob:bob-pw@gitlab.example\n",
        encoding="utf-8",
    )
    assert gc.find_clone_secrets(root, [FAKE])

    report = loader.scrub_clone_credentials(settings, home=home)

    _assert_no_token(root, FAKE)
    assert _git(clone, "config", "--get", "remote.origin.url") == GH_URL
    assert _git(clone, "config", "--get", "user.name") == "kept"
    left = store.read_text(encoding="utf-8")
    assert FAKE not in left
    assert "alice:alice-own-pw@github.com" in left and "gitlab.example" in left
    assert report and all(FAKE not in line for line in report)


def test_a_read_only_store_is_reported(tmp_path: Path, home: Path, monkeypatch) -> None:
    store = home / ".git-credentials"
    store.write_text(f"https://x-token:{FAKE}@github.com\n", encoding="utf-8")

    def _refuse(self: Path, *_a: Any, **_k: Any) -> int:
        raise PermissionError("read-only")

    monkeypatch.setattr(Path, "write_text", _refuse)
    report = gc.scrub_credential_stores(home=home, tokens=[FAKE])
    assert any("read-only" in line for line in report)
    assert all(FAKE not in line for line in report)


# ── 6. The source ────────────────────────────────────────────────────────────

#: An f-string URL with a ``{...}`` in its userinfo, as the old loader and the
#: old monorepo PR built: ``https://x-token:{token}@github.com/...``.
_SECRET_URL = re.compile(r"""https?://[^/\s'"]*\{[^}]+\}[^/\s'"]*@""")


def test_no_source_builds_a_url_with_a_secret() -> None:
    hits: list[str] = []
    for top in ("apps", "packages"):
        for path in (REPO_ROOT / top).rglob("*.py"):
            if "node_modules" in path.parts or "/tests/" in path.as_posix():
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for n, line in enumerate(text.splitlines(), 1):
                if _SECRET_URL.search(line):
                    hits.append(f"{path.relative_to(REPO_ROOT)}:{n}: {line.strip()[:120]}")
    assert not hits, "a URL with a secret in it:\n" + "\n".join(hits)


def test_the_seam_redacts_a_token_in_git_output() -> None:
    assert FAKE not in gc.redact(f"fatal: https://x-token:{FAKE}@github.com/o/r", [FAKE])
    assert gc.strip_url_secret(f"https://x-token:{FAKE}@github.com/o/r.git") == (
        "https://github.com/o/r.git"
    )
    assert gc.url_has_secret(f"https://{FAKE}@github.com/o/r") is True
    assert gc.url_has_secret("git@github.com:o/r.git") is False
    assert gc.parse_github_remote("https://evil.example/o/r.git") is None
    assert gc.parse_github_remote("ext::sh -c x") is None


# ── 7. Every stream path runs the one push step (review P0) ──────────────────


class _Resp:
    def __init__(self, text: str) -> None:
        self.text = text
        self.messages: list[Any] = []


class _BatchAgent:
    """No ``run(stream=True)`` iterator, so the stream takes its Tier 2 path."""

    def __init__(self, run_id: str) -> None:
        self.name = "test-agent"
        self.tools: list[Any] = []
        self.default_options: dict[str, Any] = {}
        self._run_id = run_id

    async def run(self, *_a: Any, **_k: Any) -> _Resp:
        from acb_skills import permission_policy as pp

        pp._PUSH_REQUESTS[self._run_id] = None  # the shell asked for a push
        return _Resp("done, and pushed")

    async def __aenter__(self) -> "_BatchAgent":
        return self

    async def __aexit__(self, *_a: Any) -> bool:
        return False


class _NativeAgent(_BatchAgent):
    """``run(stream=True)`` yields updates, so the stream takes native MAF."""

    def run(self, *_a: Any, **_k: Any) -> Any:  # type: ignore[override]
        return self._stream()

    async def _stream(self) -> Any:
        from acb_skills import permission_policy as pp

        pp._PUSH_REQUESTS[self._run_id] = None
        yield SimpleNamespace(
            role="assistant", message_id="m1",
            contents=[SimpleNamespace(type="text", text="done, and pushed")],
        )


def _stream_turn(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, agent: Any,
                 run_id: str, gate: tuple[bool, str]) -> tuple[list[dict], list[Any]]:
    import json

    executor = pytest.importorskip("orchestrator.executor")
    from acb_common import get_settings
    from acb_common.db import bind_tenant, release_tenant

    class _Loaded:
        agent_dir = tmp_path
        agent_name = "test-agent"
        config: dict[str, Any] = {}

        def build_agents(self) -> list[Any]:
            return [agent]

    class _Ctx:
        def __enter__(self) -> _Loaded:
            return _Loaded()

        def __exit__(self, *_a: Any) -> bool:
            return False

    pushes: list[Any] = []

    async def _gate() -> tuple[bool, str]:
        return gate

    async def _push(name: str, agent_dir: str, rid: str, *, since_sha: Any) -> tuple[bool, str]:
        pushes.append((name, rid))
        return True, "1 commit(s) to main"

    monkeypatch.setattr(get_settings(), "agents_clone_dir", str(tmp_path / "agents"))
    monkeypatch.setattr(executor, "load_agent", lambda *a, **k: _Ctx())
    monkeypatch.setattr(executor, "build_integrations", lambda *a, **k: ({}, {}))
    monkeypatch.setattr(executor, "_agent_push_allowed", _gate)
    monkeypatch.setattr(executor, "_push_reviewed_run", _push)
    monkeypatch.setattr(executor, "record", lambda *_a, **_k: None)
    token = bind_tenant("org-h363-stream")
    try:
        async def _collect() -> list[str]:
            return [line async for line in executor.run_agent_stream(
                "test-agent-unregistered", {"message": "commit and push"}, run_id=run_id,
            )]
        lines = asyncio.run(_collect())
    finally:
        release_tenant(token)
    events: list[dict] = []
    for line in lines:
        for part in line.split("\n"):
            part = part.strip()
            body = part[5:].strip() if part.startswith("data:") else ""
            if body and body != "[DONE]":
                with contextlib.suppress(ValueError):
                    events.append(json.loads(body))
    return events, pushes


def _chat_text(events: list[dict]) -> str:
    return "".join(str(e.get("delta") or "") for e in events
                   if e.get("type") == "TEXT_MESSAGE_CONTENT")


@pytest.mark.filterwarnings("ignore:coroutine .*run.* was never awaited:RuntimeWarning")
@pytest.mark.parametrize("shape", ["native", "tier2"])
@pytest.mark.parametrize("reviewer", [True, False])
def test_every_stream_path_settles_a_push_request(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, shape: str, reviewer: bool,
) -> None:
    """One native MAF turn and one Tier 2 turn, each with a push request.

    A reviewer's request reaches the gated push, and the chat says so. A
    non-reviewer's does not, and the chat says no push happened. Either way
    the request is gone after the turn.
    """
    from acb_skills import permission_policy as pp

    run_id = f"run-stream-{shape}-{reviewer}"
    agent = _NativeAgent(run_id) if shape == "native" else _BatchAgent(run_id)
    events, pushes = _stream_turn(
        monkeypatch, tmp_path, agent, run_id, (reviewer, "" if reviewer else "not a reviewer"),
    )
    types = [e.get("type") for e in events]
    assert "RUN_FINISHED" in types, types
    text = _chat_text(events)
    if reviewer:
        assert pushes == [("test-agent-unregistered", run_id)], "the gated push did not run"
        assert "Metorite pushed" in text
    else:
        assert pushes == [], "a non-reviewer reached the push"
        assert "Metorite did not push" in text
    # The note lands BEFORE the end of the turn, where the member reads it.
    last_text = max(i for i, t in enumerate(types) if t == "TEXT_MESSAGE_CONTENT")
    assert last_text < types.index("RUN_FINISHED")
    assert pp.take_agent_push_request(run_id) is False


@pytest.mark.filterwarnings("ignore:coroutine .*run.* was never awaited:RuntimeWarning")
def test_a_failed_turn_drops_the_request_and_pushes_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    from acb_skills import permission_policy as pp

    run_id = "run-stream-fails"

    class _Fails(_BatchAgent):
        async def run(self, *_a: Any, **_k: Any) -> _Resp:
            pp._PUSH_REQUESTS[self._run_id] = None
            raise RuntimeError("boom after the push request")

    _events, pushes = _stream_turn(monkeypatch, tmp_path, _Fails(run_id), run_id, (True, ""))
    assert pushes == []
    assert pp.take_agent_push_request(run_id) is False


def test_every_run_boundary_calls_the_one_push_step() -> None:
    """A source check: each boundary settles, and none pushes on its own."""
    import inspect

    executor = pytest.importorskip("orchestrator.executor")
    src = inspect.getsource(executor)
    # native MAF, Copilot, Tier 2, batch and sub-agent.
    assert src.count("await _settle_agent_push(") >= 5
    assert src.count("await _push_reviewed_run(") == 1, "only the one step pushes"
    for boundary in ("run_agent_stream", "_run_agent_inner", "_run_sub_agent_streaming"):
        body = inspect.getsource(getattr(executor, boundary))
        assert "_settle_agent_push(" in body, boundary
        assert "_drop_agent_push(" in body, boundary


# ── 8. The mutation container never gets the push token (review P1) ─────────


def test_the_mutation_container_never_gets_the_push_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    mutation = pytest.importorskip("orchestrator.mutation")
    for name in ("COPILOT_GITHUB_TOKEN", "GITHUB_COPILOT_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GITHUB_TOKEN", FAKE)
    spawned: list[dict[str, Any]] = []

    class _Proc:
        returncode = 1

        async def communicate(self) -> tuple[bytes, bytes]:
            return b"", b"no docker here"

    async def _exec(*argv: Any, **kw: Any) -> _Proc:
        spawned.append({"argv": [str(a) for a in argv], "env": dict(kw.get("env") or {})})
        return _Proc()

    async def _no_pull(*_a: Any, **_k: Any) -> None:
        return None

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _exec)
    monkeypatch.setattr(mutation, "_stash_pull_before_mutation", _no_pull)
    monkeypatch.setattr(mutation, "_build_mutation_prompt", lambda _t: "fix it")

    def _run(settings: Any) -> None:
        asyncio.run(mutation._run_mutation_sandbox(
            "x", "run-m", "run-m", {"local_clone_dir": None}, settings,
        ))

    base = {"llm_api_key": "", "litellm_master_key": "", "mutation_timeout_seconds": 5}
    # 1. Only the push token is set: the container does not start at all.
    _run(SimpleNamespace(github_token=FAKE, **base))
    assert spawned == [], "the container started with no Copilot-only token"
    # 2. The operator copied the push token into the Copilot name: refused too.
    monkeypatch.setenv("COPILOT_GITHUB_TOKEN", FAKE)
    _run(SimpleNamespace(github_token=FAKE, **base))
    assert spawned == []
    # 3. A Copilot-only token: it goes by NAME in argv, by value in env only.
    copilot_only = "ghu_H363copilotONLYcopilotONLY0123"
    monkeypatch.setenv("COPILOT_GITHUB_TOKEN", copilot_only)
    _run(SimpleNamespace(github_token=FAKE, **base))
    assert len(spawned) == 1
    argv, env = spawned[0]["argv"], spawned[0]["env"]
    assert all(FAKE not in a and copilot_only not in a for a in argv), "a token in argv"
    assert FAKE not in env.values(), "the push token reached the container env"
    assert env.get("COPILOT_GITHUB_TOKEN") == copilot_only
    assert "COPILOT_GITHUB_TOKEN" in argv and "GATEWAY_API_KEY" in argv


def test_mutation_source_passes_no_github_token_to_a_container() -> None:
    import inspect

    mutation = pytest.importorskip("orchestrator.mutation")
    src = inspect.getsource(mutation._run_mutation_sandbox)
    assert "github_token" not in src.replace("``github_token``", ""), (
        "_run_mutation_sandbox reads github_token again"
    )
    assert not re.search(r'"-e",\s*f"[A-Z_]*(TOKEN|KEY)[A-Z_]*=', src), "a secret value in argv"
