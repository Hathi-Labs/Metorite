// "Clean older mail" offers only what the gateway does (WS-17, after the
// EM-T6a review). Every deep download stops at 180 days after EM-T6a, so a
// choice of years, or "Everything", was a false label. A null `since_date`
// would mean "back to the member's import range", so each choice names a date.
//
// vitest here runs in the node environment. The decisions run from
// `cleanOlderMail.ts`, and the component's wiring is held by a source scan
// with the comments stripped.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  CLEAN_OLDER_MAIL_CHOICES,
  IMPORT_CEILING_DAYS,
  cleanOlderMailSince,
  cleanOlderMailTitle,
} from "./cleanOlderMail";

const EMAIL_APP = join(__dirname, "..");

function read(rel: string): string {
  return readFileSync(join(EMAIL_APP, rel), { encoding: "utf-8" });
}

/** Source with `//` and block comments removed, so prose cannot pass a scan. */
function codeOnly(src: string): string {
  return src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:"'`])\/\/.*$/gm, "$1");
}

const DAY = 86_400_000;

/** Whole UTC days from the date `since` to the UTC date of `now`. */
function ageInDays(since: string, now: Date): number {
  const today = Date.parse(now.toISOString().slice(0, 10));
  return Math.round((today - Date.parse(since)) / DAY);
}

const NOWS = [
  new Date("2026-10-02T00:00:01Z"),
  new Date("2026-10-02T12:00:00Z"),
  new Date("2026-10-02T23:59:59Z"),
  new Date("2027-03-01T08:00:00Z"),
];

const FORBIDDEN = /\byears?\b|\bentire\b|\beverything\b|\bwhole mailbox\b|\ball (?:of )?your mail\b/i;

describe("Clean older mail stops at 6 months, the ceiling of EM-T6a", () => {
  it("the ceiling is 180 days, 6 months of 30 days", () => {
    expect(IMPORT_CEILING_DAYS).toBe(180);
  });

  it("every choice sends a date no older than 180 days", () => {
    expect(CLEAN_OLDER_MAIL_CHOICES.length).toBeGreaterThan(0);
    for (const now of NOWS) {
      for (const c of CLEAN_OLDER_MAIL_CHOICES) {
        const since = cleanOlderMailSince(c.days, now);
        expect(since, `${c.label} at ${now.toISOString()}`).toMatch(/^\d{4}-\d{2}-\d{2}$/);
        expect(ageInDays(since, now)).toBeLessThanOrEqual(IMPORT_CEILING_DAYS);
        expect(ageInDays(since, now)).toBe(c.days);
      }
    }
  });

  it("the longest choice is 6 months, and a value over the ceiling is held at it", () => {
    const last = CLEAN_OLDER_MAIL_CHOICES[CLEAN_OLDER_MAIL_CHOICES.length - 1];
    expect(last).toEqual({ label: "6 months", days: 180 });
    const now = NOWS[1];
    expect(cleanOlderMailSince(365, now)).toBe(cleanOlderMailSince(180, now));
    expect(cleanOlderMailSince(-5, now)).toBe(now.toISOString().slice(0, 10));
  });

  it("no label or tooltip mentions years or the entire mailbox", () => {
    for (const c of CLEAN_OLDER_MAIL_CHOICES) {
      expect(c.label, c.label).not.toMatch(FORBIDDEN);
      expect(cleanOlderMailTitle(c), c.label).not.toMatch(FORBIDDEN);
    }
    expect(CLEAN_OLDER_MAIL_CHOICES.map((c) => c.label)).toEqual(["1 month", "3 months", "6 months"]);
  });
});

describe("the request always names since_date", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("backfillAndClean sends the date it is given, never null", async () => {
    const bodies: unknown[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_url: string, init?: RequestInit) => {
        bodies.push(JSON.parse(String(init?.body)));
        return new Response(JSON.stringify({ scheduled: true }), { status: 200 });
      }),
    );
    const api = await import("./api");
    for (const c of CLEAN_OLDER_MAIL_CHOICES) {
      await api.backfillAndClean("acc-1", cleanOlderMailSince(c.days, NOWS[1]));
    }
    expect(bodies).toEqual(
      CLEAN_OLDER_MAIL_CHOICES.map((c) => ({ account_id: "acc-1", since_date: cleanOlderMailSince(c.days, NOWS[1]) })),
    );
  });

  it("the date is a required argument, and the api no longer sends null", () => {
    const api = codeOnly(read("lib/api.ts"));
    expect(api).toMatch(/export async function backfillAndClean\(\s*accountId: string,\s*sinceDate: string\s*\)/);
    expect(api).toMatch(/since_date: sinceDate,/);
    expect(api).not.toMatch(/since_date: sinceDate \?\? null/);
  });
});

describe("the Email Cleaner draws these choices", () => {
  const src = codeOnly(read("components/automation/BulkUnsubscribeView.tsx"));

  it("maps CLEAN_OLDER_MAIL_CHOICES and sends cleanOlderMailSince for each", () => {
    expect(src).toContain("{CLEAN_OLDER_MAIL_CHOICES.map((o) => (");
    expect(src).toContain("onClick={() => cleanOlderMail(o.days)}");
    expect(src).toContain("title={cleanOlderMailTitle(o)}");
    expect(src).toContain("await backfillAndClean(accountId, cleanOlderMailSince(days));");
  });

  it("keeps no year choice, no Everything and no entire-mailbox text", () => {
    expect(src).not.toMatch(/BACKFILL_OPTIONS|isoYearsAgo|\byears:/);
    expect(src).not.toContain('"Everything"');
    expect(src).not.toMatch(/entire mailbox/i);
  });
});
