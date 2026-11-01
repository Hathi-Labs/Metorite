/**
 * The "used your profile" mark of the one assistant (`personalization.md`
 * §13.3 item 1, PZ-20b).
 *
 * When a turn's prompt holds a block from a stored profile that is not empty,
 * the executor sends a CUSTOM event `assistant_profile_used`. Its value is
 * `{ v: 1 }` and holds no profile text. The executor holds the event until
 * the run's first output, so a turn that never answers carries none. The
 * chat translator saves every custom event on the turn, so a reload draws
 * the same mark.
 *
 * Only the member who ran the turn reads the mark. The server drops it for
 * every other reader (`_events_for_reader` in `gateway/routes/chat.py`).
 * `profileMarkShown` checks the same rule a second time in the bubble.
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

/**
 * True when the bubble draws the mark under *message* for *viewerEmail*.
 *
 * An assistant turn with the event draws it when there is no reader, or when
 * the reader is the member who ran the turn (`runMemberEmail`). A turn with
 * no run member is a live turn of this browser, and its reader started it.
 * A turn that names another member draws none.
 */
export function profileMarkShown(
  message: {
    role: string;
    customEvents?: ReadonlyArray<{ name: string }>;
    runMemberEmail?: string;
  },
  viewerEmail: string | undefined,
): boolean {
  if (message.role !== "assistant" || !profileUsedIn(message.customEvents)) {
    return false;
  }
  const viewer = (viewerEmail ?? "").trim().toLowerCase();
  const runner = (message.runMemberEmail ?? "").trim().toLowerCase();
  return !viewer || !runner || runner === viewer;
}
