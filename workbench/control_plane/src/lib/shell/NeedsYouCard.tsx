"use client";

/**
 * "Needs you" — My Day's first card (`navigation_shell.md` §4.5 and §7.2).
 *
 * It draws the one needs feed, grouped in the server's order: Overdue, Due
 * today, From your projects, Waiting for your reply. The first seven rows
 * show, and "Show all" opens the rest in place.
 *
 * ⚠️ It holds the email "needs reply" rows too. §4.5 first named a separate
 * "Needs reply" card. One thread in two cards teaches the eye to skip both,
 * so the reply rows are this card's last group (the spec records the call).
 *
 * A row opens its app at `href`. A row with an act carries ONE quiet button,
 * and the act runs through the owning app's own code (`needs.ts`). The row
 * leaves at once. A done is the My Tasks store's gesture, so a parent with
 * open subtasks asks first and Undo is the store's own. A failure brings the
 * row back with a short error.
 *
 * Only the overdue tone is coloured, and it comes from `statusAccent.ts`.
 *
 * **The shell's bell draws the SAME list** (NS-6, `ShellBell.tsx`). This file
 * holds the list once, `NeedsList`, and the card and the bell's panel both
 * render it. So the groups, the rows and the acts cannot drift between the
 * two. The rows an act took off live in one store here, shared by every
 * reader of the feed, so a Done in the bell leaves My Day's card at once.
 * Fence: `shellBell.test.ts`.
 */
import Link from "next/link";
import { useCallback, useEffect, useId, useMemo, useState, useSyncExternalStore } from "react";

import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import { SkeletonRows } from "@/components/ui/Skeleton";
import { onClear } from "@/lib/dataCache";
import { accentForHue } from "@/lib/statusAccent";
import { useCachedResource } from "@/lib/useCachedResource";

import { CardError, HomeCard, keepRemoved, rowMover } from "./HomeCard";
import { appIcon, emptyNeedsLine, failedLines, rowTime, shownNeeds } from "./myDay";
import { type NeedsFeed, type NeedsItem, fetchNeeds, needsKey, runAct } from "./needs";

const DANGER = accentForHue("red");

// ── The rows an act took off, for every reader of the feed ───────────────────
//
// My Day's card and the shell's bell each read the feed. If each held its own
// "removed" set, a Done in the bell would leave the card's row in place until
// the feed read again. One small store, memory only, as `ActivityControl`'s
// open state is.

const NO_IDS: ReadonlySet<string> = new Set();
const NO_ERRORS: Readonly<Record<string, string>> = {};

let removedIds: ReadonlySet<string> = NO_IDS;
let rowErrors: Readonly<Record<string, string>> = NO_ERRORS;
const actListeners = new Set<() => void>();

function emitActs(): void {
  actListeners.forEach((l) => l());
}

function subscribeActs(listener: () => void): () => void {
  actListeners.add(listener);
  return () => {
    actListeners.delete(listener);
  };
}

function removeRow(id: string, gone: boolean): void {
  if (gone === removedIds.has(id)) return;
  const next = new Set(removedIds);
  if (gone) next.add(id);
  else next.delete(id);
  removedIds = next;
  emitActs();
}

function setRowError(id: string, message: string | null): void {
  if ((rowErrors[id] ?? null) === message) return;
  const next = { ...rowErrors };
  if (message) next[id] = message;
  else delete next[id];
  rowErrors = next;
  emitActs();
}

/** Forget each removed row that the newest answer no longer holds. */
function pruneRemoved(held: Iterable<string>): void {
  const kept = keepRemoved(removedIds, held);
  if (kept === removedIds) return;
  removedIds = kept;
  emitActs();
}

// A new member on this browser starts with nothing hidden.
onClear(() => {
  removedIds = NO_IDS;
  rowErrors = NO_ERRORS;
  emitActs();
});

export interface NeedsYou {
  /** The feed's rows, minus the ones an act just removed. */
  items: NeedsItem[] | undefined;
  /** The count for the summary line, minus the removed rows. */
  count: number | undefined;
  sources: NeedsFeed["sources"];
  loading: boolean;
  error: string | null;
  refresh: () => void;
  errors: Readonly<Record<string, string>>;
  act: (item: NeedsItem, from: HTMLElement | null) => void;
}

