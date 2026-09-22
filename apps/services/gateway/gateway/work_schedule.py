"""The company's working week — one model, three layers (WS-28p).

Spec: ``project-docs/specs/people_center_app.md`` §3.4a · **D-PC-16**, **D-PC-18**.

    layer 1  org policy      org_settings['work_schedule']  — the company's week
    layer 2  person override people.working_hours       — their exceptions
    layer 3  effective       computed here, stored nowhere

WS-28k adds the fourth thing on top of the three layers: **absences**, which do
not change the schedule but do subtract from it. `working_days_between` and
`working_hours_between` are what "at risk" is built on — the question is not
"is the deadline far away" but "do they have the hours before it".

**A leaf module, deliberately outside both route packages.** Two consumers need
it and they sit on opposite sides of an import direction that must not close:
``routes/people/*`` (the surfaces that read and edit it) and
``routes/tasks/settings.py`` (the calendar, which SEEDS its day window from it).
The People package already imports from the tasks package and never the reverse,
so a shared model living in either one would force a cycle. It lives here and
both import it.

Everything below the cleaners is **pure** — no DB, no request, no I/O. The two
readers at the end (:func:`load_policy`, :func:`person_schedule`) take the
session they are handed and **never acquire one**, so they add no connection
site (R5b) and stay importable from either package. They live here rather than
beside the routes for the same cycle reason as the model.

Why the layering, rather than a column per knob
-----------------------------------------------
An org that works Monday-Saturday, a half-timer, and a night-shift technician
are three different answers to the same question, and only the third is well
modelled by a shift list. Layer 2 exists so the exceptions do not force layer 1
to grow a field per exception.

What this is NOT
----------------
⚠️ It is **not** the calendar's day window. ``user_settings.day_start_hour`` /
``day_end_hour`` / ``daily_capacity_mins`` (migrations 77 and 97) answer *"when
may the planner place blocks in my day"* — a private preference. This answers
*"when is this person contracted to work"* — a fact about the engagement, visible
to colleagues. The direction is **People → Calendar, seeded once, never
mirrored** (D-PC-16): a person who has never set their calendar preferences gets
them derived from here, and from the moment they save one it is *theirs*. A
seeded default that later diverges is somebody changing their mind; a mirror
that diverges is a bug, and only one of those is worth building.
"""

from __future__ import annotations

import json
import re
from typing import Any

#: The key in ``org_settings``. One name, so a typo cannot create a second
#: policy nobody can find.
POLICY_KEY = "work_schedule"

#: What an organisation that has never opened the settings page gets. Chosen to
#: be unremarkable rather than clever: five days, eight hours, a working day
#: that starts at 09:30 — wrong for somebody, but wrong in a way that is visible
#: and one edit away, which is the best a default can do.
DEFAULT_POLICY: dict[str, Any] = {
    "working_days": [1, 2, 3, 4, 5],     # ISO: 1 = Monday … 7 = Sunday
    "hours_per_day": 8.0,
    # The company's STANDARD day. Shifts (below) are named alternatives, not
    # the only way to have hours — a company with one working pattern should
    # not have to model it as a shift and then put every employee on it.
    #
    # ⚠️ These two exist because the live run found their absence: with times
    # only inside shifts, a person who had named no shift had no start or end
    # at all, so the calendar seed had nothing to derive a day window from and
    # silently fell back to migration 77's 07:00-22:00. The hermetic suite
    # could not see it — every fixture it used named a shift.
    "start": "09:30",
    "end": "18:00",
    "week_start": 1,
    "default_timezone": "UTC",
    "shifts": [],
    "holidays": [],
}

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

#: Bounds, not opinions. A 24-hour day and a 7-day week are legal because
#: somewhere they are true; 25 hours is a typo, and 0 days is a company that
#: does not work — both of which should be refused where they are typed rather
#: than divided by later.
MAX_HOURS_PER_DAY = 24.0
MAX_SHIFTS = 12
MAX_HOLIDAYS = 200


class ScheduleError(ValueError):
    """A policy or override the product refuses, with the reason as its text.

    A plain ``ValueError`` so this module keeps no web framework import — the
    route turns it into a 400 that repeats the sentence. Validation lives here
    rather than in the route because both doors (the policy PUT and a person's
    own override) have to refuse the same shapes.
    """


