"use client";

/**
 * A `#171` in a chat answer, drawn as the task pill when it is a task.
 *
 * Owner report, 2026-10-13: a `#172 «one surface»` drew a pill that opens the
 * task, and a bare `#171` in the same paragraph stayed text. The parser, the
 * lookup and the pill are `lib/taskRefs.ts`, shared with the task card. This
 * file is the chat's wiring only:
 *
 * 1. **The turn's own tool results first** (`entityIndex.ts`). One task there
 *    with that number: the pill, with no request. Two: plain text, because
 *    the turn itself read two tasks with that number and a wrong link is
 *    worse than none. The lookup does not overrule that.
 * 2. **Then the lookup** (`TaskRefPill`): one batched read per answer, cached
 *    for the chat session.
 * 3. **Text while the answer streams**, because the number can be half typed.
 * 4. **No title twice.** When the answer already wrote the task's title right
 *    after the number (`#171 Composer always steers`), the pill shows the
 *    number only, and the title stays in the text after it.
 */

import EntityPill from "@/components/ui/EntityPill";
import { nameKey, type EntityIndex } from "@/lib/entityIndex";
import {
  TaskPill,
  TaskRefPill,
  taskRefResolver,
  type TaskRef,
  type TaskRefResolver,
} from "@/lib/taskRefs";

/** The distinct tasks in the index with this number. Exported for its test. */
export function indexTasksByNumber(index: EntityIndex | null | undefined, ref: string) {
  if (!index) return [];
  const hits = index.tasks.filter((t) => t.number === ref);
  return hits.filter((t, k) => hits.findIndex((o) => o.id === t.id) === k);
}

/**
 * True when `after`, the words right after the reference, start with the
 * task's whole title. Space, a dash or a colon before it do not count.
 * Exported for its test.
 */
export function echoesTitle(after: string, title: string): boolean {
  const key = nameKey(title);
  if (!key) return false;
  const lead = nameKey(after.replace(/^[\s:–—-]+/, ""));
  if (!lead.startsWith(key)) return false;
  const next = lead.charAt(key.length);
  return next === "" || !/[\p{L}\p{N}]/u.test(next);
}

export default function ChatTaskRef({
  refText,
  after = "",
  index,
  scope = "",
  live = true,
  resolver = taskRefResolver,
}: {
  /** The reference as the answer wrote it: `#171`. */
  refText: string;
  /** The words right after it in the same text run. */
  after?: string;
  /** The message's index. `MarkdownBody` passes it. */
  index?: EntityIndex;
  /** The chat session, the cache's key. */
  scope?: string;
  /** False while the answer streams. */
  live?: boolean;
  resolver?: TaskRefResolver;
}) {
  const number = Number(refText.slice(1));
  const label = (task: Pick<TaskRef, "title">) => (echoesTitle(after, task.title) ? "" : task.title);
  const local = indexTasksByNumber(index, refText);
  if (local.length === 1 && live) {
    const t = local[0];
    const task: TaskRef = { id: t.id, number, title: t.title, status: t.status, category: t.category };
    return <TaskPill task={task} label={label(task)} />;
  }
  return (
    <TaskRefPill
      number={number}
      scope={scope}
      // Two tasks in the turn's own reads: no lookup, text. Streaming: text.
      live={live && local.length === 0}
      label={label}
      resolver={resolver}
    />
  );
}

/**
 * The fenced form `#5 «Title»` whose task the turn's tools did not read (an
 * earlier turn read it). The same lookup, and the title must match too.
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
  return (
    <TaskRefPill
      number={Number(refText.slice(1))}
      title={title}
      scope={scope}
      live={live}
      resolver={resolver}
      fallback={<EntityPill kind="task" label={title} number={refText} />}
    />
  );
}
