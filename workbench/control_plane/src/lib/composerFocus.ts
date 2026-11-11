/**
 * When the chat's message box may take focus again (task 2867, owner,
 * 2026-11-11). On a phone, a program's `focus()` on the box opens the soft
 * keyboard. The member typed, closed the keyboard and pressed Send, and the
 * keyboard came back. That ruins the send.
 *
 * The rule:
 * - After a press in the composer (Send, the size toggle, a file chip, a file
 *   pick), the box takes focus again on a touch device ONLY when it had focus
 *   at the press. An open keyboard stays open, and a closed one stays closed.
 * - After an act that no press started (a refused steer gives words back,
 *   dictation ends, a sheet closes), a touch device never opens the keyboard.
 * - A desktop keeps the focus as before: the box always takes it back.
 * - A surface that puts words in the box FOR the member to edit (a chip of a
 *   new-mail card, a pending input) still focuses it. The member asked to edit.
 *
 * `touchKeyboard()` (`lib/chatSteer.ts`) says which device has a soft keyboard.
 *
 * Fence: `lib/composerFocus.test.ts` holds the table and counts the bare
 * `inputRef.current?.focus()` calls that stay in `components/AgentChat.tsx`.
 */

/** Did the box have focus? `active` is `document.activeElement`. */
export function boxHadFocus(
  box: unknown,
  active: unknown = typeof document === "undefined" ? null : document.activeElement,
): boolean {
  return box != null && box === active;
}

/** After a press in the composer: may the box take focus again? */
export function refocusAfterPress(st: { touch: boolean; hadFocus: boolean }): boolean {
  return !st.touch || st.hadFocus;
}

/** After an act that no press started: may the box take focus? */
export function refocusQuietly(st: { touch: boolean }): boolean {
  return !st.touch;
}
