"""WS-49 BH-6 — root units run only root-owned files (fence BH-F4).

Spec: project-docs/specs/box_hardening.md §5 BH-6.

acb-backup, acb-health-watchdog and acb.service run as root. They ran scripts
from the checkout and read the compose project from it, and acb owns the
checkout. They also loaded the acb-writable .env. Now:

- each root unit runs a file of the ROOT COPY at /usr/local/lib/acb, which
  deploy/hostinger/root_lib_files.txt lists;
- scripts/vps_apply.sh writes that copy from `git archive "$DEPLOY_TARGET_SHA"`
  through a 0700 root stage and one rsync. It refuses a symlink, and it runs
  only while no BH-2 rollback is on;
- /etc/acb/root.env (root:root 0600) holds only the names of
  deploy/hostinger/root_env_names.txt. scripts/root_env.sh builds it;
- every compose call names the root copy as its project dir and root.env as
  its env file.

The static tests run everywhere. The shell tests need Linux (bash, git,
rsync, tar), so they skip on the Windows dev box and run in CI. Run them in a
Linux container as a user that is not root before a PR: a skip is not a pass.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from tests.unit.test_backup_env_values import _ROOT_RUN, _lines, _run
from tests.unit.test_bh2_rollback import ACK, DROPIN, NOW, Box

ROOT = Path(__file__).resolve().parents[2]
UNITS = ROOT / "deploy" / "hostinger"
LIST = UNITS / "root_lib_files.txt"
NAMES = UNITS / "root_env_names.txt"
APPLY = ROOT / "scripts" / "vps_apply.sh"
DEPLOY = UNITS / "deploy.sh"
SECRETS = ROOT / "scripts" / "secrets.sh"
ROOT_ENV_SH = ROOT / "scripts" / "root_env.sh"
BACKUP = ROOT / "scripts" / "backup_db.sh"
COMPOSE = ROOT / "infra" / "docker-compose.yml"
MANIFEST = ROOT / "deploy" / "secrets" / "manifest.json"

COPY = "/usr/local/lib/acb"
ROOT_ENV = "/etc/acb/root.env"
PROJECT_DIR = "--project-directory /usr/local/lib/acb/infra"
ENV_FILE_ARG = "--env-file /etc/acb/root.env"
COMPOSE_FILE_ARG = "-f /usr/local/lib/acb/infra/docker-compose.yml"
_PATH_KEYS = ("ExecStart", "ExecStartPre", "ExecStartPost", "ExecStop", "ExecReload",
              "EnvironmentFile", "WorkingDirectory", "Environment")

_LINUX = sys.platform.startswith("linux") and all(
    shutil.which(t) for t in ("bash", "git", "rsync", "tar", "install")
)
needs_linux = pytest.mark.skipif(not _LINUX, reason="needs Linux with bash, git, rsync and tar")


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _code_lines(text: str) -> list[str]:
    """Executable lines, with each `\\` continuation joined to one line."""
    joined = text.replace("\\\n", " ")
    return [ln for ln in joined.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]


def _copy_list() -> list[tuple[str, str]]:
    out = []
    for raw in _read(LIST).splitlines():
        parts = raw.split()
        if not parts or parts[0].startswith("#"):
            continue
        assert len(parts) == 2, raw
        out.append((parts[0], parts[1]))
    return out


def _names() -> list[str]:
    return [ln.strip() for ln in _read(NAMES).splitlines() if ln.strip() and not ln.strip().startswith("#")]


def _unit_keys(unit: Path) -> dict[str, list[str]]:
    """The keys of a unit and its drop-ins, as systemd merges them."""
    files = [unit, *sorted((unit.parent / f"{unit.name}.d").glob("*.conf"))]
    keys: dict[str, list[str]] = {}
    for f in files:
        for raw in _read(f).splitlines():
            line = raw.strip()
            if not line or line.startswith(("#", ";", "[")) or "=" not in line:
                continue
            k, v = line.split("=", 1)
            keys.setdefault(k.strip(), []).append(v.strip())
    return keys


def _root_units() -> list[Path]:
    return [u for u in sorted(UNITS.glob("*.service"))
            if (_unit_keys(u).get("User") or ["root"])[-1] in ("root", "0")]


def _in_copy(path: str) -> str | None:
    """The repo path of a path under the root copy, or None. A dir that only
    holds copies (infra/) gives its own relative path."""
    if not path.startswith(COPY + "/"):
        return None
    rel = path[len(COPY) + 1:].rstrip("/")
    for src, dst in _copy_list():
        if rel == dst:
            return src
        if rel.startswith(dst + "/"):
            return src + rel[len(dst):]
    if any(dst.startswith(rel + "/") for _, dst in _copy_list()):
        return rel
    return None


# ── The units ─────────────────────────────────────────────────────────────


def test_the_scan_finds_the_three_root_units() -> None:
    """The companion: a scan that finds nothing passes every test below."""
    assert {u.name for u in _root_units()} == {
        "acb-backup.service", "acb-health-watchdog.service", "acb.service",
    }


@pytest.mark.parametrize("unit", [u.name for u in _root_units()])
def test_a_root_unit_names_no_path_under_opt_acb_or_home(unit: str) -> None:
    """Acceptance 1. Mutation: point a unit at /opt/acb/app again, and this
    goes red."""
    keys = _unit_keys(UNITS / unit)
    bad = [f"{k}={v}" for k in _PATH_KEYS for v in keys.get(k, [])
           if "/opt/acb" in v or "/home" in v]
    assert bad == [], bad


def _followed_scripts() -> dict[str, set[str]]:
    """unit -> each repo script it runs, directly or through another script.
    It follows `bash <path>`, `. <path>` and BH2_ROLLBACK_SCRIPT."""
    lib_ref = re.compile(r'(?:^|[\s;(])(?:bash|\.|source)\s+"?\$\{?(?:script_dir|here)\}?/([\w.-]+)"?')
    out: dict[str, set[str]] = {}
    for unit in _root_units():
        keys = _unit_keys(unit)
        seen: set[str] = set()
        todo: list[str] = []
        for v in keys.get("ExecStart", []) + keys.get("ExecStop", []):
            for tok in v.split():
                if tok.startswith(COPY + "/") and tok.endswith(".sh"):
                    src = _in_copy(tok)
                    assert src, f"{unit.name} runs {tok}, which root_lib_files.txt does not list"
                    todo.append(src)
        env = dict(e.split("=", 1) for e in keys.get("Environment", []) if "=" in e)
        while todo:
            src = todo.pop()
            if src in seen:
                continue
            seen.add(src)
            text = _read(ROOT / src)
            for name in lib_ref.findall("\n".join(_code_lines(text))):
                dep = _in_copy(f"{COPY}/{name}")
                assert dep, f"{src} runs {name} from its own dir, and the copy does not hold it"
                todo.append(dep)
            if "BH2_ROLLBACK_SCRIPT" in text:
                target = env.get("BH2_ROLLBACK_SCRIPT", "")
                dep = _in_copy(target)
                assert dep, f"{unit.name} runs {src}, which reads BH2_ROLLBACK_SCRIPT, and the unit sets {target!r}"
                todo.append(dep)
        out[unit.name] = seen
    return out


def test_each_script_a_root_unit_runs_is_in_the_root_copy() -> None:
    """Acceptance 2. Mutation: drop the BH2_ROLLBACK_SCRIPT line from the
    watchdog unit, or a script from root_lib_files.txt, and this goes red."""
    found = _followed_scripts()
    assert found["acb-backup.service"] == {
        "scripts/backup_db.sh", "scripts/offbox_lib.sh", "scripts/backup_offbox.sh",
    }, found
    assert found["acb-health-watchdog.service"] == {
        "deploy/hostinger/health-watchdog.sh", "scripts/bh2_rollback.sh",
    }, found


def test_each_root_copy_path_a_unit_names_is_listed() -> None:
    for unit in _root_units():
        keys = _unit_keys(unit)
        for k in _PATH_KEYS:
            for v in keys.get(k, []):
                for tok in re.findall(r"/usr/local/lib/acb/[\w./-]+", v):
                    assert _in_copy(tok), f"{unit.name} {k} names {tok}, which the copy does not hold"


def test_the_copy_list_holds_plain_paths_that_exist() -> None:
    pairs = _copy_list()
    dsts = [d for _, d in pairs]
    assert len(set(dsts)) == len(dsts), "two lines write one path"
    for src, dst in pairs:
        for p in (src, dst):
            assert re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9._/-]*", p) and ".." not in p.split("/"), p
        assert (ROOT / src).exists(), src
    for flat in ("backup_db.sh", "backup_offbox.sh", "offbox_lib.sh", "health-watchdog.sh",
                 "bh2_rollback.sh", "root_env.sh", "root_env_names.txt"):
        assert flat in dsts, flat
    # The gateway writes infra/provider_models_cache.json, so the copy holds no
    # other infra file.
    assert not [d for d in dsts if d.startswith("infra/") and d not in {
        "infra/docker-compose.yml", "infra/postgres/00_create_databases.sql",
        "infra/postgres/01_schema.sql"}]


def test_the_copy_holds_each_path_the_compose_file_needs() -> None:
    """⚠️ `docker compose config -q` does NOT see a missing bind source or
    build context (measured on Compose v5.6.0). So this test, and the check of
    the sync, must. The sandbox profile is the one exempt build: it runs from
    the checkout, and never from a root unit."""
    compose = yaml.safe_load(_read(COMPOSE))
    base = f"{COPY}/infra"
    for name, svc in compose["services"].items():
        if "sandbox" in svc.get("profiles", []):
            continue
        refs = []
        build = svc.get("build")
        if isinstance(build, dict) and "context" in build:
            refs.append(build["context"])
        for vol in svc.get("volumes", []):
            src = vol.split(":", 1)[0]
            if src.startswith((".", "/")):
                refs.append(src)
        for ref in refs:
            path = os.path.normpath(f"{base}/{ref}").replace("\\", "/")
            assert _in_copy(path), f"{name} needs {ref}, and the copy does not hold {path}"


# ── The name list and root.env ───────────────────────────────────────────


def test_the_name_list_holds_plain_names_and_no_dangerous_one() -> None:
    """Acceptance 4, static half."""
    names = _names()
    assert names and len(set(names)) == len(names)
    for n in names:
        assert re.fullmatch(r"[A-Z_][A-Z0-9_]*", n), n
        assert not n.startswith("COMPOSE_") and not n.endswith("_BIND"), n
        assert not n.startswith(("LD_", "DOCKER_", "BASH")), n
    assert not {"LD_PRELOAD", "BASH_ENV", "ENV", "PATH", "PYTHONPATH", "HOME"} & set(names)


def test_the_name_list_shares_no_name_with_a_pin_or_the_root_key_file() -> None:
    names = set(_names())
    pinned = set(re.findall(r"^\s*pinned_name (\w+) ", _read(BACKUP), re.M)) | {"VERIFY_RESTORE"}
    assert {"APP_DIR", "BACKUP_DIR", "PG_CONTAINER", "BACKUP_OFFBOX_ENV_FILE"} <= pinned
    assert not names & pinned, names & pinned
    lib = _read(ROOT / "scripts/offbox_lib.sh")
    block = lib[lib.index("offbox_root_names=("):]
    root_file = set(block[: block.index(")")].split()[1:])
    assert "BACKUP_S3_SECRET_ACCESS_KEY" in root_file
    assert not names & root_file, names & root_file
    import json

    m = json.loads(_read(MANIFEST))
    offbox = next(e for e in m["entries"] if e["name"] == "backup-offbox")
    assert not names & set(offbox["allowed_keys"]), names & set(offbox["allowed_keys"])


def _backup_allow_list() -> set[str]:
    text = _read(BACKUP).replace("\\\n", " ")
    line = next(ln for ln in text.splitlines() if ln.strip().startswith('offbox_clean_env_reexec "'))
    return set(line.split(" -- ")[0].split()[2:])


def _compose_names() -> dict[str, set[str]]:
    """The names each service of docker-compose.yml interpolates."""
    compose = yaml.safe_load(_read(COMPOSE))
    out = {}
    for name, svc in compose["services"].items():
        out[name] = set(re.findall(r"(?<!\$)\$\{([A-Za-z_][A-Za-z0-9_]*)", yaml.safe_dump(svc)))
    return out


def test_each_name_has_a_reader_and_each_reader_has_its_name() -> None:
    """The backup names are the allow list of backup_db.sh plus the two that
    it reads from the file. The compose names are those of the core and
    meetingbot services. A name the backup needs and the list misses makes
    the night fail (Risks)."""
    names = set(_names())
    allow = _backup_allow_list()
    from_file = set(re.findall(r"grep -E '\^(\w+)=' \"\$ENV_FILE\"", _read(BACKUP)))
    assert from_file == {"POSTGRES_USER", "DATABASE_URL"}, from_file
    by_svc = _compose_names()
    compose = set().union(*(by_svc[s] for s in ("postgres", "redis", "meeting-bot")))
    assert allow <= names, allow - names
    assert from_file <= names
    assert compose <= names, compose - names
    assert names <= allow | from_file | compose, names - (allow | from_file | compose)


def test_each_interpolation_of_the_compose_file_sits_under_environment() -> None:
    """A value of root.env picks what a container sees, never a host path, a
    port, an image or a build context. `$${` is not an interpolation."""
    compose = yaml.safe_load(_read(COMPOSE))
    bad = []

    def walk(node: object, path: tuple[str, ...]) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, (*path, str(k)))
        elif isinstance(node, list):
            for v in node:
                walk(v, path)
        elif (isinstance(node, str) and re.search(r"(?<!\$)\$\{", node)
              and "environment" not in path):
            bad.append("/".join(path))

    walk(compose, ())
    assert bad == [], bad


def test_only_the_list_names_the_root_env_names() -> None:
    """root_env_names.txt is the one list. vps_apply.sh and secrets.sh run the
    builder, and never write root.env themselves. A line with more than six
    names of the list is a second list. (The migration lift in vps_apply.sh
    names five, and it builds no root.env.)"""
    names = set(_names())
    for path in (APPLY, SECRETS, DEPLOY):
        for ln in _code_lines(_read(path)):
            held = names & set(re.findall(r"\b[A-Z_][A-Z0-9_]*\b", ln))
            assert len(held) <= 6, f"{path.name} holds a name list: {ln.strip()}"
            assert not re.search(r"(>|tee|install|cp|mv)\s[^#]*/etc/acb/root\.env", ln), ln
    assert _read(ROOT_ENV_SH).count("root_env_names.txt") >= 1


# ── No root path reads the acb-writable env ──────────────────────────────


def test_no_tracked_shell_script_sources_an_env_file() -> None:
    pat = re.compile(r"""(?:^|[;&|(]\s*|\s)(?:source|\.)\s+["']?[^\s"';|&]*\.env["']?(?:\s|;|&|\||$)""")
    skip = {".git", "node_modules", ".venv", ".next", "__pycache__"}
    bad = []
    for root, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in skip]
        for f in files:
            if f.endswith(".sh"):
                p = Path(root) / f
                for ln in _code_lines(p.read_text(encoding="utf-8", errors="replace")):
                    if pat.search(ln):
                        bad.append(f"{p.relative_to(ROOT).as_posix()}: {ln.strip()}")
    assert bad == [], bad


# ── Every compose call names the root copy and root.env ──────────────────


def test_each_compose_call_names_the_root_copy_and_root_env() -> None:
    """Mutation: give one compose call the old `-f infra/docker-compose.yml`,
    and this goes red. The only exempt call is `--profile sandbox build`."""
    seen = 0
    texts = {APPLY: _read(APPLY), DEPLOY: _read(DEPLOY)}
    for u in _root_units():
        texts[u] = _read(u)
    for path, text in texts.items():
        for ln in _code_lines(text):
            if "docker compose" not in ln and "docker-compose " not in ln:
                continue
            if path.suffix == ".service" and not ln.startswith("Exec"):
                continue
            if "--profile sandbox build" in ln:
                continue
            seen += 1
            for want in (PROJECT_DIR, ENV_FILE_ARG, COMPOSE_FILE_ARG, " -p acb "):
                assert want in ln, f"{path.name}: {want!r} missing in: {ln.strip()}"
            assert "docker-compose " not in ln
    assert seen >= 5, seen


def test_the_deploy_runs_compose_through_the_helper_only() -> None:
    for path in (APPLY, DEPLOY):
        text = _read(path)
        lines = _code_lines(text)
        calls = [ln for ln in lines if re.match(r"\s*(if\s+)?acb_compose\s", ln)]
        assert calls, path.name
        # The one line that runs docker compose is the body of the helper.
        runs = [ln.strip() for ln in lines if "docker compose" in ln and "echo " not in ln]
        helper = " ".join(_code_lines(_function(text, "acb_compose")))
        assert len(runs) == 1 and runs[0] in helper, runs
    apply = _code_lines(_read(APPLY))
    joined = "\n".join(" ".join(ln.split()) for ln in apply)
    for call in ("acb_compose --profile core up -d --remove-orphans",
                 "acb_compose --timeout 900 --profile meetingbot up -d --build meeting-bot",
                 "acb_compose --profile meetingbot rm -sf meeting-bot",
                 "acb_compose --profile core --profile meetingbot config -q"):
        assert call in joined, call


# ── The sync (static) ─────────────────────────────────────────────────────


def _bh6_block() -> str:
    text = _read(APPLY)
    return text[text.index("# >>> bh6 helpers"): text.index("# <<< bh6 helpers")]


def _function(text: str, name: str) -> str:
    m = re.search(rf"^{name}\(\) \{{\n.*?^\}}\n", text, re.S | re.M)
    assert m, name
    return m.group(0)


def test_the_sync_reads_git_archive_of_the_target_sha_and_refuses_a_symlink() -> None:
    sync = _function(_bh6_block(), "bh6_sync_root_copy")
    assert 'GIT_NO_REPLACE_OBJECTS=1 git archive --format=tar -o "$tarf" "$sha" -- "${srcs[@]}"' in sync
    assert 'GIT_NO_REPLACE_OBJECTS=1 git show "$sha:$BH6_LIST"' in sync
    assert 'links="$(sudo find "$stage" -type l)"' in sync
    assert 'sudo mktemp -d /usr/local/lib/acb-stage.XXXXXX' in sync
    assert ('sudo rsync -a --delete --chown=root:root --chmod=go-w "$stage/copy/" /usr/local/lib/acb/'
            in sync)
    assert 'sudo chmod 0755 "$stage/copy"' in sync
    # It never reads the working tree.
    assert "$APP_DIR" not in sync
    assert not re.search(r"\bcp\b", sync)
    steps = _function(_bh6_block(), "bh6_root_steps")
    assert 'bh6_sync_root_copy "${DEPLOY_TARGET_SHA:-}"' in steps


def test_the_sync_runs_only_when_the_rollback_status_is_1() -> None:
    steps = _function(_bh6_block(), "bh6_root_steps")
    assert 'bash "$APP_DIR/scripts/bh2_rollback.sh" status >/dev/null 2>&1 || rb_rc=$?' in steps
    assert 'if [ "$rb_rc" = "1" ]; then' in steps
    assert "WARN BH-6: the BH-2 rollback is not off, so the root copy stays at" in steps


def test_the_steps_sit_in_order() -> None:
    """Steps 1 to 3 after the pull block and before the first compose call.
    Step 2 again before the meeting-bot compose call. Step 4 after
    ensure_gateway_rw_paths and before the gateway restart."""
    lines = _code_lines(_read(APPLY))

    def first(pred, start: int = 0) -> int:
        return next(i for i in range(start, len(lines)) if pred(lines[i].strip()))

    reexec = first(lambda s: s.startswith("exec env VPS_APPLY_REEXECED=1"))
    helper = first(lambda s: s == "acb_compose() {")
    steps = first(lambda s: s == "bh6_root_steps || {")
    core_up = first(lambda s: s.startswith("acb_compose --profile core up"))
    token = first(lambda s: s.startswith('echo "MEETING_BOT_TOKEN=$_mbtoken"'))
    again = first(lambda s: s == "sudo bash /usr/local/lib/acb/root_env.sh || {", steps + 1)
    bot_up = first(lambda s: s.startswith("if acb_compose --timeout 900 --profile meetingbot"))
    ensure = first(lambda s: s.startswith('ensure_gateway_rw_paths "$APP_DIR"'))
    install = first(lambda s: s.startswith('sudo install -m 0644 "$APP_DIR/deploy/hostinger/acb.service"'))
    joint = first(lambda s: s == "sudo systemctl restart acb.service acb-gateway")
    restart = first(lambda s: s == "sudo systemctl restart acb-gateway")
    check = first(lambda s: s == "if ! bh2_strict_check; then")
    assert reexec < helper < steps < core_up
    assert token < again < bot_up
    assert ensure < install < joint < restart < check
    # No compose call runs between the pull block and step 3.
    assert not [ln for ln in lines[reexec:helper] if "acb_compose " in ln or "docker compose" in ln]


def test_the_watchdog_step_drops_the_chmod_of_the_checkout_script() -> None:
    assert "health-watchdog.sh" not in "\n".join(
        ln for ln in _code_lines(_read(APPLY)) if "chmod" in ln
    )


def test_deploy_sh_stops_with_no_root_copy() -> None:
    text = _read(DEPLOY)
    code = _code_lines(text)
    guard = next(i for i, ln in enumerate(code) if "sudo test -f /usr/local/lib/acb/deployed_sha" in ln)
    first_call = next(i for i, ln in enumerate(code) if re.match(r"\s*acb_compose\s", ln))
    assert guard < first_call
    assert "The next run of scripts/vps_apply.sh makes it" in text


# ── The shell tests: the sync ─────────────────────────────────────────────

STUB_SUDO = r"""#!/usr/bin/env bash
printf '%s\n' "$*" >> "$STUB_LOG.sudo"
case "$1" in
  env) printf '%s\n' "$*" >> "$STUB_LOG.compose"; exit 0 ;;
