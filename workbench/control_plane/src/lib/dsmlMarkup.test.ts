import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

import MarkdownMessage, { MarkdownBody } from "@/components/MarkdownMessage";
import { hasDsmlMarkup, stripDsmlMarkup } from "./dsmlMarkup";

// Owner report, 2026-10-11: a stored assistant message showed DeepSeek's DSML
// tool-call text. The Router fixes new answers. This fence keeps an OLD
// message, or one served without the Router, from showing the markup.

const BAR = String.fromCharCode(0xff5c);
const T = `${BAR}DSML${BAR}`;

const param = (name: string, value: string) =>
  `<${T}parameter name="${name}" string="true">${value}</${T}parameter>`;
const invoke = (name: string, ...params: string[]) =>
  `<${T}invoke name="${name}">${params.join(" ")}</${T}invoke>`;
const block = (kind: string, ...invokes: string[]) =>
  `<${T}${kind}> ${invokes.join(" ")} </${T}${kind}>`;

const OWNER = block(
  "calls",
  invoke(
    "update_task",
    param("task_id", "5ec5d369-7e1b-4d91-828e-ca103a83d5b3"),
    param("description", "Goal: ship the fix"),
  ),
);

describe("stripDsmlMarkup", () => {
  it("hides the owner's exact markup", () => {
    const out = stripDsmlMarkup(`Updating the task. ${OWNER}`);
    expect(out).toBe("Updating the task. ");
    expect(hasDsmlMarkup(out)).toBe(false);
  });

  it.each(["calls", "function_calls", "tool_calls"])("hides a %s block", (kind) => {
    const text = `a ${block(kind, invoke("x", param("p", "1")))} b`;
    expect(stripDsmlMarkup(text)).toBe("a  b");
  });

  it("hides several invokes, and keeps the text around them", () => {
    const text = `Before. ${block("calls", invoke("a"), invoke("b"))} After.`;
    expect(stripDsmlMarkup(text)).toBe("Before.  After.");
  });

  it("hides an unterminated block and everything after it", () => {
    const text = `Let me do it. <${T}calls> ${invoke("x", param("p", "half"))}`;
    expect(stripDsmlMarkup(text)).toBe("Let me do it. ");
  });

  it("keeps the text between a bare invoke and a later unclosed block", () => {
    const text = `a ${invoke("x")} b <${T}calls> cut`;
    expect(stripDsmlMarkup(text)).toBe("a  b ");
  });

  it("hides a stray tag and a marker cut off at the end", () => {
    expect(stripDsmlMarkup(`a </${T}invoke> b`)).toBe("a  b");
    expect(stripDsmlMarkup(`done <${T}inv`)).toBe("done ");
  });

  it.each(["```", "~~~", "````"])("keeps a block inside a %s fence as written", (fence) => {
    const text = `The email says:\n${fence}\n${OWNER}\n${fence}\nThat is all.`;
    expect(stripDsmlMarkup(text)).toBe(text);
  });

  it("keeps a block inside a fence in a list item as written", () => {
    const text = `1. The email:\n\n     \`\`\`\n     ${OWNER}\n     \`\`\`\n`;
    expect(stripDsmlMarkup(text)).toBe(text);
  });

  it("keeps an unclosed fence as written", () => {
    const text = "```text\n" + OWNER;
    expect(stripDsmlMarkup(text)).toBe(text);
  });

  it("strips outside a fence and keeps inside it, in one message", () => {
    const quoted = "```\n" + OWNER + "\n```\n";
    expect(stripDsmlMarkup(`${quoted}Now: ${OWNER}`)).toBe(`${quoted}Now: `);
  });

  it("keeps a marker that never forms a tag", () => {
    const text = `I saw <${T}hello world. More text follows.`;
    expect(stripDsmlMarkup(text)).toBe(text);
  });

  it("returns ordinary text as the SAME string", () => {
    const text = "a < b, c | d, a lone " + BAR + " and <b>html</b>";
    expect(stripDsmlMarkup(text)).toBe(text);
    expect(hasDsmlMarkup(text)).toBe(false);
  });

  it("keeps unicode around the markup", () => {
    expect(stripDsmlMarkup(`कार्य ✅ ${OWNER} “ok”`)).toBe("कार्य ✅  “ok”");
  });
});

// Owner report, 2026-10-17: an answer about outstanding payments showed the
// markup with TWO bars on each side of DSML. The bar code point could not be
// read, so the marker is tolerant. This fence holds the renderer to it.

const BB = BAR + BAR;
const LOW = String.fromCharCode(0x2581);
const BOX = String.fromCharCode(0x2502);
const DIVIDES = String.fromCharCode(0x2223);
const HALF = String.fromCharCode(0xffe8);

/** The owner's text of 2026-10-17, transcribed from the screenshot. */
const ownerParam = (name: string, value: string) =>
  `<${BB}DSML${BB}parameter name="${name}" string="true">${value}</${BB}DSML${BB}parameter>`;
const ownerInvoke = (measure: string) =>
  `<${BB}DSML${BB}invoke name="crm_aggregate"> ` +
  [
    ownerParam("entity", "module:Outstanding"),
    ownerParam("field", "Outstanding_Amount"),
    ownerParam("group_field", "Name"),
    ownerParam("measure", measure),
  ].join(" ") +
  ` </${BB}DSML${BB}invoke>`;
const OWNER_DOUBLE_BAR =
  `<${BB}DSML${BB}tool_calls> ${ownerInvoke("sum")} ${ownerInvoke("count")} ` +
  `</${BB}DSML${BB}tool_calls>`;

