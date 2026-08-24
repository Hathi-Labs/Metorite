"use client";

import { useMemo } from "react";
import Icon from "@/components/Icon";
import { useTaskStore, viewCounts } from "../lib/taskStore";
import { relativeTime } from "../lib/utils";
import { ViewKey } from "../lib/types";

type NavRow = {
  view: ViewKey;
  label: string;
  /** Lucide icon NAME — the theme picks the pack (see DESIGN_SYSTEM.md). */
  icon: string;
  /** show the count badge */
  showCount?: boolean;
  /** not yet built — rendered disabled with a "soon" tag */
  soon?: boolean;
};

// The single flat nav. "My Next Actions" is one destination — its slices
// (Priority / Suggestion / Context / Energy grouping, list vs board) are
// configured IN the view (toolbar group-by + column settings), not as separate
// sidebar entries. Priority and Engage were removed as standalone views for
// that reason.
const PRIMARY: NavRow[] = [
  { view: "inbox", label: "Inbox", icon: "Inbox", showCount: true },
  { view: "next", label: "My Next Actions", icon: "ListChecks", showCount: true },
  { view: "waiting", label: "Waiting For", icon: "Clock", showCount: true },
  // ⚠️ Calendar left this list 2026-08-24 (D54, board WS-39 S2): it is its own
  // Personal Center app at `/calendar`. It is deliberately NOT re-added here as
  // a link — a sidebar row that navigates OUT of the app it sits in is the kind
  // of half-move that leaves two entry points for one surface.
  // Projects and People were removed here 2026-08-06 (owner decision): this
  // app is the personal lens. The company's projects live in `/projects` and
  // the directory in `/people`, each a whole app rather than a cramped tab
  // behind a task manager.
  { view: "someday", label: "Someday / Maybe", icon: "Lightbulb", showCount: true },
  { view: "done", label: "Done", icon: "CheckCircle2", showCount: true },
  { view: "archive", label: "Archive", icon: "Archive" },
];

const SECONDARY: NavRow[] = [
  { view: "horizons", label: "Horizons of Focus", icon: "Mountain", soon: true },
];

