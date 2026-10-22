"""Knowledge route package — the gateway ``/knowledge`` API (WS-61).

Spec: ``project-docs/specs/knowledge_base.md`` §5.3 and §6.3. KB-1 builds
the four space routes (``spaces.py``) and the nine page routes
(``pages.py``), with ``restore``. KB-3 adds ``GET /knowledge/search``
(``search.py``) over the index in ``gateway/kb_index/``. KB-2f adds the
home lists and the favourites (``home.py``) and the page icon (``icon.py``).
KB-2g adds ``GET /knowledge/pages/{id}/versions/{n}`` (``history.py``), one old
version with its body. KB-5a adds the four proposal routes (``proposals.py``).

Same layout as ``routes/crm``: ``core`` is the leaf. It holds the router, the
one visibility rule (``visible_pages_clause``) and the data helpers. The
feature modules register their routes on the shared ``router`` as an import
side effect. Each path is a literal (``/knowledge/pages/{id}``,
``/knowledge/spaces/{id}/grants``), so the import order cannot change which
handler answers.

The router answers 404 unless ``KNOWLEDGE_ENABLED`` is on and the caller's
organization is on ``KNOWLEDGE_ORGS`` (``flags.py``), then 403 without
``feature:knowledge``. Fences: ``tests/unit/test_kb_visibility_one_seam.py``,
``tests/unit/test_kb_flags.py`` and, against a real database,
``tests/unit/test_kb_pages_r8.py``.
"""

from gateway.routes.knowledge import (  # noqa: F401 — registers routes
    backlinks,
    history,
    home,
    pages,
    proposals,
    search,
    spaces,
)
from gateway.routes.knowledge.core import router

__all__ = ["router"]
