"""H-170: a Console migration runs only after a good dump of the Console database.

The deploy (`scripts/vps_apply.sh`) runs the Console ladder against the
Console's own Supabase project. Before H-170, no backup of that database came
first. The pre-migration backup of `apply_migrations.sh` has no Console DSN,
and it does not run at all when no tenant migration is pending.

The gate has two halves, and this file is the fence of both (R7):

* `backup_db.sh --console-only` dumps ONLY the Console database into
  `<stamp>-console`, checks it with `pg_restore --list`, and exits 1 on any
  failure. It never touches the app cluster. It keeps its newest 5 dirs, and
  the nightly retention glob `[0-9]*Z` never counts them.
* `vps_apply.sh` runs that backup BEFORE the applier, and a failed backup
  stops the deploy before the applier runs.

These run the REAL scripts in bash, with stub tools, in the idiom of
`tests/unit/test_backup_deploy_wiring.py`: `mktemp -d` inside bash, the repo
as the working directory, and relative script paths.
"""

from __future__ import annotations

import re
import subprocess

from tests.unit.test_backup_deploy_wiring import _ROOT, _STUBS, _hermetic_env
from tests.unit.test_backup_env_values import _PIN_FILE, _run
from tests.unit.test_console_ladder_deploy_wiring import _dsn_block

_APPLY = _ROOT / "scripts/vps_apply.sh"
_APPLIER = "apply_customer_console_migrations.sh"
_PASSWORD = "Sekr3tPw"
_CC_DSN = f"postgresql://cc:{_PASSWORD}@cc.example:5432/postgres"

# Console-side stubs. A knob makes the dump or the archive check fail. The
# failing pg_dump writes the DSN, password and all, to stderr, as libpq does.
_CC_STUBS = r"""
pg_dump() {
  printf 'pg_dump %s\n' "$*" >> "$CALLS"
  if [ -n "${STUB_CONSOLE_DUMP_FAILS:-}" ]; then
    echo "pg_dump: error: connection to server failed: $*" >&2
    echo PARTIAL
    return 1
  fi
  echo DUMP
}
pg_restore() {
  printf 'pg_restore %s\n' "$*" >> "$CALLS"
  cat > /dev/null
  if [ -n "${STUB_LIST_FAILS:-}" ]; then
    echo "pg_restore: error: input file is too short" >&2
    return 1
  fi
}
rsync() { printf 'rsync %s\n' "$*" >> "$CALLS"; }
rclone() { printf 'rclone %s\n' "$*" >> "$CALLS"; }
docker() { printf 'docker %s\n' "$*" >> "$CALLS"; return 1; }
export -f pg_dump pg_restore rsync rclone docker
"""

# The app cluster is DOWN. A console-only run that asks it anything fails.
_DEAD_APP_CLUSTER = r"""
psql() {
  printf 'psql %s\n' "$*" >> "$CALLS"
  echo "psql: error: connection to server failed" >&2
  return 2
}
pg_dumpall() { printf 'pg_dumpall %s\n' "$*" >> "$CALLS"; return 2; }
export -f psql pg_dumpall
"""

# 20 old nights and 7 old Console dirs.
_PLANT = (
    'for d in $(seq -w 1 20); do mkdir -p "$W/backups/2026-01-${d}T000000Z"; '
    'echo x > "$W/backups/2026-01-${d}T000000Z/acb.dump"; done\n'
    'for d in 01 02 03 04 05 06 07; do mkdir -p "$W/backups/2026-01-${d}T000000Z-console"; '
    'echo x > "$W/backups/2026-01-${d}T000000Z-console/customer_console.dump"; done\n'
)
_COUNT = (
    'echo "NIGHTS=$(ls -1d "$W"/backups/2*Z 2>/dev/null | wc -l)" >&2\n'
    'echo "CONSOLE=$(ls -1d "$W"/backups/2*Z-console 2>/dev/null | wc -l)" >&2\n'
)
_TAIL = (
    'printf "\\n===CALLS===\\n" >&2\n'
    'cat "$CALLS" >&2\n'
    'printf "\\n===FILES===\\n" >&2\n'
    '(cd "$W" && find . | sed "s#^\\./##" | sort) >&2\n'
    'rm -rf "$W"\n'
    "exit $rc\n"
)


