"""H-200 — every CI ssh to the box checks the box's host key against a pin.

Before H-200 the deploy, ``vps-health`` and ``vps-forensics`` ran ssh with
``StrictHostKeyChecking=no`` and ``UserKnownHostsFile=/dev/null``. So ssh took
ANY host key, and a host that answered at the box's address got the deploy
session. Now the box's public keys are committed in
``deploy/hostinger/known_hosts``, ``scripts/ci_ssh_host_key.sh`` is the one
helper that writes them out with the host field, and ``CI_SSH_HOST_KEY_OPTS``
is the one option set.

Three layers:

  * Structural: no workflow or CI script trusts any key, every ssh to the box
    carries the one option set, and each workflow pins before it connects.
  * The pin itself: the file parses, and its fingerprints are the three that
    three sources agreed on, on 2026-09-29.
  * A REAL ssh against a REAL sshd (Linux, when sshd is installed): the pinned
    key passes for the plain and the bracketed host form, another key fails
    with the words ``ssh_host_key_fault`` reads, and nothing is "Permanently
    added". The fake-ssh cases in ``test_deploy_reach.py`` then prove what the
    deploy does with that failure.
"""

from __future__ import annotations

import base64
import hashlib
import io
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PINNED = ROOT / "deploy" / "hostinger" / "known_hosts"
HELPER = ROOT / "scripts" / "ci_ssh_host_key.sh"
REACH = ROOT / "scripts" / "ci_deploy_reach.sh"
WORKFLOWS = ROOT / ".github" / "workflows"
SSH_WORKFLOWS = ("deploy.yml", "vps-health.yml", "vps-forensics.yml")

# Recorded 2026-09-29. The box's own /etc/ssh/ssh_host_*_key.pub, a long-
# trusted known_hosts and a fresh ssh-keyscan all gave these three.
FINGERPRINTS = {
    "ssh-ed25519": "SHA256:p8ybBFTCC4BTM8FUCg71Sdk69gzNbJ1JB2PFh3A7D3M",
    "ecdsa-sha2-nistp256": "SHA256:kFOtIoSUTIGiqO2tv9ILNB9bnMVpw0fiElLQbcspYdc",
    "ssh-rsa": "SHA256:xLH7+ggc9RnwiS7AINCFtWs/RQrKpxuILnPfsVoDUf4",
}

TRUST_ANY = re.compile(
    r"StrictHostKeyChecking\s*=?\s*(no|off|accept-new)|UserKnownHostsFile\s*=?\s*/dev/null", re.I
)


def _read(p: Path) -> str:
    return io.open(p, encoding="utf-8").read()


def _key_lines() -> list[str]:
    return [
        ln.strip()
        for ln in _read(PINNED).splitlines()
        if ln.strip() and not ln.lstrip().startswith("#")
    ]


def _fingerprint(b64: str) -> str:
    digest = hashlib.sha256(base64.b64decode(b64)).digest()
    return "SHA256:" + base64.b64encode(digest).decode().rstrip("=")


# ── No CI ssh trusts any key ────────────────────────────────────────────────


def _ci_files() -> list[Path]:
    files = sorted(WORKFLOWS.glob("*.yml")) + sorted(WORKFLOWS.glob("*.yaml"))
    files += sorted((ROOT / "scripts").rglob("*.sh"))
    files += sorted((ROOT / "deploy").rglob("*.sh"))
    return files


def test_no_ci_ssh_trusts_any_host_key() -> None:
    """H-200's own Check, widened to every workflow and every script."""
    hits = []
    for f in _ci_files():
        for n, ln in enumerate(_read(f).splitlines(), 1):
            code = ln.split("#", 1)[0] if f.suffix == ".sh" else ln
            if TRUST_ANY.search(code) and not ln.lstrip().startswith("#"):
                hits.append(f"{f.relative_to(ROOT)}:{n}: {ln.strip()}")
    assert not hits, "these lines trust ANY ssh host key (H-200):\n" + "\n".join(hits)


def test_the_one_option_set_is_strict_and_pinned() -> None:
    body = _read(HELPER)
    opts = body[body.index("CI_SSH_HOST_KEY_OPTS=(") :]
    opts = opts[: opts.index(")\n")]
    assert "-o StrictHostKeyChecking=yes" in opts
    assert '-o UserKnownHostsFile="$CI_KNOWN_HOSTS"' in opts
    assert "-o GlobalKnownHostsFile=/dev/null" in opts
    assert "-o UpdateHostKeys=no" in opts


def test_the_deploy_options_carry_the_pin() -> None:
    body = _read(REACH)
    opts = body[body.index("CI_SSH_OPTS=(") :]
    opts = opts[: opts.index(")\n")]
    assert '"${CI_SSH_HOST_KEY_OPTS[@]}"' in opts
    assert '. "$(dirname "${BASH_SOURCE[0]}")/ci_ssh_host_key.sh"' in body


