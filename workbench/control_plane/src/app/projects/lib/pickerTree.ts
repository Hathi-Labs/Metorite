/**
 * The project picker's model — browse a collapsed tree, or search it.
 *
 * Owner ask, 2026-10-10: the Move dialog drew every node of every space as one
 * open list, with "a folder holds projects, not tasks" repeated on each folder.
 * At customer zero's size that is sixty rows to read to find one. The answer is
 * the standard one for a hierarchy you pick from: a search box over a tree
 * whose spaces start CLOSED, and a search result that names its path.
 *
 * ## One rule for "may a task land here"
 *
 * Built on `destinations()`, never beside it. That function is what the Move
 * dialog, Clarify and the capture chip already agree on, and a second copy of
 * the folder rule here would be the second vocabulary `AGENTS.md` rule 4
 * refuses. `pickerTree.test.ts` pins that the two agree on every node.
 *
 * A picker that places something ELSE passes its own rule. The project "Move
 * to…" dialog passes `moveRefusal` (owner, 2026-10-10: the same wall of text
 * there), because a folder may take a project where it may not take a task.
 * The rule returns the REASON a row is refused, never a bare boolean, so the
 * picker can say why in place.
 *
 * ## Pure on purpose
 *
 * `vitest.config.ts` runs in `node`, so no JSX assertion is available. Every
 * decision the picker makes — which rows show, which match, which open first —
 * is a function here, and the component only draws. The arrow keys walk with
 * `lib/cursor.ts`, the grammar both apps' lists already share.
 */

import { destinations } from "./destinations";
import { type NodeLevel, type ProjectNode, nodeKind, nodeLevel } from "./tree";

export interface PickerNode {
  node: ProjectNode;
  id: string;
  name: string;
  /** The indent in the browse tree. Folders count, as they do on screen. */
  depth: number;
  /** The LEVEL, for the icon. Folders are transparent to it, as in `levelOf`. */
  level: NodeLevel;
  /** The thing being placed may land here: `refusal` is null. */
  pickable: boolean;
  /** Why it may not, in the words the server uses. Null when it may. */
  refusal: string | null;
  parentId: string | null;
  /** The ancestors' names, top first — the breadcrumb a search result shows. */
  path: string[];
  hasChildren: boolean;
}

/** The task rule's refusal, for a folder. `destinations` decides which rows. */
export const FOLDER_HOLDS_NO_TASKS = "A folder holds projects, not tasks.";

/**
 * Why a row may not take the thing being placed, or null when it may. The
 * default is the task rule (`destinations`).
 */
export type PickRule = (node: ProjectNode) => string | null;

/** Every node, in tree order, with what the picker needs to draw and search it. */
export function pickerNodes(roots: readonly ProjectNode[], rule?: PickRule): PickerNode[] {
  const out: PickerNode[] = [];
  // The open ancestors at each depth, as `destinations` walks down.
  const stack: PickerNode[] = [];
  for (const row of destinations(roots)) {
    stack.length = row.depth;
    const parent = stack[row.depth - 1] ?? null;
    const generations =
      stack.filter((n) => nodeKind(n.node) === "project").length +
      (nodeKind(row.node) === "project" ? 1 : 0);
    const refusal = rule ? rule(row.node) : row.legal ? null : FOLDER_HOLDS_NO_TASKS;
    const entry: PickerNode = {
      node: row.node,
      id: row.node.id,
      name: row.node.name,
      depth: row.depth,
      level: nodeLevel(nodeKind(row.node), generations),
      pickable: refusal === null,
      refusal,
      parentId: parent?.id ?? null,
      path: stack.map((n) => n.name),
      hasChildren: (row.node.children?.length ?? 0) > 0,
    };
    out.push(entry);
    stack.push(entry);
  }
  return out;
}

/**
 * The rows the browse tree shows: a node shows when every ancestor is open.
 *
 * ⚠️ Folders show too, and are not pickable. A folder is the accordion header
 * for the projects inside it, so dropping it would leave its projects nowhere
 * to hang. The component draws it as a header that opens, never as a row with
 * an excuse beside it.
 */
export function visibleRows(
  nodes: readonly PickerNode[],
  expanded: ReadonlySet<string>,
): PickerNode[] {
  const shown = new Set<string>();
  const out: PickerNode[] = [];
  for (const n of nodes) {
    if (n.parentId === null || (shown.has(n.parentId) && expanded.has(n.parentId))) {
      shown.add(n.id);
      out.push(n);
    }
  }
  return out;
}

