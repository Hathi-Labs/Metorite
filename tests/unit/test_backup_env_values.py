"""BH-6a (WS-49, finding B3): the root backup chain trusts no value from the env.

`acb-backup.service` runs `scripts/backup_db.sh` as ROOT, and it loads
`/opt/acb/app/.env`, which the gateway can write. So before this fix a line in
that file chose the image that root runs with the whole dump streamed into it,
the directory root runs `rm -rf` in, how many nights root keeps, which dirs
root tars, and (for each name the root key file did not set) where root uploads
the night. `backup_db.sh`'s header lists the class of every name: PINNED, ROOT
FILE, VALIDATED or HARMLESS.

These run the REAL scripts with the stub tools of
`tests/unit/test_backup_deploy_wiring.py`. Each test is RED on the scripts
before the fix, except the three regression fences that say so.

The root run is forced with `BACKUP_ENV_GUARD=1`, because a test cannot be
uid 0. That knob can only make a run stricter, so the env may hold it. The
forced run takes its paths from where the script is, so those tests copy the
scripts into a layout: `$W/root/opt/acb/app/scripts`.
"""
from __future__ import annotations

import subprocess
import sys

import pytest

from tests.unit.test_backup_deploy_wiring import (
    _DOCKER,
    _FULL_ENV,
    _GPG_ENV,
    _OFFBOX_STUBS,
    _ROOT,
    _S3_ENV,
    _STUBS,
    _encrypted,
    _hermetic_env,
    _rclone_calls,
    _to_root_file,
)

# A psql that also logs the env it got, and answers from STUB_DBS for the
# database list. It reads stdin for every other call, so the migration
# runner can pipe SQL into it.
_PSQL = r"""
psql() {
  printf 'psql %s\n' "$*" >> "$CALLS"
  printf 'psql-env HOME=%s PSQLRC=%s\n' "${HOME-}" "${PSQLRC-<unset>}" >> "$CALLS"
  case "$*" in
    *"from pg_class c"*) echo 7 ;;
    *"select 1"*) echo 1 ;;
    *"server_version_num"*) echo 17 ;;
    *"show server_version"*) echo 17.6 ;;
    *"datistemplate"*) printf '%s\n' ${STUB_DBS:-acb} ;;
    *"count(*) from pg_database"*) echo 0 ;;
    *"FROM schema_migrations"*) : ;;
    *) cat > /dev/null ;;
  esac
  return 0
}
rsync() { printf 'rsync %s\n' "$*" >> "$CALLS"; }
tar() { printf 'tar %s\n' "$*" >> "$CALLS"; command tar "$@"; }
export -f psql rsync tar
"""

# The root layout of a forced root run. The .env there is the real one; a
# test puts the attacker's values in OTHER places and in the env.
_LAYOUT = r"""
R="$W/root"
mkdir -p "$R/opt/acb/app/scripts" "$R/opt/acb/backups" "$R/etc/acb"
cp scripts/backup_db.sh scripts/backup_offbox.sh scripts/offbox_lib.sh "$R/opt/acb/app/scripts/"
printf 'POSTGRES_USER=acb\n' > "$R/opt/acb/app/.env"
"""

_ROOT_RUN = 'BACKUP_ENV_GUARD=1 PG_MODE=local bash "$W/root/opt/acb/app/scripts/backup_db.sh"'
_ACB_RUN = 'PG_MODE=local APP_DIR="$W/app" BACKUP_DIR="$W/backups" bash scripts/backup_db.sh'


def _run(
    env: str = "",
    *,
    setup: str = "",
    flags: str = "",
    docker: str = "works",
    offbox: bool = False,
    root: bool = False,
    after: str = "",
    command: str = "",
) -> dict[str, object]:
    """Run the REAL backup_db.sh. `root` forces the root run in a layout.
    `offbox` adds the off-box stubs, whose key file is
    $W/backup-offbox.env, root:root 0600 to the stub `stat`. Nothing here
    copies `env` into the key file: a test that wants keys there says so."""
    run_cmd = command or (_ROOT_RUN if root else _ACB_RUN)
    prog = (
        _STUBS
        + _DOCKER[docker]
        + (_OFFBOX_STUBS if offbox else "")
        + _PSQL
        + (_LAYOUT if root else "")
        + setup
        + f"{env} {run_cmd} {flags} < /dev/null\n"
        + "rc=$?\n"
        + after
        + 'printf "\\n===CALLS===\\n" >&2\n'
        + 'cat "$CALLS" >&2\n'
        + 'printf "\\n===FILES===\\n" >&2\n'
        + '(cd "$W" && find . | sed "s#^\\./##" | sort) >&2\n'
        + 'printf "\\n===S3===\\n" >&2\n'
        + 'if [ -n "${S3:-}" ] && [ -d "$S3" ]; then (cd "$S3" && find . -type f | sort) >&2; fi\n'
        + 'rm -rf "$W"\n'
        + "exit $rc\n"
    )
    run = subprocess.run(
        ["bash"], input=prog.encode(), capture_output=True, timeout=120, cwd=_ROOT,
        env=_hermetic_env(),
    )
    err = run.stderr.decode(errors="replace")
    err, _, rest = err.partition("\n===CALLS===\n")
    calls, _, rest = rest.partition("\n===FILES===\n")
    files, _, s3 = rest.partition("\n===S3===\n")
    return {
        "rc": run.returncode,
        "out": run.stdout.decode(errors="replace"),
        "err": err,
        "calls": calls,
        "files": files.splitlines(),
        "s3": [ln for ln in s3.splitlines() if ln.strip()],
    }


def _lines(r: dict[str, object], prefix: str) -> list[str]:
    return [ln for ln in str(r["calls"]).splitlines() if ln.startswith(prefix)]


# ── VALIDATED: BACKUP_VERIFY_IMAGE ──────────────────────────────────────────


@pytest.mark.parametrize(
    "image",
    [
        "evil/x",
        "pgvector/pgvector:latest",
        "pgvector/pgvector:pg17 --privileged",
        "pgvector/pgvector:pg17@sha256:abc",
        "docker.io/pgvector/pgvector:pg17",
    ],
)
def test_a_verify_image_from_the_env_never_reaches_docker(image: str) -> None:
    """🔴 Root runs `docker run <image>` and streams the whole dump into it,
    so an image that the env names is root code execution. Mutation: take
    BACKUP_VERIFY_IMAGE as it comes, and `evil/x` reaches docker."""
    r = _run(f"BACKUP_VERIFY_IMAGE='{image}'", flags="--verify-restore")
    runs = _lines(r, "docker run")
    assert runs, f"the verify never started:\n{r['out']}\n{r['err']}"
    for ln in runs:
        assert image not in ln, f"docker got the env's image: {ln}"
        assert "pgvector/pgvector:pg17 " in ln, ln
    assert "BACKUP_VERIFY_IMAGE is not pgvector/pgvector:pg<N>" in str(r["err"]), r["err"]
    assert image not in str(r["out"]) + str(r["err"]), "the WARN printed the value"
    assert r["rc"] == 0, f"exit {r['rc']}:\n{r['out']}\n{r['err']}"


def test_a_pinned_verify_image_digest_is_still_taken() -> None:
    """The companion: a guard that refused every value passes the test above,
    and the operator could no longer pin the image by digest."""
    image = "pgvector/pgvector:pg17@sha256:" + "a" * 64
    r = _run(f"BACKUP_VERIFY_IMAGE='{image}'", flags="--verify-restore")
    assert any(image in ln for ln in _lines(r, "docker run")), r["calls"]
    assert "BACKUP_VERIFY_IMAGE is not" not in str(r["err"])


