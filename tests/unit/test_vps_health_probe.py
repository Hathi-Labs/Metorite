"""2026-10-03 — the outside probe must never read a dead box as healthy.

Production HTTPS was down that morning, from between 02:02 and 04:54 UTC until
about 07:00 UTC. ``vps-health.yml`` said HEALTHY on every hourly run. Runs
37098074149 and 37101731478 show ``OK (HTTP 000000)``.

The cause was one idiom. The probe line ended in ``|| echo 000`` INSIDE its
``$(...)``. When the connect fails, curl prints ``000`` through ``-w`` AND
exits non-zero, so the ``echo`` added a second ``000``. The code read
``000000``. That matched neither the ``000)`` arm nor the 5xx arm, and the
catch-all arm was "OK".

Two rules, and this file fences both:

  * Every probe yields ONE code. Never ``000000``, and never an empty string.
  * The verdict is an ALLOWLIST. Only 2xx, 3xx and 4xx earn "alive". The
    catch-all arm is the outage arm, so a code nobody foresaw reads as down.

The behavioural cases run the real step, lifted out of the YAML, under
``bash -e``. That is how GitHub runs a step with no ``shell:``. The fakes for
``curl``, ``sleep`` and ``python3`` are shell FUNCTIONS put in front of the
step. A function wins over any binary on PATH, so the fakes also hold in Git
Bash on Windows, where a fake binary on PATH loses to Git's own.

The static case reads every workflow and script for the idiom itself.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "vps-health.yml"

_BASH = shutil.which("bash")
# On Windows, WSL's launcher in System32 also answers to `bash`. It cannot read
# a Windows temp path, so it is not a bash this file can use.
_HAVE_BASH = bool(_BASH) and "system32" not in (_BASH or "").lower()
needs_bash = pytest.mark.skipif(not _HAVE_BASH, reason="needs bash (Git Bash on Windows)")
needs_git = pytest.mark.skipif(not shutil.which("git"), reason="needs git")

# FAKE is "<what curl prints for -w>:<curl exit code>", for example "000:7".
# FAKE_WB, when set, answers for the workbench host instead.
# FAKE_BODY is what curl prints when the step reads a body (no -w).
_FAKES = r"""
curl() {
  echo "curl $*" >> "$FAKE_LOG"
  local spec="$FAKE"
  case "$*" in *wb.test*) spec="${FAKE_WB:-$FAKE}" ;; esac
  case "$*" in
    *http_code*) printf '%s' "${spec%:*}" ;;
    *) printf '%s' "${FAKE_BODY:-}" ;;
  esac
  return "${spec##*:}"
}
sleep() { echo "sleep $*" >> "$FAKE_LOG"; }
# Windows Python ends a line with CR LF. Strip the CR, keep the exit code.
python3() {
  local o rc=0
  o="$("$FAKE_PY" "$@")" || rc=$?
  [ -z "$o" ] || printf '%s\n' "${o//$'\r'/}"
  return "$rc"
}
"""

# Every code the steps print follows "HTTP " or "curl gave ".
_CODE = re.compile(r"(?:HTTP|curl gave) ([^\s),]*)")


def _jobs() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]


def _run(cwd: Path, job: str, **fake: str) -> tuple[subprocess.CompletedProcess, str]:
    """Run the first `run:` step of `job` with the fakes in front of it."""
    step = next(s for s in _jobs()[job]["steps"] if "run" in s)
    script = cwd / "step.sh"
    script.write_text(_FAKES + "\n" + step["run"], encoding="utf-8", newline="\n")
    out = cwd / "gh_output"
    out.write_text("", encoding="utf-8")
    env = dict(os.environ)
    env.update({k: str(v) for k, v in (step.get("env") or {}).items()})
    env.update(
        GATEWAY_URL="https://gw.test/health",
        WORKBENCH_URL="https://wb.test",
        BASE="https://gw.test",
        GITHUB_OUTPUT=out.as_posix(),
        FAKE_LOG=(cwd / "calls").as_posix(),
        FAKE_PY=Path(sys.executable).as_posix(),
        FAKE="200:0",
    )
    env.update(fake)
    r = subprocess.run(
        [_BASH or "bash", "--noprofile", "--norc", "-e", "step.sh"],
        cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8",
        timeout=60,
    )
    return r, out.read_text(encoding="utf-8")


def _verdict(gh_output: str) -> tuple[str, str]:
    healthy = re.search(r"^healthy=(.*)$", gh_output, re.M)
    assert healthy, gh_output
    report = gh_output.split("report<<REPORT_EOF\n", 1)[1].split("REPORT_EOF", 1)[0]
    return healthy.group(1), report


def _curls(cwd: Path) -> int:
    log = cwd / "calls"
    if not log.exists():
        return 0
    return sum(1 for ln in log.read_text(encoding="utf-8").splitlines() if ln.startswith("curl "))


def _one_code_each(text: str) -> None:
    seen = _CODE.findall(text)
    assert seen, f"no code was printed at all:\n{text}"
    bad = [c for c in seen if not re.fullmatch(r"[0-9]{3}", c)]
    assert not bad, (
        f"a probe printed {bad!r}, which is not ONE 3-digit code. "
        f"`|| echo 000` inside the $(...) makes `000000` (2026-10-03).\n{text}"
    )


# ── The probe job: the outage verdict ───────────────────────────────────────


@needs_bash
class TestTheProbeJob:
    def test_a_failed_connect_is_unreachable(self, tmp_path: Path) -> None:
        """The 2026-10-03 shape. curl prints 000 and exits 7."""
        r, out = _run(tmp_path, "probe", FAKE="000:7")
        assert r.returncode == 0, r.stdout + r.stderr
        healthy, report = _verdict(out)
        assert healthy == "0", f"a dead box read as HEALTHY:\n{report}"
        assert report.count("UNREACHABLE") == 2, report
        assert "OK" not in report, report
        _one_code_each(r.stdout + report)
        assert _curls(tmp_path) == 6, "each target must get three tries"

    def test_no_output_at_all_is_unreachable(self, tmp_path: Path) -> None:
        """No curl output at all, for example a missing curl binary."""
        r, out = _run(tmp_path, "probe", FAKE=":127")
        assert r.returncode == 0, r.stdout + r.stderr
        healthy, report = _verdict(out)
        assert healthy == "0", report
        assert report.count("UNREACHABLE") == 2, report
        _one_code_each(r.stdout + report)

    @pytest.mark.parametrize("code", ["200", "204", "301", "307", "401", "404"])
    def test_2xx_3xx_and_4xx_are_alive(self, tmp_path: Path, code: str) -> None:
        """A redirect to sign-in and a 401 both prove the stack is serving."""
        r, out = _run(tmp_path, "probe", FAKE=f"{code}:0")
        assert r.returncode == 0, r.stdout + r.stderr
        healthy, report = _verdict(out)
        assert healthy == "1", report
        assert report.count(f"OK (HTTP {code})") == 2, report
        assert _curls(tmp_path) == 2, "a live answer must not retry"

    @pytest.mark.parametrize("code", ["500", "502", "503"])
    def test_a_5xx_is_an_outage(self, tmp_path: Path, code: str) -> None:
        r, out = _run(tmp_path, "probe", FAKE=f"{code}:0")
        assert r.returncode == 0, r.stdout + r.stderr
        healthy, report = _verdict(out)
        assert healthy == "0", report
        assert report.count(f"SERVING ERRORS (HTTP {code})") == 2, report
        assert _curls(tmp_path) == 6

    @pytest.mark.parametrize(
        "spec", ["000000:0", "000000:7", "601:0", "100:0", "abc:0", "2000:0", "20:0"]
    )
    def test_a_code_nobody_foresaw_is_never_ok(self, tmp_path: Path, spec: str) -> None:
        """The ALLOWLIST. This holds even if the curl line goes wrong again:
        `000000` itself must read as down, not fall through to OK."""
        r, out = _run(tmp_path, "probe", FAKE=spec)
        assert r.returncode == 0, r.stdout + r.stderr
        healthy, report = _verdict(out)
        assert healthy == "0", f"{spec!r} read as HEALTHY:\n{report}"
        assert "OK" not in report, report

    def test_one_target_down_is_an_outage(self, tmp_path: Path) -> None:
        r, out = _run(tmp_path, "probe", FAKE="000:7", FAKE_WB="200:0")
        assert r.returncode == 0, r.stdout + r.stderr
        healthy, report = _verdict(out)
        assert healthy == "0", report
        assert "**gateway**: UNREACHABLE" in report, report
        assert "**workbench**: OK (HTTP 200)" in report, report


# ── The exposure job: an answer is the failure ──────────────────────────────


@needs_bash
class TestTheExposureJob:
    def test_no_answer_is_never_ok(self, tmp_path: Path) -> None:
        """Not OK, and not red either. The probe job owns reachability."""
        r, _ = _run(tmp_path, "exposure", FAKE="000:7")
        assert r.returncode == 0, r.stdout + r.stderr
        assert not re.search(r"^OK", r.stdout, re.M), r.stdout
        assert "::warning::/version did not answer" in r.stdout
        _one_code_each(r.stdout)

    def test_a_5xx_is_never_ok(self, tmp_path: Path) -> None:
        r, _ = _run(tmp_path, "exposure", FAKE="502:0", FAKE_BODY="<html>502</html>")
        assert r.returncode == 0, r.stdout + r.stderr
        assert not re.search(r"^OK", r.stdout, re.M), r.stdout

    def test_a_public_schema_is_red(self, tmp_path: Path) -> None:
        r, _ = _run(tmp_path, "exposure", FAKE="200:0", FAKE_BODY='{"env":"prod"}')
        assert r.returncode == 1, r.stdout + r.stderr
        assert "::error::https://gw.test/openapi.json answers 200" in r.stdout

    def test_closed_routes_on_prod_pass(self, tmp_path: Path) -> None:
        r, _ = _run(tmp_path, "exposure", FAKE="404:0", FAKE_BODY='{"env":"prod"}')
        assert r.returncode == 0, r.stdout + r.stderr
        assert "OK      /openapi.json -> HTTP 404" in r.stdout
        assert "OK      /version reports prod" in r.stdout


# ── The delivery job: an unreadable /version is never OK ────────────────────


@pytest.fixture()
def repo(tmp_path: Path) -> tuple[Path, str]:
    """A git checkout with one commit, which plays origin/main."""
    r = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(r)], check=True)
    subprocess.run(
        ["git", "-C", str(r), "-c", "user.email=t@t", "-c", "user.name=t",
         "-c", "commit.gpgsign=false", "commit", "-q", "--allow-empty", "-m", "x"],
        check=True,
    )
    sha = subprocess.run(
        ["git", "-C", str(r), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    return r, sha


@needs_bash
@needs_git
class TestTheDeliveryJob:
    def test_no_answer_warns_and_never_says_ok(self, repo: tuple[Path, str]) -> None:
        cwd, _ = repo
        r, _ = _run(cwd, "delivery", FAKE=":7")
        assert r.returncode == 0, r.stdout + r.stderr
        assert "::warning::/version did not answer" in r.stdout
        assert "OK" not in r.stdout, r.stdout

    def test_an_error_page_warns_and_never_says_ok(self, repo: tuple[Path, str]) -> None:
        """curl without -f exits 0 on a 502 and prints Caddy's page."""
        cwd, _ = repo
        r, _ = _run(cwd, "delivery", FAKE="200:0", FAKE_BODY="<html>502 Bad Gateway</html>")
        assert r.returncode == 0, r.stdout + r.stderr
        assert "::warning::/version did not answer" in r.stdout
        assert "OK" not in r.stdout, r.stdout

    def test_the_serving_sha_passes(self, repo: tuple[Path, str]) -> None:
        cwd, sha = repo
        r, _ = _run(cwd, "delivery", FAKE_BODY=json.dumps({"sha": sha}))
        assert r.returncode == 0, r.stdout + r.stderr
        assert f"OK      serving {sha[:8]}" in r.stdout, r.stdout