def normalise_policy(raw: Any) -> dict[str, Any]:
    """A stored blob → a complete, sane policy. Never raises.

    **Read-side repair, not validation.** The stored value came from a JSONB
    column that anything could have written — an older release, a hand-run SQL
    statement, a half-finished migration — and a directory that 500s because
    somebody typed ``"eight"`` into a settings row is worse than one that falls
    back to the default for that field. :func:`validate_policy` is the strict
    half, and it runs on the way IN.
    """
    policy = dict(DEFAULT_POLICY)
    if not isinstance(raw, dict):
        return policy

    days = _clean_days(raw.get("working_days"))
    if days:
        policy["working_days"] = days

    hours = _clean_hours(raw.get("hours_per_day"))
    if hours is not None:
        policy["hours_per_day"] = hours

    week_start = raw.get("week_start")
    if isinstance(week_start, int) and 1 <= week_start <= 7:
        policy["week_start"] = week_start

    tz = raw.get("default_timezone")
    if isinstance(tz, str) and tz.strip():
        policy["default_timezone"] = tz.strip()

    # `None` is meaningful here and different from absent: an org that works a
    # number of hours with no fixed clock (fully async, or field staff) says so
    # by clearing these, and nothing should put 09:30 back.
    for edge in ("start", "end"):
        if edge in raw:
            policy[edge] = _clean_time(raw.get(edge))

    policy["shifts"] = [s for s in (_clean_shift(s)
                                    for s in _as_list(raw.get("shifts")))
                        if s][:MAX_SHIFTS]
    policy["holidays"] = [d for d in _as_list(raw.get("holidays"))
                          if isinstance(d, str) and _DATE_RE.match(d)][:MAX_HOLIDAYS]
    return policy


def validate_policy(raw: Any) -> dict[str, Any]:
    """Refuse a policy the product cannot mean, naming what is wrong.

    Strict where :func:`normalise_policy` is forgiving, and that asymmetry is
    the point: a value nobody can type again is repaired on read, and a value
    somebody is typing right now is refused while they are still looking at it.
    """
    if not isinstance(raw, dict):
        raise ScheduleError("The work schedule must be an object.")

    days = raw.get("working_days")
    if days is not None:
        if not isinstance(days, list) or not days:
            raise ScheduleError(
                "working_days must list at least one day (1 = Monday … 7 = Sunday).")
        if _clean_days(days) != sorted({int(d) for d in days
                                        if isinstance(d, int)}):
            raise ScheduleError(
                "working_days must be whole numbers from 1 (Monday) to 7 (Sunday).")

    for edge in ("start", "end"):
        value = raw.get(edge)
        if value not in (None, "") and not _TIME_RE.match(str(value)):
            raise ScheduleError(
                f"The working day's {edge} time '{value}' is not valid — use "
                "HH:MM on a 24-hour clock.")

    hours = raw.get("hours_per_day")
    if hours is not None:
        if not isinstance(hours, int | float) or isinstance(hours, bool):
            raise ScheduleError("hours_per_day must be a number.")
        if not 0 < float(hours) <= MAX_HOURS_PER_DAY:
            raise ScheduleError(
                f"hours_per_day must be between 0 and {MAX_HOURS_PER_DAY:g}.")

    _validate_clock(raw)
    _validate_shifts(raw)
    _validate_holidays(raw)
    return normalise_policy(raw)


def _validate_clock(raw: dict[str, Any]) -> None:
    """The company's standard day. Empty is legal (no fixed clock); wrong is not."""
    for edge in ("start", "end"):
        value = raw.get(edge)
        if value not in (None, "") and not _TIME_RE.match(str(value)):
            raise ScheduleError(
                f"The working day's {edge} time '{value}' is not valid - use "
                "HH:MM on a 24-hour clock.")


def _validate_shifts(raw: dict[str, Any]) -> None:
    for shift in _as_list(raw.get("shifts")):
        if not isinstance(shift, dict) or not str(shift.get("name") or "").strip():
            raise ScheduleError("Every shift needs a name.")
        for edge in ("start", "end"):
            value = shift.get(edge)
            if value is not None and not _TIME_RE.match(str(value)):
                raise ScheduleError(
                    f"Shift '{shift.get('name')}' has an invalid {edge} time "
                    f"'{value}' - use HH:MM on a 24-hour clock.")
    if len(_as_list(raw.get("shifts"))) > MAX_SHIFTS:
        raise ScheduleError(f"At most {MAX_SHIFTS} shifts.")


