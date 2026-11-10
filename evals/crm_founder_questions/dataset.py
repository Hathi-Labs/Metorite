"""The synthetic CRM of the founder question set (WS-53 CRM-Q30).

Spec: ``project-docs/specs/crm_platform.md`` §18.11.2, CRM-Q30 items 1 to 4.

:func:`build` makes one CRM in the shape of DEWiN after its import, from the
fixed seed :data:`SEED`, so each call gives the same rows. An agent invented
each name, place and number. No row comes from a real customer.

The shape:

* Custom modules: ``Company``, ``Clients``, ``Operator_Associates``,
  ``Invoices``, ``Payment_Received``, ``Payment_Made`` and ``Commercials``.
  ``Commercials`` has fields and no record, as DEWiN's does.
* Lookups: a client names its company. An invoice names its client. A
  payment received names its invoice and its client. A payment made names
  its operator associate.
* Values in Zoho's shapes: a currency is a JSON number in INR, and a few
  arrive as text. A ``date`` is ``YYYY-MM-DD``. A ``datetime`` is ISO with
  the offset ``+05:30``, and a few arrive in UTC with ``Z``, near midnight in
  India, so the CRM zone changes their day.
* ``Company.Annual_Revenue`` is empty in every row. ``Invoices.Discount``,
  ``Payment_Received.TDS_Deducted`` and some text fields are empty in some
  rows.
* The core Contacts carry the label "Existing Sites" in the profile, with
  three custom fields.
* Notes hang off ``Clients`` and ``Invoices`` records.
* The profile names the zone of the Zoho org, ``Asia/Kolkata``.

Each record keeps Zoho's created time in ``created`` (UTC). Some fall just
after midnight in India on the first day of a month, which is the last day of
the month before in UTC (CRM-Q25b).
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from functools import cache
from typing import Any
from zoneinfo import ZoneInfo

#: The seed of every random choice. A change makes a new dataset, and the
#: recorded route answers must be recorded again (README).
SEED = 20261017

#: "Today" for every question of the set. No due date falls on it, so "due
#: before today" and "due on or before today" give the same rows.
AS_OF = date(2026, 10, 15)

#: The first day of the data: the start of the Indian financial year 2025-26.
START = date(2025, 4, 1)

#: The zone of the Zoho org, and so the CRM zone of CRM-Q25b.
ZONE = "Asia/Kolkata"
IST = ZoneInfo(ZONE)

#: The members who own records. The domain is a test domain.
OWNERS: tuple[str, ...] = (
    "asha@founder-evals.test", "ravi@founder-evals.test", "meera@founder-evals.test",
)

#: Zoho counts more Payment_Made records than the connected user can read
#: (CRM-Q24). This many stay out of the mirror.
PAYMENT_MADE_HIDDEN = 58

_PLACE = ("Godavari", "Narmada", "Kaveri", "Tapti", "Krishna", "Mahanadi", "Sabari",
          "Pennar", "Tunga", "Bhima", "Indravati", "Sharavati")
_KIND = ("Waterworks", "Utilities", "Infra", "Process", "Agro", "Minerals")
_FIRST = ("Asha", "Ravi", "Meera", "Arjun", "Nisha", "Kiran", "Vikram", "Leela", "Tara", "Dev")
_LAST = ("Rao", "Iyer", "Shah", "Menon", "Das", "Patel", "Nair", "Gupta")
_CITIES = ("Pune", "Nagpur", "Indore", "Surat", "Kochi", "Mysore", "Nashik", "Vellore")

INDUSTRIES = ("Water", "Power", "Mining", "Cement")
STATES = ("Maharashtra", "Karnataka", "Gujarat", "Tamil Nadu", "Telangana")
SEGMENTS = ("Municipal", "Industrial", "Utility")
STATUSES = ("Paid", "Partially Paid", "Unpaid")
MODES = ("NEFT", "RTGS", "Cheque", "UPI")
PURPOSES = ("Service Fee", "Spares", "Travel")
RATINGS = ("A", "B", "C")

#: The phrase of the note search question. The notes that hold it, and only
#: those, are the answer of Q30.
PUMP_FAILURE = "pump failure"


@dataclass(frozen=True)
class FieldDef:
    """One row of ``crm_field_defs``. ``options`` holds picklist values."""

    key: str
    label: str
    type: str
    options: tuple[str, ...] = ()


@dataclass(frozen=True)
class Module:
    """One generic module of the profile and its field definitions."""

    api_name: str
    label: str
    singular: str
    fields: tuple[FieldDef, ...]


@dataclass
class Record:
    """One module record, or one core contact (``module`` is ``"Contacts"``)."""

    module: str
    ext_id: str
    name: str
    owner: str
    created: datetime
    fields: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Note:
    """One note on a module record, by the ext id of its record."""

    ext_id: str
    module: str
    record_ext_id: str
    subject: str
    body: str
    occurred: datetime


@dataclass
class Dataset:
    """The whole synthetic CRM."""

    modules: tuple[Module, ...]
    contact_fields: tuple[FieldDef, ...]
    records: dict[str, list[Record]]
    contacts: list[Record]
    notes: list[Note]
    zoho_counts: dict[str, int]

    def rows(self, module: str) -> list[Record]:
        return self.records[module]

    def by_id(self, module: str) -> dict[str, Record]:
        return {r.ext_id: r for r in self.records[module]}

    def size(self) -> int:
        return sum(len(v) for v in self.records.values()) + len(self.contacts) + len(self.notes)


def _f(key: str, ftype: str, *options: str, label: str | None = None) -> FieldDef:
    return FieldDef(key=key, label=label or key.replace("_", " "), type=ftype,
                    options=tuple(options))


MODULES: tuple[Module, ...] = (
    Module("Company", "Company", "Company", (
        _f("Name", "text", label="Company Name"),
        _f("Industry", "select", *INDUSTRIES),
        _f("HQ_State", "select", *STATES, label="HQ State"),
        _f("Annual_Revenue", "currency"),
        _f("Website", "url"),
    )),
    Module("Clients", "Clients", "Client", (
        _f("Name", "text", label="Client Name"),
        _f("Company", "lookup"),
        _f("Segment", "select", *SEGMENTS),
        _f("State", "select", *STATES),
        _f("Credit_Days", "number"),
        _f("Onboarded_On", "date"),
        _f("Key_Account", "boolean"),
        _f("GSTIN", "text"),
    )),
    Module("Operator_Associates", "Operator Associates", "Operator Associate", (
        _f("Name", "text", label="Operator Name"),
        _f("City", "text"),
        _f("Rating", "select", *RATINGS),
        _f("Phone", "phone"),
    )),
    Module("Invoices", "Invoices", "Invoice", (
        _f("Subject", "text", label="Invoice Number"),
        _f("Client", "lookup"),
        _f("Invoice_Date", "date"),
        _f("Due_Date", "date"),
        _f("Grand_Total", "currency"),
        _f("Discount", "currency"),
        _f("Balance_Due", "currency"),
        _f("Status", "select", *STATUSES),
        _f("Currency", "text"),
    )),
    Module("Payment_Received", "Payment Received", "Payment Received", (
        _f("Reference", "text"),
        _f("Invoice", "lookup"),
        _f("Client", "lookup"),
        _f("Amount", "currency"),
        _f("Received_On", "datetime", label="Received On"),
        _f("Mode", "select", *MODES),
        _f("TDS_Deducted", "currency", label="TDS Deducted"),
    )),
    Module("Payment_Made", "Payment Made", "Payment Made", (
        _f("Reference", "text"),
        _f("Operator", "lookup"),
        _f("Amount", "currency"),
        _f("Paid_On", "datetime", label="Paid On"),
        _f("Purpose", "select", *PURPOSES),
    )),
    Module("Commercials", "Commercials", "Commercial", (
        _f("Quote_Value", "currency", label="Quote Value"),
        _f("Valid_Till", "date", label="Valid Till"),
        _f("Client", "lookup"),
    )),
)

CONTACT_FIELDS: tuple[FieldDef, ...] = (
    _f("Site_State", "select", *STATES, label="Site State"),
    _f("Plant_Capacity_MLD", "number", label="Plant Capacity (MLD)"),
    _f("Commissioned_On", "date", label="Commissioned On"),
)


# ── The money and the times ─────────────────────────────────────────────────


def money(rng: random.Random, low: int, high: int) -> Decimal:
    """A sum in rupees with paise, so no two sums tie."""
    return Decimal(rng.randint(low * 100, high * 100)) / 100


def json_money(rng: random.Random, value: Decimal) -> Any:
    """A currency as Zoho sends it: a JSON number, or text one time in 12."""
    return f"{value:.2f}" if rng.random() < 1 / 12 else float(value)


def as_decimal(value: Any) -> Decimal | None:
    """A field value as a number, the way the route reads it: a JSON number,
    or text that is a plain decimal. Anything else is no value."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        return Decimal(str(value))
    if isinstance(value, str):
        text = value.strip()
        try:
            return Decimal(text) if text and text.replace(".", "", 1).isdigit() else None
        except ArithmeticError:
            return None
    return None


