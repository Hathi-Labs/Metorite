"""WS-27bm S16 — every deploy proves that chat saves.

Spec: ``project-docs/specs/projects_ai_chat.md`` §21.10.

For two months every chat save on production failed, and every deploy went
green. ``deploy/smoke_chat.sh`` now runs on the box after each verified
deploy. It mints a 900 s session IN MEMORY and runs
``scripts/smoke_chat_persist.py``. This file is the fence (R7) for three
claims:

  1. ``deploy.yml`` runs the smoke AFTER the deploy is verified, on both
     delivery paths, and a failed smoke turns the run red. Exit 1 and exit 2
     never retry. Only an ssh blip retries.
  2. The minted cookie and ``AUTH_SECRET`` never reach a file, a command line
     or the log. The behavioural case runs the real script against a fake
     ``node`` and a fake python, and then searches every file and every
     argument list for the two values.
  3. The script targets ``https://app.metorite.com`` by default. That is the
     ``AUTH_URL`` host. ``metorite.com`` is a different site.
"""
from __future__ import annotations

import contextlib
import os
import re
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "deploy.yml"
SCRIPT = ROOT / "deploy" / "smoke_chat.sh"
LIB = ROOT / "scripts" / "ci_deploy_reach.sh"
HOST_KEY_LIB = ROOT / "scripts" / "ci_ssh_host_key.sh"
PINNED = ROOT / "deploy" / "hostinger" / "known_hosts"

SECRET = "S3CRET-auth-value-4711"
JWE = "JWE-minted-value-0815"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def jobs() -> dict:
    return yaml.safe_load(_read(WORKFLOW))["jobs"]


def _smoke_step(jobs: dict) -> str:
    steps = jobs["chat-smoke"]["steps"]
    return next(s["run"] for s in steps if "smoke_chat.sh" in s.get("run", ""))


def _code_lines(text: str) -> list[str]:
    """The script without its comments. A comment may name a word the code
    must not use."""
    out = []
    for line in text.splitlines():
        s = line.strip()
        if s and not s.startswith("#"):
            out.append(line)
    return out


# ── 1. The wiring ───────────────────────────────────────────────────────────


def test_the_smoke_job_runs_after_the_deploy_on_both_paths(jobs: dict) -> None:
    job = jobs["chat-smoke"]
    assert set(job["needs"]) == {"deploy", "pull-delivery"}
    # The WHOLE expression, with the whitespace folded. A substring check let
    # `${{ always() }} # needs.deploy.result == 'success'` through.
    cond = " ".join(job["if"].split())
    assert cond == (
        "${{ always() && needs.deploy.result == 'success' && "
        "(needs.pull-delivery.result == 'success' || "
        "needs.pull-delivery.result == 'skipped') }}"
    ), cond


@pytest.mark.parametrize(("deploy", "pull", "runs"), [
    ("success", "skipped", True),
    ("success", "success", True),
    ("success", "failure", False),
    ("success", "cancelled", False),
    ("failure", "skipped", False),
    ("cancelled", "skipped", False),
    ("skipped", "skipped", False),
])
def test_the_if_expression_over_the_cases(jobs: dict, deploy: str, pull: str,
                                          runs: bool) -> None:
    """Evaluate the `if` as GitHub does, for each pair of results."""
    cond = " ".join(jobs["chat-smoke"]["if"].split())
    m = re.fullmatch(r"\$\{\{(.*)\}\}", cond)
    assert m, cond
    expr = m.group(1)
    assert re.fullmatch(r"[\s\w.()'=|&!-]*", expr), f"an unexpected token in {expr!r}"
    expr = (expr.replace("always()", "True")
            .replace("needs.deploy.result", repr(deploy))
            .replace("needs.pull-delivery.result", repr(pull))
            .replace("&&", " and ").replace("||", " or "))
    assert eval(expr) is runs, (expr, deploy, pull)  # a fixed grammar, checked above


def test_the_deploy_job_restarts_and_verifies_before_the_smoke(jobs: dict) -> None:
    """The restart lives in vps_apply.sh, inside the `deploy` job's step, and
    `verify()` runs after it. The smoke job needs that job, so it runs later."""
    deploy_run = next(s["run"] for s in jobs["deploy"]["steps"] if s.get("id") == "deploy")
    assert "scripts/vps_apply.sh" in deploy_run and "verify()" in deploy_run
    assert "restart acb-workbench" in _read(ROOT / "scripts" / "vps_apply.sh")
    assert "smoke_chat.sh" not in deploy_run, "the smoke must not run inside the apply rounds"


def test_the_smoke_pipes_the_script_to_the_box(jobs: dict) -> None:
    step = _smoke_step(jobs)
    assert '"bash -s" < deploy/smoke_chat.sh' in step
    assert '"${CI_SSH_OPTS[@]}"' in step, "use the H-142 ssh options, not a second set"
    assert "ci_pin_host_key" in step, "the host-key pin (H-200) comes before the first ssh"
    assert "wait_for_ssh" in step


def test_a_failed_smoke_is_red_and_never_silenced(jobs: dict) -> None:
    job = jobs["chat-smoke"]
    for s in job["steps"]:
        assert not s.get("continue-on-error"), "a smoke that cannot fail is a comment"
    assert not job.get("continue-on-error")
    step = _smoke_step(jobs)
    assert "set +e" in step, "under bash -e the ssh pipe would end the step before the case"
    assert "rc=${PIPESTATUS[0]}" in step, "tee always succeeds. Read ssh's code."


