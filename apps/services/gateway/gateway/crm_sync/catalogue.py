"""The CRM catalogue by code (WS-53 CRM-Q31-1, ``crm_platform.md`` §18.13.4).

The catalogue tells the AI what each core entity and each mirrored module is
for, which field of it holds the amount, the date, the status and the
customer, and where its lookups point. This module holds the vocabulary and
the code rules 1 to 10 of §18.13.4 as PURE functions. It reads no database
and calls no model. ``routes/crm/catalogue.py`` reads the inputs and calls
:func:`build`. The mapping pipeline of CRM-Q31-3 calls the same functions, so
the two give the same answer.

**One entry for each core entity and each mirrored module.** It holds the
purpose (one word of :data:`PURPOSES`), the key fields, the links and the
record count. Only an entry with a purpose of :data:`MONEY_PURPOSES` gets the
four key parts ``amount``, ``date``, ``status`` and ``parent``.

**The order of the answers** (§18.13.4). For the purpose: the ``role`` item
of the approved plan, then a stored ``catalogue`` item, then code. For a key
part: a stored part, then code. A stored part whose field is gone, or has
another type, is left out and counted in ``stale``, and code answers it.

**None beats a wrong answer.** A part that code cannot answer stays empty. It
is in ``open`` when a later question can answer it, and a module with 0
records gets no question (rule 5).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

#: The fixed list of purposes, in this order (§18.13.4). They are the
#: options of each purpose question.
PURPOSES: tuple[str, ...] = (
    "customer_company", "customer_site", "person", "lead", "deal", "contract",
    "invoice", "payment_in", "receivable", "payment_out", "product", "asset",
    "vendor", "partner", "activity", "reference", "other",
)

#: The purposes that hold money or time. Only their entries get the four key
#: parts.
MONEY_PURPOSES: frozenset[str] = frozenset({
    "deal", "contract", "invoice", "payment_in", "receivable", "payment_out",
})

#: Code rule 2: a standard Zoho module takes its purpose from its API name.
STANDARD_PURPOSES: Mapping[str, str] = {
    "Invoices": "invoice",
    "Quotes": "deal",
    "Sales_Orders": "contract",
    "Purchase_Orders": "payment_out",
    "Products": "product",
    "Price_Books": "reference",
    "Solutions": "reference",
    "Vendors": "vendor",
    "Cases": "activity",
    "Campaigns": "other",
}

#: Code rule 3: a custom module whose WHOLE label or API name is one of these
#: names, singular or plural, takes the purpose. "Clients" matches, and
#: "Client Visits" does not (the rule of ``map_pipeline.SLOT_WORDS``).
PURPOSE_WORDS: Mapping[str, tuple[str, ...]] = {
    "customer_company": ("client", "customer"),
    "payment_in": ("payment received", "collection"),
    "payment_out": ("payment made", "expense", "bill"),
    "receivable": ("outstanding", "receivable"),
    "partner": ("partner",),
}

#: Code rule 1: the fixed purpose of each core entity, by its Zoho module.
CORE_PURPOSES: Mapping[str, str] = {
    "Leads": "lead",
    "Contacts": "person",
    "Accounts": "customer_company",
    "Deals": "deal",
}

#: Code rule 1: the fixed column of each key part of a core entity. Only
#: Deals has a money purpose.
CORE_KEY_COLUMNS: Mapping[str, Mapping[str, str]] = {
    "Deals": {
        "amount": "amount", "date": "expected_close_date",
        "status": "status_id", "parent": "company_id",
    },
}

#: Code rule 4: the purpose of each core slot that a module can play.
SLOT_PURPOSES: Mapping[str, str] = {
    "company": "customer_company",
    "contact": "person",
    "lead": "lead",
    "deal": "deal",
}

#: The four key parts of a money entry, in the order that a line shows them.
KEY_PARTS: tuple[str, ...] = ("amount", "date", "status", "parent")

#: The field types that each key part takes. ``amount`` is
#: ``field_sql.MEASURE_TYPES`` and ``date`` is ``field_sql.DATE_TYPES``
#: (``test_crm_catalogue.py`` holds them equal).
PART_TYPES: Mapping[str, tuple[str, ...]] = {
    "amount": ("number", "decimal", "currency", "percent"),
    "date": ("date", "datetime"),
    "status": ("select",),
    "parent": ("lookup",),
}

#: The kind of a catalogue item in ``crm_mapping_plans.rules`` (§18.13.4).
#: CRM-Q31-3 writes it. This slice only reads it.
KIND_CATALOGUE = "catalogue"

#: The words of ``by``. ``role`` marks a purpose that an approved ``role``
#: item gives.
BY_WORDS: frozenset[str] = frozenset({"code", "ai", "admin"})
BY_ROLE = "role"
BY_CODE = "code"

#: The share of records that a field must fill (rules 6, 7 and 8).
FILL_SURE = 0.5
#: The share that the one date field of rule 7, second half, must fill.
DATE_FILL_SURE = 0.9

#: Rule 6: the standard names of an amount, in the order that wins.
AMOUNT_NAMES: tuple[str, ...] = ("Grand_Total", "Amount", "Total", "Total_Amount")
#: Rule 8: the standard names of a status.
STATUS_NAMES: tuple[str, ...] = ("Status", "Stage")
#: Rule 7: the system times that never count as the date of a record.
SYSTEM_TIMES: frozenset[str] = frozenset({
    "Created_Time", "Modified_Time", "Last_Activity_Time", "Unsubscribed_Time",
})

#: Rule 9: the purposes of a parent, the stronger first.
PARENT_PURPOSES: tuple[frozenset[str], ...] = (
    frozenset({"customer_company"}),
    frozenset({"customer_site", "person"}),
)

#: CRM-Q31-2 item 7: the purposes that hold the records of each core entity,
#: by its API slug. A Mirror org whose core entity holds no record learns
#: which module holds them.
CORE_FITS: Mapping[str, frozenset[str]] = {
    "deals": frozenset({"deal", "contract", "invoice"}),
    "organizations": frozenset({"customer_company"}),
    "contacts": frozenset({"person", "customer_site"}),
    "leads": frozenset({"lead"}),
}


# ── The inputs and the entry ─────────────────────────────────────────────────


@dataclass(frozen=True)
class Field:
    """One field: its key, its label, its type and, for a lookup, the API
    name of the module it points at."""

    key: str
    label: str
    type: str
    target: str | None = None


@dataclass(frozen=True)
class Source:
    """What code knows of one core entity or one mirrored module.

    ``module`` is the source API name (``Contacts``, ``Invoices``). ``entity``
    is the word of the CRM: the API slug of a core entity (``contacts``) or
    ``module:<ApiName>``. ``fills`` maps a field key to its share of filled
    records. ``None`` means that the module has no fill row (rule 10).
    ``standard_label`` is False for a core entity whose source label is not
    its standard name (rule 1).
    """

    module: str
    entity: str
    kind: str
    label: str
    records: int = 0
    singular: str | None = None
    custom: bool = False
    standard_label: bool = True
    fields: tuple[Field, ...] = ()
    fills: Mapping[str, float] | None = None
    display_field: str | None = None

    def field(self, key: str | None) -> Field | None:
        return next((f for f in self.fields if f.key == key), None)


@dataclass
class Entry:
    """One entry of the catalogue (§18.13.4).

    ``fields`` maps a key part to its field. A part with ``None`` was asked,
    and no answer was sure. An absent part is not answered. ``open`` names
    each part that waits for a question. ``alike`` (a core entity only) names
    the label of each module whose purpose fits the entity and that holds
    records (CRM-Q31-2 item 7).
    """

    module: str
    entity: str
    kind: str
    label: str
    records: int
    purpose: str | None = None
    by: dict[str, str] = field(default_factory=dict)
    fields: dict[str, Field | None] = field(default_factory=dict)
    name: Field | None = None
    links: list[Field] = field(default_factory=list)
    open: list[str] = field(default_factory=list)
    stale: list[str] = field(default_factory=list)
    alike: list[str] = field(default_factory=list)


# ── The words of a name (the rule of map_pipeline.SLOT_WORDS) ───────────────


def _word(name: Any) -> str:
    """*name* in lower case, letters and digits only, in the singular. The
    two helpers of ``map_pipeline`` give it, so one rule names a module."""
    from gateway.crm_sync.map_pipeline import _singular, norm

    return _singular(norm(name))


def singulars(src: Source) -> set[str]:
    """The singular words of the module: its API name, its label and its
    singular label (``Invoices`` gives ``invoice``)."""
    from gateway.crm_sync.map_pipeline import norm

    words = {_word(src.module), _word(src.label)}
    if src.singular:
        words.add(norm(src.singular))
    return {w for w in words if w}


# ── Code rules 1 to 5: the purpose ───────────────────────────────────────────


def word_purpose(src: Source) -> str | None:
    """Rule 3: the one purpose whose word is the whole API name, label or
    singular label of *src*. Two purposes, or none, give ``None``."""
    names = {_word(src.module), _word(src.label)}
    if src.singular:
        names.add(_word(src.singular))
    found = {
        purpose for purpose, words in PURPOSE_WORDS.items()
        if names & {_word(w) for w in words}
    }
    return next(iter(found)) if len(found) == 1 else None


def code_purpose(src: Source) -> str | None:
    """Rules 1 to 3: the purpose that code gives *src*, or ``None``.

    A core entity keeps its fixed purpose while its source label is its
    standard name. A standard Zoho module takes the purpose of its API name.
    A custom module takes the purpose of its whole name.
    """
    if src.kind == "entity":
        return CORE_PURPOSES.get(src.module) if src.standard_label else None
    if not src.custom and src.module in STANDARD_PURPOSES:
        return STANDARD_PURPOSES[src.module]
    return word_purpose(src)


def role_purposes(roles: Mapping[str, str]) -> dict[str, str]:
    """Rule 4: the module of each approved ``role`` → the purpose of its
    slot. ``roles`` maps a slot to a module."""
    return {
        module: SLOT_PURPOSES[slot] for slot, module in roles.items()
        if slot in SLOT_PURPOSES and module
    }


# ── Code rules 6 to 10: the key parts of a money entry ──────────────────────


def _share(src: Source, key: str) -> float | None:
    if src.fills is None:
        return None
    share = src.fills.get(key)
    return float(share) if share is not None else None


def _filled(src: Source, f: Field, at_least: float) -> bool:
    share = _share(src, f.key)
    return share is not None and share >= at_least


def _of_type(src: Source, part: str) -> list[Field]:
    return [f for f in src.fields if f.type in PART_TYPES[part]]


def _one(found: Sequence[Field]) -> Field | None:
    return found[0] if len(found) == 1 else None


def code_amount(src: Source) -> Field | None:
    """Rule 6. A currency field with a standard name, filled 50% or more.
    Else the one currency field filled 50% or more. Rule 10: no fill row,
    no answer."""
    if src.fills is None:
        return None
    from gateway.crm_sync.map_pipeline import norm

    money = [f for f in src.fields if f.type == "currency"]
    names = [norm(n) for n in AMOUNT_NAMES] + [f"{s}amount" for s in sorted(singulars(src))]
    for name in names:
        hit = next((f for f in money if norm(f.key) == name
                    and _filled(src, f, FILL_SURE)), None)
        if hit is not None:
            return hit
    return _one([f for f in money if _filled(src, f, FILL_SURE)])


def code_date(src: Source) -> Field | None:
    """Rule 7. A date field named ``<Singular>_Date``, filled 50% or more.
    Else the one date or datetime field filled 90% or more. A system time
    never counts."""
    if src.fills is None:
        return None
    from gateway.crm_sync.map_pipeline import norm

    dates = [f for f in _of_type(src, "date") if f.key not in SYSTEM_TIMES]
    named = {f"{s}date" for s in singulars(src)}
    hit = _one([f for f in dates if norm(f.key) in named and _filled(src, f, FILL_SURE)])
    if hit is not None:
        return hit
    return _one([f for f in dates if _filled(src, f, DATE_FILL_SURE)])


def code_status(src: Source) -> Field | None:
    """Rule 8. The one select field named ``Status``, ``Stage``,
    ``<Singular>_Status`` or ``<Singular>_Stage``, filled 50% or more."""
    if src.fills is None:
        return None
    from gateway.crm_sync.map_pipeline import norm

    names = {norm(n) for n in STATUS_NAMES}
    for s in singulars(src):
        names |= {f"{s}status", f"{s}stage"}
    return _one([f for f in _of_type(src, "status")
                 if norm(f.key) in names and _filled(src, f, FILL_SURE)])


def code_parent(src: Source, purposes: Mapping[str, str | None]) -> Field | None:
    """Rule 9. The one lookup to a ``customer_company`` entry. Else the one
    lookup to a ``customer_site`` or a ``person`` entry. *purposes* maps the
    API name of each entry to its purpose. A parent needs no fill."""
    lookups = [f for f in _of_type(src, "parent") if f.target and f.target != src.module]
    for wanted in PARENT_PURPOSES:
        found = [f for f in lookups if purposes.get(f.target or "") in wanted]
        if found:
            return _one(found)
    return None


def code_parts(src: Source, purposes: Mapping[str, str | None]) -> dict[str, Field | None]:
    """The key parts that code gives a money entry. A core entity gives its
    fixed columns. A part that code cannot answer is ``None``."""
    columns = CORE_KEY_COLUMNS.get(src.module) if src.kind == "entity" else None
    if columns is not None:
        return {part: src.field(columns.get(part)) for part in KEY_PARTS}
    if src.kind == "entity":
        return dict.fromkeys(KEY_PARTS)
    return {
        "amount": code_amount(src),
        "date": code_date(src),
        "status": code_status(src),
        "parent": code_parent(src, purposes),
    }


# ── The decide tier (CRM-Q31-3) ──────────────────────────────────────────────

#: What each purpose means: the criteria of a purpose question, in the order
#: of :data:`PURPOSES` (§18.13.4, the fixed list).
PURPOSE_MEANINGS: Mapping[str, str] = {
    "customer_company": "A company that buys from the org.",
    "customer_site": "A site, a branch or a place of a customer.",
    "person": "A person at a company.",
    "lead": "A possible customer who is not qualified yet.",
    "deal": "A sale in progress: a deal, an opportunity, a quote or an offer.",
    "contract": "An agreement, an order or a subscription that runs for a time.",
    "invoice": "A bill that the org sends to a customer.",
    "payment_in": "Money that a customer paid to the org.",
    "receivable": "Money that a customer still owes to the org.",
    "payment_out": "Money that the org paid or must pay: a payment, an expense or a vendor bill.",
    "product": "A thing or a service that the org sells.",
    "asset": "Equipment or property that the org owns or runs.",
    "vendor": "A company that sells to the org.",
    "partner": "A company or a person that works with the org and is not a customer.",
    "activity": "A task, a call, a meeting or a visit.",
    "reference": "A list that other modules point to, such as the regions of the org.",
    "other": "None of these.",
}

#: What each key part means, for the state of a key-field question.
PART_MEANINGS: Mapping[str, str] = {
    "amount": "the main money field of a record",
    "date": "the main business date of a record",
    "status": "the field that holds the status or the stage of a record",
    "parent": "the lookup to the customer of a record",
}

#: The shadow answer key of the key fields (§18.13.4): the standard fields of
#: a standard module. Only a part whose field is filled 50% or more is asked.
SHADOW_KEYS: Mapping[str, Mapping[str, str]] = {
    "Invoices": {"amount": "Grand_Total", "date": "Invoice_Date", "status": "Status",
                 "parent": "Account_Name"},
    "Quotes": {"amount": "Grand_Total", "status": "Quote_Stage", "parent": "Account_Name"},
    "Sales_Orders": {"amount": "Grand_Total", "status": "Status", "parent": "Account_Name"},
    "Purchase_Orders": {"amount": "Grand_Total", "date": "PO_Date", "status": "Status",
                        "parent": "Vendor_Name"},
}


def is_catalogue(item: Any) -> bool:
    """True for an item of the kind ``catalogue``."""
    return isinstance(item, Mapping) and item.get("kind") == KIND_CATALOGUE


def without_catalogue(items: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """*items* less each ``catalogue`` item. A draft never holds one
    (§18.13.4, rule 2 of "Why the plan")."""
    return [dict(i) for i in items if not is_catalogue(i)]


def item_problem(item: Mapping[str, Any]) -> str | None:
    """Rule 4 of the kinds for one ``catalogue`` item, or ``None`` when it
    is legal. ``purpose`` is a word of :data:`PURPOSES` or ``null``. Each key
    part is a field API name or ``null``. ``by`` names ``code``, ``ai`` or
    ``admin`` for each part."""
    module = item.get("module")
    if not isinstance(module, str) or not module:
        return "module"
    if "purpose" in item and item["purpose"] is not None and item["purpose"] not in PURPOSES:
        return "purpose"
    for part in KEY_PARTS:
        value = item.get(part)
        if part in item and value is not None and (not isinstance(value, str) or not value):
            return part
    by = item.get("by", {})
    if not isinstance(by, Mapping):
        return "by"
    for part, word in by.items():
        if part not in ("purpose", *KEY_PARTS) or word not in BY_WORDS:
            return "by"
    return None


def merge_item(old: Mapping[str, Any] | None, new: Mapping[str, Any]) -> dict[str, Any]:
    """*new* put over *old*, the item of the same module, part by part. A
    part of *old* with ``by: admin`` stays, unless *new* is the admin's too:
    the pipeline never replaces the choice of an admin (§18.13.4)."""
    if old is None:
        return {**dict(new), "by": dict(new.get("by") or {})}
    out = dict(old)
    by = dict(old.get("by") or {}) if isinstance(old.get("by"), Mapping) else {}
    new_by = new.get("by") if isinstance(new.get("by"), Mapping) else {}
    for part in ("purpose", *KEY_PARTS):
        if part not in new:
            continue
        word = new_by.get(part, "ai")
        if by.get(part) == "admin" and word != "admin":
            continue
        out[part] = new[part]
        by[part] = word
    for key in ("profile_version",):
        if key in new:
            out[key] = new[key]
    out["by"] = by
    return out


# ── A stored catalogue item ──────────────────────────────────────────────────


def stored_items(items: Iterable[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    """The ``catalogue`` items of the approved plan, by module. A later item
    of one module wins, as ``merge_kinds`` keeps one."""
    out: dict[str, Mapping[str, Any]] = {}
    for item in items:
        if (isinstance(item, Mapping) and item.get("kind") == KIND_CATALOGUE
                and isinstance(item.get("module"), str) and item.get("module")):
            out[str(item["module"])] = item
    return out


def _stored_by(item: Mapping[str, Any], part: str) -> str:
    by = item.get("by")
    word = by.get(part) if isinstance(by, Mapping) else None
    return word if word in BY_WORDS else "ai"


def problem(src: Source, part: str, key: Any) -> str | None:
    """Why a stored key part is not legal now, or ``None``. A part names a
    field of *src* of the right type (rule 4 of the kinds), or ``None``."""
    if key is None:
        return None
    found = src.field(key) if isinstance(key, str) else None
    if found is None:
        return "gone"
    if found.type not in PART_TYPES[part]:
        return "type"
    return None


# ── The entry ────────────────────────────────────────────────────────────────


def _purpose(
    src: Source, played: Mapping[str, str], item: Mapping[str, Any] | None,
) -> tuple[str | None, str | None, bool]:
    """The purpose, its ``by`` and whether a stored purpose is not legal.
    *played* maps a module that plays a slot to the purpose of the slot."""
    if src.kind == "module" and src.module in played:
        return played[src.module], BY_ROLE, False
    if item is not None and "purpose" in item:
        value = item.get("purpose")
        if value is None:
            return None, _stored_by(item, "purpose"), False
        if value in PURPOSES:
            return str(value), _stored_by(item, "purpose"), False
        bad = True
    else:
        bad = False
    code = code_purpose(src)
    return code, (BY_CODE if code is not None else None), bad


def purposes_of(
    sources: Sequence[Source], roles: Mapping[str, str],
    stored: Mapping[str, Mapping[str, Any]],
) -> dict[str, str | None]:
    """The purpose of each source, by its API name (the first pass)."""
    played = role_purposes(roles)
    return {
        src.module: _purpose(src, played, stored.get(src.module))[0] for src in sources
    }


def entry_of(
    src: Source, purposes: Mapping[str, str | None], roles: Mapping[str, str],
    item: Mapping[str, Any] | None,
) -> Entry:
    """The entry of *src*. *purposes* holds the purpose of each other entry,
    for rule 9. *roles* maps a slot to a module. *item* is the stored
    ``catalogue`` item of the module, or ``None``."""
    purpose, by_purpose, bad = _purpose(src, role_purposes(roles), item)
    entry = Entry(
        module=src.module, entity=src.entity, kind=src.kind, label=src.label,
        records=int(src.records), purpose=purpose,
        name=src.field(src.display_field) if src.display_field else None,
        links=[f for f in src.fields if f.type == "lookup" and f.target],
    )
    if by_purpose is not None:
        entry.by["purpose"] = by_purpose
    if bad:
        entry.stale.append("purpose")
    asks = src.records > 0
    if purpose is None:
        if asks and by_purpose is None:
            entry.open.append("purpose")
        return entry
    if purpose not in MONEY_PURPOSES:
        return entry
    code = code_parts(src, purposes)
    for part in KEY_PARTS:
        if item is not None and part in item:
            reason = problem(src, part, item.get(part))
            if reason is None:
                key = item.get(part)
                entry.fields[part] = src.field(key) if key is not None else None
                entry.by[part] = _stored_by(item, part)
                continue
            entry.stale.append(part)
        if code.get(part) is not None:
            entry.fields[part] = code[part]
            entry.by[part] = BY_CODE
        elif asks:
            entry.open.append(part)
    return entry


def build(
    sources: Sequence[Source],
    roles: Mapping[str, str] | None = None,
    items: Iterable[Mapping[str, Any]] = (),
) -> list[Entry]:
    """The catalogue: one entry for each source, in the order given.

    *roles* maps a slot to the module of its approved ``role`` item. *items*
    are the items of the approved plan, of any kind. Only the ``catalogue``
    kind is read.
    """
    roles = dict(roles or {})
    stored = stored_items(items)
    purposes = purposes_of(sources, roles, stored)
    entries = [entry_of(src, purposes, roles, stored.get(src.module)) for src in sources]
    for entry in entries:
        fits = CORE_FITS.get(entry.entity) if entry.kind == "entity" else None
        if fits:
            entry.alike = [
                e.label for e in entries
                if e.kind == "module" and e.records > 0 and e.purpose in fits
            ]
    return entries


__all__ = [
    "AMOUNT_NAMES",
    "BY_WORDS",
    "CORE_FITS",
    "CORE_KEY_COLUMNS",
    "CORE_PURPOSES",
    "KEY_PARTS",
    "KIND_CATALOGUE",
    "MONEY_PURPOSES",
    "PART_MEANINGS",
    "PART_TYPES",
    "PURPOSES",
    "PURPOSE_MEANINGS",
    "PURPOSE_WORDS",
    "SHADOW_KEYS",
    "SLOT_PURPOSES",
    "STANDARD_PURPOSES",
    "SYSTEM_TIMES",
    "Entry",
    "Field",
    "Source",
    "build",
    "code_amount",
    "code_date",
    "code_parent",
    "code_parts",
    "code_purpose",
    "code_status",
    "entry_of",
    "is_catalogue",
    "item_problem",
    "merge_item",
    "problem",
    "purposes_of",
    "role_purposes",
    "singulars",
    "stored_items",
    "without_catalogue",
    "word_purpose",
]
