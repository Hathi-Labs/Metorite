"""The open questions of the CRM (WS-53 CRM-Q32, ``crm_platform.md`` §18.14).

The CRM shows the admin each question that Jev could not answer with
confidence about the data of the org. Each answer becomes a fixed fact of the
mapping plan. This module holds the five kinds, the gate of ``open``, the
order, the ids and the sentences of why, as PURE functions. It reads no
database and calls no model. ``routes/crm/questions.py`` reads the inputs and
calls :func:`build`.

**One derived read. No new table** (§18.14.3). The inputs are the catalogue
entries (``catalogue.build``), the approved plan, the cursor rows of the
pipeline, the empty slots and the values of each approved stage field.

**The kinds.**

* ``purpose`` — what a module is for. Id ``purpose:<Module>``.
* ``key_field`` — the amount, the date, the status or the customer of a money
  entry. Id ``key:<Module>:<part>``.
* ``slot`` — a module that could stand in for an empty core slot. Id
  ``slot:<slot>``.
* ``stage_value`` — a value of an approved stage field with no entry. Id
  ``stage:<slot>:<hash of the value>``.
* ``revenue`` — billed or collected. Id ``revenue``.
* ``term`` — which field the org's own word for a figure means
  (§18.16 part 4). Id ``term:<Module>:<slug>``. It opens only when the chat
  met the word and two fields gave different totals (``crm_sync/terms.py``).

**The gate of ``open``.** A part in ``open`` waits for the AI or for the
admin. The admin sees it only when the mode of ``crm.map_catalogue`` is
``off`` or ``shadow``, or when the cache key of the part is parked. In ``on``
the admin sees a part only after the pipeline closed it as ``null``.

**A dismissal holds** until the hash of its question changes: the
``catalogue_hash`` of a module, the hash of the roles for a slot, and the
hash of the options of a stage field.

Fences: ``tests/unit/test_crm_questions.py`` and
``tests/unit/test_crm_questions_r8.py`` (R8).
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any

from gateway.crm_sync import catalogue as cat

KIND_PURPOSE = "purpose"
KIND_KEY = "key_field"
KIND_SLOT = "slot"
KIND_STAGE = "stage_value"
KIND_REVENUE = "revenue"
#: WS-53 §18.16 part 4: what the org's own word for a figure means.
KIND_TERM = "term"
KINDS: tuple[str, ...] = (KIND_PURPOSE, KIND_KEY, KIND_SLOT, KIND_STAGE, KIND_REVENUE,
                          KIND_TERM)

#: Why a question shows. ``answered`` marks a part in use, for a correction
#: (``GET /crm/questions?all=1``).
REASON_OPEN = "open"
REASON_UNSURE = "unsure"
REASON_STALE = "stale"
REASON_ANSWERED = "answered"

#: The id of the one revenue question.
REVENUE_ID = "revenue"

#: The cursor row of the questions (§18.14.3). No migration: it is a row of
#: ``crm_sync_cursors``, tenant-scoped with FORCE RLS. ``dismissed`` maps a
#: question id to ``{"schema": hash}``. ``seen``, ``new_at`` and ``money``
#: are the state of the questions step after each sync run.
QUESTIONS_ROW = "zoho:questions"

#: The choices of a question, and the samples of each choice.
CHOICE_LIMIT = 3
SAMPLE_LIMIT = 3

#: The questions that the section shows first (§18.14.7).
SHOWN = 5

#: CRM-Q32-9 (§18.14.10): the card of an ``amount`` question offers "These
#: fields add up to the amount" when 2 or more currency fields each fill at
#: least this share of the records. The picker lists at most
#: :data:`SUM_CHOICES` of them, the most filled first.
SUM_FILL_FLOOR = 0.2
SUM_CHOICES = 12

#: The words of a stage value, in the order the card shows them.
STAGE_WORDS: tuple[str, ...] = ("open", "won", "lost")
STAGE_LABELS: Mapping[str, str] = {"open": "Open", "won": "Won", "lost": "Lost"}

#: The value of an answer that puts a part back to the answer of the
#: pipeline or of code (§18.14.6, CRM-Q31-4 item 4). A Zoho API name cannot
#: start with ``@``.
AUTO = "@auto"

#: The mode word of the hash of a module question. A flip of the mode of the
#: pipeline does not ask a dismissed question again.
HASH_MODE = "questions"

#: Each core slot → its label, and its slug of ``catalogue.CORE_FITS``.
SLOT_LABELS: Mapping[str, str] = {
    "company": "Companies", "contact": "Contacts", "lead": "Leads", "deal": "Deals",
}
SLOT_FITS: Mapping[str, str] = {
    "company": "organizations", "contact": "contacts", "lead": "leads", "deal": "deals",
}

#: The order of the key parts inside a module (§18.14.7).
PART_ORDER: Mapping[str, int] = {"amount": 0, "date": 1, "status": 2, "parent": 3}

#: What a card calls each purpose.
PURPOSE_LABELS: Mapping[str, str] = {
    "customer_company": "Customer company", "customer_site": "Customer site",
    "person": "Person", "lead": "Lead", "deal": "Deal or quote", "contract": "Contract or order",
    "invoice": "Invoice", "payment_in": "Payment received", "receivable": "Money owed to us",
    "payment_out": "Payment made", "product": "Product", "asset": "Asset", "vendor": "Vendor",
    "partner": "Partner", "activity": "Activity", "reference": "Reference list",
    "other": "Other",
}

#: What a card calls each meaning of revenue.
REVENUE_LABELS: Mapping[str, str] = {"invoice": "Billed", "payment_in": "Collected"}


# ── The shapes ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Choice:
    """One choice of a question. ``samples`` are masked values
    (``map_pipeline.masked``). ``best`` marks Jev's best guess."""

    value: str
    label: str
    fill: float | None = None
    samples: tuple[str, ...] = ()
    best: bool = False
    records: int | None = None
    modules: tuple[str, ...] = ()
    #: CRM-Q33 part 5: the words of the evidence of a money field, such as
    #: ``excludes_gst`` (:data:`EVIDENCE_WORDS`).
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class Question:
    """One question (§18.14.3, "The wire")."""

    id: str
    kind: str
    label: str
    reason: str
    records: int
    money: bool
    why: str
    choices: tuple[Choice, ...] = ()
    none: bool = False
    other: bool = False
    module: str | None = None
    part: str | None = None
    #: The answer in use. A sum (CRM-Q32-9) is the tuple of its keys.
    current: str | tuple[str, ...] | None = None
    by: str | None = None
    count: int | None = None
    slot: str | None = None
    value: str | None = None
    field: str | None = None
    #: The hash that a dismissal records. ``None`` takes no dismissal.
    hash: str | None = None
    #: Each choice of "Pick another", for ``purpose`` and ``key_field``.
    options: tuple[Choice, ...] = ()
    #: CRM-Q32-9: the fields of the picker of a sum, on an ``amount``
    #: question only (:func:`sum_fields`). Empty when the card offers none.
    sums: tuple[Choice, ...] = ()
    #: CRM-Q33 part 5: the fields whose sum the business rules point at, on
    #: an ``amount`` question (:func:`steered`). Empty with no such rule.
    suggested_sum: tuple[str, ...] = ()

    def best(self) -> Choice | None:
        """Jev's best guess, or ``None``."""
        return next((c for c in self.choices if c.best), None)


@dataclass(frozen=True)
class SlotState:
    """One core slot: empty or not, the module of its approved role, and
    whether the open draft suggests a module for it."""

    slot: str
    empty: bool
    role: str | None = None
    suggested: bool = False


@dataclass(frozen=True)
class StageState:
    """One approved ``stage`` item, and the values of its field that the
    records hold, each with its count, the most first."""

    slot: str
    module: str
    label: str
    field: str
    values: Mapping[str, str]
    seen: Sequence[tuple[str, int]] = ()
    options_hash: str | None = None


