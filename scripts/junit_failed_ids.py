#!/usr/bin/env python3
"""CIV-5: print the failed pytest node ids of JUnit files, one per line.

Usage: python3 -I scripts/junit_failed_ids.py <file or dir>...

A directory adds each *.xml file below it. A test case with a <failure> or
<error> child is a failed test. A skipped test is not. The classname of the
xunit1 family is the dotted module path, and a class adds one more part.
A part that starts with an upper-case letter starts the class path.
The output is sorted and has no duplicate. A file that does not parse goes to
stderr, and the script goes on with the other files.
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def node_id(classname: str, name: str, file: str = "") -> str:
    # xunit1 carries `file`: the module that DEFINES the test. Use it when
    # present, because a class name that starts in lower case, or a module
    # directory in upper case, makes the classname guess wrong.
    if file:
        module = file.replace("\\", "/")
        stem = module[: -len(".py")].replace("/", ".") if module.endswith(".py") else ""
        rest = classname[len(stem) + 1 :] if stem and classname.startswith(stem + ".") else ""
        return "::".join(x for x in (module, *[c for c in rest.split(".") if c], name) if x)
    parts = [p for p in classname.split(".") if p]
    split = len(parts)
    for i, part in enumerate(parts):
        if part[:1].isupper():
            split = i
            break
    module = "/".join(parts[:split]) + ".py" if split else ""
    chain = "::".join(parts[split:])
    return "::".join(x for x in (module, chain, name) if x)


def failed_ids(path: Path) -> set[str]:
    ids: set[str] = set()
    root = ET.parse(path).getroot()
    for case in root.iter("testcase"):
        if any(child.tag in ("failure", "error") for child in case):
            ids.add(node_id(case.get("classname", ""), case.get("name", ""), case.get("file", "")))
    return ids


def main(argv: list[str]) -> int:
    files: list[Path] = []
    for arg in argv:
        p = Path(arg)
        files.extend(sorted(p.rglob("*.xml")) if p.is_dir() else [p])
    ids: set[str] = set()
    for f in files:
        try:
            ids |= failed_ids(f)
        except (OSError, ET.ParseError) as exc:
            print(f"{f}: cannot read ({exc})", file=sys.stderr)
    for one in sorted(ids):
        print(one)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
