"""WS-49 BH-2 — the strict check of `vps_apply.sh`, and the watchdog WARN.

Spec: project-docs/specs/box_hardening.md §5 BH-2 items 5 and 6, and
acceptance 5 and 6 (fix round 4, B3).

There is no test box, so every test here runs real bash against stubs:

- `systemctl show <unit> -p <Name> --value` answers from a props file.
- `sudo` runs the command, but maps each argument under `/etc/` into a fake
  root. The strict check reads the rollback through the REAL
  `scripts/bh2_rollback.sh status`, and that script reads the root-only ack
  through sudo. So the stub answers the read of `/etc/acb/bh2-rollback-ack`.
- `date -u +%s` answers a fake now, so the expiry is tested in time.

The strict check is the block between `# >>> bh2 helpers` and
`# <<< bh2 helpers` of `vps_apply.sh`, which holds definitions only. The tail
test runs the end of `vps_apply.sh` itself, from the strict check to the final
line, so a failure must stop the apply BEFORE the marker.

The watchdog tests run the whole `health-watchdog.sh`, with every service
answering "up". Only the BH-2 line changes between the cases.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
APPLY = ROOT / "scripts" / "vps_apply.sh"
WATCHDOG = ROOT / "deploy" / "hostinger" / "health-watchdog.sh"
ROLLBACK_CONF = ROOT / "deploy" / "hostinger" / "rollback" / "acb-gateway-90-bh2-off.conf"

DROPIN = "etc/systemd/system/acb-gateway.service.d/90-bh2-off.conf"
ACK = "etc/acb/bh2-rollback-ack"
NOW = 1_800_000_000
HOUR = 3600

SANDBOXED = {"ActiveState": "active", "NoNewPrivileges": "yes", "ProtectSystem": "strict"}
ROLLED_BACK = {"ActiveState": "active", "NoNewPrivileges": "no", "ProtectSystem": "no"}


def _bash() -> str | None:
    b = shutil.which("bash")
    if not b or "system32" in b.lower():
        return None
    return b


needs_bash = pytest.mark.skipif(_bash() is None or shutil.which("date") is None,
                                reason="needs a POSIX bash and GNU date")

STUB_SUDO = r"""#!/usr/bin/env bash
printf '%s\n' "$*" >> "$STUB_LOG.sudo"
args=()
for a in "$@"; do
  case "$a" in
    /etc/*) args+=("$FAKE_ROOT$a") ;;
    *) args+=("$a") ;;
  esac
done
exec "${args[@]}"
"""

STUB_DATE = r"""#!/usr/bin/env bash
if [ "$*" = "-u +%s" ]; then echo "$FAKE_NOW"; exit 0; fi
exec "$REAL_DATE" "$@"
"""

#: show <unit> -p <Name> --value. A name that the props file does not hold
#: answers an empty line, as systemd does for an unset property.
STUB_SYSTEMCTL = r"""#!/usr/bin/env bash
printf '%s\n' "$*" >> "$STUB_LOG.systemctl"
case "$1" in
  show)
    grep -m1 "^$4=" "$STUB_PROPS" | cut -d= -f2- ;;
  is-active)
    exit "${STUB_ACTIVE_RC:-0}" ;;
esac
exit 0
"""

STUB_OK = "#!/usr/bin/env bash\nexit 0\n"
#: The watchdog probes with `curl -w '%{http_code}'`. Every probe answers 200.
STUB_CURL = "#!/usr/bin/env bash\nprintf '200'\n"
STUB_GIT = "#!/usr/bin/env bash\necho 0123456789abcdef0123456789abcdef01234567\n"


class Box:
    def __init__(self, tmp_path: Path) -> None:
        self.tmp = tmp_path
        self.root = tmp_path / "root"
        self.root.mkdir()
        self.log = tmp_path / "log"
        self.props = tmp_path / "props"
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        for name, body in (("sudo", STUB_SUDO), ("date", STUB_DATE),
                           ("systemctl", STUB_SYSTEMCTL), ("curl", STUB_CURL),
                           ("sleep", STUB_OK), ("uv", STUB_OK), ("git", STUB_GIT)):
            self.stub(name, body)
        self.env = dict(
            os.environ,
            PATH=f"{self.bin}{os.pathsep}{os.environ.get('PATH', '')}",
            FAKE_ROOT=self.root.as_posix(),
            FAKE_NOW=str(NOW),
            REAL_DATE=shutil.which("date") or "date",
            STUB_LOG=self.log.as_posix(),
            STUB_PROPS=self.props.as_posix(),
            APP_DIR=ROOT.as_posix(),
        )
        self.set_props(SANDBOXED)

    def stub(self, name: str, body: str) -> None:
        p = self.bin / name
        p.write_text(body, encoding="utf-8", newline="\n")
        p.chmod(0o755)

    def set_props(self, props: dict[str, str]) -> None:
        self.props.write_text("".join(f"{k}={v}\n" for k, v in props.items()),
                              encoding="utf-8", newline="\n")

    def rollback(self, expires: int, *, conf: bool = True, ack: bool = True) -> None:
        """A rollback on the fake box, as `bh2_rollback.sh on` leaves it."""
        if conf:
            d = self.root / DROPIN
            d.parent.mkdir(parents=True, exist_ok=True)
            d.write_text(ROLLBACK_CONF.read_text(encoding="utf-8"), encoding="utf-8")
        if ack:
            a = self.root / ACK
            a.parent.mkdir(parents=True, exist_ok=True)
            a.write_text(f"EXPIRES_EPOCH={expires}\n", encoding="utf-8", newline="\n")

    def calls(self, tool: str) -> list[str]:
        p = Path(f"{self.log.as_posix()}.{tool}")
        return p.read_text(encoding="utf-8").splitlines() if p.exists() else []

    def bash(self, script: str, **extra: str) -> subprocess.CompletedProcess:
        return subprocess.run([_bash(), "-c", script], env=dict(self.env, **extra),
                              capture_output=True, text=True, encoding="utf-8",
                              timeout=120, stdin=subprocess.DEVNULL)


@pytest.fixture
def box(tmp_path: Path) -> Box:
    return Box(tmp_path)


def _block(begin: str, end: str) -> str:
    lines = APPLY.read_text(encoding="utf-8").splitlines()
    return "\n".join(lines[lines.index(begin): lines.index(end) + 1]) + "\n"


def _helpers(tmp_path: Path) -> Path:
    out = tmp_path / "bh2_helpers.sh"
    out.write_text(_block("# >>> bh2 helpers", "# <<< bh2 helpers"), encoding="utf-8",
                   newline="\n")
    return out


def _check(box: Box) -> subprocess.CompletedProcess:
    return box.bash(f'set -e; source "{_helpers(box.tmp).as_posix()}"; bh2_strict_check')


# ── The strict check (acceptance 5) ──────────────────────────────────────


@needs_bash
def test_it_passes_a_gateway_that_is_active_and_sandboxed(box: Box) -> None:
    r = _check(box)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "NoNewPrivileges=yes, ProtectSystem=strict" in r.stdout
    assert "WARN BH-2" not in r.stdout
    asked = [c for c in box.calls("systemctl") if c.startswith("show ")]
    assert asked == [f"show acb-gateway -p {p} --value"
                     for p in ("ActiveState", "NoNewPrivileges", "ProtectSystem")]


@needs_bash
@pytest.mark.parametrize("props", [
    {"NoNewPrivileges": "no"},
    {"NoNewPrivileges": ""},
    {"ProtectSystem": "full"},
    {"ProtectSystem": "yes"},
    {"ProtectSystem": "no"},
    {"ProtectSystem": ""},
    {"NoNewPrivileges": "no", "ProtectSystem": "no"},
])
def test_any_other_value_fails_with_no_rollback(box: Box, props: dict[str, str]) -> None:
    box.set_props({**SANDBOXED, **props})
    r = _check(box)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "no valid rollback" in r.stdout
    assert "WARN BH-2 rolled back" not in r.stdout


@needs_bash
@pytest.mark.parametrize("state", ["inactive", "failed", "activating", ""])
def test_a_gateway_that_is_not_active_fails_even_when_sandboxed(box: Box, state: str) -> None:
    box.set_props({**SANDBOXED, "ActiveState": state})
    r = _check(box)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "not active" in r.stdout


@needs_bash
def test_a_gateway_that_is_not_active_fails_even_with_a_valid_rollback(box: Box) -> None:
    box.rollback(NOW + 10 * HOUR)
    box.set_props({**ROLLED_BACK, "ActiveState": "inactive"})
    r = _check(box)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "not active" in r.stdout


@needs_bash
def test_a_valid_rollback_passes_and_prints_the_warn(box: Box) -> None:
    box.rollback(NOW + 10 * HOUR)
    box.set_props(ROLLED_BACK)
    r = _check(box)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "WARN BH-2 rolled back until" in r.stdout
    assert "a valid rollback holds it off" in r.stdout
    # The ack is root-only, so the read went through sudo (B2-3).
    assert "cat /etc/acb/bh2-rollback-ack" in box.calls("sudo")


@needs_bash
def test_a_valid_rollback_on_a_sandboxed_gateway_still_warns(box: Box) -> None:
    """While the rollback files are there, every run prints the WARN."""
    box.rollback(NOW + 10 * HOUR)
    r = _check(box)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "WARN BH-2 rolled back until" in r.stdout


@needs_bash
@pytest.mark.parametrize("expires", [NOW - 1, NOW, NOW - 72 * HOUR])
def test_a_rollback_with_a_date_in_the_past_fails(box: Box, expires: int) -> None:
    box.rollback(expires)
    box.set_props(ROLLED_BACK)
    r = _check(box)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "WARN BH-2 rolled back" not in r.stdout
    assert "gave 3" in r.stdout, r.stdout


@needs_bash
@pytest.mark.parametrize("half", ["conf", "ack"])
def test_a_half_rollback_fails(box: Box, half: str) -> None:
    box.rollback(NOW + 10 * HOUR, conf=half == "conf", ack=half == "ack")
    box.set_props(ROLLED_BACK)
    r = _check(box)
    assert r.returncode == 1, r.stdout + r.stderr


@needs_bash
def test_a_missing_rollback_script_is_no_rollback(box: Box) -> None:
    box.set_props(ROLLED_BACK)
    r = box.bash(f'set -e; source "{_helpers(box.tmp).as_posix()}"; bh2_strict_check',
                 BH2_ROLLBACK_SCRIPT=(box.tmp / "nope.sh").as_posix())
    assert r.returncode == 1, r.stdout + r.stderr


# ── The tail of vps_apply.sh: a failure stops the apply before the marker ─


def _tail() -> str:
    text = APPLY.read_text(encoding="utf-8")
    return text[text.index('echo "==> WS-49 BH-2: the strict check of the gateway sandbox"'):]


def _run_tail(box: Box) -> subprocess.CompletedProcess:
    marker = box.tmp / "marker"
    prelude = (
        f'set -e\nsource "{_helpers(box.tmp).as_posix()}"\n'
        f'record_applied_sha() {{ echo "$1" > "{marker.as_posix()}"; }}\n'
        'VPS_APPLY_SELF_SUM=x\n'
    )
    return box.bash(prelude + _tail())


@needs_bash
def test_the_apply_fails_before_the_marker_when_the_check_fails(box: Box) -> None:
    box.set_props({**SANDBOXED, "NoNewPrivileges": "no"})
    r = _run_tail(box)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "BH-2 STRICT CHECK FAILED" in r.stdout
    assert not (box.tmp / "marker").exists(), "a failed strict check wrote the marker"
    assert "Deployment complete" not in r.stdout


@needs_bash
def test_the_apply_reaches_the_marker_when_the_check_passes(box: Box) -> None:
    r = _run_tail(box)
    assert r.returncode == 0, r.stdout + r.stderr
    assert (box.tmp / "marker").is_file()
    assert r.stdout.rstrip().endswith("==> Deployment complete")


@needs_bash
def test_the_apply_reaches_the_marker_under_a_valid_rollback(box: Box) -> None:
    box.rollback(NOW + 10 * HOUR)
    box.set_props(ROLLED_BACK)
    r = _run_tail(box)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "WARN BH-2 rolled back until" in r.stdout
    assert (box.tmp / "marker").is_file()


def test_the_check_comment_names_the_retry_cost() -> None:
    """vps_pull.sh tries a sha at most 3 times, and each try restarts the
    gateway. The comment at the check must say so (spec item 5)."""
    head = _tail()[:2000]
    assert "MAX_FAILS" in head and "3 gateway restarts" in head


# ── ensure_gateway_rw_paths ──────────────────────────────────────────────


def _ensure(box: Box, app: Path, home: Path) -> subprocess.CompletedProcess:
    helpers = _helpers(box.tmp).as_posix()
    return box.bash(f'set -e; source "{helpers}"; '
                    f'ensure_gateway_rw_paths "{app.as_posix()}" "{home.as_posix()}"')


@needs_bash
def test_the_rw_paths_step_makes_each_path_but_env(box: Box) -> None:
    """Fix round 1, P3-1 and P3-2. data/ and the two JSON files are made when
    absent, with the shape the gateway reads. The "-" home dirs are made, so
    they bind on a fresh box. A present file is never touched."""
    app, home = box.tmp / "app", box.tmp / "home"
    (app / "infra").mkdir(parents=True)
    (app / ".env").write_text("A=1\n", encoding="utf-8")
    (app / "infra/provider_models_cache.json").write_text('{"kept": 1}\n', encoding="utf-8")
    home.mkdir()
    r = _ensure(box, app, home)
    assert r.returncode == 0, r.stdout + r.stderr
    assert (app / "data").is_dir()
    assert (app / "apps/services/gateway/agents.json").read_text(encoding="utf-8") == "[]\n"
    assert (app / "infra/provider_models_cache.json").read_text(encoding="utf-8") == '{"kept": 1}\n'
    for d in (".acb/agents", ".copilot", ".cache/copilot"):
        assert (home / d).is_dir(), d
    assert "made " in r.stdout and "agents.json as []" in r.stdout
    (app / "infra/provider_models_cache.json").unlink()
    r = _ensure(box, app, home)
    assert r.returncode == 0, r.stdout + r.stderr
    assert (app / "infra/provider_models_cache.json").read_text(encoding="utf-8") == "{}\n"
    r = _ensure(box, app, home)
    assert r.returncode == 0 and "made " not in r.stdout, r.stdout


@needs_bash
def test_the_rw_paths_step_refuses_a_missing_env(box: Box) -> None:
    app, home = box.tmp / "app", box.tmp / "home"
    app.mkdir()
    home.mkdir()
    r = _ensure(box, app, home)
    assert r.returncode == 1, r.stdout + r.stderr
    assert ".env is missing" in r.stdout


# ── compile_bytecode (fix round 1, P2-3) ─────────────────────────────────


def _compile(box: Box, app: Path) -> subprocess.CompletedProcess:
    helpers = _helpers(box.tmp).as_posix()
    return box.bash(f'set -e; source "{helpers}"; compile_bytecode "{app.as_posix()}"',
                    BH2_PYTHON=Path(sys.executable).as_posix())


@needs_bash
def test_the_deploy_compiles_the_bytecode_that_the_gateway_cannot_write(box: Box) -> None:
    app = box.tmp / "app"
    for rel in (".venv/lib/site/pkg.py", "apps/svc/mod.py", "packages/p/m.py"):
        (app / rel).parent.mkdir(parents=True, exist_ok=True)
        (app / rel).write_text("X = 1\n", encoding="utf-8")
    (app / "apps/svc/node_modules").mkdir()
    (app / "apps/svc/node_modules/skip.py").write_text("X = 1\n", encoding="utf-8")
    r = _compile(box, app)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "compiled the bytecode" in r.stdout
    for d in (".venv/lib/site", "apps/svc", "packages/p"):
        assert list((app / d / "__pycache__").glob("*.pyc")), d
    assert not (app / "apps/svc/node_modules/__pycache__").exists()


@needs_bash
def test_a_file_that_does_not_compile_does_not_stop_the_deploy(box: Box) -> None:
    app = box.tmp / "app"
    for rel in (".venv/lib", "apps", "packages"):
        (app / rel).mkdir(parents=True, exist_ok=True)
    (app / "apps/bad.py").write_text("def (:\n", encoding="utf-8")
    (app / "apps/good.py").write_text("X = 1\n", encoding="utf-8")
    r = _compile(box, app)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "non-fatal" in r.stdout
    assert list((app / "apps/__pycache__").glob("good*.pyc"))


def test_the_compile_runs_before_the_gateway_restart_in_timestamp_mode() -> None:
    lines = [ln for ln in APPLY.read_text(encoding="utf-8").splitlines()
             if not ln.lstrip().startswith("#")]
    comp = lines.index('compile_bytecode "$APP_DIR"')
    ensure = next(i for i, ln in enumerate(lines) if ln.startswith('ensure_gateway_rw_paths "$APP_DIR"'))
    restart = lines.index("sudo systemctl restart acb-gateway")
    assert comp < ensure < restart
    block = _block("# >>> bh2 helpers", "# <<< bh2 helpers")
    fn = block[block.index("compile_bytecode() {"):]
    assert "--invalidation-mode timestamp" in fn
    assert '"$app/.venv/lib" "$app/apps" "$app/packages"' in fn
    assert 'sudo -u "$owner"' in fn, "root must not leave root-owned __pycache__"


# ── The watchdog WARN (acceptance 6) ─────────────────────────────────────

STUB_STATUS = r"""#!/usr/bin/env bash
[ "$1" = "status" ] || exit 2
printf '%s\n' "$STUB_STATUS_OUT"
exit "$STUB_STATUS_RC"
"""


def _watchdog(box: Box, **extra: str) -> tuple[subprocess.CompletedProcess, list[str]]:
    logdir = box.tmp / "wdlog"
    env = dict(WATCHDOG_LOG_DIR=logdir.as_posix(), **extra)
    r = subprocess.run([_bash(), WATCHDOG.as_posix()], env=dict(box.env, **env),
                       capture_output=True, text=True, encoding="utf-8", timeout=120,
                       stdin=subprocess.DEVNULL)
    log = logdir / "health-watchdog.log"
    lines = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return r, lines


def _bh2_lines(lines: list[str]) -> list[str]:
    return [ln for ln in lines if "BH-2" in ln]


@needs_bash
@pytest.mark.parametrize(("rc", "out", "want"), [
    ("0", "BH-2 rollback: on, expires 2027-01-15T08:00:00Z\n"
          "WARN BH-2 rolled back until 2027-01-15T08:00:00Z",
     "WARN BH-2 rolled back until 2027-01-15T08:00:00Z"),
    ("1", "BH-2 rollback: off", None),
    ("3", "BH-2 rollback: EXPIRED (expired at 2027-01-15T08:00:00Z)",
     "WARN BH-2 rollback is not valid (status 3): BH-2 rollback: EXPIRED"),
])
def test_the_watchdog_logs_the_rollback_state(box: Box, rc: str, out: str,
                                              want: str | None) -> None:
    status = box.tmp / "bh2_status.sh"
    status.write_text(STUB_STATUS, encoding="utf-8", newline="\n")
    r, lines = _watchdog(box, BH2_ROLLBACK_SCRIPT=status.as_posix(),
                         STUB_STATUS_RC=rc, STUB_STATUS_OUT=out)
    assert r.returncode == 0, r.stdout + r.stderr
    got = _bh2_lines(lines)
    if want is None:
        assert got == [], got
    else:
        assert len(got) == 1, got
        assert want in got[0], got
    # The WARN is not a failure: every unit and probe is up.
    assert any("SUMMARY restarts=0 still_failing=0" in ln for ln in lines), lines


@needs_bash
def test_the_watchdog_reads_the_real_rollback_script_by_default(box: Box) -> None:
    """No override: the watchdog finds scripts/bh2_rollback.sh from its own
    path, and that script reads the fake root through the stub sudo."""
    r, lines = _watchdog(box)
    assert r.returncode == 0, r.stdout + r.stderr
    assert _bh2_lines(lines) == [], lines
    box.rollback(NOW + 10 * HOUR)
    r, lines = _watchdog(box)
    assert r.returncode == 0, r.stdout + r.stderr
    got = _bh2_lines(lines)
    assert len(got) == 1 and "WARN BH-2 rolled back until" in got[0], got


@needs_bash
def test_the_watchdog_warns_when_the_rollback_script_is_missing(box: Box) -> None:
    r, lines = _watchdog(box, BH2_ROLLBACK_SCRIPT=(box.tmp / "nope.sh").as_posix())
    assert r.returncode == 0, r.stdout + r.stderr
    got = _bh2_lines(lines)
    assert len(got) == 1 and "WARN BH-2 rollback is not valid" in got[0], got
