"use client";

/**
 * The Now card of My Day (command centre, CC-4).
 *
 * A session runs: the ring with the clock, the task, its chips and subtasks,
 * and Pause, Done and +10 min. These are the focus room's own commands (the
 * room publishes them in `focusLive.ts`), so the card never keeps a second
 * timer. No session runs: one offer, "Start focus", on the One Thing or the
 * first task of today. The foot reads "Up next" from the Today window.
 *
 * It draws nothing when no session runs and today holds nothing open.
 */
import Link from "next/link";
import { useEffect, useState, useSyncExternalStore } from "react";

import { oneThingIdFor } from "@/app/tasks/lib/focusPrefs";
import { focusCommands, useFocusLive } from "@/app/tasks/lib/focusLive";
import { useStartFocus } from "@/app/tasks/lib/focusStart";
import { useTaskStore } from "@/app/tasks/lib/taskStore";
import type { MyTask } from "@/app/tasks/lib/types";
import { fmtClock } from "@/app/tasks/lib/utils";
import Badge from "@/components/ui/Badge";
import Button from "@/components/ui/Button";
import { dueDays, dueLabel } from "@/lib/taskCard";
import type { CachedResource } from "@/lib/useCachedResource";

import { idleOffer, ringCaption, ringFill, ringText, upNext } from "./nowCardRules";

const noSubscribe = () => () => {};

/** The One Thing of today, from this browser's focus prefs. Null on the server. */
function useOneThing(now: Date | null): string | null {
  return useSyncExternalStore(
    noSubscribe,
    () => oneThingIdFor(now ?? new Date()),
    () => null,
  );
}

function Ring({ fill, paused, children }: { fill: number; paused: boolean; children: React.ReactNode }) {
  const pct = Math.min(100, Math.max(0, fill * 100));
  const color = paused ? "var(--muted-foreground)" : "var(--primary)";
  return (
    <div
      className="flex h-24 w-24 shrink-0 items-center justify-center rounded-full sm:h-28 sm:w-28"
      style={{ background: `conic-gradient(${color} ${pct}%, var(--secondary) ${pct}% 100%)` }}
    >
      <div className="flex h-[5.25rem] w-[5.25rem] flex-col items-center justify-center rounded-full bg-card text-center sm:h-[6.25rem] sm:w-[6.25rem]">
        {children}
      </div>
    </div>
  );
}

function UpNext({ task }: { task: MyTask | null }) {
  if (!task) {
    return <span className="text-muted-foreground">Nothing else planned today.</span>;
  }
  const at = task.scheduledStart ? fmtClock(new Date(task.scheduledStart)) : "";
  return (
    <span className="min-w-0 truncate" data-testid="now-up-next">
      <b className="font-semibold text-foreground">Up next {at}</b>
      <span className="text-muted-foreground"> · {task.title}</span>
    </span>
  );
}

