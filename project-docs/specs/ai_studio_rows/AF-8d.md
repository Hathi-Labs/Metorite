# AF-8d

Tier: STD

Gate: AGENT-SAFE. After AF-8a and AF-3a. **Status: BUILT, dark (2026-11-14, task 3281).** No UI, no migration and no flag

## What

**Mira learns the rail count** (task 3281, follow-up of AF-8a and AF-3a). Rung 4. The `frame` part of `studio_describe` teaches the optional `count: true` of a rail row. The cap `nav_counts` comes from `MAX_NAV_COUNTS`, and two rules say that the page of a counted row needs a table or board view (in a cell, a tab or the side), and that the filter and the sort stay on the view. `FRAME_EXAMPLE` has one counted row. The outline Nav line adds `, N with a count` only when a row has a count, so a manifest with no count keeps its old text byte for byte. The G1 skeleton still writes no `pages`, no `nav` and no `count`. **Non-goals:** any screen, any new manifest key

## Done when

`tests/unit/test_studio_frame_tools.py`: the example validates with a count, the caps move with the model (a mutation of each imported constant by +100 moves its cap), a count row on a page with no table or board view is refused, 9 counts are refused, the outline cases and the skeleton. Verify: `uv run pytest tests/unit/test_studio_frame_tools.py tests/unit/test_studio_describe.py`
