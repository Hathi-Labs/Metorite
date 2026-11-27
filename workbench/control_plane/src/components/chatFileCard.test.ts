/**
 * The save card of `edit_chat_file` (PAR-11c, task 4014): the member sees each
 * character that the save writes.
 *
 * The card reads a body of `key: value` lines as fields (`parseCardBody`). It
 * drops a `*_id` line, joins numbered keys and renames keys. So the tool starts
 * the body with two lines that are no field, and the card draws the body as
 * one text. The shared fixture `tests/fixtures/chat_file_card_cases.json` holds
 * each body as the browser gets it. `tests/unit/test_chat_workspace_tools.py`
 * makes the same bodies with the real tool.
 *
 * Mutation: send the new text as the body with no head lines, and each case of
 * fields goes red here (the Python side goes red too).
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

import ConfirmationCard, { parseCardBody } from "@/components/ConfirmationCard";
import { splitCardEmailText } from "@/lib/cardEmailText";

interface Case {
  name: string;
  content: string;
  context: string;
}

const CASES = (
  JSON.parse(
    readFileSync(resolve(__dirname, "../../../../tests/fixtures/chat_file_card_cases.json"), "utf-8"),
  ) as { cases: Case[] }
).cases;

/** The text of the one `data-card-text` block of the drawn card, unescaped. */
function drawnText(context: string): string[] {
  const html = renderToStaticMarkup(
    createElement(ConfirmationCard, {
      title: "Save this file?",
      detail: "outputs/report.md: the new text replaces all of the text.",
      context,
      onApprove: () => {},
      onReject: () => {},
    } as never),
  );
  return [...html.matchAll(/data-card-text="">([\s\S]*?)<\/p>/g)].map((m) =>
    m[1]
      .replace(/&lt;/g, "<")
      .replace(/&gt;/g, ">")
      .replace(/&quot;/g, '"')
      .replace(/&#x27;/g, "'")
      .replace(/&amp;/g, "&"),
  );
}

describe("the save card of a chat file", () => {
  it("holds the adversarial cases", () => {
    expect(CASES.length).toBeGreaterThanOrEqual(20);
  });

  it.each(CASES.map((c) => [c.name, c] as const))("%s: the body is one text, never fields", (_name, c) => {
    const body = parseCardBody(c.context);
    expect(body.fields).toEqual([]);
    expect(body.notes).toEqual([]);
    expect(body.trailing).toEqual([]);
    expect(body.text).toBe(c.context);
    // No email frame takes a part of the text away from the plain block.
    expect(splitCardEmailText(body.text)).toBeNull();
  });

  it.each(CASES.map((c) => [c.name, c] as const))("%s: the card draws each character of the text", (_name, c) => {
    const drawn = drawnText(c.context);
    expect(drawn).toEqual([c.context]);
    const text = c.content.trimEnd();
    // The new text is the end of the drawn block, whole, after the two head lines.
    expect(drawn[0].endsWith(text)).toBe(true);
    const head = drawn[0].slice(0, drawn[0].length - text.length).replace(/\n$/, "");
    expect(head.split("\n")).toHaveLength(2);
    expect(head).not.toMatch(/:/);
  });
});
