// The weekly CRM digest on the Reports tab (WS-53 CRM-Q28, spec section
// 18.11.2 item 13). The runner has no DOM, so the card is rendered to a string.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import {
  DIGEST_AI_LINE,
  NO_VALUES,
  dayLabel,
  digestMissing,
  digestTableRows,
  digestValue,
  digestWeekLabel,
  newestDigest,
} from "../lib/reports";
import type { CrmDigest } from "../lib/types";
import { DigestCardView } from "./Reports";

const digest = (over: Partial<CrmDigest> = {}): CrmDigest => ({
  week_start: "2026-10-05",
  week_end: "2026-10-11",
  text: "Qualify holds 12 deals. The amount for November 2026 is INR 10,00,000.",
  created_at: "2026-10-12T06:10:00Z",
  stored_at: "2026-10-12T06:10:05Z",
  numbers: {
    week_start: "2026-10-05",
    week_end: "2026-10-11",
    zone: "Asia/Kolkata",
    parts: [
      {
        key: "open_deals_by_stage",
        title: "Open deals by stage",
        unit: "deals",
        rows: [
          { label: "Qualify", value: "12" },
          { label: "Proposal", value: "7" },
        ],
      },
      {
        key: "amount_by_close_month",
        title: "Amount of the deals by expected close month",
        unit: "INR",
        rows: [{ label: "November 2026", value: "INR 10,00,000" }],
        total: "INR 10,00,000",
      },
      { key: "new_leads", title: "New leads in the week", unit: "leads", rows: [], total: "4" },
      {
        key: "open_amount_by_stage",
        title: "Amount of the open deals by stage",
        unit: "INR",
        rows: [{ label: "Qualify", value: NO_VALUES }],
      },
    ],
    missing: ["New contacts in the week"],
  },
  ...over,
});

describe("the digest helpers", () => {
  it("names the week as dates, with no zone shift", () => {
    expect(dayLabel("2026-10-05")).toBe("5 Oct 2026");
    expect(dayLabel("2026-13-05")).toBe("—");
    expect(dayLabel(null)).toBe("—");
    expect(digestWeekLabel(digest())).toBe("Week of 5 Oct 2026 to 11 Oct 2026");
  });

  it("flattens the parts into rows and adds a total row", () => {
    const rows = digestTableRows(digest().numbers);
    expect(rows.map((r) => [r.label, r.value])).toEqual([
      ["Qualify", "12"],
      ["Proposal", "7"],
      ["November 2026", "INR 10,00,000"],
      ["Total", "INR 10,00,000"],
      ["Total", "4"],
      ["Qualify", NO_VALUES],
    ]);
  });

  it("computes no number: each value is the server's text", () => {
    const values = digestTableRows(digest().numbers).map((r) => r.value);
    const sent = digest().numbers.parts.flatMap((p) => [
      ...p.rows.map((r) => r.value),
      ...(p.total !== undefined ? [p.total] : []),
    ]);
    expect(values.sort()).toEqual(sent.sort());
    // 12 + 7 is no row of the table.
    expect(values).not.toContain("19");
  });

  it("draws each value verbatim, and an empty field never as 0", () => {
    expect(digestValue({ value: NO_VALUES })).toBe("no values recorded");
    expect(digestValue({ value: "INR 12,34,567" })).toBe("INR 12,34,567");
    expect(digestValue({ value: "12" })).toBe("12");
  });

  it("names the missing parts and takes the newest digest", () => {
    expect(digestMissing(digest().numbers)).toEqual(["New contacts in the week"]);
    expect(digestMissing({ parts: [], missing: [] })).toEqual([]);
    expect(newestDigest([])).toBeNull();
    expect(newestDigest([digest(), digest({ week_start: "2026-09-28" })])?.week_start).toBe(
      "2026-10-05",
    );
  });
});

describe("the digest card", () => {
  const html = renderToStaticMarkup(createElement(DigestCardView, { digest: digest() }));

  it("shows the week, the text and the line that AI wrote it", () => {
    expect(html).toContain("Weekly digest");
    expect(html).toContain("Week of 5 Oct 2026 to 11 Oct 2026");
    expect(html).toContain("Qualify holds 12 deals.");
    expect(html).toContain(DIGEST_AI_LINE);
    expect(DIGEST_AI_LINE).toMatch(/^AI wrote this text from the numbers/);
  });

  it("shows the numbers as a table", () => {
    expect(html).toContain("<table");
    expect(html).toContain("November 2026");
    expect(html).toContain("INR 10,00,000");
    expect(html).toContain("no values recorded");
  });

  it("names a part that did not answer", () => {
    expect(html).toContain("Not counted this week: New contacts in the week.");
  });

  it("writes no colour and no hand-rolled control", () => {
    expect(html).not.toMatch(/#[0-9a-f]{3,6}\b|text-white|bg-\[/i);
    expect(html).not.toContain("<button");
  });
});
