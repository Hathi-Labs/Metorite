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
import { PERSONAL_NODE_NAME, displayNodeName, isPersonalRoot } from "./tree";

/** The heading of the section. The lock icon is drawn beside it. */
export const PERSONAL_SECTION_LABEL = "Personal";

/** The tooltip of the lock. */
export const PERSONAL_SECTION_HINT = "Private to you";

const NONE: ProjectRow[] = [];

/** The one name of the private space (D-OP-2, OP-12b). Defined in `tree.ts`, the leaf module. */
export { PERSONAL_NODE_NAME, displayNodeName, isPersonalRoot };

/** The line under the title of the private space. */
export const PERSONAL_SPACE_NOTE =
  "Only you can see this. These tasks also show in My tasks.";

/** The accessible label of the lock on a private task in My tasks. */
export const PRIVATE_TASK_LABEL = "Private";

/**
 * The id of the member's personal root as the page last loaded it. A surface
 * that prints a server `project_name` (Needs attention, Triage, Analytics,
 * Reports) has no tree, so it asks the seam through `displayRowProject`. The
 * page sets this with `rememberPersonalRoot`. The match is by ID only: a team
 * project that is named "My tasks" keeps its name.
 */
let rememberedRootId: string | null = null;

export function rememberPersonalRoot(roots: readonly ProjectRow[] | null | undefined): void {
  rememberedRootId = (roots ?? []).find((n) => isPersonalRoot(n))?.id ?? null;
}

/**
 * The name to print for a row that carries a project id and a stored name (a
 * search hit, a needs-attention row). `personalRootId` is the id of the
 * member's personal root, or defaults to the one the page remembered.
 */
export function displayProjectName(
  projectId: string | null | undefined,
  storedName: string | null | undefined,
  personalRootId?: string | null,
): string | null {
  const rootId = personalRootId ?? rememberedRootId;
  if (projectId && rootId && projectId === rootId) return PERSONAL_NODE_NAME;
  return storedName ?? null;
}

/** `displayProjectName` for a server row. */
export function displayRowProject(
  row: { project_id?: string | null; project_name?: string | null },
): string | null {
  return displayProjectName(row.project_id, row.project_name);
}

/** The roots of the Personal section: the caller's own tree, or none when the flag is off. */
export function personalRoots(
  data: { personal?: ProjectRow[] | null } | null | undefined,
  enabled: boolean,
): ProjectRow[] {
  if (!enabled) return NONE;
  const roots = data?.personal ?? NONE;
  // The root carries its display name, so every reader of the tree prints it.
  return roots.map((root) =>
    isPersonalRoot(root) && root.name !== PERSONAL_NODE_NAME
      ? { ...root, name: displayNodeName(root) }
      : root,
  );
}

/** Every live folder and project under a personal root, at any depth. A filed one and its subtree do not count. */
export function personalPlaceCount(root: Pick<ProjectRow, "children">): number {
  let n = 0;
  for (const child of root.children ?? []) {
    if (child.archived_at) continue;
    n += 1 + personalPlaceCount(child);
  }
  return n;
}

/**
 * The line under the PERSONAL heading when the member made nothing there
 * (owner, 2026-10-30). The "+" of the section creates a "New folder" or a
 * "New project" (`childCreationOptions`), so the line names both. Null once
 * the root has any child, archived ones included.
 */
export const PERSONAL_EMPTY_LINE = "No personal folders or projects yet.";
export function personalEmptyLine(personal: readonly ProjectRow[]): string | null {
  const { root, rows } = personalHeading(personal);
  return root && rows.length === 0 ? PERSONAL_EMPTY_LINE : null;
}

/**
 * What the sidebar draws under the PERSONAL heading (OP-12b). The heading IS
 * the first root, so that root never draws as a row. Its children draw
 * directly under the heading. A member holds one root (a partial unique index
 * on `personal_owner`), but a second root would be a node the heading cannot
 * stand for. It stays a row, so nothing is hidden.
 */
export function personalHeading(personal: readonly ProjectRow[]): {
  root: ProjectRow | null;
  rows: ProjectRow[];
} {
  const root = personal.find((n) => isPersonalRoot(n)) ?? null;
  if (!root) return { root: null, rows: personal as ProjectRow[] };
  const others = personal.filter((n) => n !== root);
  return { root, rows: [...(root.children ?? []), ...others] };
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
