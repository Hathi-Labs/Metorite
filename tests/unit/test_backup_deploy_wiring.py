"""Guards for BO-23's unit delivery: the backup timer must ride the LIVE path.

PR #380 wrote the systemd unit-sync loop into deploy/hostinger/deploy.sh — the
manual runbook script — while both automated delivery paths (the workflow's SSH
step and the box's acb-pull poller) execute scripts/vps_apply.sh. The merge went
green, the deploy went green, and the backup timer stayed unscheduled: the exact
"correction exists on paper" failure the WS-25 guards exist to catch, one file
over. These pin the loop into the file that actually runs, and keep the manual
copy from silently drifting away from it.

Idiom note (learned the hard way in test_meeting_bot_deploy_wiring): every
assertion here reads NON-COMMENT lines. A guard satisfied by prose certifies
the documentation, not the wiring.
"""
from __future__ import annotations

import pathlib
import re

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_APPLY = _ROOT / "scripts/vps_apply.sh"
_MANUAL = _ROOT / "deploy/hostinger/deploy.sh"
_UNITS_DIR = _ROOT / "deploy/hostinger"


def _executable_lines(path: pathlib.Path) -> list[str]:
    return [
        ln
        for ln in path.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]


def test_the_live_delivery_path_syncs_units_from_the_repo() -> None:
    """The loop must glob the units directory and install on change — in the
    file the workflow and the poller actually execute."""
    lines = _executable_lines(_APPLY)
    assert any(
        "deploy/hostinger/*.service" in ln and "for " in ln for ln in lines
    ), "vps_apply.sh must iterate the repo's unit files"
    assert any(
        "install -m 0644" in ln and "/etc/systemd/system/" in ln for ln in lines
    ), "vps_apply.sh must install changed units into /etc/systemd/system"


def test_the_live_delivery_path_enables_every_repo_timer() -> None:
    """`enable --now` over the timer glob — a timer added to the repo arrives
    scheduled, without anyone remembering it."""
    lines = _executable_lines(_APPLY)
    enabling = [ln for ln in lines if "enable --now" in ln]
    assert any(
        '"$(basename "$timer")"' in ln for ln in enabling
    ), "vps_apply.sh must enable timers from the glob, not from a hand list"


def test_the_backup_timer_is_excepted_on_managed_db_boxes() -> None:
    """The ONE exception to the glob rule, named (R7). A PG_MODE=local box —
    Postgres managed elsewhere, e.g. Supabase — must NOT arm the nightly local
    dump: acb-backup.service has no EnvironmentFile, so it defaults to the
    docker container and dumps the EMPTY local Postgres, which passes
    --verify-restore and forges a green restore point (PR #4 review round 1).
    The carve-out must actively DISABLE, not merely skip: a hand-enabled
    timer would otherwise survive every subsequent deploy."""
    lines = _executable_lines(_APPLY)
    assert any(
        "acb-backup.timer" in ln and "PG_MODE" in ln for ln in lines
    ), "the managed-DB carve-out for acb-backup.timer is gone"
    assert any(
        "disable --now" in ln and "acb-backup.timer" in ln for ln in lines
    ), "the carve-out must disable, not skip — a hand-enable would survive deploys"


def test_the_live_delivery_path_never_restarts_services_from_the_sync_loop() -> None:
    """The loop syncs FILES and enables TIMERS only. Service restarts belong to
    the script's dedicated steps; a `systemctl restart` keyed off the unit glob
    would bounce the gateway as a side effect of any unit edit."""
    text = _APPLY.read_text(encoding="utf-8")
    assert "==> Syncing systemd units (BO-23)" in text
    window = text[text.index("==> Syncing systemd units (BO-23)"):]
    window = window[: window.index("==> ", 10)]  # this step only
    assert "systemctl restart" not in window


def test_the_backup_units_exist_and_the_timer_has_a_schedule() -> None:
    service = (_UNITS_DIR / "acb-backup.service").read_text(encoding="utf-8")
    timer = (_UNITS_DIR / "acb-backup.timer").read_text(encoding="utf-8")
    assert "backup_db.sh" in service, "the service must run the backup script"
    assert "OnCalendar=" in timer, "a timer without a schedule schedules nothing"
    assert "WantedBy=timers.target" in timer, "unenableable timer: no [Install]"


