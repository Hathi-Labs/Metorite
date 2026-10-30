"""The old IANA zone names resolve on every box (2026-10-30).

Ubuntu 24.04 moved the old names, such as "Asia/Calcutta", to the
``tzdata-legacy`` package, and neither box installs it. ``ZoneInfo`` then
raised on them, and ``zone_of`` read the member as UTC. On production, 24 of
26 stored zones were "Asia/Calcutta". The ``tzdata`` package of
``acb_common`` holds the aliases, and ``zoneinfo`` falls back to it.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from gateway.routes.shell.state import zone_of

#: The aliases that a browser or an old client can store.
ALIASES = {
    "Asia/Calcutta": timedelta(hours=5, minutes=30),
    "Asia/Kolkata": timedelta(hours=5, minutes=30),
    "Asia/Saigon": timedelta(hours=7),
    "Asia/Katmandu": timedelta(hours=5, minutes=45),
}


@pytest.mark.parametrize(("name", "offset"), sorted(ALIASES.items()))
def test_an_old_zone_name_resolves(name, offset):
    """Catches a dropped ``tzdata`` dependency: on Ubuntu 24.04 the alias
    raises ZoneInfoNotFoundError, and this goes RED."""
    at = datetime(2026, 10, 30, 12, 0, tzinfo=ZoneInfo(name))
    assert at.utcoffset() == offset


def test_zone_of_keeps_the_member_zone_for_an_alias():
    """Catches the shell reading an alias as UTC: the member then gets the
    day, the feed and the brief 5.5 hours off."""
    at = datetime(2026, 10, 30, 12, 0, tzinfo=zone_of("Asia/Calcutta"))
    assert at.utcoffset() == timedelta(hours=5, minutes=30)


def test_acb_common_declares_tzdata():
    """Catches a lock that keeps tzdata only for Windows."""
    from importlib.metadata import requires

    assert any(r.split(">")[0].split("=")[0].strip() == "tzdata"
               for r in requires("acb-common") or [])