export function ListsSidebar({
  onNavigate,
  onOpenAssistant,
  assistantActive,
}: {
  onNavigate?: () => void;
  /** Open the AI assistant as a scene (email-app pattern). */
  onOpenAssistant?: () => void;
  /** Highlight the Assistant entry while its scene is open. */
  assistantActive?: boolean;
} = {}) {
  const items = useTaskStore((s) => s.items);
  const selectedView = useTaskStore((s) => s.selectedView);
  const selectedContext = useTaskStore((s) => s.selectedContext);
  const selectViewRaw = useTaskStore((s) => s.selectView);
  const accounts = useTaskStore((s) => s.accounts);
  const openWorkspaces = useTaskStore((s) => s.openWorkspaces);
  const openSettings = useTaskStore((s) => s.openSettings);
  const loadArchive = useTaskStore((s) => s.loadArchive);
  const loadDone = useTaskStore((s) => s.loadDone);
  const sourceFilter = useTaskStore((s) => s.sourceFilter);
  const setSourceFilter = useTaskStore((s) => s.setSourceFilter);
  const syncNow = useTaskStore((s) => s.syncNow);
  const syncing = useTaskStore((s) => s.syncing);
  const selectView: typeof selectViewRaw = (v) => {
    selectViewRaw(v);
    // Archived tasks aren't in the normal hydrate — pull them on demand.
    if (v === "archive") void loadArchive();
    // DONE tasks are excluded from the normal hydrate too — load on open.
    if (v === "done") void loadDone();
    onNavigate?.();
  };
  // Counts must honor the All / Mine / ClickUp source toggle, otherwise the
  // badges stay frozen at the "All" totals while the list below re-filters.
  const counts = useMemo(
    () => viewCounts(items, sourceFilter),
    [items, sourceFilter],
  );

  return (
    <nav className="flex h-full flex-col gap-1 overflow-y-auto p-3 text-sm">
      <div className="px-2 pb-2 pt-1">
        <h2 className="text-sm font-semibold text-foreground">Tasks</h2>
        <p className="text-[11px] text-muted-foreground">Getting Things Done</p>
      </div>

      {/* Source filter — persistent across every view, so wherever local and
          ClickUp tasks are mixed you can narrow to just your own or just the
          workspace's. Only shown once a workspace is connected. */}
      {accounts.length > 0 && (
        <div className="mb-1 px-1">
          <div className="flex items-center gap-0.5 rounded-lg border border-border bg-background p-0.5">
            {(
              [
                { id: "all", label: "All", icon: "Layers" },
                { id: "local", label: "Mine", icon: "HardDrive" },
                { id: "synced", label: "ClickUp", icon: "Cloud" },
              ] as const
            ).map(({ id, label, icon }) => (
              <button
                key={id}
                type="button"
                onClick={() => setSourceFilter(id)}
                aria-pressed={sourceFilter === id}
                title={
                  id === "local"
                    ? "Only tasks you captured here (local)"
                    : id === "synced"
                      ? "Only tasks mirrored from ClickUp"
                      : "All tasks (local + ClickUp)"
                }
                className={[
                  "tech-transition flex flex-1 items-center justify-center gap-1 rounded-md px-1.5 py-1 text-[11px] font-medium",
                  sourceFilter === id
                    ? "bg-primary text-primary-foreground"
                    : "text-muted-foreground hover:text-foreground",
                ].join(" ")}
              >
                <Icon name={icon} className="h-3 w-3 shrink-0" />
                {label}
              </button>
            ))}
          </div>
        </div>
      )}

      {PRIMARY.map((row) => {
        const count = counts[row.view];
        // My Next Actions stays highlighted even when an in-view @context pill is
        // active (selectedContext set) — it's still the Next Actions view.
        const active =
          selectedView === row.view &&
          (row.view === "next" ? true : !selectedContext);
        return (
          <NavButton
            key={row.view}
            row={row}
            active={active}
            count={count}
            onClick={() => selectView(row.view)}
          />
        );
      })}

      <div className="mt-3 border-t border-border pt-3">
        <p className="px-2 pb-1 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
          Higher altitude
        </p>
        {SECONDARY.map((row) => (
          <NavButton key={row.view} row={row} active={false} onClick={() => {}} />
        ))}
      </div>

      {/* AI assistant — opens as a scene (mirrors the email app's left-rail
          Chat entry) instead of an always-on right rail. */}
      {onOpenAssistant && (
        <div className="mt-3 border-t border-border pt-3">
          <button
            type="button"
            onClick={() => {
              onOpenAssistant();
              onNavigate?.();
            }}
            aria-pressed={assistantActive}
            className={[
              "tech-transition flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left",
              assistantActive
                ? "bg-primary/10 text-primary"
                : "text-muted-foreground hover:bg-secondary hover:text-foreground",
            ].join(" ")}
          >
            <Icon name="Sparkles" className="h-4 w-4 shrink-0" />
            <span className="flex-1">Assistant</span>
          </button>
        </div>
      )}

      <div className="mt-3 border-t border-border pt-3">
        <p className="px-2 pb-1 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
          Workspaces
        </p>
        {accounts.map((a) => (
          <div
            key={a.id}
            className="flex items-center gap-2.5 rounded-lg px-2 py-1.5 text-sm text-muted-foreground"
          >
            <Icon name="Cloud" className="h-3.5 w-3.5 shrink-0 text-primary/70" />
            <div className="min-w-0 flex-1">
              <span className="block truncate">{a.label}</span>
              {/* Sync visibility: last pull time (or the error), so it's clear
                  whether the ClickUp mirror is current without opening the
                  Workspaces modal. */}
              <span
                className={[
                  "flex items-center gap-1 truncate text-[10px]",
                  a.syncError ? "text-destructive" : "text-muted-foreground/70",
                ].join(" ")}
                title={a.syncError || undefined}
              >
                {a.syncError ? (
                  <>
                    <Icon name="AlertTriangle" className="h-2.5 w-2.5 shrink-0" />
                    Sync failed
                  </>
                ) : syncing ? (
                  "Syncing…"
                ) : a.lastSyncedAt ? (
                  `Synced ${relativeTime(a.lastSyncedAt)}`
                ) : (
                  "Not synced yet"
                )}
              </span>
            </div>
            {/* Manual sync — pull this workspace's ClickUp tasks now, from
                anywhere (previously only in the Workspaces modal). */}
            <button
              type="button"
              title="Sync this workspace's ClickUp tasks now"
              aria-label={`Sync ${a.label} now`}
              disabled={syncing}
              onClick={() => void syncNow(a.id)}
              className="tech-transition shrink-0 rounded-md p-1 text-muted-foreground/70 hover:bg-secondary hover:text-foreground disabled:opacity-50"
            >
              {syncing ? (
                <Icon name="Loader2" className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Icon name="RefreshCw" className="h-3.5 w-3.5" />
              )}
            </button>
          </div>
        ))}
        <button
          type="button"
          onClick={() => {
            openWorkspaces();
            onNavigate?.();
          }}
          className="tech-transition flex w-full items-center gap-2.5 rounded-lg px-2 py-2 text-left text-sm text-muted-foreground hover:bg-secondary hover:text-foreground"
        >
          <Icon name="Plug" className="h-3.5 w-3.5 shrink-0" />
          <span className="flex-1">Connect workspace…</span>
        </button>
        <button
          type="button"
          onClick={() => {
            openSettings();
            onNavigate?.();
          }}
          className="tech-transition flex w-full items-center gap-2.5 rounded-lg px-2 py-2 text-left text-sm text-muted-foreground hover:bg-secondary hover:text-foreground"
        >
          <Icon name="Settings2" className="h-3.5 w-3.5 shrink-0" />
          <span className="flex-1">Settings</span>
        </button>
      </div>
    </nav>
  );
}

function NavButton({
  row,
  active,
  count,
  onClick,
}: {
  row: NavRow;
  active: boolean;
  count?: number;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      disabled={row.soon}
      onClick={onClick}
      className={[
        "tech-transition flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left",
        row.soon
          ? "cursor-default text-muted-foreground/50"
          : active
            ? "bg-primary/10 text-primary"
            : "text-muted-foreground hover:bg-secondary hover:text-foreground",
      ].join(" ")}
    >
      <Icon name={row.icon} className="h-4 w-4 shrink-0" />
      <span className="flex-1 truncate">{row.label}</span>
      {row.soon ? (
        <span className="rounded bg-muted px-1.5 py-0.5 text-[9px] font-medium uppercase text-muted-foreground">
          soon
        </span>
      ) : row.showCount && count ? (
        <span
          className={[
            "min-w-[18px] rounded-full px-1.5 py-0.5 text-center text-[10px] font-semibold",
            active ? "bg-primary/20 text-primary" : "bg-muted text-muted-foreground",
          ].join(" ")}
        >
          {count}
        </span>
      ) : null}
    </button>
  );
}

