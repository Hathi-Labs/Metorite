"""CIV-29d (D218): pick the browser specs that a diff can affect.

Reads `scripts/e2e_spec_map.json`. Input: the changed paths of a PR, one per
line. Output: the spec files to run, the shard list, and the shard total.

Rules, in this order, for each changed path under workbench/control_plane:
1. A `.test.ts`, `.test.tsx` or `.md` file outside `e2e/` selects nothing.
2. A path in the `all` list selects every spec.
3. An `e2e/` spec file selects itself. Another `e2e/` file selects the specs
   that import it, directly or through other helpers. No importer: every spec.
4. A path in an area selects that area's specs.
5. Any other path selects every spec.
A path outside workbench/control_plane selects nothing. The `always_run` specs
join every partial selection. Specs that the `ci` project ignores are dropped.

Fence: tests/unit/test_e2e_select.py. Standard library only.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MAP_PATH = Path(__file__).resolve().parent / "e2e_spec_map.json"
SHARDS = 3


def load_map(path: Path = MAP_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def list_specs(e2e_dir: Path) -> list[str]:
    return sorted(p.name for p in e2e_dir.glob("*.spec.ts"))


def quarantined(config_text: str) -> set[str]:
    """Spec names in the `ci` project's testIgnore (the same read as e2eGate.test.ts)."""
    start = config_text.find('name: "ci"')
    if start < 0:
        return set()
    block = config_text[start:config_text.find("use: {", start)]
    return set(re.findall(r'"\*\*/([^"]+\.spec\.ts)"', block))


def importers(e2e_dir: Path) -> dict[str, set[str]]:
    """Map each e2e file name to the file names that import it (one hop)."""
    files = [p for p in e2e_dir.rglob("*.ts")]
    out: dict[str, set[str]] = {}
    for f in files:
        text = f.read_text(encoding="utf-8")
        for m in re.finditer(r'from\s+"(\.{1,2}/[^"]+)"', text):
            target = (f.parent / m.group(1)).resolve()
            for cand in (target, target.with_suffix(".ts"), target / "index.ts"):
                if cand.is_file() and cand.is_relative_to(e2e_dir.resolve()):
                    rel = cand.relative_to(e2e_dir.resolve()).as_posix()
                    out.setdefault(rel, set()).add(f.relative_to(e2e_dir).as_posix())
                    break
    return out


def dependents(rel: str, graph: dict[str, set[str]]) -> set[str]:
    seen: set[str] = set()
    todo = [rel]
    while todo:
        for d in graph.get(todo.pop(), ()):
            if d not in seen:
                seen.add(d)
                todo.append(d)
    return seen


def select(paths: list[str], spec_map: dict, e2e_dir: Path, config_text: str = "") -> tuple[str, list[str]]:
    """Return (mode, specs). Mode is `all`, `none` or `partial`."""
    root = spec_map["root"]
    specs = list_specs(e2e_dir)
    graph = importers(e2e_dir)
    chosen: set[str] = set()
    for raw in paths:
        p = raw.strip().replace("\\", "/")
        if not p.startswith(root):
            continue
        rel = p[len(root):]
        if not rel.startswith("e2e/") and any(rel.endswith(x) for x in spec_map["ignore_suffixes"]):
            continue
        if any(rel.startswith(x) for x in spec_map["all"]):
            return "all", specs
        if rel.startswith("e2e/"):
            sub = rel[len("e2e/"):]
            if sub.endswith(".spec.ts") and "/" not in sub:
                chosen.add(sub)
                continue
            deps = {d for d in dependents(sub, graph) if d.endswith(".spec.ts") and "/" not in d}
            if not deps:
                return "all", specs
            chosen |= deps
            continue
        hit = False
        for area in spec_map["areas"].values():
            if any(rel.startswith(x) for x in area["paths"]):
                hit = True
                for glob in area["specs"]:
                    chosen |= {s for s in specs if fnmatch.fnmatch(s, glob)}
        if not hit:
            return "all", specs
    chosen &= set(specs)
    chosen -= quarantined(config_text)
    if not chosen:
        return "none", []
    chosen |= set(spec_map["always_run"]) & set(specs)
    return "partial", sorted(chosen)


def shard_plan(count: int) -> tuple[int, list[int]]:
    total = max(1, min(SHARDS, count))
    return total, list(range(1, total + 1))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--files-from", required=True, help="a file with one changed path per line")
    ap.add_argument("--map", default=str(MAP_PATH))
    args = ap.parse_args(argv)
    paths = Path(args.files_from).read_text(encoding="utf-8").splitlines()
    spec_map = load_map(Path(args.map))
    cp = REPO / spec_map["root"]
    config_text = (cp / "playwright.config.ts").read_text(encoding="utf-8")
    mode, specs = select(paths, spec_map, cp / "e2e", config_text)
    if mode == "all":
        total, shards, files = SHARDS, [1, 2, 3], ""
    elif mode == "none":
        total, shards, files = SHARDS, [], ""
    else:
        total, shards = shard_plan(len(specs))
        files = " ".join(f"e2e/{s}" for s in specs)
    print(f"e2e_mode={mode}")
    print(f"e2e_files={files}")
    print(f"e2e_shards={json.dumps(shards, separators=(',', ':'))}")
    print(f"e2e_total={total}")
    print(f"browser specs: {mode}, {len(specs) if mode == 'partial' else 'n/a'}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
