"""Zoho module record to canonical column values (WS-53 CRM-Z4).

Spec: ``project-docs/specs/crm_platform.md`` §3.5 and §5.4.

The sync engine calls :func:`map_record` and never reads a Zoho field name. The
function is pure: it reads no database and calls no API. It returns a
:class:`Mapped` with the canonical columns of one mirror table, the links the
engine still has to resolve, and the owner.

**The field knowledge is a copy** of ``routes/crm/import_zoho.py`` (the legacy
importer), and not an import of it, so ``crm_sources`` depends on no route and
no ``ingestion`` module. The legacy path retires at CRM-Z11.

**What goes where.**

* A field with a canonical column maps to it (``Account_Name`` to ``name`` on
  a company).
* Every other field goes to ``custom``, keyed by its Zoho API name. A key that
  starts with ``$`` is a Zoho system flag, and it is dropped.
* ``Modified_Time`` becomes ``ext_modified_at``. When a tenant hides it, the
  adapter puts ``Created_Time`` in ``SourceRecord.modified_at`` and that value
  is used.
* ``Created_Time`` stays in ``custom``. ``created_at`` is a column that the
  mirror write refuses.

**The owner.** :attr:`Mapped.owner_email` holds the Zoho email of the owner.
The engine decides whether it is a member of the organization. For an
unmatched owner the engine stores the name from :attr:`Mapped.owner_name` in
``custom["_owner_name"]`` and leaves the owner column empty.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

#: The key in ``custom`` that keeps the Zoho name of an owner who is not a
#: member of the organization.
OWNER_NAME_KEY = "_owner_name"

#: ``NUMERIC(14,2)`` holds 12 integer digits. A larger value would abort the
#: transaction at the driver, so it maps to NULL and the record still lands.
_NUMERIC_CEILING = 10**12
_INT_CEILING = 2**31
_SMALLINT_CEILING = 2**15

_ACTIVITY_TYPES = {"note": "note", "call": "call", "meeting": "meeting", "task": "task"}

#: The Zoho module a ``$se_module`` value names, as a canonical entity.
_MODULE_ENTITY = {
    "Accounts": "company",
    "Contacts": "contact",
    "Leads": "lead",
    "Deals": "deal",
}

#: The column an entity fills on ``crm_activities``.
_ACTIVITY_COLUMN = {
    "company": "company_id",
    "contact": "contact_id",
    "lead": "lead_id",
    "deal": "deal_id",
}

ENTITY_TABLES: Mapping[str, str] = {
    "company": "crm_companies",
    "contact": "crm_contacts",
    "lead": "crm_leads",
    "deal": "crm_deals",
    "note": "crm_activities",
    "call": "crm_activities",
    "meeting": "crm_activities",
    "task": "crm_activities",
}


@dataclass(frozen=True)
class Link:
    """A reference to another record that the engine resolves to a local id.

    ``candidates`` is a list of ``(column, entity)``. The engine looks the
    provider id up in each entity in order. The first hit sets that column.
    """

    ext_id: str
    candidates: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class Mapped:
    """One record, ready for the engine."""

    entity: str
    table: str
    values: dict[str, Any]
    links: tuple[Link, ...] = ()
    #: The name of the lead status or the deal stage. The engine resolves it.
    status_name: str | None = None
    #: A deal's contact. The engine adds it to ``crm_deal_contacts``.
    deal_contact: Link | None = None
    #: A converted lead: the Zoho ids it became (``contact``, ``company``,
    #: ``deal``), a key only when Zoho names it. ``None`` for a lead that is
    #: not converted and for any other entity (CRM-Q5).
    conversion: dict[str, str] | None = None
    owner_email: str | None = None
    owner_name: str | None = None
    #: A deal's pipeline: ``(ext_id, name)``. The engine resolves it to a local
    #: pipeline id. ``None`` for any other entity (CRM-Q2).
    pipeline: tuple[str | None, str | None] | None = None
    #: A free note on what the engine must derive, for example the lead name.
    derived: tuple[str, ...] = field(default=())


# ── Small coercions ─────────────────────────────────────────────────────────


def text_value(value: Any) -> str | None:
    """A scalar as text. An empty string is NULL (Zoho sends '' for a cleared field)."""
    if value is None or isinstance(value, dict | list):
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return str(value)


def number(value: Any) -> float | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed != parsed or abs(parsed) >= _NUMERIC_CEILING:
        return None
    return parsed


def integer(value: Any, ceiling: int = _INT_CEILING) -> int | None:
    parsed = number(value)
    if parsed is None or abs(parsed) >= ceiling:
        return None
    return int(parsed)


def instant(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def day(value: Any) -> date | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


def lookup_id(value: Any) -> str | None:
    """The id inside a Zoho lookup (``{"id": ..., "name": ...}``), or a bare id."""
    if isinstance(value, dict):
        return text_value(value.get("id"))
    return text_value(value)


def _address(fields: Mapping[str, Any], prefix: str) -> dict[str, str] | None:
    parts = {
        "street": text_value(fields.get(f"{prefix}_Street")),
        "city": text_value(fields.get(f"{prefix}_City")),
        "state": text_value(fields.get(f"{prefix}_State")),
        "code": text_value(fields.get(f"{prefix}_Code")),
        "country": text_value(fields.get(f"{prefix}_Country")),
    }
    present = {k: v for k, v in parts.items() if v}
    return present or None


# ── Per entity ──────────────────────────────────────────────────────────────

#: The Zoho keys each entity maps to a canonical column (or reads for a link
#: or the owner). Everything else goes to ``custom``.
_CONSUMED: Mapping[str, frozenset[str]] = {
    "company": frozenset({
        "id", "Account_Name", "Website", "Industry", "Employees", "Annual_Revenue",
        "Phone", "Email", "Description", "Owner", "Modified_Time",
        "Billing_Street", "Billing_City", "Billing_State", "Billing_Code",
        "Billing_Country",
    }),
    "contact": frozenset({
        "id", "First_Name", "Last_Name", "Full_Name", "Email", "Phone", "Mobile",
        "Title", "Description", "Account_Name", "Owner", "Modified_Time",
    }),
    "lead": frozenset({
        "id", "First_Name", "Last_Name", "Company", "Email", "Phone", "Mobile",
        "Website", "Industry", "No_of_Employees", "Annual_Revenue", "Lead_Source",
        "Description", "Lead_Status", "Owner", "Modified_Time",
        "Converted__s", "Converted_Date_Time", "Converted_Contact",
        "Converted_Account", "Converted_Deal",
    }),
    "deal": frozenset({
        "id", "Deal_Name", "Amount", "Probability", "Closing_Date", "Next_Step",
        "Description", "Stage", "Account_Name", "Contact_Name", "Currency",
        "Exchange_Rate", "Owner", "Modified_Time",
    }),
    "note": frozenset({
        "id", "Note_Title", "Note_Content", "Parent_Id", "Owner", "Modified_Time",
    }),
    "call": frozenset({
        "id", "Subject", "Description", "Call_Start_Time", "Who_Id", "What_Id",
        "Owner", "Modified_Time",
    }),
    "meeting": frozenset({
        "id", "Event_Title", "Description", "Start_DateTime", "Who_Id", "What_Id",
        "Owner", "Modified_Time",
    }),
    # ``Status`` and ``Priority`` stay in ``custom`` on purpose (CRM-Q5).
    "task": frozenset({
        "id", "Subject", "Description", "Due_Date", "Closed_Time", "Who_Id",
        "What_Id", "Owner", "Modified_Time",
    }),
}


def custom_fields(entity: str, fields: Mapping[str, Any]) -> dict[str, Any]:
    """Every field with no canonical column, keyed by its Zoho API name."""
    consumed = _CONSUMED[entity]
    return {
        key: value
        for key, value in fields.items()
        if key not in consumed and not key.startswith("$")
    }


def custom_field_keys(entity: str) -> frozenset[str]:
    """The Zoho keys of an entity that have a canonical column."""
    return _CONSUMED[entity]


def _owner(fields: Mapping[str, Any], owners: Mapping[str, str]) -> tuple[str | None, str | None]:
    owner = fields.get("Owner")
    if not isinstance(owner, dict):
        return None, None
    email = text_value(owner.get("email"))
    if not email:
        email = owners.get(text_value(owner.get("id")) or "")
    return (email.lower() if email else None), text_value(owner.get("name"))


def _company(fields: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "name": text_value(fields.get("Account_Name")) or f"Account {fields.get('id')}",
        "website": text_value(fields.get("Website")),
        "industry": text_value(fields.get("Industry")),
        "no_of_employees": integer(fields.get("Employees")),
        "annual_revenue": number(fields.get("Annual_Revenue")),
        "phone": text_value(fields.get("Phone")),
        "email": text_value(fields.get("Email")),
        "address": _address(fields, "Billing"),
        "description": text_value(fields.get("Description")),
    }


def _contact(fields: Mapping[str, Any]) -> dict[str, Any]:
    first = text_value(fields.get("First_Name"))
    last = text_value(fields.get("Last_Name"))
    return {
        # ``first_name`` is NOT NULL, and a Zoho contact can have a surname only.
        "first_name": first or last or text_value(fields.get("Full_Name")) or "Contact",
        "last_name": last if first else None,
        "email": text_value(fields.get("Email")),
        "phone": text_value(fields.get("Phone")),
        "mobile": text_value(fields.get("Mobile")),
        "title": text_value(fields.get("Title")),
        "description": text_value(fields.get("Description")),
    }


def _lead(fields: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "first_name": text_value(fields.get("First_Name")),
        "last_name": text_value(fields.get("Last_Name")),
        "organization_name": text_value(fields.get("Company")),
        "email": text_value(fields.get("Email")),
        "phone": text_value(fields.get("Phone")),
        "mobile": text_value(fields.get("Mobile")),
        "website": text_value(fields.get("Website")),
        "industry": text_value(fields.get("Industry")),
        "no_of_employees": integer(fields.get("No_of_Employees")),
        "annual_revenue": number(fields.get("Annual_Revenue")),
        "lead_source": text_value(fields.get("Lead_Source")),
        "description": text_value(fields.get("Description")),
    }


def _deal(fields: Mapping[str, Any]) -> dict[str, Any]:
    values: dict[str, Any] = {
        "name": text_value(fields.get("Deal_Name")) or f"Deal {fields.get('id')}",
        "amount": number(fields.get("Amount")),
        "probability": integer(fields.get("Probability"), _SMALLINT_CEILING),
        "expected_close_date": day(fields.get("Closing_Date")),
        "next_step": text_value(fields.get("Next_Step")),
        "description": text_value(fields.get("Description")),
        "exchange_rate": number(fields.get("Exchange_Rate")),
    }
    # ``currency`` is NOT NULL with a default, so a deal with none keeps INR.
    currency = text_value(fields.get("Currency"))
    if currency:
        values["currency"] = currency
    return values


def _activity_links(entity: str, fields: Mapping[str, Any]) -> tuple[Link, ...]:
    """The records an activity belongs to. A note has a parent, a call or
    meeting has a person (``Who_Id``) and a thing (``What_Id``)."""
    se_module = _MODULE_ENTITY.get(text_value(fields.get("$se_module")) or "")
    links: list[Link] = []
    if entity == "note":
        parent = lookup_id(fields.get("Parent_Id"))
        if parent:
            if se_module:
                links.append(Link(parent, ((_ACTIVITY_COLUMN[se_module], se_module),)))
            else:
                links.append(Link(parent, tuple(
                    (column, kind) for kind, column in _ACTIVITY_COLUMN.items()
                )))
        return tuple(links)
    who = lookup_id(fields.get("Who_Id"))
    if who:
        links.append(Link(who, (("contact_id", "contact"), ("lead_id", "lead"))))
    what = lookup_id(fields.get("What_Id"))
    if what:
        if se_module:
            links.append(Link(what, ((_ACTIVITY_COLUMN[se_module], se_module),)))
        else:
            links.append(Link(what, (("deal_id", "deal"), ("company_id", "company"))))
    return tuple(links)


def lead_conversion(fields: Mapping[str, Any]) -> dict[str, str] | None:
    """The Zoho ids a converted lead became, or ``None`` for an open lead.

    v2 sends ``Converted__s`` with ``Converted_Contact``, ``Converted_Account``
    and ``Converted_Deal`` lookups, and older tenants send ``$converted`` with
    a ``$converted_detail`` object. Both are read, and a key is present only
    when Zoho names that record.
    """
    detail = fields.get("$converted_detail")
    detail = detail if isinstance(detail, dict) else {}
    ids = {
        "contact": lookup_id(fields.get("Converted_Contact"))
        or lookup_id(detail.get("contact") or detail.get("Contacts")),
        "company": lookup_id(fields.get("Converted_Account"))
        or lookup_id(detail.get("account") or detail.get("Accounts")),
        "deal": lookup_id(fields.get("Converted_Deal"))
        or lookup_id(detail.get("deal") or detail.get("Deals")),
    }
    flagged = fields.get("Converted__s") is True or fields.get("$converted") is True
    found = {key: value for key, value in ids.items() if value}
    if not flagged and not found and not instant(fields.get("Converted_Date_Time")):
        return None
    return found


def _activity(entity: str, fields: Mapping[str, Any]) -> dict[str, Any]:
    created = instant(fields.get("Created_Time"))
    if entity == "note":
        return {
            "type": "note",
            "subject": text_value(fields.get("Note_Title")),
            "body": text_value(fields.get("Note_Content")),
            "occurred_at": created,
        }
    if entity == "call":
        return {
            "type": "call",
            "subject": text_value(fields.get("Subject")),
            "body": text_value(fields.get("Description")),
            "occurred_at": instant(fields.get("Call_Start_Time")) or created,
        }
    if entity == "task":
        due = day(fields.get("Due_Date"))
        closed = instant(fields.get("Closed_Time"))
        done = (text_value(fields.get("Status")) or "").lower() == "completed"
        return {
            "type": "task",
            "subject": text_value(fields.get("Subject")),
            "body": text_value(fields.get("Description")),
            "occurred_at": created,
            "due_at": datetime(due.year, due.month, due.day, tzinfo=UTC) if due else None,
            "completed_at": (closed or instant(fields.get("Modified_Time")) or created)
            if done else None,
        }
    return {
        "type": "meeting",
        "subject": text_value(fields.get("Event_Title")),
        "body": text_value(fields.get("Description")),
        "occurred_at": instant(fields.get("Start_DateTime")) or created,
    }


_BUILDERS = {
    "company": _company,
    "contact": _contact,
    "lead": _lead,
    "deal": _deal,
}


def map_record(
    entity: str,
    fields: Mapping[str, Any],
    *,
    modified_at: datetime | None,
    owners: Mapping[str, str],
    rules: Mapping[str, str] | None = None,
) -> Mapped:
    """Map one Zoho record of ``entity`` (a canonical entity name).

    ``owners`` maps a Zoho user id to its email, for a record whose ``Owner``
    has no email of its own.

    ``rules`` holds the approved field rules of the entity (CRM-Q7a), as
    ``{target_column: field_api_name}``. Each rule applies AFTER the fixed
    rules, and only where the fixed map gives NULL (:func:`apply_field_rules`).
    The value also stays in ``custom``.
    """
    if entity not in ENTITY_TABLES:
        raise ValueError(f"No Zoho mapping for the entity {entity!r}")
    table = ENTITY_TABLES[entity]
    owner_email, owner_name = _owner(fields, owners)
    links: tuple[Link, ...] = ()
    status_name: str | None = None
    deal_contact: Link | None = None
    conversion: dict[str, str] | None = None
    derived: tuple[str, ...] = ()
    pipeline: tuple[str | None, str | None] | None = None

    if entity in _ACTIVITY_TYPES:
        values = _activity(entity, fields)
        links = _activity_links(entity, fields)
    else:
        values = _BUILDERS[entity](fields)
        if entity == "contact":
            account = lookup_id(fields.get("Account_Name"))
            if account:
                links = (Link(account, (("company_id", "company"),)),)
        elif entity == "lead":
            status_name = text_value(fields.get("Lead_Status"))
            derived = ("lead_name",)
            conversion = lead_conversion(fields)
            if conversion is not None:
                values["converted_at"] = instant(fields.get("Converted_Date_Time")) or (
                    instant(fields.get("Modified_Time")) or modified_at
                )
        elif entity == "deal":
            status_name = text_value(fields.get("Stage"))
            pipeline = pipeline_ref(fields)
            account = lookup_id(fields.get("Account_Name"))
            if account:
                links = (Link(account, (("company_id", "company"),)),)
            contact = lookup_id(fields.get("Contact_Name"))
            if contact:
                deal_contact = Link(contact, (("contact_id", "contact"),))

    if rules:
        apply_field_rules(entity, fields, values, rules)
    values["custom"] = custom_fields(entity, fields)
    values["ext_modified_at"] = instant(fields.get("Modified_Time")) or modified_at
    if entity not in _ACTIVITY_TYPES:
        # ``crm_activities`` has no ``source`` column. The four others do, and
        # the CHECK allows ``import``.
        values["source"] = "import"
    return Mapped(
        entity=entity,
        table=table,
        values=values,
        links=links,
        status_name=status_name,
        deal_contact=deal_contact,
        conversion=conversion,
        owner_email=owner_email,
        owner_name=owner_name,
        pipeline=pipeline,
        derived=derived,
    )


# ── Field rules (CRM-Q7a) ───────────────────────────────────────────────────


@dataclass(frozen=True)
class RuleTarget:
    """One column that a field rule may fill (``crm_platform.md`` §18.7).

    ``standard_field`` is the Zoho field that the fixed map reads for the
    column. It is ``None`` for a free column, which the fixed map never writes.
    """

    column: str
    column_type: str
    standard_field: str | None = None
    #: The inclusive range of a number column, or ``None``. A value outside it
    #: gives NULL.
    limits: tuple[int, int] | None = None


#: ``probability`` is a percent. A custom score above 100 would make the
#: weighted forecast larger than the amount (CRM-Q7a review).
_LIMITS: Mapping[str, tuple[int, int]] = {"probability": (0, 100)}


def _targets(*rows: tuple[str, str, str | None]) -> Mapping[str, RuleTarget]:
    return {
        column: RuleTarget(column, kind, std, _LIMITS.get(column))
        for column, kind, std in rows
    }


#: The legal targets of a field rule: the free columns, then the fixed columns
#: that an org can leave empty. Exactly the two tables of §18.7, no more. The
#: column types are the types of migration 144.
FIELD_RULE_TARGETS: Mapping[str, Mapping[str, RuleTarget]] = {
    "company": _targets(
        ("linkedin_url", "TEXT", None),
        ("website", "TEXT", "Website"),
        ("industry", "TEXT", "Industry"),
        ("phone", "TEXT", "Phone"),
        ("email", "TEXT", "Email"),
        ("description", "TEXT", "Description"),
        ("no_of_employees", "INT", "Employees"),
        ("annual_revenue", "NUMERIC", "Annual_Revenue"),
    ),
    "contact": _targets(
        ("linkedin_url", "TEXT", None),
        ("email", "TEXT", "Email"),
        ("phone", "TEXT", "Phone"),
        ("mobile", "TEXT", "Mobile"),
        ("title", "TEXT", "Title"),
        ("description", "TEXT", "Description"),
    ),
    "lead": _targets(
        ("lost_note", "TEXT", None),
        ("website", "TEXT", "Website"),
        ("industry", "TEXT", "Industry"),
        ("email", "TEXT", "Email"),
        ("phone", "TEXT", "Phone"),
        ("mobile", "TEXT", "Mobile"),
        ("lead_source", "TEXT", "Lead_Source"),
        ("organization_name", "TEXT", "Company"),
        ("description", "TEXT", "Description"),
        ("no_of_employees", "INT", "No_of_Employees"),
        ("annual_revenue", "NUMERIC", "Annual_Revenue"),
    ),
    "deal": _targets(
        ("lost_note", "TEXT", None),
        ("next_step", "TEXT", "Next_Step"),
        ("description", "TEXT", "Description"),
        ("expected_close_date", "DATE", "Closing_Date"),
        ("probability", "SMALLINT", "Probability"),
    ),
}

#: The ``crm_field_defs.type`` words, and the column types each may fill. A
#: word that is not here (``multiselect``, ``lookup``, ``boolean``) targets
#: no column.
_RULE_COLUMN_TYPES: Mapping[str, frozenset[str]] = {
    **dict.fromkeys(
        ("text", "textarea", "email", "phone", "url", "select"), frozenset({"TEXT"}),
    ),
    # CRM-C4a: `decimal` and `percent` change together with `_FIELD_TYPES`.
    # Without them an approved rule on a Zoho double or percent field goes
    # `type_mismatch` after the next sync (crm_platform.md §20.7 item 1).
    **dict.fromkeys(
        ("number", "decimal", "percent", "currency"),
        frozenset({"NUMERIC", "INT", "SMALLINT"}),
    ),
    "date": frozenset({"DATE"}),
    "datetime": frozenset({"TIMESTAMPTZ"}),
}

#: Why a rule is not legal, or why an approved rule went stale.
RULE_UNKNOWN_TARGET = "unknown_target"
RULE_TYPE_MISMATCH = "type_mismatch"
RULE_STANDARD_FIELD_USED = "standard_field_on_layout"
RULE_LAYOUT_UNKNOWN = "layout_unknown"
RULE_FIELD_GONE = "field_gone"


def rule_target(entity: str, column: str | None) -> RuleTarget | None:
    """The target ``column`` of ``entity``, or ``None`` when it is not legal."""
    return (FIELD_RULE_TARGETS.get(entity) or {}).get(column or "")


def types_agree(field_type: str | None, column_type: str) -> bool:
    """True when a field of ``field_type`` may fill a column of ``column_type``."""
    return column_type in _RULE_COLUMN_TYPES.get(field_type or "", frozenset())


def coerce_rule_value(column_type: str, value: Any) -> Any:
    """A field value as the column type, with the helpers of this module.

    A value that does not coerce is ``None``, so the column gets NULL.
    """
    if column_type == "TEXT":
        return text_value(value)
    if column_type == "NUMERIC":
        return number(value)
    if column_type == "INT":
        return integer(value)
    if column_type == "SMALLINT":
        return integer(value, _SMALLINT_CEILING)
    if column_type == "DATE":
        return day(value)
    if column_type == "TIMESTAMPTZ":
        return instant(value)
    return None


def coerce_target_value(target: RuleTarget, value: Any) -> Any:
    """A field value for one target: :func:`coerce_rule_value`, then the
    target's ``limits``. A value outside the limits is ``None``."""
    coerced = coerce_rule_value(target.column_type, value)
    if coerced is not None and target.limits is not None:
        low, high = target.limits
        if not low <= coerced <= high:
            return None
    return coerced