@pytest.mark.parametrize(("memory", "want"), [("99999999999", "1g"), ("1g --privileged", "1g"), ("512m", "512m")])
def test_the_verify_memory_is_a_size_or_the_default(memory: str, want: str) -> None:
    r = _run(f"BACKUP_VERIFY_MEMORY='{memory}'", flags="--verify-restore")
    run = _lines(r, "docker run")[0]
    assert f"--memory {want} --memory-swap {want} " in run, run


# ── VALIDATED: KEEP_DAILY, and the one shape that retention deletes ────────

_PLANT_20 = (
    'for d in $(seq -w 1 20); do mkdir -p "$W/backups/2026-01-${d}T000000Z"; '
    'echo x > "$W/backups/2026-01-${d}T000000Z/acb.dump"; done\n'
)
_COUNT = 'echo "NIGHTS=$(ls -1d "$W"/backups/2*Z 2>/dev/null | wc -l)" >&2\n'


@pytest.mark.parametrize(("keep", "left"), [("0", 14), ("2", 14), ("-1", 14), ("abc", 14), ("5", 5)])
def test_keep_daily_below_three_never_prunes_below_the_default(keep: str, left: int) -> None:
    """🔴 `head -n -0` lists EVERY night, so KEEP_DAILY=0 made root delete the
    whole local history, tonight's dump too. 20 old nights plus tonight: a bad
    value keeps 14, and 5 keeps 5 (the companion). Mutation: take KEEP_DAILY
    as it comes, and the 0 case leaves no night at all."""
    r = _run(f"KEEP_DAILY='{keep}'", setup=_PLANT_20, after=_COUNT)
    assert r["rc"] == 0, f"exit {r['rc']}:\n{r['out']}\n{r['err']}"
    assert f"NIGHTS={left}" in str(r["err"]), r["err"]
    if left == 14:
        assert "KEEP_DAILY is not a whole number of 3 or more. Using 14." in str(r["err"])


def test_retention_deletes_only_a_night_and_never_follows_a_link() -> None:
    """🔴 Every `rm -rf` goes through one guard: "$BACKUP_DIR/<stamp>". `1Z`
    matches the old `[0-9]*Z` glob, sorts first, and is NOT a night, so it
    stays. A symlink named like a night loses the link, never its target.
    Mutation: drop the case guard in rm_night, and `1Z` goes."""
    setup = (
        _PLANT_20
        + 'mkdir -p "$W/backups/1Z" "$W/victim"; echo keep > "$W/backups/1Z/keep"\n'
        + 'echo keep > "$W/victim/keep"; ln -s "$W/victim" "$W/backups/2025-01-01T000000Z"\n'
    )
    after = (
        '[ -f "$W/backups/1Z/keep" ] && echo "ODD-ENTRY-KEPT" >&2\n'
        '[ -f "$W/victim/keep" ] && echo "VICTIM-KEPT" >&2\n'
    )
    r = _run("KEEP_DAILY=3", setup=setup, after=after)
    assert r["rc"] == 0, f"exit {r['rc']}:\n{r['out']}\n{r['err']}"
    assert "ODD-ENTRY-KEPT" in str(r["err"]), "retention deleted a dir that is not a night"
    assert "VICTIM-KEPT" in str(r["err"]), "retention followed a link out of the backup dir"


# ── PINNED: the paths of the root run, and the tool names of its env ───────


def test_the_root_run_ignores_its_paths_and_tool_names_in_the_env() -> None:
    """🔴 BACKUP_DIR, ENV_FILE, APP_DIR and PG_CONTAINER from the env are
    ignored, and so are PATH, HOME and PSQLRC. The dump lands in the pinned
    <root>/opt/acb/backups, the user comes from the pinned .env, no binary on
    the env's PATH runs, and psql sees HOME=/root and no PSQLRC. Mutation:
    honour BACKUP_DIR in the root run, and the dump lands in $W/x."""
    setup = (
        'mkdir -p "$W/a" "$W/evilbin" "$W/x"\n'
        "printf 'POSTGRES_USER=evil_e\\n' > \"$W/e\"\n"
        "printf 'POSTGRES_USER=evil_a\\n' > \"$W/a/.env\"\n"
        # `dirname` is the FIRST external command of the script, and `du` a
        # late one. Each marks that it ran, then runs the real tool.
        "for t in dirname du; do printf '#!/bin/sh\\ntouch \"$W/EVIL-PATH-RAN\"\\n"
        "exec /usr/bin/%s \"$@\"\\n' \"$t\" > \"$W/evilbin/$t\"; chmod +x \"$W/evilbin/$t\"; done\n"
    )
    env = (
        'BACKUP_DIR="$W/x" ENV_FILE="$W/e" APP_DIR="$W/a" PG_CONTAINER=evil '
        'BACKUP_OFFBOX_ENV_FILE="$W/k" PATH="$W/evilbin:$PATH" HOME="$W/a" PSQLRC="$W/a/rc"'
    )
    r = _run(env, setup=setup, root=True)
    files = r["files"]
    assert r["rc"] == 0, f"exit {r['rc']}:\n{r['out']}\n{r['err']}"
    assert any(f.startswith("root/opt/acb/backups/") and f.endswith("/acb.dump") for f in files), files  # type: ignore[union-attr]
    assert not any(f.startswith("x/") for f in files), "the dump went to the env's BACKUP_DIR"  # type: ignore[union-attr]
    assert "EVIL-PATH-RAN" not in files, "a binary from the env's PATH ran"  # type: ignore[operator]
    users = [ln for ln in _lines(r, "psql ") if " -U " in ln]
    assert users and all(" -U acb " in ln for ln in users), users
    assert "evil" not in str(r["calls"]), "a value from the env reached a tool"
    for ln in _lines(r, "psql-env "):
        assert ln == "psql-env HOME=/root PSQLRC=<unset>", ln
    err = str(r["err"])
    for name in ("APP_DIR", "BACKUP_DIR", "PG_CONTAINER", "BACKUP_OFFBOX_ENV_FILE"):
        assert f"{name} is set in the environment. The root run ignores it" in err, (name, err)
    assert "Backing up cluster 'acb-postgres'" in str(r["out"]), r["out"]


def test_a_root_run_outside_the_layout_is_refused() -> None:
    """The fixed paths come from where the script is. A root run from another
    place has no fixed paths, so it stops before it touches anything."""
    r = _run(command=f"BACKUP_ENV_GUARD=1 {_ACB_RUN}")
    assert r["rc"] == 2, f"exit {r['rc']}:\n{r['out']}\n{r['err']}"
    assert "a root run must start from /opt/acb/app/scripts/backup_db.sh" in str(r["err"])
    assert not _lines(r, "pg_dump"), "a refused run still dumped"


def test_an_acb_run_keeps_the_same_defaults() -> None:
    """Regression fence. The pre-migration backup runs as acb with no path in
    its env, so it takes the defaults, and they are the old ones. The root
    run pins the SAME values. Read, not run: a run would write under /opt."""
    text = (_ROOT / "scripts/backup_db.sh").read_text(encoding="utf-8")
    for line in (
        'BACKUP_DIR="${BACKUP_DIR:-/opt/acb/backups}"',
        'PG_CONTAINER="${PG_CONTAINER:-acb-postgres}"',
        'APP_DIR="${APP_DIR:-/opt/acb/app}"',
        'offbox_key_file="${BACKUP_OFFBOX_ENV_FILE:-/etc/acb/backup-offbox.env}"',
        'BACKUP_DIR="$layout_root/opt/acb/backups"',
        'APP_DIR="$layout_root/opt/acb/app"',
        "PG_CONTAINER=acb-postgres",
        'offbox_key_file="$layout_root/etc/acb/backup-offbox.env"',
    ):
        assert any(ln.strip() == line for ln in text.splitlines()), line


# ── The migration runner's call, as acb ────────────────────────────────────


