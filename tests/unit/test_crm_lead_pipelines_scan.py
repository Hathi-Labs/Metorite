"""CIV-33d: the one source-tree scan of ``test_crm_lead_pipelines.py``.

The scan walks every Python file of the gateway, so ``affected_tests`` runs
this file on every Python change. It lives apart from the large R8 file so
that file runs only when its own imports change. The assertions are unchanged.
"""

from __future__ import annotations

import re
from pathlib import Path

from tests.unit.test_crm_lead_pipelines import _kind_misses

_ROOT = Path(__file__).resolve().parents[2]
_GATEWAY = _ROOT / "apps" / "services" / "gateway" / "gateway"


def test_each_read_of_crm_pipelines_names_the_kind_or_an_id() -> None:
    """Acceptance 2. A deal reader names ``kind = 'deal'``, so a lead pipeline
    never shows on a deal surface. A read by id names the id, and its caller
    checks the kind (``core.require_deal_pipeline``, or the kind of the Undo
    row). Mutation: drop the term from ``default_pipeline_id`` -> red here and
    in the database test below."""
    misses: list[str] = []
    by_id: list[str] = []
    for path in sorted(_GATEWAY.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        where = str(path.relative_to(_GATEWAY)).replace("\\", "/")
        misses += [f"{where}:{line}" for line in _kind_misses(source)]
        by_id += [where] * len(re.findall(
            r"(?:require|load)_row\(\s*db,\s*\"crm_pipelines\"", source))
    assert misses == [], misses
    # The id reads: the one deal check of core, and the two Undos of this seam.
    assert sorted(by_id) == ["routes/crm/core.py", "routes/crm/pipelines.py",
                             "routes/crm/pipelines.py"], by_id
