"""The one-reader fence of the CRM kill switch (WS-53 CRM-0, item 10).

CIV-33f (task 3223). These tests walk the source tree, so a file that holds them
is a scanner file and runs on each Python change. They live apart from
``test_crm_kill_switch.py`` so that its slow behaviour tests stay out of that set.
The bodies are unchanged.
"""

from __future__ import annotations

import ast
from pathlib import Path

# ── 10. One reader (R7) ──────────────────────────────────────────────────────

_REPO = Path(__file__).resolve().parents[2]
_READER = _REPO / "apps/services/gateway/gateway/routes/crm/flags.py"


#: The CRM settings that only ``flags.py`` may read.
_SWITCHES = frozenset({
    "crm_enabled", "crm_orgs", "crm_mirror", "crm_mirror_orgs",
    # WS-53 CRM-C4a: the field studio list.
    "crm_field_studio_orgs",
    # WS-53 CRM-Q17a-1: the fill rank list.
    "crm_fill_rank_orgs",
    # WS-53 CRM-Z10b: the list of the switch to Native.
    "crm_cutover_orgs",
})


def _setting_readers(roots: list[Path]) -> list[str]:
    """Each file that reads one of the CRM switches as an attribute."""
    hits: list[str] = []
    for root in roots:
        for path in root.rglob("*.py"):
            if ".venv" in path.parts or "node_modules" in path.parts:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and node.attr in _SWITCHES:
                    hits.append(path.resolve().as_posix())
                    break
    return hits


def test_flags_is_the_one_reader_of_the_switch() -> None:
    readers = _setting_readers([_REPO / "apps", _REPO / "packages"])
    assert readers == [_READER.as_posix()], (
        "Only routes/crm/flags.py may read settings.crm_enabled, "
        f"settings.crm_orgs, settings.crm_mirror or settings.crm_mirror_orgs. Readers found: {readers}"
    )


def test_the_one_reader_fence_sees_a_second_reader(tmp_path: Path) -> None:
    plant = tmp_path / "second.py"
    plant.write_text("def f(s):\n    return s.crm_orgs\n", encoding="utf-8")
    assert _setting_readers([tmp_path]) == [plant.resolve().as_posix()]
