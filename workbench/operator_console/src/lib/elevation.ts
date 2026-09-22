// The elevation window's pure logic (CP-12g, H-67).
//
// Extracted from `app/Elevation.tsx` so it can be tested without a browser —
// this console's suite is `lib/*.test.ts` and there is no React renderer in it.
// A countdown and a length floor are exactly the parts worth testing, and both
// are decidable from arguments alone.

/** The Console's own floor — `operator_elevation.MIN_REASON_CHARS`.
 *
 * ⚠️ Mirrored deliberately, and NOT trusted. Checking here saves a round trip
 * on the common mistake; the Console still decides, and its 400 is relayed
 * verbatim. A mirror that DISAGREED would only ever refuse something the
 * Console would have accepted, which is the safe direction for a mirror to
 * drift.
 */
export const MIN_REASON = 12;

export type ElevationWindow = {
  elevated: boolean;
  /** Could this caller open a window at all?
   *
   * 🔴 **`false` on the break-glass path**, which is where the control used to
   * draw a button nobody could press. `GET /operators/elevate` is
   * `viewer`-readable and break-glass BYPASSES the matrix, so it answers 200
   * — and the surface hid itself by expecting a 403. Measured 2026-09-22.
   *
   * ⚠️ Optional, defaulting to FALSE at the reader: a Console predating the
   * field cannot tell us, and drawing a control we cannot justify is the
   * defect this closes. */
  can_elevate?: boolean;
  /** Does any route actually DEMAND a window? D72 turned this off, so it is
   *  normally false — and a control for a feature that is not running is a
   *  control that only ever produces a refusal. */
  required?: boolean;
  reason?: string;
  reference?: string | null;
  expires_at?: string;
};

/** Should the elevation control be on screen at all?
 *
 * 🔴 **Three conditions, and the surface used to check none of them.** It
 * rendered whenever the Console answered 200. Break-glass answers 200. So the
 * owner, on the passphrase path, saw an "Elevate" button whose only possible
 * outcome was *"only a signed-in operator can elevate"*.
 *
 * ⚠️ An OPEN window always shows, whatever the flag says. A window somebody
 * opened before the flag changed still confers the privilege, and hiding the
 * countdown would leave it running with nothing on screen to end it.
 */
export function elevationVisible(
  win: ElevationWindow | null,
): win is ElevationWindow {
  if (win === null) return false;
  if (win.elevated) return true;
  return win.can_elevate === true && win.required === true;
}

/** Whole minutes and seconds left, or null once the window has run out.
 *
 * ⚠️ Returns null for a MISSING, unparseable or past `expires_at` alike. The
 * caller re-reads on null rather than guessing: the Console decides when a
 * window is over, and clock skew must never leave a dead countdown on screen
 * claiming a privilege the operator no longer holds.
 */
export function remaining(
  expiresAt: string | undefined | null,
  now: number,
): string | null {
  if (!expiresAt) return null;
  const ms = new Date(expiresAt).getTime() - now;
  if (!Number.isFinite(ms) || ms <= 0) return null;
  const total = Math.floor(ms / 1000);
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}

/** Whether this reason clears the floor. Trimmed, because "            " is
 * twelve characters and no reason at all. */
export function reasonIsUsable(reason: string): boolean {
  return reason.trim().length >= MIN_REASON;
}
