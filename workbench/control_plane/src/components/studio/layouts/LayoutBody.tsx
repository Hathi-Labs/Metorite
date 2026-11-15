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
 * Built: `table_workspace` (LY-2) and `board` (LY-4). Both draw the one view of their `main` region. The
 * other presets name a block or a bar that a later slice builds (LY-5 to LY-7), and each one says so in a
 * plain sentence until then.
 */

import type { ReactNode } from "react";

import ScrollCue from "@/components/studio/layouts/ScrollCue";
import type { LayoutName } from "@/lib/studio/manifest";

export interface LayoutBodyProps {
  layout: LayoutName;
  regions: Record<string, string[]>;
  renderView: (id: string) => ReactNode;
  /** The title of a view, for the label of its region. */
  titleOf: (id: string) => string;
}

/** The presets that this file draws. A later slice adds one. */
export const DRAWN_LAYOUTS: readonly LayoutName[] = ["table_workspace", "board"];

export default function LayoutBody({ layout, regions, renderView, titleOf }: LayoutBodyProps) {
  if (!DRAWN_LAYOUTS.includes(layout)) {
    return <p data-layout-pending={layout} className="text-sm text-muted-foreground">This layout is not drawn yet.</p>;
  }
  const main = regions.main?.[0];
  const node = main ? renderView(main) : null;
  return (
    <div data-layout={layout} className="flex min-w-0 flex-col gap-3">
      {main && node ? (
        <section aria-label={titleOf(main)} className="min-w-0">
          {layout === "table_workspace" ? (
            // With the record panel open the page is narrow. The table keeps a readable width and scrolls
            // inside its own strip, so no column shrinks to one character (capture finding, task 3315).
            <div data-table-strip className="overflow-x-auto"><div className="min-w-[40rem]">{node}</div></div>
          ) : layout === "board" ? <ScrollCue>{node}</ScrollCue> : node}
        </section>
      ) : (
        <p className="text-sm text-muted-foreground">{`This page has no ${layout === "board" ? "board" : "table"} to show.`}</p>
      )}
    </div>
  );
}
