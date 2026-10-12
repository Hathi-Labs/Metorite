"use client";

// ── The React face of the saved views (CRM-Q8) ─────────────────────────────
//
// One cached read for each entity, so the rail can nest the views under all
// four rows. The rules live in `savedViews.ts`.

import { invalidate } from "@/lib/dataCache";
import { useCachedResource } from "@/lib/useCachedResource";
import * as api from "./api";
import type { SavedView } from "./savedViews";
import type { EntitySlug } from "./types";

const KEY = "crm:views:";

/** Re-read every saved view after a write. */
export function invalidateViews(): void {
  invalidate(KEY);
}

function useEntityViews(entity: EntitySlug): SavedView[] {
  // A gateway without the route answers 404. Then there are no views.
  const res = useCachedResource<SavedView[]>(`${KEY}${entity}`, () =>
    api.listViews(entity).catch(() => []),
  );
  return res.data ?? [];
}

/** The saved views of all four entities, for the rail. */
export function useAllSavedViews(): Record<EntitySlug, SavedView[]> {
  const leads = useEntityViews("leads");
  const contacts = useEntityViews("contacts");
  const organizations = useEntityViews("organizations");
  const deals = useEntityViews("deals");
  return { leads, contacts, organizations, deals };
}
