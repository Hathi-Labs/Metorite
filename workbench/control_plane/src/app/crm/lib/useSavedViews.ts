"use client";

// ── The React face of the CRM saved views (CRM-Q8, CRM-U8) ──────────────────
//
// One cached read for each scope: the four lists, and each module list
// (`module:<api_name>`). The reads go through the shared seam
// (`src/lib/savedViews`). The rules live in `savedViews.ts`.

import { invalidateViews as invalidateScoped, useScopedViews } from "@/lib/savedViews/useScopedViews";
import * as api from "./api";
import type { SavedView, ViewEntity } from "./savedViews";
import type { EntitySlug } from "./types";

const KEY = "crm:views:";

/** Re-read every saved view after a write. */
export function invalidateViews(): void {
  invalidateScoped(KEY);
}

/** The saved views of one scope. A `null` scope gives none. */
export function useCrmViews(scope: ViewEntity | null): SavedView[] {
  return useScopedViews<SavedView>(KEY, scope, (s) => api.listViews(s as ViewEntity));
}

/** The saved views of all four entities, for a link that names only `?view=`. */
export function useAllSavedViews(): Record<EntitySlug, SavedView[]> {
  const leads = useCrmViews("leads");
  const contacts = useCrmViews("contacts");
  const organizations = useCrmViews("organizations");
  const deals = useCrmViews("deals");
  return { leads, contacts, organizations, deals };
}
