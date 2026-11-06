/**
 * Projects · the tabs of the task card (ORC-3, D181, `specs/one_record_card.md` §5).
 *
 * The task card's body is four tabs, and `TaskBody` draws them for each host:
 * Projects, My Tasks and Calendar.
 *
 * | Tab        | Holds                                              |
 * |------------|----------------------------------------------------|
 * | Overview   | Details, Description, the custom fields            |
 * | Related    | Subtasks and related tasks, Links                  |
 * | Files      | Files                                              |
 * | Discussion | Comments first (D172.7), then Activity             |
 *
 * The words match the CRM's where the meaning is the same (Overview, Related).
 * Above the tabs, on each tab: the host's strip, the agent run, an open
 * decision and the Add row. Nothing that waits on the member hides behind a
 * tab.
 *
 * Pure: the URL rules live here, and `components/useTaskTab.ts` is the seam
 * to the browser.
 */

export const TASK_TABS = ["overview", "related", "files", "discussion"] as const;

export type TaskTab = (typeof TASK_TABS)[number];

export const TASK_TAB_LABEL: Record<TaskTab, string> = {
  overview: "Overview",
  related: "Related",
  files: "Files",
  discussion: "Discussion",
};

/** The query key. `?rtab=` is the CRM's, so the two never collide. */
export const TASK_TAB_PARAM = "ttab";

export function isTaskTab(value: unknown): value is TaskTab {
  return (TASK_TABS as readonly string[]).includes(value as string);
}

/** The tab a query string names. Anything else opens Overview. */
export function readTaskTab(search: string): TaskTab {
  const value = new URLSearchParams(search).get(TASK_TAB_PARAM);
  return isTaskTab(value) ? value : "overview";
}

/**
 * The query string with the tab in it. Overview is the default, so it takes
 * the key out, and a card on Overview leaves the address as it found it.
 * Returns "" or a string that starts with "?".
 */
export function searchWithTab(search: string, tab: TaskTab): string {
  const params = new URLSearchParams(search);
  if (tab === "overview") params.delete(TASK_TAB_PARAM);
  else params.set(TASK_TAB_PARAM, tab);
  const qs = params.toString();
  return qs ? `?${qs}` : "";
}

/**
 * The count a tab shows, or nothing. A tab with content shows its count, and
 * an empty tab shows its word alone (§5 "Counts").
 */
export function tabCount(n: number): number | undefined {
  return n > 0 ? n : undefined;
}
