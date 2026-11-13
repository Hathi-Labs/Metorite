"""The org-list flags that the Studio STORE reads (WS-STU).

The store (``gateway/studio/objects_store.py``) holds no import of the routes
layer, so a flag that it reads lives here. The grammar is the one of the
sibling ``*_ORGS`` flags: a comma list of organization ids, ``*`` for every
org, and empty for none. The reader runs on each call, so a SIGHUP reload
takes effect with no restart (``docs/flag_reload.md``).

``STUDIO_NUM_F8_ORGS`` (C2c) adds the float8 test to a number filter of the
object query. Default OFF.
"""

from __future__ import annotations

import functools

from acb_common import get_settings
from acb_common.db import current_tenant


@functools.lru_cache(maxsize=16)
def _parse_orgs(raw: str) -> frozenset[str]:
    """The entries of a comma list, trimmed and in lower case."""
    return frozenset(p.strip().lower() for p in raw.split(",") if p.strip())


def num_f8_allowed(organization_id: str | None) -> bool:
    """True when ``STUDIO_NUM_F8_ORGS`` holds the org, or holds ``*``. Default
    OFF. The one reader of the setting. The org comes from the bound tenant,
    never from request input (R5)."""
    if not organization_id:
        return False
    listed = _parse_orgs(str(getattr(get_settings(), "studio_num_f8_orgs", "") or ""))
    return "*" in listed or str(organization_id).strip().lower() in listed


def num_f8_org() -> str | None:
    """The tenant bound to this context, when it is on ``STUDIO_NUM_F8_ORGS``,
    else None. The store asks this, because it may import no ``db`` module."""
    org = current_tenant()
    return str(org).strip().lower() if num_f8_allowed(org) and org else None
