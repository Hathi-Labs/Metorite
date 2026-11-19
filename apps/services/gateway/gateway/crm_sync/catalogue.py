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

import re
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

#: CRM-Q32-9 (§18.14.10): ``amount`` can be the sum of 2 to 5 fields. Only
#: the admin stores the list, and each key must be a ``currency`` field.
SUM_PART = "amount"
SUM_MIN = 2
SUM_MAX = 5
SUM_TYPES: tuple[str, ...] = ("currency",)
#: The key of a sum is its keys joined with ``+``, and its label the labels
#: joined with " + ". A Zoho API name holds no ``+``.
SUM_KEY_JOIN = "+"
SUM_LABEL_JOIN = " + "
#: The longest key that a sum takes. A longer one is no field of Zoho.
SUM_KEY_MAX = 200

#: The kind of a catalogue item in ``crm_mapping_plans.rules`` (§18.13.4).
#: CRM-Q31-3 writes it. This slice only reads it.
KIND_CATALOGUE = "catalogue"

#: CRM-Q32 (§18.14.4): the one org-level catalogue item. A Zoho API name
#: cannot start with ``@``, so it never names a module, and :func:`build`
#: makes no entry of it. Its one part is ``revenue``.
ORG_MODULE = "@org"
ORG_PARTS: tuple[str, ...] = ("revenue",)
#: The words of ``revenue``: billed (the ``invoice`` modules) or collected
#: (the ``payment_in`` modules). Billed is the default until an admin answers.
REVENUE_VALUES: tuple[str, ...] = ("invoice", "payment_in")
DEFAULT_REVENUE = "invoice"

