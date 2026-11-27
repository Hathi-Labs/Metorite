/**
 * WS-66 RX-2 — the receipt of a reminder write, and the clock words of its card.
 *
 * Mutations caught: the parser reads a receipt of another tool; a receipt with
 * no `reminder_id` or no `fire_at` still draws a card; Undo of a new reminder
 * stops dropping; a move goes back to a time that has passed; an update with
 * no restorable field offers Undo; the exact time says the wrong day; the
 * relative time says "in 120 minutes"; a preset lands in the past; the day
 * name leaves the saved zone; a saved zone name stays on a time with another
 * offset; a repeat of a reminder offers an Undo.
 */
import { describe, expect, it } from "vitest";

import {
  ALREADY_HEAD,
  canUndo,
  calendarReminderHref,
  readReminderReceipt,
  receiptZoneName,
  runUndo,
  undoRequest,
} from "./reminderReceipt";
import { exactWhen, laterPresets, relativeWhen } from "./reminderClock";

const RID = "e1f8fad5-d9cb-469f-a165-70867728950e";
const TASK = "1f8fad5b-d9cb-469f-a165-70867728950e";

const set = [
  "Reminder set: «Get a haircut» at Fri 31 Oct, 17:00 IST.",
  "  state scheduled · kind action · private, yours. A reminder, not a task.",
  `  reminder_id: ${RID}`,
  "  fire_at: 2099-10-31T11:30:00+00:00",
  "  zone: Asia/Kolkata",
  `  task_id: ${TASK}`,
  "  undo: drop",
  "«for the model» Tell the member this time and zone exactly as written.",
].join("\n");

const moved = [
  "Moved: «Get a haircut» at Mon 2 Nov, 10:00 IST.",
  "  state scheduled · kind action · private, yours. A reminder, not a task.",
  `  reminder_id: ${RID}`,
  "  fire_at: 2099-11-02T04:30:00+00:00",
  "  zone: Asia/Kolkata",
  "  undo: reschedule",
  "  was_fire_at: 2099-10-30T11:30:00+00:00",
  "  was_zone: Asia/Kolkata",
].join("\n");

describe("readReminderReceipt", () => {
  it("reads the title, the id, the time, the zone, the task and the Undo", () => {
    const r = readReminderReceipt("set_reminder", set)!;
    expect(r.id).toBe(RID);
    expect(r.title).toBe("Get a haircut");
    expect(r.fireAt).toBe("2099-10-31T11:30:00+00:00");
    expect(r.fireMs).toBe(Date.parse("2099-10-31T11:30:00Z"));
    expect(r.zone).toBe("Asia/Kolkata");
    expect(r.zoneName).toBe("IST");
    expect(r.taskId).toBe(TASK);
    expect(r.undo).toBe("drop");
  });

  it("reads only the three tools that draw a card", () => {
    expect(readReminderReceipt("resolve_reminder", set)).toBeNull();
    expect(readReminderReceipt("create_task", set)).toBeNull();
    expect(readReminderReceipt("update_reminder", set)).not.toBeNull();
    expect(readReminderReceipt("reschedule_reminder", moved)).not.toBeNull();
  });

  it("draws no card for a refusal, a missing id or a missing time", () => {
    expect(readReminderReceipt("set_reminder", "A reminder needs a title.")).toBeNull();
    expect(readReminderReceipt("set_reminder", set.replace(/reminder_id:.*/, "reminder_id: nope"))).toBeNull();
    expect(readReminderReceipt("set_reminder", set.replace(/fire_at:.*/, ""))).toBeNull();
  });

  it("a linked task needs a real id, and a receipt without one has none", () => {
    expect(readReminderReceipt("set_reminder", set.replace(TASK, "x"))!.taskId).toBeNull();
    expect(readReminderReceipt("reschedule_reminder", moved)!.taskId).toBeNull();
  });
});

describe("a repeat of an open reminder", () => {
  const repeat = set.replace("Reminder set:", `${ALREADY_HEAD}:`).replace("  undo: drop\n", "");

  it("says that no write happened, and offers no Undo", () => {
    const r = readReminderReceipt("set_reminder", repeat)!;
    expect(r.already).toBe(true);
    expect(r.title).toBe("Get a haircut");
    expect(r.undo).toBeNull();
    expect(canUndo(r)).toBe(false);
  });

  it("a new reminder is not a repeat", () => {
    expect(readReminderReceipt("set_reminder", set)!.already).toBe(false);
  });
});

