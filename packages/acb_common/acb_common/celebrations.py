"""Celebrations: the birthday, the festivals and the work anniversary of a member.

WS-63 PZ-43. Spec: ``project-docs/specs/personalization.md`` §16.6 (owner
decision Q3, D-PZ-41, 2026-11-06). The rule is: ask, never guess.

* The member gives a birthday as a month and a day. The profile keeps no year.
* The member picks the festivals for a greeting from the list
  :data:`FESTIVALS`. A member who picks none gets no festival greeting.
* The assistant greets once on the day, in the first reply of that local day.
* No code here reads a name, a region, a language or a mail to pick a
  festival or to guess a religion or a community (DPDP Act). The pure
  function :func:`celebration_line` takes no such input. A test reads its
  signature to hold that line.

**The festival list is EMPTY on purpose.** ``personalization.md`` §16.9 Q3
says the list fills after an owner or native-speaker check of the names and
the dates. This module holds the shape, the picker reads it, and no festival
row exists yet. Add a row to :data:`FESTIVALS` after that check.

**Where the data lives.** In the JSON profile, under the key ``celebrations``,
read by :func:`stored_profile` of ``acb_common.assistant_profile``. There is no
migration. Forget clears the whole column, so it clears this key too. The
export holds it.

**Once a day, with no stored marker.** The first assistant reply of the member's
local day carries the greeting line. A later reply that day finds an assistant
reply since local midnight, and adds no line (:func:`already_replied_today`).

The module opens no connection. :data:`READ_SQL` runs in the caller's session,
which binds the tenant (R5). Flag: ``CELEBRATIONS_ORGS``
(``celebrations_orgs``), default empty, read by :func:`celebrations_for`.

Fences: ``tests/unit/test_celebrations.py`` and
``tests/unit/test_celebrations_r8.py``.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

#: The key of the JSON profile.
KEY = "celebrations"
#: The most festivals that a member can pick.
FESTIVALS_MAX = 20
#: A leap year, so that 29 February is a valid pick.
_LEAP = 2000
#: How far back the once-a-day read looks, in hours. A zone is at most 14
#: hours from UTC, so local midnight is never older than 38 hours.
LOOKBACK_HOURS = 48


@dataclass(frozen=True)
class Festival:
    """One festival of the all-India list.

    ``id`` is a stable slug, ``name`` is what the member sees and what the
    greeting says, and ``dates`` holds the date of each year, because most
    Indian festival dates move each year."""

    id: str
    name: str
    dates: tuple[date, ...]

    def falls_on(self, day: date) -> bool:
        return day in self.dates


#: The catalogue. EMPTY until the check of ``personalization.md`` §16.9 Q3.
FESTIVALS: tuple[Festival, ...] = ()


def festival_ids() -> tuple[str, ...]:
    return tuple(f.id for f in FESTIVALS)


def catalogue() -> list[dict[str, str]]:
    """The list as the API answers it: id and name, never the dates."""
    return [{"id": f.id, "name": f.name} for f in FESTIVALS]


# ── The shape of the stored key ────────────────────────────────────────────


def empty() -> dict[str, Any]:
    """What a member who set nothing holds."""
    return {"festivals": [], "work_anniversary": False}


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _birthday(value: Any) -> dict[str, int]:
    """A birthday ``{"month": 3, "day": 12}``. Raises ValueError for any other
    shape: a year, an unknown key or an impossible date."""
    if not isinstance(value, dict):
        raise ValueError("the birthday is a month and a day")
    extra = [k for k in value if k not in ("month", "day")]
    if extra:
        # The profile keeps no year, and no other part of a date.
        raise ValueError("the birthday holds a month and a day only, with no year")
    month, day = _int(value.get("month")), _int(value.get("day"))
    if month is None or day is None:
        raise ValueError("the birthday needs a month and a day, as numbers")
    try:
        date(_LEAP, month, day)
    except ValueError:
        raise ValueError("that month and day are not a date") from None
    return {"month": month, "day": day}


def _festivals(value: Any, *, strict: bool) -> list[str]:
    """The picked ids that the catalogue holds, once each. An unknown id
    drops. A value that is not a list raises when *strict*, else it reads as
    no pick."""
    if not isinstance(value, list):
        if strict:
            raise ValueError("festivals is a list of ids")
        return []
    known = set(festival_ids())
    out: list[str] = []
    for one in value:
        if isinstance(one, str) and one in known and one not in out:
            out.append(one)
    return out[:FESTIVALS_MAX]


def clean_celebrations(value: Any) -> dict[str, Any]:
    """The strict write of the key (``PUT /auth/me/assistant-profile``).

    *value* holds ``birthday`` (month and day, no year), ``festivals`` (ids
    of the catalogue) and ``work_anniversary`` (true or false). Any other key,
    a year, an impossible date or a wrong type raises ValueError. An unknown
    festival id drops. The write replaces the whole key."""
    if not isinstance(value, dict):
        raise ValueError("celebrations is an object")
    unknown = [k for k in value if k not in ("birthday", "festivals", "work_anniversary")]
    if unknown:
        raise ValueError("celebrations has an unknown key. Known keys: "
                         "birthday, festivals, work_anniversary")
    out = empty()
    if value.get("birthday") is not None:
        out["birthday"] = _birthday(value["birthday"])
    if "festivals" in value:
        out["festivals"] = _festivals(value["festivals"], strict=True)
    if "work_anniversary" in value:
        if not isinstance(value["work_anniversary"], bool):
            raise ValueError("work_anniversary is true or false")
        out["work_anniversary"] = value["work_anniversary"]
    return out


def stored_celebrations(raw: Any) -> dict[str, Any]:
    """The lenient read of the stored key: a part of the wrong shape drops,
    and an absent part takes its default. Never raises."""
    out = empty()
    if not isinstance(raw, dict):
        return out
    with contextlib.suppress(ValueError):
        out["birthday"] = _birthday(raw.get("birthday"))
    out["festivals"] = _festivals(raw.get("festivals"), strict=False)
    if isinstance(raw.get("work_anniversary"), bool):
        out["work_anniversary"] = raw["work_anniversary"]
    return out


def has_content(celebrations: dict[str, Any]) -> bool:
    """True when the member set a birthday, a festival or the anniversary."""
    return bool(celebrations.get("birthday") or celebrations.get("festivals")
                or celebrations.get("work_anniversary"))


# ── What is celebrated today ───────────────────────────────────────────────


def _on_day(month: int, day: int, today: date) -> bool:
    """True when *today* is the day ``month``/``day`` of its year. 29 February
    falls on 28 February in a year with no 29 February (decision of PZ-43)."""
    if (month, day) == (2, 29):
        try:
            date(today.year, 2, 29)
        except ValueError:
            return (today.month, today.day) == (2, 28)
    return (today.month, today.day) == (month, day)


@dataclass(frozen=True)
class Today:
    """What the member celebrates on a day."""

    birthday: bool = False
    festivals: tuple[str, ...] = ()
    anniversary_years: int | None = None

    def __bool__(self) -> bool:
        return bool(self.birthday or self.festivals or self.anniversary_years)


def celebrated_today(celebrations: dict[str, Any], today: date,
                     join_date: date | None = None) -> Today:
    """What the member celebrates on *today*, their local date. Pure.

    It takes the stored key, the date and the People join date, and nothing
    else: no name, no region, no language and no mail. A member who picked
    no festival gets no festival. The anniversary needs the switch on and a
    join date from an earlier year."""
    c = stored_celebrations(celebrations)
    birthday = False
    b = c.get("birthday")
    if b:
        birthday = _on_day(b["month"], b["day"], today)
    picked = set(c["festivals"])
    festivals = tuple(f.name for f in FESTIVALS if f.id in picked and f.falls_on(today))
    years = None
    if (c["work_anniversary"] and isinstance(join_date, date)
            and today.year > join_date.year
            and _on_day(join_date.month, join_date.day, today)):
        years = today.year - join_date.year
    return Today(birthday=birthday, festivals=festivals, anniversary_years=years)


def line(found: Today) -> str:
    """The one line for the turn, or ``""`` when nothing is celebrated.

    The festival names come from the catalogue, never from the member. The
    text follows ``acb_llm.voice`` CORE: plain words, no dash."""
    bits: list[str] = []
    if found.birthday:
        bits.append("Today is the member's birthday.")
    if found.festivals:
        names = ", ".join(found.festivals)
        bits.append(f"Today is {names}, a day the member asked you to greet.")
    if found.anniversary_years:
        n = found.anniversary_years
        bits.append(f"Today is the member's work anniversary: {n} "
                    f"{'year' if n == 1 else 'years'} at the company.")
    if not bits:
        return ""
    bits.append("In the first line of this reply, give one short, warm wish for it, "
                "then answer. Do this in this reply only.")
    return " ".join(bits)


# ── Once a day ─────────────────────────────────────────────────────────────


def _zone(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or "Asia/Kolkata")
    except Exception:
        return ZoneInfo("Asia/Kolkata")


def local_today(now: datetime, zone: str | None) -> date:
    """The date of *now* in *zone*. A naive *now* reads as UTC."""
    moment = now if now.tzinfo is not None else now.replace(tzinfo=UTC)
    return moment.astimezone(_zone(zone)).date()


def already_replied_today(last_reply_at: datetime | None, now: datetime,
                          zone: str | None) -> bool:
    """True when an assistant reply came since the local midnight of *now*.

    *last_reply_at* is the newest assistant reply of the member, in any of
    their chats, before this turn. None means none in the look-back window."""
    if last_reply_at is None:
        return False
    tz = _zone(zone)
    moment = now if now.tzinfo is not None else now.replace(tzinfo=UTC)
    midnight = datetime.combine(moment.astimezone(tz).date(), time.min, tzinfo=tz)
    reply = last_reply_at if last_reply_at.tzinfo is not None else last_reply_at.replace(tzinfo=UTC)
    return reply >= midnight


def celebration_line(celebrations: Any, *, now: datetime, zone: str | None,
                     join_date: date | None, last_reply_at: datetime | None) -> str:
    """The line of this turn, or ``""``. Pure.

    Something must be celebrated on the member's local date, and no assistant
    reply may have come since local midnight. So the first reply of the day
    gets the line, and the second gets none. The next day starts again."""
    if already_replied_today(last_reply_at, now, zone):
        return ""
    return line(celebrated_today(celebrations, local_today(now, zone), join_date))


def lookback_since(now: datetime) -> datetime:
    """The start of the once-a-day read (:data:`LOOKBACK_HOURS` back)."""
    moment = now if now.tzinfo is not None else now.replace(tzinfo=UTC)
    return moment - timedelta(hours=LOOKBACK_HOURS)


#: One read for a turn, in the caller's tenant session: the member's
#: ``celebrations`` key (never the rest of the profile), the newest assistant
#: reply with content in any chat of the member since ``:since``, and the
#: People join date. ``:m`` is the verified member in lower case and ``:org``
#: the run's org, both from the run boundary and never from a payload (R5).
#: The profile row follows the row rule of ``assistant_profile.READ_SQL``.
READ_SQL = (
    "SELECT "
    "(SELECT assistant_profile -> 'celebrations' FROM user_settings "
    " WHERE organization_id = CAST(:org AS uuid) AND lower(user_id) = :m "
    " ORDER BY (assistant_profile IS NULL), (user_id <> lower(user_id)) LIMIT 1), "
    "(SELECT max(m.created_at) FROM chat_session s "
    " JOIN chat_message m ON m.session_id = s.id "
    " WHERE s.organization_id = CAST(:org AS uuid) "
    " AND lower(btrim(s.user_id)) = :m AND s.updated_at >= :since "
    " AND m.role = 'assistant' AND m.content <> '' AND m.created_at >= :since), "
    "(SELECT p.start_date FROM people p "
    " WHERE p.organization_id = CAST(:org AS uuid) AND lower(p.email) = :m LIMIT 1)"
)


# ── The flag ───────────────────────────────────────────────────────────────


def celebrations_for(org_id: str | None) -> bool:
    """True when celebrations are on for *org_id*. Fails closed.

    ``CELEBRATIONS_ORGS`` (``celebrations_orgs``) is a comma list of
    organization ids, or ``*`` for each one. Empty means off, which is the
    default. The profile must be on for the org too
    (``assistant_profile.enabled_for``). It reads ``get_settings()`` on each
    call, so a flag reload (``gateway/flag_reload.py``) applies a change with
    no restart. The caller passes the bound tenant, never an id from request
    input (R5)."""
    try:
        org = str(org_id or "").strip().lower()
        if not org:
            return False
        from acb_common import get_settings
        from acb_common.assistant_profile import _parse_orgs, enabled_for

        orgs = _parse_orgs(str(getattr(get_settings(), "celebrations_orgs", "") or ""), star=True)
        if "*" not in orgs and org not in orgs:
            return False
        return enabled_for(org)
    except Exception:
        return False


__all__ = [
    "FESTIVALS", "Festival", "Today", "already_replied_today", "catalogue",
    "celebrated_today", "celebration_line", "celebrations_for",
    "clean_celebrations", "has_content", "line", "stored_celebrations",
]
