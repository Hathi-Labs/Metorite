/**
 * The two tabs of /approvals (WS-55 EA-18c, `external_agent_access.md`
 * §12.8 "Approvals"): "Actions", the Action Broker queue, and "Agent
 * questions · N", the "For me" list of Projects.
 *
 * ⚠️ **The tab is the flag.** The questions route answers 404 while
 * `AGENT_QUESTIONS_ORGS` does not hold the organization. Then the page keeps
 * the CIV-14k "Waiting for you" section and draws no tabs (`tabsShown`). With
 * the flag on, the tabs replace that section, so the page has ONE list of the
 * questions that wait on a person, and never a second tab beside it.
 *
 * The count is the route's `counts.mine`, the number that the Projects sidebar
 * and the My Day card show. This file computes no count of its own.
 *
 * Fence: `approvalTabs.test.ts`.
 */

export type ApprovalTab = "actions" | "questions";

export const ACTIONS_TAB_LABEL = "Actions";
export const QUESTIONS_TAB_LABEL = "Agent questions";
/** The link under the list: the full list (with "Everyone" for a reviewer) is in Projects. */
export const FULL_LIST_LABEL = "Open the full list in Projects";
export const FULL_LIST_HREF = "/projects?app=questions";

/** The tabs draw only when the questions route is on. `null` is "not known yet". */
export function tabsShown(questionsOn: boolean | null): boolean {
  return questionsOn === true;
}

/**
 * The CIV-14k section reads the old route when the questions route is off (a
 * 404), and also when the questions read FAILED with anything else and has not
 * answered yet (`failed`). Only a 404 means off, so a 500 or a timeout never
 * leaves the page with neither the tabs nor the old section. Unknown and not
 * failed (the first read is on its way): neither draws.
 */
export function waitingSectionOn(questionsOn: boolean | null, failed = false): boolean {
  return questionsOn === false || (questionsOn === null && failed);
}

/** True while the first read is on its way: the page reserves the tab bar. */
export function tabsPending(questionsOn: boolean | null, failed: boolean): boolean {
  return questionsOn === null && !failed;
}

/** The line under the page when the questions read failed. */
export const QUESTIONS_READ_FAILED =
  "Could not read the agent questions. The page shows the older list and reads again soon.";

/** "Agent questions · 3". A count of zero or an unknown count prints the plain name. */
export function questionsTabLabel(count: number | undefined): string {
  return typeof count === "number" && count > 0
    ? `${QUESTIONS_TAB_LABEL} · ${count}`
    : QUESTIONS_TAB_LABEL;
}

/**
 * The tab on screen. A member who picked one sees it. Else the Actions tab,
 * except for a member who cannot read the queue (an admin reviews it): that
 * member starts on the questions.
 */
export function activeTab(picked: ApprovalTab | null, queueClosed: boolean): ApprovalTab {
  return picked ?? (queueClosed ? "questions" : "actions");
}
