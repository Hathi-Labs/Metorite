/**
 * The Reminders band as it draws (RX-3). The runner has no DOM, so the band
 * renders to a string and the test reads the markup: the Bell glyph and not
 * the Calendar's icon, the exact time, the linked object, Done and Snooze on
 * each row, a link to the chat card, and nothing at all with no row.
 * The column and the bell leave the same reminders out or sort them first.
 *
 * Mutations, run by hand (2026-11-02):
 *   - draw `Icon name="CalendarDays"` for the glyph: "wears the Bell" fails;
 *   - draw the band with no rows: "draws nothing with no reminder" fails;
 *   - drop `ReminderActs` from `BandItem`: "has Done and Snooze" fails;
 *   - take `isReminderKind` out of the column's `apart` set: "the column leaves the reminder cards out" fails;
 *   - take `FIRED_FIRST` out of `kindOrder`: "the bell lists a fired reminder first" fails.
 */
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { Feed } from "./FeedCard";
import RemindersBand from "./RemindersBand";
import type { FeedAnswer, FeedCard } from "./feed";
import { REMINDERS_SHOWN, groupNeeds, kindOrder, shownNeeds } from "./myDay";
import { localIso } from "./reminderBand";
import type { NeedsItem, NeedsKind } from "./needs";

const here = dirname(fileURLToPath(import.meta.url));
const src = (f: string) => readFileSync(join(here, f), "utf8");
const at = (h: number, m = 0) => new Date(2026, 10, 2, h, m, 0, 0);
const NOW = at(10);

const REMINDER_CARD: FeedCard = {
  signal_id: "reminders:r1",
  app: "calendar",
  kind: "reminder_due",
  folded: [],
  group: "now",
  score: 60,
  title: "Call the printer",
  why: null,
  at: localIso(at(9, 30)),
  href: "/chat?do=new-chat&reminder=r1",
  acts: [{ id: "open-reminder", label: "Open", way: "open", href: "/chat?do=new-chat&reminder=r1" }],
};

function feedOf(answer: Partial<FeedAnswer> | undefined): Pick<Feed, "answer" | "marked" | "hidden" | "errors" | "refresh"> {
  return {
    answer: answer
      ? ({
          cards: [],
          groups: [],
          handled: [],
          handled_ok: true,
          reminders_ahead: [],
          reminder_subjects: {},
          needs: null,
          sources: {},
          capped: [],
          fetchedAt: 0,
          ...answer,
        } as FeedAnswer)
      : undefined,
    marked: new Set(),
    hidden: new Set(),
    errors: {},
    refresh: () => {},
  };
}

const draw = (answer: Partial<FeedAnswer> | undefined, now: Date | null = NOW) =>
  renderToStaticMarkup(createElement(RemindersBand, { feed: feedOf(answer), now }));

describe("the band", () => {
  it("draws nothing with no reminder, no answer, or no clock", () => {
    expect(draw({})).toBe("");
    expect(draw(undefined)).toBe("");
    expect(draw({ cards: [REMINDER_CARD] }, null)).toBe("");
  });

  it("wears the Bell and not the Calendar's icon", () => {
    const html = draw({ cards: [REMINDER_CARD] });
    expect(html).toContain("lucide-bell");
    expect(html).not.toContain("lucide-calendar");
  });

  it("shows the exact time, the title and the linked object of a fired reminder", () => {
    const html = draw({ cards: [REMINDER_CARD], reminder_subjects: { r1: { kind: "task", label: "Send the invoice" } } });
    expect(html).toContain("Call the printer");
    expect(html).toMatch(/9:30/);
    expect(html).toContain("Task · Send the invoice");
    expect(html).toContain('data-reminder-row="fired"');
    expect(html).toContain("1 due");
  });

  it("has Done and Snooze on a fired row and on an upcoming row", () => {
    const html = draw({
      cards: [REMINDER_CARD],
      reminders_ahead: [{ id: "r2", title: "Stand-up notes", kind: "action", fire_at: localIso(at(17)), important: false }],
    });
    expect((html.match(/data-reminder-done=""/g) ?? []).length).toBe(2);
    expect((html.match(/data-reminder-snooze=""/g) ?? []).length).toBe(2);
    expect(html).toContain('data-reminder-row="upcoming"');
    expect(html).toContain("Later today");
    // On a phone each act is an icon, the due row's Done too (task 1898):
    // every visible word of an act hides below `sm`.
    expect(html.match(/<span[^>]*>(Done|Snooze)<\/span>/g)).toEqual([
      '<span class="hidden sm:inline">Done</span>',
      '<span class="hidden sm:inline">Snooze</span>',
      '<span class="hidden sm:inline">Done</span>',
      '<span class="hidden sm:inline">Snooze</span>',
    ]);
  });

  it("links a row to the chat card of the reminder", () => {
    const html = draw({ cards: [REMINDER_CARD] });
    expect(html).toContain('href="/chat?do=new-chat&amp;reminder=r1"');
  });

  it("says '1 due' only for rows that wait, so an upcoming-only band has no count", () => {
    const html = draw({
      reminders_ahead: [{ id: "r2", title: "Stand-up notes", kind: "action", fire_at: localIso(at(17)), important: false }],
    });
    expect(html).not.toContain("data-reminder-waiting");
  });
});

describe("no reminder shows twice", () => {
  it("the column leaves the reminder cards out of its groups", () => {
    const s = src("FeedCard.tsx");
    expect(s).toMatch(/isReminderKind\(c\.kind\)\)\.map\(\(c\) => c\.signal_id\)/);
    expect(s).toMatch(/hidden: apart \}/);
    expect(s).toMatch(/<RemindersBand feed=\{feed\} now=\{now\}/);
  });
});

function item(id: string, kind: NeedsKind): NeedsItem {
  return { id, app: kind.startsWith("reminder") ? "calendar" : "tasks", kind, title: id, detail: null, href: "/x", at: null, act: null, act_ref: null };
}

describe("the bell", () => {
  it("lists a fired reminder first, and an unconfirmed one last", () => {
    const order = kindOrder();
    expect(order.slice(0, 2)).toEqual(["reminder_due", "reminder_check"]);
    expect(order.at(-1)).toBe("reminder_unconfirmed");
    const groups = groupNeeds([item("t1", "overdue"), item("reminders:r", "reminder_due")]);
    expect(groups.map((g) => g.kind)).toEqual(["reminder_due", "overdue"]);
  });

  it("still lets a preset put its own kind in front", () => {
    expect(kindOrder("needs_reply")[0]).toBe("needs_reply");
    expect(kindOrder("needs_reply")[1]).toBe("reminder_due");
  });

  it("keeps the seven-row cut fair: three reminders at most, so other rows stay in view", () => {
    const items = [
      ...Array.from({ length: 6 }, (_, i) => item(`reminders:r${i}`, "reminder_due")),
      ...Array.from({ length: 6 }, (_, i) => item(`tasks:${i}`, "overdue")),
    ];
    const { groups, hidden } = shownNeeds(items, false);
    const shown = groups.flatMap((g) => g.items);
    expect(shown).toHaveLength(7);
    expect(shown.filter((i) => i.kind === "reminder_due")).toHaveLength(REMINDERS_SHOWN);
    expect(shown.filter((i) => i.kind === "overdue")).toHaveLength(4);
    expect(hidden).toBe(5);
    expect(shownNeeds(items, true).hidden).toBe(0);
  });
});
