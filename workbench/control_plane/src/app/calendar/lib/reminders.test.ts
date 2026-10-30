import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { refusalWords, UNREACHABLE_WORDS } from "@/lib/shell/reminderOpen";

import { PANEL_ROWS, RemindersPanel, RemindersSheetBody } from "../components/RemindersPanel";
import {
  applyList,
  BUSY_WORDS,
  capGroups,
  checkForm,
  createReminder,
  defaultWhen,
  dropTime,
  EMPTY_LIST,
  fetchReminders,
  FIRST_READ_RETRY_MS,
  firstReadRetry,
  gridZone,
  moveBack,
  moveRefusal,
  pinNeedsCompact,
  pinSegments,
  monthChips,
  moveReminder,
  nudgeTime,
  PAST_FIELD_WORDS,
  PAST_MOVE_WORDS,
  pinTop,
  pinTone,
  readRow,
  reminderChatPath,
  remindersOnDay,
  splitByState,
  zonedToIso,
  type CalReminder,
} from "./reminders";

/** A source file beside this one, read as text. */
const read = (rel: string) => readFileSync(fileURLToPath(new URL(rel, import.meta.url)), "utf8");

const row = (id: string, state: string, fireAt: string, extra: Record<string, unknown> = {}) => ({
  id,
  title: `t-${id}`,
  state,
  kind: "action",
  fire_at: fireAt,
  tz: "Asia/Kolkata",
  ...extra,
});
const cal = (id: string, state: string, fireAt: string): CalReminder => readRow(row(id, state, fireAt))!;

const KOLKATA = "Asia/Kolkata";
const NY = "America/New_York";

describe("zonedToIso", () => {
  it("writes the offset of Asia/Kolkata", () => {
    const z = zonedToIso("2026-10-31", "09:00", KOLKATA)!;
    expect(z.iso).toBe("2026-10-31T09:00:00+05:30");
    expect(z.ms).toBe(Date.UTC(2026, 9, 31, 3, 30));
  });

  it("changes the offset of America/New_York across the autumn clock change", () => {
    expect(zonedToIso("2026-10-31", "12:00", NY)!.iso).toBe("2026-10-31T12:00:00-04:00");
    expect(zonedToIso("2026-11-02", "12:00", NY)!.iso).toBe("2026-11-02T12:00:00-05:00");
  });

  it("takes the first run of a time that happens twice", () => {
    // 2026-11-01 01:30 happens in EDT and again in EST.
    const z = zonedToIso("2026-11-01", "01:30", NY)!;
    expect(z.iso).toBe("2026-11-01T01:30:00-04:00");
    expect(zonedToIso("2026-11-01", "02:30", NY)!.iso).toBe("2026-11-01T02:30:00-05:00");
  });

  it("moves a time in the spring gap forward", () => {
    // 2026-03-08 02:30 never happens in New York.
    const z = zonedToIso("2026-03-08", "02:30", NY)!;
    expect(z.iso).toBe("2026-03-08T03:30:00-04:00");
    expect(zonedToIso("2026-03-08", "01:30", NY)!.iso).toBe("2026-03-08T01:30:00-05:00");
  });

  it("reads the wall of the given zone, not the wall of the browser", () => {
    const la = zonedToIso("2026-10-31", "09:00", "America/Los_Angeles")!;
    const akl = zonedToIso("2026-10-31", "09:00", "Pacific/Auckland")!;
    expect(la.iso.endsWith("-07:00")).toBe(true);
    expect(akl.iso.endsWith("+13:00")).toBe(true);
    expect(la.ms).not.toBe(akl.ms);
    // The text and the instant agree whatever zone this machine runs in.
    expect(Date.parse(la.iso)).toBe(la.ms);
    expect(Date.parse(akl.iso)).toBe(akl.ms);
  });

  it("refuses a bad date, a bad time and a bad zone", () => {
    expect(zonedToIso("2026-02-30", "09:00", KOLKATA)).toBeNull();
    expect(zonedToIso("2026-10-31", "25:00", KOLKATA)).toBeNull();
    expect(zonedToIso("2026-10-31", "09:00", "Not/AZone")).toBeNull();
    expect(zonedToIso("", "", KOLKATA)).toBeNull();
  });
});

