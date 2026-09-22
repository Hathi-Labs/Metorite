"use client";

import Button from "@/components/ui/Button";
import AppIcon, { themedIcon } from "@/components/Icon";
import { useState, useRef, useEffect, useCallback } from "react";
import { Email } from "../lib/types";
import { timeLabel } from "../lib/utils";
import { useEmailStore, isRealFolder } from "../lib/emailStore";
import { LabelChip, ColorSwatch, LabelColorGrid } from "./LabelChip";
import { presetForLabel } from "../lib/labelColors";
import { FixDialog } from "./automation/ai-settings/fixDialog";
import { useViewMode } from "@/components/ViewModeProvider";

interface EmailListProps {
  emails: Email[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  onCompose: () => void;
  onToolbarAction: (action: string, email: Email | null) => void;
  loading?: boolean;
  total?: number;
  onLoadMore?: () => void;
  loadingMore?: boolean;
  onBackfill?: () => void;
  backfilling?: boolean;
  canBackfill?: boolean;
}

// Per-message actions, shown only when a single email is open. Ordered
// respond → dispose → organize. "move"/"label" open the context menu (folder &
// label pickers); everything else routes through onToolbarAction.
const TOOLBAR_ACTIONS = [
  { icon: themedIcon("Reply"), label: "Reply", key: "reply" },
  { icon: themedIcon("ReplyAll"), label: "Reply All", key: "reply-all" },
  { icon: themedIcon("Forward"), label: "Forward", key: "forward" },
  { icon: themedIcon("Archive"), label: "Archive", key: "archive" },
  { icon: themedIcon("Trash2"), label: "Delete", key: "delete" },
  { icon: themedIcon("FolderInput"), label: "Move", key: "move" },
  { icon: themedIcon("MailOpen"), label: "Mark as Read", key: "mark-read" },
  { icon: themedIcon("Flag"), label: "Flag", key: "flag" },
  { icon: themedIcon("Tag"), label: "Label", key: "label" },
];

// Render a search highlight (ts_headline output) safely: the server wraps the
// matched terms in <mark>…</mark> but does NOT escape the surrounding email text,
// so we escape everything first, then re-allow ONLY the mark tags. Prevents any
// HTML in the email body from injecting into the list.
function renderHighlight(hl: string): { __html: string } {
  const escaped = hl
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
  const withMarks = escaped
    .replace(/&lt;mark&gt;/g, '<mark class="bg-primary/25 text-foreground rounded-sm px-0.5">')
    .replace(/&lt;\/mark&gt;/g, "</mark>");
  return { __html: withMarks };
}

// Snooze-until presets, computed at click time. "Later today" is +3h; the rest
// anchor to sensible clock times so "tomorrow" means tomorrow morning, not +24h.
function snoozePresets(): { label: string; until: string }[] {
  const now = new Date();
  const at = (d: Date, h: number) => {
    const x = new Date(d);
    x.setHours(h, 0, 0, 0);
    return x;
  };
  const laterToday = new Date(now.getTime() + 3 * 60 * 60 * 1000);
  const tomorrow = new Date(now);
  tomorrow.setDate(tomorrow.getDate() + 1);
  const tomorrowAm = at(tomorrow, 8);
  // Next Saturday 8am (if today is Sat/Sun, the coming/!today Saturday).
  const weekend = new Date(now);
  const daysToSat = (6 - weekend.getDay() + 7) % 7 || 7;
  weekend.setDate(weekend.getDate() + daysToSat);
  const weekendAm = at(weekend, 8);
  // Next Monday 8am.
  const nextWeek = new Date(now);
  const daysToMon = (1 - nextWeek.getDay() + 7) % 7 || 7;
  nextWeek.setDate(nextWeek.getDate() + daysToMon);
  const nextWeekAm = at(nextWeek, 8);
  return [
    { label: "Later today", until: laterToday.toISOString() },
    { label: "Tomorrow", until: tomorrowAm.toISOString() },
    { label: "This weekend", until: weekendAm.toISOString() },
    { label: "Next week", until: nextWeekAm.toISOString() },
  ];
}

interface CtxState {
  x: number;
  y: number;
  email: Email;
  /** True when opened on a multi-selection — show bulk actions instead. */
  bulk?: boolean;
  /** How many emails the bulk action will affect. */
  count?: number;
}

export function EmailList({
  emails,
  selectedId,
  onSelect,
  onCompose,
  onToolbarAction,
  loading = false,
  total,
  onLoadMore,
  loadingMore = false,
  onBackfill,
  backfilling = false,
  canBackfill = false,
}: EmailListProps) {
  const selectedEmail = emails.find((e) => e.id === selectedId) || null;
  const {
    updateEmail, deleteEmail, folders, selectedFolder,
    selectedLabel, selectLabel,
    triggerSync, selectedAccountId, syncStatus,
    availableLabels, applyLabel, applyLabelBulk, clearCategories,
    selectedIds, toggleEmailSelected, setSelectedEmails, clearEmailSelection,
    bulkUpdateSelected, bulkDeleteSelected, captureEmailToTasks,
    runTestOnMessage, testRunningIds, snoozeEmail,
  } = useEmailStore();
  const { isMobile } = useViewMode();
  // Treat the post-sync "processing" window (background rules/labels pipeline)
  // as busy too, so the pull-to-refresh spinner keeps turning until it settles.
  const syncing = selectedAccountId
    ? syncStatus[selectedAccountId] === "syncing" ||
      syncStatus[selectedAccountId] === "processing"
    : false;
  const [ctx, setCtx] = useState<CtxState | null>(null);
  // "Fix category…" from the row context menu — the same Improve-Rules dialog
  // the AI-Settings Test/History tabs use, reachable from anywhere mail shows.
  const [fixTarget, setFixTarget] = useState<Email | null>(null);

  // Uncategorized pill click → attempt recategorization, then branch on WHY it
  // failed: "no rule matched" from a healthy classifier is a rules gap, so the
  // Fix dialog opens and the miss becomes a learned pattern or a new rule. An
  // unavailable classifier is a backend fault — the store already put it in the
  // error banner, and offering to "fix the rules" for it would be lying about
  // where the problem is. A transport error (null) likewise stays a banner.
  const recategorize = async (email: Email) => {
    if (!selectedAccountId || testRunningIds.includes(email.id)) return;
    const res = await runTestOnMessage(selectedAccountId, email.id, false);
    if (res && !res.matched && !res.unavailable) setFixTarget(email);
  };

  // ── Bulk selection ──
  // Held in the store so the desktop unified toolbar shares it; aliased to the
  // local names the rows / select-all / context-menu code below already use.
  const selected = selectedIds;
  const toggleOne = toggleEmailSelected;
  const allSelected = emails.length > 0 && emails.every((e) => selected.has(e.id));
  const someSelected = selected.size > 0 && !allSelected;
  const toggleAll = () =>
    allSelected ? clearEmailSelection() : setSelectedEmails(emails.map((e) => e.id));
  const clearSelection = clearEmailSelection;
  const bulkUpdate = bulkUpdateSelected;
  const bulkDelete = bulkDeleteSelected;

  const openContextAt = (px: number, py: number, email: Email) => {
    // Clamp so the menu stays on-screen.
    const menuW = 210;
    const menuH = 380;
    const x = Math.min(px, window.innerWidth - menuW);
    const y = Math.min(py, window.innerHeight - menuH);
    // Right-clicking a row that's part of a multi-selection acts on the whole
    // selection (Windows/Outlook behaviour); otherwise it's a single-email menu.
    const bulk = selected.has(email.id) && selected.size > 1;
    setCtx({
      x: Math.max(8, x),
      y: Math.max(8, y),
      email,
      bulk,
      count: bulk ? selected.size : 1,
    });
  };
  const openContext = (e: React.MouseEvent, email: Email) => {
    e.preventDefault();
    openContextAt(e.clientX, e.clientY, email);
  };

  // Long-press → context menu on touch devices (mirrors right-click).
  const lpTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lpFired = useRef(false);
  const startLongPress = (e: React.TouchEvent, email: Email) => {
    const t = e.touches[0];
    lpFired.current = false;
    const { clientX, clientY } = t;
    lpTimer.current = setTimeout(() => {
      lpFired.current = true;
      openContextAt(clientX, clientY, email);
    }, 500);
  };
  const cancelLongPress = () => {
    if (lpTimer.current) {
      clearTimeout(lpTimer.current);
      lpTimer.current = null;
    }
  };

  const hasMore = total !== undefined && emails.length < total;
  const canPageProvider = !hasMore && canBackfill && !!onBackfill;

  // ── Auto-load on scroll ──
  // The sentinel auto-pages what's already synced (DB) — fast and reliable.
  // Pulling OLDER mail from the provider (backfill) is slower and can fail, so
  // it's a manual button click (handleManualLoad) to avoid an auto-retry loop
  // when a backfill yields nothing new.
  const scrollRef = useRef<HTMLDivElement>(null);
  const sentinelRef = useRef<HTMLDivElement>(null);

  const handleAutoLoad = useCallback(() => {
    if (loadingMore || backfilling) return;
    if (hasMore) onLoadMore?.();
  }, [loadingMore, backfilling, hasMore, onLoadMore]);

  // ── Pull-to-refresh (touch/mobile) ──
  // Pulling down at the top of the list past a threshold triggers a sync.
  const PULL_THRESHOLD = 70;
  const pullStartRef = useRef<number | null>(null);
  const [pullY, setPullY] = useState(0);
  const onPullStart = (e: React.TouchEvent) => {
    pullStartRef.current =
      scrollRef.current && scrollRef.current.scrollTop <= 0
        ? e.touches[0].clientY
        : null;
  };
  const onPullMove = (e: React.TouchEvent) => {
    if (pullStartRef.current == null || syncing) return;
    const dy = e.touches[0].clientY - pullStartRef.current;
    // Only react to a downward pull while still at the top.
    if (dy > 0 && (scrollRef.current?.scrollTop ?? 0) <= 0) {
      setPullY(Math.min(dy * 0.5, 90)); // damped
    } else {
      setPullY(0);
    }
  };
  const onPullEnd = () => {
    if (
      pullStartRef.current != null &&
      pullY >= PULL_THRESHOLD &&
      selectedAccountId &&
      !syncing
    ) {
      triggerSync(selectedAccountId);
    }
    pullStartRef.current = null;
    setPullY(0);
  };

  // Button click: page the DB if there's more, otherwise fetch older mail from
  // the provider.
  const handleManualLoad = useCallback(() => {
    if (loadingMore || backfilling) return;
    if (hasMore) onLoadMore?.();
    else if (canPageProvider) onBackfill?.();
  }, [loadingMore, backfilling, hasMore, canPageProvider, onLoadMore, onBackfill]);

  useEffect(() => {
    const sentinel = sentinelRef.current;
    if (!sentinel) return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0]?.isIntersecting) handleAutoLoad();
      },
      { root: scrollRef.current, rootMargin: "300px" }
    );
    observer.observe(sentinel);
    return () => observer.disconnect();
  }, [handleAutoLoad]);

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Contextual toolbar row — MOBILE ONLY. On desktop the single
          EmailToolbar below the page top bar (spanning both columns) provides
          these actions instead.
          • multi-select   → bulk-action bar
          • one email open → New + per-message actions
          • nothing open   → New + message count */}
      {isMobile && (selected.size > 0 ? (
        <div className="flex items-center gap-0.5 px-2 py-1.5 border-b border-border flex-shrink-0 bg-primary/10 overflow-x-auto scrollbar-hide">
          <span className="text-[10px] font-medium text-foreground px-1">
            {selected.size} selected
          </span>
          <div className="flex-1" />
          <ToolbarBtn icon={themedIcon("MailOpen")} label="Mark read" onClick={() => bulkUpdate({ isRead: true })} />
          <ToolbarBtn icon={themedIcon("Mail")} label="Mark unread" onClick={() => bulkUpdate({ isRead: false })} />
          <ToolbarBtn icon={themedIcon("Flag")} label="Flag" onClick={() => bulkUpdate({ isFlagged: true })} />
          <ToolbarBtn icon={themedIcon("Archive")} label="Archive" onClick={() => bulkUpdate({ folder: "archive" })} />
          <ToolbarBtn icon={themedIcon("Trash2")} label="Delete" onClick={bulkDelete} />
          <Button variant="ghost" size="icon-sm" radius="keep" layout="" onClick={clearSelection} title="Clear selection" className="rounded">
            <AppIcon name="X" size={13} />
          </Button>
        </div>
      ) : (
        <div className="flex items-center gap-0.5 px-2 py-1.5 border-b border-border flex-shrink-0 overflow-x-auto scrollbar-hide">
          {/* Compose — always available, even with nothing selected */}
          <Button size="none" radius="keep" layout="flex items-center" title="New Email" onClick={onCompose} className="gap-1 px-2.5 py-1.5 rounded-md flex-shrink-0">
            <AppIcon name="Pencil" size={12} />
            <span className="text-[10px] font-medium">New</span>
          </Button>

          {selectedEmail ? (
            <>
              {/* Per-message actions — only when an email is open. */}
              <div className="w-px h-4 bg-border mx-1 flex-shrink-0" />
              {TOOLBAR_ACTIONS.map(({ icon: Icon, label, key }) => (
                <ToolbarBtn
                  key={key}
                  icon={Icon}
                  label={label}
                  onClick={(e) => {
                    // "Move"/"Label" open the context menu (folder & label
                    // pickers) anchored at the click; the rest route through
                    // onToolbarAction.
                    if (key === "move" || key === "label") {
                      setCtx({ x: e.clientX, y: e.clientY + 12, email: selectedEmail });
                    } else {
                      onToolbarAction(key, selectedEmail);
                    }
                  }}
                />
              ))}
              <div className="flex-1" />
            </>
          ) : (
            <>
              <div className="flex-1" />
              <span className="text-[10px] text-muted-foreground pr-1 whitespace-nowrap flex-shrink-0">
                {emails.length}
                {total !== undefined && total > emails.length ? ` of ${total}` : ""} msgs
              </span>
            </>
          )}
        </div>
      ))}

      {/* Active label filter */}
      {selectedLabel && (
        <div className="flex items-center gap-1.5 px-2 py-1 border-b border-border flex-shrink-0 bg-primary/5 text-[11px]">
          <AppIcon name="Tag" size={11} className="text-primary flex-shrink-0" />
          <span className="text-foreground/70">
            Filtered by label <b className="text-primary">{selectedLabel}</b>
          </span>
          <Button variant="text" size="none" layout="flex items-center" onClick={() => selectLabel(null)} title="Clear label filter" className="ml-auto gap-0.5">
            <AppIcon name="X" size={11} /> Clear
          </Button>
        </div>
      )}

      {/* Persistent select-all header — aligned with the per-row checkboxes. */}
      {emails.length > 0 && (
        <div className="flex items-center border-b border-border flex-shrink-0 bg-card/60">
          <button
            onClick={toggleAll}
            title={allSelected ? "Deselect all" : "Select all"}
            className="flex items-center pl-2 pr-1 py-1.5 flex-shrink-0"
          >
            <CheckboxSquare checked={allSelected} indeterminate={someSelected} />
          </button>
          <span className="text-[10px] text-muted-foreground select-none">
            {selected.size > 0 ? `${selected.size} selected` : "Select all"}
          </span>
        </div>
      )}

      {/* Email rows */}
      <div
        ref={scrollRef}
        className="flex-1 overflow-y-auto scrollbar-hide"
        onTouchStart={onPullStart}
        onTouchMove={onPullMove}
        onTouchEnd={onPullEnd}
        onTouchCancel={onPullEnd}
      >
        {/* Pull-to-refresh indicator (mobile) */}
        {(pullY > 0 || syncing) && (
          <div
            className="flex items-center justify-center text-muted-foreground overflow-hidden transition-[height]"
            style={{ height: syncing ? 36 : pullY }}
          >
            <AppIcon name="RefreshCw"
              size={16}
              className={syncing ? "animate-spin" : ""}
              style={syncing ? undefined : { transform: `rotate(${pullY * 3}deg)` }}
            />
          </div>
        )}
        {loading ? (
          <div className="flex flex-col items-center justify-center h-full text-muted-foreground gap-2">
            <div className="w-5 h-5 border-2 border-primary border-t-transparent rounded-full animate-spin" />
            <p className="text-xs">Loading emails...</p>
          </div>
        ) : emails.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full text-muted-foreground gap-2">
            <AppIcon name="MailOpen" size={24} className="opacity-40" />
            <p className="text-xs">No emails to show</p>
          </div>
        ) : (
          <>
            {emails.map((email) => {
              const isSel = selected.has(email.id);
              return (
              <div
                key={email.id}
                className={`group flex items-stretch border-b border-border ${
                  isSel ? "bg-primary/5" : ""
                }`}
              >
                {/* Selection checkbox — always present so it's clickable; the
                    empty square darkens on row hover. */}
                <button
                  onClick={() => toggleOne(email.id)}
                  title={isSel ? "Deselect" : "Select"}
                  className="flex items-center pl-2 pr-1 flex-shrink-0"
                >
                  <CheckboxSquare checked={isSel} hoverParent />
                </button>
              <button
                onClick={() => {
                  // Suppress the tap-click that follows a long-press.
                  if (lpFired.current) {
                    lpFired.current = false;
                    return;
                  }
                  onSelect(email.id);
                }}
                onContextMenu={(e) => openContext(e, email)}
                onTouchStart={(e) => startLongPress(e, email)}
                onTouchMove={cancelLongPress}
                onTouchEnd={cancelLongPress}
                onTouchCancel={cancelLongPress}
                className={`flex-1 min-w-0 text-left pr-3 pl-1 py-3 transition-colors flex flex-col gap-1 select-none ${
                  selectedId === email.id
                    ? "bg-primary/10 border-l-2 border-l-primary"
                    : "hover:bg-secondary/50"
                }`}
              >
                {/* Sender + indicators + time */}
                <div className="flex items-start justify-between gap-2">
                  <div className="flex items-center gap-1.5 min-w-0">
                    {!email.isRead && (
                      <div className="w-1.5 h-1.5 rounded-full bg-primary flex-shrink-0 mt-0.5" />
                    )}
                    <span
                      className={`text-xs truncate ${
                        email.isRead ? "text-foreground/70" : "text-foreground font-medium"
                      }`}
                    >
                      {email.from.name}
                    </span>
                  </div>
                  <div className="flex items-center gap-1.5 flex-shrink-0">
                    {(email.threadCount ?? 1) > 1 && (
                      <span
                        className="inline-flex items-center gap-0.5 text-[9px] px-1 py-0.5 rounded-full bg-secondary text-muted-foreground"
                        title={`${email.threadCount} messages in this conversation`}
                      >
                        <AppIcon name="MessagesSquare" size={9} />
                        {email.threadCount}
                      </span>
                    )}
                    {email.importance === "high" && (
                      <AppIcon name="AlertTriangle" size={10} className="text-red-400" />
                    )}
                    {email.hasAttachments && (
                      <AppIcon name="Paperclip" size={10} className="text-muted-foreground" />
                    )}
                    {email.isFlagged && (
                      <AppIcon name="Flag" size={10} className="text-amber-400 fill-amber-400" />
                    )}
                    {email.isStarred && (
                      <AppIcon name="Star" size={10} className="text-amber-400 fill-amber-400" />
                    )}
                    <span className="text-[10px] text-muted-foreground whitespace-nowrap">
                      {timeLabel(email.receivedAt)}
                    </span>
                  </div>
                </div>

                {/* Subject */}
                <div
                  className={`text-xs truncate ${
                    email.isRead ? "text-foreground/60" : "text-foreground"
                  }`}
                >
                  {email.subject}
                </div>

                {/* Preview — a search hit shows the highlighted match snippet
                    (why it matched); otherwise the plain snippet. */}
                {email.highlight ? (
                  <div
                    className="text-[11px] text-muted-foreground truncate leading-relaxed"
                    dangerouslySetInnerHTML={renderHighlight(email.highlight)}
                  />
                ) : (
                  <div className="text-[11px] text-muted-foreground truncate leading-relaxed">
                    {email.snippet}
                  </div>
                )}

                {/* Categories / user labels — click to filter the list.
                    When an email has NO category (rules haven't run or the
                    agent couldn't categorize it), show an "Uncategorized" pill
                    so it's visible as needing attention — clicking it reruns
                    the rules on just this email; a no-match opens Fix. */}
                {email.categories.length > 0 ? (
                  <div className="flex flex-wrap gap-1 mt-0.5">
                    {email.categories.slice(0, 3).map((label) => (
                      <LabelChip
                        key={label}
                        name={label}
                        active={selectedLabel === label}
                        title={`Filter by “${label}”`}
                        onClick={(e) => {
                          e.stopPropagation();
                          e.preventDefault();
                          selectLabel(label);
                        }}
                      />
                    ))}
                  </div>
                ) : (
                  <div className="flex flex-wrap gap-1 mt-0.5">
                    <span
                      role="button"
                      tabIndex={0}
                      title={
                        testRunningIds.includes(email.id)
                          ? "Re-running rules…"
                          : "Uncategorized — click to re-run rules; if none match, you can fix the rules"
                      }
                      onClick={(e) => {
                        e.stopPropagation();
                        e.preventDefault();
                        recategorize(email);
                      }}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" || e.key === " ") {
                          e.preventDefault();
                          recategorize(email);
                        }
                      }}
                      className="inline-flex items-center gap-1 rounded-full font-medium text-[11px] px-2 py-0.5 border border-dashed border-border text-muted-foreground hover:bg-secondary/60 cursor-pointer transition-colors"
                    >
                      {testRunningIds.includes(email.id) ? "Re-running…" : "Uncategorized"}
                    </span>
                  </div>
                )}
              </button>
              </div>
              );
            })}

            {/* Auto-load sentinel: pages from the DB, then backfills from the
                provider. Tapping it also works if auto-load hasn't fired. */}
            {(hasMore || canPageProvider) && (
              <div ref={sentinelRef} className="p-2">
                <Button variant="ghost" size="none" radius="keep" layout="flex items-center justify-center" onClick={handleManualLoad} disabled={loadingMore || backfilling} className="w-full gap-1.5 py-2 rounded-md text-xs">
                  {(loadingMore || backfilling) && (
                    <AppIcon name="Loader2" size={12} className="animate-spin" />
                  )}
                  {backfilling
                    ? "Fetching older messages…"
                    : loadingMore
                      ? "Loading…"
                      : hasMore
                        ? `Load more (${emails.length} of ${total})`
                        : "Load older messages from server"}
                </Button>
              </div>
            )}
          </>
        )}
      </div>

      {/* Right-click context menu */}
      {ctx && (
        <ContextMenu
          ctx={ctx}
          folders={folders}
          availableLabels={availableLabels}
          appliedCategories={ctx.bulk ? new Set() : new Set(ctx.email.categories)}
          onApplyLabel={(name, add) =>
            ctx.bulk
              ? applyLabelBulk([...selected], name, add)
              : applyLabel(ctx.email.id, name, add)
          }
          onClearCategories={() =>
            clearCategories(ctx.bulk ? [...selected] : [ctx.email.id])
          }
          onClose={() => setCtx(null)}
          snoozedView={selectedFolder === "snoozed"}
          onSnooze={(until) =>
            (ctx.bulk ? [...selected] : [ctx.email.id]).forEach((id) =>
              snoozeEmail(id, until),
            )
          }
          onReply={(k) => onToolbarAction(k, ctx.email)}
          onAddToTasks={() =>
            (ctx.bulk ? [...selected] : [ctx.email.id]).forEach((id) =>
              captureEmailToTasks(id),
            )
          }
          onUpdate={(u) => updateEmail(ctx.email.id, u)}
          onDelete={() => deleteEmail(ctx.email.id)}
          onBulkUpdate={(u) => bulkUpdate(u)}
          onBulkDelete={bulkDelete}
          onFix={() => setFixTarget(ctx.email)}
        />
      )}

      {fixTarget && (
        <FixDialog
          accountId={fixTarget.accountId}
          email={{
            subject: fixTarget.subject || "",
            from: fixTarget.from?.email || "",
          }}
          current={{
            matched: (fixTarget.categories?.length ?? 0) > 0,
            ruleName: fixTarget.categories?.[0] ?? null,
          }}
          messageId={fixTarget.id}
          onReran={() => useEmailStore.getState().fetchEmails()}
          onClose={() => setFixTarget(null)}
        />
      )}
    </div>
  );
}

