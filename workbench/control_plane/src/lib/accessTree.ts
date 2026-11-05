/**
 * The project tree of an agent grant, as pure decisions (WS-55 EA-17b).
 *
 * Spec: `project-docs/specs/external_agent_access.md` §10.8 (D-EA-12). The
 * consent page uses it now, and "Change access" (EA-17c) uses it next, so it
 * lives in `src/lib/` and not beside one page. `AccessTree.tsx` only draws it.
 *
 * The grant is two lists of project ids, and the gateway reads them as the
 * NEAREST MARK wins:
 *
 * - `pins`: a pin covers its project and every project beneath it.
 * - `leftOut`: a left-out project and its subtree stay out. A pin beneath it
 *   starts its own reach again.
 *
 * A project that carries no mark takes its nearest marked parent's answer. A
 * project with no marked parent is OUT here, because this model only draws the
 * "Only the ones I pick" choice. "All my projects" sends no list at all.
 *
 * ⚠️ Never send an empty `pins` for the picked choice. The gateway reads no pin
 * as "every project". `projectsReady` refuses that, and so does the page.
 */

/** The most project rows the tree holds. It is a guard, not a product rule. */
export const MAX_TREE_NODES = 500;

/** The gateway caps each list at 50 ids (`_MAX_PINS`). */
export const MAX_MARKS = 50;

export interface ProjectNode {
  id: string;
  name: string;
  depth: number;
  parent: string | null;
  children: string[];
}

export interface ProjectTree {
  /** Every node in tree order: a parent comes before its children. */
  nodes: ProjectNode[];
  byId: Record<string, ProjectNode>;
}

interface RawNode {
  id?: unknown;
  name?: unknown;
  archived_at?: unknown;
  children?: unknown;
}

/**
 * The team projects of `GET /projects/tree`, in tree order.
 *
 * That route never holds a personal project, so every row is a team project.
 * An archived node and its subtree stay out. An unknown shape is no projects.
 */
export function buildProjectTree(raw: unknown, limit = MAX_TREE_NODES): ProjectTree {
  const nodes: ProjectNode[] = [];
  const byId: Record<string, ProjectNode> = {};
  const walk = (list: unknown, depth: number, parent: ProjectNode | null) => {
    if (!Array.isArray(list)) return;
    for (const item of list as RawNode[]) {
      if (nodes.length >= limit) return;
      if (!item || typeof item.id !== "string" || typeof item.name !== "string") continue;
      if (item.archived_at || byId[item.id]) continue;
      const node: ProjectNode = {
        id: item.id,
        name: item.name,
        depth,
        parent: parent ? parent.id : null,
        children: [],
      };
      nodes.push(node);
      byId[node.id] = node;
      if (parent) parent.children.push(node.id);
      walk(item.children, depth + 1, node);
    }
  };
  walk((raw as { rows?: unknown } | null)?.rows, 0, null);
  return { nodes, byId };
}

export interface Selection {
  pins: readonly string[];
  leftOut: readonly string[];
}

export const NO_SELECTION: Selection = { pins: [], leftOut: [] };

type Mark = "pin" | "out" | null;

function markOf(sel: Selection, id: string): Mark {
  if (sel.pins.includes(id)) return "pin";
  if (sel.leftOut.includes(id)) return "out";
  return null;
}

/** The nearest mark above a project, never its own. */
function markAbove(tree: ProjectTree, sel: Selection, id: string): Mark {
  let at = tree.byId[id]?.parent ?? null;
  while (at !== null) {
    const mark = markOf(sel, at);
    if (mark) return mark;
    at = tree.byId[at]?.parent ?? null;
  }
  return null;
}

function subtreeIds(tree: ProjectTree, id: string): Set<string> {
  const out = new Set<string>();
  const walk = (at: string) => {
    out.add(at);
    for (const child of tree.byId[at]?.children ?? []) walk(child);
  };
  walk(id);
  return out;
}

function inTreeOrder(tree: ProjectTree, ids: Iterable<string>): string[] {
  const want = new Set(ids);
  return tree.nodes.filter((n) => want.has(n.id)).map((n) => n.id);
}