def _validate_holidays(raw: dict[str, Any]) -> None:
    for holiday in _as_list(raw.get("holidays")):
        if not isinstance(holiday, str) or not _DATE_RE.match(holiday):
            raise ScheduleError(f"'{holiday}' is not a date - use YYYY-MM-DD.")
    if len(_as_list(raw.get("holidays"))) > MAX_HOLIDAYS:
        raise ScheduleError(f"At most {MAX_HOLIDAYS} holidays.")


def effective_schedule(policy: Any, override: Any) -> dict[str, Any]:
    """Policy + this person's override → the schedule everything else reads.

    ``source`` travels with the answer, naming which layer decided each field.
    That is what lets a surface say *"Mon-Fri (company), 10:00-16:00 (yours)"*
    instead of showing four numbers a person cannot account for — and it is the
    difference between a schedule somebody trusts and one they work around.

    A ``shift`` named in the override pulls its times from the policy's shift
    list, so changing the general shift's hours moves everybody on it. A shift
    name that no longer exists is **ignored rather than fatal**: shifts get
    renamed, and a person should not lose their whole schedule to it.
    """
    policy = normalise_policy(policy)
    override = override if isinstance(override, dict) else {}
    source: dict[str, str] = {}

    def pick(field: str, from_override: Any, from_policy: Any) -> Any:
        if from_override is not None:
            source[field] = "person"
            return from_override
        source[field] = "org"
        return from_policy

    shift = _find_shift(policy, override.get("shift"))
    if shift is not None:
        source["shift"] = "person"

    days = pick("days",
                _clean_days(override.get("days"))
                or (_clean_days(shift.get("days")) if shift else None),
                policy["working_days"])
    hours = pick("hours_per_day",
                 _clean_hours(override.get("hours_per_day")),
                 policy["hours_per_day"])
    # Person's own time → their shift's → the company's standard day.
    start = pick("start",
                 _clean_time(override.get("start"))
                 or (_clean_time(shift.get("start")) if shift else None),
                 policy.get("start"))
    end = pick("end",
               _clean_time(override.get("end"))
               or (_clean_time(shift.get("end")) if shift else None),
               policy.get("end"))
    tz = pick("timezone", _clean_str(override.get("timezone")),
              policy["default_timezone"])

    # A half-timer works the same days for half the hours. Clamped rather than
    # refused: a stored 1.5 is somebody's mistake, and inventing a 60-hour week
    # from it would put a wrong number on a dashboard people plan against.
    fraction = _clean_fraction(override.get("fraction"))
    if fraction is not None:
        source["fraction"] = "person"
    else:
        fraction = 1.0

    return {
        "days": days,
        "hours_per_day": hours,
        "start": start,
        "end": end,
        "timezone": tz,
        "shift": (shift or {}).get("name") if shift else None,
        "fraction": fraction,
        "source": source,
    }


def contracted_hours_per_week(schedule: dict[str, Any]) -> float:
    """Days x hours x fraction, rounded to a quarter hour.

    **Derived, never stored** (D-PC-18) — the same lesson WS-28b applied to
    *load*, applied to the denominator load was being compared against. The
    typed ``people.capacity_hours_per_week`` stays (R6: the importer writes
    it) and becomes an override this figure is checked against.

    A quarter hour because that is the smallest unit anybody schedules in, and
    a bar labelled ``37.33333h`` is a bar that looks broken.
    """
    days = len(schedule.get("days") or [])
    hours = float(schedule.get("hours_per_day") or 0)
    # ⚠️ NOT `or 1.0`: a fraction of 0.0 is falsy, and `or` would quietly turn
    # "this person is contracted for nothing" into a full 40-hour week — on the
    # denominator every load bar is divided by. Found by the clamping test.
    raw_fraction = schedule.get("fraction")
    fraction = 1.0 if raw_fraction is None else float(raw_fraction)
    return round(days * hours * fraction * 4) / 4


def capacity_disagreement(
    schedule: dict[str, Any], typed: int | None, *, tolerance: float = 1.0,
) -> float | None:
    """How far the hand-typed capacity is from the derived one, or ``None``.

    Surfaced by the data-quality panel (§5.10) rather than silently corrected.
    Overwriting the typed column would be a rename in place of somebody's data
    (R6), and *quietly preferring* one without saying so is how two numbers
    start disagreeing where nobody can see them.
    """
    if typed is None:
        return None
    derived = contracted_hours_per_week(schedule)
    delta = round(float(typed) - derived, 2)
    return delta if abs(delta) > tolerance else None


