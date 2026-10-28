// CRM-Z10a: the words of the cutover preview, and the card that draws them.
// The runner has no DOM, so the card is rendered to a string.
import { readFileSync } from "node:fs";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { CutoverCardView } from "../components/CutoverCard";
import {
  SWITCH_LATER,
  ageWords,
  attention,
  isPreview,
  staysShort,
  leadSentence,
  movingTotal,
  type CutoverPreview,
} from "./cutover";

function preview(over: Partial<CutoverPreview> = {}): CutoverPreview {
  return {
    mode: "mirror",
    sync: { last_good_at: "2026-10-28T01:00:00Z", age_seconds: 7200, running: false },
    records: [
      {
        key: "deals", label: "Projects", zoho: 550, zoho_age_seconds: 7200,
        metorite: { from_zoho: 547, native: 3 }, parts: [], state: "complete", failed: 0, refused: 0,
      },
      {
        key: "module:Products", label: "Products", zoho: null, zoho_age_seconds: null,
        metorite: { from_zoho: 10, native: 0 }, parts: [], state: "held", failed: 0, refused: 0,
      },
    ],
    other: [{ key: "views", label: "Saved views", metorite: { from_zoho: 0, native: 2 } }],
    unmatched_owners: [{ name: "Asha Rao", records: 12 }],
    unmatched_owner_names: 1,
    labels_kept: [{ key: "deals", label: "Projects", singular: "Project" }],
    not_complete: ["Products"],
    stays_in_zoho: ["Attachments"],
    ...over,
  };
}

describe("CRM-Z10a cutover words", () => {
  it("leads with what moves and what stays", () => {
    expect(movingTotal(preview())).toBe(557);
    expect(leadSentence(preview())).toBe(
      "557 records from Zoho are in Metorite. Not complete: Products. Attachments stay in Zoho.",
    );
  });

  it("says so when no sync has finished", () => {
    const p = preview({ sync: { last_good_at: null, age_seconds: null, running: false } });
    expect(leadSentence(p)).toMatch(/Finish a sync first/);
  });

  it("names a module that is not complete, an open run and an owner", () => {
    const lines = attention(preview({ sync: { last_good_at: "x", age_seconds: 1, running: true } }));
    expect(lines.some((l) => l.startsWith("A sync is running"))).toBe(true);
    expect(lines.some((l) => l.startsWith("Products: Held back"))).toBe(true);
    expect(lines.some((l) => l.includes("1 Zoho owner with no Metorite member"))).toBe(true);
  });

  it("shows at most 3 names that stay in Zoho", () => {
    expect(staysShort(["A", "B", "C", "D", "E"])).toBe("A, B, C and 2 more");
    expect(staysShort(["A", "B"])).toBe("A, B");
  });

  it("draws nothing for a body that is not a preview", () => {
    expect(isPreview({})).toBe(false);
    expect(isPreview({ rows: [], total: 0 })).toBe(false);
    expect(isPreview(null)).toBe(false);
    const { sync, ...noSync } = preview();
    void sync;
    expect(isPreview(noSync)).toBe(false);
    expect(isPreview(preview())).toBe(true);
  });

  it("writes the age in plain words", () => {
    expect(ageWords(null)).toBe("never");
    expect(ageWords(90)).toBe("1 minute ago");
    expect(ageWords(7200)).toBe("2 hours ago");
    expect(ageWords(5 * 86400)).toBe("5 days ago");
  });
});

describe("CRM-Z10a cutover card", () => {
  const html = renderToStaticMarkup(createElement(CutoverCardView, { preview: preview() }));

  it("has the fold, the owner list, the names and the last line", () => {
    expect(html).toContain("Show the counts");
    expect(html).toContain("Asha Rao");
    expect(html).toContain("Projects");
    expect(html).toContain(SWITCH_LATER);
  });

  it("has no switch button and no raw control", () => {
    expect(html).not.toMatch(/Switch now|Make Metorite your CRM/i);
    const source = readFileSync(new URL("../components/CutoverCard.tsx", import.meta.url), "utf8");
    expect(source).not.toMatch(/<(button|input|select|textarea)\b/);
  });

  it("uses no developer word", () => {
    expect(html).not.toMatch(/ext_source|entity|cursor/i);
  });
});
