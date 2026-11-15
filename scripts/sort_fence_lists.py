#!/usr/bin/env python3
"""Re-sort the two files that every flag or R8 PR appends to (CIV-HS, task 3284).

    python scripts/sort_fence_lists.py          # sort tests/fences.json and docs/flag_reload.md in place
    python scripts/sort_fence_lists.py --check  # print what is out of order, exit 1; change nothing

After a merge conflict in `tests/fences.json` or `docs/flag_reload.md`, take main's side of
the file, add your entry, and run this script. It is the one rule for the order:

* `tests/fences.json`: each list of test paths (`python`, `ts`, `r8.must_run`, `r8.never_skip`)
  is sorted and unique, one entry a line. The text outside the lists stays as it is.
* `docs/flag_reload.md`: each flag paragraph of "The flags that reload" is sorted by the flag
  name that starts it. A paragraph that does not start with a flag name stays with the flag
  before it.

Fences: tests/unit/test_fence_lists.py (`test_the_lists_are_sorted_unique_and_one_entry_a_line`)
and tests/unit/test_flag_doc_sorted.py. Tests of this script: tests/unit/test_sort_fence_lists.py.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FENCES = ROOT / "tests" / "fences.json"
FLAG_DOC = ROOT / "docs" / "flag_reload.md"


def sort_fences_text(text: str) -> str:
    """Sort each list of test paths in the text of tests/fences.json."""
    json.loads(text)  # refuse a file that is not JSON
    out = text
    # The tier lists of both languages and the two r8 lists are arrays of strings, one a line.
    for m in list(re.finditer(r'"(safety|process|must_run|never_skip)": \[\n((?:[ \t]+"[^"\n]+",?\n)+)([ \t]*\])', out))[::-1]:
        lines = [ln.strip().rstrip(",") for ln in m.group(2).splitlines()]
        indent = re.match(r"[ \t]*", m.group(2)).group(0)
        body = ",\n".join(indent + ln for ln in sorted(set(lines))) + "\n"
        out = out[: m.start(2)] + body + out[m.end(2):]
    json.loads(out)  # the result must still be JSON
    return out


def sort_flag_doc_text(text: str) -> str:
    start = text.index("## The flags that reload")
    end = text.index("A reload does NOT apply any other key")
    blocks = text[start:end].split("\n\n")
    head, rest = blocks[:2], [b for b in blocks[2:] if b.strip()]
    groups: list[list[str]] = []
    for b in rest:
        if re.match(r"`[A-Z][A-Z0-9_]+`", b) or not groups:
            groups.append([b])
        else:
            groups[-1].append(b)
    groups.sort(key=lambda g: re.match(r"`([A-Z0-9_]+)`", g[0]).group(1) if re.match(r"`([A-Z0-9_]+)`", g[0]) else g[0])
    body = "\n\n".join(head + [b for g in groups for b in g])
    return text[:start] + body + "\n\n" + text[end:]


def main(argv: list[str]) -> int:
    check = "--check" in argv
    bad = []
    for path, fn in ((FENCES, sort_fences_text), (FLAG_DOC, sort_flag_doc_text)):
        old = path.read_text(encoding="utf-8")
        new = fn(old)
        if new != old:
            bad.append(path.relative_to(ROOT).as_posix())
            if not check:
                path.write_text(new, encoding="utf-8", newline="\n")
    if bad:
        print(("out of order: " if check else "sorted: ") + ", ".join(bad))
    return 1 if (check and bad) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
