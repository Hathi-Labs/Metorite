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


def test_the_backup_service_must_load_its_credentials() -> None:
    """🔴 **The fence that REPLACED the managed-DB carve-out (H-132).**

    Until 2026-09-20 `vps_apply.sh` disabled acb-backup.timer on every apply to
    a `PG_MODE=local` box. That was right on 2026-08-17. The unit loaded no
    EnvironmentFile, defaulted to `PG_MODE=docker`, dumped the EMPTY local
    container, passed `--verify-restore` and forged a green restore point.

    The EnvironmentFile landed on 2026-09-19 and the carve-out came out. This
    test is what stops the hole reopening. Delete that line from the unit and
    the false green returns, with the timer now ARMED, which is worse than
    when it was disabled.
    """
    service = (_UNITS_DIR / "acb-backup.service").read_text(encoding="utf-8")
    assert "EnvironmentFile=/opt/acb/app/.env" in service, (
        "acb-backup.service must load /opt/acb/app/.env. Without it PG_MODE "
        "defaults to docker, the unit dumps the empty local container, and "
        "--verify-restore passes on nothing. See H-132."
    )


def test_no_timer_is_carved_out_of_the_enable_loop() -> None:
    """🔴 A timer shipped in the repo arrives SCHEDULED, with no exceptions.

    The carve-out this replaces reverted two hand-enables and told neither
    session. An operator reading `systemctl list-timers` after a deploy saw no
    backup timer and no reason why. To exclude a timer again, do not SHIP it.
    Shipping a unit and then disabling it from the loop meant to arm it is how
    a box ends up with a backup service and no schedule.

    ⚠️ Scoped to the timer loop on purpose. `vps_apply.sh` legitimately
    disables `acb-whatsapp-bridge` when `WHATSAPP_BRIDGE_ENABLED` is not 1.
    That is a feature flag turning a SERVICE off. It is not a timer unwired
    from its own enable loop.
    """
    text = _APPLY.read_text(encoding="utf-8")
    loop = text[text.index('for timer in "$APP_DIR"'):]
    loop = loop[: loop.index("\ndone")]
    assert "systemctl disable" not in loop, (
        "vps_apply.sh disables a timer it also ships. See H-132 - do not "
        "reintroduce a carve-out here. Stop shipping the unit instead."
    )
    assert "enable --now" in loop, "the timer loop no longer enables anything"


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


def test_the_scratch_database_fence_runs_in_ci_and_the_silent_drop_stays_gone() -> None:
    """🔴 Incident 2026-10-06: the deep verify's scratch drop failed every night
    behind `>/dev/null 2>&1 || true`. 22 copies filled the managed disk and the
    provider made the whole project read-only for about 2 hours.

    The real fence is `scripts/rehearse_verify_scratch.sh`, which runs the
    script against a real server (R8). This test keeps that fence wired into
    CI, and it refuses the exact silent-drop shape on any executable line.
    Mutation: put `|| true` back after a scratch drop, and this fails.
    """
    workflow = (_ROOT / ".github/workflows/pr-check.yml").read_text(encoding="utf-8")
    job = workflow[workflow.index("  backup-restore:"):]
    job = job[: job.index("\n  migrations:")]
    assert "bash scripts/rehearse_verify_scratch.sh" in job, (
        "the backup-restore job no longer runs the scratch-database rehearsal"
    )

    lines = _executable_lines(_ROOT / "scripts/backup_db.sh")
    for ln in lines:
        if "dropdb" in ln or "DROP DATABASE" in ln or "drop_scratch" in ln:
            assert "|| true" not in ln, f"a scratch drop is silenced: {ln.strip()!r}"
            assert "2>/dev/null" not in ln.replace("2> /dev/null", "2>/dev/null"), (
                f"a scratch drop discards its reason: {ln.strip()!r}"
            )
    assert any("WITH (FORCE)" in ln for ln in lines), "the scratch drop lost FORCE"
    assert any(
        "SET default_transaction_read_only = off" in ln for ln in lines
    ), "the scratch drop no longer works on a read-only server"
    assert any(ln.strip() == "scratch_re='^acb_verify_[0-9]+$'" for ln in lines), (
        "the scratch pattern changed. It decides what the sweep DROPS, so it "
        "must stay exact: ^acb_verify_[0-9]+$"
    )
    assert not any("like 'acb_verify_" in ln.lower() for ln in lines), (
        "LIKE 'acb_verify_%' is back. `_` is a LIKE wildcard, so it also "
        "matches databases that are not scratch copies"
    )


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


