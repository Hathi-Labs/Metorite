/**
 * GenUIFallback — how a generative-UI node the renderer does not know is
 * drawn for a member. Never "unsupported UI element": that is the renderer's
 * word, and it hid the content the model sent.
 *
 * - A `tree` draws as a nested list, in the `list` node's look.
 * - Any other unknown node draws as a neutral card that says "This card could
 *   not be shown", with the words it holds.
 * - A node with no words draws nothing.
 *
 * The rules are pure, in `lib/genUiFallback.ts`. Fence:
 * `src/lib/genUiFallback.test.ts`.
 */

import { fallbackLines, treeItems, type FallbackNode, type TreeItem } from "@/lib/genUiFallback";
import { text } from "@/lib/genUiText";

/** The `list` node's look (`GenerativeUINode.tsx`), so a tree reads as one. */
const LIST_CLASS =
  "ml-5 space-y-0.5 text-[13px] text-foreground list-disc list-outside marker:text-muted-foreground";

function TreeList({ items }: { items: TreeItem[] }) {
  return (
    <ul className={LIST_CLASS}>
      {items.map((it, i) => (
        <li key={i}>
          {it.label}
          {it.children.length > 0 && (
            <div className="mt-0.5">
              <TreeList items={it.children} />
            </div>
          )}
        </li>
      ))}
    </ul>
  );
}

export default function GenUIFallback({ node }: { node: FallbackNode }) {
  const tree = treeItems(node);
  const props = (node.props ?? {}) as Record<string, unknown>;
  if (tree.length > 0) {
    const title = text(props.title ?? props.label ?? props.name).trim();
    return (
      <div data-genui-fallback="tree" className="rounded-lg border border-border/60 bg-card/50 p-3 space-y-2">
        {title && <div className="text-sm font-semibold text-foreground">{title}</div>}
        <TreeList items={tree} />
      </div>
    );
  }
  const lines = fallbackLines(node);
  if (lines.length === 0) return null;
  return (
    <div data-genui-fallback="card" className="rounded-lg border border-border/60 bg-card/50 p-3 space-y-1">
      <div className="text-[11px] text-muted-foreground">This card could not be shown.</div>
      {lines.map((l, i) => (
        <p key={i} className="text-[13px] leading-relaxed text-foreground">{l}</p>
      ))}
    </div>
  );
}
