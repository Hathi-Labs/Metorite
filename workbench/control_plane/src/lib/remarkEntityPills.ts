/**
 * A remark plugin that turns the names in a chat answer into pill nodes.
 *
 * Spec: `project-docs/specs/projects_ai_chat.md` §15 (WS-27bm S9). Owner
 * request, 2026-09-24: bold names in guillemets and blue mailto links read as
 * raw text, "rather than … pills".
 *
 * The Projects assistant keeps the «guillemets» its tools print around a name
 * (`instructions.md`, Rules). This plugin finds them in the Markdown tree:
 *
 * - `«X»` in a text node becomes a pill node. Code and inline code are never
 *   touched, because they are not text nodes.
 * - `**«X»**` loses the bold, so the name is a pill and not bold plus a pill.
 * - `#5 «X»` becomes ONE task pill that carries the number.
 * - A bare email, and remark-gfm's `mailto:` autolink for it, becomes a
 *   person pill. That is the fallback for a model that drops the marks.
 * - A pill never shows the guillemets. A stray mark left in the text is
 *   dropped too.
 *
 * The node is `span[data-entity-pill]` in the HTML tree, so `MarkdownBody`
 * draws it through its `span` component, and nothing here knows React.
 * `entityIndex.ts` decides what the pill resolves to.
 *
 * Pure and framework-free, so the node-env vitest can hold it.
 */

/** The smallest shape of an mdast node this plugin reads and writes. */
export interface MdNode {
  type: string;
  value?: string;
  url?: string;
  children?: MdNode[];
  data?: {
    hName?: string;
    hProperties?: Record<string, string>;
    hChildren?: { type: "text"; value: string }[];
  };
}

/** The HTML attribute a pill carries, and the attributes it reads. */
export const PILL_ATTR = "data-entity-pill";
export const PILL_TEXT_ATTR = "data-entity-text";
export const PILL_NUMBER_ATTR = "data-entity-number";

const EMAIL_SOURCE = String.raw`[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}`;

/**
 * One scan finds a fenced name (with an optional `#n ` before it) or a bare
 * email. The fenced arm comes first, so an email inside «» is one pill.
 */
const PILL = new RegExp(
  String.raw`(?:(#\d+)\s+)?«([^«»\n]{1,200})»|(?<![\w.@+-])(${EMAIL_SOURCE})(?![\w@-])`,
  "g",
);