def test_no_rollback_is_wired(jobs: dict) -> None:
    """R6: we only roll forward."""
    step = _smoke_step(jobs).lower()
    assert "rollback" not in step and "git reset" not in step and "revert" not in step


def _int_default(text: str, name: str) -> int:
    m = re.search(rf'^{name}="\$\{{{name}:-(\d+)\}}"', text, re.M)
    assert m, f"{name} needs a numeric default"
    return int(m.group(1))


def test_the_time_bounds_add_up(jobs: dict) -> None:
    """Fix round 1: the script's worst case fits inside the ssh timeout, and
    three of those plus the connect budget fit inside the job timeout."""
    text = _read(SCRIPT)
    lock = _int_default(text, "SMOKE_LOCK_WAIT")
    run = _int_default(text, "SMOKE_RUN_S")
    tries = _int_default(text, "SMOKE_WAIT_TRIES")
    nap = _int_default(text, "SMOKE_WAIT_NAP")
    assert "timeout -k 5 30 node " in text
    assert 'timeout -k 5 20 "$PY" -c "$PROBE_PY"' in text
    assert 'timeout -k 10 "$SMOKE_RUN_S" "$PY" "$SMOKE_PY"' in text
    worst = 35 + lock + tries * 25 + (tries - 1) * nap + run + 10
    m = re.search(r"worst case\s+(\d+) s", text)
    assert m and int(m.group(1)) == worst, (worst, m and m.group(1))
    step = _smoke_step(jobs)
    ssh_t = int(re.search(r"SMOKE_SSH_TIMEOUT=(\d+)", step).group(1))
    assert ssh_t > worst, "the script must report before ssh is killed"
    assert 'timeout -k 10 "$SMOKE_SSH_TIMEOUT" ssh' in step
    job_s = jobs["chat-smoke"]["timeout-minutes"] * 60
    assert job_s >= 3 * (ssh_t + 10) + 300 + 90 + 20 + 40, job_s
    # Fix round 2: the session is minted AFTER the lock, and it outlives
    # everything that comes after the mint.
    after_mint = 35 + tries * 25 + (tries - 1) * nap + run + 10
    max_age = int(re.search(r"^MAX_AGE_S=(\d+)$", text, re.M).group(1))
    assert max_age >= after_mint, (max_age, after_mint)
    assert text.index('flock -s -w "$SMOKE_LOCK_WAIT" 9') < text.index("timeout -k 5 30 node "), (
        "mint the session after the lock, or a long wait uses up its life")


# ── 2. The script's text ────────────────────────────────────────────────────


def test_the_script_targets_the_app_host_by_default() -> None:
    text = _read(SCRIPT)
    m = re.search(r'^SMOKE_BASE_URL="\$\{SMOKE_BASE_URL:-([^}]*)\}"', text, re.M)
    assert m, "SMOKE_BASE_URL needs a default"
    assert m.group(1) == "https://app.metorite.com"


def test_the_lock_default_is_the_file_every_apply_locks() -> None:
    """Fix round 3: the smoke's shared lock must be the SAME file that
    vps_apply.sh and vps_pull.sh lock exclusive, or it serialises nothing."""
    line = 'DEPLOY_LOCK="${DEPLOY_LOCK:-$(dirname "$APP_DIR")/acb-deploy.lock}"'
    for f in (SCRIPT, ROOT / "scripts" / "vps_apply.sh", ROOT / "scripts" / "vps_pull.sh"):
        assert re.search(rf"^{re.escape(line)}$", _read(f), re.M), f"{f.name} has another default"
    if sys.platform.startswith("linux") and shutil.which("bash"):
        env = {k: v for k, v in os.environ.items() if k != "DEPLOY_LOCK"}
        env["APP_DIR"] = "/opt/acb/app"
        out = subprocess.run(["bash", "-c", f'{line}; printf %s "$DEPLOY_LOCK"'], env=env,
                             capture_output=True, text=True, encoding="utf-8", timeout=30)
        assert out.stdout == "/opt/acb/acb-deploy.lock", out.stdout + out.stderr


def test_the_script_does_not_use_the_static_cookie_file() -> None:
    code = "\n".join(_code_lines(_read(SCRIPT)))
    assert ".smoke/cookie" not in code, "the static cookie expires. Mint one per run."


def test_the_cookie_never_goes_to_a_file_or_a_command_line() -> None:
    code = _code_lines(_read(SCRIPT))
    secret_words = ("SMOKE_COOKIE", "jwe", "auth_secret", "AUTH_SECRET")
    for line in code:
        assert not re.search(r"\btee\b", line), f"tee writes to a file: {line!r}"
        if not any(w in line for w in secret_words):
            continue
        # The only redirects allowed on such a line: to stderr, or from the
        # env file, or to /dev/null.
        stripped = re.sub(r"2>/dev/null|>&2|<\s*\"\$WB_ENV\"", "", line)
        assert ">" not in stripped, f"a secret next to a redirect: {line!r}"
        for tool in ("curl", "echo", "printf", "wget"):
            assert not re.search(rf"\b{tool}\b.*\$\{{?(SMOKE_COOKIE|jwe|auth_secret)", line), (
                f"a secret on the command line of {tool}: {line!r}")
    assert "set -x" not in "\n".join(code)


