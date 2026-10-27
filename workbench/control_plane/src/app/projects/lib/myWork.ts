/**
 * Projects · My work (WS-64 OP-2 and OP-11, `one_projects_app.md` §4.1, §9 OP-2
 * and §9 OP-11).
 *
 * The My work section holds two rows. "My task inbox" is the Inbox of the
 * task store, with Clarify (`MyWorkHost`). "My tasks" is the roll-up space of
 * OP-11: the project tabs over every task that is mine (`MyTasksSpace`). The
 * five old list ids (`my-today`, `my-next`, `my-waiting`, `my-someday` and
 * `my-done`) are aliases now. Each one opens My tasks with its chip
 * (`projectApps.ts` `APP_ALIASES`, `myTasks.ts` `LEGACY_CHIP`).
 *
 * ⚠️ The counts come from the server (`GET /projects/my/summary`), never from
 * a page of rows. The client counters of `/tasks` are not read on this path,
 * and `myTasks.test.ts` fails if one comes back.
 *
 * The flag is OFF by default. With it off, the sidebar has no My work section
 * and `/projects` lands where it always did.
 */

import type { ViewKey } from "@/app/tasks/lib/types";
import { VIEW_TITLES } from "@/app/tasks/lib/viewTitles";

import type { NodeSummary } from "./api";
import { openCount } from "./myTasks";

// The flag lives in a leaf file (WS-64 OP-4), so `lib/centers.ts` and
// `lib/runActivity.ts` can read it without importing the task store.
export { myWorkEnabled } from "./myWorkFlag";

/** The `?app=` keys of the two rows of the section. */
export type MyWorkId = "my-inbox" | "my-tasks";

export interface MyWorkView {
  id: MyWorkId;
  /**
   * The key of the task store's `selectedView` that draws the row, or null
   * for My tasks, which reads the board endpoints and not the store.
   */
  view: ViewKey | null;
  label: string;
  icon: string;
  note: string;
}

/** The name of the roll-up space (OP-11). The section heading stays "My work". */
export const MY_TASKS_LABEL = VIEW_TITLES.next;

/** The two rows of the section, in the order of §4.1. */
export const MY_WORK_VIEWS: readonly MyWorkView[] = [
  { id: "my-inbox", view: "inbox", label: VIEW_TITLES.inbox, icon: "Inbox", note: "Capture lands here" },
  {
    id: "my-tasks",
    view: null,
    label: MY_TASKS_LABEL,
    icon: "ListChecks",
    note: "Everything assigned to me",
  },
];

const BY_ID: ReadonlyMap<string, MyWorkView> = new Map(MY_WORK_VIEWS.map((v) => [v.id, v]));

/** Is this app id one of the two rows? */
export function isMyWorkId(id: string | null | undefined): id is MyWorkId {
  return Boolean(id) && BY_ID.has(id as string);
}

/** True for the roll-up space of OP-11. */
export function isMyTasksApp(id: string | null | undefined): id is "my-tasks" {
  return id === "my-tasks";
}

/** The task-store view that an app id draws, or null for any other id. */
export function myWorkView(id: string | null | undefined): ViewKey | null {
  return (id && BY_ID.get(id)?.view) || null;
}

/**
 * The count beside each row, from the server summary of mine. "My task inbox"
 * shows `by_disposition.inbox`, the count of the inbox badge (§9 OP-11, server
 * note 10). "My tasks" shows the open tasks. With no summary yet, a row shows
 * no count, never a zero.
 */
export function myWorkNavCounts(
  summary: Pick<NodeSummary, "tasks" | "by_category" | "by_disposition"> | null | undefined,
): Partial<Record<MyWorkId, number>> {
  if (!summary) return {};
  const out: Partial<Record<MyWorkId, number>> = { "my-tasks": openCount(summary) };
  const inbox = summary.by_disposition?.inbox;
  if (typeof inbox === "number") out["my-inbox"] = inbox;
  return out;
}

/**
 * The landing rule (§9 OP-2 item 5, §8 answer 2). With the flag on, `/projects`
 * with no `?project=`, `?task=`, `?app=`, `?assignee=` or `?center=` opens
 * My tasks. The page puts the Today chip on it, as the old landing opened
 * Today. Any other link wins. Flag off: null, and the page lands as before.
 */
export function landingApp(
  flag: boolean,
  params: {
    project?: string | null;
    task?: string | null;
    app?: string | null;
    assignee?: string | null;
    center?: string | null;
  },
): MyWorkId | null {
  if (!flag) return null;
  if (params.project || params.task || params.app || params.assignee || params.center) return null;
  return "my-tasks";
}