@dataclass(frozen=True)
class Inputs:
    """What :func:`build` reads. ``cache`` is the ``map_pipeline.Cache`` of
    ``zoho:map_pipeline``. ``dismissed`` maps a question id to the hash it
    was dismissed on (``zoho:questions``). ``samples`` maps a module to the
    masked values of each field, and ``names`` to the masked names of its
    records."""

    entries: Sequence[Any]
    sources: Mapping[str, Any]
    stored: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    mode: str = "off"
    cache: Any = None
    now: datetime | None = None
    slots: Sequence[SlotState] = ()
    doubts: Mapping[str, Sequence[str]] = field(default_factory=dict)
    dismissed_roles: Mapping[str, tuple[str, str | None]] = field(default_factory=dict)
    slots_digest: str | None = None
    stages: Sequence[StageState] = ()
    dismissed: Mapping[str, str | None] = field(default_factory=dict)
    samples: Mapping[str, Mapping[str, Sequence[str]]] = field(default_factory=dict)
    names: Mapping[str, Sequence[str]] = field(default_factory=dict)
    #: CRM-Q32-8: question id → its proposals (:func:`proposals_of`).
    proposals: Mapping[str, Sequence[Mapping[str, Any]]] = field(default_factory=dict)
    #: §18.16 part 4: the terms that the chat asked (question id →
    #: ``{"module", "word"}``), and an admin's answer of each (id → key).
    terms: Mapping[str, Mapping[str, str]] = field(default_factory=dict)
    term_answers: Mapping[str, str] = field(default_factory=dict)


# ── Ids and hashes ───────────────────────────────────────────────────────────


def _digest(material: Any, size: int = 32) -> str:
    raw = json.dumps(material, sort_keys=True, default=str, ensure_ascii=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:size]


def value_hash(value: str) -> str:
    """The short hash of a stage value in its question id. The value can
    hold any text, so it never goes in an id as it is."""
    return _digest(["stage_value", value], 12)


def purpose_id(module: str) -> str:
    return f"purpose:{module}"


def key_id(module: str, part: str) -> str:
    return f"key:{module}:{part}"


def slot_id(slot: str) -> str:
    return f"slot:{slot}"


def stage_id(slot: str, value: str) -> str:
    return f"stage:{slot}:{value_hash(value)}"


def module_hash(src: Any) -> str:
    """The hash of a module question: ``catalogue_hash`` of the module
    (§18.14.6). A new field, a changed type or a fill that moves a step of
    10% asks again."""
    from gateway.crm_sync.map_pipeline import catalogue_hash

    return catalogue_hash([src], HASH_MODE)


def slots_hash(digest: str | None, entries: Sequence[Any]) -> str:
    """The hash of a slot question: ``slots_digest`` of the pipeline. Before
    the first run of the pipeline it has none, so the modules with records
    serve, in the same spirit."""
    if digest:
        return str(digest)
    return _digest(sorted((e.module, e.records > 0) for e in entries if e.kind == "module"))


def options_hash(options: Any, field_key: str) -> str:
    """The hash of a stage question: the options of the stage field."""
    return _digest(["stage", field_key, options])


# ── The gate of ``open`` ─────────────────────────────────────────────────────


def _now(inputs: Inputs) -> datetime:
    return inputs.now or datetime.now(UTC)


def shows_open(inputs: Inputs, entry: Any, part: str) -> bool:
    """True when the admin may answer *part* of *entry* while it waits in
    ``open`` (§18.14.3, the gate): the mode is ``off`` or ``shadow``, the
    cache key of the part is parked, or the key is a cache hit whose item
    lacks the part, so the AI never asks it."""
    if inputs.mode in ("off", "shadow"):
        return True
    if inputs.cache is None:
        return False
    from gateway.crm_sync import map_pipeline as mp

    src = inputs.sources.get(entry.module)
    if src is None:
        return False
    if part == "purpose":
        key = f"{mp.CATALOGUE_PURPOSES_KEY}{entry.module}"
        digest = mp.catalogue_hash([src], inputs.mode)
    else:
        key = f"{mp.CATALOGUE_KEY}{entry.module}"
        digest = mp.catalogue_hash([src], inputs.mode, entry.purpose)
    if inputs.cache.parked(key, digest, _now(inputs)):
        return True
    # Fix round P1: a cache hit whose item lacks the part asks nothing again
    # (the pipeline keeps a hit and never re-asks it). So the AI never closes
    # the part, and only the admin can answer it. This is the path of
    # "@auto" on a part that the admin answered before the mode was `on`.
    return bool(inputs.cache.hit(key, digest)) and not any(
        part in item for item in inputs.cache.items.get(key, []))


def part_reasons(entry: Any, gate: Callable[[str], bool]) -> dict[str, str]:
    """Each part of *entry* that is a question now, and its reason.

    ``stale``: the field or the word of an answer is gone. ``open``: the
    part waits in ``open`` and *gate* lets the admin answer it. ``unsure``:
    the pipeline asked and closed it as ``null``. A part with ``by: admin``
    is answered, also a ``null`` by ``admin`` (§18.14.8), and a purpose
    that a ``role`` gives waits for the approve path. A module with no
    record asks nothing."""
    out: dict[str, str] = {}
    if entry.records <= 0:
        return out
    by = entry.by
    if by.get("purpose") != cat.BY_ROLE:
        if "purpose" in entry.stale:
            out["purpose"] = REASON_STALE
        elif "purpose" in entry.open and gate("purpose"):
            out["purpose"] = REASON_OPEN
        elif entry.purpose is None and by.get("purpose") == "ai":
            out["purpose"] = REASON_UNSURE
    if entry.purpose in cat.MONEY_PURPOSES:
        for part in cat.KEY_PARTS:
            if part in entry.stale:
                out[part] = REASON_STALE
            elif part in entry.open and gate(part):
                out[part] = REASON_OPEN
            elif (part in entry.fields and entry.fields[part] is None
                  and by.get(part) == "ai"):
                out[part] = REASON_UNSURE
    return out


# ── The sentences of why ─────────────────────────────────────────────────────


def revenue_basis(stored: Mapping[str, Mapping[str, Any]]) -> str:
    """The purpose of the modules that each answer about revenue reads:
    the admin's answer, else billed (§18.14.11, call 1)."""
    value, _by = cat.revenue_of(stored)
    return value or cat.DEFAULT_REVENUE


def why_purpose(label: str) -> str:
    return f"Totals and chat answers about {label} wait for this answer."


def why_key(part: str, label: str, revenue: bool = False) -> str:
    """The sentence of a key part (§18.14.3, "Why it matters")."""
    if part == "amount":
        line = f"Totals of {label} use this field."
        return line + (" Each answer about revenue uses it too." if revenue else "")
    if part == "date":
        return f"Totals of {label} by month or quarter use this date."
    if part == "status":
        return f"Counts of {label} by status use this field."
    return f"Totals of {label} by customer use this link."


def why_slot(slot: str, module_label: str) -> str:
    return f"{SLOT_LABELS[slot]} holds no records. {module_label} could stand in for it."


def why_stage(count: int) -> str:
    if count == 1:
        return "1 record with this value shows as Unsorted."
    return f"{count:,} records with this value show as Unsorted."


WHY_REVENUE = "Each answer about revenue uses this. Until you answer, Metorite uses Billed."


# ── The choices ──────────────────────────────────────────────────────────────


def _purpose_choice(word: str, best: bool = False) -> Choice:
    return Choice(value=word, label=PURPOSE_LABELS.get(word, word), best=best)


def _ranked(inputs: Inputs, module: str, part: str) -> list[str]:
    item = inputs.stored.get(module) or {}
    ranked = item.get("ranked") if isinstance(item.get("ranked"), Mapping) else {}
    words = ranked.get(part) if isinstance(ranked, Mapping) else None
    return [str(w) for w in words] if isinstance(words, list) else []


def purpose_choices(inputs: Inputs, module: str) -> tuple[Choice, ...]:
    """Up to 3 purposes, Jev's best first. A purpose with no rank offers no
    short list, only "Pick another" (§18.14.3)."""
    words = [w for w in _ranked(inputs, module, "purpose") if w in cat.PURPOSES]
    return tuple(_purpose_choice(w, best=(i == 0)) for i, w in enumerate(words[:CHOICE_LIMIT]))


def _fill(src: Any, key: str) -> float | None:
    fills = getattr(src, "fills", None)
    share = fills.get(key) if isinstance(fills, Mapping) else None
    return round(float(share), 4) if share is not None else None


def _field_choice(inputs: Inputs, src: Any, f: Any, best: bool = False) -> Choice:
    samples = inputs.samples.get(src.module, {}).get(f.key, ())
    return Choice(value=f.key, label=f.label, fill=_fill(src, f.key),
                  samples=tuple(samples)[:SAMPLE_LIMIT], best=best)


