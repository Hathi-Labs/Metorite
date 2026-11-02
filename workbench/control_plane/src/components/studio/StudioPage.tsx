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
 * manifest value. A narrow scroller (a phone at 390 px) gets one column in declared order.
 * From `lg` up the side view is a `w-[380px]` column beside the grid. Below `lg` it opens in a
 * `Modal` sheet. DESIGN_SYSTEM section 6 says `sm`, but the shell rail takes 260 px, so at 768 px
 * a 380 px column left the grid 110 px (seen in the D11b capture).
 *
 * The page keeps its whole height and the Ask bar floats over it (`lib/shell/askBarRest.ts`),
 * so the scroller reserves no space for the bar. The shell owns the bar, the palette and the
 * bell. This page mounts none of them.
 */
import { Fragment, useEffect, useRef, useSyncExternalStore, type ReactNode } from "react";

import { AppTopBar, type AppTopBarBack } from "@/components/AppTopBar";
import Tabs from "@/components/Tabs";
import Button from "@/components/ui/Button";
import Modal from "@/components/ui/Modal";
import type { PageDef, ViewDef } from "@/lib/studio/manifest";
import { buildPage } from "@/lib/studio/pageModel";
import type { PageState } from "@/lib/studio/pageState";

/**
 * `cols` to the classes of the grid. The columns follow the width of the scroller, not the
 * window (container queries), because the shell rail and the side column take width that
 * the window does not show. A narrow scroller, such as a phone at 390 px, gets one column.
 */
export const GRID: Record<number, string> = {
  1: "grid-cols-1",
  2: "grid-cols-1 @xl:grid-cols-2",
  3: "grid-cols-1 @xl:grid-cols-2 @3xl:grid-cols-3",
  4: "grid-cols-1 @xl:grid-cols-2 @3xl:grid-cols-3 @5xl:grid-cols-4",
};

/**
 * `span` to its class. A cell spans as many columns as the grid has at each step, up to its
 * span, so it fills its row at every width. `StudioPage.test.ts` checks each step.
 */
export const SPAN: Record<number, string> = {
  1: "",
  2: "@xl:col-span-2",
  3: "@xl:col-span-2 @3xl:col-span-3",
  4: "@xl:col-span-2 @3xl:col-span-3 @5xl:col-span-4",
};

/**
 * The frame of a block that draws none: the "not built" card, and the Detail and the Form in
 * the preview. A block that draws its own card (the table, the board) takes no second frame.
 */
export const PANEL = "min-w-0 rounded-xl border border-border bg-card p-3 sm:p-4";
const BELOW_LG = "(max-width: 1023.98px)";

function subscribe(onChange: () => void): () => void {
  const q = window.matchMedia(BELOW_LG);
  q.addEventListener("change", onChange);
  return () => q.removeEventListener("change", onChange);
}

/** True below `lg`. The server and the first render say false. */
function useBelowLg(): boolean {
  return useSyncExternalStore(subscribe, () => window.matchMedia(BELOW_LG).matches, () => false);
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

/** The member sees the title of a view, and the id only when the view has no title. */
function titleOf(views: readonly ViewDef[] | null | undefined, id: string): string {
  const t = views?.find((v) => v.id === id)?.title;
  return typeof t === "string" && t.trim() ? t : id;
}

/** The focus target after the side view closes: the control that opened it, or the scroller. */
export interface Focusable { isConnected: boolean; focus: () => void }
export function restoreFocus(opener: Focusable | null, fallback: Focusable | null): void {
  const target = opener && opener.isConnected ? opener : fallback;
  target?.focus();
}

function NotBuilt({ view, label }: { view: string; label: string }) {
  return (
    <div className={PANEL} data-not-built={view}>
      <p className="text-sm font-medium text-foreground">This block is not built yet</p>
      <p className="mt-1 text-xs text-muted-foreground">It cannot draw “{label}”.</p>
    </div>
  );
}

export default function StudioPage({
  title, subtitle, back, manifest, pageId, state, onTab, onCloseSide, renderView, toolbar,
}: StudioPageProps) {
  const built = buildPage(manifest, pageId, state.tab);
  const { page, cells } = built;
  const narrow = useBelowLg();
  const sideView = page.side && state.selected ? page.side : null;
  const side = sideView ? renderView(sideView) : null;
  const scroller = useRef<HTMLDivElement>(null);
  const sideOpen = sideView !== null;

  // "Close details" unmounts the column and drops focus to <body>. Give it back to the control
  // that opened the column, or to the scroller when that control is gone.
  useEffect(() => {
    if (!sideOpen) return;
    const active = document.activeElement;
    const opener = active instanceof HTMLElement && active !== document.body ? active : null;
    return () => restoreFocus(opener, scroller.current);
  }, [sideOpen]);

  return (
    <div className="flex h-full flex-col">
      <AppTopBar title={title} subtitle={subtitle ?? page.title ?? undefined} back={back} />
      {page.tabs.length > 0 && built.tab !== null && (
        <Tabs variant="underline" activeTab={built.tab} onTabChange={onTab}
          tabs={page.tabs.map((t) => ({ id: t.id, label: t.title }))} />
      )}
      <div className="flex min-h-0 flex-1">
        <div ref={scroller} tabIndex={-1} data-page-scroller
          className="@container min-w-0 flex-1 overflow-y-auto px-4 py-3 outline-none sm:px-6 sm:py-4">
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
                      <div className={`min-w-0 ${SPAN[c.span] ?? ""}`}><NotBuilt view={c.view} label={titleOf(manifest.views, c.view)} /></div>
                    ) : (
                      <section aria-label={titleOf(manifest.views, c.view)} className={`min-w-0 ${SPAN[c.span] ?? ""}`}>{node}</section>
                    )}
                  </Fragment>
                );
              })}
            </div>
          )}
        </div>
        {sideView && !narrow && (
          <aside aria-label="Details" className="hidden w-[380px] shrink-0 overflow-y-auto border-l border-border p-3 lg:block lg:p-4">
            <div className="mb-3 flex items-center justify-between gap-2">
              <h2 className="text-sm font-semibold text-foreground">Details</h2>
              <Button variant="ghost" size="icon-sm" icon="X" aria-label="Close details" onClick={onCloseSide} />
            </div>
            {side ?? <NotBuilt view={sideView} label={titleOf(manifest.views, sideView)} />}
          </aside>
        )}
      </div>
      {sideView && narrow && (
        <Modal open onClose={onCloseSide} title="Details" placement="sheet" size="lg">
          <div className="overflow-y-auto p-3">{side ?? <NotBuilt view={sideView} label={titleOf(manifest.views, sideView)} />}</div>
        </Modal>
      )}
    </div>
  );
}
