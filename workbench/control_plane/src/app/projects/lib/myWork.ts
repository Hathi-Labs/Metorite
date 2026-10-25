/**
 * Projects · My work (WS-64 OP-2, `one_projects_app.md` §4.1 and §9 OP-2).
 *
 * My Tasks becomes a section of the Projects sidebar: six views over the one
 * task store (D52 to D54, D-OP-1). Nothing here copies the store. The counts
 * come from the same `viewCounts` and `inboxCount` that `/tasks` draws, and
 * every view is a component of `app/tasks/components`.
 *
 * The flag is OFF by default. With it off, the sidebar has no My work section
 * and `/projects` lands where it always did.
 */

import { inboxCount } from "@/app/tasks/lib/inbox";
import { viewCounts } from "@/app/tasks/lib/taskStore";
import type { MyTask, ViewKey } from "@/app/tasks/lib/types";
import { VIEW_TITLES } from "@/app/tasks/lib/viewTitles";

/**
 * The flag. ⚠️ Only the LITERAL `process.env.NEXT_PUBLIC_PROJECTS_MY_WORK`
 * is inlined into the browser bundle, so the default reads it literally and a
 * test passes an explicit `env` instead. `publicFlags.test.ts` fences it.
 */
export function myWorkEnabled(env?: Record<string, string | undefined>): boolean {
  const raw = env
    ? env.NEXT_PUBLIC_PROJECTS_MY_WORK
    : process.env.NEXT_PUBLIC_PROJECTS_MY_WORK;
  return ["1", "true", "on", "yes"].includes(String(raw ?? "").trim().toLowerCase());
}

/** The `?app=` keys of the six views. */
export type MyWorkId =
  | "my-inbox"
  | "my-today"
  | "my-next"
  | "my-waiting"
  | "my-someday"
  | "my-done";

export interface MyWorkView {
  id: MyWorkId;
  /** The key of the task store's `selectedView` that draws it. */
  view: ViewKey;
  label: string;
  icon: string;
  note: string;
}

/** The six views of the section, in the order of §4.1. */
export const MY_WORK_VIEWS: readonly MyWorkView[] = [
  { id: "my-inbox", view: "inbox", label: VIEW_TITLES.inbox, icon: "Inbox", note: "Capture lands here" },
  {
    id: "my-today",
    view: "today",
    label: VIEW_TITLES.today,
    icon: "CalendarDays",
    note: "My next actions dated today or before",
  },
  { id: "my-next", view: "next", label: VIEW_TITLES.next, icon: "ListChecks", note: "Mine to do" },
  { id: "my-waiting", view: "waiting", label: VIEW_TITLES.waiting, icon: "Clock", note: "Who owes me what" },
  {
    id: "my-someday",
    view: "someday",
    label: VIEW_TITLES.someday,
    icon: "Lightbulb",
    note: "Not now",
  },
  { id: "my-done", view: "done", label: VIEW_TITLES.done, icon: "CheckCircle2", note: "Completed work" },
];

const BY_ID: ReadonlyMap<string, MyWorkView> = new Map(MY_WORK_VIEWS.map((v) => [v.id, v]));

/** Is this app id one of the six views? */
export function isMyWorkId(id: string | null | undefined): id is MyWorkId {
  return Boolean(id) && BY_ID.has(id as string);
}

/** The task-store view that an app id draws, or null for any other id. */
export function myWorkView(id: string | null | undefined): ViewKey | null {
  return (id && BY_ID.get(id)?.view) || null;
}

/**
 * The count beside each entry. It is `viewCounts` and `inboxCount`, the two
 * functions the `/tasks` sidebar calls, with no Area scope (My work has no
 * Area picker), so each number equals what `/tasks` shows for the same member.
 */
export function myWorkCounts(
  items: MyTask[],
  fromProjectIds: ReadonlySet<string>,
  today: string | null,
  timeZone?: string | null,
): Record<MyWorkId, number> {
  const counts = viewCounts(items, "all", today, timeZone);
  const out = {} as Record<MyWorkId, number>;
  for (const entry of MY_WORK_VIEWS) {
    out[entry.id] =
      entry.view === "inbox" ? inboxCount(items, fromProjectIds) : counts[entry.view];
  }
  return out;
}

/**
 * The landing rule (§9 OP-2 item 5, §8 answer 2). With the flag on, `/projects`
 * with no `?project=`, `?task=`, `?app=`, `?assignee=` or `?center=` opens Today.
 * Any other link wins.
 * Flag off: null, and the page lands as it always did.
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
  return "my-today";
}

/**
 * The undo scope of the Projects page (§9 OP-2). My work has its own scope, so
 * entering it clears the board history, and Ctrl+Z reaches one store only.
 */
export function projectsUndoScope(app: string | null | undefined, projectId: string | null | undefined): string {
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
}): boolean {
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
}): T | null {
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
  if (enabled && (pathname === "/projects" || (pathname ?? "").startsWith("/projects/"))) {
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