# Every line that EXPANDS a secret, word for word. A new use needs a review
# and a new entry here. `cp /dev/stdin /tmp/x <<<"$SMOKE_COOKIE"` has no
# redirect of the form the rule above looks for, and this catches it.
SECRET_LINES = {
    'auth_secret="${auth_secret#\\"}"; auth_secret="${auth_secret%\\"}"',
    "auth_secret=\"${auth_secret#\\'}\"; auth_secret=\"${auth_secret%\\'}\"",
    '[ -n "$auth_secret" ] || env_fail "AUTH_SECRET is not set in $WB_ENV"',
    'jwe="$(cd "$WB_DIR" && AUTH_SECRET="$auth_secret" SMOKE_MEMBER_EMAIL="$SMOKE_MEMBER_EMAIL" \\',
    '[ -n "$jwe" ] || env_fail "node could not mint a session (next-auth/jwt in $WB_DIR?)"',
    'SMOKE_COOKIE="$COOKIE_NAME=$jwe"',
    'state="$(SMOKE_COOKIE="$SMOKE_COOKIE" timeout -k 5 20 "$PY" -c "$PROBE_PY" </dev/null '
    '2>/dev/null)" || state="down:000"',
    'SMOKE_COOKIE="$SMOKE_COOKIE" timeout -k 10 "$SMOKE_RUN_S" "$PY" "$SMOKE_PY" </dev/null '
    '|| rc=$?',
}
_EXPANDS = re.compile(r"\$\{?(SMOKE_COOKIE|jwe|auth_secret)\b")


def test_every_expansion_of_a_secret_is_a_line_of_record() -> None:
    seen = set()
    for line in _code_lines(_read(SCRIPT)):
        if _EXPANDS.search(line):
            assert line.strip() in SECRET_LINES, f"a new use of a secret: {line.strip()!r}"
            seen.add(line.strip())
    assert seen == SECRET_LINES, SECRET_LINES - seen


def test_every_child_reads_dev_null() -> None:
    """The script arrives over `bash -s` stdin. The behavioural case below
    proves it for node and python. This one covers the rest."""
    for line in _code_lines(_read(SCRIPT)):
        if "command -v" in line or "[ -x" in line:
            continue
        if re.search(r'(^|[\s(])(node|"\$PY"|git|flock|sed|seq) ', line):
            assert "</dev/null" in line, f"a child without < /dev/null: {line!r}"


# ── 3. Behaviour, against a fake node and a fake python ─────────────────────

# Linux only, like test_deploy_reach.py: on Windows a fake on PATH loses to
# Git Bash's own tools. CI runs these on ubuntu-latest.
_TOOLS = sys.platform.startswith("linux") and all(
    shutil.which(t) for t in ("bash", "timeout", "sed", "tail", "tr")
)
needs_shell = pytest.mark.skipif(not _TOOLS, reason="needs Linux with bash and coreutils")

FAKE_NODE = r"""#!/usr/bin/env bash
printf '%s\n' "$*" >> "$FAKE/argv.log"
got=$(timeout 1 cat 2>/dev/null); [ -n "$got" ] && echo "$0" >> "$FAKE/stdin.log"
[ "$AUTH_SECRET" = "__SECRET__" ] || { echo "wrong secret" >&2; exit 3; }
printf '%s' "__JWE__"
"""

# One fake for both uses: `python -c <probe>` and `python <smoke.py>`.
FAKE_PY = r"""#!/usr/bin/env bash
printf '%s\n' "$*" >> "$FAKE/argv.log"
got=$(timeout 1 cat 2>/dev/null); [ -n "$got" ] && echo "$0" >> "$FAKE/stdin.log"
# Does the parent hold the deploy lock? A new EXCLUSIVE try must then fail.
if flock -n -x "$DEPLOY_LOCK" true 2>/dev/null; then echo free >> "$FAKE/lock.log"
else echo held >> "$FAKE/lock.log"; fi
[ "$SMOKE_COOKIE" = "__Secure-authjs.session-token=__JWE__" ] || { echo "down:401"; exit 0; }
if [ "$1" = "-c" ]; then
  n=$(cat "$FAKE/probe_n" 2>/dev/null || echo 0); n=$((n + 1)); echo "$n" > "$FAKE/probe_n"
  if [ "$n" -le "${PROBE_DOWN:-0}" ]; then echo "down:502"; else echo "${PROBE_STATE:-ok}"; fi
  exit 0
fi
[ -n "${SMOKE_HANG:-}" ] && exec /bin/sleep "$SMOKE_HANG"
echo "ok   1 create session"
[ "${SMOKE_RC:-0}" = 1 ] && { echo "FAIL 2 save one row: HTTP 500"; exit 1; }
echo "ok   2 save one row (saved: 1)"
echo "PASS chat persistence on $SMOKE_BASE_URL as $SMOKE_MEMBER_EMAIL"
"""


