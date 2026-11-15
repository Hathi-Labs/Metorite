/**
 * STU-D23a fence: the model of the floor view.
 *
 * Mutation 1: give the floor view `rail: usual || floor`. "drops the rail ..." turns red.
 * Mutation 2: let `isFloor` take any value. "turns on for 1 only" turns red.
 * Mutation 3: return the line in the usual view. "names the member in the floor view only" turns red.
 */
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

import { FLOOR_KEY, floorSubtitle, floorView, isFloor, recordFull } from "@/lib/studio/floorView";

const SRC = readFileSync(new URL("./floorView.ts", import.meta.url), "utf8");

describe("the parts of the frame", () => {
  it("keeps the cells of the page and the record page in both views", () => {
    for (const on of [true, false]) {
      expect(floorView(on).cells).toBe(true);
      expect(floorView(on).record).toBe(true);
    }
  });

  it("drops the rail, the owner verbs, the end-date bar, the setup card and the list bar in the floor view", () => {
    expect(floorView(true)).toMatchObject({
      floor: true, rail: false, ownerBar: false, lifetime: false, setupCard: false, filterBar: false,
    });
  });

  it("draws each part in the usual view", () => {
    expect(floorView(false)).toMatchObject({
      floor: false, rail: true, ownerBar: true, lifetime: true, setupCard: true, filterBar: true,
      fullRecord: false, toPanel: true,
    });
  });

  it("opens a record as the full page in the floor view, with no way to the panel", () => {
    const floor = floorView(true);
    expect(floor.fullRecord).toBe(true);
    expect(floor.toPanel).toBe(false);
    expect(recordFull(floor, false)).toBe(true);
    expect(recordFull(floorView(false), false)).toBe(false);
    expect(recordFull(floorView(false), true)).toBe(true);
  });
});

describe("the key", () => {
  it("is floor, and never a filter key", () => {
    expect(FLOOR_KEY).toBe("floor");
    expect(FLOOR_KEY.startsWith("f.")).toBe(false);
  });

  it("turns on for 1 only", () => {
    expect(isFloor("pkg=p1&floor=1")).toBe(true);
    expect(isFloor(new URLSearchParams("floor=1"))).toBe(true);
    for (const q of ["", "floor=0", "floor=true", "floor=", "floor=11", "f.floor=1", "Floor=1"]) {
      expect(isFloor(q), q).toBe(false);
    }
    expect(isFloor(null)).toBe(false);
    expect(isFloor(undefined)).toBe(false);
  });

  it("never throws on a broken query", () => {
    expect(() => isFloor("floor=%E0%A4%A&%&&=")).not.toThrow();
  });
});

describe("the line of the app bar", () => {
  const floor = floorView(true);

  it("names the member in the floor view only", () => {
    expect(floorSubtitle(floor, { name: "Asha Rao", email: "asha@example.com" })).toBe("Signed in as Asha Rao");
    expect(floorSubtitle(floorView(false), { name: "Asha Rao", email: "asha@example.com" })).toBeUndefined();
  });

  it("uses the email when the session has no name, and draws nothing with no member", () => {
    expect(floorSubtitle(floor, { name: "  ", email: "asha@example.com" })).toBe("Signed in as asha@example.com");
    expect(floorSubtitle(floor, { name: null, email: null })).toBeUndefined();
    expect(floorSubtitle(floor, null)).toBeUndefined();
  });
});

describe("what it is not", () => {
  it("is pure, signs nobody in and never says that a device is shared", () => {
    expect(SRC).not.toMatch(/^import /m);
    expect(SRC).not.toMatch(/signIn|localStorage|sessionStorage|--ui-scale/);
    expect(SRC.toLowerCase()).not.toContain("shared device");
  });

  it("is never named floorMode, the floor control of a chat room", () => {
    expect(SRC).not.toContain("export function floorMode");
    expect(SRC).not.toMatch(/floorMode\s*[:=(]/);
  });
});