describe("defaultWhen", () => {
  it("is the next half hour of today", () => {
    const now = Date.UTC(2026, 9, 30, 4, 10); // 09:40 in Kolkata
    expect(defaultWhen(now, KOLKATA)).toEqual({ date: "2026-10-30", time: "10:00" });
    expect(defaultWhen(Date.UTC(2026, 9, 30, 3, 59), KOLKATA)).toEqual({ date: "2026-10-30", time: "09:30" });
    // On the half hour itself, the next one is 30 minutes on.
    expect(defaultWhen(Date.UTC(2026, 9, 30, 4, 0), KOLKATA)).toEqual({ date: "2026-10-30", time: "10:00" });
  });

  it("rolls to the next day after 23:30", () => {
    const now = Date.UTC(2026, 9, 30, 18, 15); // 23:45 in Kolkata
    expect(defaultWhen(now, KOLKATA)).toEqual({ date: "2026-10-31", time: "00:00" });
  });
});

describe("checkForm", () => {
  const base = { title: "Call Asha", date: "2026-10-31", time: "09:00", zone: KOLKATA };
  const now = Date.UTC(2026, 9, 30, 4, 0);

  it("sends a time after now with the offset", () => {
    const c = checkForm({ ...base, nowMs: now });
    expect(c.when?.iso).toBe("2026-10-31T09:00:00+05:30");
    expect(c.titleError).toBeUndefined();
    expect(c.whenError).toBeUndefined();
  });

  it("refuses a time at or before now", () => {
    const at = zonedToIso("2026-10-31", "09:00", KOLKATA)!.ms;
    expect(checkForm({ ...base, nowMs: at }).whenError).toMatch(/after now/);
    expect(checkForm({ ...base, nowMs: at + 1 }).whenError).toMatch(/after now/);
    expect(checkForm({ ...base, nowMs: at - 1 }).whenError).toBeUndefined();
  });

  it("needs a title of at most 200 characters", () => {
    expect(checkForm({ ...base, title: "  ", nowMs: now }).titleError).toBeTruthy();
    expect(checkForm({ ...base, title: "x".repeat(201), nowMs: now }).titleError).toBeTruthy();
    expect(checkForm({ ...base, title: "x".repeat(200), nowMs: now }).titleError).toBeUndefined();
  });

  it("asks for no title on a move", () => {
    expect(checkForm({ ...base, title: "", needTitle: false, nowMs: now }).titleError).toBeUndefined();
  });

  it("asks for a date and a time", () => {
    expect(checkForm({ ...base, date: "", nowMs: now }).whenError).toMatch(/date and a time/);
  });
});

describe("the list", () => {
  it("splits by state, each in time order", () => {
    const rows = [
      cal("c", "scheduled", "2026-10-31T10:00:00+05:30"),
      cal("a", "scheduled", "2026-10-31T08:00:00+05:30"),
      cal("d", "due", "2026-10-30T09:00:00+05:30"),
      cal("u", "unconfirmed", "2026-10-29T09:00:00+05:30"),
    ];
    const g = splitByState(rows);
    expect(g.upcoming.map((r) => r.id)).toEqual(["a", "c"]);
    expect(g.waiting.map((r) => r.id)).toEqual(["d"]);
    expect(g.unconfirmed.map((r) => r.id)).toEqual(["u"]);
  });

  it("reads only open rows with a time", () => {
    expect(readRow(row("x", "done", "2026-10-31T10:00:00+05:30"))).toBeNull();
    expect(readRow(row("x", "scheduled", "not a time"))).toBeNull();
    expect(readRow(null)).toBeNull();
  });

  it("opens the chat of RM-4", () => {
    expect(reminderChatPath("abc")).toBe("/chat?do=new-chat&reminder=abc");
  });
});

