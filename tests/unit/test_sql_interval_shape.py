"""``CAST(:param AS interval)`` is never the right shape. One rule, no exceptions.

⚠️ **Why this is a separate file from `test_projects_sql_asyncpg.py`.** That
suite is the strong fence — it runs the real SQL on the real driver — and it
SKIPS when `TENANT_LADDER_DATABASE_URL` is unset, which is how CI runs today
(H-112 records the same gap for `test_tenant_coverage`). A fence that only
fires on a developer's machine is not a fence. This file needs no database,
so it runs everywhere, always.

**The fault it exists for.** `/projects/analytics/stuck` answered 500 at every
scope from the day it merged until 2026-09-17, because it bound the string
``'0 days'`` through ``CAST(:d0 AS interval)``. psycopg accepts that shape.
asyncpg refuses it, and `acb_common.db` rewrites every production DSN onto
asyncpg. So the R8 suite beside it went green while every caller got a 500 —
measured, not assumed: reintroducing the bug fails the asyncpg suite and
leaves all 19 psycopg tests passing.

**Why `interval` and not also `timestamptz`.** The rule has to be
exception-free or it grows an allowlist, and an allowlist rots. For
``interval`` there is a strictly better shape with no downside —
``make_interval(days => :d)`` taking an int — so *no* correct code needs the
cast. ``CAST(:x AS timestamptz)`` is different: binding a real `datetime`
through it is legitimate, and `email/automation/followups.py` does exactly
that. Linting it would fail correct code. The asyncpg suite and
`filters.parse_when`'s own docstring cover that half.

⚠️ **Five modules already carried a COMMENT about this trap** — `delta.py`
twice, `filters.parse_when`, `tasks/people.py` twice, `crm/core.py` — and it
shipped anyway. Prose in a neighbouring module is not a fence. This is (R7).
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

#: Where server SQL lives. Tests and docs may quote the bad shape freely —
#: this very file does, and so does the suite that proves it is refused.
_ROOTS = (
    REPO / "apps/services/gateway/gateway",
    REPO / "packages",
)

#: ``CAST(:name AS interval)``, in any spacing or case.
#:
#: ⚠️ **The parameter name may contain an f-string hole, and the first draft
#: of this regex could not see one.** The shape that actually shipped was
#: ``f"... CAST(:d{low} AS interval)"`` — built in a loop over `STALE_BANDS`,
#: so the literal text on the line is ``:d{low}`` and a plain identifier
#: pattern misses it entirely. Caught by mutation, not by reading: the lint
#: was written, the bug was reintroduced, and the lint stayed green.
#:
#: That is the same failure as the fence this replaces
#: (`"7to14d".isalnum()` is True). A fence must be tested against the exact
#: text it exists to reject, not against a tidied version of it.
_BAD = re.compile(
    r"CAST\s*\(\s*:(?:[A-Za-z0-9_]|\{[^}]*\})+\s+AS\s+interval\s*\)",
    re.I,
)

#: A line that only TALKS about the shape. Every hit in the tree today is one
#: of these — the comments that documented the trap without stopping it.
_PROSE = re.compile(r"^\s*(#|\*|\"\"\"|'''|--)")


def _offenders() -> list[str]:
    hits: list[str] = []
    for root in _ROOTS:
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for n, line in enumerate(text.splitlines(), 1):
                if _BAD.search(line) and not _PROSE.match(line):
                    hits.append(f"{path.relative_to(REPO)}:{n}: {line.strip()}")
    return hits


def test_no_module_casts_a_bound_parameter_to_an_interval():
    """⚠️ If this fails, the fix is `make_interval(unit => :param)` with an INT.

    Not a string, and not a cast. `analytics.stale_bands_sql` is the worked
    example, and the rest of `analytics.py` already used that idiom for weeks
    — which is how one module came to hold two shapes, only one of which ran.
    """
    found = _offenders()
    assert not found, (
        "CAST(:param AS interval) fails on asyncpg, which is the driver"
        " production runs. Bind an int through make_interval() instead:\n  "
        + "\n  ".join(found)
    )


def test_the_lint_can_actually_see_the_shape_it_forbids():
    """⚠️ The fence's own fence.

    The assertion above passes on an empty tree, on a broken glob, and on a
    regex that matches nothing — all of which look identical to success. The
    predecessor to this test failed exactly that way: it asked
    `"7to14d".isalnum()` and passed on the value it existed to reject.
    """
    assert _BAD.search("now() - CAST(:d0 AS interval)")
    assert _BAD.search("CAST( :weeks   AS   INTERVAL )")
    # ⚠️ THE SHAPE THAT ACTUALLY SHIPPED — built inside an f-string, so the
    # source line reads `:d{low}`. The first version of this lint could not
    # see it, and passed while the bug was present.
    assert _BAD.search('f"t.updated_at <= now() - CAST(:d{low} AS interval)"')
    assert _BAD.search("CAST(:d{high} AS interval)")
    # The shapes that must NOT trip it.
    assert not _BAD.search("make_interval(days => :d0)")
    assert not _BAD.search("make_interval(days => :d{low})")
    assert not _BAD.search("CAST(:since AS timestamptz)")
    assert not _BAD.search("CAST(:pid AS uuid)")


def test_the_lint_reads_real_files():
    """It found nothing above — prove that is because the tree is clean, and
    not because the walk visited zero files."""
    seen = [
        p
        for root in _ROOTS
        if root.exists()
        for p in root.rglob("*.py")
        if "__pycache__" not in p.parts
    ]
    assert len(seen) > 50, f"the walk only reached {len(seen)} files"
