/**
 * WS-27bk §9.12.4 slice 2 — where a dragged NODE lands.
 *
 * The claims worth pinning are the ones where a plausible implementation is
 * wrong in a way that looks fine:
 *
 * * **doubles run out.** Repeatedly dropping into one gap halves it, and after
 *   about fifty insertions the midpoint IS one of the neighbours. The order
 *   then stops changing while every write succeeds — a drag that looks broken
 *   and a server that reports nothing wrong.
 * * **a null position is not a zero.** A tree that has never been reordered
 *   carries `null` on every row, and the midpoint of two nulls is `NaN`, which
 *   Postgres accepts and sorts unpredictably.
 * * **the grammar refuses BEFORE a plan exists.** A drop the server would 422
 *   must never become a write.
 */

import { describe, expect, it } from "vitest";

import type { ProjectNode } from "./tree";
import {
  MIN_GAP,
  POSITION_SPAN,
  needsSpread,
  planTreeDrop,
  positionAt,
  siblingsOf,
  spreadPositions,
} from "./treeDrop";

const node = (
  id: string,
  position: number | null,
  extra: Partial<ProjectNode> = {},
): ProjectNode => ({ id, name: id, position, children: [], ...extra });

/**
 *   space  (100)
 *     a    (100)
 *     b    (200)
 *     c    (300)
 *   other  (200)
 */
const forest = (): ProjectNode[] => [
  {
    ...node("space", 100),
    children: [node("a", 100), node("b", 200), node("c", 300)],
  },
  node("other", 200),
];

describe("siblingsOf", () => {
  it("reads the roots for a null parent", () => {
    expect(siblingsOf(forest(), null).map((n) => n.id)).toEqual([
      "space",
      "other",
    ]);
  });

  it("reads a parent's children", () => {
    expect(siblingsOf(forest(), "space").map((n) => n.id)).toEqual([
      "a",
      "b",
      "c",
    ]);
  });

  it("is empty for a parent that is not there", () => {
    expect(siblingsOf(forest(), "ghost")).toEqual([]);
  });
});

describe("positionAt", () => {
  const others = [node("a", 100), node("b", 200), node("c", 300)];

  it("takes the midpoint between two neighbours", () => {
    expect(positionAt(others, 1)).toBe(150);
    expect(positionAt(others, 2)).toBe(250);
  });

  it("halves the first position when landing at the top", () => {
    expect(positionAt(others, 0)).toBe(50);
  });

  it("steps past the last when landing at the bottom", () => {
    expect(positionAt(others, 3)).toBe(300 + POSITION_SPAN / 2);
  });

  it("centres a lone arrival in an empty set", () => {
    expect(positionAt([], 0)).toBe(POSITION_SPAN / 2);
  });

  it("⚠️ refuses when a sibling has NO position, rather than treating null as 0", () => {
    // The midpoint of two nulls is NaN. Postgres takes it and sorts it
    // unpredictably, so the order silently stops meaning anything.
    expect(positionAt([node("a", null), node("b", 200)], 1)).toBeNull();
  });

  it("⚠️ refuses when the gap has CLOSED — doubles run out", () => {
    const tight = [node("a", 1), node("b", 1 + MIN_GAP / 2)];
    expect(positionAt(tight, 1)).toBeNull();
  });

  it("still answers for a gap comfortably above the floor", () => {
    // ⚠️ Deliberately NOT asserted at exactly MIN_GAP. `(1 + 1e-6) - 1` is
    // 9.999999999e-7 in doubles, not 1e-6, so an exact-boundary assertion
    // pins floating-point noise rather than behaviour. The floor is a
    // safety margin, and a test that treats it as a precise threshold is
    // the kind that fails on a different machine for no reason.
    const roomy = [node("a", 1), node("b", 1 + MIN_GAP * 100)];
    const answer = positionAt(roomy, 1);
    expect(answer).not.toBeNull();
    expect(answer).toBeGreaterThan(1);
    expect(answer).toBeLessThan(1 + MIN_GAP * 100);
  });
});

describe("needsSpread", () => {
  it("is true when any sibling carries no position", () => {
    expect(needsSpread([node("a", 100), node("b", null)])).toBe(true);
    expect(needsSpread([node("a", 100), node("b", 200)])).toBe(false);
  });

  it("is false for an empty set — there is nothing to disagree about", () => {
    expect(needsSpread([])).toBe(false);
  });
});

describe("spreadPositions", () => {
  it("puts the moving node at the index and spaces the rest evenly", () => {
    const out = spreadPositions([node("a", null), node("b", null)], "x", 1);
    expect(out.map((r) => r.id)).toEqual(["a", "x", "b"]);
    const positions = out.map((r) => r.position);
    expect(positions[0]).toBeLessThan(positions[1]);
    expect(positions[1]).toBeLessThan(positions[2]);
  });

  it("leaves room at both ends, so a later drop has somewhere to go", () => {
    const out = spreadPositions([node("a", null)], "x", 0);
    expect(out[0].position).toBeGreaterThan(0);
    expect(out[out.length - 1].position).toBeLessThan(POSITION_SPAN);
  });
});

describe("planTreeDrop", () => {
  it("⚠️ refuses a drop the GRAMMAR refuses, before any write exists", () => {
    const roots = forest();
    const result = planTreeDrop(roots, "space", {
      kind: "onto",
      nodeId: "a",
    });
    expect(result).toEqual({ refusal: expect.stringMatching(/inside itself/) });
  });

  it("re-parents on an ONTO drop, with no position", () => {
    const result = planTreeDrop(forest(), "a", {
      kind: "onto",
      nodeId: "other",
    });
    expect(result).toEqual({ plan: { parentId: "other" } });
  });

  it("skips an ONTO drop into the parent it already has", () => {
    // A write here would cost an activity row and a refetch for nothing.
    expect(planTreeDrop(forest(), "a", { kind: "onto", nodeId: "space" })).toBeNull();
  });

  it("reorders on a BETWEEN drop, with a midpoint", () => {
    const result = planTreeDrop(forest(), "c", {
      kind: "between",
      parentId: "space",
      index: 1,
    });
    expect(result).toEqual({ plan: { parentId: "space", position: 150 } });
  });

  it("⚠️ accounts for the dragged row still being in the list it is measured against", () => {
    // `dropIndexFor`'s whole reason. Dragging `a` DOWNWARD to the gap after
    // `b` is gap index 2, and with `a` removed that is slot 1 — between b and
    // c, which is where the line was drawn. Off by one here lands it past `c`.
    const result = planTreeDrop(forest(), "a", {
      kind: "between",
      parentId: "space",
      index: 2,
    });
    expect(result).toEqual({ plan: { parentId: "space", position: 250 } });
  });

  it("spreads the whole set when a sibling has no position", () => {
    const roots: ProjectNode[] = [
      {
        ...node("space", 100),
        children: [node("a", null), node("b", null)],
      },
    ];
    const result = planTreeDrop(roots, "b", {
      kind: "between",
      parentId: "space",
      index: 0,
    });
    expect(result).not.toBeNull();
    expect("spread" in (result as object)).toBe(true);
    const spread = (result as { spread: Array<{ id: string }> }).spread;
    expect(spread.map((r) => r.id)).toEqual(["b", "a"]);
  });

  it("moves a node out to the top level", () => {
    const result = planTreeDrop(forest(), "a", {
      kind: "between",
      parentId: null,
      index: 2,
    });
    expect(result).toMatchObject({ plan: { parentId: null } });
  });
});
