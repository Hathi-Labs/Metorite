import { describe, expect, it } from "vitest";

import {
  completion,
  completionPercent,
  isLive,
  ringDash,
  showsWheel,
  wheelLabel,
} from "./progressWheel";

describe("which rows get a wheel at all", () => {
  it("a live project with work gets one", () => {
    expect(showsWheel("active", { tasks: 8, done: 1 })).toBe(true);
  });

  it("every other run state keeps its glyph", () => {
    // The owner's "all other icons remain unchanged". A paused row that
    // became a ring like everything else would stop reading as paused at a
    // glance, which is the whole point of D-PM-27's glyph-and-hue rule.
    for (const state of ["paused", "stopped", "planned", "archived", "wat"]) {
      expect(showsWheel(state, { tasks: 8, done: 1 })).toBe(false);
    }
  });

  it("a live project with NO work anywhere keeps its dot", () => {
    // Two different nothings. "There is no work here" and "none of this work
    // is done" must not look identical — an empty ring claims the second.
    expect(showsWheel("active", { tasks: 0, done: 0 })).toBe(false);
  });

  it("treats missing counts as no work, never as zero percent", () => {
    // `/projects/nodes` returns the same rows flat and rolls nothing up.
    expect(showsWheel("active", {})).toBe(false);
    expect(showsWheel("active", { tasks: null, done: null })).toBe(false);
  });

  it("a live project with work and none done DOES get a ring", () => {
    // 0 of 12 is a real fact and worth drawing, unlike 0 of 0.
    expect(showsWheel("active", { tasks: 12, done: 0 })).toBe(true);
  });
});

describe("isLive", () => {
  it("is exactly the green state", () => {
    expect(isLive("active")).toBe(true);
    expect(isLive("paused")).toBe(false);
  });
});

describe("completion", () => {
  it("is the finished fraction", () => {
    expect(completion({ tasks: 8, done: 2 })).toBe(0.25);
    expect(completionPercent({ tasks: 8, done: 2 })).toBe(25);
  });

  it("is 0 rather than NaN on an empty subtree", () => {
    // A bare `done / tasks` is NaN here, and NaN in a dasharray silently
    // paints nothing — a wheel that vanished rather than one that read zero.
    expect(completion({ tasks: 0, done: 0 })).toBe(0);
    expect(Number.isNaN(completion({}))).toBe(false);
  });

  it("clamps rather than wrapping past a full circle", () => {
    // If a roll-up ever disagrees with itself, a ring that read 110% as 10%
    // would be worse than one that read it as full.
    expect(completion({ tasks: 5, done: 9 })).toBe(1);
    expect(completion({ tasks: 5, done: -3 })).toBe(0);
  });

  it("reaches exactly 1 when everything is closed", () => {
    expect(completionPercent({ tasks: 7, done: 7 })).toBe(100);
  });
});

describe("ringDash", () => {
  const R = 6;
  const C = 2 * Math.PI * R;

  it("paints nothing at zero and everything at one", () => {
    const [on0, off0] = ringDash({ tasks: 4, done: 0 }, R).split(" ").map(Number);
    expect(on0).toBe(0);
    expect(off0).toBeCloseTo(C, 5);

    const [on1, off1] = ringDash({ tasks: 4, done: 4 }, R).split(" ").map(Number);
    expect(on1).toBeCloseTo(C, 5);
    expect(off1).toBeCloseTo(0, 5);
  });

  it("always sums to the circumference", () => {
    // The pair is "painted, then not painted". A sum that drifted would leave
    // a gap or an overlap at the top of the ring.
    for (const done of [0, 1, 2, 3, 4, 5, 6, 7]) {
      const [on, off] = ringDash({ tasks: 7, done }, R).split(" ").map(Number);
      expect(on + off).toBeCloseTo(C, 5);
    }
  });

  it("never emits NaN", () => {
    // NaN in a dasharray paints nothing and reports no error.
    expect(ringDash({}, R)).not.toContain("NaN");
    expect(ringDash({ tasks: 0, done: 0 }, R)).not.toContain("NaN");
  });
});

describe("wheelLabel", () => {
  it("names the counts, not only the percent", () => {
    // 3 of 5 and 600 of 1000 are the same percent and not the same situation.
    const label = wheelLabel("project", "Metorite Platform", { tasks: 8, done: 2 });
    expect(label).toContain("Metorite Platform");
    expect(label).toContain("25%");
    expect(label).toContain("2 of 8");
  });

  it("says the row is active, because the wheel only ever means that", () => {
    expect(wheelLabel("subproject", "Hardware", { tasks: 3, done: 3 }))
      .toContain("active");
  });
});
