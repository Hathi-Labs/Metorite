"use client";

/**
 * What the Knowledge layout shares with its pages (WS-61 KB-2a).
 *
 * The layout owns the app bar, the rail and the first spaces read. A page
 * reads the spaces from here rather than asking again, tells the layout
 * which page is open (so the rail and the bar's subtitle follow it), and
 * opens the one New page form.
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
}

export interface KnowledgeContextValue {
  spaces: Space[];
  setPageFocus: (focus: PageFocus | null) => void;
  openNewPage: (ask?: NewPageAsk) => void;
}

const NO_CONTEXT: KnowledgeContextValue = {
  spaces: [],
  setPageFocus: () => {},
  openNewPage: () => {},
};

export const KnowledgeContext = createContext<KnowledgeContextValue>(NO_CONTEXT);

export function useKnowledge(): KnowledgeContextValue {
  return useContext(KnowledgeContext);
}
