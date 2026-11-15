/**
 * The floor view of a Studio app, as a React context (STU-D23a-2, `ai_studio.md` §14 D23a-2).
 *
 * `StudioAppFrame` gives `true` to its children when it draws the floor view. The root of the
 * frame also takes the `data-floor` attribute, and the `pointer-coarse` variant of `globals.css`
 * matches inside that root. So a control in the frame takes its touch size from CSS alone.
 *
 * Two parts need the context too:
 *
 * - **A part that draws outside the root.** `Modal` draws into a portal at the end of the body, so
 *   its fields are not inside the root. Inside the floor view it puts the attribute on its popup.
 * - **A part with no touch size of its own.** The back link of `AppTopBar` and the edit button of
 *   a table cell (`DataGrid`) add their `pointer-coarse:` size only inside the floor view. Their
 *   classes on every other page stay as they were, also on a phone.
 *
 * With no provider the value is `false`, so a part outside a Studio app draws as before.
 * Fences: `components/ui/modalFloor.test.ts`, `components/studio/StudioAppFrame.test.ts`,
 * `components/studio/StudioTable.test.ts` and `lib/theme/floorTouch.test.ts`.
 */
import { createContext, useContext } from "react";

/** True inside the floor view of a Studio app. Only `StudioAppFrame` provides it. */
export const FloorContext = createContext(false);

/** True when the caller draws inside the floor view. */
export function useFloor(): boolean {
  return useContext(FloorContext);
}