def test_the_pg_seam_actually_reaches_docker_in_docker_mode() -> None:
    """EXECUTES the seam, does not read it. #380's first version of pg()/pgi()
    called the function's own name in the docker branch — infinite recursion, a
    bash segfault at the pre-migration gate, and every deploy after the merge
    silently stopped applying migrations while verify() blessed the old healthy
    services. CI never saw it because the rehearsal only runs PG_MODE=local.

    This runs the REAL function definitions from both scripts in docker mode
    against a stubbed `docker`, timeout-bound so a recursion fails fast instead
    of hanging the suite."""
    import subprocess

    for script in ("scripts/backup_db.sh", "scripts/restore_db.sh"):
        defs = "\n".join(
            ln
            for ln in (_ROOT / script).read_text(encoding="utf-8").splitlines()
            if ln.startswith(("pg()", "pgi()"))
        )
        assert defs, f"{script} lost its pg()/pgi() seam"
        prog = (
            "set -u\n"
            'docker() { printf "STUB %s\\n" "$*"; }\n'
            "PG_MODE=docker\nPG_CONTAINER=testc\n"
            f"{defs}\n"
            "pg echo one && pgi echo two\n"
        )
        # stdin as BYTES, not `-c` argv and not text mode: Windows argv quoting
        # mangles the embedded quotes on their way into MSYS bash, and text
        # mode rewrites \n to \r\n, which bash reads as `set -u\r`.
        run = subprocess.run(
            ["bash"], input=prog.encode(), capture_output=True, timeout=10
        )
        out = run.stdout.decode(errors="replace")
        err = run.stderr.decode(errors="replace")[:300]
        assert run.returncode == 0, f"{script}: seam crashed: {err}"
        assert "STUB exec testc echo one" in out, f"{script}: pg missed docker exec"
        assert "STUB exec -i testc echo two" in out, f"{script}: pgi missed -i"


def test_no_deploy_script_redirects_into_shared_tmp() -> None:
    """`fs.protected_regular=2` (Ubuntu default) forbids opening an existing
    file in a sticky world-writable dir owned by another user -- ROOT TOO. A
    fixed /tmp path therefore works until the first time the OTHER user runs
    the script, then fails forever. This has now bitten three times: the
    nightly backup's verify log (recorded in backup_db.sh), and on 2026-08-25
    BOTH of apply_migrations.sh's fixed paths in one deploy -- the lock probe
    read as "prelude REJECTED" and the apply loop died "Permission denied"
    at the redirect, holding a live migration at the gate. Per-run mktemp is
    the rule; this pins it for every deploy-path script.
    """
    for script in (
        "scripts/apply_migrations.sh",
        "scripts/backup_db.sh",
        "scripts/restore_db.sh",
        "scripts/vps_apply.sh",
    ):
        for ln in _executable_lines(_ROOT / script):
            assert ">/tmp/" not in ln.replace("> /tmp/", ">/tmp/"), (
                f"{script}: fixed /tmp redirect target: {ln.strip()!r} -- "
                "use a per-run mktemp file instead"
            )


def test_the_app_database_is_derived_from_env_and_never_excluded() -> None:
    """2026-08-25, live: a box provisioned Supabase-style names the app
    database `postgres` (POSTGRES_DB=postgres), and backup_db.sh's
    enumeration -- which excludes `postgres` as "the maintenance database" --
    dumped NOTHING there; the pre-migration gate then fail-closed a real
    deploy carrying migration 187. Two halves, both pinned:

    (a) the pg_database query keeps the `or datname = '$APP_DB'` clause, so
        the app database is enumerated even when it is named `postgres`;
    (b) APP_DB is derived by EXECUTING the script's real derivation block --
        DATABASE_URL's path component with the query string stripped, falling
        back to `acb` when the var is absent.
    """
    import subprocess

    text = (_ROOT / "scripts/backup_db.sh").read_text(encoding="utf-8")
    assert (
        "or datname = '$APP_DB'" in text
    ), "backup_db.sh's enumeration lost the app-DB inclusion clause"

    lines = text.splitlines()
    i = lines.index('APP_DB="acb"')
    j = next(
        k for k in range(i, len(lines)) if lines[k].startswith("# ── How we reach")
    )
    block = "\n".join(lines[i:j])

    # The env file is created INSIDE the bash program: a python-made Windows
    # path does not survive into every bash on PATH (WSL wants /mnt/c, MSYS
    # wants C:/), and a path that silently fails [ -f ] makes every case pass
    # by fallback -- which is exactly how the first cut of this test lied.
    for dsn, expected in (
        ("postgresql+psycopg://u:p@h:6543/postgres?sslmode=require", "postgres"),
        ("postgresql+psycopg://acb:pw@localhost:5432/acb", "acb"),
        (None, "acb"),
    ):
        write = (
            ""
            if dsn is None
            else 'printf \'DATABASE_URL=%s\\n\' "' + dsn + '" > "$ENV_FILE"\n'
        )
        prog = (
            "set -u\n"
            'ENV_FILE="$(mktemp)"\n'
            + write
            + block
            + "\n"
            + 'printf "APP_DB=%s\\n" "$APP_DB"\n'
            + 'rm -f "$ENV_FILE"\n'
        )
        run = subprocess.run(
            ["bash"], input=prog.encode(), capture_output=True, timeout=10
        )
        out = run.stdout.decode(errors="replace")
        assert run.returncode == 0, run.stderr.decode(errors="replace")[:300]
        assert f"APP_DB={expected}" in out, f"{dsn!r} -> {out!r}"