def apply_field_rules(
    entity: str,
    fields: Mapping[str, Any],
    values: dict[str, Any],
    rules: Mapping[str, str],
) -> dict[str, Any]:
    """Fill each rule's column from its field, only where the fixed map gave
    NULL. ``rules`` is ``{target_column: field_api_name}``. A column that is
    not a legal target is skipped. *values* changes in place."""
    for column, field_api_name in rules.items():
        target = rule_target(entity, column)
        if target is None or values.get(column) is not None:
            continue
        values[column] = coerce_target_value(target, fields.get(field_api_name))
    return values


def rule_problem(
    entity: str,
    field_type: str | None,
    column: str | None,
    layout: Collection[str] | None,
) -> str | None:
    """Why a rule from a field of ``field_type`` to ``column`` is not legal, or
    ``None`` when it is (§18.7 "The legal targets").

    ``layout`` holds the field API names on the active layouts of the entity
    (:func:`entity_layout_fields`), or ``None`` when no layout was read. A
    fixed column opens only when its standard field is off every layout, so an
    unknown layout keeps it closed.
    """
    target = rule_target(entity, column)
    if target is None:
        return RULE_UNKNOWN_TARGET
    if not types_agree(field_type, target.column_type):
        return RULE_TYPE_MISMATCH
    if target.standard_field is not None:
        if layout is None:
            return RULE_LAYOUT_UNKNOWN
        if target.standard_field in layout:
            return RULE_STANDARD_FIELD_USED
    return None


