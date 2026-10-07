/**
 * A generative-UI string field is SAFE inline Markdown (owner report,
 * 2026-10-07, screenshot B: a list drew `**bold**`, backticks and «marks» as
 * raw text). `GenUiText` is `MarkdownBody` in its `inline` mode: the one chat
 * renderer, with no `rehype-raw`, the chat's URL transform, and only inline
 * elements. Each rule names the mutation that breaks it.
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

import { EntityIndexContext } from "@/components/ChatEntityPill";
import GenerativeUINode, { GenUiText } from "@/components/GenerativeUINode";
import { EMPTY_INDEX } from "@/lib/entityIndex";

/** Screenshot B, rebuilt from the exact text the owner saw. */
export const SCREENSHOT_B = {
  type: "stack",
  children: [
    { type: "heading", props: { text: "What I can create and set" } },
    {
      type: "list",
      props: {
        items: [
          "**A space, folder, project or subproject** — `create_project`, one card, and Archive is the undo. Inside «Hathi Labs Projects» a new project sits under the space.",
          "**Several tasks in one project at once** — `propose_plan` drafts them, and you approve one card.",
        ],
      },
    },
    { type: "callout", props: { title: "The guards", text: "Every change is **one card** that you approve." } },
  ],
};

const render = (node: unknown, index: unknown = null) =>
  renderToStaticMarkup(
    createElement(
      EntityIndexContext.Provider,
      { value: index as never },
      createElement(GenerativeUINode, { spec: node }),
    ),
  );

describe("screenshot B", () => {
  // Mutation caught: `{s(it)}` drawn as text again in the list node.
  it("draws bold as bold and code as code, with no raw marks", () => {
    const html = render(SCREENSHOT_B);
    expect(html).not.toContain("**");
    expect(html).not.toContain("`");
    expect(html).not.toContain("«");
    expect(html).toContain('<strong class="font-semibold text-foreground">A space, folder, project or subproject</strong>');
    expect(html).toMatch(/<code[^>]*>create_project<\/code>/);
    expect(html).toContain('<strong class="font-semibold text-foreground">one card</strong>');
  });

  it("draws the name as a pill inside a Projects turn", () => {
    const html = render(SCREENSHOT_B, EMPTY_INDEX);
    expect(html).not.toContain("«");
    expect(html).not.toContain("data-fenced-name");
    expect(html).toContain("Hathi Labs Projects");
  });
});

describe("a field stays inert", () => {
  const field = (text: string) => renderToStaticMarkup(createElement(GenUiText, { text }));

  // Mutation caught: adding `rehype-raw`, or a `dangerouslySetInnerHTML`.
  it("keeps HTML as text", () => {
    const html = field('Hi <script>alert(1)</script><img src=x onerror="alert(2)"> there');
    expect(html).not.toContain("<script");
    expect(html).not.toContain("<img");
    expect(html).toContain("&lt;script&gt;");
  });

  // Mutation caught: dropping `urlTransform`, which strips a javascript: link.
  it("never links to javascript: or data:", () => {
    const html = field("[click](javascript:alert(1)) and [x](data:text/html,<b>x</b>)");
    expect(html).not.toMatch(/href="javascript:/i);
    expect(html).not.toMatch(/href="data:/i);
  });

  // Mutation caught: an `inline` mode that keeps `img` (a remote fetch).
  it("drops an image and every block element", () => {
    const html = field("![pixel](https://evil.example/p.png)\n\n# Big\n\n- item");
    expect(html).not.toContain("<img");
    expect(html).not.toContain("<h1");
    expect(html).not.toContain("<ul");
    expect(html).not.toContain("evil.example");
  });

  it("opens an http link in a new tab, with noopener", () => {
    const html = field("[docs](https://example.com/a)");
    expect(html).toContain('href="https://example.com/a"');
    expect(html).toContain('rel="noopener noreferrer"');
  });
});

describe("a chat answer with typed bullets", () => {
  // Mutation caught: `typedBulletsToList` not called, so "• one • two" ran
  // together into one paragraph.
  it("draws a list for lines that start with •, and leaves code alone", async () => {
    const { MarkdownBody } = await import("@/components/MarkdownMessage");
    const { typedBulletsToList } = await import("@/lib/remarkEntityPills");
    const html = renderToStaticMarkup(createElement(MarkdownBody, { content: "Done:\n\n• one\n• two", fences: true }));
    expect(html).toMatch(/<ul[^>]*>[\s\S]*<li[^>]*>one<\/li>[\s\S]*<li[^>]*>two<\/li>/);
    expect(html).not.toContain("•");
    expect(typedBulletsToList("```\n• keep\n```\n• go")).toBe("```\n• keep\n```\n- go");
    expect(typedBulletsToList("a • b")).toBe("a • b");
  });
});
