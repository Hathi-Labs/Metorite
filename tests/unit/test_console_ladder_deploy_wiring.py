"""H-24/H-25 — the Customer Console ladder must ride the LIVE delivery path.

`infra/customer_console/` is the Console's own schema ladder against its own
Supabase project (D34). `scripts/apply_migrations.sh` is bolted to the local
docker Postgres and cannot reach it, so the Console got a dedicated DSN-driven
applier — `scripts/apply_customer_console_migrations.sh` — and then **nothing
invoked it**. The deploy shipped Console CODE that expected a schema its
database did not have, and reported success. That is the board's own
"`platform_api` is on the box but inert".

CP-12 is the case that made it visible. Migration 009 creates the `operator`
tables; unapplied, `GET /operators` answers 500 rather than 404 (H-64).

Two properties, and they are the same two D47 named on the way in:
  1. the applier runs BEFORE the Console unit restarts — the R6 window, so old
     code never meets new schema;
  2. a missing DSN on a box that RUNS the Console fails the deploy rather than
     skipping. A silent skip is how four deploys once reported success while
     shipping nothing.

Idiom note, inherited from `test_backup_deploy_wiring`: every assertion reads
NON-COMMENT lines. A guard satisfied by prose certifies the documentation
rather than the wiring — which is precisely the failure BO-23 shipped.
"""

from __future__ import annotations

import pathlib

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_APPLY = _ROOT / "scripts/vps_apply.sh"
_APPLIER = _ROOT / "scripts/apply_customer_console_migrations.sh"
_PR_CHECK = _ROOT / ".github/workflows/pr-check.yml"

_APPLIER_NAME = "apply_customer_console_migrations.sh"
_UNIT = "acb-customer-console"


def _executable_lines(path: pathlib.Path) -> list[str]:
    return [
        ln
        for ln in path.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]


def test_the_live_delivery_path_applies_the_console_ladder() -> None:
    """The whole of H-24. Both automated paths — the workflow's SSH step and the
    box's acb-pull poller — execute `vps_apply.sh`, so the call must live THERE.
    `deploy/hostinger/deploy.sh` is the manual runbook; BO-23 shipped a fix into
    that file alone and the live path never ran it."""
    lines = _executable_lines(_APPLY)
    assert any(_APPLIER_NAME in ln for ln in lines), (
        "vps_apply.sh must invoke the Customer Console ladder applier"
    )


def test_the_console_ladder_is_applied_before_the_console_restarts() -> None:
    """R6: the deploy applies migrations BEFORE restarting services, so old code
    always meets new schema. Reversing these two lines is a one-keystroke edit
    that no runtime check would catch — the Console would simply serve 500s for
    the length of one deploy."""
    lines = _executable_lines(_APPLY)
    apply_at = next(i for i, ln in enumerate(lines) if _APPLIER_NAME in ln)
    restart_at = next(i for i, ln in enumerate(lines) if "systemctl restart" in ln and _UNIT in ln)
    assert apply_at < restart_at, (
        "the Console ladder must be applied BEFORE acb-customer-console is "
        "restarted (R6) — old code must never meet new schema"
    )


def test_the_console_ladder_call_does_not_drain_the_scripts_own_stdin() -> None:
    """`< /dev/null` is load-bearing, not tidiness. This whole file is delivered
    as `ssh 'bash -s' < vps_apply.sh`, so the script IS stdin; a psql call that
    attaches to stdin swallows every unread line and bash exits 0 having skipped
    the rest of the deploy. It bit on 2026-08-07 across six green deploys."""
    lines = _executable_lines(_APPLY)
    call = next(ln for ln in lines if _APPLIER_NAME in ln)
    assert "< /dev/null" in call, (
        "the Console applier call must redirect stdin from /dev/null, for the "
        "same reason the tenant applier call does"
    )


