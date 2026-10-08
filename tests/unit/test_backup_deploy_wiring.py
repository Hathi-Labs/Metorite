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

import pytest

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


#: The host env must not reach a script under test. 🔴 Learned on PR #729:
#: the CI `test` job exports CUSTOMER_CONSOLE_DATABASE_URL for the whole unit
#: suite (pr-check.yml), backup_db.sh read it, and a Console dump appeared in
#: the off-box happy path on Linux only. Each name below is one the backup and
#: migration scripts read, one that changes how bash or a tool runs, or a knob
#: of these tests. A test that wants one sets it in its own program.
_HOST_ENV_PREFIXES = (
    "BACKUP_", "CUSTOMER_CONSOLE_", "CONSOLE_", "PG", "KEEP_", "RCLONE_",
    "ZSTD_", "GNUPG", "DOCKER_", "STUB_", "MIGRATION", "SKIP_", "TAR_",
    "BASH_ENV", "BASH_FUNC_", "TENANT_LADDER_",
)
_HOST_ENV_NAMES = frozenset({
    "ENV", "CDPATH", "GLOBIGNORE", "DATABASE_URL", "APP_DIR", "KEY_MODE",
    "KEY_VALIDITY", "VOL_DIR", "COUNT_RC", "RESTORE_FAILS", "RM_FAILS",
    "GZIP", "SECONDS", "CALLS", "W", "S3",
})


def _hermetic_env() -> dict[str, str]:
    """os.environ without any name that could steer a script under test."""
    import os

    return {
        k: v for k, v in os.environ.items()
        if k.upper() not in _HOST_ENV_NAMES and not k.upper().startswith(_HOST_ENV_PREFIXES)
    }


