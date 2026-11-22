/**
 * Hold to talk (owner, 2026-11-21, task 2501). The chat mic records while the
 * member holds it, and releasing stops it. This file holds the pure rules, so
 * a vitest with no DOM can test them. `components/VoiceInputButton.tsx` wires
 * them to the pointer and the keyboard.
 */

/** A press shorter than this is a tap. A tap records nothing. */
export const TAP_MS = 300;
/** The "Hold to talk" popover closes by itself after this long. */
export const POPOVER_MS = 2000;
/** The words of the popover, the button's name and its title. */
export const HOLD_TEXT = "Hold to talk";

export type ReleaseOutcome =
  /** A short press: drop the clip, show the popover. */
  | "tap"
  /** A held press: stop and transcribe. */
  | "stop"
  /** A cancelled press, or a release off the button: drop the clip, no text. */
  | "discard";

/**
 * What a release means.
 * - `cancelled`: a pointercancel, a lost window or a drag off the button.
 * - `recording`: the mic had already granted when the member let go. A first
 *   permission prompt can outlast the press. Nothing recorded, so nothing stops.
 */
export function releaseOutcome(input: { heldMs: number; cancelled: boolean; recording: boolean }): ReleaseOutcome {
  if (input.cancelled) return "discard";
  if (input.heldMs < TAP_MS) return "tap";
  return input.recording ? "stop" : "discard";
}

export interface Box {
  left: number;
  top: number;
  right: number;
  bottom: number;
}

/** Is the point outside the button? A release there discards the clip. */
export function isOffButton(x: number, y: number, box: Box, slack = 0): boolean {
  return x < box.left - slack || x > box.right + slack || y < box.top - slack || y > box.bottom + slack;
}

/** Which keys hold the button. */
export function isHoldKey(key: string): boolean {
  return key === " " || key === "Enter";
}
