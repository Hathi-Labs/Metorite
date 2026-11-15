/**
 * HRD-7 (`chat_failure_modes.md` C1): the leads of the card outcomes are two
 * copies of one table. The server writes each text from
 * `ask_tools.CARD_OUTCOMES`, and the chat cards draw by `CARD_OUTCOME_LEADS`.
 * This test reads the Python source and holds the two equal, so an edit on
 * one side alone fails here instead of drawing a decline as a success.
 * `tests/unit/test_card_outcome_vocabulary.py` reads this side too.
 */
import fs from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { CARD_OUTCOME_LEADS, NOT_DONE_AT_CARD, cardOutcomeOf, type CardOutcome } from "./cardOutcomes";

const ASK_TOOLS = path.join(
  __dirname, "../../../../packages/acb_skills/acb_skills/ask_tools.py",
);

/** The outcome word of each constant, and the lead of each table row. */
function pythonTable(): [string, string][] {
  const source = fs.readFileSync(ASK_TOOLS, "utf8");
  const words = new Map<string, string>();
  for (const m of source.matchAll(/^(CARD_[A-Z_]+|CLOSED_BY_YOUR_MESSAGE) = "([^"]+)"$/gm)) {
    words.set(m[1], m[2]);
  }
  const start = source.indexOf("CARD_OUTCOMES: dict[str, CardOutcome] = {");
  const table = source.slice(start, source.indexOf("\n}\n", start));
  const rows: [string, string][] = [];
  for (const m of table.matchAll(/^ {4}(CARD_[A-Z_]+|CLOSED_BY_YOUR_MESSAGE): CardOutcome\(\s*"([^"]+)",/gm)) {
    rows.push([words.get(m[1]) ?? `?${m[1]}`, m[2]]);
  }
  return rows;
}

describe("the leads of the card outcomes", () => {
  it("equal the Python table, outcome by outcome and in order", () => {
    const rows = pythonTable();
    expect(rows.length).toBe(8);
    expect(rows).toEqual(Object.entries(CARD_OUTCOME_LEADS));
  });

  it("are each a different word", () => {
    const leads = Object.values(CARD_OUTCOME_LEADS);
    expect(new Set(leads).size).toBe(leads.length);
  });

  it("read each outcome back from its text", () => {
    for (const [outcome, lead] of Object.entries(CARD_OUTCOME_LEADS)) {
      expect(cardOutcomeOf(`  ${lead}: nothing was done.`)).toBe(outcome as CardOutcome);
    }
    expect(cardOutcomeOf("Cancelled — nothing was changed.")).toBeNull();
    expect(cardOutcomeOf("Declined offers")).toBeNull();
    expect(cardOutcomeOf(undefined)).toBeNull();
  });

  it("draw each outcome that did nothing as cancelled, but a message and an approve", () => {
    expect(NOT_DONE_AT_CARD.has("approved")).toBe(false);
    expect(NOT_DONE_AT_CARD.has("closed by your message")).toBe(false);
    expect(NOT_DONE_AT_CARD.size).toBe(Object.keys(CARD_OUTCOME_LEADS).length - 2);
  });
});
