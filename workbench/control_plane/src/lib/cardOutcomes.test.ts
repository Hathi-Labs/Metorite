/**
 * HRD-7 (`chat_failure_modes.md` C1): the rows of the card outcomes are two
 * copies of one table. The server writes each text from
 * `ask_tools.CARD_OUTCOMES`, and the chat cards draw by `CARD_OUTCOME_TEXTS`.
 * This test reads the Python source and holds the two equal, so an edit on
 * one side alone fails here instead of drawing a decline as a success, or a
 * step for the model in front of the member.
 * `tests/unit/test_card_outcome_vocabulary.py` reads this side too.
 */
import fs from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import {
  CARD_OUTCOME_LEADS,
  CARD_OUTCOME_MEMBER_LINES,
  CARD_OUTCOME_TEXTS,
  NOT_DONE_AT_CARD,
  cardOutcomeOf,
  memberLineOf,
  type CardOutcome,
} from "./cardOutcomes";

const ROOT = path.join(__dirname, "../../../..");
const ASK_TOOLS = path.join(ROOT, "packages/acb_skills/acb_skills/ask_tools.py");

type Row = { lead: string; happened: string; next: string };

/** Each row of the Python table: the outcome word, then lead, happened, next.
 *  Adjacent string literals join, as Python joins them. */
function pythonTable(): [string, Row][] {
  const source = fs.readFileSync(ASK_TOOLS, "utf8").replace(/\r\n/g, "\n");
  const words = new Map<string, string>();
  for (const m of source.matchAll(/^(CARD_[A-Z_]+|CLOSED_BY_YOUR_MESSAGE) = "([^"]+)"$/gm)) {
    words.set(m[1], m[2]);
  }
  const start = source.indexOf("CARD_OUTCOMES: dict[str, CardOutcome] = {");
  const table = source.slice(start, source.indexOf("\n}\n", start));
  const rows: [string, Row][] = [];
  for (const m of table.matchAll(/^ {4}(CARD_[A-Z_]+|CLOSED_BY_YOUR_MESSAGE): CardOutcome\(([\s\S]*?)\),$/gm)) {
    const parts: string[] = [""];
    for (const t of m[2].matchAll(/"((?:[^"\\]|\\.)*)"|,/g)) {
      if (t[0] === ",") parts.push("");
      else parts[parts.length - 1] += t[1];
    }
    const [lead, happened, next] = parts;
    rows.push([words.get(m[1]) ?? `?${m[1]}`, { lead, happened, next }]);
  }
  return rows;
}

const ACT = "No lead was created for Veena Rao.";

