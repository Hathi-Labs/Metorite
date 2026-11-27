/**
 * My Day's Reminders band, its rules (RX-3, `calendar_events_reminders.md`
 * §5.6). The page renders to a string in this runner, so the rules are pure
 * and each one is an assertion.
 *
 * Mutations, run by hand (2026-11-02):
 *   - let `bandRows` keep a card of a task kind: "takes only reminder cards" fails;
 *   - sort `bandRows` by time alone: "fired rows come first" fails;
 *   - drop the `atMs > nowMs` filter: "drops an upcoming row whose time has passed" fails;
 *   - let `cutBand` cut by kind: "the cut runs over the whole band" fails;
 *   - make `undoMove` always return `row.at`: "sends a reminder that was waiting back" fails.
 */
import { describe, expect, it } from "vitest";

import { type FeedAnswer, type FeedCard, cardHidden } from "./feed";
import {
  BAND_SHOWN,
  COMEBACK_MS,
  bandCount,
  bandRows,
  cutBand,
  localIso,
  reminderHref,
  rowMeta,
  snoozeChoices,
  snoozeZone,
  snoozedWords,
  subjectLine,
  timeWords,
  undoMove,
} from "./reminderBand";
import { reminderIdOf } from "./reminderKinds";

// The runner's locale may write "am" or "AM", and may use a narrow space.
const flat = (s: string) => s.replace(/\s/g, " ").replace(/(\d) ([ap])m\b/gi, (_, d: string, c: string) => `${d} ${c.toUpperCase()}M`);
/** A local time on 2 November 2026, as the member's clock sees it. */
const at = (h: number, m = 0) => new Date(2026, 10, 2, h, m, 0, 0);
const iso = (h: number, m = 0) => localIso(at(h, m));
const NOW = at(10, 0).getTime();

function card(id: string, kind: string, when: Date | null, extra: Partial<FeedCard> = {}): FeedCard {
  return {
    signal_id: `reminders:${id}`,
    app: "calendar",
    kind,
    folded: [],
    group: "now",
    score: 50,
    title: `Reminder ${id}`,
    why: null,
    at: when ? localIso(when) : null,
    href: `/chat?do=new-chat&reminder=${id}`,
    acts: [],
    ...extra,
  };
}

const task = (): FeedCard => ({ ...card("t1", "overdue", at(8)), signal_id: "tasks:t1", app: "tasks", title: "A task" });

function answer(
  cards: FeedCard[],
  ahead: { id: string; when: Date; title?: string }[] = [],
  subjects: FeedAnswer["reminder_subjects"] = {},
): FeedAnswer {
  return {
    cards,
    groups: [],
    handled: [],
    handled_ok: true,
    reminders_ahead: ahead.map((a) => ({
      id: a.id,
      title: a.title ?? `Later ${a.id}`,
      kind: "action" as const,
      fire_at: localIso(a.when),
      important: false,
    })),
    reminder_subjects: subjects,
    needs: null,
    sources: {},
    capped: [],
    fetchedAt: 0,
  };
}

const none = new Set<string>();
const ids = (rows: { id: string }[]) => rows.map((r) => r.id);

