"""BH-F3, part 1 — WS-49 BH-2, the narrowed PR (spec B2-5).

Spec: project-docs/specs/box_hardening.md §5 BH-2 "Fences" and §8.

Part 1, in this PR, holds two things:

- the rollback drop-in resets the six lines that can stop the gateway start;
- `scripts/box_hardening_probe.sh` runs each probe of acceptance 2, and it
  prints no value that it reads (a canary proves that).

The third check of part 1 landed with BH-7, beside the drop-ins of the other
units (spec B2-5): every `User=acb` unit except acb-pull has
`NoNewPrivileges=yes` in the unit or in its drop-in. BH-7 also adds its own
checks here: `40-agent-site.conf`, and that the drop-in installer of
`vps_apply.sh` never deletes or writes a `90-*` file.

Part 2 (the full slice, fix round 4) is at the end of this file. It holds
the `50-hardening.conf` assertions, the write allowlist, the rollback that
resets each sandbox line, the place of the strict check in `vps_apply.sh`,
and the Operator Console unit. `tests/unit/test_bh2_strict_check.py` runs
the strict check itself.

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
  hang) exec sleep 30 ;;
  runtime-max)
    echo "Finished with result: timeout" >&2
    exit 1 ;;
  allpass-skip-t2)
    for n in $STUB_PASS_NAMES; do
      if [ "$n" = p5-touch-t2-vendor-fails ]; then echo "SKIP $n"; else echo "PASS $n"; fi
    done
    echo "BH2-PROBE-END"
    exit 0 ;;
  skip-other)
    for n in $STUB_PASS_NAMES; do
      if [ "$n" = p1-sudo-n-true-fails ]; then echo "SKIP $n"; else echo "PASS $n"; fi
    done
    echo "BH2-PROBE-END"
    exit 0 ;;
  allpass)
    for n in $STUB_PASS_NAMES; do echo "PASS $n"; done
    echo "PASS $STUB_LOWER_CANARY"
    echo "FAIL $STUB_LOWER_CANARY"
    echo "BH2-PROBE-END"
    exit 0 ;;
  allpass-rc1)
    for n in $STUB_PASS_NAMES; do echo "PASS $n"; done
    echo "BH2-PROBE-END"
    exit 1 ;;
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


def _stub_env(tmp_path: Path, props: dict[str, str], mode: str,
              **override: str) -> dict[str, str]:
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
    stubs.update(override)
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


def _probe(tmp_path: Path, props: dict[str, str], mode: str,
           env_extra: dict[str, str] | None = None,
           **override: str) -> subprocess.CompletedProcess:
    env = _stub_env(tmp_path, props, mode, **override)
    env.update(env_extra or {})
    return subprocess.run(
        [_bash(), PROBE.as_posix(), "acb-gateway"], env=env, capture_output=True,
        text=True, encoding="utf-8", timeout=60, stdin=subprocess.DEVNULL,
    )


_LINE = re.compile(
    r"^(PASS|FAIL|SKIP) [A-Za-z0-9_-]+$|^SUMMARY acb-gateway: \d+ PASS, \d+ SKIP, \d+ FAIL$"
)


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
@pytest.mark.parametrize(("answer", "verdict"), [
    ("no crontab for acb", "FAIL"),
    ("crontabs/acb/: fopen: Permission denied", "PASS"),
])
def test_no_crontab_means_setgid_worked(tmp_path: Path, answer: str, verdict: str) -> None:
    """`crontab -l` exits 1 with "no crontab for acb" when setgid WORKED and
    the user has no crontab. That answer must not count as a PASS."""
    stub = f'#!/usr/bin/env bash\necho "{answer}" >&2\nexit 1\n'
    r = _probe(tmp_path, dict(GOOD_PROPS), "run", crontab=stub)
    _assert_no_value(r)
    assert f"{verdict} setgid-crontab-l-fails" in r.stdout, r.stdout


@needs_bash
def test_the_probe_passes_a_hardened_box_and_drops_unknown_lines(tmp_path: Path) -> None:
    r = _probe(tmp_path, dict(GOOD_PROPS), "allpass")
    _assert_no_value(r)
    assert r.returncode == 0, r.stdout
    assert not re.search(r"^FAIL ", r.stdout, re.M), r.stdout
    assert r.stdout.rstrip().endswith(" PASS, 0 SKIP, 0 FAIL"), r.stdout
    for name in INNER_CHECKS:
        assert f"PASS {name}" in r.stdout


@needs_bash
def test_only_the_t2_vendor_check_may_skip(tmp_path: Path) -> None:
    """Before BH-7, /opt/acb/t2-vendor does not exist, so its check SKIPs. A
    SKIP is not a FAIL."""
    r = _probe(tmp_path, dict(GOOD_PROPS), "allpass-skip-t2")
    _assert_no_value(r)
    assert "SKIP p5-touch-t2-vendor-fails" in r.stdout
    assert r.returncode == 0, r.stdout
    assert r.stdout.rstrip().endswith(" PASS, 1 SKIP, 0 FAIL"), r.stdout


@needs_bash
def test_a_skip_of_any_other_check_is_dropped(tmp_path: Path) -> None:
    """A SKIP for any other check is dropped. Then the transient unit has not
    answered every probe, and the probe FAILs."""
    r = _probe(tmp_path, dict(GOOD_PROPS), "skip-other")
    _assert_no_value(r)
    assert "p1-sudo-n-true-fails" not in r.stdout
    assert "FAIL transient-unit-ran-every-probe" in r.stdout
    assert r.returncode == 1, r.stdout


@needs_bash
def test_the_real_inner_script_skips_a_missing_t2_vendor_dir(tmp_path: Path) -> None:
    if Path("/opt/acb/t2-vendor").exists():
        pytest.skip("this host has /opt/acb/t2-vendor")
    r = _probe(tmp_path, dict(GOOD_PROPS), "run")
    assert "SKIP p5-touch-t2-vendor-fails" in r.stdout, r.stdout


HANG = "#!/usr/bin/env bash\nexec sleep 30\n"


@needs_bash
def test_a_hanging_docker_is_a_timeout_fail(tmp_path: Path) -> None:
    """Review fix round 1, P2. `docker ps` that never returns must not hang
    the probe, and must not read as "docker ps fails" (a PASS)."""
    r = _probe(tmp_path, dict(GOOD_PROPS), "run", {"BH2_PROBE_STEP_TIMEOUT": "1"},
               docker=HANG, crontab=HANG)
    _assert_no_value(r)
    assert r.returncode == 1, r.stdout
    assert "FAIL p2-docker-ps-fails" in r.stdout, r.stdout
    assert "FAIL setgid-crontab-l-fails" in r.stdout, r.stdout
    # Two steps timed out, and the probe says so once.
    assert r.stdout.count("FAIL probe-timeout") == 1, r.stdout
    assert "transient-unit-ran-every-probe" not in r.stdout


@needs_bash
@pytest.mark.parametrize("tool", ["sudo", "crontab"])
def test_a_hanging_sudo_or_crontab_is_a_timeout_fail(tmp_path: Path, tool: str) -> None:
    r = _probe(tmp_path, dict(GOOD_PROPS), "run", {"BH2_PROBE_STEP_TIMEOUT": "1"},
               **{tool: HANG})
    _assert_no_value(r)
    assert r.returncode == 1, r.stdout
    assert "FAIL probe-timeout" in r.stdout, r.stdout


@needs_bash
def test_a_hanging_transient_unit_is_a_timeout_fail(tmp_path: Path) -> None:
    r = _probe(tmp_path, dict(GOOD_PROPS), "hang", {"BH2_PROBE_RUN_TIMEOUT": "2"})
    _assert_no_value(r)
    assert r.returncode == 1, r.stdout
    assert "FAIL probe-timeout" in r.stdout, r.stdout
    assert "FAIL transient-unit-ran-every-probe" in r.stdout


@needs_bash
def test_runtime_max_sec_ending_the_unit_is_a_timeout_fail(tmp_path: Path) -> None:
    r = _probe(tmp_path, dict(GOOD_PROPS), "runtime-max")
    _assert_no_value(r)
    assert r.returncode == 1, r.stdout
    assert "FAIL probe-timeout" in r.stdout, r.stdout


@needs_bash
def test_the_transient_unit_gets_the_unit_sandbox_and_user(tmp_path: Path) -> None:
    _probe(tmp_path, dict(GOOD_PROPS), "allpass")
    argv = (tmp_path / "run_argv").read_text(encoding="utf-8").splitlines()
    assert "--uid=acb" in argv
    assert "--wait" in argv and "--pipe" in argv
    assert argv[argv.index("--") + 1:argv.index("--") + 3] == ["/bin/bash", "-c"]
    # The stub logs one argv item per line, so the script's first line stands alone.
    assert argv[argv.index("--") + 3] == "STEP_T=10"
    pairs = {argv[i + 1] for i, a in enumerate(argv) if a == "-p"}
    for want in ("RuntimeMaxSec=60", "NoNewPrivileges=yes", "ProtectSystem=strict",
                 "ProtectHome=read-only",
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
def test_the_probe_fails_when_the_transient_unit_exits_non_zero(tmp_path: Path) -> None:
    """Every probe answered PASS, but systemd-run itself failed. The run is
    not proof, so the probe does not give a full PASS."""
    r = _probe(tmp_path, dict(GOOD_PROPS), "allpass-rc1")
    _assert_no_value(r)
    assert r.returncode == 1, r.stdout
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


# ── NoNewPrivileges on every acb unit (lands with the other drop-ins) ────
#
# The drop-ins of the other units landed with the BH-7 drop-in installer
# (spec B2-5), which installs them. These tests read the repo, never the box.

#: The four other units of BH-2 item 3. Each gets these three lines and no
#: more in this slice.
OTHER_UNITS = ("acb-workbench", "acb-customer-console", "acb-smoke-chat", "acb-whatsapp-bridge",
               "acb-operator-console")
THREE_LINES = ["[Service]", "NoNewPrivileges=yes", "PrivateTmp=yes", "RestrictSUIDSGID=yes"]

#: The User=acb units with no NoNewPrivileges, each with its reason. The
#: gateway left this list with BH-F3 part 2: its 50-hardening.conf sets it.
NNP_EXEMPT = {
    "acb-pull.service": "it runs vps_apply.sh, which needs sudo until BH-5",
}


def _acb_units() -> list[Path]:
    return [
        u for u in sorted(UNITS.glob("*.service"))
        if "User=acb" in [ln.strip() for ln in _read(u).splitlines()]
    ]


def _last_value(unit: Path, key: str) -> str | None:
    """The value systemd uses: the unit, then its drop-ins in name order."""
    value = None
    files = [unit, *sorted((UNITS / f"{unit.name}.d").glob("*.conf"))]
    for f in files:
        for ln in _conf_lines(f):
            if ln.startswith(f"{key}="):
                value = ln.split("=", 1)[1]
    return value


def test_every_acb_unit_but_pull_has_no_new_privileges() -> None:
    units = _acb_units()
    assert {u.name for u in units} >= {f"{n}.service" for n in OTHER_UNITS}
    missing = [
        u.name for u in units
        if u.name not in NNP_EXEMPT and _last_value(u, "NoNewPrivileges") != "yes"
    ]
    assert not missing, f"User=acb units with no NoNewPrivileges=yes: {missing}"


@pytest.mark.parametrize("unit", OTHER_UNITS)
def test_each_other_unit_gets_the_three_lines_and_no_more(unit: str) -> None:
    conf = UNITS / f"{unit}.service.d" / "50-hardening.conf"
    assert _conf_lines(conf) == THREE_LINES


def test_the_gateway_exemption_ends_with_part_2() -> None:
    """When the full slice adds the gateway's 50-hardening.conf, delete the
    acb-gateway entry of NNP_EXEMPT. This test fails until someone does."""
    gw = UNITS / "acb-gateway.service.d" / "50-hardening.conf"
    assert not gw.exists() or "acb-gateway.service" not in NNP_EXEMPT, (
        "50-hardening.conf is here: remove acb-gateway.service from NNP_EXEMPT"
    )


# ── The BH-7 drop-in installer and 40-agent-site.conf ──────────────────
#
# BH-7 owns the installer (spec Q3c). It installs each
# deploy/hostinger/*.service.d/*.conf before the first restart of the apply,
# writes only the repo names, and never deletes or writes a 90-* file, which
# is the BH-2 rollback on the box.

APPLY = ROOT / "scripts" / "vps_apply.sh"
AGENT_SITE_CONF = UNITS / "acb-gateway.service.d" / "40-agent-site.conf"
AGENT_SITE_LINES = [
    "[Service]",
    "StateDirectory=acb-gateway",
    "CacheDirectory=acb-gateway",
    "Environment=UV_CACHE_DIR=/var/cache/acb-gateway/uv",
    "Environment=CUSTOM_APPS_T2_VENDOR_DIR=/opt/acb/t2-vendor",
]


def test_the_agent_site_conf_holds_the_spec_lines_and_no_more() -> None:
    assert _conf_lines(AGENT_SITE_CONF) == AGENT_SITE_LINES


def test_each_drop_in_dir_names_a_unit_of_the_repo() -> None:
    """A typo in a dir name would install a drop-in that no unit reads."""
    dirs = sorted(UNITS.glob("*.service.d"))
    assert dirs
    for d in dirs:
        assert (UNITS / d.name[: -len(".d")]).is_file(), d.name
        assert sorted(p.name for p in d.iterdir()) == sorted(p.name for p in d.glob("*.conf")), d
        assert not [p.name for p in d.glob("90-*")], f"{d.name}: a 90-* name is the box rollback"


def _apply_block(begin: str, end: str) -> list[str]:
    lines = _read(APPLY).splitlines()
    return lines[lines.index(begin): lines.index(end) + 1]


def _function(name: str) -> str:
    block = _apply_block("# >>> bh7 helpers", "# <<< bh7 helpers")
    start = next(i for i, ln in enumerate(block) if ln.startswith(f"{name}() {{"))
    end = next(i for i, ln in enumerate(block) if i > start and ln == "}")
    return "\n".join(ln for ln in block[start:end + 1] if not ln.strip().startswith("#"))


def test_the_installer_never_deletes_and_never_writes_a_90_name() -> None:
    fn = _function("install_dropins")
    assert "rm " not in fn and "rmdir" not in fn and "unlink" not in fn
    assert "90-*)" in fn and "continue" in fn
    assert "sudo systemctl daemon-reload" in fn


INSTALL_STUB_SUDO = '#!/usr/bin/env bash\nexec "$@"\n'
#: The stub answers `show -p ActiveEnterTimestamp` like systemd. With
#: --timestamp=unix it prints "@<epoch>". With no such flag it prints the local
#: form with a zone name (AEST) that `date -d` cannot parse (fix round 1, B).
INSTALL_STUB_SYSTEMCTL = r"""#!/usr/bin/env bash
echo "$*" >> "$STUB_LOG"
case "$1" in
  is-active) grep -qx "$3" "$STUB_ACTIVE" ;;
  show)
    row="$(grep -m1 "^$2=" "$STUB_SINCE" | cut -d= -f2-)"
    case " $* " in
      *" --timestamp=unix "*) echo "@${row%%|*}" ;;
      *) echo "${row#*|}" ;;
    esac ;;
  *) exit 0 ;;
