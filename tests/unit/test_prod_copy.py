"""`scripts/prod_copy.sh` brings production's reduced copy to this PC, and keeps it safe.

Spec: `specs/engineering_practice.md` §1.3.1 · Metorite task LD-8a (1230).

The copy holds real customer rows. So the script must fail closed, and these
tests fail when a change lets it:

* keep a copy that does not match its SHA256SUMS, or a stale one;
* leave a folder with a final name after a failed pull (LD-8b reads it);
* put the copy inside a git work tree, or let another user read it;
* keep more than 2 copies, or print the content of a file.

Hermetic. `PROD_COPY_SSH` points at a stub that runs the remote command here,
on a fake export in a temp folder. No test reaches production.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "prod_copy.sh"
REFRESH = ROOT / "scripts" / "staging_refresh.sh"
BODY = SCRIPT.read_text(encoding="utf-8")
FILES = ("staging.dump", "roles.txt", "extensions.txt", "flags.env")
STAMP = re.compile(r"^\d{8}T\d{6}Z$")
RFOLDER = "r.20261101T023814Z.4242"

# Each fake file holds a marker. No output of the script may hold one.
MARKERS = {
    "staging.dump": "PGDMP_MARKER_dump_7f3a",
    "roles.txt": "acb_app_MARKER_role",
    "extensions.txt": "vector|MARKER_ext",
    "flags.env": "root:MARKER_FLAG_ENABLED=true",
}

# A stand-in for ssh. It logs each call, then runs the remote command here,
# with the production export folder mapped onto the fake one. During the
# pull, it also lists the names in the base folder.
STUB = r"""#!/usr/bin/env bash
printf '%s\n' "$*" >> "$STUB_LOG"
case "$*" in *"-cf -"*) ls -A "$PROD_COPY_DIR" > "$STUB_LOG.during" ;; esac
while [ $# -gt 0 ]; do
  case "$1" in -o) shift 2 ;; -*) shift ;; *) break ;; esac
