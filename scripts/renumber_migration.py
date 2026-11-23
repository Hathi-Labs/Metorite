#!/usr/bin/env python3
"""Renumber one migration file and every reference to its file name (task 3928).

Use it when R1 (premerge.sh) finds that the number you booked sits at or below
main's top. Run it through ``scripts/renumber_migration.sh``.

    scripts/renumber_migration.sh <old_number> [new_number] [--dir infra/postgres]
                                  [--repo <path>] [--no-test]

* It finds ``<dir>/<old>_<name>.sql`` and ``git mv``s it to ``<dir>/<new>_<name>.sql``.
* It rewrites each tracked text file that names the old file name (specs, tests,
  comments, ``tests/fences.json``). It rewrites the whole file name only, so a
  bare number in prose is left alone. It lists those lines for you to check.
* It runs ``tests/unit/test_migration_prefixes.py``, unless ``--no-test``.
* With no new number it books one through the box ledger:
  ``LANE=<lane> TASK=<task> scripts/renumber_migration.sh <old>``. It runs
  ``ssh metorite-home '~/lanes/reserve_migration.sh <lane> <task>'``.
  ``RENUMBER_BOOK_CMD`` replaces that command (the test uses it).

The width of the new number follows the old one (``097`` stays three digits).
Stdlib only. Fence: tests/unit/test_renumber_migration.py.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path


class RenumberError(Exception):
    """A reason to stop that the person running the script can act on."""


def _git(repo: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, check=False
    )
    if out.returncode != 0:
        raise RenumberError(f"git {' '.join(args)} failed: {out.stderr.decode('utf-8', 'replace').strip()}")
    return out.stdout.decode("utf-8", "replace")


def find_migration(repo: Path, folder: str, number: int) -> Path:
    """The one tracked file whose numeric prefix equals ``number``."""
    hits = []
    for rel in _git(repo, "ls-files", "-z", "--", folder).split("\0"):
        m = re.match(r"^(\d+)_.*\.sql$", Path(rel).name) if rel else None
        if m and int(m.group(1)) == number:
            hits.append(rel)
    if not hits:
        raise RenumberError(f"no tracked migration {number} in {folder}")
    if len(hits) > 1:
        raise RenumberError(f"migration {number} is ambiguous in {folder}: {hits}")
    return Path(hits[0])


def book_number() -> int:
    """Book the next number through the box ledger, or the test's replacement."""
    cmd = os.environ.get("RENUMBER_BOOK_CMD")
    if not cmd:
        lane, task = os.environ.get("LANE"), os.environ.get("TASK")
        if not lane or not task:
            raise RenumberError(
                "no new number. Give one, or set LANE and TASK to book one "
                "(~/lanes/reserve_migration.sh <lane> <task>)."
            )
        if not (re.fullmatch(r"[A-Za-z0-9_.-]+", lane) and re.fullmatch(r"[A-Za-z0-9_.-]+", task)):
            raise RenumberError("LANE and TASK may hold letters, digits, dot, dash and underscore only")
        cmd = f"ssh metorite-home '~/lanes/reserve_migration.sh {lane} {task}'"
    out = subprocess.run(cmd, shell=True, capture_output=True, check=False)
    text = out.stdout.decode("utf-8", "replace").strip().splitlines()
    if out.returncode != 0 or not text or not text[-1].strip().isdigit():
        raise RenumberError(f"booking failed: {out.stderr.decode('utf-8', 'replace').strip() or text}")
    return int(text[-1].strip())


def renumber(repo: Path, folder: str, old: int, new: int | None) -> tuple[Path, Path, list[str], list[str]]:
    """Move the file and rewrite its references. Returns (old, new, changed, review)."""
    src = find_migration(repo, folder, old)
    width = len(re.match(r"^(\d+)_", src.name).group(1))  # type: ignore[union-attr]
    if new is None:
        new = book_number()
    if new == old:
        raise RenumberError("the new number equals the old one")
    new_name = f"{str(new).zfill(width)}_{src.name.split('_', 1)[1]}"
    dst = src.with_name(new_name)
    clash = [
        rel for rel in _git(repo, "ls-files", "-z", "--", folder).split("\0")
        if rel and re.match(rf"^0*{new}_.*\.sql$", Path(rel).name)
    ]
    if clash:
        raise RenumberError(f"number {new} is taken already: {clash}")

    _git(repo, "mv", str(src), str(dst))
    changed: list[str] = []
    review: list[str] = []
    old_name = src.name
    # The old name only counts as a whole name: "48_x.sql" must not match inside
    # "148_x.sql", which is another migration (a finding of the verifier).
    name_re = re.compile(rb"(?<![0-9A-Za-z_])" + re.escape(old_name.encode("utf-8")))
    old_num =re.compile(rf"(?i)\bmigration\s+0*{old}\b")
    for rel in _git(repo, "ls-files", "-z").split("\0"):
        if not rel:
            continue
        path = repo / rel
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if b"\0" in data:  # a binary file
            continue
        text = data.decode("utf-8", "replace")
        if name_re.search(data):
            nl_data = name_re.sub(new_name.encode("utf-8"), data)
            path.write_bytes(nl_data)
            changed.append(rel)
            text = nl_data.decode("utf-8", "replace")
        for i, line in enumerate(text.splitlines(), 1):
            if old_num.search(line):
                review.append(f"{rel}:{i}: {line.strip()[:120]}")
    return src, dst, changed, review


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("old", type=int, help="the migration number to move")
    ap.add_argument("new", type=int, nargs="?", help="the new number (books one when absent)")
    ap.add_argument("--dir", default="infra/postgres", help="the migration folder")
    ap.add_argument("--repo", default=str(Path(__file__).resolve().parents[1]))
    ap.add_argument("--no-test", action="store_true", help="skip test_migration_prefixes.py")
    args = ap.parse_args(argv)
    repo = Path(args.repo)
    try:
        src, dst, changed, review = renumber(repo, args.dir, args.old, args.new)
    except RenumberError as err:
        print(f"renumber_migration: {err}", file=sys.stderr)
        return 2
    print(f"moved {src} -> {dst}")
    print(f"rewrote the file name in {len(changed)} file(s):")
    for rel in changed:
        print(f"  {rel}")
    if review:
        print(f"CHECK BY HAND: {len(review)} line(s) still say 'migration {args.old}':")
        for line in review:
            print(f"  {line}")
    if args.no_test:
        return 0
    test = repo / "tests" / "unit" / "test_migration_prefixes.py"
    if not test.is_file():
        print("no tests/unit/test_migration_prefixes.py here, so no test ran")
        return 0
    env = {**os.environ, "PYTEST_ADDOPTS": "-o timeout_method=thread"}
    done = subprocess.run(
        ["uv", "run", "pytest", "-p", "no:cacheprovider", str(test), "-q"], cwd=repo, env=env, check=False
    )
    return done.returncode


if __name__ == "__main__":
    raise SystemExit(main())
