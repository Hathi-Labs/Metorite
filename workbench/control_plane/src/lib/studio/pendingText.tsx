"use client";

/**
 * The text that a member has typed and not yet committed (STU-G3c fix 2). A `CommitInput` keeps its
 * raw text until blur or Enter, so the page does not see it. Each control that holds such text tells
 * the page through this context. The page then shows "Unsaved changes" and keeps the leave guard on
 * while the member types.
 */
import { createContext, useContext } from "react";

/** Add one (+1) or remove one (-1) control with pending text. */
export const PendingTextContext = createContext<((delta: 1 | -1) => void) | null>(null);

export const usePendingText = () => useContext(PendingTextContext);