def _bash(prog: str, timeout: int = 120) -> dict[str, object]:
    run = subprocess.run(
        ["bash"], input=prog.encode(), capture_output=True, timeout=timeout, cwd=_ROOT,
        env=_hermetic_env(),
    )
    err = run.stderr.decode(errors="replace")
    err, _, rest = err.partition("\n===CALLS===\n")
    calls, _, files = rest.partition("\n===FILES===\n")
    return {
        "rc": run.returncode,
        "out": run.stdout.decode(errors="replace"),
        "err": err,
        "calls": calls.splitlines(),
        "files": files.splitlines(),
    }


def _backup(
    env: str = "", *, setup: str = "", flags: str = "--console-only", after: str = "",
    dead_app_cluster: bool = True,
) -> dict[str, object]:
    """Run the REAL backup_db.sh as acb (not root) with the stub tools."""
    prog = (
        _STUBS
        + _CC_STUBS
        + (_DEAD_APP_CLUSTER if dead_app_cluster else "")
        + setup
        + f'{env} APP_DIR="$W/app" BACKUP_DIR="$W/backups" '
        + f"bash scripts/backup_db.sh {flags} < /dev/null\n"
        + "rc=$?\n"
        + after
        + _TAIL
    )
    return _bash(prog)


def _console_dirs(files: object) -> list[str]:
    return [f for f in files if re.fullmatch(r"backups/\d{4}-\d{2}-\d{2}T\d{6}Z-console", f)]  # type: ignore[union-attr]


def _app_calls(calls: object) -> list[str]:
    """Each call that reached the APP cluster."""
    return [
        ln for ln in calls  # type: ignore[union-attr]
        if ln.startswith(("psql", "pg_dumpall", "docker", "rsync", "rclone"))
        or (ln.startswith("pg_dump ") and not ln.startswith("pg_dump -d "))
    ]


# ── backup_db.sh --console-only ──────────────────────────────────────────────


def test_console_only_dumps_only_the_console_and_lists_it() -> None:
    """The Console dump lands in `<stamp>-console`, `pg_restore --list` reads
    it, and the manifest holds its sha256. The app cluster is down, and the
    run does not notice, because it never asks it. Mutation: let the mode
    fall through to `require_postgres`, and the run exits 1."""
    r = _backup(f"CUSTOMER_CONSOLE_DATABASE_URL='{_CC_DSN}'")
    assert r["rc"] == 0, f"exit {r['rc']}:\n{r['out']}\n{r['err']}"
    done = [ln for ln in str(r["out"]).splitlines() if "Console backup complete: " in ln]
    assert len(done) == 1, r["out"]
    assert done[0].rstrip().endswith("Z-console"), done
    dirs = _console_dirs(r["files"])
    assert len(dirs) == 1, r["files"]
    files = r["files"]
    assert f"{dirs[0]}/customer_console.dump" in files, files  # type: ignore[operator]
    assert f"{dirs[0]}/MANIFEST.txt" in files, files  # type: ignore[operator]
    assert not any(f.endswith(("acb.dump", "globals.sql")) for f in files), files  # type: ignore[union-attr]
    assert not any(re.fullmatch(r"backups/\d{4}-\d{2}-\d{2}T\d{6}Z", f) for f in files), (  # type: ignore[union-attr]
        "the mode made an app night"
    )
    calls = r["calls"]
    assert [ln for ln in calls if ln.startswith("pg_dump ")] == [  # type: ignore[union-attr]
        f"pg_dump -d {_CC_DSN} -Fc"
    ], calls
    assert any(ln.startswith("pg_restore --list") for ln in calls), calls  # type: ignore[union-attr]
    assert _app_calls(calls) == [], f"the mode reached the app cluster: {_app_calls(calls)}"
    assert "Retention (keeping" not in str(r["out"]), "the nightly retention ran"
    assert _PASSWORD not in str(r["out"]) + str(r["err"]), "the password reached the log"


def test_console_only_writes_the_sha256_of_the_dump() -> None:
    after = 'cat "$W"/backups/*-console/MANIFEST.txt >&2\n'
    r = _backup(f"CUSTOMER_CONSOLE_DATABASE_URL='{_CC_DSN}'", after=after)
    assert r["rc"] == 0, f"exit {r['rc']}:\n{r['out']}\n{r['err']}"
    assert re.search(r"^[0-9a-f]{64}  \./customer_console\.dump$", str(r["err"]), re.M), r["err"]


