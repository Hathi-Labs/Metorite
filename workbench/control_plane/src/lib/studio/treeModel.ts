/**
 * The pure model of the Tree block (`ai_studio.md` §14 D18, slice D18-1).
 *
 * No React, no clock, no random, no network. Each function is deterministic,
 * never throws and never changes its input. A function that answers with a
 * value returns the SAME input reference for a bad request.
 *
 * A root is level 1, as in `read_tree` of the store.
 */

/** The most nodes of one tree read. Twin of `MAX_TREE_NODES` in `objects_store.py`. */
export const MAX_TREE_NODES = 2000;
/** The most levels of a tree. Twin of `MAX_TREE_DEPTH` in `studio_rules.py`. */
export const MAX_TREE_DEPTH = 20;

export interface TreeNodeIn {
  id: string;
  parentId: string | null;
  label?: string;
  data?: Record<string, unknown>;
  /** The parent is not in the list. The node shows as a root. */
  orphan?: boolean;
  /** The node closes a cycle in the data. It shows as a root. */
  cycle?: boolean;
}

export interface TreeRow {
  id: string;
  depth: number;
  hasChildren: boolean;
  expanded: boolean;
  label: string;
  parentId: string | null;
  orphan?: boolean;
  cycle?: boolean;
}

const isObject = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);

/** The first `hierarchy` rule of an object: its name and its parent field. Null when none. */
export function hierarchyField(object: unknown): { rule: string; field: string } | null {
  if (!isObject(object) || !Array.isArray(object.rules)) return null;
  for (const r of object.rules) {
    if (!isObject(r) || r.kind !== "hierarchy") continue;
    if (typeof r.field !== "string" || r.field === "") return null;
    return { rule: typeof r.name === "string" ? r.name : "", field: r.field };
  }
  return null;
}

/** The parent id of a relation value: a string id, `{id}` (the door shape), or none. */
function parentOf(v: unknown): string | null {
  if (typeof v === "string") return v === "" ? null : v;
  if (isObject(v) && typeof v.id === "string" && v.id !== "") return v.id;
  return null;
}

/** Flat rows to nodes. An orphan stays as a root. A cycle breaks at its first node in the input. */
export function buildTree(
  rows: ReadonlyArray<Record<string, unknown>>,
  field: string,
  labelOf: (row: Record<string, unknown>) => string,
): TreeNodeIn[] {
  const out: TreeNodeIn[] = [];
  const index = new Map<string, number>();
  if (!Array.isArray(rows)) return out;
  for (const row of rows) {
    if (!isObject(row) || typeof row.id !== "string" || row.id === "" || index.has(row.id)) continue;
    let label = "";
    try { label = String(labelOf(row) ?? ""); } catch { label = ""; }
    index.set(row.id, out.length);
    out.push({ id: row.id, parentId: parentOf(row[field]), label: label === "" ? row.id : label, data: row });
  }
  for (const n of out) {
    if (n.parentId !== null && !index.has(n.parentId)) n.orphan = true;
  }
  // Break the cycles. Walk up from each node. A walk that meets its own path found a cycle.
  const state = new Uint8Array(out.length); // 0 new, 1 on the path, 2 done
  for (let start = 0; start < out.length; start++) {
    if (state[start] !== 0) continue;
    const path: number[] = [];
    let at = start;
    for (;;) {
      if (state[at] === 2) break;
      if (state[at] === 1) {
        const members = path.slice(path.indexOf(at));
        const first = Math.min(...members);
        out[first].parentId = null;
        out[first].cycle = true;
        break;
      }
      state[at] = 1;
      path.push(at);
      const p = out[at].parentId;
      const next = p === null ? undefined : index.get(p);
      if (next === undefined) break;
      at = next;
    }
    for (const i of path) state[i] = 2;
  }
  return out;
}

interface Index {
  byId: Map<string, TreeNodeIn>;
  children: Map<string | null, TreeNodeIn[]>;
}

/** The first node of a duplicate id wins. A node with an unknown parent counts as a root. */
function indexOf(nodes: ReadonlyArray<TreeNodeIn>): Index {
  const byId = new Map<string, TreeNodeIn>();
  const children = new Map<string | null, TreeNodeIn[]>();
  if (!Array.isArray(nodes)) return { byId, children };
  for (const n of nodes) if (n && typeof n.id === "string" && !byId.has(n.id)) byId.set(n.id, n);
  for (const n of byId.values()) {
    const key = n.parentId !== null && byId.has(n.parentId) && n.parentId !== n.id ? n.parentId : null;
    const list = children.get(key);
    if (list) list.push(n);
    else children.set(key, [n]);
  }
  return { byId, children };
}

/**
 * The order of two labels: not case-sensitive, and a run of digits counts as a number.
 * "Part 2" comes before "Part 10", and "Module 01.03" before "Module 01.10".
 */
export function compareLabels(a: string, b: string): number {
  return a.localeCompare(b, "en", { numeric: true, sensitivity: "base" });
}