#: CRM-Q32 (§18.14.3): ``ranked`` keeps at most this many field keys or
#: purpose words for each part that the pipeline closed as ``null``.
RANK_LIMIT = 3
#: The parts of a module item, in the order that a line shows them.
MODULE_PARTS: tuple[str, ...] = ("purpose", "amount", "date", "status", "parent")

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
    name of the module it points at.

    CRM-Q32-9: a sum of fields (:func:`composite`) holds each field in
    ``parts``. Its key is the keys joined with ``+``, its label the labels
    joined with " + ", and its type ``currency``. A plain field has no part."""

    key: str
    label: str
    type: str
    target: str | None = None
    parts: tuple[Field, ...] = ()
    #: SEM-7: the option keys of a picklist (``crm_field_defs.options``).
    #: They are schema, not data (``semantic_model.md`` §3.3 point 1).
    options: tuple[str, ...] = ()


def composite(fields: Sequence[Field]) -> Field:
    """The one field of a sum of *fields* (CRM-Q32-9)."""
    return Field(
        key=SUM_KEY_JOIN.join(f.key for f in fields),
        label=SUM_LABEL_JOIN.join(f.label for f in fields),
        type=SUM_TYPES[0], parts=tuple(fields),
    )


def sum_shape_problem(value: Any) -> bool:
    """True unless *value* is a list of :data:`SUM_MIN` to :data:`SUM_MAX`
    distinct keys, each a string of 1 to :data:`SUM_KEY_MAX` characters that
    does not start with ``@`` and holds no ``+``. It reads no field."""
    if not isinstance(value, list) or not SUM_MIN <= len(value) <= SUM_MAX:
        return True
    if len(set(map(str, value))) != len(value):
        return True
    return not all(
        isinstance(k, str) and 0 < len(k) <= SUM_KEY_MAX and not k.startswith("@")
        and SUM_KEY_JOIN not in k
        for k in value
    )


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


#: §18.16 part 5: the most money fields whose GST evidence one amount pick
#: reads (one bounded statement, ``field_sql.gst_check``).
AMOUNT_EVIDENCE_FIELDS = 6
#: The evidence word of a field that excludes GST (``questions.GST_EXCLUDES``).
EXCLUDES_GST = "excludes_gst"


def amount_evidence_fields(src: Source) -> list[Field]:
    """The currency fields of *src* that an amount pick weighs (WS-53 §18.16
    part 5): filled 50% or more, no tax part in the label or the key, the
    most filled first, at most :data:`AMOUNT_EVIDENCE_FIELDS`. A field with
    a tax word is a GST sibling, and the evidence reads it beside these."""
    from gateway.crm_sync.terms import TAX_PART_WORDS, words

    if src.fills is None:
        return []
    money = [f for f in src.fields if f.type == "currency" and _filled(src, f, FILL_SURE)
             and not (TAX_PART_WORDS & {*words(f.label), *words(f.key)})]
    money.sort(key=lambda f: (-(_share(src, f.key) or 0.0), f.key))
    return money[:AMOUNT_EVIDENCE_FIELDS]


def code_amount_ex_gst(src: Source, evidence: Mapping[str, Sequence[str]]) -> Field | None:
    """The amount that the evidence of GST picks (WS-53 §18.16 part 5): of
    :func:`amount_evidence_fields`, the most filled one that the arithmetic
    or its label marks as excluding GST. None when no field is marked."""
    found = [f for f in amount_evidence_fields(src) if EXCLUDES_GST in (evidence.get(f.key) or ())]
    return found[0] if found else None


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


def parts_of(module: Any) -> tuple[str, ...]:
    """The parts that an item of *module* may hold: ``revenue`` for the
    org-level item :data:`ORG_MODULE`, else the purpose and the key parts."""
    return ORG_PARTS if module == ORG_MODULE else MODULE_PARTS


def _rank_problem(ranked: Any, parts: Sequence[str]) -> bool:
    """True when ``ranked`` is not a map of a part to at most
    :data:`RANK_LIMIT` words (CRM-Q32-1 item 5)."""
    if not isinstance(ranked, Mapping):
        return True
    for part, words in ranked.items():
        if part not in parts or not isinstance(words, list) or len(words) > RANK_LIMIT:
            return True
        if not all(isinstance(w, str) and w for w in words):
            return True
    return False


def _key_part_problem(item: Mapping[str, Any], part: str) -> bool:
    """True when the key part *part* of *item* is not legal: a field API
    name, ``null``, or (CRM-Q32-9) for ``amount`` only a list of 2 to 5
    distinct keys that the admin stored."""
    value = item.get(part)
    if isinstance(value, list):
        by = item.get("by")
        word = by.get(part) if isinstance(by, Mapping) else None
        return part != SUM_PART or word != "admin" or sum_shape_problem(value)
    return part in item and value is not None and (not isinstance(value, str) or not value)


def item_problem(item: Mapping[str, Any]) -> str | None:
    """Rule 4 of the kinds for one ``catalogue`` item, or ``None`` when it
    is legal. ``purpose`` is a word of :data:`PURPOSES` or ``null``. Each key
    part is a field API name or ``null``. ``by`` names ``code``, ``ai`` or
    ``admin`` for each part. CRM-Q32-9: ``amount`` can also be a list of 2 to
    5 distinct keys, with ``by: admin`` only.

    CRM-Q32 (§18.14.4): the item :data:`ORG_MODULE` holds only ``revenue``,
    a word of :data:`REVENUE_VALUES`. ``ranked`` maps a part to at most
    :data:`RANK_LIMIT` words."""
    module = item.get("module")
    if not isinstance(module, str) or not module:
        return "module"
    parts = parts_of(module)
    if module == ORG_MODULE:
        if any(part in item for part in MODULE_PARTS):
            return "module"
        if "revenue" in item and item["revenue"] not in REVENUE_VALUES:
            return "revenue"
    else:
        if module.startswith("@"):
            return "module"
        if ("purpose" in item and item["purpose"] is not None
                and item["purpose"] not in PURPOSES):
            return "purpose"
        bad = next((part for part in KEY_PARTS if _key_part_problem(item, part)), None)
        if bad is not None:
            return bad
        if "revenue" in item:
            return "revenue"
    by = item.get("by", {})
    if not isinstance(by, Mapping):
        return "by"
    for part, word in by.items():
        if part not in parts or word not in BY_WORDS:
            return "by"
    if "ranked" in item and _rank_problem(item["ranked"], parts):
        return "ranked"
    return None


def _ranks(item: Mapping[str, Any] | None) -> dict[str, list[str]]:
    ranked = (item or {}).get("ranked")
    if not isinstance(ranked, Mapping):
        return {}
    return {str(p): [str(w) for w in words][:RANK_LIMIT]
            for p, words in ranked.items() if isinstance(words, list) and words}


def _with_ranks(item: dict[str, Any], ranks: Mapping[str, list[str]]) -> dict[str, Any]:
    """*item* with *ranks*, less the rank of each part with ``by: admin``. An
    empty rank leaves the key out, so an item of before CRM-Q32 stays as it
    was."""
    by = item.get("by") if isinstance(item.get("by"), Mapping) else {}
    kept = {p: list(w) for p, w in sorted(ranks.items()) if w and by.get(p) != "admin"}
    item.pop("ranked", None)
    if kept:
        item["ranked"] = kept
    return item


def merge_item(old: Mapping[str, Any] | None, new: Mapping[str, Any]) -> dict[str, Any]:
    """*new* put over *old*, the item of the same module, part by part. A
    part of *old* with ``by: admin`` stays, unless *new* is the admin's too:
    the pipeline never replaces the choice of an admin (§18.13.4).

    CRM-Q32-1 item 5: ``ranked`` rides with its part. A part that *new*
    writes takes the rank of *new*, or none. The rank of a part with
    ``by: admin`` drops.

    CRM-Q32-9: a sum (a list of keys) rides only with ``by: admin``. A list
    of another writer is left out, and the item keeps its own copy."""
    parts = parts_of(new.get("module") if new.get("module") is not None
                     else (old or {}).get("module"))
    if old is None:
        new_by = new.get("by") if isinstance(new.get("by"), Mapping) else {}
        out = {**dict(new), "by": dict(new.get("by") or {})}
        for part in parts:
            if isinstance(out.get(part), list):
                if new_by.get(part) != "admin":
                    out.pop(part)
                    out["by"].pop(part, None)
                else:
                    out[part] = list(out[part])
        return _with_ranks(out, _ranks(new))
    out = dict(old)
    by = dict(old.get("by") or {}) if isinstance(old.get("by"), Mapping) else {}
    new_by = new.get("by") if isinstance(new.get("by"), Mapping) else {}
    ranks = _ranks(old)
    new_ranks = _ranks(new)
    for part in parts:
        if part not in new:
            continue
        word = new_by.get(part, "ai")
        if by.get(part) == "admin" and word != "admin":
            continue
        if isinstance(new[part], list) and word != "admin":
            # CRM-Q32-9: code and the pipeline never write a sum.
            continue
        out[part] = list(new[part]) if isinstance(new[part], list) else new[part]
        by[part] = word
        if part in new_ranks:
            ranks[part] = new_ranks[part]
        else:
            ranks.pop(part, None)
    for key in ("profile_version",):
        if key in new:
            out[key] = new[key]
    out["by"] = by
    return _with_ranks(out, ranks)


def clear_parts(
    old: Mapping[str, Any] | None, parts: Iterable[str],
    cached: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """*old* with each of *parts* put back to the answer of the pipeline
    (CRM-Q32-2, the value ``"@auto"`` of §18.14.6).

    *cached* is the merged item that the pipeline cached for the module
    (``zoho:map_pipeline`` ``items``). A part that it holds comes back with
    its value, its ``by`` (``ai``, or ``code`` for a part that code answered
    on the sample) and its rank. A part that it does not hold leaves the
    item, so code or a later question answers it. Returns ``None`` when the
    item holds no part after it."""
    out = dict(old or {})
    by = dict(out.get("by") or {}) if isinstance(out.get("by"), Mapping) else {}
    ranks = _ranks(out)
    cached_by = (cached or {}).get("by") if isinstance((cached or {}).get("by"), Mapping) else {}
    cached_ranks = _ranks(cached)
    for part in parts:
        out.pop(part, None)
        by.pop(part, None)
        ranks.pop(part, None)
        # CRM-Q32-9: the pipeline never writes a sum, so a cached list is no
        # answer of the pipeline, and the part leaves the item.
        if cached is not None and part in cached and not isinstance(cached[part], list):
            out[part] = cached[part]
            word = cached_by.get(part)
            by[part] = word if word in ("ai", "code") else "ai"
            if part in cached_ranks:
                ranks[part] = cached_ranks[part]
    out["by"] = by
    if not any(part in out for part in parts_of(out.get("module"))):
        return None
    return _with_ranks(out, ranks)


def revenue_of(stored: Mapping[str, Mapping[str, Any]]) -> tuple[str | None, str | None]:
    """The meaning of revenue that the org-level item holds, and its ``by``:
    ``(None, None)`` with no answer (§18.14.4). A reader applies
    :data:`DEFAULT_REVENUE` itself."""
    item = stored.get(ORG_MODULE)
    value = (item or {}).get("revenue")
    if value not in REVENUE_VALUES:
        return None, None
    return str(value), _stored_by(item or {}, "revenue")


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
    field of *src* of the right type (rule 4 of the kinds), or ``None``.

    CRM-Q32-9: a sum of ``amount`` is checked key by key. A key that is gone
    gives ``gone``, and a key that is not ``currency`` gives ``type``. So
    one bad key makes the whole part ``stale``."""
    if key is None:
        return None
    if isinstance(key, list):
        if part != SUM_PART or sum_shape_problem(key):
            return "type"
        for one in key:
            found = src.field(one)
            if found is None:
                return "gone"
            if found.type not in SUM_TYPES:
                return "type"
        return None
    found = src.field(key) if isinstance(key, str) else None
    if found is None:
        return "gone"
    if found.type not in PART_TYPES[part]:
        return "type"
    return None


