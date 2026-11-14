/**
 * The counts of the rail, as pure data (STU-AF3b, `ai_studio.md` §5.5 rule 6).
 *
 * A rail row with `count: true` shows how many records its page lists. The count is one `list` read
 * with `limit: 1`. The store answers `total`, or `total_at_least` from 10,001 records. Nothing here
 * reads, draws or holds state. `useRailCounts.ts` reads, and `StudioAppFrame.tsx` draws.
 */
import type { RowsMeta, StudioSource } from "@/lib/studio/dataDoor";
import type { PageModel } from "@/lib/studio/pageModel";
import { viewObjectName } from "@/lib/studio/pageModel";
import type { ViewDef } from "@/lib/studio/manifest";
import { groupedNumber } from "@/lib/numberFormat.mjs";
import { resolveMe } from "@/lib/studio/meToken";
import { ownListArgs } from "@/lib/studio/viewBlocks";

/** The store counts to this number, and then says "at least". */
export const COUNT_CAP = 10_000;

type PageCells = Pick<PageModel, "cells" | "tabs" | "side">;
type ObjectFields = { name: string; fields: readonly { key: string; type: string }[] };
type ActionRef = readonly { id: string; uses: string }[];

/** The ids of the views of a page, in the order of the screen: the cells, the tabs, the side. */
export function pageViewIds(page: PageCells): string[] {
  return [
    ...page.cells.map((c) => c.view),
    ...page.tabs.flatMap((t) => t.cells.map((c) => c.view)),
    ...(page.side ? [page.side] : []),
  ];
}

/** The first table or board of a page. The count and the hide rule both read this view. */
export function countView(page: PageCells, views: readonly ViewDef[]): ViewDef | null {
  for (const id of pageViewIds(page)) {
    const view = views.find((v) => v.id === id);
    if (view && (view.block === "table" || view.block === "board")) return view;
  }
  return null;
}

/** The object that a page lists, or null when the page has no table or board. */
export function pageObject(page: PageCells, views: readonly ViewDef[], actions: ActionRef = []): string | null {
  const view = countView(page, views);
  return view ? viewObjectName(view, actions) : null;
}

/**
 * The `list` source that counts a row, or null. It copies the filter of the first table or board of
 * the page, so the count equals the rows of the page. The sort is dropped and the limit is 1. The
 * `me` token stays in the source. `resolveMe` turns it into the email before the read.
 */
export function countSourceFor(
  page: PageCells,
  views: readonly ViewDef[],
  objects: readonly ObjectFields[],
  actions: ActionRef = [],
): StudioSource | null {
  const view = countView(page, views);
  const name = view ? viewObjectName(view, actions) : null;
  const object = name ? objects.find((o) => o.name === name) : undefined;
  if (!view || !name || !object) return null;
  // The email is not known here. A source that still holds the token is kept as it is.
  const own = ownListArgs(view, object.fields, null);
  return { action: `object:${name}.list`, args: { ...(own.filter ? { filter: own.filter } : {}), limit: 1 } };
}

/** The source of a count with the `me` token resolved, or null when the email is not known yet. */
export function sourceWithMe(
  source: StudioSource, email: string | null | undefined, object: ObjectFields | undefined,
): StudioSource | null {
  return resolveMe(source, email, (k) => object?.fields.find((f) => f.key === k)?.type === "person");
}

/**
 * The text of a count, or null when the rail shows nothing. A read that is loading, failed, or
 * refused gives no meta, so it gives null and the row shows no number. An error never reaches the rail.
 */
export function countLabel(meta: RowsMeta | null | undefined, system?: unknown): string | null {
  if (!meta) return null;
  if (typeof meta.atLeast === "number") return `${groupedNumber(COUNT_CAP, system)}+`;
  if (typeof meta.total === "number" && Number.isFinite(meta.total) && meta.total >= 0) {
    return groupedNumber(meta.total, system);
  }
  return null;
}