def test_the_harness_env_is_hermetic(monkeypatch: pytest.MonkeyPatch) -> None:
    """The companion of the scrub: a name the CI job exports must not survive."""
    monkeypatch.setenv("CUSTOMER_CONSOLE_DATABASE_URL", "postgresql://leak@ci/x")
    monkeypatch.setenv("BACKUP_S3_BUCKET", "leak")
    monkeypatch.setenv("PGHOST", "leak")
    env = _hermetic_env()
    assert not {"CUSTOMER_CONSOLE_DATABASE_URL", "BACKUP_S3_BUCKET", "PGHOST"} & set(env)
    assert "PATH" in env or "Path" in env, "the scrub removed PATH"


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
            ["bash"], input=prog.encode(), capture_output=True, timeout=10,
            env=_hermetic_env(),
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
            ["bash"], input=prog.encode(), capture_output=True, timeout=10,
            env=_hermetic_env(),
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
    *"from pg_class c"*)
      # COUNT_RC set: the live count query fails the way psql does when it
      # loses its connection (exit 2).
      if [ -n "${COUNT_RC:-}" ]; then
        echo "psql: error: server closed the connection unexpectedly" >&2
        return "$COUNT_RC"
      fi
      echo 7 ;;
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
    rm)
      # RM_FAILS set: the daemon refuses to remove the container.
      if [ -n "${RM_FAILS:-}" ]; then
        echo "Error response from daemon: removal in progress" >&2
        return 1
      fi
      touch "$W/ctr_removed" ;;
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
      if [ "$stdin" = "1" ]; then cat > /dev/null; fi
      # RESTORE_FAILS set: the restore inside the container fails.
      case "$*" in
        pg_restore*)
          if [ -n "${RESTORE_FAILS:-}" ]; then
            echo "pg_restore: error: could not execute query: ERROR:  boom"
            return 1
          fi ;;
      esac ;;
  esac
  return 0
}
export -f docker
""",
}


def _run_backup_verify(docker_mode: str, env: str = "") -> tuple[int, str, str, str]:
    """Run the REAL backup_db.sh --verify-restore with stubbed tools. `env` is
    a prefix of NAME=value pairs for the script.
    Returns (exit code, stdout, stderr, calls log)."""
    import subprocess

    prog = (
        _STUBS
        + _DOCKER[docker_mode]
        + env
        + ' PG_MODE=local APP_DIR="$W/app" BACKUP_DIR="$W/backups" '
        + "bash scripts/backup_db.sh --verify-restore < /dev/null\n"
        + "rc=$?\n"
        + 'ls "$W"/backups/*/acb.dump >/dev/null 2>&1 && echo "DUMP-ON-DISK" >&2\n'
        + 'ls "$W"/backups/*/customer_console.dump >/dev/null 2>&1 '
        + '&& echo "CONSOLE-DUMP-ON-DISK" >&2\n'
        + 'printf "\\n===CALLS===\\n" >&2\n'
        + 'cat "$CALLS" >&2\n'
        + 'rm -rf "$W"\n'
        + "exit $rc\n"
    )
    # cwd=_ROOT and a RELATIVE script path: a python-made Windows path does
    # not survive into every bash on PATH (see the APP_DB test above).
    run = subprocess.run(
        ["bash"], input=prog.encode(), capture_output=True, timeout=60, cwd=_ROOT,
        env=_hermetic_env(),
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


_CONSOLE_ENV = "CUSTOMER_CONSOLE_DATABASE_URL=postgresql://cc:pw@cc.example:5432/postgres"


@pytest.mark.parametrize(
    ("docker_mode", "env", "why"),
    [
        ("missing", "", "needs Docker"),
        ("broken", "", "the verify container did not start"),
        ("works", "RESTORE_FAILS=1", "pg_restore FAILED in the verify container"),
        # Fix round 2: psql exits 2 on a lost connection. Exit 2 once meant
        # "container not removed", so this read as a good dump.
        ("works", "COUNT_RC=2", "server closed the connection unexpectedly"),
    ],
)
def test_a_failed_verify_still_backs_up_the_console(docker_mode: str, env: str, why: str) -> None:
    """🔴 Fix round 1. A verify that fails must not cost the Console its
    backup (H-98: Console data was lost once with no backup). The run goes on
    to the Console dump, the off-box step and retention, and exits non-zero
    at the END. Mutation: make a failed verify `exit 1` on the spot again,
    and the Console dump and the retention line both go missing."""
    rc, out, err, calls = _run_backup_verify(docker_mode, f"{_CONSOLE_ENV} {env}")
    assert rc != 0, f"a failed verify exited 0:\n{out}\n{err}"
    assert why in err, err
    assert "DUMP-ON-DISK" in err
    assert "CONSOLE-DUMP-ON-DISK" in err, (
        f"the verify failed ({docker_mode}) and the Console database was not backed up"
    )
    assert any(
        ln.startswith("pg_dump -d postgresql://cc:pw@cc.example") for ln in calls.splitlines()
    ), "the Console dump never ran"
    assert "Retention (keeping" in out, "retention did not run after a failed verify"
    assert "the deep verify FAILED" in err, "the closing ERROR is missing"
    assert "restore verified" not in out
    assert "verify container could not be removed" not in err, (
        "a failed verify was reported as a container that would not go away"
    )
    _assert_nothing_reached_the_cluster(calls)


def test_a_container_that_will_not_go_is_reported_on_its_own() -> None:
    """The verify PASSED and the daemon refused to remove the container. That
    is its own ERROR and a non-zero exit, and it is NOT a failed verify. The
    subshell reports it through a marker file, never through its exit code."""
    rc, out, err, _calls = _run_backup_verify("works", "RM_FAILS=1")
    assert rc != 0, f"a container that would not go exited 0:\n{out}\n{err}"
    assert "restore verified" in out, out
    assert "could not remove the verify container" in err, err
    assert "the verify container could not be removed" in err, "no closing ERROR"
    assert "the deep verify FAILED" not in err, (
        "a passed verify was reported as failed because its container stayed"
    )


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
        ["bash"], input=prog.encode(), capture_output=True, timeout=60, cwd=_ROOT,
        env=_hermetic_env(),
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


# ── The off-box copy in Supabase Storage (H-123, owner decision 2026-10-08) ─
#
# backup_db.sh --offbox runs backup_offbox.sh under `timeout`. That compresses
# each item with zstd, encrypts it with gpg to the owner's PUBLIC key, and
# uploads it with rclone to <bucket>/<prefix>/<stamp>/. These run the REAL
# scripts with stub rclone, gpg, zstd, id and stat. The stub bucket is a
# directory, $S3, so a test can read exactly what went up and what was
# deleted. Each stub writes its argv to the calls log, and rclone also writes
# whether the secret reached it through the ENVIRONMENT.
# The real round trip (MinIO, real rclone and gpg) is scripts/rehearse_offbox.sh.

_OFFBOX_STUBS = r"""
S3="$W/s3"
mkdir -p "$S3" "$W/files/att"
export S3
printf 'resume of a candidate\n' > "$W/files/att/cv.txt"
KEYFPR="0123456789ABCDEF0123456789ABCDEF01234567"
export KEYFPR
printf 'PUBLIC KEY BLOCK\n' > "$W/pub.asc"
printf 'PRIVATE KEY BLOCK\n' > "$W/priv.asc"
# The root-only key file. `stat` and `id` are stubs, so the test controls
# what the guard sees: root, and a root:root 0600 file, unless a knob says not.
printf 'BACKUP_S3_BUCKET=metorite-backups\n' > "$W/backup-offbox.env"
export BACKUP_OFFBOX_ENV_FILE="$W/backup-offbox.env"
# No default of the script may reach a HOST path (/home/acb/.acb/agents, a
# real Docker volume). A test that wants other values sets them itself.
export BACKUP_FILE_DIRS="$W/files/att $W/files/missing"
export BACKUP_MEETING_BOT_VOLUME=stub-meeting-bot-data
id() {
  if [ "${1:-}" = "-u" ]; then echo "${STUB_UID:-0}"; return 0; fi
  command id "$@"
}
stat() { printf '%s\n' "${STUB_KEYFILE_STAT:-0:0 600}"; }
df() {
  printf 'Filesystem 1024-blocks Used Available Capacity Mounted on\n'
  printf 'stub 1000000000 1 %s 1%% /\n' "${STUB_DF_FREE_KB:-999999999}"
}
pg_dump() {
  printf 'pg_dump %s\n' "$*" >> "$CALLS"
  if [ -n "${STUB_CONSOLE_DUMP_FAILS:-}" ] && [[ "$*" == *"postgresql://"* ]]; then
    echo "pg_dump: error: connection failed for postgresql://cc:pw@cc.example" >&2
    echo "PARTIAL"
    return 1
  fi
  echo "DUMP"
}
gpgconf() { :; }
zstd() {
  printf 'zstd %s\n' "$*" >> "$CALLS"
  local f="" a
  for a in "$@"; do case "$a" in -*) ;; *) f="$a" ;; esac; done
  if [ -n "$f" ]; then cat "$f"; else cat; fi
}
gpg() {
  printf 'gpg %s\n' "$*" >> "$CALLS"
  local out="" a prev="" mode="" target=""
  for a in "$@"; do
    case "$prev" in --output) out="$a" ;; --list-keys) target="$a" ;; esac
    case "$a" in
      --encrypt) mode=enc ;;
      --decrypt) mode=dec ;;
      --list-keys) mode=list ;;
      show-only) mode=show ;;
      --import) if [ "$mode" != show ]; then mode=import; fi ;;
    esac
    prev="$a"
  done
  case "$mode" in
    show)
      case "${KEY_MODE:-public}" in
        garbage) return 2 ;;
        private) printf 'sec:u:255:22:AAAA:1::::::scESC:\n' ;;
        *) printf 'pub:-:255:22:AAAA:1::::::scESC:\n' ;;
      esac ;;
    import)
      if [ "${KEY_MODE:-public}" = garbage ]; then return 2; fi ;;
    list)
      if [ "$target" != "$KEYFPR" ]; then return 2; fi
      printf 'pub:%s:255:22:AAAA:1::::::scESC:\n' "${KEY_VALIDITY:--}"
      printf 'fpr:::::::::%s:\n' "$KEYFPR" ;;
    enc)
      cat > "$W/enc.in"
      if [ -n "${STUB_ENCRYPT_FAILS_ON:-}" ] && [ "$(basename "$out")" = "$STUB_ENCRYPT_FAILS_ON.zst.gpg" ]; then
        echo "gpg: encryption failed: Bad public key" >&2
        return 2
      fi
      { printf 'STUBGPG\n'; tr 'A-Za-z' 'N-ZA-Mn-za-m' < "$W/enc.in"; } > "$out" ;;
    dec)
      local f="${!#}"
      head -1 "$f" | grep -qx STUBGPG || return 2
      tail -n +2 "$f" | tr 'A-Za-z' 'N-ZA-Mn-za-m' ;;
  esac
  return 0
}
_s3p() { printf '%s' "$S3/${1#offbox:}"; }
rclone() {
  printf 'rclone %s\n' "$*" >> "$CALLS"
  local secret=unset
  if [ -n "${RCLONE_CONFIG_OFFBOX_SECRET_ACCESS_KEY:-}" ]; then secret=set; fi
  printf 'rclone-env endpoint=%s secret=%s config=%s dry_run=%s\n' \
    "${RCLONE_CONFIG_OFFBOX_ENDPOINT:-}" "$secret" "${RCLONE_CONFIG:-}" \
    "${RCLONE_DRY_RUN:-}" >> "$CALLS"
  if [ -n "${STUB_RCLONE_HANGS:-}" ]; then sleep 45; fi
  if [ -n "${STUB_UPLOAD_FAILS:-}" ]; then
    echo "ERROR : AccessDenied key=$RCLONE_CONFIG_OFFBOX_ACCESS_KEY_ID secret=$RCLONE_CONFIG_OFFBOX_SECRET_ACCESS_KEY" >&2
    return 1
  fi
  local pos=() a skip=0 flag="" excl="" dirs=0 rec=0 fmt=""
  for a in "$@"; do
    if [ "$skip" = 1 ]; then
      skip=0
      if [ "$flag" = --exclude ]; then excl="$a"; fi
      if [ "$flag" = --format ]; then fmt="$a"; fi
      continue
    fi
    case "$a" in
      --retries|--low-level-retries|--stats|--log-level|--exclude|--format) skip=1; flag="$a" ;;
      --dirs-only) dirs=1 ;;
      -R) rec=1 ;;
      -*) ;;
      *) pos+=("$a") ;;
    esac
  done
  if [ -n "${STUB_COPY_FAILS:-}" ] && [ "${pos[0]}" = copy ]; then
    echo "ERROR : SlowDown: please reduce your request rate" >&2
    return 1
  fi
  local src dst d x
  case "${pos[0]}" in
    copy)
      src="${pos[1]}"; dst="${pos[2]}"
      case "$src" in offbox:*) src="$(_s3p "$src")" ;; esac
      case "$dst" in offbox:*) dst="$(_s3p "$dst")" ;; esac
      mkdir -p "$dst"
      for x in "$src"/*; do
        if [ -f "$x" ] && [ "$(basename "$x")" != "$excl" ]; then cp "$x" "$dst/"; fi
      done ;;
    copyto)
      dst="$(_s3p "${pos[2]}")"
      mkdir -p "$(dirname "$dst")"
      cp "${pos[1]}" "$dst" ;;
    lsf)
      d="$(_s3p "${pos[1]}")"
      if [ "$dirs" = 1 ]; then
        for x in "$d"/*/; do if [ -d "$x" ]; then printf '%s/\n' "$(basename "$x")"; fi; done
      elif [ "$rec" = 1 ]; then
        if [ -d "$d" ]; then (cd "$d" && find . -type f | sed 's#^\./##'); fi
      elif [ "$fmt" = sp ]; then
        for x in "$d"/*; do if [ -f "$x" ]; then printf '%s;%s\n' "$(wc -c < "$x" | tr -d ' ')" "$(basename "$x")"; fi; done
      else
        for x in "$d"/*; do if [ -f "$x" ]; then basename "$x"; fi; done
      fi ;;
    delete)
      rm -rf "$(_s3p "${pos[1]}")" ;;
  esac
  return 0
}
docker() {
  printf 'docker %s\n' "$*" >> "$CALLS"
  if [ "${1:-} ${2:-}" = "volume inspect" ] && [ -n "${VOL_DIR:-}" ]; then
    echo "$VOL_DIR"
    return 0
  fi
  return 1
}
# A COMPLETE night in the stub bucket: it holds the completion mark.
plant_night() {
  mkdir -p "$S3/metorite-backups/nightly/$1"
  echo x > "$S3/metorite-backups/nightly/$1/acb.dump.zst.gpg"
  echo x > "$S3/metorite-backups/nightly/$1/SHA256SUMS.zst.gpg"
}
# An INCOMPLETE night: no SHA256SUMS.zst.gpg.
plant_partial() {
  mkdir -p "$S3/metorite-backups/nightly/$1"
  echo x > "$S3/metorite-backups/nightly/$1/acb.dump.zst.gpg"
}
# The clock of backup_db.sh. Its FIRST `date +%s` is the start of the run, and
# every later one reads STUB_ELAPSED seconds after it. Any other `date` is real.
date() {
  if [ "$#" = 1 ] && [ "$1" = "+%s" ]; then
    if [ -e "$W/clock" ]; then
      echo "$(( $(cat "$W/clock") + ${STUB_ELAPSED:-0} ))"
    else
      command date +%s | tee "$W/clock"
    fi
    return 0
  fi
  command date "$@"
}
export -f id stat df pg_dump gpgconf zstd gpg _s3p rclone docker date
"""

_S3_SECRET = "S3cr3tStubValue987"
_S3_KEY_ID = "AKIDSTUB123"
_S3_ENV = (
    "BACKUP_S3_ENDPOINT=https://ref.storage.supabase.co/storage/v1/s3 "
    "BACKUP_S3_REGION=ap-south-1 BACKUP_S3_BUCKET=metorite-backups "
    f"BACKUP_S3_ACCESS_KEY_ID={_S3_KEY_ID} BACKUP_S3_SECRET_ACCESS_KEY={_S3_SECRET} "
)
_GPG_ENV = 'BACKUP_GPG_RECIPIENT="$KEYFPR" BACKUP_GPG_PUBLIC_KEY_FILE="$W/pub.asc" '
_DIRS_ENV = 'BACKUP_FILE_DIRS="$W/files/att $W/files/missing" '
_FULL_ENV = _S3_ENV + _GPG_ENV + _DIRS_ENV
_NIGHT_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{6}Z")


def _run_backup_offbox(
    env: str, setup: str = "", after: str = "", flags: str = "--offbox", timeout: int = 120,
) -> dict[str, object]:
    """Run the REAL backup_db.sh (no deep verify) with the off-box stubs.

    `env` prefixes the script, `setup` runs before it, and `after` runs after
    it in the same shell (for the restore half). Returns the exit code, the
    output, the calls log, the stub bucket as {relative path: first line}, and
    the seconds the run took.
    """
    import subprocess
    import time

    prog = (
        _STUBS
        + _OFFBOX_STUBS
        + setup
        + env
        + ' PG_MODE=local APP_DIR="$W/app" BACKUP_DIR="$W/backups" '
        + f"bash scripts/backup_db.sh {flags} < /dev/null\n"
        + "rc=$?\n"
        + 'ls "$W"/backups/*/acb.dump >/dev/null 2>&1 && echo "DUMP-ON-DISK" >&2\n'
        + 'ls "$W"/backups/*/customer_console.dump >/dev/null 2>&1 '
        + '&& echo "CONSOLE-DUMP-ON-DISK" >&2\n'
        + 'ls -d "$W"/backups/*/offbox.work >/dev/null 2>&1 && echo "WORK-DIR-LEFT" >&2\n'
        + after
        + 'printf "\\n===CALLS===\\n" >&2\n'
        + 'cat "$CALLS" >&2\n'
        + 'printf "\\n===S3===\\n" >&2\n'
        + '(cd "$S3" && find . -type f | sed "s#^\\./##" | sort | while read -r f; do '
        + 'printf "%s\\t%s\\n" "$f" "$(head -1 "$f")"; done) >&2\n'
        + 'rm -rf "$W"\n'
        + "exit $rc\n"
    )
    t0 = time.monotonic()
    run = subprocess.run(
        ["bash"], input=prog.encode(), capture_output=True, timeout=timeout, cwd=_ROOT,
        env=_hermetic_env(),
    )
    took = time.monotonic() - t0
    err = run.stderr.decode(errors="replace")
    err, _, rest = err.partition("\n===CALLS===\n")
    calls, _, s3 = rest.partition("\n===S3===\n")
    bucket = dict(ln.split("\t", 1) for ln in s3.splitlines() if "\t" in ln)
    return {
        "rc": run.returncode,
        "out": run.stdout.decode(errors="replace"),
        "err": err,
        "calls": calls,
        "bucket": bucket,
        "took": took,
    }


