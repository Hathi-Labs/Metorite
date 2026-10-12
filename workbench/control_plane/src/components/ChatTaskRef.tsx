"use client";

/**
 * A bare `#171` in a chat answer, drawn as the task pill when it is a task.
 *
 * Owner report, 2026-10-13: a `#172 «one surface»` drew a pill that opens the
 * task, and a bare `#171` in the same paragraph stayed text. This draws the
 * bare form as the SAME pill: `EntityPill` with the task's number, title and
 * status, and the same link (`taskHref`, the `?task=` deep link the fenced
 * pill and the tool cards open).
 *
 * Where the task comes from, first match wins:
 *
 * 1. The message's own tool results (`entityIndex.ts`), when exactly one task
 *    there has that number. No request.
 * 2. The lookup (`lib/taskRefResolver.ts`): one batched read per answer, under
 *    the member's own visibility, cached for the chat session.
 *
 * Until the lookup answers, and whenever it finds no task or two, the
 * reference is the plain text it was. It never draws a pill that does not
 * open.
 */

import EntityPill from "@/components/ui/EntityPill";
import { taskHref, type EntityIndex } from "@/lib/entityIndex";
import { statusAccent } from "@/lib/statusAccent";
import {
  pickTask,
  taskRefResolver,
  useTaskRef,
  type TaskRefResolver,
} from "@/lib/taskRefResolver";

/** The one task in the index with this number, or null. Exported for its test. */
export function indexTaskByNumber(
  index: EntityIndex | null | undefined,
  ref: string,
): { id: string; title: string; status?: string; category?: string } | null {
  if (!index) return null;
  const hits = index.tasks.filter((t) => t.number === ref);
  return new Set(hits.map((t) => t.id)).size === 1 ? hits[0] : null;
}

/** `#171` → 171, or null for anything else. */
function numberOf(ref: string): number | null {
  const m = ref.match(/^#([1-9][0-9]{0,8})$/);
  return m ? Number(m[1]) : null;
}

/** The pill, the same props the fenced task pill passes. */
export function TaskPill({
  id,
  number,
  title,
  status,
  category,
}: {
  id: string;
  number: string;
  title: string;
  status?: string | null;
  category?: string | null;
}) {
  return (
    <EntityPill
      kind="task"
      label={title}
      number={number}
      href={taskHref(id)}
      dot={
        status || category
          ? statusAccent({ category: category ?? undefined, name: status ?? undefined })
          : undefined
      }
      statusName={status ?? undefined}
    />
  );
}

export default function ChatTaskRef({
  refText,
  index,
  scope = "",
  live = true,
  resolver = taskRefResolver,
}: {
  /** The reference as the answer wrote it: `#171`. */
  refText: string;
  /** The message's index. `MarkdownBody` passes it, so this file needs no
   *  context (and `ChatEntityPill`, which imports this file, is not imported
   *  back). */
  index?: EntityIndex;
  /** The chat session, the cache's key. */
  scope?: string;
  /** False while the answer streams: text, and no lookup for a number that
   *  is still being typed. */
  live?: boolean;
  resolver?: TaskRefResolver;
}) {
  const local = indexTaskByNumber(index, refText);
  const number = numberOf(refText);
  const entry = useTaskRef(scope, number, live && !local, resolver);
  // While the answer streams, the number may be half typed: `#17` on its way
  // to `#171`. A pill for #17 would flash and go. So: text until it is done.
  if (!live) return <>{refText}</>;
  if (local) {
    return (
      <TaskPill id={local.id} number={refText} title={local.title}
        status={local.status} category={local.category} />
    );
  }
  const hit = entry?.state === "done" ? pickTask(entry.hits) : null;
  if (!hit) return <>{refText}</>;
  return (
    <TaskPill id={hit.id} number={refText} title={hit.title}
      status={hit.status_name} category={hit.category} />
  );
}

/**
 * The fenced form `#5 «Title»` whose task the message's tools did not read
 * (an earlier turn read it). The same lookup, and the title must match too.
 * Until it answers, and when it finds no single match, the neutral chip the
 * fenced form always drew.
 */
export function FencedTaskRef({
  refText,
  title,
  scope = "",
  live = true,
  resolver = taskRefResolver,
}: {
  refText: string;
  title: string;
  scope?: string;
  live?: boolean;
  resolver?: TaskRefResolver;
}) {
  const number = numberOf(refText);
  const entry = useTaskRef(scope, number, live, resolver);
  const hit = entry?.state === "done" ? pickTask(entry.hits, title) : null;
  if (!hit) return <EntityPill kind="task" label={title} number={refText} />;
  return (
    <TaskPill id={hit.id} number={refText} title={title}
      status={hit.status_name} category={hit.category} />
  );
}
