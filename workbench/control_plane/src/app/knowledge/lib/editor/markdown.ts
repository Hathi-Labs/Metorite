// ── Markdown in and out of the block editor (WS-61 KB-2b, §21.4) ───────────
//
// Pure. `body_md` stays the stored body: the chunker, the assistant's
// `read_page`, the export and the versions all read Markdown. The editor
// parses the body when it opens and writes Markdown back.
//
// ⚠️ ONE manager for the whole app. `@tiptap/markdown` registers its
// tokenizers on the shared `marked` instance each time a manager starts. The
// `Markdown` editor extension starts one per editor, so each page view would
// add the same tokenizers again, and each parse would get slower. The editor
// therefore uses this manager, and not that extension.
//
// "No loss" (§21.5) means a fixed point: parse and write once gives md′, and
// md′ parsed and written again gives md′ byte for byte. A first save may
// normalise the spelling (`*` bullets become `-`). It never drops a block.

import type { JSONContent } from "@tiptap/core";
import { MarkdownManager } from "@tiptap/markdown";

import { knowledgeExtensions } from "./schema";

let shared: MarkdownManager | null = null;

/** The one manager, made on first use. */
export function knowledgeMarkdown(): MarkdownManager {
  if (!shared) shared = new MarkdownManager({ extensions: knowledgeExtensions() });
  return shared;
}

/** The nodes that hold blocks. An inline child of one needs a paragraph. */
const BLOCK_CONTAINERS = new Set([
  "doc",
  "blockquote",
  "callout",
  "detailsContent",
  "listItem",
  "taskItem",
  "tableCell",
  "tableHeader",
]);

/** The inline nodes of the schema. */
const INLINE_NODES = new Set(["text", "image", "htmlInline", "hardBreak"]);

/**
 * Each run of inline nodes in a block container, wrapped in a paragraph.
 *
 * Images are inline, as in Markdown. `@tiptap/extension-paragraph` lifts a
 * paragraph that holds one image out to the block level, and a document
 * with an inline node there is not valid. This puts it back in its own
 * paragraph, which writes the same Markdown.
 */
export function wrapInlineRuns(node: JSONContent): JSONContent {
  if (!node.content) return node;
  const children = node.content.map(wrapInlineRuns);
  if (!BLOCK_CONTAINERS.has(node.type ?? "")) return { ...node, content: children };
  const out: JSONContent[] = [];
  let run: JSONContent[] = [];
  const flush = () => {
    if (run.length) out.push({ type: "paragraph", content: run });
    run = [];
  };
  for (const child of children) {
    if (INLINE_NODES.has(child.type ?? "")) run.push(child);
    else {
      flush();
      out.push(child);
    }
  }
  flush();
  return { ...node, content: out };
}

/** A body as the editor's document. An empty body is an empty document. */
export function parseBody(md: string): JSONContent {
  const doc = wrapInlineRuns(knowledgeMarkdown().parse(md));
  return doc.content?.length ? doc : { type: "doc", content: [{ type: "paragraph" }] };
}

/**
 * The editor's document as a body. Trailing blank lines go: the editor adds
 * an empty last paragraph after a final table or code block, so a member can
 * type below it, and that paragraph is not content.
 */
export function writeBody(doc: JSONContent): string {
  return knowledgeMarkdown().serialize(doc).replace(/\s+$/, "");
}

/** Parse and write once: the body a save of this body writes. */
export function normaliseBody(md: string): string {
  return writeBody(parseBody(md));
}

/**
 * The body a draft holds after an edit.
 *
 * `baseline` is what the editor wrote when it opened, before any edit. When
 * the editor writes the same again, the member changed nothing that
 * Markdown keeps, so the draft keeps the stored body, byte for byte. A save
 * with no edit then writes nothing (`draft.ts` finds no change).
 */
export function bodyAfterEdit(stored: string, baseline: string, current: string): string {
  return current === baseline ? stored : current;
}
