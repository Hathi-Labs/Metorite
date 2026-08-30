/**
 * Projects · the project tree, and the Center slice.
 *
 * Spec: `project-docs/specs/project_management_app.md` §5 · D-PM-2.
 *
 * Pure functions. The `?center=` filter in particular is asserted here rather
 * than in a component, because its correctness claim is a *security* one and
 * needs to be checkable without rendering anything.
 */

export interface ProjectNode {
  id: string;
  name: string;
  parent_project_id?: string | null;
  kind?: string | null;
  status?: string | null;
  lead?: string | null;
  children?: ProjectNode[];
}

export type NodeKind = "project" | "folder";

/** A row's kind. Absent/null reads as 'project' (R6 — migration 193). */
export function nodeKind(node: Pick<ProjectNode, "kind">): NodeKind {
  return node.kind === "folder" ? "folder" : "project";
}

export interface ChildOption {
  kind: NodeKind;
  label: string;
}

/**
 * What the + on a row may create (owner directive 2026-08-31):
 *
 *     space (root) → [folder] → project → [folder] → subproject
 *
 * `generation` counts PROJECT levels at the node, itself included — a space
 * is 1, a project 2, a subproject 3; a folder reports its parent's count
 * (folders are transparent to depth). The server enforces the same grammar
 * in `assert_node_grammar`; this table only decides which buttons exist,
 * and the words on them.
 */
export function childCreationOptions(
  kind: NodeKind,
  generation: number
): ChildOption[] {
  if (kind === "folder") {
    if (generation === 1) return [{ kind: "project", label: "New project" }];
    if (generation === 2) return [{ kind: "project", label: "New subproject" }];
    return [];
  }
  if (generation === 1) {
    return [
      { kind: "project", label: "New project" },
      { kind: "folder", label: "New folder" },
    ];
  }
  if (generation === 2) {
    return [
      { kind: "project", label: "New subproject" },
      { kind: "folder", label: "New folder" },
    ];
  }
  return [];
}

export interface ProjectGrant {
  project_id: string;
  subject: string;
}

/** Every id in a subtree, the node itself included. */
export function subtreeIds(node: ProjectNode): string[] {
  const out = [node.id];
  for (const child of node.children ?? []) out.push(...subtreeIds(child));
  return out;
}

/** Flatten a forest to `[node, depth]` pairs, for a sidebar that indents. */
export function flatten(
  nodes: readonly ProjectNode[],
  depth = 0
): Array<{ node: ProjectNode; depth: number }> {
  const out: Array<{ node: ProjectNode; depth: number }> = [];
  for (const node of nodes) {
    out.push({ node, depth });
    out.push(...flatten(node.children ?? [], depth + 1));
  }
  return out;
}

/**
 * The roots a Center's slice shows: those granted to `group:<slug>`.
 *
 * ⚠️ **Presentation only.** This narrows what the sidebar renders for a Center
 * link; it is NOT the access boundary. The server's grant model already
 * decided which projects came back at all, so a hand-edited `?center=` shows
 * nothing the caller could not already reach — it just shows a different
 * subset of it. Treating this as the boundary is the mistake `R9` exists to
 * name: hiding a control is a courtesy, the server check is the control.
 *
 * An unknown or absent slug returns the whole forest rather than an empty one:
 * a typo'd Center should look like "no filter", never like "you have nothing".
 */
export function filterByCenter(
  roots: readonly ProjectNode[],
  grants: readonly ProjectGrant[],
  centerSlug: string | null | undefined
): ProjectNode[] {
  const slug = (centerSlug ?? "").trim().toLowerCase();
  if (!slug) return [...roots];

  const subject = `group:${slug}`;
  const granted = new Set(
    grants
      .filter((g) => (g.subject ?? "").toLowerCase() === subject)
      .map((g) => g.project_id)
  );
  if (granted.size === 0) return [...roots];

  return roots.filter((root) => subtreeIds(root).some((id) => granted.has(id)));
}