# ── The deep verify restores OFF the cluster (incident, 2026-10-07) ─────────
#
# The verify used to `createdb` and `pg_restore` INTO the production Supabase
# cluster every night. On a small compute size that write burst used up the
# disk I/O budget: checkpoints went from 270 s to over 900 s, and statements
# timed out across the instance for hours. It now restores into a throwaway
# container on the box. These pin the two halves of that: the static shape
# (no restore target but the container), and the behaviour when Docker is
# absent or broken (fail loudly, never fall back to the cluster).
#
# The executing cases run the REAL script with every external tool replaced
# by an exported bash function. Each stub writes its argv to a calls log, so
# the test sees exactly what the script asked the live cluster to do.

_BACKUP = _ROOT / "scripts/backup_db.sh"


def _joined_executable_lines(path: pathlib.Path) -> list[str]:
    """Executable lines with `\\` continuations joined, so a command split
    over three lines is checked as the one command it is."""
    out: list[str] = []
    buf = ""
    for ln in path.read_text(encoding="utf-8").splitlines():
        if not buf and (not ln.strip() or ln.strip().startswith("#")):
            continue
        if ln.rstrip().endswith("\\"):
            buf += ln.rstrip()[:-1] + " "
            continue
        out.append(buf + ln)
        buf = ""
    return out


def test_the_verify_never_restores_into_the_live_cluster() -> None:
    """🔴 Static half. No `createdb` and no CREATE DATABASE anywhere, and the
    only `pg_restore` with a target database runs INSIDE the verify container.
    Mutation: put back `pg createdb` or `pgi pg_restore ... -d "$SCRATCH"`,
    and this fails."""
    lines = _joined_executable_lines(_BACKUP)
    for ln in lines:
        assert "createdb" not in ln, f"backup_db.sh creates a database: {ln.strip()!r}"
        assert "CREATE DATABASE" not in ln.upper(), (
            f"backup_db.sh creates a database: {ln.strip()!r}"
        )
    restores = [ln for ln in lines if re.search(r"\bpg_restore\b", ln)]
    targeted = [ln for ln in restores if re.search(r"(?:\s-d\s|--dbname)", ln)]
    assert targeted, "no pg_restore with a target database: where does the verify restore?"
    for ln in targeted:
        assert 'docker exec -i "$verify_ctr" pg_restore' in ln, (
            "a pg_restore with a target database runs outside the verify "
            f"container, so it can reach the live cluster: {ln.strip()!r}"
        )
    for ln in restores:
        if re.search(r"\bpgi? pg_restore\b", ln):
            assert "--list" in ln, (
                f"the pg seam may only LIST an archive, never restore one: {ln.strip()!r}"
            )
    run = next(ln for ln in lines if "docker run" in ln)
    assert "--network none" in run, "the verify container must have no network"
    assert '--memory "$verify_memory"' in run, "the verify container must be capped"
    assert "POSTGRES_PASSWORD=" not in run.split("docker run", 1)[1], (
        "the throwaway password must reach docker run through the environment, "
        "never as an argument (argv is visible to every user on the box)"
    )


_STUBS = r"""
set -u
W="$(mktemp -d)"
export W
mkdir -p "$W/app" "$W/backups"
printf 'POSTGRES_USER=acb\n' > "$W/app/.env"
CALLS="$W/calls.log"
: > "$CALLS"
export CALLS
psql() {
  printf 'psql %s\n' "$*" >> "$CALLS"
  # First match wins. The table count goes first: it holds `select 1` too.
  case "$*" in
    *"from pg_class c"*) echo 7 ;;
    *"select 1"*) echo 1 ;;
    *"server_version_num"*) echo 17 ;;
    *"show server_version"*) echo 17.6 ;;
    *"datistemplate"*) echo acb ;;
    *"count(*) from pg_database"*) echo 0 ;;
  esac
  return 0
}
pg_dumpall() { printf 'pg_dumpall %s\n' "$*" >> "$CALLS"; echo "-- globals"; }
pg_dump() { printf 'pg_dump %s\n' "$*" >> "$CALLS"; echo "DUMP"; }
pg_restore() { printf 'pg_restore %s\n' "$*" >> "$CALLS"; cat > /dev/null; }
createdb() { printf 'createdb %s\n' "$*" >> "$CALLS"; }
dropdb() { printf 'dropdb %s\n' "$*" >> "$CALLS"; }
export -f psql pg_dumpall pg_dump pg_restore createdb dropdb
"""

