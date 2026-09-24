"use client";

import Button from "@/components/ui/Button";
import AppIcon, { themedIcon, type ThemedIcon } from "@/components/Icon";
import { useRouter } from "next/navigation";
import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  useSyncExternalStore,
  KeyboardEvent,
} from "react";
import FilterPills from "@/components/FilterPills";
import { taskDeepLink } from "@/app/projects/lib/card";
import { clampCursor, stepCursor } from "@/lib/cursor";
import {
  NO_SELECTION,
  type SelectionState,
  clickSelect,
} from "@/lib/selection";
import { useTaskStore } from "../lib/taskStore";
import { Disposition, MyTask } from "../lib/types";
import {
  DateBucketKey,
  dateBucket,
  isTickled,
  resurfacesAt,
  matchWhere,
  msSince,
  relativeTime,
} from "../lib/utils";
import {
  type InboxScope,
  type InboxSource,
  SOURCE_PILLS,
  filterBySource,
  inboxKind,
  inboxKindCounts,
  inboxRows,
  orderInbox,
} from "../lib/inbox";
import {
  type CaptureDestination,
  openHashQuery,
  parseProjectToken,
  stripOpenHash,
} from "../lib/quickAdd";
import { captureDestinations, destinations, useCompanyTree } from "../lib/companyTree";
import { promoteAllowed } from "../lib/promote";
import { InboxCard } from "./InboxCard";
import { InboxTable } from "./InboxTable";
import { AttachmentComposer } from "./AttachmentComposer";
import type { TaskAttachment } from "../lib/types";
import { ClarifyModal } from "./ClarifyModal";
import { CaptureProjectChip } from "./CaptureProjectChip";
import { PromoteDialog } from "./PromoteDialog";

const AGING_MS = 3 * 24 * 3600 * 1000; // GTD: empty regularly — flag stale items

/** The inbox's selection is its own (its bulk actions are GTD dispositions,
 *  not archive/delete), but the GRAMMAR is the shared one — see `toggleSelect`. */
const NOBODY: ReadonlySet<string> = new Set();

// Density preference (cards vs Notion-style dense list), sticky per browser.
// Read via useSyncExternalStore so SSR HTML (always "cards") hydrates cleanly
// and the client value takes over without a mismatch.
const DENSITY_KEY = "cc.tasks.inboxDensity";
const densityListeners = new Set<() => void>();
function subscribeDensity(cb: () => void) {
  densityListeners.add(cb);
  const onStorage = (e: StorageEvent) => {
    if (e.key === DENSITY_KEY) cb();
  };
  window.addEventListener("storage", onStorage);
  return () => {
    densityListeners.delete(cb);
    window.removeEventListener("storage", onStorage);
  };
}
function readDensity(): "cards" | "list" {
  try {
    return window.localStorage.getItem(DENSITY_KEY) === "list" ? "list" : "cards";
  } catch {
    return "cards";
  }
}

type DateFilter = "all" | DateBucketKey;
type SortOrder = "newest" | "oldest";

/**
 * The Inbox (my_tasks_cutover.md §5 S6g — one inbox).
 *
 * ONE list with ONE row component for both kinds of task: my captures and the
 * board rows a colleague put on my plate. Board rows come first, then the
 * captures, each block in the member's sort. `inbox.ts` is the rule for what
 * the list holds, and the sidebar badge reads the same rule, so the header's
 * "N to process" equals the badge. Search, the date pills, the source pills,
 * selection, the keyboard and "Clarify next" all walk this one list.
 *
 * ⚠️ The Inbox is NEVER scoped by an Area (S6b repair, 2026-09-23). A capture
 * lands in the personal ROOT, before any Area, so an Area scope would empty
 * this list. Clarify is where a capture gets its Area.
 */