/**
 * Below this many nodes the tree opens fully. A short tree reads at a glance,
 * and a closed one would cost a click to show a handful of rows.
 */
export const OPEN_ALL_BELOW = 13;

/**
 * Which nodes start open: the path to each focus id (the current pick, a
 * suggestion), or the whole tree when it is short.
 */
export function initialExpanded(
  nodes: readonly PickerNode[],
  focus: readonly (string | null | undefined)[],
): Set<string> {
  if (nodes.length < OPEN_ALL_BELOW) {
    return new Set(nodes.filter((n) => n.hasChildren).map((n) => n.id));
  }
  return ancestorIds(nodes, focus);
}

/**
 * Every ancestor of each id, so the path to it can open.
 *
 * The node itself is left out: picking a space does not need its contents
 * spread out under the pick. Also what the picker adds when a pick or a
 * suggestion arrives AFTER mount, such as a late proposal in Clarify.
 */
export function ancestorIds(
  nodes: readonly PickerNode[],
  ids: readonly (string | null | undefined)[],
): Set<string> {
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const open = new Set<string>();
  for (const id of ids) {
    let at = id ? byId.get(id) : undefined;
    while (at?.parentId) {
      open.add(at.parentId);
      at = byId.get(at.parentId);
    }
  }
  return open;
}

/**
 * Where `token` hits `lower`, preferring a hit at the start of a word.
 *
 * "Reprint print" answers "print" at its second word, not inside "Reprint",
 * so it ranks with the names that start with the word.
 */
function bestHit(lower: string, token: string): { at: number; startsWord: boolean } | null {
  let first: number | null = null;
  for (let at = lower.indexOf(token); at >= 0; at = lower.indexOf(token, at + 1)) {
    if (at === 0 || /[^\p{L}\p{N}]/u.test(lower[at - 1])) return { at, startsWord: true };
    first ??= at;
  }
  return first === null ? null : { at: first, startsWord: false };
}

/** The lower-cased words of a query. Empty for a blank one. */
export function queryTokens(query: string | null | undefined): string[] {
  return (query ?? "").toLowerCase().split(/\s+/).filter(Boolean);
}

/**
 * Does `name`, read under `path`, answer every word of the query?
 *
 * Each word may match the name OR an ancestor, so "fracktory fdm" finds
 * FDM PRINTS under Fracktory, and "fracktory" alone lists everything in it.
 */
export function matchesTokens(tokens: readonly string[], name: string, path: readonly string[] = []): boolean {
  if (tokens.length === 0) return true;
  const hay = [...path, name].join(" ").toLowerCase();
  return tokens.every((t) => hay.includes(t));
}

export interface SearchHit {
  node: PickerNode;
  /** Where the first word that hit the NAME sits in it, for the highlight. */
  mark: [number, number] | null;
}

/**
 * The search results: every PICKABLE node that answers the query, flat.
 *
 * Ranked in three bands and kept in tree order inside each, so the list does
 * not reshuffle on every keystroke: the name starts with a word, then the name
 * contains one, then only the path does. A folder never appears — it holds no
 * tasks, and its projects already match through their path.
 */
export function searchRows(nodes: readonly PickerNode[], query: string): SearchHit[] {
  const tokens = queryTokens(query);
  if (tokens.length === 0) return [];
  const bands: SearchHit[][] = [[], [], []];
  for (const n of nodes) {
    if (!n.pickable || !matchesTokens(tokens, n.name, n.path)) continue;
    const lower = n.name.toLowerCase();
    let mark: [number, number] | null = null;
    let band = 2;
    for (const t of tokens) {
      const hit = bestHit(lower, t);
      if (!hit) continue;
      const b = hit.startsWord ? 0 : 1;
      if (b < band || mark === null) {
        band = Math.min(band, b);
        mark = [hit.at, hit.at + t.length];
      }
    }
    // A name whose lower case changes its length ("İ" lowers to two code
    // units) would put the highlight in the wrong place. It ranks, unmarked.
    if (lower.length !== n.name.length) mark = null;
    bands[band].push({ node: n, mark });
  }
  return [...bands[0], ...bands[1], ...bands[2]];
}

/** "Fracktory › 3d printing services" — a node's ancestors, for a second line. */
export function pathLabel(path: readonly string[]): string {
  return path.join(" › ");
}
