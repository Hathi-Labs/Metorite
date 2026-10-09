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
 * and the act runs through the owning app's own client (`needs.ts`). The row
 * leaves at once. A completion offers Undo in the app's toast, and a failure
 * brings the row back with a short error.
 *
 * Only the overdue tone is coloured, and it comes from `statusAccent.ts`.
 */
import Link from "next/link";
import { useCallback, useMemo, useState } from "react";

import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import { SkeletonRows } from "@/components/ui/Skeleton";
import { useToast } from "@/components/ui/Toast";
import { accentForHue } from "@/lib/statusAccent";
import { useCachedResource } from "@/lib/useCachedResource";

import { CardError, HomeCard } from "./HomeCard";
import { actError, appIcon, failedLines, rowTime, shownNeeds } from "./myDay";
import {
  type CompletionUndo,
  type NeedsFeed,
  type NeedsItem,
  fetchNeeds,
  needsKey,
  runAct,
  undoCompletion,
} from "./needs";

const DANGER = accentForHue("red");

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
  act: (item: NeedsItem) => void;
}

/**
 * The feed and its optimistic acts. My Day calls it once and hands it down,
 * so the summary line, this card and Next actions read ONE answer.
 */
export function useNeedsYou(enabled: boolean): NeedsYou {
  const feed = useCachedResource<NeedsFeed>(enabled ? needsKey() : null, () => fetchNeeds());
  const toast = useToast();
  const [removed, setRemoved] = useState<ReadonlySet<string>>(() => new Set());
  const [errors, setErrors] = useState<Readonly<Record<string, string>>>({});

  const remove = useCallback((id: string, gone: boolean) => {
    setRemoved((prev) => {
      const next = new Set(prev);
      if (gone) next.add(id);
      else next.delete(id);
      return next;
    });
  }, []);

  const undo = useCallback(
    async (item: NeedsItem, plan: CompletionUndo) => {
      try {
        await undoCompletion(plan);
        remove(item.id, false);
      } catch {
        toast.show({
          key: `my-day:undo:${item.id}`,
          variant: "error",
          title: "Could not undo. The task changed since.",
        });
      }
    },
    [remove, toast],
  );

  const act = useCallback(
    (item: NeedsItem) => {
      remove(item.id, true);
      setErrors((prev) => {
        const next = { ...prev };
        delete next[item.id];
        return next;
      });
      runAct(item).then(
        (plan) => {
          if (item.act !== "done") return;
          toast.show({
            key: `my-day:done:${item.id}`,
            variant: "success",
            title: "Marked done",
            action: plan ? { label: "Undo", onClick: () => void undo(item, plan) } : undefined,
          });
        },
        () => {
          remove(item.id, false);
          setErrors((prev) => ({ ...prev, [item.id]: actError(item.act) }));
        },
      );
    },
    [remove, toast, undo],
  );

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

export default function NeedsYouCard({ needs, className = "" }: { needs: NeedsYou; className?: string }) {
  const [expanded, setExpanded] = useState(false);
  const now = Date.now();

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
        <span className="text-sm text-muted-foreground">Nothing needs you right now.</span>
      </div>
    );
  } else {
    const { groups, hidden } = shownNeeds(needs.items, expanded);
    body = (
      <div className="flex flex-col gap-2">
        {groups.map((group) => (
          <div key={group.kind} role="group" aria-labelledby={`needs-${group.kind}`}>
            <div
              id={`needs-${group.kind}`}
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
                  nowMs={now}
                  error={needs.errors[item.id]}
                  onAct={() => needs.act(item)}
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
    <HomeCard title="Needs you" icon="Bell" testId="needs-you" className={className}>
      {body}
      {failed.length > 0 || stale ? (
        <div className="mt-1 flex flex-col gap-0.5 px-2 pb-1">
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
              <Button variant="text" size="none" onClick={needs.refresh} className="font-medium">
                Retry
              </Button>
            </p>
          ) : null}
        </div>
      ) : null}
    </HomeCard>
  );
}

function NeedsRow({
  item,
  nowMs,
  error,
  onAct,
}: {
  item: NeedsItem;
  nowMs: number;
  error?: string;
  onAct: () => void;
}) {
  const time = rowTime(item, nowMs);
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
        {item.act === "done" ? (
          <Button variant="ghost" size="sm" icon="Check" onClick={onAct} aria-label={`Mark ${item.title} done`}>
            Done
          </Button>
        ) : item.act === "read" ? (
          <Button variant="ghost" size="sm" onClick={onAct} aria-label={`Mark ${item.title} read`}>
            Mark read
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
