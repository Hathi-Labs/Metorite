import { describe, expect, it } from "vitest";

import { PROJECT_STATES, PROJECT_STATE_ORDER } from "@/lib/statusAccent";

import {
  MARK_BOX,
  MARK_CENTER,
  MARK_HOLE,
  MARK_RADIUS,
  MARK_STROKE,
  STATE_MARK,
  TRACK_OPACITY,
  innerReach,
  markKind,
  markParts,
  queuedDash,
} from "./stateMark";

describe("every run state has a mark of its own", () => {
  it("covers the whole vocabulary", () => {
    // A state added to PROJECT_STATES without a mark would silently draw the
    // plain-ring fallback. That is the drift this map is exhaustive to catch.
    for (const state of PROJECT_STATE_ORDER) {
      expect(STATE_MARK[state], `${state} has no mark`).toBeDefined();
    }
    expect(Object.keys(STATE_MARK).sort()).toEqual([...PROJECT_STATE_ORDER].sort());
  });

  it("gives no two states the same shape (D-PM-27)", () => {
    // Hue AND glyph, never hue alone. Amber and green are the pair most often
    // confused, and every mark now shares one ring — so the INSIDE is the
    // only thing left to tell them apart without colour.
    const kinds = PROJECT_STATE_ORDER.map((s) => markKind(s));
    expect(new Set(kinds).size).toBe(kinds.length);
  });

  it("gives only the live state the wheel", () => {
    expect(PROJECT_STATE_ORDER.filter((s) => markKind(s) === "wheel")).toEqual(["active"]);
    expect(PROJECT_STATES.active.hue).toBe("green");
  });

  it("draws DONE as a check, never as a full wheel", () => {
    // "Done" and "100%" are different claims. A completed project wears the
    // check. A live project that happens to have closed every task wears a
    // full ring, and the two must not look alike.
    expect(markKind("done")).toBe("check");
  });

  it("falls back to a plain ring for a state it does not know", () => {
    expect(markKind("something-new")).toBe("ring");
    expect(markKind(null)).toBe("ring");
    expect(markKind("ON_HOLD")).toBe("pause");
  });
});

describe("one weight for the whole family", () => {
  it("never clips its own box", () => {
    // The ring's outer edge is the radius plus half the stroke. Past the box
    // edge, the browser cuts it flat.
    expect(MARK_RADIUS + MARK_STROKE / 2).toBeLessThanOrEqual(MARK_CENTER);
    expect(MARK_BOX).toBe(MARK_CENTER * 2);
  });

  it("every ring in every mark is the SAME ring", () => {
    // The owner's ask was continuity of weight. A ring or arc part that set
    // its own radius or stroke would break it, so the parts carry neither.
    // ⚠️ What this does NOT prove: the check's own line is thinner by design
    // (`width`, two thirds of the ring), so this pins the RING, not every
    // stroke in the mark.
    for (const state of PROJECT_STATE_ORDER) {
      for (const part of markParts(markKind(state), { tasks: 4, done: 1 })) {
        expect(Object.keys(part)).not.toContain("r");
        expect(Object.keys(part)).not.toContain("strokeWidth");
      }
    }
  });

  it("is heavier than the wheel it replaced", () => {
    // 2.5 at 14px was the ring the owner asked to thicken. At 16px a stroke
    // of 3 is 3px on screen, against 2.2px before.
    const before = 2.5 * (14 / MARK_BOX);
    const after = MARK_STROKE * (16 / MARK_BOX);
    expect(after).toBeGreaterThan(before * 1.3);
  });

  it("keeps the track fainter than the arc, but visible on white", () => {
    // Fainter, so the arc is the information. But not ghostly: at 0% the
    // track is the whole mark, and at 0.18 an empty live project nearly
    // vanished in light mode (seen in the real app, 2026-09-23).
    expect(TRACK_OPACITY).toBeLessThanOrEqual(0.35);
    expect(TRACK_OPACITY).toBeGreaterThanOrEqual(0.25);
  });
});

describe("an inner mark never touches the ring", () => {
  /**
   * 🔴 The failure of the first drawing of this family. The marks filled the
   * hole, and at 14px Paused and Stopped both collapsed into solid discs that
   * only hue could tell apart — which is D-PM-27 broken by the very change
   * meant to honour it.
   */
  const CLEARANCE = 0.4;

  for (const kind of ["pause", "stop", "check"] as const) {
    it(`${kind} leaves a visible gap inside the ring`, () => {
      const reach = innerReach(markParts(kind));
      expect(reach).toBeGreaterThan(0);
      expect(reach).toBeLessThanOrEqual(MARK_HOLE - CLEARANCE);
    });
  }

  it("the reach is measured, not assumed", () => {
    // A mark with no inner parts reaches nothing. If `innerReach` ever
    // stopped seeing a shape, the three checks above would pass on zero.
    expect(innerReach(markParts("ring"))).toBe(0);
    expect(innerReach(markParts("stop"))).toBeGreaterThan(MARK_HOLE / 2);
  });
});

describe("queuedDash", () => {
  it("divides the circle into whole periods, so there is no seam", () => {
    // A pattern that does not divide the circumference leaves a short dash or
    // a double gap at twelve o'clock, which reads as a broken icon.
    const [on, off] = queuedDash(6).split(" ").map(Number);
    const circumference = 2 * Math.PI * MARK_RADIUS;
    expect((on + off) * 6).toBeCloseTo(circumference, 2);
  });

  it("leaves a gap you can see after the round caps grow each dash", () => {
    // Round caps add half a stroke at each end of a dash, so the visible gap
    // is the pattern's gap less one full stroke.
    const [, off] = queuedDash(6).split(" ").map(Number);
    expect(off - MARK_STROKE).toBeGreaterThan(1);
  });
});

describe("the wheel inside the family", () => {
  it("is a faint track and one arc", () => {
    const parts = markParts("wheel", { tasks: 10, done: 4, cancelled: 4 });
    expect(parts.map((p) => p.kind)).toEqual(["ring", "arc"]);
    expect(parts[0]).toMatchObject({ opacity: TRACK_OPACITY });
  });

  it("draws NO arc at zero, not a dot", () => {
    // 🔴 A zero-length dash with a round cap still paints the cap. The empty
    // wheel drew a dot at twelve o'clock, which claims progress that does not
    // exist. Seen in the live tree on 2026-09-23.
    for (const progress of [{}, { tasks: 0, done: 0 }, { tasks: 5, done: 0 }]) {
      expect(markParts("wheel", progress).map((p) => p.kind)).toEqual(["ring"]);
    }
    expect(markParts("wheel", { tasks: 5, done: 1 }).map((p) => p.kind)).toEqual([
      "ring",
      "arc",
    ]);
  });

  it("draws the arc from the same completion rule the dashboard prints", () => {
    // 4 of 6 closable — cancelled work leaves the denominator.
    const [, arc] = markParts("wheel", { tasks: 10, done: 4, cancelled: 4 });
    const [on, off] = (arc as { dash: string }).dash.split(" ").map(Number);
    expect(on / (on + off)).toBeCloseTo(4 / 6, 3);
  });
});