def test_a_console_box_with_no_dsn_fails_the_deploy() -> None:
    """The second half of H-24, and the half that is easy to lose. When the unit
    is enabled the Console is LIVE on this box, so an absent DSN is a
    misconfiguration rather than an absence — it must stop the deploy.

    The branch must reach `exit 1`. A bare `echo` here would restore exactly the
    silent skip this entry exists to close."""
    text = _APPLY.read_text(encoding="utf-8")
    assert _APPLIER_NAME in text
    window = text[text.index(_APPLIER_NAME) :]
    window = window[: window.index("==> ", 10)]  # this step only
    lines = [ln for ln in window.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    assert any("is-enabled" in ln and _UNIT in ln for ln in lines), (
        "the no-DSN branch must ask whether the Console actually runs here"
    )
    assert any(ln.strip() == "exit 1" for ln in lines), (
        "an enabled Console with no DSN must FAIL the deploy, never skip it"
    )


def test_the_console_service_is_restarted_and_failure_is_loud() -> None:
    """A `git reset --hard` moves files; it does not restart a running Python
    process. The BO-23 unit-sync loop deliberately installs unit FILES without
    restarting services, so without a dedicated step the Console keeps serving
    whatever code it started with — indefinitely."""
    lines = _executable_lines(_APPLY)
    assert any("systemctl restart" in ln and _UNIT in ln for ln in lines), (
        "vps_apply.sh must restart acb-customer-console after the ladder"
    )
    text = _APPLY.read_text(encoding="utf-8")
    window = text[text.index("Restarting the Customer Console") :]
    window = window[: window.index("==> ", 10)]
    assert "exit 1" in window, (
        "a Console that fails to come back must fail the deploy — a dead "
        "service behind a green deploy is the exact WS-25 failure mode"
    )


def test_the_applier_itself_still_fails_closed_on_an_unset_dsn() -> None:
    """The applier's own guard, which the wiring above relies on. `:?` makes an
    unset DSN a hard error rather than an empty string psql would take as a
    local-socket connection to some *other* database."""
    text = _APPLIER.read_text(encoding="utf-8")
    assert "CUSTOMER_CONSOLE_DATABASE_URL:?" in text
    assert "ON_ERROR_STOP=1" in text, (
        "without ON_ERROR_STOP psql reports success after a failed statement"
    )


def _require_bash_can_see(path: pathlib.Path) -> None:
    """Refuse to run the execution tests against a directory bash cannot read.

    ⚠️ Named rather than silent, because a suite that skips quietly is how this
    repo has been bitten before. Some Windows dev shells run bash in a sandbox
    that cannot reach `%TEMP%`; there, every assertion below would pass or fail
    for reasons having nothing to do with the deploy script. CI runs Linux with
    no such sandbox, so these EXECUTE there — which is where the fence must
    bite. If you are reading this skip locally, that is expected."""
    import subprocess

    probe = subprocess.run(
        ["bash"],
        input=f'test -d "{path.as_posix()}" && echo YES\n'.encode(),
        capture_output=True,
        timeout=20,
    )
    if b"YES" not in probe.stdout:
        import pytest

        pytest.skip(
            f"bash cannot read {path} (sandboxed shell) — the execution tests "
            "need a directory visible to both Python and bash; they run for "
            "real on CI's Linux runner"
        )


def _dsn_block() -> str:
    """The new step's shell, lifted from the file that actually runs it."""
    text = _APPLY.read_text(encoding="utf-8")
    start = text.index('CC_ENV="$APP_DIR')
    end = text.index('echo "==> Syncing Python deps"')
    return text[start:end].rstrip()


def _run_dsn_block(
    tmp_path, env_line: str | None, unit_enabled: bool, reachable: bool = True
) -> tuple[int, str]:
    """EXECUTE the branch, do not read it — the idiom test_backup_deploy_wiring
    learned the hard way. A source scan cannot tell a guard that works from one
    that is merely present."""
    import subprocess

    # Windows: pytest's tmp_path can carry an 8.3 short component (VIJAYR~1),
    # which MSYS bash cannot resolve — every path test would then pass for the
    # wrong reason, by finding no file at all. resolve() expands it.
    tmp_path = tmp_path.resolve()
    (tmp_path / "scripts").mkdir(parents=True, exist_ok=True)
    _require_bash_can_see(tmp_path)
    stub = tmp_path / "scripts" / _APPLIER_NAME
    stub.write_text(
        '#!/usr/bin/env bash\necho "APPLIED ${CUSTOMER_CONSOLE_DATABASE_URL}"\n',
        encoding="utf-8",
    )
    cc_dir = tmp_path / "apps" / "services" / "customer_console"
    cc_dir.mkdir(parents=True, exist_ok=True)
    if env_line is not None:
        (cc_dir / ".env").write_text(env_line + "\n", encoding="utf-8")

    # ⚠️ `psql` MUST be stubbed, exactly like `systemctl`. H-100 added a
    # reachability probe before the ladder, and without a stub these cases
    # would run REAL psql against the fake host in the fixture DSN — every one
    # of them would take the unreachable branch and pass or fail on whether
    # the CI runner has network, not on the wiring under test.
    #
    # `command -v psql` finds a shell function, so the stub satisfies the
    # probe's own guard too.
    prog = (
        "set -u\n"
        f"systemctl() {{ return {0 if unit_enabled else 1}; }}\n"
        f"psql() {{ return {0 if reachable else 2}; }}\n"
        f'APP_DIR="{tmp_path.as_posix()}"\n'
        f"{_dsn_block()}\n"
    )
    # stdin as BYTES and not text mode: text mode rewrites \n to \r\n, which
    # bash reads as `set -u\r`.
    run = subprocess.run(
        ["bash"],
        input=prog.encode(),
        capture_output=True,
        timeout=20,
        cwd=str(tmp_path),
    )
    return run.returncode, run.stdout.decode(errors="replace")


def test_a_dsn_on_the_box_actually_reaches_the_applier(tmp_path) -> None:
    code, out = _run_dsn_block(
        tmp_path,
        "CUSTOMER_CONSOLE_DATABASE_URL=postgresql+psycopg://u:p@h.supabase.co:5432/postgres",
        unit_enabled=False,
    )
    assert code == 0, out
    assert "APPLIED postgresql+psycopg://u:p@h.supabase.co:5432/postgres" in out, out


def test_an_unreachable_dsn_does_not_abort_a_box_without_the_console(
    tmp_path,
) -> None:
    """🔴 H-100, and the whole point of the fourth case.

    The owner deleted the Console Supabase project on 2026-09-02 and the
    console `.env` still named it. The DSN was PRESENT, so the old code ran
    the ladder, `psql` failed under ``set -e``, and that killed the rest of
    `vps_apply.sh` — including the workbench rebuild ~360 lines below.

    A box that does not serve the Console must not lose its TENANT deploy
    because a Console database is gone. Exit 0, and say why.
    """
    code, out = _run_dsn_block(
        tmp_path,
        "CUSTOMER_CONSOLE_DATABASE_URL=postgresql+psycopg://u:p@dead.example:5432/postgres",
        unit_enabled=False,
        reachable=False,
    )
    assert code == 0, out
    assert "UNREACHABLE" in out, out
    assert "APPLIED" not in out, out


def test_an_unreachable_dsn_fails_when_the_console_is_enabled(tmp_path) -> None:
    """The other half. A Console that IS served must not come up against an
    unmigrated schema, so this stays fail-closed — same as the no-DSN case."""
    code, out = _run_dsn_block(
        tmp_path,
        "CUSTOMER_CONSOLE_DATABASE_URL=postgresql+psycopg://u:p@dead.example:5432/postgres",
        unit_enabled=True,
        reachable=False,
    )
    assert code == 1, out
    assert "UNREACHABLE" in out, out
    assert "APPLIED" not in out, out


def test_the_probe_does_not_print_the_dsn(tmp_path) -> None:
    """🔴 `psql` quotes the whole connection string, password included, when it
    cannot connect. That is how a tenant credential reached a transcript on
    2026-09-02 (H-97). The diagnostic names the FILE, never the value."""
    dsn = "postgresql+psycopg://u:hunter2@dead.example:5432/postgres"
    code, out = _run_dsn_block(
        tmp_path,
        f"CUSTOMER_CONSOLE_DATABASE_URL={dsn}",
        unit_enabled=False,
        reachable=False,
    )
    assert code == 0, out
    assert "hunter2" not in out, out
    assert dsn not in out, out


def test_a_quoted_dsn_is_unwrapped_before_psql_sees_it(tmp_path) -> None:
    """systemd's EnvironmentFile accepts quotes. psql does not strip them, so an
    unstripped value becomes a hostname lookup for `"postgresql`."""
    code, out = _run_dsn_block(
        tmp_path,
        'CUSTOMER_CONSOLE_DATABASE_URL="postgresql://u:p@h:5432/postgres"',
        unit_enabled=False,
    )
    assert code == 0, out
    assert "APPLIED postgresql://u:p@h:5432/postgres" in out, out


def test_an_enabled_console_with_no_dsn_stops_the_deploy(tmp_path) -> None:
    """The fail-closed half of H-24, executed. The Console is live on this box
    and its schema cannot be reached — shipping on would serve new code against
    an unmigrated database."""
    code, out = _run_dsn_block(tmp_path, None, unit_enabled=True)
    assert code == 1, f"expected the deploy to fail, got {code}: {out}"
    assert "Refusing to continue" in out, out


def test_a_box_without_a_console_still_deploys(tmp_path) -> None:
    """The deliberate carve-out (R7, named). H-24 read literally would brick
    every tenant deploy on a box that runs no Console. The skip is LOUD, which
    is the property that separates it from the silent skip H-24 closes."""
    code, out = _run_dsn_block(tmp_path, None, unit_enabled=False)
    assert code == 0, out
    assert "SKIPPED" in out, out
    assert "APPLIED" not in out, out


def test_ci_replays_the_console_ladder_more_than_once() -> None:
    """H-25. The applier's header ASSERTS every ladder file is additive and
    re-runnable, and nothing checked it. That matters more here than for the
    tenant ladder: this applier keeps NO ledger, so it re-applies every file on
    every deploy. 'Idempotent' is not a nicety, it is the only thing that makes
    the second deploy safe."""
    text = _PR_CHECK.read_text(encoding="utf-8")
    assert _APPLIER_NAME in text, "pr-check.yml must replay the Console ladder — H-25"
    window = text[text.index(_APPLIER_NAME) - 2000 : text.index(_APPLIER_NAME) + 2000]
    assert "for i in" in window or "second run" in window, (
        "a single application proves nothing about idempotency — replay it"
    )