def ist_day(value: str) -> date:
    """The day of a Zoho datetime on the clock of the CRM zone."""
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(IST).date()


def created_ist(rec: Record) -> date:
    """The day of the record's Zoho created time in the CRM zone."""
    return rec.created.astimezone(IST).date()


def _moment(rng: random.Random, day: date, *, edge: bool) -> datetime:
    """A time of *day* in India. With *edge*, a time just after midnight,
    which is the day before in UTC."""
    if edge:
        clock = time(0, rng.randint(1, 59), rng.randint(0, 59))
    else:
        clock = time(rng.randint(9, 19), rng.randint(0, 59), rng.randint(0, 59))
    return datetime.combine(day, clock, tzinfo=IST)


def _zoho_time(rng: random.Random, moment: datetime) -> str:
    """A Zoho datetime: ``+05:30`` text, or one time in 6 UTC text with ``Z``."""
    if rng.random() < 1 / 6:
        return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return moment.isoformat(timespec="seconds")


def _day_between(rng: random.Random, first: date, last: date) -> date:
    return first + timedelta(days=rng.randint(0, (last - first).days))


def _lookup(rec: Record) -> dict[str, str]:
    return {"id": rec.ext_id, "name": rec.name}


def _ext(prefix: int, n: int) -> str:
    """A Zoho-like record id."""
    return f"{prefix}000000{n:06d}"


