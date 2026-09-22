// WS-31 CP-12g — the elevation window control. Closes H-67.
//
// Spec: `specs/operator_identity_and_access.md` §6.3 · §5 · D64.4.
//
// ⚠️ **CP-12g slice 1 shipped `/api/operator/elevate` and nothing called it.**
// The Console's §5 matrix binds NINE actions to a live window AND `admin`.
// Without a surface, the first signed-in operator reads everything and changes
// almost nothing — the day somebody flips `OPERATOR_IDENTITY_ENABLED`.

import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import {
  MIN_REASON,
  type ElevationWindow,
  elevationVisible,
  reasonIsUsable,
  remaining,
} from "./elevation";

const SRC = join(__dirname, "..");
const COMPONENT = readFileSync(join(SRC, "app", "Elevation.tsx"), "utf8");
const HEADER = readFileSync(join(SRC, "app", "Header.tsx"), "utf8");

describe("the countdown", () => {
  const NOW = Date.parse("2026-08-27T12:00:00Z");
  const at = (s: number) => new Date(NOW + s * 1000).toISOString();

  it("renders minutes and zero-padded seconds", () => {
    expect(remaining(at(605), NOW)).toBe("10:05");
    expect(remaining(at(60), NOW)).toBe("1:00");
    expect(remaining(at(9), NOW)).toBe("0:09");
  });

  it("is null once the window has run out", () => {
    // ⚠️ The caller RE-READS on null rather than guessing. The Console decides
    // when a window is over, and clock skew must never leave a dead countdown
    // claiming a privilege the operator no longer holds.
    expect(remaining(at(0), NOW)).toBeNull();
    expect(remaining(at(-1), NOW)).toBeNull();
  });

  it("is null for a missing or unparseable expiry, not NaN on screen", () => {
    expect(remaining(undefined, NOW)).toBeNull();
    expect(remaining(null, NOW)).toBeNull();
    expect(remaining("", NOW)).toBeNull();
    expect(remaining("not a date", NOW)).toBeNull();
  });
});

describe("the reason floor", () => {
  it("mirrors the Console's own floor", () => {
    expect(MIN_REASON).toBe(12);
  });

  it("refuses a reason that is only whitespace", () => {
    // Twelve spaces is twelve characters and no reason at all.
    expect(reasonIsUsable(" ".repeat(20))).toBe(false);
  });

  it("accepts a real one and refuses a short one", () => {
    expect(reasonIsUsable("rotating the deepseek key")).toBe(true);
    expect(reasonIsUsable("fixing it")).toBe(false);
  });
});

describe("the surface exists and is reachable", () => {
  it("drives the elevate route — H-67's own Check", () => {
    // `rg -n "api/operator/elevate" --glob '*.tsx'` is the entry's Check.
    expect(COMPONENT).toContain("/api/operator/elevate");
  });

  it("uses all three verbs the route already shipped", () => {
    // GET to read, POST to open, DELETE to close early. A control that could
    // only OPEN would train operators to wait the clock out.
    expect(COMPONENT).toContain('method: "POST"');
    expect(COMPONENT).toContain('method: "DELETE"');
  });

  it("is mounted in the top bar, not on a page nobody visits", () => {
    // ⚠️ A standing destructive privilege must be visible wherever you are.
    // Unmounting this is how the surface silently stops existing again.
    expect(HEADER).toContain("<Elevation />");
    expect(HEADER).toContain('from "./Elevation"');
  });

  it("relays the Console's refusal VERBATIM", () => {
    // The Console is the authority on a refusal. Paraphrasing it here would
    // invent a second vocabulary for the same 400.
    expect(COMPONENT).toContain("setError(await res.text())");
  });

  it("never sends an operator id — elevation is always for the caller", () => {
    // ⚠️ Elevating somebody ELSE hands out a destructive privilege they did
    // not ask for, and the audit row would name the wrong person. The Console
    // reads the operator from the session and takes no id.
    expect(COMPONENT).not.toContain("operator_id");
    expect(COMPONENT).not.toContain("operatorId");
  });

  it("asks `elevationVisible`, not whether the read merely succeeded", () => {
    // 🔴 This used to assert `if (win === null) return null;`, and that was
    // the DEFECT. It hid the control by expecting a 403 — but
    // `GET /operators/elevate` is `viewer`-readable and break-glass BYPASSES
    // the matrix, so break-glass gets a 200. The owner, on the passphrase
    // path, saw an "Elevate" button whose only outcome was
    // "only a signed-in operator can elevate". Measured 2026-09-22.
    expect(COMPONENT).toContain("if (!elevationVisible(win)) return null;");
    expect(COMPONENT).not.toContain("if (win === null) return null;");
  });
});

// ── Should the control be on screen at all? ───────────────────────────────

describe("elevationVisible", () => {
  const W = (over: Partial<ElevationWindow> = {}): ElevationWindow => ({
    elevated: false,
    can_elevate: true,
    required: true,
    ...over,
  });

  it("hides when the Console answered nothing", () => {
    expect(elevationVisible(null)).toBe(false);
  });

  it("🔴 hides on the BREAK-GLASS path, which answers 200", () => {
    // The whole bug. `can_elevate` is false and the read still succeeded.
    expect(elevationVisible(W({ can_elevate: false }))).toBe(false);
  });

  it("hides when NO route demands a window (D72)", () => {
    // A control for a feature that is not running can only ever refuse.
    expect(elevationVisible(W({ required: false }))).toBe(false);
  });

  it("shows when a window can be opened AND something demands one", () => {
    expect(elevationVisible(W())).toBe(true);
  });

  it("⚠️ ALWAYS shows an OPEN window, whatever the flag says", () => {
    // A window opened before the flag changed still confers the privilege.
    // Hiding the countdown would leave it running with no way to end it.
    expect(
      elevationVisible(W({ elevated: true, required: false, can_elevate: false })),
    ).toBe(true);
  });

  it("treats a Console that omits the fields as NOT visible", () => {
    // ⚠️ Absent is not permission. A Console predating the fields cannot tell
    // us, and drawing a control we cannot justify is the defect this closes.
    expect(elevationVisible({ elevated: false })).toBe(false);
  });
});
