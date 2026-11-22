"""Only a first-party repo installs its declared deps on the host (task 3850).

``loader._install_agent_deps`` installs the deps that an agent repo declares
into agent-site, in the gateway's own interpreter, when the agent loads. A
wheel can run code on import, so a hostile package from a tenant repo would
sit beside the data of every tenant. D85, D129 and task 3848 close the
other host-install paths. The owner chose (a) on 2026-11-22: install the
deps of a first-party repo only, and refuse a tenant repo that declares deps
until the sandbox (SBX) runs it.

``loader._gated_install_agent_deps`` is the ONE gate, and the only caller of
the installer. ``loader.first_party_source`` is the ONE test of a first-party
source: a ``local_path`` inside the platform's ``apps/agents``, or a GitHub
repo whose owner is ``settings.github_org``. No earlier predicate described a
repo, so this one is new. A first-party source installs only when the dep
files of the working copy are the source's own (``_dep_files_untouched``)
and each line names an index project (``_plain_dep_line``). Else it installs
nothing and still loads, so a file that a run planted cannot stop a built-in.

Hermetic: git runs for real in a temp dir, and every other process is a
fake that records its argv. No package is ever installed, and no network is
reached. R8 binds nothing here.

Mutations this suite catches (R7), each run red by hand on 2026-11-22:

* the clone path calls ``_install_agent_deps`` with no gate ->
  ``test_a_tenant_github_agent_with_deps_is_refused`` and the fence;
* the ``local_path`` path calls it with no gate ->
  ``test_a_tenant_local_agent_with_deps_is_refused`` and the fence;
* the gateway startup calls it with no gate -> the two startup fences;
* the gateway startup drops ``repo_name`` and ``local_path`` ->
  ``test_startup_passes_the_registry_source``;
* ``first_party_source`` answers True for every repo -> both tenant tests,
  the skill test and the spoof test.
"""
from __future__ import annotations

import ast
import subprocess
import sys
import types
from pathlib import Path
from typing import Any

import pytest
from acb_skills import agent_site, loader

REPO = Path(__file__).resolve().parents[2]
PLATFORM_ORG = "FracktalWorks"
FAKE_PKG = "t3850-fake-agent-pkg==1.0"


# ── Fixtures ─────────────────────────────────────────────────────────────────


class _Log:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def _rec(self, event: str, *_a: Any, **kw: Any) -> None:
        self.events.append((event, kw))

    info = warning = error = debug = _rec

    def names(self) -> list[str]:
        return [e for e, _ in self.events]


@pytest.fixture
def log(monkeypatch: pytest.MonkeyPatch) -> _Log:
    rec = _Log()
    monkeypatch.setattr(loader, "_log", rec)
    return rec


