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
  { id: "my-inbox", view: "inbox", label: "Inbox", icon: "Inbox", note: "Capture lands here" },
  {
    id: "my-today",
    view: "today",
    label: "Today",
    icon: "CalendarDays",
    note: "My next actions dated today or before",
  },
  { id: "my-next", view: "next", label: "Next actions", icon: "ListChecks", note: "Mine to do" },
  { id: "my-waiting", view: "waiting", label: "Waiting for", icon: "Clock", note: "Who owes me what" },
  {
    id: "my-someday",
    view: "someday",
    label: "Someday / Maybe",
    icon: "Lightbulb",
    note: "Not now",
  },
  { id: "my-done", view: "done", label: "Done", icon: "CheckCircle2", note: "Completed work" },
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
): Record<MyWorkId, number> {
  const counts = viewCounts(items, "all", today);
  const out = {} as Record<MyWorkId, number>;
  for (const entry of MY_WORK_VIEWS) {
    out[entry.id] =
      entry.view === "inbox" ? inboxCount(items, fromProjectIds) : counts[entry.view];
  }
  return out;
}

/**
 * The landing rule (§9 OP-2 item 5, §8 answer 2). With the flag on, `/projects`
 * with no `?project=`, `?task=` or `?app=` opens Today. Any other link wins.
 * Flag off: null, and the page lands as it always did.
 */
export function landingApp(
  flag: boolean,
  params: { project?: string | null; task?: string | null; app?: string | null },
): MyWorkId | null {
  if (!flag) return null;
  if (params.project || params.task || params.app) return null;
  return "my-today";
}
