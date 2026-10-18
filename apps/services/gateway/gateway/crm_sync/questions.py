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
KINDS: tuple[str, ...] = (KIND_PURPOSE, KIND_KEY, KIND_SLOT, KIND_STAGE, KIND_REVENUE)

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
    current: str | None = None
    by: str | None = None
    count: int | None = None
    slot: str | None = None
    value: str | None = None
    field: str | None = None
    #: The hash that a dismissal records. ``None`` takes no dismissal.
    hash: str | None = None
    #: Each choice of "Pick another", for ``purpose`` and ``key_field``.
    options: tuple[Choice, ...] = ()

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


def _current_part(entry: Any, part: str) -> str | None:
    if part == "purpose":
        return entry.purpose
    found = entry.fields.get(part)
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
    if q.kind == KIND_KEY or (q.kind in (KIND_SLOT, KIND_STAGE) and q.slot == "deal"):
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


def build(inputs: Inputs) -> list[Question]:
    """The open questions of the org, ranked (§18.14.7)."""
    found: list[Question] = list(module_questions(inputs))
    found += _slot_questions(inputs)
    found += _stage_questions(inputs)
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
    ``key_field`` and ``slot``."""
    if q.kind == KIND_PURPOSE:
        return None if value is None or value in cat.PURPOSES else "bad_answer"
    if q.kind == KIND_KEY:
        if value is None:
            return None
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
        return {"kind": cat.KIND_CATALOGUE, "module": q.module, str(q.part): value,
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
    id is new, so an answer that only closes questions sets no ``new_at``."""
    seen = (state or {}).get("seen")
    before = {str(i) for i in seen} if isinstance(seen, list) else set()
    ids = sorted(set(open_ids))
    out: dict[str, Any] = {"seen": ids, "money": bool(money)}
    fresh = set(ids) - before
    if fresh:
        out["new_at"] = now.astimezone(UTC).isoformat()
        out["new"] = len(fresh)
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


def card_of(state: Mapping[str, Any] | None, now: datetime) -> dict[str, Any] | None:
    """The My Day card of the state of :data:`QUESTIONS_ROW`, or ``None``
    (§18.14.5): ``new_at`` is less than :data:`CARD_HOURS` old, and a
    question is still open. It builds no question."""
    raw = (state or {}).get("new_at")
    try:
        at = datetime.fromisoformat(str(raw)) if raw else None
    except ValueError:
        at = None
    seen = (state or {}).get("seen")
    if at is None or not isinstance(seen, list) or not seen:
        return None
    if at.tzinfo is None:
        at = at.replace(tzinfo=UTC)
    age = now - at
    if age.total_seconds() < 0 or age.total_seconds() >= CARD_HOURS * 3600:
        return None
    new = (state or {}).get("new")
    count = new if isinstance(new, int) and not isinstance(new, bool) and new > 0 else len(seen)
    return {"new_at": at, "count": min(count, len(seen)), "open": len(seen),
            "money": (state or {}).get("money") is True}


def wire(q: Question) -> dict[str, Any]:
    """The JSON of one question (§18.14.3, "The wire"). A ``slot`` choice
    has no ``fill``, and a ``stage_value`` question has ``count``."""
    def choice(c: Choice) -> dict[str, Any]:
        out: dict[str, Any] = {"value": c.value, "label": c.label, "best": c.best}
        if q.kind == KIND_KEY:
            out["fill"] = c.fill
            out["samples"] = list(c.samples)
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
        "current": q.current, "by": q.by, "dismissible": q.hash is not None,
    }
    if q.kind == KIND_STAGE:
        body.update(count=q.count, slot=q.slot, value=q.value, field=q.field)
    if q.kind == KIND_SLOT:
        body["slot"] = q.slot
    if q.other:
        body["options"] = [{"value": c.value, "label": c.label, **(
            {"fill": c.fill} if q.kind == KIND_KEY else {})} for c in q.options]
    return body


__all__ = [
    "AUTO",
    "CHOICE_LIMIT",
    "KINDS",
    "KIND_KEY",
    "KIND_PURPOSE",
    "KIND_REVENUE",
    "KIND_SLOT",
    "KIND_STAGE",
    "QUESTIONS_ROW",
    "REVENUE_ID",
    "SHOWN",
    "STAGE_WORDS",
    "Choice",
    "Inputs",
    "Question",
    "SlotState",
    "StageState",
    "answer_item",
    "answer_problem",
    "answered",
    "applied_line",
    "article",
    "batch_joins",
    "build",
    "candidates",
    "card_of",
    "count_money",
    "dismissed",
    "dismissed_of",
    "key_id",
    "module_hash",
    "module_questions",
    "open_count",
    "options_hash",
    "part_fields",
    "part_reasons",
    "purpose_id",
    "rank",
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
    "step_state",
    "value_hash",
    "why_key",
    "wire",
    "with_samples",
]