@pytest.fixture(autouse=True)
def _agent_site(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    """The install needs the unit's two dirs. Give it temp ones."""
    root = tmp_path_factory.mktemp("agent-site-root")
    (root / "state").mkdir()
    (root / "cache").mkdir()
    monkeypatch.setattr(agent_site, "STATE_ROOT", root / "state")
    monkeypatch.setattr(agent_site, "AGENT_SITE", root / "state" / "agent-site")
    monkeypatch.setattr(agent_site, "CACHE_ROOT", root / "cache")
    monkeypatch.setattr(agent_site, "CONSTRAINTS", root / "cache" / "constraints.txt")
    monkeypatch.setattr(agent_site, "find_uv", lambda: "/stub/uv")
    monkeypatch.setattr(agent_site, "_frozen", {})


class _Done:
    returncode = 0
    stdout = ""
    stderr = ""


@pytest.fixture
def installs(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """The fake installer. git runs for real. Every other process is
    recorded and never started, so no package is ever installed."""
    real_run = subprocess.run
    calls: list[list[str]] = []

    def _run(cmd: Any, *args: Any, **kwargs: Any) -> Any:
        argv = [str(c) for c in cmd]
        if argv and Path(argv[0]).name.lower() in ("git", "git.exe"):
            return real_run(cmd, *args, **kwargs)
        calls.append(argv)
        return _Done()

    monkeypatch.setattr(subprocess, "run", _run)
    return calls


def _pip_installs(calls: list[list[str]]) -> list[list[str]]:
    return [c for c in calls if c[1:3] == ["pip", "install"]]


def _settings(tmp_path: Path) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        github_org=PLATFORM_ORG, github_token=None,
        agents_clone_dir=str(tmp_path / "agents"),
        github_bot_name="bot", github_bot_email="bot@example.com",
        agent_deps_allow_source_builds=False,
    )


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> types.SimpleNamespace:
    s = _settings(tmp_path)
    monkeypatch.setattr(loader, "get_settings", lambda: s)
    monkeypatch.setattr(loader, "_get_auth_token", lambda _s: None)
    monkeypatch.setattr(loader, "_sync_new_skills", lambda *_a, **_k: None)
    return s


@pytest.fixture
def platform_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A stand-in for the platform's ``apps/agents``. Only a local path inside
    it is first party."""
    root = tmp_path / "platform" / "apps" / "agents"
    root.mkdir(parents=True)
    monkeypatch.setattr(loader, "_platform_agents_root", lambda: root)
    return root


def _agent_source(path: Path, *, deps: bool) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / "agents.py").write_text("def build_agents():\n    return []\n", encoding="utf-8")
    if deps:
        (path / "requirements.txt").write_text(FAKE_PKG + "\n", encoding="utf-8")
    return path


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True,
    )


def _seed_clone(cache_root: Path, name: str, *, deps: bool) -> Path:
    """A clone that is already on disk, with its files committed."""
    clone = _agent_source(cache_root / name, deps=deps)
    _git(clone, "init", "-q")
    _git(clone, "config", "user.email", "t@example.com")
    _git(clone, "config", "user.name", "t")
    _git(clone, "add", "-A")
    _git(clone, "commit", "-q", "-m", "seed")
    return clone


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """The refresh of an existing clone reaches no remote."""
    monkeypatch.setattr(loader, "_refresh_remote_auth", lambda *_a, **_k: None)
    monkeypatch.setattr(loader, "_refresh_due", lambda _n: False)


def _load(name: str, **kw: Any) -> BaseException | None:
    """Load *name*. Return the AgentLoadError, or None when it loaded."""
    try:
        with loader.load_agent(name, **kw):
            pass
    except loader.AgentLoadError as exc:
        return exc
    return None


def _assert_refused(exc: BaseException | None, name: str) -> None:
    assert exc is not None, "the agent loaded"
    text = str(exc)
    assert "extra software" in text and name in text, text
    assert "t3850" not in text, "the refusal names a package"


# ── 1. The local_path path ───────────────────────────────────────────────────


def test_a_tenant_local_agent_with_deps_is_refused(
    tmp_path: Path, settings: Any, installs: list, log: _Log,
) -> None:
    """RED on origin/main: the installer ran pip for a tenant repo."""
    source = _agent_source(tmp_path / "tenant" / "agent-x", deps=True)
    exc = _load("tenant-x", local_path=str(source))
    assert _pip_installs(installs) == [], "a tenant repo reached the host installer"
    _assert_refused(exc, "tenant-x")
    refused = [kw for e, kw in log.events if e == "loader.agent_deps_refused"]
    assert refused and refused[0]["agent"] == "tenant-x"
    assert all("t3850" not in str(v) for _, kw in log.events for v in kw.values())


def test_a_first_party_local_agent_still_installs(
    platform_root: Path, settings: Any, installs: list,
) -> None:
    source = _agent_source(platform_root / "agent-own", deps=True)
    assert _load("own", local_path=str(source)) is None
    pip = _pip_installs(installs)
    assert len(pip) == 1, installs
    assert pip[0][pip[0].index("--only-binary") + 1] == ":all:"


def test_a_tenant_local_agent_with_no_deps_loads(
    tmp_path: Path, settings: Any, installs: list,
) -> None:
    source = _agent_source(tmp_path / "tenant" / "agent-plain", deps=False)
    assert _load("tenant-plain", local_path=str(source)) is None
    assert installs == []


def _skipped(log: _Log) -> list[str]:
    return [kw["reason"] for e, kw in log.events if e == "loader.agent_deps_skipped"]


def test_a_planted_dep_file_in_a_first_party_copy_is_not_installed(
    platform_root: Path, settings: Any, installs: list, log: _Log,
) -> None:
    """The sync leaves a file that only the copy holds. A run that wrote a
    requirements.txt there gets no install, and the agent still loads, so the
    file cannot stop a built-in agent for every org."""
    source = _agent_source(platform_root / "agent-own", deps=False)
    copy = Path(settings.agents_clone_dir) / "repos" / "own"
    copy.mkdir(parents=True)
    (copy / "requirements.txt").write_text(FAKE_PKG + "\n", encoding="utf-8")
    assert _load("own", local_path=str(source)) is None
    assert _pip_installs(installs) == []
    assert _skipped(log) == ["dep_files_changed"]
    assert "loader.agent_deps_refused" not in log.names()


def test_a_first_party_include_line_is_not_installed(
    platform_root: Path, settings: Any, installs: list, log: _Log,
) -> None:
    """A ``-r`` include reads a file that the copy check does not cover."""
    source = _agent_source(platform_root / "agent-own", deps=False)
    (source / "requirements.txt").write_text("-r extra.txt\n", encoding="utf-8")
    assert _load("own", local_path=str(source)) is None
    assert _pip_installs(installs) == []
    assert _skipped(log) == ["dep_line_names_a_file"]


@pytest.mark.parametrize(
    ("line", "plain"),
    [
        ("httpx>=0.27", True),
        ("skill-projects", True),
        ('pandas[excel]==2.2; sys_platform == "linux"', True),
        ("six==1.17.0 --hash=sha256:abc", True),
        ("-r extra.txt", False),
        ("-c constraints.txt", False),
        ("--find-links ./wheels", False),
        ("--index-url https://example.com/simple", False),
        ("./hostile-1.0-py3-none-any.whl", False),
        ("hostile-1.0-py3-none-any.whl", False),
        ("hostile @ file:///tmp/hostile.whl", False),
        ("hostile @ https://example.com/hostile.whl", False),
    ],
)
def test_a_plain_dep_line_names_only_an_index_project(line: str, plain: bool) -> None:
    assert loader._plain_dep_line(line) is plain


# ── 2. The clone path ────────────────────────────────────────────────────────


def test_a_tenant_github_agent_with_deps_is_refused(
    settings: Any, installs: list, no_network: None, log: _Log,
) -> None:
    """RED on origin/main: the installer ran pip for a tenant repo."""
    _seed_clone(Path(settings.agents_clone_dir) / "repos", "tenant-gh", deps=True)
    exc = _load("tenant-gh", repo_name="attacker/agent-gh")
    assert _pip_installs(installs) == [], "a tenant repo reached the host installer"
    _assert_refused(exc, "tenant-gh")


def test_a_first_party_github_agent_still_installs(
    settings: Any, installs: list, no_network: None,
) -> None:
    _seed_clone(Path(settings.agents_clone_dir) / "repos", "own-gh", deps=True)
    assert _load("own-gh", repo_name=f"{PLATFORM_ORG}/agent-own-gh") is None
    assert len(_pip_installs(installs)) == 1, installs


def test_a_new_dep_file_in_a_first_party_clone_is_not_installed(
    settings: Any, installs: list, no_network: None, log: _Log,
) -> None:
    clone = _seed_clone(Path(settings.agents_clone_dir) / "repos", "own-gh", deps=False)
    (clone / "requirements.txt").write_text(FAKE_PKG + "\n", encoding="utf-8")
    assert _load("own-gh", repo_name=f"{PLATFORM_ORG}/agent-own-gh") is None
    assert _pip_installs(installs) == []
    assert _skipped(log) == ["dep_files_changed"]


def test_a_tenant_skill_repo_with_deps_is_not_attached(
    settings: Any, installs: list, no_network: None, log: _Log,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A skill repo is judged by its own owner. A refused skill is skipped,
    and the agent still loads, as a skill that fails to clone does."""
    cache_root = Path(settings.agents_clone_dir) / "repos"
    monkeypatch.setattr(agent_site, "venv_version", lambda _name: None)
    _seed_clone(cache_root, "skill-hostile", deps=True)
    injected = loader._attach_skill_repos(
        ["skill-hostile"], agent_name="tenant-gh", org="attacker", token=None,
        cache_root=cache_root, settings=settings,
    )
    assert injected == []
    assert _pip_installs(installs) == []
    assert "loader.agent_deps_refused" in log.names()

    _seed_clone(cache_root, "skill-own", deps=True)
    injected = loader._attach_skill_repos(
        ["skill-own"], agent_name="own-gh", org=PLATFORM_ORG, token=None,
        cache_root=cache_root, settings=settings,
    )
    assert len(injected) == 1
    assert len(_pip_installs(installs)) == 1


# ── 3. The gateway startup ───────────────────────────────────────────────────


def test_startup_skips_and_logs_a_refused_agent(
    tmp_path: Path, settings: Any, installs: list, log: _Log,
) -> None:
    clone = _seed_clone(tmp_path / "repos", "tenant-gh", deps=True)
    ok = loader.warm_install_agent_deps(
        "tenant-gh", clone, settings, repo_name="attacker/agent-gh", local_path=None,
    )
    assert ok is False
    assert _pip_installs(installs) == []
    skipped = [kw for e, kw in log.events if e == "loader.agent_deps_skipped_at_startup"]
    assert skipped == [{"agent": "tenant-gh"}]


def test_startup_installs_a_first_party_clone(
    tmp_path: Path, settings: Any, installs: list,
) -> None:
    clone = _seed_clone(tmp_path / "repos", "own-gh", deps=True)
    ok = loader.warm_install_agent_deps(
        "own-gh", clone, settings, repo_name=f"{PLATFORM_ORG}/agent-own-gh",
        local_path=None,
    )
    assert ok is True
    assert len(_pip_installs(installs)) == 1


def _warm_clone_func() -> ast.AST:
    tree = ast.parse(
        (REPO / "apps/services/gateway/gateway/main.py").read_text(encoding="utf-8"),
    )
    found = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "_warm_clone_agents"
    ]
    assert len(found) == 1, "gateway.main._warm_clone_agents moved"
    return found[0]