def test_console_only_with_no_dsn_exits_1_and_makes_no_dir() -> None:
    r = _backup()
    assert r["rc"] == 1, f"exit {r['rc']}:\n{r['out']}\n{r['err']}"
    assert "CUSTOMER_CONSOLE_DATABASE_URL" in str(r["err"]), r["err"]
    assert "Console backup complete" not in str(r["out"])
    assert _console_dirs(r["files"]) == [], r["files"]
    assert not any(ln.startswith("pg_dump") for ln in r["calls"]), r["calls"]  # type: ignore[union-attr]


def test_console_only_with_a_failed_dump_exits_1_and_keeps_the_password_out() -> None:
    """The stub pg_dump writes the DSN to stderr. That text goes to the
    `.err` file of the dir, never to the log."""
    r = _backup(f"STUB_CONSOLE_DUMP_FAILS=1 CUSTOMER_CONSOLE_DATABASE_URL='{_CC_DSN}'")
    assert r["rc"] == 1, f"exit {r['rc']}:\n{r['out']}\n{r['err']}"
    assert "Console backup complete" not in str(r["out"])
    assert _PASSWORD not in str(r["out"]) + str(r["err"]), "the password reached the log"
    dirs = _console_dirs(r["files"])
    assert len(dirs) == 1, r["files"]
    assert f"{dirs[0]}/customer_console.dump" not in r["files"], "a failed dump looks good"  # type: ignore[operator]


def test_console_only_with_an_unreadable_archive_exits_1() -> None:
    r = _backup(f"STUB_LIST_FAILS=1 CUSTOMER_CONSOLE_DATABASE_URL='{_CC_DSN}'")
    assert r["rc"] == 1, f"exit {r['rc']}:\n{r['out']}\n{r['err']}"
    assert "Console backup complete" not in str(r["out"])
    dirs = _console_dirs(r["files"])
    assert f"{dirs[0]}/customer_console.dump" not in r["files"], "a corrupt dump looks good"  # type: ignore[operator]


def test_console_only_refuses_a_dsn_with_a_file_option() -> None:
    dsn = "postgresql://cc:pw@cc.example:5432/postgres?passfile=/root/.pgpass"
    r = _backup(f"CUSTOMER_CONSOLE_DATABASE_URL='{dsn}'")
    assert r["rc"] == 1, f"exit {r['rc']}:\n{r['out']}\n{r['err']}"
    assert not any(ln.startswith("pg_dump") for ln in r["calls"]), r["calls"]  # type: ignore[union-attr]
    assert _console_dirs(r["files"]) == [], r["files"]


def test_console_only_takes_no_other_argument() -> None:
    for flags in ("--console-only --offbox", "--console-only --verify-restore"):
        r = _backup(f"CUSTOMER_CONSOLE_DATABASE_URL='{_CC_DSN}'", flags=flags)
        assert r["rc"] == 2, f"{flags}: exit {r['rc']}:\n{r['out']}\n{r['err']}"
        assert not any(ln.startswith("pg_dump") for ln in r["calls"]), r["calls"]  # type: ignore[union-attr]


def test_console_only_keeps_5_console_dirs_and_no_night_is_touched() -> None:
    """7 old Console dirs plus the new one: the 5 newest stay. The 20 nights
    stay too, because the Console prune never reads them."""
    after = _COUNT + (
        '[ -d "$W/backups/2026-01-04T000000Z-console" ] && echo "OLD4-KEPT" >&2\n'
        '[ -d "$W/backups/2026-01-03T000000Z-console" ] && echo "OLD3-KEPT" >&2\n'
    )
    r = _backup(f"CUSTOMER_CONSOLE_DATABASE_URL='{_CC_DSN}'", setup=_PLANT, after=after)
    assert r["rc"] == 0, f"exit {r['rc']}:\n{r['out']}\n{r['err']}"
    err = str(r["err"])
    assert "CONSOLE=5" in err, err
    assert "NIGHTS=20" in err, err
    assert "OLD4-KEPT" in err and "OLD3-KEPT" not in err, err
    assert "could not prune" not in err, err


