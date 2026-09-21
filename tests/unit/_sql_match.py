r"""Match a table name in a fake database's SQL, without swallowing its family.

**Why this exists.** The People tables lost their ``gtd_`` prefix on
2026-09-21. ``gtd_people`` was unambiguous, so every fake in the suite
dispatched on ``"FROM gtd_people" in sql`` and that was correct. ``people`` is
not unambiguous — it is a PREFIX of ``people_absences``, ``people_skills``,
``people_credentials`` and ``people_resumes``.

So the sweep turned a correct check into a wrong one, silently. In
``test_projects_assignees.py`` the ``people`` arm came first and answered the
absences query with directory rows, and the route then read ``person_id`` off a
``SimpleNamespace`` that had a ``name``. Eight tests failed with an
``AttributeError`` a long way from the cause. Six other fakes carried the same
shape.

Reordering the arms fixes today and breaks again the day somebody adds
``people_agents``. A negative lookahead fixes it for good: ``people_absences``
has a word character after ``people``, so ``people(?!\w)`` does not match it.

⚠️ ``\b`` is NOT the same thing, and it was the first attempt. ``\b`` is a
boundary BETWEEN a word character and a non-word one. After a clause that ends
in ``)`` — ``"SELECT name FROM people WHERE lower(email)"`` — it therefore
demands that a word character follow, and the match fails against SQL that
reads ``lower(email) = :e``. ``(?!\w)`` says only what we mean.

One helper rather than seven regexes, per root ``CLAUDE.md`` §5.
"""

from __future__ import annotations

import re
from functools import lru_cache


@lru_cache(maxsize=None)
def _pattern(clause: str) -> re.Pattern[str]:
    return re.compile(re.escape(clause) + r"(?!\w)", re.IGNORECASE)


def hits(sql: str, clause: str) -> bool:
    """True when *sql* names exactly this table, not a longer one beside it.

    *clause* is the fragment as you would have written it in an ``in`` test —
    ``"FROM people"``, ``"INSERT INTO people"``, ``"UPDATE people"``. The
    lookahead is applied at the END of the fragment, which is where the table
    name is.
    """
    return _pattern(clause).search(sql) is not None
