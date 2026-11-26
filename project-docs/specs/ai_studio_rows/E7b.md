# E7b

Tier: STD

Gate: AGENT-SAFE. D200 small visible change, with captures. The owner reviews it after the merge

## What

**The workflow canvas takes the member's accent and the one look** (task 3340). The editor already follows light mode. Two defects stayed. xyflow adds a `light` class to its wrapper, and that class matches the `.light {` block of `globals.css` a second time. So the accent and the glass tokens stopped at the canvas edge. xyflow also paints with its own hex colours. `src/app/workflows/lib/canvasLook.ts` sets `inherit` for each token of that block and maps each `--xy-*` variable to a token. The editor passes it as `style={CANVAS_STYLE}`. The editor still has no phone layout. Row E7 owns that

## Done when

Under a Rose accent the selected ring, the edges and the insert button are Rose, in light and dark. `canvasLook.test.ts` passes. It reads `globals.css` and the xyflow style sheet as text. A removed `inherit`, a colour literal or a missing prop turns it red