/** A whole run that is one fenced name, for the bold unwrap. */
const ONE_PILL = /^\s*(?:#\d+\s+)?«[^«»\n]{1,200}»\s*$/;

/** Nodes whose inside the plugin leaves alone. */
const SKIP = new Set(["code", "inlineCode", "html", "definition", "linkReference", "image"]);

function pillNode(text: string, number?: string): MdNode {
  const props: Record<string, string> = { [PILL_ATTR]: "1", [PILL_TEXT_ATTR]: text };
  if (number) props[PILL_NUMBER_ATTR] = number;
  return {
    type: "entityPill",
    data: {
      hName: "span",
      hProperties: props,
      // The fallback text if no component draws the pill: never the marks.
      hChildren: [{ type: "text", value: number ? `${number} ${text}` : text }],
    },
  };
}

/** remark-gfm links `a@b.io` as `mailto:a@b.io`. Undo that, for the pill. */
function isMailtoAutolink(node: MdNode): boolean {
  if (node.type !== "link" || !node.url?.toLowerCase().startsWith("mailto:")) return false;
  const kids = node.children ?? [];
  if (kids.length !== 1 || kids[0].type !== "text") return false;
  return `mailto:${kids[0].value ?? ""}`.toLowerCase() === node.url.toLowerCase();
}

function textOf(nodes: MdNode[]): string | null {
  let out = "";
  for (const n of nodes) {
    if (n.type === "text") out += n.value ?? "";
    else if (isMailtoAutolink(n)) out += n.children?.[0].value ?? "";
    else return null;
  }
  return out;
}

/**
 * Normalise one parent's children: unwrap a bold or italic that holds one
 * fenced name, turn a mailto autolink back into text, and merge adjacent
 * text, so a name split across nodes by the parser is one run again.
 */
function normalise(children: MdNode[]): MdNode[] {
  const flat: MdNode[] = [];
  for (const child of children) {
    if ((child.type === "strong" || child.type === "emphasis") && child.children) {
      const inner = textOf(child.children);
      if (inner !== null && ONE_PILL.test(inner)) {
        flat.push({ type: "text", value: inner });
        continue;
      }
    }
    if (isMailtoAutolink(child)) {
      flat.push({ type: "text", value: child.children?.[0].value ?? "" });
      continue;
    }
    flat.push(child);
  }
  const merged: MdNode[] = [];
  for (const node of flat) {
    const last = merged[merged.length - 1];
    if (node.type === "text" && last?.type === "text") {
      last.value = (last.value ?? "") + (node.value ?? "");
    } else {
      merged.push(node.type === "text" ? { type: "text", value: node.value ?? "" } : node);
    }
  }
  return merged;
}

/** Split one text value into text and pill nodes. */
export function splitText(value: string): MdNode[] {
  const out: MdNode[] = [];
  let last = 0;
  for (const m of value.matchAll(PILL)) {
    const start = m.index ?? 0;
    if (start > last) out.push({ type: "text", value: value.slice(last, start) });
    if (m[2] !== undefined) {
      const name = m[2].trim();
      if (name) out.push(pillNode(name, m[1]));
      else if (m[1]) out.push({ type: "text", value: `${m[1]} ` });
    } else {
      out.push(pillNode(m[3]));
    }
    last = start + m[0].length;
  }
  if (last < value.length) out.push({ type: "text", value: value.slice(last) });
  // A stray guillemet — an unclosed mark, an empty pair — never shows.
  return glueTrailingPunctuation(
    out
      .map((n) => (n.type === "text" ? { ...n, value: (n.value ?? "").replace(/[«»]/g, "") } : n))
      .filter((n) => n.type !== "text" || n.value !== ""),
  );
}

/** The class of the span that keeps a pill and its punctuation together. */
export const PILL_GROUP_CLASS = "whitespace-nowrap";

/**
 * Keep a pill and the punctuation right after it on one line (S9 visual
 * review). Without this, a wrap can put the pill at the end of one line and
 * its comma at the start of the next. The group adds no space: the comma
 * follows the pill's closing tag directly.
 */
function glueTrailingPunctuation(nodes: MdNode[]): MdNode[] {
  const out: MdNode[] = [];
  for (let k = 0; k < nodes.length; k++) {
    const node = nodes[k];
    const next = nodes[k + 1];
    const punct = next?.type === "text" ? (next.value ?? "").match(/^[,.;:!?)\]]+/) : null;
    if (node.type !== "entityPill" || !punct) {
      out.push(node);
      continue;
    }
    out.push({
      type: "entityPillGroup",
      data: { hName: "span", hProperties: { className: PILL_GROUP_CLASS } },
      children: [node, { type: "text", value: punct[0] }],
    });
    const rest = (next!.value ?? "").slice(punct[0].length);
    if (rest) out.push({ type: "text", value: rest });
    k += 1;
  }
  return out;
}

function walk(node: MdNode, insideLink: boolean): void {
  if (!node.children || SKIP.has(node.type)) return;
  const kids = insideLink ? node.children : normalise(node.children);
  const next: MdNode[] = [];
  for (const child of kids) {
    if (child.type === "text") {
      if (insideLink) {
        // No pill inside a link (a control inside a control), and no marks.
        next.push({ ...child, value: (child.value ?? "").replace(/[«»]/g, "") });
      } else {
        next.push(...splitText(child.value ?? ""));
      }
      continue;
    }
    walk(child, insideLink || child.type === "link");
    next.push(child);
  }
  node.children = next;
}

/** The plugin. `remarkPlugins={[remarkGfm, remarkEntityPills]}`. */
export default function remarkEntityPills() {
  return (tree: MdNode) => {
    walk(tree, false);
  };
}

// ── The missing space before bold ────────────────────────────────────────────