def _rclone_calls(calls: object) -> list[str]:
    return [ln for ln in str(calls).splitlines() if ln.startswith("rclone ")]


def _encrypted(calls: object) -> bool:
    return any(
        ln.startswith("gpg ") and "--encrypt" in ln for ln in str(calls).splitlines()
    )


def _nights(bucket: object, *, complete: bool | None = None) -> list[str]:
    paths = [p for p in dict(bucket) if p.startswith("metorite-backups/nightly/")]  # type: ignore[call-overload]
    names = sorted({p.split("/")[2] for p in paths if _NIGHT_RE.fullmatch(p.split("/")[2])})
    if complete is None:
        return names
    done = {p.split("/")[2] for p in paths if p.endswith("/SHA256SUMS.zst.gpg")}
    return [n for n in names if (n in done) == complete]


def test_the_off_box_happy_path_uploads_only_encrypted_objects() -> None:
    """One night goes up under metorite-backups/nightly/<stamp>/. Every object
    is a .zst.gpg, every object is the stub's ciphertext, and SHA256SUMS goes
    up LAST, after the rest. Local retention runs BEFORE the off-box step."""
    r = _run_backup_offbox(_FULL_ENV + 'VOL_DIR="$W/files/att"')
    assert r["rc"] == 0, f"exit {r['rc']}:\n{r['out']}\n{r['err']}"
    out = str(r["out"])
    assert "off-box copy ok (offbox:metorite-backups/nightly/" in out, out
    assert out.index("Retention (keeping") < out.index("Copying off-box -> Supabase"), (
        "local retention must run BEFORE the off-box step, so a hung upload cannot cost it"
    )
    bucket: dict[str, str] = r["bucket"]  # type: ignore[assignment]
    names = sorted(p.split("/")[-1] for p in bucket)
    assert names == sorted([
        "acb.dump.zst.gpg", "globals.sql.zst.gpg", "MANIFEST.txt.zst.gpg",
        "files.tar.zst.gpg", "meeting-bot-volume.tar.zst.gpg", "SHA256SUMS.zst.gpg",
    ]), names
    for path, first in bucket.items():
        assert re.fullmatch(r"metorite-backups/nightly/\d{4}-\d{2}-\d{2}T\d{6}Z/[^/]+", path), path
        assert first == "STUBGPG", f"{path} went up as plaintext: {first!r}"
    rc = _rclone_calls(r["calls"])
    copy = next(i for i, ln in enumerate(rc) if " copy " in ln)
    sums = next(i for i, ln in enumerate(rc) if " copyto " in ln and "SHA256SUMS" in ln)
    assert copy < sums, "SHA256SUMS must go up last, as the mark of a complete night"
    assert "--exclude SHA256SUMS.zst.gpg" in rc[copy]
    assert "files/missing (no such directory)" in out
    assert "WORK-DIR-LEFT" not in str(r["err"]), "the staging directory was left on the box"
    assert not any(
        " stop " in ln for ln in str(r["calls"]).splitlines() if ln.startswith("docker")
    ), "the meeting bot must keep running while its volume is read"


