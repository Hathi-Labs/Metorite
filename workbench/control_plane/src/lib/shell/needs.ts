/**
 * The needs feed, as the browser reads it (`navigation_shell.md` §7.2, NS-3).
 *
 * `GET /api/shell/needs` merges what each app says needs the member. My Day's
 * "Needs you" card reads it, and the shell's one bell (NS-6) will read the
 * same feed. So the contract, the cache key and the one-click acts live here,
 * once, and no card holds a second copy.
 *
 * **The acts call the OWNING app's code.** A task is marked done through the
 * My Tasks store's own gesture (`completeFromHome`), so the subtask question
 * (D-PM-38) and the Undo (D79) are the ones My Tasks has. A notification is
 * marked read through the Projects bell's client (`notificationsApi.markRead`).
 * The shell adds no write route of its own (§7.2: "the bell reads both and
 * owns neither").
 *
 * ⚠️ It never reads the notification LIST. That read is the bell's, and
 * `seams.test.ts` fails on a second one. Notifications reach My Day through
 * this feed only.
 *
 * Fence: `needs.test.ts`.
 */
import { PROJECTS_CACHE, notificationsApi } from "@/app/projects/lib/api";
import { type CompletionStore, type DoneRow, markDoneFromHome } from "@/app/tasks/lib/completeFromHome";
import { cacheKey } from "@/lib/dataCache";

export type NeedsApp = "tasks" | "approvals" | "projects" | "email";
export type NeedsKind = "overdue" | "approval" | "due_today" | "notification" | "needs_reply";
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
  /** The rows in `items`. */
  count: number;
  /**
   * Every row the sources gave before the feed's own cut to `limit`. Each
   * source gives 15 at most, so it can be more than `count`, and it is a
   * floor, not an exact sum of what waits. An older gateway leaves it out,
   * and it then reads as `count`.
   */
  total: number;
  items: NeedsItem[];
  sources: Partial<Record<NeedsApp, SourceState>>;
}

/**
 * The server's order of the kinds. The feed arrives in it. An approval comes
 * right after an overdue task, because an agent's work waits on it. An
 * approval row has no act: approving runs an outward write, so the member
 * opens Approvals and reads the proposal first.
 */
export const KIND_ORDER: readonly NeedsKind[] = ["overdue", "approval", "due_today", "notification", "needs_reply"];

/** How many rows the feed asks for. The BFF and the gateway both cap it. */
export const NEEDS_LIMIT = 30;

/**
 * The feed's cache key lives in the Projects family ON PURPOSE. Each Projects
 * write drops that family (`projectsCall`), and nearly every such write can
 * change what needs the member: a task done, a notification read, a due date
 * moved. So the feed reads again after each one, from any app, with no
 * second invalidation map to keep in step.
 */
export const NEEDS_CACHE = `${PROJECTS_CACHE}shell/needs`;

export const needsKey = (limit = NEEDS_LIMIT): string => cacheKey(NEEDS_CACHE, { limit });

/**
 * Read a feed defensively. A field the server left out reads as empty, so a
 * card never throws on an older gateway (the visual rig's trap 8).
 */
export function readFeed(raw: unknown): NeedsFeed {
  const body = (raw ?? {}) as Partial<NeedsFeed>;
  const items = Array.isArray(body.items) ? body.items.filter(isItem) : [];
  const count = typeof body.count === "number" ? body.count : items.length;
  return {
    count,
    total: typeof body.total === "number" ? Math.max(body.total, count) : count,
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

/**
 * Run a row's one-click act through its owning app, and move the row.
 *
 * A done is the My Tasks store's gesture (`markDoneFromHome`). It may ASK
 * first (a parent with open subtasks). The row then stays while the question
 * is up, and leaves only if the answer completes the task. A read has no
 * Undo: the Projects bell has no "mark unread" route, and the shell adds
 * none.
 */
export async function runAct(
  item: NeedsItem,
  row: DoneRow,
  /** The My Tasks store. A test hands in a fake. */
  store?: CompletionStore,
): Promise<"done" | "kept" | "failed" | null> {
  if (!item.act || !item.act_ref) return null;
  if (item.act === "done") return markDoneFromHome(item.act_ref, row, store);
  row.hide();
  try {
    await notificationsApi.markRead([item.act_ref]);
    return "done";
  } catch {
    row.fail("Could not mark it read. Try again.");
    return "failed";
  }
}
