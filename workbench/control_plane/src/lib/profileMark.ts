/**
 * The "used your profile" mark of the one assistant (`personalization.md`
 * §13.3 item 1, PZ-20b).
 *
 * When a turn's prompt holds a block from a stored profile that is not empty,
 * the executor sends a CUSTOM event `assistant_profile_used`. Its value is
 * `{ v: 1 }` and holds no profile text. The chat translator saves every custom
 * event on the turn, so a reload draws the same mark.
 *
 * The server twin is `PROFILE_USED_EVENT` in
 * `packages/acb_common/acb_common/assistant_profile.py`.
 *
 * Pure. Fence (R7): `profileMark.test.ts`.
 */

export const PROFILE_USED_EVENT = "assistant_profile_used";

/** True when the turn carries the profile mark. */
export function profileUsedIn(
  customEvents: ReadonlyArray<{ name: string }> | undefined,
): boolean {
  return (customEvents ?? []).some((e) => e.name === PROFILE_USED_EVENT);
}