@pytest.fixture()
def app(tmp_path: Path):
    fake = tmp_path / "fake"
    binr = tmp_path / "bin"
    appd = tmp_path / "app"
    for d in (fake, binr, appd / ".venv" / "bin", appd / "scripts",
              appd / "workbench" / "control_plane"):
        d.mkdir(parents=True)
    node = binr / "node"
    node.write_text(FAKE_NODE.replace("__SECRET__", SECRET).replace("__JWE__", JWE),
                    encoding="utf-8", newline="\n")
    node.chmod(0o755)
    py = appd / ".venv" / "bin" / "python"
    py.write_text(FAKE_PY.replace("__JWE__", JWE), encoding="utf-8", newline="\n")
    py.chmod(0o755)
    (binr / "sleep").write_text("#!/usr/bin/env bash\n:\n", encoding="utf-8", newline="\n")
    (binr / "sleep").chmod(0o755)
    (appd / "scripts" / "smoke_chat_persist.py").write_text("# fake\n", encoding="utf-8")
    (tmp_path / "acb-deploy.lock").write_text("", encoding="utf-8")
    (appd / "workbench" / "control_plane" / ".env.local").write_text(
        f'AUTH_URL=https://app.metorite.com\nAUTH_SECRET="{SECRET}"\n',
        encoding="utf-8", newline="\n")

    def run(via_stdin: bool = False, **extra: str) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env.pop("SMOKE_BASE_URL", None)
        env.update(PATH=f"{binr}{os.pathsep}{env.get('PATH', '')}",
                   FAKE=fake.as_posix(), APP_DIR=appd.as_posix(), HOME=tmp_path.as_posix(),
                   DEPLOY_LOCK=(tmp_path / "acb-deploy.lock").as_posix())
        env.update(extra)
        if via_stdin:
            # How deploy.yml runs it: `ssh ... "bash -s" < deploy/smoke_chat.sh`.
            return subprocess.run(["bash", "-s"], input=_read(SCRIPT), cwd=tmp_path, env=env,
                                  capture_output=True, text=True, encoding="utf-8",
                                  timeout=60)
        return subprocess.run(["bash", SCRIPT.as_posix()], cwd=tmp_path, env=env,
                              stdin=subprocess.DEVNULL, capture_output=True, text=True,
                              encoding="utf-8", timeout=60)

    class App:
        pass

    a = App()
    a.root, a.fake, a.run = tmp_path, fake, run
    return a


def _tmp_snapshot(skip: Path) -> dict[Path, float]:
    """Every file under the system temp dir, with its mtime. The test's own
    tmp_path is left out, because its fakes hold the values on purpose."""
    snap: dict[Path, float] = {}
    for dirpath, dirnames, filenames in os.walk("/tmp"):
        if Path(dirpath).resolve() == skip.resolve():
            dirnames[:] = []
            continue
        for name in filenames:
            p = Path(dirpath) / name
            with contextlib.suppress(OSError):
                snap[p] = p.stat().st_mtime
    return snap


def _tmp_changed(before: dict[Path, float], skip: Path) -> list[Path]:
    after = _tmp_snapshot(skip)
    return [p for p, m in after.items() if p.is_file() and before.get(p) != m]


def _files_holding(root: Path, needle: str) -> list[Path]:
    hits = []
    for p in root.rglob("*"):
        if p.is_file() and needle in p.read_text(encoding="utf-8", errors="replace"):
            hits.append(p)
    return hits


@needs_shell
class TestTheScript:
    def test_a_pass_prints_only_the_step_lines_and_leaks_nothing(self, app) -> None:
        before = _tmp_snapshot(app.root)
        r = app.run()
        for path in _tmp_changed(before, app.root):
            body = path.read_text(encoding="utf-8", errors="replace")
            assert JWE not in body and SECRET not in body, f"a secret in {path}"
        assert r.returncode == 0, r.stdout + r.stderr
        assert r.stdout.splitlines() == [
            "ok   1 create session",
            "ok   2 save one row (saved: 1)",
            "PASS chat persistence on https://app.metorite.com as smoke-chat@smoke.metorite.invalid",
        ]
        for value in (SECRET, JWE):
            assert value not in r.stdout + r.stderr
            argv = (app.fake / "argv.log").read_text(encoding="utf-8")
            assert value not in argv, f"{value} reached a command line"
        # The env file and the fakes (which check the values) hold them. The
        # script wrote neither value to any file of its own.
        assert sorted(p.name for p in _files_holding(app.root, SECRET)) == [".env.local", "node"]
        assert sorted(p.name for p in _files_holding(app.root, JWE)) == ["node", "python"]

    def test_it_waits_out_the_restart_window(self, app) -> None:
        r = app.run(PROBE_DOWN="3")
        assert r.returncode == 0, r.stdout + r.stderr
        assert (app.fake / "probe_n").read_text(encoding="utf-8").strip() == "4"

    def test_a_step_failure_exits_1(self, app) -> None:
        r = app.run(SMOKE_RC="1")
        assert r.returncode == 1
        assert "FAIL 2 save one row" in r.stdout

    def test_a_missing_smoke_org_exits_2_with_a_clear_message(self, app) -> None:
        r = app.run(PROBE_STATE="org:none")
        assert r.returncode == 2
        assert "the smoke org is missing" in r.stderr
        assert (app.fake / "probe_n").read_text(encoding="utf-8").strip() == "1"

    def test_a_workbench_that_never_answers_exits_2(self, app) -> None:
        r = app.run(PROBE_DOWN="99", SMOKE_WAIT_TRIES="3")
        assert r.returncode == 2
        assert "did not answer 200 after 3 tries" in r.stderr

    def test_the_wait_and_the_smoke_hold_the_deploy_lock(self, app) -> None:
        """Fix round 1, P2: a second deploy's restart must not land mid-smoke."""
        r = app.run(PROBE_DOWN="1")
        assert r.returncode == 0, r.stdout + r.stderr
        seen = (app.fake / "lock.log").read_text(encoding="utf-8").split()
        assert seen and set(seen) == {"held"}, seen

    def test_a_busy_lock_exits_75_and_checks_nothing(self, app) -> None:
        import fcntl

        with open(app.root / "acb-deploy.lock", encoding="utf-8") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            r = app.run(SMOKE_LOCK_WAIT="1")
        assert r.returncode == 75, r.stdout + r.stderr
        assert "BUSY" in r.stderr and "the box holds" in r.stderr
        assert r.stdout == ""
        assert not (app.fake / "lock.log").exists(), "nothing may run without the lock"

    def test_a_failure_names_the_served_sha(self, app) -> None:
        r = app.run(SMOKE_RC="1")
        assert r.returncode == 1
        assert "the box holds" in r.stderr

    def test_under_bash_s_no_child_reads_the_script(self, app) -> None:
        """Fix round 1: the script arrives on stdin, as vps_apply.sh does. A
        child that reads stdin eats the rest of the script. Every child gets
        `< /dev/null`."""
        r = app.run(via_stdin=True, PROBE_DOWN="1")
        assert r.returncode == 0, r.stdout + r.stderr
        assert r.stdout.splitlines()[-1].startswith("PASS chat persistence on")
        assert not (app.fake / "stdin.log").exists(), (
            (app.fake / "stdin.log").read_text(encoding="utf-8"))

    def test_a_persist_run_past_its_bound_is_named_a_timeout(self, app) -> None:
        r = app.run(SMOKE_HANG="5", SMOKE_RUN_S="1")
        assert r.returncode == 1, r.stdout + r.stderr
        assert "did not finish in 1s" in r.stderr

    def test_no_secret_exits_2(self, app) -> None:
        (app.root / "app" / "workbench" / "control_plane" / ".env.local").write_text(
            "AUTH_URL=https://app.metorite.com\n", encoding="utf-8")
        r = app.run()
        assert r.returncode == 2
        assert "AUTH_SECRET is not set" in r.stderr