def part_fields(src: Any, part: str) -> list[Any]:
    """Each field of *src* that may hold *part*: the right type, and a
    lookup to a module for ``parent``. "Pick another field" offers them."""
    return [f for f in src.fields if f.type in cat.PART_TYPES[part]
            and not (part == "parent" and not f.target)]


def sum_fields(src: Any) -> list[Any]:
    """CRM-Q32-9: the fields that the picker of a sum lists. Each is a
    ``currency`` field with a fill of :data:`SUM_FILL_FLOOR` or more, the
    most filled first, at most :data:`SUM_CHOICES`. Empty when fewer than 2
    fields pass, so the card offers no sum."""
    found = [f for f in getattr(src, "fields", ())
             if f.type in cat.SUM_TYPES and (_fill(src, f.key) or 0.0) >= SUM_FILL_FLOOR]
    found.sort(key=lambda f: (-(_fill(src, f.key) or 0.0), f.key))
    return found[:SUM_CHOICES] if len(found) >= cat.SUM_MIN else []


def sum_problem(src: Any, keys: Any) -> bool:
    """True unless *keys* is a list of 2 to 5 distinct keys, each a
    ``currency`` field of *src* (CRM-Q32-9). The shape is checked first, so
    a hostile key never reaches a read."""
    if cat.sum_shape_problem(keys) or src is None:
        return True
    return cat.problem(src, cat.SUM_PART, keys) is not None


def sum_choice(src: Any, keys: Sequence[str]) -> Choice:
    """The choice of a sum, for the line of an answer: "the sum of A, B and
    C". The caller checked *keys* (:func:`sum_problem`)."""
    labels = [getattr(src.field(k), "label", k) for k in keys]
    return Choice(value=cat.SUM_KEY_JOIN.join(keys), label=f"the sum of {names(labels)}")


#: CRM-Q32-9: a pair of parts that holds the same value on this share or
#: more of the records that set both gets a warning. A sum counts it twice.
SUM_EQUAL_SHARE = 0.5


@dataclass(frozen=True)
class SumReport:
    """The words of the sum check: the coverage line and each warning."""

    line: str
    warnings: tuple[str, ...] = ()


def _percent(part: int, whole: int) -> int:
    return round(100 * part / whole) if whole > 0 else 0


def sum_report(labels: Sequence[str], records: int, covered: int,
               pairs: Sequence[tuple[int, int, int, int]], *, capped: bool = False,
               ) -> SumReport:
    """The coverage line of a ticked set of *labels*, and a warning for each
    pair ``(i, j, both, equal)`` whose parts are equal on
    :data:`SUM_EQUAL_SHARE` or more of the records that set both. With
    *capped*, the check read only the first records of the module."""
    whole = f"the first {records:,}" if capped else f"{records:,}"
    noun = "record" if records == 1 else "records"
    line = (f"These {len(labels)} fields cover {covered:,} of {whole} {noun} "
            f"({_percent(covered, records)}%).")
    warnings = tuple(
        f"{labels[i]} and {labels[j]} hold the same value on {_percent(equal, both)}% "
        "of records. A sum counts it twice."
        for i, j, both, equal in pairs
        if both > 0 and equal >= SUM_EQUAL_SHARE * both
    )
    return SumReport(line=line, warnings=warnings)


def names(labels: Sequence[str]) -> str:
    """``A``, ``A and B`` or ``A, B and C``."""
    if len(labels) < 2:
        return "".join(labels)
    return f"{', '.join(labels[:-1])} and {labels[-1]}"


def candidates(src: Any, part: str) -> list[Any]:
    """The candidates of code, through ``map_pipeline.part_candidates``:
    the right type, a fill above 0, the most filled first."""
    from gateway.crm_sync import map_pipeline as mp

    shapes = [mp.FieldShape(key=f.key, label=f.label, type=f.type,
                            lookup_module=f.target, fill=_fill(src, f.key))
              for f in src.fields]
    keys = [s.key for s in mp.part_candidates(part, shapes)]
    return [src.field(k) for k in keys if src.field(k) is not None]


def key_choices(inputs: Inputs, src: Any, part: str) -> tuple[Choice, ...]:
    """Up to 3 fields, Jev's best first, each with its fill and its masked
    samples. With no rank, the most filled first, and no best. A ranked
    field that is gone, or has another type, drops out (§18.14.13)."""
    legal = {f.key: f for f in part_fields(src, part)}
    ranked = [legal[k] for k in _ranked(inputs, src.module, part) if k in legal]
    if ranked:
        return tuple(_field_choice(inputs, src, f, best=(i == 0))
                     for i, f in enumerate(ranked[:CHOICE_LIMIT]))
    return tuple(_field_choice(inputs, src, f) for f in candidates(src, part)[:CHOICE_LIMIT])


# ── The kinds ────────────────────────────────────────────────────────────────


def _current_part(entry: Any, part: str) -> str | tuple[str, ...] | None:
    if part == "purpose":
        return entry.purpose
    found = entry.fields.get(part)
    if found is not None and getattr(found, "parts", ()):
        return tuple(p.key for p in found.parts)
    return found.key if found is not None else None


def _module_questions(inputs: Inputs, entry: Any, reasons: Mapping[str, str],
                      ) -> list[Question]:
    src = inputs.sources.get(entry.module)
    if src is None:
        return []
    digest = module_hash(src)
    basis = revenue_basis(inputs.stored)
    out: list[Question] = []
    for part, reason in reasons.items():
        current = _current_part(entry, part) if reason == REASON_ANSWERED else None
        by = entry.by.get(part) if reason == REASON_ANSWERED else None
        if part == "purpose":
            out.append(Question(
                id=purpose_id(entry.module), kind=KIND_PURPOSE, label=entry.label,
                reason=reason, records=entry.records, money=False,
                why=why_purpose(entry.label), choices=purpose_choices(inputs, entry.module),
                none=True, other=True, module=entry.module, part="purpose",
                current=current, by=by, hash=digest,
                options=tuple(_purpose_choice(w) for w in cat.PURPOSES),
            ))
            continue
        out.append(Question(
            id=key_id(entry.module, part), kind=KIND_KEY, label=entry.label,
            reason=reason, records=entry.records, money=True,
            why=why_key(part, entry.label, revenue=(entry.purpose == basis)),
            choices=key_choices(inputs, src, part), none=True, other=True,
            module=entry.module, part=part, current=current, by=by, hash=digest,
            options=tuple(Choice(value=f.key, label=f.label, fill=_fill(src, f.key))
                          for f in part_fields(src, part)),
            sums=tuple(Choice(value=f.key, label=f.label, fill=_fill(src, f.key))
                       for f in sum_fields(src)) if part == cat.SUM_PART else (),
        ))
    return out


def slot_candidates(inputs: Inputs, slot: str) -> list[Any]:
    """The candidate entries of an empty *slot*, best first (§18.14.3).

    The doubts of the pipeline first. With no doubt, each module whose
    purpose fits the slot and that holds records, the most records first. A
    module that plays a slot, that holds no record, or that the admin
    dismissed for the slot on the schema of today drops out."""
    from gateway.crm_sync import map_pipeline as mp

    modules = {e.module: e for e in inputs.entries if e.kind == "module"}
    taken = {s.role for s in inputs.slots if s.role}
    doubt = [m for m in inputs.doubts.get(slot) or () if m in modules]
    if doubt:
        found = [modules[m] for m in doubt]
    else:
        fits = cat.CORE_FITS.get(SLOT_FITS[slot], frozenset())
        found = sorted((e for e in modules.values() if e.purpose in fits),
                       key=lambda e: (-e.records, e.module))
    return [e for e in found if e.records > 0 and e.module not in taken
            and not mp.is_dismissed(inputs.dismissed_roles, slot, e.module,
                                    inputs.slots_digest)]


def _slot_questions(inputs: Inputs) -> list[Question]:
    out: list[Question] = []
    digest = slots_hash(inputs.slots_digest, inputs.entries)
    for state in inputs.slots:
        if state.slot not in SLOT_LABELS:
            continue
        if not state.empty or state.role or state.suggested:
            continue
        found = slot_candidates(inputs, state.slot)[:CHOICE_LIMIT]
        if not found:
            continue
        doubt = bool(inputs.doubts.get(state.slot))
        choices = tuple(
            Choice(value=e.module, label=e.label, records=e.records,
                   samples=tuple(inputs.names.get(e.module, ()))[:SAMPLE_LIMIT],
                   best=(doubt and i == 0))
            for i, e in enumerate(found)
        )
        out.append(Question(
            id=slot_id(state.slot), kind=KIND_SLOT, label=SLOT_LABELS[state.slot],
            # The slot holds no record, so it ranks after each question of a
            # money module (§18.14.7). Each choice carries its own count.
            reason=REASON_UNSURE if doubt else REASON_OPEN, records=0,
            money=state.slot == "deal", why=why_slot(state.slot, found[0].label),
            choices=choices, none=True, other=False, slot=state.slot, hash=digest,
        ))
    return out


