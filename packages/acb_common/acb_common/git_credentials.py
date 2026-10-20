"""The one way a GitHub credential reaches ``git`` (H-363).

Until H-363 the loader wrote the operator's ``GITHUB_TOKEN`` into the remote
URL of each agent clone. So the token sat in plain text in ``.git/config``. A
run with a host shell (a personal agent, the root agent, ``run_script``) could
read it, then push with another command or call the GitHub API. A regex on the
command cannot close that path. Only the absence of the token can.

The rules of this module:

1. **A remote URL carries no secret.** :func:`public_url` builds every GitHub
   URL. :func:`scrub_clone` takes the userinfo out of each remote URL of an
   existing clone, and it removes each credential helper, extra header and
   ``insteadOf`` rewrite that holds a secret.
2. **A credential lives in ONE git process at a time, and the gateway starts
   that process.** The value reaches it in the env name
   :data:`CREDENTIAL_ENV`. An inline credential helper prints it for a ``get``
   on host ``github.com`` only. A process that an agent starts (a shell, a
   script, the Copilot CLI) never holds that name: ``child_env`` copies no
   such name, and the gateway puts it only into the env of the one process.
   So when an agent's shell calls the helper, the helper has no value and
   answers nothing. The helper reads no file, no socket and no key that the
   shell could read.
3. **The credentialed process runs no config and no hook that an agent can
   write.** It runs in a new bare repo in a gateway temp dir, with
   ``GIT_CONFIG_NOSYSTEM=1``, an empty ``GIT_CONFIG_GLOBAL``, no hooks and the
   https transport only. The objects of the clone reach it as an alternate.
   ``core.alternateRefsCommand=true`` stops git from starting a
   ``for-each-ref`` child inside the clone (that child would read the clone's
   config), and auto maintenance is off. Then the clone fetches from that
   bare repo with no credential. So a ``core.fsmonitor``, a hook or a proxy
   that a run planted in the clone never runs with the credential in its env.
   The objects of the clone are data only.
4. **A read** (clone, fetch, ``ls-remote``) uses ``github_read_token``, else
   ``github_token``. A public repo uses neither: git asks the helper only
   after the server answers 401.
5. **A push** uses ``github_token``. Only these callers push, and each holds
   its own gate: the approve route of the mutation queue
   (``_require_mutation_reviewer``), the end of a chat run whose member passes
   ``acb_auth.agent_operator.may_review_agent_commits``
   (``orchestrator.executor._push_reviewed_run``), and the operator opt-in
   ``MUTATION_AUTO_PUSH``.
6. **No git process that this module starts stores a credential.** Each one
   resets ``credential.helper`` first, so no ``store`` or manager helper sees
   a value. :func:`scrub_credential_stores` removes the lines that an earlier
   clone left in ``~/.git-credentials``.
7. **The repo is the loader's record, never the clone's ``origin``.** A run
   can rewrite ``origin``. So :func:`push` refuses a GitHub clone that has no
   record (:func:`pin_remote`), and :func:`fetch` and
   :func:`remote_default_branch` read such a clone with NO credential.

The fence is ``tests/unit/test_clone_token_h363.py``.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from acb_common._log import get_logger
from acb_common.child_env import child_env

__all__ = [
    "CREDENTIAL_ENV",
    "CREDENTIAL_USERNAME",
    "GITHUB_HOST",
    "GitHubRemote",
    "GitResult",
    "clone",
    "clone_remote",
    "current_branch",
    "fetch",
    "find_clone_secrets",
    "parse_github_remote",
    "pin_remote",
    "pinned_remote",
    "public_url",
    "push",
    "push_token",
    "read_token",
    "redact",
    "remote_default_branch",
    "run_with_credential",
    "scrub_clone",
    "scrub_credential_stores",
    "strip_url_secret",
    "url_has_secret",
]

_log = get_logger("acb_common.git_credentials")

#: The env name that holds the credential, in the env of ONE git process.
#: ``child_env`` never copies it, because it is on no allowlist.
CREDENTIAL_ENV = "METORITE_GIT_CREDENTIAL"
#: The only host the helper answers for.
GITHUB_HOST = "github.com"
#: GitHub takes any user name with a token. This one works for a PAT and for
#: a GitHub App installation token.
CREDENTIAL_USERNAME = "x-access-token"
#: The user names that an earlier loader wrote into a clone URL. A stored
#: credential line with one of them came from us.
_OUR_USERNAMES = frozenset({"x-token", CREDENTIAL_USERNAME})

#: The credential helper. git runs it as ``f get`` (or ``store``, ``erase``).
#: It answers a ``get`` for host github.com only, and only when this process
#: holds :data:`CREDENTIAL_ENV`. In an agent's shell it holds nothing, so it
#: exits with no answer and git fails with no prompt.
_HELPER = (
    "!f() { "
    'test "$1" = get || exit 0; '
    'test -n "${' + CREDENTIAL_ENV + ':-}" || exit 1; '
    "ok=; "
    'while IFS= read -r line && test -n "$line"; do '
    'test "$line" = "host=' + GITHUB_HOST + '" && ok=1; '
    "done; "
    'test "$ok" = 1 || exit 1; '
    "echo username=" + CREDENTIAL_USERNAME + "; "
    'echo "password=${' + CREDENTIAL_ENV + '}"; '
    "}; f"
)

#: The env of a git process that holds no credential. It never prompts.
_PLAIN_EXTRA: dict[str, str] = {
    "GIT_TERMINAL_PROMPT": "0",
    "GCM_INTERACTIVE": "never",
}

_NAME = r"[A-Za-z0-9_.-]+"
_GITHUB_URL = re.compile(
    r"^https://(?:[^@/\s]*@)?github\.com/(" + _NAME + r")/(" + _NAME + r")/?$",
    re.IGNORECASE,
)
_USERINFO = re.compile(r"^(https?://)([^@/\s]+)@", re.IGNORECASE)
_BRANCH = re.compile(r"^[A-Za-z0-9._/-]+$")
#: The shapes of a GitHub token. A fence scans a clone's config for them.
_TOKEN_SHAPES = re.compile(
    r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})",
)


# ── URLs ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class GitHubRemote:
    """A GitHub repo, named by owner and repo. Its URL holds no secret."""

    owner: str
    repo: str

    def __post_init__(self) -> None:
        for part in (self.owner, self.repo):
            if (not re.fullmatch(_NAME, part or "") or part.startswith("-")
                    or part in (".", "..")):
                raise ValueError(f"not a GitHub name: {part!r}")

    @property
    def url(self) -> str:
        return f"https://{GITHUB_HOST}/{self.owner}/{self.repo}.git"


def public_url(owner: str, repo: str) -> str:
    """The URL of *owner*/*repo*, with no credential in it."""
    return GitHubRemote(owner, repo.removesuffix(".git")).url


def parse_github_remote(url: str | None) -> GitHubRemote | None:
    """The repo of an https GitHub URL, or ``None`` for any other URL.

    Userinfo in the URL is ignored, so a clone that an older loader made
    still resolves. A local path, an ``ssh`` URL or another host gives
    ``None``, and no credential is ever sent to it.
    """
    match = _GITHUB_URL.match(str(url or "").strip())
    if match is None:
        return None
    try:
        return GitHubRemote(match.group(1), match.group(2).removesuffix(".git"))
    except ValueError:
        return None


def url_has_secret(url: str | None) -> bool:
    """True when an http(s) URL carries userinfo (a token, or user:token)."""
    return _USERINFO.match(str(url or "").strip()) is not None


def strip_url_secret(url: str) -> str:
    """*url* with the userinfo of an http(s) URL taken out."""
    return _USERINFO.sub(r"\1", str(url).strip(), count=1)


def redact(text: str, secrets: Iterable[str | None] = ()) -> str:
    """*text* with each secret and each URL userinfo replaced."""
    out = str(text or "")
    for secret in secrets:
        if secret and len(secret) >= 4:
            out = out.replace(secret, "***")
    return re.sub(r"(https?://)[^@/\s]+@", r"\1***@", out)


# ── Tokens ───────────────────────────────────────────────────────────────────


def _setting(name: str) -> str:
    from acb_common import get_settings

    return str(getattr(get_settings(), name, "") or "").strip()


def read_token() -> str | None:
    """The credential of a read: ``github_read_token``, else ``github_token``."""
    return _setting("github_read_token") or _setting("github_token") or None


def push_token() -> str | None:
    """The credential of a push: ``github_token``. Each caller holds a gate."""
    return _setting("github_token") or None


# ── Running git ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class GitResult:
    """The outcome of one git process. ``stderr`` is redacted."""

    returncode: int
    stdout: str = ""
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def _run(
    argv: Sequence[str],
    *,
    cwd: Path | str,
    extra: Mapping[str, str | None] | None = None,
    timeout: float = 60,
    stdin: str | None = None,
) -> GitResult:
    """Run ``git *argv`` with the env of ``child_env`` and *extra*."""
    secret = (extra or {}).get(CREDENTIAL_ENV)
    try:
        proc = subprocess.run(
            ["git", *argv],
            cwd=str(cwd),
            input=stdin,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=child_env(extra={**_PLAIN_EXTRA, **dict(extra or {})}),
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return GitResult(124, "", f"git {argv[0] if argv else ''} timed out")
    except OSError as exc:
        return GitResult(127, "", str(exc))
    # stdout is DATA (a config listing, a ref list), so only the credential
    # value goes. The scrub must read a token URL to remove it. stderr is for
    # logs, so URL userinfo goes too.
    out = proc.stdout or ""
    if secret:
        out = out.replace(secret, "***")
    return GitResult(proc.returncode, out, redact(proc.stderr or "", [secret]))


@contextmanager
def _workspace() -> Iterator[Path]:
    """A gateway temp dir: an empty global config and an empty hooks dir."""
    tmp = Path(tempfile.mkdtemp(prefix="metorite-git-"))
    try:
        (tmp / "empty.gitconfig").write_text("", encoding="utf-8")
        (tmp / "no-hooks").mkdir()
        yield tmp
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _isolated_extra(tmp: Path, credential: str | None) -> dict[str, str | None]:
    """The env of a process that reads only the config this module wrote."""
    extra: dict[str, str | None] = {
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": str(tmp / "empty.gitconfig"),
    }
    if credential:
        extra[CREDENTIAL_ENV] = credential
    return extra


def _isolated_args(tmp: Path) -> list[str]:
    """The ``-c`` options of a credentialed process. None holds a secret.

    The empty ``credential.helper`` resets the helper list, so only the
    helper of this module answers, and no stored credential is read or
    written. The helper is scoped to ``https://github.com``.
    """
    return [
        "-c", "credential.helper=",
        "-c", f"credential.https://{GITHUB_HOST}.helper={_HELPER}",
        "-c", f"core.hooksPath={(tmp / 'no-hooks').as_posix()}",
        "-c", "core.fsmonitor=false",
        "-c", "protocol.allow=never",
        "-c", "protocol.https.allow=always",
        # The alternate is the agent's clone. Its refs are not needed (the
        # seed gives the haves), and listing them starts a git child in the
        # clone, which reads the clone's config with the credential in env.
        "-c", "core.alternateRefsCommand=true",
        "-c", "maintenance.auto=false",
        "-c", "gc.auto=0",
    ]


def run_with_credential(
    argv: Sequence[str],
    *,
    cwd: Path | str,
    credential: str | None,
    timeout: float = 120,
) -> GitResult:
    """Run ``git *argv`` with *credential* in a dir that the GATEWAY made.

    For a throwaway checkout that no agent shares (the monorepo PR). For an
    agent clone, use :func:`fetch` and :func:`push`, which never run a
    credentialed process in a dir that a run can write.
    """
    with _workspace() as tmp:
        return _run(
            [*_isolated_args(tmp), *argv],
            cwd=cwd, extra=_isolated_extra(tmp, credential), timeout=timeout,
        )


def _git_dir(clone_dir: Path) -> Path | None:
    git = clone_dir / ".git"
    return git if git.is_dir() else None


def clone_remote(clone_dir: Path | str) -> GitHubRemote | None:
    """The GitHub repo of the clone's ``origin``, or ``None``.

    It reads the config FILE, with no repo discovery, so no config of the
    clone runs.
    """
    git = _git_dir(Path(clone_dir))
    if git is None or not (git / "config").is_file():
        return None
    with _workspace() as tmp:
        result = _run(
            ["config", "--file", str(git / "config"), "--get", "remote.origin.url"],
            cwd=tmp, extra=_isolated_extra(tmp, None), timeout=10,
        )
    return parse_github_remote(result.stdout.strip()) if result.ok else None


def current_branch(clone_dir: Path | str) -> str | None:
    """The branch that HEAD names, or ``None`` for a detached HEAD."""
    result = _run(["symbolic-ref", "--short", "-q", "HEAD"], cwd=clone_dir, timeout=10)
    name = result.stdout.strip()
    return name if result.ok and name else None


def _bare_with_alternates(tmp: Path, clone_dir: Path) -> Path | None:
    """A new bare repo in *tmp* that sees the objects of *clone_dir*."""
    git = _git_dir(clone_dir)
    if git is None or not (git / "objects").is_dir():
        return None
    bare = tmp / "repo.git"
    made = _run(
        ["init", "--bare", "-q", "--template=", str(bare)],
        cwd=tmp, extra=_isolated_extra(tmp, None), timeout=30,
    )
    if not made.ok:
        return None
    info = bare / "objects" / "info"
    info.mkdir(parents=True, exist_ok=True)
    # Bytes, so Windows writes no CR that git would read as part of the path.
    (info / "alternates").write_bytes(
        ((git / "objects").resolve().as_posix() + "\n").encode("utf-8"),
    )
    return bare


def _seed_haves(bare: Path, clone_dir: Path, tmp: Path) -> None:
    """Give *bare* the clone's ``origin`` refs, so a fetch sends only new objects."""
    refs = _run(
        ["for-each-ref", "--format=%(objectname) %(refname)", "refs/remotes/origin/"],
        cwd=clone_dir, timeout=15,
    )
    lines: list[str] = []
    prefix = "refs/remotes/origin/"
    for row in refs.stdout.splitlines() if refs.ok else []:
        sha, _, ref = row.partition(" ")
        name = ref[len(prefix):] if ref.startswith(prefix) else ""
        if name and name != "HEAD" and re.fullmatch(r"[0-9a-f]{40,64}", sha):
            lines.append(f"update refs/heads/{name} {sha}\n")
    if lines:
        _run(
            ["update-ref", "--stdin"], cwd=bare,
            extra=_isolated_extra(tmp, None), timeout=15, stdin="".join(lines),
        )


# ── The repo of each clone, as the loader recorded it ────────────────────────

#: ``resolved clone dir -> GitHubRemote``. The loader writes it when it clones
#: or refreshes a clone (``_ensure_repo``), from the registry, never from the
#: clone. It lives in the gateway's memory, which no agent shell can write.
_PINNED: dict[str, GitHubRemote] = {}


def _pin_key(clone_dir: Path | str) -> str:
    try:
        return str(Path(clone_dir).resolve())
    except OSError:
        return str(clone_dir)


def pin_remote(clone_dir: Path | str, remote: GitHubRemote) -> None:
    """Record that *clone_dir* is a clone of *remote*. The loader calls it."""
    _PINNED[_pin_key(clone_dir)] = remote


def pinned_remote(clone_dir: Path | str) -> GitHubRemote | None:
    """The loader's record of the repo of *clone_dir*, or ``None``."""
    return _PINNED.get(_pin_key(clone_dir))


def _read_target(
    clone_dir: Path, remote: GitHubRemote | None, cred: str | None,
) -> tuple[GitHubRemote | None, str | None]:
    """The repo and the credential of a read (rule 7)."""
    pinned = remote or pinned_remote(clone_dir)
    if pinned is not None:
        return pinned, cred
    origin = clone_remote(clone_dir)
    if origin is not None and cred:
        _log.warning("git_credentials.read_without_record", clone=clone_dir.name)
    return origin, None


# ── The four network acts ────────────────────────────────────────────────────

#: A sentinel: "resolve the read credential from the settings".
_READ: str = "\0read"


def clone(
    remote: GitHubRemote,
    dest: Path | str,
    *,
    credential: str | None = _READ,
    extra_args: Sequence[str] = (),
    timeout: float = 120,
) -> GitResult:
    """Clone *remote* into *dest*. The new ``.git/config`` holds no secret.

    *dest* is new, so no agent wrote a config in it. The clone runs with an
    empty global config, so ``init.templateDir`` and a ``store`` helper of
    the home dir do not apply.
    """
    cred = read_token() if credential == _READ else credential
    with _workspace() as tmp:
        return _run(
            [*_isolated_args(tmp), "clone", *extra_args, "--", remote.url, str(dest)],
            cwd=tmp, extra=_isolated_extra(tmp, cred), timeout=timeout,
        )


def fetch(
    clone_dir: Path | str,
    *,
    credential: str | None = _READ,
    remote: GitHubRemote | None = None,
    timeout: float = 60,
) -> GitResult:
    """Update ``refs/remotes/origin/*`` of *clone_dir* from its remote.

    A GitHub remote: the credentialed fetch runs in a gateway bare repo
    (rule 3), then the clone fetches from it with no credential. Any other
    remote (a local path in a test or on a dev box) gets a plain
    ``git fetch origin``, and no credential exists for it.

    *remote* names the repo, else the loader's record (:func:`pin_remote`).
    A GitHub clone with neither is read with NO credential (rule 7), from the
    repo that its ``origin`` names, so a public repo still updates.
    """
    clone_dir = Path(clone_dir)
    cred = read_token() if credential == _READ else credential
    remote, cred = _read_target(clone_dir, remote, cred)
    if remote is None:
        return _run(["fetch", "origin"], cwd=clone_dir, timeout=timeout)
    with _workspace() as tmp:
        bare = _bare_with_alternates(tmp, clone_dir)
        if bare is None:
            return GitResult(1, "", "the clone has no object store")
        _seed_haves(bare, clone_dir, tmp)
        got = _run(
            [*_isolated_args(tmp), "fetch", "--no-tags", "--no-write-fetch-head",
             remote.url, "+refs/heads/*:refs/heads/*"],
            cwd=bare, extra=_isolated_extra(tmp, cred), timeout=timeout,
        )
        if not got.ok:
            return got
        return _run(
            ["fetch", "--no-tags", bare.as_posix(), "+refs/heads/*:refs/remotes/origin/*"],
            cwd=clone_dir, timeout=timeout,
        )


def remote_default_branch(
    clone_dir: Path | str,
    *,
    credential: str | None = _READ,
    remote: GitHubRemote | None = None,
    timeout: float = 15,
) -> str | None:
    """The branch that the remote's HEAD names, or ``None``."""
    clone_dir = Path(clone_dir)
    cred = read_token() if credential == _READ else credential
    remote, cred = _read_target(clone_dir, remote, cred)
    if remote is None:
        result = _run(["ls-remote", "--symref", "origin", "HEAD"], cwd=clone_dir, timeout=timeout)
    else:
        with _workspace() as tmp:
            result = _run(
                [*_isolated_args(tmp), "ls-remote", "--symref", remote.url, "HEAD"],
                cwd=tmp, extra=_isolated_extra(tmp, cred), timeout=timeout,
            )
    if not result.ok:
        return None
    for line in result.stdout.splitlines():
        if line.startswith("ref: refs/heads/"):
            return line.split("refs/heads/", 1)[1].split("\t", 1)[0].strip() or None
    return None


def push(
    clone_dir: Path | str,
    *,
    src: str,
    branch: str,
    credential: str | None,
    remote: GitHubRemote | None = None,
    timeout: float = 60,
) -> GitResult:
    """Push commit *src* of *clone_dir* to ``refs/heads/<branch>`` of its remote.

    The CALLER holds the gate (rule 5) and passes :func:`push_token`. The push
    runs from a gateway bare repo that sees the clone's objects (rule 3), with
    ``--no-verify``. On success the clone's ``origin/<branch>`` moves too, so
    ``git branch -r --contains`` sees the pushed commit.

    The TARGET repo is *remote*, else the loader's record of the clone
    (:func:`pin_remote`), never the clone's ``origin``: a run can rewrite
    ``origin``, and a push with the operator's token must not go to a repo
    that the run chose. A GitHub clone with neither is REFUSED (rule 7).
    """
    clone_dir = Path(clone_dir)
    if not _BRANCH.fullmatch(branch or "") or ".." in branch or branch.startswith(("-", "/")):
        return GitResult(2, "", f"not a branch name: {branch!r}")
    resolved = _run(["rev-parse", "--verify", "-q", f"{src}^{{commit}}"], cwd=clone_dir, timeout=10)
    sha = resolved.stdout.strip()
    if not resolved.ok or not re.fullmatch(r"[0-9a-f]{40,64}", sha):
        return GitResult(2, "", f"no commit {src!r} in the clone")
    target = f"{sha}:refs/heads/{branch}"
    pinned = remote or pinned_remote(clone_dir)
    if pinned is None and clone_remote(clone_dir) is not None:
        _log.warning("git_credentials.push_refused_no_record", clone=clone_dir.name)
        return GitResult(3, "", "no record of this clone's repo, so no push (H-363)")
    remote = pinned
    if remote is None:
        result = _run(["push", "--no-verify", "origin", target], cwd=clone_dir, timeout=timeout)
    else:
        with _workspace() as tmp:
            bare = _bare_with_alternates(tmp, clone_dir)
            if bare is None:
                return GitResult(1, "", "the clone has no object store")
            result = _run(
                [*_isolated_args(tmp), "push", "--no-verify", remote.url, target],
                cwd=bare, extra=_isolated_extra(tmp, credential), timeout=timeout,
            )
    if result.ok:
        _run(["update-ref", f"refs/remotes/origin/{branch}", sha], cwd=clone_dir, timeout=10)
    return result


# ── Scrubbing what an earlier loader left ────────────────────────────────────


def _config_entries(config: Path, tmp: Path) -> list[tuple[str, str]]:
    listed = _run(
        ["config", "--file", str(config), "--null", "--list"],
        cwd=tmp, extra=_isolated_extra(tmp, None), timeout=10,
    )
    entries: list[tuple[str, str]] = []
    for item in listed.stdout.split("\0") if listed.ok else []:
        if item:
            key, _, value = item.partition("\n")
            entries.append((key, value))
    return entries


def scrub_clone(clone_dir: Path | str) -> list[str]:
    """Take every secret out of the clone's ``.git/config``. Return what went.

    It edits the config FILE (``git config --file``), so no config, hook or
    fsmonitor of the clone runs, and it is safe in a dir that a sandbox could
    write. Each returned line names a key, never a value.

    * the userinfo of each ``remote.*.url`` and ``remote.*.pushurl``;
    * each ``credential.*`` key (a helper, a username, a ``store`` file);
    * each ``http.*.extraheader`` (an ``Authorization`` header);
    * each ``url.<base>.insteadOf`` whose base holds userinfo.
    """
    git = _git_dir(Path(clone_dir))
    if git is None or not (git / "config").is_file():
        return []
    config = git / "config"
    removed: list[str] = []
    with _workspace() as tmp:
        ext = _isolated_extra(tmp, None)

        def _cfg(*args: str) -> GitResult:
            return _run(["config", "--file", str(config), *args], cwd=tmp, extra=ext, timeout=10)

        entries = _config_entries(config, tmp)
        by_key: dict[str, list[str]] = {}
        for key, value in entries:
            by_key.setdefault(key, []).append(value)
        for key, values in by_key.items():
            low = key.lower()
            if low.startswith("remote.") and low.endswith((".url", ".pushurl")):
                if any(url_has_secret(v) for v in values):
                    _cfg("--unset-all", key)
                    for value in values:
                        _cfg("--add", key, strip_url_secret(value))
                    removed.append(f"{key}: a credential in the URL")
            elif low.startswith("credential."):
                # The whole section goes, so no empty [credential] stays.
                section = key[:key.rfind(".")]
                if _cfg("--remove-section", section).ok:
                    removed.append(f"{section}: a credential section")
            elif low.startswith("http.") and low.endswith(".extraheader"):
                _cfg("--unset-all", key)
                removed.append(f"{key}: an extra header")
            elif low.startswith("url.") and low.endswith((".insteadof", ".pushinsteadof")):
                base = key[len("url."):key.rfind(".")]
                if url_has_secret(base):
                    _cfg("--remove-section", f"url.{base}")
                    removed.append("url.*.insteadOf: a credential in the URL")
    return removed


def _store_files(home: Path) -> list[Path]:
    xdg = os.environ.get("XDG_CONFIG_HOME") or str(home / ".config")
    return [home / ".git-credentials", Path(xdg) / "git" / "credentials"]


def _line_is_ours(line: str, tokens: Sequence[str]) -> bool:
    """True for a stored credential line that our clone URLs left behind."""
    match = re.match(r"^\s*https?://([^@/\s]+)@([^/\s:]+)", line, re.IGNORECASE)
    if match is None:
        return False
    user, _, password = match.group(1).partition(":")
    if match.group(2).lower() != GITHUB_HOST:
        return False
    return user in _OUR_USERNAMES or any(t and t in (user, password) for t in tokens)


def scrub_credential_stores(
    *,
    home: Path | str | None = None,
    tokens: Sequence[str] = (),
) -> list[str]:
    """Remove the github.com lines that our clone URLs left in a credential store.

    It reads ``~/.git-credentials`` and ``$XDG_CONFIG_HOME/git/credentials``.
    A line goes when its user name is one that a loader URL used
    (``x-token``, ``x-access-token``) or when it holds one of *tokens*. A
    member's own line for another user name stays. A store that cannot be
    written (``ProtectHome=read-only`` on the box) is reported, not hidden.

    ``gh`` keeps its own login in ``hosts.yml``. The gateway never writes it,
    so this only reports one. Each returned line names a path, never a value.
    """
    base = Path(home) if home is not None else Path.home()
    report: list[str] = []
    for path in _store_files(base):
        try:
            if not path.is_file():
                continue
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
        except OSError:
            continue
        keep = [ln for ln in lines if not _line_is_ours(ln, tokens)]
        if len(keep) == len(lines):
            continue
        count = len(lines) - len(keep)
        try:
            path.write_text("".join(keep), encoding="utf-8")
            report.append(f"{path}: removed {count} stored GitHub credential(s)")
        except OSError:
            report.append(f"{path}: holds {count} stored GitHub credential(s), and it is read-only")
    gh_dir = Path(os.environ.get("GH_CONFIG_DIR") or (base / ".config" / "gh"))
    hosts = gh_dir / "hosts.yml"
    try:
        if hosts.is_file() and "oauth_token" in hosts.read_text(encoding="utf-8", errors="replace"):
            report.append(f"{hosts}: a gh login with a token (not changed)")
    except OSError:
        pass
    return report


def find_clone_secrets(clone_root: Path | str, tokens: Sequence[str] = ()) -> list[str]:
    """The ``.git/config`` files under *clone_root* that still hold a secret.

    A secret is URL userinfo, a credential key, an extra header, a token
    shape (``ghp_``, ``github_pat_`` ...) or one of *tokens*. It returns the
    paths, never a value. The startup check and the fence both use it.
    """
    root = Path(clone_root)
    found: list[str] = []
    try:
        dirs = sorted(p for p in root.iterdir() if p.is_dir())
    except OSError:
        return found
    for clone_dir in dirs:
        config = clone_dir / ".git" / "config"
        try:
            text = config.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        low = text.lower()
        if (
            re.search(r"https?://[^@/\s]+@", text, re.IGNORECASE)
            or _TOKEN_SHAPES.search(text)
            or "extraheader" in low
            or re.search(r"^\s*\[credential", low, re.MULTILINE)
            or any(t and t in text for t in tokens)
        ):
            found.append(str(config))
    return found