/** The visible rows, depth first. A collapsed node hides its children. Iterative and O(n). */
export function treeRows(
  nodes: ReadonlyArray<TreeNodeIn>,
  expanded: ReadonlySet<string>,
  opts?: { sort?: "input" | "label" },
): TreeRow[] {
  const { children } = indexOf(nodes);
  const sorted = (list: TreeNodeIn[] | undefined): TreeNodeIn[] => {
    const l = list ?? [];
    if (opts?.sort !== "label") return l;
    return [...l].sort((a, b) => compareLabels(a.label ?? a.id, b.label ?? b.id));
  };
  const rows: TreeRow[] = [];
  const stack: Array<{ node: TreeNodeIn; depth: number }> = [];
  const push = (list: TreeNodeIn[], depth: number) => {
    for (let i = list.length - 1; i >= 0; i--) stack.push({ node: list[i], depth });
  };
  push(sorted(children.get(null)), 0);
  while (stack.length > 0) {
    const { node, depth } = stack.pop()!;
    const kids = children.get(node.id);
    const open = expanded.has(node.id);
    const row: TreeRow = {
      id: node.id, depth, hasChildren: !!kids && kids.length > 0, expanded: open,
      label: node.label ?? node.id, parentId: node.parentId,
    };
    if (node.orphan) row.orphan = true;
    if (node.cycle) row.cycle = true;
    rows.push(row);
    if (open && kids) push(sorted(kids), depth + 1);
  }
  return rows;
}

/** The set that opens the first `levels` levels: the nodes at depth 0 to `levels - 1`. */
export function initialExpanded(nodes: ReadonlyArray<TreeNodeIn>, levels = 1): Set<string> {
  const out = new Set<string>();
  const { children } = indexOf(nodes);
  let layer = children.get(null) ?? [];
  for (let d = 0; d < levels && layer.length > 0; d++) {
    const next: TreeNodeIn[] = [];
    for (const n of layer) {
      const kids = children.get(n.id);
      if (!kids || kids.length === 0) continue;
      out.add(n.id);
      next.push(...kids);
    }
    layer = next;
  }
  return out;
}

export type MoveRefusalReason = "self" | "descendant" | "too_deep" | "unknown_node" | "unknown_parent" | "same_parent";
export type MoveCheck = { ok: true } | { ok: false; reason: MoveRefusalReason };

/** The levels from the node to its deepest leaf, counting the node: a leaf is 1. Bounded by the node count. */
function subtreeLevels(id: string, children: Map<string | null, TreeNodeIn[]>, limit: number): number {
  let layer = [id];
  let levels = 0;
  let seen = 0;
  while (layer.length > 0 && seen <= limit) {
    levels++;
    const next: string[] = [];
    for (const k of layer) {
      seen++;
      for (const c of children.get(k) ?? []) next.push(c.id);
    }
    layer = next;
  }
  return levels;
}

/** May node `id` move under `newParent` (null is the top level)? */
export function canMove(nodes: ReadonlyArray<TreeNodeIn>, id: string, newParent: string | null): MoveCheck {
  const { byId, children } = indexOf(nodes);
  const node = byId.get(id);
  if (!node) return { ok: false, reason: "unknown_node" };
  if (newParent !== null && !byId.has(newParent)) return { ok: false, reason: "unknown_parent" };
  if (newParent === id) return { ok: false, reason: "self" };
  if (node.parentId === newParent) return { ok: false, reason: "same_parent" };
  if (newParent === null) return { ok: true };
  // Walk up from the new parent. Bounded, so a cycle in the data cannot loop.
  let level = 0;
  let at: string | null = newParent;
  for (let step = 0; at !== null && step <= byId.size; step++) {
    if (at === id) return { ok: false, reason: "descendant" };
    level++;
    at = byId.get(at)?.parentId ?? null;
  }
  if (level + subtreeLevels(id, children, byId.size) > MAX_TREE_DEPTH) return { ok: false, reason: "too_deep" };
  return { ok: true };
}

/** One plain sentence for each refusal. */
export function moveRefusal(reason: MoveRefusalReason): string {
  switch (reason) {
    case "self": return "A node cannot move under itself.";
    case "descendant": return "A node cannot move under one of its own children.";
    case "too_deep": return `The tree cannot go deeper than ${MAX_TREE_DEPTH} levels.`;
    case "unknown_node": return "This node is not in the tree.";
    case "unknown_parent": return "The new parent is not in the tree.";
    case "same_parent": return "The node is already there.";
    default: return "This move is not allowed.";
  }
}

/**
 * A short reason for one line under a disabled option in the Move menu. The full sentence
 * (`moveRefusal`) stays for the alert. A long sentence widened the menu past a 320 px phone.
 */
export function menuReason(reason: MoveRefusalReason): string {
  switch (reason) {
    case "self": return "This is the node itself.";
    case "descendant": return "It is inside this node.";
    case "too_deep": return `Too deep: ${MAX_TREE_DEPTH} levels.`;
    case "unknown_node": return "Node not found.";
    case "unknown_parent": return "Parent not found.";
    case "same_parent": return "It is already there.";
    default: return "Not available.";
  }
}

/**
 * The call for the update action: `door.run("object:<name>.update", movePatch(...))`.
 * A relation value is `{id}` (`fieldCheck.ts` `relation`). A move to the top level
 * sends `null`: `fieldCheck` reads null as empty, and `tree_parent` in
 * `studio_rules.py` reads a value with no `{id}` as a root. `boardMove.ts` also
 * sends `null` for "no value".
 */
export function movePatch(field: string, id: string, newParent: string | null): { id: string; data: Record<string, unknown> } {
  return { id, data: { [field]: newParent === null ? null : { id: newParent } } };
}

/** The nodes with the new parent of `id`, for an optimistic screen. The same array when the move is refused. */
export function applyMove(nodes: ReadonlyArray<TreeNodeIn>, id: string, newParent: string | null): ReadonlyArray<TreeNodeIn> {
  if (!canMove(nodes, id, newParent).ok) return nodes;
  return nodes.map((n) => {
    if (n.id !== id) return n;
    const { orphan: _o, cycle: _c, ...rest } = n;
    void _o; void _c;
    return { ...rest, parentId: newParent };
  });
}
