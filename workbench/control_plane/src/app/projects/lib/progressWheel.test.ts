import { describe, expect, it } from "vitest";

import { PROJECT_STATES, PROJECT_STATE_ORDER } from "@/lib/statusAccent";

import {
  closableCount,
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
    //
    // ⚠️ Iterates the REAL vocabulary. A first version of this loop listed
    // "paused", "planned", "archived" and "wat" — of which only one existed.
    // It passed on four strings the product never produces, and it never
    // reached `done`, which is the state most likely to be confused with a
    // full ring. A hand-written list of another module's keys is not a fence.
    const others = PROJECT_STATE_ORDER.filter((s) => s !== "active");
    expect(others).toHaveLength(4);
    for (const state of others) {
      expect(showsWheel(state, { tasks: 8, done: 1 }), state).toBe(false);
    }
  });

  it("a DONE project keeps its check, and does not draw a full ring", () => {
    // The nastiest confusion in the set: "done" and "100%" are different
    // claims. A completed project wears `CircleCheck`; a live project that
    // happens to have finished all its work wears a filled ring.
    expect(showsWheel("done", { tasks: 8, done: 8 })).toBe(false);
    expect(PROJECT_STATES.done.icon).toBe("CircleCheck");
  });

  it("a live project with NO work anywhere STILL gets one", () => {
    // 🔴 The owner looked at a real sidebar and said "the icons still look
    // the same". An earlier rule kept the dot here, and a conditional the
    // reader cannot see is indistinguishable from a feature that did not
    // ship. Every live project wears the ring. Owner call, 2026-09-23.
    expect(showsWheel("active", { tasks: 0, done: 0 })).toBe(true);
  });

  it("gets one even before the counts have loaded", () => {
    // `/projects/nodes` returns the same rows flat and rolls nothing up, so
    // the counts can be absent. The ring still draws, empty, rather than the
    // row flipping shape once a number arrives.
    expect(showsWheel("active", {})).toBe(true);
    expect(showsWheel("active", { tasks: null, done: null })).toBe(true);
  });

  it("a live project with work and none done DOES get a ring", () => {
    // 0 of 12 is a real fact and worth drawing, unlike 0 of 0.
    expect(showsWheel("active", { tasks: 12, done: 0 })).toBe(true);
  });
});

describe("isLive", () => {
  it("is exactly the green state", () => {
    expect(isLive("active")).toBe(true);
    expect(isLive("on_hold")).toBe(false);
  });

  /**
   * ⚠️ **R7 — this is the fence `isLive`'s own docstring asks for.**
   *
   * It calls itself "the single fact this whole feature turns on, and the one
   * a later state rename would silently break", and then nothing held it to
   * the vocabulary. Rename the `active` key in `statusAccent.ts` and every
   * wheel in the sidebar disappears with no test failing and no error logged
   * — the sidebar simply goes back to dots, which is exactly what the owner
   * reported seeing on 2026-09-23.
   *
   * So: the token must still BE a state, and it must be the green one.
   */
  it("names a state that still exists, and it is the green one", () => {
    const live = PROJECT_STATE_ORDER.find((s) => isLive(s));
    expect(live, "isLive matches no state in PROJECT_STATE_ORDER").toBeDefined();
    expect(PROJECT_STATES[live!].hue).toBe("green");
  });

  it("matches exactly one state, never two", () => {
    expect(PROJECT_STATE_ORDER.filter((s) => isLive(s))).toHaveLength(1);
  });
});

describe("completion", () => {
  it("is the finished fraction", () => {
    expect(completion({ tasks: 8, done: 2 })).toBe(0.25);
    expect(completionPercent({ tasks: 8, done: 2 })).toBe(25);
  });

  /**
   * 🔴 **The P1 this file shipped with, and the one the reviewer caught.**
   *
   * The first version counted every CLOSING category as finished, so
   * `cancelled` landed in the numerator. Two consequences, both visible on
   * one screen, because `NodeDashboard` sits beside the tree:
   *
   *   - 4 done, 4 cancelled, 2 open → the ring said 80%, the dashboard 67%.
   *   - every open task cancelled → the ring said 100% and filled green,
   *     the dashboard said 0% and drew nothing.
   *
   * `core.py` names the rule these pin: *"a metric that adds them makes
   * cancellation the cheapest way to improve itself"*.
   */
  it("subtracts cancelled work, never adds it", () => {
    expect(completionPercent({ tasks: 10, done: 4, cancelled: 4 })).toBe(67);
    // The old arithmetic — (4 + 4) / 10 — would read 80.
    expect(completionPercent({ tasks: 10, done: 4, cancelled: 4 })).not.toBe(80);
  });

  it("does not call an abandoned project finished", () => {
    // Six tasks, none delivered, every one cancelled. The old rule filled the
    // ring completely and labelled it "100% done".
    expect(completion({ tasks: 6, done: 0, cancelled: 6 })).toBe(0);
    expect(completionPercent({ tasks: 6, done: 0, cancelled: 6 })).toBe(0);
  });

  it("agrees with NodeDashboard's rule on the same numbers", () => {
    // `CompletionFigure` and `ProgressCard` both call this now. The assertion
    // is the arithmetic they used to write out for themselves.
    for (const [tasks, done, cancelled] of [
      [10, 4, 4], [7, 7, 0], [12, 0, 0], [9, 3, 3], [5, 1, 4],
    ] as const) {
      const closable = tasks - cancelled;
      const expected = closable > 0 ? Math.round((done / closable) * 100) : 0;
      expect(
        completionPercent({ tasks, done, cancelled }),
        `${done}/${tasks} with ${cancelled} cancelled`,
      ).toBe(expected);
    }
  });

  it("reads a missing cancelled count as none, not as a gap", () => {
    // `/nodes` rolls nothing up, so the field can be absent. Absent must mean
    // "none cancelled", which leaves the denominator whole.
    expect(completion({ tasks: 4, done: 1 })).toBe(0.25);
    expect(completion({ tasks: 4, done: 1, cancelled: null })).toBe(0.25);
  });

  it("closableCount never goes negative", () => {
    expect(closableCount({ tasks: 3, done: 0, cancelled: 9 })).toBe(0);
    expect(closableCount({})).toBe(0);
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

  it("counts against the closable total, not the raw one", () => {
    // 10 tasks, 4 cancelled — the reader is told "4 of 6", matching the ring.
    // "4 of 10" beside a ring at 67% is the mismatch this whole fix is about.
    const label = wheelLabel("project", "Rocket", {
      tasks: 10, done: 4, cancelled: 4,
    });
    expect(label).toContain("4 of 6");
    expect(label).not.toContain("4 of 10");
  });

  it("says why the total shrank, but only when something was cancelled", () => {
    // Cancelling the last open task jumps the ring to 100%. That is correct
    // and baffling unless the label says what left the denominator.
    expect(
      wheelLabel("project", "Rocket", { tasks: 10, done: 4, cancelled: 4 }),
    ).toContain("4 cancelled, not counted");
    expect(
      wheelLabel("project", "Rocket", { tasks: 10, done: 4 }),
    ).not.toContain("cancelled");
  });
});