def test_without_the_flag_nothing_uploads_whatever_the_env_holds() -> None:
    """🔴 F1. Every key is set, and no --offbox: nothing is encrypted, rclone
    never runs, and the bucket stays empty. This is the pre-migration backup of
    a deploy. Mutation: default `offbox_requested` to 1, and this fails."""
    for flags in ("", "--verify-restore"):
        r = _run_backup_offbox(_FULL_ENV, flags=flags, setup=_DOCKER["works"])
        assert r["rc"] == 0, f"{flags!r}: exit {r['rc']}:\n{r['out']}\n{r['err']}"
        assert "Only acb-backup.service passes --offbox" in str(r["out"]), r["out"]
        assert _rclone_calls(r["calls"]) == [], f"{flags!r}: rclone ran without --offbox"
        assert not _encrypted(r["calls"])
        assert r["bucket"] == {}


def test_an_unknown_argument_is_refused() -> None:
    r = _run_backup_offbox(_FULL_ENV, flags="--off-box")
    assert r["rc"] == 2, r["rc"]
    assert "unknown argument '--off-box'" in str(r["err"])
    assert _rclone_calls(r["calls"]) == []


def test_only_the_nightly_unit_passes_offbox() -> None:
    """🔴 F1 / P2. `--offbox` appears on ONE executable line in the deploy
    tree: the ExecStart of acb-backup.service. Every caller that runs the
    backup during a deploy (deploy.sh, vps_apply.sh, apply_migrations.sh)
    passes no such flag. The rehearsal is a test harness, and it may."""
    unit = (_UNITS_DIR / "acb-backup.service").read_text(encoding="utf-8")
    exec_start = [ln for ln in unit.splitlines() if ln.startswith("ExecStart=")]
    assert exec_start == [
        "ExecStart=/bin/bash /opt/acb/app/scripts/backup_db.sh --verify-restore --offbox"
    ], exec_start
    allowed = {"deploy/hostinger/acb-backup.service", "scripts/rehearse_offbox.sh",
               "scripts/backup_db.sh"}
    roots = [_ROOT / "scripts", _ROOT / "deploy", _ROOT / ".github", _ROOT / "infra"]
    for base in roots:
        for path in base.rglob("*"):
            if not path.is_file() or path.suffix not in {".sh", ".service", ".timer", ".yml", ".yaml", ""}:
                continue
            rel = path.relative_to(_ROOT).as_posix()
            text = path.read_text(encoding="utf-8", errors="replace")
            for ln in text.splitlines():
                if ln.strip().startswith("#") or "--offbox" not in ln:
                    continue
                assert rel in allowed, f"{rel} passes --offbox: {ln.strip()!r}"
    for caller in ("deploy/hostinger/deploy.sh", "scripts/vps_apply.sh", "scripts/apply_migrations.sh"):
        for ln in _executable_lines(_ROOT / caller):
            if "backup_db.sh" in ln or '"$BACKUP_SCRIPT"' in ln:
                assert "--offbox" not in ln, f"{caller} uploads: {ln.strip()!r}"
    migrate_call = [ln for ln in _executable_lines(_MIGRATE) if 'bash "$BACKUP_SCRIPT"' in ln]
    assert migrate_call, "apply_migrations.sh no longer calls the backup the way this fence reads"


def test_only_the_backup_unit_loads_the_offbox_key_file() -> None:
    """🔴 P1. The bucket key is PROJECT-WIDE. It lives in a root-only file
    that only acb-backup.service loads. acb-gateway (User=acb, with an
    in-process Copilot CLI that inherits its env) and the WhatsApp bridge
    must never load it."""
    key_file = "/etc/acb/backup-offbox.env"
    backup = (_UNITS_DIR / "acb-backup.service").read_text(encoding="utf-8")
    assert f"EnvironmentFile=-{key_file}" in backup
    for unit in sorted(_UNITS_DIR.glob("*.service")):
        if unit.name == "acb-backup.service":
            continue
        loaded = [
            ln for ln in unit.read_text(encoding="utf-8").splitlines()
            if ln.startswith("EnvironmentFile=") and "backup-offbox" in ln
        ]
        assert loaded == [], f"{unit.name} loads the off-box key file: {loaded}"
    for name in ("acb-gateway.service", "acb-whatsapp-bridge.service"):
        text = (_UNITS_DIR / name).read_text(encoding="utf-8")
        assert "backup-offbox" not in text, f"{name} names the off-box key file"


@pytest.mark.parametrize(
    ("setup", "env", "why"),
    [
        ("export STUB_UID=1000\n", "", "runs as uid 1000. Only root may hold the bucket key"),
        ("export STUB_KEYFILE_STAT='1000:1000 600'\n", "", "It must be '0:0 600'"),
        ("export STUB_KEYFILE_STAT='0:0 644'\n", "", "It must be '0:0 600'"),
        ("", 'BACKUP_OFFBOX_ENV_FILE="$W/nope.env" ', "does not exist. The bucket key belongs there"),
        (
            "mkdir -p \"$W/app\"; printf 'POSTGRES_USER=acb\\nBACKUP_S3_SECRET_ACCESS_KEY=x\\n' > \"$W/app/.env\"\n",
            "", "holds a BACKUP_S3_*, BACKUP_GPG_* or BACKUP_OFFBOX_ENV_FILE",
        ),
        (
            "mkdir -p \"$W/app\"; printf 'export BACKUP_GPG_RECIPIENT=x\\n' >> \"$W/app/.env\"\n",
            "", "holds a BACKUP_S3_*, BACKUP_GPG_* or BACKUP_OFFBOX_ENV_FILE",
        ),
        # The acb-writable .env must not choose the key file (round 2). The
        # stub file it names is root:root 0600, so only this check stops it.
        (
            "mkdir -p \"$W/app\"; printf 'BACKUP_OFFBOX_ENV_FILE=%s\\n' \"$W/backup-offbox.env\" >> \"$W/app/.env\"\n",
            "", "holds a BACKUP_S3_*, BACKUP_GPG_* or BACKUP_OFFBOX_ENV_FILE",
        ),
    ],
)
def test_the_bucket_key_must_be_root_only(setup: str, env: str, why: str) -> None:
    """🔴 P1. Not root, a key file that is not root:root 0600, no key file, or
    a key in /opt/acb/app/.env (the gateway's env): each is refused before any
    data is staged, and rclone never runs."""
    r = _run_backup_offbox(_FULL_ENV + env, setup=setup)
    assert r["rc"] != 0, f"exit 0:\n{r['out']}\n{r['err']}"
    assert why in str(r["err"]), r["err"]
    assert "Nothing was uploaded" in str(r["err"])
    assert _rclone_calls(r["calls"]) == []
    assert not _encrypted(r["calls"])
    assert r["bucket"] == {}
    assert "DUMP-ON-DISK" in str(r["err"])