# ── The build ───────────────────────────────────────────────────────────────


def _companies(rng: random.Random) -> list[Record]:
    out = []
    for n in range(12):
        name = f"{_PLACE[n]} {_KIND[n % len(_KIND)]} Ltd"
        fields: dict[str, Any] = {
            "Name": name,
            "Industry": INDUSTRIES[n % len(INDUSTRIES)],
            "HQ_State": STATES[n % len(STATES)],
        }
        if n % 3:
            fields["Website"] = f"https://{_PLACE[n].lower()}.example.test"
        created = _moment(rng, _day_between(rng, date(2024, 1, 1), date(2025, 3, 31)), edge=False)
        out.append(Record("Company", _ext(4871, n + 1), name, OWNERS[n % 3], created, fields))
    return out


def _clients(rng: random.Random, companies: list[Record]) -> list[Record]:
    out = []
    for n in range(40):
        name = f"{_PLACE[n % 12]} {('North', 'South', 'East', 'West')[n // 12 % 4]} " \
               f"{('Water Board', 'Steel', 'Power', 'Textiles', 'Pharma')[n % 5]}"
        fields: dict[str, Any] = {
            "Name": name,
            "Company": _lookup(companies[n % len(companies)]),
            "Segment": SEGMENTS[rng.randrange(len(SEGMENTS))],
            "State": STATES[rng.randrange(len(STATES))],
            "Credit_Days": (30, 45, 60)[rng.randrange(3)],
            "Onboarded_On": _day_between(rng, date(2023, 1, 1), date(2025, 6, 30)).isoformat(),
            "Key_Account": rng.random() < 0.3,
        }
        if rng.random() < 0.7:
            fields["GSTIN"] = f"27ABCDE{1000 + n}F1Z{n % 10}"
        created = _moment(rng, date.fromisoformat(fields["Onboarded_On"]), edge=False)
        out.append(Record("Clients", _ext(4872, n + 1), name, OWNERS[rng.randrange(3)],
                          created, fields))
    return out


def _operators(rng: random.Random) -> list[Record]:
    out = []
    for n in range(15):
        name = f"{_FIRST[n % 10]} {_LAST[(n * 3) % 8]} Services"
        fields = {"Name": name, "City": _CITIES[n % len(_CITIES)],
                  "Rating": RATINGS[rng.randrange(3)], "Phone": f"+91 99000 {10000 + n}"}
        created = _moment(rng, _day_between(rng, date(2024, 6, 1), date(2025, 3, 31)), edge=False)
        out.append(Record("Operator_Associates", _ext(4873, n + 1), name,
                          OWNERS[n % 3], created, fields))
    return out