# ── 4. Behaviour of the workflow step, against a fake ssh ───────────────────

FAKE_SSH = r"""#!/usr/bin/env bash
last="${!#}"
if [ "$last" = "true" ]; then
  [ "${PROBE:-ok}" = down ] && { echo "ssh: connect to host 10.0.0.1 port 22: Connection timed out" >&2; exit 255; }
  exit 0
fi
cat > /dev/null
n=$(cat "$FAKE/run_n" 2>/dev/null || echo 0); n=$((n + 1)); echo "$n" > "$FAKE/run_n"
mode="${SMOKE_MODE:-pass}"
if [ "$mode" = blip ] && [ "$n" = 1 ]; then
  echo "client_loop: send disconnect: Connection reset by peer" >&2; exit 255
fi
case "$mode" in
  faildrop) echo "ok   1 create session"; echo "FAIL 2 save one row: HTTP 500"
            echo "client_loop: send disconnect: Connection reset by peer" >&2; exit 255 ;;
  hang) exit 124 ;;
  failhang) echo "ok   1 create session"; echo "FAIL 2 save one row: HTTP 500"; exit 124 ;;
  exit1) echo "ok   1 create session"; echo "smoke_chat: the smoke did not finish in 240s (exit 124)" >&2; exit 1 ;;
  drop) echo "client_loop: send disconnect: Connection reset by peer" >&2; exit 255 ;;
  nopass) echo "ok   1 create session"; exit 0 ;;
  busy) echo "smoke_chat: BUSY." >&2; exit 75 ;;
  fail) echo "FAIL 2 save one row: HTTP 500"; exit 1 ;;
  env)  echo "smoke_chat: the smoke org is missing." >&2; exit 2 ;;
  auth) echo "acb@10.0.0.1: Permission denied (publickey)." >&2; exit 255 ;;
esac
echo "ok   1 create session"
echo "PASS chat persistence on https://app.metorite.com as smoke-chat@smoke.metorite.invalid"
"""


@pytest.fixture()
def runner(tmp_path: Path):
    fake = tmp_path / "fake"
    binr = tmp_path / "bin"
    repo = tmp_path / "repo"
    home = tmp_path / "home"
    for d in (fake, binr, repo / "scripts", repo / "deploy" / "hostinger", home):
        d.mkdir(parents=True)
    for name, body in (("ssh", FAKE_SSH), ("sleep", "#!/usr/bin/env bash\n:\n")):
        (binr / name).write_text(body, encoding="utf-8", newline="\n")
        (binr / name).chmod(0o755)
    for src, dst in ((LIB, repo / "scripts" / LIB.name),
                     (HOST_KEY_LIB, repo / "scripts" / HOST_KEY_LIB.name),
                     (PINNED, repo / "deploy" / "hostinger" / PINNED.name),
                     (SCRIPT, repo / "deploy" / SCRIPT.name)):
        shutil.copy(src, dst)

    def run(jobs: dict, **extra: str) -> subprocess.CompletedProcess:
        step = repo / "step.sh"
        step.write_text(_smoke_step(jobs), encoding="utf-8", newline="\n")
        env = dict(os.environ)
        env.update(PATH=f"{binr}{os.pathsep}{env.get('PATH', '')}", FAKE=fake.as_posix(),
                   HOME=home.as_posix(), SSH_HOST="10.0.0.1", SSH_USER="acb",
                   SSH_PORT="22", SSH_KEY="k", CONNECT_BUDGET="60")
        env.update(extra)
        return subprocess.run(
            ["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", step.name],
            cwd=repo, env=env, capture_output=True, text=True, encoding="utf-8", timeout=120)

    class Runner:
        pass

    r = Runner()
    r.fake, r.run = fake, run
    return r