describe("the rows of the band", () => {
  it("takes only reminder cards, never a task card", () => {
    const rows = bandRows(answer([task(), card("a", "reminder_due", at(9))]), none, none, NOW);
    expect(ids(rows)).toEqual(["a"]);
  });

  it("fired rows come first, then the check-backs, then the unconfirmed, each in time order", () => {
    const cards = [
      card("u", "reminder_unconfirmed", at(7)),
      card("c", "reminder_check", at(6)),
      card("d2", "reminder_due", at(9)),
      card("d1", "reminder_due", at(8)),
    ];
    expect(ids(bandRows(answer(cards), none, none, NOW))).toEqual(["d1", "d2", "c", "u"]);
  });

  it("puts today's upcoming rows after the fired ones, soonest first", () => {
    const rows = bandRows(
      answer([card("d", "reminder_due", at(9))], [
        { id: "b", when: at(17) },
        { id: "a", when: at(12) },
      ]),
      none,
      none,
      NOW,
    );
    expect(ids(rows)).toEqual(["d", "a", "b"]);
    expect(rows.map((r) => r.phase)).toEqual(["fired", "upcoming", "upcoming"]);
  });

  it("drops an upcoming row whose time has passed: the loop fires it and it returns as a fired row", () => {
    const rows = bandRows(answer([], [{ id: "gone", when: at(9, 59) }, { id: "ok", when: at(10, 1) }]), none, none, NOW);
    expect(ids(rows)).toEqual(["ok"]);
  });

  it("shows a reminder once, even when it is both a card and an ahead row", () => {
    const rows = bandRows(answer([card("x", "reminder_due", at(9))], [{ id: "x", when: at(11) }]), none, none, NOW);
    expect(ids(rows)).toEqual(["x"]);
  });

  it("drops a row the member acted on (the needs mark) or hid", () => {
    const a = answer([card("a", "reminder_due", at(9)), card("b", "reminder_due", at(9, 30))], [{ id: "c", when: at(12) }]);
    expect(ids(bandRows(a, new Set(["reminders:a", "reminders:c"]), none, NOW))).toEqual(["b"]);
    expect(ids(bandRows(a, none, new Set(["reminders:b"]), NOW))).toEqual(["a", "c"]);
  });

  it("is empty when there is no answer or no reminder, so the band hides", () => {
    expect(bandRows(undefined, none, none, NOW)).toEqual([]);
    expect(bandRows(answer([task()]), none, none, NOW)).toEqual([]);
  });

  it("names the linked object of a row, and the kind alone when the label is hidden", () => {
    const a = answer([card("a", "reminder_due", at(9))], [{ id: "b", when: at(12) }], {
      a: { kind: "task", label: "Send the invoice" },
      b: { kind: "email_thread", label: null },
    });
    const [fired, later] = bandRows(a, none, none, NOW);
    expect(fired.subject).toBe("Task · Send the invoice");
    expect(later.subject).toBe("Email");
    expect(subjectLine(undefined)).toBeNull();
  });

  it("links every row to the chat card of the reminder", () => {
    const [row] = bandRows(answer([card("a1", "reminder_due", at(9))]), none, none, NOW);
    expect(row.href).toBe(reminderHref("a1"));
    expect(row.href).toBe("/chat?do=new-chat&reminder=a1");
  });

  it("reads the reminder id off a feed id", () => {
    expect(reminderIdOf("reminders:abc")).toBe("abc");
    expect(reminderIdOf("tasks:abc")).toBeNull();
    expect(reminderIdOf("reminders:")).toBeNull();
  });
});

describe("the card leaves by its own mark", () => {
  it("hides a reminder card whose id is marked, though it has no tap act", () => {
    const c = card("a", "reminder_due", at(9));
    expect(cardHidden(c, new Set(["reminders:a"]))).toBe(true);
    expect(cardHidden(c, new Set(["reminders:other"]))).toBe(false);
    expect(cardHidden(c, new Set())).toBe(false);
  });
});

describe("the cut", () => {
  it("runs over the whole band, so fired rows are never pushed out by upcoming ones", () => {
    const fired = Array.from({ length: 6 }, (_, i) => card(`f${i}`, "reminder_due", at(8, i)));
    const rows = bandRows(answer(fired, [{ id: "late", when: at(15) }]), none, none, NOW);
    const { shown, hidden } = cutBand(rows, false);
    expect(shown).toHaveLength(BAND_SHOWN);
    expect(shown.every((r) => r.phase === "fired")).toBe(true);
    expect(hidden).toBe(2);
    expect(cutBand(rows, true).hidden).toBe(0);
  });

  it("counts the rows that wait on the member, not the upcoming ones", () => {
    const rows = bandRows(answer([card("a", "reminder_due", at(9))], [{ id: "b", when: at(12) }]), none, none, NOW);
    expect(bandCount(rows)).toBe(1);
  });
});

describe("the words of a row", () => {
  it("gives a fired row its exact time and how long it has waited", () => {
    const [row] = bandRows(answer([card("a", "reminder_due", at(9, 35))]), none, none, NOW);
    expect(flat(timeWords(row, NOW))).toBe("9:35 AM · 25m ago");
  });

  it("asks 'Did you do it?' on a check-back", () => {
    const [row] = bandRows(answer([card("a", "reminder_check", at(9))]), none, none, NOW);
    expect(flat(timeWords(row, NOW))).toBe("Did you do it? · set for 9:00 AM");
  });

  it("says 'Later today · 5:00 PM' on an upcoming row, then the linked object", () => {
    const a = answer([], [{ id: "b", when: at(17) }], { b: { kind: "task", label: "Send the invoice" } });
    const [row] = bandRows(a, none, none, NOW);
    expect(flat(timeWords(row, NOW))).toBe("Later today · 5:00 PM");
    expect(flat(rowMeta(row, NOW))).toBe("Later today · 5:00 PM · Task · Send the invoice");
  });
});

