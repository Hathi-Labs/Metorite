"""The per-org flags of Settings live in one sorted registry (CIV-HS slice 3, task 3284).

`acb_common/flag_registry.py` holds each `*_orgs` and `*_modes` field, sorted by name.
Two flag PRs then edit different hunks, unless their names are neighbours.
`docs/merge_hotspots.md` has the design. A lane that has a conflict in `settings.py`
over a flag moves its field into the registry, at its sorted place.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

from acb_common.flag_registry import FlagRegistry
from acb_common.settings import Settings

_ROOT = Path(__file__).resolve().parents[2]
_REGISTRY = _ROOT / "packages/acb_common/acb_common/flag_registry.py"
_PER_ORG = re.compile(r"[a-z0-9_]+_(orgs|modes)")


def _own_fields(path: Path, cls: str) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    node = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls)
    return [n.target.id for n in node.body if isinstance(n, ast.AnnAssign)]


def test_the_registry_fields_are_sorted_by_name():
    names = _own_fields(_REGISTRY, "FlagRegistry")
    assert names == sorted(names), "put a new flag at its sorted place"
    assert len(names) == len(set(names))


def test_the_registry_holds_only_per_org_lists():
    for name in _own_fields(_REGISTRY, "FlagRegistry"):
        assert _PER_ORG.fullmatch(name), f"{name} is not a *_orgs or *_modes field: it belongs in settings.py"
        assert FlagRegistry.model_fields[name].default == ""


def test_settings_declares_no_per_org_list_of_its_own():
    own = _own_fields(_ROOT / "packages/acb_common/acb_common/settings.py", "Settings")
    stray = [n for n in own if _PER_ORG.fullmatch(n)]
    assert not stray, f"move {stray} into acb_common/flag_registry.py, at the sorted place"


def test_settings_inherits_every_registry_field():
    registry = set(FlagRegistry.model_fields)
    assert registry and registry <= set(Settings.model_fields)