def _runs(runner) -> int:
    p = runner.fake / "run_n"
    return int(p.read_text(encoding="utf-8").strip()) if p.exists() else 0


@needs_shell
class TestTheWorkflowStep:
    def test_a_pass_is_green(self, runner, jobs) -> None:
        r = runner.run(jobs)
        assert r.returncode == 0, r.stdout + r.stderr
        assert _runs(runner) == 1

    def test_a_step_failure_is_red_and_does_not_retry(self, runner, jobs) -> None:
        r = runner.run(jobs, SMOKE_MODE="fail")
        assert r.returncode == 1, r.stdout + r.stderr
        assert _runs(runner) == 1
        assert "Chat does not save" in r.stdout

    def test_a_wrong_environment_is_red_and_does_not_retry(self, runner, jobs) -> None:
        r = runner.run(jobs, SMOKE_MODE="env")
        assert r.returncode == 1, r.stdout + r.stderr
        assert _runs(runner) == 1

    def test_a_busy_lock_is_a_warning_and_not_a_red(self, runner, jobs) -> None:
        r = runner.run(jobs, SMOKE_MODE="busy")
        assert r.returncode == 0, r.stdout + r.stderr
        assert "::warning title=Chat smoke did not run::" in r.stdout
        assert _runs(runner) == 1

    def test_an_ssh_blip_retries_and_then_passes(self, runner, jobs) -> None:
        r = runner.run(jobs, SMOKE_MODE="blip")
        assert r.returncode == 0, r.stdout + r.stderr
        assert _runs(runner) == 2

    def test_an_unreachable_box_is_a_warning_and_not_a_red(self, runner, jobs) -> None:
        """Fix round 1, P2: H-142's pull path is green with a warning. A runner
        that cannot reach the box must not turn a proved delivery red."""
        r = runner.run(jobs, PROBE="down")
        assert r.returncode == 0, r.stdout + r.stderr
        assert "::warning title=Chat smoke did not run::" in r.stdout
        assert _runs(runner) == 0

    def test_three_drops_are_a_warning_and_not_a_red(self, runner, jobs) -> None:
        r = runner.run(jobs, SMOKE_MODE="drop")
        assert r.returncode == 0, r.stdout + r.stderr
        assert "::warning title=Chat smoke did not run::" in r.stdout
        assert _runs(runner) == 3

    def test_exit_0_without_the_pass_line_is_red(self, runner, jobs) -> None:
        r = runner.run(jobs, SMOKE_MODE="nopass")
        assert r.returncode == 1, r.stdout + r.stderr
        assert _runs(runner) == 1

    def test_a_fail_line_then_a_drop_is_red_and_does_not_retry(self, runner, jobs) -> None:
        """Fix round 2: a drop after a FAIL line is not a blip."""
        r = runner.run(jobs, SMOKE_MODE="faildrop")
        assert r.returncode == 1, r.stdout + r.stderr
        assert "a step failed before ssh dropped" in r.stdout
        assert _runs(runner) == 1

    def test_a_fail_line_then_a_timeout_is_red(self, runner, jobs) -> None:
        """Fix round 3: the FAIL check comes BEFORE the timeout's no-verdict
        arm. A step that failed is a verdict, whatever ssh did next."""
        r = runner.run(jobs, SMOKE_MODE="failhang")
        assert r.returncode == 1, r.stdout + r.stderr
        assert "::warning" not in r.stdout
        assert _runs(runner) == 1

    def test_exit_1_with_no_fail_line_is_red(self, runner, jobs) -> None:
        """Fix round 3: the rc 1 arm on its own. The persist run timed out, so
        the script exits 1 and prints no FAIL step line."""
        r = runner.run(jobs, SMOKE_MODE="exit1")
        assert r.returncode == 1, r.stdout + r.stderr
        assert "Chat does not save on production" in r.stdout
        assert "::warning" not in r.stdout
        assert _runs(runner) == 1

    def test_a_timeout_has_its_own_label_and_no_retry(self, runner, jobs) -> None:
        r = runner.run(jobs, SMOKE_MODE="hang")
        assert r.returncode == 0, r.stdout + r.stderr
        assert "timeout, exit 124" in r.stdout
        assert "ssh dropped" not in r.stdout
        assert _runs(runner) == 1

    def test_an_auth_failure_is_red_without_a_retry(self, runner, jobs) -> None:
        r = runner.run(jobs, SMOKE_MODE="auth")
        assert r.returncode == 1, r.stdout + r.stderr
        assert _runs(runner) == 1


# ── 5. The sweep of leftover sessions (fix round 1) ─────────────────────────