/** A project and its ancestors, for a breadcrumb. */
export function pathTo(
  roots: readonly ProjectNode[],
  projectId: string
): ProjectNode[] {
  for (const root of roots) {
    if (root.id === projectId) return [root];
    const below = pathTo(root.children ?? [], projectId);
    if (below.length) return [root, ...below];
  }
  return [];
}

/**
 * Refuse a move that would put a project inside its own subtree.
 *
 * The server refuses this too (a depth-bounded ancestor walk). Checking here as
 * well is not redundancy for its own sake: it stops the UI from optimistically
 * rendering a tree that cannot exist, then snapping back on the 422.
 */
export function canMoveUnder(
  roots: readonly ProjectNode[],
  projectId: string,
  newParentId: string | null
): boolean {
  if (newParentId === null) return true;
  if (newParentId === projectId) return false;
  const path = pathTo(roots, projectId);
  const node = path[path.length - 1];
  if (!node) return true;
  return !subtreeIds(node).includes(newParentId);
}

/* ── Effective run state (WS-27bg / D-PM-26) ───────────────────────────────
 *
 * A project's run state governs its whole subtree, the same way a
 * `pm_project_grants` row on a root covers everything below it. So a
 * subproject of a paused department is *effectively paused* even though its own
 * column still says `active` — and nothing is written to it to make that true,
 * which is D-PM-26's whole point.
 *
 * ⚠️ Status is writable at ANY depth here, unlike the lifecycle policy, which
 * is root-only and 422s on a child (migration 166). The distinction is real: a
 * policy is *configuration* and one per root is the right shape, while a run
 * state is a fact about a unit of work, and a subproject is a unit of work. A
 * firmware sub-project can legitimately pause while its siblings ship.
 */

/**
 * How restrictive each run state is. The effective state of a node is the MOST
 * restrictive value on its ancestor chain, itself included.
 *
 * The ordering is a judgement and is written down rather than left implicit:
 * `stopped` outranks `on_hold` because abandonment outranks a pause; both
 * outrank `queued`, which outranks `done`, which outranks `active`. The two
 * ends are the ones that matter and neither is arguable — `active` is the only
 * state in which work flows, and `stopped` is the only one from which it does
 * not return. The middle order only decides which *word* a nested node shows,
 * never whether it runs.
 */
const RESTRICTION: Record<string, number> = {
  active: 0,
  done: 1,
  queued: 2,
  on_hold: 3,
  stopped: 4,
};

function rank(state?: string | null): number {
  const key = state?.trim().toLowerCase() ?? "";
  // An unknown value ranks as `active` (0) rather than as maximally
  // restrictive: a client that has not learned a newer state must not silently
  // grey out a whole subtree it does not understand.
  return RESTRICTION[key] ?? 0;
}

/** The more restrictive of two run states — `a` when they tie. */
export function moreRestrictive(
  a?: string | null,
  b?: string | null
): string {
  const left = (a ?? "active").trim().toLowerCase() || "active";
  const right = (b ?? "active").trim().toLowerCase() || "active";
  return rank(right) > rank(left) ? right : left;
}

/** What a node's own state would be, defaulted. */
export function ownState(node: Pick<ProjectNode, "status">): string {
  const value = node.status?.trim().toLowerCase();
  return value || "active";
}

/**
 * The state a node actually behaves as, given the chain above it.
 *
 * `inherited` is the effective state of the PARENT (pass `null` at a root).
 * Returning the pair rather than one value is what lets the tree draw an
 * inherited state differently from a chosen one: a node showing "Paused"
 * because somebody paused *it* and a node showing "Paused" because its
 * department is paused are different facts, and a reader who cannot tell them
 * apart will go looking for the pause on the wrong row.
 */
export function effectiveState(
  node: Pick<ProjectNode, "status">,
  inherited?: string | null
): { state: string; inherited: boolean } {
  const own = ownState(node);
  const state = moreRestrictive(own, inherited);
  return { state, inherited: state !== own };
}