def rule_stale_reason(
    entity: str,
    field_api_name: str,
    column: str | None,
    field_types: Mapping[str, str] | None,
    layout: Collection[str] | None,
) -> str | None:
    """Why an approved rule goes ``stale`` after a discovery, or ``None``.

    ``field_types`` holds the type of each field of the entity, as the
    discovery read it (:func:`discovered_field_types`), or ``None`` when the
    discovery could not read the fields. ``layout`` is the same for the
    layouts. A section that was not read never makes a rule stale.
    """
    target = rule_target(entity, column)
    if target is None:
        return RULE_UNKNOWN_TARGET
    if field_types is not None:
        if field_api_name not in field_types:
            return RULE_FIELD_GONE
        if not types_agree(field_types[field_api_name], target.column_type):
            return RULE_TYPE_MISMATCH
    if (
        target.standard_field is not None and layout is not None
        and target.standard_field in layout
    ):
        return RULE_STANDARD_FIELD_USED
    return None


def layout_fields(
    layouts: Mapping[str, list[Mapping[str, Any]]],
) -> dict[str, list[str]]:
    """For each known entity, the field API names on at least one active layout.

    *layouts* is the raw discovery read, keyed by module. A layout with no
    ``status`` counts as active. An entity whose layouts were not read is
    absent, so a caller cannot take "not read" for "not on a layout".
    """
    out: dict[str, list[str]] = {}
    for module, rows in layouts.items():
        entity = _MODULE_ENTITY.get(module)
        if entity is None or not isinstance(rows, list):
            continue
        names: set[str] = set()
        for layout in rows:
            if not isinstance(layout, Mapping):
                continue
            if (text_value(layout.get("status")) or "active").lower() != "active":
                continue
            for section in layout.get("sections") or []:
                if not isinstance(section, Mapping):
                    continue
                for item in section.get("fields") or []:
                    name = text_value(item.get("api_name")) if isinstance(item, Mapping) else None
                    if name:
                        names.add(name)
        out[entity] = sorted(names)
    return dict(sorted(out.items()))


