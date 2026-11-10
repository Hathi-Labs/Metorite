/**
 * The "rules changed" signal of the email app (task 2451).
 *
 * The rule writers in `api.ts` call `announceRulesChanged()` after a write
 * succeeds. A surface that shows a count of rules (the Auto-run notice) calls
 * `onRulesChanged` and reads again. It follows the `window` CustomEvent idiom
 * that `cc-email-open` already uses. It has no store. Fence:
 * `autoRunNotice.test.ts`.
 */
export const RULES_CHANGED_EVENT = "cc-email-rules-changed";

/** Tell every listener that a rule was made, changed, deleted or copied. No-op without a window. */
export function announceRulesChanged(target: EventTarget | null = typeof window === "undefined" ? null : window): void {
  target?.dispatchEvent(new Event(RULES_CHANGED_EVENT));
}

/** Call `handler` on each change. Returns the function that stops it. */
export function onRulesChanged(
  handler: () => void,
  target: EventTarget | null = typeof window === "undefined" ? null : window,
): () => void {
  if (!target) return () => {};
  target.addEventListener(RULES_CHANGED_EVENT, handler);
  return () => target.removeEventListener(RULES_CHANGED_EVENT, handler);
}
