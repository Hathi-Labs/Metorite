import { describe, expect, it } from "vitest";

import { notesText, paragraphText, readSlide, slideToMarkdown } from "./pptx";
import { el, para, sp } from "./xmlTree.testkit";

const slide = (...shapes: ReturnType<typeof sp>[]) => el("sld", {}, el("cSld", {}, el("spTree", {}, ...shapes)));

describe("pptx slide to Markdown", () => {
  it("joins the runs and fields of a paragraph, and a break is a space", () => {
    const p = el("p", {}, el("r", {}, el("t", {}, "Q3 ")), el("br"), el("fld", {}, el("t", {}, "plan")), el("r", {}, el("t", {}, " now")));
    expect(paragraphText(p)).toBe("Q3 plan now");
  });

  it("takes the title from the title placeholder and the rest as bullets by level", () => {
    const read = readSlide(slide(sp("title", para("Launch plan")), sp("body", para("Goals"), para("Ship it", 1), para("Test it", 2)), sp("sldNum", para("7"))));
    expect(read.title).toBe("Launch plan");
    expect(read.bullets).toEqual([
      { level: 0, text: "Goals" },
      { level: 1, text: "Ship it" },
      { level: 2, text: "Test it" },
    ]);
    const md = slideToMarkdown(2, read).markdown;
    expect(md).toBe("## Slide 2: Launch plan\n\n- Goals\n  - Ship it\n    - Test it");
  });

  it("takes ctrTitle as a title and a typeless placeholder as a body", () => {
    const read = readSlide(slide(sp("ctrTitle", para("Welcome")), sp("", para("Hello"))));
    expect(read.title).toBe("Welcome");
    expect(read.bullets).toEqual([{ level: 0, text: "Hello" }]);
  });

  it("names a slide with no title by its number", () => {
    const out = slideToMarkdown(3, readSlide(slide(sp(null, para("Only text")))));
    expect(out.markdown).toBe("## Slide 3\n\n- Only text");
    expect(out.hasText).toBe(true);
  });

  it("reports a slide with nothing as no text", () => {
    expect(slideToMarkdown(1, readSlide(slide())).hasText).toBe(false);
  });

  it("escapes Markdown marks in slide text", () => {
    const md = slideToMarkdown(1, readSlide(slide(sp("title", para("A *big* <b>")), sp(null, para("- not a nested bullet"))))).markdown;
    expect(md).toContain("## Slide 1: A \\*big\\* \\<b\\>");
    expect(md).toContain("- \\- not a nested bullet");
  });

  it("makes a table from a:tbl", () => {
    const tc = (t: string) => el("tc", {}, el("txBody", {}, para(t)));
    const tbl = el("tbl", {}, el("tr", {}, tc("Plan"), tc("Cost")), el("tr", {}, tc("Core"), tc("500")));
    const read = readSlide(el("sld", {}, el("cSld", {}, el("spTree", {}, el("graphicFrame", {}, el("graphic", {}, el("graphicData", {}, tbl)))))));
    expect(slideToMarkdown(1, read).markdown).toContain("| Plan | Cost |\n| --- | --- |\n| Core | 500 |");
  });

  it("puts the notes under a Notes line, from the body placeholder only", () => {
    const notes = el("notes", {}, el("cSld", {}, el("spTree", {}, sp("sldImg"), sp("body", para("Say hello"), para("Then pause")), sp("sldNum", para("2")))));
    expect(notesText(notes)).toEqual(["Say hello", "Then pause"]);
    const md = slideToMarkdown(1, readSlide(slide(sp("title", para("Hi")))), notesText(notes)).markdown;
    expect(md).toBe("## Slide 1: Hi\n\n**Notes**\n\nSay hello\n\nThen pause");
  });
});
