"""Read the static fence lists, for the two-speed CI (owner directive, 2026-10-19).

A static fence is a test that reads repo files as TEXT (source, SQL, docs,
registries) and checks a rule. It takes no database and runs in about a
second. ``tests/fences.json`` sorts each one into a tier:

* ``safety``  — guards production: tenancy, auth, secrets, migrations, the
  deploy and CI path, money. Blocking, always.
* ``process`` — guards consistency: naming, docs, registries, the design
  system, source-string pins. ADVISORY while a feature is in build. A PR
  labelled ``harden`` (the hardening pass) makes it blocking.

Usage::

    uv run python scripts/fences.py run safety      # run one tier, no -x
    python scripts/fences.py list python safety     # one path a line
    python scripts/fences.py pytest-ignore          # --ignore= for each process fence
    python scripts/fences.py vitest control_plane process   # paths relative to the app
    python scripts/fences.py vitest-exclude control_plane   # --exclude= for each process fence
    python scripts/fences.py check                  # every listed file exists

Standard library only, so CI can run it before ``uv sync``.
Fence: tests/unit/test_fence_lists.py.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LISTS = ROOT / "tests" / "fences.json"
TIERS = ("safety", "process")


def load() -> dict[str, dict[str, list[str]]]:
    return json.loads(LISTS.read_text(encoding="utf-8"))


def _app_paths(data: dict, app: str, tier: str) -> list[str]:
    prefix = f"workbench/{app}/"
    return [p[len(prefix):] for p in data["ts"][tier] if p.startswith(prefix)]


def problems(data: dict) -> list[str]:
    out: list[str] = []
    for lang in ("python", "ts"):
        seen: set[str] = set()
        for tier in TIERS:
            for p in data[lang][tier]:
                if p in seen:
                    out.append(f"{p} is listed twice")
                seen.add(p)
                if not (ROOT / p).is_file():
                    out.append(f"{p} does not exist")
    return out


def main(argv: list[str]) -> int:
    data = load()
    cmd, *rest = argv or ["help"]
    if cmd == "list":
        lang, tier = rest
        print("\n".join(data[lang][tier]))
    elif cmd == "run":
        # Run one tier's python fences, all of them, with no `-x`. An empty
        # list must run NOTHING: a bare `pytest` would run the whole suite.
        (tier,) = rest
        paths = data["python"][tier]
        if not paths:
            print(f"no {tier} fences listed")
            return 0
        return subprocess.call([sys.executable, "-m", "pytest", *paths, "-q", "-p", "no:cacheprovider"])
    elif cmd == "pytest-ignore":
        print(" ".join(f"--ignore={p}" for p in data["python"]["process"]))
    elif cmd == "vitest":
        app, tier = rest
        print(" ".join(_app_paths(data, app, tier)))
    elif cmd == "vitest-exclude":
        (app,) = rest
        print(" ".join(f"--exclude={p}" for p in _app_paths(data, app, "process")))
    elif cmd == "check":
        found = problems(data)
        for line in found:
            print(line, file=sys.stderr)
        return 1 if found else 0
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