def test_the_migration_backup_as_acb_still_runs() -> None:
    """Regression fence (green before the fix too). apply_migrations.sh runs
    the REAL backup_db.sh as acb before a pending migration. It must still
    dump, and the migration must still apply after it."""
    setup = (
        'mkdir -p "$W/app/scripts" "$W/m"\n'
        'cp scripts/backup_db.sh scripts/backup_offbox.sh scripts/offbox_lib.sh "$W/app/scripts/"\n'
        "printf -- '-- MARKER_A\\nselect 1;\\n' > \"$W/m/02_a.sql\"\n"
    )
    r = _run(
        command='PG_MODE=local APP_DIR="$W/app" MIGRATIONS_DIR="$W/m" BACKUP_DIR="$W/backups" '
                "bash scripts/apply_migrations.sh",
        setup=setup,
    )
    out = str(r["out"])
    assert r["rc"] == 0, f"exit {r['rc']}:\n{out}\n{r['err']}"
    assert "Pre-migration backup" in out and "Backup complete" in out, out
    assert any(f.startswith("backups/") and f.endswith("/acb.dump") for f in r["files"]), r["files"]  # type: ignore[union-attr]
    assert "(1 applied, 0 already recorded)" in out, out


# ── VALIDATED: the names read from the pinned .env, and the database list ──


@pytest.mark.parametrize("root", [False, True], ids=["acb-run", "root-run"])
def test_a_database_name_of_an_unsafe_shape_is_not_dumped(root: bool) -> None:
    """🔴 A database name becomes a FILE name, as root. Anyone with the DB
    password can create `../../evil`, and the dump then lands outside the
    night. It is skipped, and the box's own names (`postgres`, `_supabase`)
    and the others are dumped. A root run then exits 1. The acb run (the
    pre-migration backup) only warns, so a migration deploy never blocks on
    it. Mutation: drop the name check, and evil.dump appears."""
    r = _run(
        "STUB_DBS='acb ../../evil _supabase postgres'" + (" PGHOST=db.example" if root else ""),
        root=root,
    )
    if root:
        assert r["rc"] == 1, f"exit {r['rc']}:\n{r['out']}\n{r['err']}"
        assert "had a name of an unsafe shape" in str(r["err"]), r["err"]
    else:
        assert r["rc"] == 0, f"exit {r['rc']}:\n{r['out']}\n{r['err']}"
    assert "evil.dump" not in " ".join(r["files"]), r["files"]  # type: ignore[arg-type]
    assert not any("evil" in ln for ln in _lines(r, "pg_dump")), r["calls"]
    dumps = [f for f in r["files"] if f.endswith(".dump")]  # type: ignore[union-attr]
    assert sorted(f.rsplit("/", 1)[1] for f in dumps) == ["_supabase.dump", "acb.dump", "postgres.dump"], dumps
    assert "a database name is not of the shape" in str(r["err"]), r["err"]


@pytest.mark.parametrize(
    ("dotenv", "user"),
    [
        ("POSTGRES_USER=postgres.abcdefghijklmnopqrst", "postgres.abcdefghijklmnopqrst"),
        ("POSTGRES_USER=acb;rm", "acb"),
        ("POSTGRES_USER=--host=evil", "acb"),
    ],
)
def test_the_user_from_the_env_file_has_a_safe_shape(dotenv: str, user: str) -> None:
    """The Supabase pooler user `postgres.<ref>` passes (the box's own value),
    and any other shape falls back to acb with a WARN."""
    r = _run(setup=f"printf '%s\\n' '{dotenv}' > \"$W/app/.env\"\n")
    users = [ln for ln in _lines(r, "psql ") if " -U " in ln]
    assert users and all(f" -U {user} " in ln for ln in users), users


def test_the_app_database_from_the_env_file_has_a_safe_shape() -> None:
    r = _run(setup="printf 'DATABASE_URL=postgresql://u:p@h/x%%27%%3Bdrop\\n' > \"$W/app/.env\"\n")
    assert "app_db:           acb" in str(r["out"]), r["out"]
    assert "DATABASE_URL" in str(r["err"]) and "Using acb" in str(r["err"]), r["err"]


@pytest.mark.parametrize(
    ("dsn", "dumped"),
    [
        ("postgresql://cc:pw@cc.example:5432/postgres?sslkeylogfile=/etc/cron.d/x", False),
        ("postgresql://cc:pw@cc.example:5432/postgres?passfile=/root/.pgpass", False),
        ("postgresql://cc:pw@cc.example:5432/postgres?sslmode=require&service=x", False),
        ("host=cc.example passfile=/root/.pgpass", False),
        # Fix round 2: libpq fails over along a host LIST, so a second host
        # (in the authority, or as an option) could reach another server.
        ("postgresql+psycopg://cc:pw@cc.example:1,evil.example:5432/postgres?sslmode=require", False),
        ("postgresql://cc:pw@cc.example,evil.example/postgres", False),
        ("postgresql://cc:pw@cc.example:5432/postgres?host=evil.example", False),
        ("postgresql://cc:pw@cc.example:5432/postgres?hostaddr=203.0.113.9", False),
        ("postgresql+psycopg://cc:pw@cc.example:5432/postgres?sslmode=require", True),
        # A comma AFTER the authority is not a host list: it stays allowed.
        ("postgresql://cc:pw@cc.example:5432/post,gres", True),
    ],
)
def test_the_console_dsn_cannot_carry_a_file_option(dsn: str, dumped: bool) -> None:
    """🔴 Root passes this DSN to pg_dump, and libpq options read or write a
    FILE as root. Only a postgresql:// URL with sslmode, connect_timeout or
    application_name. Mutation: drop the check, and pg_dump gets the file
    option."""
    r = _run(f"CUSTOMER_CONSOLE_DATABASE_URL='{dsn}'")
    console = [ln for ln in _lines(r, "pg_dump -d ")]
    if dumped:
        assert console, r["calls"]
        assert r["rc"] == 0, f"exit {r['rc']}:\n{r['out']}\n{r['err']}"
    else:
        assert console == [], f"pg_dump got the DSN: {console}"
        assert "The Console database is NOT" in str(r["err"]), r["err"]
        assert r["rc"] != 0


# ── ROOT FILE: the off-box names ───────────────────────────────────────────

_ROOT_KEYS = _to_root_file(_S3_ENV + _GPG_ENV)
_GOOD_ENDPOINT = "endpoint=https://ref.storage.supabase.co/storage/v1/s3"


def test_the_root_file_wins_over_an_env_line() -> None:
    """🔴 The app .env holds BACKUP_S3_ENDPOINT=evil, and the root file holds
    the real one. The night goes up to the root file's endpoint, and the
    line in .env is an ERROR (the gateway loads that file). Before the fix
    the line stopped the upload, so the night had no copy off the box."""
    setup = _ROOT_KEYS + "printf 'BACKUP_S3_ENDPOINT=https://evil.example/s3\\n' >> \"$W/app/.env\"\n"
    r = _run(_FULL_ENV, setup=setup, offbox=True, flags="--offbox")
    assert "off-box copy ok (offbox:metorite-backups/nightly/" in str(r["out"]), f"{r['out']}\n{r['err']}"
    envs = _lines(r, "rclone-env ")
    assert envs and all(_GOOD_ENDPOINT in ln for ln in envs), envs
    assert "evil.example" not in str(r["calls"])
    assert "holds a BACKUP_S3_*, BACKUP_GPG_* or BACKUP_OFFBOX_ENV_FILE" in str(r["err"]), r["err"]
    assert r["rc"] != 0, "a key line in the app .env must turn the run red"


