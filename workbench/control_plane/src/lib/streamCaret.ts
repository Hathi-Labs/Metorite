/**
 * The streaming caret — where it may draw, as a pure rehype plugin.
 *
 * ## The defect it removes (2026-10-07)
 *
 * `MarkdownMessage` drew the caret as a span AFTER the Markdown body. The
 * body ends in a block (a paragraph, a list, a code block), so the span
 * started a new line of its own. A member saw a lone thin bar "|" between the
 * answer and the next card, for as long as the turn ran. The reasoning entry
 * in `ThinkingContainer` had the same span, with the same effect.
 *
 * ## The rule
 *
 * The caret goes INSIDE the last text block, so it sits at the end of the
 * last line of words. It descends through lists, quotes and tables to the
 * last block that holds text. When the answer ends in something that is not
 * text (a code block, a rule, an image), there is no caret at all, because no
 * line exists for it to sit on. The thinking trail and the composer's working
 * bar already say that the turn is running.
 *
 * Fence: `src/lib/streamCaret.test.ts`.
 */

/** The hast shapes this plugin reads. Kept local: it reads four fields. */
export interface HastText {
  type: "text";
  value: string;
}
export interface HastElement {
  type: "element";
  tagName: string;
  properties?: Record<string, unknown>;
  children: HastNode[];
}
export interface HastParent {
  type: "root" | "element";
  children: HastNode[];
}
export type HastNode = HastText | HastElement | { type: string; children?: HastNode[] };

/** The caret's look. Tokens only: `bg-zinc-300` was here, and it did not
 *  follow the colour mode. */
export const CARET_CLASS =
  "stream-caret inline-block w-[2px] h-[1em] bg-muted-foreground/60 animate-pulse ml-0.5 align-middle rounded-full";

/** Blocks the caret looks inside for their last block. */
const CONTAINERS = new Set([
  "ul", "ol", "li", "blockquote", "table", "thead", "tbody", "tr", "td", "th",
]);

/** Blocks whose last line of words can carry the caret. */
const TEXT_HOSTS = new Set(["p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "td", "th"]);

/** Elements that start a block. Anything else (a `strong`, an `a`, a
 *  `code`, an entity pill) is inline and ends a line of words. */
const BLOCKS = new Set([
  ...CONTAINERS, "p", "h1", "h2", "h3", "h4", "h5", "h6", "pre", "hr", "div", "img",
]);

function isElement(n: HastNode | undefined): n is HastElement {
  return !!n && n.type === "element";
}

/** The last child that draws: an element, or text that is not only spaces. */
function lastMeaningful(node: { children?: HastNode[] }): HastNode | undefined {
  const kids = node.children ?? [];
  for (let i = kids.length - 1; i >= 0; i--) {
    const k = kids[i];
    if (isElement(k)) return k;
    if (k.type === "text" && (k as HastText).value.trim()) return k;
  }
  return undefined;
}

/** True when an element's last drawn child is words or inline, not a block. */
function endsInline(el: HastElement): boolean {
  const last = lastMeaningful(el);
  if (!last) return false;
  if (last.type === "text") return true;
  return isElement(last) && !BLOCKS.has(last.tagName);
}

/**
 * The element that receives the caret, or `null` when the content ends in
 * something that is not text. Pure: it reads the tree and changes nothing.
 */
export function caretHost(node: { children?: HastNode[] }, depth = 0): HastElement | null {
  if (depth > 12) return null;
  const last = lastMeaningful(node);
  if (!isElement(last)) return null;
  if (CONTAINERS.has(last.tagName)) {
    const inner = caretHost(last, depth + 1);
    if (inner) return inner;
    return TEXT_HOSTS.has(last.tagName) && endsInline(last) ? last : null;
  }
  return TEXT_HOSTS.has(last.tagName) ? last : null;
}

/** The caret as a hast node. `aria-hidden`: it is decoration. */
export function caretNode(): HastElement {
  return {
    type: "element",
    tagName: "span",
    properties: { className: CARET_CLASS.split(" "), ariaHidden: "true" },
    children: [],
  };
}

/** The rehype plugin. Pass it only while the text streams. */
export default function rehypeStreamCaret() {
  return (tree: HastParent) => {
    const host = caretHost(tree);
    if (host) host.children.push(caretNode());
  };
}
