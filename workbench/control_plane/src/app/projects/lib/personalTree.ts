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
import { splitArchived, type ArchiveSplit } from "./archivedRoots";

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

/** The fold id of the Archived fold inside the Personal section (memory-only). */
export const PERSONAL_ARCHIVED_FOLD_ID = "__personal_archived__";

/**
 * The Personal tree split into live and filed work. The live part draws under
 * Personal. The filed part draws in its own fold inside the Personal section,
 * so a private node never joins the team Archived fold.
 */
export function splitPersonal(personal: readonly ProjectRow[]): ArchiveSplit {
  return splitArchived(personal);
}

/** The sentence shown when a selection has no common destination. */
export const MIXED_MOVE_HINT =
  "These tasks sit in both a personal and a team project. Move them in two steps, one side at a time.";

/**
 * The roots a Move tasks card may offer. The server refuses team to personal
 * (`assert_move_keeps_privacy`), so the card offers the team tree for team
 * tasks and the personal tree for personal tasks. A selection that mixes both
 * has no common destination, so it gets none. "Is personal" is the `personal`
 * flag of the row the task sits in, found in the tree.
 */
export function moveDestinationRoots(
  projectIds: readonly string[],
  team: readonly ProjectRow[],
  personal: readonly ProjectRow[],
): { roots: ProjectRow[]; mixed: boolean } {
  if (personal.length === 0) return { roots: team as ProjectRow[], mixed: false };
  const inPersonal = new Set<string>();
  const walk = (nodes: readonly ProjectRow[]) => {
    for (const n of nodes) {
      inPersonal.add(n.id);
      walk(n.children ?? []);
    }
  };
  walk(personal);
  const flags = projectIds.map((id) => inPersonal.has(id));
  if (flags.length > 0 && flags.every(Boolean)) return { roots: personal as ProjectRow[], mixed: false };
  if (flags.some(Boolean)) return { roots: [], mixed: true };
  return { roots: team as ProjectRow[], mixed: false };
}

/** The space the shared vocabulary anchors on: the first TEAM space, or none. */
export function firstTeamSpaceId(spaces: readonly ProjectRow[]): string | null {
  return spaces.find((s) => !isPersonalNode(s))?.id ?? null;
}