def test_an_off_box_name_only_in_the_env_is_ignored() -> None:
    """🔴 THE HOLE. The root file sets the five keys and nothing else, so a
    name it does not set (PREFIX, KEEP) fell through from the env. Here the
    env also names another endpoint and bucket. Every one is ignored: the
    night goes to metorite-backups/nightly at the real endpoint, and KEEP
    stays 14, so the three old nights stay. Mutation: read the env, and the
    night goes to evil.example and prunes the bucket to one night."""
    setup = (
        _ROOT_KEYS
        + "plant_night 2026-01-01T000000Z\nplant_night 2026-01-02T000000Z\n"
        + "plant_night 2026-01-03T000000Z\n"
    )
    env = (
        _FULL_ENV + "BACKUP_S3_ENDPOINT=https://evil.example/s3 BACKUP_S3_BUCKET=evil-bucket "
        "BACKUP_S3_PREFIX=evilprefix BACKUP_S3_KEEP=1"
    )
    r = _run(env, setup=setup, offbox=True, flags="--offbox")
    assert r["rc"] == 0, f"exit {r['rc']}:\n{r['out']}\n{r['err']}"
    envs = _lines(r, "rclone-env ")
    assert envs and all(_GOOD_ENDPOINT in ln for ln in envs), envs
    assert "evil" not in str(r["calls"]), [ln for ln in str(r["calls"]).splitlines() if "evil" in ln]
    nights = sorted({p.split("/")[3] for p in r["s3"] if p.startswith("./metorite-backups/nightly/")})  # type: ignore[union-attr]
    assert len(nights) == 4 and nights[:3] == [
        "2026-01-01T000000Z", "2026-01-02T000000Z", "2026-01-03T000000Z",
    ], nights


def test_no_root_file_means_no_upload() -> None:
    """Regression fence (green before the fix too). The keys are in the env
    only, and there is no root file. Nothing is encrypted or uploaded."""
    r = _run(_FULL_ENV, setup='rm -f "$BACKUP_OFFBOX_ENV_FILE"\n', offbox=True, flags="--offbox")
    assert _rclone_calls(r["calls"]) == [], "rclone ran with no root file"
    assert not _encrypted(r["calls"])
    assert r["s3"] == [], r["s3"]


def test_a_group_writable_root_file_is_refused() -> None:
    """Regression fence (green before the fix too). The root file is 0620.
    The upload is refused loudly, and rclone never runs."""
    r = _run(
        _FULL_ENV, setup=_ROOT_KEYS + "export STUB_KEYFILE_STAT='0:0 620'\n",
        offbox=True, flags="--offbox",
    )
    assert r["rc"] != 0
    assert "is '0:0 620' (uid:gid mode). It must be '0:0 600'." in str(r["err"]), r["err"]
    assert "Nothing was uploaded" in str(r["err"])
    assert _rclone_calls(r["calls"]) == []


@pytest.mark.parametrize(
    "line",
    [
        "BACKUP_S3_SECRET_ACCESS_KEY=x",
        "export BACKUP_GPG_RECIPIENT=x",
        "BACKUP_OFFBOX_ENV_FILE=/tmp/k",
    ],
)
@pytest.mark.parametrize("where", ["app", "console"])
def test_a_key_line_in_an_acb_writable_env_file_is_an_error(line: str, where: str) -> None:
    """🔴 Each acb-writable env file that the unit loads: a key line there is
    ignored, the night uses the root file, and the run exits 1 with an
    ERROR. Before the fix the Console file was never checked, and the app
    file stopped the upload."""
    path = "$W/app/.env" if where == "app" else "$W/app/apps/services/customer_console/.env"
    setup = _ROOT_KEYS + f'mkdir -p "$(dirname "{path}")"; printf \'%s\\n\' \'{line}\' >> "{path}"\n'
    r = _run(_FULL_ENV, setup=setup, offbox=True, flags="--offbox")
    assert r["rc"] != 0, f"exit 0:\n{r['out']}\n{r['err']}"
    assert "holds a BACKUP_S3_*, BACKUP_GPG_* or BACKUP_OFFBOX_ENV_FILE" in str(r["err"]), r["err"]
    assert "off-box copy ok (offbox:metorite-backups/nightly/" in str(r["out"]), r["out"]


# The forced root run reads $R/etc/acb/backup-offbox.env. To the stub `stat`
# it is root:root 0600, like the harness file.
_ROOT_RUN_KEYFILE = '"$R/etc/acb/backup-offbox.env"'
# The pinned paths, named in the env too. The fixed run ignores them (they
# match its pins, so it says nothing). The run before the fix needs them to
# get past its mkdir, so it fails for the reason under test.
_LAYOUT_PATHS = 'APP_DIR="$R/opt/acb/app" BACKUP_DIR="$R/opt/acb/backups" '


def test_the_rsync_destination_comes_from_the_root_file_only() -> None:
    """🔴 BACKUP_REMOTE in the env made root rsync the whole night to the
    host it named. The root run reads it from the root file, in the shape
    [user@]host:path. Mutation: read the env, and rsync gets evil@evil."""
    setup = f"printf 'BACKUP_REMOTE=backup@vault.example:/srv/nights\\n' > {_ROOT_RUN_KEYFILE}\n"
    r = _run(_LAYOUT_PATHS + "BACKUP_REMOTE=evil@evil.example:/x", setup=setup, root=True, offbox=True)
    rs = _lines(r, "rsync ")
    assert len(rs) == 1 and rs[0].endswith(" backup@vault.example:/srv/nights/"), rs


def test_without_a_root_file_the_root_run_has_no_rsync_destination() -> None:
    """🔴 No root file: BACKUP_REMOTE from the env must still not count.
    Mutation: drop offbox_drop_root_names at the top of the root run, and
    rsync gets evil@evil."""
    r = _run(_LAYOUT_PATHS + "BACKUP_REMOTE=evil@evil.example:/x", root=True, offbox=True)
    assert _lines(r, "rsync ") == [], r["calls"]


@pytest.mark.parametrize("remote", ["-e sh@x:/y", "--rsh=sh x:/y", "host"])
def test_an_rsync_destination_of_another_shape_is_refused(remote: str) -> None:
    setup = f"printf 'BACKUP_REMOTE=%s\\n' '{remote}' > {_ROOT_RUN_KEYFILE}\n"
    r = _run(_LAYOUT_PATHS, setup=setup, root=True, offbox=True)
    assert _lines(r, "rsync ") == [], r["calls"]
    assert "BACKUP_REMOTE is not [user@]host:path" in str(r["err"]), r["err"]


# ── VALIDATED in backup_offbox.sh: the file dirs and the volume names ──────

_SYMLINK_SKIP = pytest.mark.skipif(sys.platform == "win32", reason="MSYS ln -s copies instead of linking")


_OUTSIDE = "BACKUP_FILE_DIRS names a directory outside"
_VIA_LINK = "goes through a symlink"


@pytest.mark.parametrize(
    ("dirs", "why"),
    [
        ("/etc", _OUTSIDE),
        ("/root", _OUTSIDE),
        ("/home/acb/.ssh", _OUTSIDE),
        ("$W/app/data/../../../../etc", _OUTSIDE),
        pytest.param("$W/app/data/link", _VIA_LINK, marks=_SYMLINK_SKIP),
        ("$W/app/data/att /etc", _OUTSIDE),
    ],
)
def test_file_dirs_outside_the_allowlist_are_not_tarred(dirs: str, why: str) -> None:
    """🔴 Root tars every dir that BACKUP_FILE_DIRS names. Only dirs under
    $APP_DIR/data and /home/acb/.acb/agents AS WRITTEN (`..` is folded
    first), with no symlink at or below the root. Outside means the default
    list, and a link means a skip, each with a WARN. Mutation: take the list
    as it comes, and tar gets etc."""
    setup = _ROOT_KEYS + 'mkdir -p "$W/app/data/att"; ln -s /etc "$W/app/data/link"\n'
    r = _run(_S3_ENV + _GPG_ENV + f'BACKUP_FILE_DIRS="{dirs}"', setup=setup, offbox=True, flags="--offbox")
    for ln in _lines(r, "tar "):
        args = ln.split()
        assert not any(
            a in ("etc", "root") or a.startswith(("etc/", "root/", "home/acb/.ssh")) or ".." in a
            or a.endswith("/link")
            for a in args
        ), ln
    assert why in str(r["err"]), r["err"]
    assert "off-box copy ok" in str(r["out"]), f"{r['out']}\n{r['err']}"


