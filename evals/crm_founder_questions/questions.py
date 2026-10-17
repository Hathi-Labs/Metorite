"""The founder questions and their known answers (WS-53 CRM-Q30).

Spec: ``project-docs/specs/crm_platform.md`` §18.11.2, CRM-Q30 items 5 and 6.

Each :class:`Question` holds the words a founder types, a category, the
calls that the answer needs, and the facts that the answer must hold. Plain
Python computes each fact from :func:`dataset.build`, with loops over the
rows. Nobody writes the numbers by hand.

**The calls.** A :class:`Need` is one call that the answer needs. Its
alternatives are route calls: the path, and the parameters that the call
must carry. A call may carry more parameters than the alternative names, so
a total takes any group. The checkers match the GET requests that the tools
of ``crm_tools.py`` send, because the request is what the route answers.

**The facts.** A :class:`Fact` has a key, a value and, when a route gives
the number, a selector into the answer of one call (``src``). The scripted
run and the R8 test read the selector, and the value must agree with the
route. ``in_answer`` false marks a fact that only the route check reads,
for example that an empty field gives no total.

**The CRM zone.** A ``date`` field keeps the day that Zoho wrote. A
``datetime`` field and a Zoho created time take their day in Asia/Kolkata,
as the route does (CRM-Q25b).
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from functools import cache
from typing import Any

from evals.crm_founder_questions.dataset import (
    AS_OF,
    PUMP_FAILURE,
    Dataset,
    Record,
    as_decimal,
    build,
    created_ist,
    ist_day,
)

AGG = "/crm/aggregate"
MODULES = "/crm/modules"
NOTES = "/crm/notes/search"

#: The categories that the set must cover (CRM-Q30 item 6).
CATEGORIES: tuple[str, ...] = (
    "total", "trend_month", "trend_quarter", "zone", "top_n", "overdue",
    "cross_module", "two_calls", "empty_field", "relabel", "picklist",
    "note_search", "short_read",
)

#: A forbidden pattern: a rupee zero. An empty field has no total, and
#: "INR 0" says that it has one (CRM-Q25c item 3).
MONEY_ZERO = r"(?:₹|INR|Rs\.?)\s*0(?:\.0+)?(?!\d|[.,]\d)"


@dataclass(frozen=True)
class Need:
    """One call that the answer needs. Any one alternative meets it."""

    alternatives: tuple[dict[str, str], ...]


@dataclass(frozen=True)
class Fact:
    key: str
    value: Any
    #: ``(call index, selector...)`` into the route answers, or None for a
    #: fact that the answer derives, for example a difference.
    src: tuple[Any, ...] | None = None
    in_answer: bool = True


@dataclass(frozen=True)
class Question:
    id: str
    category: str
    words: str
    needs: tuple[Need, ...]
    facts: tuple[Fact, ...]
    #: The answer must hold one of these phrases (case does not matter).
    flags: tuple[str, ...] = ()
    #: The answer must hold none of these numbers.
    forbid_numbers: tuple[Any, ...] = ()
    #: The answer must match none of these regular expressions.
    forbid_patterns: tuple[str, ...] = ()
    #: Lines that the output of a tool must hold (CRM-Q30b).
    tool_lines: tuple[str, ...] = ()
    #: The number facts whose figure ``answer_check`` cannot source yet, and
    #: why. Only a figure that states one of these facts may wait. Any other
    #: unsourced figure still fails, and a wait that no figure needs fails
    #: too, so the mark goes when the rule lands.
    answer_check_waits: tuple[str, ...] = ()
    wait_reason: str = ""
    #: False for the note search, which needs the embed door, not a route
    #: that the R8 test can serve.
    route_check: bool = True
    note: str = ""

    def numbers(self) -> list[Fact]:
        return [f for f in self.facts if f.in_answer and not isinstance(f.value, str)]

    def names(self) -> list[Fact]:
        return [f for f in self.facts if f.in_answer and isinstance(f.value, str)]


# ── Plain Python over the rows ──────────────────────────────────────────────

CENT = Decimal("0.01")


def r2(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def num(rec: Record, key: str) -> Decimal | None:
    return as_decimal(rec.fields.get(key))


def day(rec: Record, key: str) -> date | None:
    value = rec.fields.get(key)
    return date.fromisoformat(value) if isinstance(value, str) else None


def dt_day(rec: Record, key: str) -> date | None:
    value = rec.fields.get(key)
    return ist_day(value) if isinstance(value, str) else None


def between(d: date | None, first: date, last: date) -> bool:
    return d is not None and first <= d <= last


def total(rows: Iterable[Record], key: str) -> Decimal:
    out = Decimal(0)
    for rec in rows:
        value = num(rec, key)
        if value is not None:
            out += value
    return r2(out)


def sums_by(rows: Iterable[Record], group: Callable[[Record], Any],
            key: str) -> dict[Any, Decimal]:
    out: dict[Any, Decimal] = defaultdict(Decimal)
    for rec in rows:
        value = num(rec, key)
        if value is not None:
            out[group(rec)] += value
    return {k: r2(v) for k, v in out.items()}


def counts_by(rows: Iterable[Record], group: Callable[[Record], Any]) -> dict[Any, int]:
    out: dict[Any, int] = defaultdict(int)
    for rec in rows:
        out[group(rec)] += 1
    return dict(out)


def ranked(values: dict[str, Decimal], names: dict[str, str]) -> list[tuple[str, Decimal]]:
    """Largest first, then by name: the order of the route."""
    return sorted(values.items(), key=lambda kv: (-kv[1], names[kv[0]]))


def lookup_id(rec: Record, key: str) -> str:
    return str(rec.fields[key]["id"])


def month(d: date) -> str:
    return f"{d:%Y-%m}"


def quarter(d: date) -> str:
    return f"{d.year}-Q{(d.month - 1) // 3 + 1}"


def agg(entity: str, **params: str) -> dict[str, str]:
    return {"path": AGG, "entity": entity, **params}


def need(*alternatives: dict[str, str]) -> Need:
    return Need(tuple(alternatives))


FY = {"from": "2025-04-01", "to": "2026-03-31"}
FY_FIRST, FY_LAST = date(2025, 4, 1), date(2026, 3, 31)
OVERDUE_TO = ("2026-10-14", "2026-10-15")


def _range(key: str, first: str, last: str | None = None) -> dict[str, str]:
    out = {f"field.{key}.from": first}
    if last is not None:
        out[f"field.{key}.to"] = last
    return out


def _top_facts(prefix: str, top: list[tuple[str, Decimal]], names: dict[str, str],
               call: int) -> list[Fact]:
    facts = []
    for i, (key, value) in enumerate(top):
        facts.append(Fact(f"{prefix}.{i + 1}.name", names[key], (call, "rank", i, "label")))
        facts.append(Fact(f"{prefix}.{i + 1}.value", value, (call, "rank", i, "value")))
    return facts


# ── The questions ───────────────────────────────────────────────────────────


@cache
def questions() -> tuple[Question, ...]:
    """The questions of the set, over :func:`dataset.build`."""
    return make(build())


def make(ds: Dataset) -> tuple[Question, ...]:
    """The questions over *ds*, each fact computed from its rows."""
    inv = ds.rows("Invoices")
    pr = ds.rows("Payment_Received")
    pm = ds.rows("Payment_Made")
    clients = ds.rows("Clients")
    client_names = {c.ext_id: c.name for c in clients}
    op_names = {o.ext_id: o.name for o in ds.rows("Operator_Associates")}
    out: list[Question] = []

    def add(q: Question) -> None:
        out.append(q)

    # Totals and trends.
    fy_inv = [r for r in inv if between(day(r, "Invoice_Date"), FY_FIRST, FY_LAST)]
    add(Question(
        "Q01", "total", "How much did we invoice in the financial year 2025-26?",
        (need(agg("module:Invoices", measure="sum", field="Grand_Total",
                  **_range("Invoice_Date", FY["from"], FY["to"]))),),
        (Fact("total", total(fy_inv, "Grand_Total"), (0, "total")),),
    ))

    months = ("2026-04", "2026-05", "2026-06", "2026-07", "2026-08", "2026-09")
    by_month = sums_by(inv, lambda r: month(day(r, "Invoice_Date")), "Grand_Total")
    add(Question(
        "Q02", "trend_month",
        "Show me what we invoiced in each month from April to September 2026.",
        (need(agg("module:Invoices", measure="sum", field="Grand_Total",
                  group_field="Invoice_Date", bucket="month")),),
        tuple(Fact(f"month.{m}", by_month[m], (0, "group", m)) for m in months),
    ))

    by_quarter = sums_by([r for r in inv if day(r, "Invoice_Date").year == 2026],
                         lambda r: quarter(day(r, "Invoice_Date")), "Grand_Total")
    add(Question(
        "Q03", "trend_quarter", "What did we invoice in each calendar quarter of 2026?",
        (need(agg("module:Invoices", measure="sum", field="Grand_Total",
                  group_field="Invoice_Date", bucket="quarter")),),
        tuple(Fact(f"quarter.{k}", v, (0, "group", k)) for k, v in sorted(by_quarter.items())),
    ))

    received = sums_by(pr, lambda r: month(dt_day(r, "Received_On")), "Amount")
    add(Question(
        "Q04", "zone", "How much money came in each month from June to August 2026?",
        (need(agg("module:Payment_Received", measure="sum", field="Amount",
                  group_field="Received_On", bucket="month")),),
        tuple(Fact(f"month.{m}", received[m], (0, "group", m))
              for m in ("2026-06", "2026-07", "2026-08")),
        note="Received_On is a datetime. Its month is the month in Asia/Kolkata.",
    ))

    created = counts_by(inv, lambda r: month(created_ist(r)))
    add(Question(
        "Q05", "zone",
        "How many invoice records were created in Zoho in July, August and September 2026?",
        (need(agg("module:Invoices", measure="count", group_by="created_month")),),
        tuple(Fact(f"created.{m}", created.get(m, 0), (0, "group", m))
              for m in ("2026-07", "2026-08", "2026-09")),
        note="The created month is the month of Zoho's created time in Asia/Kolkata.",
    ))

    fy_pm = [r for r in pm if between(dt_day(r, "Paid_On"), FY_FIRST, FY_LAST)]
    add(Question(
        "Q06", "total", "How much did we pay our operator associates in FY 2025-26?",
        (need(agg("module:Payment_Made", measure="sum", field="Amount",
                  **_range("Paid_On", FY["from"], FY["to"]))),),
        (Fact("total", total(fy_pm, "Amount"), (0, "total")),),
    ))

    inv_2026 = [r for r in inv if day(r, "Invoice_Date").year == 2026]
    values_2026 = [num(r, "Grand_Total") for r in inv_2026]
    add(Question(
        "Q07", "total", "What was our average invoice value in calendar 2026?",
        (need(
            agg("module:Invoices", measure="avg", field="Grand_Total",
                **_range("Invoice_Date", "2026-01-01", "2026-12-31")),
            agg("module:Invoices", measure="avg", field="Grand_Total",
                **_range("Invoice_Date", "2026-01-01", AS_OF.isoformat())),
            agg("module:Invoices", measure="avg", field="Grand_Total",
                group_field="Invoice_Date", bucket="year"),
        ),),
        (Fact("avg", r2(sum(values_2026, Decimal(0)) / len(values_2026)), (0, "total")),),
    ))

    add(Question(
        "Q08", "total", "What is the largest single payment we have received?",
        (need(agg("module:Payment_Received", measure="max", field="Amount")),),
        (Fact("max", max(num(r, "Amount") for r in pr), (0, "total")),),
    ))

    sept = [r for r in inv if between(day(r, "Invoice_Date"), date(2026, 9, 1), date(2026, 9, 30))]
    add(Question(
        "Q09", "total", "How many invoices did we raise in September 2026?",
        (need(
            agg("module:Invoices", measure="count",
                **_range("Invoice_Date", "2026-09-01", "2026-09-30")),
            agg("module:Invoices", measure="count", group_field="Invoice_Date", bucket="month"),
        ),),
        (Fact("count", len(sept), (0, "total")),),
    ))

    fy_pr = [r for r in pr if between(dt_day(r, "Received_On"), FY_FIRST, FY_LAST)]
    by_mode = sums_by(fy_pr, lambda r: r.fields["Mode"], "Amount")
    add(Question(
        "Q10", "picklist", "How did clients pay us in FY 2025-26, split by payment mode?",
        (need(agg("module:Payment_Received", measure="sum", field="Amount",
                  group_field="Mode", **_range("Received_On", FY["from"], FY["to"]))),),
        tuple(Fact(f"mode.{k}", v, (0, "group", k)) for k, v in sorted(by_mode.items())),
    ))

    # Top-N.
    invoiced = sums_by(inv, lambda r: lookup_id(r, "Client"), "Grand_Total")
    top5 = ranked(invoiced, client_names)[:5]
    add(Question(
        "Q11", "top_n", "Who are our top 5 clients by total invoiced amount?",
        (need(agg("module:Invoices", measure="sum", field="Grand_Total", group_field="Client")),),
        tuple(_top_facts("top", top5, client_names, 0)),
    ))

    pm_2026 = [r for r in pm if between(dt_day(r, "Paid_On"), date(2026, 1, 1), AS_OF)]
    paid_ops = sums_by(pm_2026, lambda r: lookup_id(r, "Operator"), "Amount")
    add(Question(
        "Q12", "top_n", "Which 3 operator associates did we pay the most in 2026?",
        (need(agg("module:Payment_Made", measure="sum", field="Amount",
                  group_field="Operator", **_range("Paid_On", "2026-01-01"))),),
        tuple(_top_facts("top", ranked(paid_ops, op_names)[:3], op_names, 0)),
    ))

    owed = sums_by(inv, lambda r: lookup_id(r, "Client"), "Balance_Due")
    add(Question(
        "Q13", "top_n", "Which 5 clients owe us the most right now?",
        (need(agg("module:Invoices", measure="sum", field="Balance_Due", group_field="Client")),),
        tuple(_top_facts("top", ranked(owed, client_names)[:5], client_names, 0)),
    ))

    # Overdue or unpaid.
    overdue = [r for r in inv if day(r, "Due_Date") < AS_OF]
    add(Question(
        "Q14", "overdue", "How much money is overdue as of 15 October 2026?",
        (need(*(agg("module:Invoices", measure="sum", field="Balance_Due",
                    **{"field.Due_Date.to": to}) for to in OVERDUE_TO)),),
        (Fact("overdue", total(overdue, "Balance_Due"), (0, "total")),),
        note="No invoice falls due on 15 October 2026, so both ends of the range agree.",
    ))

    by_status = counts_by(inv, lambda r: r.fields["Status"])
    add(Question(
        "Q15", "overdue", "How many invoices have not been paid at all?",
        (need(
            agg("module:Invoices", measure="count", group_field="Status"),
            agg("module:Invoices", measure="count", **{"field.Status": "Unpaid"}),
        ),),
        (Fact("unpaid", by_status["Unpaid"], (0, "group", "Unpaid")),),
    ))

    overdue_status = counts_by(overdue, lambda r: r.fields["Status"])
    add(Question(
        "Q16", "overdue",
        "How many invoices are overdue as of 15 October 2026, split by payment status?",
        (need(*(agg("module:Invoices", measure="count", group_field="Status",
                    **{"field.Due_Date.to": to}) for to in OVERDUE_TO)),),
        tuple(Fact(f"status.{s}", overdue_status[s], (0, "group", s))
              for s in ("Unpaid", "Partially Paid")),
    ))

    add(Question(
        "Q17", "overdue", "What is our total outstanding balance across all invoices?",
        (need(agg("module:Invoices", measure="sum", field="Balance_Due")),),
        (Fact("outstanding", total(inv, "Balance_Due"), (0, "total")),),
    ))

    partial = [r for r in inv if r.fields["Status"] == "Partially Paid"]
    add(Question(
        "Q18", "overdue", "How much do the partially paid invoices still owe us?",
        (need(
            agg("module:Invoices", measure="sum", field="Balance_Due", group_field="Status"),
            agg("module:Invoices", measure="sum", field="Balance_Due",
                **{"field.Status": "Partially Paid"}),
        ),),
        (Fact("owed", total(partial, "Balance_Due"), (0, "group", "Partially Paid")),),
    ))

    # Across modules.
    paid_by = sums_by(pr, lambda r: lookup_id(r, "Client"), "Amount")
    cross: list[Fact] = []
    for i, (cid, value) in enumerate(top5):
        cross.append(Fact(f"client.{i + 1}.name", client_names[cid], (0, "rank", i, "label")))
        cross.append(Fact(f"client.{i + 1}.invoiced", value, (0, "rank", i, "value")))
        cross.append(Fact(f"client.{i + 1}.paid", paid_by.get(cid, Decimal(0)), (1, "group", cid)))
        cross.append(Fact(f"client.{i + 1}.outstanding", owed[cid], (2, "group", cid)))
    add(Question(
        "Q19", "cross_module",
        "For our top 5 clients by invoiced amount, how much has each paid us, and what is "
        "still outstanding?",
        (
            need(agg("module:Invoices", measure="sum", field="Grand_Total", group_field="Client")),
            need(
                agg("module:Payment_Received", measure="sum", field="Amount",
                    group_field="Client"),
                agg("module:Invoices", measure="sum", field="Balance_Due", group_field="Client"),
            ),
        ),
        tuple(cross),
        note="Invoices and Payment_Received join on the Client lookup. Invoiced equals paid "
             "plus outstanding for each client.",
    ))

    q2 = (date(2026, 4, 1), date(2026, 6, 30))
    add(Question(
        "Q20", "two_calls", "Compare what we invoiced with what we received between April "
                            "and June 2026.",
        (
            need(agg("module:Invoices", measure="sum", field="Grand_Total",
                     **_range("Invoice_Date", "2026-04-01", "2026-06-30"))),
            need(agg("module:Payment_Received", measure="sum", field="Amount",
                     **_range("Received_On", "2026-04-01", "2026-06-30"))),
        ),
        (
            Fact("invoiced", total([r for r in inv if between(day(r, "Invoice_Date"), *q2)],
                                   "Grand_Total"), (0, "total")),
            Fact("received", total([r for r in pr if between(dt_day(r, "Received_On"), *q2)],
                                   "Amount"), (1, "total")),
        ),
    ))

    q3 = (date(2026, 7, 1), date(2026, 9, 30))
    q3_in = total([r for r in pr if between(dt_day(r, "Received_On"), *q3)], "Amount")
    q3_out = total([r for r in pm if between(dt_day(r, "Paid_On"), *q3)], "Amount")
    add(Question(
        "Q21", "two_calls",
        "What was our net cash flow from July to September 2026: money received minus money "
        "paid out?",
        (
            need(agg("module:Payment_Received", measure="sum", field="Amount",
                     **_range("Received_On", "2026-07-01", "2026-09-30"))),
            need(agg("module:Payment_Made", measure="sum", field="Amount",
                     **_range("Paid_On", "2026-07-01", "2026-09-30"))),
        ),
        (
            Fact("received", q3_in, (0, "total")),
            Fact("paid_out", q3_out, (1, "total")),
            Fact("net", q3_in - q3_out),
        ),
    ))

    # Empty fields.
    add(Question(
        "Q22", "empty_field", "What is the combined annual revenue of the companies we sell to?",
        (need(agg("module:Company", measure="sum", field="Annual_Revenue")),),
        (Fact("values", 0, (0, "values"), in_answer=False),
         Fact("total", None, (0, "total"), in_answer=False)),
        flags=("no value", "not recorded", "no annual revenue", "empty", "not filled",
               "no revenue figure", "no data"),
        forbid_patterns=(MONEY_ZERO,),
        note="Annual_Revenue is empty in each Company row.",
    ))

    add(Question(
        "Q23", "empty_field", "What is the total value of our commercials (quotes)?",
        (need(
            agg("module:Commercials", measure="sum", field="Quote_Value"),
            agg("module:Commercials", measure="count"),
        ),),
        (Fact("values", 0, (0, "values"), in_answer=False),),
        flags=("no value", "no commercial", "no record", "not recorded", "empty", "none",
               "no quote", "no data"),
        forbid_patterns=(MONEY_ZERO,),
        note="Commercials holds field definitions and no record, as DEWiN's does.",
    ))

    disc = [r for r in inv if day(r, "Invoice_Date").year == 2026]
    add(Question(
        "Q24", "total", "How much discount did we give on invoices raised in 2026?",
        (need(agg("module:Invoices", measure="sum", field="Discount",
                  **{"field.Invoice_Date.from": "2026-01-01"})),),
        (Fact("discount", total(disc, "Discount"), (0, "total")),),
        note="Discount is empty on most invoices. The sum reads the rows that hold one.",
    ))

    # The relabel: Contacts are "Existing Sites".
    sites = counts_by(ds.contacts, lambda r: r.fields["Site_State"])
    add(Question(
        "Q25", "relabel", "How many existing sites do we have in each state?",
        (need({"path": AGG, "entity": "contacts", "measure": "count",
               "group_field": "Site_State"}),),
        tuple(Fact(f"state.{k}", v, (0, "group", k)) for k, v in sorted(sites.items())),
        note="The profile labels the core Contacts as Existing Sites.",
    ))

    mh = [r for r in ds.contacts if r.fields["Site_State"] == "Maharashtra"]
    add(Question(
        "Q26", "relabel", "What is the total plant capacity of our existing sites in Maharashtra?",
        (need(
            {"path": AGG, "entity": "contacts", "measure": "sum",
             "field": "Plant_Capacity_MLD", "custom.Site_State": "Maharashtra"},
            {"path": AGG, "entity": "contacts", "measure": "sum",
             "field": "Plant_Capacity_MLD", "group_field": "Site_State"},
        ),),
        (Fact("capacity", total(mh, "Plant_Capacity_MLD"), (0, "total")),),
    ))

    # Picklists and booleans.
    keys = counts_by(clients, lambda r: "true" if r.fields["Key_Account"] else "false")
    add(Question(
        "Q27", "picklist", "How many of our clients are key accounts?",
        (need(
            agg("module:Clients", measure="count", group_field="Key_Account"),
            agg("module:Clients", measure="count", **{"field.Key_Account": "true"}),
        ),),
        (Fact("key_accounts", keys["true"], (0, "group", "true")),),
    ))

    segments = counts_by(clients, lambda r: r.fields["Segment"])
    add(Question(
        "Q28", "picklist", "How many clients do we have in each segment?",
        (need(agg("module:Clients", measure="count", group_field="Segment")),),
        tuple(Fact(f"segment.{k}", v, (0, "group", k)) for k, v in sorted(segments.items())),
    ))

    add(Question(
        "Q29", "total", "What is the smallest invoice we raised in FY 2025-26?",
        (need(agg("module:Invoices", measure="min", field="Grand_Total",
                  **_range("Invoice_Date", FY["from"], FY["to"]))),),
        (Fact("min", min(num(r, "Grand_Total") for r in fy_inv), (0, "total")),),
    ))

    # The note search (CRM-Q27). No route of the R8 test serves it.
    pump = sorted({n.record_ext_id for n in ds.notes if PUMP_FAILURE in n.body.lower()})
    add(Question(
        "Q30", "note_search", "Which clients have notes about a pump failure?",
        (need({"path": NOTES, "q~": "pump"}),),
        tuple(Fact(f"client.{i + 1}", client_names[cid])
              for i, cid in enumerate(sorted(pump, key=lambda c: client_names[c]))),
        route_check=False,
        note="CRM-Q27: crm_search_notes. The scripted answer of the route is synthetic, "
             "because the search needs the embed door.",
    ))

    # The short read (CRM-Q24, CRM-Q30b): Zoho counts more than the connected
    # user can read, and the tool says so in one line.
    mirrored = len(pm)
    zoho = ds.zoho_counts["Payment_Made"]
    add(Question(
        "Q31", "short_read", "How many Payment Made records do we have in Zoho?",
        (need(
            {"path": MODULES},
            agg("module:Payment_Made", measure="count"),
        ),),
        (Fact("mirrored", mirrored, (0, "module", "Payment_Made", "count")),
         Fact("zoho", zoho, ("short", "module:Payment_Made", "zoho_count")),
         Fact("readable", mirrored, ("short", "module:Payment_Made", "readable"),
              in_answer=False)),
        flags=("connected zoho user", "sync health", "cannot see", "can't see", "can read",
               "can see", "may not include", "might not include", "not all", "permission",
               "visible to"),
        tool_lines=(f"Note: Zoho holds {zoho:,} Payment_Made records, and the connected "
                    f"Zoho user can read {mirrored:,}.",),
        note="Zoho counts more records than the connected user can read. The tool says so "
             "in one line (CRM-Q30b), and the answer must give both counts.",
    ))
    return tuple(out)


def by_id() -> dict[str, Question]:
    return {q.id: q for q in questions()}


def facts_of(q: Question) -> dict[str, Any]:
    return {f.key: f.value for f in q.facts}


__all__ = ["AGG", "CATEGORIES", "MODULES", "NOTES", "Fact", "Need", "Question", "by_id",
           "facts_of", "make", "questions"]