def test_startup_installs_only_through_the_gate() -> None:
    """The startup sweep hands ``warm_install_agent_deps`` to ``to_thread``.
    It never names the raw installer."""
    func = _warm_clone_func()
    names = {
        n.id for n in ast.walk(func) if isinstance(n, ast.Name)
    } | {
        a.name for n in ast.walk(func) if isinstance(n, ast.ImportFrom) for a in n.names
    }
    assert "warm_install_agent_deps" in names
    assert "_install_agent_deps" not in names


def test_startup_passes_the_registry_source() -> None:
    """With no source, the predicate reads the default repo under
    ``github_org``. So the startup call must pass the entry's two values."""
    func = _warm_clone_func()
    calls = [
        n for n in ast.walk(func)
        if isinstance(n, ast.Call)
        and any(isinstance(a, ast.Name) and a.id == "warm_install_agent_deps" for a in n.args)
    ]
    assert len(calls) == 1, "one startup call of the gate"
    kw = {k.arg: k.value for k in calls[0].keywords}
    assert isinstance(kw.get("repo_name"), ast.Name) and kw["repo_name"].id == "repo_name"
    assert isinstance(kw.get("local_path"), ast.Name) and kw["local_path"].id == "local_path"
    sources = {
        t.id: ast.unparse(n.value)
        for n in ast.walk(func) if isinstance(n, ast.Assign)
        for t in n.targets if isinstance(t, ast.Name)
    }
    assert sources.get("repo_name") == "entry.get('repo_name')"
    assert sources.get("local_path") == "entry.get('local_path')"