def _smoke_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "smoke_chat_persist", ROOT / "scripts" / "smoke_chat_persist.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_sweep_deletes_only_old_sessions_and_is_bounded(monkeypatch, capsys) -> None:
    mod = _smoke_module()
    now = 1_800_000_000.0
    iso = lambda age: datetime.fromtimestamp(now - age, UTC).isoformat()  # noqa: E731
    # Fix round 2: shared rooms come FIRST, so without the owner filter they
    # would use up the budget of 5.
    # Fix round 3: a row with no isOwner field is not the member's to delete.
    rows = [{"id": "noflag", "updatedAt": iso(7200)}]
    rows += [{"id": f"shared{i}", "updatedAt": iso(7200), "isOwner": False} for i in range(5)]
    rows += [{"id": f"old{i}", "updatedAt": iso(7200), "isOwner": True} for i in range(7)]
    rows += [{"id": "new", "updatedAt": iso(60), "isOwner": True}]
    calls: list[tuple[str, str]] = []

    def fake_call(base, cookie, method, path, body=None):
        calls.append((method, path))
        return (200, rows) if method == "GET" else (204, None)

    monkeypatch.setattr(mod, "_call", fake_call)
    mod._sweep("https://x", "c", now=now)
    deleted = [p for m, p in calls if m == "DELETE"]
    assert len(deleted) == mod.SWEEP_MAX == 5
    assert all("/old" in p for p in deleted), deleted
    assert not any("noflag" in p or "shared" in p for p in deleted), deleted
    assert "ok   0 swept 5 old session(s), 2 left" in capsys.readouterr().out


def test_a_failed_sweep_never_fails_the_smoke(monkeypatch, capsys) -> None:
    mod = _smoke_module()

    def down(*_a, **_k):
        raise SystemExit(1)

    monkeypatch.setattr(mod, "_call", down)
    mod._sweep("https://x", "c")
    assert "WARN 0 sweep" in capsys.readouterr().out


def test_the_sweep_runs_after_the_identity_check_and_before_step_1() -> None:
    text = _read(ROOT / "scripts" / "smoke_chat_persist.py")
    main = text[text.index("def main()"):]
    assert main.index("REFUSED") < main.index("_sweep(base, cookie)") < main.index(
        "1 create session")


# ── 6. The box runs the smoke by itself (H-204) ─────────────────────────────
#
# When ssh from GitHub stays blocked, the `chat-smoke` job only warns. So
# acb-smoke-chat.timer runs the same script on the box every 6 hours, and
# vps-health.yml goes red when the unit is failed.

UNITS = ROOT / "deploy" / "hostinger"
SMOKE_SERVICE = UNITS / "acb-smoke-chat.service"
SMOKE_TIMER = UNITS / "acb-smoke-chat.timer"
APPLY = ROOT / "scripts" / "vps_apply.sh"
HEALTH = ROOT / ".github" / "workflows" / "vps-health.yml"


def _unit_keys(p: Path) -> dict[str, list[str]]:
    """Each `Key=value` line of a unit, without comments. A key can repeat."""
    out: dict[str, list[str]] = {}
    for line in _code_lines(_read(p)):
        s = line.strip()
        if s.startswith("[") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        out.setdefault(k.strip(), []).append(v.strip())
    return out


def test_the_box_unit_runs_the_smoke_as_acb() -> None:
    keys = _unit_keys(SMOKE_SERVICE)
    assert keys.get("User") == ["acb"], "the smoke reads acb's .env.local. Run it as acb."
    assert keys.get("Type") == ["oneshot"]
    assert keys.get("ExecStart") == ["/bin/bash /opt/acb/app/deploy/smoke_chat.sh"], keys
    # The unit's backstop sits above the script's own bound.
    worst = int(re.search(r"worst case\s+(\d+) s", _read(SCRIPT)).group(1))
    assert int(keys["TimeoutStartSec"][0]) > worst


def test_a_busy_lock_is_a_success_in_the_unit() -> None:
    """Exit 75 means a deploy held the lock and nothing was checked. It must
    not leave the unit failed. Exit 1 and exit 2 must."""
    keys = _unit_keys(SMOKE_SERVICE)
    codes = " ".join(keys.get("SuccessExitStatus", [])).split()
    assert codes == ["75"], f"SuccessExitStatus must be 75 alone, not {codes}"
    assert "Restart" not in keys, "a restart hides a failed smoke"


def test_the_timer_drives_the_unit_every_6_hours() -> None:
    keys = _unit_keys(SMOKE_TIMER)
    assert keys.get("Unit") == ["acb-smoke-chat.service"]
    assert keys.get("OnCalendar") == ["*-*-* 00/6:40:00"], keys.get("OnCalendar")
    assert keys.get("RandomizedDelaySec"), "spread the runs"
    assert keys.get("WantedBy") == ["timers.target"], "unenableable timer: no [Install]"


def test_the_deploy_installs_the_unit_and_enables_the_timer() -> None:
    """The unit rides vps_apply.sh's glob, which installs every unit file in
    deploy/hostinger and enables every timer. So the files must live there,
    and the loop must cover them."""
    assert SMOKE_SERVICE.parent == SMOKE_TIMER.parent == UNITS
    code = _code_lines(_read(APPLY))
    assert any('for unit in "$APP_DIR"/deploy/hostinger/*.service '
               '"$APP_DIR"/deploy/hostinger/*.timer' in ln for ln in code)
    assert any('for timer in "$APP_DIR"/deploy/hostinger/*.timer' in ln for ln in code)
    assert any("enable --now" in ln and '"$(basename "$timer")"' in ln for ln in code)


def _unit_sync_block() -> str:
    text = _read(APPLY)
    start = text.index('echo "==> Syncing systemd units')
    end = text.index('echo "==> Running infra health probe"')
    return text[start:end]