function ContextMenu({
  ctx,
  folders,
  availableLabels,
  appliedCategories,
  onApplyLabel,
  onClearCategories,
  onClose,
  snoozedView,
  onSnooze,
  onReply,
  onAddToTasks,
  onUpdate,
  onDelete,
  onBulkUpdate,
  onBulkDelete,
  onFix,
}: {
  ctx: CtxState;
  folders: { key: string; label: string }[];
  availableLabels: string[];
  appliedCategories: Set<string>;
  onApplyLabel: (name: string, add: boolean) => void;
  onClearCategories: () => void;
  onClose: () => void;
  /** True in the Snoozed view — offer "Unsnooze" instead of the presets. */
  snoozedView: boolean;
  onSnooze: (until: string | null) => void;
  onReply: (action: string) => void;
  onAddToTasks: () => void;
  onUpdate: (
    updates: Partial<Pick<Email, "isRead" | "isStarred" | "isFlagged" | "folder">>
  ) => void;
  onDelete: () => void;
  onBulkUpdate: (
    updates: Partial<Pick<Email, "isRead" | "isStarred" | "isFlagged" | "folder">>
  ) => void;
  onBulkDelete: () => void;
  onFix: () => void;
}) {
  const { email, bulk, count = 1 } = ctx;
  const hasCategories = bulk || (email.categories?.length ?? 0) > 0;
  const run = (fn: () => void) => {
    fn();
    onClose();
  };
  // Bulk actions fan out across the whole selection; single uses the one email.
  const update = bulk ? onBulkUpdate : onUpdate;
  const del = bulk ? onBulkDelete : onDelete;
  const moveTargets = folders.filter(
    (f) => isRealFolder(f.key) && (bulk || f.key !== email.folder)
  );
  // Open submenu flyouts to the LEFT when the menu sits near the right edge.
  const flipLeft =
    typeof window !== "undefined" && ctx.x > window.innerWidth - 460;

  return (
    <>
      {/* Outside-click / right-click catcher */}
      <div
        className="fixed inset-0 z-[80]"
        onClick={onClose}
        onContextMenu={(e) => {
          e.preventDefault();
          onClose();
        }}
      />
      <div
        className="fixed z-[81] w-52 bg-popover border border-border rounded-lg shadow-xl py-1 text-xs"
        style={{ left: ctx.x, top: ctx.y }}
      >
        {bulk ? (
          <div className="px-3 py-1.5 text-[10px] uppercase tracking-wide text-muted-foreground border-b border-border mb-1">
            {count} selected
          </div>
        ) : (
          <>
            <CtxItem icon={themedIcon("Reply")} label="Reply" onClick={() => run(() => onReply("reply"))} />
            <CtxItem icon={themedIcon("ReplyAll")} label="Reply All" onClick={() => run(() => onReply("reply-all"))} />
            <CtxItem icon={themedIcon("Forward")} label="Forward" onClick={() => run(() => onReply("forward"))} />
            <CtxItem
              icon={themedIcon("MessageCircle")}
              label="Fix category…"
              onClick={() => run(onFix)}
            />
            <CtxDivider />
          </>
        )}

        <CtxItem
          icon={themedIcon("ListChecks")}
          label={bulk ? `Add ${count} to My Tasks` : "Add to My Tasks"}
          onClick={() => run(onAddToTasks)}
        />
        {snoozedView ? (
          <CtxItem
            icon={themedIcon("AlarmClockOff")}
            label={bulk ? `Unsnooze ${count}` : "Unsnooze"}
            onClick={() => run(() => onSnooze(null))}
          />
        ) : (
          <CtxSubmenu icon={themedIcon("Clock")} label="Snooze until…" flipLeft={flipLeft}>
            {snoozePresets().map((p) => (
              <button
                key={p.label}
                className="w-full flex items-center justify-between gap-3 px-3 py-1.5 text-foreground/80 hover:text-foreground hover:bg-secondary transition-colors"
                onClick={() => run(() => onSnooze(p.until))}
              >
                <span>{p.label}</span>
                <span className="text-[10px] text-muted-foreground">
                  {new Date(p.until).toLocaleString(undefined, {
                    weekday: "short",
                    hour: "numeric",
                    minute: "2-digit",
                  })}
                </span>
              </button>
            ))}
          </CtxSubmenu>
        )}
        <CtxDivider />
        <CtxItem
          icon={themedIcon("MailOpen")}
          label="Mark as read"
          onClick={() => run(() => update({ isRead: true }))}
        />
        <CtxItem
          icon={themedIcon("Mail")}
          label="Mark as unread"
          onClick={() => run(() => update({ isRead: false }))}
        />
        <CtxItem
          icon={themedIcon("Flag")}
          label={bulk ? "Flag" : email.isFlagged ? "Clear flag" : "Flag / mark important"}
          onClick={() => run(() => update({ isFlagged: bulk ? true : !email.isFlagged }))}
        />
        <CtxItem
          icon={themedIcon("Star")}
          label={bulk ? "Add star" : email.isStarred ? "Remove star" : "Add star"}
          onClick={() => run(() => update({ isStarred: bulk ? true : !email.isStarred }))}
        />

        <CtxDivider />

        {/* Move to → (Windows-style flyout submenu) */}
        <CtxSubmenu icon={themedIcon("FolderInput")} label="Move to…" flipLeft={flipLeft}>
          {moveTargets.length === 0 ? (
            <div className="px-3 py-1.5 text-muted-foreground">No other folders</div>
          ) : (
            moveTargets.map((f) => (
              <button
                key={f.key}
                className="w-full text-left px-3 py-1.5 text-foreground/80 hover:text-foreground hover:bg-secondary transition-colors"
                onClick={() => run(() => update({ folder: f.key }))}
              >
                {f.label}
              </button>
            ))
          )}
        </CtxSubmenu>

        {/* Categories → add (single + bulk), with checkmarks for the single case */}
        <CtxSubmenu
          icon={themedIcon("Tag")}
          label={bulk ? "Add category…" : "Categories…"}
          flipLeft={flipLeft}
          bare
        >
          <div onClick={(e) => e.stopPropagation()}>
            <CtxLabelMenu
              availableLabels={availableLabels}
              applied={appliedCategories}
              onApply={onApplyLabel}
            />
          </div>
        </CtxSubmenu>
        {/* Clear all categories on the target (single or selection) */}
        {hasCategories && (
          <CtxItem
            icon={themedIcon("Tag")}
            label={bulk ? `Clear categories on ${count}` : "Clear categories"}
            onClick={() => run(onClearCategories)}
          />
        )}

        <CtxItem
          icon={themedIcon("Archive")}
          label="Archive"
          onClick={() => run(() => update({ folder: "archive" }))}
        />

        <CtxDivider />

        <CtxItem
          icon={themedIcon("Trash2")}
          label={bulk ? `Delete ${count}` : "Delete"}
          danger
          onClick={() => run(del)}
        />
      </div>
    </>
  );
}

