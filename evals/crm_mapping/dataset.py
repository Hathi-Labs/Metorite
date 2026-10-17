"""The synthetic schemas of the mapping eval (WS-53 CRM-Q20-1).

Spec: ``project-docs/specs/crm_platform.md`` §18.9.6, CRM-Q20 item 4.

``schemas.json`` holds the schemas and their expected answers, and
``scripted_answers.json`` holds the answers that ``--scripted`` replays. An
agent invented each module, field and value. This module turns a schema into
the inputs of ``map_pipeline.Pipeline`` and into sampled records, with a
fixed seed, so each run is the same.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gateway.crm_sources.zoho import mapping
from gateway.crm_sync import map_pipeline as mp

HERE = Path(__file__).resolve().parent
SCHEMAS = HERE / "schemas.json"
ANSWERS = HERE / "scripted_answers.json"

_FIRST = ("Asha", "Ravi", "Meera", "Arjun", "Nisha", "Kiran", "Vikram", "Leela")
_LAST = ("Rao", "Iyer", "Shah", "Menon", "Das", "Patel", "Nair", "Gupta")
_WORDS = ("Blue", "River", "Delta", "Summit", "Harbor", "Cedar", "Orbit", "Granite")
_CITIES = ("Pune", "Nagpur", "Indore", "Surat", "Kochi", "Mysore")


@dataclass(frozen=True)
class EvalSchema:
    """One synthetic schema: the inputs, the records of each module, the
    records of each core slot, and the expected answers."""

    id: str
    tags: tuple[str, ...]
    inputs: mp.Inputs
    records: dict[str, list[dict[str, Any]]]
    core_records: dict[str, list[dict[str, Any]]]
    expected: dict[str, Any]


def _value(gen: str, i: int, options: list[str] | None) -> Any:
    person = f"{_FIRST[i % len(_FIRST)]} {_LAST[(i // 3) % len(_LAST)]}"
    values: dict[str, Any] = {
        "company": f"{_WORDS[i % len(_WORDS)]} {_WORDS[(i // 5) % len(_WORDS)]} Pumps",
        "name": person,
        "email": f"{person.split()[0].lower()}.{i}@example.com",
        "phone": f"+91 98{i:08d}",
        "url": f"https://www.example-{i}.com",
        "linkedin": f"https://www.linkedin.com/company/example-{i}",
        "text": f"Note {i} about the pump at the plant.",
        "code": f"DOC-{10000 + i}",
        "city": _CITIES[i % len(_CITIES)],
        "number": 10 + i,
        "currency": 1000.5 + i,
        "percent": (i * 7) % 100,
        "date": f"2026-{1 + i % 12:02d}-{1 + i % 28:02d}",
        "owner": {"id": f"u{i % 3}", "name": person, "email": f"owner{i % 3}@example.com"},
    }
    if gen == "select":
        return (options or ["A"])[i % len(options or ["A"])]
    return values[gen]


def _fields(spec: dict[str, Any]) -> list[list[Any]]:
    rows = [list(f) for f in spec.get("fields") or []]
    pad = spec.get("pad")
    if pad:
        count, fill = pad
        rows += [[f"Custom_{n}", f"Custom Field {n}", "text", "text", fill] for n in range(count)]
    return rows


def _records(fields: list[list[Any]], count: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for i in range(count):
        row: dict[str, Any] = {}
        for f in fields:
            key, _label, _type, gen, fill = f[:5]
            options = f[5] if len(f) > 5 else None
            if i < round(fill * count):
                row[key] = _value(gen, i, options)
        out.append(row)
    return out


def _module(spec: dict[str, Any]) -> tuple[mp.ModuleShape, list[dict[str, Any]]]:
    fields = _fields(spec)
    shape = mp.ModuleShape(
        api_name=spec["api_name"], label=spec["label"], singular=spec.get("singular"),
        records=int(spec["records"]), display_field=spec.get("display_field"),
        fields=tuple(mp.FieldShape(key=f[0], label=f[1], type=f[2]) for f in fields),
        # CRM-Q31-3: code rule 2 of the catalogue reads it.
        custom=spec["api_name"] not in mapping.GENERIC_STANDARD_MODULES,
    )
    return shape, _records(fields, min(int(spec["records"]), mp.SAMPLE_ROWS))


def _slot(slot: str, empty: bool, core: dict[str, Any] | None,
          ) -> tuple[mp.SlotShape, list[dict[str, Any]]]:
    if empty or not core:
        return mp.SlotShape(slot=slot, empty=empty), []
    custom = [list(f) for f in core.get("custom") or []]
    standard = mp.standard_fields(slot)
    filled = dict(core.get("filled") or {})
    columns = {t.standard_field: c for c, t in mapping.FIELD_RULE_TARGETS[slot].items()
               if t.standard_field}
    customs = _records(custom, 60)
    rows = []
    for i in range(60):
        row: dict[str, Any] = {c: None for c in columns.values()}
        for column, std in filled.items():
            gen = "email" if column == "email" else "phone" if column in ("phone", "mobile") \
                else "company" if column == "organization_name" else "text"
            row[columns[std]] = _value(gen, i, None)
        row["custom"] = customs[i]
        rows.append(row)
    shape = mp.SlotShape(
        slot=slot, empty=False,
        fields=tuple(standard) + tuple(mp.FieldShape(key=f[0], label=f[1], type=f[2])
                                       for f in custom),
        filled=filled, statuses=dict(core.get("statuses") or {}),
    )
    return shape, rows


def load_schemas(path: Path = SCHEMAS) -> list[EvalSchema]:
    """The schemas of the set, in file order."""
    out = []
    for spec in json.loads(path.read_text(encoding="utf-8"))["schemas"]:
        modules, records = [], {}
        for m in spec["modules"]:
            shape, rows = _module(m)
            modules.append(shape)
            records[shape.api_name] = rows
        slots, core_records = [], {}
        for slot in mp.executor.SLOTS:
            shape, rows = _slot(slot, bool(spec["slots"][slot]), (spec.get("core") or {}).get(slot))
            slots.append(shape)
            core_records[slot] = rows
        out.append(EvalSchema(
            id=spec["id"], tags=tuple(spec.get("tags") or ()),
            inputs=mp.Inputs(profile_version=1, modules=tuple(modules), slots=tuple(slots)),
            records=records, core_records=core_records, expected=spec["expected"],
        ))
    return out


def load_answers(path: Path = ANSWERS) -> dict[str, dict[str, dict[str, Any]]]:
    """The scripted answers: ``{schema id: {mode: {question key: answer}}}``."""
    return json.loads(path.read_text(encoding="utf-8"))["answers"]