export function InboxView() {
  const router = useRouter();
  const items = useTaskStore((s) => s.items);
  const loading = useTaskStore((s) => s.loading);
  const backend = useTaskStore((s) => s.backend);
  const capture = useTaskStore((s) => s.capture);
  const captureTo = useTaskStore((s) => s.captureTo);
  const openClarify = useTaskStore((s) => s.openClarify);
  const openQuickCapture = useTaskStore((s) => s.openQuickCapture);
  const lastCaptureIds = useTaskStore((s) => s.lastCaptureIds);
  const undoLastCapture = useTaskStore((s) => s.undoLastCapture);
  const quickDispose = useTaskStore((s) => s.quickDispose);
  const bulkDispose = useTaskStore((s) => s.bulkDispose);
  const requestDelete = useTaskStore((s) => s.requestDelete);
  const undeferItem = useTaskStore((s) => s.undeferItem);
  const dupNotice = useTaskStore((s) => s.dupNotice);
  const resolveDupNotice = useTaskStore((s) => s.resolveDupNotice);
  const processed = useTaskStore((s) => s.processedThisSession);
  const clarifyModalOpen = useTaskStore((s) => s.clarifyModalOpen);
  const quickCaptureOpen = useTaskStore((s) => s.quickCaptureOpen);
  const sourceFilter = useTaskStore((s) => s.sourceFilter);
  const setSourceFilter = useTaskStore((s) => s.setSourceFilter);
  const fromProjectIds = useTaskStore((s) => s.fromProjectIds);
  const personalRootId = useTaskStore((s) => s.personalRootId);
  const areas = useTaskStore((s) => s.areas);
  const projects = useTaskStore((s) => s.projects);
  const createArea = useTaskStore((s) => s.createArea);

  // ── what the Inbox holds, and which kind each row is (`inbox.ts`) ──
  const areaIds = useMemo(() => areas.map((a) => a.id), [areas]);
  const scope: InboxScope = useMemo(
    () => ({ personalRootId, areaIds }),
    [personalRootId, areaIds],
  );
  const kindOf = useCallback((i: MyTask) => inboxKind(i, scope), [scope]);
  const allRows = useMemo(() => inboxRows(items, fromProjectIds), [items, fromProjectIds]);
  const kindCounts = useMemo(() => inboxKindCounts(allRows, scope), [allRows, scope]);
  const activeInbox = useMemo(
    () => filterBySource(allRows, sourceFilter, scope),
    [allRows, sourceFilter, scope],
  );
  // Tickler = deferred captures, and board rows whose start date is ahead.
  const tickler = useMemo(
    () =>
      items
        .filter(
          (i) =>
            !i.archivedAt &&
            (i.disposition === "INBOX" || fromProjectIds.has(i.id)) &&
            isTickled(i),
        )
        .sort((a, b) => (resurfacesAt(a) ?? "").localeCompare(resurfacesAt(b) ?? "")),
    [items, fromProjectIds],
  );

  const oldest = useMemo(() => {
    if (!activeInbox.length) return null;
    return activeInbox.reduce((a, b) =>
      new Date(a.createdAt) < new Date(b.createdAt) ? a : b,
    );
  }, [activeInbox]);
  const isAging = !!oldest && msSince(oldest.createdAt) > AGING_MS;

  const undoCount = useMemo(
    () => lastCaptureIds.filter((id) => allRows.some((i) => i.id === id)).length,
    [lastCaptureIds, allRows],
  );

  // ── the company tree, for the capture chip and `#` (`companyTree.ts`) ──
  const { roots } = useCompanyTree(backend === "live");
  const tree = useMemo(() => destinations(roots), [roots]);
  const captureTargets = useMemo(
    () => captureDestinations({ areas, tree, projects }),
    [areas, tree, projects],
  );

  // ── local UI state ──
  const [value, setValue] = useState("");
  const [captureDest, setCaptureDest] = useState<CaptureDestination | null>(null);
  const [chipOpen, setChipOpen] = useState(false);
  const hashQuery = openHashQuery(value);
  const [search, setSearch] = useState("");
  const [dateFilter, setDateFilter] = useState<DateFilter>("all");
  const [sortOrder, setSortOrder] = useState<SortOrder>("newest");
  const density = useSyncExternalStore(subscribeDensity, readDensity, () => "cards");
  const setDensityPersist = (d: "cards" | "list") => {
    try {
      window.localStorage.setItem(DENSITY_KEY, d);
    } catch { /* private mode */ }
    densityListeners.forEach((cb) => cb());
  };
  const [pendingAtts, setPendingAtts] = useState<TaskAttachment[]>([]);
  const [showTickler, setShowTickler] = useState(false);
  const [cursorId, setCursorId] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  // S6g — the ONE promote dialog, hosted here so the card, the table, the
  // `m` key and the capture door open the same one.
  const [promote, setPromote] = useState<{ id: string; destination?: string } | null>(null);
  const promoteItem = promote ? items.find((i) => i.id === promote.id) : undefined;
  // Selection and its shift-anchor as ONE value: they are only meaningful
  // together, and holding them apart meant the keyboard's `x` could write a
  // selection from fresh state and an anchor from stale state.
  const [selection, setSelection] = useState<SelectionState>(NO_SELECTION);
  const selectedIds = selection.selected;
  const [showShortcuts, setShowShortcuts] = useState(false);
  // Inline editor for the dup-notice "rename existing" affordance: seeded with
  // the new capture's (usually clearer) title.
  const [dupRenaming, setDupRenaming] = useState(false);
  const [dupRenameValue, setDupRenameValue] = useState("");

  const bucketCounts = useMemo(() => {
    const c = { today: 0, yesterday: 0, week: 0, older: 0 };
    for (const i of activeInbox) c[dateBucket(i.createdAt).key]++;
    return c;
  }, [activeInbox]);

  const visible = useMemo(() => {
    const q = search.trim().toLowerCase();
    const filtered = activeInbox.filter((i) => {
      if (dateFilter !== "all" && dateBucket(i.createdAt).key !== dateFilter)
        return false;
      if (q && !i.title.toLowerCase().includes(q)) return false;
      return true;
    });
    return orderInbox(filtered, scope, sortOrder);
  }, [activeInbox, search, dateFilter, sortOrder, scope]);

  const pills = [
    // "Any date", not "All": the source pills beside it already say All.
    { id: "all", label: "Any date", count: activeInbox.length },
    { id: "today", label: "Today", count: bucketCounts.today },
    { id: "yesterday", label: "Yesterday", count: bucketCounts.yesterday },
    { id: "week", label: "This week", count: bucketCounts.week },
    { id: "older", label: "Older", count: bucketCounts.older },
  ].filter((p) => p.id === "all" || p.count > 0);
  // S6g — the source filter, the store's `sourceFilter` (it had no setter
  // anywhere before). Each pill says how many rows it holds.
  const sourcePills = SOURCE_PILLS.map((p) => ({ ...p, count: kindCounts[p.id] }));

  // WS-27ad — the same transition /projects' board and the rest of /tasks use:
  // a plain pick toggles and becomes the anchor, a shift-pick adds the range
  // between them in the order the list is drawn.
  const toggleSelect = useCallback(
    (id: string, shift = false) =>
      setSelection((prev) =>
        clickSelect(prev, visible.map((i) => i.id), id, shift),
      ),
    [visible],
  );
  const clearSelection = () => setSelection(NO_SELECTION);
  const bulk = (d: Disposition) => {
    bulkDispose([...selectedIds], d);
    clearSelection();
  };
  // ⚠️ S6g. A board row is never deleted from here: the delete path purges
  // when its undo window closes, and on a board task that is the TEAM's task.
  // The board rows in a selection take "Not mine" (TRASH on my overlay).
  const selectedRows = visible.filter((i) => selectedIds.has(i.id));
  const selectedBoard = selectedRows.filter((i) => kindOf(i) === "board").map((i) => i.id);
  const selectedMine = selectedRows.filter((i) => kindOf(i) === "personal").map((i) => i.id);
  const bulkRemove = () => {
    if (selectedMine.length) requestDelete(selectedMine);
    if (selectedBoard.length) bulkDispose(selectedBoard, "TRASH");
    clearSelection();
  };
  const removeLabel = selectedBoard.length
    ? selectedMine.length
      ? "Remove"
      : "Not mine"
    : "Delete";

  // ── keyboard navigation + triage over the visible list ──
  //
  // WS-27ad — the movement half is the SHARED cursor (`@/lib/cursor`). What
  // stays local is TRIAGE — `e`/`x`/`t`/`s`/`r`/`2` are GTD dispositions, and
  // S6g adds `m` (Move to project, a capture) and `o` (Open on board, a board
  // row). They walk both kinds, because `visible` holds both.
  //
  // The cursor is held as an ID here rather than an index because triage
  // ADVANCES it: dispose the current item and the row under it is gone, so
  // "the next id" has to be read before the list changes.
  useEffect(() => {
    if (showTickler) return;
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (clarifyModalOpen || quickCaptureOpen || editingId || promote) return;
      const el = e.target as HTMLElement | null;
      if (
        el &&
        (el.tagName === "INPUT" ||
          el.tagName === "TEXTAREA" ||
          el.isContentEditable)
      )
        return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (!visible.length) return;

      const rows = visible.map((i) => i.id);
      const idx = visible.findIndex((i) => i.id === cursorId);
      const cur = idx >= 0 ? visible[idx] : visible[0];
      const curKind = kindOf(cur);
      const disposeAdvance = (d: Disposition) => {
        const nextId =
          visible[idx + 1]?.id ?? visible[idx - 1]?.id ?? null;
        quickDispose(cur.id, d);
        setCursorId(nextId);
      };
      const removeAdvance = () => {
        const nextId =
          visible[idx + 1]?.id ?? visible[idx - 1]?.id ?? null;
        // A board row: "Not mine", never a delete (see `bulkRemove`).
        if (curKind === "board") quickDispose(cur.id, "TRASH");
        else requestDelete([cur.id]);
        setCursorId(nextId);
      };

      // Arrows and Enter are the shared cursor's. `stepCursor` returns null for
      // anything it does not own, so the triage switch below still sees every
      // key it cares about and nothing is eaten twice.
      const moved = stepCursor(
        rows,
        { cursor: clampCursor(rows.length, idx), anchor: null, selection: NOBODY },
        e.key,
        false,
      );
      if (moved) {
        e.preventDefault();
        // Enter clarifies rather than "opens" — the inbox's whole job is to
        // process, and `open` is the row the shared cursor was standing on.
        if (moved.open) openClarify(moved.open);
        else setCursorId(rows[moved.cursor] ?? null);
        return;
      }

      switch (e.key) {
        case "e":
          // A board task's title is the team's. Edit it on the board.
          if (curKind !== "personal") break;
          e.preventDefault();
          setEditingId(cur.id);
          break;
        case "x":
          e.preventDefault();
          toggleSelect(cur.id);
          break;
        case "t":
          e.preventDefault();
          removeAdvance();
          break;
        case "s":
          e.preventDefault();
          disposeAdvance("SOMEDAY");
          break;
        case "r":
          e.preventDefault();
          disposeAdvance("REFERENCE");
          break;
        case "2":
          e.preventDefault();
          disposeAdvance("DONE");
          break;
        case "m":
          // Move to project — a capture only. A board row is on a board.
          if (curKind !== "personal" || !promoteAllowed(cur)) break;
          e.preventDefault();
          setPromote({ id: cur.id });
          break;
        case "o":
          // Open on board — a board row only. A capture has no board.
          if (curKind !== "board") break;
          e.preventDefault();
          router.push(taskDeepLink(cur));
          break;
        // `u` (undo) is handled globally by <UndoToast/> so it works in every
        // view, not just the inbox.
        case "Escape":
          clearSelection();
          setCursorId(null);
          break;
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [
    visible,
    cursorId,
    editingId,
    showTickler,
    clarifyModalOpen,
    quickCaptureOpen,
    promote,
    kindOf,
    router,
    openClarify,
    quickDispose,
    requestDelete,
    toggleSelect,
  ]);

  const submit = () => {
    const raw = value.trim();
    if (!raw) return;
    // S6g — a destination from the chip, or from a `#Name` token in the line.
    const parsed = parseProjectToken(raw, captureTargets);
    const dest = captureDest ?? parsed.match ?? null;
    const title = captureDest ? raw : parsed.match ? parsed.title : raw;
    const atts = pendingAtts.length ? pendingAtts : undefined;
    if (dest && title) {
      void captureTo(title, dest, atts).then((res) => {
        if (res.needsFields) {
          // The project has required fields the capture does not carry. Open
          // the promote dialog on it, prefilled, rather than send a refusal.
          setPromote({ id: res.needsFields.taskId, destination: res.needsFields.destinationId });
        }
      });
    } else {
      capture(raw, atts);
    }
    setValue("");
    setPendingAtts([]);
    setChipOpen(false);
  };
  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Escape" && chipOpen) {
      e.preventDefault();
      setChipOpen(false);
      return;
    }
    if (e.key === "Enter") {
      e.preventDefault();
      submit();
    }
  };
  const onCaptureChange = (next: string) => {
    setValue(next);
    // `#` at the start of a word opens the picker, filtered by what follows.
    if (openHashQuery(next) !== null) setChipOpen(true);
  };
  const pickDest = (dest: CaptureDestination | null) => {
    setCaptureDest(dest);
    // A pick made while typing `#…` takes the fragment out of the title.
    if (hashQuery !== null) setValue((v) => stripOpenHash(v));
  };
  const startClarify = (id: string) => openClarify(id);

  const selectionActive = selectedIds.size > 0;
  const hasRows = allRows.length > 0;

  return (
    <div className="flex h-full flex-col bg-background">
      {/* Mobile heading — the hero is hidden on mobile, so orient the user. */}
      <div className="flex shrink-0 items-center gap-2 border-b border-border px-4 py-2.5 sm:hidden">
        <AppIcon name="Inbox" className="h-4 w-4 shrink-0 text-primary" />
        <h1 className="text-sm font-bold text-foreground">Inbox</h1>
        <span className="text-[11px] text-muted-foreground">Capture now, clarify later</span>
      </div>

      {/* Capture header — desktop only, ONE compact full-width row (mobile
          captures via the bottom-nav button): title · capture box · attach ·
          mind sweep · shortcuts. The old centered hero cost three stacked
          rows before the list started; full width also matches Next Actions
          and lets long captures breathe. */}
      <div className="hidden shrink-0 border-b border-border bg-card sm:block">
        <div className="flex items-center gap-2.5 px-4 py-2.5">
          <div className="flex shrink-0 items-center gap-2">
            <AppIcon name="Inbox" className="h-4 w-4 text-primary" />
            <h1 className="text-base font-bold text-foreground">Inbox</h1>
          </div>
          <div className="tech-transition flex min-w-0 flex-1 items-center gap-2 rounded-lg border border-border bg-background px-3 py-1.5 focus-within:border-primary/50">
            <AppIcon name="Plus" className="h-4 w-4 shrink-0 text-muted-foreground" />
            <input
              value={value}
              onChange={(e) => onCaptureChange(e.target.value)}
              onKeyDown={onKeyDown}
              placeholder="What's on your mind? Capture now, clarify later. Type # for a project."
              aria-label="Capture a task"
              className="min-w-0 flex-1 bg-transparent text-sm text-foreground placeholder:text-muted-foreground focus:outline-none"
            />
            {value.trim() ? (
              <Button size="none" radius="keep" layout="inline-flex items-center" type="button" onClick={submit} className="shrink-0 gap-1 rounded-md px-2 py-1 text-xs">
                Add <AppIcon name="CornerDownLeft" className="h-3 w-3" />
              </Button>
            ) : (
              <kbd className="shrink-0 rounded border border-border px-1.5 py-0.5 text-[10px] text-muted-foreground">
                ↵
              </kbd>
            )}
            {/* S6g — where the capture lands. "Inbox" until picked; `#` in
                the box opens the same picker. */}
            <CaptureProjectChip
              value={captureDest}
              onChange={pickDest}
              open={chipOpen}
              onOpenChange={setChipOpen}
              query={hashQuery}
              areas={areas}
              tree={tree}
              projects={projects}
              onCreateArea={createArea}
            />
          </div>
          {/* Context attachments: photo/file/link kept WITH the capture —
              icon triggers inline; pending chips appear above the icons. */}
          <div className="max-w-[320px] shrink-0">
            <AttachmentComposer compact attachments={pendingAtts} onChange={setPendingAtts} />
          </div>
          <button
            type="button"
            onClick={() => openQuickCapture("sweep")}
            title="Mind sweep — dump everything on your mind"
            className="tech-transition inline-flex shrink-0 items-center gap-1.5 rounded-md border border-border px-2 py-1.5 text-xs text-muted-foreground hover:border-primary/40 hover:text-foreground"
          >
            <AppIcon name="Wind" className="h-3.5 w-3.5" />
            <span className="hidden lg:inline">Mind sweep</span>
          </button>
          <button
            type="button"
            onClick={() => setShowShortcuts((v) => !v)}
            title="Keyboard shortcuts (press C to capture from anywhere)"
            aria-pressed={showShortcuts}
            className="tech-transition inline-flex shrink-0 items-center rounded-md border border-border p-1.5 text-muted-foreground hover:border-primary/40 hover:text-foreground"
          >
            <AppIcon name="Keyboard" className="h-3.5 w-3.5" />
          </button>
        </div>
        {showShortcuts && (
          <div className="flex flex-wrap gap-x-3 gap-y-1 border-t border-border px-4 py-2 text-[10px] text-muted-foreground">
            <Sc k="C">capture</Sc>
            {/* WS-27ad — was "j / k". The arrows are the one movement idiom
                across both task apps now; a vim walk on this screen only was a
                shortcut nobody could carry anywhere else. */}
            <Sc k="↑ / ↓">move</Sc>
            <Sc k="↵">clarify</Sc>
            <Sc k="e">edit</Sc>
            <Sc k="x">select</Sc>
            <Sc k="t">delete · not mine</Sc>
            <Sc k="m">move to project</Sc>
            <Sc k="o">open on board</Sc>
            <Sc k="s">someday</Sc>
            <Sc k="r">reference</Sc>
            <Sc k="2">do now</Sc>
            <Sc k="u">undo</Sc>
            <Sc k="esc">clear</Sc>
          </div>
        )}
      </div>

      {/* Capture undo — kept out of the hero so it shows on mobile too */}
      {undoCount > 0 && (
        <div className="shrink-0 border-b border-border bg-secondary/40">
          <div className="flex w-full items-center justify-between px-4 py-2">
            <span className="text-[11px] text-muted-foreground">
              Captured {undoCount} item{undoCount === 1 ? "" : "s"}
            </span>
            <button
              type="button"
              onClick={undoLastCapture}
              className="tech-transition inline-flex items-center gap-1 text-[11px] font-medium text-primary hover:underline"
            >
              <AppIcon name="Undo2" className="h-3.5 w-3.5" />
              Undo
            </button>
          </div>
        </div>
      )}

      {/* AI duplicate check on capture (atomizer verdicts): confident
          duplicates were auto-skipped (undoable); "similar" asks the user. */}
      {dupNotice && (
        <div className="shrink-0 border-b border-warning/30 bg-warning/10">
          <div className="flex w-full flex-wrap items-center justify-between gap-2 px-4 py-2">
            <span className="min-w-0 flex-1 text-[11px] text-foreground">
              {dupNotice.verdict === "duplicate" ? (
                <>Already {matchWhere(dupNotice.matchDisposition, dupNotice.matchSource)}: &ldquo;{dupNotice.matchTitle}&rdquo; — not added again.</>
              ) : (
                <>&ldquo;{dupNotice.title}&rdquo; looks similar to {matchWhere(dupNotice.matchDisposition, dupNotice.matchSource)}: &ldquo;{dupNotice.matchTitle}&rdquo;. Same item?</>
              )}
            </span>
            {dupRenaming ? (
              // Rename the EXISTING match to a clearer title (seeded from the
              // new capture). Back-syncs for a SYNCED match; drops the new copy.
              <span className="flex w-full shrink-0 items-center gap-2 sm:w-auto">
                <input
                  value={dupRenameValue}
                  onChange={(e) => setDupRenameValue(e.target.value)}
                  autoFocus
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && dupRenameValue.trim()) {
                      resolveDupNotice("rename", dupRenameValue);
                      setDupRenaming(false);
                    } else if (e.key === "Escape") {
                      setDupRenaming(false);
                    }
                  }}
                  className="min-w-0 flex-1 rounded-md border border-border bg-background/70 px-2 py-1 text-[11px] text-foreground focus:border-primary/50 focus:outline-none sm:w-64"
                />
                <Button size="none" radius="keep" layout="inline-flex items-center" type="button" aria-label="Save name" disabled={!dupRenameValue.trim()} onClick={() => { resolveDupNotice("rename", dupRenameValue); setDupRenaming(false); }} className="gap-1 rounded-md px-2 py-1 text-[11px]">
                  <AppIcon name="Check" className="h-3.5 w-3.5" />
                  Save
                </Button>
                <button
                  type="button"
                  aria-label="Cancel rename"
                  onClick={() => setDupRenaming(false)}
                  className="tech-transition text-muted-foreground hover:text-foreground"
                >
                  <AppIcon name="X" className="h-3.5 w-3.5" />
                </button>
              </span>
            ) : (
              <span className="flex shrink-0 items-center gap-3">
                {dupNotice.verdict === "duplicate" ? (
                  <button
                    type="button"
                    onClick={() => resolveDupNotice("keep")}
                    className="tech-transition text-[11px] font-medium text-primary hover:underline"
                  >
                    Add anyway
                  </button>
                ) : (
                  <>
                    <button
                      type="button"
                      onClick={() => resolveDupNotice("same")}
                      className="tech-transition text-[11px] font-medium text-primary hover:underline"
                    >
                      Same — remove it
                    </button>
                    <button
                      type="button"
                      onClick={() => resolveDupNotice("keep")}
                      className="tech-transition text-[11px] font-medium text-muted-foreground hover:underline"
                    >
                      Different — keep both
                    </button>
                  </>
                )}
                <Button variant="text" size="none" layout="inline-flex items-center" type="button" onClick={() => { setDupRenameValue(dupNotice.title); setDupRenaming(true); }} className="gap-1 text-[11px]">
                  <AppIcon name="Pencil" className="h-3 w-3" />
                  Rename existing
                </Button>
                <button
                  type="button"
                  aria-label="Dismiss"
                  onClick={() => resolveDupNotice("dismiss")}
                  className="tech-transition text-muted-foreground hover:text-foreground"
                >
                  <AppIcon name="X" className="h-3.5 w-3.5" />
                </button>
              </span>
            )}
          </div>
        </div>
      )}

      {/* Controls — ONE full-width wrap row: count/status chips · filter
          pills · search · sort · density · tickler · Clarify next. (Wraps to
          stacked lines on mobile by itself.) Selection swaps in the bulk bar. */}
      {(hasRows || tickler.length > 0) && (
        <div className="shrink-0 border-b border-border bg-background/80 backdrop-blur">
          {selectionActive && !showTickler ? (
            <div className="flex flex-wrap items-center gap-2 px-4 py-2">
              <span className="text-xs font-medium text-primary">
                {selectedIds.size} selected
              </span>
              <div className="ml-auto flex items-center gap-1">
                <BulkBtn icon={themedIcon("Lightbulb")} onClick={() => bulk("SOMEDAY")}>
                  Someday
                </BulkBtn>
                <BulkBtn icon={themedIcon("FileText")} onClick={() => bulk("REFERENCE")}>
                  Reference
                </BulkBtn>
                <BulkBtn icon={themedIcon(selectedMine.length ? "Trash2" : "UserX")} danger onClick={bulkRemove}>
                  {removeLabel}
                </BulkBtn>
                <Button variant="text" size="icon-xs" radius="keep" layout="" type="button" onClick={clearSelection} aria-label="Clear selection" className="rounded-md">
                  <AppIcon name="X" className="h-4 w-4" />
                </Button>
              </div>
            </div>
          ) : (
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1.5 px-4 py-2">
              {/* The same number as the sidebar badge (`inboxCount`). */}
              <span className="whitespace-nowrap text-xs font-medium text-muted-foreground">
                {allRows.length} to process
              </span>
              {processed > 0 && (
                <span className="hidden items-center gap-1 whitespace-nowrap text-[11px] text-success sm:inline-flex">
                  <AppIcon name="CheckCircle2" className="h-3 w-3" />
                  {processed} processed
                </span>
              )}
              {isAging && oldest && !showTickler && (
                <span className="inline-flex items-center gap-1 whitespace-nowrap rounded-full bg-warning/10 px-2 py-0.5 text-[10px] font-medium text-warning">
                  <AppIcon name="AlertCircle" className="h-3 w-3" />
                  oldest {relativeTime(oldest.createdAt)}
                </span>
              )}
              {!showTickler && (
                /* S6g — the source filter: both kinds, mine, or From
                   Projects. It scrolls sideways on a phone, never the page. */
                <FilterPills
                  items={sourcePills}
                  activeId={sourceFilter}
                  onChange={(id) => setSourceFilter(id as InboxSource)}
                  className="!min-w-0 max-w-full !shrink !border-0 !px-0 !py-0"
                />
              )}
              {!showTickler && (
                <FilterPills
                  items={pills}
                  activeId={dateFilter}
                  onChange={(id) => setDateFilter(id as DateFilter)}
                  className="!min-w-0 max-w-full !shrink !border-0 !px-0 !py-0"
                />
              )}
              {/* Search + view controls: full-width second line on mobile;
                  right-aligned same-line cluster from sm: up. */}
              <div className="flex w-full min-w-0 items-center gap-1.5 sm:ml-auto sm:w-auto sm:min-w-[320px] sm:flex-1 sm:justify-end">
                {!showTickler && (
                  <>
                    <div className="tech-transition flex min-w-0 max-w-sm flex-1 items-center gap-2 rounded-lg border border-border bg-card px-2.5 py-1.5 focus-within:border-primary/50">
                      <AppIcon name="Search" className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                      <input
                        value={search}
                        onChange={(e) => setSearch(e.target.value)}
                        placeholder="Search the inbox…"
                        aria-label="Search the inbox"
                        className="min-w-0 flex-1 bg-transparent text-base text-foreground placeholder:text-muted-foreground focus:outline-none sm:text-xs"
                      />
                    </div>
                    <button
                      type="button"
                      onClick={() =>
                        setSortOrder((o) => (o === "newest" ? "oldest" : "newest"))
                      }
                      className="tech-transition inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-lg border border-border px-2 py-1.5 text-xs text-muted-foreground hover:text-foreground"
                      title="Toggle sort order"
                    >
                      <AppIcon name="ArrowDownUp" className="h-3.5 w-3.5" />
                      <span className="hidden lg:inline">
                        {sortOrder === "newest" ? "Newest" : "Oldest"}
                      </span>
                    </button>
                    <button
                      type="button"
                      onClick={() =>
                        setDensityPersist(density === "cards" ? "list" : "cards")
                      }
                      title={density === "cards" ? "Dense list view" : "Card view"}
                      className="tech-transition inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded-lg border border-border px-2 py-1.5 text-xs text-muted-foreground hover:text-foreground"
                    >
                      {density === "cards" ? (
                        <AppIcon name="LayoutList" className="h-3.5 w-3.5" />
                      ) : (
                        <AppIcon name="LayoutGrid" className="h-3.5 w-3.5" />
                      )}
                      <span className="hidden lg:inline">
                        {density === "cards" ? "List" : "Cards"}
                      </span>
                    </button>
                  </>
                )}
                {tickler.length > 0 && (
                  <button
                    type="button"
                    onClick={() => setShowTickler((v) => !v)}
                    className={[
                      "tech-transition inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-md px-2 py-1.5 text-xs font-medium",
                      showTickler
                        ? "bg-primary/15 text-primary"
                        : "text-muted-foreground hover:bg-secondary hover:text-foreground",
                    ].join(" ")}
                  >
                    <AppIcon name="CalendarClock" className="h-3.5 w-3.5" />
                    <span className="hidden sm:inline">Tickler&nbsp;</span>
                    {tickler.length}
                  </button>
                )}
                {!showTickler && (
                  <button
                    type="button"
                    disabled={!oldest}
                    onClick={() => oldest && startClarify(oldest.id)}
                    title="Process the oldest item first (first in, first out)"
                    className="tech-transition inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-md bg-primary/10 px-2.5 py-1.5 text-xs font-medium text-primary hover:bg-primary/20 disabled:opacity-40"
                  >
                    <AppIcon name="Sparkles" className="h-3.5 w-3.5" />
                    Clarify next
                    <AppIcon name="ArrowRight" className="h-3.5 w-3.5" />
                  </button>
                )}
              </div>
            </div>
          )}
        </div>
      )}

      {/* List — full width, like Next Actions, so long captures read whole. */}
      <div className="flex-1 overflow-y-auto">
        <div className="w-full px-4 py-4 sm:py-3">
          {loading ? (
            <div className="flex flex-col items-center justify-center gap-2 py-16 text-center">
              <AppIcon name="Loader2" className="h-6 w-6 animate-spin text-muted-foreground/60" />
              <p className="text-xs text-muted-foreground">Loading your inbox…</p>
            </div>
          ) : showTickler ? (
            <TicklerList items={tickler} onUndefer={undeferItem} />
          ) : !hasRows ? (
            /* Inbox zero only when BOTH kinds are empty (S6g). */
            <div className="flex flex-col items-center justify-center gap-2 py-16 text-center">
              <AppIcon name="CheckCircle2" className="h-9 w-9 text-success/70" />
              <p className="text-sm font-medium text-foreground">
                Inbox zero. Mind like water.
              </p>
              <p className="text-xs text-muted-foreground">
                {processed > 0
                  ? `You processed ${processed} item${processed === 1 ? "" : "s"} this session. 🎉`
                  : "Nothing left to process. Capture the next thing above."}
              </p>
            </div>
          ) : visible.length === 0 ? (
            <div className="flex flex-col items-center justify-center gap-2 py-16 text-center">
              <AppIcon name="SearchX" className="h-8 w-8 text-muted-foreground/50" />
              <p className="text-sm text-muted-foreground">
                Nothing in the inbox matches this filter.
              </p>
            </div>
          ) : (
            <>
              {(search || dateFilter !== "all" || sourceFilter !== "all") && (
                <p className="mb-2 text-[11px] text-muted-foreground">
                  Showing {visible.length} of {allRows.length}
                </p>
              )}
              {density === "list" ? (
                <InboxTable
                  items={visible}
                  kindOf={kindOf}
                  onMove={(id) => setPromote({ id })}
                  cursorId={cursorId}
                  selectedIds={selectedIds}
                  onSelectToggle={toggleSelect}
                />
              ) : (
                <div className="flex flex-col gap-2">
                  {visible.map((item) => (
                    <InboxCard
                      key={item.id}
                      item={item}
                      kind={kindOf(item)}
                      onMove={() => setPromote({ id: item.id })}
                      cursor={cursorId === item.id}
                      selected={selectedIds.has(item.id)}
                      selectionMode={selectionActive}
                      editing={editingId === item.id}
                      onSelectToggle={(shift) => toggleSelect(item.id, shift)}
                      onEditStart={() => setEditingId(item.id)}
                      onEditEnd={() => setEditingId(null)}
                    />
                  ))}
                </div>
              )}
            </>
          )}
        </div>
      </div>

      {/* The one-level undo toast is now global (<UndoToast/> in page.tsx) so it
          shows in every view — the inbox no longer renders its own. */}

      <ClarifyModal />
      {/* S6g — the one promote dialog, for the card, the table, `m` and the
          capture door. Rendered outside every row: a dialog inside a row's
          clickable root opened the row on every click in it (S6c repair). */}
      {promote && promoteItem && (
        <PromoteDialog
          key={`${promote.id}:${promote.destination ?? ""}`}
          item={promoteItem}
          initialDestination={promote.destination ?? null}
          onClose={() => setPromote(null)}
        />
      )}
    </div>
  );
}

