// ── The shared list contract, client side ─────────────────────────────────
//
// One contract for all four entities (specs/crm_app.md §4 records.py row), so
// this is the one place a list request is built. Getting it wrong in four
// places is how a list endpoint grows four different caps.
//
// The rule that shapes this file: `?status_id` is a 422 on contacts and
// organizations — the gateway refuses it rather than ignoring it, precisely
// because a shared list component is what would send it. So the chips a list
// offers are derived from the entity, and the query is built from the chips.

import { RANGE_KEYS, type CrmView } from "./urlState";
import type { EntitySlug, Status } from "./types";

/** Entities with a status pipeline. Sending `status_id` for any other is a
 *  422 by design — mirrored here so the UI never asks. */
export const PIPELINED: readonly EntitySlug[] = ["leads", "deals"];

export function hasPipeline(entity: EntitySlug): boolean {
  return PIPELINED.includes(entity);
}

export const MAX_PAGE_SIZE = 100;

/** The rows of one list page, and of each "Load more" (CRM-X1). */
export const LIST_PAGE_SIZE = 50;

export type ListQuery = {
  q?: string;
  sort?: string;
  dir?: "asc" | "desc";
  page?: number;
  page_size?: number;
  status_id?: string;
  owner?: string;
  source?: string;
  include_converted?: boolean;
  /** Deals only (CRM-Q2). Sent only when the org has two pipelines or more. */
  pipeline_id?: string;
  /** Related lists (CRM-Q3): the contacts and deals of one company. */
  company_id?: string;
  /** Related lists (CRM-Q3): the deals one contact is attached to. */
  contact_id?: string;
  /** CRM-Q8: the date ranges. ISO dates. */
  created_from?: string;
  created_to?: string;
  updated_from?: string;
  updated_to?: string;
  /** Deals only. */
  close_from?: string;
  close_to?: string;
  /** CRM-Q8: `custom.<key>.in` = the chosen options, joined by commas. */
  [custom: `custom.${string}`]: string | undefined;
};

/** The filters that are the same on a list and on a board (CRM-Q8). */
export function extraQuery(entity: EntitySlug, view: CrmView): ListQuery {
  const query: ListQuery = {};
  if (view.source) query.source = view.source;
  for (const key of RANGE_KEYS) {
    const value = view.ranges[key];
    if (!value) continue;
    // A close date belongs to a deal. The gateway refuses it on the rest.
    if (key.startsWith("close_") && entity !== "deals") continue;
    query[key] = value;
  }
  for (const [key, items] of Object.entries(view.custom)) {
    if (items.length > 0) query[`custom.${key}.in`] = items.join(",");
  }
  return query;
}

/**
 * The view's filters as the list endpoint's query parameters.
 *
 * Drops `status_id` for an entity without a pipeline rather than sending it
 * and handling the 422: the refusal is the server's job, and asking anyway
 * would put a red toast on a list the user filtered by accident.
 *
 * `sort`/`dir` are sent whenever the view states them. ⚠️ They must actually
 * reach the wire: a column header that flips its own arrow and issues an
 * unchanged request is worse than no sorting at all, because it looks like it
 * worked. (`selectTab` clears `sort` when the collection changes — the keys
 * are a per-entity server allowlist and a stale one is a 422.)
 */
export function listQuery(
  entity: EntitySlug,
  view: CrmView,
  page = 1,
  pageSize = LIST_PAGE_SIZE
): ListQuery {
  const query: ListQuery = {
    page,
    page_size: Math.min(pageSize, MAX_PAGE_SIZE),
  };
  if (view.q.trim()) query.q = view.q.trim();
  if (view.statusId && hasPipeline(entity)) query.status_id = view.statusId;
  if (view.owner) query.owner = view.owner;
  Object.assign(query, extraQuery(entity, view));
  if (entity === "deals" && view.pipelineId) query.pipeline_id = view.pipelineId;
  if (entity === "leads" && view.includeConverted) {
    query.include_converted = true;
  }
  if (view.sort && canSortBy(entity, view.sort)) {
    query.sort = view.sort;
    query.dir = view.dir;
  }
  return query;
}

/**
 * The rows on screen, then the rows of the next page that are not on screen
 * (CRM-X1 "Load more").
 *
 * A record can move up between two reads, for example when somebody touches
 * it, and then it shows on both pages. The row already on screen stays, and
 * the copy on the next page is dropped. Pure, so the vitest setup tests it.
 */
