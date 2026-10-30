"""`scripts/dev_up.sh` runs the whole app locally, and nothing leaves the machine.

Spec: `specs/engineering_practice.md` §1.2 · Metorite task LD-1.

The script starts the gateway and the web app with production's switches
(`scripts/dev_up/prod_flags.env`). Those switches include mail, WhatsApp, push,
invites and sign-up. These tests fail when a change lets one of them reach the
local stack ON, or lets the stack connect as a role that bypasses RLS.

Hermetic: `--print-env` and the `.env` refusal need no Docker, no database and
no network. Whether the stack comes up is answered by running it.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "dev_up.sh"
FLAGS = ROOT / "scripts" / "dev_up" / "prod_flags.env"
STAGING_REFRESH = ROOT / "scripts" / "staging_refresh.sh"
BODY = SCRIPT.read_text(encoding="utf-8")



def _bash() -> str | None:
    if os.name == "nt":
        # Git's bash, not WSL's bash.exe, as in test_daily_release.py.
        git_bash = Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Git/bin/bash.exe"
        return str(git_bash) if git_bash.is_file() else None
    return shutil.which("bash")


_BASH = _bash()
needs_bash = pytest.mark.skipif(_BASH is None, reason="needs bash")


def _held(text: str) -> str:
    match = re.search(r"^\s*(?:held|HELD)='([^']+)'", text, re.MULTILINE)
    assert match, "no held-switch pattern found"
    return match.group(1)


def _print_env(tmp_path: Path, flags: Path | None = None) -> dict[str, dict[str, str]]:
    """Run --print-env against an empty repo dir: no state, so slot 0."""
    env = {**os.environ, "DEV_UP_REPO": tmp_path.as_posix()}
    if flags is not None:
        env["DEV_UP_FLAGS_FILE"] = flags.as_posix()
    res = subprocess.run(
        [_BASH, SCRIPT.as_posix(), "--print-env"],
        capture_output=True, text=True, encoding="utf-8", timeout=60, env=env,
    )
    assert res.returncode == 0, res.stderr
    out: dict[str, dict[str, str]] = {}
    section = ""
    for line in res.stdout.splitlines():
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            out[section] = {}
        elif "=" in line and section:
            name, value = line.split("=", 1)
            out[section][name] = value
    return out


class TestOutboundStaysOff:
    def test_the_held_pattern_is_the_one_staging_uses(self):
        """One rule for "reaches a person". Two copies that drift would let
        the local stack send what staging holds back."""
        assert _held(BODY) == _held(STAGING_REFRESH.read_text(encoding="utf-8"))

    @needs_bash
    def test_every_held_switch_reaches_the_stack_off(self, tmp_path):
        env = _print_env(tmp_path)
        held = re.compile(_held(BODY))
        # Switch-shaped names only: EXECUTIVE_EMAILS matches EMAIL and is not a switch.
        switch = re.compile(re.search(r"^SWITCH='([^']+)'", BODY, re.MULTILINE).group(1))
        checked = 0
        for section in ("gateway", "web"):
            for name, value in env[section].items():
                if switch.search(name) and held.search(name):
                    checked += 1
                    assert value in {"false", "0"}, f"{section} gets {name}={value}"
        assert checked >= 10, "the flags file lost its outbound switches"

    @needs_bash
    def test_the_two_default_on_loops_are_off(self, tmp_path):
        """EMAIL_SYNC_ENABLED and WORKFLOW_SCHEDULER_ENABLED start unless told
        not to (test_launch_defang_kill_switches.py)."""
        gw = _print_env(tmp_path)["gateway"]
        assert gw["EMAIL_SYNC_ENABLED"] == "false"
        assert gw["WORKFLOW_SCHEDULER_ENABLED"] == "false"

    @needs_bash
    def test_no_key_or_secret_reaches_the_stack(self, tmp_path):
        env = _print_env(tmp_path)
        allowed = {"GATEWAY_INTERNAL_TOKEN", "DATABASE_URL", "REDIS_URL", "GATEWAY_BASE_URL"}
        for section, values in env.items():
            for name in values:
                if name in allowed:
                    continue
                assert not re.search(r"KEY|SECRET|TOKEN|PASSWORD|PASS|DSN|URL", name), (
                    f"{section} gets {name}")


class TestProductionParity:
    @needs_bash
    def test_the_gateway_connects_as_the_non_superuser_app_role(self, tmp_path):
        """A superuser skips RLS, so a local stack that connects as one hides
        every cross-tenant leak."""
        url = _print_env(tmp_path)["gateway"]["DATABASE_URL"]
        assert url.startswith("postgresql+psycopg://acb_app:"), url
        assert "@127.0.0.1:" in url

    @needs_bash
    def test_identity_cutover_is_on_as_in_production(self, tmp_path):
        """With the cutover off, access reads app_user unbound and RLS hides
        the row. The local stack then 403s where production works."""
        assert _print_env(tmp_path)["gateway"]["IDENTITY_CUTOVER"] == "true"

    def test_the_app_role_is_made_before_the_ladder_runs(self):
        """Migrations 222 and 230 grant to acb_app only when it exists."""
        build = BODY[BODY.index("build_database() {"):]
        assert build.index("ensure_app_role") < build.index("apply_migrations.sh")
        assert "NOBYPASSRLS" in BODY and "NOSUPERUSER" in BODY

    def test_the_rls_promotion_is_applied(self):
        for phase in ("01_add_columns", "02_backfill", "03_constraints", "04_policies"):
            assert phase in BODY

    def test_the_ladder_goes_through_the_deploy_replayer(self):
        """One replayer (scripts/dev_db.sh says why), run inside the container."""
        assert "bash /app/scripts/apply_migrations.sh" in BODY


class TestTheGuards:
    @needs_bash
    def test_it_refuses_a_checkout_with_a_dotenv(self, tmp_path):
        """The gateway reads .env itself (pydantic env_file), so a real key in
        it would reach the stack whatever env -i passes."""
        (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=x\n", encoding="utf-8")
        res = subprocess.run(
            [_BASH, SCRIPT.as_posix()], capture_output=True, text=True,
            encoding="utf-8", timeout=60,
            env={**os.environ, "DEV_UP_REPO": tmp_path.as_posix()},
        )
        assert res.returncode != 0
        assert ".env exists" in res.stderr

    def test_processes_start_with_a_clean_environment(self):
        """Both launch lines, the Windows one and the Linux one. A substring
        check on the whole file passed while launch() dropped the -i."""
        launch = BODY[BODY.index("launch() {"):BODY.index("stop_one() {")]
        assert 'exec env -i "${envs[@]}" "$@"' in launch
        assert 'exec setsid env -i "${envs[@]}" "$@"' in launch
        assert not re.search(r'exec (setsid )?env "\$\{envs', launch)

    @needs_bash
    def test_a_crlf_flags_file_still_reaches_the_stack(self, tmp_path):
        """A Windows clone checked the flags file out with CRLF. Every line
        failed the strict test, IDENTITY_CUTOVER never arrived, and the seed
        then failed with 403 (found by the LD-1 verifier)."""
        crlf = tmp_path / "flags.env"
        crlf.write_bytes(FLAGS.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
        repo = tmp_path / "repo"
        repo.mkdir()
        gw = _print_env(repo, flags=crlf)["gateway"]
        assert gw["IDENTITY_CUTOVER"] == "true"
        assert gw["REACH_ENABLED"] == "false"

    def test_the_flags_file_keeps_lf_endings_on_checkout(self):
        attrs = (ROOT / ".gitattributes").read_text(encoding="utf-8")
        assert re.search(r"^scripts/dev_up/\*\.env\s+text eol=lf", attrs, re.MULTILINE)

    def test_the_flags_file_holds_switches_only(self):
        lines = [ln for ln in FLAGS.read_text(encoding="utf-8").splitlines()
                 if ln and not ln.startswith("#")]
        assert lines
        for line in lines:
            assert re.fullmatch(r"(root|workbench):[A-Z0-9_]+=[A-Za-z0-9_.:-]{0,64}", line), line
            assert not re.search(r"KEY|SECRET|TOKEN|PASSWORD|URL", line.split("=")[0]), line

    def test_the_state_folder_is_ignored(self):
        """It holds the local gateway token."""
        assert ".dev_up/" in (ROOT / ".gitignore").read_text(encoding="utf-8")
