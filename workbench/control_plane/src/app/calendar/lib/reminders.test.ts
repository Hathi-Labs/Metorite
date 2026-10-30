import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it, vi } from "vitest";

import {
  applyList,
  checkForm,
  createReminder,
  defaultWhen,
  dropTime,
  EMPTY_LIST,
  fetchReminders,
  monthChips,
  moveReminder,
  nudgeTime,
  pinTop,
  pinTone,
  readRow,
  reminderChatPath,
  remindersOnDay,
  splitByState,
  zonedToIso,
  type CalReminder,
} from "./reminders";

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
      expect(pinTone(s)).toMatch(/^border-(primary|muted-foreground|border)$/);
    }
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

  it("draws a month chip with a time and a title", () => {
    const chips = monthChips(rows, new Date(2026, 9, 31), KOLKATA);
    expect(chips).toHaveLength(2);
    expect(chips[1].label).toBe("t-a");
    expect(chips[1].time).toMatch(/9:30/);
  });
});

describe("a reminder takes no capacity and no lane", () => {
  const read = (rel: string) => readFileSync(fileURLToPath(new URL(rel, import.meta.url)), "utf8");

  it("the lib has no task-store import and no capacity type", () => {
    const src = read("./reminders.ts");
    expect(src).not.toMatch(/from "@\/app\/tasks\/lib\/(taskStore|scheduling)"/);
    expect(src).not.toMatch(/blocksForDay|layoutBlocks|leverageStats/);
  });

  it("the grid feeds a reminder to no block, lane or stat", () => {
    const grid = read("../components/TimeGrid.tsx");
    // A reminder pin reads its own list. It never joins the block layout.
    expect(grid).not.toMatch(/blocksForDay\([^)]*reminder/i);
    expect(grid).not.toMatch(/layoutBlocks\([^)]*reminder/i);
    const view = read("../CalendarView.tsx");
    expect(view).not.toMatch(/blocksForDay\([^)]*reminder/i);
    // leverageStats depends on the task items only.
    const stats = view.slice(view.indexOf("const leverageStats"), view.indexOf("const canReplan"));
    expect(stats).not.toMatch(/reminder/i);
  });

  it("the drop of a pin never reaches the task reschedule", () => {
    const grid = read("../components/TimeGrid.tsx");
    const drop = grid.slice(grid.indexOf("const handleDrop"), grid.indexOf("const onBlockDragStart"));
    expect(drop).toMatch(/REMINDER_DRAG_TYPE/);
    expect(drop.indexOf("REMINDER_DRAG_TYPE")).toBeLessThan(drop.indexOf("reschedule("));
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
