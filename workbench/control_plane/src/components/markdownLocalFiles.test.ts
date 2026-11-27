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

  it.each([
    "[x](javascript:alert(1))",
    "[x](vbscript:msgbox(1))",
    "[x](data:text/html;base64,AAAA)",
  ])("draws %s as plain text with no anchor", (md) => {
    const html = render(md);
    expect(html).not.toContain("<a");
    expect(html).toContain(">x<");
  });
});

describe("local files", () => {
  it("draws a file: URL as one chip with the file name and the tip", () => {
    const html = render("Shot: file:///C:/wt-ui-keepbar/ux-shots/a%20b.png");
    expect(chips(html)).toBe(1);
    expect(html).toContain("a b.png");
    expect(html).toContain('aria-label="Copy path"');
    // The tip comes from the tip primitive on hover, and the button has no title.
    expect(html).not.toContain("title=");
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
    expect(chips(render("C:/a/b.png"))).toBe(1);
    expect(render("C:/a/b.png")).not.toContain("title=");
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

  it("keeps a bare path with a space as plain text", () => {
    const html = render("see C:\\Users\\a b\\c.png now");
    expect(chips(html)).toBe(0);
    expect(html).toContain("a b");
  });

  it("draws a quoted path with a space as one chip with the whole name", () => {
    const html = render('open "C:\\Users\\a b\\c d.png" now');
    expect(chips(html)).toBe(1);
    expect(html).toContain("c d.png");
    expect(html).not.toContain("Users");
  });

  it("decodes %20 in a file: URL name", () => {
    const html = render("file:///C:/a%20b/c%20d.png");
    expect(chips(html)).toBe(1);
    expect(html).toContain("c d.png");
  });

  it("leaves the full stop at the end of a sentence outside the chip", () => {
    const html = render("Saved to C:/wt/shot.png. Then more.");
    expect(chips(html)).toBe(1);
    expect(html).toContain("shot.png");
    expect(html).toContain(". Then more.");
  });

  it("needs an extension or a trailing separator for a bare path", () => {
    expect(chips(render("C:/wt/shots/"))).toBe(1);
    expect(chips(render("C:/wt/shots"))).toBe(0);
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