def test_the_manual_runbook_and_the_live_path_carry_the_same_loop() -> None:
    """deploy/hostinger/deploy.sh is the hand-run runbook and keeps its copy of
    the loop; this asserts BOTH copies stay functionally present so an edit
    that 'cleans up' either one fails loudly, naming the other. If this fires
    because the duplication is being retired: fine — make the survivor the
    file that BOTH automated paths execute (scripts/vps_apply.sh), then update
    this guard, in that order."""
    for path in (_APPLY, _MANUAL):
        lines = _executable_lines(path)
        assert any(
            "deploy/hostinger/*.timer" in ln and "for " in ln for ln in lines
        ), f"{path.name} lost the timer-sync loop"


# ── The deploy script is stdin, and stdin can be stolen ─────────────────────
#
# `deploy.yml` delivers the apply script as `ssh 'bash -s' < vps_apply.sh`, so
# the script IS the shell's stdin. Anything it runs that reads stdin swallows
# every line not yet parsed; bash then hits EOF and exits **0**, so the deploy
# reports success having skipped whatever came after.
#
# That happened on 2026-08-07 and it was invisible: six consecutive deploys
# went green while the box served an old bundle, because `verify()` health-
# checks the still-running PREVIOUS deployment and cannot tell it from a new
# one. `apply_migrations.sh`'s `pgi` is `docker exec -i` — the `-i` is what
# attaches stdin — and a newly added ledger query was the first call to reach
# it without piping its own input.

_MIGRATE = _ROOT / "scripts/apply_migrations.sh"


def test_the_migration_call_cannot_eat_the_rest_of_the_deploy_script() -> None:
    """The one line that made six deploys into no-ops."""
    line = next(
        ln for ln in _executable_lines(_APPLY)
        if "apply_migrations.sh" in ln
    )
    assert "< /dev/null" in line, (
        "vps_apply.sh is piped to `bash -s` on stdin. apply_migrations.sh runs "
        "`docker exec -i`, which DRAINS stdin — without `< /dev/null` it eats "
        "the rest of this script and the deploy silently stops here, exit 0."
    )


def test_the_workbench_rebuild_comes_after_the_migration_call() -> None:
    """Ordering is what makes the bug above catastrophic rather than cosmetic:
    everything a user can SEE is rebuilt after migrations, so a script that
    dies at migrations ships no UI at all while reporting success."""
    text = _APPLY.read_text(encoding="utf-8")
    assert text.index("apply_migrations.sh") < text.index(
        "Rebuilding + restarting workbench"
    )


def test_every_stdin_attaching_psql_call_supplies_its_own_input() -> None:
    """`pgi` is `docker exec -i`. Each call must either pipe into it, use a
    heredoc, or redirect from /dev/null — never inherit the caller's stdin."""
    lines = _MIGRATE.read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(lines):
        if "pgi psql" not in line or line.strip().startswith("#"):
            continue
        window = "\n".join(lines[max(0, i - 2): i + 4])
        # `2>/dev/null` is STDERR and proves nothing — the check must see a
        # pipe, a heredoc, or a redirect of fd 0 specifically. Matching any
        # "/dev/null" let the real bug back in under mutation.
        # Each guard is exact, because the loose versions both let the real
        # bug back in under mutation: bare "/dev/null" matched `2>/dev/null`
        # (stderr), and bare "|" matched `|| true`.
        piped_in = re.search(r"[^|]\|\s*pgi psql", window)
        heredoc = "<<" in window
        stdin_redirect = re.search(r"(?<!\d)<\s*/dev/null", window)
        assert (piped_in or heredoc or stdin_redirect), (
            f"line {i + 1} runs `docker exec -i` with the caller's stdin "
            f"attached:\n{window}"
        )
