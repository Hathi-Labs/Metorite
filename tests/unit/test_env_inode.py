"""WS-49 BH-2 fix round 1, P2-1 — a writer of the box `.env` keeps its inode.

Spec: project-docs/specs/box_hardening.md §5 BH-2 item 4 and the risks.

The gateway sandbox (`50-hardening.conf`) lists `/opt/acb/app/.env` on
`ReadWritePaths`. systemd bind-mounts the INODE that `.env` has when the
gateway starts. A writer that replaces the file by rename (`sed -i`, a temp
file and `mv`) leaves the running gateway on the old, unlinked copy. The
gateway's own saves then go to that dead copy, and its next restart drops
them. So every writer builds the new content first and writes it INTO the
same inode.

- The static scan reads every shell file under `scripts/` and `deploy/`.
- The behaviour tests run `env_edit_in_place` from the `env helpers` block of
  `vps_apply.sh`, and compare the inode before and after.
- `tests/unit/test_secrets_drop.py` checks the third writer, `secrets.sh
  push app-env`, the same way.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
APPLY = ROOT / "scripts" / "vps_apply.sh"
SECRETS = ROOT / "scripts" / "secrets.sh"

#: The scripts that write the box .env. Any `sed -i` in one of them fails,
#: because a `sed -i` on a variable (`"$f"`) hides its target from a scan.
BOX_ENV_WRITERS = {
    "scripts/vps_apply.sh",
    "scripts/setup_secrets.sh",
    "deploy/hostinger/deploy.sh",
    "deploy/hostinger/bootstrap.sh",
}

#: ⚠️ KNOWN AND BLOCKED, not allowed. Two lines force the Graphiti flag to
#: false with `sed -i`. plan-guard's content arm refuses any written text
#: that sets that flag name to true, and no grant unlocks that arm. Their sed
#: expression holds that text, so an agent cannot edit them. The branch runs
#: only when someone sets the flag to true, because every deploy forces it to
#: false. The owner, or a guard-write repair of the false positive, closes
#: them. Each entry is (file, line prefix). This set may only shrink.
KNOWN_BLOCKED = {
    ("scripts/vps_apply.sh", "sed -i 's/^GRAPHITI_ENABLED"),
    ("deploy/hostinger/deploy.sh", "sed -i 's/^GRAPHITI_ENABLED"),
}

_SED_I = re.compile(r"\bsed\s+(-[A-Za-z]*\s+)*-[A-Za-z]*i")
_NAMES_ENV = re.compile(r"\.env\b(?!\.)|ENV_FILE")
_MV_ONTO_ENV = re.compile(r"\bmv\b.*\s(\"?)[^\s\"]*(\.env|ENV_FILE)\1\s*$")


def _shell_files() -> list[Path]:
    out = {*ROOT.glob("scripts/**/*.sh"), *ROOT.glob("deploy/**/*.sh")}
    return sorted(p for p in out if "node_modules" not in p.parts)


def _code(p: Path) -> list[str]:
    return [ln.strip() for ln in p.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.strip().startswith("#")]


def _violations() -> set[tuple[str, str]]:
    bad: set[tuple[str, str]] = set()
    for p in _shell_files():
        rel = p.relative_to(ROOT).as_posix()
        for ln in _code(p):
            sed_i = _SED_I.search(ln) and (rel in BOX_ENV_WRITERS or _NAMES_ENV.search(ln))
            if sed_i or _MV_ONTO_ENV.search(ln):
                bad.add((rel, ln))
    return bad


def _blocked(item: tuple[str, str]) -> bool:
    return any(item[0] == f and item[1].startswith(pre) for f, pre in KNOWN_BLOCKED)


def test_no_writer_replaces_the_inode_of_env() -> None:
    new = sorted(v for v in _violations() if not _blocked(v))
    assert not new, (
        "a writer replaces the inode of .env. Write INTO the file "
        "(env_edit_in_place in vps_apply.sh):\n" + "\n".join(f"{f}: {ln}" for f, ln in new)
    )


def test_the_known_blocked_set_only_shrinks() -> None:
    """Each entry matches exactly one line now. When one is fixed, delete it."""
    found = _violations()
    for f, pre in KNOWN_BLOCKED:
        hits = [v for v in found if v[0] == f and v[1].startswith(pre)]
        assert len(hits) == 1, (f, pre, hits)


def test_the_scan_sees_each_shape() -> None:
    """The scan must catch the shapes that fix round 1 removed."""
    for ln in ('sed -i "s|^$1=.*|$1=$2|" "$ENV_FILE"',
               "grep -vE '^X=' \"$ENV_FILE\" > \"$ENV_FILE.wb.tmp\" && mv \"$ENV_FILE.wb.tmp\" \"$ENV_FILE\"",
               "sed -i -E \"/x/d\" .env",
               "mv -f -T \"$tmp\" /opt/acb/app/.env"):
        assert (_SED_I.search(ln) and _NAMES_ENV.search(ln)) or _MV_ONTO_ENV.search(ln), ln
    assert not _MV_ONTO_ENV.search('mv "$WB_ENV.tmp" "$WB_ENV"')
    assert not _NAMES_ENV.search("cp .env.example x")


def test_secrets_merge_writes_into_the_file() -> None:
    """secrets.sh renames a whole-file push, which no unit binds. A merge,
    the app env, goes through r_put, which writes with O_NOFOLLOW."""
    body = SECRETS.read_text(encoding="utf-8")

    def fn(name: str) -> str:
        start = body.index(f"{name}() {{")
        return body[start: body.index("\n}\n", start)]

    assert 'dd if="$src" of="$path" oflag=nofollow' in fn("r_put")
    assert 'if [ "$how" = merge ]; then\n    r_put "$r_tmp" "$path"' in fn("r_write")
    assert 'r_put "$tmp" "$path"' in fn("r_rollback")


# ── env_edit_in_place keeps the inode ────────────────────────────────────


def _bash() -> str | None:
    b = shutil.which("bash")
    if not b or "system32" in b.lower():
        return None
    return b


needs_bash = pytest.mark.skipif(_bash() is None, reason="needs a POSIX bash")


def _helpers(tmp: Path) -> Path:
    lines = APPLY.read_text(encoding="utf-8").splitlines()
    a, b = lines.index("# >>> env helpers"), lines.index("# <<< env helpers")
    out = tmp / "env_helpers.sh"
    out.write_text("\n".join(lines[a:b + 1]) + "\n", encoding="utf-8", newline="\n")
    return out


def _edit(tmp: Path, env: Path, cmd: str) -> subprocess.CompletedProcess:
    script = f'set -e; source "{_helpers(tmp).as_posix()}"; env_edit_in_place "{env.as_posix()}" {cmd}'
    return subprocess.run([_bash(), "-c", script], capture_output=True, text=True,
                          encoding="utf-8", timeout=60, stdin=subprocess.DEVNULL)


@needs_bash
@pytest.mark.parametrize(("cmd", "after"), [
    ("sed 's/^B=.*/B=new/'", "A=1\nB=new\nC=3\n"),
    ("grep -vE '^B='", "A=1\nC=3\n"),
    ("grep -vE '^(A|B|C)='", ""),
])
def test_an_edit_writes_into_the_same_inode(tmp_path: Path, cmd: str, after: str) -> None:
    env = tmp_path / "dot.env"
    env.write_text("A=1\nB=old\nC=3\n", encoding="utf-8", newline="\n")
    ino = os.stat(env).st_ino
    r = _edit(tmp_path, env, cmd)
    assert r.returncode == 0, r.stdout + r.stderr
    assert env.read_text(encoding="utf-8") == after
    assert os.stat(env).st_ino == ino, "the edit replaced the inode"
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith(".env-edit.")] == []


@needs_bash
def test_a_failed_edit_changes_nothing(tmp_path: Path) -> None:
    env = tmp_path / "dot.env"
    env.write_text("A=1\n", encoding="utf-8", newline="\n")
    r = _edit(tmp_path, env, "sed 's/unclosed/'")
    assert r.returncode != 0
    assert env.read_text(encoding="utf-8") == "A=1\n"
    assert "It is unchanged" in r.stdout
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith(".env-edit.")] == []
