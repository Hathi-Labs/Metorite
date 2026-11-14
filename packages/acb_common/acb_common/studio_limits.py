"""Limits for each Studio version, and the fixed error codes (STU-L6a).

Spec: ``project-docs/specs/ai_studio.md`` section 19.10, rules 16 and 17.

The numbers are PROPOSED. They stay proposals until L6 measures them.
This module holds data only. Only the source size has an enforcement point
today: ``MAX_DOC_BYTES`` in ``studio_manifest.py`` and ``MAX_MANIFEST_BYTES``
in the gateway ``packages_store.py``. ``check_source_size`` has no live
caller yet. The other limits wait on L1, L7, SBX-9 and B5.

Two kinds hold limits. ``browser`` is a screen. ``server`` is server code.
A key that a kind does not list has no limit of its own for that kind.
The browser memory limit is the limit of the browser, so it has no key.

The TypeScript twins are ``codeErrors.ts`` and ``limits.ts`` in
``workbench/control_plane/src/lib/studio``. The golden file is
``tests/unit/golden/studio_code_errors.json``.
``tests/unit/test_studio_code_error_codes.py`` holds all of them equal.
"""

from __future__ import annotations

from types import MappingProxyType

from acb_common.studio_contract import CONTRACT_UNSUPPORTED

KIB = 1024
MIB = 1024 * KIB

LIMIT_EXCEEDED = "limit_exceeded"

#: Limits of one version, by kind. Proposed numbers, copied from the spec table.
LIMITS = MappingProxyType(
    {
        "browser": MappingProxyType(
            {
                "source_bytes": 256 * KIB,
                "source_files": 100,
                "compile_seconds": 10,
                "input_bytes": 1 * MIB,
                "output_bytes": 4 * MIB,
                "door_calls_per_second": 20,
            }
        ),
        "server": MappingProxyType(
            {
                "source_bytes": 256 * KIB,
                "source_files": 100,
                "build_seconds": 180,
                "memory_bytes": 256 * MIB,
                "call_seconds": 30,
                "input_bytes": 1 * MIB,
                "output_bytes": 4 * MIB,
                "writes_per_call": 5000,
                "queue_calls": 16,
                "warm_runtimes_per_org": 1,
                "idle_stop_seconds": 10 * 60,
                "packages_direct": 20,
                "packages_total": 150,
            }
        ),
    }
)

#: The fixed set of error codes. One sentence for each: what happened, and
#: what a person or a model does next. A sentence holds no path, no user
#: value and no number that may change.
ERROR_CODES = MappingProxyType(
    {
        "compile_failed": (
            "The screen did not compile, so open the console and fix the file and line it names."
        ),
        "build_failed": (
            "The server build failed, so the live version stays and you can fix the code"
            " and build again."
        ),
        LIMIT_EXCEEDED: (
            "The version or the call went past a limit, so make the source smaller"
            " or the call lighter."
        ),
        "timeout": "The call ran too long, so make it do less work and run it again.",
        "door_refused": "The door refused the call, so read its reason and change the call to fit.",
        "not_approved": (
            "A publisher has not approved this version yet, so ask a publisher to approve it."
        ),
        "code_off": "You have no code right, so ask an admin to turn code on for your group.",
        "code_paused": (
            "An admin stopped code for your organization, so ask an admin to turn it on again."
        ),
        "runtime_busy": "Try again in a few seconds.",
        "bad_output": (
            "The output does not match the output schema, so change the code to return"
            " the declared shape."
        ),
        CONTRACT_UNSUPPORTED: (
            "The server does not serve the contract version that the folder pins,"
            " so pin a served version."
        ),
    }
)


def check_source_size(total_bytes: int, file_count: int, kind: str = "server") -> str | None:
    """Return ``limit_exceeded`` when the source passes a proposed limit, else None.

    Pure. No live caller yet: the manifest caps enforce the byte limit today.
    Raises ``KeyError`` for a kind that ``LIMITS`` does not hold.
    """
    limits = LIMITS[kind]
    if total_bytes > limits["source_bytes"] or file_count > limits["source_files"]:
        return LIMIT_EXCEEDED
    return None
