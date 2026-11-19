"""The Zoho Books template, the first T1 template (API-1, D228).

Spec: ``project-docs/specs/api_connections.md`` §2.10. Task 3749 owns the
books connectors. This file only names what the mechanism carries.

* **Hosts.** Each host comes from ``ZOHO_DATA_CENTRES``, the one list of Zoho
  hosts. This file names no Zoho host of its own.
* **Sign-in.** The CRM's Zoho client, with the scheme ``Zoho-oauthtoken``.
  API-2 checks that the client may ask for the Books read scopes.
* **Context.** The gateway sets ``organization_id`` on each call. An argument
  that names it is refused.
* **Reads only.** Each op is class A. Writes wait for D-API-4.
* **The total.** The gateway total adds the ``total`` field of each invoice
  of ``list_invoices`` (§2.3). It refuses a list of more than one
  ``currency_code``.

**No report op yet.** §2.10 asks whether Zoho Books has a report op, such as
profit and loss. The spec and the code name no path of such an op. So API-1
adds none, and the next slice that reads Zoho's own API page adds it as one
more class A read.

**The paths.** The spec names the invoice list op and the Books v3 API, but
no path. The paths below follow the Zoho Books v3 API. API-2 checks each path
with a test read before a connection shows "connected" (§2.1).
"""

from __future__ import annotations

from gateway.connections.api.template import (
    Limits,
    Operation,
    Paging,
    SignIn,
    Template,
    Total,
)
from gateway.crm_sources.zoho.auth import ZOHO_DATA_CENTRES

#: The shapes of the query values.
_DATE = r"[0-9]{4}-[0-9]{2}-[0-9]{2}"
_NUMBER_ID = r"[0-9]{1,20}"
_PAGE = r"[0-9]{1,4}"
_PER_PAGE = r"[0-9]{1,3}"
_STATUS = r"sent|draft|overdue|paid|void|unpaid|partially_paid|viewed"
_SORT_COLUMN = r"date|due_date|total|balance|customer_name|invoice_number"
_SORT_ORDER = r"A|D"

#: Fifty invoices fit well inside the answer cap of 64 KiB.
_PAGE_SIZE = 50

ZOHO_BOOKS = Template(
    id="zoho_books",
    label="Zoho Books",
    summary="The invoices of the company in Zoho Books: amounts, dates, status and customers.",
    hosts=tuple((region, f"https://{dc.api_host}") for region, dc in ZOHO_DATA_CENTRES.items()),
    sign_in=SignIn(kind="oauth_code", header="Authorization", scheme="Zoho-oauthtoken",
                   client="zoho"),
    context=("organization_id",),
    operations=(
        Operation(
            name="list_invoices",
            method="GET",
            path="/books/v3/invoices",
            cls="A",
            title="List the invoices, with a filter on the status, the dates or the customer.",
            query=(
                ("page", _PAGE),
                ("per_page", _PER_PAGE),
                ("status", _STATUS),
                ("date_start", _DATE),
                ("date_end", _DATE),
                ("customer_id", _NUMBER_ID),
                ("sort_column", _SORT_COLUMN),
                ("sort_order", _SORT_ORDER),
            ),
            paging=Paging(page_key="page", size_key="per_page", size=_PAGE_SIZE),
            total=Total(items="invoices", field="total", currency="currency_code"),
            shape_read=True,
        ),
        Operation(
            name="get_invoice",
            method="GET",
            path="/books/v3/invoices/{invoice_id}",
            cls="A",
            title="Read one invoice, with its lines.",
            path_shapes=(("invoice_id", _NUMBER_ID),),
        ),
    ),
    # Third-party guides give 100 calls a minute for each organization
    # (§2.10). The daily cap depends on the Zoho plan. API-3 checks both
    # against Zoho's own page, and sets the daily cap then.
    limits=Limits(per_minute=100),
)

__all__ = ["ZOHO_BOOKS"]
