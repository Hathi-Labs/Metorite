"""The shell's own routes (`project-docs/specs/navigation_shell.md`).

One router. `search` declares it and serves tier 1, and `intent` adds tier 2 to
it. Importing `intent` is what registers its route, so the import stays.
"""

from gateway.routes.shell import intent as _intent  # noqa: F401  (registers /shell/intent)
from gateway.routes.shell.search import router

__all__ = ["router"]