def resolve(src: Source, key: Any) -> Field | None:
    """The field that a legal stored part names (:func:`problem` gave
    ``None``): a field of *src*, the sum of a list (:func:`composite`), or
    ``None``. Before CRM-Q32-9, a list gave ``None`` with no error."""
    if isinstance(key, list):
        found = [src.field(k) for k in key]
        return composite([f for f in found if f is not None]) if all(found) else None
    return src.field(key) if key is not None else None


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
                entry.fields[part] = resolve(src, item.get(part))
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


# ── The semantic kinds (WS-SEM SEM-7, ``semantic_model.md`` §3.2 to §3.4) ────
#
# Three plan item kinds: ``measure``, ``join`` and ``query``. An admin accepts
# each one, so it is the admin's text (§3.3 point 6). Each kind has a closed
# key set. No item holds a total, a parameter value or a record value. A
# number-like value where a name must be is refused (§3.3 points 3 and 5).
# :func:`semantic_problem` checks the shape with no read. The check against
# the catalogue (:func:`semantic_ref_problem`) reads the sources that the
# caller gives. ``SEMANTIC_MODEL_ORGS`` gates both callers.

KIND_MEASURE = "measure"
KIND_JOIN = "join"
KIND_QUERY = "query"
SEMANTIC_KINDS: tuple[str, ...] = (KIND_MEASURE, KIND_JOIN, KIND_QUERY)

#: The cap of a label and of an option key (§3.3, "A cap on a label").
LABEL_MAX = 80
#: The aggregates of a measure. ``count`` takes no field. ``sum`` takes 1 to
#: :data:`SUM_MAX` fields. The others take one field.
AGGREGATES: tuple[str, ...] = ("sum", "count", "avg", "min", "max")
#: The units of a measure: fixed words, never a figure.
UNITS: tuple[str, ...] = ("currency", "count", "number", "percent", "days")
#: The field types that a ``sum``, ``avg``, ``min`` or ``max`` reads.
NUMBER_TYPES: frozenset[str] = frozenset({"currency", "number", "decimal", "percent"})
#: The fixed filters of one measure, and the option keys of one filter.
FILTERS_MAX = 5
OPTIONS_MAX = 10
#: The kinds of a parameter of a verified query.
PARAM_KINDS: tuple[str, ...] = (
    "text", "record", "option", "date", "month", "quarter", "year", "number", "period",
)
#: The parameters, the conditions and the group fields of one query.
PARAMS_MAX = 8
WHERE_MAX = 8
GROUP_MAX = 3
#: The operators of a condition of a query. Each one compares a field with a
#: named parameter, never with a literal.
OPS: tuple[str, ...] = (
    "eq", "ne", "in", "gt", "gte", "lt", "lte", "between", "contains", "on",
)

_MEASURE_KEYS = frozenset({"kind", "key", "label", "module", "aggregate", "fields",
                           "filters", "unit"})
_FILTER_KEYS = frozenset({"field", "options"})
_JOIN_KEYS = frozenset({"kind", "module", "field", "target", "label"})
_QUERY_KEYS = frozenset({"kind", "key", "question", "params", "query", "model_version"})
_PARAM_KEYS = frozenset({"name", "kind"})
_SHAPE_KEYS = frozenset({"module", "measure", "aggregate", "fields", "where", "group_by"})
_WHERE_KEYS = frozenset({"field", "op", "param"})

_SLUG = re.compile(r"[a-z][a-z0-9_]{0,39}")
_PARAM_NAME = re.compile(r"[a-z][a-z0-9_]{0,31}")
#: A value that reads as a number, a date or an amount: digits with signs,
#: points, commas, colons, slashes, dashes or spaces.
_NUMBER_LIKE = re.compile(r"[+\-]?[\d.,:/\- ]*\d[\d.,:/\- ]*")
#: A digit that does not follow a letter or ``_`` is a literal in question
#: words. "Q1" stays legal. "2024" and "top 5" are literals.
_LITERAL_DIGIT = re.compile(r"(?<![A-Za-z_])\d")
_PLACEHOLDER = re.compile(r"\{([a-z][a-z0-9_]{0,31})\}")


def is_semantic(item: Any) -> bool:
    """True for an item of a semantic kind (SEM-7)."""
    return isinstance(item, Mapping) and item.get("kind") in SEMANTIC_KINDS


def number_like(value: Any) -> bool:
    """True for a number, or for a string that reads as a number, a date or
    an amount."""
    if isinstance(value, bool):
        return False
    if isinstance(value, int | float):
        return True
    return isinstance(value, str) and _NUMBER_LIKE.fullmatch(value.strip()) is not None


def _plain(value: Any, cap: int) -> bool:
    """True for plain text of 1 to *cap* characters, in its clean form. The
    one text rule is ``rules.clean_body``, the rule of ``crm_business_rules``."""
    # Imported here: ``gateway.routes.crm`` imports this module.
    from gateway.routes.crm.rules import clean_body

    return isinstance(value, str) and len(value) <= cap and clean_body(value) == value


def _has_literal(value: str) -> bool:
    """True when admin text holds a digit that does not follow a letter or
    ``_``: a figure, a date or a count. It catches only digits. A customer
    name or another value in plain words passes this check, and only the
    admin's accept stands against it (``semantic_model.md`` §6.2)."""
    return _LITERAL_DIGIT.search(value) is not None