def test_the_console_prune_refuses_a_dir_of_another_shape() -> None:
    """The delete guard: `1Z-console` matches the prune glob, sorts first,
    and is not a stamp. It stays, and the run still exits 0."""
    setup = 'mkdir -p "$W/backups/1Z-console"; echo keep > "$W/backups/1Z-console/keep"\n' + _PLANT
    after = '[ -f "$W/backups/1Z-console/keep" ] && echo "ODD-KEPT" >&2\n'
    r = _backup(f"CUSTOMER_CONSOLE_DATABASE_URL='{_CC_DSN}'", setup=setup, after=after)
    assert r["rc"] == 0, f"exit {r['rc']}:\n{r['out']}\n{r['err']}"
    assert "ODD-KEPT" in str(r["err"]), "the prune deleted a dir that is not a stamp"
    assert "Console backup complete: " in str(r["out"]), r["out"]


def test_the_nightly_retention_never_counts_a_console_dir() -> None:
    """KEEP_DAILY=3 over 20 nights and 7 Console dirs: 3 nights stay, and
    every Console dir stays. A deploy cannot push a night out of the 14."""
    r = _backup(
        "KEEP_DAILY=3 PG_MODE=local", setup=_PLANT, flags="", after=_COUNT,
        dead_app_cluster=False,
    )
    assert r["rc"] == 0, f"exit {r['rc']}:\n{r['out']}\n{r['err']}"
    err = str(r["err"])
    assert "NIGHTS=3" in err, err
    assert "CONSOLE=7" in err, err
    assert "Console backup complete" not in str(r["out"]), "the nightly run printed the gate line"


def test_a_root_console_only_run_keeps_its_pins_and_paths() -> None:
    """A deploy on the pull path runs as root. The root run starts again
    clean, takes its fixed paths, and checks the Console pins."""
    good = _run(
        f"PGHOST=db.example CUSTOMER_CONSOLE_DATABASE_URL='{_CC_DSN}'",
        setup=_PIN_FILE, root=True, offbox=True, flags="--console-only",
    )
    assert good["rc"] == 0, f"exit {good['rc']}:\n{good['out']}\n{good['err']}"
    assert any(
        re.fullmatch(r"root/opt/acb/backups/\d{4}-\d{2}-\d{2}T\d{6}Z-console/customer_console\.dump", f)
        for f in good["files"]  # type: ignore[union-attr]
    ), good["files"]
    assert not any(f.endswith("acb.dump") for f in good["files"]), good["files"]  # type: ignore[union-attr]
    bad = _run(
        "PGHOST=db.example CUSTOMER_CONSOLE_DATABASE_URL='postgresql://cc:pw@evil.example:5432/postgres'",
        setup=_PIN_FILE, root=True, offbox=True, flags="--console-only",
    )
    assert bad["rc"] == 1, f"exit {bad['rc']}:\n{bad['out']}\n{bad['err']}"
    assert not [ln for ln in str(bad["calls"]).splitlines() if ln.startswith("pg_dump")], bad["calls"]
    assert not any(f.endswith("-console") for f in bad["files"]), bad["files"]  # type: ignore[union-attr]


# ── vps_apply.sh: the gate before the applier ────────────────────────────────


def _branch_lines() -> list[str]:
    """The executable lines of the `elif [ -n "$CC_DSN" ]` branch."""
    lines = [ln for ln in _dsn_block().splitlines() if ln.strip() and not ln.strip().startswith("#")]
    start = next(i for i, ln in enumerate(lines) if ln.strip() == 'elif [ -n "$CC_DSN" ]; then')
    end = next(i for i in range(start + 1, len(lines)) if lines[i].startswith(("elif ", "else", "fi")))
    return lines[start + 1 : end]


def test_the_gate_runs_the_console_backup_before_the_applier() -> None:
    """Text order, inside the branch that runs the applier. The behavioural
    tests below run the same lines."""
    branch = _branch_lines()
    backup = [i for i, ln in enumerate(branch) if "backup_db.sh --console-only" in ln]
    applier = [i for i, ln in enumerate(branch) if _APPLIER in ln]
    assert backup and applier, branch
    assert backup[0] < applier[0], "the Console backup must run BEFORE the applier"
    assert "< /dev/null" in branch[backup[0]], "the backup call must not drain the script's stdin"
    between = branch[backup[0] : applier[0]]
    assert any(ln.strip() == "exit 1" for ln in between), "a failed backup must stop the deploy"


