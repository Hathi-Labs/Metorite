"use client";

/**
 * SidePanelEditor — VS Code-style collapsible editor panel that sits between the
 * file manager and the chat. Shows the agent's live documents as tabs, each with
 * a rendered ↔ edit toggle and live Markdown/HTML preview (DocumentPane).
 *
 * State lives in sidePanelStore (module-level) so the file tree, inline chat
 * cards, and the agent-context fold all stay in sync with what's open here.
 * Reads the store via useSyncExternalStore with a STABLE snapshot (getState
 * returns the same reference until a real change) to avoid React #185.
 */

import Icon from "@/components/Icon";
import { useEffect, useRef, useSyncExternalStore } from "react";
import {
  subscribe,
  getState,
  closeDoc,
  focusDoc,
  togglePanel,
  setPanelWidth,
  persistPanelWidth,
  hydratePanelWidth,
  MIN_PANEL_WIDTH,
  MAX_PANEL_WIDTH,
  DEFAULT_PANEL_WIDTH,
} from "@/lib/sidePanelStore";
import DocumentPane from "@/components/DocumentPane";
import GenerativeUINode from "@/components/GenerativeUINode";
import { emitAgentEvent } from "@/lib/agentEvents";

/**
 * Drag target on the panel's right edge.
 *
 * Uses pointer CAPTURE rather than the window-listener pattern used elsewhere in
 * the app (see TimeGrid). It has to: this panel renders artifacts inside an
 * iframe, and the moment the pointer crosses that iframe it swallows the events,
 * so a window listener would drop the drag mid-gesture. Capture keeps every
 * pointer event routed to this handle until release.
 */
function ResizeHandle({ width }: { width: number }) {
  const drag = useRef<{ startX: number; startWidth: number } | null>(null);

  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label="Resize document panel"
      aria-valuenow={width}
      aria-valuemin={MIN_PANEL_WIDTH}
      aria-valuemax={MAX_PANEL_WIDTH}
      tabIndex={0}
      onPointerDown={(e) => {
        // Ignore secondary buttons so a right-click never starts a drag.
        if (e.button !== 0) return;
        e.preventDefault();
        e.currentTarget.setPointerCapture(e.pointerId);
        drag.current = { startX: e.clientX, startWidth: width };
      }}
      onPointerMove={(e) => {
        const d = drag.current;
        if (!d) return;
        // Panel sits on the LEFT, so dragging right widens it.
        setPanelWidth(d.startWidth + (e.clientX - d.startX));
      }}
      onPointerUp={(e) => {
        if (!drag.current) return;
        drag.current = null;
        e.currentTarget.releasePointerCapture(e.pointerId);
        persistPanelWidth();
      }}
      onPointerCancel={() => {
        drag.current = null;
      }}
      onDoubleClick={() => {
        setPanelWidth(DEFAULT_PANEL_WIDTH);
        persistPanelWidth();
      }}
      onKeyDown={(e) => {
        const step = e.shiftKey ? 64 : 16;
        if (e.key === "ArrowLeft") {
          setPanelWidth(width - step);
        } else if (e.key === "ArrowRight") {
          setPanelWidth(width + step);
        } else {
          return;
        }
        e.preventDefault();
        persistPanelWidth();
      }}
      title="Drag to resize · double-click to reset"
      // A 1px border is impossible to grab, so the hit area is 7px straddling
      // the edge while the visible line stays hairline-thin until hovered.
      className="group/resize absolute -right-[3px] top-0 z-20 h-full w-[7px] cursor-col-resize touch-none select-none focus-visible:outline-none"
    >
      <div className="pointer-events-none absolute inset-y-0 left-[3px] w-px bg-transparent transition-colors group-hover/resize:bg-primary group-focus-visible/resize:bg-primary" />
    </div>
  );
}

