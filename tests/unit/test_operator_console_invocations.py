"""The Operator Console's verb list must equal the Console's own (CP-13b).

🔴 **Why this fence exists.** ``workbench/operator_console/src/lib/invocation.ts``
mirrors three constants from ``customer_console/catalog.py``:
``KNOWN_INVOCATIONS``, ``NATIVE_INVOCATION_PREFIX`` and ``NATIVE_TASKS``. The
declare form offers what the mirror holds. A verb the Console adds and the
mirror misses cannot be declared from the page. A verb the mirror keeps after
the Console drops it answers 400 on submit. Either way the two drift in
silence unless a test compares them.

⚠️ **A SOURCE test, with no database.** It reads the TypeScript as text, the
same way ``test_operator_console_vendor_slugs.py`` reads the vendor registry.
A JSON file both sides import would be a third copy.

⚠️ **The parse must fail loudly when it finds nothing.** Each reader asserts
it matched, so a changed file shape reds the test instead of blinding it.
"""
from __future__ import annotations

import re
from pathlib import Path

from customer_console import catalog

_TS = (
    Path(__file__).resolve().parents[2]
    / "workbench"
    / "operator_console"
    / "src"
    / "lib"
    / "invocation.ts"
)


def _source() -> str:
    return _TS.read_text(encoding="utf-8")


def _string_array(name: str) -> set[str]:
    """The string members of ``export const <name> ... = [ ... ]``."""
    match = re.search(
        rf"export const {name}\b[^=]*=\s*\[(?P<body>[^\]]*)\]", _source()
    )
    assert match, f"could not find the {name} array in {_TS.name}"
    found = set(re.findall(r'"([^"]+)"', match.group("body")))
    assert found, f"{name} parsed as empty from {_TS.name}"
    return found


def test_verbs_equal_known_invocations() -> None:
    assert _string_array("VERBS") == set(catalog.KNOWN_INVOCATIONS)


def test_native_prefix_equals_the_consoles() -> None:
    match = re.search(r'export const NATIVE_PREFIX\s*=\s*"([^"]*)"', _source())
    assert match, f"could not find NATIVE_PREFIX in {_TS.name}"
    assert match.group(1) == catalog.NATIVE_INVOCATION_PREFIX


def test_native_tasks_equal_the_consoles() -> None:
    assert _string_array("NATIVE_TASKS") == set(catalog.NATIVE_TASKS)