describe("the pin", () => {
  const rows = [
    cal("a", "scheduled", "2026-10-31T09:30:00+05:30"),
    cal("b", "due", "2026-11-01T09:30:00+05:30"),
    cal("e", "scheduled", "2026-10-31T01:30:00+05:30"),
  ];

  it("sits at its wall minute, in the zone of the member", () => {
    const pins = remindersOnDay(rows, new Date(2026, 9, 31), KOLKATA);
    expect(pins.map((p) => [p.row.id, p.minutes])).toEqual([
      ["e", 90],
      ["a", 570],
    ]);
    expect(pinTop(570, 0, 46)).toBeCloseTo((570 / 60) * 46);
    // 01:30 on the 31st in Kolkata is the afternoon of the 30th in New York.
    expect(remindersOnDay(rows, new Date(2026, 9, 31), NY).map((p) => p.row.id)).toEqual(["a"]);
    expect(remindersOnDay(rows, new Date(2026, 9, 30), NY).map((p) => p.row.id)).toEqual(["e"]);
  });

  it("uses tokens only", () => {
    for (const s of ["scheduled", "due", "unconfirmed"] as const) {
      expect(pinTone(s)).toMatch(/^(border-dashed )?border-(primary|muted-foreground|foreground\/50)$/);
    }
  });

  it("reads the three states apart, and scheduled is not the faint border colour", () => {
    const tones = (["scheduled", "due", "unconfirmed"] as const).map(pinTone);
    expect(new Set(tones).size).toBe(3);
    expect(pinTone("scheduled")).not.toBe("border-border");
  });

  it("uses the browser zone for the grid, never the store default", () => {
    expect(gridZone("Asia/Kolkata")).toBe("Asia/Kolkata");
    expect(gridZone(undefined)).toBe("UTC");
    expect(gridZone("Not/AZone")).toBe("UTC");
  });

  it("draws compact when the label would sit on a block", () => {
    // A block 10:00-11:00 (600-660), 46 px per hour.
    const spans = [{ start: 600, end: 660 }];
    expect(pinNeedsCompact(620, spans, 46)).toBe(true); // 10:20 inside
    expect(pinNeedsCompact(600, spans, 46)).toBe(true); // at the start
    expect(pinNeedsCompact(595, spans, 46)).toBe(true); // 5 min above: label reaches it
    expect(pinNeedsCompact(659, spans, 46)).toBe(true);
    expect(pinNeedsCompact(560, spans, 46)).toBe(false); // well above
    expect(pinNeedsCompact(700, spans, 46)).toBe(false); // well below
    expect(pinNeedsCompact(620, [], 46)).toBe(false); // no block at all
  });

  it("caps the panel rows in the order it draws them, and counts the rest", () => {
    const many = [
      cal("a", "scheduled", "2026-10-31T09:00:00+05:30"),
      cal("b", "due", "2026-10-31T10:00:00+05:30"),
      cal("c", "unconfirmed", "2026-10-31T11:00:00+05:30"),
      cal("d", "scheduled", "2026-10-31T12:00:00+05:30"),
      cal("e", "scheduled", "2026-10-31T13:00:00+05:30"),
      cal("f", "scheduled", "2026-10-31T14:00:00+05:30"),
    ];
    const cap = capGroups(splitByState(many), 4);
    expect(cap.groups.waiting.map((r) => r.id)).toEqual(["b"]);
    expect(cap.groups.unconfirmed.map((r) => r.id)).toEqual(["c"]);
    expect(cap.groups.upcoming.map((r) => r.id)).toEqual(["a", "d"]);
    expect(cap.hidden).toBe(2);
    expect(capGroups(splitByState(many), null).hidden).toBe(0);
  });

  it("turns a drop into a time of the day", () => {
    expect(dropTime(new Date(2026, 9, 31), 9 * 60 + 15, KOLKATA)!.iso).toBe("2026-10-31T09:15:00+05:30");
    expect(dropTime(new Date(2026, 9, 31), 5000, KOLKATA)!.iso).toBe("2026-10-31T23:59:00+05:30");
  });

  it("moves 15 minutes with the keys, to the grid line", () => {
    const r = cal("n", "scheduled", "2026-10-31T09:07:00+05:30");
    expect(nudgeTime(r, 1, KOLKATA)!.iso).toBe("2026-10-31T09:15:00+05:30");
    expect(nudgeTime(r, -1, KOLKATA)!.iso).toBe("2026-10-31T09:00:00+05:30");
    const on = cal("o", "scheduled", "2026-10-31T09:15:00+05:30");
    expect(nudgeTime(on, 1, KOLKATA)!.iso).toBe("2026-10-31T09:30:00+05:30");
    expect(nudgeTime(on, -1, KOLKATA)!.iso).toBe("2026-10-31T09:00:00+05:30");
    const late = cal("l", "scheduled", "2026-10-31T23:50:00+05:30");
    expect(nudgeTime(late, 1, KOLKATA)!.iso).toBe("2026-11-01T00:00:00+05:30");
  });

  it("moves 15 minutes of real time in the repeated autumn hour (Europe/Berlin, 2026-10-25)", () => {
    const BERLIN = "Europe/Berlin";
    // 02:15 CET is the second run of 02:15: 01:15 UTC.
    const second = cal("s", "scheduled", "2026-10-25T02:15:00+01:00");
    expect(second.fireMs).toBe(Date.UTC(2026, 9, 25, 1, 15));
    const down = nudgeTime(second, 1, BERLIN)!;
    expect(down.ms).toBe(Date.UTC(2026, 9, 25, 1, 30));
    expect(down.iso).toBe("2026-10-25T02:30:00+01:00");
    const up = nudgeTime(second, -1, BERLIN)!;
    expect(up.ms).toBe(Date.UTC(2026, 9, 25, 1, 0));
    expect(up.iso).toBe("2026-10-25T02:00:00+01:00");
    // The first run: 02:45 CEST goes on to 02:00 CET, 15 minutes later.
    const first = cal("f", "scheduled", "2026-10-25T02:45:00+02:00");
    expect(nudgeTime(first, 1, BERLIN)!.iso).toBe("2026-10-25T02:00:00+01:00");
    // Each step through the night moves 15 minutes of real time, both ways.
    let r = cal("w", "scheduled", "2026-10-25T00:00:00+02:00");
    for (let i = 0; i < 20; i++) {
      const next = nudgeTime(r, 1, BERLIN)!;
      expect(next.ms - r.fireMs).toBe(15 * 60_000);
      expect(Date.parse(next.iso)).toBe(next.ms);
      expect(nudgeTime({ ...r, fireMs: next.ms, fireAt: next.iso }, -1, BERLIN)!.ms).toBe(r.fireMs);
      r = { ...r, fireMs: next.ms, fireAt: next.iso };
    }
  });

  it("steps over the spring gap in real time", () => {
    // Berlin skips 02:00 to 03:00 on 2026-03-29. 01:45 CET is 00:45 UTC.
    const before = cal("g", "scheduled", "2026-03-29T01:45:00+01:00");
    const next = nudgeTime(before, 1, "Europe/Berlin")!;
    expect(next.ms - before.fireMs).toBe(15 * 60_000);
    expect(next.iso).toBe("2026-03-29T03:00:00+02:00");
  });

  it("stops the line at the edge of a block, so it never strikes through a title", () => {
    // No block at the line: one part, the full width.
    expect(pinSegments(100, [])).toEqual([{ from: 0, to: 1 }]);
    // A block in the only lane at the line: no line at all.
    const one = { top: 90, height: 40, lane: 0, lanes: 1 };
    expect(pinSegments(100, [one])).toEqual([]);
    // The 2 px line that touches the top edge of the block stops too.
    expect(pinSegments(89, [one])).toEqual([]);
    expect(pinSegments(88, [one])).toEqual([{ from: 0, to: 1 }]);
    // A block above or below the line leaves it whole.
    expect(pinSegments(131, [one])).toEqual([{ from: 0, to: 1 }]);
    // Two lanes, a block in lane 1 only: the line runs in lane 0.
    expect(pinSegments(100, [{ top: 90, height: 40, lane: 1, lanes: 2 }])).toEqual([{ from: 0, to: 0.5 }]);
    // Three lanes, the middle one held: the line runs on both sides of it.
    expect(pinSegments(100, [{ top: 90, height: 40, lane: 1, lanes: 3 }])).toEqual([
      { from: 0, to: 1 / 3 },
      { from: 2 / 3, to: 1 },
    ]);
  });

  it("draws a month chip with a time and a title", () => {
    const chips = monthChips(rows, new Date(2026, 9, 31), KOLKATA);
    expect(chips).toHaveLength(2);
    expect(chips[1].label).toBe("t-a");
    expect(chips[1].time).toMatch(/9:30/);
  });
});

