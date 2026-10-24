"""HANDOFF H-358 (WS-61) — the root script that sets a flag on the staging box.

``deploy/hostinger/staging/acb-staging-flag`` is the one door through which the
``dev`` account changes ``/opt/acb/app/.env`` on the staging box, through one
sudoers line. Each rule in its header has a test here.

Every test drives the script on a temp env file through
``ACB_STAGING_FLAG_ENV_FILE``, and passes ``--no-restart``. Under that override
the script restarts nothing and writes no syslog line. The fixed path
``/opt/acb/app/.env`` is never read here.

Fix round 1 of the security review added: root does no file work (P1), the C
locale (P2-a), exact key names (P2-b), no empty value, no ``NEXT_PUBLIC_*``
key, no odd form of a requested key, the pin file that the nightly refresh
reads, and a cap of 20 backups (P3).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy" / "hostinger" / "staging" / "acb-staging-flag"
README = ROOT / "deploy" / "hostinger" / "staging" / "README.md"
BASH = shutil.which("bash")

pytestmark = [
    pytest.mark.skipif(BASH is None, reason="bash is not installed"),
    pytest.mark.skipif(os.name != "posix", reason="the script runs on the Linux box only"),
    pytest.mark.skipif(os.geteuid() == 0 if hasattr(os, "geteuid") else False,
                       reason="as root the script ignores the override and uses /opt/acb/app/.env"),
]

SECRET = "sk-live-do-not-print-0123456789"

#: The exact names the script may write (fix round 1, P2-b). A change here is
#: a reviewed change of what an agent may flip on staging.
ALLOWED = [
    "KNOWLEDGE_ENABLED", "KNOWLEDGE_ORGS", "KNOWLEDGE_EMBED", "KNOWLEDGE_EMBED_SWEEP",
    "KNOWLEDGE_PROPOSAL_EXPIRY",
    "COMPANY_SEARCH_ENABLED", "CONTEXT_RECENCY", "MY_DAY_PROVIDERS", "ARTIFACT_REGISTRY_ENABLED",
    "CRM_ENABLED", "CRM_ORGS",
]


def _env_file(tmp_path: Path, body: str) -> Path:
    f = tmp_path / ".env"
    f.write_text(body, encoding="utf-8")
    f.chmod(0o600)
    return f


def _run(env_file: Path, *args: str, extra: dict[str, str] | None = None
         ) -> subprocess.CompletedProcess[str]:
    env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": str(env_file.parent),
        "ACB_STAGING_FLAG_ENV_FILE": str(env_file),
        **(extra or {}),
    }
    return subprocess.run(
        [BASH or "bash", str(SCRIPT), *args],
        capture_output=True, text=True, timeout=30, env=env,
    )


def _lines(f: Path) -> list[str]:
    return f.read_text(encoding="utf-8").splitlines()


def _backups(f: Path) -> list[Path]:
    return sorted(f.parent.glob(f.name + ".bak-*"))


def _pins(f: Path) -> Path:
    return f.parent / ".staging-flags"


def _body() -> str:
    return SCRIPT.read_text(encoding="utf-8")


STAGING = "DEPLOY_ENV=staging\n"


# ── The script itself ────────────────────────────────────────────────────────


def test_the_script_is_executable_bash() -> None:
    body = _body()
    assert body.startswith("#!/bin/bash\n")
    assert "set -euo pipefail" in body
    assert os.access(SCRIPT, os.X_OK), "git must track the script as mode 755"
    assert subprocess.run([BASH or "bash", "-n", str(SCRIPT)]).returncode == 0


# ── P1: root does no file work ───────────────────────────────────────────────


def _fs_bodies_and_rest(body: str) -> tuple[list[str], list[str]]:
    """Split the code into the fs_* function bodies and the rest. Comment
    lines are dropped."""
    fs: list[str] = []
    rest: list[str] = []
    inside = False
    for ln in body.splitlines():
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        if re.match(r"^fs_\w+\(\) \{$", ln):
            inside = True
            continue
        if inside and ln == "}":
            inside = False
            continue
        (fs if inside else rest).append(s)
    return fs, rest


def _unquote(s: str) -> str:
    """Drop the text of each quoted string, so a message cannot look like code."""
    return re.sub(r"'[^']*'", "''", re.sub(r'"(?:[^"\\]|\\.)*"', '""', s))


def test_the_owner_helper_drops_root_with_setpriv() -> None:
    body = _body()
    assert 'setpriv --reuid="$OWNER" --regid="$OWNER" --clear-groups -- /bin/bash -c' in body
    assert "readonly OWNER=acb" in body
    assert "chown" not in body, "a file written as acb is acb's, so no chown"


def test_no_file_operation_runs_as_root() -> None:
    """Every read or write of ENV_FILE, PIN_FILE, a temp file or a backup sits in
    an fs_* function, and those run only through as_owner."""
    fs, rest = _fs_bodies_and_rest(_body())
    assert fs, "no fs_* functions found"
    file_cmd = re.compile(
        r"(^|[\s|;&(])(cat|cp|cmp|chmod|chown|mktemp|rm|mv|find|touch|ln|truncate|tee|dd|"
        r"install|awk|grep|head|tail)\b(?!_)")
    test_op = re.compile(r"\[\[? -[LefrwsdOhx] ")
    for ln in rest:
        # Arithmetic `(( i < n ))` is not a redirection.
        code = re.sub(r"\(\(.*?\)\)", "", _unquote(ln))
        assert not file_cmd.search(code), f"a file command outside fs_*: {ln}"
        assert not test_op.search(code), f"a file test outside fs_*: {ln}"
        # The only redirections root keeps: to stderr, to /dev/null, and the
        # read end of a process substitution.
        for m in re.finditer(r"\d*(>>?|<)(?!\()", code):
            after = code[m.end():].lstrip()
            ok = after.startswith("&2") or after.startswith("/dev/null") or after.startswith("<(")
            assert ok, f"a redirection outside fs_*: {ln}"
        if "sed" in code:
            assert not re.search(r"\bsed\s+-[a-zA-Z]*i", code), ln
    # Each fs_* function is called only through as_owner.
    for ln in rest:
        for m in re.finditer(r"\bfs_\w+\b", ln):
            assert ("as_owner " + m.group(0)) in ln or "declare -f" in ln, \
                f"{m.group(0)} called without as_owner: {ln}"


# ── Rule 1: staging only ─────────────────────────────────────────────────────


@pytest.mark.parametrize("body", [
    "",                                        # no DEPLOY_ENV line
    "DEPLOY_ENV=production\n",
    "DEPLOY_ENV=staging\nDEPLOY_ENV=production\n",   # the last line wins
    'DEPLOY_ENV="staging"\n',                  # quoted
    "DEPLOY_ENV=staging \n",                   # a trailing space
    "DEPLOY_ENV=Staging\n",
    "export DEPLOY_ENV=staging\n",             # odd forms, as in vps_apply.sh
    "DEPLOY_ENV = staging\n",
    " DEPLOY_ENV=staging\n",
    "DEPLOY_ENV=staging\nexport DEPLOY_ENV=staging\n",
])
def test_it_refuses_unless_the_file_says_exactly_staging(tmp_path: Path, body: str) -> None:
    f = _env_file(tmp_path, body + "KNOWLEDGE_ENABLED=false\n")
    before = f.read_bytes()
    for args in (("--list",), ("--no-restart", "set", "KNOWLEDGE_ENABLED", "true")):
        res = _run(f, *args)
        assert res.returncode == 2, (body, args, res.stderr)
        assert "refused" in res.stderr
        assert res.stdout == ""
    assert f.read_bytes() == before
    assert _backups(f) == []
    assert not _pins(f).exists()


def test_it_refuses_a_missing_file(tmp_path: Path) -> None:
    res = _run(tmp_path / "absent.env", "--list")
    assert res.returncode == 2 and "does not exist" in res.stderr


def test_it_refuses_an_unreadable_file(tmp_path: Path) -> None:
    f = _env_file(tmp_path, STAGING)
    f.chmod(0o000)
    try:
        res = _run(f, "--list")
    finally:
        f.chmod(0o600)
    assert res.returncode == 2 and "cannot be read" in res.stderr


def test_it_refuses_a_symlink(tmp_path: Path) -> None:
    real = tmp_path / "real.env"
    real.write_text(STAGING, encoding="utf-8")
    link = tmp_path / ".env"
    link.symlink_to(real)
    res = _run(link, "--list")
    assert res.returncode == 2 and "symlink" in res.stderr


def test_a_crlf_staging_line_counts(tmp_path: Path) -> None:
    f = _env_file(tmp_path, "DEPLOY_ENV=staging\r\nKNOWLEDGE_ENABLED=true\r\n")
    res = _run(f, "--list")
    assert res.returncode == 0, res.stderr
    assert res.stdout == "KNOWLEDGE_ENABLED=true\n"


# ── Rule 2: the file path ────────────────────────────────────────────────────


def test_root_ignores_the_override() -> None:
    """A real euid 0 would read /opt/acb/app/.env, which the tests must never
    touch. So this reads the code path: the override is taken only when the
    euid is not 0, and the fixed path is the default."""
    body = _body()
    assert "readonly FIXED_ENV_FILE=/opt/acb/app/.env" in body
    assert 'ENV_FILE="$FIXED_ENV_FILE"' in body
    guard = re.search(
        r'if \[ "\$EUID" -ne 0 \] && \[ -n "\$\{ACB_STAGING_FLAG_ENV_FILE:-\}" \]; then\n'
        r'  ENV_FILE="\$ACB_STAGING_FLAG_ENV_FILE"\n'
        r'  TEST_MODE=1\n'
        r'fi\n', body)
    assert guard, "the override must sit behind the EUID check"
    # After the guard, the name is only unset, never read.
    after = body[guard.end():]
    assert [m.group(0) for m in re.finditer(r".*ACB_STAGING_FLAG_ENV_FILE.*", after)] == [
        "unset ACB_STAGING_FLAG_ENV_FILE"]
    # Nothing assigns ENV_FILE after the guard.
    assert not re.search(r"^\s*ENV_FILE=", after, re.M)


def test_the_override_restarts_nothing() -> None:
    body = _body()
    assert 'if [ "$RESTART" = 0 ] || [ "$TEST_MODE" = 1 ]; then' in body
    restart = body.index("systemctl restart")
    assert body.index('if [ "$RESTART" = 0 ] || [ "$TEST_MODE" = 1 ]; then') < restart


# ── set, --list, unset ───────────────────────────────────────────────────────


def test_set_appends_replaces_and_collapses_duplicates(tmp_path: Path) -> None:
    f = _env_file(tmp_path, STAGING
                  + "OPENAI_API_KEY=" + SECRET + "\n"
                  + "KNOWLEDGE_ENABLED=false\n"
                  + "FOO=1\n"
                  + "KNOWLEDGE_ENABLED=false\n"
                  + "KNOWLEDGE_ENABLED=other\n")
    res = _run(f, "--no-restart", "set", "KNOWLEDGE_ENABLED", "true", "KNOWLEDGE_ORGS", "*")
    assert res.returncode == 0, res.stderr
    assert _lines(f) == [
        "DEPLOY_ENV=staging",
        "OPENAI_API_KEY=" + SECRET,
        "KNOWLEDGE_ENABLED=true",   # replaced in place, the two copies gone
        "FOO=1",
        "KNOWLEDGE_ORGS=*",         # appended
    ]
    res = _run(f, "--list")
    assert res.returncode == 0, res.stderr
    assert res.stdout == "KNOWLEDGE_ENABLED=true\nKNOWLEDGE_ORGS=*\n"


def test_no_restart_may_come_last(tmp_path: Path) -> None:
    f = _env_file(tmp_path, STAGING)
    res = _run(f, "set", "COMPANY_SEARCH_ENABLED", "true", "--no-restart")
    assert res.returncode == 0, res.stderr
    assert "COMPANY_SEARCH_ENABLED=true" in _lines(f)


def test_set_keeps_the_inode_and_mode(tmp_path: Path) -> None:
    """deploy/AGENTS.md, WS-49 BH-2: a writer of .env never replaces its inode."""
    f = _env_file(tmp_path, STAGING)
    ino = f.stat().st_ino
    res = _run(f, "--no-restart", "set", "KNOWLEDGE_ENABLED", "true")
    assert res.returncode == 0, res.stderr
    assert f.stat().st_ino == ino
    assert f.stat().st_mode & 0o777 == 0o600
    assert not list(tmp_path.glob(".env-edit.*")), "a temp file stayed behind"


def test_unset_removes_every_line_of_the_key(tmp_path: Path) -> None:
    f = _env_file(tmp_path, STAGING
                  + "KNOWLEDGE_ORGS=a\nFOO=1\nKNOWLEDGE_ORGS=b\nCRM_ORGS=x\n")
    res = _run(f, "--no-restart", "unset", "KNOWLEDGE_ORGS", "CRM_ORGS")
    assert res.returncode == 0, res.stderr
    assert _lines(f) == ["DEPLOY_ENV=staging", "FOO=1"]
    assert _run(f, "--list").stdout == ""


def test_an_unchanged_value_writes_nothing(tmp_path: Path) -> None:
    f = _env_file(tmp_path, STAGING + "KNOWLEDGE_ENABLED=true\n")
    res = _run(f, "--no-restart", "set", "KNOWLEDGE_ENABLED", "true")
    assert res.returncode == 0, res.stderr
    assert "no change" in res.stdout
    assert _backups(f) == []


def test_a_key_that_is_a_prefix_of_another_is_its_own(tmp_path: Path) -> None:
    f = _env_file(tmp_path, STAGING + "CRM_ORGS=a\nCRM_ORGS_EXTRA=b\n")
    res = _run(f, "--no-restart", "unset", "CRM_ORGS")
    assert res.returncode == 0, res.stderr
    assert _lines(f) == ["DEPLOY_ENV=staging", "CRM_ORGS_EXTRA=b"]


# ── Rule 3: exact names, the deny list, the locale ───────────────────────────


def test_the_allowlist_is_exactly_these_names() -> None:
    body = _body()
    block = re.search(r"^readonly ALLOWED_KEYS=\(\n(.*?)^\)$", body, re.M | re.S)
    assert block, "ALLOWED_KEYS must be one array of exact names"
    names = block.group(1).split()
    assert names == ALLOWED
    for n in names:
        assert re.fullmatch(r"[A-Z][A-Z0-9_]+", n), f"{n} is a pattern, not a name"
    readme = README.read_text(encoding="utf-8")
    for n in names:
        assert f"`{n}`" in readme, f"README.md does not list {n}"


@pytest.mark.parametrize("key", ALLOWED)
def test_an_allowlisted_key_is_written(tmp_path: Path, key: str) -> None:
    f = _env_file(tmp_path, STAGING)
    res = _run(f, "--no-restart", "set", key, "on")
    assert res.returncode == 0, (key, res.stderr)
    assert f"{key}=on" in _lines(f)


@pytest.mark.parametrize("key", [
    # Owner-gated, money and third-party switches that a glob let in (P2-b).
    "AGENT_ACCESS_ENABLED", "AGENT_ACCESS_ORGS", "AGENT_ACCESS_WRITES_ENABLED",
    "AGENT_RUNS_ENABLED", "BYOK_ENABLED", "ROUTER_SERVING_ENABLED",
    "CONSOLE_BOOTSTRAP_ENABLED", "GITHUB_APP_ENABLED", "MAF_MCP_ORGS",
    "GRAPHITI_ENABLED", "DECIDE_ENABLED", "DECIDE_FEATURE_MODES", "DECIDE_FEATURE_ORGS",
    "NARROWING_AGENTS", "MY_DAY_LOOP_ENABLED", "AGENT_REGISTRY_ORGS", "MEM0_ENABLED",
    "ACTION_BROKER_ENFORCE", "AI_TIER_ROUTING", "MY_DAY_DECIDE_ORGS",
    "WHATSAPP_ASSISTANT_ORGS", "CRM_MIRROR_ORGS", "KNOWLEDGE_OTHER",
    # Never on the list at all.
    "COPILOT_CHAT_MODEL", "PG_MODE", "FOO", "SKIP_PRE_MIGRATION_BACKUP", "X",
    "knowledge_enabled", "1_ENABLED", "KNOWLEDGE-ENABLED", "A" * 65 + "_ENABLED",
])
def test_a_key_outside_the_allowlist_is_refused(tmp_path: Path, key: str) -> None:
    f = _env_file(tmp_path, STAGING)
    res = _run(f, "--no-restart", "set", key, "1")
    assert res.returncode == 2, (key, res.stdout)
    assert _lines(f) == ["DEPLOY_ENV=staging"]


@pytest.mark.parametrize("key", [
    "OPENAI_API_KEY", "GATEWAY_INTERNAL_TOKEN", "DATABASE_URL", "DEPLOY_ENV",
    "ACB_MASTER_KEY", "AUTH_SECRET", "POSTGRES_PASSWORD", "PGPASSWORD", "PGHOST",
    "GATEWAY_PORT", "SENTRY_DSN", "KNOWLEDGE_API_KEY",
    # The outbound switches stay off on staging (§5.10 item 3).
    "EMAIL_SYNC_ENABLED", "WHATSAPP_BRIDGE_ENABLED", "MEETING_BOT_ENABLED",
    "REACH_ENABLED", "MEMBER_INVITE_EMAIL_ENABLED", "WORKFLOW_SCHEDULER_ENABLED",
    "REACH_ORGS",
])
def test_the_deny_list_refuses(tmp_path: Path, key: str) -> None:
    f = _env_file(tmp_path, STAGING)
    for args in (("set", key, "true"), ("unset", key)):
        res = _run(f, "--no-restart", *args)
        assert res.returncode == 2, (key, args, res.stdout)
        assert "refused" in res.stderr
    assert _lines(f) == ["DEPLOY_ENV=staging"]
    assert _backups(f) == []


def test_the_deny_list_stays_as_a_second_wall() -> None:
    body = _body()
    for name in ("DENY_RE", "HELD_RE"):
        assert re.search(rf"^readonly {name}=", body, re.M), name
        assert f"=~ ${name} ]] && return 1" in body
        assert f"=~ ${name} ]] && refuse" in body


def test_a_non_ascii_capital_is_refused_under_a_utf8_locale(tmp_path: Path) -> None:
    """P2-a. sudo keeps LANG and LC_*. Under a UTF-8 locale [A-Z] matches
    U+212A, and Python lowercases U+212A to `k`. The script runs in C, so the
    name grammar refuses it."""
    have = subprocess.run(["locale", "-a"], capture_output=True, text=True).stdout.lower()
    loc = next((n for n in ("en_US.UTF-8", "C.UTF-8")
                if n.lower().replace("-", "") in have.replace("-", "")), None)
    if loc is None:
        pytest.skip("no UTF-8 locale on this machine")
    f = _env_file(tmp_path, STAGING)
    key = "KNOWLEDGE_" + chr(0x212A) + "EY"   # U+212A, the KELVIN SIGN
    res = _run(f, "--no-restart", "set", key, "1",
               extra={"LC_ALL": loc, "LANG": loc})
    assert res.returncode == 2, res.stdout
    assert "is not a key name" in res.stderr
    assert _lines(f) == ["DEPLOY_ENV=staging"]
    body = _body()
    assert body.index("export LC_ALL=C") < body.index("=~")


def test_a_next_public_key_is_refused(tmp_path: Path) -> None:
    f = _env_file(tmp_path, STAGING)
    for key in ("NEXT_PUBLIC_SHOW_PREVIEW_APPS", "NEXT_PUBLIC_TASKS_ENABLED",
                "NEXT_PUBLIC_MY_DAY_FEED"):
        res = _run(f, "--no-restart", "set", key, "1")
        assert res.returncode == 2, key
        assert ("a NEXT_PUBLIC key needs .env.local and a rebuild; "
                "this script cannot set it") in res.stderr
    assert _lines(f) == ["DEPLOY_ENV=staging"]
    assert "acb-workbench" not in _body().split("# ── 8. The restart")[1]


def test_one_bad_key_changes_nothing(tmp_path: Path) -> None:
    f = _env_file(tmp_path, STAGING)
    res = _run(f, "--no-restart", "set", "KNOWLEDGE_ENABLED", "true", "OPENAI_API_KEY", "x")
    assert res.returncode == 2
    assert _lines(f) == ["DEPLOY_ENV=staging"]


@pytest.mark.parametrize("line", [
    "export KNOWLEDGE_ORGS=old", " KNOWLEDGE_ORGS=old", "KNOWLEDGE_ORGS = old",
    "\tKNOWLEDGE_ORGS=old",
])
def test_an_odd_form_of_the_requested_key_is_refused(tmp_path: Path, line: str) -> None:
    """P3. pydantic still reads `export KEY=` and ` KEY=`, so a write beside it
    would not be the value the gateway sees."""
    f = _env_file(tmp_path, STAGING + line + "\n")
    before = f.read_bytes()
    for args in (("set", "KNOWLEDGE_ORGS", "*"), ("unset", "KNOWLEDGE_ORGS")):
        res = _run(f, "--no-restart", *args)
        assert res.returncode == 2, (line, args, res.stdout)
        assert "form other than KNOWLEDGE_ORGS=" in res.stderr
    assert f.read_bytes() == before
    # Another key is still fine.
    assert _run(f, "--no-restart", "set", "CRM_ENABLED", "true").returncode == 0


# ── Rule 4: the value ────────────────────────────────────────────────────────


@pytest.mark.parametrize("value", [
    "", "a b", '"true"', "'true'", "$(x)", "${HOME}", "`id`", "a\nDEPLOY_ENV=production",
    "a;b", "a|b", "a\\b", "a=b", "x" * 513, "\t",
])
def test_a_bad_value_is_refused(tmp_path: Path, value: str) -> None:
    f = _env_file(tmp_path, STAGING)
    res = _run(f, "--no-restart", "set", "KNOWLEDGE_ORGS", value)
    assert res.returncode == 2, (value, res.stdout)
    assert _lines(f) == ["DEPLOY_ENV=staging"]
    if value:
        assert value not in res.stderr


@pytest.mark.parametrize("value", [
    "*", "true", "0", "org-a,org-b", "a@b.co", "email.triage:shadow,my_day.x:on",
    "x" * 512, "+1/2",
])
def test_a_good_value_is_written(tmp_path: Path, value: str) -> None:
    f = _env_file(tmp_path, STAGING)
    res = _run(f, "--no-restart", "set", "KNOWLEDGE_ORGS", value)
    assert res.returncode == 0, (value, res.stderr)
    assert _lines(f) == ["DEPLOY_ENV=staging", f"KNOWLEDGE_ORGS={value}"]


# ── --list leaks nothing ─────────────────────────────────────────────────────


def test_list_prints_no_other_line(tmp_path: Path) -> None:
    f = _env_file(tmp_path, STAGING
                  + "OPENAI_API_KEY=" + SECRET + "\n"
                  + "DATABASE_URL=postgresql://u:" + SECRET + "@h/db\n"
                  + "# KNOWLEDGE_ENABLED=commented\n"
                  + "export KNOWLEDGE_ORGS=" + SECRET + "\n"
                  + "KNOWLEDGE_API_KEY=" + SECRET + "\n"
                  + "EMAIL_SYNC_ENABLED=false\n"
                  + "AGENT_ACCESS_ENABLED=true\n"
                  + "NEXT_PUBLIC_SHOW_PREVIEW_APPS=1\n"
                  + "KNOWLEDGE_ENABLED=true\n"
                  + "garbage line\n")
    res = _run(f, "--list")
    assert res.returncode == 0, res.stderr
    assert res.stdout == "KNOWLEDGE_ENABLED=true\n"
    assert SECRET not in res.stdout + res.stderr


def test_set_never_prints_the_file(tmp_path: Path) -> None:
    f = _env_file(tmp_path, STAGING + "OPENAI_API_KEY=" + SECRET + "\n")
    res = _run(f, "--no-restart", "set", "KNOWLEDGE_ENABLED", "true")
    assert res.returncode == 0
    assert SECRET not in res.stdout + res.stderr


# ── Rule 6: the backup ───────────────────────────────────────────────────────


def test_each_write_makes_a_backup(tmp_path: Path) -> None:
    f = _env_file(tmp_path, STAGING + "OPENAI_API_KEY=" + SECRET + "\n")
    first = f.read_text(encoding="utf-8")
    assert _run(f, "--no-restart", "set", "KNOWLEDGE_ENABLED", "true").returncode == 0
    assert _run(f, "--no-restart", "set", "KNOWLEDGE_ENABLED", "false").returncode == 0
    assert _run(f, "--no-restart", "unset", "KNOWLEDGE_ENABLED").returncode == 0
    baks = _backups(f)
    assert len(baks) == 3, baks
    for b in baks:
        assert re.fullmatch(r"\.env\.bak-\d{8}T\d{6}\.\d{9}Z(-\d+)?", b.name), b.name
        assert b.stat().st_mode & 0o777 == 0o600, "cp -p keeps the mode"
    assert [b.read_text(encoding="utf-8") for b in baks] == [
        first, first + "KNOWLEDGE_ENABLED=true\n", first + "KNOWLEDGE_ENABLED=false\n"]


def test_only_the_newest_20_backups_stay(tmp_path: Path) -> None:
    f = _env_file(tmp_path, STAGING)
    for i in range(23):
        res = _run(f, "--no-restart", "set", "KNOWLEDGE_ORGS", f"org{i}")
        assert res.returncode == 0, res.stderr
    baks = _backups(f)
    assert len(baks) == 20, len(baks)
    # The three oldest went: the newest backup holds org21, the oldest org2.
    assert baks[-1].read_text(encoding="utf-8").endswith("KNOWLEDGE_ORGS=org21\n")
    assert baks[0].read_text(encoding="utf-8").endswith("KNOWLEDGE_ORGS=org2\n")


# ── Rule 7: the pin file ─────────────────────────────────────────────────────


def test_set_and_unset_keep_the_pin_file(tmp_path: Path) -> None:
    f = _env_file(tmp_path, STAGING)
    pins = _pins(f)
    assert _run(f, "--no-restart", "set", "KNOWLEDGE_ENABLED", "true",
                "KNOWLEDGE_ORGS", "*").returncode == 0
    assert sorted(_lines(pins)) == ["KNOWLEDGE_ENABLED", "KNOWLEDGE_ORGS"]
    assert pins.stat().st_mode & 0o777 == 0o600
    # A second set of the same key, and an unchanged value, add no copy.
    assert _run(f, "--no-restart", "set", "KNOWLEDGE_ENABLED", "false").returncode == 0
    assert _run(f, "--no-restart", "set", "CRM_ENABLED", "true").returncode == 0
    assert _run(f, "--no-restart", "set", "CRM_ENABLED", "true").returncode == 0
    assert sorted(_lines(pins)) == ["CRM_ENABLED", "KNOWLEDGE_ENABLED", "KNOWLEDGE_ORGS"]
    assert _run(f, "--no-restart", "unset", "KNOWLEDGE_ORGS", "CRM_ENABLED").returncode == 0
    assert _lines(pins) == ["KNOWLEDGE_ENABLED"]
    assert not list(tmp_path.glob(".staging-flags.*")), "a temp file stayed behind"


def test_a_refused_command_leaves_the_pin_file(tmp_path: Path) -> None:
    f = _env_file(tmp_path, STAGING)
    _pins(f).write_text("CRM_ENABLED\n", encoding="utf-8")
    assert _run(f, "--no-restart", "set", "NEXT_PUBLIC_X_ENABLED", "1").returncode == 2
    assert _run(f, "--no-restart", "unset", "OPENAI_API_KEY").returncode == 2
    assert _lines(_pins(f)) == ["CRM_ENABLED"]


# ── Rule 8: the restart ──────────────────────────────────────────────────────


def test_the_restart_is_the_gateway_and_waits_for_health() -> None:
    body = _body()
    assert "systemctl restart acb-gateway ||" in body
    assert "readonly HEALTH_URL=http://127.0.0.1:8080/health" in body
    assert "readonly HEALTH_WAIT_S=90" in body
    # A curl exit code must not end the wait loop under set -e.
    assert re.search(r"code=\"\$\(curl [^\n]*\|\| true\)\"", body)
    assert body.rstrip().splitlines()[-1].startswith("fail "), "no 200 must exit non-zero"


# ── Rule 9: the log ──────────────────────────────────────────────────────────


def test_the_log_names_keys_and_never_values(tmp_path: Path) -> None:
    f = _env_file(tmp_path, STAGING)
    res = _run(f, "--no-restart", "set", "KNOWLEDGE_ORGS", "org-zz-unique", "CRM_ORGS", "*")
    assert res.returncode == 0, res.stderr
    log = [ln for ln in res.stderr.splitlines() if "log:" in ln]
    assert len(log) == 1
    assert "set KNOWLEDGE_ORGS CRM_ORGS" in log[0] and "user=" in log[0]
    assert "org-zz-unique" not in res.stdout + res.stderr
    body = _body()
    assert 'who="${SUDO_USER:-$(id -un)}"' in body
    assert 'logger -t acb-staging-flag -- "$msg"' in body


# ── Usage ────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("args", [
    (), ("bogus",), ("set",), ("set", "KNOWLEDGE_ENABLED"),
    ("set", "KNOWLEDGE_ENABLED", "true", "CRM_ORGS"), ("unset",), ("--list", "extra"),
])
def test_bad_usage_exits_2(tmp_path: Path, args: tuple[str, ...]) -> None:
    f = _env_file(tmp_path, STAGING)
    res = _run(f, *args)
    assert res.returncode == 2, (args, res.stdout)
    assert _lines(f) == ["DEPLOY_ENV=staging"]