def _invoices_and_receipts(
    rng: random.Random, clients: list[Record],
) -> tuple[list[Record], list[Record]]:
    """The invoices, and the payments that settle them. Each invoice is paid
    in full, paid in part, or not paid, and its Status and Balance_Due agree
    with its payments."""
    invoices: list[Record] = []
    receipts: list[Record] = []
    # Some clients buy much more, so a top-N list has a clear order.
    weight = [rng.choice((1, 1, 1, 2, 3, 6)) for _ in clients]
    for n in range(260):
        client = rng.choices(clients, weights=weight)[0]
        day = _day_between(rng, START, AS_OF)
        due = day + timedelta(days=int(client.fields["Credit_Days"]))
        if due == AS_OF:
            due += timedelta(days=1)
        total = money(rng, 25_000, 900_000)
        subject = f"INV-{day:%y%m}-{n + 1:04d}"
        fields: dict[str, Any] = {
            "Subject": subject, "Client": _lookup(client),
            "Invoice_Date": day.isoformat(), "Due_Date": due.isoformat(),
            "Grand_Total": json_money(rng, total), "Currency": "INR",
        }
        if rng.random() < 0.3:
            fields["Discount"] = json_money(rng, (total * Decimal("0.05")).quantize(Decimal("0.01")))
        # One invoice in 9 is made just after midnight on the 1st of a month.
        edge = day.day == 1 or rng.random() < 1 / 9
        created = _moment(rng, day, edge=edge)
        inv = Record("Invoices", _ext(4874, n + 1), subject, OWNERS[rng.randrange(3)],
                     created, fields)
        invoices.append(inv)

        age = (AS_OF - day).days
        roll = rng.random()
        if age < 20 or roll < 0.18:
            parts: list[Decimal] = []
        elif roll < 0.38:
            parts = [(total * Decimal(rng.choice(("0.3", "0.5", "0.6")))).quantize(Decimal("0.01"))]
        elif roll < 0.6:
            first = (total * Decimal("0.4")).quantize(Decimal("0.01"))
            parts = [first, total - first]
        else:
            parts = [total]
        paid = Decimal(0)
        last = day
        for part in parts:
            last = min(AS_OF, last + timedelta(days=rng.randint(5, 50)))
            when = _moment(rng, last, edge=rng.random() < 0.12)
            ref = f"PR-{len(receipts) + 1:05d}"
            pr_fields: dict[str, Any] = {
                "Reference": ref, "Invoice": _lookup(inv), "Client": _lookup(client),
                "Amount": json_money(rng, part), "Received_On": _zoho_time(rng, when),
                "Mode": MODES[rng.randrange(len(MODES))],
            }
            if rng.random() < 0.3:
                pr_fields["TDS_Deducted"] = float((part * Decimal("0.02")).quantize(Decimal("0.01")))
            receipts.append(Record("Payment_Received", _ext(4875, len(receipts) + 1), ref,
                                   inv.owner, when.astimezone(UTC), pr_fields))
            paid += part
        balance = total - paid
        fields["Balance_Due"] = float(balance)
        fields["Status"] = "Paid" if balance == 0 else "Unpaid" if paid == 0 else "Partially Paid"
    return invoices, receipts


def _payments_made(rng: random.Random, operators: list[Record]) -> list[Record]:
    out = []
    for n in range(90):
        op = operators[rng.randrange(len(operators))]
        day = _day_between(rng, START, AS_OF)
        when = _moment(rng, day, edge=rng.random() < 0.1)
        ref = f"PM-{n + 1:05d}"
        fields = {
            "Reference": ref, "Operator": _lookup(op),
            "Amount": json_money(rng, money(rng, 8_000, 240_000)),
            "Paid_On": _zoho_time(rng, when), "Purpose": PURPOSES[rng.randrange(3)],
        }
        out.append(Record("Payment_Made", _ext(4876, n + 1), ref, OWNERS[n % 3],
                          when.astimezone(UTC), fields))
    return out