@pytest.mark.parametrize(
    ("gpg_env", "setup", "why"),
    [
        ("", "", "BACKUP_GPG_RECIPIENT must be the full fingerprint"),
        ('BACKUP_GPG_RECIPIENT="$KEYFPR" ', "", "is not a readable file"),
        (
            'BACKUP_GPG_RECIPIENT="$KEYFPR" BACKUP_GPG_PUBLIC_KEY_FILE="$W/nope.asc" ',
            "", "is not a readable file",
        ),
        (_GPG_ENV, "export KEY_MODE=garbage\n", "holds no OpenPGP key"),
        (_GPG_ENV, "export KEY_MODE=private\n", "holds a PRIVATE key"),
        (_GPG_ENV, "export KEY_VALIDITY=r\n", "is revoked, expired or not valid"),
        (
            'BACKUP_GPG_RECIPIENT=FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF '
            'BACKUP_GPG_PUBLIC_KEY_FILE="$W/pub.asc" ',
            "", "has the exact fingerprint",
        ),
        ('BACKUP_GPG_RECIPIENT=alice@example.com BACKUP_GPG_PUBLIC_KEY_FILE="$W/pub.asc" ',
         "", "must be the full fingerprint"),
    ],
)
def test_a_missing_or_bad_key_fails_before_any_upload(gpg_env: str, setup: str, why: str) -> None:
    """🔴 S3 is configured and the key is missing or wrong: the step FAILS, and
    rclone is never called, so no plaintext can leave the box. The local dump
    stays, and local retention still runs. Mutation: make a key failure fall
    through to the upload, and the calls log shows rclone."""
    r = _run_backup_offbox(_S3_ENV + gpg_env + _DIRS_ENV, setup=setup)
    assert r["rc"] != 0, f"a bad key exited 0:\n{r['out']}\n{r['err']}"
    assert why in str(r["err"]), r["err"]
    assert "Nothing was uploaded" in str(r["err"])
    assert _rclone_calls(r["calls"]) == [], "rclone ran with no valid key"
    assert r["bucket"] == {}, f"something reached the bucket: {r['bucket']}"
    assert not _encrypted(r["calls"]), "the run encrypted data with a key it had not checked"
    assert "DUMP-ON-DISK" in str(r["err"])
    assert "Retention (keeping" in str(r["out"]), "local retention did not run"
    assert "the off-box copy to Supabase Storage FAILED" in str(r["err"])
    assert "WORK-DIR-LEFT" not in str(r["err"])


def test_a_half_configured_bucket_fails_and_says_what_is_missing() -> None:
    r = _run_backup_offbox("BACKUP_S3_BUCKET=metorite-backups " + _GPG_ENV)
    assert r["rc"] != 0
    assert "configured in part. Missing: BACKUP_S3_ENDPOINT" in str(r["err"]), r["err"]
    assert _rclone_calls(r["calls"]) == []


def test_an_encryption_failure_on_one_file_uploads_nothing() -> None:
    """🔴 F2 (mutant M10). gpg fails on globals.sql only. The night must not go
    up without it: rclone never runs, and the run exits 1. Mutation: put
    `|| true` after `--encrypt`, and the night goes up one file short, with a
    SHA256SUMS that says it is whole."""
    r = _run_backup_offbox(_FULL_ENV, setup="export STUB_ENCRYPT_FAILS_ON=globals.sql\n")
    assert r["rc"] != 0, f"exit 0:\n{r['out']}\n{r['err']}"
    assert "encryption failed" in str(r["err"]), r["err"]
    assert _rclone_calls(r["calls"]) == [], "a night went up with a file that did not encrypt"
    assert r["bucket"] == {}
    assert "the off-box copy to Supabase Storage FAILED" in str(r["err"])
    assert "WORK-DIR-LEFT" not in str(r["err"])


def test_a_failed_copy_never_marks_the_night_complete() -> None:
    """🔴 F3 (mutant M3). Only `rclone copy` fails, and lsf, copyto and delete
    work. The completion mark (SHA256SUMS) must NOT go up, the bucket must not
    be pruned, and the run exits 1. Mutation: ignore the failed copy, and the
    mark goes up over a night that holds nothing."""
    r = _run_backup_offbox(
        _FULL_ENV + "BACKUP_S3_KEEP=1 ",
        setup="export STUB_COPY_FAILS=1\nplant_night 2026-01-01T000000Z\n"
              "plant_night 2026-01-02T000000Z\n",
    )
    assert r["rc"] != 0, f"exit 0:\n{r['out']}\n{r['err']}"
    assert "the upload to offbox:metorite-backups/nightly/" in str(r["err"]), r["err"]
    rc = _rclone_calls(r["calls"])
    assert not any(" copyto " in ln for ln in rc), "the completion mark went up after a failed copy"
    assert not any(" delete " in ln for ln in rc), "the bucket was pruned after a failed copy"
    assert _nights(r["bucket"], complete=True) == ["2026-01-01T000000Z", "2026-01-02T000000Z"]
    assert "off-box copy ok" not in str(r["out"])


def test_an_upload_failure_keeps_the_console_dump_and_local_retention() -> None:
    """🔴 The upload fails. The Console dump and the local retention still
    run, the bucket retention does NOT run (a run of failed nights must never
    prune the last good copies), and the exit is non-zero at the END."""
    r = _run_backup_offbox(
        f"{_CONSOLE_ENV} " + _FULL_ENV,
        setup="export STUB_UPLOAD_FAILS=1\n",
    )
    assert r["rc"] != 0, f"a failed upload exited 0:\n{r['out']}\n{r['err']}"
    assert "the upload to offbox:metorite-backups/nightly/" in str(r["err"]), r["err"]
    assert "DUMP-ON-DISK" in str(r["err"])
    assert "CONSOLE-DUMP-ON-DISK" in str(r["err"]), "the Console dump did not run"
    assert "Retention (keeping" in str(r["out"]), "local retention did not run"
    assert "off-box copy ok" not in str(r["out"])
    assert "the off-box copy to Supabase Storage FAILED" in str(r["err"])
    assert not any(" delete " in ln for ln in _rclone_calls(r["calls"])), (
        "the bucket was pruned after a failed upload"
    )


def test_a_hung_upload_is_stopped_by_the_deadline() -> None:
    """🔴 P2. rclone hangs. `timeout` stops the whole off-box step at
    BACKUP_S3_TIMEOUT_SECS, the run reports it as a failed upload, and local
    retention had already run. Mutation: drop `timeout`, and the run waits
    for rclone and then reads as a success."""
    r = _run_backup_offbox(
        _FULL_ENV + "BACKUP_S3_TIMEOUT_SECS=3 ", setup="export STUB_RCLONE_HANGS=1\n",
        timeout=120,
    )
    assert r["rc"] != 0, f"a hung upload exited 0:\n{r['out']}\n{r['err']}"
    assert "did not finish in its 3s deadline" in str(r["err"]), r["err"]
    assert "Retention (keeping" in str(r["out"])
    assert "off-box copy ok" not in str(r["out"])
    assert "WORK-DIR-LEFT" not in str(r["err"]), "the staging directory outlived the timeout"


@pytest.mark.parametrize("value", ["", "abc", "-5", "1.5", "0", "1e3"])
def test_a_bad_timeout_is_a_failed_upload(value: str) -> None:
    r = _run_backup_offbox(_FULL_ENV + f"BACKUP_S3_TIMEOUT_SECS='{value}' ")
    assert r["rc"] != 0
    assert "BACKUP_S3_TIMEOUT_SECS" in str(r["err"]), r["err"]
    assert _rclone_calls(r["calls"]) == []


