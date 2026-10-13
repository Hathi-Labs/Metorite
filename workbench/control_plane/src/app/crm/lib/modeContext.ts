"use client";

// ── The CRM mode, for the surfaces that draw a stage colour (WS-53 CRM-C2) ──
//
// `stageAccent(status, mode)` skips the stored `gray` in Mirror mode, so a won
// stage reads green. The board gets the mode as a prop. The list, the reports,
// the status pill and the settings dot sit deeper in the tree, so the page
// provides the mode here once and each of them reads it. Without it they passed
// `null` and drew a mirrored won stage gray beside a green board lane.
//
// The value comes from `useCrmMode` through the page. Nothing here reads or
// fetches anything. A surface outside the provider reads `null`, which means
// "mode not known" and keeps the stored colour, as before.

import { createContext, useContext } from "react";

import type { CrmMode } from "./connect";

export const CrmModeContext = createContext<CrmMode>(null);

/** The mode the page provides, or `null` outside the CRM page. */
export function useCrmModeValue(): CrmMode {
  return useContext(CrmModeContext);
}