export default function NowCard({
  today,
  now,
  className = "",
}: {
  today: CachedResource<MyTask[]>;
  /** The page's clock, by the minute. */
  now: Date | null;
  className?: string;
}) {
  const live = useFocusLive();
  const start = useStartFocus();
  const oneThing = useOneThing(now);
  const nowMs = now?.getTime() ?? 0;
  const item = useTaskStore((s) => (live ? s.items.find((i) => i.id === live.itemId) : undefined));
  const loadSubtasks = useTaskStore((s) => s.loadSubtasks);
  const [subtasks, setSubtasks] = useState<MyTask[]>([]);
  const itemId = item?.id;
  const subtaskCount = item?.subtaskCount ?? 0;
  useEffect(() => {
    let alive = true;
    if (itemId && subtaskCount > 0) {
      void loadSubtasks(itemId).then((rows) => {
        if (alive) setSubtasks(rows);
      });
    }
    return () => {
      alive = false;
    };
  }, [itemId, subtaskCount, loadSubtasks]);

  const shell = "min-w-0 rounded-xl border border-border bg-card glass:rounded-[16px] glass:border-transparent glass:glass-raised";

  if (!live || !item) {
    const offer = idleOffer(today.data, nowMs, oneThing);
    if (!offer) return null;
    return (
      <section aria-label="Now" data-testid="now-card" data-now-state="idle" className={`${shell} ${className}`}>
        <div className="flex flex-wrap items-center gap-3 px-4 py-3.5">
          <div className="min-w-0 flex-1">
            <div className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Now</div>
            <div className="mt-0.5 truncate text-sm font-semibold text-foreground">
              {offer.id === oneThing ? "★ " : ""}
              {offer.title}
            </div>
            <div className="mt-0.5 text-xs text-muted-foreground">
              {offer.scheduledStart ? `${fmtClock(new Date(offer.scheduledStart))} · ` : ""}
              No focus session runs.
            </div>
          </div>
          <Button
            variant="primary"
            size="sm"
            icon="Play"
            data-testid="now-start"
            aria-label={`Start focus: ${offer.title}`}
            onClick={() => void start(offer.id, { minimized: true })}
          >
            Start focus
          </Button>
        </div>
      </section>
    );
  }

  const next = upNext(today.data, nowMs, item.id);
  const due = item.dueAt && nowMs > 0 ? dueLabel(item.dueAt, nowMs) : "";
  const dueTone = item.dueAt && nowMs > 0 && (dueDays(item.dueAt, nowMs) ?? 1) < 0 ? "destructive" : "neutral";
  const onBoard = item.statusCategory === "in_progress" && item.workflowStage;
  const cmds = focusCommands();
  const range = item.scheduledStart
    ? `${fmtClock(new Date(item.scheduledStart))}${item.scheduledEnd ? ` – ${fmtClock(new Date(item.scheduledEnd))}` : ""}`
    : "";
  return (
    <section aria-label="Now" data-testid="now-card" data-now-state={live.paused ? "paused" : "running"} className={`${shell} ${className}`}>
      <div className="grid grid-cols-[auto_minmax(0,1fr)] items-center gap-4 px-4 py-4">
        <Ring fill={ringFill(live)} paused={live.paused}>
          <span className="text-xl font-semibold tabular-nums text-foreground sm:text-2xl" data-testid="now-clock">
            {ringText(live)}
          </span>
          <span className="px-1 text-[10px] leading-tight text-muted-foreground">{ringCaption(live)}</span>
        </Ring>
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="text-xs font-semibold uppercase tracking-wide text-primary">Now · focus</span>
            {range ? <Badge tone="neutral">{range}</Badge> : null}
            {item.id === oneThing ? <Badge tone="warning">★ One Thing</Badge> : null}
          </div>
          <h3 className="mt-1 text-base font-semibold text-foreground" data-testid="now-title">
            {item.title}
          </h3>
          <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
            {item.projectName ? <Badge tone="neutral">{item.projectName}</Badge> : null}
            {due ? <Badge tone={dueTone}>Due {due}</Badge> : null}
            {onBoard ? <Badge tone="info">{item.workflowStage} on the board</Badge> : null}
          </div>
          {subtasks.length > 0 ? (
            <ul className="mt-2 flex flex-col gap-0.5 text-xs">
              {subtasks.slice(0, 4).map((st) => (
                <li key={st.id} className={st.disposition === "DONE" ? "text-muted-foreground line-through" : "text-foreground"}>
                  {st.disposition === "DONE" ? "✓" : "○"} {st.title}
                </li>
              ))}
            </ul>
          ) : null}
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <Button
              variant="secondary"
              size="sm"
              icon={live.paused ? "Play" : "Pause"}
              data-testid="now-pause"
              onClick={() => cmds?.togglePause()}
            >
              {live.paused ? "Resume" : "Pause"}
            </Button>
            <Button variant="primary" size="sm" icon="Check" data-testid="now-done" onClick={() => cmds?.complete()}>
              Done
            </Button>
            {live.phase === "work" ? (
              <Button variant="secondary" size="sm" icon="Plus" data-testid="now-extend" onClick={() => cmds?.extend10()}>
                10 min
              </Button>
            ) : null}
            <Button
              variant="ghost"
              size="sm"
              onClick={() => useTaskStore.getState().expandFocusSession()}
              data-testid="now-open-room"
            >
              Open focus room
            </Button>
          </div>
        </div>
      </div>
      <div className="flex items-center justify-between gap-2 border-t border-border px-4 py-2.5 text-xs">
        <UpNext task={next} />
        <Link href="/calendar" className="shrink-0 text-muted-foreground hover:text-foreground">
          Calendar
        </Link>
      </div>
    </section>
  );
}