def test_the_unit_budget_is_one_value() -> None:
    """🔴 Round 2. backup_db.sh cuts the off-box deadline from the unit's
    TimeoutStartSec, so the two must be ONE number. And the 30 s that
    `timeout` waits before a KILL must land inside the margin it keeps."""
    text = _BACKUP.read_text(encoding="utf-8")
    script = int(re.search(r"^unit_timeout_secs=(\d+)$", text, re.M).group(1))  # type: ignore[union-attr]
    margin = int(re.search(r"^offbox_margin_secs=(\d+)$", text, re.M).group(1))  # type: ignore[union-attr]
    kill = int(re.search(r"timeout --kill-after=(\d+)", text).group(1))  # type: ignore[union-attr]
    unit = (_UNITS_DIR / "acb-backup.service").read_text(encoding="utf-8")
    start = int(re.search(r"^TimeoutStartSec=(\d+)$", unit, re.M).group(1))  # type: ignore[union-attr]
    assert script == start, f"backup_db.sh says {script}s and the unit says {start}s"
    assert kill < margin, (kill, margin)


@pytest.mark.parametrize(
    ("elapsed", "env", "want"),
    [
        # A fast run: the configured deadline wins.
        ("0", "BACKUP_S3_TIMEOUT_SECS=300 ", "deadline 300s (run at 0s of the unit's 1800s)"),
        # A slow dump, 25 min in: 1800 - 1500 - 60 = 240 s are left.
        ("1500", "", "deadline 240s (run at 1500s of the unit's 1800s)"),
    ],
)
def test_the_deadline_is_cut_to_the_time_left_in_the_unit(elapsed: str, env: str, want: str) -> None:
    """🔴 Round 2. The deadline is min(BACKUP_S3_TIMEOUT_SECS, 1800 -
    elapsed - 60). A fixed 1200 s after an 11 min dump outlives the unit, and
    systemd kills it with no ERROR line. Mutation: drop the cut, and the
    1500 s case reads 1200 s."""
    r = _run_backup_offbox(_FULL_ENV + env, setup=f"export STUB_ELAPSED={elapsed}\n")
    assert r["rc"] == 0, f"exit {r['rc']}:\n{r['out']}\n{r['err']}"
    assert want in str(r["out"]), r["out"]
    assert "off-box copy ok" in str(r["out"])


def test_too_little_time_left_skips_the_upload_as_a_failure() -> None:
    """🔴 Round 2. 1650 s in, only 90 s are left, under the 120 s floor. The
    upload does not start, rclone never runs, and the run exits 1 with an
    ERROR that says why."""
    r = _run_backup_offbox(_FULL_ENV, setup="export STUB_ELAPSED=1650\n")
    assert r["rc"] != 0, f"exit 0:\n{r['out']}\n{r['err']}"
    assert "only 90s are left in the unit's 1800s" in str(r["err"]), r["err"]
    assert "the off-box copy to Supabase Storage FAILED" in str(r["err"])
    assert _rclone_calls(r["calls"]) == []
    assert not _encrypted(r["calls"])
    assert "Retention (keeping" in str(r["out"])


def test_a_console_failure_still_sends_the_app_dump_off_box() -> None:
    """🔴 F8. The Console dump fails. The app dump still goes off the box, the
    failed Console file does NOT go up (it is renamed), the DSN in the error
    stays out of the log, and the run exits 1 at the END. Mutation: put the
    old `exit 1` back, and the off-box copy never runs."""
    r = _run_backup_offbox(
        f"{_CONSOLE_ENV} " + _FULL_ENV, setup="export STUB_CONSOLE_DUMP_FAILS=1\n",
    )
    assert r["rc"] != 0, f"exit 0:\n{r['out']}\n{r['err']}"
    assert "off-box copy ok" in str(r["out"]), "the app dump did not go off the box"
    assert "the Customer Console dump FAILED" in str(r["err"]), r["err"]
    names = {p.split("/")[-1] for p in dict(r["bucket"])}  # type: ignore[call-overload]
    assert "acb.dump.zst.gpg" in names
    assert not any(n.startswith("customer_console") for n in names), names
    assert "cc:pw@" not in str(r["out"]) + str(r["err"]), "the Console DSN reached the log"
    assert "CONSOLE-DUMP-ON-DISK" not in str(r["err"]), "a failed Console dump kept its good name"


def test_no_secret_reaches_argv_or_the_log() -> None:
    """The keys reach rclone through its ENVIRONMENT only. Neither key value is
    on any argv, in stdout, or in stderr, even when rclone prints them in an
    error. And a RCLONE_* value already in the env file cannot redirect the
    copy: the script unsets them first. RCLONE_DRY_RUN is the name that
    matters, because the script never sets it: inherited, it would turn every
    upload into a silent no-op."""
    for setup in ("export RCLONE_CONFIG_OFFBOX_ENDPOINT=https://attacker.example\n"
                  "export RCLONE_DRY_RUN=true\n",
                  "export STUB_UPLOAD_FAILS=1\n"):
        r = _run_backup_offbox(_FULL_ENV, setup=setup)
        text = f"{r['out']}\n{r['err']}\n{r['calls']}"
        assert _S3_SECRET not in text, "the secret access key leaked"
        assert _S3_KEY_ID not in text, "the access key id leaked"
        envs = [ln for ln in str(r["calls"]).splitlines() if ln.startswith("rclone-env ")]
        assert envs, "rclone never ran"
        for ln in envs:
            assert "secret=set" in ln, "the secret did not reach rclone through its env"
            assert "endpoint=https://ref.storage.supabase.co/storage/v1/s3" in ln, ln
            assert "config=/dev/null" in ln, "rclone may read a config file"
            assert ln.endswith("dry_run="), (
                "an inherited RCLONE_DRY_RUN reached rclone, so the upload is a no-op"
            )
    assert "secret=***" in str(r["err"]), "the rclone error was not shown, redacted"


def test_bucket_retention_counts_complete_nights_only() -> None:
    """🔴 P2 / F5. KEEP=2. Complete nights 01, 02 and 03, plus tonight: 01 and
    02 go. The incomplete night 00 is older than the oldest kept one, so it
    goes. The incomplete night 05 is the NEWEST incomplete one, so it stays
    (it may still be uploading). A folder that is not a night, another prefix
    and a sibling prefix all stay. Every delete argv names exactly
    offbox:<bucket>/nightly/<stamp>."""
    setup = (
        "plant_partial 2026-01-00T000000Z\n"
        "plant_night 2026-01-01T000000Z\nplant_night 2026-01-02T000000Z\n"
        "plant_night 2026-01-03T000000Z\nplant_partial 2026-01-05T000000Z\n"
        'mkdir -p "$S3/metorite-backups/nightly/not-a-stamp" "$S3/metorite-backups/other/2026-01-01T000000Z" '
        '"$S3/metorite-backups/nightly-old/2026-01-01T000000Z"\n'
        'for d in nightly/not-a-stamp other/2026-01-01T000000Z nightly-old/2026-01-01T000000Z; do '
        'echo x > "$S3/metorite-backups/$d/x"; done\n'
    )
    r = _run_backup_offbox(_FULL_ENV + "BACKUP_S3_KEEP=2 ", setup=setup)
    assert r["rc"] == 0, f"exit {r['rc']}:\n{r['out']}\n{r['err']}"
    complete = _nights(r["bucket"], complete=True)
    assert len(complete) == 2 and complete[0] == "2026-01-03T000000Z", complete
    assert _nights(r["bucket"], complete=False) == ["2026-01-05T000000Z"]
    bucket: dict[str, str] = r["bucket"]  # type: ignore[assignment]
    assert "metorite-backups/nightly/not-a-stamp/x" in bucket
    assert "metorite-backups/other/2026-01-01T000000Z/x" in bucket
    assert "metorite-backups/nightly-old/2026-01-01T000000Z/x" in bucket
    deletes = [ln for ln in _rclone_calls(r["calls"]) if " delete " in ln]
    assert sorted(ln.split()[-1].rsplit("/", 1)[1] for ln in deletes) == [
        "2026-01-00T000000Z", "2026-01-01T000000Z", "2026-01-02T000000Z",
    ], deletes
    for ln in deletes:
        assert re.fullmatch(
            r"offbox:metorite-backups/nightly/\d{4}-\d{2}-\d{2}T\d{6}Z", ln.split()[-1]
        ), ln