/** A context-menu item that reveals a flyout submenu to the side on hover. */
function CtxSubmenu({
  icon: Icon,
  label,
  flipLeft,
  bare = false,
  children,
}: {
  icon: React.ElementType;
  label: string;
  flipLeft?: boolean;
  /** When the child manages its own scrolling (e.g. LabelMenu), skip the
   *  flyout's own max-height/scroll so there's no scrollbar-in-a-scrollbar. */
  bare?: boolean;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div
      className="relative"
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
    >
      <button
        className="w-full flex items-center justify-between gap-2 px-3 py-1.5 text-foreground/80 hover:text-foreground hover:bg-secondary transition-colors"
        onClick={() => setOpen((v) => !v)}
      >
        <span className="flex items-center gap-2">
          <Icon size={13} /> {label}
        </span>
        <AppIcon name="ChevronRight" size={12} className={flipLeft ? "rotate-180" : ""} />
      </button>
      {open && (
        <div
          className={`absolute top-0 ${
            flipLeft ? "right-full mr-0.5" : "left-full ml-0.5"
          } w-52 ${
            bare ? "" : "max-h-72 overflow-y-auto"
          } bg-popover border border-border rounded-lg shadow-xl py-1 z-[82]`}
        >
          {children}
        </div>
      )}
    </div>
  );
}

