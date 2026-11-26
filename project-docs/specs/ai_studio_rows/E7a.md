# E7a

Tier: STD

Gate: AGENT-SAFE. D145 preview before the merge

## What

🔨 **BUILT (2026-11-15, task 3341).** **The workflow editor on a phone.** Parent: E7 (task 1303).

At 390 px the editor drew three panes side by side. The palette and the inspector took 464 px, so the canvas got 0 px. Test and Publish fell off the toolbar.

Below 768 px the editor now takes the shape of the Studio IDE on a phone. The toolbar wraps to two rows. The name and the status are on top. The trigger, Save, Validate, Test and Publish are below. Save and Validate show their icon, and Publish is the icon button of the IDE.

A segmented tab bar (`Tabs`, `chrome="inline"`) picks ONE pane: Palette, Canvas, Step or Copilot. The canvas is the first tab. A tap on a block adds the step and opens the Step tab with its settings. A "+" on a wire opens the palette. The prompt on the canvas opens the Copilot, and Test opens the canvas.

The run console stays shut until Test. The notice of an action shows on its own line. The handles of a step grow to 20 px under a coarse pointer. The rules live in `src/app/workflows/lib/editorPhone.ts`. The palette, the Copilot and the inspector take an optional `fill`.

**Non-goals.** No change from 768 px up, so the desktop tree and its class strings stay the same. No change to `AppTopBar`, to the trigger panel or to the node text (row E7c-1). Drag from the palette stays a desktop act, because a tap adds the block.

## Done when

At 390 px the canvas is 300 px wide or more, and Test and Publish are inside the viewport. Each tab draws one pane. After a tap on a block, the Step tab shows that step.

The new tests pass. `editorPhone.test.ts` holds the model, the source of the page and the handles. `paneFill.test.tsx` holds that a pane with no `fill` keeps its class byte for byte.

At 768, 1024 and 1440 px the palette and the inspector keep their width. Visual review at 390 px in dark, light, compact density and a changed accent.
