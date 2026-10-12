/**
 * WS-57 TR-4 — a file pasted or dropped into a task's text.
 *
 * The rules are pure, so the node runner reaches every one of them. What no
 * test here can reach is the browser's paste event itself. The visual rig
 * (`e2e/visual/task-refs.visual.ts`, not committed) draws the result.
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { MarkdownBody } from "@/components/MarkdownMessage";
import { remoteHost } from "@/lib/markdownMedia";

import {
  altText,
  attachmentImageUrl,
  forView,
  hasUploadPlaceholder,
  imageMarkdown,
  insertAtCaret,
  isInlineImage,
  keepLineBreaks,
  onOwnLine,
  partitionFiles,
  pastePlan,
  replaceFirst,
  uploadPlaceholder,
} from "./inlineUploads";

const APP = "https://app.metorite.com";
const ID = "0b8c41e2-7d3a-4f5b-9c1d-2e3f4a5b6c7d";

const png = { name: "image.png", type: "image/png" };
const jpg = { name: "photo.jpg", type: "image/jpeg" };
const pdf = { name: "brief.pdf", type: "application/pdf" };
const svg = { name: "logo.svg", type: "image/svg+xml" };

describe("which files draw inline", () => {
  it("is the Files list's preview safelist, so SVG only attaches", () => {
    expect(isInlineImage(png)).toBe(true);
    expect(isInlineImage(jpg)).toBe(true);
    expect(isInlineImage({ name: "a.webp", type: "image/webp" })).toBe(true);
    expect(isInlineImage(svg)).toBe(false);
    expect(isInlineImage(pdf)).toBe(false);
    expect(isInlineImage({ name: "x", type: "" })).toBe(false);
  });

  it("splits a drop into images and other files, in order", () => {
    expect(partitionFiles([pdf, png, svg, jpg])).toEqual({
      images: [png, jpg],
      others: [pdf, svg],
    });
  });
});

describe("pastePlan", () => {
  it("leaves a plain-text paste to the browser", () => {
    expect(pastePlan("hello", [])).toBeNull();
    expect(pastePlan("", [])).toBeNull();
  });

  it("lets the text win when a paste carries text and a picture of it", () => {
    // Word and Excel put both on the clipboard.
    expect(pastePlan("A1\tB1", [png])).toBeNull();
  });

  it("takes the files of a screenshot paste", () => {
    expect(pastePlan("", [png, png])).toEqual({ images: [png, png], others: [] });
    expect(pastePlan("  \n", [pdf])).toEqual({ images: [], others: [pdf] });
  });
});

describe("the placeholder", () => {
  it("reads as the spec writes it", () => {
    expect(uploadPlaceholder("image.png")).toBe("![Uploading image.png…]()");
  });

  it("keeps a name from breaking the Markdown", () => {
    expect(altText("a [b] c\\d\ne.png")).toBe("a b cde.png");
    expect(altText("[]")).toBe("image");
    expect(uploadPlaceholder("[x].png")).toBe("![Uploading x.png…]()");
  });

  it("is found while it is in the text, and not after", () => {
    const text = `before ${uploadPlaceholder("image.png")} after`;
    expect(hasUploadPlaceholder(text)).toBe(true);
    expect(hasUploadPlaceholder("![image.png](/x.png)")).toBe(false);
    expect(hasUploadPlaceholder("plain")).toBe(false);
    // The check holds no state between calls.
    expect(hasUploadPlaceholder(text)).toBe(true);
  });
});

describe("insertAtCaret", () => {
  it("puts the text at the caret and moves the caret past it", () => {
    expect(insertAtCaret("ab", 1, 1, "X")).toEqual({ value: "aXb", caret: 2 });
  });

  it("replaces a selection", () => {
    expect(insertAtCaret("abcd", 1, 3, "X")).toEqual({ value: "aXd", caret: 2 });
  });

  it("clamps a stale caret instead of cutting the text", () => {
    expect(insertAtCaret("ab", 9, 12, "X")).toEqual({ value: "abX", caret: 3 });
    expect(insertAtCaret("ab", -4, 1, "X")).toEqual({ value: "Xb", caret: 1 });
  });
});

describe("onOwnLine", () => {
  const P = uploadPlaceholder("image.png");

  it("adds nothing in an empty field or on an empty line", () => {
    expect(onOwnLine("", 0, 0, P)).toBe(P);
    expect(onOwnLine("a\n\nb", 2, 2, P)).toBe(P);
  });

  it("breaks the line before and after a caret inside a sentence", () => {
    expect(onOwnLine("ab", 1, 1, P)).toBe(`\n${P}\n`);
  });

  it("adds only the break that is missing", () => {
    expect(onOwnLine("ab", 2, 2, P)).toBe(`\n${P}`);
    expect(onOwnLine("ab", 0, 0, P)).toBe(`${P}\n`);
    expect(onOwnLine("a\nb", 2, 2, P)).toBe(`${P}\n`);
  });
});

describe("replaceFirst", () => {
  it("swaps the first placeholder only, so several images keep their order", () => {
    const one = uploadPlaceholder("image.png");
    const text = `${one}\n${one}`;
    const first = replaceFirst(text, one, "![a](/1)");
    expect(first).toBe(`![a](/1)\n${one}`);
    expect(replaceFirst(first, one, "![b](/2)")).toBe("![a](/1)\n![b](/2)");
  });

  it("removes a placeholder when the replacement is empty", () => {
    expect(replaceFirst(`a ${uploadPlaceholder("x.png")} b`, uploadPlaceholder("x.png"), "")).toBe(
      "a  b",
    );
  });

  it("leaves the text alone when the member deleted the placeholder", () => {
    expect(replaceFirst("typed over", uploadPlaceholder("x.png"), "![x](/1)")).toBe("typed over");
  });

  it("pastes a $ in the replacement as it is", () => {
    expect(replaceFirst("a T b", "T", "$`$&$'")).toBe("a $`$&$' b");
  });
});

describe("the served URL", () => {
  it("is the one attachment path that markdownMedia loads at once", () => {
    for (const name of ["image.png", "Screen Shot 2026-10-13 at 9.41.02.png", "a%2Fb.png", "..", "", "ü.png"]) {
      const url = attachmentImageUrl(ID, name);
      expect(url.startsWith(`/api/projects/attachments/${ID}/`), url).toBe(true);
      expect(remoteHost(url, APP), url).toBeNull();
    }
  });

  it("writes the name segment with no escape", () => {
    expect(attachmentImageUrl(ID, "Screen Shot.png")).toBe(
      `/api/projects/attachments/${ID}/Screen_Shot.png`,
    );
    expect(attachmentImageUrl(ID, "..")).toBe(`/api/projects/attachments/${ID}/image`);
    expect(attachmentImageUrl(ID.toUpperCase(), "a.png")).toBe(
      `/api/projects/attachments/${ID}/a.png`,
    );
  });

  it("builds the image Markdown from the upload's answer", () => {
    expect(imageMarkdown("image.png", { attachment_id: ID, name: "image.png" })).toBe(
      `![image.png](/api/projects/attachments/${ID}/image.png)`,
    );
  });
});

describe("the rendered view", () => {
  const render = (content: string) =>
    renderToStaticMarkup(createElement(MarkdownBody, { content }));

  it("draws an uploaded image inline, with no click-to-load gate", () => {
    const html = render(forView(imageMarkdown("image.png", { attachment_id: ID, name: "image.png" })));
    expect(html).toContain(`<img src="/api/projects/attachments/${ID}/image.png"`);
    expect(html).not.toContain("data-remote-image");
  });

  it("keeps the gate on any other image URL", () => {
    const html = render("![x](https://attacker.example/p.png?d=1)");
    expect(html).toContain("data-remote-image");
    expect(html).not.toContain("<img");
  });

  it("draws a placeholder as words, never as a broken image", () => {
    const view = forView(`a ${uploadPlaceholder("image.png")} b`);
    expect(view).toBe("a *Uploading image.png…* b");
    expect(render(view)).not.toContain("<img");
  });
});

describe("keepLineBreaks", () => {
  const render = (content: string) =>
    renderToStaticMarkup(createElement(MarkdownBody, { content: keepLineBreaks(content) }));

  it("keeps one typed newline as one line break", () => {
    expect(keepLineBreaks("one\ntwo")).toBe("one  \ntwo");
    expect(render("one\ntwo")).toContain("<br/>");
  });

  it("keeps a blank line a new paragraph", () => {
    expect(keepLineBreaks("one\n\ntwo")).toBe("one\n\ntwo");
  });

  it("leaves a fenced code block as it was written", () => {
    const code = "```\na\nb\n```\nafter\nline";
    expect(keepLineBreaks(code)).toBe("```\na\nb\n```\nafter  \nline");
  });

  it("keeps a list and a table drawing as a list and a table", () => {
    expect(render("- a\n- b")).toMatch(/<ul[\s\S]*<li[\s\S]*<li/);
    expect(render("| a | b |\n|---|---|\n| 1 | 2 |")).toContain("<table");
  });

  it("does not double a break the member already wrote", () => {
    expect(keepLineBreaks("one  \ntwo")).toBe("one  \ntwo");
    expect(keepLineBreaks("one\\\ntwo")).toBe("one\\\ntwo");
  });
});
