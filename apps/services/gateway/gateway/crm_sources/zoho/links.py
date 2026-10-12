"""The deep link to one Zoho record (WS-53 CRM-Q1, ``crm_platform.md`` §5.2 item 7).

The link is
``https://crm.<accounts host without "accounts.">/crm/<domain_name>/tab/<Tab>/<id>``.
For example ``https://crm.zoho.in/crm/org123/tab/Potentials/456``.

The function is pure. It reads no database and calls no API, so the adapter
and the record wire (``routes/crm/core.py``) share one rule. It builds a link
only from a checked accounts server, and only when every part is a plain word.
In any other case it returns ``None``, and the UI shows no link.

**The tab** of a module is its ``module_name`` in ``GET settings/modules``. For
Deals that is ``Potentials`` and not ``Deals``. A tenant can rename a tab, so the
sync stores the tab of each module it reads, and :func:`deep_link` takes that map
first. The defaults in :data:`DEFAULT_TABS` cover a tenant that has not been
discovered yet.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from types import MappingProxyType

from gateway.crm_sources.base import UntrustedHost
from gateway.crm_sources.zoho import auth

#: Canonical entity to the Zoho module API name.
ENTITY_MODULES: Mapping[str, str] = MappingProxyType(
    {
        "lead": "Leads",
        "contact": "Contacts",
        "company": "Accounts",
        "deal": "Deals",
    }
)

#: Zoho module API name to its tab, for a tenant that has not been discovered.
DEFAULT_TABS: Mapping[str, str] = MappingProxyType(
    {
        "Leads": "Leads",
        "Contacts": "Contacts",
        "Accounts": "Accounts",
        "Deals": "Potentials",
    }
)

#: The shape of a domain name, a tab and a record id. Anything else is no link.
_WORD = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")


def crm_host(accounts_server: str | None) -> str | None:
    """``crm.<host>`` for a checked accounts server, or ``None``."""
    try:
        origin = auth.check_accounts_server(accounts_server or "")
    except UntrustedHost:  # an unchecked host is no link
        return None
    host = origin.removeprefix("https://").removeprefix("accounts.")
    return f"crm.{host}"


def deep_link(
    accounts_server: str | None,
    domain_name: str | None,
    entity: str,
    ext_id: str | None,
    tabs: Mapping[str, str] | None = None,
) -> str | None:
    """The link to one record, or ``None`` when any part is missing or odd.

    ``entity`` is lead, contact, company or deal. ``tabs`` maps a module API
    name to its tab, from the modules metadata.
    """
    module = ENTITY_MODULES.get(entity)
    host = crm_host(accounts_server)
    if module is None or host is None:
        return None
    tab = (tabs or {}).get(module) or DEFAULT_TABS[module]
    parts = (domain_name or "", tab, str(ext_id or ""))
    if not all(_WORD.match(p) for p in parts):
        return None
    return f"https://{host}/crm/{parts[0]}/tab/{parts[1]}/{parts[2]}"


def tab_link(
    accounts_server: str | None,
    domain_name: str | None,
    entity: str,
    tabs: Mapping[str, str] | None = None,
) -> str | None:
    """The link to the list tab of a module, with no record id."""
    module = ENTITY_MODULES.get(entity)
    host = crm_host(accounts_server)
    if module is None or host is None:
        return None
    tab = (tabs or {}).get(module) or DEFAULT_TABS[module]
    if not (_WORD.match(domain_name or "") and _WORD.match(tab)):
        return None
    return f"https://{host}/crm/{domain_name}/tab/{tab}"


__all__ = ["DEFAULT_TABS", "ENTITY_MODULES", "crm_host", "deep_link", "tab_link"]
