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
  holdsPending,
  imageMarkdown,
  insertAtCaret,
  isInlineImage,
  keepLineBreaks,
  onOwnLine,
  partitionFiles,
  pastePlan,
  replaceFirst,
  settleUpload,
  uploadPlaceholder,
  withoutImages,
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
  it("reads as the spec writes it, with the number of its upload", () => {
    expect(uploadPlaceholder("image.png", 1)).toBe("![Uploading image.png… (1)]()");
  });

  it("keeps a name from breaking the Markdown", () => {
    expect(altText("a [b] c\\d\ne.png")).toBe("a b cde.png");
    expect(altText("[]")).toBe("image");
    expect(uploadPlaceholder("[x].png", 2)).toBe("![Uploading x.png… (2)]()");
  });

  it("is unique for two uploads of one name", () => {
    expect(uploadPlaceholder("image.png", 1)).not.toBe(uploadPlaceholder("image.png", 2));
  });
});

describe("holdsPending (the save guard)", () => {
  const mine = uploadPlaceholder("image.png", 7);

  it("is true while the text holds a placeholder this client waits on", () => {
    expect(holdsPending(`before ${mine} after`, [mine])).toBe(true);
    expect(holdsPending(`before ${mine} after`, new Set([mine]))).toBe(true);
  });

  it("is false once the upload ended, whatever the text holds", () => {
    expect(holdsPending(`before ${mine} after`, [])).toBe(false);
  });

  it("never blocks a save for text that only looks like a placeholder", () => {
    // An agent, or a member, can write this string. It is not ours.
    const written = "Notes ![Uploading x…]() and ![Uploading image.png… (3)]()";
    expect(holdsPending(written, [mine])).toBe(false);
    expect(holdsPending(written, [])).toBe(false);
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
  const P = uploadPlaceholder("image.png", 1);

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

describe("two uploads of one name (review finding P2)", () => {
  /** Paste one image at `at`, the way the hook does. */
  const paste = (text: string, at: number, token: string) =>
    insertAtCaret(text, at, at, onOwnLine(text, at, at, token)).value;

  // Chrome names every pasted screenshot `image.png`.
  const first = uploadPlaceholder("image.png", 1);
  const second = uploadPlaceholder("image.png", 2);
  const md1 = "![image.png](/api/projects/attachments/a/1.png)";
  const md2 = "![image.png](/api/projects/attachments/b/2.png)";

  // The first goes at the end, the second ABOVE it, at the top.
  let text = paste("Intro\nOutro", "Intro\nOutro".length, first);
  text = paste(text, 0, second);

  it("puts the second placeholder above the first", () => {
    expect(text).toBe(`${second}\nIntro\nOutro\n${first}`);
  });

  it("lands each image in its own place when they end out of order", () => {
    // The first upload ends first, though its placeholder is the LOWER one.
    const afterFirst = settleUpload(text, first, md1);
    expect(afterFirst).toBe(`${second}\nIntro\nOutro\n${md1}`);
    expect(settleUpload(afterFirst, second, md2)).toBe(`${md2}\nIntro\nOutro\n${md1}`);
    // And the other way round.
    const afterSecond = settleUpload(text, second, md2);
    expect(settleUpload(afterSecond, first, md1)).toBe(`${md2}\nIntro\nOutro\n${md1}`);
  });

  it("removes only its own placeholder when one upload fails", () => {
    const failed = settleUpload(text, second, null);
    expect(failed).toBe(`Intro\nOutro\n${first}`);
    expect(settleUpload(failed, first, md1)).toBe(`Intro\nOutro\n${md1}`);
    expect(settleUpload(text, first, null)).toBe(`${second}\nIntro\nOutro\n`);
  });
});

describe("replaceFirst", () => {
  it("removes a placeholder when the replacement is empty", () => {
    const P = uploadPlaceholder("x.png", 1);
    expect(replaceFirst(`a ${P} b`, P, "")).toBe("a  b");
  });

  it("leaves the text alone when the member deleted the placeholder", () => {
    expect(replaceFirst("typed over", uploadPlaceholder("x.png", 1), "![x](/1)")).toBe("typed over");
    expect(settleUpload("typed over", uploadPlaceholder("x.png", 1), null)).toBe("typed over");
  });

  it("pastes a $ in the replacement as it is", () => {
    expect(replaceFirst("a T b", "T", "$`$&$'")).toBe("a $`$&$' b");
  });
});

describe("withoutImages (the reply excerpt)", () => {
  it("shows an image as its alt text, or as [image]", () => {
    expect(withoutImages("Look ![screen](/api/x/1.png) here")).toBe("Look [screen] here");
    expect(withoutImages("![](/api/x/1.png)")).toBe("[image]");
  });

  it("leaves a link and plain text alone", () => {
    expect(withoutImages("see [spec](https://example.com)")).toBe("see [spec](https://example.com)");
  });
});

describe("mentions stay plain text on the task card (review finding P2)", () => {
  const render = (content: string, plainEmails: boolean) =>
    renderToStaticMarkup(createElement(MarkdownBody, { content, plainEmails }));

  it("draws an @mention and a bare email as text, with no mailto link", () => {
    const html = render("Thanks @asha@example.com, ask bob@example.com", true);
    expect(html).not.toContain("mailto:");
    expect(html).not.toContain("<a");
    expect(html).toContain("@asha@example.com");
    expect(html).toContain("bob@example.com");
  });

  it("keeps a web link a link", () => {
    expect(render("see [spec](https://example.com/spec)", true)).toContain('href="https://example.com/spec"');
  });

  it("leaves the chat's rendering as it was: an email is a mailto link there", () => {
    expect(render("ask bob@example.com", false)).toContain('href="mailto:bob@example.com"');
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
    const view = forView(`a ${uploadPlaceholder("image.png", 4)} b`);
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