@pytest.mark.skipif(sys.platform == "win32", reason="MSYS ln -s copies instead of linking")
def test_a_symlinked_app_data_root_cannot_move_the_allow_list() -> None:
    """🔴 Fix round 1, P2. $APP_DIR/data is a symlink to /etc. Round 0
    resolved the ROOT through it, so /etc became the allow list and
    /etc/default was tarred. The roots are literal now, and a root that is a
    symlink is skipped. Mutation: resolve the root again, and tar gets etc."""
    setup = (
        _ROOT_KEYS
        + 'mkdir -p "$W/app2"; printf "POSTGRES_USER=acb\\n" > "$W/app2/.env"; ln -s /etc "$W/app2/data"\n'
    )
    r = _run(
        command=_S3_ENV + _GPG_ENV + 'BACKUP_FILE_DIRS="$W/app2/data/default" PG_MODE=local '
        'APP_DIR="$W/app2" BACKUP_DIR="$W/backups" bash scripts/backup_db.sh --offbox',
        setup=setup, offbox=True,
    )
    for ln in _lines(r, "tar "):
        assert not any(a == "etc" or a.startswith("etc/") for a in ln.split()), ln
    assert _VIA_LINK in str(r["err"]), r["err"]
    assert "off-box copy ok" in str(r["out"]), f"{r['out']}\n{r['err']}"


_AGENTS_SETUP = (
    _ROOT_KEYS
    + 'cp "$BACKUP_OFFBOX_ENV_FILE" {keyfile}\n'
    + 'mkdir -p "$R/opt/acb/app/data/gtd_attachments"; echo cv > "$R/opt/acb/app/data/gtd_attachments/cv.txt"\n'
)


@pytest.mark.skipif(sys.platform == "win32", reason="MSYS ln -s copies instead of linking")
@pytest.mark.parametrize("how", ["root-is-a-link", "a-dir-above-is-a-link"])
def test_a_symlinked_agents_root_is_skipped_and_the_default_list_still_works(how: str) -> None:
    """🔴 Fix round 1, P2. A root run, default list. The agents root is a
    symlink to /etc, or a dir above it is a symlink that a non-root user
    owns. Either way it is skipped with a WARN, /etc is not tarred, and the
    real attachments dir IS still tarred (the default list does not
    regress). Mutation: drop root_trusted, and tar gets etc."""
    if how == "root-is-a-link":
        link = 'mkdir -p "$R/home/acb/.acb"; ln -s /etc "$R/home/acb/.acb/agents"\n'
    else:
        link = 'mkdir -p "$W/elsewhere/acb/.acb/agents"; ln -s "$W/elsewhere" "$R/home"\n'
    setup = _AGENTS_SETUP.replace("{keyfile}", _ROOT_RUN_KEYFILE) + link
    r = _run(
        _LAYOUT_PATHS + _S3_ENV + _GPG_ENV + "BACKUP_FILE_DIRS=",
        setup=setup, root=True, offbox=True, flags="--offbox",
    )
    tars = _lines(r, "tar ")
    assert any("opt/acb/app/data/gtd_attachments" in ln for ln in tars), (tars, r["err"])
    for ln in tars:
        assert not any(a == "etc" or a.startswith("etc/") or "/home/" in a for a in ln.split()[4:]), ln
    assert _VIA_LINK in str(r["err"]), r["err"]
    assert "off-box copy ok" in str(r["out"]), f"{r['out']}\n{r['err']}"


@pytest.mark.parametrize("volume", ["--help", "a b;c", "-v"])
def test_a_meeting_bot_volume_of_another_shape_is_refused(volume: str) -> None:
    r = _run(
        _S3_ENV + _GPG_ENV + f"BACKUP_MEETING_BOT_VOLUME='{volume}'",
        setup=_ROOT_KEYS, offbox=True, flags="--offbox",
    )
    inspects = _lines(r, "docker volume inspect")
    assert inspects and not any(volume.split()[0] in ln.split()[3:] for ln in inspects), inspects
    assert "BACKUP_MEETING_BOT_VOLUME is not a list of volume names" in str(r["err"])


# ── PINNED for every child: the tool names, and BASH_ENV ───────────────────


def test_tool_settings_in_the_env_never_reach_the_off_box_child() -> None:
    """🔴 GNU tar takes options from TAR_OPTIONS (--checkpoint-action=exec
    runs a program), and a child bash runs the file BASH_ENV names. The
    off-box step runs both as root. Mutation: drop the clean start of
    backup_db.sh AND of backup_offbox.sh, and the marker files appear.

    ⚠️ The PARENT bash still reads BASH_ENV before its first line, so its
    own line is in the log. Only the unit can stop that (box_hardening.md
    BH-6). This test pins the child, so it is a ROOT run: in production
    only the root parent scrubs, and the child inherits that. The env also
    names the pinned paths, so the run before the fix gets as far."""
    setup = (
        _ROOT_KEYS
        + f'cp "$BACKUP_OFFBOX_ENV_FILE" {_ROOT_RUN_KEYFILE}\n'
        + 'mkdir -p "$R/opt/acb/app/data/att"; echo cv > "$R/opt/acb/app/data/att/cv.txt"\n'
        + "printf '#!/bin/sh\\ntouch \"$W/TAR-OPTIONS-RAN\"\\n' > \"$W/pwn.sh\"; chmod +x \"$W/pwn.sh\"\n"
        + "printf 'printf \"%%s\\\\n\" \"$0\" >> \"$W/bash_env.log\"\\n' > \"$W/benv.sh\"\n"
    )
    env = _S3_ENV + _GPG_ENV + (
        'APP_DIR="$R/opt/acb/app" BACKUP_DIR="$R/opt/acb/backups" '
        'BACKUP_OFFBOX_ENV_FILE="$R/etc/acb/backup-offbox.env" '
        'BACKUP_FILE_DIRS="$R/opt/acb/app/data/att" VOL_DIR="$W/files/att" '
        'TAR_OPTIONS="--checkpoint=1 --checkpoint-action=exec=$W/pwn.sh" BASH_ENV="$W/benv.sh"'
    )
    after = 'cat "$W/bash_env.log" 2>/dev/null | sed "s/^/BASH-ENV-RAN-IN /" >&2\n'
    r = _run(env, setup=setup, offbox=True, root=True, flags="--offbox", after=after)
    assert "off-box copy ok" in str(r["out"]), f"{r['out']}\n{r['err']}"
    assert any(ln.startswith("tar ") for ln in str(r["calls"]).splitlines()), "tar never ran"
    assert "TAR-OPTIONS-RAN" not in r["files"], "TAR_OPTIONS reached the root tar"  # type: ignore[operator]
    ran_in = [ln for ln in str(r["err"]).splitlines() if ln.startswith("BASH-ENV-RAN-IN ")]
    assert ran_in, "the harness never exercised BASH_ENV"
    assert not any(ln.endswith("backup_offbox.sh") for ln in ran_in), ran_in