def entity_layout_fields(
    profile: Mapping[str, Any] | None, entity: str,
) -> frozenset[str] | None:
    """The ``layout_fields`` of one entity in a profile, or ``None`` when the
    profile does not know them (no profile, a profile from before CRM-Q7a, or
    a layout read that failed)."""
    stored = (profile or {}).get("layout_fields")
    if not isinstance(stored, Mapping) or entity not in stored:
        return None
    return frozenset(str(name) for name in stored.get(entity) or ())


def discovered_field_types(raw: Mapping[str, Any]) -> dict[str, dict[str, str]]:
    """``{entity: {field_api_name: type}}`` of the fields that go to ``custom``,
    from a raw discovery read (the profile's ``custom_field_types``).

    The type is the ``crm_field_defs.type`` word. A field with a canonical
    column and a ``$`` field are left out, as :func:`map_field_def` leaves
    them out. An entity whose fields were not read is absent.
    """
    out: dict[str, dict[str, str]] = {}
    for module, rows in (raw.get("fields") or {}).items():
        entity = _MODULE_ENTITY.get(module)
        if entity is None or not isinstance(rows, list):
            continue
        out[entity] = dict(sorted(
            (name, _FIELD_TYPES.get(text_value(d.get("data_type")) or "", "text"))
            for d in rows
            if isinstance(d, Mapping)
            and (name := text_value(d.get("api_name")))
            and not name.startswith("$") and name not in _CONSUMED[entity]
        ))
    return dict(sorted(out.items()))