@needs_shell
def test_the_unit_sync_installs_and_enables_the_smoke_timer(tmp_path: Path) -> None:
    """Run vps_apply.sh's own unit-sync block against a fake sudo and a fake
    systemctl, over a copy of the repo's unit files."""
    appd, etc, binr = tmp_path / "app", tmp_path / "etc", tmp_path / "bin"
    (appd / "deploy" / "hostinger").mkdir(parents=True)
    etc.mkdir()
    binr.mkdir()
    for unit in list(UNITS.glob("*.service")) + list(UNITS.glob("*.timer")):
        shutil.copy(unit, appd / "deploy" / "hostinger" / unit.name)
    for name, body in (("sudo", 'exec "$@"\n'),
                       ("systemctl", 'printf "%s\\n" "$*" >> "$FAKE_LOG"\n')):
        (binr / name).write_text(f"#!/usr/bin/env bash\n{body}", encoding="utf-8", newline="\n")
        (binr / name).chmod(0o755)
    block = _unit_sync_block().replace("/etc/systemd/system", etc.as_posix())
    log = tmp_path / "systemctl.log"
    env = dict(os.environ, PATH=f"{binr}{os.pathsep}{os.environ.get('PATH', '')}",
               APP_DIR=appd.as_posix(), FAKE_LOG=log.as_posix())
    r = subprocess.run(["bash", "-c", block], env=env, capture_output=True, text=True,
                       encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
    for unit in (SMOKE_SERVICE, SMOKE_TIMER):
        assert (etc / unit.name).read_text(encoding="utf-8") == _read(unit)
    calls = log.read_text(encoding="utf-8").splitlines()
    assert "enable --now acb-smoke-chat.timer" in calls, calls
    assert "daemon-reload" in calls


def _health_step() -> str:
    jobs = yaml.safe_load(_read(HEALTH))["jobs"]
    job = jobs["chat-smoke-timer"]
    assert not job.get("continue-on-error")
    for s in job["steps"]:
        assert not s.get("continue-on-error"), "a check that cannot fail is a comment"
    return next(s["run"] for s in job["steps"] if "acb-smoke-chat" in s.get("run", ""))


def test_vps_health_reads_the_failed_state_of_the_unit() -> None:
    step = _health_step()
    assert "systemctl is-failed acb-smoke-chat.service" in step
    assert "systemctl is-active acb-smoke-chat.timer" in step
    assert '"${CI_SSH_OPTS[@]}"' in step and "ci_pin_host_key" in step
    assert "wait_for_ssh" in step


FAKE_STATE_SSH = r"""#!/usr/bin/env bash
last="${!#}"
if [ "$last" = "true" ]; then
  [ "${PROBE:-ok}" = down ] && { echo "ssh: connect to host 10.0.0.1 port 22: Connection timed out" >&2; exit 255; }
  exit 0
fi
cat > "$FAKE/remote.sh"
exit "${STATE_RC:-0}"
"""


@needs_shell
@pytest.mark.parametrize(("state_rc", "probe", "step_rc", "marker"), [
    ("0", "ok", 0, "OK      acb-smoke-chat.service is not failed"),
    ("10", "ok", 1, "::error title=Chat does not save::"),
    ("11", "ok", 1, "::error title=Chat smoke timer is off::"),
    ("255", "ok", 0, "::warning title=Chat smoke timer not read::"),
    ("0", "down", 0, "::warning title=Chat smoke timer not read::"),
])
def test_the_health_step_over_the_unit_states(tmp_path: Path, state_rc: str, probe: str,
                                               step_rc: int, marker: str) -> None:
    fake, binr, repo, home = (tmp_path / d for d in ("fake", "bin", "repo", "home"))
    for d in (fake, binr, repo / "scripts", repo / "deploy" / "hostinger", home):
        d.mkdir(parents=True)
    for name, body in (("ssh", FAKE_STATE_SSH), ("sleep", "#!/usr/bin/env bash\n:\n")):
        (binr / name).write_text(body, encoding="utf-8", newline="\n")
        (binr / name).chmod(0o755)
    for src, dst in ((LIB, repo / "scripts" / LIB.name),
                     (HOST_KEY_LIB, repo / "scripts" / HOST_KEY_LIB.name),
                     (PINNED, repo / "deploy" / "hostinger" / PINNED.name)):
        shutil.copy(src, dst)
    (repo / "step.sh").write_text(_health_step(), encoding="utf-8", newline="\n")
    env = dict(os.environ, PATH=f"{binr}{os.pathsep}{os.environ.get('PATH', '')}",
               FAKE=fake.as_posix(), HOME=home.as_posix(), SSH_HOST="10.0.0.1",
               SSH_USER="acb", SSH_PORT="22", SSH_KEY="k", CONNECT_BUDGET="30",
               STATE_RC=state_rc, PROBE=probe)
    r = subprocess.run(["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "step.sh"],
                       cwd=repo, env=env, capture_output=True, text=True, encoding="utf-8",
                       timeout=120)
    assert r.returncode == step_rc, r.stdout + r.stderr
    assert marker in r.stdout, r.stdout + r.stderr
    if probe == "ok":
        sent = (fake / "remote.sh").read_text(encoding="utf-8")
        assert "systemctl is-failed acb-smoke-chat.service" in sent
