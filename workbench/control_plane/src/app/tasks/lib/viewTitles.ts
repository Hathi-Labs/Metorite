/**
 * The one source of the six view names that `/tasks` and the Projects My work
 * section both draw (WS-64 OP-2). `ItemList` titles, the `/tasks` sidebar rows
 * and `projects/lib/myWork.ts` read these, so the case cannot drift.
 * Fence: `viewTitles.test.ts`.
 */
export const VIEW_TITLES = {
  inbox: "Inbox",
  today: "Today",
  next: "My Next Actions",
  waiting: "Waiting For",
  someday: "Someday / Maybe",
  done: "Done",
} as const;

export type TitledView = keyof typeof VIEW_TITLES;