# ── Static: no workflow or script prints its code twice ─────────────────────

# `-w '%{http_code}'` and `|| echo 000` on one logical line. Inside a `$(...)`
# that captures `000000` on a failed connect.
_IDIOM = re.compile(r"%\{http_code\}.*\|\|\s*echo\s+['\"]?000")

# A site this change could not fix, with the reason. The last test fails when
# an entry goes stale, so delete the entry in the change that fixes the line.
_KNOWN = {
    # deploy/ is OWNER-GATE (`deploy-write`). Its http_ok() accepts only one
    # real 3-digit status, so `000000` reads as DOWN there and fails safe.
    "deploy/hostinger/health-watchdog.sh": 2,
}


def _idiom_sites() -> dict[str, int]:
    hits: dict[str, int] = {}
    for top in (".github", "scripts", "deploy"):
        for p in sorted((ROOT / top).rglob("*")):
            if not p.is_file() or p.suffix not in {".yml", ".yaml", ".sh"}:
                continue
            text = p.read_text(encoding="utf-8", errors="replace").replace("\\\n", " ")
            n = sum(
                1 for ln in text.splitlines()
                if not ln.lstrip().startswith("#") and _IDIOM.search(ln)
            )
            if n:
                hits[p.relative_to(ROOT).as_posix()] = n
    return hits


