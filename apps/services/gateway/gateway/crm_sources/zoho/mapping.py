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
    }),
    "deal": frozenset({
        "id", "Deal_Name", "Amount", "Probability", "Closing_Date", "Next_Step",
        "Description", "Stage", "Account_Name", "Contact_Name", "Currency",
        "Exchange_Rate", "Owner", "Modified_Time", "Pipeline",
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
        elif entity == "deal":
            status_name = text_value(fields.get("Stage"))
            pipeline = pipeline_ref(fields)
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
        pipeline=pipeline,
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

#: The modules this mapping reads records from. A module in the profile is
#: ``mirrored`` when its API name is here.
MIRRORED_MODULES: frozenset[str] = frozenset(
    {"Leads", "Contacts", "Accounts", "Deals", "Notes", "Calls", "Events"}
)

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
        "edition": edition_of(org),
        "currency": text_value(org.get("iso_currency") or org.get("currency")),
        "multi_currency": multi if isinstance(multi, bool) else None,
        "time_zone": text_value(org.get("time_zone")),
        "domain_name": text_value(org.get("domain_name")),
        "licences": licences,
    }


def _module_rows(modules: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
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
    return sorted(rows, key=lambda r: r["api_name"])


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
    raw: Mapping[str, Any], previous: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """The source profile from the raw discovery (``ZohoSource.discover``).

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
        "modules": pick("modules", _module_rows(raw.get("modules") or [])),
        "layouts": pick("layouts", _layout_rows(raw.get("layouts") or {})),
        "fields": pick("fields", _field_counts(raw.get("fields") or {})),
        "pipelines": pick("pipelines", pipeline_rows(raw.get("pipelines") or [])),
        # The sections the read could not get. A section named here is the
        # copy of the previous profile, or empty.
        "unreadable": sorted(failed),
    }


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
    "MIRRORED_MODULES",
    "OWNER_NAME_KEY",
    "Link",
    "Mapped",
    "build_profile",
    "custom_field_keys",
    "custom_fields",
    "daily_credits",
    "edition_of",
    "forecast_category_of",
    "map_field_def",
    "map_record",
    "pipeline_ref",
    "pipeline_rows",
    "pipeline_stages",
    "profile_tabs",
    "stage_type_hint",
]
