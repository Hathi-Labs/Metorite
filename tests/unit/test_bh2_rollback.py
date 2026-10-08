"""WS-49 BH-2 — `scripts/bh2_rollback.sh`, its 72-hour expiry and its sudo reads.

Spec: project-docs/specs/box_hardening.md §5 BH-2 item 6 (S1, S4, B2-3, B2-4).

The real script runs against stubs on PATH:

- `sudo` runs the command, but first maps each argument under `/etc/` into a
  fake root. So a root file the script reached WITHOUT sudo would land on the
  real `/etc`, and these tests would not see it. The static test below closes
  that gap: every command on a root path starts with `sudo` (B2-3).
- `date` answers `-u +%s` with a fake now, so the expiry is tested in time.
- `systemctl`, `curl` and `sleep` record or answer, and never reach a box.

The `status` exit codes are the contract that the strict check of the full
BH-2 slice reads: 0 on and valid, 1 off, 3 expired or half there, 2 usage.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "bh2_rollback.sh"
CONF = ROOT / "deploy" / "hostinger" / "rollback" / "acb-gateway-90-bh2-off.conf"

DROPIN = "etc/systemd/system/acb-gateway.service.d/90-bh2-off.conf"
ACK = "etc/acb/bh2-rollback-ack"
NOW = 1_800_000_000
TTL = 72 * 3600


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
skip=0
for a in "$@"; do
  # Root ignores a mode bit, and this stub is not root. So `mkdir -m 0700`
  # drops its mode here, or Windows makes the dir unwritable to the test.
  if [ "$skip" = 1 ]; then skip=0; continue; fi
  if [ "$1" = "mkdir" ] && [ "$a" = "-m" ]; then skip=1; continue; fi
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

STUB_SYSTEMCTL = r"""#!/usr/bin/env bash
printf '%s\n' "$*" >> "$STUB_LOG.systemctl"
[ "$1" = "is-active" ] && exit "${STUB_ACTIVE_RC:-0}"
exit 0
"""

STUB_CURL = r"""#!/usr/bin/env bash
exit "${STUB_CURL_RC:-0}"
"""

STUB_SLEEP = "#!/usr/bin/env bash\nexit 0\n"


class Box:
    def __init__(self, tmp_path: Path) -> None:
        self.tmp = tmp_path
        self.root = tmp_path / "root"
        self.root.mkdir()
        self.log = tmp_path / "log"
        binr = tmp_path / "bin"
        binr.mkdir()
        for name, body in (("sudo", STUB_SUDO), ("date", STUB_DATE),
                           ("systemctl", STUB_SYSTEMCTL), ("curl", STUB_CURL),
                           ("sleep", STUB_SLEEP)):
            (binr / name).write_text(body, encoding="utf-8", newline="\n")
            (binr / name).chmod(0o755)
        self.env = dict(
            os.environ,
            PATH=f"{binr}{os.pathsep}{os.environ.get('PATH', '')}",
            FAKE_ROOT=self.root.as_posix(),
            FAKE_NOW=str(NOW),
            REAL_DATE=shutil.which("date") or "date",
            STUB_LOG=self.log.as_posix(),
        )

    def run(self, *args: str, now: int = NOW, **extra: str) -> subprocess.CompletedProcess:
        env = dict(self.env, FAKE_NOW=str(now), **extra)
        return subprocess.run([_bash(), SCRIPT.as_posix(), *args], env=env,
                              capture_output=True, text=True, encoding="utf-8",
                              timeout=60, stdin=subprocess.DEVNULL)

    def path(self, rel: str) -> Path:
        return self.root / rel

    def calls(self, tool: str) -> list[str]:
        p = Path(f"{self.log.as_posix()}.{tool}")
        return p.read_text(encoding="utf-8").splitlines() if p.exists() else []

    def clear_calls(self) -> None:
        for tool in ("sudo", "systemctl"):
            Path(f"{self.log.as_posix()}.{tool}").unlink(missing_ok=True)

    def expiry(self) -> int:
        text = self.path(ACK).read_text(encoding="utf-8")
        return int(re.search(r"^EXPIRES_EPOCH=(\d+)$", text, re.M).group(1))

    def tree(self) -> dict[str, str]:
        return {
            p.relative_to(self.root).as_posix(): p.read_text(encoding="utf-8")
            for p in sorted(self.root.rglob("*")) if p.is_file()
        }


@pytest.fixture
def box(tmp_path: Path) -> Box:
    return Box(tmp_path)


# ── on ───────────────────────────────────────────────────────────────────


@needs_bash
def test_on_installs_the_conf_writes_now_plus_72h_and_restarts(box: Box) -> None:
    r = box.run("on")
    assert r.returncode == 0, r.stdout + r.stderr
    assert box.path(DROPIN).read_text(encoding="utf-8") == CONF.read_text(encoding="utf-8")
    assert box.expiry() == NOW + TTL
    ack = box.path(ACK).read_text(encoding="utf-8")
    assert "EXPIRES_UTC=2027-01-18T08:00:00Z" in ack, ack
    assert box.calls("systemctl")[:2] == ["daemon-reload", "restart acb-gateway"]
    assert "WARN BH-2 rolled back until 2027-01-18T08:00:00Z" in r.stdout


@needs_bash
def test_a_second_on_keeps_the_expiry_and_does_not_restart(box: Box) -> None:
    assert box.run("on").returncode == 0
    box.clear_calls()
    r = box.run("on", now=NOW + 10 * 3600)
    assert r.returncode == 0, r.stdout + r.stderr
    assert box.expiry() == NOW + TTL, "a second `on` moved the expiry"
    assert "already on" in r.stdout
    assert not [c for c in box.calls("systemctl") if c.startswith(("restart", "daemon-reload"))]


@needs_bash
def test_on_refuses_an_expired_rollback(box: Box) -> None:
    assert box.run("on").returncode == 0
    before = box.tree()
    r = box.run("on", now=NOW + TTL + 1)
    assert r.returncode == 3, r.stdout + r.stderr
    assert "off" in r.stderr
    assert box.tree() == before


@needs_bash
def test_on_completes_a_half_rollback_and_keeps_a_valid_expiry(box: Box) -> None:
    ack = box.path(ACK)
    ack.parent.mkdir(parents=True)
    ack.write_text(f"EXPIRES_EPOCH={NOW + 3600}\n", encoding="utf-8")
    r = box.run("on")
    assert r.returncode == 0, r.stdout + r.stderr
    assert box.path(DROPIN).is_file()
    assert box.expiry() == NOW + 3600
    assert "restart acb-gateway" in box.calls("systemctl")


@needs_bash
def test_on_fails_when_the_gateway_does_not_answer(box: Box) -> None:
    r = box.run("on", STUB_CURL_RC="7")
    assert r.returncode == 1
    assert "did not answer" in r.stderr


# ── status and the expiry ────────────────────────────────────────────────


@needs_bash
@pytest.mark.parametrize(("offset", "rc", "word"), [
    (0, 0, "on, expires"),
    (TTL - 1, 0, "on, expires"),
    (TTL, 3, "EXPIRED"),
    (TTL + 3600, 3, "EXPIRED"),
])
def test_status_honours_the_rollback_only_until_its_date(box: Box, offset: int, rc: int,
                                                         word: str) -> None:
    assert box.run("on").returncode == 0
    r = box.run("status", now=NOW + offset)
    assert r.returncode == rc, r.stdout + r.stderr
    assert word in r.stdout
    assert ("WARN BH-2 rolled back" in r.stdout) == (rc == 0)


@needs_bash
def test_status_is_1_when_off(box: Box) -> None:
    r = box.run("status")
    assert r.returncode == 1
    assert r.stdout.strip() == "BH-2 rollback: off"


@needs_bash
@pytest.mark.parametrize("body", ["", "EXPIRES_EPOCH=soon\n", "EXPIRES_UTC=2099-01-01T00:00:00Z\n",
                                  "EXPIRES_EPOCH=99999999999 # comment\n"])
def test_a_malformed_ack_counts_as_expired(box: Box, body: str) -> None:
    box.path(DROPIN).parent.mkdir(parents=True)
    box.path(DROPIN).write_text(CONF.read_text(encoding="utf-8"), encoding="utf-8")
    box.path(ACK).parent.mkdir(parents=True)
    box.path(ACK).write_text(body, encoding="utf-8")
    r = box.run("status")
    assert r.returncode == 3, r.stdout
    assert "EXPIRED" in r.stdout


def _hand_written_ack(box: Box, value: str) -> None:
    box.path(DROPIN).parent.mkdir(parents=True)
    box.path(DROPIN).write_text(CONF.read_text(encoding="utf-8"), encoding="utf-8")
    box.path(ACK).parent.mkdir(parents=True)
    box.path(ACK).write_text(f"EXPIRES_EPOCH={value}\n", encoding="utf-8", newline="\n")


@needs_bash
@pytest.mark.parametrize(("value", "why"), [
    ("9" * 25, "no valid EXPIRES_EPOCH"),           # `[ -ge ]` errors on it
    ("9" * 13, "no valid EXPIRES_EPOCH"),           # more than 12 digits
    (str(NOW + 10 * 365 * 86400), "more than 72 h ahead"),
    (str(NOW + TTL + 301), "more than 72 h ahead"),  # past the 300 s slack
    ("-1", "no valid EXPIRES_EPOCH"),
    ("", "no valid EXPIRES_EPOCH"),
])
def test_an_untrusted_expiry_fails_closed(box: Box, value: str, why: str) -> None:
    """Review fix round 1, P2. A huge value made `[ -ge ]` error out, and the
    state fell through to "on" with exit 0. A date 10 years ahead was valid.
    Each one is now EXPIRED (exit 3), and `on` refuses it."""
    _hand_written_ack(box, value)
    r = box.run("status")
    assert r.returncode == 3, r.stdout + r.stderr
    assert "EXPIRED" in r.stdout and why in r.stdout, r.stdout
    assert "WARN BH-2 rolled back" not in r.stdout
    r = box.run("on")
    assert r.returncode == 3, r.stdout + r.stderr
    assert not [c for c in box.calls("systemctl") if c.startswith("restart")]


@needs_bash
def test_an_expiry_inside_the_slack_is_trusted(box: Box) -> None:
    """`on` at one second and the check at the next must agree. So an expiry
    up to 300 s past now + 72 h stays valid."""
    _hand_written_ack(box, str(NOW + TTL + 300))
    r = box.run("status")
    assert r.returncode == 0, r.stdout + r.stderr


@needs_bash
@pytest.mark.parametrize("which", ["conf", "ack"])
def test_a_half_rollback_is_not_honoured(box: Box, which: str) -> None:
    assert box.run("on").returncode == 0
    (box.path(ACK) if which == "conf" else box.path(DROPIN)).unlink()
    r = box.run("status")
    assert r.returncode == 3, r.stdout
    assert "PARTIAL" in r.stdout


@needs_bash
def test_status_reads_the_ack_through_sudo(box: Box) -> None:
    """B2-3: /etc/acb is root-only, so the read goes through sudo."""
    assert box.run("on").returncode == 0
    box.clear_calls()
    assert box.run("status").returncode == 0
    assert "cat /etc/acb/bh2-rollback-ack" in box.calls("sudo")


# ── off, and the round trip ──────────────────────────────────────────────


@needs_bash
def test_on_then_off_leaves_the_files_as_they_were(box: Box) -> None:
    other = box.path("etc/systemd/system/acb-gateway.service.d/40-agent-site.conf")
    other.parent.mkdir(parents=True)
    other.write_text("[Service]\nStateDirectory=acb-gateway\n", encoding="utf-8")
    keep = box.path("etc/acb/backup-offbox.env")
    keep.parent.mkdir(parents=True)
    keep.write_text("NAME=value\n", encoding="utf-8")
    before = box.tree()
    assert box.run("on").returncode == 0
    assert box.tree() != before
    box.clear_calls()
    r = box.run("off")
    assert r.returncode == 0, r.stdout + r.stderr
    assert box.tree() == before
    assert box.calls("systemctl")[:2] == ["daemon-reload", "restart acb-gateway"]
    assert "MODE=force" in r.stdout


@needs_bash
def test_off_after_the_expiry_removes_both_files(box: Box) -> None:
    """B2-4: the recovery after the expiry starts with `off`."""
    assert box.run("on").returncode == 0
    r = box.run("off", now=NOW + TTL + 7200)
    assert r.returncode == 0, r.stdout + r.stderr
    assert not box.path(DROPIN).exists()
    assert not box.path(ACK).exists()
    assert box.run("status").returncode == 1


@needs_bash
def test_a_second_off_does_not_restart(box: Box) -> None:
    r = box.run("off")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "already off" in r.stdout
    assert not [c for c in box.calls("systemctl") if c.startswith("restart")]


@needs_bash
@pytest.mark.parametrize("args", [(), ("bogus",), ("on", "off")])
def test_usage(box: Box, args: tuple[str, ...]) -> None:
    assert box.run(*args).returncode == 2


# ── static ───────────────────────────────────────────────────────────────

_ROOT_VAR = re.compile(r'"\$(ACK|DROPIN|ACK_DIR|DROPIN_DIR)"')
_FILE_VERB = re.compile(r"\b(cat|cmp|install|mkdir|rm|test|tee|cp|mv|chmod)\b")


def test_every_root_file_command_goes_through_sudo() -> None:
    """B2-3. A command on a root path that skips sudo fails for `acb`, and the
    stubbed tests above cannot see it. So each such command starts with sudo."""
    bad = []
    for n, ln in enumerate(SCRIPT.read_text(encoding="utf-8").splitlines(), 1):
        code = ln.strip()
        if not code or code.startswith("#") or not _ROOT_VAR.search(code):
            continue
        if code.startswith(("echo ", "printf ")) or "=" in code.split(" ", 1)[0]:
            continue
        for part in re.split(r"&&|\|\||;|\||\bthen\b|\bif\b|!", code):
            part = part.strip()
            if _ROOT_VAR.search(part) and _FILE_VERB.match(part):
                bad.append(f"{n}: {ln.strip()}")
    assert not bad, "a root file command without sudo:\n" + "\n".join(bad)


def test_the_paths_and_the_ttl_are_the_spec_values() -> None:
    body = SCRIPT.read_text(encoding="utf-8")
    assert 'DROPIN="$DROPIN_DIR/90-bh2-off.conf"' in body
    assert 'DROPIN_DIR="/etc/systemd/system/${UNIT}.service.d"' in body
    assert 'ACK="$ACK_DIR/bh2-rollback-ack"' in body
    assert 'ACK_DIR="/etc/acb"' in body
    assert "TTL_SECONDS=$((72 * 3600))" in body
    assert "deploy/hostinger/rollback/acb-gateway-90-bh2-off.conf" in body