function TicklerList({
  items,
  onUndefer,
}: {
  items: MyTask[];
  onUndefer: (id: string) => void;
}) {
  if (!items.length) {
    return (
      <p className="py-16 text-center text-sm text-muted-foreground">
        Nothing tickled.
      </p>
    );
  }
  return (
    <>
      <p className="mb-3 text-[11px] text-muted-foreground">
        Deferred items — hidden from the inbox until they resurface.
      </p>
      <div className="flex flex-col gap-2">
        {items.map((item) => (
          <div
            key={item.id}
            className="flex items-center gap-3 rounded-xl border border-border bg-card px-4 py-3"
          >
            <AppIcon name="CalendarClock" className="h-4 w-4 shrink-0 text-primary/70" />
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm text-foreground">{item.title}</p>
              <p className="text-[11px] text-muted-foreground">
                resurfaces {relativeTime(resurfacesAt(item))}
              </p>
            </div>
            <button
              type="button"
              onClick={() => onUndefer(item.id)}
              className="tech-transition inline-flex items-center gap-1 rounded-md border border-border px-2 py-1 text-[11px] text-muted-foreground hover:text-foreground"
            >
              <AppIcon name="RotateCcw" className="h-3 w-3" />
              Un-snooze
            </button>
          </div>
        ))}
      </div>
    </>
  );
}

function BulkBtn({
  icon: Icon,
  onClick,
  danger,
  children,
}: {
  icon: ThemedIcon;
  onClick: () => void;
  danger?: boolean;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={[
        "tech-transition inline-flex items-center gap-1.5 whitespace-nowrap rounded-md border px-2.5 py-1 text-xs font-medium",
        danger
          ? "border-destructive/30 text-destructive hover:bg-destructive/10"
          : "border-border text-muted-foreground hover:bg-secondary hover:text-foreground",
      ].join(" ")}
    >
      <Icon className="h-3.5 w-3.5" />
      {children}
    </button>
  );
}

function Sc({ k, children }: { k: string; children: React.ReactNode }) {
  return (
    <span className="inline-flex items-center gap-1">
      <kbd className="rounded border border-border px-1 py-0.5 font-mono text-[9px] text-foreground">
        {k}
      </kbd>
      {children}
    </span>
  );
}