esac
if [ "$1" = bash ] && [ "${2:-}" = /usr/local/lib/acb/root_env.sh ]; then
  echo rootenv >> "$STUB_LOG.rootenv"; exit "${STUB_ROOTENV_RC:-0}"
fi
args=()
for a in "$@"; do
  case "$a" in
    --chown=*) continue ;;
    /usr/local/lib/*|/etc/acb/*) args+=("$FAKE_ROOT$a") ;;
    *) args+=("$a") ;;
  esac
done
if [ "$1" = mktemp ]; then
  out="$("${args[@]}")" || exit $?
  printf '%s\n' "${out#"$FAKE_ROOT"}"
  exit 0
fi
exec "${args[@]}"
"""


class Sync:
    """A git repo with the paths of the list, a fake root, and the stub sudo."""

    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp
        self.repo = tmp / "repo"
        self.fake = tmp / "fake"
        (self.fake / "usr/local/lib").mkdir(parents=True)
        (self.fake / "etc/acb").mkdir(parents=True)
        binr = tmp / "bin"
        binr.mkdir()
        (binr / "sudo").write_text(STUB_SUDO, encoding="utf-8", newline="\n")
        (binr / "sudo").chmod(0o755)
        self.log = tmp / "log"
        self.env = dict(
            os.environ,
            PATH=f"{binr}{os.pathsep}{os.environ.get('PATH', '')}",
            FAKE_ROOT=self.fake.as_posix(),
            STUB_LOG=self.log.as_posix(),
            TMPDIR=tmp.as_posix(),
            GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com",
            GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com",
            GIT_CONFIG_NOSYSTEM="1", HOME=tmp.as_posix(),
        )
        self.repo.mkdir()
        self.git("init", "-q")
        (self.repo / "deploy/hostinger").mkdir(parents=True)
        shutil.copy(LIST, self.repo / "deploy/hostinger/root_lib_files.txt")
        for src, _ in _copy_list():
            p = self.repo / src
            if src == "apps/services/meeting_bot":
                (p / "app").mkdir(parents=True)
                (p / "app" / "main.py").write_text("print('bot')\n", encoding="utf-8")
                (p / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
            elif not p.exists():
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(f"# {src} at commit\n", encoding="utf-8")
        self.sha = self.commit("one")
        self.helpers = tmp / "bh6_helpers.sh"
        self.helpers.write_text(_bh6_block(), encoding="utf-8", newline="\n")

    def git(self, *args: str) -> str:
        return subprocess.run(["git", "-C", self.repo.as_posix(), *args], env=self.env, check=True,
                              capture_output=True, text=True).stdout.strip()

    def commit(self, msg: str) -> str:
        self.git("add", "-A")
        self.git("commit", "-qm", msg)
        return self.git("rev-parse", "HEAD")

    def bash(self, script: str, **extra: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["bash", "-c", f'cd "{self.repo.as_posix()}" && source "{self.helpers.as_posix()}" && {script}'],
            env=dict(self.env, **extra), capture_output=True, text=True, timeout=120,
            stdin=subprocess.DEVNULL,
        )

    def copy(self) -> Path:
        return self.fake / "usr/local/lib/acb"

    def tree(self) -> dict[str, bytes]:
        c = self.copy()
        return {p.relative_to(c).as_posix(): p.read_bytes() for p in sorted(c.rglob("*")) if p.is_file()}


@pytest.fixture
def sync(tmp_path: Path) -> Sync:
    return Sync(tmp_path)


@needs_linux
def test_the_sync_writes_the_bytes_of_the_commit_not_the_working_tree(sync: Sync) -> None:
    """Mutation: copy from the working tree, and the dirty bytes reach the
    copy, so this goes red."""
    (sync.repo / "scripts/backup_db.sh").write_text("# DIRTY working tree\n", encoding="utf-8")
    r = sync.bash(f'bh6_sync_root_copy {sync.sha} && echo "CORE=$BH6_CORE_CHANGED"')
    assert r.returncode == 0, r.stdout + r.stderr
    tree = sync.tree()
    assert tree["backup_db.sh"] == b"# scripts/backup_db.sh at commit\n"
    assert tree["deployed_sha"] == f"{sync.sha}\n".encode()
    for flat in ("backup_offbox.sh", "offbox_lib.sh", "health-watchdog.sh", "bh2_rollback.sh",
                 "root_env.sh", "root_env_names.txt", "infra/docker-compose.yml",
                 "infra/postgres/01_schema.sql", "apps/services/meeting_bot/app/main.py"):
        assert flat in tree, flat
    assert not [p for p in tree if p.startswith(("scripts/", "deploy/"))], tree.keys()
    assert "CORE=1" in r.stdout
    # The modes: no group or other write bit, and the dir is 0755 (N1).
    assert (sync.copy().stat().st_mode & 0o777) == 0o755
    for p in sync.copy().rglob("*"):
        assert not p.stat().st_mode & 0o022, p
    # The stage is gone.
    assert not list((sync.fake / "usr/local/lib").glob("acb-stage.*"))


@needs_linux
def test_a_second_sync_of_the_same_core_inputs_changes_nothing_core(sync: Sync) -> None:
    assert sync.bash(f"bh6_sync_root_copy {sync.sha}").returncode == 0
    (sync.repo / "apps/services/meeting_bot/app/main.py").write_text("print(2)\n", encoding="utf-8")
    (sync.repo / "scripts/stale.sh").write_text("x\n", encoding="utf-8")
    sha2 = sync.commit("two")
    (sync.copy() / "left-over").write_text("x", encoding="utf-8")
    r = sync.bash(f'bh6_sync_root_copy {sha2} && echo "CORE=$BH6_CORE_CHANGED"')
    assert r.returncode == 0, r.stdout + r.stderr
    assert "CORE=0" in r.stdout, "a meeting-bot change alone must not restart acb.service"
    tree = sync.tree()
    assert tree["apps/services/meeting_bot/app/main.py"] == b"print(2)\n"
    assert "left-over" not in tree, "rsync --delete must remove a file the list does not hold"


@needs_linux
def test_the_sync_refuses_a_symlink_and_keeps_the_copy(sync: Sync) -> None:
    """Mutation: drop the `find -type l` check, and the link reaches the copy."""
    assert sync.bash(f"bh6_sync_root_copy {sync.sha}").returncode == 0
    before = sync.tree()
    os.symlink("/etc/shadow", sync.repo / "apps/services/meeting_bot/app/evil.py")
    sha2 = sync.commit("link")
    r = sync.bash(f"bh6_sync_root_copy {sha2}")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "holds a symlink" in r.stdout
    assert sync.tree() == before
    assert not [p for p in sync.copy().rglob("*") if p.is_symlink()]
    assert not list((sync.fake / "usr/local/lib").glob("acb-stage.*"))


@needs_linux
def test_the_sync_stops_when_a_listed_path_is_missing(sync: Sync) -> None:
    (sync.repo / "infra/postgres/01_schema.sql").unlink()
    sha2 = sync.commit("gone")
    r = sync.bash(f"bh6_sync_root_copy {sha2}")
    assert r.returncode == 1, r.stdout + r.stderr
    assert not sync.copy().exists()


@needs_linux
def test_the_sync_refuses_a_target_that_is_not_a_sha(sync: Sync) -> None:
    r = sync.bash("bh6_sync_root_copy HEAD")
    assert r.returncode == 1
    assert not sync.copy().exists()


# ── The shell tests: the gate on the BH-2 rollback ───────────────────────


def _gate(sync: Sync, rc: int, *, copy: bool) -> subprocess.CompletedProcess:
    app = sync.tmp / "app"
    (app / "scripts").mkdir(parents=True, exist_ok=True)
    (app / "scripts/bh2_rollback.sh").write_text(
        f'[ "$1" = status ] || exit 2\necho state\nexit {rc}\n', encoding="utf-8", newline="\n")
    if copy:
        sync.copy().mkdir(parents=True, exist_ok=True)
        (sync.copy() / "deployed_sha").write_text("a" * 40 + "\n", encoding="utf-8")
    return sync.bash(
        'bh6_sync_root_copy() { echo "SYNCED $1"; }; bh6_ensure_rsync() { :; }; '
        "bh6_root_steps; echo RC=$?",
        APP_DIR=app.as_posix(), DEPLOY_TARGET_SHA=sync.sha,
    )


@needs_linux
@pytest.mark.parametrize("rc", [0, 3, 2])
def test_the_sync_skips_while_the_rollback_is_not_off(sync: Sync, rc: int) -> None:
    """Exit 0 is a valid rollback and exit 3 an expired or partial one. Each
    exit but 1 skips the sync, and the root units run the last copy.
    Mutation: sync on any exit, and SYNCED shows here."""
    r = _gate(sync, rc, copy=True)
    assert "SYNCED" not in r.stdout, r.stdout
    assert f"WARN BH-6: the BH-2 rollback is not off, so the root copy stays at {'a' * 40}" in r.stdout
    assert "RC=0" in r.stdout, r.stdout + r.stderr
    assert Path(f"{sync.log}.rootenv").exists(), "step 2 still runs"


@needs_linux
def test_the_sync_runs_when_the_rollback_is_off(sync: Sync) -> None:
    r = _gate(sync, 1, copy=False)
    assert f"SYNCED {sync.sha}" in r.stdout, r.stdout + r.stderr
    assert "RC=0" in r.stdout
    compose = Path(f"{sync.log}.compose").read_text(encoding="utf-8")
    assert "--profile core --profile meetingbot config -q" in compose
    assert PROJECT_DIR in compose and ENV_FILE_ARG in compose


@needs_linux
def test_a_skip_with_no_copy_yet_stops_the_deploy(sync: Sync) -> None:
    r = _gate(sync, 0, copy=False)
    assert "RC=1" in r.stdout, r.stdout + r.stderr
    assert "has no root copy yet" in r.stdout
    assert not Path(f"{sync.log}.compose").exists(), "a compose call ran"


@needs_linux
def test_a_failed_root_env_stops_the_steps_before_compose(sync: Sync) -> None:
    app = sync.tmp / "app"
    (app / "scripts").mkdir(parents=True, exist_ok=True)
    (app / "scripts/bh2_rollback.sh").write_text("exit 1\n", encoding="utf-8", newline="\n")
    r = sync.bash(
        'bh6_sync_root_copy() { :; }; bh6_ensure_rsync() { :; }; bh6_root_steps; echo RC=$?',
        APP_DIR=app.as_posix(), DEPLOY_TARGET_SHA=sync.sha, STUB_ROOTENV_RC="1",
    )
    assert "RC=1" in r.stdout, r.stdout + r.stderr
    assert not Path(f"{sync.log}.compose").exists()


# ── The shell tests: root_env.sh ─────────────────────────────────────────

_INSTALL_STUB = r"""
install() {
  printf '%s\n' "$*" >> "$STUB_LOG.install"
  local -a a=()
  while [ "$#" -gt 0 ]; do
    case "$1" in -o|-g) shift 2 ;; *) a+=("$1"); shift ;; esac
  done
  command install "${a[@]}"
}
export -f install
"""


class Layout:
    def __init__(self, tmp: Path) -> None:
        self.r = tmp / "r"
        self.lib = self.r / "usr/local/lib/acb"
        self.lib.mkdir(parents=True)
        (self.r / "opt/acb/app/apps/services/customer_console").mkdir(parents=True)
        (self.r / "etc/acb").mkdir(parents=True)
        shutil.copy(ROOT_ENV_SH, self.lib / "root_env.sh")
        shutil.copy(NAMES, self.lib / "root_env_names.txt")
        self.app = self.r / "opt/acb/app/.env"
        self.console = self.r / "opt/acb/app/apps/services/customer_console/.env"
        self.out = self.r / "etc/acb/root.env"
        self.log = tmp / "log"

    def run(self, script: Path | None = None) -> subprocess.CompletedProcess:
        prog = _INSTALL_STUB + f'bash "{(script or self.lib / "root_env.sh").as_posix()}"\n'
        return subprocess.run(["bash", "-c", prog], env=dict(os.environ, STUB_LOG=self.log.as_posix()),
                              capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)

    def installs(self) -> list[str]:
        p = Path(f"{self.log}.install")
        return p.read_text(encoding="utf-8").splitlines() if p.exists() else []


@pytest.fixture
def layout(tmp_path: Path) -> Layout:
    return Layout(tmp_path)


_HOSTILE = (
    "LD_PRELOAD=/tmp/x.so\nPOSTGRES_BIND=0.0.0.0\nCOMPOSE_PROJECT_NAME=x\n"
    "BACKUP_DIR=/\nPG_CONTAINER=x\nBASH_ENV=/tmp/x\nBACKUP_S3_SECRET_ACCESS_KEY=x\n"
)


@needs_linux
def test_root_env_keeps_only_the_listed_names_byte_for_byte(layout: Layout) -> None:
    """Acceptance 4. The Console wins for a name in both files, the last line
    wins inside one file, and a hostile name never reaches root.env."""
    layout.app.write_bytes(
        b"PG_MODE=docker\n" + _HOSTILE.encode()
        + b"PGPASSWORD=a b#c $x 'q'\nPG_MODE=local\nDATABASE_URL=postgresql://u:p@h:5432/acb\n"
        + b"CUSTOMER_CONSOLE_DATABASE_URL=postgresql://app-value\nexport PGHOST=ignored\n"
    )
    layout.console.write_bytes(b"CUSTOMER_CONSOLE_DATABASE_URL=postgresql://cc:pw@cc:5432/postgres\n")
    r = layout.run()
    assert r.returncode == 0, r.stdout + r.stderr
    body = layout.out.read_bytes().decode()
    lines = [ln for ln in body.splitlines() if not ln.startswith("#")]
    assert lines == [
        "PG_MODE=local",
        "PGPASSWORD=a b#c $x 'q'",
        "DATABASE_URL=postgresql://u:p@h:5432/acb",
        "CUSTOMER_CONSOLE_DATABASE_URL=postgresql://cc:pw@cc:5432/postgres",
    ], body
    for bad in ("LD_PRELOAD", "POSTGRES_BIND", "COMPOSE_PROJECT_NAME", "BACKUP_DIR", "PG_CONTAINER",
                "BASH_ENV", "BACKUP_S3", "PGHOST"):
        assert bad not in body, bad
    assert "pw@cc" not in r.stdout + r.stderr, "a value reached the output"
    assert "holds 4 of" in r.stdout


@needs_linux
def test_root_env_is_written_root_0600_and_only_when_it_changed(layout: Layout) -> None:
    """Acceptance 3, the source half. Mutation: install it 0644, and this goes
    red."""
    layout.app.write_text("POSTGRES_USER=acb\n", encoding="utf-8")
    assert layout.run().returncode == 0
    (call,) = layout.installs()
    assert call.startswith("-m 0600 -o root -g root "), call
    assert call.endswith(f" {layout.out.as_posix()}"), call
    assert (layout.out.stat().st_mode & 0o777) == 0o600
    assert not list((layout.r / "etc/acb").glob(".root.env.*")), "the temp file stayed"
    # The test is not root, so the owner check always asks for a new write.
    # A changed source writes again, with the new line.
    layout.app.write_text("POSTGRES_USER=other\n", encoding="utf-8")
    assert layout.run().returncode == 0
    assert "POSTGRES_USER=other" in layout.out.read_text(encoding="utf-8")


@needs_linux
def test_root_env_refuses_a_symlink_source(layout: Layout, tmp_path: Path) -> None:
    real = tmp_path / "elsewhere.env"
    real.write_text("PGPASSWORD=from-a-link\n", encoding="utf-8")
    layout.app.symlink_to(real)
    r = layout.run()
    assert r.returncode != 0
    assert "symlink" in r.stderr
    assert not layout.out.exists()


@needs_linux
def test_root_env_needs_no_console_file(layout: Layout) -> None:
    """B6-1: a box without the Console still gets root.env."""
    layout.app.write_text("PG_MODE=local\n", encoding="utf-8")
    r = layout.run()
    assert r.returncode == 0, r.stderr
    assert "PG_MODE=local" in layout.out.read_text(encoding="utf-8")


@needs_linux
def test_root_env_refuses_a_run_outside_the_root_copy(layout: Layout, tmp_path: Path) -> None:
    other = tmp_path / "opt/acb/app/scripts"
    other.mkdir(parents=True)
    shutil.copy(ROOT_ENV_SH, other / "root_env.sh")
    r = layout.run(other / "root_env.sh")
    assert r.returncode == 2
    assert "runs from /usr/local/lib/acb only" in r.stderr


@needs_linux
def test_root_env_refuses_a_name_list_with_a_pattern(layout: Layout) -> None:
    layout.app.write_text("PG_MODE=local\n", encoding="utf-8")
    (layout.lib / "root_env_names.txt").write_text("PG*\n", encoding="utf-8")
    r = layout.run()
    assert r.returncode == 2
    assert not layout.out.exists()


# ── The shell tests: backup_db.sh in the root copy ───────────────────────


@needs_linux
def test_a_root_run_reads_root_env_not_the_app_env() -> None:
    """The root run reads ENV_FILE from /etc/acb/root.env. A user in the app
    .env never reaches the dump."""
    setup = "printf 'POSTGRES_USER=evil_app\\n' > \"$R/opt/acb/app/.env\"\n"
    r = _run(setup=setup, root=True)
    assert r["rc"] == 0, f"{r['out']}\n{r['err']}"
    users = [ln for ln in _lines(r, "psql ") if " -U " in ln]
    assert users and all(" -U acb " in ln for ln in users), users


@needs_linux
@pytest.mark.parametrize(("content", "want"), [
    ("0123456789abcdef0123456789abcdef01234567\n", "app_commit:       0123456789ab"),
    ("not-a-sha\n", "app_commit:       unknown"),
    (None, "app_commit:       unknown"),
])
def test_a_root_run_takes_app_commit_from_deployed_sha(content: str | None, want: str) -> None:
    """Acceptance 5, the source half. No git runs as root in the checkout."""
    setup = ""
    if content is not None:
        setup = f"printf '{content.strip()}\\n' > \"$R/usr/local/lib/acb/deployed_sha\"\n"
    r = _run(setup=setup + "git() { echo \"git $*\" >> \"$CALLS\"; command git \"$@\"; }\nexport -f git\n",
             root=True)
    assert r["rc"] == 0, f"{r['out']}\n{r['err']}"
    assert want in str(r["out"]), r["out"]
    assert not _lines(r, "git "), "a root run ran git"


@needs_linux
def test_a_root_run_from_the_checkout_path_is_refused() -> None:
    setup = 'mkdir -p "$R/opt/acb/app/scripts"; cp scripts/*.sh "$R/opt/acb/app/scripts/"\n'
    cmd = _ROOT_RUN.replace("$W/root/usr/local/lib/acb/", "$W/root/opt/acb/app/scripts/")
    r = _run(setup=setup, root=True, command=cmd)
    assert r["rc"] == 2, f"{r['out']}\n{r['err']}"
    assert "a root run must start from /usr/local/lib/acb/backup_db.sh" in str(r["err"])
    assert not _lines(r, "pg_dump")


@needs_linux
def test_an_acb_run_reads_the_app_env_and_needs_no_root_env() -> None:
    """B4, the second caller. The pre-migration backup runs the checkout copy
    as acb. It reads $APP_DIR/.env, and acb cannot read root.env."""
    setup = "mkdir -p \"$W/app\"; printf 'POSTGRES_USER=from_app\\n' > \"$W/app/.env\"\n"
    r = _run(setup=setup)
    assert r["rc"] == 0, f"{r['out']}\n{r['err']}"
    users = [ln for ln in _lines(r, "psql ") if " -U " in ln]
    assert users and all(" -U from_app " in ln for ln in users), users
    assert "root.env" not in str(r["err"])


# ── bh2_rollback.sh: the flat copy serves `status` ───────────────────────


@needs_linux
@pytest.mark.parametrize("state", ["off", "on", "expired", "partial-conf", "partial-ack"])
def test_the_flat_copy_gives_the_same_status_exit_code(tmp_path: Path, state: str) -> None:
    box = Box(tmp_path)
    if state in ("on", "expired", "partial-conf"):
        d = box.path(DROPIN)
        d.parent.mkdir(parents=True, exist_ok=True)
        d.write_text("[Service]\n", encoding="utf-8")
    if state in ("on", "expired", "partial-ack"):
        a = box.path(ACK)
        a.parent.mkdir(parents=True, exist_ok=True)
        exp = NOW + 3600 if state != "expired" else NOW - 1
        a.write_text(f"EXPIRES_EPOCH={exp}\n", encoding="utf-8", newline="\n")
    flat = tmp_path / "usr/local/lib/acb"
    flat.mkdir(parents=True)
    shutil.copy(ROOT / "scripts/bh2_rollback.sh", flat / "bh2_rollback.sh")
    rcs = []
    for script in (ROOT / "scripts/bh2_rollback.sh", flat / "bh2_rollback.sh"):
        r = subprocess.run(["bash", script.as_posix(), "status"], env=box.env, capture_output=True,
                           text=True, timeout=60, stdin=subprocess.DEVNULL)
        rcs.append(r.returncode)
    assert rcs[0] == rcs[1], rcs
    assert rcs[0] == {"off": 1, "on": 0}.get(state, 3), (state, rcs)
