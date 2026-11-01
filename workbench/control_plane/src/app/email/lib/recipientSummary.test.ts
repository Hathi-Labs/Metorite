import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import {
  NAMES_PER_GROUP,
  ROLLUP_AFTER,
  addressSet,
  recipientGroups,
  rollsUp,
  shortName,
  summarize,
  summaryText,
} from "./recipientSummary";

/**
 * The recipients roll up to one line (owner, 2026-11-01). The Cc stays in
 * the line by name (owner report, 2026-10-24), the member reads "you", and
 * the full list is one press away.
 */

const p = (name: string, email: string) => ({ name, email });
// The owner's screenshot: a PO mail with three To and four Cc.
const TO = [p("Fracktal Works", "support@fracktal.in"), p("Bharat Mishra", "bm@fracktal.in"), p("Vijay Raghav Varada", "vjvarada@fracktal.in")];
const CC = [
  p("Girish R (TGSIN - Purchase)", "girish.r@tgsin.in"),
  p("Ramesh (TGSIN - Purchase)", "ramesh@tgsin.in"),
  p("Vishwanath V Kolli (TGSIN - Automation)", "vkvishwanath@tgsin.in"),
  p("Prathap Reddy S (TGSIN - Maintenance)", "prathap.rs@tgsin.in"),
];
const MINE = addressSet(["VJVarada@fracktal.in"]);

describe("the recipients roll up (owner, 2026-11-01)", () => {
  it("rolls up past two recipients, and shows a short list whole", () => {
    expect(ROLLUP_AFTER).toBe(2);
    expect(rollsUp(recipientGroups(TO, CC))).toBe(true);
    expect(rollsUp(recipientGroups([TO[0]], [CC[0]]))).toBe(false);
    expect(rollsUp(recipientGroups([TO[0]]))).toBe(false);
  });

  it("keeps the groups that hold someone, To then Cc then Bcc", () => {
    expect(recipientGroups(TO, [], [TO[0]]).map((g) => g.label)).toEqual(["To", "Bcc"]);
    expect(recipientGroups(undefined, CC).map((g) => g.label)).toEqual(["Cc"]);
  });

  it("names the member 'you', first in its group, without case", () => {
    const [to, cc] = summarize(recipientGroups(TO, CC), MINE);
    expect(to.shown.map((n) => n.text)).toEqual(["you", "Fracktal Works"]);
    expect(to.more).toBe(1);
    // The Cc stays in the line by name: one cut line once hid it. The team
    // suffix in brackets goes from the line, and stays in the details.
    expect(cc.shown.map((n) => n.text)).toEqual(["Girish R", "Ramesh"]);
    expect(cc.more).toBe(CC.length - NAMES_PER_GROUP);
  });

  it("reads the address when there is no name, or the name is the address", () => {
    expect(shortName(p("", "x@y.in"), MINE)).toBe("x@y.in");
    expect(shortName(p("X@y.in", "x@y.in"), MINE)).toBe("x@y.in");
    // A name that is only a bracket keeps it.
    expect(shortName(p("(Accounts)", "a@y.in"), MINE)).toBe("(Accounts)");
  });

  it("says the whole line in words, with the counts", () => {
    expect(summaryText(summarize(recipientGroups(TO, CC), MINE))).toBe(
      "to you, Fracktal Works and 1 more; cc Girish R, Ramesh and 2 more",
    );
  });

  it("both mail views draw the rolled-up line, and the full list stays one press away", () => {
    const src = (f: string) => readFileSync(join(__dirname, "..", "components", f), "utf8");
    expect(src("EmailDetail.tsx")).toContain("<RecipientSummary");
    expect(src("ConversationView.tsx")).toContain("<RecipientSummary");
    const card = src("ContactCard.tsx");
    expect(card).toContain("export function RecipientSummary(");
    expect(card).toMatch(/aria-expanded=\{open\}/);
    // Open, it is the full list: one person a line, name and address.
    expect(card).toMatch(/open \? \(/);
    expect(card).toContain('data-recipient-summary="open"');
    // Folded, each group is its own segment, so its label and "+N" always show.
    expect(card).toContain("data-recipient-group={g.label}");
  });
});