/**
 * Tick a project: it and everything beneath it join.
 *
 * The marks below it go, because the new tick covers them. When a pin above
 * already covers it, no new mark is needed. Under a left-out parent, or under
 * no mark, the project becomes a pin (a pin under a left-out parent wins).
 */
export function tickProject(tree: ProjectTree, sel: Selection, id: string): Selection {
  if (!tree.byId[id]) return sel;
  const sub = subtreeIds(tree, id);
  const pins = sel.pins.filter((p) => !sub.has(p));
  const leftOut = sel.leftOut.filter((p) => !sub.has(p));
  if (markAbove(tree, { pins, leftOut }, id) !== "pin") pins.push(id);
  return { pins: inTreeOrder(tree, pins), leftOut: inTreeOrder(tree, leftOut) };
}

/**
 * Untick a project: it and everything beneath it leave.
 *
 * Under a pin, the project goes on the left-out list. A project that only a
 * pin of its own covered just loses that pin. Under a left-out parent, or under
 * no mark, the project is out already, so no mark is needed.
 */
export function untickProject(tree: ProjectTree, sel: Selection, id: string): Selection {
  if (!tree.byId[id]) return sel;
  const sub = subtreeIds(tree, id);
  const pins = sel.pins.filter((p) => !sub.has(p));
  const leftOut = sel.leftOut.filter((p) => !sub.has(p));
  if (markAbove(tree, { pins, leftOut }, id) === "pin") leftOut.push(id);
  return { pins: inTreeOrder(tree, pins), leftOut: inTreeOrder(tree, leftOut) };
}

export type CheckState = "on" | "off" | "partial";

export interface NodeFacts {
  /** The gateway's answer for this project alone. */
  reached: boolean;
  /** Projects in the subtree, this one included, that the agent reaches. */
  inside: number;
  /** Projects in the subtree, this one included. */
  total: number;
  state: CheckState;
  /** The member left this very project out (shows the "left out" tag). */
  leftOutMark: boolean;
}

/** The facts of every project, in one top-down pass and one bottom-up pass. */
export function analyzeTree(tree: ProjectTree, sel: Selection): Record<string, NodeFacts> {
  const facts: Record<string, NodeFacts> = {};
  const reached: Record<string, boolean> = {};
  for (const node of tree.nodes) {
    const mark = markOf(sel, node.id);
    const parentReached = node.parent !== null ? reached[node.parent] : false;
    reached[node.id] = mark === "pin" ? true : mark === "out" ? false : parentReached;
  }
  for (let i = tree.nodes.length - 1; i >= 0; i -= 1) {
    const node = tree.nodes[i];
    let inside = reached[node.id] ? 1 : 0;
    let total = 1;
    for (const child of node.children) {
      inside += facts[child].inside;
      total += facts[child].total;
    }
    const state: CheckState = inside === 0 ? "off" : inside === total ? "on" : "partial";
    facts[node.id] = {
      reached: reached[node.id],
      inside,
      total,
      state,
      leftOutMark: markOf(sel, node.id) === "out",
    };
  }
  return facts;
}

/** A click on a box. A full box unticks. An empty or dashed box ticks everything. */
export function toggleProject(tree: ProjectTree, sel: Selection, id: string): Selection {
  const state = analyzeTree(tree, sel)[id]?.state;
  if (state === undefined) return sel;
  return state === "on" ? untickProject(tree, sel, id) : tickProject(tree, sel, id);
}

/** How many projects the agent reaches under this selection. */
export function selectedCount(tree: ProjectTree, sel: Selection): number {
  const facts = analyzeTree(tree, sel);
  return tree.nodes.filter((n) => facts[n.id].reached).length;
}

/** The ids that no longer exist in the tree never go to the gateway. */
export function cleanSelection(tree: ProjectTree, sel: Selection): Selection {
  return {
    pins: sel.pins.filter((id) => tree.byId[id]),
    leftOut: sel.leftOut.filter((id) => tree.byId[id]),
  };
}

// ── The two choices ─────────────────────────────────────────────────────────

export type ProjectMode = "all" | "pick";

/** The two lists the approval sends. "All my projects" sends none. */
export function projectLists(mode: ProjectMode, sel: Selection): Selection {
  return mode === "all" ? NO_SELECTION : { pins: [...sel.pins], leftOut: [...sel.leftOut] };
}