# An ssh COMMAND: the first word of a line, or the word after `timeout N`.
# "-u ssh -u sshd" in a journalctl line is not one.
_SSH_CALL = re.compile(r"(?:^\s*|\btimeout\s+(?:-k\s+\d+\s+)?\d+\s+)ssh\s")


@pytest.mark.parametrize("name", SSH_WORKFLOWS)
def test_every_ssh_to_the_box_uses_the_one_option_set(name: str) -> None:
    """A new ssh line that forgets the options would fall back to the
    runner's defaults. The one option set is on the same line, or it fails."""
    raw = _read(WORKFLOWS / name)
    calls = [
        ln for ln in raw.splitlines() if _SSH_CALL.search(ln) and not ln.lstrip().startswith("#")
    ]
    assert calls, f"{name}: found no ssh call, so this test checks nothing"
    for ln in calls:
        assert "CI_SSH_OPTS" in ln or "CI_SSH_HOST_KEY_OPTS" in ln, f"{name}: {ln.strip()}"


@pytest.mark.parametrize("name", SSH_WORKFLOWS)
def test_each_workflow_pins_before_it_connects(name: str) -> None:
    lines = _read(WORKFLOWS / name).splitlines()
    pin = next(i for i, ln in enumerate(lines) if "ci_pin_host_key || exit 1" in ln)
    first_ssh = next(
        i for i, ln in enumerate(lines) if _SSH_CALL.search(ln) and not ln.lstrip().startswith("#")
    )
    assert pin < first_ssh, f"{name}: ci_pin_host_key must run before the first ssh"


@pytest.mark.parametrize("name", ("vps-health.yml", "vps-forensics.yml"))
def test_the_diagnostic_workflows_check_out_the_pin(name: str) -> None:
    """They had no checkout. Without one, the helper is not there to source."""
    raw = _read(WORKFLOWS / name)
    src = raw.index(". scripts/ci_ssh_host_key.sh")
    co = raw.rindex("uses: actions/checkout@", 0, src)
    block = raw[co : raw.index("- name:", co)]
    assert "scripts/ci_ssh_host_key.sh" in block
    assert "deploy/hostinger/known_hosts" in block


@pytest.mark.parametrize("name", ("vps-health.yml", "vps-forensics.yml"))
def test_the_diagnostic_retries_stop_on_a_host_key_fault(name: str) -> None:
    raw = _read(WORKFLOWS / name)
    check = raw.index('if [ "$rc" != 0 ] && ssh_host_key_fault < /tmp/ssh.err; then')
    assert "host_key_red" in raw[check : check + 200]
    assert "exit 1" in raw[check : check + 200]


def test_the_red_message_says_what_to_do_and_what_never_to_do() -> None:
    body = _read(HELPER)
    fn = body[body.index("host_key_red() {") :]
    assert "host key changed, or something else answered at this address" in fn.lower()
    assert "H-200" in fn
    assert "check the box" in fn
    assert "NEVER disable the check" in fn
    assert "section 8.5" in fn


def test_the_repin_procedure_is_documented() -> None:
    spec = _read(ROOT / "project-docs" / "specs" / "deploy_delivery_path.md")
    sec = spec[spec.index("### 8.5") :]
    sec = sec[: sec.index("\n## ")]
    assert "deploy/hostinger/known_hosts" in sec
    assert "ssh-keyscan" in sec
    assert "ssh-keygen -lf" in sec
    assert "test_ssh_host_key_pin.py" in sec


# ── The pin itself ──────────────────────────────────────────────────────────


def test_the_pin_holds_exactly_the_three_recorded_keys() -> None:
    """Pure Python, so it runs on every OS: SHA256 of the key blob, base64,
    no padding. That is how ssh-keygen -lf prints a fingerprint."""
    got = {}
    for ln in _key_lines():
        parts = ln.split()
        assert len(parts) == 2, f"a key line is `type base64` with no host field: {ln[:40]}"
        got[parts[0]] = _fingerprint(parts[1])
    assert got == FINGERPRINTS


@pytest.mark.skipif(not shutil.which("ssh-keygen"), reason="needs ssh-keygen")
def test_ssh_keygen_parses_the_pin_and_agrees() -> None:
    r = subprocess.run(
        ["ssh-keygen", "-lf", str(PINNED)], capture_output=True, text=True, check=True
    )
    fps = sorted(ln.split()[1] for ln in r.stdout.splitlines())
    assert fps == sorted(FINGERPRINTS.values()), r.stdout


# ── Behavioural: the helper, and a real ssh against a real sshd ─────────────

_LINUX = sys.platform.startswith("linux") and shutil.which("bash")
needs_linux = pytest.mark.skipif(not _LINUX, reason="needs Linux with bash")


def _helper(tmp: Path, script: str, **env: str) -> subprocess.CompletedProcess:
    e = dict(os.environ)
    e.update(HOME=str(tmp / "home"), **env)
    return subprocess.run(
        ["bash", "-c", f". {HELPER.as_posix()}\n{script}"],
        env=e,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )


@needs_linux
@pytest.mark.parametrize(("port", "name"), [("22", "10.1.2.3"), ("2222", "[10.1.2.3]:2222")])
def test_the_helper_writes_the_host_form_ssh_looks_up(tmp_path: Path, port: str, name: str) -> None:
    r = _helper(tmp_path, f"ci_pin_host_key 10.1.2.3 {port}")
    assert r.returncode == 0, r.stdout + r.stderr
    kh = (tmp_path / "home" / ".ssh" / "metorite_known_hosts").read_text(encoding="utf-8")
    assert kh.splitlines() == [f"{name} {k}" for k in _key_lines()]
    if shutil.which("ssh-keygen"):
        f = tmp_path / "home" / ".ssh" / "metorite_known_hosts"
        found = subprocess.run(
            ["ssh-keygen", "-F", name, "-f", str(f)], capture_output=True, text=True
        )
        assert found.returncode == 0 and found.stdout.count("found: line") == 3, found.stdout


@needs_linux
def test_the_helper_refuses_an_empty_pin(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.write_text("# only a comment\n", encoding="utf-8")
    r = _helper(tmp_path, "ci_pin_host_key 10.1.2.3 22", CI_PINNED_KEYS=str(empty))
    assert r.returncode == 1
    assert "No pinned host key" in r.stdout


@needs_linux
def test_the_helper_refuses_no_host(tmp_path: Path) -> None:
    r = _helper(tmp_path, "SSH_HOST= ci_pin_host_key", SSH_HOST="")
    assert r.returncode == 1
    assert "No host to pin" in r.stdout


_SSHD = shutil.which("sshd") or ("/usr/sbin/sshd" if os.path.exists("/usr/sbin/sshd") else None)
needs_sshd = pytest.mark.skipif(
    not (_LINUX and _SSHD and shutil.which("ssh") and shutil.which("ssh-keygen")),
    reason="needs Linux with ssh, ssh-keygen and sshd",
)


@needs_sshd
class TestARealSshMeetsTheOptions:
    """ssh talks to `sshd -i` through ProxyCommand: no port, no daemon, no
    root. The host-key check runs before auth, so "Permission denied" proves
    the key PASSED, and "Host key verification failed" proves it did not."""

    @pytest.fixture()
    def world(self, tmp_path: Path):
        for n in ("hk", "other"):
            subprocess.run(
                ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(tmp_path / n)], check=True
            )
        cfg = tmp_path / "sshd_config"
        cfg.write_text("PidFile none\nUsePAM no\n", encoding="utf-8")
        return tmp_path

    def _ssh(
        self, w: Path, served: str, pinned: str, pin_port: str, port: str
    ) -> subprocess.CompletedProcess:
        pins = w / "pins"
        pins.write_text(
            " ".join((w / f"{pinned}.pub").read_text(encoding="utf-8").split()[:2]) + "\n",
            encoding="utf-8",
        )
        px = f"ProxyCommand={_SSHD} -i -f {w / 'sshd_config'} -h {w / served}"
        script = (
            f"ci_pin_host_key 10.1.2.3 {pin_port} >/dev/null || exit 9\n"
            f'ssh -p {port} "${{CI_SSH_HOST_KEY_OPTS[@]}}" -o BatchMode=yes '
            f'-o "{px}" -o ConnectTimeout=10 nobody-here@10.1.2.3 true'
        )
        return _helper(w, script, CI_PINNED_KEYS=str(pins))

    def _started(self, r: subprocess.CompletedProcess) -> None:
        if "privilege separation" in r.stderr.lower() or "Missing" in r.stderr:
            pytest.skip(f"sshd cannot run here: {r.stderr.strip()[:200]}")

    @pytest.mark.parametrize("port", ["22", "2222"])
    def test_the_pinned_key_passes(self, world: Path, port: str) -> None:
        r = self._ssh(world, "hk", "hk", port, port)
        self._started(r)
        assert "Permission denied" in r.stderr, r.stderr
        assert "Host key verification failed" not in r.stderr
        assert "Permanently added" not in r.stderr, "ssh must never add a key to the pin"

    @pytest.mark.parametrize("port", ["22", "2222"])
    def test_another_key_fails_with_the_words_the_deploy_reads(
        self, world: Path, port: str
    ) -> None:
        r = self._ssh(world, "hk", "other", port, port)
        self._started(r)
        assert r.returncode == 255
        assert "Host key verification failed" in r.stderr, r.stderr
        assert "Permission denied" not in r.stderr, "the host key must be refused BEFORE auth"
        check = subprocess.run(
            ["bash", "-c", f". {HELPER.as_posix()}\nssh_host_key_fault"],
            input=r.stderr,
            capture_output=True,
            text=True,
        )
        assert check.returncode == 0, "ssh_host_key_fault must recognise real ssh output"

    def test_a_pin_for_another_port_does_not_pass(self, world: Path) -> None:
        """A pin written for [host]:2222 names that port only."""
        r = self._ssh(world, "hk", "hk", "2222", "2022")
        self._started(r)
        assert "Host key verification failed" in r.stderr, r.stderr
