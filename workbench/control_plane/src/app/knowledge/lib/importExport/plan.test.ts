import { describe, expect, it } from "vitest";

import { ImportRefusal, MAX_DEPTH, MAX_PAGES } from "./limits";
import { buildPlan, toDraft, type HtmlConverter, type PageDraft } from "./plan";

const ID = "0123456789abcdef0123456789abcdef";
const ID2 = "fedcba9876543210fedcba9876543210";
const noHtml: HtmlConverter = () => {
  throw new Error("no HTML here");
};
const draft = (path: string, title = "T", body = ""): PageDraft => ({ path, title, body, images: 0 });
const tree = (plan: ReturnType<typeof buildPlan>) =>
  plan.pages.map((p) => `${p.parent === null ? "-" : plan.pages[p.parent].title} > ${p.title}${p.folder ? " [folder]" : ""}`);

describe("toDraft", () => {
  it("reads a Markdown title and removes the heading", () => {
    expect(toDraft({ path: "x.md", text: "# Hello\n\nText" }, noHtml)).toMatchObject({ title: "Hello", body: "Text" });
  });
  it("falls back to the file name, without the Notion id", () => {
    expect(toDraft({ path: `Leave ${ID}.md`, text: "Text" }, noHtml).title).toBe("Leave");
  });
  it("turns images into text and counts them", () => {
    const d = toDraft({ path: "x.md", text: "![Chart](c.png)" }, noHtml);
    expect(d).toMatchObject({ body: "*Chart*", images: 1 });
  });
  it("sends HTML through the converter", () => {
    const html: HtmlConverter = () => ({ title: "From HTML", markdown: "## Body", images: 2 });
    expect(toDraft({ path: "x.html", text: "<p>" }, html)).toMatchObject({ title: "From HTML", body: "## Body", images: 2 });
    const none: HtmlConverter = () => ({ title: null, markdown: "b", images: 0 });
    expect(toDraft({ path: "y.htm", text: "<p>" }, none).title).toBe("y");
  });
});

describe("the folder-to-parent mapping", () => {
  it("maps a Notion folder onto the page file beside it", () => {
    const plan = buildPlan([
      draft(`Handbook ${ID}.md`, "Handbook"),
      draft(`Handbook ${ID}/Leave ${ID2}.md`, "Leave"),
      draft(`Handbook ${ID}/Leave ${ID2}/Casual.md`, "Casual"),
    ]);
    expect(tree(plan)).toEqual(["- > Handbook", "Handbook > Leave", "Leave > Casual"]);
  });
  it("makes any other folder an empty parent page, titled by the folder", () => {
    const plan = buildPlan([draft("docs/a.md", "A"), draft("docs/guides/b.md", "B")]);
    expect(tree(plan)).toEqual(["- > docs [folder]", "docs > A", "docs > guides [folder]", "guides > B"]);
    expect(plan.pages.filter((p) => p.folder).every((p) => p.body === "")).toBe(true);
  });
  it("strips the Notion id from a folder title", () => {
    const plan = buildPlan([draft(`Team ${ID}/a.md`, "A")]);
    expect(plan.pages[0]).toMatchObject({ title: "Team", folder: true });
  });
  it("puts a loose file at the top", () => {
    expect(tree(buildPlan([draft("a.md", "A"), draft("b.html", "B")]))).toEqual(["- > A", "- > B"]);
  });
  it("lists a parent before its children, whatever the file order", () => {
    const plan = buildPlan([draft("z/deep/c.md", "C"), draft("a.md", "A"), draft("z.md", "Z")]);
    plan.pages.forEach((p, i) => {
      expect(p.index).toBe(i);
      if (p.parent !== null) expect(p.parent).toBeLessThan(i);
    });
    // `z.md` owns the folder `z`, so `deep` sits under Z and C under deep.
    expect(tree(plan)).toContain("Z > deep [folder]");
  });
  it("orders siblings by name, with numbers by value", () => {
    const plan = buildPlan([draft("p10.md", "p10"), draft("p2.md", "p2")]);
    expect(plan.pages.map((p) => p.title)).toEqual(["p2", "p10"]);
  });
  it("refuses when there is no page", () => {
    expect(() => buildPlan([])).toThrow(ImportRefusal);
  });
  it("carries the skipped files and the image count", () => {
    const plan = buildPlan([{ ...draft("a.md"), images: 3 }, { ...draft("b.md"), images: 1 }], ["x.png", "y.pdf"]);
    expect(plan.skipped).toEqual(["x.png", "y.pdf"]);
    expect(plan.images).toBe(4);
  });
});

describe("the page and depth limits", () => {
  it("allows 300 pages and refuses 301", () => {
    const files = (n: number) => Array.from({ length: n }, (_, i) => draft(`f${i}.md`, `F${i}`));
    expect(buildPlan(files(MAX_PAGES)).pages).toHaveLength(MAX_PAGES);
    expect(() => buildPlan(files(MAX_PAGES + 1))).toThrow(/301 pages/);
  });
  it("counts folder pages toward the 300", () => {
    const files = Array.from({ length: 150 }, (_, i) => draft(`d${i}/f.md`, `F${i}`));
    expect(() => buildPlan(files)).not.toThrow();
    expect(() => buildPlan([...files, draft("e/f.md")])).toThrow(/pages/);
  });
  it("allows depth 12 and refuses 13", () => {
    const deep = (levels: number) => draft(`${Array.from({ length: levels - 1 }, (_, i) => `d${i}`).join("/")}${levels > 1 ? "/" : ""}f.md`);
    expect(buildPlan([deep(MAX_DEPTH)]).pages.at(-1)?.depth).toBe(MAX_DEPTH);
    expect(() => buildPlan([deep(MAX_DEPTH + 1)])).toThrow(/13 levels deep/);
  });
});

describe("a plan with document files (KB-AI-1)", () => {
  it("makes an empty plan, not a refusal, when every document failed", () => {
    const plan = buildPlan([], [], { unreadable: [{ file: "scan.pdf", reason: "No text layer." }], notices: ["n"] });
    expect(plan.pages).toEqual([]);
    expect(plan.unreadable).toEqual([{ file: "scan.pdf", reason: "No text layer." }]);
    expect(plan.notices).toEqual(["n"]);
  });

  it("still refuses when there is nothing at all", () => {
    expect(() => buildPlan([], ["x.png"])).toThrow(/no page that can be read/);
  });

  it("keeps the failed file beside the pages that were made", () => {
    const plan = buildPlan([draft("a.docx", "A", "body")], [], { unreadable: [{ file: "b.xlsx", reason: "Empty." }] });
    expect(plan.pages.map((p) => p.title)).toEqual(["A"]);
    expect(plan.unreadable).toHaveLength(1);
  });

  it("does not let toDraft read a document", () => {
    expect(() => toDraft({ path: "a.pdf", text: "" }, () => ({ title: null, markdown: "", images: 0 }))).toThrow();
  });
});
