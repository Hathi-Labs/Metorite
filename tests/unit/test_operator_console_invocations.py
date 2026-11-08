"""The Operator Console's verb list must equal the Console's own (CP-13b).

🔴 **Why this fence exists.** ``workbench/operator_console/src/lib/invocation.ts``
mirrors four constants from ``customer_console/catalog.py``:
``KNOWN_INVOCATIONS``, ``NATIVE_INVOCATION_PREFIX``, ``NATIVE_TASKS`` and
``NATIVE_ONLY_TASKS`` (VOX-2). It mirrors two maps of the handler table too:
the vendor and the task of each native verb. The
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


def test_native_provider_map_equals_the_handler_table() -> None:
    """CP-13h review P1-2. The declare form picks a native verb from the
    model prefix through ``NATIVE_PROVIDER``. The Console refuses a pair
    whose verb calls another vendor, and it reads the map from
    ``handlers.NATIVE_HANDLERS``. The two must agree, or the form offers a
    pair the Console refuses, or hides one it accepts."""
    from customer_console import handlers

    match = re.search(
        r"export const NATIVE_PROVIDER\b[^=]*=\s*\{(?P<body>[^}]*)\}", _source()
    )
    assert match, f"could not find the NATIVE_PROVIDER map in {_TS.name}"
    found = dict(re.findall(r'(\w+):\s*"([^"]+)"', match.group("body")))
    assert found, f"NATIVE_PROVIDER parsed as empty from {_TS.name}"
    expected = {
        verb: handlers.native_provider_of(verb) for verb in handlers.NATIVE_HANDLERS
    }
    assert found == expected


def test_native_only_tasks_equal_the_consoles() -> None:
    """VOX-2. ``transcribe`` joined ``NATIVE_TASKS`` and keeps its litellm
    verb, so the form reads a second set: the jobs that take ONLY a native
    verb. A copy that drifted would refuse ``atranscription`` in the form, or
    offer ``acompletion`` for ``decide``."""
    assert _string_array("NATIVE_ONLY_TASKS") == set(catalog.NATIVE_ONLY_TASKS)


def test_native_task_map_equals_the_handler_table() -> None:
    """VOX-2. Each native verb serves the ONE job its handler names. The
    form reads ``NATIVE_TASK_OF`` to offer ``native_sarvam`` for
    ``transcribe`` alone, and the Console reads ``handlers.native_task_of``."""
    from customer_console import handlers

    match = re.search(
        r"export const NATIVE_TASK_OF\b[^=]*=\s*\{(?P<body>[^}]*)\}", _source()
    )
    assert match, f"could not find the NATIVE_TASK_OF map in {_TS.name}"
    found = dict(re.findall(r'(\w+):\s*"([^"]+)"', match.group("body")))
    assert found, f"NATIVE_TASK_OF parsed as empty from {_TS.name}"
    expected = {verb: handlers.native_task_of(verb) for verb in handlers.NATIVE_HANDLERS}
    assert found == expected