/** Category picker used in the right-click menu (single + bulk). Shows a check
 *  for already-applied categories (single), adds on click, and supports creating
 *  a new category. */
function CtxLabelMenu({
  availableLabels,
  applied,
  onApply,
}: {
  availableLabels: string[];
  applied: Set<string>;
  onApply: (name: string, add: boolean) => void;
}) {
  const { labelColors, setLabelColor } = useEmailStore();
  const [newLabel, setNewLabel] = useState("");
  const [openColorFor, setOpenColorFor] = useState<string | null>(null);
  const create = () => {
    const name = newLabel.trim();
    if (!name) return;
    onApply(name, true);
    setNewLabel("");
  };
  return (
    <div className="text-xs">
      <div className="px-3 py-1 text-[10px] uppercase tracking-wide text-muted-foreground">
        Categories
      </div>
      <div className="max-h-48 overflow-y-auto">
        {availableLabels.length === 0 ? (
          <div className="px-3 py-1.5 text-muted-foreground">No categories yet</div>
        ) : (
          availableLabels.map((name) => {
            const on = applied.has(name);
            const open = openColorFor === name;
            return (
              <div key={name}>
                <div className="w-full flex items-center gap-2 px-3 py-1.5 text-foreground/80 hover:bg-secondary transition-colors">
                  <button
                    onClick={() => onApply(name, !on)}
                    className="flex items-center gap-2 flex-1 min-w-0 text-left hover:text-foreground"
                  >
                    <CheckboxSquare checked={on} />
                    <span className="truncate">{name}</span>
                  </button>
                  <ColorSwatch
                    name={name}
                    title={open ? "Close colours" : "Set colour"}
                    onClick={() => setOpenColorFor(open ? null : name)}
                  />
                </div>
                {open && (
                  <div className="px-3 pb-1.5">
                    <LabelColorGrid
                      value={presetForLabel(name, labelColors)}
                      onPick={(c) => {
                        setLabelColor(name, c);
                        setOpenColorFor(null);
                      }}
                    />
                  </div>
                )}
              </div>
            );
          })
        )}
      </div>
      <div className="border-t border-border mt-1 pt-1 px-2 pb-1">
        <div className="flex items-center gap-1">
          <input
            value={newLabel}
            onChange={(e) => setNewLabel(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                create();
              }
            }}
            placeholder="New category…"
            className="flex-1 bg-transparent text-xs text-foreground placeholder:text-muted-foreground outline-none px-1 py-1"
          />
          <button
            onClick={create}
            disabled={!newLabel.trim()}
            title="Add category"
            className="text-primary hover:opacity-80 disabled:opacity-40"
          >
            <AppIcon name="Plus" size={13} />
          </button>
        </div>
      </div>
    </div>
  );
}