# ── 4. The predicate ─────────────────────────────────────────────────────────


def test_the_platform_root_is_this_checkout() -> None:
    assert loader._platform_agents_root() == REPO / "apps" / "agents"


def test_every_built_in_agent_is_first_party() -> None:
    """A built-in agent that the gate read as a tenant repo would stop loading
    for every org. Each ``local_path`` of the code registry is first party."""
    import re

    text = (REPO / "apps/services/gateway/gateway/routes/agent.py").read_text(encoding="utf-8")
    paths = re.findall(r'"local_path":\s*"(apps/agents/[^"]+)"', text)
    assert len(paths) >= 9, paths
    s = _settings(REPO)
    for rel in paths:
        assert (REPO / rel).is_dir(), rel
        assert loader.first_party_source(
            "x", repo_name=None, local_path=str(REPO / rel), settings=s,
        ), rel


@pytest.mark.parametrize(
    ("repo_name", "expected"),
    [
        (None, True),                                    # agent-<name> under github_org
        ("agent-x", True),                               # a bare name is under github_org
        (f"{PLATFORM_ORG}/agent-x", True),
        (f"{PLATFORM_ORG.lower()}/agent-x", True),       # GitHub owners ignore case
        ("attacker/agent-x", False),
        (f"{PLATFORM_ORG}-evil/agent-x", False),
        (f"{PLATFORM_ORG}/../attacker/agent-x", False),  # a dot segment
        (f"attacker/{PLATFORM_ORG}", False),
        (f"{PLATFORM_ORG}/", False),
    ],
)
def test_a_github_source_is_first_party_only_under_the_platform_org(
    repo_name: str | None, expected: bool, tmp_path: Path,
) -> None:
    assert loader.first_party_source(
        "x", repo_name=repo_name, local_path=None, settings=_settings(tmp_path),
    ) is expected


def test_a_local_source_is_first_party_only_inside_the_platform_tree(
    tmp_path: Path, platform_root: Path,
) -> None:
    s = _settings(tmp_path)
    inside = _agent_source(platform_root / "agent-own", deps=False)
    outside = _agent_source(tmp_path / "tenant" / "agent-x", deps=False)

    def fp(path: str) -> bool:
        return loader.first_party_source("x", repo_name=None, local_path=path, settings=s)

    assert fp(str(inside))
    assert not fp(str(outside))
    assert not fp(str(platform_root)), "the root itself is no agent"
    assert not fp(str(platform_root / ".." / ".." / ".." / "tenant" / "agent-x"))
    # A local path wins over the repo, as it does in load_agent.
    assert not loader.first_party_source(
        "x", repo_name=f"{PLATFORM_ORG}/agent-x", local_path=str(outside), settings=s,
    )