esac
"""


def _since(tmp_path: Path, started: dict[str, str]) -> None:
    """Write when each unit started: `unit=<epoch>|<local form with AEST>`."""
    import calendar
    import time

    rows = []
    for unit, utc in started.items():
        epoch = calendar.timegm(time.strptime(utc, "%Y-%m-%d %H:%M:%S"))
        local = time.strftime("%a %Y-%m-%d %H:%M:%S AEST", time.gmtime(epoch + 10 * 3600))
        rows.append(f"{unit}={epoch}|{local}")
    (tmp_path / "since").write_text("\n".join(rows) + "\n", encoding="utf-8", newline="\n")


def _installer_env(tmp_path: Path) -> dict[str, str]:
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    for name, body in (("sudo", INSTALL_STUB_SUDO), ("systemctl", INSTALL_STUB_SYSTEMCTL)):
        p = stubs / name
        p.write_text(body, encoding="utf-8", newline="\n")
        p.chmod(0o755)
    for name in ("log", "active", "since"):
        (tmp_path / name).write_text("", encoding="utf-8")
    env = dict(os.environ)
    env.update({
        "PATH": f"{stubs.as_posix()}{os.pathsep}{env.get('PATH', '')}",
        "SYSTEMD_UNIT_DIR": (tmp_path / "etc").as_posix(),
        "STUB_LOG": (tmp_path / "log").as_posix(),
        "STUB_ACTIVE": (tmp_path / "active").as_posix(),
        "STUB_SINCE": (tmp_path / "since").as_posix(),
    })
    return env


def _helpers_file(tmp_path: Path) -> Path:
    out = tmp_path / "bh7_helpers.sh"
    out.write_text("\n".join(_apply_block("# >>> bh7 helpers", "# <<< bh7 helpers")) + "\n",
                   encoding="utf-8", newline="\n")
    return out


def _run_helpers(tmp_path: Path, env: dict[str, str], body: str) -> subprocess.CompletedProcess:
    script = f'set -e; source "{_helpers_file(tmp_path).as_posix()}"; {body}'
    return subprocess.run([_bash(), "-c", script], env=env, capture_output=True, text=True,
                          encoding="utf-8", timeout=60, stdin=subprocess.DEVNULL)


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "acb-gateway.service.d").mkdir(parents=True)
    (repo / "acb-workbench.service.d").mkdir()
    (repo / "acb-gateway.service.d" / "40-agent-site.conf").write_text("[Service]\nA=1\n", encoding="utf-8")
    (repo / "acb-gateway.service.d" / "90-bh2-off.conf").write_text("[Service]\nEVIL=1\n", encoding="utf-8")
    (repo / "acb-workbench.service.d" / "50-hardening.conf").write_text("[Service]\nB=1\n", encoding="utf-8")
    return repo


@needs_bash
def test_the_installer_writes_repo_names_and_keeps_a_rollback(tmp_path: Path) -> None:
    env = _installer_env(tmp_path)
    repo = _repo(tmp_path)
    live = tmp_path / "etc" / "acb-gateway.service.d"
    live.mkdir(parents=True)
    (live / "90-bh2-off.conf").write_text("[Service]\nROLLBACK=1\n", encoding="utf-8")
    (live / "60-hand.conf").write_text("[Service]\nHAND=1\n", encoding="utf-8")

    r = _run_helpers(tmp_path, env, f'install_dropins "{repo.as_posix()}"')
    assert r.returncode == 0, r.stderr
    assert (live / "40-agent-site.conf").read_text(encoding="utf-8") == "[Service]\nA=1\n"
    assert (tmp_path / "etc" / "acb-workbench.service.d" / "50-hardening.conf").is_file()
    # The rollback on the box survives, and the repo's 90-* name never goes in.
    assert (live / "90-bh2-off.conf").read_text(encoding="utf-8") == "[Service]\nROLLBACK=1\n"
    assert (live / "60-hand.conf").is_file(), "the installer deletes nothing"
    assert "skipped acb-gateway.service.d/90-bh2-off.conf" in r.stdout
    assert [ln.strip() for ln in r.stdout.splitlines() if "installed" in ln] == [
        "installed acb-gateway.service.d/40-agent-site.conf",
        "installed acb-workbench.service.d/50-hardening.conf",
    ]
    assert (tmp_path / "log").read_text(encoding="utf-8").splitlines() == ["daemon-reload"]

    # A second run changes nothing, and still reloads.
    r = _run_helpers(tmp_path, env, f'install_dropins "{repo.as_posix()}"')
    assert r.returncode == 0, r.stderr
    assert "installed" not in r.stdout


def _installed(tmp_path: Path, unit: str, stamp: str) -> None:
    """A drop-in on the stub box, with its mtime at `stamp` (UTC)."""
    d = tmp_path / "etc" / f"{unit}.d"
    d.mkdir(parents=True, exist_ok=True)
    f = d / "50-hardening.conf"
    f.write_text("[Service]\n", encoding="utf-8")
    epoch = int(subprocess.run(["date", "-u", "-d", stamp, "+%s"], capture_output=True,
                               text=True, check=True).stdout.strip())
    os.utime(f, (epoch, epoch))


@needs_bash
def test_each_stale_unit_restarts_once_and_only_then(tmp_path: Path) -> None:
    """acb-a: active, started BEFORE its drop-in went in, so one restart.
    acb-b: a step of the apply restarted it after the install, so none.
    acb-c: not active (a oneshot), so none. acb-d: no drop-in on the box."""
    env = _installer_env(tmp_path)
    repo = tmp_path / "repo"
    for u in ("acb-a", "acb-b", "acb-c", "acb-d"):
        (repo / f"{u}.service.d").mkdir(parents=True)
    for u in ("acb-a", "acb-b", "acb-c"):
        _installed(tmp_path, f"{u}.service", "2026-10-09 10:00:00 UTC")
    (tmp_path / "active").write_text("acb-a.service\nacb-b.service\nacb-d.service\n",
                                     encoding="utf-8", newline="\n")
    _since(tmp_path, {
        "acb-a.service": "2026-10-08 10:00:00",
        "acb-b.service": "2026-10-09 10:00:05",
        "acb-d.service": "2026-10-08 10:00:00",
    })
    r = _run_helpers(tmp_path, env, f'restart_stale_dropin_units "{repo.as_posix()}"')
    assert r.returncode == 0, r.stderr
    restarts = [ln for ln in (tmp_path / "log").read_text(encoding="utf-8").splitlines()
                if ln.startswith("restart ")]
    assert restarts == ["restart acb-a.service"]
    assert "acb-c.service is not active" in r.stdout
    assert "acb-b.service started after its newest drop-in" in r.stdout


@needs_bash
def test_a_current_box_means_no_restart(tmp_path: Path) -> None:
    env = _installer_env(tmp_path)
    repo = tmp_path / "repo"
    (repo / "acb-a.service.d").mkdir(parents=True)
    _installed(tmp_path, "acb-a.service", "2026-10-08 09:00:00 UTC")
    (tmp_path / "active").write_text("acb-a.service\n", encoding="utf-8", newline="\n")
    _since(tmp_path, {"acb-a.service": "2026-10-08 10:00:00"})
    r = _run_helpers(tmp_path, env, f'restart_stale_dropin_units "{repo.as_posix()}"')
    assert r.returncode == 0, r.stderr
    assert "restart " not in (tmp_path / "log").read_text(encoding="utf-8")


@needs_bash
def test_a_zone_that_date_cannot_parse_restarts_nothing(tmp_path: Path) -> None:
    """Fix round 1, B. systemd prints ActiveEnterTimestamp in the box's local
    zone, and `date -d` cannot parse a name like AEST. The check asks for
    --timestamp=unix, so a unit that started after its drop-in gets no
    restart, whatever the zone."""
    env = _installer_env(tmp_path)
    repo = tmp_path / "repo"
    (repo / "acb-a.service.d").mkdir(parents=True)
    _installed(tmp_path, "acb-a.service", "2026-10-09 10:00:00 UTC")
    (tmp_path / "active").write_text("acb-a.service\n", encoding="utf-8", newline="\n")
    _since(tmp_path, {"acb-a.service": "2026-10-09 11:00:00"})
    assert "AEST" in (tmp_path / "since").read_text(encoding="utf-8")
    bad = subprocess.run(["date", "-u", "-d", "Fri 2026-10-09 21:00:00 AEST", "+%s"],
                         capture_output=True, text=True)
    assert bad.returncode != 0, "this date parses AEST, so the test proves nothing here"
    r = _run_helpers(tmp_path, env, f'restart_stale_dropin_units "{repo.as_posix()}"')
    assert r.returncode == 0, r.stderr
    log = (tmp_path / "log").read_text(encoding="utf-8")
    assert "--timestamp=unix" in log
    assert "restart " not in log
    assert "acb-a.service started after its newest drop-in" in r.stdout


# ── BH-F3 part 2: the gateway sandbox (the full slice, fix round 4) ──────
#
# Spec: project-docs/specs/box_hardening.md §5 BH-2 items 2, 3, 5 and 6, and
# "Part 2, in the full slice". These read the repo, never the box.

HARDENING_CONF = UNITS / "acb-gateway.service.d" / "50-hardening.conf"

#: Each line of the spec block, in order. A change here is a reviewed change.
HARDENING_LINES = [
    "[Service]",
    "ExecStart=",
    "ExecStart=/opt/acb/app/.venv/bin/uvicorn gateway.main:app --host 0.0.0.0 --port 8080"
    " --timeout-graceful-shutdown 5",
    "Environment=PATH=/opt/acb/app/.venv/bin:/home/acb/.local/bin:/usr/local/bin:/usr/bin:/bin",
    "Environment=VIRTUAL_ENV=/opt/acb/app/.venv",
    "Environment=MEM0_DIR=/var/lib/acb-gateway/mem0",
    "Environment=npm_config_cache=/var/cache/acb-gateway/npm",
    "NoNewPrivileges=yes",
    "PrivateTmp=yes",
    "ProtectSystem=strict",
    "ReadWritePaths=/opt/acb/app/.env /opt/acb/app/data",
    "ReadWritePaths=/opt/acb/app/infra/provider_models_cache.json",
    "ReadWritePaths=/opt/acb/app/apps/services/gateway/agents.json",
    "ProtectHome=read-only",
    "ReadWritePaths=-/home/acb/.acb -/home/acb/.copilot -/home/acb/.cache/copilot"
    " -/home/acb/.cache/github-copilot-sdk",
    "InaccessiblePaths=-/run/user -/run/docker.sock -/var/run/docker.sock -/etc/acb -/etc/sudoers.d",
    "ProtectProc=invisible",
    "RestrictSUIDSGID=yes",
    "CapabilityBoundingSet=",
    "AmbientCapabilities=",
    "LockPersonality=yes",
    "ProtectKernelTunables=yes",
    "ProtectKernelModules=yes",
    "ProtectKernelLogs=yes",
    "ProtectControlGroups=yes",
    "ProtectClock=yes",
    "ProtectHostname=yes",
    "RestrictRealtime=yes",
]

#: The write list of the gateway (spec §2.1 and BH-2 item 1). One constant.
#: A new write path is a reviewed change to this set AND to the conf.
RW_ALLOWLIST = frozenset({
    "/opt/acb/app/.env",
    "/opt/acb/app/data",
    "/opt/acb/app/infra/provider_models_cache.json",
    "/opt/acb/app/apps/services/gateway/agents.json",
    "-/home/acb/.acb",
    "-/home/acb/.copilot",
    "-/home/acb/.cache/copilot",
    "-/home/acb/.cache/github-copilot-sdk",
})

#: Never writable by the gateway: the deploy runs or installs each one.
NEVER_WRITABLE = (".venv", "/opt/acb/t2-vendor", "infra/enabled_models.json",
                  "/opt/acb/app/scripts", "/opt/acb/app/deploy", "/opt/acb/app/.git")

#: The lines of the sandbox that can each stop the start (spec item 6).
START_BLOCKING = ("ReadWritePaths", "InaccessiblePaths", "ProtectSystem", "ProtectHome",
                  "NoNewPrivileges", "PrivateTmp")


def _rw_paths() -> list[str]:
    out: list[str] = []
    for ln in _conf_lines(HARDENING_CONF):
        if ln.startswith("ReadWritePaths="):
            out.extend(ln.split("=", 1)[1].split())
    return out


def test_the_hardening_conf_holds_each_line_of_the_spec_block() -> None:
    assert _conf_lines(HARDENING_CONF) == HARDENING_LINES


def test_the_write_list_is_the_allowlist() -> None:
    paths = _rw_paths()
    assert len(paths) == len(set(paths)), paths
    assert set(paths) == RW_ALLOWLIST


def test_the_write_list_never_holds_a_path_that_the_deploy_runs() -> None:
    for p in _rw_paths():
        bare = p.lstrip("-")
        for bad in NEVER_WRITABLE:
            assert bad not in bare, f"{p} is on the write list of the gateway"
        # A parent of the checkout would give the gateway the whole tree.
        assert bare not in ("/opt/acb/app", "/opt/acb", "/home/acb", "/"), p


def test_the_socket_the_user_manager_and_the_root_files_are_hidden() -> None:
    (line,) = [ln for ln in _conf_lines(HARDENING_CONF) if ln.startswith("InaccessiblePaths=")]
    hidden = {p.lstrip("-") for p in line.split("=", 1)[1].split()}
    assert {"/run/user", "/run/docker.sock", "/var/run/docker.sock", "/etc/acb",
            "/etc/sudoers.d"} <= hidden


def test_the_rollback_resets_each_sandbox_line_of_the_hardening_conf() -> None:
    """The rollback must give the unit of today. A new sandbox line in
    50-hardening.conf with no reset in the rollback would survive `on`."""
    rollback_keys = {ln.split("=", 1)[0] for ln in _conf_lines(ROLLBACK_CONF) if "=" in ln}
    conf_keys = {ln.split("=", 1)[0] for ln in _conf_lines(HARDENING_CONF) if "=" in ln}
    sandbox_keys = {k for k in conf_keys if not k.startswith(("Exec", "Environment"))}
    assert sandbox_keys - rollback_keys == set()
    for key in START_BLOCKING:
        assert key in conf_keys and key in rollback_keys, key


def test_the_hardening_conf_changes_no_user_and_no_python_path() -> None:
    for ln in _conf_lines(HARDENING_CONF):
        key = ln.split("=", 1)[0]
        assert key not in ("User", "Group", "SupplementaryGroups", "EnvironmentFile"), ln
        assert "PYTHONPATH" not in ln, ln


def _code_lines(p: Path) -> list[str]:
    return [ln for ln in _read(p).splitlines() if not ln.lstrip().startswith("#")]


def test_the_strict_check_runs_after_the_last_restart_and_before_the_marker() -> None:
    lines = _code_lines(APPLY)
    check = next(i for i, ln in enumerate(lines) if ln.strip() == "if ! bh2_strict_check; then")
    marker = next(i for i, ln in enumerate(lines)
                  if ln.startswith('record_applied_sha "$(git -C "$APP_DIR" rev-parse HEAD)"'))
    stale = next(i for i, ln in enumerate(lines)
                 if ln.startswith('restart_stale_dropin_units "$APP_DIR'))
    restarts = [i for i, ln in enumerate(lines)
                if "systemctl restart" in ln or ln.startswith("restart_stale_dropin_units ")]
    assert stale < check < marker
    assert max(restarts) < check, "a restart after the strict check is a unit it never read"
    after = "\n".join(lines[check:marker])
    assert "exit 1" in after


def test_the_rw_paths_step_runs_before_the_gateway_restart() -> None:
    lines = _code_lines(APPLY)
    install = next(i for i, ln in enumerate(lines)
                   if ln.startswith('install_dropins "$APP_DIR/deploy/hostinger"'))
    ensure = next(i for i, ln in enumerate(lines)
                  if ln.startswith('ensure_gateway_rw_paths "$APP_DIR"'))
    restart = next(i for i, ln in enumerate(lines) if ln == "sudo systemctl restart acb-gateway")
    assert install < ensure < restart


def test_the_rw_paths_step_covers_each_path_with_no_dash() -> None:
    """A ReadWritePaths entry with no "-" must exist, or the unit does not
    start. ensure_gateway_rw_paths checks exactly that list."""
    must = {p.removeprefix("/opt/acb/app/") for p in _rw_paths() if not p.startswith("-")}
    block = "\n".join(_apply_block("# >>> bh2 helpers", "# <<< bh2 helpers"))
    start = block.index("ensure_gateway_rw_paths() {")
    fn = block[start: block.index("\n}\n", start)]
    named = set(re.findall(r'"\$app/([^"]+)"', fn))
    assert named == must, (named, must)


def test_the_operator_console_unit_is_a_repo_file() -> None:
    unit = UNITS / "acb-operator-console.service"
    assert unit.is_file()
    keys = _conf_lines(unit)
    assert "User=acb" in keys
    assert "ExecStart=/usr/bin/npm start" in keys
    assert "EnvironmentFile=/opt/acb/app/workbench/operator_console/.env.local" in keys
    assert _last_value(unit, "NoNewPrivileges") == "yes"