def _gate(*, env_line: str | None, unit_enabled: bool, reachable: bool = True, knobs: str = "") -> dict[str, object]:
    """Run the lifted DSN block of vps_apply.sh under `set -e`, with the REAL
    backup_db.sh and a stub applier that prints the ladder's own lines."""
    setup = (
        'mkdir -p "$W/app/scripts" "$W/app/apps/services/customer_console"\n'
        'cp scripts/backup_db.sh scripts/offbox_lib.sh "$W/app/scripts/"\n'
        "printf '#!/usr/bin/env bash\\necho \"==> applying 001_a.sql\"\\necho APPLIED\\n' "
        f'> "$W/app/scripts/{_APPLIER}"\n'
    )
    if env_line is not None:
        setup += f"printf '%s\\n' '{env_line}' > \"$W/app/apps/services/customer_console/.env\"\n"
    prog = (
        _STUBS
        + _CC_STUBS
        + setup
        + knobs
        + 'export APP_DIR="$W/app" BACKUP_DIR="$W/backups"\n'
        + f"systemctl() {{ return {0 if unit_enabled else 1}; }}\n"
        + ("" if reachable else "psql() { return 2; }\n")
        + 'cd "$W/app"\n'
        + "(\nset -e\n"
        + _dsn_block()
        + "\n)\nrc=$?\n"
        + 'cd /\n'
        + _TAIL
    )
    return _bash(prog)


_BOX_DSN = f"CUSTOMER_CONSOLE_DATABASE_URL=postgresql+psycopg://cc:{_PASSWORD}@cc.example:5432/postgres"


def test_the_deploy_log_shows_the_backup_before_the_first_migration() -> None:
    r = _gate(env_line=_BOX_DSN, unit_enabled=True)
    out = str(r["out"])
    assert r["rc"] == 0, f"exit {r['rc']}:\n{out}\n{r['err']}"
    assert "Console backup complete: " in out, out
    assert "applying 001_a.sql" in out, out
    assert out.index("Console backup complete: ") < out.index("applying 001_a.sql"), out
    assert any(ln.startswith(f"pg_dump -d postgresql://cc:{_PASSWORD}@cc.example") for ln in r["calls"]), r["calls"]  # type: ignore[union-attr]
    assert _PASSWORD not in out + str(r["err"]), "the password reached the deploy log"


def test_a_failed_console_backup_stops_the_deploy_before_the_applier() -> None:
    r = _gate(env_line=_BOX_DSN, unit_enabled=True, knobs="export STUB_CONSOLE_DUMP_FAILS=1\n")
    out = str(r["out"])
    assert r["rc"] == 1, f"exit {r['rc']}:\n{out}\n{r['err']}"
    assert "APPLIED" not in out and "applying" not in out, "the applier ran after a failed backup"
    assert "Console backup complete" not in out, out
    assert "ERROR" in out, out
    assert _PASSWORD not in out + str(r["err"]), "the password reached the deploy log"


def test_no_dsn_skips_the_backup_and_the_ladder_loudly() -> None:
    r = _gate(env_line=None, unit_enabled=False)
    assert r["rc"] == 0, f"exit {r['rc']}:\n{r['out']}\n{r['err']}"
    assert "SKIPPED" in str(r["out"]), r["out"]
    assert not any(ln.startswith("pg_dump") for ln in r["calls"]), r["calls"]  # type: ignore[union-attr]
    assert "APPLIED" not in str(r["out"])
    enabled = _gate(env_line=None, unit_enabled=True)
    assert enabled["rc"] == 1, f"exit {enabled['rc']}:\n{enabled['out']}\n{enabled['err']}"
    assert not any(ln.startswith("pg_dump") for ln in enabled["calls"]), enabled["calls"]  # type: ignore[union-attr]


def test_an_unreachable_dsn_keeps_its_matrix_and_takes_no_backup() -> None:
    off = _gate(env_line=_BOX_DSN, unit_enabled=False, reachable=False)
    assert off["rc"] == 0, f"exit {off['rc']}:\n{off['out']}\n{off['err']}"
    assert "UNREACHABLE" in str(off["out"]), off["out"]
    on = _gate(env_line=_BOX_DSN, unit_enabled=True, reachable=False)
    assert on["rc"] == 1, f"exit {on['rc']}:\n{on['out']}\n{on['err']}"
    for r in (off, on):
        assert not any(ln.startswith("pg_dump") for ln in r["calls"]), r["calls"]  # type: ignore[union-attr]
        assert "APPLIED" not in str(r["out"])
