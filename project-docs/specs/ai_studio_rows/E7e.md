# E7e

Tier: STD

Gate: AGENT-SAFE. D145 preview before the merge

## What

🔨 **BUILT (2026-11-15, task 3373).** **The workflow editor from 768 to 1023 px, and four repairs that E7a left.** Parent: E7 (task 1303). After E7a.

With the shell sidebar open, a 1024 px window left the editor 744 px. The palette and the inspector took 497 px, so the canvas got 247 px. At 768 px the canvas got 0 px, and Test and Publish fell off the toolbar.

The editor now measures its own box. `layoutFor` in `src/app/workflows/lib/editorPhone.ts` picks the layout. A phone, or a box narrower than `WIDE_EDITOR_MIN_PX` (900 px), takes the phone layout of E7a: one pane under the tab bar. A box of 900 px or more takes the three panes, and that tree is the same as before. `src/app/workflows/lib/useContainerWidth.ts` reads the width with a `ResizeObserver`.

A tap on a block on a desktop now selects the new step. Before, the page passed an inline arrow to `onSelectionChange`, and React Flow then sent its report again on each render. That repeat cleared the step. The handler is now stable, the new step is also selected on the canvas, and `selectionAfterChange` in `src/app/workflows/lib/editorSelection.ts` holds the rule.

On the phone layout the trigger panel opens below the two toolbar rows (`top-28`), and the palette hint reads "Tap a step to add it". The notice shows on its own line only. The "+" on a wire gets a 32 px hit area under a finger, and its dot stays 16 px. The name box has the label "Workflow name".

**Non-goals.** No server, route, flag or palette content change. No change to the text sizes of `WorkflowNode`. The desktop toolbar keeps its markup. A long notice makes it 936 px wide for a draft and 993 px for a published workflow (measured). So in a box from 900 to 992 px, that notice still pushes Publish partly out, as before E7e.

## Done when

From 390 to 1440 px, with the sidebar open and folded, the canvas is 300 px wide or more. Test and Publish are inside the viewport. On a desktop, a tap on a block shows the new step in the inspector.

The new tests pass: `editorPhone.test.ts`, `editorSelection.test.ts` and `phoneChrome.test.tsx`. They fail when `layoutFor` returns the phone layout for a wide box. They fail when the selection handler is inline again. They fail when the phone class of the trigger panel reaches a desktop.

Visual review at 390, 768, 900, 1024 and 1440 px, in dark, light, compact density and a changed accent.
