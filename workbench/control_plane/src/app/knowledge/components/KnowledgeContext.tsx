"use client";

/**
 * What the Knowledge layout shares with its pages (WS-61 KB-2a).
 *
 * The layout owns the app bar, the rail and the first spaces read. A page
 * reads the spaces from here rather than asking again, tells the layout
 * which page is open (so the rail and the bar's subtitle follow it), and
 * opens the one New page form.
 *
 * It also holds the ONE leave guard (fix round 1, P1). A page with a dirty
 * draft registers a guard. Every way out of that page in this app asks
 * first through `requestLeave`: a rail row, New page, the space link and
 * Cancel. One `ConfirmDialog` in the layout asks "Discard your changes?".
 *
 * ⚠️ The rail does NOT read this context. On a phone the rail draws inside
 * the shell's drawer, which renders above this provider, so the rail takes
 * props only (`KnowledgeRail.tsx`).
 */

import { createContext, useContext } from "react";

import type { Space } from "../lib/types";

/** The page on screen and its space, as the page view last read it. */
export interface PageFocus {
  pageId: string;
  spaceId: string;
}

/** What the New page form starts with. */
export interface NewPageAsk {
  /** The space on screen. The form picks it when the member can write there. */
  spaceId?: string | null;
  /** A sub-page: the parent fixes the space. */
  parent?: { id: string; title: string; spaceId: string } | null;
  /** The title the form starts with. The member may change it. */
  title?: string;
  /**
   * The gap this page answers (KB-9a). After the page is made, the layout
   * marks the gap answered with it.
   */
  gap?: { id: string; question: string } | null;
}

/** What a page with unsaved changes registers. */
export interface LeaveGuard {
  /** Drop the draft: the stored copy too. Runs before the held act. */
  onDiscard: () => void;
}

export interface KnowledgeContextValue {
  spaces: Space[];
  setPageFocus: (focus: PageFocus | null) => void;
  openNewPage: (ask?: NewPageAsk) => void;
  /** Register the guard of a dirty page, or `null` when it is clean. */
  setLeaveGuard: (guard: LeaveGuard | null) => void;
  /** True while a dirty page holds the guard. */
  leaveGuarded: () => boolean;
  /** Run `act` now, or ask first while a dirty page holds the guard. */
  requestLeave: (act: () => void) => void;
}

const NO_CONTEXT: KnowledgeContextValue = {
  spaces: [],
  setPageFocus: () => {},
  openNewPage: () => {},
  setLeaveGuard: () => {},
  leaveGuarded: () => false,
  requestLeave: (act) => act(),
};

export const KnowledgeContext = createContext<KnowledgeContextValue>(NO_CONTEXT);

export function useKnowledge(): KnowledgeContextValue {
  return useContext(KnowledgeContext);
}
