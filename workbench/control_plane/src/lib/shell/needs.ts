/**
 * The needs feed, as the browser reads it (`navigation_shell.md` §7.2, NS-3).
 *
 * `GET /api/shell/needs` merges what each app says needs the member. My Day's
 * "Needs you" card reads it, and the shell's one bell (NS-6) will read the
 * same feed. So the contract, the cache key and the one-click acts live here,
 * once, and no card holds a second copy.
 *
 * **The acts call the OWNING app's client.** A task is completed through the
 * My Tasks lens (`lensCompleteItem`), and a notification is marked read
 * through the Projects bell's client (`notificationsApi.markRead`). The shell
 * adds no write route of its own (§7.2: "the bell reads both and owns
 * neither").
 *
 * ⚠️ It never reads the notification LIST. That read is the bell's, and
 * `seams.test.ts` fails on a second one. Notifications reach My Day through
 * this feed only.
 *
 * Fence: `needs.test.ts`.
 */
import { notificationsApi } from "@/app/projects/lib/api";
import {
  lensCompleteItem,
  lensGetItem,
  lensPatchItem,
  lensSetStatusId,
} from "@/app/tasks/lib/lens";
import type { MyTask } from "@/app/tasks/lib/types";
import { cacheKey, invalidate } from "@/lib/dataCache";

export type NeedsApp = "tasks" | "projects" | "email";
export type NeedsKind = "overdue" | "due_today" | "notification" | "needs_reply";
export type NeedsAct = "done" | "read";
export type SourceState = "ok" | "failed" | "absent";

/** One row of the feed, exactly as `GET /shell/needs` sends it. */
export interface NeedsItem {
  id: string;
  app: NeedsApp;
  kind: NeedsKind;
  title: string;
  detail: string | null;
  href: string;
  at: string | null;
  act: NeedsAct | null;
  act_ref: string | null;
}

export interface NeedsFeed {
  count: number;
  items: NeedsItem[];
  sources: Partial<Record<NeedsApp, SourceState>>;
}

/** The server's order of the kinds. The feed arrives in it. */
export const KIND_ORDER: readonly NeedsKind[] = ["overdue", "due_today", "notification", "needs_reply"];

/** How many rows the feed asks for. The BFF and the gateway both cap it. */
export const NEEDS_LIMIT = 30;

/** The cache family of the feed. An act drops it, so the next read is fresh. */
export const NEEDS_CACHE = "shell/needs";

export const needsKey = (limit = NEEDS_LIMIT): string => cacheKey(NEEDS_CACHE, { limit });

/**
 * Read a feed defensively. A field the server left out reads as empty, so a
 * card never throws on an older gateway (the visual rig's trap 8).
 */
export function readFeed(raw: unknown): NeedsFeed {
  const body = (raw ?? {}) as Partial<NeedsFeed>;
  const items = Array.isArray(body.items) ? body.items.filter(isItem) : [];
  return {
    count: typeof body.count === "number" ? body.count : items.length,
    items,
    sources: body.sources && typeof body.sources === "object" ? body.sources : {},
  };
}

function isItem(v: unknown): v is NeedsItem {
  const row = v as Partial<NeedsItem> | null;
  return (
    !!row &&
    typeof row.id === "string" &&
    typeof row.title === "string" &&
    typeof row.href === "string" &&
    KIND_ORDER.includes(row.kind as NeedsKind)
  );
}

/** Fetch the feed. A refusal or an outage throws, so the card can say so. */
export async function fetchNeeds(limit = NEEDS_LIMIT): Promise<NeedsFeed> {
  const res = await fetch(`/api/shell/needs?limit=${limit}`, { cache: "no-store" });
  if (!res.ok) throw new Error("What needs you could not load.");
  return readFeed(await res.json());
}

/** How to put a completion back. `null` when nothing can be put back. */
export interface CompletionUndo {
  taskId: string;
  /** The status the task had before. Absent when the status did not move. */
  priorStatus?: string;
  /** The row's `updated_at` after our write. Undo refuses a newer change. */
  ifMatch?: string;
  /** My disposition before, when it was an open one. */
  priorDisposition?: MyTask["disposition"];
}

/**
 * The undo of one completion, from the row before and the row after.
 *
 * D79, the rule My Tasks follows: Undo writes the status back only when our
 * write moved it, and only while the row is still the one we left
 * (`If-Match`). A DONE disposition before means the task was already done,
 * so there is nothing to give back.
 */
export function completionUndo(
  before: Pick<MyTask, "id" | "statusId" | "disposition"> | null,
  after: Pick<MyTask, "statusId" | "updatedAt"> | null,
): CompletionUndo | null {
  if (!before || before.disposition === "DONE") return null;
  const moved = !!before.statusId && !!after?.statusId && before.statusId !== after.statusId;
  return {
    taskId: before.id,
    priorStatus: moved ? before.statusId : undefined,
    ifMatch: moved && after?.updatedAt ? after.updatedAt : undefined,
    priorDisposition: before.disposition,
  };
}

/**
 * Complete a task through the lens, and return how to undo it.
 *
 * The row is read FIRST, as `quickDispose` in the My Tasks store does,
 * because the undo needs the status the SERVER had, not the one a card drew.
 */
export async function completeTask(taskId: string): Promise<CompletionUndo | null> {
  const before = await lensGetItem(taskId).catch(() => null);
  const after = await lensCompleteItem(taskId);
  invalidate(NEEDS_CACHE);
  return completionUndo(before ? { ...before, id: taskId } : null, after);
}

/** Put a completion back: the status first, then my disposition. */
export async function undoCompletion(undo: CompletionUndo): Promise<void> {
  if (undo.priorStatus) {
    await lensSetStatusId(undo.taskId, undo.priorStatus, { ifMatch: undo.ifMatch });
  }
  if (undo.priorDisposition) {
    // An open disposition on a closed task reopens it (`reopen_if_closed`).
    // After the status write above the task is open again, so this only
    // restores my list.
    await lensPatchItem(undo.taskId, { disposition: undo.priorDisposition });
  }
  invalidate(NEEDS_CACHE);
}

/**
 * Run a row's one-click act through its owning app.
 *
 * Returns the undo of a completion. A notification marked read has no undo:
 * the Projects bell has no "mark unread" route, and the shell adds none.
 */
export async function runAct(item: NeedsItem): Promise<CompletionUndo | null> {
  if (!item.act || !item.act_ref) return null;
  if (item.act === "done") return completeTask(item.act_ref);
  await notificationsApi.markRead([item.act_ref]);
  invalidate(NEEDS_CACHE);
  return null;
}
