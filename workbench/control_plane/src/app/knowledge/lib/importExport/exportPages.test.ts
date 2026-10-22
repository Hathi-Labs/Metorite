import { describe, expect, it } from "vitest";

import { buildFiles, fileStem, htmlDocument, layoutExport, markdownDocument, type ExportNode } from "./exportPages";

const node = (id: string, title: string, children: ExportNode[] = []): ExportNode => ({ id, title, children });

describe("fileStem", () => {
  it("removes the characters that a file system refuses", () => {
    expect(fileStem('Q/A: "why?" <now> | *')).toBe("Q-A- -why-- -now- - -");
    expect(fileStem("a\\b")).toBe("a-b");
    expect(fileStem("tab\there\u0000")).toBe("tab-here-");
  });
  it("trims dots, spaces and length, and never gives an empty name", () => {
    expect(fileStem("  ..hidden. ")).toBe("hidden");
    expect(fileStem("x".repeat(300))).toHaveLength(100);
    expect(fileStem("...")).toBe("Untitled");
    expect(fileStem("")).toBe("Untitled");
  });
  it("keeps accents and guards device names", () => {
    expect(fileStem("Café menu")).toBe("Café menu");
    expect(fileStem("CON")).toBe("CON_");
  });
});

describe("layoutExport", () => {
  it("writes a file per page and a folder per page with children", () => {
    const layout = layoutExport([node("a", "Home", [node("b", "Sub", [node("c", "Deep")]), node("d", "Sibling")])], "md");
    expect(Object.fromEntries(layout.paths)).toEqual({
      a: "Home.md",
      b: "Home/Sub.md",
      c: "Home/Sub/Deep.md",
      d: "Home/Sibling.md",
    });
    expect(layout.order).toEqual(["a", "b", "c", "d"]);
  });
  it("numbers duplicates, ignoring case, in the same folder only", () => {
    const layout = layoutExport(
      [node("a", "Notes", [node("c", "Same"), node("d", "same")]), node("b", "notes", [node("e", "Same")])],
      "html",
    );
    expect(layout.paths.get("a")).toBe("Notes.html");
    expect(layout.paths.get("b")).toBe("notes 2.html");
    expect(layout.paths.get("c")).toBe("Notes/Same.html");
    expect(layout.paths.get("d")).toBe("Notes/same 2.html");
    expect(layout.paths.get("e")).toBe("notes 2/Same.html");
  });
});

describe("the documents", () => {
  it("writes Markdown as a heading and a body", () => {
    expect(markdownDocument("T", "Body\n\n")).toBe("# T\n\nBody\n");
    expect(markdownDocument("T", "")).toBe("# T\n");
  });
  it("writes a minimal HTML document, with the title escaped and no script", () => {
    const html = htmlDocument("A <b> & C", "<p>x</p>");
    expect(html).toContain("<title>A &lt;b&gt; &amp; C</title>");
    expect(html).toContain("<h1>A &lt;b&gt; &amp; C</h1>");
    expect(html.toLowerCase()).not.toContain("<script");
  });
});

describe("buildFiles", () => {
  it("links exported pages by relative file and others by address", () => {
    const layout = layoutExport([node("a", "Home", [node("b", "Sub page")])], "md");
    const bodies = new Map([
      ["a", "To [sub](/knowledge/pages/b) and [away](/knowledge/pages/zzz)."],
      ["b", "Back [home](/knowledge/pages/a)."],
    ]);
    const files = buildFiles(layout, bodies, "md", () => {
      throw new Error("no HTML");
    });
    expect(files).toEqual([
      { path: "Home.md", text: "# Home\n\nTo [sub](Home/Sub%20page.md) and [away](/knowledge/pages/zzz).\n" },
      { path: "Home/Sub page.md", text: "# Sub page\n\nBack [home](../Home.md).\n" },
    ]);
  });
  it("leaves out a page whose body did not load", () => {
    const layout = layoutExport([node("a", "A"), node("b", "B")], "md");
    expect(buildFiles(layout, new Map([["a", "x"]]), "md", String).map((f) => f.path)).toEqual(["A.md"]);
  });
  it("renders HTML through the given renderer, after the links are relative", () => {
    const layout = layoutExport([node("a", "Home", [node("b", "Sub")])], "html");
    const files = buildFiles(layout, new Map([["a", "[s](/knowledge/pages/b)"], ["b", ""]]), "html", (md) => `<p>${md}</p>`);
    expect(files[0].text).toContain("<p>[s](Home/Sub.html)</p>");
    expect(files[0].path).toBe("Home.html");
  });
});
