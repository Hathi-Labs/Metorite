/**
 * The entity-pill remark plugin and the missing-space fix (WS-27bm S9, spec
 * §15). The trees here are the shapes remark and remark-gfm hand the plugin.
 * `components/markdownPills.test.ts` runs the same plugin through the real
 * renderer.
 */
import { describe, expect, it } from "vitest";

import remarkEntityPills, {
  PILL_ATTR,
  PILL_NUMBER_ATTR,
  PILL_GROUP_CLASS,
  PILL_TEXT_ATTR,
  spaceBeforeBold,
  splitCodeSpans,
  splitText,
  type MdNode,
} from "./remarkEntityPills";

function run(children: MdNode[]): MdNode[] {
  const tree: MdNode = { type: "root", children: [{ type: "paragraph", children }] };
  remarkEntityPills()(tree);
  return tree.children![0].children!;
}

/** Pills at this level, and inside a pill-and-punctuation group. */
const flat = (nodes: MdNode[]): MdNode[] =>
  nodes.flatMap((n) => (n.type === "entityPillGroup" ? n.children! : [n]));

const pills = (nodes: MdNode[]) =>
  flat(nodes)
    .filter((n) => n.type === "entityPill")
    .map((n) => ({
      text: n.data!.hProperties![PILL_TEXT_ATTR],
      number: n.data!.hProperties![PILL_NUMBER_ATTR],
    }));

const texts = (nodes: MdNode[]) =>
  flat(nodes).filter((n) => n.type === "text").map((n) => n.value).join("|");

describe("splitText", () => {
  it("turns a fenced name into a pill, with no marks left", () => {
    const out = splitText("Look at «Projects/Tasks App» now");
    expect(pills(out)).toEqual([{ text: "Projects/Tasks App", number: undefined }]);
    expect(texts(out)).toBe("Look at | now");
    expect(out[1].data!.hProperties![PILL_ATTR]).toBe("1");
  });

  it("absorbs a #n before the name into one task pill", () => {
    expect(pills(splitText("#5 «Notification engine»"))).toEqual([
      { text: "Notification engine", number: "#5" },
    ]);
  });

  it("turns a bare email into a person pill", () => {
    const out = splitText("assigned to vjvarada@hathilabs.com.");
    expect(pills(out)).toEqual([{ text: "vjvarada@hathilabs.com", number: undefined }]);
    expect(texts(out)).toBe("assigned to |.");
  });

  it("keeps the punctuation after a pill with the pill, and adds no space", () => {
    const out = splitText("In «Hathi Labs», the task");
    const group = out.find((n) => n.type === "entityPillGroup")!;
    expect(group.data!.hProperties).toEqual({ className: PILL_GROUP_CLASS });
    expect(group.children!.map((n) => n.type)).toEqual(["entityPill", "text"]);
    expect(group.children![1].value).toBe(",");
    expect(out[out.length - 1]).toEqual({ type: "text", value: " the task" });
  });

  it("drops a stray mark", () => {
    expect(texts(splitText("an «unclosed name"))).toBe("an unclosed name");
  });
});

