/**
 * The streaming caret sits at the end of the last line of words, and never
 * on a line of its own (the lone "|" of 2026-10-07). `lib/streamCaret.ts`.
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import MarkdownMessage, { MarkdownBody } from "@/components/MarkdownMessage";
import { ProseTimelineEntry } from "@/components/ThinkingContainer";
import { themedIcon } from "@/components/Icon";
import { caretHost, type HastNode } from "@/lib/streamCaret";

const CARET = /<span class="stream-caret[^"]*" aria-hidden="true"><\/span>/;

function body(content: string, caret = true): string {
  return renderToStaticMarkup(createElement(MarkdownBody, { content, caret }));
}

describe("caretHost — where the caret may draw", () => {
  const el = (tagName: string, children: HastNode[] = []): HastNode =>
    ({ type: "element", tagName, children }) as HastNode;
  const txt = (value: string): HastNode => ({ type: "text", value }) as HastNode;

  it("the last paragraph", () => {
    const p = el("p", [txt("b")]);
    expect(caretHost({ children: [el("p", [txt("a")]), txt("\n"), p, txt("\n")] })).toBe(p);
  });

  it("the deepest last list item, through a nested list", () => {
    const inner = el("li", [txt("c")]);
    const tree = { children: [el("ul", [el("li", [txt("a"), el("ul", [txt("\n"), inner, txt("\n")])])])] };
    expect(caretHost(tree)).toBe(inner);
  });

  it("a list item that ends in bold is still a line of words", () => {
    const li = el("li", [txt("Due "), el("strong", [txt("today")])]);
    expect(caretHost({ children: [el("ol", [li])] })).toBe(li);
  });

  it("nothing after a code block, a rule or an image", () => {
    for (const tag of ["pre", "hr", "img"]) {
      expect(caretHost({ children: [el("p", [txt("a")]), el(tag)] })).toBeNull();
    }
    expect(caretHost({ children: [] })).toBeNull();
  });
});

describe("the rendered caret", () => {
  it("draws inside the last paragraph", () => {
    expect(body("One.\n\nTwo tasks are late.")).toMatch(/Two tasks are late\.<span class="stream-caret/);
  });

  it("draws inside the last list item", () => {
    expect(body("The late tasks:\n\n- Flash layout\n- Secure boot")).toMatch(/Secure boot<span class="stream-caret[^"]*"[^>]*><\/span><\/li>/);
  });

  it("does not draw after a code block", () => {
    expect(body("Run this:\n\n```bash\nmake flash\n```")).not.toMatch(CARET);
  });

  it("does not draw when the text is not streaming", () => {
    expect(body("Two tasks are late.", false)).not.toMatch(CARET);
  });

  it("is never a sibling after the answer body", () => {
    const html = renderToStaticMarkup(
      createElement(MarkdownMessage, { content: "Two tasks are late.", streaming: true }),
    );
    expect(html).toMatch(CARET);
    // The defect: a span after the closing tag of the last block.
    expect(html).not.toMatch(/<\/(p|ul|ol|div)><span class="[^"]*(stream-caret|w-\[2px\])/);
    expect(html).not.toContain("bg-zinc-300");
  });

  it("the reasoning entry in the trail puts it inside the line too", () => {
    const html = renderToStaticMarkup(
      createElement(ProseTimelineEntry, {
        text: "Now I draw the card.",
        live: true,
        icon: themedIcon("Brain"),
        iconClass: "text-info",
      }),
    );
    expect(html).toMatch(/Now I draw the card\.<span class="stream-caret/);
    expect(html).not.toMatch(/<\/p><span class="[^"]*w-\[2px\]/);
  });
});