export default function SidePanelEditor({
  hideWhenEmpty = false,
}: {
  /**
   * Draw nothing, not even the collapsed strip, until a document is open. For
   * a host where the panel is occasional (the Projects page), rather than a
   * standing part of the layout (the chat page).
   */
  hideWhenEmpty?: boolean;
} = {}) {
  const state = useSyncExternalStore(subscribe, getState, getState);
  const { open, docs, activePath, width } = state;

  // Apply the persisted width once on the client. Not seeded at module load —
  // that would desync SSR and hydration (see sidePanelStore).
  useEffect(() => {
    hydratePanelWidth();
  }, []);

  // Collapsed rail — a thin strip that reopens the panel and shows a doc
  // count. The WHOLE rail is the click target (a thin strip is fiddly to hit
  // an icon inside).
  if (!open && hideWhenEmpty && docs.length === 0) return null;
  if (!open) {
    return (
      <aside className="flex w-10 shrink-0 flex-col border-r border-border bg-card/40">
        <button
          onClick={togglePanel}
          className="flex w-full flex-1 cursor-pointer flex-col items-center py-2.5 text-muted-foreground hover:bg-secondary/60 hover:text-foreground transition-colors"
          title="Open document panel"
        >
          <Icon name="FileText" size={15} />
          {docs.length > 0 && (
            <span className="mt-1 rounded-full bg-secondary px-1 text-[10px]">
              {docs.length}
            </span>
          )}
          <span className="mt-3 flex flex-1 items-center justify-center">
            <span
              className="text-[10px] font-semibold tracking-widest"
              style={{ writingMode: "vertical-rl", transform: "rotate(180deg)" }}
            >
              DOCUMENTS
            </span>
          </span>
        </button>
      </aside>
    );
  }

  const active = docs.find((d) => d.path === activePath) ?? null;

  return (
    <aside
      className="relative flex shrink-0 flex-col border-r border-border bg-card/20"
      style={{ width }}
    >
      <ResizeHandle width={width} />
      {/* Tab bar */}
      <div className="flex items-stretch border-b border-border bg-card/50">
        <div className="flex flex-1 items-stretch overflow-x-auto">
          {docs.length === 0 ? (
            <div className="flex items-center px-3 py-2 text-xs text-muted-foreground">
              Documents
            </div>
          ) : (
            docs.map((d) => {
              const isActive = d.path === activePath;
              return (
                <div
                  key={d.path}
                  onClick={() => focusDoc(d.path)}
                  className={`group flex max-w-[14rem] cursor-pointer items-center gap-1.5 border-r border-border px-3 py-2 text-xs transition-colors ${
                    isActive
                      ? "bg-background text-foreground"
                      : "text-muted-foreground hover:bg-secondary/60 hover:text-foreground"
                  }`}
                  title={d.path}
                >
                  {d.live && <Icon name="Loader2" size={10} className="shrink-0 animate-spin text-primary" />}
                  <span className="truncate">{d.name}</span>
                  {/* Always rendered, never opacity-0: a hover-only close button
                      is invisible on touch, and undiscoverable on a pointer
                      device until you happen to hover the right tab. Dimmed at
                      rest, full strength on hover/focus. */}
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      closeDoc(d.path);
                    }}
                    className="shrink-0 rounded p-0.5 text-muted-foreground/70 hover:bg-secondary hover:text-foreground focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring group-hover:text-foreground transition"
                    aria-label={`Close ${d.name}`}
                    title={`Close ${d.name}`}
                  >
                    <Icon name="X" size={12} />
                  </button>
                </div>
              );
            })
          )}
        </div>
        <button
          onClick={togglePanel}
          className="flex shrink-0 items-center border-l border-border px-2 text-muted-foreground hover:bg-secondary hover:text-foreground transition-colors"
          title="Collapse document panel"
        >
          <Icon name="PanelLeftClose" size={15} />
        </button>
      </div>

      {/* Active document / generative-UI view */}
      <div className="flex flex-1 min-h-0 flex-col">
        {active && active.kind === "genui" ? (
          <div className="flex-1 overflow-y-auto p-4">
            <div className="mb-3 flex items-center gap-2 text-[11px] text-muted-foreground">
              <Icon name="Sparkles" size={12} className="text-primary" />
              Interactive view — generated by the agent
            </div>
            <GenerativeUINode
              key={`${active.sessionId}:${active.path}`}
              spec={active.spec}
              onAction={(message) =>
                // Route through the global bus: AgentChat owns the send /
                // HITL-resume plumbing and subscribes per thread.
                emitAgentEvent("onCustomEvent", {
                  name: "genui_panel_action",
                  value: { message, spec: active.spec },
                  threadId: active.sessionId,
                })
              }
            />
          </div>
        ) : active ? (
          <DocumentPane
            key={`${active.sessionId}:${active.path}`}
            sessionId={active.sessionId}
            path={active.path}
            name={active.name}
            live={active.live}
          />
        ) : (
          <div className="flex flex-1 flex-col items-center justify-center gap-2 p-6 text-center">
            <Icon name="FileText" size={22} className="text-muted-foreground/50" />
            <p className="text-xs text-muted-foreground">No document open.</p>
            <p className="max-w-[16rem] text-[11px] text-muted-foreground/70">
              Open a file from the Files panel (or right-click → “Open in side panel”).
              Documents the agent writes appear here automatically.
            </p>
          </div>
        )}
      </div>
    </aside>
  );
}