def entity_field_types(
    profile: Mapping[str, Any] | None, entity: str,
) -> dict[str, str] | None:
    """The ``custom_field_types`` of one entity in a profile, or ``None`` when
    the profile does not know them."""
    stored = (profile or {}).get("custom_field_types")
    if not isinstance(stored, Mapping) or not isinstance(stored.get(entity), Mapping):
        return None
    return {str(k): str(v) for k, v in stored[entity].items()}


# ── Field definitions ───────────────────────────────────────────────────────

#: Zoho ``data_type`` to the type word of ``crm_field_defs``.
_FIELD_TYPES = {
    "text": "text",
    "textarea": "textarea",
    "email": "email",
    "phone": "phone",
    "website": "url",
    "integer": "number",
    "bigint": "number",
    # CRM-C4a (§20.7 item 1): a Zoho double is a decimal and a percent is a
    # percent, the words of the field studio. The next sync updates the rows.
    "double": "decimal",
    "currency": "currency",
    "percent": "percent",
    "boolean": "boolean",
    "date": "date",
    "datetime": "datetime",
    "picklist": "select",
    "multiselectpicklist": "multiselect",
    "lookup": "lookup",
    "ownerlookup": "lookup",
}


def map_field_def(
    entity: str, definition: Mapping[str, Any], position: int,
) -> dict[str, Any] | None:
    """One ``settings/fields`` row as a ``crm_field_defs`` row, or ``None``.

    Only a field that lives in ``custom`` gets a row. A field with a canonical
    column is shown by its column, and a ``$`` system field is not a field.
    """
    api_name = text_value(definition.get("api_name"))
    if not api_name or api_name.startswith("$") or api_name in _CONSUMED[entity]:
        return None
    return _field_def_row(entity, api_name, definition, position)


def _field_def_row(
    entity: str, api_name: str, definition: Mapping[str, Any], position: int,
) -> dict[str, Any]:
    """The ``crm_field_defs`` columns of one field. One rule for a known entity
    and a generic module."""
    options = [
        {
            "label": text_value(o.get("display_value")) or text_value(o.get("actual_value")),
            "value": text_value(o.get("actual_value")) or text_value(o.get("display_value")),
        }
        for o in (definition.get("pick_list_values") or [])
        if isinstance(o, dict)
    ]
    sequence = integer(definition.get("sequence_number"))
    return {
        "entity": entity,
        "key": api_name,
        "label": text_value(definition.get("field_label")) or api_name,
        "type": _FIELD_TYPES.get(text_value(definition.get("data_type")) or "", "text"),
        "options": options or None,
        "provider_api_name": api_name,
        "position": sequence if sequence is not None else position,
        "visible": definition.get("visible") is not False,
    }


# ── Pipelines (CRM-Q2) ──────────────────────────────────────────────────────


def _name_of(value: Any) -> str | None:
    """A name from a plain string or from a ``{"name": ...}`` lookup object."""
    if isinstance(value, dict):
        return text_value(value.get("name") or value.get("display_value"))
    return text_value(value)


def pipeline_ref(fields: Mapping[str, Any]) -> tuple[str | None, str | None]:
    """``(ext_id, name)`` of the pipeline that a deal names.

    A live tenant sends ``Pipeline`` as a name string (``"Standard"``) or as an
    object (``{"id": "...", "name": "Standard"}``). Both give a name, and the
    object gives the id too. A deal with none names no pipeline, so the engine
    falls back to the default pipeline.
    """
    value = fields.get("Pipeline")
    ext_id = text_value(value.get("id")) if isinstance(value, dict) else None
    return ext_id, _name_of(value)


def forecast_category_of(entry: Mapping[str, Any]) -> str | None:
    """The forecast category of a stage entry, from a picklist item or a
    pipeline map. Zoho sends an object (``{"name": ...}``) or a plain string."""
    forecast = entry.get("forecast_category")
    if isinstance(forecast, dict):
        return text_value(forecast.get("name") or forecast.get("display_value"))
    return text_value(forecast)


_forecast_name = forecast_category_of


def stage_type_hint(entry: Mapping[str, Any]) -> str | None:
    """``won`` or ``lost`` when the stage entry says so, else ``None``.

    Zoho puts the word in ``forecast_type`` ("Closed Won") on a pipeline map
    and in ``forecast_category`` on a picklist item. The engine falls back to
    its own name guess for ``None``.
    """
    label = " ".join(
        part.lower() for part in (
            text_value(entry.get("forecast_type")), _forecast_name(entry),
        ) if part
    )
    if "won" in label:
        return "won"
    if "lost" in label:
        return "lost"
    return None


