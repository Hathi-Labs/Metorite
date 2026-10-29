// ── The company and contact pickers (WS-53 CRM-U16, crm_platform.md 20.20) ──
//
// A deal gets its company and its contacts where the member makes it, and on
// the deal page. The control is `components/RecordPicker.tsx`. This file holds
// what it decides, so a test runs it without a DOM:
//
// - the search for the hits, with the narrowing of contacts to one company;
// - the words of the labels, from the CRM's own names for the entities;
// - the link fields that a create form adds to its body.
//
// The search is the `?q=` list search of CRM-X1 on the list routes. Nothing
// here writes. The server guard (`assert_writable`) still refuses a write in
// Mirror mode, and the screens do not offer one.

import * as api from "./api";
import { lookupValue } from "./fieldLayout";
import { sourceSingular, ENTITY_NOUN, type SourceLabels } from "./views";
import type { EntitySlug } from "./types";

/** What a pick holds: the id to send and the name to show. */
export type Pick = { id: string; name: string };

/** The two entities a picker names. */
export type PickerTarget = "organizations" | "contacts";

/** The most hits one search asks for. */
export const PICKER_PAGE = 10;

/**
 * The hits for a search. With a `companyId`, the contacts of that company come
 * first, and then each other contact that matches. Any contact stays pickable.
 * With no `companyId`, an empty search finds nothing.
 */
export async function searchHits(
  target: PickerTarget,
  query: string,
  companyId: string | null = null,
): Promise<Pick[]> {
  const q = query.trim();
  const narrow = target === "contacts" && companyId ? companyId : null;
  if (!narrow && !q) return [];
  const asHits = (rows: Record<string, unknown>[]) => rows.map((row) => lookupValue(target, row));
  if (!narrow) {
    const page = await api.listRecords(target, { q, page_size: PICKER_PAGE });
    return asHits(page.rows);
  }
  const first = await api.listRecords("contacts", {
    ...(q ? { q } : {}),
    company_id: narrow,
    page_size: PICKER_PAGE,
  });
  const hits = asHits(first.rows);
  if (!q) return hits;
  const more = await api.listRecords("contacts", { q, page_size: PICKER_PAGE });
  const seen = new Set(hits.map((h) => h.id));
  return [...hits, ...asHits(more.rows).filter((h) => !seen.has(h.id))];
}

/** The singular name of an entity, as the CRM or its source calls it. */
export function pickerNoun(entity: EntitySlug, labels: SourceLabels | null | undefined): string {
  return sourceSingular(entity, labels) ?? ENTITY_NOUN[entity];
}

/**
 * The fields that a create form adds to its body for the picks. A deal sends
 * `company_id`, and `contact_id` for the store, which attaches that contact as
 * the primary one after the create. A contact sends `company_id`. Any other
 * entity sends none.
 */
export function linkFields(
  entity: EntitySlug,
  company: Pick | null,
  contact: Pick | null,
): Record<string, string> {
  const out: Record<string, string> = {};
  if ((entity === "deals" || entity === "contacts") && company) out.company_id = company.id;
  if (entity === "deals" && contact) out.contact_id = contact.id;
  return out;
}
