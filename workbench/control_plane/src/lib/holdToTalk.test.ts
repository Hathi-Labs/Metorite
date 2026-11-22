import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { HOLD_TEXT, isHoldKey, isOffButton, POPOVER_MS, releaseOutcome, TAP_MS } from "@/lib/holdToTalk";

const read = (p: string) => readFileSync(join(__dirname, "..", p), "utf-8");

describe("press length: tap or hold", () => {
  it("a press under 300 ms is a tap, at any state of the mic", () => {
    expect(TAP_MS).toBe(300);
    expect(releaseOutcome({ heldMs: 0, cancelled: false, recording: true })).toBe("tap");
    expect(releaseOutcome({ heldMs: 299, cancelled: false, recording: true })).toBe("tap");
    expect(releaseOutcome({ heldMs: 120, cancelled: false, recording: false })).toBe("tap");
  });
  it("300 ms and over, with the mic live, stops and sends", () => {
    expect(releaseOutcome({ heldMs: 300, cancelled: false, recording: true })).toBe("stop");
    expect(releaseOutcome({ heldMs: 90_000, cancelled: false, recording: true })).toBe("stop");
  });
  it("a long press that never got the mic records nothing and sends nothing", () => {
    expect(releaseOutcome({ heldMs: 4000, cancelled: false, recording: false })).toBe("discard");
  });
  it("a cancel always discards, even after a long hold", () => {
    expect(releaseOutcome({ heldMs: 5000, cancelled: true, recording: true })).toBe("discard");
    expect(releaseOutcome({ heldMs: 10, cancelled: true, recording: true })).toBe("discard");
  });
});

describe("drag off the button discards", () => {
  const box = { left: 100, top: 100, right: 132, bottom: 132 };
  it("inside is on the button, outside is off", () => {
    expect(isOffButton(110, 110, box)).toBe(false);
    expect(isOffButton(100, 132, box)).toBe(false);
    expect(isOffButton(99, 110, box)).toBe(true);
    expect(isOffButton(110, 140, box)).toBe(true);
  });
  it("slack widens the button", () => {
    expect(isOffButton(95, 110, box, 8)).toBe(false);
    expect(isOffButton(80, 110, box, 8)).toBe(true);
  });
});

describe("keyboard", () => {
  it("Space and Enter hold the button", () => {
    expect(isHoldKey(" ")).toBe(true);
    expect(isHoldKey("Enter")).toBe(true);
    expect(isHoldKey("Escape")).toBe(false);
    expect(isHoldKey("a")).toBe(false);
  });
});

describe("the popover and the wiring", () => {
  const button = read("components/VoiceInputButton.tsx");
  it("says Hold to talk, closes after 2 s", () => {
    expect(HOLD_TEXT).toBe("Hold to talk");
    expect(POPOVER_MS).toBe(2000);
    expect(button).toContain("POPOVER_MS");
    expect(button).toContain("data-chat-voice-popover");
  });
  it("closes on the next click elsewhere", () => {
    expect(button).toContain('document.addEventListener("pointerdown"');
    expect(button).toContain('document.removeEventListener("pointerdown"');
  });
  it("touch: no long-press menu, no selection, pointer capture", () => {
    expect(button).toContain("setPointerCapture");
    expect(button).toContain("touch-none");
    expect(button).toContain("select-none");
    expect(button).toContain("onContextMenu");
    expect(button).toContain("onPointerCancel");
  });
  it("keyboard: key down starts, key up stops, and the synthetic click is blocked", () => {
    expect(button).toContain("isHoldKey(e.key)");
    expect(button).toContain("onKeyUp");
    expect(button).toContain("e.repeat");
  });
  it("name and title say Hold to talk, and the Stop and X buttons are gone", () => {
    expect(button).toContain("HOLD_TEXT");
    expect(button).not.toContain('aria-label="Discard recording"');
    expect(button).not.toContain("Stop recording");
  });
  it("no new flag: it sits on the same availability gate as before", () => {
    const chat = read("components/AgentChat.tsx");
    expect(chat).toContain("voiceAvailability.shown && (");
    expect(button).not.toMatch(/FLAG|_ORGS/);
  });
});
