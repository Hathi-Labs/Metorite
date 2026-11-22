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

An unmapped spec (no area, importer rule or `always_run` entry names it) joins
every partial selection too. A new spec can merge before its map entry, so the
map can lag main for a while. Then the spec runs on every UI PR, and no fence
on main goes red (2026-11-14: `studio-app-rail.spec.ts` turned main red).

Import graph (fix round 1). A changed `src/` file also selects the specs of every
file that imports it, directly or through other files. The graph reads relative
imports and the `@/` alias, with `.ts`, `.tsx` and `index` files. It skips test
files. An importer maps in this order: an `importer_rules` entry (first match),
an area, then a `fallback` rule. A shared or unnamed importer that other files
import adds nothing, because its own importers are in the closure. A shared or
unnamed importer that nothing imports is an entry point and selects every spec. A spec that imports a changed `src/` file selects
itself. A DIRECT change to a shared path still selects every spec.

Fence: tests/unit/test_e2e_select.py. Standard library only.
"""
from __future__ import annotations

import argparse
import fnmatch
import functools
import json
import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MAP_PATH = Path(__file__).resolve().parent / "e2e_spec_map.json"
SHARDS = 3


def load_map(path: Path = MAP_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]+$")
IMPORT_RE = re.compile(r"""(?:from\s*|import\s*\(?\s*|require\(\s*)["']([^"']+)["']""")
EXTS = ("", ".ts", ".tsx", ".js", ".jsx", "/index.ts", "/index.tsx")


def list_specs(e2e_dir: Path) -> list[str]:
    names = sorted(p.name for p in e2e_dir.glob("*.spec.ts"))
    for n in names:
        # A name goes into a CI output file. An odd name must fail the step, not inject a line.
        if not SAFE_NAME.match(n):
            raise ValueError(f"unsafe spec file name: {n!r}")
    return names


@functools.lru_cache(maxsize=4)
def _graph(root_text: str) -> dict[str, frozenset[str]]:
    return {k: frozenset(v) for k, v in _build_graph(Path(root_text)).items()}


def src_graph(cp: Path) -> dict[str, frozenset[str]]:
    return _graph(str(cp.resolve()))


def _build_graph(cp: Path) -> dict[str, set[str]]:
    """Map each src/ file (path under workbench/control_plane) to the files that import it.

    Importers are src/ files and e2e/ files. Test files and fixture files are not importers.
    """
    root = cp.resolve()
    rev: dict[str, set[str]] = {}
    files = [f for d in ("src", "e2e") for f in (root / d).rglob("*") if f.suffix in (".ts", ".tsx", ".js", ".jsx") and f.is_file()]
    for f in files:
        name = f.name
        if ".test." in name or ".fixture." in name:
            continue
        rel_f = f.relative_to(root).as_posix()
        try:
            text = f.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for m in IMPORT_RE.finditer(text):
            spec = m.group(1)
            if spec.startswith("@/"):
                base = root / "src" / spec[2:]
            elif spec.startswith("."):
                base = f.parent / spec
            else:
                continue
            for ext in EXTS:
                cand = Path(os.path.normpath(str(base) + ext))
                if cand.is_file():
                    try:
                        rel_t = cand.resolve().relative_to(root).as_posix()
                    except ValueError:
                        break
                    if rel_t != rel_f:
                        rev.setdefault(rel_t, set()).add(rel_f)
                    break
    return rev


def importer_closure(rel: str, rev: dict[str, set[str]]) -> set[str]:
    seen: set[str] = set()
    todo = [rel]
    while todo:
        for i in rev.get(todo.pop(), ()):
            if i not in seen:
                seen.add(i)
                todo.append(i)
    return seen


def _globs(specs: list[str], globs: list[str]) -> set[str]:
    return {s for s in specs if any(fnmatch.fnmatch(s, g) for g in globs)}


def area_specs(rel: str, spec_map: dict, specs: list[str]) -> set[str] | None:
    """The specs of the areas that name `rel`, or None when no area names it."""
    hit = False
    out: set[str] = set()
    for area in spec_map["areas"].values():
        if any(rel.startswith(x) for x in area["paths"]):
            hit = True
            out |= _globs(specs, area["specs"])
    return out if hit else None


def importer_specs(rel: str, spec_map: dict, specs: list[str]) -> set[str] | None:
    """The specs for a file that is reached as an IMPORTER of a changed file.

    None means every spec. A shared file does not select every spec here: an
    `importer_rules` entry gives it its own specs.
    """
    for rule in spec_map["importer_rules"]:
        if not rule.get("fallback") and any(rel.startswith(x) for x in rule["paths"]):
            out = _globs(specs, rule.get("specs", []))
            for a in rule.get("areas", []):
                out |= _globs(specs, spec_map["areas"][a]["specs"])
            return out
    if any(rel.startswith(x) for x in spec_map["all"]):
        return None
    own = area_specs(rel, spec_map, specs)
    if own is not None:
        return own
    for rule in spec_map["importer_rules"]:
        if rule.get("fallback") and any(rel.startswith(x) for x in rule["paths"]):
            return _globs(specs, rule.get("specs", []))
    return None


def quarantined(config_text: str) -> set[str]:
    """Spec names in the `ci` project's testIgnore (the same read as e2eGate.test.ts)."""
    start = config_text.find('name: "ci"')
    if start < 0:
        return set()
    block = config_text[start:config_text.find("use: {", start)]
    return set(re.findall(r'"\*\*/([^"]+\.spec\.ts)"', block))


def importers(e2e_dir: Path) -> dict[str, set[str]]:
    return _e2e_importers(str(e2e_dir.resolve()))


@functools.lru_cache(maxsize=4)
def _e2e_importers(e2e_text: str) -> dict[str, set[str]]:
    """Map each e2e file name to the file names that import it (one hop)."""
    e2e_dir = Path(e2e_text)
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


def _importer_specs_of(
    rel: str, rev: dict[str, frozenset[str]], spec_map: dict, specs: list[str], memo: dict
) -> set[str] | None:
    """The specs of every importer of `rel`. None means every spec."""
    out: set[str] = set()
    for imp in importer_closure(rel, rev):
        if imp.startswith("e2e/"):
            name = imp[len("e2e/"):]
            if name.endswith(".spec.ts") and "/" not in name:
                out.add(name)
            continue
        if imp not in memo:
            memo[imp] = importer_specs(imp, spec_map, specs)
        got = memo[imp]
        if got is None:
            # A shared or unnamed file that other files import adds nothing of its
            # own: its importers are in this closure and map themselves. An unnamed
            # file that nothing imports is an entry point, and it selects every spec.
            if rev.get(imp):
                continue
            return None
        out |= got
    return out


def select(
    paths: list[str], spec_map: dict, e2e_dir: Path, config_text: str = "", memo: dict | None = None
) -> tuple[str, list[str]]:
    """Return (mode, specs). Mode is `all`, `none` or `partial`.

    `memo` caches the specs of an importer file across calls (a test passes one).
    """
    memo = {} if memo is None else memo
    root = spec_map["root"]
    specs = list_specs(e2e_dir)
    graph = importers(e2e_dir)
    rev = src_graph(e2e_dir.parent)
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
        own = area_specs(rel, spec_map, specs)
        if own is None:
            return "all", specs
        chosen |= own
        more = _importer_specs_of(rel, rev, spec_map, specs, memo)
        if more is None:
            return "all", specs
        chosen |= more
    chosen &= set(specs)
    chosen -= quarantined(config_text)
    if not chosen:
        return "none", []
    chosen |= set(spec_map["always_run"]) & set(specs)
    chosen |= unmapped_specs(spec_map, specs) - quarantined(config_text)
    return "partial", sorted(chosen)


def unmapped_specs(spec_map: dict, specs: list[str]) -> set[str]:
    """The specs that no area, importer rule or `always_run` entry names."""
    globs = list(spec_map["always_run"])
    for area in spec_map["areas"].values():
        globs += area["specs"]
    for rule in spec_map.get("importer_rules", []):
        globs += rule.get("specs", [])
    return {s for s in specs if not any(fnmatch.fnmatch(s, g) for g in globs)}


def shard_plan(count: int) -> tuple[int, list[int]]:
    total = max(1, min(SHARDS, count))
    return total, list(range(1, total + 1))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--files-from", required=True, help="a file with one changed path per line")
    ap.add_argument("--map", default=str(MAP_PATH))
    args = ap.parse_args(argv)
    paths = [p for p in Path(args.files_from).read_bytes().decode("utf-8").split("\n") if p]
    # git quotes a path with a newline or an odd byte. Fail loud: the step then runs every spec.
    bad = [p for p in paths if p.startswith('"') or any(ord(c) < 32 for c in p)]
    if bad:
        print(f"error: unsafe changed path: {bad[0]!r}", file=sys.stderr)
        return 2
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
