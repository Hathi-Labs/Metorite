"""A deploy never turns Mem0 on by itself (WS-17 EM-G11f3).

Spec: ``project-docs/specs/email_app_master_plan.md`` section 12.3.13.9.1,
item 7 and acceptance 8. Both deploy paths write a ``MEM0_ENABLED`` line when
``.env`` has none: ``deploy/hostinger/deploy.sh`` and ``scripts/vps_apply.sh``,
which the CI deploy runs (``.github/workflows/deploy.yml``). That default was
on, so a deleted line turned Mem0 on at the next deploy, with no owner act. A
chat memory store can hold mail text, and no mailbox purge reaches it. The
flip of ``MEM0_ENABLED`` stays an owner act (WS-7).

R7 fence ``deploy-memory-default-off``: each script writes ``false`` for a
missing ``MEM0_ENABLED`` line, and the same for ``GRAPHITI_ENABLED``.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

#: The scripts that add a missing memory flag to ``.env``.
SCRIPTS = ("deploy/hostinger/deploy.sh", "scripts/vps_apply.sh")

#: ``echo "<NAME>=<value>" >> "$ENV_FILE"`` in the script.
_DEFAULT_LINE = re.compile(
    r'echo\s+"(?P<name>MEM0_ENABLED|GRAPHITI_ENABLED)=(?P<value>[^"]*)"\s*>>\s*"\$ENV_FILE"')


def defaults(source: str) -> dict[str, list[str]]:
    """Each value that *source* writes for a missing memory flag, by name."""
    found: dict[str, list[str]] = {}
    for m in _DEFAULT_LINE.finditer(source):
        found.setdefault(m["name"], []).append(m["value"].strip().lower())
    return found


@pytest.mark.parametrize("rel", SCRIPTS)
def test_a_missing_memory_line_defaults_to_off(rel: str) -> None:
    source = (REPO / rel).read_text(encoding="utf-8")
    found = defaults(source)
    assert found.get("MEM0_ENABLED"), f"{rel} writes no default for MEM0_ENABLED"
    assert found.get("GRAPHITI_ENABLED"), f"{rel} writes no default for GRAPHITI_ENABLED"
    for name, values in found.items():
        assert values == ["false"], (
            f"{rel} writes {name}={values} for a missing line. A deploy must "
            "never turn a memory store on (EM-G11f3). The flip is an owner act")


def test_the_scan_reads_each_value() -> None:
    """The scan bites: a value other than ``false`` is read as written."""
    other = 'MEM0_ENABLED)       echo "MEM0_ENABLED=maybe" >> "$ENV_FILE" ;;'
    assert defaults(other) == {"MEM0_ENABLED": ["maybe"]}
