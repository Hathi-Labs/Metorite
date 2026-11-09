/**
 * Projects · the bar on an archived space, project or subproject (ARC-2a,
 * `one_projects_app.md` §9b.6 rule 1).
 *
 * Pure: it reads the tree and returns the words and the id Restore acts on.
 * The date comes from `shortDate`, the Projects app's own day format, which
 * parses by hand so a floating date never moves a day.
 */
import type { ProjectRow } from "./api";
import { shortDate } from "./outlook";
import { flatten } from "./tree";

export interface ArchiveBar {
  /** The one sentence the bar shows. */
  text: string;
  /** The row whose archive filed this one. Restore unarchives it. */
  rootId: string;
  /** The root row, when the tree holds it. */
  root: ProjectRow | null;
}

/** The bar for `node`, or null when it is not archived. */
export function archiveBar(
  node: ProjectRow | null | undefined,
  roots: readonly ProjectRow[]
): ArchiveBar | null {
  if (!node?.archived_at) return null;
  const rootId = node.archived_root_id || node.id;
  const root =
    rootId === node.id
      ? node
      : ((flatten(roots).find((e) => e.node.id === rootId)?.node as
          | ProjectRow
          | undefined) ?? null);
  const date = shortDate(node.archived_at);
  const text =
    rootId !== node.id && root
      ? `Archived with ${root.name} on ${date}. Restore it to change its work.`
      : `Archived on ${date}. Restore it to change its work.`;
  return { text, rootId, root };
}