def _stage_questions(inputs: Inputs) -> list[Question]:
    out: list[Question] = []
    for stage in inputs.stages:
        digest = stage.options_hash or options_hash(None, stage.field)
        for value, count in stage.seen:
            if value in stage.values or count <= 0:
                continue
            out.append(Question(
                id=stage_id(stage.slot, value), kind=KIND_STAGE, label=stage.label,
                reason=REASON_OPEN, records=int(count), money=stage.slot == "deal",
                why=why_stage(int(count)),
                choices=tuple(Choice(value=w, label=STAGE_LABELS[w]) for w in STAGE_WORDS),
                module=stage.module, count=int(count), slot=stage.slot, value=value,
                field=stage.field, hash=digest,
            ))
    return out


def revenue_question(inputs: Inputs, *, answered: bool = False) -> Question | None:
    """The question of the meaning of revenue: the catalogue holds an
    ``invoice`` entry and a ``payment_in`` entry, each with records, and no
    admin answered. With *answered*, the answer in use for a correction."""
    billed = [e for e in inputs.entries if e.purpose == "invoice" and e.records > 0]
    paid = [e for e in inputs.entries if e.purpose == "payment_in" and e.records > 0]
    if not billed or not paid:
        return None
    value, by = cat.revenue_of(inputs.stored)
    if (by == "admin") != answered:
        return None
    return Question(
        id=REVENUE_ID, kind=KIND_REVENUE, label="Revenue",
        reason=REASON_ANSWERED if answered else REASON_OPEN,
        records=max(e.records for e in [*billed, *paid]), money=True, why=WHY_REVENUE,
        choices=(
            Choice(value="invoice", label=REVENUE_LABELS["invoice"],
                   modules=tuple(e.label for e in billed)),
            Choice(value="payment_in", label=REVENUE_LABELS["payment_in"],
                   modules=tuple(e.label for e in paid)),
        ),
        current=(value or cat.DEFAULT_REVENUE) if answered else None,
        by=by if answered else None,
    )


# ── The read ─────────────────────────────────────────────────────────────────


def _tier(q: Question) -> int:
    """§18.14.7: revenue, then the questions of money, then the purposes,
    then the rest."""
    if q.kind == KIND_REVENUE:
        return 0
    if q.kind in (KIND_KEY, KIND_TERM) or (q.kind in (KIND_SLOT, KIND_STAGE) and q.slot == "deal"):
        return 1
    if q.kind == KIND_PURPOSE:
        return 2
    return 3


def rank(questions: Sequence[Question]) -> list[Question]:
    """The questions by impact (§18.14.7): ``revenue``, then each question
    of a money entry or of the slot ``deal``, the most records first and
    ``amount``, ``date``, ``status``, ``parent`` inside a module, then each
    ``purpose``, then the rest."""
    return sorted(questions, key=lambda q: (
        _tier(q), -q.records, q.module or q.slot or "", PART_ORDER.get(q.part or "", 9), q.id))


def dismissed(inputs: Inputs, q: Question) -> bool:
    """True when the admin dismissed *q* on its hash of today."""
    if q.hash is None or q.id not in inputs.dismissed:
        return False
    return inputs.dismissed[q.id] == q.hash


def module_questions(inputs: Inputs) -> list[Question]:
    """The ``purpose`` and ``key_field`` questions that are open, not
    ranked. The catalogue entry and the 422 of the aggregate read them."""
    out: list[Question] = []
    for entry in inputs.entries:
        def gate(part: str, entry: Any = entry) -> bool:
            return shows_open(inputs, entry, part)

        reasons = part_reasons(entry, gate)
        if reasons:
            out.extend(q for q in _module_questions(inputs, entry, reasons)
                       if not dismissed(inputs, q))
    return out


def term_question(inputs: Inputs, qid: str, *, answered: bool = False) -> Question | None:
    """The question of one term that the chat asked (§18.16 part 4), or
    None: the module is gone, fewer than two fields match the word, or the
    answer state is not the one asked for. Its choices are the matching
    fields, the most filled first (``terms.candidates``)."""
    from gateway.crm_sync import terms

    asked = inputs.terms.get(qid)
    src = inputs.sources.get(str((asked or {}).get("module") or ""))
    if asked is None or src is None:
        return None
    word = str(asked.get("word") or "")
    cands = terms.candidates(getattr(src, "fields", ()), getattr(src, "fills", None), word)
    if len(cands) < 2:
        return None
    current = inputs.term_answers.get(qid)
    if (current is not None) != answered:
        return None
    entry = next((e for e in inputs.entries if e.module == src.module), None)
    label = str(getattr(entry, "label", None) or getattr(src, "label", "") or src.module)
    return Question(
        id=qid, kind=KIND_TERM, label=label,
        reason=REASON_ANSWERED if answered else REASON_OPEN,
        records=int(getattr(src, "records", 0) or 0), money=True,
        why=(f'"{word}" matches more than one field of {label}, and the fields give '
             "different totals. Each answer that uses the word reads the field you choose."),
        choices=tuple(Choice(value=c.key, label=c.label, fill=c.fill, best=i == 0)
                      for i, c in enumerate(cands)),
        module=src.module, value=word, current=current,
        by="admin" if answered else None,
        hash=_digest(["term", qid, [c.key for c in cands]], 16),
    )


def term_questions(inputs: Inputs, *, answered: bool = False) -> list[Question]:
    """The term questions of the org: the open ones, or with *answered*
    each one an admin answered."""
    out = [term_question(inputs, qid, answered=answered) for qid in sorted(inputs.terms)]
    return [q for q in out if q is not None]


def build(inputs: Inputs) -> list[Question]:
    """The open questions of the org, ranked (§18.14.7)."""
    found: list[Question] = list(module_questions(inputs))
    found += _slot_questions(inputs)
    found += _stage_questions(inputs)
    found += term_questions(inputs)
    revenue = revenue_question(inputs)
    if revenue is not None:
        found.append(revenue)
    return rank([q for q in found if not dismissed(inputs, q)])


def answered(inputs: Inputs) -> list[Question]:
    """Each part in use, as a question with ``current`` (``all=1``). A
    ``purpose`` that a ``role`` gives, and a part that waits for a question,
    are left out."""
    out: list[Question] = []
    for entry in inputs.entries:
        reasons: dict[str, str] = {}
        if (entry.by.get("purpose") not in (None, cat.BY_ROLE)
                and not (entry.purpose is None and entry.by.get("purpose") == "ai")):
            reasons["purpose"] = REASON_ANSWERED
        if entry.purpose in cat.MONEY_PURPOSES:
            for part in cat.KEY_PARTS:
                if part not in entry.by or part in entry.stale:
                    continue
                if entry.fields.get(part) is None and entry.by.get(part) == "ai":
                    continue
                reasons[part] = REASON_ANSWERED
        out.extend(_module_questions(inputs, entry, reasons))
    revenue = revenue_question(inputs, answered=True)
    if revenue is not None:
        out.append(revenue)
    out.extend(term_questions(inputs, answered=True))
    return out


def count_money(questions: Sequence[Question]) -> bool:
    """True while a question of money is open (the My Day group)."""
    return any(q.money for q in questions)


def sampled_modules(questions: Sequence[Question]) -> list[str]:
    """The modules whose samples or record names a set of questions shows."""
    out: list[str] = []
    for q in questions:
        names = [c.value for c in q.choices] if q.kind == KIND_SLOT else [q.module]
        for name in names:
            if name and name not in out:
                out.append(name)
    return out


def with_samples(inputs: Inputs, samples: Mapping[str, Mapping[str, Sequence[str]]],
                 names: Mapping[str, Sequence[str]]) -> Inputs:
    """*inputs* with the masked samples and record names of the read."""
    return replace(inputs, samples=samples, names=names)


