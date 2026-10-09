"use client";

/**
 * Next actions — My Tasks' Home card (`navigation_shell.md` §4.5, NS-3).
 *
 * The member's first five next actions (`GET /projects/my/inbox?disposition=
 * NEXT`, through the lens), minus any task the Needs you card already shows.
 * One task in two cards teaches the eye to skip both (`nextActions` in
 * `lib/shell/myDay.ts`).
 *
 * Each row has one quiet complete control, and its title opens the task. The
 * completion is My Tasks' own gesture (`completeFromHome`): a parent with
 * open subtasks asks first (D-PM-38), and Undo is the store's toast (D79).
 * Lists, fields and capture stay in My Tasks (§4.3), so the footer links
 * there.
 */
import Link from "next/link";
import { useCallback, useState } from "react";

import { projectsKey } from "@/app/projects/lib/api";
import { taskDeepLink } from "@/app/projects/lib/card";
import { lensFetchNext } from "@/app/tasks/lib/lens";
import type { MyTask } from "@/app/tasks/lib/types";
import Button from "@/components/ui/Button";
import { SkeletonRows } from "@/components/ui/Skeleton";
import { CardError, FooterLink, HomeCard } from "@/lib/shell/HomeCard";
import { NEXT_SHOWN, nextActions } from "@/lib/shell/myDay";
import { completeFromHome, onNextSyncFailure } from "@/app/tasks/lib/completeFromHome";
import type { NeedsItem } from "@/lib/shell/needs";
import { accentForHue } from "@/lib/statusAccent";
import { useCachedResource } from "@/lib/useCachedResource";

/** One page, wide enough that the dedupe still leaves five. */
const NEXT_FETCH = 20;

export default function NextActionsCard({
  enabled,
  needs,
  needsSettled,
  className = "",
}: {
  enabled: boolean;
  /** The rows Needs you shows. Next actions leaves their tasks out. */
  needs: readonly NeedsItem[];
  /** Whether the needs feed has answered (or failed). Until then, wait. */
  needsSettled: boolean;
  className?: string;
}) {
  const next = useCachedResource<MyTask[]>(
    enabled ? projectsKey("my/inbox", { disposition: "NEXT", limit: NEXT_FETCH }) : null,
    () => lensFetchNext(NEXT_FETCH),
  );
  const [removed, setRemoved] = useState<ReadonlySet<string>>(() => new Set());
  const [errors, setErrors] = useState<Readonly<Record<string, string>>>({});

  // A new list forgets each removed task it no longer holds, so a task that
  // is reopened later shows again (the rule of `useNeedsYou`).
  const [seen, setSeen] = useState(next.data);
  if (next.data !== seen) {
    setSeen(next.data);
    if (next.data && removed.size > 0) {
      const held = new Set(next.data.map((t) => t.id));
      const kept = new Set([...removed].filter((id) => held.has(id)));
      if (kept.size !== removed.size) setRemoved(kept);
    }
  }

  const mark = useCallback((id: string, gone: boolean) => {
    setRemoved((prev) => {
      const s = new Set(prev);
      if (gone) s.add(id);
      else s.delete(id);
      return s;
    });
  }, []);

  const complete = useCallback(
    (task: MyTask) => {
      mark(task.id, true);
      setErrors((prev) => {
        const e = { ...prev };
        delete e[task.id];
        return e;
      });
      const fail = (message: string) => {
        mark(task.id, false);
        setErrors((prev) => ({ ...prev, [task.id]: message }));
      };
      // My Tasks' own gesture: it asks first for a parent with open
      // subtasks (D-PM-38), and its Undo is the store's toast (D79).
      const stop = onNextSyncFailure(() => fail("Could not mark it done. Try again."));
      completeFromHome(task.id).catch((err: unknown) => {
        stop();
        fail(err instanceof Error && err.message ? err.message : "Could not mark it done. Try again.");
      });
    },
    [mark],
  );

  let body: React.ReactNode;
  if (next.data === undefined || !needsSettled) {
    body =
      next.data === undefined && !next.loading ? (
        <CardError message="Your next actions could not load." onRetry={next.refresh} />
      ) : (
        <SkeletonRows count={3} className="px-2 py-1" />
      );
  } else {
    const rows = nextActions(
      next.data.filter((t) => !removed.has(t.id)),
      needs,
      NEXT_SHOWN,
    );
    body =
      rows.length === 0 ? (
        <p className="px-2 py-4 text-sm text-muted-foreground">No next actions. Your list is clear.</p>
      ) : (
        <ul className="flex flex-col">
          {rows.map((task) => (
            <li key={task.id}>
              <div className="flex min-h-11 items-center gap-1 rounded-lg pl-0.5 pr-2 hover:bg-secondary/60 tech-transition">
                <Button
                  variant="ghost"
                  size="icon-sm"
                  icon="Circle"
                  aria-label={`Complete ${task.title}`}
                  title="Mark done"
                  onClick={() => complete(task)}
                  className="h-9 w-9 shrink-0"
                />
                <Link href={taskDeepLink(task)} className="flex min-w-0 flex-1 flex-col py-1.5">
                  <span className="truncate text-sm text-foreground">{task.title}</span>
                  {task.projectName && !task.isMine ? (
                    <span className="truncate text-xs text-muted-foreground">{task.projectName}</span>
                  ) : null}
                </Link>
              </div>
              {errors[task.id] ? (
                <p role="alert" className={`pb-1 pl-11 text-xs ${accentForHue("red").text}`}>
                  {errors[task.id]}
                </p>
              ) : null}
            </li>
          ))}
        </ul>
      );
  }

  return (
    <HomeCard
      title="Next actions"
      icon="ListChecks"
      source="My Tasks"
      testId="next-actions"
      className={className}
      footer={
        <>
          <FooterLink href="/tasks" icon="CheckSquare">
            Open My Tasks
          </FooterLink>
          <FooterLink href="/tasks?do=capture" icon="Plus">
            New task
          </FooterLink>
        </>
      }
    >
      {body}
    </HomeCard>
  );
}