_DOCKER = {
    # `command -v docker` fails, and so does any call that slips past it.
    "missing": r"""
command() {
  if [ "${1:-}" = "-v" ] && [ "${2:-}" = "docker" ]; then return 1; fi
  builtin command "$@"
}
docker() { printf 'docker %s\n' "$*" >> "$CALLS"; echo "docker: not found" >&2; return 127; }
export -f command docker
""",
    # Docker is on PATH, and its daemon refuses every call.
    "broken": r"""
docker() {
  printf 'docker %s\n' "$*" >> "$CALLS"
  echo "Cannot connect to the Docker daemon. Is the docker daemon running?" >&2
  return 1
}
export -f docker
""",
    # A daemon that runs the container and answers like Postgres inside it.
    "works": r"""
docker() {
  printf 'docker %s\n' "$*" >> "$CALLS"
  case "$1" in
    run) echo cid ;;
    rm) touch "$W/ctr_removed" ;;
    container)
      [ -f "$W/ctr_removed" ] && return 1
      echo true ;;
    exec)
      shift
      local stdin=0
      if [ "$1" = "-i" ]; then stdin=1; shift; fi
      shift
      case "$*" in
        *"from pg_class c"*) echo 7 ;;
        *"pg_available_extensions"*) echo vector ;;
      esac
      if [ "$stdin" = "1" ]; then cat > /dev/null; fi ;;
  esac
  return 0
}
export -f docker
""",
}


def _run_backup_verify(docker_mode: str) -> tuple[int, str, str, str]:
    """Run the REAL backup_db.sh --verify-restore with stubbed tools.
    Returns (exit code, stdout, stderr, calls log)."""
    import subprocess

    prog = (
        _STUBS
        + _DOCKER[docker_mode]
        + 'PG_MODE=local APP_DIR="$W/app" BACKUP_DIR="$W/backups" '
        + "bash scripts/backup_db.sh --verify-restore < /dev/null\n"
        + "rc=$?\n"
        + 'ls "$W"/backups/*/acb.dump >/dev/null 2>&1 && echo "DUMP-ON-DISK" >&2\n'
        + 'printf "\\n===CALLS===\\n" >&2\n'
        + 'cat "$CALLS" >&2\n'
        + 'rm -rf "$W"\n'
        + "exit $rc\n"
    )
    # cwd=_ROOT and a RELATIVE script path: a python-made Windows path does
    # not survive into every bash on PATH (see the APP_DB test above).
    run = subprocess.run(
        ["bash"], input=prog.encode(), capture_output=True, timeout=60, cwd=_ROOT
    )
    err = run.stderr.decode(errors="replace")
    err, _, calls = err.partition("\n===CALLS===\n")
    return run.returncode, run.stdout.decode(errors="replace"), err, calls


def _assert_nothing_reached_the_cluster(calls: str) -> None:
    for ln in calls.splitlines():
        assert not ln.startswith(("createdb", "dropdb")), f"the cluster got: {ln}"
        if ln.startswith("pg_restore"):
            assert "--list" in ln and " -d " not in ln, (
                f"a restore reached the live cluster: {ln}"
            )
        if ln.startswith("psql"):
            assert "CREATE DATABASE" not in ln.upper(), f"the cluster got: {ln}"