@pytest.mark.parametrize(("keep", "left"), [("08", 8), ("09", 9), ("010", 10)])
def test_keep_is_read_in_base_10(keep: str, left: int) -> None:
    """🔴 F4. "08" and "09" are bad OCTAL to bash, and "010" is 8. Read in
    base 10, they keep 8, 9 and 10 complete nights. 12 old nights plus
    tonight are planted."""
    setup = "".join(f"plant_night 2026-01-{d:02d}T000000Z\n" for d in range(1, 13))
    r = _run_backup_offbox(_FULL_ENV + f"BACKUP_S3_KEEP={keep} ", setup=setup)
    assert r["rc"] == 0, f"exit {r['rc']}:\n{r['out']}\n{r['err']}"
    assert len(_nights(r["bucket"], complete=True)) == left


@pytest.mark.parametrize("keep", ["", "abc", "-1", "0", "1.5", "14 ", "1234567890"])
def test_a_bad_keep_fails_before_any_upload(keep: str) -> None:
    r = _run_backup_offbox(_FULL_ENV + f"BACKUP_S3_KEEP='{keep}' ")
    assert r["rc"] != 0, f"BACKUP_S3_KEEP={keep!r} exited 0"
    assert "BACKUP_S3_KEEP" in str(r["err"]), r["err"]
    assert _rclone_calls(r["calls"]) == []


_LIB = "scripts/offbox_lib.sh"


def _run_lib(body: str) -> tuple[int, str, str]:
    """Source the REAL offbox_lib.sh with a stub rclone, then run `body`."""
    import subprocess

    prog = (
        "set -u\n"
        'CALLS="$(mktemp)"\n'
        'rclone() { printf "rclone %s\\n" "$*" >> "$CALLS"; }\n'
        f". {_LIB}\n"
        + body
        + '\nprintf "\\n===CALLS===\\n"\ncat "$CALLS"\nrm -f "$CALLS"\n'
    )
    run = subprocess.run(
        ["bash"], input=prog.encode(), capture_output=True, timeout=30, cwd=_ROOT,
        env=_hermetic_env(),
    )
    return run.returncode, run.stdout.decode(errors="replace"), run.stderr.decode(errors="replace")


def _plan(keep: int, complete: list[str], partial: list[str], extra: list[str] = ()) -> list[str]:  # type: ignore[assignment]
    lines = [f"{n}/acb.dump.zst.gpg" for n in complete + partial]
    lines += [f"{n}/SHA256SUMS.zst.gpg" for n in complete]
    lines += list(extra)
    body = "printf '%s\\n' " + " ".join(f"'{ln}'" for ln in lines) + f" | offbox_plan_prune {keep}\n"
    rc, out, err = _run_lib(body)
    assert rc == 0, err
    return [ln for ln in out.partition("===CALLS===")[0].splitlines() if ln.strip()]


_D = [f"2026-01-{d:02d}T000000Z" for d in range(1, 10)]


def test_the_prune_plan_keeps_complete_nights_and_the_newest_partial() -> None:
    """🔴 P2 / F5, the planner itself, which calls nothing."""
    # Older complete nights go. Keep 2 of 01, 03, 05.
    assert _plan(2, [_D[0], _D[2], _D[4]], []) == [_D[0]]
    # A partial older than the oldest kept goes. The newest partial stays,
    # even when it is OLDER than the oldest kept night.
    assert _plan(2, [_D[2], _D[4], _D[6]], [_D[0], _D[1]]) == [_D[0], _D[2]]
    assert _plan(2, [_D[2], _D[4], _D[6]], [_D[0]]) == [_D[2]]
    # A partial between the kept nights stays, and so does the newest partial.
    assert _plan(2, [_D[1], _D[3], _D[5]], [_D[0], _D[4], _D[8]]) == [_D[0], _D[1]]
    # No complete night: nothing goes, however many partials pile up.
    assert _plan(2, [], [_D[0], _D[1], _D[2]]) == []
    # Fewer complete nights than KEEP: only partials older than the oldest go.
    assert _plan(14, [_D[3], _D[5]], [_D[0], _D[1], _D[8]]) == [_D[0], _D[1]]
    # A name that is not a night is never planned, however old it reads.
    assert _plan(1, [_D[5]], [], ["0000-not-a-night/x", "nightly/x"]) == []


def test_the_prune_plan_keeps_one_night_per_day() -> None:
    """🔴 Round 2. Retention is BY UTC DAY. 14 complete runs on one day (each
    deploy, a hand run) must keep that day's NEWEST run only, and must never
    push the earlier days out. A partial run inside the day stays while it is
    newer than the oldest kept night, and the newest partial always stays.
    Mutation: keep the newest KEEP complete nights by count, and the five
    earlier days go."""
    earlier = [f"2026-01-0{d}T023000Z" for d in range(1, 6)]
    same_day = [f"2026-01-09T{h:02d}0000Z" for h in range(14)]
    partials = ["2026-01-09T005000Z", "2026-01-09T140000Z"]
    gone = _plan(14, earlier + same_day, partials)
    assert gone == same_day[:-1], gone
    # KEEP=3: the same day, then the two newest earlier days.
    gone = _plan(3, earlier + same_day, partials)
    assert gone == earlier[:3] + same_day[:-1], gone
    # Two days, three runs each, KEEP=1: only the newest run of the newest day.
    two = ["2026-01-01T010000Z", "2026-01-01T020000Z", "2026-01-01T030000Z",
           "2026-01-02T010000Z", "2026-01-02T020000Z", "2026-01-02T030000Z"]
    assert _plan(1, two, []) == two[:-1]


@pytest.mark.parametrize(("value", "want"), [("08", "8"), ("09", "9"), ("010", "10"), ("14", "14"), ("0", "0")])
def test_offbox_uint_reads_base_10(value: str, want: str) -> None:
    _rc, out, err = _run_lib(f"offbox_uint '{value}' || echo REFUSED\n")
    assert out.partition("===CALLS===")[0].strip() == want, (value, out, err)


@pytest.mark.parametrize("value", ["", "abc", "-1", "1.5", " 1", "1 ", "1234567890", "+3"])
def test_offbox_uint_refuses_what_is_not_a_whole_number(value: str) -> None:
    _rc, out, _err = _run_lib(f"offbox_uint '{value}' || echo REFUSED\n")
    assert out.partition("===CALLS===")[0].strip() == "REFUSED", (value, out)


@pytest.mark.parametrize(
    "night",
    ["", "..", "../other", "2026-10-01T000000Z/../../other", "*", "2026-10-01T000000Z/x",
     " 2026-10-01T000000Z", "2026-10-01T000000Z ", "2026-10-01", "nightly"],
)
def test_the_delete_guard_refuses_anything_but_a_night_stamp(night: str) -> None:
    """🔴 The ONE delete in the bucket. A value that is not exactly a night
    stamp is refused, and rclone is never called."""
    rc, out, err = _run_lib(
        "offbox_bucket=metorite-backups; offbox_prefix=nightly\n"
        f"offbox_delete_night '{night}' && echo DELETED || echo REFUSED\n"
    )
    assert rc == 0, err
    assert "REFUSED" in out, f"{night!r} was not refused: {out}"
    assert "rclone " not in out.partition("===CALLS===")[2], f"{night!r} reached rclone"
    assert "refusing to delete" in err