def test_the_off_box_step_starts_clean_on_its_own() -> None:
    """🔴 backup_offbox.sh runs as root and can be started on its own (by
    hand, or by a parent that did not start clean). It starts again under
    its own allow list, so TAR_OPTIONS never reaches its tar. Mutation: drop
    its offbox_clean_env_reexec call, and the marker file appears."""
    night = "$W/backups/2026-01-01T000000Z"
    setup = (
        _ROOT_KEYS
        + f'mkdir -p "{night}"; echo DUMP > "{night}/acb.dump"\n'
        + "printf '#!/bin/sh\\ntouch \"$W/TAR-OPTIONS-RAN\"\\n' > \"$W/pwn.sh\"; chmod +x \"$W/pwn.sh\"\n"
    )
    env = _S3_ENV + _GPG_ENV + 'TAR_OPTIONS="--checkpoint=1 --checkpoint-action=exec=$W/pwn.sh"'
    r = _run(
        command=f'{env} BACKUP_ENV_GUARD=1 bash scripts/backup_offbox.sh "{night}" 2026-01-01T000000Z '
                '"$W/app" "$BACKUP_OFFBOX_ENV_FILE"',
        setup=setup, offbox=True,
    )
    assert "off-box copy ok" in str(r["out"]), f"{r['out']}\n{r['err']}"
    assert any(ln.startswith("tar ") for ln in str(r["calls"]).splitlines()), "tar never ran"
    assert "TAR-OPTIONS-RAN" not in r["files"], "TAR_OPTIONS reached the root tar"  # type: ignore[operator]


@pytest.mark.parametrize("stat", ["0:0 620", "1000:0 600"])
def test_the_off_box_step_checks_the_key_file_itself(stat: str) -> None:
    """backup_offbox.sh is an entry point of its own, so it checks the key
    file again and does not trust its caller. Mutation: drop that check, and
    rclone runs."""
    night = "$W/backups/2026-01-01T000000Z"
    setup = _ROOT_KEYS + f'mkdir -p "{night}"; echo DUMP > "{night}/acb.dump"\n'
    r = _run(
        command=f"STUB_KEYFILE_STAT='{stat}' bash scripts/backup_offbox.sh \"{night}\" "
                '2026-01-01T000000Z "$W/app" "$BACKUP_OFFBOX_ENV_FILE"',
        setup=setup, offbox=True,
    )
    assert r["rc"] != 0
    assert "It must be '0:0 600'" in str(r["err"]), r["err"]
    assert _rclone_calls(r["calls"]) == []


@pytest.mark.skipif(sys.platform == "win32", reason="MSYS ln -s copies instead of linking")
def test_a_symlinked_root_file_is_refused() -> None:
    """The key file must be a plain file. A link could point at a file that
    another user controls. (The stub `stat` says 0:0 600 for any path, so
    only the symlink check stops this.)"""
    setup = (
        _ROOT_KEYS
        + 'mv "$BACKUP_OFFBOX_ENV_FILE" "$W/real.env"; ln -s "$W/real.env" "$BACKUP_OFFBOX_ENV_FILE"\n'
    )
    r = _run(_FULL_ENV, setup=setup, offbox=True, flags="--offbox")
    assert r["rc"] != 0
    assert "is a symlink" in str(r["err"]), r["err"]
    assert _rclone_calls(r["calls"]) == []


@pytest.mark.parametrize("backup_dir", ["/", "//", "relative/dir"])
def test_backup_dir_must_be_an_absolute_dir_that_is_not_root(backup_dir: str) -> None:
    """Retention deletes only "$BACKUP_DIR/<stamp>". With "/" that is a
    night-named dir at the top of the disk. The run stops first."""
    r = _run(command=f"PG_MODE=local APP_DIR=\"$W/app\" BACKUP_DIR='{backup_dir}' bash scripts/backup_db.sh")
    assert r["rc"] == 2, f"exit {r['rc']}:\n{r['out']}\n{r['err']}"
    assert "BACKUP_DIR must be an absolute path, and not /." in str(r["err"])
    assert not _lines(r, "pg_dump"), "a refused run still dumped"


def test_the_off_box_step_stages_only_under_a_night() -> None:
    """🔴 backup_offbox.sh deletes its staging dir on exit. A <dest> that is
    not <dir>/<stamp> is refused before the trap is set, so the delete cannot
    reach a dir of the caller's choice. Mutation: drop the shape check, and
    $W/victim/offbox.work goes."""
    setup = 'mkdir -p "$W/victim/offbox.work"; echo keep > "$W/victim/offbox.work/keep"\n'
    after = '[ -f "$W/victim/offbox.work/keep" ] && echo "VICTIM-KEPT" >&2\n'
    r = _run(
        command='bash scripts/backup_offbox.sh "$W/victim" 2026-01-01T000000Z "$W/app" "$BACKUP_OFFBOX_ENV_FILE"',
        setup=setup, offbox=True, after=after,
    )
    assert "VICTIM-KEPT" in str(r["err"]), "the off-box step deleted a dir it did not make"
    assert r["rc"] != 0
    assert "refusing to stage in" in str(r["err"]), r["err"]


# ── The units: the names that act before the script ────────────────────────
#
# bash reads BASH_ENV, SHELLOPTS and PS4 before the first line of a script,
# and ld.so reads LD_PRELOAD before bash runs. No script can refuse them. So a
# ROOT unit that loads an acb-writable env file must drop them with
# UnsetEnvironment=, which systemd applies after every Environment= and
# EnvironmentFile= line (systemd.exec(5), v255). A stopgap until BH-6.

_UNITS = _ROOT / "deploy/hostinger"
#: Every root unit that loads an env file under /opt/acb carries ALL of these.
_PRE_SCRIPT_TEXT = (
    "BASH_ENV ENV SHELLOPTS BASHOPTS PS4 PROMPT_COMMAND BASH_XTRACEFD GLOBIGNORE CDPATH IFS "
    "LD_PRELOAD LD_LIBRARY_PATH LD_AUDIT LD_DEBUG LD_DEBUG_OUTPUT LD_PROFILE LD_BIND_NOW "
    "GCONV_PATH LOCPATH NLSPATH HOSTALIASES RESOLV_HOST_CONF MALLOC_CHECK_ OPENSSL_CONF "
    "OPENSSL_ENGINES PYTHONSTARTUP PYTHONPATH PYTHONHOME PERL5LIB PERL5OPT NODE_OPTIONS RUBYOPT "
    "GIT_CONFIG_GLOBAL GIT_CONFIG_SYSTEM GIT_CONFIG_COUNT GIT_EXEC_PATH GIT_SSH_COMMAND "
    "DOCKER_HOST DOCKER_CONTEXT DOCKER_CONFIG DOCKER_CERT_PATH DOCKER_TLS_VERIFY "
    "TAR_OPTIONS PSQLRC PGSYSCONFDIR PGSERVICEFILE PGPASSFILE TMPDIR "
    # Fix round 1: proxies, CAs, Kerberos and GSSAPI, ls, and compose (P3).
    "http_proxy https_proxy HTTP_PROXY HTTPS_PROXY ftp_proxy FTP_PROXY all_proxy ALL_PROXY "
    "no_proxy NO_PROXY SSL_CERT_FILE SSL_CERT_DIR CURL_CA_BUNDLE KRB5_CONFIG KRB5CCNAME "
    "KRB5_KTNAME KRB5_CLIENT_KTNAME KRB5_TRACE KRB5RCACHETYPE KRB5RCACHEDIR GSS_MECH_CONFIG "
    "QUOTING_STYLE COMPOSE_PROJECT_NAME COMPOSE_ENV_FILES"
)
_PRE_SCRIPT_NAMES = frozenset(_PRE_SCRIPT_TEXT.split())
#: The floor the coordinator named. Kept apart, so a mutation of the list
#: above that drops one of these names reads as what it is.
_PRE_SCRIPT_FLOOR = frozenset({"BASH_ENV", "LD_PRELOAD", "SHELLOPTS", "PS4", "GCONV_PATH"})


def _effective_service_keys(unit: object) -> dict[str, list[str]]:
    """The keys of a unit plus its repo drop-ins (<unit>.d/*.conf, in name
    order), as systemd merges them. A key repeats. An empty
    UnsetEnvironment= resets that list, as systemd does."""
    import pathlib

    path = pathlib.Path(str(unit))
    files = [path, *sorted((path.parent / f"{path.name}.d").glob("*.conf"))]
    keys: dict[str, list[str]] = {}
    for f in files:
        for raw in f.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith(("#", ";", "[")) or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip()
            if k == "UnsetEnvironment" and not v:
                keys[k] = []
                continue
            keys.setdefault(k, []).append(v)
    return keys


