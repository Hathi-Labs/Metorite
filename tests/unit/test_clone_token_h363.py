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
            mapped.append(str(origin) if a == GH_URL else a)
        result = real(mapped, cwd=cwd, extra=extra, timeout=timeout, stdin=stdin)
        if result.ok and "clone" in argv and GH_URL in argv:
            real(["remote", "set-url", "origin", GH_URL], cwd=Path(argv[-1]))
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
    assert gc.fetch(clone).ok
    cred = _credentialed(fake_github)
    assert len(cred) == 1 and "fetch" in cred[0]["argv"]
    # It ran in the gateway's bare repo, never in the clone.
    assert Path(cred[0]["cwd"]).resolve() != clone.resolve()
    assert str(clone.resolve()) not in str(Path(cred[0]["cwd"]).resolve())
    for call in fake_github:
        if call not in cred:
            assert gc.CREDENTIAL_ENV not in call["extra"]


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
    asyncio.run(executor._detect_agent_commits(
        "x", str(clone), run_id, since_sha=since, thread_id="t-h363",
    ))
    return rows


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
    cred = _credentialed(fake_github)
    assert len(cred) == 1 and "push" in cred[0]["argv"]
    assert cred[0]["extra"][gc.CREDENTIAL_ENV] == FAKE
    assert all(FAKE not in a for c in fake_github for a in c["argv"])
    assert _git(origin, "rev-parse", "main") == head, "the reviewer's push did not land"
    row = {r["commit_sha"]: r for r in rows}[head]
    assert row["status"] == "approved" and row["reviewed_by"] == "chat:autopush"
    _assert_no_token(clone.parent, FAKE)
    _no_store_written(home)


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