describe("the time of a receipt around midnight and across zones", () => {
  const IST = "Asia/Kolkata";
  const NY = "America/New_York";
  /** 23:50 on 30 Oct in Kolkata, and 14:20 on 30 Oct in New York. */
  const NOW = Date.parse("2099-10-30T18:20:00Z");

  it("names the day on the wall of the zone it shows", () => {
    expect(exactWhen(Date.parse("2099-10-30T18:25:00Z"), IST, NOW, "IST")).toMatch(/^Today, 11:55\s?PM IST$/i);
    expect(exactWhen(Date.parse("2099-10-30T19:00:00Z"), IST, NOW, "IST")).toMatch(/^Tomorrow, 12:30\s?AM IST$/i);
    expect(exactWhen(Date.parse("2099-10-30T18:00:00Z"), IST, NOW, "IST")).toMatch(/^Today, 11:30\s?PM IST$/i);
    // Ten minutes after midnight: the time of yesterday evening.
    const after = Date.parse("2099-10-30T18:40:00Z");
    expect(exactWhen(Date.parse("2099-10-30T18:25:00Z"), IST, after, "IST")).toMatch(/^Yesterday, 11:55\s?PM IST$/i);
  });

  it("the saved zone decides the day, not the zone of the browser", () => {
    const soon = Date.parse("2099-10-30T19:00:00Z"); // 00:30 in Kolkata, 15:00 in New York
    expect(exactWhen(soon, IST, NOW, "IST")).toMatch(/^Tomorrow/);
    expect(exactWhen(soon, NY, NOW)).toMatch(/^Today/);
  });

  it("the relative time reads instants, so no zone can change it", () => {
    const soon = Date.parse("2099-10-30T19:00:00Z");
    expect(relativeWhen(soon, NOW)).toBe("in 40 minutes");
    expect(relativeWhen(Date.parse("2099-10-30T18:00:00Z"), NOW)).toBe("20 minutes ago");
  });

  it("a saved zone name holds only for a time with the same offset", () => {
    const winter = set
      .replace("Fri 31 Oct, 17:00 IST", "Tue 1 Dec, 09:00 GMT")
      .replace("2099-10-31T11:30:00+00:00", "2099-12-01T09:00:00+00:00")
      .replace("zone: Asia/Kolkata", "zone: Europe/London");
    const r = readReminderReceipt("set_reminder", winter)!;
    expect(r.zoneName).toBe("GMT");
    expect(receiptZoneName(r, r.fireMs)).toBe("GMT");
    expect(receiptZoneName(r, Date.parse("2099-12-02T09:00:00Z"))).toBe("GMT");
    expect(receiptZoneName(r, Date.parse("2099-07-01T09:00:00Z"))).toBeNull();
  });

  it("a receipt with no saved zone names none", () => {
    const none = set.replace("  zone: Asia/Kolkata\n", "");
    expect(receiptZoneName(readReminderReceipt("set_reminder", none)!, Date.parse("2099-10-31T11:30:00Z"))).toBeNull();
  });
});

describe("Undo", () => {
  const NOW = Date.parse("2099-10-01T00:00:00Z");

  it("a new reminder is dropped, a move goes back, and a past time cannot", () => {
    const made = readReminderReceipt("set_reminder", set)!;
    expect(canUndo(made, NOW)).toBe(true);
    expect(undoRequest(made, NOW)).toBeNull(); // drop goes through runReminderAct

    const mv = readReminderReceipt("reschedule_reminder", moved)!;
    expect(canUndo(mv, NOW)).toBe(true);
    expect(undoRequest(mv, NOW)).toEqual({
      method: "POST",
      url: `/api/projects/my/reminders/${RID}/reschedule`,
      body: { fire_at: "2099-10-30T11:30:00+00:00", tz: "Asia/Kolkata" },
    });
    // The old time is past: the loop would fire it at once, so no Undo.
    expect(canUndo(mv, Date.parse("2099-12-01T00:00:00Z"))).toBe(false);
    expect(undoRequest(mv, Date.parse("2099-12-01T00:00:00Z"))).toBeNull();
  });

  it("an update restores its words and its time with one PATCH, and shows no Undo when it cannot", () => {
    const text = moved
      .replace("Moved", "Reminder changed")
      .replace("undo: reschedule", "undo: patch")
      .concat("\n  was_title: Get a short haircut");
    const r = readReminderReceipt("update_reminder", text)!;
    expect(undoRequest(r, NOW)).toEqual({
      method: "PATCH",
      url: `/api/projects/my/reminders/${RID}`,
      body: { title: "Get a short haircut", fire_at: "2099-10-30T11:30:00+00:00", tz: "Asia/Kolkata" },
    });
    const none = readReminderReceipt("update_reminder", text.replace("  undo: patch\n", ""))!;
    expect(canUndo(none, NOW)).toBe(false);
  });

  it("runUndo drops a new reminder through /resolve, and moves a move back through /reschedule", async () => {
    const calls: Array<{ url: string; method?: string; body: unknown }> = [];
    const row = { id: RID, title: "x", kind: "action", state: "dropped", resolution: "dropped", fire_at: null, tz: null };
    const impl = (async (url: RequestInfo | URL, init?: RequestInit) => {
      calls.push({ url: String(url), method: init?.method, body: JSON.parse(String(init?.body)) });
      return new Response(JSON.stringify(row), { status: 200 });
    }) as typeof fetch;
    expect((await runUndo(readReminderReceipt("set_reminder", set)!, impl, NOW)).ok).toBe(true);
    expect(calls[0]).toEqual({ url: `/api/projects/my/reminders/${RID}/resolve`, method: "POST", body: { action: "drop" } });
    expect((await runUndo(readReminderReceipt("reschedule_reminder", moved)!, impl, NOW)).ok).toBe(true);
    expect(calls[1].url).toBe(`/api/projects/my/reminders/${RID}/reschedule`);
  });

  it("a refused Undo says why in words, never a status", async () => {
    const impl = (async () => new Response(JSON.stringify({ detail: "This reminder is closed (done)." }), { status: 409 })) as typeof fetch;
    const got = await runUndo(readReminderReceipt("reschedule_reminder", moved)!, impl, NOW);
    expect(got).toEqual({ ok: false, words: "This reminder is closed (done)." });
    const down = (async () => {
      throw new Error("offline");
    }) as typeof fetch;
    const off = await runUndo(readReminderReceipt("reschedule_reminder", moved)!, down, NOW);
    expect(off.ok).toBe(false);
  });

  it("See in Calendar links the id", () => {
    expect(calendarReminderHref(RID)).toBe(`/calendar?reminder=${RID}`);
  });
});

