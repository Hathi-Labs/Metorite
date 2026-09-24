/**
 * My Tasks · where a ⌘K search hit opens.
 *
 * ⌘K is search in both task apps, and both mount ONE palette
 * (`app/projects/components/SearchPalette.tsx`). The palette searches every
 * project the member can see, so a hit can be a task My Tasks does not hold.
 *
 * - **A task My Tasks holds opens HERE**, in the detail view, because the
 *   member asked from My Tasks and the store already has the row.
 * - **Any other task opens in Projects**, at the deep link the board already
 *   reads (`taskDeepLink`). My Tasks cannot draw a task it does not hold.
 *
 * Fence: `searchHit.test.ts`.
 */

import { taskDeepLink } from "@/app/projects/lib/card";

import type { MyTask } from "./types";

export type HitTarget =
  | { kind: "here"; id: string }
  | { kind: "projects"; href: string };

/** The store fields that mean a My Tasks overlay is up. */
export interface OverlayState {
  quickCaptureOpen: boolean;
  clarifyModalOpen: boolean;
  focusedItemId: string | null;
  settingsModalOpen: boolean;
  reclarifyItemId: string | null;
  pendingDeleteIds: string[] | null;
  scheduleItemId: string | null;
  eliminateItemId: string | null;
  delegateItemId: string | null;
}

/**
 * Whether ⌘K may open search now. **Not while another overlay is up.**
 *
 * Most My Tasks overlays are hand-rolled (`z-[80]` up to `z-[95]`) and the
 * palette is a `Modal` at `z-50`. Opened over one of them, the palette sat
 * hidden BEHIND it and took every keystroke. Even painted on top, a search
 * that navigates away from a half-finished clarify or a delete prompt is
 * the wrong answer. So ⌘K does nothing until the overlay closes.
 * `maximised` is the page's own controlled focus view, which is not in the
 * store. Fence: `searchHit.test.ts`.
 */
export function searchAllowed(s: OverlayState, maximised: boolean): boolean {
  return !(
    maximised ||
    s.quickCaptureOpen ||
    s.clarifyModalOpen ||
    s.settingsModalOpen ||
    s.focusedItemId ||
    s.reclarifyItemId ||
    s.pendingDeleteIds?.length ||
    s.scheduleItemId ||
    s.eliminateItemId ||
    s.delegateItemId
  );
}

export function hitTarget(
  id: string,
  items: readonly Pick<MyTask, "id">[],
): HitTarget {
  if (items.some((item) => item.id === id)) return { kind: "here", id };
  return { kind: "projects", href: taskDeepLink({ id }) };
}