# ── An answer ────────────────────────────────────────────────────────────────


def answer_problem(q: Question, value: Any, src: Any = None) -> str | None:
    """Why *value* is not an answer of *q*, or ``None`` (§18.14.6,
    "Errors"). ``None`` is "None of these", legal for ``purpose``,
    ``key_field`` and ``slot``. A list (CRM-Q32-9) answers only the
    ``amount`` of a ``key_field``."""
    if isinstance(value, list) and q.kind != KIND_KEY:
        return "bad_answer"
    if q.kind == KIND_PURPOSE:
        return None if value is None or value in cat.PURPOSES else "bad_answer"
    if q.kind == KIND_KEY:
        if value is None:
            return None
        if isinstance(value, list):
            # CRM-Q32-9: only an amount takes a sum of currency fields.
            legal = q.part == cat.SUM_PART and not sum_problem(src, value)
            return None if legal else "bad_answer"
        if src is None or not isinstance(value, str) or not value or value.startswith("@"):
            return "bad_answer"
        legal = {f.key for f in part_fields(src, q.part or "")}
        return None if value in legal else "bad_answer"
    if q.kind == KIND_SLOT:
        return None if value is None or value in {c.value for c in q.choices} else "bad_answer"
    if q.kind == KIND_STAGE:
        return None if value in STAGE_WORDS else "bad_answer"
    if q.kind == KIND_REVENUE:
        return None if value in cat.REVENUE_VALUES else "bad_answer"
    if q.kind == KIND_TERM:
        return None if value in {c.value for c in q.choices} else "bad_answer"
    return "bad_answer"


def answer_item(q: Question, value: Any) -> dict[str, Any]:
    """The ``catalogue`` item of an answer of a ``purpose``, a
    ``key_field`` or ``revenue``, with ``by: admin``. "None of these" on a
    purpose stores ``other``, and on a key part stores ``null``."""
    if q.kind == KIND_REVENUE:
        return {"kind": cat.KIND_CATALOGUE, "module": cat.ORG_MODULE, "revenue": value,
                "by": {"revenue": "admin"}}
    if q.kind == KIND_PURPOSE:
        return {"kind": cat.KIND_CATALOGUE, "module": q.module,
                "purpose": value if value is not None else "other", "by": {"purpose": "admin"}}
    if q.kind == KIND_KEY:
        return {"kind": cat.KIND_CATALOGUE, "module": q.module,
                str(q.part): list(value) if isinstance(value, list) else value,
                "by": {str(q.part): "admin"}}
    raise ValueError(f"no catalogue item for {q.kind}")


def batch_joins(q: Question) -> bool:
    """Only a ``purpose`` or a ``key_field`` with a rank from Jev joins a
    batch. A ``slot`` or a ``stage_value`` changes what records mean, and
    ``revenue`` has a default (§18.14.6)."""
    return q.kind in (KIND_PURPOSE, KIND_KEY) and q.best() is not None


def article(word: str) -> str:
    """"an" before a word that starts with a vowel sound, else "a". Each
    purpose label starts with a letter whose sound its spelling gives."""
    return "an" if word[:1].lower() in "aeiou" else "a"


def applied_line(q: Question, choice: Choice) -> str:
    """What changes when *choice* answers *q*, for the dry run and the
    toast: "Totals of Collection now use IP Amount." """
    if q.kind == KIND_PURPOSE:
        noun = choice.label.lower()
        return f"{q.label} is now {article(noun)} {noun} module."
    if q.part == "amount":
        return f"Totals of {q.label} now use {choice.label}."
    if q.part == "date":
        return f"Totals of {q.label} by month now use {choice.label}."
    if q.part == "status":
        return f"Counts of {q.label} by status now use {choice.label}."
    return f"Totals of {q.label} by customer now use {choice.label}."


def stage_for(stage: Any, q_id: str) -> str | None:
    """The value of the ``stage`` item *stage* whose question id is
    *q_id*, or ``None``. An Undo of a stage answer finds its value so."""
    values = stage.get("values") if isinstance(stage, Mapping) else None
    slot = stage.get("slot") if isinstance(stage, Mapping) else None
    for value in (values or {}):
        if stage_id(str(slot), str(value)) == q_id:
            return str(value)
    return None


# ── The chat asks first, and a member proposes (CRM-Q32-8) ──────────────────
#
# Owner direction, 2026-10-18. A CRM tool whose answer depends on an open
# question shows the member a card before the final answer. An admin's
# choice is the answer, through the one answer route. Each other member's
# choice is a PROPOSAL: it changes that member's answer only, for an open
# question, and waits for an admin. The proposals live in the cursor row
# ``zoho:questions`` under ``proposals`` (no table, no migration). Only this
# module and ``routes/crm/questions.py`` read them, and a scan fences it.

#: The bucket of the proposals in :data:`QUESTIONS_ROW`: question id → a
#: list of ``{"value", "member", "at"}``.
PROPOSALS = "proposals"
#: The top-level key that only the questions step writes: the sorted
#: ``qid|member|at`` of each proposal it saw.
PROPOSALS_SEEN = "proposals_seen"
#: The proposals that one question keeps, the newest.
PROPOSALS_KEEP = 5

#: The kinds that a chat card asks. A ``slot`` or a ``stage_value`` changes
#: what records mean, so it goes through the approve path with a preview in
#: the CRM app, never through a chat reply (§18.14.6).
CHAT_KINDS: tuple[str, ...] = (KIND_PURPOSE, KIND_KEY, KIND_REVENUE, KIND_TERM)

#: The keys of the two options that each card adds.
NONE_KEY = "none"
DEFAULT_KEY = "@default"

#: What a card names a member with no name in the directory. Never the email.
A_MEMBER = "A member"

#: The line of a member's choice on a part in use (§18.14.10, CRM-Q32-8).
CHANGE_WAITS = "Your suggested change is waiting for a CRM admin."


def _proposal(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, Mapping) or not isinstance(raw.get("member"), str):
        return None
    value = raw.get("value")
    if value is not None and not isinstance(value, str):
        return None
    return {"value": value, "member": str(raw["member"]).lower(), "at": str(raw.get("at") or "")}


def proposals_of(state: Mapping[str, Any] | None) -> dict[str, list[dict[str, Any]]]:
    """The proposals in the state of :data:`QUESTIONS_ROW`: question id → its
    proposals, oldest first. An entry that does not parse is left out."""
    raw = (state or {}).get(PROPOSALS)
    out: dict[str, list[dict[str, Any]]] = {}
    for qid, entries in (raw.items() if isinstance(raw, Mapping) else ()):
        kept = [p for p in (_proposal(e) for e in entries or ()) if p is not None] \
            if isinstance(entries, list) else []
        if kept:
            out[str(qid)] = kept
    return out


def with_proposal(entries: Sequence[Mapping[str, Any]], value: str | None, member: str,
                  at: str) -> list[dict[str, Any]]:
    """*entries* with the proposal of *member*. A second proposal of one
    member replaces the first. The list keeps the :data:`PROPOSALS_KEEP`
    newest, oldest first."""
    me = member.lower()
    kept = [dict(p) for p in (_proposal(e) for e in entries) if p is not None
            and p["member"] != me]
    kept.append({"value": value, "member": me, "at": at})
    kept.sort(key=lambda p: p["at"])
    return kept[-PROPOSALS_KEEP:]


def live_proposals(entries: Sequence[Mapping[str, Any]], q: Question) -> list[dict[str, Any]]:
    """The proposals of *q* that still ask for a change. On a part in use,
    one that equals the answer in use drops. A sum in use (a tuple of keys)
    equals no proposal. On an open question each proposal is live."""
    if q.reason != REASON_ANSWERED or isinstance(q.current, tuple):
        return [dict(p) for p in entries]
    return [dict(p) for p in entries if p.get("value") != q.current]


def equals_current(q: Question, value: str | None) -> bool:
    """True when *value* is the answer in use of *q*, a part in use."""
    return q.reason == REASON_ANSWERED and not isinstance(q.current, tuple) \
        and value == q.current


def proposal_keys(state: Mapping[str, Any] | None) -> list[str]:
    """The sorted ``qid|member|at`` of each proposal in *state*, for
    :data:`PROPOSALS_SEEN`."""
    return sorted(f"{qid}|{p['member']}|{p['at']}"
                  for qid, entries in proposals_of(state).items() for p in entries)


