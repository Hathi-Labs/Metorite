"""BH-F3, part 1 — WS-49 BH-2, the narrowed PR (spec B2-5).

Spec: project-docs/specs/box_hardening.md §5 BH-2 "Fences" and §8.

Part 1 holds three things:

- the rollback drop-in resets the six lines that can stop the gateway start;
- `scripts/box_hardening_probe.sh` runs each probe of acceptance 2, and it
  prints no value that it reads (a canary proves that);
- every `User=acb` unit except acb-pull has `NoNewPrivileges=yes` in the unit
  or in its drop-in. This one lands with the drop-ins of the other units.

Part 2 (the full slice) adds the `50-hardening.conf` assertions, the write
allowlist and the strict check of `vps_apply.sh`. Not here.

The probe tests run the real script against a stub `systemctl`,
`systemd-run`, `sudo`, `docker`, `crontab`, `touch`, `ls` and `id` on PATH.
Each stub prints a canary. No canary may reach the probe's output.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PROBE = ROOT / "scripts" / "box_hardening_probe.sh"
ROLLBACK_CONF = ROOT / "deploy" / "hostinger" / "rollback" / "acb-gateway-90-bh2-off.conf"
UNITS = ROOT / "deploy" / "hostinger"

CANARY = "CANARY_BH2_s3cr3t_9f1c"
LOWER_CANARY = "canary-bh2-lower-value"

#: The six lines that can each stop the start (spec BH-2 item 6, BH-F3).
SIX_RESETS = (
    "ReadWritePaths=",
    "InaccessiblePaths=",
    "ProtectSystem=no",
    "ProtectHome=no",
    "NoNewPrivileges=no",
    "PrivateTmp=no",
)

#: The checks that the transient unit answers, one per probe of acceptance 2,
#: plus two that the spec's intent needs (`/run/user` itself, and `/etc/acb`).
INNER_CHECKS = (
    "p1-sudo-n-true-fails",
    "p3-touch-scripts-erofs",
    "p3-touch-deploy-hostinger-erofs",
    "p5-touch-git-hooks-fails",
    "p5-touch-venv-fails",
    "p5-touch-t2-vendor-fails",
    "env-still-writable",
    "p4-ls-run-user-uid-fails",
    "p4-run-user-hidden",
    "p2-docker-ps-fails",
    "setgid-crontab-l-fails",
    "etc-acb-hidden",
)

#: A box where the BH-2 50-hardening.conf is live: every property passes.
GOOD_PROPS = {
    "ActiveState": "active",
    "User": "acb",
    "NoNewPrivileges": "yes",
    "PrivateTmp": "yes",
    "ProtectSystem": "strict",
    "ProtectHome": "read-only",
    "RestrictSUIDSGID": "yes",
    "ProtectProc": "invisible",
    "CapabilityBoundingSet": "",
    "AmbientCapabilities": "",
    "LockPersonality": "yes",
    "ProtectKernelTunables": "yes",
    "ProtectKernelModules": "yes",
    "ProtectKernelLogs": "yes",
    "ProtectControlGroups": "yes",
    "ProtectClock": "yes",
    "ProtectHostname": "yes",
    "RestrictRealtime": "yes",
    "InaccessiblePaths": "-/run/user -/run/docker.sock -/var/run/docker.sock -/etc/acb -/etc/sudoers.d",
    "ReadWritePaths": (
        "/opt/acb/app/.env /opt/acb/app/data /opt/acb/app/infra/provider_models_cache.json "
        "/opt/acb/app/apps/services/gateway/agents.json -/home/acb/.acb -/home/acb/.copilot"
    ),
    "ReadOnlyPaths": "",
    "SupplementaryGroups": "",
}


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _conf_lines(p: Path) -> list[str]:
    return [
        ln.strip() for ln in _read(p).splitlines()
        if ln.strip() and not ln.strip().startswith(("#", ";"))
    ]


# ── The rollback drop-in ─────────────────────────────────────────────────


def test_the_rollback_conf_resets_the_six_start_blocking_lines() -> None:
    lines = _conf_lines(ROLLBACK_CONF)
    assert lines[0] == "[Service]", lines[:1]
    for want in SIX_RESETS:
        assert want in lines, f"the BH-2 rollback no longer resets {want!r}"


def test_the_rollback_conf_is_not_on_the_drop_in_install_path() -> None:
    """The BH-7 installer reads `deploy/hostinger/*.service.d/*.conf`. The
    rollback must never be on that glob, or every deploy rolls BH-2 back."""
    assert ROLLBACK_CONF.parent.name == "rollback"
    assert not ROLLBACK_CONF.parent.name.endswith(".service.d")
    installed = [p.name for p in UNITS.glob("*.service.d/*.conf")]
    assert ROLLBACK_CONF.name not in installed
    assert "90-bh2-off.conf" not in installed


def test_the_rollback_conf_sets_no_exec_or_env_line() -> None:
    """It resets sandbox lines only. An ExecStart= or Environment= line here
    would change what runs, not how it is boxed."""
    for ln in _conf_lines(ROLLBACK_CONF):
        key = ln.split("=", 1)[0]
        assert not key.startswith(("Exec", "Environment", "User", "Group")), ln


# ── The probe: what it runs ──────────────────────────────────────────────


def _probe_code() -> str:
    return "\n".join(
        ln for ln in _read(PROBE).splitlines() if not ln.lstrip().startswith("#")
    )


@pytest.mark.parametrize("needle", [
    "sudo -n true",
    '"$APP/scripts"',
    '"$APP/deploy/hostinger"',
    "Read-only file system",
    '"$APP/.git/hooks"',
    '"$APP/.venv"',
    "/opt/acb/t2-vendor",
    'test -w "$APP/.env"',
    '"/run/user/$(id -u)"',
    "docker ps",
    "crontab -l",
    "systemd-run",
    "--uid=",
    "systemctl show",
])
def test_the_probe_runs_each_probe_of_acceptance_2(needle: str) -> None:
    assert needle in _probe_code(), f"the probe no longer runs {needle!r}"


def test_the_probe_knows_each_inner_check_by_name() -> None:
    code = _probe_code()
    for name in INNER_CHECKS:
        assert re.search(rf"\b{re.escape(name)}\b", code), name
    assert f"INNER_CHECKS={len(INNER_CHECKS)}" in code


def test_the_transient_unit_loads_no_environment_file() -> None:
    code = _probe_code()
    assert "EnvironmentFile" not in code
    assert "COPY_PROPS=" in code


# ── The probe: behaviour under stubs ─────────────────────────────────────


def _bash() -> str | None:
    b = shutil.which("bash")
    if not b or "system32" in b.lower():
        return None
    return b


needs_bash = pytest.mark.skipif(_bash() is None, reason="needs a POSIX bash")

STUB_SYSTEMCTL = r"""#!/usr/bin/env bash
# systemctl show <unit> -p <Name> --value
if [ "$1" = "show" ]; then
  grep -m1 "^$4=" "$STUB_PROPS" | cut -d= -f2-
  echo "$STUB_CANARY" >&2
