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

from collections.abc import Mapping
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

_ACTIVITY_TYPES = {"note": "note", "call": "call", "meeting": "meeting"}

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
    owner_email: str | None = None
    owner_name: str | None = None
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
) -> Mapped:
    """Map one Zoho record of ``entity`` (a canonical entity name).

    ``owners`` maps a Zoho user id to its email, for a record whose ``Owner``
    has no email of its own.
    """
    if entity not in ENTITY_TABLES:
        raise ValueError(f"No Zoho mapping for the entity {entity!r}")
    table = ENTITY_TABLES[entity]
    owner_email, owner_name = _owner(fields, owners)
    links: tuple[Link, ...] = ()
    status_name: str | None = None
    deal_contact: Link | None = None
    derived: tuple[str, ...] = ()

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
        elif entity == "deal":
            status_name = text_value(fields.get("Stage"))
            account = lookup_id(fields.get("Account_Name"))
            if account:
                links = (Link(account, (("company_id", "company"),)),)
            contact = lookup_id(fields.get("Contact_Name"))
            if contact:
                deal_contact = Link(contact, (("contact_id", "contact"),))

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
        owner_email=owner_email,
        owner_name=owner_name,
        derived=derived,
    )


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
    "double": "number",
    "currency": "currency",
    "percent": "number",
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


__all__ = [
    "ENTITY_TABLES",
    "OWNER_NAME_KEY",
    "Link",
    "Mapped",
    "custom_field_keys",
    "custom_fields",
    "map_field_def",
    "map_record",
]
