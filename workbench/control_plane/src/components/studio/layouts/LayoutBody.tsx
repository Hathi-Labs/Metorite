"use client";

/**
 * The body of a page with a preset (STU-LY-2, `project-docs/specs/studio_layouts.md` §4 and §6).
 *
 * `StudioPage` draws this in place of the cell grid when the page has a `layout`. The preset
 * decides the region, the size and the phone order, and the manifest names the views of each
 * region. This file draws no colour, no pixel size and no state: the loading, empty, error and
 * no-permission states come from `StudioBlockState` through the caller of `renderView`, and the
 * fence of `StudioBlockState.test.ts` refuses a local copy in this folder.
 *
 * Built: `table_workspace` (LY-2) and `board` (LY-4), which draw the one view of their `main` region, and
 * `dashboard` (LY-7): the `kpis` in a grid (2 columns on a phone, 4 from a tablet), the `charts` in a grid
 * (one column on a phone, 2 on a wide screen) and the `worklist` table. The other presets name a block or a
 * bar that a later slice builds, and each one says so in a plain sentence until then. `work_queue` and
 * `approval_queue` (LY-6) draw the `list` and the `detail` side by side from a wide screen (35 and 65 percent),
 * and one under the other below it. The decision bar of an approval queue is not built (it waits on an owner
 * decision, task 3319).
 */

import type { ReactNode } from "react";

import type { LayoutName } from "@/lib/studio/manifest";

export interface LayoutBodyProps {
  layout: LayoutName;
  regions: Record<string, string[]>;
  renderView: (id: string) => ReactNode;
  /** The title of a view, for the label of its region. */
  titleOf: (id: string) => string;
}

/** The presets that this file draws. A later slice adds one. */
export const DRAWN_LAYOUTS: readonly LayoutName[] = ["table_workspace", "board", "dashboard", "work_queue", "approval_queue"];

export default function LayoutBody({ layout, regions, renderView, titleOf }: LayoutBodyProps) {
  if (!DRAWN_LAYOUTS.includes(layout)) {
    return <p data-layout-pending={layout} className="text-sm text-muted-foreground">This layout is not drawn yet.</p>;
  }
  if (layout === "dashboard") return <Dashboard regions={regions} renderView={renderView} titleOf={titleOf} />;
  if (layout === "work_queue" || layout === "approval_queue") {
    return <Queue layout={layout} regions={regions} renderView={renderView} titleOf={titleOf} />;
  }
  const main = regions.main?.[0];
  const node = main ? renderView(main) : null;
  return (
    <div data-layout={layout} className="flex min-w-0 flex-col gap-3">
      {main && node ? (
        <section aria-label={titleOf(main)} className="min-w-0">{node}</section>
      ) : (
        <p className="text-sm text-muted-foreground">{`This page has no ${layout === "board" ? "board" : "table"} to show.`}</p>
      )}
    </div>
  );
}

/**
 * The dashboard (L4). The most important tile is first in `kpis`, so it sits top-left. Each tile is its own
 * labelled region, and the caller of `renderView` gives each one its own loading and error state, so one
 * failed tile never blanks the page.
 */
function Dashboard({ regions, renderView, titleOf }: Pick<LayoutBodyProps, "regions" | "renderView" | "titleOf">) {
  const tile = (id: string) => {
    const node = renderView(id);
    return node ? <section key={id} aria-label={titleOf(id)} className="min-w-0">{node}</section> : null;
  };
  const kpis = (regions.kpis ?? []).map(tile);
  const charts = (regions.charts ?? []).map(tile);
  const work = (regions.worklist ?? []).map(tile);
  if (kpis.every((x) => x === null)) {
    return <p className="text-sm text-muted-foreground">This page has no stat to show.</p>;
  }
  return (
    <div data-layout="dashboard" className="flex min-w-0 flex-col gap-3 sm:gap-4">
      <div data-dashboard-kpis className="grid grid-cols-2 gap-3 sm:grid-cols-4 sm:gap-4">{kpis}</div>
      {charts.length > 0 ? <div data-dashboard-charts className="grid grid-cols-1 gap-3 sm:gap-4 lg:grid-cols-2">{charts}</div> : null}
      {work.length > 0 ? <div data-dashboard-worklist>{work}</div> : null}
    </div>
  );
}

/**
 * The queue (L7, L8): the list of the items beside the open item. The list comes first in reading order, so
 * the Tab order and a phone both go list, then item.
 */
function Queue({ layout, regions, renderView, titleOf }: LayoutBodyProps) {
  const pane = (id: string | undefined) => {
    const node = id ? renderView(id) : null;
    return id && node ? <section aria-label={titleOf(id)} className="min-w-0">{node}</section> : null;
  };
  const list = pane(regions.list?.[0]);
  const detail = pane(regions.detail?.[0]);
  if (!list) return <p className="text-sm text-muted-foreground">This page has no list to show.</p>;
  return (
    <div data-layout={layout} className="grid min-w-0 grid-cols-1 gap-3 sm:gap-4 lg:grid-cols-[minmax(0,35fr)_minmax(0,65fr)]">
      {list}
      {detail}
    </div>
  );
}