# ── 5. The fence: no path reaches the host installer past the gate ───────────


def _sources_naming(word: str) -> list[Path]:
    out = []
    for top in ("apps", "packages"):
        for path in (REPO / top).rglob("*.py"):
            if "node_modules" in path.parts or "tests" in path.parts:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if word in text:
                out.append(path)
    return out


def _references(path: Path, word: str) -> list[str]:
    """The name of the function around each reference to *word*: a name, an
    attribute, an import alias or a ``getattr`` string. ``<module>`` for one
    at module level."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[str] = []

    def visit(node: ast.AST, owner: str) -> None:
        for child in ast.iter_child_nodes(node):
            here = owner
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                here = child.name
                if child.name == word:
                    found.append(f"def:{owner}")
            hit = (
                (isinstance(child, ast.Name) and child.id == word)
                or (isinstance(child, ast.Attribute) and child.attr == word)
                or (isinstance(child, ast.alias) and word in (child.name, child.asname))
                or (isinstance(child, ast.Constant) and child.value == word)
            )
            if hit:
                found.append(here)
            visit(child, here)

    visit(tree, "<module>")
    return found


def test_only_the_gate_calls_the_host_installer() -> None:
    """R7: every reference to ``_install_agent_deps`` outside the tests is the
    def itself or the one call inside ``_gated_install_agent_deps``."""
    loader_py = REPO / "packages/acb_skills/acb_skills/loader.py"
    bad: list[str] = []
    for path in _sources_naming("_install_agent_deps"):
        rel = path.relative_to(REPO).as_posix()
        for owner in _references(path, "_install_agent_deps"):
            if path == loader_py and owner in ("def:<module>", "_gated_install_agent_deps"):
                continue
            bad.append(f"{rel}: {owner}")
    assert bad == [], "the host installer is reachable past the first-party gate"
    assert _references(loader_py, "_install_agent_deps").count("_gated_install_agent_deps") == 1


def test_only_the_two_installers_build_a_pip_command() -> None:
    """R7: ``agent_site.install_command`` builds the pip call. Only the gated
    installer and the ``install_dependency`` tool (D85, task 3848) use it."""
    allowed = {
        ("packages/acb_skills/acb_skills/loader.py", "_install_agent_deps"),
        ("packages/acb_skills/acb_skills/agent_site.py", "def:<module>"),
        ("packages/acb_skills/acb_skills/agent_site.py", "<module>"),  # __all__
    }
    seen: set[tuple[str, str]] = set()
    for path in _sources_naming("install_command"):
        rel = path.relative_to(REPO).as_posix()
        for owner in _references(path, "install_command"):
            seen.add((rel, owner))
    extra = {
        s for s in seen
        if s not in allowed and s[0] != "packages/acb_skills/acb_skills/dep_tools.py"
    }
    assert extra == set(), "a new caller builds a host pip install"


def test_each_load_path_goes_through_the_gate() -> None:
    """The clone path (``_ensure_repo``) and the ``local_path`` path
    (``load_agent``) each call the gate, and the gate asks the predicate."""
    loader_py = REPO / "packages/acb_skills/acb_skills/loader.py"
    gate = _references(loader_py, "_gated_install_agent_deps")
    assert "_ensure_repo" in gate and "load_agent" in gate and "warm_install_agent_deps" in gate
    assert "_gated_install_agent_deps" in _references(loader_py, "first_party_source")
    assert "_gated_install_agent_deps" in _references(loader_py, "_dep_files_untouched")


def test_the_refusal_never_starts_a_self_repair() -> None:
    """The batch path answers the refusal before its AgentLoadError clause,
    which opens a self-mutation."""
    text = (REPO / "apps/services/orchestrator/orchestrator/executor.py").read_text(
        encoding="utf-8",
    )
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        order = [
            h.type.id for h in node.handlers
            if isinstance(h.type, ast.Name)
        ]
        if "AgentLoadError" in order and "AgentRuntimeUnsupported" in order:
            assert "AgentDepsRefused" in order
            assert order.index("AgentDepsRefused") < order.index("AgentLoadError")
            return
    pytest.fail("the batch path's AgentLoadError clause moved")


def test_the_refusal_is_a_load_error() -> None:
    assert issubclass(loader.AgentDepsRefused, loader.AgentLoadError)
    assert sys.modules["acb_skills.loader"] is loader
