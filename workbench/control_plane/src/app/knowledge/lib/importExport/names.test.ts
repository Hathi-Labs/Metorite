import { describe, expect, it } from "vitest";

import { cleanTitle, imagesToText, mapOutsideCode, splitMarkdownTitle, stemOf, stripNotionId, titleFromPath } from "./names";

const ID = "0123456789abcdef0123456789abcdef";

describe("the Notion title strip", () => {
  it("takes the trailing 32-hex id off", () => {
    expect(stripNotionId(`Refund policy ${ID}`)).toBe("Refund policy");
    expect(stripNotionId(`Refund policy ${ID.toUpperCase()}`)).toBe("Refund policy");
  });
  it("keeps a name that has no id, or a short one", () => {
    expect(stripNotionId("Refund policy")).toBe("Refund policy");
    expect(stripNotionId("Plan abc123")).toBe("Plan abc123");
    expect(stripNotionId(`${ID} Plan`)).toBe(`${ID} Plan`);
  });
  it("keeps a name that is only an id", () => {
    expect(stripNotionId(ID)).toBe(ID);
  });
});

describe("titles from paths", () => {
  it("drops the folder, the extension and the id", () => {
    expect(titleFromPath(`Handbook ${ID}/Leave ${ID}.md`)).toBe("Leave");
    expect(titleFromPath("notes/ideas.markdown")).toBe("ideas");
    expect(titleFromPath("page.HTML")).toBe("page");
  });
  it("keeps an extension that is not a page", () => {
    expect(stemOf("a/b.txt")).toBe("a/b.txt");
  });
  it("cuts a long title and never gives an empty one", () => {
    expect(cleanTitle("x".repeat(400))).toHaveLength(300);
    expect(cleanTitle("   ")).toBe("Untitled");
    expect(cleanTitle("a \n  b")).toBe("a b");
  });
});

describe("Markdown title extraction", () => {
  it("takes the first # heading and removes it", () => {
    expect(splitMarkdownTitle("# Refund policy\n\nBody text.\n")).toEqual({ title: "Refund policy", body: "Body text." });
  });
  it("finds a heading after other text", () => {
    expect(splitMarkdownTitle("Intro line\n\n# Title\n\nMore")).toEqual({ title: "Title", body: "Intro line\n\nMore" });
  });
  it("ignores ## headings and the closing hashes", () => {
    expect(splitMarkdownTitle("## Sub\n\ntext")).toEqual({ title: null, body: "## Sub\n\ntext" });
    expect(splitMarkdownTitle("# Title ##\ntext").title).toBe("Title");
  });
  it("skips fenced code", () => {
    const md = "```\n# not a title\n```\n\n# Real";
    expect(splitMarkdownTitle(md).title).toBe("Real");
  });
  it("skips front matter", () => {
    const md = "---\ntitle: x\n# not\n---\n# Real\ntext";
    expect(splitMarkdownTitle(md)).toEqual({ title: "Real", body: "---\ntitle: x\n# not\n---\ntext" });
  });
  it("gives no title when there is no heading", () => {
    expect(splitMarkdownTitle("just text\n")).toEqual({ title: null, body: "just text" });
  });
});

describe("images", () => {
  it("become the alt text in italics, or the file name", () => {
    expect(imagesToText("A ![Team photo](img/team.png) B").md).toBe("A *Team photo* B");
    expect(imagesToText("![](img/My%20Chart.png)").md).toBe("*[image: My Chart.png]*");
    expect(imagesToText("![x](data:image/png;base64,AAAA)").md).toBe("*x*");
    expect(imagesToText("![](data:image/png;base64,AAAA)").md).toBe("*[image: image]*");
  });
  it("count and leave links and code alone", () => {
    const out = imagesToText("[a](b.md) ![i](c.png) `![k](l.png)`\n```\n![m](n.png)\n```");
    expect(out.count).toBe(1);
    expect(out.md).toBe("[a](b.md) *i* `![k](l.png)`\n```\n![m](n.png)\n```");
  });
  it("keep a link around an image", () => {
    expect(imagesToText("[![logo](l.png)](https://x.example)").md).toBe("[*logo*](https://x.example)");
  });
});

describe("mapOutsideCode", () => {
  it("skips fences and inline code", () => {
    expect(mapOutsideCode("a `a` a\n~~~\na\n~~~\na", (t) => t.replace(/a/g, "b"))).toBe("b `a` b\n~~~\na\n~~~\nb");
  });
});

describe("document kinds (KB-AI-1)", () => {
  it("knows the four document kinds and keeps md and html", async () => {
    const { pageKind, isDocumentKind, stemOf } = await import("./names");
    expect(["a.DOCX", "b.pdf", "c.pptx", "d.xlsx", "e.md", "f.html", "g.png"].map(pageKind)).toEqual([
      "docx", "pdf", "pptx", "xlsx", "md", "html", null,
    ]);
    expect(isDocumentKind("pdf")).toBe(true);
    expect(isDocumentKind("md")).toBe(false);
    expect(isDocumentKind(null)).toBe(false);
    expect(stemOf("dir/Report.pdf")).toBe("dir/Report");
  });
});
