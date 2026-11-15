# AF-8a

Tier: STD

Gate: AGENT-SAFE. After AF-2. **Status: BUILT, dark (2026-11-14, task 3156).** No UI, no migration and no flag **Follow-up (AF-3a, task 3252): DONE in AF-8d (task 3281).** The `frame` part of `studio_describe` teaches the optional `count` of a rail row (a cap of 8, a table or board page). AF-8a was built without it

## What

**Mira writes the frame** (task 3156). Rung 4. `studio_describe` (G1b) gets a `frame` part. The outline gets a Pages line and a Nav line. The `frame` part holds the caps (read from the model constants), the rules that a schema cannot say, the defaults that the app draws, the icon list and one example of `pages` and `nav`. **The skeleton keeps `pages` and `nav` absent (decision).** A `nav` in the skeleton would freeze the defaults when Mira adds objects, so the client draws the defaults of §5.5 rule 3. **Non-goals:** `record` (the schema has no such key, and no slice owns it: open), the three frame prompts of the build eval (they wait for G6 and WEB-12, and a live eval run is an owner gate), the rail count (lane-ui AF-3a), and any screen

## Done when

From one chat turn, Mira adds a rail row and a page. The describe test holds the new part. Fences: `tests/unit/test_studio_describe.py`, `tests/unit/test_studio_frame_tools.py` (the example validates, the outline lines, the skeleton, and the patch cases through the real `studio_patch`)
