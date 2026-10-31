"""The TypeScript manifest types equal the Python schema (STU-A2).

No code generator exists. ``manifest.ts`` is hand-written and this test holds
it equal to ``studio_manifest.py`` and its exported JSON Schema, as the genUI
lockstep test does for the template catalog.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from acb_common.app_manifests.base import CLASSES
from acb_common.studio_manifest import BLOCKS, FIELD_TYPES, Manifest

ROOT = Path(__file__).resolve().parents[2]
TS = ROOT / "workbench/control_plane/src/lib/studio/manifest.ts"
SCHEMA = ROOT / "packages/acb_common/acb_common/studio_manifest.schema.json"


def _ts() -> str:
    return TS.read_text(encoding="utf-8")


def _union(name: str) -> list[str]:
    m = re.search(rf"export type {name} =(.*?);", _ts(), re.S)
    assert m, f"{name} is missing from manifest.ts"
    return [a or b for a, b in re.findall(r'"([^"]+)"|(\d+)', m.group(1))]


def _interface_keys(name: str) -> set[str]:
    m = re.search(rf"export interface {name} \{{(.*?)\n\}}", _ts(), re.S)
    assert m, f"{name} is missing from manifest.ts"
    return set(re.findall(r"^  (\w+)\??:", m.group(1), re.M))


def test_top_level_keys():
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    assert (
        _interface_keys("StudioManifest") == set(schema["properties"]) == set(Manifest.model_fields)
    )


def test_field_types():
    assert _union("FieldType") == list(FIELD_TYPES)
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    assert schema["$defs"]["FieldDef"]["properties"]["type"]["enum"] == list(FIELD_TYPES)


def test_block_names():
    assert _union("BlockName") == list(BLOCKS)
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    assert schema["$defs"]["ViewDef"]["properties"]["block"]["enum"] == list(BLOCKS)


def test_class_letters():
    assert _union("ClassLetter") == list(CLASSES)


def test_studio_version_literal():
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    assert _union("StudioVersion") == [str(schema["properties"]["studio"]["const"])]


def test_nested_interface_keys_match_the_schema():
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    pairs = {
        "FieldDef": "FieldDef",
        "ObjectDef": "ObjectDef",
        "ActionDef": "ActionDef",
        "ViewDef": "ViewDef",
        "WorkflowDef": "WorkflowDef",
        "Skill": "Skill",
        "Shell": "Shell",
    }
    for ts_name, defn in pairs.items():
        assert _interface_keys(ts_name) == set(schema["$defs"][defn]["properties"]), ts_name