/**
 * `today.**Early stages**` renders as `today.Early stages`: the model left
 * out the space after a full stop. Put it back before the parser runs.
 *
 * Only after `.`, `!` or `?` that follows a letter, and only when a letter
 * follows the `**`. An opening `**` gets the space before it. A closing one
 * (`**Done.**Next`) gets it after, so the bold still closes. A number such
 * as `3.**` is not touched, because a digit is not a letter.
 *
 * Three rules keep it from damaging text (S9 fix round 1):
 *
 * - **Open or closed is counted per paragraph**, across its lines, and a
 *   blank line starts the count again. A bold that opens on one line and
 *   closes on the next is still one bold.
 * - **A line indented four spaces or more is left alone**, because it can be
 *   code. Fenced code and inline code are left alone too, and so is a URL.
 * - **A fence closes only on a fence of the same character that is at least
 *   as long.** A ``` line inside a ```` block does not end the block.
 */
export function spaceBeforeBold(content: string): string {
  if (!content.includes("**")) return content;
  const lines = content.split("\n");
  let fence: { char: string; len: number } | null = null;
  const state = { bolds: 0 };
  return lines
    .map((line) => {
      const mark = line.match(/^ {0,3}(`{3,}|~{3,})/);
      if (fence !== null) {
        if (
          mark &&
          mark[1][0] === fence.char &&
          mark[1].length >= fence.len &&
          line.slice(line.indexOf(mark[1]) + mark[1].length).trim() === ""
        ) {
          fence = null;
        }
        return line;
      }
      if (mark) {
        fence = { char: mark[1][0], len: mark[1].length };
        state.bolds = 0;
        return line;
      }
      if (line.trim() === "") {
        state.bolds = 0;
        return line;
      }
      if (/^(?: {4}|\t)/.test(line)) return line;
      return fixLine(line, state);
    })
    .join("\n");
}

/**
 * Split a line into text and inline code, the CommonMark way: a run of N
 * backticks opens a code span, and only the next run of exactly N closes it.
 * So ``a`b`` is one span. A run with no closing partner is plain text.
 * Returns the parts with code at the odd indexes. Exported for its test.
 */
export function splitCodeSpans(line: string): string[] {
  const parts: string[] = [];
  let text = "";
  let k = 0;
  while (k < line.length) {
    if (line[k] !== "`") {
      text += line[k];
      k += 1;
      continue;
    }
    let n = 0;
    while (line[k + n] === "`") n += 1;
    // Find the next run of exactly n backticks.
    let close = -1;
    let j = k + n;
    while (j < line.length) {
      if (line[j] !== "`") {
        j += 1;
        continue;
      }
      let m = 0;
      while (line[j + m] === "`") m += 1;
      if (m === n) {
        close = j;
        break;
      }
      j += m;
    }
    if (close === -1) {
      text += line.slice(k, k + n);
      k += n;
      continue;
    }
    parts.push(text, line.slice(k, close + n));
    text = "";
    k = close + n;
  }
  parts.push(text);
  return parts;
}

function fixLine(line: string, state: { bolds: number }): string {
  // Only the text between inline code spans is touched.
  const parts = splitCodeSpans(line);
  return parts
    .map((part, i) => {
      if (i % 2 === 1) return part;
      let out = "";
      let k = 0;
      while (k < part.length) {
        if (part.startsWith("**", k)) {
          const before = part.slice(0, k);
          const prev = part[k - 1] ?? "";
          const prevPrev = part[k - 2] ?? "";
          const after = part[k + 2] ?? "";
          const token = before.split(/\s/).pop() ?? "";
          const isUrl = /:\/\/|^www\./i.test(token);
          if (
            /[.!?]/.test(prev) &&
            /\p{L}/u.test(prevPrev) &&
            /\p{L}/u.test(after) &&
            !isUrl
          ) {
            out += state.bolds % 2 === 0 ? " **" : "** ";
          } else {
            out += "**";
          }
          state.bolds += 1;
          k += 2;
          continue;
        }
        out += part[k];
        k += 1;
      }
      return out;
    })
    .join("");
}