def my_proposal(entries: Sequence[Mapping[str, Any]], member: str) -> dict[str, Any] | None:
    """The proposal of *member* in *entries*, or ``None``."""
    me = member.lower()
    return next((dict(p) for p in entries if str(p.get("member") or "").lower() == me), None)


def value_label(q: Question, value: str | None) -> str:
    """The label of a value of *q*: a field, a purpose, Billed or Collected,
    or "None of these"."""
    if value is None:
        return "None of these"
    if q.kind == KIND_PURPOSE:
        return PURPOSE_LABELS.get(value, value)
    found = next((c for c in (*q.choices, *q.options) if c.value == value), None)
    return found.label if found is not None else value


def chat_choice(q: Question, key: str) -> tuple[bool, str | None]:
    """The value of the option *key* of a card of *q*: ``(True, value)``,
    or ``(False, None)`` when no option has the key. ``none`` is "None of
    these" (``None``), legal where the question takes it. A field, a purpose
    or a revenue word must be a choice or an option of the question, which
    the server builds, so the model cannot invent a value. ``@default`` and
    ``@auto`` are not values: the route takes them first."""
    if key == NONE_KEY:
        return (True, None) if q.none else (False, None)
    if not key or key.startswith("@"):
        return False, None
    legal = {c.value for c in (*q.choices, *q.options)}
    return (True, key) if key in legal else (False, None)


def ask_sentence(q: Question) -> str:
    """The question of a card as a full sentence (the owner's copy rule of
    2026-10-18, the words of ``lib/questions.ts``). It never names the
    engine."""
    if q.kind == KIND_REVENUE:
        return "When you ask about revenue, what should Metorite count?"
    if q.kind == KIND_PURPOSE:
        return f"What kind of records does {q.label} hold?"
    if q.kind == KIND_TERM:
        return f'When you say "{q.value}", which field of {q.label} do you mean?'
    if q.part == "amount":
        return f"Which field holds the amount of each record in {q.label}?"
    if q.part == "date":
        return f"Which date tells when each record in {q.label} happened?"
    if q.part == "status":
        return f"Which field shows the status of each record in {q.label}?"
    return f"Which field links each record in {q.label} to its customer?"


def ask_header(q: Question) -> str:
    """The short title of a card: "Collection: amount"."""
    if q.kind == KIND_REVENUE:
        return "Revenue"
    if q.kind == KIND_TERM:
        return f"{q.label}: {q.value}"[:50]
    word = "customer" if q.part == "parent" else (q.part or "purpose")
    return f"{q.label}: {word}"


def override_of(q: Question, value: str | None, modules: Mapping[str, str]) -> dict[str, Any]:
    """What a member's choice changes in that member's own answer, for an
    open question: the real field key of a key part, the purpose word, or
    the modules of a meaning of revenue. It writes nothing. *modules* maps
    each module of the catalogue to its purpose.

    ⚠️ A key part of ``amount`` reads as ``measure='sum'`` with
    ``field=<key>``, never ``sum_amount``, because ``sum_amount`` on an
    overlay does not read ``field`` (``aggregate.py``)."""
    if q.kind == KIND_REVENUE:
        return {"revenue": value, "label": value_label(q, value),
                "modules": sorted(m for m, p in modules.items() if p == value)}
    if q.kind == KIND_PURPOSE:
        return {"purpose": value, "label": value_label(q, value)}
    if q.kind == KIND_TERM:
        return {"term": q.value, "field": value, "label": value_label(q, value),
                "measure": "sum"}
    out: dict[str, Any] = {"part": q.part, "word": f"@{q.part}", "field": value,
                           "label": value_label(q, value)}
    if q.part == "amount" and value is not None:
        out["measure"] = "sum"
    return out


def dismissed_of(state: Mapping[str, Any] | None) -> dict[str, str | None]:
    """The dismissed questions in the state of :data:`QUESTIONS_ROW`: id to
    the hash it was dismissed on. An entry that does not parse is left out."""
    raw = (state or {}).get("dismissed")
    out: dict[str, str | None] = {}
    for qid, entry in (raw.items() if isinstance(raw, Mapping) else ()):
        if isinstance(entry, Mapping):
            schema = entry.get("schema")
            out[str(qid)] = str(schema) if schema else None
    return out


def step_state(state: Mapping[str, Any] | None, open_ids: Sequence[str], money: bool,
               now: datetime) -> dict[str, Any]:
    """The keys that the questions step writes after a sync run (§18.14.5):
    ``seen``, the open ids, and ``money``. ``new_at`` changes only when an
    id is new, so an answer that only closes questions sets no ``new_at``.

    CRM-Q32-8: it also writes ``proposals_seen``, the sorted
    ``qid|member|at`` of each proposal now. A proposal that is not in the
    list before is new: it sets ``new_at`` too, and ``proposed`` counts the
    new ones. Each write of ``new_at`` writes ``new`` and ``proposed``
    both, so the card never reads the count of an older step. It never
    returns ``proposals``: only the reply route writes them."""
    seen = (state or {}).get("seen")
    before = {str(i) for i in seen} if isinstance(seen, list) else set()
    ids = sorted(set(open_ids))
    keys = proposal_keys(state)
    seen_before = (state or {}).get(PROPOSALS_SEEN)
    known = {str(k) for k in seen_before} if isinstance(seen_before, list) else set()
    out: dict[str, Any] = {"seen": ids, "money": bool(money), PROPOSALS_SEEN: keys}
    fresh = set(ids) - before
    proposed = len(set(keys) - known)
    if fresh or proposed:
        out["new_at"] = now.astimezone(UTC).isoformat()
        out["new"] = len(fresh)
        out["proposed"] = proposed
    return out


#: The card of My Day shows while ``new_at`` is less than this old.
CARD_HOURS = 24


def seen_keys(open_questions: Sequence[Question]) -> dict[str, Any]:
    """The keys that an answer, a dismissal, its Undo or a batch writes in
    :data:`QUESTIONS_ROW`: ``seen`` and ``money`` of the open set NOW. It
    sets no ``new_at``, so an answer never raises a My Day card (§18.14.5,
    "No nag")."""
    return {"seen": sorted({q.id for q in open_questions}),
            "money": count_money(open_questions)}


def open_count(state: Mapping[str, Any] | None) -> int | None:
    """The open count of the last questions step, from the state of
    :data:`QUESTIONS_ROW`, or ``None`` before the first step."""
    seen = (state or {}).get("seen")
    return len(seen) if isinstance(seen, list) else None


def _count(raw: Any) -> int | None:
    return raw if isinstance(raw, int) and not isinstance(raw, bool) and raw >= 0 else None


def card_of(state: Mapping[str, Any] | None, now: datetime) -> dict[str, Any] | None:
    """The My Day card of the state of :data:`QUESTIONS_ROW`, or ``None``
    (§18.14.5): ``new_at`` is less than :data:`CARD_HOURS` old, and a
    question is still open. It builds no question.

    CRM-Q32-8: new proposals raise the card too, also when no question is
    open, so a proposal on an answered part reaches the admin. ``proposed``
    is their count."""
    raw = (state or {}).get("new_at")
    try:
        at = datetime.fromisoformat(str(raw)) if raw else None
    except ValueError:
        at = None
    seen = (state or {}).get("seen")
    seen = seen if isinstance(seen, list) else []
    proposed = _count((state or {}).get("proposed")) or 0
    if at is None or (not seen and not proposed):
        return None
    if at.tzinfo is None:
        at = at.replace(tzinfo=UTC)
    age = now - at
    if age.total_seconds() < 0 or age.total_seconds() >= CARD_HOURS * 3600:
        return None
    new = _count((state or {}).get("new"))
    if new is None or (new == 0 and not proposed):
        # A row of a step before CRM-Q32-8 has no `new` of 0: the open count.
        new = len(seen)
    return {"new_at": at, "count": min(new, len(seen)), "open": len(seen),
            "money": (state or {}).get("money") is True, "proposed": proposed}


