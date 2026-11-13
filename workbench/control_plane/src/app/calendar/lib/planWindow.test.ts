import { describe, expect, it } from "vitest";
import { EMPTY_WINDOW_TEXT, planWindow, WORK_HORIZON } from "./planWindow";

// Task 3163. The owner planned at 00:43 on Saturday 14 Nov with "3 more
// hours · till 3:43 AM", and the gateway refused the window, because it ran
// from 09:00 to 03:43. Local times keep each case free of the test's zone.
const at = (h: number, m = 0, day = 14) => new Date(2026, 10, day, h, m, 0, 0);

describe("planWindow (task 3163)", () => {
  it("starts now when a from-now plan before the working day ends before it", () => {
    const now = at(0, 43);
    const end = new Date(now.getTime() + 3 * 3600_000);
    const span = planWindow({
      target: at(0), dayStartHour: 9, end, nowMs: now.getTime(),
      planningToday: true, horizon: "3",
    });
    expect(span).not.toBeNull();
    expect(span!.start.getTime()).toBe(now.getTime());
    expect(span!.end.getTime()).toBe(at(3, 43).getTime());
    expect(span!.end.getTime()).toBeGreaterThan(span!.start.getTime());
  });

  it("starts now for a custom time in the small hours", () => {
    const now = at(1, 10);
    const span = planWindow({
      target: at(0), dayStartHour: 9, end: at(2, 30), nowMs: now.getTime(),
      planningToday: true, horizon: "custom",
    });
    expect(span!.start.getTime()).toBe(now.getTime());
  });

  it("keeps the working-day start in the middle of the day", () => {
    const now = at(14);
    const end = new Date(now.getTime() + 3 * 3600_000);
    const span = planWindow({
      target: at(0), dayStartHour: 9, end, nowMs: now.getTime(),
      planningToday: true, horizon: "3",
    });
    expect(span!.start.getTime()).toBe(at(9).getTime());
  });

  it("keeps the working-day start late in the evening, past midnight", () => {
    const now = at(23);
    const end = new Date(now.getTime() + 3 * 3600_000);
    const span = planWindow({
      target: at(0), dayStartHour: 9, end, nowMs: now.getTime(),
      planningToday: true, horizon: "3",
    });
    expect(span!.start.getTime()).toBe(at(9).getTime());
    expect(span!.end.getTime()).toBe(at(2, 0, 15).getTime());
  });

  it("keeps the working hours for the work choice, and for another day", () => {
    const now = at(0, 43);
    const work = planWindow({
      target: at(0), dayStartHour: 9, end: at(18), nowMs: now.getTime(),
      planningToday: true, horizon: WORK_HORIZON,
    });
    expect(work!.start.getTime()).toBe(at(9).getTime());
    const tomorrow = planWindow({
      target: at(0, 0, 15), dayStartHour: 9, end: at(18, 0, 15),
      nowMs: now.getTime(), planningToday: false, horizon: "3",
    });
    expect(tomorrow!.start.getTime()).toBe(at(9, 0, 15).getTime());
  });

  it("gives no window when the end is not after the start", () => {
    const now = at(10);
    expect(planWindow({
      target: at(0), dayStartHour: 9, end: at(9), nowMs: now.getTime(),
      planningToday: true, horizon: WORK_HORIZON,
    })).toBeNull();
    expect(EMPTY_WINDOW_TEXT).toMatch(/Pick a later time/);
  });
});