/**
 * The feed and its optimistic acts. My Day calls it once and hands it down,
 * so the summary line, this card and Next actions read ONE answer. The
 * shell's bell calls it too. The cache key is the same (`needsKey`), and the
 * removed rows are the one store above, so the two stay in step.
 */
export function useNeedsYou(enabled: boolean): NeedsYou {
  const feed = useCachedResource<NeedsFeed>(enabled ? needsKey() : null, () => fetchNeeds());
  const removed = useSyncExternalStore(subscribeActs, () => removedIds, () => NO_IDS);
  const errors = useSyncExternalStore(subscribeActs, () => rowErrors, () => NO_ERRORS);

  // A new answer from the server forgets each removed row it no longer
  // holds. Without this, a task that comes back later (reopened in Projects)
  // would stay hidden here until a reload. It only drops ids the answer does
  // not hold, so no drawn row changes, and an effect is early enough.
  useEffect(() => {
    if (feed.data) pruneRemoved(feed.data.items.map((i) => i.id));
  }, [feed.data]);

  // A done is the My Tasks store's own gesture: it may ask the subtask
  // question first, its Undo is the store's toast (`UndoToast`, mounted by
  // My Day, or `UndoToastFallback` by the bell's host), and its failure
  // arrives as `syncFailure` (`markDoneFromHome`).
  const act = useCallback((item: NeedsItem, from: HTMLElement | null) => {
    void runAct(item, rowMover(item.id, removeRow, setRowError, from));
  }, []);

  const items = useMemo(
    () => feed.data?.items.filter((i) => !removed.has(i.id)),
    [feed.data, removed],
  );
  const gone = feed.data ? feed.data.items.length - (items?.length ?? 0) : 0;
  return {
    items,
    count: feed.data ? Math.max(0, feed.data.count - gone) : undefined,
    sources: feed.data?.sources ?? {},
    loading: feed.loading,
    error: feed.error,
    refresh: feed.refresh,
    errors,
    act,
  };
}

export default function NeedsYouCard({
  needs,
  now,
  className = "",
}: {
  needs: NeedsYou;
  /** The page's clock, by the minute. A render reads no clock itself. */
  now: Date | null;
  className?: string;
}) {
  return (
    <HomeCard title="Needs you" icon="Bell" testId="needs-you" className={className}>
      <NeedsList needs={needs} now={now} />
    </HomeCard>
  );
}

/**
 * THE list of what needs the member: the groups, the rows, their acts, the
 * empty line and the caveat. My Day's card and the shell bell's panel both
 * render it, so they show the same rows (NS-6).
 */