def working_days_between(schedule: dict[str, Any], start: Any, end: Any,
                         absences: list[dict[str, Any]] | None = None) -> float:
    """How many WORKING days this person has between two dates, inclusive.

    Fractional, because a ``partial`` absence reduces a day rather than removing
    it — a half day is half a day, and rounding it either way makes a week's
    arithmetic wrong by more than the half day.

    Non-working days, holidays and `away` spans count as zero. **This is the
    function "at risk" is built on** (§5.7.2): the question is not "is the
    deadline far away" but "do they have the hours before it", and a week of
    holiday is exactly the difference between those two answers.
    """
    from datetime import date, timedelta

    if not isinstance(start, date) or not isinstance(end, date) or end < start:
        return 0.0
    days = set(schedule.get("days") or [])
    fraction = schedule.get("fraction")
    fraction = 1.0 if fraction is None else float(fraction)
    spans = _absence_spans(absences)

    total = 0.0
    day = start
    while day <= end:
        if day.isoweekday() in days:
            total += fraction * _day_fraction(day, spans)
        day += timedelta(days=1)
    return round(total, 4)


def working_hours_between(schedule: dict[str, Any], start: Any, end: Any,
                          absences: list[dict[str, Any]] | None = None) -> float:
    """The same span in hours — days x hours/day, absences applied."""
    hours = float(schedule.get("hours_per_day") or 0)
    return round(working_days_between(schedule, start, end, absences) * hours, 2)