describe("the rows of the card outcomes", () => {
  it("equal the Python table, part by part and in order", () => {
    const rows = pythonTable();
    expect(rows.length).toBe(8);
    expect(rows).toEqual(Object.entries(CARD_OUTCOME_TEXTS));
  });

  it("have each a different lead", () => {
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

  it("draw each outcome that did nothing as cancelled, but a message, a park and an approve", () => {
    expect(NOT_DONE_AT_CARD.has("approved")).toBe(false);
    expect(NOT_DONE_AT_CARD.has("closed by your message")).toBe(false);
    // A parked card still waits for the member. Mutation: add it back → red.
    expect(NOT_DONE_AT_CARD.has("parked")).toBe(false);
    expect(NOT_DONE_AT_CARD.size).toBe(Object.keys(CARD_OUTCOME_LEADS).length - 3);
  });
});

/** The words that only the model may read. */
const MODEL_WORDS = /the member|\bTell\b|\bRead\b|Do the act|Ask whether|Ask them/i;

describe("the member line of each outcome", () => {
  const rows = pythonTable();

  it("has one line for each outcome of the Python table, and no other", () => {
    expect(Object.keys(CARD_OUTCOME_MEMBER_LINES)).toEqual(rows.map(([word]) => word));
  });

  it("speaks to the member, and holds no word or step for the model", () => {
    for (const [word, r] of rows) {
      const line = CARD_OUTCOME_MEMBER_LINES[word as CardOutcome];
      expect(line, word).toMatch(/\.$/);
      // Mutation: put "the member" in a line → red.
      expect(line, word).not.toMatch(MODEL_WORDS);
      expect(line, word).not.toContain(r.next);
      expect(line, word).not.toContain(r.happened);
      expect(line, word).not.toContain(";");
    }
  });
});

describe("the line that a member reads (memberLineOf)", () => {
  // Each text as the server builds it (`card_outcome_text`), from the PYTHON
  // rows, so the cut is held to the server's own head (`card_outcome_head`).
  const rows = pythonTable();

  it("is the member line of each outcome, with its act, and nothing for the model", () => {
    for (const [word, r] of rows) {
      const full = `${r.lead}: ${r.happened} ${ACT} ${r.next}`;
      const line = memberLineOf(full);
      expect(line, word).toBe(`${CARD_OUTCOME_MEMBER_LINES[word as CardOutcome]} ${ACT}`);
      expect(line, word).not.toContain(r.next);
      expect(line, word).not.toContain(r.happened);
      // Mutation: return the whole text → red.
      expect(line, word).not.toMatch(MODEL_WORDS);
    }
  });

  it("is the member line alone when a text has no act", () => {
    for (const [word, r] of rows) {
      expect(memberLineOf(`${r.lead}: ${r.happened} ${r.next}`), word).toBe(
        CARD_OUTCOME_MEMBER_LINES[word as CardOutcome]);
    }
  });

  it("drops a next step of a family's own, and keeps no step for the model", () => {
    const own = CARD_OUTCOME_TEXTS["closed by your message"];
    const email = `${own.lead}: ${own.happened} Nothing was sent. The member's words come next, as their own chat message. If they change this mail, change it.`;
    expect(memberLineOf(email)).toBe(CARD_OUTCOME_MEMBER_LINES["closed by your message"]);
  });

  it("leaves a stored result of before HRD-7 as it was", () => {
    for (const legacy of [
      "Cancelled — nothing was changed.",
      "Send cancelled — the email was not sent.",
      "Cancelled — no lead was created for Veena Rao.",
      "The member wrote in the chat while the card was open, so the card is closed. Nothing was changed.",
      "Nothing changed. This needs your approval on a card, and a card cannot show here.",
    ]) {
      expect(memberLineOf(legacy)).toBe(legacy);
    }
  });

  it("leaves a text that only starts with a lead word as it was", () => {
    // A read can start with a lead and a colon. It is no card outcome.
    const read = "Declined: 3 deals this month\n- Orbit\n- Neuropeak";
    expect(memberLineOf(read)).toBe(read);
    expect(memberLineOf("Email sent to kim@contoso.test.")).toBe("Email sent to kim@contoso.test.");
    expect(memberLineOf(undefined)).toBe("");
  });
});

describe("each receipt draws the member line", () => {
  // The drawing sites read the result through `memberLineOf`, so no step for
  // the model reaches the member. Mutation: draw `result` itself in any of
  // them → red.
  const read = (rel: string) => fs.readFileSync(path.join(__dirname, rel), "utf8").replace(/\r\n/g, "\n");

  it("in Projects: the write receipt and the batch receipt", () => {
    const src = read("../components/projects/ProjectToolCards.tsx");
    expect(src).toContain("const detail = forPeopleFenced(memberLineOf(result));");
    expect(src).toContain("forPeopleFenced(withoutLegend(memberLineOf(result)).split(NL)[0]");
    // The line draws under the title, never beside it: on a phone a line
    // beside it cut "Cancelled" to "Ca…".
    expect(src).toContain("oneLine(detail) && cardOutcomeOf(result) === null ? detail");
    expect(src).toContain("oneLine(head) && cardOutcomeOf(result) === null ? head");
  });

  it("in email: the receipt of a result that did not act", () => {
    const src = read("../components/email/EmailToolCards.tsx");
    const card = src.slice(src.indexOf("function NoActionCard("));
    expect(card.slice(0, card.indexOf("\n}\n"))).toContain("<Readout result={memberLineOf(result)} />");
  });

  it("in the step trail: a step's result and a sub-step's result", () => {
    const src = read("../components/ThinkingContainer.tsx");
    expect(src).toContain("capOutput(memberLineOf(String(event.result)), 2000)");
    expect(src).toContain("capOutput(memberLineOf(String(st.result)), 400)");
    expect(src).not.toContain("capOutput(String(st.result)");
  });
});
