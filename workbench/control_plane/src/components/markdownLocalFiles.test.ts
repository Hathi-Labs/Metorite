/**
 * Task 3031: links and local files in the shared Markdown renderer. A bare
 * URL is a link that opens in a new tab. A `file:` URL or a drive path is a
 * "local file" chip with a Copy path button. No unsafe protocol is a link.
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

import { MarkdownBody } from "@/components/MarkdownMessage";
import { LOCAL_FILE_TIP, localPathOf } from "@/lib/markdownMedia";

const render = (content: string, inline = false) =>
  renderToStaticMarkup(createElement(MarkdownBody, { content, inline }));
const anchors = (html: string) => html.match(/<a\b[^>]*>/g) ?? [];
const chips = (html: string) => html.match(/data-local-file-chip/g)?.length ?? 0;

describe("links", () => {
  it("opens a bare https URL in a new tab", () => {
    const [a] = anchors(render("See https://example.com/a?b=1 now."));
    expect(a).toContain('href="https://example.com/a?b=1"');
    expect(a).toContain('target="_blank"');
    expect(a).toContain('rel="noopener noreferrer"');
  });

  it("opens a bare http URL in a new tab, inline too", () => {
    const [a] = anchors(render("Open http://example.com/x", true));
    expect(a).toContain('target="_blank"');
  });

  it("opens a Markdown link in a new tab", () => {
    const [a] = anchors(render("[docs](https://example.com/docs)"));
    expect(a).toContain('href="https://example.com/docs"');
    expect(a).toContain('target="_blank"');
  });

  it.each([
    "[x](javascript:alert(1))",
    "[x](vbscript:msgbox(1))",
    "[x](data:text/html;base64,AAAA)",
    "[x](file:///C:/a/b.png)",
  ])("never makes %s clickable", (md) => {
    const html = render(md);
    expect(html).not.toMatch(/href="(javascript|vbscript|data|file):/i);
  });
});

describe("local files", () => {
  it("draws a file: URL as one chip with the file name and the tip", () => {
    const html = render("Shot: file:///C:/wt-ui-keepbar/ux-shots/a%20b.png");
    expect(chips(html)).toBe(1);
    expect(html).toContain("a b.png");
    expect(html).toContain(LOCAL_FILE_TIP);
    expect(html).toContain('aria-label="Copy path"');
    expect(html).not.toContain("href=");
  });

  it("draws a Windows path with a backslash or a slash", () => {
    expect(chips(render("at C:\\wt\\shots\\one.png and C:/wt/shots/two.png."))).toBe(2);
  });

  it("draws a /c/ path", () => {
    const html = render("see /c/wt/shots/one.png.");
    expect(chips(html)).toBe(1);
    expect(html).toContain("one.png");
  });

  it("draws a link or an image that targets a local file as a chip", () => {
    expect(chips(render("[shot](file:///C:/a/b.png)"))).toBe(1);
    expect(chips(render("![shot](C:/a/b.png)"))).toBe(1);
  });

  it("keeps the tip one sentence and the same for an image", () => {
    expect(LOCAL_FILE_TIP).toBe(
      "This file is on the computer of the agent that made it. Copy the path to open it there, or ask the agent to attach it.",
    );
    expect(render("C:/a/b.png")).toContain(LOCAL_FILE_TIP);
  });

  it("leaves a path in a code span or a code block as code", () => {
    expect(chips(render("run `C:\\wt\\x.exe` now"))).toBe(0);
    expect(chips(render("```\nC:/wt/x.png\n/c/wt/y.png\n```"))).toBe(0);
  });

  it("does not turn ordinary text into a chip", () => {
    for (const text of ["a/b", "3/4", "and/or", "yes/no/maybe", "https://x.com/C:/y", "an a:b ratio"]) {
      expect(chips(render(text))).toBe(0);
    }
  });

  it("localPathOf reads each shape and nothing else", () => {
    expect(localPathOf("file:///C:/a/b.png")).toBe("C:/a/b.png");
    expect(localPathOf("file:///home/me/b.png")).toBe("/home/me/b.png");
    expect(localPathOf("C:\\a\\b")).toBe("C:\\a\\b");
    expect(localPathOf("/c/a/b")).toBe("/c/a/b");
    expect(localPathOf("C:")).toBeNull();
    expect(localPathOf("a/b")).toBeNull();
    expect(localPathOf("https://example.com")).toBeNull();
  });
});