def test_a_missing_docker_fails_the_verify_and_keeps_the_dump() -> None:
    """🔴 No Docker means no verify, LOUDLY, and never a restore into the
    cluster instead. Mutation: make the docker check fall back to the old
    in-cluster restore, and the calls log shows it."""
    rc, out, err, calls = _run_backup_verify("missing")
    assert rc != 0, f"a verify with no Docker exited 0:\n{out}\n{err}"
    assert "needs Docker" in err, err
    assert "DUMP-ON-DISK" in err, "the dump must be on disk before the verify fails"
    assert "restore verified" not in out
    assert not any(ln.startswith("docker run") for ln in calls.splitlines())
    _assert_nothing_reached_the_cluster(calls)


def test_a_container_that_will_not_start_fails_the_verify() -> None:
    rc, out, err, calls = _run_backup_verify("broken")
    assert rc != 0, f"a verify whose container never started exited 0:\n{out}\n{err}"
    assert "the verify container did not start" in err, err
    assert "Cannot connect to the Docker daemon" in err, "the reason was swallowed"
    assert "DUMP-ON-DISK" in err
    assert "restore verified" not in out
    _assert_nothing_reached_the_cluster(calls)


def test_a_working_docker_verifies_in_the_container_only() -> None:
    """The happy path, executed: the restore goes to `docker exec -i
    acb-verify-...`, the cluster sees only reads, and the container is removed
    with its volume."""
    rc, out, err, calls = _run_backup_verify("works")
    assert rc == 0, f"exit {rc}:\n{out}\n{err}"
    assert "public tables: live=7 restored=7" in out, out
    assert "restore verified" in out, out
    lines = calls.splitlines()
    run = next(ln for ln in lines if ln.startswith("docker run"))
    assert "--network none" in run and "--memory 1g" in run, run
    assert "pgvector/pgvector:pg17" in run, "the image must follow the live major"
    assert "POSTGRES_PASSWORD=" not in run, "the password reached docker's argv"
    assert any(
        ln.startswith("docker exec -i acb-verify-") and " pg_restore " in ln
        and "-d verify" in ln and "--schema=public" in ln
        for ln in lines
    ), "the restore did not go to the verify container"
    assert any(ln.startswith("docker rm -f -v acb-verify-") for ln in lines), (
        "the verify container was not removed with its volume"
    )
    _assert_nothing_reached_the_cluster(calls)


# ── The pre-migration backup runs only when something is pending ────────────
#
# Seven deploys on 2026-10-07 each took a full pg_dump of the production
# cluster with no migration pending. These run the REAL apply_migrations.sh
# against a stubbed psql and a stubbed backup script.

_MIGR_STUBS = r"""
set -u
W="$(mktemp -d)"
export W
mkdir -p "$W/app/scripts" "$W/m"
printf -- '-- MARKER_A\nselect 1;\n' > "$W/m/02_a.sql"
printf -- '-- MARKER_B\nselect 2;\n' > "$W/m/03_b.sql"
printf -- '-- init only\n' > "$W/m/01_schema.sql"
cat > "$W/app/scripts/backup_db.sh" <<'SH'
echo "BACKUP-RAN" >> "$W/calls.log"
echo "-- BACKUP-RAN" >> "$W/applied.sql"
exit "${BACKUP_RC:-0}"
SH
: > "$W/calls.log"
: > "$W/applied.sql"
: > "$W/ledger"
psql() {
  printf 'psql %s\n' "$*" >> "$W/calls.log"
  case "$*" in
    *"FROM schema_migrations"*) cat "$W/ledger" ;;
    *) cat >> "$W/applied.sql" ;;
  esac
  return 0
}
export -f psql
sum() { sha256sum "$1" | cut -d" " -f1; }
"""


def _run_apply(ledger_setup: str, backup_rc: int = 0) -> tuple[int, str, str, str, str]:
    """Run the REAL apply_migrations.sh. Returns (exit code, stdout, stderr,
    calls log, the SQL psql received)."""
    import subprocess

    prog = (
        _MIGR_STUBS
        + ledger_setup
        + f"BACKUP_RC={backup_rc} PG_MODE=local APP_DIR=\"$W/app\" MIGRATIONS_DIR=\"$W/m\" "
        + "bash scripts/apply_migrations.sh < /dev/null\n"
        + "rc=$?\n"
        + 'printf "\\n===CALLS===\\n" >&2\n'
        + 'cat "$W/calls.log" >&2\n'
        + 'printf "\\n===SQL===\\n" >&2\n'
        + 'cat "$W/applied.sql" >&2\n'
        + 'rm -rf "$W"\n'
        + "exit $rc\n"
    )
    run = subprocess.run(
        ["bash"], input=prog.encode(), capture_output=True, timeout=60, cwd=_ROOT
    )
    err = run.stderr.decode(errors="replace")
    err, _, rest = err.partition("\n===CALLS===\n")
    calls, _, sql = rest.partition("\n===SQL===\n")
    return run.returncode, run.stdout.decode(errors="replace"), err, calls, sql