def _root_units_with_an_app_env_file() -> list[str]:
    found = []
    for unit in sorted(_UNITS.glob("*.service")):
        keys = _effective_service_keys(unit)
        user = (keys.get("User") or ["root"])[-1]
        env_files = [v.lstrip("-") for v in keys.get("EnvironmentFile", [])]
        if user in ("root", "0") and any(f.startswith("/opt/acb/") for f in env_files):
            found.append(unit.name)
    return found


def test_the_scan_finds_the_root_unit_that_loads_the_app_env() -> None:
    """The companion: a scan that finds nothing passes the test below.
    acb.service loads NO env file since fix round 1 (F1), so it is not here."""
    found = set(_root_units_with_an_app_env_file())
    assert "acb-backup.service" in found, found
    assert "acb.service" not in found, "acb.service loads an env file again"


_PINNED_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


def _root_units_that_run_docker() -> list[str]:
    found = []
    for unit in sorted(_UNITS.glob("*.service")):
        keys = _effective_service_keys(unit)
        if (keys.get("User") or ["root"])[-1] not in ("root", "0"):
            continue
        execs = [v for k in ("ExecStartPre", "ExecStart", "ExecStartPost", "ExecReload", "ExecStop")
                 for v in keys.get(k, [])]
        if any("/docker " in v or v.startswith("docker ") for v in execs):
            found.append(unit.name)
    return found


def test_no_root_unit_that_runs_docker_loads_an_env_file_under_opt_acb() -> None:
    """🔴 Fix round 1, F1. Docker reads $HOME/.docker/config.json and its
    cli-plugins dir, and an acb-writable HOME made root docker run a plugin.
    So a root unit that runs docker loads NO env file under /opt/acb, and
    each of its docker calls starts under `env -i` with a pinned PATH and
    HOME=/root. A compose call that reads .env names the project with
    `-p acb`, or a COMPOSE_PROJECT_NAME line in .env renames it. Mutation:
    put EnvironmentFile=/opt/acb/app/.env back in acb.service, and this goes
    red."""
    units = _root_units_that_run_docker()
    assert "acb.service" in units, units
    clean = f"/usr/bin/env -i PATH={_PINNED_PATH} HOME=/root /usr/bin/docker "
    for unit in units:
        keys = _effective_service_keys(_UNITS / unit)
        env_files = [v.lstrip("-") for v in keys.get("EnvironmentFile", [])]
        assert not [f for f in env_files if f.startswith("/opt/acb/")], f"{unit} loads {env_files}"
        for k in ("ExecStartPre", "ExecStart", "ExecStartPost", "ExecReload", "ExecStop"):
            for v in keys.get(k, []):
                if "docker" not in v:
                    continue
                assert v.startswith(clean), f"{unit} {k} runs docker with an inherited env: {v}"
                if "--env-file" in v:
                    assert " -p acb " in v, f"{unit} {k} reads .env with no -p acb: {v}"


@pytest.mark.parametrize("unit", _root_units_with_an_app_env_file())
def test_a_root_unit_that_loads_the_app_env_drops_the_pre_script_names(unit: str) -> None:
    """🔴 Each root unit that loads an env file under /opt/acb drops every
    name of the list with UnsetEnvironment=. Mutation: delete the
    UnsetEnvironment= lines from acb-backup.service, and this goes red."""
    keys = _effective_service_keys(_UNITS / unit)
    dropped = {n for v in keys.get("UnsetEnvironment", []) for n in v.split()}
    assert not _PRE_SCRIPT_FLOOR - dropped, f"{unit} keeps {sorted(_PRE_SCRIPT_FLOOR - dropped)}"
    assert not _PRE_SCRIPT_NAMES - dropped, f"{unit} keeps {sorted(_PRE_SCRIPT_NAMES - dropped)}"
    assert not [n for n in dropped if "=" in n], "drop by NAME, so every value of it goes"


# ── Fix round 1: the ALLOW list of the root run ────────────────────────────
#
# A deny list misses the name nobody thought of. The root run now starts
# again under `env -i` with an explicit allow list (offbox_clean_env_reexec).
# These stubs print the names of the env they got, so the test sees exactly
# what pg_dump, rclone and ls would get.

_ENV_LOG = r"""
envlog() { printf 'ENV %s %s\n' "$1" "$(compgen -e | sort | tr '\n' ' ')" >> "$CALLS"; }
eval "_orig_pg_dump () $(declare -f pg_dump | sed 1d)"
eval "_orig_rclone () $(declare -f rclone | sed 1d)"
pg_dump() { envlog pg_dump; _orig_pg_dump "$@"; }
rclone() { envlog rclone; _orig_rclone "$@"; }
ls() { envlog ls; command ls "$@"; }
export -f envlog _orig_pg_dump _orig_rclone pg_dump rclone ls
"""

_HOSTILE = {
    "https_proxy": "http://evil.example:3128", "HTTPS_PROXY": "http://evil.example:3128",
    "SSL_CERT_FILE": "/tmp/evil.pem", "KRB5_TRACE": "/tmp/krb5.trace",
    "QUOTING_STYLE": "shell-escape", "AWS_ACCESS_KEY_ID": "AKIAEVIL",
    "SOME_NAME_NOBODY_LISTED": "x",
}


def _reexec_allow_list(script: str) -> set[str]:
    """The names a script passes to offbox_clean_env_reexec, read from the
    script itself, so this test checks the list the code really uses."""
    text = (_ROOT / script).read_text(encoding="utf-8").replace("\\\n", " ")
    line = next(ln for ln in text.splitlines() if ln.strip().startswith("offbox_clean_env_reexec "))
    return set(line.split(" -- ")[0].split()[2:])


def _keep_names() -> set[str]:
    import re

    m = re.search(r'export BACKUP_ENV_GUARD_KEEP="([^"]*)"', _STUBS.replace("\\\n", ""))
    assert m, "the harness lost its BACKUP_ENV_GUARD_KEEP"
    return set(m.group(1).split())


def test_the_allow_list_is_the_only_source_of_a_root_child_env() -> None:
    """🔴 A root run with a hostile .env: a proxy, a CA file, a Kerberos
    trace, QUOTING_STYLE (ls), a cloud key and a name that NO list knows.
    pg_dump, rclone and ls see none of them. Every name they DO see is on
    the allow list of backup_db.sh or backup_offbox.sh, or is one the clean
    start sets itself, or a test knob. Mutation: drop the clean start, and
    every hostile name reaches them."""
    setup = _ROOT_KEYS + f'cp "$BACKUP_OFFBOX_ENV_FILE" {_ROOT_RUN_KEYFILE}\n' + _ENV_LOG
    hostile = " ".join(f"{k}='{v}'" for k, v in _HOSTILE.items())
    r = _run(
        _LAYOUT_PATHS + _S3_ENV + _GPG_ENV + hostile,
        setup=setup, root=True, offbox=True, flags="--offbox",
    )
    assert "off-box copy ok" in str(r["out"]), f"{r['out']}\n{r['err']}"
    seen: dict[str, set[str]] = {}
    for ln in _lines(r, "ENV "):
        _tag, who, *names = ln.split()
        seen.setdefault(who, set()).update(names)
    assert {"pg_dump", "rclone", "ls"} <= set(seen), sorted(seen)
    allowed = (
        _reexec_allow_list("scripts/backup_db.sh") | _reexec_allow_list("scripts/backup_offbox.sh")
        | {"PATH", "HOME", "LANG", "LC_ALL", "PWD", "SHLVL", "_", "OLDPWD", "BACKUP_ENV_GUARD",
           "BACKUP_ENV_GUARD_KEEP", "PGGSSENCMODE", "PGSSLCERTMODE"}
        | _keep_names()
    )
    for who, names in seen.items():
        assert not set(_HOSTILE) & names, f"{who} got {sorted(set(_HOSTILE) & names)}"
        extra = {n for n in names - allowed if not n.startswith("RCLONE_CONFIG")}
        assert not extra, f"{who} got names that no allow list holds: {sorted(extra)}"
    rclone_names = seen["rclone"] - _keep_names()
    assert rclone_names <= {"PATH", "HOME", "LANG", "LC_ALL", "PWD", "SHLVL", "_", "OLDPWD"} | {
        n for n in rclone_names if n.startswith("RCLONE_CONFIG")
    }, f"rclone got more than its own allow list: {sorted(rclone_names)}"
    assert "PGGSSENCMODE" in seen["pg_dump"], "the root run did not turn GSSAPI off for libpq"