def _field_key(value: Any) -> bool:
    """True for a field API name, by the rule of a key of a sum (CRM-Q32-9)."""
    return (isinstance(value, str) and 0 < len(value) <= SUM_KEY_MAX
            and not value.startswith("@") and SUM_KEY_JOIN not in value)


def _field_keys(value: Any, low: int, high: int) -> bool:
    return (isinstance(value, list) and low <= len(value) <= high
            and all(_field_key(k) for k in value) and len(set(value)) == len(value))


def _module(value: Any) -> bool:
    return isinstance(value, str) and 0 < len(value) <= SUM_KEY_MAX and not value.startswith("@")


def _aggregate_problem(aggregate: Any, fields: Any) -> str | None:
    if aggregate not in AGGREGATES:
        return "aggregate"
    if aggregate == "count":
        return None if fields in (None, []) else "fields"
    high = SUM_MAX if aggregate == "sum" else 1
    return None if _field_keys(fields, 1, high) else "fields"


def _filters_problem(filters: Any) -> str | None:
    if filters is None:
        return None
    if not isinstance(filters, list) or len(filters) > FILTERS_MAX:
        return "filters"
    for one in filters:
        if not isinstance(one, Mapping) or set(one) != _FILTER_KEYS:
            return "filters"
        options = one.get("options")
        if not _field_key(one.get("field")) or not isinstance(options, list):
            return "filters"
        if not 0 < len(options) <= OPTIONS_MAX or len(set(map(str, options))) != len(options):
            return "filters"
        if not all(_plain(o, LABEL_MAX) for o in options):
            return "filters"
    return None


def _measure_problem(item: Mapping[str, Any]) -> str | None:
    if not set(item) <= _MEASURE_KEYS:
        return "keys"
    if not isinstance(item.get("key"), str) or not _SLUG.fullmatch(item["key"]):
        return "key"
    if not _plain(item.get("label"), LABEL_MAX):
        return "label"
    if _has_literal(item["label"]):
        return "label_value"
    if not _module(item.get("module")):
        return "module"
    found = _aggregate_problem(item.get("aggregate"), item.get("fields"))
    if found is None:
        found = _filters_problem(item.get("filters"))
    if found is not None:
        return found
    return None if item.get("unit") in UNITS else "unit"


def _join_problem(item: Mapping[str, Any]) -> str | None:
    if not set(item) <= _JOIN_KEYS:
        return "keys"
    if not _module(item.get("module")):
        return "module"
    if not _field_key(item.get("field")):
        return "field"
    if not _module(item.get("target")):
        return "target"
    if "label" in item and not _plain(item["label"], LABEL_MAX):
        return "label"
    if "label" in item and _has_literal(item["label"]):
        return "label_value"
    return None


def _params_problem(params: Any) -> str | None:
    if not isinstance(params, list) or len(params) > PARAMS_MAX:
        return "params"
    names: list[str] = []
    for one in params:
        if not isinstance(one, Mapping) or set(one) != _PARAM_KEYS:
            return "params"
        name = one.get("name")
        # A number, or text that is no name (such as "Acme Ltd"), is a value.
        if number_like(name) or not isinstance(name, str) or not _PARAM_NAME.fullmatch(name):
            return "param_value"
        if one.get("kind") not in PARAM_KINDS:
            return "params"
        names.append(name)
    return None if len(set(names)) == len(names) else "params"


def _where_problem(where: Any, names: frozenset[str]) -> str | None:
    """The conditions of a query. Each one names a parameter of *names*, and
    holds no value. Each parameter is used."""
    if not isinstance(where, list) or len(where) > WHERE_MAX:
        return "query"
    used: set[str] = set()
    for one in where:
        if not isinstance(one, Mapping):
            return "query"
        param = one.get("param")
        # A key such as ``value``, a number, or text that is no parameter
        # name (such as "Acme Ltd") is a literal where a name must be.
        if (not set(one) <= _WHERE_KEYS or number_like(param)
                or ("param" in one and not (isinstance(param, str)
                                            and _PARAM_NAME.fullmatch(param)))):
            return "query_value"
        if (set(one) != _WHERE_KEYS or not _field_key(one.get("field"))
                or one.get("op") not in OPS or not isinstance(param, str)
                or param not in names):
            return "query"
        used.add(param)
    return None if used == names else "query"


def _shape_problem(shape: Any, names: frozenset[str]) -> str | None:
    """The query of a verified query: a structure with no literal."""
    if not isinstance(shape, Mapping) or not set(shape) <= _SHAPE_KEYS:
        return "query"
    if not _module(shape.get("module")):
        return "query"
    measure = shape.get("measure")
    if measure is not None:
        if "aggregate" in shape or "fields" in shape:
            return "query"
        if not isinstance(measure, str) or not _SLUG.fullmatch(measure):
            return "query"
    elif _aggregate_problem(shape.get("aggregate"), shape.get("fields")) is not None:
        return "query"
    found = _where_problem(shape.get("where", []), names)
    if found is not None:
        return found
    return None if _field_keys(shape.get("group_by", []), 0, GROUP_MAX) else "query"


