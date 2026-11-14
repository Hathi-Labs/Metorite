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
import PageHeader from "@/components/PageHeader";
import StudioSection from "@/components/studio/StudioSection";
import Tabs from "@/components/Tabs";
import Button from "@/components/ui/Button";
import Modal from "@/components/ui/Modal";
import type { ViewDef } from "@/lib/studio/manifest";
import { buildPage, type CellModel, type HeaderModel, type PageManifest } from "@/lib/studio/pageModel";
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
  manifest: PageManifest;
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
  /** The list bar (STU-AF4) in its own row under the toolbar, over the table or board. */
  filterBar?: ReactNode;
  /**
   * The record that is open (STU-AF5). In the panel it takes the place of the side view, in the same
   * column (and in the same sheet below `lg`). As the full page it takes the place of the grid, under
   * the one app bar. The body is the record page (`StudioRecord`), and this file knows no field.
   */
  record?: RecordShell | null;
  /**
   * The app id. It keys the folded state of the sections of the viewer (STU-AF7). With none, a
   * section folds for this visit only.
   */
  foldScope?: string | null;
  /** The count of a page header (STU-AF7), such as "12 visits". `null` draws no count. */
  renderCount?: (count: NonNullable<HeaderModel["count"]>) => ReactNode;
}

export interface RecordShell {
  title: string;
  body: ReactNode;
  full: boolean;
  onClose: () => void;
  /** Move the panel to the full page, or the full page back to the panel. */
  onFull: (full: boolean) => void;
  /** The label of the way back from the full page, such as the page title. */
  backLabel: string;
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
  title, subtitle, back, manifest, pageId, state, onTab, onCloseSide, renderView, toolbar, filterBar, record, foldScope, renderCount,
}: StudioPageProps) {
  const built = buildPage(manifest, pageId, state.tab);
  const { page, cells } = built;
  const narrow = useBelowLg();
  const sideView = page.side && state.selected && !record ? page.side : null;
  const side = sideView ? renderView(sideView) : null;
  const scroller = useRef<HTMLDivElement>(null);
  const sideOpen = sideView !== null || (record !== null && record !== undefined && !record.full);

  // "Close details" unmounts the column and drops focus to <body>. Give it back to the control
  // that opened the column, or to the scroller when that control is gone.
  useEffect(() => {
    if (!sideOpen) return;
    const active = document.activeElement;
    const opener = active instanceof HTMLElement && active !== document.body ? active : null;
    return () => restoreFocus(opener, scroller.current);
  }, [sideOpen]);

  if (record?.full) {
    // The full page of a record: one column at every width, under the one app bar.
    return (
      <div className="flex h-full flex-col" data-record-page="full">
        <AppTopBar title={title} subtitle={subtitle ?? page.title ?? undefined} back={back} />
        <div ref={scroller} tabIndex={-1} data-page-scroller
          className="min-w-0 flex-1 overflow-y-auto px-4 py-3 outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring sm:px-6 sm:py-4">
          <div className="mx-auto flex max-w-3xl flex-col gap-3">
            <div>
              <Button variant="ghost" size="sm" icon="ArrowLeft" onClick={record.onClose}>{`Back to ${record.backLabel}`}</Button>
            </div>
            <div className="flex items-center justify-between gap-2">
              <h2 className="min-w-0 truncate text-base font-semibold text-foreground">{record.title}</h2>
              <Button variant="ghost" size="icon-sm" icon="Minimize2" aria-label="Show in the side panel" onClick={() => record.onFull(false)} />
            </div>
            <div className={PANEL}>{record.body}</div>
          </div>
        </div>
      </div>
    );
  }

  const panel = record && !record.full ? (
    <>
      <div className="mb-3 flex items-center justify-between gap-2">
        <h2 className="min-w-0 truncate text-sm font-semibold text-foreground">{record.title}</h2>
        <div className="flex shrink-0 items-center gap-1">
          <Button variant="ghost" size="icon-sm" icon="Maximize2" aria-label="Open full page" onClick={() => record.onFull(true)} />
          <Button variant="ghost" size="icon-sm" icon="X" aria-label="Close record" onClick={record.onClose} />
        </div>
      </div>
      {record.body}
    </>
  ) : null;

  // One grid of cells. A page, a tab and a section each draw their cells through it.
  const grid = (list: CellModel[]) => (
    <div className={`grid gap-3 sm:gap-4 ${GRID[page.cols] ?? GRID[1]}`} data-page-grid>
      {list.map((c, i) => {
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
  );

  return (
    <div className="flex h-full flex-col">
      <AppTopBar title={title} subtitle={subtitle ?? page.title ?? undefined} back={back} />
      {page.tabs.length > 0 && built.tab !== null && (
        <Tabs variant="underline" activeTab={built.tab} onTabChange={onTab}
          tabs={page.tabs.map((t) => ({ id: t.id, label: t.title }))} />
      )}
      <div className="flex min-h-0 flex-1">
        <div ref={scroller} tabIndex={-1} data-page-scroller
          className="@container min-w-0 flex-1 overflow-y-auto px-4 py-3 outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring sm:px-6 sm:py-4">
          {page.header && (
            // The one sentence and the count of the page (STU-AF7), as text. No header, no `PageHeader`.
            <PageHeader title={page.title || page.id} subtitle={page.header.text}
              meta={page.header.count && renderCount ? renderCount(page.header.count) : undefined} />
          )}
          {toolbar && <div className="mb-3 flex flex-wrap items-center gap-2 sm:mb-4">{toolbar}</div>}
          {filterBar && <div className="mb-3 sm:mb-4">{filterBar}</div>}
          {page.sections.length > 0 && built.tab === null ? (
            <div className="flex flex-col gap-3 sm:gap-4" data-page-sections>
              {page.sections.map((x) => (
                <StudioSection key={x.id} scope={foldScope} pageId={page.id} id={x.id} title={x.title}>
                  {grid(x.cells)}
                </StudioSection>
              ))}
            </div>
          ) : cells.length === 0 ? (
            <p className="text-sm text-muted-foreground">This page has no views.</p>
          ) : grid(cells)}
        </div>
        {panel && !narrow && (
          <aside aria-label="Record" data-record-page="panel"
            className="hidden w-[420px] shrink-0 overflow-y-auto border-l border-border p-3 lg:block lg:p-4">
            {panel}
          </aside>
        )}
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
      {record && !record.full && narrow && (
        // The sheet has its own title and its own close, so only the full-page button is added.
        <Modal open onClose={record.onClose} title={record.title} placement="sheet" size="lg">
          <div data-record-page="panel" className="overflow-y-auto p-3">
            <div className="mb-2 flex justify-end">
              <Button variant="ghost" size="sm" icon="Maximize2" onClick={() => record.onFull(true)}>Open full page</Button>
            </div>
            {record.body}
          </div>
        </Modal>
      )}
      {sideView && narrow && (
        <Modal open onClose={onCloseSide} title="Details" placement="sheet" size="lg">
          <div className="overflow-y-auto p-3">{side ?? <NotBuilt view={sideView} label={titleOf(manifest.views, sideView)} />}</div>
        </Modal>
      )}
    </div>
  );
}