describe("a reminder takes no capacity and no lane", () => {
  /** The first argument of every call to `fn( ... )` in the source. */
  const firstArgs = (src: string, fn: string): string[] =>
    [...src.matchAll(new RegExp(String.raw`\b${fn}\(\s*([^,)]*)`, "g"))].map((m) => m[1].trim());

  it("the lib has no task-store import and no capacity type", () => {
    const src = read("./reminders.ts");
    expect(src).not.toMatch(/from "@\/app\/tasks\/lib\/(taskStore|scheduling)"/);
    expect(src).not.toMatch(/blocksForDay|layoutBlocks|leverageStats/);
  });

  it("only task items reach the block, lane and stat functions", () => {
    // Every call takes `items` (or the `blocks` of `items`) and nothing else.
    // A spread, a cast or a reminder list in the first argument fails here.
    for (const rel of ["../CalendarView.tsx", "../components/TimeGrid.tsx", "../components/MonthGrid.tsx"]) {
      const src = read(rel);
      for (const arg of firstArgs(src, "blocksForDay")) expect(arg, `${rel} blocksForDay`).toBe("items");
      for (const arg of firstArgs(src, "deadlinesForDay")) expect(arg, `${rel} deadlinesForDay`).toBe("items");
      for (const arg of firstArgs(src, "layoutBlocks")) expect(arg, `${rel} layoutBlocks`).toBe("blocks");
    }
    // The calls exist, so the check is not empty.
    expect(firstArgs(read("../components/TimeGrid.tsx"), "blocksForDay")).toEqual(["items"]);
    expect(firstArgs(read("../CalendarView.tsx"), "blocksForDay").length).toBeGreaterThan(2);
  });

  it("the stats read no reminder name", () => {
    const view = read("../CalendarView.tsx");
    const stats = view.slice(view.indexOf("const doneStats"), view.indexOf("const schedule ="));
    const lev = view.slice(view.indexOf("const leverageStats"), view.indexOf("const canReplan"));
    for (const part of [stats, lev]) {
      expect(part.length).toBeGreaterThan(50);
      expect(part).not.toMatch(/remList|remZone|reminders?/i);
    }
  });

  it("the drop of a pin never reaches the task reschedule", () => {
    const grid = read("../components/TimeGrid.tsx");
    const drop = grid.slice(grid.indexOf("const handleDrop"), grid.indexOf("const onBlockDragStart"));
    expect(drop).toMatch(/REMINDER_DRAG_TYPE/);
    expect(drop.indexOf("REMINDER_DRAG_TYPE")).toBeLessThan(drop.indexOf("reschedule("));
  });

  it("the grid uses the browser zone for every reminder, never the store setting", () => {
    const view = read("../CalendarView.tsx");
    expect(view).toMatch(/const remZone = gridZone\(browserTimeZone\(\)\)/);
    expect(view).not.toMatch(/(?:pickZone|gridZone|knownZone)\(settings\.timezone/);
  });

  it("the grid body is one stacking context under the sticky week header", () => {
    // A pin (z-20) once drew over "FRI 30" when the week grid scrolled. The
    // body's own stacking context keeps every pin, line and block under it.
    const grid = read("../components/TimeGrid.tsx");
    const header = grid.indexOf('className="sticky top-0 z-10 ');
    const body = grid.indexOf('className="relative z-0 flex"');
    expect(header).toBeGreaterThan(-1);
    expect(body).toBeGreaterThan(header);
  });
});

describe("a move", () => {
  const NOW = Date.UTC(2026, 9, 30, 6, 0);

  it("refuses a time at or before now, with the rule of the form", () => {
    expect(moveRefusal(NOW, NOW)).toBe(PAST_MOVE_WORDS);
    expect(moveRefusal(NOW - 60_000, NOW)).toBe(PAST_MOVE_WORDS);
    expect(moveRefusal(NOW + 60_000, NOW)).toBeNull();
    // The form refuses the same times under its field.
    const at = zonedToIso("2026-10-30", "11:30", KOLKATA)!.ms; // 06:00 UTC
    expect(at).toBe(NOW);
    expect(checkForm({ title: "x", date: "2026-10-30", time: "11:30", zone: KOLKATA, nowMs: NOW }).whenError).toBe(
      PAST_FIELD_WORDS,
    );
  });

  it("checks the time before it writes, for a drag, a key and the form alike", () => {
    const view = read("../CalendarView.tsx");
    const move = view.slice(view.indexOf("const moveReminderTo"), view.indexOf("const moveReminderBack"));
    expect(move.length).toBeGreaterThan(50);
    expect(move.indexOf("moveRefusal(")).toBeGreaterThan(-1);
    expect(move.indexOf("moveRefusal(")).toBeLessThan(move.indexOf("runReminder("));
    // The grid sends each drop and each key move through this one function.
    expect(view).toMatch(/onMoveReminder=\{\(id, to\) => void moveReminderTo\(id, to\)\}/);
  });

  it("offers an Undo only for a scheduled reminder whose old time is still ahead", () => {
    const ahead = cal("a", "scheduled", "2026-10-30T12:00:00+05:30"); // 06:30 UTC
    expect(moveBack(ahead, NOW, KOLKATA)).toEqual({ fireAt: ahead.fireAt, tz: "Asia/Kolkata", ms: ahead.fireMs });
    expect(moveBack(ahead, ahead.fireMs, KOLKATA)).toBeNull();
    expect(moveBack(cal("d", "due", "2026-10-30T12:00:00+05:30"), NOW, KOLKATA)).toBeNull();
    expect(moveBack(cal("u", "unconfirmed", "2026-10-30T12:00:00+05:30"), NOW, KOLKATA)).toBeNull();
    // The Undo checks the time again, because it may have passed.
    const view = read("../CalendarView.tsx");
    const back = view.slice(view.indexOf("const moveReminderBack"), view.indexOf("const resolveReminderAs"));
    expect(back.indexOf("moveRefusal(back.ms")).toBeGreaterThan(-1);
    expect(back.indexOf("moveRefusal(back.ms")).toBeLessThan(back.indexOf("runReminder("));
  });
});

describe("the first read", () => {
  it("tries again a few times, with a growing wait, and then stops", () => {
    expect(FIRST_READ_RETRY_MS.length).toBeGreaterThanOrEqual(3);
    for (let i = 1; i < FIRST_READ_RETRY_MS.length; i++) {
      expect(FIRST_READ_RETRY_MS[i]).toBeGreaterThan(FIRST_READ_RETRY_MS[i - 1]);
    }
    expect(firstReadRetry(false, 0)).toBe(FIRST_READ_RETRY_MS[0]);
    expect(firstReadRetry(false, FIRST_READ_RETRY_MS.length)).toBeNull();
  });

  it("stops once any read answered, and shows nothing while it tries", () => {
    expect(firstReadRetry(true, 0)).toBeNull();
    // A failed read keeps `loading`, which draws no pin, panel or error.
    expect(applyList(EMPTY_LIST, { kind: "error" })).toBe(EMPTY_LIST);
  });
});

describe("one client of /resolve and /reopen", () => {
  it("the Calendar closes and opens a reminder through runReminderAct, and the lib has no copy", () => {
    const lib = read("./reminders.ts");
    // A URL of either route, as `${BASE}/${id}/resolve` builds it. The doc
    // comment may name a route. Code may not build one.
    expect(lib).not.toMatch(/\}\/(?:resolve|reopen)\b/);
    expect(lib).not.toMatch(/\{\s*action\s*\}/);
    expect(lib).not.toMatch(/function knownZone/);
    const view = read("../CalendarView.tsx");
    expect(view).toMatch(/from "@\/lib\/shell\/reminderOpen"/);
    expect(view).toMatch(/runReminderAct\(id, action\)/);
    expect(view).toMatch(/runReminderAct\(id, "undo"\)/);
  });

  it("the Undo of Done and Drop waits for its answer and shows it, good or bad", () => {
    const view = read("../CalendarView.tsx");
    const undo = view.slice(view.indexOf("const reopenReminderAgain"), view.indexOf("const submitReminderForm"));
    expect(undo).toMatch(/await runReminder\(\(\) => runReminderAct\(id, "undo"\)\)/);
    expect(undo).toMatch(/"The reminder is open again"/);
    // The error words include the busy guard of `run`.
    expect(undo).toMatch(/showWords\("error", out\.words\)/);
    expect(BUSY_WORDS).toMatch(/Wait/);
    // No action of a toast drops the result of its write.
    expect(view).not.toMatch(/onClick: \(\) => void runReminder\(/);
  });

  it("a refused create or move says the shared words", async () => {
    const res = (status: number, body: unknown = {}) =>
      (async () => ({ ok: false, status, json: async () => body }) as Response) as unknown as typeof fetch;
    const at = { fireAt: "2026-10-31T09:00:00+05:30", tz: KOLKATA };
    expect(await moveReminder("r1", at, res(404))).toEqual({ ok: false, words: refusalWords(404, null) });
    const five = await moveReminder("r1", at, res(500));
    expect(five).toEqual({ ok: false, words: "Metorite could not save the reminder. Try again." });
    const down = await moveReminder("r1", at, (async () => {
      throw new Error("offline");
    }) as unknown as typeof fetch);
    expect(down).toEqual({ ok: false, words: UNREACHABLE_WORDS });
  });
});

describe("the panel", () => {
  const noop = () => {};
  const six = [
    cal("a", "scheduled", "2026-10-31T09:00:00+05:30"),
    cal("b", "due", "2026-10-31T10:00:00+05:30"),
    cal("c", "unconfirmed", "2026-10-31T11:00:00+05:30"),
    cal("d", "scheduled", "2026-10-31T12:00:00+05:30"),
    cal("e", "scheduled", "2026-10-31T13:00:00+05:30"),
    cal("f", "scheduled", "2026-10-31T14:00:00+05:30"),
  ];
  const props = { rows: six, total: 6, zone: KOLKATA, onNew: noop, onOpen: noop, onMove: noop, onResolve: noop };
  const doneButtons = (html: string) => html.match(/<button[^>]*aria-label="Done: [^"]*"[^>]*>/g) ?? [];

  it("puts the Show all toggle in a footer outside the scroll box", () => {
    const html = renderToStaticMarkup(createElement(RemindersPanel, props));
    const box = html.indexOf("overflow-y-auto");
    const footer = html.indexOf("shrink-0 border-t border-border");
    expect(box).toBeGreaterThan(-1);
    expect(footer).toBeGreaterThan(box);
    expect(html.indexOf("Show all (6)")).toBeGreaterThan(footer);
    expect(html).toContain('aria-expanded="false"');
    expect(doneButtons(html)).toHaveLength(PANEL_ROWS);
  });

  it("counts every reminder of a group in its header, also the ones the cap hides", () => {
    // Four upcoming rows, and the cap shows two of them.
    const html = renderToStaticMarkup(createElement(RemindersPanel, props));
    expect(html).toMatch(/Upcoming(?:<!-- -->)? · (?:<!-- -->)?4</);
    expect(doneButtons(html)).toHaveLength(PANEL_ROWS);
  });

  it("fills the column in month mode, with every row and no toggle", () => {
    const html = renderToStaticMarkup(createElement(RemindersPanel, { ...props, fill: true }));
    expect(doneButtons(html)).toHaveLength(6);
    expect(html).not.toContain("Show all");
    expect(html).not.toContain("max-h-[");
  });

  it("gives the phone sheet touch-size actions with room between them", () => {
    const html = renderToStaticMarkup(createElement(RemindersSheetBody, props));
    const buttons = doneButtons(html);
    expect(buttons).toHaveLength(6);
    // icon-sm is p-1.5 around a 14 px icon: 26 px, over the 24 px of §9.
    for (const b of buttons) expect(b).toMatch(/\bp-1\.5\b/);
    expect(html).toContain("flex shrink-0 items-center gap-2");
    // The desktop panel keeps its small actions.
    const desk = doneButtons(renderToStaticMarkup(createElement(RemindersPanel, props)));
    for (const b of desk) expect(b).not.toMatch(/\bp-1\.5\b/);
  });

  it("puts the whole title of a row in its tip", () => {
    const long = cal("l", "scheduled", "2026-10-31T09:00:00+05:30");
    long.title = "Ask the supplier in Shenzhen about the second batch of the controller boards";
    const html = renderToStaticMarkup(createElement(RemindersPanel, { ...props, rows: [long], total: 1 }));
    expect(html).toContain(`title="${long.title}, `);
  });
});

describe("the client", () => {
  const res = (status: number, body: unknown = {}) =>
    ({ ok: status >= 200 && status < 300, status, json: async () => body }) as Response;

  it("draws nothing on a 404, and on a 403", async () => {
    for (const status of [404, 403]) {
      const f = vi.fn(async () => res(status));
      const got = await fetchReminders(f as unknown as typeof fetch);
      expect(got).toEqual({ kind: "absent" });
      const list = applyList({ status: "ready", rows: [cal("a", "scheduled", "2026-10-31T09:00:00+05:30")], total: 1 }, got);
      expect(list).toEqual({ status: "absent", rows: [], total: 0 });
    }
  });

  it("keeps the last good rows on a 5xx or a network fault", async () => {
    const prev = { status: "ready" as const, rows: [cal("a", "scheduled", "2026-10-31T09:00:00+05:30")], total: 1 };
    const five = await fetchReminders((async () => res(503)) as unknown as typeof fetch);
    expect(five).toEqual({ kind: "error" });
    expect(applyList(prev, five)).toBe(prev);
    const down = await fetchReminders((async () => {
      throw new Error("offline");
    }) as unknown as typeof fetch);
    expect(applyList(prev, down)).toBe(prev);
    expect(applyList(EMPTY_LIST, down).status).toBe("loading");
  });

  it("reads rows and the total in one call with limit 500", async () => {
    const f = vi.fn(async (_url: string) =>
      res(200, { rows: [row("a", "due", "2026-10-31T09:00:00+05:30"), row("z", "done", "2026-10-31T09:00:00+05:30")], total: 700 }),
    );
    const got = await fetchReminders(f as unknown as typeof fetch);
    expect(f.mock.calls[0][0]).toBe("/api/projects/my/reminders?limit=500");
    expect(got).toMatchObject({ kind: "ok", total: 700 });
    expect(got.kind === "ok" && got.rows.map((r) => r.id)).toEqual(["a"]);
  });

  it("creates with an offset, a tz and no subject", async () => {
    const f = vi.fn(async (_url: string, _init: RequestInit) => res(201));
    const out = await createReminder(
      { title: "Call", kind: "action", fireAt: "2026-10-31T09:00:00+05:30", tz: KOLKATA },
      f as unknown as typeof fetch,
    );
    expect(out).toEqual({ ok: true });
    expect(f).toHaveBeenCalledTimes(1);
    expect(f.mock.calls[0][0]).toBe("/api/projects/my/reminders");
    const body = JSON.parse(String(f.mock.calls[0][1].body));
    expect(body).toEqual({ title: "Call", kind: "action", fire_at: "2026-10-31T09:00:00+05:30", tz: KOLKATA });
    expect(body.subject_kind).toBeUndefined();
  });

  it("moves through /reschedule with the time and the zone", async () => {
    const f = vi.fn(async (_url: string, _init: RequestInit) => res(200));
    await moveReminder("r1", { fireAt: "2026-10-31T09:15:00+05:30", tz: KOLKATA }, f as unknown as typeof fetch);
    expect(f.mock.calls[0][0]).toBe("/api/projects/my/reminders/r1/reschedule");
    expect(JSON.parse(String(f.mock.calls[0][1].body))).toEqual({ fire_at: "2026-10-31T09:15:00+05:30", tz: KOLKATA });
  });

  it("says why a closed reminder refuses", async () => {
    const f = async () => res(409, { detail: "This reminder is closed (done)." });
    const out = await moveReminder("r1", { fireAt: "x", tz: KOLKATA }, f as unknown as typeof fetch);
    expect(out).toEqual({ ok: false, words: "This reminder is closed (done)." });
  });
});
