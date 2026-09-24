/**
 * Was this assistant reply cut off mid-stream?
 *
 * Only the `streaming` flag says so. The browser saves it with the message
 * (`lib/sessions.ts`) while tokens still arrive, and clears it when the run
 * settles.
 *
 * This replaced a punctuation guess ("the reply does not end in . ? !"). A
 * reply that ends in a list, a table, a code block or a card is complete, and
 * the guess called it interrupted. Every reopen of such a chat then showed
 * "Reconnecting…" and a stop button for up to 45 seconds, and afterwards a
 * permanent "Stream was interrupted" warning. `chatInterrupted.test.ts` is the
 * fence.
 */
export function isInterruptedReply(
  message: { role?: string; streaming?: boolean } | undefined,
): boolean {
  return message?.role === "assistant" && message.streaming === true;
}