def absent_on(day: Any, absences: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    """The absence covering ``day``, or None.

    Full absences win over partial ones: somebody who is both on holiday and
    "half a day" is on holiday, and answering "partial" would put them on a
    picker as available.
    """
    covering = [s for s in _absence_spans(absences) if s["starts_on"] <= day <= s["ends_on"]]
    if not covering:
        return None
    full = [s for s in covering if s["kind"] != "partial"]
    return (full or covering)[0]


def _day_fraction(day: Any, spans: list[dict[str, Any]]) -> float:
    """How much of this day is workable: 0 when away, a fraction when partial."""
    covering = [s for s in spans if s["starts_on"] <= day <= s["ends_on"]]
    if not covering:
        return 1.0
    if any(s["kind"] != "partial" for s in covering):
        return 0.0
    # Overlapping partials: the SMALLEST wins. Two claims on the same day are
    # two reasons to be less available, not an average of them.
    return min(float(s.get("fraction") or 0.5) for s in covering)


def _absence_spans(absences: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Rows or dicts → the shape the arithmetic reads, dropping what it cannot use.

    Tolerant on the way in for the same reason :func:`normalise_policy` is: an
    unparseable row should cost its own span, never the whole calculation.
    """
    from datetime import date

    spans: list[dict[str, Any]] = []
    for raw in absences or []:
        get = raw.get if isinstance(raw, dict) else lambda k, r=raw: getattr(r, k, None)
        starts, ends = get("starts_on"), get("ends_on")
        if not isinstance(starts, date) or not isinstance(ends, date):
            continue
        if ends < starts:
            continue
        hours = get("hours_per_day")
        kind = str(get("kind") or "away")
        spans.append({
            "starts_on": starts, "ends_on": ends, "kind": kind,
            # A `partial` with no figure is half a day: the commonest case by
            # far, and better than treating it as a full absence (which would
            # make somebody who took an afternoon off look unavailable all week)
            # or as no absence at all (which would make the note pointless).
            "fraction": (float(hours) if isinstance(hours, int | float) else None),
        })
    return spans


def calendar_seed(schedule: dict[str, Any]) -> dict[str, int]:
    """The calendar day window this schedule implies (D-PC-16).

    Read by ``routes/tasks/settings._load`` **only when the person has no
    ``user_settings`` row at all** — i.e. has never expressed a calendar
    preference. Nothing is written: it is a read-time default, so a schedule
    change still follows a person who has not customised anything, and the
    instant they save one setting the row exists and this stops applying
    forever. That is "seeded once, never mirrored" with no sync to maintain.

    ⚠️ **The limit, stated rather than papered over:** a row created for an
    unrelated setting (a chat model, say) carries the columns' own SQL defaults,
    and this cannot tell that apart from a person who chose 07:00-22:00. It
    does not try. Guessing which stored values were "really chosen" is the
    mirror problem wearing a disguise.
    """
    start = _hour_of(schedule.get("start"))
    end = _hour_of(schedule.get("end"))
    raw_fraction = schedule.get("fraction")
    hours = float(schedule.get("hours_per_day") or 0) * (
        1.0 if raw_fraction is None else float(raw_fraction))
    seed: dict[str, int] = {}
    if start is not None:
        # An hour of margin either side: the plannable window is not the
        # contracted one — people start before and finish after, and a grid
        # that refuses to show it is a grid they stop using.
        seed["day_start_hour"] = max(0, start - 1)
    if end is not None:
        seed["day_end_hour"] = min(23, end + 1)
    if hours > 0:
        # Focus capacity is not the working day. Six hours of deep work in an
        # eight-hour day is the ratio migration 77's own default encodes
        # (360 mins against a 07:00-22:00 window); keeping it means a seeded
        # value lands where a thoughtful person would have put it.
        seed["daily_capacity_mins"] = round(hours * 0.75 * 60)
    return seed


# ── Cleaners ────────────────────────────────────────────────────────────────
# Every one of these answers "or None", so a caller can tell "absent" from
# "present and unusable" — the distinction the whole layering rests on, since
# an absent override falls through to the policy and a broken one must too.

def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _clean_days(value: Any) -> list[int] | None:
    if not isinstance(value, list):
        return None
    days = sorted({int(d) for d in value
                   if isinstance(d, int) and not isinstance(d, bool)
                   and 1 <= d <= 7})
    return days or None


def _clean_hours(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    hours = float(value)
    return hours if 0 < hours <= MAX_HOURS_PER_DAY else None


def _clean_time(value: Any) -> str | None:
    return str(value) if isinstance(value, str) and _TIME_RE.match(value) else None


def _clean_str(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _clean_fraction(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return min(1.0, max(0.0, float(value)))


def _clean_shift(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    name = _clean_str(raw.get("name"))
    if not name:
        return None
    shift: dict[str, Any] = {"name": name}
    for edge in ("start", "end"):
        value = _clean_time(raw.get(edge))
        if value:
            shift[edge] = value
    days = _clean_days(raw.get("days"))
    if days:
        shift["days"] = days
    return shift


def _find_shift(policy: dict[str, Any], name: Any) -> dict[str, Any] | None:
    wanted = _clean_str(name)
    if not wanted:
        return None
    for shift in policy.get("shifts") or []:
        if str(shift.get("name", "")).lower() == wanted.lower():
            return shift
    return None


def _hour_of(value: Any) -> int | None:
    """``"09:30"`` → 9. The seed is hour-granular because the columns are."""
    match = _TIME_RE.match(str(value)) if value else None
    return int(match.group(1)) if match else None


# ── The two readers ─────────────────────────────────────────────────────────
# They take a session; they never open one. That is what keeps them out of
# `_SYNC_ENGINE_ALLOWED` / `_PSYCOPG_ALLOWED` and lets both route packages call
# them without either owning the other.


async def load_policy(db: Any) -> dict[str, Any]:
    """The org's policy row, repaired on read, never raising.

    ⚠️ Read through the caller's session rather than through
    ``acb_common.org_settings.load_org_setting``. That helper serves the
    appearance blob and opens its own **synchronous psycopg connection per
    call** — a cost the appearance setting never pays because nothing reads it
    on a hot path, and this value is read on *every person read*. Using the
    session the caller already holds adds no connection site and, unlike the
    psycopg helper, carries the bound tenant.

    A missing row, an unreachable table and an unparseable blob all answer the
    default. An organisation that has never opened the settings page still has
    a working week.
    """
    try:
        row = (await db.execute(
            _text("SELECT value FROM org_settings WHERE key = :key"),
            {"key": POLICY_KEY},
        )).fetchone()
    except Exception:
        return normalise_policy(None)
    if row is None:
        return normalise_policy(None)
    value = row.value
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            value = None
    return normalise_policy(value)


def person_schedule(policy: dict[str, Any], row: Any) -> dict[str, Any]:
    """The effective schedule for one ``people`` row.

    ONE call site for the layering, so "which layer won" is answered in exactly
    one place — the person page, the directory, the dashboard and the calendar
    seed all read this rather than each combining the two halves their own way.

    Tolerates ``working_hours`` arriving as a JSON **string**: raw ``text()``
    declares no column type, so asyncpg hands jsonb back unparsed.
    """
    override = getattr(row, "working_hours", None)
    if isinstance(override, str):
        try:
            override = json.loads(override)
        except ValueError:
            override = None
    return effective_schedule(policy, override)


def _text(sql: str) -> Any:
    """``sqlalchemy.text``, imported lazily so the pure half needs no SQLAlchemy."""
    from sqlalchemy import text
    return text(sql)
