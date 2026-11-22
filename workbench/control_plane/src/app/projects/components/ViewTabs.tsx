"use client";

/**
 * Projects · the view tab row (WS-64 OP-12, `one_projects_app.md` §5.3, D-OP-17).
 *
 * The six canvases, then the project's saved views as named tabs, then
 * "+ View". It replaces the canvas strip and the saved-views list that the View
 * popover held. A phone keeps the View sheet and its vertical list, so this row
 * is for a desktop and a tablet.
 *
 * It draws with `ModeSwitch`, so the tab on screen wears the house ON pair.
 * What fits is decided by `lib/viewTabs.ts`: icons only below 1280 px, then the
 * tabs that do not fit fold into "More". The row never scrolls sideways.
 *
 * Rules of hooks: every hook is above the one return.
 */

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";

import Icon from "@/components/Icon";
import { type ModeOption, ModeSwitch } from "@/components/ModeSwitch";
import AnchoredPanel from "@/components/ui/AnchoredPanel";
import Button from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { useDismiss } from "@/hooks/useDismiss";

import { VIEW_MODES, type ViewMode } from "../lib/commands";
import type { ViewRow } from "../lib/api";
import {
  type TabSpec,
  canvasTitle,
  layoutTabs,
  viewTabIcon,
} from "../lib/viewTabs";

/** The id of a saved view's tab, kept apart from the canvas ids. */
const VIEW_PREFIX = "view:";

function sentenceCase(id: string): string {
  return id.charAt(0).toUpperCase() + id.slice(1);
}

export interface ViewTabsProps {
  /** The canvas on screen, or null while a lens (Needs attention) replaces it. */
  canvas: ViewMode | null;
  onCanvas: (next: ViewMode) => void;
  views: readonly ViewRow[];
  activeViewId: string | null;
  onView: (view: ViewRow) => void;
  /** Saves the filters now on screen as a view with this name. */
  onAddView: (name: string) => void;
  /** Deletes the view. The tab on screen carries the control. */
  onDeleteView: (view: ViewRow) => void;
  /** False while nothing is filtered, regrouped or reshaped: nothing to save. */
  canAddView: boolean;
  addViewHint?: string;
  /** Classes for the outer box, so the page can move the row below the title in a narrow pane. */
  className?: string;
}