/** Shared checkbox square: checked (filled), indeterminate (dash), or empty.
 *  `hoverParent` darkens the empty border when an ancestor `.group` is hovered. */
function CheckboxSquare({
  checked,
  indeterminate = false,
  hoverParent = false,
}: {
  checked: boolean;
  indeterminate?: boolean;
  hoverParent?: boolean;
}) {
  return (
    <span
      className={`w-4 h-4 rounded border flex items-center justify-center flex-shrink-0 transition-colors ${
        checked
          ? "bg-primary border-primary text-primary-foreground"
          : indeterminate
            ? "bg-primary/30 border-primary text-primary"
            : `border-muted-foreground/40${
                hoverParent ? " group-hover:border-muted-foreground/80" : ""
              }`
      }`}
    >
      {checked ? <AppIcon name="Check" size={11} /> : indeterminate ? <AppIcon name="Minus" size={11} /> : null}
    </span>
  );
}

function CtxItem({
  icon: Icon,
  label,
  onClick,
  danger,
}: {
  icon: React.ElementType;
  label: string;
  onClick: () => void;
  danger?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      className={`w-full flex items-center gap-2 px-3 py-1.5 transition-colors hover:bg-secondary ${
        danger
          ? "text-red-500 hover:text-red-400"
          : "text-foreground/80 hover:text-foreground"
      }`}
    >
      <Icon size={13} className="flex-shrink-0" />
      {label}
    </button>
  );
}

function CtxDivider() {
  return <div className="my-1 border-t border-border" />;
}

function ToolbarBtn({
  icon: Icon,
  label,
  onClick,
}: {
  icon: React.ElementType;
  label: string;
  onClick?: (e: React.MouseEvent) => void;
}) {
  return (
    <Button variant="ghost" size="icon-sm" radius="keep" layout="" title={label} onClick={onClick} className="rounded">
      <Icon size={13} />
    </Button>
  );
}