describe("the words of a time", () => {
  const zone = "Asia/Kolkata";
  // 2026-11-02 10:00 IST
  const NOW = Date.parse("2026-11-02T04:30:00Z");

  it("the exact time says today, tomorrow or the date, in the zone", () => {
    expect(exactWhen(Date.parse("2026-11-02T11:30:00Z"), zone, NOW)).toMatch(/^Today, 5:00\s[Pp][Mm]/);
    expect(exactWhen(Date.parse("2026-11-03T03:30:00Z"), zone, NOW)).toMatch(/^Tomorrow, 9:00\s[Aa][Mm]/);
    expect(exactWhen(Date.parse("2026-11-07T03:30:00Z"), zone, NOW)).toMatch(/^Sat,? 7 Nov|^Sat, Nov 7/);
    // 23:30 UTC on 2 Nov is 05:00 on 3 Nov in Kolkata: tomorrow, not today.
    expect(exactWhen(Date.parse("2026-11-02T23:30:00Z"), zone, NOW)).toMatch(/^Tomorrow, 5:00\s[Aa][Mm]/);
  });

  it("the zone reads as the server names it, not as the browser's GMT+5:30", () => {
    const text = exactWhen(Date.parse("2026-11-02T11:30:00Z"), zone, NOW, "IST");
    expect(text).toMatch(/^Today, 5:00\s[Pp][Mm] IST$/);
    expect(text).not.toContain("GMT");
  });

  it("the relative time uses minutes, hours or days, and says ago for the past", () => {
    expect(relativeWhen(NOW + 20 * 60_000, NOW)).toBe("in 20 minutes");
    expect(relativeWhen(NOW + 60_000, NOW)).toBe("in 1 minute");
    expect(relativeWhen(NOW + 2 * 3_600_000, NOW)).toBe("in 2 hours");
    expect(relativeWhen(NOW + 3 * 86_400_000, NOW)).toBe("in 3 days");
    expect(relativeWhen(NOW - 5 * 60_000, NOW)).toBe("5 minutes ago");
    expect(relativeWhen(NOW + 10_000, NOW)).toBe("in under a minute");
  });

  it("the presets are 1 hour, this evening and tomorrow morning, all after now", () => {
    const p = laterPresets(NOW, zone);
    expect(p.map((x) => x.label)).toEqual(["1 hour", "This evening", "Tomorrow morning"]);
    expect(p[0].time.ms).toBe(NOW + 3_600_000);
    expect(p[1].time.iso).toBe("2026-11-02T18:00:00+05:30");
    expect(p[2].time.iso).toBe("2026-11-03T09:00:00+05:30");
    for (const x of p) expect(x.time.ms).toBeGreaterThan(NOW);
  });

  it("this evening is left out when it is under 30 minutes away or past", () => {
    const late = Date.parse("2026-11-02T12:15:00Z"); // 17:45 IST
    expect(laterPresets(late, zone).map((x) => x.label)).toEqual(["1 hour", "Tomorrow morning"]);
    const night = Date.parse("2026-11-02T16:00:00Z"); // 21:30 IST
    expect(laterPresets(night, zone).map((x) => x.label)).toEqual(["1 hour", "Tomorrow morning"]);
  });
});