/**
 * Why the project choice cannot go on, or null when it can.
 *
 * "Only the ones I pick" with nothing ticked would send no pin, and the
 * gateway reads no pin as every project. So the page refuses it.
 */
export function projectsProblem(mode: ProjectMode, tree: ProjectTree | null, sel: Selection): string | null {
  if (mode === "all") return null;
  if (!tree) return "Metorite has not listed your projects yet.";
  if (selectedCount(tree, sel) === 0) return "Pick one project or more, or choose All my projects.";
  if (sel.pins.length > MAX_MARKS || sel.leftOut.length > MAX_MARKS) {
    return `Metorite takes at most ${MAX_MARKS} picks and ${MAX_MARKS} left-out projects. Tick a parent to cover more.`;
  }
  return null;
}

// ── The search ──────────────────────────────────────────────────────────────

/** The tree shows a search box above this many projects. */
export const SEARCH_THRESHOLD = 12;

/** The ids that match the query, with their parents, or null for no query. */
export function searchMatches(tree: ProjectTree, query: string): Set<string> | null {
  const q = query.trim().toLowerCase();
  if (!q) return null;
  const out = new Set<string>();
  for (const node of tree.nodes) {
    if (!node.name.toLowerCase().includes(q)) continue;
    let at: string | null = node.id;
    while (at !== null && !out.has(at)) {
      out.add(at);
      at = tree.byId[at]?.parent ?? null;
    }
  }
  return out;
}

/**
 * The rows to draw. A search shows its matches and their parents, open. With
 * no search, a folded parent hides its children.
 */
export function visibleNodes(
  tree: ProjectTree,
  expanded: ReadonlySet<string>,
  matches: ReadonlySet<string> | null,
): ProjectNode[] {
  const out: ProjectNode[] = [];
  const walk = (id: string) => {
    const node = tree.byId[id];
    if (matches && !matches.has(id)) return;
    out.push(node);
    if (matches || expanded.has(id)) for (const child of node.children) walk(child);
  };
  for (const node of tree.nodes) if (node.parent === null) walk(node.id);
  return out;
}

/** Every parent id, for "Expand all". */
export function parentIds(tree: ProjectTree): string[] {
  return tree.nodes.filter((n) => n.children.length > 0).map((n) => n.id);
}

// ── The summary line ────────────────────────────────────────────────────────

export interface SummaryPart {
  text: string;
  strong?: boolean;
}

export interface GrantFacts {
  /** The VERIFIED host, never the name that the agent calls itself. */
  who: string;
  scopes: readonly string[];
  mode: ProjectMode;
  /** The projects that the agent reaches in "Only the ones I pick". */
  picked: number;
  includePersonal: boolean;
  offline: boolean;
}

/** One line that says what the agent can do, in parts so the page can bold. */
export function grantSummary(f: GrantFacts): SummaryPart[] {
  const read = f.scopes.includes("projects:read");
  const write = f.scopes.includes("projects:write");
  const context = f.scopes.includes("context:read");
  const parts: SummaryPart[] = [{ text: `${f.who} will ` }];
  if (!read && !write && !context) {
    parts.push({ text: "do nothing", strong: true }, { text: " yet. Choose one permission." });
    return parts;
  }
  const verb = write ? "read and change tasks" : read ? "only read tasks" : "read company context only";
  parts.push({ text: verb, strong: true });
  if (read || write) {
    parts.push({ text: " in " });
    if (f.mode === "all") {
      parts.push({ text: "all your team projects" });
    } else {
      parts.push({ text: `${f.picked} ${f.picked === 1 ? "project" : "projects"}`, strong: true });
    }
    if (f.includePersonal) parts.push({ text: " and your " }, { text: "personal", strong: true }, { text: " projects" });
    if (context) parts.push({ text: ". It can also read company context" });
  }
  parts.push({ text: "." });
  if (f.offline) parts.push({ text: " It stays connected until you remove it." });
  return parts;
}

export function summaryText(parts: readonly SummaryPart[]): string {
  return parts.map((p) => p.text).join("");
}