describe("the plugin", () => {
  it("unwraps **«X»** so the name is a pill and not bold", () => {
    const out = run([
      { type: "text", value: "- " },
      { type: "strong", children: [{ type: "text", value: "«Projects/Tasks App»" }] },
      { type: "text", value: " — 15 tasks" },
    ]);
    expect(out.some((n) => n.type === "strong")).toBe(false);
    expect(pills(out)).toEqual([{ text: "Projects/Tasks App", number: undefined }]);
  });

  it("absorbs #n across the bold: #5 **«X»**", () => {
    const out = run([
      { type: "text", value: "#5 " },
      { type: "strong", children: [{ type: "text", value: "«Notification engine for projects»" }] },
    ]);
    expect(pills(out)).toEqual([{ text: "Notification engine for projects", number: "#5" }]);
    expect(texts(out)).toBe("");
  });

  it("keeps a bold that is more than one name", () => {
    const out = run([
      { type: "strong", children: [{ type: "text", value: "«A» and «B»" }] },
    ]);
    expect(out[0].type).toBe("strong");
    expect(pills(out[0].children!)).toHaveLength(2);
  });

  it("turns remark-gfm's mailto autolink back into a person pill", () => {
    const out = run([
      { type: "text", value: "assigned to " },
      {
        type: "link",
        url: "mailto:vjvarada@hathilabs.com",
        children: [{ type: "text", value: "vjvarada@hathilabs.com" }],
      },
    ]);
    expect(out.some((n) => n.type === "link")).toBe(false);
    expect(pills(out)).toEqual([{ text: "vjvarada@hathilabs.com", number: undefined }]);
  });

  it("leaves inline code and code blocks alone", () => {
    const tree: MdNode = {
      type: "root",
      children: [
        { type: "paragraph", children: [{ type: "inlineCode", value: "«X» a@b.io" }] },
        { type: "code", value: "«Y»" },
      ],
    };
    remarkEntityPills()(tree);
    expect(tree.children![0].children![0]).toEqual({ type: "inlineCode", value: "«X» a@b.io" });
    expect(tree.children![1]).toEqual({ type: "code", value: "«Y»" });
  });

  it("draws no pill inside a real link, and no marks either", () => {
    const out = run([
      { type: "link", url: "https://x.io", children: [{ type: "text", value: "«Docs»" }] },
    ]);
    expect(out[0].type).toBe("link");
    expect(out[0].children).toEqual([{ type: "text", value: "Docs" }]);
  });
});

describe("spaceBeforeBold", () => {
  it("puts the space back before an opening bold", () => {
    expect(spaceBeforeBold("It is due today.**Early stages** are done.")).toBe(
      "It is due today. **Early stages** are done.",
    );
  });

  it("puts it after a closing bold, so the bold still closes", () => {
    expect(spaceBeforeBold("**It is done.**Next we ship.")).toBe("**It is done.** Next we ship.");
  });

  it("does not touch code, a URL or a number", () => {
    expect(spaceBeforeBold("run `a.**b`")).toBe("run `a.**b`");
    expect(spaceBeforeBold("```\ntoday.**Early\n```")).toBe("```\ntoday.**Early\n```");
    expect(spaceBeforeBold("see https://x.io/a.**b")).toBe("see https://x.io/a.**b");
    expect(spaceBeforeBold("version 3.**Beta**")).toBe("version 3.**Beta**");
  });

  // S9 fix round 1: three ways the first version damaged text.
  it("counts a bold that spans two lines of one paragraph", () => {
    expect(spaceBeforeBold("Intro **spans\nline.**Next")).toBe("Intro **spans\nline.** Next");
    // A blank line starts the count again.
    expect(spaceBeforeBold("An **open\n\ntoday.**Early**")).toBe("An **open\n\ntoday. **Early**");
  });

  it("leaves a code span alone when it holds a backtick (fix round 3)", () => {
    expect(spaceBeforeBold("``a`b.**c**`` d.**e**")).toBe("``a`b.**c**`` d. **e**");
    // A run with no partner of the same length is text, as CommonMark reads it.
    expect(splitCodeSpans("``a`b`` x")).toEqual(["", "``a`b``", " x"]);
    expect(splitCodeSpans("a `b` c")).toEqual(["a ", "`b`", " c"]);
    expect(splitCodeSpans("a ``b` c")).toEqual(["a ``b` c"]);
  });

  it("leaves a line indented four spaces alone", () => {
    expect(spaceBeforeBold("    x = obj.**kwargs")).toBe("    x = obj.**kwargs");
    expect(spaceBeforeBold("\tx = obj.**kwargs")).toBe("\tx = obj.**kwargs");
  });

  it("closes a fence only on a fence at least as long", () => {
    const text = "````\n```\nobj.**kwargs\n````\ntoday.**Early**";
    expect(spaceBeforeBold(text)).toBe("````\n```\nobj.**kwargs\n````\ntoday. **Early**");
    const tilde = "~~~\n```\na.**b\n~~~";
    expect(spaceBeforeBold(tilde)).toBe(tilde);
  });

  it("leaves text with a space already, or with no bold", () => {
    expect(spaceBeforeBold("today. **Early**")).toBe("today. **Early**");
    expect(spaceBeforeBold("today.Early")).toBe("today.Early");
  });
});