fi
exit 0
"""

STUB_SYSTEMD_RUN = r"""#!/usr/bin/env bash
printf '%s\n' "$@" > "$STUB_RUN_ARGV"
echo "$STUB_CANARY on stdout"
echo "$STUB_CANARY on stderr" >&2
case "$STUB_RUN_MODE" in
  fail) exit 1 ;;
  allpass)
    for n in $STUB_PASS_NAMES; do echo "PASS $n"; done
    echo "PASS $STUB_LOWER_CANARY"
    echo "FAIL $STUB_LOWER_CANARY"
    echo "BH2-PROBE-END"
    exit 0 ;;
esac
while [ "$#" -gt 0 ] && [ "$1" != "--" ]; do shift; done
shift
exec "$@"
"""

#: Each of these answers "it worked" and prints the canary. So every probe
#: that uses one FAILs, and a leak would show the canary.
LEAKY = r"""#!/usr/bin/env bash
echo "$STUB_CANARY $0 $*"
echo "$STUB_CANARY $0 $*" >&2
exit 0
"""

STUB_TOUCH = r"""#!/usr/bin/env bash
echo "touch: $STUB_CANARY: No such file or directory" >&2
exit 1
"""

STUB_ID = r"""#!/usr/bin/env bash
echo 0
"""


def _stub_env(tmp_path: Path, props: dict[str, str], mode: str) -> dict[str, str]:
    binr = tmp_path / "bin"
    binr.mkdir()
    stubs = {
        "systemctl": STUB_SYSTEMCTL,
        "systemd-run": STUB_SYSTEMD_RUN,
        "sudo": LEAKY,
        "docker": LEAKY,
        "crontab": LEAKY,
        "ls": LEAKY,
        "touch": STUB_TOUCH,
        "id": STUB_ID,
    }
    for name, body in stubs.items():
        (binr / name).write_text(body, encoding="utf-8", newline="\n")
        (binr / name).chmod(0o755)
    pf = tmp_path / "props"
    pf.write_text("".join(f"{k}={v}\n" for k, v in props.items()), encoding="utf-8", newline="\n")
    return dict(
        os.environ,
        PATH=f"{binr}{os.pathsep}{os.environ.get('PATH', '')}",
        STUB_PROPS=pf.as_posix(),
        STUB_CANARY=CANARY,
        STUB_LOWER_CANARY=LOWER_CANARY,
        STUB_RUN_MODE=mode,
        STUB_RUN_ARGV=(tmp_path / "run_argv").as_posix(),
        STUB_PASS_NAMES=" ".join(INNER_CHECKS),
    )


def _probe(tmp_path: Path, props: dict[str, str], mode: str) -> subprocess.CompletedProcess:
    env = _stub_env(tmp_path, props, mode)
    return subprocess.run(
        [_bash(), PROBE.as_posix(), "acb-gateway"], env=env, capture_output=True,
        text=True, encoding="utf-8", timeout=60, stdin=subprocess.DEVNULL,
    )


_LINE = re.compile(r"^(PASS|FAIL) [A-Za-z0-9_-]+$|^SUMMARY acb-gateway: \d+ PASS, \d+ FAIL$")


def _assert_no_value(r: subprocess.CompletedProcess) -> None:
    both = r.stdout + r.stderr
    assert CANARY not in both, both
    assert LOWER_CANARY not in both, both
    for ln in r.stdout.splitlines():
        assert _LINE.match(ln), f"the probe printed a line that is not a verdict: {ln!r}"


@needs_bash
def test_the_probe_prints_no_value_and_fails_on_a_leaky_box(tmp_path: Path) -> None:
    """The canary test. Each property holds the canary, and each stub prints
    it. The probe runs its real inner script, and reports FAILs only by name."""
    props = {k: f"{CANARY}-{k}" for k in GOOD_PROPS}
    r = _probe(tmp_path, props, "run")
    _assert_no_value(r)
    assert r.returncode == 1, r.stdout
    for name in ("p1-sudo-n-true-fails", "p2-docker-ps-fails", "setgid-crontab-l-fails",
                 "p4-run-user-hidden", "etc-acb-hidden", "prop-NoNewPrivileges",
                 "hidden-run-user"):
        assert f"FAIL {name}" in r.stdout, r.stdout
    # The stub touch fails with no EROFS, so the strict EROFS probes fail.
    assert "FAIL p3-touch-scripts-erofs" in r.stdout
    # The transient unit ran every probe, so this one does not fire.
    assert "transient-unit-ran-every-probe" not in r.stdout


@needs_bash
def test_the_probe_passes_a_hardened_box_and_drops_unknown_lines(tmp_path: Path) -> None:
    r = _probe(tmp_path, dict(GOOD_PROPS), "allpass")
    _assert_no_value(r)
    assert r.returncode == 0, r.stdout
    assert not re.search(r"^FAIL ", r.stdout, re.M), r.stdout
    assert r.stdout.rstrip().endswith(" PASS, 0 FAIL"), r.stdout
    for name in INNER_CHECKS:
        assert f"PASS {name}" in r.stdout


@needs_bash
def test_the_transient_unit_gets_the_unit_sandbox_and_user(tmp_path: Path) -> None:
    _probe(tmp_path, dict(GOOD_PROPS), "allpass")
    argv = (tmp_path / "run_argv").read_text(encoding="utf-8").splitlines()
    assert "--uid=acb" in argv
    assert "--wait" in argv and "--pipe" in argv
    pairs = {argv[i + 1] for i, a in enumerate(argv) if a == "-p"}
    for want in ("NoNewPrivileges=yes", "ProtectSystem=strict", "ProtectHome=read-only",
                 "RestrictSUIDSGID=yes", "CapabilityBoundingSet=",
                 "InaccessiblePaths=" + GOOD_PROPS["InaccessiblePaths"]):
        assert want in pairs, (want, pairs)
    assert not any(p.startswith(("EnvironmentFile", "Environment=")) for p in pairs)


@needs_bash
@pytest.mark.parametrize(("prop", "value", "check"), [
    ("ActiveState", "inactive", "prop-ActiveState"),
    ("NoNewPrivileges", "no", "prop-NoNewPrivileges"),
    ("ProtectSystem", "full", "prop-ProtectSystem"),
    ("ProtectHome", "no", "prop-ProtectHome"),
    ("InaccessiblePaths", "-/run/user -/etc/acb", "hidden-run-docker-sock"),
    ("ReadWritePaths", "/opt/acb/app/.env /opt/acb/app/.venv", "not-writable-opt-acb-app-venv"),
    ("ReadWritePaths", "/opt/acb/app", "not-writable-opt-acb-app-scripts"),
    ("ReadWritePaths", "-/opt/acb/t2-vendor/", "not-writable-opt-acb-t2-vendor"),
])
def test_the_probe_fails_one_bad_property(tmp_path: Path, prop: str, value: str,
                                          check: str) -> None:
    props = dict(GOOD_PROPS)
    props[prop] = value
    r = _probe(tmp_path, props, "allpass")
    _assert_no_value(r)
    assert r.returncode == 1, r.stdout
    assert f"FAIL {check}" in r.stdout, r.stdout


@needs_bash
def test_the_probe_fails_when_the_transient_unit_does_not_run(tmp_path: Path) -> None:
    r = _probe(tmp_path, dict(GOOD_PROPS), "fail")
    _assert_no_value(r)
    assert r.returncode == 1
    assert "FAIL transient-unit-ran-every-probe" in r.stdout


@needs_bash
def test_the_probe_refuses_a_unit_name_that_is_not_a_name(tmp_path: Path) -> None:
    env = _stub_env(tmp_path, dict(GOOD_PROPS), "allpass")
    r = subprocess.run([_bash(), PROBE.as_posix(), "acb;id"], env=env, capture_output=True,
                       text=True, encoding="utf-8", timeout=30, stdin=subprocess.DEVNULL)
    assert r.returncode == 2


def test_the_new_scripts_start_with_a_bash_shebang() -> None:
    """The spec runs both scripts as `bash <path>`, so no exec bit is needed."""
    for p in (PROBE, ROOT / "scripts" / "bh2_rollback.sh"):
        assert _read(p).startswith("#!/usr/bin/env bash\n"), p