def test_the_idiom_scan_sees_the_known_shape() -> None:
    assert _IDIOM.search(
        """code="$(curl -s -o /dev/null -m 40 -w '%{http_code}' "$url" 2>/dev/null || echo 000)\""""
    )
    assert not _IDIOM.search(
        """code="$(curl -s -o /dev/null -m 40 -w '%{http_code}' "$url" 2>/dev/null)" || true"""
    )
    assert not _IDIOM.search(
        """curl -s -o /dev/null -w 'github -> %{http_code}\\n' https://x || echo "egress FAILED\""""
    )


def test_no_workflow_or_script_prints_its_code_twice() -> None:
    hits = _idiom_sites()
    new = {k: v for k, v in hits.items() if k not in _KNOWN}
    assert not new, (
        f"`-w '%{{http_code}}' ... || echo 000` in {new}. On a failed connect that "
        "captures `000000`, and on 2026-10-03 it read a dead box as HEALTHY. "
        "Write `code=\"$(curl ...)\" || true` and then `[ -n \"$code\" ] || code=000`."
    )


def test_every_known_site_is_still_there() -> None:
    hits = _idiom_sites()
    stale = {k: (v, hits.get(k, 0)) for k, v in _KNOWN.items() if hits.get(k, 0) != v}
    assert not stale, (
        f"_KNOWN is stale {stale} (want, found). Delete the entry for a fixed file."
    )
