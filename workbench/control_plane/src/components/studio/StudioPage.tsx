"use client";

/**
 * The page of a Studio app (STU-D11b): the cells of one page in a grid, under `AppTopBar`.
 *
 * It draws the page that `buildPage` (`lib/studio/pageModel.ts`) builds, and it reads no
 * manifest a second way. Each cell gives its view id to `renderView`, so the mapper from a
 * view to a block (A5, H1) plugs in later. This file adds no genUI node type and knows no
 * block by name. A view that `renderView` cannot draw shows a "not built yet" card.
 *
 * Layout. `cols` and `span` come from a closed class table, and no class is built from a
 * manifest value. Below `sm` the grid is one column in declared order. From `sm` up the
 * side view is a `w-[380px]` column beside the grid. Below `sm` it opens in a `Modal` sheet.
 *
 * The page keeps its whole height and the Ask bar floats over it (`lib/shell/askBarRest.ts`),
 * so the scroller reserves no space for the bar. The shell owns the bar, the palette and the
 * bell. This page mounts none of them.
 */
import { Fragment, useSyncExternalStore, type ReactNode } from "react";

import { AppTopBar, type AppTopBarBack } from "@/components/AppTopBar";
import Tabs from "@/components/Tabs";
import Button from "@/components/ui/Button";
import Modal from "@/components/ui/Modal";
import type { PageDef, ViewDef } from "@/lib/studio/manifest";
import { buildPage } from "@/lib/studio/pageModel";
import type { PageState } from "@/lib/studio/pageState";

/** `cols` to the classes of the grid. One column below `sm`. */
const GRID: Record<number, string> = {
  1: "grid-cols-1",
  2: "grid-cols-1 sm:grid-cols-2",
  3: "grid-cols-1 sm:grid-cols-3",
  4: "grid-cols-1 sm:grid-cols-4",
};

/** `span` to its class. Below `sm` every cell takes the one column. */
const SPAN: Record<number, string> = {
  1: "",
  2: "sm:col-span-2",
  3: "sm:col-span-3",
  4: "sm:col-span-4",
};

const PANEL = "min-w-0 rounded-xl border border-border bg-card p-3 sm:p-4";
const BELOW_SM = "(max-width: 639.98px)";

function subscribe(onChange: () => void): () => void {
  const q = window.matchMedia(BELOW_SM);
  q.addEventListener("change", onChange);
  return () => q.removeEventListener("change", onChange);
}

/** True below `sm`. The server and the first render say false. */
function useBelowSm(): boolean {
  return useSyncExternalStore(subscribe, () => window.matchMedia(BELOW_SM).matches, () => false);
}

export interface StudioPageProps {
  /** The app's name: the one `<h1>`, in `AppTopBar`. */
  title: string;
  subtitle?: ReactNode;
  back?: AppTopBarBack;
  /** The manifest parts that a page reads. */
  manifest: { views?: readonly ViewDef[] | null; pages?: readonly PageDef[] | null };
  /** The page to show. The first page when it is missing. */
  pageId?: string | null;
  /** The page state that the URL holds. */
  state: PageState;
  onTab: (tab: string) => void;
  /** Clear the selected record, and so close the side view. */
  onCloseSide: () => void;
  /** Draw one view, or return `null` when its block is not built. */
  renderView: (viewId: string) => ReactNode;
  /** Controls above the grid, such as a filter. */
  toolbar?: ReactNode;
}

function NotBuilt({ view }: { view: string }) {
  return (
    <div className={PANEL} data-not-built={view}>
      <p className="text-sm font-medium text-foreground">This block is not built yet</p>
      <p className="mt-1 text-xs text-muted-foreground">The view {view} has no block that can draw it.</p>
    </div>
  );
}

export default function StudioPage({
  title, subtitle, back, manifest, pageId, state, onTab, onCloseSide, renderView, toolbar,
}: StudioPageProps) {
  const built = buildPage(manifest, pageId, state.tab);
  const { page, cells } = built;
  const narrow = useBelowSm();
  const sideView = page.side && state.selected ? page.side : null;
  const side = sideView ? renderView(sideView) : null;

  return (
    <div className="flex h-full flex-col">
      <AppTopBar title={title} subtitle={subtitle ?? page.title ?? undefined} back={back} />
      {page.tabs.length > 0 && built.tab !== null && (
        <Tabs variant="underline" activeTab={built.tab} onTabChange={onTab}
          tabs={page.tabs.map((t) => ({ id: t.id, label: t.title }))} />
      )}
      <div className="flex min-h-0 flex-1">
        <div className="min-w-0 flex-1 overflow-y-auto p-3 sm:p-4" data-page-scroller>
          {toolbar && <div className="mb-3 flex flex-wrap items-center gap-2 sm:mb-4">{toolbar}</div>}
          {cells.length === 0 ? (
            <p className="text-sm text-muted-foreground">This page has no views.</p>
          ) : (
            <div className={`grid gap-3 sm:gap-4 ${GRID[page.cols] ?? GRID[1]}`} data-page-grid>
              {cells.map((c, i) => {
                const node = renderView(c.view);
                return (
                  <Fragment key={`${c.view}:${i}`}>
                    {node == null || node === false ? (
                      <div className={SPAN[c.span] ?? ""}><NotBuilt view={c.view} /></div>
                    ) : (
                      <section aria-label={c.view} className={`${PANEL} ${SPAN[c.span] ?? ""}`}>{node}</section>
                    )}
                  </Fragment>
                );
              })}
            </div>
          )}
        </div>
        {sideView && !narrow && (
          <aside aria-label="Details" className="hidden w-[380px] shrink-0 overflow-y-auto border-l border-border p-3 sm:block sm:p-4">
            <div className="mb-3 flex items-center justify-between gap-2">
              <h2 className="text-sm font-semibold text-foreground">Details</h2>
              <Button variant="ghost" size="icon-sm" icon="X" aria-label="Close details" onClick={onCloseSide} />
            </div>
            {side ?? <NotBuilt view={sideView} />}
          </aside>
        )}
      </div>
      {sideView && narrow && (
        <Modal open onClose={onCloseSide} title="Details" placement="sheet" size="lg">
          {side ?? <NotBuilt view={sideView} />}
        </Modal>
      )}
    </div>
  );
}
