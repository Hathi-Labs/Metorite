"""The shell's own routes (`project-docs/specs/navigation_shell.md`).

One router. `search` declares it and serves tier 1, `intent` adds tier 2 to
it, `needs` adds the needs feed (§7.2), `feed` adds My Day's feed
(`my_day_feed.md` §14.1a), `prefs` adds My Day's feed preferences
(`my_day_feed.md` §13.3), `state` adds the card state write (§6.8), and
`feed_measure` adds the measure read of `decide` (§10.9 item 10). Importing
`intent`, `needs`, `feed`, `prefs`, `state` and `feed_measure` is what
registers their routes, so the imports stay. `feed` imports `needs` itself,
so the needs providers always register before the feed-only ones.
"""

from gateway.routes.shell import feed as _feed  # noqa: F401  (registers /shell/feed)
from gateway.routes.shell import (  # noqa: F401  (registers /shell/feed/decide-measure)
    feed_measure as _feed_measure,
)
from gateway.routes.shell import intent as _intent  # noqa: F401  (registers /shell/intent)
from gateway.routes.shell import needs as _needs  # noqa: F401  (registers /shell/needs)
from gateway.routes.shell import prefs as _prefs  # noqa: F401  (registers /shell/feed/prefs)
from gateway.routes.shell import state as _state  # noqa: F401  (registers /shell/feed/state)
from gateway.routes.shell.search import router

__all__ = ["router"]
