// CRM-X3 (crm_platform.md §19.7, done-when 9): the next-activity chip on a
// card, the day rule under it, and the lane sort choice.

import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { dueDay } from "./format";
import {
  CARD_SORT_PREFIX,
  cardSortKey,
  cardSortQuery,
  isOverdue,
  nextActivityChip,
  readCardSort,
  writeCardSort,
  type NextActivity,
} from "./nextActivity";

// The member sits in UTC+05:30. A task's due date is 00:00 UTC, so for the
// first 5.5 hours of a UTC day the instant is already "past" in India while
// the due day is today. That window is the bug the day rule exists to stop.
const PRIOR_TZ = process.env.TZ;
beforeAll(() => {
  process.env.TZ = "Asia/Kolkata";
});
afterAll(() => {
  if (PRIOR_TZ === undefined) delete process.env.TZ;
  else process.env.TZ = PRIOR_TZ;
});

/** 10:00 on 12 Oct 2026 in India, which is 04:30 UTC on the same day. */
const NOW = new Date("2026-10-12T04:30:00Z");

function task(at: string, subject = "Send the quote"): NextActivity {
  return { id: "a1", type: "task", subject, at, all_day: true };
}

function meeting(at: string, subject = "Demo"): NextActivity {
  return { id: "a2", type: "meeting", subject, at, all_day: false };
}

describe("the test runs in UTC+05:30", () => {
  it("reads the local date in India", () => {
    // 21:30 UTC on the 11th is 03:00 on the 12th in India.
    expect(new Date("2026-10-11T21:30:00Z").getDate()).toBe(12);
  });
});

describe("dueDay", () => {
  it("reads a task's due date as a UTC date, against the member's local today", () => {
    expect(dueDay("2026-10-12T00:00:00Z", true, NOW)).toBe(0);
    expect(dueDay("2026-10-11T00:00:00Z", true, NOW)).toBe(-1);
    expect(dueDay("2026-10-15T00:00:00Z", true, NOW)).toBe(3);
  });

  it("keeps today early in the Indian morning, before UTC has turned the day", () => {
    // 03:00 on the 12th in India is still the 11th in UTC.
    const early = new Date("2026-10-11T21:30:00Z");
    expect(dueDay("2026-10-12T00:00:00Z", true, early)).toBe(0);
    expect(dueDay("2026-10-11T00:00:00Z", true, early)).toBe(-1);
  });

  it("compares a call or a meeting by local dates", () => {
    // 20:00 UTC on the 12th is 01:30 on the 13th in India: tomorrow.
    expect(dueDay("2026-10-12T20:00:00Z", false, NOW)).toBe(1);
    expect(dueDay("2026-10-12T12:00:00Z", false, NOW)).toBe(0);
  });

  it("gives null for a missing or broken date", () => {
    expect(dueDay(null, true, NOW)).toBeNull();
    expect(dueDay(undefined, false, NOW)).toBeNull();
    expect(dueDay("not a date", true, NOW)).toBeNull();
  });
});

describe("nextActivityChip", () => {
  it("marks a task due before today as overdue, with the days late", () => {
    const chip = nextActivityChip(task("2026-10-09T00:00:00Z"), "open", NOW);
    expect(chip).not.toBeNull();
    expect(chip!.tone).toBe("danger");
    expect(chip!.label).toContain("Overdue");
    expect(chip!.label).toContain("3d");
    expect(isOverdue(task("2026-10-09T00:00:00Z"), "open", NOW)).toBe(true);
  });

  it("reads a task due today as Today, not Overdue, in UTC+05:30", () => {
    // 00:00 UTC is before NOW as an instant. The due DAY is today.
    const chip = nextActivityChip(task("2026-10-12T00:00:00Z"), "ongoing", NOW);
    expect(chip!.tone).toBe("accent");
    expect(chip!.label).toContain("Today");
    expect(chip!.label).toContain("Send the quote");
    expect(chip!.label).not.toContain("Overdue");
    expect(isOverdue(task("2026-10-12T00:00:00Z"), "ongoing", NOW)).toBe(false);
  });

  it("reads a meeting later today as Today", () => {
    const chip = nextActivityChip(meeting("2026-10-12T09:00:00Z"), "open", NOW);
    expect(chip!.tone).toBe("accent");
    expect(chip!.label).toContain("Today");
  });

  it("reads a later activity as the short date and the subject, muted", () => {
    const chip = nextActivityChip(task("2026-10-15T00:00:00Z", "Chase"), "open", NOW);
    expect(chip!.tone).toBe("muted");
    expect(chip!.label).toContain("15 Oct");
    expect(chip!.label).toContain("Chase");
  });

  it("shows the UTC day of a task, also where the local clock is behind it", () => {
    // 00:00 UTC on the 15th is the 15th in India too, and the label says so.
    const chip = nextActivityChip(task("2026-10-15T00:00:00Z"), "open", NOW);
    expect(chip!.label).not.toContain("14 Oct");
  });

  it("says No next step when the gateway found none", () => {
    const chip = nextActivityChip(null, "open", NOW);
    expect(chip!.tone).toBe("warning");
    expect(chip!.label).toBe("No next step");
  });

  it("draws nothing on a card in a won or lost lane", () => {
    expect(nextActivityChip(task("2026-10-01T00:00:00Z"), "won", NOW)).toBeNull();
    expect(nextActivityChip(null, "lost", NOW)).toBeNull();
    expect(isOverdue(task("2026-10-01T00:00:00Z"), "won", NOW)).toBe(false);
  });

  it("draws nothing when the field is absent (an older gateway)", () => {
    expect(nextActivityChip(undefined, "open", NOW)).toBeNull();
  });

  it("shortens a long subject so the card keeps one line", () => {
    const long = "Send the revised quote with the volume discount table";
    const chip = nextActivityChip(task("2026-10-12T00:00:00Z", long), "open", NOW);
    expect(chip!.label.length).toBeLessThan(40);
    expect(chip!.title).toContain(long);
  });
});

describe("the lane sort choice", () => {
  function memoryStore() {
    const data = new Map<string, string>();
    return {
      getItem: (k: string) => data.get(k) ?? null,
      setItem: (k: string, v: string) => void data.set(k, v),
      data,
    };
  }

  it("keys the choice like the empty stages choice, per member and org", () => {
    expect(cardSortKey("vj@x.in|org-1")).toBe(`${CARD_SORT_PREFIX}vj@x.in|org-1`);
    expect(cardSortKey(null)).toBeNull();
    expect(cardSortKey("no-scope")).toBeNull();
  });

  it("reads back what it wrote, and stage age when nothing is stored", () => {
    const store = memoryStore();
    expect(readCardSort("vj@x.in|org-1", store)).toBe("stage_age");
    writeCardSort("vj@x.in|org-1", "next_activity", store);
    expect(readCardSort("vj@x.in|org-1", store)).toBe("next_activity");
    store.data.set(`${CARD_SORT_PREFIX}vj@x.in|org-1`, "amount");
    expect(readCardSort("vj@x.in|org-1", store)).toBe("stage_age");
  });

  it("sends card_sort only when it is not the default", () => {
    expect(cardSortQuery("stage_age")).toEqual({});
    expect(cardSortQuery(undefined)).toEqual({});
    expect(cardSortQuery("next_activity")).toEqual({ card_sort: "next_activity" });
  });
});