def _query_problem(item: Mapping[str, Any]) -> str | None:
    # Imported here: ``gateway.routes.crm`` imports this module.
    from gateway.routes.crm.rules import BODY_MAX

    if not set(item) <= _QUERY_KEYS:
        return "keys"
    if not isinstance(item.get("key"), str) or not _SLUG.fullmatch(item["key"]):
        return "key"
    question = item.get("question")
    if not _plain(question, BODY_MAX):
        return "question"
    bare = _PLACEHOLDER.sub("", question)
    if _has_literal(bare):
        return "question_value"
    if "{" in bare or "}" in bare:
        return "question"
    found = _params_problem(item.get("params"))
    if found is not None:
        return found
    names = frozenset(str(p["name"]) for p in item["params"])
    if set(_PLACEHOLDER.findall(question)) != names:
        return "params"
    found = _shape_problem(item.get("query"), names)
    if found is not None:
        return found
    version = item.get("model_version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        return "model_version"
    return None


def semantic_problem(item: Mapping[str, Any]) -> str | None:
    """Why an item of a semantic kind is not legal, or ``None``. It reads
    nothing. Each word names one part:

    * ``keys``: a key outside the closed set of the kind, such as ``total``.
    * ``key``, ``label``, ``label_value``, ``module``, ``aggregate``,
      ``fields``, ``filters`` and ``unit``: that part of a ``measure``.
    * ``module``, ``field``, ``target``, ``label`` and ``label_value``: that
      part of a ``join``.
    * ``question``, ``question_value``, ``params``, ``param_value``,
      ``query``, ``query_value`` and ``model_version``: that part of a
      ``query``.

    A word that ends in ``_value`` means a literal where a name or plain
    words must be. In a label or in the question words, the code finds only
    a digit (:func:`_has_literal`). It cannot find a name that a person
    wrote in words, so the admin's accept is the guard there.
    """
    kind = item.get("kind")
    if kind == KIND_MEASURE:
        return _measure_problem(item)
    if kind == KIND_JOIN:
        return _join_problem(item)
    if kind == KIND_QUERY:
        return _query_problem(item)
    return "kind"


def _has(src: Source, keys: Iterable[Any]) -> bool:
    return all(src.field(str(k)) is not None for k in keys)


def _numbers(src: Source, aggregate: Any, keys: Iterable[Any]) -> bool:
    if aggregate == "count":
        return True
    found = [src.field(str(k)) for k in keys]
    return all(f is not None and f.type in NUMBER_TYPES for f in found)


def semantic_ref_problem(item: Mapping[str, Any], sources: Mapping[str, Source]) -> str | None:
    """Why a legal semantic item (:func:`semantic_problem` gave ``None``)
    does not fit the catalogue now, or ``None``. *sources* maps a module to
    its catalogue source.

    A ``measure`` names fields of its module, of a number type for each
    aggregate but ``count``. Each fixed filter names option keys of a field.
    A ``join`` names a lookup field whose target is the target of the item.
    A ``query`` names fields of its module. The words are ``module``,
    ``field``, ``type``, ``no_options``, ``options``, ``target`` and
    ``kind``."""
    kind = item.get("kind")
    holder = (item.get("query") or {}) if kind == KIND_QUERY else item
    src = sources.get(str(holder.get("module")))
    if src is None:
        return "module"
    if kind == KIND_JOIN:
        found = src.field(str(item.get("field")))
        if found is None:
            return "field"
        return None if found.target and found.target == item.get("target") else "target"
    if kind == KIND_MEASURE:
        fields = item.get("fields") or []
        if not _has(src, fields):
            return "field"
        if not _numbers(src, item.get("aggregate"), fields):
            return "type"
        for one in item.get("filters") or []:
            found = src.field(str(one.get("field")))
            if found is None:
                return "field"
            # A field with no fixed option keys takes no fixed filter. A core
            # select field (the stage or the status of a deal or a lead) is
            # one: its choices are rows of the org with ids, not keys of a
            # definition (``core.SYSTEM_FIELDS`` lists none). So the word
            # ``no_options`` says that, and not that a key is wrong.
            if not found.options:
                return "no_options"
            if not set(one.get("options") or []) <= set(found.options):
                return "options"
        return None
    if kind == KIND_QUERY:
        shape = item.get("query") or {}
        keys = [*(shape.get("fields") or []), *(shape.get("group_by") or []),
                *(w.get("field") for w in shape.get("where") or [])]
        if not _has(src, keys):
            return "field"
        if "aggregate" in shape and not _numbers(src, shape.get("aggregate"),
                                                  shape.get("fields") or []):
            return "type"
        return None
    return "kind"


# ── The model of a connection: ``shape_only`` (WS-SEM SEM-12) ────────────────
#
# ``semantic_model.md`` §3.3 point 6 and §10.4. The body of the model of a
# connection is one object with four keys: ``v`` (the integer 1), and the
# three sections ``code``, ``ai`` and ``admin``. The name of a section is the
# writer kind of each part in it. :func:`shape_only` is the one check of a
# body. ``connections/semantic_model.py`` calls it before each write. It
# reuses the item checks of SEM-7 above, and it adds no second copy of
# :data:`LABEL_MAX`, :func:`number_like` or :func:`_has_literal`.

#: The value of ``v`` in a body.
MODEL_V = 1
SECTION_CODE = "code"
SECTION_AI = "ai"
SECTION_ADMIN = "admin"
#: The sections, in the order of the writer kinds. A later kind wins.
MODEL_SECTIONS: tuple[str, ...] = (SECTION_CODE, SECTION_AI, SECTION_ADMIN)
#: The body keys (parts) that each section may hold (§10.4).
SECTION_PARTS: Mapping[str, frozenset[str]] = {
    SECTION_CODE: frozenset({"entities", "fields", "joins"}),
    SECTION_AI: frozenset({"entities", "fields"}),
    SECTION_ADMIN: frozenset({"measures", "joins", "tax_marks", "synonyms", "samples",
                              "queries", "instructions"}),
}
#: The most items in one part, and the most options of one field.
PART_ITEMS_MAX = 1000
FIELD_OPTIONS_MAX = 100
#: The time grains of a dimension.
GRAINS: tuple[str, ...] = ("day", "week", "month", "quarter", "year")
#: How a value lookup matches a name: the source's own search, or an exact
#: match. No value is stored (§3.3).
LOOKUPS: tuple[str, ...] = ("search", "exact")

_CODE_ENTITY_KEYS = frozenset({"key", "label", "records", "first_date", "last_date"})
_CODE_FIELD_KEYS = frozenset({"entity", "key", "label", "type", "fill", "grain", "options",
                              "lookup", "tax"})
_AI_ENTITY_KEYS = frozenset({"key", "purpose", "text"})
_AI_FIELD_KEYS = frozenset({"entity", "key", "meaning", "unit", "tax", "text"})
_TAX_MARK_KEYS = frozenset({"entity", "field", "tax"})
_SYNONYM_KEYS = frozenset({"word", "target"})
_SAMPLE_KEYS = frozenset({"question"})
_INSTRUCTION_KEYS = frozenset({"text"})
#: A key that names a secret (§8). A body never holds one, in any section.
_SECRET_WORDS: tuple[str, ...] = ("header", "token", "url", "path", "host", "secret",
                                  "password", "credential", "cookie")
#: A key that names a figure of money. A body never holds one (§3.3 point 5).
_FIGURE_WORDS = frozenset({"amount", "total", "balance", "figure", "sum"})
#: A mark of money beside a digit, on either side: "₹ 5,000", "5000 $",
#: "Rs. 400", "450000 INR", "5USD", "4500 rs". A code after a digit ends at a
#: word boundary, so "5 usda" passes. ``rs`` after a digit needs a space
#: first, so "3rs" passes and "3 rs" does not. Only the connection model
#: (``shape_only``) reads this pattern. The CRM path (SEM-7) does not.
_MONEY = re.compile(r"[₹$€£¥]\s*\d|\d\s*[₹$€£¥]|\b(?:rs|inr|usd|eur|gbp)\.?\s*\d"
                    r"|\d\s*(?:inr|usd|eur|gbp)\b|\d\s+rs\b",
                    re.IGNORECASE)
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
#: The grammar of a key of a connection model: an identifier of ASCII
#: letters, digits and ``_``, that starts with a letter. No space, no mark,
#: no control or format character. The label carries the words.
CONNECTION_KEY_MAX = 64
_CONNECTION_KEY = re.compile(r"[A-Za-z][A-Za-z0-9_]*")


def _tax_words() -> Mapping[str, str]:
    """The closed tax marks and their words: the evidence words of a money
    field (§18.15.5). Imported here: ``crm_sync.questions`` imports this
    module."""
    from gateway.crm_sync.questions import EVIDENCE_WORDS

    return EVIDENCE_WORDS


def _count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _share_value(value: Any) -> bool:
    return (isinstance(value, int | float) and not isinstance(value, bool)
            and 0 <= value <= 1)


def _iso_date(value: Any) -> bool:
    from datetime import date

    if not isinstance(value, str) or not _DATE.fullmatch(value):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def connection_key_problem(value: Any) -> str | None:
    """Why *value* is not a key of a connection model, or ``None``. A key
    names an entity, a field or a measure, and SEM-15 puts it in the prompt
    of the chat. So it is an identifier of at most
    :data:`CONNECTION_KEY_MAX` characters, and never a number, an amount, a
    date or a literal (``_has_literal``: a digit after a digit)."""
    if not isinstance(value, str) or not 0 < len(value) <= CONNECTION_KEY_MAX:
        return "key_value"
    if not _CONNECTION_KEY.fullmatch(value):
        return "key_value"
    if number_like(value) or _MONEY.search(value) or _DATE.search(value):
        return "key_value"
    return "key_value" if _has_literal(value) else None


def _label_problem(value: Any) -> str | None:
    """A label or an option that the source wrote: plain text of at most
    :data:`LABEL_MAX` characters, and never a number or an amount."""
    if not _plain(value, LABEL_MAX):
        return "label"
    if number_like(value) or _MONEY.search(value):
        return "label_value"
    return None


def _admin_text_problem(value: Any) -> str | None:
    """The admin's words: 1 to ``BODY_MAX`` characters that pass
    ``clean_body``, with no digit that :func:`_has_literal` finds."""
    from gateway.routes.crm.rules import BODY_MAX

    if not _plain(value, BODY_MAX):
        return "text"
    return "text_value" if _has_literal(value) else None


def ai_description(part: str, item: Mapping[str, Any]) -> str | None:
    """The text of an item of the ``ai`` section, built by code from its
    closed words (§4.2 step 4), or ``None`` when a word is not in its closed
    set. The profiler writes this text, and :func:`shape_only` builds it
    again and refuses any other text."""
    if part == "entities":
        return PURPOSE_MEANINGS.get(str(item.get("purpose")))
    if part != "fields":
        return None
    said: list[str] = []
    if "meaning" in item:
        meaning = PART_MEANINGS.get(str(item["meaning"]))
        if meaning is None:
            return None
        said.append(f"It is {meaning}.")
    if "unit" in item:
        if item["unit"] not in UNITS:
            return None
        said.append(f"Its unit is {item['unit']}.")
    if "tax" in item:
        word = _tax_words().get(str(item["tax"]))
        if word is None:
            return None
        said.append(f"Tax: {word}.")
    return " ".join(said) or None


def _code_entity(item: Mapping[str, Any]) -> str | None:
    if not set(item) <= _CODE_ENTITY_KEYS:
        return "keys"
    if not _module(item.get("key")):
        return "key"
    if "label" in item and (found := _label_problem(item["label"])) is not None:
        return found
    if "records" in item and not _count(item["records"]):
        return "records"
    dates = [item[k] for k in ("first_date", "last_date") if k in item]
    if not all(_iso_date(d) for d in dates):
        return "date"
    if len(dates) == 2 and dates[0] > dates[1]:
        return "date"
    return None


def _code_field(item: Mapping[str, Any]) -> str | None:
    if not set(item) <= _CODE_FIELD_KEYS:
        return "keys"
    if not _module(item.get("entity")) or not _field_key(item.get("key")):
        return "key"
    if "label" in item and (found := _label_problem(item["label"])) is not None:
        return found
    if "type" in item and not (isinstance(item["type"], str) and _SLUG.fullmatch(item["type"])):
        return "type"
    if "fill" in item and not _share_value(item["fill"]):
        return "fill"
    if "grain" in item and item["grain"] not in GRAINS:
        return "grain"
    if "lookup" in item and item["lookup"] not in LOOKUPS:
        return "lookup"
    if "tax" in item and item["tax"] not in _tax_words():
        return "tax"
    options = item.get("options")
    if options is None:
        return None
    if not isinstance(options, list) or len(options) > FIELD_OPTIONS_MAX:
        return "options"
    if len({str(o) for o in options}) != len(options):
        return "options"
    for one in options:
        found = _label_problem(one)
        if found is not None:
            return "options" if found == "label" else "options_value"
    return None


def _ai_item(part: str, item: Mapping[str, Any]) -> str | None:
    keys = _AI_ENTITY_KEYS if part == "entities" else _AI_FIELD_KEYS
    if not set(item) <= keys:
        return "keys"
    if part == "entities":
        if not _module(item.get("key")):
            return "key"
        if item.get("purpose") not in PURPOSES:
            return "purpose"
    else:
        if not _module(item.get("entity")) or not _field_key(item.get("key")):
            return "key"
        if not {"meaning", "unit", "tax"} & set(item):
            return "words"
    # Imported here: ``gateway.routes.crm`` imports this module.
    from gateway.routes.crm.rules import BODY_MAX

    built = ai_description(part, item)
    if built is None:
        return "words"
    text_ = item.get("text")
    if not isinstance(text_, str) or text_ != built or len(text_) > BODY_MAX:
        return "ai_text"
    return None


def _kind_then(kind: str, check: Any) -> Any:
    """An item of a SEM-7 kind: the ``kind`` key may be left out, and when
    present it names the kind. Then the item check of SEM-7 runs."""
    def run(item: Mapping[str, Any]) -> str | None:
        if "kind" in item and item["kind"] != kind:
            return "kind"
        return check(item)
    return run


def _tax_mark(item: Mapping[str, Any]) -> str | None:
    if set(item) != _TAX_MARK_KEYS:
        return "keys"
    if not _module(item.get("entity")) or not _field_key(item.get("field")):
        return "key"
    return None if item.get("tax") in _tax_words() else "tax"


def _words_item(keys: frozenset[str]) -> Any:
    def run(item: Mapping[str, Any]) -> str | None:
        if set(item) != keys:
            return "keys"
        for k in sorted(keys):
            found = _admin_text_problem(item.get(k))
            if found is not None:
                return found
        return None
    return run


def _synonym(targets: frozenset[str]) -> Any:
    def run(item: Mapping[str, Any]) -> str | None:
        if set(item) != _SYNONYM_KEYS:
            return "keys"
        found = _admin_text_problem(item.get("word"))
        if found is not None:
            return found
        return None if item.get("target") in targets else "target"
    return run


#: The check of a part that only one section may hold, or that has one rule
#: in each section. The section check comes first in :func:`shape_only`.
_PART_CHECKS: Mapping[str, Any] = {
    "joins": _kind_then(KIND_JOIN, _join_problem),
    "measures": _kind_then(KIND_MEASURE, _measure_problem),
    "queries": _kind_then(KIND_QUERY, _query_problem),
    "tax_marks": _tax_mark,
    "samples": _words_item(_SAMPLE_KEYS),
    "instructions": _words_item(_INSTRUCTION_KEYS),
}

#: The identity of an item, for the parts where two items may not share it.
_IDENTITY: Mapping[str, Any] = {
    "entities": lambda i: i.get("key"),
    "fields": lambda i: (i.get("entity"), i.get("key")),
    "measures": lambda i: i.get("key"),
    "queries": lambda i: i.get("key"),
}


def _keys_of(value: Any) -> Iterable[str]:
    """Each key of each mapping in *value*, at any depth."""
    if isinstance(value, Mapping):
        for k, v in value.items():
            yield str(k)
            yield from _keys_of(v)
    elif isinstance(value, list):
        for v in value:
            yield from _keys_of(v)


def _key_problem(body: Mapping[str, Any]) -> str | None:
    for key in _keys_of(body):
        low = key.lower()
        if any(word in low for word in _SECRET_WORDS):
            return "secret"
        if low in _FIGURE_WORDS or low.removesuffix("s") in _FIGURE_WORDS:
            return "figure"
    return None


def _key_values(part: str, item: Mapping[str, Any]) -> Iterable[Any]:
    """Each value of *item* that names a key of the model: an entity, a
    field, a module, a target or a measure."""
    names = {"entities": ("key",), "fields": ("entity", "key"),
             "joins": ("module", "field", "target"), "tax_marks": ("entity", "field"),
             "measures": ("key", "module"), "queries": ("key",),
             "synonyms": ("target",)}.get(part, ())
    for name in names:
        if name in item:
            yield item[name]
    if part == "measures":
        yield from item.get("fields") or []
        yield from (f.get("field") for f in item.get("filters") or [])
    if part == "queries":
        shape = item.get("query") or {}
        yield from (shape[k] for k in ("module", "measure") if k in shape)
        yield from shape.get("fields") or []
        yield from shape.get("group_by") or []
        yield from (w.get("field") for w in shape.get("where") or [])


def _declared_options(body: Mapping[str, Any]) -> dict[tuple[Any, Any], frozenset[Any]]:
    """The options of each field of the ``code`` section, by entity and key."""
    parts = body.get(SECTION_CODE)
    fields = parts.get("fields") if isinstance(parts, Mapping) else None
    found: dict[tuple[Any, Any], frozenset[Any]] = {}
    for f in fields if isinstance(fields, list) else ():
        if isinstance(f, Mapping) and isinstance(f.get("options"), list):
            found[(f.get("entity"), f.get("key"))] = frozenset(
                o for o in f["options"] if isinstance(o, str))
    return found


def _filter_option_problem(item: Mapping[str, Any],
                           options: Mapping[tuple[Any, Any], frozenset[Any]]) -> str | None:
    """A fixed filter of a measure names options that the ``code`` section
    declares for that field. The SEM-7 check allows any plain text there, and
    on the CRM path ``semantic_ref_problem`` checks it against the catalogue.
    ``shape_only`` reads no catalogue, so it checks the body itself."""
    for one in item.get("filters") or []:
        declared = options.get((item.get("module"), one.get("field")), frozenset())
        for option in one.get("options") or []:
            if _label_problem(option) is not None or option not in declared:
                return "filter_option"
    return None


def _targets(body: Mapping[str, Any]) -> frozenset[str]:
    """The keys that a synonym may name: the key of an entity, a field or a
    measure of the body, when it holds the grammar of a key."""
    found: set[str] = set()
    for section in MODEL_SECTIONS:
        parts = body.get(section)
        if not isinstance(parts, Mapping):
            continue
        for part in ("entities", "fields", "measures"):
            items = parts.get(part)
            for item in items if isinstance(items, list) else ():
                if (isinstance(item, Mapping)
                        and connection_key_problem(item.get("key")) is None):
                    found.add(item["key"])
    return frozenset(found)


#: The largest ``model_version`` of a verified query in a connection body.
#: The writer sets no cap on a version, so this is a bound that no real
#: model reaches.
MODEL_VERSION_MAX = 1000


def _value_in_word(value: Any) -> bool:
    """True when a word of closed shape (a parameter name, a field type)
    hides a value: a number, an amount, or a run of two digits such as a
    date in ``d20240301``."""
    return (not isinstance(value, str) or number_like(value)
            or _MONEY.search(value) is not None or _has_literal(value))


def _param_names(item: Mapping[str, Any]) -> Iterable[Any]:
    """Each parameter name of a verified query: in ``params`` and in each
    condition of ``query.where``."""
    yield from (p.get("name") for p in item.get("params") or [])
    shape = item.get("query") or {}
    yield from (w.get("param") for w in shape.get("where") or [] if "param" in w)


def _query_value_problem(item: Mapping[str, Any]) -> str | None:
    """The connection checks of a verified query. A parameter name is
    copied into the question as ``{name}``, and the SEM-7 literal check
    removes placeholders first. So a name never hides a value here."""
    if any(_value_in_word(name) for name in _param_names(item)):
        return "param_value"
    version = item.get("model_version")
    return "model_version" if isinstance(version, int) and version > MODEL_VERSION_MAX else None


def _connection_item_problem(part: str, item: Mapping[str, Any],
                             options: Mapping[tuple[Any, Any], frozenset[Any]]) -> str | None:
    """The checks of a connection model that run after the item check: the
    value of each key, a field type, the parameter names and version of a
    query, and the options of each fixed filter of a measure. The CRM path
    (SEM-7) does not run them."""
    for value in _key_values(part, item):
        found = connection_key_problem(value)
        if found is not None:
            return found
    if part == "fields" and "type" in item and _value_in_word(item["type"]):
        return "type"
    if part == "queries":
        return _query_value_problem(item)
    return _filter_option_problem(item, options) if part == "measures" else None


def _part_problem(section: str, part: str, items: Any, targets: frozenset[str],
                  options: Mapping[tuple[Any, Any], frozenset[Any]]) -> str | None:
    if not isinstance(items, list) or len(items) > PART_ITEMS_MAX:
        return "items"
    if section == SECTION_CODE and part == "entities":
        check: Any = _code_entity
    elif section == SECTION_CODE and part == "fields":
        check = _code_field
    elif section == SECTION_AI and part in ("entities", "fields"):
        def check(item: Mapping[str, Any]) -> str | None:
            return _ai_item(part, item)
    elif part == "synonyms":
        check = _synonym(targets)
    else:
        check = _PART_CHECKS.get(part)
    if check is None:
        return "part"
    seen: set[Any] = set()
    identity = _IDENTITY.get(part)
    for item in items:
        if not isinstance(item, Mapping):
            return "item"
        found = check(item)
        if found is not None:
            return found
        found = _connection_item_problem(part, item, options)
        if found is not None:
            return found
        if identity is not None:
            one = identity(item)
            if one in seen:
                return "duplicate"
            seen.add(one)
    return None


def shape_only(body: Any) -> str | None:
    """Why a body of the model of a connection is not shape only, or
    ``None`` (``semantic_model.md`` §3.3 point 6 and §10.4). It is pure and
    reads no database. It returns the FIRST problem as a short code:

    * ``body``: not an object with exactly the keys ``v``, ``code``, ``ai``
      and ``admin``. ``v``: ``v`` is not :data:`MODEL_V`.
    * ``secret``: a key, at any depth, names a header, a token, a url, a
      path, a host or another secret (§8).
    * ``figure``: a key names an amount, a total, a balance or a figure.
    * ``<section>``: the section is not an object.
    * ``<section>.<part>:<word>``: a part of a section. ``section`` is a
      part in the wrong section. ``part`` is a key outside the closed set.
      Each other word is the first problem of an item, such as ``keys``,
      ``key_value`` (a key that is not an identifier, see
      :func:`connection_key_problem`), ``filter_option``, ``label_value``,
      ``ai_text``, ``text_value``, ``param_value`` or
      ``query_value`` (the words of :func:`semantic_problem`).
    """
    if not isinstance(body, Mapping) or set(body) != {"v", *MODEL_SECTIONS}:
        return "body"
    if isinstance(body["v"], bool) or body["v"] != MODEL_V:
        return "v"
    found = _key_problem(body)
    if found is not None:
        return found
    targets = _targets(body)
    options = _declared_options(body)
    for section in MODEL_SECTIONS:
        parts = body[section]
        if not isinstance(parts, Mapping):
            return section
        for part, items in parts.items():
            where = f"{section}.{part}"
            if part not in SECTION_PARTS[section]:
                known = any(part in s for s in SECTION_PARTS.values())
                return f"{where}:{'section' if known else 'part'}"
            found = _part_problem(section, str(part), items, targets, options)
            if found is not None:
                return f"{where}:{found}"
    return None


__all__ = [
    "AGGREGATES",
    "AMOUNT_NAMES",
    "BY_WORDS",
    "CONNECTION_KEY_MAX",
    "CORE_FITS",
    "CORE_KEY_COLUMNS",
    "CORE_PURPOSES",
    "DEFAULT_REVENUE",
    "FIELD_OPTIONS_MAX",
    "FILTERS_MAX",
    "GRAINS",
    "GROUP_MAX",
    "KEY_PARTS",
    "KIND_CATALOGUE",
    "KIND_JOIN",
    "KIND_MEASURE",
    "KIND_QUERY",
    "LABEL_MAX",
    "LOOKUPS",
    "MODEL_SECTIONS",
    "MODEL_V",
    "MODEL_VERSION_MAX",
    "MODULE_PARTS",
    "MONEY_PURPOSES",
    "NUMBER_TYPES",
    "OPS",
    "OPTIONS_MAX",
    "ORG_MODULE",
    "ORG_PARTS",
    "PARAMS_MAX",
    "PARAM_KINDS",
    "PART_ITEMS_MAX",
    "PART_MEANINGS",
    "PART_TYPES",
    "PURPOSES",
    "PURPOSE_MEANINGS",
    "PURPOSE_WORDS",
    "RANK_LIMIT",
    "REVENUE_VALUES",
    "SECTION_ADMIN",
    "SECTION_AI",
    "SECTION_CODE",
    "SECTION_PARTS",
    "SEMANTIC_KINDS",
    "SHADOW_KEYS",
    "SLOT_PURPOSES",
    "STANDARD_PURPOSES",
    "SUM_KEY_JOIN",
    "SUM_KEY_MAX",
    "SUM_LABEL_JOIN",
    "SUM_MAX",
    "SUM_MIN",
    "SUM_PART",
    "SUM_TYPES",
    "SYSTEM_TIMES",
    "UNITS",
    "WHERE_MAX",
    "Entry",
    "Field",
    "Source",
    "ai_description",
    "build",
    "clear_parts",
    "code_amount",
    "code_amount_ex_gst",
    "code_date",
    "code_parent",
    "code_parts",
    "code_purpose",
    "code_status",
    "composite",
    "connection_key_problem",
    "entry_of",
    "is_catalogue",
    "is_semantic",
    "item_problem",
    "merge_item",
    "number_like",
    "parts_of",
    "problem",
    "purposes_of",
    "resolve",
    "revenue_of",
    "role_purposes",
    "semantic_problem",
    "semantic_ref_problem",
    "shape_only",
    "singulars",
    "stored_items",
    "sum_shape_problem",
    "without_catalogue",
    "word_purpose",
]