_ALL_RECORDED = (
    'printf "02_a.sql %s\\n03_b.sql %s\\n" "$(sum "$W/m/02_a.sql")" '
    '"$(sum "$W/m/03_b.sql")" > "$W/ledger"\n'
)
_ONE_PENDING = 'printf "02_a.sql %s\\n" "$(sum "$W/m/02_a.sql")" > "$W/ledger"\n'
_ONE_CHANGED = (
    'printf "02_a.sql %s\\n03_b.sql deadbeef\\n" "$(sum "$W/m/02_a.sql")" > "$W/ledger"\n'
)


def test_zero_pending_skips_the_pre_migration_backup() -> None:
    rc, out, err, calls, sql = _run_apply(_ALL_RECORDED)
    assert rc == 0, f"exit {rc}:\n{out}\n{err}"
    assert "No pending migrations — skipping the pre-migration backup" in out, out
    # pr-check.yml's ladder job greps this exact shape on its second run.
    assert "(0 applied, 2 already recorded)" in out, out
    assert "BACKUP-RAN" not in calls, "a backup ran with nothing pending"
    assert "MARKER_A" not in sql and "MARKER_B" not in sql


def test_a_pending_migration_still_requires_the_backup() -> None:
    rc, out, err, calls, sql = _run_apply(_ONE_PENDING)
    assert rc == 0, f"exit {rc}:\n{out}\n{err}"
    assert "BACKUP-RAN" in calls, "a migration was pending and no backup ran"
    assert sql.index("BACKUP-RAN") < sql.index("MARKER_B"), (
        "the backup must run BEFORE the migration"
    )
    assert "MARKER_B" in sql and "MARKER_A" not in sql
    assert "(1 applied, 1 already recorded)" in out, out


def test_a_changed_migration_counts_as_pending() -> None:
    """`changed` is pending too: the loop re-applies it, so the gate must
    back up first. One definition of pending, asked twice."""
    rc, out, err, calls, sql = _run_apply(_ONE_CHANGED)
    assert rc == 0, f"exit {rc}:\n{out}\n{err}"
    assert "BACKUP-RAN" in calls
    assert "MARKER_B" in sql


def test_a_failed_backup_still_blocks_a_pending_migration() -> None:
    """🔴 CLAUDE.md §3a rule 1: a production migration needs a completed
    pre-migration backup. The skip must never weaken the pending case."""
    rc, _out, err, calls, sql = _run_apply(_ONE_PENDING, backup_rc=1)
    assert rc != 0, "the backup failed and the migrations ran anyway"
    assert "BACKUP-RAN" in calls, "the backup was never attempted"
    assert "refusing to apply migrations" in err, err
    assert "MARKER_B" not in sql, "a migration reached psql after a failed backup"


def test_the_pending_count_and_the_apply_loop_share_one_definition() -> None:
    """No second definition of pending: both the count and the loop call
    `migration_state`, and only it calls `ledger_state`."""
    text = _MIGRATE.read_text(encoding="utf-8")
    code = [ln for ln in text.splitlines() if not ln.strip().startswith("#")]
    calls_ledger = [ln for ln in code if "ledger_state " in ln and "()" not in ln]
    assert len(calls_ledger) == 1 and "migration_state" not in calls_ledger[0], (
        f"ledger_state is asked outside migration_state: {calls_ledger}"
    )
    callers = [ln for ln in code if '"$(migration_state "$f")"' in ln]
    assert len(callers) == 2, f"expected the count and the loop to ask it: {callers}"
    assert text.index("No pending migrations") < text.index('BACKUP_SCRIPT="$APP_DIR'), (
        "the pending count must come BEFORE the backup"
    )