/** A menu that hangs under a button of the row. */
function RowMenu({
  label,
  icon,
  children,
  highlighted = false,
  ariaLabel,
  iconOnly = false,
}: {
  label: string;
  ariaLabel?: string;
  /** The bare icon, for a narrow row. The aria-label and the title carry the name. */
  iconOnly?: boolean;
  icon: string;
  children: (close: () => void) => React.ReactNode;
  highlighted?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  const root = useRef<HTMLSpanElement | null>(null);
  const close = () => setOpen(false);
  useDismiss(open, close, root);
  return (
    <span ref={root} className="inline-flex shrink-0">
      <Button
        variant="ghost"
        size={iconOnly ? "icon-sm" : "sm"}
        icon={icon}
        selected={highlighted}
        aria-haspopup="menu"
        aria-label={ariaLabel ?? (iconOnly ? label : undefined)}
        title={iconOnly ? (ariaLabel ?? label) : undefined}
        aria-expanded={open}
        onClick={(event) => {
          setAnchor(event.currentTarget);
          setOpen((now) => !now);
        }}
      >
        {iconOnly ? null : label}
      </Button>
      <AnchoredPanel
        anchor={anchor}
        open={open}
        align="end"
        maxHeight={420}
        className="w-64 max-h-[420px]"
        panelProps={{ role: "menu", "aria-label": label }}
      >
        <div className="p-1">{children(close)}</div>
      </AnchoredPanel>
    </span>
  );
}

export function ViewTabs({
  canvas,
  onCanvas,
  views,
  activeViewId,
  onView,
  onAddView,
  onDeleteView,
  canAddView,
  addViewHint,
  className = "",
}: ViewTabsProps) {
  const box = useRef<HTMLDivElement | null>(null);
  // Wide until measured: the first paint on the server and in the browser
  // agree, and the effect below settles it before the member sees the row.
  const inner = useRef<HTMLDivElement | null>(null);
  const [size, setSize] = useState({ available: 2000, viewport: 1440 });
  // Pixels that the estimates were short by, found by measuring the real tabs.
  const [slack, setSlack] = useState(0);
  const [viewName, setViewName] = useState("");

  const measure = useCallback(() => {
    const el = box.current;
    if (!el) return;
    const next = { available: el.clientWidth, viewport: window.innerWidth };
    setSize((now) => {
      if (now.available === next.available && now.viewport === next.viewport) return now;
      setSlack(0);
      return next;
    });
  }, []);

  useLayoutEffect(() => {
    measure();
    const el = box.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const watcher = new ResizeObserver(measure);
    watcher.observe(el);
    return () => watcher.disconnect();
  }, [measure]);
  useEffect(() => {
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, [measure]);

  const canvasOptions = useMemo(
    () =>
      VIEW_MODES.map((entry) => ({
        id: entry.id as ViewMode,
        icon: entry.icon,
        label: sentenceCase(entry.id),
        key: entry.key as string,
      })),
    []
  );
  const specs: TabSpec[] = useMemo(
    () => [
      ...canvasOptions.map((c) => ({ id: c.id, label: c.label, kind: "canvas" as const })),
      ...views.map((v) => ({ id: VIEW_PREFIX + v.id, label: v.name, kind: "view" as const })),
    ],
    [canvasOptions, views]
  );
  const savedTab = activeViewId ? VIEW_PREFIX + activeViewId : null;
  const layout = layoutTabs({
    tabs: specs,
    // The canvas and the saved view on screen are the last to fold in their group.
    active: [canvas, savedTab],
    available: size.available - slack,
    viewport: size.viewport,
  });
  const visible = new Set(layout.visible);
  const canvasModes: ModeOption<ViewMode>[] = canvasOptions
    .filter((c) => visible.has(c.id))
    .map((c) => ({ id: c.id, icon: c.icon, label: c.label, title: canvasTitle(c.label, c.key) }));
  const viewModes: ModeOption<string>[] = views
    .filter((v) => visible.has(VIEW_PREFIX + v.id))
    .map((v) => ({ id: v.id, icon: viewTabIcon(v.view_type), label: v.name, title: v.name }));
  const foldedCanvases: ModeOption<ViewMode>[] = canvasOptions
    .filter((c) => !visible.has(c.id))
    .map((c) => ({ id: c.id, icon: c.icon, label: c.label, title: canvasTitle(c.label, c.key) }));
  const foldedViews: ModeOption<string>[] = views
    .filter((v) => !visible.has(VIEW_PREFIX + v.id))
    .map((v) => ({ id: v.id, icon: viewTabIcon(v.view_type), label: v.name, title: v.name }));
  const folded = foldedCanvases.length + foldedViews.length > 0;
  const activeSavedView = views.find((v) => v.id === activeViewId) ?? null;
  // The widths are estimates. When the real tabs overflow the box, take the
  // difference off the room and lay out again, before the member sees it.
  const shownKey = layout.visible.join("|");
  useLayoutEffect(() => {
    const box_ = box.current;
    const row = inner.current;
    if (!box_ || !row || layout.visible.length <= 1) return;
    const over = row.scrollWidth - box_.clientWidth;
    if (over > 0) setSlack((now) => now + over);
  }, [shownKey, size, slack, layout.visible.length, layout.iconOnly]);
  const viewById = (id: string) => views.find((v) => v.id === id);

  return (
    // The outer box is the space the row MAY take: it is flex-1 beside a
    // shrink-0 group, so its width never follows the tabs it holds. The fold
    // measures this box and not the tabs.
    <div ref={box} className={`min-w-0 flex-1 overflow-hidden ${className}`} data-view-tabs="" data-icon-only={layout.iconOnly ? "" : undefined}>
    <div ref={inner} className="flex w-max max-w-full items-center gap-1">
      <ModeSwitch
        modes={canvasModes}
        mode={canvas}
        onPick={onCanvas}
        iconOnly={layout.iconOnly}
        label="Canvas"
      />
      {viewModes.length > 0 ? (
        <>
          <span className="mx-1 h-4 w-px shrink-0 bg-border" aria-hidden="true" />
          <ModeSwitch
            modes={viewModes}
            mode={activeViewId}
            onPick={(id) => {
              const view = viewById(id);
              if (view) onView(view);
            }}
            label="Saved views"
          />
        </>
      ) : null}
      {activeSavedView ? (
        <button
          type="button"
          aria-label={`Delete view ${activeSavedView.name}`}
          title={`Delete view ${activeSavedView.name}`}
          onClick={() => onDeleteView(activeSavedView)}
          className="shrink-0 rounded-md p-1 text-muted-foreground hover:bg-muted"
        >
          <Icon name="X" size={11} />
        </button>
      ) : null}
      {folded ? (
        <RowMenu label="More" ariaLabel="More views" iconOnly={layout.iconOnly} icon="MoreHorizontal" highlighted={foldedViews.some((v) => v.id === activeViewId)}>
          {(close) => (
            <div className="flex flex-col gap-1" data-view-tabs-more="">
              {foldedCanvases.length > 0 ? (
                <ModeSwitch
                  modes={foldedCanvases}
                  mode={canvas}
                  layout="sheet"
                  label="More canvases"
                  onPick={(next) => {
                    onCanvas(next);
                    close();
                  }}
                />
              ) : null}
              {foldedViews.length > 0 ? (
                <ModeSwitch
                  modes={foldedViews}
                  mode={activeViewId}
                  layout="sheet"
                  label="More saved views"
                  onPick={(id) => {
                    const view = viewById(id);
                    if (view) onView(view);
                    close();
                  }}
                />
              ) : null}
            </div>
          )}
        </RowMenu>
      ) : null}
      <RowMenu label="View" icon="Plus" ariaLabel="New saved view" iconOnly={layout.iconOnly}>
        {(close) => (
          <form
            className="flex flex-col gap-1.5 p-2"
            onSubmit={(event) => {
              event.preventDefault();
              const name = viewName.trim();
              if (!name || !canAddView) return;
              onAddView(name);
              setViewName("");
              close();
            }}
          >
            <span className="text-[11px] font-medium text-muted-foreground">
              Save these filters and columns as a view
            </span>
            <Input
              autoFocus
              inputSize="sm"
              value={viewName}
              onChange={(e) => setViewName(e.target.value)}
              placeholder="View name"
              aria-label="View name"
              disabled={!canAddView}
            />
            <Button type="submit" size="sm" disabled={!canAddView || !viewName.trim()} title={canAddView ? undefined : addViewHint}>
              Save view
            </Button>
            {!canAddView && addViewHint ? (
              <p className="text-[11px] text-muted-foreground">{addViewHint}</p>
            ) : null}
          </form>
        )}
      </RowMenu>
    </div>
    </div>
  );
}

export default ViewTabs;