@pytest.mark.parametrize(
    "prefix", ["", "/", "..", "../x", "a/../b", "a//b", "./a", "a/.", "a b", "*", "a/*"]
)
def test_the_delete_guard_refuses_an_unsafe_prefix(prefix: str) -> None:
    rc, out, err = _run_lib(
        f"offbox_bucket=metorite-backups; offbox_prefix='{prefix}'\n"
        "offbox_delete_night 2026-10-01T000000Z && echo DELETED || echo REFUSED\n"
        f"offbox_prefix_ok '{prefix}' && echo PREFIX-OK || echo PREFIX-BAD\n"
    )
    assert rc == 0, err
    assert "REFUSED" in out and "PREFIX-BAD" in out, f"{prefix!r}: {out}"
    assert "rclone " not in out.partition("===CALLS===")[2]


def test_the_delete_guard_deletes_exactly_one_night() -> None:
    """The companion: a guard that refused everything would pass the two
    tests above, and the bucket would grow without end."""
    rc, out, err = _run_lib(
        "offbox_bucket=metorite-backups; offbox_prefix=metorite/nightly\n"
        "offbox_delete_night 2026-10-01T023012Z && echo DELETED || echo REFUSED\n"
    )
    assert rc == 0, err
    assert "DELETED" in out, out
    calls = out.partition("===CALLS===")[2].strip().splitlines()
    assert len(calls) == 1, calls
    assert calls[0].endswith(" delete --rmdirs offbox:metorite-backups/metorite/nightly/2026-10-01T023012Z"), calls


_RESTORE = (
    'bash scripts/restore_offbox.sh --keyring-parent "$W" --key "$W/priv.asc" '
)


def _restore_step(tag: str, args: str) -> str:
    return (
        _S3_ENV + _RESTORE + args + f' < /dev/null > "$W/{tag}.txt" 2>&1\n'
        f'echo "{tag}-RC=$?" >&2; cat "$W/{tag}.txt" >&2\n'
    )


# Rewrite a night's SHA256SUMS with the stub cipher, without one name.
_DROP_FROM_SUMS = (
    'n="$(ls "$S3"/metorite-backups/nightly/)"; d="$S3/metorite-backups/nightly/$n"\n'
    'tail -n +2 "$d/SHA256SUMS.zst.gpg" | tr "A-Za-z" "N-ZA-Mn-za-m" | grep -v "  {name}$" '
    '| {{ printf "STUBGPG\\n"; tr "A-Za-z" "N-ZA-Mn-za-m"; }} > "$W/sums.new"\n'
    'mv "$W/sums.new" "$d/SHA256SUMS.zst.gpg"\n'
)


def test_the_restore_downloads_decrypts_and_verifies_a_night() -> None:
    """The restore half, against the night the backup just uploaded. Then the
    same restore against a TAMPERED object must fail, and say why."""
    restore = (
        _S3_ENV + 'bash scripts/restore_offbox.sh --list < /dev/null > "$W/list.txt" 2>&1\n'
        'echo "LIST-RC=$?" >&2; cat "$W/list.txt" >&2\n'
        + _restore_step("RESTORE1", '--night latest --out "$W/restore"')
        + 'ls "$W"/restore/*/acb.dump >/dev/null 2>&1 && echo "RESTORED-DUMP" >&2\n'
        'grep -q "resume of a candidate" <(tar -xOf "$W"/restore/*/files.tar 2>/dev/null) '
        '&& echo "RESTORED-FILES" >&2\n'
        'obj="$(ls "$S3"/metorite-backups/nightly/*/globals.sql.zst.gpg)"\n'
        'printf "STUBGPG\\n-- tampered\\n" > "$obj"\n'
        + _restore_step("RESTORE2", '--night latest --out "$W/restore2"')
    )
    r = _run_backup_offbox(_FULL_ENV, after=restore)
    err = str(r["err"])
    assert r["rc"] == 0, f"exit {r['rc']}:\n{r['out']}\n{err}"
    assert "LIST-RC=0" in err and _NIGHT_RE.search(err.partition("LIST-RC=0")[2]), err
    assert "RESTORE1-RC=0" in err, err
    assert "SHA256SUMS lists every file" in err and "every dump matches MANIFEST.txt" in err
    assert "RESTORED-DUMP" in err and "RESTORED-FILES" in err, err
    assert "RESTORE2-RC=1" in err, "a tampered night was restored without an error"
    assert "does not match SHA256SUMS" in err, err
    assert _S3_SECRET not in err and _S3_SECRET not in str(r["out"])


@pytest.mark.parametrize(
    ("after", "args", "why"),
    [
        # A decrypted file that SHA256SUMS does not list.
        (_DROP_FROM_SUMS.format(name="globals.sql"), "", "SHA256SUMS does not list it"),
        # A night with no MANIFEST.txt is refused by default.
        (
            _DROP_FROM_SUMS.format(name="MANIFEST.txt")
            + 'rm -f "$d/MANIFEST.txt.zst.gpg"\n',
            "", "has no MANIFEST.txt",
        ),
        # Not enough room on the disk.
        ("export STUB_DF_FREE_KB=1\n", "", "Free some space, or pick another --out"),
        # The keyring may not go on a disk that is not writable memory.
        ("", '--keyring-parent "$W/no-such-ram-disk"', "is not a writable"),
    ],
    ids=["unlisted-file", "no-manifest", "disk-full", "no-ram-disk"],
)
def test_the_restore_refuses_a_night_it_cannot_trust(after: str, args: str, why: str) -> None:
    restore = after + _restore_step("R", f'--night latest --out "$W/r" {args}')
    r = _run_backup_offbox(_FULL_ENV, after=restore)
    err = str(r["err"])
    assert "R-RC=1" in err, err
    assert why in err, err


def test_the_restore_accepts_no_manifest_only_when_told() -> None:
    restore = (
        _DROP_FROM_SUMS.format(name="MANIFEST.txt")
        + 'rm -f "$d/MANIFEST.txt.zst.gpg"\n'
        + _restore_step("R", '--night latest --out "$W/r" --allow-no-manifest')
    )
    r = _run_backup_offbox(_FULL_ENV, after=restore)
    err = str(r["err"])
    assert "R-RC=0" in err, err
    assert "--allow-no-manifest accepts that" in err


def test_the_deploy_installs_rclone_and_the_migration_backup_never_uploads() -> None:
    """Two static halves.

    (a) vps_apply.sh installs rclone from apt when it is absent, with stdin
        closed, a wait on the dpkg lock, and an install that still runs when
        `apt-get update` fails.
    (b) The pre-migration backup runs with the keys that vps_apply.sh lifts
        from .env, and none of them is a BACKUP_ key.
    """
    lines = _executable_lines(_APPLY)
    assert any("command -v rclone" in ln for ln in lines), "nothing checks for rclone"
    apt = [ln for ln in lines if "apt-get" in ln and "-o DPkg::Lock::Timeout=" in ln]
    assert any(" update " in ln for ln in apt), "apt-get update does not wait on the dpkg lock"
    install = [ln for ln in apt if " install " in ln and "_offbox_missing" in ln]
    assert install, "vps_apply.sh no longer installs the off-box tools"
    assert all("< /dev/null" in ln for ln in apt), apt
    assert not any("_offbox_missing" in ln and "update" in ln and "&&" in ln for ln in lines), (
        "a failed apt-get update must not block the install"
    )
    assert any('_offbox_missing="$_offbox_missing rclone"' in ln for ln in lines)

    lift = next(ln for ln in lines if ln.strip().startswith("for _k in ") and "PG_MODE" in ln)
    assert "BACKUP_" not in lift, (
        "vps_apply.sh lifts a BACKUP_ key into the migration run, so the "
        f"pre-migration backup would upload: {lift.strip()!r}"
    )