/**
 * The undo scope of the Projects page (§9 OP-2). My work has its own scope, so
 * entering it clears the board history, and Ctrl+Z reaches one store only.
 *
 * My tasks has a scope of its own (OP-11 fix round, item 12). Its writes go
 * to the server and the inbox writes through the task store, so a card move
 * in My tasks must not be undone from My task inbox, nor the reverse.
 */
export function projectsUndoScope(app: string | null | undefined, projectId: string | null | undefined): string {
  if (isMyTasksApp(app)) return "projects:my-tasks";
  return isMyWorkId(app) ? "projects:my-work" : `projects:${projectId ?? "none"}`;
}

/**
 * The open rule of OP-2b (§4.3). The task store raises `focusedItemId` for a
 * row click, Enter on a row and the undo toast. In My work that opens the ONE
 * Projects panel and not the My Tasks focus modal: the store focus is closed,
 * and the panel opener is called. Returns true when it opened one.
 */
export function openFromFocus(
  focusedItemId: string | null,
  closeFocus: () => void,
  openPanel: (taskId: string) => void,
): boolean {
  if (!focusedItemId) return false;
  closeFocus();
  openPanel(focusedItemId);
  return true;
}

/**
 * The strip rule of OP-2b (§4.3), fix round 1. The task store is one
 * singleton, and a `/tasks` visit hydrates it, so "the store holds the task"
 * proves nothing about this page. With the flag OFF nothing of My work shows.
 */
export function showMyWorkStrip(o: {
  enabled: boolean;
  stored: boolean;
  myWork: boolean;
  filedByMe: boolean;
  /**
   * OP-11 fix round, item 9: the panel opened from My tasks. My tasks reads
   * the server, never the store, so the store strip must not draw there even
   * when an inbox visit hydrated the store. The server strip draws instead.
   */
  serverOverlay?: boolean;
}): boolean {
  if (o.serverOverlay) return false;
  return o.enabled && o.stored && (o.myWork || o.filedByMe);
}

/**
 * The overlay the badge shows. The store's row wins only in My work, where
 * the strip writes through the store. A company view keeps the server read.
 */
export function pickMine<T>(o: {
  myWork: boolean;
  enabled: boolean;
  stored: unknown;
  mine: T | null;
  fromStore: () => T;
  /** OP-11 fix round, item 9: My tasks shows the server's overlay. */
  serverOverlay?: boolean;
}): T | null {
  if (o.serverOverlay) return o.mine;
  return o.enabled && o.myWork && o.mine && o.stored ? o.fromStore() : o.mine;
}

/**
 * Where a chat card sends the member for a task (fix round 1). On `/projects`
 * with My work on, the ONE Projects panel opens through its deep link. Any
 * other place keeps the My Tasks modal. The store focus is raised only on the
 * second path, because it is not a route change there.
 */
export function chatTaskTarget(
  pathname: string | null | undefined,
  enabled: boolean,
  id: string,
): { kind: "panel"; href: string } | { kind: "modal"; href: string } {
  // OP-4: with My work on, every page opens the one Projects panel, because
  // /tasks redirects there anyway. `pathname` is kept for the call sites.
  void pathname;
  if (enabled) {
    return { kind: "panel", href: `/projects?task=${encodeURIComponent(id)}` };
  }
  return { kind: "modal", href: "/tasks" };
}

/** True when a write moved the task's status away from the panel's copy. */
export function statusDrifted(
  stored: { statusId?: string } | undefined,
  taskStatusId: string | null | undefined,
): boolean {
  return !!stored?.statusId && !!taskStatusId && stored.statusId !== taskStatusId;
}

/**
 * Watch the store's focus, and hand on only a focus raised from inside My
 * work. A chat card, a search hit or any other caller raises the same field,
 * and the host must not swallow it. `isInside` reads the gesture mark the
 * host sets in the capture phase of a click or key press in its subtree.
 * Returns the unsubscribe.
 */
export function watchInsideFocus(
  store: {
    subscribe: (
      l: (s: { focusedItemId: string | null }, prev: { focusedItemId: string | null }) => void,
    ) => () => void;
    getState: () => { closeFocus: () => void };
  },
  isInside: () => boolean,
  openPanel: (taskId: string) => void,
): () => void {
  return store.subscribe((s, prev) => {
    if (s.focusedItemId && s.focusedItemId !== prev.focusedItemId && isInside()) {
      openFromFocus(s.focusedItemId, store.getState().closeFocus, openPanel);
    }
  });
}
