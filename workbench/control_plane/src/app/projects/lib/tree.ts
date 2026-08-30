/**
 * Projects · the project tree, and the Center slice.
 *
 * Spec: `project-docs/specs/project_management_app.md` §5 · D-PM-2.
 *
 * Pure functions. The `?center=` filter in particular is asserted here rather
 * than in a component, because its correctness claim is a *security* one and
 * needs to be checkable without rendering anything.
 */

import { CATEGORICAL_SLOTS, hashSlot } from "@/lib/categorical";

export interface ProjectNode {
  id: string;
  name: string;
  parent_project_id?: string | null;
  kind?: string | null;
  /** Migration 194 — a themed icon NAME and a ramp SLOT (1..8), never a colour. */
  icon?: string | null;
  icon_slot?: number | null;
  status?: string | null;
  lead?: string | null;
  children?: ProjectNode[];
}

export type NodeKind = "project" | "folder";

/** A row's kind. Absent/null reads as 'project' (R6 — migration 193). */
export function nodeKind(node: Pick<ProjectNode, "kind">): NodeKind {
  return node.kind === "folder" ? "folder" : "project";
}

/**
 * The four LEVELS a node occupies. Derived from kind plus position, never
 * stored — a stored level and the tree could disagree, and the tree is the
 * fact. Mirrors `core.node_level` on the server.
 */
export type NodeLevel = "space" | "folder" | "project" | "subproject";

export function nodeLevel(kind: NodeKind, generation: number): NodeLevel {
  if (kind === "folder") return "folder";
  if (generation <= 1) return "space";
  return generation === 2 ? "project" : "subproject";
}

/**
 * The levels that own a run state (owner directive 2026-08-31). A space
 * summarises and a folder groups; neither DOES work, so neither starts,
 * pauses or stops. The server refuses the write — this hides the control.
 */
export function hasRunState(level: NodeLevel): boolean {
  return level === "project" || level === "subproject";
}

/**
 * The levels that show a DASHBOARD instead of the project views. A space is
 * not a project (owner directive 2026-08-31): it summarises everything
 * beneath it and has none of a project's views. A folder does the same.
 *
 * A project with subprojects still shows its views — it aggregates the
 * subtree into them rather than replacing them.
 */
export function showsDashboard(level: NodeLevel): boolean {
  return level === "space" || level === "folder";
}

/**
 * What Space Settings offers as an icon (migration 194).
 *
 * A curated list, not the whole registry: a picker showing every glyph is a
 * picker nobody reads to the end, and every name here is verified present
 * in the themed registry — a missing name renders as a hole, and the theme
 * system has no way to warn about one. Listed four to a source line and
 * grouped by domain; the picker draws eight to a row, two domains a line.
 * Widened from 26 on the owner's ask (2026-08-31) — every addition is
 * registry-checked by `tree.test.ts`.
 */
export const SPACE_ICON_CHOICES: readonly string[] = [
  "Boxes", "Layers", "LayoutGrid", "Package",
  "Rocket", "Target", "Flag", "Star",
  "Building2", "Briefcase", "Users", "Globe",
  "Cpu", "Code", "Wrench", "Zap",
  "Palette", "Camera", "Megaphone", "ShoppingCart",
  "BookOpen", "Lightbulb", "Shield", "Truck",
  "Headphones", "Coffee", "Gem", "Puzzle",
  "Factory", "FlaskConical", "Printer", "Hammer",
  "Cog", "Database", "Server", "Cloud",
  "Home", "Landmark", "Mountain", "Waves",
  "Wallet", "CreditCard", "TrendingUp", "Activity",
  "Mail", "Phone", "MessageSquare", "Video",
  "Radio", "Mic", "PenTool", "Newspaper",
  "Calendar", "Clock", "Timer", "Sun",
  "Moon", "Flame", "Car", "Stethoscope",
  "Bot", "Brain", "Sparkles", "Monitor",
];

/** The default glyph for a level, when a space has chosen no icon. */
export const LEVEL_ICONS: Record<NodeLevel, string> = {
  space: "Boxes",
  folder: "Folder",
  // A project and a subproject spend this slot on their run state
  // (`StateDot`), so these are only the fallbacks a non-tree surface uses.
  project: "Kanban",
  subproject: "GitBranch",
};

/**
 * A space's marker: which glyph, and which slot on the categorical ramp.
 *
 * ⚠️ **A SLOT, never a colour** (DESIGN_SYSTEM rule 7). The caller turns the
 * slot into classes with `accentForSlot`, so the hue follows the theme in
 * both light and dark. Returning `#7c3aed` here would be the hardcoded
 * colour that rule exists to refuse.
 *
 * An unset space falls back to the default glyph and a slot HASHED from its
 * own name — stable forever, distinct from its neighbours, and never the
 * grey nothing that makes an unconfigured space look broken. `hashSlot` is
 * 0-based and the column is 1-based, which is the one place those two
 * conventions meet.
 */
export function spaceMarker(
  node: Pick<ProjectNode, "name" | "icon" | "icon_slot">
): { icon: string; slot: number } {
  const chosen = node.icon_slot;
  return {
    icon: node.icon || LEVEL_ICONS.space,
    slot:
      typeof chosen === "number" && chosen >= 1 && chosen <= CATEGORICAL_SLOTS
        ? chosen - 1
        : hashSlot(node.name),
  };
}

export interface ChildOption {
  kind: NodeKind;
  /** The LEVEL's own word — "New subproject", never "New project". */
  label: string;
  /**
   * The level the child will occupy. Carried rather than re-derived by the
   * caller: `kind` cannot express it (a project and a subproject are both
   * `kind: "project"`), and this function is the only place that already
   * knows the generation.
   */
  level: NodeLevel;
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
  const project: ChildOption =
    generation === 1
      ? { kind: "project", label: "New project", level: "project" }
      : { kind: "project", label: "New subproject", level: "subproject" };
  const folder: ChildOption = {
    kind: "folder",
    label: "New folder",
    level: "folder",
  };

  if (generation > 2) return [];
  // A folder offers only the one level it holds; it cannot hold a folder.
  return kind === "folder" ? [project] : [project, folder];
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
 * The level a node occupies within a forest — the answer a surface needs
 * when it holds a row but not its depth.
 *
 * Counts PROJECT ancestors along `pathTo`, so folders are transparent
 * exactly as they are on the server (`core._project_generation`). A node
 * the forest does not contain reports 'space', which is what an
 * unresolvable row looked like before this existed: it is the safest wrong
 * answer, because a space offers no run state and no destructive control.
 */
export function levelOf(
  roots: readonly ProjectNode[],
  projectId: string
): NodeLevel {
  const path = pathTo(roots, projectId);
  if (path.length === 0) return "space";
  const node = path[path.length - 1];
  const generations = path.filter((n) => nodeKind(n) === "project").length;
  return nodeLevel(nodeKind(node), generations);
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