done
[ "$1" = metorite ] || { echo "stub: unknown host $1" >&2; exit 255; }
shift
root="$STUB_ROOT"
if command -v cygpath >/dev/null 2>&1; then root="$(cygpath -u "$root")"; fi
cmd="$*"
exec bash -c "${cmd//\/var\/lib\/acb-staging-export/$root}"
"""


def _bash() -> str | None:
    if os.name == "nt":
        # Git's bash, not WSL's bash.exe, as in test_daily_release.py.
        git_bash = Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Git/bin/bash.exe"
        return str(git_bash) if git_bash.is_file() else None
    return shutil.which("bash")


_BASH = _bash()
needs_bash = pytest.mark.skipif(_BASH is None, reason="needs bash")
needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
windows_only = pytest.mark.skipif(os.name != "nt", reason="the NTFS ACL exists on Windows only")
posix_only = pytest.mark.skipif(os.name == "nt", reason="NTFS ignores a POSIX mode")


@dataclass
class Export:
    root: Path      # stands for /var/lib/acb-staging-export on production
    folder: Path    # the r.<stamp> folder that `current` points to
    symlinked: bool
    mtime: int


def _write_sums(folder: Path, names: tuple[str, ...] = FILES, extra: str = "") -> None:
    lines = [f"{hashlib.sha256((folder / n).read_bytes()).hexdigest()}  {n}\n" for n in names]
    (folder / "SHA256SUMS").write_bytes(("".join(lines) + extra).encode("utf-8"))


def _set_age(export: Export, hours: float) -> None:
    export.mtime = int(time.time() - hours * 3600)
    for dump in {export.folder / "staging.dump", export.root / "current" / "staging.dump"}:
        os.utime(dump, (export.mtime, export.mtime))


def _make_export(tmp_path: Path, hours: float = 1.0) -> Export:
    root = tmp_path / "export"
    folder = root / RFOLDER
    folder.mkdir(parents=True)
    for name, marker in MARKERS.items():
        (folder / name).write_bytes(f"{marker}\nsecond line of {name}\n".encode())
    _write_sums(folder)
    current = root / "current"
    try:
        os.symlink(RFOLDER, current, target_is_directory=True)
        symlinked = True
    except OSError:  # Windows with no symlink right: `current` becomes a copy
        shutil.copytree(folder, current)
        symlinked = False
    export = Export(root, folder, symlinked, 0)
    _set_age(export, hours)
    return export


def _stamp(mtime: int) -> str:
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(mtime))


def _run(tmp_path: Path, export: Export, *args: str, base: Path | None = None
         ) -> subprocess.CompletedProcess[str]:
    stub = tmp_path / "ssh_stub"
    if not stub.exists():
        stub.write_bytes(STUB.encode("utf-8"))
        stub.chmod(0o755)
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("PROD_COPY_", "GIT_DIR", "GIT_WORK_TREE"))}
    env.update({
        "PROD_COPY_SSH": stub.as_posix(),
        "PROD_COPY_DIR": (base or tmp_path / "prod-copy").as_posix(),
        "STUB_ROOT": export.root.as_posix(),
        "STUB_LOG": (tmp_path / "ssh.log").as_posix(),
    })
    return subprocess.run([_BASH, SCRIPT.as_posix(), *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=120, env=env)


def _calls(tmp_path: Path) -> list[str]:
    log = tmp_path / "ssh.log"
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


def _tar_calls(tmp_path: Path) -> list[str]:
    return [c for c in _calls(tmp_path) if "-cf -" in c]


def _copies(base: Path) -> list[str]:
    if not base.is_dir():
        return []
    return sorted(p.name for p in base.iterdir() if p.is_dir() and STAMP.match(p.name))


def _code(text: str) -> str:
    return "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))


# ── The command line ─────────────────────────────────────────────────────────


@needs_bash
def test_help_prints_the_usage_and_exits_0(tmp_path: Path) -> None:
    res = _run(tmp_path, _make_export(tmp_path), "--help")
    assert res.returncode == 0, res.stderr
    assert "bash scripts/prod_copy.sh" in res.stdout
    assert "36 hours" in res.stdout
    assert "Fence: tests/unit/test_prod_copy.py" in res.stdout
    assert _calls(tmp_path) == []


@needs_bash
def test_an_unknown_argument_is_a_usage_error_and_touches_nothing(tmp_path: Path) -> None:
    res = _run(tmp_path, _make_export(tmp_path), "--force")
    assert res.returncode == 2
    assert not (tmp_path / "prod-copy").exists()
    assert _calls(tmp_path) == []


# ── A good pull ──────────────────────────────────────────────────────────────


@needs_bash
def test_a_good_copy_lands_in_one_folder_named_for_the_export(tmp_path: Path) -> None:
    export = _make_export(tmp_path)
    base = tmp_path / "prod-copy"
    res = _run(tmp_path, export)
    assert res.returncode == 0, res.stderr
    stamp = _stamp(export.mtime)
    assert _copies(base) == [stamp]
    copy = base / stamp
    assert sorted(p.name for p in copy.iterdir()) == sorted((*FILES, "SHA256SUMS"))
    for name in (*FILES, "SHA256SUMS"):
        assert (copy / name).read_bytes() == (export.folder / name).read_bytes(), name
    # No .partial folder and no lock stays behind: the base holds the copy only.
    assert [p.name for p in base.iterdir()] == [stamp]
    # It prints the age, the folder and the size.
    assert re.search(r"is 1 hours old", res.stdout), res.stdout
    assert re.search(rf"{stamp} \([0-9.,]+[KMGT]?\)", res.stdout), res.stdout


@needs_bash
def test_the_pull_follows_the_current_symlink(tmp_path: Path) -> None:
    """`current` is a symlink to one r.<stamp> folder (staging_export.sh). The
    pull resolves it once, so the age and the files come from one folder."""
    export = _make_export(tmp_path)
    if not export.symlinked:
        pytest.skip("this Windows account may not make a symlink")
    res = _run(tmp_path, export)
    assert res.returncode == 0, res.stderr
    probe, *_ = _calls(tmp_path)
    assert "readlink -f /var/lib/acb-staging-export/current" in probe
    (tar,) = _tar_calls(tmp_path)
    assert f"/{RFOLDER}'" in tar and "current" not in tar, tar
    copy = tmp_path / "prod-copy" / _stamp(export.mtime)
    assert (copy / "staging.dump").read_bytes() == (export.folder / "staging.dump").read_bytes()


@needs_bash
def test_it_pulls_over_ssh_metorite_with_tar_and_never_rsync(tmp_path: Path) -> None:
    """Git Bash has no rsync, so the pull is `ssh ... tar`. The stub refuses
    every host but `metorite`."""
    res = _run(tmp_path, _make_export(tmp_path))
    assert res.returncode == 0, res.stderr
    calls = _calls(tmp_path)
    assert len(calls) == 2
    assert all(" metorite " in c and "BatchMode=yes" in c for c in calls)
    assert "rsync" not in _code(BODY)
    assert 'EXPORT=/var/lib/acb-staging-export/current' in BODY


@needs_bash
def test_a_second_pull_of_the_same_export_pulls_nothing(tmp_path: Path) -> None:
    export = _make_export(tmp_path)
    assert _run(tmp_path, export).returncode == 0
    res = _run(tmp_path, export)
    assert res.returncode == 0, res.stderr
    assert "already here" in res.stdout
    assert len(_tar_calls(tmp_path)) == 1
    assert _copies(tmp_path / "prod-copy") == [_stamp(export.mtime)]


# ── The checks that refuse ───────────────────────────────────────────────────


@needs_bash
def test_a_bad_checksum_is_refused_and_keeps_nothing(tmp_path: Path) -> None:
    export = _make_export(tmp_path)
    (export.folder / "roles.txt").write_bytes(b"a role that the sums do not know\n")
    base = tmp_path / "prod-copy"
    res = _run(tmp_path, export)
    assert res.returncode == 1, res.stderr
    assert "REFUSED" in res.stderr and "roles.txt" in res.stderr
    assert list(base.iterdir()) == [], "a failed check left a folder behind"


@needs_bash
@pytest.mark.parametrize("names,extra", [
    (FILES[:3], ""),
    (("staging.dump", "staging.dump", "roles.txt", "extensions.txt"), ""),
    (FILES, f"{'0' * 64}  ../../outside.txt\n"),
], ids=["leaves-out-flags.env", "names-a-file-twice", "reaches-outside"])
def test_sums_that_do_not_name_the_four_files_are_refused(
        tmp_path: Path, names: tuple[str, ...], extra: str) -> None:
    """`sha256sum -c` passes a SHA256SUMS that lists one file only. The
    script must also prove that the sums cover all four data files."""
    export = _make_export(tmp_path)
    _write_sums(export.folder, names, extra)
    res = _run(tmp_path, export)
    assert res.returncode == 1, res.stderr
    assert "SHA256SUMS does not name the four data files" in res.stderr
    assert _copies(tmp_path / "prod-copy") == []


@needs_bash
def test_a_stale_copy_is_refused_before_the_download(tmp_path: Path) -> None:
    res = _run(tmp_path, _make_export(tmp_path, hours=40))
    assert res.returncode == 1
    assert "is 40 hours old" in res.stdout
    assert "REFUSED" in res.stderr and "limit is 36" in res.stderr
    assert _tar_calls(tmp_path) == [], "it downloaded a copy that it then refused"
    assert _copies(tmp_path / "prod-copy") == []


@needs_bash
@pytest.mark.parametrize("hours,code", [(36.5, 0), (37.5, 1)])
def test_the_age_limit_is_36_whole_hours(tmp_path: Path, hours: float, code: int) -> None:
    """The rule of staging_refresh.sh step 3: whole hours, 36 at most. The
    good-copy test above covers a fresh copy."""
    assert _run(tmp_path, _make_export(tmp_path, hours=hours)).returncode == code


def test_the_age_rule_is_the_one_staging_refresh_uses() -> None:
    """One rule for "too old". Two copies that drift would let this PC keep
    a copy that staging refuses."""
    rule = re.compile(r'\[ "\$age_h" -le (\d+) \]')
    assert rule.findall(_code(BODY)) == rule.findall(_code(REFRESH.read_text(encoding="utf-8")))
    assert "/ 3600 ))" in BODY
    assert "stat -c %Y" in BODY, "the age must come from the remote mtime"


@needs_bash
def test_a_failed_pull_keeps_no_folder_with_a_final_name(tmp_path: Path) -> None:
    """LD-8b takes the newest folder by name. A half pull must never look
    like a copy."""
    export = _make_export(tmp_path)
    (export.folder / "flags.env").unlink()
    if not export.symlinked:
        (export.root / "current" / "flags.env").unlink()
    base = tmp_path / "prod-copy"
    res = _run(tmp_path, export)
    assert res.returncode == 2, res.stderr
    assert "the pull from metorite failed" in res.stderr
    assert list(base.iterdir()) == [], "a failed pull left a folder behind"


@needs_bash
def test_the_pull_writes_into_a_dot_partial_folder(tmp_path: Path) -> None:
    """While the data comes in, no folder has a copy's name. A killed run
    leaves a .partial folder, and LD-8b ignores a name that starts with "."."""
    export = _make_export(tmp_path)
    assert _run(tmp_path, export).returncode == 0
    during = (tmp_path / "ssh.log.during").read_text(encoding="utf-8").split()
    assert during and all(n.startswith(".") for n in during), during
    assert any(n.startswith(f".partial-{_stamp(export.mtime)}.") for n in during), during


@needs_bash
def test_a_copy_changed_after_the_pull_is_refused(tmp_path: Path) -> None:
    export = _make_export(tmp_path)
    assert _run(tmp_path, export).returncode == 0
    copy = tmp_path / "prod-copy" / _stamp(export.mtime)
    (copy / "extensions.txt").write_bytes(b"changed by hand\n")
    res = _run(tmp_path, export)
    assert res.returncode == 1
    assert "already here, and it fails its checks" in res.stderr


@needs_bash
def test_a_held_lock_stops_a_second_run_and_stays(tmp_path: Path) -> None:
    base = tmp_path / "prod-copy"
    (base / ".lock").mkdir(parents=True)
    res = _run(tmp_path, _make_export(tmp_path))
    assert res.returncode == 2
    assert "another pull holds" in res.stderr
    assert (base / ".lock").is_dir(), "it removed the lock of another run"
    assert _calls(tmp_path) == []


# ── Retention ────────────────────────────────────────────────────────────────


@needs_bash
def test_it_keeps_the_two_newest_copies(tmp_path: Path) -> None:
    export = _make_export(tmp_path)
    base = tmp_path / "prod-copy"
    old = [_stamp(export.mtime - days * 86400) for days in (3, 2, 1)]
    for name in old:
        (base / name).mkdir(parents=True)
        (base / name / "staging.dump").write_bytes(b"old\n")
    (base / "notes").mkdir()                       # not a copy: it stays
    (base / ".partial-20261019T000000Z.1").mkdir()  # left by a killed run
    res = _run(tmp_path, export)
    assert res.returncode == 0, res.stderr
    assert _copies(base) == [old[2], _stamp(export.mtime)]
    assert (base / "notes").is_dir()
    assert not (base / ".partial-20261019T000000Z.1").exists()
    for name in old[:2]:
        assert f"removed the older copy {name}" in res.stdout


# ── Where the copy may live ──────────────────────────────────────────────────


@needs_bash
@needs_git
@pytest.mark.parametrize("where", ["new-folder", "existing-folder", "broken-git-file"])
def test_a_target_inside_a_git_work_tree_is_refused(tmp_path: Path, where: str) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    if where == "broken-git-file":
        # A worktree's .git file whose folder is gone: git says "not a
        # repository", so only the walk for a .git entry catches it.
        (repo / ".git").write_text("gitdir: /nowhere/at/all\n", encoding="utf-8")
    else:
        subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
    base = repo / "sub" / "prod-copy"
    if where == "existing-folder":
        base.mkdir(parents=True)
    res = _run(tmp_path, _make_export(tmp_path), base=base)
    assert res.returncode == 1, res.stderr
    assert "inside a git work tree" in res.stderr
    assert _calls(tmp_path) == [], "it reached the box before the git check"
    assert _copies(base) == []
    if where != "existing-folder":
        assert not (repo / "sub").exists()


def test_the_folders_are_limited_before_any_data_lands() -> None:
    code = _code(BODY)
    assert code.index("umask 077") < code.index('mkdir -p "$BASE"')
    assert code.index('lock_dir "$BASE"') < code.index('mkdir "$LOCK"')
    assert code.index('lock_dir "$partial"') < code.index('tar -C "$partial" -xf')
    assert code.index("check_copy \"$partial\"") < code.index('mv -T "$partial" "$final"')


def _acl(path: Path) -> list[str]:
    """The entries that icacls lists for one path, one a line."""
    icacls = Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32" / "icacls.exe"
    out = subprocess.run([str(icacls), str(path)], capture_output=True, text=True,
                         encoding="utf-8", errors="replace", check=True).stdout
    entries = []
    for i, line in enumerate(out.splitlines()):
        if not line.strip():
            break
        entries.append((line[len(str(path)):] if i == 0 else line).strip())
    return entries


def _me() -> str:
    whoami = Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32" / "whoami.exe"
    out = subprocess.run([str(whoami), "/user", "/fo", "csv", "/nh"], capture_output=True,
                         text=True, encoding="utf-8", errors="replace", check=True).stdout
    return out.split(",")[0].strip().strip('"').lower()


@needs_bash
@windows_only
def test_the_windows_acl_is_one_principal_with_no_inheritance(tmp_path: Path) -> None:
    """NTFS ignores chmod. The folders must name the current user alone, with
    no entry inherited from the profile or the temp folder."""
    export = _make_export(tmp_path)
    assert _run(tmp_path, export).returncode == 0
    base = tmp_path / "prod-copy"
    copy = base / _stamp(export.mtime)
    me = _me()
    for folder in (base, copy):
        (entry,) = _acl(folder)
        assert "(I)" not in entry, f"{folder} keeps an inherited entry: {entry}"
        assert entry.split(":(")[0].lower() == me, entry
        assert "(F)" in entry
    (entry,) = _acl(copy / "staging.dump")
    assert entry.split(":(")[0].lower() == me, entry


@needs_bash
@posix_only
def test_the_posix_mode_is_700_and_the_files_are_private(tmp_path: Path) -> None:
    export = _make_export(tmp_path)
    assert _run(tmp_path, export).returncode == 0
    base = tmp_path / "prod-copy"
    copy = base / _stamp(export.mtime)
    assert base.stat().st_mode & 0o777 == 0o700
    assert copy.stat().st_mode & 0o777 == 0o700
    for f in copy.iterdir():
        assert f.stat().st_mode & 0o077 == 0, f"{f.name} can be read by others"


# ── Nothing it prints holds a file's content ─────────────────────────────────


@needs_bash
def test_no_file_content_is_printed(tmp_path: Path) -> None:
    """A pull that works, one with a bad sum, and a stale one."""
    bad = _make_export(tmp_path / "bad")
    (bad.folder / "flags.env").write_bytes(b"root:MARKER_CHANGED_ENABLED=true\n")
    outputs = [
        _run(tmp_path / "good", _make_export(tmp_path / "good")),
        _run(tmp_path / "bad", bad),
        _run(tmp_path / "stale", _make_export(tmp_path / "stale", hours=50)),
    ]
    assert [r.returncode for r in outputs] == [0, 1, 1]
    assert "the checksum of flags.env does not match" in outputs[1].stderr
    text = "".join(r.stdout + r.stderr for r in outputs)
    assert "MARKER" not in text, text
    assert "second line of" not in text
