import { describe, expect, it } from "vitest";

import {
  edgeHitOrder,
  edgeMidpoint,
  edgePath,
  edgePoints,
  polylineLength,
  roundedPath,
  stagger,
} from "./deps";
import { ROW_H } from "./geometry";

const bar = (leftPx: number, widthPx: number) => ({
  leftPx,
  widthPx,
  singleDate: false,
  derived: false,
});

describe("edgePoints", () => {
  it("is null when either end has no bar", () => {
    expect(edgePoints({ bar: null, row: 0 }, { bar: bar(0, 10), row: 1 })).toBeNull();
    expect(edgePath({ bar: bar(0, 10), row: 0 }, { bar: null, row: 1 })).toBeNull();
  });
  it("runs straight on one row with room ahead", () => {
    const pts = edgePoints({ bar: bar(0, 50), row: 0 }, { bar: bar(100, 20), row: 0 });
    expect(pts).toHaveLength(2);
  });
  it("drops straight down when the bars touch", () => {
    const pts = edgePoints({ bar: bar(0, 50), row: 0 }, { bar: bar(50, 20), row: 2 });
    expect(pts).toEqual([
      { x: 50, y: ROW_H / 2 },
      { x: 50, y: 2 * ROW_H + ROW_H / 2 },
    ]);
  });
  it("arrives from above when the target starts before the source ends", () => {
    const pts = edgePoints({ bar: bar(0, 100), row: 0 }, { bar: bar(40, 20), row: 1 });
    expect(pts).toHaveLength(5);
    expect(pts?.[4].x).toBe(40);
  });
});

describe("roundedPath and the polyline helpers", () => {
  it("rounds a corner and leaves a straight line alone", () => {
    expect(
      roundedPath([
        { x: 0, y: 0 },
        { x: 40, y: 0 },
      ]),
    ).toBe("M 0 0 L 40 0");
    expect(
      roundedPath([
        { x: 0, y: 0 },
        { x: 40, y: 0 },
        { x: 40, y: 40 },
      ]),
    ).toContain("Q 40 0");
    expect(roundedPath([{ x: 0, y: 0 }])).toBe("");
  });
  it("measures along the line and finds its middle", () => {
    const pts = [
      { x: 0, y: 0 },
      { x: 10, y: 0 },
      { x: 10, y: 10 },
    ];
    expect(polylineLength(pts)).toBe(20);
    expect(edgeMidpoint(pts)).toEqual({ x: 10, y: 0 });
    expect(edgeMidpoint([{ x: 1, y: 1 }])).toBeNull();
  });
});

describe("stagger and edgeHitOrder", () => {
  const forward = (id: string, x: number, srcY: number) => ({
    id,
    points: [
      { x: 0, y: srcY },
      { x, y: srcY },
      { x, y: srcY + 80 },
      { x: 200, y: srcY + 80 },
    ],
  });
  it("gives two colliding runs their own channels and keeps the ends", () => {
    const out = stagger([forward("a", 100, 0), forward("b", 101, 200)]);
    expect(out[0].points[1].x).not.toBe(out[1].points[1].x);
    expect(out[0].points[0]).toEqual({ x: 0, y: 0 });
    expect(out.map((r) => r.id)).toEqual(["a", "b"]);
  });
  it("leaves a lone edge where it is", () => {
    const lone = forward("a", 100, 0);
    expect(stagger([lone])[0].points).toEqual(lone.points);
  });
  it("paints the aimed edge last and leaves the rest in order", () => {
    const edges = [{ id: "a" }, { id: "b" }, { id: "c" }];
    expect(edgeHitOrder(edges, "a").map((e) => e.id)).toEqual(["b", "c", "a"]);
    expect(edgeHitOrder(edges, null).map((e) => e.id)).toEqual(["a", "b", "c"]);
    expect(edgeHitOrder(edges, "z").map((e) => e.id)).toEqual(["a", "b", "c"]);
  });
});
