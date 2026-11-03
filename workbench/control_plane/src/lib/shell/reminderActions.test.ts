/**
 * The acts of a reminder row (RX-3): Done and Snooze write the REMINDER row,
 * the row leaves at once through the needs store, and Undo puts it back.
 * The acts run with the client, the clock and the toast passed in, so no DOM
 * or network is needed.
 *
 * Mutations, run by hand (2026-11-02):
 *   - make `doneReminder` write `/shell/feed/state`: "writes the reminder row" fails;
 *   - drop `removeRow` after a done: "the row leaves at once" fails;
 *   - drop the `fail` call: "a refused write keeps the row" fails;
 *   - make the Undo of a done call `/resolve`: "Undo of a done reopens" fails;
 *   - let `rowOfNeeds` accept `reminder_unconfirmed`: "only a reminder that waits" fails.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { acquireReader, actsSnapshot, resetActs } from "./needsActs";
import { type ActDeps, type ActRow, doneReminder, rowOfNeeds, snoozeReminder } from "./reminderActions";
import { localIso, snoozeChoices } from "./reminderBand";
import { moveRequest, runReminderAct, runReminderMove } from "./reminderOpen";

const ID = "3f2a1b4c-5d6e-4f70-8a9b-0c1d2e3f4a5b";
const NOW = new Date(2026, 10, 2, 10, 0, 0, 0);
const ROW: ActRow = {
  id: ID,
  key: `reminders:${ID}`,
  title: "Call the printer",
  phase: "fired",
  at: localIso(new Date(2026, 10, 2, 9, 0)),
  atMs: new Date(2026, 10, 2, 9, 0).getTime(),
};
const okRow = (state: string) => ({
  ok: true as const,
  row: { id: ID, title: "t", kind: "action", state, resolution: null, fire_at: null, tz: null },
});

let release: () => void;
beforeEach(() => {
  release = acquireReader();
});
afterEach(() => {
  release();
  resetActs();
});

function deps(over: Partial<ActDeps> = {}): ActDeps & { said: { words: string; undo?: () => void; tone?: string }[] } {
  const said: { words: string; undo?: () => void; tone?: string }[] = [];
  return {
    act: vi.fn(async () => okRow("done")),
    move: vi.fn(async () => okRow("scheduled")),
    say: (words, undo, tone) => void said.push({ words, undo, tone }),
    refresh: vi.fn(),
    now: () => NOW,
    ...over,
    said,
  };
}

describe("Done", () => {
  it("writes the reminder row through /resolve, never card state", async () => {
    const fetchImpl = vi.fn(async () => new Response(JSON.stringify({ id: ID, state: "done" }), { status: 200 }));
    await runReminderAct(ID, "done", fetchImpl as unknown as typeof fetch);
    const [url, init] = fetchImpl.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(`/api/projects/my/reminders/${ID}/resolve`);
    expect(JSON.parse(String(init.body))).toEqual({ action: "done" });
    expect(url).not.toContain("/shell/feed");
  });

  it("the row leaves at once, and a toast offers Undo", async () => {
    const d = deps();
    expect(await doneReminder(ROW, d)).toBe(true);
    expect(d.act).toHaveBeenCalledWith(ID, "done");
    expect(actsSnapshot().removed.has(ROW.key)).toBe(true);
    expect(d.said[0].words).toBe("Reminder done");
    expect(d.refresh).toHaveBeenCalled();
  });

  it("Undo of a done reopens the reminder and brings the row back", async () => {
    const d = deps();
    await doneReminder(ROW, d);
    d.said[0].undo?.();
    await vi.waitFor(() => expect(d.act).toHaveBeenCalledWith(ID, "undo"));
    await vi.waitFor(() => expect(actsSnapshot().removed.has(ROW.key)).toBe(false));
    expect(d.said.at(-1)?.words).toBe("The reminder is open again");
  });

  it("a refused write keeps the row and shows the words on it", async () => {
    const d = deps({ act: vi.fn(async () => ({ ok: false as const, words: "This reminder is closed (done)." })) });
    expect(await doneReminder(ROW, d)).toBe(false);
    expect(actsSnapshot().removed.has(ROW.key)).toBe(false);
    expect(actsSnapshot().errors[ROW.key]).toBe("This reminder is closed (done).");
    expect(d.said).toEqual([]);
  });
});

describe("Snooze", () => {
  it("writes the reminder row through /snooze with a time that has an offset", async () => {
    const fetchImpl = vi.fn(async () => new Response(JSON.stringify({ id: ID, state: "scheduled" }), { status: 200 }));
    const at = snoozeChoices(NOW)[0].at;
    await runReminderMove(ID, "snooze", localIso(at), fetchImpl as unknown as typeof fetch);
    const [url, init] = fetchImpl.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(`/api/projects/my/reminders/${ID}/snooze`);
    expect(JSON.parse(String(init.body)).fire_at).toMatch(/^2026-11-02T11:00:00[+-]\d{2}:\d{2}$/);
    expect(moveRequest(ID, "reschedule", "x").url).toBe(`/api/projects/my/reminders/${ID}/reschedule`);
  });

  it("moves the reminder to the chosen time, takes the row off and offers Undo", async () => {
    const d = deps();
    const choice = snoozeChoices(NOW)[2];
    expect(await snoozeReminder(ROW, choice, d)).toBe(true);
    expect(d.move).toHaveBeenCalledWith(ID, "snooze", choice.iso);
    expect(actsSnapshot().removed.has(ROW.key)).toBe(true);
    expect(d.said[0].words).toMatch(/^Snoozed to tomorrow /);
  });

  it("Undo of a snooze reschedules a waiting reminder one minute ahead", async () => {
    const d = deps();
    await snoozeReminder(ROW, snoozeChoices(NOW)[0], d);
    d.said[0].undo?.();
    await vi.waitFor(() => expect(d.move).toHaveBeenCalledTimes(2));
    const [, route, when] = (d.move as ReturnType<typeof vi.fn>).mock.calls[1];
    expect(route).toBe("reschedule");
    expect(Date.parse(when as string)).toBe(NOW.getTime() + 60_000);
    expect(actsSnapshot().removed.has(ROW.key)).toBe(false);
  });

  it("a refused snooze keeps the row", async () => {
    const d = deps({ move: vi.fn(async () => ({ ok: false as const, words: "No." })) });
    expect(await snoozeReminder(ROW, snoozeChoices(NOW)[0], d)).toBe(false);
    expect(actsSnapshot().removed.has(ROW.key)).toBe(false);
  });
});

describe("the bell row", () => {
  it("has the acts only for a reminder that waits on the member", () => {
    const item = (kind: string) => ({ id: `reminders:${ID}`, kind, title: "x", at: "2026-11-02T09:00:00+05:30" }) as never;
    expect(rowOfNeeds(item("reminder_due"))?.phase).toBe("fired");
    expect(rowOfNeeds(item("reminder_check"))?.phase).toBe("check");
    expect(rowOfNeeds(item("reminder_unconfirmed"))).toBeNull();
    expect(rowOfNeeds({ id: "tasks:9", kind: "reminder_due", title: "x", at: null } as never)).toBeNull();
    expect(rowOfNeeds(item("reminder_due"))?.id).toBe(ID);
  });
});