describe("snooze", () => {
  it("offers In 1 hour, This evening and Tomorrow morning", () => {
    const [h, e, t] = snoozeChoices(at(10));
    expect([h.label, e.label, t.label]).toEqual(["In 1 hour", "This evening", "Tomorrow morning"]);
    expect(h.at.getTime()).toBe(at(11).getTime());
    expect(e.at.getTime()).toBe(at(18).getTime());
    expect(t.at.getTime()).toBe(new Date(2026, 10, 3, 9, 0).getTime());
  });

  it("offers no evening when it is under 90 minutes away or past", () => {
    expect(snoozeChoices(at(17)).map((c) => c.id)).toEqual(["hour", "tomorrow"]);
    expect(snoozeChoices(at(21)).map((c) => c.id)).toEqual(["hour", "tomorrow"]);
  });

  // RX-7 item 1. Mutation: build the evening from `getHours` of the browser
  // again, and these three fail whenever the test zone is not Kolkata.
  it("reads 18:00 and 09:00 on the wall of the saved zone, not the browser's", () => {
    const now = new Date("2026-11-02T04:30:00Z"); // 10:00 in Kolkata
    const [, evening, tomorrow] = snoozeChoices(now, "Asia/Kolkata");
    expect(evening.iso).toBe("2026-11-02T18:00:00+05:30");
    expect(tomorrow.iso).toBe("2026-11-03T09:00:00+05:30");
    expect(evening.at.getTime()).toBe(Date.parse("2026-11-02T12:30:00Z"));
    expect(tomorrow.at.getTime()).toBe(Date.parse("2026-11-03T03:30:00Z"));
  });

  it("names the day in the saved zone, also east of UTC+12", () => {
    // 20:00 UTC on 2 Nov is 09:00 on 3 Nov in Auckland (UTC+13).
    const now = new Date("2026-11-02T20:00:00Z");
    const [hour, evening, tomorrow] = snoozeChoices(now, "Pacific/Auckland");
    expect(evening.iso).toBe("2026-11-03T18:00:00+13:00");
    expect(tomorrow.iso).toBe("2026-11-04T09:00:00+13:00");
    expect(hour.iso).toBe("2026-11-03T10:00:00+13:00");
  });

  it("drops the evening when it is past in the saved zone", () => {
    const now = new Date("2026-11-02T13:00:00Z"); // 18:30 in Kolkata
    expect(snoozeChoices(now, "Asia/Kolkata").map((c) => c.id)).toEqual(["hour", "tomorrow"]);
  });

  it("says the clock of the saved zone in the toast", () => {
    const now = new Date("2026-11-02T04:30:00Z");
    const [, evening, tomorrow] = snoozeChoices(now, "Asia/Kolkata");
    expect(snoozedWords(evening, now)).toMatch(/^Snoozed to 6:00\s?PM$/i);
    expect(snoozedWords(tomorrow, now)).toMatch(/^Snoozed to tomorrow 9:00\s?AM$/i);
  });

  it("takes the saved zone first, then the browser's, then UTC", () => {
    expect(snoozeZone("Asia/Kolkata", "America/New_York")).toBe("Asia/Kolkata");
    expect(snoozeZone(null, "America/New_York")).toBe("America/New_York");
    expect(snoozeZone("Not/AZone", "America/New_York")).toBe("America/New_York");
    expect(snoozeZone(null, "")).toBe("UTC");
  });

  it("writes an ISO time WITH the offset that parses back to the same instant", () => {
    const d = at(18);
    expect(localIso(d)).toMatch(/^2026-11-02T18:00:00[+-]\d{2}:\d{2}$/);
    expect(Date.parse(localIso(d))).toBe(d.getTime());
  });

  it("sends a reminder that was waiting back one minute from now", () => {
    const row = { phase: "fired" as const, at: iso(9), atMs: at(9).getTime() };
    expect(Date.parse(undoMove(row, NOW))).toBe(NOW + COMEBACK_MS);
  });

  it("sends an upcoming reminder back to the time it had", () => {
    const row = { phase: "upcoming" as const, at: iso(17), atMs: at(17).getTime() };
    expect(undoMove(row, NOW)).toBe(iso(17));
  });
});
