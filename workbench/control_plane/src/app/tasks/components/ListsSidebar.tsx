"use client";

import { useMemo } from "react";
import Icon from "@/components/Icon";
import { useTaskStore, viewCounts } from "../lib/taskStore";
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
  const openSettings = useTaskStore((s) => s.openSettings);
  const loadArchive = useTaskStore((s) => s.loadArchive);
  const loadDone = useTaskStore((s) => s.loadDone);
  const sourceFilter = useTaskStore((s) => s.sourceFilter);
  const setSourceFilter = useTaskStore((s) => s.setSourceFilter);
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

      {/* ⚠️ The read-only "Workspaces" list was DELETED here (WS-39
          S3a-client slice 4), superseding S1 repair round 1's compromise.
          That round kept the account ROWS visible because D52 speaks only
          of COLUMNS and is silent on rows — a fair reading at the time. This
          slice deletes the whole `/tasks/accounts` client, so there is
          nothing left to read them with, and a section that can only ever
          render empty is not a compromise, it is a dead branch.
          Provenance is not lost: a row imported before the retirement keeps
          its source badge and its deep link in the task detail, which is
          where somebody actually asks where a task came from. */}

      <div className="mt-3 border-t border-border pt-3">
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

