/**
 * Projects · the Personal space in the sidebar (WS-64 OP-3, D-OP-2).
 *
 * `GET /projects/tree` returns the caller's own private tree in a separate
 * `personal` key, nested at every depth. `rows` stays the team tree. A team
 * node that migration 297 left under the caller's root is in `rows`, so it
 * draws in Spaces and never here.
 *
 * Pure: no React. The page draws what these functions return, and
 * `personalTree.test.ts` fences the rules. The flag is `myWorkEnabled`
 * (`NEXT_PUBLIC_PROJECTS_MY_WORK`). With it off the section is empty and the
 * lookup tree equals the team tree, so nothing changes.
 */

import type { ProjectRow } from "./api";

/** The heading of the section. The lock icon is drawn beside it. */
export const PERSONAL_SECTION_LABEL = "Personal";

/** The tooltip of the lock. */
export const PERSONAL_SECTION_HINT = "Private to you";

const NONE: ProjectRow[] = [];

/** The roots of the Personal section: the caller's own tree, or none when the flag is off. */
export function personalRoots(
  data: { personal?: ProjectRow[] | null } | null | undefined,
  enabled: boolean,
): ProjectRow[] {
  if (!enabled) return NONE;
  return data?.personal ?? NONE;
}

/**
 * Every root the page may look a node up in: the team rows, then the
 * personal tree. A selected personal node is found here, so its board,
 * breadcrumb and settings resolve like any project. The Spaces section draws
 * `rows` alone.
 */
export function lookupRoots(
  rows: readonly ProjectRow[],
  personal: readonly ProjectRow[],
): ProjectRow[] {
  return personal.length === 0 ? (rows as ProjectRow[]) : [...rows, ...personal];
}

/** True when a node belongs to the personal tree. */
export function isPersonalNode(node: Pick<ProjectRow, "personal">): boolean {
  return node.personal === true;
}