def pipeline_stages(pipeline: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The stages of one pipeline in Zoho's order: ``name``, ``sequence``,
    ``forecast_category`` and ``type`` (``won``, ``lost`` or ``None``).

    The key is ``maps`` (what the API documents). ``stages`` is read too, for
    the same reason the adapter reads two keys for the pipelines.
    """
    raw: list[Any] = []
    for key in ("maps", "stages"):
        found = pipeline.get(key)
        if isinstance(found, list):
            raw = found
            break
    out: list[dict[str, Any]] = []
    for index, entry in enumerate(raw):
        if not isinstance(entry, dict):
            continue
        name = text_value(entry.get("actual_value") or entry.get("display_value"))
        if not name or name == "-None-":
            continue
        sequence = integer(entry.get("sequence_number"))
        out.append({
            "name": name,
            "sequence": sequence if sequence is not None else index + 1,
            "forecast_category": _forecast_name(entry),
            "type": stage_type_hint(entry),
        })
    return sorted(out, key=lambda s: s["sequence"])


def pipeline_rows(pipelines: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The discovered pipelines as profile rows, in Zoho's order.

    Exactly one row has ``default`` true: the first pipeline that Zoho marks as
    the default, or the first pipeline when none is marked. A name that
    repeats across layouts stays, because the ``ext_id`` tells them apart.
    """
    rows: list[dict[str, Any]] = []
    for pipeline in pipelines:
        ext_id = text_value(pipeline.get("id"))
        name = text_value(pipeline.get("display_value") or pipeline.get("actual_value"))
        if not ext_id or not name:
            continue
        rows.append({
            "ext_id": ext_id,
            "name": name,
            "layout_ext_id": text_value(pipeline.get("layout_id")),
            "default": pipeline.get("default") is True,
            "stages": pipeline_stages(pipeline),
        })
    chosen = next((i for i, r in enumerate(rows) if r["default"]), 0)
    for index, row in enumerate(rows):
        row["default"] = index == chosen
    return rows


# ── The source profile (CRM-Q1) ─────────────────────────────────────────────

#: The known modules of ``crm_platform.md`` §18.3 step 2. Each has a table of
#: its own, and the fixed rules map it. A known module in the profile is always
#: ``mirrored``. Tasks joined with CRM-Q5.
MIRRORED_MODULES: frozenset[str] = frozenset(
    {"Leads", "Contacts", "Accounts", "Deals", "Notes", "Calls", "Events", "Tasks"}
)

#: The standard modules that the generic record store mirrors, in the order of
#: ``crm_platform.md`` §18.7 (CRM-Q6a). The custom modules follow, in
#: alphabetical order.
GENERIC_STANDARD_MODULES: tuple[str, ...] = (
    "Products", "Quotes", "Sales_Orders", "Purchase_Orders", "Invoices",
    "Vendors", "Price_Books", "Cases", "Solutions", "Campaigns",
)

#: The most generic modules the sync mirrors. Sync health lists the rest as
#: "not mirrored".
GENERIC_MODULE_CAP = 25

#: The ``generated_type`` values a generic module may have. A ``linking``
#: module and a ``subform`` are never mirrored.
_GENERIC_TYPES = frozenset({"default", "custom"})

#: The prefix of a generic module's entity, in the cursors, the counts and
#: ``crm_field_defs.entity`` (``module:Products``).
MODULE_ENTITY_PREFIX = "module:"


def module_entity(api_name: str) -> str:
    """The entity of a generic module: ``module:<ApiName>``."""
    return f"{MODULE_ENTITY_PREFIX}{api_name}"


def module_of(entity: str) -> str | None:
    """The API name of a generic module entity, or ``None`` for any other."""
    if entity.startswith(MODULE_ENTITY_PREFIX):
        return entity[len(MODULE_ENTITY_PREFIX):] or None
    return None


def module_read_scope(api_name: str, *, custom: bool) -> str:
    """The OAuth scope that reads a generic module.

    A standard module takes ``ZohoCRM.modules.<name>.READ``, with the API name
    in lower case and with no underscore (``Sales_Orders`` gives
    ``salesorders``). Each custom module takes ``ZohoCRM.modules.custom.READ``.
    Zoho's published scope list for API v2 and v8 holds each of these names
    (checked 2026-10-12).
    """
    if custom:
        return "ZohoCRM.modules.custom.READ"
    return f"ZohoCRM.modules.{api_name.lower().replace('_', '')}.READ"


def _generic_candidate(row: Mapping[str, Any]) -> bool:
    """The first four rules of §18.7 "Which modules". The scope is the fifth."""
    api_name = row.get("api_name")
    kind = row.get("generated_type")
    return (
        row.get("api_supported") is True
        and kind in _GENERIC_TYPES
        and api_name not in MIRRORED_MODULES
        and (api_name in GENERIC_STANDARD_MODULES or kind == "custom")
    )


def _generic_rank(row: Mapping[str, Any]) -> tuple[int, str]:
    api_name = str(row.get("api_name"))
    if api_name in GENERIC_STANDARD_MODULES:
        return GENERIC_STANDARD_MODULES.index(api_name), ""
    return len(GENERIC_STANDARD_MODULES), api_name


def generic_modules(profile: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """The generic modules of a profile that the sync takes, in sync order.

    Each row has ``generic`` true. ``mirrored`` is true when the connection
    holds the module's read scope. A row with ``mirrored`` false and a
    ``scope`` waits for a reconnect, and the sync counts it as unavailable.
    """
    rows = [
        dict(m) for m in (profile or {}).get("modules") or []
        if isinstance(m, Mapping) and m.get("generic")
    ]
    return sorted(rows, key=_generic_rank)

#: The editions that Zoho names in ``license_details.paid_type``.
EDITIONS: tuple[str, ...] = ("free", "standard", "professional", "enterprise", "ultimate")

#: Credits per 24 hours, by edition: ``(base, per licence, cap)``. ``cap`` is
#: ``None`` for no cap. Source: ``crm_platform.md`` §5.2 row 6.
EDITION_CREDITS: Mapping[str, tuple[int, int, int | None]] = {
    "free": (5_000, 0, None),
    "standard": (50_000, 250, 100_000),
    "professional": (50_000, 500, 3_000_000),
    "enterprise": (50_000, 1_000, 5_000_000),
    "ultimate": (50_000, 2_000, None),
}


def daily_credits(edition: str | None, licences: int | None = None) -> int | None:
    """The API credits that an edition gets in 24 hours, or ``None`` when the
    edition is not known. ``licences`` is the count of paid licences. With none
    given, an edition gets its base."""
    plan = EDITION_CREDITS.get((edition or "").strip().lower())
    if plan is None:
        return None
    base, per_licence, cap = plan
    total = base + per_licence * max(int(licences or 0), 0)
    return min(total, cap) if cap is not None else total


def edition_of(org: Mapping[str, Any] | None) -> str | None:
    """The edition from a ``GET org`` row, or ``None`` when it is not known.

    ``license_details.paid_type`` names the edition of a paid org. An org that is
    not paid and has no trial is on Free. A trial or an edition this list does
    not know (CRM Plus, for example) is ``None``, so the budget keeps its floor.
    """
    if not isinstance(org, Mapping):
        return None
    details = org.get("license_details")
    if not isinstance(details, Mapping):
        return None
    paid_type = (text_value(details.get("paid_type")) or "").lower()
    if details.get("paid") is True and paid_type in EDITIONS:
        return paid_type
    if details.get("paid") is False and not text_value(details.get("trial_type")):
        return "free"
    return paid_type if paid_type in EDITIONS else None


def _org_summary(org: Mapping[str, Any] | None) -> dict[str, Any]:
    org = org or {}
    details = org.get("license_details")
    licences = (
        integer(details.get("users_license_purchased"))
        if isinstance(details, Mapping) else None
    )
    multi = org.get("mc_status")
    return {
        # CRM-X0: the Zoho org id. A new consent compares it (``org_identity``).
        "id": text_value(org.get("id")),
        "edition": edition_of(org),
        "currency": text_value(org.get("iso_currency") or org.get("currency")),
        "multi_currency": multi if isinstance(multi, bool) else None,
        "time_zone": text_value(org.get("time_zone")),
        "domain_name": text_value(org.get("domain_name")),
        "licences": licences,
    }


def org_identity(org: Mapping[str, Any] | None) -> dict[str, str | None]:
    """The id and the domain name of a Zoho org (CRM-X0).

    *org* is a raw ``GET org`` row or the ``org`` section of a stored profile.
    Both carry the same two keys. A profile from before CRM-X0 has the domain
    name only.
    """
    org = org if isinstance(org, Mapping) else {}
    return {
        "id": text_value(org.get("id")),
        "domain_name": text_value(org.get("domain_name")),
    }


def same_org(
    stored: Mapping[str, Any] | None, fresh: Mapping[str, Any] | None,
) -> bool | None:
    """Is *fresh* the Zoho org that *stored* names? ``None`` when nothing tells.

    The id decides when both sides have one. Else the domain name decides,
    because Zoho gives each org its own domain name. With neither on both
    sides, the answer is ``None``, and the caller decides.
    """
    a, b = org_identity(stored), org_identity(fresh)
    for key in ("id", "domain_name"):
        if a[key] and b[key]:
            return a[key] == b[key]
    return None


def _module_rows(
    modules: list[Mapping[str, Any]], scopes: frozenset[str] = frozenset(),
) -> list[dict[str, Any]]:
    """The module list of the profile.

    A known module is ``mirrored``. A generic module (CRM-Q6a) passes the five
    rules of ``crm_platform.md`` §18.7: the API supports it, it is ``default``
    or ``custom`` (never ``linking`` or ``subform``), it is not a known module,
    it is one of :data:`GENERIC_STANDARD_MODULES` or a custom module, and
    *scopes* holds its read scope. At most :data:`GENERIC_MODULE_CAP` of them,
    in the order of :func:`generic_modules`, are taken (``generic``). A taken
    module whose scope is missing has ``mirrored`` false and keeps the
    ``scope`` it waits for.
    """
    rows: list[dict[str, Any]] = []
    for module in modules:
        api_name = text_value(module.get("api_name"))
        if not api_name:
            continue
        rows.append({
            "api_name": api_name,
            "tab": text_value(module.get("module_name")) or api_name,
            "label": text_value(module.get("plural_label")) or api_name,
            "singular_label": text_value(module.get("singular_label")),
            "custom": text_value(module.get("generated_type")) == "custom",
            "generated_type": text_value(module.get("generated_type")),
            "api_supported": module.get("api_supported") is True,
            "visible": module.get("visible") is not False,
            "mirrored": api_name in MIRRORED_MODULES,
        })
    taken = sorted((r for r in rows if _generic_candidate(r)), key=_generic_rank)
    for row in taken[:GENERIC_MODULE_CAP]:
        scope = module_read_scope(row["api_name"], custom=row["custom"])
        row["generic"] = True
        row["scope"] = scope
        row["mirrored"] = scope in scopes
    return sorted(rows, key=lambda r: r["api_name"])


def with_module_fields(
    profile: dict[str, Any],
    fields: Mapping[str, list[Mapping[str, Any]]],
    previous: Mapping[str, Any] | None = None,
    pending: set[str] | frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Put the display field of each generic module into the profile.

    *fields* maps a module API name to its ``settings/fields`` rows. The field
    with ``display_field`` true names the record (``crm_module_records.name``).
    A module whose fields were not read keeps the display field of *previous*.
    With none, ``display_field`` is ``None`` and the name stays NULL.

    *pending* names the modules whose read waited for credits. Such a module
    whose fields were never read gets ``fields_pending``. The sync reads no
    record of it, and the next discovery reads its fields.
    """
    previous_rows = {
        m.get("api_name"): m
        for m in (previous or {}).get("modules") or [] if isinstance(m, Mapping)
    }
    old = {name: m.get("display_field") for name, m in previous_rows.items()}
    for row in profile.get("modules") or []:
        if not row.get("generic"):
            continue
        api_name = row["api_name"]
        before = previous_rows.get(api_name) or {}
        # An unmirrored row (no scope yet) also carries `display_field: None`,
        # so only a module that was mirrored had its fields read.
        read_before = bool(before.get("mirrored")) and not before.get("fields_pending")
        row["fields_pending"] = api_name in pending and not read_before
        if api_name in fields:
            row["display_field"] = next(
                (
                    text_value(f.get("api_name")) for f in fields[api_name]
                    if isinstance(f, Mapping) and f.get("display_field") is True
                ),
                None,
            )
        else:
            row["display_field"] = old.get(api_name)
    return profile


def _lookup_module(definition: Mapping[str, Any]) -> str | None:
    """The module a lookup field points at. v8 sends ``lookup.module`` as an
    object with ``api_name``, and v2 as a plain name."""
    lookup = definition.get("lookup")
    if not isinstance(lookup, Mapping):
        return None
    target = lookup.get("module")
    if isinstance(target, Mapping):
        return text_value(target.get("api_name"))
    return text_value(target)


def map_module_field_def(
    api_name: str, definition: Mapping[str, Any], position: int,
) -> dict[str, Any] | None:
    """One ``settings/fields`` row of a generic module as a ``crm_field_defs``
    row with ``entity = 'module:<ApiName>'``, or ``None`` (CRM-Q6a).

    Every field of the module gets a row, because every value lives in
    ``fields``. A lookup keeps its target in ``options`` as
    ``{"lookup_module": "<ApiName>"}``, so CRM-Q6b can resolve the link.
    """
    key = text_value(definition.get("api_name"))
    if not key or key.startswith("$"):
        return None
    row = _field_def_row(module_entity(api_name), key, definition, position)
    target = _lookup_module(definition)
    if row["type"] == "lookup" and target:
        row["options"] = {"lookup_module": target}
    return row


def module_record_link(
    provider_meta: Mapping[str, Any], tab: str | None, ext_id: str,
) -> str | None:
    """The deep link of a generic record (CRM-Q1 rules), or ``None``.

    *provider_meta* is the connection's registry row: ``accounts_server`` and
    the ``domain_name`` that discovery stores. *tab* is the module's tab.
    """
    from gateway.crm_sources.zoho import links

    return links.module_link(
        text_value(provider_meta.get("accounts_server")),
        text_value(provider_meta.get("domain_name")),
        tab, ext_id,
    )


def map_module_record(
    api_name: str,
    fields: Mapping[str, Any],
    *,
    modified_at: datetime | None,
    owners: Mapping[str, str],
    display_field: str | None,
) -> dict[str, Any]:
    """One record of a generic module as the columns of ``crm_module_records``.

    Pure, like :func:`map_record`. ``fields`` keeps each value keyed by its
    API name, as ``custom`` does: a ``$`` system key is dropped, and ``id`` is
    the ``ext_id``. A lookup stays as Zoho sends it (``{id, name}``). ``name``
    is the value of the display field, or ``None``. ``owner_email`` is the
    owner's email, which the engine keeps only for a member.
    """
    owner_email, _owner_name = _owner(fields, owners)
    shown = fields.get(display_field) if display_field else None
    return {
        "name": _name_of(shown) if isinstance(shown, dict) else text_value(shown),
        "owner_email": owner_email,
        "fields": {
            key: value for key, value in fields.items()
            if key != "id" and not key.startswith("$")
        },
        "ext_created_at": instant(fields.get("Created_Time")),
        "ext_modified_at": instant(fields.get("Modified_Time")) or modified_at,
    }


def _layout_rows(
    layouts: Mapping[str, list[Mapping[str, Any]]],
) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for module, rows in sorted(layouts.items()):
        summary = []
        for layout in rows:
            sections = [s for s in layout.get("sections") or [] if isinstance(s, dict)]
            summary.append({
                "id": text_value(layout.get("id")),
                "name": text_value(layout.get("name")),
                "status": text_value(layout.get("status")),
                "sections": len(sections),
                "fields": sum(
                    len([f for f in s.get("fields") or [] if isinstance(f, dict)])
                    for s in sections
                ),
            })
        out[module] = sorted(summary, key=lambda r: r["id"] or "")
    return out


def _field_counts(
    fields: Mapping[str, list[Mapping[str, Any]]],
) -> dict[str, dict[str, int]]:
    return {
        module: {
            "total": len(rows),
            "custom": sum(1 for f in rows if f.get("custom_field") is True),
        }
        for module, rows in sorted(fields.items())
    }


def build_profile(
    raw: Mapping[str, Any],
    previous: Mapping[str, Any] | None = None,
    scopes: frozenset[str] | tuple[str, ...] = (),
) -> dict[str, Any]:
    """The source profile from the raw discovery (``ZohoSource.discover``).

    *scopes* are the scopes of the connection. They decide which generic
    module is ``mirrored`` (CRM-Q6a, :func:`_module_rows`).

    **Metadata only.** The profile holds the edition, the currency, the module
    list, a summary of each layout, the count of fields, and the pipelines with
    their stages. It holds no record, no token and no company name.

    A section that the read could not get (``raw["failed"]``) is copied from
    ``previous``, so one failed call does not erase the edition or start a new
    version. With no ``previous`` it stays empty.
    """
    failed = set(raw.get("failed") or ())
    old = previous or {}

    def pick(section: str, fresh: Any) -> Any:
        return old.get(section) if section in failed and section in old else fresh

    return {
        "org": pick("org", _org_summary(raw.get("org"))),
        "modules": pick(
            "modules", _module_rows(raw.get("modules") or [], frozenset(scopes)),
        ),
        "layouts": pick("layouts", _layout_rows(raw.get("layouts") or {})),
        "fields": pick("fields", _field_counts(raw.get("fields") or {})),
        "pipelines": pick("pipelines", pipeline_rows(raw.get("pipelines") or [])),
        # CRM-Q7a: for each known entity, the fields on an active layout. A
        # failed layout read keeps the previous list, as ``pick`` does.
        "layout_fields": (
            old.get("layout_fields")
            if "layouts" in failed and "layout_fields" in old
            else layout_fields(raw.get("layouts") or {})
        ),
        # CRM-Q7a: the type of each field that goes to ``custom``, so a new
        # profile version can make a field rule stale.
        "custom_field_types": (
            old.get("custom_field_types")
            if "fields" in failed and "custom_field_types" in old
            else discovered_field_types(raw)
        ),
        # The sections the read could not get. A section named here is the
        # copy of the previous profile, or empty.
        "unreadable": sorted(failed),
        # The scope set the module rows were built with (CRM-Q6a). A connection
        # whose scopes differ makes the next discovery due.
        "scope_set": scope_set_key(scopes),
    }


def scope_set_key(scopes: frozenset[str] | tuple[str, ...] | list[str]) -> str:
    """A stable key of a scope set: order and repeats do not change it."""
    import hashlib

    joined = "\n".join(sorted({str(s) for s in scopes}))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def profile_tabs(profile: Mapping[str, Any]) -> dict[str, str]:
    """Module API name to tab, for the four modules that have a deep link."""
    wanted = {"Leads", "Contacts", "Accounts", "Deals"}
    return {
        m["api_name"]: m["tab"]
        for m in profile.get("modules") or []
        if isinstance(m, dict) and m.get("api_name") in wanted and m.get("tab")
    }


__all__ = [
    "EDITIONS",
    "EDITION_CREDITS",
    "ENTITY_TABLES",
    "FIELD_RULE_TARGETS",
    "GENERIC_MODULE_CAP",
    "GENERIC_STANDARD_MODULES",
    "MIRRORED_MODULES",
    "MODULE_ENTITY_PREFIX",
    "OWNER_NAME_KEY",
    "RULE_FIELD_GONE",
    "RULE_LAYOUT_UNKNOWN",
    "RULE_STANDARD_FIELD_USED",
    "RULE_TYPE_MISMATCH",
    "RULE_UNKNOWN_TARGET",
    "Link",
    "Mapped",
    "RuleTarget",
    "apply_field_rules",
    "build_profile",
    "coerce_rule_value",
    "coerce_target_value",
    "custom_field_keys",
    "custom_fields",
    "daily_credits",
    "discovered_field_types",
    "edition_of",
    "entity_field_types",
    "entity_layout_fields",
    "forecast_category_of",
    "generic_modules",
    "layout_fields",
    "map_field_def",
    "map_module_field_def",
    "map_module_record",
    "map_record",
    "module_entity",
    "module_of",
    "module_read_scope",
    "module_record_link",
    "org_identity",
    "pipeline_ref",
    "pipeline_rows",
    "pipeline_stages",
    "profile_tabs",
    "rule_problem",
    "rule_stale_reason",
    "rule_target",
    "same_org",
    "scope_set_key",
    "stage_type_hint",
    "types_agree",
    "with_module_fields",
]
