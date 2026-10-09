"""The shell's own routes (`project-docs/specs/navigation_shell.md`).

One router. `search` declares it and serves tier 1, `intent` adds tier 2 to
it, and `needs` adds the needs feed (§7.2). Importing `intent` and `needs` is
what registers their routes, so the imports stay.
"""

from gateway.routes.shell import intent as _intent  # noqa: F401  (registers /shell/intent)
from gateway.routes.shell import needs as _needs  # noqa: F401  (registers /shell/needs)
from gateway.routes.shell.search import router

__all__ = ["router"]
