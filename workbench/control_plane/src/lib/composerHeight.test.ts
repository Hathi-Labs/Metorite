import { describe, expect, it } from "vitest";

import { COMPOSER_MIN_PX, composerCap, composerHeight, offerExpand } from "./composerHeight";

describe("the chat composer's height", () => {
  it("is one line when empty, so a sent message leaves a small box", () => {
    expect(composerHeight(0, 400)).toBe(COMPOSER_MIN_PX);
    expect(composerHeight(20, 400)).toBe(COMPOSER_MIN_PX);
  });

  it("grows with the message up to the cap, then scrolls", () => {
    expect(composerHeight(150, 400)).toBe(150);
    expect(composerHeight(900, 400)).toBe(400);
  });

  it("caps lower on a phone, so the keyboard and the thread keep room", () => {
    expect(composerCap(800, false, true)).toBeLessThan(composerCap(800, false, false));
  });

  it("expanding gives a taller editor than growing alone", () => {
    expect(composerCap(900, true, false)).toBeGreaterThan(composerCap(900, false, false));
    expect(composerCap(700, true, true)).toBeGreaterThan(composerCap(700, false, true));
  });

  it("never caps below a paragraph on a very short screen", () => {
    expect(composerCap(200, false, true)).toBeGreaterThanOrEqual(120);
  });

  it("offers the expand control only when the message no longer fits, or to shrink it back", () => {
    expect(offerExpand(100, 300, false)).toBe(false);
    expect(offerExpand(400, 300, false)).toBe(true);
    expect(offerExpand(40, 540, true)).toBe(true);
  });
});