/** The canonical markup in `text`, rewritten with other delimiters. */
const variant = (text: string, left: string, right: string) =>
  text.split(T).join(`${left}DSML${right}`);

const VARIANTS: [string, string, string][] = [
  ["two bars", BB, BB],
  ["ASCII bars", "|", "|"],
  ["spaces inside", ` ${BAR} `, ` ${BAR}`],
  ["tabs inside", `\t${BAR}\t`, `\t${BAR}`],
  ["mixed bars", `${BAR}|`, `${BOX}${DIVIDES}${HALF}`],
  ["the longest form", `  ${BAR}  ${BAR}  ${BAR}  `, `  ${BAR}  ${BAR}  ${BAR}`],
];

const v3Token = (name: string) => `<${BAR}${name.split("_").join(LOW)}${BAR}>`;
const V3_CALL =
  v3Token("tool_calls_begin") +
  v3Token("tool_call_begin") +
  "function" +
  v3Token("tool_sep") +
  'update_task\n```json\n{"task_id": "t1"}\n```' +
  v3Token("tool_call_end") +
  v3Token("tool_calls_end");

describe("stripDsmlMarkup with a variant marker", () => {
  it("hides the owner's exact double-bar text", () => {
    const out = stripDsmlMarkup(`Let me check. ${OWNER_DOUBLE_BAR}`);
    expect(out).toBe("Let me check. ");
    expect(out).not.toContain("DSML");
    expect(out).not.toContain("crm_aggregate");
    expect(hasDsmlMarkup(OWNER_DOUBLE_BAR)).toBe(true);
  });

  it.each(VARIANTS)("hides a block with %s", (_name, left, right) => {
    const text = `On it. ${variant(OWNER, left, right)} Done.`;
    expect(stripDsmlMarkup(text)).toBe("On it.  Done.");
  });

  it.each(VARIANTS)("keeps a fenced block with %s as written", (_name, left, right) => {
    const text = `The email says:\n\`\`\`\n${variant(OWNER, left, right)}\n\`\`\`\nThat is all.`;
    expect(stripDsmlMarkup(text)).toBe(text);
  });

  it("hides a variant head cut off at the end", () => {
    expect(stripDsmlMarkup(`done <${BB}DSML|inv`)).toBe("done ");
  });

  it("keeps a marker with a space before the tag name, or four bars", () => {
    const spaced = `See <${BB}DSML${BB} invoke> here.`;
    expect(stripDsmlMarkup(spaced)).toBe(spaced);
    const four = `<${BB}${BB}DSML${BAR}invoke name="x"></${BB}${BB}DSML${BAR}invoke>`;
    expect(stripDsmlMarkup(four)).toBe(four);
  });

  it("returns ordinary text with the word DSML as the SAME string", () => {
    const text = "The DSML spec | v2, a < 3, a lone " + BAR + " and <b>x</b>";
    expect(hasDsmlMarkup(text)).toBe(false);
    expect(stripDsmlMarkup(text)).toBe(text);
  });
});

describe("stripDsmlMarkup with DeepSeek V3 tokens", () => {
  it("hides a V3 tool-call region", () => {
    expect(stripDsmlMarkup(`Checking. ${V3_CALL}\nDone.`)).toBe("Checking. \nDone.");
    expect(hasDsmlMarkup(V3_CALL)).toBe(true);
  });

  it("does not let the region's JSON fence quote the markup after it", () => {
    expect(stripDsmlMarkup(`${V3_CALL} then ${OWNER}`)).toBe(" then ");
  });

  it("keeps a fenced V3 region as written", () => {
    const text = `Quote:\n\`\`\`\n${V3_CALL}\n\`\`\`\nDone.`;
    expect(stripDsmlMarkup(text)).toBe(text);
  });

  it("hides an unterminated region, a stray token, and a variant bar", () => {
    const cut = V3_CALL.slice(0, V3_CALL.indexOf("```json") + 9);
    expect(stripDsmlMarkup(`Checking. ${cut}`)).toBe("Checking. ");
    expect(stripDsmlMarkup(`a ${v3Token("tool_sep")} b`)).toBe("a  b");
    expect(stripDsmlMarkup(`x ${V3_CALL.split(BAR).join(BB)}`)).toBe("x ");
  });
});

// The wiring fence: the ONE chat renderer must call the strip. Without this
// case, deleting the call from `MarkdownBody` would leave the suite green.

describe("the chat renderer", () => {
  it.each([false, true])("MarkdownBody hides the markup (pills %s)", (entityPills) => {
    const html = renderToStaticMarkup(
      createElement(MarkdownBody, { content: `Updating. ${OWNER} Done.`, entityPills }),
    );
    expect(html).toContain("Updating.");
    expect(html).toContain("Done.");
    expect(html).not.toContain("DSML");
    expect(html).not.toContain("5ec5d369");
  });

  it("MarkdownBody hides the owner's double-bar text and a V3 region", () => {
    const html = renderToStaticMarkup(
      createElement(MarkdownBody, {
        content: `Checking. ${OWNER_DOUBLE_BAR}\n\n${V3_CALL}\n\nDone.`,
        entityPills: false,
      }),
    );
    expect(html).toContain("Checking.");
    expect(html).toContain("Done.");
    expect(html).not.toContain("DSML");
    expect(html).not.toContain("crm_aggregate");
    expect(html).not.toContain(LOW);
  });

  it("MarkdownMessage hides it in a stored message", () => {
    const html = renderToStaticMarkup(
      createElement(MarkdownMessage, { content: `On it. ${OWNER}` }),
    );
    expect(html).toContain("On it.");
    expect(html).not.toContain("DSML");
  });
});
