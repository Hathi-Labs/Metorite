/**
 * A remark plugin: Markdown with no image and no link (review of #1065).
 *
 * A report's text is Markdown-lite: bold, italic, lists and line breaks. Its
 * contract holds no image and no URL, and a regex cannot hold that line. A
 * nested-bracket image `![a[b]c](url)`, a reference image `![x][r]`, a
 * reference link `[click][r]` with its `[r]: url` definition, and a bare URL
 * that remark-gfm turns into a link each passed one. So this works on the
 * parsed tree, after remark-gfm:
 *
 * - an `image` or an `imageReference` goes;
 * - a `link` or a `linkReference` becomes its own words, with no target;
 * - raw `html` goes.
 *
 * A `[r]: url` definition draws nothing by itself, and each reference to it
 * becomes its words, so no definition rule is needed (a mutation that keeps
 * definitions changes no output).
 *
 * `MarkdownBody` runs it when `textOnly` is set. Fence:
 * `src/components/reportView.test.ts` ("report text drops every image and
 * every link").
 */

interface MdNode {
  type: string;
  children?: MdNode[];
}

const DROP = new Set(["image", "imageReference", "html"]);
const UNWRAP = new Set(["link", "linkReference"]);

function clean(node: MdNode): void {
  if (!node.children) return;
  const out: MdNode[] = [];
  for (const child of node.children) {
    if (DROP.has(child.type)) continue;
    clean(child);
    if (UNWRAP.has(child.type)) out.push(...(child.children ?? []));
    else out.push(child);
  }
  node.children = out;
}

/** The plugin. remark calls it with no options, and it returns the transform. */
export default function remarkTextOnly() {
  return (tree: MdNode) => {
    clean(tree);
  };
}
