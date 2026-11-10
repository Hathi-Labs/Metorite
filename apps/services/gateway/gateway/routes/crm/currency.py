"""CRM · the one currency rule of every money figure (HANDOFF H-333, task 888).

``crm_deals`` carries a ``currency`` per deal, and the app holds no exchange
rate. So no figure adds two currencies. Each money figure is for ONE currency.
This module holds the three pieces that every surface shares, so no surface
keeps its own copy: the reports (``reports.py``), the aggregate
(``aggregate.py``) and, through the aggregate, the weekly digest and the AI
tool.

**Normalise before you compare.** A stored code may be ``''``, NULL, padded or
lower case (``' usd '``). The screen prints a rupee sign for ``''`` and ``'inr'``,
and a code that is not three letters makes ``Intl.NumberFormat`` throw. So the
rule is ``upper(trim(currency))``, with ``''`` and NULL read as
:data:`DEFAULT_CURRENCY`. :func:`normalise_currency` is the Python form and
:data:`CURRENCY_SQL` is the SQL form. They must agree, and
``tests/unit/test_crm_currency.py`` checks both against the same cases.

**The order.** The primary currency is :data:`DEFAULT_CURRENCY` first, as the
column default, and then the other codes in code order (:func:`ordered_currencies`).
"""

from __future__ import annotations

from collections.abc import Iterable

#: The column default of ``crm_deals.currency`` (migration 144), and so the
#: currency that leads every money figure. The app holds no organization currency.
DEFAULT_CURRENCY = "INR"

#: The SQL form of :func:`normalise_currency`, for a column named ``currency``.
CURRENCY_SQL = "COALESCE(NULLIF(upper(trim(currency)), ''), 'INR')"


def normalise_currency(code: object) -> str:
    """The code as the rule reads it: trimmed, upper case, and
    :data:`DEFAULT_CURRENCY` for NULL or an empty string."""
    text = str(code).strip().upper() if code is not None else ""
    return text or DEFAULT_CURRENCY


def ordered_currencies(codes: Iterable[object]) -> list[str]:
    """The distinct normalised codes, :data:`DEFAULT_CURRENCY` first, then in
    code order. A NULL code counts as the default, so it is never skipped."""
    found = {normalise_currency(code) for code in codes}
    return sorted(found, key=lambda code: (code != DEFAULT_CURRENCY, code))