def _contacts(rng: random.Random) -> list[Record]:
    """The core Contacts, which the profile calls "Existing Sites"."""
    out = []
    for n in range(60):
        name = f"{_PLACE[n % 12]} Plant {n + 1}"
        custom: dict[str, Any] = {"Site_State": STATES[rng.randrange(len(STATES))]}
        if rng.random() < 0.85:
            custom["Plant_Capacity_MLD"] = rng.randint(2, 120)
        if rng.random() < 0.9:
            custom["Commissioned_On"] = _day_between(rng, date(2015, 1, 1), date(2026, 6, 30)).isoformat()
        created = _moment(rng, _day_between(rng, date(2024, 1, 1), AS_OF), edge=False)
        out.append(Record("Contacts", _ext(4870, n + 1), name, OWNERS[n % 3], created, custom))
    return out


_NOTE_TEXTS = (
    ("Payment follow-up", "Called accounts at {client}. They promised the transfer next week."),
    ("Site visit", "Visited the plant of {client}. The operators asked for a training day."),
    ("Quote request", "{client} wants a quote for two more dosing skids."),
    ("Meeting notes", "Quarterly review with {client}. They are happy with the uptime."),
    ("Documents", "Sent the commissioning report and the warranty letter to {client}."),
)

_PUMP_TEXTS = (
    ("Breakdown", "The engineer at {client} reported a pump failure on line 2. Spares sent."),
    ("Urgent: pump", "Second pump failure this month at {client}. Plan a root cause visit."),
)


def _notes(rng: random.Random, clients: list[Record], invoices: list[Record]) -> list[Note]:
    out: list[Note] = []
    pump_clients = rng.sample(clients, 4)
    for n in range(70):
        if n < 6:
            client = pump_clients[n % len(pump_clients)]
            subject, body = _PUMP_TEXTS[n % len(_PUMP_TEXTS)]
            module, rec = "Clients", client
        else:
            subject, body = _NOTE_TEXTS[n % len(_NOTE_TEXTS)]
            if n % 4 == 0:
                rec = invoices[rng.randrange(len(invoices))]
                client = next(c for c in clients if c.ext_id == rec.fields["Client"]["id"])
                module = "Invoices"
            else:
                client = rng.choice([c for c in clients if c not in pump_clients])
                module, rec = "Clients", client
        day = _day_between(rng, date(2025, 6, 1), AS_OF)
        out.append(Note(
            ext_id=_ext(4877, n + 1), module=module, record_ext_id=rec.ext_id,
            subject=subject, body=body.format(client=client.name),
            occurred=_moment(rng, day, edge=False).astimezone(UTC),
        ))
    return out


@cache
def build() -> Dataset:
    """The dataset. The same object each call, because the seed is fixed."""
    rng = random.Random(SEED)
    companies = _companies(rng)
    clients = _clients(rng, companies)
    operators = _operators(rng)
    invoices, receipts = _invoices_and_receipts(rng, clients)
    made = _payments_made(rng, operators)
    contacts = _contacts(rng)
    notes = _notes(rng, clients, invoices)
    records = {
        "Company": companies, "Clients": clients, "Operator_Associates": operators,
        "Invoices": invoices, "Payment_Received": receipts, "Payment_Made": made,
        "Commercials": [],
    }
    zoho = {name: len(rows) for name, rows in records.items()}
    zoho["Payment_Made"] += PAYMENT_MADE_HIDDEN
    return Dataset(modules=MODULES, contact_fields=CONTACT_FIELDS, records=records,
                   contacts=contacts, notes=notes, zoho_counts=zoho)


# ── The profile ─────────────────────────────────────────────────────────────


def profile_module(module: Module) -> dict[str, Any]:
    """One generic module row of the profile, in the shape the sync writes."""
    return {
        "api_name": module.api_name, "module_name": module.api_name,
        "tab": module.api_name, "label": module.label,
        "plural_label": module.label, "singular_label": module.singular,
        "custom": True, "generated_type": "custom", "api_supported": True,
        "visible": True, "mirrored": True, "generic": True,
        "scope": "ZohoCRM.modules.custom.READ", "display_field": module.fields[0].key,
    }


#: The core Contacts, as the Zoho org of DEWiN names them.
CONTACTS_PROFILE: dict[str, Any] = {
    "api_name": "Contacts", "module_name": "Contacts",
    "plural_label": "Existing Sites", "singular_label": "Existing Site",
    "generated_type": "default", "api_supported": True,
}


def profile(ds: Dataset) -> dict[str, Any]:
    """The source profile: the zone of the Zoho org and the modules."""
    return {
        "org": {"time_zone": ZONE, "name": "Founder Evals Pvt Ltd (synthetic)"},
        "modules": [CONTACTS_PROFILE, *(profile_module(m) for m in ds.modules)],
    }