def wire(q: Question) -> dict[str, Any]:
    """The JSON of one question (§18.14.3, "The wire"). A ``slot`` choice
    has no ``fill``, and a ``stage_value`` question has ``count``."""
    def choice(c: Choice) -> dict[str, Any]:
        out: dict[str, Any] = {"value": c.value, "label": c.label, "best": c.best}
        if q.kind == KIND_TERM:
            out["fill"] = c.fill
        if q.kind == KIND_KEY:
            out["fill"] = c.fill
            out["samples"] = list(c.samples)
            if c.evidence:
                # CRM-Q33 part 5: "Includes GST", "Excludes GST", "After TDS".
                out["evidence"] = evidence_labels(c.evidence)
        if q.kind == KIND_SLOT:
            out["records"] = c.records
            out["samples"] = list(c.samples)
        if q.kind == KIND_REVENUE:
            out["modules"] = list(c.modules)
        return out

    body: dict[str, Any] = {
        "id": q.id, "kind": q.kind, "module": q.module, "label": q.label, "part": q.part,
        "reason": q.reason, "records": q.records, "money": q.money, "why": q.why,
        "choices": [choice(c) for c in q.choices], "none": q.none, "other": q.other,
        "current": list(q.current) if isinstance(q.current, tuple) else q.current,
        "by": q.by, "dismissible": q.hash is not None,
    }
    if q.sums:
        body["sums"] = [{"value": c.value, "label": c.label, "fill": c.fill} for c in q.sums]
    if q.kind == KIND_STAGE:
        body.update(count=q.count, slot=q.slot, value=q.value, field=q.field)
    if q.kind == KIND_TERM:
        body["value"] = q.value
    if q.kind == KIND_SLOT:
        body["slot"] = q.slot
    if q.other:
        body["options"] = [{"value": c.value, "label": c.label, **(
            {"fill": c.fill} if q.kind == KIND_KEY else {}), **(
            {"evidence": evidence_labels(c.evidence)} if c.evidence else {})}
            for c in q.options]
    if q.suggested_sum:
        body["suggested_sum"] = list(q.suggested_sum)
    return body


# ── Answer first, clarify over time (CRM-Q33, §18.15) ───────────────────────
#
# Owner direction, 2026-10-18. A total that depends on an open question never
# blocks: the chat answers with the AI's best assumption, says how it got the
# number, and asks ONE natural question after it. The pieces below are pure:
# the evidence of GST and TDS of each money field (part 5), what a business
# rule says (part 3), and the assumption of an answer (part 1). The route
# reads the counts in SQL (``field_sql.gst_check``) and calls them.

#: The evidence words of a money field, and what a card shows for each.
GST_INCLUDES = "includes_gst"
GST_EXCLUDES = "excludes_gst"
TDS_AFTER = "after_tds"
GST_ONLY = "gst_amount"
EVIDENCE_WORDS: Mapping[str, str] = {
    GST_INCLUDES: "Includes GST", GST_EXCLUDES: "Excludes GST", TDS_AFTER: "After TDS",
    GST_ONLY: "The GST alone",
}

#: The rate that a field with GST holds over its basic field (18%).
GST_RATE = 1.18
#: The share of the records that set both fields, on which a relation must
#: hold to count, and the fewest such records.
EVIDENCE_SHARE = 0.9
EVIDENCE_MIN_ROWS = 5
#: The most money fields that one check reads, and the most GST and TDS
#: fields beside them. One statement holds at most 6 x 5 x 3 x 4 relations.
EVIDENCE_FIELDS = 6
SIBLING_FIELDS = 3

_GST_WORD = re.compile(r"\bgst\b", re.IGNORECASE)
_WITH_GST = re.compile(r"\+\s*gst|\bincl\w*\.?\s+(of\s+)?gst|\bwith\s+gst", re.IGNORECASE)
_BASIC_WORD = re.compile(r"\bbasic\b", re.IGNORECASE)
_NET_WORD = re.compile(r"\bnet\b", re.IGNORECASE)
_TDS_WORD = re.compile(r"\btds\b", re.IGNORECASE)

#: The words of a business rule that asks for amounts net of GST.
_NET_OF_GST = re.compile(
    r"\b(net|exclusive|excluding|exclude|excludes|ex)\s+(of\s+)?gst\b"
    r"|\bwithout\s+gst\b|\bbefore\s+gst\b|\bgst\s+(excluded|exclusive)\b",
    re.IGNORECASE,
)


def evidence_labels(words: Sequence[str]) -> list[str]:
    """What a card shows for *words*, in a fixed order."""
    return [EVIDENCE_WORDS[w] for w in EVIDENCE_WORDS if w in words]


def net_of_gst_rule(rules: Sequence[str]) -> str | None:
    """The first rule of *rules* that asks for amounts net of GST, or None."""
    return next((r for r in rules if _NET_OF_GST.search(str(r or ""))), None)


def _money_fields(src: Any) -> list[Any]:
    return [f for f in getattr(src, "fields", ()) if f.type in cat.PART_TYPES[cat.SUM_PART]
            and f.type != "percent"]


def gst_siblings(src: Any) -> tuple[list[Any], list[Any]]:
    """The fields of *src* that hold the GST alone, and the fields of TDS,
    by their labels, the most filled first, at most :data:`SIBLING_FIELDS`
    of each. "IP GST" holds the GST alone. "IP (Basic + GST)" holds an
    amount with GST, so it is no sibling."""
    def by_fill(f: Any) -> tuple[float, str]:
        return (-(_fill(src, f.key) or 0.0), f.key)

    money = _money_fields(src)
    gst = sorted((f for f in money if _GST_WORD.search(f.label) and not _WITH_GST.search(f.label)
                  and not _BASIC_WORD.search(f.label)), key=by_fill)
    tds = sorted((f for f in money if _TDS_WORD.search(f.label)), key=by_fill)
    return gst[:SIBLING_FIELDS], tds[:SIBLING_FIELDS]


def evidence_candidates(src: Any) -> list[Any]:
    """The money fields whose evidence one check reads: each currency or
    number field that is no GST or TDS sibling, the most filled first, at
    most :data:`EVIDENCE_FIELDS`."""
    gst, tds = gst_siblings(src)
    taken = {f.key for f in (*gst, *tds)}
    found = [f for f in _money_fields(src) if f.key not in taken and (_fill(src, f.key) or 0) > 0]
    found.sort(key=lambda f: (-(_fill(src, f.key) or 0.0), f.key))
    return found[:EVIDENCE_FIELDS]


@dataclass(frozen=True)
class Relation:
    """One relation that the check counts. ``ratio``: ``field`` is ``basic``
    times :data:`GST_RATE`. ``sum``: ``field`` is ``basic`` plus ``gst``,
    less ``tds`` when it is set."""

    kind: str
    field: str
    basic: str
    gst: str | None = None
    tds: str | None = None


def relations(cands: Sequence[Any], gst: Sequence[Any], tds: Sequence[Any]) -> list[Relation]:
    """Each relation of one check, in a fixed order, so the SQL and the
    reading agree on each index."""
    keys = [f.key for f in cands]
    out: list[Relation] = []
    for f in keys:
        for b in keys:
            if b == f:
                continue
            out.append(Relation("ratio", f, b))
            for g in gst:
                out.append(Relation("sum", f, b, g.key))
                for t in tds:
                    out.append(Relation("sum", f, b, g.key, t.key))
    return out


@dataclass(frozen=True)
class RelationCount:
    """What the check counted for one relation: the records where it can
    hold (``rows``), the records where it holds (``held``), and for a sum
    with TDS, the held records whose TDS is not 0 (``tds_rows``)."""

    rows: int
    held: int
    tds_rows: int = 0


def _holds(c: RelationCount) -> bool:
    return c.rows >= EVIDENCE_MIN_ROWS and c.held >= EVIDENCE_SHARE * c.rows


def label_evidence(label: str) -> tuple[str, ...]:
    """The evidence that a label alone gives: "+ GST" includes GST, "Basic"
    excludes it, "Net" is after TDS, and a label of the GST alone."""
    out: list[str] = []
    if _WITH_GST.search(label):
        out.append(GST_INCLUDES)
    elif _BASIC_WORD.search(label):
        out.append(GST_EXCLUDES)
    elif _GST_WORD.search(label):
        out.append(GST_ONLY)
    if _NET_WORD.search(label) and GST_ONLY not in out:
        out.append(TDS_AFTER)
    return tuple(out)


