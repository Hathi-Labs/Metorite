/**
 * genUiFallback — what a generative-UI node draws when it draws nothing, or
 * when the renderer does not know it. Pure, so it is tested without a DOM.
 *
 * ## Two defects it removes (2026-10-07)
 *
 * 1. **An empty node drew a frame.** A `callout` with no title and no text
 *    drew its border and its padding around nothing. A `stack` of empty texts
 *    took its gaps. {@link isEmptyNode} says when a node holds nothing, and
 *    the renderer then draws nothing.
 * 2. **An unknown type said "unsupported UI element: tree"** in a dashed box.
 *    That is the renderer's word, not the member's, and it hid the content
 *    the model sent. {@link treeItems} reads a `tree` as nested items, and
 *    {@link fallbackLines} reads the words of any other unknown node. The
 *    tool now refuses an unknown type (`genui_refusal` in `write_artifact.py`),
 *    so this path draws only rows that were saved before the refusal.
 *
 * Fence: `src/lib/genUiFallback.test.ts`.
 */

import { tableColumns, text } from "@/lib/genUiText";

/** A node as the renderer reads it. Mirrors `GenUINode`, without the import
 *  cycle back into the component. */
export interface FallbackNode {
  type?: unknown;
  props?: Record<string, unknown>;
  children?: unknown[];
}

/** One item of a tree, with its nested items. */
export interface TreeItem {
  label: string;
  children: TreeItem[];
}

/** The renderer's depth guard (`Node` in `GenerativeUINode.tsx`). */
const MAX_DEPTH = 20;
/** A tree deeper or wider than this is cut, so a looping tree cannot hang. */
const TREE_DEPTH = 8;
const TREE_ITEMS = 200;
/** The most lines the neutral fallback shows. */
const FALLBACK_LINES = 12;

const asRecord = (v: unknown): Record<string, unknown> =>
  v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {};

const blank = (v: unknown) => !text(v).trim();

/**
 * True when a node draws nothing a member can read or use. The renderer then
 * returns `null` for it, so an empty node takes no frame and no gap.
 *
 * A `divider` is never empty: the line is its content. A `template`, an
 * `html` and a `react` node are empty only with no name or no code, because
 * their content is not in the tree.
 */
export function isEmptyNode(node: unknown, depth = 0): boolean {
  if (depth > MAX_DEPTH) return true;
  if (!node || typeof node !== "object") return true;
  const n = node as FallbackNode;
  const type = text(n.type);
  const props = asRecord(n.props);
  const kids = Array.isArray(n.children) ? n.children : [];
  const kidsEmpty = kids.every((k) => isEmptyNode(k, depth + 1));
  switch (type) {
    case "text":
    case "heading":
    case "markdown":
    case "code":
    case "badge":
      return blank(props.text);
    case "link":
      return blank(props.text) && blank(props.href);
    case "list":
      return !(Array.isArray(props.items) && props.items.some((i) => !blank(i)));
    case "keyValue":
      return !(Array.isArray(props.pairs) && props.pairs.length > 0);
    case "table":
      // The renderer's own column reader, so a `{header}` column counts: a
      // "no results" table still draws its header row.
      return !(Array.isArray(props.rows) && props.rows.length > 0)
        && !tableColumns(props.columns, []).some((c) => c.label.trim());
    case "card":
      return blank(props.title) && kidsEmpty;
    case "callout":
      return blank(props.title) && blank(props.text) && kidsEmpty;
    case "stack":
    case "row":
      return kidsEmpty;
    case "button":
      return blank(props.label) && blank(props.action);
    case "icon":
      return blank(props.name) && blank(props.label);
    case "template":
      return blank(props.name);
    case "html":
      return blank(props.code ?? props.html);
    case "react":
      return blank(props.code);
    case "divider":
      return false;
    default:
      // An unknown type is empty when the fallback would find no words in it.
      return treeItems(n).length === 0 && fallbackLines(n).length === 0;
  }
}

/** The nested items of one tree item: the names models reach for. */
function childList(item: Record<string, unknown>): unknown[] {
  for (const key of ["children", "items", "nodes", "subtasks", "tasks"]) {
    if (Array.isArray(item[key])) return item[key] as unknown[];
  }
  return [];
}

function toItems(list: unknown[], depth: number, budget: { left: number }): TreeItem[] {
  const out: TreeItem[] = [];
  if (depth > TREE_DEPTH) return out;
  for (const raw of list) {
    if (budget.left <= 0) break;
    const label = text(raw).trim();
    const kids = typeof raw === "object" && raw ? childList(asRecord(raw)) : [];
    if (!label && kids.length === 0) continue;
    budget.left -= 1;
    out.push({ label: label || "Untitled", children: toItems(kids, depth + 1, budget) });
  }
  return out;
}

/**
 * A `tree` node as nested items, or `[]` when it is not a tree.
 *
 * Models put the items in `props.items`, `props.nodes`, `props.data`,
 * `props.tree` or `props.root`, or in the node's own `children`. Each item
 * names itself under `label`, `title`, `name` or `text`, and nests under
 * `children`, `items`, `nodes`, `subtasks` or `tasks`.
 */
export function treeItems(node: FallbackNode): TreeItem[] {
  if (!/tree|hierarchy/i.test(text(node.type))) return [];
  const props = asRecord(node.props);
  const data = props.data;
  const candidates: unknown[] = [
    props.items,
    props.nodes,
    Array.isArray(data) ? data : asRecord(data).items,
    props.tree,
    props.root ? [props.root] : undefined,
    node.children,
  ];
  const list = candidates.find((c) => Array.isArray(c) && c.length > 0) as unknown[] | undefined;
  return list ? toItems(list, 0, { left: TREE_ITEMS }) : [];
}

/** The words of an unknown node, for the neutral fallback. Title first. */
export function fallbackLines(node: FallbackNode): string[] {
  const out: string[] = [];
  const seen = new Set<string>();
  const push = (v: unknown) => {
    const t = text(v).trim();
    if (t && !seen.has(t) && out.length < FALLBACK_LINES) {
      seen.add(t);
      out.push(t);
    }
  };
  const walk = (v: unknown, depth: number) => {
    if (depth > 4 || out.length >= FALLBACK_LINES || v == null) return;
    if (typeof v === "string" || typeof v === "number") return push(v);
    if (Array.isArray(v)) return v.forEach((x) => walk(x, depth + 1));
    if (typeof v !== "object") return;
    const o = v as Record<string, unknown>;
    for (const key of ["title", "label", "name", "heading", "text", "value", "description", "body"]) {
      if (typeof o[key] === "string" || typeof o[key] === "number") push(o[key]);
    }
    for (const key of ["props", "data", "items", "rows", "children"]) {
      if (o[key] && typeof o[key] === "object") walk(o[key], depth + 1);
    }
  };
  walk(node, 0);
  return out;
}
