import { describe, expect, it, vi } from "vitest";
import type { MyTask } from "@/app/tasks/lib/types";
import type { GridEvent } from "@/components/calendar/types";
import { monthEventsForDay, openMonthEvent } from "./gridAdapter";

// The Month cell takes task blocks, then deadlines. The grid reads only the
// GridEvent, so the order and the tone are the contract of this adapter.
const task = (id: string, o: Partial<MyTask>): MyTask =>
  ({ id, title: id, disposition: "OPEN", ...o }) as unknown as MyTask;

const DAY = new Date(2026, 10, 12);

describe("monthEventsForDay", () => {
  it("lists the task blocks of the day by start, then the hard deadlines", () => {
    const items = [
      task("late", { scheduledStart: new Date(2026, 10, 12, 15).toISOString() }),
      task("early", { scheduledStart: new Date(2026, 10, 12, 9).toISOString() }),
      task("other-day", { scheduledStart: new Date(2026, 10, 13, 9).toISOString() }),
      task("due", { isHardDate: true, dueAt: new Date(2026, 10, 12, 12).toISOString() }),
    ];
    const out = monthEventsForDay(DAY, items, [], "UTC", []);
    expect(out.map((e) => [e.id, e.tone])).toEqual([
      ["early", "primary"],
      ["late", "primary"],
      ["due", "destructive"],
    ]);
    expect(out[2].glyph).toBe("flag");
  });

  it("leaves a finished deadline out", () => {
    const items = [task("done", { isHardDate: true, dueAt: DAY.toISOString(), disposition: "DONE" })];
    expect(monthEventsForDay(DAY, items, [], "UTC", [])).toEqual([]);
  });
});

describe("openMonthEvent", () => {
  const ev = (kind: string): GridEvent => ({
    id: "x", title: "x", start: DAY, end: DAY, allDay: false, tone: "primary", data: { kind },
  });
  const handlers = () => ({ onOpen: vi.fn(), onOpenReminder: vi.fn(), onOpenExternal: vi.fn() });

  it("sends a reminder, an external event and a task to their own handler", () => {
    const h = handlers();
    openMonthEvent(ev("reminder"), h);
    openMonthEvent(ev("external"), h);
    openMonthEvent(ev("block"), h);
    openMonthEvent(ev("deadline"), h);
    expect(h.onOpenReminder).toHaveBeenCalledTimes(1);
    expect(h.onOpenExternal).toHaveBeenCalledTimes(1);
    expect(h.onOpen).toHaveBeenCalledTimes(2);
  });

  it("does not open a task for a reminder when the handler is missing", () => {
    const h = handlers();
    openMonthEvent(ev("reminder"), { onOpen: h.onOpen });
    expect(h.onOpen).not.toHaveBeenCalled();
  });
});
