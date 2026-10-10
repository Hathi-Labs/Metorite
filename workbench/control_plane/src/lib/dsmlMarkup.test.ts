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

  it("MarkdownMessage hides it in a stored message", () => {
    const html = renderToStaticMarkup(
      createElement(MarkdownMessage, { content: `On it. ${OWNER}` }),
    );
    expect(html).toContain("On it.");
    expect(html).not.toContain("DSML");
  });
});