def test_the_clean_start_runs_env_i_and_refuses_a_dirty_second_pass() -> None:
    """Static half. The clean start is `exec /usr/bin/env -i`, and its second
    pass refuses any name that is not on the list. Mutation: replace `env -i`
    with plain `env`, and the dynamic test above goes red."""
    lib = (_ROOT / "scripts/offbox_lib.sh").read_text(encoding="utf-8")
    assert 'exec /usr/bin/env -i "${env_args[@]}" /bin/bash "$script" "$@"' in lib
    assert "after the clean start, the env still holds" in lib


# ── Fix round 1: the server that a root run dumps is pinned ─────────────────

_PIN_FILE = (
    "printf 'BACKUP_PG_HOST=db.example\\nBACKUP_PG_USER_SUFFIX=.abcref\\n"
    "BACKUP_CC_PG_HOST=cc.example\\nBACKUP_CC_PG_USER_SUFFIX=cc\\n' > " + _ROOT_RUN_KEYFILE + "\n"
    "printf 'POSTGRES_USER=postgres.abcref\\n' > \"$R/opt/acb/app/.env\"\n"
)
_PLANT_R = (
    'for d in $(seq -w 1 20); do mkdir -p "$R/opt/acb/backups/2026-01-${d}T000000Z"; done\n'
)
_COUNT_R = 'echo "NIGHTS=$(ls -1d "$R"/opt/acb/backups/2*Z 2>/dev/null | wc -l)" >&2\n'


def test_a_pinned_server_that_matches_is_dumped() -> None:
    """The companion: with the pins set and matched, the night is dumped,
    and no pin WARN is printed."""
    r = _run(
        "PGHOST=db.example CUSTOMER_CONSOLE_DATABASE_URL=postgresql://cc:pw@cc.example:5432/postgres",
        setup=_PIN_FILE, root=True, offbox=True,
    )
    assert r["rc"] == 0, f"exit {r['rc']}:\n{r['out']}\n{r['err']}"
    assert any(" -U postgres.abcref -d acb " in ln for ln in _lines(r, "pg_dump ")), r["calls"]
    assert _lines(r, "pg_dump -d postgresql://cc:pw@cc.example"), "the Console was not dumped"
    assert "is not set in" not in str(r["err"]), r["err"]


@pytest.mark.parametrize(
    ("command", "why"),
    [
        (f"PGHOST=evil.example {_ROOT_RUN}", "PGHOST is not the server that BACKUP_PG_HOST"),
        (
            "PGHOST=db.example " + _ROOT_RUN,
            "POSTGRES_USER in",
        ),
        (
            'BACKUP_ENV_GUARD=1 PG_MODE=docker PGHOST=db.example '
            'bash "$W/root/opt/acb/app/scripts/backup_db.sh"',
            "pins the database server, and PG_MODE is not local",
        ),
    ],
    ids=["host", "user", "docker-mode"],
)
def test_a_pinned_server_that_does_not_match_is_refused_with_no_prune(command: str, why: str) -> None:
    """🔴 Fix round 1, P2. A changed .env points the root dump at another
    server, another project, or the empty local container. The run stops
    before the first dump: exit 1, no dump, and retention does NOT run, so
    20 old nights stay. Mutation: drop the host check (or the user check,
    or the PG_MODE check), and its case dumps."""
    setup = _PIN_FILE + _PLANT_R
    if "user" in why or "POSTGRES_USER" in why:
        setup += "printf 'POSTGRES_USER=postgres.evilref\\n' > \"$R/opt/acb/app/.env\"\n"
    r = _run(command=f"KEEP_DAILY=3 {command}", setup=setup, root=True, offbox=True, after=_COUNT_R)
    assert r["rc"] == 1, f"exit {r['rc']}:\n{r['out']}\n{r['err']}"
    assert why in str(r["err"]), r["err"]
    assert "The backup REFUSED this server" in str(r["err"])
    assert not _lines(r, "pg_dump") and not _lines(r, "pg_dumpall"), r["calls"]
    assert "NIGHTS=20" in str(r["err"]), "a refused night pruned the old ones"


@pytest.mark.parametrize("pinned", [False, True], ids=["no-pin", "pinned"])
def test_a_pghost_list_is_refused_before_any_dump(pinned: bool) -> None:
    """🔴 Fix round 2. PGHOST="db.example,evil.example" would pass a pin of
    db.example on its first host, and libpq would then fail over to the
    second. A root run refuses ANY host list, pinned or not: exit 1, no
    dump, no prune. Mutation: drop the comma check, and the no-pin case
    dumps."""
    setup = (_PIN_FILE if pinned else "") + _PLANT_R
    r = _run(
        "KEEP_DAILY=3 PGHOST=db.example,evil.example", setup=setup, root=True, offbox=True,
        after=_COUNT_R,
    )
    assert r["rc"] == 1, f"exit {r['rc']}:\n{r['out']}\n{r['err']}"
    assert "PGHOST names more than one host" in str(r["err"]), r["err"]
    assert "The backup REFUSED this server" in str(r["err"])
    assert not _lines(r, "pg_dump") and not _lines(r, "pg_dumpall"), r["calls"]
    assert "NIGHTS=20" in str(r["err"]), "a refused night pruned the old ones"


def test_an_absent_pin_warns_and_still_backs_up() -> None:
    """Rollout: a box whose root file has no pins yet still backs up, and
    says loudly that the target is not pinned."""
    r = _run("PGHOST=db.example", root=True, offbox=True)
    assert r["rc"] == 0, f"exit {r['rc']}:\n{r['out']}\n{r['err']}"
    assert "BACKUP_PG_HOST is not set in" in str(r["err"]), r["err"]
    assert "BACKUP_PG_USER_SUFFIX is not set in" in str(r["err"]), r["err"]
    assert _lines(r, "pg_dump "), "an unpinned box stopped backing up"


@pytest.mark.parametrize(
    "dsn",
    [
        "postgresql://cc:pw@evil.example:5432/postgres",
        "postgresql://evil:pw@cc.example:5432/postgres",
    ],
    ids=["host", "user"],
)
def test_a_console_dsn_that_misses_its_pins_skips_the_console_only(dsn: str) -> None:
    """🔴 The Console has its own pins. A mismatch skips the Console dump,
    keeps the app night, and exits 1 at the end. Mutation: drop the Console
    host check, and pg_dump gets evil.example."""
    r = _run(f"PGHOST=db.example CUSTOMER_CONSOLE_DATABASE_URL='{dsn}'", setup=_PIN_FILE, root=True, offbox=True)
    assert r["rc"] != 0, f"exit 0:\n{r['out']}\n{r['err']}"
    assert not _lines(r, "pg_dump -d "), "the Console was dumped from a server that is not pinned"
    assert "BACKUP_CC_PG_USER_SUFFIX" in str(r["err"]) and "is NOT in this backup" in str(r["err"])
    assert any(f.endswith("/acb.dump") for f in r["files"]), "the app night was lost"  # type: ignore[union-attr]
