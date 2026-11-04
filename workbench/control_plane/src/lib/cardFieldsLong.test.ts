/**
 * Long card values (owner report, 2026-11-04, task 1566): a long text stacks
 * its row, and a change to a long text draws as a Before / Now block.
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

import CardFieldValue from "@/components/CardFieldValue";
import { LONG_VALUE_CHARS, isLongText, isLongTextChange, parseFieldValue, valueIsLong } from "@/lib/cardFields";

const LONG = "The in-product agents that read the board and draft the weekly update for the team lead";
const html = (value: string, kind: "text" | "status" = "text") =>
  renderToStaticMarkup(createElement(CardFieldValue, { value, kind, fenced: true }));

describe("the stack rule", () => {
  it("cuts at 80 characters or a newline", () => {
    expect(isLongText("a".repeat(LONG_VALUE_CHARS))).toBe(false);
    expect(isLongText("a".repeat(LONG_VALUE_CHARS + 1))).toBe(true);
    expect(isLongText("one\ntwo")).toBe(true);
  });
  it("reads a long side of a change as long", () => {
    expect(valueIsLong(parseFieldValue(`short → ${LONG}`))).toBe(true);
    expect(valueIsLong(parseFieldValue("short → also short"))).toBe(false);
  });
  it("makes a long text change a block, and not a status change", () => {
    expect(isLongTextChange(parseFieldValue(`${LONG} → new`), "text")).toBe(true);
    expect(isLongTextChange(parseFieldValue(`${LONG} → new`), "status")).toBe(false);
    expect(isLongTextChange(parseFieldValue("old → new"), "text")).toBe(false);
    expect(isLongTextChange(parseFieldValue(LONG), "text")).toBe(false);
  });
});

describe("a long text change", () => {
  it("draws Before, Show all and the full new value, with no arrow", () => {
    const out = html(`${LONG} → ${LONG} and more`);
    expect(out).toContain("Before");
    expect(out).toContain("Now");
    expect(out).toContain("Show all");
    expect(out).toContain('aria-expanded="false"');
    expect(out).toContain("line-clamp-2");
    expect(out).toContain(`${LONG} and more`);
    expect(out).not.toContain("changes to");
  });
  it("keeps the inline arrow for a short text and a status change", () => {
    expect(html("old → new")).toContain("changes to");
    expect(html("«Open» → «Done»", "status")).toContain("changes to");
  });
});