export function appendPage<T extends { id?: unknown }>(shown: readonly T[], next: readonly T[]): T[] {
  const seen = new Set(shown.map((row) => String(row.id)));
  const added = next.filter((row) => {
    const id = String(row.id);
    if (seen.has(id)) return false;
    seen.add(id);
    return true;
  });
  return [...shown, ...added];
}

/**
 * The same view, as the CSV export's query (WS-26i-export).
 *
 * ⚠️ Built by calling `listQuery` and DELETING the page, rather than by
 * assembling a second parameter set. Two builders would drift and then the
 * file and the screen would disagree about what the caller was looking at —
 * the bug class this module's one-builder rule exists for, and the reason the
 * gateway reuses `core.list_contract` on its side too.
 *
 * The page goes because the export has none: half a filter's records in a file
 * is the silent truncation the endpoint refuses (it answers 422 naming the
 * real count instead), so sending `page_size` could only ever mean something
 * the server does not do.
 */
export function exportQuery(entity: EntitySlug, view: CrmView): ListQuery {
  const query = { ...listQuery(entity, view) };
  delete query.page;
  delete query.page_size;
  return query;
}

/**
 * Is this a sort key the gateway will accept for this entity?
 *
 * Belt-and-braces behind `selectTab`'s clear: an unknown key is a 422 that
 * empties the list, and the one way a stale key survives is a hand-edited or
 * shared URL — `?tab=leads&sort=amount` is one paste away.
 */
export function canSortBy(entity: EntitySlug, key: string): boolean {
  return SORTS[entity].some((option) => option.key === key);
}

/** A query object → `?a=1&b=2`, dropping empties so the URL stays readable. */
export function queryString(query: ListQuery): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null || value === "") continue;
    params.set(key, typeof value === "boolean" ? String(value) : String(value));
  }
  const qs = params.toString();
  return qs ? `?${qs}` : "";
}

export type Chip = { id: string; label: string; count?: number };

/**
 * The filter chips a list offers: "All", one per status lane, and — on leads
 * only — "Converted".
 *
 * Empty for contacts and organizations, which is not an oversight: they have
 * no pipeline, and a chip row that filters by nothing is furniture.
 */
export function chipsFor(
  entity: EntitySlug,
  statuses: Status[],
  view: CrmView
): Chip[] {
  if (!hasPipeline(entity)) return [];
  const chips: Chip[] = [{ id: "all", label: "All" }];
  for (const status of [...statuses].sort((a, b) => a.position - b.position)) {
    chips.push({ id: status.id, label: status.name });
  }
  if (entity === "leads") {
    // Converted leads are hidden by default (§3.3 / B6 — keyed on the FK, so
    // deleting the deal returns the lead to the working list). The chip is
    // how you see them without a second endpoint.
    chips.push({
      id: "converted",
      label: view.includeConverted ? "Converted ✓" : "Converted",
    });
  }
  return chips;
}

/** Which chip reads as active for the current view. */
export function activeChip(view: CrmView): string {
  return view.statusId ?? "all";
}

/** Clicking a chip → the next view. "converted" toggles; the rest select. */
export function applyChip(view: CrmView, chipId: string): CrmView {
  if (chipId === "converted") {
    return { ...view, includeConverted: !view.includeConverted };
  }
  if (chipId === "all") return { ...view, statusId: null };
  return { ...view, statusId: chipId };
}

/** Sort keys the gateway allowlists per entity (an unknown one is a 422, not
 *  a silent fall back to the default), mirrored so the column headers can
 *  only offer real ones. */
export const SORTS: Record<EntitySlug, { key: string; label: string }[]> = {
  deals: [
    { key: "name", label: "Deal" },
    { key: "amount", label: "Amount" },
    { key: "expected_close_date", label: "Closing" },
    { key: "status_changed_at", label: "Stage age" },
    { key: "last_activity_at", label: "Last touched" },
  ],
  leads: [
    { key: "lead_name", label: "Lead" },
    { key: "email", label: "Email" },
    { key: "owner_email", label: "Owner" },
    { key: "created_at", label: "Created" },
    { key: "last_activity_at", label: "Last touched" },
  ],
  contacts: [
    { key: "first_name", label: "Name" },
    { key: "email", label: "Email" },
    { key: "owner_email", label: "Owner" },
    { key: "last_activity_at", label: "Last touched" },
  ],
  organizations: [
    { key: "name", label: "Company" },
    { key: "owner_email", label: "Owner" },
    { key: "last_activity_at", label: "Last touched" },
  ],
};