def money_evidence(src: Any, rels: Sequence[Relation],
                   counts: Sequence[RelationCount]) -> dict[str, tuple[str, ...]]:
    """The evidence of each money field of *src*: the arithmetic of
    :func:`relations` first, then the words of its label (CRM-Q33 part 5).

    A field that is its basic times 1.18, or its basic plus a GST field, on
    90% or more of the records that set both, includes GST, and that basic
    excludes it. A sum that holds with a TDS field that is not 0 is after
    TDS. A field with no arithmetic takes the words of its label."""
    found: dict[str, set[str]] = {}
    for rel, count in zip(rels, counts, strict=False):
        if not _holds(count):
            continue
        found.setdefault(rel.field, set()).add(GST_INCLUDES)
        found.setdefault(rel.basic, set()).add(GST_EXCLUDES)
        if rel.tds is not None and count.tds_rows > 0:
            found[rel.field].add(TDS_AFTER)
    out: dict[str, tuple[str, ...]] = {}
    for f in _money_fields(src):
        words = found.get(f.key)
        if words and GST_INCLUDES in words:
            words.discard(GST_EXCLUDES)
        if not words:
            words = set(label_evidence(f.label))
        elif _NET_WORD.search(f.label) and GST_INCLUDES in words:
            # "Rent Net Amount" holds GST and is net of a deduction.
            words.add(TDS_AFTER)
        if words:
            out[f.key] = tuple(w for w in EVIDENCE_WORDS if w in words)
    return out


def steered(q: Question, src: Any, evidence: Mapping[str, Sequence[str]],
            rule: str | None, inputs: Inputs | None = None) -> Question:
    """*q* with the evidence on each money choice and option (CRM-Q33 part
    5). With a rule that asks for amounts net of GST, the fields that
    exclude GST come first, the first of them is the recommendation, and
    no field with GST stays on the short list. When two or more of them
    each fill :data:`SUM_FILL_FLOOR` and none fills 90%, their sum is the
    suggestion: DEWiN's Collection splits each payment into parts."""
    if q.kind != KIND_KEY or q.part != cat.SUM_PART or src is None:
        return q

    def ev(c: Choice) -> Choice:
        return replace(c, evidence=tuple(evidence.get(c.value, ())))

    choices = [ev(c) for c in q.choices]
    options = tuple(ev(c) for c in q.options)
    suggested: tuple[str, ...] = ()
    if rule is not None:
        ex = [f for f in part_fields(src, cat.SUM_PART)
              if GST_EXCLUDES in evidence.get(f.key, ()) and (_fill(src, f.key) or 0) > 0]
        ex.sort(key=lambda f: (-(_fill(src, f.key) or 0.0), f.key))
        if ex:
            shown = inputs or Inputs(entries=(), sources={})
            lead = [ev(_field_choice(shown, src, f)) for f in ex]
            lead = [next((c for c in choices if c.value == x.value), x) for x in lead]
            keys = {c.value for c in lead}
            rest = [c for c in choices if c.value not in keys
                    and GST_INCLUDES not in c.evidence and GST_ONLY not in c.evidence]
            # Each field that excludes GST joins the short list, so the card
            # offers all of them (the DEWiN case has three).
            picked = [*lead, *rest][:max(CHOICE_LIMIT, len(lead))][:cat.SUM_MAX]
            choices = [replace(c, best=(i == 0)) for i, c in enumerate(picked)]
            wide = [f for f in ex if (_fill(src, f.key) or 0) >= SUM_FILL_FLOOR]
            if (len(wide) >= cat.SUM_MIN
                    and not any((_fill(src, f.key) or 0) >= EVIDENCE_SHARE for f in wide)
                    and all(f.type in cat.SUM_TYPES for f in wide)):
                suggested = tuple(f.key for f in wide[:cat.SUM_MAX])
    return replace(q, choices=tuple(choices), options=options, suggested_sum=suggested)


def _lower_first(word: str) -> str:
    """*word* with its first letter in lower case: "Excludes GST" reads
    "excludes GST" inside a sentence, and GST keeps its capitals."""
    return word[:1].lower() + word[1:]


def _how(label: str, words: Sequence[str]) -> str:
    shown = [_lower_first(EVIDENCE_WORDS[w]) for w in EVIDENCE_WORDS if w in words]
    return f"{label} ({', '.join(shown)})" if shown else label


def assumption_of(q: Question, rule: str | None,
                  evidence: Mapping[str, Sequence[str]] | None = None,
                  before: Question | None = None) -> dict[str, Any] | None:
    """The best assumption of an answer that depends on the open question
    *q* (CRM-Q33 part 1), or None when the question has no field to use.

    The order: the sum that a business rule points at, the recommended
    choice, then the most filled choice. The alternative is the AI's own
    recommendation of *before* (the question before a rule moved it), else
    the first other choice, for one more total when it is cheap. Each label
    carries its evidence, so the chat says what the number includes.
    ``fields`` are real field keys, which the aggregate reads as one field
    or as one sum."""
    ev = evidence or {}
    if q.kind == KIND_REVENUE:
        return {"fields": [], "value": cat.DEFAULT_REVENUE, "label": "billed revenue",
                "how": "the amounts that you billed",
                "why": "revenue means what you billed, the default", "alternative": None}
    if q.kind != KIND_KEY or not q.choices:
        return None
    earlier = before.choices if before is not None else ()
    labels = {c.value: c.label for c in (*earlier, *q.choices, *q.options)}
    best = q.best()
    if q.suggested_sum:
        fields = list(q.suggested_sum)
        why = f'the business rule "{rule}"' if rule else "the fields that exclude GST"
    elif best is not None:
        fields = [best.value]
        why = (f'the business rule "{rule}"' if rule
               else "the field that the AI found most likely")
    else:
        fields = [q.choices[0].value]
        why = "the field with the most values"
    how = " + ".join(_how(labels.get(k, k), ev.get(k, ())) for k in fields)
    label = names([labels.get(k, k) for k in fields])
    ai_best = before.best() if before is not None else None
    alt = (ai_best if ai_best is not None and ai_best.value not in fields
           else next((c for c in q.choices if c.value not in fields), None))
    alternative = ({"field": alt.value, "label": alt.label,
                    "how": _how(alt.label, ev.get(alt.value, ()))} if alt is not None else None)
    return {"fields": fields, "label": label, "how": how, "why": why,
            "alternative": alternative}


__all__ = [
    "AUTO",
    "A_MEMBER",
    "CHANGE_WAITS",
    "CHAT_KINDS",
    "CHOICE_LIMIT",
    "DEFAULT_KEY",
    "EVIDENCE_WORDS",
    "GST_EXCLUDES",
    "GST_INCLUDES",
    "GST_ONLY",
    "KINDS",
    "KIND_KEY",
    "KIND_PURPOSE",
    "KIND_REVENUE",
    "KIND_SLOT",
    "KIND_STAGE",
    "KIND_TERM",
    "NONE_KEY",
    "PROPOSALS",
    "PROPOSALS_KEEP",
    "PROPOSALS_SEEN",
    "QUESTIONS_ROW",
    "REVENUE_ID",
    "SHOWN",
    "STAGE_WORDS",
    "SUM_CHOICES",
    "SUM_EQUAL_SHARE",
    "SUM_FILL_FLOOR",
    "TDS_AFTER",
    "Choice",
    "Inputs",
    "Question",
    "Relation",
    "RelationCount",
    "SlotState",
    "StageState",
    "SumReport",
    "answer_item",
    "answer_problem",
    "answered",
    "applied_line",
    "article",
    "ask_header",
    "ask_sentence",
    "assumption_of",
    "batch_joins",
    "build",
    "candidates",
    "card_of",
    "chat_choice",
    "count_money",
    "dismissed",
    "dismissed_of",
    "equals_current",
    "evidence_candidates",
    "evidence_labels",
    "gst_siblings",
    "key_id",
    "label_evidence",
    "live_proposals",
    "module_hash",
    "module_questions",
    "money_evidence",
    "my_proposal",
    "names",
    "net_of_gst_rule",
    "open_count",
    "options_hash",
    "override_of",
    "part_fields",
    "part_reasons",
    "proposal_keys",
    "proposals_of",
    "purpose_id",
    "rank",
    "relations",
    "revenue_basis",
    "revenue_question",
    "sampled_modules",
    "seen_keys",
    "shows_open",
    "slot_candidates",
    "slot_id",
    "slots_hash",
    "stage_for",
    "stage_id",
    "steered",
    "step_state",
    "sum_choice",
    "sum_fields",
    "sum_problem",
    "sum_report",
    "term_question",
    "term_questions",
    "value_hash",
    "value_label",
    "why_key",
    "wire",
    "with_proposal",
    "with_samples",
]
