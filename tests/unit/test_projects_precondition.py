"""WS-27bi / D-PM-20 — the ``If-Match`` write precondition.

Every case below except the plain match/mismatch pair exists because the R8
measurement (spec §9.10.1) found a way the obvious implementation reports a
safety it does not have. They are fences on measured behaviour, not style.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from gateway.routes.projects.core import parse_precondition, require_precondition

STAMP = datetime(2026, 8, 14, 9, 21, 49, 448124, tzinfo=UTC)


def _row(updated_at: datetime = STAMP) -> SimpleNamespace:
    return SimpleNamespace(updated_at=updated_at)


# ── The advisory contract ────────────────────────────────────────────────────


def test_absent_header_succeeds() -> None:
    """D-PM-20: advisory now, mandatory later.

    Compulsory on day one breaks every existing caller at once, which is the
    failure the decision exists to prevent.
    """
    require_precondition(_row(), None, {})


def test_matching_token_succeeds() -> None:
    require_precondition(_row(), STAMP.isoformat(), {})


def test_an_unresolved_header_default_counts_as_absent() -> None:
    """FastAPI resolves a ``Header(...)`` default only through the HTTP layer.

    An endpoint called DIRECTLY -- which most of this package's tests do -- gets
    the sentinel object itself, not ``None``. Treating that as a supplied token
    turned 18 existing tests into 400s when this was first wired, so the guard
    is ``isinstance(str)`` rather than ``is not None``.
    """
    from fastapi.params import Header as HeaderParam

    require_precondition(_row(), HeaderParam(default=None), {})  # type: ignore[arg-type]


def test_mismatched_token_is_412_carrying_the_current_row() -> None:
    current = {"id": "t1", "title": "as it now stands"}
    with pytest.raises(HTTPException) as exc:
        require_precondition(_row(), (STAMP + timedelta(microseconds=1)).isoformat(), current)
    assert exc.value.status_code == 412
    # The body carries the row so a client can say WHAT changed, not merely that
    # something did.
    assert exc.value.detail["current"] == current


# ── Fence 1: the naive token. MEASURED, not assumed. ─────────────────────────


def test_naive_token_is_refused() -> None:
    """A tz-less token must 400 rather than compare.

    Measured: asyncpg reinterprets a naive datetime in the session zone, so on a
    UTC box an offset-stripped token silently compares EQUAL. It would pass
    every test here and start mis-comparing the moment the session TZ moved --
    the exact "reports safety it does not have" failure D-PM-20 was written
    against. Deleting the tzinfo check turns this red.
    """
    naive = STAMP.replace(tzinfo=None).isoformat()
    assert "+" not in naive
    with pytest.raises(HTTPException) as exc:
        require_precondition(_row(), naive, {})
    assert exc.value.status_code == 400
    assert "offset" in str(exc.value.detail)


def test_naive_token_is_refused_even_when_it_would_have_matched() -> None:
    """The dangerous half: it is refused because it is ambiguous, not because
    it disagrees. A naive token naming the very same wall-clock time is still a
    400 -- otherwise the check would pass on UTC and fail nowhere else."""
    with pytest.raises(HTTPException) as exc:
        require_precondition(_row(), "2026-08-14T09:21:49.448124", {})
    assert exc.value.status_code == 400


# ── Fence 2: instants, never strings. MEASURED. ──────────────────────────────


def test_trailing_zero_microseconds_match() -> None:
    """pg's ``::text`` renders this ``.1`` where the encoder renders ``.100000``.

    Equal as instants, different as strings. A string comparison passes every
    test written against ordinary microseconds and fails exactly here.
    """
    stamp = datetime(2026, 3, 4, 5, 6, 7, 100000, tzinfo=UTC)
    require_precondition(_row(stamp), stamp.isoformat(), {})
    require_precondition(_row(stamp), "2026-03-04T05:06:07.1+00:00", {})


def test_zero_microseconds_match() -> None:
    """``isoformat`` omits the fraction entirely when it is zero, so the token
    changes SHAPE on ~1 row in a million. It must still compare."""
    stamp = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
    assert stamp.isoformat() == "2026-01-01T00:00:00+00:00"
    require_precondition(_row(stamp), stamp.isoformat(), {})


def test_a_different_offset_naming_the_same_instant_matches() -> None:
    """Comparison is by instant. ``+05:30`` for the same moment is a match, and
    would not be under any string comparison."""
    other = STAMP.astimezone(timezone(timedelta(hours=5, minutes=30)))
    assert other.isoformat() != STAMP.isoformat()
    require_precondition(_row(), other.isoformat(), {})


# ── Parsing ──────────────────────────────────────────────────────────────────


def test_quoted_etag_spelling_is_tolerated() -> None:
    require_precondition(_row(), f'"{STAMP.isoformat()}"', {})


@pytest.mark.parametrize("token", ["", "not-a-time", "2026-13-45T99:99:99+00:00", '""'])
def test_unparseable_tokens_are_400(token: str) -> None:
    with pytest.raises(HTTPException) as exc:
        require_precondition(_row(), token, {})
    assert exc.value.status_code == 400


def test_parse_returns_an_aware_datetime() -> None:
    assert parse_precondition(STAMP.isoformat()).tzinfo is not None


# ── The two touch=False sites, and why they stay ─────────────────────────────


def test_touch_false_is_used_only_for_recurrence_bookkeeping() -> None:
    """D-PM-20's audit turned on this fact, so it is fenced rather than trusted.

    ``updated_at`` has to serve TWO consumers at once -- migration 168's keyset
    delta cursor and this precondition -- and the board's recorded worry was that
    one semantic could not cover both. It does, because the only writes that opt
    out of the touch are ``recurrence_spawned_at`` stamps, and such a stamp
    should neither wake a delta client nor invalidate a human's pending edit.

    A THIRD ``touch=False`` site would break that reasoning silently: some real
    edit would stop moving ``updated_at``, and this precondition would then
    report "unchanged" for a row that had changed. Whoever adds one has to come
    here and argue it for both consumers.

    **The third site, argued (WS-41 I-3b, 2026-09-28): ``import_writer.py``.**
    A re-import of a task that did not change writes ONLY ``pm_tasks.origin``:
    the importer's own bookkeeping (``import_values``, ``import_source``,
    ``updated_by_run``). No client renders those keys, and the keys a client
    reads (``kind`` and the email fields) do not change. So for the delta
    cursor there is nothing new to send, and for this precondition a member's
    pending edit is of a row whose visible fields did not move. Touching would
    re-send 2,423 rows and fail every open editor's next save after a no-op
    re-import. Any import write that changes a real field goes through the
    ordinary touch. ``tests/live/live_ws41_writer.py`` check 4c.2 fences the
    behaviour.
    """
    from pathlib import Path

    pkg = Path(__file__).resolve().parents[2] / ("apps/services/gateway/gateway/routes/projects")
    sites = [
        (path.name, line.strip())
        for path in sorted(pkg.glob("*.py"))
        for line in path.read_text(encoding="utf-8").splitlines()
        if "touch=False" in line
    ]
    assert [name for name, _ in sites] == [
        "import_writer.py",
        "recurrence.py",
        "recurrence.py",
    ], sites
