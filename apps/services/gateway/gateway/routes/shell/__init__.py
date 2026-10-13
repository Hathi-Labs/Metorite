"""The shell's own routes (`project-docs/specs/navigation_shell.md`).

One router. `search` declares it and serves tier 1, `intent` adds tier 2 to
it, `needs` adds the needs feed (§7.2), and `feed` adds My Day's feed
(`my_day_feed.md` §14.1a). Importing `intent`, `needs` and `feed` is what
registers their routes, so the imports stay. `feed` imports `needs` itself,
so the needs providers always register before the feed-only ones.
"""

from gateway.routes.shell import feed as _feed  # noqa: F401  (registers /shell/feed)
from gateway.routes.shell import intent as _intent  # noqa: F401  (registers /shell/intent)
from gateway.routes.shell import needs as _needs  # noqa: F401  (registers /shell/needs)
from gateway.routes.shell.search import router

__all__ = ["router"]