export function NeedsList({
  needs,
  now,
}: {
  needs: NeedsYou;
  /** The page's clock, by the minute. A render reads no clock itself. */
  now: Date | null;
}) {
  const [expanded, setExpanded] = useState(false);
  // The card and the bell's panel can both be on screen, so each list names
  // its group labels with its own prefix.
  const uid = useId();

  let body: React.ReactNode;
  if (needs.items === undefined) {
    body = needs.loading ? (
      <SkeletonRows count={4} className="px-2 py-1" />
    ) : (
      <CardError message="What needs you could not load." onRetry={needs.refresh} />
    );
  } else if (needs.items.length === 0) {
    body = (
      <div className="flex items-center gap-2.5 px-2 py-4">
        <span className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full ${accentForHue("green").soft}`}>
          <Icon name="Check" size={14} className={accentForHue("green").text} />
        </span>
        <span className="text-sm text-muted-foreground">
          {emptyNeedsLine(failedLines(needs.sources).length > 0)}
        </span>
      </div>
    );
  } else {
    const { groups, hidden } = shownNeeds(needs.items, expanded);
    body = (
      <div className="flex flex-col gap-2">
        {groups.map((group) => (
          <div key={group.kind} role="group" aria-labelledby={`${uid}-${group.kind}`}>
            <div
              id={`${uid}-${group.kind}`}
              className={`px-2 pt-1 pb-0.5 text-[11px] font-medium ${
                group.kind === "overdue" ? DANGER.text : "text-muted-foreground"
              }`}
            >
              {group.label}
            </div>
            <ul className="flex flex-col">
              {group.items.map((item) => (
                <NeedsRow
                  key={item.id}
                  item={item}
                  nowMs={now?.getTime() ?? null}
                  error={needs.errors[item.id]}
                  onAct={(from) => needs.act(item, from)}
                />
              ))}
            </ul>
          </div>
        ))}
        {hidden > 0 ? (
          <Button
            variant="ghost"
            size="sm"
            icon="ChevronDown"
            className="mx-2 self-start"
            onClick={() => setExpanded(true)}
          >
            Show all {needs.items.length}
          </Button>
        ) : null}
      </div>
    );
  }

  // A stale feed under a failed refresh keeps its rows, and says so.
  const stale = needs.items !== undefined && needs.error;
  const failed = failedLines(needs.sources);
  return (
    <div data-needs-list="">
      {body}
      {/* The caveat, said ONCE and where the gap is: one muted line per
          silent source, or one for a refresh that failed, and one Retry. */}
      {failed.length > 0 || stale ? (
        <div className="mt-1 flex flex-wrap items-center justify-between gap-x-3 gap-y-1 px-2 pb-1">
          <div className="flex min-w-0 flex-col gap-0.5">
            {failed.map((line) => (
              <p key={line} className="flex items-center gap-1.5 text-xs text-muted-foreground">
                <Icon name="Info" size={12} className="shrink-0" />
                {line}
              </p>
            ))}
            {stale ? (
              <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
                <Icon name="Info" size={12} className="shrink-0" />
                Could not refresh this list.
              </p>
            ) : null}
          </div>
          <Button variant="text" size="none" icon="RefreshCw" onClick={needs.refresh} className="min-h-8 gap-1 text-xs font-medium">
            Retry
          </Button>
        </div>
      ) : null}
    </div>
  );
}

function NeedsRow({
  item,
  nowMs,
  error,
  onAct,
}: {
  item: NeedsItem;
  /** `null` before the client clock exists: the row then prints no time. */
  nowMs: number | null;
  error?: string;
  onAct: (from: HTMLElement) => void;
}) {
  const time = nowMs === null ? "" : rowTime(item, nowMs);
  return (
    <li>
      <div className="flex min-h-11 items-center gap-2 rounded-lg pr-1 hover:bg-secondary/60 tech-transition">
        <Link href={item.href} className="flex min-w-0 flex-1 items-center gap-3 rounded-lg py-1.5 pl-2">
          <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-secondary text-muted-foreground">
            <Icon name={appIcon(item.app)} size={14} />
          </span>
          <span className="min-w-0 flex-1">
            <span className="block truncate text-sm text-foreground">{item.title}</span>
            {item.detail || time ? (
              <span className="block truncate text-xs text-muted-foreground">
                {item.detail}
                {item.detail && time ? " · " : null}
                {time ? <span className={item.kind === "overdue" ? DANGER.text : undefined}>{time}</span> : null}
              </span>
            ) : null}
          </span>
        </Link>
        {/* On a phone the act is its icon alone, so the title keeps the row's
            width. The name stays whole for a screen reader at every size. */}
        {item.act === "done" ? (
          <Button variant="ghost" size="sm" icon="Check" data-row-act="" onClick={(e) => onAct(e.currentTarget)} aria-label={`Mark ${item.title} done`} className="min-h-9 min-w-9">
            <span className="hidden sm:inline">Done</span>
          </Button>
        ) : item.act === "read" ? (
          <Button variant="ghost" size="sm" icon="CheckCheck" data-row-act="" onClick={(e) => onAct(e.currentTarget)} aria-label={`Mark ${item.title} read`} className="min-h-9 min-w-9">
            <span className="hidden sm:inline">Mark read</span>
          </Button>
        ) : null}
      </div>
      {error ? (
        <p role="alert" className={`px-2 pb-1 pl-12 text-xs ${DANGER.text}`}>
          {error}
        </p>
      ) : null}
    </li>
  );
}
